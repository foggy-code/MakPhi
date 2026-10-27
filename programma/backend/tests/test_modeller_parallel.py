"""Modalità parallela di MODELLER (engines/modeller.PARALLEL_MODULE): script, opzione, stime dei tempi e, con MODELLER
installato, modelli identici con qualunque numero di worker, modalità seriale deterministica e confrontata con quella
parallela, file dei worker non salvati, errore di un worker senza attese fino al timeout."""

import os
import time

import pytest

from makphi.core.jobs import is_intermediate
from makphi.domain import preflight as rules
from makphi.engines import modeller

from .conftest import YEAST_UB, requires_modeller, wait_job
from .perf_serial_parallel import _superposed_ca_rmsd


def _module_and_worker(script: str) -> tuple[str, str]:
    """Sorgenti che lo script principale scrive (modulo della classe e avvio dei worker)."""
    ns: dict = {}
    for line in script.splitlines():
        if line.startswith("    _fh.write("):
            ns.setdefault("texts", []).append(eval(line.strip()[len("_fh.write("):-1]))  # noqa: S307 (repr nostro)
    module, worker = ns["texts"]
    return module, worker


def test_parallel_script_writes_module_and_worker():
    opts = modeller.parse_options({"models": 4, "restraints": [{"type": "helix", "start": "2:A", "end": "9:A"}],
                                   "loop": {"enabled": True, "models": 2, "regions": [{"start": "5:A", "end": "8:A"}]}})
    assert opts.parallel is True and modeller.parse_options({"parallel": False}).parallel is False
    serial = modeller.build_model_script(["T_A"], "t", opts)
    parallel = modeller.build_model_script(["T_A"], "t", opts, parallel=True)
    assert "use_parallel_job" not in serial and "class MakPhiModel" in serial
    compile(parallel, "model.py", "exec")
    # la classe sta nel modulo importato dai worker, non nello script principale
    assert not any(ln.startswith("class ") for ln in parallel.splitlines())
    assert "from makphi_model import MakPhiModel" in parallel
    assert "a.use_parallel_job(job)" in parallel and "out['workers'] = _bl_workers" in parallel
    assert "os.environ['MAKPHI_MODELLER_MASTER']" in parallel
    module, worker = _module_and_worker(parallel)
    compile(module, modeller.PARALLEL_MODULE, "exec")
    compile(worker, modeller.PARALLEL_WORKER, "exec")
    assert "class MakPhiModel(DOPELoopModel)" in module and "secondary_structure.Alpha" in module
    assert "def mark(text)" in module
    # il worker applica la licenza prima di importare MODELLER e, se fallisce, ferma lo script principale
    assert worker.startswith(modeller.tools.LICENSE_PREAMBLE)
    assert "modworker import main" in worker and "@@MAKPHI worker-failed" in worker and "SIGTERM" in worker


def test_worker_files_are_intermediate():
    for name in ("model.worker0", "model.worker12", modeller.PARALLEL_MODULE, modeller.PARALLEL_WORKER):
        assert is_intermediate(name), name
    assert not is_intermediate("model.py") and not is_intermediate("yeast.B99990001.pdb")


def test_estimates_with_parallel_lanes():
    assert rules.modeling_lanes(8, 16) == 8 and rules.modeling_lanes(8, 3) == 3
    assert rules.modeling_lanes(1, 16) == 1 and rules.modeling_lanes(8, 16, parallel=False) == 1
    serial = rules.building_seconds(415, 8, "fast")
    assert rules.building_seconds(415, 8, "fast", lanes=1) == serial
    parallel = rules.building_seconds(415, 8, "fast", lanes=8)
    # un giro di modelli invece di 8, più l'avvio dei worker
    model = rules.T_MODEL * rules.growth(415 / 76, rules.MODEL_QUADRATIC)
    assert parallel == pytest.approx(serial - 7 * model + rules.T_WORKERS)
    assert rules.building_seconds(415, 8, "fast", lanes=3) > parallel  # 3 giri
    assert rules.modeling_seconds(76, 5, "fast", "best", True) == pytest.approx(13.89, rel=0.05)  # come prima


def _model(client, **options) -> dict:
    job = client.post("/api/model", json={"target": {"name": "yeast_ub", "sequence": YEAST_UB},
                                          "templates": [{"id": "1UBQ", "chains": ["A"]}], "quality": "none",
                                          "options": {"models": 3, "refinement": "very_fast", "seed": -4321,
                                                      **options}}).json()
    done = wait_job(client, job["id"], timeout=600)
    assert done["status"] == "completed", done.get("error")
    return done


def _coords(client, job: dict) -> list[list[str]]:
    core = client.app.state.core
    return [[ln[30:54] for ln in core.jobs.file_path(job["id"], m["file"]).read_text().splitlines()
             if ln.startswith("ATOM")] for m in job["result"]["models"]]


def _texts(client, job: dict) -> list[str]:
    core = client.app.state.core
    return [core.jobs.file_path(job["id"], m["file"]).read_text() for m in job["result"]["models"]]


@requires_modeller
def test_parallel_models_do_not_depend_on_the_number_of_workers(client_with_ubq):
    c = client_with_ubq
    c.put("/api/settings", json={"max_workers": 3})
    three = _model(c, refinement="fast")
    res = three["result"]
    # con almeno 3 core il primo calcolo è davvero parallelo: 3 modelli, 3 worker
    assert res["options"]["parallel"] is True and res["modeller_workers"] >= 1
    if (os.cpu_count() or 1) >= 3:
        assert res["modeller_workers"] == 3
    assert "MODELLER in parallelo" in c.get(f"/api/jobs/{three['id']}/log").json()["text"]
    names = {f["path"] for f in three["files"]}
    assert not any(".worker" in n or n in (modeller.PARALLEL_MODULE, modeller.PARALLEL_WORKER) for n in names)
    c.put("/api/settings", json={"max_workers": 1})
    one = _model(c, refinement="fast")
    assert one["result"]["modeller_workers"] == 1
    assert _coords(c, three) == _coords(c, one)
    # modalità seriale su richiesta, registrata nel risultato
    serial = _model(c, refinement="fast", parallel=False)
    assert serial["result"]["options"]["parallel"] is False and serial["result"]["modeller_workers"] is None
    # in serie: stesso seme, stesse coordinate
    assert _coords(c, _model(c, refinement="fast", parallel=False)) == _coords(c, serial)
    # serie e parallelo danno la stessa struttura in un altro sistema di riferimento: coordinate assolute diverse,
    # Cα entro 1 Å dopo la sovrapposizione (misurato su 3 semi: 0,2–0,54 Å; tests/perf_serial_parallel.py)
    assert _coords(c, serial) != _coords(c, three)
    for a, b in zip(_texts(c, serial), _texts(c, three)):
        assert _superposed_ca_rmsd(a, b) < 1.0
    single = _model(c, models=1)  # un solo modello: sempre seriale
    assert single["result"]["options"]["parallel"] is False


@requires_modeller
def test_a_failing_worker_stops_the_job_at_once(client_with_ubq, monkeypatch):
    broken = modeller._WORKER_BODY.replace("    from modeller.parallel.modworker import main as _w_main",
                                           "    raise RuntimeError('worker di prova non avviato')")
    assert broken != modeller._WORKER_BODY
    monkeypatch.setattr(modeller, "_WORKER_BODY", broken)
    c = client_with_ubq
    start = time.monotonic()
    job = c.post("/api/model", json={"target": {"name": "yeast_ub", "sequence": YEAST_UB},
                                     "templates": [{"id": "1UBQ", "chains": ["A"]}], "quality": "none",
                                     "options": {"models": 2, "refinement": "very_fast"}}).json()
    done = wait_job(c, job["id"], timeout=300)
    assert done["status"] == "failed"
    assert time.monotonic() - start < 120  # non il timeout del job (120 minuti)
    assert "Errore in un worker di MODELLER" in done["error"] and "worker di prova non avviato" in done["error"]
