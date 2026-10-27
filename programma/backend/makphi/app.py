"""Applicazione FastAPI: API REST + interfaccia web compilata (frontend/dist)."""

from __future__ import annotations

import os
import secrets
import traceback
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse

from . import __version__
from .api import analysis, labels
from .api import auth as auth_api
from .api import bio, compute, databases, files, jobs, licenses, notify, projects, system, templates
from .api import reproducibility, structure_tools
from .api import mutations as mutations_api
from .api import predict as predict_api
from .config import FRONTEND_DIST, Paths, default_paths
from .core import auth, bodylimit, guard
from .core.container import build_core
from .core.errors import AppError

DEV_ORIGINS = ("http://localhost:5173", "http://127.0.0.1:5173")  # interfaccia di sviluppo (npm run dev, Vite)


def create_app(paths: Optional[Paths] = None, frontend_dist: Optional[Path] = FRONTEND_DIST,
               dev_origins: Optional[tuple[str, ...]] = None,
               auth_config: Optional[auth.AuthConfig] = None) -> FastAPI:
    if dev_origins is None:
        # Origini ammesse da CORS e dal controllo dell'origine (core/guard), solo fuori dal container (sviluppo). Nel
        # container l'interfaccia è compilata e servita dalla stessa origine, e un npm run dev accanto passa dal proxy
        # di Vite (stessa origine per il browser): lì lascerebbero solo leggere l'API e arrestare MakPhi a qualunque
        # pagina sulla porta 5173.
        dev_origins = () if os.environ.get("MAKPHI_CONTAINER") == "1" else DEV_ORIGINS

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        resolved = paths or default_paths()
        resolved.ensure()  # la chiave generata va nella cartella dati
        try:
            app.state.auth = auth_config or auth.load(resolved.data)
            # reverse proxy fidati per l'indirizzo del client nel freno ai tentativi (MAKPHI_TRUSTED_PROXIES)
            app.state.trusted_proxies = auth.trusted_proxies() if app.state.auth.enabled else ()
        except auth.AccessKeyError as exc:  # l'avvio si ferma: il motivo ben visibile, non solo nel traceback
            print(f"\n  ERRORE: {exc}\n", flush=True)
            raise
        if app.state.auth.enabled and app.state.auth.epoch_file is None:
            app.state.auth.attach(resolved.data)  # configurazione passata da chi crea l'app (test): stessa epoca
        app.state.allowed_hosts = guard.allowed_hosts()  # MAKPHI_ALLOWED_HOSTS, letta all'avvio come MAKPHI_AUTH
        app.state.core = build_core(resolved, server=app.state.auth.enabled)
        if app.state.auth.enabled and app.state.auth.generated:
            print(f"\n  Accesso protetto: chiave d'accesso generata in {resolved.data / 'access_key'}\n"
                  f"  Chiave: {app.state.auth.key}\n", flush=True)
        yield
        app.state.core.close()

    app = FastAPI(title="MakPhi", version=__version__, lifespan=lifespan,
                  description="Suite per la modellazione di proteine per omologia (MODELLER).")
    app.state.login_limiter = auth.LoginLimiter()  # tentativi di accesso per indirizzo (api/auth.py)
    # Corpo oltre il più grande caricamento legittimo: 413 prima che Starlette scriva il multipart su disco
    # (core/bodylimit). Aggiunto per primo, quindi il più interno: richieste di altri siti (guardia) e senza chiave
    # d'accesso si rifiutano prima, e nessuno dei due controlli legge il corpo; dentro CORS, così in sviluppo anche il
    # 413 arriva leggibile all'interfaccia di Vite; e fra il `receive` contato e le rotte non resta nessun middleware.
    app.add_middleware(bodylimit.BodyLimit, limit=bodylimit.request_limit())
    app.add_middleware(CORSMiddleware, allow_origins=list(dev_origins), allow_methods=["*"], allow_headers=["*"],
                       allow_credentials=True)

    @app.middleware("http")
    async def access_control(request: Request, call_next):
        # cookie di sessione, o chiave come Bearer attraverso il freno ai tentativi (api/auth.authenticate): 401 o 429
        cfg = getattr(request.app.state, "auth", None)
        if cfg is not None and cfg.requires_auth(request.url.path):
            denied = auth_api.access_denied(request, cfg)
            if denied is not None:
                return denied
        return await call_next(request)

    # Host (DNS rebinding), origine (CSRF) e iframe (clickjacking), core/guard. In Starlette l'ultimo middleware
    # aggiunto è il più esterno: le richieste di altri siti si rifiutano prima del controllo della chiave d'accesso.
    app.add_middleware(guard.RequestGuard, dev_origins=dev_origins)

    @app.exception_handler(AppError)
    async def app_error(_request: Request, exc: AppError):
        return JSONResponse(status_code=exc.status, content={"error": exc.to_dict()})

    @app.exception_handler(RequestValidationError)
    async def validation_error(_request: Request, exc: RequestValidationError):
        details = [{"field": ".".join(str(p) for p in e.get("loc", [])[1:]), "message": e.get("msg")}
                   for e in exc.errors()]
        message = "; ".join(f"{d['field']}: {d['message']}" for d in details[:5]) or "Richiesta non valida."
        return JSONResponse(status_code=422, content={"error": {"code": "validation_error", "message": message,
                                                                "details": details}})

    @app.exception_handler(Exception)
    async def unexpected(request: Request, exc: Exception):
        # Al client un messaggio generico con un codice; il dettaglio (che può contenere percorsi, parametri o pezzi di
        # file) solo nel log, con lo stesso codice per ritrovarlo.
        code = secrets.token_hex(4)
        detail = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
        print(f"[MakPhi] Errore interno (codice {code}) su {request.method} {request.url.path}:\n{detail}",
              flush=True)
        return JSONResponse(status_code=500, content={"error": {
            "code": "internal_error",
            "message": f"Errore interno (codice {code}): i dettagli sono nel log di MakPhi."}})

    for module in (auth_api, system, licenses, notify, databases, files, projects, bio, templates, jobs, compute):
        app.include_router(module.router)
    app.include_router(analysis.router)  # analisi strutturale (F7)
    app.include_router(structure_tools.router)  # tasche, elettrostatica, rifinitura (F7b, F9.1)
    app.include_router(predict_api.router)  # confronto con un predittore esterno (F9.3)
    app.include_router(labels.router)  # etichette, preferiti e ricerca globale (F8b)
    app.include_router(reproducibility.router)  # pacchetti di riproducibilità e riesecuzione (F9.4)
    app.include_router(mutations_api.router)  # mutagenesi e scansione di alanina (F9.2)

    @app.get("/api/{rest:path}", include_in_schema=False)
    async def api_not_found(rest: str):
        return JSONResponse(status_code=404, content={"error": {"code": "not_found",
                                                                "message": f"Endpoint /api/{rest} inesistente."}})

    dist = Path(frontend_dist) if frontend_dist else None
    if dist and (dist / "index.html").exists():
        index = dist / "index.html"

        @app.get("/{full_path:path}", include_in_schema=False)
        async def spa(full_path: str):
            candidate = (dist / full_path).resolve()
            if full_path and dist.resolve() in candidate.parents and candidate.is_file():
                cache = "public, max-age=31536000, immutable" if full_path.startswith("assets/") else "no-cache"
                return FileResponse(candidate, headers={"Cache-Control": cache})
            if full_path.startswith("assets/") or Path(full_path).suffix:
                # Un asset mancante (es. build più recente) deve dare 404, non la pagina HTML.
                return JSONResponse(status_code=404, content={"error": {"code": "not_found",
                                                                        "message": f"File {full_path} inesistente."}})
            return FileResponse(index, headers={"Cache-Control": "no-cache"})
    else:
        @app.get("/", include_in_schema=False)
        async def no_frontend():
            return JSONResponse({"message": "MakPhi API attiva. Interfaccia web non compilata: "
                                            "esegui 'npm run build' in frontend/ oppure apri /docs."})

    return app
