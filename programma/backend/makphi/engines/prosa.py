"""ProSA II 2003 (Sippl): z-score energetico del ripiegamento e profilo di energia per residuo.

Richiede il file di licenza LicenseDB (Impostazioni → Licenze) e che il container abbia il MAC della
licenza (lo imposta l'avviatore). Il programma viene guidato da un file di comandi.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Optional

from ..core.errors import AppError, ToolUnavailable
from ..core.jobs import JobContext, default_env
from . import tools

# Profilo per residuo grezzo (finestra 1) e smussato (finestra 10, come ProSA-web); z-score nel file .slp.
COMMANDS = """read pdb model.pdb model
analyse energy model
print energy model model_w1
winsize model 10
print energy model model_w10
init zscore
zscore model model
exit
"""


def parse_zscores(text: str) -> Optional[dict]:
    """Riga della molecola nel file .slp: `model  76  -9.26  -5.86  -6.77 ...` (zp-comb, zp-pair, zp-surf)."""
    for line in text.splitlines():
        parts = line.split()
        if len(parts) >= 5 and parts[0] == "model" and parts[1].isdigit():
            try:
                return {"length": int(parts[1]), "combined": float(parts[2]), "pair": float(parts[3]),
                        "surface": float(parts[4])}
            except ValueError:
                continue
    return None


def parse_energy_profile(text: str) -> list[dict]:
    """Righe del file .ana: indice sequenziale, energia di coppia, di superficie, combinata."""
    rows = []
    for line in text.splitlines():
        parts = line.split()
        if len(parts) >= 4 and parts[0].isdigit():
            try:
                rows.append({"index": int(parts[0]), "pair": float(parts[1]), "surface": float(parts[2]),
                             "combined": float(parts[3])})
            except ValueError:
                continue
    return rows


def _read(path: Path) -> str:
    return path.read_text(errors="replace") if path.exists() else ""


def run(pdb_path: Path, ctx: JobContext, workdir: Path) -> dict:
    exe = tools.find("prosa")
    if not exe:
        raise ToolUnavailable("ProSA II non è installato in questa immagine.")
    workdir.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(pdb_path, workdir / "model.pdb")
    (workdir / "prosa.cmd").write_text(COMMANDS, encoding="ascii")
    env = default_env()
    for name, value in (("USER", "makphi"), ("LOGNAME", "makphi"), ("HOME", str(workdir))):
        env.setdefault(name, value)
    result = ctx.run([exe, "prosa.cmd"], cwd=workdir, env=env, log_output=False, timeout=600)
    output = "\n".join(ln for ln in result.output.splitlines() if not ln.strip().startswith("ERROR: ld.so"))
    lowered = output.lower()
    # es. "No line for 'ProSa2003' and system '...' in licence file" oppure "Cannot open licence file"
    if "licence file" in lowered or "license file" in lowered:
        raise ToolUnavailable("ProSA: licenza assente o non valida per questo computer. Carica il file LicenseDB "
                              "(Impostazioni → Licenze) e avvia MakPhi dall'avviatore, che imposta il MAC richiesto.")
    zscores = parse_zscores(_read(workdir / "model.slp"))
    if zscores is None:
        tail = "\n".join(output.strip().splitlines()[-8:])
        raise AppError(f"ProSA non ha calcolato lo z-score (codice {result.returncode}).\n{tail}", code="prosa_error")
    raw = parse_energy_profile(_read(workdir / "model_w1.ana"))
    smooth = {r["index"]: r["combined"] for r in parse_energy_profile(_read(workdir / "model_w10.ana"))}
    profile = [{"index": r["index"], "energy": r["combined"], "energy_w10": smooth.get(r["index"])} for r in raw]
    for name in os.listdir(workdir):
        if name not in ("model_w1.ana", "model_w10.ana", "model.slp", "prosa.cmd"):
            (workdir / name).unlink(missing_ok=True)
    return {"zscore": zscores["combined"], "zscore_pair": zscores["pair"], "zscore_surface": zscores["surface"],
            "profile": profile}
