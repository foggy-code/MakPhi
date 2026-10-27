"""QMEAN più veloce: più calcoli contemporanei (QMEAN_PARALLEL, posti della risorsa QMEAN_LOCAL nel dispatcher) e
profili HHblits riusati per sequenza (docker/qmean/run_qmean_wrapper.py, pulizia nel worker)."""

import importlib.util
import os
import threading
import time
from pathlib import Path

import httpx
import pytest

from makphi.core import dispatch
from makphi.core.jobs import QMEAN_LOCAL, RESOURCE_STAGE
from makphi.engines import qmean_local

from .conftest import until, wait_job
from .test_engines_extra import QMEAN_OUTPUT
from .test_qmean_fallback import OneAtATimeWorker, Worker, worker  # noqa: F401 (worker: fixture)
from .test_scheduling import _Runs

WORKER = Path(__file__).resolve().parents[2] / "docker" / "qmean" / "qmean_worker.py"
GB = 1024 ** 3


@pytest.fixture
def qw(monkeypatch):
    """Il modulo del worker QMEAN (Python della libreria standard), caricato come nei test di uniclust.py."""
    if not WORKER.exists():
        pytest.skip("sorgenti del worker QMEAN non presenti")
    monkeypatch.syspath_prepend(str(WORKER.parent))
    spec = importlib.util.spec_from_file_location("qmean_worker_speed", WORKER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# ---------------------------------------------------------------------------------------------- dispatcher
def test_a_resource_with_two_places_runs_two_jobs_together():
    runs = _Runs()
    places = {"servizio": 2}
    d = dispatch.Dispatcher("t", lambda: 4, runs, resource_capacity=lambda name: places.get(name, 1))
    try:
        for job_id in ("a", "b", "c"):
            runs.push(d, job_id, resource=lambda: "servizio")
        assert {runs.started.get(timeout=5), runs.started.get(timeout=5)} == {"a", "b"}
        assert runs.nothing_starts()  # "c" aspetta un posto
        runs.gates["a"].set()
        assert runs.started.get(timeout=5) == "c"
        runs.releases["a"]()  # già restituita alla fine: non libera un posto in più
        runs.push(d, "d", resource=lambda: "servizio")
        assert runs.nothing_starts()
        places["servizio"] = 3  # letta a ogni scelta: un posto in più vale subito
        d.wake()
        assert runs.started.get(timeout=5) == "d"
    finally:
        for gate in runs.gates.values():
            gate.set()
        d.close()


def test_without_capacity_a_resource_stays_exclusive():
    runs = _Runs()
    d = dispatch.Dispatcher("t", lambda: 4, runs, resource_capacity=lambda name: 1 / 0)  # illeggibile: 1 posto
    try:
        runs.push(d, "a", resource=lambda: "servizio")
        runs.push(d, "b", resource=lambda: "servizio")
        assert runs.started.get(timeout=5) == "a" and runs.nothing_starts()
    finally:
        for gate in runs.gates.values():
            gate.set()
        d.close()


# ---------------------------------------------------------------------------------------------- MakPhi
class TwoPlacesWorker(OneAtATimeWorker):
    """Worker con due posti (QMEAN_PARALLEL=2)."""

    def __call__(self, request: httpx.Request) -> httpx.Response:
        response = super().__call__(request)
        if request.url.path == "/status":
            return httpx.Response(200, json={**response.json(), "slots": 2})
        return response


def test_local_qmean_uses_the_places_declared_by_the_service(client_with_ubq, worker, monkeypatch):  # noqa: F811
    monkeypatch.setitem(qmean_local._slots, "value", 1)
    w = worker(TwoPlacesWorker())
    c = client_with_ubq
    body = {"source": {"type": "template", "id": "1UBQ"}}
    first = c.post("/api/qmean", json=body).json()["id"]
    until(lambda: "q1" in w.gates)
    assert qmean_local.slots() == 2  # letto dallo stato all'inizio del primo calcolo
    second = c.post("/api/qmean", json=body).json()["id"]
    until(lambda: "q2" in w.gates)  # parte mentre il primo è in corso
    third = c.post("/api/qmean", json=body).json()["id"]
    until(lambda: c.get(f"/api/jobs/{third}").json()["stage"] == RESOURCE_STAGE[QMEAN_LOCAL])
    assert "q3" not in w.gates
    w.gates["q1"].set()
    assert wait_job(c, first, timeout=20)["status"] == "completed"
    until(lambda: "q3" in w.gates)
    w.gates["q2"].set()
    w.gates["q3"].set()
    for job in (second, third):
        assert wait_job(c, job, timeout=20)["status"] == "completed"
    assert w.peak == 2


def test_slots_need_the_local_service(monkeypatch):
    monkeypatch.setitem(qmean_local._slots, "value", 4)
    monkeypatch.delenv("MAKPHI_QMEAN_URL", raising=False)
    assert qmean_local.slots() == 1
    monkeypatch.setenv("MAKPHI_QMEAN_URL", "http://qmean:8080")
    assert qmean_local.slots() == 4


class ReusedProfileWorker(Worker):
    def __call__(self, request: httpx.Request) -> httpx.Response:
        if request.url.path == "/jobs/j1" and request.method == "GET":
            return httpx.Response(200, json={"status": "completed", "result": {
                **QMEAN_OUTPUT, "makphi_database": {"name": "UniRef30_2023_02", "version": "2023-02"},
                "makphi_hhblits": {"cpu": 8, "memory": "ok", "seconds": 4, "parallel": 16},
                "makphi_profiles": {"reused": 1, "computed": 0}}})
        return super().__call__(request)


def test_a_reused_profile_is_reported(client_with_ubq, worker, monkeypatch):  # noqa: F811
    monkeypatch.setitem(qmean_local._slots, "value", 1)
    worker(ReusedProfileWorker())
    c = client_with_ubq
    job = c.post("/api/qmean", json={"source": {"type": "template", "id": "1UBQ"}}).json()
    done = wait_job(c, job["id"])
    assert done["status"] == "completed", done.get("error")
    hh = done["result"]["hhblits"]
    assert hh["profiles_reused"] == 1 and hh["profiles_computed"] == 0 and hh["parallel"] == 16
    assert "Profilo HHblits riusato per 1 sequenza" in c.get(f"/api/jobs/{job['id']}/log").json()["text"]


# ---------------------------------------------------------------------------------------------- worker
def test_processors_are_shared_between_concurrent_calculations(qw, monkeypatch):
    monkeypatch.setattr(qw, "CPU_FIXED", 0)
    monkeypatch.setattr(qw, "CPU_ALL", 8)
    monkeypatch.setattr(qw, "CPU_COUNT", 224)
    assert qw.hhblits_cpu("ok") == 8 and qw.hhblits_cpu("ok", 16) == 8  # 224 / 16 = 14, massimo 8
    monkeypatch.setattr(qw, "CPU_COUNT", 32)
    assert qw.hhblits_cpu("ok", 16) == 2 and qw.hhblits_cpu("ok", 64) == 1
    assert qw.hhblits_cpu("swap", 2) == 1
    monkeypatch.setattr(qw, "CPU_FIXED", 12)
    assert qw.hhblits_cpu("ok", 16) == 12  # QMEAN_HHBLITS_CPU vince sempre


def test_places_are_limited_by_memory(qw, monkeypatch):
    db = {"a3m": ("x", "y")}
    monkeypatch.setattr(qw.uniclust, "memory_needed", lambda _db: 12 * GB)
    monkeypatch.setattr(qw.uniclust, "memory_recommended", lambda _db: 21 * GB)  # cs219 da 9 GB, condiviso
    monkeypatch.setattr(qw, "PARALLEL", 16)
    assert qw.slots_for(db, {"total": 1500 * GB, "swap": 0}) == 16
    assert qw.slots_for(db, {"total": 64 * GB, "swap": 0}) == 4  # (57,6 - 9) / 12
    assert qw.slots_for(db, {"total": 8 * GB, "swap": 0}) == 1
    assert qw.slots_for(None, {"total": 64 * GB}) == 1 and qw.slots_for(db, None) == 1
    monkeypatch.setattr(qw, "PARALLEL", 1)
    assert qw.slots_for(db, {"total": 1500 * GB, "swap": 0}) == 1


def test_gate_runs_calculations_together_and_copies_alone(qw):
    gate = qw.Gate(2)
    events = []
    lock = threading.Lock()

    def run(name, exclusive, hold):
        gate.enter(exclusive)
        with lock:
            events.append(("in", name))
        hold.wait(5)
        with lock:
            events.append(("out", name))
        gate.leave(exclusive)

    holds = {n: threading.Event() for n in ("q1", "q2", "copy", "q3")}
    threads = [threading.Thread(target=run, args=("q1", False, holds["q1"])),
               threading.Thread(target=run, args=("q2", False, holds["q2"]))]
    for t in threads:
        t.start()
    until(lambda: gate.running == 2)
    copy = threading.Thread(target=run, args=("copy", True, holds["copy"]))
    copy.start()
    until(lambda: gate.waiting == 1)
    q3 = threading.Thread(target=run, args=("q3", False, holds["q3"]))  # non sorpassa la copia in attesa
    q3.start()
    time.sleep(0.2)
    assert ("in", "copy") not in events and ("in", "q3") not in events
    holds["q1"].set()
    holds["q2"].set()
    until(lambda: ("in", "copy") in events)
    assert ("in", "q3") not in events and gate.running == 0
    holds["copy"].set()
    until(lambda: ("in", "q3") in events)
    holds["q3"].set()
    for t in [*threads, copy, q3]:
        t.join(5)
    assert gate.running == 0 and not gate.exclusive


def test_profile_folder_follows_the_database_on_disk(qw, tmp_path, monkeypatch):
    monkeypatch.setattr(qw, "PROFILES", str(tmp_path / "profiles"))
    index = tmp_path / "UniRef30_a3m.ffindex"
    index.write_text("a\t0\t1\n")
    db = {"id": "UniRef30_2023_02/UniRef30_2023_02", "a3m": (str(tmp_path / "x.ffdata"), str(index))}
    first = qw.profile_folder(db)
    assert first.startswith(str(tmp_path / "profiles")) and "UniRef30_2023_02" in os.path.basename(first)
    assert qw.profile_folder(db) == first
    index.write_text("a\t0\t1\nb\t1\t1\n")  # database aggiornato: profili da rifare
    assert qw.profile_folder(db) != first


def test_old_profiles_are_removed_first(qw, tmp_path, monkeypatch):
    root = tmp_path / "profiles"
    monkeypatch.setattr(qw, "PROFILES", str(root))
    folder = root / "db-abc"
    folder.mkdir(parents=True)
    now = time.time()
    for n in range(10):
        path = folder / f"{n}.a3m"
        path.write_bytes(b"x" * 100)
        os.utime(path, (now - 1000 + n, now - 1000 + n))  # 0 il più vecchio, 9 il più recente
    assert qw.prune_profiles(2000) == 1000  # sotto il massimo: niente da togliere
    left = qw.prune_profiles(700)  # oltre: si scende all'80% (560 byte), togliendo i meno recenti
    assert left == 500 and sorted(p.name for p in folder.iterdir()) == [f"{n}.a3m" for n in range(5, 10)]
    status = qw.profiles_status()
    assert status["count"] == 5 and status["size"] == 500
