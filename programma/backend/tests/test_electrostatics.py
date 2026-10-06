"""Elettrostatica (F7b): PQR reale di PDB2PQR su 1UBQ, griglia come psize, input di APBS, mappe OpenDX, superficie;
job `electrostatics` con PDB2PQR + APBS reali, annullamento durante APBS e degradazione senza gli strumenti.

I test che eseguono PDB2PQR e APBS si saltano nelle immagini senza i programmi (bioloom:4.1.0); gli altri usano il PQR
reale salvato in tests/data/pdb2pqr_1ubq_parse.pqr (PDB2PQR 3.7.1, PARSE, PROPKA a pH 7) e mappe sintetiche."""

from __future__ import annotations

import subprocess
import time

import numpy as np
import pytest

from bioloom.core.errors import ToolUnavailable
from bioloom.core.jobs import PRIORITY, JobManager, ProcessResult
from bioloom.domain import electrostatics as es
from bioloom.domain import structure
from bioloom.engines import electrostatics as engine
from bioloom.engines import tools

from .conftest import DATA, ubq_variant, wait_job

UBQ = {"type": "template", "id": "1UBQ"}
PQR = DATA / "pdb2pqr_1ubq_parse.pqr"
requires_apbs = pytest.mark.skipif(not (tools.find("pdb2pqr") and tools.find("apbs")),
                                   reason="PDB2PQR o APBS non installati in questa immagine")


def _dx_text(origin, delta, counts, fn) -> str:
    """Mappa OpenDX nel formato di APBS 3.4.1 (intestazione copiata da un file reale), valori fn(x, y, z)."""
    nx, ny, nz = counts
    values = [fn(origin[0] + i * delta[0], origin[1] + j * delta[1], origin[2] + k * delta[2])
              for i in range(nx) for j in range(ny) for k in range(nz)]
    rows = [" ".join(f"{v:e}" for v in values[n:n + 3]) + " " for n in range(0, len(values), 3)]
    return "\n".join([
        "# Data from APBS 3.4.1", "# ", "# POTENTIAL (kT/e)", "# ",
        f"object 1 class gridpositions counts {nx} {ny} {nz}",
        f"origin {origin[0]:e} {origin[1]:e} {origin[2]:e}",
        f"delta {delta[0]:e} 0.000000e+00 0.000000e+00",
        f"delta 0.000000e+00 {delta[1]:e} 0.000000e+00",
        f"delta 0.000000e+00 0.000000e+00 {delta[2]:e}",
        f"object 2 class gridconnections counts {nx} {ny} {nz}",
        f"object 3 class array type double rank 0 items {nx * ny * nz}         data follows",
        *rows,
        'attribute "dep" string "positions"',
        'object "regular positions regular connections"         class field',
        'component "positions" value 1', 'component "connections" value 2', 'component "data" value 3', "",
    ])


# ------------------------------------------------------------------------------------------ PQR reale
def test_pqr_of_1ubq_net_charge_is_zero_at_ph7():
    """Ubiquitina a pH 7: 7 Lys + 4 Arg + N-terminale contro 5 Asp + 6 Glu + C-terminale, His68 neutra (HD1, tautomero
    δ: PROPKA la dà con pKa sotto 7). Carica netta 0 esatta; il pI calcolato da PROPKA è 7,07 (ripiegata)."""
    atoms = es.parse_pqr(PQR.read_text())
    assert len(atoms) == 1231 and {a.chain for a in atoms} == {"A"}
    assert atoms[0].name == "N" and atoms[0].resname == "MET" and atoms[0].charge == -0.32 and atoms[0].radius == 2.0
    assert es.net_charge(atoms) == 0.0
    s = es.charge_summary(atoms)
    assert (s["positive"], s["negative"]) == (12, 12)
    assert s["by_type"] == {"ARG+": 4, "ASP−": 5, "C-term−": 1, "GLU−": 6, "LYS+": 7, "N-term+": 1}
    his = {a.name for a in atoms if a.resnum == "68"}
    assert "HD1" in his and "HE2" not in his
    charges = es.residue_charges(atoms)
    assert charges[("A", "48")] == 1.0 and charges[("A", "24")] == -1.0 and charges[("A", "68")] == 0.0


def test_pqr_parser_reads_whitespace_format_and_insertion_codes():
    fixed = "ATOM      5  CB  LYS B  52A     25.112  24.880   3.649  0.0000 2.0000"
    spaced = "ATOM 6 NZ LYS B 52A 25.1 24.8 3.6 1.0000 1.5000"
    short = "ATOM 7 O HOH 9 1.0 2.0 3.0 -0.8 1.4"
    atoms = es.parse_pqr("\n".join([fixed, spaced, short, "REMARK x"]))
    assert [(a.chain, a.resnum, a.name, a.charge, a.radius) for a in atoms] == [
        ("B", "52A", "CB", 0.0, 2.0), ("B", "52A", "NZ", 1.0, 1.5), ("", "9", "O", -0.8, 1.4)]


# ------------------------------------------------------------------------------------------ griglia e input APBS
def test_grid_follows_psize_on_1ubq():
    """Come psize.py: grossolana 1,7 × estensione, fine = estensione + 20 Å, punti 32k + 1 con passo ~0,5 Å; sono le
    dimensioni che APBS ha usato nella mappa reale (counts 97 97 129)."""
    g = es.grid_parameters(es.parse_pqr(PQR.read_text()))
    assert g["dime"] == [97, 97, 129]
    assert g["molecule"] == pytest.approx([33.084, 34.772, 39.417], abs=1e-3)
    assert g["cglen"] == pytest.approx([1.7 * m for m in g["molecule"]], abs=2e-3)
    assert g["fglen"] == pytest.approx([m + 20 for m in g["molecule"]], abs=2e-3)
    assert all(0.45 <= s <= 0.6 for s in g["spacing"]) and not g["coarsened"]
    assert g["memory_mb"] == pytest.approx(500 * 97 * 97 * 129 / 1024 / 1024, abs=0.1)  # misurati 586 MB


def test_grid_points_and_coarsening_for_large_molecules():
    assert es._grid_points(5, 0.5) == 33 and es._grid_points(59.4, 0.5) == 129 and es._grid_points(64, 0.5) == 129
    assert all((es._grid_points(n, 0.5) - 1) % 32 == 0 for n in range(10, 300, 7))
    # piccola: la griglia fine non supera la grossolana (estensione < 28,6 Å)
    small = es.grid_parameters([es.PqrAtom("A", "1", "ALA", "CA", (0.0, 0.0, 0.0), 0.0, 2.0)])
    assert small["fglen"] == small["cglen"] == pytest.approx([6.8, 6.8, 6.8])
    # grande (140 Å): il passo cresce finché la stima resta sotto il tetto
    big = [es.PqrAtom("A", str(i), "ALA", "CA", (x, x, x), 0.0, 2.0) for i, x in enumerate((0.0, 140.0))]
    g = es.grid_parameters(big)
    assert g["coarsened"] and g["memory_mb"] <= es.GMEMCEIL_MB and min(g["spacing"]) > 0.5


def test_apbs_input_declares_every_parameter():
    g = es.grid_parameters(es.parse_pqr(PQR.read_text()))
    text = es.apbs_input("structure.pqr", g)
    for line in ("mol pqr structure.pqr", "mg-auto", "dime 97 97 129", "cgcent mol 1", "fgcent mol 1", "lpbe",
                 "bcfl sdh", "ion charge 1 conc 0.150 radius 2.0", "ion charge -1 conc 0.150 radius 2.0",
                 "pdie 2.00", "sdie 78.54", "srfm smol", "chgm spl2", "srad 1.4", "swin 0.3", "temp 298.15",
                 "write pot dx potential"):
        assert line in text
    assert text.splitlines()[-1] == "quit"


# ------------------------------------------------------------------------------------------ mappe e superficie
def test_dx_parser_and_trilinear_interpolation_are_exact_on_a_linear_field():
    origin, delta, counts = (-2.0, -1.0, 0.5), (0.5, 0.25, 1.0), (5, 6, 4)
    grid = es.parse_dx(_dx_text(origin, delta, counts, lambda x, y, z: 2 * x - 3 * y + z + 1))
    assert grid.counts == counts and grid.values.shape == counts
    assert grid.origin.tolist() == list(origin) and grid.delta.tolist() == list(delta)
    pts = np.array([[-1.3, 0.1, 1.7], [0.0, 0.0, 0.5], [0.0, 0.25, 3.5], [10.0, 0.0, 1.0]])
    got = es.interpolate(grid, pts)
    assert got[:3] == pytest.approx([2 * x - 3 * y + z + 1 for x, y, z in pts[:3]], abs=1e-5)
    assert np.isnan(got[3])  # fuori dalla griglia


def test_dx_parser_rejects_incomplete_maps():
    text = _dx_text((0, 0, 0), (1, 1, 1), (3, 3, 3), lambda x, y, z: 0.0)
    with pytest.raises(ValueError):
        es.parse_dx(text.replace("items 27", "items 30").replace("counts 3 3 3", "counts 3 3 4", 1))
    with pytest.raises(ValueError):
        es.parse_dx("# vuoto\n")


def test_surface_points_follow_shrake_rupley():
    unit = es.sphere_points(100)
    assert unit.shape == (100, 3) and np.allclose(np.linalg.norm(unit, axis=1), 1.0)
    assert np.abs(unit.mean(axis=0)).max() < 0.02
    coords = np.array([[0.0, 0.0, 0.0], [1.0, 0.0, 0.0], [20.0, 0.0, 0.0], [0.3, 0.0, 0.0]])
    radii = np.array([1.7, 1.7, 1.7, 0.5])
    pts = es.accessible_points(coords, radii)
    assert len(pts[2]) == 100  # isolato: tutta la sfera
    assert 0 < len(pts[0]) < 100 and 0 < len(pts[1]) < 100  # due atomi sovrapposti si coprono a vicenda
    assert len(pts[3]) == 0  # piccolo atomo dentro gli altri: sepolto
    for p in pts[0]:  # i punti accessibili stanno sulla sfera allargata e fuori da quelle degli altri
        assert np.linalg.norm(p - coords[0]) == pytest.approx(1.7 + 1.4, abs=1e-6)
        assert np.linalg.norm(p - coords[1]) >= 1.7 + 1.4 - 1e-6


def test_residue_potentials_average_exposed_atoms_on_the_1ubq_surface():
    """Mappa sintetica φ = x sulle coordinate di 1UBQ: il valore di un residuo è la media degli atomi esposti, quindi
    cresce con la x del residuo; i residui sepolti non hanno valore."""
    atoms = es.parse_pqr(PQR.read_text())
    xyz = np.array([a.xyz for a in atoms])
    lo, hi = xyz.min(axis=0) - 6, xyz.max(axis=0) + 6
    counts = tuple(int(n) for n in np.ceil((hi - lo) / 1.0) + 1)
    grid = es.DxGrid(origin=lo, delta=np.ones(3), counts=counts,
                     values=np.broadcast_to((lo[0] + np.arange(counts[0]))[:, None, None], counts).copy())
    res = es.residue_potentials(atoms, grid)
    assert len(res) == 76
    buried = [k for k, v in res.items() if v["potential"] is None]
    assert buried and all(res[k]["exposed"] == 0 for k in buried)
    exposed = [(k, v) for k, v in res.items() if v["potential"] is not None]
    # media dei punti sulla superficie di ogni atomo esposto: dentro l'estensione degli atomi del residuo ± 3,4 Å
    for (_chain, resnum), v in exposed:
        xs = [a.xyz[0] for a in atoms if a.resnum == resnum]
        assert min(xs) - 3.6 <= v["potential"] <= max(xs) + 3.6
    s = es.summarize([{"potential": v["potential"]} for _k, v in exposed] + [{"potential": None}])
    assert s["residues"] == len(exposed) and s["min"] < s["mean"] < s["max"]


# ------------------------------------------------------------------------------------------ catene e residui modificati
class _FakeTools:
    """Contesto di job finto, per provare `engine.run` senza i programmi: «PDB2PQR» scrive il PQR reale di 1UBQ salvato
    (catena A: PDB2PQR con --keep-chain chiama A anche una catena senza identificativo), tenendo le righe per cui
    `keep(riga)` è vero; «APBS» una mappa φ = x / 10 che copre la molecola."""

    def __init__(self, keep=lambda line: True) -> None:
        self.keep = keep
        self.inputs: list[str] = []

    def sub(self, _name, _workdir):
        return self

    def log(self, _message) -> None:
        pass

    def run(self, cmd, *, cwd, **_kwargs) -> ProcessResult:
        if cmd[0] == "pdb2pqr":
            self.inputs.append((cwd / cmd[-2]).read_text())
            rows = [ln for ln in PQR.read_text().splitlines() if self.keep(ln)]
            (cwd / cmd[-1]).write_text("\n".join(rows) + "\n")
        else:
            xyz = np.array([a.xyz for a in es.parse_pqr((cwd / engine.PQR).read_text())])
            lo, hi = xyz.min(axis=0) - 8, xyz.max(axis=0) + 8
            counts = tuple(int(n) for n in np.ceil((hi - lo) / 2.0) + 1)
            (cwd / f"{engine.DX_STEM}.dx").write_text(_dx_text(tuple(lo), (2.0, 2.0, 2.0), counts,
                                                               lambda x, y, z: x / 10))
        return ProcessResult(0, "")


@pytest.fixture
def fake_tools(monkeypatch):
    monkeypatch.setattr(tools, "missing_message", lambda *_a, **_k: None)
    monkeypatch.setattr(tools, "find", lambda key: key)


def test_chain_without_identifier_keeps_charges_and_potentials(tmp_path, fake_tools):
    """Colonna 22 vuota: la copia dà alla catena una lettera libera (A), così le chiavi coincidono con quelle del PQR;
    il risultato torna alla catena originale. Prima nessun residuo aveva carica o potenziale."""
    src = ubq_variant(tmp_path / "blank.pdb", blank_chain=True)
    fake = _FakeTools()
    res = engine.run(src, fake, tmp_path / "work")
    assert {ln[21] for ln in fake.inputs[0].splitlines() if ln.startswith("ATOM")} == {"A"}
    rows = res["residues"]
    assert len(rows) == 76 and {r["chain"] for r in rows} == {" "}
    assert all(r["charge"] is not None for r in rows)
    assert sum(r["potential"] is not None for r in rows) > 60 and res["summary"]["residues"] > 60
    assert {c["chain"] for c in res["charges"]["charged"]} == {" "} and res["charges"]["positive"] == 12
    assert [c["chain"] for c in res["structure"]["chains_detail"]] == [" "]


def test_blank_chain_takes_a_letter_not_used_by_the_other_chains():
    """Catena A e catena senza identificativo: la seconda prende B, non la A già usata (le due catene si
    confonderebbero nel PQR)."""
    model = structure.first_model(structure.load_structure(DATA / "ubq_dimer.pdb", "d"))
    model["B"].id = " "
    assert structure.name_blank_chains(model) == {"B": " "}
    assert [c.id for c in model] == ["A", "B"] and len(model["B"]) == len(model["A"])
    assert structure.name_blank_chains(model) == {}  # già tutte con un nome


def test_modified_residues_are_kept_and_reported(tmp_path, fake_tools):
    """MSE (HETATM, come nei file di RCSB) entra nel calcolo come MET; SEP resta nella copia, ma PDB2PQR non ha i suoi
    parametri e la toglie dal PQR (qui simulato): un avviso proprio, non «gruppo eteroatomico escluso»."""
    src = ubq_variant(tmp_path / "mod.pdb", hetatm={1: "MSE", 20: "SEP"})
    fake = _FakeTools(keep=lambda ln: not (ln.startswith("ATOM") and ln[22:26].strip() == "20"))
    res = engine.run(src, fake, tmp_path / "work")
    sent = fake.inputs[0].splitlines()
    assert {ln[17:20] for ln in sent if ln[22:26].strip() == "1"} == {"MET"} and not any("MSE" in ln for ln in sent)
    rows = {r["resnum"]: r for r in res["residues"]}
    assert rows["1"]["resname"] == "MET" and rows["1"]["charge"] is not None and "20" not in rows
    assert not any("eteroatomici esclusi" in w for w in res["warnings"])
    assert [w for w in res["warnings"] if "SEP A20" in w and "PDB2PQR" in w]
    assert res["structure"]["residues"] == 75


# ------------------------------------------------------------------------------------------ degradazione e rotte
def test_engine_without_tools_explains_what_is_missing(monkeypatch, ubq_path, tmp_path):
    monkeypatch.setattr(tools, "find", lambda key: None)
    with pytest.raises(ToolUnavailable) as err:
        engine.run(ubq_path, None, tmp_path)
    assert "PDB2PQR" in err.value.message and "APBS" in err.value.message


def test_routes_validate_before_queueing(client_with_ubq, monkeypatch):
    reg = client_with_ubq.app.state.core.jobs._handlers["electrostatics"]
    assert PRIORITY["electrostatics"] == 0 and reg.priority == 0 and reg.pool == "cpu"
    for body in ({"source": UBQ, "forcefield": "CHARMM"}, {"source": UBQ, "ph": 15}, {"source": UBQ, "ph": -1}):
        assert client_with_ubq.post("/api/electrostatics", json=body).status_code == 422
    assert client_with_ubq.post("/api/electrostatics", json={"source": {"type": "upload", "file": "x.pdb"}}) \
        .status_code == 400
    monkeypatch.setattr(tools, "find", lambda key: None)
    job = wait_job(client_with_ubq, client_with_ubq.post("/api/electrostatics", json={"source": UBQ}).json()["id"])
    assert job["status"] == "failed" and "APBS" in job["error"]


# ------------------------------------------------------------------------------------------ PDB2PQR + APBS reali
@requires_apbs
def test_electrostatics_job_on_1ubq(client_with_ubq):
    r = client_with_ubq.post("/api/electrostatics", json={"source": UBQ})
    assert r.status_code == 200, r.text
    job = wait_job(client_with_ubq, r.json()["id"])
    assert job["status"] == "completed", job.get("error")
    res = job["result"]
    assert job["title"] == "Elettrostatica · 1UBQ" and res["forcefield"] == "PARSE" and res["ph"] == 7.0
    # carica netta dell'ubiquitina a pH 7: 0 (pI di PROPKA 7,07); tolleranza 1 per una His68 protonata
    assert abs(res["net_charge"]) <= 1.0
    assert res["grid"]["dime"] == [97, 97, 129] and res["parameters"]["sdie"] == 78.54
    assert res["scale"] == {"unit": "kT/e", "limit": 5.0, "mv_per_unit": 25.69}
    rows = {r["resnum"]: r for r in res["residues"]}
    assert len(rows) == 76 and rows["48"]["resname"] == "LYS" and rows["48"]["charge"] == 1.0
    lys = [rows[n]["potential"] for n in ("6", "11", "27", "29", "33", "48", "63") if rows[n]["potential"] is not None]
    glu = [rows[n]["potential"] for n in ("16", "18", "24", "34", "51", "64") if rows[n]["potential"] is not None]
    # segno coerente sulle cariche esposte: tutte le Lys positive, quasi tutte le Glu negative (Glu16 sta fra Lys29
    # e Lys33 e può risultare positiva), e le due medie ben separate
    assert len(lys) == 7 and all(v > 0 for v in lys)
    assert len(glu) == 6 and sum(1 for v in glu if v < 0) >= 5
    assert np.mean(lys) - np.mean(glu) > 1.5
    assert any(r["potential"] is None and r["exposed_atoms"] == 0 for r in res["residues"])  # sepolti
    files = {f["path"] for f in job["files"]}
    assert {"potential.dx", "structure.pqr", "apbs.in", "apbs.log", "pdb2pqr.log"} <= files
    assert "io.mc" not in files and "structure.log" not in files
    dl = client_with_ubq.get(f"/api/jobs/{job['id']}/files/potential.dx", params={"download": "true"})
    assert dl.status_code == 200 and "attachment" in dl.headers["content-disposition"]
    assert dl.text.startswith("# Data from APBS") and "POTENTIAL (kT/e)" in dl.text
    latest = client_with_ubq.get("/api/electrostatics/latest", params={"type": "template", "id": "1UBQ"}).json()
    assert latest["job_id"] == job["id"]


@requires_apbs
def test_amber_forcefield_gives_the_same_net_charge(client_with_ubq):
    job = wait_job(client_with_ubq, client_with_ubq.post(
        "/api/electrostatics", json={"source": UBQ, "forcefield": "AMBER"}).json()["id"])
    assert job["status"] == "completed", job.get("error")
    assert job["result"]["forcefield"] == "AMBER" and abs(job["result"]["net_charge"]) <= 1.0


@requires_apbs
def test_hetero_groups_are_excluded_with_a_warning(client):
    with open(DATA / "ubq_zn.pdb", "rb") as fh:
        r = client.post("/api/templates/upload", files={"file": ("ubq_zn.pdb", fh, "chemical/x-pdb")},
                        data={"template_id": "UBQZN"})
    assert r.status_code == 200, r.text
    job = wait_job(client, client.post("/api/electrostatics", json={"source": {"type": "template", "id": "UBQZN"}})
                   .json()["id"])
    assert job["status"] == "completed", job.get("error")
    assert any("ZN" in w and "PDB2PQR non ha parametri" in w for w in job["result"]["warnings"])
    assert abs(job["result"]["net_charge"]) <= 1.0  # la sola proteina, come 1UBQ


@requires_apbs
def test_real_tools_on_a_blank_chain_with_a_selenomethionine(client, tmp_path):
    """PDB2PQR e APBS reali su 1UBQ con la colonna 22 vuota e MET1 scritta come HETATM MSE: cariche e potenziali per
    residuo come per 1UBQ (prima tutti None), la MSE calcolata come metionina, nessun gruppo escluso."""
    src = ubq_variant(tmp_path / "ubq_blank_mse.pdb", hetatm={1: "MSE"}, blank_chain=True)
    with open(src, "rb") as fh:
        r = client.post("/api/templates/upload", files={"file": ("ubq_blank_mse.pdb", fh, "chemical/x-pdb")},
                        data={"template_id": "UBQBLANK"})
    assert r.status_code == 200, r.text
    job = wait_job(client, client.post("/api/electrostatics",
                                       json={"source": {"type": "template", "id": "UBQBLANK"}}).json()["id"])
    assert job["status"] == "completed", job.get("error")
    res = job["result"]
    rows = {r["resnum"]: r for r in res["residues"]}
    assert len(rows) == 76 and {r["chain"] for r in rows.values()} == {" "}
    assert all(r["charge"] is not None for r in rows.values()) and rows["48"]["charge"] == 1.0
    assert sum(r["potential"] is not None for r in rows.values()) > 60 and res["summary"]["residues"] > 60
    assert rows["1"]["resname"] == "MET" and rows["1"]["charge"] is not None
    assert res["warnings"] == [] and abs(res["net_charge"]) <= 1.0


@requires_apbs
def test_cancelling_during_apbs_stops_the_process(client_with_ubq, monkeypatch):
    procs: list[subprocess.Popen] = []
    original = JobManager._register_process

    def register(self, job_id, proc):
        procs.append(proc)
        original(self, job_id, proc)

    monkeypatch.setattr(JobManager, "_register_process", register)
    jobs = client_with_ubq.app.state.core.jobs
    job_id = client_with_ubq.post("/api/electrostatics", json={"source": UBQ}).json()["id"]
    apbs = None
    deadline = time.time() + 120
    while apbs is None and time.time() < deadline:
        apbs = next((p for p in procs if "apbs" in str(p.args[0])), None)
        time.sleep(0.01)
    assert apbs is not None, "APBS non è partito"
    t0 = time.time()
    jobs.cancel(job_id)
    done = wait_job(client_with_ubq, job_id)
    assert done["status"] == "cancelled" and time.time() - t0 < 10
    assert apbs.poll() is not None  # processo terminato
    assert all(p.poll() is not None for p in procs)
