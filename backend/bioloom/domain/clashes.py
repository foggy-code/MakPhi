"""Clashscore approssimato sugli atomi pesanti.

Differenze rispetto alla v3 (che sovrastimava i clash):
  * le coppie a distanza topologica <= 3 legami covalenti sono escluse (grafo dei legami
    ricostruito dai raggi covalenti): niente falsi positivi su anello della prolina, ponti
    disolfuro, metalli coordinati;
  * le molecole d'acqua sono escluse;
  * le coppie polari N/O ricevono la tolleranza di un legame idrogeno.
MolProbity usa anche gli idrogeni espliciti: i valori sono confrontabili solo in ordine di grandezza.
"""

from __future__ import annotations

from collections import defaultdict

from .structure import WATER_NAMES, _resid

# Raggi di van der Waals di Probe (Word et al. 1999) per C/N/O/S; carbonio carbonilico a parte.
VDW_RADII = {
    "C": 1.70, "N": 1.55, "O": 1.40, "S": 1.80, "P": 1.80, "F": 1.47, "CL": 1.75, "BR": 1.85, "I": 1.98,
    "SE": 1.90, "FE": 1.47, "ZN": 1.39, "MG": 1.73, "CA": 1.74, "MN": 1.61, "CU": 1.40, "NA": 2.27, "K": 2.75,
}
COVALENT_RADII = {
    "C": 0.76, "N": 0.71, "O": 0.66, "S": 1.05, "P": 1.07, "F": 0.57, "CL": 1.02, "BR": 1.20, "I": 1.39,
    "SE": 1.20, "FE": 1.32, "ZN": 1.22, "MG": 1.41, "CA": 1.76, "MN": 1.39, "CU": 1.32, "NA": 1.66, "K": 2.03,
}
CARBONYL_C_RADIUS = 1.65
POLAR = {"N", "O"}
OVERLAP_CUTOFF = 0.4
HBOND_TOLERANCE = 0.4


def _element(atom) -> str:
    el = (atom.element or "").strip().upper()
    if el and el != "X":
        return el
    name = atom.get_id().strip().lstrip("0123456789").upper()
    if name == atom.get_parent().get_resname().strip().upper() and name in VDW_RADII:
        return name  # ione isolato (ZN, MG, ...)
    return name[:1]


def _label(atom) -> str:
    res = atom.get_parent()
    return f"{res.get_parent().id} {_resid(res)} {res.get_resname()} {atom.get_id()}"


def _within_bonds(a: int, b: int, graph: dict[int, set[int]], depth: int = 3) -> bool:
    frontier = {a}
    seen = {a}
    for _ in range(depth):
        nxt = set()
        for node in frontier:
            nxt |= graph.get(node, set())
        if b in nxt:
            return True
        nxt -= seen
        seen |= nxt
        frontier = nxt
        if not frontier:
            break
    return False


def analyze(model, max_listed: int = 100) -> dict:
    from Bio.PDB import NeighborSearch

    atoms = []
    radii = []
    for atom in model.get_atoms():
        res = atom.get_parent()
        if res.id[0] == "W" or res.get_resname().strip() in WATER_NAMES:
            continue
        el = _element(atom)
        if el in ("H", "D", ""):
            continue
        atoms.append((atom, el))
        backbone_carbonyl = el == "C" and atom.get_id() == "C" and res.id[0] == " "
        radii.append(CARBONYL_C_RADIUS if backbone_carbonyl else VDW_RADII.get(el, 1.70))
    if not atoms:
        return {"clashscore": 0.0, "count": 0, "atoms": 0, "clashes": []}

    index = {id(a): i for i, (a, _el) in enumerate(atoms)}
    elements = [el for _a, el in atoms]
    ns = NeighborSearch([a for a, _el in atoms])
    pairs = ns.search_all(4.2)

    graph: dict[int, set[int]] = defaultdict(set)
    candidates = []
    for a1, a2 in pairs:
        i, j = index[id(a1)], index[id(a2)]
        d = float(a1 - a2)
        e1, e2 = elements[i], elements[j]
        if d < COVALENT_RADII.get(e1, 0.77) + COVALENT_RADII.get(e2, 0.77) + 0.45:
            graph[i].add(j)
            graph[j].add(i)
            continue
        overlap = radii[i] + radii[j] - d
        limit = OVERLAP_CUTOFF + (HBOND_TOLERANCE if e1 in POLAR and e2 in POLAR else 0.0)
        if overlap > limit:
            candidates.append((i, j, d, overlap))

    clashes = []
    for i, j, d, overlap in candidates:
        if _within_bonds(i, j, graph):
            continue
        clashes.append({"atom1": _label(atoms[i][0]), "atom2": _label(atoms[j][0]),
                        "distance": round(d, 2), "overlap": round(overlap, 2)})
    clashes.sort(key=lambda c: -c["overlap"])
    score = 1000.0 * len(clashes) / len(atoms)
    return {"clashscore": round(score, 2), "count": len(clashes), "atoms": len(atoms),
            "clashes": clashes[:max_listed]}
