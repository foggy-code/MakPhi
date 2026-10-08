"""Contenitore dei servizi applicativi, creato una volta all'avvio del server."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from ..config import Paths, SettingsStore
from ..engines import remote, search, tools
from ..services.analysis import AnalysisService
from ..services.compute import ComputeService
from ..services.databases import DatabaseService
from ..services.globalsearch import GlobalSearch
from ..services.housekeeping import Housekeeping
from ..services.jobcleanup import JobCleanup
from ..services.labels import LabelService
from ..services.licenses import LicenseService
from ..services.live import LiveService
from ..services.mutations import MutationService
from ..services.notify import NotifyService
from ..services.pipeline import PipelineService
from ..services.predict import PredictService
from ..services.projects import ProjectService
from ..services.refine import RefineService
from ..services.reproducibility import ReproducibilityService
from ..services.selftest import SelfTestService
from ..services.structure_tools import StructureToolsService
from ..services.templates import TemplateLibrary
from . import cache, offload
from .db import Database
from .jobs import JobManager


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
    live: LiveService | None = None  # flussi SSE dei job, creato in build_core
    labels: LabelService | None = None  # etichette e preferiti (F8b), creato in build_core
    search: GlobalSearch | None = None  # ricerca globale (F8b), creato in build_core
    repro: ReproducibilityService | None = None  # pacchetti di riproducibilità e riesecuzione (F9.4)
    cleanup: JobCleanup | None = None  # pulizia di massa dei job ed eliminazione dei progetti, creato in build_core
    notify: NotifyService | None = None  # avviso per email alla fine delle pipeline, creato in build_core

    def shutting_down(self) -> bool:
        """Arresto in corso: segnale ricevuto da uvicorn (`should_exit`) o JobManager chiuso. I flussi SSE si
        chiudono da soli, altrimenti uvicorn aspetterebbe all'infinito le loro connessioni prima di fermarsi."""
        return self.jobs.closed or bool(getattr(self.server, "should_exit", False))

    def delete_project(self, project_id: str) -> dict:
        """Elimina il progetto con tutti i suoi job (services/jobcleanup.py): esito con job eliminati, byte liberati,
        resti e progetto sostitutivo se era l'ultimo."""
        return self.cleanup.delete_project(project_id)

    def close(self) -> None:
        self.housekeeping.stop()
        self.jobs.shutdown()
        offload.configure(None)  # chiude i processi di appoggio
        self.db.close()


def build_core(paths: Paths, *, server: bool = False) -> Core:
    """`server`: modalità server (accesso con chiave, BIOLOOM_AUTH=on), vedi SettingsStore."""
    paths.ensure()
    settings = SettingsStore(paths.settings_file, server=server)
    tools.configure(paths.tools)
    licenses = LicenseService(paths.licenses, settings.get)
    licenses.apply()
    remote.configure(paths.cache)
    cache.configure(paths.compute_cache)
    search.configure(paths.local_cache)
    db = Database(paths.db)
    if db.migrated:
        steps = ", ".join(f"{version} ({what})" for version, what in db.migrated)
        print(f"[BioLoom] database aggiornato: migrazioni allo schema {steps}"
              + (f"; copia di sicurezza in {db.backup}" if db.backup else ""))
    # job di calcolo in parallelo e budget di CPU seguono le Impostazioni anche dopo un cambio (workers=None)
    jobs = JobManager(db, paths.jobs, None, settings.get, scratch_dir=paths.scratch)
    # processi di appoggio per i calcoli Python dell'analisi di qualità, con molti job in parallelo (core/offload.py)
    offload.configure(jobs.cpu_workers)
    recovered = jobs.recover()
    library = TemplateLibrary(paths.templates)
    # etichette e preferiti, e indice della ricerca globale (F8b) prima di ogni altra scrittura
    labels = LabelService(db, library)
    labels.prune_templates()
    global_search = GlobalSearch(db, library, labels)
    projects = ProjectService(db)
    compute = ComputeService(jobs, library, paths.uploads, paths.databases)
    AnalysisService(jobs, compute).register()  # job "analysis" (F7)
    StructureToolsService(jobs, compute).register()  # job "pockets" ed "electrostatics" (F7b)
    RefineService(jobs, compute).register()  # job "refine" (F9.1)
    repro = ReproducibilityService(jobs, compute, library, paths, settings.get, licenses)
    repro.register()  # job "rerun" (F9.4)
    MutationService(jobs, compute).register()  # job "mutate" e "alascan" (F9.2)
    PredictService(jobs, compute).register()  # job "predict" (F9.3)
    pipeline = PipelineService(db, jobs, compute)
    databases = DatabaseService(jobs, paths.databases, paths.local_cache, settings.get)
    SelfTestService(jobs, paths.databases)
    projects.ensure_default()
    housekeeping = Housekeeping(paths, settings.get, jobs)
    try:
        housekeeping.run()
    except Exception as exc:  # come il passo giornaliero: la pulizia non deve mai impedire l'avvio
        print(f"[BioLoom] pulizia iniziale non riuscita: {exc}")
    housekeeping.start()
    core = Core(paths, settings, db, jobs, library, projects, compute, pipeline, housekeeping, licenses, databases)
    core.live = LiveService(jobs)
    core.live.stopping = core.shutting_down
    core.labels, core.search = labels, global_search
    core.repro = repro
    core.cleanup = JobCleanup(db, jobs, projects)
    core.cleanup.stopping = core.shutting_down
    core.notify = NotifyService(paths.data, paths.licenses, jobs, server=server)
    licenses.extra_secrets = core.notify.secrets  # password della posta, come le chiavi di licenza
    if recovered:
        print(f"[BioLoom] {recovered} job interrotti dal precedente arresto sono stati marcati come falliti "
              "(le pipeline si possono riprendere).")
    return core
