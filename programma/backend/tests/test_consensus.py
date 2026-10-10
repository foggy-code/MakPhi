"""Consenso tra i metodi di qualità (domain/consensus), grandezze per residuo (domain/perresidue), rotta del
consenso con QMEAN simulato e riconoscimento del pLDDT di AlphaFold."""

import json
import shutil

import pytest

from bioloom.domain import consensus, perresidue, plddt
from bioloom.domain import procheck as procheck_parser
from bioloom.engines import prosa, qmean_local, quality

from .conftest import DATA, wait_job

# Valori di riferimento misurati su 1UBQ (struttura cristallografica a 1,8 Å) con gli strumenti dell'immagine:
# MODELLER 10.8 (zDOPE e profilo DOPE in tests/data/dope_1ubq.profile), PROCHECK e ProSA II 2003 (file in
# tests/data, già usati da test_engines_extra).
UBQ_ZDOPE = -2.7335


def _dope_profile() -> list[dict]:
    """Profilo DOPE per residuo come lo legge engines/modeller.evaluate_dope (ultima colonna, già smussata)."""
    rows = []
    for line in (DATA / "dope_1ubq.profile").read_text().splitlines():
        parts = line.split()
        if len(parts) >= 3 and parts[0].isdigit():
            rows.append({"index": int(parts[0]), "resname": parts[1], "energy": float(parts[-1])})
    return rows


def _prosa_1ubq() -> dict:
    z = prosa.parse_zscores((DATA / "prosa_1ubq.slp").read_text())
    raw = prosa.parse_energy_profile((DATA / "prosa_1ubq_w1.ana").read_text())
    smooth = {r["index"]: r["combined"] for r in prosa.parse_energy_profile((DATA / "prosa_1ubq_w10.ana").read_text())}
    return {"zscore": z["combined"], "zscore_pair": z["pair"], "zscore_surface": z["surface"],
            "profile": [{"index": r["index"], "energy": r["combined"], "energy_w10": smooth.get(r["index"])}
                        for r in raw]}


@pytest.fixture(scope="module")
def ubq_report():
    """Rapporto completo di 1UBQ ricostruito con le uscite reali di MODELLER, PROCHECK e ProSA."""
    report = quality.analyze(DATA / "1UBQ.pdb", None, with_dope=False)
    profile = _dope_profile()
    assert len(profile) == len(report["residues"]) == 76
    for row, p in zip(report["residues"], profile):
        row["dope"] = round(p["energy"], 4)
    report["dope"] = {"score": -7981.95, "zscore": UBQ_ZDOPE, "profile": profile}
    report["procheck"] = procheck_parser.parse_sum((DATA / "procheck_1ubq.sum").read_text())
    report["prosa"] = _prosa_1ubq()
    return report


def _residues(n, chain="A", start=1):
    return [{"chain": chain, "resnum": str(start + i), "rama": "favored", "clashes": 0, "dope": None}
            for i in range(n)]


# ---------------------------------------------------------------------------------------------- scale e pesi
@pytest.mark.parametrize("key,raw,expected", [
    ("grade", 55, 40), ("grade", 70, 70), ("grade", 100, 100), ("grade", 85, 85),
    ("zdope", -1.0, 70), ("zdope", 0.0, 40), ("zdope", -2.0, 100), ("zdope", -3.5, 100), ("zdope", 3.0, 0),
    ("ga341", 0.7, 70), ("ga341", 1.0, 100), ("ga341", 0.35, 35),
    ("qmean", -4.0, 40), ("qmean", -2.0, 70), ("qmean", 0.0, 100), ("qmean", 1.2, 100), ("qmean", -10, 0),
    ("prosa", -6.0, 70), ("prosa", -2.0, 40), ("prosa", 0.0, 20), ("prosa", -9.26, 100),
    ("procheck", 90.0, 70), ("procheck", 70.0, 40), ("procheck", 95.5, 86.5), ("procheck", 40.0, 0),
])
def test_scales_map_literature_thresholds_to_band_limits(key, raw, expected):
    assert consensus.piecewise(raw, consensus.METHODS[key].points) == pytest.approx(expected)


@pytest.mark.parametrize("key", list(consensus.METHODS))
def test_scales_are_monotonic_and_bounded(key):
    m = consensus.METHODS[key]
    xs = [m.points[0][0] - 5 + i * (m.points[-1][0] - m.points[0][0] + 10) / 200 for i in range(201)]
    ys = [consensus.piecewise(x, m.points) for x in xs]
    assert all(0 <= y <= 100 for y in ys)
    increasing = m.points[-1][1] > m.points[0][1]
    pairs = list(zip(ys, ys[1:]))
    assert all(b >= a for a, b in pairs) if increasing else all(b <= a for a, b in pairs)
    # verso dichiarato: z-DOPE e ProSA migliori quando più negativi, gli altri quando più alti
    assert increasing == (key not in ("zdope", "prosa"))
    assert m.source and m.measures and m.weight > 0


def test_bands():
    assert consensus.band(70) == "buona" and consensus.band(69.9) == "intermedia"
    assert consensus.band(40) == "intermedia" and consensus.band(39.9) == "scarsa"


def test_weighted_mean_over_available_methods_only():
    out = consensus.global_score({"grade": 70.0, "qmean": -8.0})
    # (70·1 + 0·1,5) / 2,5
    assert out["score"] == 28.0 and out["band"] == "scarsa" and out["methods_used"] == 2
    assert out["methods_total"] == 6
    assert {m["key"] for m in out["methods"]} == {"grade", "qmean"}
    assert {m["key"] for m in out["missing"]} == {"zdope", "ga341", "prosa", "procheck"}
    assert all(m["reason"] for m in out["missing"])
    # un metodo assente non penalizza: stessa media con o senza chiavi a None
    assert consensus.global_score({"grade": 70.0, "qmean": -8.0, "ga341": None})["score"] == 28.0
    weights = {m["key"]: m["weight"] for m in consensus.global_score(
        {k: 0.0 for k in consensus.METHODS})["methods"]}
    assert weights == {"grade": 1.0, "zdope": 1.0, "ga341": 0.5, "qmean": 1.5, "prosa": 1.0, "procheck": 0.75}


def test_no_method_gives_no_score():
    out = consensus.global_score({})
    assert out["score"] is None and out["band"] is None and out["methods_used"] == 0


def test_discordance_between_good_and_poor_methods_is_explained():
    out = consensus.global_score({"ga341": 0.98, "zdope": 1.5, "grade": 60.0})
    found = consensus.discordances(out["methods"])
    assert len(found) == 1 and (found[0]["good"], found[0]["poor"]) == ("ga341", "zdope")
    text = found[0]["text"]
    assert "GA341 giudica il modello buono (0,98 → 98/100)" in text
    assert "z-DOPE scarso (1,50 → 10/100)" in text and "ripiegamento" in text
    # intermedio contro buono non è una discordanza
    assert consensus.discordances(consensus.global_score({"ga341": 0.98, "grade": 60.0})["methods"]) == []


def test_every_pair_of_scopes_has_an_explanation():
    scopes = {m.scope for m in consensus.METHODS.values()}
    for good in consensus.METHODS.values():
        for poor in consensus.METHODS.values():
            if good.key != poor.key and (good.scope, poor.scope) != ("fold", "fold"):
                assert (good.scope, poor.scope) in consensus._HINTS, (good.key, poor.key)
    assert scopes == {"geometry", "energy", "fold"}


# ---------------------------------------------------------------------------------------------- per residuo
def test_dope_threshold_is_relative_to_the_profile():
    values = [-0.05] * 20 + [-0.01, -0.005]
    t = consensus.dope_threshold(values)
    assert -0.05 < t < -0.01
    assert consensus.dope_threshold([-0.05] * 9 + [0.0]) == pytest.approx(-0.045 + 0.015)  # media + 1 DS
    assert consensus.dope_threshold([-0.05] * 5) is None  # troppo pochi valori
    assert consensus.dope_threshold([-0.05] * 20) is None  # profilo piatto: nessun picco
    assert consensus.dope_threshold([None] * 30) is None


def test_residue_flags_combine_the_available_methods():
    res = _residues(12)
    res[2]["rama"] = "outlier"
    res[2]["clashes"] = 3
    res[5]["rama"] = None  # estremità senza φ/ψ: nessuna segnalazione
    for r in res:
        r["dope"] = -0.05
    res[2]["dope"] = res[3]["dope"] = -0.005
    qm = [0.9] * 12
    qm[3] = 0.4
    pw = [None] + [-1.0] * 10 + [0.5]
    out = consensus.residue_flags(res, qmean_local=qm, prosa_w10=pw)
    assert [s["key"] for s in out["signals"]] == ["rama", "clash", "dope", "qmean", "prosa"]
    assert out["flags"][2] == ["rama", "clash", "dope"] and out["counts"][2] == 3
    assert out["flags"][3] == ["dope", "qmean"] and out["flags"][11] == ["prosa"]
    assert out["counts"][0] == 0 and out["values"]["qmean"][3] == 0.4
    # senza QMEAN, ProSA e DOPE restano Ramachandran e clash
    bare = consensus.residue_flags(_residues(4))
    assert [s["key"] for s in bare["signals"]] == ["rama", "clash"] and bare["values"] == {}


def _flags(n, hot, **extra):
    flags = [[] for _ in range(n)]
    for i in hot:
        flags[i] = ["rama", "dope"]
    for i, f in extra.items():
        flags[int(i[1:])] = f
    return flags


def test_regions_need_three_residues_flagged_by_two_methods():
    res = _residues(40)
    # 10–14 con un residuo tollerato (12, solo DOPE); 20–21 troppo corto; 25 e 28–29 separati da due residui
    flags = _flags(40, [9, 10, 12, 13, 19, 20, 24, 27, 28], r11=["dope"])
    regions = consensus.problem_regions(res, flags)
    assert len(regions) == 1
    r = regions[0]
    assert (r["chain"], r["start"], r["end"], r["length"], r["flagged"]) == ("A", "10", "14", 5, 4)
    assert (r["start_index"], r["end_index"]) == (9, 13)
    assert r["methods"] == ["rama", "dope"]
    assert r["text"] == "Ramachandran e DOPE segnalano 10-14 (catena A)"


def test_regions_tolerate_single_gaps_and_order_methods_by_frequency():
    res = _residues(12)
    flags = [[] for _ in range(12)]
    for i in (2, 4, 6):  # schema X.X.X: tre residui segnalati, due residui tollerati
        flags[i] = ["dope", "qmean", "prosa"] if i != 6 else ["dope", "clash"]
    (region,) = consensus.problem_regions(res, flags)
    assert (region["start"], region["end"], region["flagged"]) == ("3", "7", 3)
    assert region["methods"] == ["dope", "qmean", "prosa", "clash"]
    assert region["text"] == "DOPE, QMEAN, ProSA e clash segnalano 3-7 (catena A)"


def test_regions_do_not_cross_chains_or_numbering_gaps():
    res = _residues(3, "A", 28) + _residues(3, "B", 1)
    flags = [["rama", "clash"]] * 6
    regions = consensus.problem_regions(res, flags)
    assert [(r["chain"], r["start"], r["end"]) for r in regions] == [("A", "28", "30"), ("B", "1", "3")]
    gap = _residues(2, "A", 1) + _residues(2, "A", 10)  # residui 3–9 mancanti
    assert consensus.problem_regions(gap, [["rama", "clash"]] * 4) == []
    icode = [{"chain": "A", "resnum": n} for n in ("52", "52A", "53")]
    assert len(consensus.problem_regions(icode, [["rama", "clash"]] * 3)) == 1


# ---------------------------------------------------------------------------------------------- QMEAN e ProSA
def test_qmean_local_scores_are_mapped_per_chain():
    res = _residues(3, "A") + _residues(2, "B")
    local = [{"chain": "A", "index": i, "score": s} for i, s in ((1, 0.9), (2, 0.5), (3, None))] + \
            [{"chain": "B", "index": 1, "score": 0.7}, {"chain": "B", "index": 2, "score": 0.8}]
    values, warning = perresidue.qmean_local(res, local)
    assert values == [0.9, 0.5, None, 0.7, 0.8] and warning is None
    short, warning = perresidue.qmean_local(res, local[:2] + local[3:])
    assert short == [None] * 5 and "catena A: 2 punteggi per 3 residui" in warning
    other, warning = perresidue.qmean_local(res, [{"chain": "Z", "index": 1, "score": 0.5}])
    assert other == [None] * 5 and "catena Z assente" in warning
    assert perresidue.qmean_local(res, None) == ([None] * 5, None)
    malformed, warning = perresidue.qmean_local(res, [{"chain": "A", "index": "primo", "score": 0.5}])
    assert malformed == [None] * 5 and "formato" in warning


def test_prosa_window_profile_is_mapped_by_position(ubq_report):
    values, warning = perresidue.prosa_window(ubq_report["residues"], ubq_report["prosa"]["profile"])
    assert warning is None and len(values) == 76
    assert values[0] is None and values[4] == -1.121 and values[70] == -0.16 and values[75] is None
    bad, warning = perresidue.prosa_window(ubq_report["residues"][:50], ubq_report["prosa"]["profile"])
    assert bad == [None] * 50 and "non attribuibile" in warning


# ---------------------------------------------------------------------------------------------- 1UBQ
def test_1ubq_is_good_for_every_method(ubq_report):
    out = consensus.compute(ubq_report)
    by_key = {m["key"]: m for m in out["methods"]}
    assert set(by_key) == {"grade", "zdope", "prosa", "procheck"} and out["methods_used"] == 4
    assert by_key["grade"]["raw"] == pytest.approx(98.9, abs=0.2)
    assert by_key["zdope"]["normalized"] == 100.0 and by_key["prosa"]["normalized"] == 100.0
    assert by_key["procheck"]["normalized"] == 86.5
    assert all(m["band"] == "buona" for m in out["methods"])
    # (98,9 + 100 + 100 + 86,5·0,75) / 3,75
    assert out["score"] == pytest.approx(97.0, abs=0.2) and out["band"] == "buona"
    assert out["discordances"] == [] and out["regions"] == [] and out["warnings"] == []


def test_1ubq_per_residue_signals(ubq_report):
    per = consensus.compute(ubq_report)["per_residue"]
    assert [s["key"] for s in per["signals"]] == ["rama", "clash", "dope", "prosa"]
    flagged = {k: [ubq_report["residues"][i]["resnum"] for i, f in enumerate(per["flags"]) if k in f]
               for k in ("rama", "clash", "dope", "prosa")}
    # nessun outlier; un contatto grave su Arg72; picco DOPE sulla coda C-terminale flessibile (73–76);
    # nessuna finestra ProSA con energia positiva
    assert flagged == {"rama": [], "clash": ["72"], "dope": ["73", "74", "75", "76"], "prosa": []}
    assert per["dope_threshold"] == pytest.approx(-0.0355, abs=5e-4)
    assert max(per["counts"]) == 1


def test_1ubq_with_qmean_and_modeller(ubq_report):
    local = [{"chain": "A", "index": i + 1, "score": 0.85} for i in range(76)]
    out = consensus.compute(ubq_report, qmean={"qmean4_z": -0.5, "local": local},
                            modeller={"ga341": 1.0, "zdope": 0.5})
    by_key = {m["key"]: m for m in out["methods"]}
    assert out["methods_used"] == 6 and by_key["qmean"]["normalized"] == 92.5
    assert by_key["zdope"]["raw"] == UBQ_ZDOPE  # lo zDOPE della struttura prevale su quello di MODELLER
    assert by_key["ga341"]["normalized"] == 100.0 and out["band"] == "buona"
    assert "qmean" in out["per_residue"]["values"] and out["regions"] == []


def test_quality_report_contains_consensus_and_bfactor_kind(ubq_path):
    report = quality.analyze(ubq_path, None, with_dope=False)
    assert report["grade"]["grade"] == "A"  # il voto non cambia
    c = report["consensus"]
    assert c["version"] == consensus.VERSION and c["methods_used"] == 1 and c["band"] == "buona"
    assert any("Un solo metodo" in w for w in c["warnings"])
    assert len(c["per_residue"]["flags"]) == 76
    assert report["structure"]["bfactor_kind"] == "bfactor"  # B-factor cristallografici (tutti in 0–100)
    json.dumps(report)  # serializzabile


# ---------------------------------------------------------------------------------------------- pLDDT
AF_HEADER = (
    "HEADER                                            01-JUN-22                     \n"
    "TITLE     ALPHAFOLD MONOMER V2.0 PREDICTION FOR POLYUBIQUITIN-B (P0CG47)        \n"
    "REMARK   1                                                                      \n"
    "REMARK   1 REFERENCE 1                                                          \n"
    "REMARK   1  TITL   HIGHLY ACCURATE PROTEIN STRUCTURE PREDICTION WITH ALPHAFOLD  \n"
)


def _af_like(dest, header: str = AF_HEADER, plddt=lambda resnum: 92.5 if resnum <= 70 else 45.0):
    """1UBQ con il pLDDT al posto del B-factor (costante per residuo, come in AlphaFold DB)."""
    lines = []
    for line in (DATA / "1UBQ.pdb").read_text().splitlines():
        if line.startswith("ATOM"):
            value = plddt(int(line[22:26]))
            lines.append(f"{line[:60]}{value:6.2f}{line[66:]}")
    dest.write_text(header + "\n".join(lines) + "\nEND\n")
    return dest


def test_plddt_is_recognised_from_the_alphafold_header(tmp_path):
    report = quality.analyze(_af_like(tmp_path / "modello.pdb"), None, with_dope=False)
    assert report["structure"]["bfactor_kind"] == "plddt"
    by_num = {r["resnum"]: r["bfactor"] for r in report["residues"]}
    assert by_num["10"] == 92.5 and by_num["75"] == 45.0


def test_plddt_needs_alphafold_evidence_and_the_0_100_range(tmp_path):
    # stesse coordinate e stessi valori senza intestazione AlphaFold: B-factor normale
    plain = quality.analyze(_af_like(tmp_path / "modello.pdb", header=""), None, with_dope=False)
    assert plain["structure"]["bfactor_kind"] == "bfactor"
    # nome di AlphaFold DB (file scaricato o caricato con prefisso) anche senza intestazione
    named = _af_like(tmp_path / "1a2b3c4d_AF-P0CG47-F1-model_v4.pdb", header="")
    assert quality.analyze(named, None, with_dope=False)["structure"]["bfactor_kind"] == "plddt"
    # intestazione AlphaFold ma valori fuori da 0–100: non è pLDDT
    out_of_range = _af_like(tmp_path / "x.pdb", plddt=lambda n: 150.0)
    assert quality.analyze(out_of_range, None, with_dope=False)["structure"]["bfactor_kind"] == "bfactor"


@pytest.mark.parametrize("name,expected", [
    ("AF-P0CG48-F1-model_v4.pdb", True), ("AF-P0CG48-F1.pdb", True), ("AF-P0CG48-F1", True),
    ("af-p0cg48-f1-model_v4.cif", True), ("1a2b3c4d_AF-A0A024RBG1-F2-model_v3.pdb", True),
    ("AF-P0CG48-F1_A.pdb", True),  # copia preparata per MODELLER (id + catena)
    ("SAF-P0CG48-F1.pdb", False), ("AF-XYZ-F1.pdb", False), ("1UBQ.pdb", False), ("AF-P0CG48.pdb", False),
])
def test_alphafold_file_names(name, expected):
    assert bool(plddt.AF_NAME.search(name)) is expected


# Struttura cristallografica risolta per sostituzione molecolare con un modello AlphaFold: B-factor in 0–100
XRAY_FROM_AF = (
    "HEADER    HYDROLASE                               01-JAN-23   8ABC              \n"
    "TITLE     CRYSTAL STRUCTURE OF A HYDROLASE                                      \n"
    "EXPDTA    X-RAY DIFFRACTION                                                     \n"
    "REMARK 200 STARTING MODEL: ALPHAFOLD2 PREDICTION                                \n"
)
XRAY_FROM_AF_CIF = (
    "data_8ABC\n_entry.id 8ABC\n_exptl.entry_id 8ABC\n_exptl.method 'X-RAY DIFFRACTION'\n"
    "_pdbx_initial_refinement_model.id 1\n_pdbx_initial_refinement_model.source_name AlphaFold\n"
    "_refine.pdbx_starting_model AlphaFold\nloop_\n_atom_site.group_PDB\n_atom_site.id\n"
)
MODELLER_HEADER = (
    "EXPDTA    THEORETICAL MODEL, MODELLER 10.8 2026/09/27 10:40:27\n"
    "REMARK   6 MODELLER OBJECTIVE FUNCTION:       242.7604\n"
    "REMARK 999 BIOLOOM TEMPLATE AF-P0CG47-F1_A ALPHAFOLD PLDDT<70 REMOVED 11\n"
)
AF_CIF = ("data_AF-P0CG47-F1\n_software.name AlphaFold\n_ma_qa_metric.id 1\n_ma_qa_metric.name pLDDT\nloop_\n"
          "_atom_site.group_PDB\n")


def test_alphafold_detection_rules():
    kind = perresidue.bfactor_kind
    values = [12.3, 44.4, 88.0, 35.1]
    # file di AlphaFold DB (PDB e ModelCIF): pLDDT
    assert kind("modello.pdb", AF_HEADER, values) == "plddt"
    assert kind("modello.cif", AF_CIF, values) == "plddt"
    # un modello AlphaFold come punto di partenza di una struttura sperimentale non la rende predetta
    assert kind("8ABC.pdb", XRAY_FROM_AF, values) == "bfactor"
    assert kind("8ABC.cif", XRAY_FROM_AF_CIF, values) == "bfactor"
    # modello di MODELLER con il nome di un template AlphaFold (Biso di MODELLER, non pLDDT)
    assert kind("AF-P69905-F1-model_v4_A.B99990001.pdb", MODELLER_HEADER, values) == "bfactor"
    assert kind("AF-P69905-F1-model_v4_A.B99990001.pdb", "", values) == "plddt"  # solo il nome: AlphaFold DB
    # una menzione di AlphaFold che non è il TITLE di AlphaFold DB non basta
    assert kind("modello.pdb", "REMARK   1 HIGHLY ACCURATE PROTEIN STRUCTURE PREDICTION WITH ALPHAFOLD\n",
                values) == "bfactor"
    assert kind("modello.pdb", "HEADER    CHROMOSOMAL PROTEIN\nTITLE     STRUCTURE OF UBIQUITIN\n", values) == "bfactor"
    assert kind("AF-P0CG48-F1.pdb", "", [0.0, 0.0]) == "bfactor"  # colonna azzerata
    assert kind("AF-P0CG48-F1.pdb", "", []) == "bfactor"
    assert kind("AF-P0CG48-F1.pdb", AF_HEADER, [50.0, 120.0]) == "bfactor"  # fuori da 0–100
    assert plddt.declares_other_origin(XRAY_FROM_AF) and plddt.declares_other_origin(XRAY_FROM_AF_CIF)
    assert plddt.declares_other_origin(MODELLER_HEADER) and not plddt.declares_other_origin(AF_HEADER)


def test_experimental_structure_from_an_alphafold_starting_model(tmp_path):
    """Quality: EXPDTA X-RAY con REMARK 200 STARTING MODEL: ALPHAFOLD e B-factor in 0–100 → B-factor."""
    report = quality.analyze(_af_like(tmp_path / "8ABC.pdb", header=XRAY_FROM_AF), None, with_dope=False)
    assert report["structure"]["bfactor_kind"] == "bfactor"
    named = _af_like(tmp_path / "AF-P69905-F1-model_v4_A.B99990001.pdb", header=MODELLER_HEADER)
    assert quality.analyze(named, None, with_dope=False)["structure"]["bfactor_kind"] == "bfactor"


def test_library_quality_and_prepared_template_agree_on_alphafold_files(tmp_path):
    """Un solo riconoscimento: la libreria (modello predetto, ritaglio), il rapporto di qualità (pLDDT) e la copia
    del template preparata per MODELLER danno lo stesso esito sugli stessi file."""
    from bioloom.engines import modeller
    from bioloom.services.templates import TemplateLibrary

    files = tmp_path / "files"
    files.mkdir()
    cases = {  # nome del file, intestazione, pLDDT atteso
        "af_db": ("AF-P0CG47-F1-model_v4.pdb", AF_HEADER, True),  # modello di AlphaFold DB
        "af_title": ("mio_modello.pdb", AF_HEADER, True),  # file AlphaFold caricato con un altro nome
        "af_name": ("AF-P0CG47-F1.pdb", "", True),  # nome di AlphaFold DB, intestazione tolta
        "plain": ("ubiquitina.pdb", "", False),  # file normale con B-factor in 0–100
        "xray_from_af": ("8ABC.pdb", XRAY_FROM_AF, False),
        "modeller_af_name": ("AF-P69905-F1_A.B99990001.pdb", MODELLER_HEADER, False),
    }
    library = TemplateLibrary(tmp_path / "templates")
    for key, (filename, header, expected) in cases.items():
        path = _af_like(files / filename, header=header)
        uploaded = library.save_upload(filename, path.read_bytes())
        tid = uploaded["id"]
        assert uploaded["predicted"] is expected, key
        assert library.is_predicted(tid) is expected, key
        assert next(t for t in library.list() if t["id"] == tid)["predicted"] is expected, key
        kind = quality.analyze(library.path(tid), None, with_dope=False)["structure"]["bfactor_kind"]
        assert kind == ("plddt" if expected else "bfactor"), key
        assert quality.analyze(path, None, with_dope=False)["structure"]["bfactor_kind"] == kind, key
        # copia preparata (ritagliata se predetta): stesso esito anche con un nome qualunque
        spec = modeller.TemplateSpec(tid, ["A"], plddt_min=50 if expected else None)
        info = modeller.prepare_template(library.path(tid), spec, tmp_path / "job" / key)
        copy = tmp_path / "job" / key / info["file"]
        renamed = copy.with_name("copia.pdb")
        shutil.copyfile(copy, renamed)
        for candidate in (copy, renamed):
            assert quality.analyze(candidate, None, with_dope=False)["structure"]["bfactor_kind"] == kind, key


# ---------------------------------------------------------------------------------------------- rotta
def _fake_qmean(monkeypatch, *, low_from=70, qmean4_z=-1.0, calls=None):
    """QMEAN locale simulato: 76 punteggi per la catena A, sotto 0,6 dal residuo `low_from`."""

    def score(pdb_text, ctx, **_kw):
        if calls is not None:
            calls.append(pdb_text)
        local = [{"chain": "A", "index": i, "score": 0.4 if i >= low_from else 0.85} for i in range(1, 77)]
        return {"qmean4_z": qmean4_z, "qmean4": 0.7, "qmean6_z": None, "qmeandisco": 0.8, "components": {},
                "local": local, "method": "QMEAN", "qmean_version": "4.3.0", "source": "local"}

    monkeypatch.setattr(qmean_local, "score", score)


def _run(client, path, body):
    job = client.post(path, json=body).json()
    done = wait_job(client, job["id"])
    assert done["status"] == "completed", done["error"]
    return done


def test_consensus_route_for_a_quality_job_with_qmean(client_with_ubq, monkeypatch):
    c = client_with_ubq
    _fake_qmean(monkeypatch)
    source = {"type": "template", "id": "1UBQ"}
    qjob = _run(c, "/api/quality", {"source": source, "dope": False, "procheck": False, "prosa": False})
    assert qjob["result"]["consensus"]["methods_used"] == 1  # nel rapporto: solo ciò che l'analisi ha calcolato
    base = c.get(f"/api/jobs/{qjob['id']}/consensus").json()
    assert base["methods_used"] == 1 and base["inputs"]["qmean"] is None and base["inputs"]["modeller"] is None
    qm = _run(c, "/api/qmean", {"source": {"type": "template", "id": "1ubq"}})  # stessa struttura, altra grafia
    out = c.get(f"/api/jobs/{qjob['id']}/consensus", params={"qmean_job": qm["id"]}).json()
    by_key = {m["key"]: m for m in out["methods"]}
    assert out["methods_used"] == 2 and by_key["qmean"]["raw"] == -1.0 and by_key["qmean"]["normalized"] == 85.0
    assert out["inputs"]["qmean"] == {"job_id": qm["id"], "source": "local", "method": "QMEAN"}
    per = out["per_residue"]
    assert "qmean" in per["values"] and len(per["values"]["qmean"]) == 76
    assert [qjob["result"]["residues"][i]["resnum"] for i, f in enumerate(per["flags"]) if "qmean" in f] == \
        [str(n) for n in range(70, 77)]


def test_consensus_route_rejects_qmean_of_another_structure(client_with_ubq, monkeypatch):
    c = client_with_ubq
    _fake_qmean(monkeypatch)
    with open(DATA / "ubq_dimer.pdb", "rb") as fh:
        assert c.post("/api/templates/upload", files={"file": ("DIMER.pdb", fh)}).status_code == 200
    qjob = _run(c, "/api/quality", {"source": {"type": "template", "id": "1UBQ"}, "dope": False,
                                    "procheck": False, "prosa": False})
    other = _run(c, "/api/qmean", {"source": {"type": "template", "id": "DIMER"}})
    r = c.get(f"/api/jobs/{qjob['id']}/consensus", params={"qmean_job": other["id"]})
    assert r.status_code == 400 and r.json()["error"]["code"] == "qmean_source_mismatch"
    assert "template DIMER" in r.json()["error"]["message"]
    # un job che non è QMEAN, un job inesistente, un'analisi non conclusa
    r = c.get(f"/api/jobs/{qjob['id']}/consensus", params={"qmean_job": qjob["id"]})
    assert r.status_code == 400 and r.json()["error"]["code"] == "qmean_job_invalid"
    assert c.get(f"/api/jobs/{qjob['id']}/consensus", params={"qmean_job": "0123456789abcdef"}).status_code == 404
    assert c.get("/api/jobs/0123456789abcdef/consensus").status_code == 404


def test_consensus_route_rejects_qmean_of_a_replaced_template(client, monkeypatch):
    """Stesso id, file diverso: il template sostituito dopo l'analisi di qualità non riceve il QMEAN del nuovo file."""
    _fake_qmean(monkeypatch)
    library = client.app.state.core.library
    library.save_upload("X.pdb", (DATA / "1UBQ.pdb").read_bytes())
    qjob = _run(client, "/api/quality", {"source": {"type": "template", "id": "X"}, "dope": False,
                                         "procheck": False, "prosa": False})
    assert qjob["result"]["source_sha256"]
    # stesso file: il QMEAN vale
    same = _run(client, "/api/qmean", {"source": {"type": "template", "id": "X"}})
    assert same["result"]["source_sha256"] == qjob["result"]["source_sha256"]
    assert client.get(f"/api/jobs/{qjob['id']}/consensus", params={"qmean_job": same["id"]}).status_code == 200
    # un altro file caricato con lo stesso id (stesso numero di residui: anche i punteggi locali sarebbero attribuiti)
    lines = (DATA / "1UBQ.pdb").read_text().splitlines(keepends=True)
    i = next(n for n, line in enumerate(lines) if line.startswith("ATOM"))
    lines[i] = lines[i][:30] + f"{float(lines[i][30:38]) + 0.5:8.3f}" + lines[i][38:]
    library.save_upload("X.pdb", "".join(lines).encode(), overwrite=True)
    other = _run(client, "/api/qmean", {"source": {"type": "template", "id": "X"}})
    r = client.get(f"/api/jobs/{qjob['id']}/consensus", params={"qmean_job": other["id"]})
    assert r.status_code == 400 and r.json()["error"]["code"] == "qmean_source_mismatch"
    assert "è cambiato tra l'analisi di qualità e il calcolo QMEAN" in r.json()["error"]["message"]
    # job precedenti senza impronta: resta il confronto per id
    core = client.app.state.core
    stored = core.jobs.get(other["id"])["result"]
    stored.pop("source_sha256")
    core.db.execute("UPDATE jobs SET result=? WHERE id=?", (json.dumps(stored), other["id"]))
    assert client.get(f"/api/jobs/{qjob['id']}/consensus", params={"qmean_job": other["id"]}).status_code == 200


def test_consensus_route_needs_a_completed_qmean(client_with_ubq, monkeypatch):
    c = client_with_ubq
    monkeypatch.delenv("BIOLOOM_QMEAN_URL", raising=False)  # nessun QMEAN: il job fallisce
    qjob = _run(c, "/api/quality", {"source": {"type": "template", "id": "1UBQ"}, "dope": False,
                                    "procheck": False, "prosa": False})
    failed = wait_job(c, c.post("/api/qmean", json={"source": {"type": "template", "id": "1UBQ"}}).json()["id"])
    assert failed["status"] == "failed"
    r = c.get(f"/api/jobs/{qjob['id']}/consensus", params={"qmean_job": failed["id"]})
    assert r.status_code == 409


def _fake_model_job(client, ubq_path):
    """Job "model" simulato (senza MODELLER): modello, models.json e rapporto di qualità come li salva
    run_modeling, ma con un rapporto di una versione precedente (senza consenso)."""
    core = client.app.state.core
    name = "yeast.B99990001.pdb"

    def handler(ctx, p):
        shutil.copyfile(ubq_path, ctx.workdir / name)
        (ctx.workdir / "models.json").write_text(json.dumps({"models": [
            {"file": name, "failure": None, "molpdf": 350.0, "dope": -7900.0, "ga341": 0.62, "zdope": -1.1}]}))
        report = quality.analyze(ctx.workdir / name, None, with_dope=False)
        del report["consensus"]
        (ctx.workdir / f"{name}.quality.json").write_text(json.dumps(report))
        return {"models": [{"file": name, "ga341": 0.62, "zdope": -1.1, "best": True}], "best_model": name}

    core.jobs.register("model", handler)
    job = client.post("/api/model", json={"target": {"name": "yeast", "sequence": "MQIFVKTLTGKTITLEV"},
                                          "templates": [{"id": "1UBQ", "chains": ["A"]}]}).json()
    done = wait_job(client, job["id"])
    assert done["status"] == "completed", done["error"]
    return done, name


def test_consensus_route_for_a_model_uses_modeller_and_qmean(client_with_ubq, ubq_path, monkeypatch):
    c = client_with_ubq
    _fake_qmean(monkeypatch, low_from=200)  # nessun residuo sotto 0,6
    job, name = _fake_model_job(c, ubq_path)
    assert "consensus" not in c.get(f"/api/jobs/{job['id']}/quality/{name}").json()  # rapporto vecchio
    out = c.get(f"/api/jobs/{job['id']}/consensus/{name}").json()
    by_key = {m["key"]: m for m in out["methods"]}
    assert set(by_key) == {"grade", "zdope", "ga341"} and out["inputs"]["modeller"] == {"ga341": 0.62, "zdope": -1.1}
    assert by_key["ga341"]["normalized"] == 62.0 and by_key["zdope"]["raw"] == -1.1
    qm = _run(c, "/api/qmean", {"source": {"type": "job", "job_id": job["id"], "file": name}})
    out = c.get(f"/api/jobs/{job['id']}/consensus/{name}", params={"qmean_job": qm["id"]}).json()
    assert out["methods_used"] == 4 and "qmean" in {m["key"] for m in out["methods"]}
    # QMEAN di un'altra struttura (il template) respinto anche per i modelli
    tq = _run(c, "/api/qmean", {"source": {"type": "template", "id": "1UBQ"}})
    r = c.get(f"/api/jobs/{job['id']}/consensus/{name}", params={"qmean_job": tq["id"]})
    assert r.status_code == 400 and r.json()["error"]["code"] == "qmean_source_mismatch"
    # modello senza rapporto, e job di modellazione senza file indicato
    assert c.get(f"/api/jobs/{job['id']}/consensus/altro.pdb").status_code == 404
    r = c.get(f"/api/jobs/{job['id']}/consensus")
    assert r.status_code == 400 and r.json()["error"]["code"] == "consensus_model_required"


def test_quality_job_on_a_model_file_includes_modeller_metrics(client_with_ubq, ubq_path):
    c = client_with_ubq
    job, name = _fake_model_job(c, ubq_path)
    qjob = _run(c, "/api/quality", {"source": {"type": "job", "job_id": job["id"], "file": name},
                                    "dope": False, "procheck": False, "prosa": False})
    out = c.get(f"/api/jobs/{qjob['id']}/consensus").json()
    assert out["inputs"]["modeller"]["ga341"] == 0.62 and out["methods_used"] == 3


def test_plddt_template_from_the_library(client, tmp_path):
    """Un modello AlphaFold importato nella libreria (id AF-<ACC>-F1) viene riconosciuto."""
    path = _af_like(tmp_path / "AF-P0CG47-F1.pdb")
    with open(path, "rb") as fh:
        assert client.post("/api/templates/upload", files={"file": ("AF-P0CG47-F1.pdb", fh)}).status_code == 200
    done = _run(client, "/api/quality", {"source": {"type": "template", "id": "AF-P0CG47-F1"}, "dope": False,
                                         "procheck": False, "prosa": False})
    assert done["result"]["structure"]["bfactor_kind"] == "plddt"
