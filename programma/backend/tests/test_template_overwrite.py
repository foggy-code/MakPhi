"""Caricamento di un template con un id già in libreria: 409 senza conferma, sostituzione atomica con `overwrite`,
file rovinati che non toccano il template esistente; i download (RCSB, AlphaFold DB) non sostituiscono mai."""

import threading
import time

import pytest
from Bio.PDB import MMCIFIO, PDBParser

from bioloom.core.errors import RemoteServiceError
from bioloom.engines import remote
from bioloom.services.templates import STAGING_PREFIX, TemplateLibrary

from .conftest import DATA, ubq_variant

BROKEN_PDB = b"questo non e' un file PDB\n"
BROKEN_CIF = b"data_rotto\nloop_\n_atom_site.id\n_atom_site.Cartn_x\n1\n"


def _upload(client, name: str, content: bytes, **form):
    return client.post("/api/templates/upload", files={"file": (name, content, "chemical/x-pdb")},
                       data={k: str(v) for k, v in form.items()})


def _library_files(client) -> list[str]:
    return sorted(p.name for p in client.app.state.core.paths.templates.iterdir())


def _ubq_cif(tmp_path) -> bytes:
    io = MMCIFIO()
    io.set_structure(PDBParser(QUIET=True).get_structure("1UBQ", str(DATA / "1UBQ.pdb")))
    dest = tmp_path / "ubq.cif"
    io.save(str(dest))
    return dest.read_bytes()


def test_existing_id_without_overwrite_is_409_and_changes_nothing(client_with_ubq):
    before = client_with_ubq.get("/api/templates/1UBQ").json()
    for form in ({}, {"overwrite": "false"}, {"overwrite": "0"}):
        r = _upload(client_with_ubq, "1UBQ.pdb", (DATA / "ubq_zn.pdb").read_bytes(), **form)
        assert r.status_code == 409, r.text
        error = r.json()["error"]
        assert error["code"] == "template_exists" and "1UBQ" in error["message"]
        assert error["details"] == {"id": "1UBQ", "jobs": 0, "projects": 0}
    # anche con l'id scritto diversamente (template_id normalizzato)
    r = _upload(client_with_ubq, "altro.pdb", (DATA / "ubq_zn.pdb").read_bytes(), template_id="1ubq")
    assert r.status_code == 409 and r.json()["error"]["details"]["id"] == "1UBQ"
    assert client_with_ubq.get("/api/templates/1UBQ").json() == before
    assert _library_files(client_with_ubq) == ["1UBQ.analysis.json", "1UBQ.meta.json", "1UBQ.pdb"]


def test_409_says_how_many_jobs_and_projects_use_the_template(client_with_ubq):
    core = client_with_ubq.app.state.core
    core.jobs.register("demo_uso", lambda ctx, p: {"ok": True}, pool="io")
    project = client_with_ubq.post("/api/projects", json={"name": "Prova"}).json()["id"]
    core.jobs.submit("demo_uso", {"templates": [{"id": "1UBQ", "chains": ["A"]}]}, project_id=project)
    core.jobs.submit("demo_uso", {"source": {"type": "template", "id": "1ubq"}}, project_id=project)
    core.jobs.submit("demo_uso", {"templates": [{"id": "1UBQX"}]})  # un altro template: non conta
    r = _upload(client_with_ubq, "1UBQ.pdb", (DATA / "1UBQ.pdb").read_bytes())
    error = r.json()["error"]
    assert r.status_code == 409 and error["details"] == {"id": "1UBQ", "jobs": 2, "projects": 1}
    assert "usato da 2 job in 1 progetto" in error["message"]


@pytest.mark.parametrize("name, content", [("1UBQ.pdb", BROKEN_PDB), ("1UBQ.cif", BROKEN_CIF)])
def test_broken_file_never_touches_the_existing_template(client_with_ubq, name, content):
    library = client_with_ubq.app.state.core.library
    original = library.path("1UBQ").read_bytes()
    meta = library.describe("1UBQ")
    r = _upload(client_with_ubq, name, content, overwrite="true")
    assert r.status_code == 400 and r.json()["error"]["code"] == "invalid_structure", r.text
    assert "1UBQ." in r.json()["error"]["message"]  # l'errore cita il nome del template, non la cartella temporanea
    assert library.path("1UBQ").read_bytes() == original and library.describe("1UBQ") == meta
    assert _library_files(client_with_ubq) == ["1UBQ.analysis.json", "1UBQ.meta.json", "1UBQ.pdb"]
    # senza conferma: 409 subito, anche con un file rovinato
    assert _upload(client_with_ubq, name, content).status_code == 409


def test_overwrite_replaces_atomically_and_rebuilds_metadata(client_with_ubq, tmp_path):
    variant = ubq_variant(tmp_path / "1UBQ.pdb", drop=range(70, 77))  # 69 residui
    r = _upload(client_with_ubq, "1UBQ.pdb", variant.read_bytes(), overwrite="true")
    assert r.status_code == 200, r.text
    assert r.json()["replaced"] is True and r.json()["source"] == "caricamento utente"
    detail = client_with_ubq.get("/api/templates/1UBQ").json()
    assert detail["chains"][0]["residues"] == 69  # analisi rifatta dal nuovo file, non quella in cache
    assert [t["id"] for t in client_with_ubq.get("/api/templates").json()] == ["1UBQ"]
    assert not [n for n in _library_files(client_with_ubq) if n.startswith(STAGING_PREFIX)]


def test_overwrite_changing_extension_keeps_a_single_file(client_with_ubq, tmp_path):
    r = _upload(client_with_ubq, "ubiquitina.cif", _ubq_cif(tmp_path), template_id="1UBQ", overwrite="on")
    assert r.status_code == 200, r.text
    assert r.json()["format"] == "cif" and r.json()["replaced"] is True
    assert _library_files(client_with_ubq) == ["1UBQ.analysis.json", "1UBQ.cif", "1UBQ.meta.json"]
    listed = client_with_ubq.get("/api/templates").json()
    assert [(t["id"], t["format"]) for t in listed] == [("1UBQ", "cif")]
    assert client_with_ubq.get("/api/templates/1UBQ").json()["format"] == "cif"
    # e di nuovo in PDB, con "1"
    r = _upload(client_with_ubq, "1UBQ.pdb", (DATA / "1UBQ.pdb").read_bytes(), overwrite="1")
    assert r.status_code == 200 and r.json()["format"] == "pdb"
    assert _library_files(client_with_ubq) == ["1UBQ.analysis.json", "1UBQ.meta.json", "1UBQ.pdb"]


def test_new_id_with_overwrite_is_a_normal_upload(client):
    r = _upload(client, "1UBQ.pdb", (DATA / "1UBQ.pdb").read_bytes(), overwrite="true")
    assert r.status_code == 200 and r.json()["replaced"] is False
    assert _upload(client, "1UBQ.pdb", b"", overwrite="true").json()["error"]["message"] == "Il file caricato è vuoto."
    assert _upload(client, "1UBQ.pdb", b"x", overwrite="forse").status_code == 422


def test_leftover_staging_folders_are_removed_at_startup(tmp_path):
    (tmp_path / f"{STAGING_PREFIX}abc123").mkdir()
    (tmp_path / f"{STAGING_PREFIX}abc123" / "1UBQ.pdb").write_bytes((DATA / "1UBQ.pdb").read_bytes())
    library = TemplateLibrary(tmp_path)
    assert list(tmp_path.iterdir()) == [] and library.ids() == []


def test_rcsb_download_never_replaces_a_template(tmp_path, monkeypatch):
    library = TemplateLibrary(tmp_path)
    ubq = (DATA / "1UBQ.pdb").read_text()

    def download_while_user_uploads(pid, fmt):
        # mentre si scarica, l'utente carica un file con lo stesso id
        library.save_upload("9XYZ.pdb", (DATA / "ubq_zn.pdb").read_bytes())
        return ubq

    monkeypatch.setattr(remote, "rcsb_download", download_while_user_uploads)
    out = library.fetch("9XYZ")
    assert out["cached"] is True and out["source"] == "caricamento utente"
    assert library.path("9XYZ").read_bytes() == (DATA / "ubq_zn.pdb").read_bytes()

    monkeypatch.setattr(remote, "rcsb_download", lambda pid, fmt: "non una struttura\n")
    with pytest.raises(RemoteServiceError) as err:
        library.fetch("8ABC")
    assert "8ABC" in err.value.message and not library.exists("8ABC")
    assert sorted(p.name for p in tmp_path.iterdir() if p.name.startswith("8ABC")) == []


def _pause_after_install(library, monkeypatch, fmt: str):
    """La prima `_install` con formato `fmt` si ferma tra il file entrato in libreria e i suoi metadati: restituisce
    (installato, via libera, formati delle chiamate)."""
    real_install = library._install
    installed, gate, calls = threading.Event(), threading.Event(), []

    def install(tid, f, data, **kw):
        calls.append(f)
        out = real_install(tid, f, data, **kw)
        if f == fmt and not installed.is_set():
            installed.set()
            gate.wait(5)
        return out

    monkeypatch.setattr(library, "_install", install)
    return installed, gate, calls


def _run(results: dict, key: str, fn, *args, **kw) -> threading.Thread:
    def target():
        try:
            results[key] = fn(*args, **kw)
        except Exception as exc:  # il test controlla l'esito di ogni richiesta
            results[key] = exc

    thread = threading.Thread(target=target)
    thread.start()
    return thread


def test_concurrent_overwrites_with_different_formats_register_in_order(tmp_path, monkeypatch):
    """Due sostituzioni dello stesso id, .pdb e .cif, sovrapposte: la seconda aspetta che la prima abbia registrato i
    metadati del suo file (lock per id), nessuna fallisce e i metadati finali descrivono il file rimasto."""
    library = TemplateLibrary(tmp_path / "templates")
    library.save_upload("1UBQ.pdb", (DATA / "1UBQ.pdb").read_bytes())
    cif = _ubq_cif(tmp_path)
    installed, gate, calls = _pause_after_install(library, monkeypatch, "pdb")
    results: dict = {}
    first = _run(results, "pdb", library.save_upload, "1UBQ.pdb", (DATA / "ubq_zn.pdb").read_bytes(), overwrite=True)
    try:
        assert installed.wait(5)
        second = _run(results, "cif", library.save_upload, "1UBQ.cif", cif, overwrite=True)
        time.sleep(0.3)
        assert calls == ["pdb"] and second.is_alive()  # il .cif non entra finché il .pdb non è registrato
    finally:
        gate.set()
    first.join(10)
    second.join(10)
    assert isinstance(results["pdb"], dict), results["pdb"]
    assert isinstance(results["cif"], dict), results["cif"]
    assert results["pdb"]["format"] == "pdb" and results["cif"]["format"] == "cif"
    assert sorted(p.name for p in library.root.iterdir()) == ["1UBQ.analysis.json", "1UBQ.cif", "1UBQ.meta.json"]
    final = library.describe("1UBQ")
    assert final["format"] == "cif" and final["source"] == "caricamento utente" and final["chains"] == ["A"]


def test_upload_overwrite_waits_for_the_download_being_registered(tmp_path, monkeypatch):
    """Un download di RCSB (job di pipeline) tra il file e i metadati e un caricamento con sostituzione in .cif: il
    job non fallisce e i metadati rimasti sono quelli del caricamento, con il file caricato."""
    library = TemplateLibrary(tmp_path / "templates")
    monkeypatch.setattr(remote, "rcsb_download", lambda pid, fmt: (DATA / "1UBQ.pdb").read_text())
    cif = _ubq_cif(tmp_path)
    installed, gate, calls = _pause_after_install(library, monkeypatch, "pdb")
    results: dict = {}
    download = _run(results, "rcsb", library.fetch, "1UBQ")
    try:
        assert installed.wait(5)
        upload = _run(results, "upload", library.save_upload, "1UBQ.cif", cif, overwrite=True)
        time.sleep(0.3)
        assert calls == ["pdb"] and upload.is_alive()
    finally:
        gate.set()
    download.join(10)
    upload.join(10)
    assert isinstance(results["rcsb"], dict) and results["rcsb"]["cached"] is False, results["rcsb"]
    assert isinstance(results["upload"], dict) and results["upload"]["replaced"] is True, results["upload"]
    final = library.describe("1UBQ")
    assert final["format"] == "cif" and final["source"] == "caricamento utente" and final["chains"] == ["A"]
