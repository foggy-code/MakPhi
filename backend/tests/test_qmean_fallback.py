"""QMEAN: scelta della versione di UniClust30, fallback facoltativo su SWISS-MODEL, migrazione impostazioni."""

import json

import httpx
import pytest

from bioloom.config import UserSettings
from bioloom.engines import qmean_local, remote

from .conftest import wait_job
from .test_engines_extra import QMEAN_OUTPUT

REMOTE_RESULT = {"qmean4_z": -1.5, "qmean4": 0.6, "qmean6_z": None, "qmeandisco": 0.7, "components": {},
                 "local": [], "url": "https://swissmodel.expasy.org/qmean/x", "source": "swissmodel"}


class Worker:
    """Worker QMEAN simulato con due versioni del database; può far fallire il calcolo."""

    def __init__(self, fail=False):
        self.fail, self.requests = fail, []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append((request.method, request.url.path, dict(request.url.params),
                              json.loads(request.content) if request.content else None))
        path = request.url.path
        if path == "/status":
            prefer = request.url.params.get("prefer") or "UniRef30_2023_02/UniRef30_2023_02"
            ready = prefer != "old/uniclust30_2016_03"
            return httpx.Response(200, json={
                "uniclust": {"valid": ready, "prefix": prefer.rsplit("/", 1)[-1], "id": prefer,
                             "version": "2023-02", "error": None if ready else "la versione scelta non è pronta"},
                "selected": prefer if ready else None,
                "databases": [
                    {"id": "UniRef30_2023_02/UniRef30_2023_02", "name": "UniRef30_2023_02", "version": "2023-02",
                     "state": "ready", "format": "HH-suite 3", "hhm": True},
                    {"id": "old/uniclust30_2016_03", "name": "uniclust30_2016_03", "version": "2016-03",
                     "state": "needs_conversion", "format": "HH-suite 2", "hhm": True}],
                "archives": [], "unsupported": [], "qmtl": {"present": False}})
        if path in ("/jobs", "/convert") and request.method == "POST":
            return httpx.Response(202, json={"id": "j1", "status": "queued"})
        if path == "/jobs/j1" and request.method == "GET":
            if self.fail:
                return httpx.Response(200, json={"status": "failed", "error": "HHblits: memoria insufficiente"})
            return httpx.Response(200, json={"status": "completed", "result": {
                **QMEAN_OUTPUT, "database": {"state": "ready"},
                "bioloom_database": {"name": "UniRef30_2023_02", "version": "2023-02"}}})
        return httpx.Response(200, json={"status": "cancelled"})


@pytest.fixture
def worker(monkeypatch):
    def install(w):
        qmean_local._status_cache.update({"at": 0.0, "key": None, "value": None})
        monkeypatch.setenv("BIOLOOM_QMEAN_URL", "http://qmean:8080")
        monkeypatch.setattr(qmean_local, "_client", lambda: httpx.Client(
            base_url="http://qmean:8080", transport=httpx.MockTransport(w)))
        monkeypatch.setattr(qmean_local, "POLL_SECONDS", 0.01)
        return w
    return install


@pytest.fixture
def swissmodel(monkeypatch):
    calls = []

    def fake(pdb_text, email, check=None, log=print, sleep=None):
        calls.append(email)
        return dict(REMOTE_RESULT)

    monkeypatch.setattr(remote, "swissmodel_qmean", fake)
    return calls


def qmean(client, **extra):
    job = client.post("/api/qmean", json={"source": {"type": "template", "id": "1UBQ"}, **extra}).json()
    return wait_job(client, job["id"])


def test_old_auto_mode_is_migrated():
    s = UserSettings(qmean_mode="auto", qmean_remote=True)
    assert s.qmean_mode == "local" and s.qmean_fallback_remote is True
    assert UserSettings(qmean_mode="auto").qmean_fallback_remote is False
    assert UserSettings(qmean_mode="auto", qmean_remote=True, qmean_fallback_remote=False).qmean_fallback_remote is False


def test_local_uses_the_chosen_database_version(client_with_ubq, worker, swissmodel):
    w = worker(Worker())
    client_with_ubq.put("/api/settings", json={"qmean_uniclust": "UniRef30_2023_02/UniRef30_2023_02"})
    done = qmean(client_with_ubq)
    assert done["status"] == "completed", done["error"]
    assert done["result"]["source"] == "local" and done["result"]["uniclust"] == "UniRef30_2023_02"
    posted = next(body for method, path, _q, body in w.requests if method == "POST" and path == "/jobs")
    assert posted["database"] == "UniRef30_2023_02/UniRef30_2023_02"
    status = client_with_ubq.get("/api/databases").json()["qmean"]
    assert [d["name"] for d in status["databases"]] == ["UniRef30_2023_02", "uniclust30_2016_03"]
    assert not swissmodel


def test_failure_without_fallback_suggests_it(client_with_ubq, worker, swissmodel):
    worker(Worker(fail=True))
    done = qmean(client_with_ubq)
    assert done["status"] == "failed" and "HHblits" in done["error"] and "fallback" in done["error"]
    assert not swissmodel


def test_fallback_on_failure_when_enabled(client_with_ubq, worker, swissmodel):
    worker(Worker(fail=True))
    client_with_ubq.put("/api/settings", json={"qmean_fallback_remote": True, "qmean_remote": True,
                                               "contact_email": "ricerca@universita.it"})
    done = qmean(client_with_ubq)
    assert done["status"] == "completed", done["error"]
    assert done["result"]["source"] == "swissmodel" and "HHblits" in done["result"]["fallback"]["reason"]
    assert swissmodel == ["ricerca@universita.it"]


def test_fallback_when_local_service_is_missing_and_per_request_override(client_with_ubq, monkeypatch, swissmodel):
    monkeypatch.delenv("BIOLOOM_QMEAN_URL", raising=False)
    client_with_ubq.put("/api/settings", json={"qmean_remote": True, "contact_email": "a@b.it"})
    done = qmean(client_with_ubq)  # fallback spento nelle Impostazioni
    assert done["status"] == "failed" and not swissmodel
    done = qmean(client_with_ubq, fallback=True)  # attivato per questo calcolo
    assert done["status"] == "completed" and done["result"]["fallback"]["from"] == "local"
    assert "non attivo" in done["result"]["fallback"]["reason"]


def test_fallback_needs_consent_and_email(client_with_ubq, monkeypatch, swissmodel):
    monkeypatch.delenv("BIOLOOM_QMEAN_URL", raising=False)
    client_with_ubq.put("/api/settings", json={"qmean_fallback_remote": True})
    done = qmean(client_with_ubq)
    assert done["status"] == "failed" and "consenso" in done["error"] and not swissmodel


def test_remote_mode_goes_straight_to_swissmodel(client_with_ubq, worker, swissmodel):
    w = worker(Worker())
    client_with_ubq.put("/api/settings", json={"qmean_mode": "remote", "qmean_remote": True,
                                               "contact_email": "a@b.it"})
    done = qmean(client_with_ubq)
    assert done["status"] == "completed" and done["result"]["source"] == "swissmodel"
    assert "fallback" not in done["result"] and not any(p == "/jobs" for _m, p, _q, _b in w.requests)


def test_old_database_is_converted_through_a_job(client, worker):
    w = worker(Worker())
    job = client.post("/api/databases/uniclust/convert", json={"database": "old/uniclust30_2016_03"}).json()
    done = wait_job(client, job["id"])
    assert done["status"] == "completed", done["error"]
    assert ("POST", "/convert") in [(m, p) for m, p, _q, _b in w.requests]
    assert next(b for m, p, _q, b in w.requests if p == "/convert")["database"] == "old/uniclust30_2016_03"


class CopyWorker(Worker):
    """Worker che copia gli indici in locale riportando l'avanzamento."""

    def __init__(self):
        super().__init__()
        self.polls = 0

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/local":
            self.requests.append((request.method, path, {}, json.loads(request.content)))
            return httpx.Response(202, json={"id": "c1", "status": "queued"})
        if path == "/jobs/c1":
            self.polls += 1
            if self.polls < 3:
                return httpx.Response(200, json={"status": "running", "progress": 0.5,
                                                 "stage": "Copia di UniRef30_2023_02_cs219.ffdata: 3.2 di 6.4 GB"})
            return httpx.Response(200, json={"status": "completed", "result": {
                "database": "UniRef30_2023_02/UniRef30_2023_02", "seconds": 12,
                "local": {"state": "ready", "size": 7, "used": 7}}})
        return super().__call__(request)


def test_local_copy_of_the_indexes_through_a_job(client, worker):
    w = worker(CopyWorker())
    job = client.post("/api/databases/uniclust/local", json={"database": "UniRef30_2023_02/UniRef30_2023_02"}).json()
    done = wait_job(client, job["id"])
    assert done["status"] == "completed", done["error"]
    assert done["kind"] == "db_local_uniclust" and done["result"]["local"]["state"] == "ready"
    assert next(b for _m, p, _q, b in w.requests if p == "/local") == \
        {"database": "UniRef30_2023_02/UniRef30_2023_02", "action": "copy"}
    assert "Copia locale pronta in 12 s" in client.get(f"/api/jobs/{job['id']}/log").text
    assert client.post("/api/databases/uniclust/local", json={"database": "x", "action": "format"}).status_code == 422


class LowMemoryWorker(Worker):
    """Il database c'è ma la memoria di Docker non basta per HHblits."""

    def __call__(self, request: httpx.Request) -> httpx.Response:
        response = super().__call__(request)
        if request.url.path == "/status":
            data = response.json()
            data["uniclust"].update(memory="insufficient", memory_message="memoria insufficiente per UniRef30_2023_02")
            return httpx.Response(200, json=data)
        return response


def test_insufficient_memory_is_reported_and_uses_the_fallback(client_with_ubq, worker, swissmodel):
    w = worker(LowMemoryWorker())
    info = qmean_local.status(force=True)
    assert info["available"] is False and "memoria insufficiente" in info["message"]
    client_with_ubq.put("/api/settings", json={"qmean_remote": True, "contact_email": "a@b.it",
                                               "qmean_fallback_remote": True})
    done = qmean(client_with_ubq)
    assert done["status"] == "completed" and done["result"]["source"] == "swissmodel"
    assert "memoria insufficiente" in done["result"]["fallback"]["reason"]
    assert not any(p == "/jobs" for _m, p, _q, _b in w.requests)  # HHblits non parte nemmeno
