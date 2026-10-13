"""Composizione delle catene proteiche di una struttura: monomero, omo-oligomero o etero-oligomero.

Due catene sono la stessa proteina (stessa *entità*) quando il file lo dichiara: `COMPND MOL_ID … CHAIN: A, B` nel PDB,
`_entity_poly.pdbx_strand_id` nel mmCIF. Se il file non lo dice (file caricati, modelli, assemblaggi importati) o le
entità dichiarate non coprono tutte le catene, le catene si raggruppano per sequenza: SEQRES quando c'è, altrimenti i
residui osservati, con un'identità di almeno il 95% sulla più corta (residui mancanti e piccole differenze, come un
mutante, non separano due copie della stessa proteina).

Riguarda le catene del file, cioè l'unità asimmetrica per le strutture cristallografiche: lo stato biologico è quello
dell'assemblaggio (domain/assembly).
"""

from __future__ import annotations

from typing import Optional

# identità minima (residui uguali / lunghezza della sequenza più corta) per considerare due catene la stessa proteina
SAME_PROTEIN_IDENTITY = 0.95
LETTERS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"


def _identity(a: str, b: str) -> float:
    """Residui uguali allineabili (sottosequenza comune più lunga, senza penalità per i gap) sulla sequenza più corta."""
    from Bio.Align import PairwiseAligner

    if not a or not b:
        return 0.0
    aligner = PairwiseAligner(mode="global", match_score=1.0, mismatch_score=0.0, gap_score=0.0)
    return aligner.score(a, b) / min(len(a), len(b))


def _by_sequence(chains: list[dict], seqres: dict[str, str]) -> list[list[str]]:
    """Gruppi di catene con la stessa sequenza: prima le uguali, poi i rappresentanti simili oltre la soglia."""
    groups: list[tuple[str, list[str]]] = []
    exact: dict[str, int] = {}
    for c in chains:
        seq = (seqres.get(c["chain"]) or c.get("sequence") or "").upper()
        if seq in exact:
            groups[exact[seq]][1].append(c["chain"])
            continue
        for k, (rep, members) in enumerate(groups):
            if _identity(seq, rep) >= SAME_PROTEIN_IDENTITY:
                members.append(c["chain"])
                exact[seq] = k
                break
        else:
            exact[seq] = len(groups)
            groups.append((seq, [c["chain"]]))
    return [members for _, members in groups]


def _declared(chains: list[dict], entities: list[dict]) -> Optional[list[tuple[str, list[str]]]]:
    """Entità del file ristrette alle catene proteiche, se ogni catena ne ha una e una sola."""
    names = {c["chain"] for c in chains}
    seen: set[str] = set()
    out = []
    for e in entities:
        members = [ch for ch in e.get("chains", []) if ch in names]
        if any(ch in seen for ch in members):
            return None
        seen.update(members)
        if members:
            out.append((e.get("name", ""), members))
    return out if seen == names else None


def stoichiometry(counts: list[int]) -> str:
    """Stechiometria come in RCSB: entità in ordine di copie decrescenti, A2B2, AB, A3 (oltre la Z: "×n entità")."""
    counts = sorted(counts, reverse=True)
    if len(counts) > len(LETTERS):
        return f"{len(counts)} entità"
    return "".join(f"{LETTERS[i]}{n if n > 1 else ''}" for i, n in enumerate(counts))


def describe(chains: list[dict], entities: Optional[list[dict]] = None,
             seqres: Optional[dict[str, str]] = None) -> dict:
    """`chains`: riassunti delle catene proteiche (structure.chain_summaries); `entities`: [{name, chains}] del file.

    Restituisce {kind: "monomer" | "homo" | "hetero" | "none", chains, stoichiometry, source: "file" | "sequence",
    entities: [{name, chains, length}]}, con le entità in ordine di copie decrescenti.
    """
    if not chains:
        return {"kind": "none", "chains": 0, "stoichiometry": "", "source": "", "entities": []}
    seqres = seqres or {}
    declared = _declared(chains, entities or [])
    if declared is not None:
        groups, source = declared, "file"
    else:
        groups, source = [("", members) for members in _by_sequence(chains, seqres)], "sequence"
    length = {c["chain"]: len(seqres.get(c["chain"]) or c.get("sequence") or "") for c in chains}
    rows = [{"name": name, "chains": members, "length": max(length[ch] for ch in members)} for name, members in groups]
    rows.sort(key=lambda r: -len(r["chains"]))
    kind = "monomer" if len(chains) == 1 else "homo" if len(rows) == 1 else "hetero"
    return {"kind": kind, "chains": len(chains), "stoichiometry": stoichiometry([len(r["chains"]) for r in rows]),
            "source": source, "entities": rows}
