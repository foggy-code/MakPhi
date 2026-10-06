"""Strumenti su sequenze e banche dati esterne (UniProt, InterPro, RCSB)."""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field

from ..core.container import Core
from ..core.errors import AppError
from ..domain import disorder, pir_secondary, secondary, sequences
from ..engines import remote
from ..services.coverage import MAX_RANGES, MAX_TEMPLATES, CoverageService
from .deps import get_core

router = APIRouter(prefix="/api", tags=["Sequenze e banche dati"])

# Testo massimo accettato per una sequenza o un PIR (spazi e intestazioni compresi): la lunghezza vera si controlla
# sui residui (disorder.MAX_LENGTH); questo limite ferma solo i corpi enormi prima di ogni elaborazione.
MAX_TEXT = 200_000


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


class DisorderIn(BaseModel):
    sequence: str = Field(..., max_length=MAX_TEXT)


@router.post("/bio/disorder")
def predict_disorder(body: DisorderIn) -> dict:
    """Disordine per residuo (FoldIndex), regioni disordinate lunghe e regioni a bassa complessità (SEG) dalla sola
    sequenza proteica (al massimo 5000 residui). Nessun servizio remoto."""
    return disorder.predict(body.sequence)


class TemplateIn(BaseModel):
    id: str = Field(..., max_length=80)
    chains: list[str] = Field(default_factory=list, max_length=26)


class RangeIn(BaseModel):
    label: str = Field("", max_length=80)
    start: int
    end: int


class CoverageIn(BaseModel):
    sequence: str = Field("", max_length=MAX_TEXT)
    accession: Optional[str] = Field(None, max_length=20)
    templates: list[TemplateIn] = Field(default_factory=list, max_length=MAX_TEMPLATES)
    alignment: Optional[str] = Field(None, max_length=2_000_000)
    ranges: list[RangeIn] = Field(default_factory=list, max_length=MAX_RANGES)
    plddt_min: Optional[float] = Field(None, ge=0, le=100)


@router.post("/bio/coverage")
def coverage_check(body: CoverageIn, core: Core = Depends(get_core)) -> dict:
    """Avvisi prima di modellare: regioni disordinate lunghe fuori dalla copertura dei template, domini (InterPro, solo
    con l'accessione UniProt) non coperti o coperti in parte, con un suggerimento. La copertura viene dai template della
    libreria (`templates`, insieme), da un PIR (`alignment`) o da intervalli del bersaglio (`ranges`, uno per uno)."""
    return CoverageService(core.library).check(
        sequence=body.sequence, accession=body.accession, templates=[t.model_dump() for t in body.templates],
        alignment=body.alignment, ranges=[r.model_dump() for r in body.ranges], plddt_min=body.plddt_min)


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


class PirSecondaryIn(BaseModel):
    content: str = Field(..., max_length=MAX_TEXT)
    templates: list[TemplateIn] = Field(default_factory=list, max_length=MAX_TEMPLATES)


@router.post("/pir/secondary")
def pir_secondary_structure(body: PirSecondaryIn, core: Core = Depends(get_core)) -> dict:
    """Struttura secondaria per residuo di ogni voce del PIR: DSSP sul file dei template della libreria (abbinati ai
    template del documento per codice o posizione, o dal codice stesso), predizione GOR per il bersaglio. Un carattere
    per lettera della voce: l'editor lo riporta sulle colonne, qualunque sia la posizione dei gap."""

    def resolve(template_id: str):
        try:
            return core.library.path(template_id)
        except AppError:
            return None

    return pir_secondary.annotate(body.content, [t.model_dump() for t in body.templates], resolve)


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
