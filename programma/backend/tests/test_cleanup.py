"""Eliminazione dei progetti e pulizia dei job: services/jobcleanup.py, JobManager.submit/delete/purge_tombstones,
Housekeeping e le rotte /api/jobs/cleanup, /api/projects/{id}/usage, DELETE /api/projects/{id}.

I job si inseriscono direttamente nel database (`make_jobs`, senza esecuzione) con una cartella e un `result.json` di
dimensione nota. Ogni test che elimina qualcosa finisce con `assert_consistent`: nessun job orfano, nessuna cartella di
job senza riga, nessuna etichetta o stella di elementi spariti, nessun elemento di pipeline senza job, nessun resto
`.deleting-*`. Un job "ripreso" è un job in `_preparing` (ripresa in preparazione).
"""

from __future__ import annotations

import itertools
import os
import re
import shutil
import stat
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Optional

import pytest
from fastapi.testclient import TestClient

from bioloom.app import create_app
from bioloom.config import Paths
from bioloom.core import auth, storage
from bioloom.core import jobs as jobs_module
from bioloom.core.container import build_core
from bioloom.core.errors import AppError, Conflict, NotFound
from bioloom.core.jobs import TOMBSTONE_PREFIX, Deleted
from bioloom.services import housekeeping as housekeeping_module
from bioloom.services import jobcleanup
from bioloom.services.jobcleanup import STARRED
from tests.conftest import HUMAN_UB

HEX16 = re.compile(r"^[0-9a-f]{16}$")
AS_OF = re.compile(r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\+00:00$")  # come now_iso()
NOW = datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc)
_ids = itertools.count(1)


@pytest.fixture
def core(client):
    return client.app.state.core


# ----------------------------------------------------------------------------------------------- utilità
def stamp(days_ago: float, now: Optional[datetime] = None) -> str:
    return ((now or datetime.now(timezone.utc)) - timedelta(days=days_ago)).isoformat(timespec="seconds")


def make_jobs(core, n: int, *, project_id: Optional[str] = None, kind: str = "model", status: str = "completed",
              age_days: float = 0, size: int = 4, title: str = "Job di prova") -> list[str]:
    """`n` job inseriti con una sola executemany, ciascuno con la sua cartella e un result.json di `size` byte; i job
    attivi sono solo righe (nessuna esecuzione). Restituisce gli id in ordine di creazione."""
    at = stamp(age_days)
    finished = None if status in jobs_module.ACTIVE else at
    ids = [f"{next(_ids):016x}" for _ in range(n)]
    core.db.executemany(
        "INSERT INTO jobs(id, kind, title, project_id, status, created_at, started_at, finished_at, updated_at) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        [(job_id, kind, title, project_id, status, at, at, finished, at) for job_id in ids])
    for job_id in ids:
        folder = core.jobs.jobs_dir / job_id
        folder.mkdir()
        (folder / "result.json").write_bytes(b"x" * size)
    return ids


def assert_consistent(core, *, dangling: tuple[str, ...] = ()) -> None:
    """Nessun residuo dopo un'eliminazione; `dangling` = job con un progetto inesistente voluti dal test."""
    db = core.db
    orphans = {r["id"] for r in db.query(
        "SELECT id FROM jobs WHERE project_id IS NOT NULL AND project_id NOT IN (SELECT id FROM projects)")}
    assert orphans <= set(dangling), orphans
    rows = {r["id"] for r in db.query("SELECT id FROM jobs")}
    names = [p.name for p in core.jobs.jobs_dir.iterdir()]
    assert not [n for n in names if HEX16.match(n) and n not in rows], "cartelle di job senza riga"
    assert not [n for n in names if n.startswith(TOMBSTONE_PREFIX)], "resti .deleting-*"
    for etype, table in (("job", "jobs"), ("project", "projects"), ("sequence", "sequences")):
        for labels in ("tags", "favorites"):
            assert db.scalar(f"SELECT COUNT(*) FROM {labels} WHERE entity_type=? AND entity_id NOT IN "
                             f"(SELECT id FROM {table})", (etype,)) == 0, (labels, etype)
    assert db.scalar("SELECT COUNT(*) FROM pipeline_items WHERE job_id NOT IN (SELECT id FROM jobs)") == 0
    assert not core.jobs._removing


def resumed(core, monkeypatch, job_id: str) -> None:
    """Il job ha una ripresa in preparazione; l'attesa di JobManager.delete scende a 0,05 s."""
    monkeypatch.setattr(jobs_module, "REQUEUE_WAIT_SECONDS", 0.05)
    core.jobs._preparing.add(job_id)


def wait_done(core, job_id: str, timeout: float = 10.0) -> None:
    """Attende la fine dell'esecuzione (stato terminale ed esecuzione chiusa); un job già eliminato va bene."""
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        try:
            status = core.jobs.get(job_id, include_result=False)["status"]
        except NotFound:
            return
        if status in jobs_module.TERMINAL and not core.jobs.busy(job_id):
            return
        time.sleep(0.005)
    raise AssertionError(f"job {job_id} non terminato entro {timeout} s")


def criteria(core, **body):
    return core.cleanup.criteria({"scope": "all", **body})


def ticking_clock(monkeypatch, step: float = 10.0) -> None:
    """`time.monotonic` di services/jobcleanup.py che avanza di `step` secondi a ogni lettura."""
    clock = itertools.count(0, step)
    monkeypatch.setattr(jobcleanup, "time", SimpleNamespace(monotonic=lambda: next(clock)))


def stubborn_remove(real, should_stick):
    """remove_tree che, per le cartelle scelte da `should_stick(path)`, toglie tutto tranne aperto.pdb (file in uso)
    e restituisce False; per le altre usa quella vera."""

    def remove(path, attempts=3):
        path = Path(path)
        if should_stick(path):
            for item in path.iterdir():
                if item.name != "aperto.pdb":
                    item.unlink()
            return False
        return real(path, attempts)

    return remove


# ----------------------------------------------------------------------------------------------- submit
def test_submit_unknown_project_raises_notfound_and_leaves_no_trace(core):
    core.jobs.register("prova", lambda ctx, p: {})
    with pytest.raises(NotFound, match="Progetto non trovato"):
        core.jobs.submit("prova", {"x": 1}, project_id="inesistente1")
    assert core.db.scalar("SELECT COUNT(*) FROM jobs") == 0
    assert not [p for p in core.jobs.jobs_dir.iterdir() if HEX16.match(p.name)]


def test_submit_empty_project_id_means_no_project(core):
    core.jobs.register("prova", lambda ctx, p: {})
    job = core.jobs.submit("prova", {}, project_id="")
    wait_done(core, job["id"])
    assert core.db.scalar("SELECT project_id FROM jobs WHERE id=?", (job["id"],)) is None
    assert core.jobs.get(job["id"])["status"] == "completed"


def test_submit_prepare_failure_leaves_no_row_nor_folder(core):
    core.jobs.register("prova", lambda ctx, p: {})
    pid = core.projects.list()[0]["id"]

    def broken(_job_id, workdir):
        (workdir / "parziale.txt").write_text("x")
        raise RuntimeError("preparazione guasta")

    with pytest.raises(RuntimeError, match="preparazione guasta"):  # l'eccezione di prepare esce invariata
        core.jobs.submit("prova", {}, project_id=pid, prepare=broken)
    assert core.db.scalar("SELECT COUNT(*) FROM jobs") == 0  # nessuna riga 'queued' fantasma
    assert not [p for p in core.jobs.jobs_dir.iterdir() if HEX16.match(p.name)]


def test_submit_cleanup_leftover_becomes_tombstone_and_never_masks_the_error(core, monkeypatch):
    core.jobs.register("prova", lambda ctx, p: {})
    pid = core.projects.list()[0]["id"]
    real = jobs_module.remove_tree

    def broken(_job_id, workdir):
        (workdir / "aperto.pdb").write_text("x")
        raise RuntimeError("preparazione guasta")

    # la cartella non si rimuove del tutto (file in uso): il resto passa in .deleting-<id>, che la pulizia toglie
    monkeypatch.setattr(jobs_module, "remove_tree", stubborn_remove(real, lambda path: HEX16.match(path.name)))
    with pytest.raises(RuntimeError, match="preparazione guasta"):
        core.jobs.submit("prova", {}, project_id=pid, prepare=broken)
    names = [p.name for p in core.jobs.jobs_dir.iterdir()]
    assert core.db.scalar("SELECT COUNT(*) FROM jobs") == 0 and not [n for n in names if HEX16.match(n)]
    assert len([n for n in names if n.startswith(TOMBSTONE_PREFIX)]) == 1
    monkeypatch.setattr(jobs_module, "remove_tree", real)
    assert core.housekeeping.run()["jobs"] == 1

    # una pulizia che solleva non nasconde l'errore originale
    def exploding(path, attempts=3):
        raise RuntimeError("guasto della pulizia")

    monkeypatch.setattr(jobs_module, "remove_tree", exploding)
    with pytest.raises(RuntimeError, match="preparazione guasta"):
        core.jobs.submit("prova", {}, project_id=pid, prepare=broken)
    monkeypatch.setattr(jobs_module, "remove_tree", real)
    for folder in core.jobs.jobs_dir.iterdir():  # la cartella rimasta per la pulizia guasta
        if HEX16.match(folder.name):
            real(folder)
    assert_consistent(core)


def test_submit_races_delete_project(core):
    """Un job inviato mentre si elimina il progetto: o il DELETE ritenta, o l'invio riceve 404. Mai un job orfano."""
    core.jobs.register("prova", lambda ctx, p: {})
    pid = core.projects.create("Conteso")["id"]
    state = {"submitted": 0, "refused": False, "result": None, "codes": set(), "errors": []}
    deadline = time.monotonic() + 30

    def submitter():
        try:
            while time.monotonic() < deadline:
                try:
                    job = core.jobs.submit("prova", {}, project_id=pid)
                except NotFound:
                    state["refused"] = True
                    return
                state["submitted"] += 1
                wait_done(core, job["id"], timeout=5)
                time.sleep(0.005)
        except Exception as exc:  # l'esito si controlla nel thread principale
            state["errors"].append(exc)

    def deleter():
        try:
            while state["submitted"] < 5 and time.monotonic() < deadline:
                time.sleep(0.002)
            while time.monotonic() < deadline:
                try:
                    state["result"] = core.cleanup.delete_project(pid)
                    return
                except Conflict as exc:
                    state["codes"].add(exc.code)
                    time.sleep(0.002)
        except Exception as exc:
            state["errors"].append(exc)

    threads = [threading.Thread(target=submitter), threading.Thread(target=deleter)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(40)
    assert not state["errors"], state["errors"]
    assert state["result"] is not None and state["result"]["deleted"] == pid
    assert state["refused"] and state["submitted"] >= 5
    assert state["codes"] <= {"project_active_jobs", "project_busy"}
    assert core.db.scalar("SELECT COUNT(*) FROM projects WHERE id=?", (pid,)) == 0
    assert_consistent(core)


# ----------------------------------------------------------------------------------------------- un job
def test_remove_tree_reports_leftover(tmp_path, monkeypatch):
    folder = tmp_path / "cartella"
    (folder / "sotto").mkdir(parents=True)
    (folder / "sotto" / "file.txt").write_text("x")
    calls, sleeps = [], []
    monkeypatch.setattr(storage, "shutil", SimpleNamespace(rmtree=lambda *args, **kwargs: calls.append(args)))
    monkeypatch.setattr(storage, "time", SimpleNamespace(sleep=sleeps.append, monotonic=time.monotonic))
    assert storage.remove_tree(folder) is False and folder.exists()
    assert len(calls) == 3 and sleeps == [pytest.approx(0.1), pytest.approx(0.2)]  # pause solo fra i tentativi
    monkeypatch.undo()
    assert storage.remove_tree(folder) is True and not folder.exists()
    assert storage.remove_tree(folder) is True  # già assente


def test_remove_tree_errors_never_escape_nor_lock_folders(tmp_path, monkeypatch):
    folder = tmp_path / "cartella"
    sub = folder / "sotto"
    sub.mkdir(parents=True)
    (sub / "tenuto.txt").write_text("x")
    other = sub / "sola-lettura.txt"
    other.write_text("y")
    other.chmod(0o400)
    sub.chmod(0o755)
    # su POSIX 0o755; su Windows i permessi di una cartella non si cambiano (st_mode resta 0o777): si confronta con
    # quelli che ha davvero, così «un'apertura fallita non cambia i permessi» vale su entrambi
    initial = stat.S_IMODE(sub.stat().st_mode)
    if os.name == "posix":
        assert initial == 0o755
    modes, calls = [], []
    error = (OSError, OSError(13, "accesso negato"), None)

    def failing_rmtree(path, onerror):
        # come rmtree su Linux dopo un'eliminazione non riuscita: la sottocartella non si apre né si legge, un file
        # non si elimina al primo colpo, la cartella non si elimina perché non è vuota
        onerror(os.open, str(sub), error)
        onerror(os.scandir, str(sub), error)
        modes.append(stat.S_IMODE(sub.stat().st_mode))
        onerror(os.unlink, str(other), error)
        onerror(os.rmdir, str(folder), error)
        calls.append(path)

    monkeypatch.setattr(storage, "shutil", SimpleNamespace(rmtree=failing_rmtree))
    monkeypatch.setattr(storage, "time", SimpleNamespace(sleep=lambda _s: None, monotonic=time.monotonic))
    assert storage.remove_tree(folder) is False  # niente TypeError da os.open(target) senza flag
    assert len(calls) == 3 and modes == [initial] * 3  # un'apertura fallita non cambia i permessi
    assert not other.exists()  # il file sbloccato si è eliminato al secondo colpo
    assert stat.S_IMODE(sub.stat().st_mode) & 0o700 == 0o700  # cartella mai di sola scrittura
    assert (sub / "tenuto.txt").exists()
    monkeypatch.undo()
    assert storage.remove_tree(folder) is True


JOB_ID = "0123456789abcdef"


def _unsafe(base: Path, *parts: str) -> bool:
    with pytest.raises(AppError) as info:
        storage.safe_join(base, *parts)
    return info.value.code == "unsafe_path"


@pytest.mark.parametrize("parts", [(JOB_ID,), (JOB_ID, "sub", "job.log")], ids=["cartella", "annidato"])
def test_safe_join_accepts_a_folder_being_deleted(tmp_path, parts):
    """La cartella di un job si crea e si elimina in un altro thread (come lo scratch a fine job) mentre se ne calcola
    il percorso: safe_join non deve mai rifiutare l'id valido. Su Windows Path.resolve() di una cartella in
    eliminazione restituisce \\\\?\\C:\\... o C:\\$Extend\\$Deleted\\... (fuori dalla radice), anche per la cartella
    madre di un file annidato: prima GET /api/jobs/{id} rispondeva 400 unsafe_path per un istante."""
    base = tmp_path / "jobs"
    base.mkdir()
    folder = base / JOB_ID
    expected = base.resolve().joinpath(*parts)
    stop = threading.Event()

    def churn():
        while not stop.is_set():
            (folder / "sub").mkdir(parents=True, exist_ok=True)
            (folder / "sub" / "job.log").write_text("x")
            shutil.rmtree(folder, ignore_errors=True)

    thread = threading.Thread(target=churn, daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:  # su Windows la risoluzione «fuori» arriva entro pochi millisecondi
            assert storage.safe_join(base, *parts) == expected
    finally:
        stop.set()
        thread.join(10)


@pytest.mark.parametrize("parts", [
    ("..",), ("..", "fuori"), ("sub", "..", "..", "fuori"), ("a/../../fuori",), (JOB_ID, "..", "..", "fuori"),
], ids=["madre", "sorella", "risalita", "risalita-in-un-nome", "risalita-da-un-job"])
def test_safe_join_rejects_paths_leaving_the_root(tmp_path, parts):
    base = tmp_path / "jobs"
    (base / JOB_ID).mkdir(parents=True)
    assert _unsafe(base, *parts)
    assert _unsafe(base, str(tmp_path / "fuori"))  # percorso assoluto fuori dalla radice
    if sys.platform == "win32":
        assert _unsafe(base, "..\\fuori") and _unsafe(base, str(tmp_path / "fuori").replace("\\", "/"))
    assert storage.safe_join(base) == storage.safe_join(base, ".") == base.resolve()  # la radice stessa sì


def test_safe_join_fallback_keeps_the_guarantees(tmp_path, monkeypatch):
    """Il ripiego per le cartelle in eliminazione, provato su ogni sistema: Path.resolve() porta fuori dalla radice
    tutto ciò che sta sotto la cartella del job (come \\\\?\\ o $Extend\\$Deleted su Windows). I nomi validi tornano
    dentro la radice; '..', nomi di soli punti o spazi e madri fuori dalla radice restano rifiutati."""
    base = tmp_path / "jobs"
    (base / JOB_ID / "sub").mkdir(parents=True)
    root = base.resolve()
    folder, elsewhere = root / JOB_ID, tmp_path.resolve() / "$Deleted"
    real_resolve = Path.resolve

    def deleting(self, strict=False):
        out = real_resolve(self, strict)
        return elsewhere / out.relative_to(folder) if out == folder or folder in out.parents else out

    monkeypatch.setattr(Path, "resolve", deleting)
    assert storage.safe_join(base, JOB_ID) == folder
    assert storage.safe_join(base, JOB_ID, "sub", "job.log") == folder / "sub" / "job.log"
    assert storage.safe_join(base, f"{JOB_ID}/sub") == folder / "sub"
    for parts in [(JOB_ID, "..", "..", "fuori"), (JOB_ID, "..."), (JOB_ID, "sub", " "), (JOB_ID, ".. ", "x"),
                  (str(elsewhere / JOB_ID),)]:  # l'ultimo: percorso assoluto, nessuna madre dentro la radice
        assert _unsafe(base, *parts), parts


def _link_dir(kind: str, link: Path, target: Path) -> None:
    if kind == "junction":
        _winapi = pytest.importorskip("_winapi", reason="giunzioni solo su Windows")
        _winapi.CreateJunction(str(target), str(link))
        return
    try:
        os.symlink(target, link, target_is_directory=True)
    except (OSError, NotImplementedError) as exc:  # Windows senza modalità sviluppatore: WinError 1314
        pytest.skip(f"link simbolici non disponibili: {exc}")


@pytest.mark.parametrize("kind", ["symlink", "junction"])
def test_safe_join_rejects_links_leaving_the_root(tmp_path, kind):
    """Un link (o una giunzione di Windows, che os.path.islink non vede) verso fuori dalla radice resta rifiutato,
    come ultimo nome o come cartella intermedia; uno che resta dentro la radice si segue."""
    base, outside = tmp_path / "jobs", tmp_path / "fuori"
    (base / "vera").mkdir(parents=True)
    outside.mkdir()
    (outside / "segreto.txt").write_text("x")
    _link_dir(kind, base / "uscita", outside)
    _link_dir(kind, base / "alias", base / "vera")
    assert _unsafe(base, "uscita")
    assert _unsafe(base, "uscita", "segreto.txt")
    assert _unsafe(base, "uscita", "manca", "x.pdb")
    assert storage.safe_join(base, "alias", "f.pdb") == base.resolve() / "vera" / "f.pdb"


def test_remove_tree_single_file_never_raises(tmp_path, monkeypatch):
    first, second = tmp_path / "primo.txt", tmp_path / "secondo.txt"
    first.write_text("x")
    second.write_text("y")
    real_unlink = Path.unlink

    def chmod_refused(*_args, **_kwargs):
        raise PermissionError(1, "operazione non permessa")

    def unlink_refused(self, *args, **kwargs):
        if self.name == second.name:
            raise PermissionError(13, "file in uso")
        return real_unlink(self, *args, **kwargs)

    monkeypatch.setattr(storage.os, "chmod", chmod_refused)
    assert storage.remove_tree(first) is True and not first.exists()  # i permessi non servivano
    monkeypatch.setattr(Path, "unlink", unlink_refused)
    assert storage.remove_tree(second) is False and second.exists()  # nessuna eccezione: il risultato lo dice


def test_housekeeping_goes_on_past_an_undeletable_entry(core, monkeypatch):
    uploads = core.paths.uploads
    uploads.mkdir(parents=True, exist_ok=True)
    stuck = uploads / "vecchio.pdb"
    stuck.write_text("ATOM\n")
    old = time.time() - 400 * 86400
    os.utime(stuck, (old, old))
    tomb = core.jobs.jobs_dir / f"{TOMBSTONE_PREFIX}dddd"
    tomb.mkdir()
    (tomb / "resto.bin").write_bytes(b"z" * 10)
    real_unlink = Path.unlink

    def unlink_refused(self, *args, **kwargs):
        if self.name == stuck.name:
            raise PermissionError(13, "file in uso")
        return real_unlink(self, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", unlink_refused)
    report = core.housekeeping.run()
    assert report["uploads"] == 0 and stuck.exists()  # non eliminato: non si conta, si riprova la prossima volta
    assert report["jobs"] == 1 and not tomb.exists()  # il resto del job eliminato si toglie comunque
    monkeypatch.undo()
    assert core.housekeeping.run()["uploads"] == 1 and not stuck.exists()


def test_build_core_survives_a_failing_initial_cleanup(tmp_path, monkeypatch):
    def broken(_self):
        raise RuntimeError("pulizia guasta")

    monkeypatch.setattr(housekeeping_module.Housekeeping, "run", broken)
    built = build_core(Paths(tmp_path / "dati"))  # come il passo giornaliero: l'avvio non si ferma
    try:
        assert built.cleanup is not None and built.projects.list()
    finally:
        built.close()


def test_housekeeping_keeps_scratch_of_every_active_or_busy_job(core):
    (oldest,) = make_jobs(core, 1, status="running", age_days=30)  # dietro 500 più recenti
    make_jobs(core, 500, status="running")
    (closing,) = make_jobs(core, 1, status="completed")  # già terminato, file ancora da spostare
    scratch = core.paths.scratch / "jobs"
    orphan = "0123456789abcdef"
    for name in (oldest, closing, orphan):
        (scratch / name).mkdir(parents=True, exist_ok=True)
        (scratch / name / "lavoro.tmp").write_text("x")
    core.jobs._running.add(closing)
    try:
        report = core.housekeeping.run()
    finally:
        core.jobs._running.discard(closing)
    assert (scratch / oldest / "lavoro.tmp").exists() and (scratch / closing / "lavoro.tmp").exists()
    assert not (scratch / orphan).exists() and report["scratch"] == 1


def test_job_delete_returns_freed_bytes_and_no_tombstone(client, core):
    first, second = make_jobs(core, 2, status="failed", size=100)
    assert core.jobs.delete(first) == Deleted(100, False)
    assert not (core.jobs.jobs_dir / first).exists()
    r = client.delete(f"/api/jobs/{second}")
    assert r.status_code == 200 and r.json() == {"deleted": second, "freed_bytes": 100, "leftover": 0}
    assert_consistent(core)


def test_job_delete_keeps_job_intact_when_folder_is_in_use(client, core, monkeypatch):
    (job,) = make_jobs(core, 1, status="failed")
    real_replace = os.replace

    def replace_in_use(src, dst, *args, **kwargs):
        if os.path.basename(dst).startswith(TOMBSTONE_PREFIX):
            raise PermissionError(13, "file in uso")
        return real_replace(src, dst, *args, **kwargs)

    monkeypatch.setattr(jobs_module.os, "replace", replace_in_use)
    with pytest.raises(Conflict, match="in uso"):
        core.jobs.delete(job)
    r = client.delete(f"/api/jobs/{job}")
    assert r.status_code == 409 and "chiudili e riprova" in r.json()["error"]["message"]
    assert core.jobs.get(job)["status"] == "failed"  # il job resta intero: riga e file
    assert (core.jobs.jobs_dir / job / "result.json").exists()
    assert not core.jobs._removing
    monkeypatch.undo()
    core.jobs.delete(job)
    assert_consistent(core)


def test_job_delete_partial_removal_leaves_tombstone_and_housekeeping_purges_it(core, monkeypatch):
    (job,) = make_jobs(core, 1, status="cancelled", size=100)
    (core.jobs.jobs_dir / job / "aperto.pdb").write_bytes(b"y" * 30)
    real = jobs_module.remove_tree
    monkeypatch.setattr(jobs_module, "remove_tree",
                        stubborn_remove(real, lambda path: path.name.startswith(TOMBSTONE_PREFIX)))
    assert core.jobs.delete(job) == Deleted(100, True)  # i 30 byte rimasti non contano
    tomb = core.jobs.jobs_dir / f"{TOMBSTONE_PREFIX}{job}"
    assert (tomb / "aperto.pdb").exists() and not (core.jobs.jobs_dir / job).exists()
    with pytest.raises(NotFound):
        core.jobs.get(job)
    monkeypatch.setattr(jobs_module, "remove_tree", real)  # file chiuso
    report = core.housekeeping.run()
    assert report["jobs"] == 1 and report["freed_bytes"] >= 30 and not tomb.exists()
    assert_consistent(core)


def test_purge_tombstones_skips_those_being_removed(core):
    first, second = (core.jobs.jobs_dir / f"{TOMBSTONE_PREFIX}{name}" for name in ("aaaa", "bbbb"))
    for folder in (first, second):
        folder.mkdir()
        (folder / "resto.bin").write_bytes(b"z" * 50)
    core.jobs._removing.add(first.name)  # un'eliminazione lo sta togliendo
    try:
        assert core.jobs.purge_tombstones() == (1, 50)
        assert first.exists() and not second.exists()
    finally:
        core.jobs._removing.discard(first.name)
    assert core.jobs.purge_tombstones() == (1, 50)
    assert_consistent(core)


def tomb_of(core, job_id: str) -> Path:
    return core.jobs.jobs_dir / f"{TOMBSTONE_PREFIX}{job_id}"


def test_purge_tombstones_never_removes_the_folder_of_an_existing_job(client, core):
    """Rinomino inverso non riuscito o arresto brusco fra il rinomino e il DELETE: la riga c'è ancora."""
    starred, occupied = make_jobs(core, 2, status="failed", size=60)
    assert client.put(f"/api/favorites/job/{starred}").status_code == 200
    for job_id in (starred, occupied):
        os.replace(core.jobs.jobs_dir / job_id, tomb_of(core, job_id))
    (core.jobs.jobs_dir / occupied).mkdir()  # nome occupato: il resto resta dov'è
    assert core.jobs.purge_tombstones() == (0, 0)
    assert (core.jobs.jobs_dir / starred / "result.json").exists() and not tomb_of(core, starred).exists()
    assert (tomb_of(core, occupied) / "result.json").exists()
    assert core.housekeeping.run()["jobs"] == 0 and tomb_of(core, occupied).exists()
    (core.jobs.jobs_dir / occupied).rmdir()
    assert core.jobs.purge_tombstones() == (0, 0)  # ora il nome è libero: torna al suo posto
    assert (core.jobs.jobs_dir / occupied / "result.json").exists()
    assert core.jobs.delete(starred).freed_bytes == 60 and core.jobs.delete(occupied).freed_bytes == 60
    assert_consistent(core)


def test_purge_tombstones_does_not_count_a_vanished_one(core, monkeypatch):
    tomb = core.jobs.jobs_dir / f"{TOMBSTONE_PREFIX}cccc"
    tomb.mkdir()
    (tomb / "resto.bin").write_bytes(b"z" * 5)
    real_lock = core.jobs._lock

    class VanishingLock:
        """Il resto sparisce (tolto da un'eliminazione) fra l'elenco e la prenotazione."""

        def __enter__(self):
            if tomb.exists():
                shutil.rmtree(tomb)
            return real_lock.__enter__()

        def __exit__(self, *exc):
            return real_lock.__exit__(*exc)

    monkeypatch.setattr(core.jobs, "_lock", VanishingLock())
    assert core.jobs.purge_tombstones() == (0, 0)
    monkeypatch.undo()
    assert_consistent(core)


def test_job_delete_treats_folder_left_in_tombstone_as_the_jobs(core):
    first, second = make_jobs(core, 2, status="failed", size=70)
    for job_id in (first, second):  # un rinomino inverso non riuscito in passato
        os.replace(core.jobs.jobs_dir / job_id, tomb_of(core, job_id))
    answers = [1, 0]  # condizione vera al controllo e falsa nel DELETE
    core.db.create_function("bioloom_test_flip_tomb", 0, lambda: answers.pop(0) if answers else 0)
    with pytest.raises(Conflict, match="non corrisponde più"):
        core.jobs.delete(first, only_if=("bioloom_test_flip_tomb()", ()))
    assert (core.jobs.jobs_dir / first / "result.json").exists() and not tomb_of(core, first).exists()
    assert core.jobs.delete(second) == Deleted(70, False)  # i suoi file erano nel tombstone: contano
    core.jobs.delete(first)
    assert_consistent(core)


def test_job_delete_with_two_folders_touches_neither_until_the_delete(core):
    (job,) = make_jobs(core, 1, status="failed", size=70)
    old = tomb_of(core, job)  # file di prima rimasti in `.deleting-<id>` accanto alla cartella ricreata dalla ripresa
    old.mkdir()
    (old / "model.pdb").write_bytes(b"y" * 30)
    answers = [1, 0]  # condizione vera al controllo e falsa nel DELETE
    core.db.create_function("bioloom_test_flip_both", 0, lambda: answers.pop(0) if answers else 0)
    with pytest.raises(Conflict, match="non corrisponde più"):
        core.jobs.delete(job, only_if=("bioloom_test_flip_both()", ()))
    assert (old / "model.pdb").exists() and (core.jobs.jobs_dir / job / "result.json").exists()
    assert core.jobs.delete(job) == Deleted(100, False)  # riuscito il DELETE, vanno via entrambe
    assert not old.exists() and not (core.jobs.jobs_dir / job).exists()
    assert_consistent(core)


def test_job_delete_retries_the_reverse_rename(core, monkeypatch):
    (job,) = make_jobs(core, 1, status="failed")
    real_replace = os.replace
    failures = []

    def busy_once(src, dst, *args, **kwargs):
        if os.path.basename(src).startswith(TOMBSTONE_PREFIX) and not failures:
            failures.append(src)
            raise PermissionError(13, "file aperto per un istante dall'antivirus")
        return real_replace(src, dst, *args, **kwargs)

    monkeypatch.setattr(jobs_module.os, "replace", busy_once)
    answers = [1, 0]
    core.db.create_function("bioloom_test_flip_undo", 0, lambda: answers.pop(0) if answers else 0)
    with pytest.raises(Conflict, match="non corrisponde più"):
        core.jobs.delete(job, only_if=("bioloom_test_flip_undo()", ()))
    assert failures and (core.jobs.jobs_dir / job / "result.json").exists() and not tomb_of(core, job).exists()
    monkeypatch.undo()
    core.jobs.delete(job)
    assert_consistent(core)


def test_delete_only_if_rejects_job_that_no_longer_matches(client, core):
    first, second = make_jobs(core, 2, status="failed")
    assert client.put(f"/api/favorites/job/{first}").status_code == 200
    with pytest.raises(Conflict, match="non corrisponde più"):
        core.jobs.delete(first, only_if=(f"NOT {STARRED}", ()))
    assert core.jobs.get(first)["status"] == "failed" and (core.jobs.jobs_dir / first / "result.json").exists()
    assert not list(core.jobs.jobs_dir.glob(f"{TOMBSTONE_PREFIX}*"))
    # condizione vera al controllo e falsa nel DELETE: il rinomino della cartella si annulla
    answers = [1, 0]
    core.db.create_function("bioloom_test_flip", 0, lambda: answers.pop(0) if answers else 0)
    with pytest.raises(Conflict, match="non corrisponde più"):
        core.jobs.delete(second, only_if=("bioloom_test_flip()", ()))
    assert core.jobs.get(second)["status"] == "failed" and (core.jobs.jobs_dir / second / "result.json").exists()
    assert not list(core.jobs.jobs_dir.glob(f"{TOMBSTONE_PREFIX}*")) and not core.jobs._removing
    assert core.jobs.delete(first, only_if=(STARRED, ())).freed_bytes == 4  # la condizione vale: eliminato
    core.jobs.delete(second)
    assert_consistent(core)


def test_housekeeping_never_removes_job_folders_without_row(core):
    old = time.time() - 2 * 86400
    kept = [core.jobs.jobs_dir / name for name in ("0123456789abcdef", "ref", ".rerun-incoming")]
    for folder in kept:
        folder.mkdir(exist_ok=True)
        (folder / "risultato.pdb").write_text("ATOM\n")
        os.utime(folder / "risultato.pdb", (old, old))
        os.utime(folder, (old, old))
    report = core.housekeeping.run()
    assert set(report) == {"uploads", "cache", "scratch", "jobs", "freed_bytes", "at"}
    assert report["jobs"] == 0
    assert all((folder / "risultato.pdb").exists() for folder in kept)


def test_storage_reports_backups(client, core):
    backups = core.paths.db.parent / "backups"
    backups.mkdir(exist_ok=True)
    (backups / "bioloom.schema1.prova.db").write_bytes(b"b" * 1234)
    assert client.get("/api/system/storage").json()["backups"] == 1234
    assert client.post("/api/system/cleanup").status_code == 200
    assert (backups / "bioloom.schema1.prova.db").exists()  # le copie del database non si eliminano da sole


def test_dir_size_until_stops_at_deadline(tmp_path, monkeypatch):
    folder = tmp_path / "grande"
    (folder / "sotto").mkdir(parents=True)
    for i in range(300):
        (folder / f"f{i}.bin").write_bytes(b"x" * 10)
    (folder / "sotto" / "g.bin").write_bytes(b"x" * 7)
    assert storage.dir_size_until(folder, time.monotonic() + 60) == (3007, True)
    assert storage.dir_size_until(folder, time.monotonic() - 1) == (0, False)
    assert storage.dir_size_until(tmp_path / "assente", time.monotonic() - 1) == (0, True)
    ticks = itertools.count()
    monkeypatch.setattr(storage, "time", SimpleNamespace(monotonic=lambda: next(ticks), sleep=time.sleep))
    size, complete = storage.dir_size_until(folder, 1)  # scade al controllo dopo 256 file
    assert not complete and 0 < size < 3007


# ----------------------------------------------------------------------------------------------- progetti
def test_project_usage(client, core):
    pid = client.post("/api/projects", json={"name": "Grande"}).json()["id"]
    assert client.post(f"/api/projects/{pid}/sequences", json={"name": "s1", "sequence": HUMAN_UB}).status_code == 200
    assert client.post(f"/api/projects/{pid}/documents",
                       json={"name": "d", "kind": "pir", "content": ">P1;s1\n"}).status_code == 200
    done = make_jobs(core, 510, project_id=pid, size=10)
    failed = make_jobs(core, 3, project_id=pid, status="failed", size=10)
    make_jobs(core, 1, project_id=pid, status="running", size=10)
    (pipeline,) = make_jobs(core, 1, project_id=pid, kind="pipeline", status="failed", size=10)
    core.db.execute("INSERT INTO pipeline_items(job_id, idx, name, sequence, status) VALUES (?,0,'a','MK','completed')",
                    (pipeline,))
    make_jobs(core, 4, size=10)  # job senza progetto: non contano
    client.put(f"/api/favorites/job/{done[0]}")
    client.put(f"/api/favorites/job/{failed[0]}")
    client.post(f"/api/tags/job/{done[1]}", json={"tag": "da-rivedere"})
    before = core.db.scalar("SELECT COUNT(*) FROM jobs")
    r = client.get(f"/api/projects/{pid}/usage")
    assert r.status_code == 200, r.text
    usage = r.json()
    assert {k: usage[k] for k in ("id", "name", "sequences", "documents")} == \
        {"id": pid, "name": "Grande", "sequences": 1, "documents": 1}
    assert usage["created_at"] and usage["updated_at"]
    assert usage["jobs"] == {"total": 515, "active": 1, "starred": 2, "tagged": 1, "resumable": 1,
                             "by_status": {"completed": 510, "failed": 4, "running": 1}}
    assert usage["bytes"] == 515 * 10 and usage["bytes_complete"] is True
    assert core.db.scalar("SELECT COUNT(*) FROM jobs") == before  # sola lettura
    assert client.get("/api/projects/abcdef123456/usage").status_code == 404
    bad = client.get("/api/projects/-x/usage")
    assert bad.status_code == 400 and bad.json()["error"]["code"] == "invalid_identifier"


def test_delete_project_removes_more_than_500_jobs(client, core):
    a = core.projects.create("A")["id"]
    b = core.projects.create("B")["id"]
    make_jobs(core, 620, project_id=a, size=8)
    others = make_jobs(core, 30, project_id=b, size=8) + make_jobs(core, 5, size=8)
    r = client.delete(f"/api/projects/{a}")
    assert r.status_code == 200, r.text
    assert r.json() == {"deleted": a, "jobs_deleted": 620, "freed_bytes": 620 * 8, "leftover": 0, "replacement": None}
    assert {row["id"] for row in core.db.query("SELECT id FROM jobs")} == set(others)
    assert all((core.jobs.jobs_dir / job_id).is_dir() for job_id in others)
    assert client.get(f"/api/projects/{a}").status_code == 404
    assert_consistent(core)


def test_delete_project_active_job_beyond_first_500_conflicts_and_deletes_nothing(client, core):
    pid = core.projects.create("Attivo")["id"]
    (oldest,) = make_jobs(core, 1, project_id=pid, status="running", age_days=30)  # dietro 600 più recenti
    make_jobs(core, 600, project_id=pid)
    r = client.delete(f"/api/projects/{pid}")
    assert r.status_code == 409
    error = r.json()["error"]
    assert error["code"] == "project_active_jobs" and error["details"] == {"active": 1, "jobs_deleted": 0}
    assert "annullali prima di eliminarlo" in error["message"] and "già eliminati" not in error["message"]
    assert core.db.scalar("SELECT COUNT(*) FROM jobs WHERE project_id=?", (pid,)) == 601
    assert client.get(f"/api/projects/{pid}").status_code == 200
    core.db.execute("UPDATE jobs SET status='failed', finished_at=updated_at WHERE id=?", (oldest,))
    assert client.delete(f"/api/projects/{pid}").json()["jobs_deleted"] == 601
    assert_consistent(core)


def test_delete_project_job_started_mid_deletion_reports_jobs_already_deleted(client, core, monkeypatch):
    pid = core.projects.create("Ripartito")["id"]
    make_jobs(core, 3, project_id=pid)
    real = core.jobs.delete
    calls = []

    def delete_then_start_one(job_id, **kwargs):
        out = real(job_id, **kwargs)
        calls.append(job_id)
        if len(calls) == 3:  # inviato e partito durante la prima passata: lo vede la seconda
            make_jobs(core, 1, project_id=pid, status="running")
        return out

    monkeypatch.setattr(core.jobs, "delete", delete_then_start_one)
    r = client.delete(f"/api/projects/{pid}")
    assert r.status_code == 409
    error = r.json()["error"]
    assert error["code"] == "project_active_jobs" and error["details"] == {"active": 1, "jobs_deleted": 3}
    assert "3 job già eliminati" in error["message"]
    assert client.get(f"/api/projects/{pid}").status_code == 200
    monkeypatch.setattr(core.jobs, "delete", real)
    core.db.execute("UPDATE jobs SET status='failed', finished_at=updated_at WHERE project_id=?", (pid,))
    assert client.delete(f"/api/projects/{pid}").json()["jobs_deleted"] == 1
    assert_consistent(core)


def test_delete_project_removes_labels_sequences_and_documents(client, core):
    pid = client.post("/api/projects", json={"name": "Etichettato"}).json()["id"]
    seq = client.post(f"/api/projects/{pid}/sequences", json={"name": "s1", "sequence": HUMAN_UB}).json()
    client.post(f"/api/projects/{pid}/documents", json={"name": "d", "kind": "pir", "content": ">P1;s1\n"})
    (job,) = make_jobs(core, 1, project_id=pid)
    for etype, eid in (("project", pid), ("sequence", seq["id"]), ("job", job)):
        assert client.post(f"/api/tags/{etype}/{eid}", json={"tag": "zinco"}).status_code == 200
        assert client.put(f"/api/favorites/{etype}/{eid}").status_code == 200
    r = client.delete(f"/api/projects/{pid}")
    assert r.status_code == 200 and r.json()["jobs_deleted"] == 1
    for table in ("sequences", "documents", "jobs"):
        assert core.db.scalar(f"SELECT COUNT(*) FROM {table} WHERE project_id=?", (pid,)) == 0
    assert core.db.scalar("SELECT COUNT(*) FROM tags") == 0 and core.db.scalar("SELECT COUNT(*) FROM favorites") == 0
    assert_consistent(core)


def test_delete_last_project_creates_replacement(client, core):
    (default,) = [p["id"] for p in core.projects.list()]
    other = core.projects.create("Secondo")["id"]
    r = client.delete(f"/api/projects/{other}")
    assert r.status_code == 200 and r.json()["replacement"] is None  # non era l'ultimo
    make_jobs(core, 2, project_id=default)
    r = client.delete(f"/api/projects/{default}")
    assert r.status_code == 200, r.text
    out = r.json()
    replacement = out["replacement"]
    assert out["jobs_deleted"] == 2 and replacement["name"] == "Nuovo progetto"
    assert "primo avvio" not in replacement["description"]
    assert [p["id"] for p in client.get("/api/projects").json()] == [replacement["id"]]
    assert client.get(f"/api/projects/{replacement['id']}").json() == replacement  # stesso formato del GET
    assert_consistent(core)


def test_delete_project_busy_job_conflicts_after_partial_deletion(client, core, monkeypatch):
    pid = core.projects.create("In uso")["id"]
    ids = make_jobs(core, 5, project_id=pid)
    resumed(core, monkeypatch, ids[2])
    t0 = time.monotonic()
    r = client.delete(f"/api/projects/{pid}")
    assert time.monotonic() - t0 < 5  # nessuna attesa di 10 s
    assert r.status_code == 409
    error = r.json()["error"]
    assert error["code"] == "project_busy" and error["details"] == {"jobs_deleted": 2}
    assert "Eliminati 2 job" in error["message"] and "non è stato eliminato" in error["message"]
    assert client.get(f"/api/projects/{pid}").status_code == 200
    assert core.db.scalar("SELECT COUNT(*) FROM jobs WHERE project_id=?", (pid,)) == 3
    core.jobs._preparing.discard(ids[2])
    assert client.delete(f"/api/projects/{pid}").json()["jobs_deleted"] == 3  # ripetibile
    assert_consistent(core)


def test_delete_project_retries_when_a_job_appears(client, core, monkeypatch):
    real = core.jobs.delete
    once = core.projects.create("Vivace")["id"]
    make_jobs(core, 3, project_id=once)
    calls = []

    def delete_and_add_once(job_id, **kwargs):
        out = real(job_id, **kwargs)
        calls.append(job_id)
        if len(calls) == 1:
            make_jobs(core, 1, project_id=once)  # dopo l'ultimo DELETE della passata: serve la seconda
        return out

    monkeypatch.setattr(core.jobs, "delete", delete_and_add_once)
    r = client.delete(f"/api/projects/{once}")
    assert r.status_code == 200 and r.json()["jobs_deleted"] == 4
    endless = core.projects.create("Inarrestabile")["id"]
    make_jobs(core, 2, project_id=endless)

    def delete_and_add_always(job_id, **kwargs):
        out = real(job_id, **kwargs)
        make_jobs(core, 1, project_id=endless)
        return out

    monkeypatch.setattr(core.jobs, "delete", delete_and_add_always)
    r = client.delete(f"/api/projects/{endless}")
    assert r.status_code == 409
    error = r.json()["error"]
    assert error["code"] == "project_busy" and "continua a ricevere nuovi job" in error["message"]
    assert error["details"] == {"jobs_deleted": 2 * jobcleanup.PROJECT_PASSES}  # ogni passata: i job del suo inizio
    assert client.get(f"/api/projects/{endless}").status_code == 200
    monkeypatch.setattr(core.jobs, "delete", real)
    assert client.delete(f"/api/projects/{endless}").json()["jobs_deleted"] == 2
    assert_consistent(core)


def test_delete_project_in_flight_guard(client, core, monkeypatch):
    pid = core.projects.create("Lento")["id"]
    make_jobs(core, 2, project_id=pid)
    entered, release = threading.Event(), threading.Event()
    real = core.jobs.delete

    def slow_delete(job_id, **kwargs):
        entered.set()
        release.wait(10)
        return real(job_id, **kwargs)

    monkeypatch.setattr(core.jobs, "delete", slow_delete)
    result = {}
    worker = threading.Thread(target=lambda: result.update(out=core.cleanup.delete_project(pid)))
    worker.start()
    try:
        assert entered.wait(5)
        t0 = time.monotonic()
        r = client.delete(f"/api/projects/{pid}")
        assert r.status_code == 409 and r.json()["error"]["code"] == "delete_in_progress"
        assert time.monotonic() - t0 < 2  # subito, senza aspettare la prima
    finally:
        release.set()
        worker.join(20)
    assert result["out"]["deleted"] == pid and result["out"]["jobs_deleted"] == 2
    assert client.delete(f"/api/projects/{pid}").status_code == 404
    assert_consistent(core)


def test_delete_two_last_projects_concurrently_keeps_exactly_one(core):
    for _ in range(5):
        (first,) = [p["id"] for p in core.projects.list()]
        second = core.projects.create("Altro")["id"]
        barrier = threading.Barrier(2)
        results, failures = [], []

        def delete(project_id, barrier=barrier, results=results, failures=failures):
            barrier.wait(5)
            try:
                results.append(core.cleanup.delete_project(project_id))
            except Exception as exc:  # l'esito si controlla nel thread principale
                failures.append(exc)

        threads = [threading.Thread(target=delete, args=(pid,)) for pid in (first, second)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(20)
        assert not failures and len(results) == 2
        projects = core.projects.list()
        replacements = [r["replacement"] for r in results if r["replacement"]]
        assert len(projects) == 1 and len(replacements) == 1 and replacements[0]["id"] == projects[0]["id"]
    assert_consistent(core)


def test_delete_project_stops_on_shutdown(client, core, monkeypatch):
    pid = core.projects.create("Arresto")["id"]
    make_jobs(core, 3, project_id=pid)
    monkeypatch.setattr(core.cleanup, "stopping", lambda: True)
    r = client.delete(f"/api/projects/{pid}")
    assert r.status_code == 503 and r.json()["error"]["code"] == "shutting_down"
    assert client.get(f"/api/projects/{pid}").status_code == 200
    assert core.db.scalar("SELECT COUNT(*) FROM jobs WHERE project_id=?", (pid,)) == 3
    monkeypatch.undo()
    assert client.delete(f"/api/projects/{pid}").json()["jobs_deleted"] == 3
    assert_consistent(core)


# ----------------------------------------------------------------------------------------------- pulizia
def test_cleanup_preview_changes_nothing_and_matches_run(client, core):
    make_jobs(core, 12, status="failed", age_days=10, size=25)
    make_jobs(core, 3, status="completed", age_days=10, size=25)
    make_jobs(core, 2, status="failed", age_days=1, size=25)
    request = {"scope": "all", "statuses": ["failed", "cancelled"], "older_than_days": 7}
    r = client.post("/api/jobs/cleanup/preview", json=request)
    assert r.status_code == 200, r.text
    preview = r.json()
    assert preview["matched"] == 12 and preview["bytes"] == 12 * 25 and preview["bytes_complete"] is True
    assert preview["by_status"] == {"failed": 12} and preview["by_kind"] == {"model": 12}
    assert core.db.scalar("SELECT COUNT(*) FROM jobs") == 17  # sola lettura
    again = client.post("/api/jobs/cleanup/preview", json=request).json()
    assert AS_OF.match(again.pop("as_of")) and AS_OF.match(preview["as_of"])  # istante della richiesta, al secondo
    assert again == {k: v for k, v in preview.items() if k != "as_of"}  # dalla memoria: uguale
    run = client.post("/api/jobs/cleanup", json=request).json()
    assert run == {"deleted": 12, "skipped": 0, "errors": 0, "freed_bytes": 12 * 25, "leftover": 0, "remaining": 0}
    assert run["deleted"] == preview["matched"] and run["freed_bytes"] == preview["bytes"]
    assert core.db.scalar("SELECT COUNT(*) FROM jobs") == 5
    assert_consistent(core)


# nome: (progetto, tipo, stato, finished_at, updated_at, created_at); date in giorni prima di NOW, None = NULL,
# testo = valore letterale. "D" è un progetto inesistente (job con project_id penzolante).
FILTER_ROWS = {
    "a_completed": ("A", "model", "completed", 10, 10, 10),
    "a_failed": ("A", "model", "failed", 10, 10, 10),
    "a_cancelled": ("A", "quality", "cancelled", 10, 10, 10),
    "a_recent": ("A", "model", "failed", 1, 1, 1),
    "a_star": ("A", "model", "failed", 10, 10, 10),
    "a_tag": ("A", "model", "failed", 10, 10, 10),
    "b_failed": ("B", "model", "failed", 10, 10, 10),
    "global": (None, "model", "failed", 10, 10, 10),
    "dangling": ("D", "model", "failed", 10, 10, 10),
    # età = fine del job, altrimenti ultimo aggiornamento (updated_at non è mai NULL); data illeggibile = si conserva
    "fin_old": ("B", "align", "failed", 10, 1, 1),
    "upd_old": ("B", "align", "failed", None, 10, 10),
    "upd_recent": ("B", "align", "failed", None, 1, 10),
    "bad_date": ("B", "align", "failed", "non-una-data", 10, 10),
}
FILTER_CASES = [
    ({"scope": "all", "statuses": ["failed", "cancelled"]},
     {"a_failed", "a_cancelled", "a_recent", "b_failed", "global", "dangling", "fin_old", "upd_old", "upd_recent",
      "bad_date"}),
    ({"scope": "all", "statuses": ["failed"], "older_than_days": 7},
     {"a_failed", "b_failed", "global", "dangling", "fin_old", "upd_old"}),
    ({"scope": "project", "project_id": "A", "statuses": ["completed", "failed", "cancelled"]},
     {"a_completed", "a_failed", "a_cancelled", "a_recent"}),
    ({"scope": "project", "project_id": "B", "statuses": ["failed"]},
     {"b_failed", "fin_old", "upd_old", "upd_recent", "bad_date"}),
    ({"scope": "all", "statuses": ["failed"], "kinds": ["align"]}, {"fin_old", "upd_old", "upd_recent", "bad_date"}),
    ({"scope": "all", "statuses": ["cancelled", "cancelled"], "kinds": ["quality", "model"]}, {"a_cancelled"}),
    ({"scope": "project", "project_id": "A", "statuses": ["failed"], "keep_favorites": False},
     {"a_failed", "a_recent", "a_star"}),
    ({"scope": "project", "project_id": "A", "statuses": ["failed"], "keep_tagged": False},
     {"a_failed", "a_recent", "a_tag"}),
]


def filter_dataset(core) -> tuple[dict[str, str], dict[str, str]]:
    """Job di FILTER_ROWS con date relative a NOW: (nome → id, nome del progetto → id)."""
    projects = {"A": core.projects.create("A")["id"], "B": core.projects.create("B")["id"], "D": "zzzzzzzzzzzz"}

    def when(value):
        return value if value is None or isinstance(value, str) else stamp(value, NOW)

    ids = {name: f"{next(_ids):016x}" for name in FILTER_ROWS}
    core.db.executemany(
        "INSERT INTO jobs(id, kind, title, project_id, status, created_at, finished_at, updated_at) "
        "VALUES (?,?,?,?,?,?,?,?)",
        [(ids[name], kind, name, projects.get(project), status, when(created), when(finished), when(updated))
         for name, (project, kind, status, finished, updated, created) in FILTER_ROWS.items()])
    for job_id in ids.values():
        (core.jobs.jobs_dir / job_id).mkdir()
        (core.jobs.jobs_dir / job_id / "result.json").write_bytes(b"x" * 4)
    core.labels.set_favorite("job", ids["a_star"], True)
    core.labels.add_tag("job", ids["a_tag"], "tenere")
    return ids, projects


@pytest.mark.parametrize("body, expected", FILTER_CASES)
def test_cleanup_filters(core, body, expected):
    ids, projects = filter_dataset(core)
    body = dict(body)
    if "project_id" in body:
        body["project_id"] = projects[body["project_id"]]
    c = core.cleanup.criteria(body)
    preview = core.cleanup.preview(c, now=NOW)
    assert preview["matched"] == len(expected) and preview["bytes"] == 4 * len(expected)
    out = core.cleanup.run(c, now=NOW)
    assert out["deleted"] == len(expected) and out["skipped"] == 0 and out["remaining"] == 0
    left = {row["id"] for row in core.db.query("SELECT id FROM jobs")}
    assert left == {ids[name] for name in FILTER_ROWS if name not in expected}
    assert_consistent(core, dangling=(ids["dangling"],))


def test_cleanup_never_touches_active_jobs(client, core):
    active = make_jobs(core, 1, status="running") + make_jobs(core, 1, status="queued") + \
        make_jobs(core, 1, status="cancelling")
    make_jobs(core, 3, status="failed")
    for statuses in (["running"], ["failed", "queued"], ["cancelling"]):
        r = client.post("/api/jobs/cleanup", json={"scope": "all", "statuses": statuses})
        assert r.status_code == 400 and r.json()["error"]["code"] == "invalid_cleanup"
        assert r.json()["error"]["message"] == "I job in coda o in esecuzione non si eliminano: annullali prima."
    for statuses in ([], ["boh"]):
        r = client.post("/api/jobs/cleanup/preview", json={"scope": "all", "statuses": statuses})
        assert r.status_code == 400 and r.json()["error"]["code"] == "invalid_cleanup"
    everything = {"scope": "all", "statuses": ["completed", "failed", "cancelled"], "keep_favorites": False,
                  "keep_tagged": False, "include_resumable": True}
    out = client.post("/api/jobs/cleanup", json=everything).json()
    assert out["deleted"] == 3 and out["remaining"] == 0
    assert {row["id"] for row in core.db.query("SELECT id FROM jobs")} == set(active)
    assert_consistent(core)


def test_cleanup_requires_explicit_scope(client, core):
    pid = core.projects.list()[0]["id"]
    make_jobs(core, 2, project_id=pid, status="failed")
    invalid = ({"statuses": ["failed"]}, {"scope": "", "statuses": ["failed"]},
               {"scope": "tutti", "statuses": ["failed"]}, {"scope": "project", "statuses": ["failed"]},
               {"scope": "all", "project_id": pid, "statuses": ["failed"]})
    for body in invalid:
        for route in ("/api/jobs/cleanup", "/api/jobs/cleanup/preview"):
            r = client.post(route, json=body)
            assert r.status_code == 400 and r.json()["error"]["code"] == "invalid_cleanup", (route, body, r.text)
    missing = {"scope": "project", "project_id": "abcdef123456", "statuses": ["failed"]}
    r = client.post("/api/jobs/cleanup", json=missing)
    assert r.status_code == 404 and r.json()["error"]["code"] == "not_found"
    assert core.db.scalar("SELECT COUNT(*) FROM jobs") == 2  # nulla eliminato


@pytest.mark.parametrize("extra", [{"kinds": []}, {"kinds": ["../x"]}, {"older_than_days": -1},
                                   {"older_than_days": 36501}, {"older_than_days": 1.5}, {"older_than_days": True},
                                   {"older_than_days": "7"}])
def test_cleanup_rejects_invalid_kinds_and_age(client, extra):
    r = client.post("/api/jobs/cleanup/preview", json={"scope": "all", "statuses": ["failed"], **extra})
    assert r.status_code == 400 and r.json()["error"]["code"] == "invalid_cleanup", r.text


@pytest.mark.parametrize("extra", [
    {"scope": None}, {"scope": ["all"]}, {"scope": "project", "project_id": 5}, {"project_id": ["x"]},
    {"statuses": "failed"}, {"statuses": None}, {"statuses": [None]}, {"statuses": [["failed"]]},
    {"kinds": "model"}, {"kinds": [3]}, {"keep_favorites": "no"}, {"keep_favorites": None}, {"keep_tagged": 0},
    {"include_resumable": "true"}, {"max_seconds": "5"}, {"max_seconds": True}, {"max_seconds": [5]},
    {"as_of": 0}])
def test_cleanup_bad_values_are_400_in_italian_never_422(client, core, extra):
    (job,) = make_jobs(core, 1, status="failed")
    client.put(f"/api/favorites/job/{job}")  # "no" al posto di false non deve mai voler dire "elimina le stelle"
    body = {"scope": "all", "statuses": ["failed"], **extra}
    routes = ["/api/jobs/cleanup"] + ([] if "max_seconds" in extra else ["/api/jobs/cleanup/preview"])
    for route in routes:
        r = client.post(route, json=body)
        assert r.status_code == 400, (route, r.text)  # dal servizio, con il motivo in italiano (non 422 di pydantic)
        assert r.json()["error"]["code"] == "invalid_cleanup" and r.json()["error"]["message"]
    assert core.db.scalar("SELECT COUNT(*) FROM jobs") == 1  # nulla eliminato


def test_cleanup_max_seconds_defaults_and_limits(client, core):
    make_jobs(core, 2, status="failed")
    for value in (None, 0, 10**400, 3.5):  # null = predefinito; i numeri si limitano in silenzio
        r = client.post("/api/jobs/cleanup", json={"scope": "all", "statuses": ["failed"], "max_seconds": value})
        assert r.status_code == 200, r.text
    assert core.db.scalar("SELECT COUNT(*) FROM jobs") == 0
    assert_consistent(core)


FUTURE = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat(timespec="seconds")


@pytest.mark.parametrize("as_of", ["ieri", 123, True, ["2026-06-01T12:00:00+00:00"], {}, "2026-06-01T12:00:00",
                                   "2026-06-01", FUTURE])
def test_cleanup_rejects_invalid_as_of(client, as_of):
    body = {"scope": "all", "statuses": ["failed"], "as_of": as_of}
    for route in ("/api/jobs/cleanup", "/api/jobs/cleanup/preview"):
        r = client.post(route, json=body)
        assert r.status_code == 400, (route, r.text)
        assert r.json()["error"]["code"] == "invalid_cleanup" and "as_of" in r.json()["error"]["message"]


def test_cleanup_as_of_is_normalised_to_utc_seconds(core):
    for text in ("2026-06-01T12:00:00+00:00", "2026-06-01T14:00:00.750+02:00", "2026-06-01T12:00:00Z"):
        assert criteria(core, statuses=["failed"], as_of=text).as_of == NOW
    soon = datetime.now(timezone.utc) + timedelta(seconds=30)  # orologi poco disallineati: accettato
    assert criteria(core, statuses=["failed"], as_of=soon.isoformat()).as_of == soon.replace(microsecond=0)
    assert criteria(core, statuses=["failed"]).as_of is None


def test_cleanup_run_never_goes_beyond_the_confirmed_preview(client, core, monkeypatch):
    shown = make_jobs(core, 3, status="failed", age_days=10)
    request = {"scope": "all", "statuses": ["failed"], "older_than_days": None}  # qualsiasi età
    preview = client.post("/api/jobs/cleanup/preview", json=request).json()
    assert preview["matched"] == 3 and AS_OF.match(preview["as_of"])
    after = (datetime.fromisoformat(preview["as_of"]) + timedelta(seconds=30)).isoformat(timespec="seconds")
    (late,) = make_jobs(core, 1, status="failed")  # fallito dopo l'anteprima: mai mostrato
    core.db.execute("UPDATE jobs SET finished_at=?, updated_at=? WHERE id=?", (after, after, late))
    real = core.jobs.delete

    def resumed_and_failed_again(job_id, **kwargs):  # mostrato, ripreso e fallito di nuovo mentre il ciclo gira
        if job_id == shown[1]:
            core.db.execute("UPDATE jobs SET finished_at=?, updated_at=? WHERE id=?", (after, after, job_id))
        return real(job_id, **kwargs)

    monkeypatch.setattr(core.jobs, "delete", resumed_and_failed_again)
    out = client.post("/api/jobs/cleanup", json={**request, "as_of": preview["as_of"]}).json()
    assert out == {"deleted": 2, "skipped": 1, "errors": 0, "freed_bytes": 8, "leftover": 0, "remaining": 0}
    assert {row["id"] for row in core.db.query("SELECT id FROM jobs")} == {shown[1], late}
    monkeypatch.setattr(core.jobs, "delete", real)
    # senza as_of (svuotamento di un progetto) vale l'adesso di ogni chiamata: tutto
    assert client.post("/api/jobs/cleanup", json=request).json()["deleted"] == 2
    assert_consistent(core)


def test_cleanup_as_of_is_the_now_of_the_age_cutoff(core):
    old, recent, later = make_jobs(core, 3, status="failed")
    for job_id, when in ((old, stamp(10, NOW)), (recent, stamp(5, NOW)), (later, stamp(-1 / 24, NOW))):
        core.db.execute("UPDATE jobs SET finished_at=?, updated_at=? WHERE id=?", (when, when, job_id))
    c = criteria(core, statuses=["failed"], older_than_days=7, as_of=NOW.isoformat())
    preview = core.cleanup.preview(c)  # nessun `now` iniettato: vale as_of
    assert preview["matched"] == 1 and preview["as_of"] == "2026-06-01T12:00:00+00:00"
    assert core.cleanup.run(c)["deleted"] == 1
    assert {row["id"] for row in core.db.query("SELECT id FROM jobs")} == {recent, later}
    c = criteria(core, statuses=["failed"], as_of=NOW.isoformat())  # qualsiasi età, ma non dopo NOW
    assert core.cleanup.preview(c)["matched"] == 1 and core.cleanup.run(c)["deleted"] == 1
    assert {row["id"] for row in core.db.query("SELECT id FROM jobs")} == {later}
    assert_consistent(core)


def test_cleanup_as_of_spares_a_job_finished_in_the_same_second(client, core):
    (job,) = make_jobs(core, 1, status="failed", age_days=10)
    request = {"scope": "all", "statuses": ["failed"], "older_than_days": None}
    as_of = client.post("/api/jobs/cleanup/preview", json=request).json()["as_of"]
    # fallito nello stesso secondo dell'anteprima, forse dopo la sua lettura: le date sono al secondo
    core.db.execute("UPDATE jobs SET finished_at=?, updated_at=? WHERE id=?", (as_of, as_of, job))
    out = client.post("/api/jobs/cleanup", json={**request, "as_of": as_of}).json()
    assert out["deleted"] == 0 and out["remaining"] == 0
    assert core.db.query_one("SELECT 1 FROM jobs WHERE id=?", (job,)) is not None
    assert_consistent(core)


def test_cleanup_preview_bytes_converge_past_a_slow_folder(core, monkeypatch):
    slow, quick, late = make_jobs(core, 3, status="failed")
    cost = {slow: 10.0, quick: 2.0, late: 2.0}  # secondi per misurare ciascuna cartella (BYTES_SECONDS = 3)
    sizes = {slow: 1000, quick: 20, late: 300}
    clock = [0.0]

    def fake_size(path, deadline):
        need = cost[path.name]
        if clock[0] + need <= deadline:
            clock[0] += need
            return sizes[path.name], True
        clock[0] = deadline  # tempo scaduto a metà: letto solo un pezzo
        return 1, False

    monkeypatch.setattr(jobcleanup, "dir_size_until", fake_size)
    monkeypatch.setattr(jobcleanup, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    c = criteria(core, statuses=["failed"])
    results = [core.cleanup.preview(c) for _ in range(6)]
    assert not any(p["bytes_complete"] for p in results)  # la cartella lenta non finisce mai in tempo
    # quick e late, che da sole si misurano entro il tempo, diventano esatte; slow conta il pezzo letto
    assert results[-2]["bytes"] == results[-1]["bytes"] == 20 + 300 + 1


def test_cleanup_excludes_resumable_pipelines_by_default(client, core):
    resumable, plain = make_jobs(core, 2, kind="pipeline", status="failed")
    core.db.executemany("INSERT INTO pipeline_items(job_id, idx, name, sequence, status) VALUES (?,?,?,?,?)",
                        [(resumable, 0, "a", "MKV", "completed"), (resumable, 1, "b", "MKV", "failed"),
                         (plain, 0, "c", "MKV", "failed")])
    request = {"scope": "all", "statuses": ["failed"]}
    preview = client.post("/api/jobs/cleanup/preview", json=request).json()
    assert preview["matched"] == 1 and preview["resumable"] == 1 and preview["sample"][0]["id"] == plain
    assert client.post("/api/jobs/cleanup", json=request).json()["deleted"] == 1
    assert core.jobs.get(resumable)["status"] == "failed"  # il lavoro già fatto resta riprendibile
    request["include_resumable"] = True
    preview = client.post("/api/jobs/cleanup/preview", json=request).json()
    assert preview["matched"] == 1 and preview["resumable"] == 0
    assert client.post("/api/jobs/cleanup", json=request).json()["deleted"] == 1
    assert core.db.scalar("SELECT COUNT(*) FROM pipeline_items") == 0
    assert_consistent(core)


def test_cleanup_preview_reports_protected_and_sample(client, core):
    ids = make_jobs(core, 8, status="failed")
    client.put(f"/api/favorites/job/{ids[0]}")
    client.post(f"/api/tags/job/{ids[1]}", json={"tag": "tenere"})
    preview = client.post("/api/jobs/cleanup/preview", json={"scope": "all", "statuses": ["failed"]}).json()
    assert preview["matched"] == 6 and preview["protected"] == 2 and preview["resumable"] == 0
    assert [s["id"] for s in preview["sample"]] == ids[2:7]  # i 5 più vecchi che verrebbero eliminati
    assert set(preview["sample"][0]) == {"id", "kind", "title", "status", "finished_at"}
    preview = client.post("/api/jobs/cleanup/preview", json={"scope": "all", "statuses": ["failed"],
                                                             "keep_favorites": False}).json()
    assert preview["matched"] == 7 and preview["protected"] == 1
    preview = client.post("/api/jobs/cleanup/preview", json={"scope": "all", "statuses": ["failed"],
                                                             "keep_favorites": False, "keep_tagged": False}).json()
    assert preview["matched"] == 8 and preview["protected"] == 0


def test_cleanup_respects_star_added_between_selection_and_delete(core, monkeypatch):
    ids = make_jobs(core, 3, status="failed")
    real = core.jobs.delete

    def star_then_delete(job_id, **kwargs):
        if job_id == ids[1]:
            core.labels.set_favorite("job", job_id, True)  # da un'altra scheda, dopo la selezione
        return real(job_id, **kwargs)

    monkeypatch.setattr(core.jobs, "delete", star_then_delete)
    out = core.cleanup.run(criteria(core, statuses=["failed"]))
    assert out["deleted"] == 2 and out["skipped"] == 1 and out["remaining"] == 0
    assert core.jobs.get(ids[1])["status"] == "failed" and (core.jobs.jobs_dir / ids[1] / "result.json").exists()
    assert_consistent(core)


def test_cleanup_more_than_500_jobs_in_a_loop(client, core):
    make_jobs(core, 2500, status="cancelled", size=1)
    request = {"scope": "all", "statuses": ["cancelled"], "max_seconds": 1}
    assert client.post("/api/jobs/cleanup/preview", json=request).json()["matched"] == 2500
    deleted = calls = 0
    while True:
        out = client.post("/api/jobs/cleanup", json=request).json()
        calls += 1
        deleted += out["deleted"]
        assert out["errors"] == 0 and out["skipped"] == 0 and out["remaining"] == 2500 - deleted
        if out["remaining"] == 0:
            break
        assert calls < 200
    assert deleted == 2500 and core.db.scalar("SELECT COUNT(*) FROM jobs") == 0
    assert not [p for p in core.jobs.jobs_dir.iterdir() if HEX16.match(p.name)]
    assert_consistent(core)


def test_cleanup_always_examines_one_job_per_call(core, monkeypatch):
    make_jobs(core, 4, status="failed")
    ticking_clock(monkeypatch)
    c = criteria(core, statuses=["failed"])
    for left in (3, 2, 1, 0):
        out = core.cleanup.run(c, max_seconds=0)  # limitato a MIN_SECONDS: almeno un tentativo comunque
        assert out["deleted"] == 1 and out["remaining"] == left
    assert_consistent(core)


def test_cleanup_skips_busy_without_waiting(core):
    ids = make_jobs(core, 4, status="failed")
    core.jobs._preparing.add(ids[1])  # attesa di JobManager.delete invariata (10 s): non si deve aspettare
    try:
        t0 = time.monotonic()
        out = core.cleanup.run(criteria(core, statuses=["failed"]))
        assert time.monotonic() - t0 < 1
        assert out["deleted"] == 3 and out["skipped"] == 1 and out["remaining"] == 0
    finally:
        core.jobs._preparing.discard(ids[1])
    assert core.jobs.get(ids[1])["status"] == "failed"
    assert_consistent(core)


def test_cleanup_persistently_skipped_job_does_not_keep_loop_alive(core, monkeypatch):
    ids = make_jobs(core, 5, status="failed")
    ticking_clock(monkeypatch)
    c = criteria(core, statuses=["failed"])
    core.jobs._preparing.add(ids[0])
    try:
        for calls in range(1, len(ids) + 1):
            out = core.cleanup.run(c, max_seconds=1)
            assert out["skipped"] == 1, calls  # ritrovato a ogni chiamata, ma non in `remaining`
            if out["remaining"] == 0:
                break
        assert out["remaining"] == 0 and calls < len(ids)
    finally:
        core.jobs._preparing.discard(ids[0])
    assert {row["id"] for row in core.db.query("SELECT id FROM jobs")} == {ids[0]}
    assert_consistent(core)


def test_cleanup_vanished_job_is_skipped(core, monkeypatch):
    ids = make_jobs(core, 3, status="failed")
    real = core.jobs.delete

    def vanish(job_id, **kwargs):
        if job_id == ids[0]:
            real(job_id)  # eliminato altrove fra la selezione e l'eliminazione
        return real(job_id, **kwargs)

    monkeypatch.setattr(core.jobs, "delete", vanish)
    out = core.cleanup.run(criteria(core, statuses=["failed"]))
    assert out == {"deleted": 2, "skipped": 1, "errors": 0, "freed_bytes": 8, "leftover": 0, "remaining": 0}
    assert core.db.scalar("SELECT COUNT(*) FROM jobs") == 0
    assert_consistent(core)


def test_cleanup_counts_errors_and_continues(core, monkeypatch):
    ids = make_jobs(core, 3, status="failed")
    real = core.jobs.delete

    def broken(job_id, **kwargs):
        if job_id == ids[1]:
            raise RuntimeError("guasto inatteso")
        return real(job_id, **kwargs)

    monkeypatch.setattr(core.jobs, "delete", broken)
    out = core.cleanup.run(criteria(core, statuses=["failed"]))
    assert out["deleted"] == 2 and out["errors"] == 1 and out["skipped"] == 0 and out["remaining"] == 0
    monkeypatch.setattr(core.jobs, "delete", real)
    core.jobs.delete(ids[1])
    assert_consistent(core)


def test_cleanup_events_labels_and_search(client, core):
    pid = core.projects.list()[0]["id"]
    ids = make_jobs(core, 3, project_id=pid, status="failed", title="Zinco legante")
    client.put(f"/api/favorites/job/{ids[0]}")
    client.post(f"/api/tags/job/{ids[1]}", json={"tag": "metalli"})

    def found_jobs() -> set[str]:
        groups = client.get("/api/search", params={"q": "zinco"}).json()["groups"]
        return {item["id"] for g in groups if g["type"] == "job" for item in g["items"]}

    assert found_jobs() == set(ids)
    version = core.jobs.events.version
    out = client.post("/api/jobs/cleanup", json={"scope": "project", "project_id": pid, "statuses": ["failed"],
                                                 "keep_favorites": False, "keep_tagged": False}).json()
    assert out["deleted"] == 3
    _, changes, _ = core.jobs.events.since(version)
    assert {(c.job_id, c.project_id) for c in changes if c.what == "deleted"} == {(job_id, pid) for job_id in ids}
    assert core.db.scalar("SELECT COUNT(*) FROM tags") == 0 and core.db.scalar("SELECT COUNT(*) FROM favorites") == 0
    assert found_jobs() == set()
    assert_consistent(core)


def test_cleanup_freed_bytes_excludes_leftover(core, monkeypatch):
    ids = make_jobs(core, 3, status="failed", size=100)
    (core.jobs.jobs_dir / ids[1] / "aperto.pdb").write_bytes(b"y" * 40)
    real = jobs_module.remove_tree
    monkeypatch.setattr(jobs_module, "remove_tree",
                        stubborn_remove(real, lambda path: path.name == f"{TOMBSTONE_PREFIX}{ids[1]}"))
    out = core.cleanup.run(criteria(core, statuses=["failed"]))
    assert out["deleted"] == 3 and out["leftover"] == 1 and out["freed_bytes"] == 300  # i 40 byte restano
    monkeypatch.setattr(jobs_module, "remove_tree", real)
    assert core.jobs.purge_tombstones() == (1, 40)
    assert_consistent(core)


def test_cleanup_routes(client):
    for route in ("/api/jobs/cleanup", "/api/jobs/cleanup/preview"):
        r = client.post(route, json={"scope": "boh", "statuses": ["failed"]})
        assert r.status_code == 400 and r.json()["error"]["code"] == "invalid_cleanup", r.text
        r = client.post(route, json={})
        assert r.status_code == 400 and r.json()["error"]["code"] == "invalid_cleanup", r.text
    assert client.post("/api/jobs/rerun/inspect").status_code not in (404, 405)  # le altre rotte instradano ancora
    assert client.get("/api/jobs/cleanup").status_code == 404  # dettaglio di un job "cleanup", che non esiste


@pytest.fixture
def server_client(tmp_path, monkeypatch):
    monkeypatch.delenv("BIOLOOM_ALLOW_SHUTDOWN", raising=False)
    app = create_app(Paths(tmp_path / "data"), frontend_dist=None,
                     auth_config=auth.AuthConfig(enabled=True, key="segreto-12345"))
    with TestClient(app) as c:
        yield c


def test_cleanup_and_usage_require_access_key(server_client):
    c = server_client
    key = {"Authorization": "Bearer segreto-12345"}
    request = {"scope": "all", "statuses": ["failed"]}
    pid = c.get("/api/projects", headers=key).json()[0]["id"]
    assert c.post("/api/jobs/cleanup", json=request).status_code == 401
    assert c.post("/api/jobs/cleanup/preview", json=request).status_code == 401
    assert c.get(f"/api/projects/{pid}/usage").status_code == 401
    assert c.delete(f"/api/projects/{pid}").status_code == 401
    assert c.post("/api/jobs/cleanup/preview", json=request, headers=key).status_code == 200
    assert c.get(f"/api/projects/{pid}/usage", headers=key).status_code == 200
