"""Accesso con chiave (modalità server)."""

from __future__ import annotations

import asyncio
import time

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from ..core import auth

router = APIRouter(prefix="/api/auth", tags=["Accesso"])

_failures: dict[str, list[float]] = {}


class LoginIn(BaseModel):
    key: str


def _config(request: Request) -> auth.AuthConfig:
    return getattr(request.app.state, "auth", None) or auth.AuthConfig(enabled=False)


@router.get("/status")
def status(request: Request) -> dict:
    cfg = _config(request)
    authenticated = cfg.request_allowed("/api/", request.cookies.get(auth.COOKIE),
                                        request.headers.get("authorization"))
    return {"enabled": cfg.enabled, "authenticated": authenticated}


@router.post("/login")
async def login(body: LoginIn, request: Request):
    cfg = _config(request)
    if not cfg.enabled:
        return {"enabled": False, "authenticated": True}
    client = request.client.host if request.client else "?"
    now = time.time()
    recent = [t for t in _failures.get(client, []) if now - t < 900]
    if not cfg.check_key(body.key):
        recent.append(now)
        _failures[client] = recent
        await asyncio.sleep(min(10.0, 0.5 * len(recent)))  # rallenta i tentativi ripetuti
        return JSONResponse(status_code=401, content={"error": {
            "code": "auth_failed", "message": "Chiave d'accesso non corretta."}})
    _failures.pop(client, None)
    resp = JSONResponse({"enabled": True, "authenticated": True})
    resp.set_cookie(auth.COOKIE, cfg.issue(), max_age=auth.SESSION_DAYS * 86400, httponly=True,
                    samesite="strict", secure=request.url.scheme == "https", path="/")
    return resp


@router.post("/logout")
def logout() -> JSONResponse:
    resp = JSONResponse({"authenticated": False})
    resp.delete_cookie(auth.COOKIE, path="/")
    return resp
