"""Riproducibilità (F9.4): regole pure del pacchetto di un job e del confronto della riesecuzione.

Il pacchetto è un archivio ZIP:

    manifest.json   versioni di BioLoom e degli strumenti, parametri completi, semi casuali, impostazioni che
                    influiscono sul risultato, input remoti con la data, elenco dei file con SHA-256 e dimensione,
                    impronta del manifest stesso (`manifest_sha256`)
    LEGGIMI.txt     istruzioni per rieseguire
    inputs/         parametri, sequenze, template della libreria (originali e copie preparate), allineamento PIR,
                    strutture analizzate
    outputs/        i file del job (risultato, modelli, rapporti), con lo stesso percorso relativo
    scripts/        script MODELLER eseguiti: solo documentazione, la riesecuzione li rigenera dai parametri
    log/job.log     log del calcolo, senza segreti né percorsi locali

Qui stanno le regole senza I/O: limiti e controlli dell'archivio caricato (input non fidato: percorsi, link
simbolici, nomi duplicati, bombe di decompressione), validazione e impronta del manifest, oscuramento di segreti e
percorsi, confronto dei file e delle metriche con le tolleranze dichiarate, avvisi sulle versioni e testi (LEGGIMI,
rapporto della riesecuzione). La lettura e la scrittura dei file sono in services/reproducibility.py.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import stat
import unicodedata
import zipfile
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import Any, Callable, Iterable, Optional

import numpy as np

from ..core.errors import AppError

FORMAT = "bioloom-reproducibility"
FORMAT_VERSION = 1
MANIFEST = "manifest.json"
README = "LEGGIMI.txt"
TOP_FILES = frozenset({MANIFEST, README})
TOP_DIRS = frozenset({"inputs", "outputs", "scripts", "log"})

# Ruoli dei file nel manifest. Quelli d'ingresso servono alla riesecuzione e si estraggono; gli altri si verificano
# soltanto (checksum) e si confrontano con i file della riesecuzione.
INPUT_ROLES = frozenset({"params", "sequences", "template", "template_meta", "template_prepared", "alignment",
                         "structure", "msa"})
ROLES = INPUT_ROLES | {"output", "script", "log", "documentation"}
# Ruoli confrontati con la riesecuzione (stesso `job_path` nella cartella del nuovo calcolo)
COMPARED_ROLES = frozenset({"output", "template_prepared", "alignment", "script"})

# Tipi di job: tutti quelli con un pacchetto; fra questi, quelli che si possono rieseguire in locale. La ricerca dei
# template e QMEAN dipendono da servizi remoti o dal servizio QMEAN con UniClust30: il pacchetto ne documenta gli
# input (con la data delle richieste remote), ma la riesecuzione li dichiara non riproducibili localmente.
KIND_LABEL = {
    "model": "modellazione", "loop": "raffinamento di loop", "align2d": "allineamento MODELLER",
    "quality": "analisi di qualità", "align": "allineamento", "analysis": "analisi strutturale",
    "refine": "rifinitura energetica", "pockets": "ricerca delle tasche (fpocket)",
    "electrostatics": "elettrostatica (PDB2PQR + APBS)", "search": "ricerca di template", "qmean": "QMEAN",
}
EXPORTABLE = frozenset(KIND_LABEL)
RERUNNABLE = frozenset({"model", "loop", "align2d", "quality", "align", "analysis", "refine", "pockets",
                        "electrostatics"})

# ------------------------------------------------------------------------------------------ limiti dell'archivio
# Il pacchetto caricato è un input non fidato. Limiti:
# - dimensione compressa (upload) e decompressa: i pacchetti reali sono di qualche MB (analisi di qualità di 1UBQ con
#   PROCHECK, misurata: 19 file, 2,8 MB decompressi, 0,4 MB compressi); 256 MB compressi e 1 GB decompressi lasciano
#   posto a template mmCIF grandi senza permettere di riempire il disco;
# - rapporto di compressione per file: deflate non supera circa 1032:1 (zlib, «Technical details»: «the maximum
#   compression factor is 1032:1»), ed è il rapporto che usano le bombe di decompressione. I file di BioLoom si
#   comprimono molto meno (misurati nei pacchetti di un'analisi di qualità e di una modellazione di 1UBQ: PDB 4:1,
#   PostScript di PROCHECK, rapporti JSON e log 5–10:1, il profilo DOPE di MODELLER, colonne di numeri allineate, 35:1):
#   100:1 è circa 3 volte il file più comprimibile misurato e 10 volte sotto il massimo di deflate. Il controllo vale
#   oltre 1 MB decompresso: un file piccolo molto comprimibile (un PIR con molti '-') non è un pericolo; all'esportazione
#   un file grande molto comprimibile si scrive senza compressione, così il pacchetto resta importabile;
# - numero di file, lunghezza dei nomi e profondità delle cartelle: un job ha qualche decina di file.
MAX_PACKAGE_BYTES = 256 * 1024 ** 2
MAX_TOTAL_BYTES = 1024 ** 3
MAX_ENTRY_BYTES = 512 * 1024 ** 2
MAX_RATIO = 100
RATIO_MIN_BYTES = 1024 ** 2
MAX_FILES = 5000
MAX_NAME_LENGTH = 240
MAX_DEPTH = 12
# il manifest contiene i parametri completi (un allineamento incollato per l'analisi può arrivare a 10 MB)
MAX_MANIFEST_BYTES = 32 * 1024 ** 2

# ------------------------------------------------------------------------------------------ tolleranze
# Coordinate: il formato PDB scrive x, y, z con 3 decimali (record ATOM, colonne 31–54, Real(8.3): wwPDB, «Atomic
# Coordinate Entry Format Description» 3.3). Due calcoli uguali a meno dell'arrotondamento dell'ultima cifra
# differiscono al più di 0,001 Å per coordinata, cioè √3 · 0,001 ≈ 0,0017 Å per atomo: la tolleranza è 0,002 Å sullo
# scarto massimo di un atomo (e quindi anche sull'RMSD). Il confronto è senza sovrapposizione: un modello riprodotto
# deve avere le stesse coordinate assolute (MODELLER costruisce nel sistema di riferimento del template), e una
# sovrapposizione nasconderebbe una traslazione o una rotazione.
COORD_TOLERANCE = 0.002
# Numeri dei risultati (DOPE, GA341, molpdf, energie, percentuali): |Δ| ≤ max(SCORE_ABS, SCORE_REL · |valore|). In
# doppia precisione (IEEE 754: 53 bit di mantissa, circa 16 cifre significative) le stesse somme in un ordine diverso
# differiscono di poche unità dell'ultima cifra; 1e-6 relativo assorbe queste differenze con ampio margine senza
# nascondere un calcolo diverso (il DOPE di un modello di 1UBQ, circa −8000, ha così una tolleranza di 0,008).
# SCORE_ABS = 0,001 vale per i valori vicini allo zero (z-score, frazioni).
SCORE_ABS = 1e-3
SCORE_REL = 1e-6
# Campi dei risultati che non dipendono dal calcolo: provenienza dalla cache dei calcoli, durata dei passi e numero
# di thread (OpenMM). Non entrano nel confronto.
IGNORED_KEYS = frozenset({"cached", "cached_at", "timings", "threads"})
# Testo che cambia a ogni esecuzione senza dipendere dal calcolo: data e ora che MODELLER scrive nella riga EXPDTA dei
# modelli («EXPDTA THEORETICAL MODEL, MODELLER 10.8 2026/10/01 08:19:38»), riprese dal rapporto di qualità nel metodo
# della struttura. Misurato rieseguendo lo stesso job (modellazione con la qualità estesa, analisi di qualità con
# PROCHECK): è l'unica differenza fra i file dei due job, log a parte.
_MODELLER_STAMP = re.compile(r"(MODELLER \S+) \d{4}/\d{2}/\d{2} \d{2}:\d{2}:\d{2}")
MAX_EXAMPLES = 5

# Base della stima del tempo nei controlli preventivi della riesecuzione
RERUN_BASIS = ("Durata del calcolo originale registrata nel pacchetto (dall'avvio alla fine, senza l'attesa in coda): "
               "su un altro computer, con un altro carico o altre versioni può cambiare; intervallo da metà al doppio "
               "come le altre stime.")

VERDICT_TEXT = {
    "identico": "tutti i file confrontati sono uguali (a parte la data di MODELLER nella riga EXPDTA e i campi che non "
                "dipendono dal calcolo: durata dei passi, thread, provenienza dalla cache)",
    "equivalente": "nessuna differenza oltre le tolleranze: valori uguali entro la tolleranza, formattazione o file "
                   "accessori diversi",
    "diverso": "ci sono differenze oltre le tolleranze o file mancanti",
}


def _error(code: str, message: str, status: int = 400) -> AppError:
    return AppError(message, code=code, status=status)


# ------------------------------------------------------------------------------------------ nomi nell'archivio
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
_DRIVE = re.compile(r"^[A-Za-z]:")
_RESERVED = re.compile(r"^(con|prn|aux|nul|com[0-9]|lpt[0-9])(\..*)?$", re.IGNORECASE)
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


def _path_problem(name: str) -> Optional[str]:
    """Perché un percorso relativo non è accettabile (None se va bene): assoluto, con '..', vuoto, nascosto."""
    if not name:
        return "nome vuoto"
    if len(name) > MAX_NAME_LENGTH:
        return f"nome più lungo di {MAX_NAME_LENGTH} caratteri"
    if _CONTROL.search(name):
        return "caratteri di controllo nel nome"
    if "\\" in name:
        return "barra rovesciata (percorso di Windows)"
    if name.startswith("/") or _DRIVE.match(name):
        return "percorso assoluto"
    parts = name.split("/")
    if any(p in ("", ".", "..") for p in parts):
        return "componente vuota, '.' o '..' (uscirebbe dalla cartella di estrazione)"
    if len(parts) > MAX_DEPTH:
        return f"più di {MAX_DEPTH} livelli di cartelle"
    if any(p.startswith(".") or p.endswith((".", " ")) or _RESERVED.match(p) for p in parts):
        return "nome nascosto o riservato"
    return None


def member_problem(name: str) -> Optional[str]:
    """Perché un nome di file dell'archivio non è accettabile, None se va bene. Una cartella termina con '/'."""
    clean = name[:-1] if name.endswith("/") else name
    problem = _path_problem(clean)
    if problem:
        return problem
    parts = clean.split("/")
    if len(parts) == 1 and not name.endswith("/"):
        return None if clean in TOP_FILES else "file fuori dalle cartelle del pacchetto"
    if parts[0] not in TOP_DIRS:
        return f"cartella {parts[0]!r} non prevista (ammesse: {', '.join(sorted(TOP_DIRS))})"
    return None


def job_path_problem(path: str) -> Optional[str]:
    """Percorso relativo di un file nella cartella del job (`job_path` del manifest)."""
    return _path_problem(path)


def check_archive(infos: Iterable[zipfile.ZipInfo]) -> dict[str, zipfile.ZipInfo]:
    """File dell'archivio (nome → ZipInfo), dopo i controlli di sicurezza; AppError al primo problema.

    Rifiuta: percorsi assoluti, con '..' o fuori dalle cartelle del pacchetto; link simbolici e file speciali (dal
    modo Unix negli attributi esterni); nomi duplicati, anche solo per maiuscole o forma Unicode (all'estrazione su
    Windows o macOS uno coprirebbe l'altro); file cifrati e metodi di compressione diversi da stored e deflate; troppi
    file, file o totale troppo grandi, rapporto di compressione da bomba di decompressione.
    """
    files: dict[str, zipfile.ZipInfo] = {}
    folded: dict[str, str] = {}
    total = 0
    count = 0
    for info in infos:
        count += 1
        if count > MAX_FILES * 2:  # cartelle comprese
            raise _error("package_too_many_files", f"Il pacchetto contiene più di {MAX_FILES} file: non è un pacchetto "
                                                   "di BioLoom.")
        name = info.filename
        problem = member_problem(name)
        if problem:
            raise _error("package_unsafe_path", f"Percorso non consentito nel pacchetto: {name[:120]!r} ({problem}).")
        mode = (info.external_attr >> 16) & 0o170000
        if mode == stat.S_IFLNK:
            raise _error("package_symlink", f"Il pacchetto contiene un collegamento simbolico ({name}): non è ammesso.")
        if info.is_dir():
            if mode not in (0, stat.S_IFDIR):
                raise _error("package_special_file", f"Voce non valida nel pacchetto: {name}.")
            continue
        if mode not in (0, stat.S_IFREG):
            raise _error("package_special_file", f"Il pacchetto contiene un file speciale ({name}): non è ammesso.")
        if info.flag_bits & 0x1:
            raise _error("package_encrypted", f"Il file {name} del pacchetto è cifrato: i pacchetti di BioLoom non lo "
                                              "sono.")
        if info.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED):
            raise _error("package_compression", f"Il file {name} usa un metodo di compressione non ammesso (solo "
                                                "stored e deflate).")
        key = unicodedata.normalize("NFC", name).casefold()
        if name in files or key in folded:
            raise _error("package_duplicate", f"Il pacchetto contiene due volte il file {name}"
                                              + (f" (anche come {folded[key]})" if key in folded and
                                                 folded[key] != name else "") + ".")
        if info.file_size > MAX_ENTRY_BYTES:
            raise _error("package_too_large", f"Il file {name} del pacchetto supera {MAX_ENTRY_BYTES // 1024 ** 2} MB "
                                              "decompresso.", status=413)
        total += info.file_size
        if total > MAX_TOTAL_BYTES:
            raise _error("package_too_large", f"Il pacchetto supera {MAX_TOTAL_BYTES // 1024 ** 3} GB decompresso.",
                         status=413)
        if info.file_size > RATIO_MIN_BYTES and info.file_size > MAX_RATIO * max(1, info.compress_size):
            raise _error("package_bomb", f"Il file {name} ha un rapporto di compressione di "
                                         f"{info.file_size // max(1, info.compress_size)}:1 (massimo {MAX_RATIO}:1): "
                                         "il pacchetto sembra una bomba di decompressione ed è stato rifiutato.")
        files[name] = info
        folded[key] = name
    if len(files) > MAX_FILES:
        raise _error("package_too_many_files", f"Il pacchetto contiene più di {MAX_FILES} file.")
    if MANIFEST not in files:
        raise _error("package_invalid", "Il file non è un pacchetto di riproducibilità di BioLoom: manca manifest.json.")
    if files[MANIFEST].file_size > MAX_MANIFEST_BYTES:
        raise _error("package_invalid", "manifest.json è troppo grande.")
    return files


# ------------------------------------------------------------------------------------------ manifest
def canonical(obj: Any) -> bytes:
    """JSON canonico (chiavi ordinate, nessuno spazio, UTF-8): base dell'impronta del manifest."""
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def manifest_digest(manifest: dict) -> str:
    """SHA-256 del manifest senza il campo `manifest_sha256`."""
    return sha256(canonical({k: v for k, v in manifest.items() if k != "manifest_sha256"}))


def seal(manifest: dict) -> dict:
    """Manifest con la sua impronta: una modifica successiva (parametri, checksum, tipo di job) si riconosce."""
    return {**manifest, "manifest_sha256": manifest_digest(manifest)}


def _need(cond: bool, message: str) -> None:
    if not cond:
        raise _error("package_manifest_invalid", f"Manifest del pacchetto non valido: {message}.")


def validate_manifest(data: Any) -> dict:
    """Controlla forma, versione del formato e impronta del manifest; restituisce il manifest.

    Errori: `package_manifest_invalid` (campi mancanti o di tipo sbagliato), `package_format` (formato di un'altra
    versione), `package_manifest_altered` (impronta che non corrisponde: manifest modificato dopo l'esportazione).
    """
    _need(isinstance(data, dict), "non è un oggetto JSON")
    if data.get("format") != FORMAT:
        raise _error("package_invalid", "Il file non è un pacchetto di riproducibilità di BioLoom (formato "
                                        f"{str(data.get('format'))[:60]!r}).")
    version = data.get("format_version")
    _need(isinstance(version, int) and not isinstance(version, bool), "format_version mancante")
    if version > FORMAT_VERSION:
        raise _error("package_format", f"Il pacchetto usa il formato {version}, più recente di quello che questa "
                                       f"versione di BioLoom sa leggere ({FORMAT_VERSION}): aggiorna BioLoom.")
    if version < 1:
        raise _error("package_format", f"Versione del formato del pacchetto non valida: {version}.")
    digest = data.get("manifest_sha256")
    _need(isinstance(digest, str) and bool(_SHA256.match(digest)), "manifest_sha256 mancante")
    try:
        actual = manifest_digest(data)
    except (TypeError, ValueError):
        raise _error("package_manifest_invalid", "Manifest del pacchetto non valido: contiene valori non "
                                                 "rappresentabili in JSON.")
    if actual != digest:
        raise _error("package_manifest_altered", "Il manifest del pacchetto è stato modificato dopo l'esportazione "
                                                 "(impronta SHA-256 non corrispondente): parametri, checksum o versioni "
                                                 "non sono più quelli registrati da BioLoom. Usa il pacchetto originale.")
    job = data.get("job")
    _need(isinstance(job, dict), "manca la sezione job")
    _need(isinstance(job.get("kind"), str) and bool(job.get("kind")), "manca il tipo del job")
    _need(isinstance(job.get("id"), str), "manca l'identificativo del job")
    _need(isinstance(data.get("params"), dict), "mancano i parametri del job")
    _need(isinstance(data.get("bioloom"), dict) and isinstance(data["bioloom"].get("version"), str),
          "manca la versione di BioLoom")
    _need(isinstance(data.get("environment"), dict), "manca la sezione environment")
    files = data.get("files")
    _need(isinstance(files, list) and bool(files), "manca l'elenco dei file")
    seen: set[str] = set()
    for entry in files:
        _need(isinstance(entry, dict), "voce dei file non valida")
        path = entry.get("path")
        _need(isinstance(path, str), "file senza percorso")
        problem = member_problem(path)
        _need(problem is None and path != MANIFEST, f"percorso {str(path)[:120]!r} non ammesso ({problem})")
        _need(path not in seen, f"file {path} elencato due volte")
        seen.add(path)
        _need(entry.get("role") in ROLES, f"ruolo non valido per {path}")
        _need(isinstance(entry.get("sha256"), str) and bool(_SHA256.match(entry["sha256"])),
              f"checksum non valido per {path}")
        size = entry.get("size")
        _need(isinstance(size, int) and not isinstance(size, bool) and size >= 0, f"dimensione non valida per {path}")
        job_path = entry.get("job_path")
        if job_path is not None:
            _need(isinstance(job_path, str) and job_path_problem(job_path) is None,
                  f"percorso nel job non valido per {path}")
    return data


def declared_files(manifest: dict) -> dict[str, dict]:
    return {entry["path"]: entry for entry in manifest["files"]}


# ------------------------------------------------------------------------------------------ segreti e percorsi
REDACTED = "[rimosso]"
MIN_SECRET = 6  # più corto non si cerca: si rischierebbe di cambiare testo qualunque (nessuna chiave è così corta)


class Redactor:
    """Toglie da testi e valori i segreti (chiave MODELLER, codici di licenza, email, chiave d'accesso) e i percorsi
    locali (cartella dati, scratch, home, installazione), sostituiti con segnaposti.

    I segreti si cercano senza distinguere le maiuscole. Un percorso si sostituisce solo se inizia dopo uno spazio,
    una virgoletta o un inizio di riga e continua con un separatore o la fine: «https://data.rcsb.org» non contiene il
    percorso «/data».
    """

    def __init__(self, secrets: Iterable[str] = (), paths: Iterable[tuple[str, str]] = ()) -> None:
        clean = sorted({str(s).strip() for s in secrets if s and len(str(s).strip()) >= MIN_SECRET}, key=len,
                       reverse=True)
        self._secrets = clean
        self._secret_re = re.compile("|".join(re.escape(s) for s in clean), re.IGNORECASE) if clean else None
        patterns = []
        seen: set[str] = set()
        for path, placeholder in sorted(paths, key=lambda p: len(p[0]), reverse=True):
            path = str(path).rstrip("/\\")
            if len(path) < 3:
                continue
            for variant in {path, path.replace("\\", "/"), path.replace("/", "\\")}:
                if variant in seen:
                    continue
                seen.add(variant)
                patterns.append((re.compile(r"(?<![\w.\-/\\:])" + re.escape(variant) + r"(?=$|[/\\\s\"'`),;:\]>])",
                                            re.MULTILINE), placeholder))
        self._paths = patterns

    def text(self, value: str) -> str:
        if self._secret_re is not None:
            value = self._secret_re.sub(REDACTED, value)
        for pattern, placeholder in self._paths:
            value = pattern.sub(placeholder, value)
        return value

    def value(self, obj: Any) -> Any:
        """Copia di un valore JSON con i testi (anche le chiavi) oscurati."""
        if isinstance(obj, str):
            return self.text(obj)
        if isinstance(obj, dict):
            return {self.text(str(k)): self.value(v) for k, v in obj.items()}
        if isinstance(obj, list):
            return [self.value(v) for v in obj]
        return obj

    def data(self, raw: bytes) -> tuple[bytes, bool]:
        """Byte di un file oscurati e se sono cambiati; un file non testuale resta com'è (vedi `leaks`)."""
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            return raw, False
        clean = self.text(text)
        return (clean.encode("utf-8"), True) if clean != text else (raw, False)

    def leaks(self, raw: bytes) -> int:
        """Quanti segreti compaiono nei byte (senza dire quali): controllo finale di ogni file del pacchetto."""
        lowered = raw.lower()
        return sum(1 for s in self._secrets if s.lower().encode("utf-8") in lowered)


# ------------------------------------------------------------------------------------------ confronto
def normalize_volatile(text: str) -> str:
    """Testo senza le parti che cambiano a ogni esecuzione (data di MODELLER nella riga EXPDTA)."""
    return _MODELLER_STAMP.sub(r"\1 <data>", text)


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def within(a: float, b: float) -> bool:
    """Due numeri uguali entro la tolleranza dei punteggi (NaN uguale a NaN, infiniti uguali solo se identici)."""
    if a == b:
        return True
    if isinstance(a, float) and isinstance(b, float) and math.isnan(a) and math.isnan(b):
        return True
    if not (math.isfinite(a) and math.isfinite(b)):
        return False
    return abs(a - b) <= max(SCORE_ABS, SCORE_REL * max(abs(a), abs(b)))


def _short(value: Any) -> Any:
    if _is_number(value) or value is None or isinstance(value, bool):
        return value
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)[:200]
    return text if len(text) <= 80 else text[:77] + "…"


@dataclass
class Diff:
    """Differenze fra due contenuti: numeri confrontati e oltre la tolleranza (con la differenza massima), testi
    diversi, differenze di struttura (campi, elementi o righe mancanti o in più), primi esempi."""

    numbers: int = 0
    beyond: int = 0
    max_delta: float = 0.0
    max_path: str = ""
    text: int = 0
    shape: int = 0
    examples: list = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.beyond == 0 and self.text == 0 and self.shape == 0

    def note(self, path: str, original: Any, rerun: Any) -> None:
        if len(self.examples) < MAX_EXAMPLES:
            self.examples.append({"where": path or "(radice)", "original": _short(original), "rerun": _short(rerun)})

    def number(self, path: str, a: float, b: float) -> None:
        self.numbers += 1
        if a == b:
            return
        if math.isfinite(a) and math.isfinite(b):
            delta = abs(a - b)
            if delta > self.max_delta:
                self.max_delta, self.max_path = delta, path
        if not within(a, b):
            self.beyond += 1
            self.note(path, a, b)

    def to_dict(self) -> dict:
        return {"numbers": self.numbers, "beyond_tolerance": self.beyond,
                "max_delta": float(f"{self.max_delta:.6g}") if self.max_delta else 0.0,
                "max_delta_at": self.max_path or None, "text": self.text, "structure": self.shape,
                "examples": list(self.examples)}


def compare_values(a: Any, b: Any, diff: Optional[Diff] = None, path: str = "") -> Diff:
    """Confronto di due valori JSON: numeri con la tolleranza, testi e struttura uguali. Ignora `IGNORED_KEYS`."""
    diff = diff if diff is not None else Diff()
    if isinstance(a, dict) and isinstance(b, dict):
        for key, value in a.items():
            if key in IGNORED_KEYS:
                continue
            where = f"{path}.{key}" if path else str(key)
            if key not in b:
                diff.shape += 1
                diff.note(where, value, "(assente)")
            else:
                compare_values(value, b[key], diff, where)
        for key, value in b.items():
            if key not in a and key not in IGNORED_KEYS:
                diff.shape += 1
                diff.note(f"{path}.{key}" if path else str(key), "(assente)", value)
    elif isinstance(a, list) and isinstance(b, list):
        if len(a) != len(b):
            diff.shape += 1
            diff.note(path, f"{len(a)} elementi", f"{len(b)} elementi")
        for i, (x, y) in enumerate(zip(a, b)):
            compare_values(x, y, diff, f"{path}[{i}]")
    elif _is_number(a) and _is_number(b):
        diff.number(path, float(a), float(b))
    elif a != b:
        diff.text += 1
        diff.note(path, a, b)
    return diff


_NUMBER = re.compile(r"^[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?$")


def compare_text(a: str, b: str, diff: Optional[Diff] = None) -> Diff:
    """Confronto riga per riga di due testi: parole uguali, numeri uguali entro la tolleranza."""
    diff = diff if diff is not None else Diff()
    la, lb = a.splitlines(), b.splitlines()
    if len(la) != len(lb):
        diff.shape += 1
        diff.note("numero di righe", len(la), len(lb))
    for i, (x, y) in enumerate(zip(la, lb), 1):
        if x == y:
            continue
        tx, ty = x.split(), y.split()
        if len(tx) != len(ty):
            diff.text += 1
            diff.note(f"riga {i}", x, y)
            continue
        for ta, tb in zip(tx, ty):
            if ta == tb:
                continue
            if _NUMBER.match(ta) and _NUMBER.match(tb):
                diff.number(f"riga {i}", float(ta), float(tb))
            else:
                diff.text += 1
                diff.note(f"riga {i}", x, y)
                break
    return diff


def pdb_atoms(text: str) -> dict[tuple, tuple[float, float, float]]:
    """Coordinate degli atomi del primo modello di un PDB: (catena, residuo con inserzione, nome del residuo, atomo,
    posizione alternativa) → (x, y, z), dalle colonne fisse del formato."""
    atoms: dict[tuple, tuple[float, float, float]] = {}
    for line in text.splitlines():
        record = line[:6]
        if record == "ENDMDL":
            break
        if record not in ("ATOM  ", "HETATM"):
            continue
        key = (line[21:22], line[22:27].strip(), line[17:20].strip(), line[12:16].strip(), line[16:17].strip())
        try:
            xyz = (float(line[30:38]), float(line[38:46]), float(line[46:54]))
        except ValueError:
            continue
        atoms.setdefault(key, xyz)
    return atoms


def compare_coordinates(a: str, b: str) -> dict:
    """Atomi in comune, RMSD senza sovrapposizione e scarto massimo (con l'atomo) fra due PDB; `within` se gli atomi
    sono gli stessi e lo scarto massimo è entro `COORD_TOLERANCE`."""
    xa_map, xb_map = pdb_atoms(a), pdb_atoms(b)
    common = [k for k in xa_map if k in xb_map]
    out = {"atoms": len(xa_map), "rerun_atoms": len(xb_map), "common": len(common),
           "only_original": len(xa_map) - len(common), "only_rerun": len(xb_map) - len(common),
           "rmsd": None, "max_deviation": None, "max_atom": None, "within": False}
    if not common:
        return out
    xa = np.array([xa_map[k] for k in common])
    xb = np.array([xb_map[k] for k in common])
    dist = np.sqrt(((xa - xb) ** 2).sum(axis=1))
    worst = int(dist.argmax())
    chain, resid, resname, atom, _alt = common[worst]
    out.update(rmsd=round(float(np.sqrt((dist ** 2).mean())), 4), max_deviation=round(float(dist[worst]), 4),
               max_atom=f"{atom} {resname} {chain.strip() or '-'}{resid}",
               within=out["only_original"] == 0 and out["only_rerun"] == 0 and float(dist[worst]) <= COORD_TOLERANCE)
    return out


def _without_ignored(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: _without_ignored(v) for k, v in value.items() if k not in IGNORED_KEYS}
    if isinstance(value, list):
        return [_without_ignored(v) for v in value]
    return value


def _non_coordinate(text: str) -> str:
    return "\n".join(ln for ln in text.splitlines() if not ln.startswith(("ATOM  ", "HETATM", "ANISOU")))


def compare_file(path: str, original: bytes, rerun: Optional[bytes]) -> dict:
    """Esito del confronto di un file del pacchetto con quello della riesecuzione.

    - `identical`: byte uguali, oppure uguali a parte la data di MODELLER (`normalize_volatile`);
    - `equivalent`: valori uguali entro le tolleranze (PDB: stessi atomi, scarto entro `COORD_TOLERANCE`; JSON: stessa
      struttura e stessi testi, numeri entro la tolleranza; altri testi: parola per parola);
    - `different`: con le differenze quantificate (`coordinates`, `diff`); `missing`: la riesecuzione non l'ha prodotto.
    """
    out: dict = {"path": path}
    if rerun is None:
        return {**out, "status": "missing"}
    if original == rerun:
        return {**out, "status": "identical"}
    try:
        ta, tb = original.decode("utf-8"), rerun.decode("utf-8")
    except UnicodeDecodeError:
        return {**out, "status": "different", "detail": "file binario diverso"}
    na, nb = normalize_volatile(ta), normalize_volatile(tb)
    if na == nb:
        return {**out, "status": "identical", "detail": "uguale a parte la data di MODELLER"}
    suffix = PurePosixPath(path).suffix.lower()
    if suffix in (".pdb", ".ent"):
        coords = compare_coordinates(na, nb)
        other = compare_text(_non_coordinate(na), _non_coordinate(nb))
        status = "equivalent" if coords["within"] and other.ok else "different"
        return {**out, "status": status, "coordinates": coords, "diff": other.to_dict()}
    diff: Diff
    if suffix == ".json":
        try:
            ja, jb = json.loads(na), json.loads(nb)
        except ValueError:
            diff = compare_text(na, nb)
        else:
            if _without_ignored(ja) == _without_ignored(jb):
                return {**out, "status": "identical", "detail": "uguale a parte durata dei passi, thread e cache"}
            diff = compare_values(ja, jb)
    else:
        diff = compare_text(na, nb)
    return {**out, "status": "equivalent" if diff.ok else "different", "diff": diff.to_dict()}


def verdict(files: list[dict]) -> str:
    """Esito complessivo: `diverso` se un file principale è diverso o mancante, `identico` se ogni file confrontato è
    identico (e la riesecuzione non ne ha prodotti altri), altrimenti `equivalente`."""
    if any(f["status"] in ("different", "missing") and f.get("principal", True) for f in files):
        return "diverso"
    if all(f["status"] == "identical" for f in files):
        return "identico"
    return "equivalente"


def metric_rows(original: dict, rerun: dict, spec: Iterable[tuple[str, str]]) -> list[dict]:
    """Righe di confronto delle metriche principali: valore originale, della riesecuzione, differenza, entro la
    tolleranza. Le metriche assenti in entrambi si saltano."""
    rows = []
    for key, label in spec:
        a, b = (original or {}).get(key), (rerun or {}).get(key)
        if a is None and b is None:
            continue
        row = {"key": key, "label": label, "original": a, "rerun": b, "delta": None}
        if _is_number(a) and _is_number(b):
            row["delta"] = float(f"{float(b) - float(a):.6g}")
            row["within"] = within(float(a), float(b))
        else:
            row["within"] = a == b
        rows.append(row)
    return rows


# ------------------------------------------------------------------------------------------ versioni
def environment_warnings(packaged: dict, current: dict) -> list[dict]:
    """Avvisi (mai errori) quando la riesecuzione usa versioni diverse da quelle del pacchetto: BioLoom, immagine
    Docker, strumenti, Python e librerie di calcolo, architettura. Ogni avviso ha `code` e `message`."""
    out: list[dict] = []
    pb = (packaged.get("bioloom") or {})
    cb = (current.get("bioloom") or {})
    if pb.get("version") and pb.get("version") != cb.get("version"):
        out.append({"code": "rerun_version_bioloom",
                    "message": f"Il pacchetto è stato creato con BioLoom {pb.get('version')}, la riesecuzione usa "
                               f"BioLoom {cb.get('version')}: script, analisi e avvisi possono cambiare e i risultati "
                               "differire."})
    if pb.get("image") and cb.get("image") and pb.get("image") != cb.get("image"):
        out.append({"code": "rerun_version_image",
                    "message": f"Immagine Docker diversa: {pb.get('image')} nel pacchetto, {cb.get('image')} qui."})
    pe, ce = packaged.get("environment") or {}, current.get("environment") or {}
    now_tools = ce.get("tools") or {}
    for key, tool in sorted((pe.get("tools") or {}).items()):
        if not isinstance(tool, dict) or not tool.get("available"):
            continue
        label = tool.get("label") or key
        here = now_tools.get(key) or {}
        if not here.get("available"):
            out.append({"code": "rerun_tool_missing",
                        "message": f"{label} era usato dal calcolo ({tool.get('version') or 'versione non nota'}) ma "
                                   "non è installato qui: la riesecuzione fallirà o salterà quel passo."})
        elif tool.get("version") and here.get("version") and tool["version"] != here["version"]:
            out.append({"code": "rerun_version_tool",
                        "message": f"{label}: versione {tool['version']} nel pacchetto, {here['version']} qui."})
    libraries = {**({"python": pe.get("python")} if pe.get("python") else {}), **(pe.get("libraries") or {})}
    now_libs = {**({"python": ce.get("python")} if ce.get("python") else {}), **(ce.get("libraries") or {})}
    for name, version in sorted(libraries.items()):
        if version and now_libs.get(name) and now_libs[name] != version:
            label = "Python" if name == "python" else name
            out.append({"code": "rerun_version_library",
                        "message": f"{label}: versione {version} nel pacchetto, {now_libs[name]} qui."})
    if pe.get("machine") and ce.get("machine") and pe["machine"] != ce["machine"]:
        out.append({"code": "rerun_platform",
                    "message": f"Architettura diversa ({pe['machine']} nel pacchetto, {ce['machine']} qui): i calcoli "
                               "in virgola mobile possono differire nelle ultime cifre."})
    return out


# ------------------------------------------------------------------------------------------ testi
def _fmt(value: Any, digits: int = 4) -> str:
    if value is None:
        return "—"
    if _is_number(value):
        number = float(value)
        if number.is_integer() and abs(number) < 1e12:
            return str(int(number))
        return f"{number:.{digits}g}" if abs(number) >= 1e4 or abs(number) < 1e-3 else f"{number:.{digits}f}".rstrip(
            "0").rstrip(".")
    return str(value)


def readme_text(manifest: dict) -> str:
    """LEGGIMI del pacchetto: che cosa contiene e come rieseguirlo (interfaccia, riga di comando, API)."""
    job = manifest["job"]
    kind = job["kind"]
    rerun = manifest.get("rerun") or {}
    env = manifest.get("bioloom") or {}
    lines = [
        "Pacchetto di riproducibilità di BioLoom",
        "=======================================",
        "",
        f"Calcolo: {KIND_LABEL.get(kind, kind)} «{job.get('title') or job['id']}» (job {job['id']}),",
        f"concluso il {job.get('finished_at') or '?'} con BioLoom {env.get('version')}"
        + (f" (immagine {env['image']})" if env.get("image") else "") + ".",
        f"Pacchetto creato il {manifest.get('created_at')}.",
        "",
        "Contenuto",
        "  manifest.json  versioni di BioLoom e degli strumenti, parametri completi, semi casuali, impostazioni che",
        "                 influiscono sul risultato, input remoti con la data, checksum SHA-256 di ogni file",
        "  inputs/        parametri, sequenze, template della libreria e copie preparate, allineamento PIR, strutture",
        "  outputs/       i file prodotti dal job, con lo stesso percorso della cartella del job",
        "  scripts/       script MODELLER eseguiti: solo documentazione, la riesecuzione li rigenera dai parametri",
        "  log/job.log    log del calcolo (chiavi di licenza, email e percorsi locali rimossi)",
        "",
    ]
    if rerun.get("supported"):
        lines += [
            "Riesecuzione",
            "  Interfaccia:      Job → «Riesegui da pacchetto», carica questo file e avvia.",
            "  Riga di comando:  python bioloom.py rerun <pacchetto.zip> [--data <cartella dati>]",
            "                    (nel container: python3 -m bioloom rerun <pacchetto.zip> --data <cartella>)",
            "  API:              POST /api/jobs/rerun con il file nel campo «file» (multipart/form-data).",
            "",
            "  BioLoom verifica il manifest e i checksum, ricrea il job con gli stessi parametri (cache dei calcoli",
            "  ignorata, template e strutture dal pacchetto, nessun download) e confronta i risultati con quelli del",
            "  pacchetto: identici, equivalenti entro le tolleranze o diversi, con RMSD e differenze delle metriche.",
            "  Versioni diverse di BioLoom o degli strumenti producono un avviso, non un errore.",
            "",
            f"  Tolleranze: coordinate {COORD_TOLERANCE} Å per atomo (senza sovrapposizione); numeri |Δ| ≤ max("
            f"{SCORE_ABS:g}; {SCORE_REL:g}·|valore|).",
        ]
    else:
        lines += ["Riesecuzione",
                  f"  Non disponibile: {rerun.get('reason') or 'tipo di calcolo non supportato.'}",
                  "  Il pacchetto documenta comunque parametri, input (anche remoti, con la data) e risultati."]
    remote = manifest.get("remote") or []
    if remote:
        lines += ["", "Input da servizi remoti"]
        lines += [f"  - {r.get('service')}: {r.get('what')} ({r.get('date') or 'data non nota'})" for r in remote]
    notes = manifest.get("notes") or []
    if notes:
        lines += ["", "Note"] + [f"  - {n}" for n in notes]
    lines += ["", "Verifica manuale dei checksum: per ogni voce di «files» nel manifest, sha256sum <percorso> deve dare",
              "il valore «sha256». L'impronta «manifest_sha256» è lo SHA-256 del manifest (JSON canonico: chiavi",
              "ordinate, separatori senza spazi) senza quel campo.", ""]
    return "\n".join(lines)


def report_text(report: dict) -> str:
    """Rapporto leggibile della riesecuzione (salvato nel job come RAPPORTO_RIESECUZIONE.txt)."""
    original = report.get("original") or {}
    lines = [
        "Rapporto di riesecuzione — BioLoom",
        "==================================",
        "",
        f"Pacchetto: {KIND_LABEL.get(original.get('kind'), original.get('kind'))} «{original.get('title') or ''}» "
        f"(job {original.get('job_id')}), concluso il {original.get('finished_at') or '?'} con BioLoom "
        f"{original.get('bioloom') or '?'}; pacchetto del {original.get('package_created_at') or '?'}.",
        f"Riesecuzione: BioLoom {report.get('bioloom')}"
        + (f" (immagine {report['image']})" if report.get("image") else "") + f", {report.get('created_at')}.",
        "",
        f"Esito: {str(report.get('verdict', '')).upper()} — {VERDICT_TEXT.get(report.get('verdict'), '')}.",
    ]
    warnings = report.get("warnings") or []
    if warnings:
        lines += ["", "Avvisi"] + [f"  - {w}" for w in warnings]
    structures = report.get("structures") or []
    if structures:
        lines += ["", "Strutture (coordinate senza sovrapposizione)"]
        for s in structures:
            c = s.get("coordinates") or {}
            lines.append(f"  {s['path']}: {c.get('common')} atomi in comune su {c.get('atoms')}/{c.get('rerun_atoms')}, "
                         f"RMSD {_fmt(c.get('rmsd'))} Å, scarto massimo {_fmt(c.get('max_deviation'))} Å"
                         + (f" ({c.get('max_atom')})" if c.get("max_atom") and c.get("max_deviation") else "")
                         + (" — entro la tolleranza" if c.get("within") else " — OLTRE la tolleranza"))
    for section in report.get("metrics") or []:
        lines += ["", section.get("title") or "Metriche"]
        for row in section.get("rows") or []:
            delta = "" if row.get("delta") is None else f"  Δ {_fmt(row['delta'])}"
            mark = "" if row.get("within") else "  ← diverso"
            lines.append(f"  {row['label']:<34} {_fmt(row.get('original')):>14} → {_fmt(row.get('rerun')):<14}{delta}"
                         f"{mark}")
    files = report.get("files") or []
    counts: dict[str, int] = {}
    for f in files:
        counts[f["status"]] = counts.get(f["status"], 0) + 1
    names = {"identical": "identici", "equivalent": "equivalenti", "different": "diversi", "missing": "mancanti",
             "extra": "in più"}
    lines += ["", "File: " + ", ".join(f"{counts.get(k, 0)} {v}" for k, v in names.items())]
    for f in files:
        if f["status"] == "identical":
            continue
        detail = ""
        diff = f.get("diff") or {}
        if f["status"] in ("different", "equivalent") and diff:
            detail = (f": {diff.get('numbers', 0)} numeri, {diff.get('beyond_tolerance', 0)} oltre la tolleranza "
                      f"(Δ massimo {_fmt(diff.get('max_delta'))}), {diff.get('text', 0)} testi e "
                      f"{diff.get('structure', 0)} differenze di struttura")
            examples = diff.get("examples") or []
            if examples:
                ex = examples[0]
                detail += f"; es. {ex['where']}: {ex['original']} → {ex['rerun']}"
        elif f.get("detail"):
            detail = f": {f['detail']}"
        tag = "" if f.get("principal", True) else " (accessorio)"
        lines.append(f"  [{names.get(f['status'], f['status'])}] {f['path']}{tag}{detail}")
    lines += ["", f"Tolleranze: coordinate {COORD_TOLERANCE} Å per atomo, senza sovrapposizione (tre decimali del "
                  "formato PDB); numeri |Δ| ≤ max("
              f"{SCORE_ABS:g}; {SCORE_REL:g}·|valore|) (doppia precisione). Ignorati: provenienza dalla cache, durata "
              "dei passi, numero di thread, data di MODELLER nella riga EXPDTA.", ""]
    return "\n".join(lines)


def principal_file(path: str, role: str, intermediate: Callable[[str], bool]) -> bool:
    """Un file conta per l'esito (`diverso` se manca o differisce) se è un risultato: non gli script (rigenerati dalla
    versione corrente di BioLoom) né i file intermedi di MODELLER (conservati solo con l'impostazione apposita)."""
    return role != "script" and not intermediate(PurePosixPath(path).name)
