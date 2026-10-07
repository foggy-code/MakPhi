"""Calcoli Python dell'analisi di qualità che possono girare in un processo di appoggio (core/offload.py).

Ramachandran, clash e superficie accessibile (Shrake-Rupley) sono Python puro: nel processo di BioLoom occupano il GIL
e, con molte coppie di una pipeline in parallelo, diventano il collo di bottiglia (MODELLER e gli altri programmi sono
già processi separati). `python_steps` li esegue su un file di struttura in un altro processo e restituisce gli stessi
risultati che l'analisi calcolerebbe in casa; un passo non riuscito manca dal risultato e l'analisi lo rifà nel processo
principale, con lo stesso errore che avrebbe dato. Il modulo importa solo la logica pura (si carica in fretta nei
processi di appoggio).
"""

from __future__ import annotations

from pathlib import Path

from . import clashes, ramachandran, structure

# ASA massima teorica per residuo (Tien et al. 2013), per la SASA relativa.
MAX_ASA = {"A": 129, "R": 274, "N": 195, "D": 193, "C": 167, "Q": 225, "E": 223, "G": 104, "H": 224, "I": 197,
           "L": 201, "K": 236, "M": 224, "F": 240, "P": 159, "S": 155, "T": 172, "W": 285, "Y": 263, "V": 174}


def relative_sasa(model) -> dict[tuple[str, str], float]:
    """SASA relativa per residuo amminoacidico (Shrake-Rupley di Biopython), chiave (catena, residuo)."""
    from Bio.PDB.SASA import ShrakeRupley

    # Le acque cristallografiche coprirebbero la superficie: si calcola su una copia senza solvente.
    model = model.copy()
    for chain in model:
        for res in list(chain):
            if res.id[0] == "W" or res.get_resname().strip() in structure.WATER_NAMES:
                chain.detach_child(res.id)
    sr = ShrakeRupley()
    sr.compute(model, level="R")
    out = {}
    for chain in model:
        for res in chain:
            if structure.is_amino_residue(res):
                aa = structure.three_to_one(res.get_resname())
                max_asa = MAX_ASA.get(aa)
                if max_asa:
                    out[(chain.id, structure._resid(res))] = round(min(1.5, res.sasa / max_asa), 3)
    return out


STEPS = (("Ramachandran", ramachandran.analyze), ("clash", clashes.analyze), ("SASA", relative_sasa))


def python_steps(path: str, name: str) -> dict:
    """Esiti dei passi Python sulla struttura in `path` (letta come la legge l'analisi); i passi non riusciti mancano."""
    st = structure.load_structure(Path(path), name)
    model = structure.first_model(st)
    out = {}
    for key, fn in STEPS:
        try:
            out[key] = fn(model)
        except Exception:  # rifatto nel processo principale, che dà l'errore come nella versione senza appoggio
            pass
    return out
