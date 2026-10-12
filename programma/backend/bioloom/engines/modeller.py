"""Adattatore MODELLER: preparazione dei template, generazione degli script, esecuzione in sottoprocesso.

MODELLER viene sempre eseguito in un processo separato con l'interprete configurato: il server non
importa mai `modeller`, quindi può girare su qualunque Python e un crash di MODELLER non lo abbatte.
Gli script scrivono i risultati in JSON (`models.json`, `dope.json`) invece di affidarsi al parsing
del testo di log.
"""

from __future__ import annotations

import json
import re
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from ..core.errors import AppError, ToolUnavailable
from ..core.jobs import JobContext, default_env, output_secrets
from ..core.sensitive import redactor
from ..core.storage import atomic_write_text, read_json
from ..domain import ligands as ligands_mod
from ..domain import plddt, sequences, structure
from ..domain.sequences import PirEntry
from . import tools

MARKER = "@@BIOLOOM"
_RESIDUE_ID = re.compile(r"^-?\d+[A-Za-z]?:[A-Za-z0-9]$")
_ATOM_ID = re.compile(r"^[A-Za-z0-9']{1,4}:-?\d+[A-Za-z]?:[A-Za-z0-9]$")
REFINEMENTS = ("very_fast", "fast", "normal", "thorough")
# Livelli di raffinamento: impostazioni di AutoModel (manuale di MODELLER, «Changing the default optimization and
# refinement protocol»; predefiniti letti nel sorgente di MODELLER 10.8, modeller/automodel/automodel.py:
# library_schedule = autosched.normal, max_var_iterations = 200, md_level = refine.very_fast, repeat_optimization = 1,
# max_molpdf = 1e7). Un modello passa per due fasi: l'ottimizzazione con funzione obiettivo variabile (VTFM, gradienti
# coniugati, programma `library_schedule`) e la dinamica molecolare con ricottura simulata (MD&SA, `md_level`).
# Misurato su 1UBQ (ARCHITETTURA.md, Prestazioni): la dinamica occupa quasi tutto il tempo di un modello, mentre il
# programma VTFM conta poco (autosched.fast 2,32 s per modello contro 2,20 di autosched.normal, autosched.slow con 300
# iterazioni 2,52): per questo i livelli intermedi si distinguono per `md_level`.
# - very_fast: `a.very_fast()` di MODELLER: VTFM `autosched.fastest` con 50 iterazioni, nessuna dinamica, distanze di
#   taglio delle restrizioni più corte e coordinate iniziali non perturbate (`rand_method = None`), quindi tutti i
#   modelli sono identici (BioLoom lo avvisa); per prove e screening.
# - fast: i predefiniti di AutoModel, scritti per esteso (MD&SA `refine.very_fast`: equilibratura 50 passi a 3
#   temperature, campionamento 300 passi a 4). È il livello proposto dall'interfaccia.
# - normal: come fast con la dinamica `refine.fast` (equilibratura 100 passi a 4 temperature, campionamento 400 a 4):
#   ricottura più lunga, circa 1,4 volte il tempo di fast (media di 2 modelli; 1,6 sul solo modello successivo).
# - thorough: l'esempio di ottimizzazione accurata del manuale: VTFM `autosched.slow` con 300 iterazioni, MD&SA
#   `refine.slow` (200 passi a 5 temperature, 600 a 6), ciclo VTFM + MD&SA ripetuto 2 volte. `max_molpdf` resta il
#   predefinito di MODELLER 10.8 (1e7): il valore 1e6 dell'esempio è più basso e interromperebbe prima dei livelli più
#   rapidi un modello con funzione obiettivo alta.
REFINEMENT_SCRIPT = {
    "very_fast": ["a.very_fast()"],
    "fast": ["a.library_schedule = autosched.normal", "a.max_var_iterations = 200", "a.md_level = refine.very_fast"],
    "normal": ["a.library_schedule = autosched.normal", "a.max_var_iterations = 200", "a.md_level = refine.fast"],
    "thorough": ["a.library_schedule = autosched.slow", "a.max_var_iterations = 300", "a.md_level = refine.slow",
                 "a.repeat_optimization = 2"],
}
LOOP_LEVELS = {"very_fast": "refine.very_fast", "fast": "refine.fast", "slow": "refine.slow"}
# Modalità parallela di AutoModel (`use_parallel_job`, modeller.parallel): i modelli di un job si costruiscono in più
# processi worker sulla stessa macchina, dopo le restrizioni (calcolate una volta dal processo principale). Misurato su
# un server a 224 core (3PGK, 415 residui, raffinamento fast): 8 modelli da 173 s a 28 s con 8 worker, 16 modelli da 344
# s a 28 s con 16. Il risultato non dipende dal numero di worker (stesso seme, stessi modelli con 1, 2 o N worker), ma a
# parità di seme è diverso da quello seriale: la modalità si registra nel risultato (`options.parallel`) e la
# riesecuzione di un pacchetto la rispetta.
# - La classe BioLoomModel sta in un modulo (PARALLEL_MODULE) che i worker importano.
# - I worker sono processi a parte, avviati con PARALLEL_WORKER: stesso preambolo della licenza dello script principale
#   (che lascia la chiave nell'ambiente per loro) e, se il worker non parte o si interrompe con un errore, termina il
#   processo principale, che altrimenti aspetterebbe il worker fino al timeout del job.
# - I marcatori dei worker vanno nei loro log (`model.workerN`): l'avanzamento si legge dai file dei modelli scritti.
PARALLEL_MODULE = "bioloom_model.py"
PARALLEL_WORKER = "bioloom_worker.py"
_WORKER_BODY = """import os as _w_os, signal as _w_signal, sys as _w_sys, traceback as _w_tb
_w_sys.path.insert(0, _w_os.getcwd())


def _w_fail():
    print("{marker} worker-failed", flush=True)
    _w_master = int(_w_os.environ.get("BIOLOOM_MODELLER_MASTER", "0") or 0)
    if _w_master:
        try:
            _w_os.kill(_w_master, _w_signal.SIGTERM)
        except OSError:
            pass


try:
    from modeller.parallel.modworker import main as _w_main
    _w_main()
except SystemExit as _w_exc:
    if _w_exc.code not in (None, 0):
        _w_fail()
    raise
except BaseException:
    _w_tb.print_exc()
    _w_sys.stdout.flush()
    _w_fail()
    raise
"""
# Gruppo eteroatomico nelle opzioni: catena (anche di più caratteri nei mmCIF), numero con codice di inserzione,
# nome del componente chimico (fino a 5 caratteri nei mmCIF)
_LIGAND_CHAIN = re.compile(r"^[A-Za-z0-9]{1,4}$")
_LIGAND_RESNUM = re.compile(r"^-?\d{1,6}[A-Za-z]?$")
_LIGAND_NAME = re.compile(r"^[A-Za-z0-9]{1,5}$")
MAX_LIGANDS = 100


@dataclass
class TemplateSpec:
    id: str
    chains: list[str] = field(default_factory=list)
    # Trasferimento dei gruppi eteroatomici (ligandi, cofattori, metalli) nel modello
    hetatm: bool = False
    # Template predetto (AlphaFold): si tolgono i residui con pLDDT (colonna B-factor) sotto la soglia; None = no
    plddt_min: Optional[float] = None
    # Gruppi da trasferire come (catena, numero, nome); None = scelta predefinita (domain/ligands.select: niente
    # additivi, ioni monovalenti e residui modificati). Conta solo con `hetatm`.
    ligands: Optional[list[tuple[str, str, str]]] = None
    # Complessi (F5.3): catene nella copia preparata nell'ordine di `chains` (quello delle catene del bersaglio),
    # invece che nell'ordine del file
    keep_order: bool = False


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
    refinement: str = "fast"  # i predefiniti di AutoModel (REFINEMENT_SCRIPT)
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
    # Gruppi eteroatomici da trasferire: [{"template", "chain", "resnum", "resname"}] ("template" vuoto = qualunque
    # template con quel gruppo). None = scelta predefinita dei template con i ligandi attivi; un elenco = solo quei
    # gruppi (e attiva il trasferimento per i loro template).
    ligands: Optional[list[dict]] = None
    # Bersaglio a più catene (F5.3): restrizioni di simmetria fra le catene con la stessa sequenza (C-alfa dei residui
    # standard, domain/complexes.symmetry_groups) e loro peso
    symmetry: bool = False
    symmetry_weight: float = 1.0
    # Costruzione dei modelli in parallelo (PARALLEL_MODULE), con almeno 2 modelli; nel risultato il valore usato
    parallel: bool = True


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

    Ligandi (`spec.hetatm`): si scrivono solo i gruppi scelti (`domain/ligands.select`: quelli di `spec.ligands`
    oppure la scelta predefinita), in fondo alla loro catena e nell'ordine del file, così le colonne '.' del PIR
    (`ligands.pir_with_ligands`) corrispondono ai gruppi che MODELLER legge. `ligand_groups` li descrive con il sito
    nel file originale (donatori dei metalli, acque comprese, e legami covalenti) per il confronto dopo la
    modellazione; `ligands_excluded` elenca i gruppi delle catene scelte lasciati fuori, con il motivo. Le chiavi
    interne `_chain_residues` (residui ATOM per catena nella copia), `_chain_ids` (i loro numeri, nell'ordine: il
    sito di un gruppo sulle colonne del PIR) e `_hetero` (eteroatomi e acque delle catene) servono al PIR.

    Acque (`spec.ligands` con un'acqua): nessuna per default; quelle scelte entrano in `ligand_groups` (classe "water",
    con i legami H del sito) e si scrivono dopo i gruppi eteroatomici della loro catena, con il nome HOH; `waters` le
    conta. MODELLER le legge con `env.io.water = True` e le colonne 'w' di `ligands.modeller_pir`.
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
    if spec.keep_order and spec.chains:
        # complesso: la copia ha le catene nell'ordine del bersaglio, così i '/' del PIR si corrispondono
        chains = list(dict.fromkeys(spec.chains))
        model.child_list.sort(key=lambda c: chains.index(c.id) if c.id in chains else len(chains))
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
    selected = ligands_mod.select(model, chains, spec.ligands) if spec.hetatm else []
    waters = [r for r in selected if ligands_mod.is_water(r)]
    water_ids = {(r.get_parent().id, r.id) for r in waters}
    selected_ids = {(r.get_parent().id, r.id) for r in selected} - water_ids
    keep = set(selected_ids)
    if spec.hetatm:  # come prima: con i ligandi attivi le selenometionine restano (convertite in MET)
        keep |= {(c.id, r.id) for c in model if c.id in chains for r in c if r.get_resname().strip() == "MSE"}
    # siti nel file originale (con le acque non scelte, che la copia non ha), prima di riordinare le catene
    groups = ligands_mod.describe_groups(model, selected) if selected else []
    candidates = ligands_mod.candidates(model, chains)
    all_waters = [(c.id, structure._resid(r)) for c in model if c.id in chains for r in c if ligands_mod.is_water(r)]
    # gruppi scelti in fondo alla loro catena, poi le acque scelte: MODELLER li legge dopo l'ultimo residuo, nell'ordine
    # delle colonne '.' (e 'w') del PIR
    for chain in model:
        if chain.id in chains:
            chain.child_list.sort(key=lambda r, c=chain.id: 2 if (c, r.id) in water_ids else int((c, r.id) in selected_ids))
    for res in waters:  # MODELLER riconosce l'acqua come HOH (WAT, DOD... no)
        res.resname = ligands_mod.WATER_NAME
    structure.write_pdb(st, dest, chains=chains, keep_hetero=keep or False, keep_water=water_ids or False,
                        exclude=removed or None)
    _convert_selenomethionine(dest)
    header, predicted = _provenance_header(source, model, spec.id)
    if header:  # provenienza riconoscibile anche nella copia del job (per AlphaFold i B-factor restano pLDDT)
        if trim is not None and predicted:
            header.append(f"REMARK 999 BIOLOOM PLDDT_TRIM THRESHOLD {float(spec.plddt_min):g} "
                          f"REMOVED {len(removed)} OF {len(trim['rows'])}"
                          + (f" FRAGMENTS {trim['fragments']}" if trim["fragments"] else ""))
        body = dest.read_text(encoding="utf-8")
        atomic_write_text(dest, "\n".join(line.ljust(80) for line in header) + "\n" + body)
    prepared = structure.first_model(structure.load_structure(dest, code))
    if removed:
        summaries = {c["chain"]: c for c in structure.chain_summaries(prepared)}
        empty = [c for c in chains if c not in summaries]
        if empty:
            raise AppError(f"Nessun residuo della catena {', '.join(empty)} del template {spec.id} ha pLDDT ≥ "
                           f"{float(spec.plddt_min):g}: togli la catena o abbassa la soglia.", code="plddt_too_few")
    info = {
        "id": spec.id, "code": code, "file": dest.name, "chains": chains,
        "first": summaries[chains[0]]["first"], "last": summaries[chains[-1]]["last"],
        "sequence": "/".join(summaries[c]["sequence"] for c in chains), "ligands": len(selected) - len(waters),
    }
    if waters:
        info["waters"] = len(waters)
    if spec.hetatm:
        info["ligand_groups"] = groups
        chosen = {(g["chain"], g["resnum"], g["resname"]) for g in groups}
        info["ligands_excluded"] = [
            {k: g[k] for k in ("resname", "chain", "resnum", "class", "label", "reason")} for g in candidates
            if (g["chain"], g["resnum"], g["resname"]) not in chosen]
    info["_chain_residues"] = {c.id: sum(1 for r in c if r.id[0] == " ") for c in prepared}
    info["_chain_ids"] = {c.id: [structure._resid(r) for r in c if r.id[0] == " "] for c in prepared}
    info["_hetero"] = [(g["chain"], g["resnum"]) for g in candidates] + all_waters
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
    # una predizione di ESMFold (F9.3) è riconoscibile solo dal suo TITLE, che è già fra i record copiati
    if not plddt.is_predicted_text("\n".join(records)):
        records.insert(0, f"TITLE     ALPHAFOLD PREDICTION, TEMPLATE {template_id} PREPARED BY BIOLOOM"[:80])
    return records, True


def _convert_selenomethionine(path: Path) -> None:
    text, changed = structure.selenomethionine_as_methionine(path.read_text(encoding="utf-8"))
    if changed:
        atomic_write_text(path, text)


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
    """align2d (un template) oppure, con più template, salign strutturale dei template + allineamento del bersaglio
    sul loro profilo (`SALIGN_FILE`) e align2d del bersaglio su ogni template (`pair_file`). Con più template
    l'allineamento definitivo (`alignment.ali`) lo sceglie BioLoom fra i due (`domain/multitemplate.choose`)."""
    lines = [_header(), "env = Environ()", "env.io.atom_files_directory = ['.']", "aln = Alignment(env)",
             "mark('stage Lettura dei template')"]
    segments = []
    for t in templates:
        seg = (f"FIRST:{t['chains'][0]}", f"LAST:{t['chains'][-1]}")
        segments.append(seg)
        lines.append(f"mdl = Model(env, file={t['file']!r}, model_segment={seg!r})")
        lines.append(f"aln.append_model(mdl, align_codes={t['code']!r}, atom_files={t['file']!r})")
    if len(templates) > 1:
        # salign può non riuscire con template molto diversi: l'errore si scrive nel log e restano gli align2d
        lines += [
            "mark('stage Allineamento strutturale dei template (salign)')",
            "try:",
            "    aln.salign(rms_cutoff=3.5, normalize_pp_scores=False, rr_file='$(LIB)/as1.sim.mat', overhang=30,",
            "               gap_penalties_1d=(-450, -50), gap_penalties_3d=(0, 3), gap_gap_score=0,",
            "               gap_residue_score=0, alignment_type='tree', feature_weights=(1., 0., 0., 0., 1., 0.),",
            "               improve_alignment=True, fit=True, write_fit=False, output='ALIGNMENT QUALITY')",
            "    block = len(aln)",
            f"    aln.append(file='target.ali', align_codes={target_code!r})",
            "    mark('stage Allineamento del bersaglio sul profilo dei template')",
            "    aln.salign(output='', max_gap_length=20, gap_function=True, alignment_type='PAIRWISE',",
            "               align_block=block, feature_weights=(1., 0., 0., 0., 0., 0.), overhang=0,",
            "               gap_penalties_1d=(-450, 0), gap_penalties_2d=(0.35, 1.2, 0.9, 1.2, 0.6, 8.6, 1.2, 0., 0.),",
            "               similarity_flag=True)",
            f"    aln.write(file={SALIGN_FILE!r}, alignment_format='PIR')",
            "    aln.write(file='alignment.pap', alignment_format='PAP')",
            "except Exception as exc:",
            "    print('salign non riuscito: %s' % exc, flush=True)",
            "mark('stage Allineamento del bersaglio su ogni template (align2d)')",
        ]
        for i, (t, seg) in enumerate(zip(templates, segments)):
            lines += [
                "pair = Alignment(env)",
                f"mdl = Model(env, file={t['file']!r}, model_segment={seg!r})",
                f"pair.append_model(mdl, align_codes={t['code']!r}, atom_files={t['file']!r})",
                f"pair.append(file='target.ali', align_codes={target_code!r})",
                "pair.align2d(max_gap_length=50)",
                f"pair.write(file={pair_file(i)!r}, alignment_format='PIR')",
            ]
        lines.append("mark('stage Allineamenti scritti')")
        return "\n".join(lines) + "\n"
    lines += [f"aln.append(file='target.ali', align_codes={target_code!r})",
              "mark('stage Allineamento sequenza-struttura (align2d)')",
              "aln.align2d(max_gap_length=50)",
              "aln.write(file='alignment.ali', alignment_format='PIR')",
              "aln.write(file='alignment.pap', alignment_format='PAP')",
              "mark('stage Allineamento scritto')"]
    return "\n".join(lines) + "\n"


# Più template: allineamento di salign e allineamenti a coppie del bersaglio su ogni template (align2d)
SALIGN_FILE = "alignment_salign.ali"
# Acque trasferite: PIR letto da MODELLER con le colonne 'w' (alignment.ali resta il PIR di BioLoom, con '.')
WATER_ALIGNMENT_FILE = "alignment_modeller.ali"


def pair_file(index: int) -> str:
    return f"alignment_pair_{index + 1}.ali"


def chain_target_file(index: int) -> str:
    return f"target_chain_{index + 1}.ali"


def chain_alignment_file(index: int) -> str:
    return f"alignment_chain_{index + 1}.ali"


def build_complex_alignment_script(template: dict, target_code: str) -> str:
    """Complesso (F5.3): align2d di ogni catena del bersaglio (`chain_target_file`) sulla sua catena del template
    (segmento FIRST:X–LAST:X della copia preparata, catene nell'ordine del bersaglio), un allineamento per catena
    (`chain_alignment_file`); BioLoom li unisce con '/' (domain/complexes.merge_chain_alignments)."""
    lines = [_header(), "env = Environ()", "env.io.atom_files_directory = ['.']"]
    total = len(template["chains"])
    for i, chain in enumerate(template["chains"]):
        seg = (f"FIRST:{chain}", f"LAST:{chain}")
        lines += [
            f"mark('stage Allineamento della catena {chain} ({i + 1} di {total}, align2d)')",
            "aln = Alignment(env)",
            f"mdl = Model(env, file={template['file']!r}, model_segment={seg!r})",
            f"aln.append_model(mdl, align_codes={template['code']!r}, atom_files={template['file']!r})",
            f"aln.append(file={chain_target_file(i)!r}, align_codes={target_code!r})",
            "aln.align2d(max_gap_length=50)",
            f"aln.write(file={chain_alignment_file(i)!r}, alignment_format='PIR')",
        ]
    lines.append("mark('stage Allineamenti per catena scritti')")
    return "\n".join(lines) + "\n"


def build_model_script(knowns: list[str], target_code: str, opts: ModelOptions,
                       hetatm: Optional[bool] = None, symmetry: Optional[list[list[int]]] = None,
                       chain_names: Optional[list[str]] = None, water: bool = False,
                       alnfile: str = "alignment.ali", parallel: bool = False) -> str:
    """Script di AutoModel/DOPELoopModel. `hetatm`: MODELLER legge i gruppi HETATM dei template (quelli scritti nelle
    copie preparate, con le colonne '.' nel PIR); None = come l'opzione `hetatm`. `water`: legge anche le acque delle
    copie preparate (solo quelle scelte), con le colonne 'w' del PIR `alnfile` (`ligands.modeller_pir`).

    Bersaglio a più catene (F5.3): `symmetry` = gruppi di indici di catene con la stessa sequenza (ogni catena vincolata
    alla prima del gruppo: `restraints.symmetry` sui C-alfa dei residui standard, peso `opts.symmetry_weight`, come
    nell'esempio del manuale di MODELLER); `chain_names` = nomi delle catene del modello (quelli del template), con i
    residui di ogni catena numerati da 1 (`rename_segments` in `special_patches`).

    `parallel`: modelli costruiti da worker in processi separati (PARALLEL_MODULE); il numero di worker è il primo
    argomento dello script (`run_script(workers=...)`, 1 se manca) e il risultato non ne dipende."""
    base = "DOPELoopModel" if opts.loop_refinement else "AutoModel"
    body = [f"class BioLoomModel({base}):", "    _done = 0", "    _loops_done = 0"]
    if chain_names:
        names = [str(c) for c in chain_names]
        if len(set(names)) != len(names) or not all(re.fullmatch(r"[A-Za-z0-9]", c) for c in names):
            raise AppError(f"Nomi delle catene del modello non validi: {', '.join(names)}.")
        body += ["    def special_patches(self, aln):",
                 f"        self.rename_segments(segment_ids={names!r}, renumber_residues={[1] * len(names)!r})"]
    pairs = [(int(g[0]), int(k)) for g in symmetry or [] for k in g[1:]]
    if opts.restraints or pairs:
        body += ["    def special_restraints(self, aln):", "        rsr = self.restraints", "        at = self.atoms"]
        for a, b in pairs:
            body += [
                f"        s1 = Selection(self.chains[{a}]).only_std_residues().only_atom_types('CA')",
                f"        s2 = Selection(self.chains[{b}]).only_std_residues().only_atom_types('CA')",
                f"        rsr.symmetry.append(Symmetry(s1, s2, {float(opts.symmetry_weight)!r}))",
            ]
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
    if parallel:
        lines = [_header(), *_parallel_setup("\n".join([_header(), *body]) + "\n")]
    else:
        lines = [_header(), *body]
    lines += ["",
              f"env = Environ(rand_seed={int(opts.seed)})",
              "env.io.atom_files_directory = ['.']"]
    if (opts.hetatm if hetatm is None else hetatm):
        lines.append("env.io.hetatm = True")
    if water:
        lines.append("env.io.water = True")
    lines += [
        f"a = BioLoomModel(env, alnfile={alnfile!r}, knowns={knowns_tuple}, sequence={target_code!r},",
        "                 assess_methods=(assess.DOPE, assess.GA341, assess.normalized_dope))",
        "a.starting_model = 1",
        f"a.ending_model = {int(opts.models)}",
    ]
    if parallel:
        lines.append("a.use_parallel_job(job)")
    lines += REFINEMENT_SCRIPT.get(opts.refinement, REFINEMENT_SCRIPT["fast"])
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
        *(["out['workers'] = _bl_workers"] if parallel else []),
        "json.dump(out, open('models.json', 'w'), indent=1)",
        "mark('stage Modellazione completata')",
    ]
    return "\n".join(lines) + "\n"


def _parallel_setup(module: str) -> list[str]:
    """Righe dello script principale in modalità parallela: scrive il modulo della classe e l'avvio dei worker, passa
    loro la licenza e il proprio pid, crea il Job con i worker indicati dal primo argomento."""
    worker = tools.LICENSE_PREAMBLE + _WORKER_BODY.replace("{marker}", MARKER)
    return [
        "import os",
        "_bl_workers = max(1, int(sys.argv[1])) if len(sys.argv) > 1 else 1",
        f"with open({PARALLEL_MODULE!r}, 'w') as _fh:",
        f"    _fh.write({module!r})",
        f"with open({PARALLEL_WORKER!r}, 'w') as _fh:",
        f"    _fh.write({worker!r})",
        "# i worker sono processi a parte: ricevono la licenza (tolta dall'ambiente dal preambolo) e il pid da terminare",
        "# se non riescono a lavorare",
        "if globals().get('_bl_key'):",
        "    os.environ['BIOLOOM_MODELLER_KEY'] = _bl_key",
        "os.environ['BIOLOOM_MODELLER_MASTER'] = str(os.getpid())",
        "sys.path.insert(0, os.getcwd())",
        "from bioloom_model import BioLoomModel",
        "from modeller.parallel import Job, LocalWorker",
        f"job = Job(modeller_path=sys.executable + ' ' + {PARALLEL_WORKER!r})",
        "for _ in range(_bl_workers):",
        "    job.append(LocalWorker())",
        "mark('workers %d' % _bl_workers)",
    ]


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
    opts.refinement = raw.get("refinement", "fast")
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
    opts.ligands = _parse_ligands(raw.get("ligands"))
    opts.symmetry = bool(raw.get("symmetry", False))
    opts.parallel = bool(raw.get("parallel", True))
    try:
        weight = raw.get("symmetry_weight")
        opts.symmetry_weight = 1.0 if weight is None else float(weight)
    except (TypeError, ValueError):
        raise AppError("Il peso delle restrizioni di simmetria deve essere un numero.")
    if not 0 < opts.symmetry_weight <= 10:
        raise AppError("Il peso delle restrizioni di simmetria deve essere compreso tra 0 (escluso) e 10 "
                       "(predefinito 1, come nel manuale di MODELLER).")
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


def _parse_ligands(raw) -> Optional[list[dict]]:
    """Opzione `ligands`: None (scelta predefinita) oppure l'elenco dei gruppi da trasferire, validati (i valori
    finiscono nei nomi dei residui scritti da Biopython, mai negli script di MODELLER)."""
    if raw is None:
        return None
    if not isinstance(raw, list):
        raise AppError("L'opzione ligands deve essere un elenco di gruppi {template, chain, resnum, resname}.")
    if len(raw) > MAX_LIGANDS:
        raise AppError(f"Troppi ligandi da trasferire ({len(raw)}, massimo {MAX_LIGANDS}).")
    out, seen = [], set()
    for item in raw:
        if not isinstance(item, dict):
            raise AppError("Ogni ligando da trasferire è un oggetto {template, chain, resnum, resname}.")
        entry = {"template": str(item.get("template") or "").strip(), "chain": str(item.get("chain") or "").strip(),
                 "resnum": str(item.get("resnum") or "").strip(), "resname": str(item.get("resname") or "").strip()}
        if not (_LIGAND_CHAIN.match(entry["chain"]) and _LIGAND_RESNUM.match(entry["resnum"])
                and _LIGAND_NAME.match(entry["resname"])) or len(entry["template"]) > 60:
            raise AppError(f"Ligando non valido: {entry['resname'] or '?'} {entry['chain'] or '?'}"
                           f"{entry['resnum'] or '?'} (catena, numero e nome come nel file del template, "
                           "es. ZN A101).", code="invalid_ligand")
        key = tuple(entry.values())
        if key not in seen:
            seen.add(key)
            out.append(entry)
    return out


# --------------------------------------------------------------------------------- esecuzione
def run_script(ctx: JobContext, script: str, name: str, *, workdir: Optional[Path] = None,
               args: Optional[list[str]] = None, progress_total: int = 0,
               progress_range: tuple[float, float] = (0.0, 1.0), workers: int = 0,
               progress_files: Optional[list[tuple[str, str, int]]] = None) -> str:
    """Scrive ed esegue uno script MODELLER; aggiorna il progresso leggendo i marcatori @@BIOLOOM.

    `workers` > 0 (modalità parallela, `build_model_script(parallel=True)`): lo script riceve come ultimo argomento il
    numero di worker, da 1 a `workers` secondo gli slot liberi del budget di CPU (uno per worker). I marcatori dei
    worker non arrivano qui: l'avanzamento si legge dai file dei modelli, `progress_files` = [(glob nella cartella,
    etichetta, totale)], e un worker che non riesce a lavorare termina lo script (il suo log si aggiunge all'errore)."""
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
            elif text.startswith("workers "):
                n = text.split()[1]
                ctx.log(f"MODELLER in parallelo: {'un processo' if n == '1' else n + ' processi'} per i modelli")

    stop = threading.Event()
    watcher = None
    if workers and progress_files:
        def watch() -> None:  # avanzamento dai file dei modelli scritti dai worker
            seen = -1
            while not stop.wait(1.0):
                counts = [(label, len(list(workdir.glob(pattern))), total) for pattern, label, total in progress_files]
                done = sum(c for _label, c, _total in counts)
                if done == seen:
                    continue
                seen = done
                total = sum(t for _label, _c, t in counts)
                label, c, t = next(((lb, c, t) for lb, c, t in reversed(counts) if c), counts[0])
                ctx.progress(lo + (hi - lo) * min(done, total) / max(total, 1), f"{label} {c} di {t} completat"
                             + ("i" if c != 1 else "o"))

        watcher = threading.Thread(target=watch, name=f"modeller-progress-{ctx.job_id[:6]}", daemon=True)
        watcher.start()
    base = [python, name, *(args or [])]
    try:
        if workers:
            result = ctx.run(lambda n: [*base, str(n)], cwd=workdir, env=default_env(), timeout=timeout,
                             on_line=on_line, cpu=int(workers))
        else:
            result = ctx.run(base, cwd=workdir, env=default_env(), timeout=timeout, on_line=on_line)
    finally:
        stop.set()
        if watcher is not None:
            watcher.join(timeout=5)
    if result.returncode != 0:
        tail = "\n".join(result.output.strip().splitlines()[-12:])
        failed = _failed_worker_log(workdir, Path(name).stem) if workers else None
        if failed:
            tail = f"{tail}\nErrore in un worker di MODELLER ({failed[0]}):\n{failed[1]}"
        raise AppError(f"MODELLER è terminato con errore (codice {result.returncode}).\n{tail}", code="modeller_error")
    return result.output


def _failed_worker_log(workdir: Path, stem: str) -> Optional[tuple[str, str]]:
    """(nome, ultime righe) del log del primo worker che ha segnalato un errore (`worker-failed`), con la chiave di
    licenza mascherata come nell'output dei processi; None se nessuno."""
    hide = redactor(output_secrets(default_env()))
    for path in sorted(workdir.glob(f"{stem}.worker*")):
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if f"{MARKER} worker-failed" not in text:
            continue
        body = "\n".join([ln for ln in text.strip().splitlines() if not ln.startswith(MARKER)][-12:])
        return path.name, hide(body) if hide is not None else body
    return None


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
