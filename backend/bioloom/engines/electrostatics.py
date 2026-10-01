"""Elettrostatica (F7b): PDB2PQR con PROPKA → file PQR → APBS → mappa OpenDX → potenziale di superficie per residuo.

Parametri, griglia e campionamento con le loro fonti sono in `domain/electrostatics.py`. Qui i due processi, entrambi
da `JobContext.run` (uno slot del budget di CPU ciascuno: PDB2PQR è Python a un thread, il binario di APBS 3.4.1 non usa
OpenMP; annullamento e timeout), ognuno con il proprio contesto figlio per il prefisso del log.

**File conservati** nella cartella del passo: `structure.pdb` (copia pulita di `pockets.clean_copy`: primo modello,
prima altloc, solo la proteina con i residui modificati della catena, MSE come MET; una catena senza identificativo vi
prende una lettera libera, che il risultato riporta al nome originale), `structure.pqr` (cariche e raggi), `apbs.in`
(input completo, per ripetere il calcolo), `potential.dx`
(mappa del potenziale in kT/e, scaricabile e leggibile da PyMOL, Chimera, VMD; circa 13 byte per punto: 16 MB per 1UBQ,
al massimo circa 30 MB con il tetto della griglia) e i log dei due programmi.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Callable, Optional

from ..core.errors import AppError, ToolUnavailable
from ..core.jobs import JobContext
from ..domain import electrostatics as es
from ..domain import structure
from . import pockets, tools

INPUT = "structure.pdb"
PQR = "structure.pqr"
APBS_INPUT = "apbs.in"
DX_STEM = "potential"
TIMEOUT = 1800  # s, per processo
# PDB2PQR non ha parametri per ligandi e ioni senza un file mol2: il calcolo riguarda la sola proteina
HETERO_NOTE = ("PDB2PQR non ha parametri per loro, quindi le loro cariche mancano dal potenziale (un metallo o un "
               "cofattore carico lo cambia vicino al suo sito)")


def _tail(output: str, lines: int = 10) -> str:
    return "\n".join(output.strip().splitlines()[-lines:])


def run(pdb_path: Path, ctx: JobContext, workdir: Path, *, forcefield: str = es.DEFAULT_FORCEFIELD,
        ph: float = es.DEFAULT_PH, progress: Optional[Callable[[float, str], None]] = None) -> dict:
    """Calcolo completo; `progress(frazione 0–1, fase)` facoltativo. Restituisce carica, griglia, parametri, potenziale
    per residuo (residui della struttura originale, nel suo ordine), riepilogo, avvisi, file e tempi."""
    missing = tools.missing_message(["pdb2pqr", "apbs"], "Calcolo elettrostatico")
    if missing:
        raise ToolUnavailable(missing)
    forcefield = forcefield.upper()
    if forcefield not in es.FORCEFIELDS:
        raise AppError(f"Campo di forza non supportato: {forcefield} (ammessi: {', '.join(es.FORCEFIELDS)}).",
                       code="invalid_forcefield")
    step = progress or (lambda _f, _s: None)
    timings: dict[str, float] = {}
    workdir.mkdir(parents=True, exist_ok=True)
    started = time.perf_counter()
    stats, warnings = pockets.clean_copy(Path(pdb_path), workdir / INPUT, HETERO_NOTE)
    st = structure.load_structure(workdir / INPUT, "clean")
    model = structure.first_model(st)
    # catena senza identificativo: PDB2PQR (--keep-chain) la scrive come A e i residui del PQR non si ritroverebbero.
    # La copia la nomina con una lettera libera; il risultato riporta il nome originale (`original`).
    original = structure.name_blank_chains(model)
    if original:
        structure.write_pdb(st, workdir / INPUT, keep_hetero=True)

    def chain_of(chain: str) -> str:
        return original.get(chain, chain)

    names = {(c.id, structure._resid(r)): r.get_resname().strip() for c in model for r in c}

    step(0.05, f"Cariche e raggi ({forcefield}) e protonazione a pH {ph:g} con PROPKA (PDB2PQR)")
    t = time.perf_counter()
    cmd = [tools.find("pdb2pqr") or "pdb2pqr", f"--ff={forcefield}", "--titration-state-method=propka",
           f"--with-ph={ph:g}", "--keep-chain", "--drop-water", INPUT, PQR]
    result = ctx.sub("PDB2PQR", workdir).run(cmd, cwd=workdir, log_output=False, timeout=TIMEOUT)
    (workdir / "pdb2pqr.log").write_text(result.output, encoding="utf-8")
    (workdir / f"{Path(PQR).stem}.log").unlink(missing_ok=True)  # log di PDB2PQR accanto al PQR: già in pdb2pqr.log
    if result.returncode != 0 or not (workdir / PQR).is_file():
        raise AppError(f"PDB2PQR è terminato con errore (codice {result.returncode}).\n{_tail(result.output)}",
                       code="pdb2pqr_error")
    atoms = es.parse_pqr((workdir / PQR).read_text(encoding="utf-8", errors="replace"))
    if not atoms:
        raise AppError("PDB2PQR non ha scritto atomi nel file PQR.", code="pdb2pqr_error")
    charges = es.charge_summary(atoms, names)
    for row in charges["charged"]:
        row["chain"] = chain_of(row["chain"])
    ctx.log(f"PDB2PQR: {len(atoms)} atomi, carica netta {charges['net_charge']:+.2f} a pH {ph:g}")
    # residui modificati della catena tenuti nella copia (SEP, TPO…; le MSE sono già MET): PDB2PQR non ha i loro
    # parametri e li toglie dal PQR senza errore
    in_pqr = {(a.chain, a.resnum) for a in atoms}
    dropped = [f"{r.get_resname().strip()} {chain_of(c.id)}{structure._resid(r)}" for c in model for r in c
               if r.id[0] != " " and (c.id, structure._resid(r)) not in in_pqr]
    if dropped:
        warnings.append(f"Residui modificati della catena senza parametri in PDB2PQR ({', '.join(dropped[:12])}"
                        + (f" e altri {len(dropped) - 12}" if len(dropped) > 12 else "")
                        + "): le loro cariche mancano dal potenziale e non hanno un valore per residuo.")
    timings["pdb2pqr"] = time.perf_counter() - t

    grid = es.grid_parameters(atoms)
    if grid["coarsened"]:
        warnings.append(f"Molecola grande: passo della griglia fine portato a {max(grid['spacing']):.2f} Å (invece di "
                        f"{es.SPACE:g} Å) per restare entro {es.GMEMCEIL_MB} MB stimati.")
    (workdir / APBS_INPUT).write_text(es.apbs_input(PQR, grid, DX_STEM), encoding="utf-8")
    step(0.35, "Equazione di Poisson-Boltzmann (APBS, griglia {} × {} × {})".format(*grid["dime"]))
    t = time.perf_counter()
    result = ctx.sub("APBS", workdir).run([tools.find("apbs") or "apbs", APBS_INPUT], cwd=workdir, log_output=False,
                                          timeout=TIMEOUT)
    (workdir / "apbs.log").write_text(result.output, encoding="utf-8")
    (workdir / "io.mc").unlink(missing_ok=True)  # log interno di APBS, già in apbs.log
    dx = workdir / f"{DX_STEM}.dx"
    if result.returncode != 0 or not dx.is_file():
        raise AppError(f"APBS è terminato con errore (codice {result.returncode}).\n{_tail(result.output)}",
                       code="apbs_error")
    timings["apbs"] = time.perf_counter() - t

    step(0.8, "Potenziale sulla superficie accessibile per residuo")
    t = time.perf_counter()
    try:
        potential = es.parse_dx(dx.read_text(encoding="utf-8", errors="replace"))
    except ValueError as exc:
        raise AppError(f"Mappa del potenziale non leggibile: {exc}.", code="apbs_error")
    sampled = es.residue_potentials(atoms, potential)
    qres = es.residue_charges(atoms)
    residues = []
    for chain in model:
        for res in chain:
            if not structure.is_amino_residue(res):
                continue
            key = (chain.id, structure._resid(res))
            s = sampled.get(key) or {"potential": None, "exposed": 0, "points": 0}
            residues.append({"chain": chain_of(chain.id), "resnum": key[1], "resname": res.get_resname().strip(),
                             "potential": s["potential"], "exposed_atoms": s["exposed"], "points": s["points"],
                             "charge": qres.get(key)})
    timings["superficie"] = time.perf_counter() - t
    values = potential.values
    total = time.perf_counter() - started
    ctx.log("Elettrostatica in {:.2f} s ({} s)".format(total, ", ".join(f"{k} {v:.2f}" for k, v in timings.items())))
    return {
        "forcefield": forcefield, "ph": ph, "charges": charges, "net_charge": charges["net_charge"],
        "grid": grid, "parameters": es.APBS_PARAMETERS,
        "sampling": {"surface": "accessibile al solvente (sonda 1,4 Å)", "probe": es.PROBE,
                     "points_per_atom": es.SPHERE_POINTS, "residue_value": "media sugli atomi esposti"},
        "scale": {"unit": "kT/e", "limit": es.SCALE_LIMIT, "mv_per_unit": es.KT_E_MV},
        "map": {"file": f"{DX_STEM}.dx", "min": round(float(values.min()), 2), "max": round(float(values.max()), 2),
                "points": int(values.size)},
        "residues": residues, "summary": es.summarize(residues), "structure": stats, "warnings": warnings,
        "files": [INPUT, PQR, APBS_INPUT, f"{DX_STEM}.dx", "pdb2pqr.log", "apbs.log"],
        "timings": {k: round(v, 2) for k, v in timings.items()},
    }
