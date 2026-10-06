"""fpocket (F7b): tasche e cavità di una struttura, con i descrittori e il punteggio di druggability.

fpocket gira nella cartella del passo su una copia pulita della struttura (`structure.pdb`: primo modello, prima
altloc, solo la proteina con i suoi residui modificati, MSE come MET, niente acque, ligandi e ioni, scritta da Biopython
con catene e numerazione originali, così i residui delle tasche corrispondono a quelli del viewer; `clean_copy`). Una
tasca occupata da un ligando nel file originale si trova
vuota, come serve per cercare siti di legame. Il processo passa da `JobContext.run` (un thread, uno slot del budget di
CPU, annullamento e timeout); la lettura dell'uscita è in `domain/pockets.py`. Si conserva la cartella
`structure_out/` di fpocket (PDB con le sfere, script PyMOL e VMD, file per tasca), utile per riaprire il risultato.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from ..core.errors import AppError, ToolUnavailable
from ..core.jobs import JobContext
from ..core.storage import atomic_write_text
from ..domain import ligands
from ..domain import pockets as parser
from ..domain import structure
from . import tools

INPUT = "structure.pdb"
OUT_DIR = "structure_out"
TIMEOUT = 900  # s: fpocket su 1UBQ impiega meno di un secondo, su complessi grandi pochi secondi


HETERO_NOTE = "le tasche che occupano risultano vuote"


def clean_copy(pdb_path: Path, dest: Path, hetero_note: str = HETERO_NOTE) -> tuple[dict, list[str]]:
    """Copia pulita della struttura (la proteina) e avvisi: modelli in più ignorati, gruppi eteroatomici esclusi (con
    `hetero_note`, la conseguenza per il calcolo che usa la copia).

    I residui modificati della catena scritti come HETATM (MSE, SEP, TPO…: `ligands.is_modified_residue`, cioè N, CA
    e C legati con un legame peptidico a un vicino, cofattori esclusi) restano nella copia: sono proteina, e toglierli
    lascerebbe nella catena un buco che fpocket riporterebbe come tasca. Un amminoacido libero legato come substrato
    (HETATM senza legame peptidico) resta invece fuori, come gli altri ligandi. Le selenometionine diventano metionine
    (MSE → MET, SE → SD, come nella preparazione dei template): PDB2PQR ha i parametri della metionina, non della MSE.
    I conteggi (`structure_stats`, catene) sono quelli della copia, selenometionine comprese."""
    st = structure.load_structure(pdb_path, pdb_path.stem)
    model = structure.first_model(st)
    warnings: list[str] = []
    if len(st) > 1:
        warnings.append(f"La struttura ha {len(st)} modelli: analizzato il primo.")
    modified = [(c.id, r) for c in model for r in c if ligands.is_hetero(r) and ligands.is_modified_residue(r)]
    if not modified and structure.structure_stats(model)["residues"] == 0:  # prima di scrivere una copia vuota
        raise AppError("La struttura non contiene residui amminoacidici con C-alfa.")
    keep = {(chain, r.id) for chain, r in modified}
    in_chain = {(chain, structure._resid(r)) for chain, r in modified}
    hetero = [g for g in structure.hetero_groups(model) if (g["chain"], g["resnum"]) not in in_chain]
    if hetero:
        names = ", ".join(f"{g['resname']} {g['chain']}{g['resnum']}" for g in hetero[:12])
        more = f" e altri {len(hetero) - 12}" if len(hetero) > 12 else ""
        warnings.append(f"Gruppi eteroatomici esclusi dal calcolo ({names}{more}): {hetero_note}.")
    structure.write_pdb(st, dest, keep_hetero=keep or False, keep_water=False)
    text, converted = structure.selenomethionine_as_methionine(dest.read_text(encoding="utf-8"))
    if converted:
        atomic_write_text(dest, text)
    clean = structure.first_model(structure.load_structure(dest, "clean"))
    stats = structure.structure_stats(clean)
    if stats["residues"] == 0:
        raise AppError("La struttura non contiene residui amminoacidici con C-alfa.")
    return {**stats, "chains_detail": structure.chain_summaries(clean)}, warnings


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
