"""Avviso per email alla fine di un job (pipeline batch), senza risultati.

Chi lancia una pipeline può chiedere un'email alla fine (`notify_email` nei parametri): quando il job arriva a uno stato
finale (completato, fallito o annullato) BioLoom invia un messaggio di solo testo con titolo, esito, durata e numero di
elementi per stato. Non contiene risultati, sequenze, nomi delle sequenze né file: i risultati restano su BioLoom.

Server di posta (SMTP): host, porta, sicurezza (STARTTLS, SSL/TLS o nessuna), utente, mittente e indirizzo pubblico di
BioLoom (per il link nel messaggio, facoltativo) in `data/notify.json`; la password a parte, in
`data/licenses/smtp_password` (solo per il proprietario, come le chiavi di licenza), mai restituita dall'API.
In modalità server (accesso con chiave) la configurazione è solo quella dell'amministratore, dalle variabili
`BIOLOOM_SMTP_*` di server.env: chi ha la chiave d'accesso non deve poter indicare dall'interfaccia un host qualunque
della rete del server (come per l'indirizzo di ESMFold).

L'invio avviene in un thread, con due nuovi tentativi per gli errori temporanei; l'esito finisce nel log del job.
"""

from __future__ import annotations

import os
import re
import smtplib
import ssl
import threading
import time
from datetime import datetime
from email.message import EmailMessage
from email.utils import formatdate, make_msgid
from pathlib import Path
from typing import Callable, Literal, Optional
from urllib.parse import urlsplit

from pydantic import BaseModel, Field, ValidationError, field_validator

from ..core.errors import AppError
from ..core.storage import atomic_write_json, read_json
from .licenses import _write_private

SMTP_TIMEOUT = 30
# Attese prima dei nuovi tentativi (errori temporanei: connessione, risposte 4xx)
RETRY_DELAYS = (10.0, 60.0)
# Attesa massima della chiusura dell'esecuzione (file salvati, log chiuso) prima di scrivere l'esito nel log
IDLE_WAIT = 120.0
EMAIL_RE = re.compile(r"^[^@\s<>,;\"']{1,64}@[A-Za-z0-9.-]{1,253}\.[A-Za-z]{2,63}$")
STATUS_WORD = {"completed": "completata", "failed": "fallita", "cancelled": "annullata"}
ITEM_WORD = {"completed": "completati", "failed": "falliti", "filtered": "filtrati", "cancelled": "annullati",
             "pending": "non elaborati", "running": "in corso", "searching": "in corso", "modeling": "in corso"}
KIND_WORD = {"pipeline": "La pipeline"}
SERVER_LOCKED = ("In modalità server il server di posta lo configura l'amministratore (variabili BIOLOOM_SMTP_* in "
                 "server.env), non l'interfaccia.")


def valid_email(value: str, label: str = "Indirizzo email") -> str:
    value = (value or "").strip()
    if not EMAIL_RE.match(value):
        raise AppError(f"{label} non valido: «{value[:80]}».", code="invalid_email")
    return value


class SmtpConfig(BaseModel):
    host: str = Field("", max_length=253)
    port: int = Field(587, ge=1, le=65535)
    security: Literal["starttls", "ssl", "none"] = "starttls"
    user: str = Field("", max_length=254)
    sender: str = Field("", max_length=254, description="Mittente dei messaggi")
    public_url: str = Field("", max_length=300, description="Indirizzo di BioLoom per il link nel messaggio")

    @field_validator("host")
    @classmethod
    def _host(cls, value: str) -> str:
        value = value.strip()
        if value and not re.match(r"^[A-Za-z0-9.-]+$|^\[[0-9A-Fa-f:.]+\]$", value):
            raise ValueError("nome del server non valido")
        return value

    @field_validator("user")
    @classmethod
    def _user(cls, value: str) -> str:
        value = value.strip()
        if any(c in value for c in "\r\n"):
            raise ValueError("utente non valido")
        return value

    @field_validator("sender")
    @classmethod
    def _sender(cls, value: str) -> str:
        value = value.strip()
        if value and not EMAIL_RE.match(value):
            raise ValueError("indirizzo del mittente non valido")
        return value

    @field_validator("public_url")
    @classmethod
    def _public_url(cls, value: str) -> str:
        value = value.strip().rstrip("/")
        if value:
            parts = urlsplit(value)
            if parts.scheme not in ("http", "https") or not parts.netloc or any(c in value for c in "\r\n "):
                raise ValueError("indirizzo non valido (http:// o https://)")
        return value


LABELS = {"host": "Server SMTP", "port": "Porta", "security": "Sicurezza", "user": "Utente", "sender": "Mittente",
          "public_url": "Indirizzo di BioLoom"}


def _config_error(exc: ValidationError) -> AppError:
    parts = []
    for err in exc.errors():
        field = str(err.get("loc", ["?"])[0])
        msg = str(err.get("msg", "")).removeprefix("Value error, ")
        if err.get("type") == "literal_error":
            msg = "valore non ammesso"
        elif err.get("type") in ("greater_than_equal", "less_than_equal", "int_parsing"):
            msg = "deve essere un numero fra 1 e 65535"
        elif err.get("type") == "string_too_long":
            msg = "troppo lungo"
        parts.append(f"{LABELS.get(field, field)}: {msg}")
    return AppError("Configurazione della posta non valida: " + "; ".join(parts) + ".", code="invalid_notify")


def _server_config() -> tuple[SmtpConfig, str]:
    env = os.environ
    try:
        cfg = SmtpConfig(host=env.get("BIOLOOM_SMTP_HOST", ""), port=int(env.get("BIOLOOM_SMTP_PORT") or 587),
                         security=env.get("BIOLOOM_SMTP_SECURITY", "starttls") or "starttls",
                         user=env.get("BIOLOOM_SMTP_USER", ""), sender=env.get("BIOLOOM_SMTP_FROM", ""),
                         public_url=env.get("BIOLOOM_PUBLIC_URL", ""))
    except (ValidationError, ValueError):
        cfg = SmtpConfig()  # configurazione dell'amministratore non valida: avviso spento (lo stato lo dice)
    return cfg, env.get("BIOLOOM_SMTP_PASSWORD", "")


class NotifyService:
    def __init__(self, data_dir: Path, licenses_dir: Path, jobs, *, server: bool = False,
                 sender_factory: Optional[Callable] = None) -> None:
        self._file = data_dir / "notify.json"
        self._password_file = licenses_dir / "smtp_password"
        self.jobs = jobs
        self.server = server
        self._lock = threading.Lock()
        self._connect = sender_factory or _connect  # sostituibile nei test
        self.retry_delays = RETRY_DELAYS
        self.idle_wait = IDLE_WAIT
        jobs.on_finish(self.on_finish)

    # ------------------------------------------------------------------ configurazione
    def config(self) -> tuple[SmtpConfig, str]:
        """(configurazione, password): dell'amministratore in modalità server, altrimenti quella salvata."""
        if self.server:
            return _server_config()
        raw = read_json(self._file, default={}) or {}
        try:
            cfg = SmtpConfig(**raw)
        except (ValidationError, TypeError):
            cfg = SmtpConfig()
        try:
            password = self._password_file.read_text(encoding="utf-8").strip()
        except OSError:
            password = ""
        return cfg, password

    @staticmethod
    def ready(cfg: SmtpConfig) -> bool:
        return bool(cfg.host and cfg.sender)

    def status(self) -> dict:
        cfg, password = self.config()
        return {**cfg.model_dump(), "password_set": bool(password), "configured": self.ready(cfg),
                "source": "server" if self.server else "local"}

    def save(self, changes: dict, password: Optional[str] = None, clear_password: bool = False) -> dict:
        if self.server:
            raise AppError(SERVER_LOCKED, code="notify_locked")
        with self._lock:
            current, _ = self.config()
            try:
                cfg = SmtpConfig(**{**current.model_dump(), **{k: v for k, v in changes.items() if v is not None}})
            except ValidationError as exc:
                raise _config_error(exc) from None
            atomic_write_json(self._file, cfg.model_dump())
            if clear_password:
                self._password_file.unlink(missing_ok=True)
            elif password:
                if any(c in password for c in "\r\n") or len(password) > 500:
                    raise AppError("Password della posta non valida.", code="invalid_notify")
                self._password_file.parent.mkdir(parents=True, exist_ok=True)
                _write_private(self._password_file, password.encode("utf-8"))
        return self.status()

    def secrets(self) -> list[str]:
        """Password della posta (salvata e dell'amministratore): da non esportare mai."""
        out = [os.environ.get("BIOLOOM_SMTP_PASSWORD", "")]
        try:
            out.append(self._password_file.read_text(encoding="utf-8").strip())
        except OSError:
            pass
        return [s for s in out if s]

    def check_recipient(self, address: str) -> str:
        """Destinatario dell'avviso alla sottomissione: indirizzo valido e server di posta configurato."""
        address = valid_email(address, "Indirizzo per l'avviso")
        if not self.ready(self.config()[0]):
            where = "l'amministratore (server.env)" if self.server else "Impostazioni → Notifiche email"
            raise AppError(f"Per l'avviso via email serve un server di posta: lo configura {where}.",
                           code="notify_not_configured")
        return address

    # ------------------------------------------------------------------ invio
    def send(self, to: str, subject: str, body: str) -> None:
        """Invia un messaggio di testo; AppError con il motivo se non riesce (dopo i nuovi tentativi)."""
        cfg, password = self.config()
        if not self.ready(cfg):
            raise AppError("Server di posta non configurato.", code="notify_not_configured")
        msg = EmailMessage()
        msg["From"] = cfg.sender
        msg["To"] = valid_email(to, "Destinatario")
        msg["Subject"] = " ".join(subject.split())[:150]
        msg["Date"] = formatdate(localtime=True)
        msg["Message-ID"] = make_msgid(domain=cfg.sender.split("@")[-1])
        msg["Auto-Submitted"] = "auto-generated"
        msg.set_content(body)
        delays = list(self.retry_delays)
        while True:
            try:
                with self._connect(cfg) as smtp:
                    if cfg.user:
                        smtp.login(cfg.user, password)
                    smtp.send_message(msg)
                return
            except smtplib.SMTPAuthenticationError:
                raise AppError("Il server di posta ha rifiutato utente o password.", code="notify_auth") from None
            except smtplib.SMTPRecipientsRefused:
                raise AppError(f"Il server di posta ha rifiutato il destinatario {to}.", code="notify_recipient") \
                    from None
            except (smtplib.SMTPException, OSError) as exc:
                temporary = not isinstance(exc, smtplib.SMTPResponseException) or 400 <= exc.smtp_code < 500
                if not temporary or not delays:
                    raise AppError(f"Invio dell'email non riuscito: {_reason(exc)}", code="notify_failed") from None
                time.sleep(delays.pop(0))

    def send_test(self, to: str) -> dict:
        to = valid_email(to, "Destinatario")
        cfg, _ = self.config()
        lines = ["Questo è un messaggio di prova di BioLoom: il server di posta funziona.",
                 "", "Alla fine delle pipeline con l'avviso attivo arriverà un messaggio come questo, con l'esito e il "
                 "numero di elementi elaborati (mai i risultati).", "", f"Server: {cfg.host}:{cfg.port}"]
        self.send(to, "BioLoom: email di prova", "\n".join(lines) + "\n" + FOOTER)
        return {"sent": True, "to": to}

    # ------------------------------------------------------------------ fine dei job
    def on_finish(self, job_id: str, status: str) -> None:
        """Ascoltatore del JobManager (stato finale scritto): avvia l'invio in un thread se il job lo chiede."""
        try:
            row = self.jobs.db.query_one("SELECT params FROM jobs WHERE id=?", (job_id,)) or {}
        except Exception:
            return
        params = row.get("params") if isinstance(row.get("params"), dict) else {}
        to = str(params.get("notify_email") or "").strip()
        if not to or status not in STATUS_WORD:
            return
        threading.Thread(target=self._notify, args=(job_id, status, to), name=f"notify-{job_id[:6]}",
                         daemon=True).start()

    def _notify(self, job_id: str, status: str, to: str) -> None:
        try:
            subject, body = self.message(job_id, status)
            self.send(to, subject, body)
            note = f"Email di avviso inviata a {to}"
        except AppError as exc:
            note = f"Email di avviso a {to} non inviata: {exc.message}"
        except Exception as exc:  # l'avviso non deve mai disturbare il resto
            note = f"Email di avviso a {to} non inviata: {type(exc).__name__}: {exc}"
        self._log(job_id, note)

    def _log(self, job_id: str, note: str) -> None:
        """Riga nel log del job, appena l'esecuzione si è chiusa (log del job chiuso dal JobManager)."""
        deadline = time.monotonic() + self.idle_wait
        while self.jobs.busy(job_id) and time.monotonic() < deadline:
            time.sleep(0.2)
        try:
            self.jobs._log_now(job_id, note)
        except Exception:
            pass

    def message(self, job_id: str, status: str) -> tuple[str, str]:
        """Oggetto e testo dell'avviso: titolo, esito, tempi, elementi per stato; nessun risultato."""
        job = self.jobs.get(job_id, include_result=False)
        what = KIND_WORD.get(job["kind"], "Il job")
        word = STATUS_WORD[status]
        if job["kind"] != "pipeline":
            word = {"completata": "completato", "fallita": "fallito", "annullata": "annullato"}[word]
        title = job.get("title") or job_id
        lines = [f"{what} «{title}» è {word}.", ""]
        counts = {}
        if job["kind"] == "pipeline":
            rows = self.jobs.db.query("SELECT status, COUNT(*) AS n FROM pipeline_items WHERE job_id=? GROUP BY status",
                                      (job_id,))
            for r in rows:
                key = ITEM_WORD.get(r["status"], r["status"])
                counts[key] = counts.get(key, 0) + int(r["n"])
        if counts:
            total = sum(counts.values())
            detail = ", ".join(f"{k} {v}" for k, v in sorted(counts.items(), key=lambda kv: -kv[1]))
            lines.append(f"Elementi: {total} ({detail})")
        started, finished = job.get("started_at"), job.get("finished_at")
        if started:
            lines.append(f"Avviato: {_when(started)}")
        if finished:
            lines.append(f"Terminato: {_when(finished)}" + (f" (durata {_duration(started, finished)})"
                                                           if started else ""))
        if status != "completed" and job.get("error"):
            lines.append(f"Motivo: {' '.join(str(job['error']).split())[:300]}")
        cfg, _ = self.config()
        lines.append("")
        if cfg.public_url:
            lines.append(f"Apri in BioLoom: {cfg.public_url}/pipeline?job={job_id}")
        lines.append(f"Identificativo del job: {job_id}")
        return f"BioLoom: {what.split(' ', 1)[1]} «{title}» {word}", "\n".join(lines) + "\n" + FOOTER


FOOTER = "\n--\nMessaggio automatico di BioLoom. I risultati restano su BioLoom e non sono allegati a questa email.\n"


def _connect(cfg: SmtpConfig):
    if cfg.security == "ssl":
        return smtplib.SMTP_SSL(cfg.host, cfg.port, timeout=SMTP_TIMEOUT, context=ssl.create_default_context())
    smtp = smtplib.SMTP(cfg.host, cfg.port, timeout=SMTP_TIMEOUT)
    try:
        if cfg.security == "starttls":
            smtp.starttls(context=ssl.create_default_context())
    except Exception:
        smtp.close()
        raise
    return smtp


def _reason(exc: Exception) -> str:
    if isinstance(exc, smtplib.SMTPResponseException):
        text = exc.smtp_error.decode("utf-8", "replace") if isinstance(exc.smtp_error, bytes) else str(exc.smtp_error)
        return f"{exc.smtp_code} {' '.join(text.split())[:200]}"
    if isinstance(exc, (TimeoutError, ConnectionError, OSError)) and not isinstance(exc, smtplib.SMTPException):
        return f"server non raggiungibile ({type(exc).__name__}: {exc})"[:300]
    return f"{type(exc).__name__}: {exc}"[:300]


def _when(iso: str) -> str:
    try:
        moment = datetime.fromisoformat(iso)
    except ValueError:
        return iso
    return moment.strftime("%d/%m/%Y %H:%M") + (" UTC" if moment.utcoffset() is not None and
                                                 not moment.utcoffset().total_seconds() else "")


def _duration(start: str, end: str) -> str:
    try:
        seconds = int((datetime.fromisoformat(end) - datetime.fromisoformat(start)).total_seconds())
    except ValueError:
        return "?"
    days, rest = divmod(max(0, seconds), 86400)
    hours, rest = divmod(rest, 3600)
    minutes = rest // 60
    if days:
        return f"{days} g {hours} h {minutes} min"
    if hours:
        return f"{hours} h {minutes} min"
    return f"{minutes} min" if minutes else f"{seconds} s"
