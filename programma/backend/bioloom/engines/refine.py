"""Rifinitura energetica con OpenMM e PDBFixer (F9.1): preparazione, processo di calcolo e lettura del risultato.

Il server non importa OpenMM: il calcolo gira in `refine_worker.py`, eseguito con lo stesso interprete del server
(`sys.executable`, dove `tools.find_module` ha trovato OpenMM e PDBFixer) da `JobContext.run`. La richiesta di CPU è
elastica, come per MMseqs2: al suo turno il processo prende da 1 a processori − 1 slot del budget e la piattaforma CPU
di OpenMM usa altrettanti thread. L'annullamento termina il processo (gruppo di processi) anche a metà di una
minimizzazione o di una dinamica; il timeout è quello dei job. Protocollo e fonti: `domain/refinement.py`.
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path
from typing import Callable, Optional

from ..core.errors import AppError, ToolUnavailable
from ..core.jobs import JobContext, default_env
from ..core.storage import atomic_write_json, atomic_write_text, read_json
from ..domain import refinement, structure
from . import tools

WORKER = Path(__file__).with_name("refine_worker.py")
INPUT = "input.pdb"
PARAMS = "refine_params.json"
RESULT = "refine_result.json"
MARKER = "@@BIOLOOM"
_PROGRESS = re.compile(r"progress\s+([0-9.]+)\s+(.*)$")


def missing() -> Optional[str]:
    """Messaggio se OpenMM o PDBFixer mancano (None se ci sono)."""
    return tools.missing_message(["openmm", "pdbfixer"], "Rifinitura energetica")


def run(pdb_path: Path, ctx: JobContext, workdir: Path, *, output: str, md_ps: float = 0.0,
        keep_hydrogens: bool = False, progress: Optional[Callable[[float, str], None]] = None) -> dict:
    """Prepara la copia pulita, esegue il processo di OpenMM e restituisce il suo risultato (energie, atomi aggiunti,
    piattaforma, tempi) con `input` (cosa è stato tolto o segnato nella copia) e `warnings`.

    `progress(frazione 0–1, fase)` riceve le fasi del processo (marcatori @@BIOLOOM)."""
    message = missing()
    if message:
        raise ToolUnavailable(message)
    try:
        md_ps = refinement.validate_md(md_ps)
    except ValueError as exc:
        raise AppError(f"Durata della dinamica non valida: {exc}.", code="invalid_refine")
    workdir.mkdir(parents=True, exist_ok=True)
    st = structure.load_structure(Path(pdb_path), Path(pdb_path).stem)
    try:
        text, info = refinement.clean_input(structure.first_model(st))
    except ValueError as exc:
        raise AppError(f"Struttura non rifinibile: {exc}.", code="invalid_refine")
    atomic_write_text(workdir / INPUT, text)
    warnings = input_warnings(info, models=len(st))
    params = {
        "input": INPUT, "output": output, "result": RESULT, "ph": refinement.PH, "md_ps": md_ps,
        "md_steps": refinement.md_steps(md_ps), "keep_hydrogens": bool(keep_hydrogens),
        "restraint_k": refinement.restraint_k_openmm(), "backbone": list(refinement.BACKBONE),
        "min_tolerance": refinement.MIN_TOLERANCE, "min_iterations": refinement.MIN_ITERATIONS,
        "md_temperature": refinement.MD_TEMPERATURE, "md_friction": refinement.MD_FRICTION,
        "md_timestep_fs": refinement.MD_TIMESTEP_FS, "md_seed": refinement.MD_SEED,
        "cutoff_atoms": refinement.CUTOFF_ATOMS, "cutoff_nm": refinement.CUTOFF_NM,
    }
    atomic_write_json(workdir / PARAMS, params)
    (workdir / RESULT).unlink(missing_ok=True)

    def on_line(line: str) -> None:
        if progress is None or not line.startswith(MARKER):
            return
        m = _PROGRESS.match(line[len(MARKER):].strip())
        if m:
            try:
                progress(max(0.0, min(1.0, float(m.group(1)))), m.group(2).strip())
            except ValueError:
                pass

    env = default_env()
    env["OMP_NUM_THREADS"] = "1"  # i thread li decide la piattaforma CPU di OpenMM (--threads), non OpenMP di numpy
    result = ctx.run(lambda threads: [sys.executable, str(WORKER), PARAMS, "--threads", str(threads)],
                     cwd=workdir, env=env, on_line=on_line, cpu=max(1, (os.cpu_count() or 2) - 1))
    data = read_json(workdir / RESULT)
    if result.returncode != 0 or not isinstance(data, dict) or not (workdir / output).is_file():
        tail = "\n".join(line for line in result.output.strip().splitlines() if not line.startswith(MARKER))
        tail = "\n".join(tail.splitlines()[-12:])
        raise AppError(f"La rifinitura con OpenMM non è riuscita (codice {result.returncode}).\n{tail}",
                       code="refine_error")
    if data.get("nonstandard"):
        warnings.append("Residui non standard sostituiti da PDBFixer: " + ", ".join(data["nonstandard"]) + ".")
    return {**data, "input": info, "warnings": warnings}


def input_warnings(info: dict, models: int = 1) -> list[str]:
    """Avvisi sulla copia pulita: modelli in più, gruppi eteroatomici esclusi, interruzioni della catena."""
    warnings = []
    if models > 1:
        warnings.append(f"La struttura ha {models} modelli: rifinito il primo.")
    if info["excluded"]:
        names = ", ".join(info["excluded"][:12]) + (f" e altri {len(info['excluded']) - 12}"
                                                    if len(info["excluded"]) > 12 else "")
        warnings.append(f"Gruppi eteroatomici esclusi (nessun parametro nel campo di forza): {names}. Il modello "
                        "rifinito contiene solo la proteina.")
    if info["breaks"]:
        where = ", ".join(f"{b['chain']}{b['after']}/{b['before']}" for b in info["breaks"][:8])
        warnings.append(f"Catena interrotta ({where}): nessun residuo mancante viene ricostruito e le estremità "
                        "dell'interruzione diventano estremità cariche.")
    if info["hydrogens_removed"]:
        warnings.append(f"{info['hydrogens_removed']} idrogeni del file sostituiti da quelli di PDBFixer a pH "
                        f"{refinement.PH:g}.")
    return warnings
