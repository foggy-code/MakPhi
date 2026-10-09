"""Acque catalitiche e strutturali: candidate del template, scelta esplicita, copia preparata (acque in fondo alla
catena come HOH), colonne del PIR ('.' dentro BioLoom, 'w' nel file per MODELLER), verifica dell'acqua nel modello e,
con MODELLER, modellazione dell'ubiquitina di lievito su `ubq_zn.pdb` con lo Zn e una delle sue due acque.

`tests/data/ubq_zn.pdb`: Zn²⁺ (ZN A101) a 2,10 Å da NE2 di His68 e da due acque (HOH A201 e A202), entrambe nella
sfera di coordinazione dello Zn (attesa 2,09 + 0,5 Å).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from bioloom.core.errors import AppError
from bioloom.domain import ligands, sequences, structure
from bioloom.domain.sequences import PirEntry
from bioloom.engines import modeller
from tests.conftest import DATA, HUMAN_UB, YEAST_UB, requires_modeller, wait_job

ZN_PDB = DATA / "ubq_zn.pdb"
ZN_AND_WATER = [("A", "101", "ZN"), ("A", "201", "HOH")]


def _model(path: Path):
    return structure.first_model(structure.load_structure(path, path.stem))


# ---------------------------------------------------------------------------------------------- candidate e scelta
def test_water_candidates_suggest_the_waters_on_the_zinc():
    found = ligands.water_candidates(_model(ZN_PDB))
    assert [(w["chain"], w["resnum"]) for w in found["waters"]] == [("A", "201"), ("A", "202")]
    assert found["others"] == 0 and found["criteria"] == {"hbond": 3.5, "structural": 3}
    for w in found["waters"]:
        assert w["class"] == "water" and w["label"] == "acqua" and not w["default"] and w["transferable"]
        assert w["suggested"] and w["why"] == "coordina ZN A101"
        assert w["metals"] == ["ZN A101 (Zn, 2,10 Å)"] and w["bfactor"] == 20.0 and w["occupancy"] == 1.0
    # le acque non sono fra i gruppi eteroatomici
    assert [g["resname"] for g in ligands.candidates(_model(ZN_PDB))] == ["ZN"]


def test_water_candidates_skip_solvent_far_from_the_structure(tmp_path):
    lines = [ln for ln in ZN_PDB.read_text().splitlines() if not ln.startswith("END")]
    lines.append("HETATM  700  O   HOH A 301     120.000   0.000   0.000  1.00 30.00           O")
    path = tmp_path / "SOLV.pdb"
    path.write_text("\n".join(lines) + "\nEND\n", encoding="utf-8")
    found = ligands.water_candidates(_model(path))
    assert [w["resnum"] for w in found["waters"]] == ["201", "202"] and found["others"] == 1
    assert ligands.water_candidates(_model(path), chains=["B"]) == {**found, "waters": [], "others": 0}


def _bridge_pdb(tmp_path: Path, partner: str) -> Path:
    """ubq_zn con un'acqua (HOH A301) a 2,8 Å da NZ della Lys48 (in superficie, lontana dallo Zn) e un gruppo
    `partner` (un solo O) a 2,7 Å dall'acqua, in posizioni libere (nessun altro atomo entro 3,3 Å dall'acqua né entro
    3,6 Å dall'O del gruppo)."""
    import numpy as np

    model = _model(ZN_PDB)
    nz = model["A"][48]["NZ"]
    others = np.array([a.coord for a in model.get_atoms() if a is not nz])
    golden = np.pi * (3.0 - np.sqrt(5.0))  # 200 direzioni ben distribuite (spirale di Fibonacci)
    directions = [np.array([np.cos(golden * k) * np.sqrt(1 - z * z), np.sin(golden * k) * np.sqrt(1 - z * z), z])
                  for k, z in enumerate(np.linspace(1 - 1 / 200, -1 + 1 / 200, 200))]
    w = next(p for d in directions for p in [nz.coord + 2.8 * d] if np.linalg.norm(others - p, axis=1).min() >= 3.3)
    o = next(p for d in directions for p in [w + 2.7 * d]
             if np.linalg.norm(others - p, axis=1).min() >= 3.6 and np.linalg.norm(nz.coord - p) >= 3.6)
    lines = [ln for ln in ZN_PDB.read_text().splitlines() if not ln.startswith("END")]
    lines.append(f"HETATM  700  O   HOH A 301    {w[0]:8.3f}{w[1]:8.3f}{w[2]:8.3f}  1.00 15.00           O")
    lines.append(f"HETATM  701  O1  {partner} A 401    {o[0]:8.3f}{o[1]:8.3f}{o[2]:8.3f}  1.00 15.00           O")
    path = tmp_path / f"{partner}.pdb"
    path.write_text("\n".join(lines) + "\nEND\n", encoding="utf-8")
    return path


def test_bridging_water_is_suggested_only_with_a_real_ligand(tmp_path):
    by_num = {w["resnum"]: w for w in ligands.water_candidates(_model(_bridge_pdb(tmp_path, "LIG")))["waters"]}
    bridge = by_num["301"]
    assert bridge["role"] == "bridge" and bridge["suggested"] and bridge["why"] == "a ponte fra la proteina e LIG"
    assert bridge["partners"] == ["Lys A48 NZ 2,80 Å"] and bridge["groups"] == ["LIG A401 O1 2,70 Å"]
    # le acque sullo Zn restano prima nell'elenco
    assert [w["role"] for w in ligands.water_candidates(_model(_bridge_pdb(tmp_path, "LIG")))["waters"]][:3] == [
        "metal", "metal", "bridge"]
    # con un additivo (glicerolo) non è un'acqua di un sito
    plain = {w["resnum"]: w for w in ligands.water_candidates(_model(_bridge_pdb(tmp_path, "GOL")))["waters"]}["301"]
    assert plain["role"] in ("", "structural") and not plain["suggested"] and plain["groups"]


def test_waters_are_transferred_only_when_chosen():
    model = _model(ZN_PDB)
    assert [ligands.group_key(r)[2] for r in ligands.select(model, ["A"], None)] == ["ZN"]
    chosen = ligands.select(model, ["A"], ZN_AND_WATER)
    assert [ligands.group_key(r) for r in chosen] == [("A", "101", "ZN"), ("A", "201", "HOH")]
    with pytest.raises(AppError, match="HOH A999 non è tra le acque") as err:
        ligands.select(model, ["A"], [("A", "999", "HOH")])
    assert err.value.code == "ligand_not_found" and "disponibili: ZN A101" in str(err.value)


def test_selector_keeps_only_the_chosen_waters(tmp_path):
    st = structure.load_structure(ZN_PDB, "ZN")
    model = structure.first_model(st)
    water = next(r for r in model["A"] if r.id[0] == "W" and r.id[1] == 202)
    structure.write_pdb(st, tmp_path / "w.pdb", keep_water={("A", water.id)})
    text = (tmp_path / "w.pdb").read_text()
    assert "HOH A 202" in text and "HOH A 201" not in text and " ZN " not in text


# ---------------------------------------------------------------------------------------------- copia preparata
def test_prepared_template_has_the_chosen_water_after_the_ligands(tmp_path):
    spec = modeller.TemplateSpec("ZN", ["A"], hetatm=True, ligands=list(ZN_AND_WATER))
    info = modeller.prepare_template(ZN_PDB, spec, tmp_path)
    assert info["ligands"] == 1 and info["waters"] == 1
    zn, water = info["ligand_groups"]
    assert zn["resname"] == "ZN" and water["resname"] == "HOH" and water["class"] == "water"
    assert water["coordinates"] == [{"chain": "A", "resnum": "101", "resname": "ZN", "atom": "ZN", "protein": False,
                                     "element": "ZN", "distance": 2.1}]
    assert info["ligands_excluded"] == []  # le acque non scelte non sono "gruppi esclusi"
    het = [ln for ln in (tmp_path / info["file"]).read_text().splitlines() if ln.startswith("HETATM")]
    assert [ln[17:26] for ln in het] == [" ZN A 101", "HOH A 201"]
    # senza acque scelte: nessuna acqua nella copia, nessun campo "waters"
    info = modeller.prepare_template(ZN_PDB, modeller.TemplateSpec("ZN", ["A"], hetatm=True), tmp_path / "plain")
    assert "waters" not in info and "HOH" not in (tmp_path / "plain" / info["file"]).read_text()


def test_water_named_wat_is_written_as_hoh(tmp_path):
    source = tmp_path / "WAT.pdb"
    source.write_text(ZN_PDB.read_text().replace("HOH A 201", "WAT A 201"), encoding="utf-8")
    spec = modeller.TemplateSpec("WAT", ["A"], hetatm=True, ligands=[("A", "201", "WAT")])
    info = modeller.prepare_template(source, spec, tmp_path / "p")
    assert info["ligand_groups"][0]["resname"] == "HOH"
    assert "HOH A 201" in (tmp_path / "p" / info["file"]).read_text()


# ---------------------------------------------------------------------------------------------- PIR
def _entries(template_seq: str, target_seq: str) -> list[PirEntry]:
    return [PirEntry("ZN_A", "structureX", ["FIRST", "A", "LAST", "A", "", "", "", ""], template_seq),
            PirEntry("yeast", "sequence", ["", "", "", "", "", "", "0.00", "0.00"], target_seq)]


def test_water_columns_are_dots_in_bioloom_and_w_for_modeller(tmp_path):
    spec = modeller.TemplateSpec("ZN", ["A"], hetatm=True, ligands=list(ZN_AND_WATER))
    info = {**modeller.prepare_template(ZN_PDB, spec, tmp_path), "code": "ZN_A"}
    entries, notes = ligands.pir_with_ligands(_entries(info["sequence"], YEAST_UB), [info])
    assert notes == [] and [e.sequence[-2:] for e in entries] == ["..", ".."]
    assert ligands.water_columns(entries, [info]) == [len(HUMAN_UB) + 1]
    text = ligands.modeller_pir(entries, [info])
    rows = [ln for ln in text.splitlines() if ln.endswith("*")]
    assert [r[-3:] for r in rows] == [".w*", ".w*"]
    # il PIR di BioLoom resta valido e con i '.'; senza acque nessun file per MODELLER
    assert sequences.validate_pir(sequences.format_pir(entries))["valid"]
    plain = {**info, "ligand_groups": info["ligand_groups"][:1]}
    entries, _ = ligands.pir_with_ligands(_entries(info["sequence"], YEAST_UB), [plain])
    assert ligands.modeller_pir(entries, [plain]) is None


def test_the_same_water_of_two_templates_shares_the_column():
    def tmpl(code: str, resnum: str) -> dict:
        water = {"resname": "HOH", "chain": "A", "resnum": resnum, "class": "water", "metals": [], "links": [],
                 "hbonds": [{"chain": "A", "resnum": "68", "resname": "HIS", "atom": "NE2", "protein": True}],
                 "contacts": []}
        return {"code": code, "chains": ["A"], "last": "76", "_chain_residues": {"A": 76},
                "_chain_ids": {"A": [str(n) for n in range(1, 77)]}, "_hetero": [], "ligand_groups": [water]}
    templates = [tmpl("WA_A", "201"), tmpl("WB_A", "305")]
    entries = [PirEntry("WA_A", "structureX", ["FIRST", "A", "LAST", "A"], HUMAN_UB),
               PirEntry("WB_A", "structureX", ["FIRST", "A", "LAST", "A"], HUMAN_UB),
               PirEntry("yeast", "sequence", [], YEAST_UB)]
    out, notes = ligands.pir_with_ligands(entries, templates)
    assert [e.sequence[-1] for e in out] == [".", ".", "."] and "un solo HOH nel modello" in notes[0]
    assert [r[-2:] for r in ligands.modeller_pir(out, templates).splitlines() if r.endswith("*")] == ["w*"] * 3


def test_model_script_reads_waters_from_the_modeller_alignment():
    opts = modeller.parse_options({"hetatm": True})
    script = modeller.build_model_script(["T_A"], "t", opts, hetatm=True, water=True,
                                         alnfile=modeller.WATER_ALIGNMENT_FILE)
    assert "env.io.water = True" in script and "alnfile='alignment_modeller.ali'" in script
    plain = modeller.build_model_script(["T_A"], "t", opts, hetatm=True)
    assert "env.io.water" not in plain and "alnfile='alignment.ali'" in plain


# ---------------------------------------------------------------------------------------------- verifica
def _site(tmp_path: Path, model_path: Path | None = None) -> tuple:
    """Template preparato con lo Zn e l'acqua A201; il "modello" è la copia preparata (o `model_path`)."""
    spec = modeller.TemplateSpec("ZN", ["A"], hetatm=True, ligands=list(ZN_AND_WATER))
    info = {**modeller.prepare_template(ZN_PDB, spec, tmp_path / "t"), "code": "ZN_A"}
    tmodel = _model(tmp_path / "t" / info["file"])
    model = _model(model_path or tmp_path / "t" / info["file"])
    entries, _ = ligands.pir_with_ligands(_entries(info["sequence"], HUMAN_UB), [info])
    contexts, notes = ligands.template_contexts(entries, [info], {"ZN_A": tmodel}, model)
    return info, model, contexts, notes


def test_transferred_water_is_verified_with_the_template_site(tmp_path):
    info, model, contexts, notes = _site(tmp_path)
    assert notes == [] and set(contexts) == {("A", "101"), ("A", "201")}
    assert contexts[("A", "201")]["group"]["class"] == "water"
    result = ligands.evaluate(model, contexts=contexts, waters=True)
    zn, water = result["groups"]
    assert water["class"] == "water" and water["label"] == "acqua" and water["status"] == "buono"
    assert water["water"]["metals"] == [{"label": "Zn A101", "distance": 2.1}]
    assert water["template"] == {"code": "ZN_A", "group": "HOH A201"} and water["clashes"] == []
    assert water["summary"].startswith("Coordina Zn A101 (2,10 Å)")
    # lo Zn conta l'acqua trasferita fra i donatori: resta da aggiungere solo quella non trasferita
    metal = zn["metals"][0]
    assert metal["coordination_number"] == 2 and metal["template_site"]["waters"] == 1
    assert "Numero di coordinazione 2 (3 con 1 acqua del template, non trasferita), tipico per Zn 4–6." in zn["reasons"]
    # l'analisi di qualità (senza `waters`) non verifica le acque
    assert [g["resname"] for g in ligands.evaluate(model)["groups"]] == ["ZN"]


def test_water_that_lost_its_metal_is_flagged(tmp_path):
    info, _model_, _c, _n = _site(tmp_path)
    moved = tmp_path / "moved.pdb"
    lines = []
    for line in (tmp_path / "t" / info["file"]).read_text().splitlines():
        if line.startswith("HETATM") and line[17:20] == "HOH":
            line = line[:30] + f"{40.0:8.3f}{40.0:8.3f}{40.0:8.3f}" + line[54:]  # lontana da tutto
        lines.append(line)
    moved.write_text("\n".join(lines) + "\n", encoding="utf-8")
    _info, model, contexts, _notes = _site(tmp_path, moved)
    water = next(g for g in ligands.evaluate(model, contexts=contexts, waters=True)["groups"] if g["class"] == "water")
    assert water["status"] == "da controllare"
    assert "Nel template l'acqua coordinava Zn A101; nel modello non coordina nessun metallo." in water["reasons"]
    assert any(r.startswith("Nessun legame H entro 3,5 Å e nessun metallo coordinato") for r in water["reasons"])


# ---------------------------------------------------------------------------------------------- API
def test_template_analysis_lists_the_waters(client):
    with open(ZN_PDB, "rb") as fh:
        assert client.post("/api/templates/upload", files={"file": ("ZNW.pdb", fh, "chemical/x-pdb")}).status_code == 200
    data = client.get("/api/templates/ZNW").json()
    assert [w["resnum"] for w in data["waters"]["waters"]] == ["201", "202"]
    assert [g["resname"] for g in data["ligands"]] == ["ZN"]


def test_examples_with_hetatm_templates(client):
    examples = {e["name"]: e for e in client.get("/api/examples").json()}
    assert examples["myoglobin"]["template"]["id"] == "1A6K"
    ca = examples["carbonic_anhydrase"]
    assert ca["accession"] == "P00921" and (ca["template"]["id"], ca["template"]["chains"]) == ("3KS3", ["A"])
    assert "HOH A402" in ca["template"]["note"]


# ---------------------------------------------------------------------------------------------- MODELLER
@pytest.mark.modeller
@requires_modeller
def test_yeast_model_keeps_the_zinc_and_its_catalytic_water(client):
    with open(ZN_PDB, "rb") as fh:
        assert client.post("/api/templates/upload", files={"file": ("ZNH.pdb", fh, "chemical/x-pdb")}).status_code == 200
    body = {"target": {"name": "yeast", "sequence": YEAST_UB}, "templates": [{"id": "ZNH", "chains": ["A"]}],
            "options": {"models": 1, "refinement": "very_fast", "hetatm": True,
                        "ligands": [{"template": "ZNH", "chain": c, "resnum": n, "resname": r}
                                    for c, n, r in ZN_AND_WATER]},
            "quality": "best"}
    job = client.post("/api/model", json=body).json()
    done = wait_job(client, job["id"], timeout=600)
    assert done["status"] == "completed", done.get("error")
    res = done["result"]
    assert [(g["resname"], g["class"]) for g in res["ligands"]["transferred"]] == [("ZN", "metal"), ("HOH", "water")]
    assert res["templates"][0]["waters"] == 1 and res["templates"][0]["ligands"] == 1
    assert not any("Il modello migliore contiene" in w for w in res["warnings"])
    # PIR di BioLoom con i '.', file per MODELLER con la 'w'
    template_row, target_row = [e.sequence for e in sequences.parse_pir(res["alignment"])]
    assert template_row.endswith("..") and target_row.endswith("..")
    water_pir = client.get(f"/api/jobs/{job['id']}/files/{modeller.WATER_ALIGNMENT_FILE}").text
    assert water_pir.count(".w*") == 2
    best = next(m for m in res["models"] if m["best"])
    pdb = client.get(f"/api/jobs/{job['id']}/files/{best['file']}").text
    het = [ln for ln in pdb.splitlines() if ln.startswith("HETATM")]
    assert [ln[17:20] for ln in het] == [" ZN", "HOH"]
    assert "REMARK 999 BIOLOOM LIGANDS 2 ZN:A101 HOH:A201" in pdb
    # l'acqua resta sullo Zn, come nel template
    zn = next(ln for ln in het if ln[17:20] == " ZN")
    hoh = next(ln for ln in het if ln[17:20] == "HOH")
    distance = sum((float(zn[30 + 8 * k:38 + 8 * k]) - float(hoh[30 + 8 * k:38 + 8 * k])) ** 2 for k in range(3)) ** 0.5
    assert abs(distance - 2.1) < 0.4
    water = next(g for g in res["ligands"]["verification"]["groups"] if g["class"] == "water")
    assert water["template"] == {"code": "ZNH_A", "group": "HOH A201"} and water["water"]["metals"]
    assert water["status"] in ("buono", "da controllare")
