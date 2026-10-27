"""Tasche, elettrostatica e rifinitura energetica (F7b, F9.1): avvio dei job `pockets`, `electrostatics` e `refine`.

I risultati sono quelli dei job (`GET /api/jobs/{id}`); `GET /api/pockets/latest` e `GET /api/electrostatics/latest`
dicono quale calcolo completato è il più recente per una struttura (stessa sorgente e stesso file) e se ce n'è uno più
recente in coda o in corso, come `/api/analysis/latest`. La mappa del potenziale si scarica fra i file del job
(`/api/jobs/{id}/files/potential.dx?download=true`).
"""

from __future__ import annotations

from typing import Literal, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from ..core.container import Core
from ..domain import electrostatics as es
from ..domain import refinement
from ..services.refine import KIND as REFINE
from ..services.refine import RefineService
from ..services.structure_tools import ELECTROSTATICS, POCKETS, StructureToolsService
from .compute import StructureSource, _label, _project
from .deps import get_core

router = APIRouter(prefix="/api", tags=["Tasche, elettrostatica e rifinitura"])


class PocketsIn(BaseModel):
    project_id: Optional[str] = None
    source: StructureSource


class ElectrostaticsIn(BaseModel):
    project_id: Optional[str] = None
    source: StructureSource
    forcefield: Literal["PARSE", "AMBER"] = Field(es.DEFAULT_FORCEFIELD, description="Cariche e raggi di PDB2PQR")
    ph: float = Field(es.DEFAULT_PH, ge=0, le=14, description="pH per la protonazione con PROPKA")


class RefineIn(BaseModel):
    project_id: Optional[str] = None
    source: StructureSource
    md_ps: float = Field(0.0, ge=0, le=refinement.MD_MAX_PS,
                         description="Dinamica a 300 K con le restrizioni dopo la minimizzazione (ps; 0 = nessuna)")
    hydrogens: bool = Field(False, description="Idrogeni nel PDB rifinito")


def _tools(core: Core) -> StructureToolsService:
    return StructureToolsService(core.jobs, core.compute)


@router.post("/pockets")
def submit_pockets(body: PocketsIn, core: Core = Depends(get_core)) -> dict:
    params = body.model_dump()
    _tools(core).validate(POCKETS, params)  # errori subito, prima della coda
    return core.jobs.submit(POCKETS, params, project_id=_project(core, body.project_id),
                            title=f"Tasche · {_label(body.source)}")


@router.post("/electrostatics")
def submit_electrostatics(body: ElectrostaticsIn, core: Core = Depends(get_core)) -> dict:
    params = body.model_dump()
    _tools(core).validate(ELECTROSTATICS, params)
    return core.jobs.submit(ELECTROSTATICS, params, project_id=_project(core, body.project_id),
                            title=f"Elettrostatica · {_label(body.source)}")


@router.post("/refine")
def submit_refine(body: RefineIn, core: Core = Depends(get_core)) -> dict:
    params = body.model_dump()
    RefineService(core.jobs, core.compute).validate(params)
    md = f", dinamica {body.md_ps:g} ps" if body.md_ps else ""
    return core.jobs.submit(REFINE, params, project_id=_project(core, body.project_id),
                            title=f"Rifinitura · {_label(body.source)}{md}")


def _latest(kind: str, core: Core, type: str, id: Optional[str], job_id: Optional[str],
            file: Optional[str]) -> dict:
    return _tools(core).lookup(kind, {"type": type, "id": id, "job_id": job_id, "file": file})


@router.get("/pockets/latest")
def latest_pockets(type: Literal["template", "job", "upload"] = Query(..., description="Tipo della sorgente"),
                   id: Optional[str] = Query(None, max_length=200), job_id: Optional[str] = Query(None, max_length=64),
                   file: Optional[str] = Query(None, max_length=500), core: Core = Depends(get_core)) -> dict:
    """Ricerca delle tasche completata più recente sulla struttura (null se non ce n'è una) e quella in corso."""
    return _latest(POCKETS, core, type, id, job_id, file)


@router.get("/electrostatics/latest")
def latest_electrostatics(type: Literal["template", "job", "upload"] = Query(..., description="Tipo della sorgente"),
                          id: Optional[str] = Query(None, max_length=200),
                          job_id: Optional[str] = Query(None, max_length=64),
                          file: Optional[str] = Query(None, max_length=500), core: Core = Depends(get_core)) -> dict:
    """Calcolo elettrostatico completato più recente sulla struttura (null se non ce n'è uno) e quello in corso."""
    return _latest(ELECTROSTATICS, core, type, id, job_id, file)
