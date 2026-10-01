"""Riproducibilità (F9.4): pacchetto di un job, anteprima di un pacchetto caricato, riesecuzione.

- `GET /api/jobs/{id}/package`: pacchetto ZIP di un job completato (services/reproducibility.py), scritto su un file
  temporaneo dello scratch e inviato con `FileResponse`, come gli altri archivi.
- `POST /api/jobs/rerun/inspect` (multipart, campo `file`): il pacchetto si verifica (archivio, manifest, checksum) e si
  conserva nello scratch per 24 ore; la risposta è l'anteprima del manifest con `package_id` ed esiti (errori, avvisi
  sulle versioni, input remoti). I controlli preventivi (`POST /api/compute/preflight`, `kind: "rerun"`) e l'avvio lo
  indicano con `package_id`.
- `POST /api/jobs/rerun` (multipart: `file` oppure `package_id`, `project_id` facoltativo): job `rerun`.
- `DELETE /api/jobs/rerun/{package_id}`: elimina un pacchetto caricato che non serve più.
"""

from __future__ import annotations

from typing import Optional

import anyio.to_thread
from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from ..core.container import Core
from ..core.errors import AppError
from .compute import _project
from .deps import get_core
from .jobs import send_temp, temp_download

router = APIRouter(prefix="/api/jobs", tags=["Riproducibilità"])


class RerunIn(BaseModel):
    """Corpo dei controlli preventivi di una riesecuzione (`kind: "rerun"`)."""
    project_id: Optional[str] = None
    package_id: str = Field(..., min_length=32, max_length=32, description="Pacchetto caricato con /rerun/inspect")


def _access_secret(request: Request) -> tuple[str, ...]:
    """Chiave d'accesso del server (modalità server): da non esportare mai, come le licenze."""
    cfg = getattr(request.app.state, "auth", None)
    return (cfg.key,) if cfg is not None and getattr(cfg, "key", "") else ()


@router.get("/{job_id}/package", response_class=FileResponse)
def job_package(job_id: str, request: Request, core: Core = Depends(get_core)) -> FileResponse:
    """Pacchetto di riproducibilità del job: manifest (versioni, parametri, semi, impostazioni, input remoti,
    checksum), input, output, script MODELLER, log, LEGGIMI. Mai chiavi di licenza, email o percorsi locali."""
    core.jobs.get(job_id, include_result=False)  # 404 prima di creare il file temporaneo
    dest = temp_download(core, ".zip")
    try:
        name = core.repro.export(job_id, dest, extra_secrets=_access_secret(request))
    except BaseException:
        dest.unlink(missing_ok=True)
        raise
    return send_temp(dest, name, "application/zip")


@router.post("/rerun/inspect")
async def inspect_package(file: UploadFile = File(...), core: Core = Depends(get_core)) -> dict:
    """Verifica un pacchetto caricato e ne restituisce l'anteprima; il pacchetto resta disponibile 24 ore."""
    package_id = await anyio.to_thread.run_sync(core.repro.stage, file.file)
    try:
        return await anyio.to_thread.run_sync(core.repro.inspect, package_id)
    except BaseException:
        core.repro.discard(package_id)
        raise


@router.post("/rerun")
async def rerun_package(file: Optional[UploadFile] = File(None), package_id: Optional[str] = Form(None),
                        project_id: Optional[str] = Form(None), core: Core = Depends(get_core)) -> dict:
    """Riesegue il calcolo di un pacchetto (caricato ora o con /rerun/inspect) e confronta i risultati."""
    if (file is None) == (not package_id):
        raise AppError("Carica il pacchetto nel campo «file» oppure indica il «package_id» di un pacchetto già "
                       "caricato.", code="package_required")
    project = _project(core, project_id or None)
    if file is not None:
        staged = await anyio.to_thread.run_sync(core.repro.stage, file.file)
        try:
            path = core.repro.staged_path(staged)
            job = await anyio.to_thread.run_sync(lambda: core.repro.submit(path, project_id=project, move=True))
        finally:
            core.repro.discard(staged)
    else:
        path = core.repro.staged_path(str(package_id))
        job = await anyio.to_thread.run_sync(lambda: core.repro.submit(path, project_id=project))
    return core.jobs.with_eta(job)


@router.delete("/rerun/{package_id}")
def discard_package(package_id: str, core: Core = Depends(get_core)) -> dict:
    core.repro.discard(package_id)
    return {"deleted": package_id}
