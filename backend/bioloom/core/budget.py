"""Budget di CPU condiviso fra tutti i job: slot occupati dai processi esterni di calcolo.

Ogni job lancia i programmi esterni (MODELLER, PROCHECK, ProSA, Clustal Omega, MAFFT, MUSCLE, BLAST+, MMseqs2,
TM-align/US-align…) con `JobContext.run`, che prima di avviare un processo di calcolo prende uno slot da qui e lo
restituisce quando il processo termina. Senza coordinamento pipeline × elementi in parallelo × strumenti
saturerebbero la CPU (una pipeline con 8 elementi, ognuno con DOPE, PROCHECK e ProSA, più un'analisi lanciata
dall'utente). Gli slot sono tanti quanti i job di calcolo in parallelo delle Impostazioni (core − 1 se automatico)
e la capacità si rilegge a ogni scelta: un cambio dell'impostazione vale subito, senza riavvio.

Regole:
- **Quanti slot:** 1 per un processo a un thread (predefinito). Un programma multi-thread occupa uno slot per
  thread: con una richiesta elastica (`minimum` < `slots`, es. MMseqs2 o MUSCLE 5, il cui comando si costruisce con
  il numero di thread) prende da `minimum` a `slots` slot, quanti ne sono liberi quando tocca a lui, e usa altrettanti
  thread; una richiesta fissa aspetta tutti i suoi slot. Ogni richiesta è limitata alla capacità, quindi si può
  sempre soddisfare.
- **Ordine:** priorità del job (i calcoli interattivi brevi prima della modellazione, questa prima delle pipeline:
  core/jobs.PRIORITY), poi ordine di arrivo. Il primo in quell'ordine parte appena ci sono abbastanza slot liberi;
  chi viene dopo aspetta anche se a lui basterebbero (niente sorpassi: una richiesta fissa di più slot non resta
  indietro per sempre dietro a quelle da uno). I programmi multi-thread usano richieste elastiche da 1 slot minimo,
  così non bloccano la coda in attesa che se ne liberino molti.
- **Nessun deadlock:** gli slot li tengono solo processi in esecuzione, che finiscono da soli, per timeout o per
  annullamento; mai chi aspetta altro. Una pipeline non prende slot mentre aspetta i suoi elementi: li prendono i
  processi degli elementi, e solo mentre girano.
- **Attesa:** si interrompe con l'annullamento del job (`check` solleva JobCancelled; `JobManager.cancel` sveglia
  subito chi aspetta) e con l'arresto del server. Non conta nel timeout del processo, che parte dopo.
"""

from __future__ import annotations

import bisect
import itertools
import threading
from dataclasses import dataclass, field
from typing import Callable, Optional

from .errors import AppError

# riesame periodico anche senza risvegli: capacità cambiata nelle Impostazioni, annullamento
POLL_SECONDS = 1.0


@dataclass(order=True)
class _Request:
    priority: int
    seq: int
    slots: int = field(compare=False)
    minimum: int = field(compare=False, default=1)


class Grant:
    """Slot ottenuti; `release` li restituisce una volta sola (chiamarla di nuovo non fa nulla)."""

    def __init__(self, budget: "CpuBudget", slots: int) -> None:
        self._budget = budget
        self.slots = slots
        self._released = False

    def release(self) -> None:
        with self._budget._cond:
            if self._released:
                return
            self._released = True
            self._budget._used -= self.slots
            self._budget._cond.notify_all()

    def __enter__(self) -> "Grant":
        return self

    def __exit__(self, *_exc) -> None:
        self.release()


class CpuBudget:
    def __init__(self, capacity: Callable[[], int]) -> None:
        self._capacity = capacity
        self._cond = threading.Condition()
        self._used = 0
        self._waiting: list[_Request] = []  # ordinata per (priorità, arrivo): il primo è il prossimo a partire
        self._seq = itertools.count()
        self._closed = False

    @property
    def capacity(self) -> int:
        """Slot totali in questo momento (almeno 1; impostazioni non leggibili = 1)."""
        try:
            return max(1, int(self._capacity()))
        except Exception:
            return 1

    @property
    def used(self) -> int:
        with self._cond:
            return self._used

    @property
    def waiting(self) -> int:
        """Processi in attesa di uno slot (diagnostica e test)."""
        with self._cond:
            return len(self._waiting)

    def snapshot(self) -> dict:
        with self._cond:
            return {"total": self.capacity, "used": self._used, "waiting": len(self._waiting)}

    def slots_for(self, wanted: int) -> int:
        """Thread (e slot) da dare a un programma multi-thread che ne vorrebbe `wanted`: mai oltre la capacità."""
        return max(1, min(int(wanted), self.capacity))

    def acquire(self, slots: int = 1, *, minimum: Optional[int] = None, priority: int = 1,
                check: Optional[Callable[[], None]] = None, on_wait: Optional[Callable[[bool], None]] = None) -> Grant:
        """Prende `slots` slot (limitati alla capacità), aspettando il proprio turno se servono.

        Con `minimum` < `slots` la richiesta è elastica: al suo turno prende quelli liberi, almeno `minimum`
        (`Grant.slots` dice quanti). `check` si chiama durante l'attesa e la interrompe sollevando (annullamento del
        job). `on_wait(True)` e `on_wait(False)` segnalano inizio e fine di un'attesa (la fase "In attesa di CPU
        libera"); se gli slot sono subito disponibili non si chiama.
        """
        wanted = max(1, int(slots))
        request = _Request(priority, next(self._seq), wanted,
                           wanted if minimum is None else max(1, min(int(minimum), wanted)))
        with self._cond:
            if self._closed:
                raise AppError("BioLoom si sta arrestando: il processo non viene avviato.", code="shutting_down")
            bisect.insort(self._waiting, request)
            grant = self._try_take(request)
            if grant is not None:
                return grant
        _notify(on_wait, True)
        try:
            with self._cond:
                while True:
                    if check is not None:
                        check()
                    if self._closed:
                        raise AppError("BioLoom si sta arrestando: il processo non viene avviato.",
                                       code="shutting_down")
                    grant = self._try_take(request)
                    if grant is not None:
                        return grant
                    self._cond.wait(POLL_SECONDS)
        except BaseException:
            with self._cond:
                self._waiting = [r for r in self._waiting if r is not request]
                self._cond.notify_all()  # chi era dietro può essere diventato il primo
            raise
        finally:
            _notify(on_wait, False)

    def _try_take(self, request: _Request) -> Optional[Grant]:
        """Sotto il lock: slot alla richiesta se è la prima in ordine e ce ne sono abbastanza."""
        capacity = self.capacity
        want = min(request.slots, capacity)  # capacità ridotta nel frattempo: la richiesta resta soddisfacibile
        need = min(request.minimum, want)
        free = capacity - self._used
        if not self._waiting or self._waiting[0] is not request or free < need:
            return None
        take = min(want, free)
        self._waiting.pop(0)
        self._used += take
        self._cond.notify_all()  # anche la richiesta successiva può trovare posto
        return Grant(self, take)

    def wake(self) -> None:
        """Riesame immediato delle attese (annullamento di un job, capacità cambiata)."""
        with self._cond:
            self._cond.notify_all()

    def close(self) -> None:
        """Arresto: chi aspetta esce con errore e nessun nuovo processo parte."""
        with self._cond:
            self._closed = True
            self._cond.notify_all()


def _notify(on_wait: Optional[Callable[[bool], None]], waiting: bool) -> None:
    """Segnala l'attesa; un errore nel mostrarla (es. database chiuso all'arresto) non deve perdere slot."""
    if on_wait is None:
        return
    try:
        on_wait(waiting)
    except Exception:
        pass
