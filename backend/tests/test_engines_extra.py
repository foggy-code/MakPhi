"""PROCHECK, ProSA II, MMseqs2, QMEAN locale (worker simulato), database e verifica dell'installazione."""

import json

import httpx
import pytest

from bioloom.config import UserSettings
from bioloom.core.db import Database
from bioloom.core.errors import AppError, ToolUnavailable
from bioloom.core.jobs import JobCancelled, JobManager
from bioloom.domain import procheck as procheck_parser
from bioloom.engines import prosa, qmean_local, quality, remote, search, tools

from .conftest import DATA, wait_job


def test_procheck_summary_parser_on_real_output():
    data = procheck_parser.parse_sum((DATA / "procheck_1ubq.sum").read_text())
    assert data["ramachandran"] == {"core": 95.5, "allowed": 4.5, "generous": 0.0, "disallowed": 0.0}
    assert data["g_factors"] == {"dihedrals": 0.17, "covalent": -0.43, "overall": -0.04}
    assert data["residues"] == 76 and data["bad_contacts"] == 5
    assert data["mc_bond_lengths_ok"] == 95.7 and data["planar_groups_ok"] == 96.3


def test_prosa_parsers_on_real_output():
    z = prosa.parse_zscores((DATA / "prosa_1ubq.slp").read_text())
    assert z == {"length": 76, "combined": -9.26, "pair": -5.86, "surface": -6.77}
    raw = prosa.parse_energy_profile((DATA / "prosa_1ubq_w1.ana").read_text())
    smooth = prosa.parse_energy_profile((DATA / "prosa_1ubq_w10.ana").read_text())
    assert len(raw) == 76 and raw[0] == {"index": 1, "pair": 0.425, "surface": 4.092, "combined": 4.517}
    assert smooth and all(r["index"] >= 1 for r in smooth)


def test_mmseqs_m8_parser():
    row = "\t".join(["query", "pdb|1UBQ|A", "96.1", "76", "1.2e-40", "150.3", "1", "76", "1", "76", "76", "76",
                     "MQIF", "MQIF", "pdb|1UBQ|A Chain A, UBIQUITIN\x01pdb|1UBI|A Chain A"])
    dup = row.replace("150.3", "140.0")
    hits = search.parse_mmseqs_m8(row + "\n" + dup + "\nriga non valida\n")
    assert len(hits) == 1
    h = hits[0]
    assert (h["pdb_id"], h["chain"], h["identity"], h["coverage"]) == ("1UBQ", "A", 96.1, 100.0)
    assert h["title"] == "Chain A, UBIQUITIN" and h["source"] == "mmseqs"


def test_quality_reports_missing_external_tools_as_warnings(tmp_path, ubq_path, monkeypatch):
    monkeypatch.setattr(tools, "find", lambda key: None)
    db = Database(tmp_path / "q.db")
    mgr = JobManager(db, tmp_path / "jobs", 1, UserSettings, scratch_dir=tmp_path / "scratch")
    try:
        mgr.register("q", lambda ctx, p: quality.analyze(ubq_path, ctx, with_dope=False, with_procheck=True,
                                                         with_prosa=True))
        job_id = mgr.submit("q", {})["id"]
        import time

        for _ in range(200):
            job = mgr.get(job_id)
            if job["status"] in ("completed", "failed"):
                break
            time.sleep(0.05)
        assert job["status"] == "completed", job["error"]
        report = job["result"]
        assert report["procheck"] is None and report["prosa"] is None
        assert any("PROCHECK non disponibile" in w for w in report["warnings"])
        assert any("ProSA II non disponibile" in w for w in report["warnings"])
    finally:
        mgr.shutdown()
        db.close()


QMEAN_OUTPUT = {
    "qmean_version": "4.3.0", "method": "QMEAN",
    "models": {"model_001": {"scores": {
        "global_scores": {"qmean4_norm_score": 0.81, "qmean4_z_score": 0.35, "qmean6_z_score": 0.1,
                          "avg_local_score": 0.84, "cbeta_z_score": -0.2},
        "local_scores": {"A": [0.9, 0.8, None]}}}},
}


class FakeWorker:
    """Worker QMEAN simulato: coda → esecuzione → risultato."""

    def __init__(self, valid=True, final="completed"):
        self.valid, self.final, self.polls, self.deleted = valid, final, 0, []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/status":
            return httpx.Response(200, json={"uniclust": {"valid": self.valid, "prefix": "UniRef30_2023_02",
                                                          "error": None if self.valid else "manca"},
                                             "qmtl": {"present": False}, "busy": False, "queued": 0})
        if path == "/jobs" and request.method == "POST":
            body = json.loads(request.content)
            assert "ATOM" in body["pdb"]
            return httpx.Response(202, json={"id": "abc", "status": "queued"})
        if path == "/jobs/abc" and request.method == "DELETE":
            self.deleted.append("abc")
            return httpx.Response(200, json={"status": "cancelled"})
        if path == "/jobs/abc":
            self.polls += 1
            if self.polls < 3:
                return httpx.Response(200, json={"status": "queued" if self.polls == 1 else "running", "position": 0})
            if self.final == "completed":
                return httpx.Response(200, json={"status": "completed", "result": QMEAN_OUTPUT})
            return httpx.Response(200, json={"status": "failed", "error": "HHblits fallito"})
        return httpx.Response(404, json={})


@pytest.fixture
def fake_worker(monkeypatch):
    def install(worker):
        qmean_local._status_cache.update({"at": 0.0, "key": None, "value": None})
        monkeypatch.setenv("BIOLOOM_QMEAN_URL", "http://qmean:8080")
        monkeypatch.setattr(qmean_local, "_client",
                            lambda: httpx.Client(base_url="http://qmean:8080", transport=httpx.MockTransport(worker)))
        return worker
    return install


class Ctx:
    def __init__(self, cancel_after=None):
        self.logs, self.stages, self.calls, self.cancel_after = [], [], 0, cancel_after

    def check(self):
        self.calls += 1
        if self.cancel_after and self.calls > self.cancel_after:
            raise JobCancelled()

    def log(self, msg):
        self.logs.append(msg)

    def progress(self, fraction=None, stage=None):
        self.stages.append(stage)


def test_qmean_local_status_and_score(fake_worker, ubq_path):
    worker = fake_worker(FakeWorker())
    assert qmean_local.status()["available"]
    result = qmean_local.score(ubq_path.read_text(), Ctx(), sleep=lambda s: None)
    assert result["source"] == "local" and result["qmean4_z"] == 0.35 and result["qmean4"] == 0.81
    assert result["local"][:2] == [{"chain": "A", "index": 1, "score": 0.9}, {"chain": "A", "index": 2, "score": 0.8}]
    assert result["uniclust"] == "UniRef30_2023_02" and worker.polls == 3


def test_qmean_local_failures_and_cancel(fake_worker, ubq_path):
    fake_worker(FakeWorker(valid=False))
    with pytest.raises(ToolUnavailable):
        qmean_local.score(ubq_path.read_text(), Ctx(), sleep=lambda s: None)
    fake_worker(FakeWorker(final="failed"))
    with pytest.raises(AppError, match="HHblits fallito"):
        qmean_local.score(ubq_path.read_text(), Ctx(), sleep=lambda s: None)
    worker = fake_worker(FakeWorker())
    with pytest.raises(JobCancelled):
        qmean_local.score(ubq_path.read_text(), Ctx(cancel_after=1), sleep=lambda s: None)
    assert worker.deleted == ["abc"]  # il calcolo annullato viene fermato anche nel worker


def test_qmean_not_configured(monkeypatch):
    monkeypatch.delenv("BIOLOOM_QMEAN_URL", raising=False)
    info = qmean_local.status()
    assert not info["configured"] and not info["available"]


def test_normalize_qmean_matches_remote_format():
    out = remote.normalize_qmean(QMEAN_OUTPUT)
    assert out["qmean4_z"] == 0.35 and out["components"] == {"qmean4_z_score": 0.35, "qmean6_z_score": 0.1,
                                                             "cbeta_z_score": -0.2}
    assert out["method"] == "QMEAN" and len(out["local"]) == 3


def test_qmean_job_without_any_backend_explains_what_to_do(client_with_ubq, monkeypatch):
    monkeypatch.delenv("BIOLOOM_QMEAN_URL", raising=False)
    job = client_with_ubq.post("/api/qmean", json={"source": {"type": "template", "id": "1UBQ"}}).json()
    done = wait_job(client_with_ubq, job["id"])
    assert done["status"] == "failed" and "SWISS-MODEL" in done["error"]


def test_databases_status_and_selftest_job(client):
    status = client.get("/api/databases").json()
    assert {"pdbaa", "mmseqs", "qmean"} <= status.keys()
    assert status["pdbaa"]["present"] is False
    job = client.post("/api/system/selftest").json()
    done = wait_job(client, job["id"], timeout=600)
    assert done["status"] == "completed"
    checks = {c["tool"]: c for c in done["result"]["checks"]}
    assert {"MODELLER", "DSSP", "BLAST+", "PROCHECK", "ProSA II", "QMEAN locale"} <= checks.keys()
    assert checks["QMEAN locale"]["status"] == "missing"
    assert all(c["status"] in ("ok", "missing", "error") for c in checks.values())
