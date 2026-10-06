"""Ricerca globale (F8b): pertinenza, gruppi, limiti, caratteri speciali e tentativi di iniezione, template su file,
etichette e preferiti, indice allineato alle modifiche. Ogni test gira con FTS5 e con il ripiego LIKE: i due percorsi
devono dare gli stessi risultati (stessa regola di `domain/textsearch.py`)."""

from __future__ import annotations

import shutil
import time

import pytest
from fastapi.testclient import TestClient

from bioloom.app import create_app
from bioloom.config import Paths
from bioloom.core.db import Database
from bioloom.domain import textsearch
from bioloom.services import globalsearch
from bioloom.services.globalsearch import GlobalSearch, header_organism
from bioloom.services.labels import LabelService
from bioloom.services.templates import TemplateLibrary

from .conftest import DATA, HUMAN_UB, YEAST_UB, wait_job


# ------------------------------------------------------------------------------------------ logica pura
def test_tokens_and_terms():
    assert textsearch.tokens("sp_P69905_HBA_HUMAN") == ["sp", "p69905", "hba", "human"]
    assert textsearch.tokens("Proteína ÙMANA") == ["proteina", "umana"]
    assert textsearch.query_terms('  "Ubq"  ubq  NEAR(x) ') == ["ubq", "near", "x"]
    assert textsearch.query_terms("%_*\\'\";--") == []
    assert len(textsearch.query_terms(" ".join(f"w{i}" for i in range(20)))) == textsearch.MAX_TERMS
    assert textsearch.query_terms("x" * 500) == ["x" * textsearch.MAX_TERM_LENGTH]


def test_fts_and_like_escaping():
    assert textsearch.fts_query(["ubq", "a"]) == '"ubq"* "a"*'
    assert textsearch.fts_query(['a"b']) == '"a""b"*'  # mai prodotto da query_terms, ma comunque protetto
    assert textsearch.like_escape("50%_a\\b") == "%50\\%\\_a\\\\b%"


def test_score_table():
    t = textsearch.query_terms
    assert textsearch.score(t("chinasi"), "Chinasi") == 13.0  # parola intera (3) + nome identico (10)
    assert textsearch.score(t("chinasi"), "Chinasi di lievito") == 7.0  # parola intera (3) + inizio del nome (4)
    assert textsearch.score(t("chin"), "Chinasi di lievito") == 6.0  # inizio di parola (2) + inizio del nome (4)
    assert textsearch.score(t("lievito"), "Chinasi di lievito") == 3.0
    assert textsearch.score(t("chinasi"), "Studio", ["chinasi umane"]) == 1.5
    assert textsearch.score(t("chin"), "Studio", ["chinasi umane"]) == 1.0
    assert textsearch.score(t("chinasi topo"), "Chinasi", ["umane"]) == 0.0  # tutte le parole devono esserci
    assert textsearch.score([], "x") == 0.0
    assert textsearch.matches(t("p6990 hba"), "sp_P69905_HBA_HUMAN")
    assert not textsearch.matches(t("ubq"), "1UBQ")  # inizio di parola, non sottostringa


def test_header_organism():
    assert header_organism((DATA / "1UBQ.pdb").read_text()) == "HOMO SAPIENS"
    cif = "data_x\n_entity_src_gen.pdbx_gene_src_scientific_name   'Saccharomyces cerevisiae'\n"
    assert header_organism(cif) == "Saccharomyces cerevisiae"
    loop = "loop_\n_entity_src_gen.pdbx_gene_src_scientific_name\n_entity_src_gen.x\n'Homo sapiens' 1\n"
    assert header_organism(loop) == ""  # nei loop non si legge (valore non sulla stessa riga)
    assert header_organism("_entity_src_nat.pdbx_organism_scientific ?\n") == ""
    assert header_organism("HEADER    X\nATOM ...\n") == ""


# ------------------------------------------------------------------------------------------ ricerca via API
@pytest.fixture(params=["fts5", "like"])
def sclient(request, tmp_path, monkeypatch):
    monkeypatch.setenv("BIOLOOM_SEARCH_FTS", "1" if request.param == "fts5" else "0")
    app = create_app(Paths(tmp_path / "data"), frontend_dist=None)
    with TestClient(app) as c:
        assert c.app.state.core.search.engine == request.param
        yield c


def search(c, q="", **params) -> dict:
    r = c.get("/api/search", params={"q": q, **params})
    assert r.status_code == 200, r.text
    return r.json()


def ids(result: dict, group: str) -> list[str]:
    return next(([i["id"] for i in g["items"]] for g in result["groups"] if g["type"] == group), [])


def groups(result: dict) -> list[str]:
    return [g["type"] for g in result["groups"]]


@pytest.fixture
def data(sclient):
    c = sclient
    projects = {name: c.post("/api/projects", json={"name": name, "description": desc}).json()
                for name, desc in [("Chinasi", ""), ("Chinasi di lievito", "Saccharomyces"),
                                   ("Studio", "chinasi umane"), ("Proteína ùmana", "")]}
    pid = projects["Chinasi"]["id"]
    c.post(f"/api/projects/{pid}/sequences", json={"name": "UBQ_HUMAN", "sequence": HUMAN_UB, "source": "uniprot",
                                                   "meta": {"accession": "P0CG48", "protein": "Polyubiquitin-C",
                                                            "organism": "Homo sapiens"}})
    c.post(f"/api/projects/{pid}/sequences", json={"name": "sp|P69905|HBA_HUMAN", "sequence": YEAST_UB})
    c.post(f"/api/projects/{pid}/documents", json={"name": "Allineamento chinasi", "kind": "pir", "content": "x"})
    job = c.post("/api/align", json={"mode": "pairwise", "project_id": pid, "records": [
        {"name": "h", "sequence": HUMAN_UB}, {"name": "y", "sequence": YEAST_UB}]}).json()
    wait_job(c, job["id"], timeout=60)
    with open(DATA / "1UBQ.pdb", "rb") as fh:
        c.post("/api/templates/upload", files={"file": ("1UBQ.pdb", fh, "chemical/x-pdb")})
    return c, projects, job


def test_relevance_order(data):
    c, projects, _job = data
    r = search(c, "chinasi")
    assert ids(r, "project") == [projects["Chinasi"]["id"], projects["Chinasi di lievito"]["id"],
                                 projects["Studio"]["id"]]
    assert [i["score"] for i in r["groups"][0]["items"]] == [13.0, 7.0, 1.5]
    assert groups(r)[0] == "project"  # il gruppo con la voce più pertinente per primo
    assert ids(r, "document") and r["engine"] in ("fts5", "like")


def test_accents_case_and_partial_words(data):
    c, projects, _job = data
    pid = projects["Proteína ùmana"]["id"]
    for q in ("proteina", "PROTEÍNA umana", "prot um"):
        assert ids(search(c, q), "project") == [pid], q
    assert ids(search(c, "p6990"), "sequence") and ids(search(c, "hba human"), "sequence")
    acc = search(c, "P0CG48")
    assert [i["title"] for g in acc["groups"] for i in g["items"]] == ["UBQ_HUMAN"]
    assert "P0CG48" in acc["groups"][0]["items"][0]["subtitle"]


def test_groups_cover_every_entity(data):
    c, projects, job = data
    r = search(c, "allineamento")  # documento per nome, job per tipo ("Allineamento")
    assert set(groups(r)) == {"document", "job"} and ids(r, "job") == [job["id"]]
    item = next(g for g in r["groups"] if g["type"] == "job")["items"][0]
    assert item["project_id"] == projects["Chinasi"]["id"] and item["project_name"] == "Chinasi"
    assert item["kind"] == "align" and item["status"] == "completed"
    assert ids(search(c, "1ubq"), "template") == ["1UBQ"]
    assert ids(search(c, "ubiquitin"), "template") == ["1UBQ"]  # titolo dai metadati
    assert ids(search(c, "homo sapiens"), "template") == ["1UBQ"]  # organismo dal record SOURCE
    assert ids(search(c, "homo sapiens"), "sequence")  # e dai metadati della sequenza
    only = search(c, "chinasi", type=["project", "tag"])
    assert groups(only) == ["project"]


def test_limit_per_group_and_truncation(data):
    c, projects, _job = data
    pid = projects["Studio"]["id"]
    for i in range(7):
        c.post(f"/api/projects/{pid}/sequences", json={"name": f"variante{i}", "sequence": HUMAN_UB})
    r = search(c, "variante", limit=3)
    group = next(g for g in r["groups"] if g["type"] == "sequence")
    assert len(group["items"]) == 3 and group["truncated"] is True
    assert len(next(g for g in search(c, "variante", limit=10)["groups"])["items"]) == 7
    assert c.get("/api/search", params={"q": "x", "limit": 0}).status_code == 422
    assert c.get("/api/search", params={"q": "x", "limit": 51}).status_code == 422
    assert c.get("/api/search", params={"q": "x" * 201}).status_code == 422


@pytest.mark.parametrize("q", ['"', "*", "'", "%", "_", "\\", "--", ";", "()", "^", "ubq\"*", '"ubq" OR "x"',
                               "NEAR(chinasi studio)", "{name}: chinasi", "chinasi AND", "OR", "NOT chinasi",
                               "'; DROP TABLE projects; --", "\") OR 1=1 --", "%' OR '1'='1", "\x00", "ç*ñ"])
def test_special_characters_and_injection(data, q):
    c, projects, _job = data
    r = search(c, q)
    for g in r["groups"]:
        for item in g["items"]:
            # ogni risultato corrisponde davvero alle parole della richiesta (nessun "tutto" da % o _)
            assert textsearch.query_terms(q), q
            assert item["score"] > 0
    assert len(c.get("/api/projects").json()) == 5  # tabelle intatte (4 + il progetto di esempio)
    assert ids(search(c, "chinasi"), "project")


def test_operators_are_plain_words(data):
    c, projects, _job = data
    c.post("/api/projects", json={"name": "Near and far"})
    r = search(c, "NEAR(chinasi")
    assert ids(r, "project") == []  # "near" e "chinasi" insieme: nessun progetto le ha entrambe
    assert ids(search(c, "near"), "project") == [p["id"] for p in c.get("/api/projects").json()
                                                 if p["name"] == "Near and far"]
    assert search(c, "%")["groups"] == [] and search(c, "_")["groups"] == []


def test_index_follows_changes(data):
    c, projects, job = data
    pid = projects["Studio"]["id"]
    c.patch(f"/api/projects/{pid}", json={"name": "Esperimento"})
    assert pid not in ids(search(c, "studio"), "project")
    assert ids(search(c, "esperimento"), "project") == [pid]
    seq = c.post(f"/api/projects/{pid}/sequences", json={"name": "nuova_seq", "sequence": HUMAN_UB}).json()
    assert ids(search(c, "nuova"), "sequence") == [seq["id"]]
    c.delete(f"/api/projects/{pid}/sequences/{seq['id']}")
    assert ids(search(c, "nuova"), "sequence") == []
    # eliminazione del progetto: sequenze e documenti in cascata, job uno per uno
    chinasi = projects["Chinasi"]["id"]
    assert c.delete(f"/api/projects/{chinasi}").status_code == 200
    for q, group in (("ubq_human", "sequence"), ("allineamento", "document"), ("allineamento", "job")):
        assert ids(search(c, q), group) == [], (q, group)
    assert ids(search(c, "1ubq"), "template") == ["1UBQ"]  # i template non dipendono dai progetti


def test_templates_follow_the_library_and_the_folder(data):
    c, _projects, _job = data
    core = c.app.state.core
    # file copiato a mano nella cartella, senza metadati: trovato senza riavvio (firma della cartella)
    shutil.copyfile(DATA / "1UBQ.pdb", core.paths.templates / "2UBQ.pdb")
    assert ids(search(c, "2ubq"), "template") == ["2UBQ"]
    # stessa pertinenza: prima quello con la data di aggiunta (2UBQ non ha metadati)
    assert ids(search(c, "homo"), "template") == ["1UBQ", "2UBQ"]
    c.delete("/api/templates/1UBQ")
    assert ids(search(c, "1ubq"), "template") == []
    (core.paths.templates / "2UBQ.pdb").unlink()
    assert ids(search(c, "2ubq"), "template") == []


def test_tags_group_and_tag_filter(data):
    c, projects, job = data
    pid = projects["Chinasi"]["id"]
    seqs = c.get(f"/api/projects/{pid}/sequences").json()
    c.post(f"/api/tags/job/{job['id']}", json={"tag": "urgente"})
    c.post(f"/api/tags/sequence/{seqs[0]['id']}", json={"tag": "urgente"})
    c.post("/api/tags/template/1UBQ", json={"tag": "urgente"})
    c.post(f"/api/tags/project/{pid}", json={"tag": "urgenza-bassa"})
    r = search(c, "urg")
    tags = next(g for g in r["groups"] if g["type"] == "tag")["items"]
    assert [(t["title"], t["count"]) for t in tags] == [("urgente", 3), ("urgenza-bassa", 1)]
    assert tags[0]["subtitle"] == "3 elementi: 1 job, 1 sequenza, 1 template"
    # solo gli elementi con l'etichetta: senza testo li elenca tutti, con testo li filtra
    only = search(c, "", tag="Urgente")
    assert only["tag"] == "urgente" and set(groups(only)) == {"job", "sequence", "template"}
    assert ids(only, "sequence") == [seqs[0]["id"]]
    assert ids(search(c, "ubq", tag="urgente"), "sequence") == [seqs[0]["id"]]
    assert ids(search(c, "hba", tag="urgente"), "sequence") == []
    assert ids(search(c, "chinasi", tag="urgenza-bassa"), "project") == [pid]
    assert c.get("/api/search", params={"q": "x", "tag": "a/b"}).status_code == 400


def test_favorites_rank_first_and_list_without_query(data):
    c, projects, _job = data
    a, b = projects["Chinasi di lievito"]["id"], projects["Studio"]["id"]
    before = ids(search(c, "chinasi"), "project")
    assert before.index(a) < before.index(b)
    c.put(f"/api/favorites/project/{b}")
    c.put("/api/favorites/template/1UBQ")
    after = search(c, "chinasi")
    assert ids(after, "project")[:2] == [projects["Chinasi"]["id"], a]  # la stella non scavalca una pertinenza
    item = next(i for i in after["groups"][0]["items"] if i["id"] == b)
    assert item["favorite"] is True and item["score"] == 2.5
    favs = search(c, "")
    assert favs["favorites"] is True and set(groups(favs)) == {"project", "template"}
    assert ids(favs, "project") == [b] and ids(favs, "template") == ["1UBQ"]


def test_both_engines_agree(tmp_path):
    """FTS5 e LIKE sullo stesso database: stessi risultati, stesso ordine."""
    db = Database(tmp_path / "s.db")
    library = TemplateLibrary(tmp_path / "templates")
    labels = LabelService(db, library)
    now = "2026-01-01T00:00:00+00:00"
    names = ["Chinasi", "Chinasi di lievito", "kinase", "Proteína", "p69905 test", "sp_P69905_HBA", "Studio"]
    for i, name in enumerate(names):
        db.execute("INSERT INTO projects VALUES (?, ?, ?, ?, ?)", (f"p{i}", name, "chinasi umane" if i == 6 else "",
                                                                   now, now))
    fts = GlobalSearch(db, library, labels, use_fts=True)
    like = GlobalSearch(db, library, labels, use_fts=False)
    assert (fts.engine, like.engine) == ("fts5", "like")
    for q in ("chinasi", "chin", "proteina", "p6990", "hba", "x", "chinasi lievito", "_", "%"):
        a, b = fts.search(q, limit=50), like.search(q, limit=50)
        assert [(g["type"], [(i["id"], i["score"]) for i in g["items"]]) for g in a["groups"]] == \
            [(g["type"], [(i["id"], i["score"]) for i in g["items"]]) for g in b["groups"]], q
    db.close()


def test_every_registered_job_kind_has_an_italian_label(client):
    """Un tipo di job nuovo non resta fuori dalla ricerca per tipo: ogni tipo registrato ha la sua etichetta, uguale a
    quella dell'interfaccia (KIND_LABEL in frontend/src/lib/format.ts)."""
    registered = set(client.app.state.core.jobs._handlers)
    assert registered - set(globalsearch.JOB_KIND_LABELS) == set()
    frontend = (DATA.parents[2] / "frontend" / "src" / "lib" / "format.ts").read_text(encoding="utf-8")
    for kind, label in globalsearch.JOB_KIND_LABELS.items():
        assert f'{kind}: "{label}"' in frontend, kind


@pytest.mark.parametrize("use_fts", [True, False], ids=["fts5", "like"])
def test_new_job_kinds_are_found_by_their_label(tmp_path, use_fts):
    """Job dei tipi aggiunti dopo F8b con titoli che non contengono l'etichetta: si trovano per tipo con FTS5 (indice
    ricostruito con le etichette nuove, anche per righe già nel database) e con LIKE."""
    db = Database(tmp_path / "x.db")
    now = "2026-01-01T00:00:00+00:00"
    rows = [("jr", "refine", "1UBQ (OpenMM)"), ("jp", "pockets", "fpocket 1UBQ"), ("jm", "mutate", "1UBQ A:K48R"),
            ("ja", "alascan", "1UBQ (3 residui)"), ("jx", "predict", "ESMFold · 1UBQ"), ("je", "electrostatics", "1UBQ"),
            ("jq", "rerun", "pacchetto 1UBQ")]
    db.executemany("INSERT INTO jobs(id, kind, title, status, created_at, updated_at) VALUES (?,?,?,?,?,?)",
                   [(i, k, t, "completed", now, now) for i, k, t in rows])
    library = TemplateLibrary(tmp_path / "templates")
    gs = GlobalSearch(db, library, LabelService(db, library), use_fts=use_fts)
    assert gs.engine == ("fts5" if use_fts else "like")
    expected = {"rifinitura": "jr", "tasche": "jp", "mutagenesi": "jm", "scansione alanina": "ja",
                "predizione": "jx", "elettrostatica": "je", "riesecuzione": "jq"}
    for query, job_id in expected.items():
        groups = {g["type"]: g for g in gs.search(query, types=["job"])["groups"]}
        assert [i["id"] for i in groups["job"]["items"]] == [job_id], query
    assert gs.search("rifinitura", types=["job"])["groups"][0]["items"][0]["subtitle"] == "Rifinitura energetica"
    db.close()


def test_index_build_time(tmp_path):
    """Costruzione dell'indice FTS5 all'avvio su 10 000 righe (job): lineare e breve."""
    db = Database(tmp_path / "big.db")
    now = "2026-01-01T00:00:00+00:00"
    db.executemany("INSERT INTO jobs(id, kind, title, status, created_at, updated_at) VALUES (?,?,?,?,?,?)",
                   [(f"j{i:05d}", "model", f"Modello numero {i}", "completed", now, now) for i in range(10_000)])
    library = TemplateLibrary(tmp_path / "templates")
    start = time.perf_counter()
    gs = GlobalSearch(db, library, LabelService(db, library), use_fts=True)
    elapsed = time.perf_counter() - start
    print(f"indice FTS5 di 10 000 job in {elapsed:.3f} s")
    assert elapsed < 5
    r = gs.search("numero 9999")
    assert [i["id"] for i in r["groups"][0]["items"]][0] == "j09999"
    db.close()


def test_fts5_missing_falls_back_to_like(tmp_path, monkeypatch):
    monkeypatch.setattr(globalsearch, "fts5_available", lambda _db: False)
    db = Database(tmp_path / "x.db")
    library = TemplateLibrary(tmp_path / "templates")
    gs = GlobalSearch(db, library, LabelService(db, library))
    assert gs.engine == "like"
    # nessun oggetto FTS5 nel file: lo schema resta apribile da una build senza FTS5
    assert not db.query("SELECT name FROM sqlite_master WHERE sql LIKE '%fts5%'")
    db.close()


def test_fts_index_lives_only_in_memory(tmp_path):
    db = Database(tmp_path / "y.db")
    library = TemplateLibrary(tmp_path / "templates")
    GlobalSearch(db, library, LabelService(db, library), use_fts=True)
    assert not db.query("SELECT name FROM main.sqlite_master WHERE name LIKE 'search_%'")
    assert db.query("SELECT name FROM temp.sqlite_master WHERE name = 'search_project'")
    db.close()
