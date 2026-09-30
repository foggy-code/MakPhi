"""Scelta ragionata dei template: punteggio composito 0–100 per ogni hit e selezione di template complementari.

Logica pura (niente rete né file): lavora sugli hit dei motori di ricerca (`engines/search`), con i metadati di RCSB
quando ci sono (metodo, risoluzione, R-free, data di rilascio, ligandi, stato oligomerico).

1. **Punteggio.** Ogni componente (`COMPONENTS`) porta il suo valore grezzo su 0–100 con una funzione lineare a
   tratti (`consensus.piecewise`), costruita perché, come nel consenso di qualità, la soglia "affidabile" della
   letteratura cada a 70 e quella "problematica" a 40: le fasce buona ≥ 70, intermedia 40–70, scarsa < 40 valgono
   per ogni componente e per il punteggio. Il punteggio è la media pesata delle sole componenti disponibili (un
   dato assente non penalizza: non entra nel conto) e ogni componente ha la sua motivazione in italiano. Sotto la
   curva della zona crepuscolare di Rost la copertura conta al più quanto l'identità: un template in zona
   crepuscolare non arriva mai alla fascia buona.
2. **Selezione di template complementari** (`select_complementary`): il primo è il migliore; i successivi entrano
   solo se coprono residui del bersaglio non ancora coperti o migliorano l'identità locale, e se non sono
   ridondanti con quelli già scelti. Ogni scelta ed esclusione ha la sua motivazione.

Le posizioni sul bersaglio vengono dagli allineamenti della ricerca (`query_aligned`, `subject_aligned`,
`query_start`): due template si confrontano attraverso il bersaglio (allineamento transitivo), senza scaricare le
strutture.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Iterable, Optional

from .consensus import band, piecewise
from .structure import COMMON_ADDITIVES, WATER_NAMES

VERSION = 1


@dataclass(frozen=True)
class Component:
    key: str
    label: str
    weight: float
    source: str


# Pesi dichiarati (somma 100). Identità e copertura decidono quasi tutta l'accuratezza di un modello per omologia e
# pesano uguale: l'identità dice quanto è accurata la parte con template (l'errore dello scheletro cresce in modo
# esponenziale con la divergenza di sequenza, Chothia & Lesk 1986, e sotto il 30–50% dominano gli errori di
# allineamento, Baker & Šali 2001), la copertura quanta parte del modello ha un template (il resto MODELLER lo
# costruisce senza vincoli). Con questi pesi un template completo al 40% di identità batte uno quasi identico che
# copre metà del bersaglio; uno in zona crepuscolare no. La qualità sperimentale del template (risoluzione, R-free,
# metodo) conta meno: pesa sui dettagli (catene laterali, loop), non sul ripiegamento. L'E-value pesa poco perché
# dipende già da identità e lunghezza. Ligandi e stato oligomerico sono informativi (peso 0): dicono se il template
# è olo o apo e com'è assemblato, ma non rendono un template migliore di un altro in assoluto.
COMPONENTS: tuple[Component, ...] = (
    Component("identity", "Identità", 30.0,
              "Rost 1999 (curva della zona crepuscolare in funzione dei residui allineati); Baker & Šali 2001 "
              "(oltre il 50% modelli di alta accuratezza)"),
    Component("coverage", "Copertura del bersaglio", 30.0,
              "frazione del modello costruita con i vincoli del template: i residui non coperti MODELLER li costruisce "
              "senza template"),
    Component("evalue", "E-value", 5.0, "Pearson 2013: E < 10⁻³ omologia affidabile in una ricerca su database"),
    Component("resolution", "Risoluzione", 12.0,
              "Wlodawer et al. 2008 (raggi X e crio-EM); NMR e modelli predetti con regole esplicite"),
    Component("rfree", "R-free", 5.0, "Brünger 1992; Kleywegt & Brünger 1996 (oltre 0,40 errori gravi)"),
    Component("method", "Metodo", 8.0, "affidabilità delle coordinate per metodo sperimentale; predetto = ipotesi"),
    Component("gaps", "Lacune dell'allineamento", 7.0,
              "Fiser, Do & Šali 2000: l'errore dei loop ricostruiti cresce con la lunghezza"),
    Component("date", "Data di rilascio", 3.0,
              "R-free dal 1992 (Brünger 1992), fattori di struttura obbligatori dal 2008, rapporti di validazione "
              "wwPDB dal 2014 (Read et al. 2011; Gore et al. 2017)"),
    Component("ligands", "Ligandi e cofattori", 0.0, "informativo: olo o apo"),
    Component("oligomer", "Stato oligomerico", 0.0, "informativo: assemblaggio biologico 1 di RCSB"),
)
COMPONENT = {c.key: c for c in COMPONENTS}

# Identità: 0 → 0, curva di Rost → 40 (sotto: zona crepuscolare, fascia scarsa), 50% → 70 (fascia buona: modelli di
# alta accuratezza secondo Baker & Šali 2001), 100% → 100.
HIGH_ACCURACY_IDENTITY = 50.0
# Copertura (%) → punteggio: lineare. 40% (soglia di copertura predefinita della pipeline) è il confine della fascia
# scarsa, 70% quello della buona.
COVERAGE_POINTS = ((0.0, 0.0), (100.0, 100.0))
# −log10(E-value) → punteggio: E = 1 → 0, 10⁻² → 40, 10⁻³ → 70 (Pearson 2013), ≤ 10⁻¹⁰ → 100
EVALUE_POINTS = ((0.0, 0.0), (2.0, 40.0), (3.0, 70.0), (10.0, 100.0))
# Risoluzione (Å) → punteggio, raggi X e crio-EM (Wlodawer et al. 2008): sotto 2,5 Å catene laterali ben definite
# (fascia buona), 2,5–3,5 Å scheletro tracciabile e catene laterali incerte, oltre 3,5 Å solo lo scheletro.
RESOLUTION_POINTS = ((1.0, 100.0), (2.5, 70.0), (3.5, 40.0), (5.0, 0.0))
# NMR: nessuna risoluzione. Valore fisso nella fascia intermedia (scelta BioLoom), come una struttura
# cristallografica a circa 2,8 Å: insieme di conformazioni da vincoli NOE, loop e catene laterali meno definiti.
NMR_RESOLUTION_SCORE = 60.0
# Modelli predetti: pLDDT medio → punteggio con le fasce di AlphaFold DB (Tunyasuvunakool et al. 2021): 70 (inizio
# di "confidente", scheletro in genere corretto) → 40, 90 ("molto alta") → 70, sotto 50 (spesso disordine) → 0.
PLDDT_POINTS = ((50.0, 0.0), (70.0, 40.0), (90.0, 70.0), (100.0, 100.0))
# R-free → punteggio: ≤ 0,20 ottimo, 0,25 buono, 0,30 confine della fascia scarsa (scelta BioLoom), ≥ 0,40 errori
# gravi (Kleywegt & Brünger 1996). Valori assoluti: i rapporti wwPDB usano percentili per risoluzione, che qui non
# ci sono.
RFREE_POINTS = ((0.20, 100.0), (0.25, 70.0), (0.30, 40.0), (0.40, 0.0))
# Metodo → punteggio (scelta BioLoom): pesa solo per ciò che la risoluzione non dice.
METHOD_SCORE = {"xray": 100.0, "em": 90.0, "nmr": 80.0, "other": 50.0, "predicted": 40.0}
METHOD_LABEL = {"xray": "diffrazione", "em": "microscopia elettronica", "nmr": "NMR",
                "other": "altro metodo sperimentale", "predicted": "modello predetto"}
# Lacuna più lunga (residui) → punteggio (Fiser, Do & Šali 2000): fino a circa 8 residui un loop ricostruito è in
# genere accettabile, oltre 15 poco affidabile. Ogni lacuna oltre la seconda toglie 2 punti: è un punto in più dove
# l'allineamento può essere spostato.
GAP_POINTS = ((0.0, 100.0), (4.0, 90.0), (8.0, 70.0), (15.0, 40.0), (30.0, 0.0))
GAP_EXTRA_PENALTY = 2.0
# Anno di rilascio → punteggio: prima del 1992 niente R-free (Brünger 1992); dal 2008 fattori di struttura
# obbligatori nel deposito; dal 2014 rapporti di validazione wwPDB.
DATE_POINTS = ((1990.0, 40.0), (2008.0, 80.0), (2014.0, 100.0))

# Additivi e tamponi di cristallizzazione frequenti (elenco non esaustivo), oltre a quelli di domain/structure: non
# fanno di un template una struttura olo.
EXTRA_ADDITIVES = frozenset({
    "PG4", "PGE", "1PE", "P6G", "2PE", "12P", "15P", "PE4", "MPD", "TRS", "EPE", "MES", "CIT", "BME", "DTT", "IMD",
    "ACY", "NH4", "SCN", "MLI", "TLA", "BU3", "HEZ", "PEG", "SIN", "MRD", "CAC", "AZI", "UNX", "UNL",
})
ADDITIVES = frozenset(COMMON_ADDITIVES) | EXTRA_ADDITIVES | frozenset(WATER_NAMES)

# ------------------------------------------------------------------------------ selezione complementare
# Template al massimo (predefinito): con MODELLER due o tre template scelti bene migliorano in media il modello
# rispetto al solo migliore, aggiungerne altri non aiuta e può peggiorarlo (Larsson et al. 2008).
MAX_TEMPLATES = 3
MAX_TEMPLATES_LIMIT = 5
# Residui nuovi minimi: un tratto più corto MODELLER lo ricostruisce come un loop senza template con accuratezza in
# genere accettabile (Fiser, Do & Šali 2000); da 10 residui un template per quel tratto vale più della ricostruzione.
MIN_NEW_RESIDUES = 10
# Ridondanza: identità reciproca oltre il 95% sui residui del bersaglio coperti da entrambi (almeno MIN_SHARED).
# A quell'identità le strutture sono quasi uguali (Chothia & Lesk 1986: RMSD atteso del nucleo circa 0,45 Å),
# quindi basta il confronto di sequenza e non serve un TM-score, che richiederebbe di scaricare ogni candidato.
REDUNDANT_IDENTITY = 95.0
MIN_SHARED = 10
# Identità locale: finestra di 9 residui del bersaglio centrata (troncata alle estremità), come la modalità
# Confidenza degli allineamenti; un miglioramento conta da +20 punti (2 residui identici in più su 9) in almeno
# MIN_NEW_RESIDUES residui.
LOCAL_WINDOW = 9
LOCAL_GAIN = 20.0
# Un candidato complementare in fascia scarsa non entra: i suoi vincoli peggiorerebbero il modello.
MIN_COMPLEMENT_SCORE = 40.0


# ---------------------------------------------------------------------------------------------- utilità
def _n(value: float, digits: int = 1) -> str:
    return f"{value:.{digits}f}".replace(".", ",").replace("-", "−")


def _sci(value: float) -> str:
    """E-value come nell'interfaccia (`fmtEvalue`): notazione esponenziale sotto 10^−3, altrimenti 2 cifre."""
    if value == 0:
        return "0"
    if value >= 1e-3:
        return f"{value:.2g}".replace(".", ",")
    mant, exp = f"{value:.1e}".split("e")
    return f"{mant.replace('.', ',')}·10^{int(exp)}"


def _number(value) -> Optional[float]:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def _ranges(positions: Iterable[int]) -> str:
    """"1–40, 60–76" da un insieme di posizioni."""
    ordered = sorted(set(positions))
    out: list[str] = []
    start = prev = None
    for p in ordered:
        if start is None:
            start = prev = p
        elif p == prev + 1:
            prev = p
        else:
            out.append(f"{start}–{prev}" if prev != start else f"{start}")
            start = prev = p
    if start is not None:
        out.append(f"{start}–{prev}" if prev != start else f"{start}")
    return ", ".join(out)


def rost_threshold(length: Optional[float]) -> float:
    """Curva della zona crepuscolare (Rost 1999, Protein Eng 12:85, eq. 2 con n = 0): identità (%) sopra la quale due
    sequenze allineate su `length` residui hanno con buona probabilità la stessa struttura.

    pI(L) = 100 per L ≤ 11; 480 · L^(−0,32 · (1 + e^(−L/1000))) per 11 < L ≤ 450; 19,5 per L > 450. Esempi: 76
    residui → 33,2%, 100 → 29,0%, 250 → 20,7%. Senza lunghezza nota si usa l'asintoto (19,5%).
    """
    if length is None or length > 450:
        return 19.5
    if length <= 11:
        return 100.0
    value = 480.0 * float(length) ** (-0.32 * (1.0 + math.exp(-float(length) / 1000.0)))
    return min(100.0, value)


def _aligned(hit: dict) -> tuple[str, str]:
    q, s = hit.get("query_aligned"), hit.get("subject_aligned")
    if isinstance(q, str) and isinstance(s, str) and q and len(q) == len(s):
        return q.upper(), s.upper()
    return "", ""


def alignment_stats(hit: dict) -> dict:
    """Coppie allineate (colonne con un residuo in entrambe le righe), identiche, lacune interne.

    `pairs` = None senza le righe dell'allineamento: in quel caso valgono `align_length` e l'identità dell'hit.
    Lacune: tratti di '-' in una delle due righe, esclusi quelli alle estremità (non ce ne sono negli allineamenti
    locali, ma i motori li potrebbero riportare); `longest` è il più lungo, `target_insertions` i residui del
    bersaglio senza template di fronte (da ricostruire come loop).
    """
    q, s = _aligned(hit)
    if not q:
        return {"pairs": None, "identical": None, "openings": None, "longest": None, "target_insertions": None}
    pairs = identical = 0
    for a, b in zip(q, s):
        if a.isalpha() and b.isalpha():
            pairs += 1
            identical += a == b
    runs: list[tuple[str, int]] = []
    for which, row in (("template", s), ("target", q)):
        for m in re.finditer(r"-+", row.strip("-")):
            runs.append((which, len(m.group(0))))
    return {"pairs": pairs, "identical": identical, "openings": len(runs),
            "longest": max((n for _w, n in runs), default=0),
            "target_insertions": sum(n for w, n in runs if w == "template")}


def method_kind(hit: dict) -> Optional[str]:
    """xray | em | nmr | other | predicted; None se il metodo non è noto (motore locale senza metadati)."""
    if hit.get("predicted"):
        return "predicted"
    text = str(hit.get("method") or "").upper()
    if not text.strip():
        return None
    if "X-RAY" in text or "NEUTRON" in text or "ELECTRON CRYSTALLOGRAPHY" in text:
        return "xray"
    if "ELECTRON MICROSCOPY" in text or "CRYO" in text:
        return "em"
    if "NMR" in text:
        return "nmr"
    if "PREDICT" in text or "ALPHAFOLD" in text or "THEORETICAL" in text:
        return "predicted"
    return "other"


def _release_year(value) -> Optional[float]:
    m = re.match(r"^(\d{4})(?:-(\d{2}))?", str(value or ""))
    if not m:
        return None
    return int(m.group(1)) + ((int(m.group(2)) - 1) / 12.0 if m.group(2) else 0.0)


def relevant_ligands(hit: dict) -> list[str]:
    """Ligandi e cofattori del template (codici chimici), senza acqua né additivi di cristallizzazione."""
    out = []
    for code in hit.get("ligands") or []:
        name = str(code or "").strip().upper()
        if name and name not in ADDITIVES and name not in out:
            out.append(name)
    return out


def _oligomer_text(state: str, count) -> str:
    state = (state or "").strip()
    n = _number(count)
    m = re.match(r"^(Homo|Hetero)\s+(\d+)-mer$", state, re.IGNORECASE)
    if state.lower() == "monomer" or (not state and n == 1):
        return "monomero"
    if m:
        kind = "omo" if m.group(1).lower() == "homo" else "etero"
        return f"{kind}-oligomero di {m.group(2)} catene"
    if state:
        return state.lower() + (f" ({int(n)} catene)" if n else "")
    return f"{int(n)} catene" if n else ""


# ---------------------------------------------------------------------------------------------- componenti
def _identity(hit: dict, stats: dict) -> tuple[Optional[float], str, bool]:
    raw = _number(hit.get("identity"))
    pairs = stats["pairs"]
    if pairs:
        identity = 100.0 * stats["identical"] / pairs
        length: Optional[float] = pairs
    elif raw is not None:
        identity = raw
        length = _number(hit.get("align_length"))
    else:
        return None, "identità non disponibile", False
    identity = max(0.0, min(100.0, identity))
    threshold = rost_threshold(length)
    high = max(HIGH_ACCURACY_IDENTITY, threshold + 10.0)
    points = [(0.0, 0.0), (threshold, 40.0)]
    if high < 100.0:
        points.append((high, 70.0))
    if threshold < 100.0:
        points.append((100.0, 100.0))
    score = piecewise(identity, tuple(points))
    where = f"su {int(length)} residui allineati" if length else "(lunghezza allineata non nota: soglia asintotica)"
    diff = identity - threshold
    head = (f"{_n(identity)}% {where}, {_n(abs(diff))} punti {'sopra' if diff >= 0 else 'sotto'} la curva di Rost "
            f"({_n(threshold)}%)")
    if diff < 0:
        text = (f"{head}: zona crepuscolare, l'identità non basta a dimostrare l'omologia e gli errori di allineamento "
                "sono probabili.")
    elif identity >= HIGH_ACCURACY_IDENTITY:
        text = f"{head}: omologia sicura; oltre il 50% modelli di alta accuratezza (Baker & Šali 2001)."
    else:
        text = (f"{head}: omologia probabile; sotto il 50% accuratezza media, con errori concentrati nei loop e dove "
                "l'allineamento è incerto (Baker & Šali 2001).")
    return score, text, diff < 0


def _coverage(hit: dict) -> tuple[Optional[float], str]:
    cov = _number(hit.get("coverage"))
    if cov is None:
        return None, "copertura non disponibile"
    cov = max(0.0, min(100.0, cov))
    start, end = hit.get("query_start"), hit.get("query_end")
    span = f" (residui {start}–{end})" if isinstance(start, int) and isinstance(end, int) else ""
    if cov >= 95:
        note = "quasi tutto il bersaglio ha vincoli dal template."
    elif cov >= 40:
        note = "i residui non coperti si costruiscono senza vincoli del template: valuta un secondo template."
    else:
        note = "la maggior parte del modello sarebbe costruita senza template (poco affidabile)."
    return piecewise(cov, COVERAGE_POINTS), f"{_n(cov)}% del bersaglio{span}: {note}"


def _evalue(hit: dict) -> tuple[Optional[float], str]:
    value = _number(hit.get("evalue"))
    if value is None or value < 0:
        return None, "E-value non disponibile"
    score = 100.0 if value == 0 else piecewise(-math.log10(value), EVALUE_POINTS)
    if value <= 1e-3:
        note = "omologia significativa (sotto 10^−3)."
    elif value <= 1e-2:
        note = "significatività debole (tra 10^−3 e 10^−2): verifica l'allineamento."
    else:
        note = "non significativo (sopra 10^−2): l'omologia è dubbia."
    return score, f"E-value {_sci(value)}: {note}"


def _resolution(hit: dict, kind: Optional[str]) -> tuple[Optional[float], str, str]:
    """(punteggio, motivazione, etichetta): per i modelli predetti la componente è il pLDDT medio."""
    if kind == "predicted":
        plddt = _number(hit.get("plddt"))
        if plddt is None:
            return None, "modello predetto senza pLDDT medio", "Confidenza (pLDDT medio)"
        if plddt >= 90:
            note = "molto alta (≥ 90): scheletro e catene laterali in genere accurati"
        elif plddt >= 70:
            note = "confidente (70–90): scheletro in genere corretto"
        elif plddt >= 50:
            note = "bassa (50–70): da usare con cautela"
        else:
            note = "molto bassa (< 50): spesso regioni disordinate"
        return (piecewise(plddt, PLDDT_POINTS),
                f"pLDDT medio {_n(plddt)}, {note} (Tunyasuvunakool et al. 2021); i residui sotto 70 si possono "
                "togliere prima della modellazione.", "Confidenza (pLDDT medio)")
    if kind == "nmr":
        return (NMR_RESOLUTION_SCORE, "struttura NMR, senza risoluzione: valore fisso 60 (fascia intermedia, scelta "
                "BioLoom), perché è un insieme di conformazioni con loop e catene laterali meno definiti; si usa il "
                "primo modello.", "Risoluzione")
    res = _number(hit.get("resolution"))
    if res is None or res <= 0:
        return None, "risoluzione non disponibile", "Risoluzione"
    if res <= 2.5:
        note = "fino a 2,5 Å catene laterali ben definite"
    elif res <= 3.5:
        note = "tra 2,5 e 3,5 Å scheletro tracciabile, catene laterali incerte"
    else:
        note = "oltre 3,5 Å è affidabile solo lo scheletro"
    text = f"{_n(res, 2)} Å: {note} (Wlodawer et al. 2008)"
    if kind == "em":
        text += "; per la crio-EM è la risoluzione globale della mappa: loop e periferia sono in genere peggiori"
    return piecewise(res, RESOLUTION_POINTS), text + ".", "Risoluzione"


def _rfree(hit: dict, kind: Optional[str]) -> tuple[Optional[float], str]:
    if kind != "xray":
        return None, "si applica solo alle strutture da diffrazione"
    value = _number(hit.get("rfree"))
    if value is None or not 0 < value < 1:
        return None, "R-free non depositato (strutture precedenti al 1992 o dato assente)"
    if value <= 0.25:
        note = "affinamento buono (fino a 0,25)"
    elif value <= 0.30:
        note = "affinamento accettabile (0,25–0,30)"
    elif value < 0.40:
        note = "alto (oltre 0,30): modello meno affidabile"
    else:
        note = "molto alto (0,40 o più): probabili errori gravi (Kleywegt & Brünger 1996)"
    return piecewise(value, RFREE_POINTS), f"R-free {_n(value, 3)}: {note}."


def _method(hit: dict, kind: Optional[str]) -> tuple[Optional[float], str]:
    if kind is None:
        return None, "metodo non disponibile"
    method = str(hit.get("method") or "").strip()
    detail = f" ({method})" if method and kind != "predicted" else ""
    notes = {
        "xray": "coordinate da una mappa di densità, con risoluzione e R-free per valutarle.",
        "em": "la risoluzione locale varia nella mappa.",
        "nmr": "insieme di conformazioni vincolate da NOE; regioni flessibili meno definite.",
        "other": "informazione strutturale limitata.",
        "predicted": "un'ipotesi, non un dato sperimentale: a parità del resto vince una struttura sperimentale.",
    }
    return METHOD_SCORE[kind], f"{METHOD_LABEL[kind]}{detail}: {notes[kind]}"


def _gaps(stats: dict) -> tuple[Optional[float], str]:
    if stats["openings"] is None:
        return None, "allineamento non disponibile"
    n, longest = stats["openings"], stats["longest"]
    if not n:
        return 100.0, "nessuna lacuna nell'allineamento."
    score = max(0.0, piecewise(longest, GAP_POINTS) - GAP_EXTRA_PENALTY * max(0, n - 2))
    count = "1 lacuna" if n == 1 else f"{n} lacune"
    if longest <= 8:
        note = "loop ricostruiti da MODELLER con accuratezza in genere accettabile fino a circa 8 residui"
    elif longest <= 15:
        note = "lacuna lunga: il tratto si ricostruisce con accuratezza scarsa"
    else:
        note = "lacuna molto lunga: il tratto è in pratica senza template"
    return score, f"{count}, la più lunga di {longest} residui: {note} (Fiser, Do & Šali 2000)."


def _date(hit: dict, kind: Optional[str]) -> tuple[Optional[float], str]:
    if kind == "predicted":
        return None, "non si applica ai modelli predetti"
    year = _release_year(hit.get("released"))
    if year is None:
        return None, "data di rilascio non disponibile"
    y = int(year)
    if year < 1992:
        note = "prima dell'R-free e dei rapporti di validazione: standard di affinamento meno rigorosi"
    elif year < 2008:
        note = "prima del deposito obbligatorio dei dati sperimentali (2008)"
    elif year < 2014:
        note = "con i dati sperimentali depositati, prima dei rapporti di validazione wwPDB (2014)"
    else:
        note = "con i rapporti di validazione wwPDB"
    return piecewise(year, DATE_POINTS), f"rilasciata nel {y}: {note}."


def _ligands(hit: dict) -> tuple[Optional[float], str]:
    if "ligands" not in hit or hit.get("ligands") is None:
        return None, "ligandi non noti (metadati di RCSB assenti)"
    found = relevant_ligands(hit)
    if found:
        shown = ", ".join(found[:8]) + (f" e altri {len(found) - 8}" if len(found) > 8 else "")
        return None, (f"struttura olo con {shown}: utile se il bersaglio lega gli stessi cofattori (opzione «Ligandi» "
                      "della modellazione per trasferirli); informativo, non cambia il punteggio.")
    return None, "nessun ligando oltre ad acqua e additivi di cristallizzazione (apo); informativo, nessuna penalità."


def _oligomer(hit: dict) -> tuple[Optional[float], str]:
    text = _oligomer_text(str(hit.get("oligomeric_state") or ""), hit.get("oligomeric_count"))
    if not text:
        return None, "stato oligomerico non noto"
    return None, f"assemblaggio biologico: {text}; informativo, non cambia il punteggio."


# ---------------------------------------------------------------------------------------------- punteggio
def score_hit(hit: dict) -> dict:
    """Punteggio 0–100, fascia, zona crepuscolare (sotto la curva di Rost) e motivazioni per componente.

    `reasons`: una voce per componente con `key`, `label`, `score` (0–100, None se non disponibile o informativa),
    `weight` e `text` (motivazione in italiano). Il punteggio è la media pesata delle componenti con punteggio.
    """
    stats = alignment_stats(hit)
    kind = method_kind(hit)
    identity, identity_text, twilight = _identity(hit, stats)
    resolution, resolution_text, resolution_label = _resolution(hit, kind)
    coverage, coverage_text = _coverage(hit)
    if twilight and identity is not None and coverage is not None and coverage > identity:
        # Regola della zona crepuscolare (scelta BioLoom): coprire il bersaglio con un allineamento incerto non dà
        # vincoli affidabili, quindi la copertura conta al più quanto l'identità. Così un template sotto la curva di
        # Rost non arriva mai alla fascia buona (al massimo 0,3 × 40 + 0,3 × 40 + 0,4 × 100 = 64).
        coverage_text += (f" In zona crepuscolare la copertura conta al più quanto l'identità ({_n(coverage)} → "
                          f"{_n(identity)}).")
        coverage = identity
    values = {
        "identity": (identity, identity_text),
        "coverage": (coverage, coverage_text),
        "evalue": _evalue(hit),
        "resolution": (resolution, resolution_text),
        "rfree": _rfree(hit, kind),
        "method": _method(hit, kind),
        "gaps": _gaps(stats),
        "date": _date(hit, kind),
        "ligands": _ligands(hit),
        "oligomer": _oligomer(hit),
    }
    reasons, total, weights = [], 0.0, 0.0
    for comp in COMPONENTS:
        score, text = values[comp.key]
        label = resolution_label if comp.key == "resolution" else comp.label
        if score is not None and comp.weight > 0:
            score = round(max(0.0, min(100.0, score)), 1)
            total += comp.weight * score
            weights += comp.weight
        else:
            score = None
        reasons.append({"key": comp.key, "label": label, "score": score, "weight": comp.weight, "text": text})
    final = round(total / weights, 1) if weights else 0.0
    return {"score": final, "band": band(final), "twilight": twilight, "reasons": reasons}


def rank_hits(hits: list[dict]) -> list[dict]:
    """Aggiunge a ogni hit `rank_score`, `rank_band`, `rank_twilight` e `rank_reasons` (sul posto)."""
    for hit in hits:
        info = score_hit(hit)
        hit["rank_score"] = info["score"]
        hit["rank_band"] = info["band"]
        hit["rank_twilight"] = info["twilight"]
        hit["rank_reasons"] = info["reasons"]
    return hits


def _score(hit: dict) -> float:
    value = _number(hit.get("rank_score"))
    return value if value is not None else score_hit(hit)["score"]


def _twilight(hit: dict) -> bool:
    value = hit.get("rank_twilight")
    return value if isinstance(value, bool) else score_hit(hit)["twilight"]


def sort_key(hit: dict) -> tuple:
    """Ordine di merito: punteggio (a 0,1); a parità una struttura sperimentale prima di un modello predetto, poi
    identità × copertura, la risoluzione migliore e il pLDDT medio più alto."""
    return (round(_score(hit), 1), 0 if hit.get("predicted") else 1,
            (_number(hit.get("identity")) or 0.0) * (_number(hit.get("coverage")) or 0.0),
            -(_number(hit.get("resolution")) or 99.0), _number(hit.get("plddt")) or 0.0)


def best(hits: list[dict]) -> Optional[dict]:
    return max(hits, key=sort_key) if hits else None


# ---------------------------------------------------------------------------------------------- complementari
def coverage_map(hit: dict) -> dict[int, Optional[str]]:
    """Posizione del bersaglio (1-based) → residuo del template allineato; senza le righe dell'allineamento
    l'intervallo query_start–query_end con residuo sconosciuto (None)."""
    q, s = _aligned(hit)
    start = hit.get("query_start")
    if not isinstance(start, int) or start < 1:
        return {}
    if not q:
        end = hit.get("query_end")
        return {p: None for p in range(start, end + 1)} if isinstance(end, int) and end >= start else {}
    out: dict[int, Optional[str]] = {}
    pos = start
    for a, b in zip(q, s):
        if a.isalpha():
            if b.isalpha():
                out[pos] = b
            pos += 1
    return out


def _query_letters(hits: Iterable[dict]) -> dict[int, str]:
    letters: dict[int, str] = {}
    for hit in hits:
        q, _s = _aligned(hit)
        start = hit.get("query_start")
        if not q or not isinstance(start, int):
            continue
        pos = start
        for a in q:
            if a.isalpha():
                letters.setdefault(pos, a)
                pos += 1
    return letters


def _local_identity(cov: dict[int, Optional[str]], letters: dict[int, str], length: int) -> dict[int, float]:
    """Identità locale (%) per posizione coperta, su una finestra centrata di LOCAL_WINDOW residui del bersaglio
    (troncata alle estremità); una posizione non coperta conta come non identica. Somme prefisse: O(lunghezza)."""
    half = LOCAL_WINDOW // 2
    prefix = [0] * (length + 1)
    for p in range(1, length + 1):
        same = cov.get(p) is not None and cov[p] == letters.get(p)
        prefix[p] = prefix[p - 1] + int(same)
    out = {}
    for p in cov:
        if not 1 <= p <= length:
            continue
        lo, hi = max(1, p - half), min(length, p + half)
        out[p] = 100.0 * (prefix[hi] - prefix[lo - 1]) / (hi - lo + 1)
    return out


def _mutual_identity(a: dict[int, Optional[str]], b: dict[int, Optional[str]]) -> tuple[int, Optional[float]]:
    """Residui del bersaglio coperti da entrambi e identità reciproca (%) dei template su quelle posizioni."""
    shared = [p for p in a if p in b and a[p] is not None and b[p] is not None]
    if not shared:
        return 0, None
    return len(shared), 100.0 * sum(1 for p in shared if a[p] == b[p]) / len(shared)


def _entry(hit: dict, role: str, reason: str, new: set[int]) -> dict:
    return {"template_id": hit.get("template_id"), "pdb_id": hit.get("pdb_id"), "chain": hit.get("chain"),
            "rank_score": round(_score(hit), 1), "identity": hit.get("identity"), "coverage": hit.get("coverage"),
            "predicted": bool(hit.get("predicted")), "role": role, "reason": reason, "new_residues": len(new),
            "ranges": _ranges(new)}


def select_complementary(hits: list[dict], query_length: int, *, max_templates: int = MAX_TEMPLATES,
                         min_new: int = MIN_NEW_RESIDUES) -> dict:
    """Fino a `max_templates` template complementari, con la motivazione di ogni scelta ed esclusione.

    1. Il primo è il migliore per punteggio (`sort_key`: a parità lo sperimentale vince sul predetto).
    2. I successivi, in ordine di merito, si scartano se sono in zona crepuscolare (sotto la curva di Rost) o in
       fascia scarsa; altrimenti:
       - entrano se coprono almeno `min_new` residui del bersaglio che nessun template scelto copre. Un candidato
         quasi identico a uno scelto nella regione comune (un costrutto più lungo della stessa proteina) entra lo
         stesso: porta vincoli dove non ce ne sono e nella regione comune i vincoli coincidono;
       - altrimenti sono ridondanti (identità reciproca > 95% su almeno 10 residui in comune con uno scelto) e si
         scartano;
       - altrimenti entrano se migliorano l'identità locale (+20 punti sulla finestra di 9 residui) in almeno
         `min_new` residui, e si scartano se non aggiungono nulla.
    Due template scelti senza residui in comune restano senza vincoli reciproci: MODELLER costruisce ogni regione dal
    suo template, ma l'orientamento relativo delle due regioni non è vincolato (nota nella motivazione).
    """
    length = max(1, int(query_length or 0))
    max_templates = max(1, min(int(max_templates), MAX_TEMPLATES_LIMIT))
    ordered = sorted((h for h in hits if isinstance(h, dict)), key=sort_key, reverse=True)
    letters = _query_letters(ordered)
    chosen: list[dict] = []
    chosen_maps: list[dict[int, Optional[str]]] = []
    excluded: list[dict] = []
    covered: set[int] = set()
    # miglior identità locale dei template scelti per posizione, aggiornata a ogni scelta
    best_local: dict[int, float] = {}
    not_examined = 0

    def choose(hit: dict, cov: dict[int, Optional[str]], role: str, reason: str, residues: set[int]) -> None:
        covered.update(cov)
        chosen.append(_entry(hit, role, reason, residues))
        chosen_maps.append(cov)
        for p, value in _local_identity(cov, letters, length).items():
            best_local[p] = max(best_local.get(p, 0.0), value)

    for hit in ordered:
        cov = coverage_map(hit)
        if not chosen:
            note = (" È in zona crepuscolare (identità sotto la curva di Rost): il modello va verificato con "
                    "attenzione." if _twilight(hit) else "")
            choose(hit, cov, "principale", f"miglior punteggio fra i template trovati ({_n(_score(hit))}/100)."
                   + note, set(cov))
            continue
        if len(chosen) >= max_templates:
            not_examined += 1
            continue
        tid = hit.get("template_id")
        if _twilight(hit):
            excluded.append({"template_id": tid, "reason": "identità sotto la curva di Rost (zona crepuscolare): i "
                             "suoi vincoli rischiano di peggiorare il modello."})
            continue
        if _score(hit) < MIN_COMPLEMENT_SCORE:
            excluded.append({"template_id": tid, "reason": f"punteggio {_n(_score(hit))} in fascia scarsa (sotto "
                             f"{_n(MIN_COMPLEMENT_SCORE, 0)})."})
            continue
        new = set(cov) - covered
        twin = None
        for other, other_map in zip(chosen, chosen_maps):
            shared, mutual = _mutual_identity(cov, other_map)
            if shared >= MIN_SHARED and mutual is not None and mutual > REDUNDANT_IDENTITY:
                twin = (other["template_id"], mutual, shared)
                break
        overlap = any(p in covered for p in cov)
        if len(new) >= min_new:
            reason = f"copre {len(new)} residui del bersaglio non coperti ({_ranges(new)})"
            if twin:
                reason += (f"; nella regione comune è quasi identico a {twin[0]} (identità reciproca {_n(twin[1])}% "
                           f"su {twin[2]} residui)")
            if not overlap:
                reason += ("; nessun residuo in comune con i template già scelti: l'orientamento relativo delle "
                           "regioni non è vincolato")
            choose(hit, cov, "complementare", reason + ".", new)
            continue
        if twin:
            excluded.append({"template_id": tid, "reason": f"ridondante: quasi identico a {twin[0]} (identità "
                             f"reciproca {_n(twin[1])}% su {twin[2]} residui in comune) e con {len(new)} residui "
                             f"nuovi (minimo {min_new})."})
            continue
        own = _local_identity(cov, letters, length)
        gains = {p: own[p] - best_local[p] for p in own if p in best_local}
        improved = {p for p, g in gains.items() if g >= LOCAL_GAIN}
        if len(improved) >= min_new and letters:
            top = max(gains[p] for p in improved)
            choose(hit, cov, "complementare",
                   f"migliora l'identità locale in {len(improved)} residui ({_ranges(improved)}), fino a "
                   f"+{_n(top, 0)} punti sulla finestra di {LOCAL_WINDOW} residui.", improved)
            continue
        excluded.append({"template_id": tid, "reason": f"non aggiunge copertura ({len(new)} residui nuovi, minimo "
                         f"{min_new}) né migliora l'identità locale in almeno {min_new} residui."})
    return {
        "templates": chosen, "excluded": excluded, "not_examined": not_examined,
        "coverage": round(min(100.0, 100.0 * len(covered) / length), 1), "covered_residues": len(covered),
        "query_length": length, "max_templates": max_templates, "min_new_residues": min_new,
        "version": VERSION,
    }


def modeling_templates(selection: dict) -> list[dict]:
    """Template scelti nel formato di `run_modeling` ({id, chains})."""
    return [{"id": t["pdb_id"], "chains": [t["chain"]] if t.get("chain") else []}
            for t in selection.get("templates") or []]
