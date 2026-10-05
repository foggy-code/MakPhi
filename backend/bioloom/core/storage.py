"""Primitive di I/O sicuro: scritture atomiche, percorsi confinati, identificatori validati."""

from __future__ import annotations

import json
import os
import re
import shutil
import stat
import tempfile
import time
from pathlib import Path
from typing import Any

from .errors import AppError

_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.\-]{0,63}$")


def validate_identifier(value: str, name: str = "identificatore") -> str:
    """Accetta solo identificatori semplici, senza separatori di percorso né '..'."""
    clean = (value or "").strip()
    if not clean or not _IDENTIFIER.match(clean) or ".." in clean:
        raise AppError(f"{name} non valido: '{value}'", code="invalid_identifier")
    return clean


def safe_join(base: Path, *parts: str) -> Path:
    """Unisce i percorsi e verifica che il risultato resti dentro `base`."""
    root = Path(base).resolve()
    target = root.joinpath(*parts).resolve()
    if target != root and root not in target.parents:
        raise AppError("Percorso non consentito.", code="unsafe_path")
    return target


def _replace_with_retry(src: str, dst: Path, attempts: int = 6) -> None:
    # Su Windows antivirus e indicizzatori tengono aperti i file per pochi millisecondi.
    for i in range(attempts):
        try:
            os.replace(src, dst)
            return
        except PermissionError:
            if i == attempts - 1:
                raise
            time.sleep(0.05 * (i + 1))


def atomic_write_text(path: Path, text: str) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        _replace_with_retry(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise


def atomic_write_bytes(path: Path, data: bytes) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        _replace_with_retry(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise


def atomic_write_json(path: Path, data: Any) -> None:
    atomic_write_text(path, json.dumps(data, indent=2, ensure_ascii=False))


def read_json(path: Path, default: Any = None) -> Any:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return default


def _grant(path: str, bits: int) -> None:
    """Aggiunge i permessi `bits` (mai ne toglie); un collegamento simbolico non si segue."""
    mode = os.lstat(path).st_mode
    if not stat.S_ISLNK(mode):
        os.chmod(path, stat.S_IMODE(mode) | bits)


def remove_tree(path: Path, attempts: int = 3) -> bool:
    """Rimozione ricorsiva che sblocca i file in sola lettura (Windows); True se non resta nulla.

    Quando un file o una cartella non si elimina, il proprietario riceve i permessi che servono (lettura e scrittura
    sul file; lettura, scrittura e accesso sulla cartella da eliminare e su quella che lo contiene, se è dentro
    l'albero) e si riprova una volta: i permessi si aggiungono e non si tolgono mai, così nessuna cartella diventa
    illeggibile per il tentativo successivo. Una cartella da cui qualcosa non si è potuto togliere si riprova fino a
    `attempts` volte, con una breve pausa (antivirus e indicizzatori tengono aperti i file per pochi istanti). Gli
    errori dell'eliminazione non escono da qui, neanche per un file singolo: il risultato dice se la rimozione è
    riuscita.
    """
    root = os.path.abspath(path)

    def _on_error(func, target, _exc):
        # si riprova solo l'eliminazione (rmtree passa il percorso completo anche quando lavora con dir_fd);
        # un'apertura o una lettura non riuscita (os.open, os.scandir, os.lstat) resta com'è
        if func not in (os.unlink, os.remove, os.rmdir):
            return
        try:
            full = os.path.abspath(target)
            if full != root:  # la cartella che contiene la radice non si tocca
                _grant(os.path.dirname(full), stat.S_IRWXU)
            _grant(full, stat.S_IRWXU if func is os.rmdir else stat.S_IREAD | stat.S_IWRITE)
            func(full)
        except Exception:
            pass

    path = Path(path)
    if path.is_dir():
        for i in range(1, max(1, attempts) + 1):
            shutil.rmtree(path, onerror=_on_error)
            if not path.exists() or i >= attempts:
                break
            time.sleep(0.1 * i)
    elif path.exists():
        try:
            _grant(str(path), stat.S_IREAD | stat.S_IWRITE)
        except OSError:
            pass
        try:
            path.unlink()
        except OSError:
            pass
    return not path.exists()


def dir_size(path: Path) -> int:
    total = 0
    for root, _dirs, files in os.walk(path):
        for name in files:
            try:
                total += os.path.getsize(os.path.join(root, name))
            except OSError:
                pass
    return total


def dir_size_until(path: Path, deadline: float) -> tuple[int, bool]:
    """Come `dir_size`, ma si ferma quando `time.monotonic()` raggiunge `deadline` (controllo a ogni cartella e ogni
    256 file): (byte contati, completo). Con il tempo scaduto i byte sono un minimo, non il totale."""
    total = 0
    for root, _dirs, files in os.walk(path):
        if time.monotonic() >= deadline:
            return total, False
        for i, name in enumerate(files, 1):
            if i % 256 == 0 and time.monotonic() >= deadline:
                return total, False
            try:
                total += os.path.getsize(os.path.join(root, name))
            except OSError:
                pass
    return total, True
