"""Database locali: stato, aggiornamento di pdbaa, indice MMseqs2, UniClust30 per QMEAN."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from ..core.container import Core
from .deps import get_core

router = APIRouter(prefix="/api/databases", tags=["Database"])


@router.get("")
def databases_status(core: Core = Depends(get_core)) -> dict:
    return core.databases.status()


@router.post("/pdbaa/update")
def update_pdbaa(core: Core = Depends(get_core)) -> dict:
    return core.jobs.submit("db_update_pdbaa", {}, title="Aggiornamento del database pdbaa (NCBI)")


@router.post("/mmseqs/build")
def build_mmseqs(index: bool = True, core: Core = Depends(get_core)) -> dict:
    """Database MMseqs2 da pdbaa (nella cartella dati) e, se richiesto, indice veloce su questo computer."""
    return core.jobs.submit("db_build_mmseqs", {"index": index}, title="Database MMseqs2 da pdbaa")


class ConvertIn(BaseModel):
    database: str = Field(..., min_length=1, max_length=300)


@router.post("/uniclust/convert")
def convert_uniclust(body: ConvertIn, core: Core = Depends(get_core)) -> dict:
    """Converte per HH-suite 3 un database UniClust30 in formato HH-suite 2 (ricrea il cs219, una volta sola)."""
    return core.jobs.submit("db_convert_uniclust", {"database": body.database},
                            title=f"Conversione di {body.database.rsplit('/', 1)[-1]} per HH-suite 3")


class LocalCopyIn(BaseModel):
    database: str = Field(..., min_length=1, max_length=300)
    action: Literal["copy", "remove"] = "copy"


@router.post("/uniclust/local")
def local_uniclust(body: LocalCopyIn, core: Core = Depends(get_core)) -> dict:
    """Copia su questo computer indici e cs219 di un database UniClust30 (HHblits molto più veloce con
    Docker Desktop), oppure elimina la copia."""
    name = body.database.rsplit("/", 1)[-1]
    title = (f"Copia locale degli indici di {name}" if body.action == "copy"
             else f"Rimozione della copia locale di {name}")
    return core.jobs.submit("db_local_uniclust", {"database": body.database, "action": body.action}, title=title)


@router.post("/mmseqs/index")
def index_mmseqs(core: Core = Depends(get_core)) -> dict:
    """Solo l'indice veloce locale (es. dopo aver portato la chiavetta su un altro computer)."""
    return core.jobs.submit("db_index_mmseqs", {}, title="Indice veloce MMseqs2 su questo computer")
