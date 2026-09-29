"""Rilevamento degli strumenti opzionali installati (DSSP, BLAST+, allineatori, validatori)."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Optional

TOOLS: dict[str, dict] = {
    "dssp": {"names": ["mkdssp", "dssp"], "label": "DSSP", "use": "struttura secondaria e SASA di riferimento"},
    "blastp": {"names": ["blastp"], "label": "BLAST+ (blastp)", "use": "ricerca template locale"},
    "clustalo": {"names": ["clustalo"], "label": "Clustal Omega", "use": "allineamento multiplo"},
    "mafft": {"names": ["mafft", "mafft.bat"], "label": "MAFFT", "use": "allineamento multiplo"},
    "muscle": {"names": ["muscle"], "label": "MUSCLE", "use": "allineamento multiplo"},
    "tmalign": {"names": ["TMalign", "tmalign"], "label": "TM-align", "use": "allineamento strutturale"},
    "usalign": {"names": ["USalign", "usalign"], "label": "US-align", "use": "allineamento strutturale"},
    "mmseqs": {"names": ["mmseqs"], "label": "MMseqs2", "use": "ricerca template locale veloce"},
    "blastdbcmd": {"names": ["blastdbcmd"], "label": "BLAST+ (blastdbcmd)", "use": "estrazione sequenze dal database pdbaa"},
    "procheck": {"names": ["procheck.scr"], "label": "PROCHECK", "use": "stereochimica (Ramachandran, G-factor)"},
    "prosa": {"names": ["prosa2003"], "label": "ProSA II 2003", "use": "z-score energetico del ripiegamento"},
}

_extra_dirs: list[Path] = []
_cache: dict[str, Optional[str]] = {}
_lock = threading.Lock()


def configure(tools_dir: Path) -> None:
    """Aggiunge data/tools e le sue sottocartelle bin/ ai percorsi di ricerca."""
    global _extra_dirs
    dirs = [tools_dir, tools_dir / "bin"]
    if tools_dir.exists():
        dirs += [p for p in tools_dir.iterdir() if p.is_dir()]
        dirs += [p / "bin" for p in tools_dir.iterdir() if (p / "bin").is_dir()]
    _extra_dirs = dirs
    with _lock:
        _cache.clear()


def find(key: str) -> Optional[str]:
    with _lock:
        if key in _cache:
            return _cache[key]
    names = TOOLS[key]["names"]
    found = None
    search_path = os.pathsep.join([str(d) for d in _extra_dirs] + [os.environ.get("PATH", "")])
    for name in names:
        found = shutil.which(name, path=search_path)
        if found:
            break
    with _lock:
        _cache[key] = found
    return found


def status() -> dict:
    return {key: {"label": meta["label"], "use": meta["use"], "path": find(key), "available": find(key) is not None}
            for key, meta in TOOLS.items()}


# ------------------------------------------------------------------------------------------ MODELLER
# Preambolo degli script MODELLER: se BioLoom ha una chiave di licenza (variabile BIOLOOM_MODELLER_KEY),
# registra un modulo `modeller.config` con quella chiave prima dell'import di `modeller`. Il file
# config.py dell'installazione non viene modificato (funziona anche con installazioni in sola lettura).
LICENSE_PREAMBLE = '''\
import os as _bl_os, sys as _bl_sys
_bl_key = _bl_os.environ.pop("BIOLOOM_MODELLER_KEY", "")
if _bl_key:
    import importlib.util as _bl_util, types as _bl_types
    _bl_spec = _bl_util.find_spec("modeller")
    if _bl_spec is not None and _bl_spec.origin:
        _bl_real = _bl_os.path.join(_bl_os.path.dirname(_bl_spec.origin), "config.py")
        _bl_ns = {"__file__": _bl_real, "__name__": "modeller.config"}
        try:
            with open(_bl_real) as _bl_fh:
                exec(compile(_bl_fh.read(), _bl_real, "exec"), _bl_ns)
        except Exception:
            pass
        _bl_cfg = _bl_types.ModuleType("modeller.config")
        _bl_cfg.__dict__.update({k: v for k, v in _bl_ns.items() if not k.startswith("__")})
        _bl_cfg.__file__ = _bl_real
        _bl_cfg.license = _bl_key
        _bl_sys.modules["modeller.config"] = _bl_cfg
'''

_modeller_cache: dict = {"at": 0.0, "key": None, "info": None}
_PROBE = LICENSE_PREAMBLE + (
    "import json, sys, modeller\n"
    "print(json.dumps({'version': getattr(modeller, '__version__', '?'), 'python': sys.version.split()[0]}))\n")


def modeller_candidates(configured: str) -> list[str]:
    candidates = [configured, os.environ.get("BIOLOOM_MODELLER_PYTHON", ""), sys.executable]
    seen, out = set(), []
    for c in candidates:
        if c and c not in seen:
            seen.add(c)
            out.append(c)
    return out


def detect_modeller(configured: str = "", force: bool = False, license_key: Optional[str] = None) -> dict:
    """Cerca un interprete Python in cui `import modeller` funzioni. Il risultato resta in cache 5 minuti.

    `license_key` permette di provare una chiave prima di salvarla; altrimenti vale quella attiva.
    """
    from ..core.jobs import default_env

    key = configured or ""
    if license_key is None and not force and _modeller_cache["key"] == key \
            and time.time() - _modeller_cache["at"] < 300:
        return _modeller_cache["info"]
    env = default_env()
    if license_key is not None:
        env["BIOLOOM_MODELLER_KEY"] = license_key
    attempts = []
    info = {"available": False, "python": None, "version": None, "attempts": attempts}
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0
    for python in modeller_candidates(configured):
        if not Path(python).exists() and not shutil.which(python):
            attempts.append({"python": python, "error": "interprete non trovato"})
            continue
        try:
            proc = subprocess.run([python, "-c", _PROBE], capture_output=True, text=True, timeout=90,
                                  creationflags=creationflags, env=env)
        except (OSError, subprocess.TimeoutExpired) as exc:
            attempts.append({"python": python, "error": str(exc)})
            continue
        if proc.returncode == 0:
            try:
                data = json.loads(proc.stdout.strip().splitlines()[-1])
                info.update({"available": True, "python": python, "version": data.get("version"),
                             "python_version": data.get("python")})
                break
            except (ValueError, IndexError):
                pass
        lines = (proc.stderr or proc.stdout).strip().splitlines()
        # es. "ModellerError: check_lice_E> Invalid license key" è più utile dell'ultima riga del traceback
        message = next((ln.strip() for ln in reversed(lines) if "Error" in ln), lines[-1] if lines else "")
        attempts.append({"python": python, "error": message or f"exit {proc.returncode}"})
    if license_key is None:  # la prova di una chiave candidata non sostituisce lo stato attivo
        _modeller_cache.update({"at": time.time(), "key": key, "info": info})
    return info


def invalidate_modeller_cache() -> None:
    _modeller_cache.update({"at": 0.0, "key": None, "info": None})
