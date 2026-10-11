"""Database locali: pdbaa (BLAST+), indice MMseqs2, UniClust30/QMTL (servizio QMEAN locale).

- pdbaa sta in `data/databases/pdbaa/` (viaggia con la cartella dati); si aggiorna da NCBI con un job.
- L'indice MMseqs2 si crea da pdbaa (`data/databases/pdbaa_mmseqs/`).
- UniClust30 e QMTL restano sul disco esterno: li monta l'avviatore nel container `qmean`.
I file intermedi (download, FASTA estratto, indici temporanei) stanno nello scratch del job.

Aggiornamento di pdbaa, database MMseqs2 e indice locale si eseguono uno alla volta (risorsa esclusiva `PDBAA_FILES`):
ognuno scrive una cartella che un altro legge per costruire la propria (pdbaa → pdbaa_mmseqs → indice locale). Le
cartelle di lavoro accanto a quelle definitive (stesso disco, per lo scambio con un rename) hanno il nome del job.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tarfile
import time
from pathlib import Path
from typing import Callable, Optional

import httpx

from ..core.errors import AppError, ToolUnavailable
from ..core.jobs import QMEAN_LOCAL, RESOURCE_STAGE, JobContext, JobManager
from ..core.storage import dir_size, remove_tree
from ..engines import qmean_local, remote, search, tools

PDBAA_URL = "https://ftp.ncbi.nlm.nih.gov/blast/db/pdbaa.tar.gz"
# Risorsa esclusiva (core/dispatch.py) dei job che scrivono pdbaa, pdbaa_mmseqs o l'indice locale, o li leggono per
# costruirne un altro. Le risorse valgono dentro un pool (il dispatcher tiene le sue): i tre job stanno tutti in "io",
# e i processi di calcolo che lanciano passano comunque dal budget di CPU condiviso (JobContext.run).
PDBAA_FILES = "pdbaa_files"
RESOURCE_STAGE[PDBAA_FILES] = "In coda: un altro job sta aggiornando pdbaa o il database MMseqs2"


def _info(path: Path) -> dict:
    if not path.exists():
        return {"present": False}
    try:
        return {"present": True, "size": dir_size(path), "updated": time.strftime(
            "%Y-%m-%d %H:%M", time.localtime(max((p.stat().st_mtime for p in path.rglob("*") if p.is_file()),
                                                 default=path.stat().st_mtime)))}
    except OSError:  # es. cartella dati condivisa da Docker Desktop con la memoria esaurita: lo stato resta visibile
        return {"present": True, "size": None, "updated": None}


def _work_dir(final_dir: Path, stage: str, job_id: str) -> Path:
    """Cartella di lavoro di un job accanto a `final_dir` (stesso disco): `<nome>.<stage>-<job>`."""
    return final_dir.with_name(f"{final_dir.name}.{stage}-{job_id}")


def _clear_leftovers(final_dir: Path) -> None:
    """Toglie le cartelle di lavoro lasciate da un job interrotto (arresto, memoria esaurita), anche quelle a nome
    fisso delle versioni precedenti (`<nome>.new`, `<nome>.old`). Si chiama tenendo `PDBAA_FILES`: nessun altro job
    le sta usando."""
    pattern = re.compile(rf"{re.escape(final_dir.name)}\.(new|old)(-[0-9a-f]+)?")
    if not final_dir.parent.is_dir():
        return
    for path in final_dir.parent.iterdir():
        if pattern.fullmatch(path.name) and path.is_dir():
            remove_tree(path)


def _swap_in(new_dir: Path, final_dir: Path, job_id: str) -> None:
    """Sostituisce `final_dir` con `new_dir` (stesso disco: operazione quasi atomica)."""
    old = _work_dir(final_dir, "old", job_id)
    remove_tree(old)
    if final_dir.exists():
        final_dir.rename(old)
    new_dir.rename(final_dir)
    remove_tree(old)


class DatabaseService:
    def __init__(self, jobs: JobManager, databases_dir: Path, local_cache: Path,
                 settings_provider: Optional[Callable] = None) -> None:
        self.jobs = jobs
        self.dir = databases_dir
        self.local_cache = local_cache
        self.settings_provider = settings_provider
        # uno alla volta (PDBAA_FILES), nello stesso pool: con pool diversi l'esclusione non varrebbe
        for kind, handler in (("db_update_pdbaa", self.handle_update_pdbaa),
                              ("db_build_mmseqs", self.handle_build_mmseqs),
                              ("db_index_mmseqs", self.handle_index_mmseqs)):
            jobs.register(kind, handler, pool="io", resource=lambda _p: PDBAA_FILES)
        # conversioni e copie passano dalla stessa coda dei calcoli nel servizio QMEAN (una cosa alla volta):
        # in BioLoom restano in coda, invece di occupare un worker del pool "io" in attesa del servizio
        jobs.register("db_convert_uniclust", self.handle_convert_uniclust, pool="io",
                      resource=lambda _p: QMEAN_LOCAL)
        jobs.register("db_local_uniclust", self.handle_local_uniclust, pool="io", resource=lambda _p: QMEAN_LOCAL)

    def _preferred_uniclust(self) -> str:
        settings = self.settings_provider() if self.settings_provider else None
        return getattr(settings, "qmean_uniclust", "") or ""

    def handle_convert_uniclust(self, ctx: JobContext, p: dict) -> dict:
        """Rende utilizzabile da HHblits 3 un database HH-suite 2 (uniclust30 2016-2017) ricreandone il cs219."""
        database = str(p.get("database") or "").strip()
        if not database:
            raise AppError("Indica il database da convertire.")
        return qmean_local.convert(database, ctx)

    def handle_local_uniclust(self, ctx: JobContext, p: dict) -> dict:
        """Copia su questo computer (o elimina) indici e cs219 di un database UniClust30, per HHblits più veloce."""
        database = str(p.get("database") or "").strip()
        action = str(p.get("action") or "copy")
        if not database or action not in ("copy", "remove"):
            raise AppError("Indica il database e l'operazione (copy o remove).")
        return qmean_local.local_copy(database, action, ctx)

    def status(self) -> dict:
        pdbaa_db = search.local_database(self.dir)
        index_dir = self.local_cache / "mmseqs"
        return {
            "pdbaa": {**_info(self.dir / "pdbaa"), "usable": pdbaa_db is not None,
                      "blast": tools.find("blastp") is not None},
            "mmseqs": {**_info(self.dir / "pdbaa_mmseqs"), "usable": search.mmseqs_database(self.dir) is not None,
                       "tool": tools.find("mmseqs") is not None,
                       "indexed_here": search.mmseqs_local_index(self.dir) is not None,
                       "index_size": dir_size(index_dir) if index_dir.exists() else 0},
            "qmean": qmean_local.status(self._preferred_uniclust()),
        }

    # ------------------------------------------------------------------ pdbaa da NCBI
    def handle_update_pdbaa(self, ctx: JobContext, p: dict) -> dict:
        tmp = ctx.tmp_dir
        archive = tmp / "pdbaa.tar.gz"
        ctx.progress(0.02, "Download di pdbaa da NCBI")
        md5 = hashlib.md5()
        headers = {"User-Agent": remote.USER_AGENT}
        with httpx.stream("GET", PDBAA_URL, headers=headers, timeout=httpx.Timeout(60.0, connect=20.0),
                          follow_redirects=True) as resp:
            if resp.status_code != 200:
                raise AppError(f"NCBI ha risposto {resp.status_code} al download di pdbaa.", code="remote_error")
            total = int(resp.headers.get("content-length") or 0)
            done = 0
            last = 0.0
            with open(archive, "wb") as fh:
                for chunk in resp.iter_bytes(1 << 20):
                    ctx.check()
                    fh.write(chunk)
                    md5.update(chunk)
                    done += len(chunk)
                    if total and time.monotonic() - last > 1:
                        ctx.progress(0.02 + 0.7 * done / total, f"Download di pdbaa: {done >> 20} di {total >> 20} MB")
                        last = time.monotonic()
        expected = httpx.get(PDBAA_URL + ".md5", headers=headers, timeout=30, follow_redirects=True).text.split()
        if expected and expected[0] != md5.hexdigest():
            raise AppError("Il file scaricato è corrotto (MD5 diverso): riprova.", code="checksum")
        ctx.progress(0.75, "Estrazione del database")
        _clear_leftovers(self.dir / "pdbaa")
        new_dir = _work_dir(self.dir / "pdbaa", "new", ctx.job_id)
        new_dir.mkdir(parents=True)
        try:
            with tarfile.open(archive) as tar:
                for member in tar.getmembers():
                    if not member.isfile() or "/" in member.name or member.name.startswith("."):
                        continue  # solo i file del database, senza percorsi
                    tar.extract(member, new_dir, filter="data")
            archive.unlink(missing_ok=True)
            if not (any(new_dir.glob("pdbaa*.pin")) or any(new_dir.glob("pdbaa*.pal"))):
                raise AppError("L'archivio scaricato non contiene un database pdbaa valido.")
        except BaseException:  # un'estrazione a metà non resta nella cartella dati
            remove_tree(new_dir)
            raise
        _swap_in(new_dir, self.dir / "pdbaa", ctx.job_id)
        ctx.log(f"pdbaa aggiornato ({dir_size(self.dir / 'pdbaa') >> 20} MB)")
        return {"database": "pdbaa", **_info(self.dir / "pdbaa")}

    # ------------------------------------------------------------------ indice MMseqs2
    def handle_build_mmseqs(self, ctx: JobContext, p: dict) -> dict:
        mmseqs, blastdbcmd = tools.find("mmseqs"), tools.find("blastdbcmd")
        if not mmseqs or not blastdbcmd:
            raise ToolUnavailable("Servono MMseqs2 e blastdbcmd (BLAST+).")
        db = search.local_database(self.dir)
        if db is None:
            raise ToolUnavailable("Database pdbaa assente: scaricalo prima (Impostazioni → Database).")
        tmp = ctx.tmp_dir
        fasta = tmp / "pdbaa.fasta"
        ctx.progress(0.05, "Estrazione delle sequenze da pdbaa")
        # lettura e scrittura di file: processo di I/O, fuori dal budget di CPU
        res = ctx.run([blastdbcmd, "-db", str(db), "-entry", "all", "-out", str(fasta)], log_output=False,
                      cpu=False)
        if res.returncode != 0 or not fasta.exists():
            raise AppError(f"blastdbcmd non riuscito: {res.output[-400:]}")
        # il database (≈ 140 MB) si crea nello scratch e poi si copia nella cartella dati
        build = tmp / "pdbaa_mmseqs"
        build.mkdir(parents=True)
        ctx.progress(0.35, "Creazione del database MMseqs2")
        res = ctx.run([mmseqs, "createdb", str(fasta), str(build / "pdbaa")], log_output=False)
        if res.returncode != 0:
            raise AppError(f"mmseqs createdb non riuscito: {res.output[-400:]}")
        fasta.unlink(missing_ok=True)
        _clear_leftovers(self.dir / "pdbaa_mmseqs")
        staged = _work_dir(self.dir / "pdbaa_mmseqs", "new", ctx.job_id)
        try:
            shutil.copytree(build, staged)
        except BaseException:  # copia a metà (disco pieno): non resta nella cartella dati
            remove_tree(staged)
            raise
        remove_tree(build)
        _swap_in(staged, self.dir / "pdbaa_mmseqs", ctx.job_id)
        result = {"database": "pdbaa_mmseqs", **_info(self.dir / "pdbaa_mmseqs")}
        if p.get("index", True):
            result["local_index"] = self._build_local_index(ctx, (0.6, 0.98))
        return result

    # ------------------------------------------------------------------ indice veloce su questo computer
    def handle_index_mmseqs(self, ctx: JobContext, p: dict) -> dict:
        return {"local_index": self._build_local_index(ctx, (0.05, 0.98))}

    def _build_local_index(self, ctx: JobContext, progress: tuple[float, float]) -> dict:
        """Copia il database nel volume locale e ci crea l'indice k-mer (≈ 10 volte il database)."""
        mmseqs = tools.find("mmseqs")
        db = search.mmseqs_database(self.dir)
        if not mmseqs or db is None:
            raise ToolUnavailable("Serve prima il database MMseqs2 (Prepara MMseqs2).")
        ctx.progress(progress[0], "Copia del database MMseqs2 su questo computer")
        folder = self.local_cache / "mmseqs"
        _clear_leftovers(folder)
        staged = _work_dir(folder, "new", ctx.job_id)
        try:
            shutil.copytree(db.parent, staged)
            ctx.progress((progress[0] + progress[1]) / 2, "Indice k-mer MMseqs2 (ricerche più veloci)")
            # un thread per slot libero del budget di CPU condiviso (da 1 ai processori − 1)
            res = ctx.run(lambda threads: [mmseqs, "createindex", str(staged / "pdbaa"),
                                           str(ctx.tmp_dir / "mmseqs_idx"), "--threads", str(threads)],
                          log_output=False, cpu=max(1, (os.cpu_count() or 2) - 1))
            shutil.rmtree(ctx.tmp_dir / "mmseqs_idx", ignore_errors=True)
            if res.returncode != 0:
                raise AppError(f"mmseqs createindex non riuscito: {res.output[-400:]}")
        except BaseException:  # errore o annullamento: la copia a metà non resta nel volume locale
            remove_tree(staged)
            raise
        (staged / "stamp.json").write_text(json.dumps(search.mmseqs_stamp(db)), encoding="utf-8")
        _swap_in(staged, folder, ctx.job_id)
        return {"size": dir_size(folder), "path": "volume locale"}
