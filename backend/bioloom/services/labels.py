"""Etichette e preferiti (stella) di progetti, job, sequenze e template (F8b).

Tabelle `tags` e `favorites` (migrazione 2 di core/db.py), con chiave (tipo di entità, id). Le etichette si
normalizzano con `domain.labels.normalize_tag`; gli id dei template con `templates.normalize_id` (maiuscole).

Pulizia: per progetti, sequenze e job la fanno i trigger del database, qualunque sia il percorso dell'eliminazione
(rotta, eliminazione di un progetto con i suoi job, cascata delle sequenze). I template stanno su file: la libreria
avvisa dopo l'eliminazione (`TemplateLibrary.subscribe`) e `prune_templates` toglie all'avvio e prima dei conteggi le
etichette dei template spariti dalla cartella per altre vie (file cancellati a mano, caricamento sostitutivo non
valido).
"""

from __future__ import annotations

from typing import Iterable, Optional

from ..core.db import LABEL_ENTITIES, Database, now_iso
from ..core.errors import AppError, NotFound
from ..core.storage import validate_identifier
from ..domain.labels import MAX_TAGS_PER_ENTITY, normalize_tag
from .templates import TemplateLibrary, normalize_id

TABLES = {"project": "projects", "job": "jobs", "sequence": "sequences"}
ENTITY_LABEL = {"project": "Progetto", "job": "Job", "sequence": "Sequenza", "template": "Template"}


def entity_type(value: str) -> str:
    if value not in LABEL_ENTITIES:
        raise AppError(f"Tipo di elemento non valido: '{value}' (ammessi: {', '.join(LABEL_ENTITIES)}).",
                       code="invalid_entity")
    return value


class LabelService:
    def __init__(self, db: Database, library: TemplateLibrary) -> None:
        self.db = db
        self.library = library
        library.subscribe(self._library_changed)

    def _library_changed(self, event: str, template_id: str) -> None:
        if event == "deleted":
            self.forget("template", template_id)

    # ----------------------------------------------------------------------------- identità ed esistenza
    @staticmethod
    def entity_id(etype: str, eid: str) -> str:
        entity_type(etype)
        if etype == "template":
            return normalize_id(eid)
        return validate_identifier(eid, f"{etype}_id")

    def _exists(self, conn, etype: str, eid: str) -> bool:
        if etype == "template":
            return self.library.exists(eid)
        return conn.execute(f"SELECT 1 FROM {TABLES[etype]} WHERE id=?", (eid,)).fetchone() is not None

    def _missing(self, etype: str, eid: str) -> NotFound:
        return NotFound(f"{ENTITY_LABEL[etype]} {eid} non trovato.")

    # ----------------------------------------------------------------------------- lettura
    def get(self, etype: str, eid: str) -> dict:
        """Etichette (in ordine alfabetico) e stella di un elemento."""
        eid = self.entity_id(etype, eid)
        tags = [r["tag"] for r in self.db.query(
            "SELECT tag FROM tags WHERE entity_type=? AND entity_id=? ORDER BY tag", (etype, eid))]
        favorite = self.db.scalar("SELECT 1 FROM favorites WHERE entity_type=? AND entity_id=?", (etype, eid))
        return {"entity_type": etype, "entity_id": eid, "tags": tags, "favorite": bool(favorite)}

    def annotate(self, etype: str, rows: list[dict], key: str = "id") -> list[dict]:
        """Aggiunge `tags` e `favorite` alle righe di un elenco (una query per tabella, non una per riga)."""
        entity_type(etype)
        if not rows:
            return rows
        tags: dict[str, list[str]] = {}
        for r in self.db.query("SELECT entity_id, tag FROM tags WHERE entity_type=? ORDER BY tag", (etype,)):
            tags.setdefault(r["entity_id"], []).append(r["tag"])
        stars = {r["entity_id"] for r in self.db.query(
            "SELECT entity_id FROM favorites WHERE entity_type=?", (etype,))}
        for row in rows:
            rid = row.get(key)
            row["tags"] = tags.get(rid, [])
            row["favorite"] = rid in stars
        return rows

    def filter(self, etype: str, rows: list[dict], *, tag: Optional[str] = None,
               favorite: Optional[bool] = None, key: str = "id") -> list[dict]:
        """Elenco annotato, ristretto agli elementi con l'etichetta `tag` e/o con (o senza) la stella."""
        self.annotate(etype, rows, key)
        if tag:
            wanted = normalize_tag(tag)
            rows = [r for r in rows if wanted in r["tags"]]
        if favorite is not None:
            rows = [r for r in rows if r["favorite"] == favorite]
        return rows

    def ids_with(self, etype: str, tag: str) -> set[str]:
        """Id degli elementi di un tipo che hanno l'etichetta (filtro degli elenchi lunghi in SQL, es. i job)."""
        entity_type(etype)
        return {r["entity_id"] for r in self.db.query(
            "SELECT entity_id FROM tags WHERE entity_type=? AND tag=?", (etype, normalize_tag(tag)))}

    def favorite_ids(self, etype: str) -> set[str]:
        entity_type(etype)
        return {r["entity_id"] for r in self.db.query(
            "SELECT entity_id FROM favorites WHERE entity_type=?", (etype,))}

    def tag_counts(self, etype: Optional[str] = None) -> list[dict]:
        """Etichette in uso con il numero di elementi, in totale e per tipo; le più usate prima."""
        if etype is not None:
            entity_type(etype)
        self.prune_templates()
        where, args = ("WHERE entity_type=?", (etype,)) if etype else ("", ())
        out: dict[str, dict] = {}
        for r in self.db.query(f"SELECT tag, entity_type, COUNT(*) AS n FROM tags {where} "
                               "GROUP BY tag, entity_type", args):
            item = out.setdefault(r["tag"], {"tag": r["tag"], "count": 0, "types": {}})
            item["count"] += r["n"]
            item["types"][r["entity_type"]] = r["n"]
        return sorted(out.values(), key=lambda t: (-t["count"], t["tag"]))

    def favorites(self, etype: Optional[str] = None) -> list[dict]:
        """Elementi con la stella, dal più recente."""
        if etype is not None:
            entity_type(etype)
        where, args = ("WHERE entity_type=?", (etype,)) if etype else ("", ())
        return self.db.query(f"SELECT entity_type, entity_id, created_at FROM favorites {where} "
                             "ORDER BY created_at DESC, entity_type, entity_id", args)

    # ----------------------------------------------------------------------------- modifiche
    def add_tag(self, etype: str, eid: str, raw_tag: str) -> dict:
        """Aggiunge un'etichetta (già presente: nessun cambiamento). Al più `MAX_TAGS_PER_ENTITY` per elemento.

        Controllo dell'elemento, limite e inserimento stanno nella stessa transazione (la connessione è unica e
        serializzata): due aggiunte concorrenti non superano il limite e un elemento eliminato nel frattempo non
        riceve etichette orfane. Per un template eliminato fra il controllo e l'inserimento, la verifica successiva
        toglie l'etichetta appena scritta.
        """
        eid = self.entity_id(etype, eid)
        tag = normalize_tag(raw_tag)
        with self.db.transaction() as conn:
            if not self._exists(conn, etype, eid):
                raise self._missing(etype, eid)
            current = {r[0] for r in conn.execute(
                "SELECT tag FROM tags WHERE entity_type=? AND entity_id=?", (etype, eid))}
            if tag not in current and len(current) >= MAX_TAGS_PER_ENTITY:
                raise AppError(f"Al massimo {MAX_TAGS_PER_ENTITY} etichette per elemento.", code="too_many_tags")
            conn.execute("INSERT OR IGNORE INTO tags(entity_type, entity_id, tag, created_at) VALUES (?,?,?,?)",
                         (etype, eid, tag, now_iso()))
        if etype == "template" and not self.library.exists(eid):
            self.forget(etype, eid)
            raise self._missing(etype, eid)
        return self.get(etype, eid)

    def remove_tag(self, etype: str, eid: str, raw_tag: str) -> dict:
        """Toglie un'etichetta; idempotente, e valida anche per un elemento che non esiste più."""
        eid = self.entity_id(etype, eid)
        self.db.execute("DELETE FROM tags WHERE entity_type=? AND entity_id=? AND tag=?",
                        (etype, eid, normalize_tag(raw_tag)))
        return self.get(etype, eid)

    def set_favorite(self, etype: str, eid: str, on: bool) -> dict:
        eid = self.entity_id(etype, eid)
        if not on:
            self.db.execute("DELETE FROM favorites WHERE entity_type=? AND entity_id=?", (etype, eid))
            return self.get(etype, eid)
        with self.db.transaction() as conn:
            if not self._exists(conn, etype, eid):
                raise self._missing(etype, eid)
            conn.execute("INSERT OR IGNORE INTO favorites(entity_type, entity_id, created_at) VALUES (?,?,?)",
                         (etype, eid, now_iso()))
        if etype == "template" and not self.library.exists(eid):
            self.forget(etype, eid)
            raise self._missing(etype, eid)
        return self.get(etype, eid)

    def forget(self, etype: str, eid: str) -> None:
        """Etichette e stella di un elemento eliminato."""
        eid = self.entity_id(etype, eid)
        with self.db.transaction() as conn:
            conn.execute("DELETE FROM tags WHERE entity_type=? AND entity_id=?", (etype, eid))
            conn.execute("DELETE FROM favorites WHERE entity_type=? AND entity_id=?", (etype, eid))

    def prune_templates(self, present: Optional[Iterable[str]] = None) -> int:
        """Toglie etichette e stella dei template che non sono più nella libreria; restituisce le righe tolte.

        Se la cartella della libreria non esiste (non montata) non tocca nulla: un elenco vuoto per un disco
        assente non deve cancellare le etichette.
        """
        if present is None:
            if not self.library.root.is_dir():
                return 0
            present = self.library.ids()
        keep = set(present)
        labelled = {r["entity_id"] for r in self.db.query(
            "SELECT entity_id FROM tags WHERE entity_type='template' UNION "
            "SELECT entity_id FROM favorites WHERE entity_type='template'")}
        gone = sorted(labelled - keep)
        removed = 0
        if gone:
            with self.db.transaction() as conn:
                for tid in gone:
                    removed += conn.execute("DELETE FROM tags WHERE entity_type='template' AND entity_id=?",
                                            (tid,)).rowcount
                    removed += conn.execute("DELETE FROM favorites WHERE entity_type='template' AND entity_id=?",
                                            (tid,)).rowcount
        return removed
