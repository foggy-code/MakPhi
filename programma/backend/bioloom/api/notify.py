"""Avviso per email alla fine delle pipeline: server di posta (SMTP) ed email di prova (services/notify.py)."""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from ..core.container import Core
from .deps import get_core

router = APIRouter(prefix="/api/notify", tags=["Notifiche"])


class NotifyIn(BaseModel):
    host: Optional[str] = None
    port: Optional[int] = None
    security: Optional[str] = None
    user: Optional[str] = None
    sender: Optional[str] = None
    public_url: Optional[str] = None
    password: Optional[str] = Field(None, description="Nuova password; assente o vuota = resta quella salvata")
    clear_password: bool = False


class TestIn(BaseModel):
    to: str = Field(..., max_length=254)


@router.get("")
def notify_status(core: Core = Depends(get_core)) -> dict:
    """Configurazione del server di posta (senza la password: solo se è impostata)."""
    return core.notify.status()


@router.put("")
def save_notify(body: NotifyIn, core: Core = Depends(get_core)) -> dict:
    changes = body.model_dump(exclude={"password", "clear_password"})
    return core.notify.save(changes, password=body.password, clear_password=body.clear_password)


@router.post("/test")
def send_test(body: TestIn, core: Core = Depends(get_core)) -> dict:
    """Invia subito un'email di prova al destinatario indicato."""
    return core.notify.send_test(body.to)
