"""Cache su file dei risultati di calcolo: ricerche di template e allineamenti multipli e strutturali.

Rilanciare una pipeline con le stesse sequenze e altre opzioni di modellazione, o ripetere un allineamento,
non deve rifare ricerche e allineamenti identici. Ogni voce è un file JSON in
`data/cache/compute/<operazione>/<chiave>.json`:

- **chiave**: SHA-256 del JSON canonico (chiavi ordinate) di operazione, input normalizzati, parametri,
  versione del formato della cache e di BioLoom e "timbro" delle dipendenze (dimensione e data dei file dei
  database locali, percorso e data degli eseguibili, SHA-256 del contenuto delle strutture). Se una di queste
  cose cambia cambia la chiave: la voce vecchia non viene più letta e la elimina la pulizia automatica;
- **valore**: il risultato del motore e il testo dei file che il calcolo ha scritto nella cartella di lavoro
  (query.fasta, output di MMseqs2 o di TM-align...): un risultato dalla cache ricrea gli stessi file del job;
- **scadenza**: facoltativa, scelta da chi chiama per ogni operazione (i motori remoti dipendono dal PDB, che
  cambia ogni settimana). Le voci più vecchie di 30 giorni le elimina `services/housekeeping.py`, come le
  risposte dei servizi remoti che stanno nella stessa cartella `cache`.

Scritture atomiche (`core/storage`) e lettura tollerante: un file illeggibile, di un altro formato o scaduto
è solo un mancato riscontro, mai un errore. Entrano in cache solo i calcoli riusciti e completi di job non
annullati. Senza `configure()` (test, avvio senza cartella dati) la cache non fa nulla.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Optional

from .. import __version__
from .errors import AppError
from .storage import atomic_write_bytes, atomic_write_text, read_json, safe_join

FORMAT_VERSION = 1
# Ricerche su RCSB e NCBI: il PDB si aggiorna ogni settimana, i risultati più vecchi non sono più completi.
REMOTE_TTL = 7 * 86400
# Oltre questa dimensione dei file prodotti il risultato non entra in cache (la cartella dati può essere
# una chiavetta): si ricalcola.
MAX_FILES_BYTES = 20 * 1024 * 1024
# Orologio di un altro computer (cartella dati portatile): una voce "dal futuro" oltre un giorno non vale.
CLOCK_TOLERANCE = 86400
# File del job che non sono prodotti dal calcolo (senza scratch stanno nella stessa cartella di lavoro).
RESERVED_FILES = frozenset({"job.log", "params.json", "result.json"})
CACHE_FIELDS = ("cached", "cached_at")

_OPERATION = re.compile(r"^[a-z][a-z0-9_]{0,40}$")
_root: Optional[Path] = None


def configure(root: Optional[Path]) -> None:
    """Cartella della cache dei calcoli (`data/cache/compute`); None la disattiva."""
    global _root
    _root = Path(root) if root is not None else None


def root() -> Optional[Path]:
    return _root


def enabled(ctx: Any = None) -> bool:
    """Cache configurata e non disattivata nelle Impostazioni (`compute_cache`)."""
    if _root is None:
        return False
    settings = getattr(ctx, "settings", None) if ctx is not None else None
    return bool(getattr(settings, "compute_cache", True))


# ------------------------------------------------------------------------------------------ chiavi e timbri
def make_key(operation: str, descriptor: dict) -> str:
    """SHA-256 del JSON canonico della richiesta. Solleva ValueError/TypeError se non è serializzabile."""
    payload = {"format": FORMAT_VERSION, "bioloom": __version__, "operation": operation, "input": descriptor}
    text = json.dumps(payload, sort_keys=True, ensure_ascii=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def file_stamp(path: Optional[os.PathLike | str]) -> Optional[dict]:
    """Timbro di un file (eseguibile, database): percorso, dimensione e data di modifica in secondi.

    Le date intere evitano differenze di arrotondamento tra filesystem (exFAT, bind mount di Docker Desktop).
    """
    if path is None:
        return None
    try:
        st = os.stat(path)
    except OSError:
        return {"path": str(path), "missing": True}
    return {"path": str(path), "size": st.st_size, "mtime": int(st.st_mtime)}


def files_stamp(paths: Iterable[Path]) -> list:
    """Timbro di un insieme di file (es. tutti i file di un database): nome, dimensione, data."""
    out = []
    for path in paths:
        try:
            st = path.stat()
        except OSError:
            continue
        out.append([path.name, st.st_size, int(st.st_mtime)])
    return sorted(out)


def content_hash(path: Path) -> str:
    """SHA-256 del contenuto di un file (strutture: stesso contenuto = stesso risultato, qualunque il nome)."""
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def iso(timestamp: float) -> str:
    return datetime.fromtimestamp(timestamp, timezone.utc).isoformat(timespec="seconds")


# ------------------------------------------------------------------------------------------ lettura e scrittura
def _entry_path(operation: str, key: str) -> Optional[Path]:
    if _root is None:
        return None
    if not _OPERATION.match(operation) or not re.fullmatch(r"[0-9a-f]{64}", key):
        raise ValueError(f"operazione o chiave di cache non valida: {operation}/{key}")
    return _root / operation / f"{key}.json"


def _valid(entry: Any, operation: str, key: str) -> bool:
    if not isinstance(entry, dict):
        return False
    files = entry.get("files")
    return (entry.get("format") == FORMAT_VERSION and entry.get("operation") == operation
            and entry.get("key") == key and isinstance(entry.get("created"), (int, float))
            and isinstance(entry.get("result"), dict) and isinstance(files, dict)
            and all(isinstance(k, str) and isinstance(v, str) for k, v in files.items()))


def lookup(operation: str, key: str, ttl: Optional[float] = None, now: Optional[float] = None) -> Optional[dict]:
    """Voce valida e non scaduta, altrimenti None (file assente, corrotto, di un altro formato o scaduto)."""
    path = _entry_path(operation, key)
    if path is None:
        return None
    entry = read_json(path)
    if not _valid(entry, operation, key):
        return None
    age = (time.time() if now is None else now) - float(entry["created"])
    if age < -CLOCK_TOLERANCE or (ttl is not None and age > ttl):
        return None
    return entry


def store(operation: str, key: str, result: dict, files: Optional[dict[str, str]] = None) -> Optional[dict]:
    """Scrive (o sostituisce) una voce con scrittura atomica: un lettore vede la voce vecchia o la nuova."""
    path = _entry_path(operation, key)
    if path is None:
        return None
    now = time.time()
    entry = {"format": FORMAT_VERSION, "operation": operation, "key": key, "created": now, "created_at": iso(now),
             "bioloom": __version__, "result": {k: v for k, v in result.items() if k not in CACHE_FIELDS},
             "files": files or {}}
    # compatto: la cartella dati può stare su una chiavetta
    atomic_write_text(path, json.dumps(entry, ensure_ascii=False, separators=(",", ":")))
    return entry


# ------------------------------------------------------------------------------------------ file del calcolo
def _snapshot(workdir: Path) -> dict[str, tuple[int, int]]:
    """Firma (dimensione, data in ns) dei file della cartella di lavoro, esclusi temporanei e file del job."""
    out: dict[str, tuple[int, int]] = {}
    if not workdir.is_dir():
        return out
    for dirpath, dirnames, filenames in os.walk(workdir):
        dirnames[:] = [d for d in dirnames if not d.startswith(".")]
        for name in filenames:
            if name.startswith(".") or name in RESERVED_FILES:
                continue
            full = os.path.join(dirpath, name)
            try:
                st = os.stat(full)
            except OSError:
                continue
            out[Path(full).relative_to(workdir).as_posix()] = (st.st_size, st.st_mtime_ns)
    return out


def _written_files(workdir: Path, before: dict[str, tuple[int, int]]) -> Optional[dict[str, str]]:
    """Testo dei file creati o modificati dal calcolo; None se troppo grandi o non testuali (niente cache)."""
    files: dict[str, str] = {}
    total = 0
    for rel, signature in sorted(_snapshot(workdir).items()):
        if before.get(rel) == signature:
            continue
        data = (workdir / rel).read_bytes()
        total += len(data)
        if total > MAX_FILES_BYTES:
            return None
        try:
            files[rel] = data.decode("utf-8")
        except UnicodeDecodeError:
            return None
    return files


def _restore_files(workdir: Path, files: dict[str, str]) -> None:
    """Ricrea i file del calcolo (byte per byte) dentro la cartella di lavoro, senza uscirne."""
    for rel, text in files.items():
        parts = rel.split("/")
        if any(not p or p.startswith(".") for p in parts) or parts[-1] in RESERVED_FILES:
            raise ValueError(f"percorso non valido nella cache: {rel}")
        atomic_write_bytes(safe_join(workdir, *parts), text.encode("utf-8"))


# ------------------------------------------------------------------------------------------ uso dai motori
def run_cached(ctx: Any, operation: str, descriptor: dict, compute: Callable[[], dict], *,
               use_cache: bool = True, ttl: Optional[float] = None,
               keep: Optional[Callable[[dict], bool]] = None,
               accept: Optional[Callable[[dict], bool]] = None) -> dict:
    """Esegue `compute()` passando dalla cache.

    - `use_cache=True`: se c'è una voce valida restituisce il suo risultato con `cached=True` e `cached_at`
      (data ISO del calcolo), dopo aver ricreato i file del calcolo nella cartella di lavoro;
    - `use_cache=False`: ricalcola sempre e sostituisce la voce;
    - dopo un calcolo riuscito, se il job non è stato annullato e `keep(result)` è vero (risultato completo),
      la voce viene scritta. Un errore di lettura o scrittura della cache non fa mai fallire il calcolo;
    - `accept(result)`, facoltativo, si applica alla lettura: una voce il cui risultato non lo soddisfa (per
      esempio scritta da una versione che non calcolava ancora campi oggi richiesti) è un mancato riscontro e
      viene sostituita dal nuovo calcolo.
    """
    if not enabled(ctx):
        return compute()
    try:
        key = make_key(operation, descriptor)
    except (TypeError, ValueError):  # input non rappresentabile: si calcola senza cache
        return compute()
    workdir = Path(ctx.workdir)
    if use_cache:
        entry = lookup(operation, key, ttl)
        if entry is not None and accept is not None and not accept(entry["result"]):
            ctx.log("Risultato in cache incompleto (versione precedente): nuovo calcolo.")
            entry = None
        if entry is not None:
            try:
                _restore_files(workdir, entry["files"])
            except (OSError, ValueError, AppError) as exc:
                ctx.log(f"Avviso: file della cache non ripristinati ({exc}): nuovo calcolo.")
            else:
                when = datetime.fromtimestamp(float(entry["created"])).strftime("%d/%m/%Y %H:%M")
                ctx.log(f"Risultato dalla cache del {when}: nessun nuovo calcolo "
                        "(per rifarlo: «Ricalcola ignorando la cache»).")
                return {**entry["result"], "cached": True, "cached_at": iso(float(entry["created"]))}
    else:
        ctx.log("Cache dei calcoli ignorata su richiesta: nuovo calcolo, il risultato sostituisce quello in cache.")
    before = _snapshot(workdir)
    result = compute()
    if getattr(ctx, "cancelled", False) or (keep is not None and not keep(result)):
        return result
    try:
        files = _written_files(workdir, before)
        if files is None:
            ctx.log("Risultato non salvato nella cache dei calcoli: file prodotti troppo grandi o non testuali.")
        else:
            store(operation, key, result, files)
    except (OSError, TypeError, ValueError) as exc:
        ctx.log(f"Avviso: cache dei calcoli non aggiornata ({exc}).")
    return result
