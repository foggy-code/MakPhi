"""Robustezza dell'esecuzione dei job: nessun job bloccato in 'running', nessun processo esterno orfano.

- Un errore nella preparazione dell'esecuzione (cartella di lavoro nello scratch, tipo di job, contesto, log) chiude il
  job come fallito, con un messaggio leggibile, e lo toglie dalle esecuzioni attive: si può eliminare e riprendere.
- Un errore mentre si legge l'output di un processo esterno (on_line, log su disco pieno) termina il processo prima di
  propagarsi invariato: niente processi che continuano fuori dal budget di CPU e non più annullabili.
"""

import shutil
import sys
import threading
import time

import pytest

from makphi.core import jobs as jobs_module

from .test_jobs import _scratch_manager, wait


def _wait_idle(mgr, job_id, timeout=10):
    """Attende che l'esecuzione del job sia chiusa (fuori da `_running`): lo stato finale si scrive un istante prima."""
    start = time.time()
    while mgr.busy(job_id):
        if time.time() - start > timeout:
            raise AssertionError(f"il job {job_id} resta fra le esecuzioni attive")
        time.sleep(0.02)


def _break_scratch(mgr):
    """Scratch inutilizzabile: un file al posto della cartella dei job. Come un volume pieno o non scrivibile (la
    cartella di lavoro non si crea), ma uguale su Windows e su Linux, anche da root."""
    shutil.rmtree(mgr.scratch_jobs)
    mgr.scratch_jobs.write_text("non una cartella\n")


def _repair_scratch(mgr):
    mgr.scratch_jobs.unlink()
    mgr.scratch_jobs.mkdir()


def test_unusable_scratch_fails_the_job_instead_of_leaving_it_running(tmp_path):
    db, mgr = _scratch_manager(tmp_path, "s")
    runs = []
    mgr.register("demo", lambda ctx, p: runs.append(1) or {"ok": True})
    try:
        _break_scratch(mgr)
        first = wait(mgr, mgr.submit("demo", {})["id"], timeout=10)
        second = wait(mgr, mgr.submit("demo", {})["id"], timeout=10)
        for job in (first, second):
            assert job["status"] == "failed" and job["stage"] == "Fallito", job
            assert job["error"].startswith("Cartella di lavoro del job non creata") and "scratch" in job["error"]
            _wait_idle(mgr, job["id"])
        assert not runs  # l'handler non è mai partito
        assert "ERRORE: Cartella di lavoro del job non creata" in mgr.read_log(first["id"])["text"]
        assert mgr.cancel(first["id"])["status"] == "failed"  # già terminato: niente da annullare
        # chiuso davvero: si elimina e si riprende subito, senza Conflict
        mgr.delete(second["id"])
        assert not mgr.workdir(second["id"]).exists()
        _repair_scratch(mgr)
        mgr.requeue(first["id"])
        done = wait(mgr, first["id"])
        assert done["status"] == "completed" and done["result"] == {"ok": True} and runs == [1]
        assert not mgr.live_dir(first["id"]).exists()  # scratch svuotato come sempre
    finally:
        mgr.shutdown()
        db.close()


def test_unknown_kind_at_start_fails_the_job(tmp_path):
    """Un tipo non più registrato quando il job parte: fallito con il motivo, non un KeyError fuori dal try."""
    db, mgr = _scratch_manager(tmp_path, "k")
    gate, started = threading.Event(), threading.Event()

    def blocker(ctx, params):
        started.set()
        gate.wait(10)
        return {}

    mgr.register("block", blocker)
    mgr.register("gone", lambda ctx, p: {})
    try:
        busy = mgr.submit("block", {})["id"]
        assert started.wait(5)  # l'unico worker è occupato: il job resta in coda
        job_id = mgr.submit("gone", {})["id"]
        del mgr._handlers["gone"]
        gate.set()
        assert wait(mgr, busy)["status"] == "completed"
        job = wait(mgr, job_id, timeout=10)
        assert job["status"] == "failed" and job["error"] == "Tipo di job sconosciuto: gone"
        mgr.delete(job_id)  # senza Conflict
    finally:
        gate.set()
        mgr.shutdown()
        db.close()


@pytest.mark.parametrize("where", ["contesto", "log"])
def test_failure_while_starting_fails_the_job(tmp_path, monkeypatch, where):
    """Contesto non costruito o log non apribile (disco dati pieno) all'avvio: il job finisce fallito e si elimina."""
    db, mgr = _scratch_manager(tmp_path, where)
    if where == "contesto":
        def broken(*_args, **_kwargs):
            raise RuntimeError("contesto non creato")

        monkeypatch.setattr(jobs_module, "JobContext", broken)
        expected = "RuntimeError: contesto non creato"
    else:
        def disk_full(_job_id, _line):
            raise OSError(28, "No space left on device")

        mgr._append_log = disk_full
        expected = "OSError: [Errno 28] No space left on device"
    mgr.register("demo", lambda ctx, p: {"ok": True})
    try:
        job = wait(mgr, mgr.submit("demo", {})["id"], timeout=10)
        assert job["status"] == "failed" and job["error"] == expected, job
        _wait_idle(mgr, job["id"])
        if where == "contesto":
            log = mgr.read_log(job["id"])["text"]
            assert "ERRORE INATTESO" in log and "contesto non creato" in log
        assert not mgr.live_dir(job["id"]).exists()  # la cartella di lavoro creata si toglie comunque
        mgr.delete(job["id"])
    finally:
        mgr.shutdown()
        db.close()


# stampa una riga e poi resterebbe in esecuzione a lungo
SLEEPER = "import time; print('x', flush=True); time.sleep(60)"


@pytest.mark.parametrize("where", ["on_line", "log"])
def test_error_while_reading_output_kills_the_process(tmp_path, where):
    """on_line che solleva (es. "database or disk is full" scrivendo il progresso) o log su disco pieno: il processo
    esterno si termina e l'eccezione originale arriva invariata a chi ha chiamato `run`, in pochi secondi."""
    db, mgr = _scratch_manager(tmp_path, f"o-{where}")
    procs, raised, seen = [], [], []
    register = mgr._register_process

    def spy(job_id, proc):
        procs.append(proc)
        register(job_id, proc)

    mgr._register_process = spy
    if where == "on_line":
        def on_line(_line):
            exc = RuntimeError("database or disk is full")
            raised.append(exc)
            raise exc
    else:
        on_line = None
        append = mgr._append_log

        def disk_full(job_id, line):
            if line == "x":  # solo l'output del processo: le righe con orario del job si scrivono
                exc = OSError(28, "No space left on device")
                raised.append(exc)
                raise exc
            append(job_id, line)

        mgr._append_log = disk_full

    def handler(ctx, params):
        t0 = time.monotonic()
        try:
            ctx.run([sys.executable, "-c", SLEEPER], on_line=on_line, timeout=120)
        except Exception as exc:
            seen.append((exc, time.monotonic() - t0))
            raise
        return {}

    mgr.register("slow", handler)
    try:
        job = wait(mgr, mgr.submit("slow", {})["id"], timeout=30)
        assert job["status"] == "failed" and str(raised[0]) in job["error"], job
        (exc, elapsed), = seen
        assert exc is raised[0]  # la stessa eccezione, non un TimeoutExpired o un errore della pulizia
        assert elapsed < 15  # non i 60 s del processo
        (proc,) = procs
        assert proc.poll() is not None  # terminato, non orfano
        assert proc.stdout.closed
        _wait_idle(mgr, job["id"])
        assert mgr.budget.used == 0  # slot di CPU restituito a processo finito
        assert not mgr._processes.get(job["id"])
    finally:
        for proc in procs:  # se il test fallisce non restano processi in giro
            if proc.poll() is None:
                proc.kill()
        mgr.shutdown()
        db.close()


@pytest.mark.parametrize("how", ["cancel", "shutdown"])
def test_cancel_while_a_process_registers_still_kills_it(tmp_path, how):
    """Annullamento (o arresto) che arriva mentre un processo appena lanciato si registra: il processo si termina
    comunque. Si forza l'ordine peggiore: la registrazione cade dopo la copia dei processi da terminare ma prima che
    l'evento di annullamento risulti impostato. Prima l'evento si impostava fuori dal lock della copia e il processo
    sfuggiva a entrambi i controlli: continuava fino alla fine naturale (ore, per MODELLER o OpenMM)."""
    db, mgr = _scratch_manager(tmp_path, f"r-{how}")
    procs = []
    registering, set_called, registered = threading.Event(), threading.Event(), threading.Event()
    register = mgr._register_process

    def racing_register(job_id, proc):
        procs.append(proc)
        event = mgr._cancel_events[job_id]
        real_set = event.set

        def slow_set():
            # qui si inserirebbe la registrazione; se `cancel` tiene il lock non può, e dopo l'attesa si prosegue
            set_called.set()
            registered.wait(1)
            real_set()

        event.set = slow_set
        registering.set()
        set_called.wait(10)  # la registrazione parte solo quando l'annullamento sta impostando l'evento
        register(job_id, proc)
        registered.set()

    mgr._register_process = racing_register

    def handler(ctx, params):
        ctx.run([sys.executable, "-c", "import time; time.sleep(60)"], timeout=120)
        return {}

    mgr.register("slow", handler)
    try:
        job_id = mgr.submit("slow", {})["id"]
        assert registering.wait(10)  # processo lanciato, non ancora registrato
        if how == "cancel":
            mgr.cancel(job_id)
        else:
            mgr.shutdown()
        job = wait(mgr, job_id, timeout=15)  # non i 60 s del processo
        assert job["status"] == "cancelled", job
        (proc,) = procs
        assert proc.poll() is not None  # terminato dall'annullamento, non lasciato girare
        _wait_idle(mgr, job_id)
    finally:
        for proc in procs:  # se il test fallisce non restano processi in giro
            if proc.poll() is None:
                proc.kill()
        mgr.shutdown()
        db.close()
