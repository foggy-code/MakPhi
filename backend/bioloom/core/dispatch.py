"""Coda con priorità dei job: un dispatcher per pool ("cpu", "io") al posto del ThreadPoolExecutor FIFO.

I job in coda stanno in un heap ordinato per (priorità del tipo di job, `created_at`, ordine di creazione) e i
worker del pool prendono sempre il primo che si può avviare. La priorità viene dal tipo (core/jobs.PRIORITY):
ricerche, allineamenti, qualità e QMEAN prima di modellazione e loop, questi prima di pipeline e manutenzione
dei database; a parità, il job creato prima (FIFO, anche per un job ripreso).

- **Risorse esclusive:** un job può dichiarare una risorsa (es. il servizio QMEAN locale, un calcolo alla
  volta). Finché un altro job la tiene resta in coda, con la sua posizione, e il worker passa al successivo:
  il pool non si blocca per gli altri job. La risorsa si calcola al momento della scelta (le Impostazioni
  possono cambiare mentre il job aspetta) e si tiene fino alla chiusura dell'esecuzione.
- **Capacità dinamica:** i job contemporanei del pool si rileggono a ogni scelta (il pool "cpu" segue i job di
  calcolo in parallelo delle Impostazioni); i thread si creano quando servono, fino alla capacità.
- **Stato vero su SQLite:** una voce dell'heap dice solo "prova ad avviare questo job". `JobManager._execute`
  parte soltanto da 'queued' con un passaggio condizionato, quindi una voce rimasta indietro (job annullato in
  coda e ripreso, ripresa in preparazione) non avvia nulla due volte. Un job ha al più una voce valida.
- **Arresto:** `close` svuota la coda; i job ancora 'queued' li marca il `recover` del prossimo avvio.
"""

from __future__ import annotations

import heapq
import itertools
import threading
import traceback
from dataclasses import dataclass, field
from typing import Callable, Optional

# riesame periodico dei worker inattivi: capacità cambiata senza `wake`
IDLE_SECONDS = 5.0


@dataclass(order=True)
class _Entry:
    priority: int
    created_at: str
    created_seq: int  # ordine di creazione a parità di created_at (al secondo): il rowid della riga
    seq: int
    job_id: str = field(compare=False)
    resource: Optional[Callable[[], Optional[str]]] = field(compare=False, default=None)
    blocked: bool = field(compare=False, default=False)  # attesa della risorsa già segnalata
    held: Optional[str] = field(compare=False, default=None)


class Dispatcher:
    def __init__(self, name: str, capacity: Callable[[], int], run: Callable[[str], None],
                 on_blocked: Optional[Callable[[str, str], None]] = None) -> None:
        self.name = name
        self._capacity = capacity
        self._run = run
        self._on_blocked = on_blocked
        self._cond = threading.Condition()
        self._heap: list[_Entry] = []
        self._queued: dict[str, _Entry] = {}  # voce valida di ogni job in coda
        self._held: set[str] = set()
        self._busy = 0
        self._threads: list[threading.Thread] = []
        self._seq = itertools.count()
        self._closed = False

    def capacity(self) -> int:
        try:
            return max(1, int(self._capacity()))
        except Exception:
            return 1

    @property
    def busy(self) -> int:
        with self._cond:
            return self._busy

    # ------------------------------------------------------------------ coda
    def push(self, job_id: str, *, priority: int, created_at: str, created_seq: int = 0,
             resource: Optional[Callable[[], Optional[str]]] = None) -> None:
        """Mette in coda un job; se ha già una voce valida non ne aggiunge un'altra.

        Ordine: `priority`, poi `created_at` e `created_seq` (creazione), infine l'ordine di arrivo in coda.
        """
        with self._cond:
            if self._closed or job_id in self._queued:
                return
            entry = _Entry(int(priority), created_at or "", int(created_seq or 0), next(self._seq), job_id, resource)
            heapq.heappush(self._heap, entry)
            self._queued[job_id] = entry
            self._ensure_threads()
            self._cond.notify_all()

    def discard(self, job_id: str) -> None:
        """Toglie un job dalla coda (annullato mentre aspettava); la voce nell'heap resta, ma non vale più."""
        with self._cond:
            if self._queued.pop(job_id, None) is not None:
                self._cond.notify_all()

    def positions(self) -> dict[str, int]:
        """Posizione (da 1) dei job in coda in questo pool, nell'ordine in cui partiranno."""
        with self._cond:
            valid = sorted(e for e in self._heap if self._queued.get(e.job_id) is e)
        return {e.job_id: n for n, e in enumerate(valid, start=1)}

    def wake(self) -> None:
        """Riesame immediato (capacità cambiata nelle Impostazioni)."""
        with self._cond:
            self._ensure_threads()
            self._cond.notify_all()

    def close(self) -> None:
        with self._cond:
            self._closed = True
            self._heap.clear()
            self._queued.clear()
            self._cond.notify_all()

    # ------------------------------------------------------------------ worker
    def _ensure_threads(self) -> None:
        """Sotto il lock: thread fino alla capacità, solo se c'è lavoro in coda."""
        self._threads = [t for t in self._threads if t.is_alive()]
        wanted = min(self.capacity(), self._busy + len(self._queued))
        for _ in range(wanted - len(self._threads)):
            thread = threading.Thread(target=self._worker, name=f"bioloom-{self.name}-{len(self._threads)}",
                                      daemon=True)
            self._threads.append(thread)
            thread.start()

    def _pick(self) -> tuple[Optional[_Entry], list[tuple[str, str]]]:
        """Sotto il lock: il primo job avviabile (la sua risorsa è libera) e i job appena trovati in attesa di
        una risorsa (da segnalare fuori dal lock)."""
        skipped: list[_Entry] = []
        blocked: list[tuple[str, str]] = []
        found: Optional[_Entry] = None
        while self._heap:
            entry = heapq.heappop(self._heap)
            if self._queued.get(entry.job_id) is not entry:
                continue  # voce non più valida (annullato in coda, già avviato)
            resource = None
            if entry.resource is not None:
                try:
                    resource = entry.resource()
                except Exception:  # impostazioni non leggibili: nessuna esclusione
                    resource = None
            if resource is not None and resource in self._held:
                skipped.append(entry)
                if not entry.blocked:
                    entry.blocked = True
                    blocked.append((entry.job_id, resource))
                continue
            found = entry
            found.held = resource
            break
        for entry in skipped:
            heapq.heappush(self._heap, entry)
        if found is not None:
            del self._queued[found.job_id]
            if found.held is not None:
                self._held.add(found.held)
        return found, blocked

    def _worker(self) -> None:
        while True:
            with self._cond:
                entry, blocked = None, []
                while not self._closed:
                    if self._busy < self.capacity():
                        entry, blocked = self._pick()
                        if entry is not None or blocked:
                            break
                    self._cond.wait(IDLE_SECONDS)
                if self._closed:
                    return
                if entry is not None:
                    self._busy += 1
            for job_id, resource in blocked:
                if self._on_blocked is not None:
                    try:
                        self._on_blocked(job_id, resource)
                    except Exception:  # la segnalazione è cosmetica: la coda continua
                        pass
            if entry is None:
                continue
            try:
                self._run(entry.job_id)
            except Exception:  # _execute gestisce i propri errori: questo è un guasto del gestore
                traceback.print_exc()
            finally:
                with self._cond:
                    self._busy -= 1
                    if entry.held is not None:
                        self._held.discard(entry.held)
                    self._cond.notify_all()
