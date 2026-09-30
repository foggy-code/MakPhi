"""Report HTML del modello: domain/report.py (HTML autonomo, SVG, PNG), services/reports.py e rotte
/api/jobs/{id}/report, ZIP della pipeline con i report.

MODELLER è simulato (come in test_consensus e test_pipeline_resume): i job scrivono i file che salverebbe
run_modeling, con le coordinate e le uscite reali di 1UBQ (profilo DOPE di MODELLER 10.8 in tests/data). Il test
con MODELLER reale (`requires_modeller`) costruisce il modello dell'ubiquitina di lievito su 1UBQ.
"""

import base64
import io
import json
import re
import shutil
import struct
import threading
import time
import xml.etree.ElementTree as ET
import zipfile
import zlib
from dataclasses import asdict

import pytest

from bioloom.core.errors import AppError
from bioloom.domain import consensus, grading, report, sequences
from bioloom.engines import modeller, quality
from bioloom.services import reports as reports_service

from .conftest import DATA, HUMAN_UB, YEAST_UB, requires_modeller, wait_job
from .test_consensus import UBQ_ZDOPE, _dope_profile, _fake_qmean

SVG = re.compile(r"<svg\b.*?</svg>", re.S)
NS = "{http://www.w3.org/2000/svg}"
EVIL = "<script>alert('x')</script>\"&"
SECTIONS = ("riepilogo", "bersaglio", "template", "allineamento", "parametri", "modelli", "qualita", "voto",
            "consenso", "ramachandran", "profili", "struttura-secondaria", "clash", "avvisi", "legenda", "riferimenti")
MODELLER_HEADER = ("EXPDTA    THEORETICAL MODEL, MODELLER 10.8 2026/09/30 15:53:05\n"
                   "REMARK   6 MODELLER OBJECTIVE FUNCTION:       242.7604\n"
                   "REMARK   6 MODELLER BEST TEMPLATE % SEQ ID:  96.053\n")


# ---------------------------------------------------------------------------------------------- dati di prova
def make_png(width=4, height=3, *, bad_crc=False, trailing=b"", idat=True) -> bytes:
    """PNG RGB minimo costruito secondo la specifica (firma, IHDR, IDAT compresso con zlib, IEND)."""
    def chunk(kind: bytes, body: bytes, crc_delta: int = 0) -> bytes:
        crc = (zlib.crc32(kind + body) + crc_delta) & 0xFFFFFFFF
        return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", crc)

    raw = b"".join(b"\x00" + b"\x1d\x4e\x89" * width for _ in range(height))
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    data = report.PNG_SIGNATURE + chunk(b"IHDR", ihdr, 1 if bad_crc else 0)
    if idat:
        data += chunk(b"IDAT", zlib.compress(raw))
    return data + chunk(b"IEND", b"") + trailing


def b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def modeller_like(dest, header: str = MODELLER_HEADER) -> None:
    """Coordinate di 1UBQ (senza acque) con l'intestazione di un modello di MODELLER 10.8."""
    atoms = [ln for ln in (DATA / "1UBQ.pdb").read_text().splitlines() if ln.startswith(("ATOM", "TER"))]
    dest.write_text(header + "\n".join(atoms) + "\nEND\n")


def quality_with_dope(path) -> dict:
    """Rapporto di qualità della struttura con il profilo DOPE reale di 1UBQ (MODELLER 10.8)."""
    rep = quality.analyze(path, None, with_dope=False)
    profile = _dope_profile()
    for row, p in zip(rep["residues"], profile):
        row["dope"] = round(p["energy"], 4)
    rep["dope"] = {"score": -7981.95, "zscore": UBQ_ZDOPE, "profile": profile}
    rep["grade"] = grading.grade(rep["ramachandran"]["summary"]["outlier_pct"], rep["clashes"]["clashscore"], UBQ_ZDOPE)
    return rep


def pir(target: str, target_seq: str, template_seq: str = HUMAN_UB) -> str:
    return sequences.format_pir([sequences.template_entry("1UBQ_A", template_seq, "1", "A", "76", "A"),
                                 sequences.target_entry(target, target_seq)])


def svgs(text: str) -> list[ET.Element]:
    """Tutti i grafici SVG del documento, analizzati come XML (fallisce se uno non è ben formato)."""
    return [ET.fromstring(s) for s in SVG.findall(text)]


def rama_points(text: str) -> int:
    return sum(1 for svg in svgs(text) for c in svg.iter(f"{NS}circle") if c.get("class", "").startswith("rp "))


def flagged(text: str, title: str) -> int:
    svg = next(s for s in svgs(text) if s.get("aria-label") == title)
    return sum(1 for c in svg.iter(f"{NS}circle") if c.get("class") == "flag")


@pytest.fixture(scope="module")
def ubq_quality():
    return quality_with_dope(DATA / "1UBQ.pdb")


def model_data(rep: dict, **extra) -> dict:
    """Dati di un report di modellazione come li raccoglie services/reports.py."""
    alignment = pir("yeast", YEAST_UB)
    target_pir = next(e.sequence for e in sequences.parse_pir(alignment) if e.kind == "sequence")
    identity, coverage = report.alignment_identity(target_pir, HUMAN_UB)
    data = {
        "kind": "model", "title": "Modello yeast · 1UBQ", "job_id": "abc123", "project": "Ubiquitine",
        "created_at": "2026-09-30T15:50:00+00:00", "finished_at": "2026-09-30T15:53:05+00:00",
        "generated_at": "2026-09-30T16:00:00+00:00", "tools": [("BioLoom", "4.1.0"), ("MODELLER", "10.8")],
        "model": "yeast.B99990001.pdb", "target": {"name": "yeast", "sequence": YEAST_UB},
        "templates": [{"id": "1UBQ", "code": "1UBQ_A", "chains": ["A"], "first": "1", "last": "76",
                       "source": "RCSB PDB", "title": "UBIQUITIN", "method": "X-RAY DIFFRACTION", "resolution": 1.8,
                       "identity": identity, "coverage": coverage, "predicted": False}],
        "alignment": alignment, "options": asdict(modeller.parse_options({"models": 2, "refinement": "fast"})),
        "models": [{"file": "yeast.B99990001.pdb", "kind": "model", "dope": -7620.66, "zdope": -2.47, "ga341": 1.0,
                    "molpdf": 242.76, "grade": rep["grade"]["grade"], "grade_score": rep["grade"]["score"],
                    "best": True, "chosen": True},
                   {"file": "yeast.B99990002.pdb", "kind": "model", "dope": -7500.0, "zdope": -2.2, "ga341": 1.0,
                    "molpdf": 250.0, "best": False, "chosen": False}],
        "quality": rep, "consensus": consensus.compute(rep, modeller={"ga341": 1.0, "zdope": -2.47}),
        "warnings": [], "image_png": None,
    }
    data.update(extra)
    return data


# ---------------------------------------------------------------------------------------------- dominio
def test_alignment_identity_of_yeast_and_human_ubiquitin():
    # 73 residui identici su 76 (come il "% SEQ ID 96.053" di MODELLER), copertura completa
    identity, coverage = report.alignment_identity(YEAST_UB, HUMAN_UB)
    assert identity == pytest.approx(96.05, abs=0.01) and coverage == 100.0
    # gap del template e separatori non contano; il bersaglio scoperto riduce la copertura
    identity, coverage = report.alignment_identity("MQIFV/KT-L", "MQ--V/KTAL")
    assert identity == 100.0 and coverage == 75.0  # 6 degli 8 residui del bersaglio hanno un residuo di fronte
    assert report.alignment_identity("---", "AAA") == (None, None)


def test_modeller_texts():
    assert report.modeller_version(MODELLER_HEADER) == "10.8"
    assert report.modeller_version("EXPDTA    X-RAY DIFFRACTION\n") is None
    head = ("REMARK 999 BIOLOOM PLDDT_TRIM THRESHOLD 70 REMOVED 11 OF 76 FRAGMENTS 4\n")
    assert report.prepared_trim(head) == {"threshold": 70.0, "removed": 11, "total": 76, "removed_fragments": 4,
                                          "kept": 65}
    trims = report.model_trims("REMARK 999 BIOLOOM TEMPLATE AF-P0CG47-F1_A ALPHAFOLD PLDDT<70 REMOVED 11\n"
                               "REMARK 999 BIOLOOM TEMPLATE AF-Q1-F1_A ALPHAFOLD PLDDT_TRIM OFF\n")
    assert trims == {"AF-P0CG47-F1_A": {"threshold": 70.0, "removed": 11}, "AF-Q1-F1_A": None}


def test_png_is_validated():
    data = make_png(6, 5)
    assert report.decode_png(b64(data)) == data
    assert report.decode_png("data:image/png;base64," + b64(data)) == data  # come pngURI di 3Dmol
    assert report.decode_png("DATA:image/png;base64,\n" + b64(data)[:10] + "\n" + b64(data)[10:]) == data
    assert report.png_size(data) == (6, 5)


@pytest.mark.parametrize("value", [
    "non è base64!", b64(b"\xff\xd8\xff\xe0" + b"\x00" * 64), b64(make_png(bad_crc=True)), b64(make_png()[:-6]),
    b64(make_png(trailing=b"<script>")), b64(make_png(idat=False)), "data:image/jpeg;base64," + b64(make_png()),
    "data:image/png," + b64(make_png()), b64(make_png(0, 3)), "",
], ids=["testo", "jpeg", "crc", "troncato", "dati-dopo-iend", "senza-idat", "data-uri-jpeg", "data-uri-senza-base64",
        "larghezza-zero", "vuoto"])
def test_invalid_png_is_rejected(value):
    with pytest.raises(AppError) as exc:
        report.decode_png(value)
    assert exc.value.code == "report_image_invalid" and exc.value.status == 400


def test_too_large_png_is_rejected_before_decoding():
    with pytest.raises(AppError) as exc:
        report.decode_png("A" * (4 * report.MAX_IMAGE_BYTES // 3 + 8))
    assert exc.value.code == "report_image_too_large" and exc.value.status == 413


@pytest.mark.parametrize("stem,expected", [
    ("report_yeast.B99990001", "report_yeast.B99990001.html"), ("../../etc/passwd", "etc_passwd.html"),
    ('x";evil<script>.pdb', "x_evil_script_.pdb.html"), ("réport ümlaut", "r_port_mlaut.html"), ("", "report.html"),
    ("...", "report.html"), ("a" * 200, "a" * 80 + ".html"),
], ids=["modello", "percorso", "virgolette", "accenti", "vuoto", "punti", "lungo"])
def test_safe_filename(stem, expected):
    name = report.safe_filename(stem)
    assert name == expected and re.fullmatch(r"[A-Za-z0-9_.-]+", name) and ".." not in name


def test_render_has_every_section(ubq_quality):
    text = report.render(model_data(ubq_quality))
    assert text.startswith("<!DOCTYPE html>") and "<script" not in text.lower()
    assert f'content="{report.CSP}"' in text and "http://" not in text.replace(report.SVG_NS, "") \
        and "https://" not in text
    for sid in SECTIONS:
        assert f'id="{sid}"' in text, sid
    # intestazione, bersaglio, template con identità/copertura, allineamento a blocchi, modelli
    assert "Ubiquitine" in text and "MODELLER 10.8" in text and "30/09/2026 15:53 UTC" in text
    assert "     1  MQIFVKTLTG KTITLEVESS" in text and "96,1%" in text and "100,0%" in text
    assert text.count('<pre class="aln blk">') == 2  # 76 colonne: blocchi di 60 + 16
    assert '<tr class="best chosen">' in text and "rapido (md_level = refine.very_fast)" in text
    # voto con penalità e consenso (voto 98,9; zDOPE −2,73 → 100; GA341 1 → 100, peso 0,5)
    assert "98,9" in text and "99,6/100" in text
    assert '<span class="tag ok">buona</span>' in text
    # riferimenti usati
    for ref in ("Šali A., Blundell T.L. (1993)", "Shen M.Y., Sali A. (2006)", "Melo F., Sánchez R., Sali A. (2002)",
                "Lovell S.C. et al. (2003)", "Kabsch W., Sander C. (1983)"):
        assert ref in text
    assert "Benkert" not in text.split('id="riferimenti"')[1]  # QMEAN non calcolato: nessuna fonte
    # grafici: SVG ben formati, un punto di Ramachandran per residuo con φ e ψ (74 su 76 in 1UBQ), picco DOPE 73–76
    assert len(ubq_quality["ramachandran"]["points"]) == 74
    assert rama_points(text) == 74
    assert flagged(text, "Energia DOPE per residuo (media mobile di MODELLER)") == 4
    assert any(s.get("aria-label") == "Struttura secondaria per residuo" for s in svgs(text))


def test_render_escapes_untrusted_text(ubq_quality):
    rep = json.loads(json.dumps(ubq_quality))
    rep["structure"]["title"] = EVIL
    rep["ramachandran"]["points"][0]["resname"] = EVIL  # nome di residuo in un file caricato
    rep["ramachandran"]["points"][0]["region"] = "outlier"  # etichettato anche nel grafico
    rep["clashes"]["clashes"] = [{"atom1": f"A 1 {EVIL} CA", "atom2": "A 2 GLN N", "distance": 2.0, "overlap": 1.1}]
    rep["warnings"] = [EVIL]
    data = model_data(rep, title=EVIL, project=EVIL, model=f"{EVIL}.pdb", target={"name": EVIL, "sequence": "MQ"},
                      tools=[("BioLoom", EVIL)], warnings=[EVIL])
    data["templates"][0].update(title=EVIL, source=EVIL, id=EVIL)
    data["models"][0]["file"] = EVIL
    text = report.render(data)
    assert "<script" not in text.lower() and EVIL not in text
    assert "&lt;script&gt;alert(&#x27;x&#x27;)&lt;/script&gt;&quot;&amp;" in text
    assert rama_points(text) == 74  # SVG ancora ben formati con i nomi da escapare


def test_render_marks_alphafold_templates(ubq_quality):
    trim = {"threshold": 70.0, "total": 76, "removed": 15, "removed_threshold": 11, "removed_fragments": 4,
            "min_fragment": 5, "kept": 61, "mean_before": 85.1, "mean_after": 92.4,
            "removed_ranges": [["1", "5", "A"], ["71", "76", "A"]]}
    data = model_data(ubq_quality)
    data["templates"] = [{**data["templates"][0], "id": "AF-P0CG47-F1", "code": "AF-P0CG47-F1_A",
                          "source": "AlphaFold DB (EBI)", "predicted": True, "plddt_mean": 85.1, "trim": trim},
                         {**data["templates"][0], "id": "AF-Q1-F1", "code": "AF-Q1-F1_A", "predicted": True,
                          "trim": None, "trim_off": True}]
    text = report.render(data)
    assert text.count("modello predetto (AlphaFold DB), non una struttura sperimentale") == 2
    assert "rimossi 15 residui (di cui 4 in frammenti isolati di meno di 5 residui), restano 61 di 76" in text
    assert "pLDDT medio 85,1 → 92,4" in text and "tolti 1–5:A, 71–76:A" in text
    assert "usato senza ritaglio dei residui a basso pLDDT" in text
    assert "Jumper J. et al. (2021)" in text and "Tunyasuvunakool K. et al. (2021)" in text and "<dt>pLDDT</dt>" in text
    # senza template predetti nessuna fonte di AlphaFold
    assert "Jumper" not in report.render(model_data(ubq_quality))


def test_render_embeds_a_valid_png(ubq_quality):
    image = make_png(8, 6)
    text = report.render(model_data(ubq_quality, image_png=image))
    assert f'width="8" height="6" src="data:image/png;base64,{b64(image)}"' in text


def test_render_without_quality_report(ubq_quality):
    text = report.render(model_data(ubq_quality, quality=None, consensus=None))
    assert "Analisi di qualità non calcolata" in text and 'id="ramachandran"' not in text
    assert 'id="modelli"' in text and rama_points(text) == 0


# ---------------------------------------------------------------------------------------------- job model (simulato)
BEST, SECOND, FAILED = "yeast.B99990001.pdb", "yeast.B99990002.pdb", "yeast.B99990003.pdb"


def fake_model_job(client, *, files=(BEST, SECOND)) -> dict:
    """Job "model" con i file e il risultato che salverebbe run_modeling (modelli con le coordinate di 1UBQ)."""
    core = client.app.state.core

    def handler(ctx, p):
        alignment = pir("yeast", YEAST_UB)
        (ctx.workdir / "alignment.ali").write_text(alignment)
        for name in files:
            modeller_like(ctx.workdir / name)
        rep = quality_with_dope(ctx.workdir / files[0])
        (ctx.workdir / f"{files[0]}.quality.json").write_text(json.dumps(rep))
        models = [{"file": files[0], "failure": None, "molpdf": 242.76, "dope": -7620.66, "ga341": 1.0, "zdope": -2.47,
                   "kind": "model", "best": True, "quality": quality.summary_metrics(rep)}]
        models += [{"file": f, "failure": None, "molpdf": 250.0, "dope": -7500.0, "ga341": 1.0, "zdope": -2.2,
                    "kind": "model", "best": False} for f in files[1:]]
        models.append({"file": FAILED, "failure": "optimization failed", "molpdf": None, "dope": None, "ga341": None,
                       "zdope": None, "kind": "model", "best": False})
        return {"target": {"code": "yeast", "sequence": YEAST_UB},
                "templates": [{"id": "1UBQ", "code": "1UBQ_A", "file": "1UBQ_A.pdb", "chains": ["A"], "first": "1",
                               "last": "76", "sequence": HUMAN_UB, "ligands": 0}],
                "plddt_removed": 0, "alignment": alignment, "alignment_report": sequences.validate_pir(alignment),
                "options": asdict(modeller.parse_options({"models": 3, "refinement": "fast"})), "models": models,
                "best_model": files[0], "warnings": ["Avviso del job di prova"]}

    core.jobs.register("model", handler)
    job = client.post("/api/model", json={"target": {"name": "yeast", "sequence": YEAST_UB},
                                          "templates": [{"id": "1UBQ", "chains": ["A"]}]}).json()
    done = wait_job(client, job["id"])
    assert done["status"] == "completed", done["error"]
    return done


def test_model_report_route(client_with_ubq):
    c = client_with_ubq
    job = fake_model_job(c)
    r = c.get(f"/api/jobs/{job['id']}/report/{BEST}")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/html")
    assert r.headers["content-disposition"] == 'attachment; filename="report_yeast.B99990001.html"'
    assert r.headers["content-security-policy"] == report.CSP and r.headers["x-content-type-options"] == "nosniff"
    text = r.text
    for sid in SECTIONS:
        assert f'id="{sid}"' in text, sid
    assert "MODELLER 10.8" in text and "BioLoom 4." in text and "file caricato" in text  # 1UBQ caricato nei test
    assert "96,1%" in text and "Avviso del job di prova" in text and "optimization failed" in text
    assert "99,6/100" in text and rama_points(text) == 74
    assert flagged(text, "Energia DOPE per residuo (media mobile di MODELLER)") == 4
    # inline, per aprirlo nel browser
    inline = c.get(f"/api/jobs/{job['id']}/report/{BEST}", params={"download": "false"})
    assert inline.headers["content-disposition"].startswith("inline;")
    # un modello senza rapporto di qualità: report dei soli risultati di MODELLER
    other = c.get(f"/api/jobs/{job['id']}/report/{SECOND}").text
    assert "Analisi di qualità non calcolata" in other and rama_points(other) == 0
    assert '<tr class="chosen">' in other and '<tr class="best">' in other


def test_model_report_errors(client_with_ubq):
    c = client_with_ubq
    job = fake_model_job(c)
    base = f"/api/jobs/{job['id']}/report"
    assert c.get(f"{base}/altro.pdb").status_code == 404  # non è un modello del job
    assert c.get(f"{base}/{FAILED}").status_code == 404  # modello fallito
    assert c.get(f"{base}/alignment.ali").status_code == 404  # file del job che non è un modello
    assert c.get(f"{base}/..%2F..%2Fsettings.json").status_code in (400, 404)
    r = c.get(base)
    assert r.status_code == 400 and r.json()["error"]["code"] == "report_model_required"
    assert c.get("/api/jobs/0123456789abcdef/report/x.pdb").status_code == 404
    # job non completato
    core = c.app.state.core
    core.db.execute("UPDATE jobs SET status='running' WHERE id=?", (job["id"],))
    assert c.get(f"{base}/{BEST}").status_code == 409
    core.db.execute("UPDATE jobs SET status='completed' WHERE id=?", (job["id"],))
    # QMEAN su un modello senza rapporto di qualità: non si può includere
    assert c.get(f"{base}/{SECOND}", params={"qmean_job": job["id"]}).status_code == 404


def test_model_report_with_the_3d_image(client_with_ubq):
    c = client_with_ubq
    job = fake_model_job(c)
    url = f"/api/jobs/{job['id']}/report/{BEST}"
    image = make_png(12, 9)
    r = c.post(url, json={"image_png_base64": "data:image/png;base64," + b64(image)})
    assert r.status_code == 200 and f"src=\"data:image/png;base64,{b64(image)}\"" in r.text
    assert c.post(url, json={}).status_code == 200 and "data:image/png" not in c.post(url, json={}).text
    assert c.post(url).status_code == 200  # senza corpo
    bad = c.post(url, json={"image_png_base64": b64(b"GIF89a" + b"\x00" * 40)})
    assert bad.status_code == 400 and bad.json()["error"]["code"] == "report_image_invalid"
    big = c.post(url, json={"image_png_base64": "A" * (4 * report.MAX_IMAGE_BYTES // 3 + 8)})
    assert big.status_code == 413 and big.json()["error"]["code"] == "report_image_too_large"


def test_download_name_is_safe_for_any_model_name(client_with_ubq):
    c = client_with_ubq
    evil = 'y";a<b>.pdb'
    job = fake_model_job(c, files=(evil,))
    r = c.get(f"/api/jobs/{job['id']}/report/{evil}")
    assert r.status_code == 200
    assert r.headers["content-disposition"] == 'attachment; filename="report_y_a_b.html"'
    assert "y&quot;;a&lt;b&gt;.pdb" in r.text and 'y";a<b>' not in r.text


def test_loop_report(client_with_ubq):
    c = client_with_ubq
    core = c.app.state.core
    name = "1UBQ.BL00010001.pdb"

    def handler(ctx, p):
        modeller_like(ctx.workdir / name)
        (ctx.workdir / f"{name}.quality.json").write_text(json.dumps(quality.analyze(ctx.workdir / name, None,
                                                                                     with_dope=False)))
        return {"source": "1UBQ", "region": ["7:A", "12:A"], "best_model": name,
                "models": [{"file": name, "failure": None, "dope": -7700.0, "zdope": -2.5, "molpdf": 100.0,
                            "kind": "loop", "best": True}]}

    core.jobs.register("loop", handler)
    job = c.post("/api/model/loop", json={"source": {"type": "template", "id": "1UBQ"}, "chain": "A", "start": 7,
                                          "end": 12, "models": 2}).json()
    assert wait_job(c, job["id"])["status"] == "completed"
    text = c.get(f"/api/jobs/{job['id']}/report/{name}").text
    assert 'id="parametri"' in text and "DOPELoopModel" in text and "7:A – 12:A" in text
    assert 'id="allineamento"' not in text and 'id="template"' not in text
    assert "MQIFVKTLTG" in text and rama_points(text) == 74


# ---------------------------------------------------------------------------------------------- job quality
def test_quality_job_report(client_with_ubq, monkeypatch):
    c = client_with_ubq
    _fake_qmean(monkeypatch)  # punteggi locali sotto 0,6 dal residuo 70
    qjob = c.post("/api/quality", json={"source": {"type": "template", "id": "1UBQ"}, "dope": False,
                                        "procheck": False, "prosa": False}).json()
    assert wait_job(c, qjob["id"])["status"] == "completed"
    r = c.get(f"/api/jobs/{qjob['id']}/report")
    assert r.status_code == 200
    assert r.headers["content-disposition"] == 'attachment; filename="report_qualita_1UBQ.html"'
    text = r.text
    assert "Report dell'analisi di qualità" in text and "Struttura analizzata" in text and "template 1UBQ" in text
    for sid in ("riepilogo", "bersaglio", "qualita", "consenso", "ramachandran", "struttura-secondaria", "clash",
                "legenda", "riferimenti"):
        assert f'id="{sid}"' in text, sid
    for sid in ("template", "allineamento", "parametri", "modelli"):
        assert f'id="{sid}"' not in text, sid
    assert rama_points(text) == 74 and "UBIQUITIN" in text
    assert "Šali A., Blundell" not in text  # niente MODELLER: né DOPE né modelli
    # con QMEAN (stessa struttura): Z-score nel consenso e profilo locale con 70–76 sotto 0,6
    qm = c.post("/api/qmean", json={"source": {"type": "template", "id": "1UBQ"}}).json()
    assert wait_job(c, qm["id"])["status"] == "completed"
    text = c.post(f"/api/jobs/{qjob['id']}/report", params={"qmean_job": qm["id"]},
                  json={"image_png_base64": b64(make_png())}).text
    assert flagged(text, "QMEAN locale per residuo") == 7 and "QMEAN4 Z-score" in text
    assert "Benkert P., Biasini M., Schwede T. (2011)" in text and "QMEAN 4.3.0 QMEAN (locale)" in text
    assert "data:image/png;base64," in text
    # errori: un file per un job quality, un QMEAN di un altro job, un job di un tipo senza report
    r = c.get(f"/api/jobs/{qjob['id']}/report/modello.pdb")
    assert r.status_code == 400 and r.json()["error"]["code"] == "report_unsupported"
    r = c.get(f"/api/jobs/{qjob['id']}/report", params={"qmean_job": qjob["id"]})
    assert r.status_code == 400 and r.json()["error"]["code"] == "qmean_job_invalid"
    align = c.post("/api/align", json={"mode": "pairwise", "records": [{"name": "a", "sequence": HUMAN_UB},
                                                                      {"name": "b", "sequence": YEAST_UB}]}).json()
    assert wait_job(c, align["id"])["status"] == "completed"
    r = c.get(f"/api/jobs/{align['id']}/report/alignment.fasta")
    assert r.status_code == 400 and r.json()["error"]["code"] == "report_unsupported"


# ---------------------------------------------------------------------------------------------- pipeline
class ReportModeller:
    """Sostituisce run_modeling nella pipeline: scrive modello, models.json, allineamento, copia preparata del
    template e rapporto di qualità come la modellazione vera; le sequenze in `fail` falliscono."""

    def __init__(self, fail=()) -> None:
        self.fail = set(fail)

    def __call__(self, sub, *, target, **_kwargs) -> dict:
        if target.name in self.fail:
            raise AppError(f"modellazione di {target.name} non riuscita")
        work, name = sub.workdir, f"{target.name}.B99990001.pdb"
        modeller_like(work / name)
        shutil.copyfile(DATA / "1UBQ.pdb", work / "1UBQ_A.pdb")
        (work / "alignment.ali").write_text(pir(target.name, target.sequence))
        (work / "models.json").write_text(json.dumps({"models": [
            {"file": name, "failure": None, "molpdf": 242.76, "dope": -7620.66, "ga341": 1.0, "zdope": -2.47}]}))
        rep = quality_with_dope(work / name)
        (work / f"{name}.quality.json").write_text(json.dumps(rep))
        return {"models": [{"file": name, "best": True, "dope": -7620.66, "ga341": 1.0, "zdope": -2.47,
                            "molpdf": 242.76, "quality": quality.summary_metrics(rep)}], "templates": []}


def test_pipeline_zip_contains_one_report_per_completed_item(client_with_ubq, monkeypatch):
    c = client_with_ubq
    core = c.app.state.core
    monkeypatch.setattr(core.compute, "run_modeling", ReportModeller(fail={"b"}))
    fasta = f">a\n{YEAST_UB}\n>b\n{HUMAN_UB}\n>c\n{HUMAN_UB}\n"
    job = c.post("/api/pipeline", json={"fasta": fasta, "template_mode": "fixed", "parallel": 1,
                                        "templates": [{"id": "1UBQ", "chains": ["A"]}]}).json()
    assert wait_job(c, job["id"])["status"] == "completed"
    calls = []
    original = reports_service.ReportService.pipeline_item

    def spy(self, job_row, row):
        calls.append(row["name"])
        return original(self, job_row, row)

    monkeypatch.setattr(reports_service.ReportService, "pipeline_item", spy)
    r = c.get(f"/api/pipeline/{job['id']}/export.zip")
    assert r.status_code == 200
    with zipfile.ZipFile(io.BytesIO(r.content)) as zf:
        names = set(zf.namelist())
        assert {"report/a.html", "report/c.html", "modelli/a.pdb", "LEGGIMI.txt"} <= names
        assert "report/b.html" not in names and "report/non_generati.txt" not in names
        assert "report/" in zf.read("LEGGIMI.txt").decode() and "stampare in PDF" in zf.read("LEGGIMI.txt").decode()
        text = zf.read("report/a.html").decode()
    assert calls == ["a", "c"]  # uno per elemento completato, generati uno alla volta
    for sid in SECTIONS:
        assert f'id="{sid}"' in text, sid
    assert "data:image/png" not in text and rama_points(text) == 74
    assert "scelto dall&#x27;utente" in text and "96,1%" in text and "template fisso" in text
    assert "MODELLER 10.8" in text and "Pipeline di 3 sequenze · a" in text
    # un elemento con i file illeggibili non ferma l'esportazione: il motivo finisce in report/non_generati.txt
    (core.jobs.workdir(job["id"]) / "items" / "0002_c" / "models.json").write_text("{rotto")
    with zipfile.ZipFile(io.BytesIO(c.get(f"/api/pipeline/{job['id']}/export.zip").content)) as zf:
        names = set(zf.namelist())
        assert "report/a.html" in names and "report/c.html" not in names and "modelli/c.pdb" in names
        assert zf.read("report/non_generati.txt").decode().splitlines()[2].startswith("c: ")


def _pipeline_zip(c, job_id) -> dict[str, bytes]:
    r = c.get(f"/api/pipeline/{job_id}/export.zip")
    assert r.status_code == 200
    with zipfile.ZipFile(io.BytesIO(r.content)) as zf:
        names = zf.namelist()
        assert len(names) == len(set(names)), names  # nessuna voce ripetuta
        return {name: zf.read(name) for name in names}


def test_pipeline_zip_names_are_unique_and_match_the_models(client_with_ubq, monkeypatch):
    """"abc" e "abc-" sono elementi diversi: modello, rapporto di qualità e report di ognuno hanno lo stesso nome
    nell'archivio, senza voci ripetute (prima entrambi i report diventavano report/abc.html)."""
    c = client_with_ubq
    core = c.app.state.core
    monkeypatch.setattr(core.compute, "run_modeling", ReportModeller())
    fasta = f">abc\n{YEAST_UB}\n>abc-\n{HUMAN_UB}\n>-abc\n{HUMAN_UB}\n"
    job = c.post("/api/pipeline", json={"fasta": fasta, "template_mode": "fixed", "parallel": 1,
                                        "templates": [{"id": "1UBQ", "chains": ["A"]}]}).json()
    assert wait_job(c, job["id"])["status"] == "completed"
    files = _pipeline_zip(c, job["id"])
    for idx, name in enumerate(("abc", "abc-", "-abc")):
        assert {f"modelli/{name}.pdb", f"qualita/{name}.quality.json", f"report/{name}.html"} <= set(files)
        assert f"items/{idx:04d}_{name}/{name}.B99990001.pdb" in files[f"report/{name}.html"].decode()
    assert files["modelli/abc-.pdb"] == (core.jobs.workdir(job["id"]) / "items" / "0001_abc-" /
                                         "abc-.B99990001.pdb").read_bytes()


def test_pipeline_deduplicates_generated_names_too():
    from bioloom.services.pipeline import PipelineService

    fasta = "".join(f">{name}\n{HUMAN_UB}\n" for name in ("x", "x", "x_2", "x", "abc", "abc-"))
    names = [r.name for r in PipelineService._records({"fasta": fasta})]
    assert names == ["x", "x_2", "x_2_2", "x_3", "abc", "abc-"]


def test_pipeline_zip_of_an_old_job_with_repeated_names(client_with_ubq, monkeypatch):
    """Job di una versione precedente con nomi ripetuti ("x", "x", "x_2" → "x", "x_2", "x_2"): nello ZIP il secondo
    "x_2" prende il nome della sua cartella, e ogni report resta accanto al suo modello."""
    from bioloom.services.pipeline import PipelineService

    c = client_with_ubq
    core = c.app.state.core
    monkeypatch.setattr(core.compute, "run_modeling", ReportModeller())
    monkeypatch.setattr(PipelineService, "_records", staticmethod(lambda params: [
        sequences.SeqRecord("x", YEAST_UB), sequences.SeqRecord("x_2", HUMAN_UB), sequences.SeqRecord("x_2", HUMAN_UB)]))
    job = c.post("/api/pipeline", json={"fasta": ">x\nMKV\n", "template_mode": "fixed", "parallel": 1,
                                        "templates": [{"id": "1UBQ", "chains": ["A"]}]}).json()
    assert wait_job(c, job["id"])["status"] == "completed"
    files = _pipeline_zip(c, job["id"])
    for stem, folder in (("x", "0000_x"), ("x_2", "0001_x_2"), ("0002_x_2", "0002_x_2")):
        assert f"items/{folder}/" in files[f"report/{stem}.html"].decode()
        model = core.jobs.workdir(job["id"]) / "items" / folder / f"{folder[5:]}.B99990001.pdb"
        assert files[f"modelli/{stem}.pdb"] == model.read_bytes()
        assert f"qualita/{stem}.quality.json" in files


def test_pipeline_item_report_route(client_with_ubq, monkeypatch):
    c = client_with_ubq
    core = c.app.state.core
    monkeypatch.setattr(core.compute, "run_modeling", ReportModeller(fail={"b"}))
    job = c.post("/api/pipeline", json={"fasta": f">a\n{YEAST_UB}\n>b\n{HUMAN_UB}\n", "template_mode": "fixed",
                                        "parallel": 1, "templates": [{"id": "1UBQ", "chains": ["A"]}]}).json()
    assert wait_job(c, job["id"])["status"] == "completed"
    base = f"/api/jobs/{job['id']}/report"
    r = c.get(f"{base}/items/0000_a/a.B99990001.pdb")
    assert r.status_code == 200 and r.headers["content-disposition"] == \
        'attachment; filename="report_a.B99990001.html"'
    assert "Scelta del template" in r.text and rama_points(r.text) == 74
    assert c.get(f"{base}/items/0001_b/b.B99990001.pdb").status_code == 409  # elemento fallito
    assert c.get(f"{base}/items/0000_x/a.B99990001.pdb").status_code == 404  # nome diverso dall'elemento
    assert c.get(f"{base}/items/0000_a/altro.pdb").status_code == 404
    assert c.get(f"{base}/riepilogo.pdb").status_code == 404
    r = c.get(base)
    assert r.status_code == 400 and r.json()["error"]["code"] == "report_model_required"


def test_pipeline_item_report_while_the_pipeline_runs(client_with_ubq, monkeypatch):
    """Un elemento completato ha il suo report mentre la pipeline elabora ancora gli altri."""
    c = client_with_ubq
    core = c.app.state.core
    gate = threading.Event()

    class Held(ReportModeller):
        def __call__(self, sub, *, target, **kwargs):
            while target.name == "b" and not gate.is_set():
                sub.check()
                time.sleep(0.02)
            return super().__call__(sub, target=target, **kwargs)

    monkeypatch.setattr(core.compute, "run_modeling", Held())
    job = c.post("/api/pipeline", json={"fasta": f">a\n{YEAST_UB}\n>b\n{HUMAN_UB}\n", "template_mode": "fixed",
                                        "parallel": 1, "templates": [{"id": "1UBQ", "chains": ["A"]}]}).json()
    try:
        deadline = time.time() + 60
        while c.get(f"/api/pipeline/{job['id']}/items/0").json()["status"] != "completed":
            assert time.time() < deadline, "elemento a non completato"
            time.sleep(0.05)
        assert c.get(f"/api/jobs/{job['id']}/status").json()["status"] == "running"
        r = c.get(f"/api/jobs/{job['id']}/report/items/0000_a/a.B99990001.pdb")
        assert r.status_code == 200 and rama_points(r.text) == 74
        # l'elemento in lavorazione non ha ancora un report
        assert c.get(f"/api/jobs/{job['id']}/report/items/0001_b/b.B99990001.pdb").status_code == 409
    finally:
        gate.set()
    assert wait_job(c, job["id"])["status"] == "completed"


# ---------------------------------------------------------------------------------------------- MODELLER reale
@pytest.mark.modeller
@requires_modeller
def test_report_of_a_real_model(client_with_ubq):
    c = client_with_ubq
    job = c.post("/api/model", json={"target": {"name": "yeast_ub", "sequence": YEAST_UB},
                                     "templates": [{"id": "1UBQ", "chains": ["A"]}],
                                     "options": {"models": 2, "refinement": "very_fast"}}).json()
    done = wait_job(c, job["id"], timeout=600)
    assert done["status"] == "completed", done.get("error")
    best = done["result"]["best_model"]
    image = make_png(20, 10)
    r = c.post(f"/api/jobs/{job['id']}/report/{best}", json={"image_png_base64": b64(image)})
    assert r.status_code == 200
    text = r.text
    for sid in SECTIONS:
        assert f'id="{sid}"' in text, sid
    assert re.search(r"MODELLER \d+\.\d+", text) and "96,1%" in text and b64(image) in text
    rep = c.get(f"/api/jobs/{job['id']}/quality/{best}").json()
    assert rama_points(text) == len(rep["ramachandran"]["points"]) > 70
    assert flagged(text, "Energia DOPE per residuo (media mobile di MODELLER)") >= 1
    assert text.count('<tr class="') >= 2 and '<tr class="best chosen">' in text
