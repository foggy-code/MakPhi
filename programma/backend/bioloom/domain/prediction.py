"""Predizioni esterne di struttura (F9.3): file del predittore e controllo di plausibilità di un modello per omologia.

Logica pura, nessun I/O. Il job `predict` (services/predict.py) ottiene la predizione (ESMFold dal servizio ESM Atlas,
oppure il modello di AlphaFold DB già supportato), la confronta con il modello (`domain/comparison` attraverso
`engines/align.structural`, modello = A, riferimento) e chiede qui l'esito.

1. **File della predizione** (`normalize_pdb`). ESMFold scrive il pLDDT di ogni atomo nella colonna B-factor (Lin et
   al., Science 379:1123, 2023). Il servizio pubblico lo scrive su 0–1 (verificato il 1° ottobre 2026: ubiquitina
   umana, valori dei Cα da 0,45 a 0,96), mentre AlphaFold e la libreria di ESMFold lo scrivono su 0–100: un file con un
   massimo non oltre 1 è su 0–1 e si moltiplica per 100, così il pLDDT ha la stessa scala dei modelli di AlphaFold (fasce
   di `domain/plddt`). Si verificano coordinate, sequenza (i residui sono quelli inviati, nello stesso ordine: il servizio
   salta in silenzio le lettere non standard) e valori; l'intestazione del servizio resta (TITLE "ESMFOLD … PREDICTION",
   riferimento e licenza) e, se manca, BioLoom scrive un TITLE equivalente, così il file è riconosciuto da
   `plddt.predictor` anche con un altro nome.
2. **Plausibilità** (`assess`). Tre livelli, dal globale al locale:

   | Livello | Regola | Fonte |
   |---|---|---|
   | ripiegamento | TM-score normalizzato sul modello ≥ 0,5 = stesso ripiegamento | Xu & Zhang, Bioinformatics 26:889, 2010; casuale ≈ 0,17 (Zhang & Skolnick, Proteins 57:702, 2004) |
   | residuo divergente | lDDT-Cα del residuo del modello rispetto alla predizione < 0,5: perse più della metà delle distanze entro 15 Å (soglie 0,5, 1, 2, 4 Å) | Mariani et al., Bioinformatics 29:2722, 2013; confine della fascia "molto bassa" del pLDDT, che è un lDDT-Cα predetto (scelta BioLoom) |
   | regione divergente | almeno 3 residui divergenti, uno non divergente tollerato fra due, stessa catena e numerazione contigua | come le regioni problematiche del consenso (`domain/consensus`) |
   | predizione sicura | pLDDT medio della regione (Cα della predizione) ≥ 70; incerta sotto, molto incerta sotto 50 | fasce di AlphaFold DB (Tunyasuvunakool et al., Nature 596:590, 2021), valide per ESMFold (stesso lDDT-Cα predetto) |

   Esito: **non conclusivo** con meno di 30 residui a predizione confidente (pLDDT ≥ 70; circa la dimensione dei più
   piccoli domini che si ripiegano da soli, come `plddt.MIN_TRIMMED_RESIDUES`): il predittore non sa nulla del
   ripiegamento; **ripiegamento diverso** con TM-score < 0,5; **divergenze locali** se almeno una regione divergente
   cade dove la predizione è sicura (lì è più probabile un errore del modello: allineamento o template); altrimenti
   **concordano**, con le eventuali divergenze dove anche la predizione è incerta (regioni flessibili o disordinate, il
   confronto lì non è informativo). Ogni regione divergente si incrocia con le regioni problematiche del consenso del
   modello: quando coincidono, due prove indipendenti indicano lo stesso tratto.

**Limiti** (anche nel risultato): una predizione non è una misura sperimentale. ESMFold è meno accurato di AlphaFold
sulle proteine con poche sequenze omologhe (Lin et al. 2023) e, come AlphaFold, può sbagliare con pLDDT alto; un
template sperimentale vicino (alta identità) è di solito più affidabile della predizione. Il confronto riguarda una
catena: le interfacce di un complesso non si valutano. La predizione è di una sola conformazione: movimenti di dominio,
stati legati e apo diversi dal template danno divergenze che non sono errori.
"""

from __future__ import annotations

import math
import re
from typing import Iterable, Optional

from . import plddt, structure

VERSION = 1

PREDICTORS = {
    "esmfold": {"label": "ESMFold", "service": "ESM Atlas (Meta)", "reference": "esmfold"},
    "alphafold": {"label": "AlphaFold DB", "service": "AlphaFold DB (EBI)", "reference": "afdb"},
}

PLDDT_CONFIDENT = plddt.DEFAULT_THRESHOLD  # 70: fascia "confidente" di AlphaFold DB
PLDDT_VERY_LOW = 50.0  # sotto: fascia "molto bassa", spesso disordine
LDDT_DIVERGENT = 0.5
TM_SAME_FOLD = 0.5
TM_RANDOM = 0.17
MIN_CONFIDENT_RESIDUES = plddt.MIN_TRIMMED_RESIDUES  # 30
REGION_MIN_RESIDUES = 3  # come domain/consensus.REGION_MIN_RESIDUES
REGION_MAX_GAP = 1  # come domain/consensus.REGION_MAX_GAP

STATUS_TONE = {"concordano": "ok", "divergenze locali": "warn", "ripiegamento diverso": "err",
               "non conclusivo": "info"}
CONFIDENCE_LABEL = {"sicura": "la predizione è sicura", "incerta": "anche la predizione è incerta",
                    "molto incerta": "anche la predizione è molto incerta"}
LIMITS = ("Una predizione non è una misura sperimentale: ESMFold è meno accurato di AlphaFold sulle proteine con poche "
          "sequenze omologhe (Lin et al. 2023) e può sbagliare anche con pLDDT alto; un template sperimentale vicino "
          "(alta identità) è di solito più affidabile della predizione. Il confronto riguarda una catena (le interfacce "
          "di un complesso non si valutano) e una sola conformazione: movimenti di dominio e stati legati diversi dal "
          "template danno divergenze che non sono errori.")

_RESNUM = re.compile(r"^-?\d+")


# ---------------------------------------------------------------------------------------------- utilità
def _num(value: Optional[float], digits: int = 2) -> str:
    if value is None:
        return "—"
    return f"{value:.{digits}f}".replace(".", ",").replace("-", "−")


def _join(items: list[str]) -> str:
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " e " + items[-1]


def _resnum_int(resnum: object) -> Optional[int]:
    m = _RESNUM.match(str(resnum))
    return int(m.group()) if m else None


def _consecutive(a: dict, b: dict) -> bool:
    """Stessa catena e numerazione contigua (0: codice di inserzione, 52 e 52A), come domain/consensus."""
    if a["chain"] != b["chain"]:
        return False
    na, nb = _resnum_int(a["resnum"]), _resnum_int(b["resnum"])
    return na is None or nb is None or 0 <= nb - na <= 1


def _mean(values: Iterable[Optional[float]]) -> Optional[float]:
    vals = [float(v) for v in values if isinstance(v, (int, float))]
    return sum(vals) / len(vals) if vals else None


def confidence(plddt_mean: Optional[float]) -> Optional[str]:
    """"sicura" (pLDDT ≥ 70), "incerta" (50–70), "molto incerta" (< 50), None senza valori."""
    if plddt_mean is None:
        return None
    if plddt_mean >= PLDDT_CONFIDENT:
        return "sicura"
    return "incerta" if plddt_mean >= PLDDT_VERY_LOW else "molto incerta"


# ---------------------------------------------------------------------------------------------- sequenza dalla struttura
STANDARD = frozenset("ACDEFGHIKLMNPQRSTVWY")
# Residui della struttura entro questa distanza (in posizioni della sequenza inviata) da una giunzione fra due tratti
# uniti: nella predizione i due tratti sono legati da un legame peptidico che nella proteina non c'è, e la geometria
# locale lì è forzata. Scelta BioLoom: 5 posizioni per parte, circa un giro e mezzo d'elica o un filamento corto, come
# la finestra delle regioni divergenti; oltre, il confronto torna informativo.
JUNCTION_WINDOW = 5


def _chain_letter(res) -> Optional[str]:
    """Lettera del residuo della catena: amminoacidi (anche non standard, 'X') e residui modificati scritti come
    HETATM con la lettera del residuo standard (MSE → M, SEP → S), se legati alla catena; None per il resto."""
    from .ligands import is_modified_residue

    if structure.is_amino_residue(res):
        return structure.three_to_one(res.get_resname())
    if res.id[0] in (" ", "W") or "CA" not in res:
        return None
    letter = structure.three_to_one(res.get_resname())
    return letter if letter in STANDARD and is_modified_residue(res) else None


def _clean_seqres(seqres: Optional[str]) -> str:
    """SEQRES senza spazi e senza le estremità non standard (gruppi ACE, NH2: 'X' in testa o in coda)."""
    return "".join((seqres or "").split()).upper().strip("X")


def _segments_in_order(segments: list[str], full: str) -> bool:
    """I tratti osservati compaiono nella sequenza completa, nello stesso ordine: SEQRES della stessa catena."""
    pos = 0
    for seg in segments:
        k = full.find(seg, pos)
        if k < 0:
            return False
        pos = k + len(seg)
    return True


def chain_sequence(chain, seqres: Optional[str] = None, max_length: Optional[int] = None) -> dict:
    """Sequenza da predire ricavata da una catena della struttura (quando non se ne indica una).

    I residui modificati della catena (HETATM legati, MSE → M) contano con la lettera del residuo standard. Le
    interruzioni sono quelle fisiche (C–N oltre 2,0 Å, senza quegli atomi Cα–Cα oltre 4,2 Å, come la rifinitura e
    `structure.chain_summaries`): un salto di numerazione fra residui legati non toglie residui; il salto serve solo a
    contare i mancanti. Senza interruzioni la sequenza è quella osservata. Con interruzioni unire i tratti darebbe al
    predittore una proteina che non esiste: si usa la sequenza completa della catena (`seqres`: SEQRES del PDB o
    `_entity_poly` del mmCIF) se c'è, ha solo lettere standard, contiene i tratti osservati nell'ordine e sta in
    `max_length`; altrimenti la sequenza osservata con l'avviso e i residui vicini alle giunzioni (`junctions`, da
    escludere dalle divergenze in `assess`).

    Restituisce {sequence, origin ("structure" | "seqres"), gaps [{after, before, missing}], modified, junctions
    [(catena, residuo)], warning (None se la sequenza è completa)}."""
    from .refinement import _linked

    residues = [(r, letter) for r in chain if (letter := _chain_letter(r)) is not None]
    name = chain.id.strip() or "_"
    modified = [f"{r.get_resname().strip()} {name}{structure._resid(r)} → {letter}" for r, letter in residues
                if r.id[0] != " "]
    gaps, cuts = [], []
    for k in range(1, len(residues)):
        prev, cur = residues[k - 1][0], residues[k][0]
        if _linked(prev, cur):
            continue
        jump = cur.id[1] - prev.id[1] - 1
        gaps.append({"after": structure._resid(prev), "before": structure._resid(cur),
                     "missing": jump if jump > 0 else None})
        cuts.append(k)
    observed = "".join(letter for _r, letter in residues)
    out = {"sequence": observed, "origin": "structure", "gaps": gaps, "modified": modified, "junctions": [],
           "warning": None}
    if not gaps:
        return out
    bounds = [0, *cuts, len(residues)]
    segments = [observed[a:b] for a, b in zip(bounds, bounds[1:])]
    full = _clean_seqres(seqres)
    usable = bool(full) and set(full) <= STANDARD and _segments_in_order(segments, full)
    if usable and (max_length is None or len(full) <= max_length):
        return {**out, "sequence": full, "origin": "seqres"}
    where = "; ".join(f"fra {g['after']} e {g['before']}" + (f", {g['missing']} residui mancanti" if g["missing"] else "")
                      for g in gaps[:6]) + ("…" if len(gaps) > 6 else "")
    why = ("" if not full else
           f" La sequenza completa del file (SEQRES) ha {len(full)} residui, oltre il limite di {max_length}."
           if usable else " La sequenza completa del file (SEQRES) non corrisponde ai residui della catena.")
    junctions: list[tuple[str, str]] = []
    for k in cuts:
        for r, _letter in residues[max(0, k - JUNCTION_WINDOW):k + JUNCTION_WINDOW]:
            key = (chain.id, structure._resid(r))
            if key not in junctions:
                junctions.append(key)
    warning = (f"La catena {name} è interrotta ({where}): la sequenza ricavata dalla struttura salta i residui mancanti "
               f"e unisce i tratti, quindi la predizione è di una proteina che non esiste vicino alle giunzioni.{why} "
               f"Indica la sequenza completa della catena (SEQRES o UniProt) nel campo Sequenza; i residui entro "
               f"{JUNCTION_WINDOW} posizioni dalle giunzioni non contano come divergenze.")
    return {**out, "junctions": junctions, "warning": warning}


# ---------------------------------------------------------------------------------------------- file della predizione
def plddt_scale(values: Iterable[float]) -> tuple[float, str]:
    """(fattore per portare i valori su 0–100, scala originale): massimo ≤ 1 → (100, "0-1"), altrimenti (1, "0-100").

    ValueError se i valori non sono un pLDDT (negativi, oltre 100, non finiti, tutti nulli)."""
    vals = list(values)
    if not vals:
        raise ValueError("nessun valore di pLDDT nella colonna B-factor")
    if any(not math.isfinite(v) for v in vals):
        raise ValueError("valori non numerici nella colonna B-factor")
    low, high = min(vals), max(vals)
    if low < 0 or high > 100:
        raise ValueError(f"valori della colonna B-factor fuori dalla scala del pLDDT ({low:g}–{high:g})")
    if high <= 0:
        raise ValueError("colonna B-factor tutta a zero: il file non contiene il pLDDT")
    return (100.0, "0-1") if high <= plddt.UNIT_SCALE_MAX else (1.0, "0-100")


def normalize_pdb(text: str, sequence: str, *, predictor: str = "esmfold", name: str = "",
                  service: str = "") -> tuple[str, dict]:
    """PDB della predizione con il pLDDT su 0–100 e l'intestazione riconoscibile, più le informazioni del file.

    `sequence`: la sequenza inviata (20 lettere standard). Verifica che i Cα, nell'ordine del file, abbiano proprio
    quelle lettere. Restituisce (testo, {scale, factor, residues, chains, plddt: domain/plddt.summarize}).
    ValueError con il motivo se il testo non è una predizione valida di quella sequenza.
    """
    lines = (text or "").splitlines()
    atoms = [n for n, ln in enumerate(lines) if ln.startswith(("ATOM  ", "HETATM"))]
    if not atoms:
        raise ValueError("nessuna coordinata atomica nella risposta")
    values, ca, chains = [], [], []
    for n in atoms:
        ln = lines[n]
        try:
            values.append(float(ln[60:66]))
            for start in (30, 38, 46):  # x, y, z
                float(ln[start:start + 8])
        except ValueError:
            raise ValueError(f"riga di coordinate non leggibile: {ln[:30].strip()}") from None
        if ln.startswith("ATOM  ") and ln[12:16].strip() == "CA":
            ca.append((ln[21], ln[22:27].strip(), structure.three_to_one(ln[17:20]), len(values) - 1))
            if ln[21] not in chains:
                chains.append(ln[21])
    factor, scale = plddt_scale(values)
    letters = "".join(c[2] for c in ca)
    wanted = (sequence or "").strip().upper()
    if wanted and letters != wanted:
        raise ValueError(f"la predizione ha {len(letters)} residui ({letters[:12]}…), la sequenza inviata "
                         f"{len(wanted)} ({wanted[:12]}…)")
    out = list(lines)
    for n in atoms:
        ln = out[n].ljust(66)
        value = float(ln[60:66]) * factor
        out[n] = ln[:60] + f"{value:6.2f}" + ln[66:]
    first = atoms[0]
    header = [ln for ln in out[:first] if ln.strip()]
    body = out[first:]
    label = PREDICTORS.get(predictor, {}).get("label", predictor).upper()
    if predictor == "esmfold" and not plddt.is_esmfold_text("\n".join(header)):
        header.insert(0, f"TITLE     ESMFOLD PREDICTION, {name or 'SEQUENCE'} SAVED BY BIOLOOM"[:80].upper())
    header = [ln for ln in header if not (ln.startswith("REMARK 999") and "BIOLOOM" in ln)]
    header.append(f"REMARK 999 BIOLOOM PREDICTOR {label} {service}"[:80].rstrip())
    header.append(f"REMARK 999 BIOLOOM PLDDT SCALE {scale}" + (" X100 TO 0-100" if factor != 1 else ""))
    if not any(ln.startswith("END") for ln in body):
        body.append("END")
    ca_values = [values[i] * factor for *_rest, i in ca]
    info = {"scale": scale, "factor": factor, "residues": len(ca), "chains": chains,
            "plddt": plddt.summarize(ca_values)}
    return "\n".join(header + body) + "\n", info


def residue_plddt(model) -> dict[tuple[str, str], float]:
    """pLDDT (Cα, come `plddt.residue_plddt`) per (catena, residuo) della predizione."""
    return {(chain, structure._resid(res)): round(value, 2) for chain, res, value in plddt.residues(model)}


# ---------------------------------------------------------------------------------------------- plausibilità
def _regions(rows: list[dict]) -> list[list[int]]:
    """Gruppi di indici di righe divergenti: almeno REGION_MIN_RESIDUES divergenti, REGION_MAX_GAP tollerati."""
    hot = [i for i, r in enumerate(rows) if r["divergent"]]
    groups: list[list[int]] = []
    for i in hot:
        if groups:
            prev = groups[-1][-1]
            if i - prev - 1 <= REGION_MAX_GAP and all(_consecutive(rows[k], rows[k + 1]) for k in range(prev, i)):
                groups[-1].append(i)
                continue
        groups.append([i])
    return [g for g in groups if len(g) >= REGION_MIN_RESIDUES]


def _span(chain: str, first: str, last: str) -> str:
    return f"{first if first == last else f'{first}-{last}'} (catena {chain})"


def assess(comparison: dict, plddt_map: dict, *, predictor: str = "esmfold",
           consensus_regions: Optional[list[dict]] = None,
           junctions: Optional[Iterable[tuple[str, str]]] = None) -> dict:
    """Esito motivato del confronto fra il modello (A, riferimento) e la predizione (B).

    `comparison`: risultato di `engines/align.structural` (pairs con il lDDT del residuo di A, tm_score, lddt,
    lengths). `plddt_map`: pLDDT della predizione per (catena, residuo) di B (`residue_plddt`). `consensus_regions`:
    regioni problematiche del consenso del modello come {chain, resnums, text} (None = consenso non disponibile).
    `junctions`: residui del modello vicini alle giunzioni di una sequenza inviata senza i residui mancanti
    (`chain_sequence`): lì la predizione è forzata e il confronto non è informativo, quindi non sono divergenti.
    """
    label = PREDICTORS.get(predictor, {}).get("label", predictor)
    pairs = [p for p in comparison.get("pairs") or [] if isinstance(p, dict)]
    near_junction = {(str(c), str(r)) for c, r in junctions or []}
    rows = []
    for p in pairs:
        value = plddt_map.get((str(p.get("chain_b")), str(p.get("resnum_b"))))
        lddt = p.get("lddt")
        junction = (str(p.get("chain_a")), str(p.get("resnum_a"))) in near_junction
        rows.append({"chain": str(p.get("chain_a")), "resnum": str(p.get("resnum_a")), "aa": p.get("aa_a"),
                     "resnum_b": str(p.get("resnum_b")), "lddt": lddt, "plddt": value, "junction": junction,
                     "divergent": isinstance(lddt, (int, float)) and lddt < LDDT_DIVERGENT and not junction,
                     "confident": isinstance(value, (int, float)) and value >= PLDDT_CONFIDENT})
    tm = comparison.get("tm_score")
    confident_rows = [r for r in rows if r["confident"] and not r["junction"]]  # le giunzioni non informano
    lddt_confident = _mean(r["lddt"] for r in confident_rows)
    plddt_mean = _mean(r["plddt"] for r in rows)
    needed = min(MIN_CONFIDENT_RESIDUES, len(rows)) if rows else MIN_CONFIDENT_RESIDUES
    lengths = comparison.get("lengths") or []
    notes = []
    unpaired = (int(lengths[0]) - len(rows)) if lengths and isinstance(lengths[0], (int, float)) else 0
    if unpaired > 0:
        notes.append(f"{unpaired} residui del modello senza un corrispondente nella predizione: non confrontati.")
    if consensus_regions is None:
        notes.append("Regioni problematiche del consenso non disponibili (analisi di qualità non calcolata): le "
                     "divergenze non sono incrociate con il consenso.")
    skipped = sum(1 for r in rows if r["junction"])
    if skipped:
        notes.append(f"{skipped} residui vicini alle giunzioni della sequenza inviata (catena interrotta, residui "
                     "mancanti saltati): non contano come divergenze, il confronto lì non è informativo.")
    regions = []
    for g in _regions(rows):
        chunk = rows[g[0]:g[-1] + 1]
        chain, first, last = chunk[0]["chain"], chunk[0]["resnum"], chunk[-1]["resnum"]
        r_plddt = _mean(r["plddt"] for r in chunk)
        level = confidence(r_plddt)
        resnums = {r["resnum"] for r in chunk}
        overlaps = [c for c in consensus_regions or []
                    if str(c.get("chain")) == chain and resnums & {str(x) for x in c.get("resnums") or []}]
        where = _span(chain, first, last)
        if level is None:
            where += " (pLDDT della predizione non disponibile)"
        else:
            where += f", dove {CONFIDENCE_LABEL[level]} (pLDDT medio {_num(r_plddt, 0)})"
        if overlaps:
            where += ", segnalata anche dal consenso (" + "; ".join(str(c.get("text")) for c in overlaps) + ")"
        text = f"divergono in {where}" + (": controlla l'allineamento e il template in questa regione"
                                          if level == "sicura" else
                                          ": regione flessibile o poco determinata, il confronto lì non è informativo")
        regions.append({"chain": chain, "start": first, "end": last, "start_index": g[0], "end_index": g[-1],
                        "length": len(chunk), "divergent": len(g),
                        "lddt_mean": _round(_mean(r["lddt"] for r in chunk), 3),
                        "plddt_mean": _round(r_plddt, 1), "confidence": level,
                        "consensus": [str(c.get("text")) for c in overlaps], "where": where, "text": text,
                        "tone": "warn" if level == "sicura" else "info"})
    sure = [r for r in regions if r["confidence"] == "sicura"]
    unsure = [r for r in regions if r["confidence"] != "sicura"]
    tm_text = f"TM-score {_num(tm, 2)}"
    lddt_text = f"lDDT {_num(comparison.get('lddt'), 2)}"
    if len(confident_rows) < needed:
        status = "non conclusivo"
        summary = (f"Confronto non conclusivo: la predizione {label} è sicura (pLDDT ≥ {PLDDT_CONFIDENT:g}) solo su "
                   f"{len(confident_rows)} residui su {len(rows)} (pLDDT medio {_num(plddt_mean, 0)}), quindi non dice "
                   f"nulla sul ripiegamento del modello ({tm_text}, {lddt_text}).")
    elif tm is None or tm < TM_SAME_FOLD:
        status = "ripiegamento diverso"
        summary = (f"Le due strutture hanno ripiegamenti diversi ({tm_text}, sotto {_num(TM_SAME_FOLD, 1)}) e la "
                   f"predizione {label} è sicura su {len(confident_rows)} residui su {len(rows)} (pLDDT medio "
                   f"{_num(plddt_mean, 0)}): controlla la scelta del template e l'allineamento.")
    elif sure:
        status = "divergenze locali"
        summary = (f"Stesso ripiegamento ({tm_text}, {lddt_text}), ma le due strutture divergono in "
                   + _join([r["where"] for r in sure]) + ": controlla l'allineamento e il template "
                   + ("in questa regione." if len(sure) == 1 else "in queste regioni."))
        if unsure:
            summary += (" Divergono anche in " + _join([r["where"] for r in unsure])
                        + ", dove il confronto non è informativo.")
    else:
        status = "concordano"
        summary = f"Le due strutture concordano: {tm_text} (stesso ripiegamento), {lddt_text}"
        summary += ("; divergono solo in " + _join([r["where"] for r in unsure]) + ": "
                    + ("regione flessibile o poco determinata" if len(unsure) == 1
                       else "regioni flessibili o poco determinate") + ", il confronto lì non è informativo."
                    if unsure else ".")
    return {
        "version": VERSION, "predictor": predictor, "label": label, "status": status, "tone": STATUS_TONE[status],
        "summary": summary, "tm_score": tm, "lddt": comparison.get("lddt"), "gdt_ts": comparison.get("gdt_ts"),
        "rmsd": comparison.get("rmsd"), "pairs": len(rows), "plddt_mean": _round(plddt_mean, 1),
        "confident_residues": len(confident_rows), "lddt_confident": _round(lddt_confident, 4),
        "divergent_residues": sum(1 for r in rows if r["divergent"]), "regions": regions,
        "residues": [{k: r[k] for k in ("chain", "resnum", "lddt", "plddt", "divergent", "confident", "junction")}
                     for r in rows],
        "consensus_available": consensus_regions is not None, "notes": notes, "limits": LIMITS,
        "thresholds": {"tm_same_fold": TM_SAME_FOLD, "tm_random": TM_RANDOM, "lddt_divergent": LDDT_DIVERGENT,
                       "plddt_confident": PLDDT_CONFIDENT, "plddt_very_low": PLDDT_VERY_LOW,
                       "min_confident_residues": MIN_CONFIDENT_RESIDUES, "region_min_residues": REGION_MIN_RESIDUES,
                       "region_max_gap": REGION_MAX_GAP},
    }


def _round(value: Optional[float], digits: int) -> Optional[float]:
    return None if value is None else round(float(value), digits)


def consensus_regions(report: dict) -> Optional[list[dict]]:
    """Regioni problematiche del consenso di un rapporto di qualità come {chain, resnums, text} (per `assess`); None
    se il rapporto non ha un consenso con le regioni."""
    cons = report.get("consensus") if isinstance(report, dict) else None
    residues = report.get("residues") if isinstance(report, dict) else None
    if not isinstance(cons, dict) or not isinstance(residues, list):
        return None
    out = []
    for r in cons.get("regions") or []:
        if not isinstance(r, dict):
            continue
        try:
            span = residues[int(r["start_index"]):int(r["end_index"]) + 1]
        except (KeyError, TypeError, ValueError):
            continue
        out.append({"chain": str(r.get("chain")),
                    "resnums": [str(x.get("resnum")) for x in span if isinstance(x, dict)],
                    "text": str(r.get("text") or "")})
    return out
