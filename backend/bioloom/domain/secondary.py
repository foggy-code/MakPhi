"""Struttura secondaria: assegnazione DSSP integrata (Kabsch & Sander 1983) e predizione GOR euristica.

L'implementazione DSSP integrata copre legami H elettrostatici, turn, eliche α/3₁₀/π, ponti e
ladder β (senza gestione dei β-bulge), bend. È usata quando `mkdssp` non è installato.
"""

from __future__ import annotations

import numpy as np

from .structure import is_amino_residue, three_to_one, _resid

HBOND_CUTOFF = -0.5  # kcal/mol
Q1Q2F = 0.084 * 332.0
EIGHT_TO_THREE = {"H": "H", "G": "H", "I": "H", "E": "E", "B": "E", "T": "C", "S": "C", "-": "C", " ": "C"}
SS_LABELS = {
    "H": "α-elica", "G": "elica 3₁₀", "I": "π-elica", "E": "filamento β", "B": "ponte β isolato",
    "T": "turn", "S": "bend", "-": "coil",
}


def _backbone(model) -> list[dict]:
    residues: list[dict] = []
    for chain in model:
        prev = None
        for res in chain:
            if not is_amino_residue(res) or not all(k in res for k in ("N", "CA", "C", "O")):
                prev = None
                continue
            entry = {
                "chain": chain.id, "resnum": _resid(res), "resname": res.get_resname(),
                "aa": three_to_one(res.get_resname()),
                "N": res["N"].coord.astype(float), "CA": res["CA"].coord.astype(float),
                "C": res["C"].coord.astype(float), "O": res["O"].coord.astype(float), "H": None,
                "break": True,
            }
            if prev is not None and np.linalg.norm(prev["C"] - entry["N"]) < 2.5:
                entry["break"] = False
                if res.get_resname() != "PRO":
                    co = prev["C"] - prev["O"]
                    entry["H"] = entry["N"] + co / np.linalg.norm(co)
            residues.append(entry)
            prev = entry
    return residues


def _hbond_energy(acc: dict, don: dict) -> float:
    if don["H"] is None:
        return 0.0
    d_on = np.linalg.norm(acc["O"] - don["N"])
    d_ch = np.linalg.norm(acc["C"] - don["H"])
    d_oh = np.linalg.norm(acc["O"] - don["H"])
    d_cn = np.linalg.norm(acc["C"] - don["N"])
    if min(d_on, d_ch, d_oh, d_cn) < 0.5:
        return -9.9
    energy = Q1Q2F * (1.0 / d_on + 1.0 / d_ch - 1.0 / d_oh - 1.0 / d_cn)
    return max(energy, -9.9)


def assign(model) -> list[dict]:
    """Assegna gli stati DSSP a 8 classi a ogni residuo con backbone completo."""
    from Bio.PDB.kdtrees import KDTree

    res = _backbone(model)
    n = len(res)
    if n == 0:
        return []
    segment = np.zeros(n, dtype=int)
    seg = 0
    for i, r in enumerate(res):
        if r["break"] and i > 0:
            seg += 1
        segment[i] = seg

    def contiguous(i: int, j: int) -> bool:
        return 0 <= i < n and 0 <= j < n and segment[i] == segment[j]

    # Legami H: per ogni donatore (N-H) si tengono i due accettori (C=O) più favorevoli.
    ca = np.array([r["CA"] for r in res])
    tree = KDTree(ca, 10)
    best_acceptors: dict[int, list[tuple[float, int]]] = {}
    for pair in tree.neighbor_search(9.0):
        a, b = pair.index1, pair.index2
        for acc, don in ((a, b), (b, a)):
            if abs(acc - don) < 2:
                continue
            e = _hbond_energy(res[acc], res[don])
            if e < HBOND_CUTOFF:
                best_acceptors.setdefault(don, []).append((e, acc))
    hb = set()
    for don, lst in best_acceptors.items():
        for _e, acc in sorted(lst)[:2]:
            hb.add((acc, don))  # C=O(acc) ... H-N(don)

    def bond(acc: int, don: int) -> bool:
        return (acc, don) in hb

    ss = ["-"] * n
    # Turn e eliche
    turns = {k: np.zeros(n, dtype=bool) for k in (3, 4, 5)}
    for k in (3, 4, 5):
        for i in range(n - k):
            if contiguous(i, i + k) and bond(i, i + k):
                turns[k][i] = True
    helix = {k: np.zeros(n, dtype=bool) for k in (3, 4, 5)}
    for k in (3, 4, 5):
        for i in range(1, n - k):
            if turns[k][i - 1] and turns[k][i]:
                helix[k][i:i + k] = True

    # Ponti β
    bridges: dict[int, list[tuple[int, str]]] = {}
    for pair in tree.neighbor_search(9.0):
        i, j = sorted((pair.index1, pair.index2))
        if j - i < 3 or not (contiguous(i - 1, i + 1) and contiguous(j - 1, j + 1)):
            continue
        kind = None
        if (bond(i - 1, j) and bond(j, i + 1)) or (bond(j - 1, i) and bond(i, j + 1)):
            kind = "P"
        elif (bond(i, j) and bond(j, i)) or (bond(i - 1, j + 1) and bond(j - 1, i + 1)):
            kind = "A"
        if kind:
            bridges.setdefault(i, []).append((j, kind))
            bridges.setdefault(j, []).append((i, kind))

    def has_bridge(i: int, j: int, kind: str) -> bool:
        return any(p == j and k == kind for p, k in bridges.get(i, []))

    strand = np.zeros(n, dtype=bool)
    isolated = np.zeros(n, dtype=bool)
    for i, partners in bridges.items():
        in_ladder = False
        for j, kind in partners:
            neighbours = [(i + 1, j - 1), (i - 1, j + 1)] if kind == "A" else [(i + 1, j + 1), (i - 1, j - 1)]
            if any(has_bridge(a, b, kind) for a, b in neighbours):
                in_ladder = True
        if in_ladder:
            strand[i] = True
        else:
            isolated[i] = True

    # Bend: angolo CA(i-2)->CA(i) / CA(i)->CA(i+2) > 70°
    bend = np.zeros(n, dtype=bool)
    for i in range(2, n - 2):
        if contiguous(i - 2, i + 2):
            v1 = ca[i] - ca[i - 2]
            v2 = ca[i + 2] - ca[i]
            cosang = np.dot(v1, v2) / (np.linalg.norm(v1) * np.linalg.norm(v2))
            if np.degrees(np.arccos(np.clip(cosang, -1, 1))) > 70:
                bend[i] = True

    turn_res = np.zeros(n, dtype=bool)
    for k in (3, 4, 5):
        for i in np.where(turns[k])[0]:
            turn_res[i + 1:i + k] = True

    for i in range(n):  # priorità DSSP: H > B > E > G > I > T > S
        if helix[4][i]:
            ss[i] = "H"
        elif isolated[i] and not strand[i]:
            ss[i] = "B"
        elif strand[i]:
            ss[i] = "E"
        elif helix[3][i]:
            ss[i] = "G"
        elif helix[5][i]:
            ss[i] = "I"
        elif turn_res[i]:
            ss[i] = "T"
        elif bend[i]:
            ss[i] = "S"
    return [{"chain": r["chain"], "resnum": r["resnum"], "resname": r["resname"], "aa": r["aa"],
             "ss": ss[i], "ss3": EIGHT_TO_THREE[ss[i]]} for i, r in enumerate(res)]


def summarize(assignment: list[dict]) -> dict:
    counts = {"H": 0, "E": 0, "C": 0}
    detail: dict[str, int] = {}
    for row in assignment:
        counts[row["ss3"]] += 1
        detail[row["ss"]] = detail.get(row["ss"], 0) + 1
    total = sum(counts.values()) or 1
    return {"helix_pct": round(100 * counts["H"] / total, 1), "strand_pct": round(100 * counts["E"] / total, 1),
            "coil_pct": round(100 * counts["C"] / total, 1), "counts": counts, "detail": detail}


def segments(ss3: str) -> list[dict]:
    """Converte una stringa H/E/C in segmenti (inizio/fine 1-based) di eliche e filamenti."""
    out = []
    i, n = 0, len(ss3)
    while i < n:
        j = i
        while j < n and ss3[j] == ss3[i]:
            j += 1
        if ss3[i] in "HE":
            out.append({"type": "helix" if ss3[i] == "H" else "strand", "start": i + 1, "end": j})
        i = j
    return out


# ------------------------------------------------------------------ predizione da sequenza (euristica)
_GOR = {
    "H": {"A": 0.5, "L": 0.4, "M": 0.4, "E": 0.5, "K": 0.3, "R": 0.2, "H": 0.1, "I": 0.2, "V": 0.1, "F": 0.2,
          "W": 0.1, "Y": -0.1, "S": -0.2, "T": -0.1, "N": -0.2, "D": 0.1, "Q": 0.3, "C": -0.1, "G": -0.5, "P": -0.7},
    "E": {"V": 0.6, "I": 0.6, "L": 0.3, "F": 0.4, "Y": 0.4, "W": 0.3, "T": 0.3, "C": 0.2, "M": 0.1, "A": -0.2,
          "R": -0.1, "N": -0.1, "D": -0.4, "Q": -0.1, "E": -0.5, "G": -0.2, "H": -0.1, "K": -0.2, "P": -0.3, "S": -0.1},
    "C": {"G": 0.5, "P": 0.6, "N": 0.3, "D": 0.3, "S": 0.2, "T": 0.1, "H": 0.1, "A": -0.1, "R": 0.1, "Q": 0.0,
          "E": 0.1, "K": 0.1, "M": -0.2, "L": -0.3, "I": -0.4, "V": -0.4, "F": -0.2, "Y": -0.1, "W": -0.1, "C": 0.0},
}


def predict_gor(sequence: str, window: int = 7) -> dict:
    """Predizione euristica in stile GOR (finestra pesata). Indicativa: non sostituisce PSIPRED/JPred."""
    seq = sequence.upper()
    half = window // 2
    states = []
    for i in range(len(seq)):
        score = {"H": 0.0, "E": 0.0, "C": 0.0}
        for dw in range(-half, half + 1):
            pos = i + dw
            if 0 <= pos < len(seq):
                weight = 1.0 - abs(dw) / (half + 1)
                for state in score:
                    score[state] += _GOR[state].get(seq[pos], 0.0) * weight
        states.append(max(("H", "E", "C"), key=lambda s: (score[s], s == "H", s == "E")))
    prediction = "".join(states)
    return {"method": "GOR semplificato (euristico, finestra 7)", "prediction": prediction,
            "segments": segments(prediction)}
