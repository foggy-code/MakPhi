"""Mutagenesi in silico e scansione di alanina (F9.2): avvio dei job `mutate` e `alascan`, varianti di UniProt,
cronologia per struttura.

I risultati sono quelli dei job (`GET /api/jobs/{id}`); i modelli (selvatico ottimizzato e mutante) sono file del job
con il loro rapporto di qualità (`/api/jobs/{id}/quality/<file>`) e il report HTML del mutante
(`/api/jobs/{id}/report/<mutante>`, sezione Mutagenesi; per la scansione `/api/jobs/{id}/report`).
"""

from __future__ import annotations

from typing import Literal, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from ..core.container import Core
from ..domain import mutations as domain
from ..services.mutations import ALASCAN, MUTATE, MutationService
from .compute import StructureSource, _label, _project
from .deps import get_core

router = APIRouter(prefix="/api", tags=["Mutagenesi"])


class MutateIn(BaseModel):
    project_id: Optional[str] = None
    source: StructureSource
    mutations: str = Field(..., max_length=2000, description="Notazione A:K48R; più mutazioni separate da virgole")
    energy: bool = Field(False, description="Differenza di energia con OpenMM (minimizzazione di selvatico, mutante e "
                                            "tripeptidi di riferimento)")
    seed: int = Field(domain.SEED, ge=-50000, le=-2, description="Seme di MODELLER del primo modello")


class ScanSelection(BaseModel):
    mode: Literal["residues", "interface", "pocket"] = "residues"
    residues: str = Field("", max_length=4000, description="Solo mode=residues: A:30, A:43, A:45-48")
    chain: Optional[str] = Field(None, max_length=4, description="Solo mode=interface: residui d'interfaccia di questa "
                                                                 "catena (vuoto = tutte)")
    pocket_job: Optional[str] = Field(None, max_length=64, description="Solo mode=pocket: job `pockets` completato")
    pocket: Optional[int] = Field(None, ge=1, description="Solo mode=pocket: numero della tasca di fpocket")


class AlaScanIn(BaseModel):
    project_id: Optional[str] = None
    source: StructureSource
    selection: ScanSelection
    energy: bool = Field(False, description="Differenza di energia con OpenMM per ogni residuo (lenta)")
    seed: int = Field(domain.SEED, ge=-50000, le=-2)


def _service(core: Core) -> MutationService:
    return MutationService(core.jobs, core.compute)


@router.post("/mutate")
def submit_mutate(body: MutateIn, core: Core = Depends(get_core)) -> dict:
    params = body.model_dump()
    muts = _service(core).validate_mutate(params)  # errori subito, prima della coda
    params["mutations"] = domain.notation(muts)
    return core.jobs.submit(MUTATE, params, project_id=_project(core, body.project_id),
                            title=f"Mutagenesi · {_label(body.source)} {params['mutations']}"[:200])


@router.post("/alascan")
def submit_alascan(body: AlaScanIn, core: Core = Depends(get_core)) -> dict:
    params = body.model_dump()
    muts, _skipped, selection = _service(core).validate_scan(params)
    return core.jobs.submit(ALASCAN, params, project_id=_project(core, body.project_id),
                            title=f"Scansione di alanina · {_label(body.source)} ({len(muts)} residui)")


def _source(type: str, id: Optional[str], job_id: Optional[str], file: Optional[str]) -> dict:
    return {"type": type, "id": id, "job_id": job_id, "file": file}


@router.get("/mutations/variants")
def uniprot_variants(accession: str = Query(..., max_length=20, description="Accessione UniProtKB"),
                     type: Literal["template", "job", "upload"] = Query(..., description="Tipo della sorgente"),
                     id: Optional[str] = Query(None, max_length=200), job_id: Optional[str] = Query(None, max_length=64),
                     file: Optional[str] = Query(None, max_length=500), chain: Optional[str] = Query(None, max_length=4),
                     core: Core = Depends(get_core)) -> dict:
    """Varianti naturali e di mutagenesi di UniProt (cache 7 giorni) mappate sulla numerazione della struttura."""
    return _service(core).variants(accession, _source(type, id, job_id, file), chain)


@router.get("/mutations/history")
def mutation_history(type: Literal["template", "job", "upload"] = Query(..., description="Tipo della sorgente"),
                     id: Optional[str] = Query(None, max_length=200), job_id: Optional[str] = Query(None, max_length=64),
                     file: Optional[str] = Query(None, max_length=500), core: Core = Depends(get_core)) -> list[dict]:
    """Job di mutagenesi e di scansione di alanina sulla stessa struttura, dal più recente."""
    return _service(core).history(_source(type, id, job_id, file))
