"""Client dei servizi remoti: UniProt, InterPro, RCSB PDB, NCBI BLAST, SWISS-MODEL QMEAN."""

from __future__ import annotations

import hashlib
import io
import json
import math
import re
import time
from pathlib import Path
from typing import Callable, Optional

import httpx

from .. import __version__
from ..core.errors import AppError, NotFound, RemoteServiceError
from ..core.storage import atomic_write_json, read_json

USER_AGENT = f"BioLoom/{__version__} (structural bioinformatics desktop suite)"
UNIPROT = "https://rest.uniprot.org/uniprotkb"
INTERPRO = "https://www.ebi.ac.uk/interpro/api"
RCSB_FILES = "https://files.rcsb.org/download"
RCSB_SEARCH = "https://search.rcsb.org/rcsbsearch/v2/query"
RCSB_GRAPHQL = "https://data.rcsb.org/graphql"
NCBI_BLAST = "https://blast.ncbi.nlm.nih.gov/Blast.cgi"
SWISSMODEL_QMEAN = "https://swissmodel.expasy.org/qmean/submit/"

_ACCESSION = re.compile(r"^[A-Za-z0-9_]{3,20}$")
_PDB_ID = re.compile(r"^[0-9][A-Za-z0-9]{3}$")

_client: Optional[httpx.Client] = None
_cache_dir: Optional[Path] = None


def configure(cache_dir: Path) -> None:
    global _cache_dir
    _cache_dir = cache_dir


def client() -> httpx.Client:
    global _client
    if _client is None:
        _client = httpx.Client(timeout=httpx.Timeout(45.0, connect=15.0), follow_redirects=True,
                               headers={"User-Agent": USER_AGENT})
    return _client


def _cache_get(namespace: str, key: str, ttl: float) -> Optional[object]:
    if _cache_dir is None:
        return None
    path = _cache_dir / namespace / f"{hashlib.sha256(key.encode()).hexdigest()[:32]}.json"
    if path.exists() and time.time() - path.stat().st_mtime < ttl:
        return read_json(path)
    return None


def _cache_put(namespace: str, key: str, value: object) -> None:
    if _cache_dir is None:
        return
    path = _cache_dir / namespace / f"{hashlib.sha256(key.encode()).hexdigest()[:32]}.json"
    atomic_write_json(path, value)


def _request(method: str, url: str, service: str, **kwargs) -> httpx.Response:
    try:
        resp = client().request(method, url, **kwargs)
    except httpx.TimeoutException:
        raise RemoteServiceError(f"{service}: tempo di risposta scaduto.")
    except httpx.HTTPError as exc:
        raise RemoteServiceError(f"{service} non raggiungibile: {exc}")
    if resp.status_code == 404:
        raise NotFound(f"{service}: risorsa non trovata.")
    if resp.status_code >= 400:
        err = RemoteServiceError(f"{service} ha risposto con errore {resp.status_code}.", details=resp.text[:500])
        err.upstream_status = resp.status_code
        raise err
    return resp


def _request_retry(method: str, url: str, service: str, *, attempts: int = 3, wait: float = 5.0,
                   check: Optional[Callable[[], None]] = None, log: Optional[Callable[[str], None]] = None,
                   **kwargs) -> httpx.Response:
    """Come _request, ma ritenta gli errori transitori (timeout, 5xx) con attesa crescente."""
    for attempt in range(1, attempts + 1):
        try:
            return _request(method, url, service, **kwargs)
        except RemoteServiceError as exc:
            status = getattr(exc, "upstream_status", None)
            if attempt == attempts or (status is not None and status < 500):
                raise
            if log:
                log(f"{exc.message} Nuovo tentativo {attempt + 1}/{attempts} tra {int(wait * attempt)} s...")
            _sleep_checked(wait * attempt, check, time.sleep)
    raise RemoteServiceError(f"{service} non disponibile.")


def validate_pdb_id(pdb_id: str) -> str:
    clean = (pdb_id or "").strip().upper()
    if not _PDB_ID.match(clean):
        raise AppError(f"Codice PDB non valido: '{pdb_id}' (atteso un codice di 4 caratteri, es. 1UBQ).")
    return clean


# ------------------------------------------------------------------------------------------ UniProt
def _uniprot_text(block: dict) -> str:
    return " ".join(t.get("value", "") for t in block.get("texts", []) if t.get("value"))


def uniprot_entry(accession: str) -> dict:
    acc = (accession or "").strip()
    if not _ACCESSION.match(acc):
        raise AppError(f"Accession UniProt non valido: '{accession}'.")
    cached = _cache_get("uniprot", acc.upper(), 7 * 86400)
    if cached:
        return cached
    data = _request("GET", f"{UNIPROT}/{acc}.json", "UniProt", headers={"Accept": "application/json"}).json()
    desc = data.get("proteinDescription", {})
    rec = desc.get("recommendedName") or (desc.get("submissionNames") or [{}])[0]
    genes = [g.get("geneName", {}).get("value") for g in data.get("genes", []) if g.get("geneName")]
    comments: dict[str, list[str]] = {}
    for c in data.get("comments", []):
        ctype = c.get("commentType", "")
        if ctype == "SUBCELLULAR LOCATION":
            locs = [loc.get("location", {}).get("value") for loc in c.get("subcellularLocations", [])]
            text = ", ".join(loc for loc in locs if loc)
        elif ctype == "DISEASE":
            dis = c.get("disease", {})
            text = f"{dis.get('diseaseId', '')}: {dis.get('description', '')}".strip(": ")
        else:
            text = _uniprot_text(c)
        if text:
            comments.setdefault(ctype, []).append(text)
    features = []
    for f in data.get("features", []):
        loc = f.get("location", {})
        start = loc.get("start", {}).get("value")
        end = loc.get("end", {}).get("value")
        if start is None or end is None:
            continue
        features.append({"type": f.get("type", ""), "start": start, "end": end,
                         "description": f.get("description", ""),
                         "ligand": (f.get("ligand") or {}).get("name", "")})
    pdb_refs, go_terms = [], []
    for x in data.get("uniProtKBCrossReferences", []):
        props = {p.get("key"): p.get("value") for p in x.get("properties", [])}
        if x.get("database") == "PDB":
            res = props.get("Resolution", "-")
            try:
                resolution = float(res.split()[0])
            except (ValueError, AttributeError, IndexError):
                resolution = None
            pdb_refs.append({"id": x.get("id"), "method": props.get("Method", ""), "resolution": resolution,
                             "chains": props.get("Chains", "")})
        elif x.get("database") == "GO":
            go_terms.append({"id": x.get("id"), "term": props.get("GoTerm", "")})
    seq = data.get("sequence", {})
    entry = {
        "accession": data.get("primaryAccession", acc),
        "entry_name": data.get("uniProtkbId", ""),
        "reviewed": "unreviewed" not in data.get("entryType", "").lower(),
        "protein_name": (rec.get("fullName") or {}).get("value", ""),
        "genes": genes,
        "organism": data.get("organism", {}).get("scientificName", ""),
        "taxonomy_id": data.get("organism", {}).get("taxonId"),
        "existence": data.get("proteinExistence", ""),
        "sequence": seq.get("value", ""),
        "length": seq.get("length", 0),
        "mass": seq.get("molWeight"),
        "comments": comments,
        "features": features,
        "pdb": sorted(pdb_refs, key=lambda r: (r["resolution"] is None, r["resolution"] or 0)),
        "go": go_terms[:60],
        "keywords": [k.get("name") for k in data.get("keywords", []) if k.get("name")],
    }
    _cache_put("uniprot", acc.upper(), entry)
    return entry


def uniprot_search(query: str, size: int = 15) -> list[dict]:
    q = (query or "").strip()
    if len(q) < 2:
        return []
    params = {"query": q, "size": str(max(1, min(size, 50))), "format": "json",
              "fields": "accession,id,protein_name,gene_names,organism_name,length,reviewed"}
    data = _request("GET", f"{UNIPROT}/search", "UniProt", params=params).json()
    out = []
    for r in data.get("results", []):
        desc = r.get("proteinDescription", {})
        rec = desc.get("recommendedName") or (desc.get("submissionNames") or [{}])[0]
        out.append({
            "accession": r.get("primaryAccession"),
            "entry_name": r.get("uniProtkbId"),
            "protein_name": (rec.get("fullName") or {}).get("value", ""),
            "genes": [g.get("geneName", {}).get("value") for g in r.get("genes", []) if g.get("geneName")],
            "organism": r.get("organism", {}).get("scientificName", ""),
            "length": r.get("sequence", {}).get("length"),
            "reviewed": "unreviewed" not in r.get("entryType", "").lower(),
        })
    return out


def interpro_domains(accession: str) -> list[dict]:
    acc = (accession or "").strip()
    if not _ACCESSION.match(acc):
        raise AppError(f"Accession UniProt non valido: '{accession}'.")
    cached = _cache_get("interpro", acc.upper(), 7 * 86400)
    if cached is not None:
        return cached
    try:
        resp = _request("GET", f"{INTERPRO}/entry/interpro/protein/uniprot/{acc}", "InterPro",
                        params={"page_size": "100"}, headers={"Accept": "application/json"})
    except NotFound:
        return []
    if resp.status_code == 204 or not resp.content:
        return []
    out = []
    for item in resp.json().get("results", []):
        meta = item.get("metadata", {})
        locations = []
        for prot in item.get("proteins", []):
            for group in prot.get("entry_protein_locations", []) or []:
                for frag in group.get("fragments", []):
                    locations.append({"start": frag.get("start"), "end": frag.get("end")})
        out.append({"accession": meta.get("accession"), "name": meta.get("name"), "type": meta.get("type"),
                    "locations": locations})
    _cache_put("interpro", acc.upper(), out)
    return out


# ------------------------------------------------------------------------------------------ RCSB
def rcsb_download(pdb_id: str, fmt: str = "pdb") -> str:
    pid = validate_pdb_id(pdb_id)
    ext = "cif" if fmt == "cif" else "pdb"
    resp = _request("GET", f"{RCSB_FILES}/{pid}.{ext}", "RCSB PDB")
    return resp.text


_GQL_ENTRIES = """
query($ids: [String!]!) {
  entries(entry_ids: $ids) {
    rcsb_id
    struct { title }
    exptl { method }
    rcsb_entry_info { resolution_combined deposited_polymer_monomer_count }
    rcsb_accession_info { initial_release_date }
    nonpolymer_entities { pdbx_entity_nonpoly { comp_id name } }
    polymer_entities {
      entity_poly { pdbx_seq_one_letter_code_can rcsb_entity_polymer_type }
      rcsb_polymer_entity { pdbx_description }
      rcsb_polymer_entity_container_identifiers { auth_asym_ids }
      rcsb_entity_source_organism { scientific_name }
    }
  }
}
"""


def rcsb_metadata(ids: list[str], check: Optional[Callable[[], None]] = None,
                  log: Optional[Callable[[str], None]] = None) -> dict[str, dict]:
    clean = sorted({validate_pdb_id(i) for i in ids if i})
    if not clean:
        return {}
    out: dict[str, dict] = {}
    missing = []
    for pid in clean:
        cached = _cache_get("rcsb_meta", pid, 30 * 86400)
        if cached:
            out[pid] = cached
        else:
            missing.append(pid)
    for start in range(0, len(missing), 25):
        chunk = missing[start:start + 25]
        body = {"query": _GQL_ENTRIES, "variables": {"ids": chunk}}
        data = _request_retry("POST", RCSB_GRAPHQL, "RCSB GraphQL", json=body, attempts=3, wait=5.0,
                              check=check, log=log, timeout=httpx.Timeout(60.0, connect=15.0)).json()
        for entry in (data.get("data") or {}).get("entries") or []:
            if not entry:
                continue
            pid = entry["rcsb_id"].upper()
            info = entry.get("rcsb_entry_info") or {}
            res = info.get("resolution_combined") or []
            chains = {}
            for poly in entry.get("polymer_entities") or []:
                seq = (poly.get("entity_poly") or {}).get("pdbx_seq_one_letter_code_can") or ""
                desc = (poly.get("rcsb_polymer_entity") or {}).get("pdbx_description") or ""
                orgs = poly.get("rcsb_entity_source_organism") or []
                for ch in (poly.get("rcsb_polymer_entity_container_identifiers") or {}).get("auth_asym_ids") or []:
                    chains[ch] = {"sequence": seq.replace("\n", ""), "description": desc,
                                  "organism": orgs[0].get("scientific_name") if orgs else ""}
            ligands = sorted({(n.get("pdbx_entity_nonpoly") or {}).get("comp_id")
                              for n in entry.get("nonpolymer_entities") or []} - {None})
            meta = {
                "id": pid,
                "title": (entry.get("struct") or {}).get("title", ""),
                "method": ", ".join(e.get("method", "") for e in entry.get("exptl") or []),
                "resolution": res[0] if res else None,
                "released": ((entry.get("rcsb_accession_info") or {}).get("initial_release_date") or "")[:10],
                "chains": chains,
                "ligands": ligands,
            }
            out[pid] = meta
            _cache_put("rcsb_meta", pid, meta)
    return out


# Parametri Karlin-Altschul per BLOSUM62 con gap 11/1 (tabelle NCBI).
KA_LAMBDA = 0.267
KA_K = 0.041
PDB_DB_RESIDUES = 5.0e7  # dimensione efficace stimata del PDB (residui)


def rcsb_sequence_search(sequence: str, evalue: float = 10.0, max_hits: int = 50,
                         check: Optional[Callable[[], None]] = None,
                         log: Optional[Callable[[str], None]] = None) -> list[dict]:
    """Ricerca di sequenza su RCSB e riallineamento locale Smith-Waterman (BLOSUM62, gap 11/1).

    RCSB restituisce le catene candidate (MMseqs2 lato server); il riallineamento locale fornisce
    identità, copertura, coordinate e sequenze allineate. L'E-value è stimato con la statistica di
    Karlin-Altschul su una dimensione del database fissa (valore indicativo).
    """
    from Bio import Align
    from Bio.Align import substitution_matrices

    query = re.sub(r"[^A-Z]", "", sequence.upper())
    if len(query) < 10:
        raise AppError("La sequenza è troppo corta per la ricerca (minimo 10 residui).")
    payload = {
        "query": {"type": "terminal", "service": "sequence",
                  "parameters": {"evalue_cutoff": float(evalue), "identity_cutoff": 0,
                                 "sequence_type": "protein", "value": query}},
        "return_type": "polymer_instance",
        "request_options": {"paginate": {"start": 0, "rows": max(60, max_hits * 2)},
                            "scoring_strategy": "sequence", "results_content_type": ["experimental"]},
    }
    # Il server di ricerca RCSB va in timeout (500) sotto carico; il risultato resta però in calcolo
    # e un nuovo tentativo di solito riesce.
    resp = _request_retry("POST", RCSB_SEARCH, "RCSB Search", json=payload, attempts=4, wait=5.0,
                          check=check, log=log, timeout=httpx.Timeout(90.0, connect=15.0))
    if resp.status_code == 204 or not resp.content:
        return []
    hits_raw = []
    for item in resp.json().get("result_set", []):
        ident = item.get("identifier", "")
        pid, _, chain = ident.partition(".")
        if len(pid) == 4:
            hits_raw.append((pid.upper(), chain or "A"))
    if check:
        check()
    meta = rcsb_metadata([p for p, _c in hits_raw], check=check, log=log)
    aligner = Align.PairwiseAligner()
    aligner.mode = "local"
    aligner.substitution_matrix = substitution_matrices.load("BLOSUM62")
    aligner.open_gap_score = -11.0
    aligner.extend_gap_score = -1.0
    results = []
    for pid, chain in hits_raw:
        if check:
            check()
        info = meta.get(pid, {})
        chain_info = info.get("chains", {}).get(chain) or {}
        target = chain_info.get("sequence", "")
        if not target:
            continue
        aln = aligner.align(query, target)[0]
        q_aln, t_aln = str(aln[0]), str(aln[1])
        q_blocks, t_blocks = aln.aligned
        if len(q_blocks) == 0:
            continue
        q_start, q_end = int(q_blocks[0][0]) + 1, int(q_blocks[-1][1])
        t_start, t_end = int(t_blocks[0][0]) + 1, int(t_blocks[-1][1])
        length = len(q_aln)
        identities = sum(1 for a, b in zip(q_aln, t_aln) if a == b and a != "-")
        positives = sum(1 for a, b in zip(q_aln, t_aln)
                        if a != "-" and b != "-" and aligner.substitution_matrix[a][b] > 0)
        bits = (KA_LAMBDA * aln.score - math.log(KA_K)) / math.log(2)
        e_val = len(query) * PDB_DB_RESIDUES * 2.0 ** (-bits)
        if e_val > evalue:
            continue
        results.append({
            "pdb_id": pid, "chain": chain, "template_id": f"{pid}_{chain}",
            "title": info.get("title", ""), "description": chain_info.get("description", ""),
            "organism": chain_info.get("organism", ""),
            "method": info.get("method", ""), "resolution": info.get("resolution"),
            "identity": round(100.0 * identities / length, 1) if length else 0.0,
            "positives": round(100.0 * positives / length, 1) if length else 0.0,
            "coverage": round(100.0 * (q_end - q_start + 1) / len(query), 1),
            "evalue": float(f"{e_val:.2e}"), "bitscore": round(bits, 1), "align_length": length,
            "query_start": q_start, "query_end": q_end, "subject_start": t_start, "subject_end": t_end,
            "query_aligned": q_aln, "subject_aligned": t_aln, "subject_length": len(target),
            "source": "rcsb",
        })
    results.sort(key=lambda h: (h["evalue"], -h["bitscore"]))
    return results[:max_hits]


# ------------------------------------------------------------------------------------------ NCBI BLAST
def ncbi_blast(sequence: str, *, program: str = "blastp", database: str = "pdb", evalue: float = 10.0,
               max_hits: int = 50, matrix: str = "BLOSUM62", entrez_query: str = "", email: str = "",
               check: Optional[Callable[[], None]] = None, log: Callable[[str], None] = print,
               sleep: Callable[[float], None] = time.sleep, max_wait: float = 45 * 60) -> str:
    """QBlast remoto conforme alle linee guida NCBI (polling ogni 60 s). Restituisce l'XML dei risultati."""
    params = {"CMD": "Put", "PROGRAM": program, "DATABASE": database, "QUERY": sequence,
              "EXPECT": str(evalue), "HITLIST_SIZE": str(max_hits), "MATRIX_NAME": matrix,
              "FORMAT_TYPE": "XML", "tool": "BioLoom"}
    if email:
        params["email"] = email
    if entrez_query:
        params["ENTREZ_QUERY"] = entrez_query
    rid, rtoe = None, 30
    for attempt in range(3):
        try:
            resp = _request("POST", NCBI_BLAST, "NCBI BLAST", data=params)
            m_rid = re.search(r"RID = (\S+)", resp.text)
            m_rtoe = re.search(r"RTOE = (\d+)", resp.text)
            if m_rid:
                rid = m_rid.group(1)
                rtoe = int(m_rtoe.group(1)) if m_rtoe else 30
                break
        except RemoteServiceError as exc:
            log(f"Invio a NCBI fallito (tentativo {attempt + 1}/3): {exc.message}")
        _sleep_checked(10 * (attempt + 1), check, sleep)
    if not rid:
        raise RemoteServiceError("NCBI BLAST non ha accettato la richiesta.")
    log(f"Richiesta registrata su NCBI: RID {rid}, tempo stimato {rtoe} s")
    _sleep_checked(max(15, min(rtoe, 90)), check, sleep)
    drops = 0
    started = time.time()
    while True:
        if check:
            check()
        try:
            status = _request("GET", NCBI_BLAST, "NCBI BLAST",
                              params={"CMD": "Get", "FORMAT_OBJECT": "SearchInfo", "RID": rid}).text
            drops = 0
        except RemoteServiceError as exc:
            drops += 1
            if drops > 5:
                raise RemoteServiceError(f"Connessione con NCBI persa ripetutamente (RID {rid}): {exc.message}")
            log(f"Connessione NCBI interrotta, nuovo tentativo ({drops}/5)...")
            _sleep_checked(30, check, sleep)
            continue
        if "Status=WAITING" in status:
            if time.time() - started > max_wait:
                raise RemoteServiceError(
                    f"NCBI non ha completato la ricerca entro {int(max_wait // 60)} minuti (RID {rid}): la coda dei "
                    f"server NCBI è congestionata. Riprova più tardi o usa il motore RCSB.")
            log(f"In attesa dei risultati NCBI (RID {rid}, {int(time.time() - started)} s)")
            _sleep_checked(60, check, sleep)
            continue
        if "Status=FAILED" in status:
            raise RemoteServiceError(f"La ricerca BLAST è fallita sui server NCBI (RID {rid}).")
        if "Status=UNKNOWN" in status:
            raise RemoteServiceError(f"RID {rid} scaduto o sconosciuto a NCBI.")
        if "Status=READY" in status:
            break
        _sleep_checked(60, check, sleep)
    resp = _request("GET", NCBI_BLAST, "NCBI BLAST",
                    params={"CMD": "Get", "RID": rid, "FORMAT_TYPE": "XML",
                            "ALIGNMENTS": str(max_hits), "DESCRIPTIONS": str(max_hits)})
    return resp.text


def _sleep_checked(seconds: float, check: Optional[Callable[[], None]], sleep: Callable[[float], None]) -> None:
    """Attesa a passi di 1 s con controllo di annullamento (il tempo è contato tramite `sleep`)."""
    remaining = float(seconds)
    while remaining > 0:
        if check:
            check()
        step = min(1.0, remaining)
        sleep(step)
        remaining -= step


# ------------------------------------------------------------------------------------------ SWISS-MODEL QMEAN
def normalize_qmean(data: dict) -> dict:
    """Punteggi dal JSON di QMEAN (stesso formato per SWISS-MODEL e per run_qmean.py locale)."""
    model = next(iter((data.get("models") or {}).values()), {})
    scores = model.get("scores", {}) or {}
    glob = scores.get("global_scores", {}) or {}
    local = scores.get("local_scores", {}) or {}
    per_residue = []
    if isinstance(local, dict):
        for chain, values in local.items():
            if isinstance(values, list):
                per_residue += [{"chain": chain, "index": i + 1, "score": v} for i, v in enumerate(values)]
    return {
        "qmean4_z": glob.get("qmean4_z_score"), "qmean4": glob.get("qmean4_norm_score"),
        "qmean6_z": glob.get("qmean6_z_score"), "qmeandisco": glob.get("avg_local_score"),
        "components": {k: v for k, v in glob.items() if k.endswith("_z_score")},
        "local": per_residue, "method": data.get("method"), "qmean_version": data.get("qmean_version"),
    }


def swissmodel_qmean(pdb_text: str, email: str, check: Optional[Callable[[], None]] = None,
                     log: Callable[[str], None] = print, sleep: Callable[[float], None] = time.sleep) -> dict:
    """Invia il modello al server QMEAN di SWISS-MODEL e attende i punteggi (richiede un'email reale)."""
    if not email:
        raise AppError("Per usare QMEAN (SWISS-MODEL) imposta la tua email nelle Impostazioni.",
                       code="email_required")
    files = {"structure": ("model.pdb", io.BytesIO(pdb_text.encode("utf-8")), "chemical/x-pdb")}
    resp = _request("POST", SWISSMODEL_QMEAN, "SWISS-MODEL QMEAN", files=files,
                    data={"email": email, "method": "qmeandisco"})
    info = resp.json()
    url = info.get("results_json")
    if not url:
        raise RemoteServiceError("Risposta inattesa dal server QMEAN.", details=info)
    log("Modello inviato a SWISS-MODEL QMEAN, attesa dei risultati...")
    for _ in range(120):
        _sleep_checked(5, check, sleep)
        data = _request("GET", url, "SWISS-MODEL QMEAN").json()
        status = data.get("status")
        if status == "COMPLETED":
            return {**normalize_qmean(data), "url": data.get("results_page"), "source": "swissmodel"}
        if status == "FAILED":
            raise RemoteServiceError("Il server QMEAN ha segnalato un errore di analisi.")
    raise RemoteServiceError("Il server QMEAN non ha completato l'analisi entro 10 minuti.")


def json_dumps(obj: object) -> str:
    return json.dumps(obj, ensure_ascii=False)
