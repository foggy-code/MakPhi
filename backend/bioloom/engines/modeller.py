"""Adattatore MODELLER: preparazione dei template, generazione degli script, esecuzione in sottoprocesso.

MODELLER viene sempre eseguito in un processo separato con l'interprete configurato: il server non
importa mai `modeller`, quindi può girare su qualunque Python e un crash di MODELLER non lo abbatte.
Gli script scrivono i risultati in JSON (`models.json`, `dope.json`) invece di affidarsi al parsing
del testo di log.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from ..core.errors import AppError, ToolUnavailable
from ..core.jobs import JobContext, default_env
from ..core.storage import atomic_write_text, read_json
from ..domain import plddt, sequences, structure
from ..domain.sequences import PirEntry
from . import tools

MARKER = "@@BIOLOOM"
_RESIDUE_ID = re.compile(r"^-?\d+[A-Za-z]?:[A-Za-z0-9]$")
_ATOM_ID = re.compile(r"^[A-Za-z0-9']{1,4}:-?\d+[A-Za-z]?:[A-Za-z0-9]$")
REFINEMENTS = ("very_fast", "fast", "normal", "thorough")
LOOP_LEVELS = {"very_fast": "refine.very_fast", "fast": "refine.fast", "slow": "refine.slow"}


@dataclass
class TemplateSpec:
    id: str
    chains: list[str] = field(default_factory=list)
    hetatm: bool = False
    # Template predetto (AlphaFold): si tolgono i residui con pLDDT (colonna B-factor) sotto la soglia; None = no
    plddt_min: Optional[float] = None


@dataclass
class Restraint:
    type: str  # helix | strand | distance
    start: str = ""
    end: str = ""
    atom1: str = ""
    atom2: str = ""
    mean: float = 0.0
    stdev: float = 0.1


@dataclass
class ModelOptions:
    models: int = 5
    refinement: str = "normal"
    loop_refinement: bool = False
    loop_models: int = 4
    loop_level: str = "fast"
    loop_regions: list[tuple[str, str]] = field(default_factory=list)
    restraints: list[Restraint] = field(default_factory=list)
    hetatm: bool = False
    seed: int = -8123
    # Solo per i template AlphaFold: ritaglio dei residui con pLDDT < plddt_min (domain/plddt.py)
    plddt_trim: bool = True
    plddt_min: float = plddt.DEFAULT_THRESHOLD


def modeller_python(ctx: JobContext) -> str:
    info = tools.detect_modeller(ctx.settings.modeller_python)
    if not info["available"]:
        raise ToolUnavailable(
            "MODELLER non è disponibile. Indica nelle Impostazioni un interprete Python con MODELLER installato "
            "(licenza gratuita per uso accademico: https://salilab.org/modeller/).",
            details=info.get("attempts"))
    return info["python"]


# --------------------------------------------------------------------------------- preparazione template
def prepare_template(source: Path, spec: TemplateSpec, dest_dir: Path) -> dict:
    """Converte il template in PDB pulito (catene scelte, ligandi opzionali, MSE→MET) nella cartella del job.

    Con `spec.plddt_min` (template AlphaFold) toglie i residui con pLDDT sotto la soglia e i frammenti isolati che
    ne restano (`plddt.low_confidence`): il template preparato, la sua sequenza e il primo/ultimo residuo sono quelli
    ritagliati, e il risultato riporta in `plddt` quanti residui sono stati tolti (per soglia e per frammento) e dove.
    `_residues` (chiave interna, da non salvare nei risultati) elenca i residui del template completo con l'esito,
    per riportare sul ritaglio un PIR scritto sul template completo. La copia resta riconoscibile come il file
    d'origine (`_provenance_header`): modello AlphaFold oppure struttura con il suo metodo (EXPDTA).
    """
    st = structure.load_structure(source, spec.id)
    model = structure.first_model(st)
    summaries = {c["chain"]: c for c in structure.chain_summaries(model)}
    if not summaries:
        raise AppError(f"Il template {spec.id} non contiene catene proteiche.")
    order = [c for c in summaries]  # ordine di comparsa nel file
    chains = [c for c in order if c in set(spec.chains)] if spec.chains else [order[0]]
    unknown = set(spec.chains) - set(order)
    if unknown:
        raise AppError(f"Catene {', '.join(sorted(unknown))} non presenti nel template {spec.id} "
                       f"(disponibili: {', '.join(order)}).")
    code = sequences.sanitize_code(f"{spec.id}_{''.join(chains)}", default="TEMPLATE")
    dest = dest_dir / f"{code}.pdb"
    dest_dir.mkdir(parents=True, exist_ok=True)
    trim = None
    removed: set = set()
    if spec.plddt_min is not None:
        trim = plddt.low_confidence(model, chains, float(spec.plddt_min))
        removed = trim["removed"]
        kept = len(trim["rows"]) - len(removed)
        if removed and kept < plddt.MIN_TRIMMED_RESIDUES:
            raise AppError(
                f"Togliendo i residui con pLDDT < {float(spec.plddt_min):g} del template {spec.id} ne restano "
                f"{kept} su {len(trim['rows'])} (minimo {plddt.MIN_TRIMMED_RESIDUES}): il modello AlphaFold è poco "
                "affidabile in questa regione. Abbassa la soglia pLDDT o disattiva il ritaglio nelle opzioni di "
                "modellazione, oppure cerca un altro template.", code="plddt_too_few")
    structure.write_pdb(st, dest, chains=chains, keep_hetero=spec.hetatm, keep_water=False, exclude=removed or None)
    _convert_selenomethionine(dest)
    header, predicted = _provenance_header(source, model, spec.id)
    if header:  # provenienza riconoscibile anche nella copia del job (per AlphaFold i B-factor restano pLDDT)
        if trim is not None and predicted:
            header.append(f"REMARK 999 BIOLOOM PLDDT_TRIM THRESHOLD {float(spec.plddt_min):g} "
                          f"REMOVED {len(removed)} OF {len(trim['rows'])}"
                          + (f" FRAGMENTS {trim['fragments']}" if trim["fragments"] else ""))
        body = dest.read_text(encoding="utf-8")
        atomic_write_text(dest, "\n".join(line.ljust(80) for line in header) + "\n" + body)
    if removed:
        summaries = {c["chain"]: c for c in structure.chain_summaries(
            structure.first_model(structure.load_structure(dest, code)))}
        empty = [c for c in chains if c not in summaries]
        if empty:
            raise AppError(f"Nessun residuo della catena {', '.join(empty)} del template {spec.id} ha pLDDT ≥ "
                           f"{float(spec.plddt_min):g}: togli la catena o abbassa la soglia.", code="plddt_too_few")
    ligands = []
    if spec.hetatm:
        ligands = [g for g in structure.hetero_groups(structure.first_model(structure.load_structure(dest, code)))]
    info = {
        "id": spec.id, "code": code, "file": dest.name, "chains": chains,
        "first": summaries[chains[0]]["first"], "last": summaries[chains[-1]]["last"],
        "sequence": "/".join(summaries[c]["sequence"] for c in chains), "ligands": len(ligands),
    }
    if trim is not None:
        values = [row[4] for row in trim["rows"]]
        kept_values = [row[4] for row in trim["rows"] if row[5]]
        info["plddt"] = {
            "threshold": float(spec.plddt_min), "total": len(values), "removed": len(removed),
            "removed_threshold": trim["below"], "removed_fragments": trim["fragments"],
            "min_fragment": plddt.MIN_FRAGMENT,
            "kept": len(kept_values), "mean_before": plddt.summarize(values)["mean"],
            "mean_after": plddt.summarize(kept_values)["mean"], "removed_ranges": plddt.removed_ranges(trim["rows"]),
        }
        info["_residues"] = [(row[0], row[2], row[3], row[5]) for row in trim["rows"]]
    return info


_HEADER_RECORDS = ("HEADER", "TITLE ", "COMPND", "SOURCE", "KEYWDS", "EXPDTA", "AUTHOR", "REMARK")


def _provenance_header(source: Path, model, template_id: str) -> tuple[list[str], bool]:
    """Intestazione della copia preparata e se il template è un modello AlphaFold (`plddt.is_alphafold_model`).

    La copia ha un altro nome e perde l'intestazione: riceve i record che la fanno riconoscere come il file d'origine.
    - Template AlphaFold: record descrittivi del PDB originale (HEADER, TITLE, COMPND, SOURCE, REMARK...) e, se non
      bastano (mmCIF, file riconosciuto dal nome), un TITLE "ALPHAFOLD … PREDICTION".
    - Altre strutture: il metodo dichiarato (EXPDTA del PDB, `_exptl.method` del mmCIF), così i B-factor di una
      struttura sperimentale o di un modello MODELLER non passano per pLDDT anche se l'id somiglia a quelli di
      AlphaFold DB.
    """
    head = structure.read_text_head(source, 200_000)
    is_pdb = source.suffix.lower() in (".pdb", ".ent")
    records = []
    if is_pdb:
        for line in head.splitlines():
            if line.startswith(("ATOM", "HETATM", "MODEL", "SEQRES", "DBREF", "CRYST1")):
                break
            if line.startswith(_HEADER_RECORDS) and not line.startswith(("REMARK 465", "REMARK 470")):
                records.append(line.rstrip())
    if not plddt.is_alphafold_model(source.name, head, plddt.atom_values(model)):
        if is_pdb:
            return [line for line in records if line.startswith("EXPDTA")], False
        method = structure.parse_cif_header(source).get("method") if "_exptl.method" in head else ""
        return ([f"EXPDTA    {method.upper()}"[:80]] if method else []), False
    if not plddt.is_alphafold_text("\n".join(records)):
        records.insert(0, f"TITLE     ALPHAFOLD PREDICTION, TEMPLATE {template_id} PREPARED BY BIOLOOM"[:80])
    return records, True


def _convert_selenomethionine(path: Path) -> None:
    lines = []
    changed = False
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("HETATM") and line[17:20] == "MSE":
            changed = True
            line = "ATOM  " + line[6:17] + "MET" + line[20:]
            if line[12:16] == "SE  ":
                line = line[:12] + " SD " + line[16:76] + " S" + line[78:]
        lines.append(line)
    if changed:
        atomic_write_text(path, "\n".join(lines) + "\n")


# --------------------------------------------------------------------------------- generazione script
_HEADER = """# Script generato da BioLoom v4
import json, sys
from modeller import *
from modeller.automodel import *
log.minimal()

def mark(text):
    print("{marker} " + text, flush=True)
"""


def _header() -> str:
    return _HEADER.replace("{marker}", MARKER)


def build_alignment_script(templates: list[dict], target_code: str) -> str:
    """align2d (un template) oppure salign strutturale dei template + allineamento del bersaglio."""
    lines = [_header(), "env = Environ()", "env.io.atom_files_directory = ['.']", "aln = Alignment(env)",
             "mark('stage Lettura dei template')"]
    for t in templates:
        seg = (f"FIRST:{t['chains'][0]}", f"LAST:{t['chains'][-1]}")
        lines.append(f"mdl = Model(env, file={t['file']!r}, model_segment={seg!r})")
        lines.append(f"aln.append_model(mdl, align_codes={t['code']!r}, atom_files={t['file']!r})")
    if len(templates) > 1:
        lines += [
            "mark('stage Allineamento strutturale dei template (salign)')",
            "aln.salign(rms_cutoff=3.5, normalize_pp_scores=False, rr_file='$(LIB)/as1.sim.mat', overhang=30,",
            "           gap_penalties_1d=(-450, -50), gap_penalties_3d=(0, 3), gap_gap_score=0, gap_residue_score=0,",
            "           alignment_type='tree', feature_weights=(1., 0., 0., 0., 1., 0.), improve_alignment=True,",
            "           fit=True, write_fit=False, output='ALIGNMENT QUALITY')",
            "block = len(aln)",
            f"aln.append(file='target.ali', align_codes={target_code!r})",
            "mark('stage Allineamento del bersaglio sul profilo dei template')",
            "aln.salign(output='', max_gap_length=20, gap_function=True, alignment_type='PAIRWISE',",
            "           align_block=block, feature_weights=(1., 0., 0., 0., 0., 0.), overhang=0,",
            "           gap_penalties_1d=(-450, 0), gap_penalties_2d=(0.35, 1.2, 0.9, 1.2, 0.6, 8.6, 1.2, 0., 0.),",
            "           similarity_flag=True)",
        ]
    else:
        lines += [f"aln.append(file='target.ali', align_codes={target_code!r})",
                  "mark('stage Allineamento sequenza-struttura (align2d)')",
                  "aln.align2d(max_gap_length=50)"]
    lines += ["aln.write(file='alignment.ali', alignment_format='PIR')",
              "aln.write(file='alignment.pap', alignment_format='PAP')",
              "mark('stage Allineamento scritto')"]
    return "\n".join(lines) + "\n"


def build_model_script(knowns: list[str], target_code: str, opts: ModelOptions) -> str:
    base = "DOPELoopModel" if opts.loop_refinement else "AutoModel"
    body = [f"class BioLoomModel({base}):", "    _done = 0", "    _loops_done = 0"]
    if opts.restraints:
        body += ["    def special_restraints(self, aln):", "        rsr = self.restraints", "        at = self.atoms"]
        for r in opts.restraints:
            if r.type in ("helix", "strand"):
                cls = "Alpha" if r.type == "helix" else "Strand"
                body.append(f"        rsr.add(secondary_structure.{cls}(self.residue_range({r.start!r}, {r.end!r})))")
            elif r.type == "distance":
                body += [
                    "        rsr.add(forms.Gaussian(group=physical.xy_distance,",
                    f"                               feature=features.Distance(at[{r.atom1!r}], at[{r.atom2!r}]),",
                    f"                               mean={float(r.mean)!r}, stdev={float(r.stdev)!r}))",
                ]
    if opts.loop_refinement and opts.loop_regions:
        ranges = ", ".join(f"self.residue_range({a!r}, {b!r})" for a, b in opts.loop_regions)
        body += ["    def select_loop_atoms(self):", f"        return Selection({ranges})"]
    body += [
        "    def user_after_single_model(self):",
        "        BioLoomModel._done += 1",
        f"        mark('model %d/{opts.models}' % BioLoomModel._done)",
    ]
    if opts.loop_refinement:
        body += [
            "    def user_after_single_loop_model(self):",
            "        BioLoomModel._loops_done += 1",
            f"        mark('loop %d/{opts.models * opts.loop_models}' % BioLoomModel._loops_done)",
        ]
    knowns_tuple = "(" + ", ".join(repr(k) for k in knowns) + ("," if len(knowns) == 1 else "") + ")"
    lines = [_header(), *body, "",
             f"env = Environ(rand_seed={int(opts.seed)})",
             "env.io.atom_files_directory = ['.']"]
    if opts.hetatm:
        lines.append("env.io.hetatm = True")
    lines += [
        f"a = BioLoomModel(env, alnfile='alignment.ali', knowns={knowns_tuple}, sequence={target_code!r},",
        "                 assess_methods=(assess.DOPE, assess.GA341, assess.normalized_dope))",
        "a.starting_model = 1",
        f"a.ending_model = {int(opts.models)}",
    ]
    if opts.refinement == "very_fast":
        lines.append("a.very_fast()")
    elif opts.refinement == "fast":
        lines.append("a.md_level = refine.very_fast")
    elif opts.refinement == "thorough":
        lines += ["a.library_schedule = autosched.slow", "a.max_var_iterations = 300",
                  "a.md_level = refine.slow", "a.repeat_optimization = 2", "a.max_molpdf = 1e6"]
    if opts.loop_refinement:
        lines += ["a.loop.starting_model = 1", f"a.loop.ending_model = {int(opts.loop_models)}",
                  f"a.loop.md_level = {LOOP_LEVELS.get(opts.loop_level, 'refine.fast')}",
                  "a.loop.assess_methods = (assess.DOPE, assess.normalized_dope)"]
    lines += [
        "mark('stage Costruzione dei modelli')",
        "a.make()",
        "def _clean(o):",
        "    ga = o.get('GA341 score')",
        "    return {'file': o.get('name'), 'failure': None if o.get('failure') is None else str(o.get('failure')),",
        "            'molpdf': o.get('molpdf'), 'dope': o.get('DOPE score'),",
        "            'ga341': ga[0] if isinstance(ga, (list, tuple)) and ga else ga,",
        "            'zdope': o.get('Normalized DOPE score')}",
        "out = {'models': [_clean(o) for o in a.outputs]}",
        "if hasattr(a, 'loop') and getattr(a.loop, 'outputs', None):",
        "    out['loop_models'] = [_clean(o) for o in a.loop.outputs]",
        "json.dump(out, open('models.json', 'w'), indent=1)",
        "mark('stage Modellazione completata')",
    ]
    return "\n".join(lines) + "\n"


def build_loop_script(model_file: str, code: str, start: str, end: str, loop_models: int, level: str) -> str:
    lines = [
        _header(),
        "class BioLoomLoop(DOPELoopModel):",
        "    _done = 0",
        "    def select_loop_atoms(self):",
        f"        return Selection(self.residue_range({start!r}, {end!r}))",
        "    def user_after_single_loop_model(self):",
        "        BioLoomLoop._done += 1",
        f"        mark('loop %d/{int(loop_models)}' % BioLoomLoop._done)",
        "env = Environ()",
        "env.io.atom_files_directory = ['.']",
        f"m = Model(env, file={model_file!r})",
        "aln = Alignment(env)",
        f"aln.append_model(m, align_codes={code!r})",
        "aln.write(file='loop_input.ali')",
        f"a = BioLoomLoop(env, inimodel={model_file!r}, sequence={code!r},",
        "                assess_methods=(assess.DOPE, assess.normalized_dope))",
        "a.loop.starting_model = 1",
        f"a.loop.ending_model = {int(loop_models)}",
        f"a.loop.md_level = {LOOP_LEVELS.get(level, 'refine.fast')}",
        "a.loop.assess_methods = (assess.DOPE, assess.normalized_dope)",
        "mark('stage Raffinamento del loop')",
        "a.make()",
        "out = [{'file': o.get('name'), 'failure': None if o.get('failure') is None else str(o.get('failure')),",
        "        'dope': o.get('DOPE score'), 'zdope': o.get('Normalized DOPE score'), 'molpdf': o.get('molpdf')}",
        "       for o in a.loop.outputs]",
        "json.dump({'loop_models': out}, open('models.json', 'w'), indent=1)",
    ]
    return "\n".join(lines) + "\n"


DOPE_SCRIPT = """# Script generato da BioLoom v4: valutazione DOPE
import json, sys
from modeller import *
from modeller.scripts import complete_pdb
log.none()
env = Environ()
env.libs.topology.read(file='$(LIB)/top_heav.lib')
env.libs.parameters.read(file='$(LIB)/par.lib')
mdl = complete_pdb(env, sys.argv[1])
sel = Selection(mdl)
score = sel.assess_dope(output='ENERGY_PROFILE NO_REPORT', file='dope.profile', normalize_profile=True,
                        smoothing_window=15)
try:
    z = mdl.assess_normalized_dope()
except Exception:
    z = None
json.dump({'dope': score, 'zdope': z}, open('dope.json', 'w'))
"""


# --------------------------------------------------------------------------------- validazione opzioni
def parse_options(raw: dict) -> ModelOptions:
    opts = ModelOptions()
    opts.models = int(raw.get("models", 5))
    if not 1 <= opts.models <= 100:
        raise AppError("Il numero di modelli deve essere compreso tra 1 e 100.")
    opts.refinement = raw.get("refinement", "normal")
    if opts.refinement not in REFINEMENTS:
        raise AppError(f"Livello di raffinamento non valido: {opts.refinement}")
    opts.hetatm = bool(raw.get("hetatm", False))
    opts.seed = int(raw.get("seed", -8123))
    if not -50000 <= opts.seed <= -2:
        raise AppError("Il seme casuale di MODELLER deve essere compreso tra -50000 e -2.")
    opts.plddt_trim = bool(raw.get("plddt_trim", True))
    try:
        value = raw.get("plddt_min")
        opts.plddt_min = plddt.DEFAULT_THRESHOLD if value is None else float(value)
    except (TypeError, ValueError):
        raise AppError("La soglia pLDDT deve essere un numero tra 0 e 100.")
    if not 0 <= opts.plddt_min <= 100:
        raise AppError("La soglia pLDDT deve essere compresa tra 0 e 100 (predefinita 70).")
    loop = raw.get("loop") or {}
    opts.loop_refinement = bool(loop.get("enabled", False))
    opts.loop_models = int(loop.get("models", 4))
    if opts.loop_refinement and not 1 <= opts.loop_models <= 50:
        raise AppError("I modelli di loop devono essere tra 1 e 50.")
    opts.loop_level = loop.get("level", "fast")
    for region in loop.get("regions", []) or []:
        start, end = str(region.get("start", "")).strip(), str(region.get("end", "")).strip()
        if not (_RESIDUE_ID.match(start) and _RESIDUE_ID.match(end)):
            raise AppError(f"Regione di loop non valida: {start}-{end} (formato atteso 'numero:catena', es. 45:A).")
        opts.loop_regions.append((start, end))
    for r in raw.get("restraints", []) or []:
        rtype = r.get("type")
        if rtype in ("helix", "strand"):
            start, end = str(r.get("start", "")).strip(), str(r.get("end", "")).strip()
            if not (_RESIDUE_ID.match(start) and _RESIDUE_ID.match(end)):
                raise AppError(f"Vincolo {rtype} non valido: usa il formato 'numero:catena' (es. 10:A).")
            opts.restraints.append(Restraint(type=rtype, start=start, end=end))
        elif rtype == "distance":
            a1, a2 = str(r.get("atom1", "")).strip(), str(r.get("atom2", "")).strip()
            if not (_ATOM_ID.match(a1) and _ATOM_ID.match(a2)):
                raise AppError("Vincolo di distanza non valido: usa il formato 'ATOMO:numero:catena' (es. CA:10:A).")
            mean, stdev = float(r.get("mean", 0)), float(r.get("stdev", 0.1))
            if not (0 < mean < 100 and 0 < stdev < 10):
                raise AppError("Distanza media (0–100 Å) o deviazione standard (0–10 Å) fuori intervallo.")
            opts.restraints.append(Restraint(type="distance", atom1=a1, atom2=a2, mean=mean, stdev=stdev))
        else:
            raise AppError(f"Tipo di vincolo sconosciuto: {rtype}")
    return opts


# --------------------------------------------------------------------------------- esecuzione
def run_script(ctx: JobContext, script: str, name: str, *, workdir: Optional[Path] = None,
               args: Optional[list[str]] = None, progress_total: int = 0,
               progress_range: tuple[float, float] = (0.0, 1.0)) -> str:
    """Scrive ed esegue uno script MODELLER; aggiorna il progresso leggendo i marcatori @@BIOLOOM."""
    workdir = workdir or ctx.workdir
    script_path = workdir / name
    # la chiave di licenza gestita da BioLoom arriva dall'ambiente, mai scritta nello script
    atomic_write_text(script_path, "# Licenza MODELLER: chiave da BIOLOOM_MODELLER_KEY, se impostata\n"
                      + tools.LICENSE_PREAMBLE + script)
    python = modeller_python(ctx)
    lo, hi = progress_range
    timeout = ctx.settings.job_timeout_minutes * 60

    def on_line(line: str) -> None:
        if not line.startswith(MARKER):
            return
        text = line[len(MARKER):].strip()
        if text.startswith("stage "):
            ctx.progress(stage=text[6:])
        else:
            m = re.match(r"(model|loop) (\d+)/(\d+)", text)
            if m:
                done, total = int(m.group(2)), int(m.group(3))
                label = "Modello" if m.group(1) == "model" else "Loop"
                ctx.progress(lo + (hi - lo) * done / max(total, 1), f"{label} {done} di {total} completato")

    result = ctx.run([python, name, *(args or [])], cwd=workdir, env=default_env(), timeout=timeout,
                     on_line=on_line)
    if result.returncode != 0:
        tail = "\n".join(result.output.strip().splitlines()[-12:])
        raise AppError(f"MODELLER è terminato con errore (codice {result.returncode}).\n{tail}", code="modeller_error")
    return result.output


def read_models(workdir: Path) -> dict:
    data = read_json(workdir / "models.json")
    if not data:
        raise AppError("MODELLER non ha prodotto il riepilogo dei modelli (models.json).", code="modeller_error")
    return data


def evaluate_dope(ctx: JobContext, pdb_path: Path, workdir: Path) -> dict:
    """DOPE totale, z-score normalizzato e profilo per residuo (finestra 15)."""
    workdir.mkdir(parents=True, exist_ok=True)
    local = workdir / "dope_input.pdb"
    local.write_bytes(pdb_path.read_bytes())
    run_script(ctx, DOPE_SCRIPT, "dope_eval.py", workdir=workdir, args=[local.name])
    data = read_json(workdir / "dope.json") or {}
    profile = []
    prof_path = workdir / "dope.profile"
    if prof_path.exists():
        for line in prof_path.read_text(encoding="utf-8", errors="replace").splitlines():
            parts = line.split()
            if len(parts) >= 3 and parts[0].isdigit():
                try:
                    profile.append({"index": int(parts[0]), "resname": parts[1], "energy": float(parts[-1])})
                except ValueError:
                    pass
    return {"dope": data.get("dope"), "zdope": data.get("zdope"), "profile": profile}


def alignment_to_pir_entries(text: str) -> list[PirEntry]:
    return sequences.parse_pir(text)


def target_ali(code: str, sequence: str) -> str:
    return sequences.format_pir([sequences.target_entry(code, sequence)])


def dump(obj: object) -> str:
    return json.dumps(obj, indent=1, ensure_ascii=False)
