"""PROCHECK (Laskowski et al. 1993): qualità stereochimica, Ramachandran a 4 regioni e G-factor.

Il programma (script csh + eseguibili Fortran) gira nella cartella di lavoro del job; si conservano il
riepilogo `.sum` e i grafici PostScript, gli altri file intermedi vengono eliminati.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from ..core.errors import AppError, ToolUnavailable
from ..core.jobs import JobContext
from ..domain import procheck as parser
from . import tools

KEEP_SUFFIXES = (".sum", ".ps")


def run(pdb_path: Path, ctx: JobContext, workdir: Path, resolution: float = 2.0) -> dict:
    exe = tools.find("procheck")
    if not exe:
        raise ToolUnavailable("PROCHECK non è installato in questa immagine.")
    csh = shutil.which("csh") or shutil.which("tcsh")
    if not csh:
        raise ToolUnavailable("PROCHECK richiede la shell csh.")
    workdir.mkdir(parents=True, exist_ok=True)
    model = workdir / "model.pdb"
    shutil.copyfile(pdb_path, model)
    result = ctx.run([csh, exe, model.name, f"{resolution:.1f}"], cwd=workdir, log_output=False, timeout=900)
    summary = workdir / "model.sum"
    if not summary.exists():
        tail = "\n".join(result.output.strip().splitlines()[-8:])
        raise AppError(f"PROCHECK non ha prodotto il riepilogo (codice {result.returncode}).\n{tail}",
                       code="procheck_error")
    data = parser.parse_sum(summary.read_text(encoding="utf-8", errors="replace"))
    for path in workdir.iterdir():  # si conservano solo riepilogo e grafici
        if path.is_file() and path.suffix not in KEEP_SUFFIXES:
            path.unlink(missing_ok=True)
    data["files"] = sorted(p.name for p in workdir.iterdir() if p.is_file())
    data["resolution_used"] = resolution
    return data
