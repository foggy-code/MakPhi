"""ZIP delle pipeline in background (services/pipeline_export.py) e ricerca per struttura (services/compute.job_sources).

Sul server lo ZIP di una pipeline di 139.296 coppie, generato dentro la richiesta, avrebbe impiegato circa 5 giorni
(4 ricerche nel database per report) e i report non avevano il QMEAN: la ricerca guardava solo i 200 QMEAN più recenti
di tutto MakPhi."""

import io
import json
import zipfile

import pytest

from makphi.core import db as db_module
from makphi.services import reports as reports_service
from makphi.services.pipeline_export import ItemContext, ZipBuilder
from makphi.services.reports import ReportService

from .conftest import until, wait_job
from .test_qmean_auto import REMOTE, all_done, modeller, pipeline, qmean_jobs, swissmodel  # noqa: F401 (fixture)


def _with_qmean(client) -> str:
    client.put("/api/settings", json=REMOTE)
    job_id = pipeline(client, qmean_scope="all")
    assert all(q["status"] == "completed" for q in all_done(client, until(lambda: len(qmean_jobs(client)) == 6
                                                                         and qmean_jobs(client))))
    return job_id


def _names(content: bytes) -> dict[str, bytes]:
    with zipfile.ZipFile(io.BytesIO(content)) as zf:
        return {n: zf.read(n) for n in zf.namelist()}


def test_detached_report_is_the_same_as_the_page_report(client, modeller, swissmodel, monkeypatch):  # noqa: F811
    job_id = _with_qmean(client)
    core = client.app.state.core
    monkeypatch.setattr(reports_service, "now_iso", lambda: "2026-10-26T00:00:00+00:00")
    job = core.jobs.get(job_id)
    row = core.db.query_one("SELECT idx, name, sequence, status, template, template_source, identity, coverage, "
                            "evalue, twilight, best_model, dope, ga341, grade, grade_score, rama_favored, clashscore, "
                            "metrics, error, started_at, finished_at FROM pipeline_items WHERE job_id=? AND idx=1",
                            (job_id,))
    service = ReportService(core.jobs, core.compute, core.db)
    before = service.pipeline_item(job, row, qmean=True)
    found = core.pipeline.qmean_jobs_by_file(job_id)[row["best_model"]]
    after, info = ReportService(core.jobs, core.compute, core.db).pipeline_item_detached(
        job, row, ItemContext(project="Predefinito" if job.get("project_id") else None, qmean=found))
    assert info["qmean"] and "o meno: bassa qualità" in after  # scheda QMEAN
    project = core.db.scalar("SELECT name FROM projects WHERE id=?", (job["project_id"],))
    after, _info = ReportService(core.jobs, core.compute, core.db).pipeline_item_detached(
        job, row, ItemContext(project=project, qmean=found))
    assert after == before


def test_zip_is_prepared_in_background_once(client, modeller, swissmodel):  # noqa: F811
    job_id = _with_qmean(client)
    direct = _names(client.get(f"/api/pipeline/{job_id}/export.zip").content)
    assert client.get(f"/api/pipeline/{job_id}/export").json() is None
    first = client.post(f"/api/pipeline/{job_id}/export").json()
    assert first["kind"] == "pipeline_export" and first["title"].startswith("ZIP · ")
    done = wait_job(client, first["id"])
    assert done["status"] == "completed", done.get("error")
    result = done["result"]
    assert result["file"] == f"pipeline_{job_id}.zip" and result["models"] == 6 and result["reports"] == 6
    assert result["qmean"] == 6 and result["missing"] == 0 and result["size"] > 0
    r = client.get(f"/api/jobs/{first['id']}/files/{result['file']}", params={"download": "true"})
    assert r.status_code == 200
    background = _names(r.content)
    assert background.keys() == direct.keys()
    for name in ("metriche.csv", "migliori.csv", "parametri.json", "LEGGIMI.txt"):
        assert background[name] == direct[name], name
    assert all("o meno: bassa qualità" in background[n].decode() for n in background if n.endswith(".html"))
    assert client.get(f"/api/pipeline/{job_id}/export").json()["id"] == first["id"]
    log = client.get(f"/api/jobs/{first['id']}/log").json()["text"]
    assert "Archivio pronto: 6 modelli, 6 report (6 con QMEAN)" in log


def test_a_second_request_returns_the_running_export(client, modeller, swissmodel, monkeypatch):  # noqa: F811
    import threading

    job_id = _with_qmean(client)
    gate = threading.Event()
    original = ZipBuilder.run

    def slow(self):
        assert gate.wait(30)
        return original(self)

    monkeypatch.setattr(ZipBuilder, "run", slow)
    first = client.post(f"/api/pipeline/{job_id}/export").json()
    second = client.post(f"/api/pipeline/{job_id}/export").json()
    assert second["id"] == first["id"]
    gate.set()
    assert wait_job(client, first["id"])["status"] == "completed"
    third = client.post(f"/api/pipeline/{job_id}/export").json()  # finito: uno nuovo
    assert third["id"] != first["id"]
    wait_job(client, third["id"])


def test_parallel_reports_give_the_same_archive(client, modeller, swissmodel, tmp_path, monkeypatch):  # noqa: F811
    job_id = _with_qmean(client)
    core = client.app.state.core
    one = ZipBuilder(core.pipeline, job_id, tmp_path / "uno.zip").run()
    many = ZipBuilder(core.pipeline, job_id, tmp_path / "due.zip", workers=2).run()
    assert {k: v for k, v in one.items() if k != "size"} == {k: v for k, v in many.items() if k != "size"}
    a, b = _names((tmp_path / "uno.zip").read_bytes()), _names((tmp_path / "due.zip").read_bytes())
    assert a.keys() == b.keys()
    strip = lambda text: text.split(b"generated", 1)[0]  # noqa: E731 (data di generazione a parte)
    for name in a:
        if name.endswith(".html"):
            assert strip(a[name]) == strip(b[name]), name
        else:
            assert a[name] == b[name], name


def test_export_needs_a_finished_pipeline(client, modeller, swissmodel):  # noqa: F811
    job_id = _with_qmean(client)
    qmean = qmean_jobs(client)[0]["id"]
    r = client.post(f"/api/pipeline/{qmean}/export")
    assert r.status_code == 400 and r.json()["error"]["code"] == "export_unsupported"
    assert client.post(f"/api/pipeline/{job_id}/export").status_code == 200


def test_qmean_is_found_among_many_newer_ones(client, modeller, swissmodel):  # noqa: F811
    job_id = _with_qmean(client)
    core = client.app.state.core
    first = core.db.query_one("SELECT best_model FROM pipeline_items WHERE job_id=? AND idx=0", (job_id,))["best_model"]
    mine = client.get("/api/qmean/latest", params={"type": "job", "job_id": job_id, "file": first}).json()
    assert mine["job_id"]
    # 250 QMEAN più recenti su altre strutture: prima la ricerca ne guardava solo 200 e non lo trovava più
    params = json.dumps({"source": {"type": "template", "id": "1UBQ"}, "auto": False})
    core.db.executemany(
        "INSERT INTO jobs(id, kind, title, status, params, result, created_at, finished_at, updated_at) "
        "VALUES (?, 'qmean', 'altro', 'completed', ?, '{}', '2099-01-01', '2099-01-01', '2099-01-01')",
        [(f"altro{i:04d}", params) for i in range(250)])
    again = client.get("/api/qmean/latest", params={"type": "job", "job_id": job_id, "file": first}).json()
    assert again["job_id"] == mine["job_id"]


def test_lookup_indexes_are_created_and_optional(tmp_path, monkeypatch):
    database = db_module.Database(tmp_path / "a.db")
    names = {r["name"] for r in database.query("SELECT name FROM sqlite_master WHERE type='index'")}
    assert {"idx_jobs_kind", "idx_jobs_source"} <= names
    plan = " ".join(str(r["detail"]) for r in database.query(
        "EXPLAIN QUERY PLAN SELECT id FROM jobs WHERE kind IN ('qmean') AND status='completed' AND "
        "json_extract(params, '$.source.job_id')='x' AND json_extract(params, '$.source.file') IN ('f')"))
    assert "idx_jobs_source" in plan
    database.close()
    # un indice che non si può creare (SQLite senza funzioni JSON) non impedisce l'avvio
    monkeypatch.setattr(db_module, "OPTIONAL_INDEXES", ("CREATE INDEX nope ON jobs(funzione_inesistente(params))",))
    db_module.Database(tmp_path / "b.db").close()


@pytest.mark.parametrize("workers", [0])
def test_cancelled_export_stops(client, modeller, swissmodel, workers):  # noqa: F811
    job_id = _with_qmean(client)
    core = client.app.state.core
    calls = []

    def check():
        calls.append(1)
        if len(calls) > 2:
            from makphi.core.jobs import JobCancelled
            raise JobCancelled()

    from makphi.core.jobs import JobCancelled
    with pytest.raises(JobCancelled):
        ZipBuilder(core.pipeline, job_id, core.paths.scratch_tmp / "annullato.zip", workers=workers,
                   check=check).run()
