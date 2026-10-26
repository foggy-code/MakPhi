"""Persistenza SQLite (progetti, sequenze, documenti, job, elementi della pipeline, etichette e preferiti).

Lo schema ha una versione (`meta.schema_version`). `SCHEMA` è lo schema di base, quello della versione 1; le
versioni successive si ottengono con le migrazioni di `MIGRATIONS`, applicate all'apertura in ordine (vedi
`Database._migrate`): copia di sicurezza del file prima di toccarlo, una transazione per migrazione e versione
scritta nella stessa transazione, quindi aggiornata solo se la migrazione riesce.
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterator, Optional

# schema di base: tabelle create da `SCHEMA` (la versione 1, dalla quale partono le migrazioni)
BASE_SCHEMA_VERSION = 1

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);

CREATE TABLE IF NOT EXISTS projects (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sequences (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    sequence TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT 'manual',
    meta TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_sequences_project ON sequences(project_id);

CREATE TABLE IF NOT EXISTS documents (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    kind TEXT NOT NULL,
    content TEXT NOT NULL,
    meta TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_documents_project ON documents(project_id);

CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    title TEXT NOT NULL DEFAULT '',
    project_id TEXT,
    status TEXT NOT NULL,
    progress REAL NOT NULL DEFAULT 0,
    stage TEXT NOT NULL DEFAULT '',
    params TEXT NOT NULL DEFAULT '{}',
    result TEXT,
    error TEXT,
    created_at TEXT NOT NULL,
    started_at TEXT,
    finished_at TEXT,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_jobs_project ON jobs(project_id, created_at);
CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status);

CREATE TABLE IF NOT EXISTS pipeline_items (
    job_id TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    idx INTEGER NOT NULL,
    name TEXT NOT NULL,
    sequence TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    stage TEXT NOT NULL DEFAULT '',
    template TEXT,
    template_source TEXT,
    identity REAL,
    coverage REAL,
    evalue REAL,
    twilight INTEGER NOT NULL DEFAULT 0,
    best_model TEXT,
    dope REAL,
    ga341 REAL,
    grade TEXT,
    grade_score REAL,
    rama_favored REAL,
    clashscore REAL,
    metrics TEXT NOT NULL DEFAULT '{}',
    error TEXT,
    started_at TEXT,
    finished_at TEXT,
    PRIMARY KEY (job_id, idx)
);
-- conteggi per stato e tempo residuo di pipeline con centinaia di migliaia di elementi, senza leggere le righe (con
-- le sequenze). Un indice non cambia i dati: sta nello schema di base, creato anche sui database esistenti all'avvio,
-- senza alzare la versione (una versione precedente di BioLoom apre ancora il file).
CREATE INDEX IF NOT EXISTS idx_pipeline_items_status ON pipeline_items(job_id, status, finished_at, started_at);
"""


@dataclass(frozen=True)
class Migration:
    """Passaggio dalla versione `version - 1` a `version`: istruzioni SQL eseguite una per una nella stessa
    transazione (niente `executescript`, che chiuderebbe la transazione).

    Le istruzioni devono essere idempotenti (`IF NOT EXISTS`): una versione precedente di BioLoom, aperta sulla
    stessa cartella dati, riscriveva `schema_version` a 1 a ogni avvio, e la migrazione può quindi ripartire su
    tabelle che esistono già. Una migrazione che ricostruisce tabelle deve gestire da sé `foreign_keys` (il PRAGMA
    non ha effetto dentro una transazione).
    """

    version: int
    description: str
    statements: tuple[str, ...]


# entità che possono avere etichette e stella (vincolo CHECK delle tabelle e rotte /api/tags, /api/favorites)
LABEL_ENTITIES = ("project", "job", "sequence", "template")
_ENTITY_CHECK = "entity_type IN (" + ", ".join(f"'{e}'" for e in LABEL_ENTITIES) + ")"


def _cleanup_trigger(table: str, entity: str) -> str:
    """Etichette e stella spariscono con l'entità, qualunque sia il percorso dell'eliminazione: rotta, eliminazione
    del progetto (i suoi job uno per uno) o ON DELETE CASCADE (sequenze del progetto: SQLite esegue i trigger anche
    sulle righe eliminate in cascata). I template stanno su file: li pulisce la libreria (services/labels.py)."""
    return (f"CREATE TRIGGER IF NOT EXISTS {table}_labels_cleanup AFTER DELETE ON {table} BEGIN "
            f"DELETE FROM tags WHERE entity_type='{entity}' AND entity_id=OLD.id; "
            f"DELETE FROM favorites WHERE entity_type='{entity}' AND entity_id=OLD.id; END")


MIGRATIONS: tuple[Migration, ...] = (
    Migration(2, "etichette e preferiti", (
        f"""CREATE TABLE IF NOT EXISTS tags (
            entity_type TEXT NOT NULL CHECK ({_ENTITY_CHECK}),
            entity_id TEXT NOT NULL,
            tag TEXT NOT NULL,
            created_at TEXT NOT NULL,
            PRIMARY KEY (entity_type, entity_id, tag)
        ) WITHOUT ROWID""",
        "CREATE INDEX IF NOT EXISTS idx_tags_tag ON tags(tag, entity_type)",
        f"""CREATE TABLE IF NOT EXISTS favorites (
            entity_type TEXT NOT NULL CHECK ({_ENTITY_CHECK}),
            entity_id TEXT NOT NULL,
            created_at TEXT NOT NULL,
            PRIMARY KEY (entity_type, entity_id)
        ) WITHOUT ROWID""",
        _cleanup_trigger("projects", "project"),
        _cleanup_trigger("sequences", "sequence"),
        _cleanup_trigger("jobs", "job"),
    )),
)

SCHEMA_VERSION = MIGRATIONS[-1].version if MIGRATIONS else BASE_SCHEMA_VERSION

# Ricerca dei job per struttura (services/compute.job_sources: report, pagina Qualità). Con centinaia di migliaia di
# job (139.296 QMEAN di una pipeline sul server) ogni ricerca ordinava tutti i job completati: il primo indice la
# restringe al tipo di job, il secondo, sulle espressioni JSON, al job e al file della sorgente.
OPTIONAL_INDEXES: tuple[str, ...] = (
    "CREATE INDEX IF NOT EXISTS idx_jobs_kind ON jobs(kind, status, finished_at)",
    "CREATE INDEX IF NOT EXISTS idx_jobs_source ON jobs(kind, json_extract(params, '$.source.job_id'), "
    "json_extract(params, '$.source.file'))",
)

JSON_COLUMNS = {"params", "result", "meta", "metrics"}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _decode(row: sqlite3.Row) -> dict:
    out = dict(row)
    for key in JSON_COLUMNS & out.keys():
        value = out[key]
        if isinstance(value, str):
            try:
                out[key] = json.loads(value)
            except ValueError:
                pass
    return out


def encode_json(value: Any) -> Optional[str]:
    return None if value is None else json.dumps(value, ensure_ascii=False)


class DatabaseInUse(RuntimeError):
    """La cartella dati è già aperta da un'altra istanza di BioLoom."""


class MigrationError(RuntimeError):
    """Migrazione dello schema non riuscita, oppure database di una versione più recente di BioLoom: il file resta
    com'era (la transazione della migrazione fallita è annullata)."""


BACKUP_DIR = "backups"


class Database:
    """Connessione SQLite unica del processo, serializzata da un lock.

    `locking_mode=EXCLUSIVE` impostato prima del WAL: SQLite non usa la memoria condivisa (file `-shm`),
    che non funziona in modo affidabile sui bind mount di Docker Desktop e sulle chiavette exFAT, e il
    file resta bloccato finché il server è attivo, così due istanze non possono usare la stessa
    cartella dati.

    All'apertura lo schema si porta all'ultima versione (`_migrate`). `migrated` elenca le migrazioni applicate
    (versione, descrizione) e `backup` è la copia di sicurezza fatta prima, None se non serviva.
    """

    def __init__(self, path: Path, busy_timeout: float = 5.0) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self.migrated: list[tuple[int, str]] = []
        self.backup: Optional[Path] = None
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None,
                                     timeout=busy_timeout)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            try:
                self._conn.execute("PRAGMA locking_mode=EXCLUSIVE")
                mode = self._conn.execute("PRAGMA journal_mode=WAL").fetchone()[0]
                if str(mode).lower() != "wal":  # filesystem senza WAL: giornale classico
                    self._conn.execute("PRAGMA journal_mode=DELETE")
                self._conn.execute("PRAGMA foreign_keys=ON")
                self._conn.execute("PRAGMA synchronous=NORMAL")
                self._migrate()
                self._optional_indexes()
            except sqlite3.OperationalError as exc:
                self._conn.close()
                if "locked" in str(exc).lower():
                    raise DatabaseInUse(
                        f"La cartella dati {path.parent} è già usata da un'altra istanza di BioLoom: "
                        "chiudi l'altra istanza oppure usa un'altra cartella dati.") from exc
                raise
            except BaseException:
                self._conn.close()
                raise

    # ------------------------------------------------------------------ versioni dello schema
    def schema_version(self) -> int:
        """Versione dello schema scritta nel file (0 se manca: database mai inizializzato)."""
        with self._lock:
            return self._stored_version()

    def _stored_version(self) -> int:
        try:
            row = self._conn.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
        except sqlite3.OperationalError:  # nessuna tabella meta
            return 0
        try:
            return int(row[0]) if row else 0
        except (TypeError, ValueError):
            return 0

    def _migrate(self) -> None:
        """Porta lo schema all'ultima versione, senza perdere dati.

        1. Database nuovo (nessuna tabella): schema di base e migrazioni, senza copia di sicurezza.
        2. Database esistente con migrazioni da applicare: prima una copia coerente del file con l'API di backup di
           SQLite (`_backup`; legge anche le pagine ancora nel WAL), verificata con `quick_check`; senza copia non
           si migra.
        3. Ogni migrazione in una transazione con la nuova `schema_version`: se un'istruzione fallisce la
           transazione si annulla, il file resta alla versione precedente (DDL compreso: in SQLite è
           transazionale) e l'avvio si ferma con `MigrationError`.

        Un database di una versione più recente di BioLoom (schema più alto di quello noto) non si apre: le sue
        tabelle potrebbero avere vincoli che questa versione non rispetta.
        """
        migrations = MIGRATIONS
        latest = migrations[-1].version if migrations else BASE_SCHEMA_VERSION
        fresh = not self._conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' LIMIT 1").fetchone()
        # Schema di base idempotente (IF NOT EXISTS), come a ogni avvio della versione 1. Un file della versione 1
        # ha sempre la riga `schema_version`; senza (avvio interrotto prima di scriverla) è la versione di base.
        self._conn.executescript(SCHEMA)
        current = self._stored_version()
        if not current:
            current = BASE_SCHEMA_VERSION
            self._set_version(current)
        if current > latest:
            raise MigrationError(
                f"Il database {self.path} è stato aggiornato da una versione più recente di BioLoom (schema "
                f"{current}, questa versione conosce fino allo schema {latest}): aggiorna BioLoom.")
        pending = [m for m in migrations if m.version > current]
        if not pending:
            return
        if not fresh:
            self.backup = self._backup(current)
        for migration in pending:
            try:
                self._conn.execute("BEGIN")
                for sql in migration.statements:
                    self._conn.execute(sql)
                self._set_version(migration.version)
                self._conn.execute("COMMIT")
            except BaseException as exc:
                if self._conn.in_transaction:
                    self._conn.execute("ROLLBACK")
                if isinstance(exc, sqlite3.OperationalError) and "locked" in str(exc).lower():
                    raise
                where = f" (copia di sicurezza: {self.backup})" if self.backup else ""
                raise MigrationError(
                    f"Aggiornamento del database allo schema {migration.version} ({migration.description}) non "
                    f"riuscito: {exc}. Il database è rimasto allo schema {self._stored_version()}{where}.") from exc
            self.migrated.append((migration.version, migration.description))

    def _optional_indexes(self) -> None:
        """Indici che velocizzano soltanto (`OPTIONAL_INDEXES`): fuori dalle migrazioni, perché non cambiano lo schema
        (niente copia di sicurezza) e una versione precedente di BioLoom li ignora. Uno che non si può creare
        (SQLite senza le funzioni JSON) si salta: le ricerche funzionano lo stesso, più lente."""
        for sql in OPTIONAL_INDEXES:
            try:
                self._conn.execute(sql)
            except sqlite3.OperationalError as exc:
                if "locked" in str(exc).lower():
                    raise

    def _set_version(self, version: int) -> None:
        self._conn.execute("INSERT OR REPLACE INTO meta(key, value) VALUES ('schema_version', ?)", (str(version),))

    def _backup(self, version: int) -> Path:
        """Copia coerente del database in `backups/bioloom.schema<N>.<data>.db`, accanto al file.

        L'API di backup di SQLite copia le pagine attraverso questa connessione, quindi anche quelle ancora nel WAL,
        e funziona con il lock esclusivo (una copia del file con `shutil` le perderebbe). La copia si scrive con un
        nome temporaneo, si verifica con `PRAGMA quick_check` e solo allora prende il nome definitivo: una copia a
        metà (disco pieno) non viene scambiata per buona e la migrazione non parte.
        """
        folder = self.path.parent / BACKUP_DIR
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        final = folder / f"{self.path.stem}.schema{version}.{stamp}.db"
        partial = folder / f".{final.name}.tmp"
        try:
            folder.mkdir(parents=True, exist_ok=True)
            target = sqlite3.connect(str(partial))
            try:
                self._conn.backup(target)
                check = target.execute("PRAGMA quick_check").fetchone()[0]
            finally:
                target.close()
            if str(check).lower() != "ok":
                raise sqlite3.DatabaseError(f"copia non integra ({check})")
            os.replace(partial, final)
        except (OSError, sqlite3.Error) as exc:
            for leftover in (partial, Path(f"{partial}-journal")):
                try:
                    leftover.unlink(missing_ok=True)
                except OSError:
                    pass
            raise MigrationError(
                f"Copia di sicurezza del database non riuscita ({exc}): il database (schema {version}) non è stato "
                "aggiornato. Libera spazio nella cartella dati e riavvia BioLoom.") from exc
        return final

    def execute(self, sql: str, params: tuple | dict = ()) -> int:
        with self._lock:
            cur = self._conn.execute(sql, params)
            return cur.rowcount

    def executemany(self, sql: str, rows: list) -> None:
        with self._lock:
            self._conn.execute("BEGIN")
            try:
                self._conn.executemany(sql, rows)
                self._conn.execute("COMMIT")
            except BaseException:
                self._conn.execute("ROLLBACK")
                raise

    def query(self, sql: str, params: tuple | dict = ()) -> list[dict]:
        with self._lock:
            return [_decode(r) for r in self._conn.execute(sql, params).fetchall()]

    def query_one(self, sql: str, params: tuple | dict = ()) -> Optional[dict]:
        with self._lock:
            row = self._conn.execute(sql, params).fetchone()
            return _decode(row) if row else None

    def scalar(self, sql: str, params: tuple | dict = ()) -> Any:
        with self._lock:
            row = self._conn.execute(sql, params).fetchone()
            return row[0] if row else None

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            self._conn.execute("BEGIN")
            try:
                yield self._conn
                self._conn.execute("COMMIT")
            except BaseException:
                self._conn.execute("ROLLBACK")
                raise

    def create_function(self, name: str, nargs: int, fn: Callable[..., Any]) -> None:
        """Funzione SQL scritta in Python, valida per questa connessione (es. il confronto senza accenti della
        ricerca globale): mai nei trigger o nello schema del file, che altre connessioni non conoscono."""
        with self._lock:
            self._conn.create_function(name, nargs, fn, deterministic=True)

    def close(self) -> None:
        with self._lock:
            self._conn.close()
