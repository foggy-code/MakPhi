"""Libreria globale dei template strutturali (data/templates).

Oltre alle strutture sperimentali (RCSB) e ai file caricati, contiene i modelli di AlphaFold DB (`AF-<ACC>-F<n>`):
il file resta quello originale, con i record HEADER/TITLE/REMARK di AlphaFold e il pLDDT nella colonna B-factor, e
i metadati li segnano come modelli predetti (`predicted`), con versione, pLDDT medio e intervallo UniProt coperto.
"""

from __future__ import annotations

import hashlib
import os
import re
import tempfile
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

from ..core.errors import AppError, Conflict, NotFound, RemoteServiceError
from ..core.storage import atomic_write_bytes, atomic_write_json, atomic_write_text, read_json, remove_tree, safe_join
from ..domain import assembly as assembly_mod
from ..domain import composition, ligands, plddt, secondary, structure
from ..engines import remote

EXTENSIONS = {".pdb": "pdb", ".ent": "pdb", ".cif": "cif", ".mmcif": "cif"}
OTHER_FORMAT = {"pdb": "cif", "cif": "pdb"}
# Cartelle temporanee dei file in arrivo, dentro la libreria (stesso disco: il file entra con un rename). Il punto
# iniziale le tiene fuori dall'indice della ricerca globale (l'elenco dei template conta solo i file); quelle rimaste da
# un arresto si tolgono all'avvio.
STAGING_PREFIX = ".upload-"
# 3: ligandi con la classificazione di domain/ligands (le analisi in cache delle versioni precedenti si ricalcolano)
# 4: acque candidate al trasferimento (domain/ligands.water_candidates)
# 5: entità dichiarate nel file e composizione delle catene (domain/composition: monomero, omo- o etero-oligomero)
ANALYSIS_VERSION = 5
SOURCE_ALPHAFOLD = "alphafold"
PREDICTED_NOTE = "modello predetto, non struttura sperimentale"
PREDICTED_METHOD = "Modello predetto (AlphaFold)"
# metodo mostrato per predittore (domain/plddt.predictor); una predizione di ESMFold (F9.3) caricata nella libreria
PREDICTED_METHODS = {"alphafold": PREDICTED_METHOD, "esmfold": "Modello predetto (ESMFold)"}
# Campi della voce di AlphaFold DB conservati nei metadati del template
ALPHAFOLD_META = ("entry_id", "accession", "fragment", "version", "uniprot_start", "uniprot_end", "uniprot_id",
                  "gene", "description", "organism", "tax_id", "created", "tool", "pae_url")


MAX_ID = 40


def _clean_id(raw: str) -> str:
    clean = re.sub(r"[^A-Za-z0-9_-]", "_", (raw or "").strip()).strip("_-").upper()
    return re.sub(r"_+", "_", clean)


def normalize_id(raw: str) -> str:
    clean = _clean_id(raw)[:MAX_ID]
    if not clean:
        raise AppError("Identificativo del template non valido.")
    return clean


def assembly_template_id(template_id: str, assembly_id: str) -> str:
    """Id del template di un assemblaggio: `<ID>_ASM<n>`, senza troncamenti che farebbero coincidere assemblaggi
    diversi (con `normalize_id` gli assemblaggi 1 e 10 di un id di 35 caratteri diventavano lo stesso template). Se
    supera `MAX_ID` caratteri, l'id d'origine si accorcia e prende un'impronta di 6 cifre esadecimali (SHA-1 dell'id
    intero): `<inizio>_<impronta>_ASM<n>`, uguale a ogni richiesta e diverso per ogni id d'origine."""
    suffix = _clean_id(f"ASM{assembly_id}")
    full = _clean_id(f"{template_id}_{suffix}")
    if len(full) <= MAX_ID:
        return full
    digest = hashlib.sha1(template_id.encode("utf-8")).hexdigest()[:6].upper()
    keep = MAX_ID - len(f"_{digest}_{suffix}")
    return _clean_id(f"{template_id[:max(1, keep)]}_{digest}_{suffix}")[:MAX_ID]


def exists_conflict(tid: str, jobs: int = 0, projects: int = 0) -> Conflict:
    """409 `template_exists`: un caricamento con l'id di un template già in libreria, senza conferma della
    sostituzione. `jobs` e `projects`: job che citano il template nei parametri e loro progetti, se noti."""
    used = ""
    if jobs:
        used = f", usato da {jobs} job" + (f" in {projects} progett{'o' if projects == 1 else 'i'}" if projects else "")
    return Conflict(f"Nella libreria c'è già un template {tid}{used}. Per sostituirlo conferma la sovrascrittura (i "
                    f"calcoli successivi useranno il nuovo file); altrimenti scegli un altro identificativo.",
                    code="template_exists", details={"id": tid, "jobs": jobs, "projects": projects})


class TemplateLibrary:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)
        for leftover in self.root.glob(f"{STAGING_PREFIX}*"):  # file in arrivo lasciati da un arresto
            remove_tree(leftover)
        self._lock = threading.Lock()
        # un download alla volta per modello AlphaFold: elementi di pipeline in parallelo con lo stesso template
        # aspettano il primo e poi lo trovano in libreria
        self._fetching: dict[str, threading.Lock] = {}
        self._writing: dict[str, threading.Lock] = {}
        self._listeners: list[Callable[[str, str], None]] = []

    def _fetch_lock(self, tid: str) -> threading.Lock:
        with self._lock:
            return self._fetching.setdefault(tid, threading.Lock())

    def _write_lock(self, tid: str) -> threading.Lock:
        """Una scrittura alla volta per id, dal file in libreria ai suoi metadati (`_install` + `_register`): chi
        installa per ultimo registra per ultimo, e una sostituzione con l'altro formato non toglie il file a chi lo sta
        ancora registrando. Si prende per ultimo (dopo `_fetch_lock`, mai il contrario) e solo attorno alla scrittura,
        non attorno ai download."""
        with self._lock:
            return self._writing.setdefault(tid, threading.Lock())

    # ----------------------------------------------------------------------------- notifiche (F8b)
    def subscribe(self, listener: Callable[[str, str], None]) -> None:
        """`listener(evento, id)` dopo ogni modifica fatta dalla libreria: "changed" (template aggiunto o sostituito)
        o "deleted". La usano l'indice della ricerca globale e la pulizia di etichette e preferiti
        (services/labels.py); le modifiche fatte a mano nella cartella le scopre l'indice dalle date dei file."""
        self._listeners.append(listener)

    def _notify(self, event: str, tid: str) -> None:
        for listener in list(self._listeners):
            try:
                listener(event, tid)
            except Exception as exc:  # l'operazione sulla libreria è già fatta: un ascoltatore non la annulla
                print(f"[BioLoom] notifica della libreria ({event} {tid}) non riuscita: {exc}")

    def ids(self) -> list[str]:
        """Identificativi dei template presenti, dai soli nomi dei file (nessuna lettura dei metadati)."""
        return sorted(self._files())

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
            meta["method"] = meta.get("method") or PREDICTED_METHODS.get(extra.get("predictor"), PREDICTED_METHOD)
        atomic_write_json(self._meta_path(tid), meta)
        return meta

    def _predicted_meta(self, tid: str, path: Path, *, force: bool = False,
                        alphafold: Optional[dict] = None) -> Optional[dict]:
        """Campi dei metadati di un modello predetto (AlphaFold, ESMFold), None per ogni altra struttura.

        Predetto: scaricato da AlphaFold DB (`force`), oppure riconosciuto da `plddt.predictor`, lo stesso criterio
        del rapporto di qualità (mai dai soli B-factor: servono segni di AlphaFold o di ESMFold nell'intestazione, o il
        nome di AlphaFold DB, e nessuna altra origine dichiarata).
        """
        model = structure.first_model(structure.load_structure(path, tid))
        found = plddt.predictor(path.name, structure.read_text_head(path, 200_000), plddt.atom_values(model))
        if not force and found is None:
            return None
        summary = plddt.summarize([v for _c, _r, v in plddt.residues(model)])
        info = alphafold or {}
        out = {
            "predicted": True, "note": PREDICTED_NOTE,
            # pLDDT medio di AlphaFold DB (globalMetricValue) se c'è, altrimenti la media per residuo del file
            "plddt_mean": info.get("plddt") if info.get("plddt") is not None else summary["mean"],
            "plddt_fractions": summary["fractions"],
            "predictor": "alphafold" if force else found,
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
        """Modello predetto (AlphaFold, ESMFold), dai metadati (completati alla prima lettura per i template
        precedenti)."""
        return bool(self._meta(normalize_id(template_id)).get("predicted"))

    def predictor(self, template_id: str) -> Optional[str]:
        """Predittore di un modello predetto riconosciuto da `plddt.predictor` ("alphafold", "esmfold"), dai
        metadati; None per le strutture sperimentali e per i metadati che non lo registrano."""
        meta = self._meta(normalize_id(template_id))
        return meta.get("predictor") if meta.get("predicted") else None

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
            with self._write_lock(tid):
                try:
                    installed = self._install(tid, fmt, text.encode("utf-8"))
                except AppError as exc:
                    raise RemoteServiceError(f"AlphaFold DB: il modello {tid} scaricato non è leggibile "
                                             f"({exc.message}).")
                if installed is None:  # caricato dall'utente nel frattempo: il download non lo sostituisce
                    return {**self.describe(tid), "cached": True}
                dest = safe_join(self.root, f"{tid}.{fmt}")
                return {**self._register(tid, dest, source=SOURCE_ALPHAFOLD, alphafold=entry), "cached": False}
        finally:
            lock.release()

    def fetch(self, pdb_id: str, fmt: str = "pdb") -> dict:
        """Struttura di RCSB PDB nella libreria; già presente (anche caricata dall'utente con lo stesso id) si usa
        quella, senza download e senza sostituirla."""
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
        with self._write_lock(pid):
            try:
                installed = self._install(pid, fmt, text.encode("utf-8"))
            except AppError as exc:
                raise RemoteServiceError(f"RCSB PDB: la struttura {pid} scaricata non è leggibile ({exc.message}).")
            if installed is None:  # arrivata nel frattempo (caricamento, altro job): il download non la sostituisce
                return {**self.describe(pid), "cached": True}
            return {**self._register(pid, safe_join(self.root, f"{pid}.{fmt}"), source="RCSB PDB"), "cached": False}

    def save_upload(self, filename: str, data: bytes, template_id: Optional[str] = None, *, overwrite: bool = False,
                    usage: Optional[Callable[[str], tuple[int, int]]] = None) -> dict:
        """File caricato dall'utente come template `template_id` (senza: dal nome del file).

        Un id già in libreria si sostituisce solo con `overwrite`; altrimenti `exists_conflict` (409
        `template_exists`), subito, senza scrivere né leggere il file, con i job e i progetti che lo usano
        (`usage(id)` → (job, progetti), se data). Il file si legge prima di entrare nella libreria (`_install`): uno
        illeggibile non tocca il template con lo stesso id. Sostituito, metadati e analisi si rifanno dal nuovo file
        (anche cambiando formato, .pdb ↔ .cif). Restituisce la descrizione del template e `replaced`.
        """
        suffix = Path(filename or "").suffix.lower()
        if suffix not in EXTENSIONS:
            raise AppError("Formato non supportato: carica un file .pdb, .ent, .cif o .mmcif.")
        if not data.strip():
            raise AppError("Il file caricato è vuoto.")
        tid = normalize_id(template_id or Path(filename).stem)
        fmt = EXTENSIONS[suffix]

        def conflict() -> Conflict:
            return exists_conflict(tid, *(usage(tid) if usage else (0, 0)))

        if not overwrite and self.exists(tid):
            raise conflict()
        with self._write_lock(tid):
            replaced = self._install(tid, fmt, data, overwrite=overwrite)
            if replaced is None:  # caricato da un'altra richiesta mentre questo file si leggeva
                raise conflict()
            try:
                described = self._register(tid, safe_join(self.root, f"{tid}.{fmt}"), source="caricamento utente")
            except BaseException:
                # analisi non riuscita: i metadati del file sostituito non descrivono il nuovo (si rifanno alla
                # lettura); sotto il lock per id sono di questa richiesta, non di una sostituzione arrivata dopo
                self._meta_path(tid).unlink(missing_ok=True)
                raise
        return {**described, "replaced": replaced}

    def _install(self, tid: str, fmt: str, data: bytes, *, overwrite: bool = False) -> Optional[bool]:
        """Mette `data` nella libreria come `<tid>.<fmt>`, letto prima di entrarci.

        Il file si scrive e si legge in una cartella temporanea della libreria (stesso disco), poi entra con un rename
        atomico sotto il lock: un file illeggibile solleva l'errore di domain/structure e la libreria resta com'era.
        Con un template `tid` già presente e senza `overwrite` non cambia nulla e restituisce None; altrimenti True se
        ha sostituito un template, False se è nuovo. Il file con l'altro formato (.pdb ↔ .cif) si toglie solo dopo che
        il nuovo è al suo posto: chi legge trova sempre il vecchio file o il nuovo, mai nessuno dei due.
        """
        staging = Path(tempfile.mkdtemp(prefix=STAGING_PREFIX, dir=self.root))
        try:
            staged = staging / f"{tid}.{fmt}"  # stesso nome del file definitivo: gli errori di lettura lo citano
            atomic_write_bytes(staged, data)
            structure.load_structure(staged, tid)
            with self._lock:
                replaced = self.exists(tid)
                if replaced and not overwrite:
                    return None
                os.replace(staged, safe_join(self.root, f"{tid}.{fmt}"))
                safe_join(self.root, f"{tid}.{OTHER_FORMAT[fmt]}").unlink(missing_ok=True)
                return replaced
        finally:
            remove_tree(staging)

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
            meta["method"] = meta["method"] or PREDICTED_METHODS.get(predicted.get("predictor"), PREDICTED_METHOD)
            meta["title"] = meta["title"] or (alphafold or {}).get("description", "")
        else:
            meta["predicted"] = False
        atomic_write_json(self._meta_path(tid), meta)
        self._notify("changed", tid)
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
        self._notify("deleted", tid)  # etichette e stella del template se ne vanno con il file

    def text(self, template_id: str) -> str:
        return self.path(template_id).read_text(encoding="utf-8", errors="replace")

    def analyze(self, template_id: str) -> dict:
        """Catene, sequenze, residui mancanti, ligandi (classificati: metallo, cofattore, additivo, residuo
        modificato, altro ligando), acque legate alla struttura e struttura secondaria (in cache per file)."""
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
            # acque legate alla struttura (acque catalitiche e strutturali), da scegliere una per una
            "waters": ligands.water_candidates(model),
            "models": len(st),
            "composition": composition.describe(chains, header.get("entities"), header.get("seqres")),
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
        """Assemblaggio biologico come nuovo template `<ID>_ASM<n>` (`assembly_template_id`): coordinate generate con
        gli operatori del file (domain/assembly.pdb_text), catene rinominate in modo univoco, metadati con l'origine di
        ogni catena. L'assemblaggio chiesto si verifica prima di tutto (404 se il file non lo dichiara). Già in libreria,
        come lo stesso assemblaggio dello stesso template e generato dallo stesso file d'origine (impronta SHA-256): si
        riusa. Un template con quel nome che non è questo assemblaggio (un altro assemblaggio, un altro template, un
        file dell'utente) non si restituisce né si sovrascrive: Conflict."""
        from ..core.cache import content_hash

        tid = normalize_id(template_id)
        path = self.path(tid)
        if self._meta(tid, path).get("assembly"):
            raise AppError(f"{tid} è già un assemblaggio biologico.", code="invalid_assembly")
        aid = str(assembly_id or "").strip()
        info = assembly_mod.read(path)
        chosen = assembly_mod.find(info, aid)
        new_id = assembly_template_id(tid, chosen.id)
        fingerprint = content_hash(path)
        lock = self._fetch_lock(new_id)
        with lock:
            if self.exists(new_id):
                existing = self._meta(new_id).get("assembly") or {}
                if existing.get("source_id") != tid or existing.get("assembly_id") != chosen.id:
                    raise Conflict(f"In libreria c'è già un template {new_id} che non è l'assemblaggio {chosen.id} di "
                                   f"{tid}: eliminalo o rinominalo prima di importare l'assemblaggio.")
                if existing.get("source_sha256") == fingerprint:
                    return {**self.describe(new_id), "cached": True}
            model = structure.first_model(structure.load_structure(path, tid))
            analysis = self.analyze(tid)
            text, chains = assembly_mod.pdb_text(model, info, chosen, source_id=tid, header=analysis["header"])
            dest = safe_join(self.root, f"{new_id}.pdb")
            extra = {"assembly": {
                "source_id": tid, "assembly_id": chosen.id, "details": chosen.details, "method": chosen.method,
                "oligomeric_details": chosen.oligomeric_details, "oligomeric_count": chosen.oligomeric_count,
                "chains": chains, "format": info.format, "source_sha256": fingerprint}}
            with self._write_lock(new_id):
                # un assemblaggio rigenerato (file d'origine cambiato) sostituisce il vecchio
                atomic_write_text(dest, text)
                described = self._register(new_id, dest, source=f"assemblaggio {chosen.id} di {tid}", extra=extra)
        return {**described, "cached": False}
