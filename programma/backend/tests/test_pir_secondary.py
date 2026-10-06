"""Struttura secondaria per residuo delle voci di un PIR (Editor PIR): DSSP dei template, GOR del bersaglio."""

from __future__ import annotations

from pathlib import Path

import pytest

from bioloom.domain import pir_secondary, secondary, structure
from bioloom.domain.sequences import parse_pir

from .conftest import DATA, HUMAN_UB, ubq_variant


def pir(target: str, template: str, header: str = "structureX:1UBQ_A:1:A:76:A::::", code: str = "1UBQ_A") -> str:
    return f">P1;tgt\nsequence:tgt::::::::\n{target}*\n\n>P1;{code}\n{header}\n{template}*\n"


@pytest.fixture
def ubq_ss() -> str:
    model = structure.first_model(structure.load_structure(DATA / "1UBQ.pdb", "1UBQ"))
    return "".join(r["ss"] for r in secondary.assign(model))


UBQ_A = [{"id": "1UBQ", "chains": ["A"]}]


def post(client, content: str, templates: list | None = None) -> dict:
    r = client.post("/api/pir/secondary", json={"content": content, "templates": templates or []})
    assert r.status_code == 200, r.text
    return r.json()


def by_code(result: dict, code: str) -> dict:
    return next(e for e in result["entries"] if e["code"] == code)


def test_api_dssp_for_template_and_gor_for_target(client_with_ubq, ubq_ss):
    out = post(client_with_ubq, pir(HUMAN_UB, HUMAN_UB), UBQ_A)
    tpl = by_code(out, "1UBQ_A")
    assert tpl["source"] == "dssp" and tpl["matched_by"] == "code"
    assert tpl["template"] == {"id": "1UBQ", "chains": ["A"]}
    assert tpl["ss"] == ubq_ss and len(tpl["ss"]) == 76
    # elica 24-33 e filamenti come nell'intestazione di 1UBQ (test_builtin_dssp_matches_header)
    assert set(tpl["ss"][23:33]) == {"H"} and set(tpl["ss"][2:5]) == {"E"}
    assert tpl["mismatch_count"] == 0 and tpl["problems"] == []
    tgt = by_code(out, "tgt")
    assert tgt["source"] == "gor" and len(tgt["ss"]) == 76 and set(tgt["ss"]) <= {"H", "E", "C"}


def test_gaps_do_not_change_the_per_residue_result(client_with_ubq, ubq_ss):
    gapped_target = HUMAN_UB[:10] + "---" + HUMAN_UB[10:] + "--"
    gapped_template = "--" + HUMAN_UB[:40] + "-" + HUMAN_UB[40:] + "--"
    out = post(client_with_ubq, pir(gapped_target, gapped_template), UBQ_A)
    assert by_code(out, "1UBQ_A")["ss"] == ubq_ss
    assert len(by_code(out, "tgt")["ss"]) == 76


def test_template_found_from_code_alone(client_with_ubq, ubq_ss):
    out = post(client_with_ubq, pir(HUMAN_UB, HUMAN_UB))
    tpl = by_code(out, "1UBQ_A")
    assert tpl["matched_by"] == "code_guess" and tpl["template"] == {"id": "1UBQ", "chains": ["A"]}
    assert tpl["ss"] == ubq_ss


def test_unknown_template_has_a_message(client):
    out = post(client, pir(HUMAN_UB, HUMAN_UB, code="9XYZ_A"))
    tpl = by_code(out, "9XYZ_A")
    assert tpl["ss"] is None and tpl["source"] is None and "non trovato" in tpl["message"]


def test_letters_different_from_the_file_are_reported(client_with_ubq):
    changed = HUMAN_UB[:1] + "K" + HUMAN_UB[2:]  # Q2K
    out = post(client_with_ubq, pir(HUMAN_UB, changed), UBQ_A)
    tpl = by_code(out, "1UBQ_A")
    assert tpl["mismatch_count"] == 1
    assert tpl["mismatches"] == [{"index": 2, "letter": "K", "residue": "Q A:2"}]


def test_start_residue_from_the_pir_header(client_with_ubq, ubq_ss):
    header = "structureX:1UBQ_A:10:A:76:A::::"
    out = post(client_with_ubq, pir(HUMAN_UB, "-" * 9 + HUMAN_UB[9:], header), UBQ_A)
    tpl = by_code(out, "1UBQ_A")
    assert tpl["ss"] == ubq_ss[9:] and tpl["mismatch_count"] == 0
    missing = post(client_with_ubq, pir(HUMAN_UB, HUMAN_UB, "structureX:1UBQ_A:500:A:76:A::::"))
    tpl = by_code(missing, "1UBQ_A")
    assert set(tpl["ss"]) == {"?"} and "500:A" in tpl["problems"][0]


def test_longer_entry_than_the_chain(client_with_ubq, ubq_ss):
    out = post(client_with_ubq, pir(HUMAN_UB + "AA", HUMAN_UB + "AA"))
    tpl = by_code(out, "1UBQ_A")
    assert tpl["ss"] == ubq_ss + "??" and "2 lettere senza residuo" in tpl["problems"][0]


def test_two_chains_follow_the_slash(ubq_ss):
    residues, ss = pir_secondary.structure_data(DATA / "ubq_dimer.pdb")
    dimer = HUMAN_UB + "/" + HUMAN_UB
    [entry] = [e for e in parse_pir(pir(dimer, dimer, "structureX:UBQD:1:A:76:B::::", "UBQD")) if e.is_template]
    out = pir_secondary.template_secondary(entry, residues, ss, [])
    assert out["chains"] == ["A", "B"]
    assert len(out["ss"]) == 152 and out["mismatch_count"] == 0 and out["problems"] == []


def test_selenomethionine_counts_as_met(tmp_path):
    path = ubq_variant(tmp_path / "mse.pdb", hetatm={1: "MSE"})
    residues, ss = pir_secondary.structure_data(path)
    assert residues["A"][0] == ("1", "M")
    [entry] = [e for e in parse_pir(pir(HUMAN_UB, HUMAN_UB)) if e.is_template]
    out = pir_secondary.template_secondary(entry, residues, ss, ["A"])
    assert out["mismatch_count"] == 0 and len(out["ss"]) == 76


def test_target_prediction_does_not_cross_chains():
    [target] = [e for e in parse_pir(pir("MKTAY/GGG", "MKTAY/GGG")) if e.kind == "sequence"]
    joined = secondary.predict_gor("MKTAY")["prediction"] + secondary.predict_gor("GGG")["prediction"]
    assert pir_secondary.target_secondary(target)["ss"] == joined


def test_templates_matched_by_position_when_codes_differ(tmp_path):
    def resolve(tid: str):
        return DATA / "1UBQ.pdb" if tid == "1UBQ" else None

    out = pir_secondary.annotate(pir(HUMAN_UB, HUMAN_UB, code="mytemplate"), UBQ_A, resolve)
    tpl = by_code(out, "mytemplate")
    assert tpl["matched_by"] == "position" and tpl["source"] == "dssp"


def test_request_limits(client):
    r = client.post("/api/pir/secondary", json={"content": "x" * 200_001})
    assert r.status_code == 422


def test_structure_data_is_cached(tmp_path: Path):
    path = ubq_variant(tmp_path / "copy.pdb")
    first = pir_secondary.structure_data(path)
    assert pir_secondary.structure_data(path) is first
