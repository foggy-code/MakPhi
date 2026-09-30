"""Ricerca di template omologhi: RCSB (default), NCBI BLAST remoto, BLAST+ locale, MMseqs2 locale, AlphaFold DB.

I risultati passano dalla cache dei calcoli (`core/cache.py`): la chiave comprende sequenza, motore e
parametri e, per i motori locali, il timbro del database e dell'eseguibile.

AlphaFold DB non ha una ricerca per sequenza: il motore `alphafold` usa l'accessione UniProt indicata oppure, senza
accessione, le proteine di Swiss-Prot più simili trovate con NCBI BLAST remoto, e ne prende i modelli predetti.
"""

from __future__ import annotations

import io
import json
import math
import os
import re
from pathlib import Path
from typing import Optional

from ..core import cache
from ..core.errors import AppError, NotFound, RemoteServiceError, ToolUnavailable
from ..core.jobs import JobContext, default_env
from ..domain.sequences import SeqRecord
from . import align, remote, tools

ENGINES = {
    "rcsb": "RCSB PDB (ricerca rapida, di solito pochi secondi)",
    "ncbi": "NCBI BLAST remoto (1–5 min)",
    "local": "BLAST+ locale (database pdbaa)",
    "mmseqs": "MMseqs2 locale (indice di pdbaa, senza internet)",
    "alphafold": "AlphaFold DB (modelli predetti; senza accessione UniProt passa da NCBI BLAST, 1–5 min)",
}
MMSEQS_FIELDS = ["query", "target", "pident", "alnlen", "evalue", "bits", "qstart", "qend", "tstart", "tend",
                 "qlen", "tlen", "qaln", "taln", "theader"]
# Scadenza della cache per motore: RCSB e NCBI cercano nel PDB, che cambia ogni settimana; AlphaFold DB passa da
# NCBI (Swiss-Prot) e da EBI, servizi remoti come gli altri. I motori locali non scadono: la chiave contiene il
# timbro del database e la pulizia automatica elimina le voci dopo 30 giorni.
CACHE_TTL = {"rcsb": cache.REMOTE_TTL, "ncbi": cache.REMOTE_TTL, "alphafold": cache.REMOTE_TTL}
# Motore alphafold senza accessione: modelli delle prime N proteine Swiss-Prot trovate da BLAST (una richiesta a
# EBI ciascuna)
ALPHAFOLD_ACCESSIONS = 10
PREDICTED_METHOD = "Modello predetto (AlphaFold DB)"


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


# ------------------------------------------------------------------------------------------ AlphaFold DB
# Identificativi di Swiss-Prot/TrEMBL nei risultati BLAST: "sp|P0CG48.1|UBC_HUMAN", "tr|A0A024R7T2|..."
_UNIPROT_TITLE = re.compile(r"(?:^|[\s>|])(?:sp|tr)\|([A-Z0-9]{6,10})(?:\.\d+)?\|", re.IGNORECASE)
_VERSIONED = re.compile(r"^([A-Z0-9]{6,10})(?:\.\d+)?$", re.IGNORECASE)
# Nome di sequenza da intestazione FASTA di UniProt dopo sanitize_code ("sp|P0CG48|UBC_HUMAN" → "sp_P0CG48_UBC_HUMAN")
_UNIPROT_NAME = re.compile(r"^(?:sp|tr)_([A-Z0-9]{6,10})(?:_|$)", re.IGNORECASE)
# Lettere fuori dall'alfabeto di BLOSUM62 (U, O, J): X nell'allineamento
_BLOSUM_OTHER = re.compile(r"[^ARNDCQEGHILKMFPSTWYVBZX]")


def _uniprot_or_none(value: str) -> Optional[str]:
    try:
        return remote.validate_uniprot_accession(value)
    except AppError:
        return None


def uniprot_from_name(name: str) -> str:
    """Accessione UniProt dal nome di una sequenza con intestazione FASTA di UniProt (sp|ACC|NOME), altrimenti ''."""
    m = _UNIPROT_NAME.match(name or "")
    return (_uniprot_or_none(m.group(1)) or "") if m else ""


def uniprot_hits_from_blast_xml(xml_text: str) -> list[dict]:
    """Accessioni UniProt dei risultati di BLAST su Swiss-Prot, nell'ordine di BLAST e senza ripetizioni.

    L'accessione viene dall'identificativo (sp|P0CG48.1|UBC_HUMAN) o dall'accessione NCBI con versione (P0CG48.1);
    solo quelle nel formato UniProtKB. E-value e bit score sono quelli del primo HSP.
    """
    from Bio.Blast import NCBIXML

    record = NCBIXML.read(io.StringIO(xml_text))
    out, seen = [], set()
    for alignment in record.alignments:
        acc = None
        for text in (alignment.hit_id, alignment.title):
            m = _UNIPROT_TITLE.search(text or "")
            if m:
                acc = _uniprot_or_none(m.group(1))
                if acc:
                    break
        if acc is None:
            m = _VERSIONED.match((alignment.accession or "").strip())
            acc = _uniprot_or_none(m.group(1)) if m else None
        if acc is None or acc in seen:
            continue
        seen.add(acc)
        hsp = alignment.hsps[0] if alignment.hsps else None
        out.append({"accession": acc, "evalue": hsp.expect if hsp else None,
                    "bitscore": round(hsp.bits, 1) if hsp else None, "title": alignment.hit_def[:300]})
    return out


def _alphafold_hit(query: str, entry: dict, blast: Optional[dict]) -> Optional[dict]:
    """Hit per un modello di AlphaFold DB: allineamento locale del bersaglio sulla sequenza del modello.

    Smith-Waterman con BLOSUM62 e gap 11/1 (come BLAST e il motore RCSB); identità e positivi sulla lunghezza
    dell'allineamento (gap compresi), copertura = tratto del bersaglio allineato. L'E-value è quello di BLAST su
    Swiss-Prot se l'accessione viene da lì, altrimenti quello del solo confronto a coppie (statistica di
    Karlin-Altschul con spazio di ricerca lunghezza del bersaglio × lunghezza del modello): è un valore indicativo.
    """
    subject = _BLOSUM_OTHER.sub("X", (entry.get("sequence") or "").upper())
    if not subject:
        return None
    res = align.pairwise(SeqRecord("query", _BLOSUM_OTHER.sub("X", query)), SeqRecord(entry["entry_id"], subject),
                         mode="local", matrix="BLOSUM62", gap_open=11.0, gap_extend=1.0)
    q_aln, s_aln = res["aligned"]
    length = len(q_aln)
    ranges = res.get("ranges")
    if not length or not ranges:
        return None
    (q_start, q_end), (s_start, s_end) = ranges
    from Bio.Align import substitution_matrices

    blosum = substitution_matrices.load("BLOSUM62")
    positives = sum(1 for a, b in zip(q_aln, s_aln) if a != "-" and b != "-" and blosum[a][b] > 0)
    bits = (remote.KA_LAMBDA * res["score"] - math.log(remote.KA_K)) / math.log(2)
    pair_evalue = len(query) * len(subject) * 2.0 ** (-bits)
    evalue = blast["evalue"] if blast and blast.get("evalue") is not None else float(f"{pair_evalue:.2e}")
    name = entry.get("description") or entry.get("uniprot_id") or entry["entry_id"]
    return {
        "pdb_id": entry["entry_id"], "chain": entry.get("chain") or "A", "template_id": entry["entry_id"],
        "title": f"{name}" + (f" ({entry['gene']})" if entry.get("gene") else ""),
        "description": entry.get("description", ""), "organism": entry.get("organism", ""),
        "method": PREDICTED_METHOD + (f" v{entry['version']}" if entry.get("version") else ""), "resolution": None,
        "identity": round(100.0 * res["stats"]["identities"] / length, 1),
        "positives": round(100.0 * positives / length, 1),
        "coverage": round(100.0 * (q_end - q_start + 1) / len(query), 1),
        "evalue": evalue, "bitscore": blast["bitscore"] if blast and blast.get("bitscore") is not None
        else round(bits, 1), "align_length": length,
        "query_start": q_start, "query_end": q_end, "subject_start": s_start, "subject_end": s_end,
        "query_aligned": q_aln, "subject_aligned": s_aln, "subject_length": len(subject),
        "source": "alphafold", "predicted": True, "plddt": entry.get("plddt"),
        "uniprot_accession": entry.get("accession"), "uniprot_start": entry.get("uniprot_start"),
        "uniprot_end": entry.get("uniprot_end"), "model_version": entry.get("version"),
    }


def _alphafold_search(ctx: JobContext, sequence: str, *, uniprot: str, evalue: float, max_hits: int,
                      outcome: dict) -> list[dict]:
    """Modelli di AlphaFold DB per l'accessione indicata o per le proteine Swiss-Prot più simili (NCBI BLAST).

    Un'accessione senza modello (404) è un esito definitivo; un errore di EBI rende il risultato parziale (niente
    cache).
    """
    query = re.sub(r"[^A-Z]", "", sequence.upper())
    if uniprot:
        candidates = [{"accession": uniprot, "evalue": None, "bitscore": None, "title": ""}]
        ctx.log(f"Modelli di AlphaFold DB per l'accessione UniProt {uniprot}")
    else:
        ctx.progress(0.1, "NCBI BLAST su Swiss-Prot: proteine UniProt più simili")
        ctx.log("Nessuna accessione UniProt indicata: ricerca delle proteine più simili con NCBI BLAST su Swiss-Prot")
        wanted = max(1, min(max_hits, ALPHAFOLD_ACCESSIONS))
        xml = remote.ncbi_blast(sequence, database="swissprot", evalue=evalue, max_hits=wanted,
                                email=ctx.settings.contact_email, check=ctx.check, log=ctx.log)
        candidates = uniprot_hits_from_blast_xml(xml)[:wanted]
        if not candidates:
            ctx.log("Nessuna proteina di Swiss-Prot sotto la soglia di E-value.")
            return []
        ctx.log("Proteine Swiss-Prot più simili: " + ", ".join(c["accession"] for c in candidates))
    hits = []
    for n, cand in enumerate(candidates):
        ctx.check()
        ctx.progress(0.5 + 0.4 * n / len(candidates), f"AlphaFold DB: {cand['accession']}")
        try:
            entries = remote.alphafold_entries(cand["accession"], check=ctx.check, log=ctx.log)
        except NotFound:
            ctx.log(f"{cand['accession']}: nessun modello in AlphaFold DB")
            continue
        except RemoteServiceError as exc:
            ctx.log(f"{cand['accession']}: AlphaFold DB non disponibile ({exc.message})")
            outcome["complete"] = False
            continue
        for entry in entries:
            hit = _alphafold_hit(query, entry, cand)
            if hit is not None:
                hits.append(hit)
    hits.sort(key=lambda h: (h["evalue"], -h["bitscore"]))
    return hits[:max_hits]


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
           databases_dir: Optional[Path] = None, taxid: str = "", use_cache: bool = True, uniprot: str = "") -> dict:
    """Ricerca dei template; `use_cache=False` ricalcola e sostituisce il risultato in cache.

    `uniprot`: solo per il motore alphafold, accessione UniProt della sequenza (facoltativa).
    """
    if engine not in ENGINES:
        raise AppError(f"Motore di ricerca sconosciuto: {engine}")
    uniprot = remote.validate_uniprot_accession(uniprot) if engine == "alphafold" and uniprot.strip() else ""
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
    if engine == "alphafold":  # solo qui: le chiavi degli altri motori restano quelle di prima
        descriptor["uniprot"] = uniprot
    outcome = {"complete": True}

    def run() -> dict:
        if engine == "rcsb":
            hits = remote.rcsb_sequence_search(sequence, evalue=evalue, max_hits=max_hits, check=ctx.check,
                                               log=ctx.log)
        elif engine == "alphafold":
            hits = _alphafold_search(ctx, sequence, uniprot=uniprot, evalue=evalue, max_hits=max_hits,
                                     outcome=outcome)
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
        if engine not in ("rcsb", "alphafold") and hits:
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
        out = {"engine": engine, "engine_label": ENGINES[engine], "query_length": len(sequence), "hits": hits}
        if engine == "alphafold":
            out["uniprot"] = uniprot
            out["engine_label"] = (f"AlphaFold DB, modelli predetti dell'accessione UniProt {uniprot}" if uniprot
                                   else "AlphaFold DB, modelli predetti delle proteine Swiss-Prot più simili (NCBI BLAST)")
        return out

    return cache.run_cached(ctx, "search", descriptor, run, use_cache=use_cache, ttl=CACHE_TTL.get(engine),
                            keep=lambda _result: outcome["complete"])


def best_template(hits: list[dict]) -> Optional[dict]:
    """Template consigliato: massimo prodotto identità × copertura; a parità, una struttura sperimentale prima di un
    modello predetto (AlphaFold), poi la risoluzione migliore, poi il pLDDT medio più alto.

    Un modello predetto è un'ipotesi e non un dato: lo si preferisce solo se allinea meglio il bersaglio.
    """
    if not hits:
        return None
    return max(hits, key=lambda h: (h["identity"] * h["coverage"], 0 if h.get("predicted") else 1,
                                    -(h.get("resolution") or 99), h.get("plddt") or 0))
