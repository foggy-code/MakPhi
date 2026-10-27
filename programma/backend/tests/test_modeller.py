"""Generazione degli script MODELLER (sempre) ed esecuzione reale (solo se MODELLER è installato)."""

import pytest

from makphi.core.errors import AppError
from makphi.engines import modeller
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


class _FakeProcessModel:
    """Modello finto con il comportamento di GA341 in MODELLER 10.8: il primo calcolo del processo (`process`, un dict
    condiviso dai modelli dello stesso processo) dà un valore diverso dai successivi."""

    def __init__(self, process, stable):
        self.process, self.stable = process, stable

    def assess_ga341(self):
        first = not self.process.get("used")
        self.process["used"] = True
        return [self.stable + 0.17 if first else self.stable]

    def get_model(self):  # anche selezione di atomi del modello (atmsel.get_model())
        return self


def _ga341_namespace():
    """Funzione di valutazione di GA341 come la eseguono uno script MODELLER o un worker (un processo nuovo)."""
    namespace = {"assess": type("assess", (), {"GA341": staticmethod(lambda atmsel: atmsel.get_model().assess_ga341())})}
    exec(modeller.GA341_FUNCTION, namespace)
    return namespace["ga341_stable"]


def test_ga341_does_not_depend_on_model_order_or_workers():
    """Il primo calcolo di GA341 di ogni processo si fa a vuoto: lo stesso modello ha lo stesso valore se è il primo di
    un worker nuovo o arriva dopo altri modelli, e un processo che lo ricalcola dopo l'altro dà lo stesso valore."""
    serial, fresh_1, fresh_2 = {}, {}, {}
    stable = _ga341_namespace()
    one_worker = [stable(_FakeProcessModel(serial, s))[0] for s in (0.05, 0.14, 0.17)]
    three_workers = [_ga341_namespace()(_FakeProcessModel(p, s))[0]
                     for p, s in ((fresh_1, 0.05), (fresh_2, 0.14), ({}, 0.17))]
    assert one_worker == three_workers == [0.05, 0.14, 0.17]
    assert stable(_FakeProcessModel(serial, 0.14))[0] == 0.14  # e di nuovo nello stesso processo


@pytest.mark.parametrize("parallel", [False, True])
def test_model_script_assesses_ga341_with_warm_up(parallel):
    script = modeller.build_model_script(["1UBQ_A"], "target", modeller.parse_options({"models": 3}), parallel=parallel)
    compile(script, "model.py", "exec")
    assert "assess_methods=(assess.DOPE, ga341_stable, assess.normalized_dope))" in script
    assert "assess.GA341," not in script
    # in parallelo i worker importano la funzione dal modulo della classe, come il processo principale
    assert ("from makphi_model import MakPhiModel, ga341_stable" in script) == parallel
    assert "def ga341_stable(atmsel):" in script


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
    assert "REMARK 999 MAKPHI" in pdb
