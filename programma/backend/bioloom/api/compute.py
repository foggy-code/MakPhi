"""Avvio dei calcoli: ricerca template, allineamenti, MODELLER, qualità, QMEAN, pipeline."""

from __future__ import annotations

from pathlib import Path
from typing import Literal, Optional

from fastapi import APIRouter, Depends, File, Form, Query, UploadFile
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
    engine: Literal["rcsb", "ncbi", "local", "mmseqs", "alphafold"] = "rcsb"
    evalue: float = Field(10.0, gt=0, le=1000)
    max_hits: int = Field(50, ge=1, le=500)
    taxid: str = ""
    use_cache: bool = Field(True, description="False = ricalcola ignorando la cache dei calcoli (e la aggiorna)")
    uniprot: str = Field("", max_length=20, description="Solo motore alphafold: accessione UniProt della sequenza; "
                                                         "vuota = proteine più simili da NCBI BLAST su Swiss-Prot")


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
    use_cache: bool = Field(True, description="Solo multiplo e strutturale: False = ricalcola ignorando la "
                                               "cache dei calcoli (e la aggiorna)")


class ComplexChainIn(BaseModel):
    template_chain: str = Field(..., max_length=4, description="Catena del template che modella questa catena")
    sequence: str = Field("", max_length=20_000)
    name: str = Field("", max_length=200)
    same_as: Optional[str] = Field(None, max_length=4, description="Stessa sequenza della catena indicata (del "
                                                                   "template): omo-oligomeri")


class ComplexIn(BaseModel):
    """Bersaglio a più catene (F5.3): per ogni catena del template la sequenza del bersaglio, nell'ordine."""
    name: str = Field("complesso", max_length=200)
    # limiti (2–12 catene, residui totali) con messaggi chiari in domain/complexes.parse_target
    chains: list[ComplexChainIn] = Field(..., max_length=64)


class Align2dIn(BaseModel):
    project_id: Optional[str] = None
    target: Optional[SeqIn] = None
    templates: list[TemplateRef]
    complex: Optional[ComplexIn] = None


class ModelIn(BaseModel):
    project_id: Optional[str] = None
    target: Optional[SeqIn] = None
    alignment: Optional[str] = None
    templates: list[TemplateRef]
    options: dict = {}
    quality: Literal["best", "all", "none"] = "best"
    extended_quality: bool = False
    complex: Optional[ComplexIn] = Field(None, description="Modalità Complesso: un template, una sequenza per catena")


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
    template_mode: Literal["auto", "fixed", "multi"] = Field(
        "auto", description="auto = il template migliore della ricerca; fixed = template scelti; multi = fino a "
                            "max_templates template complementari della ricerca")
    max_templates: int = Field(3, ge=2, le=5, description="Solo modalità multi: template al massimo per sequenza")
    templates: list[TemplateRef] = []
    fixed_combination: Literal["combined", "each"] = Field(
        "combined", description="Solo template fissi: combined = un modello per sequenza con tutti i template; each = "
                                "un modello per ogni coppia sequenza × template (M × N)")
    engine: Literal["rcsb", "ncbi", "local", "mmseqs", "alphafold"] = "rcsb"
    evalue: float = Field(1e-3, gt=0, le=100)
    twilight: dict = {"filter": False, "identity": 25.0, "coverage": 40.0}
    options: dict = {"models": 3, "refinement": "fast"}
    quality: bool = True
    extended_quality: bool = False
    parallel: int = Field(2, ge=1, le=256)
    use_cache: bool = Field(True, description="Ricerca dei template: False = ricalcola ignorando la cache "
                                               "dei calcoli (e la aggiorna)")


def _project(core: Core, project_id: Optional[str]) -> Optional[str]:
    if project_id:
        core.projects.get(project_id)
        core.projects.touch(project_id)
    return project_id


@router.post("/search")
def submit_search(body: SearchIn, core: Core = Depends(get_core)) -> dict:
    params = body.model_dump()
    if body.engine == "alphafold" and body.uniprot.strip():
        from ..engines import remote

        params["uniprot"] = remote.validate_uniprot_accession(body.uniprot)  # errore subito, prima della coda
    else:
        params["uniprot"] = ""
    label = "AlphaFold DB" if body.engine == "alphafold" else body.engine.upper()
    title = f"Ricerca template ({label}) · {body.query.name}"
    return core.jobs.submit("search", params, project_id=_project(core, body.project_id), title=title)


@router.post("/align")
def submit_align(body: AlignIn, core: Core = Depends(get_core)) -> dict:
    if body.mode == "structural":
        names = " vs ".join((s.id or s.file or "?") + (f":{s.chain}" if s.chain else "") for s in body.structures)
        title = f"Allineamento strutturale · {names}"
    else:
        title = f"Allineamento {'multiplo' if body.mode == 'multiple' else 'pairwise'} · {len(body.records)} sequenze"
    return core.jobs.submit("align", body.model_dump(), project_id=_project(core, body.project_id), title=title)


def _complex(body: ModelIn | Align2dIn) -> Optional[str]:
    """Validazione immediata del complesso (catene, sequenze, limiti, un solo template): etichetta per il titolo del
    job, es. "ubd (2 catene)"."""
    if body.complex is None:
        return None
    from ..domain import complexes
    from ..services.compute import complex_templates

    target = complexes.parse_target(body.complex.model_dump())
    complex_templates(target, [t.model_dump() for t in body.templates])
    return f"{target['name']} ({len(target['chains'])} catene)"


@router.post("/align2d")
def submit_align2d(body: Align2dIn, core: Core = Depends(get_core)) -> dict:
    label = _complex(body)
    if label is None and not (body.target and body.target.sequence):
        raise AppError("Indica la sequenza bersaglio.")
    title = f"Allineamento MODELLER · {label or body.target.name} su {', '.join(t.id for t in body.templates)}"
    return core.jobs.submit("align2d", body.model_dump(), project_id=_project(core, body.project_id), title=title)


@router.post("/model")
def submit_model(body: ModelIn, core: Core = Depends(get_core)) -> dict:
    label = _complex(body)
    if label is None and not body.alignment and not (body.target and body.target.sequence):
        raise AppError("Indica una sequenza bersaglio oppure un allineamento PIR.")
    from ..engines import modeller

    modeller.parse_options(body.options)  # errori di validazione immediati, prima di accodare il job
    target = label or (body.target.name if body.target else "PIR")
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
                   direction: str = "asc", q: str = "", sequence: Optional[int] = Query(None, ge=0),
                   template: Optional[int] = Query(None, ge=0), best: bool = False,
                   core: Core = Depends(get_core)) -> dict:
    """`sequence`, `template` e `best` (solo i migliori di ogni sequenza) valgono per le pipeline M × N."""
    return core.pipeline.items(job_id, page=page, size=size, status=status, sort=sort, direction=direction,
                               search_text=q, sequence=sequence, template=template, best_only=best)


@router.get("/pipeline/{job_id}/matrix")
def pipeline_matrix(job_id: str, core: Core = Depends(get_core)) -> dict:
    """Pipeline M × N: matrice sequenze × template con le metriche di ogni coppia."""
    return core.pipeline.matrix(job_id)


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


class PreflightContext(BaseModel):
    accession: Optional[str] = Field(None, max_length=20, description="Accessione UniProt del bersaglio: domini di "
                                                                      "InterPro (come /api/bio/coverage)")
    search_job: Optional[str] = Field(None, max_length=64, description="Ricerca da cui vengono i template: punteggio "
                                                                       "dei template scelti")


class PreflightIn(BaseModel):
    kind: Literal["model", "loop", "align2d", "pipeline", "quality", "qmean", "search", "analysis",
                  "rerun", "mutate", "alascan", "predict"]  # rerun (F9.4), mutate e alascan (F9.2), predict (F9.3)
    params: dict = Field(default_factory=dict, description="Lo stesso corpo della rotta di avvio del calcolo")
    context: PreflightContext = Field(default_factory=PreflightContext)


def _preflight_bodies() -> dict:
    from .analysis import AnalysisIn  # api/analysis importa questo modulo
    from .mutations import AlaScanIn, MutateIn  # mutagenesi (F9.2), stesso motivo
    from .predict import PredictIn  # come api/analysis (F9.3)
    from .reproducibility import RerunIn

    return {"model": ModelIn, "loop": LoopIn, "align2d": Align2dIn, "pipeline": PipelineIn, "quality": QualityIn,
            "qmean": QmeanIn, "search": SearchIn, "analysis": AnalysisIn, "rerun": RerunIn, "mutate": MutateIn,
            "alascan": AlaScanIn, "predict": PredictIn}


@router.post("/compute/preflight")
def preflight(body: PreflightIn, core: Core = Depends(get_core)) -> dict:
    """Controlli preventivi (F8a), senza avviare nulla: esiti `error` (l'avvio fallirebbe), `warning` (conferma
    richiesta), `info`, con stima del tempo e, per la modellazione, la verifica di copertura. `params` si valida con
    lo stesso modello della rotta di avvio: un campo non valido è un esito `error`, come il 422 dell'avvio."""
    from pydantic import ValidationError

    from ..domain import preflight as rules
    from ..services.preflight import PreflightService

    try:
        params = _preflight_bodies()[body.kind].model_validate(body.params).model_dump()
    except ValidationError as exc:
        details = [f"{'.'.join(str(p) for p in e.get('loc', []))}: {e.get('msg')}" for e in exc.errors()]
        check = rules.invalid("validation_error", "; ".join(details[:5]) or "Richiesta non valida.")
        return {"kind": body.kind, "ok": False, "counts": rules.counts([check]), "checks": [check], "estimate": None,
                "coverage": None}
    return PreflightService(core).run(body.kind, params, body.context.model_dump())
