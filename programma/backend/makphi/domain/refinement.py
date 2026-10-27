"""Rifinitura energetica (F9.1): protocollo dichiarato, copia pulita per OpenMM e confronto prima/dopo. Logica pura;
OpenMM e PDBFixer li esegue `engines/refine_worker.py` in un processo separato.

**Protocollo** (costanti di questo modulo, riportate nel risultato del job):

1. **Copia pulita** (`clean_input`): primo modello, prima altloc, solo i residui amminoacidici (anche quelli modificati
   con un C-alfa, es. MSE, che PDBFixer sostituisce con il residuo standard), senza acque, ligandi, ioni e idrogeni.
   I gruppi eteroatomici sono esclusi con un avviso: il campo di forza non ha parametri per loro e un ligando senza
   parametri non si può tenere fermo in modo affidabile. Alle interruzioni della catena (C–N > 2,0 Å, come
   `structure.chain_summaries`) si inserisce un TER: OpenMM altrimenti legherebbe i due residui lontani con un legame
   peptidico e la minimizzazione li avvicinerebbe. Nessun residuo mancante viene ricostruito: le estremità
   dell'interruzione diventano estremità cariche (avviso).
2. **PDBFixer** (Eastman et al., PLoS Comput. Biol. 13:e1005659, 2017): atomi pesanti mancanti e idrogeni a pH 7
   (varianti dei residui titolabili alla pKa del residuo libero, istidine dai legami idrogeno; regole di
   `Modeller.addHydrogens` di OpenMM).
3. **Campo di forza** AMBER ff14SB (Maier et al., J. Chem. Theory Comput. 11:3696, 2015; `amber14-all.xml`) con
   solvente implicito **OBC2** (Onufriev, Bashford & Case, Proteins 55:383, 2004; raggi mbondi2, dielettrici 1 e 78,5,
   termine di superficie ACE, nessun sale) nella forza nativa `GBSAOBCForce`. **Perché OBC2 e non GBn2** (Nguyen, Roe &
   Simmerling, J. Chem. Theory Comput. 9:2020, 2013, più accurato e raccomandato con ff14SB): in OpenMM GBn2 esiste solo
   come `CustomGBForce`, che sulla piattaforma CPU (l'unica nel container) è molto più lenta. Misurato su 1UBQ (1231
   atomi con gli idrogeni, i3-1115G4, 4 thread): GBn2 201 ms per passo, OBC2 nativo 21 ms (energia uguale a
   `implicit/obc2.xml` entro 0,003 kJ/mol); la minimizzazione con GBn2 durava 370 s contro 15 s. Con le restrizioni
   sulla catena principale il modello di solvente cambia poco la geometria finale; il costo, invece, decide se la
   rifinitura è usabile. Oltre `CUTOFF_ATOMS` atomi (costo O(N²) senza taglio) le interazioni non legate e GB si
   troncano a `CUTOFF_NM` (CutoffNonPeriodic, dielettrico di campo di reazione 1 come gli script implicit di OpenMM).
4. **Restrizioni** armoniche sugli atomi pesanti della catena principale (N, CA, C, O) verso le posizioni di partenza:
   E = k·|r − r₀|² con k = 10 kcal·mol⁻¹·Å⁻² (convenzione di `restraint_wt` di AMBER, senza ½), cioè 4184
   kJ·mol⁻¹·nm⁻². Le catene laterali e gli idrogeni sono liberi.
5. **Minimizzazione** L-BFGS (`LocalEnergyMinimizer`) fino a una forza massima di 10 kJ·mol⁻¹·nm⁻¹ (predefinito di
   OpenMM) o `MIN_ITERATIONS` iterazioni.
6. **Dinamica facoltativa**: Langevin (`LangevinMiddleIntegrator`) a 300 K, attrito 1 ps⁻¹, passo 2 fs con i legami
   con gli idrogeni vincolati, stesse restrizioni, durata scelta fino a `MD_MAX_PS` (predefinita `MD_DEFAULT_PS`), seme
   fisso; poi una seconda minimizzazione. Una dinamica così breve serve a uscire da minimi locali vicini, non a
   campionare.

**Energie:** energia potenziale del campo di forza (senza il termine delle restrizioni, riportato a parte) prima della
minimizzazione (struttura con gli atomi e gli idrogeni aggiunti da PDBFixer) e alla fine, con le componenti (legami,
angoli, torsioni, non legate, solvatazione). Valgono per confrontare prima e dopo sulla stessa struttura, non fra
strutture diverse.

**Confronto prima/dopo** (`compare`): le stesse metriche di `engines/quality` sulla copia pulita (prima) e sul modello
rifinito (dopo), più l'RMSD della catena principale (N, CA, C, O) dopo la sovrapposizione di Kabsch
(`comparison.kabsch_rmsd`) e lo spostamento massimo senza sovrapposizione.
"""

from __future__ import annotations

import io
from typing import Optional

import numpy as np

from . import comparison, structure

# ------------------------------------------------------------------------------------------ protocollo
FORCEFIELD = "AMBER ff14SB (amber14-all.xml)"
SOLVENT = "OBC2 (GBSAOBCForce nativa, raggi mbondi2, dielettrici 1/78,5, ACE)"
PH = 7.0
BACKBONE = ("N", "CA", "C", "O")
RESTRAINT_KCAL_A2 = 10.0
KCAL_TO_KJ = 4.184
MIN_TOLERANCE = 10.0       # kJ/mol/nm
MIN_ITERATIONS = 5000
MD_TEMPERATURE = 300.0     # K
MD_FRICTION = 1.0          # 1/ps
MD_TIMESTEP_FS = 2.0
MD_DEFAULT_PS = 10.0
MD_MAX_PS = 50.0
MD_SEED = 20240611
CUTOFF_ATOMS = 4000
CUTOFF_NM = 2.0
BREAK_DISTANCE = 2.0       # Å, C–N oltre cui la catena è interrotta (come structure.chain_summaries)
CA_BREAK_DISTANCE = 4.2    # Å, Cα–Cα se manca un atomo del legame peptidico


def restraint_k_openmm(k_kcal_a2: float = RESTRAINT_KCAL_A2) -> float:
    """Costante delle restrizioni da kcal·mol⁻¹·Å⁻² a kJ·mol⁻¹·nm⁻² (unità di OpenMM): × 4,184 × 100."""
    return k_kcal_a2 * KCAL_TO_KJ * 100.0


def protocol(md_ps: float = 0.0, keep_hydrogens: bool = False) -> dict:
    """Parametri della rifinitura, nel risultato del job e nell'interfaccia."""
    return {
        "forcefield": FORCEFIELD, "solvent": SOLVENT, "ph": PH,
        "restraint": {"atoms": list(BACKBONE), "k_kcal_mol_A2": RESTRAINT_KCAL_A2,
                      "k_kj_mol_nm2": restraint_k_openmm(), "form": "k·|r − r0|²"},
        "minimization": {"tolerance_kj_mol_nm": MIN_TOLERANCE, "max_iterations": MIN_ITERATIONS},
        "md": None if md_ps <= 0 else {"ps": md_ps, "temperature_k": MD_TEMPERATURE, "friction_ps": MD_FRICTION,
                                        "timestep_fs": MD_TIMESTEP_FS, "steps": md_steps(md_ps), "seed": MD_SEED},
        "cutoff": {"atoms_over": CUTOFF_ATOMS, "nm": CUTOFF_NM},
        "hydrogens_in_output": keep_hydrogens,
    }


def md_steps(md_ps: float) -> int:
    return int(round(md_ps * 1000.0 / MD_TIMESTEP_FS))


def validate_md(md_ps: float) -> float:
    md_ps = float(md_ps or 0.0)
    if md_ps < 0 or md_ps > MD_MAX_PS:
        raise ValueError(f"la dinamica dura da 0 a {MD_MAX_PS:g} ps")
    return md_ps


# ------------------------------------------------------------------------------------------ copia pulita
def _is_polymer_residue(residue) -> bool:
    """Amminoacido standard, oppure modificato (HETATM con C-alfa e lettera nota, es. MSE)."""
    if residue.get_resname().strip() in structure.WATER_NAMES or residue.id[0] == "W":
        return False
    if residue.id[0] == " ":
        return "CA" in residue
    return "CA" in residue and structure.three_to_one(residue.get_resname()) != "X"


def _linked(prev, cur) -> bool:
    """Due residui consecutivi sono legati: C–N ≤ 2,0 Å; senza quegli atomi, Cα–Cα ≤ 4,2 Å."""
    if "C" in prev and "N" in cur:
        return (prev["C"] - cur["N"]) <= BREAK_DISTANCE
    return (prev["CA"] - cur["CA"]) <= CA_BREAK_DISTANCE


def clean_input(model) -> tuple[str, dict]:
    """Copia pulita per OpenMM (testo PDB) e cosa è stato tolto o segnato: gruppi eteroatomici esclusi, residui
    modificati tenuti, idrogeni tolti, interruzioni della catena (con un TER prima del residuo che riprende)."""
    from Bio.PDB import PDBIO, Select

    excluded: list[str] = []
    modified: list[str] = []
    breaks: list[dict] = []
    starts: set[tuple[str, str]] = set()
    keep: set[tuple[str, tuple]] = set()
    hydrogens = 0
    for chain in model:
        prev = None
        for res in chain:
            label = f"{res.get_resname().strip()} {chain.id}{structure._resid(res)}"
            if not _is_polymer_residue(res):
                if res.id[0] != "W" and res.get_resname().strip() not in structure.WATER_NAMES:
                    excluded.append(label)
                continue
            keep.add((chain.id, res.id))
            if res.id[0] != " ":
                modified.append(label)
            hydrogens += sum(1 for a in res if (a.element or "").upper() in ("H", "D"))
            if prev is not None and not _linked(prev, res):
                breaks.append({"chain": chain.id, "after": structure._resid(prev), "before": structure._resid(res)})
                # catena senza identificativo (colonna 22 vuota): Biopython la chiama ' ', il testo la rilegge vuota
                starts.add((chain.id.strip(), structure._resid(res)))
            prev = res
    if not keep:
        raise ValueError("nessun residuo amminoacidico da rifinire")

    class _Clean(Select):
        def accept_model(self, m) -> bool:
            return m.id == model.id

        def accept_residue(self, residue) -> bool:
            return (residue.get_parent().id, residue.id) in keep

        def accept_atom(self, atom) -> bool:
            if (atom.element or "").upper() in ("H", "D"):
                return False
            return (not atom.is_disordered()) or atom.get_altloc() in ("A", " ", "1")

    buffer = io.StringIO()
    pdbio = PDBIO()
    pdbio.set_structure(model.get_parent())
    pdbio.save(buffer, select=_Clean())
    text = split_chain_breaks(buffer.getvalue(), starts)
    return text, {"excluded": excluded, "modified": modified, "breaks": breaks, "hydrogens_removed": hydrogens,
                  "residues": len(keep)}


def split_chain_breaks(pdb_text: str, starts: set[tuple[str, str]]) -> str:
    """Inserisce un TER prima del primo atomo di ogni residuo in `starts` ((catena, residuo)): OpenMM vi chiude la
    catena e ne apre una nuova con lo stesso identificativo, senza legame peptidico fra i due tratti.

    Le catene si confrontano senza spazi: una catena senza identificativo (colonna 22 vuota, comune nei file di
    dinamica molecolare o scritti a mano) è ' ' per Biopython e '' nel testo, e deve avere il suo TER."""
    starts = {(chain.strip(), resid) for chain, resid in starts}
    if not starts:
        return pdb_text
    out: list[str] = []
    seen: set[tuple[str, str]] = set()
    for line in pdb_text.splitlines():
        if line.startswith(("ATOM", "HETATM")):
            try:
                key = (line[21:22].strip(), f"{int(line[22:26])}{line[26:27].strip()}")
            except ValueError:
                key = None
            if key in starts and key not in seen:
                seen.add(key)
                if not (out and out[-1].startswith("TER")):
                    out.append("TER")
        out.append(line)
    return "\n".join(out) + "\n"


# ------------------------------------------------------------------------------------------ confronto prima/dopo
def backbone_coordinates(model) -> dict[tuple[str, str, str], np.ndarray]:
    """Coordinate degli atomi della catena principale (N, CA, C, O) per (catena, residuo, atomo)."""
    out = {}
    for chain in model:
        for res in chain:
            if not _is_polymer_residue(res):
                continue
            for name in BACKBONE:
                if name in res:
                    out[(chain.id, structure._resid(res), name)] = res[name].coord.astype(float)
    return out


def backbone_rmsd(before, after) -> dict:
    """RMSD della catena principale fra due modelli di Biopython sugli atomi in comune: dopo la sovrapposizione di
    Kabsch (`comparison.kabsch_rmsd`) e senza sovrapposizione (le restrizioni tengono le posizioni assolute), con lo
    spostamento massimo e l'atomo che lo ha."""
    a = backbone_coordinates(before)
    b = backbone_coordinates(after)
    keys = [k for k in a if k in b]
    if len(keys) < 3:
        return {"atoms": len(keys), "rmsd": None, "rmsd_no_fit": None, "max_displacement": None, "max_atom": None}
    xa = np.array([a[k] for k in keys])
    xb = np.array([b[k] for k in keys])
    shift = np.sqrt(((xa - xb) ** 2).sum(axis=1))
    worst = int(shift.argmax())
    chain, resnum, name = keys[worst]
    return {"atoms": len(keys), "rmsd": comparison.kabsch_rmsd(xb, xa),
            "rmsd_no_fit": round(float(np.sqrt((shift ** 2).mean())), 3),
            "max_displacement": round(float(shift[worst]), 3), "max_atom": f"{name} {chain}{resnum}"}


# metriche del confronto: chiave in quality.summary_metrics, etichetta, verso del miglioramento (+1 = più alto è
# meglio), cifre decimali
COMPARED = (
    ("grade_score", "Punteggio MakPhi", 1, 1),
    ("rama_favored", "Ramachandran favorite (%)", 1, 1),
    ("rama_outliers", "Ramachandran outlier (%)", -1, 1),
    ("clashscore", "Clashscore", -1, 2),
    ("dope", "DOPE", -1, 0),
    ("zdope", "zDOPE", -1, 2),
)
# differenze più piccole di queste sono "invariato" (arrotondamento dei rapporti)
UNCHANGED = {"grade_score": 0.05, "rama_favored": 0.05, "rama_outliers": 0.05, "clashscore": 0.005, "dope": 0.5,
             "zdope": 0.005}


def compare(before: dict, after: dict) -> list[dict]:
    """Righe del confronto prima/dopo dalle metriche compatte (`quality.summary_metrics`): valori, differenza ed
    esito (meglio, peggio, invariato; None se una delle due manca)."""
    rows = []
    for key, label, sign, digits in COMPARED:
        b, a = before.get(key), after.get(key)
        delta = None if a is None or b is None else round(float(a) - float(b), digits + 1)
        if delta is None:
            verdict: Optional[str] = None
        elif abs(delta) <= UNCHANGED[key]:
            verdict = "invariato"
        else:
            verdict = "meglio" if delta * sign > 0 else "peggio"
        rows.append({"key": key, "label": label, "before": b, "after": a, "delta": delta, "verdict": verdict,
                     "higher_is_better": sign > 0})
    return rows


def energy_change(energy: dict) -> dict:
    """Energia del campo di forza prima e dopo (kJ/mol), differenza e componenti, dal risultato del processo."""
    before = (energy.get("before") or {}).get("total")
    after = (energy.get("final") or {}).get("total")
    return {"before": before, "after": after,
            "delta": None if before is None or after is None else round(after - before, 1),
            "decreased": before is not None and after is not None and after < before,
            "restraint_final": energy.get("restraint_final"), "unit": "kJ/mol"}
