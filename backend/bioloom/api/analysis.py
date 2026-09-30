"""Analisi strutturale (F7): interazioni, interfacce fra catene e conservazione mappata sulla struttura.

`POST /api/analysis` accoda un job `analysis` (risultato in `GET /api/jobs/{id}`); `GET /api/analysis/latest` dice
quale analisi completata è la più recente per una struttura (la pagina Qualità la mostra, il report la riassume) e
se ce n'è una più recente ancora in coda o in corso (la pagina la segue).
"""

from __future__ import annotations

from typing import Literal, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from ..core import cache
from ..core.container import Core
from ..core.errors import AppError
from ..services.analysis import KIND, MAX_MSA_TEXT, AnalysisService
from .compute import StructureSource, _label, _project
from .deps import get_core

router = APIRouter(prefix="/api/analysis", tags=["Analisi strutturale"])


class MsaIn(BaseModel):
    job_id: Optional[str] = Field(None, max_length=64, description="Job `align` completato (righe `aligned`)")
    text: Optional[str] = Field(None, max_length=MAX_MSA_TEXT,
                                description="Allineamento o sequenze (FASTA, Clustal, Stockholm, …); sequenze di "
                                            "lunghezza diversa si allineano prima con l'allineamento multiplo")
    row: Optional[str] = Field(None, max_length=200, description="Riga della sequenza della struttura; vuota = "
                                                                 "la più simile a ogni catena")
    tool: Literal["auto", "builtin", "clustalo", "mafft", "muscle"] = "auto"
    use_cache: bool = True


class AnalysisIn(BaseModel):
    project_id: Optional[str] = None
    source: StructureSource
    chains: list[str] = Field([], max_length=64, description="Catene per la conservazione; vuoto = tutte")
    msa: Optional[MsaIn] = Field(None, description="Allineamento multiplo per la conservazione (facoltativo)")


@router.post("")
def submit_analysis(body: AnalysisIn, core: Core = Depends(get_core)) -> dict:
    params = body.model_dump()
    AnalysisService(core.jobs, core.compute).validate(params)  # errori subito, prima della coda
    return core.jobs.submit(KIND, params, project_id=_project(core, body.project_id),
                            title=f"Analisi strutturale · {_label(body.source)}")


@router.get("/latest")
def latest_analysis(type: Literal["template", "job", "upload"] = Query(..., description="Tipo della sorgente"),
                    id: Optional[str] = Query(None, max_length=200), job_id: Optional[str] = Query(None, max_length=64),
                    file: Optional[str] = Query(None, max_length=500), core: Core = Depends(get_core)) -> dict:
    """Analisi strutturale completata più recente sulla struttura indicata (null se non ce n'è una): stessa sorgente
    e, se il file esiste ancora, stesso contenuto. `active_job_id`: analisi della stessa sorgente in coda o in corso,
    lanciata dopo quella completata (null se non c'è)."""
    source = {"type": type, "id": id, "job_id": job_id, "file": file}
    try:
        path, _name = core.compute.resolve_structure(source)
        fingerprint: Optional[str] = cache.content_hash(path)
    except (AppError, OSError):
        fingerprint = None
    service = AnalysisService(core.jobs, core.compute)
    job = service.latest(source, fingerprint)
    active = service.active(source)
    if active and job and str(active.get("created_at") or "") < str(job.get("created_at") or ""):
        active = None  # l'analisi completata è stata lanciata dopo: è quella da mostrare
    return {"job_id": job["id"] if job else None, "finished_at": job.get("finished_at") if job else None,
            "active_job_id": active["id"] if active else None}
