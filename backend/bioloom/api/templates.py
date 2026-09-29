"""Libreria dei template."""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, File, Form, UploadFile
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel

from ..core.container import Core
from .deps import get_core, read_upload

router = APIRouter(prefix="/api/templates", tags=["Template"])


class FetchIn(BaseModel):
    pdb_id: str
    format: str = "pdb"


@router.get("")
def list_templates(core: Core = Depends(get_core)) -> list[dict]:
    return core.library.list()


@router.post("/fetch")
def fetch_template(body: FetchIn, core: Core = Depends(get_core)) -> dict:
    return core.library.fetch(body.pdb_id, body.format)


@router.post("/upload")
async def upload_template(file: UploadFile = File(...), template_id: Optional[str] = Form(None),
                          core: Core = Depends(get_core)) -> dict:
    data = await read_upload(file)
    return core.library.save_upload(file.filename or "template.pdb", data, template_id)


@router.get("/{template_id}")
def analyze_template(template_id: str, core: Core = Depends(get_core)) -> dict:
    return {**core.library.analyze(template_id), "meta": core.library.describe(template_id)}


@router.get("/{template_id}/file", response_class=PlainTextResponse)
def template_file(template_id: str, core: Core = Depends(get_core)) -> PlainTextResponse:
    path = core.library.path(template_id)
    return PlainTextResponse(path.read_text(encoding="utf-8", errors="replace"), media_type="chemical/x-pdb",
                             headers={"Content-Disposition": f'inline; filename="{path.name}"'})


@router.delete("/{template_id}")
def delete_template(template_id: str, core: Core = Depends(get_core)) -> dict:
    core.library.delete(template_id)
    return {"deleted": template_id}
