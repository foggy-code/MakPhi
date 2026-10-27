"""Pulizia automatica dei file temporanei e occupazione dello spazio.

Cosa viene eliminato (all'avvio, una volta al giorno e su richiesta):
  - file caricati per l'analisi più vecchi dei giorni impostati (0 = mai);
  - voci della cache più vecchie di CACHE_MAX_AGE_DAYS (oltre il TTL più lungo): risposte dei servizi remoti
    e risultati di ricerche e allineamenti (`cache/compute`, core/cache.py), più i file temporanei di
    scritture atomiche interrotte (es. chiavetta scollegata durante la scrittura);
  - voci della cache dei calcoli usate meno di recente, se la cache supera il tetto delle Impostazioni
    (`compute_cache_max_mb`, core/cache.prune), contate con le altre voci della cache;
  - file temporanei e cartelle di lavoro nello scratch che non appartengono a job attivi;
  - resti di job già eliminati dall'utente (cartelle `jobs/.deleting-<id>`, file che erano in uso: core/jobs.py).
I risultati dei job, la libreria template, i database e le licenze non vengono mai toccati; si eliminano solo i resti
di job già eliminati dall'utente (cartelle `.deleting-…`). Nessun passo elimina cartelle `jobs/<id>` senza riga: dopo un
ripristino del database o una copia parziale della cartella dati sarebbero risultati veri. Le copie di sicurezza del
database (`backups/`) si mostrano nell'occupazione ma non si eliminano mai da sole.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Callable

from ..config import Paths
from ..core import cache as compute_cache
from ..core.db import BACKUP_DIR
from ..core.jobs import ACTIVE, JobManager
from ..core.storage import dir_size, remove_tree

CACHE_MAX_AGE_DAYS = 30
SCRATCH_TMP_MAX_AGE_HOURS = 24
INTERVAL_SECONDS = 24 * 3600


def _older_than(path: Path, seconds: float, now: float) -> bool:
    try:
        return now - path.stat().st_mtime > seconds
    except OSError:
        return False


class Housekeeping:
    def __init__(self, paths: Paths, settings_provider: Callable, jobs: JobManager) -> None:
        self.paths = paths
        self.settings_provider = settings_provider
        self.jobs = jobs
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.last_run: dict | None = None

    def run(self) -> dict:
        now = time.time()
        report = {"uploads": 0, "cache": 0, "scratch": 0, "jobs": 0, "freed_bytes": 0}

        def drop(path: Path, key: str) -> None:
            # un elemento che non si riesce a eliminare (file in uso) non ferma la pulizia e non si conta: si
            # riprova alla prossima
            try:
                size = dir_size(path) if path.is_dir() else (path.stat().st_size if path.exists() else 0)
                if remove_tree(path):
                    report[key] += 1
                    report["freed_bytes"] += size
            except OSError as exc:
                print(f"[MakPhi] pulizia automatica: {path} non eliminato: {exc}")

        try:
            days = int(getattr(self.settings_provider(), "uploads_retention_days", 30) or 0)
            if days > 0 and self.paths.uploads.exists():
                for path in self.paths.uploads.iterdir():
                    if path.is_file() and _older_than(path, days * 86400, now):
                        drop(path, "uploads")
            if self.paths.cache.exists():
                for path in list(self.paths.cache.rglob("*")):
                    if not path.is_file():
                        continue
                    if path.suffix == ".json" and _older_than(path, CACHE_MAX_AGE_DAYS * 86400, now):
                        drop(path, "cache")
                    elif path.name.startswith(".") and path.suffix == ".tmp" \
                            and _older_than(path, SCRATCH_TMP_MAX_AGE_HOURS * 3600, now):
                        drop(path, "cache")  # scrittura atomica interrotta
            if self.paths.compute_cache.exists():  # tetto della cache dei calcoli, dopo la pulizia per età
                removed, freed = compute_cache.prune(compute_cache.max_bytes(self.settings_provider()),
                                                     self.paths.compute_cache)
                report["cache"] += removed
                report["freed_bytes"] += freed
            # tutti i job attivi (nessun limite sul numero), più quelli con l'esecuzione o una ripresa ancora in
            # corso (`busy`: anche partiti dopo questa lettura, o già terminati ma con i file ancora da spostare)
            marks = ",".join("?" * len(ACTIVE))
            active = {r["id"] for r in self.jobs.db.query(f"SELECT id FROM jobs WHERE status IN ({marks})",
                                                          tuple(sorted(ACTIVE)))}
            scratch_jobs = self.paths.scratch / "jobs"
            if scratch_jobs.exists() and self.jobs.scratch_jobs is not None:
                for path in scratch_jobs.iterdir():
                    if path.name not in active and not self.jobs.busy(path.name):
                        drop(path, "scratch")
            if self.paths.scratch_tmp.exists():
                for path in self.paths.scratch_tmp.iterdir():
                    if _older_than(path, SCRATCH_TMP_MAX_AGE_HOURS * 3600, now):
                        drop(path, "scratch")
        except Exception as exc:  # es. cartella illeggibile: i resti dei job eliminati si tolgono comunque
            print(f"[MakPhi] pulizia automatica incompleta: {exc}")
        count, size = self.jobs.purge_tombstones()  # "jobs" = resti di job eliminati
        report["jobs"] += count
        report["freed_bytes"] += size
        self.last_run = {**report, "at": now}
        return self.last_run

    def storage(self) -> dict:
        p = self.paths
        db_files = [p.db, p.db.with_name(p.db.name + "-wal"), p.db.with_name(p.db.name + "-journal")]
        compute_cache = dir_size(p.compute_cache)
        return {
            "jobs": dir_size(p.jobs),
            "templates": dir_size(p.templates),
            "uploads": dir_size(p.uploads),
            # "cache" = risposte dei servizi remoti; la cache dei calcoli, che sta al suo interno, è a parte
            "cache": max(0, dir_size(p.cache) - compute_cache),
            "compute_cache": compute_cache,
            "databases": dir_size(p.databases),
            "licenses": dir_size(p.licenses),
            "database_file": sum(f.stat().st_size for f in db_files if f.exists()),
            "scratch": dir_size(p.scratch),
            "local_cache": dir_size(p.local_cache),
            # copie del database fatte prima di ogni migrazione: solo mostrate, mai eliminate da sole
            "backups": dir_size(p.db.parent / BACKUP_DIR),
        }

    def start(self) -> None:
        def loop() -> None:
            while not self._stop.wait(INTERVAL_SECONDS):
                try:
                    self.run()
                except Exception as exc:  # la pulizia non deve mai fermare il server
                    print(f"[MakPhi] pulizia automatica non riuscita: {exc}")

        self._thread = threading.Thread(target=loop, name="makphi-housekeeping", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
