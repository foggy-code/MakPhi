"""Budget di CPU condiviso fra i job e coda con priorità (core/budget.py, core/dispatch.py, core/jobs.py).

I processi sono veri (l'interprete Python che dorme): ognuno scrive nel proprio file l'istante di inizio e di fine
misurati da dentro il processo, cioè mentre tiene lo slot. Dalla sovrapposizione degli intervalli si ricava il
massimo di processi contemporanei, senza affidarsi a contatori interni.
"""

import queue
import sys
import threading
import time
from pathlib import Path

import pytest

from bioloom.config import UserSettings
from bioloom.core import budget as budget_module
from bioloom.core import dispatch
from bioloom.core.budget import CpuBudget
from bioloom.core.db import Database
from bioloom.core.errors import AppError
from bioloom.core.events import IDLE, JOB
from bioloom.core.jobs import PRIORITY, WAIT_STAGE, JobCancelled, JobManager
from bioloom.services.compute import ComputeService
from bioloom.services.live import LiveService, _ListState
from bioloom.services.pipeline import PipelineService
from bioloom.services.templates import TemplateLibrary

from .conftest import dispatcher_settled, until

SLEEPER = ("import sys, time; t0 = time.time(); time.sleep(float(sys.argv[2])); "
           "open(sys.argv[1], 'w').write(f'{t0} {time.time()}')")
# aspetta che compaia un file (cancello aperto dal test), con un limite di sicurezza
GATE = ("import os, sys, time; end = time.time() + 30\n"
        "while not os.path.exists(sys.argv[1]) and time.time() < end: time.sleep(0.02)")


@pytest.fixture
def make_manager(tmp_path):
    made = []

    def make(workers=1, settings=UserSettings, name="s"):
        db = Database(tmp_path / f"{name}.db")
        mgr = JobManager(db, tmp_path / f"jobs_{name}", workers, settings, scratch_dir=tmp_path / f"scratch_{name}")
        made.append((mgr, db))
        return mgr

    yield make
    for mgr, db in made:
        mgr.shutdown()
        db.close()


def wait(mgr, job_id, timeout=30):
    start = time.time()
    while time.time() - start < timeout:
        job = mgr.get(job_id)
        if job["status"] in ("completed", "failed", "cancelled"):
            return job
        time.sleep(0.02)
    raise AssertionError(f"job non terminato entro {timeout} s: {mgr.get(job_id)}")


class FakeClock:
    """Orologio dei test dell'invecchiamento: avanza solo quando il test lo sposta."""

    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def sleeper(ctx, marks: Path, name: str, seconds: float, **kwargs):
    return ctx.run([sys.executable, "-c", SLEEPER, str(marks / f"{name}.txt"), str(seconds)], **kwargs)


def gated(ctx, gate: Path, **kwargs):
    return ctx.run([sys.executable, "-c", GATE, str(gate)], **kwargs)


def max_overlap(marks: Path) -> int:
    """Massimo di processi contemporanei dagli intervalli scritti dai processi stessi."""
    events = []
    for f in marks.glob("*.txt"):
        start, end = map(float, f.read_text().split())
        events += [(start, 1), (end, -1)]
    current = peak = 0
    for _t, delta in sorted(events, key=lambda e: (e[0], e[1])):  # a parità di istante, prima le fini
        current += delta
        peak = max(peak, current)
    return peak


# ------------------------------------------------------------------------------------------ budget di CPU
def test_budget_never_runs_more_processes_than_its_slots(make_manager, tmp_path):
    mgr = make_manager(workers=2)
    marks = tmp_path / "marks"
    marks.mkdir()

    def handler(ctx, p):
        for n in range(2):
            sleeper(ctx, marks, f"{p['n']}_{n}", 0.4)
        return {}

    mgr.register("tool", handler, pool="io")  # fino a 8 job insieme: il limite viene solo dal budget
    ids = [mgr.submit("tool", {"n": i})["id"] for i in range(5)]
    for job_id in ids:
        assert wait(mgr, job_id)["status"] == "completed"
    assert len(list(marks.glob("*.txt"))) == 10
    assert max_overlap(marks) == 2  # mai più di 2, ma 2 sì: il budget non serializza tutto
    assert mgr.budget.used == 0 and mgr.budget.waiting == 0


def test_budget_governs_threads_of_the_same_job(make_manager, tmp_path):
    """Passi eseguiti in parallelo da thread dello stesso job (come la validazione in parallelo): stesso limite,
    senza che il codice che li lancia debba occuparsene."""
    mgr = make_manager(workers=2)
    marks = tmp_path / "marks"
    marks.mkdir()

    def handler(ctx, p):
        errors = []

        def step(n):
            try:
                sleeper(ctx.sub(f"passo{n}", ctx.workdir / f"passo{n}"), marks, f"passo{n}", 0.3)
            except Exception as exc:  # l'esito si controlla sotto
                errors.append(exc)

        threads = [threading.Thread(target=step, args=(n,)) for n in range(6)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        if errors:
            raise errors[0]
        return {}

    mgr.register("steps", handler)
    assert wait(mgr, mgr.submit("steps", {})["id"])["status"] == "completed"
    assert max_overlap(marks) == 2


def test_cancel_while_waiting_for_cpu(make_manager, tmp_path):
    mgr = make_manager(workers=1)
    gate, marker = tmp_path / "apri", tmp_path / "partito.txt"
    mgr.register("holder", lambda ctx, p: gated(ctx, gate) and {}, pool="io")
    mgr.register("waiter", lambda ctx, p: ctx.run([sys.executable, "-c", f"open(r'{marker}', 'w')"]) and {},
                 pool="io")
    holder = mgr.submit("holder", {})["id"]
    until(lambda: mgr.budget.used == 1)
    waiter = mgr.submit("waiter", {})["id"]
    until(lambda: mgr.budget.waiting == 1 and mgr.get(waiter)["stage"] == WAIT_STAGE)
    t0 = time.monotonic()
    mgr.cancel(waiter)
    done = wait(mgr, waiter)
    assert done["status"] == "cancelled" and time.monotonic() - t0 < 2  # risvegliato subito, non a fine attesa
    assert not marker.exists()  # il processo non è mai partito
    assert mgr.budget.waiting == 0 and mgr.budget.used == 1  # lo slot resta a chi lo aveva
    gate.write_text("")
    assert wait(mgr, holder)["status"] == "completed"
    assert mgr.budget.used == 0


def test_waiting_for_cpu_does_not_count_in_the_process_timeout(make_manager, tmp_path):
    mgr = make_manager(workers=1)
    gate = tmp_path / "apri"
    mgr.register("holder", lambda ctx, p: gated(ctx, gate) and {}, pool="io")

    def short(ctx, p):
        t0 = time.monotonic()
        result = sleeper(ctx, tmp_path, "breve", 0.3, timeout=1.0)
        return {"elapsed": time.monotonic() - t0, "code": result.returncode}

    mgr.register("short", short, pool="io")
    holder = mgr.submit("holder", {})["id"]
    until(lambda: mgr.budget.used == 1)
    job = mgr.submit("short", {})["id"]
    until(lambda: mgr.budget.waiting == 1)
    time.sleep(1.3)  # più del timeout del processo, passato in attesa dello slot
    gate.write_text("")
    done = wait(mgr, job)
    assert done["status"] == "completed", done["error"]
    assert done["result"]["elapsed"] > 1.3 and done["result"]["code"] == 0
    assert wait(mgr, holder)["status"] == "completed"


def test_timeout_still_applies_to_the_process_itself(make_manager, tmp_path):
    mgr = make_manager(workers=1)
    mgr.register("slow", lambda ctx, p: sleeper(ctx, tmp_path, "lento", 20, timeout=0.5) and {})
    done = wait(mgr, mgr.submit("slow", {})["id"])
    assert done["status"] == "failed" and "timeout" in done["error"]
    assert mgr.budget.used == 0  # slot restituito anche dopo il timeout


def test_budget_and_workers_follow_the_setting_without_restart(make_manager, tmp_path):
    settings = {"value": UserSettings(max_workers=1)}
    mgr = make_manager(workers=None, settings=lambda: settings["value"])
    assert mgr.cpu_workers() == 1 and mgr.budget.capacity == 1
    gate, marker = tmp_path / "apri", tmp_path / "secondo.txt"
    mgr.register("holder", lambda ctx, p: gated(ctx, gate) and {}, pool="io")
    mgr.register("quick", lambda ctx, p: ctx.run([sys.executable, "-c", f"open(r'{marker}', 'w')"]) and {},
                 pool="io")
    mgr.register("cpu_job", lambda ctx, p: {})
    holder = mgr.submit("holder", {})["id"]
    until(lambda: mgr.budget.used == 1)
    quick = mgr.submit("quick", {})["id"]
    # in attesa nella coda del budget (budget.waiting): con l'unico slot occupato il processo non può partire
    until(lambda: mgr.budget.waiting == 1)
    assert not marker.exists() and mgr.get(quick)["status"] == "running"  # un solo slot: aspetta
    settings["value"] = UserSettings(max_workers=2)
    mgr.reconfigure()
    assert wait(mgr, quick)["status"] == "completed" and marker.exists()
    assert mgr.get(holder)["status"] == "running"  # il secondo è partito mentre il primo teneva il suo slot
    assert mgr.budget.capacity == 2 and mgr.cpu_workers() == 2
    gate.write_text("")
    assert wait(mgr, holder)["status"] == "completed"


def test_settings_api_updates_the_budget_and_stats_report_it(client):
    core = client.app.state.core
    assert client.put("/api/settings", json={"max_workers": 3}).status_code == 200
    assert core.jobs.cpu_workers() == 3 and core.jobs.budget.capacity == 3
    assert client.get("/api/system/info").json()["workers"] == 3
    assert client.get("/api/system/stats").json()["cpu_slots"] == {"total": 3, "used": 0, "waiting": 0}
    client.put("/api/settings", json={"max_workers": 0})  # automatico: core − 1
    assert core.jobs.budget.capacity == core.settings.get().effective_workers()


def test_cpu_pool_follows_the_setting(make_manager):
    settings = {"value": UserSettings(max_workers=1)}
    mgr = make_manager(workers=None, settings=lambda: settings["value"])
    gates = {n: threading.Event() for n in ("a", "b")}
    mgr.register("model", lambda ctx, p: gates[p["n"]].wait(10) and {})
    a = mgr.submit("model", {"n": "a"})["id"]
    until(lambda: mgr.get(a)["status"] == "running")
    b = mgr.submit("model", {"n": "b"})["id"]
    until(lambda: dispatcher_settled(mgr, "cpu"))  # coda riesaminata: nessun worker libero per b
    queued = mgr.with_eta(mgr.get(b))
    assert queued["status"] == "queued" and queued["queue_position"] == 1  # un solo job di calcolo alla volta
    settings["value"] = UserSettings(max_workers=2)
    mgr.reconfigure()
    until(lambda: mgr.get(b)["status"] == "running")
    for gate in gates.values():
        gate.set()
    assert wait(mgr, a)["status"] == "completed" and wait(mgr, b)["status"] == "completed"


def test_multithreaded_program_takes_one_slot_per_thread(make_manager, tmp_path):
    mgr = make_manager(workers=3)
    gate = tmp_path / "apri"
    seen = {}

    def handler(ctx, p):
        threads = ctx.cpu_threads(16)  # come MMseqs2 con --threads: non oltre gli slot
        seen["threads"] = threads
        gated(ctx, gate, cpu=threads)
        return {}

    mgr.register("mmseqs_like", handler, pool="io")
    mgr.register("single", lambda ctx, p: sleeper(ctx, tmp_path, "singolo", 0.1) and {}, pool="io")
    big = mgr.submit("mmseqs_like", {})["id"]
    until(lambda: mgr.budget.used == 3)
    assert seen["threads"] == 3 and mgr.cpu_workers() == 3
    single = mgr.submit("single", {})["id"]
    until(lambda: mgr.budget.waiting == 1)  # tutti gli slot occupati da un solo programma
    gate.write_text("")
    assert wait(mgr, big)["status"] == "completed" and wait(mgr, single)["status"] == "completed"


def test_elastic_multithreaded_program_takes_the_free_slots(make_manager, tmp_path):
    """Comando costruito dal numero di thread (MMseqs2, MUSCLE 5): prende subito gli slot liberi invece di aspettarli
    tutti, e parte con altrettanti thread."""
    mgr = make_manager(workers=3)
    hold, gate = tmp_path / "tiene", tmp_path / "apri"
    seen = []
    mgr.register("holder", lambda ctx, p: gated(ctx, hold) and {}, pool="io")

    def elastic(ctx, p):
        def build(threads):
            seen.append(threads)
            return [sys.executable, "-c", GATE, str(gate)]

        ctx.run(build, cpu=16)
        return {}

    mgr.register("elastic", elastic, pool="io")
    holder = mgr.submit("holder", {})["id"]
    until(lambda: mgr.budget.used == 1)
    job = mgr.submit("elastic", {})["id"]
    until(lambda: bool(seen))
    assert seen == [2] and mgr.budget.used == 3 and mgr.budget.waiting == 0  # i 2 slot liberi, senza attesa
    gate.write_text("")
    hold.write_text("")
    assert wait(mgr, job)["status"] == "completed" and wait(mgr, holder)["status"] == "completed"
    assert mgr.budget.used == 0


def test_elastic_request_is_not_blocked_behind_busy_slots():
    budget = CpuBudget(lambda: 3)
    long_running = budget.acquire(2)
    elastic = budget.acquire(3, minimum=1)  # come MMseqs2: parte subito con lo slot libero
    assert elastic.slots == 1 and budget.used == 3 and budget.waiting == 0
    elastic.release()
    long_running.release()
    with budget.acquire(3, minimum=1) as all_free:
        assert all_free.slots == 3  # con tutto libero prende tutti i thread che vuole
    assert budget.used == 0


def test_light_processes_do_not_wait_for_cpu(make_manager, tmp_path):
    mgr = make_manager(workers=1)
    gate, marker = tmp_path / "apri", tmp_path / "io.txt"
    mgr.register("holder", lambda ctx, p: gated(ctx, gate) and {}, pool="io")
    mgr.register("io_tool", lambda ctx, p: ctx.run([sys.executable, "-c", f"open(r'{marker}', 'w')"],
                                                   cpu=False) and {}, pool="io")
    holder = mgr.submit("holder", {})["id"]
    until(lambda: mgr.budget.used == 1)
    assert wait(mgr, mgr.submit("io_tool", {})["id"])["status"] == "completed" and marker.exists()
    gate.write_text("")
    assert wait(mgr, holder)["status"] == "completed"


def test_budget_order_priority_then_arrival_without_overtaking():
    budget = CpuBudget(lambda: 3)
    held = budget.acquire(2)
    order, threads = [], []

    def take(name, slots, priority):
        grant = budget.acquire(slots, priority=priority)
        order.append(name)
        time.sleep(0.05)
        grant.release()

    def start(name, slots, priority):
        thread = threading.Thread(target=take, args=(name, slots, priority))
        threads.append(thread)
        thread.start()
        until(lambda: budget.waiting == len(threads) - len(order))

    start("grande", 3, 1)      # primo: aspetta 3 slot, ne è libero 1
    start("piccolo", 1, 1)     # stessa priorità, arrivato dopo: non sorpassa anche se 1 slot è libero
    start("interattivo", 1, 0)  # priorità più alta: passa davanti a entrambi
    assert order == ["interattivo"]  # lo slot libero va subito al primo in ordine di priorità
    held.release()
    for thread in threads:
        thread.join(10)
    assert order == ["interattivo", "grande", "piccolo"]
    assert budget.used == 0 and budget.waiting == 0


def test_budget_wait_is_interrupted_and_frees_the_turn():
    budget = CpuBudget(lambda: 1)
    held = budget.acquire(1)
    cancel = threading.Event()
    outcome = {}

    def check():
        if cancel.is_set():
            raise JobCancelled()

    def waiter():
        try:
            budget.acquire(1, check=check)
            outcome["first"] = "slot"
        except JobCancelled:
            outcome["first"] = "annullato"

    first = threading.Thread(target=waiter)
    first.start()
    until(lambda: budget.waiting == 1)
    second = threading.Thread(target=lambda: outcome.setdefault("second", budget.acquire(1)))
    second.start()
    until(lambda: budget.waiting == 2)
    cancel.set()
    budget.wake()
    first.join(5)
    assert outcome["first"] == "annullato" and budget.waiting == 1
    held.release()
    second.join(5)
    assert "second" in outcome and budget.used == 1  # il secondo è diventato il primo
    outcome["second"].release()
    budget.close()
    with pytest.raises(AppError):
        budget.acquire(1)  # arresto: nessun nuovo processo


def test_requests_larger_than_the_capacity_are_clamped():
    budget = CpuBudget(lambda: 2)
    with budget.acquire(10) as grant:
        assert grant.slots == 2 and budget.used == 2
    assert budget.used == 0 and budget.slots_for(10) == 2 and budget.slots_for(1) == 1


def test_effective_priority_ages_one_level_per_interval():
    age = dispatch.effective_priority
    assert [age(2, t, 60) for t in (0, 59.9, 60, 119, 120, 1000)] == [2, 2, 1, 1, 0, 0]
    assert age(0, 1000, 60) == 0 and age(1, 30, 60) == 1
    assert age(2, 1000, 0) == 2  # nessun invecchiamento
    # i test di ordinamento con l'orologio vero durano pochi secondi: restano dentro il primo intervallo
    assert dispatch.AGING_SECONDS >= 60 and budget_module.AGING_SECONDS >= 30


def _budget_waiter(budget, got, name, priority):
    thread = threading.Thread(target=lambda: got.put((name, budget.acquire(1, priority=priority))), daemon=True)
    thread.start()
    return thread


def test_budget_aging_serves_a_pipeline_process_under_continuous_interactive_ones():
    """Un processo di pipeline (priorità 2) non aspetta per sempre dietro a processi interattivi continui: dopo
    2 intervalli di invecchiamento è servito prima di quelli arrivati dopo di lui."""
    clock = FakeClock()
    budget = CpuBudget(lambda: 1, aging=60, clock=clock)
    got: queue.Queue = queue.Queue()
    current = budget.acquire(1, priority=0)
    _budget_waiter(budget, got, "pipeline", 2)
    until(lambda: budget.waiting == 1)
    order = []
    for n in range(1, 10):
        _budget_waiter(budget, got, f"interattivo{n}", 0)  # ne arriva sempre uno nuovo prima che lo slot si liberi
        until(lambda: budget.waiting == 2)
        clock.advance(30)
        current.release()
        name, current = got.get(timeout=5)
        order.append(name)
        if name == "pipeline":
            break
    # t=30: priorità 2; t=60 e t=90: 1 (gli interattivi, arrivati dopo, sono a 0); t=120: 0 e arrivata prima
    assert order == ["interattivo1", "interattivo2", "interattivo3", "pipeline"] and clock.now == 120
    current.release()
    name, last = got.get(timeout=5)
    assert name == "interattivo4"
    last.release()
    assert budget.used == 0 and budget.waiting == 0


def test_budget_aging_keeps_arrival_order_at_equal_effective_priority():
    clock = FakeClock()
    budget = CpuBudget(lambda: 1, aging=60, clock=clock)
    got: queue.Queue = queue.Queue()
    held = budget.acquire(1)
    _budget_waiter(budget, got, "pipeline", 2)  # t=0
    until(lambda: budget.waiting == 1)
    clock.advance(50)
    _budget_waiter(budget, got, "modello", 1)  # t=50
    until(lambda: budget.waiting == 2)
    clock.advance(15)  # t=65: la pipeline è salita a 1, il modello è ancora a 1: vale l'arrivo
    held.release()
    name, grant = got.get(timeout=5)
    assert name == "pipeline"
    grant.release()
    name, grant = got.get(timeout=5)
    assert name == "modello"
    grant.release()
    # senza attesa sufficiente vale la priorità: il modello arrivato dopo passa davanti alla pipeline
    held = budget.acquire(1)
    _budget_waiter(budget, got, "pipeline", 2)
    until(lambda: budget.waiting == 1)
    clock.advance(50)
    _budget_waiter(budget, got, "modello", 1)
    until(lambda: budget.waiting == 2)
    held.release()
    name, grant = got.get(timeout=5)
    assert name == "modello"
    grant.release()
    name, grant = got.get(timeout=5)
    assert name == "pipeline"
    grant.release()
    assert budget.used == 0 and budget.waiting == 0


# ------------------------------------------------------------------------------------------ fase durante l'attesa
def test_stage_while_waiting_for_cpu(make_manager, tmp_path):
    """Il job mostra "In attesa di CPU libera" mentre aspetta e riprende la sua fase appena parte il processo;
    un elemento di pipeline (figlio con fase propria) non cancella la fase della pipeline ma la completa."""
    mgr = make_manager(workers=1)
    gates = {name: tmp_path / name for name in ("tiene", "diretto", "elemento", "silenzioso")}
    mgr.register("holder", lambda ctx, p: gated(ctx, gates["tiene"]) and {}, pool="io")

    def direct(ctx, p):
        ctx.progress(0.1, "Calcolo di prova")
        gated(ctx, gates["diretto"])
        return {}

    def pipeline_like(ctx, p):
        ctx.progress(0.2, "1 di 3 sequenze elaborate")
        item = ctx.sub("a", ctx.workdir / "a", on_progress=lambda f, s: None)
        gated(item.sub("qa", item.workdir / "qa"), gates["elemento"])  # anche da un figlio senza fase dell'elemento
        return {}

    def silent_child(ctx, p):
        ctx.progress(0.8, "Analisi di qualità del modello")
        gated(ctx.sub("qa", ctx.workdir / "qa"), gates["silenzioso"])
        return {}

    mgr.register("direct", direct, pool="io")
    mgr.register("pipeline_like", pipeline_like, pool="io")
    mgr.register("silent", silent_child, pool="io")
    holder = mgr.submit("holder", {})["id"]
    until(lambda: mgr.budget.used == 1)
    jobs = {}
    for n, kind in enumerate(("direct", "pipeline_like", "silent"), start=1):
        jobs[kind] = mgr.submit(kind, {})["id"]
        until(lambda n=n: mgr.budget.waiting == n)  # in attesa in quest'ordine: partiranno così
    until(lambda: mgr.get(jobs["direct"])["stage"] == WAIT_STAGE)
    until(lambda: mgr.get(jobs["pipeline_like"])["stage"] == "1 di 3 sequenze elaborate · in attesa di CPU libera")
    until(lambda: mgr.get(jobs["silent"])["stage"] == WAIT_STAGE)
    gates["tiene"].write_text("")
    until(lambda: mgr.get(jobs["direct"])["stage"] == "Calcolo di prova")  # primo arrivato: parte e torna la fase
    gates["diretto"].write_text("")
    until(lambda: mgr.get(jobs["pipeline_like"])["stage"] == "1 di 3 sequenze elaborate")
    gates["elemento"].write_text("")
    until(lambda: mgr.get(jobs["silent"])["stage"] == "Analisi di qualità del modello")
    gates["silenzioso"].write_text("")
    for job_id in (holder, *jobs.values()):
        assert wait(mgr, job_id)["status"] == "completed"


def test_stage_with_parallel_steps_of_the_same_job(make_manager, tmp_path):
    """Passi in parallelo dello stesso job: finché uno gira, l'attesa dell'altro si aggiunge alla fase invece di
    sostituirla (il job non è fermo)."""
    mgr = make_manager(workers=1)
    first, second = tmp_path / "primo", tmp_path / "secondo"

    def handler(ctx, p):
        ctx.progress(0.5, "Validazione")
        errors = []

        def step(gate):
            try:
                gated(ctx.sub(gate.name, ctx.workdir / gate.name), gate)
            except Exception as exc:  # l'esito si controlla sotto
                errors.append(exc)

        a = threading.Thread(target=step, args=(first,))
        a.start()
        until(lambda: mgr.budget.used == 1)
        b = threading.Thread(target=step, args=(second,))
        b.start()
        a.join()
        b.join()
        if errors:
            raise errors[0]
        return {}

    mgr.register("parallel", handler)
    job_id = mgr.submit("parallel", {})["id"]
    until(lambda: mgr.get(job_id)["stage"] == "Validazione · in attesa di CPU libera")
    first.write_text("")  # il primo passo finisce: il secondo prende lo slot
    until(lambda: mgr.get(job_id)["stage"] == "Validazione")
    second.write_text("")
    assert wait(mgr, job_id)["status"] == "completed"


# ------------------------------------------------------------------------------------------ coda con priorità
def test_queue_runs_by_kind_priority_then_creation(make_manager):
    mgr = make_manager(workers=1)
    gate, started, order = threading.Event(), threading.Event(), []

    def blocker(ctx, p):
        started.set()
        gate.wait(10)
        return {}

    mgr.register("gate", blocker)
    kinds = ("pipeline", "model", "loop", "quality", "align", "search", "qmean", "align2d")
    for kind in kinds:
        mgr.register(kind, lambda ctx, p: order.append(p["name"]) or {})  # tutti nel pool "cpu", un worker
    blocking = mgr.submit("gate", {})["id"]
    assert started.wait(5)
    submitted = [("pipeline", "p1"), ("model", "m1"), ("quality", "q1"), ("pipeline", "p2"), ("search", "s1"),
                 ("loop", "l1"), ("align", "a1"), ("model", "m2"), ("qmean", "qm1"), ("align2d", "a2")]
    ids = {name: mgr.submit(kind, {"name": name})["id"] for kind, name in submitted}
    expected = ["q1", "s1", "a1", "qm1", "a2", "m1", "l1", "m2", "p1", "p2"]  # priorità, poi creazione
    queued = {job["id"]: job for job in mgr.with_eta(mgr.list(statuses=["queued"]))}
    assert [queued[ids[name]]["queue_position"] for name in expected] == list(range(1, 11))
    assert mgr.with_eta(mgr.get(blocking))["queue_position"] is None  # in esecuzione: nessuna posizione
    gate.set()
    for job_id in ids.values():
        assert wait(mgr, job_id)["status"] == "completed"
    assert order == expected
    assert PRIORITY["search"] < PRIORITY["model"] < PRIORITY["pipeline"]


def test_resumed_job_keeps_its_place_by_creation(make_manager):
    """Una ripresa rientra in coda con la data di creazione del job: a parità di priorità passa davanti ai job
    creati dopo (FIFO per created_at)."""
    mgr = make_manager(workers=1)
    gate, started, order, runs = threading.Event(), threading.Event(), [], {}

    def blocker(ctx, p):
        started.set()
        gate.wait(10)
        return {}

    def flaky(ctx, p):
        runs[p["name"]] = runs.get(p["name"], 0) + 1
        if runs[p["name"]] == 1 and p["name"] == "vecchia":
            raise AppError("primo tentativo fallito")
        order.append(p["name"])
        return {}

    mgr.register("model", blocker)
    mgr.register("pipeline", flaky)
    old = mgr.submit("pipeline", {"name": "vecchia"})["id"]
    assert wait(mgr, old)["status"] == "failed"
    blocking = mgr.submit("model", {})["id"]  # anche nello stesso secondo (created_at uguale) vale la creazione
    assert started.wait(5)
    new = mgr.submit("pipeline", {"name": "nuova"})["id"]
    resumed = mgr.with_eta(mgr.requeue(old))
    assert resumed["queue_position"] == 1 and mgr.with_eta(mgr.get(new))["queue_position"] == 2
    gate.set()
    assert wait(mgr, old)["status"] == "completed" and wait(mgr, new)["status"] == "completed"
    assert wait(mgr, blocking)["status"] == "completed"
    assert order == ["vecchia", "nuova"]


def test_cancelled_queued_job_leaves_the_queue_and_can_be_resumed(make_manager):
    mgr = make_manager(workers=1)
    gate, started, order = threading.Event(), threading.Event(), []

    def blocker(ctx, p):
        started.set()
        gate.wait(10)
        return {}

    mgr.register("gate", blocker)
    mgr.register("model", lambda ctx, p: order.append(p["name"]) or {})
    mgr.submit("gate", {})
    assert started.wait(5)
    ids = {name: mgr.submit("model", {"name": name})["id"] for name in ("a", "b", "c")}

    def positions():
        return {name: mgr.with_eta(mgr.get(job_id))["queue_position"] for name, job_id in ids.items()}

    assert positions() == {"a": 1, "b": 2, "c": 3}
    assert mgr.cancel(ids["b"])["status"] == "cancelled"
    assert positions() == {"a": 1, "b": None, "c": 2}
    assert mgr.requeue(ids["b"])["status"] == "queued"
    assert positions() == {"a": 1, "b": 2, "c": 3}  # creato prima di c: torna davanti
    gate.set()
    for job_id in ids.values():
        assert wait(mgr, job_id)["status"] == "completed"
    assert order == ["a", "b", "c"]  # ognuno una volta sola


class _Runs:
    """Esecuzione dei test del dispatcher: registra l'avvio e aspetta il cancello del job."""

    def __init__(self) -> None:
        self.started: queue.Queue = queue.Queue()
        self.gates: dict[str, threading.Event] = {}
        self.releases: dict[str, object] = {}

    def __call__(self, job_id, release):
        self.releases[job_id] = release
        self.started.put(job_id)
        assert self.gates[job_id].wait(10)

    def push(self, d, job_id, priority=0, created_at="", resource=None):
        self.gates[job_id] = threading.Event()
        d.push(job_id, priority=priority, created_at=created_at or job_id, resource=resource)

    def nothing_starts(self, seconds=0.3) -> bool:
        try:
            self.started.get(timeout=seconds)
        except queue.Empty:
            return True
        return False


def test_queue_aging_starts_a_pipeline_under_continuous_interactive_jobs():
    """Con un flusso continuo di job interattivi una pipeline in coda parte dopo 2 intervalli di invecchiamento,
    prima degli interattivi creati dopo di lei; le posizioni in coda seguono lo stesso ordine."""
    clock, runs = FakeClock(), _Runs()
    d = dispatch.Dispatcher("t", lambda: 1, runs, aging=300, clock=clock)
    try:
        runs.push(d, "i00", 0, "t00")
        current = runs.started.get(timeout=5)
        runs.push(d, "pipeline", 2, "t01")
        order = []
        for n in range(1, 10):
            newest = f"i{n:02d}"
            runs.push(d, newest, 0, f"t{n + 1:02d}")  # creati dopo la pipeline
            clock.advance(150)
            if clock.now < 600:
                assert d.positions() == {newest: 1, "pipeline": 2}
            else:
                assert d.positions() == {"pipeline": 1, newest: 2}
            runs.gates[current].set()
            current = runs.started.get(timeout=5)
            order.append(current)
            if current == "pipeline":
                break
        assert order == ["i01", "i02", "i03", "pipeline"] and clock.now == 600
        runs.gates[current].set()
        assert runs.started.get(timeout=5) == "i04"
    finally:
        for gate in runs.gates.values():
            gate.set()
        d.close()


def test_resource_released_early_lets_the_next_job_start_and_is_not_freed_twice():
    runs = _Runs()
    d = dispatch.Dispatcher("t", lambda: 4, runs)
    try:
        for job_id in ("a", "b", "c"):
            runs.push(d, job_id, resource=lambda: "servizio")
        assert runs.started.get(timeout=5) == "a" and runs.nothing_starts()  # b e c aspettano la risorsa
        runs.releases["a"]()  # "a" ha finito la parte che usa il servizio: "b" parte mentre "a" continua
        assert runs.started.get(timeout=5) == "b"
        runs.releases["a"]()  # una seconda volta non fa nulla
        runs.gates["a"].set()  # la chiusura di "a" non libera la risorsa, che ora è di "b"
        assert runs.nothing_starts()
        runs.gates["b"].set()
        assert runs.started.get(timeout=5) == "c"
    finally:
        for gate in runs.gates.values():
            gate.set()
        d.close()


def test_job_context_releases_the_exclusive_resource(make_manager):
    """`JobContext.release_resource` arriva al dispatcher, anche da un contesto figlio: il job successivo con la
    stessa risorsa parte mentre il primo è ancora in esecuzione; senza risorsa non fa nulla."""
    mgr = make_manager(workers=2)
    gates = {name: threading.Event() for name in ("a", "b")}
    started: queue.Queue = queue.Queue()

    def holder(ctx, p):
        started.put(p["name"])
        if p["name"] == "a":
            ctx.sub("figlio", ctx.workdir / "figlio").release_resource()
        assert gates[p["name"]].wait(10)
        return {}

    mgr.register("esclusivo", holder, pool="io", resource=lambda _p: "servizio")
    mgr.register("libero", lambda ctx, p: ctx.release_resource() or {}, pool="io")
    a = mgr.submit("esclusivo", {"name": "a"})["id"]
    assert started.get(timeout=5) == "a"
    b = mgr.submit("esclusivo", {"name": "b"})["id"]
    assert started.get(timeout=5) == "b"
    assert mgr.get(a)["status"] == "running"  # "a" è ancora in esecuzione
    assert wait(mgr, mgr.submit("libero", {})["id"])["status"] == "completed"
    for gate in gates.values():
        gate.set()
    assert wait(mgr, a)["status"] == "completed" and wait(mgr, b)["status"] == "completed"


def test_queued_jobs_at_shutdown_are_marked_by_recover(tmp_path):
    db = Database(tmp_path / "r.db")
    mgr = JobManager(db, tmp_path / "jobs", 1, UserSettings)
    gate, started, ran = threading.Event(), threading.Event(), []

    def blocker(ctx, p):
        started.set()
        gate.wait(10)
        return {}

    mgr.register("gate", blocker)
    mgr.register("model", lambda ctx, p: ran.append(1) or {})
    mgr.submit("gate", {})
    assert started.wait(5)
    queued = mgr.submit("model", {})["id"]
    mgr.shutdown()
    gate.set()
    # finito il job in corso, i worker escono: dopo non c'è più nessuno che possa avviare il job in coda
    until(lambda: not any(t.is_alive() for pool in mgr._pools.values() for t in pool._threads))
    assert not ran  # la coda è chiusa: il job in coda non parte più
    other = JobManager(db, tmp_path / "jobs", 1, UserSettings)
    try:
        assert other.recover() >= 1
        job = other.get(queued)
        assert job["status"] == "failed" and job["stage"] == "Interrotto"
    finally:
        other.shutdown()
        db.close()


def test_state_changes_publish_the_same_events(make_manager):
    mgr = make_manager(workers=1)
    gate, started = threading.Event(), threading.Event()

    def blocker(ctx, p):
        started.set()
        gate.wait(10)
        return {}

    mgr.register("gate", blocker)
    mgr.register("model", lambda ctx, p: {})
    mgr.submit("gate", {})
    assert started.wait(5)
    version = mgr.events.version
    job_id = mgr.submit("model", {})["id"]
    gate.set()
    wait(mgr, job_id)

    def kinds() -> list[str]:
        return [c.what for c in mgr.events.since(version)[1] if c.job_id == job_id]

    until(lambda: IDLE in kinds())  # esecuzione chiusa: pubblicata per ultima
    seen = kinds()
    assert seen[0] == JOB and seen[-1] == IDLE  # in coda, ..., esecuzione chiusa
    assert seen.count(JOB) >= 3  # in coda, avviato, completato


def test_list_stream_reports_queue_position_changes(make_manager):
    mgr = make_manager(workers=1)
    gate, started = threading.Event(), threading.Event()

    def blocker(ctx, p):
        started.set()
        gate.wait(10)
        return {}

    mgr.register("gate", blocker)
    mgr.register("model", lambda ctx, p: {})
    mgr.submit("gate", {})
    assert started.wait(5)
    first = mgr.submit("model", {})["id"]
    second = mgr.submit("model", {})["id"]
    live, state = LiveService(mgr), _ListState()
    live._list_pass(None, [], state, False)
    mgr.cancel(first)
    events = "".join(live._list_pass(None, [], state, True))
    assert f'"id":"{second}"' in events and '"queue_position":1' in events  # era 2°, ora 1°
    gate.set()
    assert wait(mgr, second)["status"] == "completed"


# ------------------------------------------------------------------------------------------ pipeline
def test_pipeline_parallel_4_with_2_cpu_slots_finishes(tmp_path):
    """Nessun deadlock: 4 elementi in parallelo, 2 slot, ogni elemento con un processo e poi due passi in
    parallelo da thread diversi (come la validazione). La pipeline non tiene slot mentre aspetta gli elementi."""
    db = Database(tmp_path / "p.db")
    mgr = JobManager(db, tmp_path / "jobs", 2, lambda: UserSettings(max_workers=4), scratch_dir=tmp_path / "scratch")
    try:
        compute = ComputeService(mgr, TemplateLibrary(tmp_path / "templates"), tmp_path / "uploads",
                                 tmp_path / "databases")
        pipeline = PipelineService(db, mgr, compute)
        marks = tmp_path / "marks"
        marks.mkdir()

        def fake_modeling(sub, *, target, **_kwargs):
            sleeper(sub, marks, f"{target.name}_modello", 0.15)
            errors = []

            def quality_step(n):
                try:
                    sleeper(sub.sub(f"qa{n}", sub.workdir / f"qa{n}"), marks, f"{target.name}_qa{n}", 0.15)
                except Exception as exc:  # l'esito si controlla sotto
                    errors.append(exc)

            threads = [threading.Thread(target=quality_step, args=(n,)) for n in range(2)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()
            if errors:
                raise errors[0]
            model = f"{target.name}.B99990001.pdb"
            (sub.workdir / model).write_text("ATOM\n")
            return {"models": [{"file": model, "best": True, "dope": -1.0, "quality": {}}]}

        compute.run_modeling = fake_modeling
        fasta = "".join(f">s{i}\nMQIFVKTLTGKTITLEVEPSDTIENVKAKIQDKEGIPPDQQRLIFAGKQLEDG\n" for i in range(8))
        job = pipeline.submit({"fasta": fasta, "template_mode": "fixed", "templates": [{"id": "1UBQ", "chains": ["A"]}],
                               "options": {"models": 1}, "quality": False, "parallel": 4}, None)
        done = wait(mgr, job["id"], timeout=90)
        assert done["status"] == "completed", done["error"]
        assert done["result"]["counts"] == {"completed": 8}
        assert "4 in parallelo" in mgr.read_log(job["id"])["text"]
        assert len(list(marks.glob("*.txt"))) == 24
        assert max_overlap(marks) == 2
        assert mgr.budget.used == 0 and mgr.budget.waiting == 0
    finally:
        mgr.shutdown()
        db.close()
