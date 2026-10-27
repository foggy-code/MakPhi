import numpy as np
import pytest

from makphi.domain import clashes, geometry, grading, ramachandran, secondary, structure
from makphi.domain.sequences import SeqRecord
from makphi.engines import align, quality


@pytest.fixture
def ubq_model(ubq_path):
    return structure.first_model(structure.load_structure(ubq_path, "1UBQ"))


def test_chain_summary_and_header(ubq_model, ubq_path):
    chains = structure.chain_summaries(ubq_model)
    assert chains[0]["chain"] == "A" and chains[0]["residues"] == 76
    assert chains[0]["sequence"].startswith("MQIFVKTLTG")
    header = structure.parse_header(ubq_path.read_text())
    assert header["resolution"] == 1.8
    assert {"type": "helix", "chain": "A", "start": 23, "end": 34} in header["secondary_structure"]


def test_ramachandran_reference_structure(ubq_model):
    summary = ramachandran.analyze(ubq_model)["summary"]
    # MolProbity riporta ~98% di residui favoriti su 1UBQ.
    assert summary["favored_pct"] >= 95
    assert summary["outlier"] == 0


def test_classification_regions():
    assert ramachandran.classify(-63, -41) == "favored"         # α-elica
    assert ramachandran.classify(-70, 145) == "favored"         # poliprolina II
    assert ramachandran.classify(60, 45) == "favored"           # α sinistrorsa
    assert ramachandran.classify(0, 0) == "outlier"
    assert ramachandran.classify(80, -170, "glycine") in ("favored", "allowed")
    assert ramachandran.classify(-60, -30, "proline") == "favored"
    assert ramachandran.classify(60, 60, "proline") == "outlier"


def test_clashscore_no_covalent_false_positives(ubq_model):
    result = clashes.analyze(ubq_model)
    # l'anello della prolina e le acque non vanno contati come clash
    assert result["clashscore"] < 5
    assert all("PRO" not in c["atom1"] + c["atom2"] for c in result["clashes"])


def test_builtin_dssp_matches_header(ubq_model):
    ss = secondary.assign(ubq_model)
    by_res = {r["resnum"]: r["ss"] for r in ss}
    helix = [by_res[str(i)] for i in range(24, 34)]
    assert helix.count("H") >= 9
    strands = [by_res[str(i)] for i in (3, 4, 5, 12, 13, 14, 41, 42, 43, 66, 67, 68)]
    assert strands.count("E") >= 10
    summary = secondary.summarize(ss)
    assert 15 < summary["helix_pct"] < 35 and 25 < summary["strand_pct"] < 45


def test_gor_prediction_shape():
    pred = secondary.predict_gor("AAAALLLLKKKKEEEEAAAAGPGPVVIVIVVTYTV")
    assert len(pred["prediction"]) == 35 and set(pred["prediction"]) <= set("HEC")


def test_grade_formula():
    assert grading.grade(0.0, 0.0)["grade"] == "A"
    g = grading.grade(2.5, 15.0)
    assert g["score"] == pytest.approx(100 - 16 - 10) and g["grade"] == "B"
    assert grading.grade(10, 60)["grade"] == "D"
    assert grading.grade(0, 0, dope_zscore=1.0)["penalties"]["dope"] == 20.0


def test_kabsch_and_tm_score_on_rotated_copy():
    rng = np.random.default_rng(1)
    coords = rng.normal(size=(60, 3)) * 10
    theta = 0.7
    rot = np.array([[np.cos(theta), -np.sin(theta), 0], [np.sin(theta), np.cos(theta), 0], [0, 0, 1]])
    moved = coords @ rot.T + np.array([5.0, -3.0, 2.0])
    r, t = geometry.kabsch(moved, coords)
    assert geometry.rmsd(geometry.apply(moved, r, t), coords) < 1e-6
    res = geometry.tm_score_search(moved, coords, 60)
    assert res["tm_score"] == pytest.approx(1.0, abs=1e-6)


def test_center_star_msa():
    recs = [SeqRecord("a", "MKTAYIAKQR"), SeqRecord("b", "MKTAYIAKR"), SeqRecord("c", "MKTYIAKQR")]
    aligned = align.center_star(recs)
    assert len({len(s) for s in aligned}) == 1
    assert [s.replace("-", "") for s in aligned] == [r.sequence for r in recs]


def test_quality_report_without_dope(ubq_path):
    report = quality.analyze(ubq_path, None, with_dope=False)
    assert report["grade"]["grade"] == "A"
    assert report["structure"]["residues"] == 76
    assert len(report["residues"]) == 76
    # SASA senza acque cristallografiche: confrontabile con mkdssp (RSA media 0,39 su 1UBQ)
    assert 0.3 < report["sasa"]["mean_rsa"] < 0.45
    assert report["dope"] is None
