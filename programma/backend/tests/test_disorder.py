"""Regioni disordinate, bassa complessità e domini (F5.5): domain/disorder (FoldIndex, SEG, copertura), segnale
informativo del consenso, report HTML, rotte /api/bio/disorder e /api/bio/coverage.

Nessun accesso alla rete: UniProt e InterPro sono simulati con httpx.MockTransport sul client condiviso di
engines/remote.py (come test_alphafold). Sequenze di riferimento: ubiquitina umana (1UBQ, ripiegata) e
α-sinucleina umana (UniProt P37840, 140 residui, coda C-terminale acida disordinata).
"""

from __future__ import annotations

import math
import re
import threading
import xml.etree.ElementTree as ET

import httpx
import pytest

from makphi.core.errors import AppError
from makphi.domain import consensus, disorder, report, sequences
from makphi.engines import quality, remote
from makphi.services.coverage import CoverageService, local_pairs, pir_coverage
from makphi.services.templates import TemplateLibrary

from .conftest import DATA, HUMAN_UB
from .test_alphafold import af_pdb
from .test_report import NS, model_data, quality_with_dope, svgs

# α-sinucleina umana, UniProt P37840 (sequenza canonica, 140 aa; A30, E46, A53, S129 e Y125/133/136 al loro posto)
SNCA = ("MDVFMKGLSKAKEGVVAAAEKTKQGVAEAAGKTKEGVLYVGSKTKEGVVHGVATVAEKTKEQVTNVGGAVVTGVTAVAQKTVEGAGSIAAATGFVKKDQLGKNEEG"
        "APQEGILEDMPVDPDNEAYEMPSEEGYQDYEPEA")
# coda C-terminale dell'α-sinucleina (residui 96–140), acida e disordinata
SNCA_TAIL = SNCA[95:]
KD = disorder.KYTE_DOOLITTLE


def fi(h_norm: float, charge: float) -> float:
    """FoldIndex scritto dalla formula di Prilusky et al. 2005, per i valori calcolati a mano."""
    return 2.785 * h_norm - abs(charge) - 1.151


def h(aa: str) -> float:
    return (KD[aa] + 4.5) / 9


# ---------------------------------------------------------------------------------------------- FoldIndex
def test_sequence_constants():
    assert len(SNCA) == 140 and (SNCA[29], SNCA[45], SNCA[52], SNCA[128]) == ("A", "E", "A", "S")
    assert len(HUMAN_UB) == 76


@pytest.mark.parametrize("seq,expected", [
    ("IIIII", fi(1.0, 0.0)),  # idropatia massima (I = 4,5 → 1), neutra: 1,634, ripiegata
    ("KKKKK", fi(h("K"), 1.0)),  # (−3,9 + 4,5) / 9 = 0,067, carica +1: −1,965
    ("AAAAA", fi(0.7, 0.0)),  # (1,8 + 4,5) / 9 = 0,7: 0,7985
    ("KDKD", fi((h("K") + h("D")) / 2, 0.0)),  # cariche opposte: carica netta 0
    ("IXIX", fi(1.0, 0.0)),  # X non entra nelle medie
    ("BBB", fi(h("D"), 0.5)),  # B = media di D e N: stessa idropatia (−3,5), carica −0,5
])
def test_foldindex_formula(seq, expected):
    assert disorder.foldindex(seq) == pytest.approx([expected] * len(seq))


def test_foldindex_constants_by_hand():
    assert fi(1.0, 0.0) == pytest.approx(1.634)
    assert fi(h("K"), 1.0) == pytest.approx(-1.96533, abs=1e-5)
    assert disorder.foldindex("XXX") == [None, None, None]
    assert disorder.foldindex("") == []


def test_window_is_centred_and_shrinks_at_the_ends():
    seq = "I" * 30 + "K" * 30
    scores = disorder.foldindex(seq)
    # residuo 1: finestra 1–26 (solo I); residuo 60: 35–60 (solo K); residuo 30: 5–55 (26 I e 25 K)
    assert scores[0] == pytest.approx(fi(1.0, 0.0))
    assert scores[59] == pytest.approx(fi(h("K"), 1.0))
    assert scores[29] == pytest.approx(fi((26 * 1.0 + 25 * h("K")) / 51, 25 / 51))
    # finestra piena di 51 residui al centro: residuo 26 = finestra 1–51 (30 I, 21 K)
    assert scores[25] == pytest.approx(fi((30 + 21 * h("K")) / 51, 21 / 51))


def test_ubiquitin_global_foldindex_matches_protparam_gravy():
    """FoldIndex della sequenza intera (finestra più lunga della sequenza) con il GRAVY di Biopython (ProtParam, la
    stessa scala di Kyte-Doolittle calcolata da un'altra implementazione): ubiquitina −0,489, carica netta 0
    (K 7 + R 4 − D 5 − E 6), quindi 2,785·(−0,489 + 4,5)/9 − 1,151 = +0,090: sul lato ripiegato del confine di
    Uversky."""
    gravy = sequences.sequence_properties(HUMAN_UB)["gravy"]
    assert gravy == pytest.approx(-0.489, abs=5e-4)
    net = sum(disorder.CHARGE.get(c, 0.0) for c in HUMAN_UB)
    assert net == 0
    whole = disorder.foldindex(HUMAN_UB, window=1001)
    assert whole[0] == pytest.approx(fi((gravy + 4.5) / 9, 0.0), abs=1e-3)
    assert whole[0] == pytest.approx(0.090, abs=1e-3)


def test_ubiquitin_is_ordered():
    """Ubiquitina: nessuna regione disordinata lunga, frazione disordinata 0. I valori per residuo stanno vicino al
    confine (fra −0,17 e +0,26) come atteso da FoldIndex per una proteina piccola e carica."""
    p = disorder.predict(HUMAN_UB)
    assert p["regions"] == [] and p["fraction"] == 0.0
    assert -0.2 < p["scale"]["min"] < 0 < p["scale"]["max"] < 0.3
    assert p["low_complexity"]["regions"] == []
    assert len(p["scores"]) == 76 and p["window"] == 51 and p["length"] == 76


def test_ubiquitin_with_a_fixed_edge_window_would_be_wrongly_disordered():
    """Perché la finestra si accorcia alle estremità: l'ultima finestra completa (residui 26–76) ha FoldIndex
    −0,018; ripetuta sui 25 residui finali, con i valori negativi di 32–50, darebbe una regione disordinata 32–76 a
    una proteina ripiegata."""
    last_window = disorder.foldindex(HUMAN_UB[25:], window=1001)[0]
    assert last_window == pytest.approx(-0.018, abs=1e-3)
    fixed = [last_window if i >= 50 else v for i, v in enumerate(disorder.foldindex(HUMAN_UB))]
    assert disorder.disordered_regions(fixed) == [(31, 75)]


def test_alpha_synuclein_c_terminal_region_is_disordered():
    """α-sinucleina: la coda C-terminale (~96–140, acida e ricca di prolina, disordinata anche nei complessi) è una
    regione disordinata lunga. Tolleranza sull'inizio (90–105): FoldIndex sposta i confini fino a metà finestra. Il
    tratto N-terminale 1–60, disordinato in soluzione ma anfipatico, risulta ordinato: è il limite noto dell'analisi
    carica-idropatia (nessuna regione prima di 90)."""
    p = disorder.predict(SNCA)
    [region] = p["regions"]
    assert 90 <= region["start"] <= 105 and region["end"] == 140
    assert region["start"] == 98 and region["disordered"] == region["length"] == 43  # valore di riferimento
    assert 0.25 <= p["fraction"] <= 0.40
    assert min(v for v in p["scores"] if v is not None) < -0.6  # la coda più acida
    assert all(v > 0 for v in p["scores"][:90])


def test_regions_need_30_residues_and_tolerate_short_interruptions():
    neg, pos = -0.1, 0.1
    assert disorder.disordered_regions([neg] * 30) == [(0, 29)]
    assert disorder.disordered_regions([neg] * 29) == []
    # 20 + 3 tollerati + 10: una regione di 33 residui con 30 disordinati
    assert disorder.disordered_regions([neg] * 20 + [pos] * 3 + [neg] * 10) == [(0, 32)]
    # 4 residui sopra soglia spezzano la regione: 20 e 10 da soli sono troppo corti
    assert disorder.disordered_regions([neg] * 20 + [pos] * 4 + [neg] * 10) == []
    # i residui tollerati non contano nei 30; un valore mancante vale come interruzione
    assert disorder.disordered_regions([neg] * 15 + [None, pos] + [neg] * 15) == [(0, 31)]
    assert disorder.disordered_regions([neg] * 15 + [None, pos] + [neg] * 14) == []  # 29 disordinati
    assert disorder.disordered_regions([0.0] * 40) == []  # la soglia è stretta: FoldIndex 0 non è disordine
    assert disorder.ranges([False, True, True, False, True]) == [(1, 2), (4, 4)]


@pytest.mark.parametrize("text,code", [
    ("A" * (disorder.MAX_LENGTH + 1), "sequence_too_long"),
    ("ACGTACGTTTGACCAGTAGG", "not_protein"),  # ≥ 90% di A, C, G, T: acido nucleico (sequences.detect_kind)
    ("   12 *", "empty_sequence"),
    (">a\nMQIF\n>b\nMKV\n", "multiple_sequences"),
], ids=["troppo-lunga", "nucleotidica", "vuota", "due-record"])
def test_predict_validates_the_sequence(text, code):
    with pytest.raises(AppError) as exc:
        disorder.predict(text)
    assert exc.value.code == code and exc.value.status == 400


def test_predict_accepts_fasta_and_text_like_the_other_routes():
    assert disorder.predict(">sp|P0CG48|UBC\nmqif vktl\n")["length"] == 8  # l'intestazione non entra
    assert disorder.predict("MQIF VKTL 10\n")["length"] == 8
    longest = disorder.predict("MKVLE" * (disorder.MAX_LENGTH // 5))  # il limite è compreso
    assert longest["length"] == disorder.MAX_LENGTH == 5000


# ---------------------------------------------------------------------------------------------- SEG
def test_seg_entropy_and_probability_by_hand():
    one = disorder._counts("Q" * 12)
    assert disorder._entropy(one) == 0.0
    assert disorder._entropy(disorder._counts("ACDEFGHIKLMN")) == pytest.approx(math.log2(12))
    # 9 Q e 3 lettere diverse: −(0,75·log2 0,75 + 3·(1/12)·log2(1/12)) = 1,2075 bit ≤ K1
    assert disorder._entropy(disorder._counts("QQQQQQQQQACD")) == pytest.approx(1.2075, abs=1e-4)
    # P0 di 27 Q: ln 20 (quale lettera) + 0 (una sola permutazione) − 27·ln 20 = −26·ln 20
    assert disorder._log_probability(disorder._counts("Q" * 27), 27) == pytest.approx(-26 * math.log(20))
    # 27 Q e una D: ln(20·19) + ln 28 − 28·ln 20, più probabile: il tratto di soli Q vince
    mixed = disorder._log_probability(disorder._counts("Q" * 27 + "D"), 28)
    assert mixed == pytest.approx(math.log(380) + math.log(28) - 28 * math.log(20))
    assert mixed > -26 * math.log(20)


def test_seg_finds_a_polyglutamine_tract_exactly():
    """25 Q inseriti dopo il residuo 40 dell'ubiquitina (che ha già Q40 e Q41): tratto poli-Q 40–66 di 27 residui.
    Le finestre con almeno 6 Q restano sotto K2 e allargano il segmento grezzo; l'ottimizzazione (P0 minima) lo
    riporta ai soli Q, perché ogni residuo diverso aggiunto rende la composizione più probabile."""
    seq = HUMAN_UB[:40] + "Q" * 25 + HUMAN_UB[40:]
    p = disorder.predict(seq)
    assert p["low_complexity"]["regions"] == [{"start": 40, "end": 66, "length": 27, "composition": "Q 27"}]
    assert p["low_complexity"]["params"] == {"window": 12, "k1": 2.2, "k2": 2.5}
    assert disorder.low_complexity("Q" * 50) == [(0, 49)]
    assert disorder.low_complexity("Q" * 11) == []  # più corta della finestra
    assert disorder.low_complexity(HUMAN_UB) == []
    # una X interrompe la valutazione delle finestre che la contengono
    assert disorder.low_complexity("Q" * 20 + "X" + "Q" * 20) == [(0, 19), (21, 40)]


def test_seg_merges_overlapping_segments_and_is_deterministic():
    seq = "PAPAPAPAPAPAPAPAP" + HUMAN_UB + "SGSGSGSGSGSGSGSG"
    first = disorder.low_complexity(seq)
    assert first == disorder.low_complexity(seq)
    assert first[0][0] == 0 and first[-1][1] == len(seq) - 1
    assert all(a[1] < b[0] for a, b in zip(first, first[1:]))


# ---------------------------------------------------------------------------------------------- domini e copertura
INTERPRO_UBQ_TAIL = [
    {"accession": "IPR000626", "name": "Ubiquitin-like domain", "type": "domain",
     "locations": [{"start": 1, "end": 72}]},
    {"accession": "IPR019956", "name": "Ubiquitin domain", "type": "domain", "locations": [{"start": 1, "end": 76}]},
    {"accession": "IPR029071", "name": "Ubiquitin-like domain superfamily", "type": "homologous_superfamily",
     "locations": [{"start": 1, "end": 76}]},
    {"accession": "IPR999999", "name": "Coda di prova", "type": "domain", "locations": [{"start": 80, "end": 121}]},
    {"accession": "IPR000000", "name": "Famiglia", "type": "family", "locations": [{"start": 1, "end": 121}]},
]


def test_target_domains_merge_duplicates_and_keep_domains_only():
    domains = disorder.target_domains(INTERPRO_UBQ_TAIL, 121)
    assert [(d["start"], d["end"]) for d in domains] == [(1, 76), (80, 121)]
    assert domains[0]["accessions"] == ["IPR019956", "IPR000626"]
    assert domains[0]["name"] == "Ubiquitin domain / Ubiquitin-like domain"
    # senza voci "domain", le superfamiglie omologhe; con la lunghezza si taglia ciò che esce dalla sequenza
    only_sf = [e for e in INTERPRO_UBQ_TAIL if e["type"] != "domain"]
    assert [(d["start"], d["end"], d["type"]) for d in disorder.target_domains(only_sf, 50)] == \
        [(1, 50, "homologous_superfamily")]
    assert disorder.target_domains([], 10) == []


def test_coverage_assessment_for_alpha_synuclein():
    p = disorder.predict(SNCA)
    # template che copre 1–95 (la parte predetta ordinata): la coda 98–140 resta fuori
    out = disorder.coverage_assessment(140, p["regions"], [], range(1, 96))
    [w] = out["warnings"]
    assert w["kind"] == "disorder_uncovered" and (w["start"], w["end"]) == (98, 140) and "98–140" in w["text"]
    assert out["coverage"] == pytest.approx(67.9) and out["covered"] == [[1, 95]]
    assert "escludere dal bersaglio i tratti disordinati non coperti (98–140)" in out["suggestion"]
    # copertura completa: la regione è coperta, solo un'informazione
    full = disorder.coverage_assessment(140, p["regions"], [], range(1, 141))
    assert [w["kind"] for w in full["warnings"]] == ["disorder_covered"] and full["suggestion"] is None
    # il testo non dà per certo il disordine: anche la predizione può essere sbagliata (falsi positivi come 3IU0)
    assert "la predizione è sbagliata" in full["warnings"][0]["text"]
    # metà della regione scoperta (43 residui, 22 fuori) basta per l'avviso
    half = disorder.coverage_assessment(140, p["regions"], [], range(1, 119))
    assert half["warnings"][0]["kind"] == "disorder_uncovered" and "119–140" in half["warnings"][0]["text"]


def test_coverage_assessment_for_domains():
    domains = disorder.target_domains(INTERPRO_UBQ_TAIL, 121)
    out = disorder.coverage_assessment(121, [], domains, range(1, 77))
    status = {d["start"]: (d["status"], d["covered_pct"]) for d in out["domains"]}
    assert status == {1: ("covered", 100.0), 80: ("uncovered", 0.0)}
    assert [w["kind"] for w in out["warnings"]] == ["domain_uncovered"]
    assert out["suggestion"].startswith("Modella per domini")
    partial = disorder.coverage_assessment(121, [], domains, range(1, 100))  # 80–99: 20 su 42 = 47,6%
    assert [w["kind"] for w in partial["warnings"]] == ["domain_partial"] and "47,6%" in partial["warnings"][0]["text"]
    one = disorder.coverage_assessment(76, [], domains[:1], range(1, 30))  # un solo dominio, coperto a metà
    assert one["suggestion"].startswith("Cerca un template che copra il dominio")
    ok = disorder.coverage_assessment(121, [], domains, range(1, 122))
    assert ok["warnings"] == [] and ok["suggestion"] is None and ok["coverage"] == 100.0


def test_local_pairs_and_pir_coverage():
    target = HUMAN_UB + SNCA_TAIL
    pairs = local_pairs(target, HUMAN_UB)
    assert len(pairs) == 76 and pairs[0] == (1, 1) and pairs[-1] == (76, 76)
    assert local_pairs("", HUMAN_UB) == []
    pir = sequences.format_pir([sequences.template_entry("1UBQ_A", HUMAN_UB + "-" * 45, "1", "A", "76", "A"),
                                sequences.target_entry("t", target)])
    letters, sets, note = pir_coverage(pir)
    assert letters == target and note is None
    assert sets[0]["label"] == "1UBQ_A" and sets[0]["covered_positions"] == set(range(1, 77))
    two = sequences.format_pir([sequences.template_entry("1UBQ_A", HUMAN_UB + "/" + HUMAN_UB, "1", "A", "76", "B"),
                                sequences.target_entry("t", HUMAN_UB + "/" + HUMAN_UB)])
    assert pir_coverage(two)[2].startswith("Bersaglio con più catene")


# ---------------------------------------------------------------------------------------------- consenso
def synuclein_report(flagged: tuple[range, ...] = (range(20, 25), range(110, 115))) -> dict:
    """Rapporto di qualità sintetico di un modello di α-sinucleina (catena A, 1–140): outlier di Ramachandran e
    clash sugli stessi residui, così ogni tratto è una regione problematica (2 metodi)."""
    residues = []
    for i, aa in enumerate(SNCA, start=1):
        bad = any(i in r for r in flagged)
        residues.append({"chain": "A", "resnum": str(i), "resname": "ALA", "aa": aa,
                         "rama": "outlier" if bad else "favored", "clashes": 1 if bad else 0, "dope": None})
    return {"residues": residues, "grade": {"score": 72.5}, "dope": None, "procheck": None, "prosa": None}


def test_consensus_marks_disordered_regions_as_expected_unreliable():
    c = consensus.compute(synuclein_report())
    assert c["version"] == 2
    dis = c["disorder"]
    assert dis["informative"] and [(r["start"], r["end"], r["chain"]) for r in dis["regions"]] == [("98", "140", "A")]
    assert dis["flagged"] == 43 and dis["fraction"] == pytest.approx(43 / 140, abs=1e-4)
    assert sum(dis["flags"]) == 43 and dis["flags"][97] and not dis["flags"][96] and len(dis["scores"]) == 140
    assert "98-140 (catena A)" in dis["text"] and "non abbassa il punteggio" in dis["text"]
    by_start = {r["start"]: r for r in c["regions"]}
    assert set(by_start) == {"20", "110"}
    assert by_start["110"]["expected"] and by_start["110"]["disorder_overlap"] == 5
    assert "in una regione disordinata predetta (attesa poco affidabile)" in by_start["110"]["text"]
    assert not by_start["20"]["expected"] and by_start["20"]["disorder_overlap"] == 0
    assert "disordinata" not in by_start["20"]["text"]
    assert c["thresholds"]["disorder_min_region"] == 30 and c["thresholds"]["disorder_window"] == 51


def test_disorder_does_not_change_score_signals_or_regions():
    """Stesso rapporto con la sequenza dell'α-sinucleina e con una sequenza ordinata (tutta I): punteggio globale,
    segnali per residuo, conteggi e regioni identici; cambia solo il segnale informativo."""
    disordered = consensus.compute(synuclein_report())
    ordered_report = synuclein_report()
    for r in ordered_report["residues"]:
        r["aa"] = "I"
    ordered = consensus.compute(ordered_report)
    assert ordered["disorder"]["regions"] == [] and ordered["disorder"]["flagged"] == 0
    assert disordered["score"] == ordered["score"] == consensus.global_score({"grade": 72.5})["score"]
    assert disordered["methods"] == ordered["methods"] and disordered["methods_used"] == 1
    assert disordered["per_residue"] == ordered["per_residue"]
    expected_flags = consensus.residue_flags(synuclein_report()["residues"])
    assert disordered["per_residue"]["flags"] == expected_flags["flags"]
    assert "disorder" not in {s["key"] for s in disordered["per_residue"]["signals"]}

    def strip(regions):
        return [{k: v for k, v in r.items() if k not in ("text", "expected", "disorder_overlap")} for r in regions]

    assert strip(disordered["regions"]) == strip(ordered["regions"])


def test_region_needs_half_of_its_residues_in_the_disordered_region():
    # tratto 92–101: 4 residui su 10 (98–101) nella regione disordinata → non "atteso"; 94–101: 4 su 8 → sì
    c = consensus.compute(synuclein_report((range(92, 102),)))
    [r] = c["regions"]
    assert r["disorder_overlap"] == 4 and not r["expected"]
    c = consensus.compute(synuclein_report((range(94, 102),)))
    [r] = c["regions"]
    assert r["disorder_overlap"] == 4 and r["expected"]


def test_consensus_disorder_on_real_structures():
    """1UBQ e il dimero sintetico: nessuna regione disordinata, per catena (152 residui in due catene)."""
    rep = quality.analyze(DATA / "1UBQ.pdb", None, with_dope=False)
    dis = rep["consensus"]["disorder"]
    assert dis["regions"] == [] and dis["flagged"] == 0 and len(dis["scores"]) == 76
    assert "Nessuna regione disordinata lunga" in dis["text"]
    dimer = quality.analyze(DATA / "ubq_dimer.pdb", None, with_dope=False)
    ddis = dimer["consensus"]["disorder"]
    assert len(ddis["scores"]) == len(dimer["residues"]) == 152 and ddis["regions"] == []
    # stessa sequenza nelle due catene: stessi valori (le finestre non attraversano il confine di catena)
    assert ddis["scores"][:76] == ddis["scores"][76:] == dis["scores"]
    zn = quality.analyze(DATA / "ubq_zn.pdb", None, with_dope=False)
    assert zn["consensus"]["disorder"]["regions"] == []
    assert consensus.disorder_signal([]) is None


# ---------------------------------------------------------------------------------------------- report HTML
def tail_report() -> dict:
    """1UBQ con le lettere dei residui 41–76 sostituite dalla coda dell'α-sinucleina (105–140): la struttura resta
    quella di 1UBQ, la sequenza ha una regione disordinata (20–76)."""
    rep = quality_with_dope(DATA / "1UBQ.pdb")
    for row, aa in zip(rep["residues"][40:], SNCA[104:]):
        row["aa"] = aa
    return rep


def test_report_draws_the_disorder_track_next_to_the_profiles():
    rep = quality_with_dope(DATA / "1UBQ.pdb")
    text = report.render(model_data(rep))
    svg = next(s for s in svgs(text) if s.get("aria-label") == report.DISORDER_TITLE)
    assert not [r for r in svg.iter(f"{NS}rect") if r.get("class") == "shade"]  # ubiquitina: nessuna regione
    assert not [c for c in svg.iter(f"{NS}circle") if c.get("class") == "flag"]  # i singoli residui non si segnano
    assert "Prilusky J." in text and "Uversky V.N." in text and "Disordine predetto" in text
    assert "attesi poco affidabili" not in text  # nessuna nota senza regioni


def test_report_explains_disordered_regions():
    rep = tail_report()
    data = model_data(rep)
    data["consensus"] = consensus.compute(rep, modeller={"ga341": 1.0, "zdope": -2.47})
    assert [(r["start"], r["end"]) for r in data["consensus"]["disorder"]["regions"]] == [("20", "76")]
    text = report.render(data)
    svg = next(s for s in svgs(text) if s.get("aria-label") == report.DISORDER_TITLE)
    [shade] = [r for r in svg.iter(f"{NS}rect") if r.get("class") == "shade"]
    assert shade.find(f"{NS}title").text == "regione disordinata predetta 20-76 (catena A)"
    assert "Disordine predetto dalla sequenza" in text and "20-76 (catena A)" in text
    assert "sfondo rosa" in text
    ET.fromstring(svgs_text(text, report.DISORDER_TITLE))  # ben formato


def svgs_text(text: str, title: str) -> str:
    return next(s for s in re.findall(r"<svg\b.*?</svg>", text, re.S) if f'aria-label="{title}"' in s)


# ---------------------------------------------------------------------------------------------- rotte
class FakeEbi:
    """UniProt e InterPro simulati: voci per accessione e registro delle richieste."""

    def __init__(self) -> None:
        self.uniprot: dict[str, str] = {}
        self.interpro: dict[str, list[dict]] = {}
        self.calls: list[str] = []
        self.fail = False

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(str(request.url))
        if self.fail:
            return httpx.Response(503, text="Service Unavailable")
        path = request.url.path
        if request.url.host == "rest.uniprot.org" and path.startswith("/uniprotkb/"):
            acc = path.rsplit("/", 1)[1].removesuffix(".json")
            if acc not in self.uniprot:
                return httpx.Response(404, json={})
            seq = self.uniprot[acc]
            return httpx.Response(200, json={
                "primaryAccession": acc, "uniProtkbId": "TEST_HUMAN", "entryType": "UniProtKB reviewed (Swiss-Prot)",
                "proteinDescription": {"recommendedName": {"fullName": {"value": "Proteina di prova"}}},
                "sequence": {"value": seq, "length": len(seq)}})
        if request.url.host == "www.ebi.ac.uk" and "/interpro/api/entry/interpro/protein/uniprot/" in path:
            acc = path.rsplit("/", 1)[1]
            results = [{"metadata": {"accession": e["accession"], "name": e["name"], "type": e["type"]},
                        "proteins": [{"entry_protein_locations": [{"fragments": e["locations"]}]}]}
                       for e in self.interpro.get(acc, [])]
            return httpx.Response(200, json={"results": results})
        return httpx.Response(599, text="host inatteso")


@pytest.fixture
def ebi(monkeypatch, tmp_path):
    fake = FakeEbi()
    monkeypatch.setattr(remote, "_client", httpx.Client(transport=httpx.MockTransport(lambda r: fake.handler(r))))
    monkeypatch.setattr(remote, "_cache_dir", tmp_path / "remote_cache")
    return fake


def test_disorder_route(client, ebi):
    r = client.post("/api/bio/disorder", json={"sequence": HUMAN_UB})
    assert r.status_code == 200, r.text
    assert r.json()["fraction"] == 0 and r.json()["regions"] == []
    out = client.post("/api/bio/disorder", json={"sequence": f">sp|P37840|SYUA_HUMAN\n{SNCA}\n"}).json()
    assert [(x["start"], x["end"]) for x in out["regions"]] == [(98, 140)]
    assert out["method"].startswith("FoldIndex") and out["limits"]
    assert ebi.calls == []  # nessuna richiesta remota


@pytest.mark.parametrize("body,status,code", [
    ({"sequence": "A" * (disorder.MAX_LENGTH + 1)}, 400, "sequence_too_long"),
    ({"sequence": "ACGUACGUUUGACCAGUAGG"}, 400, "not_protein"),
    ({"sequence": ""}, 400, "empty_sequence"),
    ({"sequence": "A" * 200_001}, 422, "validation_error"),
    ({}, 422, "validation_error"),
    ({"sequence": 42}, 422, "validation_error"),
], ids=["troppo-lunga", "rna", "vuota", "corpo-enorme", "senza-sequenza", "non-testo"])
def test_disorder_route_rejects_bad_sequences(client, body, status, code):
    r = client.post("/api/bio/disorder", json=body)
    assert r.status_code == status and r.json()["error"]["code"] == code


def test_coverage_with_a_library_template_and_no_accession(client_with_ubq, ebi):
    target = HUMAN_UB + SNCA_TAIL + SNCA_TAIL  # ubiquitina con una coda disordinata di 90 residui
    r = client_with_ubq.post("/api/bio/coverage", json={"sequence": target, "templates": [{"id": "1ubq",
                                                                                            "chains": ["A"]}]})
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["length"] == 166 and out["domains"] == [] and out["domains_info"]["available"] is False
    assert "Nessuna accessione UniProt" in out["domains_info"]["note"]
    assert ebi.calls == []  # senza accessione nessun dominio e nessuna richiesta remota
    [t] = out["templates"]
    assert t["label"] == "1UBQ:A" and t["covered"] == [[1, 76]] and t["coverage"] == pytest.approx(45.8)
    region = out["disorder"]["regions"][-1]
    assert region["end"] == 166
    kinds = [w["kind"] for w in out["combined"]["warnings"]]
    assert "disorder_uncovered" in kinds
    w = next(w for w in out["combined"]["warnings"] if w["kind"] == "disorder_uncovered")
    assert "77–166" in w["text"]
    # il suggerimento nomina solo il tratto scoperto, non la parte della regione coperta dal template
    assert "non coperti (77–166)" in out["combined"]["suggestion"]


def test_coverage_with_interpro_domains(client_with_ubq, ebi):
    target = HUMAN_UB + SNCA_TAIL
    ebi.uniprot["P0CG48"] = target
    ebi.interpro["P0CG48"] = INTERPRO_UBQ_TAIL
    out = client_with_ubq.post("/api/bio/coverage", json={"sequence": target, "accession": "p0cg48",
                                                          "templates": [{"id": "1UBQ", "chains": ["A"]}]}).json()
    assert out["domains_info"] == {"source": "InterPro", "accession": "P0CG48", "available": True, "note": None}
    assert [(d["start"], d["end"]) for d in out["domains"]] == [(1, 76), (80, 121)]
    combined = out["combined"]
    assert {d["start"]: d["status"] for d in combined["domains"]} == {1: "covered", 80: "uncovered"}
    assert "domain_uncovered" in [w["kind"] for w in combined["warnings"]]
    assert combined["suggestion"].startswith("Modella per domini")
    # seconda richiesta: UniProt e InterPro dalla cache
    calls = len(ebi.calls)
    client_with_ubq.post("/api/bio/coverage", json={"sequence": target, "accession": "P0CG48"})
    assert len(ebi.calls) == calls


def test_coverage_domains_on_a_fragment_or_a_different_sequence(client, ebi):
    ebi.uniprot["P0CG48"] = "MGS" + HUMAN_UB + SNCA_TAIL  # il bersaglio è un tratto di UniProt (da 4)
    ebi.interpro["P0CG48"] = [{"accession": "IPR1", "name": "Ubiquitin", "type": "domain",
                               "locations": [{"start": 4, "end": 79}]}]
    out = client.post("/api/bio/coverage", json={"sequence": HUMAN_UB, "accession": "P0CG48"}).json()
    assert [(d["start"], d["end"]) for d in out["domains"]] == [(1, 76)]
    assert out["domains_info"]["note"] == "Bersaglio = residui 4–79 di UniProt P0CG48."
    assert out["templates"] == [] and out["combined"] is None
    other = client.post("/api/bio/coverage", json={"sequence": SNCA, "accession": "P0CG48"}).json()
    assert other["domains"] == [] and "non coincide" in other["domains_info"]["note"]


def test_coverage_with_unavailable_services_or_bad_accession(client, ebi):
    ebi.fail = True
    out = client.post("/api/bio/coverage", json={"sequence": HUMAN_UB, "accession": "P0CG48"})
    assert out.status_code == 200
    assert out.json()["domains"] == [] and out.json()["domains_info"]["note"].startswith("Domini non disponibili")
    ebi.fail = False
    missing = client.post("/api/bio/coverage", json={"sequence": HUMAN_UB, "accession": "Q9Z999"}).json()
    assert "non conosce" in missing["domains_info"]["note"]
    bad = client.post("/api/bio/coverage", json={"sequence": HUMAN_UB, "accession": "non-valida"})
    assert bad.status_code == 400 and bad.json()["error"]["code"] == "invalid_accession"


def test_coverage_with_search_ranges(client, ebi):
    ranges = [{"label": "1XQ8_A", "start": 1, "end": 95}, {"label": "intera", "start": 1, "end": 140},
              {"label": "rovesciato", "start": 140, "end": 120}]
    out = client.post("/api/bio/coverage", json={"sequence": SNCA, "ranges": ranges}).json()
    assert out["combined"] is None  # candidati alternativi: nessuna valutazione d'insieme
    by = {t["label"]: t for t in out["templates"]}
    assert [w["kind"] for w in by["1XQ8_A"]["warnings"]] == ["disorder_uncovered"]
    assert [w["kind"] for w in by["intera"]["warnings"]] == ["disorder_covered"]
    assert by["rovesciato"]["covered"] == [[120, 140]]
    too_many = client.post("/api/bio/coverage", json={"sequence": SNCA, "ranges": [ranges[0]] * 501})
    assert too_many.status_code == 422


def test_coverage_with_a_pir_alignment(client, ebi):
    target = HUMAN_UB + SNCA_TAIL + SNCA_TAIL
    pir = sequences.format_pir([sequences.template_entry("1UBQ_A", HUMAN_UB + "-" * 90, "1", "A", "76", "A"),
                                sequences.target_entry("t", target)])
    out = client.post("/api/bio/coverage", json={"alignment": pir}).json()
    assert out["length"] == 166 and out["templates"][0]["covered"] == [[1, 76]]
    assert "disorder_uncovered" in [w["kind"] for w in out["combined"]["warnings"]]
    bad = client.post("/api/bio/coverage", json={"alignment": ">P1;x\nstructureX:x:1:A:3:A::::\nAAA*\n"})
    assert bad.status_code == 400 and bad.json()["error"]["code"] == "invalid_pir"


def test_coverage_template_errors_do_not_hide_the_others(client_with_ubq, ebi):
    out = client_with_ubq.post("/api/bio/coverage", json={"sequence": HUMAN_UB, "templates": [
        {"id": "9ZZZ", "chains": ["A"]}, {"id": "1UBQ", "chains": ["Q"]}, {"id": "1UBQ", "chains": []}]}).json()
    errors = [t.get("error") for t in out["templates"]]
    assert "non presente" in errors[0] and "non ha la catena Q" in errors[1] and errors[2] is None
    assert out["templates"][2]["label"] == "1UBQ:A"  # senza catene: la prima
    assert out["combined"]["label"] == "1UBQ:A" and out["combined"]["coverage"] == 100.0


def test_concurrent_checks_on_the_same_template_agree(tmp_path, ebi):
    """Verifiche in parallelo (Modellazione e Ricerca aperte insieme) sullo stesso template senza analisi in cache: la
    scrittura atomica dell'analisi della libreria lascia risultati uguali e un file valido."""
    library = TemplateLibrary(tmp_path / "templates")
    library.save_upload("1UBQ.pdb", (DATA / "1UBQ.pdb").read_bytes())
    (tmp_path / "templates" / "1UBQ.analysis.json").unlink()
    service = CoverageService(library)
    target = HUMAN_UB + SNCA_TAIL + SNCA_TAIL
    results: list[dict] = []
    errors: list[Exception] = []

    def run() -> None:
        try:
            results.append(service.check(sequence=target, templates=[{"id": "1UBQ", "chains": ["A"]}]))
        except Exception as exc:  # riportata dal test principale
            errors.append(exc)

    threads = [threading.Thread(target=run) for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(30)
    assert not errors and len(results) == 6
    assert all(r == results[0] for r in results)
    assert results[0]["combined"]["covered"] == [[1, 76]]
    assert library.analyze("1UBQ")["chains"][0]["residues"] == 76


def test_coverage_of_an_alphafold_template_follows_the_plddt_trim(client, ebi):
    """Modello AlphaFold di prova (1UBQ con pLDDT 40 su 1–5 e 30 su 71–76): con il ritaglio a 70 quei residui non
    coprono il bersaglio, come nella modellazione; senza ritaglio lo coprono tutto."""
    r = client.post("/api/templates/upload", files={"file": ("AF-P0CG48-F1.pdb", af_pdb().encode(), "chemical/x-pdb")})
    assert r.status_code == 200 and r.json()["predicted"] is True
    body = {"sequence": HUMAN_UB, "templates": [{"id": "AF-P0CG48-F1", "chains": ["A"]}]}
    trimmed = client.post("/api/bio/coverage", json={**body, "plddt_min": 70}).json()["templates"][0]
    assert trimmed["covered"] == [[6, 70]] and "pLDDT < 70" in trimmed["note"]
    whole = client.post("/api/bio/coverage", json=body).json()["templates"][0]
    assert whole["covered"] == [[1, 76]] and whole["note"] is None
