"""Notifiche in-process dei cambiamenti dei job, per gli aggiornamenti in tempo reale (Server-Sent Events).

Il JobManager e la pipeline pubblicano i cambiamenti dai thread di lavoro: stato o progresso di un job, nuove
righe di log arrivate su disco, elementi di pipeline, esecuzione chiusa, job eliminato. I flussi SSE sono
coroutine nel loop asyncio del server e li attendono con `wait` senza occupare un thread ciascuno: la
pubblicazione le sveglia con `loop.call_soon_threadsafe`.

Ogni cambiamento ha un numero di versione crescente e resta in un buffer circolare: un flusso chiede quelli
successivi all'ultima versione vista (`since`) e scopre se ne ha persi (buffer superato), nel qual caso
rilegge tutto. La notifica è solo un'accelerazione: i flussi rileggono comunque lo stato a intervalli brevi.
"""

from __future__ import annotations

import asyncio
import itertools
import threading
from collections import deque
from dataclasses import dataclass
from typing import Optional

# tipi di cambiamento pubblicati
JOB = "job"          # stato, progresso, fase, errore (riga di `jobs`)
LOG = "log"          # nuove righe del log scritte su disco (flush)
ITEM = "item"        # elemento di pipeline (idx None = più elementi insieme, es. azzerati da una ripresa)
IDLE = "idle"        # esecuzione o preparazione di una ripresa chiusa: log e file sono completi
DELETED = "deleted"  # job eliminato


@dataclass(frozen=True)
class Change:
    version: int
    job_id: str
    what: str
    idx: Optional[int] = None
    # progetto del job, solo per DELETED: la riga non c'è più e un flusso filtrato non potrebbe rileggerlo
    project_id: Optional[str] = None


class ChangeFeed:
    def __init__(self, size: int = 4096) -> None:
        self._lock = threading.Lock()
        self._version = 0
        self._changes: deque[Change] = deque(maxlen=size)
        self._waiters: set[tuple[asyncio.AbstractEventLoop, asyncio.Event]] = set()

    @property
    def version(self) -> int:
        with self._lock:
            return self._version

    def publish(self, job_id: str, what: str, idx: Optional[int] = None, project_id: Optional[str] = None) -> None:
        """Registra un cambiamento e sveglia i flussi in attesa (chiamabile da qualunque thread)."""
        with self._lock:
            self._version += 1
            self._changes.append(Change(self._version, job_id, what, idx, project_id))
            waiters, self._waiters = self._waiters, set()
        for loop, event in waiters:
            try:
                loop.call_soon_threadsafe(event.set)
            except RuntimeError:  # loop già chiuso (arresto del server)
                pass

    def since(self, version: int) -> tuple[int, list[Change], bool]:
        """(versione attuale, cambiamenti successivi a `version`, persi): `persi` se il buffer li ha già scartati."""
        with self._lock:
            current = self._version
            if version >= current:
                return current, [], False
            oldest = self._changes[0].version if self._changes else current + 1
            lost = version + 1 < oldest
            start = max(0, version + 1 - oldest)
            return current, list(itertools.islice(self._changes, start, None)), lost

    async def wait(self, version: int, timeout: float) -> int:
        """Attende una pubblicazione successiva a `version` per al massimo `timeout` secondi; restituisce la
        versione attuale. Non occupa thread: è un `asyncio.Event` svegliato dal thread che pubblica."""
        loop = asyncio.get_running_loop()
        waiter = (loop, asyncio.Event())
        with self._lock:
            if self._version != version:
                return self._version
            self._waiters.add(waiter)
        try:
            await asyncio.wait_for(waiter[1].wait(), max(0.0, timeout))
        except asyncio.TimeoutError:
            pass
        finally:
            with self._lock:
                self._waiters.discard(waiter)
        return self.version

    @property
    def waiting(self) -> int:
        """Flussi in attesa in questo momento (diagnostica e test)."""
        with self._lock:
            return len(self._waiters)
