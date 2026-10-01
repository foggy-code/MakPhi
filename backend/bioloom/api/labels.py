"""Etichette, preferiti e ricerca globale (F8b).

Gli elementi si indicano con tipo (`project`, `job`, `sequence`, `template`) e id; le etichette si normalizzano
(minuscole, spazi → '-', al più 32 caratteri tra lettere, cifre e - _ . : +). Gli elenchi di progetti, job,
sequenze e template riportano `tags` e `favorite` e accettano il filtro `?tag=`.
"""

from __future__ import annotations

from typing import Literal, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from ..core.container import Core
from ..domain import labels as labels_domain
from ..domain import textsearch
from ..services import globalsearch
from .deps import get_core

router = APIRouter(tags=["Etichette e ricerca"])

EntityType = Literal["project", "job", "sequence", "template"]
GroupType = Literal["project", "sequence", "document", "job", "template", "tag"]


class TagIn(BaseModel):
    tag: str = Field(..., min_length=1, max_length=200,
                     description=f"Etichetta; normalizzata (al più {labels_domain.TAG_MAX_LENGTH} caratteri)")


@router.get("/api/tags")
def list_tags(entity_type: Optional[EntityType] = None, core: Core = Depends(get_core)) -> list[dict]:
    """Etichette in uso, con il numero di elementi in totale e per tipo (le più usate prima)."""
    return core.labels.tag_counts(entity_type)


@router.get("/api/tags/{entity_type}/{entity_id}")
def entity_labels(entity_type: EntityType, entity_id: str, core: Core = Depends(get_core)) -> dict:
    return core.labels.get(entity_type, entity_id)


@router.post("/api/tags/{entity_type}/{entity_id}")
def add_tag(entity_type: EntityType, entity_id: str, body: TagIn, core: Core = Depends(get_core)) -> dict:
    """Aggiunge un'etichetta all'elemento (404 se non esiste; già presente: nessun cambiamento)."""
    return core.labels.add_tag(entity_type, entity_id, body.tag)


@router.delete("/api/tags/{entity_type}/{entity_id}/{tag}")
def remove_tag(entity_type: EntityType, entity_id: str, tag: str, core: Core = Depends(get_core)) -> dict:
    """Toglie un'etichetta (idempotente)."""
    return core.labels.remove_tag(entity_type, entity_id, tag)


@router.get("/api/favorites")
def list_favorites(entity_type: Optional[EntityType] = None, core: Core = Depends(get_core)) -> list[dict]:
    return core.labels.favorites(entity_type)


@router.put("/api/favorites/{entity_type}/{entity_id}")
def add_favorite(entity_type: EntityType, entity_id: str, core: Core = Depends(get_core)) -> dict:
    return core.labels.set_favorite(entity_type, entity_id, True)


@router.delete("/api/favorites/{entity_type}/{entity_id}")
def remove_favorite(entity_type: EntityType, entity_id: str, core: Core = Depends(get_core)) -> dict:
    return core.labels.set_favorite(entity_type, entity_id, False)


@router.get("/api/search")
def global_search(q: str = Query("", max_length=textsearch.MAX_QUERY_LENGTH,
                                 description="Testo: ogni parola è l'inizio di una parola dei campi"),
                  tag: Optional[str] = Query(None, max_length=200, description="Solo elementi con questa etichetta"),
                  type: Optional[list[GroupType]] = Query(None, description="Gruppi da cercare (ripetibile)"),
                  limit: int = Query(globalsearch.DEFAULT_LIMIT, ge=1, le=globalsearch.MAX_LIMIT,
                                     description="Risultati per gruppo"),
                  core: Core = Depends(get_core)) -> dict:
    """Ricerca in progetti, sequenze, documenti, job, template ed etichette, con risultati raggruppati e ordinati
    per pertinenza. Senza testo: gli elementi con l'etichetta `tag`, oppure i preferiti."""
    return core.search.search(q, tag=tag or None, types=type, limit=limit)
