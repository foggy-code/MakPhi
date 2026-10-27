"""Interfacce fra catene: residui d'interfaccia, area sepolta, interazioni e plausibilità biologica (F7).

Logica pura (Biopython `ShrakeRupley`, numpy). Per una struttura con più catene proteiche (primo modello; soli
amminoacidi senza idrogeni: acque, ligandi e ioni esclusi, come nelle interazioni):

- **SASA** per atomo con Shrake & Rupley 1973 (sonda 1,4 Å, 100 punti per atomo, raggi di Biopython) nel complesso,
  in ogni coppia di catene e in ogni catena isolata. Aggiungere atomi può solo coprire punti della superficie, quindi
  ΔSASA = SASA isolata − SASA nel complesso non è mai negativa.
- **Residui d'interfaccia:** ΔSASA > 1 Å² fra la catena isolata e il complesso (o la coppia), come PISA (Krissinel &
  Henrick 2007) e Janin et al. 2008.
- **Coppie di catene:** quelle con due atomi entro 2 × (raggio massimo + sonda) = 6,4 Å, le sole la cui SASA può
  cambiare. Per ogni coppia la SASA si ricalcola sulle due catene da sole: con più di due catene, i residui sepolti
  da una terza catena non entrano nell'interfaccia della coppia.
- **Area d'interfaccia** = (Σ ΔSASA delle due catene) / 2, cioè metà dell'area sepolta totale B (convenzione di PISA).
- **Composizione:** classi dei residui d'interfaccia (apolari G A V L I M P F W; polari S T C N Q Y H; carichi D E K
  R) e frazione apolare dell'area sepolta (atomi di C e S rispetto a N e O).
- **Interazioni attraverso l'interfaccia:** quelle di `domain/interactions.analyze` con le estremità nelle due catene.
- **Plausibilità biologica** (indicatore semplice, sulla sola area): < 400 Å² bassa, nell'intervallo dei contatti di
  impaccamento cristallino (B < 800 Å², Janin et al. 2008); 400–600 Å² incerta; ≥ 600 Å² compatibile, come le
  interfacce proteina-proteina standard (B 1200–2000 Å², Lo Conte, Chothia & Janin 1999). Limiti: l'area da sola
  sbaglia per una parte delle interfacce (le più piccole biologiche e le più grandi cristalline si sovrappongono);
  non valuta la conservazione né la complementarità chimica (PISA, EPPIC); in un modello per omologia l'interfaccia
  è quella del template, e in una struttura sintetica o in un'unità asimmetrica può non essere l'assemblaggio
  biologico.
"""

from __future__ import annotations

from typing import Callable, Optional

from .structure import WATER_NAMES, _resid, is_amino_residue, three_to_one

VERSION = 1
PROBE = 1.4
N_POINTS = 100
MAX_RADIUS = 1.8  # raggio più grande fra gli elementi delle proteine (S) nei raggi di Biopython
CONTACT_RADIUS = 2 * (MAX_RADIUS + PROBE)
INTERFACE_MIN_DELTA = 1.0  # Å²
AREA_LOW = 400.0  # Å², sotto: tipica dei contatti cristallini (Janin et al. 2008)
AREA_HIGH = 600.0  # Å², sopra: come le interfacce standard (Lo Conte et al. 1999)
APOLAR = set("GAVLIMPFW")
POLAR = set("STCNQYH")
CHARGED = set("DEKR")
APOLAR_ELEMENTS = {"C", "S"}

PLAUSIBILITY = {
    "bassa": f"area sotto {AREA_LOW:.0f} Å²: nell'intervallo dei contatti di impaccamento cristallino "
             f"(area sepolta B < {2 * AREA_LOW:.0f} Å², Janin et al. 2008)",
    "incerta": f"area fra {AREA_LOW:.0f} e {AREA_HIGH:.0f} Å²: la sola area non distingue un contatto cristallino da "
               "un'interfaccia biologica piccola",
    "compatibile": f"area di almeno {AREA_HIGH:.0f} Å²: come le interfacce proteina-proteina standard (B 1200–2000 Å², "
                   "Lo Conte, Chothia & Janin 1999)",
}
LIMITS = ("Indicatore basato sulla sola area: le interfacce biologiche piccole e i contatti cristallini grandi si "
          "sovrappongono; non valuta conservazione né complementarità chimica (per questo PISA o EPPIC). In un modello "
          "per omologia l'interfaccia è quella del template; un'unità asimmetrica o una struttura sintetica può non "
          "essere l'assemblaggio biologico.")


def _element(atom) -> str:
    el = (atom.element or "").strip().upper()
    if el and el != "X":
        return el
    return atom.get_id().strip().lstrip("0123456789")[:1].upper()


def protein_copy(model):
    """Copia del modello con i soli amminoacidi senza idrogeni; le catene senza amminoacidi escono."""
    copy = model.copy()
    for chain in list(copy):
        for res in list(chain):
            if not is_amino_residue(res) or res.get_resname().strip() in WATER_NAMES:
                chain.detach_child(res.id)
                continue
            for atom in list(res):
                if _element(atom) in ("H", "D"):
                    res.detach_child(atom.get_id())
        if len(chain) == 0:
            copy.detach_child(chain.id)
    return copy


def _sasa(entity) -> dict[tuple[str, tuple], tuple[float, float]]:
    """SASA per residuo (catena, id del residuo) → (totale, parte apolare) in Å²."""
    from Bio.PDB.SASA import ShrakeRupley

    ShrakeRupley(probe_radius=PROBE, n_points=N_POINTS).compute(entity, level="A")
    out: dict[tuple[str, tuple], tuple[float, float]] = {}
    for atom in entity.get_atoms():
        res = atom.get_parent()
        key = (res.get_parent().id, res.id)
        total, apolar = out.get(key, (0.0, 0.0))
        value = float(atom.sasa)
        out[key] = (total + value, apolar + (value if _element(atom) in APOLAR_ELEMENTS else 0.0))
    return out


def _pair_model(chains: list):
    from Bio.PDB.Model import Model

    model = Model(0)
    for chain in chains:
        model.add(chain.copy())
    return model


def _composition(residues: list[dict]) -> dict:
    letters = [r["aa"] for r in residues]
    n = len(letters) or 1
    counts = {"apolari": sum(1 for a in letters if a in APOLAR), "polari": sum(1 for a in letters if a in POLAR),
              "carichi": sum(1 for a in letters if a in CHARGED)}
    counts["altri"] = len(letters) - sum(counts.values())
    return {"counts": counts, "percent": {k: round(100.0 * v / n, 1) for k, v in counts.items()}}


def plausibility(area: float) -> dict:
    level = "bassa" if area < AREA_LOW else "incerta" if area < AREA_HIGH else "compatibile"
    return {"level": level, "text": PLAUSIBILITY[level], "limits": LIMITS,
            "thresholds": {"low": AREA_LOW, "high": AREA_HIGH}}


def _crossing(interactions: Optional[dict], a: str, b: str) -> dict:
    """Interazioni con un'estremità in ciascuna delle due catene."""
    data = interactions or {}

    def across(entries: list) -> list:
        return [e for e in entries if {e["a"]["chain"], e["b"]["chain"]} == {a, b}]

    arom = across(data.get("aromatic") or [])
    return {"hbonds": len(across(data.get("hbonds") or [])), "salt_bridges": len(across(data.get("salt_bridges") or [])),
            "disulfides": len(across(data.get("disulfides") or [])),
            "pi_stacking": sum(1 for e in arom if e["type"] != "cation_pi"),
            "cation_pi": sum(1 for e in arom if e["type"] == "cation_pi")}


def analyze(model, interactions: Optional[dict] = None, check: Optional[Callable[[], None]] = None) -> dict:
    """Interfacce del modello. `interactions`: risultato di `domain/interactions.analyze` sullo stesso modello, per
    le interazioni attraverso ogni interfaccia; `check` si chiama fra un calcolo della SASA e l'altro
    (annullamento)."""
    from Bio.PDB import NeighborSearch

    def tick() -> None:
        if check is not None:
            check()

    protein = protein_copy(model)
    chains = [c for c in protein]
    base = {"version": VERSION,
            "method": f"Shrake-Rupley (Biopython), sonda {PROBE:.1f} Å, {N_POINTS} punti".replace(".", ","),
            "chains": [c.id for c in chains], "min_delta": INTERFACE_MIN_DELTA, "pairs": [], "residues": []}
    if len(chains) < 2:
        return {**base, "note": "Una sola catena proteica: nessuna interfaccia."}
    info = {(c.id, r.id): r for c in chains for r in c}
    complex_sasa = _sasa(protein)
    tick()
    isolated: dict[tuple[str, tuple], tuple[float, float]] = {}
    for chain in chains:
        isolated.update(_sasa(chain.copy()))
        tick()

    def row(key: tuple, delta: float, apolar: float) -> dict:
        res = info[key]
        return {"chain": key[0], "resnum": _resid(res), "resname": res.get_resname().strip(),
                "aa": three_to_one(res.get_resname()), "delta": round(delta, 1), "apolar_delta": round(apolar, 1)}

    residues = []
    for key in info:
        total, apolar = isolated[key]
        c_total, c_apolar = complex_sasa[key]
        residues.append({**row(key, total - c_total, apolar - c_apolar), "sasa": round(c_total, 1),
                         "sasa_isolated": round(total, 1)})

    ns = NeighborSearch(list(protein.get_atoms()))
    by_id = {c.id: c for c in chains}
    order = {c.id: k for k, c in enumerate(chains)}
    contact = sorted({tuple(sorted((a.id, b.id), key=order.get)) for a, b in ns.search_all(CONTACT_RADIUS, level="C")
                      if a.id != b.id}, key=lambda p: (order[p[0]], order[p[1]]))
    pairs = []
    for a, b in contact:
        pair_sasa = complex_sasa if len(chains) == 2 else _sasa(_pair_model([by_id[a], by_id[b]]))
        tick()
        sides: dict[str, list[dict]] = {a: [], b: []}
        buried = apolar_buried = 0.0
        for key in info:
            if key[0] not in sides:
                continue
            total, apolar = isolated[key]
            p_total, p_apolar = pair_sasa[key]
            delta, apolar_delta = total - p_total, apolar - p_apolar
            buried += delta
            apolar_buried += apolar_delta
            if delta > INTERFACE_MIN_DELTA:
                sides[key[0]].append(row(key, delta, apolar_delta))
        if not sides[a] and not sides[b]:
            continue  # catene vicine ma nessun residuo sepolto oltre la soglia
        area = buried / 2.0
        pairs.append({
            "chains": [a, b], "area": round(area, 1), "buried": round(buried, 1),
            "apolar_fraction": round(apolar_buried / buried, 3) if buried > 0 else None,
            "residues": sides, "counts": {a: len(sides[a]), b: len(sides[b])},
            "composition": _composition(sides[a] + sides[b]),
            "interactions": _crossing(interactions, a, b), "plausibility": plausibility(area),
        })
    return {**base, "pairs": pairs, "residues": residues,
            "interface_residues": sum(1 for r in residues if r["delta"] > INTERFACE_MIN_DELTA)}
