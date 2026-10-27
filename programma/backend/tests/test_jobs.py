import sys
import threading
import time

import pytest

from makphi.config import UserSettings
from makphi.core.db import Database
from makphi.core.errors import AppError, Conflict, NotFound
from makphi.core.jobs import JobManager

from .conftest import until


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
    assert started.wait(5)
    # il processo è partito davvero (la sua prima riga è nel log): l'annullamento deve terminarlo
    until(lambda: "via" in manager.read_log(job["id"])["text"])
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


def test_requeue_completed_only_when_explicitly_allowed(manager):
    gate = threading.Event()
    runs = []

    def handler(ctx, params):
        runs.append(1)
        if len(runs) > 1:
            gate.wait(10)  # la ripresa resta in esecuzione
        return {"runs": len(runs)}

    manager.register("twice", handler)
    job_id = wait(manager, manager.submit("twice", {})["id"])["id"]
    with pytest.raises(Conflict, match="già completato"):
        manager.requeue(job_id)  # predefinito: solo falliti e annullati
    with pytest.raises(ValueError):
        manager.requeue(job_id, statuses=frozenset({"completed", "running"}))  # mai da uno stato attivo

    def refuse(_job):
        raise Conflict("niente da riprendere")

    with pytest.raises(Conflict, match="niente da riprendere"):
        manager.requeue(job_id, statuses=frozenset({"completed"}), check=refuse)
    assert manager.get(job_id)["status"] == "completed" and len(runs) == 1  # rifiutata dal controllo: nulla cambia
    seen = []
    manager.requeue(job_id, statuses=frozenset({"completed"}), check=lambda job: seen.append(job["status"]))
    assert seen == ["completed"]  # il controllo vede il job riletto, prima del passaggio a 'queued'
    start = time.time()
    while manager.get(job_id)["status"] != "running" and time.time() - start < 10:
        time.sleep(0.02)
    job = manager.get(job_id)
    assert job["status"] == "running" and job["progress"] == 0  # non più la barra piena del job completato
    gate.set()
    done = wait(manager, job_id)
    assert done["status"] == "completed" and done["result"] == {"runs": 2} and done["progress"] == 1


def _scratch_manager(tmp_path, name: str):
    db = Database(tmp_path / f"{name}.db")
    mgr = JobManager(db, tmp_path / "jobs", workers=1, settings_provider=UserSettings,
                     scratch_dir=tmp_path / "scratch")
    return db, mgr


def test_delete_and_resume_never_both_succeed(tmp_path):
    """Eliminazione e ripresa concorrenti: o il job sparisce e la ripresa non lo trova, o la ripresa parte e
    l'eliminazione è rifiutata. Mai un job eliminato mentre riparte (cartelle orfane, processi non annullabili)."""
    db, mgr = _scratch_manager(tmp_path, "d")
    gate = threading.Event()
    runs = []

    def handler(ctx, params):
        runs.append(1)
        if len(runs) == 1:
            raise AppError("primo tentativo fallito")
        gate.wait(10)  # la ripresa resta in esecuzione
        (ctx.workdir / "modello.pdb").write_text("ATOM\n")
        return {}

    mgr.register("flaky", handler)
    try:
        job_id = mgr.submit("flaky", {})["id"]
        assert wait(mgr, job_id)["status"] == "failed"
        # l'eliminazione si ferma a metà (in _close_log) mentre arriva la ripresa
        in_delete, release = threading.Event(), threading.Event()
        close_log = mgr._close_log

        def held_close_log(jid):
            if threading.current_thread().name == "elimina":
                in_delete.set()
                release.wait(10)
            close_log(jid)

        mgr._close_log = held_close_log
        outcome = {}

        def delete():
            try:
                mgr.delete(job_id)
                outcome["delete"] = "ok"
            except Conflict:
                outcome["delete"] = "conflict"

        deleter = threading.Thread(target=delete, name="elimina")
        deleter.start()
        assert in_delete.wait(5)
        try:
            mgr.requeue(job_id)
            outcome["resume"] = "ok"
            start = time.time()
            while mgr.get(job_id)["status"] != "running" and time.time() - start < 5:
                time.sleep(0.02)
        except NotFound:
            outcome["resume"] = "notfound"
        except Conflict:
            outcome["resume"] = "conflict"
        release.set()
        deleter.join(20)
        assert outcome in ({"delete": "ok", "resume": "notfound"}, {"delete": "conflict", "resume": "ok"}), outcome
        gate.set()
        if outcome["delete"] == "ok":
            with pytest.raises(NotFound):
                mgr.get(job_id)
            with pytest.raises(NotFound):
                mgr.cancel(job_id)
            assert len(runs) == 1  # nessuna esecuzione ripartita
            assert not mgr.workdir(job_id).exists() and not mgr.live_dir(job_id).exists()
        else:
            assert wait(mgr, job_id)["status"] == "completed"
            assert (mgr.workdir(job_id) / "modello.pdb").exists()
    finally:
        gate.set()
        mgr.shutdown()
        db.close()


def test_delete_waits_for_a_resume_being_prepared(tmp_path):
    """Ripresa annullata mentre si prepara (stato già terminale): l'eliminazione aspetta la fine della
    preparazione invece di cancellare i file mentre questa li sta azzerando."""
    db, mgr = _scratch_manager(tmp_path, "p")
    mgr.register("once", lambda ctx, p: (_ for _ in ()).throw(AppError("guasto")))
    try:
        job_id = wait(mgr, mgr.submit("once", {})["id"])["id"]
        preparing, go = threading.Event(), threading.Event()

        def slow_prepare(_job_id, final_dir):
            preparing.set()
            go.wait(10)
            (final_dir / "items").mkdir(exist_ok=True)
            (final_dir / "items" / "azzerato.txt").write_text("preparazione")

        outcome = {}
        # l'eliminazione deve fermarsi sull'attesa di `_idle` con la preparazione in corso (predicato falso)
        waits: list[bool] = []
        real_wait_for = mgr._idle.wait_for

        def watched_wait_for(predicate, timeout=None):
            if threading.current_thread().name == "elimina":
                waits.append(predicate())  # chiamato con il lock preso: lo stato visto dall'eliminazione
            return real_wait_for(predicate, timeout)

        mgr._idle.wait_for = watched_wait_for

        def resume():
            try:
                mgr.requeue(job_id, prepare=slow_prepare)
                outcome["resume"] = "ok"
            except NotFound:  # eliminato subito dopo la preparazione
                outcome["resume"] = "notfound"

        def delete():
            try:
                mgr.delete(job_id)
                outcome["delete"] = "ok"
            except Exception as exc:  # l'esito si controlla sotto
                outcome["delete"] = repr(exc)

        resumer = threading.Thread(target=resume)
        resumer.start()
        assert preparing.wait(5)
        assert mgr.cancel(job_id)["status"] == "cancelled"
        deleter = threading.Thread(target=delete, name="elimina")
        deleter.start()
        until(lambda: waits)
        # in attesa della preparazione: ha trovato il job in `_preparing` e resta fermo finché questa non finisce
        assert waits == [False] and deleter.is_alive() and "delete" not in outcome
        go.set()
        resumer.join(20)
        deleter.join(20)
        assert outcome["delete"] == "ok" and outcome["resume"] in ("ok", "notfound"), outcome
        with pytest.raises(NotFound):
            mgr.get(job_id)
        assert not mgr.workdir(job_id).exists()  # nessuna cartella ricreata dopo l'eliminazione
    finally:
        go.set()
        mgr.shutdown()
        db.close()


def test_final_state_is_written_even_if_saving_the_files_fails(tmp_path):
    """Disco dati pieno: il job finisce 'failed' con il motivo invece di restare 'running' fino al riavvio."""
    db, mgr = _scratch_manager(tmp_path, "f")
    commit = mgr._commit
    full = {"on": True}

    def disk_full(job_id, source, *, success):
        if full["on"]:
            raise OSError(28, "No space left on device")
        commit(job_id, source, success=success)

    mgr._commit = disk_full
    mgr.register("app_err", lambda ctx, p: (_ for _ in ()).throw(AppError("errore chiaro")))
    mgr.register("ok", lambda ctx, p: {"ok": True})
    try:
        failed = wait(mgr, mgr.submit("app_err", {})["id"], timeout=10)
        assert failed["status"] == "failed" and failed["error"].startswith("errore chiaro")
        assert "salvataggio dei file non riuscito" in failed["error"]
        assert "salvataggio dei file non riuscito" in mgr.read_log(failed["id"])["text"]
        ok = wait(mgr, mgr.submit("ok", {})["id"], timeout=10)
        assert ok["status"] == "failed" and "No space left on device" in ok["error"]
        # chiuso davvero: si può riprendere subito, senza attendere
        full["on"] = False
        mgr.register("app_err", lambda ctx, p: {"ok": True})
        mgr.requeue(failed["id"])
        assert wait(mgr, failed["id"])["status"] == "completed"
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
