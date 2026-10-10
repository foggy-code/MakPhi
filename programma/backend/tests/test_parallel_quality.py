"""Profilazione e validazione in parallelo (F1.2/F1.3).

- `core/parallel.Steps`: ordine dei risultati, modalità seriale, errori isolati, annullamento;
- `quality.analyze` in parallelo = in serie (stesso rapporto) su 1UBQ, sulle strutture sintetiche e su un modello di
  MODELLER; passi falliti con gli stessi avvisi di prima; annullamento che ferma passi e processi; mkdssp da ctx.run;
- qualità "all" con i modelli analizzati in parallelo = in serie;
- template preparati riusati fra gli elementi di una pipeline, con la chiave che cambia con template, catene, ligandi e
  ritaglio pLDDT.

I processi lenti sono veri (`python -c "sleep"` lanciati da ctx.run), così l'annullamento li deve terminare davvero.
"""

from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from bioloom.config import UserSettings
from bioloom.core import jobs as jobs_module
from bioloom.core.db import Database
from bioloom.core.errors import AppError, ToolUnavailable
from bioloom.core.jobs import JobCancelled, JobManager
from bioloom.core.parallel import Steps
from bioloom.domain import structure
from bioloom.engines import modeller, procheck, prosa, quality, tools
from bioloom.services import compute as compute_module
from bioloom.services.compute import PreparedTemplates

from .conftest import DATA, YEAST_UB, blocked_in, requires_modeller, until, wait_job
from .test_alphafold import af_pdb

SLEEP = [sys.executable, "-c", "import time; time.sleep(60)"]
ORIGINAL_PREPARE = modeller.prepare_template


# ------------------------------------------------------------------------------------------ gestore dei job
@pytest.fixture
def manager(tmp_path):
    db = Database(tmp_path / "q.db")
    mgr = JobManager(db, tmp_path / "jobs", 2, UserSettings, scratch_dir=tmp_path / "scratch")
    yield mgr
    mgr.shutdown()
    db.close()


def run_job(mgr: JobManager, fn, timeout: float = 300.0) -> dict:
    """Esegue `fn(ctx)` come job e ne restituisce la riga finale (con il risultato)."""
    kind = f"probe{abs(hash(fn)) % 10**8}"
    mgr.register(kind, lambda ctx, _p: fn(ctx))
    job_id = mgr.submit(kind, {})["id"]
    until(lambda: mgr.get(job_id)["status"] in ("completed", "failed", "cancelled") and not mgr.busy(job_id),
          timeout)
    return mgr.get(job_id)


def log_text(mgr: JobManager, job_id: str) -> str:
    return mgr.read_log(job_id, 0, 10**7)["text"]


# ------------------------------------------------------------------------------------------ core/parallel
def test_steps_results_follow_the_requested_order_not_the_completion_order():
    gates = {k: threading.Event() for k in "abc"}
    finished: list[str] = []

    def step(key):
        def run():
            assert gates[key].wait(5)
            finished.append(key)
            return key.upper()
        return run

    with Steps(workers=3) as steps:
        for key in "abc":
            steps.start(key, step(key))
        for key in "cba":  # finiscono al contrario dell'ordine di lettura
            gates[key].set()
            until(lambda k=key: k in finished)
        assert [steps.result(k) for k in "abc"] == ["A", "B", "C"]
        assert finished == ["c", "b", "a"]
        assert set(steps.timings) == {"a", "b", "c"}


def test_serial_steps_run_lazily_in_the_order_of_the_results():
    order: list[str] = []
    steps = Steps(parallel=False)
    for key in "xyz":
        steps.start(key, lambda k=key: order.append(k) or k)
    assert order == []  # nulla parte prima della richiesta
    assert steps.result("z") == "z" and steps.result("x") == "x"
    assert order == ["z", "x"]
    steps.close()  # "y" non è mai stato chiesto: non gira
    assert order == ["z", "x"] and not steps.parallel
    duplicated = Steps(parallel=False)
    duplicated.start("a", lambda: 1)
    with pytest.raises(ValueError):
        duplicated.start("a", lambda: 2)


def test_a_failing_step_does_not_stop_the_others():
    with Steps(workers=3) as steps:
        steps.start("rotto", lambda: (_ for _ in ()).throw(AppError("guasto")))
        steps.start("lento", lambda: time.sleep(0.3) or "ok")
        steps.start("veloce", lambda: 42)
        out = steps.outcome("rotto")
        assert isinstance(out.error, AppError) and out.value is None
        with pytest.raises(AppError, match="guasto"):
            steps.result("rotto")
        assert steps.result("lento") == "ok" and steps.result("veloce") == 42


def test_cancellation_while_waiting_stops_pending_steps_and_waits_for_running_ones():
    cancelled = threading.Event()
    started: list[str] = []
    release = threading.Event()

    def check():
        if cancelled.is_set():
            raise JobCancelled()

    def running():
        started.append("in corso")
        release.wait(5)
        return "fine"

    steps = Steps(check=check, workers=2)
    steps.start("primo", running)
    steps.start("secondo", running)
    steps.start("in coda", lambda: started.append("in coda"))  # nessun thread libero: aspetta
    until(lambda: started == ["in corso", "in corso"])
    cancelled.set()
    t0 = time.perf_counter()
    with pytest.raises(JobCancelled):
        steps.result("primo")
    assert time.perf_counter() - t0 < 1.0  # l'attesa controlla l'annullamento
    closer = threading.Thread(target=steps.close)
    closer.start()
    until(lambda: blocked_in(closer, "shutdown"))  # close aspetta i passi in corso (join dei thread del pool)
    assert closer.is_alive()
    release.set()
    closer.join(5)
    assert not closer.is_alive() and started == ["in corso", "in corso"]  # il passo in coda non è mai partito


def test_job_cancelled_inside_a_step_is_raised_even_by_outcome():
    with Steps(workers=2) as steps:
        steps.start("annullato", lambda: (_ for _ in ()).throw(JobCancelled()))
        with pytest.raises(JobCancelled):
            steps.outcome("annullato")


# ------------------------------------------------------------------------------------------ analisi: equivalenza
@pytest.mark.parametrize("name", ["1UBQ.pdb", "ubq_dimer.pdb", "ubq_zn.pdb"])
def test_parallel_analysis_matches_serial_without_a_job(name):
    """Senza job (nessun DOPE, PROCHECK, ProSA): Ramachandran, clash, SASA e DSSP (mkdssp o integrato)."""
    path = DATA / name
    par = quality.analyze(path, None, with_dope=False)
    ser = quality.analyze(path, None, with_dope=False, parallel=False)
    assert par == ser
    if name == "1UBQ.pdb":  # valori di riferimento (docs/ARCHITETTURA.md)
        assert par["ramachandran"]["summary"]["favored_pct"] == 95.9 and par["clashes"]["clashscore"] == 1.66
        assert par["grade"]["grade"] == "A" and len(par["residues"]) == 76


def test_parallel_analysis_matches_serial_in_a_job_with_all_tools(manager, ubq_path):
    """Con un job: DOPE, PROCHECK e ProSA quando ci sono, altrimenti gli stessi avvisi nei due modi."""

    def both(ctx):
        kw = dict(with_dope=True, with_procheck=True, with_prosa=True)
        par = quality.analyze(ubq_path, ctx, dope_workdir=ctx.workdir / "par", **kw)
        ser = quality.analyze(ubq_path, ctx, dope_workdir=ctx.workdir / "ser", parallel=False, **kw)
        return {"par": par, "ser": ser}

    job = run_job(manager, both)
    assert job["status"] == "completed", job["error"]
    par, ser = job["result"]["par"], job["result"]["ser"]
    assert par == ser
    if par["dope"] is not None:  # MODELLER presente: zDOPE di 1UBQ come in test_consensus
        assert par["dope"]["zscore"] == pytest.approx(-2.7335, abs=1e-3)
        assert len(par["dope"]["profile"]) == 76
    if par["procheck"] is not None:  # PROCHECK reale: stesso riepilogo di tests/data/procheck_1ubq.sum
        assert par["procheck"]["ramachandran"]["core"] == 95.5
    log = log_text(manager, job["id"])
    assert "Analisi di qualità in" in log and "passi in parallelo:" in log and "passi in serie:" in log


@pytest.mark.modeller
@requires_modeller
def test_parallel_analysis_matches_serial_on_a_modeller_model(client_with_ubq):
    c = client_with_ubq
    job = c.post("/api/model", json={"target": {"name": "yeast_ub", "sequence": YEAST_UB},
                                     "templates": [{"id": "1UBQ", "chains": ["A"]}],
                                     "options": {"models": 1, "refinement": "very_fast"}, "quality": "none"}).json()
    done = wait_job(c, job["id"], timeout=600)
    assert done["status"] == "completed", done.get("error")
    core = c.app.state.core
    model = core.jobs.file_path(job["id"], done["result"]["best_model"])

    def both(ctx):
        kw = dict(with_dope=True, with_procheck=True, with_prosa=True)
        return {"par": quality.analyze(model, ctx, dope_workdir=ctx.workdir / "par", **kw),
                "ser": quality.analyze(model, ctx, dope_workdir=ctx.workdir / "ser", parallel=False, **kw)}

    core.jobs.register("qa_both", lambda ctx, _p: both(ctx))
    probe = wait_job(c, core.jobs.submit("qa_both", {})["id"], timeout=600)
    assert probe["status"] == "completed", probe.get("error")
    par, ser = probe["result"]["par"], probe["result"]["ser"]
    assert par == ser
    assert par["dope"]["score"] < -6000 and par["structure"]["residues"] == 76


# ------------------------------------------------------------------------------------------ analisi: errori
def test_failing_steps_give_the_same_warnings_in_the_same_order(manager, ubq_path, monkeypatch):
    """Avvisi identici alla versione seriale (stesso testo, stesso ordine) e gli altri passi completi."""

    def dope_fails(ctx, pdb, workdir):
        raise AppError("MODELLER è terminato con errore (codice 1).\ndettaglio")

    def procheck_fails(pdb, ctx, workdir):
        raise AppError("PROCHECK non ha prodotto il riepilogo (codice 2).\ncoda")

    def prosa_missing(pdb, ctx, workdir):
        raise ToolUnavailable("ProSA II non è installato in questa immagine.")

    def sasa_fails(model):
        raise ValueError("raggi mancanti")

    monkeypatch.setattr(modeller, "evaluate_dope", dope_fails)
    monkeypatch.setattr(procheck, "run", procheck_fails)
    monkeypatch.setattr(prosa, "run", prosa_missing)
    monkeypatch.setattr(quality, "_sasa", sasa_fails)
    expected = ["SASA non calcolata: raggi mancanti",
                "DOPE non calcolato: MODELLER è terminato con errore (codice 1).",
                "PROCHECK non riuscito: PROCHECK non ha prodotto il riepilogo (codice 2).",
                "ProSA II non disponibile: ProSA II non è installato in questa immagine."]

    def both(ctx):
        kw = dict(with_dope=True, with_procheck=True, with_prosa=True)
        return {"par": quality.analyze(ubq_path, ctx, **kw), "ser": quality.analyze(ubq_path, ctx, parallel=False, **kw)}

    job = run_job(manager, both)
    assert job["status"] == "completed", job["error"]
    for report in job["result"].values():
        assert report["warnings"] == expected
        assert report["dope"] is None and report["procheck"] is None and report["prosa"] is None
        assert report["ramachandran"]["summary"]["favored_pct"] == 95.9 and len(report["residues"]) == 76
    assert job["result"]["par"] == job["result"]["ser"]


def test_a_slow_step_is_not_blocked_by_a_failing_one(manager, ubq_path, monkeypatch):
    """PROCHECK fallisce subito, il DOPE (processo di 1 s) finisce dopo e il suo risultato c'è."""
    fake_dope = {"dope": -7000.0, "zdope": -2.0, "profile": [{"index": i + 1, "resname": "X", "energy": -0.01}
                                                            for i in range(76)]}

    def slow_dope(ctx, pdb, workdir):
        ctx.run([sys.executable, "-c", "import time; time.sleep(1)"], log_output=False)
        return fake_dope

    monkeypatch.setattr(modeller, "evaluate_dope", slow_dope)
    monkeypatch.setattr(procheck, "run", lambda pdb, ctx, wd: (_ for _ in ()).throw(AppError("rotto")))
    job = run_job(manager, lambda ctx: quality.analyze(ubq_path, ctx, with_procheck=True))
    assert job["status"] == "completed", job["error"]
    report = job["result"]
    assert report["dope"]["score"] == -7000.0 and report["residues"][0]["dope"] == -0.01
    assert report["warnings"] == ["PROCHECK non riuscito: rotto"]


def test_an_unexpected_step_error_fails_the_analysis_like_before(manager, ubq_path, monkeypatch):
    monkeypatch.setattr(procheck, "run", lambda pdb, ctx, wd: (_ for _ in ()).throw(KeyError("imprevisto")))
    job = run_job(manager, lambda ctx: quality.analyze(ubq_path, ctx, with_dope=False, with_procheck=True))
    assert job["status"] == "failed" and "KeyError" in job["error"]


# ------------------------------------------------------------------------------------------ analisi: annullamento
def test_cancelling_a_parallel_analysis_stops_every_step_and_process(manager, ubq_path, monkeypatch):
    """DOPE, PROCHECK e ProSA sono processi di 60 s: l'annullamento li termina tutti e l'analisi finisce subito,
    senza passi ancora in esecuzione né processi vivi."""
    procs: list[subprocess.Popen] = []
    original = JobManager._register_process

    def register(self, job_id, proc):
        procs.append(proc)
        original(self, job_id, proc)

    monkeypatch.setattr(JobManager, "_register_process", register)

    def slow(ctx):
        ctx.run(SLEEP, log_output=False)
        raise AssertionError("il processo doveva essere terminato")

    monkeypatch.setattr(modeller, "evaluate_dope", lambda ctx, pdb, wd: slow(ctx))
    monkeypatch.setattr(procheck, "run", lambda pdb, ctx, wd: slow(ctx))
    monkeypatch.setattr(prosa, "run", lambda pdb, ctx, wd: slow(ctx))
    kind = "qa_cancel"
    manager.register(kind, lambda ctx, _p: quality.analyze(ubq_path, ctx, with_procheck=True, with_prosa=True))
    job_id = manager.submit(kind, {})["id"]
    # i processi lenti partono insieme fino agli slot del budget di CPU; gli altri passi aspettano uno slot
    started = min(3, manager.budget.capacity)
    until(lambda: sum(1 for p in procs if p.args == SLEEP) == started)
    t0 = time.perf_counter()
    manager.cancel(job_id)
    until(lambda: manager.get(job_id)["status"] == "cancelled" and not manager.busy(job_id), 15)
    assert time.perf_counter() - t0 < 10
    assert all(p.poll() is not None for p in procs)  # nessun processo sopravvive
    assert sum(1 for p in procs if p.args == SLEEP) == started  # chi aspettava lo slot non parte più
    assert not [t for t in threading.enumerate() if t.name.startswith("bioloom-qualita")]


# ------------------------------------------------------------------------------------------ mkdssp da ctx.run
@pytest.mark.skipif(not tools.find("dssp"), reason="mkdssp non installato")
def test_mkdssp_runs_through_the_job_context_with_the_same_assignment(manager, ubq_path, monkeypatch):
    import importlib

    from Bio.PDB import DSSP

    dssp_module = importlib.import_module("Bio.PDB.DSSP")

    st = structure.load_structure(ubq_path, "1UBQ")
    model = structure.first_model(st)
    # riferimento: Biopython lancia mkdssp da sé, come prima della F1.3
    folder = Path(manager.jobs_dir) / "ref"
    folder.mkdir()
    ref_input = quality._dssp_input(st, folder)
    ref = DSSP(structure.first_model(structure.load_structure(ubq_path, "1UBQ")), str(ref_input),
               dssp=tools.find("dssp"))
    ref_rows = [(k[0], k[1][1], ref[k][2], ref[k][3]) for k in ref.keys()]

    commands: list[list[str]] = []
    original_run = jobs_module.JobContext.run

    def recording_run(self, cmd, **kwargs):
        commands.append([str(c) for c in cmd])
        return original_run(self, cmd, **kwargs)

    def forbidden(*_a, **_k):
        raise AssertionError("mkdssp deve passare da ctx.run")

    monkeypatch.setattr(jobs_module.JobContext, "run", recording_run)
    monkeypatch.setattr(dssp_module, "subprocess", SimpleNamespace(Popen=forbidden, check_output=forbidden))
    quality._dssp_versions.clear()
    job = run_job(manager, lambda ctx: quality._dssp_external(model, st, ctx))
    assert job["status"] == "completed", job["error"]
    rows = job["result"]
    assert [(r["chain"], int(r["resnum"]), r["ss"], r["rsa"]) for r in rows] == \
        [(c, n, ss, round(float(rsa), 3)) for c, n, ss, rsa in ref_rows]
    assert [c[1:] for c in commands] == [["--version"], ["--output-format=dssp", "dssp_input.pdb",
                                                         "dssp_input.dssp"]]
    # versione memorizzata: una seconda lettura lancia solo mkdssp
    commands.clear()
    job = run_job(manager, lambda ctx: quality.analyze(ubq_path, ctx, with_dope=False))
    assert job["result"]["secondary_structure"]["method"] == "mkdssp"
    assert [c[1] for c in commands] == ["--output-format=dssp"]
    assert "[mkdssp]" not in log_text(manager, job["id"])  # nessuna uscita di mkdssp nel log


def test_step_logs_carry_the_step_prefix(manager, ubq_path, monkeypatch):
    def noisy(name):
        def run(pdb, ctx, workdir):
            ctx.log(f"messaggio di {name}")
            raise AppError("fine")
        return run

    monkeypatch.setattr(procheck, "run", noisy("PROCHECK"))
    monkeypatch.setattr(prosa, "run", noisy("ProSA"))
    job = run_job(manager, lambda ctx: quality.analyze(ubq_path, ctx, with_dope=False, with_procheck=True,
                                                       with_prosa=True))
    log = log_text(manager, job["id"])
    assert "[PROCHECK] messaggio di PROCHECK" in log and "[ProSA II] messaggio di ProSA" in log


# ------------------------------------------------------------------------------------------ qualità "all"
@pytest.mark.modeller
@requires_modeller
def test_quality_all_in_parallel_matches_serial(client_with_ubq, monkeypatch):
    c = client_with_ubq
    body = {"target": {"name": "yeast_ub", "sequence": YEAST_UB}, "templates": [{"id": "1UBQ", "chains": ["A"]}],
            "options": {"models": 3, "refinement": "very_fast"}, "quality": "all", "extended_quality": True}

    def run(parallel: int) -> tuple[dict, dict, str]:
        monkeypatch.setattr(compute_module, "QUALITY_MODELS_PARALLEL", parallel)
        job = c.post("/api/model", json=body).json()
        done = wait_job(c, job["id"], timeout=900)
        assert done["status"] == "completed", done.get("error")
        reports = {m["file"]: c.get(f"/api/jobs/{job['id']}/quality/{m['file']}").json()
                   for m in done["result"]["models"]}
        return done["result"], reports, c.get(f"/api/jobs/{job['id']}/log").json()["text"]

    serial, serial_reports, serial_log = run(1)
    par, par_reports, par_log = run(3)
    assert [m["quality"] for m in par["models"]] == [m["quality"] for m in serial["models"]]
    assert all(m["quality"] for m in par["models"])
    for reports in (serial_reports, par_reports):  # EXPDTA di MODELLER con data e ora del job: si toglie
        for report in reports.values():
            assert report["structure"].pop("method").startswith("THEORETICAL MODEL, MODELLER")
    assert par_reports == serial_reports
    assert "3 in parallelo" in par_log and "in parallelo" not in serial_log.replace("passi in parallelo", "")


# ------------------------------------------------------------------------------------------ template preparati
class _Check:
    def __init__(self) -> None:
        self.cancelled = threading.Event()

    def __call__(self) -> None:
        if self.cancelled.is_set():
            raise JobCancelled()


@pytest.fixture
def counted(monkeypatch):
    """Conta le preparazioni effettive (modeller.prepare_template)."""
    calls: list[tuple] = []
    original = modeller.prepare_template

    def prepare(source, spec, dest_dir):
        calls.append((Path(source).name, tuple(spec.chains), spec.hetatm, spec.plddt_min))
        return original(source, spec, dest_dir)

    monkeypatch.setattr(modeller, "prepare_template", prepare)
    return calls


def test_prepared_templates_are_copied_to_each_item(tmp_path, ubq_path, counted):
    cache = PreparedTemplates(tmp_path / "cache")
    check = _Check()
    spec = modeller.TemplateSpec("1UBQ", ["A"])
    first, reused1 = cache.get(ubq_path, spec, tmp_path / "item1", check)
    second, reused2 = cache.get(ubq_path, modeller.TemplateSpec("1UBQ", ["A"]), tmp_path / "item2", check)
    assert (reused1, reused2) == (False, True) and len(counted) == 1
    assert first == second == ORIGINAL_PREPARE(ubq_path, spec, tmp_path / "direct")  # come senza cache
    for item in ("item1", "item2"):
        assert (tmp_path / item / "1UBQ_A.pdb").read_bytes() == (tmp_path / "cache" / "0001" / "1UBQ_A.pdb").read_bytes()
    second["chains"].append("Z")  # copia indipendente: la voce in cache non cambia
    third, _ = cache.get(ubq_path, spec, tmp_path / "item3", check)
    assert third["chains"] == ["A"] and (cache.prepared, cache.reused) == (1, 2)


def test_prepared_templates_key_changes_with_template_chains_and_options(tmp_path, counted):
    cache = PreparedTemplates(tmp_path / "cache")
    check = _Check()
    dimer = tmp_path / "DIMER.pdb"
    dimer.write_bytes((DATA / "ubq_dimer.pdb").read_bytes())
    zn = tmp_path / "ZN.pdb"
    zn.write_bytes((DATA / "ubq_zn.pdb").read_bytes())
    af = tmp_path / "AF-P0CG48-F1.pdb"
    af.write_text(af_pdb(), encoding="utf-8")
    T = modeller.TemplateSpec
    cases = [
        (dimer, T("DIMER", ["A"])), (dimer, T("DIMER", ["A"])),            # stesso: riuso
        (dimer, T("DIMER", ["B"])), (dimer, T("DIMER", ["A", "B"])),        # catene
        (zn, T("ZN", ["A"])), (zn, T("ZN", ["A"], hetatm=True)),            # ligandi
        (af, T("AF-P0CG48-F1", ["A"])), (af, T("AF-P0CG48-F1", ["A"], plddt_min=70)),   # ritaglio pLDDT
        (af, T("AF-P0CG48-F1", ["A"], plddt_min=50)), (af, T("AF-P0CG48-F1", ["A"], plddt_min=70)),
    ]
    results = [cache.get(src, spec, tmp_path / f"i{n}", check) for n, (src, spec) in enumerate(cases)]
    assert [reused for _info, reused in results] == [False, True, False, False, False, False, False, False, False,
                                                     True]
    assert len(counted) == 8
    info = [i for i, _r in results]
    assert info[3]["chains"] == ["A", "B"] and info[2]["sequence"] == info[0]["sequence"]
    assert (info[4]["ligands"], info[5]["ligands"]) == (0, 1)
    assert "plddt" not in info[6] and info[7]["plddt"]["removed"] == 11 and info[7]["plddt"]["threshold"] == 70
    assert info[8]["plddt"]["threshold"] == 50 and info[9] == info[7]
    # il file del template cambia (nuovo caricamento con lo stesso id): nuova preparazione. La data di modifica si
    # sposta in avanti di proposito: con la risoluzione dell'orologio del filesystem due scritture vicine possono avere
    # la stessa data, e la chiave deve cambiare per la data come per la dimensione
    before = zn.stat().st_mtime_ns
    zn.write_text((DATA / "1UBQ.pdb").read_text(), encoding="utf-8")
    os.utime(zn, ns=(before + 10 ** 9, before + 10 ** 9))
    again, reused = cache.get(zn, T("ZN", ["A"]), tmp_path / "after", check)
    assert reused is False and again["sequence"] == info[4]["sequence"] and len(counted) == 9


def test_prepared_templates_do_not_keep_failures(tmp_path, ubq_path, counted):
    cache = PreparedTemplates(tmp_path / "cache")
    for n in range(2):
        with pytest.raises(AppError, match="Catene Z non presenti"):
            cache.get(ubq_path, modeller.TemplateSpec("1UBQ", ["Z"]), tmp_path / f"i{n}", _Check())
    assert len(counted) == 2 and (cache.prepared, cache.reused) == (0, 0)


def test_prepared_templates_concurrent_items_prepare_once(tmp_path, ubq_path, monkeypatch):
    gate = threading.Event()
    calls: list[int] = []
    original = modeller.prepare_template

    def slow(source, spec, dest_dir):
        calls.append(1)
        assert gate.wait(5)
        return original(source, spec, dest_dir)

    monkeypatch.setattr(modeller, "prepare_template", slow)
    cache = PreparedTemplates(tmp_path / "cache")
    out: dict[int, tuple] = {}
    threads = [threading.Thread(target=lambda n=n: out.__setitem__(
        n, cache.get(ubq_path, modeller.TemplateSpec("1UBQ", ["A"]), tmp_path / f"i{n}", _Check())))
        for n in range(3)]
    for t in threads:
        t.start()
    until(lambda: len(calls) == 1)
    # gli altri due sono fermi sul lock della chiave (acquire con timeout in PreparedTemplates.get)
    until(lambda: sum(blocked_in(t, "get") for t in threads) == 2)
    assert len(calls) == 1  # gli altri aspettano la prima preparazione
    gate.set()
    for t in threads:
        t.join(10)
    assert sorted(r for _i, r in out.values()) == [False, True, True]
    assert all((tmp_path / f"i{n}" / "1UBQ_A.pdb").exists() for n in range(3))


def test_waiting_for_a_prepared_template_is_cancellable(tmp_path, ubq_path, monkeypatch):
    """L'annullamento ferma l'attesa del lock della chiave (ciclo `lock.acquire(timeout=0.5)` con il controllo), non
    solo il controllo dopo l'acquisizione: il primo elemento tiene il lock finché il test non apre il cancello."""
    gate, entered = threading.Event(), threading.Event()
    original = modeller.prepare_template

    def slow(source, spec, dest_dir):
        entered.set()
        assert gate.wait(5)
        return original(source, spec, dest_dir)

    monkeypatch.setattr(modeller, "prepare_template", slow)
    cache = PreparedTemplates(tmp_path / "cache")
    first = threading.Thread(target=lambda: cache.get(ubq_path, modeller.TemplateSpec("1UBQ", ["A"]),
                                                      tmp_path / "a", _Check()))
    first.start()
    until(entered.is_set)  # il primo ha il lock della chiave e sta preparando
    check = _Check()
    check.cancelled.set()
    t0 = time.perf_counter()
    with pytest.raises(JobCancelled):
        cache.get(ubq_path, modeller.TemplateSpec("1UBQ", ["A"]), tmp_path / "b", check)
    elapsed = time.perf_counter() - t0
    # almeno un giro del ciclo di attesa (0,5 s, con un margine per gli orologi): l'attesa c'è stata ed è annullabile
    assert 0.45 <= elapsed < 2 and not gate.is_set()
    gate.set()
    first.join(10)
    assert not (tmp_path / "b" / "1UBQ_A.pdb").exists() and (tmp_path / "a" / "1UBQ_A.pdb").exists()


@pytest.mark.modeller
@requires_modeller
def test_pipeline_prepares_a_shared_template_once(client_with_ubq, counted):
    """Pipeline di 4 sequenze con lo stesso template: una sola preparazione (dal log e dal risultato)."""
    c = client_with_ubq
    fasta = "".join(f">s{i}\n{YEAST_UB}\n" for i in range(4))
    body = {"fasta": fasta, "template_mode": "fixed", "templates": [{"id": "1UBQ", "chains": ["A"]}],
            "options": {"models": 1, "refinement": "very_fast"}, "quality": False, "parallel": 2}
    job = c.post("/api/pipeline", json=body).json()
    done = wait_job(c, job["id"], timeout=900)
    assert done["status"] == "completed", done.get("error")
    assert done["result"]["counts"] == {"completed": 4}
    assert (done["result"]["templates_prepared"], done["result"]["templates_reused"]) == (1, 3)
    assert len(counted) == 1
    log = c.get(f"/api/jobs/{job['id']}/log").json()["text"]
    assert log.count("(preparazione riusata da un elemento precedente)") == 3
    assert "Template preparati: 1, riusati da altri elementi 3 volte" in log
    # nessuna copia della cache tra i risultati del job
    files = [f["path"] for f in done["files"]]
    assert not [f for f in files if "templates/" in f and not f.startswith("items/")]
    assert sum(1 for f in files if f.endswith("/1UBQ_A.pdb")) == 4
