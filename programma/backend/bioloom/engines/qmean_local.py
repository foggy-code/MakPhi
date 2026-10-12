"""QMEAN locale: client del worker QMEAN (container `qmean`, UniClust30/UniRef30 dal disco esterno).

Il worker è raggiungibile solo sulla rete interna di Docker (BIOLOOM_QMEAN_URL). Riconosce qualunque
versione del database (anche più versioni sullo stesso disco, vedi docker/qmean/uniclust.py): BioLoom
indica quale usare (impostazione `qmean_uniclust`, vuota = la più recente pronta). Il calcolo usa HHblits:
dal disco esterno servono da qualche minuto a oltre mezz'ora per modello. Con Docker Desktop conviene la
copia locale di indici e cs219 (`local_copy`), che evita le letture piccole attraverso la condivisione di file.

Memoria: prima di ogni calcolo si confronta la memoria del servizio (`memory` dello stato del worker) con quella
richiesta dal database scelto (`memory_requirement`). Se non basta il calcolo non parte e il job fallisce subito
con il motivo (o passa al fallback su SWISS-MODEL, se attivo): con meno memoria HHblits rilegge di continuo il
cs219 e il calcolo si fermerebbe a metà per il timeout.
"""

from __future__ import annotations

import math
import os
import time
from typing import Callable, Optional

import httpx

from ..core.errors import AppError, ToolUnavailable
from ..core.jobs import JobCancelled, JobContext
from .remote import normalize_qmean

POLL_SECONDS = 3.0
STATUS_TTL = 15.0
NOT_RUNNING = ("QMEAN locale non attivo: collega il disco con UniClust30/UniRef30 e avvia (o riavvia) BioLoom "
               "dall'avviatore, che attiva il servizio quando trova il database.")
_status_cache: dict = {"at": 0.0, "key": None, "value": None}
# calcoli contemporanei del worker (QMEAN_PARALLEL, `slots` nel suo stato): ultimo valore letto, per il dispatcher
_slots = {"value": 1}

GIB = 1024 ** 3  # unità binarie, come il worker e l'interfaccia ("GB")
# Memoria da assegnare a Docker perché HHblits finisca in tempi ragionevoli, misurata (Guida, docs/ARCHITETTURA.md):
# con UniRef30_2020_06 HHblits tiene in memoria 3,8 GB di indici (1,36 GB sul disco) e deve avere nella cache
# l'intero cs219 (6 GB), più circa 1 GB per il resto del programma e il 10% di margine per kernel e container.
# Con 5,7 GB ha letto oltre 100 GB in 4 minuti senza avanzare.
MEASURED_MEMORY = {"uniref30_2020_06": 12 * GIB}
REFERENCE_DATABASE = "uniref30_2020_06"
REFERENCE_SIZE = 194 * GIB  # UniRef30_2020_06 sul disco (HH-suite 3, file a3m, hhm e cs219)
# Versioni non misurate: stima in proporzione alla dimensione del database, più il 50%. La memoria dipende da
# indici e cs219, che crescono con il numero di cluster; la dimensione la fanno soprattutto gli allineamenti, meno
# profondi nelle versioni vecchie, che hanno quindi più indice e cs219 per GB di database. È una stima prudente, non
# una misura: se il worker, che conosce le dimensioni vere degli indici, giudica la memoria sufficiente ("ok"),
# vale il suo giudizio.
ESTIMATE_MARGIN = 1.5
MIN_MEMORY = 2 * GIB  # HHblits e OpenStructure anche con un database minuscolo
# /proc/meminfo del container riporta un po' meno della memoria assegnata a Docker (kernel, riserve: con 12 GB
# assegnati si leggono circa 11,7 GB): si accetta il 95% del requisito.
MEMINFO_TOLERANCE = 0.95


def _gb(value: float, decimals: int = 1) -> str:
    return f"{value / GIB:.{decimals}f}".replace(".", ",")


def memory_requirement(uniclust: Optional[dict]) -> Optional[dict]:
    """Memoria richiesta dal database scelto: {"bytes", "basis": "measured" | "estimate"}; None se non si sa.

    `uniclust` è la descrizione del database scelto nello stato del worker (nome in `prefix`, `size` in byte).
    """
    if not isinstance(uniclust, dict):
        return None
    name = str(uniclust.get("prefix") or "").lower()
    if name in MEASURED_MEMORY:
        return {"bytes": MEASURED_MEMORY[name], "basis": "measured"}
    size = uniclust.get("size")
    if isinstance(size, bool) or not isinstance(size, (int, float)) or size <= 0:
        return None
    estimate = size / REFERENCE_SIZE * MEASURED_MEMORY[REFERENCE_DATABASE] * ESTIMATE_MARGIN
    return {"bytes": int(math.ceil(max(MIN_MEMORY, estimate) / GIB)) * GIB, "basis": "estimate"}


def memory_problem(uniclust: Optional[dict], memory: Optional[dict]) -> Optional[str]:
    """Perché la memoria del servizio QMEAN non basta per il database scelto; None se basta o non si sa.

    Nell'ordine: il giudizio del worker sulle dimensioni vere degli indici ("insufficient": non basta nemmeno con
    lo swap; "ok": basta con il 10% di margine), poi il requisito di `memory_requirement` confrontato con la memoria
    del container (senza lo swap, che per HHblits non serve: il cs219 va tenuto nella cache).
    """
    uniclust = uniclust if isinstance(uniclust, dict) else {}
    fit = uniclust.get("memory")
    if fit == "insufficient":
        return uniclust.get("memory_message") or "memoria insufficiente per HHblits."
    if fit == "ok":
        return None
    total = (memory or {}).get("total") if isinstance(memory, dict) else None
    need = memory_requirement(uniclust)
    if not isinstance(total, (int, float)) or total <= 0 or need is None:
        return None  # worker di una versione che non misura la memoria: nessun controllo
    if total >= need["bytes"] * MEMINFO_TOLERANCE:
        return None
    name = uniclust.get("prefix") or "il database scelto"
    basis = "valore misurato" if need["basis"] == "measured" else \
        f"stima dalla dimensione del database, {_gb(float(uniclust.get('size') or 0), 0)} GB"
    return (f"memoria insufficiente per {name}: servono circa {_gb(need['bytes'], 0)} GB assegnati a Docker "
            f"({basis}) e il servizio QMEAN ne ha {_gb(total)}. Con meno memoria HHblits rilegge di continuo il "
            "cs219 e il calcolo si fermerebbe a metà per il timeout. Aumenta la memoria di Docker, scegli una "
            "versione più piccola del database oppure usa il fallback su SWISS-MODEL.")


def memory_sufficient(uniclust: Optional[dict], memory: Optional[dict]) -> Optional[bool]:
    """Esito di `memory_problem` per l'interfaccia: False se la memoria non basta, True se basta (giudizio "ok" del
    worker, anche quando la stima prudente direbbe di no, oppure requisito entro la tolleranza), None se non si sa
    (worker che non misura la memoria o database senza requisito noto)."""
    if memory_problem(uniclust, memory) is not None:
        return False
    uniclust = uniclust if isinstance(uniclust, dict) else {}
    if uniclust.get("memory") == "ok":
        return True
    total = memory.get("total") if isinstance(memory, dict) else None
    known = isinstance(total, (int, float)) and not isinstance(total, bool) and total > 0
    if not known or memory_requirement(uniclust) is None:
        return None
    return True


def base_url() -> str:
    """Indirizzo del worker: l'avviatore lo imposta solo quando ha trovato UniClust30."""
    return os.environ.get("BIOLOOM_QMEAN_URL", "").rstrip("/")


def _client() -> httpx.Client:
    return httpx.Client(base_url=base_url(), timeout=httpx.Timeout(20.0, connect=3.0))


def slots() -> int:
    """Calcoli QMEAN locali contemporanei (posti della risorsa QMEAN_LOCAL): l'ultimo valore dichiarato dal worker,
    senza I/O (si legge sotto il lock del dispatcher); 1 finché il worker non è stato interrogato. Lo stato si rilegge
    all'inizio di ogni calcolo (`score`), quindi dopo il primo i posti in più si usano."""
    return max(1, int(_slots["value"])) if base_url() else 1


def status(prefer: str = "", force: bool = False) -> dict:
    """Stato del worker e dei database trovati (cache di 15 s).

    `available` è vero se la versione richiesta (o, senza richiesta, almeno una) è pronta e la memoria del
    servizio basta per quel database (`memory_problem`); `memory_required` è il requisito del database scelto, con
    `sufficient` (`memory_sufficient`): l'esito del controllo, che l'interfaccia mostra accanto al requisito (una stima
    più alta della memoria non è un difetto se il worker ha giudicato la memoria sufficiente).
    """
    url = base_url()
    if not url:
        return {"configured": False, "reachable": False, "available": False, "message": NOT_RUNNING,
                "databases": [], "archives": [], "unsupported": [], "local_orphans": []}
    key = (url, prefer)
    cached = _status_cache
    if not force and cached["key"] == key and time.monotonic() - cached["at"] < STATUS_TTL:
        return cached["value"]
    try:
        with _client() as c:
            params = {"prefer": prefer} if prefer else {}
            if force:
                params["refresh"] = "1"
            data = c.get("/status", params=params).json()
        uniclust = data.get("uniclust") or {}
        valid = bool(uniclust.get("valid"))
        # memoria che non basta: HHblits si fermerebbe a metà (o verrebbe interrotto), meglio dirlo subito
        no_memory = memory_problem(uniclust, data.get("memory")) if valid else None
        if not valid:
            message = f"UniClust30 non utilizzabile: {uniclust.get('error')}"
        else:
            message = f"QMEAN locale non utilizzabile: {no_memory}" if no_memory else None
        value = {"configured": True, "reachable": True, "available": valid and not no_memory,
                 "uniclust": uniclust, "selected": data.get("selected"), "preferred": prefer or None,
                 "databases": data.get("databases") or [], "archives": data.get("archives") or [],
                 "unsupported": data.get("unsupported") or [], "local_orphans": data.get("local_orphans") or [],
                 "qmtl": data.get("qmtl") or {},
                 "memory": data.get("memory"),
                 "memory_required": _requirement(uniclust, data.get("memory")) if valid else None,
                 "busy": data.get("busy"), "queued": data.get("queued"), "qmean_version": data.get("qmean_version"),
                 "slots": _count(data.get("slots")) or 1, "running": _count(data.get("running")),
                 "profiles": data.get("profiles") if isinstance(data.get("profiles"), dict) else None,
                 "message": message}
        _slots["value"] = value["slots"]
    except (httpx.HTTPError, ValueError):
        value = {"configured": True, "reachable": False, "available": False, "message": NOT_RUNNING,
                 "databases": [], "archives": [], "unsupported": [], "local_orphans": []}
    _status_cache.update({"at": time.monotonic(), "key": key, "value": value})
    return value


def _requirement(uniclust: dict, memory: Optional[dict]) -> Optional[dict]:
    need = memory_requirement(uniclust)
    return None if need is None else {**need, "sufficient": memory_sufficient(uniclust, memory)}


def _follow(c: httpx.Client, job_id: str, ctx: JobContext, *, timeout_minutes: float, running_stage: str,
            sleep: Callable[[float], None], what: str) -> dict:
    """Segue un job del worker fino al termine; annullamento e timeout lo fermano anche nel worker."""
    deadline = time.monotonic() + timeout_minutes * 60
    last: Optional[tuple] = None
    try:
        while True:
            ctx.check()
            if time.monotonic() > deadline:
                raise AppError(f"{what} non ha terminato entro {int(timeout_minutes)} minuti "
                               "(il tempo si modifica nelle Impostazioni).", code="timeout")
            job = c.get(f"/jobs/{job_id}").json()
            state = job.get("status")
            if state == "completed":
                return job
            if state in ("failed", "cancelled"):
                raise AppError(f"{what} non riuscito: " + str(job.get("error") or state), code="qmean_error")
            if state == "queued":
                current = (0.1, f"{what} in coda (posizione {job.get('position', 0) + 1})")
            elif job.get("progress") is not None:  # il worker misura l'avanzamento (copie)
                current = (0.05 + 0.9 * float(job["progress"]), job.get("stage") or running_stage)
            else:
                current = (0.3, running_stage)
            if current != last:
                ctx.progress(*current)
                last = current
            sleep(POLL_SECONDS)
    except (JobCancelled, AppError):
        try:
            c.delete(f"/jobs/{job_id}")
        except httpx.HTTPError:
            pass
        raise


def score(pdb_text: str, ctx: JobContext, *, method: str = "auto", seqres: str = "", database: str = "",
          timeout_minutes: float = 60, sleep: Callable[[float], None] = time.sleep) -> dict:
    info = status(database, force=True)
    if not info["available"]:
        raise ToolUnavailable(info["message"] or "QMEAN locale non disponibile.")
    with _client() as c:
        resp = c.post("/jobs", json={"pdb": pdb_text, "method": method, "seqres": seqres, "database": database})
        if resp.status_code != 202:
            raise AppError(f"QMEAN locale: {resp.json().get('error', resp.text)}", code="qmean_error")
        chosen = info["uniclust"]
        ctx.log(f"Modello inviato al servizio QMEAN locale (database {chosen.get('prefix')}"
                f"{', versione ' + chosen['version'] if chosen.get('version') else ''})")
        job = _follow(c, resp.json()["id"], ctx, timeout_minutes=timeout_minutes, sleep=sleep, what="QMEAN locale",
                      running_stage="QMEAN locale: profilo HHblits su UniClust30 e punteggi")
    result = job.get("result") or {}
    used = result.get("bioloom_database") or {}
    hhblits = _hhblits(result.get("bioloom_hhblits"))
    if hhblits and hhblits["seconds"] is not None:
        ctx.log(f"QMEAN locale concluso in {hhblits['seconds']} s nel worker (HHblits su "
                f"{hhblits['cpu'] or '?'} processori, memoria {hhblits['memory'] or 'non nota'})")
    profiles = result.get("bioloom_profiles")
    if hhblits is not None and isinstance(profiles, dict):
        hhblits["profiles_reused"] = _count(profiles.get("reused"))
        hhblits["profiles_computed"] = _count(profiles.get("computed"))
        if hhblits["profiles_reused"]:
            ctx.log(f"Profilo HHblits riusato per {hhblits['profiles_reused']} "
                    f"{'sequenza' if hhblits['profiles_reused'] == 1 else 'sequenze'} già valutate con lo stesso "
                    "database: ricerca su UniRef30 saltata, stesso risultato")
    return {**normalize_qmean(result), "source": "local",
            "uniclust": used.get("name") or chosen.get("prefix"), "uniclust_version": used.get("version"),
            "qmtl": (info.get("qmtl") or {}).get("version"), "hhblits": hhblits}


def _hhblits(raw) -> Optional[dict]:
    """Risorse del calcolo dal worker (`bioloom_hhblits`): processori dati a HHblits, giudizio sulla memoria del
    database (ok | swap) e secondi dell'intero calcolo nel worker (profilo HHblits e punteggi QMEAN). None se il worker
    non le riporta (versioni precedenti); valori di tipo inatteso diventano None."""
    if not isinstance(raw, dict):
        return None

    memory = raw.get("memory")
    return {"cpu": _count(raw.get("cpu")), "memory": memory if memory in ("ok", "swap") else None,
            "seconds": _count(raw.get("seconds")), "parallel": _count(raw.get("parallel"))}


def _count(value) -> Optional[int]:
    """Numero intero non negativo dal worker, None per valori di tipo inatteso."""
    return int(value) if isinstance(value, (int, float)) and not isinstance(value, bool) and value >= 0 else None


def convert(database: str, ctx: JobContext, *, sleep: Callable[[float], None] = time.sleep) -> dict:
    """Ricrea il cs219 di un database HH-suite 2 (una volta sola; può richiedere ore sui database completi)."""
    info = status(force=True)
    if not info["reachable"]:
        raise ToolUnavailable(info["message"] or NOT_RUNNING)
    with _client() as c:
        resp = c.post("/convert", json={"database": database})
        if resp.status_code != 202:
            raise AppError(f"Conversione non avviata: {resp.json().get('error', resp.text)}", code="qmean_error")
        ctx.log(f"Conversione del database {database} per HH-suite 3 (cstranslate) avviata")
        job = _follow(c, resp.json()["id"], ctx, timeout_minutes=48 * 60, sleep=sleep, what="La conversione",
                      running_stage="Ricostruzione del file cs219 dagli allineamenti (cstranslate)")
    status(force=True)
    return job.get("result") or {}


def local_copy(database: str, action: str, ctx: JobContext, *, sleep: Callable[[float], None] = time.sleep) -> dict:
    """Copia (o elimina) nel volume locale gli indici e il cs219 di un database: una volta per computer."""
    info = status(force=True)
    if not info["reachable"]:
        raise ToolUnavailable(info["message"] or NOT_RUNNING)
    with _client() as c:
        resp = c.post("/local", json={"database": database, "action": action})
        if resp.status_code != 202:
            raise AppError(f"Operazione non avviata: {resp.json().get('error', resp.text)}", code="qmean_error")
        if action == "copy":
            ctx.log(f"Copia di indici e cs219 di {database} nel volume locale (i file grandi restano sul disco)")
        job = _follow(c, resp.json()["id"], ctx, timeout_minutes=12 * 60, sleep=sleep,
                      what="La copia locale" if action == "copy" else "La rimozione della copia locale",
                      running_stage="Copia degli indici su questo computer" if action == "copy"
                      else "Rimozione della copia locale")
    status(force=True)
    result = job.get("result") or {}
    if action == "copy" and result.get("seconds") is not None:
        ctx.log(f"Copia locale pronta in {result['seconds']} s")
    return result
