"""Etichette e preferiti (F8b): normalizzazione, rotte, filtri degli elenchi, pulizia a cascata, concorrenza."""

from __future__ import annotations

import threading

import pytest

from makphi.core.errors import AppError, NotFound
from makphi.domain.labels import MAX_TAGS_PER_ENTITY, TAG_MAX_LENGTH, normalize_tag

from .conftest import DATA, HUMAN_UB, YEAST_UB, wait_job


# ------------------------------------------------------------------------------------------ normalizzazione
@pytest.mark.parametrize("raw, expected", [
    ("Urgente", "urgente"),
    ("  urgente  ", "urgente"),
    ("#Da Rivedere", "da-rivedere"),
    ("a  -  b", "a-b"),
    ("--piega__", "piega"),
    ("Zn2+", "zn2+"),  # gli ioni tengono il "+" finale
    ("ca2+ legante", "ca2+-legante"),
    ("v1.2:finale", "v1.2:finale"),
    ("PROTEÍNA", "proteína"),
    ("x" * TAG_MAX_LENGTH, "x" * TAG_MAX_LENGTH),
])
def test_normalize_tag(raw, expected):
    assert normalize_tag(raw) == expected


def test_normalize_tag_unifies_composed_and_decomposed_accents():
    # "í" precomposto (U+00ED) e "i" + accento combinante (U+0301) sono la stessa etichetta, in forma NFC
    assert normalize_tag("proteína") == normalize_tag("proteína") == "proteína"
    assert normalize_tag("Café") == normalize_tag("café") == "café"


@pytest.mark.parametrize("raw", ["", "   ", "#", "---", "a/b", "a b/c", "x" * (TAG_MAX_LENGTH + 1), "+uno", "😀",
                                 "a\"b", "a'b", "a;b", "<script>"])
def test_normalize_tag_rejects(raw):
    with pytest.raises(AppError) as err:
        normalize_tag(raw)
    assert err.value.code == "invalid_tag" and err.value.status == 400


# ------------------------------------------------------------------------------------------ rotte
@pytest.fixture
def setup(client_with_ubq):
    c = client_with_ubq
    p = c.post("/api/projects", json={"name": "Chinasi"}).json()
    seqs = c.post(f"/api/projects/{p['id']}/sequences/import",
                  json={"fasta": f">ub_h\n{HUMAN_UB}\n>ub_y\n{YEAST_UB}\n"}).json()
    job = c.post("/api/align", json={"mode": "pairwise", "project_id": p["id"], "records": [
        {"name": "h", "sequence": HUMAN_UB}, {"name": "y", "sequence": YEAST_UB}]}).json()
    wait_job(c, job["id"], timeout=60)
    return c, p, seqs, job


def test_add_list_remove_tags_on_every_entity(setup):
    c, p, seqs, job = setup
    targets = [("project", p["id"]), ("sequence", seqs[0]["id"]), ("job", job["id"]), ("template", "1ubq")]
    for etype, eid in targets:
        r = c.post(f"/api/tags/{etype}/{eid}", json={"tag": " Da Rivedere "})
        assert r.status_code == 200, r.text
        assert r.json()["tags"] == ["da-rivedere"] and r.json()["favorite"] is False
        # stessa etichetta in un'altra forma: nessun doppione
        assert c.post(f"/api/tags/{etype}/{eid}", json={"tag": "#DA-RIVEDERE"}).json()["tags"] == ["da-rivedere"]
        assert c.post(f"/api/tags/{etype}/{eid}", json={"tag": "zn2+"}).json()["tags"] == ["da-rivedere", "zn2+"]
    assert c.get("/api/tags/template/1UBQ").json() == {"entity_type": "template", "entity_id": "1UBQ",
                                                         "tags": ["da-rivedere", "zn2+"], "favorite": False}
    counts = c.get("/api/tags").json()
    assert counts[0] == {"tag": "da-rivedere", "count": 4,
                         "types": {"job": 1, "project": 1, "sequence": 1, "template": 1}}
    assert c.get("/api/tags", params={"entity_type": "job"}).json()[0]["count"] == 1
    # rimozione idempotente (anche con l'etichetta scritta in un'altra forma)
    assert c.delete(f"/api/tags/job/{job['id']}/Zn2+").json()["tags"] == ["da-rivedere"]
    assert c.delete(f"/api/tags/job/{job['id']}/zn2+").json()["tags"] == ["da-rivedere"]


def test_tag_errors(setup):
    c, p, _seqs, _job = setup
    assert c.post("/api/tags/project/inesistente", json={"tag": "x"}).status_code == 404
    assert c.post("/api/tags/template/9ZZZ", json={"tag": "x"}).status_code == 404
    assert c.post("/api/tags/utente/abc", json={"tag": "x"}).status_code == 422
    bad = c.post(f"/api/tags/project/{p['id']}", json={"tag": "a/b"})
    assert bad.status_code == 400 and bad.json()["error"]["code"] == "invalid_tag"
    assert c.post(f"/api/tags/project/{p['id']}", json={"tag": "x" * 33}).status_code == 400
    # "../.." decodificato: identificatore rifiutato o nessuna rotta POST con quella forma
    assert c.post("/api/tags/project/..%2F..", json={"tag": "x"}).status_code in (400, 404, 405)
    bad_id = c.post("/api/tags/project/a%20b", json={"tag": "x"})
    assert bad_id.status_code == 400 and bad_id.json()["error"]["code"] == "invalid_identifier"
    for i in range(MAX_TAGS_PER_ENTITY):
        assert c.post(f"/api/tags/project/{p['id']}", json={"tag": f"t{i}"}).status_code == 200
    over = c.post(f"/api/tags/project/{p['id']}", json={"tag": "una-di-troppo"})
    assert over.status_code == 400 and over.json()["error"]["code"] == "too_many_tags"
    # un'etichetta già presente non conta come nuova
    assert c.post(f"/api/tags/project/{p['id']}", json={"tag": "T0"}).status_code == 200


def test_favorites(setup):
    c, p, seqs, job = setup
    assert c.put(f"/api/favorites/sequence/{seqs[1]['id']}").json()["favorite"] is True
    assert c.put(f"/api/favorites/sequence/{seqs[1]['id']}").json()["favorite"] is True  # idempotente
    assert c.put("/api/favorites/template/1UBQ").json()["favorite"] is True
    assert c.put("/api/favorites/job/nonesiste").status_code == 404
    favs = c.get("/api/favorites").json()
    assert {(f["entity_type"], f["entity_id"]) for f in favs} == {("sequence", seqs[1]["id"]), ("template", "1UBQ")}
    assert [f["entity_id"] for f in c.get("/api/favorites", params={"entity_type": "template"}).json()] == ["1UBQ"]
    assert c.delete("/api/favorites/template/1UBQ").json()["favorite"] is False
    assert c.delete("/api/favorites/template/1UBQ").json()["favorite"] is False


def test_lists_carry_labels_and_filter_by_tag(setup):
    c, p, seqs, job = setup
    other = c.post("/api/projects", json={"name": "Altro"}).json()
    c.post(f"/api/tags/project/{p['id']}", json={"tag": "chinasi"})
    c.put(f"/api/favorites/project/{other['id']}")
    projects = {x["id"]: x for x in c.get("/api/projects").json()}
    assert projects[p["id"]]["tags"] == ["chinasi"] and projects[other["id"]]["favorite"] is True
    assert [x["id"] for x in c.get("/api/projects", params={"tag": "Chinasi"}).json()] == [p["id"]]
    assert [x["id"] for x in c.get("/api/projects", params={"favorite": True}).json()] == [other["id"]]
    without = {x["id"] for x in c.get("/api/projects", params={"favorite": False}).json()}
    assert p["id"] in without and other["id"] not in without
    assert c.get("/api/projects", params={"tag": "a/b"}).status_code == 400
    # sequenze
    c.post(f"/api/tags/sequence/{seqs[0]['id']}", json={"tag": "umana"})
    listed = c.get(f"/api/projects/{p['id']}/sequences").json()
    assert [s["tags"] for s in listed] == [["umana"], []]
    assert [s["id"] for s in c.get(f"/api/projects/{p['id']}/sequences", params={"tag": "umana"}).json()] == \
        [seqs[0]["id"]]
    # job: filtro in SQL prima del limite, anche fra tutti i progetti
    second = c.post("/api/align", json={"mode": "pairwise", "records": [
        {"name": "h", "sequence": HUMAN_UB}, {"name": "y", "sequence": YEAST_UB}]}).json()
    wait_job(c, second["id"], timeout=60)
    c.post(f"/api/tags/job/{job['id']}", json={"tag": "riferimento"})
    c.put(f"/api/favorites/job/{second['id']}")
    jobs = c.get("/api/jobs").json()
    assert {j["id"]: (j["tags"], j["favorite"]) for j in jobs} == {
        job["id"]: (["riferimento"], False), second["id"]: ([], True)}
    assert [j["id"] for j in c.get("/api/jobs", params={"tag": "riferimento", "limit": 1}).json()] == [job["id"]]
    assert [j["id"] for j in c.get("/api/jobs", params={"favorite": True}).json()] == [second["id"]]
    assert c.get("/api/jobs", params={"tag": "riferimento", "favorite": True}).json() == []
    assert c.get("/api/jobs", params={"tag": "nessuna"}).json() == []
    # template
    c.post("/api/tags/template/1UBQ", json={"tag": "ubiquitina"})
    templates = c.get("/api/templates").json()
    assert templates[0]["id"] == "1UBQ" and templates[0]["tags"] == ["ubiquitina"]
    assert [t["id"] for t in c.get("/api/templates", params={"tag": "ubiquitina"}).json()] == ["1UBQ"]
    assert c.get("/api/templates", params={"tag": "altro"}).json() == []


def test_cascade_cleanup(setup):
    c, p, seqs, job = setup
    core = c.app.state.core
    for etype, eid in (("project", p["id"]), ("sequence", seqs[0]["id"]), ("sequence", seqs[1]["id"]),
                       ("job", job["id"]), ("template", "1UBQ")):
        c.post(f"/api/tags/{etype}/{eid}", json={"tag": "pulizia"})
        c.put(f"/api/favorites/{etype}/{eid}")
    # sequenza eliminata
    c.delete(f"/api/projects/{p['id']}/sequences/{seqs[0]['id']}")
    assert core.labels.get("sequence", seqs[0]["id"]) == {"entity_type": "sequence", "entity_id": seqs[0]["id"],
                                                          "tags": [], "favorite": False}
    # progetto eliminato: i suoi job (uno per uno) e le sue sequenze (in cascata) perdono etichette e stella
    assert c.delete(f"/api/projects/{p['id']}").status_code == 200
    for etype, eid in (("project", p["id"]), ("sequence", seqs[1]["id"]), ("job", job["id"])):
        assert core.labels.get(etype, eid)["tags"] == [] and not core.labels.get(etype, eid)["favorite"]
    # template eliminato dalla libreria
    assert core.labels.get("template", "1UBQ")["tags"] == ["pulizia"]
    c.delete("/api/templates/1UBQ")
    assert core.labels.get("template", "1UBQ") == {"entity_type": "template", "entity_id": "1UBQ", "tags": [],
                                                   "favorite": False}
    assert core.db.scalar("SELECT COUNT(*) FROM tags") == 0 and core.db.scalar("SELECT COUNT(*) FROM favorites") == 0


def test_job_delete_cleans_labels(setup):
    c, _p, _seqs, job = setup
    c.post(f"/api/tags/job/{job['id']}", json={"tag": "x"})
    c.put(f"/api/favorites/job/{job['id']}")
    assert c.delete(f"/api/jobs/{job['id']}").status_code == 200
    assert c.get(f"/api/tags/job/{job['id']}").json() == {"entity_type": "job", "entity_id": job["id"], "tags": [],
                                                          "favorite": False}


def test_templates_removed_outside_the_app_are_pruned(client_with_ubq, tmp_path):
    c = client_with_ubq
    core = c.app.state.core
    c.post("/api/tags/template/1UBQ", json={"tag": "fuori"})
    c.put("/api/favorites/template/1UBQ")
    (core.paths.templates / "1UBQ.pdb").unlink()  # cancellato a mano dalla cartella
    assert c.get("/api/tags").json() == []  # i conteggi non contano i template spariti
    assert core.db.scalar("SELECT COUNT(*) FROM favorites") == 0
    # e all'avvio: un template ricaricato non eredita etichette di uno precedente con lo stesso nome
    with open(DATA / "1UBQ.pdb", "rb") as fh:
        c.post("/api/templates/upload", files={"file": ("1UBQ.pdb", fh, "chemical/x-pdb")})
    assert c.get("/api/tags/template/1UBQ").json()["tags"] == []


def test_prune_skips_missing_library_folder(client):
    core = client.app.state.core
    core.db.execute("INSERT INTO tags VALUES ('template', '1UBQ', 'x', '2026-01-01T00:00:00+00:00')")
    root = core.library.root
    root.rename(root.with_name("templates_altrove"))
    try:
        assert core.labels.prune_templates() == 0
        assert core.db.scalar("SELECT COUNT(*) FROM tags") == 1
    finally:
        root.with_name("templates_altrove").rename(root)
    assert core.labels.prune_templates() == 1


def test_concurrent_adds_respect_the_limit(setup):
    c, p, _seqs, _job = setup
    core = c.app.state.core
    errors: list[str] = []
    barrier = threading.Barrier(8)

    def worker(n: int) -> None:
        barrier.wait()
        for i in range(4):
            try:
                core.labels.add_tag("project", p["id"], f"t{n}-{i}")
            except AppError as exc:
                errors.append(exc.code)

    threads = [threading.Thread(target=worker, args=(n,)) for n in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    tags = core.labels.get("project", p["id"])["tags"]
    assert len(tags) == MAX_TAGS_PER_ENTITY  # 32 richieste distinte, mai oltre il limite
    assert len(errors) == 32 - MAX_TAGS_PER_ENTITY and set(errors) == {"too_many_tags"}


def test_concurrent_same_tag_is_stored_once(setup):
    c, p, _seqs, _job = setup
    core = c.app.state.core
    barrier = threading.Barrier(10)

    def worker() -> None:
        barrier.wait()
        core.labels.add_tag("project", p["id"], "Stessa Etichetta")

    threads = [threading.Thread(target=worker) for _ in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert core.labels.get("project", p["id"])["tags"] == ["stessa-etichetta"]


def test_template_deleted_while_tagging_leaves_no_orphan(client_with_ubq, monkeypatch):
    """Il template sparisce fra il controllo e l'inserimento: la verifica successiva toglie l'etichetta."""
    core = client_with_ubq.app.state.core
    answers = iter([True, False])
    monkeypatch.setattr(core.labels.library, "exists", lambda _tid: next(answers))
    with pytest.raises(NotFound):
        core.labels.add_tag("template", "1UBQ", "x")
    assert core.db.scalar("SELECT COUNT(*) FROM tags") == 0
