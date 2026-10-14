"""Pipeline grandi (server): limiti, processi di appoggio dell'analisi di qualità, log dei programmi per elemento,
parametri in cache ed elenco dei file limitato.

- limite di 100.000 sequenze per pipeline e parallelismo fino ai job delle Impostazioni (non più 8);
- `core/offload` + `domain/qa_steps`: con il pool attivo (processi veri, avviati con "spawn") l'analisi dà lo stesso
  rapporto della versione in serie; con un pool che fallisce i passi si rifanno in casa, con lo stesso rapporto;
- `JobContext.sub(output_log=...)`: l'output dei programmi va nel file e non nel log del job;
- `GET /api/jobs/{id}` con più file del limite: elenco parziale e `files_truncated`.
"""

from __future__ import annotations

import sys
from concurrent.futures import Future

import pytest

from bioloom.core import offload
from bioloom.core.errors import AppError
from bioloom.domain import clashes, qa_steps, ramachandran, structure
from bioloom.engines import quality
from bioloom.services import pipeline as pipeline_service

from .conftest import DATA, HUMAN_UB, wait_job
from .test_parallel_quality import log_text, manager, run_job  # noqa: F401 (fixture)


# ------------------------------------------------------------------------------------------ limiti
def test_pipeline_accepts_up_to_100000_sequences():
    many = "".join(f">s{i}\nMKV\n" for i in range(pipeline_service.MAX_SEQUENCES))
    assert len(pipeline_service.PipelineService._records({"fasta": many})) == 100_000
    with pytest.raises(AppError, match="Massimo 100.000 sequenze"):
        pipeline_service.PipelineService._records({"fasta": many + ">oltre\nMKV\n"})


def test_parallel_follows_the_settings_beyond_eight():
    assert pipeline_service.effective_parallel({"parallel": 32}, 64) == 32
    assert pipeline_service.effective_parallel({"parallel": 32}, 12) == 12
    assert pipeline_service.effective_parallel({"parallel": 1000}, 1000) == pipeline_service.MAX_PARALLEL == 256
    assert pipeline_service.effective_parallel({}, 64) == 2


def test_settings_and_api_accept_up_to_256(client):
    r = client.put("/api/settings", json={"max_workers": 256})
    assert r.status_code == 200, r.text
    assert client.get("/api/settings").json()["max_workers"] == 256
    assert client.put("/api/settings", json={"max_workers": 257}).status_code == 400


# ------------------------------------------------------------------------------------------ processi di appoggio
def test_offload_processes_only_with_many_workers():
    assert [offload.processes_for(n) for n in (1, 3, 7, 8, 16, 63, 256)] == [0, 0, 0, 2, 4, 15, 64]


def test_python_steps_match_the_in_process_steps():
    path = DATA / "1UBQ.pdb"
    model = structure.first_model(structure.load_structure(path, "1UBQ"))
    out = qa_steps.python_steps(str(path), "1UBQ")
    assert out == {"Ramachandran": ramachandran.analyze(model), "clash": clashes.analyze(model),
                   "SASA": quality._sasa(model)}


@pytest.fixture
def pool():
    offload.configure(lambda: 8)  # 2 processi di appoggio veri
    yield
    offload.configure(None)


@pytest.mark.parametrize("name", ["1UBQ.pdb", "ubq_dimer.pdb", "ubq_zn.pdb"])
def test_analysis_with_the_process_pool_matches_serial(manager, pool, monkeypatch, name):  # noqa: F811
    used: list[str] = []
    submit = offload.submit

    def spy(fn, *args):
        future = submit(fn, *args)
        assert future is not None  # il pool è attivo
        used.append(args[1])
        return future

    monkeypatch.setattr(offload, "submit", spy)
    path = DATA / name

    def both(ctx):
        par = quality.analyze(path, ctx, with_dope=False)
        ser = quality.analyze(path, ctx, with_dope=False, parallel=False)
        return {"par": par, "ser": ser}

    job = run_job(manager, both)
    assert job["status"] == "completed", job["error"]
    assert job["result"]["par"] == job["result"]["ser"]
    assert used == [path.stem]  # solo l'analisi in parallelo usa il pool


def test_a_failing_pool_falls_back_to_the_main_process(manager, monkeypatch):  # noqa: F811
    def broken(fn, *args):
        future: Future = Future()
        future.set_exception(RuntimeError("processo di appoggio morto"))
        return future

    monkeypatch.setattr(offload, "submit", broken)
    path = DATA / "1UBQ.pdb"
    job = run_job(manager, lambda ctx: {"par": quality.analyze(path, ctx, with_dope=False),
                                        "ser": quality.analyze(path, ctx, with_dope=False, parallel=False)})
    assert job["status"] == "completed", job["error"]
    assert job["result"]["par"] == job["result"]["ser"]


def test_partial_results_from_the_pool_are_completed_locally(manager, monkeypatch):  # noqa: F811
    def only_clash(fn, path, name):
        future: Future = Future()
        future.set_result({"clash": qa_steps.python_steps(path, name)["clash"]})
        return future

    monkeypatch.setattr(offload, "submit", only_clash)
    path = DATA / "ubq_zn.pdb"
    job = run_job(manager, lambda ctx: {"par": quality.analyze(path, ctx, with_dope=False),
                                        "ser": quality.analyze(path, ctx, with_dope=False, parallel=False)})
    assert job["status"] == "completed", job["error"]
    assert job["result"]["par"] == job["result"]["ser"]


# ------------------------------------------------------------------------------------------ log dei programmi
def test_program_output_goes_to_the_item_file_not_to_the_job_log(manager, tmp_path):  # noqa: F811
    out = tmp_path / "elemento" / "programmi.log"

    def fn(ctx):
        sub = ctx.sub("elemento", tmp_path / "elemento", output_log=out)
        sub.run([sys.executable, "-c", "print('riga di MODELLER'); print('seconda riga')"], cpu=False)
        inner = sub.sub("DOPE", tmp_path / "elemento" / "dope")  # i figli ereditano il file
        result = inner.run([sys.executable, "-c", "print('riga del DOPE')"], cpu=False)
        sub.log("messaggio di BioLoom")
        return {"output": result.output}

    job = run_job(manager, fn)
    assert job["status"] == "completed", job["error"]
    assert job["result"]["output"].strip() == "riga del DOPE"  # l'esito del processo ha sempre l'output
    text = out.read_text(encoding="utf-8")
    assert "riga di MODELLER\nseconda riga\n" in text and "riga del DOPE\n" in text
    assert text.count("=====") == 2 and "[elemento] [DOPE] " in text and "(uscita 0)" in text
    log = log_text(manager, job["id"])
    assert "riga di MODELLER" not in log and "riga del DOPE" not in log
    assert "[elemento] messaggio di BioLoom" in log


def test_without_output_log_the_job_log_keeps_the_program_output(manager, tmp_path):  # noqa: F811
    def fn(ctx):
        ctx.sub("elemento", tmp_path / "e").run([sys.executable, "-c", "print('nel log del job')"], cpu=False)

    job = run_job(manager, fn)
    assert job["status"] == "completed", job["error"]
    assert "[elemento] nel log del job" in log_text(manager, job["id"])


# ------------------------------------------------------------------------------------------ parametri ed elenco dei file
def test_pipeline_params_are_read_once(client, monkeypatch):
    core = client.app.state.core
    r = client.post("/api/pipeline", json={"fasta": f">a\n{HUMAN_UB}\n", "template_mode": "fixed",
                                           "templates": [{"id": "NONE", "chains": ["A"]}], "parallel": 1})
    assert r.status_code == 200, r.text
    job_id = r.json()["id"]
    first = core.pipeline._params(job_id)
    calls = []
    original = core.db.query_one
    monkeypatch.setattr(core.db, "query_one", lambda sql, *a: calls.append(sql) or original(sql, *a))
    assert core.pipeline._params(job_id) is first and first["fasta"].startswith(">a")
    assert not [c for c in calls if "SELECT params" in c]
    assert core.pipeline._params("000000000000") == {}  # job inesistente: niente cache
    assert "000000000000" not in core.pipeline._params_cache


def test_job_detail_lists_at_most_the_shown_files(client, monkeypatch):
    from bioloom.api import jobs as jobs_api

    core = client.app.state.core
    core.jobs.register("molti_file", lambda ctx, _p: [ctx.path(f"f{i:02d}.txt").write_text("x") for i in range(7)]
                       and {})
    job_id = core.jobs.submit("molti_file", {})["id"]
    assert wait_job(client, job_id)["status"] == "completed"
    full = client.get(f"/api/jobs/{job_id}").json()
    assert full["files_truncated"] is False and len([f for f in full["files"] if f["path"].startswith("f")]) == 7
    monkeypatch.setattr(jobs_api, "JOB_FILES_SHOWN", 3)
    part = client.get(f"/api/jobs/{job_id}").json()
    assert part["files_truncated"] is True and len(part["files"]) == 3
    assert len(core.jobs.list_files(job_id, limit=2)) == 2
