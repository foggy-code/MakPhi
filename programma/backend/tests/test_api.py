from tests.conftest import HUMAN_UB, YEAST_UB, until, wait_job


def test_health_and_unknown_endpoint(client):
    assert client.get("/api/health").json()["status"] == "ok"
    r = client.get("/api/does-not-exist")
    assert r.status_code == 404 and r.json()["error"]["code"] == "not_found"


def test_projects_sequences_documents(client):
    default = client.get("/api/projects").json()
    assert len(default) == 1  # progetto creato al primo avvio
    p = client.post("/api/projects", json={"name": "Chinasi"}).json()
    seqs = client.post(f"/api/projects/{p['id']}/sequences/import",
                       json={"fasta": f">ub_h umana\n{HUMAN_UB}\n>ub_y\n{YEAST_UB}\n"}).json()
    assert [s["name"] for s in seqs] == ["ub_h", "ub_y"]
    assert client.get(f"/api/projects/{p['id']}/sequences").json()[1]["length"] == 76
    bad = client.post(f"/api/projects/{p['id']}/sequences", json={"name": "x", "sequence": "  "})
    assert bad.status_code == 400 and bad.json()["error"]["code"] == "empty_sequence"
    doc = client.post(f"/api/projects/{p['id']}/documents",
                      json={"name": "aln", "kind": "pir", "content": ">P1;a\nsequence:a::::::::\nAC*\n"}).json()
    upd = client.put(f"/api/projects/{p['id']}/documents/{doc['id']}",
                     json={"name": "aln2", "kind": "pir", "content": "x"}).json()
    assert upd["name"] == "aln2" and upd["content"] == "x"
    assert client.delete(f"/api/projects/{p['id']}").status_code == 200
    assert client.get(f"/api/projects/{p['id']}").status_code == 404


def test_path_traversal_rejected(client):
    assert client.get("/api/projects/..%2F..%2Fetc").status_code in (400, 404)
    r = client.get("/api/jobs/abc/files/..%2F..%2Fbioloom.db")
    assert r.status_code in (400, 404)


def test_template_library(client_with_ubq):
    c = client_with_ubq
    lst = c.get("/api/templates").json()
    assert lst[0]["id"] == "1UBQ" and lst[0]["resolution"] == 1.8 and lst[0]["chains"] == ["A"]
    detail = c.get("/api/templates/1UBQ").json()
    assert detail["chains"][0]["ss"].count("H") > 10
    assert c.get("/api/templates/1UBQ/file").text.startswith("HEADER")
    bad = c.post("/api/templates/upload", files={"file": ("x.txt", b"ciao", "text/plain")})
    assert bad.status_code == 400
    assert c.delete("/api/templates/1UBQ").status_code == 200
    assert c.get("/api/templates/1UBQ").status_code == 404


def test_pairwise_alignment_job(client):
    job = client.post("/api/align", json={"mode": "pairwise", "records": [
        {"name": "h", "sequence": HUMAN_UB}, {"name": "y", "sequence": YEAST_UB}]}).json()
    done = wait_job(client, job["id"])
    assert done["status"] == "completed", done
    assert done["result"]["stats"]["identities"] == 73
    files = {f["path"] for f in done["files"]}
    assert {"alignment.fasta", "alignment.aln"} <= files


def test_multiple_alignment_builtin(client):
    job = client.post("/api/align", json={"mode": "multiple", "tool": "builtin", "records": [
        {"name": "h", "sequence": HUMAN_UB}, {"name": "y", "sequence": YEAST_UB},
        {"name": "t", "sequence": HUMAN_UB[5:60]}]}).json()
    done = wait_job(client, job["id"])
    assert done["status"] == "completed", done
    res = done["result"]
    assert len({len(s) for s in res["aligned"]}) == 1 and res["identity_matrix"]["values"][0][1] > 95


def test_quality_job_and_upload(client_with_ubq, ubq_path):
    c = client_with_ubq
    job = c.post("/api/quality", json={"source": {"type": "template", "id": "1UBQ"}, "dope": False}).json()
    done = wait_job(c, job["id"])
    assert done["status"] == "completed", done
    assert done["result"]["grade"]["grade"] == "A"
    with open(ubq_path, "rb") as fh:
        up = c.post("/api/quality/upload", files={"file": ("mio.pdb", fh)}, data={"dope": "false"}).json()
    assert wait_job(c, up["id"])["status"] == "completed"


def test_structural_alignment_builtin(client_with_ubq):
    c = client_with_ubq
    job = c.post("/api/align", json={"mode": "structural", "tool": "builtin", "structures": [
        {"type": "template", "id": "1UBQ", "chain": "A"}, {"type": "template", "id": "1UBQ", "chain": "A"}]}).json()
    done = wait_job(c, job["id"])
    assert done["status"] == "completed", done
    assert done["result"]["tm_score"] > 0.99 and done["result"]["rmsd"] < 0.01
    assert "superposed.pdb" in {f["path"] for f in done["files"]}


def test_validation_errors_are_readable(client):
    r = client.post("/api/model", json={"templates": []})
    assert r.status_code == 400
    r = client.post("/api/model", json={"target": {"name": "t", "sequence": YEAST_UB},
                                        "templates": [{"id": "1UBQ"}], "options": {"models": 0}})
    assert r.status_code == 400 and "numero di modelli" in r.json()["error"]["message"]
    r = client.post("/api/pir/validate", json={"content": "niente"}).json()
    assert r["valid"] is False


def test_settings_errors_are_short_and_in_italian(client):
    """Un'impostazione rifiutata dal modello (validatore o vincolo del campo): messaggio breve in italiano con il nome
    del campo come nella pagina, mai il testo di pydantic (inglese, con il link a errors.pydantic.dev)."""
    r = client.put("/api/settings", json={"contact_email": "non-valida"})
    assert r.status_code == 400
    assert r.json()["error"] == {"code": "invalid_settings",
                                 "message": "Impostazioni non valide: Email di contatto: indirizzo email non valido."}
    r = client.put("/api/settings", json={"max_workers": 300, "qmean_mode": "altrove", "qmean_uniclust": "x" * 301})
    message = r.json()["error"]["message"]
    assert r.status_code == 400 and "pydantic" not in message and "Input should" not in message
    assert "Job di calcolo in parallelo: deve essere al massimo 256" in message
    assert "Dove calcolare QMEAN: valore non ammesso (ammessi: 'local' o 'remote')" in message
    assert "Versione di UniClust30/UniRef30: al massimo 300 caratteri" in message
    r = client.put("/api/settings", json={"job_timeout_minutes": 0})
    assert r.json()["error"]["message"] == "Impostazioni non valide: Timeout per processo (minuti): deve essere almeno 1."
    assert client.get("/api/settings").json()["contact_email"] == ""  # niente salvato


def test_pipeline_items_pagination(client):
    # Sottomissione con motore inesistente per MODELLER assente: si verifica solo la gestione degli elementi.
    core = client.app.state.core
    core.jobs.register("pipeline", lambda ctx, p: {"total": 0})
    job = core.pipeline.submit({"fasta": f">a\n{HUMAN_UB}\n>b\n{YEAST_UB}\n>a\n{HUMAN_UB}\n",
                                "template_mode": "auto", "options": {}}, None)
    page = client.get(f"/api/pipeline/{job['id']}/items?size=2&sort=name&direction=desc").json()
    assert page["total"] == 3 and page["pages"] == 2
    assert [i["name"] for i in page["items"]] == ["b", "a_2"]
    csv_text = client.get(f"/api/pipeline/{job['id']}/export.csv").text
    assert csv_text.splitlines()[0].startswith("idx,name,length,status")
    # il job finisce prima della chiusura dell'app: altrimenti scriverebbe l'esito sul database già chiuso
    wait_job(client, job["id"])
    until(lambda: not core.jobs.busy(job["id"]))
