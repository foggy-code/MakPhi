"""Pipeline da 100.000 sequenze: limiti di file e pipeline, pulizia delle sequenze veloce e uguale a prima, matrice a
pagine con il riepilogo del server, pagine degli elementi e job senza i dati giganti (nomi di tutte le sequenze, FASTA).
"""

from __future__ import annotations

import io
import json
import time
import zipfile

import pytest

from makphi.domain import fileformats, sequences
from makphi.services import pipeline as pipeline_service

from .conftest import until, wait_job
from .test_pipeline_cross import cross  # noqa: F401 (fixture)


# ------------------------------------------------------------------------------------------ limiti
def test_a_file_can_hold_as_many_sequences_as_a_pipeline():
    assert fileformats.MAX_RECORDS == pipeline_service.MAX_SEQUENCES == 100_000
    assert fileformats.MAX_BYTES == 200 * 1024 * 1024
    text = "".join(f">s{i}\nMKV\n" for i in range(fileformats.MAX_RECORDS + 1)).encode()
    out = fileformats.parse_records(text, "tante.fasta")
    assert len(out["records"]) == 100_000
    assert out["warnings"] == ["Il file contiene 100001 sequenze: lette le prime 100000."]


# ------------------------------------------------------------------------------------------ pulizia delle sequenze
def _old_clean(text: str, keep_gaps: bool = False) -> str:
    """La versione carattere per carattere, come riferimento."""
    import re

    allowed_extra = "-." if keep_gaps else ""
    out = re.sub(r"[\s\d*]", "", text or "").upper()
    return "".join(ch for ch in out if ch.isalpha() or ch in allowed_extra)


@pytest.mark.parametrize("text", ["mkv lq*", "MK-V.L\n12 3", "acgt-u..n", "MKVé ß", "", "  \t", "M_K#V@L", "x1y2z3*"])
def test_clean_sequence_is_unchanged(text):
    for keep in (False, True):
        assert sequences.clean_sequence(text, keep) == _old_clean(text, keep)


def test_kind_and_invalid_positions_are_unchanged():
    assert sequences.detect_kind("ACGTACGTNN") == "nucleic"
    assert sequences.detect_kind("acgu acgu") == "nucleic"
    assert sequences.detect_kind("MKVLAAGIV") == "protein"
    assert sequences.detect_kind("123-") == "protein"
    assert sequences.detect_kind("ÉÉACGT") == "protein"  # 4 su 6 lettere
    assert sequences.invalid_positions("MKV") == []
    assert sequences.invalid_positions("MK1V") == [{"position": 3, "char": "1"}]
    assert sequences.invalid_positions("MK-V", allow_gaps=True) == []
    assert sequences.invalid_positions("ACGTE", kind="nucleic") == [{"position": 5, "char": "E"}]


# ------------------------------------------------------------------------------------------ esecuzione e annullamento
def test_cancel_marks_the_waiting_items_at_once_without_starting_them(client, monkeypatch):
    core = client.app.state.core
    started: list[int] = []

    def slow(ctx, p, item, prepared=None):
        # in esecuzione finché la pipeline non viene annullata (ctx.check solleva JobCancelled): con un limite di
        # tempo un elemento poteva finire senza stato finale prima dell'annullamento, su una macchina con pochi core
        # (un elemento alla volta), e restare "running"
        started.append(item["idx"])
        core.pipeline._update(ctx.job_id, item["idx"], status="running", stage="Prova")
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            ctx.check()
            time.sleep(0.02)

    monkeypatch.setattr(core.pipeline, "_process_item", slow)
    fasta = "".join(f">s{i}\nMKVLAAGIV\n" for i in range(60))
    r = client.post("/api/pipeline", json={"fasta": fasta, "template_mode": "fixed", "parallel": 2,
                                           "templates": [{"id": "T1", "chains": ["A"]}]})
    assert r.status_code == 200, r.text
    job_id = r.json()["id"]
    until(lambda: len(started) >= 1)
    assert client.post(f"/api/jobs/{job_id}/cancel").status_code == 200
    assert wait_job(client, job_id)["status"] == "cancelled"
    counts = client.get(f"/api/pipeline/{job_id}/items").json()["counts"]
    assert counts == {"cancelled": 60}
    assert len(started) <= 4  # solo gli elementi già affidati al pool (al più 2 × parallelo)


# ------------------------------------------------------------------------------------------ matrice e pagine
def test_matrix_pages_with_the_summary_of_the_whole_matrix(cross):  # noqa: F811
    client, _core, _fake, submit = cross
    job_id = submit(names=["a", "b", "c"])
    assert wait_job(client, job_id)["status"] == "completed"
    full = client.get(f"/api/pipeline/{job_id}/matrix").json()
    assert full["sequences"] == ["a", "b", "c"] and "page" not in full
    second = client.get(f"/api/pipeline/{job_id}/matrix?page=2&size=2").json()
    assert second["sequences"] == ["c"] and second["cells"] == full["cells"][2:]
    assert (second["page"], second["pages"], second["start"], second["sequence_count"]) == (2, 2, 2, 3)
    assert second["summary"] == full["summary"]
    summary = full["summary"]
    cells = [c for row in full["cells"] for c in row]
    status = [c["status"] for c in cells]
    assert summary["pairs"] == 9 and summary["open"] == 0
    assert summary["completed"] == status.count("completed") and summary["failed"] == status.count("failed")
    best = [k for row in full["cells"] for k, c in enumerate(row) if c["best"]]
    assert summary["closed"] == len(best) and summary["wins"] == [best.count(k) for k in range(3)]
    # pagina oltre la fine: l'ultima
    assert client.get(f"/api/pipeline/{job_id}/matrix?page=9&size=2").json()["page"] == 2


def test_items_list_the_sequence_names_only_when_few(cross, monkeypatch):  # noqa: F811
    client, _core, _fake, submit = cross
    job_id = submit()
    assert wait_job(client, job_id)["status"] == "completed"
    pairs = client.get(f"/api/pipeline/{job_id}/items").json()["pairs"]
    assert pairs["sequences"] == ["a", "b"] and pairs["sequence_count"] == 2
    monkeypatch.setattr(pipeline_service, "PAIRS_LISTED", 1)
    page = client.get(f"/api/pipeline/{job_id}/items?q=b__").json()
    assert page["pairs"]["sequences"] == [] and page["pairs"]["sequence_count"] == 2
    assert {r["pair"]["sequence"] for r in page["items"]} == {"b"}  # ricerca per nome al posto del filtro


def test_job_responses_carry_only_the_start_of_a_huge_fasta(cross, monkeypatch):  # noqa: F811
    client, core, _fake, submit = cross
    monkeypatch.setattr(pipeline_service, "FASTA_SHOWN", 10)
    monkeypatch.setattr(pipeline_service, "PAIRS_LISTED", 1)
    job_id = submit()
    assert wait_job(client, job_id)["status"] == "completed"
    params = client.get(f"/api/jobs/{job_id}").json()["params"]
    assert len(params["fasta"]) == 10 and params["fasta_truncated"] is True and params["fasta_bytes"] > 10
    assert "sequences" not in params and params["sequence_count"] == 2
    # nel database, nello ZIP e per chi lavora sul job: parametri interi
    stored = core.jobs.get(job_id)["params"]
    assert len(stored["fasta"]) > 10 and len(stored["sequences"]) == 2 and "fasta_truncated" not in stored
    with zipfile.ZipFile(io.BytesIO(client.get(f"/api/pipeline/{job_id}/export.zip").content)) as zf:
        assert json.loads(zf.read("parametri.json"))["fasta"] == stored["fasta"]
    # una pipeline piccola resta com'è
    monkeypatch.setattr(pipeline_service, "FASTA_SHOWN", 10 ** 6)
    monkeypatch.setattr(pipeline_service, "PAIRS_LISTED", 1000)
    assert client.get(f"/api/jobs/{job_id}").json()["params"] == stored
