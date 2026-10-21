"""Versioni degli strumenti per i pacchetti di riproducibilità (F9.4).

Come la verifica dell'installazione (services/selftest.py) gli strumenti si trovano con `tools.find` /
`tools.find_module` e MODELLER con `tools.detect_modeller`; in più qui si chiede a ogni programma la sua versione,
con l'opzione che la stampa (misurato nell'immagine: `mkdssp --version` → «mkdssp version 4.2.2», `blastp -version`
→ «blastp: 2.12.0+», `TMalign -v` → «TM-align Version 20190822», `USalign -h` → «US-align (Version 20260527)», …), in
un processo breve con timeout. Il risultato resta in memoria per eseguibile (percorso, dimensione, data): un
aggiornamento dell'immagine cambia il timbro e la versione si richiede.

PROCHECK non ha un'opzione di versione: la si legge dallo script `procheck.scr` («PROCHECK v.3.5.4»). ProSA II 2003 non
si avvia senza licenza: resta la versione del nome. Le librerie Python (OpenMM, PDBFixer, Biopython, NumPy) si leggono
dai metadati del pacchetto installato, senza importarle.
"""

from __future__ import annotations

import importlib.metadata
import os
import platform
import re
import subprocess
import sys
import tempfile
import threading
from typing import Optional

from .. import __version__
from ..core.fingerprint import code_fingerprint
from ..core.jobs import default_env
from . import tools

# opzione che stampa la versione ed espressione che la estrae (stdout + stderr)
PROBES: dict[str, tuple[list[str], str]] = {
    "dssp": (["--version"], r"version\s+(\d+(?:\.\d+)+)"),
    "blastp": (["-version"], r"blastp:\s*(\S+)"),
    "blastdbcmd": (["-version"], r"blastdbcmd:\s*(\S+)"),
    "clustalo": (["--version"], r"(\d+(?:\.\d+)+)"),
    "mafft": (["--version"], r"v(\d+(?:\.\d+)+)"),
    "muscle": (["-version"], r"(?:MUSCLE|muscle)\s+v?(\d+(?:\.\d+)+)"),
    "tmalign": (["-v"], r"Version\s+(\d+)"),
    "usalign": (["-h"], r"Version\s+(\d+)"),
    "mmseqs": (["version"], r"^\s*(\S+)\s*$"),
    "fpocket": ([], r"fpocket\s+(\d+(?:\.\d+)+)"),
    "apbs": (["--version"], r"APBS\s+(\d+(?:\.\d+)+)"),
    "pdb2pqr": (["--version"], r"pdb2pqr\s+(\d+(?:\.\d+)+)"),
}
FIXED = {"prosa": "2003"}
DISTRIBUTIONS = {"openmm": ("openmm", "OpenMM"), "pdbfixer": ("pdbfixer",)}
LIBRARIES = {"biopython": ("biopython",), "numpy": ("numpy",)}
PROBE_TIMEOUT = 10
_ANSI = re.compile(r"\x1b\[[0-9;]*m")

_cache: dict[tuple, Optional[str]] = {}
_lock = threading.Lock()


def _stamp(path: str) -> tuple:
    try:
        st = os.stat(path)
        return (path, st.st_size, int(st.st_mtime))
    except OSError:
        return (path, None, None)


def _probe(key: str, exe: str) -> Optional[str]:
    """Versione dichiarata dal programma (None se non la stampa o non risponde entro il timeout)."""
    stamp = (key, *_stamp(exe))
    with _lock:
        if stamp in _cache:
            return _cache[stamp]
    version = None
    if key == "procheck":
        try:
            with open(exe, encoding="utf-8", errors="replace") as fh:
                match = re.search(r"PROCHECK v\.?\s*(\d+(?:\.\d+)+)", fh.read(200_000))
            version = match.group(1) if match else None
        except OSError:
            version = None
    elif key in PROBES:
        args, pattern = PROBES[key]
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0
        try:
            # cartella temporanea: alcuni programmi senza argomenti (fpocket) scrivono la loro guida e nient'altro,
            # ma nessun file deve finire nella cartella del server
            with tempfile.TemporaryDirectory(prefix="bioloom-version-") as cwd:
                proc = subprocess.run([exe, *args], capture_output=True, text=True, errors="replace",
                                      timeout=PROBE_TIMEOUT, cwd=cwd, stdin=subprocess.DEVNULL,
                                      creationflags=creationflags, env=default_env())  # senza i segreti del server
            text = _ANSI.sub("", (proc.stdout or "") + "\n" + (proc.stderr or ""))
            match = re.search(pattern, text, re.MULTILINE)
            version = match.group(1) if match else None
        except (OSError, subprocess.SubprocessError, ValueError):
            version = None
    with _lock:
        _cache[stamp] = version
    return version


def _distribution(names: tuple[str, ...]) -> Optional[str]:
    for name in names:
        try:
            return importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            continue
    return None


def tool(key: str, modeller_python: str = "") -> dict:
    """{label, available, version} di uno strumento (chiave di `tools.TOOLS`, `tools.MODULES` o "modeller")."""
    if key == "modeller":
        info = tools.detect_modeller(modeller_python)
        return {"label": "MODELLER", "available": bool(info.get("available")), "version": info.get("version")}
    if key in tools.MODULES:
        found = tools.find_module(key) is not None
        return {"label": tools.MODULES[key]["label"], "available": found,
                "version": _distribution(DISTRIBUTIONS.get(key, (key,))) if found else None}
    meta = tools.TOOLS[key]
    exe = tools.find(key)
    version = FIXED.get(key) if exe else None
    if exe and version is None:
        version = _probe(key, exe)
    return {"label": meta["label"], "available": exe is not None, "version": version}


def environment(keys: list[str], modeller_python: str = "") -> dict:
    """Ambiente di calcolo per il manifest: Python, sistema, architettura, librerie di calcolo, strumenti indicati.
    Nessun percorso: solo nomi e versioni."""
    return {
        "python": sys.version.split()[0],
        "system": platform.system(),
        "machine": platform.machine(),
        "libraries": {name: _distribution(dist) for name, dist in LIBRARIES.items()},
        "tools": {key: tool(key, modeller_python) for key in keys},
    }


def bioloom() -> dict:
    """Versione di BioLoom, dell'immagine Docker e impronta del codice (core/fingerprint.py); le ultime due sono
    vuote fuori dal container."""
    return {"version": __version__, "image": os.environ.get("BIOLOOM_IMAGE_VERSION") or None,
            "fingerprint": code_fingerprint()}
