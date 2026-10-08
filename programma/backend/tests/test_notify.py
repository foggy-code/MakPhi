"""Avviso per email alla fine delle pipeline (services/notify.py), con un server SMTP finto in locale: nessun messaggio
esce dal computer.

- configurazione salvata senza mai restituire la password; password fra i segreti da non esportare;
- email di prova, autenticazione rifiutata, nuovi tentativi per gli errori temporanei;
- sottomissione rifiutata senza server di posta o con un indirizzo non valido;
- pipeline con l'avviso: un messaggio alla fine, con esito ed elementi ma senza sequenze né nomi, ed esito nel log;
- modalità server: configurazione solo dalle variabili dell'amministratore.
"""

from __future__ import annotations

import base64
import email
import email.policy
import socketserver
import threading
import time

import pytest

from bioloom.core.errors import AppError
from bioloom.services import notify as notify_module
from bioloom.services.notify import NotifyService, SmtpConfig

from .conftest import HUMAN_UB, wait_job

USER, PASSWORD = "bioloom", "pa55-segreta"


class FakeSmtp(socketserver.ThreadingTCPServer):
    """SMTP minimo: EHLO, AUTH PLAIN, MAIL, RCPT (rifiuta gli indirizzi che iniziano con «rifiuta»), DATA, QUIT."""

    allow_reuse_address = True
    daemon_threads = True

    def __init__(self) -> None:
        super().__init__(("127.0.0.1", 0), _Handler)
        self.messages: list[dict] = []
        self.logins: list[tuple[str, str]] = []


class _Handler(socketserver.StreamRequestHandler):
    def reply(self, line: str) -> None:
        self.wfile.write((line + "\r\n").encode())

    def handle(self) -> None:
        server: FakeSmtp = self.server  # type: ignore[assignment]
        self.reply("220 finto ESMTP")
        mail: dict = {"rcpt": []}
        while True:
            raw = self.rfile.readline()
            if not raw:
                return
            line = raw.decode().rstrip("\r\n")
            cmd = line.split(" ", 1)[0].upper()
            if cmd in ("EHLO", "HELO"):
                self.wfile.write(b"250-finto\r\n250 AUTH PLAIN\r\n")
            elif cmd == "AUTH":
                _, user, password = base64.b64decode(line.split()[2]).decode().split("\0")
                server.logins.append((user, password))
                self.reply("235 ok" if (user, password) == (USER, PASSWORD) else "535 credenziali errate")
            elif cmd == "MAIL":
                mail = {"from": line, "rcpt": []}
                self.reply("250 ok")
            elif cmd == "RCPT":
                if "<rifiuta" in line:
                    self.reply("550 destinatario sconosciuto")
                else:
                    mail["rcpt"].append(line)
                    self.reply("250 ok")
            elif cmd == "DATA":
                self.reply("354 avanti")
                data = []
                while (row := self.rfile.readline().decode()) not in (".\r\n", ""):
                    data.append(row)
                mail["data"] = "".join(data)
                parsed = email.message_from_string(mail["data"], policy=email.policy.default)
                mail["subject"], mail["body"] = str(parsed["Subject"]), parsed.get_content()
                server.messages.append(mail)
                self.reply("250 accettato")
            elif cmd == "QUIT":
                self.reply("221 ciao")
                return
            else:
                self.reply("250 ok")


@pytest.fixture
def smtp():
    server = FakeSmtp()
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield server
    server.shutdown()
    server.server_close()


def configure(client, smtp, **extra):
    body = {"host": "127.0.0.1", "port": smtp.server_address[1], "security": "none", "user": USER,
            "sender": "bioloom@example.org", "password": PASSWORD, **extra}
    r = client.put("/api/notify", json=body)
    assert r.status_code == 200, r.text
    return r.json()


def until(condition, timeout: float = 30.0):
    start = time.time()
    while time.time() - start < timeout:
        value = condition()
        if value:
            return value
        time.sleep(0.05)
    raise AssertionError("condizione non raggiunta in tempo")


# ------------------------------------------------------------------------------------------ configurazione
def test_configuration_is_saved_without_returning_the_password(client, smtp):
    status = client.get("/api/notify").json()
    assert status["configured"] is False and status["password_set"] is False and status["source"] == "local"
    saved = configure(client, smtp, public_url="https://bioloom.example.org/")
    assert saved["configured"] and saved["password_set"] and saved["public_url"] == "https://bioloom.example.org"
    assert PASSWORD not in client.get("/api/notify").text
    core = client.app.state.core
    assert PASSWORD in core.licenses.secrets()  # mai in un pacchetto di riproducibilità
    # cambiare solo la porta lascia la password; clear_password la toglie
    assert client.put("/api/notify", json={"port": 2525}).json()["password_set"] is True
    assert client.put("/api/notify", json={"clear_password": True}).json()["password_set"] is False


@pytest.mark.parametrize("body,field", [({"host": "a b"}, "Server SMTP"), ({"port": 0}, "Porta"),
                                        ({"security": "tls"}, "Sicurezza"), ({"sender": "non-email"}, "Mittente"),
                                        ({"public_url": "ftp://x"}, "Indirizzo di BioLoom")])
def test_invalid_configuration_is_refused_in_italian(client, body, field):
    r = client.put("/api/notify", json=body)
    assert r.status_code == 400 and field in r.json()["error"]["message"]


# ------------------------------------------------------------------------------------------ invio
def test_test_email_is_delivered(client, smtp):
    configure(client, smtp)
    r = client.post("/api/notify/test", json={"to": "utente@example.org"})
    assert r.status_code == 200, r.text
    msg = until(lambda: smtp.messages)[0]
    assert smtp.logins == [(USER, PASSWORD)] and "<utente@example.org>" in msg["rcpt"][0]
    assert msg["subject"] == "BioLoom: email di prova" and "Auto-Submitted: auto-generated" in msg["data"]
    assert "I risultati restano su BioLoom e non sono allegati" in msg["body"]


def test_wrong_password_and_refused_recipient_are_explained(client, smtp):
    configure(client, smtp, password="sbagliata")
    r = client.post("/api/notify/test", json={"to": "utente@example.org"})
    assert r.status_code == 400 and "rifiutato utente o password" in r.json()["error"]["message"]
    configure(client, smtp)
    r = client.post("/api/notify/test", json={"to": "rifiuta@example.org"})
    assert r.status_code == 400 and "rifiutato il destinatario" in r.json()["error"]["message"]
    assert client.post("/api/notify/test", json={"to": "non-email"}).status_code == 400


def test_temporary_errors_are_retried(client, smtp):
    configure(client, smtp)
    service = client.app.state.core.notify
    service.retry_delays = (0.0, 0.0)
    calls = []

    def flaky(cfg):
        calls.append(cfg)
        if len(calls) < 3:
            raise ConnectionRefusedError("rifiutata")
        return notify_module._connect(cfg)

    service._connect = flaky
    service.send_test("utente@example.org")
    assert len(calls) == 3 and len(smtp.messages) == 1
    calls.clear()
    service._connect = lambda cfg: (_ for _ in ()).throw(TimeoutError("scaduto"))
    with pytest.raises(AppError, match="server non raggiungibile"):
        service.send_test("utente@example.org")


# ------------------------------------------------------------------------------------------ pipeline
PIPELINE = {"fasta": f">proteina_riservata\n{HUMAN_UB}\n", "template_mode": "fixed",
            "templates": [{"id": "ZZZZ", "chains": ["A"]}], "parallel": 1, "name": "Prova avviso"}


def test_pipeline_with_notice_needs_a_mail_server_and_a_valid_address(client, smtp):
    r = client.post("/api/pipeline", json={**PIPELINE, "notify_email": "utente@example.org"})
    assert r.status_code == 400 and r.json()["error"]["code"] == "notify_not_configured"
    configure(client, smtp)
    r = client.post("/api/pipeline", json={**PIPELINE, "notify_email": "utente@@example"})
    assert r.status_code == 400 and r.json()["error"]["code"] == "invalid_email"


def test_pipeline_end_sends_one_notice_without_results(client, smtp):
    configure(client, smtp, public_url="https://bioloom.example.org")
    r = client.post("/api/pipeline", json={**PIPELINE, "notify_email": "utente@example.org"})
    assert r.status_code == 200, r.text
    job_id = r.json()["id"]
    job = wait_job(client, job_id)
    msg = until(lambda: smtp.messages)[0]
    data = msg["body"]
    status_word = {"completed": "completata", "failed": "fallita"}[job["status"]]
    assert msg["subject"] == f"BioLoom: pipeline «Prova avviso» {status_word}"
    assert data.startswith(f"La pipeline «Prova avviso» è {status_word}.") and "Elementi: 1 (falliti 1)" in data
    assert f"https://bioloom.example.org/pipeline?job={job_id}" in data and f"Identificativo del job: {job_id}" in data
    assert "proteina_riservata" not in msg["data"] and HUMAN_UB[:20] not in msg["data"]  # niente sequenze né nomi
    log = until(lambda: "Email di avviso" in (t := client.get(f"/api/jobs/{job_id}/log").json()["text"]) and t)
    assert "Email di avviso inviata a utente@example.org" in log
    time.sleep(0.5)
    assert len(smtp.messages) == 1  # un solo avviso per esecuzione


def test_pipeline_without_notice_sends_nothing(client, smtp):
    configure(client, smtp)
    job_id = client.post("/api/pipeline", json=PIPELINE).json()["id"]
    wait_job(client, job_id)
    time.sleep(0.5)
    assert smtp.messages == []


def test_failed_delivery_is_written_in_the_job_log(client, smtp):
    configure(client, smtp)
    client.app.state.core.notify.retry_delays = ()
    client.app.state.core.notify._connect = lambda cfg: (_ for _ in ()).throw(ConnectionRefusedError("giù"))
    job_id = client.post("/api/pipeline", json={**PIPELINE, "notify_email": "utente@example.org"}).json()["id"]
    wait_job(client, job_id)
    log = until(lambda: "Email di avviso" in (t := client.get(f"/api/jobs/{job_id}/log").json()["text"]) and t)
    assert "Email di avviso a utente@example.org non inviata: Invio dell'email non riuscito" in log


# ------------------------------------------------------------------------------------------ modalità server
def test_server_mode_uses_only_the_administrator_settings(tmp_path, monkeypatch):
    monkeypatch.setenv("BIOLOOM_SMTP_HOST", "smtp.universita.example")
    monkeypatch.setenv("BIOLOOM_SMTP_PORT", "465")
    monkeypatch.setenv("BIOLOOM_SMTP_SECURITY", "ssl")
    monkeypatch.setenv("BIOLOOM_SMTP_FROM", "bioloom@universita.example")
    monkeypatch.setenv("BIOLOOM_SMTP_PASSWORD", "dell-amministratore")

    class Jobs:
        def on_finish(self, _fn):
            pass

    service = NotifyService(tmp_path, tmp_path / "licenses", Jobs(), server=True)
    status = service.status()
    assert status["source"] == "server" and status["configured"] and status["host"] == "smtp.universita.example"
    assert status["security"] == "ssl" and status["port"] == 465 and "dell-amministratore" not in str(status)
    assert service.secrets() == ["dell-amministratore"]
    with pytest.raises(AppError, match="amministratore"):
        service.save({"host": "10.0.0.1"})


def test_header_injection_is_impossible():
    with pytest.raises(ValueError):
        SmtpConfig(user="a\r\nBcc: x@y.z")
    with pytest.raises(AppError):
        notify_module.valid_email("a@b.it\r\nBcc: x@y.z")
