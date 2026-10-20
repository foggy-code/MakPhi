"""Riproducibilità (F9.4): pacchetto di un job, sicurezza dell'archivio caricato, riesecuzione e confronto."""

from __future__ import annotations

import io
import json
import os
import stat
import subprocess
import sys
import threading
import time
import warnings
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from bioloom import __version__
from bioloom.app import create_app
from bioloom.config import Paths
from bioloom.core import auth
from bioloom.core.errors import AppError
from bioloom.domain import reproducibility as repro
from bioloom.engines import tools, versions
from bioloom.services import reproducibility as service
from tests.conftest import DATA, YEAST_UB, requires_modeller, ubq_variant, wait_job

ROOT = Path(__file__).resolve().parents[2]
ACCESS_KEY = "chiave-accesso-di-prova-7Q2"
EMAIL = "ricercatrice.prova@example.org"
FAKE_MODELLER_KEY = "MODELFINTA9X4"
requires_openmm = pytest.mark.skipif(not (tools.find_module("openmm") and tools.find_module("pdbfixer")),
                                     reason="OpenMM o PDBFixer non installati (immagine bioloom:dev)")


# ------------------------------------------------------------------------------------------ aiuti
def _quality(c, source=None, **flags) -> dict:
    body = {"source": source or {"type": "template", "id": "1UBQ"}, "dope": False, "procheck": False, "prosa": False,
            **flags}
    job = c.post("/api/quality", json=body).json()
    done = wait_job(c, job["id"])
    assert done["status"] == "completed", done.get("error")
    return done


def _package(c, job_id: str) -> bytes:
    r = c.get(f"/api/jobs/{job_id}/package")
    assert r.status_code == 200, r.text
    assert r.headers["content-type"] == "application/zip"
    assert "bioloom_riproducibile_" in r.headers["content-disposition"]
    return r.content


def _members(data: bytes) -> dict[str, bytes]:
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        return {i.filename: zf.read(i) for i in zf.infolist()}


def _manifest(data: bytes) -> dict:
    return json.loads(_members(data)["manifest.json"])


def _rebuild(data: bytes, *, change=None, manifest=None, reseal: bool = True, extra=()) -> bytes:
    """Pacchetto modificato: `change(files)` sui contenuti, `manifest(m)` sul manifest (risigillato con `reseal`),
    `extra` = ZipInfo/bytes aggiunti così come sono."""
    files = _members(data)
    m = json.loads(files.pop("manifest.json"))
    if change:
        change(files)
    if manifest:
        manifest(m)
        if reseal:
            m = repro.seal({k: v for k, v in m.items() if k != "manifest_sha256"})
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf, warnings.catch_warnings():
        warnings.simplefilter("ignore")  # nomi duplicati voluti
        for name, content in files.items():
            zf.writestr(name, content)
        zf.writestr("manifest.json", json.dumps(m))
        for info, content in extra:
            zf.writestr(info, content)
    return out.getvalue()


def _inspect(c, data: bytes):
    return c.post("/api/jobs/rerun/inspect", files={"file": ("pacchetto.zip", data, "application/zip")})


def _rerun(c, data: bytes) -> dict:
    r = c.post("/api/jobs/rerun", files={"file": ("pacchetto.zip", data, "application/zip")})
    assert r.status_code == 200, r.text
    done = wait_job(c, r.json()["id"], timeout=900)
    assert done["status"] == "completed", done.get("error")
    return done


def _sha(data: bytes) -> str:
    return repro.sha256(data)


# ------------------------------------------------------------------------------------------ regole dell'archivio
@pytest.mark.parametrize("name", [
    "../fuori.txt", "/etc/passwd", "C:/Windows/x.txt", "inputs\\x.pdb", "inputs/../manifest.json", "inputs/./x",
    "inputs//x", "", "altro/x.txt", "x.txt", "inputs/.nascosto", "inputs/con.txt", "inputs/a\x00b",
    "inputs/" + "/".join(["d"] * 13) + "/x"])
def test_unsafe_member_names_are_rejected(name):
    assert repro.member_problem(name) is not None


@pytest.mark.parametrize("name", ["manifest.json", "LEGGIMI.txt", "inputs/library/1UBQ.pdb", "inputs/",
                                  "outputs/qa_x.B99990001/procheck/model.sum", "scripts/model.py", "log/job.log",
                                  "inputs/library/AF-P0CG48-F1.meta.json"])
def test_safe_member_names_are_accepted(name):
    assert repro.member_problem(name) is None


def _zip(entries) -> zipfile.ZipFile:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf, warnings.catch_warnings():
        warnings.simplefilter("ignore")
        for info, content in entries:
            zf.writestr(info, content)
    return zipfile.ZipFile(io.BytesIO(buf.getvalue()))


def _code(fn) -> str:
    with pytest.raises(AppError) as exc:
        fn()
    return exc.value.code


def test_check_archive_rejects_symlinks_special_files_duplicates_and_encryption():
    link = zipfile.ZipInfo("inputs/collegamento")
    link.external_attr = (stat.S_IFLNK | 0o777) << 16
    assert _code(lambda: repro.check_archive(_zip([("manifest.json", "{}"), (link, "/etc/passwd")]).infolist())) \
        == "package_symlink"
    fifo = zipfile.ZipInfo("inputs/fifo")
    fifo.external_attr = (stat.S_IFIFO | 0o644) << 16
    assert _code(lambda: repro.check_archive(_zip([("manifest.json", "{}"), (fifo, "")]).infolist())) \
        == "package_special_file"
    dup = _zip([("manifest.json", "{}"), ("outputs/a.pdb", "1"), ("outputs/a.pdb", "2")])
    assert _code(lambda: repro.check_archive(dup.infolist())) == "package_duplicate"
    case = _zip([("manifest.json", "{}"), ("outputs/A.pdb", "1"), ("outputs/a.pdb", "2")])
    assert _code(lambda: repro.check_archive(case.infolist())) == "package_duplicate"
    zf = _zip([("manifest.json", "{}"), ("outputs/a.pdb", "1")])
    infos = zf.infolist()
    infos[1].flag_bits |= 0x1
    assert _code(lambda: repro.check_archive(infos)) == "package_encrypted"
    assert _code(lambda: repro.check_archive(_zip([("outputs/a.pdb", "1")]).infolist())) == "package_invalid"
    assert _code(lambda: repro.check_archive(_zip([("manifest.json", "{}"), ("../a", "1")]).infolist())) \
        == "package_unsafe_path"


def test_check_archive_rejects_decompression_bombs_and_size_limits(monkeypatch):
    bomb = _zip([("manifest.json", "{}"), ("outputs/zeri.txt", b"0" * (4 * 1024 ** 2))])
    info = bomb.getinfo("outputs/zeri.txt")
    assert info.file_size / info.compress_size > repro.MAX_RATIO  # deflate di zeri: circa 1000:1
    assert _code(lambda: repro.check_archive(bomb.infolist())) == "package_bomb"
    # un file piccolo molto comprimibile non è una bomba
    repro.check_archive(_zip([("manifest.json", "{}"), ("outputs/gap.ali", b"-" * 500_000)]).infolist())
    monkeypatch.setattr(repro, "MAX_FILES", 3)
    many = _zip([("manifest.json", "{}")] + [(f"outputs/{i}.txt", str(i)) for i in range(5)])
    assert _code(lambda: repro.check_archive(many.infolist())) == "package_too_many_files"
    monkeypatch.setattr(repro, "MAX_FILES", 5000)
    monkeypatch.setattr(repro, "MAX_TOTAL_BYTES", 1000)
    big = _zip([("manifest.json", "{}"), ("outputs/a.txt", os.urandom(800)), ("outputs/b.txt", os.urandom(800))])
    assert _code(lambda: repro.check_archive(big.infolist())) == "package_too_large"


def test_manifest_seal_detects_any_change():
    base = {"format": repro.FORMAT, "format_version": 1, "created_at": "x", "bioloom": {"version": "4.1.0"},
            "environment": {}, "job": {"id": "abc", "kind": "quality"}, "params": {"dope": True},
            "files": [{"path": "inputs/params.json", "role": "params", "sha256": "0" * 64, "size": 2}]}
    sealed = repro.seal(base)
    assert repro.validate_manifest(sealed) is sealed
    altered = {**sealed, "params": {"dope": False}}
    assert _code(lambda: repro.validate_manifest(altered)) == "package_manifest_altered"
    assert _code(lambda: repro.validate_manifest(repro.seal({**base, "format_version": 2}))) == "package_format"
    assert _code(lambda: repro.validate_manifest(repro.seal({**base, "format": "zip qualunque"}))) == "package_invalid"
    bad_path = repro.seal({**base, "files": [{**base["files"][0], "path": "../x"}]})
    assert _code(lambda: repro.validate_manifest(bad_path)) == "package_manifest_invalid"
    bad_job_path = repro.seal({**base, "files": [{**base["files"][0], "job_path": "/etc/passwd"}]})
    assert _code(lambda: repro.validate_manifest(bad_job_path)) == "package_manifest_invalid"
    assert _code(lambda: repro.validate_manifest([])) == "package_manifest_invalid"


def test_redactor_replaces_secrets_and_local_paths_only_at_path_boundaries():
    r = repro.Redactor([FAKE_MODELLER_KEY, EMAIL, "abc"], [("/data", "<cartella dati>"), ("/home/utente", "~")])
    text = (f"chiave {FAKE_MODELLER_KEY.lower()} email {EMAIL.upper()} file /data/jobs/x.pdb e \"/data\" "
            "url https://data.rcsb.org/rest e /database/x, /home/utente/.bashrc, metadata/x abc")
    out = r.text(text)
    assert FAKE_MODELLER_KEY.lower() not in out.lower() and EMAIL.lower() not in out.lower()
    assert out.count(repro.REDACTED) == 2
    assert "<cartella dati>/jobs/x.pdb" in out and '"<cartella dati>"' in out
    assert "https://data.rcsb.org/rest" in out and "/database/x" in out and "~/.bashrc" in out
    assert out.endswith("abc")  # segreti troppo corti: mai cercati
    assert r.leaks(text.encode()) == 2 and r.leaks(out.encode()) == 0
    assert r.value({"a": [f"x {EMAIL}"], EMAIL: 1}) == {"a": [f"x {repro.REDACTED}"], repro.REDACTED: 1}
    assert r.data(b"\xff\xfe binario") == (b"\xff\xfe binario", False)


# ------------------------------------------------------------------------------------------ confronto
def _shift(text: str, dx: float, only: int | None = None) -> str:
    """PDB con gli atomi spostati di dx lungo x (tutti, o solo il numero `only`), colonne fisse del formato."""
    out = []
    n = 0
    for line in text.splitlines():
        if line.startswith(("ATOM  ", "HETATM")):
            n += 1
            if only is None or n == only:
                line = f"{line[:30]}{float(line[30:38]) + dx:8.3f}{line[38:]}"
        out.append(line)
    return "\n".join(out) + "\n"


def test_coordinates_of_1ubq_compared_without_superposition():
    text = (DATA / "1UBQ.pdb").read_text()
    atoms = repro.pdb_atoms(text)
    assert len(atoms) == 660  # 1UBQ: 602 atomi della proteina + 58 ossigeni delle acque
    same = repro.compare_coordinates(text, text)
    assert same["rmsd"] == 0.0 and same["max_deviation"] == 0.0 and same["within"]
    # tutti gli atomi spostati di 0,001 Å (un'unità dell'ultima cifra): entro la tolleranza
    tiny = repro.compare_coordinates(text, _shift(text, 0.001))
    assert tiny["within"] and tiny["max_deviation"] == pytest.approx(0.001, abs=1e-6)
    # traslazione di 0,5 Å: RMSD senza sovrapposizione 0,5 (una sovrapposizione la nasconderebbe)
    moved = repro.compare_coordinates(text, _shift(text, 0.5))
    assert moved["rmsd"] == pytest.approx(0.5, abs=1e-4) and not moved["within"]
    # un solo atomo spostato di 0,66 Å: RMSD = 0,66/√660 ≈ 0,0257, scarto massimo 0,66 con l'atomo
    one = repro.compare_coordinates(text, _shift(text, 0.66, only=1))
    assert one["rmsd"] == pytest.approx(0.66 / 660 ** 0.5, abs=1e-4) and one["max_deviation"] == pytest.approx(0.66)
    assert one["max_atom"] == "N MET A1"
    fewer = repro.compare_coordinates(text, "\n".join(ln for ln in text.splitlines() if " HOH " not in ln))
    assert fewer["common"] == 602 and fewer["only_original"] == 58 and not fewer["within"]


def test_compare_file_statuses():
    pdb = (DATA / "1UBQ.pdb").read_text()
    stamped = "EXPDTA    THEORETICAL MODEL, MODELLER 10.8 2026/10/01 08:19:38\n" + pdb
    restamped = stamped.replace("08:19:38", "08:19:51")
    assert repro.compare_file("m.pdb", stamped.encode(), stamped.encode())["status"] == "identical"
    res = repro.compare_file("m.pdb", stamped.encode(), restamped.encode())
    assert res["status"] == "identical" and "MODELLER" in res["detail"]
    assert repro.compare_file("m.pdb", pdb.encode(), _shift(pdb, 0.001).encode())["status"] == "equivalent"
    moved = repro.compare_file("m.pdb", pdb.encode(), _shift(pdb, 0.2).encode())
    assert moved["status"] == "different" and moved["coordinates"]["rmsd"] == pytest.approx(0.2, abs=1e-4)
    a = {"dope": -8123.456789, "grade": "A", "cached": True, "timings": {"x": 1}, "list": [1, 2]}
    near = {**a, "dope": -8123.4568, "cached": False, "timings": {"x": 9}}
    assert repro.compare_file("r.json", json.dumps(a).encode(), json.dumps(near).encode())["status"] == "equivalent"
    # il numero di worker di MODELLER dipende dai core liberi, non dal calcolo
    workers = {**a, "options": {"parallel": True}, "modeller_workers": 3}
    assert repro.compare_file("r.json", json.dumps(workers).encode(),
                              json.dumps({**workers, "modeller_workers": 2}).encode())["status"] == "identical"
    far = repro.compare_file("r.json", json.dumps(a).encode(), json.dumps({**a, "dope": -8100.0}).encode())
    assert far["status"] == "different" and far["diff"]["beyond_tolerance"] == 1
    assert far["diff"]["max_delta"] == pytest.approx(23.456789) and far["diff"]["max_delta_at"] == "dope"
    text = repro.compare_file("r.json", json.dumps(a).encode(), json.dumps({**a, "grade": "B", "x": 1}).encode())
    assert text["status"] == "different" and text["diff"]["text"] == 1 and text["diff"]["structure"] == 1
    profile = "1 MET -0.0123\n2 GLN -0.0456\n"
    assert repro.compare_file("dope.profile", profile.encode(),
                              profile.replace("-0.0456", "-0.04560001").encode())["status"] == "equivalent"
    assert repro.compare_file("x.ali", b"A", None)["status"] == "missing"


def test_score_tolerance_and_verdict():
    assert repro.within(-8000.0, -8000.007) and not repro.within(-8000.0, -8000.01)
    assert repro.within(0.0, 0.0009) and not repro.within(0.0, 0.002)
    assert repro.verdict([{"status": "identical"}]) == "identico"
    assert repro.verdict([{"status": "identical"}, {"status": "equivalent"}]) == "equivalente"
    assert repro.verdict([{"status": "identical"}, {"status": "extra", "principal": False}]) == "equivalente"
    assert repro.verdict([{"status": "different", "principal": False}]) == "equivalente"
    assert repro.verdict([{"status": "missing", "principal": True}]) == "diverso"
    rows = repro.metric_rows({"dope": -8000.0, "grade": "A"}, {"dope": -7990.0, "grade": "A"},
                             [("dope", "DOPE"), ("grade", "Voto"), ("assente", "x")])
    assert [r["key"] for r in rows] == ["dope", "grade"] and rows[0]["delta"] == 10.0 and not rows[0]["within"]
    assert rows[1]["within"]


def test_environment_warnings_name_every_difference():
    packaged = {"bioloom": {"version": "4.0.0", "image": "4.0.0"},
                "environment": {"python": "3.11.9", "machine": "aarch64", "libraries": {"numpy": "1.26.0"},
                                "tools": {"modeller": {"label": "MODELLER", "available": True, "version": "10.4"},
                                          "dssp": {"label": "DSSP", "available": True, "version": "4.2.2"},
                                          "apbs": {"label": "APBS", "available": True, "version": "3.4.1"},
                                          "prosa": {"label": "ProSA", "available": False, "version": None}}}}
    current = {"bioloom": {"version": "4.1.0", "image": "4.1.0"},
               "environment": {"python": "3.12.3", "machine": "x86_64", "libraries": {"numpy": "2.5.3"},
                               "tools": {"modeller": {"available": True, "version": "10.8"},
                                         "dssp": {"available": True, "version": "4.2.2"}}}}
    codes = [w["code"] for w in repro.environment_warnings(packaged, current)]
    assert codes == ["rerun_version_bioloom", "rerun_version_image", "rerun_tool_missing", "rerun_version_tool",
                     "rerun_version_library", "rerun_version_library", "rerun_platform"]
    assert repro.environment_warnings(current, current) == []


def test_tool_versions_are_read_from_the_programs():
    found = tools.find("dssp")
    info = versions.tool("dssp")
    assert info["available"] == bool(found)
    if found:  # mkdssp nell'immagine: «mkdssp version 4.x»
        assert info["version"] and info["version"][0].isdigit()
    env = versions.environment(["dssp"])
    assert env["python"] == sys.version.split()[0] and "numpy" in env["libraries"]
    assert not any(isinstance(v, str) and ("/" in v or "\\" in v) for v in env["tools"]["dssp"].values())


# ------------------------------------------------------------------------------------------ esportazione
def test_quality_package_content_manifest_and_checksums(client_with_ubq):
    c = client_with_ubq
    job = _quality(c)
    data = _package(c, job["id"])
    files = _members(data)
    m = json.loads(files["manifest.json"])
    assert m["format"] == repro.FORMAT and m["format_version"] == repro.FORMAT_VERSION
    assert repro.validate_manifest(m) is m
    assert m["bioloom"]["version"] == __version__ and m["job"]["id"] == job["id"] and m["job"]["kind"] == "quality"
    assert m["params"] == job["params"] and m["rerun"] == {"supported": True, "reason": None}
    assert m["seeds"]["note"].startswith("calcolo deterministico")
    assert set(m["settings"]["values"]) == set(service.RELEVANT_SETTINGS)
    assert "contact_email" not in json.dumps(m["settings"]) and "modeller_python" not in json.dumps(m)
    assert set(m["environment"]["tools"]) == {"dssp"} and m["environment"]["python"]
    declared = {f["path"]: f for f in m["files"]}
    assert set(declared) == set(files) - {"manifest.json"}
    for path, entry in declared.items():  # checksum e dimensione di ogni file
        assert entry["sha256"] == _sha(files[path]) and entry["size"] == len(files[path]), path
    roles = {f["role"] for f in m["files"]}
    assert {"params", "template", "template_meta", "output", "log", "documentation"} <= roles
    assert files["inputs/library/1UBQ.pdb"] == (DATA / "1UBQ.pdb").read_bytes()
    assert json.loads(files["inputs/library/1UBQ.meta.json"])["id"] == "1UBQ"
    assert json.loads(files["inputs/params.json"]) == job["params"]
    assert json.loads(files["outputs/result.json"])["grade"] == job["result"]["grade"]
    assert declared["outputs/result.json"]["job_path"] == "result.json"
    assert not any(p.startswith("outputs/") and p.endswith(("params.json", "job.log")) for p in files)
    readme = files["LEGGIMI.txt"].decode()
    assert "python programma/bioloom.py rerun" in readme and "POST /api/jobs/rerun" in readme
    # riaperto dal servizio: valido, con l'anteprima senza errori
    summary = _inspect(c, data).json()
    assert summary["job"]["id"] == job["id"] and summary["rerun"]["supported"]
    assert not [x for x in summary["checks"] if x["level"] == "error"]
    assert summary["files"]["count"] == len(declared)


def test_package_never_contains_secrets_or_local_paths(tmp_path, monkeypatch):
    real_key = os.environ.get("MODELLER_KEY", "")
    monkeypatch.setenv("MODELLER_KEY", real_key or FAKE_MODELLER_KEY)
    app = create_app(Paths(tmp_path / "data"), frontend_dist=None,
                     auth_config=auth.AuthConfig(enabled=True, key=ACCESS_KEY))
    with TestClient(app, headers={"Authorization": f"Bearer {ACCESS_KEY}"}) as c:
        (tmp_path / "data" / "licenses" / "modeller.key").write_text(FAKE_MODELLER_KEY + "\n")
        assert c.put("/api/settings", json={"contact_email": EMAIL}).status_code == 200
        with open(DATA / "1UBQ.pdb", "rb") as fh:
            assert c.post("/api/templates/upload", files={"file": ("1UBQ.pdb", fh, "chemical/x-pdb")}).status_code == 200
        job = _quality(c)
        # come un messaggio d'errore di MODELLER che ripete la chiave, o un percorso nel log
        log = tmp_path / "data" / "jobs" / job["id"] / "job.log"
        with open(log, "a", encoding="utf-8") as fh:
            fh.write(f"chiave {FAKE_MODELLER_KEY} {real_key} contatto {EMAIL} accesso {ACCESS_KEY}\n"
                     f"file {tmp_path / 'data' / 'jobs' / 'x.pdb'}\n")  # separatori del sistema, come nei log veri
        data = _package(c, job["id"])
    secrets = [s for s in (FAKE_MODELLER_KEY, real_key, EMAIL, ACCESS_KEY) if s]
    files = _members(data)
    # controllo programmatico: nessun segreto in nessun file, senza stamparli (si elencano solo i file)
    leaks = sorted({name for name, content in files.items() for s in secrets if s.lower().encode() in content.lower()})
    assert leaks == []
    # il percorso anche come lo scrive JSON (su Windows le barre rovesciate raddoppiano: "C:\\Users\\...") e con "/"
    local = {str(tmp_path).encode(), json.dumps(str(tmp_path))[1:-1].encode(), tmp_path.as_posix().encode()}
    assert not [name for name, content in files.items() if any(path in content for path in local)]
    text = files["log/job.log"].decode()
    assert repro.REDACTED in text and f"<cartella dati>{os.sep}jobs{os.sep}x.pdb" in text
    manifest = json.loads(files["manifest.json"])
    assert next(f for f in manifest["files"] if f["path"] == "log/job.log").get("redacted") is True


def test_big_compressible_output_is_stored_so_the_package_stays_importable(client_with_ubq, tmp_path):
    c = client_with_ubq
    job = _quality(c)
    folder = Path(c.app.state.core.jobs.workdir(job["id"]))
    (folder / "spazi.txt").write_text(" " * (3 * 1024 ** 2))
    data = _package(c, job["id"])
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        assert zf.getinfo("outputs/spazi.txt").compress_type == zipfile.ZIP_STORED
    assert _inspect(c, data).status_code == 200


def test_export_errors_unsupported_and_active_jobs(client_with_ubq):
    c = client_with_ubq
    core = c.app.state.core
    release = threading.Event()
    started = threading.Event()

    def slow_quality(ctx, p):  # in questa istanza il job quality aspetta: è ancora in esecuzione durante l'export
        started.set()
        release.wait(30)
        return {"grade": None}

    core.jobs.register("quality", slow_quality)
    core.jobs.register("altro", lambda ctx, p: {"ok": True})
    try:
        running = core.jobs.submit("quality", {"source": {"type": "template", "id": "1UBQ"}})
        assert started.wait(30)
        r = c.get(f"/api/jobs/{running['id']}/package")
        assert r.status_code == 409 and "completato" in r.json()["error"]["message"]
        other = core.jobs.submit("altro", {})
        wait_job(c, other["id"])
        r = c.get(f"/api/jobs/{other['id']}/package")
        assert r.status_code == 400 and r.json()["error"]["code"] == "package_unsupported"
        assert "non è disponibile" in r.json()["error"]["message"]
        assert c.get("/api/jobs/0000000000000000/package").status_code == 404
    finally:
        release.set()
    wait_job(c, running["id"])
    assert not list(core.paths.scratch_tmp.glob("download_*"))  # i file temporanei degli export falliti spariscono


def test_search_package_declares_remote_inputs_and_is_not_rerunnable(client, monkeypatch):
    from bioloom.engines import remote

    monkeypatch.setattr(remote, "rcsb_sequence_search", lambda seq, **kw: [
        {"pdb_id": "1UBQ", "chain": "A", "template_id": "1UBQ_A", "identity": 96.0, "coverage": 100.0,
         "evalue": 1e-40, "bitscore": 150.0, "aligned_length": 76, "title": "UBIQUITIN", "method": "X-RAY",
         "resolution": 1.8}])
    job = client.post("/api/search", json={"query": {"name": "ub", "sequence": YEAST_UB}, "engine": "rcsb"}).json()
    done = wait_job(client, job["id"])
    assert done["status"] == "completed", done.get("error")
    data = _package(client, job["id"])
    m = _manifest(data)
    assert m["remote"][0]["service"].startswith("RCSB PDB") and m["remote"][0]["date"] == done["finished_at"]
    assert m["rerun"]["supported"] is False and "non è riproducibile localmente" in m["rerun"]["reason"]
    assert _members(data)["inputs/sequences.fasta"].decode().startswith(">ub")
    summary = _inspect(client, data).json()
    assert summary["checks"][0]["code"] == "rerun_not_reproducible" and not summary["rerun"]["supported"]
    r = client.post("/api/jobs/rerun", files={"file": ("p.zip", data, "application/zip")})
    assert r.status_code == 400 and r.json()["error"]["code"] == "rerun_not_reproducible"
    assert "RCSB" in r.json()["error"]["message"]


# ------------------------------------------------------------------------------------------ pacchetti non validi
def test_altered_manifest_or_wrong_checksum_are_rejected(client_with_ubq):
    c = client_with_ubq
    data = _package(c, _quality(c)["id"])

    def tweak_params(m):
        m["params"]["procheck"] = True

    altered = _rebuild(data, manifest=tweak_params, reseal=False)
    for r in (_inspect(c, altered), c.post("/api/jobs/rerun", files={"file": ("p.zip", altered, "application/zip")})):
        assert r.status_code == 400 and r.json()["error"]["code"] == "package_manifest_altered"
        assert "modificato dopo l'esportazione" in r.json()["error"]["message"]

    def tweak_input(files):
        files["inputs/library/1UBQ.pdb"] = files["inputs/library/1UBQ.pdb"].replace(b"  1.00 ", b"  0.50 ", 1)

    wrong = _rebuild(data, change=tweak_input)
    r = _inspect(c, wrong)
    assert r.status_code == 400 and r.json()["error"]["code"] == "package_checksum"
    assert "inputs/library/1UBQ.pdb" in r.json()["error"]["message"]
    undeclared = _rebuild(data, change=lambda files: files.update({"outputs/in_piu.txt": b"x"}))
    assert _inspect(c, undeclared).json()["error"]["code"] == "package_undeclared"
    missing = _rebuild(data, change=lambda files: files.pop("outputs/result.json"))
    assert _inspect(c, missing).json()["error"]["code"] == "package_missing_file"
    assert _inspect(c, b"non sono uno zip").json()["error"]["code"] == "package_invalid"
    # niente rimane nello scratch dopo un pacchetto rifiutato
    tmp = c.app.state.core.paths.scratch_tmp
    assert not list(tmp.glob(f"{service.STAGE_PREFIX}*"))


def test_traversal_symlink_and_bomb_packages_are_rejected_without_writing(client_with_ubq, tmp_path):
    c = client_with_ubq
    data = _package(c, _quality(c)["id"])

    def symlink():
        link = zipfile.ZipInfo("inputs/library/collegamento.pdb")
        link.external_attr = (stat.S_IFLNK | 0o777) << 16
        return [(link, b"/etc/passwd")]

    def bomb():
        info = zipfile.ZipInfo("outputs/zeri.txt")
        info.compress_type = zipfile.ZIP_DEFLATED
        return [(info, b"\0" * (8 * 1024 ** 2))]

    cases = {"package_unsafe_path": lambda: [(zipfile.ZipInfo("../../fuori.txt"), b"x")],
             "package_symlink": symlink, "package_bomb": bomb}
    for code, extra in cases.items():
        r = _inspect(c, _rebuild(data, extra=extra()))
        assert r.status_code in (400, 413) and r.json()["error"]["code"] == code, code
        r = c.post("/api/jobs/rerun", files={"file": ("p.zip", _rebuild(data, extra=extra()), "application/zip")})
        assert r.json()["error"]["code"] == code
    root = c.app.state.core.paths
    assert not list(Path(root.data).rglob("fuori.txt")) and not list(Path(root.scratch).rglob("fuori.txt"))
    assert not [j for j in c.get("/api/jobs").json() if j["kind"] == "rerun"]


def test_scripts_in_a_package_are_never_extracted(client_with_ubq, tmp_path):
    c = client_with_ubq
    data = _package(c, _quality(c)["id"])
    evil = b"import os\nos.system('touch /tmp/bioloom_eseguito')\n"

    def add_script(m):
        m["files"].append({"path": "scripts/model.py", "role": "script", "sha256": _sha(evil), "size": len(evil),
                           "job_path": "model.py"})

    crafted = _rebuild(data, change=lambda files: files.update({"scripts/model.py": evil}), manifest=add_script)
    path = tmp_path / "p.zip"
    path.write_bytes(crafted)
    pkg = service.open_package(path)
    extracted = service.extract(pkg, tmp_path / "estratto", repro.ROLES)
    assert "scripts/model.py" not in extracted and not (tmp_path / "estratto" / "scripts").exists()
    assert "inputs/library/1UBQ.pdb" in extracted


# ------------------------------------------------------------------------------------------ riesecuzione
def test_rerun_of_a_quality_job_is_identical(client_with_ubq):
    c = client_with_ubq
    job = _quality(c)
    done = _rerun(c, _package(c, job["id"]))
    res = done["result"]
    assert res["verdict"] == "identico", res
    assert res["kind"] == "quality" and res["original"]["job_id"] == job["id"]
    assert res["counts"]["different"] == 0 and res["counts"]["missing"] == 0 and res["counts"]["identical"] >= 1
    rows = {r["key"]: r for r in res["metrics"][0]["rows"]}
    assert rows["grade_score"]["original"] == job["result"]["grade"]["score"] and rows["grade_score"]["delta"] == 0
    names = {f["path"] for f in done["files"]}
    assert {"package.zip", service.REPORT_TEXT, service.REPORT_JSON, "riesecuzione/result.json"} <= names
    report = c.get(f"/api/jobs/{done['id']}/files/{service.REPORT_TEXT}").text
    assert "Esito: IDENTICO" in report and job["id"] in report
    assert done["title"].startswith("Riesecuzione · ")
    # la riesecuzione non ha un pacchetto proprio
    r = c.get(f"/api/jobs/{done['id']}/package")
    assert r.status_code == 400 and r.json()["error"]["code"] == "package_unsupported"


def test_rerun_through_inspect_preflight_and_concurrent_launches(client_with_ubq):
    c = client_with_ubq
    data = _package(c, _quality(c)["id"])
    summary = _inspect(c, data).json()
    package_id = summary["package_id"]
    assert len(package_id) == 32 and summary["kind_label"] == "analisi di qualità"
    pre = c.post("/api/compute/preflight", json={"kind": "rerun", "params": {"package_id": package_id}}).json()
    assert pre["ok"], pre["checks"]
    assert pre["estimate"] and pre["estimate"]["basis"] == repro.RERUN_BASIS
    assert any(x["code"] == "estimated_time" for x in pre["checks"])
    bad = c.post("/api/compute/preflight", json={"kind": "rerun", "params": {"package_id": "f" * 32}}).json()
    assert not bad["ok"] and bad["checks"][0]["code"] == "package_not_found"
    invalid = c.post("/api/compute/preflight", json={"kind": "rerun", "params": {}}).json()
    assert invalid["checks"][0]["code"] == "validation_error"
    # due avvii insieme dallo stesso pacchetto caricato: due job, ognuno con la sua copia del pacchetto
    with ThreadPoolExecutor(2) as pool:
        responses = list(pool.map(lambda _i: c.post("/api/jobs/rerun", data={"package_id": package_id}), range(2)))
    ids = [r.json()["id"] for r in responses]
    assert all(r.status_code == 200 for r in responses) and len(set(ids)) == 2
    for job_id in ids:
        done = wait_job(c, job_id)
        assert done["status"] == "completed" and done["result"]["verdict"] == "identico"
    assert c.delete(f"/api/jobs/rerun/{package_id}").status_code == 200
    assert c.post("/api/jobs/rerun", data={"package_id": package_id}).json()["error"]["code"] == "package_not_found"
    assert c.post("/api/jobs/rerun", data={}).json()["error"]["code"] == "package_required"


def test_different_versions_are_a_warning_not_an_error(client_with_ubq):
    c = client_with_ubq
    data = _package(c, _quality(c)["id"])

    def older(m):
        m["bioloom"] = {"version": "4.0.0", "image": "4.0.0"}
        m["environment"]["tools"]["dssp"] = {"label": "DSSP", "available": True, "version": "0.9.9"}

    summary = _inspect(c, _rebuild(data, manifest=older)).json()
    warn = {x["code"] for x in summary["checks"] if x["level"] == "warning"}
    assert "rerun_version_bioloom" in warn and not [x for x in summary["checks"] if x["level"] == "error"]
    pre = c.post("/api/compute/preflight", json={"kind": "rerun", "params": {"package_id": summary["package_id"]}})
    assert pre.json()["ok"] and "rerun_version_bioloom" in {x["code"] for x in pre.json()["checks"]}
    done = _rerun(c, _rebuild(data, manifest=older))
    assert any("BioLoom 4.0.0" in w for w in done["result"]["warnings"])
    assert done["result"]["verdict"] == "identico"


def test_rerun_of_structural_alignment_and_analysis_with_packaged_inputs(client_with_ubq, tmp_path):
    c = client_with_ubq
    variant = ubq_variant(tmp_path / "ubq_corta.pdb", drop=range(70, 77))
    with open(variant, "rb") as fh:
        upload = c.post("/api/files/structure", files={"file": ("ubq_corta.pdb", fh, "chemical/x-pdb")}).json()
    align = c.post("/api/align", json={"mode": "structural", "tool": "builtin", "structures": [
        {"type": "template", "id": "1UBQ", "chain": "A"}, upload["source"]]}).json()
    done = wait_job(c, align["id"])
    assert done["status"] == "completed", done.get("error")
    msa = c.post("/api/align", json={"mode": "multiple", "tool": "builtin", "records": [
        {"name": "umana", "sequence": YEAST_UB.replace("S", "P", 1)}, {"name": "lievito", "sequence": YEAST_UB},
        {"name": "corta", "sequence": YEAST_UB[:70]}]}).json()
    assert wait_job(c, msa["id"])["status"] == "completed"
    analysis = c.post("/api/analysis", json={"source": {"type": "template", "id": "1UBQ"},
                                             "msa": {"job_id": msa["id"]}}).json()
    assert wait_job(c, analysis["id"])["status"] == "completed"
    structural = _members(_package(c, align["id"]))
    assert f"inputs/uploads/{upload['source']['file']}" in structural and "inputs/library/1UBQ.pdb" in structural
    packaged_msa = _members(_package(c, analysis["id"]))[f"inputs/jobs/{msa['id']}/alignment.json"]
    assert json.loads(packaged_msa)["ids"] == ["umana", "lievito", "corta"]
    # le sorgenti originali spariscono: la riesecuzione usa solo il pacchetto
    pkg_align, pkg_analysis = _package(c, align["id"]), _package(c, analysis["id"])
    Path(c.app.state.core.paths.uploads / upload["source"]["file"]).unlink()
    c.delete(f"/api/jobs/{msa['id']}")
    results = [_rerun(c, data)["result"] for data in (pkg_align, pkg_analysis)]
    assert [r["verdict"] for r in results] == ["identico", "identico"], results
    rows = {r["key"]: r for r in results[0]["metrics"][0]["rows"]}
    assert rows["tm_score"]["within"] and rows["tm_score"]["delta"] == 0
    rows = {r["key"]: r for r in results[1]["metrics"][0]["rows"]}
    assert rows["hbonds"]["original"] > 0 and rows["hbonds"]["delta"] == 0


def test_cli_rerun_in_a_subprocess_with_a_temporary_data_folder(client_with_ubq, tmp_path):
    c = client_with_ubq
    data = _package(c, _quality(c)["id"])
    package = tmp_path / "pacchetto.zip"
    package.write_bytes(data)
    cli_data = tmp_path / "dati_cli"
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    env.pop("BIOLOOM_SCRATCH_DIR", None)
    proc = subprocess.run([sys.executable, str(ROOT / "bioloom.py"), "rerun", str(package), "--data", str(cli_data)],
                          capture_output=True, text=True, encoding="utf-8", timeout=600, env=env, cwd=str(tmp_path))
    assert proc.returncode == 0, proc.stdout[-2000:] + proc.stderr[-2000:]
    assert "Esito: IDENTICO" in proc.stdout and "Job di riesecuzione" in proc.stdout
    assert (cli_data / "bioloom.db").is_file() and (cli_data / ".scratch").is_dir()
    jobs = [p for p in (cli_data / "jobs").iterdir() if p.is_dir() and not p.name.startswith(".")]
    assert len(jobs) == 1 and (jobs[0] / service.REPORT_TEXT).is_file() and (jobs[0] / "package.zip").is_file()
    broken = tmp_path / "rotto.zip"
    broken.write_bytes(_rebuild(data, change=lambda files: files.update({"outputs/result.json": b"{}"})))
    proc = subprocess.run([sys.executable, str(ROOT / "bioloom.py"), "rerun", str(broken), "--data", str(cli_data)],
                          capture_output=True, text=True, encoding="utf-8", timeout=300, env=env, cwd=str(tmp_path))
    assert proc.returncode == 2 and "Checksum SHA-256" in proc.stderr


def test_cli_exit_codes_for_a_failed_or_different_rerun_with_italian_messages(client_with_ubq, tmp_path, monkeypatch,
                                                                            capsys):
    """Codice 2 e «Riesecuzione non riuscita: …» (mai lo stato in inglese) per un job fallito, codice 1 per un esito
    diverso: i due casi del docstring di cli.py che il test del sottoprocesso non esercita."""
    from bioloom import cli

    c = client_with_ubq
    package = tmp_path / "pacchetto.zip"
    package.write_bytes(_package(c, _quality(c)["id"]))

    def failing(self, ctx, p):
        raise AppError("MODELLER non è configurato: manca la chiave di licenza.", code="modeller_license_missing")

    monkeypatch.setattr(service.ReproducibilityService, "handle", failing)
    assert cli.rerun_main([str(package), "--data", str(tmp_path / "dati_1")]) == cli.EXIT_ERROR
    err = capsys.readouterr().err
    assert "Riesecuzione non riuscita: MODELLER non è configurato" in err and "failed" not in err
    monkeypatch.setattr(service.ReproducibilityService, "handle",
                        lambda self, ctx, p: {"verdict": "diverso", "summary": "Diverso: 1 file oltre la tolleranza."})
    assert cli.rerun_main([str(package), "--data", str(tmp_path / "dati_2")]) == cli.EXIT_DIFFERENT
    assert "Diverso: 1 file oltre la tolleranza." in capsys.readouterr().out
    assert cli.failure_text({"status": "cancelled", "error": None}) == "Riesecuzione annullata."


@pytest.mark.modeller
@requires_modeller
def test_model_package_and_rerun_with_the_same_seed(client_with_ubq):
    c = client_with_ubq
    job = c.post("/api/model", json={"target": {"name": "yeast_ub", "sequence": YEAST_UB},
                                     "templates": [{"id": "1UBQ", "chains": ["A"]}],
                                     "options": {"models": 2, "refinement": "fast"}}).json()
    done = wait_job(c, job["id"], timeout=600)
    assert done["status"] == "completed", done.get("error")
    data = _package(c, job["id"])
    files = _members(data)
    m = json.loads(files["manifest.json"])
    assert m["seeds"]["modeller_rand_seed"] == -8123
    assert m["seeds"]["modeller_parallel"] is True and done["result"]["options"]["parallel"] is True
    assert m["environment"]["tools"]["modeller"]["version"]
    assert {"inputs/library/1UBQ.pdb", "inputs/library/1UBQ.meta.json", "inputs/templates/1UBQ_A.pdb",
            "inputs/alignment.ali", "inputs/sequences.fasta", "scripts/model.py", "scripts/align.py",
            "outputs/models.json"} <= set(files)
    for model in done["result"]["models"]:
        assert f"outputs/{model['file']}" in files
    assert files["inputs/alignment.ali"].decode() == done["result"]["alignment"]
    assert "rand_seed=-8123" in files["scripts/model.py"].decode()
    assert "BIOLOOM_MODELLER_KEY" in files["scripts/model.py"].decode()  # la chiave arriva dall'ambiente, mai scritta
    rerun = _rerun(c, data)
    res = rerun["result"]
    # MODELLER è deterministico con lo stesso seme: modelli identici (a parte la data della riga EXPDTA)
    assert res["verdict"] in ("identico", "equivalente"), res
    assert res["counts"]["different"] == 0 and res["counts"]["missing"] == 0
    for section in res["metrics"]:
        assert all(row["within"] for row in section["rows"]), section
    model_rows = {r["key"]: r for r in res["metrics"][1]["rows"]}
    assert model_rows["dope"]["delta"] == 0 and model_rows["ga341"]["within"]
    assert "Allineamento usato da MODELLER" in json.dumps(res["metrics"][0])

    # un altro seme (manifest risigillato, come un pacchetto di un altro job): differenze quantificate
    def other_seed(manifest):
        manifest["params"]["options"]["seed"] = -1234

    changed = _rerun(c, _rebuild(data, manifest=other_seed))["result"]
    assert changed["verdict"] == "diverso"

    # pacchetto di una versione precedente (senza la modalità): riesecuzione seriale, quindi modelli diversi da
    # quelli costruiti in parallelo
    def older(manifest):
        manifest["seeds"].pop("modeller_parallel")

    serial = _rerun(c, _rebuild(data, manifest=older))
    assert serial["result"]["verdict"] == "diverso"
    assert "MODELLER in parallelo" not in c.get(f"/api/jobs/{serial['id']}/log").json()["text"]
    moved = [s for s in changed["structures"] if s["path"].endswith(".pdb") and "yeast_ub.B9999" in s["path"]]
    assert moved and all(s["coordinates"]["rmsd"] > repro.COORD_TOLERANCE for s in moved)
    assert any(not row["within"] for section in changed["metrics"][1:] for row in section["rows"])

    # analisi di qualità di un modello del job (sorgente "job"): il modello viaggia nel pacchetto
    best = done["result"]["best_model"]
    quality = _quality(c, {"type": "job", "job_id": job["id"], "file": best})
    packaged = _package(c, quality["id"])
    assert f"inputs/jobs/{job['id']}/{best}" in _members(packaged)
    c.delete(f"/api/jobs/{job['id']}")
    assert _rerun(c, packaged)["result"]["verdict"] == "identico"


@pytest.mark.modeller
@requires_modeller
def test_loop_package_and_rerun(client_with_ubq):
    c = client_with_ubq
    job = c.post("/api/model/loop", json={"source": {"type": "template", "id": "1UBQ"}, "chain": "A", "start": 7,
                                          "end": 10, "models": 1, "level": "very_fast"}).json()
    done = wait_job(c, job["id"], timeout=600)
    assert done["status"] == "completed", done.get("error")
    data = _package(c, job["id"])
    m = _manifest(data)
    assert m["seeds"]["modeller_rand_seed"] == -8123 and "scripts/loop.py" in _members(data)
    res = _rerun(c, data)["result"]
    assert res["verdict"] == "identico", res
    assert all(row["within"] for section in res["metrics"] for row in section["rows"])


@pytest.mark.modeller
@requires_modeller
def test_align2d_package_and_rerun(client_with_ubq):
    c = client_with_ubq
    job = c.post("/api/align2d", json={"target": {"name": "yeast_ub", "sequence": YEAST_UB},
                                       "templates": [{"id": "1UBQ", "chains": ["A"]}]}).json()
    assert wait_job(c, job["id"])["status"] == "completed"
    data = _package(c, job["id"])
    assert "scripts/align.py" in _members(data)
    res = _rerun(c, data)["result"]
    assert res["verdict"] == "identico", res
    assert res["metrics"][0]["rows"][0]["within"]


@pytest.mark.modeller
@requires_modeller
def test_same_template_with_two_chains_packages_both_prepared_copies(client):
    """Lo stesso template due volte con catene diverse (dimero sintetico): un file della libreria, due copie
    preparate, entrambe confrontate con la riesecuzione (salign + align2d sui due template)."""
    c = client
    with open(DATA / "ubq_dimer.pdb", "rb") as fh:
        assert c.post("/api/templates/upload", files={"file": ("ubq_dimer.pdb", fh, "chemical/x-pdb")}).status_code == 200
    job = c.post("/api/align2d", json={"target": {"name": "yeast_ub", "sequence": YEAST_UB},
                                       "templates": [{"id": "UBQ_DIMER", "chains": ["A"]},
                                                     {"id": "UBQ_DIMER", "chains": ["B"]}]}).json()
    done = wait_job(c, job["id"])
    assert done["status"] == "completed", done.get("error")
    data = _package(c, job["id"])
    files = _members(data)
    assert {"inputs/library/UBQ_DIMER.pdb", "inputs/templates/UBQ_DIMER_A.pdb",
            "inputs/templates/UBQ_DIMER_B.pdb"} <= set(files)
    res = _rerun(c, data)["result"]
    assert res["verdict"] == "identico", res


@requires_openmm
def test_refine_package_and_rerun_is_identical_with_one_openmm_thread(client_with_ubq, monkeypatch):
    """Con il seme anche per idrogeni e atomi aggiunti (PDBFixer, Modeller di OpenMM) e un solo thread della
    piattaforma CPU la rifinitura è identica byte per byte. Con più thread OpenMM somma le forze in un ordine che
    cambia da un'esecuzione all'altra: differenze piccole (misurate su 1UBQ: RMSD 0,016 Å) che il confronto quantifica."""
    from bioloom.core import jobs as jobs_module
    from bioloom.engines import refine as refine_engine

    jobs_module.set_subprocess_env("OPENMM_CPU_THREADS", "1")  # anche il contesto di Modeller.addHydrogens
    monkeypatch.setattr(refine_engine.os, "cpu_count", lambda: 2)  # richiesta di un solo slot: --threads 1
    c = client_with_ubq
    job = c.post("/api/refine", json={"source": {"type": "template", "id": "1UBQ"}}).json()
    done = wait_job(c, job["id"], timeout=900)
    assert done["status"] == "completed", done.get("error")
    assert done["result"]["openmm"]["threads"] == 1
    m = _manifest(_package(c, job["id"]))
    assert {"openmm", "pdbfixer"} <= set(m["environment"]["tools"])
    assert m["environment"]["tools"]["openmm"]["version"] and m["seeds"]["openmm_seed"] == 20240611
    res = _rerun(c, _package(c, job["id"]))["result"]
    assert res["verdict"] == "identico", res["summary"]
    rows = {r["key"]: r for r in res["metrics"][0]["rows"]}
    assert rows["before"]["delta"] == 0 and rows["after"]["delta"] == 0 and rows["rmsd"]["within"]


@pytest.mark.modeller
@requires_modeller
def test_mutate_package_and_rerun_with_the_same_seeds(client_with_ubq):
    """Mutagenesi K48R: semi di MODELLER e strumenti nel manifest, struttura nel pacchetto; la riesecuzione dà gli
    stessi modelli (MODELLER deterministico con gli stessi semi), la stessa classe e lo stesso ΔDOPE."""
    c = client_with_ubq
    job = wait_job(c, c.post("/api/mutate", json={"source": {"type": "template", "id": "1UBQ"},
                                                   "mutations": "A:K48R"}).json()["id"], timeout=600)
    assert job["status"] == "completed", job.get("error")
    data = _package(c, job["id"])
    m = _manifest(data)
    assert m["rerun"]["supported"] and m["seeds"]["modeller_rand_seed"] == [-49837, -49838, -49839]
    assert {"modeller", "dssp"} <= set(m["environment"]["tools"]) and "openmm" not in m["environment"]["tools"]
    files = _members(data)
    assert "inputs/library/1UBQ.pdb" in files and "outputs/1UBQ_K48R.pdb" in files and "scripts/mutate.py" in files
    res = _rerun(c, data)["result"]
    assert res["verdict"] in ("identico", "equivalente"), res["summary"]
    assert res["scientific"] is None  # senza energia il calcolo è deterministico: nessuna tolleranza scientifica
    rows = {r["key"]: r for r in res["metrics"][0]["rows"]}
    assert rows["class"]["original"] == job["result"]["assessment"]["code"] and rows["class"]["within"]
    assert rows["delta_dope"]["delta"] == 0 and rows["rmsd_all"]["within"]


@pytest.mark.modeller
@requires_modeller
def test_alascan_of_a_pocket_package_carries_the_pockets_and_reruns(client, monkeypatch):
    """Scansione della tasca di un job `pockets`: le tasche viaggiano nel pacchetto (inputs/jobs/<id>/pockets.json) e
    la riesecuzione le usa anche dopo l'eliminazione del job delle tasche."""
    from bioloom.services import structure_tools

    fake = {"summary": {"count": 1, "druggable": 1, "threshold": 0.5,
                        "best": {"id": 1, "druggability": 0.8, "volume": 300.0}},
            "pockets": [{"id": 1, "druggability": 0.8, "residues": [{"chain": "A", "resnum": "30"},
                                                                   {"chain": "A", "resnum": "43"}]}],
            "warnings": []}
    monkeypatch.setattr(structure_tools.pockets, "run", lambda path, ctx, workdir: fake)
    with open(DATA / "1UBQ.pdb", "rb") as fh:
        assert client.post("/api/templates/upload", files={"file": ("1UBQ.pdb", fh, "chemical/x-pdb")}).status_code == 200
    src = {"type": "template", "id": "1UBQ"}
    pockets = wait_job(client, client.post("/api/pockets", json={"source": src}).json()["id"])
    scan = wait_job(client, client.post("/api/alascan", json={"source": src, "selection": {
        "mode": "pocket", "pocket_job": pockets["id"], "pocket": 1}}).json()["id"], timeout=900)
    assert scan["status"] == "completed", scan.get("error")
    data = _package(client, scan["id"])
    files = _members(data)
    carried = json.loads(files[f"inputs/jobs/{pockets['id']}/pockets.json"])
    assert carried["result"]["pockets"][0]["residues"][1]["resnum"] == "43"
    assert next(f for f in _manifest(data)["files"] if f["path"].endswith("pockets.json"))["role"] == "job_result"
    client.delete(f"/api/jobs/{pockets['id']}")
    res = _rerun(client, data)["result"]
    assert res["verdict"] in ("identico", "equivalente"), res["summary"]
    rows = {r["key"]: r for r in res["metrics"][0]["rows"]}
    assert rows["counts"]["within"] and rows["A:I30A:class"]["within"] and rows["A:L43A:delta_dope"]["delta"] == 0


def test_prediction_package_documents_the_remote_input_and_is_not_rerunnable(client_with_ubq, monkeypatch, tmp_path):
    """Confronto con ESMFold (servizio simulato): pacchetto con la predizione ricevuta, la struttura confrontata e la
    predizione come input remoto datato; la riesecuzione è rifiutata come non riproducibile localmente."""
    import httpx

    from bioloom.engines import remote
    from tests.test_predict import FakeServices, ubq_prediction

    fake = FakeServices()
    fake.esm = [(200, ubq_prediction())]
    monkeypatch.setattr(remote, "_client", httpx.Client(transport=httpx.MockTransport(lambda r: fake.handler(r))))
    monkeypatch.setattr(remote, "_cache_dir", tmp_path / "remote_cache")
    c = client_with_ubq
    assert c.put("/api/settings", json={"esmfold_remote": True}).status_code == 200
    job = wait_job(c, c.post("/api/predict", json={"source": {"type": "template", "id": "1UBQ", "chain": "A"}}
                                 ).json()["id"], timeout=120)
    assert job["status"] == "completed", job.get("error")
    data = _package(c, job["id"])
    m = _manifest(data)
    [esm] = m["remote"]
    assert esm["service"].startswith("ESMFold") and esm["date"] and "76 residui" in esm["what"]
    assert m["rerun"]["supported"] is False and "servizio remoto" in m["rerun"]["reason"]
    assert {"outputs/prediction_esmfold.pdb", "inputs/library/1UBQ.pdb", "outputs/result.json"} <= set(_members(data))
    summary = _inspect(c, data).json()
    assert summary["checks"][0]["code"] == "rerun_not_reproducible" and summary["kind_label"] == \
        "confronto con una predizione"
    r = c.post("/api/jobs/rerun", files={"file": ("p.zip", data, "application/zip")})
    assert r.status_code == 400 and r.json()["error"]["code"] == "rerun_not_reproducible"


def test_scientific_tolerance_of_a_refinement_rerun():
    """Rifinitura rieseguita con più thread di OpenMM (risultati simulati): entro le tolleranze scientifiche l'esito è
    «compatibile», distinto da «identico» e da «diverso»; oltre (catena principale o energia) resta «diverso»."""
    pdb = (DATA / "1UBQ.pdb").read_text()

    def files(dx: float) -> list[dict]:
        refined = repro.compare_file("1UBQ_refined.pdb", pdb.encode(), _shift(pdb, dx).encode())
        return [{**refined, "role": "output", "principal": True},
                {"path": "result.json", "status": "different", "role": "output", "principal": True}]

    def metrics(after: float) -> list[dict]:
        return [{"title": "Rifinitura", "rows": repro.metric_rows(
            {"before": -396.07, "after": -12431.37}, {"before": -395.47, "after": after},
            (("before", "Energia prima (kJ/mol)"), ("after", "Energia dopo (kJ/mol)")))}]

    near = repro.scientific_check("refine", files(0.01), metrics(-12392.2))  # rumore misurato: 0,020 Å, 41 kJ/mol
    assert near["within"] and repro.verdict(files(0.01), near) == "compatibile"
    assert files(0.01)[0]["coordinates"]["backbone_rmsd"] == pytest.approx(0.01, abs=1e-4)
    assert [c["within"] for c in near["checks"]] == [True, True, True] and near["checks"][2]["limit"] == 62.2
    backbone = repro.scientific_check("refine", files(0.08), metrics(-12392.2))
    assert not backbone["within"] and repro.verdict(files(0.08), backbone) == "diverso"
    energy = repro.scientific_check("refine", files(0.01), metrics(-12300.0))  # 131 kJ/mol: un altro minimo
    assert not energy["within"] and repro.verdict(files(0.01), energy) == "diverso"
    missing = files(0.01) + [{"path": "1UBQ_refined.pdb.quality.json", "status": "missing", "principal": True}]
    assert repro.verdict(missing, repro.scientific_check("refine", missing, metrics(-12392.2))) == "diverso"
    # tipi senza tolleranze scientifiche (o mutagenesi senza energia: deterministica)
    assert repro.scientific_check("quality", files(0.01), []) is None
    assert repro.scientific_check("mutate", files(0.01), [], {"energy": False}) is None
    assert repro.verdict(files(0.01), None) == "diverso"
    assert repro.verdict([{"status": "identical"}], near) == "identico"
    text = repro.report_text({"verdict": "compatibile", "scientific": near, "files": files(0.01)})
    assert "Esito: COMPATIBILE" in text and "Tolleranze scientifiche" in text and "0,020 Å" in text


def test_scientific_tolerance_of_a_mutagenesis_with_energy_keeps_the_class():
    """Mutagenesi con l'energia: le minimizzazioni hanno il rumore della rifinitura, ma classe e ΔDOPE devono restare
    uguali; se la classe cambia l'esito è «diverso» anche con energie entro la tolleranza."""
    pdb = (DATA / "1UBQ.pdb").read_text()
    files = [{**repro.compare_file("openmm_mutante/m_min.pdb", pdb.encode(), _shift(pdb, 0.01).encode()),
              "principal": True}]
    base = {"assessment": {"code": "neutral"}, "dope": {"delta": -30.0}, "energy": {"wild_type": -12000.0,
                                                                                   "mutant": -12100.0,
                                                                                   "reference_wild_type": -900.0,
                                                                                   "reference_mutant": -950.0}}
    rerun = {**base, "energy": {**base["energy"], "mutant": -12130.0}}
    metrics = service.ReproducibilityService._metrics("mutate", base, rerun)
    same = repro.scientific_check("mutate", files, metrics, {"energy": True})
    assert same["within"] and repro.verdict(files, same) == "compatibile"
    flipped = service.ReproducibilityService._metrics("mutate", base, {**rerun, "assessment": {"code": "uncertain"}})
    assert repro.verdict(files, repro.scientific_check("mutate", files, flipped, {"energy": True})) == "diverso"


def test_unsupported_rerun_kind_in_a_resealed_manifest_is_refused(client_with_ubq):
    c = client_with_ubq
    data = _package(c, _quality(c)["id"])

    def pipeline(m):
        m["job"]["kind"] = "pipeline"

    r = _inspect(c, _rebuild(data, manifest=pipeline))
    assert r.status_code == 200 and r.json()["checks"][0]["code"] == "rerun_unsupported"
    r = c.post("/api/jobs/rerun", files={"file": ("p.zip", _rebuild(data, manifest=pipeline), "application/zip")})
    assert r.status_code == 400 and r.json()["error"]["code"] == "rerun_unsupported"

    def bad_params(m):
        m["params"]["source"] = {"type": "ftp", "id": "x"}

    r = c.post("/api/jobs/rerun", files={"file": ("p.zip", _rebuild(data, manifest=bad_params), "application/zip")})
    assert r.status_code == 400 and r.json()["error"]["code"] == "package_params_invalid"


def test_manifest_of_an_unexpected_shape_is_a_reasoned_refusal(client_with_ubq):
    """Il manifest è un input non fidato e l'impronta si ricalcola: campi di forma inattesa (risigillati) danno
    package_manifest_invalid nell'anteprima, nei controlli preventivi e all'avvio, mai un errore interno."""
    c = client_with_ubq
    data = _package(c, _quality(c)["id"])
    cases = {
        "libraries": lambda m: m["environment"].update(libraries=[1]),
        "tools": lambda m: m["environment"].update(tools=[1]),
        "python": lambda m: m["environment"].update(python={"v": 3}),
        "notes": lambda m: m.update(notes=5),
        "remote": lambda m: m.update(remote=["RCSB"]),
        "duration": lambda m: m["job"].update(duration_seconds="lungo"),
        "rerun": lambda m: m.update(rerun=[True]),
    }
    staged = []
    for name, tweak in cases.items():
        bad = _rebuild(data, manifest=tweak)
        r = _inspect(c, bad)
        assert r.status_code == 400 and r.json()["error"]["code"] == "package_manifest_invalid", (name, r.text)
        assert "Manifest del pacchetto non valido" in r.json()["error"]["message"]
        r = c.post("/api/jobs/rerun", files={"file": ("p.zip", bad, "application/zip")})
        assert r.status_code == 400 and r.json()["error"]["code"] == "package_manifest_invalid", name
        package_id = c.app.state.core.repro.stage(io.BytesIO(bad))
        staged.append(package_id)
        pre = c.post("/api/compute/preflight", json={"kind": "rerun", "params": {"package_id": package_id}})
        assert pre.status_code == 200 and not pre.json()["ok"], name
        assert pre.json()["checks"][0]["code"] == "package_manifest_invalid", name
    assert not [j for j in c.get("/api/jobs").json() if j["kind"] == "rerun"]

    # parametri liberi di un tipo non rieseguibile (ricerca): anteprima con il rifiuto motivato, non un 500
    def search_with_odd_engine(m):
        m["job"]["kind"] = "search"
        m["params"]["engine"] = ["x"]

    r = _inspect(c, _rebuild(data, manifest=search_with_odd_engine))
    assert r.status_code == 200 and r.json()["checks"][0]["code"] == "rerun_not_reproducible"
    for package_id in staged:
        c.app.state.core.repro.discard(package_id)


def test_rerun_registration_and_packaged_compute_never_downloads(client, tmp_path, monkeypatch):
    from types import SimpleNamespace

    from bioloom.engines import remote
    from bioloom.engines.modeller import TemplateSpec

    core = client.app.state.core
    registration = core.jobs._handlers["rerun"]
    assert registration.priority == 1 and registration.pool == "cpu"
    before = dict(core.jobs._handlers)
    sandbox = service.PackagedCompute(core.compute, tmp_path / "pacchetto")
    assert core.jobs._handlers == before  # gli handler di sempre, senza registrarne altri
    monkeypatch.setattr(remote, "rcsb_download", lambda *a, **k: pytest.fail("nessun download dalla riesecuzione"))
    ctx = SimpleNamespace(check=lambda: None, log=lambda _m: None)
    with pytest.raises(AppError) as exc:
        sandbox._prepare_templates(ctx, [TemplateSpec(id="1ABC", chains=["A"])], tmp_path)
    assert exc.value.code == "package_missing_input" and "internet" in exc.value.message
    for bad in ({"type": "job", "job_id": "abc", "file": "../../etc/passwd"}, {"type": "job", "job_id": "../x", "file": "a"},
                {"type": "job", "job_id": "abc", "file": "/etc/passwd"}):
        with pytest.raises(AppError):
            sandbox.resolve_structure(bad)
    (tmp_path / "pacchetto" / "inputs" / "jobs" / "abc").mkdir(parents=True)
    (tmp_path / "pacchetto" / "inputs" / "jobs" / "abc" / "m.pdb").write_text((DATA / "1UBQ.pdb").read_text())
    path, label = sandbox.resolve_structure({"type": "job", "job_id": "abc", "file": "m.pdb"})
    assert path.name == "m.pdb" and label == "m"


def test_staged_packages_expire_with_the_scratch_cleanup(client_with_ubq):
    c = client_with_ubq
    summary = _inspect(c, _package(c, _quality(c)["id"])).json()
    core = c.app.state.core
    staged = core.repro.staged_path(summary["package_id"])
    old = time.time() - 25 * 3600
    os.utime(staged, (old, old))
    core.housekeeping.run()
    assert not staged.exists()
    r = c.post("/api/jobs/rerun", data={"package_id": summary["package_id"]})
    assert r.status_code == 404 and "24 ore" in r.json()["error"]["message"]
