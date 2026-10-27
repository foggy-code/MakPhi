"""Mutagenesi in silico (F9.2): mutanti con MODELLER e, facoltativa, energia con OpenMM.

**MODELLER.** Uno script generato (`MUTATE_SCRIPT`, derivato da `mutate_model.py` della Sali lab; protocollo e fonti in
`domain/mutations.py`) costruisce per ogni variante (selvatico riottimizzato e mutante) `MODELS` modelli con semi
consecutivi e ne valuta il DOPE: totale, locale (residui entro 8 Å dai siti, coppie con almeno un atomo fra quei residui)
e, con più catene, fra le catene (per ogni gruppo di catene g: DOPE(g) + DOPE(resto) − DOPE(tutto), cioè le sole coppie
fra g e il resto, perché `assess_dope` su una selezione conta le coppie con almeno un atomo selezionato). I risultati
vanno in `mutate_models.json` (niente lettura del testo del log); i progressi con i marcatori `@@MAKPHI model k/n`. Lo
script legge la copia di `domain/mutations.modeller_input` (una catena per tratto continuo); i file scelti tornano ai
nomi originali delle catene con `restore_chains`. Un processo per variante della scansione: annullamento, timeout e
budget di CPU di `JobContext.run` (uno slot: MODELLER usa un thread).

**OpenMM** (`energy`): il selvatico e il mutante scelti passano dalla stessa minimizzazione con restrizioni della
rifinitura (`engines/refine.run`, protocollo in `domain/refinement.py`); poi i tripeptidi di riferimento attorno ai siti,
estratti dalle due strutture minimizzate (`domain/mutations.fragments_pdb`), con lo stesso protocollo. Quattro processi,
due alla volta; ognuno prende da 1 a processori − 1 slot del budget.
"""

from __future__ import annotations

from functools import partial
from pathlib import Path
from typing import Callable, Optional

from ..core.errors import AppError
from ..core.jobs import JobContext
from ..core.parallel import Steps
from ..core.storage import atomic_write_json, atomic_write_text, read_json
from ..domain import mutations, structure
from . import modeller, refine

INPUT = "mutate_input.pdb"
PARAMS = "mutate_params.json"
RESULT = "mutate_models.json"
SCRIPT = "mutate.py"

MUTATE_SCRIPT = r'''
# Script generato da MakPhi: mutagenesi in silico, derivato da mutate_model.py della Sali lab
# (Feyfant, Sali & Fiser, Protein Sci. 16:2030, 2007). Parametri in mutate_params.json.
import json, os, sys
from modeller import *
from modeller.optimizers import MolecularDynamics, ConjugateGradients
from modeller.automodel import autosched
from modeller.scripts import complete_pdb

log.none()


def mark(text):
    print("@@MAKPHI " + text, flush=True)


def optimize(atmsel, sched):
    # gradienti coniugati sul programma dei loop, breve ricottura, gradienti coniugati finali (come mutate_model.py)
    for step in sched:
        step.optimize(atmsel, max_iterations=200, min_atom_shift=0.001)
    refine(atmsel)
    cg = ConjugateGradients()
    cg.optimize(atmsel, max_iterations=200, min_atom_shift=0.001)


def refine(atmsel):
    md = MolecularDynamics(cap_atom_shift=0.39, md_time_step=4.0, md_return="FINAL")
    init_vel = True
    for its, equil, temps in ((200, 20, (150.0, 250.0, 400.0, 700.0, 1000.0)),
                              (200, 600, (1000.0, 800.0, 600.0, 500.0, 400.0, 300.0))):
        for temp in temps:
            md.optimize(atmsel, init_velocities=init_vel, temperature=temp, max_iterations=its, equilibrate=equil)
            init_vel = False


def make_restraints(mdl, aln):
    rsr = mdl.restraints
    rsr.clear()
    s = Selection(mdl)
    for typ in ("stereo", "phi-psi_binormal"):
        rsr.make(s, restraint_type=typ, aln=aln, spline_on_site=True)
    for typ in ("omega", "chi1", "chi2", "chi3", "chi4"):
        rsr.make(s, restraint_type=typ + "_dihedral", spline_range=4.0, spline_dx=0.3, spline_min_points=5,
                 aln=aln, spline_on_site=True)


def residue(mdl, chain, resnum):
    return mdl.chains[chain].residues[resnum]


def build(p, variant, seed, out):
    env = Environ(rand_seed=seed)
    env.io.hetatm = False
    env.edat.dynamic_sphere = False
    env.edat.dynamic_lennard = True
    env.edat.contact_shell = 4.0
    env.edat.update_dynamic = 0.39
    env.libs.topology.read(file="$(LIB)/top_heav.lib")
    env.libs.parameters.read(file="$(LIB)/par.lib")
    mdl1 = Model(env, file=p["input"])
    ali = Alignment(env)
    ali.append_model(mdl1, atom_files=p["input"], align_codes="input")
    for m in variant["mutations"]:
        Selection(residue(mdl1, m["chain"], m["resnum"])).mutate(residue_type=m["restyp"])
    ali.append_model(mdl1, align_codes="variant")
    mdl1.clear_topology()
    mdl1.generate_topology(ali[-1])
    mdl1.transfer_xyz(ali)
    mdl1.build(initialize_xyz=False, build_method="INTERNAL_COORDINATES")
    mdl2 = Model(env, file=p["input"])
    mdl1.res_num_from(mdl2, ali)
    mdl1.write(file=out + ".tmp")
    mdl1.read(file=out + ".tmp")
    os.remove(out + ".tmp")
    make_restraints(mdl1, ali)
    mdl1.env.edat.nonbonded_sel_atoms = 1
    sched = autosched.loop.make_for_model(mdl1)
    s = Selection(*[residue(mdl1, m["chain"], m["resnum"]) for m in variant["mutations"]])
    mdl1.restraints.unpick_all()
    mdl1.restraints.pick(s)
    s.energy()
    s.randomize_xyz(deviation=p["randomize"])
    # prima il solo residuo mutato, poi con l'intorno: catene laterali entro il raggio (estensione di MakPhi)
    mdl1.env.edat.nonbonded_sel_atoms = 2
    optimize(s, sched)
    mdl1.env.edat.nonbonded_sel_atoms = 1
    shell = (s.select_sphere(p["radius"]).by_residue().only_sidechain() | s) if p["radius"] > 0 else s
    mdl1.restraints.unpick_all()
    mdl1.restraints.pick(shell)
    optimize(shell, sched)
    mdl1.write(file=out)
    return len(shell)


def assess(p, file):
    env = Environ()
    env.libs.topology.read(file="$(LIB)/top_heav.lib")
    env.libs.parameters.read(file="$(LIB)/par.lib")
    mdl = complete_pdb(env, file, transfer_res_num=True)
    total = Selection(mdl).assess_dope()
    local = Selection(*[residue(mdl, c, r) for c, r in p["local"]]).assess_dope() if p["local"] else None
    groups = []
    if len(p["groups"]) > 1:
        for g in p["groups"]:
            inside = Selection(*[mdl.chains[c] for c in g])
            outside = Selection(*[ch for ch in mdl.chains if ch.name not in g])
            groups.append(inside.assess_dope() + outside.assess_dope() - total)
    return {"dope": total, "local": local, "interchain": groups}


p = json.load(open(sys.argv[1]))
total = len(p["variants"]) * len(p["seeds"])
done = 0
out = []
for variant in p["variants"]:
    for k, seed in enumerate(p["seeds"]):
        name = "%s_%d.pdb" % (variant["name"], k + 1)
        moved = build(p, variant, seed, name)
        row = assess(p, name)
        row.update({"variant": variant["name"], "role": variant["role"], "seed": seed, "file": name,
                    "moved_atoms": moved})
        out.append(row)
        done += 1
        mark("model %d/%d" % (done, total))
json.dump(out, open(p["result"], "w"), indent=1)
'''


def run(ctx: JobContext, workdir: Path, *, input_text: str, variants: list[dict], seeds: list[int],
        local: list[tuple[str, str]], groups: list[list[str]], radius: float = mutations.RADIUS,
        progress_range: tuple[float, float] = (0.0, 1.0)) -> list[dict]:
    """Costruisce e valuta i modelli delle varianti ({name, role, mutations [{chain, resnum, restyp}]}, catene di
    MODELLER) in `workdir`; restituisce una riga per modello (variant, role, seed, file, dope, local, interchain)."""
    workdir.mkdir(parents=True, exist_ok=True)
    atomic_write_text(workdir / INPUT, input_text)
    atomic_write_json(workdir / PARAMS, {
        "input": INPUT, "result": RESULT, "radius": float(radius), "randomize": mutations.RANDOMIZE,
        "seeds": [int(s) for s in seeds], "local": [list(x) for x in local], "groups": groups,
        "variants": variants,
    })
    (workdir / RESULT).unlink(missing_ok=True)
    modeller.run_script(ctx, MUTATE_SCRIPT, SCRIPT, workdir=workdir, args=[PARAMS], progress_range=progress_range)
    rows = read_json(workdir / RESULT)
    if not isinstance(rows, list) or not rows:
        raise AppError("MODELLER non ha prodotto il riepilogo dei mutanti (mutate_models.json).", code="modeller_error")
    for row in rows:
        if not (workdir / str(row.get("file") or "")).is_file():
            raise AppError(f"MODELLER non ha scritto il modello {row.get('file')}.", code="modeller_error")
    return rows


def best(rows: list[dict], role: str) -> dict:
    """Modello con il DOPE più basso fra quelli della variante `role` (wild_type | mutant)."""
    candidates = [r for r in rows if r.get("role") == role and isinstance(r.get("dope"), (int, float))]
    if not candidates:
        raise AppError(f"Nessun modello valido per {'il selvatico' if role == 'wild_type' else 'il mutante'}.",
                       code="modeller_error")
    return min(candidates, key=lambda r: float(r["dope"]))


def finalize(workdir: Path, rows: list[dict], chosen: dict[str, str], chains: dict[str, str]) -> list[str]:
    """Scrive i modelli scelti con i nomi finali ({ruolo: nome del file}) e i nomi originali delle catene, toglie gli
    altri modelli; restituisce le catene rinominate (nomi di più caratteri)."""
    renamed: list[str] = []
    picked = {role: best(rows, role)["file"] for role in chosen}
    for role, dest in chosen.items():
        text, ren = mutations.restore_chains((workdir / picked[role]).read_text(encoding="utf-8", errors="replace"),
                                             chains)
        renamed += ren
        atomic_write_text(workdir / dest, text)
    for row in rows:
        row["chosen"] = row["file"] in picked.values()
        if row["file"] not in chosen.values():
            (workdir / row["file"]).unlink(missing_ok=True)
    return sorted(set(renamed))


# ------------------------------------------------------------------------------------------ OpenMM
def energy(ctx: JobContext, workdir: Path, wild_type: Path, mutant: Path, sites: list[tuple[str, str]], *,
           names: tuple[str, str] = ("wt", "mut"),
           progress: Optional[Callable[[float, str], None]] = None) -> dict:
    """ΔΔE di OpenMM (domain/mutations.energy_terms): minimizzazione di selvatico e mutante con le restrizioni della
    rifinitura, poi dei frammenti di riferimento. Senza OpenMM o PDBFixer `available` è falso con il motivo; un errore
    di OpenMM è un avviso (`error`), non il fallimento del job: la stima resta con DOPE e indicatori strutturali."""
    missing = refine.missing()
    if missing:
        return {"available": False, "message": missing}
    workdir.mkdir(parents=True, exist_ok=True)

    def step(fraction: float, stage: str) -> None:
        if progress is not None:
            progress(fraction, stage)

    def minimize(path: Path, folder: str, output: str) -> dict:
        return refine.run(path, ctx.sub(folder, workdir / folder), workdir / folder, output=output)

    try:
        step(0.0, "Energia con OpenMM: minimizzazione di selvatico e mutante")
        outputs = {"wild_type": f"{names[0]}_min.pdb", "mutant": f"{names[1]}_min.pdb"}
        with Steps(check=ctx.check, parallel=True, workers=2, name="energia") as steps:
            steps.start("wild_type", partial(minimize, wild_type, "openmm_selvatico", outputs["wild_type"]))
            steps.start("mutant", partial(minimize, mutant, "openmm_mutante", outputs["mutant"]))
            full = {k: steps.result(k) for k in ("wild_type", "mutant")}
        step(0.6, "Energia con OpenMM: frammenti di riferimento")
        frags = {}
        for key, folder in (("wild_type", "openmm_selvatico"), ("mutant", "openmm_mutante")):
            model = structure.first_model(structure.load_structure(workdir / folder / outputs[key], key))
            path = workdir / f"frammenti_{'selvatico' if key == 'wild_type' else 'mutante'}.pdb"
            atomic_write_text(path, mutations.fragments_pdb(model, sites))
            frags[key] = path
        with Steps(check=ctx.check, parallel=True, workers=2, name="frammenti") as steps:
            steps.start("wild_type", partial(minimize, frags["wild_type"], "openmm_rif_selvatico", "rif_min.pdb"))
            steps.start("mutant", partial(minimize, frags["mutant"], "openmm_rif_mutante", "rif_min.pdb"))
            ref = {k: steps.result(k) for k in ("wild_type", "mutant")}
    except AppError as exc:
        return {"available": True, "error": exc.message.splitlines()[0]}

    def total(data: dict) -> Optional[float]:
        value = ((data.get("energy") or {}).get("final") or {}).get("total")
        return float(value) if isinstance(value, (int, float)) else None

    terms = mutations.energy_terms(total(full["wild_type"]), total(full["mutant"]), total(ref["wild_type"]),
                                   total(ref["mutant"]))
    warnings = list(dict.fromkeys(w for d in full.values() for w in d.get("warnings") or []))
    step(1.0, "Energia con OpenMM completata")
    return {"available": True, **terms, "openmm": full["mutant"].get("openmm"),
            "platform": full["mutant"].get("platform"),
            "files": {"wild_type": f"openmm_selvatico/{outputs['wild_type']}",
                      "mutant": f"openmm_mutante/{outputs['mutant']}"},
            "fragments": len(mutations.fragment_windows(
                structure.first_model(structure.load_structure(wild_type, "wt")), sites)),
            "protocol": "minimizzazione con restrizioni della rifinitura (AMBER ff14SB, OBC2, catena principale "
                        "trattenuta a 10 kcal·mol⁻¹·Å⁻²), poi la stessa sui tripeptidi i−1..i+1 isolati",
            "warnings": warnings}
