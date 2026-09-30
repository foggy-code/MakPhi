"""pLDDT dei modelli AlphaFold: lettura dalla colonna B-factor, fasce di confidenza, riconoscimento dei file.

AlphaFold scrive il pLDDT del residuo (0–100, uguale per tutti i suoi atomi) nella colonna B-factor del PDB e in
`_atom_site.B_iso_or_equiv` del mmCIF (Jumper et al., Nature 596:583, 2021). Fasce di confidenza di AlphaFold DB
(Tunyasuvunakool et al., Nature 596:590, 2021; documentazione di AlphaFold DB), limite inferiore compreso:

| Fascia | pLDDT | Significato |
|---|---|---|
| molto alta | ≥ 90 | scheletro e catene laterali in genere accurati |
| confidente | 70–90 | scheletro in genere corretto |
| bassa | 50–70 | da trattare con cautela |
| molto bassa | < 50 | spesso regioni disordinate in isolamento, non conformazioni |

Il ritaglio predefinito dei template predetti (pLDDT < 70) toglie le fasce "bassa" e "molto bassa": lì lo scheletro
non è affidabile e, usato come template, trasferirebbe a MODELLER vincoli su conformazioni non predette con
confidenza (spesso regioni disordinate). I residui tolti diventano gap nell'allineamento e MODELLER li costruisce
senza vincoli dal template.
"""

from __future__ import annotations

import re
from typing import Iterable, Optional

from . import structure

# (nome, limite inferiore compreso), dalla fascia più alta
BANDS = (("very_high", 90.0), ("confident", 70.0), ("low", 50.0), ("very_low", float("-inf")))
BAND_LABELS = {"very_high": "molto alta", "confident": "confidente", "low": "bassa", "very_low": "molto bassa"}
DEFAULT_THRESHOLD = 70.0
# Minimo di residui di un template dopo il ritaglio: circa la dimensione dei più piccoli domini che si ripiegano da
# soli (headpiece della villina HP35, 35 aa, McKnight et al. 1997; dominio WW, ~35 aa, Macias et al. 1996; zinc
# finger C2H2, ~30 aa). Sotto, il template descrive un frammento e non un ripiegamento, e l'allineamento
# sequenza-struttura di MODELLER su così pochi residui non è affidabile.
MIN_TRIMMED_RESIDUES = 30

# TITLE dei PDB di AlphaFold DB: "ALPHAFOLD MONOMER V2.0 PREDICTION FOR <PROTEINA> (<ACC>)"
_AF_TITLE = re.compile(r"^TITLE\s+(?:\d+\s+)?ALPHAFOLD\b.*\bPREDICTION\b", re.MULTILINE)
_EXPDTA = re.compile(r"^EXPDTA", re.MULTILINE)


def band(value: float) -> str:
    """Fascia di confidenza di un valore di pLDDT."""
    for name, lower in BANDS:
        if value >= lower:
            return name
    return "very_low"


def residue_plddt(residue) -> Optional[float]:
    """pLDDT di un residuo: B-factor del C-alfa (AlphaFold lo ripete su tutti gli atomi), altrimenti la media."""
    if "CA" in residue:
        return float(residue["CA"].get_bfactor())
    values = [float(a.get_bfactor()) for a in residue.get_atoms()]
    return sum(values) / len(values) if values else None


def residues(model, chains: Optional[Iterable[str]] = None) -> list[tuple[str, object, float]]:
    """(catena, residuo, pLDDT) degli amminoacidi delle catene indicate (tutte se None), nell'ordine del file."""
    wanted = set(chains) if chains else None
    out = []
    for chain in model:
        if wanted is not None and chain.id not in wanted:
            continue
        for res in chain:
            if structure.is_amino_residue(res):
                value = residue_plddt(res)
                if value is not None:
                    out.append((chain.id, res, value))
    return out


def summarize(values: list[float]) -> dict:
    """Media e frazione di residui per fascia (come `fractionPlddt*` di AlphaFold DB)."""
    n = len(values)
    counts = {name: 0 for name, _lower in BANDS}
    for v in values:
        counts[band(v)] += 1
    return {"mean": round(sum(values) / n, 2) if n else None, "residues": n,
            "fractions": {k: round(c / n, 3) if n else 0.0 for k, c in counts.items()}}


def removed_ranges(rows: list[tuple]) -> list[list[str]]:
    """Intervalli contigui di residui esclusi (righe di `low_confidence`): [[primo, ultimo, catena], ...]."""
    out: list[list[str]] = []
    prev_removed, prev_chain = False, None
    for chain, _rid, resid, _aa, _value, keep in rows:
        if not keep:
            if prev_removed and prev_chain == chain:
                out[-1][1] = resid
            else:
                out.append([resid, resid, chain])
        prev_removed, prev_chain = not keep, chain
    return out


def low_confidence(model, chains: Iterable[str], threshold: float) -> dict:
    """Residui con pLDDT sotto la soglia nelle catene scelte, con i conteggi.

    Restituisce `rows` (catena, id Biopython del residuo, codice di residuo, lettera, pLDDT, tenuto) nell'ordine del
    file e `removed`, l'insieme (catena, id Biopython) da escludere.
    """
    rows = []
    removed = set()
    for chain_id, res, value in residues(model, chains):
        keep = value >= threshold
        rows.append((chain_id, res.id, _resid(res), structure.three_to_one(res.get_resname()), value, keep))
        if not keep:
            removed.add((chain_id, res.id))
    return {"rows": rows, "removed": removed}


def _resid(res) -> str:
    _, num, icode = res.id
    return f"{num}{icode.strip()}"


def is_alphafold_text(text: str) -> bool:
    """Il testo (inizio del file) è un modello di AlphaFold DB? Solo segni certi, per non scambiare i B-factor
    di una struttura sperimentale per pLDDT.

    - PDB: TITLE "ALPHAFOLD ... PREDICTION ..." e nessun record EXPDTA (sempre presente nelle strutture sperimentali);
    - mmCIF (ModelCIF): metriche di qualità `_ma_qa_metric` e il nome AlphaFold, senza `_exptl.method`.
    """
    head = text or ""
    if head.lstrip().startswith("data_") or "_atom_site." in head:
        return ("_ma_qa_metric" in head and re.search(r"alphafold", head, re.IGNORECASE) is not None
                and "_exptl.method" not in head)
    return bool(_AF_TITLE.search(head)) and not _EXPDTA.search(head)
