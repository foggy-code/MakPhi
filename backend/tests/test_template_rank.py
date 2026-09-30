"""Scelta ragionata dei template (F5.2): punteggio composito, template complementari, metadati RCSB e cache,
modalità multi della pipeline.

Nessun accesso alla rete: RCSB è simulato con monkeypatch o con httpx.MockTransport sul client condiviso di
engines/remote.py. Gli hit sintetici allineano l'ubiquitina di lievito (bersaglio) su quella umana (1UBQ): 73
residui identici su 76, 96,05% di identità.
"""

from __future__ import annotations

import json
import math
import threading
from pathlib import Path

import httpx
import numpy as np
import pytest

from bioloom.core import cache
from bioloom.core.errors import RemoteServiceError
from bioloom.domain import geometry, multitemplate, sequences, template_rank
from bioloom.engines import modeller, remote, search

from .conftest import DATA, HUMAN_UB, YEAST_UB, requires_modeller, wait_job


def _mutate(seq: str) -> str:
    """Ogni residuo sostituito con un altro (identità 0 con l'originale)."""
    return "".join("A" if c != "A" else "G" for c in seq)


def hit(tid: str = "1UBQ_A", start: int = 1, end: int = 76, *, subject: str | None = None, **over) -> dict:
    """Hit del bersaglio YEAST_UB[start-1:end] allineato senza lacune su `subject` (predefinito: lo stesso tratto
    dell'ubiquitina umana). Metadati di una struttura a raggi X a 1,8 Å del 1987, come 1UBQ."""
    query = YEAST_UB[start - 1:end]
    subject = HUMAN_UB[start - 1:end] if subject is None else subject
    same = sum(a == b for a, b in zip(query, subject))
    pid, _, chain = tid.partition("_")
    h = {"pdb_id": pid, "chain": chain or "A", "template_id": tid, "title": "UBIQUITIN", "description": "",
         "organism": "Homo sapiens", "method": "X-RAY DIFFRACTION", "resolution": 1.8,
         "identity": round(100.0 * same / len(query), 1), "positives": None,
         "coverage": round(100.0 * (end - start + 1) / len(YEAST_UB), 1), "evalue": 1e-30, "bitscore": 150.0,
         "align_length": len(query), "query_start": start, "query_end": end, "subject_start": start,
         "subject_end": end, "query_aligned": query, "subject_aligned": subject, "subject_length": 76,
         "source": "rcsb", "rfree": None, "released": "1987-04-16", "ligands": [], "oligomeric_state": "Monomer",
         "oligomeric_count": 1}
    h.update(over)
    return h


def reason(info: dict, key: str) -> dict:
    return next(r for r in info["reasons"] if r["key"] == key)


# ------------------------------------------------------------------------------------------ curva di Rost
def test_rost_threshold_reference_values():
    # Rost 1999, eq. 2 (n = 0): 100% fino a 11 residui, asintoto 19,5% oltre 450
    assert template_rank.rost_threshold(11) == 100.0
    assert template_rank.rost_threshold(76) == pytest.approx(33.23, abs=0.01)
    assert template_rank.rost_threshold(100) == pytest.approx(28.98, abs=0.01)
    assert template_rank.rost_threshold(250) == pytest.approx(20.72, abs=0.01)
    assert template_rank.rost_threshold(450) == pytest.approx(19.54, abs=0.01)
    assert template_rank.rost_threshold(451) == template_rank.rost_threshold(None) == 19.5
    # decrescente (allineamenti lunghi, soglia più bassa) fino a circa 420 residui; poi la formula risale di pochi
    # centesimi verso 19,5 (minimo 19,5 circa): nessun salto all'asintoto
    values = [template_rank.rost_threshold(n) for n in range(12, 401)]
    assert all(a > b for a, b in zip(values, values[1:]))
    assert all(19.45 < template_rank.rost_threshold(n) < 19.6 for n in range(401, 452))


# ------------------------------------------------------------------------------------------ punteggio
def test_score_of_1ubq_for_yeast_ubiquitin():
    info = template_rank.score_hit(hit())
    identity = reason(info, "identity")
    # 73/76 = 96,05%: 70 + 30 × (96,05 − 50) / 50 = 97,6; soglia di Rost per 76 residui 33,2%
    assert identity["score"] == 97.6 and identity["weight"] == 30.0
    assert "96,1% su 76 residui allineati" in identity["text"] and "curva di Rost (33,2%)" in identity["text"]
    assert "omologia sicura" in identity["text"]
    assert reason(info, "coverage")["score"] == 100.0
    assert reason(info, "evalue")["score"] == 100.0
    assert reason(info, "resolution")["score"] == 84.0  # 1,8 Å: 100 − 30 × 0,8 / 1,5
    assert reason(info, "method")["score"] == 100.0
    assert reason(info, "gaps")["score"] == 100.0 and "nessuna lacuna" in reason(info, "gaps")["text"]
    assert reason(info, "date")["score"] == 40.0 and "1987" in reason(info, "date")["text"]
    assert reason(info, "rfree")["score"] is None and "non depositato" in reason(info, "rfree")["text"]
    # (30 × 97,6 + 30 × 100 + 5 × 100 + 12 × 84 + 8 × 100 + 7 × 100 + 3 × 40) / 95 = 95,3
    assert info["score"] == 95.3 and info["band"] == "buona" and info["twilight"] is False
    assert sum(c.weight for c in template_rank.COMPONENTS) == 100.0
    assert all(isinstance(r["text"], str) and r["text"] for r in info["reasons"])


def test_high_identity_but_low_resolution():
    low = template_rank.score_hit(hit(resolution=3.8, rfree=0.33, released="2019-05-02"))
    res = reason(low, "resolution")
    assert res["score"] == 32.0 and "solo lo scheletro" in res["text"]  # 40 − 40 × 0,3 / 1,5
    rfree = reason(low, "rfree")
    assert rfree["score"] == 28.0 and "0,330" in rfree["text"]  # 40 − 40 × 0,03 / 0,10
    assert reason(low, "date")["score"] == 100.0
    # stessa identità, risoluzione migliore: vince la migliore
    good = template_rank.score_hit(hit(resolution=1.5, rfree=0.20, released="2019-05-02"))
    assert good["score"] > low["score"]
    # a 3,8 Å l'identità del 96% vale ancora più di una struttura a 1,5 Å al 35,5% (27 residui identici su 76)
    far = template_rank.score_hit(hit(subject=HUMAN_UB[:30] + _mutate(HUMAN_UB[30:]), resolution=1.5, rfree=0.20,
                                      released="2019-05-02"))
    assert reason(far, "identity")["score"] == 44.1 and "omologia probabile" in reason(far, "identity")["text"]
    assert far["score"] == 82.0 and low["score"] == 87.5


def test_nmr_structure_rules():
    info = template_rank.score_hit(hit(method="SOLUTION NMR", resolution=None))
    res = reason(info, "resolution")
    assert res["score"] == template_rank.NMR_RESOLUTION_SCORE == 60.0 and "NMR" in res["text"]
    assert reason(info, "method")["score"] == 80.0
    rfree = reason(info, "rfree")
    assert rfree["score"] is None and "diffrazione" in rfree["text"]
    xray = template_rank.score_hit(hit())
    assert info["score"] == 90.6 and info["score"] < xray["score"]
    # crio-EM: scala della risoluzione, con la nota sulla risoluzione locale; niente R-free
    em = template_rank.score_hit(hit(method="ELECTRON MICROSCOPY", resolution=3.2, rfree=0.3))
    assert reason(em, "resolution")["score"] == 49.0 and "crio-EM" in reason(em, "resolution")["text"]
    assert reason(em, "rfree")["score"] is None and reason(em, "method")["score"] == 90.0


def test_predicted_model_rules():
    af = hit("AF-P0CG48-F1", method="Modello predetto (AlphaFold DB) v6", resolution=None, predicted=True,
             plddt=88.6, released="", ligands=None, oligomeric_state="", oligomeric_count=None)
    info = template_rank.score_hit(af)
    res = reason(info, "resolution")
    assert res["label"] == "Confidenza (pLDDT medio)" and res["score"] == 67.9  # 40 + 30 × 18,6 / 20
    assert "confidente" in res["text"]
    method = reason(info, "method")
    assert method["score"] == 40.0 and "ipotesi" in method["text"]
    assert reason(info, "date")["score"] is None and reason(info, "rfree")["score"] is None
    assert info["score"] == 89.8  # (30 × 97,6 + 30 × 100 + 5 × 100 + 12 × 67,9 + 8 × 40 + 7 × 100) / 92
    # stesso allineamento: la struttura sperimentale vince; con la sola metà del bersaglio no
    exp = hit()
    assert template_rank.best([af, exp])["template_id"] == "1UBQ_A"
    assert template_rank.best([exp, af])["template_id"] == "1UBQ_A"
    half = hit(start=1, end=38)
    assert template_rank.best([half, af])["template_id"] == "AF-P0CG48-F1"


def test_equal_scores_prefer_experimental_structures():
    exp = {"template_id": "X_A", "identity": 90.0, "coverage": 100.0, "rank_score": 80.0}
    af = {"template_id": "AF-P0CG48-F1", "identity": 99.0, "coverage": 100.0, "rank_score": 80.0, "predicted": True}
    assert template_rank.best([af, exp])["template_id"] == "X_A"
    assert template_rank.best([exp, {**af, "rank_score": 80.1}])["template_id"] == "AF-P0CG48-F1"
    assert template_rank.best([]) is None


def test_partial_coverage():
    info = template_rank.score_hit(hit(start=1, end=38))
    cov = reason(info, "coverage")
    assert cov["score"] == 50.0 and "residui 1–38" in cov["text"] and "secondo template" in cov["text"]
    # identità 92,1% su 38 residui, sopra la curva (48,9% per 38 residui)
    assert "su 38 residui allineati" in reason(info, "identity")["text"]
    assert info["score"] < template_rank.score_hit(hit())["score"]


def test_twilight_zone_depends_on_the_aligned_length():
    # 25% su 100 residui: sotto la curva (29,0%) → zona crepuscolare
    q = (HUMAN_UB + HUMAN_UB)[:100]
    s = "".join(c if i % 4 == 0 else _mutate(c) for i, c in enumerate(q))
    short = {"identity": 25.0, "coverage": 100.0, "query_start": 1, "query_end": 100, "query_aligned": q,
             "subject_aligned": s, "evalue": 1e-4}
    info = template_rank.score_hit(short)
    assert info["twilight"] is True
    ident = reason(info, "identity")
    assert ident["score"] == pytest.approx(25 / 28.98 * 40, abs=0.1) and "zona crepuscolare" in ident["text"]
    # copertura completa, ma in zona crepuscolare conta al più quanto l'identità: mai in fascia buona
    cov = reason(info, "coverage")
    assert cov["score"] == ident["score"] and "conta al più quanto l'identità (100,0 → 34,5)" in cov["text"]
    assert info["band"] == "intermedia" and info["score"] == round((30 * 34.5 * 2 + 5 * 74.3 + 7 * 100) / 72, 1)
    # stessa identità su 450 residui: sopra la soglia asintotica (19,5%), non crepuscolare
    long_q, long_s = (q * 5)[:450], (s * 5)[:450]
    info = template_rank.score_hit({**short, "query_aligned": long_q, "subject_aligned": long_s, "query_end": 450})
    assert info["twilight"] is False and "sopra la curva di Rost (19,5%)" in reason(info, "identity")["text"]


def test_alignment_gaps():
    q, s = YEAST_UB[:30] + "---" + YEAST_UB[30:], HUMAN_UB[:30] + "AAA" + HUMAN_UB[30:]
    one = template_rank.score_hit(hit(query_aligned=q, subject_aligned=s))
    gaps = reason(one, "gaps")
    assert gaps["score"] == 92.5 and "1 lacuna, la più lunga di 3 residui" in gaps["text"]  # 100 − 10 × 3 / 4
    stats = template_rank.alignment_stats(hit(query_aligned=q, subject_aligned=s))
    assert stats["pairs"] == 76 and stats["openings"] == 1 and stats["target_insertions"] == 0
    # lacuna lunga (20 residui del bersaglio senza template) e 4 lacune: 40 − 40 × 5 / 15 − 2 × 2
    long_q = YEAST_UB
    long_s = HUMAN_UB[:10] + "-" + HUMAN_UB[11:20] + "-" * 20 + HUMAN_UB[40:50] + "-" + HUMAN_UB[51:60] + "-" \
        + HUMAN_UB[61:]
    info = template_rank.score_hit(hit(query_aligned=long_q, subject_aligned=long_s))
    assert reason(info, "gaps")["score"] == pytest.approx(40 - 40 * 5 / 15 - 4, abs=0.05)
    assert "4 lacune, la più lunga di 20 residui" in reason(info, "gaps")["text"]
    assert template_rank.alignment_stats(hit(query_aligned=long_q, subject_aligned=long_s))["target_insertions"] == 23
    # lacune alle estremità: non contano
    assert template_rank.alignment_stats({"query_aligned": "--MQIF", "subject_aligned": "AAMQIF"})["openings"] == 0


def test_evalue_scale():
    def score(value):
        return reason(template_rank.score_hit(hit(evalue=value)), "evalue")["score"]

    assert score(1e-3) == 70.0 and score(1e-2) == 40.0 and score(1.0) == 0.0 and score(0) == 100.0
    assert score(1e-10) == 100.0 and score(1e-5) == pytest.approx(70 + 30 * 2 / 7, abs=0.05)
    assert "non significativo" in reason(template_rank.score_hit(hit(evalue=0.5)), "evalue")["text"]


def test_missing_metadata_does_not_penalize():
    bare = {k: v for k, v in hit().items() if k not in ("method", "resolution", "released", "rfree", "ligands",
                                                         "oligomeric_state", "oligomeric_count")}
    info = template_rank.score_hit(bare)
    for key in ("resolution", "rfree", "method", "date", "ligands", "oligomer"):
        assert reason(info, key)["score"] is None
    # solo identità, copertura, E-value e lacune: (30 × 97,6 + 30 × 100 + 5 × 100 + 7 × 100) / 72
    assert info["score"] == round((30 * 97.6 + 3000 + 500 + 700) / 72, 1)


def test_ligands_and_oligomeric_state_are_informative():
    holo = hit(ligands=["HEM", "SO4", "GOL", "HOH", "ZN"], oligomeric_state="Homo 2-mer", oligomeric_count=2)
    info = template_rank.score_hit(holo)
    lig = reason(info, "ligands")
    assert lig["score"] is None and lig["weight"] == 0.0 and "HEM, ZN" in lig["text"] and "SO4" not in lig["text"]
    assert template_rank.relevant_ligands(holo) == ["HEM", "ZN"]
    assert "omo-oligomero di 2 catene" in reason(info, "oligomer")["text"]
    assert info["score"] == template_rank.score_hit(hit())["score"]  # non penalizza né premia
    assert "apo" in reason(template_rank.score_hit(hit(ligands=["GOL"])), "ligands")["text"]
    assert "non noti" in reason(template_rank.score_hit(hit(ligands=None)), "ligands")["text"]


def test_rank_hits_adds_the_fields_in_place():
    hits = [hit(), hit("2XYZ_B", start=1, end=38)]
    assert template_rank.rank_hits(hits) is hits
    assert [h["rank_score"] for h in hits] == [95.3, template_rank.score_hit(hits[1])["score"]]
    assert all(h["rank_band"] in ("buona", "intermedia", "scarsa") and isinstance(h["rank_reasons"], list)
               and h["rank_twilight"] is False for h in hits)
    json.dumps(hits)  # serializzabile nel risultato del job


# ------------------------------------------------------------------------------------------ complementari
def test_two_halves_are_both_chosen():
    # la seconda metà è identica al bersaglio ma a 3,0 Å: punteggio 76,6 contro 78,5 della prima
    first, second = hit("AAAA_A", start=1, end=38), hit("BBBB_B", start=39, end=76, resolution=3.0)
    assert template_rank.score_hit(first)["score"] == 78.5 and template_rank.score_hit(second)["score"] == 76.6
    sel = template_rank.select_complementary([second, first], 76)
    assert [t["template_id"] for t in sel["templates"]] == ["AAAA_A", "BBBB_B"]
    assert [t["role"] for t in sel["templates"]] == ["principale", "complementare"]
    comp = sel["templates"][1]
    assert comp["new_residues"] == 38 and comp["ranges"] == "39–76"
    assert "copre 38 residui del bersaglio non coperti (39–76)" in comp["reason"]
    assert "orientamento relativo" in comp["reason"]  # nessun residuo in comune
    assert sel["coverage"] == 100.0 and sel["covered_residues"] == 76 and not sel["excluded"]
    assert template_rank.modeling_templates(sel) == [{"id": "AAAA", "chains": ["A"]}, {"id": "BBBB", "chains": ["B"]}]


def test_nearly_identical_templates_give_one_only():
    a = hit("AAAA_A")
    b = hit("BBBB_A", resolution=2.0)  # stessa sequenza, stessa regione
    c = hit("CCCC_A", subject=HUMAN_UB[:40] + "R" + HUMAN_UB[41:], resolution=2.5)  # un residuo diverso
    sel = template_rank.select_complementary([c, b, a], 76)
    assert [t["template_id"] for t in sel["templates"]] == ["AAAA_A"]
    assert [e["template_id"] for e in sel["excluded"]] == ["BBBB_A", "CCCC_A"]
    assert all("ridondante" in e["reason"] and "quasi identico a AAAA_A" in e["reason"] for e in sel["excluded"])
    assert "identità reciproca 100,0% su 76 residui" in sel["excluded"][0]["reason"]
    assert sel["coverage"] == 100.0


def test_overlapping_fragments_and_longer_construct():
    n_term, c_term = hit("NNNN_A", start=1, end=50), hit("CCCC_A", start=30, end=76, resolution=2.4)
    sel = template_rank.select_complementary([n_term, c_term], 76)
    assert [t["template_id"] for t in sel["templates"]] == ["NNNN_A", "CCCC_A"]
    reason_text = sel["templates"][1]["reason"]
    # 26 residui nuovi; nella regione comune (30–50) è la stessa sequenza: lo si dice, ma entra lo stesso
    assert "copre 26 residui" in reason_text and "(51–76)" in reason_text and "quasi identico a NNNN_A" in reason_text
    assert "orientamento" not in reason_text
    # un costrutto più lungo della stessa proteina diventa il principale; quello contenuto è ridondante
    longer = hit("SSSS_A", start=1, end=55, resolution=2.0)
    sel = template_rank.select_complementary([n_term, longer], 76)
    assert [t["template_id"] for t in sel["templates"]] == ["SSSS_A"]
    assert "ridondante" in sel["excluded"][0]["reason"] and "0 residui nuovi" in sel["excluded"][0]["reason"]
    # meno di 10 residui nuovi (71–76) e troppo pochi in comune per giudicare la ridondanza: non aggiunge nulla
    main, tail = hit("MMMM_A", start=1, end=70), hit("TTTT_A", start=66, end=76)
    assert not template_rank.score_hit(tail)["twilight"] and template_rank.score_hit(tail)["score"] >= 40
    sel = template_rank.select_complementary([main, tail], 76)
    assert [t["template_id"] for t in sel["templates"]] == ["MMMM_A"]
    assert "non aggiunge copertura (6 residui nuovi, minimo 10)" in sel["excluded"][0]["reason"]


def test_local_identity_improvement():
    # A copre tutto ma ha 26–55 mutati (identità 60,5%); B copre solo 26–55 identico: nessun residuo nuovo, ma
    # l'identità locale sale di oltre 20 punti in più di 10 residui
    subject = HUMAN_UB[:25] + _mutate(HUMAN_UB[25:55]) + HUMAN_UB[55:]
    a = hit("AAAA_A", subject=subject, evalue=1e-20)
    b = hit("BBBB_A", start=26, end=55, evalue=1e-10)
    assert template_rank.score_hit(a)["score"] > template_rank.score_hit(b)["score"]
    sel = template_rank.select_complementary([b, a], 76)
    assert [t["template_id"] for t in sel["templates"]] == ["AAAA_A", "BBBB_A"]
    comp = sel["templates"][1]
    assert comp["reason"].startswith("migliora l'identità locale in") and comp["new_residues"] >= 10
    assert "finestra di 9 residui" in comp["reason"]
    # C copre 56–70, dove A è già identico al bersaglio: nessun residuo nuovo, nessun guadagno, ridondante con A
    c = hit("CCCC_A", start=56, end=70, evalue=1e-10)
    assert not template_rank.score_hit(c)["twilight"] and template_rank.score_hit(c)["score"] >= 40
    sel = template_rank.select_complementary([c, b, a], 76)
    assert [t["template_id"] for t in sel["templates"]] == ["AAAA_A", "BBBB_A"]
    assert [e["template_id"] for e in sel["excluded"]] == ["CCCC_A"]
    assert "ridondante: quasi identico a AAAA_A" in sel["excluded"][0]["reason"]


def test_twilight_poor_and_limit_exclusions():
    best = hit("AAAA_A", start=1, end=25)
    # 26–76 al 25% di identità su 51 residui: sotto la curva di Rost (41,4%)
    s = "".join(c if i % 4 == 0 else _mutate(c) for i, c in enumerate(HUMAN_UB[25:]))
    twilight = hit("TTTT_A", start=26, end=76, subject=s)
    assert template_rank.score_hit(twilight)["twilight"] is True
    sel = template_rank.select_complementary([best, twilight], 76)
    assert [t["template_id"] for t in sel["templates"]] == ["AAAA_A"]
    assert "zona crepuscolare" in sel["excluded"][0]["reason"]
    assert "zona crepuscolare" not in sel["templates"][0]["reason"]
    # un solo candidato in zona crepuscolare resta il principale, con l'avviso
    alone = template_rank.select_complementary([twilight], 76)["templates"][0]
    assert alone["template_id"] == "TTTT_A" and "è in zona crepuscolare" in alone["reason"].lower()
    # limite di template: gli altri non si esaminano (pezzi di 15 residui identici al bersaglio)
    pieces = [hit(f"P{i:03d}_A", start=1 + 15 * i, end=15 * (i + 1), subject=YEAST_UB[15 * i:15 * (i + 1)])
              for i in range(5)]
    sel = template_rank.select_complementary(pieces, 76, max_templates=2)
    assert len(sel["templates"]) == 2 and sel["not_examined"] == 3 and sel["max_templates"] == 2
    assert template_rank.select_complementary(pieces, 76, max_templates=99)["max_templates"] == 5
    assert template_rank.select_complementary([], 76)["templates"] == []


def test_selection_scales_to_long_targets_and_many_hits():
    """Bersaglio di 1000 residui, 400 candidati quasi identici fra loro (come i risultati di RCSB per una proteina
    molto studiata): la selezione resta rapida (identità locale con somme prefisse, calcolata una volta per scelto)."""
    import time

    target = (YEAST_UB * 14)[:1000]
    many = []
    for i in range(400):
        start = 1 + (i % 5) * 2  # al più 8 residui nuovi rispetto al primo: nessun complementare
        q = target[start - 1:start + 899]
        many.append({"template_id": f"{i:04d}_A", "pdb_id": f"{i:04d}", "chain": "A", "identity": 95.0,
                     "coverage": 90.0, "evalue": 1e-50, "query_start": start, "query_end": start + 899,
                     "query_aligned": q, "subject_aligned": q[:500] + _mutate(q[500:510]) + q[510:],
                     "rank_score": 90.0 - i * 0.01, "rank_twilight": False})
    began = time.perf_counter()
    sel = template_rank.select_complementary(many, 1000)
    assert time.perf_counter() - began < 10
    assert [t["template_id"] for t in sel["templates"]] == ["0000_A"]
    assert len(sel["excluded"]) == 399 and sel["coverage"] == 90.0


def test_complementary_without_alignment_strings():
    # motori senza le righe dell'allineamento: si usano gli intervalli del bersaglio
    a = {k: v for k, v in hit("AAAA_A", start=1, end=40).items() if k not in ("query_aligned", "subject_aligned")}
    b = {k: v for k, v in hit("BBBB_A", start=35, end=76, resolution=3.0).items()
         if k not in ("query_aligned", "subject_aligned")}
    assert template_rank.alignment_stats(a)["pairs"] is None
    assert reason(template_rank.score_hit(a), "gaps")["score"] is None
    sel = template_rank.select_complementary([a, b], 76)
    assert [t["template_id"] for t in sel["templates"]] == ["AAAA_A", "BBBB_A"]
    assert sel["templates"][1]["ranges"] == "41–76"


# ------------------------------------------------------------------------------------------ metadati RCSB
def _gql_entry(pid: str, rfree=0.206, state="Homo 2-mer", count=2) -> dict:
    """Voce GraphQL di RCSB nel formato reale (campi verificati su data.rcsb.org, settembre 2026)."""
    first = {"pdbx_struct_assembly": {"id": "2", "oligomeric_details": "monomeric", "oligomeric_count": 1},
             "rcsb_struct_symmetry": [{"kind": "Global Symmetry", "oligomeric_state": "Monomer"}]}
    second = {"pdbx_struct_assembly": {"id": "1", "oligomeric_details": "dimeric", "oligomeric_count": count},
              "rcsb_struct_symmetry": [{"kind": "Pseudo Symmetry", "oligomeric_state": "Homo 4-mer"},
                                       {"kind": "Global Symmetry", "oligomeric_state": state}]}
    return {
        "rcsb_id": pid, "struct": {"title": "UBIQUITIN"}, "exptl": [{"method": "X-RAY DIFFRACTION"}],
        "rcsb_entry_info": {"resolution_combined": [1.8]},
        "rcsb_accession_info": {"initial_release_date": "2016-06-01T00:00:00Z"},
        "refine": [{"ls_R_factor_R_free": rfree, "pdbx_refine_id": "X-RAY DIFFRACTION"}],
        "assemblies": [first, second],  # l'assemblaggio 1 non è sempre il primo dell'elenco (come in 1AKE)
        "nonpolymer_entities": [{"pdbx_entity_nonpoly": {"comp_id": "ZN"}},
                                {"pdbx_entity_nonpoly": {"comp_id": "GOL"}}],
        "polymer_entities": [{"entity_poly": {"pdbx_seq_one_letter_code_can": HUMAN_UB},
                              "rcsb_polymer_entity": {"pdbx_description": "Ubiquitin"},
                              "rcsb_polymer_entity_container_identifiers": {"auth_asym_ids": ["A", "B"]},
                              "rcsb_entity_source_organism": [{"scientific_name": "Homo sapiens"}]}],
    }


class FakeGraphQL:
    def __init__(self) -> None:
        self.requests: list[list[str]] = []
        self.status = 200
        self.payload = None

    def handler(self, request: httpx.Request) -> httpx.Response:
        ids = json.loads(request.content)["variables"]["ids"]
        self.requests.append(ids)
        if self.status != 200:
            return httpx.Response(self.status, text="errore")
        if self.payload is not None:
            return httpx.Response(200, json=self.payload)
        return httpx.Response(200, json={"data": {"entries": [_gql_entry(i) for i in ids]}})


@pytest.fixture
def gql(monkeypatch, tmp_path):
    fake = FakeGraphQL()
    monkeypatch.setattr(remote, "_client", httpx.Client(transport=httpx.MockTransport(lambda r: fake.handler(r))))
    monkeypatch.setattr(remote, "_cache_dir", tmp_path / "remote_cache")
    monkeypatch.setattr(remote, "_sleep_checked", lambda seconds, check, sleep: None)
    return fake


def test_rcsb_metadata_has_rfree_and_oligomeric_state(gql):
    meta = remote.rcsb_metadata(["1abc"])["1ABC"]
    assert meta["rfree"] == 0.206 and meta["version"] == remote.RCSB_META_VERSION
    # assemblaggio 1 (non il primo dell'elenco), simmetria globale
    assert meta["oligomeric_state"] == "Homo 2-mer" and meta["oligomeric_count"] == 2
    assert meta["released"] == "2016-06-01" and meta["ligands"] == ["GOL", "ZN"]
    fields = remote.hit_metadata(meta)
    assert fields == {"rfree": 0.206, "released": "2016-06-01", "ligands": ["GOL", "ZN"],
                      "oligomeric_state": "Homo 2-mer", "oligomeric_count": 2}
    assert remote.hit_metadata(None)["ligands"] is None
    # seconda lettura dalla cache
    assert remote.rcsb_metadata(["1ABC"])["1ABC"]["rfree"] == 0.206 and len(gql.requests) == 1


def test_rcsb_metadata_old_cache_entries_are_refreshed(gql, tmp_path):
    old = {"id": "1ABC", "title": "vecchio", "method": "X-RAY DIFFRACTION", "resolution": 2.0, "released": "2001-01-01",
           "chains": {"A": {"sequence": HUMAN_UB, "description": "", "organism": ""}}, "ligands": []}
    remote._cache_put("rcsb_meta", "1ABC", old)
    meta = remote.rcsb_metadata(["1ABC"])["1ABC"]
    assert gql.requests == [["1ABC"]] and meta["rfree"] == 0.206 and "stale" not in meta
    assert remote._cache_get("rcsb_meta", "1ABC", 86400)["version"] == remote.RCSB_META_VERSION
    # RCSB non raggiungibile: la voce vecchia si usa lo stesso, segnata
    remote._cache_put("rcsb_meta", "2DEF", {**old, "id": "2DEF"})
    gql.status = 503
    logs: list[str] = []
    meta = remote.rcsb_metadata(["2DEF"], log=logs.append)["2DEF"]
    assert meta["stale"] is True and meta["resolution"] == 2.0 and "rfree" not in meta
    assert any("Metadati RCSB non aggiornabili" in m for m in logs)
    # senza voce vecchia l'errore resta un errore
    with pytest.raises(RemoteServiceError):
        remote.rcsb_metadata(["3GHI"])


def test_rcsb_graphql_errors_are_not_an_empty_result(gql):
    gql.payload = {"errors": [{"message": "Cannot query field"}], "data": None}
    with pytest.raises(RemoteServiceError) as err:
        remote.rcsb_metadata(["1ABC"])
    assert "rifiutato" in err.value.message
    gql.payload = {"data": {"entries": []}}  # codici inesistenti: nessuna voce, nessun errore
    assert remote.rcsb_metadata(["9ZZZ"]) == {}


def test_rfree_prefers_the_xray_refinement():
    entry = {"refine": [{"ls_R_factor_R_free": 0.28, "pdbx_refine_id": "NEUTRON DIFFRACTION"},
                        {"ls_R_factor_R_free": 0.21, "pdbx_refine_id": "X-RAY DIFFRACTION"}]}
    assert remote._rfree(entry) == 0.21
    assert remote._rfree({"refine": [{"ls_R_factor_R_free": None}]}) is None and remote._rfree({}) is None
    assert remote._oligomer({"assemblies": []}) == ("", None)


# ------------------------------------------------------------------------------------------ ricerca e cache
def _search(client, sequence=YEAST_UB, **extra) -> dict:
    body = {"query": {"name": "q", "sequence": sequence}, "engine": "rcsb", "evalue": 0.001, "max_hits": 50, **extra}
    job = client.post("/api/search", json=body).json()
    return wait_job(client, job["id"])


@pytest.fixture
def halves(monkeypatch):
    calls = []

    def fake(sequence, evalue=10.0, max_hits=50, check=None, log=None):
        calls.append(sequence)
        return [hit("AAAA_A", start=1, end=38), hit("BBBB_B", start=39, end=76, resolution=3.0),
                hit("CCCC_A", start=1, end=38, resolution=2.9)]

    monkeypatch.setattr(remote, "rcsb_sequence_search", fake)
    return calls


def test_search_result_has_scores_and_suggestions(client, halves):
    done = _search(client)
    assert done["status"] == "completed", done
    res = done["result"]
    assert all(isinstance(h["rank_score"], float) and h["rank_reasons"] for h in res["hits"])
    assert res["best"] == "AAAA_A"
    sug = res["suggested"]
    assert [t["template_id"] for t in sug["templates"]] == ["AAAA_A", "BBBB_B"]
    assert sug["coverage"] == 100.0 and sug["excluded"][0]["template_id"] == "CCCC_A"
    log = client.get(f"/api/jobs/{done['id']}/log").json()["text"]
    assert "Template consigliati: AAAA_A (principale), BBBB_B (complementare)" in log
    # dalla cache: stessi punteggi, ricalcolati dopo la lettura
    again = _search(client)
    assert again["result"]["cached"] is True and len(halves) == 1
    assert again["result"]["hits"] == res["hits"] and again["result"]["suggested"] == sug


def test_old_search_cache_entries_are_not_read(client, halves):
    first = _search(client)
    [entry] = sorted((client.app.state.core.paths.compute_cache / "search").glob("*.json"))
    data = json.loads(entry.read_text(encoding="utf-8"))
    assert data["result"]["search_version"] == search.SEARCH_VERSION
    # voce della versione precedente: chiave senza "version" e risultato senza "search_version"
    descriptor = {"engine": "rcsb", "sequence": YEAST_UB, "evalue": 0.001, "max_hits": 50, "taxid": "",
                  "database": None, "indexed": False, "tool": None}
    old_key = cache.make_key("search", descriptor)
    assert old_key != entry.stem
    old_result = {k: v for k, v in data["result"].items() if k != "search_version"}
    old_result["hits"] = [{"template_id": "OLD1_A", "pdb_id": "OLD1", "chain": "A", "identity": 99.0,
                           "coverage": 100.0}]
    cache.store("search", old_key, old_result)
    entry.unlink()
    again = _search(client)
    assert not again["result"].get("cached") and len(halves) == 2
    assert [h["template_id"] for h in again["result"]["hits"]] == [h["template_id"] for h in first["result"]["hits"]]
    # voce con la chiave attuale ma senza la versione nel risultato (scritta a mano o danneggiata): scartata
    [entry] = [p for p in (client.app.state.core.paths.compute_cache / "search").glob("*.json") if p.stem != old_key]
    data = json.loads(entry.read_text(encoding="utf-8"))
    data["result"].pop("search_version")
    entry.write_text(json.dumps(data), encoding="utf-8")
    third = _search(client)
    assert not third["result"].get("cached") and len(halves) == 3
    assert "Risultato in cache incompleto" in client.get(f"/api/jobs/{third['id']}/log").json()["text"]


def test_local_search_with_stale_metadata_is_not_cached(client, monkeypatch, tmp_path):
    from bioloom.core.jobs import JobContext, ProcessResult
    from bioloom.engines import tools

    from .test_search import BLAST_XML

    db_dir = client.app.state.core.paths.databases / "pdbaa"
    db_dir.mkdir(parents=True)
    (db_dir / "pdbaa.pin").write_bytes(b"x")
    exe = tmp_path / "blastp"
    exe.write_text("")
    monkeypatch.setattr(tools, "find", lambda key: str(exe) if key == "blastp" else None)
    monkeypatch.setattr(JobContext, "run", lambda self, cmd, **kw: ProcessResult(0, BLAST_XML))
    stale = {"flag": True}

    def fake_metadata(ids, check=None, log=None):
        return {i: {"method": "X-RAY DIFFRACTION", "resolution": 1.8, "title": "Ubiquitina", "rfree": 0.2,
                    "released": "2010-01-01", "ligands": ["ZN"], **({"stale": True} if stale["flag"] else {})}
                for i in ids}

    monkeypatch.setattr(remote, "rcsb_metadata", fake_metadata)
    done = _search(client, engine="local")
    assert done["status"] == "completed", done
    hits = done["result"]["hits"]
    assert hits[0]["rfree"] == 0.2 and hits[0]["ligands"] == ["ZN"] and "metadata_stale" not in hits[0]
    assert "risultato non salvato nella cache" in client.get(f"/api/jobs/{done['id']}/log").json()["text"]
    assert not list((client.app.state.core.paths.compute_cache / "search").glob("*.json"))
    stale["flag"] = False
    _search(client, engine="local")
    assert _search(client, engine="local")["result"]["cached"] is True


# ------------------------------------------------------------------------------------------ pipeline multi
class CapturingModeller:
    """Sostituisce ComputeService.run_modeling e registra i template ricevuti."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, list[dict]]] = []
        self._lock = threading.Lock()

    def __call__(self, sub, *, target, templates_raw, **_kwargs) -> dict:
        with self._lock:
            self.calls.append((target.name, templates_raw))
        model = f"{target.name}.B99990001.pdb"
        (sub.workdir / model).write_text("ATOM\n")
        return {"models": [{"file": model, "best": True, "dope": -1000.0, "ga341": 0.9, "zdope": -1.1,
                            "molpdf": 50.0, "quality": {"grade": "A", "grade_score": 95.0, "rama_favored": 98.0,
                                                        "clashscore": 1.0}}],
                "templates": [{"code": t["id"]} for t in templates_raw]}


def _pipeline(client, **extra) -> dict:
    body = {"fasta": f">a\n{YEAST_UB}\n", "template_mode": "multi", "options": {"models": 1}, "quality": False,
            "parallel": 1, **extra}
    r = client.post("/api/pipeline", json=body)
    assert r.status_code == 200, r.text
    return wait_job(client, r.json()["id"])


def test_pipeline_multi_mode_uses_complementary_templates(client, halves, monkeypatch):
    fake = CapturingModeller()
    monkeypatch.setattr(client.app.state.core.compute, "run_modeling", fake)
    done = _pipeline(client)
    assert done["status"] == "completed", done
    assert fake.calls == [("a", [{"id": "AAAA", "chains": ["A"]}, {"id": "BBBB", "chains": ["B"]}])]
    [item] = client.get(f"/api/pipeline/{done['id']}/items").json()["items"]
    assert item["status"] == "completed" and item["template"] == "AAAA_A,BBBB_B"
    # identità del principale, copertura dell'insieme
    assert item["identity"] == hit("AAAA_A", start=1, end=38)["identity"] and item["coverage"] == 100.0
    assert item["twilight"] == 0
    metrics = item["metrics"]
    assert [t["role"] for t in metrics["templates"]] == ["principale", "complementare"]
    assert metrics["combined_coverage"] == 100.0 and metrics["excluded_templates"] == 1
    log = client.get(f"/api/jobs/{done['id']}/log").json()["text"]
    assert "Template complementare BBBB_B" in log and "Escluso CCCC_A" in log
    # modalità auto: un solo template (il migliore), copertura del solo template
    fake.calls.clear()
    auto = _pipeline(client, template_mode="auto")
    assert fake.calls == [("a", [{"id": "AAAA", "chains": ["A"]}])]
    [item] = client.get(f"/api/pipeline/{auto['id']}/items").json()["items"]
    assert item["template"] == "AAAA_A" and item["coverage"] == 50.0 and "templates" not in item["metrics"]
    # al massimo un template in più: con max_templates=2 restano gli stessi due
    fake.calls.clear()
    _pipeline(client, max_templates=2)
    assert len(fake.calls[0][1]) == 2


def test_pipeline_multi_validation(client):
    body = {"fasta": f">a\n{YEAST_UB}\n", "template_mode": "multi"}
    for bad in (1, 6):
        assert client.post("/api/pipeline", json={**body, "max_templates": bad}).status_code == 422
    assert client.post("/api/pipeline", json={**body, "template_mode": "boh"}).status_code == 422
    from bioloom.core.errors import AppError
    from bioloom.services import pipeline

    assert pipeline.max_templates({}) == 3 and pipeline.max_templates({"max_templates": "4"}) == 4
    for bad in (1, 6, "x", None, True):
        with pytest.raises(AppError):
            pipeline.max_templates({"max_templates": bad})


def test_pipeline_multi_twilight_filter_uses_combined_coverage(client, monkeypatch):
    # ognuno copre il 30% (sotto la copertura minima del 40%), insieme il 60%: non filtrato
    def fake(sequence, evalue=10.0, max_hits=50, check=None, log=None):
        return [hit("AAAA_A", start=1, end=23), hit("BBBB_A", start=31, end=53), hit("CCCC_A", start=54, end=76)]

    monkeypatch.setattr(remote, "rcsb_sequence_search", fake)
    fake_modeller = CapturingModeller()
    monkeypatch.setattr(client.app.state.core.compute, "run_modeling", fake_modeller)
    tw = {"filter": True, "identity": 25.0, "coverage": 40.0}
    done = _pipeline(client, twilight=tw, max_templates=2)
    [item] = client.get(f"/api/pipeline/{done['id']}/items").json()["items"]
    assert item["status"] == "completed" and item["coverage"] == pytest.approx(60.5, abs=0.1)
    auto = _pipeline(client, twilight=tw, template_mode="auto")
    [item] = client.get(f"/api/pipeline/{auto['id']}/items").json()["items"]
    assert item["status"] == "filtered"


# ------------------------------------------------------------------------------------ allineamento su più template
def _pir(*rows: tuple[str, str, str]) -> str:
    """PIR da (codice, riga descrittiva senza codice, sequenza allineata)."""
    return "\n\n".join(f">P1;{code}\n{head}\n{seq}*" for code, head, seq in rows) + "\n"


def _pair(code: str, first: int, target: str, template: str) -> str:
    return _pir((code, f"structureX:{code}:{first}:A:+{sum(c.isalpha() for c in template)}:A:::-1.00:-1.00",
                 template), ("a", "sequence:a:: :: ::: 0.00: 0.00", target))


def test_merge_pairwise_alignments_on_the_target():
    # bersaglio MQIFVKTLTG. N: residui 1–6 del bersaglio con un'inserzione di 2 residui del template dopo il 3;
    # C: residui 5–10 con un gap del template di fronte all'8
    n = _pair("N_A", 1, "MQI--FVKTLTG", "MQIAAFVK----")
    c = _pair("C_A", 20, "MQIFVKTLTG", "----VKT-TG")
    merged = multitemplate.merge_on_target([n, c])
    assert [e.code for e in merged] == ["N_A", "C_A", "a"]
    rows = {e.code: e.sequence for e in merged}
    assert rows["a"] == "MQI--FVKTLTG"
    assert rows["N_A"] == "MQIAAFVK----"
    assert rows["C_A"] == "------VKT-TG"
    assert merged[0].fields[:4] == ["1", "A", "+8", "A"] and merged[2].kind == "sequence"
    # ogni template resta allineato come nel suo allineamento a coppie
    for code, pair in (("N_A", n), ("C_A", c)):
        t_row, target_row = rows[code], rows["a"]
        tgt_pairs = [(a, b) for a, b in zip(target_row, t_row) if a.isalpha()]
        parsed = sequences.parse_pir(pair)
        expected = [(a, b if b.isalpha() else "-") for a, b in zip(parsed[1].sequence, parsed[0].sequence)
                    if a.isalpha()]
        assert tgt_pairs == expected
    # inserzioni di due template nello stesso punto: colonne condivise, allineate a sinistra
    x = _pair("X_A", 1, "MQ---IFVKTLTG", "MQWWWIFVKTLTG")
    y = _pair("Y_A", 1, "MQ-IFVKTLTG", "MQYIFVKTLTG")
    rows = {e.code: e.sequence for e in multitemplate.merge_on_target([x, y])}
    assert rows == {"X_A": "MQWWWIFVKTLTG", "Y_A": "MQY--IFVKTLTG", "a": "MQ---IFVKTLTG"}
    with pytest.raises(ValueError):
        multitemplate.merge_on_target([x, _pair("Z_A", 1, "MQIFVKTLTA", "MQIFVKTLTA")])


def test_choose_salign_or_pairwise():
    target = YEAST_UB
    n = _pair("N_A", 1, target, HUMAN_UB[:50] + "-" * 26)
    c = _pair("C_A", 30, target, "-" * 29 + HUMAN_UB[29:])
    # salign coerente: stesse colonne degli allineamenti a coppie → resta salign
    good = _pir(("N_A", "structureX:N_A.pdb:1:A:+50:A:::-1.00:-1.00", HUMAN_UB[:50] + "-" * 26),
                ("C_A", "structureX:C_A.pdb:30:A:+47:A:::-1.00:-1.00", "-" * 29 + HUMAN_UB[29:]),
                ("a", "sequence:a:: :: ::: 0.00: 0.00", target))
    choice = multitemplate.choose(good, [n, c])
    assert choice["strategy"] == "salign" and choice["text"] == good and "coerente" in choice["message"]
    assert {r["code"]: (r["salign"], r["pairwise"]) for r in choice["templates"]} == {"N_A": (47, 47),
                                                                                      "C_A": (47, 47)}
    # salign che sposta C all'inizio del bersaglio (come con i frammenti di 1UBQ): unione degli align2d
    shifted = _pir(("N_A", "structureX:N_A.pdb:1:A:+50:A:::-1.00:-1.00", HUMAN_UB[:50] + "-" * 26),
                   ("C_A", "structureX:C_A.pdb:30:A:+47:A:::-1.00:-1.00", HUMAN_UB[29:] + "-" * 29),
                   ("a", "sequence:a:: :: ::: 0.00: 0.00", target))
    choice = multitemplate.choose(shifted, [n, c])
    assert choice["strategy"] == "pairwise" and "ha spostato C_A" in choice["message"]
    entries = sequences.parse_pir(choice["text"])
    assert [e.code for e in entries] == ["N_A", "C_A", "a"] and entries[2].sequence == target
    assert sequences.validate_pir(choice["text"])["valid"]
    # salign non riuscito: unione degli align2d; senza align2d utilizzabili è un errore
    assert multitemplate.choose(None, [n, c])["strategy"] == "pairwise"
    with pytest.raises(ValueError):
        multitemplate.choose(None, [])
    # template a più catene: non si uniscono, resta salign con l'avviso
    multi_chain = _pair("M_A", 1, target[:40] + "/" + target[40:], HUMAN_UB[:40] + "/" + HUMAN_UB[40:])
    choice = multitemplate.choose(shifted, [n, multi_chain])
    assert choice["strategy"] == "salign"
    assert multitemplate.mergeable([n, c]) and not multitemplate.mergeable([n, multi_chain])


def test_alignment_script_writes_salign_and_pairwise_alignments():
    templates = [{"file": f"{code}.pdb", "code": code, "chains": ["A"]} for code in ("N_A", "C_A")]
    script = modeller.build_alignment_script(templates, "a")
    compile(script, "align.py", "exec")
    assert "try:" in script and modeller.SALIGN_FILE in script and "except Exception" in script
    assert script.count("pair.align2d(max_gap_length=50)") == 2
    assert modeller.pair_file(0) in script and modeller.pair_file(1) in script
    single = modeller.build_alignment_script(templates[:1], "a")
    assert "salign" not in single and "alignment.ali" in single and "pair.align2d" not in single


# ------------------------------------------------------------------------------------------ MODELLER reale
def _fragment(path: Path, first: int, last: int) -> str:
    lines = [ln for ln in path.read_text(encoding="utf-8").splitlines()
             if ln.startswith("ATOM") and first <= int(ln[22:26]) <= last]
    return "\n".join(lines + ["TER", "END"]) + "\n"


def _ca(text: str) -> dict[int, list[float]]:
    return {int(ln[22:26]): [float(ln[30:38]), float(ln[38:46]), float(ln[46:54])] for ln in text.splitlines()
            if ln.startswith("ATOM") and ln[12:16] == " CA " and ln[21] in "A "}


def _tm_to_1ubq(model_text: str) -> float:
    """TM-score del modello rispetto a 1UBQ sui Cα con lo stesso numero di residuo, normalizzato su 76."""
    model, ref = _ca(model_text), _ca((DATA / "1UBQ.pdb").read_text(encoding="utf-8"))
    common = sorted(set(model) & set(ref))
    return geometry.tm_score_search(np.array([model[i] for i in common]), np.array([ref[i] for i in common]),
                                    76)["tm_score"]


@requires_modeller
@pytest.mark.parametrize("ranges", [((1, 50), (30, 76)), ((1, 40), (41, 76))], ids=["sovrapposti", "disgiunti"])
def test_pipeline_multi_with_two_real_templates(client, monkeypatch, ubq_path, ranges):
    """Due frammenti di 1UBQ come template: MODELLER con entrambi costruisce il modello completo di 76 residui, uguale
    a 1UBQ (TM-score misurato 0,998 con i frammenti sovrapposti, 0,997 con quelli disgiunti; con il solo salign, che
    allineava il secondo frammento dal residuo 1 del bersaglio, era 0,35)."""
    (a_first, a_last), (b_first, b_last) = ranges
    for name, first, last in (("UBQN", a_first, a_last), ("UBQC", b_first, b_last)):
        r = client.post("/api/templates/upload",
                        files={"file": (f"{name}.pdb", _fragment(ubq_path, first, last).encode(), "chemical/x-pdb")})
        assert r.status_code == 200, r.text

    def fake(sequence, evalue=10.0, max_hits=50, check=None, log=None):
        # il frammento C-terminale a 2,2 Å: il principale resta quello N-terminale
        return [hit("UBQN_A", start=a_first, end=a_last), hit("UBQC_A", start=b_first, end=b_last, resolution=2.2)]

    monkeypatch.setattr(remote, "rcsb_sequence_search", fake)
    done = _pipeline(client, options={"models": 1, "refinement": "very_fast"}, quality=True)
    assert done["status"] == "completed", done
    [item] = client.get(f"/api/pipeline/{done['id']}/items").json()["items"]
    assert item["status"] == "completed", item["error"]
    assert item["template"] == "UBQN_A,UBQC_A" and item["coverage"] == 100.0
    model = client.get(f"/api/jobs/{done['id']}/files/{item['best_model']}").content.decode()
    residues = [int(ln[22:26]) for ln in model.splitlines() if ln.startswith("ATOM") and ln[12:16] == " CA "]
    assert residues == list(range(1, 77))  # modello completo, un Cα per residuo
    assert "TEMPLATES UBQN_A UBQC_A" in model
    folder = item["best_model"].rsplit("/", 1)[0]
    ali = client.get(f"/api/jobs/{done['id']}/files/{folder}/alignment.ali").text
    assert ali.count(">P1;UBQN_A") == 1 and ali.count(">P1;UBQC_A") == 1
    # entrambi i template preparati nella cartella dell'elemento, con i loro intervalli
    for code, first, last in (("UBQN_A", a_first, a_last), ("UBQC_A", b_first, b_last)):
        prepared = client.get(f"/api/jobs/{done['id']}/files/{folder}/{code}.pdb").text
        numbers = sorted({int(ln[22:26]) for ln in prepared.splitlines() if ln.startswith("ATOM")})
        assert (numbers[0], numbers[-1]) == (first, last)
    assert item["grade"] in ("A", "B") and math.isfinite(float(item["dope"]))
    # il modello ricalca 1UBQ: ogni frammento allineato sul bersaglio dal suo align2d
    assert _tm_to_1ubq(model) > 0.95
    log = client.get(f"/api/jobs/{done['id']}/log").json()["text"]
    assert "allineamento di ogni template sul bersaglio (align2d)" in log
    for i in (1, 2):
        assert client.get(f"/api/jobs/{done['id']}/files/{folder}/{modeller.pair_file(i - 1)}").status_code == 200


@requires_modeller
def test_modeling_with_two_full_length_templates_keeps_salign(client, ubq_path):
    """Due template completi (1UBQ e una sua copia): salign è coerente con gli align2d e resta quello di prima."""
    for name in ("UBQA", "UBQB"):
        r = client.post("/api/templates/upload",
                        files={"file": (f"{name}.pdb", ubq_path.read_bytes(), "chemical/x-pdb")})
        assert r.status_code == 200, r.text
    body = {"target": {"name": "yeast", "sequence": YEAST_UB}, "templates": [{"id": "UBQA", "chains": ["A"]},
                                                                              {"id": "UBQB", "chains": ["A"]}],
            "options": {"models": 1, "refinement": "very_fast"}, "quality": "none"}
    job = client.post("/api/model", json=body).json()
    done = wait_job(client, job["id"])
    assert done["status"] == "completed", done.get("error")
    log = client.get(f"/api/jobs/{done['id']}/log").json()["text"]
    assert "Allineamento dei template con salign, coerente" in log
    model = client.get(f"/api/jobs/{done['id']}/files/{done['result']['best_model']}").text
    assert _tm_to_1ubq(model) > 0.95
    assert client.get(f"/api/jobs/{done['id']}/files/alignment.pap").status_code == 200
