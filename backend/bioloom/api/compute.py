"""Avvio dei calcoli: ricerca template, allineamenti, MODELLER, qualità, QMEAN, pipeline."""

from __future__ import annotations

from pathlib import Path
from typing import Literal, Optional

from fastapi import APIRouter, Depends, File, Form, UploadFile
from fastapi.responses import PlainTextResponse, Response
from pydantic import BaseModel, Field

from ..core.container import Core
from ..core.errors import AppError
from .deps import get_core, read_upload

router = APIRouter(prefix="/api", tags=["Calcoli"])


class SeqIn(BaseModel):
    name: str = "target"
    sequence: str


class TemplateRef(BaseModel):
    id: str
    chains: list[str] = []
    hetatm: bool = False


class StructureSource(BaseModel):
    type: Literal["template", "job", "upload"]
    id: Optional[str] = None
    job_id: Optional[str] = None
    file: Optional[str] = None
    chain: Optional[str] = None


class SearchIn(BaseModel):
    project_id: Optional[str] = None
    query: SeqIn
    engine: Literal["rcsb", "ncbi", "local", "mmseqs"] = "rcsb"
    evalue: float = Field(10.0, gt=0, le=1000)
    max_hits: int = Field(50, ge=1, le=500)
    taxid: str = ""


class AlignIn(BaseModel):
    project_id: Optional[str] = None
    mode: Literal["pairwise", "multiple", "structural"] = "pairwise"
    records: list[SeqIn] = []
    structures: list[StructureSource] = []
    kind: Literal["protein", "nucleic"] = "protein"
    algorithm: Literal["global", "local"] = "global"
    matrix: str = ""
    gap_open: float = Field(10.0, ge=0, le=100)
    gap_extend: float = Field(0.5, ge=0, le=100)
    tool: Literal["auto", "builtin", "clustalo", "mafft", "muscle", "tmalign", "usalign"] = "auto"


class Align2dIn(BaseModel):
    project_id: Optional[str] = None
    target: SeqIn
    templates: list[TemplateRef]


class ModelIn(BaseModel):
    project_id: Optional[str] = None
    target: Optional[SeqIn] = None
    alignment: Optional[str] = None
    templates: list[TemplateRef]
    options: dict = {}
    quality: Literal["best", "all", "none"] = "best"
    extended_quality: bool = False


class LoopIn(BaseModel):
    project_id: Optional[str] = None
    source: StructureSource
    chain: str = "A"
    start: int
    end: int
    models: int = Field(4, ge=1, le=50)
    level: Literal["very_fast", "fast", "slow"] = "fast"


class QualityIn(BaseModel):
    project_id: Optional[str] = None
    source: StructureSource
    dope: bool = True
    procheck: bool = True
    prosa: bool = True


class QmeanIn(BaseModel):
    project_id: Optional[str] = None
    source: StructureSource
    mode: Optional[Literal["auto", "local", "remote"]] = None
    method: Literal["auto", "QMEAN", "QMEANDisCo"] = "auto"
    fallback: Optional[bool] = Field(None, description="Fallback su SWISS-MODEL; vuoto = come nelle Impostazioni")
    database: Optional[str] = Field(None, max_length=300, description="Versione di UniClust30/UniRef30")


class PipelineIn(BaseModel):
    project_id: Optional[str] = None
    name: str = ""
    fasta: str = ""
    records: list[SeqIn] = []
    template_mode: Literal["auto", "fixed"] = "auto"
    templates: list[TemplateRef] = []
    engine: Literal["rcsb", "ncbi", "local", "mmseqs"] = "rcsb"
    evalue: float = Field(1e-3, gt=0, le=100)
    twilight: dict = {"filter": False, "identity": 25.0, "coverage": 40.0}
    options: dict = {"models": 3, "refinement": "fast"}
    quality: bool = True
    extended_quality: bool = False
    parallel: int = Field(2, ge=1, le=8)


def _project(core: Core, project_id: Optional[str]) -> Optional[str]:
    if project_id:
        core.projects.get(project_id)
        core.projects.touch(project_id)
    return project_id


@router.post("/search")
def submit_search(body: SearchIn, core: Core = Depends(get_core)) -> dict:
    title = f"Ricerca template ({body.engine.upper()}) · {body.query.name}"
    return core.jobs.submit("search", body.model_dump(), project_id=_project(core, body.project_id), title=title)


@router.post("/align")
def submit_align(body: AlignIn, core: Core = Depends(get_core)) -> dict:
    if body.mode == "structural":
        names = " vs ".join((s.id or s.file or "?") + (f":{s.chain}" if s.chain else "") for s in body.structures)
        title = f"Allineamento strutturale · {names}"
    else:
        title = f"Allineamento {'multiplo' if body.mode == 'multiple' else 'pairwise'} · {len(body.records)} sequenze"
    return core.jobs.submit("align", body.model_dump(), project_id=_project(core, body.project_id), title=title)


@router.post("/align2d")
def submit_align2d(body: Align2dIn, core: Core = Depends(get_core)) -> dict:
    title = f"Allineamento MODELLER · {body.target.name} su {', '.join(t.id for t in body.templates)}"
    return core.jobs.submit("align2d", body.model_dump(), project_id=_project(core, body.project_id), title=title)


@router.post("/model")
def submit_model(body: ModelIn, core: Core = Depends(get_core)) -> dict:
    if not body.alignment and not (body.target and body.target.sequence):
        raise AppError("Indica una sequenza bersaglio oppure un allineamento PIR.")
    from ..engines import modeller

    modeller.parse_options(body.options)  # errori di validazione immediati, prima di accodare il job
    target = body.target.name if body.target else "PIR"
    title = f"Modello {target} · {', '.join(t.id for t in body.templates)}"
    return core.jobs.submit("model", body.model_dump(), project_id=_project(core, body.project_id), title=title)


@router.post("/model/loop")
def submit_loop(body: LoopIn, core: Core = Depends(get_core)) -> dict:
    title = f"Raffinamento loop {body.start}-{body.end}:{body.chain}"
    return core.jobs.submit("loop", body.model_dump(), project_id=_project(core, body.project_id), title=title)


def _label(source: StructureSource) -> str:
    return source.id or (Path(source.file).name if source.file else "struttura")


@router.post("/quality")
def submit_quality(body: QualityIn, core: Core = Depends(get_core)) -> dict:
    core.compute.resolve_structure(body.source.model_dump())
    return core.jobs.submit("quality", body.model_dump(), project_id=_project(core, body.project_id),
                            title=f"Qualità · {_label(body.source)}")


@router.post("/quality/upload")
async def upload_for_quality(file: UploadFile = File(...), project_id: Optional[str] = Form(None),
                             dope: bool = Form(True), procheck: bool = Form(True), prosa: bool = Form(True),
                             core: Core = Depends(get_core)) -> dict:
    from .files import store_structure

    stored = store_structure(core, file.filename or "", await read_upload(file))
    params = {"project_id": project_id, "source": stored["source"], "dope": dope, "procheck": procheck,
              "prosa": prosa}
    return core.jobs.submit("quality", params, project_id=_project(core, project_id),
                            title=f"Qualità · {stored['label']}")


@router.post("/qmean")
def submit_qmean(body: QmeanIn, core: Core = Depends(get_core)) -> dict:
    core.compute.resolve_structure(body.source.model_dump())
    return core.jobs.submit("qmean", body.model_dump(), project_id=_project(core, body.project_id),
                            title=f"QMEAN · {_label(body.source)}")


@router.post("/pipeline")
def submit_pipeline(body: PipelineIn, core: Core = Depends(get_core)) -> dict:
    return core.pipeline.submit(body.model_dump(), _project(core, body.project_id))


@router.get("/pipeline/{job_id}/items")
def pipeline_items(job_id: str, page: int = 1, size: int = 25, status: Optional[str] = None, sort: str = "idx",
                   direction: str = "asc", q: str = "", core: Core = Depends(get_core)) -> dict:
    return core.pipeline.items(job_id, page=page, size=size, status=status, sort=sort, direction=direction,
                               search_text=q)


@router.get("/pipeline/{job_id}/items/{idx}")
def pipeline_item(job_id: str, idx: int, core: Core = Depends(get_core)) -> dict:
    return core.pipeline.item(job_id, idx)


@router.get("/pipeline/{job_id}/export.csv", response_class=PlainTextResponse)
def pipeline_csv(job_id: str, core: Core = Depends(get_core)) -> Response:
    core.jobs.get(job_id, include_result=False)
    return Response(core.pipeline.export_csv(job_id), media_type="text/csv",
                    headers={"Content-Disposition": f'attachment; filename="pipeline_{job_id}.csv"'})


@router.get("/pipeline/{job_id}/export.zip")
def pipeline_zip(job_id: str, core: Core = Depends(get_core)) -> Response:
    from .jobs import send_temp, temp_download

    core.jobs.get(job_id, include_result=False)
    dest = temp_download(core, ".zip")
    try:
        core.pipeline.export_zip(job_id, dest)
    except BaseException:
        dest.unlink(missing_ok=True)
        raise
    return send_temp(dest, f"pipeline_{job_id}.zip", "application/zip")
