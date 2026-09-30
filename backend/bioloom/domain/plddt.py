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
senza vincoli dal template. Dopo la soglia si tolgono anche i frammenti isolati (`MIN_FRAGMENT`).

Riconoscimento dei modelli AlphaFold (`is_alphafold_model`): è l'unico criterio di BioLoom, usato dal rapporto di
qualità (colonna "pLDDT" e colorazione), dalla libreria dei template (modello predetto, ritaglio) e dalla copia del
template preparata per MODELLER.
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
# Tratto confidente più corto che resta nel template quando la soglia lo isola (residui tolti accanto): 5 residui
# contigui. È il minimo per un giro d'elica α con il suo legame idrogeno i→i+4 (Pauling et al. 1951), cioè per una
# conformazione locale che si regge da sola. Il pLDDT misura solo la confidenza locale (lDDT): la posizione di un
# tratto più corto rispetto al resto del modello, in mezzo a regioni disordinate, non è stimata (è il PAE a dirlo) e
# darebbe a MODELLER restrizioni di distanza da un punto arbitrario. I residui tolti così diventano gap come gli altri.
MIN_FRAGMENT = 5
PLDDT_RANGE = (0.0, 100.0)

# TITLE dei PDB di AlphaFold DB: "ALPHAFOLD MONOMER V2.0 PREDICTION FOR <PROTEINA> (<ACC>)"
_AF_TITLE = re.compile(r"^TITLE\s+(?:\d+\s+)?ALPHAFOLD\b.*\bPREDICTION\b", re.MULTILINE)
_EXPDTA = re.compile(r"^EXPDTA", re.MULTILINE)
# Righe che MODELLER scrive nei suoi modelli (oltre a "EXPDTA THEORETICAL MODEL, MODELLER …")
_MODELLER_REMARK = re.compile(r"^REMARK   6 MODELLER", re.MULTILINE)
# Accessione UniProt (espressione regolare ufficiale di UniProt, https://www.uniprot.org/help/accession_numbers).
_UNIPROT_ACCESSION = r"(?:[OPQ][0-9][A-Z0-9]{3}[0-9]|[A-NR-Z][0-9](?:[A-Z][A-Z0-9]{2}[0-9]){1,2})"
# File di AlphaFold DB ("AF-P0CG48-F1-model_v4.pdb") e identificativi di libreria che ne derivano ("AF-P0CG48-F1",
# anche con la catena della copia preparata, "AF-P0CG48-F1_A.pdb"), anche con il prefisso casuale dei file caricati
# ("1a2b3c4d_AF-P0CG48-F1-model_v4.pdb").
AF_NAME = re.compile(rf"(?:^|[^A-Z0-9])AF-{_UNIPROT_ACCESSION}-F\d+(?:-MODEL_V\d+)?(?=[^A-Z0-9]|$)", re.IGNORECASE)


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


def low_confidence(model, chains: Iterable[str], threshold: float, min_fragment: int = MIN_FRAGMENT) -> dict:
    """Residui da togliere nelle catene scelte: pLDDT sotto la soglia e frammenti isolati, con i conteggi.

    Frammento isolato: tratto di residui sopra soglia, contigui nel file e nella numerazione, più corto di
    `min_fragment` e accanto ad almeno un residuo tolto per la soglia (un tratto delimitato solo dalle estremità
    della catena o da un'interruzione del file non è un effetto del ritaglio e resta).

    Restituisce `rows` (catena, id Biopython del residuo, codice di residuo, lettera, pLDDT, tenuto) nell'ordine del
    file, `removed`, l'insieme (catena, id Biopython) da escludere, e i conteggi `below` (sotto soglia) e
    `fragments` (residui dei frammenti isolati).
    """
    items = [(chain_id, res, value, value >= threshold) for chain_id, res, value in residues(model, chains)]
    isolated = _isolated_fragments(items, min_fragment)
    rows = []
    removed = set()
    for n, (chain_id, res, value, above) in enumerate(items):
        keep = above and n not in isolated
        rows.append((chain_id, res.id, _resid(res), structure.three_to_one(res.get_resname()), value, keep))
        if not keep:
            removed.add((chain_id, res.id))
    return {"rows": rows, "removed": removed, "below": sum(1 for item in items if not item[3]),
            "fragments": len(isolated)}


def _isolated_fragments(items: list[tuple], min_fragment: int) -> set[int]:
    """Indici (in `items`) dei residui sopra soglia che formano frammenti isolati (vedi `low_confidence`)."""
    runs: list[list[int]] = []  # tratti sopra soglia: indici consecutivi, stessa catena, numerazione contigua
    for n, (chain_id, res, _value, above) in enumerate(items):
        if not above:
            continue
        prev = items[n - 1] if n else None
        if (runs and runs[-1][-1] == n - 1 and prev is not None and prev[0] == chain_id
                and 0 <= res.id[1] - prev[1].id[1] <= 1):  # 0: codice di inserzione (52, 52A)
            runs[-1].append(n)
        else:
            runs.append([n])
    out: set[int] = set()
    for run in runs:
        if len(run) >= min_fragment:
            continue
        chain_id = items[run[0]][0]
        if any(0 <= k < len(items) and items[k][0] == chain_id and not items[k][3] for k in (run[0] - 1, run[-1] + 1)):
            out.update(run)
    return out


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


def declares_other_origin(text: str) -> bool:
    """L'intestazione dichiara un'origine diversa da AlphaFold: un metodo sperimentale (EXPDTA del PDB,
    `_exptl.method` del mmCIF, anche in forma di loop) oppure MODELLER ("EXPDTA THEORETICAL MODEL, MODELLER",
    righe `REMARK   6 MODELLER`). Una struttura risolta a partire da un modello AlphaFold (sostituzione molecolare,
    `REMARK 200 STARTING MODEL: ALPHAFOLD`, `_pdbx_initial_refinement_model`) resta sperimentale."""
    head = text or ""
    return bool(_EXPDTA.search(head) or "_exptl.method" in head or _MODELLER_REMARK.search(head))


def plausible_values(values: Iterable[float]) -> bool:
    """Valori della colonna B-factor compatibili con il pLDDT: tutti in 0–100 e non tutti nulli (una colonna
    azzerata non porta pLDDT)."""
    vals = list(values)
    lo, hi = PLDDT_RANGE
    return bool(vals) and any(v != 0 for v in vals) and all(lo <= v <= hi for v in vals)


def is_alphafold_model(name: str, head: str, values: Iterable[float]) -> bool:
    """Il file è un modello di AlphaFold, con il pLDDT nella colonna B-factor? Unico criterio di BioLoom.

    1. i valori sono compatibili con il pLDDT (`plausible_values`, su tutti gli atomi degli amminoacidi);
    2. l'intestazione ha i segni certi di AlphaFold DB (`is_alphafold_text`: TITLE "ALPHAFOLD … PREDICTION" senza
       EXPDTA, oppure ModelCIF con `_ma_qa_metric` senza `_exptl.method`),
       oppure il nome segue lo schema di AlphaFold DB (`AF_NAME`: file scaricati, caricati con prefisso, id di
       libreria e loro copie preparate) e l'intestazione non dichiara un'altra origine (`declares_other_origin`).

    Un B-factor cristallografico può stare in 0–100 (1UBQ: massimo 42,75): per questo la sola scala non basta, e
    una menzione di AlphaFold altrove nell'intestazione (per esempio il modello di partenza di una sostituzione
    molecolare) non conta.
    """
    if not plausible_values(values):
        return False
    if is_alphafold_text(head):
        return True
    return AF_NAME.search(name or "") is not None and not declares_other_origin(head)


def atom_values(model) -> list[float]:
    """B-factor di tutti gli atomi degli amminoacidi del modello (i valori che `is_alphafold_model` controlla)."""
    return [float(a.get_bfactor()) for chain in model for res in chain if structure.is_amino_residue(res)
            for a in res]
