"""Ricerca globale (F8b): progetti, sequenze, documenti, job, template ed etichette in una sola richiesta.

**Indice.** Con FTS5 nella build di SQLite (quella dell'immagine, SQLite 3.45 di Ubuntu 24.04, lo ha) ogni tabella ha
un indice FTS5 *temporaneo* (`temp.search_<tipo>`, rowid = rowid della riga) costruito all'avvio dalle tabelle e
tenuto allineato da trigger temporanei su inserimenti, eliminazioni e modifiche dei campi indicizzati. Nulla di FTS5
finisce nel file `bioloom.db`: una build di SQLite senza FTS5 (esecuzione nativa di sviluppo) apre e scrive la stessa
cartella dati, cosa impossibile con tabelle virtuali o trigger FTS5 salvati nello schema. Costo: la ricostruzione a
ogni avvio, lineare nel numero di righe (misurati 0,04 s per 10 000 job nel container di test). Senza FTS5 si usa LIKE con
`bioloom_fold` (minuscole senza accenti, funzione SQL della connessione), che seleziona un soprainsieme dei candidati.

**Campi indicizzati.**

| Gruppo | Nome | Altri campi |
|---|---|---|
| progetti | nome | descrizione |
| sequenze | nome | accessione UniProt, proteina e organismo dai metadati |
| documenti | nome | tipo (pir, fasta…) |
| job | titolo | tipo e sua etichetta italiana ("model", "Modellazione") |
| template | id | titolo e organismo dai metadati (o dal record SOURCE / mmCIF del file) |
| etichette | etichetta | — |

I template stanno su file: un indice in memoria (`TemplateIndex`) si ricostruisce quando cambia la cartella (nomi,
date e dimensioni dei file) o quando la libreria lo avvisa; l'organismo letto dal file resta in cache per data.

**Risultati.** Corrispondenza e pertinenza vengono da `domain/textsearch.py`, uguali per FTS5, LIKE e template. Per
gruppo si leggono al più `FTS_CANDIDATES` candidati per pertinenza BM25 di FTS5 (o `LIKE_CANDIDATES` dai più recenti
con LIKE), si ordinano per punteggio, stella e data, e se ne restituiscono `limit`; `truncated` dice se ce n'erano
altri. I gruppi si ordinano per il miglior punteggio. Con `tag` si cerca solo fra gli elementi con quell'etichetta (e
senza testo si elencano tutti); senza testo né etichetta si elencano i preferiti.
"""

from __future__ import annotations

import os
import re
import sqlite3
import threading
from typing import Callable, Iterable, Optional

from ..core.db import LABEL_ENTITIES, Database
from ..core.storage import read_json, safe_join
from ..domain import structure, textsearch
from ..domain.labels import normalize_tag
from .labels import LabelService
from .templates import TemplateLibrary

GROUPS = ("project", "sequence", "document", "job", "template", "tag")
GROUP_LABEL = {"project": "Progetti", "sequence": "Sequenze", "document": "Documenti", "job": "Job",
               "template": "Template", "tag": "Etichette"}
DEFAULT_LIMIT = 5
MAX_LIMIT = 50
FTS_CANDIDATES = 200
LIKE_CANDIDATES = 2000
FAVORITE_BONUS = 1.0  # a parità di corrispondenza vince l'elemento con la stella

# Etichette italiane dei tipi di job (come KIND_LABEL del frontend): "modellazione" trova i job "model". Ogni tipo
# registrato nel JobManager deve averne una (test_global_search lo verifica). Entrano nell'indice FTS5 temporaneo,
# ricostruito a ogni avvio con queste etichette, e nel ripiego LIKE: una nuova etichetta vale anche per i job vecchi.
JOB_KIND_LABELS = {
    "search": "Ricerca template", "align": "Allineamento", "align2d": "Allineamento MODELLER", "model": "Modellazione",
    "loop": "Raffinamento loop", "quality": "Analisi qualità", "qmean": "QMEAN", "pipeline": "Pipeline batch",
    "selftest": "Verifica installazione", "db_update_pdbaa": "Aggiornamento pdbaa", "db_build_mmseqs": "Database MMseqs2",
    "db_index_mmseqs": "Indice MMseqs2", "db_convert_uniclust": "Conversione UniClust30",
    "db_local_uniclust": "Copia locale UniClust30", "analysis": "Analisi strutturale",
    "pockets": "Tasche (fpocket)", "electrostatics": "Elettrostatica (APBS)", "refine": "Rifinitura energetica",
    "rerun": "Riesecuzione da pacchetto", "mutate": "Mutagenesi", "alascan": "Scansione di alanina",
    "predict": "Confronto con una predizione", "pipeline_export": "ZIP della pipeline",
}


def _sql_text(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _job_other(r: str) -> str:
    labels = " ".join(f"WHEN {_sql_text(k)} THEN {_sql_text(v)}" for k, v in JOB_KIND_LABELS.items())
    return f"coalesce({r}kind, '') || ' ' || CASE {r}kind {labels} ELSE '' END"


def _sequence_other(r: str) -> str:
    fields = " || ' ' || ".join(f"coalesce(json_extract({r}meta, '$.{k}'), '')" for k in ("accession", "protein",
                                                                                           "organism"))
    return f"CASE WHEN json_valid({r}meta) THEN {fields} ELSE '' END"


def _column(name: str) -> Callable[[str], str]:
    return lambda r: f"coalesce({r}{name}, '')"


# tipo → (tabella, nome, altri campi, colonne che cambiano l'indice, colonne lette). Nome e altri campi sono
# espressioni SQL costruite con il prefisso della riga: "NEW." nei trigger, "t." nelle query, "" nel riempimento.
_SOURCES: dict[str, tuple[str, Callable[[str], str], Callable[[str], str], str, str]] = {
    "project": ("projects", _column("name"), _column("description"), "name, description",
                "t.id, t.name, t.description, t.updated_at"),
    "sequence": ("sequences", _column("name"), _sequence_other, "name, meta",
                 "t.id, t.project_id, t.name, t.meta, length(t.sequence) AS length, t.created_at AS updated_at, "
                 "p.name AS project_name"),
    "document": ("documents", _column("name"), _column("kind"), "name, kind",
                 "t.id, t.project_id, t.name, t.kind, t.updated_at, p.name AS project_name"),
    "job": ("jobs", _column("title"), _job_other, "title, kind",
            "t.id, t.project_id, t.kind, t.title, t.status, t.updated_at, p.name AS project_name"),
}
# parole per il riepilogo di un'etichetta ("3 elementi: 2 job, 1 template")
_TYPE_WORDS = {"project": ("progetto", "progetti"), "sequence": ("sequenza", "sequenze"), "job": ("job", "job"),
               "template": ("template", "template")}


def fts5_available(db: Database) -> bool:
    """La build di SQLite di questa connessione ha FTS5 (prova a creare una tabella temporanea)."""
    try:
        with db.transaction() as conn:
            conn.execute("CREATE VIRTUAL TABLE temp._bioloom_fts5_probe USING fts5(x)")
            conn.execute("DROP TABLE temp._bioloom_fts5_probe")
        return True
    except sqlite3.Error:
        return False


# ------------------------------------------------------------------------------------- template su file
_PDB_ORGANISM = re.compile(r"^SOURCE.{4}[^\n]*?ORGANISM_SCIENTIFIC:[ \t]*([^;\n]+)", re.MULTILINE)
_CIF_ORGANISM = re.compile(r"^_(?:entity_src_gen\.pdbx_gene_src_scientific_name|entity_src_nat\.pdbx_organism_scientific"
                           r"|pdbx_entity_src_syn\.organism_scientific)[ \t]+(?:'([^'\n]*)'|\"([^\"\n]*)\"|(\S+))",
                           re.MULTILINE)


def header_organism(text: str) -> str:
    """Organismo dall'intestazione di un PDB (`SOURCE … ORGANISM_SCIENTIFIC: …;`) o di un mmCIF (voce singola, non
    in un loop); '' se manca."""
    m = _PDB_ORGANISM.search(text)
    if m:
        return m.group(1).strip()
    m = _CIF_ORGANISM.search(text)
    if m:
        value = next(g for g in m.groups() if g is not None).strip()
        return "" if value in ("?", ".") else value
    return ""


class TemplateIndex:
    """Voci della libreria per la ricerca (id, titolo, organismo), in memoria."""

    def __init__(self, library: TemplateLibrary) -> None:
        self.library = library
        self._lock = threading.Lock()
        self._signature: Optional[tuple] = None
        self._entries: list[dict] = []
        self._organisms: dict[tuple[str, int], str] = {}  # (file, mtime_ns) → organismo letto dall'intestazione

    def invalidate(self, *_args: object) -> None:
        with self._lock:
            self._signature = None

    def _scan(self) -> tuple:
        """Firma della cartella: nome, data e dimensione di strutture e metadati. Costa una lettura della cartella."""
        out = []
        try:
            with os.scandir(self.library.root) as it:
                for entry in it:
                    name = entry.name
                    if name.startswith(".") or not name.lower().endswith((".pdb", ".cif", ".meta.json")):
                        continue
                    try:
                        st = entry.stat()
                    except OSError:
                        continue
                    out.append((name, st.st_mtime_ns, st.st_size))
        except OSError:
            return ()
        return tuple(sorted(out))

    def entries(self) -> list[dict]:
        signature = self._scan()
        with self._lock:
            if signature == self._signature:
                return self._entries
        entries = []
        for tid in self.library.ids():
            try:
                path = self.library.path(tid)
                meta = read_json(safe_join(self.library.root, f"{tid}.meta.json"), default={}) or {}
            except Exception:  # file sparito nel frattempo o id non valido
                continue
            alphafold = meta.get("alphafold") or {}
            organism = meta.get("organism") or alphafold.get("organism") or self._file_organism(path)
            entries.append({"id": tid, "title": str(meta.get("title") or alphafold.get("description") or ""),
                            "organism": str(organism or ""), "source": str(meta.get("source") or ""),
                            "added_at": meta.get("added_at"), "predicted": bool(meta.get("predicted")),
                            "predictor": meta.get("predictor") if meta.get("predicted") else None})
        with self._lock:
            self._entries, self._signature = entries, signature
        return entries

    def _file_organism(self, path) -> str:
        try:
            key = (path.name, path.stat().st_mtime_ns)
        except OSError:
            return ""
        if key not in self._organisms:
            try:
                self._organisms[key] = header_organism(structure.read_text_head(path, 200_000))
            except Exception:
                self._organisms[key] = ""
        return self._organisms[key]


# ------------------------------------------------------------------------------------- ricerca
class GlobalSearch:
    def __init__(self, db: Database, library: TemplateLibrary, labels: LabelService,
                 use_fts: Optional[bool] = None) -> None:
        """`use_fts` None = FTS5 se disponibile (`BIOLOOM_SEARCH_FTS=0` forza LIKE); False = sempre LIKE."""
        self.db = db
        self.library = library
        self.labels = labels
        self.templates = TemplateIndex(library)
        library.subscribe(self.templates.invalidate)
        db.create_function("bioloom_fold", 1, textsearch.fold)
        if use_fts is None:
            use_fts = os.environ.get("BIOLOOM_SEARCH_FTS", "1") != "0"
        self.engine = "fts5" if use_fts and fts5_available(db) else "like"
        if self.engine == "fts5":
            self._build_index()

    def _build_index(self) -> None:
        """Indici FTS5 temporanei riempiti dalle tabelle e trigger temporanei che li tengono allineati, tutto in una
        transazione: nessuna scrittura può cadere fra la lettura delle righe e la creazione dei trigger."""
        with self.db.transaction() as conn:
            for kind, (table, name, other, watched, _cols) in _SOURCES.items():
                index = f"search_{kind}"
                conn.execute(f"DROP TABLE IF EXISTS temp.{index}")
                conn.execute(f"CREATE VIRTUAL TABLE temp.{index} USING fts5(name, other, "
                             "tokenize='unicode61 remove_diacritics 2', prefix='2 3')")
                conn.execute(f"INSERT INTO temp.{index}(rowid, name, other) "
                             f"SELECT rowid, {name('')}, {other('')} FROM main.{table}")
                # nei corpi dei trigger SQLite non ammette nomi qualificati: la tabella temporanea si trova per nome
                insert = f"INSERT INTO {index}(rowid, name, other) VALUES (NEW.rowid, {name('NEW.')}, " \
                         f"{other('NEW.')})"
                delete = f"DELETE FROM {index} WHERE rowid=OLD.rowid"
                conn.execute(f"CREATE TEMP TRIGGER {index}_ai AFTER INSERT ON main.{table} BEGIN {insert}; END")
                conn.execute(f"CREATE TEMP TRIGGER {index}_ad AFTER DELETE ON main.{table} BEGIN {delete}; END")
                conn.execute(f"CREATE TEMP TRIGGER {index}_au AFTER UPDATE OF {watched} ON main.{table} "
                             f"BEGIN {delete}; {insert}; END")

    # ----------------------------------------------------------------------------- lettura delle righe
    def _restriction(self, kind: str, tag: Optional[str], favorites: bool) -> tuple[str, tuple]:
        if tag:
            return " AND t.id IN (SELECT entity_id FROM tags WHERE entity_type=? AND tag=?)", (kind, tag)
        if favorites:
            return " AND t.id IN (SELECT entity_id FROM favorites WHERE entity_type=?)", (kind,)
        return "", ()

    def _rows(self, kind: str, terms: list[str], tag: Optional[str], favorites: bool) -> list[dict]:
        table, name, other, _watched, cols = _SOURCES[kind]
        join = "" if kind == "project" else " LEFT JOIN projects p ON p.id = t.project_id"
        extra, extra_args = self._restriction(kind, tag, favorites)
        if not terms:  # elenco di un'etichetta o dei preferiti
            return self.db.query(f"SELECT {cols} FROM main.{table} t{join} WHERE 1=1{extra} "
                                 f"ORDER BY t.rowid DESC LIMIT ?", (*extra_args, LIKE_CANDIDATES))
        if self.engine == "fts5":
            index = f"search_{kind}"
            return self.db.query(
                f"SELECT {cols} FROM temp.{index} AS f JOIN main.{table} t ON t.rowid = f.rowid{join} "
                f"WHERE f.{index} MATCH ?{extra} ORDER BY f.rank LIMIT ?",
                (textsearch.fts_query(terms), *extra_args, FTS_CANDIDATES))
        clauses, args = [], []
        for term in terms:  # ogni parola nel nome o negli altri campi (soprainsieme: decide `textsearch.score`)
            clauses.append(f"(bioloom_fold({name('t.')}) LIKE ? ESCAPE '\\' "
                           f"OR bioloom_fold({other('t.')}) LIKE ? ESCAPE '\\')")
            pattern = textsearch.like_escape(term)
            args += [pattern, pattern]
        return self.db.query(f"SELECT {cols} FROM main.{table} t{join} WHERE {' AND '.join(clauses)}{extra} "
                             f"ORDER BY t.rowid DESC LIMIT ?", (*args, *extra_args, LIKE_CANDIDATES))

    @staticmethod
    def _item(kind: str, row: dict) -> tuple[dict, str, list]:
        """Voce del risultato, nome e altri campi per corrispondenza e pertinenza."""
        if kind == "project":
            item = {"title": row["name"], "subtitle": (row.get("description") or "")[:160]}
            return item, row["name"], [row.get("description")]
        if kind == "sequence":
            meta = row.get("meta") if isinstance(row.get("meta"), dict) else {}
            others = [meta.get("accession"), meta.get("protein"), meta.get("organism")]
            sub = " · ".join(str(x) for x in [f"{row.get('length', 0)} aa", *others] if x)
            return {"title": row["name"], "subtitle": sub}, row["name"], others
        if kind == "document":
            return {"title": row["name"], "subtitle": str(row.get("kind", "")).upper()}, row["name"], [row.get("kind")]
        label = JOB_KIND_LABELS.get(row.get("kind", ""), row.get("kind", ""))
        title = row.get("title") or label
        item = {"title": title, "subtitle": label, "kind": row.get("kind"), "status": row.get("status")}
        return item, row.get("title") or "", [row.get("kind"), label]

    # ----------------------------------------------------------------------------- ricerca
    def search(self, query: str = "", *, tag: Optional[str] = None, types: Optional[Iterable[str]] = None,
               limit: int = DEFAULT_LIMIT) -> dict:
        terms = textsearch.query_terms(query)
        tag = normalize_tag(tag) if tag else None
        limit = max(1, min(int(limit), MAX_LIMIT))
        wanted = [g for g in GROUPS if types is None or g in set(types)]
        favorites = not terms and not tag
        groups = []
        for kind in wanted:
            if kind == "tag":
                if tag or favorites:  # con un'etichetta scelta il gruppo delle etichette non serve
                    continue
                group = self._tag_group(terms, limit)
            elif kind == "template":
                group = self._template_group(terms, tag, favorites, limit)
            else:
                group = self._table_group(kind, terms, tag, favorites, limit)
            if group["items"]:
                groups.append(group)
        order = {g: i for i, g in enumerate(GROUPS)}
        groups.sort(key=lambda g: (-g["items"][0]["score"], order[g["type"]]))
        return {"query": query, "terms": terms, "tag": tag, "favorites": favorites, "engine": self.engine,
                "groups": groups}

    def _rank(self, kind: str, candidates: list[tuple[dict, float, str]], limit: int) -> dict:
        """Ordina e taglia; `candidates` = (voce, punteggio, data). Ordine: punteggio con la stella, poi il più
        recente, poi titolo e id (stesso ordine con FTS5 e con LIKE, che leggono i candidati in ordini diversi)."""
        if kind in LABEL_ENTITIES:
            self.labels.annotate(kind, [c[0] for c in candidates])
        else:  # i documenti non hanno etichette né stella
            for item, _points, _stamp in candidates:
                item.update(tags=[], favorite=False)
        scored = []
        for item, points, stamp in candidates:
            item["score"] = round(points + (FAVORITE_BONUS if item["favorite"] else 0.0), 2)
            scored.append((item, stamp or ""))
        scored.sort(key=lambda x: (x[0]["title"], x[0]["id"]))  # ordinamenti stabili, dal criterio più debole
        scored.sort(key=lambda x: x[1], reverse=True)
        scored.sort(key=lambda x: x[0]["score"], reverse=True)
        items = [s[0] for s in scored]
        return {"type": kind, "label": GROUP_LABEL[kind], "items": items[:limit], "truncated": len(items) > limit}

    def _table_group(self, kind: str, terms: list[str], tag: Optional[str], favorites: bool, limit: int) -> dict:
        candidates = []
        for row in self._rows(kind, terms, tag, favorites):
            item, primary, others = self._item(kind, row)
            points = textsearch.score(terms, primary, others) if terms else 0.0
            if terms and points <= 0:
                continue
            # un progetto "appartiene" a sé stesso: aprirlo significa sceglierlo come progetto attivo
            project_id = row["id"] if kind == "project" else row.get("project_id")
            item.update({"type": kind, "id": row["id"], "project_id": project_id,
                         "project_name": row["name"] if kind == "project" else row.get("project_name")})
            candidates.append((item, points, row.get("updated_at")))
        return self._rank(kind, candidates, limit)

    def _template_group(self, terms: list[str], tag: Optional[str], favorites: bool, limit: int) -> dict:
        allowed = self.labels.ids_with("template", tag) if tag else \
            self.labels.favorite_ids("template") if favorites else None
        candidates = []
        for entry in self.templates.entries():
            if allowed is not None and entry["id"] not in allowed:
                continue
            others = [entry["title"], entry["organism"]]
            points = textsearch.score(terms, entry["id"], others) if terms else 0.0
            if terms and points <= 0:
                continue
            sub = " · ".join(x for x in (entry["title"], entry["organism"]) if x)
            item = {"type": "template", "id": entry["id"], "title": entry["id"], "subtitle": sub[:160],
                    "project_id": None, "project_name": None, "predicted": entry["predicted"],
                    "predictor": entry.get("predictor")}
            candidates.append((item, points, entry.get("added_at")))
        return self._rank("template", candidates, limit)

    def _tag_group(self, terms: list[str], limit: int) -> dict:
        items = []
        for t in self.labels.tag_counts():
            points = textsearch.score(terms, t["tag"])
            if points <= 0:
                continue
            parts = ", ".join(f"{n} {_TYPE_WORDS[k][0] if n == 1 else _TYPE_WORDS[k][1]}"
                              for k, n in sorted(t["types"].items()))
            total = f"{t['count']} {'elemento' if t['count'] == 1 else 'elementi'}"
            items.append({"type": "tag", "id": t["tag"], "title": t["tag"], "subtitle": f"{total}: {parts}",
                          "count": t["count"], "score": round(points, 2), "project_id": None, "project_name": None})
        items.sort(key=lambda i: (-i["score"], -i["count"], i["title"]))
        return {"type": "tag", "label": GROUP_LABEL["tag"], "items": items[:limit], "truncated": len(items) > limit}
