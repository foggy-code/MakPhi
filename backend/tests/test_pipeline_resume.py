"""Ripresa delle pipeline: solo gli elementi non conclusi, file vecchi eliminati, stati, concorrenza.

MODELLER e la ricerca sono sostituiti da finti (come in test_api.py): ogni sequenza ha l'esito deciso
dal test e le pipeline girano con un elemento alla volta, nell'ordine del FASTA.
"""

import threading
import time

import pytest

from bioloom.core.errors import AppError, Conflict
from bioloom.engines import search
from tests.conftest import HUMAN_UB, YEAST_UB, wait_job

NAMES = ["a", "b", "c", "d", "e"]


class FakeModeller:
    """Sostituisce ComputeService.run_modeling: fallisce, si ferma (annullabile) o scrive un modello."""

    def __init__(self) -> None:
        self.calls: list[str] = []
        self.fail: set[str] = set()
        self.holds: dict[str, threading.Event] = {}
        self._lock = threading.Lock()

    def hold(self, *names: str) -> None:
        """Le sequenze indicate si fermano in modellazione finché non si chiama `let_go`."""
        for name in names:
            self.holds[name] = threading.Event()

    def let_go(self, *names: str) -> None:
        for name in names or list(self.holds):
            gate = self.holds.pop(name, None)
            if gate is not None:
                gate.set()

    def __call__(self, sub, *, target, **_kwargs) -> dict:
        name = target.name
        with self._lock:
            self.calls.append(name)
        gate = self.holds.get(name)
        if gate is not None:
            (sub.workdir / "parziale.txt").write_text("tentativo interrotto")
            while not gate.is_set():
                sub.check()  # l'annullamento del job arriva qui
                time.sleep(0.02)
        if name in self.fail:
            raise AppError(f"modellazione di {name} non riuscita")
        model = f"{name}.B99990001.pdb"
        (sub.workdir / model).write_text("ATOM\n")
        return {"models": [{"file": model, "best": True, "dope": -1000.0, "ga341": 0.9, "zdope": -1.1,
                            "molpdf": 50.0, "quality": {"grade": "A", "grade_score": 95.0, "rama_favored": 98.0,
                                                        "clashscore": 1.0}}]}


@pytest.fixture
def pipe(client, monkeypatch):
    core = client.app.state.core
    fake = FakeModeller()
    monkeypatch.setattr(core.compute, "run_modeling", fake)

    def submit(names=NAMES, **extra) -> str:
        fasta = "".join(f">{n}\n{HUMAN_UB if i % 2 == 0 else YEAST_UB}\n" for i, n in enumerate(names))
        body = {"fasta": fasta, "template_mode": "fixed", "templates": [{"id": "1UBQ", "chains": ["A"]}],
                "options": {"models": 1}, "quality": False, "parallel": 1, **extra}
        r = client.post("/api/pipeline", json=body)
        assert r.status_code == 200, r.text
        return r.json()["id"]

    return client, core, fake, submit


def until(condition, timeout: float = 20) -> None:
    start = time.time()
    while time.time() - start < timeout:
        if condition():
            return
        time.sleep(0.02)
    raise AssertionError("condizione non raggiunta in tempo")


def items(client, job_id: str) -> dict:
    return {i["name"]: i for i in client.get(f"/api/pipeline/{job_id}/items?size=100").json()["items"]}


def statuses(client, job_id: str) -> dict:
    return {name: item["status"] for name, item in items(client, job_id).items()}


def cancel_at(client, fake, job_id: str, name: str, attempt: int = 1) -> dict:
    """Annulla la pipeline quando l'elemento `name` è in modellazione (al tentativo `attempt`)."""
    until(lambda: fake.calls.count(name) >= attempt)
    assert client.post(f"/api/jobs/{job_id}/cancel").status_code == 200
    job = wait_job(client, job_id)
    assert job["status"] == "cancelled", job
    return job


def test_resume_after_cancel_processes_only_unfinished_items(pipe):
    client, core, fake, submit = pipe
    fake.hold("c")
    job_id = submit()
    job = cancel_at(client, fake, job_id, "c")
    assert statuses(client, job_id) == {"a": "completed", "b": "completed", "c": "cancelled", "d": "cancelled",
                                        "e": "cancelled"}
    assert job["resumable"] is True
    assert job["resumable_items"] == {"total": 5, "incomplete": 3, "failed": 0}
    final = core.jobs.workdir(job_id)
    stale = final / "items" / "0002_c" / "parziale.txt"
    assert stale.exists()  # salvato all'annullamento
    kept = final / "items" / "0000_a" / "a.B99990001.pdb"
    kept_stamp = kept.stat().st_mtime_ns

    fake.let_go()
    fake.calls.clear()
    r = client.post(f"/api/jobs/{job_id}/resume")
    assert r.status_code == 200, r.text
    assert r.json()["id"] == job_id and r.json()["error"] is None
    done = wait_job(client, job_id)
    assert done["status"] == "completed", done
    assert fake.calls == ["c", "d", "e"]  # gli elementi già conclusi non si rifanno
    assert done["result"] == {"total": 5, "counts": {"completed": 5}}
    assert done["progress"] == 1 and done["error"] is None
    assert done["resumable"] is False and done["resumable_items"] is None
    # il file del tentativo interrotto è sparito, il nuovo modello c'è, gli altri elementi sono intatti
    assert not stale.exists()
    assert (final / "items" / "0002_c" / "c.B99990001.pdb").exists()
    assert kept.stat().st_mtime_ns == kept_stamp
    item_c = items(client, job_id)["c"]
    assert item_c["best_model"] == "items/0002_c/c.B99990001.pdb" and item_c["dope"] == -1000.0
    log = client.get(f"/api/jobs/{job_id}/log").json()["text"]
    assert "Job annullato dall'utente" in log  # il log della prima esecuzione resta
    assert "Ripresa del job: 3 sequenze da rielaborare (elementi non conclusi)" in log
    assert "Ripresa: 3 sequenze da elaborare, 2 già elaborate" in log
    assert log.count("Job pipeline avviato") == 2


def test_resume_after_server_restart(pipe):
    client, core, fake, submit = pipe
    job_id = submit(names=["a", "b", "c", "d"])
    assert wait_job(client, job_id)["status"] == "completed"
    # Stato lasciato da un arresto improvviso: job in esecuzione, "c" a metà (con un file salvato),
    # "d" ancora in attesa. Il riavvio (recover) li marca come interrotti.
    core.db.execute("UPDATE jobs SET status='running', finished_at=NULL, result=NULL WHERE id=?", (job_id,))
    core.db.execute("UPDATE pipeline_items SET status='modeling', stage='Modellazione', best_model=NULL "
                    "WHERE job_id=? AND idx=2", (job_id,))
    core.db.execute("UPDATE pipeline_items SET status='pending', stage='', best_model=NULL "
                    "WHERE job_id=? AND idx=3", (job_id,))
    stale = core.jobs.workdir(job_id) / "items" / "0002_c" / "vecchio.pdb"
    stale.write_text("ATOM\n")
    assert core.jobs.recover() == 1
    job = client.get(f"/api/jobs/{job_id}").json()
    assert job["status"] == "failed" and "riavvio" in job["error"]
    assert statuses(client, job_id) == {"a": "completed", "b": "completed", "c": "failed", "d": "failed"}
    assert items(client, job_id)["d"]["stage"] == "Interrotto"  # anche un elemento che era in attesa
    assert job["resumable"] is True and job["resumable_items"] == {"total": 4, "incomplete": 2, "failed": 2}

    fake.calls.clear()
    assert client.post(f"/api/jobs/{job_id}/resume", json={"scope": "incomplete"}).status_code == 200
    done = wait_job(client, job_id)
    assert done["status"] == "completed", done
    assert fake.calls == ["c", "d"]
    assert done["result"]["counts"] == {"completed": 4}
    assert not stale.exists()
    reset = items(client, job_id)["d"]
    assert reset["error"] is None and reset["status"] == "completed"


def test_resume_failed_scope_leaves_cancelled_items_alone(pipe):
    client, core, fake, submit = pipe
    fake.fail.add("b")
    fake.hold("c")
    job_id = submit()
    job = cancel_at(client, fake, job_id, "c")
    assert statuses(client, job_id) == {"a": "completed", "b": "failed", "c": "cancelled", "d": "cancelled",
                                        "e": "cancelled"}
    assert job["resumable_items"] == {"total": 5, "incomplete": 4, "failed": 1}
    before = items(client, job_id)
    partial = core.jobs.workdir(job_id) / "items" / "0002_c" / "parziale.txt"
    assert partial.exists()

    fake.fail.clear()
    fake.let_go()
    fake.calls.clear()
    assert client.post(f"/api/jobs/{job_id}/resume", json={"scope": "failed"}).status_code == 200
    done = wait_job(client, job_id)
    assert fake.calls == ["b"]
    after = items(client, job_id)
    assert after["b"]["status"] == "completed" and after["b"]["error"] is None
    for name in ("c", "d", "e"):  # gli annullati restano identici, file compresi
        assert after[name] == before[name]
    assert partial.exists()
    # restano elementi annullati: la pipeline non si dichiara completata e resta riprendibile
    assert done["status"] == "cancelled" and "Restano 3 sequenze" in done["error"]
    assert done["progress"] == pytest.approx(0.4)
    assert done["resumable"] is True and done["resumable_items"] == {"total": 5, "incomplete": 3, "failed": 0}
    r = client.post(f"/api/jobs/{job_id}/resume", json={"scope": "failed"})
    assert r.status_code == 409 and "fallito" in r.json()["error"]["message"]

    assert client.post(f"/api/jobs/{job_id}/resume").status_code == 200
    final = wait_job(client, job_id)
    assert final["status"] == "completed", final
    assert fake.calls == ["b", "c", "d", "e"]
    assert not partial.exists()


def test_resume_refused_without_items_or_when_completed(pipe):
    client, core, fake, submit = pipe
    job_id = submit(names=["a", "b"])
    done = wait_job(client, job_id)
    assert done["status"] == "completed" and done["resumable"] is False
    r = client.post(f"/api/jobs/{job_id}/resume")
    assert r.status_code == 409 and "già completato" in r.json()["error"]["message"]
    # fallita dopo aver concluso tutti gli elementi: non c'è nulla da riprendere
    core.db.execute("UPDATE jobs SET status='failed', error='guasto finale' WHERE id=?", (job_id,))
    detail = client.get(f"/api/jobs/{job_id}").json()
    assert detail["resumable"] is False and detail["resumable_items"]["incomplete"] == 0
    r = client.post(f"/api/jobs/{job_id}/resume")
    assert r.status_code == 409 and "Nessun elemento da riprendere" in r.json()["error"]["message"]
    assert client.get(f"/api/jobs/{job_id}").json()["status"] == "failed"  # stato invariato
    assert client.post(f"/api/jobs/{job_id}/resume", json={"scope": "tutti"}).status_code == 422
    assert client.post("/api/jobs/0123456789abcdef/resume").status_code == 404
    assert fake.calls == ["a", "b"]


def test_resume_refused_while_active(pipe):
    client, core, fake, submit = pipe
    fake.hold("a")
    job_id = submit(names=["a", "b"])
    until(lambda: "a" in fake.calls)
    r = client.post(f"/api/jobs/{job_id}/resume")
    assert r.status_code == 409 and "in esecuzione" in r.json()["error"]["message"]
    assert client.get(f"/api/jobs/{job_id}").json()["resumable"] is False
    client.post(f"/api/jobs/{job_id}/cancel")
    assert wait_job(client, job_id)["status"] == "cancelled"
    assert fake.calls == ["a"]


def test_concurrent_resumes_start_a_single_execution(pipe):
    client, core, fake, submit = pipe
    fake.hold("c")
    job_id = submit(names=["a", "b", "c", "d"])
    cancel_at(client, fake, job_id, "c")
    fake.let_go()
    fake.calls.clear()
    barrier = threading.Barrier(5)
    outcome: list[str] = []

    def attempt() -> None:
        barrier.wait()
        try:
            core.pipeline.resume(job_id)
            outcome.append("ok")
        except Conflict:
            outcome.append("conflict")

    threads = [threading.Thread(target=attempt) for _ in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(20)
    assert sorted(outcome) == ["conflict"] * 4 + ["ok"]
    assert wait_job(client, job_id)["status"] == "completed"
    assert fake.calls == ["c", "d"]  # ogni elemento una sola volta
    log = client.get(f"/api/jobs/{job_id}/log").json()["text"]
    assert log.count("Ripresa del job") == 1 and log.count("Job pipeline avviato") == 2


def test_resume_not_supported_for_other_job_kinds(pipe):
    client, core, _fake, _submit = pipe
    core.jobs.register("demo_fail", lambda ctx, p: (_ for _ in ()).throw(AppError("guasto")))
    job_id = core.jobs.submit("demo_fail", {})["id"]
    job = wait_job(client, job_id)
    assert job["status"] == "failed" and job["resumable"] is False and job["resumable_items"] is None
    r = client.post(f"/api/jobs/{job_id}/resume")
    assert r.status_code == 400 and r.json()["error"]["code"] == "resume_unsupported"
    assert "solo le pipeline" in r.json()["error"]["message"]


def test_resumed_pipeline_can_be_cancelled_again(pipe):
    client, core, fake, submit = pipe
    fake.hold("c")
    job_id = submit()
    cancel_at(client, fake, job_id, "c")
    assert client.post(f"/api/jobs/{job_id}/resume").status_code == 200
    job = cancel_at(client, fake, job_id, "c", attempt=2)  # "c" si ferma di nuovo
    assert statuses(client, job_id) == {"a": "completed", "b": "completed", "c": "cancelled", "d": "cancelled",
                                        "e": "cancelled"}
    assert job["resumable"] is True and job["error"] == "Annullato dall'utente."
    fake.let_go()
    assert client.post(f"/api/jobs/{job_id}/resume").status_code == 200
    assert wait_job(client, job_id)["status"] == "completed"
    assert fake.calls == ["a", "b", "c", "c", "c", "d", "e"]


def test_resume_progress_starts_from_processed_items(pipe):
    client, core, fake, submit = pipe
    fake.fail.add("b")
    fake.hold("d")
    job_id = submit()
    cancel_at(client, fake, job_id, "d")
    assert statuses(client, job_id) == {"a": "completed", "b": "failed", "c": "completed", "d": "cancelled",
                                        "e": "cancelled"}
    fake.fail.clear()
    fake.hold("b")
    fake.calls.clear()
    assert client.post(f"/api/jobs/{job_id}/resume").status_code == 200

    def stage() -> dict:
        return client.get(f"/api/jobs/{job_id}/status").json()

    until(lambda: "b" in fake.calls)  # a e c già elaborati: si parte da 2 su 5
    assert stage()["status"] == "running"
    assert stage()["stage"] == "2 di 5 sequenze elaborate" and stage()["progress"] == pytest.approx(0.4)
    fake.let_go("b")
    until(lambda: "d" in fake.calls)  # b concluso, d di nuovo fermo
    assert stage()["stage"] == "3 di 5 sequenze elaborate" and stage()["progress"] == pytest.approx(0.6)
    fake.let_go("d")
    done = wait_job(client, job_id)
    assert done["status"] == "completed" and done["progress"] == 1
    assert fake.calls == ["b", "d", "e"]


def test_resume_in_auto_mode_clears_search_results(pipe, monkeypatch):
    client, core, fake, submit = pipe
    identity = {"value": 30.0}

    def fake_search(ctx, sequence, **_kwargs):
        hit = {"template_id": "1UBQ_A", "pdb_id": "1UBQ", "chain": "A", "identity": identity["value"],
               "coverage": 90.0, "evalue": 1e-20, "resolution": 1.8}
        return {"engine": "rcsb", "hits": [hit]}

    monkeypatch.setattr(search, "search", fake_search)
    fake.fail.add("b")
    job_id = submit(names=["a", "b"], template_mode="auto", templates=[])
    done = wait_job(client, job_id)
    assert done["status"] == "completed" and statuses(client, job_id) == {"a": "completed", "b": "failed"}
    assert items(client, job_id)["b"]["identity"] == 30.0
    core.db.execute("UPDATE jobs SET status='failed' WHERE id=?", (job_id,))  # per poterla riprendere
    fake.fail.clear()
    fake.hold("b")
    identity["value"] = 80.0
    assert client.post(f"/api/jobs/{job_id}/resume", json={"scope": "failed"}).status_code == 200
    until(lambda: fake.calls.count("b") == 2)
    retry = items(client, job_id)["b"]  # azzerato prima della nuova esecuzione, poi ricercato di nuovo
    assert retry["status"] == "modeling" and retry["identity"] == 80.0
    assert retry["error"] is None and retry["finished_at"] is None and retry["best_model"] is None
    fake.let_go()
    assert wait_job(client, job_id)["status"] == "completed"
    b = items(client, job_id)["b"]
    assert b["status"] == "completed" and b["identity"] == 80.0 and b["template"] == "1UBQ_A"
    assert items(client, job_id)["a"]["identity"] == 30.0  # l'elemento concluso non cambia
