"""Confronto strutturale esteso: distanze per residuo, GDT-TS/GDT-HA, lDDT sui Cα (domain/comparison.py) e i due
percorsi di `engines/align.structural` (integrato e TM-align/US-align).

Valori di riferimento su 1UBQ, su copie sintetiche generate qui in modo riproducibile (moto rigido, tratto 31–37
spostato verso l'esterno, residuo inserito, Cα mancante, catena accorciata) e su tests/data/ubq_dimer.pdb.
TM-align e US-align sono quelli veri dell'immagine: senza, i casi relativi si saltano. Nessun accesso alla rete.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest
from Bio.PDB.Atom import Atom
from Bio.PDB.Residue import Residue

from bioloom.config import UserSettings
from bioloom.core import cache
from bioloom.core.jobs import JobContext, ProcessResult
from bioloom.domain import comparison, geometry, structure
from bioloom.engines import align, tools

from .conftest import DATA, HUMAN_UB, wait_job

UBQ_A = {"type": "template", "id": "1UBQ", "chain": "A"}
SEGMENT = range(31, 38)  # 7 residui: fine dell'elica α1 e ansa successiva


def _tool(name: str):
    missing = name != "builtin" and not tools.find(name)
    return pytest.param(name, id=name, marks=pytest.mark.skipif(missing, reason=f"{name} non installato"))


ALL_TOOLS = [_tool("builtin"), _tool("tmalign"), _tool("usalign")]
EXTERNAL = [_tool("tmalign"), _tool("usalign")]


# ------------------------------------------------------------------------------------------ strutture sintetiche
def _rotation(axis, angle: float) -> np.ndarray:
    """Matrice di rotazione (Rodrigues) di `angle` radianti attorno ad `axis`."""
    k = np.asarray(axis, dtype=float)
    k /= np.linalg.norm(k)
    cross = np.array([[0, -k[2], k[1]], [k[2], 0, -k[0]], [-k[1], k[0], 0]])
    return np.eye(3) + np.sin(angle) * cross + (1 - np.cos(angle)) * cross @ cross


ROT = _rotation([1, 2, 3], 0.7)
TRANS = np.array([10.0, -5.0, 3.0])


def _ubq():
    return structure.load_structure(DATA / "1UBQ.pdb")


def _chain(st):
    return structure.first_model(st)["A"]


def _ca(st) -> list[dict]:
    return structure.ca_trace(structure.first_model(st), "A")


def _save(st, path: Path) -> Path:
    structure.write_pdb(st, path, chains=["A"])
    return path


def _rigid(st):
    for atom in st.get_atoms():
        atom.set_coord(atom.coord @ ROT.T + TRANS)
    return st


def _shift_segment(st, amount: float):
    """Tratto 31–37 traslato di `amount` Å verso l'esterno (dal baricentro dei Cα al baricentro del tratto)."""
    chain = _chain(st)
    residues = [r for r in chain if structure.is_amino_residue(r)]
    segment = [r for r in residues if r.id[1] in SEGMENT]
    direction = (np.mean([r["CA"].coord for r in segment], axis=0) - np.mean([r["CA"].coord for r in residues], axis=0))
    direction /= np.linalg.norm(direction)
    for r in segment:
        for atom in r:
            atom.set_coord(atom.coord + amount * direction)
    return st


def _with_insertion(st):
    """Un TRP in più (numero 35, codice di inserzione A) tra Gly35 e Ile36, 3 Å fuori dalla catena."""
    chain = _chain(st)
    g35, i36 = chain[35], chain[36]
    centre = np.mean([r["CA"].coord for r in chain if structure.is_amino_residue(r)], axis=0)
    mid = (g35["CA"].coord + i36["CA"].coord) / 2
    out = (mid - centre) / np.linalg.norm(mid - centre)
    residue = Residue((" ", 35, "A"), "TRP", "    ")
    for name, offset in (("N", -1.2), ("CA", 0.0), ("C", 1.2)):
        pos = (mid + 3.0 * out + np.array([offset, 0.0, 0.0])).astype("f")
        residue.add(Atom(name, pos, 20.0, 1.0, " ", f" {name:<3}", None, name[0]))
    chain.insert(chain.child_list.index(i36), residue)
    return st


def _without_ca(st, resnum: int):
    _chain(st)[resnum].detach_child("CA")
    return st


def _upload(c, path: Path) -> dict:
    r = c.post("/api/files/structure", files={"file": (path.name, path.read_bytes())})
    assert r.status_code == 200, r.text
    return {**r.json()["source"], "chain": "A"}


def _compare(c, a: dict, b: dict, tool: str, **extra) -> dict:
    job = c.post("/api/align", json={"mode": "structural", "tool": tool, "structures": [a, b], **extra}).json()
    assert "id" in job, job
    done = wait_job(c, job["id"])
    assert done["status"] == "completed", done
    return done


def _by_a(result: dict) -> dict[str, dict]:
    return {p["resnum_a"]: p for p in result["pairs"]}


def _neighbour_pairs(coords: np.ndarray, radius: float = 15.0) -> np.ndarray:
    """Matrice booleana delle coppie di Cα più vicine di `radius` (senza la diagonale), calcolata a parte."""
    d = np.sqrt(((coords[:, None, :] - coords[None, :, :]) ** 2).sum(-1))
    inside = d < radius
    np.fill_diagonal(inside, False)
    return inside


# ------------------------------------------------------------------------------------------ logica pura
def test_lddt_matches_hand_computed_values():
    # tre Cα in linea a 3,8 Å; nel modello il terzo si allontana di 1,5 Å: le distanze 1–3 e 2–3 cambiano di 1,5 Å,
    # conservate solo alle soglie 2 e 4 Å
    a = np.array([[0.0, 0, 0], [3.8, 0, 0], [7.6, 0, 0]])
    b = np.array([[0.0, 0, 0], [3.8, 0, 0], [9.1, 0, 0]])
    res = comparison.lddt_ca(a, b, [0, 1, 2])
    assert res["global"] == pytest.approx((4 + 2 + 2) / 12, abs=1e-4)
    assert res["per_residue"] == pytest.approx([6 / 8, 6 / 8, 4 / 8], abs=1e-4)
    assert res["distances"] == 3
    # terzo residuo senza corrispondente: le sue distanze sono tutte non conservate
    missing = comparison.lddt_ca(a, b, [0, 1, -1])
    assert missing["global"] == pytest.approx(4 / 12, abs=1e-4)
    assert missing["per_residue"] == pytest.approx([4 / 8, 4 / 8, 0.0], abs=1e-4)
    # raggio di inclusione escluso (d < 15 Å) e soglie escluse (|Δd| < t): 1,0 Å di differenza vale per 2 e 4 Å
    far = comparison.lddt_ca(np.array([[0.0, 0, 0], [15.0, 0, 0]]), np.array([[0.0, 0, 0], [15.0, 0, 0]]), [0, 1])
    assert far == {"global": None, "per_residue": [None, None], "distances": 0}
    edge = comparison.lddt_ca(np.array([[0.0, 0, 0], [5.0, 0, 0]]), np.array([[0.0, 0, 0], [6.0, 0, 0]]), [0, 1])
    assert edge["global"] == pytest.approx(0.5)


def test_lddt_is_superposition_free_on_1ubq():
    ca = _ca(_ubq())
    coords = np.array([r["coord"] for r in ca])
    moved = coords @ ROT.T + TRANS
    res = comparison.lddt_ca(coords, moved, np.arange(len(coords)))
    assert res["global"] == 1.0 and set(res["per_residue"]) == {1.0}
    assert res["distances"] == int(_neighbour_pairs(coords).sum() // 2)


def test_gdt_maximises_each_cutoff_on_its_own_superposition():
    # due gruppi rigidi mescolati nello spazio: 10 Cα fermi, 5 traslati di 5 Å. Sulla sovrapposizione che tiene
    # fermi i 10 (quella del TM-score) solo 10 stanno entro 4 Å; spostando tutto di 5/3 Å ci stanno tutti e 15.
    rng = np.random.default_rng(7)
    target = rng.uniform(0, 12, size=(15, 3))
    mobile = target.copy()
    mobile[10:] += np.array([5.0, 0.0, 0.0])
    tm_superposition = (np.eye(3), np.zeros(3))
    on_tm = {c: int((np.linalg.norm(mobile - target, axis=1) <= c).sum()) for c in (1.0, 4.0)}
    assert on_tm == {1.0: 10, 4.0: 10}
    searched = comparison.gdt_counts(mobile, target, [tm_superposition], cutoffs=(1.0, 4.0))
    assert searched[1.0] == 10 and searched[4.0] == 15
    scores = comparison.gdt_scores({0.5: 38, 1.0: 57, 2.0: 68, 4.0: 76, 8.0: 76}, 76)
    assert scores["gdt"][0] == {"cutoff": 0.5, "count": 38, "percent": 50.0}
    assert scores["gdt_ts"] == pytest.approx((57 + 68 + 76 + 76) / 4 / 76 * 100, abs=0.01)
    assert scores["gdt_ha"] == pytest.approx((38 + 57 + 68 + 76) / 4 / 76 * 100, abs=0.01)


def test_compare_on_identical_1ubq():
    ca = _ca(_ubq())
    res = comparison.compare(ca, ca, [(i, i) for i in range(len(ca))], np.eye(3), np.zeros(3))
    assert res["gdt_ts"] == res["gdt_ha"] == 100.0 and res["lddt"] == 1.0
    assert res["close_pairs"] == 76 and res["rmsd_close"] == 0.0
    assert len(res["pairs"]) == 76 and {p["distance"] for p in res["pairs"]} == {0.0}
    first = res["pairs"][0]
    assert first == {"chain_a": "A", "resnum_a": "1", "aa_a": "M", "chain_b": "A", "resnum_b": "1", "aa_b": "M",
                     "distance": 0.0, "lddt": 1.0}
    assert comparison.kabsch_rmsd(np.array([r["coord"] for r in ca]), np.array([r["coord"] for r in ca])) == 0.0


def test_matrix_parser_reads_tmalign_and_usalign_formats(tmp_path):
    tm = tmp_path / "tm.txt"
    tm.write_text(" -------- Rotation matrix to rotate Chain_1 to Chain_2 ------\n"
                  " m          t(m)         u(m,1)         u(m,2)         u(m,3)\n"
                  " 1     -4.1840575711   0.7816371227   0.5501208931  -0.2939564786\n"
                  " 2      8.1706252459  -0.4829313484   0.8320274508   0.2729608651\n"
                  " 3     -7.0523745750   0.3947413344  -0.0713955466   0.9160141674\n"
                  " Code for rotating Chain_1 from (x,y,z) to (X,Y,Z):\n    do i=1,L\n", encoding="utf-8")
    us = tmp_path / "us.txt"
    us.write_text("------ The rotation matrix to rotate Structure_1 to Structure_2 ------\n"
                  "m               t[m]        u[m][0]        u[m][1]        u[m][2]\n"
                  "0      -4.1840573273   0.7816371348   0.5501208734  -0.2939564835\n"
                  "1       8.1706241660  -0.4829313289   0.8320274636   0.2729608605\n"
                  "2      -7.0523744259   0.3947413344  -0.0713955497   0.9160141671\n\n"
                  "   X[i] = t[0] + u[0][0]*x[i] + u[0][1]*y[i] + u[0][2]*z[i];\n", encoding="utf-8")
    rot_tm, trans_tm = align._read_matrix(tm)
    rot_us, trans_us = align._read_matrix(us)
    assert trans_tm == pytest.approx([-4.18405757, 8.17062525, -7.05237457])
    assert rot_tm[0] == pytest.approx([0.78163712, 0.55012089, -0.29395648])
    assert np.allclose(rot_tm, rot_us, atol=1e-7) and np.allclose(trans_tm, trans_us, atol=1e-5)
    bad = tmp_path / "bad.txt"
    bad.write_text("1 0 2 0 0\n2 0 0 2 0\n3 0 0 0 2\n", encoding="utf-8")  # non è una rotazione
    assert align._read_matrix(bad) is None and align._read_matrix(tmp_path / "manca.txt") is None


# ------------------------------------------------------------------------------------------ motore: due percorsi
@pytest.mark.parametrize("tool", ALL_TOOLS)
def test_self_and_rigid_copy_give_perfect_scores(client_with_ubq, tmp_path, tool):
    c = client_with_ubq
    rigid = _upload(c, _save(_rigid(_ubq()), tmp_path / "ruotata.pdb"))
    ref_ca = np.array([r["coord"] for r in _ca(_ubq())])
    for other in (UBQ_A, rigid):
        done = _compare(c, UBQ_A, other, tool)
        r = done["result"]
        assert r["tm_score"] == pytest.approx(1.0, abs=1e-4) and r["tm_score_b"] == pytest.approx(1.0, abs=1e-4)
        assert r["rmsd"] <= 0.01 and r["rmsd_close"] <= 0.01 and r["close_pairs"] == 76
        assert r["gdt_ts"] == r["gdt_ha"] == 100.0 and r["lddt"] == 1.0
        assert [g["cutoff"] for g in r["gdt"]] == [0.5, 1.0, 2.0, 4.0, 8.0] and {g["count"] for g in r["gdt"]} == {76}
        assert len(r["pairs"]) == 76 == r["aligned_length"] and max(p["distance"] for p in r["pairs"]) < 0.01
        assert all(p["resnum_a"] == p["resnum_b"] and p["aa_a"] == p["aa_b"] for p in r["pairs"])
        assert r["lengths"] == [76, 76] and r["lddt_distances"] == int(_neighbour_pairs(ref_ca).sum() // 2)
        # la sovrapposizione porta B su A: applicata ai Cα della copia li riporta su quelli di 1UBQ
        rot, trans = np.array(r["transform"]["rotation"]), np.array(r["transform"]["translation"])
        moved = ref_ca @ ROT.T + TRANS if other is rigid else ref_ca
        assert np.abs(geometry.apply(moved, rot, trans) - ref_ca).max() < 0.01
        # PDB per il viewer: catena A = 1UBQ com'è, catena B = la copia sovrapposta (in tutti e due i percorsi)
        text = c.get(f"/api/jobs/{done['id']}/files/superposed.pdb").text
        sup = structure.first_model(structure.load_structure(_write(tmp_path / f"sup_{tool}.pdb", text)))
        chain_a = np.array([t["coord"] for t in structure.ca_trace(sup, "A")])
        chain_b = np.array([t["coord"] for t in structure.ca_trace(sup, "B")])
        assert np.abs(chain_a - ref_ca).max() < 0.002 and np.abs(chain_b - ref_ca).max() < 0.01


def _write(path: Path, text: str) -> Path:
    path.write_text(text, encoding="utf-8")
    return path


@pytest.mark.parametrize("tool", ALL_TOOLS)
def test_shifted_segment_is_localised(client_with_ubq, tmp_path, tool):
    c = client_with_ubq
    shifted = _upload(c, _save(_shift_segment(_ubq(), 6.0), tmp_path / "tratto.pdb"))
    r = _compare(c, UBQ_A, shifted, tool)["result"]
    pairs = _by_a(r)
    assert len(pairs) == 76
    inside = {str(n) for n in SEGMENT}
    # distanze alte solo nel tratto spostato (la sovrapposizione del TM-score tiene fermi gli altri 69 residui)
    assert all(abs(pairs[n]["distance"] - 6.0) < 0.01 for n in inside)
    assert all(p["distance"] < 0.01 for n, p in pairs.items() if n not in inside)
    # lDDT per residuo basso solo nel tratto: fuori, anche i vicini restano sopra 0,8
    assert max(pairs[n]["lddt"] for n in inside) < 0.3
    assert min(p["lddt"] for n, p in pairs.items() if n not in inside) > 0.8
    ca = _ca(_ubq())
    coords = np.array([t["coord"] for t in ca])
    seg_idx = [i for i, t in enumerate(ca) if t["resnum"] in inside]
    far = [t["resnum"] for i, t in enumerate(ca)
           if np.linalg.norm(coords[seg_idx] - coords[i], axis=1).min() >= 15.0]
    assert far and all(pairs[n]["lddt"] == 1.0 for n in far)
    # valori di riferimento: lDDT (indipendente dalla sovrapposizione), GDT per soglia, RMSD
    assert r["lddt"] == pytest.approx(0.8778, abs=1e-4)
    counts = {g["cutoff"]: g["count"] for g in r["gdt"]}
    assert counts[0.5] == counts[1.0] == counts[2.0] == 69 and counts[8.0] == 76
    assert 69 < counts[4.0] <= 76  # a 4 Å conviene una sovrapposizione a metà fra i due gruppi
    assert r["gdt_ts"] < 100 and r["gdt_ha"] < 100 and r["gdt_ha"] < r["gdt_ts"]
    assert r["gdt_ha"] == pytest.approx((3 * 69 + counts[4.0]) / 4 / 76 * 100, abs=0.01)
    assert r["close_pairs"] == 69 and r["rmsd_close"] < 0.01
    assert r["tm_score"] == pytest.approx(0.9271, abs=2e-4)
    assert r["rmsd"] == pytest.approx(1.735, abs=0.01)  # Kabsch su tutte le coppie, come TM-align


@pytest.mark.parametrize("tool", ALL_TOOLS)
def test_dimer_chain_a_against_chain_b(client, tool):
    c = client
    dimer = _upload(c, DATA / "ubq_dimer.pdb")
    r = _compare(c, {**dimer, "chain": "A"}, {**dimer, "chain": "B"}, tool)["result"]
    assert r["rmsd"] < 0.01 and r["tm_score"] == pytest.approx(1.0, abs=1e-4) and r["lddt"] == 1.0
    assert r["gdt_ts"] == 100.0 and len(r["pairs"]) == 76
    assert {p["chain_a"] for p in r["pairs"]} == {"A"} and {p["chain_b"] for p in r["pairs"]} == {"B"}
    assert max(p["distance"] for p in r["pairs"]) < 0.01
    # senza catena: la prima (A) del dimero contro 1UBQ
    ubq = _upload(c, DATA / "1UBQ.pdb")
    first = _compare(c, {**dimer, "chain": None}, {**ubq, "chain": None}, tool)["result"]
    assert first["rmsd"] < 0.01 and {p["chain_a"] for p in first["pairs"]} == {"A"}


@pytest.mark.parametrize("tool", ALL_TOOLS)
def test_insertion_in_the_second_structure_keeps_the_mapping(client_with_ubq, tmp_path, tool):
    c = client_with_ubq
    inserted = _upload(c, _save(_with_insertion(_ubq()), tmp_path / "inserzione.pdb"))
    r = _compare(c, UBQ_A, inserted, tool)["result"]
    pairs = _by_a(r)
    assert r["lengths"] == [76, 77] and len(pairs) == 76
    assert "35A" not in {p["resnum_b"] for p in r["pairs"]}
    assert pairs["35"]["resnum_b"] == "35" and pairs["36"]["resnum_b"] == "36" and pairs["36"]["aa_b"] == "I"
    assert all(p["resnum_a"] == p["resnum_b"] for p in r["pairs"])
    assert max(p["distance"] for p in r["pairs"]) < 0.01
    # A è il riferimento: tutti i suoi residui hanno un corrispondente identico
    assert r["lddt"] == 1.0 and r["gdt_ts"] == 100.0
    assert r["tm_score"] == pytest.approx(1.0, abs=1e-4) and r["tm_score_b"] == pytest.approx(76 / 77, abs=2e-4)
    # al contrario il residuo in più è di A, senza corrispondente: il lDDT lo conta
    reverse = _compare(c, inserted, UBQ_A, tool)["result"]
    assert reverse["lengths"] == [77, 76] and len(reverse["pairs"]) == 76 and reverse["lddt"] < 1.0
    assert reverse["gdt_ts"] == pytest.approx(76 / 77 * 100, abs=0.01)


@pytest.mark.parametrize("tool", ALL_TOOLS)
def test_residue_without_ca_has_no_pair(client_with_ubq, tmp_path, tool):
    c = client_with_ubq
    no_ca = _upload(c, _save(_without_ca(_ubq(), 20), tmp_path / "senza_ca.pdb"))
    r = _compare(c, UBQ_A, no_ca, tool)["result"]
    pairs = _by_a(r)
    assert r["lengths"] == [76, 75] and len(pairs) == 75 and "20" not in pairs
    assert pairs["21"]["resnum_b"] == "21" and max(p["distance"] for p in r["pairs"]) < 0.01
    # lDDT: le sole distanze del residuo 20 (senza corrispondente) non sono conservate
    coords = np.array([t["coord"] for t in _ca(_ubq())])
    inside = _neighbour_pairs(coords)
    expected = 1 - inside[19].sum() / (inside.sum() / 2)  # distanze del residuo 20 / distanze distinte
    assert r["lddt"] == pytest.approx(expected, abs=1e-4) and r["lddt"] == pytest.approx(0.9774, abs=1e-4)
    assert r["gdt_ts"] == pytest.approx(75 / 76 * 100, abs=0.01) and r["tm_score_b"] == pytest.approx(1.0, abs=1e-4)


@pytest.mark.parametrize("tool", EXTERNAL)
def test_real_tool_and_builtin_agree(client_with_ubq, tmp_path, tool):
    c = client_with_ubq
    cases = {"tratto": _shift_segment(_ubq(), 6.0), "ruotata": _rigid(_shift_segment(_ubq(), 3.0)),
             "senza_ca": _without_ca(_ubq(), 50)}
    for name, st in cases.items():
        src = _upload(c, _save(st, tmp_path / f"{name}.pdb"))
        ext = _compare(c, UBQ_A, src, tool)["result"]
        own = _compare(c, UBQ_A, src, "builtin")["result"]
        assert ext["method"] != own["method"]
        assert ext["tm_score"] == pytest.approx(own["tm_score"], abs=1e-3)
        assert ext["tm_score_b"] == pytest.approx(own["tm_score_b"], abs=1e-3)
        assert ext["rmsd"] == pytest.approx(own["rmsd"], abs=0.01)
        assert ext["lddt"] == pytest.approx(own["lddt"], abs=1e-4)
        assert ext["gdt_ts"] == pytest.approx(own["gdt_ts"], abs=1.5)
        assert ext["close_pairs"] == own["close_pairs"]
        assert [(p["resnum_a"], p["resnum_b"]) for p in ext["pairs"]] == [(p["resnum_a"], p["resnum_b"]) for p in
                                                                         own["pairs"]]
        # le due ricerche del TM-score sono euristiche: con il tratto a 3 Å (dentro la distanza di ricerca) TM-align
        # trova 0,9557 e l'integrato 0,9553, due sovrapposizioni diverse di circa un decimo di Å; negli altri casi
        # la sovrapposizione ottima è una sola (69 o 75 coppie identiche)
        tolerance = 0.2 if name == "ruotata" else 0.01
        assert np.allclose([p["distance"] for p in ext["pairs"]], [p["distance"] for p in own["pairs"]],
                           atol=tolerance)
        # la matrice del programma riproduce il TM-score che il programma stesso riporta
        d = np.array([p["distance"] for p in ext["pairs"]])
        assert (1 / (1 + (d / geometry.tm_d0(76)) ** 2)).sum() / 76 == pytest.approx(ext["tm_score"], abs=1e-4)


@pytest.mark.parametrize("tool", EXTERNAL)
def test_tool_leaves_far_residues_unaligned(client_with_ubq, tmp_path, tool):
    # tratto spostato di 12 Å: TM-align/US-align lo lasciano fuori dall'allineamento (colonne senza ":" né "."),
    # l'integrato, che allinea per sequenza, lo tiene con la sua distanza
    c = client_with_ubq
    far = _upload(c, _save(_shift_segment(_ubq(), 12.0), tmp_path / "lontano.pdb"))
    ext = _compare(c, UBQ_A, far, tool)["result"]
    assert ext["aligned_length"] == len(ext["pairs"]) == 69
    assert not {str(n) for n in SEGMENT} & set(_by_a(ext))
    assert ext["lddt"] == pytest.approx(0.8552, abs=1e-4) and ext["gdt_ts"] == pytest.approx(69 / 76 * 100, abs=0.01)
    assert "note" not in ext
    own = _compare(c, UBQ_A, far, "builtin")["result"]
    assert len(own["pairs"]) == 76 and _by_a(own)["34"]["distance"] == pytest.approx(12.0, abs=0.01)
    assert own["lddt"] > ext["lddt"]  # le distanze interne al tratto spostato rigidamente restano conservate


# ------------------------------------------------------------------------------------------ uscite anomale
def _fake_tool(monkeypatch, tmp_path, output: str, matrix: str | None = None):
    exe = tmp_path / "TMalign"
    exe.write_text("")
    monkeypatch.setattr(tools, "find", lambda key: str(exe) if key == "tmalign" else None)

    def fake(self, cmd, **kwargs):
        if matrix is not None:
            Path(cmd[cmd.index("-m") + 1]).write_text(matrix, encoding="utf-8")
        return ProcessResult(0, output)

    monkeypatch.setattr(JobContext, "run", fake)


def _tm_output(mobile: str, target: str, marks: str | None = None) -> str:
    return ("Aligned length=   76, RMSD=   0.00, Seq_ID=n_identical/n_aligned= 1.000\n"
            "TM-score= 1.00000 (if normalized by length of Chain_1)\n"
            "TM-score= 1.00000 (if normalized by length of Chain_2)\n"
            '(":" denotes residue pairs of d <  5.0 Angstrom, "." denotes other aligned residues)\n'
            f"{mobile}\n{marks if marks is not None else ':' * len(mobile)}\n{target}\n")


def test_residues_not_matching_the_files_are_reported_and_not_cached(client_with_ubq, monkeypatch, tmp_path):
    c = client_with_ubq
    wrong = HUMAN_UB[:10] + HUMAN_UB[11:] + "-"  # un residuo in meno rispetto al file
    _fake_tool(monkeypatch, tmp_path, _tm_output(wrong, HUMAN_UB))
    done = _compare(c, UBQ_A, UBQ_A, "tmalign")
    r = done["result"]
    assert r["tm_score"] == 1.0 and "pairs" not in r and "non corrispondono" in r["note"]
    assert not list((c.app.state.core.paths.compute_cache / "align_structural").glob("*.json"))


def test_missing_matrix_falls_back_to_the_builtin_superposition(client_with_ubq, monkeypatch, tmp_path):
    c = client_with_ubq
    marks = ":" * 70 + " " * 6  # le ultime 6 colonne hanno due residui ma non sono allineate
    _fake_tool(monkeypatch, tmp_path, _tm_output(HUMAN_UB, HUMAN_UB, marks))
    r = _compare(c, UBQ_A, UBQ_A, "tmalign")["result"]
    assert len(r["pairs"]) == 70 and r["pairs"][-1]["resnum_a"] == "70"
    assert "Matrice di rotazione" in r["note"] and "76 coppie" in r["note"]
    assert max(p["distance"] for p in r["pairs"]) < 0.01 and r["gdt_ts"] == pytest.approx(70 / 76 * 100, abs=0.01)
    # con la matrice (identità) nessuna nota sulla sovrapposizione
    identity = "".join(f" {i + 1}  0.0  {' '.join('1.0' if j == i else '0.0' for j in range(3))}\n" for i in range(3))
    _fake_tool(monkeypatch, tmp_path, _tm_output(HUMAN_UB, HUMAN_UB), matrix=identity)
    again = _compare(c, UBQ_A, UBQ_A, "tmalign", use_cache=False)["result"]
    assert len(again["pairs"]) == 76 and "note" not in again


# ------------------------------------------------------------------------------------------ cache
def test_structural_cache_entries_without_the_comparison_are_not_reused(client_with_ubq):
    c = client_with_ubq
    core = c.app.state.core
    path = core.library.path("1UBQ")
    descriptor = align.structural_descriptor(path, "A", path, "A", "builtin", None)
    old = {"method": "vecchio", "ids": ["x", "y"], "aligned": [HUMAN_UB, HUMAN_UB], "tm_score": 0.5,
           "tm_score_b": 0.5, "rmsd": 9.9, "aligned_length": 76, "superposed_pdb": "END\n"}
    # voce della versione precedente (chiave senza "version") e voce con la chiave nuova ma senza il confronto
    previous = {k: v for k, v in descriptor.items() if k != "version"}
    cache.store("align_structural", cache.make_key("align_structural", previous), old, {})
    new_key = cache.make_key("align_structural", descriptor)
    cache.store("align_structural", new_key, old, {})
    done = _compare(c, UBQ_A, UBQ_A, "builtin")
    r = done["result"]
    assert not r.get("cached") and r["tm_score"] == 1.0 and len(r["pairs"]) == 76
    assert "Risultato in cache incompleto" in c.get(f"/api/jobs/{done['id']}/log").json()["text"]
    entry = json.loads((core.paths.compute_cache / "align_structural" / f"{new_key}.json").read_text("utf-8"))
    assert len(entry["result"]["pairs"]) == 76 and entry["result"]["lddt"] == 1.0
    # la voce completa si riusa, con tutti i campi nuovi
    again = _compare(c, UBQ_A, UBQ_A, "builtin")["result"]
    assert again["cached"] is True and again["pairs"] == r["pairs"] and again["gdt"] == r["gdt"]
    assert align._complete(again) and not align._complete({**again, "pairs": []})


def test_structural_results_of_the_engine_have_a_versioned_key(tmp_path):
    p = DATA / "1UBQ.pdb"
    descriptor = align.structural_descriptor(p, "A", p, None, "builtin", None)
    assert descriptor["version"] == align.STRUCTURAL_VERSION >= 2
    assert descriptor["structures"][1]["chain"] == ""


def test_builtin_structural_direct_call(tmp_path):
    """Senza JobManager (come la verifica dell'installazione): stessi campi, nessuna cache configurata."""

    class Ctx:
        workdir = tmp_path
        settings = UserSettings()
        cancelled = False

        def progress(self, *a, **k):
            pass

        def log(self, message):
            pass

        def path(self, *parts):
            return tmp_path.joinpath(*parts)

    shifted = _save(_shift_segment(_ubq(), 6.0), tmp_path / "tratto.pdb")
    r = align.structural(DATA / "1UBQ.pdb", "A", shifted, "A", Ctx(), tool="builtin", use_cache=False)
    assert align._complete(r) and r["ids"] == ["A", "B"] and r["superposed_pdb"].startswith("ATOM")
    assert r["lddt"] == pytest.approx(0.8778, abs=1e-4)
