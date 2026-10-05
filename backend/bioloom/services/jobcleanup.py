"""Pulizia di massa dei job ed eliminazione dei progetti con i loro job.

Due capacità manuali (l'utente sceglie e conferma, nulla qui parte da solo):
  - pulizia dei job terminati con criteri espliciti: ambito (un progetto o tutti), stati, tipi ed età; stella,
    etichette e pipeline riprendibili si conservano per impostazione predefinita. L'anteprima (`preview`) è in sola
    lettura; l'esecuzione (`run`) è un ciclo a tempo senza stato: ogni chiamata elimina finché non scade il suo tempo
    e dice quanti job restano da esaminare (`remaining`), e il client ripete finché non sono 0, sempre con l'istante
    dell'anteprima confermata (`as_of`), così nessun job terminato dopo l'anteprima viene eliminato. Niente job di
    pulizia in background: occuperebbe uno slot del pool dietro le pipeline lunghe e comparirebbe nell'elenco che svuota.
  - eliminazione di un progetto con tutti i suoi job (`delete_project`), completa e sincrona: se era l'ultimo
    progetto ne resta uno vuoto (services/projects.py, `delete_if_unused`).
I job si eliminano uno alla volta con `JobManager.delete` (cartella `.deleting-<id>`, `leftover`): nella pulizia i
criteri si rivalutano nello stesso DELETE (`only_if`), così una stella aggiunta nel frattempo salva il job, e un job
ripreso o in uso si salta invece di aspettarlo.

Ordine dei lock: guardia di `JobCleanup` → `JobManager._lock` → `Database._lock`, mai al contrario. La guardia protegge
solo l'appartenenza al set dei progetti in eliminazione e non si tiene mai durante una chiamata ad altri componenti;
`JobManager.delete` non si chiama mai dentro `db.transaction()`.
"""

from __future__ import annotations

import math
import threading
import time
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Callable, Optional

from ..core.db import Database
from ..core.errors import AppError, Conflict, NotFound
from ..core.jobs import ACTIVE, TERMINAL, JobManager
from ..core.storage import dir_size_until, validate_identifier
from .projects import ProjectService

DEFAULT_SECONDS = 15.0  # tempo di una chiamata di pulizia se il client non lo indica
MIN_SECONDS = 1.0
MAX_SECONDS = 25.0  # limite morbido: si controlla dopo ogni tentativo, un'eliminazione lenta può superarlo
BYTES_SECONDS = 3.0  # tempo massimo per misurare le cartelle (anteprima, uso del progetto)
BATCH = 50  # job letti per volta dal ciclo di pulizia
PROJECT_BATCH = 200  # job letti per volta dall'eliminazione di un progetto
PROJECT_PASSES = 3  # passate dell'eliminazione di un progetto che continua a ricevere job
SAMPLE_SIZE = 5
MAX_MEMO = 50_000  # dimensioni di cartelle ricordate (oltre si svuota)
MAX_AGE_DAYS = 36500

# Frammenti SQL sulle colonne di `jobs`, usati senza alias nelle query, nei DELETE e nei predicati di `only_if`
STARRED = "EXISTS (SELECT 1 FROM favorites WHERE entity_type='job' AND entity_id=jobs.id)"
TAGGED = "EXISTS (SELECT 1 FROM tags WHERE entity_type='job' AND entity_id=jobs.id)"
# pipeline fallita o annullata con elementi già completati: riprenderla non rifà il lavoro salvato
RESUMABLE = ("(jobs.kind='pipeline' AND jobs.status IN ('failed','cancelled') AND EXISTS (SELECT 1 FROM "
             "pipeline_items WHERE pipeline_items.job_id=jobs.id AND pipeline_items.status='completed'))")
# età = fine del job, altrimenti ultimo aggiornamento; una data illeggibile (NULL per julianday) conserva il job
AGE = "julianday(COALESCE(finished_at, updated_at, created_at)) < julianday(?)"
# con `as_of` (istante dell'anteprima confermata): un job terminato, o ripreso e terminato di nuovo, dopo l'anteprima
# non corrisponde più, né nella SELECT né nel DELETE (`only_if`). Confronto stretto: le date sono al secondo, e un job
# terminato nello stesso secondo dell'anteprima, forse dopo la sua lettura, si risparmia invece di rischiare di
# eliminarlo senza averlo mostrato
UNTIL = "julianday(COALESCE(finished_at, updated_at, created_at)) < julianday(?)"
AS_OF_TOLERANCE = timedelta(minutes=1)  # orologi del client e del server non allineati al secondo
ACTIVE_SQL = "status IN ('queued','running','cancelling')"

SCOPES = ("project", "all")


@dataclass(frozen=True)
class Criteria:
    """Criteri validati di una pulizia (`JobCleanup.criteria`)."""

    scope: str  # "project" o "all" (tutti i progetti, compresi i job senza progetto)
    project_id: Optional[str]  # solo con scope "project"
    statuses: tuple[str, ...]  # sottoinsieme non vuoto di TERMINAL
    kinds: Optional[tuple[str, ...]]  # None = tutti i tipi
    older_than_days: Optional[int]  # None = qualsiasi età
    keep_favorites: bool
    keep_tagged: bool
    include_resumable: bool
    # istante dell'anteprima confermata (UTC, al secondo): fa da "adesso" per l'età e nessun job terminato dopo
    # corrisponde; None = adesso, senza limite superiore (lo svuotamento di un progetto da eliminare, che deve essere
    # completo)
    as_of: Optional[datetime] = None


def _invalid(message: str) -> AppError:
    return AppError(message, code="invalid_cleanup")


def _as_of(value) -> Optional[datetime]:
    """`as_of` del corpo: istante ISO 8601 con fuso orario, portato in UTC e al secondo; mai nel futuro (oltre la
    tolleranza). None = assente."""
    if value is None:
        return None
    instant = None
    if isinstance(value, str):
        text = value.strip()
        if text[-1:] in ("Z", "z"):  # forma di JavaScript (toISOString)
            text = text[:-1] + "+00:00"
        try:
            instant = datetime.fromisoformat(text)
        except ValueError:
            instant = None
    if instant is None or instant.tzinfo is None or instant.utcoffset() is None:
        raise _invalid("as_of deve essere un istante ISO 8601 con il fuso orario (quello restituito dall'anteprima), "
                       "oppure null.")
    instant = instant.astimezone(timezone.utc).replace(microsecond=0)
    if instant > datetime.now(timezone.utc) + AS_OF_TOLERANCE:
        raise _invalid("as_of è nel futuro: usa l'istante restituito dall'anteprima.")
    return instant


def _seconds(value) -> float:
    """`max_seconds` del corpo: un numero di secondi limitato a MIN_SECONDS..MAX_SECONDS; None = DEFAULT_SECONDS."""
    if value is None:
        return DEFAULT_SECONDS
    if isinstance(value, bool) or not isinstance(value, (int, float)) \
            or (isinstance(value, float) and not math.isfinite(value)):
        raise _invalid(f"max_seconds deve essere un numero di secondi (si limita da {MIN_SECONDS:g} a "
                       f"{MAX_SECONDS:g}).")
    return float(min(MAX_SECONDS, max(MIN_SECONDS, value)))


def _instant(c: Criteria, now: Optional[datetime]) -> datetime:
    """Istante usato come "adesso" da un'anteprima o da un'esecuzione: `c.as_of` se c'è, altrimenti `now` (iniettabile
    nei test) o l'ora attuale; in UTC e al secondo, come `now_iso`."""
    instant = c.as_of or now or datetime.now(timezone.utc)
    if instant.tzinfo is not None:
        instant = instant.astimezone(timezone.utc)
    return instant.replace(microsecond=0)


def _marks(values: tuple) -> str:
    return ",".join("?" * len(values))


def _never_stopping() -> bool:
    return False


class JobCleanup:
    def __init__(self, db: Database, jobs: JobManager, projects: ProjectService) -> None:
        self.db = db
        self.jobs = jobs
        self.projects = projects
        # arresto del server in corso: il ciclo di pulizia si ferma, l'eliminazione di un progetto risponde 503
        # (build_core lo collega a Core.shutting_down)
        self.stopping: Callable[[], bool] = _never_stopping
        self._guard = threading.Lock()
        self._deleting: set[str] = set()  # progetti in eliminazione (sotto `_guard`)
        # dimensioni delle cartelle dei job per (id, updated_at), valide per i job terminati, e cartelle "lente" (non
        # finite in tempo) con il numero d'ordine dell'ultimo tentativo (tutto sotto `_sizes_lock`)
        self._sizes: dict[tuple[str, str], int] = {}
        self._slow: dict[tuple[str, str], int] = {}
        self._tries = 0
        self._sizes_lock = threading.Lock()

    # ------------------------------------------------------------------------------ criteri
    def criteria(self, body: dict) -> Criteria:
        """Valida i criteri di una pulizia (corpo di `/api/jobs/cleanup` e `/cleanup/preview`): nessun valore
        predefinito per ambito e stati; i sì/no devono essere true o false. Ogni valore non valido (anche null o di
        un altro tipo) dà 400 `invalid_cleanup` in italiano; progetto inesistente 404. `as_of` (facoltativo) è
        l'istante restituito dall'anteprima confermata."""
        scope = body.get("scope")
        if scope not in SCOPES:
            raise _invalid("Indica l'ambito della pulizia: «project» (un progetto, con project_id) oppure «all» "
                           "(tutti i progetti).")
        project_id = body.get("project_id")
        if scope == "project":
            if not project_id or not isinstance(project_id, str):
                raise _invalid("Con l'ambito «project» serve l'identificatore del progetto (project_id).")
            project_id = self.projects.get(project_id)["id"]  # 400 identificatore non valido, 404 inesistente
        elif project_id is not None:
            raise _invalid("Con l'ambito «all» (tutti i progetti) non si indica un progetto: togli project_id.")

        statuses = body.get("statuses")
        if not isinstance(statuses, (list, tuple)) or not statuses:
            raise _invalid("Indica almeno uno stato dei job da eliminare: completed, failed o cancelled.")
        chosen: set[str] = set()
        for status in statuses:
            if isinstance(status, str) and status in ACTIVE:
                raise _invalid("I job in coda o in esecuzione non si eliminano: annullali prima.")
            if not isinstance(status, str) or status not in TERMINAL:
                raise _invalid(f"Stato dei job sconosciuto: «{status}» (ammessi: completed, failed, cancelled).")
            chosen.add(status)

        kinds = body.get("kinds")
        if kinds is not None:
            if not isinstance(kinds, (list, tuple)) or not kinds:
                raise _invalid("Indica almeno un tipo di job, oppure nessun filtro sui tipi (null).")
            clean: set[str] = set()
            for kind in kinds:
                try:
                    clean.add(validate_identifier(kind if isinstance(kind, str) else "", "tipo di job"))
                except AppError:
                    raise _invalid(f"Tipo di job non valido: «{kind}».")
            kinds = tuple(sorted(clean))

        age = body.get("older_than_days")
        if age is not None and (isinstance(age, bool) or not isinstance(age, int) or not 0 <= age <= MAX_AGE_DAYS):
            raise _invalid(f"L'età (older_than_days) deve essere un numero intero di giorni da 0 a {MAX_AGE_DAYS}, "
                           "oppure null (qualsiasi età).")

        flags = {}
        for name, default in (("keep_favorites", True), ("keep_tagged", True), ("include_resumable", False)):
            value = body.get(name, default)
            if not isinstance(value, bool):
                raise _invalid(f"{name} deve essere true o false.")
            flags[name] = value
        return Criteria(scope=scope, project_id=project_id, statuses=tuple(sorted(chosen)), kinds=kinds,
                        older_than_days=age, as_of=_as_of(body.get("as_of")), **flags)

    def _where(self, c: Criteria, now: datetime, part: str) -> tuple[list[str], list]:
        """Condizioni SQL (con i loro argomenti) dei job di una parte: "matched" (da eliminare), "protected"
        (risparmiati da stella o etichette) o "resumable" (pipeline riprendibili risparmiate). Le tre sono disgiunte
        e sommano ai job che corrispondono ai filtri (stati, progetto, tipi, età)."""
        where = [f"status IN ({_marks(c.statuses)})"]
        args: list = list(c.statuses)
        if c.project_id is not None:
            where.append("project_id=?")
            args.append(c.project_id)
        if c.kinds is not None:
            where.append(f"kind IN ({_marks(c.kinds)})")
            args.extend(c.kinds)
        if c.older_than_days is not None:
            where.append(AGE)
            args.append((now - timedelta(days=c.older_than_days)).isoformat(timespec="seconds"))
        if c.as_of is not None:
            where.append(UNTIL)
            args.append(c.as_of.isoformat(timespec="seconds"))
        keep =[fragment for on, fragment in ((c.keep_favorites, STARRED), (c.keep_tagged, TAGGED)) if on]
        protected = f"({' OR '.join(keep)})" if keep else None
        if part == "protected":
            where.append(protected or "0")
        else:
            if protected is not None:
                where.append(f"NOT {protected}")
            if part == "resumable":
                where.append(RESUMABLE)
            elif not c.include_resumable:
                where.append(f"NOT {RESUMABLE}")
        return where, args

    def _count(self, c: Criteria, now: datetime, part: str) -> int:
        where, args = self._where(c, now, part)
        return int(self.db.scalar(f"SELECT COUNT(*) FROM jobs WHERE {' AND '.join(where)}", tuple(args)) or 0)

    # ------------------------------------------------------------------------------ anteprima ed esecuzione
    def preview(self, c: Criteria, *, now: Optional[datetime] = None) -> dict:
        """Quanti job (e byte) la pulizia eliminerebbe, per stato e per tipo, quanti ne risparmiano stella ed
        etichette (`protected`) o perché pipeline riprendibili (`resumable`), e i più vecchi (`sample`). Sola lettura:
        una SELECT senza `params` né `result` (grandi per le pipeline), due COUNT e le dimensioni a tempo.

        `as_of` = l'istante (UTC, al secondo) usato come "adesso": il client lo ripassa all'esecuzione confermata,
        che così non elimina job terminati dopo l'anteprima."""
        now = _instant(c, now)
        where, args = self._where(c, now, "matched")
        rows = self.db.query("SELECT rowid AS rid, id, kind, title, status, finished_at, updated_at FROM jobs "
                             f"WHERE {' AND '.join(where)} ORDER BY rowid", tuple(args))
        size, complete = self._bytes(rows, time.monotonic() + BYTES_SECONDS)
        return {
            "matched": len(rows),
            "bytes": size,
            "bytes_complete": complete,
            "by_status": dict(Counter(r["status"] for r in rows)),
            "by_kind": dict(Counter(r["kind"] for r in rows)),
            "protected": self._count(c, now, "protected") if c.keep_favorites or c.keep_tagged else 0,
            "resumable": 0 if c.include_resumable else self._count(c, now, "resumable"),
            "sample": [{key: r[key] for key in ("id", "kind", "title", "status", "finished_at")}
                       for r in rows[:SAMPLE_SIZE]],
            "as_of": now.isoformat(timespec="seconds"),
        }

    def run(self, c: Criteria, *, max_seconds: float = DEFAULT_SECONDS, now: Optional[datetime] = None) -> dict:
        """Elimina i job che corrispondono ai criteri, dal più vecchio (ordine di creazione), per al più
        `max_seconds` secondi (limitati a MIN_SECONDS..MAX_SECONDS; il tempo si controlla dopo ogni tentativo, quindi
        ogni chiamata ne esamina almeno uno).

        Senza stato e sicura da ripetere: un cursore sul rowid evita di riesaminare un job nella stessa chiamata, e
        `remaining` conta solo i job corrispondenti non ancora esaminati, non i saltati (ripresi, in uso, già
        eliminati, con una stella aggiunta nel frattempo), così un job non eliminabile non tiene vivo il ciclo del
        client. Il lock del database non si tiene mai durante un'eliminazione.

        Con `c.as_of` (l'istante dell'anteprima confermata) l'età si calcola da lì e un job terminato dopo, anche
        mentre il ciclo gira o perché ripreso e terminato di nuovo, non si elimina: la pulizia resta dentro l'insieme
        mostrato. Senza, vale l'adesso di ogni chiamata (lo svuotamento di un progetto da eliminare, che deve essere
        completo).
        `max_seconds` non numerico: 400 `invalid_cleanup`.
        """
        deadline = time.monotonic() + _seconds(max_seconds)
        now = _instant(c, now)
        where, args = self._where(c, now, "matched")
        condition = " AND ".join(where)
        predicate = (condition, tuple(args))  # rivalutato nel DELETE di ogni job
        cursor = deleted = skipped = errors = freed = leftover = 0
        stop = exhausted = False
        while not stop:
            rows = self.db.query(f"SELECT rowid AS rid, id FROM jobs WHERE rowid > ? AND {condition} "
                                 "ORDER BY rowid LIMIT ?", (cursor, *args, BATCH))
            if not rows:
                exhausted = True
                break
            for row in rows:
                if self.stopping():
                    stop = True
                    break
                cursor = row["rid"]  # esaminato: non rientra più in `remaining`
                if self.jobs.busy(row["id"]):  # esecuzione o ripresa in corso: niente attesa
                    skipped += 1
                    continue
                try:
                    out = self.jobs.delete(row["id"], only_if=predicate)
                    deleted += 1
                    freed += out.freed_bytes
                    leftover += int(out.leftover)
                except (Conflict, NotFound):  # ripreso, in uso, già eliminato o non più corrispondente
                    skipped += 1
                except Exception as exc:
                    errors += 1
                    print(f"[BioLoom] pulizia dei job: {row['id']} non eliminato: {exc}")
                if time.monotonic() >= deadline:  # dopo un tentativo: ogni chiamata ne esamina almeno uno
                    stop = True
                    break
        remaining = 0 if exhausted else int(self.db.scalar(
            f"SELECT COUNT(*) FROM jobs WHERE rowid > ? AND {condition}", (cursor, *args)) or 0)
        return {"deleted": deleted, "skipped": skipped, "errors": errors, "freed_bytes": freed,
                "leftover": leftover, "remaining": remaining}

    def _bytes(self, rows: list[dict], deadline: float) -> tuple[int, bool]:
        """Byte delle cartelle dei job di `rows` (con `id` e `updated_at`) fino a `deadline`: (byte, completo).

        Le dimensioni misurate per intero si ricordano per (id, updated_at), che per un job terminato non cambia, e si
        sommano sempre. Il tempo va alle cartelle non ancora misurate: prima quelle mai provate, poi quelle che una
        volta non sono finite in tempo ("lente"), dalla provata meno di recente. Una cartella interrotta dal tempo
        conta con i byte letti fino a lì e il risultato è incompleto (un minimo); quelle non raggiunte contano 0.
        Ripetendo l'anteprima, quindi, diventa esatta la dimensione di ogni cartella che da sola si misura entro
        BYTES_SECONDS (a ogni chiamata almeno una cartella viene misurata per intero o passa in fondo); una cartella
        che da sola richiede di più resta parziale a ogni chiamata e il risultato resta incompleto.
        """
        total, complete = 0, True
        pending: list[tuple[tuple[bool, int], tuple[str, str]]] = []
        with self._sizes_lock:
            for row in rows:
                key = (row["id"], row.get("updated_at") or "")
                known = self._sizes.get(key)
                if known is not None:
                    total += known
                else:
                    tried = self._slow.get(key)
                    pending.append(((tried is not None, tried or 0), key))
        pending.sort(key=lambda item: item[0])  # stabile: a parità, l'ordine delle righe
        for _order, key in pending:
            if time.monotonic() >= deadline:  # tempo finito: le cartelle rimaste non si misurano
                complete = False
                break
            size, done = dir_size_until(self.jobs.workdir(key[0]), deadline)
            total += size
            with self._sizes_lock:
                if len(self._sizes) >= MAX_MEMO or len(self._slow) >= MAX_MEMO:
                    self._sizes.clear()
                    self._slow.clear()
                if done:
                    self._sizes[key] = size
                    self._slow.pop(key, None)
                else:  # tempo scaduto a metà: i byte sono un minimo, la prossima volta va in fondo
                    complete = False
                    self._tries += 1
                    self._slow[key] = self._tries
        return total, complete

    # ------------------------------------------------------------------------------ progetti
    def project_usage(self, project_id: str) -> dict:
        """Cosa va via eliminando il progetto (anteprima della conferma): sequenze, documenti, job per stato, con
        stella, con etichette, pipeline riprendibili, e byte delle cartelle dei job. Conteggi da SQL, su tutti i job
        del progetto; i byte con un tempo massimo (`bytes_complete` false = "almeno")."""
        project = self.projects.get(project_id)
        pid = project["id"]

        def count(table: str, condition: str = "") -> int:
            where = f" AND {condition}" if condition else ""
            return int(self.db.scalar(f"SELECT COUNT(*) FROM {table} WHERE project_id=?{where}", (pid,)) or 0)

        by_status = {r["status"]: int(r["n"]) for r in self.db.query(
            "SELECT status, COUNT(*) AS n FROM jobs WHERE project_id=? GROUP BY status ORDER BY status", (pid,))}
        rows = self.db.query("SELECT id, updated_at FROM jobs WHERE project_id=? ORDER BY rowid", (pid,))
        size, complete = self._bytes(rows, time.monotonic() + BYTES_SECONDS)
        return {
            "id": pid, "name": project["name"], "created_at": project["created_at"],
            "updated_at": project["updated_at"],
            "sequences": count("sequences"),
            "documents": count("documents"),
            "jobs": {
                "total": sum(by_status.values()),
                "active": sum(n for status, n in by_status.items() if status in ACTIVE),
                "starred": count("jobs", STARRED),
                "tagged": count("jobs", TAGGED),
                "resumable": count("jobs", RESUMABLE),
                "by_status": by_status,
            },
            "bytes": size,
            "bytes_complete": complete,
        }

    def delete_project(self, project_id: str) -> dict:
        """Elimina il progetto con tutti i suoi job, sequenze, documenti, etichette e stelle; completa e sincrona.

        I job attivi si contano in SQL su tutto il progetto all'inizio di ogni passata: alla prima, se ce ne sono, nulla
        si elimina; a una passata successiva (un job inviato e partito nel frattempo) l'eliminazione si ferma lì, con i
        job già eliminati che restano eliminati. In entrambi i casi 409 `project_active_jobs` con `details`
        {"active": n, "jobs_deleted": k} (k = job eliminati da questa chiamata, 0 alla prima passata). I job si
        eliminano dal più vecchio, rileggendo sempre dall'inizio (mai OFFSET); la riga del progetto si cancella solo se
        non resta alcun job (`delete_if_unused`, NOT EXISTS), e `JobManager.submit` rifiuta i progetti inesistenti:
        nessun job orfano. Un progetto che riceve job durante l'eliminazione si riprova fino a PROJECT_PASSES volte;
        ogni passata esamina al più i job che c'erano al suo inizio, così una sorgente continua di job non la tiene
        in ciclo. Un job ripreso o in uso interrompe con 409 `project_busy`: i job già eliminati restano eliminati e
        il messaggio lo dice (stato coerente, ripetibile). Una seconda richiesta sullo stesso progetto mentre la prima
        gira riceve subito 409 `delete_in_progress`; progetti diversi non si aspettano a vicenda.
        """
        pid = self.projects.get(project_id)["id"]  # 400 identificatore non valido, 404 inesistente
        with self._guard:
            if pid in self._deleting:
                raise Conflict("L'eliminazione di questo progetto è già in corso: attendi che finisca.",
                               code="delete_in_progress")
            self._deleting.add(pid)
        deleted = freed = leftover = 0
        try:
            for _ in range(PROJECT_PASSES):
                active = int(self.db.scalar(f"SELECT COUNT(*) FROM jobs WHERE project_id=? AND {ACTIVE_SQL}",
                                            (pid,)) or 0)
                if active:
                    done = f" ({deleted} job già eliminati)" if deleted else ""
                    raise Conflict(f"Il progetto ha {active} job in coda o in esecuzione: annullali prima di "
                                   f"eliminarlo{done}.", code="project_active_jobs",
                                   details={"active": active, "jobs_deleted": deleted})
                budget = int(self.db.scalar("SELECT COUNT(*) FROM jobs WHERE project_id=?", (pid,)) or 0)
                while budget > 0:
                    ids = [r["id"] for r in self.db.query(
                        "SELECT id FROM jobs WHERE project_id=? ORDER BY rowid LIMIT ?",
                        (pid, min(PROJECT_BATCH, budget)))]
                    if not ids:
                        break
                    for job_id in ids:
                        if self.stopping():
                            raise AppError(f"Arresto del server in corso: eliminati {deleted} job, il progetto "
                                           "resta. Riprova dopo il riavvio.", code="shutting_down", status=503)
                        budget -= 1  # esaminato: eliminato, sparito o motivo dell'interruzione
                        try:
                            out = self.jobs.delete(job_id)
                        except NotFound:
                            continue
                        except Conflict as exc:
                            raise Conflict(_busy_message(deleted, exc.message), code="project_busy",
                                           details={"jobs_deleted": deleted})
                        deleted += 1
                        freed += out.freed_bytes
                        leftover += int(out.leftover)
                gone, replacement = self.projects.delete_if_unused(pid)
                if gone:
                    return {"deleted": pid, "jobs_deleted": deleted, "freed_bytes": freed, "leftover": leftover,
                            "replacement": replacement}
            raise Conflict(f"Il progetto continua a ricevere nuovi job: eliminati {deleted} job, ma il progetto resta. "
                           "Riprova tra poco.", code="project_busy", details={"jobs_deleted": deleted})
        finally:
            with self._guard:
                self._deleting.discard(pid)


def _busy_message(deleted: int, reason: str) -> str:
    """Messaggio del 409 `project_busy` quando un job non si può eliminare: dice che il progetto resta."""
    head = f"Eliminati {deleted} job, ma il progetto non è stato eliminato" if deleted else \
        "Il progetto non è stato eliminato"
    return f"{head}: un suo job è in uso o è stato ripreso nel frattempo ({reason.rstrip('.')}). Riprova tra poco."
