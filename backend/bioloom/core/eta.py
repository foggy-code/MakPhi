"""Stima del tempo residuo dei job (ETA): logica pura, senza database né orologio.

Due stimatori, scelti dal tipo di job (`JobManager.estimate`):

- **Pipeline** (elementi indipendenti eseguiti su `parallel` corsie): lavoro residuo = mediana della durata
  (finished_at − started_at) degli elementi conclusi nell'esecuzione corrente × elementi rimanenti, meno il
  tempo già speso dagli elementi in corso (ciascuno al massimo per la mediana); ETA = lavoro residuo / corsie
  occupate, cioè min(parallelismo effettivo, elementi rimanenti).
  - La mediana e non la media: le durate hanno code lunghe (una ricerca NCBI di venti minuti, un elemento che
    fallisce in un secondo) e la mediana ha punto di rottura del 50 % (Huber e Ronchetti, *Robust Statistics*,
    2ª ed., Wiley 2009, cap. 1), quindi pochi elementi anomali non spostano la stima.
  - Lavoro residuo / processori è il limite inferiore classico del tempo di completamento di compiti
    indipendenti su processori identici (Graham, "Bounds on multiprocessing timing anomalies", SIAM J. Appl.
    Math. 17, 1969); con elementi di durata simile è anche il valore atteso. Togliere il tempo già speso da
    chi è in corso fa scendere la stima con continuità invece che a scatti, un elemento alla volta.
  - Nessuna stima con meno di 2 elementi conclusi: un solo campione non dice nulla sulla dispersione.
- **Altri job** (progresso p ∈ [0, 1] dichiarato dall'handler): estrapolazione lineare a velocità costante,
  t_residuo = t_trascorso × (1 − p) / p. Solo con p ≥ 0,05 e almeno 10 s di esecuzione: all'inizio le fasi
  di avvio (preparazione dei template, avvio di MODELLER) pesano troppo e la stima oscillerebbe.

I valori sono arrotondati con un passo che cresce con la durata (5 s sotto il minuto, 10 s sotto i 10 minuti,
1 min sotto l'ora, 5 min oltre), così la stima non cambia a ogni lettura e i flussi di eventi la reinviano solo
quando cambia davvero. Non sono mai negativi.
"""

from __future__ import annotations

import math
import statistics
from datetime import datetime, timezone
from typing import Iterable, Optional

# almeno 2 elementi conclusi nell'esecuzione corrente per stimare una pipeline
MIN_PIPELINE_SAMPLES = 2
# stima dal progresso solo da p >= 5 % e dopo 10 s di esecuzione
MIN_PROGRESS = 0.05
MIN_ELAPSED_SECONDS = 10.0
# (limite superiore escluso, passo di arrotondamento) in secondi; oltre l'ultimo limite vale LONG_STEP
ROUNDING_STEPS = ((60.0, 5), (600.0, 10), (3600.0, 60))
LONG_STEP = 300


def round_eta(seconds: float) -> int:
    """Secondi arrotondati al passo della loro grandezza (metà per eccesso); mai negativi."""
    value = max(0.0, float(seconds))
    step = next((s for limit, s in ROUNDING_STEPS if value < limit), LONG_STEP)
    return int(math.floor(value / step + 0.5) * step)


def progress_eta(elapsed: Optional[float], progress: Optional[float]) -> Optional[int]:
    """ETA dal progresso: t × (1 − p) / p; None sotto il 5 % o nei primi 10 s (stima instabile)."""
    if elapsed is None or progress is None:
        return None
    elapsed, progress = float(elapsed), float(progress)
    if not (math.isfinite(elapsed) and math.isfinite(progress)):
        return None
    if elapsed < MIN_ELAPSED_SECONDS or progress < MIN_PROGRESS:
        return None
    p = min(1.0, progress)
    return round_eta(elapsed * (1.0 - p) / p)


def pipeline_eta(durations: Iterable[Optional[float]], remaining: int, parallel: int,
                 running_elapsed: Iterable[Optional[float]] = ()) -> Optional[int]:
    """ETA di una pipeline.

    `durations`: durate (s) degli elementi conclusi nell'esecuzione corrente; `remaining`: elementi ancora da
    concludere (in attesa o in corso); `parallel`: parallelismo effettivo della pipeline; `running_elapsed`:
    da quanti secondi sono in corso gli elementi avviati e non conclusi. None con meno di 2 durate valide.
    """
    samples = [max(0.0, float(d)) for d in durations if d is not None and math.isfinite(float(d))]
    if len(samples) < MIN_PIPELINE_SAMPLES or remaining < 0:
        return None
    if remaining == 0:
        return 0  # tutti gli elementi conclusi: la pipeline sta chiudendo
    typical = statistics.median(samples)
    spent = [min(max(0.0, float(e)), typical) for e in running_elapsed
             if e is not None and math.isfinite(float(e))][:remaining]
    work = typical * remaining - sum(spent)
    lanes = max(1, min(int(parallel), remaining))
    return round_eta(max(0.0, work) / lanes)


def parse_time(value) -> Optional[datetime]:
    """Istante ISO 8601 (come `db.now_iso`) o datetime; senza fuso è UTC. None se assente o illeggibile."""
    if isinstance(value, datetime):
        moment = value
    elif isinstance(value, str) and value:
        try:
            moment = datetime.fromisoformat(value)
        except ValueError:
            return None
    else:
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def seconds_between(start, end) -> Optional[float]:
    """Secondi da `start` a `end` (ISO o datetime); None se uno dei due manca."""
    a, b = parse_time(start), parse_time(end)
    if a is None or b is None:
        return None
    return (b - a).total_seconds()
