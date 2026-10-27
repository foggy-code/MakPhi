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


def _unsafe_path() -> AppError:
    return AppError("Percorso non consentito.", code="unsafe_path")


def _inside(path: Path, root: Path) -> bool:
    return path == root or root in path.parents


def _is_link(path: Path) -> bool:
    """Link simbolico o, su Windows, punto di reparse (giunzioni comprese, che os.path.islink non vede). Un percorso
    che non si legge (assente, in eliminazione) non è un link: come per Path.resolve, che lo lascia com'è."""
    try:
        st = os.lstat(path)
    except OSError:
        return False
    return stat.S_ISLNK(st.st_mode) or bool(getattr(st, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT)


def safe_join(base: Path, *parts: str) -> Path:
    r"""Unisce i percorsi e verifica che il risultato resti dentro `base`.

    Su Windows Path.resolve() di una cartella che un altro thread sta eliminando (lo scratch a fine job) restituisce
    \\?\C:\... o \\?\C:\$Extend\$Deleted\..., fuori dalla radice anche per un id valido. Quando la risoluzione
    completa esce dalla radice si risale fino alla prima cartella madre che si risolve dentro la radice e si
    riaggiungono i nomi restanti così come sono: sono rifiutati comunque '..', '.', nomi vuoti o di soli punti e spazi,
    link simbolici e giunzioni (che porterebbero fuori), madri fuori dalla radice e nomi che Windows riscriverebbe
    (dispositivi come CON, punti o spazi finali). Su Linux un percorso esce dalla radice solo con '..', percorsi
    assoluti o link: resta rifiutato come prima.
    """
    root = Path(base).resolve()
    joined = root.joinpath(*parts)
    target = joined.resolve()
    if _inside(target, root):
        return target
    names: list[str] = []
    current, anchor = joined, root
    while current != root:
        if current == current.parent or not current.name.strip(". "):  # radice del disco, '', '.', '..', '...'
            raise _unsafe_path()
        names.append(current.name)
        current = current.parent
        if current != root:
            resolved = current.resolve()
            if _inside(resolved, root):
                anchor = resolved
                break
    target = anchor
    for name in reversed(names):
        target = target / name
        if _is_link(target):
            raise _unsafe_path()
    if not _inside(target, root) or Path(os.path.abspath(target)) != target:
        raise _unsafe_path()
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
