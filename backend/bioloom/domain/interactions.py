"""Interazioni non covalenti e ponti disolfuro di una struttura, con criteri geometrici dichiarati (F7).

Logica pura (numpy + `NeighborSearch` di Biopython) su tutte le catene del primo modello. Contano i soli amminoacidi
(record ATOM con C-alfa; per gli atomi disordinati la variante scelta da Biopython, quella di occupanza maggiore):
acque, ligandi e ioni restano fuori (sono di F5.4). Ogni interazione ha le due estremità `a` e `b` (catena, residuo,
nome del residuo, atomo o gruppo), la distanza e i valori geometrici del suo criterio.

| Interazione | Criterio | Fonte |
|---|---|---|
| Legame idrogeno | D…A ≤ 3,5 Å e D…A–AA ≥ 90°; con l'idrogeno H…A ≤ 2,5 Å, D–H…A ≥ 120° e H…A–AA ≥ 90°; per i gruppi che ruotano, senza idrogeno, AA(D)–D…A ≥ 90° | Baker & Hubbard 1984; McDonald & Thornton 1994 (HBPLUS) |
| Ponte salino | un O carico di Asp/Glu (OD1, OD2, OE1, OE2) entro 4,0 Å da un N di Lys/Arg/His (NZ; NE, NH1, NH2; ND1, NE2) | Barlow & Thornton 1983 |
| π-stacking | centroidi degli anelli ≤ 5,5 Å e spostamento ≤ 2,0 Å; parallelo con angolo fra i piani ≤ 30°, a T con angolo ≥ 60° | McGaughey et al. 1998; Salentin et al. 2015 (PLIP) |
| Catione-π | NZ di Lys o CZ (centro del guanidinio) di Arg ≤ 6,0 Å dal centroide, spostamento ≤ 2,0 Å | Gallivan & Dougherty 1999; Salentin et al. 2015 |
| Ponte disolfuro | SG–SG 2,0 ± 0,2 Å (legame S–S tipico circa 2,04 Å); χ3 CB–SG–SG–CB riportato (vicino a ±90°) | Richardson 1981 |

D = donatore, A = accettore, AA = atomo antecedente (legato) dell'accettore o del donatore; spostamento = distanza
fra un centroide e la proiezione dell'altro (o del catione) sul piano dell'anello.

**Idrogeni.** Se il file ne contiene (modelli con idrogeni, strutture NMR) si usano quelli: un donatore senza idrogeni
non dona e un azoto di istidina protonato non accetta. Altrimenti si posizionano, a 1,0 Å, dove la geometria è fissa,
come fa HBPLUS: N–H dello scheletro come DSSP (opposto al C=O del residuo precedente, Kabsch & Sander 1983), NH
degli anelli di His e Trp e NE di Arg sulla bisettrice esterna, NH₂ di Asn, Gln e Arg nel piano del gruppo a 120°
dal legame C–N. Senza idrogeni entrambi gli azoti dell'istidina valgono da donatori e da accettori (il tautomero non
si conosce). Per i gruppi che ruotano (OH di Ser, Thr e Tyr, NH₃⁺ di Lys e dell'N-terminale) l'idrogeno non è
determinato e vale il criterio sugli atomi pesanti.

**Esclusioni dichiarate:** coppie nello stesso residuo; fra residui consecutivi legati dal legame peptidico le coppie
scheletro-scheletro (N–H…O=C del residuo precedente non è un legame idrogeno); atomi di zolfo come donatori o
accettori (legami deboli); estremità N e C delle catene nei ponti salini (la loro carica dipende dal costrutto); His
come catione nel catione-π (in maggioranza neutra a pH 7); le coppie di anelli con angolo fra 30° e 60°.

Una coppia di atomi entrambi donatori e accettori (Ser–Thr, His) compare una volta sola, nel verso con la geometria
migliore. Ponti salini e interazioni aromatiche sono uno per coppia di residui (la coppia di atomi o di anelli più
vicina, con il numero di coppie di atomi entro la soglia per i ponti salini).
"""

from __future__ import annotations

from typing import Optional

import numpy as np

from .structure import WATER_NAMES, _resid, is_amino_residue

VERSION = 1

# Legami idrogeno (Baker & Hubbard 1984; angoli degli antecedenti come HBPLUS, McDonald & Thornton 1994)
HBOND_DA_MAX = 3.5
HBOND_HA_MAX = 2.5
HBOND_DHA_MIN = 120.0
HBOND_ANTECEDENT_MIN = 90.0
H_BOND_LENGTH = 1.0  # N–H / O–H degli idrogeni posizionati (come DSSP)
H_ATTACH_MAX = 1.25  # un idrogeno del file appartiene all'atomo pesante del residuo entro questa distanza
# Ponti salini (Barlow & Thornton 1983)
SALT_BRIDGE_MAX = 4.0
# Anelli aromatici (McGaughey et al. 1998; Gallivan & Dougherty 1999; parametri di PLIP, Salentin et al. 2015)
PI_STACK_MAX = 5.5
PI_ANGLE_DEV = 30.0
PI_OFFSET_MAX = 2.0
CATION_PI_MAX = 6.0
# Ponti disolfuro
SS_DISTANCE = 2.0
SS_TOLERANCE = 0.2

BACKBONE = {"N", "CA", "C", "O", "OXT"}

# Donatori delle catene laterali: atomo → (antecedenti, geometria degli idrogeni da posizionare)
#   "bisector": un idrogeno sulla bisettrice esterna dei due antecedenti (sp2);
#   "planar2": due idrogeni nel piano (antecedente legato, atomo che fissa il piano) a 120° dal legame;
#   "rotor": gruppo che ruota, idrogeno non determinato (criterio sugli atomi pesanti).
SIDECHAIN_DONORS: dict[str, dict[str, tuple[tuple[str, ...], str]]] = {
    "ARG": {"NE": (("CD", "CZ"), "bisector"), "NH1": (("CZ", "NE"), "planar2"), "NH2": (("CZ", "NE"), "planar2")},
    "ASN": {"ND2": (("CG", "OD1"), "planar2")},
    "GLN": {"NE2": (("CD", "OE1"), "planar2")},
    "HIS": {"ND1": (("CG", "CE1"), "bisector"), "NE2": (("CD2", "CE1"), "bisector")},
    "LYS": {"NZ": (("CE",), "rotor")},
    "SER": {"OG": (("CB",), "rotor")},
    "THR": {"OG1": (("CB",), "rotor")},
    "TYR": {"OH": (("CZ",), "rotor")},
    "TRP": {"NE1": (("CD1", "CE2"), "bisector")},
}
# Accettori delle catene laterali: atomo → antecedenti (più di uno: la loro media, per gli N degli anelli)
SIDECHAIN_ACCEPTORS: dict[str, dict[str, tuple[str, ...]]] = {
    "ASP": {"OD1": ("CG",), "OD2": ("CG",)},
    "GLU": {"OE1": ("CD",), "OE2": ("CD",)},
    "ASN": {"OD1": ("CG",)},
    "GLN": {"OE1": ("CD",)},
    "HIS": {"ND1": ("CG", "CE1"), "NE2": ("CD2", "CE1")},
    "SER": {"OG": ("CB",)},
    "THR": {"OG1": ("CB",)},
    "TYR": {"OH": ("CZ",)},
}
ANIONIC = {"ASP": ("OD1", "OD2"), "GLU": ("OE1", "OE2")}
CATIONIC = {"LYS": ("NZ",), "ARG": ("NE", "NH1", "NH2"), "HIS": ("ND1", "NE2")}
# Anelli aromatici; il triptofano ha due anelli (pirrolico e benzenico), valutati separatamente come in PLIP
RINGS: dict[str, tuple[tuple[str, tuple[str, ...]], ...]] = {
    "PHE": (("anello", ("CG", "CD1", "CD2", "CE1", "CE2", "CZ")),),
    "TYR": (("anello", ("CG", "CD1", "CD2", "CE1", "CE2", "CZ")),),
    "HIS": (("anello", ("CG", "ND1", "CD2", "CE1", "NE2")),),
    "TRP": (("anello a 5", ("CG", "CD1", "NE1", "CE2", "CD2")), ("anello a 6", ("CD2", "CE2", "CE3", "CZ2", "CZ3", "CH2"))),
}
CATION_PI = {"LYS": "NZ", "ARG": "CZ"}

CRITERIA = {
    "hbond": {"da_max": HBOND_DA_MAX, "ha_max": HBOND_HA_MAX, "dha_min": HBOND_DHA_MIN,
              "antecedent_min": HBOND_ANTECEDENT_MIN, "source": "Baker & Hubbard 1984; McDonald & Thornton 1994"},
    "salt_bridge": {"max": SALT_BRIDGE_MAX, "source": "Barlow & Thornton 1983"},
    "pi_stacking": {"max": PI_STACK_MAX, "angle_dev": PI_ANGLE_DEV, "offset_max": PI_OFFSET_MAX,
                    "source": "McGaughey et al. 1998; Salentin et al. 2015"},
    "cation_pi": {"max": CATION_PI_MAX, "offset_max": PI_OFFSET_MAX,
                  "source": "Gallivan & Dougherty 1999; Salentin et al. 2015"},
    "disulfide": {"distance": SS_DISTANCE, "tolerance": SS_TOLERANCE, "source": "Richardson 1981"},
}


def _element(atom) -> str:
    el = (atom.element or "").strip().upper()
    if el and el != "X":
        return el
    return atom.get_id().strip().lstrip("0123456789")[:1].upper()


def _is_hydrogen(atom) -> bool:
    return _element(atom) in ("H", "D")


def _xyz(atom) -> np.ndarray:
    return np.asarray(atom.coord, dtype=float)


def _unit(v: np.ndarray) -> Optional[np.ndarray]:
    norm = float(np.linalg.norm(v))
    return v / norm if norm > 1e-6 else None


def _angle(a: np.ndarray, vertex: np.ndarray, b: np.ndarray) -> float:
    """Angolo a–vertice–b in gradi."""
    u, v = a - vertex, b - vertex
    denom = float(np.linalg.norm(u) * np.linalg.norm(v))
    if denom < 1e-9:
        return 0.0
    return float(np.degrees(np.arccos(np.clip(np.dot(u, v) / denom, -1.0, 1.0))))


def dihedral(p0: np.ndarray, p1: np.ndarray, p2: np.ndarray, p3: np.ndarray) -> float:
    """Diedro p0–p1–p2–p3 in gradi (−180, 180]."""
    b0, b1, b2 = p0 - p1, p2 - p1, p3 - p2
    b1n = b1 / np.linalg.norm(b1)
    v = b0 - np.dot(b0, b1n) * b1n
    w = b2 - np.dot(b2, b1n) * b1n
    x = np.dot(v, w)
    y = np.dot(np.cross(b1n, v), w)
    return float(np.degrees(np.arctan2(y, x)))


class _Residues:
    """Residui amminoacidici del modello, nell'ordine del file, con i legami peptidici fra consecutivi."""

    def __init__(self, model) -> None:
        self.items: list = []
        self.chain: list[str] = []
        self.index: dict[int, int] = {}  # id(residuo) → posizione
        self.bonded_next: set[int] = set()  # i: residuo i legato al residuo i+1 (C–N < 2,0 Å)
        for chain in model:
            prev = None
            for res in chain:
                if not is_amino_residue(res) or res.get_resname().strip() in WATER_NAMES:
                    prev = None
                    continue
                i = len(self.items)
                self.items.append(res)
                self.chain.append(chain.id)
                self.index[id(res)] = i
                if prev is not None and "C" in prev and "N" in res and float(prev["C"] - res["N"]) < 2.0:
                    self.bonded_next.add(i - 1)
                prev = res

    def end(self, i: int, atom: str) -> dict:
        res = self.items[i]
        return {"chain": self.chain[i], "resnum": _resid(res), "resname": res.get_resname().strip(), "atom": atom}

    def peptide_neighbours(self, i: int, j: int) -> bool:
        lo, hi = min(i, j), max(i, j)
        return hi == lo + 1 and lo in self.bonded_next


def _explicit_hydrogens(res, heavy) -> list[np.ndarray]:
    """Idrogeni del file legati all'atomo pesante `heavy` (stesso residuo, entro H_ATTACH_MAX)."""
    centre = _xyz(heavy)
    out = []
    for atom in res:
        if _is_hydrogen(atom):
            xyz = _xyz(atom)
            if float(np.linalg.norm(xyz - centre)) <= H_ATTACH_MAX:
                out.append(xyz)
    return out


def _placed_hydrogens(res, name: str, antecedents: tuple[str, ...], geometry: str) -> Optional[list[np.ndarray]]:
    """Idrogeni posizionati di un donatore a geometria fissa; None per i gruppi che ruotano (o atomi mancanti)."""
    if geometry == "rotor" or any(a not in res for a in antecedents):
        return None
    d = _xyz(res[name])
    if geometry == "bisector":
        u = _unit(d - np.mean([_xyz(res[a]) for a in antecedents], axis=0))
        return None if u is None else [d + H_BOND_LENGTH * u]
    # planar2: due idrogeni nel piano (C, N, X) a 120° dal legame C–N, cioè a 60° dal prolungamento del legame
    c, x = _xyz(res[antecedents[0]]), _xyz(res[antecedents[1]])
    u = _unit(d - c)
    if u is None:
        return None
    w = _unit((x - c) - np.dot(x - c, u) * u)
    if w is None:
        return None
    cos60, sin60 = 0.5, np.sqrt(3.0) / 2.0
    return [d + H_BOND_LENGTH * (cos60 * u + sin60 * w), d + H_BOND_LENGTH * (cos60 * u - sin60 * w)]


def _donor(i: int, res, atom_name: str, ante: Optional[np.ndarray], hydrogens: Optional[list[np.ndarray]],
           backbone: bool, explicit: bool) -> Optional[dict]:
    """Donatore con i suoi idrogeni: quelli del file se ce ne sono (dona solo chi li ha), altrimenti quelli
    posizionati (None = gruppo che ruota, criterio sugli atomi pesanti)."""
    mode = "placed" if hydrogens else None
    if explicit:
        hydrogens = _explicit_hydrogens(res, res[atom_name])
        if not hydrogens:
            return None
        mode = "explicit"
    return {"i": i, "atom": res[atom_name], "name": atom_name, "xyz": _xyz(res[atom_name]), "ante": ante,
            "hs": hydrogens, "mode": mode, "backbone": backbone}


def _sites(residues: _Residues, explicit: bool) -> tuple[list[dict], list[dict]]:
    """Donatori e accettori di legame idrogeno, con antecedenti e idrogeni."""
    donors: list[Optional[dict]] = []
    acceptors: list[dict] = []
    for i, res in enumerate(residues.items):
        name = res.get_resname().strip()
        # scheletro: N–H come DSSP (non per la prolina né per il primo residuo di un tratto)
        if "N" in res and name != "PRO":
            prev = residues.items[i - 1] if (i - 1) in residues.bonded_next else None
            hs = None
            if prev is not None and "C" in prev and "O" in prev:
                co = _unit(_xyz(prev["C"]) - _xyz(prev["O"]))
                hs = [_xyz(res["N"]) + H_BOND_LENGTH * co] if co is not None else None
            # senza residuo precedente (N-terminale, NH₃⁺) il gruppo ruota: criterio sugli atomi pesanti
            donors.append(_donor(i, res, "N", _xyz(res["CA"]), hs, True, explicit))
        for atom_name, (antecedents, geometry) in SIDECHAIN_DONORS.get(name, {}).items():
            if atom_name not in res or antecedents[0] not in res:
                continue
            ante_names = antecedents if geometry == "bisector" else antecedents[:1]
            ante = np.mean([_xyz(res[a]) for a in ante_names if a in res], axis=0)
            hs = _placed_hydrogens(res, atom_name, antecedents, geometry)
            donors.append(_donor(i, res, atom_name, ante, hs, False, explicit))
        for atom_name in ("O", "OXT"):
            if atom_name in res and "C" in res:
                acceptors.append({"i": i, "atom": res[atom_name], "name": atom_name, "xyz": _xyz(res[atom_name]),
                                  "ante": _xyz(res["C"]), "backbone": True})
        for atom_name, antecedents in SIDECHAIN_ACCEPTORS.get(name, {}).items():
            if atom_name not in res or not all(a in res for a in antecedents):
                continue
            if explicit and atom_name.startswith("N") and _explicit_hydrogens(res, res[atom_name]):
                continue  # azoto dell'istidina protonato: niente doppietto libero
            acceptors.append({"i": i, "atom": res[atom_name], "name": atom_name, "xyz": _xyz(res[atom_name]),
                              "ante": np.mean([_xyz(res[a]) for a in antecedents], axis=0), "backbone": False})
    return [d for d in donors if d is not None], acceptors


def _hbond_geometry(donor: dict, acceptor: dict) -> Optional[dict]:
    """Valori geometrici se la coppia soddisfa il criterio, altrimenti None."""
    a = acceptor["xyz"]
    acceptor_angle = _angle(donor["xyz"], a, acceptor["ante"])
    if acceptor_angle < HBOND_ANTECEDENT_MIN:
        return None
    if donor["hs"]:
        best = None
        for h in donor["hs"]:
            ha = float(np.linalg.norm(h - a))
            dha = _angle(donor["xyz"], h, a)
            if ha > HBOND_HA_MAX or dha < HBOND_DHA_MIN or _angle(h, a, acceptor["ante"]) < HBOND_ANTECEDENT_MIN:
                continue
            if best is None or dha > best["angle"]:
                best = {"ha_distance": round(ha, 2), "angle": round(dha, 1), "donor_angle": None}
        if best is None:
            return None
        return {**best, "acceptor_angle": round(acceptor_angle, 1), "hydrogen": donor["mode"]}
    if donor["ante"] is None:
        return None
    donor_angle = _angle(donor["ante"], donor["xyz"], a)
    if donor_angle < HBOND_ANTECEDENT_MIN:
        return None
    return {"ha_distance": None, "angle": None, "donor_angle": round(donor_angle, 1),
            "acceptor_angle": round(acceptor_angle, 1), "hydrogen": None}


def hydrogen_bonds(model, residues: Optional[_Residues] = None) -> tuple[list[dict], str]:
    """Legami idrogeno fra amminoacidi (anche fra catene) e modo degli idrogeni ("explicit" | "placed")."""
    from Bio.PDB import NeighborSearch

    residues = residues or _Residues(model)
    explicit = any(_is_hydrogen(a) for res in residues.items for a in res)
    donors, acceptors = _sites(residues, explicit)
    if not donors or not acceptors:
        return [], "explicit" if explicit else "placed"
    by_atom = {id(acc["atom"]): acc for acc in acceptors}
    ns = NeighborSearch([acc["atom"] for acc in acceptors])
    found: dict[frozenset, dict] = {}
    for donor in donors:
        for atom in ns.search(donor["atom"].coord, HBOND_DA_MAX, level="A"):
            acc = by_atom.get(id(atom))
            if acc is None or acc["i"] == donor["i"] or acc["atom"] is donor["atom"]:
                continue
            if donor["backbone"] and acc["backbone"] and residues.peptide_neighbours(donor["i"], acc["i"]):
                continue
            distance = float(np.linalg.norm(donor["xyz"] - acc["xyz"]))
            if distance > HBOND_DA_MAX:
                continue
            geom = _hbond_geometry(donor, acc)
            if geom is None:
                continue
            kind = ("backbone" if donor["backbone"] and acc["backbone"]
                    else "mixed" if donor["backbone"] or acc["backbone"] else "sidechain")
            entry = {"a": residues.end(donor["i"], donor["name"]), "b": residues.end(acc["i"], acc["name"]),
                     "distance": round(distance, 2), **geom, "kind": kind,
                     "inter_chain": residues.chain[donor["i"]] != residues.chain[acc["i"]],
                     "_order": (donor["i"], acc["i"])}
            key = frozenset((id(donor["atom"]), id(acc["atom"])))
            previous = found.get(key)
            # stessa coppia nei due versi (Ser–Thr, His): si tiene quella con l'angolo migliore
            if previous is None or _score(entry) > _score(previous):
                found[key] = entry
    out = sorted(found.values(), key=lambda e: e["_order"])
    for e in out:
        del e["_order"]
    return out, "explicit" if explicit else "placed"


def _score(entry: dict) -> float:
    return float(entry.get("angle") or entry.get("donor_angle") or 0.0) - 10.0 * entry["distance"]


def salt_bridges(model, residues: Optional[_Residues] = None) -> list[dict]:
    """Ponti salini (Barlow & Thornton 1983): una voce per coppia di residui, con la coppia di atomi più vicina."""
    from Bio.PDB import NeighborSearch

    residues = residues or _Residues(model)
    anions, cations = [], []
    for i, res in enumerate(residues.items):
        name = res.get_resname().strip()
        for atom_name in ANIONIC.get(name, ()):
            if atom_name in res:
                anions.append((i, atom_name, res[atom_name]))
        for atom_name in CATIONIC.get(name, ()):
            if atom_name in res:
                cations.append((i, atom_name, res[atom_name]))
    if not anions or not cations:
        return []
    by_atom = {id(atom): (i, atom_name) for i, atom_name, atom in anions}
    ns = NeighborSearch([atom for _i, _n, atom in anions])
    pairs: dict[tuple[int, int], dict] = {}
    for ci, cname, catom in cations:
        for atom in ns.search(catom.coord, SALT_BRIDGE_MAX, level="A"):
            ai, aname = by_atom[id(atom)]
            distance = float(np.linalg.norm(_xyz(catom) - _xyz(atom)))
            if distance > SALT_BRIDGE_MAX or ai == ci:
                continue
            entry = pairs.get((ci, ai))
            if entry is None:
                entry = pairs[(ci, ai)] = {"a": residues.end(ci, cname), "b": residues.end(ai, aname),
                                           "distance": round(distance, 2), "pairs": 0,
                                           "his": residues.items[ci].get_resname().strip() == "HIS",
                                           "inter_chain": residues.chain[ci] != residues.chain[ai], "_d": distance}
            entry["pairs"] += 1
            if distance < entry["_d"]:
                entry.update({"a": residues.end(ci, cname), "b": residues.end(ai, aname),
                              "distance": round(distance, 2), "_d": distance})
    out = [pairs[k] for k in sorted(pairs)]
    for e in out:
        del e["_d"]
    return out


def disulfides(model, residues: Optional[_Residues] = None) -> list[dict]:
    """Ponti disolfuro: SG–SG entro 2,0 ± 0,2 Å, con distanza dei Cα e diedro χ3 (CB–SG–SG–CB)."""
    residues = residues or _Residues(model)
    cys = [(i, res) for i, res in enumerate(residues.items) if res.get_resname().strip() == "CYS" and "SG" in res]
    out = []
    for k, (i, ri) in enumerate(cys):
        for j, rj in cys[k + 1:]:
            distance = float(np.linalg.norm(_xyz(ri["SG"]) - _xyz(rj["SG"])))
            if abs(distance - SS_DISTANCE) > SS_TOLERANCE:
                continue
            chi3 = (round(dihedral(_xyz(ri["CB"]), _xyz(ri["SG"]), _xyz(rj["SG"]), _xyz(rj["CB"])), 1)
                    if "CB" in ri and "CB" in rj else None)
            out.append({"a": residues.end(i, "SG"), "b": residues.end(j, "SG"), "distance": round(distance, 3),
                        "ca_distance": round(float(np.linalg.norm(_xyz(ri["CA"]) - _xyz(rj["CA"]))), 2),
                        "chi3": chi3, "inter_chain": residues.chain[i] != residues.chain[j]})
    return out


def _rings(residues: _Residues) -> list[dict]:
    out = []
    for i, res in enumerate(residues.items):
        for label, names in RINGS.get(res.get_resname().strip(), ()):
            if not all(n in res for n in names):
                continue
            xyz = np.array([_xyz(res[n]) for n in names])
            centre = xyz.mean(axis=0)
            normal = np.linalg.svd(xyz - centre)[2][-1]  # piano dei minimi quadrati
            out.append({"i": i, "label": label, "centre": centre, "normal": normal / np.linalg.norm(normal)})
    return out


def _offset(ring: dict, point: np.ndarray) -> float:
    """Distanza fra il centroide dell'anello e la proiezione di `point` sul suo piano."""
    v = point - ring["centre"]
    return float(np.linalg.norm(v - np.dot(v, ring["normal"]) * ring["normal"]))


def aromatic(model, residues: Optional[_Residues] = None) -> list[dict]:
    """π-stacking (parallelo o a T) e catione-π: una voce per coppia di residui, la più vicina."""
    residues = residues or _Residues(model)
    rings = _rings(residues)
    best: dict[tuple, dict] = {}

    def keep(key: tuple, entry: dict) -> None:
        if key not in best or entry["distance"] < best[key]["distance"]:
            best[key] = entry

    for k, r1 in enumerate(rings):
        for r2 in rings[k + 1:]:
            if r1["i"] == r2["i"]:
                continue
            distance = float(np.linalg.norm(r1["centre"] - r2["centre"]))
            if distance > PI_STACK_MAX:
                continue
            angle = float(np.degrees(np.arccos(np.clip(abs(np.dot(r1["normal"], r2["normal"])), 0.0, 1.0))))
            offset = min(_offset(r1, r2["centre"]), _offset(r2, r1["centre"]))
            if offset > PI_OFFSET_MAX:
                continue
            if angle <= PI_ANGLE_DEV:
                kind = "pi_parallel"
            elif angle >= 90.0 - PI_ANGLE_DEV:
                kind = "pi_tshaped"
            else:
                continue
            first, second = sorted((r1, r2), key=lambda r: r["i"])
            keep(("pi", first["i"], second["i"]), {
                "type": kind, "a": residues.end(first["i"], first["label"]),
                "b": residues.end(second["i"], second["label"]), "distance": round(distance, 2),
                "angle": round(angle, 1), "offset": round(offset, 2),
                "inter_chain": residues.chain[first["i"]] != residues.chain[second["i"]], "_order": (first["i"], second["i"])})
    for i, res in enumerate(residues.items):
        atom_name = CATION_PI.get(res.get_resname().strip())
        if atom_name is None or atom_name not in res:
            continue
        cation = _xyz(res[atom_name])
        for ring in rings:
            if ring["i"] == i:
                continue
            distance = float(np.linalg.norm(cation - ring["centre"]))
            if distance > CATION_PI_MAX:
                continue
            offset = _offset(ring, cation)
            if offset > PI_OFFSET_MAX:
                continue
            angle = _angle(ring["centre"] + ring["normal"], ring["centre"], cation)
            keep(("cation", i, ring["i"]), {
                "type": "cation_pi", "a": residues.end(i, atom_name), "b": residues.end(ring["i"], ring["label"]),
                "distance": round(distance, 2), "angle": round(min(angle, 180.0 - angle), 1),
                "offset": round(offset, 2), "inter_chain": residues.chain[i] != residues.chain[ring["i"]],
                "_order": (min(i, ring["i"]), max(i, ring["i"]))})
    out = sorted(best.values(), key=lambda e: (e["_order"], e["type"]))
    for e in out:
        del e["_order"]
    return out


def analyze(model) -> dict:
    """Tutte le interazioni del modello (primo modello di Biopython), con criteri e riepilogo."""
    residues = _Residues(model)
    hbonds, hydrogens = hydrogen_bonds(model, residues)
    bridges = salt_bridges(model, residues)
    ss = disulfides(model, residues)
    arom = aromatic(model, residues)
    pi = [e for e in arom if e["type"] != "cation_pi"]
    summary = {
        "residues": len(residues.items), "chains": len(set(residues.chain)),
        "hbonds": len(hbonds), "hbonds_backbone": sum(1 for h in hbonds if h["kind"] == "backbone"),
        "hbonds_mixed": sum(1 for h in hbonds if h["kind"] == "mixed"),
        "hbonds_sidechain": sum(1 for h in hbonds if h["kind"] == "sidechain"),
        "salt_bridges": len(bridges), "disulfides": len(ss), "pi_stacking": len(pi),
        "cation_pi": len(arom) - len(pi),
        "inter_chain": {"hbonds": sum(1 for h in hbonds if h["inter_chain"]),
                        "salt_bridges": sum(1 for b in bridges if b["inter_chain"]),
                        "disulfides": sum(1 for s in ss if s["inter_chain"]),
                        "aromatic": sum(1 for e in arom if e["inter_chain"])},
    }
    return {"version": VERSION, "hydrogens": hydrogens, "criteria": CRITERIA, "summary": summary,
            "hbonds": hbonds, "salt_bridges": bridges, "disulfides": ss, "aromatic": arom}
