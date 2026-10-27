"""Protezione dalle richieste di altri siti (core/guard): Host contro il DNS rebinding, origine contro il CSRF."""

from contextlib import ExitStack
from itertools import count
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from makphi.api import system
from makphi.app import DEV_ORIGINS, create_app
from makphi.config import Paths
from makphi.core import auth, guard

from .conftest import DATA

KEY = "segreto-12345"
EVIL = "https://evil.example"


@pytest.fixture
def make_client(tmp_path, monkeypatch):
    """App nuova con MAKPHI_ALLOWED_HOSTS a scelta (None = assente: solo i nomi di loopback). Le origini di Vite
    sono esplicite: i test girano anche nel container, dove create_app non le ammette (None = predefinite)."""
    counter = count()
    with ExitStack() as stack:
        def make(allowed=None, *, auth_config=None, frontend_dist=None, base_url="http://127.0.0.1:8765",
                 dev_origins=DEV_ORIGINS):
            if allowed is None:
                monkeypatch.delenv(guard.ENV, raising=False)
            else:
                monkeypatch.setenv(guard.ENV, allowed)  # letta dal lifespan, all'ingresso nel TestClient
            app = create_app(Paths(tmp_path / f"data{next(counter)}"), frontend_dist=frontend_dist,
                             auth_config=auth_config, dev_origins=dev_origins)
            return stack.enter_context(TestClient(app, base_url=base_url))
        yield make


def _code(r) -> str:
    return r.json()["error"]["code"]


# ---------- funzioni pure ----------

def test_split_host_forms():
    assert guard.split_host("127.0.0.1:8765") == ("127.0.0.1", 8765)
    assert guard.split_host("LocalHost") == ("localhost", None)
    assert guard.split_host("[::1]:8765") == ("::1", 8765)
    assert guard.split_host("[::1]") == ("::1", None)
    assert guard.split_host("::1") == ("::1", None)  # IPv6 senza parentesi
    assert guard.split_host("localhost.:5173") == ("localhost", 5173)  # nome con il punto finale
    assert guard.split_host("evil.example:abc") == ("evil.example", None)
    assert guard.split_host("evil.example:²") == ("evil.example", None)  # cifra non ASCII: niente errore


def test_allowed_hosts_parsing():
    assert guard.allowed_hosts("") == frozenset()
    assert guard.allowed_hosts(" Makphi.Lab, 10.0.0.5:8765  altro.example,,") == {"makphi.lab", "10.0.0.5",
                                                                                     "altro.example"}
    assert guard.allowed_hosts("*") == {"*"}
    # voci scritte come URL, per analogia con MAKPHI_PUBLIC_URL: conta il nome
    assert guard.allowed_hosts("https://Makphi.Lab, http://10.0.0.5:8765/ makphi2.lab/ http://[::2]:8765/x") == {
        "makphi.lab", "10.0.0.5", "makphi2.lab", "::2"}
    assert guard.host_allowed("makphi.lab", guard.allowed_hosts("https://makphi.lab"))
    assert guard.allowed_hosts("http://[::1, https://") == frozenset()  # URL non validi: nessun nome


def test_host_allowed():
    allowed = guard.allowed_hosts("makphi.lab")
    for host in ("127.0.0.1:8765", "127.0.0.1:1", "localhost", "localhost:5173", "[::1]:8765", "MAKPHI.LAB:8765",
                 None, ""):
        assert guard.host_allowed(host, allowed), host
    for host in ("evil.example", "evil.example:8765", "127.0.0.1.nip.io:8765", "localhost.evil.example",
                 "makphi.lab.evil.example", "[::2]:8765"):
        assert not guard.host_allowed(host, allowed), host
    assert guard.host_allowed("evil.example", guard.allowed_hosts("*"))


def test_same_origin():
    assert guard.same_origin("http://127.0.0.1:8765", "127.0.0.1:8765")
    assert guard.same_origin("http://LOCALHOST:8765", "localhost:8765")
    assert guard.same_origin("http://[::1]:8765", "[::1]:8765")
    assert guard.same_origin("https://makphi.lab", "makphi.lab")  # porta predefinita dello schema
    assert guard.same_origin("https://makphi.lab", "makphi.lab:443")
    assert guard.same_origin("http://makphi.lab:80", "makphi.lab")
    assert not guard.same_origin("http://127.0.0.1:8766", "127.0.0.1:8765")
    assert not guard.same_origin("https://makphi.lab:8443", "makphi.lab")
    assert not guard.same_origin("http://makphi.lab", "makphi.lab:8765")
    assert not guard.same_origin("http://evil.example:8765", "127.0.0.1:8765")
    assert not guard.same_origin("null", "127.0.0.1:8765")
    assert not guard.same_origin("http://[::1", "[::1]:8765")  # Origin non valido
    assert not guard.same_origin("ftp://127.0.0.1:8765", "127.0.0.1:8765")
    assert not guard.same_origin("http://127.0.0.1:8765", None)
    # reverse proxy: conta il primo X-Forwarded-Host
    assert guard.same_origin("https://makphi.lab", "127.0.0.1:8765", "makphi.lab, proxy-interno")
    assert not guard.same_origin("https://makphi.lab", "127.0.0.1:8765", "proxy-interno, makphi.lab")


def test_origin_allowed_rules():
    dev = ("http://localhost:5173",)

    def ok(method="POST", origin=None, fetch_site=None, host="127.0.0.1:8765", forwarded_host=None):
        return guard.origin_allowed(method, origin=origin, fetch_site=fetch_site, host=host,
                                    forwarded_host=forwarded_host, dev_origins=dev)

    for method in ("GET", "HEAD", "OPTIONS", "get"):
        assert ok(method, origin=EVIL, fetch_site="cross-site"), method
    for method in ("POST", "PUT", "PATCH", "DELETE"):
        assert not ok(method, origin=EVIL, fetch_site="cross-site"), method
    assert ok(origin="http://localhost:5173", fetch_site="cross-site")  # sviluppo con CORS
    assert ok(fetch_site="same-origin") and ok(fetch_site="none") and ok(fetch_site="Same-Origin")
    assert not ok(fetch_site="same-site") and not ok(fetch_site="cross-site")
    # Sec-Fetch-Site vince su Origin: è il punto di vista del browser, anche dietro un proxy che cambia Host
    assert not ok(origin="http://127.0.0.1:8765", fetch_site="cross-site")
    assert ok(origin="https://makphi.lab", fetch_site="same-origin")
    assert ok(origin="http://127.0.0.1:8765") and not ok(origin=EVIL) and not ok(origin="null")
    assert ok()  # né Origin né Sec-Fetch-Site: curl, script, avviatori


# ---------- controllo dell'Host (DNS rebinding) ----------

def test_hostile_host_is_refused_without_access_key(make_client, tmp_path):
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("<!doctype html><title>MakPhi</title>", encoding="utf-8")
    c = make_client(frontend_dist=dist)
    for path in ("/api/projects", "/api/system/info", "/", "/progetti", "/docs", "/openapi.json"):
        r = c.get(path, headers={"Host": "evil.example:8765"})
        assert r.status_code == 400 and _code(r) == "host_not_allowed", path
    msg = r.json()["error"]["message"]
    assert "MAKPHI_ALLOWED_HOSTS" in msg and "evil.example" in msg
    assert c.post("/api/projects", json={"name": "x"}, headers={"Host": "evil.example"}).status_code == 400
    assert c.get("/api/health", headers={"Host": "evil.example"}).status_code == 200  # esente
    page = c.get("/", headers={"Host": "localhost:5173"})  # proxy di Vite
    assert page.status_code == 200 and "MakPhi" in page.text


def test_loopback_hosts_with_any_port_are_allowed(make_client):
    c = make_client()
    for host in ("127.0.0.1:8765", "127.0.0.1:9999", "localhost", "localhost:5173", "[::1]:8765", "[::1]"):
        assert c.get("/api/projects", headers={"Host": host}).status_code == 200, host


def test_allowed_hosts_variable(make_client):
    c = make_client("makphi.lab, Altro.Example")
    assert c.get("/api/projects", headers={"Host": "makphi.lab:8765"}).status_code == 200
    assert c.get("/api/projects", headers={"Host": "ALTRO.example"}).status_code == 200
    assert c.get("/api/projects", headers={"Host": "127.0.0.1:8765"}).status_code == 200
    assert c.get("/api/projects", headers={"Host": "evil.example"}).status_code == 400
    star = make_client("*")
    assert star.get("/api/projects", headers={"Host": "evil.example"}).status_code == 200


def test_host_check_is_off_with_access_key(make_client):
    # in modalità server proteggono chiave e cookie, e il server si raggiunge per IP, nome o proxy
    c = make_client(auth_config=auth.AuthConfig(enabled=True, key=KEY), base_url="http://makphi.lab")
    r = c.get("/api/projects", headers={"Host": "10.0.0.5:8765"})
    assert r.status_code == 401 and _code(r) == "auth_required"
    assert c.get("/api/projects", headers={"Authorization": f"Bearer {KEY}"}).status_code == 200


# ---------- controllo dell'origine (CSRF) ----------

def test_cross_site_writes_are_refused(make_client):
    c = make_client()
    before = c.get("/api/projects").json()
    for site in ("cross-site", "same-site"):
        r = c.post("/api/projects", json={"name": "x"}, headers={"Sec-Fetch-Site": site, "Origin": EVIL})
        assert r.status_code == 403 and _code(r) == "cross_origin", site
        assert "altro sito" in r.json()["error"]["message"]
    # browser senza Sec-Fetch-Site: decide l'Origin
    assert c.post("/api/projects", json={"name": "x"}, headers={"Origin": EVIL}).status_code == 403
    assert c.post("/api/projects", json={"name": "x"}, headers={"Origin": "null"}).status_code == 403
    assert c.delete("/api/projects/inesistente", headers={"Sec-Fetch-Site": "cross-site"}).status_code == 403
    # upload multipart da un form di un altro sito
    with open(DATA / "1UBQ.pdb", "rb") as fh:
        r = c.post("/api/files/structure", files={"file": ("1UBQ.pdb", fh, "chemical/x-pdb")},
                   headers={"Sec-Fetch-Site": "cross-site"})
    assert r.status_code == 403
    assert c.get("/api/projects").json() == before  # nessun progetto creato


def test_same_origin_and_non_browser_writes_pass(make_client):
    c = make_client()
    for i, headers in enumerate(({"Sec-Fetch-Site": "same-origin", "Origin": "http://127.0.0.1:8765"},
                                 {"Sec-Fetch-Site": "none"},
                                 {"Origin": "http://127.0.0.1:8765"},
                                 {},  # curl, script, avviatori
                                 {"Origin": "http://localhost:5173", "Sec-Fetch-Site": "cross-site"})):  # Vite
        r = c.post("/api/projects", json={"name": f"Progetto {i}"}, headers=headers)
        assert r.status_code == 200, (headers, r.text)


def test_origin_is_compared_with_host_and_default_port(make_client):
    c = make_client("makphi.lab", base_url="http://makphi.lab")
    assert c.post("/api/projects", json={"name": "a"}, headers={"Origin": "http://makphi.lab"}).status_code == 200
    assert c.post("/api/projects", json={"name": "b"}, headers={"Origin": "http://makphi.lab:80"}).status_code == 200
    assert c.post("/api/projects", json={"name": "c"},
                  headers={"Origin": "http://makphi.lab:8080"}).status_code == 403


def test_reverse_proxy_and_server_mode(make_client):
    c = make_client(auth_config=auth.AuthConfig(enabled=True, key=KEY))
    bearer = {"Authorization": f"Bearer {KEY}"}
    # il proxy TLS raggiunge il container su 127.0.0.1:8765 e dichiara il nome pubblico
    proxied = {**bearer, "Origin": "https://makphi.example.org", "X-Forwarded-Host": "makphi.example.org"}
    assert c.post("/api/projects", json={"name": "a"}, headers=proxied).status_code == 200
    assert c.post("/api/projects", json={"name": "b"},
                  headers={**proxied, "X-Forwarded-Host": "altro.example.org"}).status_code == 403
    # la guardia è il middleware più esterno: rifiuta prima della chiave, anche con una chiave valida
    assert c.app.user_middleware[0].cls is guard.RequestGuard
    r = c.post("/api/projects", json={"name": "c"}, headers={"Sec-Fetch-Site": "cross-site"})
    assert r.status_code == 403 and _code(r) == "cross_origin"
    assert c.post("/api/projects", json={"name": "d"},
                  headers={**bearer, "Sec-Fetch-Site": "cross-site"}).status_code == 403
    assert c.post("/api/projects", json={"name": "e"},
                  headers={**bearer, "Sec-Fetch-Site": "same-origin"}).status_code == 200


def test_reads_and_preflight_are_never_blocked_by_origin(make_client):
    c = make_client()
    cross = {"Sec-Fetch-Site": "cross-site", "Origin": EVIL}
    assert c.get("/api/projects", headers=cross).status_code == 200
    assert c.head("/api/health", headers=cross).status_code != 403
    pre = c.options("/api/projects", headers={"Origin": "http://localhost:5173", "Sec-Fetch-Site": "cross-site",
                                              "Access-Control-Request-Method": "POST"})
    assert pre.status_code == 200 and pre.headers["access-control-allow-origin"] == "http://localhost:5173"
    evil = c.options("/api/projects", headers={**cross, "Access-Control-Request-Method": "POST"})
    assert evil.status_code != 403 and "access-control-allow-origin" not in evil.headers  # decide CORS


def test_cross_site_shutdown_never_reaches_the_handler(make_client, monkeypatch):
    """Lo scenario reale: una pagina qualsiasi fa fetch("http://127.0.0.1:8765/api/system/shutdown",
    {method: "POST", mode: "no-cors"}) con l'arresto consentito come in Docker."""
    monkeypatch.setenv("MAKPHI_ALLOW_SHUTDOWN", "1")
    started = []

    class FakeThread:  # al posto del thread di arresto: niente si ferma anche se la guardia lasciasse passare
        def __init__(self, target, daemon=None):
            self.target = target

        def start(self):
            started.append(self.target)

    monkeypatch.setattr(system, "threading", SimpleNamespace(Thread=FakeThread))
    c = make_client()
    for headers in ({"Sec-Fetch-Site": "cross-site", "Origin": EVIL}, {"Origin": EVIL}, {"Origin": "null"}):
        r = c.post("/api/system/shutdown", headers=headers)
        assert r.status_code == 403 and _code(r) == "cross_origin", headers
    assert started == []
    # dalla pagina di MakPhi l'arresto passa ancora la guardia
    ok = c.post("/api/system/shutdown", headers={"Sec-Fetch-Site": "same-origin", "Origin": "http://127.0.0.1:8765"})
    assert ok.status_code == 200 and ok.json()["status"] == "stopping" and len(started) == 1


def test_dev_origins_only_outside_the_container(make_client, monkeypatch):
    vite = {"Origin": "http://localhost:5173", "Sec-Fetch-Site": "cross-site"}  # pagina sulla porta 5173
    preflight = {"Origin": "http://localhost:5173", "Access-Control-Request-Method": "POST"}
    monkeypatch.setenv("MAKPHI_CONTAINER", "1")
    c = make_client(dev_origins=None)
    r = c.post("/api/system/cleanup", json={}, headers=vite)
    assert r.status_code == 403 and _code(r) == "cross_origin"
    assert "access-control-allow-origin" not in c.options("/api/projects", headers=preflight).headers
    # l'interfaccia compilata e il proxy di Vite sono stessa origine: passano anche nel container
    assert c.post("/api/projects", json={"name": "a"}, headers={"Sec-Fetch-Site": "same-origin"}).status_code == 200
    assert c.post("/api/projects", json={"name": "b"}, headers={"Host": "localhost:5173",
                                                                "Origin": "http://localhost:5173"}).status_code == 200
    monkeypatch.delenv("MAKPHI_CONTAINER")
    native = make_client(dev_origins=None)
    assert native.post("/api/projects", json={"name": "c"}, headers=vite).status_code == 200
    assert native.options("/api/projects", headers=preflight).headers["access-control-allow-origin"] == (
        "http://localhost:5173")


# ---------- iframe (clickjacking) ----------

def _no_frames(r) -> bool:
    return (r.headers.get("x-frame-options") == "DENY"
            and "frame-ancestors 'none'" in r.headers.get("content-security-policy", ""))


def test_pages_and_api_cannot_be_framed(make_client, tmp_path):
    """Una pagina di MakPhi in un iframe trasparente di un altro sito invierebbe le richieste come «stessa origine»
    (Sec-Fetch-Site: same-origin) e passerebbe il controllo dell'origine: il browser non deve mostrarla."""
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("<!doctype html><title>MakPhi</title>", encoding="utf-8")
    c = make_client(frontend_dist=dist)
    framed = {"Sec-Fetch-Dest": "iframe", "Sec-Fetch-Site": "cross-site"}
    for path in ("/", "/progetti", "/api/projects", "/api/health", "/docs"):
        r = c.get(path, headers=framed)
        assert r.status_code == 200 and _no_frames(r), path
    # anche sui rifiuti della guardia e della chiave d'accesso
    assert _no_frames(c.get("/", headers={"Host": "evil.example"}))
    assert _no_frames(c.post("/api/projects", json={"name": "x"}, headers={"Sec-Fetch-Site": "cross-site"}))
    server = make_client(auth_config=auth.AuthConfig(enabled=True, key=KEY))
    r = server.get("/api/projects")
    assert r.status_code == 401 and _no_frames(r)
    assert r.headers.get_list("content-security-policy") == ["frame-ancestors 'none'"]  # una sola, senza doppioni
    # una CSP con frame-ancestors già presente resta com'è: report HTML, vedi test_report (CSP == report.CSP)
