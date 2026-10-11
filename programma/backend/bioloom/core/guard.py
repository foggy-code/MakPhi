"""Protezione dalle richieste di altri siti: DNS rebinding (header Host), CSRF (Origin, Sec-Fetch-Site), iframe.

In locale l'accesso è libero e la porta è pubblicata solo su 127.0.0.1, ma il browser che apre BioLoom apre anche
altri siti. Senza controlli una pagina qualsiasi potrebbe inviare a http://127.0.0.1:8765 richieste che modificano
(`fetch` con `mode: "no-cors"` o un form non passano dalla preflight CORS: arresto, aggiornamento dei database,
upload) e, con un proprio dominio risolto su 127.0.0.1 (DNS rebinding), diventare «stessa origine» e leggere l'API.

- **Host** (solo con l'accesso protetto spento): ammessi i nomi di loopback e quelli di BIOLOOM_ALLOWED_HOSTS. In
  modalità server proteggono già la chiave e il cookie, legato al nome del sito, e il server si raggiunge per IP,
  nome o reverse proxy.
- **Origine** (sempre, sui metodi che modificano): il browser dichiara da dove parte la richiesta con Sec-Fetch-Site
  oppure, se non lo invia, con Origin. Le richieste senza nessuno dei due (curl, script, avviatori, test) passano.
- **Iframe** (sempre, su ogni risposta): X-Frame-Options DENY e CSP frame-ancestors 'none'. Una pagina di BioLoom
  dentro un iframe trasparente di un altro sito invierebbe le richieste come «stessa origine» (clickjacking: due clic
  su «Arresta» bastano); il frontend non mette mai in un iframe le proprie pagine.

Il middleware è il più esterno (aggiunto per ultimo in `create_app`): rifiuta prima del controllo della chiave.
"""

from __future__ import annotations

import os
import re
from collections.abc import Collection
from typing import Optional
from urllib.parse import urlsplit

from starlette.datastructures import MutableHeaders
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

ENV = "BIOLOOM_ALLOWED_HOSTS"
LOOPBACK = frozenset({"127.0.0.1", "localhost", "::1"})
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})  # OPTIONS: le preflight CORS dello sviluppo devono passare
SAME_SITE_FETCH = frozenset({"same-origin", "none"})  # "none": richiesta partita dall'utente (indirizzo, segnalibro)
HOST_EXEMPT = ("/api/health",)  # controlli di salute di avviatori, Docker e monitoraggio
DEFAULT_PORTS = {"http": 80, "https": 443}


def split_host(value: str) -> tuple[str, Optional[int]]:
    """Nome (minuscolo, senza parentesi né punto finale) e porta di un header Host o X-Forwarded-Host."""
    value = value.strip().lower()
    port = ""
    if value.startswith("["):  # IPv6 con porta facoltativa: [::1]:8765
        name, _, rest = value[1:].partition("]")
        port = rest[1:] if rest.startswith(":") else ""
    elif value.count(":") == 1:
        name, port = value.split(":")
    else:  # nome senza porta, o IPv6 senza parentesi
        name = value
    return name.rstrip("."), int(port) if port.isascii() and port.isdigit() else None


def _entry_name(item: str) -> str:
    """Nome di una voce di BIOLOOM_ALLOWED_HOSTS. Vale anche scritta come URL (https://nome:porta/), per analogia
    con BIOLOOM_PUBLIC_URL: altrimenti diventerebbe un nome sbagliato ("https") e il nome voluto resterebbe escluso."""
    if "://" in item:
        try:
            return (urlsplit(item).hostname or "").rstrip(".")
        except ValueError:  # IPv6 senza parentesi chiusa
            return ""
    return split_host(item.split("/", 1)[0])[0]


def allowed_hosts(value: Optional[str] = None) -> frozenset[str]:
    """Nomi ammessi oltre ai loopback, da BIOLOOM_ALLOWED_HOSTS: separati da virgole e/o spazi, senza distinzione
    di maiuscole, con o senza porta (ignorata), anche come URL; "*" li ammette tutti."""
    raw = os.environ.get(ENV, "") if value is None else value
    names = ("*" if item == "*" else _entry_name(item) for item in re.split(r"[\s,]+", raw) if item)
    return frozenset(name for name in names if name)


def host_allowed(host: Optional[str], allowed: Collection[str]) -> bool:
    """Header Host ammesso: assente (client non browser), loopback o nell'elenco. Conta solo il nome, non la porta:
    in sviluppo il proxy di Vite inoltra "localhost:5173"."""
    if not host or "*" in allowed:
        return True
    name = split_host(host)[0]
    return name in LOOPBACK or name in allowed


def same_origin(origin: str, host: Optional[str], forwarded_host: Optional[str] = None) -> bool:
    """L'Origin coincide (nome e porta) con l'header Host o con il primo X-Forwarded-Host del reverse proxy. Un
    host senza porta ha quella predefinita dello schema dell'Origin, come fa il browser; "null" (iframe con sandbox,
    file locali, reindirizzamenti fra siti) non coincide mai."""
    try:
        parts = urlsplit(origin.strip())
        port = parts.port
    except ValueError:
        return False
    if parts.scheme not in DEFAULT_PORTS or not parts.hostname:
        return False
    default = DEFAULT_PORTS[parts.scheme]
    target = (parts.hostname.rstrip("."), port or default)
    for candidate in (host, (forwarded_host or "").split(",")[0]):
        if candidate and candidate.strip():
            name, candidate_port = split_host(candidate)
            if (name, candidate_port or default) == target:
                return True
    return False


def origin_allowed(method: str, *, origin: Optional[str], fetch_site: Optional[str], host: Optional[str],
                   forwarded_host: Optional[str] = None, dev_origins: Collection[str] = ()) -> bool:
    """Una richiesta che modifica (non GET, HEAD, OPTIONS) parte da una pagina di BioLoom o da un client non
    browser."""
    if method.upper() in SAFE_METHODS:
        return True
    if origin and origin in dev_origins:  # sviluppo: interfaccia di Vite con CORS
        return True
    if fetch_site:  # punto di vista del browser: vale anche dietro un reverse proxy
        return fetch_site.strip().lower() in SAME_SITE_FETCH
    if origin:  # browser che non invia Sec-Fetch-Site
        return same_origin(origin, host, forwarded_host)
    return True  # né Origin né Sec-Fetch-Site: curl, script, avviatori, test


def _error(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"error": {"code": code, "message": message}})


class RequestGuard:
    """Middleware ASGI puro (non BaseHTTPMiddleware: nessun livello in più sulle risposte in streaming). Legge da
    `app.state` la configurazione dell'accesso (`auth`) e i nomi ammessi (`allowed_hosts`), impostati dal lifespan."""

    def __init__(self, app: ASGIApp, dev_origins: Collection[str] = ()) -> None:
        self.app = app
        self.dev_origins = frozenset(dev_origins)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_no_frames(message: Message) -> None:
            # contro l'inclusione in un iframe, anche sui rifiuti; senza toccare le intestazioni che la risposta ha
            # già (il report HTML ha una CSP propria con frame-ancestors 'none')
            if message["type"] == "http.response.start":
                message.setdefault("headers", [])
                headers = MutableHeaders(scope=message)
                headers.setdefault("x-frame-options", "DENY")
                if "frame-ancestors" not in ",".join(headers.getlist("content-security-policy")):
                    headers.append("content-security-policy", "frame-ancestors 'none'")
            await send(message)

        refusal = self.check(Request(scope))
        await (self.app if refusal is None else refusal)(scope, receive, send_no_frames)

    def check(self, request: Request) -> Optional[JSONResponse]:
        state = request.app.state
        cfg = getattr(state, "auth", None)
        headers = request.headers
        host = headers.get("host")
        # il percorso dallo scope: request.url ricostruisce l'URL anche dall'header Host, che qui non è fidato
        if not (cfg and cfg.enabled) and request.scope.get("path", "") not in HOST_EXEMPT:
            allowed = getattr(state, "allowed_hosts", None)
            if allowed is None:  # lifespan non eseguito
                allowed = allowed_hosts()
            if not host_allowed(host, allowed):
                name = split_host(host or "")[0][:100]
                return _error(400, "host_not_allowed",
                              f"Indirizzo non consentito: senza accesso protetto BioLoom risponde solo a localhost e "
                              f"127.0.0.1, non a «{name}» (protezione dal DNS rebinding). Per aprirlo con questo nome "
                              f"aggiungilo a BIOLOOM_ALLOWED_HOSTS.")
        if not origin_allowed(request.method, origin=headers.get("origin"), fetch_site=headers.get("sec-fetch-site"),
                              host=host, forwarded_host=headers.get("x-forwarded-host"),
                              dev_origins=self.dev_origins):
            return _error(403, "cross_origin", "Richiesta rifiutata: proviene da un'altra pagina o da un altro sito.")
        return None
