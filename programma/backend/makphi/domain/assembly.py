"""Assemblaggi biologici delle strutture del PDB: lettura dal file e coordinate dell'assemblaggio (F5.3).

Logica pura (numpy, Biopython). Un file del PDB contiene l'unità asimmetrica del cristallo; l'assemblaggio biologico
(il dimero, il tetramero…) si ottiene applicando alle catene gli operatori di simmetria dichiarati nel file. Due
sorgenti, secondo il formato del template in libreria:

- **mmCIF (PDBx)**: `pdbx_struct_assembly` (id, details, method_details, oligomeric_details, oligomeric_count),
  `pdbx_struct_assembly_gen` (assembly_id, oper_expression, asym_id_list) e `pdbx_struct_oper_list` (id, type, name,
  matrix[i][j], vector[i]). Le catene dei generatori sono `label_asym_id`: nel mmCIF ogni entità ha il suo asym
  (proteina, ligandi, acque), mentre Biopython raggruppa i residui per `auth_asym_id`. Ogni residuo del primo modello
  riceve il suo `label_asym_id` dall'`atom_site`, così un generatore prende esattamente i residui elencati (anche solo
  una parte di una catena dell'autore).
- **PDB**: REMARK 350 (BIOMOLECULE, AUTHOR DETERMINED BIOLOGICAL UNIT / SOFTWARE DETERMINED QUATERNARY STRUCTURE,
  APPLY THE FOLLOWING TO CHAINS con le righe AND CHAINS, BIOMT1–3). Le catene sono quelle dell'autore, con i loro
  eteroatomi e le acque. Ogni gruppo "APPLY THE FOLLOWING TO CHAINS" ha i suoi operatori (i numeri BIOMT si ripetono
  fra gruppi diversi). È il formato con cui la libreria scarica da RCSB per default.

**Espressione degli operatori** (`oper_expression`, dizionario PDBx): "1", "1,2,5", "1-4", "(1-4)" e il prodotto
cartesiano di gruppi fra parentesi, "(1,2)(3,4)" = 1·3, 1·4, 2·3, 2·4, applicato da destra a sinistra (prima 3, poi
1: x' = M1·(M3·x + t3) + t1). Ogni operatore porta x in x' = R·x + t (matrix[i][j] è la riga i, colonna j).

**Nomi delle catene** (il formato PDB e MODELLER vogliono un carattere): le copie si generano nell'ordine dei
generatori, poi degli operatori dell'espressione, poi delle catene nell'ordine del file. La prima copia di ogni
catena dell'autore tiene il suo nome, se è di un carattere; tutte le altre copie ricevono, nell'ordine di
generazione, il primo carattere libero di A–Z, a–z, 0–9 non tenuto da una prima copia. Esempio: catene A e B, due
operatori per A e uno per B → A (operatore 1), C (A con l'operatore 2), B. La corrispondenza nuova catena → catena
d'origine e operatore è nel risultato (`chains`) e nel file (`REMARK 999 MAKPHI ASSEMBLY CHAIN`).

**Limiti:** al massimo 62 catene (i caratteri disponibili) e 99 999 atomi (numerazione degli atomi del formato PDB):
gli assemblaggi più grandi (capsidi virali, 60 operatori) non si costruiscono, con un errore che lo dice. Si usa il
primo modello del file e, per gli atomi con posizioni alternative, la prima (A, ' ' o 1), come nella preparazione dei
template.
"""

from __future__ import annotations

import io
import re
from dataclasses import dataclass, field
from itertools import product
from pathlib import Path
from typing import Iterable, Optional

import numpy as np

from ..core.errors import AppError
from .structure import is_amino_residue, load_structure, first_model, read_text_head

CHAIN_IDS = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789"
MAX_CHAINS = len(CHAIN_IDS)
MAX_ATOMS = 99_999
# oltre questi operatori combinati per generatore l'espressione non si espande (capsidi: 60 × 60 nei prodotti)
MAX_OPERATIONS = 1000
IDENTITY_TOLERANCE = 1e-4


@dataclass
class Operator:
    id: str
    matrix: np.ndarray
    vector: np.ndarray
    type: str = ""
    name: str = ""

    def is_identity(self) -> bool:
        return bool(np.allclose(self.matrix, np.eye(3), atol=IDENTITY_TOLERANCE)
                    and np.allclose(self.vector, 0.0, atol=IDENTITY_TOLERANCE))


@dataclass
class Generator:
    """Catene (label_asym_id nel mmCIF, catene dell'autore nel PDB) e combinazioni di operatori da applicare."""
    chains: list[str]
    expression: str
    operations: list[tuple[str, ...]] = field(default_factory=list)


@dataclass
class Assembly:
    id: str
    details: str = ""
    method: str = ""
    oligomeric_details: str = ""
    oligomeric_count: Optional[int] = None
    generators: list[Generator] = field(default_factory=list)


@dataclass
class AssemblyInfo:
    """Assemblaggi letti da un file: `labels` = le catene dei generatori sono label_asym_id (mmCIF)."""
    format: str
    assemblies: list[Assembly] = field(default_factory=list)
    operators: dict[str, Operator] = field(default_factory=dict)
    labels: bool = False
    # (catena dell'autore, id Biopython del residuo) → label_asym_id (solo mmCIF, primo modello)
    residue_labels: dict[tuple[str, tuple], str] = field(default_factory=dict)
    note: str = ""


# ---------------------------------------------------------------------------------------------- espressioni
def _expand_list(text: str) -> list[str]:
    """"1,2,5" o "1-4" (anche insieme: "1-3,7") → identificativi, nell'ordine. Gli intervalli valgono per i numeri."""
    out: list[str] = []
    for token in (t.strip() for t in text.split(",")):
        if not token:
            continue
        m = re.fullmatch(r"(-?\d+)\s*-\s*(-?\d+)", token)
        if m:
            a, b = int(m.group(1)), int(m.group(2))
            if b < a or b - a >= MAX_OPERATIONS:
                raise AppError(f"Intervallo di operatori non valido nell'assemblaggio: {token}.",
                               code="invalid_assembly")
            out.extend(str(i) for i in range(a, b + 1))
        else:
            out.append(token)
    return out


def parse_oper_expression(expression: str) -> list[tuple[str, ...]]:
    """Combinazioni di operatori di un'espressione PDBx: ogni tupla elenca gli operatori da sinistra a destra (si
    applicano da destra a sinistra). "1,2" → [("1",), ("2",)]; "(1,2)(3)" → [("1", "3"), ("2", "3")]."""
    text = re.sub(r"\s", "", expression or "")
    if not text:
        raise AppError("Assemblaggio senza operatori (oper_expression vuota).", code="invalid_assembly")
    if "(" in text or ")" in text:
        groups = re.findall(r"\(([^()]*)\)", text)
        if not groups or re.sub(r"\([^()]*\)", "", text):
            raise AppError(f"Espressione degli operatori non riconosciuta: {expression}.", code="invalid_assembly")
        lists = [_expand_list(g) for g in groups]
    else:
        lists = [_expand_list(text)]
    if any(not items for items in lists):
        raise AppError(f"Espressione degli operatori non riconosciuta: {expression}.", code="invalid_assembly")
    total = 1
    for items in lists:
        total *= len(items)
    if total > MAX_OPERATIONS:
        raise AppError(f"L'espressione {expression} genera {total} operazioni (massimo {MAX_OPERATIONS}).",
                       code="assembly_too_large")
    return [tuple(combo) for combo in product(*lists)]


def compose(ops: Iterable[Operator]) -> tuple[np.ndarray, np.ndarray]:
    """Rotazione e traslazione della combinazione (da destra a sinistra): x' = R·x + t."""
    rot, vec = np.eye(3), np.zeros(3)
    for op in reversed(list(ops)):
        rot, vec = op.matrix @ rot, op.matrix @ vec + op.vector
    return rot, vec


# ---------------------------------------------------------------------------------------------- lettura mmCIF
def _column(data: dict, key: str) -> list[str]:
    value = data.get(key)
    if value is None:
        return []
    return list(value) if isinstance(value, list) else [value]


def _text(value: Optional[str]) -> str:
    return "" if value in (None, "?", ".") else str(value).strip()


def _int(value: Optional[str]) -> Optional[int]:
    try:
        return int(_text(value))
    except (TypeError, ValueError):
        return None


def from_mmcif_dict(data: dict) -> AssemblyInfo:
    """Assemblaggi, operatori e label_asym_id dei residui da un dizionario di `MMCIF2Dict`."""
    info = AssemblyInfo(format="mmcif", labels=True)
    ids = _column(data, "_pdbx_struct_oper_list.id")
    for k, oid in enumerate(ids):
        try:
            matrix = np.array([[float(_column(data, f"_pdbx_struct_oper_list.matrix[{i}][{j}]")[k])
                                for j in (1, 2, 3)] for i in (1, 2, 3)])
            vector = np.array([float(_column(data, f"_pdbx_struct_oper_list.vector[{i}]")[k]) for i in (1, 2, 3)])
        except (IndexError, ValueError) as exc:
            raise AppError(f"Operatore {oid} dell'assemblaggio non leggibile nel mmCIF ({exc}).",
                           code="invalid_assembly") from exc
        types = _column(data, "_pdbx_struct_oper_list.type")
        names = _column(data, "_pdbx_struct_oper_list.name")
        info.operators[str(oid)] = Operator(str(oid), matrix, vector, _text(types[k] if k < len(types) else ""),
                                            _text(names[k] if k < len(names) else ""))
    columns = {name: _column(data, f"_pdbx_struct_assembly.{name}")
               for name in ("id", "details", "method_details", "oligomeric_details", "oligomeric_count")}

    def cell(name: str, k: int) -> str:
        values = columns[name]
        return _text(values[k]) if k < len(values) else ""

    by_id: dict[str, Assembly] = {}
    for k, aid in enumerate(columns["id"]):
        by_id[str(aid)] = Assembly(id=str(aid), details=cell("details", k), method=cell("method_details", k),
                                   oligomeric_details=cell("oligomeric_details", k),
                                   oligomeric_count=_int(cell("oligomeric_count", k)))
    gen_ids = _column(data, "_pdbx_struct_assembly_gen.assembly_id")
    exprs = _column(data, "_pdbx_struct_assembly_gen.oper_expression")
    asyms = _column(data, "_pdbx_struct_assembly_gen.asym_id_list")
    for aid, expr, asym in zip(gen_ids, exprs, asyms):
        assembly = by_id.setdefault(str(aid), Assembly(id=str(aid)))
        assembly.generators.append(Generator(chains=[c.strip() for c in str(asym).split(",") if c.strip()],
                                             expression=str(expr)))
    info.assemblies = list(by_id.values())
    # label_asym_id dei residui del primo modello, con le stesse chiavi dei residui di Biopython (MMCIFParser)
    auth = _column(data, "_atom_site.auth_asym_id")
    label = _column(data, "_atom_site.label_asym_id")
    seq = _column(data, "_atom_site.auth_seq_id")
    icode = _column(data, "_atom_site.pdbx_PDB_ins_code")
    comp = _column(data, "_atom_site.label_comp_id")
    group = _column(data, "_atom_site.group_PDB")
    models = _column(data, "_atom_site.pdbx_PDB_model_num")
    first = models[0] if models else None
    for k in range(min(len(auth), len(label), len(seq), len(comp))):
        if models and k < len(models) and models[k] != first:
            continue
        resname = str(comp[k])
        code = str(icode[k]) if k < len(icode) else "?"
        hetflag = " "
        if k < len(group) and group[k] == "HETATM":
            hetflag = "W" if resname in ("HOH", "WAT") else f"H_{resname}"
        try:
            rid = (hetflag, int(seq[k]), " " if code in ("?", ".") else code)
        except ValueError:
            continue
        info.residue_labels.setdefault((str(auth[k]), rid), str(label[k]))
    if not info.assemblies:
        info.note = "Il file mmCIF non dichiara assemblaggi biologici (pdbx_struct_assembly)."
    return info


# ---------------------------------------------------------------------------------------------- lettura PDB
def from_pdb_text(text: str) -> AssemblyInfo:
    """Assemblaggi dal REMARK 350 di un file PDB (catene dell'autore; operatori propri di ogni gruppo di catene)."""
    info = AssemblyInfo(format="pdb", labels=False)
    current: Optional[Assembly] = None
    generator: Optional[Generator] = None
    rows: dict[str, dict[int, list[float]]] = {}
    serials: list[str] = []

    def close_generator() -> None:
        nonlocal generator, rows, serials
        if generator is None or current is None:
            return
        keys = []
        for serial in serials:
            parts = rows.get(serial, {})
            if set(parts) != {1, 2, 3}:
                raise AppError(f"BIOMT {serial} incompleto nel REMARK 350 (biomolecola {current.id}).",
                               code="invalid_assembly")
            key = f"{current.id}.{len(current.generators) + 1}.{serial}"
            matrix = np.array([parts[i][:3] for i in (1, 2, 3)])
            vector = np.array([parts[i][3] for i in (1, 2, 3)])
            info.operators[key] = Operator(serial, matrix, vector)
            keys.append((key,))
        if keys:
            generator.operations = keys
            generator.expression = ",".join(serials)
            current.generators.append(generator)
        generator, rows, serials = None, {}, []

    for line in (text or "").splitlines():
        if not line.startswith("REMARK 350"):
            continue
        body = line[10:].strip()
        upper = body.upper()
        if upper.startswith("BIOMOLECULE:"):
            close_generator()
            current = Assembly(id=body.split(":", 1)[1].strip() or str(len(info.assemblies) + 1))
            info.assemblies.append(current)
        elif current is None:
            continue
        elif upper.startswith("AUTHOR DETERMINED BIOLOGICAL UNIT:"):
            current.details = "author_defined_assembly"
            current.oligomeric_details = body.split(":", 1)[1].strip().lower()
        elif upper.startswith("SOFTWARE DETERMINED QUATERNARY STRUCTURE:"):
            if not current.details:
                current.details = "software_defined_assembly"
                current.oligomeric_details = body.split(":", 1)[1].strip().lower()
        elif upper.startswith("SOFTWARE USED:"):
            current.method = body.split(":", 1)[1].strip()
        elif upper.startswith("APPLY THE FOLLOWING TO CHAINS:"):
            close_generator()
            generator = Generator(chains=[], expression="")
            generator.chains += [c.strip() for c in body.split(":", 1)[1].split(",") if c.strip()]
        elif upper.startswith("AND CHAINS:") and generator is not None:
            generator.chains += [c.strip() for c in body.split(":", 1)[1].split(",") if c.strip()]
        elif upper.startswith("BIOMT") and generator is not None:
            parts = body.split()
            try:
                row, serial = int(parts[0][5]), parts[1]
                values = [float(x) for x in parts[2:6]]
            except (IndexError, ValueError) as exc:
                raise AppError(f"Riga BIOMT non leggibile nel REMARK 350: {body}", code="invalid_assembly") from exc
            if serial not in rows:
                rows[serial] = {}
                serials.append(serial)
            rows[serial][row] = values
    close_generator()
    info.assemblies = [a for a in info.assemblies if a.generators]
    if not info.assemblies:
        info.note = "Il file PDB non dichiara assemblaggi biologici (REMARK 350 con BIOMT)."
    return info


def read(path: Path) -> AssemblyInfo:
    """Assemblaggi dichiarati nel file del template (mmCIF o PDB)."""
    path = Path(path)
    if path.suffix.lower() in (".cif", ".mmcif"):
        from Bio.PDB.MMCIF2Dict import MMCIF2Dict

        try:
            data = MMCIF2Dict(str(path))
        except Exception as exc:  # file corrotto
            raise AppError(f"Impossibile leggere il mmCIF {path.name}: {exc}", code="invalid_structure") from exc
        return from_mmcif_dict(data)
    return from_pdb_text(read_text_head(path, 50_000_000))


# ---------------------------------------------------------------------------------------------- piano delle copie
def _operations(info: AssemblyInfo, generator: Generator) -> list[tuple[str, ...]]:
    if generator.operations:
        return generator.operations
    combos = parse_oper_expression(generator.expression)
    missing = sorted({o for combo in combos for o in combo if o not in info.operators})
    if missing:
        raise AppError(f"Operatori {', '.join(missing)} dell'assemblaggio non definiti nel file "
                       "(pdbx_struct_oper_list).", code="invalid_assembly")
    return combos


def find(info: AssemblyInfo, assembly_id: str) -> Assembly:
    wanted = str(assembly_id).strip()
    for a in info.assemblies:
        if a.id == wanted:
            return a
    available = ", ".join(a.id for a in info.assemblies) or "nessuno"
    raise AppError(f"Assemblaggio {wanted} non presente nel file (disponibili: {available}).",
                   code="assembly_not_found", status=404)


def plan(model, info: AssemblyInfo, assembly: Assembly) -> list[dict]:
    """Copie dell'assemblaggio, nell'ordine di generazione: {"chain" (nuovo nome), "source" (catena dell'autore),
    "operators" (combinazione), "operator" (testo, es. "2" o "1×61"), "residues" (residui di Biopython),
    "protein" (con amminoacidi)}. Errore chiaro oltre `MAX_CHAINS` catene."""
    copies = []
    for generator in assembly.generators:
        combos = _operations(info, generator)
        wanted = set(generator.chains)
        for combo in combos:
            for chain in model:
                if info.labels:
                    residues = [r for r in chain if info.residue_labels.get((chain.id, r.id)) in wanted]
                else:
                    residues = list(chain) if chain.id in wanted else []
                if not residues:
                    continue
                copies.append({"source": chain.id, "operators": combo, "residues": residues,
                               "operator": "×".join(info.operators[o].id for o in combo),
                               "identity": all(info.operators[o].is_identity() for o in combo),
                               "protein": any(is_amino_residue(r) for r in residues)})
    if not copies:
        raise AppError(f"L'assemblaggio {assembly.id} non corrisponde a nessuna catena del file.",
                       code="invalid_assembly")
    if len(copies) > MAX_CHAINS:
        raise AppError(f"L'assemblaggio {assembly.id} ha {len(copies)} catene: il formato PDB (e MODELLER) ne ammette "
                       f"al massimo {MAX_CHAINS} con identificativi di un carattere. Usa un assemblaggio più piccolo o "
                       "le sole catene dell'unità asimmetrica.", code="assembly_too_large")
    # la prima copia di ogni catena tiene il nome (se di un carattere); le altre i primi caratteri liberi
    keep: dict[int, str] = {}
    seen: set[str] = set()
    for k, c in enumerate(copies):
        if c["source"] not in seen:
            seen.add(c["source"])
            if len(c["source"]) == 1 and c["source"] in CHAIN_IDS:
                keep[k] = c["source"]
    free = iter([x for x in CHAIN_IDS if x not in set(keep.values())])
    for k, c in enumerate(copies):
        c["chain"] = keep[k] if k in keep else next(free)
    return copies


def _chain_row(copy: dict) -> dict:
    """Catena dell'assemblaggio per i risultati: nome, origine, operatore, se l'operatore è l'identità (coordinate del
    file) e se ha amminoacidi."""
    return {k: copy[k] for k in ("chain", "source", "operator", "identity", "protein")}


def _accepted_atoms(residue) -> list:
    """Atomi della prima posizione alternativa (A, ' ' o 1), uno per nome, come `structure._Selector`."""
    out, names = [], set()
    for atom in residue.get_unpacked_list():
        if atom.is_disordered() and atom.get_altloc() not in ("A", " ", "1"):
            continue
        if atom.get_name() in names:
            continue
        names.add(atom.get_name())
        out.append(atom)
    return out


def summary(model, info: AssemblyInfo, assembly: Assembly) -> dict:
    """Descrizione di un assemblaggio per la libreria: stato oligomerico, catene generate e loro origine."""
    base = {"id": assembly.id, "details": assembly.details, "method": assembly.method,
            "oligomeric_details": assembly.oligomeric_details, "oligomeric_count": assembly.oligomeric_count,
            "generators": [{"chains": g.chains, "operators": g.expression} for g in assembly.generators]}
    try:
        copies = plan(model, info, assembly)
    except AppError as exc:
        return {**base, "chains": [], "chain_count": 0, "protein_chains": 0, "buildable": False, "note": exc.message}
    atoms = sum(len(_accepted_atoms(r)) for c in copies for r in c["residues"])
    note = ""
    buildable = True
    if atoms > MAX_ATOMS:
        buildable = False
        note = (f"{atoms} atomi: oltre il massimo del formato PDB ({MAX_ATOMS}), l'assemblaggio non si può importare "
                "come template.")
    identity = all(c["identity"] for c in copies)
    return {**base, "chains": [_chain_row(c) for c in copies],
            "chain_count": len(copies), "protein_chains": sum(1 for c in copies if c["protein"]),
            "source_chains": sorted({c["source"] for c in copies}), "atoms": atoms,
            "identity_only": identity, "buildable": buildable, "note": note}


def describe(path: Path) -> dict:
    """Assemblaggi del file per la libreria: {"format", "assemblies": [summary], "note"}."""
    info = read(path)
    model = first_model(load_structure(path, Path(path).stem))
    return {"format": info.format, "assemblies": [summary(model, info, a) for a in info.assemblies],
            "note": info.note}


# ---------------------------------------------------------------------------------------------- coordinate
def build(model, info: AssemblyInfo, assembly: Assembly):
    """Struttura di Biopython dell'assemblaggio (un modello, una catena per copia) e le copie del piano."""
    from Bio.PDB.Atom import Atom
    from Bio.PDB.Chain import Chain
    from Bio.PDB.Model import Model
    from Bio.PDB.Residue import Residue
    from Bio.PDB.Structure import Structure

    copies = plan(model, info, assembly)
    atoms = sum(len(_accepted_atoms(r)) for c in copies for r in c["residues"])
    if atoms > MAX_ATOMS:
        raise AppError(f"L'assemblaggio {assembly.id} ha {atoms} atomi: il formato PDB ne numera al massimo "
                       f"{MAX_ATOMS}.", code="assembly_too_large")
    out = Structure("assembly")
    new_model = Model(0)
    out.add(new_model)
    for c in copies:
        rot, vec = compose(info.operators[o] for o in c["operators"])
        chain = Chain(c["chain"])
        new_model.add(chain)
        for res in c["residues"]:
            copy = Residue(res.id, res.get_resname(), res.segid)
            for atom in _accepted_atoms(res):
                xyz = rot @ np.asarray(atom.coord, dtype=float) + vec
                copy.add(Atom(atom.get_name(), xyz.astype("f"), atom.get_bfactor(), atom.get_occupancy(), " ",
                              atom.get_fullname(), None, atom.element))
            chain.add(copy)
    return out, copies


def pdb_text(model, info: AssemblyInfo, assembly: Assembly, *, source_id: str,
             header: Optional[dict] = None) -> tuple[str, list[dict]]:
    """Testo PDB dell'assemblaggio con l'intestazione (titolo, metodo e risoluzione del file d'origine) e le righe
    `REMARK 999 MAKPHI ASSEMBLY` con l'origine di ogni catena; restituisce anche le catene [{chain, source,
    operator, protein}]."""
    from Bio.PDB import PDBIO

    structure, copies = build(model, info, assembly)
    header = header or {}
    lines = [f"HEADER    BIOLOGICAL ASSEMBLY {assembly.id} OF {source_id}"[:80]]
    title = str(header.get("title") or "").upper()
    for k in range(0, min(len(title), 280), 70):
        lines.append((f"TITLE     {title[k:k + 70]}" if k == 0 else f"TITLE   {k // 70 + 1:>2} {title[k:k + 70]}")[:80])
    if header.get("method"):
        lines.append(f"EXPDTA    {str(header['method']).upper()}"[:80])
    if header.get("resolution"):
        lines.append(f"REMARK   2 RESOLUTION.    {float(header['resolution']):.2f} ANGSTROMS."[:80])
    state = assembly.oligomeric_details or f"{sum(1 for c in copies if c['protein'])} catene proteiche"
    lines.append(f"REMARK 999 MAKPHI ASSEMBLY {assembly.id} OF {source_id} {state.upper()} "
                 f"CHAINS {len(copies)}"[:80])
    for c in copies:
        lines.append(f"REMARK 999 MAKPHI ASSEMBLY CHAIN {c['chain']} FROM {c['source']} OPERATOR "
                     f"{c['operator']}"[:80])
    buf = io.StringIO()
    writer = PDBIO()
    writer.set_structure(structure)
    writer.save(buf)
    text = "\n".join(line.ljust(80) for line in lines) + "\n" + buf.getvalue()
    return text, [_chain_row(c) for c in copies]
