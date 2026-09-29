"""Angoli diedri del backbone e classificazione di Ramachandran.

Le regioni sono rettangoli (φ_min, φ_max, ψ_min, ψ_max) in gradi. Rispetto alla v3 la regione
favorita del caso generale include la poliprolina II e il bordo dell'α-elica, in linea con le aree
favorite di MolProbity (Lovell et al. 2003). Restano un'approssimazione dei contorni reali: per una
validazione formale usare MolProbity o PROCHECK.
"""

from __future__ import annotations

import numpy as np

from .geometry import dihedral
from .structure import is_amino_residue, three_to_one, _resid

Rect = tuple[float, float, float, float]

REGIONS: dict[str, dict[str, list[Rect]]] = {
    "general": {
        "favored": [
            (-115, -40, -75, -5),     # α destrorsa
            (-135, -55, -5, 20),      # ponte α-β adiacente all'elica
            (-180, -45, 100, 180),    # foglietto β e poliprolina II
            (-180, -45, -180, -170),  # continuazione periodica di β
            (-170, -100, 80, 100),    # bordo inferiore di β
            (45, 80, 10, 70),         # α sinistrorsa
        ],
        "allowed": [
            (-180, -35, -100, 180),
            (-180, -45, -180, -150),
            (35, 105, -35, 110),
        ],
    },
    "glycine": {
        "favored": [
            (-180, -60, 60, 180),
            (60, 180, -180, -60),
            (-120, -40, -80, 10),
            (40, 120, -10, 80),
        ],
        "allowed": [
            (-180, -30, -180, 180),
            (30, 180, -180, 180),
            (-30, 30, 30, 180),
            (-30, 30, -180, -30),
        ],
    },
    "proline": {
        "favored": [
            (-95, -45, -65, -5),
            (-95, -45, 100, 180),
            (-95, -45, -180, -170),
        ],
        "allowed": [
            (-110, -40, -90, 60),
            (-110, -40, 60, 180),
            (-110, -40, -180, -150),
        ],
    },
}


def residue_class(resname: str, next_resname: str | None = None) -> str:
    name = (resname or "").upper()
    if name == "GLY":
        return "glycine"
    if name == "PRO":
        return "proline"
    return "prepro" if (next_resname or "").upper() == "PRO" else "general"


def _inside(phi: float, psi: float, rects: list[Rect]) -> bool:
    return any(a <= phi <= b and c <= psi <= d for a, b, c, d in rects)


def classify(phi: float, psi: float, rclass: str = "general") -> str:
    """Restituisce 'favored', 'allowed' o 'outlier' (angoli in gradi)."""
    table = REGIONS["general" if rclass == "prepro" else rclass]
    if _inside(phi, psi, table["favored"]):
        return "favored"
    if _inside(phi, psi, table["allowed"]):
        return "allowed"
    return "outlier"


def backbone_dihedrals(model) -> list[dict]:
    """φ, ψ, ω per ogni residuo amminoacidico, rispettando le interruzioni di catena."""
    rows = []
    for chain in model:
        residues = [r for r in chain if is_amino_residue(r) and all(k in r for k in ("N", "CA", "C"))]
        for i, res in enumerate(residues):
            prev = residues[i - 1] if i > 0 else None
            nxt = residues[i + 1] if i + 1 < len(residues) else None
            n, ca, c = res["N"].coord, res["CA"].coord, res["C"].coord
            phi = psi = omega = None
            if prev is not None and np.linalg.norm(prev["C"].coord - n) < 2.0:
                phi = dihedral(prev["C"].coord, n, ca, c)
            if nxt is not None and np.linalg.norm(c - nxt["N"].coord) < 2.0:
                psi = dihedral(n, ca, c, nxt["N"].coord)
                omega = dihedral(ca, c, nxt["N"].coord, nxt["CA"].coord)
            rows.append({
                "chain": chain.id,
                "resnum": _resid(res),
                "resname": res.get_resname(),
                "aa": three_to_one(res.get_resname()),
                "phi": None if phi is None else round(phi, 1),
                "psi": None if psi is None else round(psi, 1),
                "omega": None if omega is None else round(omega, 1),
                "next_resname": nxt.get_resname() if nxt is not None else None,
            })
    return rows


def analyze(model) -> dict:
    rows = backbone_dihedrals(model)
    counts = {"favored": 0, "allowed": 0, "outlier": 0}
    points = []
    cis_non_pro = 0
    for row in rows:
        if row["omega"] is not None and abs(row["omega"]) < 30 and (row["next_resname"] or "").upper() != "PRO":
            cis_non_pro += 1
        if row["phi"] is None or row["psi"] is None:
            continue
        rclass = residue_class(row["resname"], row["next_resname"])
        region = classify(row["phi"], row["psi"], rclass)
        counts[region] += 1
        points.append({**{k: row[k] for k in ("chain", "resnum", "resname", "aa", "phi", "psi")},
                       "class": rclass, "region": region})
    total = sum(counts.values())
    pct = {k: round(100.0 * v / total, 1) if total else 0.0 for k, v in counts.items()}
    return {
        "summary": {"total": total, **counts, "favored_pct": pct["favored"], "allowed_pct": pct["allowed"],
                    "outlier_pct": pct["outlier"], "cis_non_proline": cis_non_pro},
        "points": points,
        "outliers": [p for p in points if p["region"] == "outlier"],
        "regions": REGIONS,
    }
