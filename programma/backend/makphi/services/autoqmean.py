"""QMEAN automatico dopo la modellazione e la pipeline (opzione `qmean` dei due job).

A job completato (`JobManager.on_finish`) si accoda un job `qmean` sul modello migliore: per la modellazione quello del
risultato, per la pipeline quello di ogni elemento completato (M × N: il migliore di ogni sequenza, oppure tutte le
coppie con `qmean_scope` = "all", i migliori per primi). Si accoda dopo la
fine perché solo allora i file del modello sono nella cartella definitiva del job. Sono job `qmean` come quelli lanciati
dalla pagina Qualità: QMEAN locale uno alla volta, con le Impostazioni (locale o SWISS-MODEL, fallback, timeout) lette
al momento del calcolo; la pagina Qualità e il report li ritrovano per struttura (`/api/qmean/latest`).

Un modello che ha già un QMEAN in coda, in corso o completato non ne riceve un altro (ripresa della pipeline). Se QMEAN
non è utilizzabile (servizio locale assente senza fallback consentito, SWISS-MODEL senza consenso o email) non si accoda
nulla e il motivo va nel log del job, invece di creare un job fallito per ogni modello.

Fine dei QMEAN: quando il job non ha più QMEAN automatici da accodare, in coda o in corso (o non ne ha accodati) si
chiama `on_done`, una volta: l'avviso per email (services/notify.py), che per questi job aspetta QMEAN invece di partire
alla fine del calcolo. `status` dice alle pagine se ci sono ancora QMEAN da attendere (lo ZIP scaricato prima non li
contiene). L'attesa è in memoria: dopo un riavvio i QMEAN in coda sono interrotti (JobManager.recover) e non c'è più
nulla da attendere.
"""
from __future__ import annotations

import sqlite3
import threading
import time
from pathlib import PurePosixPath
from typing import Callable, Optional

from ..core.errors import AppError, Conflict
from ..core.jobs import JobManager
from ..engines import qmean_local
from .pipeline import is_cross

KINDS = ("model", "pipeline")
IDLE_WAIT = 30.0  # secondi al massimo per la chiusura dell'esecuzione (log del job) prima di scrivere nel log
TAKEN = ("queued", "running", "completed")  # un QMEAN in questi stati vale per il modello: non se ne accoda un altro
PENDING = ("queued", "running", "cancelling")  # QMEAN non ancora finiti


def waits(kind: Optional[str], params: dict, status: str) -> bool:
    """Il job, finito con questo stato, riceve QMEAN automatici (e l'avviso per email li aspetta)."""
    return status == "completed" and kind in KINDS and bool((params or {}).get("qmean"))


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
        # chiamato una volta quando i QMEAN automatici di un job sono tutti finiti: (job, {"counts", "note"})
        self.on_done: Optional[Callable[[str, dict], None]] = None
        self._lock = threading.Lock()
        self._open: set[str] = set()  # job di cui si aspettano i QMEAN (fine non ancora segnalata)
        self._submitting: set[str] = set()  # job i cui QMEAN si stanno ancora accodando
        self._notes: dict[str, str] = {}
        jobs.on_finish(self.on_finish)

    def on_finish(self, job_id: str, status: str) -> None:
        """Ascoltatore del JobManager: torna subito, l'accodamento (anche migliaia di modelli) va in un thread. La fine
        di un QMEAN automatico controlla se il suo job ne aspetta ancora."""
        try:
            row = self.jobs.db.query_one("SELECT kind, params FROM jobs WHERE id=?", (job_id,)) or {}
        except Exception:
            return
        params = row.get("params") if isinstance(row.get("params"), dict) else {}
        if row.get("kind") == "qmean":
            parent = (params.get("source") or {}).get("job_id")
            if params.get("auto") and parent:
                self._check_done(str(parent))
            return
        if not waits(row.get("kind"), params, status):
            return
        with self._lock:
            self._open.add(job_id)
            self._submitting.add(job_id)
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
        finally:
            with self._lock:
                self._submitting.discard(job_id)
        if note:
            try:
                self.jobs.log_if_present(job_id, note)
            except Exception:
                pass
            with self._lock:
                if job_id in self._open:
                    self._notes[job_id] = note
        self._check_done(job_id)  # nessuno accodato, o già tutti finiti mentre si accodavano gli ultimi
        return submitted

    # ------------------------------------------------------------------------------ fine dei QMEAN
    def counts(self, job_id: str) -> dict[str, int]:
        """QMEAN automatici del job per stato."""
        try:
            rows = self.jobs.db.query(
                "SELECT status, COUNT(*) AS n FROM jobs WHERE kind='qmean' AND json_extract(params, '$.auto') AND "
                "json_extract(params, '$.source.job_id')=? GROUP BY status", (job_id,))
            return {str(r["status"]): int(r["n"]) for r in rows}
        except sqlite3.OperationalError:  # SQLite senza funzioni JSON: parametri interi
            out: dict[str, int] = {}
            for r in self.jobs.db.query("SELECT status, params FROM jobs WHERE kind='qmean'"):
                p = r.get("params") or {}
                if p.get("auto") and (p.get("source") or {}).get("job_id") == job_id:
                    out[r["status"]] = out.get(r["status"], 0) + 1
            return out

    def status(self, job_id: str) -> dict:
        """Per le pagine: `requested` (il job ha chiesto QMEAN automatico), `pending` (QMEAN ancora da accodare, in
        coda o in corso: lo ZIP scaricato ora non li contiene), `counts` per stato, `total` e `done` (finiti)."""
        job = self.jobs.get(job_id, include_result=False)  # NotFound per un job inesistente
        row = self.jobs.db.query_one("SELECT params FROM jobs WHERE id=?", (job_id,)) or {}
        params = row.get("params") if isinstance(row.get("params"), dict) else {}
        requested = job["kind"] in KINDS and bool(params.get("qmean"))
        counts = self.counts(job_id) if requested else {}
        with self._lock:
            submitting = job_id in self._submitting
        total = sum(counts.values())
        active = sum(counts.get(s, 0) for s in PENDING)
        return {"requested": requested, "pending": submitting or active > 0, "counts": counts, "total": total,
                "done": total - active}

    def _check_done(self, job_id: str) -> None:
        """Se il job aspettava i suoi QMEAN e non ce ne sono più da accodare, in coda o in corso: `on_done`, una volta."""
        with self._lock:
            if job_id not in self._open or job_id in self._submitting:
                return
            counts = self.counts(job_id)
            if any(counts.get(s) for s in PENDING):
                return
            self._open.discard(job_id)
            note = self._notes.pop(job_id, None)
        if self.on_done is not None:
            try:
                self.on_done(job_id, {"counts": counts, "note": note})
            except Exception:  # l'avviso non deve mai disturbare QMEAN
                pass

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

    def retry_failed(self, job_id: str) -> dict:
        """Rimette in coda i QMEAN automatici falliti o annullati del job: gli stessi job, con i loro parametri (restano
        automatici e i conteggi di `status` tornano giusti), nell'ordine in cui erano stati accodati. Un modello che ha
        già un QMEAN in coda, in corso o completato non ne riprende un altro, e di più falliti sullo stesso modello si
        riprende il primo. Serve dopo un guasto del servizio (il 25 ottobre 2026 un worker bloccato ne ha fatti
        fallire 107.918). Il lavoro va in un thread, perché con centinaia di migliaia di QMEAN richiede minuti; alla
        fine dei QMEAN l'avviso per email parte di nuovo. Restituisce `requeued` (da rimettere in coda) e `skipped`."""
        if not self.status(job_id)["requested"]:  # NotFound per un job inesistente
            raise AppError("Questo job non ha chiesto QMEAN automatico.", code="qmean_auto_not_requested")
        with self._lock:
            if job_id in self._submitting:
                raise Conflict("I QMEAN automatici di questo job si stanno già accodando: riprova fra qualche minuto.")
        taken = self._taken(job_id)
        ids, skipped = [], 0
        for row in self._failed(job_id):
            if row["file"] in taken:
                skipped += 1
                continue
            taken.add(row["file"])
            ids.append(row["id"])
        if not ids:
            return {"requeued": 0, "skipped": skipped}
        with self._lock:
            self._open.add(job_id)
            self._submitting.add(job_id)
        threading.Thread(target=self._requeue, args=(job_id, ids), name=f"autoqmean-retry-{job_id[:6]}",
                         daemon=True).start()
        return {"requeued": len(ids), "skipped": skipped}

    def _requeue(self, job_id: str, ids: list[str]) -> None:
        done = 0
        try:
            for qmean_id in ids:
                try:
                    self.jobs.requeue(qmean_id, note="QMEAN automatico rilanciato", stage="In coda (rilanciato)")
                    done += 1
                except AppError:  # ripreso, annullato o eliminato nel frattempo
                    continue
        finally:
            with self._lock:
                self._submitting.discard(job_id)
            try:
                self.jobs.log_if_present(job_id, f"QMEAN automatico: {done} "
                                                 f"{'calcolo fallito rimesso' if done == 1 else 'calcoli falliti rimessi'}"
                                                 " in coda.")
                self._check_done(job_id)
            except Exception:  # database chiuso (arresto): il rilancio non deve far rumore
                pass

    def _failed(self, job_id: str) -> list[dict]:
        """QMEAN automatici falliti o annullati del job, nell'ordine in cui erano stati accodati: [{id, file}]."""
        try:
            rows = self.jobs.db.query(
                "SELECT id, json_extract(params, '$.source.file') AS file FROM jobs WHERE kind='qmean' AND "
                "status IN ('failed', 'cancelled') AND json_extract(params, '$.auto') AND "
                "json_extract(params, '$.source.job_id')=? ORDER BY created_at, rowid", (job_id,))
            return [{"id": r["id"], "file": str(r["file"])} for r in rows if r.get("file")]
        except sqlite3.OperationalError:  # SQLite senza funzioni JSON: parametri interi
            out = []
            for r in self.jobs.db.query("SELECT id, params FROM jobs WHERE kind='qmean' AND status IN "
                                        "('failed', 'cancelled') ORDER BY created_at, rowid"):
                p = r.get("params") or {}
                source = p.get("source") or {}
                if p.get("auto") and source.get("job_id") == job_id and source.get("file"):
                    out.append({"id": r["id"], "file": str(source["file"])})
            return out

    def _targets(self, job: dict) -> list[tuple[str, str]]:
        """(file del modello nel job, nome per il titolo) dei modelli su cui calcolare QMEAN."""
        if job["kind"] == "model":
            best = (job.get("result") or {}).get("best_model")
            return [(best, PurePosixPath(best).name)] if best else []
        params = job.get("params") or {}
        cross = is_cross(params)
        every = params.get("qmean_scope") == "all"
        rows = self.jobs.db.query("SELECT idx, name, best_model, metrics FROM pipeline_items WHERE job_id=? AND "
                                  "status='completed' AND best_model IS NOT NULL ORDER BY idx", (job["id"],))
        best, others = [], []
        for r in rows:
            metrics = r["metrics"] if isinstance(r.get("metrics"), dict) else {}
            if not cross or metrics.get("best_in_sequence"):
                best.append((r["best_model"], r["name"]))
            elif every:  # M × N, tutte le coppie: dopo i migliori di ogni sequenza, che così arrivano prima
                others.append((r["best_model"], r["name"]))
        return best + others

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
