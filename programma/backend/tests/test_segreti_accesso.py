"""Segreti e accesso: chiave MODELLER mascherata ovunque esca dal backend, ambiente dei programmi esterni senza i
segreti del server, freno ai tentativi di accesso (anche per la chiave come Bearer e dietro un reverse proxy fidato),
chiave d'accesso troppo corta, sessioni revocabili (logout-all, epoca rovinata), interprete di MODELLER bloccato in
modalità server (anche nella verifica da riga di comando), limite delle email di prova e degli avvisi di fine job."""

from __future__ import annotations

import os
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from makphi import selftest as selftest_cli
from makphi.api import auth as auth_api
from makphi.app import create_app
from makphi.config import Paths, SettingsStore, UserSettings
from makphi.core import auth, sensitive
from makphi.core import jobs as jobs_module
from makphi.core.db import Database
from makphi.core.errors import AppError
from makphi.core.jobs import JobManager, default_env, set_subprocess_env
from makphi.engines import quality, tools, versions
from makphi.services import licenses as lic
from makphi.services.notify import NotifyService, wait_text

from .conftest import until

KEY = "MODELSEGRETA9"
ACCESS = "chiave-d-accesso-di-prova-24"


# ------------------------------------------------------------------------------------------ mascheratura
def test_mask_and_redact():
    assert sensitive.mask(KEY) == lic.mask(KEY) == "MOD••••••••A9"
    text = f"ModellerError: check_lice_E> Invalid license key: {KEY} (di nuovo {KEY.lower()})"
    out = sensitive.redact(text, [KEY, None, ""])
    assert KEY not in out and KEY.lower() not in out and out.count("MOD••••••••A9") == 2
    assert sensitive.redact("niente da nascondere", [KEY]) == "niente da nascondere"
    assert sensitive.redact("chiave ab nel testo", ["ab"]) == "chiave ab nel testo"  # troppo corta: non si tocca
    assert sensitive.redactor([]) is None


class _Probe:
    """subprocess.run di engines/tools simulato: MODELLER che rifiuta la chiave e la ripete nel messaggio."""

    def __init__(self) -> None:
        self.envs: list[dict] = []

    def __call__(self, cmd, **kwargs):
        env = kwargs.get("env") or {}
        self.envs.append(env)
        key = env.get("MAKPHI_MODELLER_KEY", "")
        return SimpleNamespace(returncode=1, stdout="",
                               stderr=f"Traceback...\nModellerError: check_lice_E> Invalid license key: {key}\n")


@pytest.fixture
def probe(monkeypatch):
    fake = _Probe()
    monkeypatch.setattr(tools.subprocess, "run", fake)
    tools.invalidate_modeller_cache()
    yield fake
    tools.invalidate_modeller_cache()


def test_modeller_probe_masks_active_and_candidate_keys(probe, monkeypatch):
    monkeypatch.delenv("MODELLER_KEY", raising=False)
    set_subprocess_env("MAKPHI_MODELLER_KEY", KEY)
    info = tools.detect_modeller("", force=True)
    errors = [a["error"] for a in info["attempts"]]
    assert errors and all(KEY not in e for e in errors) and any("MOD••••••••A9" in e for e in errors)
    candidate = "MODELPROVA77X"
    trial = tools.detect_modeller("", license_key=candidate)
    assert all(candidate not in a["error"] for a in trial["attempts"])
    assert any(sensitive.mask(candidate) in a["error"] for a in trial["attempts"])


def test_system_info_selftest_and_licenses_never_show_the_key(client, probe, monkeypatch):
    monkeypatch.delenv("MODELLER_KEY", raising=False)
    set_subprocess_env("MAKPHI_MODELLER_KEY", KEY)
    info = client.get("/api/system/info?refresh=true")
    assert info.status_code == 200 and KEY not in info.text and "MOD••••••••A9" in info.text
    assert KEY not in client.get("/api/licenses").text
    r = client.put("/api/licenses/modeller", json={"key": "MODELNUOVA55"})
    assert r.status_code == 422 and "MODELNUOVA55" not in r.text
    # verifica dell'installazione (job selftest): il passo MODELLER riporta il messaggio mascherato
    from makphi.services.selftest import SelfTestService

    ctx = SimpleNamespace(settings=UserSettings())
    with pytest.raises(AppError) as exc:
        SelfTestService._modeller(ctx, Path("."))
    assert KEY not in exc.value.message and "MOD••••••••A9" in exc.value.message


def test_secrets_are_not_passed_to_external_programs(monkeypatch):
    for name, value in {"MAKPHI_ACCESS_KEY": ACCESS, "MAKPHI_SMTP_PASSWORD": "pw-posta",
                        "MAKPHI_SMTP_USER": "utente", "MAKPHI_SMTP_HOST": "smtp.example.org",
                        "MAKPHI_SMTP_FROM": "a@example.org", "MODELLER_KEY": "MODELDAENV12"}.items():
        monkeypatch.setenv(name, value)
    set_subprocess_env("MAKPHI_MODELLER_KEY", KEY)
    env = default_env()
    assert not {k for k in env if k.upper().startswith("MAKPHI_SMTP_")}
    assert "MAKPHI_ACCESS_KEY" not in env and "MODELLER_KEY" not in env
    assert env["MAKPHI_MODELLER_KEY"] == KEY  # MODELLER riceve ancora la chiave attiva
    assert env.get("PATH") == os.environ.get("PATH") and env["PYTHONIOENCODING"] == "utf-8"
    # anche i processi lanciati senza job: sonde delle versioni e passi di qualità fuori da un job
    seen = []

    def fake_run(cmd, **kwargs):
        seen.append(kwargs.get("env"))
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(versions, "_cache", {})
    monkeypatch.setattr(versions.subprocess, "run", fake_run)
    monkeypatch.setattr(quality.subprocess, "run", fake_run)
    versions._probe("dssp", sys.executable)
    quality._run([sys.executable, "--version"], Path("."), None, 10)
    assert len(seen) == 2
    for value in seen:
        assert value is not None and "MAKPHI_ACCESS_KEY" not in value and "MAKPHI_SMTP_PASSWORD" not in value


@pytest.fixture
def manager(tmp_path):
    db = Database(tmp_path / "data" / "t.db")
    mgr = JobManager(db, tmp_path / "data" / "jobs", workers=2, settings_provider=UserSettings,
                     scratch_dir=tmp_path / "scratch")
    yield mgr
    mgr.shutdown()
    db.close()


def _wait(mgr, job_id, timeout=30):
    start = time.time()
    while time.time() - start < timeout:
        job = mgr.get(job_id)
        if job["status"] in ("completed", "failed", "cancelled") and not mgr.busy(job_id):
            return job
        time.sleep(0.05)
    raise AssertionError("job non terminato")


def test_external_process_environment_and_output_in_a_job(manager, monkeypatch):
    monkeypatch.setenv("MAKPHI_ACCESS_KEY", ACCESS)
    monkeypatch.setenv("MAKPHI_SMTP_PASSWORD", "pw-posta")
    set_subprocess_env("MAKPHI_MODELLER_KEY", KEY)
    script = ("import os\n"
              "print('chiave d accesso:', os.environ.get('MAKPHI_ACCESS_KEY', 'assente'))\n"
              "print('password:', os.environ.get('MAKPHI_SMTP_PASSWORD', 'assente'))\n"
              "print('check_lice_E> Invalid license key:', os.environ.get('MAKPHI_MODELLER_KEY'))\n")
    outputs = []

    def handler(ctx, _params):
        outputs.append(ctx.run([sys.executable, "-c", script], cpu=False).output)
        return {}

    manager.register("prova", handler)
    job = manager.submit("prova", {})
    assert _wait(manager, job["id"])["status"] == "completed"
    log = manager.read_log(job["id"])["text"]
    for text in (log, outputs[0]):
        assert "chiave d accesso: assente" in text and "password: assente" in text
        assert KEY not in text and "MOD••••••••A9" in text  # chiave MODELLER ripetuta: mascherata


# ------------------------------------------------------------------------------------------ tentativi di accesso
class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def test_login_limiter_serializes_and_slows_down_attempts():
    clock = Clock()
    lim = auth.LoginLimiter(step=0.5, max_delay=10.0, window=900.0, clock=clock)
    assert lim.begin("1.2.3.4") is None
    assert lim.begin("1.2.3.4") is not None  # in parallelo: rifiutato
    assert lim.begin("5.6.7.8") is None  # altri indirizzi no
    lim.end("5.6.7.8")
    assert lim.failure("1.2.3.4") == 0.5
    lim.end("1.2.3.4")
    assert lim.begin("1.2.3.4") == pytest.approx(0.5)  # prima della fine dell'attesa: rifiutato anche da solo
    clock.now += 0.6
    for n in range(2, 30):
        assert lim.begin("1.2.3.4") is None
        delay = lim.failure("1.2.3.4")
        assert delay == min(10.0, 0.5 * n)
        lim.end("1.2.3.4")
        clock.now += delay
    assert lim.begin("1.2.3.4") is None
    lim.success("1.2.3.4")
    lim.end("1.2.3.4")
    assert len(lim) == 0  # niente errori recenti: indirizzo dimenticato
    # gli errori scadono dopo la finestra
    lim.begin("9.9.9.9")
    lim.failure("9.9.9.9")
    lim.end("9.9.9.9")
    clock.now += 901
    assert lim.begin("9.9.9.9") is None
    assert lim.failure("9.9.9.9") == 0.5
    lim.end("9.9.9.9")


def test_login_limiter_memory_is_bounded():
    clock = Clock()
    lim = auth.LoginLimiter(max_clients=50, window=900.0, clock=clock)
    for i in range(500):
        address = f"10.0.{i // 256}.{i % 256}"
        assert lim.begin(address) is None
        lim.failure(address)
        lim.end(address)
        clock.now += 0.01
    assert len(lim) <= 50
    clock.now += 1000  # scaduti: alla prossima pulizia spariscono tutti
    lim.begin("10.9.9.9")
    lim._prune(clock.now)
    assert len(lim) == 1


@pytest.fixture
def server(tmp_path, monkeypatch):
    monkeypatch.delenv("MAKPHI_ALLOW_SHUTDOWN", raising=False)
    data = tmp_path / "data"
    app = create_app(Paths(data), frontend_dist=None, auth_config=auth.AuthConfig(enabled=True, key=ACCESS))
    with TestClient(app) as c:
        yield c


def test_parallel_login_attempts_are_rejected_while_one_is_running(server):
    limiter = server.app.state.login_limiter
    limiter.step = 1.0
    results = {}

    def wrong():
        results["first"] = server.post("/api/auth/login", json={"key": "sbagliata"})

    worker = threading.Thread(target=wrong)
    worker.start()
    until(lambda: limiter._clients.get("testclient") and limiter._clients["testclient"].busy, timeout=10,
          what="primo tentativo in corso")
    parallel = server.post("/api/auth/login", json={"key": ACCESS})  # anche con la chiave giusta: aspetta il turno
    worker.join()
    assert parallel.status_code == 429
    error = parallel.json()["error"]
    assert error["code"] == "rate_limited" and "riprova tra" in error["message"]
    assert results["first"].status_code == 401 and results["first"].json()["error"]["code"] == "auth_failed"
    ok = server.post("/api/auth/login", json={"key": ACCESS})  # finita l'attesa
    assert ok.status_code == 200 and auth.COOKIE in ok.cookies
    assert len(limiter) == 0


def test_bearer_key_goes_through_the_limiter(server, monkeypatch):
    """La chiave come Bearer non è un modo per provare chiavi in parallelo senza attese: /api/auth/status (esente dal
    controllo dell'accesso) risponde attraverso lo stesso freno del login."""
    limiter = server.app.state.login_limiter
    clock = Clock()  # fermo: l'attesa dopo il primo errore non finisce durante la prova
    limiter.clock = clock
    cfg = server.app.state.auth
    compared = []
    real_check = cfg.check_key
    monkeypatch.setattr(cfg, "check_key", lambda key: compared.append(key) or real_check(key))
    results = []

    def guess(n):
        results.append(server.get("/api/auth/status", headers={"Authorization": f"Bearer tentativo-{n}"}))

    workers = [threading.Thread(target=guess, args=(n,)) for n in range(40)]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join()
    codes = sorted(r.status_code for r in results)
    assert codes == [200] + [429] * 39 and len(compared) == 1  # una chiave sola confrontata
    assert next(r for r in results if r.status_code == 200).json() == {"enabled": True, "authenticated": False}
    assert all(r.json()["error"]["code"] == "rate_limited" for r in results if r.status_code == 429)
    # durante l'attesa nemmeno la chiave giusta si confronta: la risposta non dice quale chiave è giusta
    r = server.get("/api/auth/status", headers={"Authorization": f"Bearer {ACCESS}"})
    assert r.status_code == 429 and "riprova tra 1 s" in r.json()["error"]["message"] and len(compared) == 1
    clock.now += 0.6
    r = server.get("/api/auth/status", headers={"Authorization": f"Bearer {ACCESS}"})
    assert r.status_code == 200 and r.json() == {"enabled": True, "authenticated": True}
    assert len(limiter) == 1  # l'errore resta: una chiave giusta come Bearer non azzera gli errori


def test_bearer_key_on_protected_routes(server):
    limiter = server.app.state.login_limiter
    clock = Clock()
    limiter.clock = clock
    session = server.post("/api/auth/login", json={"key": ACCESS}).cookies[auth.COOKIE]
    server.cookies.clear()
    wrong = server.get("/api/projects", headers={"Authorization": "Bearer sbagliata"})
    assert wrong.status_code == 401 and wrong.json()["error"]["code"] == "auth_required"
    for method, url in (("get", "/api/projects"), ("post", "/api/auth/logout-all")):
        blocked = getattr(server, method)(url, headers={"Authorization": f"Bearer {ACCESS}"})
        assert blocked.status_code == 429 and blocked.json()["error"]["code"] == "rate_limited", (url, blocked.text)
    assert server.post("/api/auth/logout-all", headers={"Authorization": "Bearer sbagliata"}).status_code == 429
    # una sessione valida non passa mai dal freno
    server.cookies.set(auth.COOKIE, session)
    assert server.get("/api/projects").status_code == 200
    server.cookies.clear()
    clock.now += 0.6
    # una chiave giusta non occupa l'indirizzo: le richieste in parallelo degli script passano tutte
    results = []
    workers = [threading.Thread(target=lambda: results.append(
        server.get("/api/projects", headers={"Authorization": f"Bearer {ACCESS}"}).status_code)) for _ in range(20)]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join()
    assert results == [200] * 20
    # gli errori si sommano a quelli del login: la seconda chiave sbagliata fa attendere 1 s
    assert server.post("/api/auth/logout-all", headers={"Authorization": "Bearer ancora-sbagliata"}).status_code == 401
    r = server.post("/api/auth/login", json={"key": ACCESS})
    assert r.status_code == 429 and "riprova tra 1 s" in r.json()["error"]["message"]
    clock.now += 1.1
    assert server.post("/api/auth/login", json={"key": ACCESS}).status_code == 200


def test_limiter_try_key():
    clock = Clock()
    lim = auth.LoginLimiter(step=0.5, max_delay=10.0, window=900.0, clock=clock)
    assert lim.try_key("1.2.3.4", lambda: True) == (True, None) and len(lim) == 0  # niente memoria
    assert lim.try_key("1.2.3.4", lambda: False) == (False, None)
    assert lim.try_key("1.2.3.4", lambda: pytest.fail("confrontata durante l'attesa")) == (False, pytest.approx(0.5))
    assert lim.try_key("5.6.7.8", lambda: True) == (True, None)  # altri indirizzi no
    assert lim.begin("1.2.3.4") == pytest.approx(0.5)  # anche il login aspetta
    clock.now += 0.5
    assert lim.begin("1.2.3.4") is None
    assert lim.try_key("1.2.3.4", lambda: pytest.fail("confrontata durante un login")) == (False, 1.0)
    lim.end("1.2.3.4")
    assert lim.try_key("1.2.3.4", lambda: False) == (False, None)
    assert lim._clients["1.2.3.4"].until == pytest.approx(clock.now + 1.0)  # secondo errore: 1 s


def test_client_address_behind_a_trusted_proxy():
    trusted = auth.trusted_proxies("172.16.0.0/12, 10.88.0.1")
    assert auth.client_address("172.18.0.1", "203.0.113.7", trusted) == "203.0.113.7"
    # quello che il client scrive a sinistra non conta: vale l'ultimo indirizzo aggiunto dal proxy
    assert auth.client_address("172.18.0.1", "198.51.100.5, 203.0.113.7", trusted) == "203.0.113.7"
    assert auth.client_address("172.18.0.1", "203.0.113.7, 10.88.0.1", trusted) == "203.0.113.7"  # due proxy
    assert auth.client_address("172.18.0.1", "203.0.113.7:5555", trusted) == "203.0.113.7"
    assert auth.client_address("::ffff:172.18.0.1", "[2001:db8::1]:4321", trusted) == "2001:db8::1"
    assert auth.client_address("172.18.0.1", "unknown", trusted) == "172.18.0.1"  # proxy configurato male
    assert auth.client_address("172.18.0.1", "", trusted) == "172.18.0.1"
    # da un indirizzo che non è un proxy fidato, o senza proxy fidati, X-Forwarded-For non conta
    assert auth.client_address("198.51.100.9", "203.0.113.7", trusted) == "198.51.100.9"
    assert auth.client_address("172.18.0.1", "203.0.113.7", ()) == "172.18.0.1"
    assert auth.client_address("testclient", "203.0.113.7", trusted) == "testclient"
    assert auth.trusted_proxies("") == () and len(auth.trusted_proxies("::1 127.0.0.1;10.0.0.0/8")) == 3
    with pytest.raises(auth.AccessKeyError, match="«caddy»"):
        auth.trusted_proxies("172.18.0.1, caddy")


def test_login_limit_per_client_behind_a_trusted_proxy(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(auth_api, "_warned", set())
    monkeypatch.setenv("MAKPHI_TRUSTED_PROXIES", "172.16.0.0/12")
    app = create_app(Paths(tmp_path / "data"), frontend_dist=None,
                     auth_config=auth.AuthConfig(enabled=True, key=ACCESS))
    with TestClient(app, client=("172.18.0.1", 40000)) as proxy:
        app.state.login_limiter.clock = Clock()
        attacker = {"X-Forwarded-For": "203.0.113.66"}
        assert proxy.get("/api/projects", headers={**attacker, "Authorization": "Bearer sbagliata"}).status_code == 401
        assert proxy.post("/api/auth/login", json={"key": ACCESS}, headers=attacker).status_code == 429
        # chi scrive a sinistra l'indirizzo di un altro resta sé stesso
        spoofed = {"X-Forwarded-For": "198.51.100.5, 203.0.113.66"}
        assert proxy.post("/api/auth/login", json={"key": ACCESS}, headers=spoofed).status_code == 429
        # gli altri client dietro lo stesso proxy non sono bloccati
        assert proxy.post("/api/auth/login", json={"key": ACCESS},
                          headers={"X-Forwarded-For": "198.51.100.5"}).status_code == 200
    assert "MAKPHI_TRUSTED_PROXIES=" not in capsys.readouterr().out
    # senza proxy fidati: un unico indirizzo per tutti, e un avviso nel log (una volta) con quello da scrivere
    monkeypatch.setenv("MAKPHI_TRUSTED_PROXIES", "")
    app = create_app(Paths(tmp_path / "data2"), frontend_dist=None,
                     auth_config=auth.AuthConfig(enabled=True, key=ACCESS))
    with TestClient(app, client=("172.18.0.1", 40000)) as proxy:
        app.state.login_limiter.clock = Clock()
        assert proxy.get("/api/projects", headers={"X-Forwarded-For": "203.0.113.66",
                                                   "Authorization": "Bearer sbagliata"}).status_code == 401
        assert proxy.post("/api/auth/login", json={"key": ACCESS},
                          headers={"X-Forwarded-For": "198.51.100.5"}).status_code == 429
    out = capsys.readouterr().out
    assert out.count("MAKPHI_TRUSTED_PROXIES=172.18.0.1 in server.env") == 1
    # un valore sbagliato ferma l'avvio, con il motivo
    monkeypatch.setenv("MAKPHI_TRUSTED_PROXIES", "proxy.example.org")
    app = create_app(Paths(tmp_path / "data3"), frontend_dist=None,
                     auth_config=auth.AuthConfig(enabled=True, key=ACCESS))
    with pytest.raises(auth.AccessKeyError):
        with TestClient(app):
            pass
    assert "ERRORE: MAKPHI_TRUSTED_PROXIES contiene «proxy.example.org»" in capsys.readouterr().out


def test_short_access_key_stops_the_startup(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("MAKPHI_AUTH", "on")
    monkeypatch.setenv("MAKPHI_ACCESS_KEY", "corta-15-chars!")
    with pytest.raises(auth.AccessKeyError) as exc:
        auth.load(tmp_path)
    message = str(exc.value)
    assert "15 caratteri" in message and "almeno 16" in message and "server.env" in message
    app = create_app(Paths(tmp_path / "data"), frontend_dist=None)
    with pytest.raises(auth.AccessKeyError):
        with TestClient(app):
            pass
    assert "ERRORE: MAKPHI_ACCESS_KEY è troppo corta" in capsys.readouterr().out
    monkeypatch.setenv("MAKPHI_ACCESS_KEY", "x" * 16)
    cfg = auth.load(tmp_path)
    assert cfg.enabled and cfg.key == "x" * 16 and (tmp_path / "session_epoch").read_text().strip() == "0"


# ------------------------------------------------------------------------------------------ sessioni revocabili
def test_logout_all_revokes_every_session(server, tmp_path):
    epoch = tmp_path / "data" / auth.EPOCH_FILE
    assert epoch.read_text().strip() == "0"
    first = server.post("/api/auth/login", json={"key": ACCESS}).cookies[auth.COOKIE]
    other = server.app.state.auth.issue(now=time.time() + 5)  # sessione di un altro dispositivo
    assert server.get("/api/projects").status_code == 200
    server.cookies.clear()
    assert server.post("/api/auth/logout-all").status_code == 401  # senza sessione
    server.cookies.set(auth.COOKIE, first)
    r = server.post("/api/auth/logout-all")
    assert r.status_code == 200 and r.json() == {"ok": True}
    assert auth.COOKIE in r.headers["set-cookie"] and "max-age=0" in r.headers["set-cookie"].lower()
    assert epoch.read_text().strip() == "1"
    for token in (first, other):
        server.cookies.clear()
        server.cookies.set(auth.COOKIE, token)
        assert server.get("/api/projects").status_code == 401
        assert server.get("/api/auth/status").json() == {"enabled": True, "authenticated": False}
    server.cookies.clear()
    # gli script con la chiave continuano a funzionare, e un nuovo accesso dà una sessione valida
    assert server.get("/api/projects", headers={"Authorization": f"Bearer {ACCESS}"}).status_code == 200
    fresh = server.post("/api/auth/login", json={"key": ACCESS}).cookies[auth.COOKIE]
    assert server.get("/api/projects").status_code == 200
    assert server.post("/api/auth/logout-all", headers={"Authorization": f"Bearer {ACCESS}"}).status_code == 200
    assert epoch.read_text().strip() == "2"
    # al riavvio l'epoca si rilegge: le sessioni revocate restano revocate
    restarted = auth.AuthConfig(enabled=True, key=ACCESS)
    restarted.attach(tmp_path / "data")
    assert restarted.epoch == 2 and not restarted.verify(fresh) and not restarted.verify(first)
    assert restarted.verify(restarted.issue())


def test_epoch_zero_keeps_the_sessions_of_previous_versions():
    import hashlib
    import hmac

    cfg = auth.AuthConfig(enabled=True, key=ACCESS)
    expires = int(time.time() + 3600)
    legacy_secret = hashlib.sha256(b"bioloom-session:" + ACCESS.encode()).digest()
    token = f"{expires}.{hmac.new(legacy_secret, str(expires).encode(), hashlib.sha256).hexdigest()}"
    assert cfg.verify(token)  # cookie emesso dalla versione precedente: valido dopo l'aggiornamento
    cfg.revoke_all()
    assert not cfg.verify(token)


def test_damaged_epoch_file_closes_every_session(tmp_path, monkeypatch, capsys):
    """Un file dell'epoca rovinato o illeggibile non riporta l'epoca a 0: le sessioni revocate restano revocate."""
    cfg = auth.AuthConfig(enabled=True, key=ACCESS)
    cfg.attach(tmp_path)
    stolen = cfg.issue()  # sessione all'epoca 0 (un dispositivo rubato), poi revocata due volte
    cfg.revoke_all()
    cfg.revoke_all()
    current = cfg.issue()
    path = tmp_path / auth.EPOCH_FILE
    for text in (" 2 \n", "2"):
        path.write_text(text, encoding="utf-8")
        restarted = auth.AuthConfig(enabled=True, key=ACCESS)
        restarted.attach(tmp_path)
        assert restarted.epoch == 2 and restarted.verify(current) and not restarted.verify(stolen)
    capsys.readouterr()
    for text in ("2\x00garbage", "-1", "", "abc", "1e3"):
        path.write_text(text, encoding="utf-8")
        restarted = auth.AuthConfig(enabled=True, key=ACCESS)
        restarted.attach(tmp_path)
        assert restarted.epoch not in (0, 2), text
        assert not restarted.verify(stolen) and not restarted.verify(current)
        assert path.read_text(encoding="utf-8").strip() == str(restarted.epoch)  # salvata: vale anche al riavvio
        assert "ne è stata scelta una nuova" in capsys.readouterr().out
    # illeggibile (permessi): un'epoca nuova almeno in memoria
    real_read = Path.read_text

    def unreadable(self, *args, **kwargs):
        if self.name == auth.EPOCH_FILE:
            raise PermissionError(13, "Permesso negato")
        return real_read(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", unreadable)
    restarted = auth.AuthConfig(enabled=True, key=ACCESS)
    restarted.attach(tmp_path)
    assert restarted.epoch != 0 and not restarted.verify(stolen) and not restarted.verify(current)
    assert "illeggibile" in capsys.readouterr().out
    monkeypatch.undo()
    # file assente (prima creazione, o aggiornamento da una versione precedente): epoca 0, come prima
    path.unlink()
    fresh = auth.AuthConfig(enabled=True, key=ACCESS)
    fresh.attach(tmp_path)
    assert fresh.epoch == 0 and path.read_text(encoding="utf-8").strip() == "0"


def test_logout_all_in_local_mode(client):
    r = client.post("/api/auth/logout-all")
    assert r.status_code == 400 and r.json()["error"]["code"] == "auth_disabled"
    assert client.get("/api/auth/status").json() == {"enabled": False, "authenticated": True}


# ------------------------------------------------------------------------------------------ interprete di MODELLER
def test_modeller_python_is_set_by_the_server_in_server_mode(server, monkeypatch, tmp_path):
    monkeypatch.setenv("MAKPHI_MODELLER_PYTHON", "/opt/modeller/bin/python")
    assert server.post("/api/auth/login", json={"key": ACCESS}).status_code == 200
    current = server.get("/api/settings").json()
    assert current["modeller_python"] == "/opt/modeller/bin/python"
    same = server.put("/api/settings", json={**current, "max_workers": 3})  # la pagina rimanda il valore in uso
    assert same.status_code == 200, same.text
    assert same.json()["modeller_python"] == "/opt/modeller/bin/python" and same.json()["max_workers"] == 3
    other = server.put("/api/settings", json={"modeller_python": "/tmp/altro/python"})
    assert other.status_code == 403
    error = other.json()["error"]
    assert error["code"] == "modeller_python_locked" and "MAKPHI_MODELLER_PYTHON" in error["message"]
    assert server.app.state.core.settings._settings.modeller_python == ""  # niente salvato
    # in locale vale il valore salvato
    path = tmp_path / "settings.json"
    path.write_text('{"modeller_python": "/usr/bin/python3"}', encoding="utf-8")
    assert SettingsStore(path).get().modeller_python == "/usr/bin/python3"
    assert SettingsStore(path, server=True).get().modeller_python == "/opt/modeller/bin/python"


def test_selftest_command_uses_the_server_settings(tmp_path, monkeypatch):
    """`makphi.sh verifica` (python -m makphi.selftest nel container) usa l'interprete di MODELLER del server."""
    data = tmp_path / "data"
    data.mkdir()
    (data / "settings.json").write_text('{"modeller_python": "/usr/bin/python3"}', encoding="utf-8")
    monkeypatch.setenv("MAKPHI_DATA_DIR", str(data))
    monkeypatch.setenv("MAKPHI_MODELLER_PYTHON", "/opt/modeller/bin/python")
    seen = []

    class Stop(Exception):
        pass

    real = selftest_cli.SettingsStore

    def spy(path, **kwargs):
        seen.append(real(path, **kwargs).get().modeller_python)
        raise Stop

    monkeypatch.setattr(selftest_cli, "SettingsStore", spy)
    monkeypatch.setenv("MAKPHI_AUTH", "on")
    with pytest.raises(Stop):
        selftest_cli.main()
    monkeypatch.setenv("MAKPHI_AUTH", "off")
    with pytest.raises(Stop):
        selftest_cli.main()
    assert seen == ["/opt/modeller/bin/python", "/usr/bin/python3"]


# ------------------------------------------------------------------------------------------ email di prova
class _Smtp:
    sent: list = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def login(self, user, password):
        pass

    def send_message(self, msg):
        _Smtp.sent.append(msg["To"])


@pytest.fixture
def mailer(tmp_path):
    class Jobs:
        def on_finish(self, _fn):
            pass

    service = NotifyService(tmp_path, tmp_path / "licenses", Jobs(), sender_factory=lambda cfg: _Smtp())
    service.file.parent.mkdir(parents=True)
    service.file.write_text("MAKPHI_SMTP_HOST=smtp.example.org\nMAKPHI_SMTP_FROM=makphi@example.org\n",
                            encoding="utf-8")
    clock = Clock()
    service.clock = clock
    _Smtp.sent = []
    return service, clock


def test_test_email_rate_limit(mailer, monkeypatch):
    for name in ("MAKPHI_SMTP_HOST", "MAKPHI_SMTP_PASSWORD"):
        monkeypatch.delenv(name, raising=False)
    service, clock = mailer
    assert service.send_test("utente@example.org")["sent"]
    with pytest.raises(AppError) as exc:
        service.send_test("utente@example.org")
    assert exc.value.status == 429 and exc.value.code == "rate_limited"
    assert exc.value.message == "È appena stata inviata un'email di prova: riprova tra 30 s."
    with pytest.raises(AppError) as bad:
        service.send_test("non-email")  # indirizzo non valido: errore suo, non conta
    assert bad.value.code == "invalid_email"
    for _ in range(4):
        clock.now += 31
        service.send_test("utente@example.org")
    assert len(_Smtp.sent) == 5
    clock.now += 31
    with pytest.raises(AppError) as hourly:
        service.send_test("utente@example.org")
    assert hourly.value.status == 429 and "Limite di 5 email di prova all'ora" in hourly.value.message
    assert hourly.value.message.endswith("riprova tra 58 min.")  # la prima delle cinque esce dall'ora fra 3445 s
    clock.now += 3600
    assert service.send_test("utente@example.org")["sent"] and len(_Smtp.sent) == 6


def test_test_email_limit_counts_failed_attempts_too(mailer, monkeypatch):
    monkeypatch.delenv("MAKPHI_SMTP_HOST", raising=False)
    service, clock = mailer
    service.retry_delays = ()
    service._connect = lambda cfg: (_ for _ in ()).throw(ConnectionRefusedError("giù"))
    with pytest.raises(AppError) as first:
        service.send_test("utente@example.org")
    assert first.value.code == "notify_failed"
    with pytest.raises(AppError) as second:
        service.send_test("utente@example.org")
    assert second.value.code == "rate_limited"
    assert wait_text(25.2) == "26 s" and wait_text(600) == "10 min" and wait_text(0) == "1 s"


def test_test_email_rate_limit_through_the_api(client, monkeypatch):
    monkeypatch.delenv("MAKPHI_SMTP_HOST", raising=False)
    service = client.app.state.core.notify
    service.file.parent.mkdir(parents=True, exist_ok=True)
    service.file.write_text("MAKPHI_SMTP_HOST=smtp.example.org\nMAKPHI_SMTP_FROM=makphi@example.org\n",
                            encoding="utf-8")
    service._connect = lambda cfg: _Smtp()
    assert client.post("/api/notify/test", json={"to": "utente@example.org"}).status_code == 200
    r = client.post("/api/notify/test", json={"to": "utente@example.org"})
    assert r.status_code == 429 and r.json()["error"]["code"] == "rate_limited"
    assert "riprova tra" in r.json()["error"]["message"]


class _FinishedJobs:
    """JobManager minimo per gli avvisi di fine job: parametri dei job, log dei job (note scritte), nessun elemento."""

    def __init__(self) -> None:
        self.params: dict[str, dict] = {}
        self.notes: list[tuple[str, str]] = []
        self.db = SimpleNamespace(query_one=lambda _sql, args: {"params": self.params.get(args[0], {})},
                                  query=lambda *_args: [])

    def on_finish(self, _fn):
        pass

    def busy(self, _job_id):
        return False

    def log_if_present(self, job_id, note):
        self.notes.append((job_id, note))
        return True

    def get(self, job_id, include_result=False):
        return {"kind": "pipeline", "title": f"Pipeline {job_id}", "started_at": None, "finished_at": None,
                "error": None}


def test_job_notices_are_rate_limited(tmp_path, monkeypatch):
    """Gli avvisi di fine job non aggirano il limite delle email di prova: al più N all'ora allo stesso indirizzo e
    M per tutto il server; oltre, niente invio e il motivo nel log del job."""
    monkeypatch.delenv("MAKPHI_SMTP_HOST", raising=False)
    jobs = _FinishedJobs()
    service = NotifyService(tmp_path, tmp_path / "licenses", jobs, sender_factory=lambda cfg: _Smtp())
    service.file.parent.mkdir(parents=True)
    service.file.write_text("MAKPHI_SMTP_HOST=smtp.example.org\nMAKPHI_SMTP_FROM=makphi@example.org\n",
                            encoding="utf-8")
    clock = Clock()
    service.clock = clock
    service.notice_per_recipient, service.notice_per_hour = 3, 5
    _Smtp.sent = []

    def finish(job_id, to, status="cancelled"):
        jobs.params[job_id] = {"notify_email": to}
        service.on_finish(job_id, status)

    for i, to in enumerate(("vittima@example.org", "vittima@example.org", "Vittima@Example.org",
                            "vittima@example.org")):
        finish(f"a{i}", to)
    until(lambda: len(jobs.notes) == 4, timeout=10, what="avvisi allo stesso indirizzo")
    assert len(_Smtp.sent) == 3
    assert dict(jobs.notes)["a3"] == ("Email di avviso a vittima@example.org non inviata: limite di 3 avvisi all'ora "
                                      "allo stesso indirizzo raggiunto.")
    for i in range(3):
        finish(f"b{i}", f"altro{i}@example.org", "failed")
    until(lambda: len(jobs.notes) == 7, timeout=10, what="avvisi ad altri indirizzi")
    assert len(_Smtp.sent) == 5
    assert dict(jobs.notes)["b2"].endswith("limite di 5 avvisi all'ora per tutto il server raggiunto.")
    clock.now += 3601  # passata l'ora
    finish("c0", "vittima@example.org", "completed")
    until(lambda: len(jobs.notes) == 8, timeout=10, what="avviso dopo un'ora")
    assert len(_Smtp.sent) == 6 and dict(jobs.notes)["c0"] == "Email di avviso inviata a vittima@example.org"


def test_subprocess_env_strips_case_insensitive_names():
    env = sensitive.subprocess_env({"makphi_access_key": "x", "Makphi_Smtp_Password": "y", "PATH": "/bin",
                                    "MAKPHI_DATA_DIR": "/data"})
    assert env == {"PATH": "/bin", "MAKPHI_DATA_DIR": "/data"}
    assert jobs_module.output_secrets({"MAKPHI_MODELLER_KEY": "MODELXYZ1"})[-1] == "MODELXYZ1"
