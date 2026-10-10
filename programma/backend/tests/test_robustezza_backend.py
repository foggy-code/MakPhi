"""Robustezza del backend: log dell'avviso per email dopo l'eliminazione del job, XML di blastp locale letto da file,
tetto della cache dei calcoli (pulizia delle voci usate meno di recente), errori interni con codice, indirizzo della
pagina dei risultati di SWISS-MODEL solo https."""

from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from bioloom.app import create_app
from bioloom.config import Paths, SettingsStore, UserSettings
from bioloom.core import cache
from bioloom.core.db import Database
from bioloom.core.jobs import JobManager, ProcessResult
from bioloom.engines import remote, search
from bioloom.services.housekeeping import Housekeeping

from .conftest import until, wait_job


# ------------------------------------------------------------------------------------------ avviso dopo l'eliminazione
@pytest.fixture
def manager(tmp_path):
    db = Database(tmp_path / "data" / "t.db")
    mgr = JobManager(db, tmp_path / "data" / "jobs", workers=2, settings_provider=UserSettings,
                     scratch_dir=tmp_path / "scratch")
    mgr.register("vuoto", lambda ctx, params: {})
    yield mgr
    mgr.shutdown()
    db.close()


def _finished(mgr, job_id, timeout=20):
    start = time.time()
    while time.time() - start < timeout:
        if mgr.get(job_id)["status"] == "completed" and not mgr.busy(job_id):
            return
        time.sleep(0.05)
    raise AssertionError("job non terminato")


def test_log_if_present_never_recreates_a_deleted_job(manager):
    job_id = manager.submit("vuoto", {})["id"]
    _finished(manager, job_id)
    assert manager.log_if_present(job_id, "Email di avviso inviata a utente@example.org")
    assert "Email di avviso inviata" in manager.read_log(job_id)["text"]
    manager.delete(job_id)
    folder = manager.jobs_dir / job_id
    assert not folder.exists()
    assert manager.log_if_present(job_id, "Email di avviso inviata a utente@example.org") is False
    assert not folder.exists()
    # anche mentre i file si stanno ancora eliminando (cartella `.deleting-<id>` in rimozione)
    other = manager.submit("vuoto", {})["id"]
    _finished(manager, other)
    manager._removing.add(f".deleting-{other}")
    try:
        assert manager.log_if_present(other, "riga") is False
    finally:
        manager._removing.discard(f".deleting-{other}")


def test_notice_sent_while_the_job_is_deleted_leaves_no_folder(client, monkeypatch):
    core = client.app.state.core
    core.jobs.register("vuoto", lambda ctx, params: {})
    job_id = core.jobs.submit("vuoto", {"notify_email": "utente@example.org"})["id"]
    job = wait_job(client, job_id)
    assert job["status"] == "completed"
    until(lambda: not core.jobs.busy(job_id))
    deleted = []

    def send_and_delete(to, subject, body):  # l'utente elimina il job mentre l'email parte
        core.jobs.delete(job_id)
        deleted.append(to)

    monkeypatch.setattr(core.notify, "send", send_and_delete)
    core.notify._notify(job_id, "completed", "utente@example.org")
    assert deleted == ["utente@example.org"]
    assert not (core.paths.jobs / job_id).exists()


# ------------------------------------------------------------------------------------------ blastp locale
def _blast_xml(n_hits: int) -> str:
    """XML di blastp con `n_hits` risultati e righe come quelle vere (un elemento per riga)."""
    head = """<?xml version="1.0"?>
<!DOCTYPE BlastOutput PUBLIC "-//NCBI//NCBI BlastOutput/EN" "http://www.ncbi.nlm.nih.gov/dtd/NCBI_BlastOutput.dtd">
<BlastOutput>
  <BlastOutput_program>blastp</BlastOutput_program>
  <BlastOutput_version>BLASTP 2.15.0+</BlastOutput_version>
  <BlastOutput_reference>ref</BlastOutput_reference>
  <BlastOutput_db>pdbaa</BlastOutput_db>
  <BlastOutput_query-ID>Query_1</BlastOutput_query-ID>
  <BlastOutput_query-def>query</BlastOutput_query-def>
  <BlastOutput_query-len>20</BlastOutput_query-len>
  <BlastOutput_param>
    <Parameters>
      <Parameters_matrix>BLOSUM62</Parameters_matrix>
      <Parameters_expect>10</Parameters_expect>
      <Parameters_gap-open>11</Parameters_gap-open>
      <Parameters_gap-extend>1</Parameters_gap-extend>
      <Parameters_filter>F</Parameters_filter>
    </Parameters>
  </BlastOutput_param>
  <BlastOutput_iterations>
    <Iteration>
      <Iteration_iter-num>1</Iteration_iter-num>
      <Iteration_query-ID>Query_1</Iteration_query-ID>
      <Iteration_query-def>query</Iteration_query-def>
      <Iteration_query-len>20</Iteration_query-len>
      <Iteration_hits>
"""
    hit = """        <Hit>
          <Hit_num>{n}</Hit_num>
          <Hit_id>pdb|{pid}|A</Hit_id>
          <Hit_def>Chain A, Proteina {n}</Hit_def>
          <Hit_accession>{pid}_A</Hit_accession>
          <Hit_len>76</Hit_len>
          <Hit_hsps>
            <Hsp>
              <Hsp_num>1</Hsp_num>
              <Hsp_bit-score>{bits}</Hsp_bit-score>
              <Hsp_score>85</Hsp_score>
              <Hsp_evalue>{evalue}</Hsp_evalue>
              <Hsp_query-from>1</Hsp_query-from>
              <Hsp_query-to>10</Hsp_query-to>
              <Hsp_hit-from>1</Hsp_hit-from>
              <Hsp_hit-to>10</Hsp_hit-to>
              <Hsp_query-frame>0</Hsp_query-frame>
              <Hsp_hit-frame>0</Hsp_hit-frame>
              <Hsp_identity>9</Hsp_identity>
              <Hsp_positive>10</Hsp_positive>
              <Hsp_gaps>0</Hsp_gaps>
              <Hsp_align-len>10</Hsp_align-len>
              <Hsp_qseq>MQIFVKTLTG</Hsp_qseq>
              <Hsp_hseq>MQIFVKTLSG</Hsp_hseq>
              <Hsp_midline>MQIFVKTL G</Hsp_midline>
            </Hsp>
          </Hit_hsps>
        </Hit>
"""
    tail = """      </Iteration_hits>
      <Iteration_stat>
        <Statistics>
          <Statistics_db-num>2</Statistics_db-num>
          <Statistics_db-len>152</Statistics_db-len>
          <Statistics_hsp-len>0</Statistics_hsp-len>
          <Statistics_eff-space>0</Statistics_eff-space>
          <Statistics_kappa>0.041</Statistics_kappa>
          <Statistics_lambda>0.267</Statistics_lambda>
          <Statistics_entropy>0.14</Statistics_entropy>
        </Statistics>
      </Iteration_stat>
    </Iteration>
  </BlastOutput_iterations>
</BlastOutput>
"""
    body = "".join(hit.format(n=i + 1, pid=f"{i % 9 + 1}{i:03d}", bits=500 - i * 0.1,
                              evalue=f"{1e-30 * (i + 1):.3g}") for i in range(n_hits))
    return head + body + tail


class _Ctx:
    """Contesto minimo per `search._local_blast`: cartella temporanea e un blastp simulato."""

    def __init__(self, folder: Path, xml: str) -> None:
        self.tmp_dir = folder / ".tmp"
        self.tmp_dir.mkdir(parents=True)
        self.xml = xml
        self.cmds: list[list[str]] = []
        self.output = ""

    def run(self, cmd, **kwargs):
        self.cmds.append(cmd)
        out = Path(cmd[cmd.index("-out") + 1])
        out.write_text(self.xml, encoding="utf-8")
        # quello che il JobContext vero conserverebbe dell'output del processo: le ultime 20000 righe
        lines = self.xml.splitlines(keepends=True)
        self.output = "".join(lines[-20000:])
        return ProcessResult(0, "")


def test_local_blast_reads_the_xml_from_a_file_beyond_20000_lines(tmp_path):
    xml = _blast_xml(800)
    assert len(xml.splitlines()) > 20000 + 1000
    ctx = _Ctx(tmp_path, xml)
    hits = search._local_blast(ctx, "blastp", tmp_path / "query.fasta", tmp_path / "pdbaa", evalue=10.0,
                               max_hits=800, query_len=20)
    assert len(hits) == 800 and [h["pdb_id"] for h in hits[:3]] == ["1000", "2001", "3002"]
    cmd = ctx.cmds[0]
    assert cmd[cmd.index("-outfmt") + 1] == "5" and Path(cmd[cmd.index("-out") + 1]).parent == ctx.tmp_dir
    assert not list(ctx.tmp_dir.iterdir())  # il file dei risultati si elimina appena letto
    # dall'output troncato (come prima) l'XML non si leggeva: mancano l'intestazione e i primi risultati
    assert "<?xml" not in ctx.output and "<BlastOutput>" not in ctx.output
    assert "<Hit_num>1</Hit_num>" not in ctx.output and "<Hit_num>800</Hit_num>" in ctx.output
    # stessi risultati di un XML piccolo letto per intero
    small = search._local_blast(_Ctx(tmp_path / "b", _blast_xml(3)), "blastp", tmp_path / "q", tmp_path / "d",
                                evalue=10.0, max_hits=3, query_len=20)
    assert small == search._hits_from_blast_xml(_blast_xml(3), 20, "local")


def test_local_blast_errors(tmp_path):
    class Failing(_Ctx):
        def run(self, cmd, **kwargs):
            return ProcessResult(2, "BLAST Database error: No alias or index file found")

    with pytest.raises(Exception, match="blastp è terminato con errore: BLAST Database error"):
        search._local_blast(Failing(tmp_path, ""), "blastp", tmp_path / "q", tmp_path / "d", evalue=1.0,
                            max_hits=5, query_len=20)

    class Silent(_Ctx):
        def run(self, cmd, **kwargs):
            return ProcessResult(0, "Warning: niente")

    with pytest.raises(Exception, match="blastp non ha scritto il file dei risultati"):
        search._local_blast(Silent(tmp_path / "s", ""), "blastp", tmp_path / "q", tmp_path / "d", evalue=1.0,
                            max_hits=5, query_len=20)


# ------------------------------------------------------------------------------------------ tetto della cache
def _entry(folder: Path, name: str, size: int, used: float) -> Path:
    path = folder / "search" / f"{name}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("x" * size, encoding="utf-8")
    os.utime(path, (used, used))
    return path


def test_prune_removes_the_least_recently_used_entries(tmp_path):
    root = tmp_path / "compute"
    cache.configure(root)
    now = time.time()
    old = _entry(root, "a" * 64, 400, now - 5000)
    mid = _entry(root, "b" * 64, 400, now - 3000)
    new = _entry(root, "c" * 64, 400, now - 1000)
    tmp = root / "search" / ".x.json.123.tmp"
    tmp.write_text("y" * 50)
    assert cache.prune(2000) == (0, 0) and cache.usage() == 1250  # sotto il tetto: niente
    removed, freed = cache.prune(1000)  # oltre: scende al 90% (900 byte) togliendo le meno usate
    assert (removed, freed) == (1, 400) and not old.exists() and mid.exists() and new.exists()
    assert tmp.exists() and cache.usage() == 850  # i temporanei contano ma non si toccano
    assert cache.prune(400) == (2, 800) and not mid.exists() and not new.exists() and cache.usage() == 50


def test_lookup_marks_an_entry_as_used(tmp_path, monkeypatch):
    root = tmp_path / "compute"
    cache.configure(root)
    key_old, key_new = "1" * 64, "2" * 64
    cache.store("search", key_old, {"hits": [1]})
    cache.store("search", key_new, {"hits": [2]})
    now = time.time()
    for key, when in ((key_old, now - 9000), (key_new, now - 100)):
        path = root / "search" / f"{key}.json"
        os.utime(path, (when, when))
    path_old = root / "search" / f"{key_old}.json"
    mtime = path_old.stat().st_mtime
    # l'orologio di cache._touch un'ora avanti: la data di ultimo accesso deve essere quella scritta dal codice, non
    # quella che il sistema operativo aggiorna da sé leggendo il file (Windows, Linux con relatime), che sarebbe
    # l'ora vera, un'ora prima
    future_ns = (int(time.time()) + 3600) * 10 ** 9
    monkeypatch.setattr(cache, "time", SimpleNamespace(time=time.time, time_ns=lambda: future_ns))
    assert cache.lookup("search", key_old) is not None  # letta adesso: diventa la più recente
    assert abs(path_old.stat().st_atime - future_ns / 1e9) < 1
    assert path_old.stat().st_mtime == mtime  # la data di modifica (età per la pulizia automatica) non cambia
    size = path_old.stat().st_size
    removed, _freed = cache.prune(int(size * 1.5))  # c'è posto per una voce sola
    assert removed == 1 and path_old.exists() and not (root / "search" / f"{key_new}.json").exists()


def test_store_keeps_the_usage_estimate_and_enforce_prunes_only_when_needed(tmp_path, monkeypatch):
    root = tmp_path / "compute"
    cache.configure(root)
    assert cache.usage() is None
    cache.enforce(10 ** 9)  # prima misura
    assert cache.usage() == 0
    cache.store("search", "3" * 64, {"hits": list(range(100))})
    first = root / "search" / f"{'3' * 64}.json"
    size = first.stat().st_size
    assert cache.usage() == size
    scans = []
    real = cache.prune
    monkeypatch.setattr(cache, "prune", lambda limit, folder=None: scans.append(limit) or real(limit, folder))
    assert cache.enforce(size + 1) == (0, 0) and scans == []  # sotto il tetto: nessuna scansione
    # usata un minuto fa: l'ordine d'uso non dipende dalla risoluzione dell'orologio del filesystem
    old = time.time() - 60
    os.utime(first, (old, old))
    cache.store("search", "4" * 64, {"hits": list(range(100))})
    assert cache.enforce(int(size * 1.5)) == (1, size) and len(scans) == 1  # via la meno recente
    assert not (root / "search" / f"{'3' * 64}.json").exists() and (root / "search" / f"{'4' * 64}.json").exists()


def test_run_cached_applies_the_limit_after_writing(tmp_path, monkeypatch):
    root = tmp_path / "compute"
    cache.configure(root)
    monkeypatch.setattr(cache, "MB", 1)  # tetto in byte invece che in MB
    logs = []
    work = tmp_path / "work"
    work.mkdir()
    ctx = SimpleNamespace(workdir=work, settings=SimpleNamespace(compute_cache=True, compute_cache_max_mb=1500),
                          cancelled=False, log=logs.append)
    # ordine d'uso esplicito (la voce i usata all'istante base + i, la nuova sempre adesso): scritture vicine possono
    # avere la stessa data con la risoluzione dell'orologio del filesystem
    base = time.time() - 1000
    for i in range(6):
        before = set((root / "search").glob("*.json"))
        cache.run_cached(ctx, "search", {"i": i}, lambda i=i: {"hits": ["h" * 300], "i": i})
        for path in set((root / "search").glob("*.json")) - before:
            os.utime(path, (base + i, base + i))
    entries = list((root / "search").glob("*.json"))
    assert 0 < sum(p.stat().st_size for p in entries) <= 1500
    assert any("oltre il tetto" in line for line in logs)
    # l'ultima scritta resta
    assert cache.run_cached(ctx, "search", {"i": 5}, lambda: {"hits": []}).get("cached") is True


def test_housekeeping_applies_the_compute_cache_limit(tmp_path, monkeypatch):
    paths = Paths(tmp_path / "data", scratch_dir=tmp_path / "scratch")
    paths.ensure()
    db = Database(paths.db)
    mgr = JobManager(db, paths.jobs, 1, UserSettings, scratch_dir=paths.scratch)
    try:
        now = time.time()
        old = _entry(paths.compute_cache, "a" * 64, 600, now - 3600)
        new = _entry(paths.compute_cache, "b" * 64, 600, now - 60)
        monkeypatch.setattr(cache, "MB", 1)
        settings = SimpleNamespace(uploads_retention_days=30, compute_cache_max_mb=1000)
        report = Housekeeping(paths, lambda: settings, mgr).run()
        assert report["cache"] == 1 and report["freed_bytes"] >= 600 and not old.exists() and new.exists()
    finally:
        mgr.shutdown()
        db.close()


def test_compute_cache_limit_setting(client, tmp_path):
    current = client.get("/api/settings").json()
    assert current["compute_cache_max_mb"] == 2048
    low = client.put("/api/settings", json={"compute_cache_max_mb": 50})
    assert low.status_code == 400
    assert low.json()["error"]["message"] == ("Impostazioni non valide: Spazio massimo della cache dei calcoli (MB): "
                                              "deve essere almeno 100.")
    high = client.put("/api/settings", json={"compute_cache_max_mb": 200000})
    assert high.status_code == 400 and "al massimo 102400" in high.json()["error"]["message"]
    ok = client.put("/api/settings", json={"compute_cache_max_mb": 500})
    assert ok.status_code == 200 and ok.json()["compute_cache_max_mb"] == 500
    # un valore scritto a mano non valido torna al predefinito senza azzerare le altre impostazioni
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"compute_cache_max_mb": "tanto", "max_workers": 3}), encoding="utf-8")
    store = SettingsStore(path)
    assert store.get().compute_cache_max_mb == 2048 and store.get().max_workers == 3
    path.write_text(json.dumps({"compute_cache_max_mb": 10, "max_workers": 3}), encoding="utf-8")
    assert SettingsStore(path).get().max_workers == 3
    assert cache.max_bytes(None) == 2048 * 1024 * 1024 and cache.max_bytes(UserSettings()) == 2048 * 1024 * 1024


# ------------------------------------------------------------------------------------------ errori interni
def test_internal_errors_hide_details_and_log_them_with_a_code(tmp_path, capsys):
    app = create_app(Paths(tmp_path / "data"), frontend_dist=None)

    @app.post("/api/prova-errore")
    def boom():
        raise RuntimeError("dettaglio riservato /srv/bioloom/data/jobs/abc")

    with TestClient(app, raise_server_exceptions=False) as c:
        r = c.post("/api/prova-errore")
    assert r.status_code == 500
    error = r.json()["error"]
    assert error["code"] == "internal_error"
    match = re.fullmatch(r"Errore interno \(codice ([0-9a-f]{8})\): i dettagli sono nel log di BioLoom\.",
                         error["message"])
    assert match and "riservato" not in r.text
    out = capsys.readouterr().out
    assert f"Errore interno (codice {match.group(1)}) su POST /api/prova-errore" in out
    assert "RuntimeError: dettaglio riservato" in out and "Traceback" in out


# ------------------------------------------------------------------------------------------ SWISS-MODEL
@pytest.mark.parametrize("page, expected", [
    ("https://swissmodel.expasy.org/qmean/abc123/", "https://swissmodel.expasy.org/qmean/abc123/"),
    ("http://swissmodel.expasy.org/qmean/abc123/", None),
    ("javascript:alert(1)", None),
    ("data:text/html,<script>alert(1)</script>", None),
    ("https://", None),
    ("https://utente:pw@swissmodel.expasy.org/x", None),
    ("https://swissmodel.expasy.org/a b", None),
    (None, None),
    (42, None),
])
def test_swissmodel_results_page_must_be_https(monkeypatch, page, expected):
    responses = iter([{"results_json": "https://swissmodel.expasy.org/qmean/abc123.json"},
                      {"status": "COMPLETED", "results_page": page, "models": {}}])
    monkeypatch.setattr(remote, "_request", lambda *a, **k: SimpleNamespace(json=lambda: next(responses)))
    result = remote.swissmodel_qmean("ATOM\n", "utente@example.org", sleep=lambda _s: None, log=lambda _m: None)
    assert result["url"] == expected and result["source"] == "swissmodel"
