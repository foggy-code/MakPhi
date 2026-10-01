"""Assemblaggi biologici (domain/assembly.py, F5.3): espressioni degli operatori, lettura da mmCIF (pdbx_struct_*) e
da PDB (REMARK 350), coordinate generate, nomi delle catene, limiti, libreria dei template e rotte."""

from __future__ import annotations

import threading
from pathlib import Path

import numpy as np
import pytest

from bioloom.core.errors import AppError
from bioloom.domain import assembly, geometry, interfaces, structure
from tests.conftest import DATA

UBQ_ATOMS = [ln for ln in (DATA / "1UBQ.pdb").read_text(encoding="utf-8").splitlines() if ln.startswith("ATOM")]


def _load(path: Path, name: str = "s"):
    return structure.first_model(structure.load_structure(path, name))


def _coords(chain) -> np.ndarray:
    return np.array([a.coord for r in chain if r.id[0] == " " for a in r], dtype=float)


def _c2_operator() -> tuple[np.ndarray, np.ndarray]:
    """Rotazione e traslazione che portano la catena A di ubq_dimer.pdb sulla catena B (x' = R·x + t)."""
    dimer = _load(DATA / "ubq_dimer.pdb", "dimer")
    rot, trans = geometry.kabsch(_coords(dimer["A"]), _coords(dimer["B"]))
    return rot, trans


def _oper_rows(ops: list[tuple[str, str, np.ndarray, np.ndarray]]) -> str:
    lines = ["loop_", "_pdbx_struct_oper_list.id", "_pdbx_struct_oper_list.type", "_pdbx_struct_oper_list.name"]
    lines += [f"_pdbx_struct_oper_list.matrix[{i}][{j}]" for i in (1, 2, 3) for j in (1, 2, 3)]
    lines += [f"_pdbx_struct_oper_list.vector[{i}]" for i in (1, 2, 3)]
    for oid, kind, rot, vec in ops:
        values = " ".join(f"{rot[i][j]:.6f}" for i in range(3) for j in range(3))
        lines.append(f"{oid} '{kind}' {oid}_555 {values} {vec[0]:.6f} {vec[1]:.6f} {vec[2]:.6f}")
    return "\n".join(lines)


def make_mmcif(ops: list, assemblies: list[tuple[str, str, str, int]], gens: list[tuple[str, str, str]],
               auth_chain: str = "A") -> str:
    """mmCIF sintetico: 1UBQ (asym A, catena dell'autore `auth_chain`) più due acque (asym B, stessa catena
    dell'autore, come nei file del PDB), con gli assemblaggi e gli operatori indicati."""
    lines = ["data_TEST", "#", "_struct.title 'SYNTHETIC UBIQUITIN C2 ASSEMBLY (BIOLOOM TESTS)'",
             "_exptl.method 'X-RAY DIFFRACTION'", "#", "loop_"]
    lines += [f"_atom_site.{k}" for k in (
        "group_PDB", "id", "type_symbol", "label_atom_id", "label_alt_id", "label_comp_id", "label_asym_id",
        "label_entity_id", "label_seq_id", "pdbx_PDB_ins_code", "Cartn_x", "Cartn_y", "Cartn_z", "occupancy",
        "B_iso_or_equiv", "pdbx_formal_charge", "auth_seq_id", "auth_comp_id", "auth_asym_id", "auth_atom_id",
        "pdbx_PDB_model_num")]
    serial = 0
    for ln in UBQ_ATOMS:
        serial += 1
        name, resn, num = ln[12:16].strip(), ln[17:20], int(ln[22:26])
        x, y, z = ln[30:38].strip(), ln[38:46].strip(), ln[46:54].strip()
        lines.append(f"ATOM {serial} {ln[76:78].strip()} {name} . {resn} A 1 {num} ? {x} {y} {z} "
                     f"{ln[54:60].strip()} {ln[60:66].strip()} ? {num} {resn} {auth_chain} {name} 1")
    for k, (x, y, z) in enumerate(((30.0, 30.0, 10.0), (31.0, 28.0, 12.0)), start=1):
        serial += 1
        lines.append(f"HETATM {serial} O O . HOH B 2 . ? {x:.3f} {y:.3f} {z:.3f} 1.00 20.00 ? {200 + k} HOH "
                     f"{auth_chain} O 1")
    lines += ["#", "loop_", "_pdbx_struct_assembly.id", "_pdbx_struct_assembly.details",
              "_pdbx_struct_assembly.method_details", "_pdbx_struct_assembly.oligomeric_details",
              "_pdbx_struct_assembly.oligomeric_count"]
    lines += [f"{aid} {details} ? {state} {count}" for aid, details, state, count in assemblies]
    lines += ["#", "loop_", "_pdbx_struct_assembly_gen.assembly_id", "_pdbx_struct_assembly_gen.oper_expression",
              "_pdbx_struct_assembly_gen.asym_id_list"]
    lines += [f"{aid} '{expr}' {asym}" for aid, expr, asym in gens]
    lines += ["#", _oper_rows(ops), "#"]
    return "\n".join(lines) + "\n"


IDENTITY = ("1", "identity operation", np.eye(3), np.zeros(3))


def _c2_cif(tmp_path: Path, **kwargs) -> Path:
    rot, trans = _c2_operator()
    shift = ("3", "translation", np.eye(3), np.array([10.0, 0.0, 0.0]))
    text = make_mmcif([IDENTITY, ("2", "point symmetry operation", rot, trans), shift],
                      [("1", "author_defined_assembly", "dimeric", 2),
                       ("2", "software_defined_assembly", "monomeric", 1),
                       ("3", "author_defined_assembly", "dimeric", 2)],
                      [("1", "1,2", "A,B"), ("2", "1", "A"), ("3", "(1,2)(3)", "A")], **kwargs)
    path = tmp_path / "C2UBQ.cif"
    path.write_text(text, encoding="utf-8")
    return path


# ---------------------------------------------------------------------------------------------- espressioni
@pytest.mark.parametrize("expr, expected", [
    ("1", [("1",)]),
    ("1,2,5", [("1",), ("2",), ("5",)]),
    ("1-4", [("1",), ("2",), ("3",), ("4",)]),
    ("(1-3)", [("1",), ("2",), ("3",)]),
    ("1-2,7", [("1",), ("2",), ("7",)]),
    ("(1,2)(3,4)", [("1", "3"), ("1", "4"), ("2", "3"), ("2", "4")]),
    ("(X0)(1-2)", [("X0", "1"), ("X0", "2")]),
    (" ( 1 , 2 ) ", [("1",), ("2",)]),
])
def test_operator_expressions(expr, expected):
    assert assembly.parse_oper_expression(expr) == expected


@pytest.mark.parametrize("expr", ["", "5-3", "(1,2", "1)(2", "()", "(1)x(2)"])
def test_invalid_operator_expressions(expr):
    with pytest.raises(AppError):
        assembly.parse_oper_expression(expr)


def test_operator_expression_limit():
    with pytest.raises(AppError, match="massimo") as err:
        assembly.parse_oper_expression("(1-60)(61-120)")  # 3600 combinazioni, come i capsidi
    assert err.value.code == "assembly_too_large"


def test_products_apply_right_to_left():
    """(1)(2) = prima 2, poi 1: rotazione di 90° attorno a z dopo una traslazione di 1 Å lungo x."""
    rot90 = assembly.Operator("1", np.array([[0.0, -1.0, 0.0], [1.0, 0.0, 0.0], [0.0, 0.0, 1.0]]), np.zeros(3))
    shift = assembly.Operator("2", np.eye(3), np.array([1.0, 0.0, 0.0]))
    rot, vec = assembly.compose([rot90, shift])
    assert np.allclose(rot @ np.zeros(3) + vec, [0.0, 1.0, 0.0])
    rot, vec = assembly.compose([shift, rot90])  # prima la rotazione (l'origine resta), poi la traslazione
    assert np.allclose(rot @ np.zeros(3) + vec, [1.0, 0.0, 0.0])
    assert assembly.Operator("1", np.eye(3), np.zeros(3)).is_identity() and not shift.is_identity()


# ---------------------------------------------------------------------------------------------- mmCIF
def test_mmcif_assemblies_are_read(tmp_path):
    info = assembly.read(_c2_cif(tmp_path))
    assert info.format == "mmcif" and info.labels
    assert [a.id for a in info.assemblies] == ["1", "2", "3"]
    first = info.assemblies[0]
    assert (first.details, first.oligomeric_details, first.oligomeric_count) == ("author_defined_assembly",
                                                                                  "dimeric", 2)
    assert first.generators[0].chains == ["A", "B"] and first.generators[0].expression == "1,2"
    assert set(info.operators) == {"1", "2", "3"} and info.operators["1"].is_identity()
    # label_asym_id dei residui: la proteina è l'asym A, le acque (stessa catena dell'autore) l'asym B
    assert info.residue_labels[("A", (" ", 1, " "))] == "A"
    assert info.residue_labels[("A", ("W", 201, " "))] == "B"


def test_mmcif_c2_assembly_reproduces_the_synthetic_dimer(tmp_path):
    """Identità + rotazione C2 (quella che porta la catena A di ubq_dimer.pdb sulla B) → due catene: A uguale a 1UBQ,
    B con le coordinate della catena B del dimero (operatore scritto con 6 decimali, coordinate con 3)."""
    path = _c2_cif(tmp_path)
    info = assembly.read(path)
    model = _load(path, "c2")
    built, copies = assembly.build(model, info, assembly.find(info, "1"))
    assert [(c["chain"], c["source"], c["operator"]) for c in copies] == [("A", "A", "1"), ("B", "A", "2")]
    chains = {c.id: c for c in structure.first_model(built)}
    dimer = _load(DATA / "ubq_dimer.pdb", "dimer")
    assert np.abs(_coords(chains["A"]) - _coords(dimer["A"])).max() < 1e-3
    assert np.abs(_coords(chains["B"]) - _coords(dimer["B"])).max() < 0.01
    # le acque (asym B nel generatore) seguono la loro catena, anche nella copia ruotata
    waters = [r for r in chains["B"] if r.id[0] == "W"]
    assert len(waters) == 2
    rot, trans = _c2_operator()
    assert np.allclose(waters[0]["O"].coord, rot @ np.array([30.0, 30.0, 10.0]) + trans, atol=0.01)


def test_mmcif_generator_takes_only_the_listed_asym(tmp_path):
    path = _c2_cif(tmp_path)
    info = assembly.read(path)
    built, copies = assembly.build(_load(path), info, assembly.find(info, "2"))
    residues = list(structure.first_model(built)["A"])
    assert len(copies) == 1 and len(residues) == 76 and all(r.id[0] == " " for r in residues)  # acque escluse


def test_mmcif_operator_products(tmp_path):
    """(1,2)(3): prima la traslazione di 10 Å (operatore 3), poi l'identità o la rotazione C2."""
    path = _c2_cif(tmp_path)
    info = assembly.read(path)
    model = _load(path)
    built, copies = assembly.build(model, info, assembly.find(info, "3"))
    assert [c["operator"] for c in copies] == ["1×3", "2×3"]
    chains = list(structure.first_model(built))
    ref = _coords(model["A"])
    rot, trans = _c2_operator()
    assert np.allclose(_coords(chains[0]), ref + [10.0, 0.0, 0.0], atol=1e-3)
    assert np.allclose(_coords(chains[1]), (ref + [10.0, 0.0, 0.0]) @ rot.T + trans, atol=0.01)


def test_chain_names_of_the_copies():
    """La prima copia di ogni catena tiene il nome; le altre il primo carattere libero non tenuto da una prima copia."""
    rot, trans = _c2_operator()
    text = "\n".join([
        "REMARK 350 BIOMOLECULE: 1",
        "REMARK 350 AUTHOR DETERMINED BIOLOGICAL UNIT: TRIMERIC",
        "REMARK 350 APPLY THE FOLLOWING TO CHAINS: A",
        *(f"REMARK 350   BIOMT{i + 1}   1  {' '.join(f'{v:9.6f}' for v in row)}        0.00000"
          for i, row in enumerate(np.eye(3))),
        *(f"REMARK 350   BIOMT{i + 1}   2  {' '.join(f'{v:9.6f}' for v in rot[i])}   {trans[i]:12.5f}"
          for i in range(3)),
        "REMARK 350 APPLY THE FOLLOWING TO CHAINS: B",
        *(f"REMARK 350   BIOMT{i + 1}   1  {' '.join(f'{v:9.6f}' for v in row)}        0.00000"
          for i, row in enumerate(np.eye(3))),
    ])
    info = assembly.from_pdb_text(text)
    dimer = _load(DATA / "ubq_dimer.pdb", "dimer")
    copies = assembly.plan(dimer, info, info.assemblies[0])
    assert [(c["chain"], c["source"], c["operator"]) for c in copies] == [("A", "A", "1"), ("C", "A", "2"),
                                                                           ("B", "B", "1")]
    # i numeri BIOMT dei due gruppi sono operatori distinti (il 1 di B è l'identità, il 2 di A la rotazione)
    assert len(info.operators) == 3


def test_multi_character_chain_is_renamed(tmp_path):
    path = _c2_cif(tmp_path, auth_chain="AA")
    info = assembly.read(path)
    copies = assembly.plan(_load(path), info, assembly.find(info, "1"))
    assert [(c["chain"], c["source"]) for c in copies] == [("A", "AA"), ("B", "AA")]


# ---------------------------------------------------------------------------------------------- PDB
def test_pdb_remark_350_of_1ubq():
    described = assembly.describe(DATA / "1UBQ.pdb")
    assert described["format"] == "pdb" and len(described["assemblies"]) == 1
    a = described["assemblies"][0]
    assert a["oligomeric_details"] == "monomeric" and a["details"] == "author_defined_assembly"
    assert a["chain_count"] == 1 and a["protein_chains"] == 1 and a["identity_only"] and a["buildable"]
    assert a["chains"] == [{"chain": "A", "source": "A", "operator": "1", "identity": True, "protein": True}]


def test_pdb_without_remark_350_has_no_assembly():
    described = assembly.describe(DATA / "ubq_dimer.pdb")
    assert described["assemblies"] == [] and "REMARK 350" in described["note"]


def test_pdb_assembly_with_continued_chain_list():
    text = "\n".join([
        "REMARK 350 BIOMOLECULE: 1",
        "REMARK 350 SOFTWARE DETERMINED QUATERNARY STRUCTURE: DIMERIC",
        "REMARK 350 SOFTWARE USED: PISA",
        "REMARK 350 APPLY THE FOLLOWING TO CHAINS: A,",
        "REMARK 350                    AND CHAINS: B",
        "REMARK 350   BIOMT1   1  1.000000  0.000000  0.000000        0.00000",
        "REMARK 350   BIOMT2   1  0.000000  1.000000  0.000000        0.00000",
        "REMARK 350   BIOMT3   1  0.000000  0.000000  1.000000        0.00000",
    ])
    info = assembly.from_pdb_text(text)
    a = info.assemblies[0]
    assert a.generators[0].chains == ["A", "B"] and a.method == "PISA"
    assert a.details == "software_defined_assembly" and a.oligomeric_details == "dimeric"
    with pytest.raises(AppError, match="BIOMT 1 incompleto"):
        assembly.from_pdb_text("\n".join(text.splitlines()[:-1]))


def test_assembly_limits():
    """Oltre 62 catene (i caratteri di un identificativo PDB) l'assemblaggio non si costruisce, con un errore chiaro."""
    ops = "\n".join(f"REMARK 350   BIOMT{i + 1} {n:3d}  " + " ".join(f"{v:9.6f}" for v in row) + "   0.00000"
                    for n in range(1, 64) for i, row in enumerate(np.eye(3)))
    info = assembly.from_pdb_text("REMARK 350 BIOMOLECULE: 1\nREMARK 350 APPLY THE FOLLOWING TO CHAINS: A\n" + ops)
    model = _load(DATA / "1UBQ.pdb", "ubq")
    with pytest.raises(AppError, match="al massimo 62") as err:
        assembly.plan(model, info, info.assemblies[0])
    assert err.value.code == "assembly_too_large"
    summary = assembly.summary(model, info, info.assemblies[0])
    assert not summary["buildable"] and "62" in summary["note"]
    with pytest.raises(AppError, match="non presente") as err:
        assembly.find(info, "7")
    assert err.value.status == 404


def test_generated_pdb_text_and_interface(tmp_path):
    """Il PDB dell'assemblaggio ha l'intestazione d'origine, le righe REMARK 999 con l'origine delle catene e la stessa
    interfaccia del dimero sintetico."""
    path = _c2_cif(tmp_path)
    info = assembly.read(path)
    header = structure.parse_cif_header(path)
    text, chains = assembly.pdb_text(_load(path), info, assembly.find(info, "1"), source_id="C2UBQ", header=header)
    assert text.startswith("HEADER    BIOLOGICAL ASSEMBLY 1 OF C2UBQ")
    assert "EXPDTA    X-RAY DIFFRACTION" in text and "TITLE     SYNTHETIC UBIQUITIN C2 ASSEMBLY" in text
    assert "REMARK 999 BIOLOOM ASSEMBLY CHAIN B FROM A OPERATOR 2" in text
    assert chains == [{"chain": "A", "source": "A", "operator": "1", "identity": True, "protein": True},
                      {"chain": "B", "source": "A", "operator": "2", "identity": False, "protein": True}]
    out = tmp_path / "asm.pdb"
    out.write_text(text, encoding="utf-8")
    iface = interfaces.analyze(_load(out, "asm"))
    ref = interfaces.analyze(_load(DATA / "ubq_dimer.pdb", "dimer"))
    assert iface["pairs"][0]["area"] == pytest.approx(ref["pairs"][0]["area"], abs=1.0)


# ---------------------------------------------------------------------------------------------- libreria e rotte
def _upload(client, path: Path, name: str) -> str:
    with open(path, "rb") as fh:
        r = client.post("/api/templates/upload", files={"file": (name, fh, "chemical/x-cif")})
    assert r.status_code == 200, r.text
    return r.json()["id"]


def test_library_lists_and_imports_assemblies(client, tmp_path):
    tid = _upload(client, _c2_cif(tmp_path), "C2UBQ.cif")
    listed = client.get(f"/api/templates/{tid}/assemblies").json()
    assert listed["template"] == "C2UBQ" and listed["format"] == "mmcif"
    assert [(a["id"], a["chain_count"], a["oligomeric_details"]) for a in listed["assemblies"]] == [
        ("1", 2, "dimeric"), ("2", 1, "monomeric"), ("3", 2, "dimeric")]
    r = client.post(f"/api/templates/{tid}/assemblies/1")
    assert r.status_code == 200, r.text
    imported = r.json()
    assert imported["id"] == "C2UBQ_ASM1" and not imported["cached"] and imported["chains"] == ["A", "B"]
    assert imported["source"] == "assemblaggio 1 di C2UBQ" and imported["method"] == "X-RAY DIFFRACTION"
    assert imported["assembly"]["chains"][1] == {"chain": "B", "source": "A", "operator": "2", "identity": False,
                                                 "protein": True}
    # assemblaggio 3: anche la prima copia (catena A) è trasformata (prima l'operatore 3, la traslazione)
    third = next(a for a in listed["assemblies"] if a["id"] == "3")
    assert [(c["chain"], c["operator"], c["identity"]) for c in third["chains"]] == [("A", "1×3", False),
                                                                                    ("B", "2×3", False)]
    assert not imported["predicted"]
    detail = client.get("/api/templates/C2UBQ_ASM1").json()
    assert [c["residues"] for c in detail["chains"]] == [76, 76]
    item = next(t for t in client.get("/api/templates").json() if t["id"] == "C2UBQ_ASM1")
    assert item["assembly"]["source_id"] == "C2UBQ"
    # di nuovo: stesso file d'origine, si riusa; l'assemblaggio non ha assemblaggi propri
    again = client.post(f"/api/templates/{tid}/assemblies/1").json()
    assert again["cached"] and again["id"] == "C2UBQ_ASM1"
    nested = client.get("/api/templates/C2UBQ_ASM1/assemblies").json()
    assert nested["assemblies"] == [] and "già l'assemblaggio 1 di C2UBQ" in nested["note"]
    assert client.post("/api/templates/C2UBQ_ASM1/assemblies/1").status_code == 400


def test_assembly_rebuilt_when_the_source_changes(client, tmp_path):
    tid = _upload(client, _c2_cif(tmp_path), "C2UBQ.cif")
    first = client.post(f"/api/templates/{tid}/assemblies/3").json()
    # stesso id, file diverso (la sola acqua spostata): l'assemblaggio si rigenera
    text = _c2_cif(tmp_path).read_text(encoding="utf-8").replace("30.000 30.000 10.000", "29.000 30.000 10.000")
    changed = tmp_path / "changed" / "C2UBQ.cif"
    changed.parent.mkdir()
    changed.write_text(text, encoding="utf-8")
    _upload(client, changed, "C2UBQ.cif")
    second = client.post(f"/api/templates/{tid}/assemblies/3").json()
    assert not second["cached"] and second["assembly"]["source_sha256"] != first["assembly"]["source_sha256"]


def test_assembly_route_errors(client, tmp_path):
    tid = _upload(client, _c2_cif(tmp_path), "C2UBQ.cif")
    r = client.post(f"/api/templates/{tid}/assemblies/9")
    assert r.status_code == 404 and r.json()["error"]["code"] == "assembly_not_found"
    assert client.post(f"/api/templates/{tid}/assemblies/x!y").status_code == 422  # id non valido
    assert client.post(f"/api/templates/{tid}/assemblies/..%2Fx").status_code in (404, 405, 422)
    assert client.get("/api/templates/NOPE/assemblies").status_code == 404
    # un template dell'utente con il nome dell'assemblaggio non si sovrascrive
    _upload(client, DATA / "ubq_dimer.pdb", "C2UBQ_ASM2.pdb")
    r = client.post(f"/api/templates/{tid}/assemblies/2")
    assert r.status_code == 409 and "non è l'assemblaggio 2 di C2UBQ" in r.json()["error"]["message"]
    assert client.get("/api/templates/C2UBQ_ASM2").json()["meta"]["source"] == "caricamento utente"


def test_concurrent_imports_give_one_template(client, tmp_path):
    tid = _upload(client, _c2_cif(tmp_path), "C2UBQ.cif")
    library = client.app.state.core.library
    results, errors = [], []

    def run() -> None:
        try:
            results.append(library.import_assembly(tid, "1"))
        except Exception as exc:  # pragma: no cover - riportato sotto
            errors.append(exc)

    threads = [threading.Thread(target=run) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors and len(results) == 4
    assert sum(not r["cached"] for r in results) == 1  # uno costruisce, gli altri riusano
    assert [t["id"] for t in library.list()].count("C2UBQ_ASM1") == 1
    model = structure.first_model(structure.load_structure(library.path("C2UBQ_ASM1"), "x"))
    assert [c.id for c in model] == ["A", "B"]
