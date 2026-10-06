"""Interazioni non covalenti e ponti disolfuro (domain/interactions.py, F7).

Valori di riferimento su 1UBQ verificati sul file (coordinate lette qui a parte) e confrontati con il criterio
elettrostatico indipendente del DSSP integrato (Kabsch & Sander 1983); criteri geometrici su strutture sintetiche
costruite atomo per atomo.
"""

from __future__ import annotations

import math

import numpy as np
import pytest

from bioloom.domain import interactions, secondary, structure

from .conftest import DATA

HELIX = (23, 34)  # HELIX H1 di 1UBQ
STRANDS = [(10, 17), (1, 7), (64, 72), (40, 45), (48, 50)]  # SHEET di 1UBQ


@pytest.fixture(scope="module")
def ubq():
    return structure.first_model(structure.load_structure(DATA / "1UBQ.pdb", "1UBQ"))


@pytest.fixture(scope="module")
def ubq_result(ubq):
    return interactions.analyze(ubq)


def _strand(resnum: int):
    return next((k for k, (a, b) in enumerate(STRANDS) if a <= resnum <= b), None)


def _pdb(atoms: list[tuple]) -> str:
    """Righe ATOM da (nome, residuo, catena, numero, (x, y, z), elemento)."""
    lines = []
    for serial, (name, resname, chain, resnum, xyz, element) in enumerate(atoms, start=1):
        label = f" {name:<3}" if len(name) < 4 else name
        lines.append(f"ATOM  {serial:5d} {label} {resname:>3} {chain}{resnum:4d}    "
                     f"{xyz[0]:8.3f}{xyz[1]:8.3f}{xyz[2]:8.3f}  1.00 20.00          {element:>2}")
    return "\n".join(lines + ["END"]) + "\n"


def _model(tmp_path, atoms: list[tuple], name: str = "synthetic"):
    path = tmp_path / f"{name}.pdb"
    path.write_text(_pdb(atoms), encoding="utf-8")
    return structure.first_model(structure.load_structure(path, name))


def _ring(centre, normal_axis: str = "z") -> dict:
    """Esagono di un anello fenilico (raggio 1,39 Å) nel piano perpendicolare a `normal_axis`."""
    names = ("CG", "CD1", "CE1", "CZ", "CE2", "CD2")
    out = {}
    for k, name in enumerate(names):
        a = math.radians(60 * k)
        u, v = 1.39 * math.cos(a), 1.39 * math.sin(a)
        offset = {"z": (u, v, 0.0), "x": (0.0, u, v), "y": (u, 0.0, v)}[normal_axis]
        out[name] = tuple(c + o for c, o in zip(centre, offset))
    return out


def _phe(chain: str, resnum: int, centre, normal_axis: str = "z") -> list[tuple]:
    ring = _ring(centre, normal_axis)
    cg = np.array(ring["CG"])
    outward = cg - np.array(centre)
    outward = outward / np.linalg.norm(outward)
    cb = cg + 1.50 * outward
    ca = cb + 1.53 * outward
    atoms = [("N", "PHE", chain, resnum, tuple(ca + [0.0, 1.2, 0.6]), "N"),
             ("CA", "PHE", chain, resnum, tuple(ca), "C"), ("CB", "PHE", chain, resnum, tuple(cb), "C")]
    atoms += [(name, "PHE", chain, resnum, xyz, "C") for name, xyz in ring.items()]
    return atoms


# ------------------------------------------------------------------------------------------ 1UBQ
def test_ubq_helix_hbonds_ideal_count(ubq_result):
    """Elica α 23–34 (12 residui): n − 4 = 8 legami N–H(i+4)…O=C(i) in un'elica ideale; tutti trovati."""
    bb = [h for h in ubq_result["hbonds"] if h["kind"] == "backbone"]
    i4 = [(int(h["b"]["resnum"]), int(h["a"]["resnum"])) for h in bb
          if HELIX[0] <= int(h["b"]["resnum"]) and int(h["a"]["resnum"]) <= HELIX[1]
          and int(h["a"]["resnum"]) - int(h["b"]["resnum"]) == 4]
    assert sorted(i4) == [(i, i + 4) for i in range(23, 31)]
    for h in bb:
        if HELIX[0] <= int(h["b"]["resnum"]) and int(h["a"]["resnum"]) <= HELIX[1]:
            assert 2.7 <= h["distance"] <= 3.2 and h["angle"] >= 140 and h["hydrogen"] == "placed"


def test_ubq_sheet_hbonds_and_dssp_agreement(ubq, ubq_result):
    """Foglietto β a 5 filamenti: 21 legami scheletro-scheletro fra filamenti diversi (8 nella forcina β1–β2, 5 fra
    β1 e β5 paralleli, 5 fra β3 e β5, 3 fra β3 e β4). Tolleranza ±3 per piccoli cambi del criterio; confronto con il
    criterio elettrostatico del DSSP (energia < −0,5 kcal/mol): gli stessi legami, salvo l'N-terminale, per cui il
    DSSP non ha idrogeno."""
    bb = [h for h in ubq_result["hbonds"] if h["kind"] == "backbone"]
    sheet = {(h["b"]["resnum"], h["a"]["resnum"]) for h in bb
             if _strand(int(h["a"]["resnum"])) is not None and _strand(int(h["b"]["resnum"])) is not None
             and _strand(int(h["a"]["resnum"])) != _strand(int(h["b"]["resnum"]))}
    assert 18 <= len(sheet) <= 24
    assert len(sheet) == 21
    rows = secondary._backbone(ubq)
    dssp = {(rows[a]["resnum"], rows[d]["resnum"]) for a in range(len(rows)) for d in range(len(rows))
            if abs(a - d) > 1 and secondary._hbond_energy(rows[a], rows[d]) < secondary.HBOND_CUTOFF}
    dssp_sheet = {p for p in dssp if _strand(int(p[0])) is not None and _strand(int(p[1])) is not None
                  and _strand(int(p[0])) != _strand(int(p[1]))}
    assert dssp_sheet <= sheet
    assert sheet - dssp_sheet == {("17", "1")}  # N-terminale (NH₃⁺): criterio sugli atomi pesanti
    # nel complesso i legami geometrici dello scheletro sono per oltre il 90% anche legami del DSSP
    geo = {(h["b"]["resnum"], h["a"]["resnum"]) for h in bb}
    assert len(geo & dssp) / len(geo) >= 0.9


def test_ubq_no_backbone_pairs_between_consecutive_residues(ubq_result):
    for h in ubq_result["hbonds"]:
        assert h["a"]["resnum"] != h["b"]["resnum"]
        if h["kind"] == "backbone":
            assert abs(int(h["a"]["resnum"]) - int(h["b"]["resnum"])) >= 2
        assert h["distance"] <= interactions.HBOND_DA_MAX
        assert h["acceptor_angle"] >= interactions.HBOND_ANTECEDENT_MIN


def test_ubq_salt_bridges_known_pairs(ubq, ubq_result):
    """K11–E34, K27–D52 (ponti salini noti dell'ubiquitina) e R54–D58, le sole coppie entro 4,0 Å nel file."""
    got = {(b["a"]["resname"], b["a"]["resnum"], b["b"]["resname"], b["b"]["resnum"]): b
           for b in ubq_result["salt_bridges"]}
    assert set(got) == {("LYS", "11", "GLU", "34"), ("LYS", "27", "ASP", "52"), ("ARG", "54", "ASP", "58")}
    k27 = got[("LYS", "27", "ASP", "52")]
    chain = ubq["A"]
    expected = min(float(chain[27]["NZ"] - chain[52][name]) for name in ("OD1", "OD2"))
    assert k27["distance"] == pytest.approx(expected, abs=0.005) and k27["distance"] == pytest.approx(2.90, abs=0.01)
    assert (k27["a"]["atom"], k27["b"]["atom"]) == ("NZ", "OD2") and k27["pairs"] == 1
    assert not k27["his"] and not k27["inter_chain"]
    # verifica indipendente: tutte le coppie N+–O− entro 4,0 Å del file
    cations = [(r, a) for r in chain for a in interactions.CATIONIC.get(r.get_resname(), ()) if a in r]
    anions = [(r, a) for r in chain for a in interactions.ANIONIC.get(r.get_resname(), ()) if a in r]
    close = {(rc.id[1], ra.id[1]) for rc, ac in cations for ra, aa in anions if float(rc[ac] - ra[aa]) <= 4.0}
    assert close == {(11, 34), (27, 52), (54, 58)}


def test_ubq_no_disulfide_no_aromatic(ubq_result):
    s = ubq_result["summary"]
    assert ubq_result["disulfides"] == [] and s["disulfides"] == 0  # 1UBQ non ha cisteine
    assert ubq_result["aromatic"] == []  # F4, F45, Y59, H68 lontani fra loro
    assert s["residues"] == 76 and s["chains"] == 1 and s["inter_chain"]["hbonds"] == 0
    assert s["hbonds"] == s["hbonds_backbone"] + s["hbonds_mixed"] + s["hbonds_sidechain"]
    assert ubq_result["hydrogens"] == "placed"


def test_explicit_hydrogens_are_used(ubq, ubq_result):
    """Con gli idrogeni nel file: gli N–H dello scheletro (qui nelle posizioni del DSSP) danno gli stessi legami;
    i donatori senza idrogeni (catene laterali, N-terminale) non donano più; i ponti salini non cambiano."""
    from Bio.PDB.Atom import Atom

    model = ubq.copy()
    rows = secondary._backbone(model)
    chain = model["A"]
    for row in rows:
        if row["H"] is not None:
            chain[int(row["resnum"])].add(Atom("H", row["H"].astype("f"), 20.0, 1.0, " ", " H  ", 0, element="H"))
    res = interactions.analyze(model)
    assert res["hydrogens"] == "explicit"
    placed = {(h["a"]["resnum"], h["b"]["resnum"]) for h in ubq_result["hbonds"]
              if h["kind"] == "backbone" and h["a"]["resnum"] != "1"}
    explicit = {(h["a"]["resnum"], h["b"]["resnum"]) for h in res["hbonds"] if h["kind"] == "backbone"}
    assert explicit == placed
    assert all(h["a"]["atom"] == "N" and h["hydrogen"] == "explicit" for h in res["hbonds"])
    assert res["salt_bridges"] == ubq_result["salt_bridges"]


# ------------------------------------------------------------------------------------------ strutture sintetiche
def test_disulfide_synthetic(tmp_path):
    """Due Cys con SG–SG 2,05 Å e χ3 = 90° (costruito): ponte trovato; a 2,30 Å no."""
    def cys_pair(distance: float) -> list[tuple]:
        sg1, sg2 = np.zeros(3), np.array([distance, 0.0, 0.0])
        a = math.radians(104.0)
        cb1 = sg1 + 1.81 * np.array([math.cos(a), math.sin(a), 0.0])
        cb2 = sg2 + 1.81 * np.array([-math.cos(a), 0.0, math.sin(a)])
        ca1, ca2 = cb1 + [-0.9, 1.1, 0.5], cb2 + [0.9, 0.5, 1.1]
        atoms = []
        for resnum, (ca, cb, sg) in ((1, (ca1, cb1, sg1)), (10, (ca2, cb2, sg2))):
            atoms += [("N", "CYS", "A", resnum, tuple(ca + [0.5, 1.2, 0.0]), "N"),
                      ("CA", "CYS", "A", resnum, tuple(ca), "C"), ("CB", "CYS", "A", resnum, tuple(cb), "C"),
                      ("SG", "CYS", "A", resnum, tuple(sg), "S")]
        return atoms

    found = interactions.disulfides(_model(tmp_path, cys_pair(2.05), "ss"))
    assert len(found) == 1
    ss = found[0]
    assert (ss["a"]["resnum"], ss["b"]["resnum"], ss["a"]["atom"]) == ("1", "10", "SG")
    assert ss["distance"] == pytest.approx(2.05, abs=0.002)
    assert abs(ss["chi3"]) == pytest.approx(90.0, abs=0.5)
    assert interactions.disulfides(_model(tmp_path, cys_pair(2.19), "ss2"))
    assert interactions.disulfides(_model(tmp_path, cys_pair(2.30), "ss3")) == []
    assert interactions.disulfides(_model(tmp_path, cys_pair(1.75), "ss4")) == []


def test_salt_bridge_threshold_and_histidine(tmp_path):
    def pair(cation: str, atom: str, distance: float) -> list[tuple]:
        return [("CA", "ASP", "A", 1, (-3.0, 0.0, 0.0), "C"), ("CG", "ASP", "A", 1, (-1.25, 0.0, 0.0), "C"),
                ("OD1", "ASP", "A", 1, (0.0, 0.0, 0.0), "O"), ("OD2", "ASP", "A", 1, (-1.9, 1.1, 0.0), "O"),
                ("CA", cation, "B", 5, (distance + 3.0, 0.0, 0.0), "C"),
                (atom, cation, "B", 5, (distance, 0.0, 0.0), "N")]

    got = interactions.salt_bridges(_model(tmp_path, pair("LYS", "NZ", 3.9), "sb1"))
    assert len(got) == 1 and got[0]["distance"] == pytest.approx(3.9, abs=0.002)
    assert got[0]["inter_chain"] and got[0]["a"]["chain"] == "B" and got[0]["b"]["atom"] == "OD1"
    assert interactions.salt_bridges(_model(tmp_path, pair("LYS", "NZ", 4.1), "sb2")) == []
    his = interactions.salt_bridges(_model(tmp_path, pair("HIS", "NE2", 3.5), "sb3"))
    assert len(his) == 1 and his[0]["his"]


def test_hbond_criteria_side_chain_heavy_atoms(tmp_path):
    """Ser OG (gruppo che ruota: criterio sugli atomi pesanti) → Asp OD1: distanza e angoli degli antecedenti."""
    def ser_asp(distance: float, cg) -> list[tuple]:
        return [("CA", "SER", "A", 1, (-2.9, 0.4, 0.0), "C"), ("CB", "SER", "A", 1, (-1.43, 0.0, 0.0), "C"),
                ("OG", "SER", "A", 1, (0.0, 0.0, 0.0), "O"),
                ("CA", "ASP", "A", 8, (distance + 2.0, 2.5, 0.0), "C"), ("CG", "ASP", "A", 8, cg(distance), "C"),
                ("OD1", "ASP", "A", 8, (distance, 0.0, 0.0), "O")]

    good = lambda d: (d + 0.6, 1.1, 0.0)  # noqa: E731 - angolo OG…OD1–CG ≈ 119°
    bent = lambda d: (d - 0.8, 0.9, 0.0)  # noqa: E731 - angolo OG…OD1–CG ≈ 48°
    hb, mode = interactions.hydrogen_bonds(_model(tmp_path, ser_asp(2.7, good), "hb1"))
    assert mode == "placed"
    assert [(h["a"]["atom"], h["b"]["atom"], h["kind"], h["hydrogen"]) for h in hb] == [("OG", "OD1", "sidechain", None)]
    assert hb[0]["distance"] == pytest.approx(2.7, abs=0.002) and hb[0]["donor_angle"] == pytest.approx(180, abs=0.5)
    assert hb[0]["acceptor_angle"] == pytest.approx(118.6, abs=0.5)
    assert interactions.hydrogen_bonds(_model(tmp_path, ser_asp(3.6, good), "hb2"))[0] == []
    assert interactions.hydrogen_bonds(_model(tmp_path, ser_asp(2.7, bent), "hb3"))[0] == []


def test_hbond_pair_listed_once(tmp_path):
    """Ser OG ↔ Thr OG1: entrambi donatori e accettori, la coppia compare una volta sola."""
    atoms = [("CA", "SER", "A", 1, (-2.9, 0.5, 0.0), "C"), ("CB", "SER", "A", 1, (-1.43, 0.0, 0.0), "C"),
             ("OG", "SER", "A", 1, (0.0, 0.0, 0.0), "O"),
             ("CA", "THR", "A", 9, (5.7, 0.5, 0.0), "C"), ("CB", "THR", "A", 9, (4.2, 0.0, 0.0), "C"),
             ("OG1", "THR", "A", 9, (2.77, 0.0, 0.0), "O")]
    hb, _mode = interactions.hydrogen_bonds(_model(tmp_path, atoms, "st"))
    assert len(hb) == 1 and {hb[0]["a"]["atom"], hb[0]["b"]["atom"]} == {"OG", "OG1"}


def test_placed_hydrogens_geometry():
    """NH₂ di un'ammide: due idrogeni a 1,0 Å, nel piano del gruppo, a 120° dal legame C–N."""
    from Bio.PDB.StructureBuilder import StructureBuilder

    builder = StructureBuilder()
    builder.init_structure("x")
    builder.init_model(0)
    builder.init_chain("A")
    builder.init_seg("    ")
    builder.init_residue("ASN", " ", 1, " ")
    for name, xyz in (("CA", (-2.5, 0.0, 0.0)), ("CB", (-1.5, 1.0, 0.0)), ("CG", (0.0, 0.0, 0.0)),
                      ("OD1", (-0.6, -1.06, 0.0)), ("ND2", (1.33, 0.0, 0.0))):
        builder.init_atom(name, np.array(xyz, "f"), 20.0, 1.0, " ", f" {name:<3}", element=name[0])
    res = builder.get_structure()[0]["A"][1]
    hs = interactions._placed_hydrogens(res, "ND2", ("CG", "OD1"), "planar2")
    assert len(hs) == 2
    n, c = np.array([1.33, 0.0, 0.0]), np.zeros(3)
    for h in hs:
        assert np.linalg.norm(h - n) == pytest.approx(1.0, abs=1e-6)
        assert interactions._angle(c, n, h) == pytest.approx(120.0, abs=1e-4)
        assert h[2] == pytest.approx(0.0, abs=1e-6)  # nel piano CG, OD1, ND2
    assert interactions._placed_hydrogens(res, "ND2", ("CG",), "rotor") is None


def test_pi_stacking_parallel_tshaped_and_rejected(tmp_path):
    parallel = interactions.aromatic(_model(tmp_path, _phe("A", 1, (0, 0, 0)) + _phe("A", 20, (0, 0, 3.8)), "pp"))
    assert [(e["type"], e["distance"], e["angle"], e["offset"]) for e in parallel] == [("pi_parallel", 3.8, 0.0, 0.0)]
    tshaped = interactions.aromatic(_model(tmp_path, _phe("A", 1, (0, 0, 0)) + _phe("B", 3, (0, 0, 5.0), "x"), "pt"))
    assert len(tshaped) == 1 and tshaped[0]["type"] == "pi_tshaped" and tshaped[0]["angle"] == pytest.approx(90.0)
    assert tshaped[0]["inter_chain"] and tshaped[0]["a"]["atom"] == "anello"
    shifted = _phe("A", 1, (0, 0, 0)) + _phe("A", 20, (3.0, 0, 3.6))  # centroidi a 4,7 Å ma spostamento 3,0 Å
    assert interactions.aromatic(_model(tmp_path, shifted, "ps")) == []
    far = _phe("A", 1, (0, 0, 0)) + _phe("A", 20, (0, 0, 5.8))
    assert interactions.aromatic(_model(tmp_path, far, "pf")) == []


def test_cation_pi(tmp_path):
    def lys(z: float, x: float = 0.0) -> list[tuple]:
        return [("CA", "LYS", "B", 7, (x, 0.0, z + 5.0), "C"), ("CE", "LYS", "B", 7, (x, 0.0, z + 1.5), "C"),
                ("NZ", "LYS", "B", 7, (x, 0.0, z), "N")]

    got = interactions.aromatic(_model(tmp_path, _phe("A", 1, (0, 0, 0)) + lys(4.0), "cp"))
    assert [(e["type"], e["a"]["atom"], e["distance"], e["offset"]) for e in got] == [("cation_pi", "NZ", 4.0, 0.0)]
    assert interactions.aromatic(_model(tmp_path, _phe("A", 1, (0, 0, 0)) + lys(4.0, x=3.0), "cp2")) == []
    assert interactions.aromatic(_model(tmp_path, _phe("A", 1, (0, 0, 0)) + lys(6.2), "cp3")) == []


def test_waters_and_ligands_ignored():
    model =structure.first_model(structure.load_structure(DATA / "ubq_zn.pdb", "zn"))
    res = interactions.analyze(model)
    names = {e[k]["resname"] for key in ("hbonds", "salt_bridges", "aromatic") for e in res[key] for k in ("a", "b")}
    assert not names & {"HOH", "ZN"}
    assert res["summary"]["residues"] == 76


def test_dihedral_reference():
    p = [np.array(v, float) for v in ((1, 0, 0), (0, 0, 0), (0, 0, 1), (0, 1, 1))]
    assert interactions.dihedral(*p) == pytest.approx(90.0)
    p[3] = np.array([1.0, 0.0, 1.0])
    assert interactions.dihedral(*p) == pytest.approx(0.0)
