"""Regioni disordinate, bassa complessità e domini: dove il modello per omologia sarà poco affidabile (F5.5).

Logica pura, dalla sola sequenza, con metodi pubblicati e riproducibili che non richiedono dipendenze né tabelle
stimate da MakPhi. Tre segnali separati:

1. **Disordine: FoldIndex** (Prilusky et al., Bioinformatics 21:3435, 2005), l'analisi carica-idropatia di Uversky
   et al. (Proteins 41:415, 2000) applicata su una finestra scorrevole:

       FoldIndex = 2,785·⟨H⟩ − |⟨R⟩| − 1,151

   con ⟨H⟩ idropatia media della finestra sulla scala di Kyte & Doolittle (J. Mol. Biol. 157:105, 1982) riportata
   su 0–1, cioè (KD + 4,5) / 9, e ⟨R⟩ carica netta media a pH 7 (K, R = +1; D, E = −1; H neutra). La retta
   2,785·⟨H⟩ − 1,151 = |⟨R⟩| è il confine di Uversky tra proteine native ripiegate e intrinsecamente disordinate:
   FoldIndex > 0 = probabilmente ripiegata, < 0 = probabilmente disordinata. Finestra predefinita di FoldIndex: 51
   residui, passo 1, con il valore assegnato al residuo centrale. Il server di FoldIndex dà valori solo ai centri
   delle finestre complete; qui ogni residuo ha un valore e nei primi e negli ultimi 25 residui la finestra si
   accorcia (fino a 26 residui all'estremità), come nei profili a finestra troncata. Una finestra fissa appoggiata al
   bordo ripeterebbe su 25 residui il valore dell'ultima finestra: all'ubiquitina (ripiegata, FoldIndex −0,02
   sull'ultima finestra 26–76) darebbe una regione disordinata 32–76 che non esiste.
2. **Bassa complessità: SEG** (Wootton & Federhen, Comput. Chem. 17:149, 1993; Methods Enzymol. 266:554, 1996) con i
   parametri standard per le proteine: finestra 12, K1 = 2,2 e K2 = 2,5 bit (entropia di Shannon della composizione
   della finestra). Le finestre con entropia ≤ K1 innescano un segmento, esteso alle finestre contigue con entropia
   ≤ K2 e poi ridotto al sottotratto di composizione meno probabile (misura P0, `_trim`), come nel programma `seg`.
   È un segnale diverso dal disordine: un tratto poli-Q o ricco di prolina può essere ordinato o no, ma di solito non
   ha template affidabili e allinea male.
3. **Domini** (`target_domains`, `coverage_assessment`): i domini InterPro di un'accessione UniProt tracciati sulla
   sequenza del bersaglio e, per ogni template, la frazione di ciascun dominio coperta dall'allineamento. Senza
   accessione non si inventano domini.

**Perché FoldIndex e non IUPred:** IUPred (Dosztányi et al., J. Mol. Biol. 347:827, 2005) stima l'energia di
interazione a coppie con una matrice 20 × 20 e converte il punteggio in probabilità con una distribuzione tabulata; i
file ufficiali sono distribuiti con IUPred per uso accademico, e ricostruirli dalle pubblicazioni non darebbe gli stessi
numeri del programma. FoldIndex è definito da due costanti pubblicate e dalla scala di Kyte-Doolittle: chiunque ottiene
gli stessi valori.

**Limiti** (da dichiarare all'utente): non è IUPred2A, né un predittore di deep learning (flDPnn, metapredict, pLDDT
di AlphaFold), che nelle valutazioni CAID sono molto più accurati. FoldIndex vede solo carica e idropatia medie:
- non riconosce il disordine con composizione simile alle proteine ripiegate (l'N-terminale anfipatico
  dell'α-sinucleina, disordinato in soluzione, risulta ordinato) né i loop disordinati corti;
- la finestra di 51 residui sposta i confini delle regioni anche di una ventina di residui (una regione disordinata
  accanto a un tratto vicino allo zero lo trascina con sé) e diluisce le code terminali corte;
- non conosce il contesto: domini che si ripiegano solo con un partner, un metallo, i ponti disolfuro o una membrana.
Va letto come un'indicazione su dove il modello sarà poco affidabile, non come una misura.

**Regioni disordinate lunghe:** almeno 30 residui con FoldIndex < 0, la soglia delle "long disordered regions"
(Ward et al., J. Mol. Biol. 337:635, 2004). Tra due tratti disordinati si tollerano fino a 3 residui sopra
la soglia: meno di un giro d'elica α (3,6 residui) non basta a formare un elemento di struttura secondaria, e con la
finestra di 51 residui un'oscillazione così breve attorno allo zero non è un segnale. I residui tollerati fanno parte
della regione ma non contano nei 30 (come le regioni problematiche di `domain/consensus`).
"""

from __future__ import annotations

import math
from typing import Iterable, Optional

from ..core.errors import AppError
from . import sequences

# ---------------------------------------------------------------------------------------------- scale
# Idropatia di Kyte & Doolittle (1982), tabella 2.
KYTE_DOOLITTLE = {
    "A": 1.8, "R": -4.5, "N": -3.5, "D": -3.5, "C": 2.5, "Q": -3.5, "E": -3.5, "G": -0.4, "H": -3.2, "I": 4.5,
    "L": 3.8, "K": -3.9, "M": 1.9, "F": 2.8, "P": -1.6, "S": -0.8, "T": -0.7, "W": -0.9, "Y": -1.3, "V": 4.2,
}
# Carica netta a pH 7 dell'analisi di Uversky (istidina neutra).
CHARGE = {"K": 1.0, "R": 1.0, "D": -1.0, "E": -1.0}
# Lettere IUPAC ambigue o rare: B = D/N, Z = E/Q, J = I/L (media delle due), U (selenocisteina) come C, O
# (pirrolisina) come K. X e le altre lettere non entrano nelle medie della finestra.
_AMBIGUOUS = {"B": ("D", "N"), "Z": ("E", "Q"), "J": ("I", "L"), "U": ("C",), "O": ("K",)}

FOLDINDEX_SLOPE = 2.785  # Uversky et al. 2000: ⟨H⟩ di confine = (|⟨R⟩| + 1,151) / 2,785
FOLDINDEX_INTERCEPT = 1.151
FOLDINDEX_WINDOW = 51  # finestra predefinita di FoldIndex (Prilusky et al. 2005)
THRESHOLD = 0.0  # FoldIndex < 0 = disordinato
MIN_REGION = 30  # "long disordered region" (Ward et al. 2004)
MAX_GAP = 3  # residui sopra soglia tollerati tra due tratti disordinati (< un giro d'elica, 3,6 residui)

SEG_ALPHABET = "ACDEFGHIKLMNPQRSTVWY"
SEG_WINDOW = 12  # parametri standard di SEG per le proteine: finestra 12, K1 2,2, K2 2,5 bit
SEG_LOCUT = 2.2
SEG_HICUT = 2.5
SEG_MAXTRIM = 100  # accorciamento massimo del segmento nell'ottimizzazione, come `maxtrim` di seg

# Lunghezza massima predefinita, come config.SEQUENCE_MAX_DEFAULT; chi chiama passa quella delle Impostazioni
# (`max_sequence_length`), la sequenza più lunga che MakPhi modella
MAX_LENGTH = 5000

# Domini di InterPro usati per la copertura: le voci di tipo "domain"; senza, le superfamiglie omologhe (domini
# strutturali di CATH-Gene3D e SUPERFAMILY). Famiglie, siti e ripetizioni brevi non sono unità da modellare a parte.
DOMAIN_TYPES = ("domain",)
FALLBACK_DOMAIN_TYPES = ("homologous_superfamily",)
DOMAIN_MERGE_OVERLAP = 0.5  # voci dello stesso dominio: sovrapposte per almeno metà del tratto più corto
# Copertura di un dominio da parte di un template: i confini delle firme di InterPro (Pfam, SMART, PROSITE…) sono
# approssimati di qualche residuo, quindi "coperto" dal 90% in su; sotto il 10% il template non lo descrive.
DOMAIN_COVERED = 90.0
DOMAIN_UNCOVERED = 10.0
# Una regione disordinata è "fuori dalla copertura" quando almeno metà dei suoi residui non ha un residuo del template
# di fronte: MODELLER la costruisce senza vincoli dal template.
DISORDER_UNCOVERED_FRACTION = 0.5

METHOD = "FoldIndex (Prilusky et al. 2005)"
SEG_METHOD = "SEG (Wootton & Federhen 1993)"
LIMITS = (
    "Predizione dalla sola sequenza con FoldIndex (carica netta e idropatia medie su 51 residui): non è IUPred2A né "
    "un predittore di deep learning, ed è molto meno accurato.",
    "Non riconosce il disordine con composizione simile alle proteine ripiegate né i loop disordinati corti; i confini "
    "delle regioni sono incerti di circa metà finestra (25 residui).",
    "Non conosce il contesto: regioni che si ripiegano solo con un partner, un cofattore o i ponti disolfuro "
    "risultano disordinate.",
)


# ---------------------------------------------------------------------------------------------- utilità
def _residue_terms(aa: str) -> Optional[tuple[float, float]]:
    """(idropatia normalizzata 0–1, carica) di una lettera; None per X e le lettere non riconosciute."""
    letters = _AMBIGUOUS.get(aa, (aa,))
    if not all(c in KYTE_DOOLITTLE for c in letters):
        return None
    kd = sum(KYTE_DOOLITTLE[c] for c in letters) / len(letters)
    charge = sum(CHARGE.get(c, 0.0) for c in letters) / len(letters)
    return (kd + 4.5) / 9.0, charge


def clean(sequence: str, max_length: int = MAX_LENGTH) -> str:
    """Sequenza proteica validata (IUPAC esteso) e limitata a `max_length` residui; accetta anche un FASTA con una
    sola sequenza (l'intestazione non entra nella sequenza)."""
    text = sequence or ""
    if text.lstrip().startswith(">"):
        records = sequences.parse_fasta(text)
        if len(records) > 1:
            raise AppError("Indica una sola sequenza: il FASTA ne contiene più di una.", code="multiple_sequences")
        text = records[0].sequence if records else ""
    seq, _kind = sequences.validate_sequence(text, "protein")
    if len(seq) > max_length:
        raise AppError(f"Sequenza troppo lunga ({len(seq)} residui, massimo {max_length}).", code="sequence_too_long")
    if sequences.detect_kind(seq) == "nucleic":  # FoldIndex e SEG (parametri per proteine) valgono per le proteine
        raise AppError("La sequenza sembra di un acido nucleico: disordine e bassa complessità si calcolano sulle "
                       "proteine.", code="not_protein")
    return seq


def ranges(mask: Iterable[bool]) -> list[tuple[int, int]]:
    """Tratti contigui veri di una maschera: (inizio, fine) 0-based, estremi compresi."""
    out: list[tuple[int, int]] = []
    start = None
    values = list(mask)
    for i, flag in enumerate(values):
        if flag and start is None:
            start = i
        elif not flag and start is not None:
            out.append((start, i - 1))
            start = None
    if start is not None:
        out.append((start, len(values) - 1))
    return out


# ---------------------------------------------------------------------------------------------- FoldIndex
def foldindex(sequence: str, window: int = FOLDINDEX_WINDOW) -> list[Optional[float]]:
    """FoldIndex per residuo (Prilusky et al. 2005): 2,785·⟨H⟩ − |⟨R⟩| − 1,151 sulla finestra del residuo.

    La finestra è centrata sul residuo (25 residui per parte con la finestra di 51) e si accorcia vicino alle
    estremità. Le medie si fanno sulle sole lettere con un valore (X esclusa); None per una finestra senza lettere
    valide.
    """
    seq = sequence.upper()
    n = len(seq)
    if n == 0:
        return []
    half = max(1, int(window)) // 2
    terms = [_residue_terms(c) for c in seq]
    # somme prefisse di idropatia, carica e numero di lettere valide
    ph, pq, pc = [0.0], [0.0], [0]
    for t in terms:
        ph.append(ph[-1] + (t[0] if t else 0.0))
        pq.append(pq[-1] + (t[1] if t else 0.0))
        pc.append(pc[-1] + (1 if t else 0))
    out: list[Optional[float]] = []
    for i in range(n):
        start, end = max(0, i - half), min(n, i + half + 1)
        count = pc[end] - pc[start]
        if not count:
            out.append(None)
            continue
        h = (ph[end] - ph[start]) / count
        r = (pq[end] - pq[start]) / count
        out.append(FOLDINDEX_SLOPE * h - abs(r) - FOLDINDEX_INTERCEPT)
    return out


def disordered_regions(scores: list[Optional[float]], *, threshold: float = THRESHOLD, min_length: int = MIN_REGION,
                       max_gap: int = MAX_GAP) -> list[tuple[int, int]]:
    """Regioni disordinate: almeno `min_length` residui con punteggio < `threshold`, con al più `max_gap` residui
    sopra la soglia (o senza valore) tollerati tra due tratti disordinati. (inizio, fine) 0-based, estremi compresi:
    la regione comincia e finisce con un residuo disordinato."""
    hot = [i for i, v in enumerate(scores) if v is not None and v < threshold]
    groups: list[list[int]] = []
    for i in hot:
        if groups and i - groups[-1][-1] - 1 <= max_gap:
            groups[-1].append(i)
        else:
            groups.append([i])
    return [(g[0], g[-1]) for g in groups if len(g) >= min_length]


# ---------------------------------------------------------------------------------------------- SEG
def _entropy(counts: list[int]) -> float:
    """Entropia di Shannon della composizione, in bit (come `entropy` di seg)."""
    total = sum(counts)
    if not total:
        return 0.0
    return -sum(c / total * math.log2(c / total) for c in counts if c)


def _lnfac(n: int) -> float:
    return math.lgamma(n + 1)


def _log_probability(counts: list[int], total: int) -> float:
    """ln P0 della composizione (Wootton & Federhen 1993, `getprob` di seg): numero di sequenze di lunghezza `total`
    con una composizione dello stesso "stato" (stessi conteggi a meno di permutare le 20 lettere) su 20^total.

    ln P0 = ln[20! / (∏ m_k! · m_0!)] + ln[L! / ∏ n_i!] − L·ln 20, con m_k il numero di lettere presenti k volte e
    m_0 quello delle lettere assenti.
    """
    classes: dict[int, int] = {}
    for c in counts:
        classes[c] = classes.get(c, 0) + 1
    assignments = _lnfac(len(SEG_ALPHABET)) - sum(_lnfac(m) for m in classes.values())
    permutations = _lnfac(total) - sum(_lnfac(c) for c in counts)
    return assignments + permutations - total * math.log(len(SEG_ALPHABET))


def _counts(seq: str) -> list[int]:
    out = [0] * len(SEG_ALPHABET)
    for c in seq:
        k = SEG_ALPHABET.find(c)
        if k >= 0:
            out[k] += 1
    return out


def _trim(seq: str, maxtrim: int = SEG_MAXTRIM) -> tuple[int, int]:
    """Sottotratto di `seq` con la composizione meno probabile (`trim` di seg): (inizio, fine) 0-based.

    Si provano tutte le finestre di lunghezza da quella intera fino a `maxtrim` residui in meno (almeno 2); a parità
    vince la più lunga e, a pari lunghezza, la prima."""
    n = len(seq)
    best, lend, rend = 1.0, 0, n - 1
    minlen = max(1, n - maxtrim)
    for length in range(n, minlen, -1):
        counts = _counts(seq[:length])
        for i in range(n - length + 1):
            if i:
                for c, delta in ((seq[i - 1], -1), (seq[i + length - 1], 1)):
                    k = SEG_ALPHABET.find(c)
                    if k >= 0:
                        counts[k] += delta
            prob = _log_probability(counts, sum(counts))
            if prob < best:
                best, lend, rend = prob, i, i + length - 1
    return lend, rend


def _window_entropies(seq: str, window: int) -> list[Optional[float]]:
    """Entropia della finestra centrata su ogni residuo (None dove la finestra non entra o contiene lettere non
    standard: X, B, Z… non si valutano)."""
    n = len(seq)
    out: list[Optional[float]] = [None] * n
    downset = (window + 1) // 2 - 1
    for start in range(0, n - window + 1):
        chunk = seq[start:start + window]
        if all(c in SEG_ALPHABET for c in chunk):
            out[start + downset] = _entropy(_counts(chunk))
    return out


def _segseq(seq: str, offset: int, out: list[tuple[int, int]], window: int, locut: float, hicut: float,
            maxtrim: int) -> None:
    """Segmenti a bassa complessità di `seq` (algoritmo `segseq` di seg), aggiunti a `out` con `offset`."""
    n = len(seq)
    if window > n:
        return
    entropy = _window_entropies(seq, window)
    downset = (window + 1) // 2 - 1
    upset = window - downset
    last = n - upset
    lowlim = downset
    i = downset
    while i <= last:
        h = entropy[i]
        if h is not None and h <= locut:
            lo = i  # estensione a sinistra e a destra sulle finestre con entropia ≤ K2
            while lo - 1 >= lowlim and entropy[lo - 1] is not None and entropy[lo - 1] <= hicut:
                lo -= 1
            hi = i
            while hi + 1 <= last and entropy[hi + 1] is not None and entropy[hi + 1] <= hicut:
                hi += 1
            left, right = lo - downset, hi + upset - 1
            t_left, t_right = _trim(seq[left:right + 1], maxtrim)
            trimmed_left, trimmed_right = left + t_left, left + t_right
            if i + upset - 1 < trimmed_left:  # la finestra d'innesco è rimasta fuori a sinistra: si riesamina
                _segseq(seq[left:trimmed_left], offset + left, out, window, locut, hicut, maxtrim)
            out.append((trimmed_left + offset, trimmed_right + offset))
            i = min(hi, trimmed_right + downset)
            lowlim = i + 1
        i += 1


def low_complexity(sequence: str, *, window: int = SEG_WINDOW, locut: float = SEG_LOCUT, hicut: float = SEG_HICUT,
                   maxtrim: int = SEG_MAXTRIM) -> list[tuple[int, int]]:
    """Segmenti a bassa complessità di SEG: (inizio, fine) 0-based, estremi compresi, fusi se si sovrappongono."""
    raw: list[tuple[int, int]] = []
    _segseq(sequence.upper(), 0, raw, window, locut, hicut, maxtrim)
    merged: list[list[int]] = []
    for start, end in sorted(raw):
        if merged and start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    return [(a, b) for a, b in merged]


def _composition(chunk: str, top: int = 3) -> str:
    """"Q 18, P 3": le lettere più frequenti di un tratto."""
    counts: dict[str, int] = {}
    for c in chunk:
        counts[c] = counts.get(c, 0) + 1
    ordered = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[:top]
    return ", ".join(f"{c} {n}" for c, n in ordered)


# ---------------------------------------------------------------------------------------------- predizione
def span_text(start: int, end: int) -> str:
    """"96–140" (1-based)."""
    return str(start) if start == end else f"{start}–{end}"


def predict(sequence: str, *, window: int = FOLDINDEX_WINDOW, max_length: int = MAX_LENGTH) -> dict:
    """Disordine (FoldIndex), regioni disordinate lunghe e regioni a bassa complessità (SEG) di una sequenza.

    Posizioni 1-based. `scores` segue la scala di FoldIndex (negativo = disordinato; in pratica tra −1,5 e +0,8);
    `fraction` è la frazione di residui nelle regioni disordinate lunghe, `below_fraction` quella dei residui con
    FoldIndex < 0 anche fuori dalle regioni.
    """
    seq = clean(sequence, max_length)
    n = len(seq)
    scores = foldindex(seq, window)
    regions = disordered_regions(scores)
    in_regions = sum(b - a + 1 for a, b in regions)
    below = sum(1 for v in scores if v is not None and v < THRESHOLD)
    lcr = low_complexity(seq)
    numeric = [v for v in scores if v is not None]
    return {
        "method": METHOD,
        "length": n,
        "window": min(window, n),
        "scale": {"name": "FoldIndex", "threshold": THRESHOLD, "disordered": "below",
                  "formula": "2,785·⟨H⟩ − |⟨R⟩| − 1,151 (⟨H⟩ Kyte-Doolittle su 0–1, ⟨R⟩ carica netta media)",
                  "min": round(min(numeric), 4) if numeric else None,
                  "max": round(max(numeric), 4) if numeric else None},
        "scores": [None if v is None else round(v, 4) for v in scores],
        "regions": [{"start": a + 1, "end": b + 1, "length": b - a + 1,
                     "disordered": sum(1 for v in scores[a:b + 1] if v is not None and v < THRESHOLD),
                     "mean_score": round(sum(v for v in scores[a:b + 1] if v is not None) / (b - a + 1), 4)}
                    for a, b in regions],
        "fraction": round(in_regions / n, 4),
        "below_fraction": round(below / n, 4),
        "params": {"window": window, "threshold": THRESHOLD, "min_region": MIN_REGION, "max_gap": MAX_GAP},
        "low_complexity": {
            "method": SEG_METHOD,
            "params": {"window": SEG_WINDOW, "k1": SEG_LOCUT, "k2": SEG_HICUT},
            "regions": [{"start": a + 1, "end": b + 1, "length": b - a + 1, "composition": _composition(seq[a:b + 1])}
                        for a, b in lcr],
            "fraction": round(sum(b - a + 1 for a, b in lcr) / n, 4),
        },
        "limits": list(LIMITS),
    }


def residue_mask(sequence: str, *, window: int = FOLDINDEX_WINDOW) -> tuple[list[Optional[float]], list[bool]]:
    """FoldIndex e appartenenza a una regione disordinata lunga per ogni lettera (senza validazione: le lettere
    sconosciute non entrano nelle medie)."""
    scores = foldindex(sequence, window)
    mask = [False] * len(scores)
    for a, b in disordered_regions(scores):
        for i in range(a, b + 1):
            mask[i] = True
    return scores, mask


# ---------------------------------------------------------------------------------------------- domini e copertura
def target_domains(entries: list[dict], length: Optional[int] = None) -> list[dict]:
    """Domini di InterPro (`remote.interpro_domains`) come tratti del bersaglio, ordinati per inizio.

    Si usano le voci di tipo "domain"; se non ce ne sono, le superfamiglie omologhe. Ogni frammento di una voce è un
    tratto; con `length` i tratti si limitano alla sequenza (quelli del tutto fuori si scartano). Voci diverse per lo
    stesso dominio (firme di banche dati diverse integrate in InterPro con confini leggermente diversi) si fondono
    quando si sovrappongono per almeno metà del tratto più corto: sono un dominio solo, non due da modellare a parte.
    """
    def pick(types: tuple[str, ...]) -> list[dict]:
        out = []
        for e in entries or []:
            if str(e.get("type") or "").lower() not in types:
                continue
            for loc in e.get("locations") or []:
                try:
                    start, end = int(loc.get("start")), int(loc.get("end"))
                except (TypeError, ValueError):
                    continue
                if length is not None:
                    start, end = max(1, start), min(length, end)
                if start > end:
                    continue
                out.append({"accession": e.get("accession") or "", "name": e.get("name") or e.get("accession") or "",
                            "type": str(e.get("type") or "").lower(), "start": start, "end": end})
        return out

    domains = sorted(pick(DOMAIN_TYPES) or pick(FALLBACK_DOMAIN_TYPES),
                     key=lambda d: (d["start"], -d["end"], d["accession"]))
    merged: list[dict] = []
    for d in domains:
        last = merged[-1] if merged else None
        if last is not None:
            overlap = min(last["end"], d["end"]) - max(last["start"], d["start"]) + 1
            shorter = min(last["end"] - last["start"], d["end"] - d["start"]) + 1
            if overlap >= DOMAIN_MERGE_OVERLAP * shorter:
                last["start"], last["end"] = min(last["start"], d["start"]), max(last["end"], d["end"])
                if d["accession"] not in last["accessions"]:
                    last["accessions"].append(d["accession"])
                    if d["name"] and d["name"] not in last["name"].split(" / "):
                        last["name"] = f"{last['name']} / {d['name']}"
                continue
        merged.append({**d, "accessions": [d["accession"]]})
    return merged


def covered_ranges(covered: Iterable[int]) -> list[list[int]]:
    """Posizioni coperte (1-based) come intervalli [inizio, fine]."""
    positions = sorted(set(covered))
    out: list[list[int]] = []
    for p in positions:
        if out and p == out[-1][1] + 1:
            out[-1][1] = p
        else:
            out.append([p, p])
    return out


def coverage_assessment(length: int, disorder_regions: list[dict], domains: list[dict],
                        covered: Iterable[int]) -> dict:
    """Avvisi prima di modellare: regioni disordinate lunghe fuori dalla copertura del template, domini non coperti
    o coperti in parte, con un suggerimento.

    `covered`: posizioni del bersaglio (1-based) con un residuo del template di fronte; `disorder_regions` e
    `domains` con `start`/`end` 1-based (da `predict` e `target_domains`).
    """
    cov = {p for p in covered if 1 <= p <= length}
    warnings: list[dict] = []
    uncovered_disorder = []
    for r in disorder_regions:
        span = range(int(r["start"]), int(r["end"]) + 1)
        missing = [p for p in span if p not in cov]
        text_span = span_text(span.start, span.stop - 1)
        if len(missing) >= DISORDER_UNCOVERED_FRACTION * len(span):
            parts = ", ".join(span_text(a, b) for a, b in covered_ranges(missing))
            uncovered_disorder.append(parts)
            warnings.append({
                "kind": "disorder_uncovered", "tone": "warn", "start": span.start, "end": span.stop - 1,
                "text": f"La regione {text_span} ({len(span)} residui) è predetta disordinata e il template non copre "
                        f"{parts}: MODELLER la costruirà senza vincoli dal template, come un tratto esteso senza "
                        "significato strutturale. Nel modello sarà poco affidabile."})
        else:
            warnings.append({
                "kind": "disorder_covered", "tone": "info", "start": span.start, "end": span.stop - 1,
                # Due spiegazioni, nessuna da escludere a priori: FoldIndex vede solo carica e idropatia medie e
                # sbaglia anche su proteine ripiegate molto idrofile (3IU0: GRAVY −1,07, 89% della catena in una
                # regione "disordinata" in una struttura cristallina a 1,9 Å).
                "text": f"La regione {text_span} è predetta disordinata ma il template la copre e il modello ne "
                        "seguirà la conformazione. Nel template può essere ordinata per un partner, un ligando o "
                        "l'impaccamento del cristallo, oppure la predizione è sbagliata: FoldIndex usa solo carica e "
                        "idropatia medie e segna come disordinate anche proteine ripiegate molto idrofile."})
    assessed = []
    for d in domains:
        span = range(int(d["start"]), int(d["end"]) + 1)
        pct = round(100.0 * sum(1 for p in span if p in cov) / len(span), 1) if len(span) else 0.0
        status = "covered" if pct >= DOMAIN_COVERED else "uncovered" if pct < DOMAIN_UNCOVERED else "partial"
        assessed.append({**d, "covered_pct": pct, "status": status})
        label = f"{d.get('name') or d.get('accession')} ({span_text(span.start, span.stop - 1)})"
        share = f"{pct:g}".replace(".", ",")
        if status == "uncovered":
            warnings.append({"kind": "domain_uncovered", "tone": "warn", "start": span.start, "end": span.stop - 1,
                             "text": f"Il dominio {label} non è coperto dal template ({share}% dei residui): nel "
                                     "modello non avrà un ripiegamento affidabile."})
        elif status == "partial":
            warnings.append({"kind": "domain_partial", "tone": "warn", "start": span.start, "end": span.stop - 1,
                             "text": f"Il dominio {label} è coperto solo in parte ({share}% dei residui): la parte "
                                     "scoperta sarà costruita senza vincoli dal template."})
    suggestion = None
    incomplete = [d for d in assessed if d["status"] != "covered"]
    if incomplete and len(assessed) > 1:
        parts = ", ".join(f"{d['name']} {span_text(d['start'], d['end'])}" for d in assessed)
        suggestion = ("Modella per domini: cerca un template per ciascun dominio (Ricerca template con la sequenza del "
                      f"solo dominio; domini: {parts}) e costruisci i domini separatamente, oppure usa insieme più "
                      "template che coprano domini diversi.")
    elif incomplete:
        d = incomplete[0]
        suggestion = (f"Cerca un template che copra il dominio {d['name']} ({span_text(d['start'], d['end'])}), "
                      "anche con un motore più sensibile (NCBI BLAST) o un modello di AlphaFold DB.")
    elif uncovered_disorder:
        suggestion = ("Valuta di escludere dal bersaglio i tratti disordinati non coperti "
                      f"({', '.join(uncovered_disorder)}) e di modellare solo la parte coperta dal template; se li tieni, "
                      "leggi quelle regioni del modello come ipotesi, non come struttura.")
    return {"coverage": round(100.0 * len(cov) / length, 1) if length else 0.0, "covered": covered_ranges(cov),
            "warnings": warnings, "domains": assessed, "suggestion": suggestion}
