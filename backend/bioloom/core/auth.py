"""Accesso con chiave per l'uso da server (BIOLOOM_AUTH=on).

In locale (porta pubblicata solo su 127.0.0.1) l'accesso è libero. Su un server la chiave viene da
BIOLOOM_ACCESS_KEY oppure è generata al primo avvio in `data/access_key` e stampata nel log. Il login
imposta un cookie HttpOnly firmato (HMAC) con scadenza; è accettato anche `Authorization: Bearer <chiave>`
per gli script.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

COOKIE = "bioloom_session"
SESSION_DAYS = 30
EXEMPT = ("/api/health", "/api/auth/")
PROTECTED_PREFIXES = ("/api/", "/docs", "/redoc", "/openapi.json")


def _truthy(value: Optional[str]) -> bool:
    return (value or "").strip().lower() in ("1", "on", "true", "yes", "si", "sì")


@dataclass
class AuthConfig:
    enabled: bool
    key: str = ""
    generated: bool = False

    @property
    def _secret(self) -> bytes:
        return hashlib.sha256(b"bioloom-session:" + self.key.encode()).digest()

    def check_key(self, candidate: str) -> bool:
        return bool(self.key) and hmac.compare_digest(candidate.strip().encode(), self.key.encode())

    def issue(self, now: Optional[float] = None) -> str:
        expires = int((now or time.time()) + SESSION_DAYS * 86400)
        sig = hmac.new(self._secret, str(expires).encode(), hashlib.sha256).hexdigest()
        return f"{expires}.{sig}"

    def verify(self, token: str, now: Optional[float] = None) -> bool:
        try:
            expires_raw, sig = token.split(".", 1)
            expires = int(expires_raw)
        except (ValueError, AttributeError):
            return False
        expected = hmac.new(self._secret, str(expires).encode(), hashlib.sha256).hexdigest()
        return hmac.compare_digest(sig, expected) and expires > (now or time.time())

    def request_allowed(self, path: str, cookie: Optional[str], authorization: Optional[str]) -> bool:
        if not self.enabled:
            return True
        if not path.startswith(PROTECTED_PREFIXES) or path.startswith(EXEMPT):
            return True
        if cookie and self.verify(cookie):
            return True
        if authorization and authorization.lower().startswith("bearer "):
            return self.check_key(authorization[7:])
        return False


def load(data_dir: Path) -> AuthConfig:
    if not _truthy(os.environ.get("BIOLOOM_AUTH")):
        return AuthConfig(enabled=False)
    key = os.environ.get("BIOLOOM_ACCESS_KEY", "").strip()
    if key:
        return AuthConfig(enabled=True, key=key)
    path = data_dir / "access_key"
    try:
        key = path.read_text(encoding="utf-8").strip()
    except OSError:
        key = ""
    generated = False
    if len(key) < 8:
        key = secrets.token_urlsafe(18)
        path.write_text(key + "\n", encoding="utf-8")
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
        generated = True
    return AuthConfig(enabled=True, key=key, generated=generated)


def shutdown_allowed() -> bool:
    return _truthy(os.environ.get("BIOLOOM_ALLOW_SHUTDOWN"))
