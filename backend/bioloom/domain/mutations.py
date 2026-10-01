"""Mutagenesi in silico (F9.2): notazione, copia per MODELLER, indicatori strutturali, stima qualitativa dell'effetto,
scansione di alanina e varianti di UniProt. Logica pura (Biopython, numpy); MODELLER e OpenMM li eseguono
`engines/mutate.py` ed `engines/refine.py` in processi separati.

**Protocollo del mutante** (script in `engines/mutate.py`, derivato da `mutate_model.py` della Sali lab, Feyfant, Sali &
Fiser, Protein Sci. 16:2030, 2007): sostituzione del residuo (`Selection.mutate`), topologia e coordinate ricostruite dal
selvatico (`transfer_xyz` + costruzione per coordinate interne), restrizioni statistiche (stereochimica, φ/ψ binormali,
diedri ω e χ1–χ4); il residuo mutato si randomizza (4 Å) e si ottimizza da solo, poi con le catene laterali entro
`RADIUS` Å (estensione di BioLoom: lo script originale ottimizza solo il residuo mutato); gradienti coniugati, breve
ricottura di dinamica molecolare (fino a 1000 K) e gradienti coniugati finali. Il resto della struttura non si muove.

- **Stesso trattamento per il selvatico:** il selvatico passa dallo stesso protocollo (il residuo "mutato" in sé stesso,
  stessa randomizzazione, stessi semi), così la differenza misura la sostituzione e non l'ottimizzazione: su 1UBQ la sola
  riottimizzazione di Ile30 nel selvatico cambia il DOPE di 40–90 unità rispetto alla struttura cristallografica.
- **Più modelli:** `MODELS` modelli per variante con semi consecutivi; si tiene quello con il DOPE più basso, come la
  scelta del modello migliore di AutoModel (Shen & Sali 2006). Misurato su 1UBQ: due serie di 3 semi danno ΔDOPE di
  K48R −39 e −34, di I30G +349 e +368 (un solo modello variava fino a 55 unità).

**Stima dell'effetto (non un ΔΔG):** si combinano in modo dichiarato indicatori con soglie dalla letteratura o misurate,
ognuno con il suo peso qualitativo ("forte", "moderato", "informativo"), e se ne ricava una classe: *probabilmente
destabilizzante* (un segnale forte o almeno due moderati), *incerta* (un segnale moderato, oppure residuo sepolto senza
segnali, oppure solo segnali di possibile stabilizzazione), *probabilmente neutra* (nessun segnale e residuo esposto).

| Indicatore | Segnale | Fonte o motivo |
|---|---|---|
| ΔDOPE totale (mutante − selvatico ottimizzato) in "residui equivalenti" = ΔDOPE / (|DOPE del selvatico| / residui) | ≥ 2 forte, ≥ 1 moderato, ≤ −1 possibile guadagno | DOPE, Shen & Sali 2006; scala per residuo per confrontare proteine di dimensioni diverse; soglie sopra il rumore misurato del protocollo (scelta BioLoom) |
| ΔΔE OpenMM = (E_mut − E_wt) − (E_rif,mut − E_rif,wt), riferimento = tripeptide i−1..i+1 isolato | ≥ 100 kJ/mol forte, ≥ 50 moderato, ≤ −50 possibile guadagno | energia potenziale AMBER ff14SB + OBC2 dopo la stessa minimizzazione con restrizioni (`domain/refinement.py`); soglie oltre lo scarto misurato fra due selvatici ottimizzati (fino a 36 kJ/mol su 1UBQ) |
| prolina in α-elica oltre il primo giro | forte | propensione per l'elica di Pace & Scholtz 1998 (Pro +3,16 kcal/mol rispetto ad Ala); Pro tollerata nel primo giro (MacArthur & Thornton 1991) |
| altra sostituzione in α-elica con ΔP ≥ 0,5 kcal/mol | moderato | Pace & Scholtz 1998 |
| prolina o glicina in un filamento β | forte (Pro con l'NH legato), moderato | Pro senza NH donatore; Gly e Pro con la propensione β più bassa (Minor & Kim 1994; Smith, Withka & Regan 1994) |
| conformazione della catena principale non permessa al nuovo residuo (es. Gly con φ > 0) | forte | regioni di Ramachandran di BioLoom (Lovell et al. 2003) |
| ponte disolfuro perso | forte | Betz 1993 |
| legami idrogeno persi | sepolto ≥ 1, esposto ≥ 2: moderato | circa 1 kcal/mol per legame (Myers & Pace 1996); quelli esposti competono con l'acqua |
| ponte salino perso | sepolto: moderato, esposto: informativo | i ponti salini di superficie contano poco (Strop & Mayo 2000) |
| superficie apolare sepolta persa ≥ 40 Å² | moderato | effetto idrofobico circa 25 cal·mol⁻¹·Å⁻² (Chothia 1974; Reynolds, Gilbert & Tanford 1974): 40 Å² ≈ 1 kcal/mol |
| residuo nel nucleo (RSA < 20%) con sostituzione non conservativa (BLOSUM62 < 0) | moderato | RSA con le ASA massime di Tien et al. 2013; Henikoff & Henikoff 1992 |
| carica introdotta nel nucleo | moderato | cariche sepolte destabilizzanti (Isom et al. 2011) |
| nuovi contatti sterici gravi del residuo mutato | moderato | sovrapposizione > 0,4 Å (Word et al. 1999), come il clashscore |
| residuo d'interfaccia (ΔSASA > 1 Å² fra catene) | effetto sul legame, separato dalla stabilità | Krissinel & Henrick 2007 |

**Scansione di alanina:** ogni residuo scelto diventa alanina (stesso protocollo, un processo per residuo). Glicina e
prolina si escludono, come nella scansione di alanina computazionale (Kortemme & Baker 2002): la sostituzione cambia
la flessibilità della catena principale e non misura il contributo della catena laterale; l'alanina non ha nulla da
togliere. Al più `MAX_SCAN` residui (`MAX_SCAN_ENERGY` con l'energia di OpenMM). Hot spot d'interfaccia (definizione
sperimentale: ΔΔG di legame ≥ 2 kcal/mol, Bogan & Thorn 1998): *probabile* se il residuo è d'interfaccia, sepolto nel
complesso (RSA < 20%, come l'"O-ring" di Bogan & Thorn) e la sua catena laterale contribuisce al legame (ΔDOPE fra le
catene ≥ 0,5 residui equivalenti, oppure un legame idrogeno o un ponte salino fra catene perso); *possibile* con il
solo contributo al legame.

**Varianti di UniProt:** le varianti naturali e di mutagenesi sono numerate sulla sequenza di UniProt; si portano sulla
struttura con un allineamento globale (Needleman-Wunsch, BLOSUM62, come la conservazione), mai assumendo che le due
numerazioni coincidano.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable, Optional

import numpy as np

from ..core.errors import AppError
from . import ramachandran, refinement
from .structure import WATER_NAMES, _resid, is_amino_residue, three_to_one

VERSION = 1

# ------------------------------------------------------------------------------------------ protocollo
RADIUS = 5.0           # Å: catene laterali ottimizzate attorno ai residui mutati (seconda fase)
LOCAL_RADIUS = 8.0     # Å: residui del DOPE locale
MODELS = 3             # modelli per variante (semi consecutivi), si tiene il DOPE più basso
SEED = -49837          # seme di mutate_model.py; MODELLER accetta da -50000 a -2
MAX_MUTATIONS = 10     # sostituzioni in una variante
MAX_SCAN = 50          # residui di una scansione di alanina
MAX_SCAN_ENERGY = 20   # con l'energia di OpenMM: 4 minimizzazioni per residuo
RANDOMIZE = 4.0        # Å: deviazione della randomizzazione del residuo mutato (mutate_model.py)
FRAGMENT_SPACING = 1000.0  # Å fra i frammenti di riferimento nello stesso calcolo di OpenMM

# ------------------------------------------------------------------------------------------ soglie
CORE_RSA = 0.20
DOPE_STRONG = 2.0       # residui equivalenti
DOPE_MODERATE = 1.0
DOPE_GAIN = -1.0
ENERGY_STRONG = 100.0   # kJ/mol
ENERGY_MODERATE = 50.0
ENERGY_GAIN = -50.0
HELIX_MODERATE = 0.5    # kcal/mol (Pace & Scholtz 1998)
HELIX_FIRST_TURN = 3    # posizioni dall'inizio dell'elica in cui la prolina è tollerata
APOLAR_LOSS = 40.0      # Å²
HYDROPHOBIC_CAL_A2 = 25.0
HOTSPOT_DOPE = 0.5      # residui equivalenti di DOPE fra le catene
KCAL = 4.184

# Propensione per l'α-elica, ΔΔG rispetto all'alanina in kcal/mol (Pace & Scholtz, Biophys. J. 75:422, 1998)
HELIX_PROPENSITY = {"A": 0.0, "L": 0.21, "R": 0.21, "M": 0.24, "K": 0.26, "Q": 0.39, "E": 0.40, "I": 0.41, "W": 0.49,
                    "S": 0.50, "Y": 0.53, "F": 0.54, "H": 0.61, "V": 0.61, "N": 0.65, "T": 0.66, "C": 0.68, "D": 0.69,
                    "G": 1.00, "P": 3.16}
AMINO = {"A": "ALA", "C": "CYS", "D": "ASP", "E": "GLU", "F": "PHE", "G": "GLY", "H": "HIS", "I": "ILE", "K": "LYS",
         "L": "LEU", "M": "MET", "N": "ASN", "P": "PRO", "Q": "GLN", "R": "ARG", "S": "SER", "T": "THR", "V": "VAL",
         "W": "TRP", "Y": "TYR"}
THREE = {v: k for k, v in AMINO.items()}
CHARGED = set("DEKR")
HELIX_CODES = {"H"}
STRAND_CODES = {"E"}
CHAIN_LETTERS = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789"
BLANK = "_"  # catena senza identificativo nella notazione

CLASSES = {
    "destabilizing": "probabilmente destabilizzante",
    "uncertain": "incerta",
    "neutral": "probabilmente neutra",
}
CAVEAT = ("Stima qualitativa: combina indicatori strutturali, la differenza di DOPE (energia statistica) e, se "
          "calcolata, quella dell'energia del campo di forza fra mutante e selvatico ottimizzati allo stesso modo. Non è "
          "un ΔΔG sperimentale né una previsione calibrata (FoldX, Rosetta, metodi di apprendimento): indica la direzione "
          "probabile dell'effetto sulla stabilità. Anche i predittori dedicati sbagliano il segno di una parte delle "
          "mutazioni; un modello per omologia aggiunge l'incertezza della struttura, e il protocollo non considera "
          "ligandi, metalli, acqua né cambiamenti della catena principale oltre il residuo mutato.")

_BLOSUM = None


def blosum62(a: str, b: str) -> int:
    """Punteggio BLOSUM62 (Henikoff & Henikoff 1992) fra due amminoacidi standard."""
    global _BLOSUM
    if _BLOSUM is None:
        from Bio.Align import substitution_matrices

        _BLOSUM = substitution_matrices.load("BLOSUM62")
    return int(_BLOSUM[a, b])


# ------------------------------------------------------------------------------------------ notazione
@dataclass(frozen=True)
class Mutation:
    """Una sostituzione sulla struttura: catena come nel file (' ' per la catena senza identificativo), residuo con il
    codice di inserzione ("48", "52A"), lettera del selvatico e del mutante."""
    chain: str
    resnum: str
    wt: str
    mut: str

    @property
    def label(self) -> str:
        """"A:K48R"; "K48R" per la catena senza identificativo."""
        site = f"{self.wt}{self.resnum}{self.mut}"
        return site if not self.chain.strip() else f"{self.chain}:{site}"

    @property
    def site(self) -> str:
        return f"{self.chain.strip() or BLANK}:{self.resnum}"

    def to_dict(self) -> dict:
        return {"chain": self.chain, "resnum": self.resnum, "wt": self.wt, "mut": self.mut, "label": self.label,
                "wt_name": AMINO[self.wt], "mut_name": AMINO[self.mut]}


_TOKEN = re.compile(r"^(?:(?P<chain>[A-Za-z0-9_]{1,4}):)?(?P<body>[A-Za-z]{1,3}-?\d+[A-Za-z]{1,4})$")
_BODY = re.compile(r"^(?P<wt>[A-Z]{3}|[A-Z])(?P<num>-?\d+)(?P<rest>[A-Z]{1,4})$")
_SEPARATORS = re.compile(r"[,;\s]+")
NOTATION_HELP = ("Formato: catena, due punti, residuo originale, numero, nuovo residuo (es. A:K48R oppure A:LYS48ARG); "
                 "più mutazioni separate da virgole; la catena si può omettere se la struttura ne ha una sola; _ per la "
                 "catena senza identificativo; codice di inserzione dopo il numero (A:G52AA = Gly 52A → Ala).")


def _amino(code: str, token: str) -> str:
    if len(code) == 3:
        if code not in THREE:
            raise AppError(f"Amminoacido non valido in «{token}»: {code} (ammessi i 20 standard).", code="invalid_mutation")
        return THREE[code]
    if code not in AMINO:
        raise AppError(f"Amminoacido non valido in «{token}»: {code} (ammessi i 20 standard).", code="invalid_mutation")
    return code


def parse(text: str) -> list[dict]:
    """Mutazioni dalla notazione (senza struttura): [{chain (None se omessa), resnum, wt, mut, token}].

    Ogni voce: `[catena:]<originale><numero>[inserzione]<nuovo>` con codici a una o tre lettere; la parte dopo il numero
    si legge dalla sua lunghezza (1 = nuovo residuo, 2 = inserzione + residuo, 3 = residuo a tre lettere, 4 =
    inserzione + residuo a tre lettere), quindi non è mai ambigua. Le lettere degli amminoacidi non distinguono
    maiuscole e minuscole, la catena sì (come nei file)."""
    tokens = [t for t in _SEPARATORS.split(str(text or "").strip()) if t]
    if not tokens:
        raise AppError("Indica almeno una mutazione (es. A:K48R).", code="invalid_mutation")
    if len(tokens) > MAX_MUTATIONS:
        raise AppError(f"Troppe mutazioni in una variante ({len(tokens)}, al massimo {MAX_MUTATIONS}).",
                       code="invalid_mutation")
    out: list[dict] = []
    for token in tokens:
        m = _TOKEN.match(token)
        body = _BODY.match(m.group("body").upper()) if m else None
        if not m or not body:
            raise AppError(f"Mutazione non valida: «{token}». {NOTATION_HELP}", code="invalid_mutation")
        rest = body.group("rest")
        icode, mut = (rest[0], rest[1:]) if len(rest) in (2, 4) else ("", rest)
        wt = _amino(body.group("wt"), token)
        mut = _amino(mut, token)
        if wt == mut:
            raise AppError(f"«{token}» non cambia il residuo ({AMINO[wt]} resta {AMINO[mut]}).", code="invalid_mutation")
        chain = m.group("chain")
        out.append({"chain": chain, "resnum": f"{int(body.group('num'))}{icode}", "wt": wt, "mut": mut, "token": token})
    return out


def _protein_chains(model) -> list:
    return [c for c in model if any(is_amino_residue(r) or _modified_letter(r) for r in c)]


def chain_name(chain_id: str) -> str:
    """Nome della catena nei testi e nella notazione: '_' per la catena senza identificativo."""
    return chain_id.strip() or BLANK


_chain_name = chain_name


def find_residue(chain, resnum: str):
    """Residuo della catena con il numero (e codice di inserzione) indicato; il codice di inserzione si confronta anche
    in maiuscolo. None se non c'è."""
    wanted = str(resnum).strip()
    for res in chain:
        rid = _resid(res)
        if rid == wanted or rid.upper() == wanted.upper():
            return res
    return None


def _chain_for(model, chain: Optional[str], token: str):
    chains = _protein_chains(model)
    names = ", ".join(_chain_name(c.id) for c in chains) or "nessuna"
    if chain is None:
        if len(chains) != 1:
            raise AppError(f"Indica la catena di «{token}»: la struttura ne ha {len(chains)} ({names}).",
                           code="invalid_mutation")
        return chains[0]
    wanted = " " if chain == BLANK else chain
    for c in chains:
        if c.id == wanted or (wanted == " " and not c.id.strip()):
            return c
    raise AppError(f"Catena {chain} non presente nella struttura (catene: {names}).", code="invalid_mutation")


def _range_text(chain) -> str:
    residues = [r for r in chain if is_amino_residue(r) or _modified_letter(r)]
    return f"residui {_resid(residues[0])}–{_resid(residues[-1])}" if residues else "nessun residuo"


def residue_letter(res) -> str:
    """Lettera del residuo (i modificati con la lettera del residuo standard, es. MSE → M); 'X' se non è un
    amminoacido."""
    return three_to_one(res.get_resname()) if "CA" in res else "X"


def resolve(parsed: list[dict], model) -> list[Mutation]:
    """Mutazioni verificate sulla struttura: catena esistente (omessa solo con una catena), residuo esistente e
    amminoacido, residuo originale uguale a quello della struttura, una sola sostituzione per residuo."""
    out: list[Mutation] = []
    seen: set[tuple[str, str]] = set()
    for p in parsed:
        token = p.get("token") or f"{p['wt']}{p['resnum']}{p['mut']}"
        chain = _chain_for(model, p.get("chain"), token)
        res = find_residue(chain, p["resnum"])
        if res is None:
            raise AppError(f"Residuo {p['resnum']} non presente nella catena {_chain_name(chain.id)} "
                           f"({_range_text(chain)}) per «{token}».", code="invalid_mutation")
        letter = residue_letter(res)
        if letter not in AMINO or res.get_resname().strip() in WATER_NAMES:
            raise AppError(f"{res.get_resname().strip()} {_chain_name(chain.id)}{_resid(res)} non è un amminoacido: "
                           f"«{token}» non si può applicare.", code="invalid_mutation")
        if letter != p["wt"]:
            raise AppError(f"In «{token}» il residuo originale non corrisponde: {_chain_name(chain.id)}{_resid(res)} è "
                           f"{res.get_resname().strip()} ({letter}), non {AMINO[p['wt']]} ({p['wt']}). Usa la numerazione "
                           "della struttura (quella di UniProt può essere diversa).", code="invalid_mutation")
        key = (chain.id, _resid(res))
        if key in seen:
            raise AppError(f"Il residuo {_chain_name(chain.id)}{_resid(res)} compare in più mutazioni.",
                           code="invalid_mutation")
        seen.add(key)
        out.append(Mutation(chain.id, _resid(res), letter, p["mut"]))
    return out


def notation(mutations: Iterable[Mutation]) -> str:
    return ", ".join(m.label for m in mutations)


def file_tag(mutations: list[Mutation], multichain: bool) -> str:
    """Parte del nome dei file del mutante: "K48R", "A-K48R_B-E51Q" con più catene; oltre 60 caratteri "<n>mut"."""
    parts = [(f"{_chain_name(m.chain)}-" if multichain else "") + f"{m.wt}{m.resnum}{m.mut}" for m in mutations]
    tag = re.sub(r"[^A-Za-z0-9_-]", "", "_".join(parts))
    return tag if 0 < len(tag) <= 60 else f"{len(mutations)}mut"


# ------------------------------------------------------------------------------------------ copia per MODELLER
def _modified_letter(res) -> Optional[str]:
    """Residuo modificato della catena scritto come HETATM (MSE, SEP, TPO…): lettera del residuo standard, se ha un
    C-alfa e Biopython lo conosce; altrimenti None (ligando)."""
    if res.id[0] == " " or res.id[0] == "W" or "CA" not in res:
        return None
    letter = three_to_one(res.get_resname())
    return letter if letter in AMINO else None


def _first_altloc(atom):
    if atom.is_disordered():
        for code in ("A", "1", " "):
            if atom.disordered_has_id(code):
                return atom.disordered_get(code)
        return atom.selected_child
    return atom


def _pdb_line(serial: int, atom, resname: str, chain: str, res) -> str:
    _flag, num, icode = res.id
    name = atom.get_fullname() if len(atom.get_fullname()) == 4 else f" {atom.get_id():<3s}"
    element = (atom.element or "").strip().upper()
    if resname == "MET" and res.get_resname().strip() == "MSE" and atom.get_id() == "SE":
        name, element = " SD ", "S"  # selenometionina → metionina, come la preparazione dei template
    x, y, z = (float(v) for v in atom.coord)
    return (f"ATOM  {serial % 100000:5d} {name}{' '}{resname:>3s} {chain}{num:4d}{(icode or ' ')[:1]}   "
            f"{x:8.3f}{y:8.3f}{z:8.3f}{float(atom.occupancy or 1.0):6.2f}{float(atom.bfactor or 0.0):6.2f}          "
            f"{element:>2s}")


def modeller_input(model) -> tuple[str, dict]:
    """Copia della struttura per MODELLER (testo PDB) e come leggerla.

    Solo la proteina del primo modello: residui standard e modificati come il residuo standard (MSE → MET con SE → SD,
    SEP → SER…: gli atomi in più li scarta MODELLER), prima altloc, niente idrogeni, acque e gruppi eteroatomici (esclusi
    con avviso). Ogni tratto continuo (C–N ≤ 2,0 Å, come la rifinitura) è una catena per MODELLER, che altrimenti
    legherebbe i due residui ai lati di un'interruzione: il primo tratto di una catena con un identificativo di un
    carattere lo conserva, gli altri tratti (e le catene senza identificativo o con più caratteri) prendono una lettera
    libera, e `restore_chains` riporta i nomi originali nei file prodotti.

    `info`: chains {catena di MODELLER: catena originale}, residues {(catena originale, residuo): catena di MODELLER},
    groups {catena originale: [catene di MODELLER]}, excluded, modified, breaks, hydrogens_removed, residues_count."""
    chains = _protein_chains(model)
    if not chains:
        raise AppError("La struttura non contiene residui amminoacidici con C-alfa.", code="invalid_mutation")
    used = {c.id for c in chains if len(c.id) == 1 and c.id.strip()}
    free = (ch for ch in CHAIN_LETTERS if ch not in used)
    lines: list[str] = []
    info: dict = {"chains": {}, "residues": {}, "groups": {}, "excluded": [], "modified": [], "breaks": [],
                  "hydrogens_removed": 0, "residues_count": 0}
    serial = 0
    for chain in model:
        prev = None
        current: Optional[str] = None
        for res in chain:
            name = res.get_resname().strip()
            letter = residue_letter(res) if is_amino_residue(res) else _modified_letter(res)
            if not (is_amino_residue(res) or letter) or name in WATER_NAMES or res.id[0] == "W":
                if res.id[0] != "W" and name not in WATER_NAMES:
                    info["excluded"].append(f"{name} {_chain_name(chain.id)}{_resid(res)}")
                continue
            if prev is not None and not refinement._linked(prev, res):
                info["breaks"].append({"chain": chain.id, "after": _resid(prev), "before": _resid(res)})
                current = None
            if current is None:
                first = chain.id not in info["groups"]
                keep = first and len(chain.id) == 1 and chain.id.strip()
                current = chain.id if keep else next(free, None)
                if current is None:
                    raise AppError("Troppe catene o interruzioni per MODELLER (al massimo 62 tratti).",
                                   code="invalid_mutation")
                if lines:
                    lines.append("TER")
                info["chains"][current] = chain.id
                info["groups"].setdefault(chain.id, []).append(current)
            resname = AMINO.get(letter or "", name) if res.id[0] != " " else name
            if res.id[0] != " ":
                info["modified"].append(f"{name} {_chain_name(chain.id)}{_resid(res)} → {resname}")
            for atom in res:
                atom = _first_altloc(atom)
                if (atom.element or "").upper() in ("H", "D"):
                    info["hydrogens_removed"] += 1
                    continue
                serial += 1
                lines.append(_pdb_line(serial, atom, resname, current, res))
            info["residues"][(chain.id, _resid(res))] = current
            info["residues_count"] += 1
            prev = res
    lines += ["TER", "END"]
    return "\n".join(lines) + "\n", info


def restore_chains(pdb_text: str, chains: dict[str, str]) -> tuple[str, list[str]]:
    """Riporta in un PDB scritto da MODELLER i nomi originali delle catene (`chains` di `modeller_input`): i tratti
    della stessa catena tornano una catena sola (senza TER fra loro). Le catene con un nome di più caratteri, che il
    formato PDB non ammette, restano con la lettera di MODELLER: restituisce anche quelle rinominate."""
    renamed = sorted({f"{orig}→{new}" for new, orig in chains.items() if len(orig) > 1})
    rows = pdb_text.splitlines()

    def target(line: str) -> Optional[str]:
        if line.startswith(("ATOM", "HETATM", "TER")) and len(line) > 21:
            new = line[21]
            orig = chains.get(new, new)
            return orig if len(orig) == 1 else new
        return None

    out: list[str] = []
    previous: Optional[str] = None  # catena (finale) dell'ultimo atomo scritto
    for i, line in enumerate(rows):
        if line.startswith("TER"):
            nxt = next((r for r in rows[i + 1:] if r.startswith(("ATOM", "HETATM"))), None)
            if nxt is not None and previous is not None and target(nxt) == previous:
                continue  # tratto successivo della stessa catena
        tgt = target(line)
        if line.startswith(("ATOM", "HETATM")):
            previous = tgt
        out.append(line if tgt is None or len(line) <= 21 else line[:21] + tgt + line[22:])
    return "\n".join(out) + "\n", renamed


def neighbourhood(model, sites: Iterable[tuple[str, str]], radius: float) -> list[tuple[str, str]]:
    """Residui amminoacidici con un atomo pesante entro `radius` Å da un atomo pesante dei residui `sites` (compresi),
    nell'ordine della struttura."""
    from Bio.PDB import NeighborSearch

    atoms = [a for a in model.get_atoms() if (a.element or "").upper() not in ("H", "D")
             and (is_amino_residue(a.get_parent()) or _modified_letter(a.get_parent()))]
    wanted = {(c, r) for c, r in sites}
    centre = [a for a in atoms if (a.get_parent().get_parent().id, _resid(a.get_parent())) in wanted]
    ns = NeighborSearch(atoms)
    near: set[tuple[str, str]] = set(wanted)
    for a in centre:
        for b in ns.search(a.coord, radius, level="A"):
            res = b.get_parent()
            near.add((res.get_parent().id, _resid(res)))
    return [(c.id, _resid(r)) for c in model for r in c if (c.id, _resid(r)) in near]


def neighbourhood_hetero(model, sites: Iterable[tuple[str, str]], radius: float) -> list[str]:
    """Gruppi eteroatomici (non acqua, non residui della catena) con un atomo entro `radius` Å dai residui `sites`:
    ligandi, cofattori e metalli che la copia per MODELLER esclude."""
    from Bio.PDB import NeighborSearch

    wanted = {(c, r) for c, r in sites}
    hetero = [a for a in model.get_atoms() if a.get_parent().id[0] not in (" ", "W")
              and a.get_parent().get_resname().strip() not in WATER_NAMES and not _modified_letter(a.get_parent())]
    if not hetero:
        return []
    ns = NeighborSearch(hetero)
    found: set[str] = set()
    for chain in model:
        for res in chain:
            if (chain.id, _resid(res)) not in wanted:
                continue
            for atom in res:
                for b in ns.search(atom.coord, radius, level="A"):
                    group = b.get_parent()
                    found.add(f"{group.get_resname().strip()} {chain_name(group.get_parent().id)}{_resid(group)}")
    return sorted(found)


def seeds(seed: int = SEED, count: int = MODELS) -> list[int]:
    """Semi dei modelli di una variante: consecutivi a partire da `seed`, sempre fra -50000 e -2 (limiti di MODELLER)."""
    seed = int(seed)
    if not -50000 <= seed <= -2:
        raise AppError("Il seme casuale di MODELLER deve essere compreso tra -50000 e -2.", code="invalid_mutation")
    step = -1 if seed - (count - 1) >= -50000 else 1
    return [seed + step * k for k in range(count)]


# ------------------------------------------------------------------------------------------ indicatori strutturali
def _element(atom) -> str:
    el = (atom.element or "").strip().upper()
    return el if el and el != "X" else atom.get_id().strip().lstrip("0123456789")[:1].upper()


def _sasa_by_atom(entity) -> dict[tuple[str, str], tuple[float, float]]:
    """SASA (Shrake & Rupley 1973, sonda 1,4 Å, 100 punti) per residuo: (totale, parte apolare C/S) in Å²."""
    from Bio.PDB.SASA import ShrakeRupley

    ShrakeRupley(probe_radius=1.4, n_points=100).compute(entity, level="A")
    out: dict[tuple[str, str], tuple[float, float]] = {}
    for atom in entity.get_atoms():
        res = atom.get_parent()
        key = (res.get_parent().id, _resid(res))
        total, apolar = out.get(key, (0.0, 0.0))
        value = float(atom.sasa)
        out[key] = (total + value, apolar + (value if _element(atom) in ("C", "S") else 0.0))
    return out


def _protein(model):
    """Copia con i soli amminoacidi senza idrogeni (come le interfacce)."""
    from .interfaces import protein_copy

    return protein_copy(model)


def _reference_sasa(model, chain_id: str, resnum: str) -> tuple[float, float]:
    """SASA del residuo nel tripeptide i−1..i+1 isolato, nella conformazione della struttura: lo stato di riferimento
    per la superficie sepolta (come il Gly-X-Gly di Lee & Richards, con i vicini veri)."""
    from Bio.PDB.Chain import Chain
    from Bio.PDB.Model import Model

    chain = model[chain_id]
    residues = [r for r in chain if is_amino_residue(r)]
    idx = next((i for i, r in enumerate(residues) if _resid(r) == resnum), None)
    if idx is None:
        return 0.0, 0.0
    window = [residues[idx]]
    if idx > 0 and refinement._linked(residues[idx - 1], residues[idx]):
        window.insert(0, residues[idx - 1])
    if idx + 1 < len(residues) and refinement._linked(residues[idx], residues[idx + 1]):
        window.append(residues[idx + 1])
    tiny = Model(0)
    copy = Chain(chain_id)
    tiny.add(copy)
    for res in window:
        dup = res.copy()
        for atom in list(dup):
            if _element(atom) in ("H", "D"):
                dup.detach_child(atom.get_id())
        copy.add(dup)
    return _sasa_by_atom(tiny).get((chain_id, resnum), (0.0, 0.0))


def _ss_positions(model) -> dict[tuple[str, str], dict]:
    """Struttura secondaria (DSSP integrato, 8 stati) con la posizione nel segmento di elica o filamento."""
    from . import secondary

    rows = secondary.assign(model)
    out: dict[tuple[str, str], dict] = {}
    i = 0
    while i < len(rows):
        code = rows[i]["ss"]
        j = i
        while j + 1 < len(rows) and rows[j + 1]["ss"] == code and rows[j + 1]["chain"] == rows[i]["chain"]:
            j += 1
        for k in range(i, j + 1):
            out[(rows[k]["chain"], rows[k]["resnum"])] = {"ss": code, "position": k - i, "length": j - i + 1}
        i = j + 1
    return out


def _site_interactions(data: dict, site: tuple[str, str]) -> dict[str, dict]:
    """Interazioni con un'estremità nel residuo `site`, per chiave confrontabile fra selvatico e mutante: tipo, residuo
    partner e, per i legami idrogeno, se l'estremità nel sito è della catena principale o laterale."""
    from .interactions import BACKBONE

    out: dict[str, dict] = {}

    def add(kind: str, here: dict, there: dict, entry: dict, role: str = "") -> None:
        key = f"{kind}|{there['chain']}:{there['resnum']}|{role}"
        if key not in out:
            out[key] = {"type": kind, "partner": f"{there['resname']} {_chain_name(there['chain'])}{there['resnum']}",
                        "role": role, "atom": here.get("atom"), "inter_chain": bool(entry.get("inter_chain")),
                        "distance": entry.get("distance")}

    for e in data.get("hbonds") or []:
        for here, there in ((e["a"], e["b"]), (e["b"], e["a"])):
            if (here["chain"], here["resnum"]) == site and (there["chain"], there["resnum"]) != site:
                role = "catena principale" if here["atom"] in BACKBONE else "catena laterale"
                if here["atom"] == "N" and here is e["a"]:
                    role = "NH della catena principale"
                add("hbond", here, there, e, role)
    for kind, field in (("salt_bridge", "salt_bridges"), ("disulfide", "disulfides"), ("aromatic", "aromatic")):
        for e in data.get(field) or []:
            for here, there in ((e["a"], e["b"]), (e["b"], e["a"])):
                if (here["chain"], here["resnum"]) == site and (there["chain"], there["resnum"]) != site:
                    add(kind, here, there, e, e.get("type", "") if kind == "aromatic" else "")
    return out


def _clashes_at(clash: dict, site: tuple[str, str]) -> list[dict]:
    prefix = f"{site[0]} {site[1]} "
    return [c for c in clash.get("clashes") or [] if c["atom1"].startswith(prefix) or c["atom2"].startswith(prefix)]


@dataclass
class ModelFacts:
    """Fatti di una struttura (selvatico ottimizzato o mutante) calcolati una volta: SASA, interazioni, clash, struttura
    secondaria e diedri."""
    model: object
    sasa: dict
    interactions: dict
    clashes: dict
    ss: dict
    dihedrals: dict


def facts(model) -> ModelFacts:
    from . import clashes, interactions

    protein = _protein(model)
    dihedrals = {(r["chain"], r["resnum"]): r for r in ramachandran.backbone_dihedrals(protein)}
    return ModelFacts(model=protein, sasa=_sasa_by_atom(protein), interactions=interactions.analyze(protein),
                      clashes=clashes.analyze(protein, max_listed=100000), ss=_ss_positions(protein),
                      dihedrals=dihedrals)


def _rsa(sasa: float, letter: str, max_asa: dict) -> Optional[float]:
    ref = max_asa.get(letter)
    return round(min(1.5, sasa / ref), 3) if ref else None


def site_indicators(mutation: Mutation, wt: ModelFacts, mut: ModelFacts, max_asa: dict,
                    interface: Optional[dict] = None) -> dict:
    """Indicatori strutturali di una sostituzione (selvatico ottimizzato contro mutante, stessi residui).

    `max_asa`: ASA massime per la RSA (Tien et al. 2013, `engines/quality.MAX_ASA`); `interface`: riga del residuo
    nelle interfacce della struttura (`domain/interfaces.analyze`, campi delta e sasa) o None."""
    site = (mutation.chain, mutation.resnum)
    w_total, w_apolar = wt.sasa.get(site, (0.0, 0.0))
    m_total, m_apolar = mut.sasa.get(site, (0.0, 0.0))
    rw_total, rw_apolar = _reference_sasa(wt.model, *site)
    rm_total, rm_apolar = _reference_sasa(mut.model, *site)
    buried_wt = {"total": round(rw_total - w_total, 1), "apolar": round(rw_apolar - w_apolar, 1)}
    buried_mut = {"total": round(rm_total - m_total, 1), "apolar": round(rm_apolar - m_apolar, 1)}
    rsa_wt = _rsa(w_total, mutation.wt, max_asa)
    rsa_mut = _rsa(m_total, mutation.mut, max_asa)
    before = _site_interactions(wt.interactions, site)
    after = _site_interactions(mut.interactions, site)
    lost = [v for k, v in before.items() if k not in after]
    gained = [v for k, v in after.items() if k not in before]
    clash_wt = _clashes_at(wt.clashes, site)
    clash_mut = _clashes_at(mut.clashes, site)
    ss = wt.ss.get(site) or {"ss": "-", "position": 0, "length": 1}
    dih = wt.dihedrals.get(site) or {}
    rama = None
    if dih.get("phi") is not None and dih.get("psi") is not None:
        before_class = ramachandran.residue_class(AMINO[mutation.wt], dih.get("next_resname"))
        after_class = ramachandran.residue_class(AMINO[mutation.mut], dih.get("next_resname"))
        rama = {"phi": dih["phi"], "psi": dih["psi"],
                "wt": ramachandran.classify(dih["phi"], dih["psi"], before_class),
                "mut": ramachandran.classify(dih["phi"], dih["psi"], after_class)}
    out = {
        "site": mutation.site, "label": mutation.label, "wt": mutation.wt, "mut": mutation.mut,
        "rsa_wt": rsa_wt, "rsa_mut": rsa_mut, "core": rsa_wt is not None and rsa_wt < CORE_RSA,
        "buried_wt": buried_wt, "buried_mut": buried_mut,
        "buried_delta": {"total": round(buried_mut["total"] - buried_wt["total"], 1),
                         "apolar": round(buried_mut["apolar"] - buried_wt["apolar"], 1)},
        "ss": ss["ss"], "ss_position": ss["position"], "ss_length": ss["length"], "rama": rama,
        "blosum62": blosum62(mutation.wt, mutation.mut),
        "helix_propensity": (round(HELIX_PROPENSITY[mutation.mut] - HELIX_PROPENSITY[mutation.wt], 2)
                             if ss["ss"] in HELIX_CODES else None),
        "interactions": {"wild_type": len(before), "mutant": len(after), "lost": lost, "gained": gained},
        "clashes": {"wild_type": len(clash_wt), "mutant": len(clash_mut),
                    "worst": max((c["overlap"] for c in clash_mut), default=None)},
        "interface": None,
    }
    if interface is not None and float(interface.get("delta") or 0.0) > 1.0:
        sasa_complex = float(interface.get("sasa") or 0.0)
        out["interface"] = {"delta": interface.get("delta"),
                            "rsa_complex": _rsa(sasa_complex, mutation.wt, max_asa),
                            "lost_inter_chain": sum(1 for x in lost if x["inter_chain"]
                                                    and x["type"] in ("hbond", "salt_bridge"))}
    return out


# ------------------------------------------------------------------------------------------ classificazione
def _signal(level: str, code: str, text: str) -> dict:
    return {"level": level, "code": code, "text": text}


def _n(value: float, digits: int = 1) -> str:
    return f"{value:.{digits}f}".replace(".", ",").replace("-", "−")


def site_signals(ind: dict) -> list[dict]:
    """Segnali di un sito (livelli "strong", "moderate", "info", "gain") dagli indicatori di `site_indicators`."""
    out: list[dict] = []
    wt, mut = ind["wt"], ind["mut"]
    core, rsa = ind["core"], ind["rsa_wt"]
    where = (f"sepolto (RSA {_n(100 * rsa, 0)}%)" if core else f"esposto (RSA {_n(100 * rsa, 0)}%)") \
        if rsa is not None else "esposizione non calcolata"
    # struttura secondaria
    if ind["ss"] in HELIX_CODES:
        dp = ind["helix_propensity"] or 0.0
        if mut == "P" and ind["ss_position"] >= HELIX_FIRST_TURN:
            out.append(_signal("strong", "proline_helix",
                               f"Prolina in un'α-elica oltre il primo giro (posizione {ind['ss_position'] + 1} di "
                               f"{ind['ss_length']}): rompe l'elica, +{_n(dp, 2)} kcal/mol di propensione rispetto a "
                               f"{AMINO[wt]} (Pace & Scholtz 1998)."))
        elif mut == "P":
            out.append(_signal("info", "proline_helix_start",
                               f"Prolina nel primo giro dell'α-elica (posizione {ind['ss_position'] + 1}): posizione in "
                               "cui la prolina è comune (MacArthur & Thornton 1991)."))
        elif dp >= HELIX_MODERATE and 0 < ind["ss_position"] < ind["ss_length"] - 1:
            out.append(_signal("moderate", "helix_propensity",
                               f"Nell'α-elica {AMINO[mut]} ha una propensione più bassa di {AMINO[wt]}: "
                               f"+{_n(dp, 2)} kcal/mol (Pace & Scholtz 1998)."))
    elif ind["ss"] in STRAND_CODES and mut in ("P", "G"):
        nh_bonded = any(x["type"] == "hbond" and x["role"] == "NH della catena principale"
                        for x in ind["interactions"]["lost"])
        if mut == "P" and nh_bonded:
            out.append(_signal("strong", "proline_strand",
                               "Prolina in un filamento β: perde il legame idrogeno dell'NH della catena principale con "
                               "il filamento vicino."))
        else:
            out.append(_signal("moderate", "strand_breaker",
                               f"{AMINO[mut]} in un filamento β: propensione β fra le più basse (Minor & Kim 1994; "
                               "Smith, Withka & Regan 1994)."))
    rama = ind.get("rama")
    if rama and rama["mut"] == "outlier" and rama["wt"] != "outlier":
        out.append(_signal("strong", "backbone_conformation",
                           f"La conformazione della catena principale (φ {_n(rama['phi'], 0)}°, ψ {_n(rama['psi'], 0)}°) è "
                           f"{'favorita' if rama['wt'] == 'favored' else 'consentita'} per {AMINO[wt]} ma non per "
                           f"{AMINO[mut]} (regioni di Ramachandran, Lovell et al. 2003)."))
    # interazioni perse
    lost = ind["interactions"]["lost"]
    ss_lost = [x for x in lost if x["type"] == "disulfide"]
    if ss_lost:
        out.append(_signal("strong", "disulfide_lost",
                           f"Ponte disolfuro perso con {', '.join(x['partner'] for x in ss_lost)} (Betz 1993)."))
    hb = [x for x in lost if x["type"] == "hbond"]
    if hb:
        level = "moderate" if (core and len(hb) >= 1) or len(hb) >= 2 else "info"
        partners = "; ".join(x["partner"] + ", " + x["role"] for x in hb[:4])
        out.append(_signal(level, "hbonds_lost",
                           f"{len(hb)} {'legame idrogeno perso' if len(hb) == 1 else 'legami idrogeno persi'} "
                           f"({partners}): circa 1 kcal/mol ciascuno se sepolti (Myers & Pace 1996); residuo {where}."))
    sb = [x for x in lost if x["type"] == "salt_bridge"]
    if sb:
        out.append(_signal("moderate" if core else "info", "salt_bridge_lost",
                           f"Ponte salino perso con {', '.join(x['partner'] for x in sb)}"
                           + ("." if core else ": in superficie il contributo è di solito piccolo (Strop & Mayo 2000).")))
    # superficie sepolta e nucleo
    apolar_loss = -float(ind["buried_delta"]["apolar"])
    if apolar_loss >= APOLAR_LOSS:
        out.append(_signal("moderate", "buried_apolar_lost",
                           f"{_n(apolar_loss, 0)} Å² di superficie apolare sepolta in meno: circa "
                           f"{_n(apolar_loss * HYDROPHOBIC_CAL_A2 / 1000, 1)} kcal/mol di effetto idrofobico a 25 "
                           "cal·mol⁻¹·Å⁻² (Chothia 1974)."))
    if core and ind["blosum62"] < 0:
        out.append(_signal("moderate", "core_nonconservative",
                           f"Residuo nel nucleo (RSA {_n(100 * rsa, 0)}%) con una sostituzione non conservativa "
                           f"(BLOSUM62 {AMINO[wt]}→{AMINO[mut]} = {_n(ind['blosum62'], 0)})."))
    if core and mut in CHARGED and wt not in CHARGED:
        out.append(_signal("moderate", "buried_charge",
                           f"Carica introdotta nel nucleo ({AMINO[mut]}): una carica sepolta è di solito destabilizzante "
                           "(Isom et al. 2011)."))
    new_clashes = ind["clashes"]["mutant"] - ind["clashes"]["wild_type"]
    if new_clashes > 0:
        out.append(_signal("moderate", "steric_clash",
                           f"{new_clashes} {'contatto sterico grave' if new_clashes == 1 else 'contatti sterici gravi'} "
                           f"del residuo mutato dopo l'ottimizzazione (sovrapposizione fino a "
                           f"{_n(ind['clashes']['worst'] or 0, 2)} Å): la catena laterale non trova spazio."))
    gained = [x for x in ind["interactions"]["gained"] if x["type"] in ("hbond", "salt_bridge", "disulfide")]
    if gained:
        names = {"hbond": "legame idrogeno", "salt_bridge": "ponte salino", "disulfide": "ponte disolfuro"}
        listed = "; ".join(x["partner"] + " (" + names[x["type"]] + ")" for x in gained[:4])
        # in superficie un'interazione nuova compete con l'acqua: solo informativa
        out.append(_signal("gain" if core else "info", "interactions_gained", f"Interazioni nuove: {listed}."))
    return out


def dope_signal(delta_eq: Optional[float], delta: Optional[float]) -> Optional[dict]:
    if delta_eq is None or delta is None:
        return None
    text = (f"ΔDOPE {'+' if delta > 0 else ''}{_n(delta, 0)} ({'+' if delta_eq > 0 else ''}{_n(delta_eq, 2)} residui "
            "equivalenti) fra mutante e selvatico ottimizzati")
    if delta_eq >= DOPE_STRONG:
        return _signal("strong", "dope", text + ": contatti molto meno favorevoli (DOPE, Shen & Sali 2006).")
    if delta_eq >= DOPE_MODERATE:
        return _signal("moderate", "dope", text + ": contatti meno favorevoli.")
    if delta_eq <= DOPE_GAIN:
        return _signal("gain", "dope", text + ": contatti più favorevoli (un residuo più grande aggiunge contatti anche "
                                              "senza stabilizzare).")
    return _signal("info", "dope", text + ": nessuna differenza rilevante.")


def energy_signal(energy: Optional[dict]) -> Optional[dict]:
    if not energy or energy.get("ddE") is None:
        return None
    dde = float(energy["ddE"])
    text = (f"ΔΔE {'+' if dde > 0 else ''}{_n(dde, 0)} kJ/mol ({'+' if dde > 0 else ''}{_n(dde / KCAL, 1)} kcal/mol) "
            "dall'energia del campo di forza con il riferimento del tripeptide")
    if dde >= ENERGY_STRONG:
        return _signal("strong", "energy", text + ": molto sfavorevole.")
    if dde >= ENERGY_MODERATE:
        return _signal("moderate", "energy", text + ": sfavorevole.")
    if dde <= ENERGY_GAIN:
        return _signal("gain", "energy", text + ": favorevole.")
    return _signal("info", "energy", text + ": entro l'incertezza della minimizzazione.")


def classify(sites: list[dict], dope: Optional[dict] = None, energy: Optional[dict] = None) -> dict:
    """Classe qualitativa della variante con le motivazioni, dai segnali dei siti, del DOPE e dell'energia.

    `dope`: {delta, delta_eq, interchain_delta_eq}; `energy`: {ddE} o None. Restituisce code, label, reasons (testi dei
    segnali in ordine di peso), signals, score (chiave di ordinamento: 100 per segnale forte, 10 per moderato, più i
    residui equivalenti di ΔDOPE; non è un ΔΔG) e binding (effetto sul legame dei siti d'interfaccia)."""
    signals: list[dict] = []
    for ind in sites:
        for s in site_signals(ind):
            signals.append({**s, "site": ind["label"]} if len(sites) > 1 else s)
    for extra in (dope_signal((dope or {}).get("delta_eq"), (dope or {}).get("delta")), energy_signal(energy)):
        if extra is not None:
            signals.append(extra)
    strong = [s for s in signals if s["level"] == "strong"]
    moderate = [s for s in signals if s["level"] == "moderate"]
    gains = [s for s in signals if s["level"] == "gain"]
    core = any(ind["core"] for ind in sites)
    if strong or len(moderate) >= 2:
        code = "destabilizing"
        lead = (f"{len(strong)} {'segnale forte' if len(strong) == 1 else 'segnali forti'}" if strong else
                f"{len(moderate)} segnali moderati")
        summary = f"Probabilmente destabilizzante: {lead}."
    elif moderate:
        code = "uncertain"
        summary = "Effetto incerto: un solo segnale moderato."
    elif core:
        code = "uncertain"
        summary = ("Effetto incerto: residuo nel nucleo senza segnali chiari; una sostituzione sepolta può comunque "
                   "cambiare l'impaccamento in modo che questi indicatori non vedono.")
    elif gains:
        code = "uncertain"
        summary = "Effetto incerto: solo segnali di possibile stabilizzazione, da verificare."
    else:
        code = "neutral"
        summary = "Probabilmente neutra: residuo esposto, nessun segnale di destabilizzazione."
    rank = {"strong": 0, "moderate": 1, "gain": 2, "info": 3}
    ordered = sorted(signals, key=lambda s: rank.get(s["level"], 9))
    delta_eq = (dope or {}).get("delta_eq")
    score = 100 * len(strong) + 10 * len(moderate) + (float(delta_eq) if delta_eq is not None else 0.0)

    def reason(s: dict) -> str:  # con più siti il motivo dice di quale sito è
        return f"{s['site']}: {s['text']}" if s.get("site") else s["text"]

    return {"code": code, "label": CLASSES[code], "summary": summary,
            "reasons": [reason(s) for s in ordered if s["level"] != "info"] or [reason(s) for s in ordered],
            "signals": ordered, "score": round(score, 2), "binding": binding_effect(sites, dope)}


def binding_effect(sites: list[dict], dope: Optional[dict] = None) -> Optional[dict]:
    """Effetto sul legame fra catene dei siti d'interfaccia (separato dalla stabilità), None se nessun sito è
    d'interfaccia. Hot spot *probabile*: sepolto nel complesso (RSA < 20%) e con contributo al legame (ΔDOPE fra le
    catene ≥ 0,5 residui equivalenti o interazioni fra catene perse); *possibile*: il solo contributo."""
    at = [s for s in sites if s.get("interface")]
    if not at:
        return None
    inter_eq = (dope or {}).get("interchain_delta_eq")
    lost = sum(s["interface"]["lost_inter_chain"] for s in at)
    contributes = (inter_eq is not None and inter_eq >= HOTSPOT_DOPE) or lost > 0
    buried = any((s["interface"].get("rsa_complex") or 1.0) < CORE_RSA for s in at)
    reasons = []
    if inter_eq is not None:
        reasons.append(f"ΔDOPE fra le catene {'+' if inter_eq > 0 else ''}{_n(inter_eq, 2)} residui equivalenti")
    if lost:
        reasons.append(f"{lost} {'interazione' if lost == 1 else 'interazioni'} fra catene perse")
    if buried:
        reasons.append("residuo sepolto nel complesso (RSA < 20%)")
    level = "probabile" if contributes and buried else "possibile" if contributes else None
    text = ("Residuo d'interfaccia: " + ("; ".join(reasons) if reasons else "nessun contributo evidente al legame")
            + (f". Hot spot {level} (Bogan & Thorn 1998)." if level else "."))
    return {"interface": True, "hotspot": level, "text": text, "interchain_delta_eq": inter_eq,
            "lost_inter_chain": lost}


# ------------------------------------------------------------------------------------------ DOPE ed energia
def dope_scale(dope_wt: float, residues: int) -> Optional[float]:
    """DOPE medio per residuo del selvatico (valore assoluto): l'unità dei "residui equivalenti"."""
    if not residues or not dope_wt:
        return None
    return abs(float(dope_wt)) / int(residues)


def dope_summary(best_wt: dict, best_mut: dict, residues: int, local_residues: int,
                 models: Optional[list[dict]] = None) -> dict:
    """ΔDOPE totale, locale (coppie con un atomo entro LOCAL_RADIUS Å dai siti) e fra le catene del modello migliore di
    ogni variante, con lo scarto fra i modelli della stessa variante (rumore del protocollo)."""
    scale = dope_scale(best_wt["dope"], residues)
    delta = round(float(best_mut["dope"]) - float(best_wt["dope"]), 1)
    local = None
    if best_wt.get("local") is not None and best_mut.get("local") is not None:
        local = round(float(best_mut["local"]) - float(best_wt["local"]), 1)
    inter_w, inter_m = best_wt.get("interchain"), best_mut.get("interchain")
    inter_delta = None if inter_w is None or inter_m is None else round(float(inter_m) - float(inter_w), 1)
    spread = {}
    for variant in ("wild_type", "mutant"):
        values = [float(m["dope"]) for m in models or [] if m.get("role") == variant and m.get("dope") is not None]
        if values:
            spread[variant] = round(max(values) - min(values), 1)
    return {
        "wild_type": round(float(best_wt["dope"]), 1), "mutant": round(float(best_mut["dope"]), 1), "delta": delta,
        "local_wild_type": None if best_wt.get("local") is None else round(float(best_wt["local"]), 1),
        "local_mutant": None if best_mut.get("local") is None else round(float(best_mut["local"]), 1),
        "delta_local": local, "local_residues": local_residues, "local_radius": LOCAL_RADIUS,
        "per_residue": None if scale is None else round(scale, 2),
        "delta_eq": None if scale is None else round(delta / scale, 2),
        "interchain_wild_type": None if inter_w is None else round(float(inter_w), 1),
        "interchain_mutant": None if inter_m is None else round(float(inter_m), 1),
        "interchain_delta": inter_delta,
        "interchain_delta_eq": None if scale is None or inter_delta is None else round(inter_delta / scale, 2),
        "spread": spread,
    }


def fragment_windows(model, sites: list[tuple[str, str]]) -> list[list[tuple[str, str]]]:
    """Residui i−1..i+1 di ogni sito (legati al sito), con le finestre sovrapposte o adiacenti della stessa catena unite:
    i frammenti di riferimento dell'energia."""
    windows: list[list[tuple[str, str]]] = []
    for chain in model:
        residues = [r for r in chain if is_amino_residue(r)]
        ids = [_resid(r) for r in residues]
        marks = [False] * len(residues)
        for c, rnum in sites:
            if c != chain.id or rnum not in ids:
                continue
            k = ids.index(rnum)
            marks[k] = True
            if k > 0 and refinement._linked(residues[k - 1], residues[k]):
                marks[k - 1] = True
            if k + 1 < len(residues) and refinement._linked(residues[k], residues[k + 1]):
                marks[k + 1] = True
        current: list[tuple[str, str]] = []
        for k, flag in enumerate(marks):
            linked = k > 0 and refinement._linked(residues[k - 1], residues[k])
            if flag and current and linked:
                current.append((chain.id, ids[k]))
            elif flag:
                if current:
                    windows.append(current)
                current = [(chain.id, ids[k])]
            elif current:
                windows.append(current)
                current = []
        if current:
            windows.append(current)
    return windows


def fragments_pdb(model, sites: list[tuple[str, str]]) -> str:
    """PDB dei frammenti di riferimento (finestre di `fragment_windows`, soli atomi pesanti), ognuno spostato di
    FRAGMENT_SPACING Å lungo x: nello stesso calcolo di OpenMM non interagiscono (a 1000 Å, con lo schermo del solvente
    implicito, meno di 0,1 kJ/mol fra due cariche unitarie)."""
    rows: list[str] = []
    serial = 0
    for k, window in enumerate(fragment_windows(model, sites)):
        shift = np.array([FRAGMENT_SPACING * (k + 1), 0.0, 0.0])
        for chain_id, resnum in window:
            res = find_residue(model[chain_id], resnum)
            for atom in res:
                atom = _first_altloc(atom)
                if _element(atom) in ("H", "D"):
                    continue
                serial += 1
                line = _pdb_line(serial, atom, res.get_resname().strip(), chain_id if len(chain_id) == 1 else "A", res)
                x, y, z = (float(v) for v in atom.coord + shift)
                rows.append(line[:30] + f"{x:8.3f}{y:8.3f}{z:8.3f}" + line[54:])
        rows.append("TER")
    return "\n".join(rows + ["END"]) + "\n"


def energy_terms(e_wt: Optional[float], e_mut: Optional[float], ref_wt: Optional[float],
                 ref_mut: Optional[float]) -> dict:
    """ΔΔE = (E_mut − E_wt) − (E_rif,mut − E_rif,wt) in kJ/mol: il riferimento (tripeptidi isolati) toglie la parte
    dell'energia che dipende solo dalla composizione (atomi diversi), che da sola renderebbe incomparabili le due
    energie. Resta un'energia, non un'energia libera: niente entropia né rilassamento della catena principale."""
    folded = None if e_wt is None or e_mut is None else round(e_mut - e_wt, 1)
    reference = None if ref_wt is None or ref_mut is None else round(ref_mut - ref_wt, 1)
    dde = None if folded is None or reference is None else round(folded - reference, 1)
    return {"wild_type": e_wt, "mutant": e_mut, "reference_wild_type": ref_wt, "reference_mutant": ref_mut,
            "delta_folded": folded, "delta_reference": reference, "ddE": dde,
            "ddE_kcal": None if dde is None else round(dde / KCAL, 1), "unit": "kJ/mol"}


def backbone_shift(wt_model, mut_model, moved: set[tuple[str, str]]) -> dict:
    """RMSD della catena principale fra selvatico e mutante fuori dai residui `moved` (i siti e le catene laterali
    ottimizzate: lì la catena principale è ferma) e su tutti i residui."""
    a = refinement.backbone_coordinates(wt_model)
    b = refinement.backbone_coordinates(mut_model)
    common = [k for k in a if k in b]
    outside = [k for k in common if (k[0], k[1]) not in moved]

    def rmsd(keys: list) -> Optional[float]:
        if not keys:
            return None
        xa = np.array([a[k] for k in keys])
        xb = np.array([b[k] for k in keys])
        return round(float(np.sqrt(((xa - xb) ** 2).sum(axis=1).mean())), 3)

    return {"outside": rmsd(outside), "outside_atoms": len(outside), "all": rmsd(common), "atoms": len(common)}


# ------------------------------------------------------------------------------------------ scansione di alanina
_RESIDUE = re.compile(r"^(?:(?P<chain>[A-Za-z0-9_]{1,4}):)?(?P<aa>[A-Za-z]{1,3})?(?P<start>-?\d+)(?P<icode>[A-Za-z]?)"
                      r"(?:-(?P<end>-?\d+))?$")


def parse_residues(text: str, model) -> list[tuple[str, str]]:
    """Residui di una scansione dall'elenco: "A:30", "30" (una sola catena), "A:I30" (con la verifica del residuo),
    "A:28-34" (intervallo), separati da virgole o spazi."""
    tokens = [t for t in _SEPARATORS.split(str(text or "").strip()) if t]
    if not tokens:
        raise AppError("Indica i residui da scandire (es. A:30, A:43, A:45-48).", code="invalid_scan")
    out: list[tuple[str, str]] = []
    for token in tokens:
        m = _RESIDUE.match(token)
        if not m:
            raise AppError(f"Residuo non valido: «{token}» (es. A:30, A:I30, A:28-34).", code="invalid_scan")
        try:
            chain = _chain_for(model, m.group("chain"), token)
        except AppError as exc:
            raise AppError(exc.message, code="invalid_scan")
        residues = [r for r in chain if is_amino_residue(r) or _modified_letter(r)]
        if m.group("end") is not None:
            lo, hi = sorted((int(m.group("start")), int(m.group("end"))))
            picked = [r for r in residues if lo <= r.id[1] <= hi]
            if not picked:
                raise AppError(f"Nessun residuo fra {lo} e {hi} nella catena {_chain_name(chain.id)}.",
                               code="invalid_scan")
        else:
            res = find_residue(chain, f"{int(m.group('start'))}{m.group('icode') or ''}")
            if res is None or not (is_amino_residue(res) or _modified_letter(res)):
                raise AppError(f"Residuo {m.group('start')}{m.group('icode') or ''} non presente nella catena "
                               f"{_chain_name(chain.id)} ({_range_text(chain)}).", code="invalid_scan")
            aa = (m.group("aa") or "").upper()
            if aa:
                letter = THREE.get(aa) if len(aa) == 3 else aa
                if letter != residue_letter(res):
                    raise AppError(f"«{token}»: {_chain_name(chain.id)}{_resid(res)} è {res.get_resname().strip()}, non "
                                   f"{aa}.", code="invalid_scan")
            picked = [res]
        for res in picked:
            key = (chain.id, _resid(res))
            if key not in out:
                out.append(key)
    return out


def scan_plan(model, residues: list[tuple[str, str]], *, energy: bool = False) -> tuple[list[Mutation], list[dict]]:
    """Mutazioni in alanina dei residui scelti e residui esclusi con il motivo (glicina, prolina, alanina, non
    standard). Oltre il limite (`MAX_SCAN`, `MAX_SCAN_ENERGY` con l'energia) un errore con il conteggio."""
    mutations: list[Mutation] = []
    skipped: list[dict] = []
    reasons = {"G": "glicina: la sostituzione aggiunge una catena laterale e irrigidisce la catena principale "
                    "(esclusa, come in Kortemme & Baker 2002)",
               "P": "prolina: la sostituzione libera la catena principale (esclusa, come in Kortemme & Baker 2002)",
               "A": "già alanina"}
    for chain_id, resnum in residues:
        res = find_residue(model[chain_id], resnum)
        letter = residue_letter(res) if res is not None else "X"
        row = {"chain": chain_id, "resnum": resnum, "resname": res.get_resname().strip() if res is not None else "?"}
        if letter in reasons:
            skipped.append({**row, "reason": reasons[letter]})
        elif letter not in AMINO:
            skipped.append({**row, "reason": "residuo non standard"})
        else:
            mutations.append(Mutation(chain_id, resnum, letter, "A"))
    limit = MAX_SCAN_ENERGY if energy else MAX_SCAN
    if not mutations:
        raise AppError("Nessun residuo da scandire: " + ("; ".join(f"{_chain_name(s['chain'])}{s['resnum']} "
                                                                     f"{s['reason']}" for s in skipped[:5])
                                                         or "elenco vuoto") + ".", code="invalid_scan")
    if len(mutations) > limit:
        raise AppError(f"Troppi residui per la scansione: {len(mutations)} (al massimo {limit}"
                       + (" con l'energia di OpenMM, che richiede 4 minimizzazioni per residuo" if energy else "")
                       + "; ogni residuo costruisce 6 modelli con MODELLER). Scegli una catena, una tasca o un elenco.",
                       code="invalid_scan")
    return mutations, skipped


def scan_order(rows: list[dict]) -> list[dict]:
    """Righe della scansione dalla più destabilizzante: classe, poi punteggio, poi ΔDOPE (residui equivalenti)."""
    rank = {"destabilizing": 0, "uncertain": 1, "neutral": 2}
    return sorted(rows, key=lambda r: (rank.get((r.get("assessment") or {}).get("code"), 3),
                                       -float((r.get("assessment") or {}).get("score") or 0.0),
                                       -float(((r.get("dope") or {}).get("delta_eq")) or 0.0)))


# ------------------------------------------------------------------------------------------ varianti di UniProt
MIN_MAP_IDENTITY = 30.0   # % : sotto, la struttura non è riconducibile alla proteina di UniProt
SAME_PROTEIN_IDENTITY = 90.0
# Ripetizioni (es. la poliubiquitina, tre copie identiche in UBB): altri tratti della voce allineati alla catena con
# un'identità entro REPEAT_TOLERANCE punti dal migliore e quasi la stessa copertura; al più MAX_REPEATS tratti.
REPEAT_TOLERANCE = 5.0
REPEAT_COVERAGE = 0.9
MAX_REPEATS = 20


def _placements(chain_seq: str, seq: str) -> list[tuple[dict[int, int], float, float, int, int]]:
    """Tratti della sequenza di UniProt allineati alla catena: il migliore (allineamento globale con gap terminali
    gratuiti) e, nelle parti di sequenza che restano fuori, le copie ripetute equivalenti. Per ognuno {indice nella
    catena: posizione in UniProt da 1}, identità, copertura, primo e ultimo residuo di UniProt."""
    from . import conservation

    out: list[tuple[dict[int, int], float, float, int, int]] = []
    queue = [(0, len(seq))]
    while queue and len(out) < MAX_REPEATS:
        a, b = queue.pop(0)
        if b - a < max(1, len(chain_seq) // 2):
            continue
        mapping, identity, coverage = conservation._map(chain_seq, seq[a:b])
        positions = {k: a + pos + 1 for k, pos in enumerate(mapping) if pos is not None}
        if not positions:
            continue
        if out and (identity < out[0][1] - REPEAT_TOLERANCE or coverage < REPEAT_COVERAGE * out[0][2]):
            continue
        lo, hi = min(positions.values()), max(positions.values())
        out.append((positions, identity, coverage, lo, hi))
        queue += [(a, lo - 1), (hi, b)]
    return out


def map_variants(variants: list[dict], uniprot_sequence: str, model, chain: Optional[str] = None) -> dict:
    """Varianti di UniProt (posizione sulla sequenza di UniProt) portate sulla numerazione della struttura.

    Per ogni catena proteica (o solo `chain`) la sequenza dei residui si allinea alla sequenza di UniProt
    (Needleman-Wunsch, BLOSUM62, gap terminali gratuiti, come la conservazione); si sceglie la catena con identità ×
    copertura più alta. Se la catena corrisponde a più tratti della voce (proteine con ripetizioni, come la
    poliubiquitina), le varianti di ogni tratto si portano sulla struttura: UniProt annota spesso la mutagenesi su una
    sola copia. Una variante è applicabile se la sua posizione corrisponde a un residuo della struttura con lo stesso
    amminoacido originale; altrimenti il motivo (fuori dalla struttura, residuo diverso)."""
    seq = "".join(c for c in str(uniprot_sequence or "").upper() if c.isalpha())
    if not seq:
        raise AppError("La voce di UniProt non ha una sequenza.", code="invalid_variants")
    chains = _protein_chains(model)
    if chain is not None:
        chains = [c for c in chains if _chain_name(c.id) == chain or c.id == chain]
        if not chains:
            raise AppError(f"Catena {chain} non presente nella struttura.", code="invalid_variants")
    best = None
    for c in chains:
        residues = [r for r in c if is_amino_residue(r) or _modified_letter(r)]
        found = _placements("".join(residue_letter(r) for r in residues), seq)
        if found and (best is None or found[0][1] * found[0][2] > best[2][0][1] * best[2][0][2]):
            best = (c, residues, found)
    if best is None:
        raise AppError("La struttura non corrisponde alla sequenza di UniProt: le varianti non si possono mappare.",
                       code="invalid_variants")
    c, residues, found = best
    identity, coverage = found[0][1], found[0][2]
    if identity < MIN_MAP_IDENTITY:
        raise AppError(f"La struttura non corrisponde alla sequenza di UniProt (identità {identity:.0f}% con la catena "
                       f"{_chain_name(c.id)}, minimo {MIN_MAP_IDENTITY:.0f}%): le varianti non si possono mappare.",
                       code="invalid_variants")
    found.sort(key=lambda p: p[3])  # tratti nell'ordine di UniProt
    by_position: dict[int, object] = {}
    segment_of: dict[int, int] = {}
    for n, (positions, _i, _c, _lo, _hi) in enumerate(found):
        for k, pos in positions.items():
            by_position[pos] = residues[k]
            segment_of[pos] = n + 1
    rows = []
    for v in variants:
        pos = int(v["position"])
        res = by_position.get(pos)
        row = {**v, "chain": c.id, "resnum": None, "structure_aa": None, "applicable": False, "notation": None,
               "reason": None, "segment": segment_of.get(pos) if len(found) > 1 else None}
        if res is None:
            row["reason"] = ("posizione fuori dalla sequenza di UniProt" if pos > len(seq)
                             else "posizione non presente nella struttura")
        else:
            letter = residue_letter(res)
            row.update(resnum=_resid(res), structure_aa=letter)
            if letter != v["original"]:
                row["reason"] = (f"nella struttura c'è {AMINO.get(letter, letter)} ({letter}), non "
                                 f"{AMINO.get(v['original'], v['original'])}")
            elif v["variation"] not in AMINO or v["variation"] == v["original"]:
                row["reason"] = "sostituzione non standard"
            else:
                row["applicable"] = True
                row["notation"] = Mutation(c.id, _resid(res), letter, v["variation"]).label
        rows.append(row)
    shift = None
    offsets = {pos - residues[k].id[1] for k, pos in found[0][0].items()}
    if len(found) == 1 and len(offsets) == 1:
        shift = next(iter(offsets))
    notes = []
    if identity < SAME_PROTEIN_IDENTITY:
        notes.append(f"Identità {identity:.0f}%: la struttura è un omologo, non la stessa proteina; le varianti sono "
                     "mappate per allineamento e il loro effetto può essere diverso.")
    if shift:
        notes.append(f"Numerazione di UniProt = numerazione della struttura {'+' if shift > 0 else '−'} {abs(shift)}.")
    if len(found) > 1:
        spans = ", ".join(f"{lo}–{hi}" for _p, _i, _c, lo, hi in found)
        notes.append(f"La catena corrisponde a {len(found)} tratti ripetuti della voce di UniProt ({spans}): le varianti "
                     "di ciascun tratto sono portate sulla struttura.")
    return {"chain": c.id, "identity": round(identity, 1), "coverage": round(coverage, 1),
            "offset": shift, "segments": [{"start": lo, "end": hi, "identity": round(i, 1)}
                                          for _p, i, _c, lo, hi in found],
            "variants": rows, "applicable": sum(1 for r in rows if r["applicable"]),
            "notes": notes, "method": "Needleman-Wunsch, BLOSUM62, gap 10/0,5 con gap terminali gratuiti"}
