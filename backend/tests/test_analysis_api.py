"""Job `analysis` (services/analysis.py), rotte /api/analysis e riepilogo nel report HTML (F7)."""

from __future__ import annotations

import threading

import pytest

from bioloom.core.jobs import PRIORITY
from bioloom.domain import report
from bioloom.services.reports import ReportService

from .conftest import DATA, HUMAN_UB, YEAST_UB, wait_job

UBQ = {"type": "template", "id": "1UBQ"}


def _upload(client, name: str, template_id: str) -> str:
    with open(DATA / name, "rb") as fh:
        r = client.post("/api/templates/upload", files={"file": (name, fh, "chemical/x-pdb")},
                        data={"template_id": template_id})
    assert r.status_code == 200, r.text
    return r.json()["id"]


def _analysis(client, body: dict) -> dict:
    r = client.post("/api/analysis", json=body)
    assert r.status_code == 200, r.text
    job = wait_job(client, r.json()["id"])
    assert job["status"] == "completed", job.get("error")
    return job


def _fasta(rows: list[str]) -> str:
    return "".join(f">s{k}\n{row}\n" for k, row in enumerate(rows))


def _aligned_rows() -> list[str]:
    rows = [HUMAN_UB, YEAST_UB]
    for k in range(10):
        s = list(HUMAN_UB)
        s[19] = "ACDEFGHIKL"[k]
        rows.append("".join(s))
    return rows


def test_registration_priority(client):
    registration = client.app.state.core.jobs._handlers["analysis"]
    assert PRIORITY["analysis"] == 0 and registration.priority == 0 and registration.pool == "cpu"


def test_analysis_job_on_1ubq(client_with_ubq):
    job = _analysis(client_with_ubq, {"source": UBQ})
    res = job["result"]
    assert job["kind"] == "analysis" and job["title"] == "Analisi strutturale · 1UBQ"
    assert res["label"] == "1UBQ" and len(res["source_sha256"]) == 64
    assert {k: v for k, v in res["source"].items() if v is not None} == UBQ
    assert res["structure"]["residues"] == 76 and res["structure"]["chains"] == 1
    s = res["interactions"]["summary"]
    assert s["salt_bridges"] == 3 and s["disulfides"] == 0 and s["hbonds_backbone"] == 45
    assert res["interfaces"]["pairs"] == [] and res["conservation"] is None
    log = client_with_ubq.get(f"/api/jobs/{job['id']}/log").json()["text"]
    assert "Analisi strutturale in" in log and "3 ponti salini" in log


def test_conservation_from_aligned_text(client_with_ubq):
    job = _analysis(client_with_ubq, {"source": UBQ, "msa": {"text": _fasta(_aligned_rows())}})
    cons = job["result"]["conservation"]
    assert cons["source"] == "allineamento fornito" and cons["sequences"] == 12
    by = {r["resnum"]: r for r in cons["residues"]}
    assert by["1"]["score"] == 1.0 and by["20"]["score"] < 0.1
    assert cons["chains"][0]["row"] == "s0"


def test_conservation_from_unaligned_sequences(client_with_ubq):
    """Sequenze di lunghezza diversa: allineamento multiplo nel job (center-star integrato), poi la conservazione."""
    text = _fasta([HUMAN_UB, YEAST_UB, HUMAN_UB[5:], YEAST_UB[:60]])
    job = _analysis(client_with_ubq, {"source": UBQ, "msa": {"text": text, "tool": "builtin"}})
    cons = job["result"]["conservation"]
    assert cons["source"].startswith("allineamento multiplo calcolato") and cons["sequences"] == 4
    assert cons["chains"][0]["identity"] == 100.0 and cons["chains"][0]["mapped"] == 76
    log = client_with_ubq.get(f"/api/jobs/{job['id']}/log").json()["text"]
    assert "allineamento multiplo di 4 sequenze" in log


def test_conservation_with_selenocysteine(client_with_ubq):
    """Una selenoproteina (U) nel FASTA: il job completa, sia con le righe già allineate sia con l'allineamento
    center-star integrato, che conserva le lettere originali."""
    c = client_with_ubq
    seleno = HUMAN_UB[:10] + "U" + HUMAN_UB[11:]
    job = _analysis(c, {"source": UBQ, "msa": {"text": _fasta([seleno, *_aligned_rows()[1:]])}})
    assert job["result"]["interactions"]["summary"]["salt_bridges"] == 3
    assert job["result"]["conservation"]["chains"][0]["mapped"] == 76
    text = _fasta([seleno, YEAST_UB, HUMAN_UB[5:], YEAST_UB[:60]])
    job = _analysis(c, {"source": UBQ, "msa": {"text": text, "tool": "builtin", "row": "s0"}})
    cons = job["result"]["conservation"]
    assert cons["source"].startswith("allineamento multiplo calcolato") and cons["chains"][0]["row"] == "s0"
    assert {r["resnum"]: r for r in cons["residues"]}["11"]["row_aa"] == "U"


def test_row_by_the_name_in_the_file(client_with_ubq):
    """La riga si indica anche con il nome del FASTA (le righe sono rinominate con sanitize_code); una riga che non
    c'è è un errore prima di accodare il job."""
    c = client_with_ubq
    rows = _aligned_rows()
    text = f">sp|P0CG47|UBB_HUMAN Polyubiquitin-B\n{rows[0]}\n" + _fasta(rows[1:])
    for row in ("sp|P0CG47|UBB_HUMAN", "sp_P0CG47_UBB_HUMAN", "sp|P0CG47|UBB_HUMAN Polyubiquitin-B",
                ">sp|P0CG47|UBB_HUMAN"):
        job = _analysis(c, {"source": UBQ, "msa": {"text": text, "row": row}})
        assert job["result"]["conservation"]["chains"][0]["row"] == "sp_P0CG47_UBB_HUMAN", row
    r = c.post("/api/analysis", json={"source": UBQ, "msa": {"text": text, "row": "sp|Q00000|NOPE"}})
    assert r.status_code == 400 and r.json()["error"]["code"] == "conservation_invalid"
    assert "non è nell'allineamento" in r.json()["error"]["message"] and "sp_P0CG47_UBB_HUMAN" in \
        r.json()["error"]["message"]
    # sequenze non allineate: i nomi restano quelli dei record nell'allineamento multiplo
    unaligned = f">sp|P0CG47|UBB_HUMAN\n{HUMAN_UB}\n>yeast\n{YEAST_UB[:60]}\n"
    job = _analysis(c, {"source": UBQ, "msa": {"text": unaligned, "tool": "builtin", "row": "sp|P0CG47|UBB_HUMAN"}})
    assert job["result"]["conservation"]["chains"][0]["row"] == "sp_P0CG47_UBB_HUMAN"
    r = c.post("/api/analysis", json={"source": UBQ, "msa": {"text": unaligned, "row": "human"}})
    assert r.status_code == 400 and r.json()["error"]["code"] == "conservation_invalid"
    # job di allineamento: i nomi del job sono già sanificati
    records = [{"name": "sp|P0CG47|UBB_HUMAN", "sequence": rows[0]}, {"name": "b", "sequence": rows[1]}]
    aln = c.post("/api/align", json={"mode": "multiple", "records": records, "tool": "builtin"}).json()
    assert wait_job(c, aln["id"])["status"] == "completed"
    job = _analysis(c, {"source": UBQ, "msa": {"job_id": aln["id"], "row": "sp|P0CG47|UBB_HUMAN"}})
    assert job["result"]["conservation"]["chains"][0]["row"] == "sp_P0CG47_UBB_HUMAN"
    r = c.post("/api/analysis", json={"source": UBQ, "msa": {"job_id": aln["id"], "row": "zzz"}})
    assert r.status_code == 400 and r.json()["error"]["code"] == "conservation_invalid"


def test_conservation_from_alignment_job(client_with_ubq):
    c = client_with_ubq
    records = [{"name": f"s{k}", "sequence": row} for k, row in enumerate(_aligned_rows())]
    aln = c.post("/api/align", json={"mode": "multiple", "records": records, "tool": "builtin"}).json()
    assert wait_job(c, aln["id"])["status"] == "completed"
    job = _analysis(c, {"source": UBQ, "msa": {"job_id": aln["id"], "row": "s0"}})
    cons = job["result"]["conservation"]
    assert aln["id"] in cons["source"] and cons["chains"][0]["row"] == "s0"
    assert {r["resnum"]: r for r in cons["residues"]}["20"]["score"] < 0.1


def test_request_errors(client_with_ubq):
    c = client_with_ubq
    first = _analysis(c, {"source": UBQ})
    r = c.post("/api/analysis", json={"source": UBQ, "msa": {"job_id": first["id"]}})
    assert r.status_code == 400 and r.json()["error"]["code"] == "conservation_invalid"
    r = c.post("/api/analysis", json={"source": UBQ, "msa": {"job_id": "0123456789abcdef"}})
    assert r.status_code == 404
    r = c.post("/api/analysis", json={"source": UBQ, "msa": {}})
    assert r.status_code == 400
    r = c.post("/api/analysis", json={"source": UBQ, "msa": {"text": f">a\n{HUMAN_UB}\n"}})
    assert r.status_code == 400 and r.json()["error"]["code"] == "conservation_invalid"
    r = c.post("/api/analysis", json={"source": UBQ, "msa": {"text": ">a\nAAA-\n>b\nAA\n>c\nA-A\n"}})
    assert r.status_code == 200  # righe di lunghezza diversa: si riallineano
    r = c.post("/api/analysis", json={"source": {"type": "template", "id": "9ZZZ"}})
    assert r.status_code == 404
    r = c.post("/api/analysis", json={"source": {"type": "nope"}})
    assert r.status_code == 422


def test_incomplete_sources_are_clear_errors(client_with_ubq):
    """Sorgenti senza il file o il job (campi facoltativi nulli): errore chiaro o nessuna analisi, mai un 500."""
    c = client_with_ubq
    for params in ({"type": "upload"}, {"type": "job", "job_id": "abc"}, {"type": "job"}, {"type": "template"}):
        r = c.get("/api/analysis/latest", params=params)
        assert r.status_code == 200 and r.json()["job_id"] is None, (params, r.text)
    for source in ({"type": "upload"}, {"type": "job", "job_id": "abc"}, {"type": "job", "file": "m.pdb"}):
        for route in ("/api/analysis", "/api/quality"):
            r = c.post(route, json={"source": source})
            assert r.status_code == 400 and r.json()["error"]["code"] == "invalid_source", (route, source, r.text)


def test_conservation_failure_fails_the_job(client_with_ubq):
    """Allineamento che non contiene la sequenza della struttura: job fallito con il motivo."""
    unrelated = "ACDEFGHIKLMNPQRSTVWY" * 4
    r = client_with_ubq.post("/api/analysis", json={"source": UBQ, "msa": {"text": _fasta([unrelated, unrelated])}})
    job = wait_job(client_with_ubq, r.json()["id"])
    assert job["status"] == "failed" and "Nessuna catena" in job["error"]


def test_latest_by_source_and_fingerprint(client_with_ubq):
    c = client_with_ubq
    assert c.get("/api/analysis/latest", params={"type": "template", "id": "1UBQ"}).json()["job_id"] is None
    first = _analysis(c, {"source": UBQ})
    second = _analysis(c, {"source": {"type": "template", "id": "1ubq"}})  # stessa sorgente, scritta diversamente
    latest = c.get("/api/analysis/latest", params={"type": "template", "id": "1UBQ"}).json()
    assert latest["job_id"] == second["id"] != first["id"]
    tid = _upload(c, "ubq_dimer.pdb", "UBQD")
    assert c.get("/api/analysis/latest", params={"type": "template", "id": tid}).json()["job_id"] is None
    # lo stesso id con un altro file: l'analisi precedente non vale più
    dimer = _analysis(c, {"source": {"type": "template", "id": tid}})
    assert c.get("/api/analysis/latest", params={"type": "template", "id": tid}).json()["job_id"] == dimer["id"]
    _upload(c, "1UBQ.pdb", tid)
    assert c.get("/api/analysis/latest", params={"type": "template", "id": tid}).json()["job_id"] is None


def test_latest_reports_the_running_analysis(client_with_ubq, monkeypatch):
    """Un'analisi in corso sulla stessa struttura è `active_job_id` (la pagina Qualità la riprende invece di proporne
    un'altra); finita, diventa `job_id`."""
    from bioloom.services import analysis as analysis_service

    c = client_with_ubq
    first = _analysis(c, {"source": UBQ})
    gate, started = threading.Event(), threading.Event()
    original = analysis_service.interactions.analyze

    def slow(model):
        started.set()
        assert gate.wait(30)
        return original(model)

    monkeypatch.setattr(analysis_service.interactions, "analyze", slow)
    job = c.post("/api/analysis", json={"source": UBQ}).json()
    try:
        assert started.wait(30)
        latest = c.get("/api/analysis/latest", params={"type": "template", "id": "1UBQ"}).json()
        assert latest["job_id"] == first["id"] and latest["active_job_id"] == job["id"]
        other = c.get("/api/analysis/latest", params={"type": "template", "id": "2XYZ"}).json()
        assert other == {"job_id": None, "finished_at": None, "active_job_id": None}
    finally:
        gate.set()
    assert wait_job(c, job["id"])["status"] == "completed"
    latest = c.get("/api/analysis/latest", params={"type": "template", "id": "1UBQ"}).json()
    assert latest["job_id"] == job["id"] and latest["active_job_id"] is None


def test_latest_reads_only_the_source(client_with_ubq, monkeypatch):
    """La ricerca legge solo `params.source` (i parametri possono contenere un allineamento incollato); senza le
    funzioni JSON di SQLite legge i parametri interi, con lo stesso esito."""
    import sqlite3

    c = client_with_ubq
    job = _analysis(c, {"source": UBQ, "msa": {"text": _fasta(_aligned_rows())}})
    core = c.app.state.core
    from bioloom.services.analysis import AnalysisService

    service = AnalysisService(core.jobs, core.compute)
    seen: list[str] = []
    original = core.jobs.db.query

    def spy(sql, params=()):
        seen.append(sql)
        return original(sql, params)

    monkeypatch.setattr(core.jobs.db, "query", spy)
    assert service.latest(UBQ)["id"] == job["id"]
    lookup = next(s for s in seen if "WHERE kind=?" in s)
    assert lookup.startswith("SELECT id, json_extract(params, '$.source') AS source FROM")

    def no_json(sql, params=()):
        if "json_extract" in sql:
            raise sqlite3.OperationalError("no such function: json_extract")
        return original(sql, params)

    monkeypatch.setattr(core.jobs.db, "query", no_json)
    assert service.latest(UBQ)["id"] == job["id"]
    assert service.latest({"type": "template", "id": "2XYZ"}) is None


def test_concurrent_analyses(client_with_ubq):
    c = client_with_ubq
    ids: list[str] = []
    lock = threading.Lock()

    def submit():
        r = c.post("/api/analysis", json={"source": UBQ})
        with lock:
            ids.append(r.json()["id"])

    threads = [threading.Thread(target=submit) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    jobs = [wait_job(c, i) for i in ids]
    assert all(j["status"] == "completed" for j in jobs)
    summaries = [j["result"]["interactions"]["summary"] for j in jobs]
    assert all(s == summaries[0] for s in summaries)
    assert c.get("/api/analysis/latest", params={"type": "template", "id": "1UBQ"}).json()["job_id"] in ids


def test_quality_report_includes_interfaces(client_with_ubq):
    """Report di un'analisi di qualità: la sezione "Analisi strutturale" compare con le interfacce del dimero, e non
    per 1UBQ (una catena, nessun disolfuro)."""
    c = client_with_ubq
    tid = _upload(c, "ubq_dimer.pdb", "UBQD")
    params = {"dope": False, "procheck": False, "prosa": False}
    qjob = c.post("/api/quality", json={"source": {"type": "template", "id": tid}, **params}).json()
    assert wait_job(c, qjob["id"])["status"] == "completed"
    before = c.get(f"/api/jobs/{qjob['id']}/report").text
    assert 'id="analisi"' not in before
    _analysis(c, {"source": {"type": "template", "id": tid}})
    html = c.get(f"/api/jobs/{qjob['id']}/report").text
    assert 'id="analisi"' in html and "Interfacce fra catene" in html and "Ponti disolfuro" not in html
    assert '<span class="tag err">bassa</span>' in html and "Krissinel" in html and "Janin" in html
    assert "GLN A62" in html or "A62" in html
    ujob = c.post("/api/quality", json={"source": UBQ, **params}).json()
    assert wait_job(c, ujob["id"])["status"] == "completed"
    _analysis(c, {"source": UBQ})
    assert 'id="analisi"' not in c.get(f"/api/jobs/{ujob['id']}/report").text


def test_report_lookup_for_job_models(client_with_ubq):
    """Per i modelli dei job la sorgente è {job, file}: qui il PDB sovrapposto di un allineamento strutturale (due
    catene), con la stessa ricerca del report dei modelli."""
    c = client_with_ubq
    tid = _upload(c, "ubq_dimer.pdb", "UBQD")
    aln = c.post("/api/align", json={"mode": "structural", "tool": "builtin",
                                     "structures": [UBQ, {"type": "template", "id": tid, "chain": "B"}]}).json()
    done = wait_job(c, aln["id"])
    assert done["status"] == "completed" and done["result"]["superposed_file"] == "superposed.pdb"
    core = c.app.state.core
    service = ReportService(core.jobs, core.compute, core.db)
    assert service._analysis(core.jobs.get(aln["id"]), "superposed.pdb") is None
    job = _analysis(c, {"source": {"type": "job", "job_id": aln["id"], "file": "superposed.pdb"}})
    summary = service._analysis(core.jobs.get(aln["id"]), "superposed.pdb")
    assert summary["job_id"] == job["id"] and [p["chains"] for p in summary["interfaces"]] == [["A", "B"]]


def test_report_section_render_and_escape():
    data = {"kind": "quality", "title": "t", "job_id": "abc", "tools": [("BioLoom", "4")], "warnings": [],
            "analysis": {"job_id": "j1", "finished_at": "2026-09-30T10:00:00+00:00", "disulfides": [
                {"a": {"chain": "A", "resnum": "3", "resname": "CYS"}, "b": {"chain": "B", "resnum": "40",
                                                                            "resname": "<b>X</b>"},
                 "distance": 2.05, "ca_distance": 5.6, "chi3": -87.2}],
                "interfaces": [{"chains": ["A", "B"], "area": 812.4, "buried": 1624.8, "apolar_fraction": 0.61,
                                "counts": {"A": 20, "B": 21}, "interactions": {"hbonds": 7, "salt_bridges": 2},
                                "residues": {"A": [{"chain": "A", "resnum": "5", "resname": "LEU"}], "B": []},
                                "plausibility": {"level": "compatibile", "text": "ok", "limits": "limiti & note"}}]}}
    html = report.render(data)
    assert 'id="analisi"' in html and "Ponti disolfuro" in html and "2,05" in html and "−87°" in html
    assert "&lt;b&gt;X&lt;/b&gt;" in html and "<b>X</b>" not in html and "limiti &amp; note" in html
    assert '<span class="tag ok">compatibile</span>' in html and "812" in html and "61%" in html
    assert "Richardson" in html
    assert html.count("<section") == html.count("</section>")
    assert 'id="analisi"' not in report.render({**data, "analysis": None})
    assert 'id="analisi"' not in report.render({**data, "analysis": {"disulfides": [], "interfaces": []}})


@pytest.mark.parametrize("field, value", [("chains", ["A"] * 65), ("msa", {"row": "x" * 201})])
def test_validation_limits(client_with_ubq, field, value):
    r = client_with_ubq.post("/api/analysis", json={"source": UBQ, field: value})
    assert r.status_code == 422
