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
    assert tpl["template"] == {"id": "1UBQ", "chains": ["A"], "format": "pdb"}
    assert tpl["residue_ids"][:2] == ["A:1", "A:2"] and len(tpl["residue_ids"]) == 76
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
    assert tpl["matched_by"] == "code_guess" and tpl["template"] == {"id": "1UBQ", "chains": ["A"], "format": "pdb"}
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


def test_selenomethionine_follows_the_ligand_option(tmp_path, ubq_ss):
    """MSE nella copia per MODELLER solo con l'opzione dei ligandi; il DSSP la tiene sempre nella catena."""
    path = ubq_variant(tmp_path / "mse.pdb", hetatm={27: "MSE"})
    residues, ss = pir_secondary.structure_data(path)
    assert residues["A"][26] == ("27", "M", True)
    # l'elica 23-34 resta intera anche con la selenometionina in mezzo
    assert "".join(ss[("A", str(n))] for n in range(24, 34)) == ubq_ss[23:33]
    # il file di prova rinomina la Lys 27 di 1UBQ in MSE: con l'opzione dei ligandi nel PIR c'è una M
    with_met = [e for e in parse_pir(pir(HUMAN_UB, HUMAN_UB[:26] + "M" + HUMAN_UB[27:])) if e.is_template][0]
    without_met = [e for e in parse_pir(pir(HUMAN_UB, HUMAN_UB[:26] + HUMAN_UB[27:])) if e.is_template][0]
    on = pir_secondary.template_secondary(with_met, residues, ss, ["A"], hetatm=True)
    assert on["mismatch_count"] == 0 and on["ss"] == ubq_ss
    off = pir_secondary.template_secondary(without_met, residues, ss, ["A"], hetatm=False)
    assert off["mismatch_count"] == 0 and off["ss"] == ubq_ss[:26] + ubq_ss[27:]
    # opzione non nota: la lettura che corrisponde alle lettere, in entrambi i casi
    assert pir_secondary.template_secondary(with_met, residues, ss, ["A"])["mismatch_count"] == 0
    assert pir_secondary.template_secondary(without_met, residues, ss, ["A"])["ss"] == off["ss"]


def test_chains_in_file_order_unless_the_header_says_otherwise():
    residues, ss = pir_secondary.structure_data(DATA / "ubq_dimer.pdb")
    dimer = HUMAN_UB + "/" + HUMAN_UB
    [ab] = [e for e in parse_pir(pir(dimer, dimer, "structureX:UBQD_AB:1:A:76:B::::", "UBQD_AB")) if e.is_template]
    assert pir_secondary.template_secondary(ab, residues, ss, ["B", "A"])["chains"] == ["A", "B"]
    [ba] = [e for e in parse_pir(pir(dimer, dimer, "structureX:UBQD_BA:1:B:76:A::::", "UBQD_BA")) if e.is_template]
    assert pir_secondary.template_secondary(ba, residues, ss, ["B", "A"])["chains"] == ["B", "A"]
    # il codice con le catene in ordine di file si abbina anche se il documento le elenca al contrario
    assert "UBQD_AB" in pir_secondary.template_codes("UBQD", ["B", "A"])


def test_pairing_follows_modelling():
    """Come ComputeService._normalize_pir: voce per voce, codice se libero, altrimenti il primo template libero."""
    text = pir(HUMAN_UB, HUMAN_UB, code="foo") + pir(HUMAN_UB, HUMAN_UB, code="1UBQ_A").split("\n\n", 1)[1]
    entries = parse_pir(text)
    specs = [{"id": "1UBQ", "chains": ["A"]}, {"id": "UBQD", "chains": ["A"]}]
    pick = pir_secondary.pair_templates(entries, specs)
    assert pick == {1: (0, "position"), 2: (1, "position")}


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
