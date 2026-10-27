"""Caricamento da file: sequenze/allineamenti in qualunque formato comune e strutture PDB/mmCIF."""

from __future__ import annotations

import uuid
from pathlib import Path

import anyio.to_thread
from fastapi import APIRouter, Depends, File, UploadFile
from fastapi.responses import FileResponse

from ..core.container import Core
from ..core.errors import AppError, NotFound
from ..core.storage import atomic_write_bytes, safe_join, validate_identifier
from ..domain import fileformats, structure
from ..services.templates import EXTENSIONS
from .deps import get_core, read_upload

router = APIRouter(prefix="/api/files", tags=["Caricamento da file"])


@router.post("/sequences")
async def parse_sequence_file(file: UploadFile = File(...)) -> dict:
    """Legge sequenze o allineamenti (FASTA, PIR, Clustal, Stockholm, MSF, GenBank, EMBL, UniProt, PDB, mmCIF,
    testo semplice; anche compressi con gzip). Non salva nulla: restituisce i record riconosciuti."""
    data = await read_upload(file, limit=fileformats.MAX_BYTES)
    # decompressione e riconoscimento del formato in un thread: il loop resta libero per le altre richieste
    return await anyio.to_thread.run_sync(fileformats.parse_records, data, file.filename or "")


def store_structure(core: Core, filename: str, data: bytes) -> dict:
    """Salva una struttura tra i file caricati (dopo averla validata) e la restituisce come sorgente."""
    suffix = Path(filename or "").suffix.lower()
    if suffix not in EXTENSIONS:
        raise AppError("Formato non supportato: carica un file .pdb, .ent, .cif o .mmcif.", code="invalid_file")
    stem = "".join(c if c.isalnum() or c in "_-" else "_" for c in Path(filename).stem)[:40] or "struttura"
    name = f"{uuid.uuid4().hex[:8]}_{stem}{'.cif' if EXTENSIONS[suffix] == 'cif' else '.pdb'}"
    dest = core.paths.uploads / name
    atomic_write_bytes(dest, data)
    try:
        model = structure.first_model(structure.load_structure(dest, stem))
    except AppError:
        dest.unlink(missing_ok=True)
        raise
    chains = structure.chain_summaries(model)
    if not chains:
        dest.unlink(missing_ok=True)
        raise AppError("La struttura non contiene catene proteiche.", code="invalid_file")
    return {"source": {"type": "upload", "file": name}, "label": stem, "filename": Path(filename).name,
            "chains": [{"chain": c["chain"], "residues": c["residues"], "first": c["first"], "last": c["last"]}
                       for c in chains]}


@router.post("/structure")
async def upload_structure(file: UploadFile = File(...), core: Core = Depends(get_core)) -> dict:
    """Struttura caricata da file, utilizzabile ovunque serva una struttura (allineamento strutturale,
    raffinamento dei loop, qualità, QMEAN). Conservata secondo la pulizia automatica degli upload."""
    data = await read_upload(file)
    return await anyio.to_thread.run_sync(store_structure, core, file.filename or "", data)


@router.get("/uploads/{name}")
def uploaded_file(name: str, core: Core = Depends(get_core)) -> FileResponse:
    """Struttura caricata (per il visualizzatore 3D)."""
    path = safe_join(core.paths.uploads, validate_identifier(name, "file"))
    if not path.is_file():
        raise NotFound("File caricato non trovato (forse eliminato dalla pulizia automatica).")
    return FileResponse(path, media_type="chemical/x-pdb")
