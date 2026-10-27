"""Processi di appoggio per i calcoli Python pesanti degli elementi di una pipeline.

I thread di MakPhi condividono il GIL: un calcolo in Python puro (l'analisi di qualità: Ramachandran, clash, SASA)
non va mai su più core insieme. Con ~1,5 s di Python su ~30 s di calcolo per coppia, oltre 15–20 coppie in parallelo il
processo principale diventa il collo di bottiglia. Qui un pool di processi (avviati con "spawn": niente fork di un
processo con molti thread) esegue quei calcoli fuori dal GIL.

Il pool si usa solo con molti job in parallelo (`processes_for`): con pochi core i calcoli restano nel processo
principale come prima, senza processi in più (ognuno occupa 60–100 MB). La dimensione segue le Impostazioni (job di
calcolo in parallelo, riletti a ogni richiesta). Ogni errore del pool (processo morto, pool chiuso) fa tornare `None`:
chi chiama calcola in casa.
"""

from __future__ import annotations

import multiprocessing
import threading
from concurrent.futures import Future, ProcessPoolExecutor
from typing import Callable, Optional

# Da quanti job di calcolo in parallelo serve il pool, e quanti job per processo di appoggio
MIN_WORKERS = 8
WORKERS_PER_PROCESS = 4

_lock = threading.Lock()
_pool: Optional[ProcessPoolExecutor] = None
_size = 0
_workers: Optional[Callable[[], int]] = None


def processes_for(workers: int) -> int:
    """Processi di appoggio per `workers` job in parallelo: nessuno sotto `MIN_WORKERS`, poi uno ogni quattro."""
    return int(workers) // WORKERS_PER_PROCESS if int(workers) >= MIN_WORKERS else 0


def configure(workers: Optional[Callable[[], int]]) -> None:
    """Fonte dei job di calcolo in parallelo (le Impostazioni); None = pool disattivato."""
    global _workers
    with _lock:
        _workers = workers
    if workers is None:
        shutdown()


def _current() -> Optional[ProcessPoolExecutor]:
    global _pool, _size
    try:
        wanted = processes_for(_workers()) if _workers is not None else 0
    except Exception:
        wanted = 0
    old = None
    with _lock:
        if wanted != _size:
            old, _pool, _size = _pool, None, wanted
        if _size and _pool is None:
            _pool = ProcessPoolExecutor(_size, mp_context=multiprocessing.get_context("spawn"))
        pool = _pool
    if old is not None:
        old.shutdown(wait=False, cancel_futures=True)
    return pool


def submit(fn: Callable, *args) -> Optional[Future]:
    """`fn(*args)` in un processo di appoggio; None se il pool non è attivo o non accetta il lavoro."""
    global _pool
    pool = _current()
    if pool is None:
        return None
    try:
        return pool.submit(fn, *args)
    except Exception:  # pool rotto (un processo morto) o chiuso: si ricrea alla prossima richiesta
        with _lock:
            if _pool is pool:
                _pool = None
        pool.shutdown(wait=False, cancel_futures=True)
        return None


def shutdown() -> None:
    global _pool, _size
    with _lock:
        pool, _pool, _size = _pool, None, 0
    if pool is not None:
        pool.shutdown(wait=False, cancel_futures=True)
