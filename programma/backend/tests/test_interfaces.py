"""Interfacce fra catene (domain/interfaces.py, F7) su ubq_dimer.pdb e su assemblaggi sintetici; prestazioni
dell'analisi strutturale completa su una struttura di circa 5000 atomi."""

from __future__ import annotations

import time

import numpy as np
import pytest

from bioloom.domain import conservation, interactions, interfaces, structure

from .conftest import DATA, HUMAN_UB


def _load(path, name="s"):
    return structure.first_model(structure.load_structure(path, name))


def _assembly(tmp_path, copies: int, spacing: float = 80.0, name: str = "assembly"):
    """`copies` copie di ubq_dimer.pdb traslate di `spacing` Å lungo x, con catene A, B, C, D, …"""
    lines = [ln for ln in (DATA / "ubq_dimer.pdb").read_text(encoding="utf-8").splitlines() if ln.startswith("ATOM")]
    out = []
    letters = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    for k in range(copies):
        for ln in lines:
            chain = letters[2 * k + (0 if ln[21] == "A" else 1)]
            x = float(ln[30:38]) + k * spacing
            out.append(f"{ln[:21]}{chain}{ln[22:30]}{x:8.3f}{ln[38:]}")
    path = tmp_path / f"{name}.pdb"
    path.write_text("\n".join(out + ["END"]) + "\n", encoding="utf-8")
    return path


@pytest.fixture(scope="module")
def dimer():
    model = _load(DATA / "ubq_dimer.pdb", "dimer")
    return model, interfaces.analyze(model, interactions.analyze(model))


def test_dimer_interface_symmetric_and_small(dimer):
    """Omodimero C2 sintetico (26 contatti < 4,5 Å): stessi residui d'interfaccia in A e in B, area positiva e
    piccola, sotto la soglia dei contatti cristallini."""
    _model, res = dimer
    assert res["chains"] == ["A", "B"] and len(res["pairs"]) == 1
    pair = res["pairs"][0]
    a = {r["resnum"] for r in pair["residues"]["A"]}
    b = {r["resnum"] for r in pair["residues"]["B"]}
    assert a == b == {"57", "58", "60", "62"}
    for ra, rb in zip(pair["residues"]["A"], pair["residues"]["B"]):
        # stessa ΔSASA a meno dell'orientamento dei punti di Shrake-Rupley (non simmetrici per la rotazione C2)
        assert ra["resname"] == rb["resname"] and ra["delta"] == pytest.approx(rb["delta"], rel=0.1, abs=0.5)
    assert 0 < pair["area"] < interfaces.AREA_LOW
    assert pair["area"] == pytest.approx(pair["buried"] / 2, abs=0.1)
    assert pair["area"] == pytest.approx(123, abs=5)
    assert pair["plausibility"]["level"] == "bassa" and "cristallino" in pair["plausibility"]["text"]
    assert pair["counts"] == {"A": 4, "B": 4}
    assert sum(pair["composition"]["counts"].values()) == 8
    assert 0 <= pair["apolar_fraction"] <= 1


def test_dimer_residue_deltas(dimer):
    """ΔSASA mai negativa (aggiungere atomi copre solo punti) e > 1 Å² esattamente per i residui d'interfaccia."""
    _model, res = dimer
    assert len(res["residues"]) == 152
    assert all(r["delta"] >= 0 and r["sasa"] <= r["sasa_isolated"] + 1e-6 for r in res["residues"])
    flagged = {(r["chain"], r["resnum"]) for r in res["residues"] if r["delta"] > interfaces.INTERFACE_MIN_DELTA}
    assert flagged == {(c, n) for c in "AB" for n in ("57", "58", "60", "62")}
    assert res["interface_residues"] == 8


def test_dimer_crossing_interactions(dimer):
    model, res = dimer
    inter = interactions.analyze(model)
    crossing = res["pairs"][0]["interactions"]
    assert crossing["hbonds"] == inter["summary"]["inter_chain"]["hbonds"]
    assert crossing["salt_bridges"] == inter["summary"]["inter_chain"]["salt_bridges"]
    assert crossing["disulfides"] == 0


def test_single_chain_has_no_interface():
    res = interfaces.analyze(_load(DATA / "1UBQ.pdb", "1UBQ"))
    assert res["pairs"] == [] and res["residues"] == [] and res["chains"] == ["A"] and res["note"]


def test_waters_and_ions_do_not_bury_surface():
    """ubq_zn.pdb: lo ione e le due acque sono esclusi, quindi nessuna interfaccia."""
    res = interfaces.analyze(_load(DATA / "ubq_zn.pdb", "zn"))
    assert res["chains"] == ["A"] and res["pairs"] == []


def test_pairs_only_between_contacting_chains(tmp_path):
    """Due dimeri lontani 80 Å: solo le coppie A–B e C–D, ciascuna con l'area del dimero isolato."""
    single = interfaces.analyze(_load(DATA / "ubq_dimer.pdb", "dimer"))["pairs"][0]
    res = interfaces.analyze(_load(_assembly(tmp_path, 2), "two"))
    assert [p["chains"] for p in res["pairs"]] == [["A", "B"], ["C", "D"]]
    for p in res["pairs"]:
        assert p["area"] == pytest.approx(single["area"], rel=0.05)
        assert set(p["counts"].values()) == {4}


def test_third_chain_does_not_inflate_pair(tmp_path):
    """Con tre catene l'area di una coppia si misura sulle sole due catene: una terza catena lontana non la cambia."""
    lines = (DATA / "ubq_dimer.pdb").read_text(encoding="utf-8").splitlines()
    extra = [f"{ln[:21]}C{ln[22:30]}{float(ln[30:38]) + 90.0:8.3f}{ln[38:]}" for ln in lines
             if ln.startswith("ATOM") and ln[21] == "A"]
    path = tmp_path / "three.pdb"
    path.write_text("\n".join([ln for ln in lines if ln.startswith("ATOM")] + extra + ["END"]) + "\n",
                    encoding="utf-8")
    res = interfaces.analyze(_load(path, "three"))
    assert res["chains"] == ["A", "B", "C"] and [p["chains"] for p in res["pairs"]] == [["A", "B"]]


def test_plausibility_bands():
    assert interfaces.plausibility(399.9)["level"] == "bassa"
    assert interfaces.plausibility(400.0)["level"] == "incerta"
    assert interfaces.plausibility(599.9)["level"] == "incerta"
    assert interfaces.plausibility(600.0)["level"] == "compatibile"
    assert "EPPIC" in interfaces.plausibility(1000)["limits"]


def test_cancellation_between_sasa_steps(dimer):
    model, _res = dimer
    calls = []

    class Stop(Exception):
        pass

    def check():
        calls.append(1)
        if len(calls) == 2:
            raise Stop()

    with pytest.raises(Stop):
        interfaces.analyze(model, check=check)


def test_performance_about_5000_atoms(tmp_path):
    """Analisi completa (interazioni, interfacce, conservazione) di 8 catene, 4816 atomi: tempo misurato, limite
    largo (60 s; circa 2–3 s sul PC di sviluppo)."""
    model = _load(_assembly(tmp_path, 4), "big")
    atoms = sum(1 for _a in model.get_atoms())
    assert atoms == 4816
    rows = [HUMAN_UB] + [HUMAN_UB[:k] + "A" + HUMAN_UB[k + 1:] for k in range(0, 76, 4)]
    started = time.perf_counter()
    inter = interactions.analyze(model)
    iface = interfaces.analyze(model, inter)
    cons = conservation.compute(model, [f"s{k}" for k in range(len(rows))], rows)
    elapsed = time.perf_counter() - started
    print(f"\nanalisi strutturale di {atoms} atomi: {elapsed:.2f} s")
    assert elapsed < 60
    assert inter["summary"]["residues"] == 608 and inter["summary"]["salt_bridges"] == 4 * 6
    assert len(iface["pairs"]) == 4 and len(cons["chains"]) == 8
    assert np.isclose([p["area"] for p in iface["pairs"]], iface["pairs"][0]["area"], rtol=0.05).all()
