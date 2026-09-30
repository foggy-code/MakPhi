"""Ligandi, cofattori e metalli (F5.4): classificazione dei gruppi eteroatomici, colonne dei ligandi nel PIR di
MODELLER e verifica dei ligandi di una struttura (contatti, scontri, geometria di coordinazione dei metalli).

Logica pura su modelli di Biopython e voci PIR: nessun file, nessun processo.

**Classificazione** (`classify`): ogni gruppo eteroatomico non d'acqua è un *metallo* (ione di un solo atomo di un
elemento metallico), un *cofattore noto* (`COFACTORS`), un *additivo* di cristallizzazione (`ADDITIVES`), un *residuo
modificato* della catena (MSE, SEP…: non è un ligando e non si trasferisce) oppure un *altro ligando*. Un residuo
modificato ha N, CA e C dello scheletro *e* un legame peptidico con il residuo precedente o seguente della catena: SAM,
SAH o un amminoacido libero legato come substrato hanno gli stessi atomi ma nessun legame, e sono ligandi. Nella
modellazione gli additivi e gli ioni monovalenti (Na⁺, K⁺, spesso dal tampone) sono esclusi per
default: come nelle banche dati dei ligandi biologicamente rilevanti (BioLiP, Yang et al. 2013), che scartano gli
artefatti di cristallizzazione, di solito non hanno un ruolo biologico e trasferirli aggiungerebbe solo vincoli.

**Allineamento** (`pir_with_ligands`): MODELLER legge i gruppi HETATM del template (con `env.io.hetatm = True`) come
residui '.' (BLK) e li copia nel modello solo se anche il bersaglio ha '.' nella stessa colonna. BioLoom scrive i
gruppi scelti in fondo alla loro catena nella copia preparata del template e aggiunge le colonne corrispondenti alla
fine del tratto di catena nel PIR: la stessa regola vale per l'allineamento automatico e per quello dell'utente, i
cui eventuali '.' vengono prima tolti (la scelta dei ligandi è quella delle opzioni). Con più template, lo stesso gruppo
nello stesso sito (stesso nome, residui del sito allineati agli stessi residui del bersaglio) occupa una sola colonna:
nel modello c'è un solo gruppo, con i vincoli di tutti quei template, e non una copia per template.

**Verifica** (`evaluate`), per ogni gruppo:
- contatti: residui della proteina con un atomo pesante entro 4 Å;
- scontri: coppie di atomi pesanti a meno di 2,2 Å con la proteina o con un altro gruppo, escluse la coordinazione dei
  metalli (metallo–donatore entro la sfera di coordinazione) e i legami covalenti presenti anche nel template;
- metalli: donatori N, O, S (His, Cys, Asp/Glu, Met, scheletro, acqua, altri ligandi) entro la distanza attesa +
  0,5 Å, distanza di ciascuno rispetto al valore atteso (± 0,25 Å), numero di coordinazione rispetto all'intervallo
  tipico del metallo e geometria più probabile dagli angoli donatore–metallo–donatore (gRMSD con posizioni vacanti,
  come CheckMyMetal);
- confronto con il sito del template (modellazione): i residui che coordinano il metallo nel template, riportati sul
  bersaglio attraverso l'allineamento, lo coordinano anche nel modello?
Esito: *buono*, *da controllare* o *problematico*, con le motivazioni in italiano. Nessun effetto sul voto A–D.

Fonti:
- Harding M.M. (2006) Small revisions to predicted distances around metal sites in proteins. Acta Cryst. D62:678–682
  (distanze attese metallo–donatore dal CSD e da strutture proteiche a ≤ 1,25 Å, `REFERENCE_DISTANCES`: valori
  verificati sulla tabella «revised July 2006» del sito dell'autrice, con l'affidabilità di ciascuno);
- Zheng H., Cooper D.R., Porebski P.J., Shabalin I.G., Handing K.B., Minor W. (2017) CheckMyMetal: a
  macromolecular metal-binding validation tool. Acta Cryst. D73:223–233 (geometria con posizioni vacanti, soglie di
  gRMSD 13,5° e 21,5° e di vacanza 25%; distanze del Ni dalla Tabella 3, CSD, verificate su PMC5349434);
- Bazayeva M., Andreini C., Rosato A. (2024) A database overview of metal-coordination distances in metalloproteins.
  Acta Cryst. D80:362–376 (confronto con Harding);
- Kuppuraj G., Dudev M., Lim C. (2009) Factors governing metal–ligand distances and coordination geometries of metal
  complexes. J. Phys. Chem. B 113:2952–2960 (confronto degli angoli con le geometrie ideali);
- Yang J., Roy A., Zhang Y. (2013) BioLiP: a semi-manually curated database for biologically relevant ligand–protein
  interactions. Nucleic Acids Res. 41:D1096–D1103 (additivi di cristallizzazione esclusi).
"""

from __future__ import annotations

import itertools
import math
from typing import Iterable, Optional

import numpy as np

from ..core.errors import AppError
from .sequences import PirEntry
from .structure import COMMON_ADDITIVES, WATER_NAMES, _resid, three_to_one

VERSION = 1

# ---------------------------------------------------------------------------------------------- classificazione
CLASS_LABEL = {"metal": "metallo", "cofactor": "cofattore noto", "additive": "additivo", "ligand": "altro ligando",
               "modified": "residuo modificato"}

# Elementi metallici riconosciuti negli ioni di un solo atomo (colonna "element" del PDB, altrimenti il nome del
# residuo: ZN, MG, FE2, CU1, 3CO...).
METAL_ELEMENTS = {"LI", "NA", "K", "RB", "CS", "MG", "CA", "SR", "BA", "AL", "GA", "V", "CR", "MN", "FE", "CO", "NI",
                  "CU", "ZN", "MO", "W", "CD", "HG", "PT", "AU", "AG", "PB", "LA", "SM", "EU", "GD", "TB", "YB", "LU"}
METAL_RESNAMES = {"FE2": "FE", "FE3": "FE", "CU1": "CU", "CU2": "CU", "MN3": "MN", "3CO": "CO", "CO3": "CO",
                  "NI2": "NI", "ZN2": "ZN"}
# Ioni monovalenti: spesso vengono dal tampone o dalla soluzione di cristallizzazione, esclusi per default.
MONOVALENT = {"LI", "NA", "K", "RB", "CS"}

# Cofattori noti (codici del Chemical Component Dictionary del PDB): selezionati per default.
COFACTORS = {
    "HEM": "eme b (protoporfirina IX con ferro)", "HEC": "eme c", "HEA": "eme a",
    "FAD": "flavina adenina dinucleotide", "FMN": "flavina mononucleotide",
    "NAD": "NAD⁺", "NAI": "NADH", "NAP": "NADP⁺", "NDP": "NADPH",
    "ATP": "ATP", "ADP": "ADP", "AMP": "AMP", "ANP": "AMP-PNP (analogo non idrolizzabile dell'ATP)",
    "GTP": "GTP", "GDP": "GDP", "GNP": "GMP-PNP (analogo non idrolizzabile del GTP)",
    "SAM": "S-adenosilmetionina", "SAH": "S-adenosilomocisteina",
    "PLP": "piridossal-5'-fosfato", "PMP": "piridossamina-5'-fosfato",
    "COA": "coenzima A", "ACO": "acetil-coenzima A", "TPP": "tiamina difosfato", "TDP": "tiamina difosfato",
    "BTN": "biotina", "LPA": "acido lipoico", "FES": "cluster [2Fe-2S]", "SF4": "cluster [4Fe-4S]",
    "F3S": "cluster [3Fe-4S]", "CLA": "clorofilla a", "BCL": "batterioclorofilla a", "RET": "retinale",
    "B12": "cobalamina", "CNC": "cianocobalamina", "PQQ": "pirrolochinolin chinone", "H4B": "tetraidrobiopterina",
    "MGD": "molibdopterina guanina dinucleotide",
}

# Additivi di cristallizzazione comuni: quelli di domain/structure (anche nell'elenco dei ligandi della libreria) più
# glicoli, tamponi, crioprotettori, detergenti e piccoli anioni frequenti nel PDB. Na⁺ e K⁺ sono classificati come
# metalli (ioni monovalenti, anch'essi esclusi per default).
ADDITIVES = (COMMON_ADDITIVES - MONOVALENT) | {
    "MPD", "MRD", "PG4", "PGE", "1PE", "P6G", "EPE", "MES", "TRS", "CIT", "FLC", "BME", "IPA", "EOH", "ACY", "IMD",
    "NH4", "SCN", "AZI", "MLI", "TAR", "BOG", "DTT", "CO3", "BCT",
}

# ---------------------------------------------------------------------------------------------- soglie
CONTACT_DISTANCE = 4.0   # residui "in contatto" con il ligando (Å)
CLASH_DISTANCE = 2.2     # scontro pesante fra atomi pesanti non legati (Å)
DISTANCE_TOLERANCE = 0.25  # distanza metallo–donatore "nella norma": atteso ± 0,25 Å
SHELL_TOLERANCE = 0.5    # sfera di coordinazione: donatori entro atteso + 0,5 Å
FALLBACK_SHELL = 3.0     # metalli senza distanze di riferimento: donatori entro 3,0 Å
COVALENT_TOLERANCE = 0.45  # legame covalente: somma dei raggi covalenti + 0,45 Å (come domain/clashes)
PEPTIDE_BOND = 1.9       # legame peptidico C–N (circa 1,33 Å) con margine: il residuo modificato è nella catena
GRMSD_BORDERLINE = 13.5  # CheckMyMetal: gRMSD oltre 13,5° al limite, oltre 21,5° anomalo (gradi)
GRMSD_OUTLIER = 21.5
VACANCY_LIMIT = 0.25     # CheckMyMetal: oltre il 25% di posizioni vacanti anomalo
GEOMETRY_TIE = 1.0       # geometrie entro 1° di gRMSD: si preferisce quella con meno posizioni vacanti

# Distanze attese metallo–donatore (Å). Categorie: O dell'acqua, O carbossilico di Asp/Glu monodentato, O carbonilico
# dello scheletro, N imidazolico dell'His, S della Cys. Una categoria assente = donatore insolito per quel metallo.
# - Na, Mg, K, Ca, Mn, Fe, Co, Cu, Zn: tabella «Metal-donor atom target distances (revised July 2006)» di Harding
#   (2006), dal sito dell'autrice (tanna.bch.ed.ac.uk/newtargs_06.html, copia del 29/04/2009 nell'Internet Archive),
#   basata sul CSD e sulle strutture proteiche a risoluzione ≤ 1,25 Å; tutti i valori verificati (settembre 2026). La
#   tabella indica l'affidabilità (`REFERENCE_RELIABILITY`): *** deviazione standard ~0,05 Å, ** 0,10, * 0,15–0,20,
#   nessuna stella la meno affidabile (Mg–O dello scheletro, Cu–O dell'acqua e dello scheletro, Cu–S della Cys…).
# - Ni (assente in Harding): Tabella 3 di Zheng et al. (2017, CheckMyMetal, CSD), verificata sul testo completo di
#   Europe PMC (PMC5349434): O dell'acqua 2,08(6); O di un carbonio (carbossilato, carbonile) 2,07(7) della moda più
#   lunga, usato per Asp/Glu e per lo scheletro; N 2,09(7) della moda più lunga; S 2,18(3).
# Bazayeva et al. (2024, MetalPDB, strutture < 1,5 Å) non trovano differenze significative con Harding et al. (2010)
# entro la dispersione (carbonile dello scheletro dei metalli alcalini e alcalino-terrosi entro 0,05 Å; Zn–N(His)
# 2,05 ± 0,06 Å per Nδ1 e 2,03 ± 0,07 Å per Nε2): la tolleranza di ± 0,25 Å le comprende.
REFERENCE_DISTANCES: dict[str, dict[str, float]] = {
    "NA": {"O_water": 2.41, "O_carboxyl": 2.41, "O_backbone": 2.38},
    "MG": {"O_water": 2.07, "O_carboxyl": 2.07, "O_backbone": 2.26},
    "K": {"O_water": 2.81, "O_carboxyl": 2.82, "O_backbone": 2.74},
    "CA": {"O_water": 2.39, "O_carboxyl": 2.36, "O_backbone": 2.36},
    "MN": {"O_water": 2.19, "O_carboxyl": 2.15, "O_backbone": 2.19, "N_his": 2.21, "S_cys": 2.35},
    "FE": {"O_water": 2.09, "O_carboxyl": 2.04, "O_backbone": 2.04, "N_his": 2.16, "S_cys": 2.30},
    "CO": {"O_water": 2.09, "O_carboxyl": 2.05, "O_backbone": 2.08, "N_his": 2.14, "S_cys": 2.25},
    "CU": {"O_water": 2.13, "O_carboxyl": 1.99, "O_backbone": 2.04, "N_his": 2.02, "S_cys": 2.15},
    "ZN": {"O_water": 2.09, "O_carboxyl": 1.99, "O_backbone": 2.07, "N_his": 2.03, "S_cys": 2.31},
    "NI": {"O_water": 2.08, "O_carboxyl": 2.07, "O_backbone": 2.07, "N_his": 2.09, "S_cys": 2.18},
}
# Affidabilità di Harding (2006) per valore (numero di stelle, 0 = la meno affidabile); il Ni non ne ha (CSD).
REFERENCE_RELIABILITY: dict[str, dict[str, int]] = {
    "NA": {"O_water": 2, "O_carboxyl": 2, "O_backbone": 2},
    "MG": {"O_water": 3, "O_carboxyl": 2, "O_backbone": 0},
    "K": {"O_water": 1, "O_carboxyl": 1, "O_backbone": 1},
    "CA": {"O_water": 2, "O_carboxyl": 2, "O_backbone": 2},
    "MN": {"O_water": 3, "O_carboxyl": 3, "O_backbone": 0, "N_his": 2, "S_cys": 0},
    "FE": {"O_water": 2, "O_carboxyl": 2, "O_backbone": 0, "N_his": 1, "S_cys": 3},
    "CO": {"O_water": 2, "O_carboxyl": 3, "O_backbone": 0, "N_his": 2, "S_cys": 1},
    "CU": {"O_water": 0, "O_carboxyl": 1, "O_backbone": 0, "N_his": 2, "S_cys": 0},
    "ZN": {"O_water": 3, "O_carboxyl": 3, "O_backbone": 0, "N_his": 3, "S_cys": 2},
}
# Numero di coordinazione tipico nei siti proteici (estremi compresi). Zheng et al. (2017): Mg ottaedrico stretto,
# siti con meno o più di 6 donatori rari; Na di solito 6, a volte 5 o 4; K di solito ottaedrico. Bazayeva et al.
# (2024): Zn con 4 donatori nel 61% dei siti. Gli estremi degli intervalli sono una scelta di BioLoom.
CN_RANGE = {"ZN": (4, 6), "FE": (4, 6), "CU": (3, 6), "MN": (4, 6), "CO": (4, 6), "NI": (4, 6), "MG": (5, 6),
            "CA": (6, 8), "NA": (4, 7), "K": (5, 8)}
ELEMENT_LABEL = {"ZN": "Zn", "MG": "Mg", "CA": "Ca", "MN": "Mn", "FE": "Fe", "CU": "Cu", "CO": "Co", "NI": "Ni",
                 "NA": "Na", "K": "K", "CD": "Cd", "HG": "Hg"}

# Donatori: categoria del donatore → (etichetta, categoria delle distanze attese, tipico?). Dalle note della tabella di
# Harding (2006): Asn/Gln come il carbossilato monodentato («perhaps very slightly longer»); O, N e S dei ligandi come
# l'O dell'Asn (quindi il carbossilato), l'N dell'His e l'S della Cys. Scelte di BioLoom, non da Harding: Ser/Thr/Tyr
# con il valore dell'acqua (per Harding Ser/Thr stanno fra acqua e carbossilato, Tyr circa 0,1 Å sotto il
# carbossilato: differenze entro la tolleranza), Met con quello della Cys.
DONORS = {
    "N_his": ("N (His)", "N_his", True), "S_cys": ("S (Cys)", "S_cys", True),
    "S_met": ("S (Met)", "S_cys", True), "O_carboxyl": ("O (Asp/Glu)", "O_carboxyl", True),
    "O_backbone": ("O (scheletro)", "O_backbone", True), "O_amide": ("O (Asn/Gln)", "O_carboxyl", True),
    "O_hydroxyl": ("O (Ser/Thr/Tyr)", "O_water", True), "O_water": ("O (acqua)", "O_water", True),
    "O_ligand": ("O (ligando)", "O_carboxyl", True), "N_ligand": ("N (ligando)", "N_his", True),
    "S_ligand": ("S (ligando)", "S_cys", True), "N_other": ("N (altro)", "N_his", False),
}
_SIDECHAIN_DONORS = {("HIS", "ND1"): "N_his", ("HIS", "NE2"): "N_his", ("CYS", "SG"): "S_cys",
                     ("MET", "SD"): "S_met", ("ASP", "OD1"): "O_carboxyl", ("ASP", "OD2"): "O_carboxyl",
                     ("GLU", "OE1"): "O_carboxyl", ("GLU", "OE2"): "O_carboxyl", ("ASN", "OD1"): "O_amide",
                     ("GLN", "OE1"): "O_amide", ("SER", "OG"): "O_hydroxyl", ("THR", "OG1"): "O_hydroxyl",
                     ("TYR", "OH"): "O_hydroxyl"}
# Residui che possono coordinare con la catena laterale (confronto con il sito del template)
DONOR_RESIDUES = {"HIS", "CYS", "MET", "ASP", "GLU", "ASN", "GLN", "SER", "THR", "TYR"}

# Raggi covalenti (Å) per riconoscere i legami fra ligando e proteina (stessi valori di domain/clashes)
COVALENT_RADII = {"C": 0.76, "N": 0.71, "O": 0.66, "S": 1.05, "P": 1.07, "F": 0.57, "CL": 1.02, "BR": 1.20,
                  "I": 1.39, "SE": 1.20, "B": 0.84}

STATUS_GOOD, STATUS_CHECK, STATUS_BAD = "buono", "da controllare", "problematico"
_RANK = {STATUS_GOOD: 0, STATUS_CHECK: 1, STATUS_BAD: 2}


# ---------------------------------------------------------------------------------------------- utilità
def _num(value: float, digits: int = 2) -> str:
    """Numero all'italiana per le motivazioni (virgola decimale)."""
    return f"{value:.{digits}f}".replace(".", ",")


def element_of(atom) -> str:
    """Elemento di un atomo (maiuscolo): colonna "element", altrimenti dal nome del residuo per gli ioni di un solo
    atomo (ZN, FE2...), altrimenti la prima lettera del nome dell'atomo."""
    el = (atom.element or "").strip().upper()
    res = atom.get_parent()
    name = res.get_resname().strip().upper()
    if len(res) == 1 and (name in METAL_ELEMENTS or name in METAL_RESNAMES):
        return METAL_RESNAMES.get(name, name)
    if el and el != "X":
        return el
    return atom.get_id().strip().lstrip("0123456789").upper()[:1]


def is_water(residue) -> bool:
    return residue.id[0] == "W" or residue.get_resname().strip().upper() in WATER_NAMES


def is_hetero(residue) -> bool:
    """Gruppo eteroatomico non d'acqua (HETATM del PDB, `id[0]` = "H_…")."""
    return residue.id[0] not in (" ", "W") and not is_water(residue)


def _peptide_linked(residue) -> bool:
    """Legame peptidico con il residuo precedente o seguente della catena, nell'ordine del file: C del precedente–N
    oppure C–N del seguente entro `PEPTIDE_BOND`."""
    chain = residue.get_parent()
    siblings = chain.child_list if chain is not None else [residue]
    k = next((i for i, r in enumerate(siblings) if r is residue), None)
    if k is None:
        return False
    for other, theirs, mine in ((siblings[k - 1] if k > 0 else None, "C", "N"),
                                (siblings[k + 1] if k + 1 < len(siblings) else None, "N", "C")):
        if other is not None and theirs in other and mine in residue \
                and other[theirs] - residue[mine] <= PEPTIDE_BOND:
            return True
    return False


def is_modified_residue(residue) -> bool:
    """Residuo modificato della catena (MSE, SEP, TPO…): ha gli atomi N, CA e C dello scheletro ed è legato con un
    legame peptidico al residuo precedente o seguente. Gli stessi atomi senza legame (SAM, SAH, un amminoacido libero
    legato come substrato) non bastano, e i cofattori noti non sono mai residui modificati.

    L'esito si conserva nel residuo (`xtra`): la verifica lo chiede per ogni atomo vicino, e la copia preparata del
    template sposta i gruppi in fondo alla catena solo dopo averli classificati."""
    cached = residue.xtra.get("bioloom_modified")
    if cached is not None:
        return cached
    names = {a.get_id() for a in residue}
    value = ({"N", "CA", "C"} <= names and residue.get_resname().strip().upper() not in COFACTORS
             and _peptide_linked(residue))
    residue.xtra["bioloom_modified"] = value
    return value


def group_key(residue) -> tuple[str, str, str]:
    """(catena, numero, nome) di un gruppo, come nelle opzioni di modellazione."""
    return residue.get_parent().id, _resid(residue), residue.get_resname().strip()


def group_label(chain: str, resnum: str, resname: str) -> str:
    return f"{resname} {chain}{resnum}"


def _heavy(residue) -> list:
    return [a for a in residue if element_of(a) not in ("H", "D")]


def classify(residue) -> dict:
    """Classe di un gruppo eteroatomico non d'acqua, se è scelto per default nella modellazione e perché no."""
    name = residue.get_resname().strip().upper()
    atoms = _heavy(residue)
    if is_modified_residue(residue):
        return {"class": "modified", "default": False, "transferable": False,
                "reason": "residuo modificato della catena, non un ligando: nel modello diventa il residuo del "
                          "bersaglio allineato"}
    if len(atoms) == 1 and element_of(atoms[0]) in METAL_ELEMENTS:
        element = element_of(atoms[0])
        if element in MONOVALENT:
            return {"class": "metal", "element": element, "default": False, "transferable": True,
                    "reason": "ione monovalente, spesso dal tampone o dalla soluzione di cristallizzazione"}
        return {"class": "metal", "element": element, "default": True, "transferable": True, "reason": ""}
    if name in COFACTORS:
        return {"class": "cofactor", "description": COFACTORS[name], "default": True, "transferable": True,
                "reason": ""}
    if name in ADDITIVES:
        return {"class": "additive", "default": False, "transferable": True,
                "reason": "additivo comune di cristallizzazione (glicole, tampone, crioprotettore o piccolo ione): "
                          "di solito senza ruolo biologico"}
    return {"class": "ligand", "default": True, "transferable": True, "reason": ""}


def candidates(model, chains: Optional[Iterable[str]] = None) -> list[dict]:
    """Gruppi eteroatomici non d'acqua (delle catene indicate) con la classificazione, nell'ordine del file.

    Stessi campi di `structure.hetero_groups` (resname, chain, resnum, atoms, additive) più class, label, default
    (scelto per default nella modellazione), transferable, reason (perché non è scelto per default) ed eventualmente
    element (metalli) e description (cofattori).
    """
    wanted = set(chains) if chains else None
    out = []
    for chain in model:
        if wanted is not None and chain.id not in wanted:
            continue
        for res in chain:
            if not is_hetero(res):
                continue
            info = classify(res)
            name = res.get_resname().strip()
            out.append({"resname": name, "chain": chain.id, "resnum": _resid(res), "atoms": len(res),
                        "additive": name in COMMON_ADDITIVES, **info, "label": CLASS_LABEL[info["class"]]})
    return out


def select(model, chains: list[str], requested: Optional[list]) -> list:
    """Residui eteroatomici da trasferire dalle catene del template.

    `requested` None: scelta predefinita (metalli non monovalenti, cofattori e altri ligandi; niente additivi, ioni
    monovalenti e residui modificati). Altrimenti esattamente i gruppi (catena, numero, nome) indicati: un gruppo che
    non c'è o un residuo modificato sono un errore chiaro. I residui sono sempre nell'ordine del file.
    """
    groups = {}
    for chain in model:
        if chain.id not in chains:
            continue
        for res in chain:
            if is_hetero(res):
                groups[group_key(res)] = res
    if requested is None:
        return [res for res in groups.values() if classify(res)["default"]]
    wanted = set()
    for item in requested:
        key = tuple(str(x).strip() for x in item)
        res = groups.get(key)
        if res is None:
            available = ", ".join(group_label(*k) for k in list(groups)[:12]) or "nessuno"
            raise AppError(f"Il gruppo {group_label(*key)} non è tra gli eteroatomi delle catene {', '.join(chains)} "
                           f"del template (disponibili: {available}).", code="ligand_not_found")
        info = classify(res)
        if not info["transferable"]:
            raise AppError(f"{group_label(*key)}: {info['reason']}.", code="ligand_not_transferable")
        wanted.add(key)
    return [res for key, res in groups.items() if key in wanted]


# ---------------------------------------------------------------------------------------------- donatori e geometria
def donor_category(atom) -> Optional[str]:
    """Categoria di un possibile atomo donatore (N, O, S), None per gli altri atomi."""
    element = element_of(atom)
    if element not in ("N", "O", "S"):
        return None
    res = atom.get_parent()
    if is_water(res):
        return "O_water" if element == "O" else None
    name = atom.get_id().strip()
    if _is_protein(res):
        if name in ("O", "OXT"):
            return "O_backbone"
        side = _SIDECHAIN_DONORS.get((res.get_resname().strip().upper(), name))
        if side:
            return side
        return "N_other" if element == "N" else ("O_hydroxyl" if element == "O" else "S_cys")
    return {"N": "N_ligand", "O": "O_ligand", "S": "S_ligand"}[element]


def expected_distance(metal: str, category: str) -> Optional[float]:
    """Distanza attesa metallo–donatore (Å) o None (metallo senza riferimenti o donatore insolito)."""
    ref = REFERENCE_DISTANCES.get(metal)
    if ref is None:
        return None
    return ref.get(DONORS[category][1])


def shell_cutoff(metal: str, category: str) -> float:
    """Distanza massima di un donatore della sfera di coordinazione: attesa + 0,5 Å. Per un donatore insolito vale
    quella dell'acqua (il donatore è contato e segnalato), per un metallo senza riferimenti 3,0 Å."""
    ref = REFERENCE_DISTANCES.get(metal)
    if ref is None:
        return FALLBACK_SHELL
    value = expected_distance(metal, category) or ref.get("O_water") or FALLBACK_SHELL - SHELL_TOLERANCE
    return value + SHELL_TOLERANCE


def _unit_rows(rows: list[tuple[float, float, float]]) -> np.ndarray:
    arr = np.array(rows, dtype=float)
    return arr / np.linalg.norm(arr, axis=1)[:, None]


def _ring(count: int) -> list[tuple[float, float, float]]:
    return [(math.cos(2 * math.pi * k / count), math.sin(2 * math.pi * k / count), 0.0) for k in range(count)]


# Geometrie ideali (vettori unitari metallo → donatore). La piramide a base quadrata ha il metallo nel piano della
# base (angoli di 90° con l'apice), come l'ottaedro con una posizione vacante.
GEOMETRIES: dict[str, tuple[str, np.ndarray]] = {
    "linear": ("lineare", _unit_rows([(0, 0, 1), (0, 0, -1)])),
    "trigonal_planar": ("trigonale planare", _unit_rows(_ring(3))),
    "tetrahedral": ("tetraedrica", _unit_rows([(1, 1, 1), (1, -1, -1), (-1, 1, -1), (-1, -1, 1)])),
    "square_planar": ("quadrato planare", _unit_rows(_ring(4))),
    "trigonal_bipyramidal": ("bipiramide trigonale", _unit_rows([(0, 0, 1), (0, 0, -1), *_ring(3)])),
    "square_pyramidal": ("piramide a base quadrata", _unit_rows([(0, 0, 1), *_ring(4)])),
    "octahedral": ("ottaedrica", _unit_rows([(0, 0, 1), (0, 0, -1), *_ring(4)])),
    "pentagonal_bipyramidal": ("bipiramide pentagonale", _unit_rows([(0, 0, 1), (0, 0, -1), *_ring(5)])),
}


def _angles(vectors: np.ndarray) -> np.ndarray:
    unit = vectors / np.linalg.norm(vectors, axis=1)[:, None]
    return np.degrees(np.arccos(np.clip(unit @ unit.T, -1.0, 1.0)))


_IDEAL_ANGLES = {key: _angles(vec) for key, (_label, vec) in GEOMETRIES.items()}


def geometry(vectors: np.ndarray) -> Optional[dict]:
    """Geometria più probabile per i vettori metallo → donatore (da 2 a 7 donatori), come CheckMyMetal.

    Per ogni geometria ideale con m posizioni (m da n a n + 2, al massimo 7) si cerca l'assegnazione dei n donatori a
    n posizioni che minimizza lo scarto quadratico medio fra gli angoli donatore–metallo–donatore osservati e quelli
    ideali (gRMSD, gradi); le posizioni libere sono vacanti (vacancy = (m − n) / m). Vince la gRMSD minima; entro
    `GEOMETRY_TIE` gradi si preferisce la geometria con meno posizioni vacanti. None con meno di 2 o più di 7 donatori.
    """
    n = len(vectors)
    if n < 2 or n > 7:
        return None
    observed = _angles(np.asarray(vectors, dtype=float))
    iu = np.triu_indices(n, 1)
    obs = observed[iu]
    results = []
    for key, (label, ideal_vectors) in GEOMETRIES.items():
        m = len(ideal_vectors)
        if not n <= m <= n + 2:
            continue
        ideal = _IDEAL_ANGLES[key]
        perms = np.array(list(itertools.permutations(range(m), n)))
        diffs = ideal[perms[:, iu[0]], perms[:, iu[1]]] - obs[None, :]
        rmsd = np.sqrt((diffs ** 2).mean(axis=1))
        results.append({"key": key, "name": label, "positions": m, "grmsd": float(rmsd.min()),
                        "vacancy": (m - n) / m})
    best = min(results, key=lambda r: (r["grmsd"], r["vacancy"]))
    close = [r for r in results if r["grmsd"] <= best["grmsd"] + GEOMETRY_TIE]
    chosen = min(close, key=lambda r: (r["vacancy"], r["grmsd"]))
    return {**chosen, "grmsd": round(chosen["grmsd"], 1), "vacancy": round(chosen["vacancy"], 3)}


# ---------------------------------------------------------------------------------------------- vicini
class _Neighbors:
    """Atomi pesanti della struttura (acque comprese, idrogeni esclusi) per le ricerche di vicinato."""

    def __init__(self, model) -> None:
        from Bio.PDB import NeighborSearch

        self.atoms = [a for a in model.get_atoms() if element_of(a) not in ("H", "D")]
        self.search = NeighborSearch(self.atoms) if self.atoms else None

    def near(self, atom, radius: float) -> list:
        if self.search is None:
            return []
        return [a for a in self.search.search(atom.coord, radius) if a is not atom]


def _is_protein(residue) -> bool:
    return residue.id[0] == " " or is_modified_residue(residue)


def _atom_ref(atom) -> dict:
    res = atom.get_parent()
    return {"chain": res.get_parent().id, "resnum": _resid(res), "resname": res.get_resname().strip(),
            "atom": atom.get_id().strip(), "protein": _is_protein(res)}


def _atom_label(atom) -> str:
    res = atom.get_parent()
    name = res.get_resname().strip()
    pretty = name.capitalize() if res.id[0] == " " and len(name) == 3 else name
    return f"{pretty} {res.get_parent().id}{_resid(res)} {atom.get_id().strip()}"


def _covalent_range(a, b, distance: float) -> bool:
    ea, eb = element_of(a), element_of(b)
    if ea in METAL_ELEMENTS or eb in METAL_ELEMENTS:
        return False
    return distance < COVALENT_RADII.get(ea, 0.77) + COVALENT_RADII.get(eb, 0.77) + COVALENT_TOLERANCE


def _metal_donors(metal_atom, neighbors: _Neighbors) -> list[tuple]:
    """(atomo, categoria, distanza) dei donatori nella sfera di coordinazione, dal più vicino."""
    metal = element_of(metal_atom)
    out = []
    for other in neighbors.near(metal_atom, max(FALLBACK_SHELL, 3.4)):
        category = donor_category(other)
        if category is None:
            continue
        distance = float(np.linalg.norm(other.coord - metal_atom.coord))
        if distance <= shell_cutoff(metal, category):
            out.append((other, category, distance))
    out.sort(key=lambda item: item[2])
    return out


def describe_groups(model, residues: list) -> list[dict]:
    """Siti dei gruppi di un template (con le acque del file originale) per il confronto dopo la modellazione.

    Per ogni gruppo: resname, chain, resnum, atoms, classificazione; `metals` con i donatori di ogni atomo metallico
    (residuo, atomo, categoria, distanza, acqua), `links` con i legami covalenti verso la proteina o altri gruppi e
    `contacts` con i residui della proteina entro 4 Å (sito di un gruppo senza donatori né legami, per riconoscere lo
    stesso gruppo in più template: `pir_with_ligands`).
    """
    neighbors = _Neighbors(model)
    out = []
    for res in residues:
        chain, resnum, resname = group_key(res)
        info = classify(res)
        metals = []
        links = []
        contacts: dict[tuple[str, str], dict] = {}
        for atom in _heavy(res):
            for other in neighbors.near(atom, CONTACT_DISTANCE):
                ores = other.get_parent()
                if ores is not res and _is_protein(ores):
                    key = (ores.get_parent().id, _resid(ores))
                    contacts.setdefault(key, {"chain": key[0], "resnum": key[1], "resname": ores.get_resname().strip()})
            if element_of(atom) in METAL_ELEMENTS:
                donors = [{**_atom_ref(d), "category": cat, "distance": round(dist, 2),
                           "water": is_water(d.get_parent())}
                          for d, cat, dist in _metal_donors(atom, neighbors) if d.get_parent() is not res]
                metals.append({"atom": atom.get_id().strip(), "element": element_of(atom), "donors": donors})
                continue
            for other in neighbors.near(atom, 2.6):
                if other.get_parent() is res or is_water(other.get_parent()):
                    continue
                distance = float(np.linalg.norm(other.coord - atom.coord))
                if _covalent_range(atom, other, distance):
                    links.append({"atom": atom.get_id().strip(), "partner": _atom_ref(other),
                                  "distance": round(distance, 2)})
        out.append({"resname": resname, "chain": chain, "resnum": resnum, "atoms": len(res), "class": info["class"],
                    "label": CLASS_LABEL[info["class"]], "metals": metals, "links": links,
                    "contacts": list(contacts.values())})
    return out


# ---------------------------------------------------------------------------------------------- allineamento PIR
def _segments(seq: str) -> list[int]:
    """Indici di fine dei tratti di catena (posizione di ogni '/' e lunghezza della riga)."""
    return [i for i, c in enumerate(seq) if c == "/"] + [len(seq)]


def _site(group: dict) -> set[tuple[str, str]]:
    """Residui della proteina (catena, numero) che definiscono il sito di un gruppo del template: donatori dei metalli
    e partner dei legami covalenti, altrimenti i residui entro 4 Å."""
    keys = {(d["chain"], d["resnum"]) for m in group.get("metals") or [] for d in m.get("donors") or []
            if d.get("protein")}
    keys |= {(lk["partner"]["chain"], lk["partner"]["resnum"]) for lk in group.get("links") or []
             if (lk.get("partner") or {}).get("protein")}
    return keys or {(c["chain"], c["resnum"]) for c in group.get("contacts") or []}


def _site_columns(entry: PirEntry, t: dict, target: PirEntry) -> dict[tuple[str, str], int]:
    """Colonna del PIR di ogni residuo (catena, numero) del template allineato a un residuo del bersaglio.

    Solo per le catene la cui voce comprende tutti i residui della copia preparata (`_chain_ids`): le lettere del
    tratto sono allora quei residui, nell'ordine. Le altre catene non contribuiscono al sito."""
    ids = t.get("_chain_ids") or {}
    out: dict[tuple[str, str], int] = {}
    offset = 0
    for k, piece in enumerate(entry.sequence.split("/")):
        chain = t["chains"][k] if k < len(t["chains"]) else None
        residues = ids.get(chain) if chain is not None else None
        letters = [i for i, c in enumerate(piece) if c.isalpha()]
        if residues is not None and len(letters) == len(residues):
            for i, resid in zip(letters, residues):
                if target.sequence[offset + i:offset + i + 1].isalpha():
                    out[(chain, str(resid))] = offset + i
        offset += len(piece) + 1
    return out


def _match_slots(found: list[tuple[str, set[int]]], slots: list[dict]) -> list[Optional[dict]]:
    """Colonna già presente (di altri template) per ogni gruppo di un template, o None: associazione che rispetta
    l'ordine (gruppi e colonne nello stesso ordine) e massimizza i gruppi uniti e poi i residui del sito in comune
    (programmazione dinamica, come l'allineamento di due sequenze). Si uniscono solo gruppi con lo stesso nome e
    almeno un residuo del bersaglio del sito in comune."""
    n, m = len(found), len(slots)

    def weight(i: int, j: int) -> int:
        name, site = found[i]
        return len(site & slots[j]["site"]) if slots[j]["resname"] == name else 0

    best = [[(0, 0)] * (m + 1) for _ in range(n + 1)]
    for i in range(n - 1, -1, -1):
        for j in range(m - 1, -1, -1):
            options = [best[i + 1][j], best[i][j + 1]]
            w = weight(i, j)
            if w:
                options.append((best[i + 1][j + 1][0] + 1, best[i + 1][j + 1][1] + w))
            best[i][j] = max(options)
    out: list[Optional[dict]] = [None] * n
    i = j = 0
    while i < n and j < m:
        w = weight(i, j)
        if w and best[i][j] == (best[i + 1][j + 1][0] + 1, best[i + 1][j + 1][1] + w):
            out[i] = slots[j]
            i, j = i + 1, j + 1
        elif best[i][j] == best[i + 1][j]:
            i += 1
        else:
            j += 1
    return out


def pir_with_ligands(entries: list[PirEntry], templates: list[dict]) -> tuple[list[PirEntry], list[str]]:
    """Colonne dei ligandi ('.') del PIR per i gruppi trasferiti dai template preparati.

    `templates`: informazioni di `engines/modeller.prepare_template` (code, chains, last, `ligand_groups` nell'ordine
    del file, `_chain_residues` = residui ATOM per catena, `_hetero` = eteroatomi delle catene scelte).

    1. I '.' già presenti (PIR dell'utente, allineamento di una modellazione precedente) diventano gap e le colonne
       rimaste vuote si tolgono: i ligandi trasferiti sono quelli scelti nelle opzioni.
    2. Per ogni template e ogni sua catena con gruppi trasferiti si aggiungono, alla fine del tratto di quella catena
       (prima del '/' o della fine), le colonne dei gruppi con '.' nel template e nel bersaglio e '-' negli altri
       template. MODELLER legge i gruppi in fondo alla catena nella copia preparata, nello stesso ordine. Lo stesso
       gruppo di template diversi (stesso nome, sito — donatori, legami o residui entro 4 Å, `_site` — riportato
       attraverso le righe del PIR su residui del bersaglio in comune) condivide la colonna: un solo '.' nel bersaglio,
       quindi un solo gruppo nel modello con i vincoli di tutti quei template. L'associazione rispetta in ogni riga
       l'ordine dei gruppi nel file (le colonne di un template si susseguono come i suoi gruppi) e unisce più gruppi
       possibile (`_match_slots`); un gruppo senza corrispondente ha una colonna nuova prima di quella del gruppo
       unito che lo segue nello stesso template, altrimenti in fondo. Una nota elenca i gruppi uniti.
    3. L'intervallo del template si aggiorna: fine sull'ultimo gruppo se la sua ultima catena ne ha, "+N" ricontato,
       una fine che indicava un eteroatomo tolto torna sull'ultimo residuo.

    Restituisce le voci e gli avvisi. Errore chiaro se i '/' del template e del bersaglio non sono nella stessa
    colonna o se la voce del template non comprende tutti i residui di una catena con ligandi (i gruppi stanno dopo
    l'ultimo residuo nel file e l'intervallo deve arrivarci).
    """
    notes: list[str] = []
    entries = [PirEntry(e.code, e.kind, list(e.fields), e.sequence) for e in entries]
    target = next((e for e in entries if e.kind == "sequence"), None)
    if target is None:
        raise AppError("Allineamento PIR senza la sequenza bersaglio.", code="invalid_pir")
    by_code = {t["code"]: t for t in templates}
    rows = {e.code: e for e in entries if e.is_template}

    # 1. '.' esistenti → gap
    dotted = [i for i in range(len(target.sequence)) if any(e.sequence[i:i + 1] == "." for e in entries)]
    if dotted:
        stripped = {e.code: e.sequence.count(".") for e in entries if e.is_template and "." in e.sequence}
        for e in entries:
            chars = list(e.sequence)
            for i in dotted:
                if i < len(chars) and chars[i] == ".":
                    chars[i] = "-"
            e.sequence = "".join(chars)
        empty = {i for i in dotted if all(e.sequence[i:i + 1] in ("-", "") for e in entries)}
        for e in entries:
            e.sequence = "".join(c for i, c in enumerate(e.sequence) if i not in empty)
        for code, count in stripped.items():
            chosen = len((by_code.get(code) or {}).get("ligand_groups") or [])
            if count != chosen:
                notes.append(f"Il PIR indicava {count} {'ligando' if count == 1 else 'ligandi'} ('.') per {code}: "
                             f"{'nessuno è trasferito' if not chosen else f'si trasferiscono i {chosen} scelti'} "
                             "nelle opzioni di modellazione (Ligandi, cofattori e metalli).")

    # 2. colonne dei gruppi scelti, per tratto di catena; lo stesso gruppo di template diversi in una sola colonna
    slots: dict[int, list[dict]] = {}  # tratto → colonne [{"resname", "site": colonne, "members": {codice: gruppo}}]
    for t in templates:
        groups = t.get("ligand_groups") or []
        if not groups:
            continue
        entry = rows.get(t["code"])
        if entry is None:
            continue
        if _segments(entry.sequence) != _segments(target.sequence):
            raise AppError(f"Le interruzioni di catena ('/') di {t['code']} e del bersaglio non sono nelle stesse "
                           "colonne del PIR: i ligandi si trasferiscono solo con catene allineate.", code="invalid_pir")
        pieces = entry.sequence.split("/")
        chain_residues = t.get("_chain_residues") or {}
        columns = _site_columns(entry, t, target)
        for k, chain in enumerate(t["chains"]):
            mine = [g for g in groups if g["chain"] == chain]
            if not mine:
                continue
            letters = sum(1 for c in pieces[k] if c.isalpha()) if k < len(pieces) else 0
            expected = chain_residues.get(chain)
            if expected is not None and letters != expected:
                raise AppError(f"Per trasferire i ligandi della catena {chain} di {t['code']} la sua voce nel PIR deve "
                               f"comprendere tutti i {expected} residui della catena (ne ha {letters}): i gruppi "
                               "eteroatomici stanno dopo l'ultimo residuo. Estendi l'allineamento o togli quei "
                               "ligandi dalla scelta.", code="invalid_pir")
            column_slots = slots.setdefault(k, [])
            found = [(str(g["resname"]).strip().upper(), {columns[key] for key in _site(g) if key in columns})
                     for g in mine]
            matched = _match_slots(found, column_slots)
            for n, (g, (name, site), slot) in enumerate(zip(mine, found, matched)):
                if slot is None:
                    # gruppo senza corrispondente: colonna nuova prima di quella del prossimo gruppo unito dello
                    # stesso template (le associazioni sono in ordine), altrimenti in fondo
                    following = next((s for s in matched[n + 1:] if s is not None), None)
                    at = len(column_slots) if following is None else \
                        next(i for i, s in enumerate(column_slots) if s is following)
                    column_slots.insert(at, {"resname": name, "site": set(site), "members": {t["code"]: g}})
                else:
                    slot["members"][t["code"]] = g
                    slot["site"] |= site
    ends = _segments(target.sequence)
    for k in sorted(slots, reverse=True):
        at = ends[k]
        for e in entries:
            add = "".join("." if e.kind == "sequence" or e.code in slot["members"] else "-" for slot in slots[k])
            e.sequence = e.sequence[:at] + add + e.sequence[at:]
    for k in sorted(slots):
        for slot in slots[k]:
            if len(slot["members"]) > 1:
                labels = " e ".join(f"{group_label(g['chain'], g['resnum'], g['resname'])} di {code}"
                                    for code, g in slot["members"].items())
                n = len(slot["members"])
                notes.append(f"{labels}: stesso gruppo nello stesso sito, un solo {slot['resname']} nel modello con i "
                             f"vincoli di {'entrambi i template' if n == 2 else f'tutti i {n} template'}.")

    # 3. intervalli dei template
    for t in templates:
        entry = rows.get(t["code"])
        if entry is None:
            continue
        fields = (entry.fields + [""] * 8)[:8]
        groups = t.get("ligand_groups") or []
        end = (fields[2] or "").strip()
        last_groups = [g for g in groups if g["chain"] == t["chains"][-1]]
        hetero = {(c, r) for c, r in t.get("_hetero") or []}
        if end.startswith("+"):
            fields[2] = f"+{sum(1 for c in entry.sequence if c.isalpha() or c == '.')}"
        elif last_groups and end.upper() not in ("LAST", ""):
            fields[2], fields[3] = last_groups[-1]["resnum"], last_groups[-1]["chain"]
        elif (fields[3] or "", end) in hetero or (t["chains"][-1], end) in hetero:
            fields[2], fields[3] = t["last"], t["chains"][-1]
        entry.fields = fields
    return entries, notes


def model_groups(entries: list[PirEntry], templates: list[dict]) -> list[list[tuple[str, dict]]]:
    """Gruppi eteroatomici del modello: per ogni '.' del bersaglio, nell'ordine (come MODELLER li scrive), i gruppi
    dei template con '.' nella stessa colonna come (codice, gruppo di `ligand_groups`), nell'ordine dei template (il
    principale per primo). Il n-esimo '.' di una riga di template è il suo n-esimo gruppo."""
    target = next((e for e in entries if e.kind == "sequence"), None)
    if target is None:
        return []
    order = {t["code"]: i for i, t in enumerate(templates)}
    groups = {t["code"]: t.get("ligand_groups") or [] for t in templates}
    rows = sorted((e for e in entries if e.is_template), key=lambda e: order.get(e.code, len(order)))
    seen = {e.code: 0 for e in rows}
    out: list[list[tuple[str, dict]]] = []
    for col, c in enumerate(target.sequence):
        members = []
        for e in rows:
            if e.sequence[col:col + 1] != ".":
                continue
            n = seen[e.code]
            seen[e.code] = n + 1
            if n < len(groups.get(e.code) or []):
                members.append((e.code, groups[e.code][n]))
        if c == ".":
            out.append(members)
    return out


# ---------------------------------------------------------------------------------------------- template → modello
def _walk(seq: str, protein: list, hetero: list) -> dict[int, object]:
    """Colonna → residuo: le lettere consumano i residui della proteina, i '.' i gruppi eteroatomici, in ordine."""
    out: dict[int, object] = {}
    p = h = 0
    for i, c in enumerate(seq):
        if c.isalpha():
            if p >= len(protein):
                raise ValueError("più lettere che residui")
            res = protein[p]
            letter = three_to_one(res.get_resname())
            if c.upper() != "X" and letter != "X" and c.upper() != letter:
                raise ValueError(f"lettera {c} contro {res.get_resname()}")
            out[i] = res
            p += 1
        elif c == ".":
            if h >= len(hetero):
                raise ValueError("più '.' che gruppi")
            out[i] = hetero[h]
            h += 1
    return out


def _residues(model, start: Optional[tuple[str, str]] = None) -> tuple[list, list]:
    """Residui della proteina (ATOM) e gruppi eteroatomici non d'acqua, nell'ordine del file, dal residuo `start`."""
    protein, hetero = [], []
    started = start is None
    for chain in model:
        for res in chain:
            if not started and res.id[0] == " " and (chain.id, _resid(res)) == start:
                started = True
            if not started:
                continue
            if res.id[0] == " ":
                protein.append(res)
            elif is_hetero(res):
                hetero.append(res)
    return protein, hetero


def template_contexts(entries: list[PirEntry], templates: list[dict], template_models: dict, model) \
        -> tuple[dict, list[str]]:
    """Per ogni gruppo eteroatomico del modello venuto da un template: sito del template e corrispondenza dei residui.

    `template_models`: codice del template → modello di Biopython della copia preparata. Le colonne del PIR finale
    (quello usato da MODELLER) legano i residui: le lettere del template ai residui ATOM della copia preparata (dal
    residuo iniziale dell'intervallo), le lettere del bersaglio ai residui del modello, i '.' ai gruppi in ordine.
    Restituisce {(catena, numero) del gruppo nel modello: contesto} e le note per i template non confrontabili. Un
    gruppo del modello vincolato da più template (stessa colonna) si confronta con il sito del primo, il principale.
    """
    notes: list[str] = []
    target = next((e for e in entries if e.kind == "sequence"), None)
    if target is None:
        return {}, notes
    try:
        target_map = _walk(target.sequence, *_residues(model))
    except ValueError as exc:
        return {}, [f"Confronto con il sito del template non disponibile: il modello non corrisponde al bersaglio "
                    f"dell'allineamento ({exc})."]
    contexts: dict = {}
    for t in templates:
        groups = t.get("ligand_groups") or []
        entry = next((e for e in entries if e.is_template and e.code == t["code"]), None)
        tmodel = template_models.get(t["code"])
        if not groups or entry is None or tmodel is None:
            continue
        fields = (entry.fields + [""] * 4)[:4]
        start = None
        if fields[0] and fields[0].upper() not in ("FIRST", "."):
            start = ((fields[1] or t["chains"][0]).strip(), fields[0].strip())
        try:
            tmap = _walk(entry.sequence, *_residues(tmodel, start))
        except ValueError as exc:
            notes.append(f"Confronto con il sito del template {t['code']} non disponibile: il PIR non corrisponde "
                         f"alla copia preparata ({exc}).")
            continue
        residue_map: dict[tuple[str, str], Optional[tuple[str, str, str]]] = {}
        pairs = []
        for col, tres in tmap.items():
            mres = target_map.get(col)
            if tres.id[0] == " ":
                key = (tres.get_parent().id, _resid(tres))
                residue_map[key] = None if mres is None or mres.id[0] != " " else group_key(mres)
            elif mres is not None and is_hetero(mres):
                pairs.append((group_key(tres), group_key(mres)))
        by_key = {(g["chain"], g["resnum"], g["resname"]): g for g in groups}
        for tkey, mkey in pairs:
            group = by_key.get(tkey)
            if group is not None:
                contexts.setdefault(mkey[:2], {"template": t["code"], "group": group, "map": residue_map})
    return contexts, notes


# ---------------------------------------------------------------------------------------------- verifica
def _worst(*statuses: str) -> str:
    return max(statuses, key=lambda s: _RANK[s]) if statuses else STATUS_GOOD


def _metal_report(metal_atom, residue, neighbors: _Neighbors, template_metal: Optional[dict],
                  residue_map: Optional[dict]) -> tuple[dict, list[str], list[str], str]:
    """Coordinazione di un atomo metallico: (rapporto, motivazioni, note positive, esito)."""
    element = element_of(metal_atom)
    sym = ELEMENT_LABEL.get(element, element.capitalize())
    donors = []
    reasons: list[str] = []
    status = STATUS_GOOD
    for atom, category, distance in _metal_donors(metal_atom, neighbors):
        expected = expected_distance(element, category)
        label, _ref, typical = DONORS[category]
        entry = {**_atom_ref(atom), "label": _atom_label(atom), "category": category, "donor": label,
                 "distance": round(distance, 2), "expected": expected, "water": is_water(atom.get_parent()),
                 "same_group": atom.get_parent() is residue, "coord": [round(float(x), 3) for x in atom.coord]}
        if entry["same_group"]:
            entry["label"] = f"{atom.get_id().strip()} dello stesso gruppo"
        if expected is None:
            entry["status"] = "insolito" if element in REFERENCE_DISTANCES else "senza riferimento"
            if element in REFERENCE_DISTANCES:
                reasons.append(f"Donatore insolito per {sym}: {label} di {_atom_label(atom)} a {_num(distance)} Å.")
                status = _worst(status, STATUS_CHECK)
        else:
            deviation = distance - expected
            entry["deviation"] = round(deviation, 2)
            if deviation < -SHELL_TOLERANCE:
                entry["status"] = "troppo corta"
                reasons.append(f"{sym}–{label} troppo vicino: {_atom_label(atom)} a {_num(distance)} Å (atteso "
                               f"{_num(expected)} Å).")
                status = _worst(status, STATUS_BAD)
            elif abs(deviation) > DISTANCE_TOLERANCE:
                entry["status"] = "corta" if deviation < 0 else "lunga"
                reasons.append(f"Distanza {entry['status']}: {sym}–{label} di {_atom_label(atom)} {_num(distance)} Å, "
                               f"attesa {_num(expected)} ± {_num(DISTANCE_TOLERANCE)} Å.")
                status = _worst(status, STATUS_CHECK)
            else:
                entry["status"] = "nella norma"
            if not typical:
                reasons.append(f"Donatore insolito per {sym}: {label} di {_atom_label(atom)}.")
                status = _worst(status, STATUS_CHECK)
        donors.append(entry)
    cn = len(donors)
    vectors = np.array([np.array(d["coord"]) - metal_atom.coord for d in donors]) if donors else np.zeros((0, 3))
    geo = geometry(vectors) if cn >= 2 else None
    report = {"atom": metal_atom.get_id().strip(), "element": element, "symbol": sym,
              "coord": [round(float(x), 3) for x in metal_atom.coord], "coordination_number": cn,
              "donors": donors, "geometry": geo, "cn_range": list(CN_RANGE[element]) if element in CN_RANGE else None,
              "reference": element in REFERENCE_DISTANCES}
    good: list[str] = []
    if element not in REFERENCE_DISTANCES:
        reasons.append(f"Distanze di riferimento non disponibili per {sym}: controllati solo i donatori entro "
                       f"{_num(FALLBACK_SHELL, 1)} Å e la geometria.")
        status = _worst(status, STATUS_CHECK)
    if cn == 0:
        reasons.append(f"Nessun atomo donatore (N, O, S) nella sfera di coordinazione: {sym} non è coordinato.")
        status = _worst(status, STATUS_BAD)

    # confronto con il sito del template
    waters = 0
    if template_metal is not None:
        template_report, template_reasons, template_status, waters = _compare_site(
            template_metal, donors, residue_map or {}, sym)
        report["template_site"] = template_report
        reasons.extend(template_reasons)
        status = _worst(status, template_status)
        if template_status == STATUS_GOOD and template_report["protein_donors"]:
            good.append(f"sito del template conservato ({template_report['conserved']} su "
                        f"{template_report['protein_donors']} donatori della proteina)")

    rng = CN_RANGE.get(element)
    effective = cn + waters
    if rng and cn and not rng[0] <= effective <= rng[1]:
        extra = (f" ({effective} con {waters} {'acqua' if waters == 1 else 'acque'} del template, non "
                 f"{'trasferita' if waters == 1 else 'trasferite'})") if waters else ""
        reasons.append(f"Numero di coordinazione {cn}{extra}, tipico per {sym} {rng[0]}–{rng[1]}.")
        status = _worst(status, STATUS_CHECK)
    if geo is not None:
        if geo["grmsd"] > GRMSD_OUTLIER:
            reasons.append(f"Geometria non riconoscibile: la più vicina è {geo['name']} con gRMSD "
                           f"{_num(geo['grmsd'], 1)}° (oltre {_num(GRMSD_OUTLIER, 1)}°).")
            status = _worst(status, STATUS_BAD)
        elif geo["grmsd"] > GRMSD_BORDERLINE:
            reasons.append(f"Geometria {geo['name']} distorta: gRMSD {_num(geo['grmsd'], 1)}° (oltre "
                           f"{_num(GRMSD_BORDERLINE, 1)}°).")
            status = _worst(status, STATUS_CHECK)
        if geo["vacancy"] > VACANCY_LIMIT + 1e-9:
            reasons.append(f"Geometria {geo['name']} con {geo['positions'] - cn} posizioni vacanti su "
                           f"{geo['positions']} ({_num(100 * geo['vacancy'], 0)}%, oltre il "
                           f"{_num(100 * VACANCY_LIMIT, 0)}%).")
            status = _worst(status, STATUS_CHECK)
    if cn:
        text = f"{sym} coordinato da {cn} {'atomo' if cn == 1 else 'atomi'}"
        if geo is not None:
            text += f", geometria {geo['name']} (gRMSD {_num(geo['grmsd'], 1)}°"
            vacant = geo["positions"] - cn
            text += f", {vacant} {'posizione vacante' if vacant == 1 else 'posizioni vacanti'})" if vacant else ")"
        good.insert(0, text)
    return report, reasons, good, status


def _compare_site(template_metal: dict, donors: list[dict], residue_map: dict, sym: str) \
        -> tuple[dict, list[str], str, int]:
    """Donatori della proteina del template riportati sul bersaglio attraverso l'allineamento.

    Per ogni residuo donatore del template: *conservato* (il residuo allineato del bersaglio coordina il metallo nel
    modello, anche se di altro tipo), *perso* (allineato ma non coordina), *sostituito* (allineato a un residuo che
    non può coordinare con la catena laterale), *non allineato* (gap nel bersaglio) o *non modellato* (catena o tratto
    del template fuori dall'allineamento). Meno della metà dei donatori conservati = sito non conservato.
    """
    model_protein = {(d["chain"], d["resnum"]) for d in donors if d.get("protein")}
    rows = []
    reasons: list[str] = []
    waters = sum(1 for d in template_metal.get("donors") or [] if d.get("water"))
    seen = set()
    for d in template_metal.get("donors") or []:
        key = (d["chain"], d["resnum"])
        if d.get("water") or not d.get("protein", True) or key in seen:
            continue  # acque e altri gruppi eteroatomici non si riportano; un residuo bidentato conta una volta
        seen.add(key)
        label = f"{d['resname'].capitalize()} {d['chain']}{d['resnum']}"
        row = {"template": label, "atom": d["atom"], "target": None}
        if key not in residue_map:
            row["status"] = "non modellato"
        elif residue_map[key] is None:
            row["status"] = "non allineato"
        else:
            chain, resnum, resname = residue_map[key]
            row["target"] = f"{resname.capitalize()} {chain}{resnum}"
            if (chain, resnum) in model_protein:
                row["status"] = "conservato"
            elif resname != d["resname"] and resname not in DONOR_RESIDUES and d["atom"] not in ("O", "OXT"):
                row["status"] = "sostituito"
            else:
                row["status"] = "perso"
        rows.append(row)
    lost = [r for r in rows if r["status"] != "conservato"]
    for r in lost:
        if r["status"] == "non allineato":
            reasons.append(f"Il donatore del template {r['template']} ({r['atom']}) non ha un residuo allineato nel "
                           "bersaglio.")
        elif r["status"] == "non modellato":
            reasons.append(f"Il donatore del template {r['template']} ({r['atom']}) è fuori dalle catene o dal "
                           "tratto modellati: il sito nel modello è incompleto.")
        elif r["status"] == "sostituito":
            reasons.append(f"Il donatore del template {r['template']} ({r['atom']}) è sostituito da "
                           f"{r['target']}, che non può coordinare {sym}.")
        else:
            reasons.append(f"{r['target']} (allineato a {r['template']}, donatore nel template) non coordina {sym} "
                           "nel modello.")
    conserved = len(rows) - len(lost)
    status = STATUS_GOOD
    if lost:
        status = STATUS_BAD if 2 * conserved < len(rows) else STATUS_CHECK
        if status == STATUS_BAD:
            reasons.append(f"Il sito del template non è conservato: {conserved} donatori della proteina su "
                           f"{len(rows)}.")
    mapped = {v[:2] for v in residue_map.values() if v}
    template_targets = {tuple(residue_map[(d["chain"], d["resnum"])][:2]) for d in template_metal.get("donors") or []
                        if residue_map.get((d["chain"], d["resnum"]))}
    new = sorted({f"{d['resname'].capitalize()} {d['chain']}{d['resnum']}" for d in donors
                  if d.get("protein") and (d["chain"], d["resnum"]) in mapped
                  and (d["chain"], d["resnum"]) not in template_targets})
    report = {"donors": rows, "protein_donors": len(rows), "conserved": conserved, "waters": waters, "new": new}
    return report, reasons, status, waters


def evaluate(model, *, contexts: Optional[dict] = None, notes: Iterable[str] = ()) -> Optional[dict]:
    """Verifica dei gruppi eteroatomici (non acqua, non residui modificati) di una struttura; None se non ce ne sono.

    `contexts` (modellazione, da `template_contexts`): per gruppo del modello, sito del template e corrispondenza dei
    residui; senza, la verifica riguarda solo la struttura (analisi di qualità).
    """
    groups = [r for r in model.get_residues() if is_hetero(r) and not is_modified_residue(r)]
    if not groups:
        return None
    contexts = contexts or {}
    neighbors = _Neighbors(model)
    # atomi metallici di tutti i gruppi e i loro donatori: la coordinazione non è uno scontro
    coordination: set[tuple[int, int]] = set()
    for res in groups:
        for atom in _heavy(res):
            if element_of(atom) in METAL_ELEMENTS:
                for donor, _cat, _dist in _metal_donors(atom, neighbors):
                    coordination.add((id(atom), id(donor)))
                    coordination.add((id(donor), id(atom)))
    out = []
    for res in groups:
        chain, resnum, resname = group_key(res)
        info = classify(res)
        ctx = contexts.get((chain, resnum))
        template_group = (ctx or {}).get("group")
        residue_map = (ctx or {}).get("map")
        reasons: list[str] = []
        good: list[str] = []
        status = STATUS_GOOD
        contacts: dict[tuple[str, str], dict] = {}
        clashes = []
        links = []
        template_links = {(lk["atom"], lk["partner"]["chain"], lk["partner"]["resnum"], lk["partner"]["atom"])
                          for lk in (template_group or {}).get("links") or []}
        for atom in _heavy(res):
            for other in neighbors.near(atom, CONTACT_DISTANCE):
                ores = other.get_parent()
                if ores is res or is_water(ores):
                    continue
                distance = float(np.linalg.norm(other.coord - atom.coord))
                if _is_protein(ores):
                    key = (ores.get_parent().id, _resid(ores))
                    if key not in contacts or distance < contacts[key]["distance"]:
                        contacts[key] = {"chain": key[0], "resnum": key[1], "resname": ores.get_resname().strip(),
                                         "distance": round(distance, 2)}
                if distance >= CLASH_DISTANCE or (id(atom), id(other)) in coordination:
                    continue
                pair = {"atom": atom.get_id().strip(), "partner": _atom_label(other), "distance": round(distance, 2)}
                if _covalent_range(atom, other, distance):
                    # distanza di legame: senza template è un possibile legame covalente (glicani, eme c...); nel
                    # modello è un legame solo se il template ha lo stesso legame con il residuo allineato
                    if ctx is None:
                        links.append({**pair, "confirmed": False})
                        continue
                    tkey = next((k for k, v in (residue_map or {}).items() if v and v[:2] ==
                                 (ores.get_parent().id, _resid(ores))), None)
                    if tkey and (atom.get_id().strip(), tkey[0], tkey[1], other.get_id().strip()) in template_links:
                        links.append({**pair, "confirmed": True})
                        continue
                clashes.append(pair)
        clashes.sort(key=lambda c: c["distance"])
        for c in clashes[:5]:
            reasons.append(f"Scontro: {c['atom']} di {resname} a {_num(c['distance'])} Å da {c['partner']} (sotto "
                           f"{_num(CLASH_DISTANCE, 1)} Å).")
        if len(clashes) > 5:
            reasons.append(f"… e altri {len(clashes) - 5} scontri.")
        if clashes:
            status = STATUS_BAD
        for lk in links:
            good.append(f"{'legame covalente' if lk['confirmed'] else 'possibile legame covalente'} con "
                        f"{lk['partner']} ({_num(lk['distance'])} Å)")
        metals = []
        template_metals = {m["atom"]: m for m in (template_group or {}).get("metals") or []}
        for atom in _heavy(res):
            if element_of(atom) not in METAL_ELEMENTS:
                continue
            report, metal_reasons, metal_good, metal_status = _metal_report(
                atom, res, neighbors, template_metals.get(atom.get_id().strip()) if ctx else None, residue_map)
            metals.append(report)
            reasons.extend(metal_reasons)
            good.extend(metal_good)
            status = _worst(status, metal_status)
        protein_contacts = sorted(contacts.values(), key=lambda c: c["distance"])
        if not protein_contacts:
            reasons.append(f"Nessun residuo della proteina entro {_num(CONTACT_DISTANCE, 0)} Å: il gruppo non "
                           "interagisce con la struttura.")
            status = _worst(status, STATUS_CHECK)
        elif not clashes:
            good.append(f"{len(protein_contacts)} {'residuo' if len(protein_contacts) == 1 else 'residui'} entro "
                        f"{_num(CONTACT_DISTANCE, 0)} Å, nessuno scontro")
        if status == STATUS_GOOD:
            text = "; ".join(good) or "nessun problema rilevato"
            text = text[0].upper() + text[1:] + "."
        else:
            text = " ".join(reasons)
        entry = {"resname": resname, "chain": chain, "resnum": resnum, "atoms": len(res), "class": info["class"],
                 "label": CLASS_LABEL[info["class"]], "status": status, "summary": text, "reasons": reasons,
                 "contacts": protein_contacts, "clashes": clashes, "links": links, "metals": metals}
        if info.get("description"):
            entry["description"] = info["description"]
        if ctx is not None:
            entry["template"] = {"code": ctx["template"], "group": group_label(template_group["chain"],
                                                                               template_group["resnum"],
                                                                               template_group["resname"])}
        out.append(entry)
    counts = {s: sum(1 for g in out if g["status"] == s) for s in (STATUS_GOOD, STATUS_CHECK, STATUS_BAD)}
    return {
        "version": VERSION, "groups": out, "counts": counts,
        "status": _worst(*(g["status"] for g in out)),
        "template_compared": bool(contexts),
        "thresholds": {"contact": CONTACT_DISTANCE, "clash": CLASH_DISTANCE, "distance_tolerance": DISTANCE_TOLERANCE,
                       "shell_tolerance": SHELL_TOLERANCE, "grmsd_borderline": GRMSD_BORDERLINE,
                       "grmsd_outlier": GRMSD_OUTLIER, "vacancy": VACANCY_LIMIT},
        "notes": list(notes),
    }


def summary(evaluation: Optional[dict]) -> Optional[dict]:
    """Riepilogo compatto per le tabelle dei modelli: numero di gruppi, esito peggiore e conteggi."""
    if not evaluation:
        return None
    return {"groups": len(evaluation["groups"]), "status": evaluation["status"], "counts": evaluation["counts"]}
