from __future__ import annotations

import os
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from bioloom.app import create_app
from bioloom.config import Paths
from bioloom.core import cache as cache_module
from bioloom.core import jobs as jobs_module
from bioloom.engines import tools

DATA = Path(__file__).parent / "data"

# Chiave MODELLER per i test (es. nel container: -e MODELLER_KEY=...), come la usa il server.
if os.environ.get("MODELLER_KEY"):
    jobs_module.set_subprocess_env("BIOLOOM_MODELLER_KEY", os.environ["MODELLER_KEY"])


@pytest.fixture(autouse=True)
def _restore_subprocess_env():
    """Le licenze attivate da un test non devono influenzare i successivi."""
    saved = dict(jobs_module._extra_env)
    yield
    jobs_module._extra_env.clear()
    jobs_module._extra_env.update(saved)


@pytest.fixture(autouse=True)
def _reset_compute_cache():
    """La cartella della cache dei calcoli configurata da un test (build_core) non vale per i successivi."""
    saved = cache_module.root()
    yield
    cache_module.configure(saved)


HUMAN_UB = "MQIFVKTLTGKTITLEVEPSDTIENVKAKIQDKEGIPPDQQRLIFAGKQLEDGRTLSDYNIQKESTLHLVLRLRGG"
YEAST_UB = "MQIFVKTLTGKTITLEVESSDTIDNVKSKIQDKEGIPPDQQRLIFAGKQLEDGRTLSDYNIQKESTLHLVLRLRGG"


@pytest.fixture
def ubq_path() -> Path:
    return DATA / "1UBQ.pdb"


@pytest.fixture
def client(tmp_path):
    app = create_app(Paths(tmp_path / "data"), frontend_dist=None)
    with TestClient(app) as c:
        yield c


@pytest.fixture
def client_with_ubq(client, ubq_path):
    with open(ubq_path, "rb") as fh:
        r = client.post("/api/templates/upload", files={"file": ("1UBQ.pdb", fh, "chemical/x-pdb")})
    assert r.status_code == 200, r.text
    return client


def wait_job(client, job_id: str, timeout: float = 300) -> dict:
    start = time.time()
    while time.time() - start < timeout:
        job = client.get(f"/api/jobs/{job_id}").json()
        if job["status"] in ("completed", "failed", "cancelled"):
            return job
        time.sleep(0.2)
    raise AssertionError(f"job {job_id} non terminato entro {timeout}s")


def modeller_available() -> bool:
    return tools.detect_modeller("")["available"]


requires_modeller = pytest.mark.skipif(not modeller_available(), reason="MODELLER non installato")
