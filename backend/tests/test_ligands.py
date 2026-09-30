"""Ligandi, cofattori e metalli (F5.4): classificazione, trasferimento selettivo, colonne '.' del PIR, verifica dei
contatti, degli scontri e della geometria di coordinazione, confronto con il sito del template, rapporto di qualità,
report HTML e, con MODELLER, modellazione dell'ubiquitina di lievito su `ubq_zn.pdb` con il solo Zn trasferito.

Valori di riferimento su `tests/data/ubq_zn.pdb` (Zn²⁺ a 2,10 Å da NE2 di His68 e da due acque): numero di
coordinazione 3, angoli donatore–Zn–donatore 67,2°, 81,1° e 90,0°, geometria più vicina piramide a base quadrata con
2 posizioni vacanti (gRMSD 14,1°): esito "da controllare".
"""

from __future__ import annotations

import math
import threading
from pathlib import Path

import numpy as np
import pytest

from bioloom.core.errors import AppError
from bioloom.core.jobs import JobCancelled
from bioloom.domain import ligands, report, sequences, structure
from bioloom.domain.sequences import PirEntry
from bioloom.engines import modeller, quality
from bioloom.services.compute import PreparedTemplates, _apply_ligand_options
from tests.conftest import DATA, HUMAN_UB, YEAST_UB, requires_modeller, wait_job

ZN_PDB = DATA / "ubq_zn.pdb"


def _model(path: Path):
    return structure.first_model(structure.load_structure(path, path.stem))


def _het(serial: int, name: str, resn: str, resi: int, xyz, elem: str, chain: str = "A") -> str:
    atom = f" {name:<3}" if len(elem) == 1 and len(name) < 4 else f"{name:<4}"
    return (f"HETATM{serial:5d} {atom} {resn:>3} {chain}{resi:4d}    {xyz[0]:8.3f}{xyz[1]:8.3f}{xyz[2]:8.3f}"
            f"  1.00 20.00          {elem:>2}")


def _with_groups(tmp_path: Path, groups: list[tuple[str, int, list[tuple[str, tuple, str]]]],
                 base: Path = ZN_PDB, name: str = "LIG.pdb") -> Path:
    """Copia di `base` (senza END) con i gruppi HETATM aggiunti: [(nome, numero, [(atomo, xyz, elemento)])]."""
    lines = [ln for ln in base.read_text().splitlines() if not ln.startswith("END")]
    serial = 900
    for resn, resi, atoms in groups:
        for atom, xyz, elem in atoms:
            lines.append(_het(serial, atom, resn, resi, xyz, elem))
            serial += 1
    path = tmp_path / name
    path.write_text("\n".join(lines) + "\nEND\n", encoding="utf-8")
    return path


def _far(k: int) -> tuple:
    """Posizioni lontane dalla proteina (x > 100 Å), una per gruppo."""
    return (100.0 + 10.0 * k, 0.0, 0.0)


def _offset(base: tuple, d: tuple) -> tuple:
    return tuple(a + b for a, b in zip(base, d))


def _classification_pdb(tmp_path: Path) -> Path:
    groups = [
        ("GOL", 301, [("C1", _offset(_far(0), (0, 0, 0)), "C"), ("O1", _offset(_far(0), (1.4, 0, 0)), "O"),
                      ("C2", _offset(_far(0), (0, 1.5, 0)), "C")]),
        ("SO4", 302, [("S", _far(1), "S"), ("O1", _offset(_far(1), (1.5, 0, 0)), "O"),
                      ("O2", _offset(_far(1), (-1.5, 0, 0)), "O")]),
        ("HEM", 401, [("FE", _far(2), "FE"), ("NA", _offset(_far(2), (2.0, 0, 0)), "N"),
                      ("NB", _offset(_far(2), (0, 2.0, 0)), "N"), ("NC", _offset(_far(2), (-2.0, 0, 0)), "N"),
                      ("ND", _offset(_far(2), (0, -2.0, 0)), "N")]),
        ("NA", 501, [("NA", _far(3), "NA")]),
        ("MSE", 601, [("N", _far(4), "N"), ("CA", _offset(_far(4), (1.46, 0, 0)), "C"),
                      ("C", _offset(_far(4), (2.0, 1.4, 0)), "C"), ("SE", _offset(_far(4), (1.5, -1.5, 1.0)), "SE")]),
        ("LIG", 701, [("C1", _far(5), "C"), ("N1", _offset(_far(5), (1.4, 0, 0)), "N")]),
    ]
    return _with_groups(tmp_path, groups, name="CLS.pdb")


# ---------------------------------------------------------------------------------------------- classificazione
def test_classification_of_groups(tmp_path):
    model = _model(_classification_pdb(tmp_path))
    found = {g["resname"]: g for g in ligands.candidates(model)}
    assert list(found) == ["ZN", "GOL", "SO4", "HEM", "NA", "MSE", "LIG"]  # HOH esclusa, ordine del file
    assert found["ZN"]["class"] == "metal" and found["ZN"]["element"] == "ZN" and found["ZN"]["default"]
    assert found["ZN"]["label"] == "metallo" and found["ZN"]["transferable"]
    for name in ("GOL", "SO4"):
        assert found[name]["class"] == "additive" and not found[name]["default"]
        assert "cristallizzazione" in found[name]["reason"] and found[name]["additive"] is True
    assert found["HEM"]["class"] == "cofactor" and found["HEM"]["default"] and "eme" in found["HEM"]["description"]
    assert found["NA"]["class"] == "metal" and not found["NA"]["default"] and "monovalente" in found["NA"]["reason"]
    assert found["MSE"]["class"] == "modified" and not found["MSE"]["transferable"]
    assert found["LIG"]["class"] == "ligand" and found["LIG"]["default"]
    # scelta predefinita e scelta esplicita, sempre nell'ordine del file
    assert [ligands.group_key(r)[2] for r in ligands.select(model, ["A"], None)] == ["ZN", "HEM", "LIG"]
    chosen = ligands.select(model, ["A"], [("A", "701", "LIG"), ("A", "301", "GOL")])
    assert [ligands.group_key(r)[2] for r in chosen] == ["GOL", "LIG"]
    with pytest.raises(AppError, match="HEM A999 non è tra gli eteroatomi") as err:
        ligands.select(model, ["A"], [("A", "999", "HEM")])
    assert err.value.code == "ligand_not_found"
    with pytest.raises(AppError, match="residuo modificato") as err:
        ligands.select(model, ["A"], [("A", "601", "MSE")])
    assert err.value.code == "ligand_not_transferable"
    assert ligands.select(model, ["B"], None) == []  # nessun gruppo nelle catene non scelte


def test_template_detail_lists_classified_candidates(client, tmp_path):
    path = _classification_pdb(tmp_path)
    with open(path, "rb") as fh:
        r = client.post("/api/templates/upload", files={"file": ("CLS.pdb", fh, "chemical/x-pdb")})
    assert r.status_code == 200
    detail = client.get("/api/templates/CLS").json()
    by_name = {g["resname"]: g for g in detail["ligands"]}
    assert by_name["ZN"]["class"] == "metal" and by_name["SO4"]["class"] == "additive"
    assert by_name["HEM"]["class"] == "cofactor" and by_name["MSE"]["transferable"] is False
    assert "HOH" not in by_name


# ---------------------------------------------------------------------------------------------- geometria
def _tetra() -> np.ndarray:
    return np.array([(1, 1, 1), (1, -1, -1), (-1, 1, -1), (-1, -1, 1)], dtype=float)


def test_geometry_of_ideal_and_incomplete_sites():
    g = ligands.geometry(2.0 * _tetra() / math.sqrt(3))
    assert (g["key"], g["grmsd"], g["vacancy"]) == ("tetrahedral", 0.0, 0.0)
    octa = np.array([(1, 0, 0), (-1, 0, 0), (0, 1, 0), (0, -1, 0), (0, 0, 1), (0, 0, -1)], dtype=float)
    assert ligands.geometry(octa)["key"] == "octahedral"
    assert ligands.geometry(octa[:4])["key"] == "square_planar"
    tbp = np.array([(0, 0, 1), (0, 0, -1), (1, 0, 0), (-0.5, math.sqrt(3) / 2, 0), (-0.5, -math.sqrt(3) / 2, 0)])
    assert ligands.geometry(tbp)["key"] == "trigonal_bipyramidal"
    assert ligands.geometry(octa[[0, 2, 3, 4, 5]])["key"] == "square_pyramidal"
    # tre donatori di un tetraedro: tetraedrica con una posizione vacante (25%), non trigonale planare
    three = ligands.geometry(_tetra()[:3])
    assert (three["key"], three["grmsd"], three["vacancy"]) == ("tetrahedral", 0.0, 0.25)
    # tetraedro deformato di 10°: gRMSD sotto la soglia di CheckMyMetal (13,5°)
    rot = np.array([[1, 0, 0], [0, math.cos(0.17), -math.sin(0.17)], [0, math.sin(0.17), math.cos(0.17)]])
    bent = _tetra().copy()
    bent[0] = rot @ bent[0]
    g = ligands.geometry(bent)
    assert g["key"] == "tetrahedral" and 0 < g["grmsd"] < ligands.GRMSD_BORDERLINE
    assert ligands.geometry(octa[:1]) is None and ligands.geometry(np.ones((8, 3))) is None


def test_zinc_site_of_ubq_zn_reference_values():
    """Zn con His68 e due acque a 2,10 Å: coordinazione 3, geometria distorta, esito motivato."""
    result = ligands.evaluate(_model(ZN_PDB))
    assert result["counts"] == {"buono": 0, "da controllare": 1, "problematico": 0}
    assert result["template_compared"] is False and result["status"] == "da controllare"
    (group,) = result["groups"]
    assert (group["resname"], group["chain"], group["resnum"], group["class"]) == ("ZN", "A", "101", "metal")
    assert group["contacts"] == [{"chain": "A", "resnum": "68", "resname": "HIS", "distance": 2.1}]
    assert group["clashes"] == [] and group["links"] == []
    (zn,) = group["metals"]
    assert zn["coordination_number"] == 3 and zn["cn_range"] == [4, 6]
    donors = {(d["resname"], d["resnum"], d["atom"]): d for d in zn["donors"]}
    assert set(donors) == {("HIS", "68", "NE2"), ("HOH", "201", "O"), ("HOH", "202", "O")}
    his = donors[("HIS", "68", "NE2")]
    assert (his["category"], his["distance"], his["expected"], his["status"]) == ("N_his", 2.1, 2.03, "nella norma")
    water = donors[("HOH", "201", "O")]
    assert (water["category"], water["expected"], water["water"], water["status"]) == ("O_water", 2.09, True,
                                                                                         "nella norma")
    vectors = np.array([np.array(d["coord"]) - np.array(zn["coord"]) for d in zn["donors"]])
    angles = sorted(ligands._angles(vectors)[np.triu_indices(3, 1)])
    assert angles == pytest.approx([67.2, 81.1, 90.0], abs=0.1)
    assert zn["geometry"] == {"key": "square_pyramidal", "name": "piramide a base quadrata", "positions": 5,
                              "grmsd": 14.1, "vacancy": 0.4}
    assert group["reasons"] == [
        "Numero di coordinazione 3, tipico per Zn 4–6.",
        "Geometria piramide a base quadrata distorta: gRMSD 14,1° (oltre 13,5°).",
        "Geometria piramide a base quadrata con 2 posizioni vacanti su 5 (40%, oltre il 25%).",
    ]
    assert group["summary"] == " ".join(group["reasons"])


def _moved(tmp_path: Path, changes: dict[tuple[str, int], tuple]) -> Path:
    """Copia di ubq_zn.pdb con alcuni atomi HETATM spostati: {(nome del residuo, numero): nuove coordinate}."""
    out = []
    for line in ZN_PDB.read_text().splitlines():
        if line.startswith("HETATM") and (line[17:20].strip(), int(line[22:26])) in changes:
            x, y, z = changes[(line[17:20].strip(), int(line[22:26]))]
            line = f"{line[:30]}{x:8.3f}{y:8.3f}{z:8.3f}{line[54:]}"
        out.append(line)
    path = tmp_path / "moved.pdb"
    path.write_text("\n".join(out) + "\n", encoding="utf-8")
    return path


def test_clash_detected_when_zinc_is_moved_inside_the_protein(tmp_path):
    model = _model(ZN_PDB)
    cb = model["A"][43]["CB"].coord  # Leu43, nel nucleo idrofobico
    path = _moved(tmp_path, {("ZN", 101): tuple(float(v) for v in cb + np.array([0.9, 0.0, 0.0]))})
    (group,) = ligands.evaluate(_model(path))["groups"]
    assert group["status"] == "problematico"
    assert group["clashes"] and group["clashes"][0]["distance"] < 1.0
    assert any(c["partner"] == "Leu A43 CB" for c in group["clashes"])
    assert group["reasons"][0].startswith("Scontro: ZN di ZN a ")
    assert any(c["resnum"] == "43" for c in group["contacts"])


def test_distance_statuses_long_and_too_short(tmp_path):
    model = _model(ZN_PDB)
    zn = model["A"][("H_ZN", 101, " ")]["ZN"].coord
    path = _moved(tmp_path, {("HOH", 201): tuple(float(v) for v in zn + np.array([0.0, 0.0, 2.45])),
                             ("HOH", 202): tuple(float(v) for v in zn + np.array([0.0, 1.45, 0.0]))})
    (group,) = ligands.evaluate(_model(path))["groups"]
    donors = {d["resnum"]: d for d in group["metals"][0]["donors"]}
    assert (donors["201"]["status"], donors["201"]["deviation"]) == ("lunga", 0.36)
    assert donors["202"]["status"] == "troppo corta" and group["status"] == "problematico"
    assert "Distanza lunga: Zn–O (acqua) di HOH A201 O 2,45 Å, attesa 2,09 ± 0,25 Å." in group["reasons"]
    assert any("troppo vicino" in r for r in group["reasons"])


def test_structure_without_ligands_has_no_ligand_report(ubq_path):
    assert ligands.evaluate(_model(ubq_path)) is None  # solo acque


# ---------------------------------------------------------------------------------------------- preparazione
def test_prepare_template_writes_only_the_chosen_groups(tmp_path):
    source = _with_groups(tmp_path, [
        ("SO4", 301, [("S", _far(0), "S"), ("O1", _offset(_far(0), (1.5, 0, 0)), "O")]),
        ("GOL", 302, [("C1", _far(1), "C"), ("O1", _offset(_far(1), (1.4, 0, 0)), "O")]),
    ])
    spec = modeller.TemplateSpec("LIG", ["A"], hetatm=True)
    info = modeller.prepare_template(source, spec, tmp_path / "default")
    text = (tmp_path / "default" / info["file"]).read_text()
    assert info["ligands"] == 1 and [g["resname"] for g in info["ligand_groups"]] == ["ZN"]
    assert " ZN " in text and "SO4" not in text and "GOL" not in text and "HOH" not in text
    excluded = {g["resname"]: g for g in info["ligands_excluded"]}
    assert set(excluded) == {"SO4", "GOL"} and "cristallizzazione" in excluded["GOL"]["reason"]
    # sito del template dal file originale, acque comprese (la copia non le ha)
    (zn,) = info["ligand_groups"][0]["metals"]
    assert sorted((d["resname"], d["atom"], d["water"]) for d in zn["donors"]) == [
        ("HIS", "NE2", False), ("HOH", "O", True), ("HOH", "O", True)]
    # il gruppo sta dopo l'ultimo residuo della catena, prima del TER
    records = [ln for ln in text.splitlines() if ln.startswith(("ATOM", "HETATM", "TER"))]
    assert records[-2].startswith("HETATM") and records[-2][17:20] == " ZN" and records[-1].startswith("TER")
    assert info["_chain_residues"] == {"A": 76} and ("A", "301") in info["_hetero"]
    # scelta esplicita: solo il glicerolo
    only = modeller.prepare_template(source, modeller.TemplateSpec("LIG", ["A"], True, None, [("A", "302", "GOL")]),
                                     tmp_path / "gol")
    assert [g["resname"] for g in only["ligand_groups"]] == ["GOL"]
    assert {g["resname"] for g in only["ligands_excluded"]} == {"ZN", "SO4"}
    # senza ligandi: nessun eteroatomo, nessuna descrizione
    none = modeller.prepare_template(source, modeller.TemplateSpec("LIG", ["A"]), tmp_path / "none")
    assert none["ligands"] == 0 and "ligand_groups" not in none
    assert "HETATM" not in (tmp_path / "none" / none["file"]).read_text()


class _Check:
    def __init__(self) -> None:
        self.cancelled = threading.Event()

    def __call__(self) -> None:
        if self.cancelled.is_set():
            raise JobCancelled()


def test_prepared_templates_key_changes_with_ligand_choice(tmp_path):
    source = tmp_path / "ZN.pdb"
    source.write_bytes(ZN_PDB.read_bytes())
    cache = PreparedTemplates(tmp_path / "cache")
    T = modeller.TemplateSpec
    cases = [
        T("ZN", ["A"], hetatm=True),                                # scelta predefinita
        T("ZN", ["A"], hetatm=True, ligands=[("A", "101", "ZN")]),   # scelta esplicita
        T("ZN", ["A"], hetatm=True, ligands=[]),                     # nessun gruppo
        T("ZN", ["A"], hetatm=True, ligands=[("A", "101", "ZN")]),   # come la seconda: riuso
        T("ZN", ["A"], hetatm=False, ligands=[("A", "101", "ZN")]),  # ligandi spenti: la scelta non conta
        T("ZN", ["A"], hetatm=False),
    ]
    keys = [PreparedTemplates.key(source, spec) for spec in cases]
    assert len({keys[0], keys[1], keys[2]}) == 3 and keys[1] == keys[3] and keys[4] == keys[5]
    results = [cache.get(source, spec, tmp_path / f"i{n}", _Check()) for n, spec in enumerate(cases)]
    assert [reused for _info, reused in results] == [False, False, False, True, False, True]
    assert [info["ligands"] for info, _r in results] == [1, 1, 0, 1, 0, 0]


# ---------------------------------------------------------------------------------------------- opzioni
def test_ligand_options_are_validated():
    opts = modeller.parse_options({"ligands": [{"template": "1abc", "chain": "A", "resnum": "101", "resname": "ZN"},
                                               {"template": "1abc", "chain": "A", "resnum": "101", "resname": "ZN"},
                                               {"chain": "B", "resnum": "-5A", "resname": "HEM"}]})
    assert opts.ligands == [{"template": "1abc", "chain": "A", "resnum": "101", "resname": "ZN"},
                            {"template": "", "chain": "B", "resnum": "-5A", "resname": "HEM"}]
    assert modeller.parse_options({}).ligands is None
    for bad in ("ZN", [1], [{"chain": "A", "resnum": "x", "resname": "ZN"}],
                [{"chain": "A'); import os", "resnum": "1", "resname": "ZN"}],
                [{"chain": "A", "resnum": "1", "resname": "TOOLONG"}], [{"chain": "A", "resnum": "1"}] * 101):
        with pytest.raises(AppError):
            modeller.parse_options({"ligands": bad})


def test_ligand_options_choose_templates_and_groups():
    specs = [modeller.TemplateSpec("1ABC", ["A"]), modeller.TemplateSpec("2XYZ", ["B"], hetatm=True)]
    _apply_ligand_options(specs, modeller.parse_options(
        {"ligands": [{"template": "1abc", "chain": "A", "resnum": "101", "resname": "ZN"}]}))
    assert (specs[0].hetatm, specs[0].ligands) == (True, [("A", "101", "ZN")])
    assert (specs[1].hetatm, specs[1].ligands) == (True, [])  # attivo senza gruppi scelti: nessun gruppo
    specs = [modeller.TemplateSpec("1ABC", ["A"])]
    _apply_ligand_options(specs, modeller.parse_options({"hetatm": True}))
    assert (specs[0].hetatm, specs[0].ligands) == (True, None)  # scelta predefinita
    two = [modeller.TemplateSpec("1ABC", ["A"]), modeller.TemplateSpec("2XYZ", ["A"])]
    with pytest.raises(AppError, match="indica da quale template"):
        _apply_ligand_options(two, modeller.parse_options({"ligands": [{"chain": "A", "resnum": "1",
                                                                         "resname": "ZN"}]}))
    with pytest.raises(AppError, match="non è tra quelli scelti"):
        _apply_ligand_options(two, modeller.parse_options({"ligands": [{"template": "3QQQ", "chain": "A",
                                                                         "resnum": "1", "resname": "ZN"}]}))


def test_api_rejects_invalid_ligand_options_before_queueing(client):
    bad = {"ligands": [{"chain": "A", "resnum": "x", "resname": "ZN"}]}
    r = client.post("/api/model", json={"target": {"name": "t", "sequence": YEAST_UB},
                                        "templates": [{"id": "1UBQ", "chains": ["A"]}], "options": bad})
    assert r.status_code == 400 and r.json()["error"]["code"] == "invalid_ligand"
    # pipeline con la ricerca automatica: i template non si conoscono, vale solo la scelta predefinita (hetatm)
    chosen = {"ligands": [{"template": "1UBQ", "chain": "A", "resnum": "101", "resname": "ZN"}]}
    r = client.post("/api/pipeline", json={"fasta": f">a\n{YEAST_UB}\n", "options": chosen})
    assert r.status_code == 400 and r.json()["error"]["code"] == "invalid_ligand"
    assert "hetatm" in r.json()["error"]["message"]


def test_model_script_reads_hetatm_only_when_groups_are_transferred():
    opts = modeller.parse_options({"hetatm": True})
    assert "env.io.hetatm = True" in modeller.build_model_script(["T_A"], "t", opts)
    assert "env.io.hetatm = True" not in modeller.build_model_script(["T_A"], "t", opts, hetatm=False)
    assert "env.io.hetatm = True" in modeller.build_model_script(["T_A"], "t", modeller.parse_options({}),
                                                                 hetatm=True)


# ---------------------------------------------------------------------------------------------- PIR
def _entries(template_seq: str, target_seq: str, fields=("1", "A", "+76", "A")) -> list[PirEntry]:
    return [PirEntry("ZN_A", "structureX", [*fields, "", "", "", ""], template_seq),
            PirEntry("yeast", "sequence", ["", "", "", "", "", "", "0.00", "0.00"], target_seq)]


def _zn_template(**extra) -> dict:
    return {"code": "ZN_A", "chains": ["A"], "last": "76", "_chain_residues": {"A": 76},
            "_hetero": [("A", "101")], "ligand_groups": [{"resname": "ZN", "chain": "A", "resnum": "101"}], **extra}


def test_pir_gets_ligand_columns_for_template_and_target():
    entries, notes = ligands.pir_with_ligands(_entries(HUMAN_UB, YEAST_UB), [_zn_template()])
    assert notes == []
    template, target = entries
    assert template.sequence == HUMAN_UB + "." and target.sequence == YEAST_UB + "."
    assert template.fields[:4] == ["1", "A", "+77", "A"]  # "+N" ricontato con il gruppo
    # fine esplicita: sull'ultimo gruppo trasferito
    entries, _ = ligands.pir_with_ligands(_entries(HUMAN_UB, YEAST_UB, ("1", "A", "76", "A")), [_zn_template()])
    assert entries[0].fields[:4] == ["1", "A", "101", "A"]
    # PIR di una modellazione precedente (già con i '.'): stesse colonne, nessun doppione
    again, notes = ligands.pir_with_ligands(entries, [_zn_template()])
    assert [e.sequence for e in again] == [HUMAN_UB + ".", YEAST_UB + "."] and notes == []
    text = sequences.format_pir(again)
    assert sequences.validate_pir(text)["valid"] and "101:A" in text


def test_pir_ligands_removed_when_not_chosen():
    user = _entries(HUMAN_UB + ".", YEAST_UB + ".", ("1", "A", "101", "A"))
    entries, notes = ligands.pir_with_ligands(user, [_zn_template(ligand_groups=[])])
    assert [e.sequence for e in entries] == [HUMAN_UB, YEAST_UB]
    assert entries[0].fields[:4] == ["1", "A", "76", "A"]  # la fine indicava lo Zn tolto
    assert notes == ["Il PIR indicava 1 ligando ('.') per ZN_A: nessuno è trasferito nelle opzioni di modellazione "
                     "(Ligandi, cofattori e metalli)."]
    # un '.' del bersaglio davanti a un gap del template sparisce con la sua colonna
    odd = _entries(HUMAN_UB + "-", YEAST_UB + ".")
    entries, _ = ligands.pir_with_ligands(odd, [_zn_template(ligand_groups=[])])
    assert [e.sequence for e in entries] == [HUMAN_UB, YEAST_UB]


def test_pir_ligand_columns_with_chains_and_several_templates():
    dimer = {"code": "D_AB", "chains": ["A", "B"], "last": "76", "_chain_residues": {"A": 76, "B": 76},
             "_hetero": [("A", "101")], "ligand_groups": [{"resname": "ZN", "chain": "A", "resnum": "101"}]}
    other = {"code": "O_AB", "chains": ["A", "B"], "last": "76", "_chain_residues": {"A": 76, "B": 76},
             "_hetero": [], "ligand_groups": []}
    entries = [PirEntry("D_AB", "structureX", ["1", "A", "76", "B"], f"{HUMAN_UB}/{HUMAN_UB}"),
               PirEntry("O_AB", "structureX", ["1", "A", "76", "B"], f"{HUMAN_UB}/{HUMAN_UB}"),
               PirEntry("t", "sequence", [], f"{YEAST_UB}/{YEAST_UB}")]
    out, _ = ligands.pir_with_ligands(entries, [dimer, other])
    assert [e.sequence for e in out] == [f"{HUMAN_UB}./{HUMAN_UB}", f"{HUMAN_UB}-/{HUMAN_UB}",
                                         f"{YEAST_UB}./{YEAST_UB}"]
    assert out[0].fields[:4] == ["1", "A", "76", "B"]  # l'ultima catena non ha gruppi: fine invariata
    # '/' in colonne diverse fra template e bersaglio
    shifted = [PirEntry("D_AB", "structureX", ["1", "A", "76", "B"], f"{HUMAN_UB}-/{HUMAN_UB}"),
               PirEntry("t", "sequence", [], f"{YEAST_UB}/-{YEAST_UB}")]
    with pytest.raises(AppError, match="interruzioni di catena"):
        ligands.pir_with_ligands(shifted, [dimer])
    # voce del template che non arriva all'ultimo residuo della catena con il ligando
    partial = _entries(HUMAN_UB[:70] + "------", YEAST_UB, ("1", "A", "70", "A"))
    with pytest.raises(AppError, match=r"catena A di ZN_A .* tutti i 76 residui della catena \(ne ha 70\)"):
        ligands.pir_with_ligands(partial, [_zn_template()])


# ---------------------------------------------------------------------------------------------- sito del template
def _site_setup(tmp_path: Path, model_path: Path = None, target_seq: str = HUMAN_UB) -> tuple:
    """Template ubq_zn preparato con lo Zn; il "modello" è la copia preparata (senza acque, come un modello)."""
    info = modeller.prepare_template(ZN_PDB, modeller.TemplateSpec("ZN", ["A"], hetatm=True), tmp_path / "t")
    tmodel = _model(tmp_path / "t" / info["file"])
    model = _model(model_path or tmp_path / "t" / info["file"])
    entries, _ = ligands.pir_with_ligands(_entries(info["sequence"], target_seq, ("FIRST", "A", "LAST", "A")),
                                          [{**info, "code": "ZN_A"}])
    return info, tmodel, model, entries


def test_template_site_is_compared_through_the_alignment(tmp_path):
    info, tmodel, model, entries = _site_setup(tmp_path)
    contexts, notes = ligands.template_contexts(entries, [info], {"ZN_A": tmodel}, model)
    assert notes == [] and set(contexts) == {("A", "101")}
    assert contexts[("A", "101")]["map"][("A", "68")] == ("A", "68", "HIS")
    result = ligands.evaluate(model, contexts=contexts)
    (group,) = result["groups"]
    assert result["template_compared"] and group["template"] == {"code": "ZN_A", "group": "ZN A101"}
    (zn,) = group["metals"]
    assert zn["coordination_number"] == 1 and zn["geometry"] is None
    site = zn["template_site"]
    assert site == {"donors": [{"template": "His A68", "atom": "NE2", "target": "His A68", "status": "conservato"}],
                    "protein_donors": 1, "conserved": 1, "waters": 2, "new": []}
    # le due acque del template non vengono trasferite: la coordinazione efficace è 3, ancora sotto 4
    assert group["status"] == "da controllare"
    assert group["reasons"] == ["Numero di coordinazione 1 (3 con 2 acque del template, non trasferite), tipico per "
                                "Zn 4–6."]


def test_template_site_lost_when_the_donor_is_substituted(tmp_path):
    # His68 → Leu nel "modello": via gli atomi dell'anello, nome del residuo cambiato
    info = modeller.prepare_template(ZN_PDB, modeller.TemplateSpec("ZN", ["A"], hetatm=True), tmp_path / "p")
    lines = []
    for line in (tmp_path / "p" / info["file"]).read_text().splitlines():
        if line.startswith("ATOM") and line[17:20] == "HIS" and int(line[22:26]) == 68:
            if line[12:16].strip() in ("ND1", "CD2", "CE1", "NE2"):
                continue
            line = line[:17] + "LEU" + line[20:]
        lines.append(line)
    mutant = tmp_path / "mutant.pdb"
    mutant.write_text("\n".join(lines) + "\n", encoding="utf-8")
    target = HUMAN_UB[:67] + "L" + HUMAN_UB[68:]
    info, tmodel, model, entries = _site_setup(tmp_path, mutant, target)
    contexts, _ = ligands.template_contexts(entries, [info], {"ZN_A": tmodel}, model)
    (group,) = ligands.evaluate(model, contexts=contexts)["groups"]
    site = group["metals"][0]["template_site"]
    assert site["donors"][0]["status"] == "sostituito" and site["conserved"] == 0
    assert group["status"] == "problematico"
    assert ("Il donatore del template His A68 (NE2) è sostituito da Leu A68, che non può coordinare Zn."
            in group["reasons"])
    assert "Nessun atomo donatore (N, O, S) nella sfera di coordinazione: Zn non è coordinato." in group["reasons"]


def test_template_site_donor_without_aligned_residue(tmp_path):
    # bersaglio senza His68 (gap nell'allineamento, residuo assente dal modello): donatore "non allineato"
    info = modeller.prepare_template(ZN_PDB, modeller.TemplateSpec("ZN", ["A"], hetatm=True), tmp_path / "p")
    lines = [ln for ln in (tmp_path / "p" / info["file"]).read_text().splitlines()
             if not (ln.startswith("ATOM") and ln[17:20] == "HIS" and int(ln[22:26]) == 68)]
    deleted = tmp_path / "deleted.pdb"
    deleted.write_text("\n".join(lines) + "\n", encoding="utf-8")
    info, tmodel, model, entries = _site_setup(tmp_path, deleted, HUMAN_UB[:67] + "-" + HUMAN_UB[68:])
    contexts, notes = ligands.template_contexts(entries, [info], {"ZN_A": tmodel}, model)
    assert notes == [] and contexts[("A", "101")]["map"][("A", "68")] is None
    (group,) = ligands.evaluate(model, contexts=contexts)["groups"]
    assert group["metals"][0]["template_site"]["donors"][0]["status"] == "non allineato"
    assert "Il donatore del template His A68 (NE2) non ha un residuo allineato nel bersaglio." in group["reasons"]
    assert group["status"] == "problematico"
    # allineamento che non corrisponde al modello (il gap c'è, ma il modello ha ancora His68): nessun confronto
    full = _model(tmp_path / "t" / info["file"])
    contexts, notes = ligands.template_contexts(entries, [info], {"ZN_A": tmodel}, full)
    assert contexts == {} and "il modello non corrisponde al bersaglio" in notes[0]


# ---------------------------------------------------------------------------------------------- rapporto e report
def test_quality_report_has_ligands_only_with_heteroatoms(ubq_path):
    rep = quality.analyze(ZN_PDB, with_dope=False)
    assert rep["ligands"]["groups"][0]["resname"] == "ZN" and rep["ligands"]["status"] == "da controllare"
    assert "ligands" not in quality.analyze(ubq_path, with_dope=False)  # solo acque
    # il voto resta quello di Ramachandran e clash
    from bioloom.domain import grading
    assert rep["grade"] == grading.grade(rep["ramachandran"]["summary"]["outlier_pct"], rep["clashes"]["clashscore"])


def test_report_has_ligand_section_only_with_ligands(ubq_path):
    base = {"kind": "quality", "title": "Qualità", "job_id": "j1", "generated_at": "2026-09-30T10:00:00+00:00",
            "tools": [("BioLoom", "4")], "target": {"name": "ubq", "sequence": HUMAN_UB}}
    rep = quality.analyze(ZN_PDB, with_dope=False)
    rep["ligands"]["groups"][0]["description"] = "<script>x</script>"
    html = report.render({**base, "quality": rep})
    assert 'id="ligandi"' in html and "Ligandi e metalli" in html and "ZN A101" in html
    assert "da controllare" in html and "His A68 NE2 2,10 Å" in html and "piramide a base quadrata" in html
    assert "<script>x" not in html and "&lt;script&gt;x" in html
    assert "Harding M.M. (2006)" in html and "CheckMyMetal" in html
    plain = report.render({**base, "quality": quality.analyze(ubq_path, with_dope=False)})
    assert 'id="ligandi"' not in plain and "Harding" not in plain


# ---------------------------------------------------------------------------------------------- MODELLER
@pytest.mark.modeller
@requires_modeller
def test_yeast_model_on_ubq_zn_transfers_only_the_zinc(client, tmp_path):
    source = _with_groups(tmp_path, [("SO4", 301, [("S", _far(0), "S"), ("O1", _offset(_far(0), (1.5, 0, 0)), "O")])],
                          name="ZNS.pdb")
    with open(source, "rb") as fh:
        r = client.post("/api/templates/upload", files={"file": ("ZNS.pdb", fh, "chemical/x-pdb")})
    assert r.status_code == 200
    body = {"target": {"name": "yeast", "sequence": YEAST_UB}, "templates": [{"id": "ZNS", "chains": ["A"]}],
            "options": {"models": 1, "refinement": "very_fast", "hetatm": True,
                        "ligands": [{"template": "ZNS", "chain": "A", "resnum": "101", "resname": "ZN"}]},
            "quality": "best"}
    job = client.post("/api/model", json=body).json()
    done = wait_job(client, job["id"], timeout=600)
    assert done["status"] == "completed", done.get("error")
    res = done["result"]
    assert res["ligands"]["transferred"] == [{"template": "ZNS_A", "resname": "ZN", "chain": "A", "resnum": "101",
                                             "class": "metal", "label": "metallo"}]
    assert [g["resname"] for g in res["templates"][0]["ligand_groups"]] == ["ZN"]
    assert {g["resname"] for g in res["templates"][0]["ligands_excluded"]} == {"SO4"}
    # PIR: un solo '.' per template e bersaglio, nella stessa colonna, in fondo alla catena
    template_row, target_row = [e.sequence for e in sequences.parse_pir(res["alignment"])]
    assert template_row.count(".") == target_row.count(".") == 1 and template_row.endswith(".")
    assert target_row.endswith(".") and res["target"]["sequence"] == YEAST_UB
    best = next(m for m in res["models"] if m["best"])
    pdb = client.get(f"/api/jobs/{job['id']}/files/{best['file']}").text
    hetatm = [ln for ln in pdb.splitlines() if ln.startswith("HETATM")]
    assert len(hetatm) == 1 and hetatm[0][17:20] == " ZN" and "SO4" not in pdb
    assert "REMARK 999 BIOLOOM LIGANDS 1 ZN:A101" in pdb
    # verifica nel rapporto di qualità: His68 coordina lo Zn come nel template
    rep = client.get(f"/api/jobs/{job['id']}/quality/{best['file']}").json()
    (group,) = rep["ligands"]["groups"]
    assert group["template"] == {"code": "ZNS_A", "group": "ZN A101"}
    (zn,) = group["metals"]
    his = next(d for d in zn["donors"] if d["resname"] == "HIS")
    assert his["resnum"] == "68" and his["atom"] == "NE2" and abs(his["distance"] - 2.1) < 0.3
    assert zn["template_site"]["conserved"] == 1 and zn["template_site"]["waters"] == 2
    assert group["status"] == "da controllare" and best["ligands"]["groups"] == 1
    assert res["ligands"]["verification"]["groups"][0]["resname"] == "ZN"
    # report HTML con la sezione dei ligandi
    html = client.get(f"/api/jobs/{job['id']}/report/{best['file']}").text
    assert 'id="ligandi"' in html and "ZN A101 da ZNS_A" in html

    # stesso PIR (con il '.') ma ligandi spenti: il '.' si toglie e il modello non ha lo Zn
    again = client.post("/api/model", json={**body, "target": None, "alignment": res["alignment"],
                                             "options": {"models": 1, "refinement": "very_fast"}}).json()
    done = wait_job(client, again["id"], timeout=600)
    assert done["status"] == "completed", done.get("error")
    assert any("nessuno è trasferito" in w for w in done["result"]["warnings"])
    best = next(m for m in done["result"]["models"] if m["best"])
    assert "HETATM" not in client.get(f"/api/jobs/{again['id']}/files/{best['file']}").text
    assert "ligands" not in done["result"]
