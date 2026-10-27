"""Client dei servizi remoti: UniProt, InterPro, RCSB PDB, NCBI BLAST, AlphaFold DB, SWISS-MODEL QMEAN, ESMFold."""

from __future__ import annotations

import hashlib
import io
import json
import math
import re
import time
from pathlib import Path
from typing import Callable, Optional
from urllib.parse import urlsplit

import httpx

from .. import __version__
from ..core.errors import AppError, NotFound, RemoteServiceError
from ..core.storage import atomic_write_json, read_json
from ..domain import prediction

USER_AGENT = f"MakPhi/{__version__} (structural bioinformatics desktop suite)"
UNIPROT = "https://rest.uniprot.org/uniprotkb"
INTERPRO = "https://www.ebi.ac.uk/interpro/api"
RCSB_FILES = "https://files.rcsb.org/download"
RCSB_SEARCH = "https://search.rcsb.org/rcsbsearch/v2/query"
RCSB_GRAPHQL = "https://data.rcsb.org/graphql"
NCBI_BLAST = "https://blast.ncbi.nlm.nih.gov/Blast.cgi"
SWISSMODEL_QMEAN = "https://swissmodel.expasy.org/qmean/submit/"
ALPHAFOLD = "https://alphafold.ebi.ac.uk"
ALPHAFOLD_API = f"{ALPHAFOLD}/api/prediction"
# ESMFold (F9.3), API pubblica di ESM Atlas: POST con la sequenza nel corpo, risposta PDB (impostazione esmfold_url)
ESMFOLD_URL = "https://api.esmatlas.com/foldSequence/v1/pdb/"

_ACCESSION = re.compile(r"^[A-Za-z0-9_]{3,20}$")
_PDB_ID = re.compile(r"^[0-9][A-Za-z0-9]{3}$")
# Formato ufficiale delle accessioni UniProtKB (https://www.uniprot.org/help/accession_numbers)
_UNIPROT_ACCESSION = re.compile(r"^(?:[OPQ][0-9][A-Z0-9]{3}[0-9]|[A-NR-Z][0-9](?:[A-Z][A-Z0-9]{2}[0-9]){1,2})$")
# Modelli di AlphaFold DB: AF-<accessione>-F<frammento> (F2, F3... solo per le proteine lunghe divise in frammenti)
_AF_ID = re.compile(r"^AF-([A-Z0-9]{6,10})-F([1-9][0-9]{0,3})$")
# Si scaricano solo file del dominio di AlphaFold DB, qualunque URL contenga la risposta
_AF_FILE_URL = re.compile(r"^https://alphafold\.ebi\.ac\.uk/files/[A-Za-z0-9_.-]+$")
_AF_URL_VERSION = re.compile(r"_v(\d+)\.(?:pdb|cif|bcif)$")

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
    """Come _request, ma ritenta gli errori transitori (timeout, 5xx) con attesa crescente. `check` (annullamento)
    si controlla prima di ogni tentativo e durante le pause."""
    for attempt in range(1, attempts + 1):
        if check:
            check()
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


# Varianti di una voce UniProtKB (F9.2): le feature "Natural variant" e "Mutagenesis" sono già nella risposta di
# `uniprot_entry` ({acc}.json), ma lì se ne tengono solo tipo e posizione; qui la stessa richiesta con la sua cache
# (namespace proprio: le voci di `uniprot_entry` già in cache non cambiano formato). Si tengono le sole sostituzioni
# di un residuo (le delezioni e le sostituzioni di più residui non sono mutazioni puntuali).
VARIANT_TYPES = {"Natural variant": "naturale", "Mutagenesis": "mutagenesi"}
UNIPROT_VARIANTS_VERSION = 1


def parse_uniprot_variants(data: dict, accession: str = "") -> dict:
    """Varianti puntuali di una voce UniProtKB in JSON: [{type, category (malattia, naturale, mutagenesi), position,
    original, variation, description, ids (dbSNP…), evidences, feature_id}], con sequenza e malattie della voce."""
    diseases = {}
    for c in data.get("comments") or []:
        if c.get("commentType") == "DISEASE":
            dis = c.get("disease") or {}
            if dis.get("acronym"):
                diseases[str(dis["acronym"])] = str(dis.get("diseaseId") or dis["acronym"])
    out = []
    for f in data.get("features") or []:
        kind = VARIANT_TYPES.get(f.get("type", ""))
        loc = f.get("location") or {}
        start, end = (loc.get("start") or {}).get("value"), (loc.get("end") or {}).get("value")
        alt = f.get("alternativeSequence") or {}
        original = str(alt.get("originalSequence") or "")
        variations = [str(v) for v in alt.get("alternativeSequences") or []]
        if kind is None or start is None or start != end or len(original) != 1:
            continue
        description = str(f.get("description") or "")
        # "in CMH1; …": la variante è associata alla malattia con quella sigla (commenti DISEASE della voce)
        words = set(re.findall(r"[A-Za-z0-9-]+", description.split(";")[0])) if description.startswith("in ") else set()
        linked = sorted(diseases[w] for w in words if w in diseases)
        category = "mutagenesi" if kind == "mutagenesi" else "malattia" if linked else "naturale"
        ids = [f"{x.get('database')}:{x.get('id')}" for x in f.get("featureCrossReferences") or [] if x.get("id")]
        for variation in variations:
            if len(variation) != 1:
                continue
            out.append({"type": kind, "category": category, "position": int(start), "original": original.upper(),
                        "variation": variation.upper(), "description": description, "diseases": linked, "ids": ids,
                        "evidences": len(f.get("evidences") or []), "feature_id": f.get("featureId") or ""})
    out.sort(key=lambda v: (v["position"], v["variation"]))
    seq = data.get("sequence") or {}
    return {"accession": data.get("primaryAccession", accession), "entry_name": data.get("uniProtkbId", ""),
            "sequence": seq.get("value", ""), "variants": out, "diseases": diseases,
            "version": UNIPROT_VARIANTS_VERSION}


def uniprot_variants(accession: str) -> dict:
    """Varianti puntuali naturali e di mutagenesi di una voce UniProtKB (cache 7 giorni, come `uniprot_entry`)."""
    acc = validate_uniprot_accession(accession)
    key = f"v{UNIPROT_VARIANTS_VERSION}:{acc}"
    cached = _cache_get("uniprot_variants", key, 7 * 86400)
    if isinstance(cached, dict):
        return cached
    data = _request("GET", f"{UNIPROT}/{acc}.json", "UniProt", headers={"Accept": "application/json"}).json()
    parsed = parse_uniprot_variants(data, acc)
    _cache_put("uniprot_variants", key, parsed)
    return parsed


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
    refine { ls_R_factor_R_free pdbx_refine_id }
    assemblies {
      pdbx_struct_assembly { id oligomeric_details oligomeric_count }
      rcsb_struct_symmetry { kind oligomeric_state }
    }
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
# Versione dei metadati in cache: la 2 aggiunge R-free e stato oligomerico (scelta ragionata dei template). Una voce
# senza versione (scritta prima) si riscarica; se RCSB non risponde si usa lo stesso, segnata `stale` (niente R-free
# né stato oligomerico), così un computer senza rete non perde metodo e risoluzione già noti.
RCSB_META_VERSION = 2


def _rfree(entry: dict) -> Optional[float]:
    """R-free dell'affinamento da diffrazione; con più affinamenti (per esempio raggi X + neutroni) quello a
    raggi X."""
    rows = [r for r in entry.get("refine") or [] if isinstance(r, dict)]
    rows.sort(key=lambda r: 0 if "X-RAY" in str(r.get("pdbx_refine_id") or "").upper() else 1)
    for row in rows:
        value = _as_float(row.get("ls_R_factor_R_free"), 0.0, 1.0)
        if value:
            return value
    return None


def _oligomer(entry: dict) -> tuple[str, Optional[int]]:
    """Stato oligomerico dell'assemblaggio biologico 1 (il primo elencato se manca): la simmetria globale di RCSB
    ("Homo 2-mer", "Monomer") o, senza, `oligomeric_details` ("dimeric"), e il numero di catene."""
    assemblies = [a for a in entry.get("assemblies") or [] if isinstance(a, dict)]
    if not assemblies:
        return "", None
    first = next((a for a in assemblies if str((a.get("pdbx_struct_assembly") or {}).get("id")) == "1"),
                 assemblies[0])
    info = first.get("pdbx_struct_assembly") or {}
    symmetry = [s for s in first.get("rcsb_struct_symmetry") or [] if isinstance(s, dict)]
    glob = next((s for s in symmetry if str(s.get("kind") or "").lower().startswith("global")), None)
    state = str((glob or {}).get("oligomeric_state") or info.get("oligomeric_details") or "")
    return state[:60], _as_int(info.get("oligomeric_count"))


def hit_metadata(info: Optional[dict]) -> dict:
    """Campi dei metadati RCSB di una voce per un hit della ricerca (valori vuoti se la voce non li ha)."""
    info = info if isinstance(info, dict) else {}
    ligands = info.get("ligands")
    return {"rfree": info.get("rfree"), "released": info.get("released") or "",
            "ligands": list(ligands) if isinstance(ligands, list) else None,
            "oligomeric_state": info.get("oligomeric_state") or "", "oligomeric_count": info.get("oligomeric_count")}


def _graphql_entries(ids: list[str], check: Optional[Callable[[], None]],
                     log: Optional[Callable[[str], None]]) -> list[dict]:
    """Voci della risposta GraphQL; una risposta illeggibile, o con soli errori (query rifiutata), è un errore del
    servizio e non un elenco vuoto: prima diventava "nessun template trovato"."""
    resp = _request_retry("POST", RCSB_GRAPHQL, "RCSB GraphQL", json={"query": _GQL_ENTRIES, "variables": {"ids": ids}},
                          attempts=3, wait=5.0, check=check, log=log, timeout=httpx.Timeout(60.0, connect=15.0))
    try:
        data = resp.json()
    except ValueError:
        raise RemoteServiceError("RCSB GraphQL ha inviato una risposta non leggibile.", details=resp.text[:300])
    data = data if isinstance(data, dict) else {}
    entries = (data.get("data") or {}).get("entries") if isinstance(data.get("data"), dict) else None
    if entries is None and data.get("errors"):
        raise RemoteServiceError("RCSB GraphQL ha rifiutato la richiesta dei metadati.",
                                 details=str(data.get("errors"))[:500])
    return [e for e in entries or [] if isinstance(e, dict)]


def rcsb_metadata(ids: list[str], check: Optional[Callable[[], None]] = None,
                  log: Optional[Callable[[str], None]] = None) -> dict[str, dict]:
    """Metadati delle voci PDB (GraphQL di RCSB, in cache 30 giorni).

    Voci in cache di una versione precedente (senza R-free e stato oligomerico) si riscaricano; se RCSB non risponde
    si usano lo stesso, con `stale: True`, invece di perdere metodo e risoluzione.
    """
    clean = sorted({validate_pdb_id(i) for i in ids if i})
    if not clean:
        return {}
    out: dict[str, dict] = {}
    missing = []
    stale: dict[str, dict] = {}
    for pid in clean:
        cached = _cache_get("rcsb_meta", pid, 30 * 86400)
        if isinstance(cached, dict) and cached.get("version") == RCSB_META_VERSION:
            out[pid] = cached
        else:
            if isinstance(cached, dict) and cached:
                stale[pid] = cached
            missing.append(pid)
    for start in range(0, len(missing), 25):
        chunk = missing[start:start + 25]
        try:
            entries = _graphql_entries(chunk, check, log)
        except RemoteServiceError:
            rest = missing[start:]
            if not all(pid in stale for pid in rest):
                raise
            if log:
                log("Metadati RCSB non aggiornabili: uso quelli in cache, senza R-free né stato oligomerico.")
            for pid in rest:
                out[pid] = {**stale[pid], "stale": True}
            break
        for entry in entries:
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
            state, count = _oligomer(entry)
            meta = {
                "id": pid,
                "version": RCSB_META_VERSION,
                "title": (entry.get("struct") or {}).get("title", ""),
                "method": ", ".join(e.get("method", "") for e in entry.get("exptl") or []),
                "resolution": res[0] if res else None,
                "released": ((entry.get("rcsb_accession_info") or {}).get("initial_release_date") or "")[:10],
                "chains": chains,
                "ligands": ligands,
                "rfree": _rfree(entry),
                "oligomeric_state": state,
                "oligomeric_count": count,
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
            "source": "rcsb", **hit_metadata(info),
        })
        if info.get("stale"):  # metadati di una versione precedente: la ricerca li segnala come parziali
            results[-1]["metadata_stale"] = True
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
              "FORMAT_TYPE": "XML", "tool": "MakPhi"}
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


# ------------------------------------------------------------------------------------------ AlphaFold DB
def validate_uniprot_accession(accession: str) -> str:
    """Accessione UniProtKB in maiuscolo, controllata con il formato ufficiale (es. P69905, A0A0B4J2F0)."""
    clean = (accession or "").strip().upper()
    if not _UNIPROT_ACCESSION.match(clean):
        raise AppError(f"Accessione UniProt non valida: '{accession}' (formato UniProtKB, es. P69905 o A0A0B4J2F0; "
                       "le isoforme non sono supportate).", code="invalid_accession")
    return clean


def parse_alphafold_id(value: str) -> Optional[tuple[str, int]]:
    """(accessione, frammento) da un identificativo AF-<ACC>-F<n>; None se non è un modello di AlphaFold DB."""
    m = _AF_ID.match((value or "").strip().upper())
    if m and _UNIPROT_ACCESSION.match(m.group(1)):
        return m.group(1), int(m.group(2))
    return None


def _as_int(value) -> Optional[int]:
    try:
        return int(value) if value is not None and not isinstance(value, bool) else None
    except (TypeError, ValueError):
        return None


def _as_float(value, low: float = 0.0, high: float = 100.0) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) and low <= number <= high else None


def _af_url(value) -> Optional[str]:
    return value if isinstance(value, str) and _AF_FILE_URL.match(value) else None


def _entry_sequence(item: dict, start: Optional[int], end: Optional[int]) -> str:
    """Sequenza modellata: quella della voce, oppure il tratto start–end della sequenza UniProt completa."""
    candidates = [re.sub(r"[^A-Z]", "", str(item.get(k) or "").upper()) for k in ("sequence", "uniprotSequence")]
    candidates = [c for c in candidates if c]
    if not candidates:
        return ""
    if start is not None and end is not None and end >= start:
        expected = end - start + 1
        for seq in candidates:
            if len(seq) == expected:
                return seq
        for seq in candidates:  # sequenza UniProt intera con l'intervallo del frammento
            if len(seq) >= end:
                return seq[start - 1:end]
    return candidates[0]


def parse_alphafold_entries(data: object, accession: str) -> list[dict]:
    """Voci della risposta di `/api/prediction/{accessione}`, tollerante a campi mancanti e a versioni diverse.

    Campi usati (API del 2021–2025): entryId (o modelEntityId), uniprotAccession, uniprotStart/uniprotEnd (o
    sequenceStart/sequenceEnd), uniprotSequence (o sequence), latestVersion (o la versione negli URL, o il
    massimo di allVersions), globalMetricValue (pLDDT medio), fractionPlddt*, pdbUrl, cifUrl, paeDocUrl. Le voci
    senza un identificativo AF-<ACC>-F<n> (complessi, altri formati) non sono template monomerici e si scartano;
    a parità di identificativo resta la versione più recente. Ordine: per frammento.
    """
    items = data if isinstance(data, list) else [data] if isinstance(data, dict) and data else []
    found: dict[str, dict] = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        acc = str(item.get("uniprotAccession") or accession or "").strip().upper()
        entry_id = str(item.get("entryId") or item.get("modelEntityId") or f"AF-{acc}-F1").strip().upper()
        parsed = parse_alphafold_id(entry_id)
        if parsed is None:
            continue
        acc, fragment = parsed
        pdb_url, cif_url = _af_url(item.get("pdbUrl")), _af_url(item.get("cifUrl"))
        versions = sorted({v for v in (_as_int(x) for x in item.get("allVersions") or []) if v})
        url_version = next((int(m.group(1)) for m in (_AF_URL_VERSION.search(u or "") for u in (pdb_url, cif_url))
                            if m), None)
        version = _as_int(item.get("latestVersion")) or url_version or (versions[-1] if versions else None)
        if version and not pdb_url:
            pdb_url = f"{ALPHAFOLD}/files/{entry_id}-model_v{version}.pdb"
        if version and not cif_url:
            cif_url = f"{ALPHAFOLD}/files/{entry_id}-model_v{version}.cif"
        start = _as_int(item.get("uniprotStart")) or _as_int(item.get("sequenceStart"))
        end = _as_int(item.get("uniprotEnd")) or _as_int(item.get("sequenceEnd"))
        sequence = _entry_sequence(item, start, end)
        if sequence and (start is None or end is None) and fragment == 1:
            start, end = 1, len(sequence)  # modello dell'intera proteina
        fractions = {key: _as_float(item.get(field), 0.0, 1.0) for key, field in (
            ("very_high", "fractionPlddtVeryHigh"), ("confident", "fractionPlddtConfident"),
            ("low", "fractionPlddtLow"), ("very_low", "fractionPlddtVeryLow"))}
        entry = {
            "entry_id": entry_id, "accession": acc, "fragment": fragment, "version": version, "versions": versions,
            "plddt": _as_float(item.get("globalMetricValue")),
            "plddt_fractions": fractions if any(v is not None for v in fractions.values()) else None,
            "uniprot_start": start, "uniprot_end": end, "sequence": sequence,
            "uniprot_id": str(item.get("uniprotId") or ""), "gene": str(item.get("gene") or ""),
            "description": str(item.get("uniprotDescription") or ""),
            "organism": str(item.get("organismScientificName") or ""), "tax_id": _as_int(item.get("taxId")),
            "chain": str(item.get("chainId") or "A")[:1] or "A",
            "created": str(item.get("modelCreatedDate") or "")[:10], "tool": str(item.get("toolUsed") or ""),
            "pdb_url": pdb_url, "cif_url": cif_url, "pae_url": _af_url(item.get("paeDocUrl")),
        }
        previous = found.get(entry_id)
        if previous is None or (entry["version"] or 0) > (previous["version"] or 0):
            found[entry_id] = entry
    return sorted(found.values(), key=lambda e: e["fragment"])


def alphafold_entries(accession: str, *, check: Optional[Callable[[], None]] = None,
                      log: Optional[Callable[[str], None]] = None) -> list[dict]:
    """Modelli di AlphaFold DB (EBI) per un'accessione UniProt, in cache per 7 giorni come le altre risposte remote.

    404, oppure una risposta senza voci utilizzabili: NotFound con un messaggio chiaro (nessun modello).
    """
    acc = validate_uniprot_accession(accession)
    cached = _cache_get("alphafold", acc, 7 * 86400)
    if isinstance(cached, list) and cached and all(isinstance(e, dict) and e.get("entry_id") for e in cached):
        return cached
    missing = NotFound(f"AlphaFold DB non ha un modello per l'accessione UniProt {acc} (proteina non coperta, ad "
                       "esempio più lunga di 2700 residui, oppure accessione secondaria o obsoleta).",
                       code="alphafold_not_found")
    try:
        resp = _request_retry("GET", f"{ALPHAFOLD_API}/{acc}", "AlphaFold DB", attempts=3, wait=5.0, check=check,
                              log=log, headers={"Accept": "application/json"})
    except NotFound:
        raise missing
    try:
        data = resp.json()
    except ValueError:
        raise RemoteServiceError("AlphaFold DB ha inviato una risposta non leggibile.", details=resp.text[:300])
    entries = parse_alphafold_entries(data, acc)
    if not entries:
        raise missing
    _cache_put("alphafold", acc, entries)
    return entries


def _looks_like_model(text: str, fmt: str) -> bool:
    if fmt == "cif":
        return "_atom_site." in text and re.search(r"^ATOM\s", text, re.MULTILINE) is not None
    return re.search(r"^ATOM  ", text, re.MULTILINE) is not None


def alphafold_download(entry: dict, *, check: Optional[Callable[[], None]] = None,
                       log: Optional[Callable[[str], None]] = None) -> tuple[str, str]:
    """File del modello: PDB, oppure mmCIF se il PDB non c'è. Restituisce (testo, formato) dopo aver verificato
    che contenga coordinate atomiche (non una pagina d'errore)."""
    entry_id = entry.get("entry_id", "?")
    for fmt, key in (("pdb", "pdb_url"), ("cif", "cif_url")):
        url = _af_url(entry.get(key))
        if not url:
            continue
        try:
            resp = _request_retry("GET", url, "AlphaFold DB", attempts=3, wait=5.0, check=check, log=log,
                                  timeout=httpx.Timeout(120.0, connect=15.0))
        except NotFound:
            continue
        text = resp.text
        if not _looks_like_model(text, fmt):
            raise RemoteServiceError(f"AlphaFold DB: il file {fmt.upper()} di {entry_id} non contiene coordinate "
                                     "atomiche.", details=text[:300])
        return text, fmt
    raise NotFound(f"AlphaFold DB: file del modello {entry_id} non trovato (né PDB né mmCIF).",
                   code="alphafold_not_found")


# ------------------------------------------------------------------------------------------ ESMFold (F9.3)
# Comportamento del servizio pubblico verificato a mano il 1° ottobre 2026 (mai nei test, che lo simulano):
# - POST del testo della sequenza → 200, `chemical/x-pdb`: HEADER, TITLE "ESMFOLD V1 PREDICTION FOR INPUT",
#   riferimento e licenza in REMARK 1, ATOM numerati da 1 sulla catena A, senza TER né END; pLDDT su 0–1 in ogni atomo
#   (ubiquitina umana, 76 residui: 1,0 s; un'altra sequenza di 50 residui: 19 s, il servizio ha una coda);
# - oltre 400 residui → 413 "Sequence is longer than 400." (JSON) senza calcolo;
# - lettere non standard (X, B, Z) → 200, ma i residui mancano dal file: per questo si rifiutano prima dell'invio.
ESMFOLD_MAX_LENGTH = 400
# Sotto i 10 residui non c'è un ripiegamento da confrontare (come il minimo della ricerca su RCSB)
ESMFOLD_MIN_LENGTH = 10
ESMFOLD_ALPHABET = frozenset("ACDEFGHIKLMNPQRSTVWY")
# Predizioni in cache per sequenza e indirizzo del servizio: 30 giorni, come la pulizia della cache remota
ESMFOLD_TTL = 30 * 86400
# Una predizione di 400 residui in coda al servizio può richiedere minuti
ESMFOLD_TIMEOUT = httpx.Timeout(300.0, connect=15.0)
# Risposta più grande di così: non è una predizione di al più 400 residui (circa 660 byte per residuo, 0,3 MB a 400
# residui), qualunque servizio sia configurato; come il limite di 20 MB per voce delle cache
ESMFOLD_MAX_BYTES = 20 * 1024 * 1024
_HTTP_URL = re.compile(r"^https?://[A-Za-z0-9.-]+(?::\d{1,5})?(?:/[A-Za-z0-9._~!$&'()*+,;=:@%/-]*)?$")


def validate_esmfold_url(url: str) -> str:
    """Indirizzo del servizio ESMFold (impostazione `esmfold_url`): http o https, senza credenziali, query né spazi;
    vuoto = il servizio pubblico di ESM Atlas."""
    clean = (url or "").strip()
    if not clean:
        return ESMFOLD_URL
    if len(clean) > 300 or not _HTTP_URL.match(clean):
        raise AppError(f"Indirizzo del servizio ESMFold non valido: '{clean[:80]}' (atteso un URL http o https, es. "
                       f"{ESMFOLD_URL}).", code="invalid_esmfold_url")
    return clean


def esmfold_sequence(sequence: str, max_length: int = ESMFOLD_MAX_LENGTH) -> str:
    """Sequenza da inviare a ESMFold: maiuscola, senza spazi né '*' finale, solo i 20 amminoacidi standard e al più
    `max_length` residui. Gli errori si danno prima di qualunque richiesta."""
    seq = re.sub(r"\s+", "", sequence or "").upper().rstrip("*")
    if not seq:
        raise AppError("Sequenza vuota: niente da inviare a ESMFold.", code="empty_sequence")
    bad = sorted({c for c in seq if c not in ESMFOLD_ALPHABET})
    if bad:
        where = ", ".join(str(i + 1) for i, c in enumerate(seq) if c not in ESMFOLD_ALPHABET)
        raise AppError(f"ESMFold accetta solo i 20 amminoacidi standard: la sequenza contiene {', '.join(bad)} "
                       f"(posizioni {where[:80]}). Il servizio salterebbe quei residui senza avvisare.",
                       code="invalid_sequence")
    if len(seq) > max_length:
        raise AppError(f"Sequenza troppo lunga per ESMFold: {len(seq)} residui, il servizio ne accetta al più "
                       f"{max_length}. Confronta un dominio alla volta oppure usa il modello di AlphaFold DB "
                       "(con l'accessione UniProt).", code="sequence_too_long")
    if len(seq) < ESMFOLD_MIN_LENGTH:
        raise AppError(f"Sequenza troppo corta per una predizione utile: {len(seq)} residui (minimo "
                       f"{ESMFOLD_MIN_LENGTH}).", code="sequence_too_short")
    return seq


def _esmfold_error_detail(service: str, status: Optional[int], body: object) -> str:
    """Testo di una risposta d'errore di ESMFold da mostrare nel messaggio del job: solo quello del servizio pubblico
    (es. 413 "Sequence is longer than 400."). Il corpo della risposta di un altro indirizzo va solo nel log del
    server: un indirizzo della rete interna non deve far leggere le sue risposte a chi può avviare i job."""
    detail = str(body or "")[:200]
    if not detail or httpx.URL(service).host == httpx.URL(ESMFOLD_URL).host:
        return detail
    print(f"[MakPhi] ESMFold {service}: errore {status}: {detail!r}", flush=True)
    return ""


def esmfold_cached(sequence: str, url: str = ESMFOLD_URL) -> Optional[dict]:
    """Predizione già in cache per la sequenza e il servizio (senza richieste), oppure None."""
    try:
        key = f"{validate_esmfold_url(url)}\n{esmfold_sequence(sequence)}"
    except AppError:
        return None
    cached = _cache_get("esmfold", key, ESMFOLD_TTL)
    return cached if isinstance(cached, dict) and cached.get("pdb") and cached.get("info") else None


def esmfold_predict(sequence: str, *, url: str = ESMFOLD_URL, name: str = "", use_cache: bool = True,
                    check: Optional[Callable[[], None]] = None, log: Optional[Callable[[str], None]] = None) -> dict:
    """Predizione di ESMFold per una sequenza: {pdb (pLDDT su 0–100, intestazione riconoscibile), info (scala
    originale, residui, pLDDT), url, sequence, cached}.

    Lunghezza e alfabeto si controllano prima dell'invio (`esmfold_sequence`); i nuovi tentativi valgono per i 5xx e
    gli errori di rete. Errori in italiano: servizio non raggiungibile o dismesso (`esmfold_unavailable`), sequenza
    rifiutata (`sequence_too_long` con il 413 del servizio, `esmfold_rejected`), risposta che non è una predizione
    della sequenza inviata (`esmfold_invalid_response`).
    """
    seq = esmfold_sequence(sequence)
    service = validate_esmfold_url(url)
    key = f"{service}\n{seq}"
    if use_cache:
        cached = esmfold_cached(seq, service)
        if cached is not None:
            return {**cached, "cached": True}
    host = httpx.URL(service).host
    try:
        # nessun reindirizzamento: il servizio pubblico risponde direttamente, e uno seguito porterebbe la richiesta
        # del server a un indirizzo che nessuno ha scelto (anche della sua rete interna)
        resp = _request_retry("POST", service, "ESMFold", attempts=3, wait=5.0, check=check, log=log,
                              content=seq.encode("ascii"), timeout=ESMFOLD_TIMEOUT, follow_redirects=False,
                              headers={"Content-Type": "text/plain", "Accept": "chemical/x-pdb, text/plain, */*"})
    except NotFound:
        raise RemoteServiceError(f"Il servizio ESMFold non risponde all'indirizzo {service} (404): potrebbe essere "
                                 "stato dismesso, oppure l'indirizzo in Impostazioni → Generale è sbagliato.",
                                 code="esmfold_unavailable")
    except RemoteServiceError as exc:
        status = getattr(exc, "upstream_status", None)
        detail = _esmfold_error_detail(service, status, exc.details)
        if status == 413:
            raise AppError(f"Il servizio ESMFold ha rifiutato la sequenza perché troppo lunga ({len(seq)} residui): "
                           f"{detail or 'limite del servizio superato'}.", code="sequence_too_long")
        if status is not None and status < 500:
            raise RemoteServiceError(f"Il servizio ESMFold ha rifiutato la richiesta (errore {status})"
                                     + (f": {detail}" if detail else ".")
                                     + (" Troppe richieste: riprova fra qualche minuto." if status == 429 else ""),
                                     code="esmfold_rejected")
        raise RemoteServiceError(f"{exc.message} Controlla la connessione a internet e l'indirizzo del servizio in "
                                 "Impostazioni → Generale; la predizione si può ripetere più tardi.",
                                 code="esmfold_unavailable")
    if 300 <= resp.status_code < 400:
        raise RemoteServiceError(f"Il servizio ESMFold all'indirizzo {service} risponde con un reindirizzamento "
                                 f"(codice {resp.status_code}), che non si segue: indica in Impostazioni → Generale "
                                 "l'indirizzo finale del servizio.", code="esmfold_unavailable")
    if len(resp.content) > ESMFOLD_MAX_BYTES:
        raise RemoteServiceError(f"ESMFold ha inviato una risposta non valida: {len(resp.content) // 1024 ** 2} MB per "
                                 f"{len(seq)} residui.", code="esmfold_invalid_response")
    try:
        pdb, info = prediction.normalize_pdb(resp.text, seq, predictor="esmfold", name=name, service=host or "")
    except ValueError as exc:
        raise RemoteServiceError(f"ESMFold ha inviato una risposta non valida: {exc}.",
                                 code="esmfold_invalid_response", details=resp.text[:300])
    out = {"pdb": pdb, "info": info, "url": service, "sequence": seq,
           "received": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    _cache_put("esmfold", key, out)
    return {**out, "cached": False}


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


def https_url(value: object, max_length: int = 500) -> Optional[str]:
    """`value` se è un indirizzo https:// ben formato (host presente, niente spazi né caratteri di controllo, niente
    credenziali), altrimenti None. Per gli indirizzi ricevuti da un servizio remoto che l'interfaccia mostra come link
    (pagina dei risultati di SWISS-MODEL): mai javascript:, data: o http in chiaro."""
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text or len(text) > max_length or any(c.isspace() or ord(c) < 32 or ord(c) == 127 for c in text):
        return None
    try:
        parts = urlsplit(text)
    except ValueError:
        return None
    if parts.scheme.lower() != "https" or not parts.hostname or "@" in parts.netloc:
        return None
    return text


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
            return {**normalize_qmean(data), "url": https_url(data.get("results_page")), "source": "swissmodel"}
        if status == "FAILED":
            raise RemoteServiceError("Il server QMEAN ha segnalato un errore di analisi.")
    raise RemoteServiceError("Il server QMEAN non ha completato l'analisi entro 10 minuti.")


def json_dumps(obj: object) -> str:
    return json.dumps(obj, ensure_ascii=False)
