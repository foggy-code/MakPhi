"""Progetti di lavoro: sequenze bersaglio e documenti di allineamento (PIR/FASTA/Clustal)."""

from __future__ import annotations

import uuid
from typing import Optional

from ..core.db import Database, encode_json, now_iso
from ..core.errors import AppError, NotFound
from ..core.storage import validate_identifier
from ..domain import sequences

DOCUMENT_KINDS = {"pir", "fasta", "clustal", "text"}
# progetto vuoto creato quando si elimina l'ultimo (BioLoom ne richiede sempre almeno uno)
REPLACEMENT_NAME = "Nuovo progetto"
REPLACEMENT_DESCRIPTION = "Creato automaticamente: BioLoom richiede almeno un progetto."


def _new_id() -> str:
    return uuid.uuid4().hex[:12]


class ProjectService:
    def __init__(self, db: Database) -> None:
        self.db = db

    # ----------------------------------------------------------------------------- progetti
    def list(self) -> list[dict]:
        return self.db.query(
            "SELECT p.*, (SELECT COUNT(*) FROM sequences s WHERE s.project_id=p.id) AS sequences, "
            "(SELECT COUNT(*) FROM documents d WHERE d.project_id=p.id) AS documents, "
            "(SELECT COUNT(*) FROM jobs j WHERE j.project_id=p.id) AS jobs "
            "FROM projects p ORDER BY p.updated_at DESC")

    def get(self, project_id: str) -> dict:
        validate_identifier(project_id, "project_id")
        row = self.db.query_one("SELECT * FROM projects WHERE id=?", (project_id,))
        if not row:
            raise NotFound("Progetto non trovato.")
        return row

    def create(self, name: str, description: str = "") -> dict:
        name = (name or "").strip()
        if not name:
            raise AppError("Il nome del progetto è obbligatorio.")
        pid, now = _new_id(), now_iso()
        self.db.execute("INSERT INTO projects(id, name, description, created_at, updated_at) VALUES (?,?,?,?,?)",
                        (pid, name[:120], (description or "")[:2000], now, now))
        return self.get(pid)

    def update(self, project_id: str, name: Optional[str] = None, description: Optional[str] = None) -> dict:
        current = self.get(project_id)
        new_name = (name if name is not None else current["name"]).strip()
        if not new_name:
            raise AppError("Il nome del progetto è obbligatorio.")
        self.db.execute("UPDATE projects SET name=?, description=?, updated_at=? WHERE id=?",
                        (new_name[:120], (description if description is not None else current["description"])[:2000],
                         now_iso(), project_id))
        return self.get(project_id)

    def touch(self, project_id: Optional[str]) -> None:
        if project_id:
            self.db.execute("UPDATE projects SET updated_at=? WHERE id=?", (now_iso(), project_id))

    def delete_if_unused(self, project_id: str) -> tuple[bool, Optional[dict]]:
        """Elimina il progetto solo se nessun job lo referenzia; se era l'ultimo ne crea uno vuoto, nella stessa
        transazione. (True, sostituto|None) eliminato; (False, None) ci sono ancora job (nuovi tentativi a carico
        del chiamante); NotFound se il progetto non esiste più.

        La transazione tiene il lock del database: né `JobManager.submit` (INSERT condizionato all'esistenza del
        progetto) né un'altra eliminazione possono inserirsi in mezzo. Due eliminazioni degli ultimi due progetti si
        serializzano: la prima vede ancora l'altro progetto, la seconda nessuno e crea il sostituto, quindi ne resta
        sempre esattamente uno. Sequenze e documenti vanno via in cascata, etichette e stella con i trigger.
        """
        with self.db.transaction() as conn:
            # rowcount conta solo il DELETE diretto, non le cascate né i trigger
            cur = conn.execute(
                "DELETE FROM projects WHERE id=? AND NOT EXISTS (SELECT 1 FROM jobs WHERE project_id=?)",
                (project_id, project_id))
            if cur.rowcount != 1:
                if conn.execute("SELECT 1 FROM projects WHERE id=?", (project_id,)).fetchone() is None:
                    raise NotFound("Progetto non trovato.")
                return False, None
            new_id = None
            if conn.execute("SELECT COUNT(*) FROM projects").fetchone()[0] == 0:
                new_id, now = _new_id(), now_iso()
                conn.execute("INSERT INTO projects(id, name, description, created_at, updated_at) VALUES (?,?,?,?,?)",
                             (new_id, REPLACEMENT_NAME, REPLACEMENT_DESCRIPTION, now, now))
        return True, (self.get(new_id) if new_id else None)

    def ensure_default(self) -> dict:
        rows = self.list()
        return rows[0] if rows else self.create("Progetto di esempio", "Creato automaticamente al primo avvio.")

    # ----------------------------------------------------------------------------- sequenze
    def list_sequences(self, project_id: str) -> list[dict]:
        self.get(project_id)
        rows = self.db.query("SELECT * FROM sequences WHERE project_id=? ORDER BY created_at, rowid", (project_id,))
        for r in rows:
            r["length"] = len(r["sequence"])
        return rows

    def get_sequence(self, project_id: str, seq_id: str) -> dict:
        validate_identifier(seq_id, "sequence_id")
        row = self.db.query_one("SELECT * FROM sequences WHERE id=? AND project_id=?", (seq_id, project_id))
        if not row:
            raise NotFound("Sequenza non trovata.")
        row["length"] = len(row["sequence"])
        return row

    def add_sequence(self, project_id: str, name: str, sequence: str, source: str = "manual",
                     meta: Optional[dict] = None) -> dict:
        self.get(project_id)
        clean, kind = sequences.validate_sequence(sequence, "protein", name or "sequenza")
        code = sequences.sanitize_code(name, default="") or f"seq{self._count(project_id) + 1}"
        sid = _new_id()
        self.db.execute(
            "INSERT INTO sequences(id, project_id, name, sequence, source, meta, created_at) VALUES (?,?,?,?,?,?,?)",
            (sid, project_id, code, clean, source[:40], encode_json(meta or {}), now_iso()))
        self.touch(project_id)
        return self.get_sequence(project_id, sid)

    def import_fasta(self, project_id: str, text: str, source: str = "fasta") -> list[dict]:
        records = sequences.parse_fasta(text)
        if not records:
            raise AppError("Nessuna sequenza trovata nel testo FASTA.")
        if len(records) > 500:
            raise AppError("Massimo 500 sequenze per importazione.")
        return [self.add_sequence(project_id, r.name, r.sequence, source, {"description": r.description})
                for r in records]

    def delete_sequence(self, project_id: str, seq_id: str) -> None:
        self.get_sequence(project_id, seq_id)
        self.db.execute("DELETE FROM sequences WHERE id=? AND project_id=?", (seq_id, project_id))

    def _count(self, project_id: str) -> int:
        return int(self.db.scalar("SELECT COUNT(*) FROM sequences WHERE project_id=?", (project_id,)))

    # ----------------------------------------------------------------------------- documenti
    def list_documents(self, project_id: str) -> list[dict]:
        self.get(project_id)
        return self.db.query("SELECT id, project_id, name, kind, meta, created_at, updated_at, length(content) AS size "
                             "FROM documents WHERE project_id=? ORDER BY updated_at DESC", (project_id,))

    def get_document(self, project_id: str, doc_id: str) -> dict:
        validate_identifier(doc_id, "document_id")
        row = self.db.query_one("SELECT * FROM documents WHERE id=? AND project_id=?", (doc_id, project_id))
        if not row:
            raise NotFound("Documento non trovato.")
        return row

    def save_document(self, project_id: str, name: str, kind: str, content: str, meta: Optional[dict] = None,
                      doc_id: Optional[str] = None) -> dict:
        self.get(project_id)
        if kind not in DOCUMENT_KINDS:
            raise AppError(f"Tipo di documento non valido: {kind}")
        if len(content) > 5_000_000:
            raise AppError("Documento troppo grande (max 5 MB).")
        name = (name or "").strip()[:120] or "Senza titolo"
        now = now_iso()
        if doc_id:
            current = self.get_document(project_id, doc_id)
            merged_meta = {**(current.get("meta") or {}), **(meta or {})}
            self.db.execute("UPDATE documents SET name=?, kind=?, content=?, meta=?, updated_at=? WHERE id=?",
                            (name, kind, content, encode_json(merged_meta), now, doc_id))
        else:
            doc_id = _new_id()
            self.db.execute(
                "INSERT INTO documents(id, project_id, name, kind, content, meta, created_at, updated_at) "
                "VALUES (?,?,?,?,?,?,?,?)", (doc_id, project_id, name, kind, content, encode_json(meta or {}), now, now))
        self.touch(project_id)
        return self.get_document(project_id, doc_id)

    def delete_document(self, project_id: str, doc_id: str) -> None:
        self.get_document(project_id, doc_id)
        self.db.execute("DELETE FROM documents WHERE id=?", (doc_id,))
