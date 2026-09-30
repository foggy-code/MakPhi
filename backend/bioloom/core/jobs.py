"""Gestore unico dei job: persistenza su SQLite, esecuzione in thread pool, log, cancellazione.

Ogni calcolo (ricerca, allineamento, modellazione, qualità, pipeline) è un job con:
  - una riga nella tabella `jobs` (stato, progresso, parametri, risultato);
  - una cartella definitiva `data/jobs/<id>/` con `params.json`, `job.log`, `result.json` e i risultati;
  - una cartella di lavoro nello scratch (`<scratch>/jobs/<id>/`), veloce e locale, dove girano i
    programmi esterni: a fine job i risultati vengono spostati nella cartella definitiva (senza i file
    intermedi di MODELLER, salvo diversa impostazione) e lo scratch viene svuotato;
  - un `JobContext` passato all'handler per log, progresso, sottoprocessi e cancellazione.

Un job terminato con errore o annullato può essere rimesso in coda (`requeue`): la pipeline lo usa
per riprendere solo gli elementi non conclusi, conservando log e risultati già salvati (e, su richiesta
esplicita, anche da completata, per riprovare gli elementi falliti). Ripresa, avvio ed eliminazione sono
mutuamente esclusivi: un job rimesso in coda non può essere eliminato mentre riparte.
"""

from __future__ import annotations

import collections
import fnmatch
import os
import shutil
import signal
import subprocess
import sys
import threading
import time
import traceback
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

from .db import Database, encode_json, now_iso
from .errors import AppError, Conflict, NotFound
from .storage import atomic_write_json, remove_tree, safe_join, validate_identifier

TERMINAL = frozenset({"completed", "failed", "cancelled"})
ACTIVE = frozenset({"queued", "running", "cancelling"})
# stati da cui un job si può rimettere in coda (ripresa); un job completato no, salvo richiesta esplicita
# del chiamante (`requeue(statuses=...)`: la pipeline completata con elementi falliti)
REQUEUABLE = frozenset({"failed", "cancelled"})
LOG_NAME = "job.log"
LOG_FLUSH_SECONDS = 0.5
# attesa massima, per una ripresa o un'eliminazione, della chiusura dell'esecuzione precedente (spostamento
# dei file, scratch) o della preparazione di una ripresa
REQUEUE_WAIT_SECONDS = 10.0

# File intermedi di MODELLER (restrizioni, schedule, traiettorie, violazioni, modelli iniziali) e copie
# temporanee delle analisi: non vengono salvati tra i risultati di un job riuscito.
INTERMEDIATE_PATTERNS = ("*.rsr", "*.lrsr", "*.sch", "*.ini", "*.D[0-9]*", "*.V[0-9]*", "*.DL[0-9]*",
                         "*.IL[0-9]*", "dope_input.pdb")

_extra_env: dict[str, str] = {}


def set_subprocess_env(name: str, value: Optional[str]) -> None:
    """Variabili aggiunte all'ambiente di tutti i sottoprocessi (es. la chiave di licenza di MODELLER)."""
    if value:
        _extra_env[name] = value
    else:
        _extra_env.pop(name, None)


def default_env() -> dict:
    env = os.environ.copy()
    env.update(_extra_env)
    env["PYTHONUNBUFFERED"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    return env


def is_intermediate(name: str) -> bool:
    return any(fnmatch.fnmatchcase(name, pattern) for pattern in INTERMEDIATE_PATTERNS)


def requeue_error(job: dict, statuses: frozenset[str] = REQUEUABLE) -> Optional[AppError]:
    """Perché il job non si può rimettere in coda (None se si può, cioè se il suo stato è fra `statuses`)."""
    status = job["status"]
    if status in statuses:
        return None
    if status in ACTIVE:
        return Conflict("Il job è ancora in coda o in esecuzione: non c'è nulla da riprendere.")
    if status == "completed":
        return Conflict("Il job è già completato: si possono riprendere solo job falliti o annullati.")
    return Conflict(f"Un job nello stato '{status}' non si può riprendere.")


def _kill(proc: subprocess.Popen) -> None:
    """Termina il processo e i suoi figli (su POSIX ogni processo esterno ha un proprio gruppo)."""
    try:
        if os.name == "posix":
            os.killpg(proc.pid, signal.SIGKILL)
        else:
            proc.kill()
    except (OSError, ProcessLookupError):
        pass


class JobCancelled(Exception):
    """Job annullato. Un messaggio facoltativo sostituisce "Annullato dall'utente." come motivo."""


@dataclass
class ProcessResult:
    returncode: int
    output: str


Handler = Callable[["JobContext", dict], dict]


@dataclass
class _Registration:
    fn: Handler
    pool: str


class JobContext:
    """Interfaccia offerta agli handler: log, progresso, sottoprocessi, cancellazione."""

    def __init__(self, manager: "JobManager", job_id: str, workdir: Path, params: dict,
                 cancel_event: threading.Event, prefix: str = "",
                 on_progress: Optional[Callable[[Optional[float], Optional[str]], None]] = None) -> None:
        self._manager = manager
        self.job_id = job_id
        self.workdir = workdir
        self.params = params
        self._cancel = cancel_event
        self._prefix = prefix
        self._on_progress = on_progress

    @property
    def settings(self):
        return self._manager.settings_provider()

    @property
    def cancelled(self) -> bool:
        return self._cancel.is_set()

    @property
    def tmp_dir(self) -> Path:
        """Cartella temporanea del job (nello scratch), eliminata a fine job."""
        path = self._manager.live_dir(self.job_id) / ".tmp"
        path.mkdir(parents=True, exist_ok=True)
        return path

    def check(self) -> None:
        if self._cancel.is_set():
            raise JobCancelled()

    def log(self, message: str) -> None:
        stamp = datetime.now().strftime("%H:%M:%S")
        for line in str(message).splitlines() or [""]:
            self._manager._append_log(self.job_id, f"[{stamp}] {self._prefix}{line}")

    def progress(self, fraction: Optional[float] = None, stage: Optional[str] = None) -> None:
        if self._on_progress is not None:
            self._on_progress(fraction, stage)
        else:
            self._manager._set_progress(self.job_id, fraction, stage)

    def sub(self, name: str, workdir: Path,
            on_progress: Optional[Callable[[Optional[float], Optional[str]], None]] = None) -> "JobContext":
        """Contesto figlio (es. un elemento di pipeline): condivide cancellazione e log del job padre."""
        workdir.mkdir(parents=True, exist_ok=True)
        return JobContext(self._manager, self.job_id, workdir, self.params, self._cancel,
                          prefix=f"{self._prefix}[{name}] ", on_progress=on_progress or (lambda f, s: None))

    def path(self, *parts: str) -> Path:
        return safe_join(self.workdir, *parts)

    def commit(self, success: bool = True) -> None:
        """Sposta subito i file di questa cartella di lavoro tra i risultati definitivi del job.

        Usato dalla pipeline a ogni elemento concluso: un batch lungo salva il lavoro man mano e lo
        scratch resta piccolo.
        """
        self._manager._commit(self.job_id, self.workdir, success=success)

    def run(self, cmd: list[str], *, cwd: Optional[Path] = None, env: Optional[dict] = None,
            timeout: Optional[float] = None, on_line: Optional[Callable[[str], None]] = None,
            log_output: bool = True) -> ProcessResult:
        """Esegue un processo esterno registrandone l'output nel log; rispetta cancellazione e timeout.

        Senza `timeout` esplicito vale il timeout dei job delle Impostazioni.
        """
        self.check()
        if timeout is None:
            timeout = float(self.settings.job_timeout_minutes) * 60
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0
        proc = subprocess.Popen(
            cmd, cwd=str(cwd or self.workdir), env=env if env is not None else default_env(),
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, text=True,
            encoding="utf-8", errors="replace", bufsize=1, creationflags=creationflags,
            start_new_session=os.name == "posix",
        )
        self._manager._register_process(self.job_id, proc)
        timed_out = threading.Event()

        def _kill_on_timeout() -> None:
            timed_out.set()
            _kill(proc)

        timer = threading.Timer(timeout, _kill_on_timeout) if timeout else None
        if timer:
            timer.daemon = True
            timer.start()
        tail: collections.deque[str] = collections.deque(maxlen=20000)
        try:
            assert proc.stdout is not None
            for line in proc.stdout:
                tail.append(line)
                if log_output:
                    self._manager._append_log(self.job_id, f"{self._prefix}{line.rstrip()}")
                if on_line is not None:
                    on_line(line)
            proc.wait()
        finally:
            if timer:
                timer.cancel()
            self._manager._unregister_process(self.job_id, proc)
        if self._cancel.is_set():
            raise JobCancelled()
        if timed_out.is_set():
            raise AppError(f"Processo interrotto dopo il timeout di {int(timeout or 0)} s.", code="timeout")
        return ProcessResult(proc.returncode, "".join(tail))


class JobManager:
    def __init__(self, db: Database, jobs_dir: Path, workers: int, settings_provider: Callable,
                 scratch_dir: Optional[Path] = None) -> None:
        self.db = db
        self.jobs_dir = jobs_dir
        # senza scratch i job lavorano direttamente nella cartella definitiva
        self.scratch_jobs = (scratch_dir / "jobs") if scratch_dir is not None else None
        self.settings_provider = settings_provider
        self._handlers: dict[str, _Registration] = {}
        self._pools = {
            "cpu": ThreadPoolExecutor(max_workers=max(1, workers), thread_name_prefix="bioloom-cpu"),
            "io": ThreadPoolExecutor(max_workers=8, thread_name_prefix="bioloom-io"),
        }
        self._lock = threading.RLock()
        self._cancel_events: dict[str, threading.Event] = {}
        self._processes: dict[str, set[subprocess.Popen]] = {}
        # Job con un'esecuzione non ancora chiusa (lo stato può essere già terminale mentre si spostano i
        # file e si svuota lo scratch) e job con una ripresa in preparazione: nessuna ripresa li tocca.
        self._running: set[str] = set()
        self._preparing: set[str] = set()
        self._idle = threading.Condition(self._lock)
        self._log_handles: dict[str, object] = {}
        self._log_dirty: dict[str, float] = {}
        self._log_lock = threading.Lock()
        self._stop = threading.Event()
        jobs_dir.mkdir(parents=True, exist_ok=True)
        if self.scratch_jobs is not None:
            self.scratch_jobs.mkdir(parents=True, exist_ok=True)
        self._flusher = threading.Thread(target=self._flush_loop, name="bioloom-log-flush", daemon=True)
        self._flusher.start()

    # ------------------------------------------------------------------ registrazione e avvio
    def register(self, kind: str, fn: Handler, pool: str = "cpu") -> None:
        self._handlers[kind] = _Registration(fn, pool)

    def recover(self) -> int:
        """Marca come falliti i job rimasti attivi da un'esecuzione precedente e svuota lo scratch.

        Gli elementi di pipeline non conclusi diventano falliti ("interrotti"): la pipeline resta
        riprendibile (`requeue`) e gli elementi già salvati non vanno rifatti.
        """
        now = now_iso()
        count = self.db.execute(
            "UPDATE jobs SET status='failed', stage='Interrotto', error='Interrotto dal riavvio del server.', "
            "finished_at=?, updated_at=? WHERE status IN ('queued','running','cancelling')", (now, now))
        self.db.execute(
            "UPDATE pipeline_items SET status='failed', stage='Interrotto', error='Interrotto dal riavvio del server.' "
            "WHERE status NOT IN ('completed','failed','cancelled','filtered')")
        if self.scratch_jobs is not None and self.scratch_jobs.exists():
            for leftover in self.scratch_jobs.iterdir():
                remove_tree(leftover)
        return count

    def submit(self, kind: str, params: dict, *, project_id: Optional[str] = None, title: str = "",
               prepare: Optional[Callable[[str, Path], None]] = None) -> dict:
        if kind not in self._handlers:
            raise AppError(f"Tipo di job sconosciuto: {kind}")
        job_id = uuid.uuid4().hex[:16]
        workdir = self.jobs_dir / job_id
        workdir.mkdir(parents=True, exist_ok=True)
        atomic_write_json(workdir / "params.json", params)
        now = now_iso()
        self.db.execute(
            "INSERT INTO jobs(id, kind, title, project_id, status, progress, stage, params, created_at, updated_at)"
            " VALUES (?,?,?,?, 'queued', 0, 'In coda', ?, ?, ?)",
            (job_id, kind, title[:200], project_id, encode_json(params), now, now))
        if prepare is not None:
            prepare(job_id, workdir)
        with self._lock:
            self._cancel_events[job_id] = threading.Event()
        self._pools[self._handlers[kind].pool].submit(self._execute, job_id)
        return self.get(job_id)

    def requeue(self, job_id: str, *, prepare: Optional[Callable[[str, Path], None]] = None,
                note: str = "", stage: str = "In coda (ripresa)", statuses: frozenset[str] = REQUEUABLE,
                check: Optional[Callable[[dict], object]] = None) -> dict:
        """Rimette in coda un job fallito o annullato; log, parametri e file salvati restano.

        `statuses` sono gli stati da cui si riprende: fallito e annullato, oppure anche completato se il
        chiamante lo chiede esplicitamente (la pipeline con elementi falliti); mai uno stato attivo.
        `check(job)` è un controllo del chiamante eseguito sotto il lock, con il job riletto, subito prima
        del passaggio a 'queued': se solleva, la ripresa è rifiutata e nulla cambia.

        Il passaggio a 'queued' è un solo UPDATE condizionato allo stato: fra due richieste concorrenti
        una sola lo ottiene, l'altra riceve Conflict. `prepare(job_id, cartella definitiva)` gira dopo quel
        passaggio e prima dell'esecuzione (la pipeline vi azzera gli elementi da rifare); se fallisce il
        job torna 'failed' con il motivo e l'errore viene rilanciato.
        """
        if not statuses or not statuses <= TERMINAL:
            raise ValueError("Si possono rimettere in coda solo job terminati.")
        with self._idle:
            job = self.get(job_id, include_result=False)
            if job["kind"] not in self._handlers:
                raise AppError(f"Tipo di job sconosciuto: {job['kind']}")
            blocked = requeue_error(job, statuses)
            if blocked is not None:
                raise blocked
            # Lo stato finale è scritto prima che l'esecuzione chiuda log, file e scratch (e una ripresa
            # annullata mentre si preparava può non aver finito di azzerare): si attende che abbiano finito.
            if not self._idle.wait_for(lambda: job_id not in self._running and job_id not in self._preparing,
                                       timeout=REQUEUE_WAIT_SECONDS):
                raise Conflict("Il job sta ancora terminando: riprova tra qualche secondo.")
            # durante l'attesa il lock è rilasciato: un'altra ripresa può essere passata (o il job eliminato)
            current = self.get(job_id, include_result=False)
            blocked = requeue_error(current, statuses)
            if blocked is not None:
                raise blocked
            if check is not None:
                check(current)
            allowed = sorted(statuses)
            # da completato il progresso riparte (la barra piena in coda sarebbe fuorviante): lo aggiorna l'handler
            changed = self.db.execute(
                "UPDATE jobs SET status='queued', stage=?, error=NULL, result=NULL, finished_at=NULL, updated_at=?, "
                "progress=CASE WHEN status='completed' THEN 0 ELSE progress END "
                f"WHERE id=? AND status IN ({','.join('?' * len(allowed))})",
                (stage[:200], now_iso(), job_id, *allowed))
            if changed != 1:
                raise requeue_error(self.get(job_id, include_result=False), statuses) \
                    or Conflict("Ripresa non riuscita.")
            self._cancel_events[job_id] = threading.Event()
            self._preparing.add(job_id)
        try:
            self._log_now(job_id, "Ripresa del job" + (f": {note}" if note else ""))
            if prepare is not None:
                prepare(job_id, self.workdir(job_id))
        except BaseException as exc:
            reason = exc.message if isinstance(exc, AppError) else f"{type(exc).__name__}: {exc}"
            self._finish(job_id, "failed", error=f"Ripresa non riuscita: {reason}", only_from=("queued",))
            try:
                self._log_now(job_id, f"Ripresa non riuscita: {reason}")
            except OSError:
                pass
            raise
        finally:
            with self._idle:
                self._preparing.discard(job_id)
                self._idle.notify_all()
        # se nel frattempo è stato annullato, l'esecuzione trova lo stato cambiato e non parte
        self._pools[self._handlers[job["kind"]].pool].submit(self._execute, job_id)
        return self.get(job_id, include_result=False)

    # ------------------------------------------------------------------ interrogazione
    def get(self, job_id: str, include_result: bool = True) -> dict:
        validate_identifier(job_id, "job_id")
        cols = "*" if include_result else "id, kind, title, project_id, status, progress, stage, error, " \
                                          "created_at, started_at, finished_at, updated_at"
        job = self.db.query_one(f"SELECT {cols} FROM jobs WHERE id=?", (job_id,))
        if not job:
            raise NotFound(f"Job {job_id} non trovato.")
        return job

    def list(self, *, project_id: Optional[str] = None, kind: Optional[str] = None,
             statuses: Optional[list[str]] = None, limit: int = 100, offset: int = 0) -> list[dict]:
        where, args = [], []
        if project_id:
            where.append("project_id=?")
            args.append(project_id)
        if kind:
            where.append("kind=?")
            args.append(kind)
        if statuses:
            where.append(f"status IN ({','.join('?' * len(statuses))})")
            args.extend(statuses)
        clause = f"WHERE {' AND '.join(where)}" if where else ""
        return self.db.query(
            "SELECT id, kind, title, project_id, status, progress, stage, error, created_at, started_at, "
            f"finished_at, updated_at FROM jobs {clause} ORDER BY created_at DESC, rowid DESC LIMIT ? OFFSET ?",
            (*args, max(1, min(limit, 500)), max(0, offset)))

    def count_active(self) -> int:
        return int(self.db.scalar("SELECT COUNT(*) FROM jobs WHERE status IN ('queued','running','cancelling')"))

    def workdir(self, job_id: str) -> Path:
        """Cartella definitiva del job (nella cartella dati)."""
        validate_identifier(job_id, "job_id")
        return safe_join(self.jobs_dir, job_id)

    def live_dir(self, job_id: str) -> Path:
        """Cartella di lavoro durante l'esecuzione (nello scratch, se configurato)."""
        validate_identifier(job_id, "job_id")
        if self.scratch_jobs is None:
            return self.workdir(job_id)
        return safe_join(self.scratch_jobs, job_id)

    def read_log(self, job_id: str, offset: int = 0, limit: int = 256 * 1024) -> dict:
        path = self.workdir(job_id) / LOG_NAME
        if not path.exists():
            return {"text": "", "offset": 0, "size": 0}
        size = path.stat().st_size
        start = offset if 0 <= offset <= size else max(0, size - limit)
        with open(path, "rb") as fh:
            fh.seek(start)
            data = fh.read(limit)
        return {"text": data.decode("utf-8", errors="replace"), "offset": start + len(data), "size": size}

    def list_files(self, job_id: str) -> list[dict]:
        """File del job: quelli definitivi più, mentre il job è attivo, quelli ancora nello scratch.

        Il job può spostare o cancellare file mentre li si elenca: quelli spariti vengono ignorati.
        """
        found: dict[str, int] = {}
        roots = [self.workdir(job_id)]
        live = self.live_dir(job_id)
        if live != roots[0]:
            roots.append(live)
        for root in roots:
            for dirpath, dirnames, filenames in os.walk(root, onerror=lambda _err: None):
                dirnames[:] = [d for d in dirnames if not d.startswith(".")]
                for name in filenames:
                    if name.startswith("."):
                        continue
                    full = os.path.join(dirpath, name)
                    try:
                        size = os.stat(full).st_size
                    except OSError:
                        continue
                    rel = Path(full).relative_to(root).as_posix()
                    found.setdefault(rel, size)
        return [{"path": p, "size": s} for p, s in sorted(found.items())]

    def file_path(self, job_id: str, relpath: str) -> Path:
        parts = relpath.replace("\\", "/").split("/")
        for root in (self.workdir(job_id), self.live_dir(job_id)):
            path = safe_join(root, *parts)
            if path.is_file():
                return path
        raise NotFound(f"File {relpath} non trovato nel job {job_id}.")

    # ------------------------------------------------------------------ controllo
    def cancel(self, job_id: str) -> dict:
        job = self.get(job_id, include_result=False)
        if job["status"] in TERMINAL:
            return job
        with self._lock:
            event = self._cancel_events.get(job_id)
            procs = list(self._processes.get(job_id, ()))
        if event is not None:
            event.set()
        for proc in procs:
            _kill(proc)
        # un job in coda si chiude subito; se nel frattempo è partito, lo chiuderà la sua esecuzione
        if not (job["status"] == "queued"
                and self._finish(job_id, "cancelled", error="Annullato dall'utente.", only_from=("queued",))):
            self.db.execute("UPDATE jobs SET status='cancelling', stage='Annullamento in corso', updated_at=? "
                            "WHERE id=? AND status='running'", (now_iso(), job_id))
        return self.get(job_id, include_result=False)

    def delete(self, job_id: str) -> None:
        """Elimina un job terminato con i suoi file.

        Mutuamente esclusivo con `requeue` ed `_execute`: un job terminato può tornare in coda (ripresa),
        quindi si attende che l'esecuzione precedente o la preparazione di una ripresa abbiano finito, e la
        riga si cancella solo se il job è ancora terminato (DELETE condizionato allo stato). Dopo quel DELETE
        nessuna esecuzione può ripartire, e i file si rimuovono fuori dal lock.
        """
        with self._idle:
            job = self.get(job_id, include_result=False)
            if job["status"] in ACTIVE:
                raise Conflict("Il job è ancora attivo: annullalo prima di eliminarlo.")
            if not self._idle.wait_for(lambda: job_id not in self._running and job_id not in self._preparing,
                                       timeout=REQUEUE_WAIT_SECONDS):
                raise Conflict("Il job sta ancora terminando o viene ripreso: riprova tra qualche secondo.")
            allowed = sorted(TERMINAL)
            if self.db.execute(f"DELETE FROM jobs WHERE id=? AND status IN ({','.join('?' * len(allowed))})",
                               (job_id, *allowed)) != 1:
                self.get(job_id, include_result=False)  # NotFound se eliminato nel frattempo
                raise Conflict("Il job è stato ripreso nel frattempo: annullalo prima di eliminarlo.")
            self._cancel_events.pop(job_id, None)
        self._close_log(job_id)
        remove_tree(self.workdir(job_id))
        remove_tree(self.live_dir(job_id))

    def shutdown(self) -> None:
        with self._lock:
            events = list(self._cancel_events.values())
            procs = [p for group in self._processes.values() for p in group]
        for event in events:
            event.set()
        for proc in procs:
            _kill(proc)
        for pool in self._pools.values():
            pool.shutdown(wait=False, cancel_futures=True)
        self._stop.set()
        self._flush_all()

    # ------------------------------------------------------------------ esecuzione
    def _execute(self, job_id: str) -> None:
        job = self.db.query_one("SELECT kind, status, params FROM jobs WHERE id=?", (job_id,))
        if not job or job["status"] != "queued":
            return
        # Avvio sotto il lock, atomico rispetto a `requeue`: un job annullato in coda e poi ripreso può avere
        # nel pool anche la vecchia esecuzione in attesa, e deve partirne una sola, a ripresa preparata.
        with self._lock:
            event = self._cancel_events.setdefault(job_id, threading.Event())
            if job_id in self._preparing:
                return  # la ripresa sta azzerando gli elementi: a preparazione finita sottomette la sua
            if event.is_set():
                self._finish(job_id, "cancelled", error="Annullato dall'utente.", only_from=("queued",))
                return
            now = now_iso()
            if self.db.execute("UPDATE jobs SET status='running', started_at=?, updated_at=?, stage='Avvio' "
                               "WHERE id=? AND status='queued'", (now, now, job_id)) != 1:
                return
            self._running.add(job_id)
        live = self.live_dir(job_id)
        live.mkdir(parents=True, exist_ok=True)
        ctx = JobContext(self, job_id, live, job["params"] or {}, event)
        registration = self._handlers[job["kind"]]
        try:
            ctx.log(f"Job {job['kind']} avviato")
            result = registration.fn(ctx, job["params"] or {})
            if event.is_set():
                raise JobCancelled()
            self._commit(job_id, live, success=True)
            atomic_write_json(self.workdir(job_id) / "result.json", result)
            ctx.log("Job completato")
            self._finish(job_id, "completed", result=result)
        except JobCancelled as exc:
            reason = str(exc)
            self._end_run(ctx, live, "cancelled", reason or "Annullato dall'utente.",
                          f"Job annullato: {reason}" if reason else "Job annullato dall'utente")
        except AppError as exc:
            self._end_run(ctx, live, "failed", exc.message, f"ERRORE: {exc.message}")
        except Exception as exc:  # errore inatteso (anche il salvataggio non riuscito): traccia completa nel log
            self._end_run(ctx, live, "failed", f"{type(exc).__name__}: {exc}",
                          "ERRORE INATTESO:\n" + traceback.format_exc())
        finally:
            self._close_log(job_id)
            if live != self.workdir(job_id):
                remove_tree(live)
            with self._idle:
                if self._cancel_events.get(job_id) is event:
                    self._cancel_events.pop(job_id, None)
                self._processes.pop(job_id, None)
                self._running.discard(job_id)
                self._idle.notify_all()  # una ripresa o un'eliminazione in attesa può procedere

    def _end_run(self, ctx: JobContext, live: Path, status: str, error: str, message: str) -> None:
        """Chiude un'esecuzione annullata o fallita: salva i file, scrive il motivo nel log e lo stato finale.

        Lo stato finale si scrive comunque, anche se salvataggio o log non riescono (es. disco dati pieno):
        altrimenti il job resterebbe 'running' fino al riavvio, senza poterlo riprendere né eliminare.
        """
        try:
            self._commit(ctx.job_id, live, success=status == "cancelled")
        except OSError as exc:
            note = f"salvataggio dei file non riuscito: {exc}"
            message = f"{message}\nERRORE: {note}"
            error = f"{error} ({note})"
        try:
            ctx.log(message)
        except OSError:
            pass
        self._finish(ctx.job_id, status, error=error)

    def _commit(self, job_id: str, source: Path, *, success: bool) -> None:
        """Sposta i file da `source` (nello scratch) alla cartella definitiva del job.

        Sui job riusciti o annullati i file intermedi vengono scartati, salvo l'impostazione
        "conserva file intermedi"; sui job falliti si conserva tutto per la diagnosi.
        """
        try:
            keep_all = (not success) or bool(getattr(self.settings_provider(), "keep_intermediates", False))
        except Exception:  # impostazioni non leggibili: comportamento predefinito
            keep_all = not success
        live_root = self.live_dir(job_id)
        final_root = self.workdir(job_id)
        if not source.exists():
            return
        if live_root == final_root:  # nessuno scratch: si eliminano solo gli intermedi
            if not keep_all:
                for path in list(source.rglob("*")):
                    if path.is_file() and is_intermediate(path.name):
                        path.unlink(missing_ok=True)
            return
        for path in sorted(source.rglob("*")):
            if not path.is_file():
                continue
            rel = path.relative_to(live_root)
            if any(part.startswith(".") for part in rel.parts):
                continue  # cartelle temporanee (.tmp) e file di scrittura atomica
            if not keep_all and is_intermediate(path.name):
                continue
            dest = final_root.joinpath(*rel.parts)
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(path), str(dest))
        if source != live_root:
            remove_tree(source)

    def _finish(self, job_id: str, status: str, *, result: Optional[dict] = None, error: Optional[str] = None,
                only_from: Optional[tuple[str, ...]] = None) -> bool:
        """Scrive lo stato finale; con `only_from` solo se lo stato attuale è fra quelli indicati."""
        now = now_iso()
        stage = {"completed": "Completato", "failed": "Fallito", "cancelled": "Annullato"}[status]
        guard, extra = "", ()
        if only_from:
            guard = f" AND status IN ({','.join('?' * len(only_from))})"
            extra = tuple(only_from)
        return self.db.execute(
            "UPDATE jobs SET status=?, result=?, error=?, stage=?, progress=CASE WHEN ?='completed' THEN 1 "
            f"ELSE progress END, finished_at=?, updated_at=? WHERE id=?{guard}",
            (status, encode_json(result), error, stage, status, now, now, job_id, *extra)) == 1

    def _set_progress(self, job_id: str, fraction: Optional[float], stage: Optional[str]) -> None:
        sets, args = ["updated_at=?"], [now_iso()]
        if fraction is not None:
            sets.append("progress=?")
            args.append(max(0.0, min(1.0, float(fraction))))
        if stage is not None:
            sets.append("stage=?")
            args.append(stage[:200])
        self.db.execute(f"UPDATE jobs SET {', '.join(sets)} WHERE id=? AND status='running'", (*args, job_id))

    # ------------------------------------------------------------------ log
    def _append_log(self, job_id: str, line: str) -> None:
        """Scrive una riga di log; il flush su disco avviene al massimo ogni LOG_FLUSH_SECONDS."""
        with self._log_lock:
            fh = self._log_handles.get(job_id)
            if fh is None:
                path = self.jobs_dir / job_id / LOG_NAME
                path.parent.mkdir(parents=True, exist_ok=True)
                fh = open(path, "a", encoding="utf-8", newline="\n")
                self._log_handles[job_id] = fh
            fh.write(line + "\n")
            last = self._log_dirty.get(job_id)
            now = time.monotonic()
            if last is None:
                self._log_dirty[job_id] = now
            elif now - last >= LOG_FLUSH_SECONDS:
                fh.flush()
                self._log_dirty.pop(job_id, None)

    def _log_now(self, job_id: str, message: str) -> None:
        """Riga di log con orario scritta subito su disco, fuori da un'esecuzione (es. la ripresa).

        Il file si chiude: la prossima esecuzione lo riapre in aggiunta, così il log continua.
        """
        self._append_log(job_id, f"[{datetime.now().strftime('%H:%M:%S')}] {message}")
        self._close_log(job_id)

    def _flush_all(self) -> None:
        with self._log_lock:
            for job_id in list(self._log_dirty):
                fh = self._log_handles.get(job_id)
                if fh is not None:
                    try:
                        fh.flush()
                    except (OSError, ValueError):
                        pass
                self._log_dirty.pop(job_id, None)

    def _flush_loop(self) -> None:
        while not self._stop.wait(LOG_FLUSH_SECONDS):
            self._flush_all()

    def _close_log(self, job_id: str) -> None:
        with self._log_lock:
            fh = self._log_handles.pop(job_id, None)
            self._log_dirty.pop(job_id, None)
            if fh is not None:
                try:
                    fh.close()
                except OSError:  # disco pieno: le ultime righe si perdono, ma l'esecuzione si chiude comunque
                    pass

    # ------------------------------------------------------------------ processi
    def _register_process(self, job_id: str, proc: subprocess.Popen) -> None:
        with self._lock:
            self._processes.setdefault(job_id, set()).add(proc)
            cancelled = self._cancel_events.get(job_id)
        if cancelled is not None and cancelled.is_set():
            _kill(proc)

    def _unregister_process(self, job_id: str, proc: subprocess.Popen) -> None:
        with self._lock:
            self._processes.get(job_id, set()).discard(proc)
