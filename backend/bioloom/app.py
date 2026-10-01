"""Applicazione FastAPI: API REST + interfaccia web compilata (frontend/dist)."""

from __future__ import annotations

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
from .api import bio, compute, databases, files, jobs, licenses, projects, system, templates
from .config import FRONTEND_DIST, Paths, default_paths
from .core import auth
from .core.container import build_core
from .core.errors import AppError


def create_app(paths: Optional[Paths] = None, frontend_dist: Optional[Path] = FRONTEND_DIST,
               dev_origins: tuple[str, ...] = ("http://localhost:5173", "http://127.0.0.1:5173"),
               auth_config: Optional[auth.AuthConfig] = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        resolved = paths or default_paths()
        app.state.core = build_core(resolved)
        app.state.auth = auth_config or auth.load(resolved.data)
        if app.state.auth.enabled and app.state.auth.generated:
            print(f"\n  Accesso protetto: chiave d'accesso generata in {resolved.data / 'access_key'}\n"
                  f"  Chiave: {app.state.auth.key}\n", flush=True)
        yield
        app.state.core.close()

    app = FastAPI(title="BioLoom v4", version=__version__, lifespan=lifespan,
                  description="Suite per la modellazione di proteine per omologia (MODELLER).")
    app.add_middleware(CORSMiddleware, allow_origins=list(dev_origins), allow_methods=["*"], allow_headers=["*"],
                       allow_credentials=True)

    @app.middleware("http")
    async def access_control(request: Request, call_next):
        cfg = getattr(request.app.state, "auth", None)
        if cfg is not None and not cfg.request_allowed(request.url.path, request.cookies.get(auth.COOKIE),
                                                       request.headers.get("authorization")):
            return JSONResponse(status_code=401, content={"error": {
                "code": "auth_required", "message": "Accesso richiesto: inserisci la chiave d'accesso."}})
        return await call_next(request)

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
    async def unexpected(_request: Request, exc: Exception):
        traceback.print_exception(type(exc), exc, exc.__traceback__)
        return JSONResponse(status_code=500, content={"error": {"code": "internal_error",
                                                                "message": f"Errore interno: {exc}"}})

    for module in (auth_api, system, licenses, databases, files, projects, bio, templates, jobs, compute):
        app.include_router(module.router)
    app.include_router(analysis.router)  # analisi strutturale (F7)
    app.include_router(labels.router)  # etichette, preferiti e ricerca globale (F8b)

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
            return JSONResponse({"message": "BioLoom v4 API attiva. Interfaccia web non compilata: "
                                            "esegui 'npm run build' in frontend/ oppure apri /docs."})

    return app
