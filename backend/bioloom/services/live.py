"""Aggiornamenti in tempo reale dei job con Server-Sent Events (SSE).

Ogni evento è `event: <tipo>` + `data: <JSON su una riga>`.

- **Un job** (`GET /api/jobs/{id}/events?offset=&log=`):
  - `status`: riepilogo del job con `eta_seconds`, all'apertura e a ogni cambiamento;
  - `log`: righe complete aggiunte al log, `{"start", "offset", "text"}` in byte del file (il client riparte
    da `offset`, come con `GET /log?offset=`);
  - `end`: job terminato con l'esecuzione chiusa, quindi log completo; subito dopo il flusso si chiude. Lo stato
    terminale arriva solo a esecuzione chiusa, insieme alle ultime righe: chi ricarica il job a quel punto
    trova il log intero. Un job eliminato chiude con `end` e `deleted: true`. Una ripresa riporta il job
    attivo: il client apre un nuovo flusso.
- **Elenco** (`GET /api/jobs/events?project_id=`):
  - `ready` all'apertura, con il numero dei job attivi (`active`);
  - `job` quando un job cambia stato, progresso, fase o stima (riepilogo con `eta_seconds`);
  - `items` quando cambiano elementi di una pipeline (`idx`, null = molti insieme);
  - `active` quando cambia il numero dei job attivi; `deleted` per un job eliminato; `resync` se il flusso ha
    perso cambiamenti (il client ricarica tutto).
- **Comuni:** commento `: ping` dopo 15 s senza eventi, così proxy e browser non chiudono la connessione;
  dopo 30 minuti `reconnect` e chiusura: il client si riconnette subito e l'accesso (cookie o Bearer, che il
  middleware controlla a ogni richiesta, anche di streaming) viene verificato di nuovo.

Costo: un flusso è una coroutine nel loop del server che attende le notifiche del JobManager (`ChangeFeed`),
senza occupare un thread per client; le letture (SQLite, file di log) passano per il thread pool solo il tempo
della lettura. Anche senza notifiche lo stato si rilegge almeno ogni secondo (controllo di riserva). Al massimo
MAX_STREAMS flussi aperti: oltre, 503 e il client resta sul polling. Un flusso finisce quando il client si
disconnette (StreamingResponse annulla il generatore), a fine job, dopo la durata massima o all'arresto del
server (altrimenti uvicorn aspetterebbe la chiusura delle connessioni aperte prima di fermarsi).
"""

from __future__ import annotations

import asyncio
import json
import threading
import time
import weakref
from dataclasses import dataclass, field
from typing import AsyncIterator, Callable, Optional

import anyio.to_thread

from ..core.errors import AppError, NotFound
from ..core.events import DELETED, ITEM, JOB, Change
from ..core.jobs import ACTIVE, TERMINAL, JobManager

POLL_SECONDS = 1.0          # controllo di riserva anche senza notifiche
COALESCE_SECONDS = 0.2      # notifiche ravvicinate raggruppate in un solo invio
HEARTBEAT_SECONDS = 15.0
MAX_STREAM_SECONDS = 30 * 60
END_WAIT_SECONDS = 10.0     # attesa massima della chiusura dell'esecuzione dopo lo stato terminale
MAX_STREAMS = 64
LOG_CHUNK = 256 * 1024
LOG_PASS_BYTES = 1024 * 1024  # log inviato per passata: un log lungo arriva in più passate consecutive
MAX_ITEM_IDS = 500
RETRY_MS = 3000             # attesa di riconnessione suggerita ai client SSE generici
HEARTBEAT = ": ping\n\n"
STATUS_FIELDS = ("status", "progress", "stage", "error", "started_at", "finished_at", "updated_at", "eta_seconds")


def sse(event: str, data) -> str:
    """Un evento SSE con dati JSON su una sola riga (json.dumps rappresenta gli a capo come \\n)."""
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False, separators=(',', ':'))}\n\n"


def signature(job: dict) -> tuple:
    """Ciò che, cambiando, fa reinviare il riepilogo di un job (la stima è già arrotondata)."""
    return tuple(job.get(key) for key in STATUS_FIELDS)


class StreamSlots:
    """Flussi aperti, con un limite. Ogni posto ottenuto si rilascia una volta sola."""

    def __init__(self, limit: int) -> None:
        self.limit = limit
        self._used = 0
        self._lock = threading.Lock()

    @property
    def used(self) -> int:
        with self._lock:
            return self._used

    def acquire(self) -> Callable[[], None]:
        with self._lock:
            if self._used >= self.limit:
                raise AppError(f"Troppi aggiornamenti in tempo reale aperti ({self.limit}): l'interfaccia "
                               "continua con gli aggiornamenti periodici.", code="too_many_streams", status=503)
            self._used += 1
        released = threading.Event()

        def release() -> None:
            with self._lock:
                if not released.is_set():
                    released.set()
                    self._used -= 1

        return release


@dataclass
class _ListState:
    sent: dict[str, tuple] = field(default_factory=dict)  # ultimo riepilogo inviato di ogni job visto
    active_ids: set[str] = field(default_factory=set)
    active: int = -1


class LiveService:
    def __init__(self, jobs: JobManager, max_streams: int = MAX_STREAMS) -> None:
        self.jobs = jobs
        self.slots = StreamSlots(max_streams)
        # arresto in corso: i flussi si chiudono (Core.shutting_down aggiunge il segnale di uvicorn)
        self.stopping: Callable[[], bool] = lambda: jobs.closed

    # ------------------------------------------------------------------ apertura
    def job_events(self, job_id: str, offset: int = 0, with_log: bool = True) -> AsyncIterator[str]:
        """Flusso di un job. Il posto è riservato subito (503 se finiti) e rilasciato alla fine del flusso, o
        quando il generatore viene scartato senza essere mai partito (client andato via prima dell'invio)."""
        release = self.slots.acquire()
        stream = self._job_stream(job_id, max(0, int(offset)), with_log, release)
        weakref.finalize(stream, release)
        return stream

    def list_events(self, project_id: Optional[str] = None) -> AsyncIterator[str]:
        """Flusso dell'elenco dei job (tutti o di un progetto)."""
        release = self.slots.acquire()
        stream = self._list_stream(project_id or None, release)
        weakref.finalize(stream, release)
        return stream

    # ------------------------------------------------------------------ attesa delle notifiche
    async def _collect(self, version: int, relevant: Callable[[Change], bool]) -> tuple[int, list[Change], bool]:
        """Attende al massimo POLL_SECONDS un cambiamento rilevante, poi raccoglie anche quelli arrivati subito
        dopo. Restituisce (versione, cambiamenti rilevanti, persi)."""
        feed = self.jobs.events
        deadline = time.monotonic() + POLL_SECONDS
        found: list[Change] = []
        lost = False
        while True:
            remaining = deadline - time.monotonic()
            if remaining > 0:
                await feed.wait(version, remaining)
            version, changes, gap = feed.since(version)
            lost = lost or gap
            found.extend(c for c in changes if relevant(c))
            if found or lost:
                await asyncio.sleep(COALESCE_SECONDS)
                version, changes, gap = feed.since(version)
                return version, found + [c for c in changes if relevant(c)], lost or gap
            if remaining <= 0:
                return version, found, lost

    # ------------------------------------------------------------------ flusso di un job
    async def _job_stream(self, job_id: str, offset: int, with_log: bool,
                          release: Callable[[], None]) -> AsyncIterator[str]:
        try:
            version = self.jobs.events.version
            opened = last_sent = time.monotonic()
            last_sig: Optional[tuple] = None
            terminal_since: Optional[float] = None
            yield f"retry: {RETRY_MS}\n\n"
            while True:
                force = terminal_since is not None and time.monotonic() - terminal_since >= END_WAIT_SECONDS
                snap = await anyio.to_thread.run_sync(self._job_snapshot, job_id, offset, with_log, force)
                job = snap["job"]
                if job is None:
                    yield sse("end", {"id": job_id, "deleted": True})
                    return
                out = []
                # lo stato terminale si invia solo a esecuzione chiusa (log completo), insieme a "end"
                sig = signature(job)
                if sig != last_sig and (job["status"] not in TERMINAL or snap["final"]):
                    out.append(sse("status", job))
                    last_sig = sig
                for chunk in snap["log"]:
                    out.append(sse("log", chunk))
                    offset = chunk["offset"]
                if snap["final"]:
                    out.append(sse("end", job))
                if out:
                    yield "".join(out)
                    last_sent = time.monotonic()
                if snap["final"]:
                    return
                if job["status"] not in TERMINAL:
                    terminal_since = None
                elif terminal_since is None:
                    terminal_since = time.monotonic()
                if self.stopping():
                    return
                if time.monotonic() - opened >= MAX_STREAM_SECONDS:
                    yield sse("reconnect", {})
                    return
                if not snap["more"]:
                    version, _, _ = await self._collect(version, lambda c: c.job_id == job_id and c.what != ITEM)
                if time.monotonic() - last_sent >= HEARTBEAT_SECONDS:
                    yield HEARTBEAT
                    last_sent = time.monotonic()
        finally:
            release()

    def _job_snapshot(self, job_id: str, offset: int, with_log: bool, force: bool) -> dict:
        """Stato e nuove righe di log di un job (in un thread: SQLite e file).

        `final`: job terminato con l'esecuzione chiusa (o attesa scaduta) e log letto fino in fondo. Lo stato si
        legge prima di `busy`: se a quel punto il job era terminato e l'esecuzione non è più aperta, il log su
        disco è completo.
        """
        try:
            job = self.jobs.get(job_id, include_result=False)
        except NotFound:
            return {"job": None}
        closing = job["status"] in TERMINAL and (force or not self.jobs.busy(job_id))
        self.jobs.with_eta(job)
        chunks: list[dict] = []
        more = False
        if with_log:
            budget = LOG_PASS_BYTES
            while True:
                part = self.jobs.read_log(job_id, offset, LOG_CHUNK, whole_lines=not closing)
                size = part["offset"] - part["start"]
                if size <= 0:
                    break
                chunks.append({"start": part["start"], "offset": part["offset"], "text": part["text"]})
                offset = part["offset"]
                budget -= size
                if offset >= part["size"]:
                    break
                if budget <= 0:
                    more = True
                    break
        return {"job": job, "log": chunks, "more": more, "final": closing and not more}

    # ------------------------------------------------------------------ flusso dell'elenco
    async def _list_stream(self, project_id: Optional[str], release: Callable[[], None]) -> AsyncIterator[str]:
        try:
            version = self.jobs.events.version
            state = _ListState()
            opened = last_sent = time.monotonic()
            await anyio.to_thread.run_sync(self._list_pass, project_id, [], state, False)
            yield f"retry: {RETRY_MS}\n\n" + sse("ready", {"active": state.active})
            while True:
                if self.stopping():
                    return
                if time.monotonic() - opened >= MAX_STREAM_SECONDS:
                    yield sse("reconnect", {})
                    return
                version, changes, lost = await self._collect(version, lambda c: c.what in (JOB, ITEM, DELETED))
                events = await anyio.to_thread.run_sync(self._list_pass, project_id, changes, state, True)
                if lost:
                    events.insert(0, sse("resync", {}))
                if events:
                    yield "".join(events)
                    last_sent = time.monotonic()
                elif time.monotonic() - last_sent >= HEARTBEAT_SECONDS:
                    yield HEARTBEAT
                    last_sent = time.monotonic()
        finally:
            release()

    def _list_pass(self, project_id: Optional[str], changes: list[Change], state: _ListState,
                   emit: bool) -> list[str]:
        """Eventi dell'elenco (in un thread). Si rileggono i job notificati, quelli attivi (controllo di riserva:
        anche un cambiamento non notificato arriva) e quelli che erano attivi alla passata precedente."""
        events: list[str] = []
        changed = {c.job_id for c in changes if c.what == JOB}
        deleted = {c.job_id: c.project_id for c in changes if c.what == DELETED}
        items: dict[str, Optional[set[int]]] = {}
        for c in changes:
            if c.what != ITEM:
                continue
            if c.idx is None:
                items[c.job_id] = None
            elif items.get(c.job_id, set()) is not None:
                items.setdefault(c.job_id, set()).add(c.idx)
        active_rows = self.jobs.list(project_id=project_id, statuses=sorted(ACTIVE), limit=500)
        active_ids = {row["id"] for row in active_rows}
        others = (changed | state.active_ids | set(items)) - active_ids - set(deleted)
        rows = active_rows + (self.jobs.list(project_id=project_id, ids=others, limit=500) if others else [])
        self.jobs.with_eta(rows)
        for row in rows:
            sig = signature(row)
            if state.sent.get(row["id"]) != sig:
                state.sent[row["id"]] = sig
                if emit:
                    events.append(sse("job", row))
        state.active_ids = active_ids
        if len(active_rows) != state.active:
            state.active = len(active_rows)
            if emit:
                events.append(sse("active", {"active": state.active}))
        for job_id, project in sorted(deleted.items()):
            state.sent.pop(job_id, None)
            state.active_ids.discard(job_id)
            if project_id is None or project == project_id:
                events.append(sse("deleted", {"id": job_id}))
        known = {row["id"] for row in rows}
        for job_id, idx in sorted(items.items()):
            if job_id in known:  # esiste ancora ed è del progetto richiesto
                ids = sorted(idx) if idx is not None and len(idx) <= MAX_ITEM_IDS else None
                events.append(sse("items", {"job_id": job_id, "idx": ids}))
        return events if emit else []
