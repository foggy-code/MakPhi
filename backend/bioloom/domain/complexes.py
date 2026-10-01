"""Complessi e omo-oligomeri: bersaglio a più catene, allineamento per catena, simmetria e confronto delle
interfacce con il template (F5.3).

Logica pura (Biopython, numpy), usata da `services/compute.run_modeling`.

**Bersaglio.** Un complesso è un elenco di catene: per ogni catena del template scelta, la sequenza del bersaglio
(anche uguale a un'altra: "stessa della catena A" negli omo-oligomeri). Nel PIR le catene sono separate da '/',
nell'ordine della corrispondenza, sia nel bersaglio sia nel template (la copia preparata del template ha le catene
in quell'ordine). Limiti:

- `MAX_CHAINS` = 12 catene: copre gli omo-oligomeri comuni fino ai dodecameri; oltre (capsidi, ferritine a 24
  catene) scegliere una sequenza per catena non è pratico, e il costo dell'analisi delle interfacce cresce con le
  coppie di catene a contatto (fino a 66 con 12 catene).
- `MAX_RESIDUES` = 3000 residui in tutto: il tempo di MODELLER cresce con i residui totali del modello, un po' più che
  linearmente (misure in ARCHITETTURA.md, Prestazioni: 6,4 s per modello con 152 residui e 20 s con 608, raffinamento
  "rapido"); con 3000 residui 5 modelli "accurati" (3–5 volte più lenti) restano nel timeout predefinito dei job
  (120 minuti), con 5000 no.

**Allineamento per catena** (`merge_chain_alignments`): MODELLER allinea ogni catena del bersaglio sulla sua catena
del template (`align2d`), e le righe si uniscono con '/': i separatori stanno nelle stesse colonne in tutte le voci.

**Simmetria** (`symmetry_groups`): con l'opzione, le catene del bersaglio con la stessa sequenza formano un gruppo e
ogni catena del gruppo è vincolata alla prima con una restrizione di simmetria di MODELLER sui C-alfa dei residui
standard (`restraints.symmetry`, peso predefinito 1,0, come nell'esempio del manuale di MODELLER "Building a model
with symmetry restraints"). La restrizione penalizza la differenza fra le distanze interne corrispondenti delle due
catene (Šali & Blundell 1993; manuale di MODELLER, classe Symmetry): rende uguali le catene, non impone la
disposizione simmetrica, che viene dal template.

**Confronto delle interfacce** (`compare`): l'interfaccia di ogni coppia di catene del modello (domain/interfaces:
residui con ΔSASA > 1 Å², area = metà dell'area sepolta, come PISA) si confronta con quella della coppia
corrispondente del template attraverso le colonne del PIR:

- residui d'interfaccia del template conservati: il residuo allineato del modello è d'interfaccia nella stessa coppia;
- area del modello e del template;
- DockQ (Basu & Wallner 2016, PLoS ONE 11:e0161879) con il template come riferimento: Fnat = frazione dei contatti
  fra residui del template (atomi pesanti entro 5 Å, criterio di CAPRI, Méndez et al. 2003) presenti nel modello;
  iRMS = RMSD degli atomi dello scheletro (N, CA, C, O) dei residui d'interfaccia (entro 10 Å dall'altra catena nel
  template) dopo la loro sovrapposizione; LRMS = RMSD dello scheletro della catena più corta dopo la sovrapposizione
  della più lunga; DockQ = (Fnat + 1/(1 + (iRMS/1,5)²) + 1/(1 + (LRMS/8,5)²)) / 3. Classi di DockQ, corrispondenti a
  quelle di CAPRI: ≥ 0,80 alta, ≥ 0,49 media, ≥ 0,23 accettabile, sotto non riprodotta.

  Il template non è la struttura vera del complesso del bersaglio: DockQ qui misura quanto il modello riproduce
  l'interfaccia del template (quello che un modello per omologia dovrebbe fare), non l'accuratezza rispetto al
  complesso reale. Le coppie di residui si confrontano anche se il residuo è diverso (mutazioni).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable, Optional

import numpy as np

from ..core.errors import AppError
from . import geometry, interactions, interfaces, sequences
from .sequences import PirEntry
from .structure import _resid, three_to_one

VERSION = 1
MAX_CHAINS = 12
MAX_RESIDUES = 3000
SYMMETRY_WEIGHT = 1.0
MAX_SYMMETRY_WEIGHT = 10.0
CONTACT_CUTOFF = 5.0  # Å, contatti fra residui (Fnat, CAPRI)
IRMS_CUTOFF = 10.0  # Å, residui d'interfaccia per iRMS (CAPRI, DockQ)
BACKBONE = ("N", "CA", "C", "O")
IRMS_D0 = 1.5  # Å, scala di iRMS in DockQ
LRMS_D0 = 8.5  # Å, scala di LRMS in DockQ
DOCKQ_CLASSES = ((0.80, "alta"), (0.49, "media"), (0.23, "accettabile"), (float("-inf"), "non riprodotta"))
LEVEL_TONE = {"alta": "ok", "media": "ok", "accettabile": "warn", "non riprodotta": "err", "nuova": "warn",
              "non valutabile": "warn"}
_CHAIN = re.compile(r"^[A-Za-z0-9]$")
LIMITS = ("Il template non è la struttura vera del complesso del bersaglio: il confronto dice quanto il modello "
          "riproduce l'interfaccia del template, non se l'interfaccia è corretta. Un'interfaccia del template "
          "piccola (contatto cristallino) resta piccola anche nel modello: vedi la plausibilità dell'area.")


@dataclass
class ComplexChain:
    template_chain: str
    name: str
    sequence: str
    same_as: Optional[str] = None


# ---------------------------------------------------------------------------------------------- bersaglio
def parse_target(raw: dict, *, max_chains: int = MAX_CHAINS, max_residues: int = MAX_RESIDUES) -> dict:
    """Bersaglio a più catene: {"name", "chains": [{"template_chain", "sequence" | "same_as", "name"}]} →
    {"name", "chains": [ComplexChain]} con le sequenze validate. Errori chiari se la corrispondenza catena del
    template → sequenza è incompleta o ambigua."""
    if not isinstance(raw, dict):
        raise AppError("Il complesso deve indicare le catene: {name, chains: [{template_chain, sequence}]}.",
                       code="invalid_complex")
    items = raw.get("chains") or []
    if not isinstance(items, list) or len(items) < 2:
        raise AppError("Un complesso ha almeno due catene: per una sola catena usa l'allineamento automatico.",
                       code="invalid_complex")
    if len(items) > max_chains:
        raise AppError(f"Troppe catene nel complesso ({len(items)}, massimo {max_chains}).", code="complex_too_large")
    chains: list[ComplexChain] = []
    seen: set[str] = set()
    for k, item in enumerate(items, start=1):
        item = item if isinstance(item, dict) else {}
        tc = str(item.get("template_chain") or "").strip()
        if not _CHAIN.match(tc):
            raise AppError(f"Catena {k} del complesso: indica la catena del template (un carattere, es. A).",
                           code="invalid_complex")
        if tc in seen:
            raise AppError(f"La catena {tc} del template è assegnata a più catene del bersaglio: ogni catena del "
                           "template modella una sola catena.", code="invalid_complex")
        seen.add(tc)
        same = str(item.get("same_as") or "").strip() or None
        chains.append(ComplexChain(template_chain=tc, name=str(item.get("name") or "").strip()[:60],
                                   sequence=str(item.get("sequence") or ""), same_as=same))
    by_chain = {c.template_chain: c for c in chains}
    for c in chains:
        if c.same_as:
            ref = by_chain.get(c.same_as)
            if ref is None:
                raise AppError(f"La catena {c.template_chain} ha la stessa sequenza della catena {c.same_as}, che non "
                               "è fra le catene del complesso.", code="invalid_complex")
            if ref.same_as or not ref.sequence.strip():
                raise AppError(f"La catena {c.template_chain} rimanda alla catena {c.same_as}, che non ha una "
                               "sequenza propria.", code="invalid_complex")
    explicit = {c.template_chain: c.name for c in chains if c.name}
    for c in chains:
        text = by_chain[c.same_as].sequence if c.same_as else c.sequence
        if not text.strip():
            raise AppError(f"Manca la sequenza del bersaglio per la catena {c.template_chain} del template: incollala, "
                           "scegline una del progetto o indica «stessa della catena …».", code="invalid_complex")
        c.sequence, _kind = sequences.validate_sequence(text, "protein", f"sequenza della catena {c.template_chain}")
        if not c.name:  # senza nome: quello della catena di cui ripete la sequenza, altrimenti la catena del template
            c.name = explicit.get(c.same_as or "") or c.template_chain
    total = sum(len(c.sequence) for c in chains)
    if total > max_residues:
        raise AppError(f"Il complesso ha {total} residui in tutto (massimo {max_residues}: il tempo di MODELLER cresce "
                       "con i residui del modello). Modella meno catene o un dominio alla volta.",
                       code="complex_too_large")
    name = sequences.sanitize_code(raw.get("name"), default="COMPLEX")
    return {"name": name, "chains": chains}


def symmetry_groups(chain_sequences: list[str]) -> list[list[int]]:
    """Gruppi (indici, ≥ 2) di catene del bersaglio con la stessa sequenza, nell'ordine della prima comparsa."""
    groups: dict[str, list[int]] = {}
    for k, seq in enumerate(chain_sequences):
        letters = "".join(c for c in seq.upper() if c.isalpha())
        if letters:
            groups.setdefault(letters, []).append(k)
    return [g for g in groups.values() if len(g) > 1]


def target_chains(entry: PirEntry) -> list[str]:
    """Sequenze (solo lettere) delle catene di una voce PIR."""
    return ["".join(c for c in piece if c.isalpha()) for piece in entry.sequence.split("/")]


def break_columns(seq: str) -> list[int]:
    return [i for i, c in enumerate(seq) if c == "/"]


# ---------------------------------------------------------------------------------------------- allineamento
def merge_chain_alignments(texts: list[str], template: dict, target_code: str) -> list[PirEntry]:
    """Unisce gli allineamenti per catena (PIR di `align2d`, uno per catena del template, nell'ordine) in un PIR a più
    catene: template (intervallo dal primo residuo della prima catena all'ultimo dell'ultima) e bersaglio, con '/'
    nelle stesse colonne. Ogni allineamento deve avere la sua catena del template intera (come la scrive align2d)."""
    expected = template["sequence"].split("/")
    if len(texts) != len(template["chains"]) or len(expected) != len(texts):
        raise AppError("Allineamenti per catena incompleti: MODELLER non ha allineato tutte le catene.",
                       code="modeller_error")
    t_rows, s_rows = [], []
    for chain, want, text in zip(template["chains"], expected, texts):
        entries = sequences.parse_pir(text)
        tmpl = [e for e in entries if e.is_template]
        tgt = [e for e in entries if e.kind == "sequence"]
        if len(tmpl) != 1 or len(tgt) != 1:
            raise AppError(f"Allineamento della catena {chain} non valido (serve un template e un bersaglio).",
                           code="modeller_error")
        letters = "".join(c for c in tmpl[0].sequence if c.isalpha())
        if letters != want:
            raise AppError(f"L'allineamento della catena {chain} non contiene la catena {chain} intera del template "
                           f"({len(letters)} residui invece di {len(want)}).", code="modeller_error")
        t_rows.append(tmpl[0].sequence.replace("/", ""))
        s_rows.append(tgt[0].sequence.replace("/", ""))
    chains = template["chains"]
    return [sequences.template_entry(template["code"], "/".join(t_rows), template["first"], chains[0],
                                     template["last"], chains[-1]),
            sequences.target_entry(target_code, "/".join(s_rows))]


def check_chains(entries: list[PirEntry], templates: list[dict], *, strict: bool) -> list[str]:
    """Coerenza delle catene di un PIR con i template preparati.

    - La voce di ogni template ha tanti tratti ('/') quante catene del template nel suo intervallo (catena iniziale e
      finale dei campi, oppure tutte le catene scelte): altrimenti residui di una catena del template passerebbero per
      residui di un'altra. Con `strict` (modalità Complesso) è un errore, altrimenti un avviso.
    - I '/' del bersaglio e dei template nelle stesse colonne: MODELLER lo tollera (verificato con MODELLER 10.8), ma
      allora un residuo di una catena del bersaglio è allineato a un'altra catena del template. Con `strict` è un
      errore; altrimenti l'avviso è già quello di `sequences.validate_pir`.
    Restituisce gli avvisi."""
    notes: list[str] = []
    target = next((e for e in entries if e.kind == "sequence"), None)
    if target is None:
        return notes
    by_code = {t["code"].upper(): t for t in templates}
    for e in entries:
        if not e.is_template:
            continue
        t = by_code.get(e.code.upper())
        if t is None:
            continue
        chains = list(t.get("chains") or [])
        fields = (e.fields + [""] * 4)[:4]
        first = fields[1] if fields[1] in chains else chains[0] if chains else ""
        last = fields[3] if fields[3] in chains else chains[-1] if chains else ""
        if first in chains and last in chains and chains.index(last) >= chains.index(first):
            span = chains[chains.index(first):chains.index(last) + 1]
            pieces = len(e.sequence.split("/"))
            if pieces != len(span):
                text = (f"Nel PIR la voce di {e.code} ha {pieces} {'tratto' if pieces == 1 else 'tratti'} separati da "
                        f"'/' ma il suo intervallo comprende {len(span)} {'catena' if len(span) == 1 else 'catene'} "
                        f"del template ({', '.join(span)}): servono tanti tratti quante catene, nell'ordine del "
                        "template.")
                if strict:
                    raise AppError(text, code="invalid_pir")
                notes.append(text)
        if strict and break_columns(e.sequence) != break_columns(target.sequence):
            raise AppError(f"I separatori di catena ('/') di {e.code} e del bersaglio non sono nelle stesse colonne del "
                           "PIR: nella modalità Complesso ogni catena del bersaglio sta di fronte alla sua catena del "
                           "template.", code="invalid_pir")
    return notes


# ---------------------------------------------------------------------------------------------- corrispondenza residui
def _protein(model, start: Optional[tuple[str, str]] = None) -> list:
    """Residui ATOM nell'ordine del file, dal residuo `start` (catena, numero) se indicato."""
    out = []
    started = start is None
    for chain in model:
        for res in chain:
            if res.id[0] != " ":
                continue
            if not started and (chain.id, _resid(res)) == start:
                started = True
            if started:
                out.append(res)
    return out


def _columns(seq: str, residues: list) -> dict[int, object]:
    """Colonna → residuo: le lettere consumano i residui in ordine ('.', '/' e '-' no)."""
    out: dict[int, object] = {}
    k = 0
    for i, c in enumerate(seq):
        if not c.isalpha():
            continue
        if k >= len(residues):
            raise ValueError("più lettere che residui")
        res = residues[k]
        letter = three_to_one(res.get_resname())
        if c.upper() != "X" and letter != "X" and c.upper() != letter:
            raise ValueError(f"lettera {c} contro {res.get_resname()} {_resid(res)}")
        out[i] = res
        k += 1
    return out


def residue_map(entries: list[PirEntry], template: dict, template_model, model) -> dict:
    """Corrispondenza residuo del template → residuo del modello dalle colonne del PIR usato da MODELLER.

    Restituisce {"residues": {(catena, numero) del template: (catena, numero) del modello}, "chains": {catena del
    template: catena del modello}}; ValueError se il PIR non corrisponde alla copia preparata o al modello."""
    target = next((e for e in entries if e.kind == "sequence"), None)
    entry = next((e for e in entries if e.is_template and e.code == template["code"]), None)
    if target is None or entry is None:
        raise ValueError("voce del template o del bersaglio assente dal PIR")
    fields = (entry.fields + [""] * 4)[:4]
    start = None
    if fields[0] and fields[0].upper() not in ("FIRST", "."):
        start = ((fields[1] or template["chains"][0]).strip(), fields[0].strip())
    tmap = _columns(entry.sequence, _protein(template_model, start))
    mmap = _columns(target.sequence, _protein(model))
    residues: dict[tuple[str, str], tuple[str, str]] = {}
    votes: dict[str, dict[str, int]] = {}
    for col, tres in tmap.items():
        mres = mmap.get(col)
        if mres is None:
            continue
        tkey = (tres.get_parent().id, _resid(tres))
        mkey = (mres.get_parent().id, _resid(mres))
        residues[tkey] = mkey
        count = votes.setdefault(tkey[0], {})
        count[mkey[0]] = count.get(mkey[0], 0) + 1
    chains = {tc: max(c, key=c.get) for tc, c in votes.items()}
    return {"residues": residues, "chains": chains}


# ---------------------------------------------------------------------------------------------- DockQ
def _heavy(chain) -> list:
    return [a for res in chain if res.id[0] == " " for a in res if (a.element or "").upper() not in ("H", "D")]


def _contacts(model, a: str, b: str, cutoff: float) -> set[tuple[tuple[str, str], tuple[str, str]]]:
    """Coppie di residui (di a, di b) con due atomi pesanti entro `cutoff` Å. Un albero sugli atomi di b e una ricerca
    per atomo di a: niente elenco di tutte le coppie di atomi entro 10 Å delle due catene."""
    from Bio.PDB import NeighborSearch

    if a not in model or b not in model:
        return set()
    atoms_a, atoms_b = _heavy(model[a]), _heavy(model[b])
    if not atoms_a or not atoms_b:
        return set()
    search = NeighborSearch(atoms_b)
    out = set()
    for atom in atoms_a:
        near = search.search(atom.coord, cutoff, level="R")
        if near:
            ka = (a, _resid(atom.get_parent()))
            out.update((ka, (b, _resid(res))) for res in near)
    return out


def _backbone(model, keys: list[tuple[str, str]]) -> dict[tuple[str, str, str], np.ndarray]:
    out = {}
    index = {(c.id, _resid(r)): r for c in model for r in c if r.id[0] == " "}
    for key in keys:
        res = index.get(key)
        if res is None:
            continue
        for name in BACKBONE:
            if name in res:
                out[(key[0], key[1], name)] = np.asarray(res[name].coord, dtype=float)
    return out


def _paired(native: dict, model: dict, rmap: dict) -> tuple[np.ndarray, np.ndarray]:
    """Coordinate appaiate (template, modello) degli stessi atomi dello scheletro dei residui corrispondenti."""
    xs, ys = [], []
    for (chain, resnum, atom), xyz in native.items():
        mkey = rmap.get((chain, resnum))
        if mkey is None:
            continue
        other = model.get((mkey[0], mkey[1], atom))
        if other is not None:
            xs.append(xyz)
            ys.append(other)
    return np.array(xs).reshape(-1, 3), np.array(ys).reshape(-1, 3)


def _fit_rmsd(fixed: np.ndarray, mobile: np.ndarray) -> Optional[float]:
    if len(fixed) < 3:
        return None
    rot, trans = geometry.kabsch(mobile, fixed)
    return geometry.rmsd(geometry.apply(mobile, rot, trans), fixed)


def dockq_class(value: Optional[float]) -> Optional[str]:
    if value is None:
        return None
    return next(label for threshold, label in DOCKQ_CLASSES if value >= threshold)


def dockq(template_model, model, pair: tuple[str, str], rmap: dict, chains: dict[str, str]) -> dict:
    """Fnat, iRMS, LRMS e DockQ della coppia di catene del template `pair` (con il template come riferimento) sul
    modello, attraverso la corrispondenza `rmap` (residuo del template → residuo del modello) e `chains` (catena del
    template → catena del modello). Ricettore = la catena più lunga del template, come in CAPRI."""
    a, b = pair
    size = {c: sum(1 for r in template_model[c] if r.id[0] == " ") for c in pair}
    receptor, ligand = (a, b) if size[a] >= size[b] else (b, a)
    ma, mb = chains.get(a), chains.get(b)
    native = _contacts(template_model, a, b, CONTACT_CUTOFF)
    reproduced = 0
    if ma and mb and native:
        model_contacts = _contacts(model, ma, mb, CONTACT_CUTOFF)
        for x, y in native:
            mx, my = rmap.get(x), rmap.get(y)
            if mx is not None and my is not None and (mx, my) in model_contacts:
                reproduced += 1
    fnat = reproduced / len(native) if native else None
    near = _contacts(template_model, a, b, IRMS_CUTOFF)
    iface = sorted({k for pair_ in near for k in pair_})
    fixed, mobile = _paired(_backbone(template_model, iface), _backbone(model, [rmap[k] for k in iface if k in rmap]),
                            rmap)
    irms = _fit_rmsd(fixed, mobile)
    lrms = None
    rec_keys = [(receptor, _resid(r)) for r in template_model[receptor] if r.id[0] == " "]
    lig_keys = [(ligand, _resid(r)) for r in template_model[ligand] if r.id[0] == " "]
    r_fixed, r_mobile = _paired(_backbone(template_model, rec_keys),
                                _backbone(model, [rmap[k] for k in rec_keys if k in rmap]), rmap)
    l_fixed, l_mobile = _paired(_backbone(template_model, lig_keys),
                                _backbone(model, [rmap[k] for k in lig_keys if k in rmap]), rmap)
    if len(r_fixed) >= 3 and len(l_fixed):
        rot, trans = geometry.kabsch(r_mobile, r_fixed)
        lrms = geometry.rmsd(geometry.apply(l_mobile, rot, trans), l_fixed)
    score = None
    if fnat is not None and irms is not None and lrms is not None:
        score = (fnat + 1.0 / (1.0 + (irms / IRMS_D0) ** 2) + 1.0 / (1.0 + (lrms / LRMS_D0) ** 2)) / 3.0
    return {"fnat": None if fnat is None else round(fnat, 3), "native_contacts": len(native),
            "reproduced_contacts": reproduced, "irms": None if irms is None else round(irms, 2),
            "lrms": None if lrms is None else round(lrms, 2), "dockq": None if score is None else round(score, 3),
            "dockq_class": dockq_class(score), "receptor": receptor, "ligand": ligand,
            "interface_atoms": int(len(fixed))}


# ---------------------------------------------------------------------------------------------- confronto
def _pair_of(ifaces: dict, a: Optional[str], b: Optional[str]) -> Optional[dict]:
    if not a or not b:
        return None
    return next((p for p in ifaces.get("pairs") or [] if set(p["chains"]) == {a, b}), None)


def _label(key: tuple[str, str]) -> str:
    return f"{key[0]}{key[1]}"


def _fmt(value: Optional[float], digits: int = 2) -> str:
    return "—" if value is None else f"{value:.{digits}f}".replace(".", ",")


def compare(template_model, model, template_ifaces: dict, model_ifaces: dict, rmap: dict) -> list[dict]:
    """Confronto di ogni interfaccia del template con quella corrispondente del modello, più le interfacce del modello
    che il template non ha. Ogni voce: catene del template e del modello, aree, residui conservati, DockQ, esito e
    motivazione."""
    residues = rmap["residues"]
    chains = rmap["chains"]
    out: list[dict] = []
    matched: set[frozenset] = set()
    for tp in template_ifaces.get("pairs") or []:
        a, b = tp["chains"]
        ma, mb = chains.get(a), chains.get(b)
        mp = _pair_of(model_ifaces, ma, mb)
        if mp is not None:
            matched.add(frozenset(mp["chains"]))
        t_keys = [(c, r["resnum"]) for c in (a, b) for r in tp["residues"].get(c) or []]
        m_keys = {(c, r["resnum"]) for c in (mp["chains"] if mp else []) for r in mp["residues"].get(c) or []}
        conserved = [k for k in t_keys if residues.get(k) in m_keys]
        unaligned = [k for k in t_keys if k not in residues]
        lost = [residues[k] for k in t_keys if k in residues and residues[k] not in m_keys]
        scores = dockq(template_model, model, (a, b), residues, chains) if ma and mb else {
            "fnat": None, "irms": None, "lrms": None, "dockq": None, "dockq_class": None, "native_contacts": 0,
            "reproduced_contacts": 0, "receptor": a, "ligand": b, "interface_atoms": 0}
        percent = round(100.0 * len(conserved) / len(t_keys), 1) if t_keys else None
        area_t, area_m = tp["area"], (mp["area"] if mp else 0.0)
        ratio = round(100.0 * area_m / area_t, 1) if area_t else None
        level = scores["dockq_class"] or "non valutabile"
        parts = []
        if scores["dockq"] is not None:
            parts.append(f"DockQ {_fmt(scores['dockq'], 2)} ({level}; Fnat {_fmt(scores['fnat'], 2)}, iRMS "
                         f"{_fmt(scores['irms'])} Å, LRMS {_fmt(scores['lrms'])} Å)")
        else:
            parts.append("DockQ non calcolabile (nessun contatto entro 5 Å nel template o troppi residui non "
                         "allineati)")
        if percent is not None:
            parts.append(f"residui d'interfaccia del template conservati {_fmt(percent, 0)}% ({len(conserved)} su "
                         f"{len(t_keys)}" + (f", {len(unaligned)} non allineati" if unaligned else "") + ")")
        parts.append(f"area {_fmt(area_m, 0)} Å² contro {_fmt(area_t, 0)} Å² del template"
                     + (f" ({_fmt(ratio, 0)}%)" if ratio is not None else ""))
        if mp is None:
            parts.append("le catene corrispondenti del modello non sono più a contatto")
        verdict = {"alta": "interfaccia del template riprodotta", "media": "interfaccia del template in gran parte "
                   "riprodotta", "accettabile": "interfaccia del template riprodotta solo in parte",
                   "non riprodotta": "interfaccia del template non riprodotta",
                   "non valutabile": "confronto dell'interfaccia non valutabile"}[level]
        out.append({
            "template_chains": [a, b], "model_chains": list(mp["chains"]) if mp else [ma, mb],
            "status": level, "tone": LEVEL_TONE[level], "area_template": area_t, "area_model": area_m,
            "area_ratio": ratio, "template_residues": len(t_keys), "conserved": len(conserved),
            "conserved_percent": percent, "unaligned": len(unaligned),
            "conserved_residues": [_label(residues[k]) for k in conserved], "lost_residues": [_label(k) for k in lost],
            **{k: scores[k] for k in ("fnat", "irms", "lrms", "dockq", "dockq_class", "native_contacts",
                                      "reproduced_contacts", "receptor", "ligand")},
            "text": f"{verdict[0].upper()}{verdict[1:]}: " + "; ".join(parts) + ".",
        })
    for mp in model_ifaces.get("pairs") or []:
        if frozenset(mp["chains"]) in matched:
            continue
        out.append({"template_chains": None, "model_chains": list(mp["chains"]), "status": "nuova",
                    "tone": LEVEL_TONE["nuova"], "area_template": 0.0, "area_model": mp["area"], "area_ratio": None,
                    "template_residues": 0, "conserved": 0, "conserved_percent": None, "unaligned": 0,
                    "conserved_residues": [], "lost_residues": [], "fnat": None, "irms": None, "lrms": None,
                    "dockq": None, "dockq_class": None, "native_contacts": 0, "reproduced_contacts": 0,
                    "receptor": None, "ligand": None,
                    "text": f"Interfaccia assente nel template (area {_fmt(mp['area'], 0)} Å²): contatto creato dalla "
                            "modellazione (ottimizzazione o loop senza template), da verificare."})
    return out


def _slim(ifaces: dict) -> dict:
    """Interfacce senza l'elenco di tutti i residui (le coppie hanno già i residui d'interfaccia)."""
    return {k: v for k, v in ifaces.items() if k != "residues"}


def assess(model, template_model, entries: list[PirEntry], template: dict, *, template_ifaces: Optional[dict] = None,
           check: Optional[Callable[[], None]] = None) -> dict:
    """Analisi delle interfacce del modello di un complesso e confronto con quelle del template.

    `template`: informazioni del template preparato (code, chains); `template_model`: la sua copia preparata;
    `template_ifaces`: interfacce del template già calcolate (una volta per job). Restituisce {"version", "template",
    "chains" [{"model", "template"}], "interfaces" (modello, domain/interfaces senza l'elenco di tutti i residui),
    "template_interfaces", "comparison", "status", "tone", "summary", "notes", "limits", "criteria"}."""
    inter = interactions.analyze(model)
    model_ifaces = interfaces.analyze(model, inter, check=check)
    if template_ifaces is None:
        template_ifaces = interfaces.analyze(template_model, interactions.analyze(template_model), check=check)
    notes: list[str] = []
    base = {"version": VERSION, "template": template["code"], "interfaces": _slim(model_ifaces),
            "template_interfaces": _slim(template_ifaces), "limits": LIMITS,
            "criteria": {"contact": CONTACT_CUTOFF, "irms_cutoff": IRMS_CUTOFF, "backbone": list(BACKBONE),
                         "classes": {label: threshold for threshold, label in DOCKQ_CLASSES if threshold > -1},
                         "min_delta": interfaces.INTERFACE_MIN_DELTA}}
    try:
        rmap = residue_map(entries, template, template_model, model)
    except ValueError as exc:
        notes.append(f"Confronto con il template non disponibile: il PIR non corrisponde alla copia preparata o al "
                     f"modello ({exc}).")
        return {**base, "chains": [], "comparison": [], "status": "non valutabile", "tone": "warn",
                "summary": notes[0], "notes": notes}
    comparison = compare(template_model, model, template_ifaces, model_ifaces, rmap)
    chains = [{"template": tc, "model": mc} for tc, mc in rmap["chains"].items()]
    if not template_ifaces.get("pairs"):
        notes.append("Il template non ha interfacce fra le catene scelte (nessun residuo con ΔSASA > 1 Å²): le catene "
                     "del modello sono indipendenti anche nel template.")
    ranked = [c for c in comparison if c["template_chains"]]
    order = ["non riprodotta", "non valutabile", "accettabile", "media", "alta"]
    worst = min((c["status"] for c in ranked), key=order.index) if ranked else "non valutabile"
    new = [c for c in comparison if not c["template_chains"]]
    if ranked:
        summary = (f"{len(ranked)} {'interfaccia' if len(ranked) == 1 else 'interfacce'} del template: esito peggiore "
                   f"«{worst}». " + " ".join(f"{'–'.join(c['model_chains'])}: {c['text']}" for c in ranked))
    else:
        summary = notes[0] if notes else "Nessuna interfaccia da confrontare."
    if new:
        summary += f" {len(new)} {'interfaccia nuova' if len(new) == 1 else 'interfacce nuove'} nel modello."
    return {**base, "chains": chains, "comparison": comparison, "status": worst, "tone": LEVEL_TONE[worst],
            "summary": summary, "notes": notes}


def brief(assessment: Optional[dict]) -> Optional[dict]:
    """Riepilogo per la tabella dei modelli: esito peggiore, DockQ minimo, interfacce."""
    if not isinstance(assessment, dict):
        return None
    values = [c["dockq"] for c in assessment.get("comparison") or [] if c.get("dockq") is not None]
    return {"status": assessment.get("status"), "tone": assessment.get("tone"),
            "dockq": min(values) if values else None, "interfaces": len((assessment.get("interfaces") or {})
                                                                          .get("pairs") or [])}
