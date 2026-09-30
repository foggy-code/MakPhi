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

Ogni cambiamento (stato, progresso, righe di log scritte su disco, esecuzione chiusa, eliminazione) viene
pubblicato su `events` (core/events.py), da cui i flussi SSE lo ricevono senza interrogare di continuo il
database; `estimate` stima il tempo residuo dei job in esecuzione (core/eta.py).

Coda e CPU: i job in coda partono in ordine di priorità del tipo (`PRIORITY`), a parità per data di creazione,
da un dispatcher per pool (core/dispatch.py: "cpu" con i job di calcolo in parallelo delle Impostazioni, "io"
con 8); i processi esterni di calcolo lanciati da `JobContext.run` prendono gli slot di un budget di CPU
condiviso fra tutti i job (core/budget.py), così pipeline, elementi e strumenti insieme non saturano la CPU.
"""

from __future__ import annotations

import codecs
import collections
import fnmatch
import functools
import os
import shutil
import signal
import subprocess
import sys
import threading
import time
import traceback
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterable, Optional, Union

from .budget import CpuBudget
from .db import Database, encode_json, now_iso
from .dispatch import Dispatcher
from .errors import AppError, Conflict, NotFound
from .eta import progress_eta, seconds_between
from .events import DELETED, IDLE, JOB, LOG, ChangeFeed
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

# Priorità di esecuzione per tipo di job (numero più basso = parte prima), nella coda di ciascun pool e nel
# budget di CPU: i calcoli interattivi brevi, che l'utente sta aspettando davanti allo schermo, prima della
# modellazione; questa prima dei lavori lunghi in blocco (pipeline, manutenzione dei database). A parità di
# priorità vale l'ordine di creazione. `compare` è il confronto strutturale (leggero, interattivo). La priorità
# migliora con l'attesa (invecchiamento: core/dispatch.AGING_SECONDS in coda, core/budget.AGING_SECONDS per gli slot),
# così un flusso continuo di calcoli interattivi non ferma per sempre modellazioni e pipeline.
PRIORITY = {
    "search": 0, "align": 0, "align2d": 0, "quality": 0, "qmean": 0, "compare": 0,
    "model": 1, "loop": 1, "selftest": 1,
    "pipeline": 2, "db_update_pdbaa": 2, "db_build_mmseqs": 2, "db_index_mmseqs": 2, "db_convert_uniclust": 2,
    "db_local_uniclust": 2,
}
DEFAULT_PRIORITY = 1
IO_WORKERS = 8
# Fase mostrata mentre un processo aspetta uno slot del budget di CPU (vedi JobContext.run)
WAIT_STAGE = "In attesa di CPU libera"
WAIT_SUFFIX = " · in attesa di CPU libera"
# Risorse esclusive (core/dispatch.py): il servizio QMEAN locale esegue un calcolo alla volta
QMEAN_LOCAL = "qmean_local"
RESOURCE_STAGE = {QMEAN_LOCAL: "In coda: il servizio QMEAN locale sta eseguendo un altro calcolo"}

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
# stima del tempo residuo di un job in esecuzione (riga di `jobs`, adesso): secondi o None
EtaEstimator = Callable[[dict, datetime], Optional[int]]
# risorsa esclusiva richiesta da un job, dai suoi parametri (None = nessuna)
ResourceFn = Callable[[dict], Optional[str]]


@dataclass
class _Registration:
    fn: Handler
    pool: str
    eta: Optional[EtaEstimator] = None
    priority: int = DEFAULT_PRIORITY
    resource: Optional[ResourceFn] = None


def _silent(_fraction: Optional[float], _stage: Optional[str]) -> None:
    """Progresso di un contesto figlio senza fase propria (es. l'analisi di qualità dentro la modellazione)."""


def cpu_slots(cpu: Union[bool, int]) -> int:
    """Slot del budget di CPU per il parametro `cpu` di `JobContext.run`: True = 1, False = 0, un numero = quello."""
    if cpu is True:
        return 1
    if cpu is False or cpu is None:
        return 0
    return max(1, int(cpu))


class JobContext:
    """Interfaccia offerta agli handler: log, progresso, sottoprocessi, cancellazione.

    I contesti figli (`sub`) condividono cancellazione e log del job. Le attese di CPU dei loro processi si
    mostrano nella fase del job: al posto della fase ("In attesa di CPU libera") se il processo è del job stesso
    o di un figlio senza fase propria e nessun altro processo del job è in esecuzione; altrimenti in coda alla fase
    ("Validazione · in attesa di CPU libera" con passi in parallelo, "3 di 5 sequenze elaborate · in attesa di CPU
    libera" per gli elementi di pipeline, figli con una fase propria), così le fasi della pipeline restano.
    """

    def __init__(self, manager: "JobManager", job_id: str, workdir: Path, params: dict,
                 cancel_event: threading.Event, prefix: str = "",
                 on_progress: Optional[Callable[[Optional[float], Optional[str]], None]] = None, *,
                 priority: int = DEFAULT_PRIORITY, parent: Optional["JobContext"] = None, lane: bool = False,
                 stage: Optional[str] = None, release_resource: Optional[Callable[[], None]] = None) -> None:
        self._manager = manager
        self.job_id = job_id
        self.workdir = workdir
        self.params = params
        self._cancel = cancel_event
        self._prefix = prefix
        self._on_progress = on_progress
        self.priority = priority
        # radice (la fase del job) e "corsia": il contesto con una fase propria da cui dipende questo (sé stesso
        # se è la radice o un figlio con progresso proprio, come un elemento di pipeline; altrimenti quella del padre)
        self._root: JobContext = parent._root if parent is not None else self
        self._lane: JobContext = self if parent is None or lane else parent._lane
        # solo nella radice: fase dichiarata dall'handler, fase mostrata e processi in attesa di CPU
        self._stage_lock = threading.Lock()
        self._stage = stage
        self._shown = stage
        self._own_waiting = 0
        self._lane_waiting = 0
        self._running = 0  # processi del job in esecuzione (da qualunque contesto)
        self._release = release_resource  # solo nella radice: restituisce la risorsa esclusiva (core/dispatch.py)

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

    def release_resource(self) -> None:
        """Restituisce subito la risorsa esclusiva del job (`register(resource=...)`), prima della fine
        dell'esecuzione: il prossimo job che la aspetta può partire. Es. QMEAN finita la parte locale, prima del
        fallback su SWISS-MODEL. Senza risorsa, o già restituita, non fa nulla."""
        release = self._root._release
        if release is not None:
            release()

    def log(self, message: str) -> None:
        stamp = datetime.now().strftime("%H:%M:%S")
        for line in str(message).splitlines() or [""]:
            self._manager._append_log(self.job_id, f"[{stamp}] {self._prefix}{line}")

    def progress(self, fraction: Optional[float] = None, stage: Optional[str] = None) -> None:
        if self._on_progress is not None:
            self._on_progress(fraction, stage)
            return
        # radice: la fase mostrata tiene conto dei processi in attesa di CPU (sotto il lock: le fasi scritte da
        # più thread, es. gli elementi di una pipeline, arrivano nel database nell'ordine in cui si decidono)
        with self._stage_lock:
            shown = None
            if stage is not None:
                self._stage = stage
                shown = self._shown = self._display()
            self._manager._set_progress(self.job_id, fraction, shown)

    def _display(self) -> Optional[str]:
        """Fase da mostrare (radice, sotto il lock). Al posto della fase solo se il job non ha nessun processo in
        esecuzione: con passi in parallelo (uno gira, un altro aspetta) o con gli elementi di una pipeline l'attesa
        si aggiunge in coda alla fase dichiarata dall'handler."""
        if self._own_waiting > 0 and self._running == 0:
            return WAIT_STAGE
        if self._own_waiting > 0 or self._lane_waiting > 0:
            return f"{self._stage}{WAIT_SUFFIX}" if self._stage else WAIT_STAGE
        return self._stage

    def _track(self, *, waiting: int = 0, running: int = 0) -> None:
        """Processi di questo contesto in attesa di uno slot di CPU o in esecuzione: aggiorna la fase del job (quella
        dichiarata dall'handler torna appena nessun processo aspetta più). Mostrarla è accessorio: un errore nello
        scriverla (database chiuso all'arresto) non interrompe il processo."""
        root = self._root
        with root._stage_lock:
            if waiting and self._lane is root:
                root._own_waiting = max(0, root._own_waiting + waiting)
            elif waiting:
                root._lane_waiting = max(0, root._lane_waiting + waiting)
            root._running = max(0, root._running + running)
            shown = root._display()
            if shown is not None and shown != root._shown:
                root._shown = shown
                try:
                    self._manager._set_progress(self.job_id, None, shown)
                except Exception:
                    pass

    def _cpu_wait(self, waiting: bool) -> None:
        """Inizio o fine dell'attesa di uno slot di CPU (chiamata dal budget)."""
        self._track(waiting=1 if waiting else -1)

    def cpu_threads(self, wanted: int) -> int:
        """Thread da dare a un programma multi-thread che ne vorrebbe `wanted`: non oltre gli slot del budget di
        CPU. Lo stesso numero va passato a `run(cpu=...)`, così il programma occupa uno slot per thread."""
        return self._manager.budget.slots_for(wanted)

    def sub(self, name: str, workdir: Path,
            on_progress: Optional[Callable[[Optional[float], Optional[str]], None]] = None) -> "JobContext":
        """Contesto figlio (es. un elemento di pipeline): condivide cancellazione e log del job padre.

        Con `on_progress` il figlio ha una fase propria (l'elemento di pipeline); senza, il suo progresso non si
        mostra e le sue attese di CPU contano come quelle del padre.
        """
        workdir.mkdir(parents=True, exist_ok=True)
        return JobContext(self._manager, self.job_id, workdir, self.params, self._cancel,
                          prefix=f"{self._prefix}[{name}] ", on_progress=on_progress or _silent,
                          priority=self.priority, parent=self, lane=on_progress is not None)

    def path(self, *parts: str) -> Path:
        return safe_join(self.workdir, *parts)

    def commit(self, success: bool = True) -> None:
        """Sposta subito i file di questa cartella di lavoro tra i risultati definitivi del job.

        Usato dalla pipeline a ogni elemento concluso: un batch lungo salva il lavoro man mano e lo
        scratch resta piccolo.
        """
        self._manager._commit(self.job_id, self.workdir, success=success)

    def run(self, cmd: Union[list[str], Callable[[int], list[str]]], *, cwd: Optional[Path] = None,
            env: Optional[dict] = None, timeout: Optional[float] = None,
            on_line: Optional[Callable[[str], None]] = None, log_output: bool = True,
            cpu: Union[bool, int] = True) -> ProcessResult:
        """Esegue un processo esterno registrandone l'output nel log; rispetta cancellazione e timeout.

        Senza `timeout` esplicito vale il timeout dei job delle Impostazioni.

        `cpu`: slot del budget di CPU condiviso fra i job (core/budget.py) occupati dal processo finché gira.
        True (predefinito) = 1, per i programmi di calcolo a un thread; False per i processi leggeri o di I/O, che
        partono subito; un numero N per i programmi multi-thread, uno slot per thread:
        - con `cmd` funzione del numero di thread (`cmd(n)` → comando) la richiesta è elastica: al suo turno il
          processo prende da 1 a N slot, quanti ne sono liberi, e parte con altrettanti thread (non blocca la coda
          aspettando che se ne liberino N);
        - con `cmd` lista il processo aspetta tutti gli N slot (N non oltre la capacità, vedi `cpu_threads`).
        L'attesa di uno slot si interrompe con l'annullamento, mostra "In attesa di CPU libera" e non conta nel
        timeout, che parte con il processo.
        """
        self.check()
        if timeout is None:
            timeout = float(self.settings.job_timeout_minutes) * 60
        slots = cpu_slots(cpu)
        elastic = callable(cmd)
        grant = self._manager.budget.acquire(slots, minimum=1 if elastic else None, priority=self.priority,
                                             check=self.check, on_wait=self._cpu_wait) if slots else None
        started = False
        try:
            argv = cmd(grant.slots if grant is not None else 1) if elastic else cmd
            self._track(running=1)
            started = True
            return self._popen(argv, cwd=cwd, env=env, timeout=timeout, on_line=on_line, log_output=log_output)
        finally:
            if grant is not None:
                grant.release()
            if started:
                self._track(running=-1)

    def _popen(self, cmd: list[str], *, cwd: Optional[Path], env: Optional[dict], timeout: Optional[float],
               on_line: Optional[Callable[[str], None]], log_output: bool) -> ProcessResult:
        self.check()  # annullato mentre aspettava la CPU: il processo non parte
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
    def __init__(self, db: Database, jobs_dir: Path, workers: Optional[int], settings_provider: Callable,
                 scratch_dir: Optional[Path] = None) -> None:
        """`workers`: job di calcolo in parallelo (pool "cpu") e slot del budget di CPU; None = dalle Impostazioni
        (`effective_workers`), riletti a ogni scelta, così un cambio vale senza riavvio."""
        self.db = db
        self.jobs_dir = jobs_dir
        # senza scratch i job lavorano direttamente nella cartella definitiva
        self.scratch_jobs = (scratch_dir / "jobs") if scratch_dir is not None else None
        self.settings_provider = settings_provider
        self._fixed_workers = workers
        self._handlers: dict[str, _Registration] = {}
        self.budget = CpuBudget(self.cpu_workers)
        self._pools = {
            "cpu": Dispatcher("cpu", self.cpu_workers, self._execute, on_blocked=self._blocked),
            "io": Dispatcher("io", lambda: IO_WORKERS, self._execute, on_blocked=self._blocked),
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
        self.events = ChangeFeed()
        jobs_dir.mkdir(parents=True, exist_ok=True)
        if self.scratch_jobs is not None:
            self.scratch_jobs.mkdir(parents=True, exist_ok=True)
        self._flusher = threading.Thread(target=self._flush_loop, name="bioloom-log-flush", daemon=True)
        self._flusher.start()

    # ------------------------------------------------------------------ registrazione e avvio
    def register(self, kind: str, fn: Handler, pool: str = "cpu", eta: Optional[EtaEstimator] = None,
                 priority: Optional[int] = None, resource: Optional[ResourceFn] = None) -> None:
        """Registra l'handler di un tipo di job; `eta` sostituisce la stima dal progresso (es. la pipeline).

        `priority` sostituisce quella del tipo (`PRIORITY`); `resource(params)` indica una risorsa esclusiva
        (es. `QMEAN_LOCAL`): un job che la chiede resta in coda finché un altro la tiene, cioè fino alla fine della sua
        esecuzione o a `JobContext.release_resource`.
        """
        if pool not in self._pools:
            raise ValueError(f"Pool sconosciuto: {pool}")
        rank = PRIORITY.get(kind, DEFAULT_PRIORITY) if priority is None else int(priority)
        self._handlers[kind] = _Registration(fn, pool, eta, rank, resource)

    def cpu_workers(self) -> int:
        """Job di calcolo in parallelo e slot del budget di CPU in questo momento."""
        if self._fixed_workers:
            return max(1, int(self._fixed_workers))
        try:
            return max(1, int(self.settings_provider().effective_workers()))
        except Exception:  # impostazioni non leggibili: si procede con un solo job alla volta
            return 1

    def reconfigure(self) -> None:
        """Le Impostazioni sono cambiate (job in parallelo): code e budget di CPU si riesaminano subito."""
        for pool in self._pools.values():
            pool.wake()
        self.budget.wake()

    def _enqueue(self, job_id: str, kind: str, params: dict, created_at: str) -> None:
        """Mette il job nella coda del suo pool, con la priorità del tipo e l'eventuale risorsa esclusiva.

        A parità di priorità l'ordine è quello di creazione: `created_at` (al secondo) e poi il rowid della riga,
        così un job ripreso torna davanti a quelli creati dopo di lui anche nello stesso secondo.
        """
        registration = self._handlers[kind]
        # la risorsa si ricalcola al momento della scelta: le Impostazioni possono cambiare mentre il job aspetta
        resource = None if registration.resource is None else \
            functools.partial(registration.resource, params if isinstance(params, dict) else {})
        rowid = self.db.scalar("SELECT rowid FROM jobs WHERE id=?", (job_id,)) or 0
        self._pools[registration.pool].push(job_id, priority=registration.priority, created_at=created_at,
                                            created_seq=int(rowid), resource=resource)

    def _blocked(self, job_id: str, resource: str) -> None:
        """Il job resta in coda perché la sua risorsa esclusiva è occupata: la fase lo dice."""
        stage = RESOURCE_STAGE.get(resource, "In coda: la risorsa richiesta è occupata da un altro job")
        if self.db.execute("UPDATE jobs SET stage=?, updated_at=? WHERE id=? AND status='queued'",
                           (stage, now_iso(), job_id)):
            self.events.publish(job_id, JOB)

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
        self.events.publish(job_id, JOB)
        self._enqueue(job_id, kind, params, now)
        return self.with_eta(self.get(job_id))

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
        self.events.publish(job_id, JOB)  # di nuovo attivo: i client aprono un nuovo flusso
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
            self.events.publish(job_id, IDLE)
        # se nel frattempo è stato annullato, l'esecuzione trova lo stato cambiato e non parte; in coda il job
        # riprende il suo posto per priorità e data di creazione
        stored = self.db.query_one("SELECT params FROM jobs WHERE id=?", (job_id,)) or {}
        self._enqueue(job_id, job["kind"], stored.get("params") or {}, job["created_at"])
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
             statuses: Optional[list[str]] = None, limit: int = 100, offset: int = 0,
             ids: Optional[Iterable[str]] = None) -> list[dict]:
        where, args = [], []
        if ids is not None:  # solo questi job (quelli inesistenti si ignorano)
            ids = sorted(set(ids))
            if not ids:
                return []
            where.append(f"id IN ({','.join('?' * len(ids))})")
            args.extend(ids)
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

    def estimate(self, job: dict, now: Optional[datetime] = None) -> Optional[int]:
        """Secondi rimanenti stimati di un job in esecuzione (core/eta.py); None se non stimabile.

        Nessuna stima per job in coda, in annullamento o terminati. I tipi che registrano uno stimatore
        (`register(eta=...)`, la pipeline) lo usano; gli altri stimano dal progresso.
        """
        if job.get("status") != "running":
            return None
        now = now or datetime.now(timezone.utc)
        registration = self._handlers.get(job.get("kind", ""))
        try:
            if registration is not None and registration.eta is not None:
                return registration.eta(job, now)
            return progress_eta(seconds_between(job.get("started_at"), now), job.get("progress"))
        except (TypeError, ValueError, ArithmeticError):  # dati inattesi: la stima manca, la lettura riesce
            return None

    def with_eta(self, jobs: dict | list[dict], now: Optional[datetime] = None) -> dict | list[dict]:
        """Aggiunge `eta_seconds` (null se non stimabile) e `queue_position` a un riepilogo di job o a un elenco;
        restituisce l'input.

        `queue_position`: per un job in coda, la sua posizione (da 1) nella coda del suo pool, nell'ordine in cui
        i job partiranno; null per gli altri e per un job non ancora (o non più) nella coda, come una ripresa in
        preparazione.
        """
        now = now or datetime.now(timezone.utc)
        rows = [jobs] if isinstance(jobs, dict) else jobs
        positions: dict[str, int] = {}
        if any(job.get("status") == "queued" for job in rows):
            for pool in self._pools.values():
                positions.update(pool.positions())
        for job in rows:
            job["eta_seconds"] = self.estimate(job, now)
            job["queue_position"] = positions.get(job.get("id", "")) if job.get("status") == "queued" else None
        return jobs

    def busy(self, job_id: str) -> bool:
        """L'esecuzione del job (o la preparazione di una sua ripresa) non è ancora chiusa: anche con lo stato
        già terminale, log e file possono non essere completi."""
        with self._lock:
            return job_id in self._running or job_id in self._preparing

    @property
    def closed(self) -> bool:
        """Il gestore è stato arrestato (`shutdown`)."""
        return self._stop.is_set()

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

    def read_log(self, job_id: str, offset: int = 0, limit: int = 256 * 1024, whole_lines: bool = False) -> dict:
        """Log dal byte `offset` (al massimo `limit` byte): testo, offset successivo, dimensione, inizio letto.

        Con `whole_lines` restituisce solo righe complete (un flush a metà riga o a metà di un carattere
        multibyte arriva alla lettura successiva); una riga più lunga di `limit` si spezza, ma mai dentro un
        carattere. I flussi SSE lo usano per inviare ogni riga una volta sola e intera.
        """
        path = self.workdir(job_id) / LOG_NAME
        if not path.exists():
            return {"text": "", "offset": 0, "size": 0, "start": 0}
        size = path.stat().st_size
        start = offset if 0 <= offset <= size else max(0, size - limit)
        with open(path, "rb") as fh:
            fh.seek(start)
            data = fh.read(limit)
        if whole_lines and data and not data.endswith(b"\n"):
            cut = data.rfind(b"\n")
            if cut >= 0:
                data = data[:cut + 1]
            elif len(data) < limit:
                data = b""  # riga non ancora completa su disco
            else:
                decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
                decoder.decode(data, final=False)
                data = data[:len(data) - len(decoder.getstate()[0])]  # senza il carattere incompleto finale
        return {"text": data.decode("utf-8", errors="replace"), "offset": start + len(data), "size": size,
                "start": start}

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
        self.budget.wake()  # un processo in attesa di CPU se ne accorge subito e non parte
        for proc in procs:
            _kill(proc)
        # Un job in coda si chiude subito ed esce dalla coda, nello stesso passo sotto il lock: una ripresa
        # successiva (che lo rimette in coda) non può vedersi togliere la nuova voce. Se nel frattempo è partito,
        # lo chiuderà la sua esecuzione.
        closed = False
        if job["status"] == "queued":
            with self._lock:
                closed = self._finish(job_id, "cancelled", error="Annullato dall'utente.", only_from=("queued",))
                if closed:
                    for pool in self._pools.values():
                        pool.discard(job_id)
        if not closed:
            if self.db.execute("UPDATE jobs SET status='cancelling', stage='Annullamento in corso', updated_at=? "
                               "WHERE id=? AND status='running'", (now_iso(), job_id)):
                self.events.publish(job_id, JOB)
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
        self.events.publish(job_id, DELETED, project_id=job.get("project_id"))
        self._close_log(job_id)
        remove_tree(self.workdir(job_id))
        remove_tree(self.live_dir(job_id))

    def shutdown(self) -> None:
        with self._lock:
            events = list(self._cancel_events.values())
            procs = [p for group in self._processes.values() for p in group]
        for event in events:
            event.set()
        self.budget.close()
        for proc in procs:
            _kill(proc)
        for pool in self._pools.values():
            pool.close()  # i job ancora in coda li marca `recover` al prossimo avvio
        self._stop.set()
        self._flush_all()

    # ------------------------------------------------------------------ esecuzione
    def _execute(self, job_id: str, release_resource: Optional[Callable[[], None]] = None) -> None:
        """Esegue un job in coda (dal dispatcher del suo pool); `release_resource` restituisce prima della fine la
        sua risorsa esclusiva (`JobContext.release_resource`)."""
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
        self.events.publish(job_id, JOB)
        live = self.live_dir(job_id)
        live.mkdir(parents=True, exist_ok=True)
        registration = self._handlers[job["kind"]]
        ctx = JobContext(self, job_id, live, job["params"] or {}, event, priority=registration.priority,
                         stage="Avvio", release_resource=release_resource)
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
            self.events.publish(job_id, IDLE)  # log completo su disco: i flussi SSE possono chiudersi

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
        changed = self.db.execute(
            "UPDATE jobs SET status=?, result=?, error=?, stage=?, progress=CASE WHEN ?='completed' THEN 1 "
            f"ELSE progress END, finished_at=?, updated_at=? WHERE id=?{guard}",
            (status, encode_json(result), error, stage, status, now, now, job_id, *extra)) == 1
        if changed:
            self.events.publish(job_id, JOB)
        return changed

    def _set_progress(self, job_id: str, fraction: Optional[float], stage: Optional[str]) -> None:
        sets, args = ["updated_at=?"], [now_iso()]
        if fraction is not None:
            sets.append("progress=?")
            args.append(max(0.0, min(1.0, float(fraction))))
        if stage is not None:
            sets.append("stage=?")
            args.append(stage[:200])
        if self.db.execute(f"UPDATE jobs SET {', '.join(sets)} WHERE id=? AND status='running'", (*args, job_id)):
            self.events.publish(job_id, JOB)

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
                self.events.publish(job_id, LOG)  # righe nuove su disco: i flussi SSE le leggono

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
                        self.events.publish(job_id, LOG)
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
                self.events.publish(job_id, LOG)

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
