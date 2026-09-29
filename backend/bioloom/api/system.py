"""Sistema: stato, strumenti disponibili, risorse, impostazioni, arresto."""

from __future__ import annotations

import os
import platform
import sys
import threading
import time

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel

from .. import __version__
from ..core import auth
from ..core.container import Core
from ..core.errors import AppError
from ..engines import tools
from .deps import get_core, is_loopback

router = APIRouter(prefix="/api", tags=["Sistema"])

IN_CONTAINER = os.environ.get("BIOLOOM_CONTAINER") == "1"

EXAMPLES = [
    {"name": "yeast_ubiquitin", "title": "Ubiquitina di lievito",
     "sequence": "MQIFVKTLTGKTITLEVESSDTIDNVKSKIQDKEGIPPDQQRLIFAGKQLEDGRTLSDYNIQKESTLHLVLRLRGG",
     "note": "76 aa, 96% di identità con 1UBQ (umana): il primo modello si calcola in pochi secondi."},
    {"name": "myoglobin", "title": "Mioglobina umana", "accession": "P02144",
     "note": "Proteina tutta α con gruppo eme: esempio per i ligandi HETATM."},
    {"name": "lysozyme", "title": "Lisozima di pollo", "accession": "P00698",
     "note": "Enzima compatto con quattro ponti disolfuro."},
    {"name": "insulin", "title": "Insulina umana", "accession": "P01308",
     "note": "Pre-pro-ormone: esempio di copertura parziale del template."},
]


@router.get("/health")
def health() -> dict:
    return {"status": "ok", "version": __version__}


def _shutdown_allowed(request: Request) -> bool:
    cfg = getattr(request.app.state, "auth", None)
    if auth.shutdown_allowed():
        return True
    # esecuzione nativa (sviluppo): solo dal computer locale e senza accesso protetto
    return not (cfg and cfg.enabled) and not IN_CONTAINER and is_loopback(request)


@router.get("/system/info")
def info(request: Request, refresh: bool = False, core: Core = Depends(get_core)) -> dict:
    settings = core.settings.get()
    if refresh:
        tools.configure(core.paths.tools)
    cfg = getattr(request.app.state, "auth", None)
    return {
        "version": __version__,
        "python": sys.version.split()[0],
        "platform": f"{platform.system()} {platform.release()}",
        "data_dir": str(core.paths.data),
        "uptime": round(time.time() - core.started_at),
        "workers": settings.effective_workers(),
        "modeller": tools.detect_modeller(settings.modeller_python, force=refresh),
        "tools": tools.status(),
        "services": {"qmean_remote": settings.qmean_remote, "contact_email": bool(settings.contact_email)},
        "runtime": {
            "container": IN_CONTAINER,
            "image": os.environ.get("BIOLOOM_IMAGE_VERSION", ""),
            "arch": platform.machine(),
            "auth": bool(cfg and cfg.enabled),
            "allow_shutdown": _shutdown_allowed(request),
            "modeller_key": core.licenses.modeller_key()[1],
        },
    }


@router.get("/system/stats")
def stats(core: Core = Depends(get_core)) -> dict:
    import psutil

    mem = psutil.virtual_memory()
    disk = psutil.disk_usage(str(core.paths.data))
    proc = psutil.Process(os.getpid())
    return {
        "cpu_percent": psutil.cpu_percent(interval=None),
        "cpu_count": psutil.cpu_count(),
        "memory": {"total": mem.total, "used": mem.used, "percent": mem.percent},
        "disk": {"total": disk.total, "free": disk.free, "percent": disk.percent},
        "process_memory": proc.memory_info().rss,
        "active_jobs": core.jobs.count_active(),
    }


@router.get("/system/storage")
def storage(core: Core = Depends(get_core)) -> dict:
    return core.housekeeping.storage()


@router.post("/system/selftest")
def selftest(core: Core = Depends(get_core)) -> dict:
    """Job che prova ogni strumento su 1UBQ (MODELLER, DSSP, BLAST+, MSA, TM/US-align, PROCHECK, ProSA...)."""
    return core.jobs.submit("selftest", {}, title="Verifica dell'installazione")


@router.post("/system/cleanup")
def cleanup(core: Core = Depends(get_core)) -> dict:
    """Pulizia immediata di upload scaduti, cache scaduta e scratch orfano."""
    return core.housekeeping.run()


@router.get("/examples")
def examples() -> list[dict]:
    return EXAMPLES


class SettingsUpdate(BaseModel):
    contact_email: str | None = None
    modeller_python: str | None = None
    max_workers: int | None = None
    job_timeout_minutes: int | None = None
    qmean_remote: bool | None = None
    qmean_mode: str | None = None
    qmean_fallback_remote: bool | None = None
    qmean_uniclust: str | None = None
    qmean_timeout_minutes: int | None = None
    default_search_engine: str | None = None
    keep_intermediates: bool | None = None
    uploads_retention_days: int | None = None


@router.get("/settings")
def get_settings(core: Core = Depends(get_core)) -> dict:
    return core.settings.get().model_dump()


@router.put("/settings")
def update_settings(body: SettingsUpdate, core: Core = Depends(get_core)) -> dict:
    changes = body.model_dump(exclude_none=True)
    try:
        updated = core.settings.update(changes)
    except ValueError as exc:
        raise AppError(f"Impostazioni non valide: {exc}")
    if "modeller_python" in changes:
        tools.detect_modeller(updated.modeller_python, force=True)
    return updated.model_dump()


@router.post("/system/shutdown")
def shutdown(request: Request, core: Core = Depends(get_core)) -> dict:
    if not _shutdown_allowed(request):
        raise AppError("L'arresto da interfaccia non è consentito su questa installazione: "
                       "usa l'avviatore o il comando 'docker compose down' sul server.", status=403)
    active = core.jobs.count_active()

    def stop() -> None:
        time.sleep(0.5)
        core.jobs.shutdown()
        if core.server is not None:
            core.server.should_exit = True
        else:
            os._exit(0)

    threading.Thread(target=stop, daemon=True).start()
    return {"status": "stopping", "cancelled_jobs": active}
