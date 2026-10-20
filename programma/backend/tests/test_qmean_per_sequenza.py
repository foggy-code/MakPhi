"""QMEAN per sequenza: tutti i posti del servizio usati da BioLoom (pool "io" più largo, dispatcher che non riordina
code enormi a ogni risveglio) e una sola ricerca HHblits per sequenza fra calcoli contemporanei (blocco del wrapper,
pulizia e stato dei profili senza rileggere ogni file a ogni calcolo)."""

import importlib.util
import os
import sys
import threading
import time
import types
from pathlib import Path

import pytest

from bioloom.core import dispatch
from bioloom.core.db import Database
from bioloom.core.jobs import IO_WORKERS, QMEAN_LOCAL, JobManager
from bioloom.config import UserSettings

from .test_qmean_speed import qw  # noqa: F401 (fixture)
from .test_scheduling import _Runs

WRAPPER = Path(__file__).resolve().parents[2] / "docker" / "qmean" / "run_qmean_wrapper.py"


# ---------------------------------------------------------------------------------------------- BioLoom
def test_the_io_pool_grows_with_the_places_of_local_qmean(tmp_path):
    mgr = JobManager(Database(tmp_path / "b.db"), tmp_path / "jobs", workers=1, settings_provider=UserSettings)
    try:
        assert mgr.io_workers() == IO_WORKERS
        places = {"value": 32}
        mgr.set_resource_capacity(QMEAN_LOCAL, lambda: places["value"])
        assert mgr.io_workers() == IO_WORKERS + 31  # 32 QMEAN locali e ancora 8 posti per ricerche e altro
        places["value"] = 1
        assert mgr.io_workers() == IO_WORKERS
        mgr.set_resource_capacity("illeggibile", lambda: 1 / 0)
        assert mgr.io_workers() == IO_WORKERS
    finally:
        mgr.shutdown()


def test_threads_follow_a_capacity_that_grows_without_wake():
    """Coda già piena quando la capacità cresce (posti del QMEAN locale letti dal primo calcolo): i thread in più
    partono appena parte un job, senza `wake` né un nuovo job in coda."""
    runs = _Runs()
    capacity = {"value": 1}
    d = dispatch.Dispatcher("t", lambda: capacity["value"], runs)
    try:
        for job_id in ("a", "b", "c", "d"):
            runs.push(d, job_id)
        assert runs.started.get(timeout=5) == "a" and runs.nothing_starts()
        capacity["value"] = 3
        runs.gates["a"].set()  # la fine di "a" fa partire "b", che porta i thread a 3
        assert {runs.started.get(timeout=5) for _ in range(3)} == {"b", "c", "d"}
    finally:
        for gate in runs.gates.values():
            gate.set()
        d.close()


def test_idle_threads_do_not_sort_a_long_queue_at_every_wakeup(monkeypatch):
    """Con la risorsa piena e una coda lunga, i thread inattivi riordinano la coda solo dopo un evento che può
    rendere avviabile un job, non a ogni risveglio del pool."""
    runs = _Runs()
    d = dispatch.Dispatcher("t", lambda: 8, runs)
    sorts = {"n": 0}
    original = d._ordered

    def counted():
        sorts["n"] += 1
        return original()

    monkeypatch.setattr(d, "_ordered", counted)
    try:
        for n in range(200):
            runs.push(d, f"j{n:03d}", resource=lambda: "servizio")
        assert runs.started.get(timeout=5) == "j000" and runs.nothing_starts()  # risorsa con un posto
        time.sleep(0.3)
        before = sorts["n"]
        with d._cond:
            for _ in range(50):  # risvegli senza cambiamenti (per esempio la fine di un job di un altro tipo)
                d._cond.notify_all()
        time.sleep(0.3)
        assert sorts["n"] == before
        runs.gates["j000"].set()  # posto liberato: si riguarda la coda e parte il successivo
        assert runs.started.get(timeout=5) == "j001"
    finally:
        for gate in runs.gates.values():
            gate.set()
        d.close()


# ---------------------------------------------------------------------------------------------- worker
def test_profile_status_is_reused_and_ignores_the_locks(qw, tmp_path, monkeypatch):  # noqa: F811
    root = tmp_path / "profiles"
    monkeypatch.setattr(qw, "PROFILES", str(root))
    folder = root / "db-abc"
    (folder / qw.LOCKS).mkdir(parents=True)
    (folder / qw.LOCKS / "abcd.lock").write_text("")
    (folder / "a.a3m").write_bytes(b"x" * 100)
    assert qw.profiles_status()["count"] == 1  # il blocco non è un profilo
    (folder / "b.a3m").write_bytes(b"x" * 100)
    assert qw.profiles_status()["count"] == 1  # stato riletto al più ogni PROFILES_STATUS_TTL secondi
    monkeypatch.setattr(qw, "PROFILES_STATUS_TTL", 0)
    assert qw.profiles_status()["count"] == 2


def test_profiles_are_pruned_at_most_once_a_minute(qw, tmp_path, monkeypatch):  # noqa: F811
    root = tmp_path / "profiles"
    monkeypatch.setattr(qw, "PROFILES", str(root))
    monkeypatch.setattr(qw, "PROFILE_CACHE_GB", 300 / qw.GB)
    folder = root / "db-abc"
    (folder / qw.LOCKS).mkdir(parents=True)
    lock = folder / qw.LOCKS / "abcd.lock"
    lock.write_text("")
    now = time.time()
    for n in range(5):
        path = folder / f"{n}.a3m"
        path.write_bytes(b"x" * 100)
        os.utime(path, (now - 100 + n, now - 100 + n))
    qw.maybe_prune_profiles()  # 500 byte oltre i 300: si scende a 240, togliendo i 3 meno recenti
    assert sorted(p.name for p in folder.glob("*.a3m")) == ["3.a3m", "4.a3m"] and lock.exists()
    for n in range(5, 8):
        (folder / f"{n}.a3m").write_bytes(b"x" * 100)
    qw.maybe_prune_profiles()  # meno di un minuto dopo: nessuna pulizia
    assert len(list(folder.glob("*.a3m"))) == 5
    monkeypatch.setitem(qw._pruned, "at", time.time() - qw.PRUNE_SECONDS)
    qw.maybe_prune_profiles()
    assert len(list(folder.glob("*.a3m"))) == 2


# ---------------------------------------------------------------------------------------------- wrapper
@pytest.fixture
def wrapper(monkeypatch, tmp_path):
    """Il wrapper con un finto `ost.bindings.hhblits3` (l'OpenStructure vero c'è solo nell'immagine di QMEAN)."""
    if sys.platform == "win32":
        pytest.skip("il wrapper usa flock: gira solo su Linux, come nel container di QMEAN")
    hhblits3 = types.ModuleType("ost.bindings.hhblits3")
    hhblits3.HHblits = type("HHblits", (), {"OUTPUT_PREFIX": "query_hhblits"})
    ost = types.ModuleType("ost")
    bindings = types.ModuleType("ost.bindings")
    bindings.hhblits3 = hhblits3
    ost.bindings = bindings
    for name, module in (("ost", ost), ("ost.bindings", bindings), ("ost.bindings.hhblits3", hhblits3)):
        monkeypatch.setitem(sys.modules, name, module)
    monkeypatch.setenv("TMPDIR", str(tmp_path))
    spec = importlib.util.spec_from_file_location("run_qmean_wrapper_test", WRAPPER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _Sequence:
    def __init__(self, name):
        self.name = name

    def GetName(self):
        return self.name


def _fake_run_qmean(searches, lock):
    """Un finto run_qmean: `_seqanno` fa la "ricerca" (scrive l'a3m) solo se l'a3m non c'è, come quello vero."""

    def seqanno(target_seq, workdir, *args, **kwargs):
        a3m = os.path.join(workdir, target_seq.GetName(), "query_hhblits.a3m")
        if not os.path.exists(a3m):
            with lock:
                searches.append(target_seq.GetName())
            time.sleep(0.3)  # HHblits
            os.makedirs(os.path.dirname(a3m), exist_ok=True)
            with open(a3m, "w") as fh:
                fh.write(">profilo di " + target_seq.GetName())
        return "risultato"

    return types.SimpleNamespace(_seqanno=seqanno)


def test_models_of_the_same_sequence_wait_for_one_search(wrapper, tmp_path):
    folder = str(tmp_path / "profiles" / "db-abc")
    searches, lock = [], threading.Lock()
    modules = []
    for _ in range(4):  # 4 calcoli contemporanei: 3 modelli della stessa sequenza e uno di un'altra
        module = _fake_run_qmean(searches, lock)
        wrapper._profile_cache(module, folder)
        modules.append(module)
    names = ["a" * 32, "a" * 32, "a" * 32, "b" * 32]
    threads = [threading.Thread(target=m._seqanno, args=(_Sequence(n), str(tmp_path / f"w{i}")))
               for i, (m, n) in enumerate(zip(modules, names))]
    start = time.monotonic()
    for t in threads:
        t.start()
    for t in threads:
        t.join(10)
    assert sorted(searches) == ["a" * 32, "b" * 32]  # una ricerca per sequenza
    assert time.monotonic() - start < 1.5  # le due sequenze in parallelo, gli altri modelli senza ricerca
    assert sorted(os.listdir(folder)) == [wrapper.LOCKS, "a" * 32 + ".a3m", "b" * 32 + ".a3m"]
    for i in (0, 1, 2):
        with open(tmp_path / f"w{i}" / ("a" * 32) / "query_hhblits.a3m") as fh:
            assert fh.read() == ">profilo di " + "a" * 32


def test_a_failed_search_releases_the_sequence(wrapper, tmp_path):
    folder = str(tmp_path / "profiles" / "db-abc")

    def broken(target_seq, workdir, *args, **kwargs):
        raise RuntimeError("HHblits interrotto")

    module = types.SimpleNamespace(_seqanno=broken)
    wrapper._profile_cache(module, folder)
    with pytest.raises(RuntimeError):
        module._seqanno(_Sequence("c" * 32), str(tmp_path / "w"))
    searches = []
    retry = _fake_run_qmean(searches, threading.Lock())
    wrapper._profile_cache(retry, folder)
    done = threading.Thread(target=retry._seqanno, args=(_Sequence("c" * 32), str(tmp_path / "w2")))
    done.start()
    done.join(5)
    assert not done.is_alive() and searches == ["c" * 32]  # il blocco non è rimasto preso
