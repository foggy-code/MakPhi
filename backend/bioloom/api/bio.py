"""Strumenti su sequenze e banche dati esterne (UniProt, InterPro, RCSB)."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel

from ..core.container import Core
from ..core.errors import AppError
from ..domain import secondary, sequences
from ..engines import remote
from .deps import get_core

router = APIRouter(prefix="/api", tags=["Sequenze e banche dati"])


class SequenceText(BaseModel):
    sequence: str
    kind: str = "protein"


class ConvertIn(BaseModel):
    ids: list[str]
    aligned: list[str]
    format: str = "fasta"


@router.post("/sequences/analyze")
def analyze_sequence(body: SequenceText) -> dict:
    """Validazione, proprietà fisico-chimiche e predizione euristica della struttura secondaria."""
    seq, kind = sequences.validate_sequence(body.sequence, body.kind if body.kind in ("protein", "nucleic", "auto")
                                            else "protein")
    out = {"sequence": seq, "kind": kind, "length": len(seq)}
    if kind == "protein":
        out["properties"] = sequences.sequence_properties(seq)
        out["secondary_prediction"] = secondary.predict_gor(seq)
    return out


@router.post("/sequences/parse-fasta")
def parse_fasta(body: SequenceText) -> list[dict]:
    return [r.to_dict() for r in sequences.parse_fasta(body.sequence)]


@router.post("/alignments/convert", response_class=PlainTextResponse)
def convert_alignment(body: ConvertIn) -> str:
    if len(body.ids) != len(body.aligned) or not body.ids:
        raise AppError("Identificativi e sequenze allineate non corrispondono.")
    if body.format == "fasta":
        return sequences.format_fasta(body.ids, body.aligned)
    if body.format == "clustal":
        return sequences.format_clustal(body.ids, body.aligned)
    if body.format == "pir":
        entries = [sequences.target_entry(sequences.sanitize_code(i, f"seq{n}"), s.replace(".", "-"))
                   for n, (i, s) in enumerate(zip(body.ids, body.aligned))]
        return sequences.format_pir(entries)
    raise AppError(f"Formato non supportato: {body.format}")


class PirIn(BaseModel):
    content: str


@router.post("/pir/validate")
def validate_pir(body: PirIn) -> dict:
    return sequences.validate_pir(body.content)


@router.get("/uniprot/search")
def uniprot_search(q: str) -> list[dict]:
    return remote.uniprot_search(q)


@router.get("/uniprot/{accession}")
def uniprot_entry(accession: str) -> dict:
    return remote.uniprot_entry(accession)


@router.get("/uniprot/{accession}/domains")
def uniprot_domains(accession: str) -> list[dict]:
    return remote.interpro_domains(accession)


class IdsIn(BaseModel):
    ids: list[str]


@router.post("/rcsb/metadata")
def rcsb_metadata(body: IdsIn) -> dict:
    if len(body.ids) > 200:
        raise AppError("Massimo 200 codici PDB per richiesta.")
    return remote.rcsb_metadata(body.ids)
