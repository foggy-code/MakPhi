"""QMEAN automatico dopo la modellazione e la pipeline (opzione `qmean` dei due job).

A job completato (`JobManager.on_finish`) si accoda un job `qmean` sul modello migliore: per la modellazione quello del
risultato, per la pipeline quello di ogni elemento completato (M × N: il migliore di ogni sequenza). Si accoda dopo la
fine perché solo allora i file del modello sono nella cartella definitiva del job. Sono job `qmean` come quelli lanciati
dalla pagina Qualità: QMEAN locale uno alla volta, con le Impostazioni (locale o SWISS-MODEL, fallback, timeout) lette
al momento del calcolo; la pagina Qualità e il report li ritrovano per struttura (`/api/qmean/latest`).

Un modello che ha già un QMEAN in coda, in corso o completato non ne riceve un altro (ripresa della pipeline). Se QMEAN
non è utilizzabile (servizio locale assente senza fallback consentito, SWISS-MODEL senza consenso o email) non si accoda
nulla e il motivo va nel log del job, invece di creare un job fallito per ogni modello.
"""
from __future__ import annotations

import sqlite3
import threading
import time
from pathlib import PurePosixPath
from typing import Optional

from ..core.errors import AppError
from ..core.jobs import JobManager
from ..engines import qmean_local
from .pipeline import is_cross

KINDS = ("model", "pipeline")
IDLE_WAIT = 30.0  # secondi al massimo per la chiusura dell'esecuzione (log del job) prima di scrivere nel log
TAKEN = ("queued", "running", "completed")  # un QMEAN in questi stati vale per il modello: non se ne accoda un altro


def unavailable(settings) -> Optional[str]:
    """Motivo per cui QMEAN non può partire con queste Impostazioni, o None. Solo i controlli senza I/O (il servizio
    locale c'è se l'avviatore ne ha impostato l'indirizzo): memoria e database li controlla il job stesso."""
    mode = "local" if settings.qmean_mode == "auto" else settings.qmean_mode
    if mode == "remote":
        if not settings.qmean_remote:
            return "l'invio a SWISS-MODEL non è consentito nelle Impostazioni"
        if not settings.contact_email:
            return "per QMEAN su SWISS-MODEL manca l'email nelle Impostazioni"
        return None
    if qmean_local.base_url() or (settings.qmean_fallback_remote and settings.remote_qmean_allowed()):
        return None
    return ("il servizio QMEAN locale non è attivo (nessun UniClust30/UniRef30 configurato nell'avviatore) e il "
            "fallback su SWISS-MODEL non è consentito nelle Impostazioni")


class AutoQmean:
    def __init__(self, jobs: JobManager) -> None:
        self.jobs = jobs
        self.idle_wait = IDLE_WAIT
        jobs.on_finish(self.on_finish)

    def on_finish(self, job_id: str, status: str) -> None:
        """Ascoltatore del JobManager: torna subito, l'accodamento (anche migliaia di modelli) va in un thread."""
        if status != "completed":
            return
        try:
            row = self.jobs.db.query_one("SELECT kind, params FROM jobs WHERE id=?", (job_id,)) or {}
        except Exception:
            return
        params = row.get("params") if isinstance(row.get("params"), dict) else {}
        if row.get("kind") not in KINDS or not params.get("qmean"):
            return
        threading.Thread(target=self.run, args=(job_id,), name=f"autoqmean-{job_id[:6]}", daemon=True).start()

    def run(self, job_id: str) -> int:
        """Accoda i QMEAN del job e scrive l'esito nel suo log; restituisce quanti ne ha accodati."""
        deadline = time.monotonic() + self.idle_wait
        while self.jobs.busy(job_id) and time.monotonic() < deadline:
            time.sleep(0.2)
        try:
            submitted, note = self.submit_for(job_id)
        except Exception as exc:  # QMEAN automatico non deve mai disturbare il job concluso
            submitted, note = 0, f"QMEAN automatico non accodato: {exc}"
        if note:
            try:
                self.jobs.log_if_present(job_id, note)
            except Exception:
                pass
        return submitted

    def submit_for(self, job_id: str) -> tuple[int, str]:
        """(job accodati, riga per il log del job)."""
        job = self.jobs.get(job_id)
        reason = unavailable(self.jobs.settings_provider())
        if reason:
            return 0, f"QMEAN automatico non accodato: {reason}."
        targets = self._targets(job)
        if not targets:
            return 0, "QMEAN automatico: nessun modello da valutare."
        taken = self._taken(job_id)
        todo = [(file, name) for file, name in targets if file not in taken]
        submitted, failed = 0, None
        for file, name in todo:
            source = {"type": "job", "id": None, "job_id": job_id, "file": file}
            try:
                self.jobs.submit("qmean", {"project_id": job.get("project_id"), "source": source, "mode": None,
                                           "method": "auto", "fallback": None, "database": None, "auto": True},
                                 project_id=job.get("project_id"), title=f"QMEAN · {name} (automatico)")
                submitted += 1
            except AppError as exc:  # es. progetto eliminato nel frattempo: inutile continuare
                failed = exc.message
                break
        parts = [f"QMEAN automatico: {submitted} {'calcolo accodato' if submitted == 1 else 'calcoli accodati'}"]
        if len(targets) > len(todo):
            skipped = len(targets) - len(todo)
            parts.append(f"{skipped} {'modello aveva' if skipped == 1 else 'modelli avevano'} già QMEAN")
        if failed:
            parts.append(f"interrotto: {failed}")
        return submitted, ", ".join(parts) + "."

    def _targets(self, job: dict) -> list[tuple[str, str]]:
        """(file del modello nel job, nome per il titolo) dei modelli su cui calcolare QMEAN."""
        if job["kind"] == "model":
            best = (job.get("result") or {}).get("best_model")
            return [(best, PurePosixPath(best).name)] if best else []
        cross = is_cross(job.get("params") or {})
        rows = self.jobs.db.query("SELECT idx, name, best_model, metrics FROM pipeline_items WHERE job_id=? AND "
                                  "status='completed' AND best_model IS NOT NULL ORDER BY idx", (job["id"],))
        out = []
        for r in rows:
            metrics = r["metrics"] if isinstance(r.get("metrics"), dict) else {}
            if cross and not metrics.get("best_in_sequence"):
                continue
            out.append((r["best_model"], r["name"]))
        return out

    def _taken(self, job_id: str) -> set[str]:
        """File del job che hanno già un QMEAN in coda, in corso o completato."""
        states = ",".join("?" * len(TAKEN))
        try:
            rows = self.jobs.db.query(
                "SELECT json_extract(params, '$.source.file') AS file FROM jobs WHERE kind='qmean' AND "
                f"status IN ({states}) AND json_extract(params, '$.source.job_id')=?", (*TAKEN, job_id))
            return {str(r["file"]) for r in rows if r.get("file")}
        except sqlite3.OperationalError:  # SQLite senza funzioni JSON: parametri interi
            out = set()
            for r in self.jobs.db.query(f"SELECT params FROM jobs WHERE kind='qmean' AND status IN ({states})", TAKEN):
                source = (r.get("params") or {}).get("source") or {}
                if source.get("job_id") == job_id and source.get("file"):
                    out.add(str(source["file"]))
            return out
