"""Tasche (fpocket) ed elettrostatica (PDB2PQR + APBS) di una struttura (F7b): job `pockets` ed `electrostatics`.

Affiancano il job `analysis` (services/analysis.py) con lo stesso schema: sorgente della struttura come la qualità
(template, file di un job, file caricato), impronta SHA-256 del file analizzato nel risultato, ricerca del job completato
più recente sulla stessa struttura per la pagina Qualità (`latest`, `active`). I motori sono `engines/pockets.py` ed
`engines/electrostatics.py`, la logica pura `domain/pockets.py` e `domain/electrostatics.py`.

- **Priorità:** 0 per entrambi (`core/jobs.PRIORITY`, come `quality` e `analysis`): fpocket impiega meno di un secondo
  su 1UBQ, PDB2PQR + APBS una decina di secondi; l'utente li aspetta davanti allo schermo. Pool "cpu".
- **Niente cache dei calcoli:** fpocket è istantaneo e la mappa di APBS (16 MB per 1UBQ) è un file del job, non un
  risultato da tenere in `data/cache`.
- **Strumenti mancanti:** il job fallisce con `ToolUnavailable` e il messaggio di `tools.missing_message`; la pagina
  Qualità lo dice prima di proporre il calcolo (`/api/system/info`).
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Optional

from ..core import cache
from ..core.errors import AppError
from ..core.jobs import JobContext, JobManager
from ..domain import electrostatics as es
from ..engines import electrostatics, pockets
from .compute import ComputeService, source_key

POCKETS = "pockets"
ELECTROSTATICS = "electrostatics"
KINDS = (POCKETS, ELECTROSTATICS)
LOOKUP_LIMIT = 200  # job completati esaminati per trovare il più recente di una struttura


def _result(job: dict) -> dict:
    return job.get("result") if isinstance(job.get("result"), dict) else {}


def pockets_summary(job: dict) -> Optional[dict]:
    """Riepilogo per il report del modello: tasche con i descrittori e i residui (senza le sfere alfa, che servono
    solo al 3D), riepilogo e parametri di fpocket. None senza l'elenco delle tasche."""
    result = _result(job)
    if not isinstance(result.get("pockets"), list):
        return None
    pockets = [{k: v for k, v in p.items() if k not in ("spheres", "center")}
               for p in result["pockets"] if isinstance(p, dict)]
    return {"job_id": job.get("id"), "finished_at": job.get("finished_at"), "pockets": pockets,
            "summary": result.get("summary") or {}, "params": result.get("params") or {},
            "warnings": [str(w) for w in result.get("warnings") or []]}


def electrostatics_summary(job: dict) -> Optional[dict]:
    """Riepilogo per il report del modello: potenziale per residuo, cariche, parametri di PDB2PQR e APBS. None senza
    i residui."""
    result = _result(job)
    if not isinstance(result.get("residues"), list):
        return None
    keys = ("forcefield", "ph", "net_charge", "charges", "grid", "parameters", "sampling", "scale", "map", "residues",
            "summary")
    return {"job_id": job.get("id"), "finished_at": job.get("finished_at"), **{k: result.get(k) for k in keys},
            "warnings": [str(w) for w in result.get("warnings") or []]}


class StructureToolsService:
    def __init__(self, jobs: JobManager, compute: ComputeService) -> None:
        self.jobs = jobs
        self.compute = compute

    def register(self) -> None:
        self.jobs.register(POCKETS, self.handle_pockets)
        self.jobs.register(ELECTROSTATICS, self.handle_electrostatics)

    # ------------------------------------------------------------------------------ richiesta
    def validate(self, kind: str, params: dict) -> None:
        """Errori immediati, prima di accodare il job: struttura, campo di forza, pH."""
        self.compute.resolve_structure(params.get("source") or {})
        if kind == ELECTROSTATICS:
            ff = str(params.get("forcefield") or es.DEFAULT_FORCEFIELD).upper()
            if ff not in es.FORCEFIELDS:
                raise AppError(f"Campo di forza non supportato: {ff} (ammessi: {', '.join(es.FORCEFIELDS)}).",
                               code="invalid_forcefield")

    def _source(self, p: dict) -> tuple[dict, Path, str, str]:
        source = p.get("source") or {}
        path, label = self.compute.resolve_structure(source)
        return source, path, label, cache.content_hash(path)

    # ------------------------------------------------------------------------------ job
    def handle_pockets(self, ctx: JobContext, p: dict) -> dict:
        source, path, label, fingerprint = self._source(p)
        ctx.progress(0.1, "Ricerca delle tasche (fpocket)")
        data = pockets.run(path, ctx, ctx.workdir)
        s = data["summary"]
        best = s["best"]
        ctx.log(f"fpocket: {s['count']} tasche, {s['druggable']} con druggability ≥ {s['threshold']:g}"
                + (f"; migliore tasca {best['id']} (druggability {best['druggability']}, volume {best['volume']} Å³)"
                   if best else ""))
        return {"source": source, "label": label, "source_sha256": fingerprint, **data}

    def handle_electrostatics(self, ctx: JobContext, p: dict) -> dict:
        source, path, label, fingerprint = self._source(p)
        ff = str(p.get("forcefield") or es.DEFAULT_FORCEFIELD).upper()
        ph = float(p.get("ph") if p.get("ph") is not None else es.DEFAULT_PH)
        data = electrostatics.run(path, ctx, ctx.workdir, forcefield=ff, ph=ph,
                                  progress=lambda f, stage: ctx.progress(0.05 + 0.9 * f, stage))
        s = data["summary"]
        ctx.log(f"Potenziale di superficie: {s['residues']} residui esposti, da {s['min']} a {s['max']} kT/e; "
                f"carica netta {data['net_charge']:+.2f}")
        return {"source": source, "label": label, "source_sha256": fingerprint, **data}

    # ------------------------------------------------------------------------------ ricerca
    def latest(self, kind: str, source: dict, fingerprint: Optional[str] = None) -> Optional[dict]:
        """Job `kind` completato più recente sulla stessa struttura (stessa sorgente e, se entrambe le impronte ci
        sono, stesso contenuto del file), oppure None."""
        key = source_key(source)
        if key == ("?",):
            return None
        for row in self._sources(kind):
            if source_key(row["source"]) != key:
                continue
            try:
                job = self.jobs.get(row["id"])
            except AppError:  # eliminato nel frattempo
                continue
            result = job.get("result")
            if not isinstance(result, dict):
                continue
            if fingerprint and result.get("source_sha256") and result["source_sha256"] != fingerprint:
                continue  # stesso nome, file cambiato dopo il calcolo
            return job
        return None

    def active(self, kind: str, source: dict) -> Optional[dict]:
        """Job `kind` in coda o in corso più recente sulla stessa sorgente, oppure None."""
        key = source_key(source)
        if key == ("?",):
            return None
        for row in self._sources(kind, "status IN ('queued','running')", "created_at DESC"):
            if source_key(row["source"]) != key:
                continue
            try:
                return self.jobs.get(row["id"])
            except AppError:
                continue
        return None

    def _sources(self, kind: str, where: str = "status='completed'", order: str = "finished_at DESC") -> list[dict]:
        """(id, sorgente) dei job `kind` nello stato `where`, dal più recente; solo `params.source` (json_extract),
        con i parametri interi se SQLite non ha le funzioni JSON. `where` e `order` sono costanti del modulo."""
        sql = f"FROM jobs WHERE kind=? AND {where} ORDER BY {order}, rowid DESC LIMIT ?"
        try:
            rows = self.jobs.db.query(f"SELECT id, json_extract(params, '$.source') AS source {sql}",
                                      (kind, LOOKUP_LIMIT))
        except sqlite3.OperationalError:
            return [{"id": r["id"], "source": (r.get("params") or {}).get("source")}
                    for r in self.jobs.db.query(f"SELECT id, params {sql}", (kind, LOOKUP_LIMIT))]
        out = []
        for r in rows:
            try:
                src = json.loads(r["source"]) if isinstance(r.get("source"), str) else None
            except ValueError:
                src = None
            out.append({"id": r["id"], "source": src})
        return out

    def lookup(self, kind: str, source: dict) -> dict:
        """Risposta di `/latest`: job completato più recente e job della stessa sorgente in coda o in corso lanciato
        dopo (la pagina lo segue invece di proporre un altro calcolo)."""
        try:
            path, _name = self.compute.resolve_structure(source)
            fingerprint: Optional[str] = cache.content_hash(path)
        except (AppError, OSError):
            fingerprint = None
        job = self.latest(kind, source, fingerprint)
        active = self.active(kind, source)
        if active and job and str(active.get("created_at") or "") < str(job.get("created_at") or ""):
            active = None
        return {"job_id": job["id"] if job else None, "finished_at": job.get("finished_at") if job else None,
                "active_job_id": active["id"] if active else None}
