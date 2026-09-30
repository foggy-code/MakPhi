"""Cache dei calcoli (core/cache.py): ricerche e allineamenti riusati, invalidazione, tolleranza agli errori.

Nessun accesso alla rete né a MODELLER: i motori sono simulati e contano le chiamate.
"""

import json
import os
import threading
import time
from datetime import datetime
from pathlib import Path

import pytest

from bioloom.config import SettingsStore, UserSettings
from bioloom.core import cache
from bioloom.core.errors import AppError, RemoteServiceError
from bioloom.core.jobs import JobContext, ProcessResult
from bioloom.engines import align, remote, search, tools

from .conftest import HUMAN_UB, YEAST_UB, wait_job
from .test_search import BLAST_XML


def _hit(pid="1UBQ", identity=96.0, coverage=100.0) -> dict:
    return {"pdb_id": pid, "chain": "A", "template_id": f"{pid}_A", "title": "UBIQUITIN", "description": "",
            "organism": "Homo sapiens", "method": "X-RAY DIFFRACTION", "resolution": 1.8, "identity": identity,
            "positives": identity, "coverage": coverage, "evalue": 1e-30, "bitscore": 150.0, "align_length": 76,
            "query_start": 1, "query_end": 76, "subject_start": 1, "subject_end": 76, "query_aligned": HUMAN_UB,
            "subject_aligned": HUMAN_UB, "subject_length": 76, "source": "rcsb"}


@pytest.fixture
def fake_rcsb(monkeypatch):
    """RCSB simulato: registra ogni ricerca effettivamente eseguita."""
    calls = []

    def fake(sequence, evalue=10.0, max_hits=50, check=None, log=None):
        calls.append((sequence, evalue, max_hits))
        return [_hit()]

    monkeypatch.setattr(remote, "rcsb_sequence_search", fake)
    return calls


def _search(client, sequence=YEAST_UB, **extra) -> dict:
    body = {"query": {"name": "q", "sequence": sequence}, "engine": "rcsb", "evalue": 0.001, "max_hits": 50,
            **extra}
    job = client.post("/api/search", json=body).json()
    return wait_job(client, job["id"])


def _align(client, body: dict) -> dict:
    job = client.post("/api/align", json=body).json()
    assert "id" in job, job
    return wait_job(client, job["id"])


def _entries(client, operation: str) -> list[Path]:
    folder = client.app.state.core.paths.compute_cache / operation
    return sorted(folder.glob("*.json")) if folder.exists() else []


def _log(client, job_id: str) -> str:
    return client.get(f"/api/jobs/{job_id}/log").json()["text"]


def _job_files(job: dict) -> set[str]:
    return {f["path"] for f in job["files"]} - {"job.log", "params.json", "result.json"}


class FakeCtx:
    """Contesto minimo per provare `run_cached` senza JobManager."""

    def __init__(self, workdir: Path, settings=None) -> None:
        workdir.mkdir(parents=True, exist_ok=True)
        self.workdir = workdir
        self.settings = settings or UserSettings()
        self.cancelled = False
        self.logs: list[str] = []

    def log(self, message: str) -> None:
        self.logs.append(message)


# ------------------------------------------------------------------------------------------ ricerca
def test_second_identical_search_does_not_run_the_engine(client, fake_rcsb):
    first = _search(client)
    assert first["status"] == "completed", first
    assert not first["result"].get("cached")
    second = _search(client)
    assert second["status"] == "completed"
    assert len(fake_rcsb) == 1
    res = second["result"]
    assert res["cached"] is True
    datetime.fromisoformat(res["cached_at"])
    assert res["hits"] == first["result"]["hits"] and res["best"] == "1UBQ_A"
    assert res["query"] == first["result"]["query"]
    assert "Risultato dalla cache del" in _log(client, second["id"])
    assert len(_entries(client, "search")) == 1


def test_changed_parameter_or_sequence_is_a_miss(client, fake_rcsb):
    _search(client)
    _search(client, evalue=0.01)
    _search(client, max_hits=10)
    _search(client, sequence=HUMAN_UB)
    assert len(fake_rcsb) == 4
    # stessa sequenza scritta diversamente (minuscole, a capo) e filtro tassonomico, che RCSB non usa: stessa chiave
    same = _search(client, sequence=YEAST_UB.lower()[:40] + "\n" + YEAST_UB.lower()[40:], taxid="9606")
    assert same["result"]["cached"] is True and len(fake_rcsb) == 4
    assert len(_entries(client, "search")) == 4


def test_use_cache_false_recomputes_and_replaces_the_entry(client, fake_rcsb):
    _search(client)
    entry = _entries(client, "search")[0]
    created = json.loads(entry.read_text(encoding="utf-8"))["created"]
    forced = _search(client, use_cache=False)
    assert forced["status"] == "completed" and not forced["result"].get("cached")
    assert len(fake_rcsb) == 2
    assert "ignorata su richiesta" in _log(client, forced["id"])
    replaced = json.loads(entry.read_text(encoding="utf-8"))["created"]
    assert replaced > created
    again = _search(client)
    assert again["result"]["cached"] is True and len(fake_rcsb) == 2
    assert again["result"]["cached_at"] == cache.iso(replaced)


def test_remote_search_expires_after_a_week(client, fake_rcsb):
    _search(client)
    entry = _entries(client, "search")[0]
    data = json.loads(entry.read_text(encoding="utf-8"))
    data["created"] -= 6 * 86400
    entry.write_text(json.dumps(data), encoding="utf-8")
    assert _search(client)["result"]["cached"] is True  # 6 giorni: ancora valida
    data["created"] -= 2 * 86400
    entry.write_text(json.dumps(data), encoding="utf-8")
    expired = _search(client)
    assert not expired["result"].get("cached") and len(fake_rcsb) == 2
    assert json.loads(entry.read_text(encoding="utf-8"))["created"] > data["created"]  # voce rinnovata


def test_corrupted_entry_is_ignored_and_rewritten(client, fake_rcsb):
    _search(client)
    entry = _entries(client, "search")[0]
    entry.write_bytes(b'{"format": 1, "result": [tronc')
    again = _search(client)
    assert again["status"] == "completed" and not again["result"].get("cached")
    assert len(fake_rcsb) == 2
    assert json.loads(entry.read_text(encoding="utf-8"))["result"]["hits"]
    assert _search(client)["result"]["cached"] is True


def test_failed_search_is_not_cached(client, monkeypatch):
    def unavailable(*_a, **_k):
        raise RemoteServiceError("RCSB Search ha risposto con errore 500.")

    monkeypatch.setattr(remote, "rcsb_sequence_search", unavailable)
    done = _search(client)
    assert done["status"] == "failed"
    assert not _entries(client, "search")


@pytest.fixture
def local_blast(client, monkeypatch, tmp_path):
    """BLAST+ locale simulato: database pdbaa finto, eseguibile finto, ctx.run che conta le esecuzioni."""
    db_dir = client.app.state.core.paths.databases / "pdbaa"
    db_dir.mkdir(parents=True)
    for ext in (".pin", ".phr", ".psq"):
        (db_dir / f"pdbaa{ext}").write_bytes(b"x" * 64)
    exe = tmp_path / "bin" / "blastp"
    exe.parent.mkdir()
    exe.write_text("#!/bin/sh\n")
    monkeypatch.setattr(tools, "find", lambda key: str(exe) if key == "blastp" else None)
    runs = []

    def fake_run(self, cmd, **kwargs):
        runs.append(cmd)
        return ProcessResult(0, BLAST_XML)

    monkeypatch.setattr(JobContext, "run", fake_run)
    meta = {"calls": 0, "fail": False}

    def fake_metadata(ids, check=None, log=None):
        meta["calls"] += 1
        if meta["fail"]:
            raise RemoteServiceError("RCSB GraphQL non raggiungibile.")
        return {i: {"method": "X-RAY DIFFRACTION", "resolution": 1.8, "title": "Ubiquitina"} for i in ids}

    monkeypatch.setattr(remote, "rcsb_metadata", fake_metadata)
    return {"db": db_dir, "exe": exe, "runs": runs, "meta": meta}


def test_local_database_stamp_invalidates_the_cache(client, local_blast):
    runs = local_blast["runs"]
    first = _search(client, engine="local")
    assert first["status"] == "completed", first
    second = _search(client, engine="local")
    assert second["result"]["cached"] is True and len(runs) == 1
    assert _job_files(second) == _job_files(first) == {"query.fasta"}  # stessi file del job
    # i motori locali non scadono dopo 7 giorni: vale il timbro del database
    entry = _entries(client, "search")[0]
    data = json.loads(entry.read_text(encoding="utf-8"))
    data["created"] -= 10 * 86400
    entry.write_text(json.dumps(data), encoding="utf-8")
    assert _search(client, engine="local")["result"]["cached"] is True and len(runs) == 1
    # database aggiornato (dimensione e data cambiano): mancato riscontro
    pin = local_blast["db"] / "pdbaa.pin"
    pin.write_bytes(b"y" * 80)
    later = time.time() + 120
    os.utime(pin, (later, later))
    assert not _search(client, engine="local")["result"].get("cached") and len(runs) == 2
    # nuova versione di blastp: mancato riscontro
    os.utime(local_blast["exe"], (later + 60, later + 60))
    assert not _search(client, engine="local")["result"].get("cached") and len(runs) == 3
    assert _search(client, engine="local")["result"]["cached"] is True and len(runs) == 3


def test_partial_result_without_metadata_is_not_cached(client, local_blast):
    local_blast["meta"]["fail"] = True
    done = _search(client, engine="local")
    assert done["status"] == "completed"
    assert "Metadati RCSB non disponibili" in _log(client, done["id"])
    assert not _entries(client, "search")
    local_blast["meta"]["fail"] = False
    _search(client, engine="local")
    assert _search(client, engine="local")["result"]["cached"] is True
    assert len(local_blast["runs"]) == 2


def test_database_stamp_covers_every_file_of_the_database(tmp_path):
    folder = tmp_path / "pdbaa_mmseqs"
    folder.mkdir()
    for name in ("pdbaa", "pdbaa.index", "pdbaa.dbtype", "pdbaa_h", "pdbaa_h.index", "altro.txt", "pdbaa-meta.json"):
        (folder / name).write_text(name)
    stamp = search.database_stamp(folder / "pdbaa")
    assert [row[0] for row in stamp] == ["pdbaa", "pdbaa.dbtype", "pdbaa.index", "pdbaa_h", "pdbaa_h.index"]
    (folder / "pdbaa_h.index").write_text("cambiato")
    assert search.database_stamp(folder / "pdbaa") != stamp


def test_global_switch_disables_the_cache(client, fake_rcsb):
    assert client.put("/api/settings", json={"compute_cache": False}).json()["compute_cache"] is False
    _search(client)
    second = _search(client)
    assert not second["result"].get("cached") and len(fake_rcsb) == 2
    assert not _entries(client, "search")  # disattivata: né letture né scritture
    assert client.put("/api/settings", json={"compute_cache": True}).json()["compute_cache"] is True
    _search(client)
    assert _search(client)["result"]["cached"] is True and len(fake_rcsb) == 3


def test_previous_settings_get_the_cache_enabled(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"contact_email": "a@b.it", "qmean_mode": "auto", "qmean_remote": True}),
                    encoding="utf-8")
    settings = SettingsStore(path).get()
    assert settings.compute_cache is True
    assert settings.contact_email == "a@b.it" and settings.qmean_mode == "local" and settings.qmean_fallback_remote
    path.write_text(json.dumps({"contact_email": "a@b.it", "compute_cache": None}), encoding="utf-8")
    settings = SettingsStore(path).get()
    assert settings.compute_cache is True and settings.contact_email == "a@b.it"  # le altre restano
    store = SettingsStore(path)
    assert store.update({"compute_cache": False}).compute_cache is False
    assert SettingsStore(path).get().compute_cache is False


# ------------------------------------------------------------------------------------------ allineamenti
MSA_RECORDS = [{"name": "h", "sequence": HUMAN_UB}, {"name": "y", "sequence": YEAST_UB},
               {"name": "t", "sequence": HUMAN_UB[5:60]}]


def test_multiple_alignment_is_cached(client, monkeypatch):
    calls = []
    original = align.center_star

    def counting(*args, **kwargs):
        calls.append(1)
        return original(*args, **kwargs)

    monkeypatch.setattr(align, "center_star", counting)
    body = {"mode": "multiple", "tool": "builtin", "records": MSA_RECORDS}
    first = _align(client, body)
    second = _align(client, body)
    assert first["status"] == second["status"] == "completed"
    assert len(calls) == 1 and second["result"]["cached"] is True
    for key in ("aligned", "ids", "identity_matrix", "conservation", "method"):
        assert second["result"][key] == first["result"][key]
    assert _job_files(second) == _job_files(first) == {"alignment.fasta", "alignment.aln"}
    # ordine o nomi diversi: mancato riscontro; use_cache=False: ricalcolo
    assert not _align(client, {**body, "records": MSA_RECORDS[::-1]})["result"].get("cached")
    renamed = [{**MSA_RECORDS[0], "name": "umana"}, *MSA_RECORDS[1:]]
    assert not _align(client, {**body, "records": renamed})["result"].get("cached")
    assert not _align(client, {**body, "use_cache": False})["result"].get("cached")
    assert len(calls) == 4


def test_pairwise_alignment_is_not_cached(client):
    body = {"mode": "pairwise", "records": MSA_RECORDS[:2]}
    _align(client, body)
    assert not _align(client, body)["result"].get("cached")
    assert not client.app.state.core.paths.compute_cache.exists() or not any(
        client.app.state.core.paths.compute_cache.rglob("*.json"))


def test_cli_msa_output_files_restored_and_tool_update_invalidates(client, monkeypatch, tmp_path):
    exe = tmp_path / "bin" / "clustalo"
    exe.parent.mkdir()
    exe.write_text("#!/bin/sh\n")
    monkeypatch.setattr(tools, "find", lambda key: str(exe) if key == "clustalo" else None)
    runs = []

    def fake_clustalo(self, cmd, **kwargs):
        runs.append(cmd)
        src, dst = Path(cmd[cmd.index("-i") + 1]), Path(cmd[cmd.index("-o") + 1])
        dst.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")  # sequenze già della stessa lunghezza
        return ProcessResult(0, "Clustal Omega simulato\n")

    monkeypatch.setattr(JobContext, "run", fake_clustalo)
    body = {"mode": "multiple", "tool": "clustalo", "records": MSA_RECORDS[:2]}
    first = _align(client, body)
    assert first["status"] == "completed", first
    second = _align(client, body)
    assert second["result"]["cached"] is True and len(runs) == 1
    assert _job_files(second) == _job_files(first) == {"msa_input.fasta", "msa_output.fasta", "alignment.fasta",
                                                       "alignment.aln"}
    for name in ("msa_input.fasta", "msa_output.fasta"):
        assert client.get(f"/api/jobs/{second['id']}/files/{name}").content == \
            client.get(f"/api/jobs/{first['id']}/files/{name}").content
    later = time.time() + 120
    os.utime(exe, (later, later))  # Clustal Omega aggiornato
    assert not _align(client, body)["result"].get("cached") and len(runs) == 2


STRUCTURAL = {"mode": "structural", "tool": "builtin", "structures": [
    {"type": "template", "id": "1UBQ", "chain": "A"}, {"type": "template", "id": "1UBQ", "chain": "A"}]}


def test_structural_alignment_recreates_the_superposed_pdb(client_with_ubq, ubq_path):
    c = client_with_ubq
    first = _align(c, STRUCTURAL)
    second = _align(c, STRUCTURAL)
    assert first["status"] == second["status"] == "completed"
    assert second["result"]["cached"] is True and not first["result"].get("cached")
    assert second["result"]["tm_score"] == first["result"]["tm_score"]
    assert second["result"]["superposed_file"] == "superposed.pdb" and "superposed_pdb" not in second["result"]
    assert _job_files(second) == _job_files(first) == {"superposed.pdb", "alignment.fasta", "alignment.aln"}
    pdb_first = c.get(f"/api/jobs/{first['id']}/files/superposed.pdb").content
    assert pdb_first.startswith(b"ATOM")
    assert c.get(f"/api/jobs/{second['id']}/files/superposed.pdb").content == pdb_first
    entry = json.loads(_entries(c, "align_structural")[0].read_text(encoding="utf-8"))
    assert entry["result"]["superposed_pdb"].encode("utf-8") == pdb_first

    # stesso contenuto con un altro nome (upload): riscontro, con i nomi nuovi
    up = c.post("/api/files/structure", files={"file": ("copia.pdb", ubq_path.read_bytes())}).json()
    renamed = _align(c, {**STRUCTURAL, "structures": [STRUCTURAL["structures"][0], {**up["source"], "chain": "A"}]})
    assert renamed["result"]["cached"] is True
    assert renamed["result"]["ids"][1].endswith("copiaA") and renamed["result"]["ids"][0] == "1UBQA"

    # contenuto diverso (una coordinata spostata): mancato riscontro
    lines = ubq_path.read_text(encoding="utf-8").splitlines(keepends=True)
    i = next(n for n, line in enumerate(lines) if line.startswith("ATOM"))
    lines[i] = lines[i][:30] + f"{float(lines[i][30:38]) + 0.5:8.3f}" + lines[i][38:]
    moved = c.post("/api/files/structure", files={"file": ("mossa.pdb", "".join(lines).encode())}).json()
    changed = _align(c, {**STRUCTURAL, "structures": [STRUCTURAL["structures"][0], {**moved["source"], "chain": "A"}]})
    assert changed["status"] == "completed" and not changed["result"].get("cached")
    # catena indicata o no (vuota = prima catena): chiavi diverse, anche se il risultato coincide
    assert not _align(c, {**STRUCTURAL, "structures": [STRUCTURAL["structures"][0],
                                                       {"type": "template", "id": "1UBQ"}]})["result"].get("cached")


def test_tmalign_output_files_are_restored_from_cache(client_with_ubq, monkeypatch, tmp_path):
    c = client_with_ubq
    exe = tmp_path / "TMalign"
    exe.write_text("")
    monkeypatch.setattr(tools, "find", lambda key: str(exe) if key == "tmalign" else None)
    runs = []

    def fake_tmalign(self, cmd, **kwargs):
        runs.append(cmd)
        prefix = cmd[cmd.index("-o") + 1]
        Path(prefix + "_all_atm").write_text(Path(cmd[2]).read_text(encoding="utf-8"), encoding="utf-8")
        Path(prefix + "_all_atm.pml").write_text("load superposed_all_atm\n", encoding="utf-8")
        out = ("Aligned length=   76, RMSD=   0.00, Seq_ID=n_identical/n_aligned= 1.000\n"
               "TM-score= 1.00000 (if normalized by length of Chain_1)\n"
               "TM-score= 1.00000 (if normalized by length of Chain_2)\n"
               '(":" denotes residue pairs of d <  5.0 Angstrom, "." denotes other aligned residues)\n'
               f"{HUMAN_UB}\n{':' * 76}\n{HUMAN_UB}\n")
        return ProcessResult(0, out)

    monkeypatch.setattr(JobContext, "run", fake_tmalign)
    body = {**STRUCTURAL, "tool": "tmalign"}
    first = _align(c, body)
    assert first["status"] == "completed", first
    assert first["result"]["tm_score"] == 1.0
    second = _align(c, body)
    assert second["result"]["cached"] is True and len(runs) == 1
    expected = {"mobile.pdb", "target.pdb", "superposed_all_atm", "superposed_all_atm.pml", "superposed.pdb",
                "alignment.fasta", "alignment.aln"}
    assert _job_files(first) == _job_files(second) == expected
    for name in expected:
        assert c.get(f"/api/jobs/{second['id']}/files/{name}").content == \
            c.get(f"/api/jobs/{first['id']}/files/{name}").content


def test_unrecognized_tmalign_output_is_not_cached(client_with_ubq, monkeypatch, tmp_path):
    exe = tmp_path / "TMalign"
    exe.write_text("")
    monkeypatch.setattr(tools, "find", lambda key: str(exe) if key == "tmalign" else None)
    monkeypatch.setattr(JobContext, "run", lambda self, cmd, **kw: ProcessResult(0, "formato sconosciuto\n"))
    done = _align(client_with_ubq, {**STRUCTURAL, "tool": "tmalign"})
    assert done["status"] == "completed"
    assert not _entries(client_with_ubq, "align_structural")


def test_cancelled_job_does_not_write_the_cache(client, monkeypatch):
    started, release = threading.Event(), threading.Event()

    def slow_center_star(records, kind="protein", ctx=None):
        started.set()
        release.wait(10)
        ctx.check()
        return [r.sequence for r in records]

    monkeypatch.setattr(align, "center_star", slow_center_star)
    job = client.post("/api/align", json={"mode": "multiple", "tool": "builtin", "records": MSA_RECORDS[:2]}).json()
    assert started.wait(10)
    client.post(f"/api/jobs/{job['id']}/cancel")
    release.set()
    assert wait_job(client, job["id"])["status"] == "cancelled"
    assert not _entries(client, "align_multiple")


# ------------------------------------------------------------------------------------------ pipeline
def _pipeline(client, body: dict) -> dict:
    job = client.post("/api/pipeline", json=body).json()
    assert "id" in job, job
    return wait_job(client, job["id"])


# identità sotto la soglia con il filtro attivo: gli elementi vengono "filtrati" e MODELLER non serve
TWILIGHT = {"filter": True, "identity": 25.0, "coverage": 40.0}


def test_pipeline_reuses_the_search_cache(client, monkeypatch):
    calls = []

    def fake(sequence, evalue=10.0, max_hits=50, check=None, log=None):
        calls.append(sequence)
        return [_hit(identity=20.0)]

    monkeypatch.setattr(remote, "rcsb_sequence_search", fake)
    body = {"fasta": f">a\n{HUMAN_UB}\n>b\n{YEAST_UB}\n", "twilight": TWILIGHT, "parallel": 1}
    first = _pipeline(client, body)
    assert first["status"] == "completed", first
    assert first["result"]["counts"] == {"filtered": 2} and first["result"]["search_cache_hits"] == 0
    assert len(calls) == 2
    # stesse sequenze, altre opzioni di modellazione: le ricerche vengono dalla cache
    second = _pipeline(client, {**body, "options": {"models": 5, "refinement": "normal"}})
    assert second["status"] == "completed" and len(calls) == 2
    assert second["result"]["search_cache_hits"] == 2
    items = client.get(f"/api/pipeline/{second['id']}/items").json()["items"]
    assert all(datetime.fromisoformat(i["metrics"]["search_cached_at"]) for i in items)
    assert all(i["template"] == "1UBQ_A" and i["identity"] == 20.0 for i in items)
    log = _log(client, second["id"])
    assert "Risultato dalla cache del" in log and "2 ricerche di template riusate" in log
    # ricerca forzata
    third = _pipeline(client, {**body, "use_cache": False})
    assert len(calls) == 4 and third["result"]["search_cache_hits"] == 0
    items = client.get(f"/api/pipeline/{third['id']}/items").json()["items"]
    assert not any(i["metrics"].get("search_cached_at") for i in items)


def test_pipeline_concurrent_identical_searches_leave_a_valid_entry(client, monkeypatch):
    lock = threading.Lock()
    calls = []

    def slow(sequence, evalue=10.0, max_hits=50, check=None, log=None):
        with lock:
            calls.append(sequence)
        time.sleep(0.2)
        return [_hit(identity=20.0)]

    monkeypatch.setattr(remote, "rcsb_sequence_search", slow)
    body = {"fasta": "".join(f">s{i}\n{YEAST_UB}\n" for i in range(4)), "twilight": TWILIGHT, "parallel": 4}
    done = _pipeline(client, body)
    assert done["status"] == "completed" and done["result"]["counts"] == {"filtered": 4}
    assert 1 <= len(calls) <= 4  # in parallelo più elementi possono calcolare la stessa ricerca
    entries = _entries(client, "search")
    assert len(entries) == 1 and cache.lookup("search", entries[0].stem) is not None
    assert not list(entries[0].parent.glob(".*.tmp"))  # nessun file temporaneo rimasto
    before = len(calls)
    again = _pipeline(client, body)
    assert len(calls) == before and again["result"]["search_cache_hits"] == 4


def test_selftest_always_runs_the_tools(monkeypatch, tmp_path):
    """La verifica dell'installazione non deve mai rispondere con un risultato dalla cache."""
    from types import SimpleNamespace

    from bioloom.services.selftest import SelfTestService

    seen = []

    def recorder(name, result):
        return lambda *a, **k: seen.append((name, k.get("use_cache"))) or result

    monkeypatch.setattr(tools, "find", lambda key: f"/usr/bin/{key}")
    monkeypatch.setattr(search, "local_database", lambda _d: tmp_path / "pdbaa")
    monkeypatch.setattr(search, "mmseqs_database", lambda _d: tmp_path / "pdbaa")
    monkeypatch.setattr(search, "search", recorder("search", {"hits": []}))
    monkeypatch.setattr(align, "multiple", recorder("multiple", {"aligned": ["MQIF"]}))
    monkeypatch.setattr(align, "structural", recorder("structural", {"tm_score": 1.0, "rmsd": 0.0}))
    ctx = SimpleNamespace(workdir=tmp_path, sub=lambda name, workdir: ctx)
    service = SelfTestService(SimpleNamespace(register=lambda *a, **k: None), tmp_path)
    service._blast(ctx, tmp_path)
    service._mmseqs(ctx, tmp_path)
    SelfTestService._msa(ctx, "clustalo")
    SelfTestService._structural(ctx, "tmalign")
    assert seen == [("search", False), ("search", False), ("multiple", False), ("structural", False)]


# ------------------------------------------------------------------------------------------ pulizia e spazio
def test_housekeeping_cleans_and_reports_the_compute_cache(client, fake_rcsb):
    paths = client.app.state.core.paths
    _search(client)
    entry = _entries(client, "search")[0]
    stale_tmp = entry.parent / f".{entry.name}.abc123.tmp"
    stale_tmp.write_text("scrittura interrotta")
    remote_entry = paths.cache / "uniprot" / "risposta.json"
    remote_entry.parent.mkdir(parents=True)
    remote_entry.write_text("{}" * 50)
    storage = client.get("/api/system/storage").json()
    assert storage["compute_cache"] == entry.stat().st_size + stale_tmp.stat().st_size
    assert storage["cache"] == remote_entry.stat().st_size  # solo le risposte remote
    assert client.post("/api/system/cleanup").json()["cache"] == 0  # niente di scaduto
    old = time.time() - 31 * 86400
    for path in (entry, stale_tmp):
        os.utime(path, (old, old))
    report = client.post("/api/system/cleanup").json()
    assert report["cache"] == 2 and not entry.exists() and not stale_tmp.exists() and remote_entry.exists()


# ------------------------------------------------------------------------------------------ modulo core/cache
def test_key_is_canonical_and_sensitive():
    a = cache.make_key("search", {"x": 1, "y": [1, 2], "z": {"b": 1, "a": 2}})
    assert a == cache.make_key("search", {"z": {"a": 2, "b": 1}, "y": [1, 2], "x": 1}) and len(a) == 64
    assert a != cache.make_key("search", {"x": 1, "y": [2, 1], "z": {"b": 1, "a": 2}})
    assert a != cache.make_key("align_multiple", {"x": 1, "y": [1, 2], "z": {"b": 1, "a": 2}})
    with pytest.raises(ValueError):
        cache.make_key("search", {"x": float("nan")})


def test_without_configuration_the_cache_does_nothing(tmp_path):
    cache.configure(None)
    ctx = FakeCtx(tmp_path / "work")
    calls = []
    for _ in range(2):
        assert cache.run_cached(ctx, "search", {"a": 1}, lambda: calls.append(1) or {"x": 1}) == {"x": 1}
    assert len(calls) == 2 and not ctx.logs
    assert cache.store("search", cache.make_key("search", {"a": 1}), {"x": 1}) is None
    assert cache.lookup("search", cache.make_key("search", {"a": 1})) is None


def _valid_entry(entry_key: str, **over) -> dict:
    return {"format": cache.FORMAT_VERSION, "operation": "search", "key": entry_key, "created": time.time(),
            "result": {"x": 1}, "files": {}, **over}


@pytest.mark.parametrize("make", [
    lambda key: b"",
    lambda key: b"\x00\xff\xfe spazzatura",
    lambda key: b"[1, 2, 3]",
    lambda key: json.dumps(_valid_entry(key, format=999)).encode(),
    lambda key: json.dumps(_valid_entry(key, key="0" * 64)).encode(),
    lambda key: json.dumps(_valid_entry(key, operation="align_multiple")).encode(),
    lambda key: json.dumps(_valid_entry(key, result=[1])).encode(),
    lambda key: json.dumps(_valid_entry(key, files={"a.txt": 3})).encode(),
    lambda key: json.dumps(_valid_entry(key, created="ieri")).encode(),
])
def test_lookup_tolerates_invalid_entries(tmp_path, make):
    cache.configure(tmp_path)
    key = cache.make_key("search", {"a": 1})
    path = tmp_path / "search" / f"{key}.json"
    path.parent.mkdir()
    path.write_bytes(make(key))
    assert cache.lookup("search", key) is None
    path.write_bytes(json.dumps(_valid_entry(key)).encode())
    assert cache.lookup("search", key)["result"] == {"x": 1}  # la stessa voce, valida, viene letta


def test_ttl_and_clock_from_another_computer(tmp_path):
    cache.configure(tmp_path)
    key = cache.make_key("search", {"a": 1})
    entry = cache.store("search", key, {"x": 1, "cached": True, "cached_at": "vecchio"})
    assert "cached" not in entry["result"]  # la provenienza non si salva
    now = entry["created"]
    assert cache.lookup("search", key, ttl=60, now=now + 30) is not None
    assert cache.lookup("search", key, ttl=60, now=now + 61) is None
    assert cache.lookup("search", key, ttl=None, now=now + 365 * 86400) is not None
    assert cache.lookup("search", key, now=now - 3600) is not None  # orologi poco diversi
    assert cache.lookup("search", key, now=now - 2 * 86400) is None  # voce "dal futuro"


def test_files_written_by_the_computation_are_restored_byte_for_byte(tmp_path):
    cache.configure(tmp_path / "cache")
    first = FakeCtx(tmp_path / "w1")
    (first.workdir / "prima.txt").write_text("già presente")
    (first.workdir / "job.log").write_text("log")

    def compute():
        (first.workdir / "sub").mkdir()
        (first.workdir / "sub" / "out.txt").write_bytes("riga\r\nè\n".encode("utf-8"))
        (first.workdir / ".tmp").mkdir()
        (first.workdir / ".tmp" / "x").write_text("temporaneo")
        (first.workdir / "job.log").write_text("log aggiornato")
        return {"ok": True}

    cache.run_cached(first, "align_multiple", {"a": 1}, compute)
    second = FakeCtx(tmp_path / "w2")
    res = cache.run_cached(second, "align_multiple", {"a": 1}, lambda: pytest.fail("ricalcolato"))
    assert res["ok"] is True and res["cached"] is True
    assert (second.workdir / "sub" / "out.txt").read_bytes() == "riga\r\nè\n".encode("utf-8")
    assert sorted(p.relative_to(second.workdir).as_posix() for p in second.workdir.rglob("*") if p.is_file()) \
        == ["sub/out.txt"]
    assert any("Risultato dalla cache del" in m for m in second.logs)


def test_large_or_binary_outputs_are_not_cached(tmp_path, monkeypatch):
    cache.configure(tmp_path / "cache")
    ctx = FakeCtx(tmp_path / "w")

    def binary():
        (ctx.workdir / "b.bin").write_bytes(b"\xff\xfe\x00")
        return {"x": 1}

    def large():
        (ctx.workdir / "big.txt").write_text("x" * 11)
        return {"x": 2}

    assert cache.run_cached(ctx, "search", {"a": 1}, binary) == {"x": 1}
    monkeypatch.setattr(cache, "MAX_FILES_BYTES", 10)
    assert cache.run_cached(ctx, "search", {"a": 2}, large) == {"x": 2}
    assert not list((tmp_path / "cache").rglob("*.json"))
    assert sum("non salvato nella cache" in m for m in ctx.logs) == 2


def test_cancelled_or_incomplete_results_are_not_stored(tmp_path):
    cache.configure(tmp_path / "cache")
    ctx = FakeCtx(tmp_path / "w")

    def cancelled_meanwhile():
        ctx.cancelled = True
        return {"x": 1}

    cache.run_cached(ctx, "search", {"a": 1}, cancelled_meanwhile)
    ctx.cancelled = False
    cache.run_cached(ctx, "search", {"a": 1}, lambda: {"x": 1}, keep=lambda r: False)
    assert not list((tmp_path / "cache").rglob("*.json"))
    cache.run_cached(ctx, "search", {"a": 1}, lambda: {"x": 1})
    assert len(list((tmp_path / "cache").rglob("*.json"))) == 1


def test_cache_errors_never_fail_the_computation(tmp_path, monkeypatch):
    cache.configure(tmp_path / "cache")
    ctx = FakeCtx(tmp_path / "w")

    def disk_full(*_a, **_k):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(cache, "atomic_write_text", disk_full)
    assert cache.run_cached(ctx, "search", {"a": 1}, lambda: {"x": 1}) == {"x": 1}
    assert any("non aggiornata" in m for m in ctx.logs)
    # voce con percorsi fuori dalla cartella di lavoro: non ripristinata, si ricalcola
    monkeypatch.undo()
    key = cache.make_key("search", {"b": 1})
    cache.store("search", key, {"x": 1}, {"../fuori.txt": "x"})
    calls = []
    assert cache.run_cached(ctx, "search", {"b": 1}, lambda: calls.append(1) or {"x": 2}) == {"x": 2}
    assert calls and not (tmp_path / "fuori.txt").exists()
    cache.store("search", key, {"x": 1}, {"/assoluto.txt": "x"})
    with pytest.raises((ValueError, AppError)):
        cache._restore_files(ctx.workdir, {"/assoluto.txt": "x"})


def test_file_stamp_and_content_hash(tmp_path):
    f = tmp_path / "a.pdb"
    f.write_text("ATOM\n")
    stamp = cache.file_stamp(f)
    assert stamp["size"] == 5 and isinstance(stamp["mtime"], int)
    assert cache.file_stamp(tmp_path / "manca") == {"path": str(tmp_path / "manca"), "missing": True}
    assert cache.file_stamp(None) is None
    g = tmp_path / "b.pdb"
    g.write_text("ATOM\n")
    assert cache.content_hash(f) == cache.content_hash(g)
    g.write_text("ATOM \n")
    assert cache.content_hash(f) != cache.content_hash(g)
