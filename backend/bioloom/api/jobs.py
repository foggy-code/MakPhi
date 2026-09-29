"""Job: elenco, stato, log incrementale, file prodotti, annullamento, eliminazione."""

from __future__ import annotations

import os
import tempfile
import zipfile
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, Query
from fastapi.responses import FileResponse, PlainTextResponse, Response
from starlette.background import BackgroundTask

from ..core.container import Core
from ..core.errors import AppError
from ..core.storage import read_json
from ..domain import exports
from .deps import get_core

router = APIRouter(prefix="/api/jobs", tags=["Job"])

MEDIA = {".pdb": "chemical/x-pdb", ".json": "application/json", ".ali": "text/plain", ".pap": "text/plain",
         ".fasta": "text/plain", ".aln": "text/plain", ".log": "text/plain", ".py": "text/plain", ".csv": "text/csv"}


@router.get("")
def list_jobs(project_id: Optional[str] = None, kind: Optional[str] = None,
              status: Optional[str] = Query(None, description="Stati separati da virgola"),
              limit: int = 100, offset: int = 0, core: Core = Depends(get_core)) -> list[dict]:
    statuses = [s for s in (status or "").split(",") if s]
    return core.jobs.list(project_id=project_id, kind=kind, statuses=statuses or None, limit=limit, offset=offset)


@router.get("/{job_id}")
def get_job(job_id: str, core: Core = Depends(get_core)) -> dict:
    job = core.jobs.get(job_id)
    job["files"] = core.jobs.list_files(job_id)
    return job


@router.get("/{job_id}/status")
def job_status(job_id: str, core: Core = Depends(get_core)) -> dict:
    return core.jobs.get(job_id, include_result=False)


@router.get("/{job_id}/log")
def job_log(job_id: str, offset: int = 0, core: Core = Depends(get_core)) -> dict:
    return core.jobs.read_log(job_id, offset)


@router.post("/{job_id}/cancel")
def cancel_job(job_id: str, core: Core = Depends(get_core)) -> dict:
    return core.jobs.cancel(job_id)


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
