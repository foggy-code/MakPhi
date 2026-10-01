"""fpocket (F7b): tasche e cavità di una struttura, con i descrittori e il punteggio di druggability.

fpocket gira nella cartella del passo su una copia pulita della struttura (`structure.pdb`: primo modello, prima
altloc, solo la proteina, niente acque, ligandi e ioni, scritta da Biopython con catene e numerazione originali, così i
residui delle tasche corrispondono a quelli del viewer). Una tasca occupata da un ligando nel file originale si trova
vuota, come serve per cercare siti di legame. Il processo passa da `JobContext.run` (un thread, uno slot del budget di
CPU, annullamento e timeout); la lettura dell'uscita è in `domain/pockets.py`. Si conserva la cartella
`structure_out/` di fpocket (PDB con le sfere, script PyMOL e VMD, file per tasca), utile per riaprire il risultato.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from ..core.errors import AppError, ToolUnavailable
from ..core.jobs import JobContext
from ..domain import pockets as parser
from ..domain import structure
from . import tools

INPUT = "structure.pdb"
OUT_DIR = "structure_out"
TIMEOUT = 900  # s: fpocket su 1UBQ impiega meno di un secondo, su complessi grandi pochi secondi


HETERO_NOTE = "le tasche che occupano risultano vuote"


def clean_copy(pdb_path: Path, dest: Path, hetero_note: str = HETERO_NOTE) -> tuple[dict, list[str]]:
    """Copia pulita della struttura (solo proteina) e avvisi: modelli in più ignorati, gruppi eteroatomici esclusi (con
    `hetero_note`, la conseguenza per il calcolo che usa la copia)."""
    st = structure.load_structure(pdb_path, pdb_path.stem)
    model = structure.first_model(st)
    stats = structure.structure_stats(model)
    if stats["residues"] == 0:
        raise AppError("La struttura non contiene residui amminoacidici con C-alfa.")
    warnings: list[str] = []
    if len(st) > 1:
        warnings.append(f"La struttura ha {len(st)} modelli: analizzato il primo.")
    hetero = [g for g in structure.hetero_groups(model)]
    if hetero:
        names = ", ".join(f"{g['resname']} {g['chain']}{g['resnum']}" for g in hetero[:12])
        more = f" e altri {len(hetero) - 12}" if len(hetero) > 12 else ""
        warnings.append(f"Gruppi eteroatomici esclusi dal calcolo ({names}{more}): {hetero_note}.")
    structure.write_pdb(st, dest, keep_hetero=False, keep_water=False)
    return {**stats, "chains_detail": structure.chain_summaries(model)}, warnings


def run(pdb_path: Path, ctx: JobContext, workdir: Path) -> dict:
    """Esegue fpocket con i parametri predefiniti e restituisce tasche (ordinate per druggability), riepilogo, avvisi e
    file prodotti."""
    exe = tools.find("fpocket")
    if not exe:
        raise ToolUnavailable(tools.missing_message(["fpocket"], "Ricerca delle tasche")
                              or "fpocket non è installato.")
    workdir.mkdir(parents=True, exist_ok=True)
    shutil.rmtree(workdir / OUT_DIR, ignore_errors=True)
    stats, warnings = clean_copy(Path(pdb_path), workdir / INPUT)
    result = ctx.run([exe, "-f", INPUT], cwd=workdir, log_output=False, timeout=TIMEOUT)
    out_dir = workdir / OUT_DIR
    name = Path(INPUT).stem
    (workdir / "gmon.out").unlink(missing_ok=True)  # profilazione di build non ufficiali di fpocket
    if not (out_dir / f"{name}_info.txt").is_file():
        tail = "\n".join(result.output.strip().splitlines()[-8:])
        raise AppError(f"fpocket non ha prodotto il riepilogo delle tasche (codice {result.returncode}).\n{tail}",
                       code="fpocket_error")
    data = parser.read_output(out_dir, name)
    files = sorted(p.relative_to(workdir).as_posix() for p in out_dir.rglob("*") if p.is_file())
    return {**data, "structure": stats, "warnings": warnings, "files": files,
            "params": {"program": "fpocket", "options": "predefinite (sfere alfa 3,4–6,2 Å, almeno 15 per tasca)",
                       "druggable_threshold": parser.DRUGGABLE},
            "pdb": f"{OUT_DIR}/{name}_out.pdb"}
