"""Confronto strutturale per residuo: distanze Cα dopo la sovrapposizione, GDT-TS e GDT-HA, lDDT sui Cα.

La struttura A è il **riferimento** (B si sovrappone su A): GDT e lDDT sono normalizzati sui residui di A e il raggio
di inclusione del lDDT si misura su A. La corrispondenza tra i residui è quella dell'allineamento (di sequenza nel
percorso integrato, strutturale con TM-align/US-align) ed è fissata: nessuna metrica la cambia.

| Metrica | Definizione | Fonte |
|---|---|---|
| distanza per coppia | ‖Cα_A − Cα_B‖ dopo la sovrapposizione che massimizza il TM-score | Zhang & Skolnick, Proteins 57:702, 2004 |
| RMSD entro 5 Å | RMSD delle sole coppie a meno di 5 Å nella stessa sovrapposizione (le coppie ":" di TM-align) | Zhang & Skolnick, Nucleic Acids Res 33:2302, 2005 |
| GDT-TS | (P1 + P2 + P4 + P8) / 4 | Zemla, Nucleic Acids Res 31:3370, 2003 (LGA); Zemla et al., Proteins S3:22, 1999 (CASP3) |
| GDT-HA | (P0,5 + P1 + P2 + P4) / 4 | Read & Chavali, Proteins 69 S8:27, 2007 (CASP7, alta accuratezza) |
| lDDT | media delle frazioni di distanze conservate entro 0,5, 1, 2, 4 Å | Mariani et al., Bioinformatics 29:2722, 2013 |

**GDT.** Pd è la percentuale dei Cα di A (sul numero totale dei residui di A, non delle coppie) che stanno entro d Å
(estremo compreso) dal Cα corrispondente di B. Come in LGA, Pd è il massimo su sovrapposizioni diverse, scelte per
ogni soglia: non è calcolato sulla sola sovrapposizione del TM-score. Ricerca per soglia a sottoinsiemi iterativi,
con gli stessi semi della ricerca del TM-score (`geometry.tm_score_search`): frammenti contigui delle coppie
allineate di lunghezza L, L/2, L/4… fino a 4, con passo di mezzo frammento, più le sovrapposizioni ricevute (quella
del TM-score). Da ogni seme: Kabsch sulle coppie entro d, di nuovo le coppie entro d, fino a quando l'insieme non
cambia (al massimo 20 passi). Ogni sovrapposizione provata conta per tutte le soglie. La ricerca è euristica, come
quella di LGA: il valore è il massimo sulle sovrapposizioni esplorate, quindi un limite inferiore del massimo vero e
mai inferiore al valore sulla sovrapposizione del TM-score.

**lDDT sui Cα.** Si considerano le distanze tra i Cα di residui diversi di A più corte del raggio di inclusione
R0 = 15 Å; una distanza è conservata alla soglia t se |d_B − d_A| < t, con t = 0,5, 1, 2, 4 Å, e il punteggio è la
media delle quattro frazioni. Non usa nessuna sovrapposizione. Una distanza che coinvolge un residuo di A senza
corrispondente in B conta come non conservata (come gli atomi mancanti nel modello del lDDT originale): il lDDT
globale penalizza anche la copertura. Per residuo: le sole distanze che coinvolgono quel residuo. Globale: tutte le
distanze insieme (media pesata sulle distanze, non media dei residui). Il controllo stereochimico e la simmetria
delle catene laterali del lDDT a tutti gli atomi non si applicano ai soli Cα.
"""

from __future__ import annotations

from typing import Iterable, Optional, Sequence

import numpy as np

from . import geometry

GDT_CUTOFFS = (0.5, 1.0, 2.0, 4.0, 8.0)
GDT_TS_CUTOFFS = (1.0, 2.0, 4.0, 8.0)
GDT_HA_CUTOFFS = (0.5, 1.0, 2.0, 4.0)
LDDT_RADIUS = 15.0
LDDT_THRESHOLDS = (0.5, 1.0, 2.0, 4.0)
# Coppie "vicine": la soglia con cui TM-align e US-align segnano ":" le coppie e contano l'RMSD dei residui vicini.
CLOSE_CUTOFF = 5.0
MAX_ITERATIONS = 20
MIN_SEED = 4
MAX_SEEDS_PER_LENGTH = 48
# Righe della matrice delle distanze di A calcolate insieme per il lDDT (5000 residui: 128 × 5000 × 3 numeri).
_LDDT_BLOCK = 128

Transform = tuple[np.ndarray, np.ndarray]


def _seeds(n: int) -> Iterable[np.ndarray]:
    """Frammenti contigui di lunghezza n, n/2, n/4… (almeno MIN_SEED) con passo di mezzo frammento.

    Al massimo MAX_SEEDS_PER_LENGTH frammenti per lunghezza, distribuiti uniformemente: il limite conta solo oltre
    i ~100 residui (per 1UBQ i semi sono tutti) e tiene la ricerca lineare nel numero dei residui invece che
    quadratica (5000 coppie: pochi secondi invece di minuti).
    """
    flen = n
    while flen >= MIN_SEED:
        starts = np.arange(0, n - flen + 1, max(1, flen // 2))
        if len(starts) > MAX_SEEDS_PER_LENGTH:
            starts = np.unique(np.linspace(0, n - flen, MAX_SEEDS_PER_LENGTH).round().astype(int))
        for start in starts:
            yield np.arange(start, start + flen)
        flen //= 2


def gdt_counts(mobile: np.ndarray, target: np.ndarray, transforms: Sequence[Transform] = (),
               cutoffs: Sequence[float] = GDT_CUTOFFS) -> dict[float, int]:
    """Per ogni soglia, numero massimo di coppie entro la soglia (≤) sulle sovrapposizioni esplorate.

    `mobile` e `target`: Cα di B e di A delle coppie allineate, nello stesso ordine. `transforms`: sovrapposizioni
    (rotazione, traslazione) da provare oltre ai semi, per esempio quella del TM-score.
    """
    mobile = np.asarray(mobile, dtype=float)
    target = np.asarray(target, dtype=float)
    counts = {float(c): 0 for c in cutoffs}
    if not len(mobile):
        return counts

    def evaluate(rot: np.ndarray, trans: np.ndarray) -> np.ndarray:
        dist = np.linalg.norm(geometry.apply(mobile, rot, trans) - target, axis=1)
        for c in counts:
            k = int((dist <= c).sum())
            if k > counts[c]:
                counts[c] = k
        return dist

    for rot, trans in transforms:
        evaluate(np.asarray(rot, dtype=float), np.asarray(trans, dtype=float))
    if len(mobile) < 3:
        return counts
    # insiemi già sovrapposti per ogni soglia: da lì la traiettoria è già stata esplorata (semi diversi convergono
    # spesso allo stesso insieme)
    seen: dict[float, set[int]] = {c: set() for c in counts}
    for seed in _seeds(len(mobile)):
        first = evaluate(*geometry.kabsch(mobile[seed], target[seed]))
        for c in counts:
            dist, current = first, seed
            for _ in range(MAX_ITERATIONS):
                selected = np.flatnonzero(dist <= c)
                if len(selected) < 3 or np.array_equal(selected, current):
                    break
                key = hash(selected.tobytes())
                if key in seen[c]:
                    break
                seen[c].add(key)
                current = selected
                dist = evaluate(*geometry.kabsch(mobile[selected], target[selected]))
    return counts


def gdt_scores(counts: dict[float, int], reference_length: int) -> dict:
    """Pd in percentuale dei residui del riferimento, GDT-TS e GDT-HA."""
    length = max(1, int(reference_length))
    percent = {c: 100.0 * k / length for c, k in counts.items()}
    return {
        "gdt": [{"cutoff": c, "count": counts[c], "percent": round(percent[c], 2)} for c in sorted(counts)],
        "gdt_ts": round(sum(percent[c] for c in GDT_TS_CUTOFFS) / len(GDT_TS_CUTOFFS), 2),
        "gdt_ha": round(sum(percent[c] for c in GDT_HA_CUTOFFS) / len(GDT_HA_CUTOFFS), 2),
    }


def lddt_ca(coords_a: np.ndarray, coords_b: np.ndarray, mapping: Sequence[int],
            radius: float = LDDT_RADIUS, thresholds: Sequence[float] = LDDT_THRESHOLDS) -> dict:
    """lDDT sui Cα con A come riferimento.

    `mapping[i]`: indice in B del residuo corrispondente all'i-esimo residuo di A, oppure −1. Restituisce il valore
    globale (None se A non ha distanze entro il raggio), quello per residuo di A (None per un residuo senza vicini)
    e il numero di distanze considerate (ogni coppia di residui una volta).
    """
    a = np.asarray(coords_a, dtype=float)
    b = np.asarray(coords_b, dtype=float)
    idx = np.asarray(mapping, dtype=int)
    n = len(a)
    if n == 0:
        return {"global": None, "per_residue": [], "distances": 0}
    # coordinate di B nell'ordine di A; NaN per i residui senza corrispondente (ogni confronto con NaN è falso)
    mapped = np.full((n, 3), np.nan)
    has = idx >= 0
    mapped[has] = b[idx[has]]
    conserved = np.zeros(n)
    neighbours = np.zeros(n)
    for start in range(0, n, _LDDT_BLOCK):
        rows = slice(start, min(n, start + _LDDT_BLOCK))
        d_a = np.linalg.norm(a[rows, None, :] - a[None, :, :], axis=2)
        with np.errstate(invalid="ignore"):
            d_b = np.linalg.norm(mapped[rows, None, :] - mapped[None, :, :], axis=2)
            diff = np.abs(d_b - d_a)
        inside = d_a < radius
        own = np.arange(rows.start, rows.stop)
        inside[own - rows.start, own] = False  # un residuo con sé stesso
        neighbours[rows] = inside.sum(axis=1)
        with np.errstate(invalid="ignore"):
            conserved[rows] = sum(((diff < t) & inside).sum(axis=1) for t in thresholds)
    k = len(thresholds)
    per = [round(float(conserved[i] / (k * neighbours[i])), 4) if neighbours[i] else None for i in range(n)]
    total = neighbours.sum()
    return {"global": round(float(conserved.sum() / (k * total)), 4) if total else None, "per_residue": per,
            # la matrice è simmetrica: ogni distanza compare due volte
            "distances": int(total // 2)}


def compare(ca_a: Sequence[dict], ca_b: Sequence[dict], pairs: Sequence[tuple[int, int]], rotation: np.ndarray,
            translation: np.ndarray, transforms: Sequence[Transform] = ()) -> dict:
    """Tabella per coppia allineata e metriche del confronto.

    `ca_a`, `ca_b`: Cα delle due catene come da `structure.ca_trace` (chain, resnum, aa, coord). `pairs`: coppie
    (indice in A, indice in B) dell'allineamento. (`rotation`, `translation`) è la sovrapposizione di B su A del
    TM-score, con cui si misurano le distanze; entra anche tra le sovrapposizioni del GDT insieme a `transforms`.
    """
    rot = np.asarray(rotation, dtype=float)
    trans = np.asarray(translation, dtype=float)
    coords_a = np.array([r["coord"] for r in ca_a], dtype=float).reshape(-1, 3)
    coords_b = np.array([r["coord"] for r in ca_b], dtype=float).reshape(-1, 3)
    ia = np.array([i for i, _ in pairs], dtype=int)
    ib = np.array([j for _, j in pairs], dtype=int)
    target = coords_a[ia] if len(ia) else np.zeros((0, 3))
    mobile = coords_b[ib] if len(ib) else np.zeros((0, 3))
    dist = np.linalg.norm(geometry.apply(mobile, rot, trans) - target, axis=1) if len(ia) else np.zeros(0)
    mapping = np.full(len(ca_a), -1, dtype=int)
    mapping[ia] = ib
    lddt = lddt_ca(coords_a, coords_b, mapping)
    close = dist < CLOSE_CUTOFF
    counts = gdt_counts(mobile, target, [(rot, trans), *transforms])
    table = []
    for k, (i, j) in enumerate(pairs):
        a, b = ca_a[i], ca_b[j]
        table.append({"chain_a": a["chain"], "resnum_a": a["resnum"], "aa_a": a["aa"],
                      "chain_b": b["chain"], "resnum_b": b["resnum"], "aa_b": b["aa"],
                      "distance": round(float(dist[k]), 3), "lddt": lddt["per_residue"][i]})
    return {
        "pairs": table,
        **gdt_scores(counts, len(ca_a)),
        "lddt": lddt["global"],
        "lddt_distances": lddt["distances"],
        "rmsd_close": round(float(np.sqrt((dist[close] ** 2).mean())), 3) if close.any() else None,
        "close_pairs": int(close.sum()),
        "transform": {"rotation": [[round(float(x), 8) for x in row] for row in rot],
                      "translation": [round(float(x), 6) for x in trans]},
    }


def kabsch_rmsd(mobile: np.ndarray, target: np.ndarray) -> Optional[float]:
    """RMSD dopo la sovrapposizione di Kabsch di tutte le coppie (come l'RMSD riportato da TM-align/US-align)."""
    mobile = np.asarray(mobile, dtype=float)
    target = np.asarray(target, dtype=float)
    if len(mobile) < 3:
        return None
    rot, trans = geometry.kabsch(mobile, target)
    return round(geometry.rmsd(geometry.apply(mobile, rot, trans), target), 3)
