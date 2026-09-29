"""Persistenza SQLite (progetti, sequenze, documenti, job, elementi della pipeline)."""

from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Optional

SCHEMA_VERSION = 1

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
"""

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


class Database:
    """Connessione SQLite unica del processo, serializzata da un lock.

    `locking_mode=EXCLUSIVE` impostato prima del WAL: SQLite non usa la memoria condivisa (file `-shm`),
    che non funziona in modo affidabile sui bind mount di Docker Desktop e sulle chiavette exFAT, e il
    file resta bloccato finché il server è attivo, così due istanze non possono usare la stessa
    cartella dati.
    """

    def __init__(self, path: Path, busy_timeout: float = 5.0) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
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
                self._conn.executescript(SCHEMA)
                self._conn.execute(
                    "INSERT OR REPLACE INTO meta(key, value) VALUES ('schema_version', ?)", (str(SCHEMA_VERSION),)
                )
            except sqlite3.OperationalError as exc:
                self._conn.close()
                if "locked" in str(exc).lower():
                    raise DatabaseInUse(
                        f"La cartella dati {path.parent} è già usata da un'altra istanza di BioLoom: "
                        "chiudi l'altra istanza oppure usa un'altra cartella dati.") from exc
                raise

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

    def close(self) -> None:
        with self._lock:
            self._conn.close()
