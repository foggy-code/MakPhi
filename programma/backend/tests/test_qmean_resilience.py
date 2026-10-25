"""QMEAN locale che resiste a un worker occupato: BioLoom aspetta e riprova invece di far fallire il calcolo, rimanda
un calcolo perso dal worker riavviato, accorpa le richieste di stato; il worker serve le richieste con un numero
fisso di thread, chiude quelle scadute e non rifà la scansione dei database a ogni calcolo.

Sul server una pipeline ha perso 107.918 QMEAN: con 1096 thread aperti il worker non rispondeva più e ogni calcolo
falliva in 4 s con "QMEAN locale non attivo", lasciando partire subito il seguente."""

import importlib.util
import json
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler
from pathlib import Path

import httpx
import pytest

from bioloom.core.errors import ToolUnavailable
from bioloom.core.jobs import JobCancelled
from bioloom.engines import qmean_local

from .test_engines_extra import QMEAN_OUTPUT, Ctx
from .test_qmean_speed import qw  # noqa: F401 (fixture: il modulo del worker)

UNICLUST = Path(__file__).resolve().parents[2] / "docker" / "qmean" / "uniclust.py"


class FlakyWorker:
    """Worker simulato che per `down` richieste non risponde (connessione rifiutata) e può perdere il primo job."""

    def __init__(self, down=0, lose_first=False, poll_errors=0):
        self.down, self.lose_first, self.poll_errors = down, lose_first, poll_errors
        self.requests, self.posts = [], 0

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append((request.method, request.url.path, dict(request.url.params)))
        if self.down > 0:
            self.down -= 1
            raise httpx.ConnectError("connessione rifiutata", request=request)
        path = request.url.path
        if path == "/status":
            return httpx.Response(200, json={"uniclust": {"valid": True, "prefix": "UniRef30_2023_02", "error": None},
                                             "qmtl": {"present": False}, "slots": 4})
        if path == "/jobs" and request.method == "POST":
            self.posts += 1
            return httpx.Response(202, json={"id": f"j{self.posts}", "status": "queued"})
        if path.startswith("/jobs/") and request.method == "GET":
            if self.lose_first and path == "/jobs/j1":
                return httpx.Response(404, json={"error": "job sconosciuto"})
            if self.poll_errors > 0:
                self.poll_errors -= 1
                raise httpx.ReadTimeout("nessuna risposta", request=request)
            return httpx.Response(200, json={"status": "completed", "result": QMEAN_OUTPUT})
        return httpx.Response(200, json={"status": "cancelled"})

    def count(self, path, method="GET"):
        return sum(1 for m, p, _q in self.requests if p == path and m == method)


@pytest.fixture
def flaky(monkeypatch):
    def install(w):
        qmean_local._status_cache.update({"at": 0.0, "key": None, "value": None})
        monkeypatch.setenv("BIOLOOM_QMEAN_URL", "http://qmean:8080")
        monkeypatch.setattr(qmean_local, "_client", lambda: httpx.Client(
            base_url="http://qmean:8080", transport=httpx.MockTransport(w)))
        return w
    return install


class Sleeps:
    def __init__(self):
        self.total = 0.0

    def __call__(self, seconds):
        self.total += seconds


# ------------------------------------------------------------------------------------------------- BioLoom
def test_an_unreachable_worker_is_waited_for_instead_of_failing(flaky, ubq_path):
    w = flaky(FlakyWorker(down=3))
    ctx, sleeps = Ctx(), Sleeps()
    result = qmean_local.score(ubq_path.read_text(), ctx, sleep=sleeps)
    assert result["source"] == "local" and result["qmean4_z"] == 0.35
    assert w.posts == 1 and sleeps.total >= 3 * qmean_local.RETRY_SECONDS
    assert any("non risponde" in m for m in ctx.logs) and any("di nuovo" in m for m in ctx.logs)
    assert any(s and "nuovo tentativo" in s for s in ctx.stages)


def test_the_wait_has_a_limit_and_then_fails_with_the_real_reason(flaky, ubq_path, monkeypatch):
    monkeypatch.setattr(qmean_local, "WAIT_MINUTES", 1.0)  # 4 tentativi da 15 s
    w = flaky(FlakyWorker(down=10 ** 6))
    with pytest.raises(ToolUnavailable, match="non risponde") as err:
        qmean_local.score(ubq_path.read_text(), Ctx(), sleep=Sleeps())
    assert "collega il disco" not in err.value.message  # il servizio c'è: non è un disco mancante
    assert w.count("/status") == 5 and w.posts == 0  # prima lettura + un tentativo per attesa


def test_a_cancelled_job_stops_waiting(flaky, ubq_path):
    flaky(FlakyWorker(down=10 ** 6))
    with pytest.raises(JobCancelled):
        qmean_local.score(ubq_path.read_text(), Ctx(cancel_after=5), sleep=Sleeps())


def test_a_job_lost_by_a_restarted_worker_is_sent_again(flaky, ubq_path):
    w = flaky(FlakyWorker(lose_first=True))
    ctx = Ctx()
    result = qmean_local.score(ubq_path.read_text(), ctx, sleep=Sleeps())
    assert result["qmean4_z"] == 0.35 and w.posts == 2
    assert any("perso il calcolo" in m and "Nuovo invio" in m for m in ctx.logs)


def test_polling_errors_do_not_fail_the_calculation(flaky, ubq_path):
    w = flaky(FlakyWorker(poll_errors=2))
    result = qmean_local.score(ubq_path.read_text(), Ctx(), sleep=Sleeps())
    assert result["qmean4_z"] == 0.35 and w.posts == 1  # nessun secondo invio: il job era ancora nel worker


def test_calculations_reuse_the_cached_status(flaky, ubq_path):
    w = flaky(FlakyWorker())
    for _ in range(5):
        qmean_local.score(ubq_path.read_text(), Ctx(), sleep=Sleeps())
    statuses = [q for m, p, q in w.requests if p == "/status"]
    assert len(statuses) == 1 and "refresh" not in statuses[0]  # niente nuova scansione nel worker per calcolo
    assert w.posts == 5 and qmean_local.slots() == 4


def test_simultaneous_status_requests_share_one_answer(flaky):
    w = FlakyWorker()
    slow_calls = []

    def slow(request):
        if request.url.path == "/status":
            slow_calls.append(1)
            time.sleep(0.2)
        return w(request)

    flaky(slow)
    threads = [threading.Thread(target=qmean_local.status, kwargs={"force": True}) for _ in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert 1 <= len(slow_calls) <= 2  # non una richiesta per chiamata
    before = len(slow_calls)
    qmean_local.status(force=True)  # chiesta dopo: una risposta nuova
    assert len(slow_calls) == before + 1


def test_unreachable_status_explains_it_is_not_the_disk(flaky):
    flaky(FlakyWorker(down=1))
    info = qmean_local.status(force=True)
    assert info["configured"] and not info["reachable"] and info["message"] == qmean_local.UNREACHABLE


# --------------------------------------------------------------------------------------------------- worker
@pytest.fixture
def uc(monkeypatch):
    spec = importlib.util.spec_from_file_location("uniclust_resilience", UNICLUST)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_simultaneous_scans_are_shared(uc, monkeypatch):
    calls = []

    def discover():
        calls.append(1)
        time.sleep(0.2)
        return {"databases": [], "archives": [], "unsupported": []}

    monkeypatch.setattr(uc, "discover", discover)
    cache = uc.Cache(ttl=30)
    threads = [threading.Thread(target=cache.get, kwargs={"force": True}) for _ in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert 1 <= len(calls) <= 2
    cache.get()
    assert len(calls) <= 2  # entro il ttl si riusa
    cache.get(force=True)  # chiesta dopo l'ultima scansione: se ne fa una nuova
    assert len(calls) <= 3 and calls


def _serve(worker_module, handler, **kwargs):
    server = worker_module.Server(("127.0.0.1", 0), handler, **kwargs)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, "http://127.0.0.1:%d" % server.server_address[1]


def _get(url, results):
    try:
        with urllib.request.urlopen(url, timeout=10) as resp:
            results.append(resp.read().decode())
    except Exception as exc:  # noqa: BLE001 (connessione chiusa senza risposta)
        results.append(type(exc).__name__)


def test_the_server_uses_a_fixed_number_of_threads(qw):  # noqa: F811
    state = {"now": 0, "max": 0}
    lock = threading.Lock()

    class Slow(BaseHTTPRequestHandler):
        def do_GET(self):
            with lock:
                state["now"] += 1
                state["max"] = max(state["max"], state["now"])
            time.sleep(0.2)
            with lock:
                state["now"] -= 1
            body = b"ok"
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    before = threading.active_count()
    server, url = _serve(qw, Slow, threads=2, stale=30)
    try:
        results = []
        clients = [threading.Thread(target=_get, args=(url, results)) for _ in range(8)]
        for t in clients:
            t.start()
        for t in clients:
            t.join()
        assert results == ["ok"] * 8 and state["max"] == 2
        assert threading.active_count() - before <= 3  # 2 thread del server + serve_forever, non uno per richiesta
    finally:
        server.shutdown()
        server.server_close()


def test_requests_left_waiting_too_long_are_closed_without_work(qw):  # noqa: F811
    served = []

    class Slow(BaseHTTPRequestHandler):
        def do_GET(self):
            served.append(1)
            time.sleep(0.5)
            self.send_response(200)
            self.send_header("Content-Length", "2")
            self.end_headers()
            self.wfile.write(b"ok")

        def log_message(self, *args):
            pass

    server, url = _serve(qw, Slow, threads=1, stale=0.2)
    try:
        results = []
        clients = [threading.Thread(target=_get, args=(url, results)) for _ in range(4)]
        for t in clients:
            t.start()
            time.sleep(0.02)
        for t in clients:
            t.join()
        assert results.count("ok") == len(served) < 4  # le richieste scadute non arrivano al gestore
    finally:
        server.shutdown()
        server.server_close()


def test_submitting_a_model_does_not_rescan_the_databases(qw, monkeypatch):  # noqa: F811
    forced = []

    def status(prefer=None, force=False):
        forced.append(force)
        return {"uniclust": {"valid": True}}

    monkeypatch.setattr(qw, "database_status", status)
    monkeypatch.setattr(qw, "_queue", type("Q", (), {"put": lambda self, job_id: None})())
    server, url = _serve(qw, qw.Handler, threads=2)
    try:
        body = json.dumps({"pdb": "ATOM      1  N   MET A   1", "method": "QMEAN"}).encode()
        req = urllib.request.Request(url + "/jobs", data=body, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            assert resp.status == 202 and json.loads(resp.read())["status"] == "queued"
        assert forced == [False]
    finally:
        server.shutdown()
        server.server_close()
