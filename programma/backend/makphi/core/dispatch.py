"""Coda con priorità dei job: un dispatcher per pool ("cpu", "io") al posto del ThreadPoolExecutor FIFO.

I worker del pool prendono sempre il primo job avviabile nell'ordine (priorità effettiva, `created_at`, ordine di
creazione, ordine di arrivo in coda). La priorità viene dal tipo (core/jobs.PRIORITY): ricerche, allineamenti,
qualità e QMEAN prima di modellazione e loop, questi prima di pipeline e manutenzione dei database; a parità, il job
creato prima (FIFO, anche per un job ripreso).

- **Invecchiamento:** la priorità effettiva di un job in coda migliora di un livello ogni `AGING_SECONDS` di attesa,
  fino a 0 (la più alta). Un flusso continuo di job interattivi non lascia quindi ferma per sempre una pipeline
  (priorità 2): al più tardi dopo 2 × `AGING_SECONDS` di attesa conta solo la creazione. A parità di priorità
  effettiva l'ordine resta quello di creazione, e un job della stessa priorità arrivato dopo non sorpassa mai chi
  aspetta da prima (ha atteso di meno, quindi non è invecchiato di più).
- **Risorse esclusive:** un job può dichiarare una risorsa (es. il servizio QMEAN locale, un calcolo alla
  volta). Finché un altro job la tiene resta in coda, con la sua posizione, e il worker passa al successivo:
  il pool non si blocca per gli altri job. Una risorsa può avere più posti (`resource_capacity`, es. QMEAN locale
  con più calcoli contemporanei sul server): il job aspetta solo quando sono tutti occupati. La risorsa si calcola al momento della scelta (le Impostazioni
  possono cambiare mentre il job aspetta) e si tiene fino alla chiusura dell'esecuzione, salvo che il job la
  restituisca prima con `release`, passato all'esecuzione (QMEAN: finita la parte locale, prima del fallback remoto).
- **Capacità dinamica:** i job contemporanei del pool si rileggono a ogni scelta (il pool "cpu" segue i job di
  calcolo in parallelo delle Impostazioni); i thread si creano quando servono, fino alla capacità.
- **Code lunghe:** un thread inattivo riguarda la coda solo dopo un evento che può rendere avviabile un job (job in
  coda, risorsa o posto liberato, `wake`) o allo scadere di `IDLE_SECONDS`, non a ogni risveglio: con centinaia di
  migliaia di QMEAN in coda ordinarla costa decimi di secondo.
- **Stato vero su SQLite:** una voce della coda dice solo "prova ad avviare questo job". `JobManager._execute`
  parte soltanto da 'queued' con un passaggio condizionato, quindi una voce rimasta indietro (job annullato in
  coda e ripreso, ripresa in preparazione) non avvia nulla due volte. Un job ha al più una voce in coda.
- **Arresto:** `close` svuota la coda; i job ancora 'queued' li marca il `recover` del prossimo avvio.
"""

from __future__ import annotations

import itertools
import threading
import time
import traceback
from dataclasses import dataclass, field
from functools import partial
from typing import Callable, Optional

# riesame periodico dei worker inattivi: capacità cambiata senza `wake`
IDLE_SECONDS = 5.0
# Invecchiamento: un livello di priorità ogni 5 minuti di attesa in coda. È più lungo di un calcolo interattivo tipico
# (ricerca, allineamento, analisi di qualità: da secondi a pochi minuti), quindi le richieste di chi è davanti allo
# schermo passano comunque davanti a modellazioni e pipeline; è abbastanza breve che, anche con un flusso continuo di
# richieste interattive, una modellazione parta dopo al più 5 minuti di attesa e una pipeline dopo al più 10 (più i
# job creati prima di lei).
AGING_SECONDS = 300.0


def effective_priority(priority: int, waited: float, aging: float) -> int:
    """Priorità dopo `waited` secondi di attesa: un livello in meno ogni `aging` secondi, mai sotto 0 (una priorità
    già 0 resta com'è). `aging` <= 0 = nessun invecchiamento."""
    if aging <= 0 or priority <= 0 or waited <= 0:
        return priority
    return max(0, priority - int(waited // aging))


@dataclass(eq=False)
class _Entry:
    priority: int
    created_at: str
    created_seq: int  # ordine di creazione a parità di created_at (al secondo): il rowid della riga
    seq: int
    job_id: str
    since: float  # ingresso in coda (orologio del dispatcher): da qui conta l'invecchiamento
    resource: Optional[Callable[[], Optional[str]]] = field(default=None)
    blocked: bool = field(default=False)  # attesa della risorsa già segnalata
    held: Optional[str] = field(default=None)

    def rank(self, now: float, aging: float) -> tuple:
        """Posizione nell'ordine di partenza adesso: priorità effettiva (invecchiata), poi creazione e arrivo."""
        return (effective_priority(self.priority, now - self.since, aging), self.created_at, self.created_seq,
                self.seq)


class Dispatcher:
    def __init__(self, name: str, capacity: Callable[[], int], run: Callable[[str, Callable[[], None]], None],
                 on_blocked: Optional[Callable[[str, str], None]] = None, *, aging: float = AGING_SECONDS,
                 clock: Callable[[], float] = time.monotonic,
                 resource_capacity: Optional[Callable[[str], int]] = None) -> None:
        """`run(job_id, release)` esegue il job; con `release()` il job restituisce la sua risorsa esclusiva prima
        di finire (una volta sola: dopo non fa nulla). `aging` e `clock` servono ai test dell'invecchiamento.
        `resource_capacity(nome)`: posti della risorsa (1 se manca); letto a ogni scelta, senza I/O (sotto il lock)."""
        self.name = name
        self._capacity = capacity
        self._run = run
        self._on_blocked = on_blocked
        self._aging = float(aging)
        self._clock = clock
        self._cond = threading.Condition()
        self._queued: dict[str, _Entry] = {}  # voce di ogni job in coda
        self._resource_capacity = resource_capacity
        self._held: dict[str, int] = {}  # posti occupati di ogni risorsa
        self._busy = 0
        # Riesame solo se qualcosa è cambiato: `_version` cresce a ogni evento che può rendere avviabile un job (job
        # in coda, risorsa o posto liberato, `wake`); `_stale` è la versione dell'ultima scelta senza nulla da
        # avviare. Con centinaia di migliaia di job in coda ordinare costa decimi di secondo, e senza questo ogni
        # fine di un job lo farebbe rifare a tutti i thread inattivi del pool.
        self._version = 0
        self._stale = -1
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
        """Mette in coda un job; se ha già una voce in coda non ne aggiunge un'altra.

        Ordine: `priority` (invecchiata con l'attesa), poi `created_at` e `created_seq` (creazione), infine l'ordine
        di arrivo in coda.
        """
        with self._cond:
            if self._closed or job_id in self._queued:
                return
            self._queued[job_id] = _Entry(int(priority), created_at or "", int(created_seq or 0), next(self._seq),
                                          job_id, self._clock(), resource)
            self._version += 1
            self._ensure_threads()
            self._cond.notify_all()

    def discard(self, job_id: str) -> None:
        """Toglie un job dalla coda (annullato mentre aspettava)."""
        with self._cond:
            if self._queued.pop(job_id, None) is not None:
                self._cond.notify_all()

    def _ordered(self) -> list[_Entry]:
        """Sotto il lock: i job in coda nell'ordine in cui partirebbero adesso (l'invecchiamento cambia col tempo)."""
        now = self._clock()
        return sorted(self._queued.values(), key=lambda e: e.rank(now, self._aging))

    def positions(self) -> dict[str, int]:
        """Posizione (da 1) dei job in coda in questo pool, nell'ordine in cui partiranno."""
        with self._cond:
            ordered = self._ordered()
        return {e.job_id: n for n, e in enumerate(ordered, start=1)}

    def wake(self) -> None:
        """Riesame immediato (capacità cambiata nelle Impostazioni)."""
        with self._cond:
            self._version += 1
            self._ensure_threads()
            self._cond.notify_all()

    def close(self) -> None:
        with self._cond:
            self._closed = True
            self._queued.clear()
            self._cond.notify_all()

    # ------------------------------------------------------------------ worker
    def _ensure_threads(self) -> None:
        """Sotto il lock: thread fino alla capacità, solo se c'è lavoro in coda."""
        self._threads = [t for t in self._threads if t.is_alive()]
        wanted = min(self.capacity(), self._busy + len(self._queued))
        for _ in range(wanted - len(self._threads)):
            thread = threading.Thread(target=self._worker, name=f"makphi-{self.name}-{len(self._threads)}",
                                      daemon=True)
            self._threads.append(thread)
            thread.start()

    def _pick(self) -> tuple[Optional[_Entry], list[tuple[str, str]]]:
        """Sotto il lock: il primo job avviabile (la sua risorsa è libera) e i job appena trovati in attesa di
        una risorsa (da segnalare fuori dal lock)."""
        blocked: list[tuple[str, str]] = []
        for entry in self._ordered():
            resource = None
            if entry.resource is not None:
                try:
                    resource = entry.resource()
                except Exception:  # impostazioni non leggibili: nessuna esclusione
                    resource = None
            if resource is not None and self._held.get(resource, 0) >= self._slots(resource):
                if not entry.blocked:
                    entry.blocked = True
                    blocked.append((entry.job_id, resource))
                continue
            entry.held = resource
            del self._queued[entry.job_id]
            if resource is not None:
                self._held[resource] = self._held.get(resource, 0) + 1
            return entry, blocked
        return None, blocked

    def _slots(self, resource: str) -> int:
        if self._resource_capacity is None:
            return 1
        try:
            return max(1, int(self._resource_capacity(resource)))
        except Exception:  # come la risorsa illeggibile: il valore prudente
            return 1

    def _release(self, entry: _Entry) -> None:
        """Restituisce la risorsa esclusiva del job, una volta sola. `held` torna None: la chiusura dell'esecuzione
        (o una seconda chiamata) non libera la stessa risorsa presa nel frattempo da un altro job."""
        with self._cond:
            if entry.held is not None:
                left = self._held.get(entry.held, 0) - 1
                if left > 0:
                    self._held[entry.held] = left
                else:
                    self._held.pop(entry.held, None)
                entry.held = None
                self._version += 1
                self._cond.notify_all()

    def _worker(self) -> None:
        while True:
            with self._cond:
                entry, blocked = None, []
                fresh = True  # prima scelta del thread, o riesame periodico: si guarda la coda comunque
                while not self._closed:
                    if self._busy < self.capacity() and (fresh or self._version != self._stale):
                        entry, blocked = self._pick()
                        if entry is not None or blocked:
                            break
                        self._stale = self._version
                    # allo scadere un riesame completo: capacità di una risorsa o Impostazioni cambiate senza `wake`
                    fresh = not self._cond.wait(IDLE_SECONDS)
                if self._closed:
                    return
                if entry is not None:
                    self._busy += 1
                    # la capacità può essere cresciuta senza `wake` (posti del QMEAN locale letti dal primo calcolo,
                    # con la coda già piena): i thread che mancano partono adesso
                    self._ensure_threads()
            for job_id, resource in blocked:
                if self._on_blocked is not None:
                    try:
                        self._on_blocked(job_id, resource)
                    except Exception:  # la segnalazione è cosmetica: la coda continua
                        pass
            if entry is None:
                continue
            try:
                self._run(entry.job_id, partial(self._release, entry))
            except Exception:  # _execute gestisce i propri errori: questo è un guasto del gestore
                traceback.print_exc()
            finally:
                self._release(entry)
                with self._cond:
                    self._busy -= 1
                    self._version += 1
                    self._cond.notify_all()
