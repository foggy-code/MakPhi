"""Contenitore dei servizi applicativi, creato una volta all'avvio del server."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from ..config import Paths, SettingsStore
from ..engines import remote, search, tools
from ..services.compute import ComputeService
from ..services.databases import DatabaseService
from ..services.housekeeping import Housekeeping
from ..services.licenses import LicenseService
from ..services.pipeline import PipelineService
from ..services.projects import ProjectService
from ..services.selftest import SelfTestService
from ..services.templates import TemplateLibrary
from . import cache
from .db import Database
from .errors import Conflict
from .jobs import ACTIVE, JobManager


@dataclass
class Core:
    paths: Paths
    settings: SettingsStore
    db: Database
    jobs: JobManager
    library: TemplateLibrary
    projects: ProjectService
    compute: ComputeService
    pipeline: PipelineService
    housekeeping: Housekeeping
    licenses: LicenseService
    databases: DatabaseService
    started_at: float = field(default_factory=time.time)
    server: Any = None

    def delete_project(self, project_id: str) -> None:
        self.projects.get(project_id)
        jobs = self.jobs.list(project_id=project_id, limit=500)
        if any(j["status"] in ACTIVE for j in jobs):
            raise Conflict("Il progetto ha job ancora in esecuzione: annullali prima di eliminarlo.")
        for job in jobs:
            self.jobs.delete(job["id"])
        self.projects.delete(project_id)

    def close(self) -> None:
        self.housekeeping.stop()
        self.jobs.shutdown()
        self.db.close()


def build_core(paths: Paths) -> Core:
    paths.ensure()
    settings = SettingsStore(paths.settings_file)
    tools.configure(paths.tools)
    licenses = LicenseService(paths.licenses, settings.get)
    licenses.apply()
    remote.configure(paths.cache)
    cache.configure(paths.compute_cache)
    search.configure(paths.local_cache)
    db = Database(paths.db)
    jobs = JobManager(db, paths.jobs, settings.get().effective_workers(), settings.get,
                      scratch_dir=paths.scratch)
    recovered = jobs.recover()
    library = TemplateLibrary(paths.templates)
    projects = ProjectService(db)
    compute = ComputeService(jobs, library, paths.uploads, paths.databases)
    pipeline = PipelineService(db, jobs, compute)
    databases = DatabaseService(jobs, paths.databases, paths.local_cache, settings.get)
    SelfTestService(jobs, paths.databases)
    projects.ensure_default()
    housekeeping = Housekeeping(paths, settings.get, jobs)
    try:
        housekeeping.run()
    except OSError as exc:
        print(f"[BioLoom] pulizia iniziale non riuscita: {exc}")
    housekeeping.start()
    core = Core(paths, settings, db, jobs, library, projects, compute, pipeline, housekeeping, licenses, databases)
    if recovered:
        print(f"[BioLoom] {recovered} job interrotti dal precedente arresto sono stati marcati come falliti.")
    return core
