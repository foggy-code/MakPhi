"""Consenso tra i metodi di validazione: punteggio globale 0–100, discordanze, regioni problematiche.

Tre livelli, calcolati con ciò che il rapporto contiene: un metodo assente non penalizza, semplicemente non
entra nel conto.

1. **Punteggio globale.** Ogni metodo disponibile viene portato su 0–100 con una funzione lineare a tratti
   (punti in `METHODS`, ciascuno con la sua fonte) e i valori si combinano in una media pesata sui soli metodi
   disponibili. Le scale sono costruite perché la soglia "affidabile" della letteratura cada a 70 e quella
   "problematica" a 40: così le fasce (buona ≥ 70, intermedia 40–70, scarsa < 40) hanno lo stesso significato
   per ogni metodo e per il consenso.
2. **Concordanza.** Due metodi discordano quando uno è in fascia buona e l'altro in fascia scarsa; la frase
   spiega che cosa misura ciascuno e come leggere la differenza.
3. **Consenso per residuo.** Per ogni residuo, i metodi che lo segnalano (`SIGNALS`); le regioni problematiche
   sono tratti di almeno 3 residui segnalati da almeno 2 metodi, con un residuo non segnalato tollerato tra due
   segnalati.

Il voto A–D (`domain/grading`) non cambia: il consenso è un campo in più del rapporto.
"""

from __future__ import annotations

import re
import statistics
from dataclasses import dataclass
from typing import Optional

from . import perresidue

VERSION = 1

GOOD = 70.0  # fascia buona: normalizzato ≥ 70
POOR = 40.0  # fascia scarsa: normalizzato < 40
BANDS = (("buona", GOOD), ("intermedia", POOR), ("scarsa", float("-inf")))


@dataclass(frozen=True)
class Method:
    key: str
    label: str
    # (valore grezzo, punteggio 0–100) in ordine crescente di valore grezzo; fuori dall'intervallo si satura
    points: tuple[tuple[float, float], ...]
    weight: float
    scope: str  # "geometry" | "energy" | "fold": serve alle frasi di discordanza
    measures: str
    source: str
    digits: int = 2


# Scale e pesi dichiarati. Pesi: QMEAN pesa di più perché combina quattro potenziali (torsioni, solvatazione,
# coppie Cβ e di tutti gli atomi) ed è già riferito a strutture sperimentali di pari dimensione; GA341 pesa la
# metà perché valuta solo il ripiegamento e, contenendo l'identità con il template, satura vicino a 1 per quasi
# tutti i modelli per omologia; PROCHECK pesa 0,75 perché misura i soli diedri dello scheletro, già presenti nel
# voto BioLoom. Il voto contiene anche una penalità zDOPE (al massimo 20 punti, solo sopra −1): la sovrapposizione
# con z-DOPE è parziale e si accetta.
METHODS: dict[str, Method] = {m.key: m for m in (
    Method("grade", "Voto BioLoom", ((0.0, 0.0), (55.0, 40.0), (70.0, 70.0), (100.0, 100.0)), 1.0, "geometry",
           "stereochimica (outlier di Ramachandran, clashscore) ed energia zDOPE",
           "domain/grading: voto D sotto 55, B da 70, A da 85", digits=1),
    Method("zdope", "z-DOPE", ((-2.0, 100.0), (-1.0, 70.0), (0.0, 40.0), (2.0, 0.0)), 1.0, "energy",
           "l'energia statistica di tutti gli atomi rispetto alle strutture native",
           "Shen & Sali 2006; manuale MODELLER (assess_normalized_dope): sotto −1 modelli simili a strutture "
           "native, valori positivi probabilmente scarsi"),
    Method("ga341", "GA341", ((0.0, 0.0), (0.7, 70.0), (1.0, 100.0)), 0.5, "fold",
           "la correttezza del ripiegamento complessivo",
           "Melo et al. 2002; John & Sali 2003; manuale MODELLER: da 0 (peggiore) a 1 (nativo), ripiegamento "
           "affidabile sopra 0,7"),
    Method("qmean", "QMEAN4 Z-score", ((-8.0, 0.0), (-4.0, 40.0), (-2.0, 70.0), (0.0, 100.0)), 1.5, "energy",
           "geometria locale, interazioni e solvatazione rispetto a strutture sperimentali di pari dimensione",
           "Benkert et al. 2011; SWISS-MODEL: vicino a 0 accordo con strutture sperimentali, −4 o meno bassa "
           "qualità; −2 = due deviazioni standard dalle strutture di riferimento"),
    Method("prosa", "ProSA z-score", ((-8.0, 100.0), (-6.0, 70.0), (-2.0, 40.0), (2.0, 0.0)), 1.0, "energy",
           "l'energia di coppia e di superficie del ripiegamento rispetto a conformazioni alternative",
           "Sippl 1993; Wiederstein & Sippl 2007: valori positivi o vicini a zero indicano errori (−0,60 'troppo "
           "alto' per una struttura nativa), −8,29 nell'intervallo nativo; confine della fascia buona a −6 scelto "
           "da BioLoom (l'intervallo nativo dipende dalla lunghezza: per catene corte è meno negativo)"),
    Method("procheck", "PROCHECK (regioni core)", ((50.0, 0.0), (70.0, 40.0), (90.0, 70.0), (100.0, 100.0)), 0.75,
           "geometry", "la geometria dello scheletro (diedri φ/ψ nelle regioni di Ramachandran)",
           "Laskowski et al. 1993; manuale PROCHECK: oltre il 90% nelle regioni core atteso per strutture a "
           "2 Å o meglio; sotto il 70% fascia scarsa (scelta BioLoom)", digits=1),
)}

MISSING_REASON = {
    "grade": "rapporto senza voto",
    "zdope": "richiede il calcolo DOPE con MODELLER",
    "ga341": "disponibile solo per i modelli costruiti con MODELLER",
    "qmean": "calcolo QMEAN non eseguito su questa struttura",
    "prosa": "ProSA II non eseguito (opzione o licenza)",
    "procheck": "PROCHECK non eseguito",
}

# Segnali per residuo: (chiave, etichetta, regola).
QMEAN_LOCAL_THRESHOLD = 0.6  # SWISS-MODEL: residui con punteggio locale sotto 0,6 di bassa qualità attesa
PROSA_THRESHOLD = 0.0  # ProSA: "valori positivi corrispondono a parti problematiche" (Wiederstein & Sippl 2007)
# Il profilo DOPE per residuo è già una media mobile su 15 residui calcolata da MODELLER (engines/modeller,
# `assess_dope(..., normalize_profile=True, smoothing_window=15)`, come nel tutorial di MODELLER): non si smussa
# di nuovo. Il profilo non ha una soglia assoluta, se ne leggono i picchi: si segnalano i residui oltre
# media + 1 deviazione standard del profilo della stessa struttura.
DOPE_WINDOW = 15
DOPE_SD_FACTOR = 1.0
DOPE_MIN_VALUES = 10
SIGNALS = (
    ("rama", "Ramachandran", "outlier di Ramachandran"),
    ("clash", "clash", "almeno un contatto sterico grave"),
    ("dope", "DOPE", f"DOPE locale (media mobile di {DOPE_WINDOW} residui) oltre media + 1 deviazione standard"),
    ("qmean", "QMEAN", "punteggio QMEAN locale sotto 0,6"),
    ("prosa", "ProSA", "energia ProSA sulla finestra di 10 residui sopra 0"),
)
SIGNAL_LABEL = {k: label for k, label, _rule in SIGNALS}
REGION_MIN_METHODS = 2
REGION_MIN_RESIDUES = 3  # residui segnalati (il residuo tollerato non conta)
REGION_MAX_GAP = 1  # residui non segnalati tollerati tra due segnalati


# ---------------------------------------------------------------------------------------------- scale
def piecewise(value: float, points: tuple[tuple[float, float], ...]) -> float:
    """Interpolazione lineare a tratti; fuori dall'intervallo dei punti si satura al valore estremo."""
    pts = sorted(points)
    if value <= pts[0][0]:
        return pts[0][1]
    for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
        if value <= x1:
            return y0 + (y1 - y0) * (value - x0) / (x1 - x0)
    return pts[-1][1]


def band(score: float) -> str:
    return next(name for name, threshold in BANDS if score >= threshold)


def _num(value: float, digits: int = 2) -> str:
    return f"{value:.{digits}f}".replace(".", ",").replace("-", "−")


def _join(items: list[str]) -> str:
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " e " + items[-1]


# ---------------------------------------------------------------------------------------------- globale
def global_score(values: dict[str, Optional[float]]) -> dict:
    """Media pesata dei punteggi normalizzati dei metodi disponibili (valori None o assenti esclusi)."""
    methods, missing = [], []
    for key, m in METHODS.items():
        raw = values.get(key)
        if raw is None or not isinstance(raw, (int, float)):
            missing.append({"key": key, "label": m.label, "reason": MISSING_REASON[key]})
            continue
        norm = round(piecewise(float(raw), m.points), 1)
        methods.append({"key": key, "label": m.label, "raw": round(float(raw), 4), "normalized": norm,
                        "weight": m.weight, "band": band(norm), "measures": m.measures, "source": m.source,
                        "points": [list(p) for p in m.points]})
    total_weight = sum(m["weight"] for m in methods)
    score = round(sum(m["normalized"] * m["weight"] for m in methods) / total_weight, 1) if total_weight else None
    return {"score": score, "band": band(score) if score is not None else None, "methods_used": len(methods),
            "methods_total": len(METHODS), "methods": methods, "missing": missing}


_HINTS = {
    ("geometry", "energy"): "la stereochimica è regolare ma l'energia indica un impaccamento poco nativo: possibile "
                            "errore di allineamento o di template anche con diedri corretti.",
    ("energy", "geometry"): "il ripiegamento sembra plausibile ma la geometria locale (diedri, contatti) va "
                            "rifinita: controlla loop e residui segnalati.",
    ("fold", "geometry"): "il ripiegamento complessivo è probabilmente corretto; i difetti sono locali.",
    ("fold", "energy"): "il ripiegamento complessivo è probabilmente corretto; i difetti sono locali o di "
                        "impaccamento.",
    ("geometry", "fold"): "la struttura è localmente regolare ma il ripiegamento potrebbe non essere affidabile "
                          "(GA341 dipende anche dall'identità con il template).",
    ("energy", "fold"): "l'energia è buona ma il ripiegamento potrebbe non essere affidabile (GA341 dipende anche "
                        "dall'identità con il template).",
    ("energy", "energy"): "i potenziali statistici non concordano: conferma con le regioni segnalate per residuo.",
    ("geometry", "geometry"): "i criteri stereochimici non concordano: controlla i residui fuori dalle regioni "
                              "favorite di Ramachandran.",
}


def discordances(methods: list[dict]) -> list[dict]:
    """Coppie di metodi in fasce opposte (uno buono, l'altro scarso), con una frase esplicativa."""
    out = []
    good = [m for m in methods if m["band"] == "buona"]
    poor = [m for m in methods if m["band"] == "scarsa"]
    for g in good:
        for p in poor:
            mg, mp = METHODS[g["key"]], METHODS[p["key"]]
            hint = _HINTS.get((mg.scope, mp.scope), "")
            text = (f"{mg.label} giudica il modello buono ({_num(g['raw'], mg.digits)} → {_num(g['normalized'], 0)}/100), "
                    f"{mp.label} scarso ({_num(p['raw'], mp.digits)} → {_num(p['normalized'], 0)}/100). "
                    f"{mg.label} valuta {mg.measures}; {mp.label} valuta {mp.measures}: {hint}")
            out.append({"good": g["key"], "poor": p["key"], "text": text.strip()})
    return out


# ---------------------------------------------------------------------------------------------- per residuo
def dope_threshold(values: list[Optional[float]]) -> Optional[float]:
    vals = [v for v in values if isinstance(v, (int, float))]
    if len(vals) < DOPE_MIN_VALUES:
        return None
    sd = statistics.pstdev(vals)
    if sd <= 0:
        return None
    return round(statistics.fmean(vals) + DOPE_SD_FACTOR * sd, 4)


def residue_flags(residues: list[dict], *, qmean_local: Optional[list[Optional[float]]] = None,
                  prosa_w10: Optional[list[Optional[float]]] = None) -> dict:
    """Segnalazioni dei metodi per ogni residuo del rapporto (stesso ordine di `residues`)."""
    n = len(residues)
    dope = [r.get("dope") for r in residues]
    threshold = dope_threshold(dope)
    available: dict[str, list[bool]] = {}
    if any(r.get("rama") is not None for r in residues):
        available["rama"] = [r.get("rama") == "outlier" for r in residues]
    available["clash"] = [(r.get("clashes") or 0) > 0 for r in residues]
    if threshold is not None:
        available["dope"] = [isinstance(v, (int, float)) and v > threshold for v in dope]
    if qmean_local and any(v is not None for v in qmean_local):
        available["qmean"] = [v is not None and v < QMEAN_LOCAL_THRESHOLD for v in qmean_local]
    if prosa_w10 and any(v is not None for v in prosa_w10):
        available["prosa"] = [v is not None and v > PROSA_THRESHOLD for v in prosa_w10]
    flags = [[k for k in available if available[k][i]] for i in range(n)]
    values: dict[str, list[Optional[float]]] = {}
    if "dope" in available:
        values["dope"] = dope
    if "qmean" in available:
        values["qmean"] = list(qmean_local or [])
    if "prosa" in available:
        values["prosa"] = list(prosa_w10 or [])
    return {
        "signals": [{"key": k, "label": label, "rule": rule, "flagged": sum(available[k])}
                    for k, label, rule in SIGNALS if k in available],
        "flags": flags,
        "counts": [len(f) for f in flags],
        "values": values,
        "dope_threshold": threshold,
    }


_NUM = re.compile(r"^-?\d+")


def _resnum_int(resnum: str) -> Optional[int]:
    m = _NUM.match(str(resnum))
    return int(m.group()) if m else None


def problem_regions(residues: list[dict], flags: list[list[str]], *, min_methods: int = REGION_MIN_METHODS,
                    min_residues: int = REGION_MIN_RESIDUES, max_gap: int = REGION_MAX_GAP) -> list[dict]:
    """Tratti di almeno `min_residues` residui segnalati da almeno `min_methods` metodi.

    Tra due residui segnalati si tollerano fino a `max_gap` residui non segnalati. Un tratto non attraversa il
    confine tra catene né un'interruzione della numerazione (residui mancanti).
    """
    hot = [i for i, f in enumerate(flags) if len(f) >= min_methods]
    groups: list[list[int]] = []
    for i in hot:
        if groups:
            prev = groups[-1][-1]
            if i - prev - 1 <= max_gap and all(_consecutive(residues[k], residues[k + 1]) for k in range(prev, i)):
                groups[-1].append(i)
                continue
        groups.append([i])
    out = []
    for g in groups:
        if len(g) < min_residues:
            continue
        start, end = g[0], g[-1]
        counts: dict[str, int] = {}
        for i in g:
            for k in flags[i]:
                counts[k] = counts.get(k, 0) + 1
        order = [k for k, _label, _rule in SIGNALS]
        methods = sorted(counts, key=lambda k: (-counts[k], order.index(k)))
        chain = residues[start]["chain"]
        first, last = residues[start]["resnum"], residues[end]["resnum"]
        span = first if first == last else f"{first}-{last}"
        labels = [SIGNAL_LABEL[k] for k in methods]
        out.append({"chain": chain, "start": first, "end": last, "start_index": start, "end_index": end,
                    "length": end - start + 1, "flagged": len(g), "methods": methods,
                    "text": f"{_join(labels)} segnalano {span} (catena {chain})"})
    return out


def _consecutive(a: dict, b: dict) -> bool:
    if a["chain"] != b["chain"]:
        return False
    na, nb = _resnum_int(a["resnum"]), _resnum_int(b["resnum"])
    return na is None or nb is None or 0 <= nb - na <= 1


# ---------------------------------------------------------------------------------------------- rapporto
def compute(report: dict, *, qmean: Optional[dict] = None, modeller: Optional[dict] = None) -> dict:
    """Consenso di un rapporto di `engines/quality.analyze`, più QMEAN e i risultati di MODELLER se disponibili.

    `qmean`: risultato di un job qmean (`qmean4_z`, `local`); `modeller`: voce di models.json (`ga341`,
    `zdope`). Lo zDOPE del rapporto (calcolato sulla struttura) prevale su quello di MODELLER.
    """
    residues = report.get("residues") or []
    dope = report.get("dope") or {}
    procheck = ((report.get("procheck") or {}).get("ramachandran") or {})
    modeller = modeller or {}
    values = {
        "grade": (report.get("grade") or {}).get("score"),
        "zdope": dope.get("zscore") if dope.get("zscore") is not None else modeller.get("zdope"),
        "ga341": modeller.get("ga341"),
        "qmean": (qmean or {}).get("qmean4_z"),
        "prosa": (report.get("prosa") or {}).get("zscore"),
        "procheck": procheck.get("core"),
    }
    result = global_score(values)
    warnings = []
    q_local, q_warn = perresidue.qmean_local(residues, (qmean or {}).get("local"))
    p_w10, p_warn = perresidue.prosa_window(residues, (report.get("prosa") or {}).get("profile"))
    warnings += [w for w in (q_warn, p_warn) if w]
    per_residue = residue_flags(residues, qmean_local=q_local, prosa_w10=p_w10)
    regions = problem_regions(residues, per_residue["flags"])
    if result["methods_used"] == 1:
        warnings.append("Un solo metodo disponibile: il consenso coincide con quel metodo.")
    return {
        "version": VERSION,
        **result,
        "discordances": discordances(result["methods"]),
        "per_residue": per_residue,
        "regions": regions,
        "thresholds": {"good": GOOD, "poor": POOR, "qmean_local": QMEAN_LOCAL_THRESHOLD,
                       "prosa_window": PROSA_THRESHOLD, "dope_window": DOPE_WINDOW,
                       "dope_sd_factor": DOPE_SD_FACTOR, "region_min_methods": REGION_MIN_METHODS,
                       "region_min_residues": REGION_MIN_RESIDUES, "region_max_gap": REGION_MAX_GAP},
        "warnings": warnings,
    }
