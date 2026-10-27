"""Sistema: stato, strumenti disponibili, risorse, impostazioni, arresto."""

from __future__ import annotations

import os
import platform
import sys
import threading
import time

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ValidationError

from .. import __version__
from ..core import auth, cache
from ..core.container import Core
from ..core.errors import AppError
from ..engines import tools
from .deps import get_core, is_loopback

router = APIRouter(prefix="/api", tags=["Sistema"])

IN_CONTAINER = os.environ.get("MAKPHI_CONTAINER") == "1"

# Esempi della panoramica. `template` (facoltativo): struttura del PDB proposta come template, scaricata nella libreria
# e preselezionata nella Modellazione con il trasferimento dei ligandi attivo (esempi con HETATM e acque).
EXAMPLES = [
    {"name": "yeast_ubiquitin", "title": "Ubiquitina di lievito",
     "sequence": "MQIFVKTLTGKTITLEVESSDTIDNVKSKIQDKEGIPPDQQRLIFAGKQLEDGRTLSDYNIQKESTLHLVLRLRGG",
     "note": "76 aa, 96% di identità con 1UBQ (umana): il primo modello si calcola in pochi secondi."},
    {"name": "myoglobin", "title": "Mioglobina umana", "accession": "P02144",
     "template": {"id": "1A6K", "chains": ["A"],
                  "note": "aquomet-mioglobina di capodoglio a 1,1 Å: eme (HEM A154) con l'acqua sul ferro (HOH A1001)"},
     "note": "Proteina tutta α con gruppo eme: esempio per i ligandi HETATM e per un'acqua legata a un metallo."},
    {"name": "carbonic_anhydrase", "title": "Anidrasi carbonica II bovina", "accession": "P00921",
     "template": {"id": "3KS3", "chains": ["A"],
                  "note": "anidrasi carbonica II umana a 0,9 Å: zinco (ZN A262) legato a His94, His96, His119 e "
                          "all'acqua catalitica (HOH A402)"},
     "note": "Enzima con zinco e acqua catalitica: esempio per trasferire un metallo e la sua acqua (Ligandi → Acque)."},
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
        "services": {"qmean_remote": settings.qmean_remote, "contact_email": bool(settings.contact_email),
                     "esmfold_remote": settings.esmfold_remote},
        "runtime": {
            "container": IN_CONTAINER,
            "image": os.environ.get("MAKPHI_IMAGE_VERSION", ""),
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
        # budget di CPU condiviso fra i job: slot totali, occupati dai processi di calcolo, processi in attesa
        "cpu_slots": core.jobs.budget.snapshot(),
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
    """Pulizia immediata di upload scaduti, cache scaduta, scratch orfano e resti di job eliminati (`jobs`)."""
    return core.housekeeping.run()


@router.get("/examples")
def examples() -> list[dict]:
    return EXAMPLES


class SettingsUpdate(BaseModel):
    contact_email: str | None = None
    modeller_python: str | None = None
    max_workers: int | None = None
    job_timeout_minutes: int | None = None
    max_sequence_length: int | None = None  # residui al massimo della sequenza bersaglio
    qmean_remote: bool | None = None
    qmean_mode: str | None = None
    qmean_fallback_remote: bool | None = None
    qmean_uniclust: str | None = None
    qmean_timeout_minutes: int | None = None
    default_search_engine: str | None = None
    keep_intermediates: bool | None = None
    procheck_plots: str | None = None
    uploads_retention_days: int | None = None
    compute_cache: bool | None = None
    compute_cache_max_mb: int | None = None  # tetto della cache dei calcoli (MB)
    esmfold_remote: bool | None = None  # F9.3: consenso all'invio delle sequenze a ESMFold
    esmfold_url: str | None = None


# Nomi delle impostazioni come nella pagina Impostazioni, per i messaggi d'errore
SETTING_LABELS = {
    "contact_email": "Email di contatto", "modeller_python": "Interprete Python con MODELLER",
    "max_workers": "Job di calcolo in parallelo", "job_timeout_minutes": "Timeout per processo (minuti)",
    "max_sequence_length": "Lunghezza massima della sequenza (residui)",
    "qmean_mode": "Dove calcolare QMEAN", "qmean_uniclust": "Versione di UniClust30/UniRef30",
    "qmean_timeout_minutes": "Tempo massimo per un calcolo QMEAN locale (minuti)",
    "default_search_engine": "Motore di ricerca predefinito",
    "uploads_retention_days": "Conservazione dei file caricati (giorni)",
    "procheck_plots": "Grafici PostScript di PROCHECK",
    "esmfold_url": "Indirizzo del servizio ESMFold",
    "compute_cache_max_mb": "Spazio massimo della cache dei calcoli (MB)",
}
ESMFOLD_URL_LOCKED = ("In modalità server l'indirizzo del servizio ESMFold lo imposta l'amministratore sul server "
                      "(MAKPHI_ESMFOLD_URL in server.env; vuoto = API pubblica di ESM Atlas), non l'interfaccia.")
MODELLER_PYTHON_LOCKED = ("In modalità server l'interprete Python con MODELLER lo imposta l'amministratore sul server "
                          "(variabile d'ambiente MAKPHI_MODELLER_PYTHON; vuoto = rilevamento automatico, cioè "
                          "l'interprete dell'immagine di MakPhi), non l'interfaccia.")
# Impostazioni che in modalità server sceglie l'amministratore (config.SettingsStore): la pagina rimanda il valore in
# uso, che si accetta e resta com'è; un valore diverso è un errore 403 (campo → messaggio, codice)
SERVER_LOCKED = {
    "esmfold_url": (ESMFOLD_URL_LOCKED, "esmfold_url_locked"),
    "modeller_python": (MODELLER_PYTHON_LOCKED, "modeller_python_locked"),
}


def _setting_error(err: dict) -> str:
    """Un errore di validazione di UserSettings in italiano: i validatori del modello scrivono già in italiano, i
    vincoli dei campi (minimo, massimo, lunghezza, valori ammessi) li scrive pydantic in inglese."""
    field = ".".join(str(p) for p in err.get("loc", ()))
    ctx = err.get("ctx") or {}
    kind = err.get("type", "")
    if kind == "value_error":
        text = str(err.get("msg", "")).removeprefix("Value error, ")
    elif kind in ("less_than_equal", "greater_than_equal", "less_than", "greater_than"):
        bound = {"less_than_equal": ("al massimo", "le"), "greater_than_equal": ("almeno", "ge"),
                 "less_than": ("minore di", "lt"), "greater_than": ("maggiore di", "gt")}[kind]
        text = f"deve essere {bound[0]} {ctx.get(bound[1])}"
    elif kind == "string_too_long":
        text = f"al massimo {ctx.get('max_length')} caratteri"
    elif kind == "literal_error":
        text = f"valore non ammesso (ammessi: {str(ctx.get('expected', '')).replace(' or ', ' o ')})"
    else:  # tipi sbagliati: li ferma prima la validazione della richiesta (SettingsUpdate, errore 422)
        text = str(err.get("msg", "valore non valido"))
    return f"{SETTING_LABELS.get(field, field or 'impostazioni')}: {text}"


@router.get("/settings")
def get_settings(core: Core = Depends(get_core)) -> dict:
    return core.settings.get().model_dump()


@router.put("/settings")
def update_settings(body: SettingsUpdate, core: Core = Depends(get_core)) -> dict:
    changes = body.model_dump(exclude_none=True)
    if core.settings.server:
        # In modalità server l'indirizzo di ESMFold (l'unico indirizzo impostabile) e l'interprete di MODELLER (un
        # programma che il server esegue) li sceglie l'amministratore, non chi ha la chiave d'accesso. La pagina
        # rimanda il valore in uso, che resta com'è.
        current = core.settings.get()
        for name, (message, code) in SERVER_LOCKED.items():
            if name in changes and changes.pop(name).strip().strip('"') != getattr(current, name):
                raise AppError(message, code=code, status=403)
    if "esmfold_url" in changes:  # stessa verifica dell'uso (F9.3): un indirizzo sbagliato è un errore, non si ignora
        from ..engines import remote

        if changes["esmfold_url"].strip():
            remote.validate_esmfold_url(changes["esmfold_url"])
    try:
        updated = core.settings.update(changes)
    except ValidationError as exc:
        raise AppError("Impostazioni non valide: " + "; ".join(_setting_error(e) for e in exc.errors()[:5]) + ".",
                       code="invalid_settings")
    except ValueError as exc:
        raise AppError(f"Impostazioni non valide: {exc}")
    if "modeller_python" in changes:
        tools.detect_modeller(updated.modeller_python, force=True)
    if "max_workers" in changes:
        core.jobs.reconfigure()  # job in parallelo e slot di CPU cambiano subito, senza riavvio
    if "compute_cache_max_mb" in changes:
        # tetto più basso: la cache si riduce subito, in un thread (una cache grande si scorre in qualche secondo)
        threading.Thread(target=cache.enforce, args=(cache.max_bytes(updated),), name="makphi-cache-prune",
                         daemon=True).start()
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
