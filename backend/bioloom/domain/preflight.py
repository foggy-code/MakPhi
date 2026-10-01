"""Controlli preventivi prima di avviare un calcolo (F8a): logica pura, nessun I/O.

Ogni controllo produce esiti `{"level", "code", "message", "suggestion"}` (più campi facoltativi: `group`, `template`):

- `error`: il calcolo verrebbe rifiutato o fallirebbe di sicuro (licenza MODELLER assente, template che dopo il
  ritaglio pLDDT resta sotto il minimo, disco senza lo spazio per i risultati): l'interfaccia disattiva l'avvio;
- `warning`: il calcolo parte, ma il risultato rischia di essere poco affidabile o il job di non arrivare in fondo
  (zona crepuscolare, regioni disordinate scoperte, spazio scarso, tempo oltre il timeout): l'interfaccia chiede
  conferma («Avvia comunque»);
- `info`: da sapere, senza conseguenze sul risultato (tempo stimato, dati inviati a un servizio remoto, strumenti
  facoltativi assenti mentre gli altri girano).

I fatti (stato delle licenze e degli strumenti, spazio libero, stato di QMEAN, copertura del bersaglio…) li raccoglie
`services/preflight.py`; qui ci sono solo le regole, con le fonti delle soglie. Le stime dei tempi vengono dalle
misure della sezione Prestazioni di docs/ARCHITETTURA.md (`tests/perf_timing.py`, `test_preflight_calibration`).
"""

from __future__ import annotations

import math
from typing import Iterable, Optional

from . import plddt, template_rank

LEVELS = ("error", "warning", "info")
_RANK = {level: n for n, level in enumerate(LEVELS)}

# ---------------------------------------------------------------------------------------------- identità e copertura
# Baker & Šali 2001 (Science 294:93): sotto il 30% di identità i modelli per omologia sono di bassa accuratezza e gli
# errori di allineamento diventano la fonte d'errore principale; dal 50% alta accuratezza. La zona crepuscolare vera e
# propria dipende dalla lunghezza allineata (curva di Rost 1999, `template_rank.rost_threshold`): sotto la curva
# l'identità non basta a dimostrare l'omologia.
LOW_ACCURACY_IDENTITY = 30.0
# Copertura del bersaglio sotto il 40%: la maggior parte del modello sarebbe costruita senza vincoli dal template. È
# la soglia di copertura predefinita della pipeline (filtro della zona crepuscolare) e il confine della fascia scarsa
# della copertura nel punteggio dei template (domain/template_rank.py).
LOW_COVERAGE = 40.0
# Punteggio del template (domain/template_rank.py) sotto 40: fascia scarsa.
POOR_TEMPLATE_SCORE = template_rank.MIN_COMPLEMENT_SCORE

# ---------------------------------------------------------------------------------------------- ritaglio pLDDT
# Template AlphaFold: con meno di MIN_TRIMMED_RESIDUES residui dopo il ritaglio la modellazione si rifiuta
# (`modeller.prepare_template`, codice `plddt_too_few`); se il ritaglio toglie più della metà dei residui del modello
# predetto, AlphaFold è confidente solo su una minoranza della catena (spesso una proteina in buona parte disordinata)
# e il resto del bersaglio resterà senza vincoli.
PLDDT_TRIM_MOST = 0.5

# ---------------------------------------------------------------------------------------------- tempi
# Tempi misurati (ARCHITETTURA.md, Prestazioni; container di test, Intel i3-1115G4 con 4 CPU logiche): ubiquitina di
# lievito (76 residui) su 1UBQ, raffinamento "fast", secondi. Il costo della costruzione dei modelli cresce con i
# residui: le restrizioni e le coppie non legate che MODELLER ottimizza sono entro distanze di taglio fisse, quindi in
# numero proporzionale ai residui. Misurato (`test_preflight_calibration`, 1 modello "fast"): 0,0386 s per residuo con
# 76 residui, 0,0369 con 152, 0,0370 con 304: la stima è lineare.
REFERENCE_RESIDUES = 76
T_PREPARE = 0.02  # preparazione del template
T_ALIGN = 0.34  # script di allineamento (align2d)
T_STARTUP = 0.26  # avvio di MODELLER e lettura dell'allineamento
T_RESTRAINTS = 0.46  # restrizioni: modello 1 (2,75 s) meno un modello successivo (2,29 s)
T_MODEL = 2.29  # ogni modello, raffinamento "fast"
T_QUALITY = 1.22  # analisi di qualità di un modello (DOPE, mkdssp, PROCHECK, ProSA in parallelo)
T_QUALITY_BASIC = 1.23  # analisi di un elemento di pipeline (senza PROCHECK e ProSA)
QUALITY_MODELS_PARALLEL = 3  # come services/compute.QUALITY_MODELS_PARALLEL
# Tempo di un modello rispetto a "fast", misurato nello stesso processo con i livelli alternati (mediana di 3 giri,
# `test_preflight_calibration`): very_fast 0,55 s, fast 2,37, normal 2,32, thorough 9,60 per modello. "normal" coincide
# con "fast": entrambi lasciano a MODELLER 10.8 `md_level = refine.very_fast`, il suo predefinito.
REFINEMENT_FACTOR = {"very_fast": 0.23, "fast": 1.0, "normal": 1.0, "thorough": 4.05}
REFINEMENT_LABEL = {"very_fast": "rapidissimo", "fast": "rapido", "normal": "standard", "thorough": "accurato"}
# Le misure vengono da un portatile a 4 CPU logiche: un altro processore, l'emulazione x86 su Apple Silicon o altri
# calcoli in corso cambiano i tempi. L'intervallo mostrato va da metà al doppio della stima.
ESTIMATE_SPREAD = 2.0
# Tempi di ricerca per motore (secondi, per sequenza): MMseqs2 misurato (indice locale 2,3 s, senza indice 12 s);
# NCBI QBlast da 1 a 5 minuti (le linee guida di NCBI chiedono di interrogare lo stato al più una volta al minuto);
# RCSB, BLAST+ e AlphaFold DB con l'accessione: pochi secondi (non misurati, ordine di grandezza).
SEARCH_SECONDS = {
    "rcsb": (2.0, 15.0),
    "ncbi": (60.0, 300.0),
    "local": (2.0, 30.0),
    "mmseqs": (2.3, 12.0),
    "mmseqs_noindex": (12.0, 22.0),
    "alphafold": (2.0, 15.0),
    "alphafold_blast": (60.0, 300.0),
}
# Stima oltre metà del timeout dei processi: con l'incertezza di un fattore 2 il processo potrebbe superarlo.
TIMEOUT_MARGIN = 0.5

# ---------------------------------------------------------------------------------------------- servizi remoti
# Linee guida di NCBI per l'interfaccia URL di BLAST (Common URL API, "Usage guidelines"): una richiesta ogni 10 s al
# massimo, stato di un RID al più una volta al minuto, e oltre 50 ricerche da lanciare di sera (21–5, ora della
# costa est degli USA) o nel fine settimana; NCBI può limitare chi non le rispetta.
NCBI_MANY_SEARCHES = 50

# ---------------------------------------------------------------------------------------------- spazio su disco
# Byte scritti da un job model (misura di `test_preflight_calibration`, 5 modelli "fast", analisi estesa del migliore):
#   76 residui: risultati 3103 KiB (modelli 329, rapporto di qualità 60, altro 2714), intermedi di MODELLER 855 KiB;
#   304 residui: risultati 4861 KiB (modelli 1308, rapporto 209, altro 3344), intermedi 3168 KiB.
# Quindi: modelli PDB 880 byte per residuo e per modello (circa 8 atomi pesanti da 81 byte); per il modello analizzato
# 3600 byte per residuo (rapporto JSON e uscite di PROCHECK e DOPE che crescono con i residui); una parte fissa di circa
# 2,5 MiB (soprattutto i grafici PostScript di PROCHECK, poi log, script, allineamento). Gli intermedi di MODELLER
# (restrizioni .rsr, traiettorie .D, violazioni .V, .ini, .sch) restano nello scratch fino alla fine del job: 2300 byte
# per residuo e per modello, attribuendo prudentemente anche le restrizioni (una per job) ai singoli modelli.
RESULT_BYTES_PER_RESIDUE_MODEL = 880
ANALYSIS_BYTES_PER_RESIDUE = 3600
SCRATCH_BYTES_PER_RESIDUE_MODEL = 2300
JOB_BASE_BYTES = int(2.5 * 1024 ** 2)
# Sotto lo spazio stimato il salvataggio finale non riesce e il job fallisce dopo aver calcolato tutto (errore).
# Avviso sotto 1 GiB (o sotto 10 volte la stima, se più grande): la cartella dati ospita anche il database SQLite
# con il suo WAL, le cache delle risposte remote e dei calcoli (fino a 20 MB per voce) e i template scaricati da
# altri job nello stesso momento (un mmCIF di un grande complesso pesa decine di MB); lo scratch le cartelle di
# lavoro di tutti i job attivi. Scelta BioLoom.
DISK_WARNING_FLOOR = 1024 ** 3
DISK_WARNING_FACTOR = 10

# ---------------------------------------------------------------------------------------------- pipeline
# Disordine delle sequenze della pipeline: calcolato solo fino a questo totale di residui (FoldIndex è lineare, circa
# 1 s ogni 500 000 residui in Python).
PIPELINE_DISORDER_MAX_RESIDUES = 500_000


# ---------------------------------------------------------------------------------------------- utilità
def check(level: str, code: str, message: str, suggestion: Optional[str] = None, **extra) -> dict:
    """Un esito: livello (`error`, `warning`, `info`), codice stabile, messaggio e suggerimento in italiano."""
    if level not in _RANK:
        raise ValueError(f"livello sconosciuto: {level}")
    out = {"level": level, "code": code, "message": message, "suggestion": suggestion}
    out.update({k: v for k, v in extra.items() if v is not None})
    return out


def ordered(checks: Iterable[dict]) -> list[dict]:
    """Errori, poi avvisi, poi informazioni; a parità l'ordine in cui sono stati prodotti."""
    items = list(checks)
    return [c for _n, c in sorted(enumerate(items), key=lambda p: (_RANK[p[1]["level"]], p[0]))]


def counts(checks: Iterable[dict]) -> dict:
    out = {level: 0 for level in LEVELS}
    for c in checks:
        out[c["level"]] += 1
    return out


def _n(value: float, digits: int = 1) -> str:
    return f"{value:.{digits}f}".replace(".", ",")


def fmt_seconds(seconds: float) -> str:
    """"45 s", "3 min", "1 h 20 min", "2 giorni"."""
    s = max(0.0, float(seconds))
    if s < 60:
        return f"{max(1, round(s))} s"
    if s < 3600:
        return f"{round(s / 60)} min"
    if s < 48 * 3600:
        h, m = divmod(round(s / 60), 60)
        return f"{h} h" + (f" {m} min" if m else "")
    return f"{round(s / 86400)} giorni"


def fmt_bytes(n: float) -> str:
    units = ("B", "KB", "MB", "GB", "TB")
    value, unit = float(n), 0
    while value >= 1024 and unit < len(units) - 1:
        value /= 1024
        unit += 1
    return f"{_n(value, 0 if unit < 2 else 1)} {units[unit]}"


# ---------------------------------------------------------------------------------------------- MODELLER
def modeller_checks(status: dict, *, required: bool = True, purpose: str = "la modellazione") -> list[dict]:
    """MODELLER non utilizzabile. `status` = `LicenseService.status()["modeller"]` (configured, available, message).

    `required` falso (analisi di qualità con il DOPE): l'analisi prosegue senza DOPE, quindi è un avviso."""
    if status.get("available"):
        return []
    message = str(status.get("message") or "")
    if "no module named" in message.lower() or "interprete non trovato" in message.lower():
        code, text = "modeller_not_installed", "MODELLER non è installato o l'interprete indicato non lo trova."
        hint = "Indica nelle Impostazioni un interprete Python con MODELLER (nel container è già installato)."
    elif not status.get("configured"):
        code, text = "modeller_license_missing", "Manca la chiave di licenza di MODELLER."
        hint = ("Inserisci la chiave in Impostazioni → Licenze (gratuita per uso accademico: "
                "https://salilab.org/modeller/registration.html).")
    else:
        code = "modeller_license_invalid"
        text = "MODELLER non accetta la chiave di licenza configurata" + (f": {message}" if message else ".")
        hint = "Controlla la chiave in Impostazioni → Licenze."
    if required:
        return [check("error", code, f"{text} Senza MODELLER {purpose} non può partire.", hint)]
    return [check("warning", "dope_unavailable", f"{text} L'analisi proseguirà senza il DOPE: voto e consenso senza "
                                                 "zDOPE.", hint)]


# ---------------------------------------------------------------------------------------------- validazione
def invalid(code: str, message: str, suggestion: Optional[str] = None) -> dict:
    """Richiesta che la rotta di avvio rifiuterebbe (stessa validazione, stesso messaggio)."""
    return check("error", code or "invalid_request", message, suggestion)


# ---------------------------------------------------------------------------------------------- template
def template_availability(template_id: str, *, in_library: bool, downloadable: Optional[str]) -> list[dict]:
    """Template assente dalla libreria: si scarica all'avvio (codice PDB, id AlphaFold) o è un errore."""
    if in_library:
        return []
    if downloadable:
        return [check("info", "template_download",
                      f"Il template {template_id} non è nella libreria: il job lo scaricherà da {downloadable} "
                      "(serve la connessione).", template=template_id)]
    return [check("error", "template_missing", f"Il template {template_id} non è nella libreria.",
                  "Scaricalo o caricalo dalla Libreria template.", template=template_id)]


def similarity_checks(templates: list[dict], coverage: Optional[float]) -> list[dict]:
    """Identità e copertura dei template sul bersaglio.

    `templates`: {label, identity (%), aligned (coppie allineate), source} per template; `coverage`: copertura
    complessiva del bersaglio (%) dei template usati insieme. Identità sotto la curva di Rost per la lunghezza
    allineata → avviso (zona crepuscolare); sopra ma sotto il 30% → informazione (bassa accuratezza attesa, Baker &
    Šali 2001); copertura sotto il 40% → avviso.
    """
    out: list[dict] = []
    for t in templates:
        identity, aligned = t.get("identity"), t.get("aligned")
        if identity is None:
            continue
        threshold = template_rank.rost_threshold(aligned if aligned else None)
        where = f"su {int(aligned)} residui allineati" if aligned else "(lunghezza allineata non nota)"
        if identity < threshold:
            out.append(check(
                "warning", "twilight_zone",
                f"{t['label']}: identità {_n(identity)}% {where}, sotto la curva della zona crepuscolare di Rost "
                f"({_n(threshold)}% per questa lunghezza): l'omologia non è dimostrata e gli errori di allineamento "
                "sono probabili.",
                "Controlla l'allineamento (modalità Confidenza, Editor PIR), cerca un template migliore con NCBI BLAST "
                "o valuta un modello di AlphaFold DB.", template=t["label"]))
        elif identity < LOW_ACCURACY_IDENTITY:
            out.append(check(
                "info", "low_identity",
                f"{t['label']}: identità {_n(identity)}% {where}: sotto il 30% i modelli per omologia sono di bassa "
                "accuratezza e gli errori di allineamento pesano più di tutto il resto (Baker & Šali 2001).",
                "Verifica l'allineamento nelle regioni critiche (loop, sito attivo) prima di fidarti del modello.",
                template=t["label"]))
    if coverage is not None and coverage < LOW_COVERAGE:
        out.append(check(
            "warning", "low_coverage",
            f"I template coprono il {_n(coverage)}% del bersaglio: la maggior parte del modello sarebbe costruita senza "
            "vincoli dal template (poco affidabile).",
            "Aggiungi template che coprano il resto del bersaglio o modella solo la parte coperta."))
    return out


def rank_checks(hits: list[dict]) -> list[dict]:
    """Punteggio dei template scelti nella ricerca da cui vengono (domain/template_rank.py): fascia scarsa = avviso."""
    out = []
    for h in hits:
        score = h.get("rank_score")
        if not isinstance(score, (int, float)) or score >= POOR_TEMPLATE_SCORE:
            continue
        weak = sorted((r for r in h.get("rank_reasons") or [] if r.get("score") is not None and r.get("weight")),
                      key=lambda r: r["score"])[:2]
        why = "; ".join(f"{r['label']} {_n(r['score'], 0)}/100" for r in weak)
        out.append(check("warning", "template_score_poor",
                         f"{h.get('template_id')}: punteggio {_n(score)}/100 nella ricerca (fascia scarsa"
                         + (f": {why}" if why else "") + ").",
                         "Confronta con gli altri candidati della ricerca: i consigliati sono in cima.",
                         template=h.get("template_id")))
    return out


def plddt_checks(trims: list[dict]) -> list[dict]:
    """Ritaglio dei template AlphaFold: {template, chains, threshold (None = ritaglio spento), total, kept, below,
    empty (catene senza residui sopra soglia), low (residui sotto 70, per il ritaglio spento)}."""
    out = []
    for t in trims:
        tid = t["template"]
        if t.get("threshold") is None:
            low = int(t.get("low") or 0)
            if low:
                out.append(check(
                    "warning", "plddt_untrimmed",
                    f"{tid}: modello AlphaFold usato senza ritaglio, con {low} di {t['total']} residui a pLDDT < "
                    f"{plddt.DEFAULT_THRESHOLD:g}: MODELLER riceverà vincoli anche da conformazioni predette con bassa "
                    "confidenza (spesso regioni disordinate).",
                    f"Riattiva il ritaglio dei residui con pLDDT < {plddt.DEFAULT_THRESHOLD:g}.", template=tid))
            continue
        threshold, total, kept = float(t["threshold"]), int(t["total"]), int(t["kept"])
        removed = total - kept
        if t.get("empty"):
            out.append(check("error", "plddt_too_few",
                             f"{tid}: nessun residuo della catena {', '.join(t['empty'])} ha pLDDT ≥ {threshold:g}.",
                             "Togli la catena o abbassa la soglia pLDDT.", template=tid))
        elif removed and kept < plddt.MIN_TRIMMED_RESIDUES:
            out.append(check("error", "plddt_too_few",
                             f"{tid}: togliendo i residui con pLDDT < {threshold:g} ne restano {kept} su {total} (minimo "
                             f"{plddt.MIN_TRIMMED_RESIDUES}): il modello AlphaFold è poco affidabile in questa regione.",
                             "Abbassa la soglia pLDDT o disattiva il ritaglio, oppure cerca un altro template.",
                             template=tid))
        elif removed > PLDDT_TRIM_MOST * total:
            out.append(check("warning", "plddt_trim_most",
                             f"{tid}: il ritaglio a pLDDT {threshold:g} toglie {removed} residui su {total} "
                             f"({_n(100.0 * removed / total, 0)}%): AlphaFold è confidente solo su una minoranza della "
                             "catena e il resto del bersaglio resterà senza vincoli dal template.",
                             "Controlla le regioni disordinate del bersaglio o cerca un template sperimentale.",
                             template=tid))
        elif removed:
            where = f" ({t['ranges']})" if t.get("ranges") else ""
            out.append(check("info", "plddt_trim",
                             f"{tid}: il ritaglio toglie {removed} residui con pLDDT < {threshold:g} o isolati{where}, "
                             f"ne restano {kept} di {total}.", template=tid))
    return out


# ---------------------------------------------------------------------------------------------- copertura (F5.5)
COVERAGE_LEVEL = {"warn": "warning", "info": "info"}


def coverage_checks(assessment: Optional[dict]) -> list[dict]:
    """Avvisi di `disorder.coverage_assessment` (regioni disordinate e domini fuori dalla copertura) come esiti del
    gruppo `coverage`: l'interfaccia li mostra con CoverageAlerts. Il suggerimento accompagna il primo avviso."""
    if not assessment:
        return []
    out, suggestion = [], assessment.get("suggestion")
    for w in assessment.get("warnings") or []:
        level = COVERAGE_LEVEL.get(w.get("tone"), "info")
        hint = suggestion if level == "warning" and suggestion else None
        if hint:
            suggestion = None
        out.append(check(level, w.get("kind") or "coverage", w.get("text") or "", hint, group="coverage"))
    return out


# ---------------------------------------------------------------------------------------------- strumenti facoltativi
def optional_tool_checks(requested: dict[str, bool], usable: dict[str, Optional[str]]) -> list[dict]:
    """PROCHECK e ProSA chiesti per l'analisi estesa. `usable[tool]` = None se utilizzabile, altrimenti il motivo.

    Se nessuno degli strumenti chiesti può girare l'opzione non ha effetto: avviso; se uno gira e l'altro no,
    informazione (l'analisi prosegue e il rapporto lo dice)."""
    wanted = [k for k, v in requested.items() if v]
    missing = [k for k in wanted if usable.get(k)]
    if not missing:
        return []
    level = "warning" if len(missing) == len(wanted) else "info"
    labels = {"procheck": "PROCHECK", "prosa": "ProSA II"}
    out = []
    for tool in missing:
        out.append(check(level, f"{tool}_unavailable", f"{labels.get(tool, tool)} non verrà eseguito: {usable[tool]}",
                         "Impostazioni → Licenze (ProSA) o → Strumenti; oppure togli l'opzione."
                         if tool == "prosa" else "Verifica l'installazione in Impostazioni → Strumenti, oppure togli "
                                                 "l'opzione."))
    return out


# ---------------------------------------------------------------------------------------------- tempi
def modeling_seconds(residues: int, models: int, refinement: str = "fast", quality: str = "best",
                     extended: bool = True, workers: int = 1) -> float:
    """Tempo stimato di un job model (secondi): template, allineamento, costruzione dei modelli, analisi di qualità."""
    scale = max(1, int(residues)) / REFERENCE_RESIDUES
    factor = REFINEMENT_FACTOR.get(refinement, 1.0)
    build = T_STARTUP + scale * (T_RESTRAINTS + max(1, int(models)) * T_MODEL * factor)
    per_quality = scale * (T_QUALITY if extended else T_QUALITY_BASIC)
    if quality == "all":
        lanes = max(1, min(QUALITY_MODELS_PARALLEL, int(workers), int(models)))
        analysis = math.ceil(max(1, int(models)) / lanes) * per_quality
    elif quality == "none":
        analysis = 0.0
    else:
        analysis = per_quality
    return T_PREPARE + T_ALIGN * max(1.0, scale) + build + analysis


def building_seconds(residues: int, models: int, refinement: str = "fast") -> float:
    """Tempo del solo processo di costruzione dei modelli (model.py): è quello che il timeout dei processi limita."""
    scale = max(1, int(residues)) / REFERENCE_RESIDUES
    return T_STARTUP + scale * (T_RESTRAINTS + max(1, int(models)) * T_MODEL * REFINEMENT_FACTOR.get(refinement, 1.0))


MODELING_BASIS = ("Stima dai tempi misurati su un portatile a 4 CPU logiche (Intel i3-1115G4, ubiquitina di 76 "
                  "residui), lineare nei residui: su un altro computer, in emulazione x86 o con altri calcoli in "
                  "corso può variare di un fattore 2.")
SEARCH_BASIS = ("Tempi tipici del motore: MMseqs2 misurato (2,3 s con l'indice locale, 12 s senza), NCBI BLAST da 1 a 5 "
                "minuti, RCSB, BLAST+ e AlphaFold DB con l'accessione pochi secondi.")


def estimate(seconds: float, low: Optional[float] = None, high: Optional[float] = None, *, detail: str = "",
             basis: str = MODELING_BASIS) -> dict:
    """Stima del tempo con l'intervallo (predefinito: da metà al doppio) e la sua base."""
    lo = seconds / ESTIMATE_SPREAD if low is None else low
    hi = seconds * ESTIMATE_SPREAD if high is None else high
    return {"seconds": round(seconds, 1), "low": round(lo, 1), "high": round(hi, 1), "detail": detail,
            "basis": basis}


def runtime_checks(est: dict, *, process_seconds: Optional[float] = None, timeout_seconds: Optional[float] = None,
                   what: str = "Il calcolo") -> list[dict]:
    """Tempo stimato (informazione) e rischio di superare il timeout dei processi (avviso)."""
    text = (f"{what} richiede circa {fmt_seconds(est['seconds'])} (da {fmt_seconds(est['low'])} a "
            f"{fmt_seconds(est['high'])})" + (f": {est['detail']}" if est.get("detail") else "") + ".")
    out = [check("info", "estimated_time", text)]
    if process_seconds is not None and timeout_seconds and process_seconds > TIMEOUT_MARGIN * timeout_seconds:
        out.append(check(
            "warning", "timeout_risk",
            f"La costruzione dei modelli richiede circa {fmt_seconds(process_seconds)} in un solo processo e il timeout "
            f"dei processi è di {fmt_seconds(timeout_seconds)}: con l'incertezza della stima potrebbe superarlo e il "
            "job fallirebbe.",
            "Riduci il numero di modelli o il raffinamento, oppure aumenta il timeout in Impostazioni → Generale."))
    return out


def pipeline_estimate(items: int, *, residues: int, models: int, refinement: str, quality: bool, extended: bool,
                      search: Optional[tuple[float, float]], parallel: int) -> dict:
    """Pipeline: (ricerca + modellazione) per sequenza × sequenze / elementi in parallelo."""
    model = modeling_seconds(residues, models, refinement, "best" if quality else "none", extended)
    lanes = max(1, min(int(parallel), max(1, int(items))))
    s_lo, s_hi = search or (0.0, 0.0)
    rounds = math.ceil(max(1, items) / lanes)
    seconds = rounds * (model + (s_lo + s_hi) / 2)
    low = rounds * (model / ESTIMATE_SPREAD + s_lo)
    high = rounds * (model * ESTIMATE_SPREAD + s_hi)
    detail = (f"{items} {'sequenza' if items == 1 else 'sequenze'} di {residues} residui in media, {lanes} in "
              f"parallelo, {models} {'modello' if models == 1 else 'modelli'} ciascuna")
    return estimate(seconds, low, high, detail=detail,
                    basis=MODELING_BASIS + (f" {SEARCH_BASIS}" if search else ""))


def mixed_search_time(ranges: list[tuple[float, float]]) -> Optional[tuple[float, float]]:
    """Intervallo medio per sequenza di ricerche con tempi diversi (AlphaFold DB con e senza accessione)."""
    if not ranges:
        return None
    return (sum(r[0] for r in ranges) / len(ranges), sum(r[1] for r in ranges) / len(ranges))


def pipeline_alphafold_checks(items: int, without_accession: int) -> list[dict]:
    """Pipeline con AlphaFold DB: le sequenze con l'accessione nell'intestazione (sp|ACC|NOME) mandano a EBI solo
    l'accessione; le altre passano da NCBI BLAST su Swiss-Prot."""
    with_accession = items - without_accession
    parts = []
    if with_accession:
        parts.append(f"{with_accession} con l'accessione UniProt nell'intestazione (a EBI va solo l'accessione)")
    if without_accession:
        parts.append(f"{without_accession} senza: per queste la sequenza va a NCBI BLAST su Swiss-Prot (da 1 a 5 "
                     "minuti ciascuna)")
    return [check("info", "pipeline_alphafold", "AlphaFold DB (modelli predetti, non strutture sperimentali): "
                  + "; ".join(parts) + ".",
                  "«Usa le sequenze del progetto» aggiunge l'intestazione sp|ACC|NOME alle sequenze importate da "
                  "UniProt." if without_accession else None)]


# ---------------------------------------------------------------------------------------------- spazio su disco
def modeling_bytes(residues: int, models: int, analysed: int) -> tuple[int, int]:
    """(risultati nella cartella dati, file nello scratch durante il job) di un job model, in byte."""
    n, m = max(1, int(residues)), max(1, int(models))
    results = JOB_BASE_BYTES + n * (m * RESULT_BYTES_PER_RESIDUE_MODEL + max(0, int(analysed)) *
                                    ANALYSIS_BYTES_PER_RESIDUE)
    scratch = results + n * m * SCRATCH_BYTES_PER_RESIDUE_MODEL
    return results, scratch


def disk_checks(*, data_free: Optional[int], data_need: int, scratch_free: Optional[int], scratch_need: int,
                same_disk: bool = False) -> list[dict]:
    """Spazio libero nella cartella dati e nello scratch rispetto a quello stimato per il job."""
    out = []
    places = [("data", "nella cartella dati", data_free, data_need)]
    if same_disk:
        places = [("data", "nella cartella dati (sullo stesso disco dello scratch)", data_free,
                   data_need + scratch_need)]
    else:
        places.append(("scratch", "nello scratch (volume di lavoro dei job)", scratch_free, scratch_need))
    for key, where, free, need in places:
        if free is None:
            continue
        if free < need:
            out.append(check("error", f"disk_full_{key}",
                             f"Spazio libero {where}: {fmt_bytes(free)}; il job ne richiede circa {fmt_bytes(need)}. Il "
                             "salvataggio dei risultati non riuscirebbe e il job fallirebbe a calcolo finito.",
                             "Libera spazio (Impostazioni → Spazio e file temporanei: pulizia, job vecchi) o usa un "
                             "disco più grande."))
        elif free < max(DISK_WARNING_FLOOR, DISK_WARNING_FACTOR * need):
            out.append(check("warning", f"disk_low_{key}",
                             f"Spazio libero {where}: {fmt_bytes(free)} (il job ne richiede circa {fmt_bytes(need)}). "
                             "Il database, le cache e gli altri job in corso scrivono sullo stesso disco.",
                             "Libera spazio in Impostazioni → Spazio e file temporanei."))
    return out


# ---------------------------------------------------------------------------------------------- QMEAN
def qmean_checks(*, mode: str, status: Optional[dict], memory_problem: Optional[str], fallback: bool,
                 remote_consent: bool, email: bool) -> list[dict]:
    """QMEAN locale (memoria, database, servizio) o SWISS-MODEL (consenso ed email), con il fallback facoltativo."""
    remote_ok = remote_consent and email

    def remote_problem() -> Optional[dict]:
        if not remote_consent:
            return check("error", "qmean_remote_disabled",
                         "L'invio a SWISS-MODEL non è consentito.",
                         "Abilitalo in Impostazioni → Generale (le coordinate del modello lasciano il computer).")
        if not email:
            return check("error", "email_required", "Per QMEAN su SWISS-MODEL serve la tua email.",
                         "Impostala in Impostazioni → Generale.")
        return None

    if mode == "remote":
        problem = remote_problem()
        if problem:
            return [problem]
        return [check("info", "qmean_remote", "Il modello sarà inviato a SWISS-MODEL (servizio pubblico, di solito "
                                              "qualche minuto): le coordinate lasciano il computer.")]
    status = status or {}
    reason = memory_problem or (None if status.get("available") else
                                (status.get("message") or "servizio QMEAN locale non disponibile."))
    if reason:
        code = "qmean_memory" if memory_problem else "qmean_unavailable"
        text = f"QMEAN locale non utilizzabile: {reason}"
        if fallback and remote_ok:
            return [check("warning", code, text + " Il job passerà al fallback su SWISS-MODEL: le coordinate "
                                                  "lasceranno il computer.")]
        if fallback:
            problem = remote_problem()
            return [check("error", code, text + " Il fallback su SWISS-MODEL è attivo ma " +
                          ("manca il consenso all'invio." if not remote_consent else "manca l'email."),
                          problem["suggestion"] if problem else None)]
        hint = ("Aumenta la memoria di Docker o scegli una versione più piccola del database; oppure attiva il "
                "fallback su SWISS-MODEL." if memory_problem else
                "Collega il disco con UniClust30/UniRef30 e riavvia BioLoom, oppure attiva il fallback su SWISS-MODEL.")
        return [check("error", code, text, hint)]
    out = []
    db = (status.get("uniclust") or {}).get("prefix")
    out.append(check("info", "qmean_local",
                     f"QMEAN locale{f' con {db}' if db else ''}: da qualche minuto a oltre mezz'ora per modello "
                     "(HHblits sul database)."))
    if status.get("busy") or status.get("queued"):
        out.append(check("info", "qmean_busy", "Il servizio QMEAN locale sta eseguendo un altro calcolo: questo "
                                               "resterà in coda (un calcolo alla volta)."))
    return out


# ---------------------------------------------------------------------------------------------- ricerca
def search_checks(engine: str, facts: dict) -> list[dict]:
    """Motore di ricerca dei template: strumenti e database locali, servizi remoti e dati che lasciano il computer.

    `facts`: blastp, pdbaa (database BLAST+), mmseqs, mmseqs_db, mmseqs_index (bool), email (bool), accession (str,
    solo AlphaFold DB)."""
    if engine == "local":
        if not facts.get("blastp"):
            return [check("error", "blast_missing", "BLAST+ (blastp) non è installato: la ricerca locale non può "
                                                     "partire.", "Usa RCSB o MMseqs2.")]
        if not facts.get("pdbaa"):
            return [check("error", "pdbaa_missing", "Il database locale pdbaa non c'è.",
                          "Scaricalo in Impostazioni → Database, oppure usa RCSB.")]
        return []
    if engine == "mmseqs":
        if not facts.get("mmseqs"):
            return [check("error", "mmseqs_missing", "MMseqs2 non è installato.", "Usa RCSB o BLAST+ locale.")]
        if not facts.get("mmseqs_db"):
            return [check("error", "mmseqs_database_missing", "Il database MMseqs2 non è preparato.",
                          "Impostazioni → Database → Prepara MMseqs2 (serve pdbaa), oppure usa RCSB.")]
        if not facts.get("mmseqs_index"):
            return [check("info", "mmseqs_no_index",
                          "L'indice veloce di MMseqs2 non è su questo computer: ogni ricerca richiede circa 12 s "
                          "invece di 2,3 s (misure in ARCHITETTURA.md).",
                          "Impostazioni → Database → Indicizza su questo computer.")]
        return []
    if engine == "ncbi":
        out = [check("info", "remote_ncbi", "NCBI BLAST: la sequenza va ai server di NCBI; la ricerca richiede da 1 a 5 "
                                            "minuti (lo stato si controlla una volta al minuto, come chiede NCBI).")]
        if not facts.get("email"):
            out.append(check("info", "ncbi_email", "NCBI chiede un indirizzo di contatto per le ricerche automatiche.",
                             "Imposta la tua email in Impostazioni → Generale."))
        return out
    if engine == "alphafold":
        if facts.get("accession"):
            return [check("info", "remote_alphafold", f"AlphaFold DB: a EBI va solo l'accessione {facts['accession']}; "
                                                      "i modelli sono predetti, non strutture sperimentali.")]
        return [check("info", "alphafold_blast",
                      "AlphaFold DB senza accessione UniProt: la sequenza va a NCBI BLAST su Swiss-Prot per trovare le "
                      "proteine più simili (da 1 a 5 minuti).",
                      "Indica l'accessione UniProt, se la conosci: si usano direttamente i suoi modelli.")]
    return []


def search_time(engine: str, facts: dict) -> tuple[float, float]:
    """Intervallo del tempo di una ricerca (secondi) per il motore scelto."""
    if engine == "mmseqs" and not facts.get("mmseqs_index"):
        return SEARCH_SECONDS["mmseqs_noindex"]
    if engine == "alphafold" and not facts.get("accession"):
        return SEARCH_SECONDS["alphafold_blast"]
    return SEARCH_SECONDS.get(engine, SEARCH_SECONDS["rcsb"])


def pipeline_remote_checks(*, engine: str, items: int, ncbi_searches: int, parallel: int) -> list[dict]:
    """Pipeline con molte ricerche su NCBI (motore NCBI, o AlphaFold DB per le sequenze senza accessione)."""
    if engine not in ("ncbi", "alphafold") or ncbi_searches <= 0:
        return []
    out = []
    if ncbi_searches > NCBI_MANY_SEARCHES:
        out.append(check(
            "warning", "ncbi_many_searches",
            f"La pipeline invierà {ncbi_searches} ricerche a NCBI BLAST ({items} sequenze): le linee guida di NCBI "
            f"chiedono di lanciare oltre {NCBI_MANY_SEARCHES} ricerche di sera (21–5, ora della costa est degli USA) o "
            "nel fine settimana, e NCBI può limitare chi non le rispetta. Ogni ricerca richiede da 1 a 5 minuti.",
            "Usa un motore locale (MMseqs2, BLAST+) o RCSB; con AlphaFold DB indica le accessioni nelle intestazioni "
            "(sp|ACC|NOME, «Usa le sequenze del progetto»)."))
    if parallel > 1:
        out.append(check("info", "ncbi_parallel",
                         f"{min(parallel, items)} ricerche NCBI partono insieme: NCBI chiede al più una richiesta ogni "
                         "10 secondi, quindi con molte sequenze conviene 1 elemento in parallelo."))
    return out


def pipeline_disorder_checks(disordered: list[str], analysed: int, total: int) -> list[dict]:
    """Sequenze della pipeline con regioni disordinate lunghe (FoldIndex): nei modelli saranno poco affidabili se il
    template non le copre (i template si scelgono solo durante la pipeline)."""
    out = []
    if disordered:
        names = ", ".join(disordered[:5]) + (f" e altre {len(disordered) - 5}" if len(disordered) > 5 else "")
        out.append(check("info", "pipeline_disorder",
                         f"{len(disordered)} {'sequenza ha' if len(disordered) == 1 else 'sequenze hanno'} regioni "
                         f"disordinate lunghe predette (FoldIndex): {names}. Se il template non le copre, nel modello "
                         "saranno tratti estesi senza significato strutturale."))
    if analysed < total:
        out.append(check("info", "pipeline_disorder_partial",
                         f"Disordine valutato sulle prime {analysed} sequenze di {total} (limite di tempo dei "
                         "controlli)."))
    return out


# ---------------------------------------------------------------------------------------------- coda
def queue_checks(active: int, workers: int) -> list[dict]:
    if active < max(1, workers):
        return []
    return [check("info", "queue_busy",
                  f"{active} job attivi con {workers} {'job' if workers == 1 else 'job'} di calcolo in parallelo: questo "
                  "aspetterà in coda (ricerche, allineamenti e analisi brevi hanno la precedenza).")]


def single_model_checks(models: int) -> list[dict]:
    if models != 1:
        return []
    return [check("info", "single_model", "Con un solo modello non c'è scelta: il migliore si sceglie per DOPE fra più "
                                          "modelli costruiti dallo stesso allineamento.",
                  "5 modelli, come l'esempio di base del tutorial di MODELLER.")]
