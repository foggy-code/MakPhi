"""Rifinitura energetica (F9.1): protocollo e copia pulita (domain/refinement.py), job `refine` con OpenMM e PDBFixer
reali su 1UBQ con una catena laterale ruotata, dinamica breve, interruzioni della catena, annullamento durante OpenMM e
degradazione senza OpenMM.

I test che eseguono OpenMM si saltano nelle immagini senza OpenMM o PDBFixer (bioloom:4.1.0)."""

from __future__ import annotations

import subprocess
import time

import numpy as np
import pytest

from bioloom.core.errors import ToolUnavailable
from bioloom.core.jobs import PRIORITY, JobManager
from bioloom.domain import clashes, refinement, structure
from bioloom.engines import refine as engine
from bioloom.engines import tools

from .conftest import DATA, wait_job

UBQ = {"type": "template", "id": "1UBQ"}
requires_openmm = pytest.mark.skipif(not (tools.find_module("openmm") and tools.find_module("pdbfixer")),
                                     reason="OpenMM o PDBFixer non installati in questa immagine")


def _model(path):
    return structure.first_model(structure.load_structure(path, "x"))


def _rotate_side_chain(src, dest, resnum: int, degrees: float) -> None:
    """Copia di `src` con la catena laterale del residuo ruotata attorno all'asse CA→CB (legami e angoli invariati:
    cambia solo χ1, come un rotamero sbagliato)."""
    st = structure.load_structure(src, "x")
    res = structure.first_model(st)["A"][resnum]
    axis_origin = res["CA"].coord.astype(float)
    k = res["CB"].coord.astype(float) - axis_origin
    k /= np.linalg.norm(k)
    t = np.radians(degrees)
    for atom in res:
        if atom.get_id() in ("N", "CA", "C", "O", "CB"):
            continue
        v = atom.coord.astype(float) - axis_origin
        rotated = v * np.cos(t) + np.cross(k, v) * np.sin(t) + k * np.dot(k, v) * (1 - np.cos(t))
        atom.set_coord((rotated + axis_origin).astype(np.float32))
    structure.write_pdb(st, dest)


def _upload(client, path, template_id: str) -> None:
    with open(path, "rb") as fh:
        r = client.post("/api/templates/upload", files={"file": (path.name, fh, "chemical/x-pdb")},
                        data={"template_id": template_id})
    assert r.status_code == 200, r.text


# ------------------------------------------------------------------------------------------ protocollo
def test_protocol_constants_and_units():
    assert refinement.restraint_k_openmm(10.0) == pytest.approx(4184.0)  # kcal/mol/Å² → kJ/mol/nm²
    assert refinement.md_steps(10.0) == 5000 and refinement.md_steps(0.5) == 250  # passo 2 fs
    assert refinement.validate_md(0) == 0.0 and refinement.validate_md(50) == 50.0
    for bad in (-1, 50.5):
        with pytest.raises(ValueError):
            refinement.validate_md(bad)
    p = refinement.protocol(10.0, keep_hydrogens=True)
    assert p["restraint"]["atoms"] == ["N", "CA", "C", "O"] and p["restraint"]["k_kcal_mol_A2"] == 10.0
    assert p["md"]["temperature_k"] == 300.0 and p["md"]["steps"] == 5000 and p["hydrogens_in_output"] is True
    assert refinement.protocol(0)["md"] is None and "OBC2" in p["solvent"] and "ff14SB" in p["forcefield"]


def test_clean_input_keeps_only_the_protein(tmp_path):
    text, info = refinement.clean_input(_model(DATA / "ubq_zn.pdb"))
    assert info["excluded"] == ["ZN A101"] and info["breaks"] == []  # le acque si tolgono senza avviso
    assert "HETATM" not in text and "HOH" not in text and " ZN " not in text
    _text, plain = refinement.clean_input(_model(DATA / "1UBQ.pdb"))
    assert plain["excluded"] == [] and plain["residues"] == 76 and plain["hydrogens_removed"] == 0


def test_clean_input_splits_chains_at_breaks(tmp_path):
    """Senza i residui 35–37 la catena si interrompe: un TER prima del 38, così OpenMM non lega 34 e 38."""
    lines = (DATA / "1UBQ.pdb").read_text().splitlines()
    gap = tmp_path / "gap.pdb"
    gap.write_text("\n".join(ln for ln in lines if not (ln.startswith("ATOM") and 35 <= int(ln[22:26]) <= 37)) + "\n")
    text, info = refinement.clean_input(_model(gap))
    assert info["breaks"] == [{"chain": "A", "after": "34", "before": "38"}]
    rows = text.splitlines()
    first_38 = next(i for i, ln in enumerate(rows) if ln.startswith("ATOM") and ln[22:26].strip() == "38")
    assert rows[first_38 - 1] == "TER" and sum(1 for ln in rows if ln.startswith("TER")) == 2
    assert refinement.split_chain_breaks(text, set()) == text


def test_compare_rows_and_backbone_rmsd(tmp_path):
    before = {"grade_score": 90.0, "rama_favored": 95.0, "rama_outliers": 1.0, "clashscore": 8.0, "dope": -8000.0,
              "zdope": None}
    after = {"grade_score": 95.0, "rama_favored": 95.02, "rama_outliers": 2.0, "clashscore": 1.5, "dope": -8200.0,
             "zdope": -1.2}
    rows = {r["key"]: r for r in refinement.compare(before, after)}
    assert rows["clashscore"]["verdict"] == "meglio" and rows["clashscore"]["delta"] == -6.5
    assert rows["rama_outliers"]["verdict"] == "peggio" and rows["rama_favored"]["verdict"] == "invariato"
    assert rows["dope"]["verdict"] == "meglio" and rows["zdope"]["verdict"] is None
    same = refinement.backbone_rmsd(_model(DATA / "1UBQ.pdb"), _model(DATA / "1UBQ.pdb"))
    assert same["atoms"] == 76 * 4 and same["rmsd"] == 0.0 and same["max_displacement"] == 0.0
    moved = tmp_path / "moved.pdb"
    st = structure.load_structure(DATA / "1UBQ.pdb", "x")
    structure.first_model(st)["A"][40]["CA"].set_coord(structure.first_model(st)["A"][40]["CA"].coord + [0.5, 0, 0])
    structure.write_pdb(st, moved)
    r = refinement.backbone_rmsd(_model(DATA / "1UBQ.pdb"), _model(moved))
    assert r["max_atom"] == "CA A40" and r["max_displacement"] == pytest.approx(0.5, abs=1e-3)
    assert r["rmsd_no_fit"] == pytest.approx(0.5 / np.sqrt(304), abs=1e-3)
    e = refinement.energy_change({"before": {"total": 100.0}, "final": {"total": -50.0}, "restraint_final": 1.0})
    assert e == {"before": 100.0, "after": -50.0, "delta": -150.0, "decreased": True, "restraint_final": 1.0,
                 "unit": "kJ/mol"}


# ------------------------------------------------------------------------------------------ rilevamento degli strumenti
def test_python_tools_are_detected_without_importing_them(monkeypatch):
    tools.configure(DATA / "inesistente")  # svuota la cache del rilevamento
    monkeypatch.setitem(tools.MODULES, "assente", {"module": "bioloom_modulo_inesistente", "label": "Assente",
                                                   "use": "prova"})
    assert tools.find_module("assente") is None
    monkeypatch.setitem(tools.MODULES, "json", {"module": "json", "label": "JSON", "use": "prova"})
    assert tools.find_module("json").endswith("__init__.py")
    status = tools.status()
    assert {"fpocket", "apbs", "pdb2pqr", "openmm", "pdbfixer"} <= status.keys()
    assert status["assente"] == {"label": "Assente", "use": "prova", "path": None, "available": False}
    assert tools.missing_message(["json"], "Prova") is None
    text = tools.missing_message(["assente", "json"], "Prova")
    assert text.startswith("Prova non disponibile: manca Assente in questa installazione.") and "Docker" in text
    monkeypatch.setattr(tools, "find", lambda key: None)
    assert "mancano fpocket, Assente" in tools.missing_message(["fpocket", "assente"], "Prova")
    tools.configure(DATA / "inesistente")


# ------------------------------------------------------------------------------------------ degradazione e rotte
def test_without_openmm_the_job_fails_with_a_clear_message(client_with_ubq, monkeypatch, ubq_path, tmp_path):
    monkeypatch.setattr(tools, "find_module", lambda key: None)
    with pytest.raises(ToolUnavailable) as err:
        engine.run(ubq_path, None, tmp_path, output="x.pdb")
    assert "OpenMM" in err.value.message and "PDBFixer" in err.value.message
    job = wait_job(client_with_ubq, client_with_ubq.post("/api/refine", json={"source": UBQ}).json()["id"])
    assert job["status"] == "failed" and "OpenMM" in job["error"]
    tools_info = client_with_ubq.get("/api/system/info").json()["tools"]
    assert {"openmm", "pdbfixer"} <= tools_info.keys()


def test_refine_route_validates_before_queueing(client_with_ubq):
    reg = client_with_ubq.app.state.core.jobs._handlers["refine"]
    assert PRIORITY["refine"] == 1 and reg.priority == 1 and reg.pool == "cpu"
    assert client_with_ubq.post("/api/refine", json={"source": UBQ, "md_ps": 51}).status_code == 422
    assert client_with_ubq.post("/api/refine", json={"source": UBQ, "md_ps": -1}).status_code == 422
    assert client_with_ubq.post("/api/refine", json={"source": {"type": "template", "id": "9ZZZ"}}).status_code == 404
    assert client_with_ubq.get("/api/jobs?kind=refine").json() == []


# ------------------------------------------------------------------------------------------ OpenMM reale
@requires_openmm
def test_refine_fixes_a_wrong_rotamer_of_1ubq(client, tmp_path):
    """1UBQ con χ1 di Ile30 (nucleo idrofobico) ruotato di 120°: dopo la rifinitura clashscore non peggiore (di solito
    molto migliore), RMSD della catena principale < 0,5 Å con le restrizioni, energia diminuita."""
    perturbed = tmp_path / "ubq_rot.pdb"
    _rotate_side_chain(DATA / "1UBQ.pdb", perturbed, 30, 120.0)
    clash_perturbed = clashes.analyze(_model(perturbed))["clashscore"]
    assert clash_perturbed > clashes.analyze(_model(DATA / "1UBQ.pdb"))["clashscore"]
    _upload(client, perturbed, "UBQROT")
    r = client.post("/api/refine", json={"source": {"type": "template", "id": "UBQROT"}})
    assert r.status_code == 200, r.text
    job = wait_job(client, r.json()["id"], timeout=900)
    assert job["status"] == "completed", job.get("error")
    res = job["result"]
    assert job["title"] == "Rifinitura · UBQROT" and res["refined_file"] == "UBQROT_refined.pdb"
    assert res["energy"]["decreased"] and res["energy"]["after"] < res["energy"]["before"]
    assert res["rmsd"]["atoms"] == 304 and res["rmsd"]["rmsd"] < 0.5
    q = res["quality"]
    assert q["after"]["clashscore"] <= q["before"]["clashscore"]
    assert q["before"]["clashscore"] == pytest.approx(clash_perturbed, abs=0.01)
    assert q["after"]["rama_favored"] >= q["before"]["rama_favored"] - 2.0
    rows = {row["key"]: row for row in res["comparison"]}
    assert rows["clashscore"]["verdict"] in ("meglio", "invariato")
    assert res["openmm"]["platform"] == "CPU" and res["openmm"]["threads"] >= 1
    assert res["added"]["hydrogens"] > 500 and res["protocol"]["md"] is None
    files = {f["path"] for f in job["files"]}
    assert {"UBQROT_refined.pdb", "UBQROT_refined.pdb.quality.json", "input.pdb", "refine_result.json"} <= files
    text = client.get(f"/api/jobs/{job['id']}/files/UBQROT_refined.pdb").text
    assert "REMARK 999 BIOLOOM REFINED FROM UBQROT OPENMM" in text and "BACKBONE RMSD" in text
    refined = _model(client.app.state.core.jobs.file_path(job["id"], "UBQROT_refined.pdb"))
    assert not any(a.element == "H" for a in refined.get_atoms())  # idrogeni non richiesti
    assert len([r for r in refined.get_residues() if structure.is_amino_residue(r)]) == 76
    report = client.get(f"/api/jobs/{job['id']}/quality/UBQROT_refined.pdb").json()
    assert report["clashes"]["clashscore"] == q["after"]["clashscore"]


@requires_openmm
def test_refine_with_short_dynamics_hydrogens_and_a_chain_break(client, tmp_path):
    lines = (DATA / "1UBQ.pdb").read_text().splitlines()
    gap = tmp_path / "ubq_gap.pdb"
    gap.write_text("\n".join(ln for ln in lines if not (ln.startswith("ATOM") and 35 <= int(ln[22:26]) <= 37)) + "\n")
    _upload(client, gap, "UBQGAP")
    job = wait_job(client, client.post("/api/refine", json={"source": {"type": "template", "id": "UBQGAP"},
                                                            "md_ps": 0.5, "hydrogens": True}).json()["id"], timeout=900)
    assert job["status"] == "completed", job.get("error")
    res = job["result"]
    assert res["protocol"]["md"]["steps"] == 250 and res["openmm"]["md_steps"] == 250
    assert "after_md" in res["energy"]["components"] and res["energy"]["decreased"]
    assert any("Catena interrotta" in w for w in res["warnings"])
    refined = _model(client.app.state.core.jobs.file_path(job["id"], "UBQGAP_refined.pdb"))
    assert any(a.element == "H" for a in refined.get_atoms())
    chain = refined["A"]
    # nessun residuo ricostruito e nessun legame fra 34 e 38: la distanza resta quella dell'interruzione
    assert 35 not in chain and chain[34]["C"] - chain[38]["N"] > 5.0
    assert res["rmsd"]["rmsd"] < 0.5


@requires_openmm
def test_cancelling_during_openmm_stops_the_process(client_with_ubq, monkeypatch):
    procs: list[subprocess.Popen] = []
    original = JobManager._register_process

    def register(self, job_id, proc):
        procs.append(proc)
        original(self, job_id, proc)

    monkeypatch.setattr(JobManager, "_register_process", register)
    jobs = client_with_ubq.app.state.core.jobs
    job_id = client_with_ubq.post("/api/refine", json={"source": UBQ, "md_ps": 50}).json()["id"]
    deadline = time.time() + 300
    while time.time() < deadline and "Dinamica" not in (jobs.get(job_id)["stage"] or ""):
        assert jobs.get(job_id)["status"] in ("queued", "running"), jobs.get(job_id).get("error")
        time.sleep(0.2)
    assert "Dinamica" in jobs.get(job_id)["stage"]
    worker = next(p for p in procs if any("refine_worker.py" in str(a) for a in p.args))
    assert worker.poll() is None
    t0 = time.time()
    jobs.cancel(job_id)
    done = wait_job(client_with_ubq, job_id)
    assert done["status"] == "cancelled" and time.time() - t0 < 10
    assert worker.poll() is not None
