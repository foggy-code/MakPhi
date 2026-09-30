"""Grandezze per residuo che vengono da fuori del rapporto di qualità: pLDDT, QMEAN locale, profilo ProSA.

Logica pura: riconosce se la colonna B-factor contiene la confidenza pLDDT di AlphaFold e riporta sui residui
del rapporto (`report["residues"]`, nell'ordine del file) i punteggi locali di QMEAN e l'energia ProSA.
"""

from __future__ import annotations

import re
from collections import defaultdict
from typing import Iterable, Optional

# Accessione UniProt (espressione regolare ufficiale di UniProt, https://www.uniprot.org/help/accession_numbers).
_UNIPROT_ACCESSION = r"(?:[OPQ][0-9][A-Z0-9]{3}[0-9]|[A-NR-Z][0-9](?:[A-Z][A-Z0-9]{2}[0-9]){1,2})"
# File di AlphaFold DB ("AF-P0CG48-F1-model_v4.pdb") e identificativi di libreria che ne derivano ("AF-P0CG48-F1"),
# anche con il prefisso casuale dei file caricati ("1a2b3c4d_AF-P0CG48-F1-model_v4.pdb").
AF_NAME = re.compile(rf"(?:^|[^A-Z0-9])AF-{_UNIPROT_ACCESSION}-F\d+(?:-MODEL_V\d+)?(?=[^A-Z0-9]|$)", re.IGNORECASE)
_ALPHAFOLD = re.compile(r"ALPHA\s?FOLD", re.IGNORECASE)
# Record PDB dell'intestazione in cui AlphaFold DB scrive titolo, riferimenti e avvertenze.
_PDB_HEADER_RECORDS = ("HEADER", "TITLE ", "REMARK")

PLDDT_RANGE = (0.0, 100.0)


def header_mentions_alphafold(text: str, fmt: str = "pdb") -> bool:
    """L'intestazione cita AlphaFold? Si guarda solo prima delle coordinate.

    * PDB: record HEADER, TITLE e REMARK (AlphaFold DB scrive "ALPHAFOLD MONOMER V2.0 PREDICTION FOR …" nel
      TITLE e il riferimento a Jumper et al. 2021 nel REMARK 1). Le righe `REMARK 999 BIOLOOM` sono di BioLoom
      (tracciabilità dei modelli, possono nominare un template AF) e non contano.
    * mmCIF: qualunque voce prima di `_atom_site` (`_struct.title`, `_software.name`, categorie ModelCIF).
    """
    for line in text.splitlines():
        if fmt == "cif":
            if line.startswith(("_atom_site.", "ATOM", "HETATM")):
                return False
            if _ALPHAFOLD.search(line):
                return True
            continue
        if line.startswith(("ATOM", "HETATM", "MODEL")):
            return False
        if line.startswith(_PDB_HEADER_RECORDS) and not line.startswith("REMARK 999 BIOLOOM") \
                and _ALPHAFOLD.search(line):
            return True
    return False


def bfactor_kind(name: str, header_text: str, values: Iterable[float], fmt: str = "pdb") -> str:
    """"plddt" se la colonna B-factor contiene la confidenza pLDDT di AlphaFold, altrimenti "bfactor".

    Euristica (AlphaFold scrive il pLDDT per residuo, 0–100, nella colonna del B-factor: Jumper et al. 2021;
    Varadi et al. 2022, AlphaFold DB):

    1. il file è di AlphaFold: l'intestazione cita AlphaFold (`header_mentions_alphafold`) oppure il nome segue
       lo schema di AlphaFold DB, `AF-<accessione UniProt>-F<n>[-model_v<k>]` (file scaricati, id di libreria);
    2. tutti i valori stanno in 0–100 e non sono tutti nulli (una colonna azzerata non porta pLDDT).

    Un B-factor cristallografico può stare in 0–100: per questo la sola scala non basta e serve la condizione 1.
    """
    vals = list(values)
    if not vals or not any(v != 0 for v in vals):
        return "bfactor"
    lo, hi = PLDDT_RANGE
    if not all(lo <= v <= hi for v in vals):
        return "bfactor"
    if AF_NAME.search(name or "") or header_mentions_alphafold(header_text or "", fmt):
        return "plddt"
    return "bfactor"


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
