"""Grandezze per residuo che vengono da fuori del rapporto di qualità: pLDDT, QMEAN locale, profilo ProSA.

Logica pura: dice se la colonna B-factor contiene la confidenza pLDDT di AlphaFold (con il riconoscimento unico di
`plddt.is_alphafold_model`) e riporta sui residui
del rapporto (`report["residues"]`, nell'ordine del file) i punteggi locali di QMEAN e l'energia ProSA.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Iterable, Optional

from . import plddt


def bfactor_kind(name: str, header_text: str, values: Iterable[float]) -> str:
    """"plddt" se la colonna B-factor contiene la confidenza pLDDT di AlphaFold, altrimenti "bfactor".

    AlphaFold scrive il pLDDT per residuo, 0–100, nella colonna del B-factor (Jumper et al. 2021; Varadi et al.
    2022, AlphaFold DB). Il riconoscimento è `plddt.is_alphafold_model`, lo stesso della libreria dei template e del
    ritaglio per pLDDT: segni certi di AlphaFold DB nell'intestazione, oppure nome `AF-<ACC>-F<n>` senza un'altra
    origine dichiarata (EXPDTA, `_exptl.method`, MODELLER), e valori in 0–100 non tutti nulli.
    """
    return "plddt" if plddt.is_alphafold_model(name, header_text, values) else "bfactor"


def _by_chain(residues: list[dict]) -> dict[str, list[int]]:
    out: dict[str, list[int]] = defaultdict(list)
    for i, r in enumerate(residues):
        out[r["chain"]].append(i)
    return out


def qmean_local(residues: list[dict], local: Optional[list[dict]]) -> tuple[list[Optional[float]], Optional[str]]:
    """Punteggi QMEAN locali (0–1) allineati ai residui del rapporto.

    `remote.normalize_qmean` restituisce per ogni catena l'elenco dei punteggi nell'ordine dei residui, con
    indice 1-based per catena. QMEAN riceve la stessa struttura senza eteroatomi: ogni catena deve avere lo
    stesso numero di residui del rapporto, altrimenti i punteggi non sono attribuibili e vengono esclusi.
    """
    out: list[Optional[float]] = [None] * len(residues)
    if not local:
        return out, None
    chains = _by_chain(residues)
    per_chain: dict[str, dict[int, Optional[float]]] = defaultdict(dict)
    for item in local:
        try:
            per_chain[str(item.get("chain", ""))][int(item.get("index"))] = item.get("score")
        except (TypeError, ValueError, AttributeError):
            return [None] * len(residues), "Punteggi QMEAN locali in un formato non riconosciuto: esclusi."
    problems = []
    for chain, scores in per_chain.items():
        idx = chains.get(chain)
        if idx is None:
            problems.append(f"catena {chain or '?'} assente")
        elif len(scores) != len(idx) or set(scores) != set(range(1, len(idx) + 1)):
            problems.append(f"catena {chain}: {len(scores)} punteggi per {len(idx)} residui")
    if problems:
        return [None] * len(residues), ("Punteggi QMEAN locali non attribuibili ai residui della struttura ("
                                        + "; ".join(problems) + "): esclusi dal consenso per residuo.")
    for chain, scores in per_chain.items():
        for pos, i in enumerate(chains[chain], start=1):
            v = scores.get(pos)
            out[i] = round(float(v), 4) if isinstance(v, (int, float)) else None
    return out, None


def prosa_window(residues: list[dict], profile: Optional[list[dict]]) -> tuple[list[Optional[float]], Optional[str]]:
    """Energia ProSA su finestra di 10 residui (`energy_w10`) allineata ai residui del rapporto.

    L'indice del profilo di ProSA è sequenziale (1…N) sui residui del file, nello stesso ordine del rapporto;
    alle estremità la finestra non è definita (valore assente). Se il numero di righe non corrisponde, il
    profilo non è attribuibile e viene escluso.
    """
    out: list[Optional[float]] = [None] * len(residues)
    if not profile:
        return out, None
    indices = [int(p.get("index", 0)) for p in profile]
    if len(profile) != len(residues) or sorted(indices) != list(range(1, len(residues) + 1)):
        return out, (f"Profilo ProSA ({len(profile)} righe) non attribuibile ai {len(residues)} residui della "
                     "struttura: escluso dal consenso per residuo.")
    for p in profile:
        v = p.get("energy_w10")
        out[int(p["index"]) - 1] = round(float(v), 4) if isinstance(v, (int, float)) else None
    return out, None
