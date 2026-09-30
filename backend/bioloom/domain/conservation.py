"""Conservazione per colonna di un allineamento multiplo, riportata sui residui di una struttura (F7).

Logica pura (numpy; `PairwiseAligner` di Biopython solo per la mappatura sulla struttura).

**Punteggio per colonna** (entropia di Shannon con la penalità per i gap di Capra & Singh 2007):

- pesi delle sequenze di Henikoff & Henikoff 1994 (per colonna 1 / (tipi diversi × sequenze con quel tipo), il gap
  conta come ventunesimo tipo come in Capra & Singh; somma dei pesi = 1), così un gruppo di sequenze quasi uguali
  non conta come tante sequenze indipendenti;
- frequenze p_a pesate dei 20 amminoacidi fra le sequenze con un residuo nella colonna (le lettere non standard, X,
  B, Z, J, U, O, contano come gap);
- entropia H = −Σ p_a ln p_a, normalizzata con ln(min(20, N)), N = sequenze dell'allineamento (il massimo raggiungibile
  con N sequenze, come in Capra & Singh);
- conservazione = (1 − H / H_max) × (1 − g), con g = frazione pesata di gap nella colonna: 1 per una colonna invariante
  senza gap, 0 per una colonna di massima variabilità;
- colonne con più del 30% di gap (frazione semplice delle sequenze): nessun punteggio (`None`), come il `gap_cutoff`
  di Capra & Singh; `gap_fraction` nel risultato è questa frazione.

**Mappatura sulla struttura:** per ogni catena la sequenza dei residui della struttura (X per quelli non standard) si
allinea (Needleman-Wunsch, BLOSUM62, gap terminali gratuiti) alla riga dell'allineamento senza gap: residuo della
struttura → residuo della riga → colonna. Così residui mancanti nella struttura, code in più o piccole differenze non
spostano la numerazione. La riga è quella indicata oppure, per ogni catena, quella con identità più alta (candidate
scelte per 3-mer in comune, poi allineate). Scelta automatica: serve identità ≥ 90% e copertura ≥ 50% della catena
(l'allineamento deve contenere la sequenza della catena); una riga indicata vale anche sotto, con un avviso. Con meno
di 10 sequenze il risultato ha un avviso (scelta BioLoom: le frequenze di una colonna sono poco stimabili).
"""

from __future__ import annotations

import math
from typing import Callable, Optional

import numpy as np

from ..core.errors import AppError
from .sequences import GAPS
from .structure import _resid, is_amino_residue, three_to_one

VERSION = 1
AMINO = "ACDEFGHIKLMNPQRSTVWY"
GAP = len(AMINO)  # indice del gap (e delle lettere non standard)
GAP_CUTOFF = 0.3
MIN_IDENTITY = 90.0
MIN_COVERAGE = 50.0
FEW_SEQUENCES = 10
MAX_SEQUENCES = 5000
MAX_COLUMNS = 20000
CANDIDATES = 5  # righe allineate per catena nella scelta automatica, dopo il filtro dei 3-mer

_INDEX = np.full(256, GAP, dtype=np.int8)
for _k, _aa in enumerate(AMINO):
    _INDEX[ord(_aa)] = _k


def validate(ids: list[str], aligned: list[str]) -> list[str]:
    """Righe maiuscole di un allineamento valido; errore chiaro altrimenti."""
    if len(aligned) < 2:
        raise AppError("Per la conservazione servono almeno due sequenze allineate.", code="conservation_invalid")
    if len(aligned) > MAX_SEQUENCES:
        raise AppError(f"Allineamento troppo grande: al massimo {MAX_SEQUENCES} sequenze.", code="conservation_invalid")
    if len(ids) != len(aligned):
        raise AppError("Nomi e righe dell'allineamento non corrispondono.", code="conservation_invalid")
    rows = [str(s).upper() for s in aligned]
    lengths = {len(s) for s in rows}
    if len(lengths) != 1:
        raise AppError("Le righe dell'allineamento hanno lunghezze diverse: serve un allineamento multiplo.",
                       code="conservation_invalid")
    if not rows[0] or len(rows[0]) > MAX_COLUMNS:
        raise AppError(f"L'allineamento deve avere da 1 a {MAX_COLUMNS} colonne.", code="conservation_invalid")
    bad = sorted({c for s in rows for c in s if not ((c.isascii() and c.isalpha()) or c in GAPS)})
    if bad:
        raise AppError(f"Caratteri non validi nell'allineamento: {''.join(bad)}", code="conservation_invalid")
    return rows


def _encode(rows: list[str]) -> np.ndarray:
    raw = np.frombuffer("".join(rows).encode("ascii", errors="replace"), dtype=np.uint8)
    return _INDEX[raw].reshape(len(rows), len(rows[0]))


def henikoff_weights(codes: np.ndarray) -> np.ndarray:
    """Pesi di Henikoff & Henikoff 1994 (gap come 21° tipo), normalizzati a somma 1."""
    n, length = codes.shape
    weights = np.zeros(n)
    for col in range(length):
        column = codes[:, col]
        counts = np.bincount(column, minlength=GAP + 1)
        types = int((counts > 0).sum())
        weights += 1.0 / (types * counts[column])
    total = weights.sum()
    return weights / total if total > 0 else np.full(n, 1.0 / n)


def column_scores(aligned: list[str]) -> dict:
    """Conservazione, entropia e frazione di gap di ogni colonna (None oltre il 30% di gap)."""
    codes = _encode(aligned)
    n, length = codes.shape
    w = henikoff_weights(codes)
    h_max = math.log(min(len(AMINO), n))
    scores: list[Optional[float]] = []
    entropies: list[Optional[float]] = []
    gaps: list[float] = []
    for col in range(length):
        column = codes[:, col]
        fraction = float((column == GAP).sum()) / n  # soglia del 30%: frazione semplice, come Capra & Singh
        gaps.append(round(fraction, 3))
        freq = np.bincount(column, weights=w, minlength=GAP + 1)
        if fraction > GAP_CUTOFF or freq[:GAP].sum() <= 0:
            scores.append(None)
            entropies.append(None)
            continue
        weighted_gap = float(min(1.0, freq[GAP]))  # penalità: frazione pesata
        p = freq[:GAP] / freq[:GAP].sum()
        p = p[p > 0]
        h = max(0.0, float(-(p * np.log(p)).sum()))
        entropies.append(round(h, 4))
        score = (1.0 - h / h_max) * (1.0 - weighted_gap) if h_max > 0 else 1.0 - weighted_gap
        scores.append(round(min(1.0, max(0.0, score)), 4))
    return {"scores": scores, "entropy": entropies, "gap_fraction": gaps, "weights": w}


def _kmers(seq: str, k: int = 3) -> set[str]:
    return {seq[i:i + k] for i in range(len(seq) - k + 1)}


def _aligner():
    """Needleman-Wunsch con BLOSUM62, gap 10/0,5 e gap terminali gratuiti: gli stessi parametri predefiniti di
    `engines/align.pairwise` (qui senza passare dagli adattatori: logica pura)."""
    from Bio.Align import PairwiseAligner, substitution_matrices

    aligner = PairwiseAligner()
    aligner.mode = "global"
    aligner.substitution_matrix = substitution_matrices.load("BLOSUM62")
    aligner.open_gap_score = -10.0
    aligner.extend_gap_score = -0.5
    aligner.end_gap_score = 0.0
    return aligner


def _map(chain_seq: str, row_seq: str) -> tuple[list[Optional[int]], float, float]:
    """Per ogni residuo della catena la posizione nella riga (o None); identità e copertura in percentuale."""
    aln = _aligner().align(chain_seq, row_seq)[0]
    top, bottom = str(aln[0]), str(aln[1])
    mapping: list[Optional[int]] = []
    j = identical = pairs = 0
    for x, y in zip(top, bottom):
        if x != "-" and y != "-":
            mapping.append(j)
            pairs += 1
            identical += x == y
        elif x != "-":
            mapping.append(None)
        if y != "-":
            j += 1
    identity = 100.0 * identical / pairs if pairs else 0.0
    coverage = 100.0 * pairs / len(chain_seq) if chain_seq else 0.0
    return mapping, identity, coverage


def _chains(model) -> list[tuple[str, list]]:
    return [(chain.id, [r for r in chain if is_amino_residue(r)]) for chain in model
            if any(is_amino_residue(r) for r in chain)]


def compute(model, ids: list[str], aligned: list[str], *, chains: Optional[list[str]] = None,
            row: Optional[str] = None, source: str = "", check: Optional[Callable[[], None]] = None) -> dict:
    """Conservazione per residuo delle catene della struttura (tutte o `chains`) dall'allineamento `ids`/`aligned`.

    `row`: nome della riga da usare per tutte le catene (altrimenti la più simile a ciascuna catena)."""
    rows = validate(ids, aligned)
    columns = column_scores(rows)
    ungapped = ["".join(c for c in s if c not in GAPS) for s in rows]
    # colonna di ogni residuo di ciascuna riga
    positions = [[k for k, c in enumerate(s) if c not in GAPS] for s in rows]
    warnings: list[str] = []
    if len(rows) < FEW_SEQUENCES:
        warnings.append(f"L'allineamento ha solo {len(rows)} sequenze: con meno di {FEW_SEQUENCES} la conservazione "
                        "per colonna è poco affidabile.")
    fixed = None
    if row:
        if row not in ids:
            raise AppError(f"La riga {row} non è nell'allineamento.", code="conservation_invalid")
        fixed = ids.index(row)
    selected = _chains(model)
    if chains:
        wanted = set(chains)
        missing = wanted - {c for c, _r in selected}
        if missing:
            raise AppError(f"Catene non presenti nella struttura: {', '.join(sorted(missing))}", code="invalid_chain")
        selected = [(c, r) for c, r in selected if c in wanted]
    residues_out: list[dict] = []
    chains_out: list[dict] = []
    unmatched: list[dict] = []
    cache: dict[tuple[str, int], tuple[list[Optional[int]], float, float]] = {}
    kmers = [_kmers(s) for s in ungapped] if fixed is None else []
    for chain_id, residues in selected:
        if check is not None:
            check()
        seq = "".join(three_to_one(r.get_resname()) for r in residues)
        if fixed is not None:
            candidates = [fixed]
        else:
            own = _kmers(seq)
            ranked = sorted(range(len(rows)), key=lambda k: (-len(own & kmers[k]), k))
            candidates = ranked[:CANDIDATES]
        best = None
        for k in candidates:
            if (seq, k) not in cache:
                cache[(seq, k)] = _map(seq, ungapped[k])
            mapping, identity, coverage = cache[(seq, k)]
            if best is None or (identity * coverage, identity) > (best[2] * best[3], best[2]):
                best = (k, mapping, identity, coverage)
        k, mapping, identity, coverage = best
        if fixed is None and (identity < MIN_IDENTITY or coverage < MIN_COVERAGE):
            unmatched.append({"chain": chain_id, "best_row": ids[k], "identity": round(identity, 1),
                              "coverage": round(coverage, 1)})
            continue
        if identity < MIN_IDENTITY or coverage < MIN_COVERAGE:
            warnings.append(f"Catena {chain_id}: la riga {ids[k]} ha identità {identity:.1f}% e copertura "
                            f"{coverage:.1f}%: la conservazione è quella di un omologo, non della sequenza della catena.")
        scored = mapped = 0
        values = []
        for res, pos in zip(residues, mapping):
            column = positions[k][pos] if pos is not None else None
            score = columns["scores"][column] if column is not None else None
            mapped += column is not None
            scored += score is not None
            values.append(score)
            residues_out.append({
                "chain": chain_id, "resnum": _resid(res), "aa": three_to_one(res.get_resname()),
                "column": column + 1 if column is not None else None,
                "row_aa": rows[k][column] if column is not None else None, "score": score,
                "entropy": columns["entropy"][column] if column is not None else None,
                "gap_fraction": columns["gap_fraction"][column] if column is not None else None,
            })
        valid = [v for v in values if v is not None]
        chains_out.append({"chain": chain_id, "row": ids[k], "identity": round(identity, 1),
                           "coverage": round(coverage, 1), "residues": len(residues), "mapped": mapped,
                           "scored": scored, "mean": round(sum(valid) / len(valid), 3) if valid else None})
    if not chains_out:
        detail = "; ".join(f"catena {u['chain']}: al massimo {u['identity']}% di identità ({u['best_row']})"
                           for u in unmatched)
        raise AppError("Nessuna catena della struttura corrisponde a una riga dell'allineamento (servono identità "
                       f"≥ {MIN_IDENTITY:.0f}% e copertura ≥ {MIN_COVERAGE:.0f}%): {detail}. Indica la riga da usare "
                       "oppure un allineamento che contenga la sequenza della struttura.", code="conservation_no_match")
    for u in unmatched:
        warnings.append(f"Catena {u['chain']}: nessuna riga dell'allineamento corrisponde (al massimo {u['identity']}% "
                        f"di identità con {u['best_row']}): conservazione non riportata.")
    return {"version": VERSION, "source": source, "sequences": len(rows), "columns": len(rows[0]),
            "method": "entropia di Shannon con pesi di Henikoff e penalità dei gap (Capra & Singh 2007)",
            "gap_cutoff": GAP_CUTOFF, "chains": chains_out, "unmatched": unmatched, "residues": residues_out,
            "warnings": warnings}
