"""Licenze (MODELLER, ProSA) e accesso con chiave in modalità server."""

import importlib.util
import os
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from makphi.app import create_app
from makphi.config import Paths, UserSettings
from makphi.core import auth
from makphi.core.errors import AppError
from makphi.core.jobs import default_env
from makphi.services import licenses as lic
from makphi.services.licenses import LicenseService

from .conftest import requires_modeller

LICENSE_DB = "tester123 dec-31-2099 0A1b2C3d4E5f 0123456789abcdef0123456789abcdef\n"


def test_mask_and_mac_helpers():
    assert lic.mask("") == ""
    masked = lic.mask("MODELABCDEF")
    assert masked.startswith("MOD") and masked.endswith("EF") and "ABCD" not in masked
    assert lic.format_mac("0A1b2C3d4E5f") == "0a:1b:2c:3d:4e:5f"
    assert lic.format_mac("0a-1b-2c-3d-4e-5f") == "0a:1b:2c:3d:4e:5f"
    assert lic.format_mac("123") == ""


def test_parse_prosa_license():
    entries = lic.parse_prosa_license(LICENSE_DB)
    assert entries == [{"name": "tester123", "expires": "dec-31-2099", "mac": "0a:1b:2c:3d:4e:5f"}]
    with pytest.raises(AppError):
        lic.parse_prosa_license("solo due campi\n")
    with pytest.raises(AppError):
        lic.parse_prosa_license("   \n")


def test_modeller_key_sources_and_subprocess_env(tmp_path, monkeypatch):
    svc = LicenseService(tmp_path / "licenses", UserSettings)
    monkeypatch.delenv("MODELLER_KEY", raising=False)
    monkeypatch.delenv("MAKPHI_MODELLER_KEY", raising=False)
    assert svc.modeller_key() == ("", None)
    monkeypatch.setenv("MODELLER_KEY", "MODELDAENV")
    assert svc.modeller_key() == ("MODELDAENV", "env")
    (tmp_path / "licenses").mkdir()
    (tmp_path / "licenses" / "modeller.key").write_text("MODELDAFILE\n")
    assert svc.modeller_key() == ("MODELDAFILE", "file")  # il file salvato dall'interfaccia ha la precedenza
    svc.apply()
    assert default_env()["MAKPHI_MODELLER_KEY"] == "MODELDAFILE"
    svc.remove_modeller_key()
    assert default_env()["MAKPHI_MODELLER_KEY"] == "MODELDAENV"
    monkeypatch.delenv("MODELLER_KEY")
    svc.apply()
    assert "MAKPHI_MODELLER_KEY" not in default_env()


def test_invalid_key_format_is_rejected_without_probe(tmp_path):
    svc = LicenseService(tmp_path, UserSettings)
    with pytest.raises(AppError) as exc:
        svc.set_modeller_key("chiave con spazi!")
    assert exc.value.status == 422
    assert not (tmp_path / "modeller.key").exists()


def _installed_key() -> str:
    """Chiave valida: MODELLER_KEY (container/server) oppure quella scritta nell'installazione locale."""
    if os.environ.get("MODELLER_KEY"):
        return os.environ["MODELLER_KEY"]
    spec = importlib.util.find_spec("modeller")
    text = (Path(spec.origin).parent / "config.py").read_text()
    return re.search(r"license\s*=\s*r?['\"]([^'\"]+)['\"]", text).group(1)


@requires_modeller
def test_modeller_key_is_verified_before_saving(tmp_path):
    svc = LicenseService(tmp_path, UserSettings)
    with pytest.raises(AppError) as exc:
        svc.set_modeller_key("MODELSBAGLIATA")
    assert exc.value.status == 422 and "MODELSBAGLIATA" not in exc.value.message  # mai in chiaro
    assert not (tmp_path / "modeller.key").exists()
    status = svc.set_modeller_key(_installed_key())
    assert status["modeller"]["configured"] and status["modeller"]["source"] == "file"
    assert status["modeller"]["available"]
    assert _installed_key() not in str(status)
    if os.name == "posix":
        assert (tmp_path / "modeller.key").stat().st_mode & 0o077 == 0
    svc.remove_modeller_key()


def test_prosa_license_upload_api(client):
    r = client.post("/api/licenses/prosa", files={"file": ("LicenseDB", LICENSE_DB.encode(), "text/plain")})
    assert r.status_code == 200, r.text
    prosa = r.json()["prosa"]
    assert prosa["license"] and prosa["mac_required"] == "0a:1b:2c:3d:4e:5f" and prosa["program"] == "tester123"
    bad = client.post("/api/licenses/prosa", files={"file": ("LicenseDB", b"non valido", "text/plain")})
    assert bad.status_code == 400 and bad.json()["error"]["code"] == "invalid_license"
    assert client.delete("/api/licenses/prosa").json()["prosa"]["license"] is False
    status = client.get("/api/licenses").json()
    assert {"modeller", "prosa", "procheck"} <= status.keys()


def test_auth_config_tokens():
    cfg = auth.AuthConfig(enabled=True, key="segreto-12345")
    token = cfg.issue(now=1000.0)
    assert cfg.verify(token, now=1001.0)
    assert not cfg.verify(token, now=1000.0 + 31 * 86400)  # scaduto
    assert not cfg.verify(token.replace(".", ".0"), now=1001.0)
    assert not auth.AuthConfig(enabled=True, key="altra-chiave").verify(token, now=1001.0)
    assert not cfg.requires_auth("/")  # l'interfaccia (senza dati) è sempre servita
    assert not cfg.requires_auth("/api/health") and not cfg.requires_auth("/api/auth/status")
    assert cfg.requires_auth("/api/projects") and cfg.requires_auth("/openapi.json")
    assert not auth.AuthConfig(enabled=False).requires_auth("/api/projects")
    assert cfg.session_valid(cfg.issue()) and not cfg.session_valid(None) and not cfg.session_valid("")
    # la chiave come Bearer si confronta solo attraverso il freno ai tentativi (api/auth.authenticate)
    assert auth.bearer_key("Bearer segreto-12345") == "segreto-12345" and auth.bearer_key("bearer x") == "x"
    assert auth.bearer_key("Basic abc") is None and auth.bearer_key(None) is None


def test_auth_load_generates_and_reuses_key(tmp_path, monkeypatch):
    monkeypatch.delenv("MAKPHI_ACCESS_KEY", raising=False)
    monkeypatch.setenv("MAKPHI_AUTH", "off")
    assert auth.load(tmp_path).enabled is False
    monkeypatch.setenv("MAKPHI_AUTH", "on")
    first = auth.load(tmp_path)
    assert first.enabled and first.generated and len(first.key) >= 16
    second = auth.load(tmp_path)
    assert second.key == first.key and not second.generated
    monkeypatch.setenv("MAKPHI_ACCESS_KEY", "chiave-fissa-abbastanza-lunga")  # almeno 16 caratteri
    assert auth.load(tmp_path).key == "chiave-fissa-abbastanza-lunga"


@pytest.fixture
def server_client(tmp_path, monkeypatch):
    monkeypatch.delenv("MAKPHI_ALLOW_SHUTDOWN", raising=False)
    app = create_app(Paths(tmp_path / "data"), frontend_dist=None,
                     auth_config=auth.AuthConfig(enabled=True, key="segreto-12345"))
    with TestClient(app) as c:
        yield c


def test_server_mode_requires_access_key(server_client):
    c = server_client
    assert c.get("/api/health").status_code == 200
    assert c.get("/api/auth/status").json() == {"enabled": True, "authenticated": False}
    r = c.get("/api/projects")
    assert r.status_code == 401 and r.json()["error"]["code"] == "auth_required"
    assert c.post("/api/auth/login", json={"key": "sbagliata"}).status_code == 401
    assert c.get("/api/projects", headers={"Authorization": "Bearer segreto-12345"}).status_code == 200
    ok = c.post("/api/auth/login", json={"key": "segreto-12345"})
    assert ok.status_code == 200 and auth.COOKIE in ok.cookies
    assert c.get("/api/projects").status_code == 200  # cookie di sessione
    info = c.get("/api/system/info").json()
    assert info["runtime"]["auth"] is True and info["runtime"]["allow_shutdown"] is False
    assert c.post("/api/system/shutdown").status_code == 403
    c.post("/api/auth/logout")
    assert c.get("/api/projects").status_code == 401


def test_session_cookie_is_secure_behind_an_https_proxy(server_client):
    c = server_client
    plain = c.post("/api/auth/login", json={"key": "segreto-12345"})
    assert "secure" not in plain.headers["set-cookie"].lower()
    # il proxy TLS raggiunge il container in HTTP e dichiara lo schema originale
    proxied = c.post("/api/auth/login", json={"key": "segreto-12345"},
                     headers={"X-Forwarded-Proto": "https"})
    assert "secure" in proxied.headers["set-cookie"].lower()
    chained = c.post("/api/auth/login", json={"key": "segreto-12345"},
                     headers={"X-Forwarded-Proto": "HTTPS, http"})
    assert "secure" in chained.headers["set-cookie"].lower()


def test_shutdown_allowed_by_launcher_flag(monkeypatch):
    monkeypatch.setenv("MAKPHI_ALLOW_SHUTDOWN", "1")
    assert auth.shutdown_allowed()
    monkeypatch.setenv("MAKPHI_ALLOW_SHUTDOWN", "0")
    assert not auth.shutdown_allowed()
