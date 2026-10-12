"""Worker QMEAN per BioLoom: piccolo servizio HTTP interno che esegue run_qmean.py.

Gira nel container ufficiale di QMEAN (Python 3.6, sola libreria standard). BioLoom lo raggiunge sulla
rete interna di Docker (nessuna porta pubblicata, nessun accesso al socket di Docker).

  GET    /status[?prefer=<id>]  database UniClust30/UniRef30 trovati (qualunque versione), QMTL, coda
  POST   /jobs                  {"pdb", "seqres"?, "method": "auto|QMEAN|QMEANDisCo", "database"?: <id>}
  POST   /convert               {"database": <id>}  ricrea il cs219 di un database HH-suite 2
  POST   /local                 {"database": <id>, "action": "copy|remove"}  copia locale di indici e cs219
  GET    /jobs/<id>             stato, risultato (JSON di run_qmean.py) o errore
  DELETE /jobs/<id>             annulla (termina il processo)

I database stanno in /uniclust_src (disco esterno, sola lettura; vedi uniclust.py). Un calcolo alla volta, salvo
QMEAN_PARALLEL su un server con memoria per più HHblits (slots_for). Anche copie e conversioni passano dalla stessa
coda e da sole, così non cambiano mai i file sotto un calcolo in corso. I profili HHblits di ogni sequenza si
conservano nel volume /cache (run_qmean_wrapper.py): una sequenza già vista non rifà la ricerca su UniRef30. La cartella di lavoro di ogni calcolo viene eliminata
appena finisce; i risultati restano in memoria per RESULT_TTL secondi.
"""

import hashlib
import io
import json
import os
import queue
import shutil
import signal
import socketserver
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import uniclust  # noqa: E402

QMTL = "/qmtl"
OST = "/usr/local/bin/ost"  # interprete di OpenStructure: carica la libreria dei composti chimici
WRAPPER = os.path.join(os.path.dirname(os.path.abspath(__file__)), "run_qmean_wrapper.py")
QMTL_FILES = ["VERSION", "CHAINCLUSTERINDEX", "indexer.dat", "seqres_data.dat", "atomseq_data.dat", "ca_pos_data.dat"]
MAX_SECONDS = int(os.environ.get("QMEAN_MAX_SECONDS", str(4 * 3600)))
CONVERT_MAX_SECONDS = int(os.environ.get("QMEAN_CONVERT_MAX_SECONDS", str(48 * 3600)))
RESULT_TTL = 3600
GB, MB = uniclust.GB, uniclust.MB
# processori per HHblits (run_qmean.py ne userebbe 1): cambia solo la velocità, non il risultato.
# QMEAN_HHBLITS_CPU li fissa; altrimenti tutti (max 8) se la memoria basta, 1 se è al limite: con 4 thread e
# il cs219 che non sta in memoria HHblits passava il 95% del tempo a rileggerlo (misurato su UniRef30_2020_06).
CPU_FIXED = int(os.environ.get("QMEAN_HHBLITS_CPU") or 0)
CPU_COUNT = os.cpu_count() or 1
CPU_ALL = min(CPU_COUNT, 8)
# Calcoli QMEAN contemporanei (QMEAN_PARALLEL, predefinito 1: un PC ha memoria per uno solo). Ognuno è un HHblits con
# la sua copia degli indici e del cs219 in memoria: il numero effettivo si ferma a quanti ne entrano (slots_for). Copie
# locali e conversioni dei database restano esclusive: nessun calcolo mentre cambiano i file.
PARALLEL = max(1, int(os.environ.get("QMEAN_PARALLEL") or 1))
# Profili HHblits riusati per sequenza (run_qmean_wrapper.py): spazio massimo in GB, 0 = nessun riuso
PROFILE_CACHE_GB = float(os.environ.get("QMEAN_PROFILE_CACHE_GB") or 10)
PROFILES = os.path.join(uniclust.CACHE, "profiles")


def hhblits_cpu(fit, slots=1):
    """Processori di un HHblits: QMEAN_HHBLITS_CPU se indicato; 1 con la memoria al limite; altrimenti tutti (max 8),
    divisi fra i calcoli contemporanei."""
    if CPU_FIXED > 0:
        return CPU_FIXED
    if fit not in ("ok", None):
        return 1
    return CPU_ALL if slots <= 1 else max(1, min(CPU_ALL, CPU_COUNT // slots))


def slots_for(db, mem):
    """Calcoli contemporanei possibili con questo database: QMEAN_PARALLEL, non oltre quanti HHblits entrano nel 90%
    della memoria (il cs219 nella cache del sistema è uno per tutti)."""
    if PARALLEL <= 1 or not db or not mem:
        return 1
    need = uniclust.memory_needed(db)
    shared = uniclust.memory_recommended(db) - need
    if need <= 0:
        return PARALLEL
    return max(1, min(PARALLEL, int((mem["total"] * 0.9 - shared) // need)))


class Gate:
    """Calcoli QMEAN insieme fino a `slots`; copie e conversioni da sole (aspettano la fine dei calcoli in corso e
    fermano i nuovi finché non tocca a loro)."""

    def __init__(self, slots=1):
        self.slots = max(1, slots)
        self.running = 0
        self.exclusive = False
        self.waiting = 0
        self._cond = threading.Condition()

    def enter(self, exclusive):
        with self._cond:
            if exclusive:
                self.waiting += 1
                while self.exclusive or self.running:
                    self._cond.wait()
                self.waiting -= 1
                self.exclusive = True
            else:
                while self.exclusive or self.waiting or self.running >= self.slots:
                    self._cond.wait()
                self.running += 1

    def leave(self, exclusive):
        with self._cond:
            if exclusive:
                self.exclusive = False
            else:
                self.running -= 1
            self._cond.notify_all()


_gate = Gate()
_prune_lock = threading.Lock()


def profile_folder(db):
    """Cartella dei profili di questo database: cambia se il database sul disco cambia (dimensione o data dell'indice
    degli a3m), così un profilo non sopravvive a un aggiornamento di UniRef30."""
    index = db["a3m"][1]
    try:
        st = os.stat(index)
        stamp = "%s:%d:%d" % (index, st.st_size, int(st.st_mtime))
    except OSError:
        stamp = index
    return os.path.join(PROFILES, "%s-%s" % (uniclust.safe_id(db["id"]), hashlib.md5(stamp.encode()).hexdigest()[:12]))


def prune_profiles(limit_bytes=None):
    """Toglie i profili usati meno di recente finché lo spazio scende all'80% del massimo; le cartelle di database non
    più presenti contano come le altre (si svuotano per prime se più vecchie). Restituisce i byte occupati."""
    limit = PROFILE_CACHE_GB * GB if limit_bytes is None else limit_bytes
    with _prune_lock:
        files = []
        for root, _dirs, names in os.walk(PROFILES):
            for name in names:
                path = os.path.join(root, name)
                try:
                    st = os.stat(path)
                except OSError:
                    continue
                files.append((st.st_mtime, st.st_size, path))
        total = sum(size for _t, size, _p in files)
        if total <= limit:
            return total
        for _mtime, size, path in sorted(files):
            if total <= limit * 0.8:
                break
            try:
                os.remove(path)
                total -= size
            except OSError:
                pass
        return total
MAX_BODY = 50 * 1024 * 1024

_jobs = {}
_lock = threading.Lock()
_queue = queue.Queue()
_scan = uniclust.Cache(ttl=30)


def qmtl_status():
    present = all(os.path.exists(os.path.join(QMTL, f)) for f in QMTL_FILES)
    version = None
    if present:
        try:
            with io.open(os.path.join(QMTL, "VERSION"), encoding="utf-8", errors="replace") as fh:
                version = fh.read().strip()
        except OSError:
            present = False
    return {"present": present, "version": version}


def database_status(prefer=None, force=False):
    scan = _scan.get(force=force)
    chosen = uniclust.choose(scan["databases"], prefer)
    with _lock:
        active = [j for j in _jobs.values() if j["status"] in ("queued", "running")]
        converting = {j["database"] for j in active if j["type"] == "convert"}
        copying = {j["database"]: j for j in active if j["type"] == "local" and j.get("action") == "copy"}
    dbs = []
    for db in scan["databases"]:
        item = uniclust.public(db)
        if db["id"] in converting:
            item["state"] = "converting"
        if db["id"] in copying:
            item["local"] = dict(item["local"], state="copying", progress=copying[db["id"]].get("progress", 0.0))
        dbs.append(item)
    if chosen:
        compat = {"valid": True, "prefix": chosen["name"], "id": chosen["id"], "version": chosen["version"],
                  "size": chosen["size"], "hhm": chosen["hhm"], "error": None}
    else:
        if prefer and any(d["id"] == prefer for d in scan["databases"]):
            error = "la versione scelta (%s) non è pronta" % prefer
        elif scan["databases"]:
            error = "nessun database pronto: " + "; ".join(
                "%s: %s" % (d["name"], ", ".join(d["problems"]) or "da convertire") for d in scan["databases"])
        elif scan["archives"]:
            error = "trovato solo l'archivio compresso (%s): va estratto sul disco" % scan["archives"][0]["file"]
        else:
            error = "nessun database UniClust30/UniRef30 nella cartella montata"
        compat = {"valid": False, "prefix": None, "id": None, "error": error}
    known = {uniclust.safe_id(d["id"]) for d in scan["databases"]}
    orphans = [c for c in uniclust.local_copies() if c["id"] not in known]
    mem = uniclust.memory()
    for item, db in zip(dbs, scan["databases"]):
        item["memory"] = uniclust.memory_fit(db, mem)
    if chosen:
        compat["memory"] = uniclust.memory_fit(chosen, mem)
        compat["memory_message"] = uniclust.memory_message(chosen, mem)
    return {"uniclust": compat, "databases": dbs, "archives": scan["archives"], "unsupported": scan["unsupported"],
            "selected": chosen["id"] if chosen else None, "local_orphans": orphans, "memory": mem}


def _purge():
    now = time.time()
    with _lock:
        for job_id in [j for j, v in _jobs.items() if v.get("finished") and now - v["finished"] > RESULT_TTL]:
            _jobs.pop(job_id, None)


def _run_process(job, cmd, cwd, env, limit):
    proc = subprocess.Popen(cmd, cwd=cwd, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            encoding="utf-8", errors="replace", start_new_session=True)
    with _lock:
        job["pid"] = proc.pid
    try:
        output, _ = proc.communicate(timeout=limit)
    except subprocess.TimeoutExpired:
        os.killpg(proc.pid, signal.SIGKILL)
        output, _ = proc.communicate()
        raise RuntimeError("interrotto dopo %d s" % limit)
    return proc.returncode, output or ""


def _run_qmean(job):
    scan = _scan.get(force=True)
    db = uniclust.choose(scan["databases"], job.get("database") or None)
    if db is None:
        raise RuntimeError(database_status(job.get("database") or None)["uniclust"]["error"])
    mem = uniclust.memory()
    fit = uniclust.memory_fit(db, mem)
    if fit == "insufficient":
        raise RuntimeError(uniclust.memory_message(db, mem))
    cpu = hhblits_cpu(fit, _gate.slots)
    workdir = tempfile.mkdtemp(prefix="qmean_")
    try:
        prefix = uniclust.prepare_view(db, os.path.join(workdir, "uniclust_view"))
        model = os.path.join(workdir, "model.pdb")
        with io.open(model, "w", encoding="utf-8") as fh:
            fh.write(job["pdb"])
        method = job["method"]
        if method == "auto":
            method = "QMEANDisCo" if qmtl_status()["present"] else "QMEAN"
        cmd = [OST, WRAPPER, model, "--out", os.path.join(workdir, "out.json"), "--method", method]
        if job.get("seqres"):
            seqres = os.path.join(workdir, "seqres.fasta")
            with io.open(seqres, "w", encoding="utf-8") as fh:
                fh.write(job["seqres"])
            cmd += ["--seqres", seqres]
        # TMPDIR: anche le cartelle temporanee di run_qmean.py finiscono in workdir, eliminata sempre
        env = dict(os.environ, BIOLOOM_UNICLUST_PREFIX=prefix, BIOLOOM_HHBLITS_CPU=str(cpu), TMPDIR=workdir)
        if PROFILE_CACHE_GB > 0:
            env["BIOLOOM_PROFILE_CACHE"] = profile_folder(db)
        with _lock:
            job.update(method_used=method, database_used=uniclust.public(db), cpu=cpu)
        code, output = _run_process(job, cmd, workdir, env, MAX_SECONDS)
        out_path = os.path.join(workdir, "out.json")
        if code != 0 or not os.path.exists(out_path):
            tail = "\n".join(output.strip().splitlines()[-15:])
            raise RuntimeError("run_qmean.py terminato con codice %s\n%s" % (code, tail))
        with io.open(out_path, encoding="utf-8") as fh:
            result = json.load(fh)
        result["bioloom_database"] = uniclust.public(db)
        result["bioloom_hhblits"] = {"cpu": cpu, "memory": fit, "seconds": round(time.time() - job["started"]),
                                     "parallel": _gate.slots}
        try:  # profili riusati e calcolati (run_qmean_wrapper.py)
            with io.open(os.path.join(workdir, "profile_cache.json"), encoding="utf-8") as fh:
                result["bioloom_profiles"] = json.load(fh)
        except (OSError, ValueError):
            pass
        if PROFILE_CACHE_GB > 0:
            prune_profiles()
        return result, output[-4000:]
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


def _run_convert(job):
    scan = _scan.get(force=True)
    db = next((d for d in scan["databases"] if d["id"] == job["database"]), None)
    if db is None:
        raise RuntimeError("database %s non trovato" % job["database"])
    if db["state"] == "ready":
        return {"database": uniclust.public(db), "converted": False}, "già pronto"
    plan = uniclust.conversion_plan(db)
    uniclust.prepare_conversion(db, plan)
    code, output = _run_process(job, plan["command"], plan["staging"], dict(os.environ), CONVERT_MAX_SECONDS)
    if code != 0:
        tail = "\n".join(output.strip().splitlines()[-10:])
        raise RuntimeError("cstranslate terminato con codice %s\n%s" % (code, tail))
    uniclust.finish_conversion(plan)
    db = uniclust.describe(db["id"], os.path.join(db["dir"], db["name"]), db["name"])
    _scan.get(force=True)
    return {"database": uniclust.public(db), "converted": True}, output[-2000:]


def _run_local(job):
    if job["action"] == "remove":
        freed = uniclust.remove_local(job["database"])
        _scan.get(force=True)
        return {"database": job["database"], "removed": True, "freed": freed}, "copia locale eliminata"
    scan = _scan.get(force=True)
    db = next((d for d in scan["databases"] if d["id"] == job["database"]), None)
    if db is None:
        raise RuntimeError("database %s non trovato" % job["database"])
    if db["state"] == "incomplete":
        raise RuntimeError("il database è incompleto: " + "; ".join(db["problems"]))
    started = time.time()

    def report(done, total, name):
        elapsed = max(time.time() - started, 1e-3)
        with _lock:
            job["progress"] = done / float(total or 1)
            job["stage"] = "Copia di %s: %s di %s GB (%s MB/s)" % (
                name, uniclust.num(done / GB), uniclust.num(total / GB), uniclust.num(done / elapsed / MB, 0))

    try:
        state = uniclust.copy_local(db, report=report, cancelled=lambda: job["status"] == "cancelled")
    except uniclust.Cancelled:
        raise RuntimeError("copia annullata")
    _scan.get(force=True)
    return ({"database": db["id"], "local": {k: v for k, v in state.items() if k != "files"},
             "seconds": round(time.time() - started)}, "copia locale pronta")


RUNNERS = {"qmean": _run_qmean, "convert": _run_convert, "local": _run_local}


def _run(job_id):
    with _lock:
        job = _jobs.get(job_id)
        if job is None or job["status"] == "cancelled":
            return
        exclusive = job["type"] != "qmean"
    _gate.enter(exclusive)
    try:
        _run_entered(job_id)
    finally:
        _gate.leave(exclusive)


def _run_entered(job_id):
    with _lock:
        job = _jobs.get(job_id)
        if job is None or job["status"] == "cancelled":  # annullato mentre aspettava il suo turno
            return
        job["status"] = "running"
        job["started"] = time.time()
    try:
        result, log_tail = RUNNERS[job["type"]](job)
        with _lock:
            if job["status"] != "cancelled":
                job.update(status="completed", result=result, log_tail=log_tail)
    except Exception as exc:
        with _lock:
            if job["status"] != "cancelled":
                job.update(status="failed", error=str(exc))
    finally:
        with _lock:
            job["finished"] = time.time()
            job.pop("pdb", None)
            job.pop("seqres", None)


def _worker_loop():
    while True:
        job_id = _queue.get()
        try:
            _run(job_id)
        finally:
            _queue.task_done()
            _purge()


def _new_job(kind, **fields):
    job_id = uuid.uuid4().hex[:16]
    with _lock:
        _jobs[job_id] = dict(id=job_id, type=kind, status="queued", created=time.time(), **fields)
    _queue.put(job_id)
    return job_id


class Handler(BaseHTTPRequestHandler):
    server_version = "BioLoomQMEAN/2"

    def log_message(self, fmt, *args):  # registro sintetico su stdout
        print("[qmean-worker] " + fmt % args, flush=True)

    def _send(self, code, payload):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _body(self):
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0 or length > MAX_BODY:
            self._send(413, {"error": "richiesta vuota o troppo grande"})
            return None
        try:
            return json.loads(self.rfile.read(length).decode("utf-8"))
        except ValueError:
            self._send(400, {"error": "JSON non valido"})
            return None

    def _job_view(self, job_id):
        with _lock:
            job = _jobs.get(job_id)
            if job is None:
                return None
            view = {k: v for k, v in job.items() if k not in ("pdb", "seqres", "pid")}
            if job["status"] == "queued":
                view["position"] = sum(1 for j in _jobs.values() if j["status"] == "queued"
                                       and j["created"] < job["created"])
            if job.get("started") and job["status"] == "running":
                view["elapsed"] = round(time.time() - job["started"])
            return view

    def do_GET(self):
        url = urlparse(self.path)
        if url.path == "/status":
            query = parse_qs(url.query)
            prefer = (query.get("prefer") or [""])[0] or None
            force = (query.get("refresh") or ["0"])[0] == "1"
            with _lock:
                running = sum(1 for j in _jobs.values() if j["status"] == "running")
                waiting = sum(1 for j in _jobs.values() if j["status"] == "queued")
            payload = database_status(prefer, force=force)
            # busy: nessun posto libero per un altro calcolo (con un calcolo alla volta: uno in corso)
            payload.update(qmtl=qmtl_status(), busy=running >= _gate.slots, running=running, queued=waiting,
                           slots=_gate.slots, qmean_version=os.environ.get("VERSION_QMEAN"),
                           profiles=profiles_status())
            self._send(200, payload)
            return
        if url.path.startswith("/jobs/"):
            view = self._job_view(url.path[6:])
            self._send(200 if view else 404, view or {"error": "job sconosciuto"})
            return
        self._send(404, {"error": "percorso sconosciuto"})

    def do_POST(self):
        path = urlparse(self.path).path
        if path not in ("/jobs", "/convert", "/local"):
            self._send(404, {"error": "percorso sconosciuto"})
            return
        data = self._body()
        if data is None:
            return
        if path == "/convert":
            db_id = data.get("database") or ""
            if not any(d["id"] == db_id for d in _scan.get(force=True)["databases"]):
                self._send(404, {"error": "database sconosciuto: %s" % db_id})
                return
            self._send(202, {"id": _new_job("convert", database=db_id), "status": "queued"})
            return
        if path == "/local":
            db_id = data.get("database") or ""
            action = data.get("action") or "copy"
            scan = _scan.get(force=True)
            known = any(d["id"] == db_id for d in scan["databases"])
            orphan = action == "remove" and any(c["id"] == db_id for c in uniclust.local_copies())
            if action not in ("copy", "remove") or not (known or orphan):
                self._send(404, {"error": "database o azione sconosciuti: %s %s" % (db_id, action)})
                return
            self._send(202, {"id": _new_job("local", database=db_id, action=action), "status": "queued"})
            return
        pdb = data.get("pdb") or ""
        method = data.get("method") or "auto"
        if "ATOM" not in pdb or method not in ("auto", "QMEAN", "QMEANDisCo"):
            self._send(400, {"error": "struttura o metodo non validi"})
            return
        status = database_status(data.get("database") or None, force=True)["uniclust"]
        if not status["valid"]:
            self._send(503, {"error": "UniClust30 non disponibile: " + str(status["error"])})
            return
        if status.get("memory") == "insufficient":
            self._send(503, {"error": status["memory_message"]})
            return
        if method == "QMEANDisCo" and not qmtl_status()["present"]:
            self._send(503, {"error": "QMEANDisCo richiede la QMTL montata su /qmtl"})
            return
        job_id = _new_job("qmean", pdb=pdb, seqres=data.get("seqres") or "", method=method,
                          database=data.get("database") or "")
        self._send(202, {"id": job_id, "status": "queued"})

    def do_DELETE(self):
        path = urlparse(self.path).path
        if not path.startswith("/jobs/"):
            self._send(404, {"error": "percorso sconosciuto"})
            return
        job_id = path[6:]
        with _lock:
            job = _jobs.get(job_id)
            if job is None:
                self._send(404, {"error": "job sconosciuto"})
                return
            pid = job.get("pid") if job["status"] == "running" else None
            if job["status"] in ("queued", "running"):
                job.update(status="cancelled", finished=time.time())
        if pid:
            try:
                os.killpg(pid, signal.SIGKILL)
            except OSError:
                pass
        self._send(200, {"id": job_id, "status": "cancelled"})


class Server(socketserver.ThreadingMixIn, HTTPServer):
    daemon_threads = True


def profiles_status():
    """Profili HHblits salvati: numero, spazio occupato e massimo (byte)."""
    count = size = 0
    for root, _dirs, names in os.walk(PROFILES):
        for name in names:
            try:
                size += os.path.getsize(os.path.join(root, name))
                count += 1
            except OSError:
                pass
    return {"enabled": PROFILE_CACHE_GB > 0, "count": count, "size": size, "limit": int(PROFILE_CACHE_GB * GB)}


def main():
    port = int(os.environ.get("QMEAN_WORKER_PORT", "8080"))
    status = database_status(force=True)
    chosen = uniclust.choose(_scan.get()["databases"], None)
    _gate.slots = slots_for(chosen, status.get("memory"))
    for _ in range(max(PARALLEL, 1)):  # un thread per posto; i posti in più (memoria) aspettano al cancello
        threading.Thread(target=_worker_loop, daemon=True).start()
    for db in status["databases"]:
        print("[qmean-worker] database %s (%s, %s, %s): %s, copia locale %s"
              % (db["name"], db["version"] or "versione ?", db["format"], db["filesystem"] or "fs ?", db["state"],
                 db["local"]["state"]), flush=True)
    print("[qmean-worker] in uso: %s" % (status["uniclust"]["prefix"] or status["uniclust"]["error"]), flush=True)
    print("[qmean-worker] QMTL: %s" % (qmtl_status()["version"] or "assente (metodo QMEAN)"), flush=True)
    mem = status.get("memory") or {}
    print("[qmean-worker] HHblits: %d processori, memoria %.1f GB + swap %.1f GB" % (
        hhblits_cpu(status["uniclust"].get("memory"), _gate.slots), mem.get("total", 0) / GB,
        mem.get("swap", 0) / GB), flush=True)
    print("[qmean-worker] calcoli contemporanei: %d%s; profili riusati: %s" % (
        _gate.slots, " (QMEAN_PARALLEL=%d, limitati dalla memoria)" % PARALLEL if _gate.slots < PARALLEL else "",
        "fino a %s GB" % uniclust.num(PROFILE_CACHE_GB) if PROFILE_CACHE_GB > 0 else "no"), flush=True)
    if status["uniclust"].get("memory_message"):
        print("[qmean-worker] attenzione: " + status["uniclust"]["memory_message"], flush=True)
    Server(("0.0.0.0", port), Handler).serve_forever()


if __name__ == "__main__":
    main()
