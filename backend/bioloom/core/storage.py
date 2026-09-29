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


def remove_tree(path: Path) -> None:
    """Rimozione ricorsiva che sblocca i file in sola lettura (Windows)."""

    def _on_error(func, target, _exc):
        try:
            os.chmod(target, stat.S_IWRITE)
            func(target)
        except OSError:
            pass

    path = Path(path)
    if path.is_dir():
        shutil.rmtree(path, onerror=_on_error)
    elif path.exists():
        os.chmod(path, stat.S_IWRITE)
        path.unlink()


def dir_size(path: Path) -> int:
    total = 0
    for root, _dirs, files in os.walk(path):
        for name in files:
            try:
                total += os.path.getsize(os.path.join(root, name))
            except OSError:
                pass
    return total
