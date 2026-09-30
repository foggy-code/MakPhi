"""Tempo residuo (ETA) e aggiornamenti in tempo reale dei job (Server-Sent Events).

Con TestClient un flusso SSE restituisce il corpo solo quando si chiude: ogni richiesta di streaming gira in un
thread con un timeout, così un flusso che non si chiude fa fallire il test invece di bloccare la suite, e i
flussi dell'elenco (che non finiscono da soli) hanno una durata massima ridotta. La consegna in tempo reale
(eventi prima della fine del job, chiusura alla disconnessione del client e all'arresto) si verifica con un
server uvicorn vero su una porta libera e httpx con timeout di lettura.
"""

from __future__ import annotations

import asyncio
import json
import socket
import threading
import time
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any, Optional

import httpx
import pytest
import uvicorn
from fastapi.testclient import TestClient

from bioloom.app import create_app
from bioloom.config import Paths, UserSettings
from bioloom.core import auth, eta
from bioloom.core.db import Database
from bioloom.core.events import ChangeFeed
from bioloom.core.jobs import JobManager
from bioloom.services import live
from tests.conftest import HUMAN_UB, YEAST_UB, wait_job
from tests.test_pipeline_resume import FakeModeller, cancel_at, until

# ----------------------------------------------------------------------------------------------- utilità


def parse_sse(text: str) -> list[tuple[str, Any]]:
    """Eventi (tipo, dati JSON) di un corpo text/event-stream; i commenti diventano (":", testo)."""
    events: list[tuple[str, Any]] = []
    for block in text.replace("\r\n", "\n").split("\n\n"):
        name, data = "message", []
        for line in block.split("\n"):
            if not line:
                continue
            if line.startswith(":"):
                events.append((":", line[1:].strip()))
                continue
            key, _, value = line.partition(":")
            value = value[1:] if value.startswith(" ") else value
            if key == "event":
                name = value
            elif key == "data":
                data.append(value)
        if data:
            events.append((name, json.loads("\n".join(data))))
    return events


def of(events: list[tuple[str, Any]], kind: str) -> list[Any]:
    return [data for name, data in events if name == kind]


class Stream:
    """Richiesta di streaming con TestClient in un thread: `result()` attende la chiusura con un timeout."""

    def __init__(self, client, url: str, **kwargs) -> None:
        self._box: dict = {}
        self._thread = threading.Thread(target=self._run, args=(client, url, kwargs), daemon=True,
                                        name="sse-test")
        self.url = url
        self._thread.start()

    def _run(self, client, url, kwargs) -> None:
        try:
            self._box["response"] = client.get(url, **kwargs)
        except BaseException as exc:  # riportata dal thread principale
            self._box["error"] = exc

    def result(self, timeout: float = 20.0):
        self._thread.join(timeout)
        assert not self._thread.is_alive(), f"il flusso {self.url} non si è chiuso entro {timeout} s"
        if "error" in self._box:
            raise self._box["error"]
        return self._box["response"]


def stream(client, url: str, timeout: float = 20.0, **kwargs):
    return Stream(client, url, **kwargs).result(timeout)


def stamp(ref: datetime, seconds_ago: float) -> str:
    return (ref - timedelta(seconds=seconds_ago)).isoformat(timespec="seconds")


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def status(client, job_id: str) -> dict:
    return client.get(f"/api/jobs/{job_id}/status").json()


def wait_core(core, job_id: str, timeout: float = 20) -> dict:
    until(lambda: core.jobs.get(job_id)["status"] in ("completed", "failed", "cancelled"), timeout)
    return core.jobs.get(job_id)


@pytest.fixture
def pipe(client, monkeypatch):
    """Pipeline con MODELLER finto (come in test_pipeline_resume): un elemento alla volta, nell'ordine."""
    core = client.app.state.core
    fake = FakeModeller()
    monkeypatch.setattr(core.compute, "run_modeling", fake)

    def submit(names, **extra) -> str:
        fasta = "".join(f">{n}\n{HUMAN_UB if i % 2 == 0 else YEAST_UB}\n" for i, n in enumerate(names))
        body = {"fasta": fasta, "template_mode": "fixed", "templates": [{"id": "1UBQ", "chains": ["A"]}],
                "options": {"models": 1}, "quality": False, "parallel": 1, **extra}
        r = client.post("/api/pipeline", json=body)
        assert r.status_code == 200, r.text
        return r.json()["id"]

    yield client, core, fake, submit
    fake.let_go()


def gated_job(core, kind: str = "demo_gate"):
    """Job che scrive una riga, arriva a metà e aspetta il cancello."""
    gate, reached = threading.Event(), threading.Event()

    def handler(ctx, params):
        ctx.log("prima riga")
        ctx.progress(0.5, "a metà")
        reached.set()
        gate.wait(30)
        ctx.log("ultima riga")
        return {"ok": True}

    core.jobs.register(kind, handler)
    return gate, reached


# ----------------------------------------------------------------------------------------------- ETA (logica)


def test_round_eta_steps_and_never_negative():
    assert eta.round_eta(-5) == 0 and eta.round_eta(0) == 0
    assert eta.round_eta(2.4) == 0 and eta.round_eta(2.5) == 5  # metà per eccesso, non all'intero pari
    assert eta.round_eta(42) == 40 and eta.round_eta(58) == 60  # passo di 5 s sotto il minuto
    assert eta.round_eta(125) == 130  # 10 s sotto i 10 minuti
    assert eta.round_eta(754) == 780  # 1 min sotto l'ora
    assert eta.round_eta(3 * 3600 + 100) == 3 * 3600  # 5 min oltre


def test_progress_eta_extrapolates_only_when_reliable():
    assert eta.progress_eta(60, 0.25) == 180  # 60 s × 0,75 / 0,25
    assert eta.progress_eta(100, 0.5) == 100
    assert eta.progress_eta(10, 0.05) == 190  # proprio sulle soglie: 10 s e 5 %
    assert eta.progress_eta(30, 1.0) == 0 and eta.progress_eta(30, 1.4) == 0  # mai negativa
    assert eta.progress_eta(600, 0.049) is None  # sotto il 5 %
    assert eta.progress_eta(9.9, 0.5) is None  # meno di 10 s di esecuzione
    assert eta.progress_eta(None, 0.5) is None and eta.progress_eta(60, None) is None
    assert eta.progress_eta(float("nan"), 0.5) is None


def test_pipeline_eta_median_remaining_and_lanes():
    # mediana 90 s: l'elemento lento (300 s) non sposta la stima; 4 rimanenti su 2 corsie
    assert eta.pipeline_eta([60, 90, 300], remaining=4, parallel=2) == 180
    # un elemento in corso da 30 s: (4 × 90 − 30) / 2 = 165 → 170 (passo di 10 s)
    assert eta.pipeline_eta([60, 90, 300], 4, 2, running_elapsed=[30]) == 170
    # più corsie che elementi: lavorano solo quelle occupate
    assert eta.pipeline_eta([100, 100], 1, 8) == 100
    # in corso da più della mediana: quasi finito, mai negativo
    assert eta.pipeline_eta([100, 100], 1, 1, [250]) == 0
    # meno di 2 elementi conclusi: nessuna stima
    assert eta.pipeline_eta([100], 5, 2) is None and eta.pipeline_eta([], 5, 2) is None
    assert eta.pipeline_eta([100, None, 120], 2, 1) == 220  # durate illeggibili ignorate
    assert eta.pipeline_eta([100, 120], 0, 2) == 0  # tutto concluso: la pipeline sta chiudendo
    assert eta.pipeline_eta([-50, -10], 3, 1) == 0  # orologio spostato: durate negative come zero


def test_seconds_between_reads_iso_times():
    assert eta.seconds_between("2026-01-01T10:00:00+00:00", "2026-01-01T10:01:30+00:00") == 90
    assert eta.seconds_between("2026-01-01T10:00:00", "2026-01-01T11:00:00+01:00") == 0  # senza fuso = UTC
    assert eta.seconds_between(None, "2026-01-01T10:00:00+00:00") is None
    assert eta.seconds_between("non una data", "2026-01-01T10:00:00+00:00") is None


# ----------------------------------------------------------------------------------------------- ETA (API)


def test_pipeline_eta_uses_items_of_the_current_run(pipe):
    client, core, fake, submit = pipe
    fake.hold("c")
    job_id = submit(names=["a", "b", "c", "d", "e"])
    until(lambda: "c" in fake.calls)
    assert status(client, job_id)["status"] == "running"
    # durate note: a 100 s e b 120 s (mediana 110), c in corso da 30 s, d ed e in attesa
    ref = utcnow()
    core.db.execute("UPDATE jobs SET started_at=? WHERE id=?", (stamp(ref, 1000), job_id))
    for name, start, end in (("a", 600, 500), ("b", 500, 380), ("c", 30, None)):
        core.db.execute("UPDATE pipeline_items SET started_at=?, finished_at=? WHERE job_id=? AND name=?",
                        (stamp(ref, start), stamp(ref, end) if end is not None else None, job_id, name))
    # 3 rimanenti × 110 s − 30 s già spesi da c, su una corsia (parallel=1)
    assert status(client, job_id)["eta_seconds"] == 300
    listed = next(j for j in client.get("/api/jobs?kind=pipeline").json() if j["id"] == job_id)
    assert listed["eta_seconds"] == 300 and client.get(f"/api/jobs/{job_id}").json()["eta_seconds"] == 300
    # un solo elemento concluso con durata nota: nessuna stima
    core.db.execute("UPDATE pipeline_items SET started_at=NULL WHERE job_id=? AND name='b'", (job_id,))
    assert status(client, job_id)["eta_seconds"] is None
    fake.let_go()
    done = wait_job(client, job_id)
    assert done["status"] == "completed" and done["eta_seconds"] is None


def test_pipeline_eta_after_resume_counts_only_reprocessed_items(pipe):
    client, core, fake, submit = pipe
    fake.hold("c")
    job_id = submit(names=["a", "b", "c", "d", "e", "f"])
    cancel_at(client, fake, job_id, "c")  # a e b completati nella prima esecuzione
    fake.let_go()
    fake.hold("e")
    assert client.post(f"/api/jobs/{job_id}/resume").status_code == 200
    until(lambda: "e" in fake.calls)  # c e d rielaborati, e in lavorazione, f in attesa
    ref = utcnow()
    core.db.execute("UPDATE jobs SET started_at=? WHERE id=?", (stamp(ref, 500), job_id))  # inizio della ripresa
    for name, start, end in (("a", 5000, 4000), ("b", 5000, 4000),  # 1000 s, prima esecuzione
                             ("c", 400, 340), ("d", 340, 260), ("e", 20, None)):
        core.db.execute("UPDATE pipeline_items SET started_at=?, finished_at=? WHERE job_id=? AND name=?",
                        (stamp(ref, start), stamp(ref, end) if end is not None else None, job_id, name))
    # mediana dei soli rielaborati: 70 s (con a e b sarebbe 540); (2 × 70 − 20) su una corsia
    assert status(client, job_id)["eta_seconds"] == 120
    fake.let_go()
    assert wait_job(client, job_id)["status"] == "completed"


def test_progress_eta_for_other_jobs(client):
    core = client.app.state.core
    gate = threading.Event()

    def handler(ctx, params):
        ctx.progress(params["p"], "a metà")
        gate.wait(20)
        return {}

    core.jobs.register("demo_eta", handler, pool="io")  # due job insieme anche con un solo processore
    try:
        job_id = core.jobs.submit("demo_eta", {"p": 0.25})["id"]
        slow_id = core.jobs.submit("demo_eta", {"p": 0.02})["id"]
        until(lambda: status(client, job_id)["progress"] == 0.25 and status(client, slow_id)["progress"] == 0.02)
        ref = utcnow()
        core.db.execute("UPDATE jobs SET started_at=? WHERE id=?", (stamp(ref, 5), job_id))
        assert status(client, job_id)["eta_seconds"] is None  # in esecuzione da meno di 10 s
        core.db.execute("UPDATE jobs SET started_at=? WHERE id IN (?,?)", (stamp(ref, 60), job_id, slow_id))
        assert status(client, job_id)["eta_seconds"] == 180  # 60 s × 0,75 / 0,25
        assert status(client, slow_id)["eta_seconds"] is None  # progresso sotto il 5 %
        now = datetime.now(timezone.utc)
        assert core.jobs.estimate({"kind": "demo_eta", "status": "queued", "progress": 0.5,
                                   "started_at": stamp(ref, 60)}, now) is None
        assert core.jobs.estimate({"kind": "demo_eta", "status": "cancelling", "progress": 0.5,
                                   "started_at": stamp(ref, 60)}, now) is None
    finally:
        gate.set()
    assert wait_job(client, job_id)["eta_seconds"] is None


# ----------------------------------------------------------------------------------------------- notifiche


def test_change_feed_wakes_async_waiters_from_other_threads():
    feed = ChangeFeed(size=4)

    async def scenario() -> None:
        start = feed.version
        t0 = time.monotonic()
        threading.Timer(0.2, feed.publish, args=("a", "job")).start()
        assert await feed.wait(start, 5) == start + 1  # svegliato dal thread che pubblica
        assert time.monotonic() - t0 < 2
        t0 = time.monotonic()
        assert await feed.wait(start + 1, 0.2) == start + 1  # nessuna pubblicazione: scade
        assert time.monotonic() - t0 >= 0.15
        feed.publish("b", "log")
        assert await feed.wait(start + 1, 5) == start + 2  # già cambiata: nessuna attesa
        assert feed.waiting == 0

    asyncio.run(scenario())
    version, changes, lost = feed.since(0)
    assert [(c.job_id, c.what) for c in changes] == [("a", "job"), ("b", "log")] and not lost
    for i in range(6):
        feed.publish("c", "item", i)
    current, changes, lost = feed.since(version)
    assert lost and len(changes) == 4 and changes[-1].idx == 5  # il buffer ha scartato i più vecchi
    assert feed.since(current) == (current, [], False)


def test_job_manager_publishes_state_log_and_deletion(tmp_path):
    db = Database(tmp_path / "n.db")
    mgr = JobManager(db, tmp_path / "jobs", workers=1, settings_provider=UserSettings)
    try:
        mgr.register("demo", lambda ctx, p: ctx.log("ciao") or ctx.progress(0.5, "metà") or {})
        start = mgr.events.version
        job_id = mgr.submit("demo", {}, project_id="p1")["id"]

        def published() -> list[str]:
            return [c.what for c in mgr.events.since(start)[1] if c.job_id == job_id]

        until(lambda: "idle" in published())
        mine = published()
        assert mine[0] == "job" and "log" in mine and mine[-1] == "idle"
        assert mine.count("job") >= 4  # in coda, avviato, progresso, completato
        # lo stato finale è pubblicato prima della chiusura dell'esecuzione (log completo)
        assert max(i for i, w in enumerate(mine) if w == "job") < mine.index("idle")
        version = mgr.events.version
        mgr.delete(job_id)
        _, changes, _ = mgr.events.since(version)
        assert ("deleted", job_id, "p1") in [(c.what, c.job_id, c.project_id) for c in changes]
    finally:
        mgr.shutdown()
        db.close()


def test_read_log_whole_lines(tmp_path):
    db = Database(tmp_path / "l.db")
    mgr = JobManager(db, tmp_path / "jobs", workers=1, settings_provider=UserSettings)
    try:
        log = mgr.workdir("0123456789abcdef") / "job.log"
        log.parent.mkdir(parents=True)
        log.write_bytes("uno\ndue\ntr".encode())
        part = mgr.read_log("0123456789abcdef", 0, whole_lines=True)
        assert part == {"text": "uno\ndue\n", "offset": 8, "size": 10, "start": 0}
        assert mgr.read_log("0123456789abcdef", 8, whole_lines=True)["text"] == ""  # riga non ancora completa
        assert mgr.read_log("0123456789abcdef", 8)["text"] == "tr"  # il polling resta com'era
        log.write_bytes("èèèèè".encode())  # riga più lunga del blocco, senza a capo
        part = mgr.read_log("0123456789abcdef", 0, limit=5, whole_lines=True)
        assert part["text"] == "èè" and part["offset"] == 4  # mai a metà di un carattere
    finally:
        mgr.shutdown()
        db.close()


# ----------------------------------------------------------------------------------------------- flusso di un job


def test_job_stream_sends_status_log_and_end(client):
    core = client.app.state.core

    def handler(ctx, params):
        for i in range(1, 6):
            ctx.log(f"riga {i}: ciò che è pronto")
            ctx.progress(i / 5, f"passo {i} di 5")
            time.sleep(0.3)
        return {"ok": True}

    core.jobs.register("demo_sse", handler)
    job_id = core.jobs.submit("demo_sse", {})["id"]
    r = stream(client, f"/api/jobs/{job_id}/events")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/event-stream")
    assert r.headers["cache-control"] == "no-cache" and r.headers["x-accel-buffering"] == "no"
    events = parse_sse(r.text)
    names = [name for name, _ in events if name != ":"]
    assert names[0] == "status" and names[-1] == "end"
    statuses = of(events, "status")
    progress = [s["progress"] for s in statuses]
    assert len(statuses) >= 3 and progress == sorted(progress)  # arrivano man mano, in ordine
    assert all("eta_seconds" in s for s in statuses)
    # lo stato terminale arriva una volta sola, alla fine, insieme a "end"
    assert [s["status"] for s in statuses].count("completed") == 1 and statuses[-1]["status"] == "completed"
    end = of(events, "end")[0]
    assert end["id"] == job_id and end["status"] == "completed" and end["progress"] == 1
    # blocchi di righe intere, contigui, che ricompongono tutto il log (anche le righe finali)
    chunks, offset = of(events, "log"), 0
    for chunk in chunks:
        assert chunk["start"] == offset and chunk["text"].endswith("\n")
        offset = chunk["offset"]
    text = "".join(c["text"] for c in chunks)
    full = client.get(f"/api/jobs/{job_id}/log").json()
    assert text == full["text"] and offset == full["size"]
    assert [line.split("] ", 1)[1] for line in text.splitlines() if "riga" in line] == [
        f"riga {i}: ciò che è pronto" for i in range(1, 6)]
    assert "Job completato" in text


def test_job_stream_log_offset_and_status_only(client):
    core = client.app.state.core
    core.jobs.register("demo_lines", lambda ctx, p: [ctx.log(f"riga {i}") for i in range(5)] and {})
    job_id = core.jobs.submit("demo_lines", {})["id"]
    wait_job(client, job_id)
    until(lambda: not core.jobs.busy(job_id))
    raw = (core.jobs.workdir(job_id) / "job.log").read_bytes()
    cut = raw.index(b"riga 3")
    cut = raw.rindex(b"\n", 0, cut) + 1  # inizio della riga
    events = parse_sse(stream(client, f"/api/jobs/{job_id}/events?offset={cut}").text)
    chunks = of(events, "log")
    assert chunks[0]["start"] == cut and chunks[-1]["offset"] == len(raw)
    assert "".join(c["text"] for c in chunks) == raw[cut:].decode()
    assert [name for name, _ in events if name != ":"] == ["status", "log", "end"]  # job già terminato
    only_status = parse_sse(stream(client, f"/api/jobs/{job_id}/events?log=false").text)
    assert [name for name, _ in only_status if name != ":"] == ["status", "end"]
    assert client.get(f"/api/jobs/{job_id}/events?offset=-1").status_code == 422


def test_job_stream_unknown_job(client):
    r = client.get("/api/jobs/0123456789abcdef/events")
    assert r.status_code == 404 and r.json()["error"]["code"] == "not_found"
    assert client.get("/api/jobs/non-valido!/events").status_code == 400
    assert client.app.state.core.live.slots.used == 0  # nessun posto occupato da una richiesta rifiutata


def test_job_stream_ends_on_terminal_job_and_follows_a_resume(pipe):
    client, core, fake, submit = pipe
    fake.fail.add("b")
    job_id = submit(names=["a", "b"])
    assert wait_job(client, job_id)["status"] == "completed"
    # un job terminato: stato, log e fine subito
    first = parse_sse(stream(client, f"/api/jobs/{job_id}/events", timeout=10).text)
    assert [s["status"] for s in of(first, "status")] == ["completed"] and len(of(first, "end")) == 1
    offset = of(first, "log")[-1]["offset"]
    # la ripresa lo riporta attivo: un nuovo flusso lo segue fino alla nuova fine
    fake.fail.clear()
    fake.hold("b")
    assert client.post(f"/api/jobs/{job_id}/resume", json={"scope": "failed"}).status_code == 200
    until(lambda: fake.calls.count("b") == 2)
    follow = Stream(client, f"/api/jobs/{job_id}/events?offset={offset}")
    until(lambda: core.live.slots.used == 1)
    # il flusso attende le notifiche: ha già inviato la prima lettura dello stato, con "b" ancora ferma
    until(lambda: core.jobs.events.waiting >= 1)
    fake.let_go()
    events = parse_sse(follow.result(20).text)
    statuses = of(events, "status")
    assert statuses[0]["status"] == "running" and statuses[0]["stage"] == "1 di 2 sequenze elaborate"
    assert of(events, "end")[0]["status"] == "completed"
    # il log continua dall'offset del primo flusso: righe della ripresa e della nuova esecuzione, una volta sola
    text = "".join(c["text"] for c in of(events, "log"))
    assert of(events, "log")[0]["start"] == offset
    assert text.count("Ripresa del job") == 1 and text.count("Job completato") == 1
    assert "Job pipeline avviato" in text and text.count("Job pipeline avviato") == 1


def test_stream_limit_and_slot_release(client):
    core = client.app.state.core
    core.jobs.register("ok", lambda ctx, p: {"ok": True})
    job_id = core.jobs.submit("ok", {})["id"]
    wait_job(client, job_id)
    held = [core.live.slots.acquire() for _ in range(core.live.slots.limit)]
    try:
        for url in (f"/api/jobs/{job_id}/events", "/api/jobs/events"):
            r = client.get(url)
            assert r.status_code == 503 and r.json()["error"]["code"] == "too_many_streams"
        held.pop()()  # si libera un posto
        assert stream(client, f"/api/jobs/{job_id}/events").status_code == 200
        until(lambda: core.live.slots.used == len(held))  # il flusso chiuso restituisce il suo posto
    finally:
        for release in held:
            release()
            release()  # il secondo rilascio non conta
    assert core.live.slots.used == 0


# ----------------------------------------------------------------------------------------------- flusso dell'elenco


def stream_caught_up(core) -> None:
    """Attende che l'unico flusso aperto abbia elaborato tutti i cambiamenti pubblicati finora.

    Ogni pubblicazione sveglia e toglie chi attende (`ChangeFeed.publish`), e un flusso si rimette in attesa solo
    con la versione corrente, dopo la passata che segue i cambiamenti rilevanti: un flusso in attesa adesso ha già
    inviato ciò che è stato pubblicato prima. I cambiamenti da attendere devono essere già pubblicati (per un job:
    esecuzione chiusa, `busy` falso, che viene dopo la pubblicazione dello stato finale)."""
    until(lambda: core.jobs.events.waiting >= 1)


def close_after_last_pass(monkeypatch, core, *job_ids: str) -> None:
    """Chiude i flussi dell'elenco dopo che hanno elaborato gli ultimi cambiamenti dei job indicati: la durata
    massima scade alla passata successiva."""
    until(lambda: not any(core.jobs.busy(job_id) for job_id in job_ids))
    stream_caught_up(core)
    monkeypatch.setattr(live, "MAX_STREAM_SECONDS", 0.0)


def test_list_stream_notifies_jobs_and_pipeline_items(pipe, monkeypatch):
    client, core, fake, submit = pipe
    monkeypatch.setattr(live, "MAX_STREAM_SECONDS", 3600.0)
    fake.hold("b")
    listening = Stream(client, "/api/jobs/events")
    until(lambda: core.jobs.events.waiting >= 1)  # flusso aperto, stato iniziale letto
    job_id = submit(names=["a", "b", "c"])
    until(lambda: "b" in fake.calls)
    stream_caught_up(core)  # "running" e "1 di 3" già inviati, con "b" ancora ferma in modellazione
    fake.let_go()
    assert wait_job(client, job_id)["status"] == "completed"
    close_after_last_pass(monkeypatch, core, job_id)
    events = parse_sse(listening.result(20).text)
    assert events[0] == ("ready", {"active": 0}) and events[-1] == ("reconnect", {})
    jobs = [j for j in of(events, "job") if j["id"] == job_id]
    states = [j["status"] for j in jobs]
    assert "running" in states and states[-1] == "completed"
    assert all("eta_seconds" in j for j in jobs)
    assert any(j["stage"] == "1 di 3 sequenze elaborate" for j in jobs)  # progresso della pipeline
    changed = set()
    for event in of(events, "items"):
        assert event["job_id"] == job_id
        changed.update(event["idx"] or [])
    assert changed == {0, 1, 2}  # ogni elemento, anche quello fermo in modellazione
    actives = [a["active"] for a in of(events, "active")]
    assert 1 in actives and actives[-1] == 0


def test_list_stream_project_filter_and_deletions(client, monkeypatch):
    monkeypatch.setattr(live, "MAX_STREAM_SECONDS", 3600.0)
    core = client.app.state.core
    core.jobs.register("ok", lambda ctx, p: {"ok": True})
    mine = client.post("/api/projects", json={"name": "Seguito"}).json()["id"]
    other = client.post("/api/projects", json={"name": "Altro"}).json()["id"]
    old = core.jobs.submit("ok", {}, project_id=mine)["id"]
    wait_job(client, old)
    listening = Stream(client, f"/api/jobs/events?project_id={mine}")
    until(lambda: core.jobs.events.waiting >= 1)
    seen = core.jobs.submit("ok", {}, project_id=mine)["id"]
    hidden = core.jobs.submit("ok", {}, project_id=other)["id"]
    wait_job(client, seen)
    wait_job(client, hidden)
    until(lambda: not core.jobs.busy(old))
    assert client.delete(f"/api/jobs/{old}").status_code == 200  # del progetto seguito: segnalato
    assert client.delete(f"/api/jobs/{hidden}").status_code == 200  # di un altro progetto: no
    close_after_last_pass(monkeypatch, core, seen)
    events = parse_sse(listening.result(15).text)
    ids = {j["id"] for j in of(events, "job")}
    assert seen in ids and hidden not in ids
    assert of(events, "deleted") == [{"id": old}]


def test_list_stream_heartbeat(client, monkeypatch):
    monkeypatch.setattr(live, "MAX_STREAM_SECONDS", 1.5)
    monkeypatch.setattr(live, "HEARTBEAT_SECONDS", 0.3)
    events = parse_sse(stream(client, "/api/jobs/events", timeout=10).text)
    assert events[0][0] == "ready" and (":", "ping") in events and events[-1][0] == "reconnect"


def test_streams_close_when_the_server_stops(client):
    core = client.app.state.core
    gate, reached = gated_job(core)
    job_id = core.jobs.submit("demo_gate", {})["id"]
    try:
        assert reached.wait(5)
        core.server = SimpleNamespace(should_exit=False)
        streams = [Stream(client, f"/api/jobs/{job_id}/events"), Stream(client, "/api/jobs/events")]
        until(lambda: core.live.slots.used == 2)
        core.server.should_exit = True  # segnale di arresto a uvicorn
        for s in streams:
            assert s.result(5).status_code == 200
        assert core.live.slots.used == 0
    finally:
        gate.set()
        core.server = None


# ----------------------------------------------------------------------------------------------- accesso


def test_streams_require_the_access_key(tmp_path, monkeypatch):
    monkeypatch.setattr(live, "MAX_STREAM_SECONDS", 1.0)
    app = create_app(Paths(tmp_path / "data"), frontend_dist=None,
                     auth_config=auth.AuthConfig(enabled=True, key="segreto-12345"))
    with TestClient(app) as c:
        core = c.app.state.core
        core.jobs.register("ok", lambda ctx, p: {"ok": True})
        job_id = core.jobs.submit("ok", {})["id"]
        wait_core(core, job_id)
        for url in (f"/api/jobs/{job_id}/events", "/api/jobs/events"):
            r = c.get(url)
            assert r.status_code == 401 and r.json()["error"]["code"] == "auth_required"
        assert core.live.slots.used == 0
        assert c.post("/api/auth/login", json={"key": "segreto-12345"}).status_code == 200  # cookie
        r = stream(c, f"/api/jobs/{job_id}/events")
        assert r.status_code == 200 and of(parse_sse(r.text), "end")[0]["status"] == "completed"
        assert of(parse_sse(stream(c, "/api/jobs/events").text), "ready") == [{"active": 0}]
        c.post("/api/auth/logout")
        assert c.get(f"/api/jobs/{job_id}/events").status_code == 401
        bearer = stream(c, f"/api/jobs/{job_id}/events", headers={"Authorization": "Bearer segreto-12345"})
        assert bearer.status_code == 200


# ----------------------------------------------------------------------------------------------- server vero


class SseReader:
    """Eventi letti man mano da una risposta httpx in streaming (il timeout di lettura è del client)."""

    def __init__(self, response: httpx.Response) -> None:
        self._lines = response.iter_lines()
        self.events: list[tuple[str, Any]] = []

    def next(self) -> Optional[tuple[str, Any]]:
        name, data = "message", []
        for line in self._lines:
            if line.startswith(":"):
                continue
            if not line:
                if data:
                    event = (name, json.loads("\n".join(data)))
                    self.events.append(event)
                    return event
                name, data = "message", []
                continue
            key, _, value = line.partition(":")
            value = value[1:] if value.startswith(" ") else value
            if key == "event":
                name = value
            elif key == "data":
                data.append(value)
        return None  # flusso chiuso dal server

    def until(self, predicate, timeout: float = 10.0) -> tuple[str, Any]:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            event = self.next()
            assert event is not None, f"flusso chiuso prima dell'evento atteso: {self.events}"
            if predicate(event):
                return event
        raise AssertionError(f"evento atteso non arrivato entro {timeout} s: {self.events}")


@pytest.fixture
def live_server(tmp_path):
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    sock.listen()
    app = create_app(Paths(tmp_path / "data"), frontend_dist=None)
    server = uvicorn.Server(uvicorn.Config(app, log_level="warning"))
    thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True, name="uvicorn-test")
    thread.start()
    until(lambda: server.started, timeout=15)
    core = app.state.core
    core.server = server  # come l'avvio in __main__: i flussi vedono il segnale di arresto
    try:
        yield f"http://127.0.0.1:{sock.getsockname()[1]}", core, server
    finally:
        server.should_exit = True
        thread.join(15)
        sock.close()
    assert not thread.is_alive(), "il server non si è fermato"


def test_events_arrive_in_real_time_on_a_real_server(live_server):
    base, core, server = live_server
    gate, reached = gated_job(core)
    timeout = httpx.Timeout(10.0)
    try:
        with httpx.Client(timeout=timeout) as http, http.stream("GET", f"{base}/api/jobs/events") as feed:
            assert feed.status_code == 200
            jobs = SseReader(feed)
            jobs.until(lambda e: e[0] == "ready")
            job_id = core.jobs.submit("demo_gate", {})["id"]
            with http.stream("GET", f"{base}/api/jobs/{job_id}/events") as r:
                assert r.status_code == 200 and r.headers["content-type"].startswith("text/event-stream")
                events = SseReader(r)
                # stato e prima riga arrivano mentre il job è fermo sul cancello
                events.until(lambda e: e[0] == "status" and e[1]["progress"] == 0.5)
                events.until(lambda e: e[0] == "log" and "prima riga" in e[1]["text"])
                jobs.until(lambda e: e[0] == "job" and e[1]["id"] == job_id and e[1]["status"] == "running")
                assert not gate.is_set() and reached.is_set()
                gate.set()
                end = events.until(lambda e: e[0] == "end")
                assert end[1]["status"] == "completed"
                assert any("ultima riga" in e[1]["text"] for e in events.events if e[0] == "log")
                assert events.next() is None  # dopo "end" il server chiude il flusso
            jobs.until(lambda e: e[0] == "job" and e[1]["id"] == job_id and e[1]["status"] == "completed")
            until(lambda: core.live.slots.used == 1, timeout=5)  # resta aperto solo l'elenco
        # il client ha chiuso la connessione: il server se ne accorge e libera il posto
        until(lambda: core.live.slots.used == 0, timeout=10)
    finally:
        gate.set()


def test_open_streams_do_not_block_the_server_shutdown(live_server):
    base, core, server = live_server
    with httpx.Client(timeout=httpx.Timeout(10.0)) as http, http.stream("GET", f"{base}/api/jobs/events") as feed:
        reader = SseReader(feed)
        reader.until(lambda e: e[0] == "ready")
        t0 = time.monotonic()
        server.should_exit = True  # come Ctrl+C o `docker stop`
        assert reader.next() is None  # il flusso si chiude
        assert time.monotonic() - t0 < 5
