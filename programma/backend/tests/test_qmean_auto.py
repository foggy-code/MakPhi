"""QMEAN automatico (services/autoqmean.py): job `qmean` accodati a modellazione o pipeline completata con l'opzione
`qmean`, ritrovati per struttura (`/api/qmean/latest`) e inclusi nel report; controlli preventivi."""

import pytest

from makphi.engines import remote
from makphi.services.autoqmean import AutoQmean, unavailable

from .conftest import until, wait_job
from .test_pipeline_cross import FakeModeller
from .test_notify import configure, smtp  # noqa: F401 (smtp: fixture)
from .test_preflight import codes, env, model_params, run  # noqa: F401 (env: fixture)
from .test_qmean_fallback import REMOTE_RESULT

REMOTE = {"qmean_mode": "remote", "qmean_remote": True, "contact_email": "a@b.it"}


@pytest.fixture
def swissmodel(monkeypatch):
    calls = []

    def fake(pdb_text, email, check=None, log=print, sleep=None):
        calls.append(pdb_text)
        return dict(REMOTE_RESULT)

    monkeypatch.setattr(remote, "swissmodel_qmean", fake)
    monkeypatch.delenv("MAKPHI_QMEAN_URL", raising=False)
    return calls


@pytest.fixture
def modeller(client, monkeypatch):
    """run_modeling simulato: un modello con il rapporto di qualità (come la pipeline M × N dei test)."""
    core = client.app.state.core
    fake = FakeModeller()

    def run_modeling(ctx, **kwargs):
        out = fake(ctx, **kwargs)
        return {**out, "best_model": out["models"][0]["file"]}

    monkeypatch.setattr(core.compute, "run_modeling", run_modeling)
    return core


def qmean_jobs(client) -> list[dict]:
    return client.get("/api/jobs?kind=qmean&limit=200").json()


def all_done(client, jobs: list[dict]) -> list[dict]:
    return [wait_job(client, j["id"]) for j in jobs]


def job_log(client, job_id: str) -> str:
    return client.get(f"/api/jobs/{job_id}/log").json()["text"]


def model_job(client, **extra) -> dict:
    body = {"target": {"name": "a", "sequence": "MQIFVKTLTGK"}, "templates": [{"id": "T1", "chains": ["A"]}],
            "options": {"models": 1}, **extra}
    r = client.post("/api/model", json=body)
    assert r.status_code == 200, r.text
    done = wait_job(client, r.json()["id"])
    assert done["status"] == "completed", done.get("error")
    return done


def test_unavailable_reasons(monkeypatch):
    from makphi.config import UserSettings

    monkeypatch.delenv("MAKPHI_QMEAN_URL", raising=False)
    assert "non è attivo" in unavailable(UserSettings())
    assert "non è consentito" in unavailable(UserSettings(qmean_mode="remote"))
    assert "email" in unavailable(UserSettings(qmean_mode="remote", qmean_remote=True))
    assert unavailable(UserSettings(**REMOTE)) is None
    assert unavailable(UserSettings(qmean_remote=True, contact_email="a@b.it", qmean_fallback_remote=True)) is None
    monkeypatch.setenv("MAKPHI_QMEAN_URL", "http://qmean:8080")
    assert unavailable(UserSettings()) is None


def test_model_job_queues_qmean_on_the_best_model(client, modeller, swissmodel):
    client.put("/api/settings", json=REMOTE)
    job = model_job(client, qmean=True)
    until(lambda: qmean_jobs(client))
    [q] = all_done(client, qmean_jobs(client))
    assert q["status"] == "completed", q.get("error")
    assert q["params"]["source"] == {"type": "job", "id": None, "job_id": job["id"], "file": "a.B99990001.pdb"}
    assert q["params"]["auto"] is True and q["title"] == "QMEAN · a.B99990001.pdb (automatico)"
    assert len(swissmodel) == 1
    until(lambda: "QMEAN automatico: 1 calcolo accodato." in job_log(client, job["id"]))
    # la pagina Qualità lo ritrova per struttura
    latest = client.get("/api/qmean/latest", params={"type": "job", "job_id": job["id"],
                                                     "file": "a.B99990001.pdb"}).json()
    assert latest["job_id"] == q["id"] and latest["active_job_id"] is None
    # il report del modello lo include senza indicarlo
    text = client.get(f"/api/jobs/{job['id']}/report/a.B99990001.pdb").text
    assert "SWISS-MODEL" in text and "QMEAN" in text
    # una seconda passata non accoda doppioni
    submitted, note = AutoQmean(modeller.jobs).submit_for(job["id"])
    assert submitted == 0 and "1 modello aveva già QMEAN" in note


def test_without_the_option_nothing_is_queued(client, modeller, swissmodel):
    client.put("/api/settings", json=REMOTE)
    job = model_job(client)
    assert AutoQmean(modeller.jobs).on_finish(job["id"], "completed") is None
    assert not qmean_jobs(client) and not swissmodel
    assert "QMEAN automatico" not in job_log(client, job["id"])


def test_unusable_qmean_is_logged_instead_of_failing_jobs(client, modeller, swissmodel):
    job = model_job(client, qmean=True)  # nessun servizio locale, SWISS-MODEL non consentito
    until(lambda: "QMEAN automatico non accodato" in job_log(client, job["id"]))
    assert "non è attivo" in job_log(client, job["id"])
    assert not qmean_jobs(client) and not swissmodel


def test_pipeline_queues_qmean_on_the_best_model_of_each_sequence(client, modeller, swissmodel):
    client.put("/api/settings", json=REMOTE)
    fasta = ">a\nMQIFVKTLTGK\n>b\nMQIFVKTLTGK\n"
    body = {"fasta": fasta, "template_mode": "fixed", "fixed_combination": "each", "options": {"models": 1},
            "templates": [{"id": "T1", "chains": ["A"]}, {"id": "T2", "chains": ["A"]}, {"id": "T3", "chains": ["B"]}],
            "quality": True, "parallel": 2, "qmean": True}
    r = client.post("/api/pipeline", json=body)
    assert r.status_code == 200, r.text
    job_id = r.json()["id"]
    assert wait_job(client, job_id)["status"] == "completed"
    until(lambda: len(qmean_jobs(client)) == 2)
    done = all_done(client, qmean_jobs(client))
    assert all(q["status"] == "completed" for q in done)
    # M × N: il migliore per zDOPE di ogni sequenza (a su T2, b su T3)
    files = sorted(q["params"]["source"]["file"] for q in done)
    assert files == ["items/0001_a__T2_A/a.B99990001.pdb", "items/0005_b__T3_B/b.B99990001.pdb"]
    assert sorted(q["title"] for q in done) == ["QMEAN · a__T2_A (automatico)", "QMEAN · b__T3_B (automatico)"]
    until(lambda: "QMEAN automatico: 2 calcoli accodati." in job_log(client, job_id))


def test_preflight_of_modeling_and_pipeline(client_with_ubq, env, monkeypatch):  # noqa: F811 (fixture)
    monkeypatch.delenv("MAKPHI_QMEAN_URL", raising=False)
    assert "qmean_auto" not in codes(run(client_with_ubq, "model", model_params()))
    out = run(client_with_ubq, "model", {**model_params(), "qmean": True})
    assert "qmean_auto" in codes(out) and "qmean_unavailable" in codes(out, "error")
    client_with_ubq.put("/api/settings", json=REMOTE)
    out = run(client_with_ubq, "model", {**model_params(), "qmean": True})
    assert "qmean_remote" in codes(out) and not codes(out, "error")
    fasta = ">a\nMQIFVKTLTGKTITLEVEPSDTIENVKAKIQDKEGIPPDQQRLIFAGKQLEDGRTLSDYNIQKESTLHLVLRLRGG\n" * 1
    pipe = run(client_with_ubq, "pipeline", {"fasta": fasta, "template_mode": "fixed",
                                             "templates": [{"id": "1UBQ", "chains": ["A"]}], "qmean": True})
    auto = next(c for c in pipe["checks"] if c["code"] == "qmean_auto")
    assert "fino a 1 calcolo" in auto["message"]
    pipe = run(client_with_ubq, "pipeline", {"fasta": fasta * 3, "template_mode": "fixed", "fixed_combination": "each",
                                             "templates": [{"id": "1UBQ", "chains": ["A"]}], "qmean": True,
                                             "qmean_scope": "all"})
    auto = next(c for c in pipe["checks"] if c["code"] == "qmean_auto")
    assert "su tutti i modelli (fino a 3 calcoli" in auto["message"]


# ------------------------------------------------------------------------------ tutte le coppie, ZIP, attesa, avviso
CROSS = {"fasta": ">a\nMQIFVKTLTGK\n>b\nMQIFVKTLTGK\n", "template_mode": "fixed", "fixed_combination": "each",
         "options": {"models": 1}, "quality": True, "parallel": 2, "qmean": True,
         "templates": [{"id": "T1", "chains": ["A"]}, {"id": "T2", "chains": ["A"]}, {"id": "T3", "chains": ["B"]}]}
BEST = ["items/0001_a__T2_A/a.B99990001.pdb", "items/0005_b__T3_B/b.B99990001.pdb"]


@pytest.fixture
def gate(monkeypatch):
    """SWISS-MODEL simulato che aspetta `open` prima di rispondere: i QMEAN restano in corso finché il test vuole."""
    import threading

    event = threading.Event()

    def fake(pdb_text, email, check=None, log=print, sleep=None):
        assert event.wait(60)
        return dict(REMOTE_RESULT)

    monkeypatch.setattr(remote, "swissmodel_qmean", fake)
    monkeypatch.delenv("MAKPHI_QMEAN_URL", raising=False)
    yield event
    event.set()


def pipeline(client, **extra) -> str:
    r = client.post("/api/pipeline", json={**CROSS, **extra})
    assert r.status_code == 200, r.text
    job_id = r.json()["id"]
    assert wait_job(client, job_id)["status"] == "completed"
    return job_id


def auto_status(client, job_id: str) -> dict:
    r = client.get(f"/api/jobs/{job_id}/qmean-auto")
    assert r.status_code == 200, r.text
    return r.json()


def test_pipeline_can_queue_qmean_on_every_pair_best_first(client, modeller, swissmodel):
    client.put("/api/settings", json=REMOTE)
    job_id = pipeline(client, qmean_scope="all")
    until(lambda: len(qmean_jobs(client)) == 6)
    done = all_done(client, qmean_jobs(client))
    assert all(q["status"] == "completed" for q in done)
    assert len({q["params"]["source"]["file"] for q in done}) == 6
    targets = AutoQmean(modeller.jobs)._targets(modeller.jobs.get(job_id))
    assert [file for file, _name in targets[:2]] == BEST  # prima i migliori di ogni sequenza
    until(lambda: "QMEAN automatico: 6 calcoli accodati." in job_log(client, job_id))


def test_status_and_zip_reports_include_qmean_once_finished(client, modeller, gate):
    import csv
    import io
    import zipfile

    client.put("/api/settings", json=REMOTE)
    job_id = pipeline(client)
    # QMEAN accodati ma fermi: lo ZIP non li ha ancora e la pagina lo sa
    until(lambda: auto_status(client, job_id)["total"] == 2)
    status = auto_status(client, job_id)
    assert status["requested"] and status["pending"] and status["done"] == 0
    gate.set()
    all_done(client, qmean_jobs(client))
    status = until(lambda: (s := auto_status(client, job_id)) and not s["pending"] and s)
    assert status == {"requested": True, "pending": False, "counts": {"completed": 2}, "total": 2, "done": 2}
    archive = zipfile.ZipFile(io.BytesIO(client.get(f"/api/pipeline/{job_id}/export.zip").content))
    reports = {n: archive.read(n).decode("utf-8") for n in archive.namelist() if n.endswith(".html")}
    assert len(reports) == 6
    with_qmean = sorted(n for n, text in reports.items() if "o meno: bassa qualità" in text)  # scheda QMEAN
    assert with_qmean == ["template/T2_A/a/a__T2_A.html", "template/T3_B/b/b__T3_B.html"]
    best = list(csv.DictReader(io.StringIO(archive.read("migliori.csv").decode("utf-8-sig"))))
    assert all(r["qmean4_z"] for r in best)
    # senza l'opzione: nulla da attendere
    plain = pipeline(client, qmean=False)
    assert auto_status(client, plain) == {"requested": False, "pending": False, "counts": {}, "total": 0, "done": 0}


def test_notice_waits_for_the_qmean_jobs(client, modeller, gate, smtp):  # noqa: F811 (fixture)
    configure(client, smtp)
    client.put("/api/settings", json=REMOTE)
    job_id = pipeline(client, notify_email="utente@example.org")
    until(lambda: auto_status(client, job_id)["total"] == 2)
    assert smtp.messages == []  # pipeline completata, QMEAN in corso: ancora nessun avviso
    gate.set()
    msg = until(lambda: smtp.messages)[0]
    assert msg["subject"].startswith("MakPhi: pipeline «") and msg["subject"].endswith("» completata")
    assert "QMEAN automatico: 2 calcoli (completati 2), tutti terminati." in msg["body"]
    assert all(q["status"] == "completed" for q in qmean_jobs(client))  # inviato dopo l'ultimo QMEAN
    until(lambda: "Email di avviso inviata a utente@example.org" in job_log(client, job_id))
    assert len(smtp.messages) == 1


def test_notice_without_usable_qmean_is_sent_at_once(client, modeller, swissmodel, smtp):  # noqa: F811 (fixture)
    configure(client, smtp)  # QMEAN non utilizzabile: nessun servizio locale, SWISS-MODEL non consentito
    job_id = pipeline(client, notify_email="utente@example.org")
    msg = until(lambda: smtp.messages)[0]
    assert "QMEAN automatico: nessun calcolo (il servizio QMEAN locale non è attivo" in msg["body"]
    assert not qmean_jobs(client) and not auto_status(client, job_id)["pending"]


def test_failed_qmean_are_requeued_on_request(client, modeller, monkeypatch):
    from makphi.core.errors import ToolUnavailable

    state = {"down": True}

    def fake(pdb_text, email, check=None, log=print, sleep=None):
        if state["down"]:  # servizio guasto: come il worker QMEAN bloccato sul server
            raise ToolUnavailable("servizio non raggiungibile")
        return dict(REMOTE_RESULT)

    monkeypatch.setattr(remote, "swissmodel_qmean", fake)
    monkeypatch.delenv("MAKPHI_QMEAN_URL", raising=False)
    client.put("/api/settings", json=REMOTE)
    job_id = pipeline(client, qmean_scope="all")
    until(lambda: (s := auto_status(client, job_id)) and not s["pending"] and s["counts"] == {"failed": 6})
    failed = {q["id"] for q in qmean_jobs(client)}
    state["down"] = False
    r = client.post(f"/api/jobs/{job_id}/qmean-auto/retry")
    assert r.status_code == 200 and r.json() == {"requeued": 6, "skipped": 0}
    status = until(lambda: (s := auto_status(client, job_id)) and not s["pending"] and s)
    assert status["counts"] == {"completed": 6} and status["total"] == 6  # gli stessi job, niente doppioni
    assert {q["id"] for q in qmean_jobs(client)} == failed
    assert all(q["params"]["auto"] for q in all_done(client, qmean_jobs(client)))
    until(lambda: "QMEAN automatico: 6 calcoli falliti rimessi in coda." in job_log(client, job_id))
    # nessun fallito rimasto: nulla da rimettere in coda
    assert client.post(f"/api/jobs/{job_id}/qmean-auto/retry").json() == {"requeued": 0, "skipped": 0}
    # un job senza QMEAN automatico rifiuta la richiesta
    plain = pipeline(client, qmean=False)
    r = client.post(f"/api/jobs/{plain}/qmean-auto/retry")
    assert r.status_code == 400 and r.json()["error"]["code"] == "qmean_auto_not_requested"


def test_a_model_with_a_valid_qmean_is_not_requeued(client, modeller, swissmodel):
    client.put("/api/settings", json=REMOTE)
    job_id = pipeline(client)
    done = all_done(client, until(lambda: len(qmean_jobs(client)) == 2 and qmean_jobs(client)))
    first = done[0]
    # un fallito più vecchio sullo stesso modello di un QMEAN completato (servizio guasto, poi rilanciato a mano)
    jobs = client.app.state.core.jobs
    jobs.db.execute("UPDATE jobs SET status='failed' WHERE id=?", (done[1]["id"],))
    copy = jobs.submit("qmean", {**first["params"]}, project_id=first.get("project_id"), title="copia")
    wait_job(client, copy["id"])
    jobs.db.execute("UPDATE jobs SET status='failed' WHERE id=?", (copy["id"],))
    out = AutoQmean(jobs).retry_failed(job_id)
    assert out == {"requeued": 1, "skipped": 1}  # il modello di `first` ha già il suo QMEAN completato
