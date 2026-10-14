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
import sys
import threading
import time
import xml.etree.ElementTree as ET
import zipfile
import zlib
from dataclasses import asdict
from html.parser import HTMLParser

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
    assert '<tr class="best chosen">' in text
    assert "rapido (predefiniti di AutoModel: autosched.normal, refine.very_fast)" in text
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
                         {**data["templates"][0], "id": "AF-P69905-F1", "code": "AF-P69905-F1_A", "predicted": True,
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


NTFS_FORBIDDEN = pytest.mark.skipif(sys.platform == "win32", reason='" < > non sono ammessi nei nomi di file NTFS')


@pytest.mark.parametrize("evil, escaped", [
    pytest.param('y";a<b>.pdb', "y&quot;;a&lt;b&gt;.pdb", id="virgolette-minore-maggiore", marks=NTFS_FORBIDDEN),
    pytest.param("y';a&b.pdb", "y&#x27;;a&amp;b.pdb", id="apostrofo-e-commerciale"),  # nome valido anche su Windows
])
def test_download_name_is_safe_for_any_model_name(client_with_ubq, evil, escaped):
    c = client_with_ubq
    job = fake_model_job(c, files=(evil,))
    r = c.get(f"/api/jobs/{job['id']}/report/{evil}")
    assert r.status_code == 200
    assert r.headers["content-disposition"] == 'attachment; filename="report_y_a_b.html"'
    assert escaped in r.text and evil.removesuffix(".pdb") not in r.text


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


# ---------------------------------------------------------------------------------------------- job refine (simulato)
REFINED = "1UBQ_refined.pdb"
REFINE_SECTIONS = ("riepilogo", "bersaglio", "rifinitura", "qualita", "voto", "ramachandran", "clash", "avvisi",
                   "legenda", "riferimenti")


def _energies(total: float) -> dict:
    return {"bonds": 120.5, "angles": 400.2, "torsions": 2300.0, "nonbonded": total - 2820.7, "solvation": -2000.0,
            "total": total}


def fake_refine_job(client) -> dict:
    """Job "refine" con i file e il risultato che salverebbe services/refine.py (OpenMM simulato: coordinate di 1UBQ,
    energie e tempi plausibili)."""
    from bioloom.domain import refinement

    core = client.app.state.core

    def handler(ctx, p):
        modeller_like(ctx.workdir / "input.pdb", header="")
        modeller_like(ctx.workdir / REFINED, header="REMARK 999 BIOLOOM REFINED FROM 1UBQ OPENMM 8.2 AMBER ff14SB OBC2\n")
        before = quality.analyze(ctx.workdir / "input.pdb", None, with_dope=False)
        after = quality_with_dope(ctx.workdir / REFINED)
        (ctx.workdir / f"{REFINED}.quality.json").write_text(json.dumps(after))
        mb, ma = quality.summary_metrics(before), quality.summary_metrics(after)
        raw = {"before": _energies(71160.0), "after_minimization": _energies(-5500.0), "after_md": _energies(-4800.0),
               "final": _energies(-6000.0), "restraint_final": 12.5}
        return {"source": p["source"], "label": "1UBQ", "source_sha256": "0" * 64, "refined_file": REFINED,
                "input_file": "input.pdb", "protocol": refinement.protocol(0.5, False),
                "openmm": {"openmm": "8.2", "platform": "CPU", "threads": 3, "atoms": 1231, "nonbonded": "NoCutoff",
                           "restrained_atoms": 304, "md_steps": 250, "timings": {"pdbfixer": 0.5}},
                "added": {"heavy_atoms": 0, "terminal_atoms": 1, "hydrogens": 629, "nonstandard": []},
                "input": {"excluded": ["ZN A101"], "modified": ["MSE A1"],
                          "breaks": [{"chain": "A", "after": "34", "before": "38"}], "hydrogens_removed": 0,
                          "residues": 76},
                "energy": {**refinement.energy_change(raw), "components": raw},
                "rmsd": {"atoms": 304, "rmsd": 0.214, "rmsd_no_fit": 0.25, "max_displacement": 0.81,
                         "max_atom": "CA A40"},
                "quality": {"before": mb, "after": ma}, "comparison": refinement.compare(mb, ma),
                "warnings": ["Avviso della rifinitura di prova"]}

    core.jobs.register("refine", handler)
    job = client.post("/api/refine", json={"source": {"type": "template", "id": "1UBQ"}, "md_ps": 0.5}).json()
    done = wait_job(client, job["id"])
    assert done["status"] == "completed", done["error"]
    return done


def test_refine_report(client_with_ubq):
    """Report del modello rifinito (prima rispondeva 400 report_unsupported): al posto di template, allineamento e
    parametri di MODELLER, la rifinitura con il confronto prima/dopo, l'energia per componente, l'RMSD, il protocollo
    di OpenMM e le fonti; la qualità è quella del modello rifinito."""
    c = client_with_ubq
    job = fake_refine_job(c)
    r = c.get(f"/api/jobs/{job['id']}/report/{REFINED}")
    assert r.status_code == 200, r.text
    assert r.headers["content-disposition"] == 'attachment; filename="report_1UBQ_refined.html"'
    text = r.text
    for sid in REFINE_SECTIONS:
        assert f'id="{sid}"' in text, sid
    for absent in ("template", "allineamento", "parametri", "modelli"):
        assert f'id="{absent}"' not in text, absent
    assert "Report del modello rifinito" in text and "Rifinitura energetica" in text and "Struttura rifinita" in text
    assert "1UBQ (rifinito)" in text and "OpenMM 8.2" in text and "PDBFixer" in text
    # confronto prima/dopo, energia (iniziale, dopo la minimizzazione, dopo la dinamica, finale) e RMSD
    assert "<th>Metrica</th>" in text and "Clashscore" in text and "Ramachandran favorite (%)" in text
    assert "Da 71160,0 a −6000,0 kJ/mol (Δ −77160,0 kJ/mol, diminuita)" in text
    assert "Dopo la dinamica" in text and "Solvatazione (OBC2)" in text and "12,50 kJ/mol" in text
    assert "RMSD 0,214 Å dopo la sovrapposizione" in text and "(CA A40)" in text
    # protocollo e preparazione
    assert "AMBER ff14SB" in text and "k = 10,0 kcal·mol⁻¹·Å⁻²" in text and "0,5 ps a 300 K" in text
    assert "629 idrogeni" in text and "ZN A101" in text and "MSE A1" in text and "A34/38" in text
    assert "Avviso della rifinitura di prova" in text and rama_points(text) == 74
    for ref in ("Eastman P. et al. (2017)", "Maier J.A.", "Onufriev A., Bashford D., Case D.A. (2004)",
                "Liu D.C., Nocedal J. (1989)", "Kabsch W. (1976)"):
        assert ref in text, ref
    assert "<dt>Energia del campo di forza</dt>" in text and "<dt>RMSD della catena principale</dt>" in text
    # solo il modello rifinito ha un report; il file va indicato
    assert c.get(f"/api/jobs/{job['id']}/report/input.pdb").status_code == 404
    r = c.get(f"/api/jobs/{job['id']}/report")
    assert r.status_code == 400 and r.json()["error"]["code"] == "report_model_required"


def test_quality_report_names_keep_the_model_number(client_with_ubq):
    """Report di qualità di due modelli dello stesso job: l'etichetta ("yeast.B99990002") è già senza estensione e il
    nome del file la conserva intera (prima entrambi diventavano report_qualita_yeast.html)."""
    c = client_with_ubq
    job = fake_model_job(c)
    names = []
    for model in (BEST, SECOND):
        q = c.post("/api/quality", json={"source": {"type": "job", "job_id": job["id"], "file": model}, "dope": False,
                                         "procheck": False, "prosa": False}).json()
        assert wait_job(c, q["id"])["status"] == "completed"
        names.append(c.get(f"/api/jobs/{q['id']}/report").headers["content-disposition"])
    assert names == ['attachment; filename="report_qualita_yeast.B99990001.html"',
                     'attachment; filename="report_qualita_yeast.B99990002.html"']
    # stessa regola per la scansione di alanina: si toglie solo l'estensione di un file di struttura
    stem = reports_service._label_stem
    assert stem("ubi_classica.B99990001") == "ubi_classica.B99990001" and stem("mio.modello") == "mio.modello"
    assert [stem(n) for n in ("1UBQ.pdb", "x.B99990001.PDB", "y.cif", "z.mmcif", "w.ent.gz", "dir/v.pdb")] == \
        ["1UBQ", "x.B99990001", "y", "z", "w", "v"]


def test_refine_report_without_quality_and_dynamics(ubq_quality):
    """Dati di render senza rapporto di qualità né dinamica: avviso senza «MODELLER», «nessuna» dinamica."""
    from bioloom.domain import refinement

    data = {"kind": "refine", "title": "Rifinitura · x", "job_id": "r1", "model": "x_refined.pdb",
            "tools": [("BioLoom", "4.1.0"), ("OpenMM", "8.2")], "target": {"name": "x (rifinito)", "sequence": "MQ"},
            "quality": None, "consensus": None, "warnings": [],
            "refine": {"protocol": refinement.protocol(0.0, False), "energy": {"before": 10.0, "after": 5.0,
                                                                              "delta": -5.0, "decreased": True},
                       "rmsd": {"atoms": 8, "rmsd": 0.1}, "comparison": [], "input": {"residues": 2}}}
    text = report.render(data)
    assert "il report riporta solo i risultati della rifinitura" in text and "di MODELLER" not in text
    assert "nessuna (solo minimizzazione)" in text and "nessun gruppo escluso" in text
    assert "<title>Report rifinitura · Rifinitura · x</title>" in text


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

    def spy(self, job_row, row, **kwargs):
        calls.append(row["name"])
        return original(self, job_row, row, **kwargs)

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


# ---------------------------------------------------------------------------------------------- struttura del documento
VOID = frozenset({"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "track", "wbr"})


class _StrictHTML(HTMLParser):
    """Controllo dell'HTML del report: ogni elemento non vuoto chiuso nell'ordine giusto, nessuna sezione dentro
    un'altra, id univoci."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.stack: list[str] = []
        self.ids: list[str] = []
        self.sections: list[str] = []
        self.errors: list[str] = []

    def _ids(self, attrs) -> None:
        self.ids += [v for k, v in attrs if k == "id"]

    def handle_starttag(self, tag, attrs):
        self._ids(attrs)
        if tag == "section":
            if "section" in self.stack:
                self.errors.append(f"sezione {dict(attrs).get('id')} dentro {self.stack}")
            self.sections.append(dict(attrs).get("id"))
        if tag not in VOID:
            self.stack.append(tag)

    def handle_startendtag(self, tag, attrs):
        self._ids(attrs)

    def handle_endtag(self, tag):
        if tag in VOID:
            return
        if not self.stack or self.stack[-1] != tag:
            self.errors.append(f"</{tag}> con aperti {self.stack[-3:]}")
            if tag in self.stack:
                while self.stack and self.stack.pop() != tag:
                    pass
            return
        self.stack.pop()


def _check_document(text: str) -> _StrictHTML:
    parser = _StrictHTML()
    parser.feed(text)
    parser.close()
    assert parser.errors == [], parser.errors[:5]
    assert parser.stack == [], parser.stack
    duplicated = sorted({i for i in parser.ids if parser.ids.count(i) > 1})
    assert duplicated == [], duplicated
    assert text.count("<section") == text.count("</section>") == len(parser.sections)
    return parser


def _site(**over) -> dict:
    base = {"site": "A:30", "label": "A:I30A", "wt": "I", "mut": "A", "rsa_wt": 0.04, "rsa_mut": 0.05, "core": True,
            "buried_wt": {"total": 160.0, "apolar": 130.0}, "buried_mut": {"total": 90.0, "apolar": 70.0},
            "buried_delta": {"total": -70.0, "apolar": -60.0}, "ss": "H", "ss_position": 7, "ss_length": 12,
            "rama": None, "blosum62": -1, "helix_propensity": -0.41,
            "interactions": {"wild_type": 1, "mutant": 0, "gained": [], "lost": [
                {"type": "hbond", "partner": "VAL A26", "role": "NH della catena principale", "atom": "N",
                 "inter_chain": True, "distance": 2.97}]},
            "clashes": {"wild_type": 0, "mutant": 0, "worst": None},
            "interface": {"delta": 40.0, "rsa_complex": 0.0, "lost_inter_chain": 1}}
    base.update(over)
    return base


def _mutation_result(energy: bool = True) -> dict:
    from bioloom.domain import mutations, refinement
    from bioloom.services.mutations import MutationService

    rows = [{"role": "wild_type", "dope": -7918.0, "local": -5576.3, "interchain": -120.0},
            {"role": "mutant", "dope": -7569.4, "local": -5227.7, "interchain": -60.0}]
    dope = mutations.dope_summary(rows[0], rows[1], 76, 32, rows)
    terms = ({"available": True, **mutations.energy_terms(-12393.2, -12370.3, -904.0, -953.7), "openmm": "8.2"}
             if energy else None)
    site = _site()
    metrics = {"grade_score": 98.9, "rama_favored": 95.9, "rama_outliers": 0.0, "clashscore": 1.66, "dope": -7918.0,
               "zdope": -2.67}
    return {"notation": "A:I30A", "assessment": mutations.classify([site], dope, terms), "sites": [site],
            "dope": dope, "energy": terms, "rmsd": {"all": 0.21, "outside": 0.0, "atoms": 304, "outside_atoms": 280},
            "comparison": refinement.compare(metrics, {**metrics, "clashscore": 3.33}),
            "protocol": MutationService._protocol(mutations.SEED, energy), "caveat": mutations.CAVEAT}


def _scan_result() -> dict:
    from bioloom.domain import mutations

    mut = _mutation_result(energy=False)
    row = {"label": "A:I30A", "mutation": {"chain": "A", "resnum": "30"}, "site": mut["sites"][0], "dope": mut["dope"],
           "energy": None, "assessment": mut["assessment"]}
    return {"rows": [row, {**row, "label": "A:L43A"}], "selection": {"detail": "2 residui indicati"},
            "skipped": [{"chain": "A", "resnum": "10", "resname": "GLY", "reason": "glicina"}],
            "summary": {"residues": 2, "skipped": 1, "counts": {"destabilizing": 2, "uncertain": 0, "neutral": 0},
                        "hotspots": 1},
            "hotspots": [{"label": "A:I30A", "level": "probabile", "text": "Hot spot probabile"}], "interface": True,
            "protocol": mut["protocol"], "caveat": mutations.CAVEAT}


def _prediction_summary() -> dict:
    plaus = {"status": "divergenze locali", "summary": "divergono in 31-37", "label": "ESMFold",
             "lddt_confident": 0.87, "confident_residues": 76, "pairs": 76,
             "regions": [{"chain": "A", "start": "31", "end": "37", "divergent": 7, "length": 7, "lddt_mean": 0.24,
                          "plddt_mean": 90.0, "confidence": "sicura", "consensus": ["DOPE"]}],
             "notes": ["nota"], "limits": "limiti"}
    return {"job_id": "p1", "finished_at": "2026-10-01T10:00:00+00:00", "predictor": "esmfold", "label": "ESMFold",
            "origin": {"service": "https://api.esmatlas.com/x", "scale": "0-1"}, "plddt": {"mean": 90.0},
            "chain": "A", "sequence": {"length": 76},
            "comparison": {"tm_score": 0.927, "lddt": 0.878, "gdt_ts": 95.07, "rmsd": 1.74}, "plausibility": plaus}


def _analysis_summary() -> dict:
    cys = {"chain": "A", "resnum": "22", "resname": "CYS"}
    return {"job_id": "a1", "finished_at": "2026-10-01T10:00:00+00:00",
            "disulfides": [{"a": cys, "b": {**cys, "resnum": "70"}, "distance": 2.04, "ca_distance": 5.5, "chi3": 92}],
            "interfaces": [{"chains": ["A", "B"], "area": 650.0, "counts": {"A": 18, "B": 17},
                            "interactions": {"hbonds": 6, "salt_bridges": 2}, "apolar_fraction": 0.58,
                            "residues": {"A": [cys], "B": [{**cys, "chain": "B"}]},
                            "plausibility": {"level": "compatibile", "text": "area tipica", "limits": "limiti"}}]}


def _complex_info() -> dict:
    return {"chains": [{"model_chain": "A", "template_chain": "A", "length": 76},
                       {"model_chain": "B", "template_chain": "B", "length": 76, "same_as": "A"}],
            "symmetry": {"applied": True, "groups": [["A", "B"]], "weight": 1.0},
            "assessment": {"template": "DIMER", "notes": ["nota"], "limits": "limiti",
                           "comparison": [{"model_chains": ["A", "B"], "template_chains": ["A", "B"],
                                           "area_model": 640.0, "area_template": 650.0, "conserved_percent": 88.0,
                                           "conserved": 15, "template_residues": 17, "fnat": 0.82, "irms": 0.9,
                                           "lrms": 1.8, "dockq": 0.79, "status": "alta",
                                           "text": "interfaccia riprodotta"}]}}


def _disordered_report(rep: dict) -> dict:
    """1UBQ con la coda dell'alfa-sinucleina sui residui 41-76: una regione disordinata predetta dalla sequenza."""
    out = json.loads(json.dumps(rep))
    for row, aa in zip(out["residues"][40:], "APQEGILEDMPVDPDNEAYEMPSEEGYQDYEPEAEEGAPQ"):
        row["aa"] = aa
    return out


def _report_case(name: str, rep: dict) -> dict:
    """Dati di render per ogni tipo di report e per le sezioni facoltative."""
    from bioloom.domain import refinement

    lig = quality.analyze(DATA / "ubq_zn.pdb", None, with_dope=False)
    base = model_data(rep)
    plain = {"templates": [], "alignment": None, "options": None, "models": []}
    if name == "model":
        return base
    if name == "model-ligands-complex-prediction":
        return {**base, "quality": {**rep, "ligands": lig["ligands"]}, "complex": _complex_info(),
                "prediction": _prediction_summary(), "analysis": _analysis_summary()}
    if name == "model-without-quality":
        return {**base, "quality": None, "consensus": None, "ligands": lig["ligands"]}
    if name == "loop":
        return {**base, "kind": "loop", "options": None, "templates": [], "alignment": None,
                "loop": {"source": "1UBQ", "start": "7:A", "end": "12:A", "models": 2, "level": "fast"}}
    if name == "pipeline":
        return {**base, "kind": "pipeline", "search": {"engine": "rcsb", "identity": 96.0, "coverage": 100.0,
                                                        "evalue": 1e-40, "twilight": False},
                "pipeline": {"template_mode": "auto"}}
    if name == "quality":
        return {**base, **plain, "kind": "quality", "structure": rep.get("structure"), "source": "template 1UBQ",
                "analysis": _analysis_summary(), "prediction": _prediction_summary()}
    if name == "quality-disorder":
        dis = _disordered_report(rep)
        return {**base, **plain, "kind": "quality", "quality": dis, "consensus": consensus.compute(dis)}
    if name == "refine":
        raw = {"before": {"total": 71160.0}, "after_minimization": {"total": -5500.0}, "final": {"total": -6000.0},
               "restraint_final": 12.5}
        return {**base, **plain, "kind": "refine",
                "refine": {"protocol": refinement.protocol(0.5, False),
                           "energy": {**refinement.energy_change(raw), "components": raw},
                           "rmsd": {"atoms": 304, "rmsd": 0.2}, "comparison": [], "input": {"residues": 76},
                           "openmm": {"openmm": "8.2", "platform": "CPU", "threads": 1}}}
    if name == "mutate":
        return {**base, **plain, "kind": "mutate", "mutation": _mutation_result()}
    if name == "alascan":
        return {**base, **plain, "kind": "alascan", "quality": None, "consensus": None, "scan": _scan_result()}
    raise AssertionError(name)


REPORT_CASES = ("model", "model-ligands-complex-prediction", "model-without-quality", "loop", "pipeline", "quality",
                "quality-disorder", "refine", "mutate", "alascan")


@pytest.mark.parametrize("name", REPORT_CASES)
def test_every_report_kind_is_well_formed(ubq_quality, name):
    """Ogni tipo di report (con e senza ligandi, complessi, predizione, analisi strutturale, disordine): sezioni
    bilanciate e mai annidate, id univoci, HTML ben formato (ogni elemento chiuso nell'ordine giusto)."""
    text = report.render(_report_case(name, ubq_quality))
    parser = _check_document(text)
    expected = {"model-ligands-complex-prediction": {"ligandi", "complesso", "predizione", "analisi"},
                "quality": {"analisi", "predizione"}, "refine": {"rifinitura"}, "mutate": {"mutagenesi"},
                "alascan": {"scansione"}, "loop": {"parametri"}}.get(name, set())
    assert expected <= set(parser.sections), (expected, parser.sections)
    if name == "quality-disorder":
        assert "Disordine predetto dalla sequenza" in text


def test_document_sections_cannot_stay_open():
    """Equilibrio delle sezioni come proprietà di _Doc: sezione aperta dentro un'altra, chiusa senza essere aperta,
    id ripetuto o documento finito con una sezione aperta sono errori di programmazione."""
    doc = report._Doc()
    doc.section("a", "A")
    with pytest.raises(report.ReportStructureError, match="ancora aperta"):
        doc.section("b", "B")
    with pytest.raises(report.ReportStructureError, match="ancora aperta"):
        doc.html()
    doc.end()
    with pytest.raises(report.ReportStructureError, match="senza una sezione aperta"):
        doc.end()
    with pytest.raises(report.ReportStructureError, match="ripetuta"):
        doc.section("a", "di nuovo A")
    assert doc.html() == '<section id="a"><h2>A</h2></section>'


# ---------------------------------------------------------------------------------------------- report completo
def _real_analysis(path=DATA / "ubq_dimer.pdb") -> dict:
    """Riepilogo di un'analisi strutturale vera (interazioni, interfacce del dimero, conservazione) come lo prepara
    services/analysis.report_summary."""
    from bioloom.domain import conservation, interactions, interfaces, structure
    from bioloom.services.analysis import report_summary

    model = structure.first_model(structure.load_structure(path, "dimero"))
    inter = interactions.analyze(model)
    third = HUMAN_UB[:30] + "A" * 10 + HUMAN_UB[40:]
    cons = conservation.compute(model, ["human", "yeast", "variant"], [HUMAN_UB, YEAST_UB, third], source="prova")
    result = {"source": {"type": "template", "id": "DIMER"}, "interactions": inter,
              "interfaces": interfaces.analyze(model, inter), "conservation": cons, "warnings": []}
    return report_summary({"id": "an1", "finished_at": "2026-10-02T10:00:00+00:00", "result": result})


def _real_pockets() -> dict:
    from bioloom.domain import pockets
    from bioloom.services.structure_tools import pockets_summary

    result = {**pockets.read_output(DATA / "fpocket_1ubq_out", "structure"), "params": {"program": "fpocket 4.2.3"},
              "warnings": []}
    return pockets_summary({"id": "pk1", "finished_at": "2026-10-02T10:00:00+00:00", "result": result})


def _fake_apbs(monkeypatch):
    """PDB2PQR e APBS simulati: PQR reale di 1UBQ e mappa sintetica (test_electrostatics._FakeTools)."""
    from bioloom.engines import tools

    monkeypatch.setattr(tools, "missing_message", lambda *_a, **_k: None)
    monkeypatch.setattr(tools, "find", lambda key: key)


def _real_electrostatics(tmp_path, monkeypatch) -> dict:
    from bioloom.engines import electrostatics as engine
    from bioloom.services.structure_tools import electrostatics_summary

    from .test_electrostatics import _FakeTools

    _fake_apbs(monkeypatch)
    result = engine.run(DATA / "1UBQ.pdb", _FakeTools(), tmp_path / "apbs")
    return electrostatics_summary({"id": "es1", "finished_at": "2026-10-02T10:00:00+00:00", "result": result})


def _labels(text: str) -> list[str]:
    return [s.get("aria-label") for s in svgs(text)]


def test_full_report_has_every_chart_and_table(ubq_quality, tmp_path, monkeypatch):
    """Report di un modello con tutto: punteggi dei modelli, tracce del consenso, profili per residuo (DOPE, RSA,
    B-factor), PROCHECK, interazioni, interfacce, conservazione, tasche, elettrostatica, predizione e la tabella per
    residuo; ogni grafico è SVG ben formato e le tabelle lunghe stanno in blocchi richiudibili."""
    from bioloom.domain import procheck

    rep = json.loads(json.dumps(ubq_quality))
    rep["procheck"] = procheck.parse_sum((DATA / "procheck_1ubq.sum").read_text())
    prediction = _prediction_summary()
    prediction["plausibility"]["residues"] = [
        {"chain": "A", "resnum": str(i + 1), "lddt": 0.2 if 30 <= i <= 36 else 0.9, "plddt": 90.0,
         "divergent": 30 <= i <= 36, "confident": True} for i in range(76)]
    analysis, pockets_info = _real_analysis(), _real_pockets()
    electro = _real_electrostatics(tmp_path, monkeypatch)
    text = report.render(model_data(rep, analysis=analysis, pockets=pockets_info, electrostatics=electro,
                                    prediction=prediction))
    parser = _check_document(text)
    assert {"modelli", "qualita", "analisi", "tasche", "elettrostatica", "predizione", "dati-residui"} <= \
        set(parser.sections)
    labels = _labels(text)
    for title in ("DOPE per modello (più basso è migliore)", "zDOPE per modello (sotto −1 simile a una struttura nativa)",
                  report.TRACKS_TITLE, "Superficie accessibile relativa (RSA, %)", "B-factor per residuo (Å²)",
                  "Composizione della struttura secondaria (% dei residui)",
                  "Ramachandran secondo PROCHECK (% dei residui non Gly e non Pro)",
                  "Conservazione per residuo (0–1)", "Druggability per tasca (≥ 0,5 probabilmente druggabile)",
                  "Volume per tasca (Å³)", "Potenziale di superficie per residuo (kT/e)",
                  "lDDT-Cα del modello rispetto alla predizione", "pLDDT della predizione (ESMFold)"):
        assert title in labels, title
    assert any(label.startswith("ΔSASA dei residui d'interfaccia · catene A – B") for label in labels)
    # tasche: una barra per tasca, la 2 sola druggabile; tabella con tutti i residui che la rivestono
    svg = next(s for s in svgs(text) if s.get("aria-label").startswith("Druggability per tasca"))
    bars = [r for r in svg.iter(f"{NS}rect") if r.get("class") == "bar"]
    assert len(bars) == 4 and [b.get("fill") for b in bars].count(report.BAR_COLOR["ok"]) == 1
    assert "MET A1, GLU A18, PRO A19, SER A57, ASN A60, ILE A61, GLN A62, LYS A63" in text
    # interazioni: un blocco per tipo con tutte le righe, conteggi come nel riepilogo
    s = analysis["summary"]
    block = re.search(rf"<summary>Legami idrogeno \({s['hbonds']}\)</summary>(.*?)</details>", text, re.S).group(1)
    assert block.count("<tr") == s["hbonds"] + 1
    assert f"<summary>Ponti salini ({s['salt_bridges']})</summary>" in text
    # tabella per residuo: 76 righe in un blocco richiudibile
    block = re.search(r"<summary>Tabella completa \(76 residui\)</summary>(.*?)</details>", text, re.S).group(1)
    assert block.count("<tr") == 77
    # elettrostatica: tabella completa dei residui
    assert f"<summary>Tutti i residui ({len(electro['residues'])})</summary>" in text
    for ref in ("Le Guilloux V., Schmidtke P., Tufféry P. (2009)", "Baker N.A., Sept D.", "Capra J.A., Singh M.",
                "McDonald I.K., Thornton J.M."):
        assert ref in text, ref
    # in stampa i blocchi richiudibili si aprono
    assert "details::details-content{content-visibility:visible}" in text


def test_bar_chart_handles_negative_and_missing_values():
    svg = report.bars_svg([("a", -3.0, "accent"), ("b", None, "ok"), (EVIL, 2.0, "nessuno")], title=EVIL)
    root = ET.fromstring(svg)
    bars = [r for r in root.iter(f"{NS}rect") if r.get("class") == "bar"]
    assert len(bars) == 2 and bars[1].get("fill") == report.BAR_COLOR["accent"]  # colore sconosciuto: predefinito
    assert "<script>" not in svg and root.get("aria-label") == EVIL
    assert report.bars_svg([("a", None, "ok")], title="vuoto") == ""
    assert report.stack_svg([("a", 0, "#000000")], title="vuoto") == ""


def test_consensus_tracks_mark_regions(ubq_quality):
    cons = consensus.compute(ubq_quality, modeller={"ga341": 1.0, "zdope": -2.47})
    residues = ubq_quality["residues"]
    root = ET.fromstring(report.tracks_svg(residues, cons))
    regions = [r for r in root.iter(f"{NS}rect") if r.get("class") == "region"]
    assert len(regions) == len(cons["regions"])
    assert report.tracks_svg(residues[:10], cons) == ""  # residui non allineati alle segnalazioni


def test_report_route_includes_analysis_pockets_and_electrostatics(client_with_ubq, monkeypatch):
    """Dalla rotta: l'analisi strutturale, le tasche e l'elettrostatica più recenti sulla stessa struttura entrano nel
    report dell'analisi di qualità (fpocket e APBS simulati con le uscite reali di 1UBQ)."""
    from bioloom.domain import pockets
    from bioloom.engines import electrostatics as es_engine
    from bioloom.engines import pockets as pk_engine

    from .test_electrostatics import _FakeTools

    c = client_with_ubq
    source = {"type": "template", "id": "1UBQ"}
    _fake_apbs(monkeypatch)
    monkeypatch.setattr(pk_engine, "run", lambda _path, _ctx, _work: {
        **pockets.read_output(DATA / "fpocket_1ubq_out", "structure"), "params": {}, "warnings": []})
    real = es_engine.run
    monkeypatch.setattr(es_engine, "run", lambda path, _ctx, work, **kw: real(
        path, _FakeTools(), work, **{k: v for k, v in kw.items() if k != "progress"}))
    qjob = c.post("/api/quality", json={"source": source, "dope": False, "procheck": False, "prosa": False}).json()
    assert wait_job(c, qjob["id"])["status"] == "completed"
    text = c.get(f"/api/jobs/{qjob['id']}/report").text
    assert 'id="tasche"' not in text and 'id="elettrostatica"' not in text
    for path in ("/api/analysis", "/api/pockets", "/api/electrostatics"):
        done = wait_job(c, c.post(path, json={"source": source}).json()["id"])
        assert done["status"] == "completed", done.get("error")
    text = c.get(f"/api/jobs/{qjob['id']}/report").text
    _check_document(text)
    for sid in ("analisi", "interazioni", "tasche", "elettrostatica", "dati-residui"):
        assert f'id="{sid}"' in text, sid
    assert "Druggability per tasca (≥ 0,5 probabilmente druggabile)" in _labels(text)
