"""Licenze dei programmi esterni (MODELLER, ProSA II, PROCHECK)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, File, UploadFile
from pydantic import BaseModel

from ..core.container import Core
from .deps import get_core, read_upload

router = APIRouter(prefix="/api/licenses", tags=["Licenze"])


class ModellerKeyIn(BaseModel):
    key: str


@router.get("")
def licenses_status(core: Core = Depends(get_core)) -> dict:
    return core.licenses.status()


@router.put("/modeller")
def set_modeller_key(body: ModellerKeyIn, core: Core = Depends(get_core)) -> dict:
    """Prova la chiave con MODELLER e la salva solo se viene accettata."""
    return core.licenses.set_modeller_key(body.key)


@router.delete("/modeller")
def remove_modeller_key(core: Core = Depends(get_core)) -> dict:
    return core.licenses.remove_modeller_key()


@router.post("/prosa")
async def upload_prosa_license(file: UploadFile = File(...), core: Core = Depends(get_core)) -> dict:
    return core.licenses.set_prosa_license(await read_upload(file, limit=64 * 1024))


@router.delete("/prosa")
def remove_prosa_license(core: Core = Depends(get_core)) -> dict:
    return core.licenses.remove_prosa_license()
