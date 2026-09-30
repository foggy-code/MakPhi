"""Libreria globale dei template strutturali (data/templates).

Oltre alle strutture sperimentali (RCSB) e ai file caricati, contiene i modelli di AlphaFold DB (`AF-<ACC>-F<n>`):
il file resta quello originale, con i record HEADER/TITLE/REMARK di AlphaFold e il pLDDT nella colonna B-factor, e
i metadati li segnano come modelli predetti (`predicted`), con versione, pLDDT medio e intervallo UniProt coperto.
"""

from __future__ import annotations

import re
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from ..core.errors import AppError, NotFound, RemoteServiceError
from ..core.storage import atomic_write_bytes, atomic_write_json, atomic_write_text, read_json, safe_join
from ..domain import plddt, secondary, structure
from ..engines import remote

EXTENSIONS = {".pdb": "pdb", ".ent": "pdb", ".cif": "cif", ".mmcif": "cif"}
ANALYSIS_VERSION = 2
SOURCE_ALPHAFOLD = "alphafold"
PREDICTED_NOTE = "modello predetto, non struttura sperimentale"
PREDICTED_METHOD = "Modello predetto (AlphaFold)"
# Campi della voce di AlphaFold DB conservati nei metadati del template
ALPHAFOLD_META = ("entry_id", "accession", "fragment", "version", "uniprot_start", "uniprot_end", "uniprot_id",
                  "gene", "description", "organism", "tax_id", "created", "tool", "pae_url")


def normalize_id(raw: str) -> str:
    clean = re.sub(r"[^A-Za-z0-9_-]", "_", (raw or "").strip()).strip("_-").upper()
    clean = re.sub(r"_+", "_", clean)[:40]
    if not clean:
        raise AppError("Identificativo del template non valido.")
    return clean


class TemplateLibrary:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        # un download alla volta per modello AlphaFold: elementi di pipeline in parallelo con lo stesso template
        # aspettano il primo e poi lo trovano in libreria
        self._fetching: dict[str, threading.Lock] = {}

    def _fetch_lock(self, tid: str) -> threading.Lock:
        with self._lock:
            return self._fetching.setdefault(tid, threading.Lock())

    # ----------------------------------------------------------------------------- accesso ai file
    def _files(self) -> dict[str, Path]:
        out = {}
        for path in self.root.iterdir():
            if path.is_file() and path.suffix.lower() in (".pdb", ".cif"):
                out.setdefault(path.stem.upper(), path)
        return out

    def path(self, template_id: str) -> Path:
        tid = normalize_id(template_id)
        for ext in (".pdb", ".cif"):
            candidate = safe_join(self.root, f"{tid}{ext}")
            if candidate.exists():
                return candidate
        raise NotFound(f"Template {tid} non presente nella libreria.")

    def exists(self, template_id: str) -> bool:
        try:
            self.path(template_id)
            return True
        except (NotFound, AppError):
            return False

    def _meta_path(self, tid: str) -> Path:
        return safe_join(self.root, f"{tid}.meta.json")

    def _analysis_path(self, tid: str) -> Path:
        return safe_join(self.root, f"{tid}.analysis.json")

    # ----------------------------------------------------------------------------- operazioni
    def list(self) -> list[dict]:
        items = []
        for tid, path in sorted(self._files().items()):
            meta = read_json(self._meta_path(tid), default={}) or {}
            items.append({
                "id": tid, "format": path.suffix.lstrip(".").lower(), "size": path.stat().st_size,
                "source": meta.get("source", "sconosciuta"), "added_at": meta.get("added_at"),
                "title": meta.get("title", ""), "method": meta.get("method", ""),
                "resolution": meta.get("resolution"), "chains": meta.get("chains", []),
                "predicted": bool(meta.get("predicted")), "plddt_mean": meta.get("plddt_mean"),
                "alphafold": meta.get("alphafold"),
            })
        return items

    def is_predicted(self, template_id: str) -> bool:
        """Modello predetto (AlphaFold): dai metadati o, per i file registrati prima che esistessero, dall'header."""
        tid = normalize_id(template_id)
        meta = read_json(self._meta_path(tid), default={}) or {}
        if "predicted" in meta:
            return bool(meta["predicted"])
        if meta.get("source") == SOURCE_ALPHAFOLD:
            return True
        return plddt.is_alphafold_text(structure.read_text_head(self.path(tid), 200_000))

    def fetch_alphafold(self, accession: str) -> dict:
        """Modello di AlphaFold DB per un'accessione UniProt, o per un identificativo AF-<ACC>-F<n> (frammenti delle
        proteine lunghe), salvato con id AF-<ACC>-F<n> e i record originali del file."""
        parsed = remote.parse_alphafold_id(accession)
        acc, fragment = parsed if parsed else (remote.validate_uniprot_accession(accession), None)
        guess = normalize_id(f"AF-{acc}-F{fragment or 1}")
        with self._fetch_lock(guess):
            if self.exists(guess):  # già in libreria: nessuna richiesta a EBI
                return {**self.describe(guess), "cached": True}
            entries = remote.alphafold_entries(acc)
            if fragment is None:
                entry = entries[0]
            else:
                entry = next((e for e in entries if e["fragment"] == fragment), None)
                if entry is None:
                    raise NotFound(f"AlphaFold DB non ha il frammento F{fragment} di {acc} (disponibili: "
                                   f"{', '.join(e['entry_id'] for e in entries)}).", code="alphafold_not_found")
            tid = normalize_id(entry["entry_id"])
            if self.exists(tid):  # accessione secondaria di un modello già in libreria
                return {**self.describe(tid), "cached": True}
            text, fmt = remote.alphafold_download(entry)
            other = safe_join(self.root, f"{tid}.{'cif' if fmt == 'pdb' else 'pdb'}")
            dest = safe_join(self.root, f"{tid}.{fmt}")
            other.unlink(missing_ok=True)
            atomic_write_text(dest, text)
            try:
                structure.load_structure(dest, tid)
            except AppError as exc:
                dest.unlink(missing_ok=True)
                raise RemoteServiceError(f"AlphaFold DB: il modello {tid} scaricato non è leggibile ({exc.message}).")
            return {**self._register(tid, dest, source=SOURCE_ALPHAFOLD, alphafold=entry), "cached": False}

    def fetch(self, pdb_id: str, fmt: str = "pdb") -> dict:
        pid = remote.validate_pdb_id(pdb_id)
        if self.exists(pid):
            return {**self.describe(pid), "cached": True}
        fmt = "cif" if fmt == "cif" else "pdb"
        try:
            text = remote.rcsb_download(pid, fmt)
        except NotFound:
            if fmt == "pdb":  # le strutture molto grandi esistono solo in mmCIF
                fmt = "cif"
                text = remote.rcsb_download(pid, fmt)
            else:
                raise
        dest = safe_join(self.root, f"{pid}.{fmt}")
        atomic_write_text(dest, text)
        return {**self._register(pid, dest, source="RCSB PDB"), "cached": False}

    def save_upload(self, filename: str, data: bytes, template_id: Optional[str] = None) -> dict:
        suffix = Path(filename or "").suffix.lower()
        if suffix not in EXTENSIONS:
            raise AppError("Formato non supportato: carica un file .pdb, .ent, .cif o .mmcif.")
        if not data.strip():
            raise AppError("Il file caricato è vuoto.")
        tid = normalize_id(template_id or Path(filename).stem)
        fmt = EXTENSIONS[suffix]
        with self._lock:
            for ext in (".pdb", ".cif"):
                old = safe_join(self.root, f"{tid}{ext}")
                if old.exists():
                    old.unlink()
            dest = safe_join(self.root, f"{tid}.{fmt}")
            atomic_write_bytes(dest, data)
        try:
            structure.load_structure(dest, tid)
        except AppError:
            dest.unlink(missing_ok=True)
            raise
        return self._register(tid, dest, source="caricamento utente")

    def save_text(self, template_id: str, text: str, source: str, fmt: str = "pdb") -> dict:
        tid = normalize_id(template_id)
        dest = safe_join(self.root, f"{tid}.{fmt}")
        atomic_write_text(dest, text)
        return self._register(tid, dest, source=source)

    def _register(self, tid: str, path: Path, source: str, alphafold: Optional[dict] = None) -> dict:
        self._analysis_path(tid).unlink(missing_ok=True)
        analysis = self.analyze(tid)
        meta = {
            "id": tid, "source": source, "added_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "title": analysis["header"].get("title", ""), "method": analysis["header"].get("method", ""),
            "resolution": analysis["header"].get("resolution"),
            "chains": [c["chain"] for c in analysis["chains"]],
        }
        # Modello predetto: scaricato da AlphaFold DB, oppure un file di AlphaFold caricato dall'utente
        # (riconosciuto solo da segni certi nell'header, mai dai soli B-factor).
        if alphafold is not None or plddt.is_alphafold_text(structure.read_text_head(path, 200_000)):
            values = [v for _c, _r, v in plddt.residues(structure.first_model(structure.load_structure(path, tid)))]
            summary = plddt.summarize(values)
            info = alphafold or {}
            meta.update({
                "predicted": True, "note": PREDICTED_NOTE, "method": meta["method"] or PREDICTED_METHOD,
                "title": meta["title"] or info.get("description", ""),
                # pLDDT medio di AlphaFold DB (globalMetricValue) se c'è, altrimenti la media per residuo del file
                "plddt_mean": info.get("plddt") if info.get("plddt") is not None else summary["mean"],
                "plddt_fractions": summary["fractions"],
            })
            if alphafold is not None:
                meta["alphafold"] = {k: alphafold.get(k) for k in ALPHAFOLD_META}
        else:
            meta["predicted"] = False
        atomic_write_json(self._meta_path(tid), meta)
        return self.describe(tid)

    def describe(self, template_id: str) -> dict:
        tid = normalize_id(template_id)
        path = self.path(tid)
        meta = read_json(self._meta_path(tid), default={}) or {}
        return {"id": tid, "format": path.suffix.lstrip("."), "size": path.stat().st_size, **meta}

    def delete(self, template_id: str) -> None:
        tid = normalize_id(template_id)
        path = self.path(tid)
        path.unlink()
        self._meta_path(tid).unlink(missing_ok=True)
        self._analysis_path(tid).unlink(missing_ok=True)

    def text(self, template_id: str) -> str:
        return self.path(template_id).read_text(encoding="utf-8", errors="replace")

    def analyze(self, template_id: str) -> dict:
        """Catene, sequenze, residui mancanti, ligandi e struttura secondaria (in cache per file)."""
        tid = normalize_id(template_id)
        path = self.path(tid)
        cache_path = self._analysis_path(tid)
        cached = read_json(cache_path)
        mtime = path.stat().st_mtime
        if cached and cached.get("mtime") == mtime and cached.get("version") == ANALYSIS_VERSION:
            return cached["data"]
        st = structure.load_structure(path, tid)
        model = structure.first_model(st)
        if path.suffix.lower() == ".cif":
            header = structure.parse_cif_header(path)
        else:
            header = structure.parse_header(structure.read_text_head(path))
        chains = structure.chain_summaries(model)
        assignment = secondary.assign(model)
        ss_by_chain: dict[str, str] = {}
        for row in assignment:
            ss_by_chain[row["chain"]] = ss_by_chain.get(row["chain"], "") + row["ss3"]
        for chain in chains:
            chain["ss"] = ss_by_chain.get(chain["chain"], "")
            chain["missing"] = sum(1 for m in header["missing_residues"] if m["chain"] == chain["chain"])
        data = {
            "id": tid, "format": path.suffix.lstrip(".").lower(), "header": header, "chains": chains,
            "ligands": structure.hetero_groups(model), "stats": structure.structure_stats(model),
            "models": len(st),
        }
        atomic_write_json(cache_path, {"mtime": mtime, "version": ANALYSIS_VERSION, "data": data})
        return data
