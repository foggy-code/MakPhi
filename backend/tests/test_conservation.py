"""Conservazione per colonna (entropia di Shannon, pesi di Henikoff, penalità dei gap di Capra & Singh 2007) e
mappatura sui residui di 1UBQ (domain/conservation.py, F7)."""

from __future__ import annotations

import math

import numpy as np
import pytest

from bioloom.core.errors import AppError
from bioloom.domain import conservation, structure

from .conftest import DATA, HUMAN_UB, YEAST_UB


@pytest.fixture(scope="module")
def ubq():
    return structure.first_model(structure.load_structure(DATA / "1UBQ.pdb", "1UBQ"))


def _variants() -> list[str]:
    """12 righe: 1UBQ, lievito e 10 varianti con la colonna 20 variabile (10 amminoacidi diversi) e, in 3 righe,
    gap nelle colonne 75–76."""
    rows = [HUMAN_UB, YEAST_UB]
    for k in range(10):
        s = list(HUMAN_UB)
        s[19] = "ACDEFGHIKL"[k]
        if k < 3:
            s[74:76] = "--"
        rows.append("".join(s))
    return rows


def test_henikoff_weights_by_hand():
    """Pesi di Henikoff & Henikoff 1994 calcolati a mano (gap come 21° tipo): 1/4 per la colonna invariante, 1/6 e
    1/2 per A,A,A,- … somme 5/6, 5/6, 1, 4/3 → normalizzate."""
    codes = conservation._encode(["AAAA", "AAAC", "AAGD", "A-TE"])
    w = conservation.henikoff_weights(codes)
    raw = np.array([5 / 6, 5 / 6, 1.0, 4 / 3])
    assert w == pytest.approx(raw / raw.sum())
    assert w.sum() == pytest.approx(1.0)
    # sequenze identiche si dividono il peso: due copie contano come una
    dup = conservation.henikoff_weights(conservation._encode(["AC", "AC", "GT"]))
    assert dup[0] == pytest.approx(dup[1]) and dup[0] + dup[1] == pytest.approx(dup[2])


def test_column_scores_reference():
    """Colonna invariante → 1; colonna con un gap su quattro (25%): punteggio con penalità (1 − g); oltre il 30% di
    gap nessun punteggio; colonna variabile calcolata con la formula."""
    res = conservation.column_scores(["AAAA", "AAAC", "AAGD", "A-TE"])
    assert res["scores"][0] == 1.0 and res["entropy"][0] == 0.0 and res["gap_fraction"][0] == 0.0
    w = np.array([5 / 6, 5 / 6, 1.0, 4 / 3]) / 4.0
    # colonna 3 = A, A, G, T
    p = np.array([w[0] + w[1], w[2], w[3]])
    h = -(p * np.log(p)).sum()
    assert res["entropy"][2] == pytest.approx(h, abs=1e-4)
    assert res["scores"][2] == pytest.approx(1 - h / math.log(4), abs=1e-4)
    # colonna 4 = A, C, D, E: quasi massima variabilità (pesi diversi, quindi non esattamente 0)
    assert res["scores"][3] < 0.05
    # colonna 2 = A, A, A, - : 25% di gap, sotto la soglia del 30%
    two = conservation.column_scores(["AA", "AA", "AA", "A-"])
    assert two["gap_fraction"][1] == 0.25
    assert two["scores"][1] == pytest.approx(1 - conservation.henikoff_weights(
        conservation._encode(["AA", "AA", "AA", "A-"]))[3], abs=1e-4)
    three = conservation.column_scores(["AA", "AA", "A-", "A-"])  # 50% di gap
    assert three["scores"][1] is None and three["gap_fraction"][1] == 0.5


def test_nonstandard_letters_count_as_gaps():
    res = conservation.column_scores(["AX", "AB", "AA", "AA"])
    assert res["gap_fraction"][1] == 0.5 and res["scores"][1] is None


def test_mapping_on_1ubq(ubq):
    """Allineamento sintetico su 1UBQ: colonne invarianti → 1, la colonna 20 variabile → bassa, P19 (S nel lievito)
    in mezzo, colonne 75–76 con 3 gap su 12 penalizzate."""
    rows = _variants()
    res = conservation.compute(ubq, [f"s{k}" for k in range(len(rows))], rows, source="test")
    assert res["sequences"] == 12 and res["columns"] == 76 and res["warnings"] == []
    assert res["chains"] == [{"chain": "A", "row": "s0", "identity": 100.0, "coverage": 100.0, "residues": 76,
                              "mapped": 76, "scored": 76, "mean": res["chains"][0]["mean"]}]
    by = {r["resnum"]: r for r in res["residues"]}
    assert by["1"]["score"] == 1.0 and by["1"]["column"] == 1 and by["1"]["row_aa"] == "M"
    assert by["20"]["score"] < 0.1 < by["19"]["score"] < 1.0
    assert by["75"]["gap_fraction"] == 0.25 and 0.6 < by["75"]["score"] < 0.8
    assert all(by[str(n)]["score"] == 1.0 for n in (2, 21, 48, 63))


def test_mapping_follows_the_row_not_the_numbering(ubq):
    """Riga con 5 residui in più all'N-terminale e un'inserzione: la mappatura segue l'allineamento con la catena,
    non la numerazione (residuo 1 → colonna 6)."""
    row = "GSHMA" + HUMAN_UB[:30] + "W" + HUMAN_UB[30:]
    other = "GSHMA" + YEAST_UB[:30] + "-" + YEAST_UB[30:]
    res = conservation.compute(ubq, ["long", "yeast"], [row, other])
    by = {r["resnum"]: r for r in res["residues"]}
    assert by["1"]["column"] == 6 and by["30"]["column"] == 35 and by["31"]["column"] == 37
    assert by["76"]["column"] == len(row)
    assert all(r["row_aa"] == r["aa"] for r in res["residues"])
    assert res["chains"][0]["row"] == "long"
    assert any("solo 2 sequenze" in w for w in res["warnings"])


def test_automatic_row_choice_and_no_match(ubq):
    unrelated = "ACDEFGHIKLMNPQRSTVWY" * 3 + "ACDEFGHIKLMNPQRS"
    rows = [unrelated, YEAST_UB, HUMAN_UB]
    res = conservation.compute(ubq, ["x", "yeast", "human"], rows)
    assert res["chains"][0]["row"] == "human" and res["chains"][0]["identity"] == 100.0
    with pytest.raises(AppError) as err:
        conservation.compute(ubq, ["x", "y"], [unrelated, unrelated[::-1]])
    assert err.value.code == "conservation_no_match"
    # riga indicata con identità bassa: usata, con un avviso
    forced = conservation.compute(ubq, ["x", "y"], [unrelated, unrelated[::-1]], row="x")
    assert forced["chains"][0]["row"] == "x" and any("omologo" in w for w in forced["warnings"])


def test_multichain_mapping_and_selection():
    model = structure.first_model(structure.load_structure(DATA / "ubq_dimer.pdb", "dimer"))
    rows = _variants()
    ids = [f"s{k}" for k in range(len(rows))]
    both = conservation.compute(model, ids, rows)
    assert [c["chain"] for c in both["chains"]] == ["A", "B"] and len(both["residues"]) == 152
    a = [r["score"] for r in both["residues"] if r["chain"] == "A"]
    b = [r["score"] for r in both["residues"] if r["chain"] == "B"]
    assert a == b
    only_b = conservation.compute(model, ids, rows, chains=["B"])
    assert [c["chain"] for c in only_b["chains"]] == ["B"]
    with pytest.raises(AppError):
        conservation.compute(model, ids, rows, chains=["Z"])


@pytest.mark.parametrize("ids, rows", [
    (["a"], ["AAA"]),
    (["a", "b"], ["AAA", "AA"]),
    (["a", "b"], ["AA1", "AAA"]),
    (["a", "b"], ["AAÀ", "AAA"]),
    (["a"], ["AAA", "AAA"]),
])
def test_invalid_alignments(ids, rows):
    with pytest.raises(AppError) as err:
        conservation.validate(ids, rows)
    assert err.value.code == "conservation_invalid"
