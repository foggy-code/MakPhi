"""QMEAN nei CSV della pipeline (metriche.csv, migliori.csv, /export.csv, riepilogo.csv): punteggi e componenti dei
job qmean sui modelli, solo se riguardano il file attuale."""

import csv
import io
import zipfile

import pytest

from bioloom.engines import remote
from bioloom.services import pipeline as pipeline_service

from .conftest import HUMAN_UB, YEAST_UB, until, wait_job
from .test_pipeline_cross import FakeModeller

REMOTE = {"qmean_mode": "remote", "qmean_remote": True, "contact_email": "a@b.it"}
COMPONENTS = {"qmean4_z_score": -1.2, "qmean6_z_score": -1.4, "interaction_z_score": -0.5, "cbeta_z_score": -0.8,
              "packing_z_score": -1.1, "torsion_z_score": 0.3, "ss_agreement_z_score": -0.2,
              "acc_agreement_z_score": 0.4}
SCORES = {"qmean4_z": -1.2, "qmean4": 0.71, "qmean6_z": -1.4, "qmeandisco": 0.68, "components": COMPONENTS,
          "local": [], "method": "QMEAN", "qmean_version": "4.3.0", "source": "swissmodel", "url": None}


@pytest.fixture
def setup(client, monkeypatch):
    core = client.app.state.core
    fake = FakeModeller()
    monkeypatch.setattr(core.compute, "run_modeling", fake)
    monkeypatch.setattr(remote, "swissmodel_qmean", lambda *a, **k: dict(SCORES))
    monkeypatch.delenv("BIOLOOM_QMEAN_URL", raising=False)
    client.put("/api/settings", json=REMOTE)
    return client


def _run(client, **extra) -> str:
    fasta = f">a\n{HUMAN_UB}\n>b\n{YEAST_UB}\n"
    body = {"fasta": fasta, "template_mode": "fixed", "options": {"models": 1}, "quality": True, "parallel": 2,
            "qmean": True, **extra}
    r = client.post("/api/pipeline", json=body)
    assert r.status_code == 200, r.text
    job_id = r.json()["id"]
    assert wait_job(client, job_id)["status"] == "completed"
    return job_id


def _qmean_done(client, n: int) -> None:
    def ready():
        jobs = client.get("/api/jobs?kind=qmean&limit=50").json()
        return len(jobs) == n and all(j["status"] == "completed" for j in jobs)
    until(ready)


def _table(text: str) -> list[dict]:
    return list(csv.DictReader(io.StringIO(text)))


def test_cross_metrics_have_qmean_of_the_best_models(setup):
    client = setup
    templates = [{"id": "T1", "chains": ["A"]}, {"id": "T2", "chains": ["A"]}, {"id": "T3", "chains": ["B"]}]
    job_id = _run(client, templates=templates, fixed_combination="each")
    _qmean_done(client, 2)  # QMEAN automatico: il migliore di ogni sequenza
    zf = zipfile.ZipFile(io.BytesIO(client.get(f"/api/pipeline/{job_id}/export.zip").content))
    table = _table(zf.read("metriche.csv").decode())
    assert list(table[0]) == pipeline_service.CROSS_FIELDS
    best = [r for r in table if r["best_in_sequence"] == "1"]
    assert [(r["sequence"], r["template"]) for r in best] == [("a", "T2_A"), ("b", "T3_B")]
    for r in best:
        assert (r["qmean4_z"], r["qmean4"], r["qmean6_z"], r["qmean_local_mean"]) == ("-1.2", "0.71", "-1.4", "0.68")
        assert (r["qmean_interaction_z"], r["qmean_cbeta_z"], r["qmean_packing_z"], r["qmean_torsion_z"],
                r["qmean_ss_agreement_z"], r["qmean_acc_agreement_z"]) == ("-0.5", "-0.8", "-1.1", "0.3", "-0.2", "0.4")
        assert r["qmean_method"] == "QMEAN" and r["qmean_source"] == "SWISS-MODEL"
    assert all(r["qmean4_z"] == "" for r in table if r["best_in_sequence"] == "0")  # non valutati
    assert [r["qmean4_z"] for r in _table(zf.read("migliori.csv").decode())] == ["-1.2", "-1.2"]
    route = _table(client.get(f"/api/pipeline/{job_id}/export.csv").text)
    assert sorted(r["qmean4_z"] for r in route) == ["", "", "", "", "-1.2", "-1.2"]
    assert "qmean_acc_agreement_z" in zf.read("LEGGIMI.txt").decode()


def test_summary_has_qmean_and_ignores_a_changed_model(setup):
    client = setup
    job_id = _run(client, templates=[{"id": "T1", "chains": ["A"]}])
    _qmean_done(client, 2)
    zf = zipfile.ZipFile(io.BytesIO(client.get(f"/api/pipeline/{job_id}/export.zip").content))
    rows = _table(zf.read("riepilogo.csv").decode())
    assert [r["qmean4_z"] for r in rows] == ["-1.2", "-1.2"] and rows[0]["qmean_cbeta_z"] == "-0.8"
    assert list(rows[0]).index("qmean4_z") < list(rows[0]).index("best_model")
    # modello cambiato dopo QMEAN (come una ripresa che rielabora l'elemento): il QMEAN vecchio non vale più
    core = client.app.state.core
    model = core.jobs.file_path(job_id, rows[0]["best_model"])
    model.write_text(model.read_text() + "REMARK 999 modificato\n")
    rows = _table(client.get(f"/api/pipeline/{job_id}/export.csv").text)
    assert [r["qmean4_z"] for r in rows] == ["", "-1.2"]
