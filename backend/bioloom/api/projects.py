"""Progetti, sequenze e documenti."""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from ..core.container import Core
from .deps import get_core

router = APIRouter(prefix="/api/projects", tags=["Progetti"])


class ProjectIn(BaseModel):
    name: str = Field(..., min_length=1, max_length=120)
    description: str = ""


class ProjectPatch(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None


class SequenceIn(BaseModel):
    name: str = ""
    sequence: str
    source: str = "manual"
    meta: dict = {}


class FastaIn(BaseModel):
    fasta: str
    source: str = "fasta"


class DocumentIn(BaseModel):
    name: str = "Senza titolo"
    kind: str = "pir"
    content: str
    meta: dict = {}


TAG_HELP = "Solo gli elementi con questa etichetta"
FAVORITE_HELP = "true = solo con la stella, false = solo senza"


@router.get("")
def list_projects(tag: Optional[str] = Query(None, max_length=200, description=TAG_HELP),
                  favorite: Optional[bool] = Query(None, description=FAVORITE_HELP),
                  core: Core = Depends(get_core)) -> list[dict]:
    """Progetti con `tags` e `favorite` (F8b)."""
    return core.labels.filter("project", core.projects.list(), tag=tag, favorite=favorite)


@router.post("")
def create_project(body: ProjectIn, core: Core = Depends(get_core)) -> dict:
    return core.projects.create(body.name, body.description)


@router.get("/{project_id}")
def get_project(project_id: str, core: Core = Depends(get_core)) -> dict:
    return core.projects.get(project_id)


@router.patch("/{project_id}")
def update_project(project_id: str, body: ProjectPatch, core: Core = Depends(get_core)) -> dict:
    return core.projects.update(project_id, body.name, body.description)


@router.delete("/{project_id}")
def delete_project(project_id: str, core: Core = Depends(get_core)) -> dict:
    core.delete_project(project_id)
    return {"deleted": project_id}


@router.get("/{project_id}/sequences")
def list_sequences(project_id: str, tag: Optional[str] = Query(None, max_length=200, description=TAG_HELP),
                   favorite: Optional[bool] = Query(None, description=FAVORITE_HELP),
                   core: Core = Depends(get_core)) -> list[dict]:
    """Sequenze del progetto con `tags` e `favorite` (F8b)."""
    return core.labels.filter("sequence", core.projects.list_sequences(project_id), tag=tag, favorite=favorite)


@router.post("/{project_id}/sequences")
def add_sequence(project_id: str, body: SequenceIn, core: Core = Depends(get_core)) -> dict:
    return core.projects.add_sequence(project_id, body.name, body.sequence, body.source, body.meta)


@router.post("/{project_id}/sequences/import")
def import_fasta(project_id: str, body: FastaIn, core: Core = Depends(get_core)) -> list[dict]:
    return core.projects.import_fasta(project_id, body.fasta, body.source)


@router.delete("/{project_id}/sequences/{seq_id}")
def delete_sequence(project_id: str, seq_id: str, core: Core = Depends(get_core)) -> dict:
    core.projects.delete_sequence(project_id, seq_id)
    return {"deleted": seq_id}


@router.get("/{project_id}/documents")
def list_documents(project_id: str, core: Core = Depends(get_core)) -> list[dict]:
    return core.projects.list_documents(project_id)


@router.post("/{project_id}/documents")
def create_document(project_id: str, body: DocumentIn, core: Core = Depends(get_core)) -> dict:
    return core.projects.save_document(project_id, body.name, body.kind, body.content, body.meta)


@router.get("/{project_id}/documents/{doc_id}")
def get_document(project_id: str, doc_id: str, core: Core = Depends(get_core)) -> dict:
    return core.projects.get_document(project_id, doc_id)


@router.put("/{project_id}/documents/{doc_id}")
def update_document(project_id: str, doc_id: str, body: DocumentIn, core: Core = Depends(get_core)) -> dict:
    return core.projects.save_document(project_id, body.name, body.kind, body.content, body.meta, doc_id=doc_id)


@router.delete("/{project_id}/documents/{doc_id}")
def delete_document(project_id: str, doc_id: str, core: Core = Depends(get_core)) -> dict:
    core.projects.delete_document(project_id, doc_id)
    return {"deleted": doc_id}
