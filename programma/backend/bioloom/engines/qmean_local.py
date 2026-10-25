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
import threading
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
UNREACHABLE = ("Il servizio QMEAN locale non risponde: è avviato ma occupato o bloccato. Se dura, riavvia BioLoom "
               "dall'avviatore.")
# Servizio configurato che non risponde (occupato, bloccato o in riavvio): il calcolo aspetta e riprova ogni
# RETRY_SECONDS per al più WAIT_MINUTES, poi fallisce. Prima falliva subito: sul server una pipeline ha perso 107.918
# QMEAN in 5 ore, uno ogni 4 s, perché ogni fallimento lasciava partire subito il calcolo seguente.
RETRY_SECONDS = 15.0
WAIT_MINUTES = 30.0
LOST_RETRIES = 3  # nuovi invii di un calcolo perso dal worker (riavviato mentre BioLoom lo seguiva)
_status_cache: dict = {"at": 0.0, "key": None, "value": None}
_status_lock = threading.Lock()  # una richiesta di stato alla volta: le altre ne usano la risposta
_status_fetches = {"begun": 0}  # richieste di stato partite (`fetch` nella cache: quella che ha dato il valore)
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
    all'inizio di ogni calcolo (`score`, cache di STATUS_TTL secondi), quindi dopo il primo i posti in più si usano."""
    return max(1, int(_slots["value"])) if base_url() else 1


def status(prefer: str = "", force: bool = False) -> dict:
    """Stato del worker e dei database trovati (cache di 15 s).

    `available` è vero se la versione richiesta (o, senza richiesta, almeno una) è pronta e la memoria del
    servizio basta per quel database (`memory_problem`); `memory_required` è il requisito del database scelto, con
    `sufficient` (`memory_sufficient`): l'esito del controllo, che l'interfaccia mostra accanto al requisito (una stima
    più alta della memoria non è un difetto se il worker ha giudicato la memoria sufficiente).

    Una richiesta al worker alla volta: chi arriva mentre un'altra è in corso ne usa la risposta. Con `force` vale solo
    una risposta chiesta dopo la chiamata, così decine di calcoli che ripartono insieme fanno al più due richieste.
    """
    url = base_url()
    if not url:
        return {"configured": False, "reachable": False, "available": False, "message": NOT_RUNNING,
                "databases": [], "archives": [], "unsupported": [], "local_orphans": []}
    key = (url, prefer)
    before = _status_fetches["begun"]  # richieste partite prima di questa chiamata
    with _status_lock:
        cached = _status_cache
        if cached["key"] == key and cached["value"] is not None:
            if (cached.get("fetch", 0) > before) if force else time.monotonic() - cached["at"] < STATUS_TTL:
                return cached["value"]
        _status_fetches["begun"] += 1
        fetch = _status_fetches["begun"]
        value = _fetch_status(prefer, force)
        _status_cache.update({"at": time.monotonic(), "key": key, "value": value, "fetch": fetch})
        return value


def _fetch_status(prefer: str, force: bool) -> dict:
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
        value = {"configured": True, "reachable": False, "available": False, "message": UNREACHABLE,
                 "databases": [], "archives": [], "unsupported": [], "local_orphans": []}
    return value


def _requirement(uniclust: dict, memory: Optional[dict]) -> Optional[dict]:
    need = memory_requirement(uniclust)
    return None if need is None else {**need, "sufficient": memory_sufficient(uniclust, memory)}


def _error_text(resp: httpx.Response) -> str:
    """Errore del worker (campo `error` del JSON) o testo della risposta."""
    try:
        return str(resp.json().get("error") or resp.text)
    except (ValueError, AttributeError):
        return resp.text


class _WorkerLost(AppError):
    """Il worker non conosce più il job (è stato riavviato mentre BioLoom lo seguiva)."""

    def __init__(self, what: str) -> None:
        super().__init__(f"{what}: il servizio QMEAN locale è stato riavviato e ha perso il calcolo.",
                         code="qmean_lost")


class _Patience:
    """Attesa del worker che non risponde, comune a tutte le richieste di un calcolo: un nuovo tentativo ogni
    RETRY_SECONDS, al più WAIT_MINUTES (e al più quei tentativi) dalla prima risposta mancata; si azzera quando il
    worker risponde. Poi ToolUnavailable, che lascia passare al fallback su SWISS-MODEL se è attivo."""

    def __init__(self, ctx: JobContext, sleep: Callable[[float], None]) -> None:
        self.ctx, self.sleep = ctx, sleep
        self.since: Optional[float] = None
        self.tries = 0

    def ok(self) -> None:
        if self.since is not None:
            self.ctx.log("Il servizio QMEAN locale risponde di nuovo")
        self.since, self.tries = None, 0

    def wait(self) -> None:
        now = time.monotonic()
        if self.since is None:
            self.since = now
            self.ctx.log(f"Il servizio QMEAN locale non risponde: nuovo tentativo ogni {int(RETRY_SECONDS)} s per "
                         f"al più {int(WAIT_MINUTES)} minuti")
        self.tries += 1
        if self.tries > WAIT_MINUTES * 60 / RETRY_SECONDS or now - self.since > WAIT_MINUTES * 60:
            raise ToolUnavailable(f"{UNREACHABLE} Atteso per {int(WAIT_MINUTES)} minuti.")
        self.ctx.progress(None, f"QMEAN locale: il servizio non risponde, nuovo tentativo fra {int(RETRY_SECONDS)} s")
        for _ in range(int(RETRY_SECONDS)):  # a passi di un secondo: l'annullamento arriva subito
            self.ctx.check()
            self.sleep(1.0)


def _follow(c: httpx.Client, job_id: str, ctx: JobContext, *, timeout_minutes: float, running_stage: str,
            sleep: Callable[[float], None], what: str, patience: Optional[_Patience] = None) -> dict:
    """Segue un job del worker fino al termine; annullamento e timeout lo fermano anche nel worker. Se il worker non
    risponde si riprova (`_Patience`); se non conosce più il job, `_WorkerLost`."""
    patience = patience or _Patience(ctx, sleep)
    deadline = time.monotonic() + timeout_minutes * 60
    last: Optional[tuple] = None
    try:
        while True:
            ctx.check()
            if time.monotonic() > deadline:
                raise AppError(f"{what} non ha terminato entro {int(timeout_minutes)} minuti "
                               "(il tempo si modifica nelle Impostazioni).", code="timeout")
            try:
                resp = c.get(f"/jobs/{job_id}")
                job = resp.json()
            except (httpx.HTTPError, ValueError):
                patience.wait()
                continue
            patience.ok()
            if resp.status_code == 404:
                raise _WorkerLost(what)
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
    """Calcolo QMEAN nel worker. Lo stato viene dalla cache (15 s, comune a tutti i calcoli): con una pipeline di
    migliaia di modelli una richiesta di stato completa per calcolo teneva il worker occupato. Se il worker non
    risponde il calcolo aspetta (`_Patience`) invece di fallire; se lo perde (riavvio) lo invia di nuovo."""
    # l'attesa si azzera solo quando il worker segue davvero il calcolo (`_follow`): uno stato che risponde mentre gli
    # invii non passano non la allunga all'infinito
    patience = _Patience(ctx, sleep)
    body = {"pdb": pdb_text, "method": method, "seqres": seqres, "database": database}
    lost = 0
    while True:
        info = status(database)
        while info["configured"] and not info["reachable"]:
            patience.wait()
            info = status(database, force=True)
        if not info["available"]:
            raise ToolUnavailable(info["message"] or "QMEAN locale non disponibile.")
        with _client() as c:
            try:
                resp = c.post("/jobs", json=body)
                accepted = resp.json() if resp.status_code == 202 else None
            except (httpx.HTTPError, ValueError):
                patience.wait()  # nessuna risposta all'invio: si riprova dopo aver riletto lo stato
                continue
            if accepted is None:
                raise AppError(f"QMEAN locale: {_error_text(resp)}", code="qmean_error")
            chosen = info["uniclust"]
            ctx.log(f"Modello inviato al servizio QMEAN locale (database {chosen.get('prefix')}"
                    f"{', versione ' + chosen['version'] if chosen.get('version') else ''})")
            try:
                job = _follow(c, accepted["id"], ctx, timeout_minutes=timeout_minutes, sleep=sleep,
                              what="QMEAN locale", patience=patience,
                              running_stage="QMEAN locale: profilo HHblits su UniClust30 e punteggi")
                break
            except _WorkerLost as exc:
                lost += 1
                if lost > LOST_RETRIES:
                    raise
                ctx.log(f"{exc.message} Nuovo invio ({lost} di {LOST_RETRIES})")
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
