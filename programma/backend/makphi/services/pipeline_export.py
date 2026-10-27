"""ZIP di una pipeline: modelli, rapporti di qualità, report HTML e tabelle, scritti su file uno alla volta.

Due ingressi, stesso archivio (`ZipBuilder`):

* job `pipeline_export` (`PipelineExportService`, `POST /api/pipeline/{id}/export`): l'archivio si costruisce in
  background con l'avanzamento e i report generati da più processi (`export_workers`); a job completato si scarica
  dai file del job. Sul server lo ZIP di 139.296 coppie, generato dentro la richiesta, avrebbe impiegato circa 5 giorni
  prima di mandare il primo byte (4 ricerche nel database per report) e il browser restava in caricamento;
* `GET /api/pipeline/{id}/export.zip`, per le pipeline piccole: lo stesso archivio dentro la richiesta, in un solo
  processo.

Tutto ciò che viene dal database (elementi, QMEAN, strumenti strutturali, progetto, template della libreria) si legge
una volta all'inizio; un report si genera poi solo dai file del job (`ReportService.pipeline_item_detached`), quindi
anche in un altro processo. I processi di lavoro partono con `spawn` (un `fork` del server, con i suoi thread e la
connessione al database, non sarebbe sicuro) e non aprono né il database né la libreria. Al più `WINDOW` report per
processo sono in volo: l'archivio si scrive nell'ordine degli elementi e i report pronti non si accumulano in memoria.
"""

from __future__ import annotations

import collections
import json
import multiprocessing
import os
import sqlite3
import zipfile
from concurrent.futures import Future, ProcessPoolExecutor
from pathlib import Path
from typing import Callable, Iterator, Optional

from ..core import cache
from ..core.errors import AppError, Conflict, NotFound
from ..core.jobs import TERMINAL, JobContext, JobManager
from ..core.storage import safe_join
from .analysis import KIND as ANALYSIS, report_summary
from .reports import ItemContext, ReportService
from .structure_tools import ELECTROSTATICS, POCKETS, electrostatics_summary, pockets_summary

KIND = "pipeline_export"
WINDOW = 4  # report in volo per processo di lavoro
PROGRESS_EVERY = 100  # elementi fra due aggiornamenti dell'avanzamento
MAX_WORKERS = 8  # oltre, la scrittura dell'archivio (un solo processo) resta il collo di bottiglia
# chiavi dei parametri della pipeline che servono al report (il FASTA può essere di centinaia di MB)
REPORT_PARAMS = ("template_mode", "engine", "evalue", "twilight", "max_templates", "fixed_combination", "options",
                 "templates")
TOOLS = ((ANALYSIS, "analysis", report_summary), (POCKETS, "pockets", pockets_summary),
         (ELECTROSTATICS, "electrostatics", electrostatics_summary))


def export_workers() -> int:
    """Processi per i report: i core meno uno, al più MAX_WORKERS (misurato sul server: un report costa circa 120 ms,
    la scrittura nell'archivio circa 14 ms per coppia)."""
    return max(1, min(MAX_WORKERS, (os.cpu_count() or 2) - 1))


# ------------------------------------------------------------------------------ processi di lavoro
class _FolderJobs:
    """I soli file di un job, dalla sua cartella definitiva: quanto serve a `pipeline_item_detached`."""

    def __init__(self, root: Path) -> None:
        self.root = root

    def file_path(self, job_id: str, relpath: str) -> Path:
        path = safe_join(self.root, *relpath.replace("\\", "/").split("/"))
        if path.is_file():
            return path
        raise NotFound(f"File {relpath} non trovato nel job {job_id}.")


_worker: dict = {}


def _init_worker(root: str, job: dict, library: dict) -> None:
    service = ReportService(_FolderJobs(Path(root)), None, None)  # type: ignore[arg-type]
    service.library_cache = library
    _worker.update(service=service, job=job)


def _render(row: dict, context: ItemContext) -> tuple[Optional[str], Optional[str], dict]:
    """(report, errore, esito) di un elemento: l'esito ha `consensus` (punteggio e fascia del rapporto di qualità,
    per metriche.csv) e `qmean` (il report include il QMEAN)."""
    return _render_with(_worker["service"], _worker["job"], row, context)


def _render_with(service: ReportService, job: dict, row: dict,
                 context: ItemContext) -> tuple[Optional[str], Optional[str], dict]:
    try:
        text, info = service.pipeline_item_detached(job, row, context)
    except Exception as exc:  # un report non generato non deve fermare l'esportazione
        return None, getattr(exc, "message", None) or f"{type(exc).__name__}: {exc}", {}
    quality = info.get("quality") if isinstance(info.get("quality"), dict) else {}
    cons = quality.get("consensus") if isinstance(quality.get("consensus"), dict) else {}
    return text, None, {"consensus": (cons.get("score"), cons.get("band")), "qmean": info.get("qmean")}


# ------------------------------------------------------------------------------ costruzione dell'archivio
class ZipBuilder:
    """Archivio di una pipeline su `dest`. `workers` = 0: report nello stesso processo; `progress(frazione, fase)` e
    `check()` (annullamento) vengono dal job."""

    def __init__(self, pipeline, job_id: str, dest: Path, *, workers: int = 0,
                 progress: Optional[Callable[[Optional[float], Optional[str]], None]] = None,
                 check: Optional[Callable[[], None]] = None) -> None:
        self.pipeline, self.jobs, self.db = pipeline, pipeline.jobs, pipeline.db
        self.job_id, self.dest, self.workers = job_id, dest, workers
        self.progress, self.check = progress or (lambda *_a: None), check or (lambda: None)
        self.summary = {"models": 0, "reports": 0, "missing": 0, "qmean": 0}

    def run(self) -> dict:
        from .pipeline import ITEM_COLUMNS, is_cross  # services/pipeline importa questo modulo

        job = self.jobs.get(self.job_id)
        self.progress(0.01, "Lettura degli elementi, dei QMEAN e degli strumenti della pipeline")
        rows = self.db.query(f"SELECT {ITEM_COLUMNS}, sequence FROM pipeline_items WHERE job_id=? "
                             "AND status='completed' ORDER BY idx", (self.job_id,))
        self._qmean = self.pipeline.qmean_jobs_by_file(self.job_id)  # impronte verificate a ogni report
        self._tools = self._tools_by_file()
        self._project = self.db.scalar("SELECT name FROM projects WHERE id=?", (job["project_id"],)) \
            if job.get("project_id") else None
        self._library = self._templates(rows)
        self._job = {**{k: job.get(k) for k in ("id", "kind", "title", "project_id", "created_at", "finished_at",
                                                 "status")},
                     "params": {k: v for k, v in (job.get("params") or {}).items() if k in REPORT_PARAMS}}
        self._valid_qmean: dict[str, dict] = {}
        self._consensus: dict[int, tuple] = {}
        if is_cross(job.get("params") or {}):
            self._cross(job, rows)
        else:
            self._plain(job, rows)
        return {**self.summary, "size": self.dest.stat().st_size}

    # -------------------------------------------------------------- dati dal database, una volta sola
    def _tools_by_file(self) -> dict[str, dict]:
        """Riepiloghi di analisi strutturale, tasche ed elettrostatica più recenti per modello della pipeline (come
        `ReportService._structure_tools`: stessa sorgente e stessa impronta del file)."""
        out: dict[str, dict] = {}
        for kind, key, summarize in TOOLS:
            try:
                rows = self.db.query(
                    "SELECT id, json_extract(params, '$.source.file') AS file FROM jobs WHERE kind=? AND "
                    "status='completed' AND json_extract(params, '$.source.job_id')=? ORDER BY finished_at, rowid",
                    (kind, self.job_id))
            except sqlite3.OperationalError:  # SQLite senza funzioni JSON: parametri interi
                rows = []
                for r in self.db.query("SELECT id, params FROM jobs WHERE kind=? AND status='completed' "
                                       "ORDER BY finished_at, rowid", (kind,)):
                    source = (r.get("params") or {}).get("source") or {}
                    if source.get("job_id") == self.job_id:
                        rows.append({"id": r["id"], "file": source.get("file")})
            latest = {str(r["file"]): r["id"] for r in rows if r.get("file")}  # in ordine di fine: il più recente
            for file, tool_job in latest.items():
                try:
                    found = self.jobs.get(tool_job)
                    fingerprint = (found.get("result") or {}).get("source_sha256")
                    if fingerprint and cache.content_hash(self.jobs.file_path(self.job_id, file)) != fingerprint:
                        continue
                except (AppError, OSError):
                    continue
                out.setdefault(file, {"analysis": None, "pockets": None, "electrostatics": None})[key] = \
                    summarize(found)
        return out

    def _templates(self, rows: list[dict]) -> dict[str, dict]:
        """Descrizioni dei template della libreria usati dagli elementi (colonna `template`, id separati da
        virgole), lette una volta: nei processi di lavoro la libreria non si apre."""
        reports = ReportService(self.jobs, self.pipeline.compute, self.db)
        ids = {i.strip() for r in rows for i in str(r.get("template") or "").split(",") if i.strip()}
        for tid in sorted(ids):
            reports._library(tid)
        return dict(reports.library_cache)

    def _context(self, row: dict) -> ItemContext:
        model = str(row.get("best_model") or "")
        return ItemContext(project=self._project, qmean=self._qmean.get(model),
                           tools=self._tools.get(model) or {"analysis": None, "pockets": None, "electrostatics": None})

    # -------------------------------------------------------------- report, nell'ordine degli elementi
    def _reports(self, rows: list[dict]) -> Iterator[tuple[dict, Optional[str], Optional[str], dict]]:
        total = len(rows)
        if self.workers <= 0 or total < 2:
            service = ReportService(self.jobs, self.pipeline.compute, self.db)
            service.library_cache = dict(self._library)
            for i, row in enumerate(rows):
                self.check()
                yield (row, *_render_with(service, self._job, row, self._context(row)))
                self._tick(i + 1, total)
            return
        root = str(self.jobs.workdir(self.job_id))
        pool = ProcessPoolExecutor(max_workers=min(self.workers, total), mp_context=multiprocessing.get_context("spawn"),
                                   initializer=_init_worker, initargs=(root, self._job, self._library))
        pending: collections.deque[tuple[dict, Future]] = collections.deque()
        todo = iter(rows)
        try:
            for row in todo:
                pending.append((row, pool.submit(_render, row, self._context(row))))
                if len(pending) >= self.workers * WINDOW:
                    break
            done = 0
            while pending:
                self.check()
                row, future = pending.popleft()
                yield (row, *future.result())
                done += 1
                self._tick(done, total)
                nxt = next(todo, None)
                if nxt is not None:
                    pending.append((nxt, pool.submit(_render, nxt, self._context(nxt))))
        finally:
            pool.shutdown(wait=True, cancel_futures=True)

    def _tick(self, done: int, total: int) -> None:
        if done % PROGRESS_EVERY == 0 or done == total:
            self.progress(0.02 + 0.95 * done / max(total, 1), f"ZIP: {done} di {total} modelli")

    def _record(self, row: dict, info: dict) -> None:
        if info.get("qmean"):
            model = str(row.get("best_model") or "")
            self._valid_qmean[model] = self._qmean[model]["result"]
            self.summary["qmean"] += 1
        if info.get("consensus"):
            self._consensus[int(row["idx"])] = info["consensus"]

    # -------------------------------------------------------------- disposizioni dell'archivio
    def _plain(self, job: dict, rows: list[dict]) -> None:
        """Pipeline di sequenze: `modelli/`, `qualita/`, `report/` con un nome unico per elemento (`_zip_stem`),
        `riepilogo.csv`, `LEGGIMI.txt`, `parametri.json`."""
        from .pipeline import README_ZIP, _zip_stem

        missing: list[str] = []
        used: set[str] = set()
        with zipfile.ZipFile(self.dest, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("LEGGIMI.txt", README_ZIP)
            for row, text, error, info in self._reports(self._saved(rows)):
                stem = _zip_stem(row["name"], int(row["idx"]), used)
                self._write_model(zf, row, f"modelli/{stem}.pdb", f"qualita/{stem}.quality.json")
                if text is None:
                    missing.append(f"{stem}: {error}")
                    continue
                zf.writestr(f"report/{stem}.html", text)
                self.summary["reports"] += 1
                self._record(row, info)
            if missing:
                zf.writestr("report/non_generati.txt", "Report non generati\n\n" + "\n".join(missing) + "\n")
            zf.writestr("riepilogo.csv", self.pipeline.export_csv(self.job_id, qmean=self._valid_qmean))
            zf.writestr("parametri.json", json.dumps(job["params"], indent=2, ensure_ascii=False))
        self.summary["missing"] = len(missing)

    def _cross(self, job: dict, rows: list[dict]) -> None:
        """Pipeline M × N: `template/<template>/<sequenza>/<sequenza>__<template>` (modello, rapporto di qualità,
        report), `metriche.csv`, `migliori.csv`, `LEGGIMI.txt`, `parametri.json`."""
        from .pipeline import (BEST_FIELDS, CROSS_FIELDS, ITEM_COLUMNS, README_CROSS_ZIP, _best_rows, _metric_rows,
                               _write_csv, pair_axes, pair_names, pair_of)

        params = job.get("params") or {}
        names = pair_axes(params)
        missing: list[str] = []
        files: dict[int, str] = {}
        with zipfile.ZipFile(self.dest, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("LEGGIMI.txt", README_CROSS_ZIP)
            for row, text, error, info in self._reports(self._saved(rows)):
                s, t = pair_of(row["idx"], params)
                label = names["templates"][t] if t < len(names["templates"]) else pair_names(row["name"])[1]
                sequence = names["sequences"][s] if s < len(names["sequences"]) else pair_names(row["name"])[0]
                base = f"template/{label}/{sequence}/{row['name']}"
                self._write_model(zf, row, f"{base}.pdb", f"{base}.quality.json")
                files[int(row["idx"])] = f"{base}.pdb"
                if text is None:
                    missing.append(f"{row['name']}: {error}")
                    continue
                zf.writestr(f"{base}.html", text)
                self.summary["reports"] += 1
                self._record(row, info)
            if missing:
                zf.writestr("report_non_generati.txt", "Report non generati\n\n" + "\n".join(missing) + "\n")
            self.progress(0.98, "ZIP: tabelle delle metriche")
            every = self.db.query(f"SELECT {ITEM_COLUMNS} FROM pipeline_items WHERE job_id=? ORDER BY idx",
                                  (self.job_id,))

            def consensus_of(r: dict) -> tuple:
                idx = int(r["idx"])
                return self._consensus[idx] if idx in self._consensus else self.pipeline._consensus(self.job_id, r)

            metric_rows = _metric_rows(every, params, files, consensus_of, self._valid_qmean)
            zf.writestr("metriche.csv", _write_csv(CROSS_FIELDS, metric_rows))
            zf.writestr("migliori.csv", _write_csv(BEST_FIELDS, _best_rows(metric_rows, params)))
            zf.writestr("parametri.json", json.dumps(params, indent=2, ensure_ascii=False))
        self.summary["missing"] = len(missing)

    def _saved(self, rows: list[dict]) -> list[dict]:
        """Elementi il cui modello è nella cartella del job (gli altri non entrano nell'archivio, come prima)."""
        out = []
        for row in rows:
            try:
                self.jobs.file_path(self.job_id, row["best_model"])
            except (NotFound, AppError, TypeError):
                continue
            out.append(row)
        return out

    def _write_model(self, zf: zipfile.ZipFile, row: dict, model_name: str, quality_name: str) -> None:
        model = self.jobs.file_path(self.job_id, row["best_model"])
        zf.write(model, model_name)
        quality = Path(str(model) + ".quality.json")
        if quality.exists():
            zf.write(quality, quality_name)
        self.summary["models"] += 1


# ------------------------------------------------------------------------------ job in background
class PipelineExportService:
    """Job `pipeline_export`: ZIP di una pipeline nei file del job, con avanzamento; uno alla volta per pipeline."""

    def __init__(self, jobs: JobManager, pipeline) -> None:
        self.jobs, self.pipeline = jobs, pipeline

    def register(self) -> None:
        # pool "io": i processi dei report sono suoi, e uno ZIP lungo non occupa i posti della modellazione
        self.jobs.register(KIND, self.handle, pool="io")

    def submit(self, pipeline_id: str) -> dict:
        """Avvia lo ZIP della pipeline; se uno è già in coda o in corso restituisce quello (un secondo clic non ne
        avvia un altro)."""
        job = self.jobs.get(pipeline_id, include_result=False)
        if job["kind"] != "pipeline":
            raise AppError("Lo ZIP dei modelli si prepara solo per le pipeline.", code="export_unsupported")
        if job["status"] not in TERMINAL:
            raise Conflict("La pipeline è ancora in corso: lo ZIP si prepara quando è terminata.")
        active = self.latest(pipeline_id, active=True)
        if active is not None:
            return active
        return self.jobs.submit(KIND, {"pipeline": pipeline_id}, project_id=job.get("project_id"),
                                title=f"ZIP · {job.get('title') or pipeline_id}")

    def latest(self, pipeline_id: str, active: bool = False) -> Optional[dict]:
        """ZIP più recente della pipeline (solo in coda o in corso con `active`), oppure None."""
        where = "status IN ('queued','running','cancelling')" if active else "1=1"
        try:
            row = self.jobs.db.query_one(
                f"SELECT id FROM jobs WHERE kind=? AND {where} AND json_extract(params, '$.pipeline')=? "
                "ORDER BY created_at DESC, rowid DESC LIMIT 1", (KIND, pipeline_id))
        except sqlite3.OperationalError:  # SQLite senza funzioni JSON
            row = next((r for r in self.jobs.db.query(
                f"SELECT id, params FROM jobs WHERE kind=? AND {where} ORDER BY created_at DESC, rowid DESC", (KIND,))
                if (r.get("params") or {}).get("pipeline") == pipeline_id), None)
        return self.jobs.get(row["id"]) if row else None

    def handle(self, ctx: JobContext, p: dict) -> dict:
        pipeline_id = str(p.get("pipeline") or "")
        job = self.jobs.get(pipeline_id, include_result=False)  # NotFound se eliminata nel frattempo
        name = f"pipeline_{pipeline_id}.zip"
        workers = export_workers()
        ctx.log(f"ZIP della pipeline «{job.get('title') or pipeline_id}»: report generati da {workers} "
                f"{'processo' if workers == 1 else 'processi'}")
        out = ZipBuilder(self.pipeline, pipeline_id, ctx.path(name), workers=workers, progress=ctx.progress,
                         check=ctx.check).run()
        ctx.log(f"Archivio pronto: {out['models']} modelli, {out['reports']} report ({out['qmean']} con QMEAN), "
                f"{out['size'] / 1e9:.2f} GB" + (f"; {out['missing']} report non generati" if out["missing"] else ""))
        return {"pipeline": pipeline_id, "file": name, **out}
