"""Passi indipendenti di un calcolo eseguiti in parallelo su un piccolo pool locale di thread.

Lo usano l'analisi di qualità (`engines/quality.analyze`: DOPE, mkdssp, PROCHECK, ProSA, Ramachandran, clash, SASA)
e l'analisi di più modelli dello stesso job (`ComputeService.run_modeling`, qualità "all").

- **Ordine deterministico:** i risultati si leggono per chiave (`result`, `outcome`) nell'ordine scelto dal
  chiamante, qualunque sia l'ordine in cui i passi finiscono: il risultato del calcolo non dipende dai tempi.
- **Annullamento:** mentre aspetta un passo il chiamante controlla `check` (di solito `ctx.check`) ogni
  `POLL_SECONDS`, e ogni passo lo controlla prima di partire: dopo l'annullamento nessun passo nuovo parte. `close`
  (anche all'uscita dal blocco `with`) toglie dalla coda i passi non partiti e aspetta quelli in corso, così nessun
  thread scrive più nella cartella del job dopo la fine del calcolo. I processi esterni dei passi li termina il
  JobManager all'annullamento: sono tutti lanciati con `JobContext.run`.
- **Errori:** l'eccezione di un passo resta al passo e arriva al chiamante quando ne chiede l'esito; gli altri passi
  continuano. `JobCancelled` si solleva sempre, anche da `outcome`.
- **Nessun limite globale:** il pool appartiene al singolo calcolo e serve solo ad aspettare più cose insieme. I
  processi pesanti li governa il budget di CPU del JobManager, perché passano da `JobContext.run`.
- **Modalità seriale** (`parallel=False`): un passo gira nel thread del chiamante quando se ne chiede l'esito, cioè
  nell'ordine della versione seriale. Serve ai test di equivalenza e a eseguire senza thread.
"""

from __future__ import annotations

import time
from concurrent.futures import Future, ThreadPoolExecutor, wait
from dataclasses import dataclass
from typing import Any, Callable, Optional

from .jobs import JobCancelled

POLL_SECONDS = 0.1


@dataclass
class Outcome:
    """Esito di un passo: valore oppure eccezione, e durata del passo (s)."""
    value: Any = None
    error: Optional[BaseException] = None
    seconds: float = 0.0


class Steps:
    def __init__(self, *, check: Optional[Callable[[], None]] = None, parallel: bool = True, workers: int = 4,
                 name: str = "passi") -> None:
        self._check = check
        self.parallel = bool(parallel) and workers > 1
        self._workers = max(1, int(workers))
        self._name = name
        self._pool: Optional[ThreadPoolExecutor] = None
        self._futures: dict[str, Future] = {}
        self._deferred: dict[str, Callable[[], Any]] = {}  # modalità seriale: eseguiti alla richiesta dell'esito
        self._outcomes: dict[str, Outcome] = {}

    def __enter__(self) -> "Steps":
        return self

    def __exit__(self, *_exc) -> None:
        self.close()

    @property
    def timings(self) -> dict[str, float]:
        """Durata (s) dei passi di cui si è letto l'esito."""
        return {key: out.seconds for key, out in self._outcomes.items()}

    def started(self, key: str) -> bool:
        return key in self._futures or key in self._deferred or key in self._outcomes

    def start(self, key: str, fn: Callable[[], Any]) -> None:
        """Avvia il passo `key` (in modalità seriale lo prenota per quando se ne chiederà l'esito)."""
        if self.started(key):
            raise ValueError(f"Passo già avviato: {key}")
        if not self.parallel:
            self._deferred[key] = fn
            return
        if self._pool is None:
            self._pool = ThreadPoolExecutor(max_workers=self._workers, thread_name_prefix=f"makphi-{self._name}")
        self._futures[key] = self._pool.submit(self._run, fn)

    def _run(self, fn: Callable[[], Any]) -> Outcome:
        start = time.perf_counter()
        try:
            if self._check is not None:
                self._check()  # annullato mentre il passo era in coda: non parte
            value = fn()
        except BaseException as exc:  # consegnata al chiamante con l'esito del passo
            return Outcome(error=exc, seconds=time.perf_counter() - start)
        return Outcome(value=value, seconds=time.perf_counter() - start)

    def outcome(self, key: str) -> Outcome:
        """Esito del passo, aspettando che finisca; l'annullamento del job (anche di un passo) si solleva qui."""
        out = self._outcomes.get(key)
        if out is None:
            if key in self._deferred:
                out = self._run(self._deferred.pop(key))
            else:
                future = self._futures[key]
                while not wait([future], timeout=POLL_SECONDS).done:
                    if self._check is not None:
                        self._check()
                out = future.result()
            self._outcomes[key] = out
        if isinstance(out.error, JobCancelled):
            raise out.error
        return out

    def result(self, key: str) -> Any:
        """Valore del passo; la sua eccezione, se è fallito, si solleva qui."""
        out = self.outcome(key)
        if out.error is not None:
            raise out.error
        return out.value

    def close(self) -> None:
        """Toglie dalla coda i passi non ancora partiti e aspetta quelli in corso."""
        self._deferred.clear()
        pool, self._pool = self._pool, None
        if pool is not None:
            pool.shutdown(wait=True, cancel_futures=True)
