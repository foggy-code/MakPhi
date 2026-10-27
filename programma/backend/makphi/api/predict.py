"""Confronto con un predittore esterno di struttura (F9.3): avvio del job `predict` e ricerca del più recente.

Il risultato è quello del job (`GET /api/jobs/{id}`): predizione salvata tra i file, confronto con il modello nel
formato dell'allineamento strutturale (per il componente StructuralComparison) ed esito motivato. Il consenso e il
report lo includono con `predict_job` (rotte del consenso e del report in api/jobs.py).
"""

from __future__ import annotations

from typing import Literal, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from ..core.container import Core
from ..services.predict import KIND, PredictService
from .compute import SeqIn, StructureSource, _label, _project
from .deps import get_core

router = APIRouter(prefix="/api", tags=["Predizione esterna"])


class PredictIn(BaseModel):
    project_id: Optional[str] = None
    predictor: Literal["esmfold", "alphafold"] = Field(
        "esmfold", description="esmfold = servizio ESMFold (la sequenza lascia il computer, serve il consenso "
                               "esmfold_remote); alphafold = modello di AlphaFold DB per l'accessione (va solo "
                               "l'accessione)")
    source: Optional[StructureSource] = Field(None, description="Modello da confrontare con la predizione")
    chain: Optional[str] = Field(None, max_length=4, description="Catena del modello; vuota = la prima")
    sequence: Optional[SeqIn] = Field(None, description="Sequenza da predire; vuota = quella della catena del modello")
    accession: Optional[str] = Field(None, max_length=40, description="Solo alphafold: accessione UniProt o "
                                                                      "AF-<ACC>-F<n>")


def _service(core: Core) -> PredictService:
    return PredictService(core.jobs, core.compute)


@router.post("/predict")
def submit_predict(body: PredictIn, core: Core = Depends(get_core)) -> dict:
    """Job `predict`: errori subito, prima della coda (consenso all'invio, sequenza, struttura, accessione)."""
    params = body.model_dump()
    req = _service(core).request(params, core.settings.get())
    what = "Predizione ESMFold" if body.predictor == "esmfold" else f"Modello AlphaFold DB {req['accession']}"
    subject = _label(body.source) if body.source else req["name"]
    return core.jobs.submit(KIND, params, project_id=_project(core, body.project_id),
                            title=f"{what} · {subject}" + (" (confronto)" if body.source else ""))


@router.get("/predict/latest")
def latest_prediction(type: Literal["template", "job", "upload"] = Query(..., description="Tipo della sorgente"),
                      id: Optional[str] = Query(None, max_length=200), job_id: Optional[str] = Query(None, max_length=64),
                      file: Optional[str] = Query(None, max_length=500), core: Core = Depends(get_core)) -> dict:
    """Confronto con una predizione completato più recente sulla struttura (null se non ce n'è uno) e quello in
    corso, come `/api/analysis/latest`."""
    return _service(core).lookup({"type": type, "id": id, "job_id": job_id, "file": file})
