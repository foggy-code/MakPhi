"""Riconoscimento dei database UniClust30 / UniRef30 per HHblits, in qualunque versione e formato.

Formati riconosciuti (tutti nella stessa cartella, con lo stesso prefisso):
  - HH-suite 3 (UniRef30 2020-2023, uniclust30 2018_08):  <p>_a3m.ffdata/.ffindex, <p>_cs219.ffdata/.ffindex,
    <p>_hhm.ffdata/.ffindex (facoltativi: senza, HHblits calcola i profili dagli allineamenti);
  - HH-suite 2 (uniclust30 2016-2017):  <p>_a3m_db/.index, <p>_hhm_db/.index, <p>.cs219 (+ .cs219.sizes).
    Il file cs219 di HH-suite 2 non è leggibile da HHblits 3: si ricrea una volta dagli allineamenti
    (cstranslate) e si conserva nel volume /cache.

Più versioni possono stare sullo stesso disco: si usa quella scelta in BioLoom, altrimenti la più recente.
Per ogni calcolo si prepara una "vista" con i nomi standard (collegamenti simbolici), senza toccare il disco.

Copia locale: con Docker Desktop (Windows, macOS) il disco arriva nel container attraverso la condivisione
di file (9p, virtiofs), veloce a blocchi grandi ma molto lenta per le letture piccole con cui HHblits scorre
gli indici (misurati 1,8 MB/s contro 40-60 MB/s). Gli indici e il cs219 (qualche GB, contro le centinaia del
database) si possono copiare una volta nel volume /cache di ogni computer: la vista li usa al posto di quelli
sul disco, mentre i grandi a3m/hhm.ffdata restano sul disco e se ne leggono solo le voci trovate.

Memoria: HHblits tiene in memoria gli indici letti (misurati 3,8 GB per gli 1,36 GB di indici di UniRef30_2020_06)
e mappa il cs219 dal file. Nel prefiltro con gap lo legge in ordine sparso: se il cs219 non resta tutto nella
cache (6 GB per UniRef30_2020_06) lo rilegge di continuo, decine di GB in pochi minuti, e il calcolo si trascina.
Servono quindi indici + cs219 + margine; con meno il calcolo parte ma è lento, e lo si dice prima di partire.
Modulo in sola libreria standard (Python 3.6 dell'immagine QMEAN), testato anche fuori dal container.
"""

import io
import json
import os
import re
import shutil
import threading
import time

SOURCES = [p for p in os.environ.get("BIOLOOM_UNICLUST_SOURCES", "/uniclust_src:/uniclust30").split(":") if p]
CACHE = os.environ.get("BIOLOOM_UNICLUST_CACHE", "/cache")
VIEWS = os.environ.get("BIOLOOM_UNICLUST_VIEWS", "/tmp/uniclust_views")
MAX_DEPTH = 4

A3M_PAIRS = [("_a3m.ffdata", "_a3m.ffindex"), ("_a3m_db", "_a3m_db.index")]
HHM_PAIRS = [("_hhm.ffdata", "_hhm.ffindex"), ("_hhm_db", "_hhm_db.index")]
CS219_PAIR = ("_cs219.ffdata", "_cs219.ffindex")
LEGACY_CS219 = ".cs219"
INDEX_MARKERS = ("_a3m.ffindex", "_a3m_db.index")
ARCHIVE = re.compile(r"(uniclust|uniref|hhsuite|hh-suite).*\.(tar\.gz|tgz|tar|zip|tar\.zst)$", re.IGNORECASE)
MMSEQS = re.compile(r"(uniclust|uniref).*\.dbtype$", re.IGNORECASE)
VERSION = re.compile(r"(19|20)(\d{2})[_\-.]?(\d{2})")
SKIP_DIRS = {"$RECYCLE.BIN", "System Volume Information", ".Trash", ".Trashes", ".Spotlight-V100", ".fseventsd"}
LOCAL_DIR = "local"
LOCAL_MARGIN = 2 * 1024 ** 3  # spazio da lasciare libero nel volume dopo la copia
MOUNTS = "/proc/mounts"
MEMINFO = "/proc/meminfo"
CGROUP_LIMITS = ("/sys/fs/cgroup/memory.max", "/sys/fs/cgroup/memory/memory.limit_in_bytes")
GB, MB = 1024.0 ** 3, 1024.0 ** 2  # unità binarie, come nell'interfaccia
HHBLITS_INDEX_FACTOR = 3  # memoria per byte di indice letto (strutture ffindex e nomi delle sequenze)
HHBLITS_OVERHEAD = 1024 ** 3  # profilo, allineamenti dei risultati, OpenStructure
# filesystem condivisi o di rete: letture piccole lente, conviene la copia locale degli indici
SHARED_FS = {"9p", "virtiofs", "fuse.virtiofs", "grpcfuse", "fuse.grpcfuse", "fakeowner", "osxfs", "fuse.osxfs",
             "drvfs", "cifs", "smb3", "smbfs", "nfs", "nfs4", "fuse.sshfs"}


class Cancelled(Exception):
    pass


def _size(path):
    try:
        return os.path.getsize(path)
    except OSError:
        return 0


def _mtime(path):
    try:
        return int(os.stat(path).st_mtime)
    except OSError:
        return 0


def filesystem(path):
    """Tipo di filesystem del punto di montaggio che contiene `path` (None fuori da Linux)."""
    best, fstype = "", None
    try:
        with io.open(MOUNTS, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                parts = line.split()
                if len(parts) < 3:
                    continue
                mnt = parts[1].replace("\\040", " ")
                if (path == mnt or path.startswith(mnt.rstrip("/") + "/")) and len(mnt) >= len(best):
                    best, fstype = mnt, parts[2]
    except OSError:
        return None
    return fstype


def memory():
    """Memoria disponibile per il container: {"total", "swap"} in byte (None fuori da Linux)."""
    values = {}
    try:
        with io.open(MEMINFO, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                key, _, rest = line.partition(":")
                if key in ("MemTotal", "SwapTotal"):
                    values[key] = int(rest.split()[0]) * 1024
    except (OSError, ValueError, IndexError):
        return None
    total = values.get("MemTotal")
    if not total:
        return None
    for path in CGROUP_LIMITS:
        try:
            with io.open(path, encoding="utf-8", errors="replace") as fh:
                limit = fh.read().strip()
        except OSError:
            continue
        if limit.isdigit() and 0 < int(limit) < total:
            total = int(limit)
        break
    return {"total": total, "swap": values.get("SwapTotal", 0)}


def memory_needed(db):
    """Memoria minima di HHblits con questo database (byte): gli indici letti più il resto del programma."""
    indexes = sum(_size(pair[1]) for pair in (db.get("a3m"), db.get("hhm_files"), db.get("cs219")) if pair)
    return indexes * HHBLITS_INDEX_FACTOR + HHBLITS_OVERHEAD


def memory_recommended(db):
    """Memoria perché anche il cs219 resti nella cache: senza, HHblits lo rilegge di continuo (byte)."""
    return memory_needed(db) + (_size(db["cs219"][0]) if db.get("cs219") else 0)


def memory_fit(db, mem):
    """ok | swap (parte ma lento: memoria al limite) | insufficient | None (sconosciuto)."""
    if not mem or not db.get("a3m"):
        return None
    # 10% di margine per gli altri container e il kernel. Misurato con UniRef30_2020_06 su 6,1 GB: lo swap si è
    # riempito, il cs219 veniva riletto a 1,4 GB/s e le cartelle condivise davano "Cannot allocate memory"
    if memory_recommended(db) <= mem["total"] * 0.9:
        return "ok"
    if memory_needed(db) <= mem["total"] + mem["swap"]:
        return "swap"
    return "insufficient"


def num(value, decimals=1):
    """Numero con la virgola decimale, come nell'interfaccia."""
    return ("%.*f" % (decimals, value)).replace(".", ",")


def memory_message(db, mem):
    fit = memory_fit(db, mem)
    if fit in (None, "ok"):
        return None
    needed = memory_needed(db) / GB
    have = "Docker dispone di %s GB di memoria e %s GB di swap" % (num(mem["total"] / GB), num(mem["swap"] / GB))
    if fit == "swap":
        return ("memoria al limite per %s: HHblits usa circa %s GB per gli indici e il cs219 (%s GB) non resta "
                "in memoria, quindi viene riletto di continuo e il calcolo può durare ore; %s. Perché sia veloce "
                "servono circa %s GB assegnati a Docker."
                % (db["name"], num(needed), num(_size(db["cs219"][0]) / GB if db.get("cs219") else 0), have,
                   num(memory_recommended(db) / GB / 0.9 + 0.5, 0)))
    return ("memoria insufficiente per %s: HHblits ne richiede circa %s GB e %s. Aumenta la memoria assegnata "
            "a Docker oppure usa una versione più piccola del database." % (db["name"], num(needed), have))


def _pair(base, pairs):
    """Prima coppia (dati, indice) presente e non vuota tra i nomi possibili."""
    for data_suffix, index_suffix in pairs:
        data, index = base + data_suffix, base + index_suffix
        if _size(data) > 0 and _size(index) > 0:
            return data, index
    return None


def index_problem(data, index):
    """Controllo rapido di coerenza: le voci campione dell'indice devono cadere dentro il file dati.

    L'indice è ordinato per nome e non per posizione: si controllano il primo e l'ultimo blocco, che
    bastano a riconoscere le copie interrotte a metà (file dati troncato) senza leggere gigabyte.
    """
    size = _size(data)
    try:
        with open(index, "rb") as fh:
            head = fh.read(8192)
            fh.seek(0, 2)
            end = fh.tell()
            fh.seek(max(0, end - 8192))
            tail = fh.read()
    except OSError as exc:
        return "indice non leggibile: %s" % exc
    lines = head.decode("ascii", "replace").splitlines()[:-1] + tail.decode("ascii", "replace").splitlines()[1:]
    checked = 0
    for line in lines:
        parts = line.split("\t")
        if len(parts) < 3:
            continue
        try:
            offset, length = int(parts[1]), int(parts[2])
        except ValueError:
            return "indice illeggibile (formato inatteso)"
        checked += 1
        if offset + length > size:
            return "file dati incompleto (copia interrotta?): %s" % os.path.basename(data)
    if not checked:
        return "indice vuoto o illeggibile: %s" % os.path.basename(index)
    return None


def _version(name):
    m = VERSION.search(name)
    return "%s%s-%s" % (m.group(1), m.group(2), m.group(3)) if m else None


def _family(name):
    low = name.lower()
    if "uniref30" in low or "uniref" in low:
        return "UniRef30"
    if "uniclust" in low:
        return "UniClust30"
    return "HH-suite"


def safe_id(db_id):
    value = re.sub(r"[^A-Za-z0-9_.-]+", "_", db_id).strip("_")[:120]
    return value if value.strip(".") else "db"


def _stamp(a3m_index):
    try:
        st = os.stat(a3m_index)
        return {"size": st.st_size, "mtime": int(st.st_mtime)}
    except OSError:
        return None


def converted_cs219(db_id, name, a3m_index):
    """cs219 ricreato per HH-suite 3 (volume /cache), se ancora valido per questo database."""
    folder = os.path.join(CACHE, safe_id(db_id))
    base = os.path.join(folder, name)
    pair = _pair(base, [CS219_PAIR])
    if not pair:
        return None
    try:
        with io.open(os.path.join(folder, "stamp.json"), encoding="utf-8") as fh:
            stamp = json.load(fh)
    except (OSError, ValueError):
        return None
    return pair if stamp == _stamp(a3m_index) else None


def _walk(root):
    root_depth = root.rstrip(os.sep).count(os.sep)
    for dirpath, dirnames, filenames in os.walk(root, onerror=lambda _e: None):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and not d.startswith(".")]
        if dirpath.rstrip(os.sep).count(os.sep) - root_depth >= MAX_DEPTH:
            dirnames[:] = []
        yield dirpath, filenames


def discover():
    """Tutti i database HH-suite trovati nelle sorgenti montate, più archivi e formati non utilizzabili."""
    found, archives, unsupported, seen = [], [], [], set()
    for src_index, root in enumerate(SOURCES):
        if not os.path.isdir(root):
            continue
        for dirpath, filenames in _walk(root):
            for fname in filenames:
                full = os.path.join(dirpath, fname)
                rel = os.path.relpath(full, root)
                if ARCHIVE.search(fname):
                    archives.append({"file": rel, "size": _size(full)})
                    continue
                if MMSEQS.search(fname):
                    unsupported.append({"file": rel, "reason": "database in formato MMseqs2 (ColabFold): HHblits "
                                                              "richiede la versione HH-suite (…_hhsuite.tar.gz)"})
                    continue
                marker = next((m for m in INDEX_MARKERS if fname.endswith(m)), None)
                if marker is None:
                    continue
                name = fname[:-len(marker)]
                base = os.path.join(dirpath, name)
                if base in seen:
                    continue
                seen.add(base)
                rel_base = os.path.relpath(base, root).replace(os.sep, "/")
                db_id = rel_base if src_index == 0 else "%d:%s" % (src_index, rel_base)
                found.append(describe(db_id, base, name))
    found.sort(key=lambda d: (d["state"] == "ready", d["version"] or "", d["size"]), reverse=True)
    return {"databases": found, "archives": archives, "unsupported": unsupported}


def describe(db_id, base, name):
    problems = []
    a3m = _pair(base, A3M_PAIRS)
    hhm = _pair(base, HHM_PAIRS)
    cs219 = _pair(base, [CS219_PAIR])
    cs219_source = "database" if cs219 else None
    if a3m is None:
        problems.append("allineamenti (a3m) mancanti o vuoti")
    else:
        problem = index_problem(*a3m)
        if problem:
            problems.append(problem)
        if cs219 is None:
            cs219 = converted_cs219(db_id, name, a3m[1])
            cs219_source = "converted" if cs219 else None
    if cs219 is not None:
        problem = index_problem(*cs219)
        if problem:
            problems.append(problem)
    if hhm is not None and index_problem(*hhm):
        hhm = None  # HMM danneggiati: HHblits li ricalcola dagli allineamenti
    legacy_cs219 = _size(base + LEGACY_CS219) > 0
    if problems:
        state = "incomplete"
    elif cs219 is None:
        state = "needs_conversion"
    else:
        state = "ready"
    files = [p for pair in (a3m, hhm, cs219) if pair for p in pair]
    fstype = filesystem(base)
    db = {
        "id": db_id, "name": name, "family": _family(name), "version": _version(name),
        "dir": os.path.dirname(base), "state": state, "problems": problems,
        "format": "HH-suite 2" if a3m and a3m[0].endswith("_a3m_db") else "HH-suite 3",
        "hhm": hhm is not None, "cs219_source": cs219_source, "legacy_cs219": legacy_cs219,
        "size": sum(_size(p) for p in files),
        "filesystem": fstype, "shared": fstype in SHARED_FS,
        "a3m": a3m, "hhm_files": hhm, "cs219": cs219,
    }
    local = local_state(db) if a3m else {"state": "none", "size": 0, "used": 0, "files": {}}
    db["local"] = {k: v for k, v in local.items() if k != "files"}
    db["local_files"] = local["files"]
    return db


def public(db):
    """Descrizione senza percorsi interni, per l'API."""
    return {k: v for k, v in db.items() if k not in ("a3m", "hhm_files", "cs219", "dir", "local_files")}


def choose(databases, prefer=None):
    """Il database richiesto se utilizzabile, altrimenti il più recente pronto."""
    ready = [d for d in databases if d["state"] == "ready"]
    if prefer:
        for d in databases:
            if d["id"] == prefer or d["name"] == prefer:
                return d if d["state"] == "ready" else None
    return ready[0] if ready else None


def _link(target, link):
    if os.path.lexists(link):
        os.remove(link)
    os.symlink(target, link)


def prepare_view(db, folder=None):
    """Cartella con i nomi standard HH-suite 3 (collegamenti simbolici) e prefisso da passare a HHblits.
    `folder`: cartella propria del calcolo (calcoli contemporanei: una vista condivisa ricreata da un calcolo che parte
    toglierebbe i collegamenti sotto un HHblits in corso); senza, quella del database in VIEWS."""
    folder = folder or os.path.join(VIEWS, safe_id(db["id"]))
    shutil.rmtree(folder, ignore_errors=True)
    os.makedirs(folder)
    base = os.path.join(folder, db["name"])
    local = db.get("local_files") or {}
    for kind, pair in (("a3m", db["a3m"]), ("cs219", db["cs219"]), ("hhm", db["hhm_files"])):
        if pair:
            for ext, source in (("ffdata", pair[0]), ("ffindex", pair[1])):
                fname = "%s_%s.%s" % (db["name"], kind, ext)
                _link(local.get(fname, source), os.path.join(folder, fname))
    return base


def _local_folder(db_id):
    return os.path.join(CACHE, safe_id(db_id), LOCAL_DIR)


def _read_json(path):
    try:
        with io.open(path, encoding="utf-8") as fh:
            value = json.load(fh)
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def _write_json(path, value):
    tmp = path + ".tmp"
    with io.open(tmp, "w", encoding="utf-8") as fh:
        json.dump(value, fh)
    os.replace(tmp, path)


def local_plan(db):
    """File da copiare nel volume locale: tutti gli indici (letti a piccoli blocchi) e il cs219 (letto per
    intero dal prefiltro). Gli a3m/hhm.ffdata restano sul disco; il cs219 già ricreato in /cache non si copia."""
    cache = os.path.join(CACHE, "")
    plan = []
    for kind, pair in (("a3m", db["a3m"]), ("hhm", db["hhm_files"]), ("cs219", db["cs219"])):
        if not pair:
            continue
        wanted = [("ffindex", pair[1])] + ([("ffdata", pair[0])] if kind == "cs219" else [])
        for ext, source in wanted:
            if source.startswith(cache):
                continue
            plan.append({"name": "%s_%s.%s" % (db["name"], kind, ext), "src": source,
                         "size": _size(source), "mtime": _mtime(source)})
    return plan


def local_state(db, plan=None):
    """Stato della copia locale: none | ready | stale (il database sul disco è cambiato o la copia è parziale).

    `files` elenca le copie valide (nome nella vista -> percorso): ognuna è confrontata con dimensione e data
    del file sul disco, quindi una copia vecchia non viene mai usata con un database aggiornato.
    """
    plan = local_plan(db) if plan is None else plan
    folder = _local_folder(db["id"])
    saved = _read_json(os.path.join(folder, "local.json"))
    needed = sum(f["size"] for f in plan)
    valid = {}
    for f in plan:
        stamp = saved.get(f["name"]) or {}
        dest = os.path.join(folder, f["name"])
        if stamp.get("size") == f["size"] and stamp.get("mtime") == f["mtime"] and _size(dest) == f["size"]:
            valid[f["name"]] = dest
    if not saved:
        state = "none"
    elif plan and len(valid) == len(plan):
        state = "ready"
    else:
        state = "stale"
    return {"state": state, "size": needed, "used": sum(_size(p) for p in valid.values()), "files": valid}


def copy_local(db, report=None, cancelled=None, chunk=16 * 1024 * 1024):
    """Copia indici e cs219 nel volume locale, a blocchi grandi; riprende saltando i file già copiati."""
    plan = local_plan(db)
    if not plan:
        raise RuntimeError("niente da copiare per %s" % db["name"])
    folder = _local_folder(db["id"])
    os.makedirs(folder, exist_ok=True)
    current = local_state(db, plan)["files"]
    total = sum(f["size"] for f in plan)
    missing = sum(f["size"] for f in plan if f["name"] not in current)
    reusable = sum(_size(os.path.join(folder, f["name"])) for f in plan if f["name"] not in current)
    free = shutil.disk_usage(folder).free + reusable
    if missing + LOCAL_MARGIN > free:
        raise RuntimeError("spazio insufficiente nel volume Docker: servono %s GB, liberi %s GB"
                           % (num(missing / GB), num(max(0, free - LOCAL_MARGIN) / GB)))
    saved = _read_json(os.path.join(folder, "local.json"))
    done = 0
    for f in plan:
        dest = os.path.join(folder, f["name"])
        if f["name"] in current:
            done += f["size"]
            continue
        saved.pop(f["name"], None)
        tmp = dest + ".part"
        try:
            with open(f["src"], "rb") as src, open(tmp, "wb") as out:
                while True:
                    if cancelled and cancelled():
                        raise Cancelled()
                    block = src.read(chunk)
                    if not block:
                        break
                    out.write(block)
                    done += len(block)
                    if report:
                        report(done, total, f["name"])
            if _size(tmp) != f["size"] or _mtime(f["src"]) != f["mtime"]:
                raise RuntimeError("%s è cambiato durante la copia" % os.path.basename(f["src"]))
            os.replace(tmp, dest)
        finally:
            if os.path.exists(tmp):
                os.remove(tmp)
        saved[f["name"]] = {"size": f["size"], "mtime": f["mtime"], "src": f["src"]}
        _write_json(os.path.join(folder, "local.json"), saved)
    return local_state(db, plan)


def remove_local(db_id):
    """Elimina la copia locale di un database (anche di uno non più collegato). Restituisce i byte liberati."""
    folder = _local_folder(db_id)
    if os.path.dirname(os.path.dirname(folder)) != os.path.normpath(CACHE) or not os.path.isdir(folder):
        return 0
    freed = sum(_size(os.path.join(folder, n)) for n in os.listdir(folder))
    shutil.rmtree(folder, ignore_errors=True)
    try:
        os.rmdir(os.path.dirname(folder))  # solo se vuota (resta se c'è un cs219 ricreato)
    except OSError:
        pass
    return freed


def local_copies():
    """Copie locali presenti nel volume, anche di database non collegati: [{id, size}]."""
    found = []
    try:
        names = sorted(os.listdir(CACHE))
    except OSError:
        return found
    for name in names:
        folder = os.path.join(CACHE, name, LOCAL_DIR)
        if os.path.isdir(folder):
            found.append({"id": name, "size": sum(_size(os.path.join(folder, n)) for n in os.listdir(folder))})
    return found


def conversion_plan(db):
    """Comandi per ricreare il cs219 di HH-suite 3 dagli allineamenti (database HH-suite 2)."""
    if not db["a3m"]:
        raise ValueError("il database non ha allineamenti a3m utilizzabili")
    target_dir = os.path.join(CACHE, safe_id(db["id"]))
    staging = os.path.join(VIEWS, safe_id(db["id"]) + "-conversione")
    hhlib = os.environ.get("HHLIB", "/usr/local")
    return {
        "staging": staging, "target_dir": target_dir, "name": db["name"],
        "stamp": _stamp(db["a3m"][1]),
        "command": ["cstranslate", "-f", "-x", "0.3", "-c", "4", "-I", "a3m",
                    "-i", os.path.join(staging, db["name"] + "_a3m"),
                    "-o", os.path.join(target_dir, "tmp", db["name"] + "_cs219"),
                    "-A", os.path.join(hhlib, "data", "cs219.lib"),
                    "-D", os.path.join(hhlib, "data", "context_data.lib")],
    }


def prepare_conversion(db, plan):
    shutil.rmtree(plan["staging"], ignore_errors=True)
    os.makedirs(plan["staging"])
    _link(db["a3m"][0], os.path.join(plan["staging"], db["name"] + "_a3m.ffdata"))
    _link(db["a3m"][1], os.path.join(plan["staging"], db["name"] + "_a3m.ffindex"))
    shutil.rmtree(os.path.join(plan["target_dir"], "tmp"), ignore_errors=True)
    os.makedirs(os.path.join(plan["target_dir"], "tmp"))


def finish_conversion(plan):
    tmp = os.path.join(plan["target_dir"], "tmp")
    for suffix in CS219_PAIR:
        os.replace(os.path.join(tmp, plan["name"] + suffix), os.path.join(plan["target_dir"], plan["name"] + suffix))
    with io.open(os.path.join(plan["target_dir"], "stamp.json"), "w", encoding="utf-8") as fh:
        json.dump(plan["stamp"], fh)
    shutil.rmtree(tmp, ignore_errors=True)
    shutil.rmtree(plan["staging"], ignore_errors=True)


class Cache(object):
    """Risultato della scansione riutilizzato per qualche secondo (la scansione legge solo le cartelle).

    Una scansione alla volta: chi arriva mentre un'altra è in corso la aspetta e, se non è troppo vecchia, ne usa il
    risultato. Con `force` vale solo una scansione cominciata dopo la richiesta: tante richieste insieme ne fanno al
    più due, non una a testa (sul server, con decine di calcoli, le scansioni contemporanee bloccavano il worker)."""

    def __init__(self, ttl=30):
        self.ttl, self.at, self.value = ttl, 0.0, None
        self._lock = threading.Lock()
        self._begun = 0  # scansioni cominciate
        self._value_scan = 0  # numero della scansione che ha dato `value`

    def get(self, force=False):
        before = self._begun  # scansioni cominciate prima della richiesta
        with self._lock:
            stale = self._value_scan <= before if force else time.time() - self.at > self.ttl
            if self.value is None or stale:
                self._begun += 1
                started = time.time()
                self.value = discover()
                self.at, self._value_scan = started, self._begun  # età dalla partenza della scansione
            return self.value
