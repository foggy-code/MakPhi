"""Composizione delle catene (domain/composition): monomero, omo- ed etero-oligomero, dalle entità del file o per
sequenza; entità dal COMPND del PDB e da _entity_poly del mmCIF."""

from pathlib import Path

from bioloom.domain import composition, structure

DATA = Path(__file__).parent / "data"
UBQ = "MQIFVKTLTGKTITLEVEPSDTIENVKAKIQDKEGIPPDQQRLIFAGKQLEDGRTLSDYNIQKESTLHLVLRLRGG"
HBA = "VLSPADKTNVKAAWGKVGAHAGEYGAEALERMFLSFPTTKTYFPHFDLSHGSAQVKGHGKKVADALTNAVAHV"
HBB = "VHLTPEEKSAVTALWGKVNVDEVGGEALGRLLVVYPWTQRFFESFGDLSTPDAVMGNPKVKAHGKKVLGAFSD"


def chain(cid: str, seq: str) -> dict:
    return {"chain": cid, "sequence": seq}


def test_monomer_and_empty():
    assert composition.describe([chain("A", UBQ)])["kind"] == "monomer"
    assert composition.describe([])["kind"] == "none"


def test_homodimer_by_sequence_tolerates_missing_residues_and_point_mutation():
    partial = UBQ[3:60]                       # residui mancanti alle estremità
    mutant = UBQ[:20] + "A" + UBQ[21:]        # una mutazione puntiforme
    out = composition.describe([chain("A", UBQ), chain("B", partial), chain("C", mutant)])
    assert out["kind"] == "homo" and out["source"] == "sequence"
    assert out["stoichiometry"] == "A3"
    assert out["entities"][0]["chains"] == ["A", "B", "C"]


def test_heterotetramer_by_sequence():
    out = composition.describe([chain("A", HBA), chain("B", HBB), chain("C", HBA), chain("D", HBB)])
    assert out["kind"] == "hetero" and out["stoichiometry"] == "A2B2"
    assert [e["chains"] for e in out["entities"]] == [["A", "C"], ["B", "D"]]


def test_seqres_preferred_over_observed_residues():
    # i residui osservati sono troppo pochi per un confronto, la SEQRES dice che sono due proteine diverse
    out = composition.describe([chain("A", "GG"), chain("B", "GG")], seqres={"A": HBA, "B": HBB})
    assert out["kind"] == "hetero"


def test_declared_entities_win_and_are_ordered_by_copies():
    entities = [{"name": "BETA", "chains": ["B"]}, {"name": "ALPHA", "chains": ["A", "C"]}]
    out = composition.describe([chain("A", UBQ), chain("B", UBQ), chain("C", UBQ)], entities)
    assert out["source"] == "file" and out["kind"] == "hetero" and out["stoichiometry"] == "A2B"
    assert out["entities"][0]["name"] == "ALPHA"


def test_incomplete_entities_fall_back_to_sequence():
    out = composition.describe([chain("A", HBA), chain("B", HBB)], [{"name": "X", "chains": ["A"]}])
    assert out["source"] == "sequence" and out["kind"] == "hetero"


def test_stoichiometry_many_entities():
    assert composition.stoichiometry([1] * 30) == "30 entità"
    assert composition.stoichiometry([1, 3]) == "A3B"


def test_compnd_entities_from_pdb_header():
    text = (
        "COMPND    MOL_ID: 1;\n"
        "COMPND   2 MOLECULE: HEMOGLOBIN ALPHA\n"
        "COMPND   3 CHAIN;\n"
        "COMPND   4 CHAIN: A, C;\n"
        "COMPND   5 MOL_ID: 2;\n"
        "COMPND   6 MOLECULE: HEMOGLOBIN BETA CHAIN;\n"
        "COMPND   7 CHAIN: B, D;\n"
        "COMPND   8 ENGINEERED: YES\n"
    )
    assert structure.parse_header(text)["entities"] == [
        {"name": "HEMOGLOBIN ALPHA CHAIN", "chains": ["A", "C"]},
        {"name": "HEMOGLOBIN BETA CHAIN", "chains": ["B", "D"]},
    ]
    one = structure.parse_header((DATA / "1UBQ.pdb").read_text())["entities"]
    assert one == [{"name": "UBIQUITIN", "chains": ["A"]}]


def test_entities_from_mmcif(tmp_path):
    cif = tmp_path / "x.cif"
    cif.write_text(
        "data_X\n"
        "loop_\n_entity.id\n_entity.pdbx_description\n1 'Hemoglobin alpha'\n2 'Hemoglobin beta'\n3 water\n"
        "loop_\n_entity_poly.entity_id\n_entity_poly.pdbx_strand_id\n_entity_poly.pdbx_seq_one_letter_code_can\n"
        f"1 A,C {HBA}\n2 B,D {HBB}\n"
    )
    header = structure.parse_cif_header(cif)
    assert header["entities"] == [{"name": "Hemoglobin alpha", "chains": ["A", "C"]},
                                  {"name": "Hemoglobin beta", "chains": ["B", "D"]}]


def test_template_analysis_reports_composition(client, client_with_ubq):
    with open(DATA / "ubq_dimer.pdb", "rb") as fh:
        r = client.post("/api/templates/upload", files={"file": ("UBQDIM.pdb", fh, "chemical/x-pdb")})
    assert r.status_code == 200, r.text
    dimer = client.get(f"/api/templates/{r.json()['id']}").json()["composition"]
    assert dimer["kind"] == "homo" and dimer["stoichiometry"] == "A2" and dimer["source"] == "sequence"
    ubq = client.get("/api/templates/1UBQ").json()["composition"]
    assert ubq["kind"] == "monomer" and ubq["source"] == "file" and ubq["entities"][0]["name"] == "UBIQUITIN"
