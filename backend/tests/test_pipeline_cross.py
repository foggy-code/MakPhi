"""Pipeline M × N: ogni sequenza modellata su ogni template fisso, un modello per coppia.

MODELLER è simulato (come in test_pipeline_resume): la modellazione finta scrive per ogni coppia il modello (coordinate
di 1UBQ), `models.json` e l'allineamento come run_modeling, con uno zDOPE deciso dal test per (sequenza, template).
Con l'analisi richiesta (quality_mode "best", per ogni coppia) fa l'analisi vera senza DOPE (MODELLER assente) e scrive
rapporto e REMARK come run_modeling.
"""

from __future__ import annotations

import csv
import io
import json
import threading
import zipfile

import pytest

from bioloom.core.errors import AppError
from bioloom.domain import sequences
from bioloom.engines import quality
from bioloom.services import pipeline as pipeline_service
from bioloom.services.compute import model_remarks
from tests.conftest import DATA, HUMAN_UB, YEAST_UB, wait_job

TEMPLATES = [{"id": "T1", "chains": ["A"]}, {"id": "T2", "chains": ["A"]}, {"id": "T3", "chains": ["B"]}]
LABELS = ["T1_A", "T2_A", "T3_B"]
NAMES = ["a", "b"]
# zDOPE per (sequenza, template): il migliore di "a" è T2, quello di "b" è T3
ZDOPE = {("a", "T1"): -1.0, ("a", "T2"): -1.8, ("a", "T3"): -0.5,
         ("b", "T1"): -0.9, ("b", "T2"): -1.2, ("b", "T3"): -1.5}


def _model_text() -> str:
    return "\n".join(ln for ln in (DATA / "1UBQ.pdb").read_text().splitlines()
                     if ln.startswith(("ATOM", "TER"))) + "\nEND\n"


class FakeModeller:
    """Sostituisce ComputeService.run_modeling: un modello per chiamata, con lo zDOPE della coppia."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, list[str]]] = []
        self.assessed: list[tuple[str, str]] = []  # coppie analizzate (sequenza, template)
        self.fail: set[tuple[str, str]] = set()
        self.zdope = dict(ZDOPE)
        self._lock = threading.Lock()

    def __call__(self, sub, *, target, templates_raw, quality_mode, **_kwargs) -> dict:
        ids = [t["id"] for t in templates_raw]
        with self._lock:
            self.calls.append((target.name, ids))
        assert quality_mode in ("best", "none")
        if (target.name, ids[0]) in self.fail:
            raise AppError(f"modellazione di {target.name} su {ids[0]} non riuscita")
        zdope = self.zdope[(target.name, ids[0])]
        model = f"{target.name}.B99990001.pdb"
        (sub.workdir / model).write_text(_model_text())
        entry = {"file": model, "dope": round(zdope * 4000, 1), "zdope": zdope, "ga341": 0.9, "molpdf": 300.0,
                 "failure": None}
        (sub.workdir / "models.json").write_text(json.dumps({"models": [entry]}))
        code = f"{ids[0]}_{templates_raw[0]['chains'][0]}"
        pir = sequences.format_pir([sequences.template_entry(code, HUMAN_UB, "1", "A", "76", "A"),
                                    sequences.target_entry(target.name, target.sequence)])
        (sub.workdir / "alignment.ali").write_text(pir)
        templates = [{"code": code, "id": ids[0], "chains": ["A"]}]
        best = {**entry, "best": True, "kind": "model"}
        if quality_mode == "best":  # come run_modeling: rapporto accanto al modello, metriche, REMARK con il voto
            with self._lock:
                self.assessed.append((target.name, ids[0]))
            report = quality.analyze(sub.workdir / model, None, with_dope=False)
            report["dope"] = {"score": entry["dope"], "zscore": None, "profile": []}  # zDOPE del rapporto assente
            (sub.workdir / f"{model}.quality.json").write_text(json.dumps(report))
            best["quality"] = {**quality.summary_metrics(report), "zdope": None}
            quality.inject_remarks(sub.workdir / model, model_remarks(best, target.name, templates))
        return {"models": [best], "alignment": pir, "target": {"code": target.name}, "templates": templates}


@pytest.fixture
def cross(client, monkeypatch):
    core = client.app.state.core
    fake = FakeModeller()
    monkeypatch.setattr(core.compute, "run_modeling", fake)

    def submit(names=NAMES, templates=TEMPLATES, **extra) -> str:
        fasta = "".join(f">{n}\n{HUMAN_UB if i % 2 == 0 else YEAST_UB}\n" for i, n in enumerate(names))
        body = {"fasta": fasta, "template_mode": "fixed", "fixed_combination": "each", "templates": templates,
                "options": {"models": 1}, "quality": True, "parallel": 2, **extra}
        r = client.post("/api/pipeline", json=body)
        assert r.status_code == 200, r.text
        return r.json()["id"]

    return client, core, fake, submit


def _items(client, job_id: str, query: str = "") -> list[dict]:
    return client.get(f"/api/pipeline/{job_id}/items?size=100{query}").json()["items"]


def test_every_sequence_is_modelled_on_every_template(cross):
    client, core, fake, submit = cross
    job_id = submit()
    job = wait_job(client, job_id)
    assert job["status"] == "completed", job.get("error")
    assert job["title"] == "Pipeline di 2 sequenze × 3 template"
    # 2 × 3 coppie, una chiamata di MODELLER per coppia con un solo template, bersaglio con il nome della sequenza
    assert sorted(fake.calls) == sorted((n, [t["id"]]) for n in NAMES for t in TEMPLATES)
    page = client.get(f"/api/pipeline/{job_id}/items?size=100").json()
    assert page["pairs"] == {"sequences": NAMES, "templates": LABELS, "template_ids": ["T1", "T2", "T3"]}
    rows = page["items"]
    assert [r["name"] for r in rows] == [f"{n}__{label}" for n in NAMES for label in LABELS]
    assert [(r["pair"]["sequence"], r["pair"]["template"], r["template"]) for r in rows] == [
        (n, label, tid) for n in NAMES for label, tid in zip(LABELS, ["T1", "T2", "T3"])]
    assert all(r["status"] == "completed" for r in rows)
    # identità e copertura dall'allineamento: "a" è l'ubiquitina umana, uguale al template
    first = rows[0]
    assert first["identity"] == 100.0 and first["coverage"] == 100.0 and first["twilight"] == 0
    assert rows[3]["identity"] == pytest.approx(96.1, abs=0.1)
    # cartella <idx>_<sequenza>__<template> e riga della coppia nel PDB
    model = core.jobs.workdir(job_id) / "items" / "0001_a__T2_A" / "a.B99990001.pdb"
    assert "REMARK 999 BIOLOOM PIPELINE SEQUENCE a TEMPLATE T2_A" in model.read_text()


def test_every_pair_is_analysed_and_the_best_is_chosen_by_zdope(cross):
    client, core, fake, submit = cross
    job_id = submit()
    assert wait_job(client, job_id)["status"] == "completed"
    rows = _items(client, job_id)
    best = {r["pair"]["sequence"]: r["pair"]["template"] for r in rows if r["best_in_sequence"]}
    assert best == {"a": "T2_A", "b": "T3_B"}
    # analisi di qualità di ogni coppia: voto in ogni riga, rapporto accanto a ogni modello
    assert sorted(fake.assessed) == sorted((n, t["id"]) for n in NAMES for t in TEMPLATES)
    assert all(r["grade"] in ("A", "B", "C", "D") for r in rows)
    for r in rows:
        folder = core.jobs.workdir(job_id) / "items" / f"{r['idx']:04d}_{r['name']}"
        assert (folder / f"{r['pair']['sequence']}.B99990001.pdb.quality.json").exists(), r["name"]
    # lo zDOPE della riga resta quello di MODELLER anche se il rapporto di qualità non lo ha
    assert [r["metrics"]["zdope"] for r in rows] == [ZDOPE[(n, t["id"])] for n in NAMES for t in TEMPLATES]
    # REMARK della coppia accanto a quelle dell'analisi (voto compreso)
    text = (core.jobs.workdir(job_id) / "items" / "0001_a__T2_A" / "a.B99990001.pdb").read_text()
    assert "PIPELINE SEQUENCE a TEMPLATE T2_A" in text and "BIOLOOM GRADE" in text
    # filtri: per sequenza, per template, solo i migliori
    assert [r["name"] for r in _items(client, job_id, "&sequence=1")] == ["b__T1_A", "b__T2_A", "b__T3_B"]
    assert [r["name"] for r in _items(client, job_id, "&template=2")] == ["a__T3_B", "b__T3_B"]
    assert [r["name"] for r in _items(client, job_id, "&best=true")] == ["a__T2_A", "b__T3_B"]
    # matrice sequenze × template
    matrix = client.get(f"/api/pipeline/{job_id}/matrix").json()
    assert matrix["sequences"] == NAMES and matrix["templates"] == LABELS
    assert [[c["zdope"] for c in row] for row in matrix["cells"]] == [[-1.0, -1.8, -0.5], [-0.9, -1.2, -1.5]]
    assert [[c["best"] for c in row] for row in matrix["cells"]] == [[False, True, False], [False, False, True]]


def test_without_quality_the_best_is_marked_but_not_analysed(cross):
    client, _core, fake, submit = cross
    job_id = submit(quality=False)
    assert wait_job(client, job_id)["status"] == "completed"
    rows = _items(client, job_id)
    assert fake.assessed == [] and all(r["grade"] is None for r in rows)
    assert [r["name"] for r in rows if r["best_in_sequence"]] == ["a__T2_A", "b__T3_B"]


def test_zip_has_one_folder_per_template(cross):
    client, _core, _fake, submit = cross
    job_id = submit()
    assert wait_job(client, job_id)["status"] == "completed"
    r = client.get(f"/api/pipeline/{job_id}/export.zip")
    assert r.status_code == 200
    zf = zipfile.ZipFile(io.BytesIO(r.content))
    names = set(zf.namelist())
    for n in NAMES:
        for label in LABELS:
            base = f"template/{label}/{n}__{label}"
            assert {f"{base}.pdb", f"{base}.html", f"{base}.quality.json"} <= names, base
    assert {"LEGGIMI.txt", "riepilogo.csv", "migliori.csv", "parametri.json", "matrici/zdope.csv", "matrici/dope.csv",
            "matrici/identita.csv", "matrici/copertura.csv"} <= names
    assert "report_non_generati.txt" not in names
    summary = list(csv.DictReader(io.StringIO(zf.read("riepilogo.csv").decode())))
    assert len(summary) == 6
    row = next(s for s in summary if s["sequence"] == "b" and s["template"] == "T3_B")
    assert row["best_in_sequence"] == "1" and row["file"] == "template/T3_B/b__T3_B.pdb" and row["zdope"] == "-1.5"
    assert row["file"] in names
    best = list(csv.DictReader(io.StringIO(zf.read("migliori.csv").decode())))
    assert [(b["sequence"], b["best_template"], b["completed"], b["templates"]) for b in best] == [
        ("a", "T2_A", "3", "3"), ("b", "T3_B", "3", "3")]
    assert best[0]["grade"] in "ABCD" and best[0]["file"] == "template/T2_A/a__T2_A.pdb"
    matrix = list(csv.reader(io.StringIO(zf.read("matrici/zdope.csv").decode())))
    assert matrix == [["sequenza", *LABELS], ["a", "-1.0", "-1.8", "-0.5"], ["b", "-0.9", "-1.2", "-1.5"]]
    # report della coppia: titolo con sequenza × template, bersaglio con il nome della sequenza
    html = zf.read("template/T2_A/a__T2_A.html").decode()
    assert "a × T2_A" in html and "un modello per ogni template" in html


def test_failed_pair_and_resume_choose_the_best_again(cross):
    client, _core, fake, submit = cross
    fake.fail.add(("a", "T2"))  # il migliore di "a" fallisce: il migliore diventa T1
    job_id = submit()
    assert wait_job(client, job_id)["status"] == "completed"
    rows = _items(client, job_id)
    assert {r["name"]: r["status"] for r in rows}["a__T2_A"] == "failed"
    assert [r["name"] for r in rows if r["best_in_sequence"]] == ["a__T1_A", "b__T3_B"]
    matrix = client.get(f"/api/pipeline/{job_id}/matrix").json()
    assert matrix["cells"][0][1]["status"] == "failed" and matrix["cells"][0][1]["zdope"] is None
    # ripresa dei falliti: solo T2 si rifà (e si analizza) ed è migliore; l'altra sequenza resta com'era
    fake.fail.clear()
    fake.assessed.clear()
    assert client.post(f"/api/jobs/{job_id}/resume", json={"scope": "failed"}).status_code == 200
    job = wait_job(client, job_id)
    assert job["status"] == "completed", job.get("error")
    rows = _items(client, job_id)
    assert [r["name"] for r in rows if r["best_in_sequence"]] == ["a__T2_A", "b__T3_B"]
    assert fake.assessed == [("a", "T2")]


def test_validation(client):
    body = {"fasta": f">a\n{HUMAN_UB}\n", "template_mode": "fixed", "fixed_combination": "each",
            "templates": [{"id": "1UBQ", "chains": ["A"]}, {"id": "1ubq", "chains": ["A"]}]}
    r = client.post("/api/pipeline", json=body)
    assert r.status_code == 400 and "più volte" in r.json()["error"]["message"]
    # stesso template con catene diverse: due coppie distinte, etichette diverse
    assert pipeline_service.template_labels([{"id": "1UBQ", "chains": ["A"]}, {"id": "1UBQ", "chains": ["B"]},
                                             {"id": "1UBQ", "chains": []}]) == ["1UBQ_A", "1UBQ_B", "1UBQ"]
    r = client.post("/api/pipeline", json={**body, "fixed_combination": "tutti"})
    assert r.status_code == 422
    # con la ricerca automatica la scelta non conta
    assert pipeline_service.is_cross({"template_mode": "auto", "fixed_combination": "each"}) is False


def test_matrix_only_for_cross_pipelines(client):
    core = client.app.state.core
    core.jobs.register("pipeline", lambda ctx, p: {"total": 0})
    job = core.pipeline.submit({"fasta": f">a\n{HUMAN_UB}\n", "template_mode": "fixed",
                                "templates": [{"id": "1UBQ"}], "options": {}}, None)
    wait_job(client, job["id"])
    r = client.get(f"/api/pipeline/{job['id']}/matrix")
    assert r.status_code == 400 and r.json()["error"]["code"] == "matrix_unsupported"
    assert "pairs" not in client.get(f"/api/pipeline/{job['id']}/items").json()
