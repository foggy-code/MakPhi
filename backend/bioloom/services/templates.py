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
from typing import Callable, Optional

from ..core.errors import AppError, Conflict, NotFound, RemoteServiceError
from ..core.storage import atomic_write_bytes, atomic_write_json, atomic_write_text, read_json, safe_join
from ..domain import assembly as assembly_mod
from ..domain import ligands, plddt, secondary, structure
from ..engines import remote

EXTENSIONS = {".pdb": "pdb", ".ent": "pdb", ".cif": "cif", ".mmcif": "cif"}
# 3: ligandi con la classificazione di domain/ligands (le analisi in cache delle versioni precedenti si ricalcolano)
ANALYSIS_VERSION = 3
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

    # ----------------------------------------------------------------------------- metadati
    def _meta(self, tid: str, path: Optional[Path] = None) -> dict:
        """Metadati di un template. Quelli registrati prima dei modelli predetti (senza `predicted`) si completano
        alla prima lettura con lo stesso riconoscimento dei template nuovi (`_predicted_meta`) e si riscrivono:
        elenco, dettaglio e modellazione (`is_predicted`) usano così la stessa informazione."""
        meta = read_json(self._meta_path(tid), default={}) or {}
        if "predicted" in meta:
            return meta
        try:
            extra = self._predicted_meta(tid, path or self.path(tid), force=meta.get("source") == SOURCE_ALPHAFOLD)
        except AppError:  # file illeggibile: nulla da aggiungere, niente da riscrivere
            return meta
        if extra is None:
            meta["predicted"] = False
        else:
            meta.update(extra)
            meta["method"] = meta.get("method") or PREDICTED_METHOD
        atomic_write_json(self._meta_path(tid), meta)
        return meta

    def _predicted_meta(self, tid: str, path: Path, *, force: bool = False,
                        alphafold: Optional[dict] = None) -> Optional[dict]:
        """Campi dei metadati di un modello predetto (AlphaFold), None per ogni altra struttura.

        Predetto: scaricato da AlphaFold DB (`force`), oppure riconosciuto da `plddt.is_alphafold_model`, lo stesso
        criterio del rapporto di qualità (mai dai soli B-factor: servono segni di AlphaFold nell'intestazione o nel
        nome e nessuna altra origine dichiarata).
        """
        model = structure.first_model(structure.load_structure(path, tid))
        if not force and not plddt.is_alphafold_model(path.name, structure.read_text_head(path, 200_000),
                                                      plddt.atom_values(model)):
            return None
        summary = plddt.summarize([v for _c, _r, v in plddt.residues(model)])
        info = alphafold or {}
        out = {
            "predicted": True, "note": PREDICTED_NOTE,
            # pLDDT medio di AlphaFold DB (globalMetricValue) se c'è, altrimenti la media per residuo del file
            "plddt_mean": info.get("plddt") if info.get("plddt") is not None else summary["mean"],
            "plddt_fractions": summary["fractions"],
        }
        if alphafold is not None:
            out["alphafold"] = {k: alphafold.get(k) for k in ALPHAFOLD_META}
        return out

    # ----------------------------------------------------------------------------- operazioni
    def list(self) -> list[dict]:
        items = []
        for tid, path in sorted(self._files().items()):
            meta = self._meta(tid, path)
            items.append({
                "id": tid, "format": path.suffix.lstrip(".").lower(), "size": path.stat().st_size,
                "source": meta.get("source", "sconosciuta"), "added_at": meta.get("added_at"),
                "title": meta.get("title", ""), "method": meta.get("method", ""),
                "resolution": meta.get("resolution"), "chains": meta.get("chains", []),
                "predicted": bool(meta.get("predicted")), "plddt_mean": meta.get("plddt_mean"),
                "alphafold": meta.get("alphafold"), "assembly": meta.get("assembly"),
            })
        return items

    def is_predicted(self, template_id: str) -> bool:
        """Modello predetto (AlphaFold), dai metadati (completati alla prima lettura per i template precedenti)."""
        return bool(self._meta(normalize_id(template_id)).get("predicted"))

    def fetch_alphafold(self, accession: str, *, check: Optional[Callable[[], None]] = None,
                        log: Optional[Callable[[str], None]] = None) -> dict:
        """Modello di AlphaFold DB per un'accessione UniProt, o per un identificativo AF-<ACC>-F<n> (frammenti delle
        proteine lunghe), salvato con id AF-<ACC>-F<n> e i record originali del file.

        `check` (annullamento del job) interrompe i tentativi verso EBI, le loro pause e l'attesa del download dello
        stesso modello da parte di un altro elemento di pipeline; `log` riceve i messaggi dei nuovi tentativi.
        """
        parsed = remote.parse_alphafold_id(accession)
        acc, fragment = parsed if parsed else (remote.validate_uniprot_accession(accession), None)
        guess = normalize_id(f"AF-{acc}-F{fragment or 1}")
        lock = self._fetch_lock(guess)
        while not lock.acquire(timeout=0.5):
            if check:
                check()
        try:
            if check:  # svegliato dopo l'annullamento: nessun nuovo download
                check()
            if self.exists(guess):  # già in libreria: nessuna richiesta a EBI
                return {**self.describe(guess), "cached": True}
            entries = remote.alphafold_entries(acc, check=check, log=log)
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
            text, fmt = remote.alphafold_download(entry, check=check, log=log)
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
        finally:
            lock.release()

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

    def _register(self, tid: str, path: Path, source: str, alphafold: Optional[dict] = None,
                  extra: Optional[dict] = None) -> dict:
        self._analysis_path(tid).unlink(missing_ok=True)
        analysis = self.analyze(tid)
        meta = {
            "id": tid, "source": source, "added_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "title": analysis["header"].get("title", ""), "method": analysis["header"].get("method", ""),
            "resolution": analysis["header"].get("resolution"),
            "chains": [c["chain"] for c in analysis["chains"]],
            **(extra or {}),
        }
        # Modello predetto: scaricato da AlphaFold DB, oppure un file di AlphaFold caricato dall'utente
        # (riconosciuto come nel rapporto di qualità, mai dai soli B-factor).
        predicted = self._predicted_meta(tid, path, force=alphafold is not None, alphafold=alphafold)
        if predicted is not None:
            meta.update(predicted)
            meta["method"] = meta["method"] or PREDICTED_METHOD
            meta["title"] = meta["title"] or (alphafold or {}).get("description", "")
        else:
            meta["predicted"] = False
        atomic_write_json(self._meta_path(tid), meta)
        return self.describe(tid)

    def describe(self, template_id: str) -> dict:
        tid = normalize_id(template_id)
        path = self.path(tid)
        meta = self._meta(tid, path)
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
        """Catene, sequenze, residui mancanti, ligandi (classificati: metallo, cofattore, additivo, residuo
        modificato, altro ligando) e struttura secondaria (in cache per file)."""
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
            # gruppi eteroatomici candidati al trasferimento nella modellazione, con classe e scelta predefinita
            "ligands": ligands.candidates(model), "stats": structure.structure_stats(model),
            "models": len(st),
        }
        atomic_write_json(cache_path, {"mtime": mtime, "version": ANALYSIS_VERSION, "data": data})
        return data

    # ----------------------------------------------------------------------------- assemblaggi biologici (F5.3)
    def assemblies(self, template_id: str) -> dict:
        """Assemblaggi biologici dichiarati nel file (REMARK 350 del PDB, pdbx_struct_assembly del mmCIF): stato
        oligomerico, catene generate e loro origine (domain/assembly). Si leggono dal file a ogni richiesta."""
        tid = normalize_id(template_id)
        path = self.path(tid)
        meta = self._meta(tid, path)
        out = {"template": tid, **assembly_mod.describe(path)}
        if meta.get("assembly"):
            out["note"] = (f"{tid} è già l'assemblaggio {meta['assembly'].get('assembly_id')} di "
                           f"{meta['assembly'].get('source_id')}.")
            out["assemblies"] = []
        elif meta.get("predicted"):
            out["note"] = "Modello predetto: nessun assemblaggio biologico."
            out["assemblies"] = []
        return out

    def import_assembly(self, template_id: str, assembly_id: str) -> dict:
        """Assemblaggio biologico come nuovo template `<ID>_ASM<n>`: coordinate generate con gli operatori del file
        (domain/assembly.pdb_text), catene rinominate in modo univoco, metadati con l'origine di ogni catena. Già in
        libreria e generato dallo stesso file d'origine (impronta SHA-256): si riusa."""
        from ..core.cache import content_hash

        tid = normalize_id(template_id)
        path = self.path(tid)
        if self._meta(tid, path).get("assembly"):
            raise AppError(f"{tid} è già un assemblaggio biologico.", code="invalid_assembly")
        aid = str(assembly_id or "").strip()
        new_id = normalize_id(f"{tid}_ASM{aid}")
        fingerprint = content_hash(path)
        lock = self._fetch_lock(new_id)
        with lock:
            if self.exists(new_id):
                existing = self._meta(new_id).get("assembly") or {}
                if existing.get("source_id") != tid:  # un template dell'utente con lo stesso nome non si sovrascrive
                    raise Conflict(f"In libreria c'è già un template {new_id} che non è l'assemblaggio {aid} di {tid}: "
                                   "eliminalo o rinominalo prima di importare l'assemblaggio.")
                if existing.get("source_sha256") == fingerprint:
                    return {**self.describe(new_id), "cached": True}
            info = assembly_mod.read(path)
            chosen = assembly_mod.find(info, aid)
            model = structure.first_model(structure.load_structure(path, tid))
            analysis = self.analyze(tid)
            text, chains = assembly_mod.pdb_text(model, info, chosen, source_id=tid, header=analysis["header"])
            dest = safe_join(self.root, f"{new_id}.pdb")
            atomic_write_text(dest, text)  # un assemblaggio rigenerato (file d'origine cambiato) sostituisce il vecchio
            extra = {"assembly": {
                "source_id": tid, "assembly_id": chosen.id, "details": chosen.details, "method": chosen.method,
                "oligomeric_details": chosen.oligomeric_details, "oligomeric_count": chosen.oligomeric_count,
                "chains": chains, "format": info.format, "source_sha256": fingerprint}}
            described = self._register(new_id, dest, source=f"assemblaggio {chosen.id} di {tid}", extra=extra)
        return {**described, "cached": False}
