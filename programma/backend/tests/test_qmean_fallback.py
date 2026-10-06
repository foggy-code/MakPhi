"""QMEAN: scelta della versione di UniClust30, fallback facoltativo su SWISS-MODEL, migrazione impostazioni,
memoria richiesta dal database, un calcolo locale alla volta."""

import json
import threading
import time

import httpx
import pytest

from bioloom.config import UserSettings
from bioloom.core.jobs import QMEAN_LOCAL, RESOURCE_STAGE
from bioloom.engines import qmean_local, remote

from .conftest import wait_job
from .test_engines_extra import QMEAN_OUTPUT

GIB = 1024 ** 3

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


# ------------------------------------------------------------------------------------------ memoria richiesta
def test_memory_requirement_measured_and_estimated():
    measured = qmean_local.memory_requirement({"prefix": "UniRef30_2020_06", "size": 194 * GIB})
    assert measured == {"bytes": 12 * GIB, "basis": "measured"}  # README: circa 12 GB assegnati a Docker
    # versione non misurata: 12 GB × 250/194 × 1,5 = 23,2 GB, arrotondati al GB superiore
    assert qmean_local.memory_requirement({"prefix": "UniRef30_2023_02", "size": 250 * GIB}) == \
        {"bytes": 24 * GIB, "basis": "estimate"}
    # stessa dimensione di UniRef30_2020_06 ma non misurata: resta il margine del 50%
    assert qmean_local.memory_requirement({"prefix": "UniRef30_2021_03", "size": 194 * GIB})["bytes"] == 18 * GIB
    assert qmean_local.memory_requirement({"prefix": "mini", "size": 5 * 1024 ** 2})["bytes"] == 2 * GIB  # minimo
    assert qmean_local.memory_requirement({"prefix": "senza_dimensione"}) is None
    assert qmean_local.memory_requirement(None) is None


def test_memory_problem_rules():
    ref = {"prefix": "UniRef30_2020_06", "size": 194 * GIB}
    # il giudizio del worker sulle dimensioni vere degli indici vale per primo
    assert qmean_local.memory_problem({**ref, "memory": "ok"}, {"total": 4 * GIB, "swap": 0}) is None
    assert qmean_local.memory_problem({**ref, "memory": "insufficient", "memory_message": "memoria insufficiente X"},
                                      None) == "memoria insufficiente X"
    low = qmean_local.memory_problem({**ref, "memory": "swap"}, {"total": 8 * GIB, "swap": 2 * GIB})
    assert "memoria insufficiente per UniRef30_2020_06" in low and "circa 12 GB" in low
    assert "valore misurato" in low and "ne ha 8,0" in low and "SWISS-MODEL" in low
    # 12 GB assegnati a Docker: il container ne vede un po' meno (circa 11,7 GB) e il requisito è rispettato
    assert qmean_local.memory_problem({**ref, "memory": "swap"}, {"total": int(11.7 * GIB), "swap": 0}) is None
    assert qmean_local.memory_problem({**ref, "memory": "swap"}, {"total": int(11.3 * GIB), "swap": 0}) is not None
    assert qmean_local.memory_problem({**ref, "memory": "swap"}, None) is None  # worker che non misura: nessun controllo
    estimate = qmean_local.memory_problem({"prefix": "UniRef30_2023_02", "size": 250 * GIB, "memory": "swap"},
                                          {"total": 16 * GIB, "swap": 0})
    assert "circa 24 GB" in estimate and "stima dalla dimensione del database, 250 GB" in estimate


def test_memory_sufficient_is_the_outcome_of_the_check():
    """L'esito mostrato accanto al requisito è quello del controllo prima del calcolo, giudizio del worker compreso."""
    ref = {"prefix": "UniRef30_2020_06", "size": 194 * GIB}
    newer = {"prefix": "UniRef30_2023_02", "size": 250 * GIB}  # stima 24 GB
    sufficient = qmean_local.memory_sufficient
    assert sufficient({**ref, "memory": "swap"}, {"total": int(11.7 * GIB), "swap": 0}) is True  # entro il 95%
    assert sufficient({**ref, "memory": "swap"}, {"total": 8 * GIB, "swap": 0}) is False
    assert sufficient({**ref, "memory": "insufficient"}, {"total": 64 * GIB, "swap": 0}) is False
    # stima più alta della memoria, ma il worker (dimensioni vere degli indici) dice "ok": parte, ed è sufficiente
    assert qmean_local.memory_problem({**newer, "memory": "ok"}, {"total": 16 * GIB, "swap": 0}) is None
    assert sufficient({**newer, "memory": "ok"}, {"total": 16 * GIB, "swap": 0}) is True
    assert qmean_local._requirement({**newer, "memory": "ok"}, {"total": 16 * GIB, "swap": 0}) == \
        {"bytes": 24 * GIB, "basis": "estimate", "sufficient": True}
    # worker che non misura la memoria o database senza dimensione: non si sa
    assert sufficient({**ref, "memory": None}, None) is None
    assert sufficient({"prefix": "x"}, {"total": 8 * GIB, "swap": 0}) is None
    assert qmean_local._requirement({"prefix": "x"}, None) is None


class SmallDockerWorker(Worker):
    """UniRef30_2020_06 con poca memoria per Docker: il worker lo farebbe partire (memoria al limite)."""

    def __init__(self, total: float):
        super().__init__()
        self.total = total

    def __call__(self, request: httpx.Request) -> httpx.Response:
        response = super().__call__(request)
        if request.url.path == "/status":
            data = response.json()
            data["uniclust"].update(prefix="UniRef30_2020_06", id="UniRef30_2020_06/UniRef30_2020_06",
                                    size=194 * GIB, memory="swap",
                                    memory_message="memoria al limite per UniRef30_2020_06")
            data["memory"] = {"total": int(self.total), "swap": 2 * GIB}
            return httpx.Response(200, json=data)
        return response


def test_local_qmean_fails_at_once_when_memory_is_below_the_requirement(client_with_ubq, worker, swissmodel):
    w = worker(SmallDockerWorker(8 * GIB))
    info = qmean_local.status(force=True)
    assert info["available"] is False and "circa 12 GB" in info["message"]
    assert info["memory_required"] == {"bytes": 12 * GIB, "basis": "measured", "sufficient": False}
    t0 = time.monotonic()
    done = qmean(client_with_ubq)
    assert done["status"] == "failed" and time.monotonic() - t0 < 10  # subito, non dopo il timeout
    assert "memoria insufficiente per UniRef30_2020_06" in done["error"] and "circa 12 GB" in done["error"]
    assert not any(p == "/jobs" for _m, p, _q, _b in w.requests) and not swissmodel  # HHblits non parte
    # con il fallback attivo va subito a SWISS-MODEL, con il motivo
    client_with_ubq.put("/api/settings", json={"qmean_remote": True, "contact_email": "a@b.it",
                                               "qmean_fallback_remote": True})
    done = qmean(client_with_ubq)
    assert done["status"] == "completed" and done["result"]["source"] == "swissmodel"
    assert "circa 12 GB" in done["result"]["fallback"]["reason"]


def test_local_qmean_starts_with_the_required_memory(client_with_ubq, worker, swissmodel):
    w = worker(SmallDockerWorker(11.7 * GIB))  # 12 GB assegnati a Docker, visti dal container
    info = qmean_local.status(force=True)
    assert info["available"] is True and info["message"] is None
    assert info["memory_required"] == {"bytes": 12 * GIB, "basis": "measured", "sufficient": True}
    done = qmean(client_with_ubq)
    assert done["status"] == "completed" and done["result"]["source"] == "local"
    assert any(p == "/jobs" for _m, p, _q, _b in w.requests) and not swissmodel


# ------------------------------------------------------------------------------------------ un calcolo alla volta
class OneAtATimeWorker(Worker):
    """Worker che tiene ogni calcolo in esecuzione finché il test non lo libera e conta quelli contemporanei."""

    def __init__(self):
        super().__init__()
        self.lock = threading.Lock()
        self.gates: dict[str, threading.Event] = {}
        self.active: set[str] = set()
        self.peak = 0

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/jobs" and request.method == "POST":
            with self.lock:
                job_id = f"q{len(self.gates) + 1}"
                self.gates[job_id] = threading.Event()
                self.active.add(job_id)
                self.peak = max(self.peak, len(self.active))
            return httpx.Response(202, json={"id": job_id, "status": "queued"})
        if path.startswith("/jobs/q"):
            job_id = path.rsplit("/", 1)[-1]
            if request.method == "DELETE" or self.gates[job_id].is_set():
                with self.lock:
                    self.active.discard(job_id)
                if request.method == "DELETE":
                    return httpx.Response(200, json={"status": "cancelled"})
                return httpx.Response(200, json={"status": "completed", "result": {
                    **QMEAN_OUTPUT, "bioloom_database": {"name": "UniRef30_2023_02", "version": "2023-02"}}})
            return httpx.Response(200, json={"status": "running"})
        return super().__call__(request)


def until(condition, timeout: float = 20) -> None:
    start = time.time()
    while time.time() - start < timeout:
        if condition():
            return
        time.sleep(0.02)
    raise AssertionError("condizione non raggiunta in tempo")


def test_local_qmean_runs_one_at_a_time_without_blocking_the_io_pool(client_with_ubq, worker):
    w = worker(OneAtATimeWorker())
    core = client_with_ubq.app.state.core
    core.jobs.register("demo_io", lambda ctx, p: {"ok": True}, pool="io")
    body = {"source": {"type": "template", "id": "1UBQ"}}
    first = client_with_ubq.post("/api/qmean", json=body).json()["id"]
    until(lambda: "q1" in w.gates)
    second = client_with_ubq.post("/api/qmean", json=body).json()["id"]
    until(lambda: client_with_ubq.get(f"/api/jobs/{second}").json()["stage"] == RESOURCE_STAGE[QMEAN_LOCAL])
    queued = client_with_ubq.get(f"/api/jobs/{second}").json()
    assert queued["status"] == "queued" and queued["queue_position"] == 1 and "q2" not in w.gates
    # il pool "io" resta libero: un altro job parte e finisce mentre il secondo QMEAN aspetta il suo turno
    other = core.jobs.submit("demo_io", {})["id"]
    assert wait_job(client_with_ubq, other, timeout=20)["status"] == "completed"
    assert client_with_ubq.get(f"/api/jobs/{second}").json()["status"] == "queued"
    w.gates["q1"].set()
    assert wait_job(client_with_ubq, first, timeout=20)["status"] == "completed"
    until(lambda: "q2" in w.gates)  # parte solo dopo il primo
    w.gates["q2"].set()
    done = wait_job(client_with_ubq, second, timeout=20)
    assert done["status"] == "completed", done["error"]
    assert w.peak == 1


def test_remote_qmean_is_not_serialized(client, monkeypatch):
    compute = client.app.state.core.compute
    monkeypatch.setenv("BIOLOOM_QMEAN_URL", "http://qmean:8080")
    assert compute._qmean_resource({"mode": "remote"}) is None  # SWISS-MODEL: nessuna risorsa locale
    assert compute._qmean_resource({}) == QMEAN_LOCAL  # Impostazioni predefinite: locale
    assert compute._qmean_resource({"mode": "auto"}) == QMEAN_LOCAL
    # senza servizio locale (l'avviatore non ha trovato UniClust30) il job fallisce subito o va al fallback
    monkeypatch.delenv("BIOLOOM_QMEAN_URL")
    assert compute._qmean_resource({}) is None
    monkeypatch.setenv("BIOLOOM_QMEAN_URL", "http://qmean:8080")
    client.put("/api/settings", json={"qmean_mode": "remote"})
    assert compute._qmean_resource({}) is None


class SlowSwissModel:
    """SWISS-MODEL simulato che risponde solo quando il test apre il cancello (annullabile nell'attesa)."""

    def __init__(self) -> None:
        self.gate = threading.Event()
        self.lock = threading.Lock()
        self.calls: list[str] = []

    def __call__(self, pdb_text, email, check=None, log=print, sleep=None):
        with self.lock:
            self.calls.append(email)
        while not self.gate.wait(0.05):
            if check is not None:
                check()
        return dict(REMOTE_RESULT)


FALLBACK_SETTINGS = {"qmean_remote": True, "contact_email": "a@b.it", "qmean_fallback_remote": True}


def test_without_the_local_service_fallbacks_run_together(client_with_ubq, monkeypatch):
    """Modalità locale senza servizio QMEAN e fallback attivo: nessuna risorsa esclusiva, i due invii a SWISS-MODEL
    partono insieme e nessun job dice di aspettare un servizio locale che non c'è."""
    c = client_with_ubq
    monkeypatch.delenv("BIOLOOM_QMEAN_URL", raising=False)
    slow = SlowSwissModel()
    monkeypatch.setattr(remote, "swissmodel_qmean", slow)
    c.put("/api/settings", json=FALLBACK_SETTINGS)
    body = {"source": {"type": "template", "id": "1UBQ"}}
    ids = [c.post("/api/qmean", json=body).json()["id"] for _ in range(2)]
    until(lambda: len(slow.calls) == 2)  # entrambi su SWISS-MODEL nello stesso momento
    for job_id in ids:
        job = c.get(f"/api/jobs/{job_id}").json()
        assert job["status"] == "running" and job["stage"] != RESOURCE_STAGE[QMEAN_LOCAL]
    slow.gate.set()
    for job_id in ids:
        done = wait_job(c, job_id, timeout=20)
        assert done["status"] == "completed", done["error"]
        assert done["result"]["source"] == "swissmodel" and done["result"]["fallback"]["from"] == "local"


class FailFirstWorker(Worker):
    """Worker QMEAN: il primo calcolo fallisce (poi c'è il fallback), i successivi riescono."""

    def __init__(self):
        super().__init__()
        self.posted = 0

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/jobs" and request.method == "POST":
            self.posted += 1
            return httpx.Response(202, json={"id": f"q{self.posted}", "status": "queued"})
        if path.startswith("/jobs/q") and request.method == "GET":
            if path == "/jobs/q1":
                return httpx.Response(200, json={"status": "failed", "error": "HHblits: interrotto"})
            return httpx.Response(200, json={"status": "completed", "result": {
                **QMEAN_OUTPUT, "bioloom_database": {"name": "UniRef30_2023_02", "version": "2023-02"}}})
        return super().__call__(request)


def test_local_service_is_released_before_the_remote_fallback(client_with_ubq, worker, monkeypatch):
    """Il primo QMEAN locale fallisce e ripiega su SWISS-MODEL (lento): il servizio locale è già libero, e un secondo
    QMEAN locale parte e finisce mentre il primo aspetta ancora SWISS-MODEL."""
    c = client_with_ubq
    w = worker(FailFirstWorker())
    slow = SlowSwissModel()
    monkeypatch.setattr(remote, "swissmodel_qmean", slow)
    c.put("/api/settings", json=FALLBACK_SETTINGS)
    assert c.app.state.core.compute._qmean_resource({}) == QMEAN_LOCAL  # servizio presente: un calcolo alla volta
    body = {"source": {"type": "template", "id": "1UBQ"}}
    first = c.post("/api/qmean", json=body).json()["id"]
    until(lambda: slow.calls)  # il primo è nel fallback su SWISS-MODEL
    second = c.post("/api/qmean", json=body).json()["id"]
    done = wait_job(c, second, timeout=20)
    assert done["status"] == "completed", done["error"]
    assert done["result"]["source"] == "local" and w.posted == 2
    assert c.get(f"/api/jobs/{first}").json()["status"] == "running"  # ancora in attesa di SWISS-MODEL
    slow.gate.set()
    done = wait_job(c, first, timeout=20)
    assert done["status"] == "completed", done["error"]
    assert done["result"]["source"] == "swissmodel" and "HHblits" in done["result"]["fallback"]["reason"]
    assert len(slow.calls) == 1
