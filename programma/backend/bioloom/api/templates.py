"""Libreria dei template."""

from __future__ import annotations

from typing import Optional

import anyio.to_thread
from fastapi import APIRouter, Depends, File, Form, Path, Query, UploadFile
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field

from ..core.container import Core
from .deps import get_core, read_upload

router = APIRouter(prefix="/api/templates", tags=["Template"])


class FetchIn(BaseModel):
    pdb_id: str
    format: str = "pdb"


class AlphaFoldIn(BaseModel):
    accession: str = Field(..., max_length=40, description="Accessione UniProt (es. P69905) oppure identificativo "
                                                           "AlphaFold DB AF-<ACC>-F<n>")


@router.get("")
def list_templates(tag: Optional[str] = Query(None, max_length=200, description="Solo i template con questa etichetta"),
                   favorite: Optional[bool] = Query(None, description="true = solo con la stella, false = solo senza"),
                   core: Core = Depends(get_core)) -> list[dict]:
    """Template della libreria con `tags` e `favorite` (F8b)."""
    return core.labels.filter("template", core.library.list(), tag=tag, favorite=favorite)


@router.post("/fetch")
def fetch_template(body: FetchIn, core: Core = Depends(get_core)) -> dict:
    return core.library.fetch(body.pdb_id, body.format)


@router.post("/alphafold")
def fetch_alphafold(body: AlphaFoldIn, core: Core = Depends(get_core)) -> dict:
    """Modello predetto di AlphaFold DB (EBI) nella libreria, con id AF-<ACC>-F<n>: l'accessione va a EBI."""
    return core.library.fetch_alphafold(body.accession)


@router.post("/upload")
async def upload_template(file: UploadFile = File(...), template_id: Optional[str] = Form(None),
                          overwrite: bool = Form(False, description="true (o 1, on) = sostituisce il template con lo "
                                                                    "stesso id, se c'è già"),
                          core: Core = Depends(get_core)) -> dict:
    """Struttura caricata come template (id da `template_id` o dal nome del file), con `replaced` nella risposta.

    Id già in libreria senza `overwrite`: 409 `template_exists` (in `details`: `id`, `jobs`, `projects`), libreria
    invariata. Con `overwrite` il file si sostituisce solo se si legge: uno rovinato lascia intatto il template.
    """
    data = await read_upload(file)
    name = file.filename or "template.pdb"
    # lettura della struttura e analisi in un thread: il loop resta libero per le altre richieste
    return await anyio.to_thread.run_sync(
        lambda: core.library.save_upload(name, data, template_id, overwrite=overwrite,
                                         usage=lambda tid: template_usage(core, tid)))


def template_usage(core: Core, tid: str) -> tuple[int, int]:
    """Job che citano il template nei parametri (`"id": "1UBQ"`, sorgente di un'analisi, template di una
    modellazione o di una pipeline a template fissi) e loro progetti distinti. Le pipeline che lo scelgono dalla
    ricerca non lo citano nei parametri e non si contano."""
    escaped = tid.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    row = core.db.query_one("SELECT COUNT(*) AS jobs, COUNT(DISTINCT project_id) AS projects FROM jobs "
                            "WHERE params LIKE ? ESCAPE '\\'", (f'%"{escaped}"%',)) or {}
    return int(row.get("jobs") or 0), int(row.get("projects") or 0)


@router.get("/{template_id}")
def analyze_template(template_id: str, core: Core = Depends(get_core)) -> dict:
    return {**core.library.analyze(template_id), "meta": core.library.describe(template_id)}


@router.get("/{template_id}/assemblies")
def template_assemblies(template_id: str, core: Core = Depends(get_core)) -> dict:
    """Assemblaggi biologici del file (REMARK 350 o pdbx_struct_assembly): stato oligomerico e catene generate."""
    return core.library.assemblies(template_id)


@router.post("/{template_id}/assemblies/{assembly_id}")
def import_assembly(template_id: str, assembly_id: str = Path(..., pattern=r"^[A-Za-z0-9_-]{1,12}$"),
                    core: Core = Depends(get_core)) -> dict:
    """Importa un assemblaggio come template `<ID>_ASM<n>` (coordinate generate con gli operatori di simmetria)."""
    return core.library.import_assembly(template_id, assembly_id)


@router.get("/{template_id}/file", response_class=PlainTextResponse)
def template_file(template_id: str, core: Core = Depends(get_core)) -> PlainTextResponse:
    path = core.library.path(template_id)
    return PlainTextResponse(path.read_text(encoding="utf-8", errors="replace"), media_type="chemical/x-pdb",
                             headers={"Content-Disposition": f'inline; filename="{path.name}"'})


@router.delete("/{template_id}")
def delete_template(template_id: str, core: Core = Depends(get_core)) -> dict:
    core.library.delete(template_id)
    return {"deleted": template_id}
