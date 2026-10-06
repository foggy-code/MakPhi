"""Tasche con fpocket (F7b): lettura dell'uscita reale di 1UBQ, job `pockets`, rotte e degradazione senza fpocket.

I test che eseguono fpocket si saltano nelle immagini senza il programma (bioloom:4.1.0); quelli del parser usano
l'uscita reale salvata in tests/data/fpocket_1ubq_out (fpocket 4.2.3 su 1UBQ, sola proteina)."""

from __future__ import annotations

import pytest

from bioloom.core.errors import ToolUnavailable
from bioloom.core.jobs import PRIORITY
from bioloom.domain import pockets
from bioloom.engines import pockets as engine
from bioloom.engines import tools

from .conftest import DATA, ubq_variant, wait_job

OUT = DATA / "fpocket_1ubq_out"
UBQ = {"type": "template", "id": "1UBQ"}
requires_fpocket = pytest.mark.skipif(not tools.find("fpocket"), reason="fpocket non installato in questa immagine")


# ------------------------------------------------------------------------------------------ parser (uscita reale)
def test_parser_reads_the_real_fpocket_output_of_1ubq():
    out = pockets.read_output(OUT, "structure")
    ps = out["pockets"]
    # 4 tasche, ordinate per druggability: la 2 (0,535) è l'unica sopra 0,5 (Schmidtke & Barril 2010)
    assert [p["id"] for p in ps] == [2, 4, 1, 3] and [p["rank"] for p in ps] == [1, 2, 3, 4]
    assert [p["druggability"] for p in ps] == [0.535, 0.498, 0.032, 0.0]
    assert [p["druggable"] for p in ps] == [True, False, False, False]
    first = ps[0]
    assert first["score"] == 0.286 and first["volume"] == 323.734 and first["alpha_spheres"] == 19
    assert first["sasa_total"] == 88.206 and first["polarity_score"] == 5 and first["charge_score"] == 0
    assert first["hydrophobicity_score"] == 3.75 and first["polar_atoms_pct"] == 28.571
    # residui che rivestono la tasca, ordinati per catena e numero, senza ripetizioni
    assert [r["resname"] + r["resnum"] for r in first["residues"]] == [
        "MET1", "GLU18", "PRO19", "SER57", "ASN60", "ILE61", "GLN62", "LYS63"]
    assert all(r["chain"] == "A" for r in first["residues"]) and first["atoms"] > len(first["residues"])
    # una sfera alfa per ogni sfera dichiarata in info.txt; centro = baricentro dei centri
    for p in ps:
        assert len(p["spheres"]) == p["alpha_spheres"]
        assert all(3.4 - 1e-6 <= s["radius"] <= 6.2 + 1e-6 for s in p["spheres"])
        mean = [sum(s["center"][k] for s in p["spheres"]) / len(p["spheres"]) for k in range(3)]
        assert p["center"] == pytest.approx(mean, abs=1e-3)
    assert ps[0]["center"] == pytest.approx([20.917, 20.985, 6.172], abs=1e-3)
    assert out["summary"] == {"count": 4, "druggable": 1, "threshold": 0.5,
                              "best": {"id": 2, "druggability": 0.535, "volume": 323.734, "residues": 8}}


def test_info_parser_keeps_unknown_descriptors_and_ignores_text_outside_pockets():
    text = ("intestazione : ignorata\nPocket 7 :\n\tScore : \t0.5\n\tDruggability Score : \t0.9\n"
            "\tNumber of Alpha Spheres : \t20\n\tNuovo descrittore : \t1.5\n\tVolume : \tn/d\n")
    info = pockets.parse_info(text)
    assert list(info) == [7]
    assert info[7]["score"] == 0.5 and info[7]["druggability"] == 0.9 and info[7]["alpha_spheres"] == 20
    assert info[7]["other"] == {"Nuovo descrittore": 1.5} and "volume" not in info[7]


def test_lining_parser_deduplicates_residues_and_reads_insertion_codes():
    line = "ATOM    {:3d}  {:<3s} LYS A  52{}      1.000   2.000   3.000  0.00  0.00           C 0"
    text = "\n".join([line.format(1, "CA", " "), line.format(2, "CB", " "), line.format(3, "CA", "A"), "HEADER x"])
    residues, atoms = pockets.parse_lining(text)
    assert atoms == 3 and [r["resnum"] for r in residues] == ["52", "52A"]


def test_empty_output_gives_an_empty_summary(tmp_path):
    (tmp_path / "x_info.txt").write_text("", encoding="utf-8")
    out = pockets.read_output(tmp_path, "x")
    assert out["pockets"] == [] and out["summary"]["count"] == 0 and out["summary"]["best"] is None


# ------------------------------------------------------------------------------------------ copia pulita
def test_clean_copy_keeps_modified_residues_of_the_chain(tmp_path):
    """MSE e SEP scritte come HETATM (come nei file di RCSB) sono residui della catena, non ligandi: restano nella copia
    (toglierli lasciava nella proteina un buco che fpocket riportava come tasca) e non compaiono fra i gruppi esclusi;
    la selenometionina diventa metionina (ATOM MET, SE → SD), che PDB2PQR sa parametrizzare."""
    src = ubq_variant(tmp_path / "semet.pdb", hetatm={1: "MSE", 20: "SEP"})
    stats, warnings = engine.clean_copy(src, tmp_path / "clean.pdb")
    assert warnings == []
    by_res: dict[int, list[str]] = {}
    for ln in (tmp_path / "clean.pdb").read_text().splitlines():
        if ln.startswith(("ATOM", "HETATM")):
            by_res.setdefault(int(ln[22:26]), []).append(ln)
    assert sorted(by_res) == list(range(1, 77))
    assert all(ln.startswith("ATOM  ") and ln[17:20] == "MET" for ln in by_res[1])
    assert [ln[76:78] for ln in by_res[1] if ln[12:16] == " SD "] == [" S"]
    assert {ln[:6] + ln[17:20] for ln in by_res[20]} == {"HETATMSEP"}
    assert stats["residues"] == 75  # conteggi della copia: la MSE convertita conta, la SEP resta HETATM
    # un ligando vero resta fuori, con l'avviso
    _stats, zn = engine.clean_copy(DATA / "ubq_zn.pdb", tmp_path / "zn.pdb")
    assert len(zn) == 1 and "(ZN A101)" in zn[0] and "HETATM" not in (tmp_path / "zn.pdb").read_text()


# ------------------------------------------------------------------------------------------ degradazione
def test_engine_without_fpocket_explains_what_is_missing(monkeypatch, ubq_path, tmp_path):
    monkeypatch.setattr(tools, "find", lambda key: None)
    with pytest.raises(ToolUnavailable) as err:
        engine.run(ubq_path, None, tmp_path)  # il contesto non serve: si ferma prima
    assert "fpocket" in err.value.message and "immagine" in err.value.message


def test_pockets_job_fails_with_a_clear_message_without_fpocket(client_with_ubq, monkeypatch):
    real = tools.find
    monkeypatch.setattr(tools, "find", lambda key: None if key == "fpocket" else real(key))
    job = client_with_ubq.post("/api/pockets", json={"source": UBQ}).json()
    done = wait_job(client_with_ubq, job["id"])
    assert done["status"] == "failed" and "fpocket" in done["error"]
    info = client_with_ubq.get("/api/system/info").json()["tools"]
    assert info["fpocket"]["available"] is False  # l'interfaccia lo dice prima di proporre il calcolo


def test_registration_priority_and_validation(client):
    reg = client.app.state.core.jobs._handlers["pockets"]
    assert PRIORITY["pockets"] == 0 and reg.priority == 0 and reg.pool == "cpu"
    # struttura assente: errore subito, nessun job in coda
    r = client.post("/api/pockets", json={"source": {"type": "template", "id": "9ZZZ"}})
    assert r.status_code == 404
    r = client.post("/api/pockets", json={"source": {"type": "job"}})
    assert r.status_code == 400 and r.json()["error"]["code"] == "invalid_source"
    assert client.get("/api/jobs?kind=pockets").json() == []
    latest = client.get("/api/pockets/latest", params={"type": "template", "id": "1UBQ"}).json()
    assert latest == {"job_id": None, "finished_at": None, "active_job_id": None}


# ------------------------------------------------------------------------------------------ fpocket reale
@requires_fpocket
def test_pockets_job_on_1ubq(client_with_ubq):
    r = client_with_ubq.post("/api/pockets", json={"source": UBQ})
    assert r.status_code == 200, r.text
    job = wait_job(client_with_ubq, r.json()["id"])
    assert job["status"] == "completed", job.get("error")
    assert job["title"] == "Tasche · 1UBQ"
    res = job["result"]
    assert res["label"] == "1UBQ" and len(res["source_sha256"]) == 64 and res["warnings"] == []
    ps = res["pockets"]
    assert len(ps) >= 1 and res["summary"]["count"] == len(ps)
    drugg = [p["druggability"] for p in ps]
    assert drugg == sorted(drugg, reverse=True) and all(0.0 <= d <= 1.0 for d in drugg)
    for p in ps:
        assert p["residues"] and p["center"] and len(p["spheres"]) == p["alpha_spheres"] >= 15
        assert p["volume"] > 0
    # stesse tasche dell'uscita salvata (il volume di fpocket è stimato con Monte Carlo: non si confronta)
    saved = pockets.read_output(OUT, "structure")["pockets"]
    assert {tuple(r["resnum"] for r in p["residues"]) for p in ps} == \
        {tuple(r["resnum"] for r in p["residues"]) for p in saved}
    files = {f["path"] for f in job["files"]}
    assert {"structure.pdb", "structure_out/structure_info.txt", "structure_out/structure_out.pdb"} <= files
    assert res["pdb"] == "structure_out/structure_out.pdb" and "gmon.out" not in files
    latest = client_with_ubq.get("/api/pockets/latest", params={"type": "template", "id": "1UBQ"}).json()
    assert latest["job_id"] == job["id"] and latest["active_job_id"] is None
    log = client_with_ubq.get(f"/api/jobs/{job['id']}/log").json()["text"]
    assert "fpocket:" in log and "tasche" in log


@requires_fpocket
def test_pockets_exclude_hetero_groups_with_a_warning(client):
    with open(DATA / "ubq_zn.pdb", "rb") as fh:
        r = client.post("/api/templates/upload", files={"file": ("ubq_zn.pdb", fh, "chemical/x-pdb")},
                        data={"template_id": "UBQZN"})
    assert r.status_code == 200, r.text
    job = wait_job(client, client.post("/api/pockets", json={"source": {"type": "template", "id": "UBQZN"}})
                   .json()["id"])
    assert job["status"] == "completed", job.get("error")
    assert any("eteroatomici esclusi" in w and "ZN" in w for w in job["result"]["warnings"])
    clean = (client.app.state.core.jobs.file_path(job["id"], "structure.pdb")).read_text()
    assert "HETATM" not in clean


@requires_fpocket
def test_pockets_keep_a_selenomethionine_in_the_chain(client, tmp_path):
    """1UBQ con MET1 scritta come HETATM MSE: 76 residui analizzati e nessun avviso di gruppi esclusi."""
    src = ubq_variant(tmp_path / "ubq_mse.pdb", hetatm={1: "MSE"})
    with open(src, "rb") as fh:
        r = client.post("/api/templates/upload", files={"file": ("ubq_mse.pdb", fh, "chemical/x-pdb")},
                        data={"template_id": "UBQMSE"})
    assert r.status_code == 200, r.text
    job = wait_job(client, client.post("/api/pockets", json={"source": {"type": "template", "id": "UBQMSE"}})
                   .json()["id"])
    assert job["status"] == "completed", job.get("error")
    res = job["result"]
    assert res["warnings"] == [] and res["structure"]["residues"] == 76
    clean = client.app.state.core.jobs.file_path(job["id"], "structure.pdb").read_text()
    assert "MSE" not in clean and clean.count(" CA ") == 76
