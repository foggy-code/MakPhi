"""Generazione degli script MODELLER (sempre) ed esecuzione reale (solo se MODELLER è installato)."""

import pytest

from bioloom.core.errors import AppError
from bioloom.engines import modeller
from tests.conftest import YEAST_UB, requires_modeller, wait_job


def test_scripts_are_valid_python():
    templates = [{"code": "1UBQ_A", "file": "1UBQ_A.pdb", "chains": ["A"]},
                 {"code": "2XYZ_B", "file": "2XYZ_B.pdb", "chains": ["B"]}]
    compile(modeller.build_alignment_script(templates, "target"), "align.py", "exec")
    compile(modeller.build_alignment_script(templates[:1], "target"), "align.py", "exec")
    opts = modeller.parse_options({
        "models": 3, "refinement": "thorough", "hetatm": True,
        "loop": {"enabled": True, "models": 2, "regions": [{"start": "5:A", "end": "10:A"}]},
        "restraints": [{"type": "helix", "start": "20:A", "end": "30:A"},
                       {"type": "distance", "atom1": "CA:3:A", "atom2": "CA:40:A", "mean": 10, "stdev": 0.5}],
    })
    script = modeller.build_model_script(["1UBQ_A", "2XYZ_B"], "target", opts)
    compile(script, "model.py", "exec")
    assert "DOPELoopModel" in script and "secondary_structure.Alpha" in script and "env.io.hetatm = True" in script
    compile(modeller.build_loop_script("m.pdb", "m", "5:A", "10:A", 3, "fast"), "loop.py", "exec")


REFINEMENT_LINES = {
    "very_fast": ["a.very_fast()"],
    "fast": ["a.library_schedule = autosched.normal", "a.max_var_iterations = 200", "a.md_level = refine.very_fast"],
    "normal": ["a.library_schedule = autosched.normal", "a.max_var_iterations = 200", "a.md_level = refine.fast"],
    "thorough": ["a.library_schedule = autosched.slow", "a.max_var_iterations = 300", "a.md_level = refine.slow",
                 "a.repeat_optimization = 2"],
}


@pytest.mark.parametrize("level", list(REFINEMENT_LINES))
def test_refinement_levels_write_distinct_settings(level):
    """Ogni livello scrive le sue impostazioni di AutoModel (engines/modeller.REFINEMENT_SCRIPT): "normal" non coincide
    più con "fast" (dinamica refine.fast invece di refine.very_fast) e "thorough" lascia max_molpdf al predefinito."""
    script = modeller.build_model_script(["1UBQ_A"], "target", modeller.parse_options({"refinement": level}))
    compile(script, "model.py", "exec")
    lines = script.splitlines()
    for line in REFINEMENT_LINES[level]:
        assert line in lines, line
    others = {line for other, rows in REFINEMENT_LINES.items() if other != level for line in rows}
    assert not [line for line in others - set(REFINEMENT_LINES[level]) if line in lines]
    assert "max_molpdf" not in script


def test_refinement_default_is_the_modeller_default():
    """Senza livello: "fast", i predefiniti di AutoModel (come l'interfaccia); i quattro script sono diversi."""
    assert modeller.parse_options({}).refinement == "fast" and modeller.ModelOptions().refinement == "fast"
    scripts = {modeller.build_model_script(["1UBQ_A"], "t", modeller.parse_options({"refinement": level}))
               for level in modeller.REFINEMENTS}
    assert len(scripts) == len(modeller.REFINEMENTS)


@pytest.mark.parametrize("raw", [
    {"models": 0}, {"refinement": "ultra"}, {"seed": 5},
    {"restraints": [{"type": "helix", "start": "1'); import os; ('", "end": "5:A"}]},
    {"restraints": [{"type": "distance", "atom1": "CA:1:A", "atom2": "CA:2:A", "mean": 500}]},
    {"loop": {"enabled": True, "regions": [{"start": "a", "end": "b"}]}},
])
def test_invalid_options_rejected(raw):
    with pytest.raises(AppError):
        modeller.parse_options(raw)


def test_template_preparation(tmp_path, ubq_path):
    info = modeller.prepare_template(ubq_path, modeller.TemplateSpec("1UBQ", ["A"]), tmp_path)
    assert info["code"] == "1UBQ_A" and info["first"] == "1" and info["last"] == "76"
    text = (tmp_path / "1UBQ_A.pdb").read_text()
    assert "HOH" not in text and text.count(" CA ") == 76
    with pytest.raises(AppError):
        modeller.prepare_template(ubq_path, modeller.TemplateSpec("1UBQ", ["Z"]), tmp_path)


@pytest.mark.modeller
@requires_modeller
def test_end_to_end_modeling(client_with_ubq):
    c = client_with_ubq
    job = c.post("/api/model", json={"target": {"name": "yeast_ub", "sequence": YEAST_UB},
                                     "templates": [{"id": "1UBQ", "chains": ["A"]}],
                                     "options": {"models": 2, "refinement": "very_fast"}}).json()
    done = wait_job(c, job["id"], timeout=600)
    assert done["status"] == "completed", done.get("error")
    res = done["result"]
    assert len(res["models"]) == 2 and sum(m["best"] for m in res["models"]) == 1
    best = next(m for m in res["models"] if m["best"])
    assert best["dope"] < -6000 and best["quality"]["grade"] in ("A", "B")
    # a.very_fast() non perturba le coordinate iniziali: i due modelli sono uguali, e il risultato lo dice
    assert res["models"][0]["dope"] == res["models"][1]["dope"]
    assert any("i 2 modelli sono identici" in w for w in res["warnings"])
    report = c.get(f"/api/jobs/{job['id']}/quality/{best['file']}").json()
    assert report["dope"]["profile"] and report["residues"][0]["dope"] is not None
    # consenso del modello: GA341 di MODELLER già nel rapporto salvato, e la rotta lo ricalcola uguale
    methods = {m["key"]: m for m in report["consensus"]["methods"]}
    assert {"grade", "zdope", "ga341"} <= set(methods) and methods["ga341"]["raw"] == pytest.approx(best["ga341"])
    assert report["structure"]["bfactor_kind"] == "bfactor"
    again = c.get(f"/api/jobs/{job['id']}/consensus/{best['file']}").json()
    assert again["score"] == report["consensus"]["score"] and again["inputs"]["modeller"]["ga341"] == best["ga341"]
    pml =c.get(f"/api/jobs/{job['id']}/pymol/{best['file']}").text
    assert "cmd.read_pdbstr" in pml
    pdb = c.get(f"/api/jobs/{job['id']}/files/{best['file']}").text
    assert "REMARK 999 BIOLOOM" in pdb
