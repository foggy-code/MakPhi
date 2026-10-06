"""Rifinitura energetica con OpenMM (F9.1): processo separato, lanciato da `engines/refine.py`.

Si esegue come script (`python refine_worker.py <parametri.json> --threads N`), non come modulo di BioLoom: importa
solo OpenMM, PDBFixer e la libreria standard, così un errore nativo di OpenMM non tocca il server e l'annullamento del
job lo termina (gruppo di processi, `core/jobs`). Il protocollo e le sue fonti sono in `domain/refinement.py`, che
passa qui i parametri; il numero di thread della piattaforma CPU lo decide il budget di CPU del JobManager (richiesta
elastica, come MMseqs2).

Parametri (JSON): input, output, result, ph, md_ps, md_steps, keep_hydrogens, restraint_k (kJ/mol/nm²), backbone,
min_tolerance, min_iterations, md_temperature, md_friction, md_timestep_fs, md_seed, cutoff_atoms, cutoff_nm.
Uscita: il PDB rifinito e un JSON con energie, atomi aggiunti, piattaforma e tempi. Il progresso arriva al server con
righe `@@BIOLOOM progress <frazione> <fase>`.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time

MARKER = "@@BIOLOOM"
# gruppi di forze: le componenti dell'energia si leggono separatamente; 1 = restrizioni (fuori dal totale)
GROUPS = {"HarmonicBondForce": 2, "HarmonicAngleForce": 3, "PeriodicTorsionForce": 4, "NonbondedForce": 5,
          "GBSAOBCForce": 6}
COMPONENTS = {"bonds": 2, "angles": 3, "torsions": 4, "nonbonded": 5, "solvation": 6}
RESTRAINT_GROUP = 1


def say(fraction: float, stage: str) -> None:
    print(f"{MARKER} progress {fraction:.3f} {stage}", flush=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("params")
    parser.add_argument("--threads", type=int, default=1)
    args = parser.parse_args()
    with open(args.params, encoding="utf-8") as fh:
        p = json.load(fh)
    timings: dict[str, float] = {}
    t0 = time.perf_counter()

    import random

    import openmm
    from openmm import app, unit
    from openmm.app.internal.customgbforces import GBSAOBC2Force
    from pdbfixer import PDBFixer

    # ------------------------------------------------------------------ PDBFixer
    say(0.02, "Atomi mancanti e idrogeni (PDBFixer)")
    # Riproducibilità (F9.4): Modeller.addHydrogens di OpenMM mette gli idrogeni in posizioni casuali (modulo random)
    # prima di minimizzarli, e PDBFixer minimizza gli atomi aggiunti con un integratore con seme casuale. Senza seme due
    # esecuzioni dello stesso job davano energie iniziali diverse (misurato su 1UBQ: 850 e −201 kJ/mol) e catene laterali
    # diverse; con lo stesso seme della dinamica (`md_seed`) il risultato dipende solo dall'ingresso e dalla piattaforma.
    random.seed(int(p["md_seed"]))
    fixer = PDBFixer(filename=p["input"])
    fixer.findMissingResidues()
    fixer.missingResidues = {}  # nessun residuo mancante ricostruito: le interruzioni restano
    fixer.findNonstandardResidues()
    nonstandard = [f"{res.name} {res.chain.id}{res.id} → {new}" for res, new in fixer.nonstandardResidues]
    fixer.replaceNonstandardResidues()
    fixer.findMissingAtoms()
    heavy_added = sum(len(v) for v in fixer.missingAtoms.values())
    terminal_added = sum(len(v) for v in fixer.missingTerminals.values())
    try:
        fixer.addMissingAtoms(seed=int(p["md_seed"]))
    except TypeError:  # PDBFixer precedente alla 1.8, senza il parametro
        fixer.addMissingAtoms()
    before_h = fixer.topology.getNumAtoms()
    fixer.addMissingHydrogens(float(p["ph"]))
    topology, positions = fixer.topology, fixer.positions
    n_atoms = topology.getNumAtoms()
    print(f"PDBFixer: {heavy_added} atomi pesanti e {terminal_added} atomi terminali aggiunti, "
          f"{n_atoms - before_h} idrogeni a pH {p['ph']:g}; {n_atoms} atomi", flush=True)
    timings["pdbfixer"] = time.perf_counter() - t0

    # ------------------------------------------------------------------ sistema
    say(0.08, "Campo di forza AMBER ff14SB con solvente implicito OBC2")
    t = time.perf_counter()
    cutoff = n_atoms > int(p["cutoff_atoms"])
    forcefield = app.ForceField("amber14-all.xml")
    if cutoff:
        system = forcefield.createSystem(topology, nonbondedMethod=app.CutoffNonPeriodic,
                                         nonbondedCutoff=float(p["cutoff_nm"]) * unit.nanometer, constraints=app.HBonds)
    else:
        system = forcefield.createSystem(topology, nonbondedMethod=app.NoCutoff, constraints=app.HBonds)
    nonbonded = next(f for f in system.getForces() if isinstance(f, openmm.NonbondedForce))
    nonbonded.setReactionFieldDielectric(1.0)  # come gli script implicit/*.xml di OpenMM
    gb = openmm.GBSAOBCForce()
    gb.setSoluteDielectric(1.0)
    gb.setSolventDielectric(78.5)
    if cutoff:
        gb.setNonbondedMethod(openmm.GBSAOBCForce.CutoffNonPeriodic)
        gb.setCutoffDistance(float(p["cutoff_nm"]) * unit.nanometer)
    else:
        gb.setNonbondedMethod(openmm.GBSAOBCForce.NoCutoff)
    # raggi mbondi2 e fattori di scala di OBC2, gli stessi di implicit/obc2.xml
    for i, (radius, scale) in enumerate(GBSAOBC2Force.getStandardParameters(topology)):
        gb.addParticle(nonbonded.getParticleParameters(i)[0], float(radius), float(scale))
    system.addForce(gb)
    for force in system.getForces():
        force.setForceGroup(GROUPS.get(type(force).__name__, 0))
    restraint = openmm.CustomExternalForce("k_restr*((x-x0)^2+(y-y0)^2+(z-z0)^2)")
    restraint.addGlobalParameter("k_restr", float(p["restraint_k"]))
    for name in ("x0", "y0", "z0"):
        restraint.addPerParticleParameter(name)
    backbone = set(p["backbone"])
    restrained = 0
    for atom in topology.atoms():
        if atom.name in backbone and atom.element is not None and atom.element.symbol != "H":
            restraint.addParticle(atom.index, positions[atom.index].value_in_unit(unit.nanometer))
            restrained += 1
    restraint.setForceGroup(RESTRAINT_GROUP)
    system.addForce(restraint)

    integrator = openmm.LangevinMiddleIntegrator(float(p["md_temperature"]) * unit.kelvin,
                                                 float(p["md_friction"]) / unit.picosecond,
                                                 float(p["md_timestep_fs"]) * unit.femtoseconds)
    integrator.setRandomNumberSeed(int(p["md_seed"]))
    threads = max(1, int(args.threads))
    try:
        platform = openmm.Platform.getPlatformByName("CPU")
        context = openmm.Context(system, integrator, platform, {"Threads": str(threads)})
    except Exception as exc:  # piattaforma CPU non disponibile: Reference, corretta ma lenta
        print(f"Piattaforma CPU non disponibile ({exc}): uso Reference", flush=True)
        platform = openmm.Platform.getPlatformByName("Reference")
        context = openmm.Context(system, integrator, platform)
    context.setPositions(positions)
    timings["sistema"] = time.perf_counter() - t

    kj = unit.kilojoule_per_mole

    def energies() -> dict:
        out = {name: round(context.getState(getEnergy=True, groups={g}).getPotentialEnergy().value_in_unit(kj), 2)
               for name, g in COMPONENTS.items()}
        total = context.getState(getEnergy=True, groups=set(COMPONENTS.values())).getPotentialEnergy()
        out["total"] = round(total.value_in_unit(kj), 2)
        return out

    def restraint_energy() -> float:
        e = context.getState(getEnergy=True, groups={RESTRAINT_GROUP}).getPotentialEnergy()
        return round(e.value_in_unit(kj), 3)

    energy = {"before": energies()}
    print(f"Energia iniziale {energy['before']['total']:.1f} kJ/mol", flush=True)

    def minimize(stage: str, fraction: float) -> None:
        say(fraction, stage)
        openmm.LocalEnergyMinimizer.minimize(context, float(p["min_tolerance"]), int(p["min_iterations"]))

    t = time.perf_counter()
    minimize("Minimizzazione con restrizioni sulla catena principale", 0.12)
    energy["after_minimization"] = energies()
    timings["minimizzazione"] = time.perf_counter() - t
    print(f"Energia dopo la minimizzazione {energy['after_minimization']['total']:.1f} kJ/mol "
          f"(restrizioni {restraint_energy():.2f})", flush=True)

    steps = int(p.get("md_steps") or 0)
    if steps > 0:
        t = time.perf_counter()
        context.setVelocitiesToTemperature(float(p["md_temperature"]) * unit.kelvin, int(p["md_seed"]))
        chunk = max(1, steps // 20)
        done = 0
        say(0.4, f"Dinamica molecolare: {p['md_ps']:g} ps a {p['md_temperature']:g} K")
        while done < steps:
            n = min(chunk, steps - done)
            integrator.step(n)
            done += n
            say(0.4 + 0.45 * done / steps,
                f"Dinamica molecolare: {done * float(p['md_timestep_fs']) / 1000:.1f} di {p['md_ps']:g} ps")
        energy["after_md"] = energies()
        timings["dinamica"] = time.perf_counter() - t
        t = time.perf_counter()
        minimize("Minimizzazione finale", 0.88)
        timings["minimizzazione finale"] = time.perf_counter() - t
    energy["final"] = energies()
    energy["restraint_final"] = restraint_energy()
    print(f"Energia finale {energy['final']['total']:.1f} kJ/mol", flush=True)

    # ------------------------------------------------------------------ uscita
    say(0.95, "Scrittura del modello rifinito")
    final = context.getState(getPositions=True).getPositions()
    out_topology, out_positions = topology, final
    if not p.get("keep_hydrogens"):
        modeller = app.Modeller(topology, final)
        modeller.delete([a for a in topology.atoms() if a.element is not None and a.element.symbol == "H"])
        out_topology, out_positions = modeller.topology, modeller.positions
    with open(p["output"], "w", encoding="utf-8") as fh:
        app.PDBFile.writeFile(out_topology, out_positions, fh, keepIds=True)
    result = {
        "openmm": openmm.__version__, "platform": platform.getName(), "threads": threads if platform.getName() == "CPU"
        else 1, "atoms": n_atoms, "heavy_atoms_added": heavy_added, "terminal_atoms_added": terminal_added,
        "hydrogens_added": n_atoms - before_h, "nonstandard": nonstandard, "restrained_atoms": restrained,
        "nonbonded": f"CutoffNonPeriodic {float(p['cutoff_nm']):g} nm" if cutoff else "NoCutoff",
        "energy": energy, "md_steps": steps, "timings": {k: round(v, 2) for k, v in timings.items()},
        "output": os.path.basename(p["output"]),
    }
    with open(p["result"], "w", encoding="utf-8") as fh:
        json.dump(result, fh, indent=1)
    say(1.0, "Rifinitura completata")
    return 0


if __name__ == "__main__":
    sys.exit(main())
