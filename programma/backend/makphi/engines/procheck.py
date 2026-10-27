"""PROCHECK (Laskowski et al. 1993): qualità stereochimica, Ramachandran a 4 regioni e G-factor.

Il programma (script csh + eseguibili Fortran) gira nella cartella di lavoro del job; si conservano il
riepilogo `.sum` e i grafici PostScript scelti nelle Impostazioni (`procheck_plots`: tutti, solo il Ramachandran
complessivo, nessuno), gli altri file intermedi vengono eliminati. Le metriche vengono sempre dal riepilogo: i grafici
servono solo a chi vuole le tavole originali di PROCHECK, ma sono quasi tutto lo spazio dei risultati (~3,7 MB per un
modello di 420 residui, contro 2 KB del riepilogo), che in una pipeline con decine di migliaia di modelli pesa centinaia
di GB.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from ..core.errors import AppError, ToolUnavailable
from ..core.jobs import JobContext
from ..domain import procheck as parser
from . import tools

KEEP_SUFFIXES = (".sum", ".ps")
PLOT_CHOICES = ("all", "ramachandran", "none")
# Titolo PostScript del Ramachandran complessivo ("Ramachandran plot, page 1"), diverso da quelli per residuo
# ("All-residue Ramachandran plots")
RAMACHANDRAN_TITLE = "%%Title: Ramachandran plot"


def _keep_plot(path: Path, choice: str) -> bool:
    """Grafico PostScript da conservare secondo l'impostazione `procheck_plots`."""
    if choice == "all":
        return True
    if choice == "none":
        return False
    try:
        with open(path, "rb") as fh:
            head = fh.read(4096).decode("latin-1")
    except OSError:
        return False
    return any(line.startswith(RAMACHANDRAN_TITLE) for line in head.splitlines())


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
    choice = getattr(ctx.settings, "procheck_plots", "all")
    choice = choice if choice in PLOT_CHOICES else "all"
    for path in workdir.iterdir():  # si conservano solo riepilogo e grafici scelti
        if path.is_file() and (path.suffix not in KEEP_SUFFIXES or (path.suffix == ".ps" and not _keep_plot(path, choice))):
            path.unlink(missing_ok=True)
    data["files"] = sorted(p.name for p in workdir.iterdir() if p.is_file())
    data["resolution_used"] = resolution
    data["plots"] = choice
    return data
