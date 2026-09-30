import sys
import threading
import time

import pytest

from bioloom.config import UserSettings
from bioloom.core.db import Database
from bioloom.core.errors import AppError, Conflict, NotFound
from bioloom.core.jobs import JobManager


@pytest.fixture
def manager(tmp_path):
    db = Database(tmp_path / "t.db")
    mgr = JobManager(db, tmp_path / "jobs", workers=2, settings_provider=UserSettings)
    yield mgr
    mgr.shutdown()
    db.close()


def wait(mgr, job_id, timeout=20):
    start = time.time()
    while time.time() - start < timeout:
        job = mgr.get(job_id)
        if job["status"] in ("completed", "failed", "cancelled"):
            return job
        time.sleep(0.05)
    raise AssertionError("timeout")


def test_success_with_progress_and_log(manager):
    def handler(ctx, params):
        ctx.progress(0.5, "a metà")
        ctx.log("messaggio")
        return {"double": params["x"] * 2}

    manager.register("demo", handler)
    job = manager.submit("demo", {"x": 21}, title="prova")
    done = wait(manager, job["id"])
    assert done["status"] == "completed" and done["result"] == {"double": 42} and done["progress"] == 1
    assert "messaggio" in manager.read_log(job["id"])["text"]
    assert any(f["path"] == "result.json" for f in manager.list_files(job["id"]))


def test_failure_messages(manager):
    manager.register("app_err", lambda ctx, p: (_ for _ in ()).throw(AppError("errore chiaro")))
    manager.register("crash", lambda ctx, p: 1 / 0)
    a = wait(manager, manager.submit("app_err", {})["id"])
    b = wait(manager, manager.submit("crash", {})["id"])
    assert a["status"] == "failed" and a["error"] == "errore chiaro"
    assert b["status"] == "failed" and "ZeroDivisionError" in b["error"]
    assert "Traceback" in manager.read_log(b["id"])["text"]


def test_cancel_kills_subprocess(manager):
    started = threading.Event()

    def handler(ctx, params):
        started.set()
        ctx.run([sys.executable, "-c", "import time; print('via', flush=True); time.sleep(60)"])
        return {}

    manager.register("slow", handler)
    job = manager.submit("slow", {})
    started.wait(5)
    time.sleep(0.5)
    t0 = time.time()
    manager.cancel(job["id"])
    done = wait(manager, job["id"])
    assert done["status"] == "cancelled"
    assert time.time() - t0 < 10
    job2 = manager.submit("slow", {})
    with pytest.raises(Conflict):
        manager.delete(job2["id"])  # un job attivo non si elimina
    manager.cancel(job2["id"])
    wait(manager, job2["id"])
    manager.delete(job2["id"])
    assert not manager.workdir(job2["id"]).exists()


def test_timeout_and_recover(tmp_path):
    db = Database(tmp_path / "r.db")
    settings = lambda: UserSettings(job_timeout_minutes=1)  # noqa: E731
    mgr = JobManager(db, tmp_path / "jobs", workers=1, settings_provider=settings)
    mgr.register("t", lambda ctx, p: ctx.run([sys.executable, "-c", "import time; time.sleep(30)"], timeout=0.5) and {})
    job = wait(mgr, mgr.submit("t", {})["id"])
    assert job["status"] == "failed" and "timeout" in job["error"]
    db.execute("UPDATE jobs SET status='running' WHERE id=?", (job["id"],))
    mgr.shutdown()
    mgr2 = JobManager(db, tmp_path / "jobs", workers=1, settings_provider=settings)
    assert mgr2.recover() == 1
    assert mgr2.get(job["id"])["status"] == "failed"
    mgr2.shutdown()
    db.close()


def test_requeue_concurrent_requests_start_one_execution(manager):
    runs = []

    def handler(ctx, params):
        runs.append(ctx.job_id)
        ctx.log(f"esecuzione {len(runs)}")
        if len(runs) == 1:
            raise AppError("primo tentativo fallito")
        return {"runs": len(runs)}

    manager.register("flaky", handler)
    job_id = manager.submit("flaky", {})["id"]
    assert wait(manager, job_id)["status"] == "failed"
    barrier = threading.Barrier(6)
    outcome = []

    def attempt():
        barrier.wait()
        try:
            manager.requeue(job_id, note="prova")
            outcome.append("ok")
        except Conflict:
            outcome.append("conflict")

    threads = [threading.Thread(target=attempt) for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(20)
    assert sorted(outcome) == ["conflict"] * 5 + ["ok"]
    done = wait(manager, job_id)
    assert done["status"] == "completed" and done["result"] == {"runs": 2}
    assert done["error"] is None and done["finished_at"]
    assert len(runs) == 2  # una sola nuova esecuzione
    log = manager.read_log(job_id)["text"]
    # il log precedente resta e continua
    assert log.index("primo tentativo fallito") < log.index("Ripresa del job: prova") < log.index("esecuzione 2")
    assert log.count("Ripresa del job") == 1


def test_requeue_refuses_active_completed_and_missing_jobs(manager):
    gate = threading.Event()
    manager.register("slow", lambda ctx, p: gate.wait(10) and {})
    manager.register("ok", lambda ctx, p: {})
    done = wait(manager, manager.submit("ok", {})["id"])
    with pytest.raises(Conflict, match="già completato"):
        manager.requeue(done["id"])
    active = manager.submit("slow", {})["id"]
    t0 = time.time()
    with pytest.raises(Conflict, match="in esecuzione"):
        manager.requeue(active)
    assert time.time() - t0 < 2  # nessuna attesa per un job attivo
    gate.set()
    assert wait(manager, active)["status"] == "completed"
    with pytest.raises(NotFound):
        manager.requeue("0123456789abcdef")


def test_requeue_waits_in_queue_and_can_be_cancelled_again(tmp_path):
    db = Database(tmp_path / "q.db")
    mgr = JobManager(db, tmp_path / "jobs", workers=1, settings_provider=UserSettings)
    try:
        gate = threading.Event()
        started = threading.Event()

        def blocker(ctx, params):
            started.set()
            gate.wait(10)
            return {}

        mgr.register("block", blocker)
        mgr.register("once", lambda ctx, p: (_ for _ in ()).throw(AppError("guasto")))
        failed = wait(mgr, mgr.submit("once", {})["id"])
        busy = mgr.submit("block", {})["id"]
        assert started.wait(5)  # l'unico worker è occupato: la ripresa resta in coda
        queued = mgr.requeue(failed["id"])
        assert queued["status"] == "queued" and queued["stage"] == "In coda (ripresa)" and queued["error"] is None
        assert mgr.cancel(failed["id"])["status"] == "cancelled"
        # annullata mentre era in coda, si riprende di nuovo: nel pool restano due esecuzioni in attesa
        assert mgr.requeue(failed["id"])["status"] == "queued"
        gate.set()
        assert wait(mgr, busy)["status"] == "completed"
        final = wait(mgr, failed["id"])
        assert final["status"] == "failed" and final["error"] == "guasto"
        log = mgr.read_log(failed["id"])["text"]
        assert log.count("Ripresa del job") == 2 and log.count("Job once avviato") == 2  # parte una sola volta
    finally:
        mgr.shutdown()
        db.close()


def test_requeue_prepare_failure_leaves_job_failed(manager):
    manager.register("once", lambda ctx, p: (_ for _ in ()).throw(AppError("guasto")))
    job_id = wait(manager, manager.submit("once", {})["id"])["id"]

    def broken(_job_id, _workdir):
        raise AppError("disco pieno")

    with pytest.raises(AppError, match="disco pieno"):
        manager.requeue(job_id, prepare=broken)
    job = manager.get(job_id)
    assert job["status"] == "failed" and job["error"] == "Ripresa non riuscita: disco pieno"
    assert "Ripresa non riuscita: disco pieno" in manager.read_log(job_id)["text"]
    manager.register("once", lambda ctx, p: {"ok": True})
    manager.requeue(job_id)  # dopo l'errore la ripresa resta possibile
    assert wait(manager, job_id)["status"] == "completed"
