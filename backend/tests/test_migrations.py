"""Migrazioni dello schema SQLite (F8b, core/db.py): da un database della versione 1 alla 2 senza perdite.

Il database della versione 1 si genera dal codice dello schema (`db.SCHEMA`, lo schema di base) con le stesse
impostazioni della versione 1 (WAL con lock esclusivo) e dati in tutte le tabelle, JSON compresi. Per la copia di
sicurezza "coerente" una parte dei dati resta solo nel WAL, come dopo un arresto brusco del server.
"""

from __future__ import annotations

import shutil
import sqlite3
import threading

import pytest
from fastapi.testclient import TestClient

from bioloom.app import create_app
from bioloom.config import Paths
from bioloom.core import db as db_module
from bioloom.core.db import BACKUP_DIR, Database, DatabaseInUse, Migration, MigrationError

from .conftest import HUMAN_UB, YEAST_UB

V1_TABLES = ("meta", "projects", "sequences", "documents", "jobs", "pipeline_items")


def _v1_rows(conn: sqlite3.Connection) -> None:
    now = "2026-01-01T10:00:00+00:00"
    conn.execute("INSERT INTO projects VALUES ('p1', 'Chinasi', 'Chinasi di lievito', ?, ?)", (now, now))
    conn.execute("INSERT INTO projects VALUES ('p2', 'Proteína ùmana', '', ?, ?)", (now, now))
    conn.execute("INSERT INTO sequences VALUES ('s1', 'p1', 'ub_h', ?, 'uniprot', ?, ?)",
                 (HUMAN_UB, '{"accession": "P0CG48", "protein": "Polyubiquitin-C"}', now))
    conn.execute("INSERT INTO sequences VALUES ('s2', 'p1', 'ub_y', ?, 'fasta', '{}', ?)", (YEAST_UB, now))
    conn.execute("INSERT INTO documents VALUES ('d1', 'p1', 'aln', 'pir', '>P1;a\nsequence:a::::::::\nAC*\n', "
                 "'{\"templates\": [{\"id\": \"1UBQ\"}]}', ?, ?)", (now, now))
    conn.execute("INSERT INTO jobs(id, kind, title, project_id, status, progress, stage, params, result, error, "
                 "created_at, started_at, finished_at, updated_at) VALUES ('j1', 'pipeline', 'Pipeline ubiquitine', "
                 "'p1', 'completed', 1, 'Completato', '{\"engine\": \"rcsb\"}', '{\"total\": 2}', NULL, ?, ?, ?, ?)",
                 (now, now, now, now))
    conn.execute("INSERT INTO jobs(id, kind, title, project_id, status, created_at, updated_at) "
                 "VALUES ('j2', 'model', 'Modello', 'p2', 'failed', ?, ?)", (now, now))
    conn.execute("INSERT INTO pipeline_items(job_id, idx, name, sequence, status, template, identity, metrics) "
                 "VALUES ('j1', 0, 'ub_y', ?, 'completed', '1UBQ', 96.1, '{\"search_cached_at\": null}')", (YEAST_UB,))


def make_v1_database(path, *, wal_only: bool = False) -> None:
    """Database della versione 1 dal codice dello schema di base. `wal_only`: le ultime scritture restano nel WAL
    (nessun checkpoint), come dopo un arresto brusco; il file si copia mentre la connessione è aperta."""
    path.parent.mkdir(parents=True, exist_ok=True)
    work = path.with_name(path.name + ".build") if wal_only else path
    conn = sqlite3.connect(str(work), isolation_level=None)
    conn.execute("PRAGMA locking_mode=EXCLUSIVE")
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(db_module.SCHEMA)
    conn.execute("INSERT OR REPLACE INTO meta(key, value) VALUES ('schema_version', '1')")
    if wal_only:
        conn.execute("PRAGMA wal_autocheckpoint=0")
    _v1_rows(conn)
    if wal_only:
        # copia "a caldo": il file principale ha solo lo schema, le righe sono nel WAL
        shutil.copyfile(work, path)
        shutil.copyfile(f"{work}-wal", f"{path}-wal")
    conn.close()


def dump(path) -> dict:
    """Tutte le righe delle tabelle della versione 1, per confrontare prima e dopo."""
    conn = sqlite3.connect(str(path))
    try:
        return {t: conn.execute(f"SELECT * FROM {t} ORDER BY rowid").fetchall() for t in V1_TABLES if t != "meta"}
    finally:
        conn.close()


def schema_objects(path) -> set[tuple[str, str]]:
    conn = sqlite3.connect(str(path))
    try:
        return {(r[0], r[1]) for r in conn.execute("SELECT type, name FROM sqlite_master")}
    finally:
        conn.close()


def stored_version(path) -> str:
    conn = sqlite3.connect(str(path))
    try:
        return conn.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()[0]
    finally:
        conn.close()


@pytest.fixture
def v1(tmp_path):
    path = tmp_path / "data" / "bioloom.db"
    make_v1_database(path)
    reference = tmp_path / "reference.db"
    shutil.copyfile(path, reference)
    return path, dump(reference)


def test_v1_fixture_matches_v1_schema(v1):
    path, before = v1
    assert stored_version(path) == "1"
    assert ("table", "tags") not in schema_objects(path)
    assert len(before["projects"]) == 2 and len(before["pipeline_items"]) == 1


def test_migration_v1_to_v2_keeps_data_and_backs_up(v1):
    path, before = v1
    db = Database(path)
    try:
        assert db.migrated == [(2, "etichette e preferiti")]
        assert db.schema_version() == db_module.SCHEMA_VERSION == 2
        # nuove tabelle e trigger di pulizia
        names = {r["name"] for r in db.query("SELECT name FROM sqlite_master")}
        assert {"tags", "favorites", "idx_tags_tag", "projects_labels_cleanup", "sequences_labels_cleanup",
                "jobs_labels_cleanup"} <= names
        # i dati JSON si leggono come prima
        seq = db.query_one("SELECT * FROM sequences WHERE id='s1'")
        assert seq["meta"]["accession"] == "P0CG48" and seq["sequence"] == HUMAN_UB
        assert db.query_one("SELECT result FROM jobs WHERE id='j1'")["result"] == {"total": 2}
        backup = db.backup
    finally:
        db.close()
    assert dump(path) == before  # nessuna riga persa né cambiata
    # copia di sicurezza: schema 1, stessi dati, integra
    assert backup is not None and backup.parent == path.parent / BACKUP_DIR and backup.exists()
    assert backup.name.startswith("bioloom.schema1.") and backup.suffix == ".db"
    assert stored_version(backup) == "1" and dump(backup) == before
    assert ("table", "tags") not in schema_objects(backup)
    assert not list(backup.parent.glob(".*.tmp"))  # nessun file temporaneo lasciato


def test_backup_includes_rows_still_in_the_wal(tmp_path):
    """Arresto brusco della versione 1: le righe sono solo nel WAL. La copia (API di backup) le contiene, una copia
    del solo file principale no."""
    path = tmp_path / "data" / "bioloom.db"
    make_v1_database(path, wal_only=True)
    assert (path.parent / "bioloom.db-wal").stat().st_size > 0
    main_only = tmp_path / "main_only.db"
    shutil.copyfile(path, main_only)
    conn = sqlite3.connect(str(main_only))
    try:  # il file principale da solo non ha le righe (nemmeno le tabelle: anche lo schema è nel WAL)
        assert not conn.execute("SELECT name FROM sqlite_master WHERE name='projects'").fetchone() or \
            conn.execute("SELECT COUNT(*) FROM projects").fetchone()[0] == 0
    finally:
        conn.close()
    db = Database(path)
    try:
        backup = db.backup
        assert db.scalar("SELECT COUNT(*) FROM projects") == 2
    finally:
        db.close()
    rows = dump(backup)
    assert len(rows["projects"]) == 2 and len(rows["sequences"]) == 2 and len(rows["pipeline_items"]) == 1


def test_reopening_is_idempotent(v1):
    path, before = v1
    Database(path).close()
    backups = sorted((path.parent / BACKUP_DIR).iterdir())
    db = Database(path)
    try:
        assert db.migrated == [] and db.backup is None
        db.execute("INSERT INTO tags VALUES ('project', 'p1', 'urgente', '2026-01-02T00:00:00+00:00')")
    finally:
        db.close()
    assert sorted((path.parent / BACKUP_DIR).iterdir()) == backups  # nessuna nuova copia


def test_migration_reapplied_after_old_version_reset_schema_version(v1):
    """La versione 1 di BioLoom riscriveva schema_version=1 a ogni avvio: aperta dopo la 2, il database torna
    "versione 1" con le tabelle nuove già presenti. La migrazione riparte senza errori e senza perdere etichette."""
    path, before = v1
    db = Database(path)
    db.execute("INSERT INTO tags VALUES ('project', 'p1', 'urgente', '2026-01-02T00:00:00+00:00')")
    db.execute("INSERT INTO favorites VALUES ('sequence', 's1', '2026-01-02T00:00:00+00:00')")
    db.close()
    conn = sqlite3.connect(str(path))  # avvio della versione 1: schema di base e versione 1
    conn.executescript(db_module.SCHEMA)
    conn.execute("INSERT OR REPLACE INTO meta(key, value) VALUES ('schema_version', '1')")
    conn.commit()
    conn.close()
    db = Database(path)
    try:
        assert db.migrated == [(2, "etichette e preferiti")] and db.schema_version() == 2
        assert db.query("SELECT entity_id, tag FROM tags") == [{"entity_id": "p1", "tag": "urgente"}]
        assert db.scalar("SELECT COUNT(*) FROM favorites") == 1
    finally:
        db.close()
    assert dump(path) == before


def test_failed_migration_leaves_database_unchanged(v1, monkeypatch):
    path, before = v1
    real = db_module.MIGRATIONS[0]
    broken = Migration(2, "etichette e preferiti", real.statements[:3] + ("CREATE TABLE rotta (",))
    monkeypatch.setattr(db_module, "MIGRATIONS", (broken,))
    with pytest.raises(MigrationError) as err:
        Database(path)
    assert "schema 2" in str(err.value) and "rimasto allo schema 1" in str(err.value)
    assert "copia di sicurezza" in str(err.value)
    # tutto annullato: versione, tabelle create prima dell'errore, dati
    assert stored_version(path) == "1"
    objects = schema_objects(path)
    assert ("table", "tags") not in objects and ("table", "favorites") not in objects
    assert dump(path) == before
    assert len(list((path.parent / BACKUP_DIR).glob("bioloom.schema1.*.db"))) == 1
    # con le migrazioni vere il database si aggiorna
    monkeypatch.setattr(db_module, "MIGRATIONS", (real,))
    db = Database(path)
    try:
        assert db.schema_version() == 2
    finally:
        db.close()
    assert dump(path) == before


def test_failure_in_second_migration_keeps_the_first(v1, monkeypatch):
    """Una transazione per migrazione: se la 3 fallisce, la 2 resta applicata e la versione è 2."""
    path, before = v1
    real = db_module.MIGRATIONS[0]
    third = Migration(3, "prova", ("CREATE TABLE IF NOT EXISTS prova (x)", "INSERT INTO tabella_inesistente VALUES (1)"))
    monkeypatch.setattr(db_module, "MIGRATIONS", (real, third))
    with pytest.raises(MigrationError):
        Database(path)
    assert stored_version(path) == "2"
    objects = schema_objects(path)
    assert ("table", "tags") in objects and ("table", "prova") not in objects
    assert dump(path) == before


def test_no_migration_without_backup(v1):
    """La copia di sicurezza non si può scrivere (al posto della cartella c'è un file): nessuna migrazione."""
    path, before = v1
    (path.parent / BACKUP_DIR).write_text("non una cartella")
    with pytest.raises(MigrationError) as err:
        Database(path)
    assert "Copia di sicurezza" in str(err.value)
    assert stored_version(path) == "1" and ("table", "tags") not in schema_objects(path)
    assert dump(path) == before


def test_new_database_needs_no_backup(tmp_path):
    db = Database(tmp_path / "data" / "bioloom.db")
    try:
        assert db.schema_version() == 2 and db.backup is None
        assert db.migrated == [(2, "etichette e preferiti")]
    finally:
        db.close()
    assert not (tmp_path / "data" / BACKUP_DIR).exists()


def test_newer_schema_is_refused(v1):
    path, before = v1
    conn = sqlite3.connect(str(path))
    conn.execute("UPDATE meta SET value='99' WHERE key='schema_version'")
    conn.commit()
    conn.close()
    with pytest.raises(MigrationError) as err:
        Database(path)
    assert "più recente" in str(err.value)
    assert stored_version(path) == "99" and dump(path) == before


def test_second_instance_still_refused_during_and_after_migration(v1):
    path, _before = v1
    first = Database(path)
    try:
        with pytest.raises(DatabaseInUse):
            Database(path, busy_timeout=0.2)
    finally:
        first.close()


def test_cleanup_triggers_follow_deletes_and_cascades(tmp_path):
    db = Database(tmp_path / "t.db")
    try:
        now = "2026-01-01T00:00:00+00:00"
        db.execute("INSERT INTO projects VALUES ('p1', 'a', '', ?, ?)", (now, now))
        db.execute("INSERT INTO sequences VALUES ('s1', 'p1', 'x', 'AC', 'manual', '{}', ?)", (now,))
        db.execute("INSERT INTO jobs(id, kind, status, project_id, created_at, updated_at) "
                   "VALUES ('j1', 'align', 'completed', 'p1', ?, ?)", (now, now))
        for etype, eid in (("project", "p1"), ("sequence", "s1"), ("job", "j1"), ("template", "1UBQ")):
            db.execute("INSERT INTO tags VALUES (?, ?, 'x', ?)", (etype, eid, now))
            db.execute("INSERT INTO favorites VALUES (?, ?, ?)", (etype, eid, now))
        db.execute("DELETE FROM jobs WHERE id='j1'")
        assert db.scalar("SELECT COUNT(*) FROM tags WHERE entity_type='job'") == 0
        db.execute("DELETE FROM projects WHERE id='p1'")  # le sequenze se ne vanno in cascata, con i trigger
        left = db.query("SELECT entity_type FROM tags UNION ALL SELECT entity_type FROM favorites")
        assert left == [{"entity_type": "template"}, {"entity_type": "template"}]  # i template li pulisce la libreria
        with pytest.raises(sqlite3.IntegrityError):  # tipo di entità non ammesso
            db.execute("INSERT INTO tags VALUES ('utente', 'u', 'x', ?)", (now,))
    finally:
        db.close()


def test_app_starts_on_v1_data_folder(v1):
    """Una cartella dati della versione 1 in uso continua a funzionare: dati visibili, etichette e ricerca."""
    path, before = v1
    app = create_app(Paths(path.parent), frontend_dist=None)
    with TestClient(app) as c:
        projects = {p["id"]: p for p in c.get("/api/projects").json()}
        assert set(projects) == {"p1", "p2"} and projects["p1"]["tags"] == [] and projects["p1"]["favorite"] is False
        assert c.post("/api/tags/project/p1", json={"tag": "Urgente"}).json()["tags"] == ["urgente"]
        seqs = c.get("/api/projects/p1/sequences").json()
        assert [s["name"] for s in seqs] == ["ub_h", "ub_y"] and seqs[0]["tags"] == []
        found = c.get("/api/search", params={"q": "P0CG48"}).json()
        assert [(g["type"], [i["id"] for i in g["items"]]) for g in found["groups"]] == [("sequence", ["s1"])]
        jobs = c.get("/api/jobs", params={"project_id": "p1"}).json()
        assert [j["id"] for j in jobs] == ["j1"] and jobs[0]["tags"] == []
    after = dump(path)
    assert after["sequences"] == before["sequences"] and after["documents"] == before["documents"]
    assert after["pipeline_items"] == before["pipeline_items"]


def test_concurrent_writes_after_migration(v1):
    """Dopo la migrazione la connessione unica serializza le scritture di più thread (nessun errore di lock)."""
    path, _before = v1
    db = Database(path)
    errors: list[BaseException] = []

    def worker(n: int) -> None:
        try:
            for i in range(20):
                db.execute("INSERT OR IGNORE INTO tags VALUES ('project', 'p1', ?, '2026-01-01T00:00:00+00:00')",
                           (f"t{n}-{i}",))
        except BaseException as exc:  # pragma: no cover - diagnosi
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(n,)) for n in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    try:
        assert not errors and db.scalar("SELECT COUNT(*) FROM tags") == 80
    finally:
        db.close()
