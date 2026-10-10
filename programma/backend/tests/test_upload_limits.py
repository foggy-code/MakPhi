"""Caricamenti: limite del corpo prima di Starlette (core/bodylimit) e lavoro sui file fuori dal loop degli endpoint."""

import asyncio
import json
import threading

import pytest
from fastapi.testclient import TestClient
from starlette.requests import Request
from starlette.responses import JSONResponse

from bioloom.api import files as files_api
from bioloom.app import create_app
from bioloom.config import MAX_UPLOAD_BYTES, Paths
from bioloom.core import bodylimit, guard
from bioloom.core.bodylimit import BodyLimit
from bioloom.core.errors import AppError
from bioloom.domain import fileformats
from bioloom.domain.reproducibility import MAX_PACKAGE_BYTES

from .conftest import DATA

LIMIT = 1000


def _run(app, scope_headers, chunks):
    """Esegue `app` (ASGI) con un corpo in più messaggi; restituisce (risposta, blocchi letti dall'app)."""
    sent, pending = [], list(chunks)
    reads = []

    async def receive():
        if not pending:
            return {"type": "http.disconnect"}
        chunk = pending.pop(0)
        reads.append(chunk)
        return {"type": "http.request", "body": chunk, "more_body": bool(pending)}

    async def send(message):
        sent.append(message)

    scope = {"type": "http", "method": "POST", "path": "/x", "headers": scope_headers, "query_string": b""}
    asyncio.run(app(scope, receive, send))
    start = next(m for m in sent if m["type"] == "http.response.start")
    body = b"".join(m.get("body", b"") for m in sent if m["type"] == "http.response.body")
    return start["status"], json.loads(body), reads


async def _echo(scope, receive, send):
    """App che legge tutto il corpo, come il parser del multipart."""
    body = await Request(scope, receive).body()
    await JSONResponse({"size": len(body)})(scope, receive, send)


async def _fastapi_like(scope, receive, send):
    """Come FastAPI: un errore nella lettura del corpo diventa un 400."""
    try:
        await Request(scope, receive).body()
    except Exception:
        await JSONResponse({"detail": "There was an error parsing the body"}, status_code=400)(scope, receive, send)
        return
    await JSONResponse({"ok": True})(scope, receive, send)


def test_declared_content_length_over_the_limit_is_refused_without_reading_the_body():
    called = []

    async def app(scope, receive, send):
        called.append(True)

    status, payload, reads = _run(BodyLimit(app, LIMIT), [(b"content-length", str(LIMIT + 1).encode())], [b"x" * 10])
    assert status == 413 and payload["error"]["code"] == "too_large" and not called and reads == []
    assert "Richiesta troppo grande" in payload["error"]["message"]


@pytest.mark.parametrize("inner", [_echo, _fastapi_like])
def test_chunked_body_over_the_limit_is_refused_while_reading(inner):
    chunks = [b"a" * 400, b"b" * 400, b"c" * 400, b"d" * 400]  # 1600 byte senza Content-Length
    status, payload, reads = _run(BodyLimit(inner, LIMIT), [(b"transfer-encoding", b"chunked")], chunks)
    assert status == 413 and payload["error"]["code"] == "too_large"
    assert len(reads) == 3  # smette di leggere appena supera il limite: l'ultimo blocco resta non letto


def test_bodies_within_the_limit_pass_unchanged():
    status, payload, _ = _run(BodyLimit(_echo, LIMIT), [(b"content-length", b"1000")], [b"x" * 600, b"y" * 400])
    assert (status, payload) == (200, {"size": 1000})
    status, payload, _ = _run(BodyLimit(_fastapi_like, LIMIT), [], [b"x" * 1000])
    assert (status, payload) == (200, {"ok": True})


def test_app_limit_is_the_largest_upload_plus_the_multipart_margin(client):
    assert bodylimit.request_limit() == max(MAX_PACKAGE_BYTES, MAX_UPLOAD_BYTES) + bodylimit.MULTIPART_MARGIN
    stack = [m.cls for m in client.app.user_middleware]  # dal più esterno al più interno
    assert stack[0] is guard.RequestGuard and stack[-1] is BodyLimit
    assert client.app.user_middleware[-1].kwargs["limit"] == bodylimit.request_limit()


@pytest.fixture
def small_client(tmp_path, monkeypatch):
    """App con un limite del corpo di 200 kB (1UBQ.pdb, 80 kB, passa)."""
    monkeypatch.setattr(bodylimit, "request_limit", lambda: 200_000)
    with TestClient(create_app(Paths(tmp_path / "data"), frontend_dist=None)) as c:
        yield c


def test_app_refuses_oversized_uploads_before_the_endpoint(small_client, monkeypatch):
    monkeypatch.setattr(files_api, "store_structure", lambda *_a: pytest.fail("l'endpoint non deve partire"))
    big = b"ATOM" * 100_000
    r = small_client.post("/api/files/structure", files={"file": ("big.pdb", big)})  # con Content-Length
    assert r.status_code == 413 and r.json()["error"]["code"] == "too_large", r.text
    assert r.json()["error"]["message"] == "Richiesta troppo grande: BioLoom accetta al massimo 195 kB per richiesta."

    def chunked():
        for _ in range(10):
            yield b"x" * 50_000

    r = small_client.post("/api/files/structure", content=chunked(),
                          headers={"content-type": "multipart/form-data; boundary=abc"})
    assert r.status_code == 413 and r.json()["error"]["code"] == "too_large", r.text


def test_normal_uploads_and_other_requests_are_unchanged(small_client):
    with open(DATA / "1UBQ.pdb", "rb") as fh:
        r = small_client.post("/api/files/structure", files={"file": ("1UBQ.pdb", fh, "chemical/x-pdb")})
    assert r.status_code == 200 and r.json()["chains"][0]["residues"] == 76
    assert small_client.get("/api/health").status_code == 200
    assert small_client.get("/api/templates").json() == []
    # il limite dell'endpoint resta: 413 con il suo codice anche sotto il limite di tutta l'app
    r = small_client.post("/api/licenses/prosa", files={"file": ("LicenseDB", b"x" * 70_000)})
    assert r.status_code == 413 and r.json()["error"]["code"] == "too_large"


def _replace_work(client, monkeypatch, name, stub) -> None:
    """Sostituisce con `stub` il lavoro sincrono dell'endpoint `name` dopo la lettura del file."""
    core = client.app.state.core
    if name == "sequences":
        monkeypatch.setattr(fileformats, "parse_records", stub)
    elif name in ("structure", "quality"):
        monkeypatch.setattr(files_api, "store_structure", stub)
    elif name == "template":
        monkeypatch.setattr(core.library, "save_upload", stub)
    else:
        monkeypatch.setattr(core.licenses, "set_prosa_license", stub)


ENDPOINTS = {
    "sequences": ("/api/files/sequences", "seq.fasta", b">a\nMKV\n"),
    "structure": ("/api/files/structure", "x.pdb", b"ATOM\n"),
    "quality": ("/api/quality/upload", "x.pdb", b"ATOM\n"),
    "template": ("/api/templates/upload", "x.pdb", b"ATOM\n"),
    "prosa": ("/api/licenses/prosa", "LicenseDB", b"licenza\n"),
}


@pytest.mark.parametrize("name", list(ENDPOINTS))
def test_upload_work_does_not_block_the_event_loop(client, monkeypatch, name):
    """Mentre l'endpoint lavora sul file, un'altra richiesta riceve risposta: il lavoro è in un thread."""
    path, filename, content = ENDPOINTS[name]
    entered, health_done, seen = threading.Event(), threading.Event(), []

    def stub(*_a, **_k):
        entered.set()
        seen.append(health_done.wait(10))  # con il loop bloccato la richiesta di controllo non arriverebbe
        raise AppError("fine della prova", code="test_stop")

    _replace_work(client, monkeypatch, name, stub)
    result = {}
    worker = threading.Thread(target=lambda: result.update(r=client.post(path, files={"file": (filename, content)})))
    worker.start()
    assert entered.wait(10)
    assert client.get("/api/health").status_code == 200
    health_done.set()
    worker.join(20)
    assert seen == [True]
    assert result["r"].status_code == 400 and result["r"].json()["error"]["code"] == "test_stop"
