"""pdbaa, database MMseqs2 e indice locale: un job alla volta (risorsa PDBAA_FILES) e cartelle di lavoro per job.

Il download da NCBI è simulato: ogni download aspetta il suo via libera, così due aggiornamenti inviati uno dopo
l'altro girerebbero insieme se la risorsa esclusiva non li separasse."""

import contextlib
import hashlib
import io
import tarfile
import threading
from types import SimpleNamespace

import httpx
import pytest

from makphi.core.jobs import RESOURCE_STAGE
from makphi.engines import tools
from makphi.services import databases
from makphi.services.databases import PDBAA_FILES

from .conftest import dispatcher_settled, until, wait_job

KINDS = ("db_update_pdbaa", "db_build_mmseqs", "db_index_mmseqs")


def _archive() -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for name in ("pdbaa.pin", "pdbaa.phr", "pdbaa.psq"):
            data = f"{name}\n".encode() * 16
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return buf.getvalue()


class FakeNcbi:
    """NCBI simulato: un via libera per download; conta gli aggiornamenti in corso (dal download allo scambio)."""

    def __init__(self, swap_in):
        self.archive = _archive()
        self.lock = threading.Lock()
        self.gates: list[threading.Event] = []
        self.active = 0
        self.peak = 0
        self.work_dirs = []
        self._swap_in = swap_in

    @contextlib.contextmanager
    def stream(self, _method, _url, **_kw):
        with self.lock:
            self.active += 1
            self.peak = max(self.peak, self.active)
            gate = threading.Event()
            self.gates.append(gate)
        assert gate.wait(20), "download mai sbloccato"
        yield SimpleNamespace(status_code=200, headers={"content-length": str(len(self.archive))},
                              iter_bytes=lambda _size: iter([self.archive]))

    def get(self, _url, **_kw):
        return SimpleNamespace(text=f"{hashlib.md5(self.archive).hexdigest()}  pdbaa.tar.gz\n")

    def swap_in(self, new_dir, final_dir, job_id):
        self.work_dirs.append(new_dir)
        self._swap_in(new_dir, final_dir, job_id)
        with self.lock:
            self.active -= 1


@pytest.fixture
def ncbi(monkeypatch):
    fake = FakeNcbi(databases._swap_in)
    monkeypatch.setattr(databases, "httpx", SimpleNamespace(stream=fake.stream, get=fake.get, Timeout=httpx.Timeout))
    monkeypatch.setattr(databases, "_swap_in", fake.swap_in)
    return fake


def _job(client, job_id: str) -> dict:
    return client.get(f"/api/jobs/{job_id}").json()


def test_pdbaa_jobs_share_one_exclusive_resource_in_the_same_pool(client):
    handlers = client.app.state.core.jobs._handlers
    for kind in KINDS:
        assert handlers[kind].pool == "io" and handlers[kind].resource({}) == PDBAA_FILES, kind
    assert RESOURCE_STAGE[PDBAA_FILES].startswith("In coda")


def test_two_pdbaa_updates_never_run_together(client, ncbi):
    data = client.app.state.core.paths.databases
    (data / "pdbaa.new").mkdir(parents=True)  # cartella a nome fisso di una versione precedente, interrotta
    (data / "pdbaa.old-0123456789abcdef").mkdir()
    (data / "pdbaa.copia").mkdir()  # non è una cartella di lavoro: resta
    first = client.post("/api/databases/pdbaa/update").json()["id"]
    until(lambda: len(ncbi.gates) == 1)
    second = client.post("/api/databases/pdbaa/update").json()["id"]
    until(lambda: _job(client, second)["stage"] == RESOURCE_STAGE[PDBAA_FILES])
    # coda riesaminata dopo la segnalazione: il secondo resta in coda, nessun secondo download
    until(lambda: dispatcher_settled(client.app.state.core.jobs, "io"))
    assert _job(client, second)["status"] == "queued" and len(ncbi.gates) == 1
    ncbi.gates[0].set()
    assert wait_job(client, first, timeout=20)["status"] == "completed"
    until(lambda: len(ncbi.gates) == 2)  # parte solo dopo la fine del primo
    ncbi.gates[1].set()
    done = wait_job(client, second, timeout=20)
    assert done["status"] == "completed", done["error"]
    assert ncbi.peak == 1
    # una cartella di lavoro per job, con il suo id; nessuna resta accanto al database
    assert [d.name for d in ncbi.work_dirs] == [f"pdbaa.new-{first}", f"pdbaa.new-{second}"]
    assert sorted(p.name for p in data.iterdir()) == ["pdbaa", "pdbaa.copia"]
    assert (data / "pdbaa" / "pdbaa.pin").is_file()


def test_mmseqs_build_waits_for_the_pdbaa_update(client, ncbi, monkeypatch):
    monkeypatch.setattr(tools, "find", lambda _name: None)  # senza MMseqs2: il job fallisce appena parte
    update = client.post("/api/databases/pdbaa/update").json()["id"]
    until(lambda: len(ncbi.gates) == 1)
    build = client.post("/api/databases/mmseqs/build").json()["id"]
    index = client.post("/api/databases/mmseqs/index").json()["id"]
    until(lambda: _job(client, build)["stage"] == RESOURCE_STAGE[PDBAA_FILES])
    until(lambda: _job(client, index)["stage"] == RESOURCE_STAGE[PDBAA_FILES])
    until(lambda: dispatcher_settled(client.app.state.core.jobs, "io"))
    assert _job(client, build)["status"] == "queued" and _job(client, index)["status"] == "queued"
    ncbi.gates[0].set()
    assert wait_job(client, update, timeout=20)["status"] == "completed"
    built = wait_job(client, build, timeout=20)
    assert built["status"] == "failed" and "MMseqs2" in built["error"]  # è partito, dopo l'aggiornamento
    assert wait_job(client, index, timeout=20)["status"] == "failed"


def test_failed_extraction_leaves_no_work_dir(client, ncbi):
    ncbi.archive = b"non un archivio"  # MD5 giusto, contenuto illeggibile per tarfile
    data = client.app.state.core.paths.databases
    job_id = client.post("/api/databases/pdbaa/update").json()["id"]
    until(lambda: len(ncbi.gates) == 1)
    ncbi.gates[0].set()
    assert wait_job(client, job_id, timeout=20)["status"] == "failed"
    assert not [p.name for p in data.iterdir() if p.name.startswith("pdbaa")]
