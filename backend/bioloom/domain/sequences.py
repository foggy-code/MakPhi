"""Sequenze: parsing FASTA, validazione IUPAC, statistiche di allineamento e formati di output."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable, Optional

from ..core.errors import AppError

PROTEIN_STANDARD = set("ACDEFGHIKLMNPQRSTVWY")
PROTEIN_EXTENDED = PROTEIN_STANDARD | set("BZXJUO")
NUCLEIC = set("ACGTURYKMSWBDHVN")
GAPS = set("-.")

# Gruppi di conservazione usati da Clustal per la riga di consenso.
STRONG_GROUPS = ["STA", "NEQK", "NHQK", "NDEQ", "QHRK", "MILV", "MILF", "HY", "FYW"]
WEAK_GROUPS = ["CSA", "ATV", "SAG", "STNK", "STPA", "SGND", "SNDEQK", "NDEQHK", "NEQHRK", "FVLIM", "HFY"]


@dataclass
class SeqRecord:
    name: str
    sequence: str
    description: str = ""

    def to_dict(self) -> dict:
        return {"name": self.name, "sequence": self.sequence, "description": self.description,
                "length": len(self.sequence)}


def clean_sequence(text: str, keep_gaps: bool = False) -> str:
    """Rimuove spazi, numeri, asterischi finali; restituisce maiuscolo."""
    allowed_extra = "-." if keep_gaps else ""
    out = re.sub(r"[\s\d*]", "", text or "").upper()
    return "".join(ch for ch in out if ch.isalpha() or ch in allowed_extra)


def parse_fasta(text: str, default_name: str = "seq") -> list[SeqRecord]:
    """Parsa FASTA (anche multi-record). Una sequenza senza intestazione riceve un nome di default."""
    records: list[SeqRecord] = []
    name, desc, chunks = None, "", []
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line or line.startswith(";"):
            continue
        if line.startswith(">"):
            if name is not None or chunks:
                records.append(SeqRecord(name or f"{default_name}{len(records) + 1}", clean_sequence("".join(chunks)), desc))
            header = line[1:].strip()
            parts = header.split(None, 1)
            name = parts[0] if parts else f"{default_name}{len(records) + 1}"
            desc = parts[1] if len(parts) > 1 else ""
            chunks = []
        else:
            chunks.append(line)
    if name is not None or chunks:
        records.append(SeqRecord(name or f"{default_name}{len(records) + 1}", clean_sequence("".join(chunks)), desc))
    return [r for r in records if r.sequence]


def detect_kind(sequence: str) -> str:
    letters = [c for c in sequence.upper() if c.isalpha()]
    if not letters:
        return "protein"
    nucleic_core = sum(1 for c in letters if c in "ACGTUN")
    return "nucleic" if nucleic_core / len(letters) >= 0.9 else "protein"


def invalid_positions(sequence: str, kind: str = "protein", allow_gaps: bool = False) -> list[dict]:
    alphabet = PROTEIN_EXTENDED if kind == "protein" else NUCLEIC
    if allow_gaps:
        alphabet = alphabet | GAPS
    return [{"position": i + 1, "char": c} for i, c in enumerate(sequence.upper()) if c not in alphabet]


def validate_sequence(sequence: str, kind: str = "auto", name: str = "sequenza") -> tuple[str, str]:
    """Restituisce (sequenza pulita, tipo). Solleva AppError con le posizioni non valide."""
    seq = clean_sequence(sequence)
    if not seq:
        raise AppError(f"La {name} è vuota.", code="empty_sequence")
    resolved = detect_kind(seq) if kind == "auto" else kind
    bad = invalid_positions(seq, resolved)
    if bad:
        chars = "".join(sorted({b["char"] for b in bad}))
        raise AppError(f"La {name} contiene caratteri non validi per {resolved}: {chars}",
                       code="invalid_sequence", details={"invalid": bad[:50], "kind": resolved})
    return seq, resolved


def sequence_properties(sequence: str) -> dict:
    """Proprietà fisico-chimiche (ProtParam) di una sequenza proteica."""
    from Bio.SeqUtils.ProtParam import ProteinAnalysis

    seq = "".join(c for c in clean_sequence(sequence) if c in PROTEIN_STANDARD)
    if not seq:
        return {}
    analysis = ProteinAnalysis(seq)
    helix, turn, sheet = analysis.secondary_structure_fraction()
    counts = analysis.count_amino_acids()
    return {
        "length": len(sequence),
        "molecular_weight": round(analysis.molecular_weight(), 1),
        "isoelectric_point": round(analysis.isoelectric_point(), 2),
        "instability_index": round(analysis.instability_index(), 2),
        "gravy": round(analysis.gravy(), 3),
        "aromaticity": round(analysis.aromaticity(), 3),
        "extinction_coefficient": analysis.molar_extinction_coefficient()[0],
        "propensity": {"helix": round(helix, 3), "turn": round(turn, 3), "sheet": round(sheet, 3)},
        "composition": {aa: counts.get(aa, 0) for aa in sorted(PROTEIN_STANDARD)},
    }


def sanitize_code(value: Optional[str], default: str = "TARGET", max_len: int = 40) -> str:
    """Identificatore sicuro per MODELLER/PIR: primo token, separatori ':|.' -> '_', solo [A-Za-z0-9_-]."""
    if not value:
        return default
    text = re.sub(r"^(?:>P1;|>)+", "", str(value).strip())
    first = next((ln.strip() for ln in text.splitlines() if ln.strip()), "")
    token = first.split()[0] if first.split() else ""
    token = re.sub(r"[:|.]", "_", token)
    token = re.sub(r"[^A-Za-z0-9_-]", "", token)
    token = re.sub(r"_+", "_", token).strip("_")
    return token[:max_len] if token else default


# --------------------------------------------------------------------------- statistiche allineamento
def alignment_stats(a: str, b: str) -> dict:
    """Identità, similarità (BLOSUM62 > 0), gap e copertura tra due sequenze allineate."""
    from Bio.Align import substitution_matrices

    if len(a) != len(b):
        raise AppError("Le sequenze allineate hanno lunghezze diverse.")
    blosum = substitution_matrices.load("BLOSUM62")
    matches = similar = gaps = cols = 0
    for x, y in zip(a.upper(), b.upper()):
        if x in GAPS and y in GAPS:
            continue
        cols += 1
        if x in GAPS or y in GAPS:
            gaps += 1
            continue
        if x == y:
            matches += 1
            similar += 1
        else:
            try:
                if blosum[x][y] > 0:
                    similar += 1
            except (KeyError, IndexError):
                pass
    aligned_pairs = cols - gaps
    len_a = sum(1 for c in a if c not in GAPS)
    len_b = sum(1 for c in b if c not in GAPS)
    return {
        "length": cols,
        "identities": matches,
        "identity": round(100.0 * matches / aligned_pairs, 2) if aligned_pairs else 0.0,
        "identity_over_shorter": round(100.0 * matches / min(len_a, len_b), 2) if min(len_a, len_b) else 0.0,
        "similarity": round(100.0 * similar / aligned_pairs, 2) if aligned_pairs else 0.0,
        "gaps": gaps,
        "gap_percent": round(100.0 * gaps / cols, 2) if cols else 0.0,
        "coverage_a": round(100.0 * aligned_pairs / len_a, 2) if len_a else 0.0,
        "coverage_b": round(100.0 * aligned_pairs / len_b, 2) if len_b else 0.0,
    }


def identity_matrix(ids: list[str], aligned: list[str]) -> dict:
    n = len(aligned)
    values = [[100.0 if i == j else 0.0 for j in range(n)] for i in range(n)]
    for i in range(n):
        for j in range(i + 1, n):
            pairs = [(x, y) for x, y in zip(aligned[i], aligned[j]) if x not in GAPS and y not in GAPS]
            ident = round(100.0 * sum(1 for x, y in pairs if x == y) / len(pairs), 1) if pairs else 0.0
            values[i][j] = values[j][i] = ident
    return {"ids": ids, "values": values}


def consensus_line(aligned: list[str]) -> str:
    """Simboli Clustal: '*' identico, ':' gruppo forte, '.' gruppo debole, ' ' altrimenti."""
    if not aligned:
        return ""
    out = []
    for col in range(len(aligned[0])):
        chars = [s[col].upper() for s in aligned if col < len(s)]
        if not chars or any(c in GAPS for c in chars):
            out.append(" ")
        elif len(set(chars)) == 1:
            out.append("*")
        elif any(all(c in grp for c in chars) for grp in STRONG_GROUPS):
            out.append(":")
        elif any(all(c in grp for c in chars) for grp in WEAK_GROUPS):
            out.append(".")
        else:
            out.append(" ")
    return "".join(out)


# --------------------------------------------------------------------------- formati di output
def format_fasta(ids: Iterable[str], seqs: Iterable[str], width: int = 60) -> str:
    lines = []
    for sid, seq in zip(ids, seqs):
        lines.append(f">{sid}")
        lines.extend(seq[i:i + width] for i in range(0, len(seq), width))
    return "\n".join(lines) + "\n"


def format_clustal(ids: list[str], seqs: list[str], width: int = 60) -> str:
    name_w = max(16, max((len(i) for i in ids), default=0) + 2)
    lines = ["CLUSTAL W (1.83) multiple sequence alignment (BioLoom v4)", "", ""]
    counts = [0] * len(ids)
    cons = consensus_line(seqs)
    total = len(seqs[0]) if seqs else 0
    for start in range(0, total, width):
        for k, (sid, seq) in enumerate(zip(ids, seqs)):
            chunk = seq[start:start + width]
            counts[k] += sum(1 for c in chunk if c not in GAPS)
            lines.append(f"{sid:<{name_w}}{chunk} {counts[k]}")
        lines.append(" " * name_w + cons[start:start + width])
        lines.append("")
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------- PIR (MODELLER)
@dataclass
class PirEntry:
    code: str
    kind: str  # "sequence" | "structureX" | "structureN" | "structure"
    fields: list[str] = field(default_factory=list)  # campi della riga descrittiva dopo il tipo
    sequence: str = ""  # con gap '-', separatori di catena '/', ligandi '.'; senza '*'

    @property
    def is_template(self) -> bool:
        return self.kind.startswith("structure")

    def header_line(self) -> str:
        return ":".join([self.kind, self.code, *self.fields])

    def chains(self) -> list[str]:
        return self.sequence.split("/")


def parse_pir(text: str) -> list[PirEntry]:
    entries: list[PirEntry] = []
    lines = [ln.rstrip() for ln in (text or "").splitlines()]
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        if not line.startswith(">P1;"):
            i += 1
            continue
        code = line[4:].strip()
        i += 1
        while i < len(lines) and not lines[i].strip():
            i += 1
        header = lines[i].strip() if i < len(lines) else ""
        parts = header.split(":")
        kind = parts[0].strip() if parts else "sequence"
        fields = [p.strip() for p in parts[2:]] if len(parts) > 2 else []
        i += 1
        seq_parts = []
        while i < len(lines) and not lines[i].strip().startswith(">P1;"):
            seq_parts.append(lines[i].strip())
            i += 1
        seq = re.sub(r"\s", "", "".join(seq_parts))
        seq = seq.split("*", 1)[0].upper()
        entries.append(PirEntry(code=code, kind=kind, fields=fields, sequence=seq))
    return entries


def format_pir(entries: list[PirEntry], width: int = 75) -> str:
    blocks = []
    for e in entries:
        seq = e.sequence + "*"
        body = "\n".join(seq[i:i + width] for i in range(0, len(seq), width))
        blocks.append(f">P1;{e.code}\n{e.header_line()}\n{body}")
    return "\n\n".join(blocks) + "\n"


def target_entry(code: str, sequence: str) -> PirEntry:
    return PirEntry(code=code, kind="sequence", fields=["", "", "", "", "", "", "0.00", "0.00"], sequence=sequence)


def template_entry(code: str, sequence: str, first: str, chain_first: str, last: str, chain_last: str) -> PirEntry:
    return PirEntry(code=code, kind="structureX",
                    fields=[first, chain_first, last, chain_last, "", "", "", ""], sequence=sequence)


def validate_pir(text: str) -> dict:
    """Controlli di coerenza per un allineamento destinato a MODELLER."""
    errors: list[str] = []
    warnings: list[str] = []
    entries = parse_pir(text)
    if not entries:
        return {"valid": False, "errors": ["Nessun blocco '>P1;' trovato."], "warnings": [], "entries": []}
    targets = [e for e in entries if e.kind == "sequence"]
    templates = [e for e in entries if e.is_template]
    if len(targets) != 1:
        errors.append(f"Serve esattamente una sequenza bersaglio ('sequence:'), trovate {len(targets)}.")
    if not templates:
        errors.append("Nessun template ('structureX:') nell'allineamento.")
    codes = [e.code for e in entries]
    if len(set(codes)) != len(codes):
        errors.append("Codici duplicati nell'allineamento.")
    allowed = PROTEIN_EXTENDED | GAPS | {"/"}
    for e in entries:
        if not e.code or sanitize_code(e.code, default="") != e.code:
            errors.append(f"Codice '{e.code}' non valido: usa solo lettere, numeri, '_' e '-'.")
        bad = sorted({c for c in e.sequence if c not in allowed})
        if bad:
            errors.append(f"{e.code}: caratteri non ammessi {''.join(bad)}.")
        if not any(c.isalpha() for c in e.sequence):
            errors.append(f"{e.code}: sequenza vuota.")
    lengths = {e.code: len(e.sequence) for e in entries}
    if len(set(lengths.values())) > 1:
        errors.append("Le sequenze hanno lunghezze allineate diverse: " +
                      ", ".join(f"{k}={v}" for k, v in lengths.items()))
    chain_counts = {e.code: len(e.chains()) for e in entries}
    if len(set(chain_counts.values())) > 1:
        errors.append("Numero di catene ('/') diverso tra le voci: " +
                      ", ".join(f"{k}={v}" for k, v in chain_counts.items()))
    if targets and templates and not errors:
        tgt = targets[0].sequence
        for t in templates:
            pairs = [(x, y) for x, y in zip(tgt, t.sequence) if x.isalpha() and y.isalpha()]
            ident = 100.0 * sum(1 for x, y in pairs if x == y) / len(pairs) if pairs else 0.0
            if ident < 25:
                warnings.append(f"Identità con {t.code} del {ident:.1f}%: zona crepuscolare (< 25%), "
                                f"il modello potrebbe essere poco affidabile.")
            covered = sum(1 for x, y in zip(tgt, t.sequence) if x.isalpha() and y.isalpha())
            tgt_len = sum(1 for x in tgt if x.isalpha())
            if tgt_len and covered / tgt_len < 0.5:
                warnings.append(f"{t.code} copre solo il {100 * covered / tgt_len:.0f}% del bersaglio.")
        for t in templates:
            if len(t.fields) < 4 or not t.fields[1]:
                warnings.append(f"{t.code}: intervallo di residui/catena non specificato (verranno usati FIRST/LAST).")
    return {
        "valid": not errors,
        "errors": errors,
        "warnings": warnings,
        "entries": [{"code": e.code, "kind": e.kind, "length": len(e.sequence),
                     "residues": sum(1 for c in e.sequence if c.isalpha()), "chains": len(e.chains())}
                    for e in entries],
    }
