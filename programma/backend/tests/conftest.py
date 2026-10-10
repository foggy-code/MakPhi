from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Iterable, Optional

import pytest
from fastapi.testclient import TestClient

from bioloom.app import create_app
from bioloom.config import Paths
from bioloom.core import cache as cache_module
from bioloom.core import jobs as jobs_module
from bioloom.engines import tools

DATA = Path(__file__).parent / "data"

# I TestClient mandano l'header Host "testserver", che senza accesso protetto il controllo dell'Host (core/guard)
# rifiuterebbe. Aggiunto qui, prima di ogni create_app, vale anche per le app create direttamente nei test; aggiunto e
# non solo predefinito, perché la variabile può esistere vuota (compose.yml) o con altri nomi. I test della guardia la
# tolgono o la cambiano con monkeypatch.
os.environ["BIOLOOM_ALLOWED_HOSTS"] = f"{os.environ.get('BIOLOOM_ALLOWED_HOSTS', '')} testserver".strip()

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


def ubq_variant(dest: Path, *, hetatm: Optional[dict[int, str]] = None, blank_chain: bool = False,
                drop: Iterable[int] = ()) -> Path:
    """Copia di 1UBQ modificata come i file reali: `hetatm` riscrive i residui indicati come HETATM con un altro nome
    (es. {1: "MSE"}: selenometionina, con SD → SE come nei file di RCSB; {20: "SEP"}), `blank_chain` svuota la colonna
    22 (catena senza identificativo, come nei file di dinamica molecolare), `drop` toglie dei residui (interruzione)."""
    hetatm = hetatm or {}
    dropped = set(drop)
    rows = []
    for ln in (DATA / "1UBQ.pdb").read_text().splitlines():
        if ln.startswith("ATOM") and int(ln[22:26]) in dropped:
            continue
        if ln.startswith("ATOM") and int(ln[22:26]) in hetatm:
            name = hetatm[int(ln[22:26])]
            ln = "HETATM" + ln[6:17] + name + ln[20:]
            if name == "MSE" and ln[12:16] == " SD ":
                ln = ln[:12] + "SE  " + ln[16:76] + "SE" + ln[78:]
        if blank_chain and ln.startswith(("ATOM", "HETATM", "TER")) and len(ln) > 21:
            ln = ln[:21] + " " + ln[22:]
        rows.append(ln)
    dest.write_text("\n".join(rows) + "\n")
    return dest


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
