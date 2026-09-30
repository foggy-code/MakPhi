"""Strutture 3D: caricamento PDB/mmCIF, catene, residui mancanti, ligandi, filtri ed esportazione."""

from __future__ import annotations

import io
import re
import warnings
from pathlib import Path
from typing import Iterable, Optional

from Bio.PDB.PDBExceptions import PDBConstructionWarning

from ..core.errors import AppError

# Gli avvisi di costruzione di Biopython (record non riconosciuti, atomi incompleti) si ignorano sempre, anche con un
# filtro permanente: `warnings.catch_warnings` di `load_structure` non è sicuro fra thread (Python < 3.14) e con più
# letture insieme (elementi di pipeline, modelli analizzati in parallelo) un avviso poteva sfuggire.
warnings.filterwarnings("ignore", category=PDBConstructionWarning)

WATER_NAMES = {"HOH", "WAT", "DOD", "H2O", "TIP", "TIP3", "SOL"}
# Ioni e additivi di cristallizzazione esclusi dall'elenco "ligandi di interesse".
COMMON_ADDITIVES = {"SO4", "PO4", "GOL", "EDO", "ACT", "FMT", "CL", "NA", "K", "PEG", "DMS", "IOD", "BR", "NO3"}


def three_to_one(resname: str) -> str:
    from Bio.Data.PDBData import protein_letters_3to1_extended

    return protein_letters_3to1_extended.get(resname.strip().upper(), "X")


def is_amino_residue(residue) -> bool:
    return residue.id[0] == " " and "CA" in residue


def load_structure(path: Path, structure_id: str = "structure"):
    """Carica un file PDB o mmCIF e restituisce l'oggetto Structure di Biopython."""
    from Bio.PDB import MMCIFParser, PDBParser

    path = Path(path)
    suffix = path.suffix.lower()
    parser = MMCIFParser(QUIET=True) if suffix in (".cif", ".mmcif") else PDBParser(PERMISSIVE=True, QUIET=True)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", PDBConstructionWarning)
        try:
            structure = parser.get_structure(structure_id, str(path))
        except Exception as exc:  # file corrotto o formato errato
            raise AppError(f"Impossibile leggere la struttura {path.name}: {exc}", code="invalid_structure")
    if len(structure) == 0:
        raise AppError(f"{path.name} non contiene modelli 3D.", code="invalid_structure")
    return structure


def first_model(structure):
    return next(iter(structure))


def chain_summaries(model) -> list[dict]:
    """Per ogni catena: intervallo di numerazione, sequenza (solo residui ATOM), interruzioni."""
    out = []
    for chain in model:
        residues = [r for r in chain if is_amino_residue(r)]
        if not residues:
            continue
        seq = "".join(three_to_one(r.get_resname()) for r in residues)
        breaks = []
        for prev, cur in zip(residues, residues[1:]):
            if "C" in prev and "N" in cur and (prev["C"] - cur["N"]) > 2.0:
                breaks.append({"after": _resid(prev), "before": _resid(cur)})
        hetero = [r for r in chain if r.id[0] not in (" ", "W") and r.get_resname().strip() not in WATER_NAMES]
        out.append({
            "chain": chain.id,
            "first": _resid(residues[0]),
            "last": _resid(residues[-1]),
            "residues": len(residues),
            "sequence": seq,
            "breaks": breaks,
            "ligands": len(hetero),
        })
    return out


def _resid(residue) -> str:
    _, num, icode = residue.id
    return f"{num}{icode.strip()}"


def hetero_groups(model) -> list[dict]:
    groups = []
    for chain in model:
        for res in chain:
            name = res.get_resname().strip()
            if res.id[0] == " " or name in WATER_NAMES or res.id[0] == "W":
                continue
            groups.append({"resname": name, "chain": chain.id, "resnum": _resid(res),
                           "atoms": len(res), "additive": name in COMMON_ADDITIVES})
    return groups


def parse_header(text: str) -> dict:
    """Estrae dall'header PDB: titolo, metodo, risoluzione, residui mancanti (REMARK 465), SEQRES, HELIX/SHEET."""
    title_parts: list[str] = []
    method = ""
    resolution: Optional[float] = None
    missing: list[dict] = []
    seqres: dict[str, list[str]] = {}
    ss: list[dict] = []
    in_465 = False
    for line in text.splitlines():
        rec = line[:6]
        if rec == "TITLE ":
            title_parts.append(line[10:].strip())
        elif rec == "EXPDTA":
            method = line[10:].strip()
        elif line.startswith("REMARK   2 RESOLUTION."):
            m = re.search(r"([\d.]+)\s+ANGSTROM", line)
            if m:
                resolution = float(m.group(1))
        elif line.startswith("REMARK 465"):
            body = line[10:].strip()
            if body.startswith("M RES C SSSEQI"):
                in_465 = True
                continue
            if in_465:
                parts = body.split()
                if len(parts) >= 3 and parts[-1].lstrip("-").rstrip("ABCDEFGHIJKLMNOPQRSTUVWXYZ").isdigit():
                    # formato: [modello] RES CATENA NUM[icode]
                    resname, chain, num = parts[-3], parts[-2], parts[-1]
                    if len(resname) <= 3 and len(chain) == 1:
                        missing.append({"resname": resname, "chain": chain, "resnum": num})
        elif rec == "SEQRES":
            chain = line[11].strip()
            seqres.setdefault(chain, []).extend(line[19:].split())
        elif rec == "HELIX ":
            try:
                ss.append({"type": "helix", "chain": line[19].strip(), "start": int(line[21:25]),
                           "end": int(line[33:37])})
            except ValueError:
                pass
        elif rec == "SHEET ":
            try:
                ss.append({"type": "strand", "chain": line[21].strip(), "start": int(line[22:26]),
                           "end": int(line[33:37])})
            except ValueError:
                pass
    return {
        "title": " ".join(title_parts).strip(),
        "method": method,
        "resolution": resolution,
        "missing_residues": missing,
        "seqres": {c: "".join(three_to_one(r) for r in names) for c, names in seqres.items()},
        "secondary_structure": ss,
    }


def parse_cif_header(path: Path) -> dict:
    """Metadati minimi da un file mmCIF (titolo, metodo, risoluzione)."""
    from Bio.PDB.MMCIF2Dict import MMCIF2Dict

    try:
        data = MMCIF2Dict(str(path))
    except Exception:
        return {"title": "", "method": "", "resolution": None, "missing_residues": [], "seqres": {},
                "secondary_structure": []}

    def first(key: str) -> str:
        value = data.get(key)
        if isinstance(value, list):
            value = value[0] if value else ""
        return "" if value in (None, "?", ".") else str(value)

    res = first("_refine.ls_d_res_high") or first("_em_3d_reconstruction.resolution")
    try:
        resolution = float(res) if res else None
    except ValueError:
        resolution = None
    missing = []
    names = data.get("_pdbx_unobs_or_zero_occ_residues.auth_comp_id") or []
    chains = data.get("_pdbx_unobs_or_zero_occ_residues.auth_asym_id") or []
    nums = data.get("_pdbx_unobs_or_zero_occ_residues.auth_seq_id") or []
    for n, c, s in zip(names, chains, nums):
        missing.append({"resname": n, "chain": c, "resnum": s})
    return {"title": first("_struct.title"), "method": first("_exptl.method"), "resolution": resolution,
            "missing_residues": missing, "seqres": {}, "secondary_structure": []}


class _Selector:
    """Selettore per PDBIO: catene scelte, ligandi facoltativi, niente acqua, prima altloc, residui esclusi."""

    def __init__(self, chains: Optional[set[str]], keep_hetero: bool | set[str], keep_water: bool,
                 exclude: Optional[set] = None) -> None:
        self.chains = chains
        self.keep_hetero = keep_hetero
        self.keep_water = keep_water
        self.exclude = exclude or set()

    def accept_model(self, model) -> bool:
        return model.id == first_model(model.get_parent()).id

    def accept_chain(self, chain) -> bool:
        return not self.chains or chain.id in self.chains

    def accept_residue(self, residue) -> bool:
        flag = residue.id[0]
        name = residue.get_resname().strip()
        if self.exclude and (residue.get_parent().id, residue.id) in self.exclude:
            return False
        if flag == " ":
            return True
        if flag == "W" or name in WATER_NAMES:
            return self.keep_water
        if isinstance(self.keep_hetero, set):
            return name in self.keep_hetero
        return bool(self.keep_hetero)

    def accept_atom(self, atom) -> bool:
        return (not atom.is_disordered()) or atom.get_altloc() in ("A", " ", "1")


def write_pdb(structure, dest: Path, chains: Optional[Iterable[str]] = None,
              keep_hetero: bool | set[str] = False, keep_water: bool = False,
              exclude: Optional[set] = None) -> None:
    """Scrive un PDB filtrato (catene, ligandi) — usato anche per convertire mmCIF in PDB.

    `exclude`: residui da togliere, come coppie (catena, id Biopython del residuo), es. quelli a basso pLDDT.
    """
    from Bio.PDB import PDBIO

    chain_set = {c for c in chains if c} if chains else None
    if chain_set:
        available = {c.id for c in first_model(structure)}
        missing = chain_set - available
        if missing:
            raise AppError(f"Catene non presenti nella struttura: {', '.join(sorted(missing))}",
                           code="invalid_chain")
        too_long = [c for c in chain_set if len(c) > 1]
        if too_long:
            raise AppError("Il formato PDB supporta solo identificativi di catena di un carattere.")
    io_ = PDBIO()
    io_.set_structure(structure)
    io_.save(str(dest), select=_Selector(chain_set, keep_hetero, keep_water, exclude))


def pdb_text(structure, **kwargs) -> str:
    import tempfile
    import os

    fd, tmp = tempfile.mkstemp(suffix=".pdb")
    os.close(fd)
    try:
        write_pdb(structure, Path(tmp), **kwargs)
        return Path(tmp).read_text(encoding="utf-8")
    finally:
        os.remove(tmp)


def ca_trace(model, chain: Optional[str] = None) -> list[dict]:
    """Coordinate dei C-alfa (catena opzionale) con residuo e lettera."""
    out = []
    for ch in model:
        if chain and ch.id != chain:
            continue
        for res in ch:
            if is_amino_residue(res):
                out.append({"chain": ch.id, "resnum": _resid(res), "aa": three_to_one(res.get_resname()),
                            "coord": res["CA"].coord.tolist(), "residue": res})
    return out


def structure_stats(model) -> dict:
    residues = [r for r in model.get_residues() if is_amino_residue(r)]
    atoms = [a for a in model.get_atoms() if a.element not in ("H", "D")]
    return {"chains": len([c for c in model if any(is_amino_residue(r) for r in c)]),
            "residues": len(residues), "heavy_atoms": len(atoms)}


def read_text_head(path: Path, limit: int = 2_000_000) -> str:
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        return fh.read(limit)


def string_io(text: str):
    return io.StringIO(text)
