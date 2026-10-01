"""Pacchetti di riproducibilità e riesecuzione (F9.4).

**Esportazione** (`ReproducibilityService.export`, rotta `GET /api/jobs/{id}/package`): un job completato diventa un
archivio ZIP (struttura e regole in domain/reproducibility.py), scritto su un file temporaneo dello scratch e inviato
con `FileResponse` come gli altri archivi. Contiene:

- **manifest**: versione di BioLoom e dell'immagine, versioni degli strumenti usati dal calcolo (engines/versions.py:
  MODELLER, DSSP, TM-align/US-align, BLAST+/MMseqs2, PROCHECK/ProSA, OpenMM…), Python e librerie di calcolo, parametri
  completi del job, semi casuali, impostazioni che influiscono sul risultato (`RELEVANT_SETTINGS`), provenienza del
  risultato dalla cache dei calcoli, input remoti con la data (template scaricati da RCSB o AlphaFold DB, ricerche,
  QMEAN di SWISS-MODEL), timbro dei database locali, SHA-256 e dimensione di ogni file, impronta del manifest;
- **input**: parametri, sequenze, template della libreria (file originale e metadati) e copie preparate del job,
  allineamento PIR usato da MODELLER, strutture analizzate (template, file caricati, file di altri job), allineamento
  della conservazione;
- **output**: tutti i file del job; **script** MODELLER eseguiti (documentazione); **log** del job.

Mai chiavi di licenza (MODELLER, codici ProSA), email, chiave d'accesso o percorsi locali: ogni testo passa da un
`Redactor` (segreti → «[rimosso]», cartella dati, scratch, home e installazione → segnaposti) e ogni file, oscurato,
si ricontrolla prima di entrare nell'archivio.

**Pacchetto caricato** (`open_package`): input non fidato. Limiti e controlli dell'archivio (percorsi, link simbolici,
duplicati, cifratura, dimensioni e rapporto di compressione), manifest valido con l'impronta corrispondente, file
presenti tutti e soli quelli dichiarati, checksum SHA-256 verificati leggendo a blocchi con il limite della dimensione
dichiarata. Nessun codice del pacchetto si esegue: gli script sono solo documentazione e non si estraggono.

**Riesecuzione** (job `rerun`, rotta `POST /api/jobs/rerun`, comando `python bioloom.py rerun`): i parametri del
manifest si validano con gli stessi modelli Pydantic delle rotte di avvio e gli stessi controlli immediati; il calcolo
gira con gli handler di sempre (`PackagedCompute`: libreria privata con i template del pacchetto, strutture del
pacchetto, nessun download), cache dei calcoli ignorata (`use_cache` False), nella sottocartella `riesecuzione/` con gli
stessi percorsi del job originale. Poi ogni file si confronta con quello del pacchetto (domain/reproducibility.
compare_file) e le metriche principali si mettono a confronto: esito `identico`, `equivalente` o `diverso`, rapporto
leggibile `RAPPORTO_RIESECUZIONE.txt` e `confronto.json` nel job. Versioni diverse di BioLoom o degli strumenti sono
avvisi, non errori.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import time
import uuid
import zipfile
import zlib
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, BinaryIO, Callable, Iterator, Optional

from .. import __version__
from ..config import ROOT_DIR, Paths
from ..core import cache
from ..core.db import now_iso
from ..core.errors import AppError, Conflict, NotFound
from ..core.jobs import JobContext, JobManager, default_env, is_intermediate
from ..core.storage import atomic_write_json, atomic_write_text, remove_tree, safe_join, validate_identifier
from ..domain import complexes, refinement, sequences
from ..domain import reproducibility as repro
from ..engines import modeller, quality, search, versions
from .analysis import AnalysisService
from .compute import ComputeService, complex_templates
from .refine import RefineService
from .structure_tools import StructureToolsService
from .templates import TemplateLibrary, normalize_id

if TYPE_CHECKING:
    from .licenses import LicenseService

KIND = "rerun"
PACKAGE_NAME = "package.zip"  # pacchetto originale, nella cartella del job di riesecuzione
RERUN_DIR = "riesecuzione"  # file del calcolo rieseguito, con gli stessi percorsi della cartella del job originale
REPORT_JSON = "confronto.json"
REPORT_TEXT = "RAPPORTO_RIESECUZIONE.txt"
STAGE_PREFIX = "rerun_"  # pacchetti caricati in attesa dell'avvio, nello scratch (eliminati dopo 24 ore)
INCOMING = ".rerun-incoming"  # copie in arrivo nella cartella dei job, spostate nel job alla creazione
# File più grandi di così si copiano a blocchi senza oscuramento (solo il controllo dei segreti) e si confrontano con
# il solo checksum: in memoria resta al più un file di questa dimensione.
REDACT_MAX_BYTES = 64 * 1024 ** 2
COMPARE_MAX_BYTES = 64 * 1024 ** 2
CHUNK = 1024 ** 2
# Impostazioni che influiscono sul risultato o sui file prodotti (nel manifest); le altre (email, interprete di
# MODELLER, processori, conservazione degli upload…) no, e alcune sono dati personali o percorsi locali.
RELEVANT_SETTINGS = {
    "compute_cache": "cache dei calcoli: ricerche e allineamenti possono venire da un calcolo precedente",
    "keep_intermediates": "file intermedi di MODELLER conservati fra i risultati",
    "job_timeout_minutes": "timeout dei processi: un calcolo più lungo fallisce",
}
QMEAN_SETTINGS = ("qmean_mode", "qmean_fallback_remote", "qmean_remote", "qmean_uniclust")
SEARCH_SERVICE = {"rcsb": "RCSB PDB (ricerca per sequenza)", "ncbi": "NCBI BLAST (remoto)",
                  "alphafold": "AlphaFold DB (EBI) e NCBI BLAST su Swiss-Prot"}
# Metriche principali mostrate nel confronto, per tipo di job
MODEL_METRICS = (("dope", "DOPE"), ("zdope", "zDOPE"), ("ga341", "GA341"), ("molpdf", "molpdf (funzione obiettivo)"))
QUALITY_METRICS = (("grade", "Voto"), ("grade_score", "Punteggio BioLoom"), ("rama_favored", "Ramachandran favorite (%)"),
                   ("rama_outliers", "Ramachandran outlier (%)"), ("clashscore", "Clashscore"),
                   ("helix_pct", "Elica (%)"), ("strand_pct", "Foglietto (%)"), ("dope", "DOPE"), ("zdope", "zDOPE"),
                   ("prosa_z", "ProSA z-score"), ("procheck_core", "PROCHECK core (%)"),
                   ("procheck_g_overall", "PROCHECK G-factor"))
STRUCTURAL_METRICS = (("tm_score", "TM-score"), ("rmsd", "RMSD (Å)"), ("aligned_length", "Residui allineati"),
                      ("seq_identity", "Identità di sequenza"), ("gdt_ts", "GDT-TS"), ("gdt_ha", "GDT-HA"),
                      ("lddt", "lDDT"))
INTERACTION_METRICS = (("hbonds", "Legami idrogeno"), ("salt_bridges", "Ponti salini"), ("disulfides", "Ponti disolfuro"),
                       ("pi_stacking", "π-stacking"), ("cation_pi", "Catione-π"))


def _start_models() -> dict:
    """Modelli Pydantic delle rotte di avvio, per tipo (import ritardato: le rotte importano i servizi)."""
    from ..api.analysis import AnalysisIn
    from ..api.compute import Align2dIn, AlignIn, LoopIn, ModelIn, QmeanIn, QualityIn, SearchIn
    from ..api.structure_tools import ElectrostaticsIn, PocketsIn, RefineIn

    return {"model": ModelIn, "loop": LoopIn, "align2d": Align2dIn, "quality": QualityIn, "align": AlignIn,
            "analysis": AnalysisIn, "refine": RefineIn, "pockets": PocketsIn, "electrostatics": ElectrostaticsIn,
            "search": SearchIn, "qmean": QmeanIn}


def _utc(value: Optional[str]) -> Optional[datetime]:
    try:
        moment = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def _duration(job: dict) -> Optional[float]:
    start, end = _utc(job.get("started_at")), _utc(job.get("finished_at"))
    return round((end - start).total_seconds(), 1) if start and end else None


def _file_sha256(path: Path) -> str:
    return cache.content_hash(path)


# ------------------------------------------------------------------------------------------ pacchetto caricato
@dataclass
class Package:
    """Pacchetto aperto e verificato: percorso dell'archivio, manifest, file dell'archivio."""

    path: Path
    manifest: dict
    members: dict[str, zipfile.ZipInfo]

    @property
    def kind(self) -> str:
        return self.manifest["job"]["kind"]

    @property
    def files(self) -> dict[str, dict]:
        return repro.declared_files(self.manifest)

    def read(self, name: str, limit: int = COMPARE_MAX_BYTES) -> Optional[bytes]:
        """Byte di un file dichiarato (None se manca o supera `limit`)."""
        info = self.members.get(name)
        if info is None or info.file_size > limit:
            return None
        with zipfile.ZipFile(self.path) as zf:
            return b"".join(_stream(zf, info))

    def result(self) -> dict:
        """Risultato del job originale (outputs/result.json), {} se manca o non si legge."""
        try:
            data = json.loads((self.read("outputs/result.json") or b"{}").decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}


def _stream(zf: zipfile.ZipFile, info: zipfile.ZipInfo) -> Iterator[bytes]:
    """Contenuto di un file dell'archivio a blocchi, mai oltre la dimensione dichiarata (intestazioni falsificate)."""
    read = 0
    with zf.open(info) as fh:
        while True:
            chunk = fh.read(CHUNK)
            if not chunk:
                break
            read += len(chunk)
            if read > info.file_size:
                raise AppError(f"Il file {info.filename} del pacchetto è più grande di quanto dichiarato: pacchetto "
                               "rifiutato.", code="package_bomb")
            yield chunk


def open_package(path: Path, *, verify: bool = True) -> Package:
    """Apre e verifica un pacchetto (vedi il docstring del modulo); AppError con un codice `package_*` se non va."""
    try:
        size = path.stat().st_size
    except OSError:
        raise NotFound("Pacchetto non trovato (forse eliminato dalla pulizia automatica dopo 24 ore): caricalo di "
                       "nuovo.", code="package_not_found")
    if size > repro.MAX_PACKAGE_BYTES:
        raise AppError(f"Il pacchetto supera {repro.MAX_PACKAGE_BYTES // 1024 ** 2} MB.", code="package_too_large",
                       status=413)
    try:
        with zipfile.ZipFile(path) as zf:
            members = repro.check_archive(zf.infolist())
            try:
                manifest = json.loads(b"".join(_stream(zf, members[repro.MANIFEST])).decode("utf-8"))
            except (UnicodeDecodeError, ValueError):
                raise AppError("manifest.json del pacchetto non è un JSON valido.", code="package_manifest_invalid")
            repro.validate_manifest(manifest)
            declared = repro.declared_files(manifest)
            present = set(members) - {repro.MANIFEST}
            undeclared = sorted(present - set(declared))
            missing = sorted(set(declared) - present)
            if undeclared:
                raise AppError(f"Il pacchetto contiene file non elencati nel manifest: {', '.join(undeclared[:5])}"
                               + ("…" if len(undeclared) > 5 else "") + ".", code="package_undeclared")
            if missing:
                raise AppError(f"Mancano nel pacchetto file elencati nel manifest: {', '.join(missing[:5])}"
                               + ("…" if len(missing) > 5 else "") + ".", code="package_missing_file")
            if verify:
                bad = []
                for name, entry in declared.items():
                    digest, count = hashlib.sha256(), 0
                    for chunk in _stream(zf, members[name]):
                        digest.update(chunk)
                        count += len(chunk)
                    if digest.hexdigest() != entry["sha256"] or count != entry["size"]:
                        bad.append(name)
                if bad:
                    raise AppError(f"Checksum SHA-256 non corrispondente per {', '.join(bad[:5])}"
                                   + ("…" if len(bad) > 5 else "") + ": il pacchetto è danneggiato o è stato modificato "
                                   "dopo l'esportazione.", code="package_checksum")
    except (zipfile.BadZipFile, zlib.error, EOFError, NotImplementedError, RuntimeError):
        raise AppError("Il file non è un archivio ZIP valido o è danneggiato.", code="package_invalid")
    return Package(path, manifest, members)


def extract(pkg: Package, dest: Path, roles: frozenset[str]) -> list[str]:
    """Estrae in `dest` i file dichiarati con uno dei `roles`, ricontrollando il checksum durante la scrittura.
    Restituisce i percorsi estratti. Mai gli script né il log: nessun codice del pacchetto finisce su disco."""
    out = []
    with zipfile.ZipFile(pkg.path) as zf:
        for name, entry in sorted(pkg.files.items()):
            if entry["role"] not in roles or entry["role"] in ("script", "log"):
                continue
            target = safe_join(dest, *name.split("/"))
            target.parent.mkdir(parents=True, exist_ok=True)
            digest = hashlib.sha256()
            with open(target, "wb") as fh:
                for chunk in _stream(zf, pkg.members[name]):
                    digest.update(chunk)
                    fh.write(chunk)
            if digest.hexdigest() != entry["sha256"]:
                raise AppError(f"Checksum SHA-256 non corrispondente per {name}: il pacchetto è cambiato durante la "
                               "riesecuzione.", code="package_checksum")
            out.append(name)
    return out


# ------------------------------------------------------------------------------------------ calcolo dal pacchetto
class PackagedCompute(ComputeService):
    """Gli handler di ComputeService con template e strutture del pacchetto estratto in `root`: libreria privata
    (`inputs/library`), file caricati (`inputs/uploads`), file di altri job (`inputs/jobs/<id>/…`). Nessun download: un
    template che manca nel pacchetto è un errore, mai una richiesta a RCSB o ad AlphaFold DB."""

    def __init__(self, base: ComputeService, root: Path) -> None:
        inputs = root / "inputs"
        super().__init__(base.jobs, TemplateLibrary(inputs / "library"), inputs / "uploads", base.databases_dir,
                         register=False)
        self.job_files = inputs / "jobs"

    def resolve_structure(self, source: dict) -> tuple[Path, str]:
        if (source or {}).get("type") == "job":
            job_id = validate_identifier(str(source.get("job_id") or "").strip(), "job_id")
            rel = str(source.get("file") or "").replace("\\", "/")
            if not rel.strip() or repro.job_path_problem(rel):
                raise AppError("Indica il job e il file della struttura da analizzare.", code="invalid_source")
            path = safe_join(self.job_files, job_id, *rel.split("/"))
            if not path.is_file():
                raise AppError(f"Il pacchetto non contiene il file {rel} del job {job_id}.", code="package_missing_input")
            return path, path.stem
        return super().resolve_structure(source)

    def _prepare_templates(self, ctx: JobContext, specs: list, workdir: Path, auto_fetch: bool = True,
                           plddt_min: Optional[float] = None, cache=None) -> list[dict]:
        missing = [s.id for s in specs if not self.library.exists(s.id)]
        if missing:
            raise AppError(f"Il pacchetto non contiene il template {', '.join(missing)}: la riesecuzione non scarica "
                           "nulla da internet.", code="package_missing_input")
        return super()._prepare_templates(ctx, specs, workdir, auto_fetch=False, plddt_min=plddt_min, cache=cache)


class PackagedAnalysis(AnalysisService):
    """Analisi strutturale con l'allineamento della conservazione dal pacchetto (`inputs/jobs/<id>/alignment.json`)
    invece che dal job di allineamento della cartella dati."""

    def __init__(self, jobs: JobManager, compute: PackagedCompute) -> None:
        super().__init__(jobs, compute)
        self.packaged = compute

    def _job_alignment(self, job_id: str) -> tuple[list[str], list[str], str]:
        path = safe_join(self.packaged.job_files, validate_identifier(job_id, "job_id"), "alignment.json")
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            raise AppError(f"Il pacchetto non contiene l'allineamento del job {job_id}.", code="package_missing_input")
        ids, aligned = list(data.get("ids") or []), list(data.get("aligned") or [])
        if len(aligned) < 2 or len(ids) != len(aligned):
            raise AppError(f"Allineamento del job {job_id} non valido nel pacchetto.", code="conservation_invalid")
        return [str(i) for i in ids], [str(s) for s in aligned], \
            f"job di allineamento {job_id}: {data.get('method') or data.get('mode') or 'allineamento'}"


# ------------------------------------------------------------------------------------------ costruzione dell'archivio
class _Builder:
    """File del pacchetto in scrittura: oscuramento, controllo dei segreti, checksum, voci del manifest."""

    def __init__(self, zf: zipfile.ZipFile, redactor: repro.Redactor) -> None:
        self.zf = zf
        self.redactor = redactor
        self.entries: list[dict] = []
        self.notes: list[str] = []
        self._paths: set[str] = set()

    def _info(self, path: str, data_size: int) -> zipfile.ZipInfo:
        info = zipfile.ZipInfo(path, date_time=time.localtime()[:6])
        info.external_attr = (stat.S_IFREG | 0o644) << 16
        info.compress_type = zipfile.ZIP_DEFLATED
        info.file_size = data_size
        return info

    def _accept(self, path: str) -> bool:
        if path in self._paths:
            return False
        problem = repro.member_problem(path)
        if problem:
            self.notes.append(f"File non incluso: {path} ({problem}).")
            return False
        return True

    def _entry(self, path: str, role: str, digest: str, size: int, job_path: Optional[str], redacted: bool) -> None:
        entry = {"path": path, "role": role, "sha256": digest, "size": size}
        if job_path:
            entry["job_path"] = job_path
        if redacted:
            entry["redacted"] = True
        self.entries.append(entry)
        self._paths.add(path)

    def add_bytes(self, path: str, data: bytes, role: str, job_path: Optional[str] = None) -> bool:
        if not self._accept(path):
            return False
        data, redacted = self.redactor.data(data)
        if self.redactor.leaks(data):  # file non testuale con un segreto: fuori dal pacchetto
            self.notes.append(f"File non incluso: {path} (conteneva dati riservati).")
            return False
        info = self._info(path, len(data))
        # file grande e molto comprimibile: senza compressione, perché all'importazione non sembri una bomba di
        # decompressione (il livello 1 comprime meno del livello predefinito: margine di 4 sul limite)
        if len(data) > repro.RATIO_MIN_BYTES and \
                len(data) > repro.MAX_RATIO // 4 * max(1, len(zlib.compress(data, 1))):
            info.compress_type = zipfile.ZIP_STORED
        self.zf.writestr(info, data)
        self._entry(path, role, hashlib.sha256(data).hexdigest(), len(data), job_path, redacted)
        if redacted:
            self.notes.append(f"{path}: dati riservati o percorsi locali sostituiti da segnaposti.")
        return True

    def add_file(self, path: str, source: Path, role: str, job_path: Optional[str] = None) -> bool:
        try:
            size = source.stat().st_size
        except OSError:
            self.notes.append(f"File non incluso: {path} (non più leggibile).")
            return False
        if size > repro.MAX_ENTRY_BYTES:
            self.notes.append(f"File non incluso: {path} (oltre {repro.MAX_ENTRY_BYTES // 1024 ** 2} MB).")
            return False
        if size <= REDACT_MAX_BYTES:
            return self.add_bytes(path, source.read_bytes(), role, job_path)
        if not self._accept(path):
            return False
        # file grande (template mmCIF enorme, mappa del potenziale): a blocchi, senza oscuramento ma con il controllo
        # dei segreti (sovrapposizione fra i blocchi per non perderne uno a cavallo)
        digest, written, tail = hashlib.sha256(), 0, b""
        info = self._info(path, size)
        with open(source, "rb") as src, self.zf.open(info, "w", force_zip64=True) as out:
            while True:
                chunk = src.read(CHUNK)
                if not chunk:
                    break
                if self.redactor.leaks(tail + chunk):
                    raise AppError(f"Il file {path} contiene dati riservati: pacchetto non creato.",
                                   code="package_secret")
                tail = chunk[-256:]
                digest.update(chunk)
                out.write(chunk)
                written += len(chunk)
        self._entry(path, role, digest.hexdigest(), written, job_path, False)
        return True


# ------------------------------------------------------------------------------------------ servizio
class ReproducibilityService:
    def __init__(self, jobs: JobManager, compute: ComputeService, library: TemplateLibrary, paths: Paths,
                 settings_provider: Callable, licenses: "LicenseService") -> None:
        self.jobs = jobs
        self.compute = compute
        self.library = library
        self.paths = paths
        self.settings_provider = settings_provider
        self.licenses = licenses

    def register(self) -> None:
        # priorità 1 come la modellazione (i tipi non elencati in core/jobs.PRIORITY): minuti, non secondi
        self.jobs.register(KIND, self.handle, priority=1)
        incoming = self.jobs.jobs_dir / INCOMING
        if incoming.exists():  # copie rimaste da un avvio interrotto
            remove_tree(incoming)

    # ------------------------------------------------------------------------------ segreti
    def redactor(self, extra_secrets: tuple[str, ...] = ()) -> repro.Redactor:
        """Segreti (chiavi di licenza, email, chiave d'accesso) e percorsi locali da non esportare."""
        settings = self.settings_provider()
        access = os.environ.get("BIOLOOM_ACCESS_KEY", "")
        try:
            stored = (self.paths.data / "access_key").read_text(encoding="utf-8").strip()
        except OSError:
            stored = ""
        secrets = [*self.licenses.secrets(), default_env().get("BIOLOOM_MODELLER_KEY", ""), settings.contact_email,
                   access, stored, *extra_secrets]
        places = [(self.paths.data, "<cartella dati>"), (self.paths.scratch, "<scratch>"),
                  (self.paths.local_cache, "<cache locale>"), (ROOT_DIR, "<installazione>"), (Path.home(), "~")]
        return repro.Redactor(secrets, [(str(p), label) for p, label in places])

    # ------------------------------------------------------------------------------ esportazione
    def export(self, job_id: str, dest: Path, extra_secrets: tuple[str, ...] = ()) -> str:
        """Scrive in `dest` il pacchetto del job; restituisce il nome del file da proporre al download."""
        job = self.jobs.get(job_id)
        kind = job["kind"]
        if kind == KIND:
            raise AppError("Una riesecuzione non ha un pacchetto proprio: esporta il job originale. Rapporto e file della "
                           "riesecuzione sono nell'archivio ZIP del job.", code="package_unsupported")
        if kind not in repro.EXPORTABLE:
            raise AppError(f"Il pacchetto di riproducibilità non è disponibile per i job di tipo {kind}: è previsto per "
                           "modellazione, loop, allineamenti, qualità, analisi strutturale, rifinitura, tasche, "
                           "elettrostatica, ricerca di template e QMEAN. Per una pipeline usa l'archivio ZIP dei "
                           "risultati.", code="package_unsupported")
        if job["status"] != "completed":
            raise Conflict("Il pacchetto si crea da un job completato: contiene i risultati con cui confrontare la "
                           "riesecuzione.")
        params = job.get("params") if isinstance(job.get("params"), dict) else {}
        result = job.get("result") if isinstance(job.get("result"), dict) else {}
        settings = self.settings_provider()
        redactor = self.redactor(extra_secrets)
        facts: dict = {"remote": [], "databases": [], "missing": [], "changed": []}
        folder = self.jobs.workdir(job_id)
        try:
            with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED, allowZip64=True) as zf:
                b = _Builder(zf, redactor)
                b.add_bytes("inputs/params.json",
                            json.dumps(params, indent=2, ensure_ascii=False).encode("utf-8"), "params")
                self._add_inputs(b, job, params, result, facts)
                taken = {e["job_path"] for e in b.entries if e.get("job_path")}
                for info in self.jobs.list_files(job_id):
                    rel = info["path"]
                    if rel in ("params.json", "job.log") or rel in taken or repro.job_path_problem(rel):
                        continue
                    script = PurePosixPath(rel).suffix == ".py"
                    b.add_file(("scripts/" if script else "outputs/") + rel, safe_join(folder, *rel.split("/")),
                               "script" if script else "output", job_path=rel)
                log = folder / "job.log"
                if log.is_file():
                    b.add_file("log/job.log", log, "log")
                # testi del manifest oscurati come i file; l'elenco dei file (percorsi e checksum) si aggiunge dopo, e
                # il controllo finale vale per tutto
                manifest = self._manifest(job, params, result, settings, facts)
                manifest["notes"] = list(b.notes) + manifest["notes"]
                manifest = redactor.value(manifest)
                b.add_bytes(repro.README, repro.readme_text(manifest).encode("utf-8"), "documentation")
                manifest["files"] = b.entries
                manifest = repro.seal(manifest)
                text = json.dumps(manifest, indent=2, ensure_ascii=False).encode("utf-8")
                if redactor.leaks(text):  # impossibile dopo l'oscuramento: ultima difesa
                    raise AppError("Il manifest conterrebbe dati riservati: pacchetto non creato.", code="package_secret")
                zf.writestr(b._info(repro.MANIFEST, len(text)), text)
        except FileNotFoundError:
            raise Conflict("Un file del job è sparito durante l'esportazione (job eliminato?): riprova.")
        return f"bioloom_riproducibile_{kind}_{job_id}.zip"

    def _manifest(self, job: dict, params: dict, result: dict, settings, facts: dict) -> dict:
        kind = job["kind"]
        relevant = {k: getattr(settings, k, None) for k in RELEVANT_SETTINGS}
        if kind == "qmean":
            for key in QMEAN_SETTINGS:
                value = getattr(settings, key, None)
                relevant[key] = PurePosixPath(str(value).replace("\\", "/")).name if key == "qmean_uniclust" else value
        reason = self._refusal_reason(kind, params, result, facts)
        own_notes = []
        if result.get("cached"):
            own_notes.append(f"Il risultato del job veniva dalla cache dei calcoli (calcolo del "
                             f"{result.get('cached_at') or '?'}): la riesecuzione lo ricalcola.")
        own_notes += facts["changed"]
        return {
            "format": repro.FORMAT, "format_version": repro.FORMAT_VERSION, "created_at": now_iso(),
            "bioloom": versions.bioloom(),
            "environment": {**versions.environment(self._tool_keys(kind, params), settings.modeller_python),
                            "detected": "all'esportazione, su questa installazione (i job non registrano l'ambiente)"},
            "job": {"id": job["id"], "kind": kind, "title": job.get("title") or "", "status": job["status"],
                    "created_at": job.get("created_at"), "started_at": job.get("started_at"),
                    "finished_at": job.get("finished_at"), "duration_seconds": _duration(job)},
            "params": params,
            "seeds": self._seeds(kind, params, result),
            "settings": {"values": relevant, "meaning": {k: RELEVANT_SETTINGS[k] for k in RELEVANT_SETTINGS},
                         "note": "impostazioni al momento dell'esportazione che influiscono sul risultato o sui file"},
            "cache": {"use_cache": params.get("use_cache"), "result_from_cache": bool(result.get("cached")),
                      "cached_at": result.get("cached_at")},
            "remote": facts["remote"], "databases": facts["databases"],
            "rerun": {"supported": reason is None, "reason": reason},
            "notes": own_notes,
            "files": [],
        }

    @staticmethod
    def _seeds(kind: str, params: dict, result: dict) -> dict:
        if kind == "model":
            options = result.get("options") if isinstance(result.get("options"), dict) else {}
            raw = params.get("options") if isinstance(params.get("options"), dict) else {}
            seed = options.get("seed", raw.get("seed", modeller.ModelOptions().seed))
            return {"modeller_rand_seed": seed, "note": "Environ(rand_seed=…) di MODELLER: stesso seme, stessa "
                                                        "versione e stesso processore danno gli stessi modelli."}
        if kind == "loop":
            return {"modeller_rand_seed": modeller.ModelOptions().seed,
                    "note": "seme predefinito di MODELLER (Environ() senza rand_seed)."}
        if kind == "refine":
            threads = ((result.get("openmm") or {}).get("threads") if isinstance(result.get("openmm"), dict) else None)
            return {"openmm_seed": refinement.MD_SEED, "openmm_threads": threads,
                    "note": "seme (domain/refinement.MD_SEED) del posizionamento iniziale di idrogeni e atomi aggiunti "
                            "(PDBFixer, Modeller di OpenMM) e, con la dinamica, dell'integratore di Langevin e delle "
                            "velocità. Con più thread la piattaforma CPU di OpenMM somma le forze in un ordine che "
                            "cambia fra le esecuzioni: differenze piccole oltre la tolleranza sono attese; con un "
                            "thread (OPENMM_CPU_THREADS=1) le esecuzioni sono identiche."}
        return {"note": "calcolo deterministico: nessun seme casuale."}

    @staticmethod
    def _tool_keys(kind: str, params: dict) -> list[str]:
        """Strumenti che il calcolo usa (le loro versioni vanno nel manifest)."""
        if kind == "model":
            extended = bool(params.get("extended_quality")) and params.get("quality") != "none"
            return ["modeller", "dssp", *(["procheck", "prosa"] if extended else [])]
        if kind == "loop":
            return ["modeller", "dssp"]
        if kind == "align2d":
            return ["modeller"]
        if kind == "quality":
            return [*(["modeller"] if params.get("dope", True) else []), "dssp",
                    *(["procheck"] if params.get("procheck", True) else []),
                    *(["prosa"] if params.get("prosa", True) else [])]
        if kind == "refine":
            return ["openmm", "pdbfixer", "modeller", "dssp"]
        if kind == "pockets":
            return ["fpocket"]
        if kind == "electrostatics":
            return ["pdb2pqr", "apbs"]
        if kind == "align":
            tool = params.get("tool") or "auto"
            if params.get("mode") == "structural":
                return [tool] if tool in ("tmalign", "usalign") else ["usalign", "tmalign"]
            if params.get("mode") == "multiple":
                return [tool] if tool in ("clustalo", "mafft", "muscle") else ["clustalo", "mafft", "muscle"]
            return []
        if kind == "analysis":
            msa = params.get("msa") if isinstance(params.get("msa"), dict) else {}
            if msa.get("text"):
                tool = msa.get("tool") or "auto"
                return [tool] if tool in ("clustalo", "mafft", "muscle") else ["clustalo", "mafft", "muscle"]
            return []
        if kind == "search":
            return {"local": ["blastp"], "mmseqs": ["mmseqs"]}.get(params.get("engine") or "", [])
        return []

    @staticmethod
    def _required_tools(kind: str, params: dict) -> list[str]:
        """Strumenti senza i quali la riesecuzione fallirebbe (MODELLER, PROCHECK e ProSA a parte: licenze)."""
        if kind == "refine":
            return ["openmm", "pdbfixer"]
        if kind == "pockets":
            return ["fpocket"]
        if kind == "electrostatics":
            return ["pdb2pqr", "apbs"]
        msa = params.get("msa") if isinstance(params.get("msa"), dict) else {}
        tool = params.get("tool") if kind == "align" else msa.get("tool") if kind == "analysis" else None
        return [tool] if tool in ("tmalign", "usalign", "clustalo", "mafft", "muscle") else []

    def _refusal_reason(self, kind: str, params: dict, result: dict, facts: dict) -> Optional[str]:
        """Perché il calcolo non si può rieseguire localmente (None se si può)."""
        when = ", ".join(f"{r['service']} ({r.get('date') or 'data non nota'})" for r in facts["remote"])
        if kind == "search":
            return (f"La ricerca di template interroga servizi remoti o database locali che cambiano ({when}): non è "
                    "riproducibile localmente. Il pacchetto ne documenta parametri, input e risultati.")
        if kind == "qmean":
            if result.get("source") == "swissmodel":
                return (f"QMEAN è stato calcolato da SWISS-MODEL, un servizio remoto ({when}): non è riproducibile "
                        "localmente.")
            database = f" ({result['uniclust']})" if result.get("uniclust") else ""
            return (f"Il QMEAN locale usa il servizio QMEAN e il database UniClust30/UniRef30{database}: la "
                    "riesecuzione da pacchetto non è supportata per questo calcolo. Rilancia QMEAN dalla pagina "
                    "Qualità.")
        if kind not in repro.RERUNNABLE:
            return "Tipo di calcolo non supportato dalla riesecuzione."
        if facts["missing"]:
            return ("Mancano input non più disponibili quando il pacchetto è stato creato: "
                    + "; ".join(facts["missing"]) + ".")
        if facts.get("changed_input"):
            return "; ".join(facts["changed_input"])
        return None

    # ------------------------------------------------------------------------------ input
    def _add_inputs(self, b: _Builder, job: dict, params: dict, result: dict, facts: dict) -> None:
        kind = job["kind"]
        folder = self.jobs.workdir(job["id"])
        records: list[tuple[str, str]] = []
        if kind in ("model", "align2d"):
            raw = list(params.get("templates") or [])
            if params.get("complex"):
                try:
                    target = complexes.parse_target(params["complex"])
                    raw = complex_templates(target, raw)
                    records += [(c.name or f"catena_{c.template_chain}", c.sequence) for c in target["chains"]]
                except AppError:
                    pass
            # copie preparate del job (una per template e catene: lo stesso template può comparire con catene diverse)
            prepared: list[str] = []
            first: dict[str, str] = {}
            for t in result.get("templates") or []:
                rel = str(t.get("file") or "") if isinstance(t, dict) else ""
                if not rel or repro.job_path_problem(rel) or rel in prepared:
                    continue
                prepared.append(rel)
                try:
                    first.setdefault(normalize_id(str(t.get("id") or "")), rel)
                except AppError:
                    continue
            ids = []
            for t in raw:
                try:
                    ids.append(normalize_id(str(t.get("id") or "")))
                except AppError:
                    continue
            for tid in dict.fromkeys(ids):
                self._add_template(b, tid, job, facts,
                                   prepared=safe_join(folder, *first[tid].split("/")) if tid in first else None)
            for rel in prepared:
                path = safe_join(folder, *rel.split("/"))
                if path.is_file():
                    b.add_file(f"inputs/templates/{rel}", path, "template_prepared", job_path=rel)
            if (folder / "alignment.ali").is_file():
                b.add_file("inputs/alignment.ali", folder / "alignment.ali", "alignment", job_path="alignment.ali")
            target = params.get("target") if isinstance(params.get("target"), dict) else {}
            if target.get("sequence"):
                records.append((str(target.get("name") or "TARGET"), str(target["sequence"])))
        elif kind in ("quality", "qmean", "analysis", "refine", "loop", "pockets", "electrostatics"):
            source = params.get("source") if isinstance(params.get("source"), dict) else {}
            self._add_source(b, source, result.get("source_sha256"), job, facts)
            msa = params.get("msa") if isinstance(params.get("msa"), dict) else {}
            if kind == "analysis" and msa.get("job_id"):
                self._add_msa_job(b, str(msa["job_id"]), facts)
            if kind == "qmean" and result.get("source") == "swissmodel":
                facts["remote"].append({"service": "SWISS-MODEL QMEAN", "what": "punteggi QMEAN della struttura",
                                        "date": job.get("finished_at"), "url": result.get("url")})
        elif kind == "align":
            if params.get("mode") == "structural":
                for s in params.get("structures") or []:
                    if isinstance(s, dict):
                        self._add_source(b, s, None, job, facts)
            else:
                records += [(str(r.get("name") or f"seq{i + 1}"), str(r.get("sequence") or ""))
                            for i, r in enumerate(params.get("records") or []) if isinstance(r, dict)]
        elif kind == "search":
            query = params.get("query") if isinstance(params.get("query"), dict) else {}
            if query.get("sequence"):
                records.append((str(query.get("name") or "query"), str(query["sequence"])))
            engine = params.get("engine") or "rcsb"
            if engine in SEARCH_SERVICE:
                facts["remote"].append({"service": SEARCH_SERVICE[engine], "what": "ricerca dei template",
                                        "date": job.get("finished_at")})
            else:
                facts["remote"].append({"service": "RCSB PDB (metadati delle strutture trovate)",
                                        "what": "metodo, risoluzione e titolo dei template", "date": job.get("finished_at")})
                db = (search.local_database(self.compute.databases_dir) if engine == "local"
                      else search.mmseqs_database(self.compute.databases_dir))
                if db is not None:
                    facts["databases"].append({"name": "pdbaa" if engine == "local" else "pdbaa (MMseqs2)",
                                               "stamp": search.database_stamp(db),
                                               "note": "timbro (file, dimensione, data) al momento dell'esportazione"})
        if records:
            ids = [sequences.sanitize_code(name, default=f"seq{i + 1}") for i, (name, _s) in enumerate(records)]
            text = sequences.format_fasta(ids, [s for _n, s in records])
            b.add_bytes("inputs/sequences.fasta", text.encode("utf-8"), "sequences")

    def _add_template(self, b: _Builder, tid: str, job: dict, facts: dict, prepared: Optional[Path] = None) -> None:
        """File originale del template nella libreria e metadati; se non c'è più, la copia preparata del job."""
        try:
            path = self.library.path(tid)
        except AppError:
            if prepared is not None and prepared.is_file():
                b.add_file(f"inputs/library/{tid}.pdb", prepared, "template")
                b.notes.append(f"Il template {tid} non è più nella libreria: la riesecuzione parte dalla copia "
                               "preparata del job (catene, ligandi e ritaglio già applicati).")
            else:
                facts["missing"].append(f"template {tid} (non più nella libreria)")
            return
        b.add_file(f"inputs/library/{path.name}", path, "template")
        try:
            meta = {k: v for k, v in self.library.describe(tid).items() if k not in ("format", "size")}
        except (AppError, OSError):
            meta = {}
        b.add_bytes(f"inputs/library/{tid}.meta.json", json.dumps(meta, indent=2, ensure_ascii=False).encode("utf-8"),
                    "template_meta")
        source = str(meta.get("source") or "")
        if source == "RCSB PDB" or source == "alphafold":
            facts["remote"].append({"service": "RCSB PDB" if source == "RCSB PDB" else "AlphaFold DB (EBI)",
                                    "what": f"template {tid} scaricato nella libreria", "date": meta.get("added_at")})
        # dopo la fine del job (durante, è il job stesso che l'ha scaricato); le date dei job sono al secondo
        finished = _utc(job.get("finished_at"))
        try:
            modified = datetime.fromtimestamp(path.stat().st_mtime, timezone.utc)
        except OSError:
            modified = None
        if finished and modified and modified > finished + timedelta(seconds=1):
            facts["changed"].append(f"Il file del template {tid} in libreria è stato modificato dopo il job: se è "
                                    "cambiato, la riesecuzione darà risultati diversi (le copie preparate del job sono "
                                    "nel pacchetto per il confronto).")

    def _add_source(self, b: _Builder, source: dict, fingerprint: Optional[str], job: dict, facts: dict) -> None:
        """Struttura d'ingresso di un'analisi: template della libreria, file caricato o file di un altro job."""
        kind = source.get("type")
        try:
            if kind == "template":
                tid = normalize_id(str(source.get("id") or ""))
                self._add_template(b, tid, job, facts)
                path = self.library.path(tid) if self.library.exists(tid) else None
            elif kind == "job":
                job_id = validate_identifier(str(source.get("job_id") or "").strip(), "job_id")
                rel = str(source.get("file") or "").replace("\\", "/")
                if repro.job_path_problem(rel):
                    raise AppError(f"percorso {rel!r} non valido")
                path = self.jobs.file_path(job_id, rel)
                b.add_file(f"inputs/jobs/{job_id}/{rel}", path, "structure")
            elif kind == "upload":
                name = str(source.get("file") or "")
                if repro.job_path_problem(name) or "/" in name:
                    raise AppError(f"nome {name!r} non valido")
                path = safe_join(self.paths.uploads, name)
                if not path.is_file():
                    raise NotFound("file caricato eliminato dalla pulizia automatica")
                b.add_file(f"inputs/uploads/{name}", path, "structure")
            else:
                raise AppError("sorgente sconosciuta")
        except AppError as exc:
            facts["missing"].append(f"struttura {source.get('id') or source.get('file') or '?'} ({exc.message})")
            return
        if path is not None and fingerprint:
            try:
                same = _file_sha256(path) == fingerprint
            except OSError:
                same = True
            if not same:
                facts.setdefault("changed_input", []).append(
                    f"Il file analizzato ({source.get('id') or source.get('file')}) è cambiato dopo il calcolo (SHA-256 "
                    "diverso da quello registrato nel risultato): il pacchetto non contiene più l'input del job e la "
                    "riesecuzione non sarebbe confrontabile.")

    def _add_msa_job(self, b: _Builder, job_id: str, facts: dict) -> None:
        try:
            validate_identifier(job_id, "job_id")
            msa_job = self.jobs.get(job_id)
            data = msa_job.get("result") if isinstance(msa_job.get("result"), dict) else {}
        except AppError:
            facts["missing"].append(f"allineamento del job {job_id} (job eliminato)")
            return
        payload = {k: data.get(k) for k in ("mode", "method", "ids", "aligned")}
        b.add_bytes(f"inputs/jobs/{job_id}/alignment.json",
                    json.dumps(payload, indent=1, ensure_ascii=False).encode("utf-8"), "msa")

    # ------------------------------------------------------------------------------ pacchetti caricati
    def stage(self, source: BinaryIO) -> str:
        """Salva nello scratch un pacchetto caricato (a blocchi, con il limite di dimensione); restituisce l'id."""
        package_id = uuid.uuid4().hex
        dest = self.staged_path(package_id, must_exist=False)
        _copy_limited(source, dest)
        return package_id

    def staged_path(self, package_id: str, must_exist: bool = True) -> Path:
        token = str(package_id or "").strip().lower()
        if len(token) != 32 or any(c not in "0123456789abcdef" for c in token):
            raise AppError("Identificativo del pacchetto non valido.", code="package_not_found")
        self.paths.scratch_tmp.mkdir(parents=True, exist_ok=True)
        path = self.paths.scratch_tmp / f"{STAGE_PREFIX}{token}.zip"
        if must_exist and not path.is_file():
            raise NotFound("Pacchetto non trovato (forse eliminato dalla pulizia automatica dopo 24 ore): caricalo di "
                           "nuovo.", code="package_not_found")
        return path

    def discard(self, package_id: str) -> None:
        self.staged_path(package_id, must_exist=False).unlink(missing_ok=True)

    def current(self, kind: str, params: dict) -> dict:
        """Versioni di questa installazione per il confronto con il pacchetto."""
        settings = self.settings_provider()
        return {"bioloom": versions.bioloom(),
                "environment": versions.environment(self._tool_keys(kind, params), settings.modeller_python)}

    def refusal(self, pkg: Package) -> Optional[tuple[str, str]]:
        """(codice, motivo) se il pacchetto non si può rieseguire, None se si può."""
        kind = pkg.kind
        rerun = pkg.manifest.get("rerun") if isinstance(pkg.manifest.get("rerun"), dict) else {}
        if kind not in repro.RERUNNABLE:
            remote = kind in ("search", "qmean")
            reason = str(rerun.get("reason") or "") or f"I job di tipo {kind} non si possono rieseguire da pacchetto."
            return ("rerun_not_reproducible" if remote else "rerun_unsupported"), reason
        if rerun.get("supported") is False:
            return "rerun_unsupported", str(rerun.get("reason") or "Il pacchetto non contiene tutti gli input.")
        return None

    def rerun_params(self, pkg: Package) -> dict:
        """Parametri del job originale validati con il modello Pydantic della rotta di avvio e con i suoi controlli
        immediati; cache dei calcoli ignorata, nessun progetto (lo sceglie chi rilancia)."""
        from pydantic import ValidationError

        kind = pkg.kind
        model = _start_models()[kind]
        try:
            params = model.model_validate(pkg.manifest["params"]).model_dump()
        except ValidationError as exc:
            details = "; ".join(f"{'.'.join(str(p) for p in e.get('loc', []))}: {e.get('msg')}" for e in exc.errors()[:5])
            raise AppError(f"Parametri del pacchetto non validi per un job {kind}: {details}.",
                           code="package_params_invalid")
        params["project_id"] = None
        if "use_cache" in params:
            params["use_cache"] = False
        if isinstance(params.get("msa"), dict):
            params["msa"]["use_cache"] = False
        try:
            if kind in ("model", "align2d") and params.get("complex"):
                complex_templates(complexes.parse_target(params["complex"]), params.get("templates") or [])
            elif kind == "model" and not params.get("alignment") and not (params.get("target") or {}).get("sequence"):
                raise AppError("Indica una sequenza bersaglio oppure un allineamento PIR.")
            elif kind == "align2d" and not (params.get("target") or {}).get("sequence"):
                raise AppError("Indica la sequenza bersaglio.")
            if kind == "model":
                modeller.parse_options(params.get("options") or {})
        except AppError as exc:
            raise AppError(f"Parametri del pacchetto non validi: {exc.message}", code="package_params_invalid")
        return params

    def summary(self, pkg: Package, package_id: Optional[str] = None) -> dict:
        """Anteprima del pacchetto per l'interfaccia e il comando: manifest, file, esiti (errori, avvisi, informazioni)."""
        m = pkg.manifest
        kind = pkg.kind
        checks: list[dict] = []
        refusal = self.refusal(pkg)
        params = m.get("params") or {}
        if refusal:
            checks.append({"level": "error", "code": refusal[0], "message": refusal[1]})
        else:
            try:
                params = self.rerun_params(pkg)
            except AppError as exc:
                checks.append({"level": "error", "code": exc.code, "message": exc.message})
        current = self.current(kind, params)
        packaged = {"bioloom": m.get("bioloom") or {}, "environment": m.get("environment") or {}}
        checks += [{"level": "warning", **w} for w in repro.environment_warnings(packaged, current)]
        for r in m.get("remote") or []:
            if isinstance(r, dict):
                checks.append({"level": "info", "code": "rerun_remote_input",
                               "message": f"Input remoto: {r.get('service')}, {r.get('what')} ("
                                          f"{r.get('date') or 'data non nota'})."})
        for note in m.get("notes") or []:
            checks.append({"level": "info", "code": "package_note", "message": str(note)})
        files = m.get("files") or []
        by_role: dict[str, dict] = {}
        for f in files:
            slot = by_role.setdefault(f.get("role"), {"count": 0, "bytes": 0})
            slot["count"] += 1
            slot["bytes"] += int(f.get("size") or 0)
        return {
            "package_id": package_id, "format_version": m.get("format_version"), "created_at": m.get("created_at"),
            "bioloom": m.get("bioloom"), "environment": m.get("environment"), "job": m.get("job"),
            "kind_label": repro.KIND_LABEL.get(kind, kind), "params": m.get("params"), "seeds": m.get("seeds"),
            "settings": m.get("settings"), "cache": m.get("cache"), "remote": m.get("remote") or [],
            "databases": m.get("databases") or [], "notes": m.get("notes") or [],
            "rerun": {"supported": refusal is None, "reason": refusal[1] if refusal else None},
            "files": {"count": len(files), "bytes": sum(int(f.get("size") or 0) for f in files), "by_role": by_role,
                      "list": [{"path": f["path"], "role": f["role"], "size": f["size"]} for f in files[:500]]},
            "current": current, "checks": checks, "manifest_sha256": m.get("manifest_sha256"),
        }

    def inspect(self, package_id: str) -> dict:
        return self.summary(open_package(self.staged_path(package_id)), package_id)

    def preflight_facts(self, package_id: str) -> dict:
        """Fatti per i controlli preventivi (services/preflight.py): esiti del pacchetto, strumenti e licenze necessari,
        durata originale, spazio."""
        path = self.staged_path(package_id)
        pkg = open_package(path)
        info = self.summary(pkg, package_id)
        kind = pkg.kind
        params = pkg.manifest.get("params") or {}
        keys = self._tool_keys(kind, params)
        needs = {"modeller": "modeller" in keys, "modeller_required": kind in ("model", "loop", "align2d"),
                 "procheck": "procheck" in keys, "prosa": "prosa" in keys,
                 "tools": self._required_tools(kind, params)}
        roles = info["files"]["by_role"]
        inputs = sum(v["bytes"] for r, v in roles.items() if r in repro.INPUT_ROLES)
        outputs = sum(v["bytes"] for r, v in roles.items() if r in ("output", "script"))
        return {"kind": kind, "label": info["kind_label"], "checks": info["checks"], "needs": needs,
                "duration": (pkg.manifest.get("job") or {}).get("duration_seconds"),
                "data_bytes": path.stat().st_size + outputs, "scratch_bytes": inputs + 2 * outputs}

    def submit(self, path: Path, *, project_id: Optional[str] = None, move: bool = False) -> dict:
        """Crea il job di riesecuzione da un pacchetto (copiato, o spostato con `move`, nella cartella del job)."""
        pkg = open_package(path)
        refusal = self.refusal(pkg)
        if refusal:
            raise AppError(refusal[1], code=refusal[0])
        self.rerun_params(pkg)
        m = pkg.manifest
        original = m["job"]
        incoming = self.jobs.jobs_dir / INCOMING
        incoming.mkdir(parents=True, exist_ok=True)
        holding = incoming / f"{uuid.uuid4().hex}.zip"
        # la copia si fa prima di creare il job: dopo resta solo uno spostamento nella stessa cartella dati
        if move:
            shutil.move(str(path), str(holding))
        else:
            shutil.copyfile(path, holding)
        params = {
            "package": {"sha256": _file_sha256(holding), "size": holding.stat().st_size,
                        "created_at": m.get("created_at"), "manifest_sha256": m.get("manifest_sha256")},
            "original": {"job_id": original.get("id"), "kind": pkg.kind, "title": original.get("title"),
                         "finished_at": original.get("finished_at"), "bioloom": (m.get("bioloom") or {}).get("version")},
            "project_id": project_id,
        }
        title = f"Riesecuzione · {original.get('title') or repro.KIND_LABEL.get(pkg.kind, pkg.kind)}"

        def prepare(_job_id: str, workdir: Path) -> None:
            os.replace(holding, workdir / PACKAGE_NAME)

        try:
            return self.jobs.submit(KIND, params, project_id=project_id, title=title, prepare=prepare)
        finally:
            holding.unlink(missing_ok=True)

    # ------------------------------------------------------------------------------ job di riesecuzione
    def handle(self, ctx: JobContext, p: dict) -> dict:
        ctx.progress(0.01, "Verifica del pacchetto")
        pkg = open_package(self.jobs.workdir(ctx.job_id) / PACKAGE_NAME)
        refusal = self.refusal(pkg)
        if refusal:
            raise AppError(refusal[1], code=refusal[0])
        kind = pkg.kind
        params = self.rerun_params(pkg)
        m = pkg.manifest
        original = m["job"]
        current = self.current(kind, params)
        warnings = [w["message"] for w in repro.environment_warnings(
            {"bioloom": m.get("bioloom") or {}, "environment": m.get("environment") or {}}, current)]
        for w in warnings:
            ctx.log(f"Avviso: {w}")
        ctx.log(f"Pacchetto verificato: job {kind} {original.get('id')} del {original.get('finished_at')}, "
                f"{len(pkg.files)} file, checksum SHA-256 corrispondenti")
        root = ctx.tmp_dir / "pacchetto"
        remove_tree(root)
        ctx.progress(0.03, "Estrazione degli input")
        extract(pkg, root, repro.INPUT_ROLES)
        sandbox = PackagedCompute(self.compute, root)
        folder = ctx.workdir / RERUN_DIR
        folder.mkdir(parents=True, exist_ok=True)

        def progress(fraction: Optional[float], stage: Optional[str]) -> None:
            ctx.progress(None if fraction is None else 0.05 + 0.85 * fraction, stage)

        sub = ctx.sub(RERUN_DIR, folder, on_progress=progress)
        ctx.log(f"Riesecuzione con gli stessi parametri: cache dei calcoli ignorata, template e strutture dal "
                f"pacchetto, nessun download; file in {RERUN_DIR}/")
        started = time.perf_counter()
        inner = self._run(kind, sandbox, sub, params)
        elapsed = time.perf_counter() - started
        atomic_write_json(folder / "result.json", inner)
        ctx.check()
        ctx.progress(0.92, "Confronto con i risultati del pacchetto")
        report = self._compare(pkg, folder, inner, warnings)
        report["duration_seconds"] = round(elapsed, 1)
        atomic_write_json(ctx.workdir / REPORT_JSON, report)
        atomic_write_text(ctx.workdir / REPORT_TEXT, repro.report_text(report))
        counts = report["counts"]
        ctx.log(f"Esito del confronto: {report['verdict']} ({counts['identical']} file identici, "
                f"{counts['equivalent']} equivalenti, {counts['different']} diversi, {counts['missing']} mancanti); "
                f"rapporto in {REPORT_TEXT}")
        return {key: report[key] for key in ("verdict", "summary", "original", "counts", "structures", "metrics",
                                             "warnings", "tolerance", "bioloom", "image", "created_at",
                                             "duration_seconds")} | {
            "kind": kind, "report_file": REPORT_TEXT, "report_json": REPORT_JSON, "folder": RERUN_DIR,
            "files": [f for f in report["files"] if f["status"] != "identical"][:200]}

    def _run(self, kind: str, sandbox: PackagedCompute, ctx: JobContext, params: dict) -> dict:
        """Il calcolo originale con gli handler di sempre, sugli input del pacchetto."""
        if kind == "model":
            return sandbox.handle_model(ctx, params)
        if kind == "loop":
            return sandbox.handle_loop(ctx, params)
        if kind == "align2d":
            return sandbox.handle_align2d(ctx, params)
        if kind == "quality":
            sandbox.resolve_structure(params.get("source") or {})
            return sandbox.handle_quality(ctx, params)
        if kind == "align":
            return sandbox.handle_align(ctx, params)
        if kind == "analysis":
            service = PackagedAnalysis(self.jobs, sandbox)
            service.validate(params)
            return service.handle(ctx, params)
        if kind == "refine":
            refine = RefineService(self.jobs, sandbox)
            refine.validate(params)
            return refine.handle(ctx, params)
        if kind in ("pockets", "electrostatics"):
            tools_service = StructureToolsService(self.jobs, sandbox)
            tools_service.validate(kind, params)
            return getattr(tools_service, f"handle_{kind}")(ctx, params)
        raise AppError(f"I job di tipo {kind} non si possono rieseguire da pacchetto.", code="rerun_unsupported")

    # ------------------------------------------------------------------------------ confronto
    def _compare(self, pkg: Package, folder: Path, inner: dict, warnings: list[str]) -> dict:
        files: list[dict] = []
        compared: set[str] = set()
        with zipfile.ZipFile(pkg.path) as zf:
            for name, entry in sorted(pkg.files.items()):
                rel = entry.get("job_path")
                if entry["role"] not in repro.COMPARED_ROLES or not rel:
                    continue
                compared.add(rel)
                target = safe_join(folder, *rel.split("/"))
                principal = repro.principal_file(rel, entry["role"], is_intermediate)
                if entry["size"] > COMPARE_MAX_BYTES or (target.is_file() and target.stat().st_size > COMPARE_MAX_BYTES):
                    same = target.is_file() and _file_sha256(target) == entry["sha256"]
                    res = {"path": rel, "status": "identical" if same else ("different" if target.is_file()
                                                                            else "missing"),
                           "detail": "file grande: confrontato solo il checksum"}
                else:
                    original = b"".join(_stream(zf, pkg.members[name]))
                    res = repro.compare_file(rel, original, target.read_bytes() if target.is_file() else None)
                res.update(role=entry["role"], principal=principal)
                files.append(res)
        for path in sorted(folder.rglob("*")):
            relative = path.relative_to(folder)
            rel = relative.as_posix()
            if not path.is_file() or rel in compared or any(part.startswith(".") for part in relative.parts) \
                    or is_intermediate(path.name):
                continue
            files.append({"path": rel, "status": "extra", "role": "output", "principal": False})
        verdict = repro.verdict(files)
        statuses = ("identical", "equivalent", "different", "missing", "extra")
        counts = {s: sum(1 for f in files if f["status"] == s) for s in statuses}
        m = pkg.manifest
        job = m["job"]
        metrics = self._metrics(pkg.kind, pkg.result(), inner)
        structures = [{"path": f["path"], "coordinates": f["coordinates"]} for f in files if f.get("coordinates")]
        beyond = sum(1 for section in metrics for row in section["rows"] if not row.get("within"))
        summary = (f"{repro.VERDICT_TEXT[verdict][0].upper()}{repro.VERDICT_TEXT[verdict][1:]}: "
                   f"{counts['identical']} file identici su {len(files) - counts['extra']}"
                   + (f", {counts['equivalent']} equivalenti" if counts["equivalent"] else "")
                   + (f", {counts['different']} diversi" if counts["different"] else "")
                   + (f", {counts['missing']} mancanti" if counts["missing"] else "")
                   + (f", {counts['extra']} in più" if counts["extra"] else "")
                   + (f"; {beyond} metriche principali oltre la tolleranza" if beyond else "") + ".")
        return {
            "verdict": verdict, "summary": summary, "counts": counts, "files": files, "structures": structures,
            "metrics": metrics, "warnings": warnings,
            "tolerance": {"coordinates_angstrom": repro.COORD_TOLERANCE, "score_abs": repro.SCORE_ABS,
                          "score_rel": repro.SCORE_REL, "ignored": sorted(repro.IGNORED_KEYS)},
            "original": {"job_id": job.get("id"), "kind": pkg.kind, "title": job.get("title"),
                         "finished_at": job.get("finished_at"), "bioloom": (m.get("bioloom") or {}).get("version"),
                         "image": (m.get("bioloom") or {}).get("image"), "package_created_at": m.get("created_at")},
            "bioloom": __version__, "image": versions.bioloom().get("image"), "created_at": now_iso(),
        }

    @staticmethod
    def _metrics(kind: str, a: dict, b: dict) -> list[dict]:
        """Metriche principali del job originale e della riesecuzione, per sezione."""
        sections: list[dict] = []

        def quality_metrics(report: dict) -> dict:
            try:
                return quality.summary_metrics(report)
            except (KeyError, TypeError, AttributeError):
                return {}

        if kind in ("model", "loop"):
            rerun_models = {m.get("file"): m for m in b.get("models") or [] if isinstance(m, dict)}
            sections.append({"title": "Modellazione", "rows": repro.metric_rows(
                {"best_model": a.get("best_model"), "models": len(a.get("models") or [])},
                {"best_model": b.get("best_model"), "models": len(b.get("models") or [])},
                (("best_model", "Modello migliore (DOPE minimo)"), ("models", "Modelli prodotti")))})
            for model in a.get("models") or []:
                if not isinstance(model, dict):
                    continue
                other = rerun_models.get(model.get("file")) or {}
                rows = repro.metric_rows(model, other, MODEL_METRICS)
                rows += repro.metric_rows(model.get("quality") or {}, other.get("quality") or {},
                                          [m for m in QUALITY_METRICS if m[0] in ("grade_score", "rama_favored",
                                                                                  "clashscore")])
                sections.append({"title": f"Modello {model.get('file')}" + (" (migliore)" if model.get("best") else ""),
                                 "rows": rows})
            if kind == "model":
                same = a.get("alignment") == b.get("alignment")
                sections[0]["rows"].append({"key": "alignment", "label": "Allineamento usato da MODELLER",
                                            "original": "PIR del pacchetto", "rerun": "identico" if same else "diverso",
                                            "delta": None, "within": same})
        elif kind == "align2d":
            same = a.get("pir") == b.get("pir")
            sections.append({"title": "Allineamento", "rows": [
                {"key": "pir", "label": "Allineamento PIR", "original": "PIR del pacchetto",
                 "rerun": "identico" if same else "diverso", "delta": None, "within": same}]})
        elif kind == "quality":
            sections.append({"title": "Qualità", "rows": repro.metric_rows(quality_metrics(a), quality_metrics(b),
                                                                            QUALITY_METRICS)})
        elif kind == "align":
            if a.get("mode") == "structural" or b.get("mode") == "structural":
                sections.append({"title": "Allineamento strutturale", "rows": repro.metric_rows(a, b, STRUCTURAL_METRICS)})
            else:
                same = a.get("aligned") == b.get("aligned")
                sections.append({"title": "Allineamento", "rows": [
                    {"key": "method", "label": "Programma", "original": a.get("method"), "rerun": b.get("method"),
                     "delta": None, "within": a.get("method") == b.get("method")},
                    {"key": "aligned", "label": "Righe allineate", "original": f"{len(a.get('aligned') or [])} righe",
                     "rerun": "identiche" if same else "diverse", "delta": None, "within": same}]})
        elif kind == "analysis":
            ia, ib = (a.get("interactions") or {}).get("summary") or {}, (b.get("interactions") or {}).get("summary") or {}
            rows = repro.metric_rows(ia, ib, INTERACTION_METRICS)
            fa, fb = (a.get("interfaces") or {}).get("pairs") or [], (b.get("interfaces") or {}).get("pairs") or []
            rows += repro.metric_rows({"pairs": len(fa), "area": round(sum(p.get("area") or 0 for p in fa), 1)},
                                      {"pairs": len(fb), "area": round(sum(p.get("area") or 0 for p in fb), 1)},
                                      (("pairs", "Interfacce fra catene"), ("area", "Area sepolta totale (Å²)")))
            sections.append({"title": "Analisi strutturale", "rows": rows})
        elif kind == "refine":
            ea, eb = a.get("energy") or {}, b.get("energy") or {}
            rows = repro.metric_rows(ea, eb, (("before", "Energia prima (kJ/mol)"), ("after", "Energia dopo (kJ/mol)")))
            rows += repro.metric_rows(a.get("rmsd") or {}, b.get("rmsd") or {},
                                      (("rmsd", "RMSD catena principale (Å)"), ("rmsd_no_fit", "RMSD senza "
                                                                                              "sovrapposizione (Å)")))
            after_a = (a.get("quality") or {}).get("after") or {}
            after_b = (b.get("quality") or {}).get("after") or {}
            rows += repro.metric_rows(after_a, after_b, QUALITY_METRICS)
            sections.append({"title": "Rifinitura", "rows": rows})
        elif kind == "pockets":
            sa, sb = a.get("summary") or {}, b.get("summary") or {}
            rows = repro.metric_rows(sa, sb, (("count", "Tasche"), ("druggable", "Tasche druggabili")))
            rows += repro.metric_rows(sa.get("best") or {}, sb.get("best") or {},
                                      (("druggability", "Druggability della migliore"), ("volume", "Volume (Å³)")))
            sections.append({"title": "Tasche", "rows": rows})
        elif kind == "electrostatics":
            rows = repro.metric_rows(a, b, (("net_charge", "Carica netta"),))
            rows += repro.metric_rows(a.get("summary") or {}, b.get("summary") or {},
                                      (("min", "Potenziale minimo (kT/e)"), ("max", "Potenziale massimo (kT/e)"),
                                       ("mean", "Potenziale medio (kT/e)")))
            sections.append({"title": "Elettrostatica", "rows": rows})
        return [s for s in sections if s["rows"]]


def _copy_limited(source: BinaryIO, dest: Path, limit: int = repro.MAX_PACKAGE_BYTES) -> int:
    """Copia a blocchi un pacchetto caricato, con il limite di dimensione (413 oltre il limite, file rimosso)."""
    written = 0
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        with open(dest, "wb") as out:
            while True:
                chunk = source.read(CHUNK)
                if not chunk:
                    break
                written += len(chunk)
                if written > limit:
                    raise AppError(f"Il pacchetto supera {limit // 1024 ** 2} MB.", code="package_too_large",
                                   status=413)
                out.write(chunk)
    except BaseException:
        dest.unlink(missing_ok=True)
        raise
    return written
