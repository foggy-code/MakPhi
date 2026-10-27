"""Flusso dei dati: lavoro nello scratch, salvataggio definitivo, pulizia, blocco della cartella dati."""

import os
import sys
import threading
import time
import zipfile

import pytest

from makphi.config import Paths, UserSettings
from makphi.core.db import Database, DatabaseInUse
from makphi.core.jobs import JobManager, is_intermediate
from makphi.services.housekeeping import Housekeeping


@pytest.fixture
def env(tmp_path):
    db = Database(tmp_path / "data" / "t.db")
    settings = {"value": UserSettings()}
    mgr = JobManager(db, tmp_path / "data" / "jobs", workers=2, settings_provider=lambda: settings["value"],
                     scratch_dir=tmp_path / "scratch")
    yield mgr, settings, tmp_path
    mgr.shutdown()
    db.close()


def wait(mgr, job_id, timeout=20):
    start = time.time()
    while time.time() - start < timeout:
        job = mgr.get(job_id)
        if job["status"] in ("completed", "failed", "cancelled"):
            return job
        time.sleep(0.05)
    raise AssertionError("timeout")


def _write_outputs(ctx):
    (ctx.workdir / "T.B99990001.pdb").write_text("ATOM\n")
    (ctx.workdir / "T.B99990001.pdb.quality.json").write_text("{}")
    for name in ("T.rsr", "T.sch", "T.ini", "T.D00000001", "T.V99990001", "T.IL00000001.pdb"):
        (ctx.workdir / name).write_text("x")
    (ctx.workdir / "T.BL00010001.pdb").write_text("ATOM\n")  # modello di loop: è un risultato
    (ctx.workdir / "qa").mkdir()
    (ctx.workdir / "qa" / "dope_input.pdb").write_text("copia")
    (ctx.workdir / "qa" / "dope.json").write_text("{}")
    (ctx.tmp_dir / "scratch.txt").write_text("temporaneo")


def test_intermediate_patterns():
    assert is_intermediate("A.rsr") and is_intermediate("A.D00000001") and is_intermediate("A.V99990001")
    assert is_intermediate("A.IL00000001.pdb") and is_intermediate("dope_input.pdb")
    assert not is_intermediate("A.B99990001.pdb") and not is_intermediate("A.BL00010001.pdb")
    assert not is_intermediate("alignment.ali") and not is_intermediate("models.json")


def test_work_in_scratch_then_commit_without_intermediates(env):
    mgr, _settings, tmp = env
    seen = {}

    def handler(ctx, params):
        seen["workdir"] = ctx.workdir
        _write_outputs(ctx)
        seen["files_during_run"] = {f["path"] for f in mgr.list_files(ctx.job_id)}
        return {"ok": True}

    mgr.register("demo", handler)
    job = wait(mgr, mgr.submit("demo", {})["id"])
    assert job["status"] == "completed"
    assert str(seen["workdir"]).startswith(str(tmp / "scratch"))
    assert "T.B99990001.pdb" in seen["files_during_run"]  # visibili anche mentre il job gira
    final = mgr.workdir(job["id"])
    names = {f["path"] for f in mgr.list_files(job["id"])}
    assert {"T.B99990001.pdb", "T.BL00010001.pdb", "qa/dope.json", "result.json", "params.json", "job.log"} <= names
    assert not names & {"T.rsr", "T.sch", "T.ini", "T.D00000001", "T.V99990001", "T.IL00000001.pdb",
                        "qa/dope_input.pdb"}
    assert not (final / ".tmp").exists()
    assert not mgr.live_dir(job["id"]).exists()  # scratch svuotato


def test_keep_intermediates_setting(env):
    mgr, settings, _ = env
    settings["value"] = UserSettings(keep_intermediates=True)
    mgr.register("demo", lambda ctx, p: _write_outputs(ctx) or {})
    job = wait(mgr, mgr.submit("demo", {})["id"])
    names = {f["path"] for f in mgr.list_files(job["id"])}
    assert "T.rsr" in names and "qa/dope_input.pdb" in names


def test_failed_job_keeps_everything_for_diagnosis(env):
    mgr, _, _ = env

    def handler(ctx, params):
        _write_outputs(ctx)
        raise RuntimeError("guasto")

    mgr.register("bad", handler)
    job = wait(mgr, mgr.submit("bad", {})["id"])
    assert job["status"] == "failed"
    names = {f["path"] for f in mgr.list_files(job["id"])}
    assert "T.rsr" in names and "T.B99990001.pdb" in names
    assert not mgr.live_dir(job["id"]).exists()


def test_progressive_commit_of_sub_contexts(env):
    mgr, _, _ = env
    checkpoint = threading.Event()
    release = threading.Event()

    def handler(ctx, params):
        sub = ctx.sub("item", ctx.workdir / "items" / "0000_A")
        (sub.workdir / "A.B99990001.pdb").write_text("ATOM\n")
        (sub.workdir / "A.rsr").write_text("x")
        sub.commit()
        checkpoint.set()
        release.wait(10)
        return {}

    mgr.register("pipe", handler)
    job_id = mgr.submit("pipe", {})["id"]
    assert checkpoint.wait(10)
    final_file = mgr.workdir(job_id) / "items" / "0000_A" / "A.B99990001.pdb"
    assert final_file.exists()  # già nei risultati mentre il job è ancora in corso
    assert not (mgr.workdir(job_id) / "items" / "0000_A" / "A.rsr").exists()
    assert not (mgr.live_dir(job_id) / "items" / "0000_A").exists()
    assert mgr.file_path(job_id, "items/0000_A/A.B99990001.pdb") == final_file
    release.set()
    assert wait(mgr, job_id)["status"] == "completed"


def test_list_files_tolerates_files_vanishing(env, monkeypatch):
    """Il job sposta o cancella file mentre l'interfaccia li elenca: niente errore 500."""
    mgr, _, _ = env
    job_id = "feedfacefeedface"
    root = mgr.workdir(job_id)
    root.mkdir(parents=True)
    (root / "ok.pdb").write_text("ATOM\n")
    (root / "sparito.pdb").write_text("ATOM\n")
    real_stat = os.stat

    def flaky_stat(path, *args, **kwargs):
        if str(path).endswith("sparito.pdb"):
            raise FileNotFoundError(path)
        return real_stat(path, *args, **kwargs)

    monkeypatch.setattr("makphi.core.jobs.os.stat", flaky_stat)
    assert [f["path"] for f in mgr.list_files(job_id)] == ["ok.pdb"]


def test_recover_purges_scratch(env):
    mgr, settings, tmp = env
    orphan = tmp / "scratch" / "jobs" / "deadbeefdeadbeef"
    orphan.mkdir(parents=True)
    (orphan / "big.rsr").write_text("x" * 1000)
    mgr.recover()
    assert not orphan.exists()


def test_second_instance_on_same_data_dir_is_refused(tmp_path):
    first = Database(tmp_path / "makphi.db")
    try:
        with pytest.raises(DatabaseInUse):
            Database(tmp_path / "makphi.db", busy_timeout=0.2)
    finally:
        first.close()
    Database(tmp_path / "makphi.db").close()  # chiusa la prima istanza, la cartella torna libera


def test_housekeeping_removes_only_expired_files(tmp_path):
    paths = Paths(tmp_path / "data", scratch_dir=tmp_path / "scratch")
    paths.ensure()
    db = Database(paths.db)
    mgr = JobManager(db, paths.jobs, 1, UserSettings, scratch_dir=paths.scratch)
    try:
        old = time.time() - 40 * 86400
        stale_upload = paths.uploads / "vecchio.pdb"
        fresh_upload = paths.uploads / "nuovo.pdb"
        stale_cache = paths.cache / "uniprot" / "a.json"
        fresh_cache = paths.cache / "uniprot" / "b.json"
        stale_tmp = paths.scratch_tmp / "download_x.zip"
        orphan = paths.scratch / "jobs" / "0123456789abcdef"
        for f in (stale_upload, fresh_upload, stale_cache, fresh_cache, stale_tmp):
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text("x" * 100)
        orphan.mkdir(parents=True)
        for f in (stale_upload, stale_cache, stale_tmp):
            os.utime(f, (old, old))
        hk = Housekeeping(paths, UserSettings, mgr)
        report = hk.run()
        assert not stale_upload.exists() and fresh_upload.exists()
        assert not stale_cache.exists() and fresh_cache.exists()
        assert not stale_tmp.exists() and not orphan.exists()
        assert report["uploads"] == 1 and report["cache"] == 1 and report["freed_bytes"] >= 300
        os.utime(fresh_upload, (old, old))
        Housekeeping(paths, lambda: UserSettings(uploads_retention_days=0), mgr).run()
        assert fresh_upload.exists()  # 0 = conserva sempre
        storage = hk.storage()
        assert {"jobs", "uploads", "cache", "databases", "licenses", "database_file", "scratch"} <= storage.keys()
    finally:
        mgr.shutdown()
        db.close()


@pytest.mark.skipif(os.name != "posix", reason="gruppi di processi solo su POSIX")
def test_cancel_kills_child_processes(env):
    mgr, _, tmp = env
    marker = tmp / "child.pid"
    started = threading.Event()
    script = ("import subprocess, sys, time; "
              f"p = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)']); "
              f"open(r'{marker}', 'w').write(str(p.pid)); print('ok', flush=True); time.sleep(60)")

    def handler(ctx, params):
        started.set()
        ctx.run([sys.executable, "-c", script])
        return {}

    mgr.register("tree", handler)
    job_id = mgr.submit("tree", {})["id"]
    started.wait(5)
    for _ in range(100):
        if marker.exists() and marker.read_text():
            break
        time.sleep(0.05)
    child = int(marker.read_text())
    mgr.cancel(job_id)
    assert wait(mgr, job_id)["status"] == "cancelled"
    for _ in range(50):
        try:
            os.kill(child, 0)
        except ProcessLookupError:
            break
        time.sleep(0.1)
    else:
        pytest.fail("il processo figlio è sopravvissuto all'annullamento")


def test_archive_and_pipeline_zip_use_temp_files(client, tmp_path):
    job = client.post("/api/align", json={"mode": "pairwise", "records": [
        {"name": "a", "sequence": "MKTAYIAKQR"}, {"name": "b", "sequence": "MKTAYIAKQK"}]}).json()
    from tests.conftest import wait_job

    assert wait_job(client, job["id"])["status"] == "completed"
    resp = client.get(f"/api/jobs/{job['id']}/archive")
    assert resp.status_code == 200
    import io

    names = zipfile.ZipFile(io.BytesIO(resp.content)).namelist()
    assert "alignment.fasta" in names and "result.json" in names
    scratch_tmp = client.app.state.core.paths.scratch_tmp
    assert not list(scratch_tmp.glob("download_*"))  # il file temporaneo è stato eliminato dopo l'invio
    storage = client.get("/api/system/storage").json()
    assert storage["jobs"] > 0
    assert client.post("/api/system/cleanup").status_code == 200
