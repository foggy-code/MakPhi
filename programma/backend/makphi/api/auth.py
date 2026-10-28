"""Accesso con chiave (modalità server)."""

from __future__ import annotations

import asyncio
import threading
from typing import Optional

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from ..core import auth

router = APIRouter(prefix="/api/auth", tags=["Accesso"])

# Margine oltre l'attesa dopo una chiave sbagliata: asyncio può svegliarsi qualche millisecondo prima dell'istante
# chiesto, e il tentativo successivo dello stesso client troverebbe l'indirizzo ancora fermo (429)
WAIT_MARGIN = 0.05
# Proxy non fidati già segnalati nel log (al più PROXY_WARNINGS indirizzi)
PROXY_WARNINGS = 16
_warned: set[str] = set()
_warned_lock = threading.Lock()


class LoginIn(BaseModel):
    key: str


def _config(request: Request) -> auth.AuthConfig:
    return getattr(request.app.state, "auth", None) or auth.AuthConfig(enabled=False)


def _limiter(request: Request) -> auth.LoginLimiter:
    limiter = getattr(request.app.state, "login_limiter", None)
    if limiter is None:  # app creata senza create_app (non dovrebbe succedere): un freno comunque
        limiter = request.app.state.login_limiter = auth.LoginLimiter()
    return limiter


def _error(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"error": {"code": code, "message": message}})


def rate_limited(wait: float) -> JSONResponse:
    return _error(429, "rate_limited", "Troppi tentativi d'accesso da questo indirizzo: riprova tra "
                                       f"{auth.wait_seconds(wait)} s.")


def client_of(request: Request) -> str:
    """Indirizzo del client per il freno ai tentativi (core/auth.client_address, proxy fidati da
    MAKPHI_TRUSTED_PROXIES letti all'avvio). Una richiesta inoltrata da un proxy che non è fra quelli fidati si segnala
    una volta nel log, con l'indirizzo da scrivere nella variabile: lì il freno vale per tutti i client insieme."""
    peer = request.client.host if request.client else "?"
    trusted = getattr(request.app.state, "trusted_proxies", ())
    forwarded = ", ".join(request.headers.getlist("x-forwarded-for"))
    if forwarded and not auth.is_trusted(peer, trusted):
        _warn_untrusted_proxy(peer)
    return auth.client_address(peer, forwarded, trusted)


def _warn_untrusted_proxy(peer: str) -> None:
    with _warned_lock:
        if peer in _warned or len(_warned) >= PROXY_WARNINGS:
            return
        _warned.add(peer)
    print(f"[MakPhi] Richieste inoltrate da un reverse proxy (X-Forwarded-For) arrivano da {peer}, che non è fra i "
          "proxy fidati (MAKPHI_TRUSTED_PROXIES): il freno ai tentativi d'accesso tratta tutti i client come un unico "
          "indirizzo, e chi sbaglia la chiave di continuo può bloccare l'accesso agli altri. Se è il tuo reverse proxy, "
          f"scrivi MAKPHI_TRUSTED_PROXIES={peer} in server.env e riavvia MakPhi.", flush=True)


def authenticate(request: Request, cfg: auth.AuthConfig) -> tuple[bool, Optional[float]]:
    """Accesso di una richiesta: (True, None) senza accesso protetto o con un cookie di sessione valido, che non passa
    mai dal freno ai tentativi; altrimenti la chiave come Bearer, attraverso il freno (LoginLimiter.try_key):
    (True, None) giusta, (False, None) sbagliata o assente, (False, secondi) indirizzo fermo, la richiesta risponde
    429 senza che la chiave si confronti."""
    cookie = request.cookies.get(auth.COOKIE) or request.cookies.get(auth.LEGACY_COOKIE)
    if not cfg.enabled or cfg.session_valid(cookie):
        return True, None
    key = auth.bearer_key(request.headers.get("authorization"))
    if key is None:
        return False, None
    return _limiter(request).try_key(client_of(request), lambda: cfg.check_key(key))


def access_denied(request: Request, cfg: auth.AuthConfig) -> Optional[JSONResponse]:
    """Risposta a una richiesta senza accesso (401 `auth_required`, 429 `rate_limited`), None se può proseguire. Per
    il middleware dell'accesso (app.py) e le rotte di questo modulo che lo richiedono."""
    ok, wait = authenticate(request, cfg)
    if wait is not None:
        return rate_limited(wait)
    if not ok:
        return _error(401, "auth_required", "Accesso richiesto: inserisci la chiave d'accesso.")
    return None


@router.get("/status")
def status(request: Request):
    """Accesso protetto e sessione valida. Con la chiave come Bearer risponde attraverso il freno ai tentativi: non è
    un modo per provare chiavi senza attese."""
    cfg = _config(request)
    authenticated, wait = authenticate(request, cfg)
    if wait is not None:
        return rate_limited(wait)
    return {"enabled": cfg.enabled, "authenticated": authenticated}


@router.post("/login")
async def login(body: LoginIn, request: Request):
    """Una chiave sbagliata risponde 401 dopo un'attesa che cresce con gli errori recenti dello stesso indirizzo
    (core/auth.LoginLimiter); un tentativo mentre un altro dello stesso indirizzo è in corso, o prima della fine di
    quell'attesa, risponde subito 429 `rate_limited` con l'attesa."""
    cfg = _config(request)
    if not cfg.enabled:
        return {"enabled": False, "authenticated": True}
    limiter = _limiter(request)
    client = client_of(request)
    wait = limiter.begin(client)
    if wait is not None:
        return rate_limited(wait)
    try:
        if not cfg.check_key(body.key):
            # l'indirizzo resta occupato durante l'attesa: le richieste in parallelo non la scavalcano
            await asyncio.sleep(limiter.failure(client) + WAIT_MARGIN)
            return _error(401, "auth_failed", "Chiave d'accesso non corretta.")
        limiter.success(client)
    finally:
        limiter.end(client)
    resp = JSONResponse({"enabled": True, "authenticated": True})
    resp.set_cookie(auth.COOKIE, cfg.issue(), max_age=auth.SESSION_DAYS * 86400, httponly=True,
                    samesite="strict", secure=_https(request), path="/")
    return resp


def _https(request: Request) -> bool:
    """La richiesta è arrivata in HTTPS, anche attraverso un reverse proxy.

    Dietro un proxy (Caddy, nginx) uvicorn vede HTTP: si fida degli header inoltrati solo da 127.0.0.1, e il proxy
    raggiunge il container dalla rete di Docker. Qui conta anche `X-Forwarded-Proto`, ma solo per il flag `Secure` del
    cookie: un header falso può solo renderlo più restrittivo. Gli header inoltrati non diventano fidati in generale:
    `X-Forwarded-For` vale solo per il freno ai tentativi e solo dai proxy di MAKPHI_TRUSTED_PROXIES (`client_of`),
    altrimenti chiunque lo scriverebbe per aggirarlo.
    """
    if request.url.scheme == "https":
        return True
    proto = request.headers.get("x-forwarded-proto", "")
    return proto.split(",")[0].strip().lower() == "https"


@router.post("/logout")
def logout() -> JSONResponse:
    resp = JSONResponse({"authenticated": False})
    for name in (auth.COOKIE, auth.LEGACY_COOKIE):
        resp.delete_cookie(name, path="/")
    return resp


@router.post("/logout-all")
def logout_all(request: Request) -> JSONResponse:
    """Chiude tutte le sessioni, su tutti i dispositivi, compresa questa (core/auth: epoca delle sessioni). Solo con
    l'accesso protetto e una sessione valida (o la chiave come Bearer); gli script con la chiave continuano a
    funzionare, i browser devono inserirla di nuovo."""
    cfg = _config(request)
    if not cfg.enabled:
        return _error(400, "auth_disabled", "L'accesso con chiave non è attivo su questa installazione: non ci sono "
                                            "sessioni da chiudere.")
    denied = access_denied(request, cfg)  # la chiave come Bearer passa dal freno ai tentativi
    if denied is not None:
        return denied
    try:
        cfg.revoke_all()
    except auth.EpochError as exc:
        # il percorso e l'errore del sistema vanno solo nel log, come per gli errori interni (app.unexpected)
        print(f"[MakPhi] Sessioni non chiuse: {exc}", flush=True)
        return _error(500, "session_epoch_error", "Sessioni non chiuse: non è stato possibile salvare l'epoca delle "
                                                  "sessioni (dettagli nel log di MakPhi). Controlla i permessi della "
                                                  "cartella dati.")
    resp = JSONResponse({"ok": True})
    for name in (auth.COOKIE, auth.LEGACY_COOKIE):
        resp.delete_cookie(name, path="/")
    return resp
