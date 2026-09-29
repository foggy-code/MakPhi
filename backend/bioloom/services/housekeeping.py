"""Pulizia automatica dei file temporanei e occupazione dello spazio.

Cosa viene eliminato (all'avvio, una volta al giorno e su richiesta):
  - file caricati per l'analisi più vecchi dei giorni impostati (0 = mai);
  - risposte dei servizi remoti in cache più vecchie di CACHE_MAX_AGE_DAYS (oltre il TTL più lungo);
  - file temporanei e cartelle di lavoro nello scratch che non appartengono a job attivi.
I risultati dei job, la libreria template, i database e le licenze non vengono mai toccati.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Callable

from ..config import Paths
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
        report = {"uploads": 0, "cache": 0, "scratch": 0, "freed_bytes": 0}

        def drop(path: Path, key: str) -> None:
            size = dir_size(path) if path.is_dir() else (path.stat().st_size if path.exists() else 0)
            remove_tree(path)
            report[key] += 1
            report["freed_bytes"] += size

        days = int(getattr(self.settings_provider(), "uploads_retention_days", 30) or 0)
        if days > 0 and self.paths.uploads.exists():
            for path in self.paths.uploads.iterdir():
                if path.is_file() and _older_than(path, days * 86400, now):
                    drop(path, "uploads")
        if self.paths.cache.exists():
            for path in self.paths.cache.rglob("*.json"):
                if _older_than(path, CACHE_MAX_AGE_DAYS * 86400, now):
                    drop(path, "cache")
        active = {j["id"] for j in self.jobs.list(statuses=sorted(ACTIVE), limit=500)}
        scratch_jobs = self.paths.scratch / "jobs"
        if scratch_jobs.exists() and self.jobs.scratch_jobs is not None:
            for path in scratch_jobs.iterdir():
                if path.name not in active:
                    drop(path, "scratch")
        if self.paths.scratch_tmp.exists():
            for path in self.paths.scratch_tmp.iterdir():
                if _older_than(path, SCRATCH_TMP_MAX_AGE_HOURS * 3600, now):
                    drop(path, "scratch")
        self.last_run = {**report, "at": now}
        return self.last_run

    def storage(self) -> dict:
        p = self.paths
        db_files = [p.db, p.db.with_name(p.db.name + "-wal"), p.db.with_name(p.db.name + "-journal")]
        return {
            "jobs": dir_size(p.jobs),
            "templates": dir_size(p.templates),
            "uploads": dir_size(p.uploads),
            "cache": dir_size(p.cache),
            "databases": dir_size(p.databases),
            "licenses": dir_size(p.licenses),
            "database_file": sum(f.stat().st_size for f in db_files if f.exists()),
            "scratch": dir_size(p.scratch),
            "local_cache": dir_size(p.local_cache),
        }

    def start(self) -> None:
        def loop() -> None:
            while not self._stop.wait(INTERVAL_SECONDS):
                try:
                    self.run()
                except Exception as exc:  # la pulizia non deve mai fermare il server
                    print(f"[BioLoom] pulizia automatica non riuscita: {exc}")

        self._thread = threading.Thread(target=loop, name="bioloom-housekeeping", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
