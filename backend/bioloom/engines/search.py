"""Ricerca di template omologhi: RCSB (default), NCBI BLAST remoto, BLAST+ locale, MMseqs2 locale.

I risultati passano dalla cache dei calcoli (`core/cache.py`): la chiave comprende sequenza, motore e
parametri e, per i motori locali, il timbro del database e dell'eseguibile.
"""

from __future__ import annotations

import io
import json
import os
import re
from pathlib import Path
from typing import Optional

from ..core import cache
from ..core.errors import AppError, ToolUnavailable
from ..core.jobs import JobContext, default_env
from . import remote, tools

ENGINES = {
    "rcsb": "RCSB PDB (ricerca rapida, di solito pochi secondi)",
    "ncbi": "NCBI BLAST remoto (1–5 min)",
    "local": "BLAST+ locale (database pdbaa)",
    "mmseqs": "MMseqs2 locale (indice di pdbaa, senza internet)",
}
MMSEQS_FIELDS = ["query", "target", "pident", "alnlen", "evalue", "bits", "qstart", "qend", "tstart", "tend",
                 "qlen", "tlen", "qaln", "taln", "theader"]
# Scadenza della cache per motore: RCSB e NCBI cercano nel PDB, che cambia ogni settimana. I motori locali
# non scadono: la chiave contiene il timbro del database e la pulizia automatica elimina le voci dopo 30 giorni.
CACHE_TTL = {"rcsb": cache.REMOTE_TTL, "ncbi": cache.REMOTE_TTL}


_local_cache: Optional[Path] = None


def configure(local_cache: Path) -> None:
    """Cartella locale al computer (volume Docker `cache`) per l'indice veloce di MMseqs2."""
    global _local_cache
    _local_cache = local_cache


def mmseqs_database(databases_dir: Path) -> Optional[Path]:
    """Database MMseqs2 creato da pdbaa, nella cartella dati (piccolo, viaggia con i dati)."""
    candidate = databases_dir / "pdbaa_mmseqs" / "pdbaa"
    return candidate if Path(str(candidate) + ".dbtype").exists() else None


def mmseqs_stamp(db: Path) -> dict:
    stat = db.stat()
    return {"size": stat.st_size, "mtime": int(stat.st_mtime)}


def mmseqs_local_index(databases_dir: Path) -> Optional[Path]:
    """Copia indicizzata del database su questo computer, se aggiornata (ricerche ~10 volte più veloci).

    L'indice k-mer pesa circa 10 volte il database: sta nel volume locale e non sulla chiavetta.
    """
    db = mmseqs_database(databases_dir)
    if db is None or _local_cache is None:
        return None
    folder = _local_cache / "mmseqs"
    try:
        stamp = json.loads((folder / "stamp.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    indexed = folder / "pdbaa"
    if stamp == mmseqs_stamp(db) and Path(str(indexed) + ".idx").exists():
        return indexed
    return None


def parse_mmseqs_m8(text: str, source: str = "mmseqs") -> list[dict]:
    """Righe tabulari di `mmseqs easy-search` con i campi MMSEQS_FIELDS."""
    hits, seen = [], set()
    for line in text.splitlines():
        cols = line.rstrip("\n").split("\t")
        if len(cols) < len(MMSEQS_FIELDS):
            continue
        row = dict(zip(MMSEQS_FIELDS, cols))
        pid, chain = parse_pdb_title(row["target"])
        if not pid:
            pid, chain = parse_pdb_title(row["theader"])
        if not pid or (pid, chain) in seen:
            continue
        seen.add((pid, chain))
        qlen = int(row["qlen"]) or 1
        qstart, qend = int(row["qstart"]), int(row["qend"])
        title = row["theader"].split("\x01")[0]
        title = title.split(" ", 1)[1] if " " in title else title
        identity = float(row["pident"])
        hits.append({
            "pdb_id": pid, "chain": chain, "template_id": f"{pid}_{chain}", "title": title[:300],
            "description": "", "organism": "", "method": "", "resolution": None,
            "identity": round(identity if identity > 1 else identity * 100, 1), "positives": None,
            "coverage": round(100.0 * (qend - qstart + 1) / qlen, 1),
            "evalue": float(row["evalue"]), "bitscore": round(float(row["bits"]), 1),
            "align_length": int(row["alnlen"]), "query_start": qstart, "query_end": qend,
            "subject_start": int(row["tstart"]), "subject_end": int(row["tend"]),
            "query_aligned": row["qaln"], "subject_aligned": row["taln"], "subject_length": int(row["tlen"]),
            "source": source,
        })
    return hits
_TITLE_PDB = re.compile(r"pdb\|([0-9][A-Za-z0-9]{3})\|([A-Za-z0-9]+)", re.IGNORECASE)
_TITLE_UNDERSCORE = re.compile(r"\b([0-9][A-Za-z0-9]{3})_([A-Za-z0-9]{1,4})\b")


def parse_pdb_title(title: str) -> tuple[Optional[str], Optional[str]]:
    for pattern in (_TITLE_PDB, _TITLE_UNDERSCORE):
        m = pattern.search(title or "")
        if m:
            return m.group(1).upper(), m.group(2)
    return None, None


def local_database(databases_dir: Path) -> Optional[Path]:
    """Cerca un database BLAST 'pdbaa' in data/databases (file .pin/.pal)."""
    for candidate in (databases_dir / "pdbaa" / "pdbaa", databases_dir / "pdbaa"):
        if any(Path(str(candidate) + ext).exists() for ext in (".pin", ".pal", ".00.pin")):
            return candidate
    return None


def _hits_from_blast_xml(xml_text: str, query_len: int, source: str) -> list[dict]:
    from Bio.Blast import NCBIXML

    record = NCBIXML.read(io.StringIO(xml_text))
    qlen = record.query_letters or query_len
    hits = []
    seen = set()
    for alignment in record.alignments:
        pid, chain = parse_pdb_title(alignment.title)
        if not pid:
            pid, chain = parse_pdb_title(f"{alignment.hit_id} {alignment.hit_def}")
        if not pid:
            continue
        hsp = alignment.hsps[0]
        key = (pid, chain)
        if key in seen:
            continue
        seen.add(key)
        length = hsp.align_length or 1
        hits.append({
            "pdb_id": pid, "chain": chain, "template_id": f"{pid}_{chain}",
            "title": alignment.hit_def.split(">")[0][:300], "description": "", "organism": "",
            "method": "", "resolution": None,
            "identity": round(100.0 * hsp.identities / length, 1),
            "positives": round(100.0 * (hsp.positives or 0) / length, 1),
            "coverage": round(100.0 * (hsp.query_end - hsp.query_start + 1) / qlen, 1) if qlen else 0.0,
            "evalue": hsp.expect, "bitscore": round(hsp.bits, 1), "align_length": length,
            "query_start": hsp.query_start, "query_end": hsp.query_end,
            "subject_start": hsp.sbjct_start, "subject_end": hsp.sbjct_end,
            "query_aligned": hsp.query, "subject_aligned": hsp.sbjct, "subject_length": alignment.length,
            "source": source,
        })
    return hits


def database_stamp(db: Path) -> list:
    """Timbro di un database locale: dimensione e data di tutti i file con il suo prefisso (pdbaa.pin,
    pdbaa.00.psq, pdbaa_h.index...). Cambia a ogni aggiornamento o ricostruzione del database."""
    try:
        files = [p for p in db.parent.iterdir() if p.is_file() and (
            p.name == db.name or p.name.startswith(db.name + ".") or p.name.startswith(db.name + "_"))]
    except OSError:
        files = []
    return cache.files_stamp(files)


def search(ctx: JobContext, sequence: str, *, engine: str = "rcsb", evalue: float = 10.0, max_hits: int = 50,
           databases_dir: Optional[Path] = None, taxid: str = "", use_cache: bool = True) -> dict:
    """Ricerca dei template; `use_cache=False` ricalcola e sostituisce il risultato in cache."""
    if engine not in ENGINES:
        raise AppError(f"Motore di ricerca sconosciuto: {engine}")
    ctx.progress(0.05, ENGINES[engine])
    exe = db = fast = None
    if engine == "mmseqs":
        exe = tools.find("mmseqs")
        if not exe:
            raise ToolUnavailable("MMseqs2 non è installato.")
        db = mmseqs_database(databases_dir) if databases_dir else None
        if db is None:
            raise ToolUnavailable("Database MMseqs2 non preparato: Impostazioni → Database → Prepara MMseqs2.")
        fast = mmseqs_local_index(databases_dir)
    elif engine == "local":
        exe = tools.find("blastp")
        if not exe:
            raise ToolUnavailable("BLAST+ (blastp) non è installato.")
        db = local_database(databases_dir) if databases_dir else None
        if db is None:
            raise ToolUnavailable("Database locale 'pdbaa' non trovato in data/databases/pdbaa.")
    taxid = taxid if engine == "ncbi" else ""  # il filtro tassonomico esiste solo per NCBI
    descriptor = {
        "engine": engine, "sequence": re.sub(r"\s+", "", sequence).upper(), "evalue": float(evalue),
        "max_hits": int(max_hits), "taxid": taxid,
        "database": database_stamp(db) if db is not None else None,
        # l'indice veloce di MMseqs2 è una copia su questo computer: i risultati si tengono distinti
        "indexed": fast is not None,
        "tool": cache.file_stamp(exe),
    }
    outcome = {"complete": True}

    def run() -> dict:
        if engine == "rcsb":
            hits = remote.rcsb_sequence_search(sequence, evalue=evalue, max_hits=max_hits, check=ctx.check,
                                               log=ctx.log)
        elif engine == "ncbi":
            entrez = f"txid{taxid}[ORGN]" if taxid else ""
            xml = remote.ncbi_blast(sequence, evalue=evalue, max_hits=max_hits, entrez_query=entrez,
                                    email=ctx.settings.contact_email, check=ctx.check, log=ctx.log)
            hits = _hits_from_blast_xml(xml, len(sequence), "ncbi")
        elif engine == "mmseqs":
            if fast is None:
                ctx.log("Indice veloce MMseqs2 assente su questo computer: ricerca senza indice "
                        "(crealo da Impostazioni → Database).")
            query = ctx.path("query.fasta")
            query.write_text(f">query\n{sequence}\n", encoding="utf-8")
            out = ctx.path("mmseqs_hits.m8")
            threads = max(1, (os.cpu_count() or 2) - 1)
            result = ctx.run([exe, "easy-search", str(query), str(fast or db), str(out), str(ctx.tmp_dir / "mmseqs"),
                              "-e", str(evalue), "-s", "7.5", "--max-seqs", str(max(300, max_hits * 6)),
                              "--threads", str(threads), "--format-output", ",".join(MMSEQS_FIELDS)],
                             log_output=False)
            if result.returncode != 0 or not out.exists():
                raise AppError(f"MMseqs2 è terminato con errore: {result.output[-500:]}")
            hits = parse_mmseqs_m8(out.read_text(encoding="utf-8", errors="replace"))[:max_hits]
        else:
            query = ctx.path("query.fasta")
            query.write_text(f">query\n{sequence}\n", encoding="utf-8")
            result = ctx.run([exe, "-query", str(query), "-db", str(db), "-evalue", str(evalue),
                              "-max_target_seqs", str(max_hits), "-outfmt", "5"], env=default_env(), log_output=False)
            if result.returncode != 0:
                raise AppError(f"blastp è terminato con errore: {result.output[-500:]}")
            start = result.output.find("<?xml")
            hits = _hits_from_blast_xml(result.output[start:] if start >= 0 else result.output, len(sequence),
                                        "local")
        if engine != "rcsb" and hits:
            ctx.progress(0.9, "Metadati delle strutture (RCSB)")
            try:
                meta = remote.rcsb_metadata([h["pdb_id"] for h in hits[:100]])
                for h in hits:
                    info = meta.get(h["pdb_id"], {})
                    h["method"] = info.get("method", "")
                    h["resolution"] = info.get("resolution")
                    h["title"] = h["title"] or info.get("title", "")
            except AppError as exc:
                ctx.log(f"Metadati RCSB non disponibili: {exc.message}")
                outcome["complete"] = False  # senza metodo e risoluzione: risultato parziale, non va in cache
        return {"engine": engine, "engine_label": ENGINES[engine], "query_length": len(sequence), "hits": hits}

    return cache.run_cached(ctx, "search", descriptor, run, use_cache=use_cache, ttl=CACHE_TTL.get(engine),
                            keep=lambda _result: outcome["complete"])


def best_template(hits: list[dict]) -> Optional[dict]:
    """Template consigliato: massimo prodotto identità × copertura, poi risoluzione migliore."""
    if not hits:
        return None
    return max(hits, key=lambda h: (h["identity"] * h["coverage"], -(h.get("resolution") or 99)))
