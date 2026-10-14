"""Lettura di sequenze e allineamenti da file, in qualunque formato comune.

Usato da tutti i campi dell'interfaccia che accettano sequenze o allineamenti "da file". Formati:
FASTA (anche allineato o A3M), PIR/NBRF, Clustal, Stockholm, MSF, GenBank, EMBL, UniProt/SwissProt,
PDB e mmCIF (una sequenza per catena), testo semplice. File compressi con gzip, codifiche UTF-8,
UTF-16 (con BOM) e Windows-1252, fine riga Windows/Mac/Unix.
"""

from __future__ import annotations

import gzip
import io
import os
import re
import tempfile
from pathlib import Path
from typing import Optional

from ..core.errors import AppError
from . import sequences

# Un file di sequenze per una pipeline da server: 100.000 sequenze di qualche centinaio di residui sono ~50 MB in FASTA
# e più del doppio in GenBank/EMBL; oltre, il file si comprime (.gz) fino a MAX_UNCOMPRESSED.
MAX_BYTES = 200 * 1024 * 1024
MAX_UNCOMPRESSED = 1024 * 1024 * 1024
# Sequenze lette da un file: quante ne accetta una pipeline (services/pipeline.MAX_SEQUENCES)
MAX_RECORDS = 100_000

FORMAT_LABELS = {
    "fasta": "FASTA", "pir": "PIR (MODELLER)", "clustal": "Clustal", "stockholm": "Stockholm", "msf": "MSF",
    "genbank": "GenBank", "embl": "EMBL", "swiss": "UniProt/SwissProt", "pdb": "PDB", "mmcif": "mmCIF",
    "raw": "testo semplice",
}
EXTENSION_HINTS = {
    ".aln": "clustal", ".clustal": "clustal", ".clw": "clustal", ".sto": "stockholm", ".stk": "stockholm",
    ".msf": "msf", ".gb": "genbank", ".gbk": "genbank", ".genbank": "genbank", ".embl": "embl",
    ".pdb": "pdb", ".ent": "pdb", ".cif": "mmcif", ".mmcif": "mmcif", ".pir": "pir", ".ali": "pir",
}


def decode(data: bytes) -> str:
    """Byte del file → testo: gzip, BOM UTF-8/UTF-16, UTF-8, altrimenti Windows-1252."""
    if data[:2] == b"\x1f\x8b":
        try:
            with gzip.GzipFile(fileobj=io.BytesIO(data)) as gz:
                data = gz.read(MAX_UNCOMPRESSED + 1)
        except OSError as exc:
            raise AppError(f"File compresso non leggibile: {exc}", code="invalid_file") from exc
        if len(data) > MAX_UNCOMPRESSED:
            raise AppError("File troppo grande una volta decompresso.", code="invalid_file", status=413)
    if data.startswith(b"\xef\xbb\xbf"):
        text = data[3:].decode("utf-8", errors="replace")
    elif data.startswith((b"\xff\xfe", b"\xfe\xff")):
        text = data.decode("utf-16", errors="replace")
    else:
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            text = data.decode("cp1252", errors="replace")
    if "\x00" in text:
        raise AppError("Il file sembra binario (non è un file di testo con sequenze).", code="invalid_file")
    return text.replace("\r\n", "\n").replace("\r", "\n")


def detect_format(text: str, filename: str = "") -> str:
    lines = [ln for ln in text.split("\n") if ln.strip()]
    if not lines:
        raise AppError("Il file è vuoto.", code="invalid_file")
    first = lines[0].lstrip()
    upper = first.upper()
    if re.search(r"^>(P1|F1|DL|DC|RL|RC|N1|N3|XX);", text, re.MULTILINE):
        return "pir"
    if first.startswith(">"):
        return "fasta"
    if upper.startswith(("CLUSTAL", "MUSCLE (", "PROBCONS", "MAFFT")):
        return "clustal"
    if upper.startswith("# STOCKHOLM"):
        return "stockholm"
    if upper.startswith(("!!AA_MULTIPLE_ALIGNMENT", "!!NA_MULTIPLE_ALIGNMENT", "PILEUP")) or \
            (re.search(r"\bMSF:\s*\d+", text[:5000]) and "//" in text):
        return "msf"
    if upper.startswith("LOCUS"):
        return "genbank"
    if upper.startswith("ID ") and "\nSQ " in text:
        return "swiss" if re.search(r"^SQ\s+SEQUENCE\s", text, re.MULTILINE) else "embl"
    if first.startswith("data_") and "_atom_site." in text:
        return "mmcif"
    if re.search(r"^(ATOM  |HETATM)", text, re.MULTILINE) and \
            re.match(r"^(HEADER|ATOM|HETATM|REMARK|CRYST1|MODEL|TITLE|COMPND|SEQRES|EXPDTA)", first):
        return "pdb"
    hint = EXTENSION_HINTS.get(Path(filename).suffix.lower())
    return hint if hint in ("clustal", "stockholm", "msf") else "raw"


def _fasta(text: str) -> list[sequences.SeqRecord]:
    """FASTA conservando i gap ('-', '.') e il maiuscolo/minuscolo degli A3M ridotto a maiuscolo."""
    records, name, desc, chunks = [], None, "", []

    def flush():
        if name is not None:
            records.append(sequences.SeqRecord(name, sequences.clean_sequence("".join(chunks), keep_gaps=True), desc))

    for raw in text.split("\n"):
        line = raw.strip()
        if not line or line.startswith(";") or line.startswith("#"):
            continue
        if line.startswith(">"):
            flush()
            parts = line[1:].strip().split(None, 1)
            name = parts[0] if parts else f"seq{len(records) + 1}"
            desc = parts[1] if len(parts) > 1 else ""
            chunks = []
        else:
            chunks.append(line)
    flush()
    return records


def _biopython_alignment(text: str, fmt: str) -> list[sequences.SeqRecord]:
    from Bio import AlignIO

    try:
        alignments = list(AlignIO.parse(io.StringIO(text), fmt))
    except Exception as exc:  # Biopython solleva ValueError e altri errori di formato
        raise AppError(f"File {FORMAT_LABELS[fmt]} non valido: {exc}", code="invalid_file") from exc
    return [sequences.SeqRecord(r.id, str(r.seq).upper(), r.description if r.description != r.id else "")
            for aln in alignments for r in aln]


def _biopython_records(text: str, fmt: str, warnings: list[str]) -> list[sequences.SeqRecord]:
    from Bio import SeqIO

    try:
        parsed = list(SeqIO.parse(io.StringIO(text), fmt))
    except Exception as exc:
        raise AppError(f"File {FORMAT_LABELS[fmt]} non valido: {exc}", code="invalid_file") from exc
    out = []
    for rec in parsed:
        seq = str(rec.seq).upper()
        is_nucleic = seq and sequences.detect_kind(seq) == "nucleic"
        translations = []
        if is_nucleic:
            for feat in rec.features:
                if feat.type == "CDS" and feat.qualifiers.get("translation"):
                    label = (feat.qualifiers.get("protein_id") or feat.qualifiers.get("gene")
                             or feat.qualifiers.get("locus_tag") or [f"{rec.name}_cds{len(translations) + 1}"])[0]
                    product = (feat.qualifiers.get("product") or [""])[0]
                    translations.append(sequences.SeqRecord(label, feat.qualifiers["translation"][0].upper(), product))
        if translations:
            warnings.append(f"{rec.name}: record nucleotidico, usate le {len(translations)} traduzioni proteiche "
                            "delle CDS annotate.")
            out.extend(translations)
        else:
            out.append(sequences.SeqRecord(rec.name or rec.id, seq, rec.description or ""))
    return out


def _structure_chains(data: bytes, filename: str, fmt: str) -> list[sequences.SeqRecord]:
    from . import structure

    suffix = ".cif" if fmt == "mmcif" else ".pdb"
    stem = re.sub(r"[^A-Za-z0-9_-]", "_", Path(filename).stem)[:30] or "struttura"
    fd, tmp = tempfile.mkstemp(suffix=suffix)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data if not data[:2] == b"\x1f\x8b" else gzip.decompress(data))
        model = structure.first_model(structure.load_structure(Path(tmp), stem))
        return [sequences.SeqRecord(f"{stem}_{c['chain']}", c["sequence"],
                                    f"catena {c['chain']}, residui {c['first']}–{c['last']}")
                for c in structure.chain_summaries(model)]
    finally:
        os.remove(tmp)


def _raw(text: str, filename: str) -> list[sequences.SeqRecord]:
    """Testo semplice: una sequenza per blocco (separati da righe vuote); numeri e spazi ignorati."""
    stem = re.sub(r"[^A-Za-z0-9_-]", "_", Path(filename).stem)[:40] or "sequenza"
    blocks = [b for b in re.split(r"\n\s*\n", text) if b.strip()]
    records = []
    for block in blocks:
        seq = sequences.clean_sequence(re.sub(r"(?i)^\s*(origin|sequence)\b.*$", "", block, flags=re.MULTILINE))
        if seq:
            records.append(sequences.SeqRecord(stem if len(blocks) == 1 else f"{stem}_{len(records) + 1}", seq))
    return records


def parse_records(data: bytes, filename: str = "") -> dict:
    """Sequenze (o righe di un allineamento) contenute nel file, con formato riconosciuto e avvisi."""
    if len(data) > MAX_BYTES:
        raise AppError(f"File troppo grande: massimo {MAX_BYTES // (1024 * 1024)} MB.", code="invalid_file",
                       status=413)
    text = decode(data)
    fmt = detect_format(text, filename)
    warnings: list[str] = []
    if fmt == "pir":
        entries = sequences.parse_pir(text)
        records = [sequences.SeqRecord(e.code, e.sequence, e.header_line()) for e in entries]
    elif fmt == "fasta":
        records = _fasta(text)
    elif fmt in ("clustal", "stockholm", "msf"):
        records = _biopython_alignment(text, fmt)
    elif fmt in ("genbank", "embl", "swiss"):
        records = _biopython_records(text, fmt, warnings)
    elif fmt in ("pdb", "mmcif"):
        records = _structure_chains(data, filename, fmt)
    else:
        records = _raw(text, filename)
    empty = [r for r in records if not r.sequence.replace("-", "").replace(".", "").replace("/", "")]
    records = [r for r in records if r not in empty]
    if empty:
        warnings.append(f"{len(empty)} record senza sequenza ignorati.")
    if not records:
        raise AppError(f"Nessuna sequenza trovata nel file ({FORMAT_LABELS[fmt]}).", code="invalid_file")
    if len(records) > MAX_RECORDS:
        warnings.append(f"Il file contiene {len(records)} sequenze: lette le prime {MAX_RECORDS}.")
        records = records[:MAX_RECORDS]
    aligned = fmt in ("clustal", "stockholm", "msf") or any(set("-.") & set(r.sequence) for r in records)
    out = []
    unusual = 0
    for r in records:
        ungapped = re.sub(r"[-./]", "", r.sequence)
        if sequences.invalid_positions(ungapped, "protein") and sequences.detect_kind(ungapped) != "nucleic":
            unusual += 1
        out.append({"name": r.name, "description": r.description, "sequence": ungapped,
                    "aligned": r.sequence if aligned else None, "length": len(ungapped)})
    if unusual:
        warnings.append(f"{unusual} sequenze contengono caratteri non amminoacidici: verranno segnalati all'uso.")
    return {"format": fmt, "format_label": FORMAT_LABELS[fmt], "aligned": aligned, "records": out,
            "warnings": warnings, "filename": Path(filename).name}


def looks_like_pir(text: Optional[str]) -> bool:
    return bool(text) and bool(re.search(r"^>P1;", text, re.MULTILINE))
