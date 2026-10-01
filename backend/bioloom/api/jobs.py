"""Job: elenco, stato, log incrementale, file prodotti, annullamento, ripresa, eliminazione, report HTML.

I riepiloghi dei job (elenco, dettaglio, stato, annullamento, ripresa) hanno `eta_seconds`: tempo residuo
stimato dei job in esecuzione, null se non stimabile; e `queue_position`: posizione (da 1) di un job in coda
nella coda del suo pool, null per gli altri. `GET /api/jobs/events` e `GET /api/jobs/{id}/events`
sono flussi Server-Sent Events (services/live.py) per seguire i job senza polling.
"""

from __future__ import annotations

import os
import tempfile
import zipfile
from pathlib import Path
from typing import AsyncIterator, Literal, Optional

import anyio.to_thread
from fastapi import APIRouter, Depends, Query
from fastapi.responses import FileResponse, HTMLResponse, PlainTextResponse, Response, StreamingResponse
from pydantic import BaseModel, Field
from starlette.background import BackgroundTask

from ..core.container import Core
from ..core.errors import AppError
from ..core.storage import read_json
from ..domain import exports, report
from ..services.reports import ReportService
from .deps import get_core

router = APIRouter(prefix="/api/jobs", tags=["Job"])

MEDIA = {".pdb": "chemical/x-pdb", ".json": "application/json", ".ali": "text/plain", ".pap": "text/plain",
         ".fasta": "text/plain", ".aln": "text/plain", ".log": "text/plain", ".py": "text/plain", ".csv": "text/csv"}
# niente cache né buffering dei proxy (nginx: X-Accel-Buffering) sugli eventi in tempo reale
SSE_HEADERS = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
SSE_RESPONSE = {200: {"content": {"text/event-stream": {}}, "description": "Flusso Server-Sent Events"},
                503: {"description": "Troppi flussi aperti: usare il polling"}}


class ResumeIn(BaseModel):
    scope: Literal["incomplete", "failed"] = Field(
        "incomplete", description="incomplete = tutti gli elementi non conclusi; failed = solo i falliti")


def _event_stream(stream: AsyncIterator[str]) -> StreamingResponse:
    return StreamingResponse(stream, media_type="text/event-stream", headers=SSE_HEADERS)


@router.get("")
def list_jobs(project_id: Optional[str] = None, kind: Optional[str] = None,
              status: Optional[str] = Query(None, description="Stati separati da virgola"),
              limit: int = 100, offset: int = 0,
              tag: Optional[str] = Query(None, max_length=200, description="Solo i job con questa etichetta"),
              favorite: bool = Query(False, description="true = solo i job con la stella"),
              core: Core = Depends(get_core)) -> list[dict]:
    """Job (dal più recente) con `tags` e `favorite` (F8b); `tag` e `favorite` filtrano in SQL, prima del limite."""
    statuses = [s for s in (status or "").split(",") if s]
    ids: Optional[set[str]] = None
    if tag:
        ids = core.labels.ids_with("job", tag)
    if favorite:
        stars = core.labels.favorite_ids("job")
        ids = stars if ids is None else ids & stars
    rows = core.jobs.list(project_id=project_id, kind=kind, statuses=statuses or None, limit=limit, offset=offset,
                          ids=ids)
    return core.jobs.with_eta(core.labels.annotate("job", rows))


@router.get("/events", response_class=StreamingResponse, responses=SSE_RESPONSE)
async def jobs_events(project_id: Optional[str] = None, core: Core = Depends(get_core)) -> StreamingResponse:
    """Flusso SSE dell'elenco dei job (tutti o di un progetto): eventi `ready`, `job`, `items`, `active`,
    `deleted`, `resync`, `reconnect`. Dichiarata prima di `/{job_id}`, che altrimenti la catturerebbe."""
    return _event_stream(core.live.list_events(project_id))


@router.get("/{job_id}")
def get_job(job_id: str, core: Core = Depends(get_core)) -> dict:
    job = core.jobs.with_eta(core.jobs.get(job_id))
    job["files"] = core.jobs.list_files(job_id)
    job.update(core.pipeline.resume_state(job))  # resumable, resumable_items (solo le pipeline si riprendono)
    return job


@router.get("/{job_id}/status")
def job_status(job_id: str, core: Core = Depends(get_core)) -> dict:
    return core.jobs.with_eta(core.jobs.get(job_id, include_result=False))


@router.get("/{job_id}/events", response_class=StreamingResponse, responses=SSE_RESPONSE)
async def job_events(job_id: str, offset: int = Query(0, ge=0, description="Byte del log da cui ripartire"),
                     log: bool = Query(True, description="false = solo stato, senza righe di log"),
                     core: Core = Depends(get_core)) -> StreamingResponse:
    """Flusso SSE di un job: `status` (riepilogo con eta_seconds) quando cambia, `log` (righe nuove dal byte
    `offset`, con l'offset successivo), `end` quando il job è terminato; poi il flusso si chiude."""
    await anyio.to_thread.run_sync(core.jobs.get, job_id, False)  # 404 (o 400) prima di aprire il flusso
    return _event_stream(core.live.job_events(job_id, offset, log))


@router.get("/{job_id}/log")
def job_log(job_id: str, offset: int = 0, core: Core = Depends(get_core)) -> dict:
    return core.jobs.read_log(job_id, offset)


@router.post("/{job_id}/cancel")
def cancel_job(job_id: str, core: Core = Depends(get_core)) -> dict:
    return core.jobs.with_eta(core.jobs.cancel(job_id))


@router.post("/{job_id}/resume")
def resume_job(job_id: str, body: Optional[ResumeIn] = None, core: Core = Depends(get_core)) -> dict:
    """Riprende una pipeline fallita, annullata o completata con elementi falliti: rielabora solo gli elementi
    non conclusi (o i falliti)."""
    return core.jobs.with_eta(core.pipeline.resume(job_id, (body or ResumeIn()).scope))


@router.delete("/{job_id}")
def delete_job(job_id: str, core: Core = Depends(get_core)) -> dict:
    core.jobs.delete(job_id)
    return {"deleted": job_id}


@router.get("/{job_id}/files/{path:path}")
def job_file(job_id: str, path: str, download: bool = False, core: Core = Depends(get_core)):
    file = core.jobs.file_path(job_id, path)
    media = MEDIA.get(file.suffix.lower(), "application/octet-stream")
    if download:
        return FileResponse(file, media_type=media, filename=file.name)
    return FileResponse(file, media_type=media)


@router.get("/{job_id}/quality/{model:path}")
def model_quality(job_id: str, model: str, core: Core = Depends(get_core)) -> dict:
    report = read_json(core.jobs.file_path(job_id, f"{model}.quality.json"))
    if report is None:
        raise AppError("Rapporto di qualità non disponibile per questo modello.", status=404)
    return report


QMEAN_JOB_HELP = "Job qmean completato sulla stessa struttura"
PREDICT_JOB_HELP = "Job predict (confronto con ESMFold o AlphaFold DB) completato sulla stessa struttura (F9.3)"


@router.get("/{job_id}/consensus")
def quality_consensus(job_id: str, qmean_job: Optional[str] = Query(None, max_length=64, description=QMEAN_JOB_HELP),
                      predict_job: Optional[str] = Query(None, max_length=64, description=PREDICT_JOB_HELP),
                      core: Core = Depends(get_core)) -> dict:
    """Consenso di un'analisi di qualità (job quality), con QMEAN e, per i modelli, GA341/zDOPE di MODELLER."""
    return core.compute.consensus(job_id, None, qmean_job or None, predict_job or None)


@router.get("/{job_id}/consensus/{model:path}")
def model_consensus(job_id: str, model: str,
                    qmean_job: Optional[str] = Query(None, max_length=64, description=QMEAN_JOB_HELP),
                    predict_job: Optional[str] = Query(None, max_length=64, description=PREDICT_JOB_HELP),
                    core: Core = Depends(get_core)) -> dict:
    """Consenso di un modello di un job model, loop o pipeline (rapporto `<modello>.quality.json`)."""
    return core.compute.consensus(job_id, model, qmean_job or None, predict_job or None)


class ReportIn(BaseModel):
    image_png_base64: Optional[str] = Field(
        None, description="Vista 3D in PNG (base64 o data URI data:image/png;base64,…, come pngURI di 3Dmol), al "
                          f"massimo {report.MAX_IMAGE_BYTES // (1024 * 1024)} MB")


# il report è un documento autonomo: nessuno script, nessuna risorsa esterna anche aperto dal server
REPORT_HEADERS = {"Content-Security-Policy": report.CSP, "X-Content-Type-Options": "nosniff",
                  "Cache-Control": "no-store"}
DOWNLOAD_HELP = "true = da scaricare (attachment); false = da aprire nel browser (inline)"


REPORT_PREDICT_HELP = (PREDICT_JOB_HELP + "; senza, il confronto più recente sulla stessa struttura, se c'è")


def _report(core: Core, job_id: str, model: Optional[str], body: Optional[ReportIn], qmean_job: Optional[str],
            download: bool, predict_job: Optional[str] = None) -> HTMLResponse:
    image = report.decode_png(body.image_png_base64) if body and body.image_png_base64 else None
    text, filename = ReportService(core.jobs, core.compute, core.db).build(job_id, model, qmean_job=qmean_job or None,
                                                                           image_png=image,
                                                                           predict_job=predict_job or None)
    disposition = "attachment" if download else "inline"
    return HTMLResponse(text, headers={**REPORT_HEADERS, "Content-Disposition": f'{disposition}; filename="{filename}"'})


@router.get("/{job_id}/report", response_class=HTMLResponse)
def quality_report(job_id: str, qmean_job: Optional[str] = Query(None, max_length=64, description=QMEAN_JOB_HELP),
                   download: bool = Query(True, description=DOWNLOAD_HELP),
                   predict_job: Optional[str] = Query(None, max_length=64, description=REPORT_PREDICT_HELP),
                   core: Core = Depends(get_core)):
    """Report HTML autonomo (stampabile in PDF) di un'analisi di qualità: struttura, voto, consenso, grafici."""
    return _report(core, job_id, None, None, qmean_job, download, predict_job)


@router.post("/{job_id}/report", response_class=HTMLResponse)
def quality_report_with_image(job_id: str, body: Optional[ReportIn] = None,
                              qmean_job: Optional[str] = Query(None, max_length=64, description=QMEAN_JOB_HELP),
                              download: bool = Query(True, description=DOWNLOAD_HELP),
                              predict_job: Optional[str] = Query(None, max_length=64, description=REPORT_PREDICT_HELP),
                              core: Core = Depends(get_core)):
    """Come la GET, con l'immagine corrente del viewer 3D (PNG) incorporata nel report."""
    return _report(core, job_id, None, body, qmean_job, download, predict_job)


@router.get("/{job_id}/report/{model:path}", response_class=HTMLResponse)
def model_report(job_id: str, model: str,
                 qmean_job: Optional[str] = Query(None, max_length=64, description=QMEAN_JOB_HELP),
                 download: bool = Query(True, description=DOWNLOAD_HELP),
                 predict_job: Optional[str] = Query(None, max_length=64, description=REPORT_PREDICT_HELP),
                 core: Core = Depends(get_core)):
    """Report HTML autonomo (stampabile in PDF) di un modello di un job model, loop o pipeline: bersaglio,
    template, allineamento, parametri di MODELLER, modelli, qualità del modello, legenda e riferimenti."""
    return _report(core, job_id, model, None, qmean_job, download, predict_job)


@router.post("/{job_id}/report/{model:path}", response_class=HTMLResponse)
def model_report_with_image(job_id: str, model: str, body: Optional[ReportIn] = None,
                            qmean_job: Optional[str] = Query(None, max_length=64, description=QMEAN_JOB_HELP),
                            download: bool = Query(True, description=DOWNLOAD_HELP),
                            predict_job: Optional[str] = Query(None, max_length=64, description=REPORT_PREDICT_HELP),
                            core: Core = Depends(get_core)):
    """Come la GET, con l'immagine corrente del viewer 3D (PNG) incorporata nel report."""
    return _report(core, job_id, model, body, qmean_job, download, predict_job)


@router.get("/{job_id}/pymol/{model:path}", response_class=PlainTextResponse)
def model_pymol(job_id: str, model: str, scheme: str = "ss", core: Core = Depends(get_core)) -> Response:
    file = core.jobs.file_path(job_id, model)
    name = file.stem.replace(".", "_")
    script = exports.pymol_script(file.read_text(encoding="utf-8", errors="replace"), name, scheme)
    return PlainTextResponse(script, headers={"Content-Disposition": f'attachment; filename="{name}.pml"'})


def temp_download(core: Core, suffix: str) -> Path:
    """File temporaneo nello scratch per un download generato al volo (eliminato dopo l'invio)."""
    core.paths.scratch_tmp.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(dir=core.paths.scratch_tmp, prefix="download_", suffix=suffix)
    os.close(fd)
    return Path(name)


def send_temp(path: Path, filename: str, media_type: str) -> FileResponse:
    return FileResponse(path, media_type=media_type, filename=filename,
                        background=BackgroundTask(lambda: path.unlink(missing_ok=True)))


@router.get("/{job_id}/archive")
def job_archive(job_id: str, core: Core = Depends(get_core)) -> Response:
    job = core.jobs.get(job_id, include_result=False)
    dest = temp_download(core, ".zip")
    try:
        with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as zf:
            for info in core.jobs.list_files(job_id):
                zf.write(core.jobs.file_path(job_id, info["path"]), info["path"])
    except BaseException:
        dest.unlink(missing_ok=True)
        raise
    return send_temp(dest, f"bioloom_{job['kind']}_{job_id}.zip", "application/zip")
