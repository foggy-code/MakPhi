"""Libreria globale dei template strutturali (data/templates)."""

from __future__ import annotations

import re
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from ..core.errors import AppError, NotFound
from ..core.storage import atomic_write_bytes, atomic_write_json, atomic_write_text, read_json, safe_join
from ..domain import secondary, structure
from ..engines import remote

EXTENSIONS = {".pdb": "pdb", ".ent": "pdb", ".cif": "cif", ".mmcif": "cif"}
ANALYSIS_VERSION = 2


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
            })
        return items

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

    def _register(self, tid: str, path: Path, source: str) -> dict:
        self._analysis_path(tid).unlink(missing_ok=True)
        analysis = self.analyze(tid)
        meta = {
            "id": tid, "source": source, "added_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "title": analysis["header"].get("title", ""), "method": analysis["header"].get("method", ""),
            "resolution": analysis["header"].get("resolution"),
            "chains": [c["chain"] for c in analysis["chains"]],
        }
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
