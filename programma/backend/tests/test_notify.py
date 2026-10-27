"""Avviso per email alla fine delle pipeline (services/notify.py), con un server SMTP finto in locale: nessun messaggio
esce dal computer.

- configurazione solo da file (licenses/posta.env) o da variabili d'ambiente, mai dall'interfaccia; password mai
  restituita e fra i segreti da non esportare;
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
from types import SimpleNamespace

import pytest

from makphi.core.errors import AppError
from makphi.services import notify as notify_module
from makphi.services.notify import NotifyService, SmtpConfig

from .conftest import HUMAN_UB, until, wait_job

USER, PASSWORD = "makphi", "pa55-segreta"


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


FIELDS = {"host": "MAKPHI_SMTP_HOST", "port": "MAKPHI_SMTP_PORT", "security": "MAKPHI_SMTP_SECURITY",
          "user": "MAKPHI_SMTP_USER", "password": "MAKPHI_SMTP_PASSWORD", "sender": "MAKPHI_SMTP_FROM",
          "public_url": "MAKPHI_PUBLIC_URL"}


def write_config(client, **values):
    """Scrive licenses/posta.env come lo scriverebbe chi gestisce MakPhi (righe NOME=valore, con un commento)."""
    path = client.app.state.core.notify.file
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = ["# posta di prova", ""] + [f"{FIELDS[k]}={v}" for k, v in values.items()]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def configure(client, smtp, **extra):
    values = {"host": "127.0.0.1", "port": smtp.server_address[1], "security": "none", "user": USER,
              "sender": "makphi@example.org", "password": PASSWORD, **extra}
    write_config(client, **values)
    status = client.get("/api/notify").json()
    assert status["configured"] and status["error"] is None, status
    return status


def without_test_limit(client):
    """Toglie il limite delle email di prova (una ogni 30 s): per i test che ne inviano più d'una di seguito."""
    service = client.app.state.core.notify
    service.test_min_interval, service.test_per_hour = 0, 1000
    return service


@pytest.fixture
def notices(client, monkeypatch):
    """Segnali dell'avviso alla fine dei job, al posto delle pause: `threads` raccoglie i thread d'invio creati da
    NotifyService.on_finish, `finished` gli id dei job di cui gli ascoltatori di fine job hanno finito. Questo
    ascoltatore è registrato dopo quello dell'avviso (build_core) e il JobManager li chiama in ordine: quando un id
    compare, la decisione di inviare o no è già presa e l'eventuale thread è già creato."""
    threads: list[threading.Thread] = []

    class Recorded(threading.Thread):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            threads.append(self)

    monkeypatch.setattr(notify_module, "threading", SimpleNamespace(Thread=Recorded, Lock=threading.Lock))
    finished: set[str] = set()
    client.app.state.core.jobs.on_finish(lambda job_id, _status: finished.add(job_id))
    return SimpleNamespace(threads=threads, finished=finished)


# ------------------------------------------------------------------------------------------ configurazione
def test_configuration_comes_only_from_the_file_and_never_returns_the_password(client, smtp):
    status = client.get("/api/notify").json()
    assert status["configured"] is False and status["source"] == "none" and status["file"] == "licenses/posta.env"
    saved = configure(client, smtp, public_url='"https://makphi.example.org/"')  # virgolette tolte, "/" finale anche
    assert saved["source"] == "file" and saved["password_set"] and saved["public_url"] == "https://makphi.example.org"
    assert PASSWORD not in client.get("/api/notify").text
    assert PASSWORD in client.app.state.core.licenses.secrets()  # mai in un pacchetto di riproducibilità
    # dall'interfaccia non si cambia nulla
    assert client.put("/api/notify", json={"host": "10.0.0.1"}).status_code == 405
    assert client.get("/api/notify").json()["host"] == "127.0.0.1"


@pytest.mark.parametrize("values,field", [({"host": "a b"}, "Server SMTP"), ({"port": "0"}, "Porta"),
                                          ({"security": "tls"}, "Sicurezza"), ({"sender": "non-email"}, "Mittente"),
                                          ({"public_url": "ftp://x"}, "Indirizzo di MakPhi")])
def test_invalid_file_is_explained_in_italian(client, values, field):
    write_config(client, **{"host": "smtp.example.org", "sender": "makphi@example.org", **values})
    status = client.get("/api/notify").json()
    assert status["configured"] is False and field in status["error"] and status["error"].startswith("Configurazione")


# ------------------------------------------------------------------------------------------ invio
def test_test_email_is_delivered(client, smtp):
    configure(client, smtp)
    r = client.post("/api/notify/test", json={"to": "utente@example.org"})
    assert r.status_code == 200, r.text
    msg = until(lambda: smtp.messages)[0]
    assert smtp.logins == [(USER, PASSWORD)] and "<utente@example.org>" in msg["rcpt"][0]
    assert msg["subject"] == "MakPhi: email di prova" and "Auto-Submitted: auto-generated" in msg["data"]
    assert "I risultati restano su MakPhi e non sono allegati" in msg["body"]


def test_wrong_password_and_refused_recipient_are_explained(client, smtp):
    without_test_limit(client)
    configure(client, smtp, password="sbagliata")
    r = client.post("/api/notify/test", json={"to": "utente@example.org"})
    assert r.status_code == 400 and "rifiutato utente o password" in r.json()["error"]["message"]
    configure(client, smtp)
    r = client.post("/api/notify/test", json={"to": "rifiuta@example.org"})
    assert r.status_code == 400 and "rifiutato il destinatario" in r.json()["error"]["message"]
    assert client.post("/api/notify/test", json={"to": "non-email"}).status_code == 400


def test_temporary_errors_are_retried(client, smtp):
    configure(client, smtp)
    service = without_test_limit(client)
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


def test_pipeline_end_sends_one_notice_without_results(client, smtp, notices):
    configure(client, smtp, public_url="https://makphi.example.org")
    r = client.post("/api/pipeline", json={**PIPELINE, "notify_email": "utente@example.org"})
    assert r.status_code == 200, r.text
    job_id = r.json()["id"]
    job = wait_job(client, job_id)
    msg = until(lambda: smtp.messages)[0]
    data = msg["body"]
    status_word = {"completed": "completata", "failed": "fallita"}[job["status"]]
    assert msg["subject"] == f"MakPhi: pipeline «Prova avviso» {status_word}"
    assert data.startswith(f"La pipeline «Prova avviso» è {status_word}.") and "Elementi: 1 (falliti 1)" in data
    assert f"https://makphi.example.org/pipeline?job={job_id}" in data and f"Identificativo del job: {job_id}" in data
    assert "proteina_riservata" not in msg["data"] and HUMAN_UB[:20] not in msg["data"]  # niente sequenze né nomi
    log = until(lambda: "Email di avviso" in (t := client.get(f"/api/jobs/{job_id}/log").json()["text"]) and t)
    assert "Email di avviso inviata a utente@example.org" in log
    until(lambda: job_id in notices.finished)
    for thread in notices.threads:
        thread.join(30)
    # un solo avviso per esecuzione: un solo thread d'invio, per questo job, e un solo messaggio
    assert [t.name for t in notices.threads] == [f"notify-{job_id[:6]}"] and len(smtp.messages) == 1


def test_pipeline_without_notice_sends_nothing(client, smtp, notices):
    configure(client, smtp)
    job_id = client.post("/api/pipeline", json=PIPELINE).json()["id"]
    wait_job(client, job_id)
    until(lambda: job_id in notices.finished)  # decisione dell'avviso presa: nessun thread d'invio
    assert notices.threads == [] and smtp.messages == []


def test_failed_delivery_is_written_in_the_job_log(client, smtp):
    configure(client, smtp)
    client.app.state.core.notify.retry_delays = ()
    client.app.state.core.notify._connect = lambda cfg: (_ for _ in ()).throw(ConnectionRefusedError("giù"))
    job_id = client.post("/api/pipeline", json={**PIPELINE, "notify_email": "utente@example.org"}).json()["id"]
    wait_job(client, job_id)
    log = until(lambda: "Email di avviso" in (t := client.get(f"/api/jobs/{job_id}/log").json()["text"]) and t)
    assert "Email di avviso a utente@example.org non inviata: Invio dell'email non riuscito" in log


# ------------------------------------------------------------------------------------------ modalità server
def test_environment_variables_take_precedence_over_the_file(tmp_path, monkeypatch):
    class Jobs:
        def on_finish(self, _fn):
            pass

    service = NotifyService(tmp_path, tmp_path / "licenses", Jobs(), server=True)
    service.file.parent.mkdir(parents=True)
    service.file.write_text("MAKPHI_SMTP_HOST=smtp.dal-file.example\nMAKPHI_SMTP_FROM=a@dal-file.example\n"
                            "MAKPHI_SMTP_PASSWORD=del-file\n", encoding="utf-8")
    assert service.status()["source"] == "file" and service.status()["host"] == "smtp.dal-file.example"
    monkeypatch.setenv("MAKPHI_SMTP_HOST", "smtp.universita.example")
    monkeypatch.setenv("MAKPHI_SMTP_PORT", "465")
    monkeypatch.setenv("MAKPHI_SMTP_SECURITY", "ssl")
    monkeypatch.setenv("MAKPHI_SMTP_FROM", "makphi@universita.example")
    monkeypatch.setenv("MAKPHI_SMTP_PASSWORD", "dell-amministratore")
    status = service.status()
    assert status["source"] == "env" and status["configured"] and status["host"] == "smtp.universita.example"
    assert status["security"] == "ssl" and status["port"] == 465 and "dell-amministratore" not in str(status)
    assert set(service.secrets()) == {"dell-amministratore", "del-file"}


def test_header_injection_is_impossible():
    with pytest.raises(ValueError):
        SmtpConfig(user="a\r\nBcc: x@y.z")
    with pytest.raises(AppError):
        notify_module.valid_email("a@b.it\r\nBcc: x@y.z")
