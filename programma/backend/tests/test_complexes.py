"""Complessi e omo-oligomeri (domain/complexes.py, F5.3): bersaglio a più catene, allineamento per catena, PIR a più
catene, simmetria, confronto delle interfacce con il template (DockQ), report e, con MODELLER, l'omodimero
dell'ubiquitina di lievito su tests/data/ubq_dimer.pdb."""

from __future__ import annotations

import time
from pathlib import Path

import numpy as np
import pytest

from bioloom.core.errors import AppError
from bioloom.domain import complexes, geometry, ligands, report, sequences, structure
from bioloom.domain.sequences import PirEntry
from bioloom.engines import modeller
from bioloom.services.compute import ComputeService
from tests.conftest import DATA, HUMAN_UB, YEAST_UB, requires_modeller, wait_job


def _load(path: Path, name: str = "s"):
    return structure.first_model(structure.load_structure(path, name))


def _dimer_variant(tmp_path: Path, name: str, *, shift=(0.0, 0.0, 0.0), chains=("A", "B"), renumber: int = 0,
                   rot: np.ndarray | None = None, trans=(0.0, 0.0, 0.0)) -> Path:
    """ubq_dimer.pdb con la catena B spostata di `shift`, catene rinominate, numeri spostati di `renumber` e tutto
    ruotato e traslato (moto rigido) con `rot`, `trans`."""
    rot = np.eye(3) if rot is None else rot
    out = []
    for ln in (DATA / "ubq_dimer.pdb").read_text(encoding="utf-8").splitlines():
        if not ln.startswith("ATOM"):
            continue
        xyz = np.array([float(ln[30:38]), float(ln[38:46]), float(ln[46:54])])
        if ln[21] == "B":
            xyz = xyz + np.asarray(shift)
        xyz = rot @ xyz + np.asarray(trans)
        chain = chains[0] if ln[21] == "A" else chains[1]
        out.append(f"{ln[:21]}{chain}{int(ln[22:26]) + renumber:4d}{ln[26:30]}{xyz[0]:8.3f}{xyz[1]:8.3f}"
                   f"{xyz[2]:8.3f}{ln[54:]}")
    path = tmp_path / f"{name}.pdb"
    path.write_text("\n".join(out) + "\nEND\n", encoding="utf-8")
    return path


def _outward(distance: float) -> np.ndarray:
    """Spostamento della catena B lungo la direzione dal centro di A al centro di B."""
    model = _load(DATA / "ubq_dimer.pdb", "d")
    ca = {c.id: np.array([r["CA"].coord for r in c]) for c in model}
    direction = ca["B"].mean(axis=0) - ca["A"].mean(axis=0)
    return distance * direction / np.linalg.norm(direction)


def _entries(template_rows=(HUMAN_UB, HUMAN_UB), target_rows=(HUMAN_UB, HUMAN_UB), code="D_AB",
             fields=("1", "A", "76", "B")) -> list[PirEntry]:
    return [sequences.template_entry(code, "/".join(template_rows), *fields),
            sequences.target_entry("tgt", "/".join(target_rows))]


TEMPLATE = {"code": "D_AB", "chains": ["A", "B"]}


# ---------------------------------------------------------------------------------------------- bersaglio
def test_parse_target_with_same_as():
    target = complexes.parse_target({"name": "ub dimer", "chains": [
        {"template_chain": "A", "sequence": YEAST_UB.lower(), "name": "ub1"},
        {"template_chain": "B", "same_as": "A"}]})
    assert target["name"] == "ub"
    assert [(c.template_chain, c.name, c.sequence, c.same_as) for c in target["chains"]] == [
        ("A", "ub1", YEAST_UB, None), ("B", "ub1", YEAST_UB, "A")]


@pytest.mark.parametrize("raw, message, code", [
    ({"chains": [{"template_chain": "A", "sequence": YEAST_UB}]}, "almeno due catene", "invalid_complex"),
    ({"chains": [{"template_chain": c, "sequence": "MKV"} for c in "ABCDEFGHIJKLM"]}, "massimo 12",
     "complex_too_large"),
    ({"chains": [{"template_chain": "A", "sequence": YEAST_UB}, {"template_chain": "A", "sequence": YEAST_UB}]},
     "assegnata a più catene", "invalid_complex"),
    ({"chains": [{"template_chain": "A", "sequence": YEAST_UB}, {"template_chain": "B", "sequence": " "}]},
     "Manca la sequenza del bersaglio per la catena B", "invalid_complex"),
    ({"chains": [{"template_chain": "A", "sequence": YEAST_UB}, {"template_chain": "B", "same_as": "C"}]},
     "non è fra le catene", "invalid_complex"),
    ({"chains": [{"template_chain": "A", "same_as": "B"}, {"template_chain": "B", "same_as": "A"}]},
     "non ha una sequenza propria", "invalid_complex"),
    ({"chains": [{"template_chain": "AB", "sequence": YEAST_UB}, {"template_chain": "B", "sequence": YEAST_UB}]},
     "un carattere", "invalid_complex"),
    ({"chains": [{"template_chain": "A", "sequence": "M" * 1600}, {"template_chain": "B", "sequence": "M" * 1401}]},
     "3001 residui in tutto", "complex_too_large"),
    ({"chains": [{"template_chain": "A", "sequence": YEAST_UB}, {"template_chain": "B", "sequence": "12 34*"}]},
     "sequenza della catena B è vuota", "empty_sequence"),
])
def test_parse_target_errors(raw, message, code):
    with pytest.raises(AppError, match=message) as err:
        complexes.parse_target(raw)
    assert err.value.code == code


def test_residue_limit_is_inclusive():
    target = complexes.parse_target({"chains": [{"template_chain": "A", "sequence": "M" * 1500},
                                                {"template_chain": "B", "same_as": "A"}]})
    assert sum(len(c.sequence) for c in target["chains"]) == complexes.MAX_RESIDUES


def test_symmetry_groups():
    assert complexes.symmetry_groups(["AAA", "BBB", "A-AA", "CCC", "BBB"]) == [[0, 2], [1, 4]]
    assert complexes.symmetry_groups([YEAST_UB, HUMAN_UB]) == []


# ---------------------------------------------------------------------------------------------- allineamento
def _pair_pir(template_row: str, target_row: str, chain: str) -> str:
    return sequences.format_pir([PirEntry("D_AB", "structureX", ["1", chain, "+76", chain, "", "", "-1.00", "-1.00"],
                                          template_row), sequences.target_entry("tgt", target_row)])


def test_merge_chain_alignments():
    """Un allineamento per catena (come li scrive align2d) → PIR a più catene con i '/' nelle stesse colonne."""
    info = {"code": "D_AB", "chains": ["A", "B"], "first": "1", "last": "76", "sequence": f"{HUMAN_UB}/{HUMAN_UB}"}
    longer = YEAST_UB[:10] + "MKV" + YEAST_UB[10:]  # 3 residui del bersaglio senza template nella catena B
    texts = [_pair_pir(HUMAN_UB, YEAST_UB, "A"), _pair_pir(HUMAN_UB[:10] + "---" + HUMAN_UB[10:], longer, "B")]
    entries = complexes.merge_chain_alignments(texts, info, "tgt")
    tmpl, tgt = entries
    assert tmpl.fields[:4] == ["1", "A", "76", "B"] and tmpl.kind == "structureX" and tgt.kind == "sequence"
    assert complexes.break_columns(tmpl.sequence) == complexes.break_columns(tgt.sequence) == [76]
    assert complexes.target_chains(tgt) == [YEAST_UB, longer]
    assert tmpl.sequence == f"{HUMAN_UB}/{HUMAN_UB[:10]}---{HUMAN_UB[10:]}"
    report_ = sequences.validate_pir(sequences.format_pir(entries))
    assert report_["valid"] and [e["chains"] for e in report_["entries"]] == [2, 2]
    # la catena B del template non è intera nell'allineamento: errore chiaro
    wrong = [texts[0], _pair_pir(HUMAN_UB[:70], YEAST_UB[:70], "B")]
    with pytest.raises(AppError, match="non contiene la catena B intera"):
        complexes.merge_chain_alignments(wrong, info, "tgt")
    with pytest.raises(AppError, match="incompleti"):
        complexes.merge_chain_alignments(texts[:1], info, "tgt")


def test_check_chains_of_a_user_pir():
    templates = [{"code": "D_AB", "chains": ["A", "B"]}]
    assert complexes.check_chains(_entries(), templates, strict=True) == []
    # '/' in colonne diverse: errore nella modalità Complesso, avviso di validate_pir altrimenti
    shifted = _entries(template_rows=(HUMAN_UB + "-", HUMAN_UB), target_rows=(HUMAN_UB, "-" + HUMAN_UB))
    with pytest.raises(AppError, match="stesse colonne"):
        complexes.check_chains(shifted, templates, strict=True)
    assert complexes.check_chains(shifted, templates, strict=False) == []
    warnings = sequences.validate_pir(sequences.format_pir(shifted))["warnings"]
    assert any("colonne diverse da quelle del bersaglio" in w for w in warnings)
    # una catena del template senza il suo tratto: errore (Complesso) o avviso (PIR)
    merged = _entries(template_rows=(HUMAN_UB + HUMAN_UB,), target_rows=(YEAST_UB + YEAST_UB,))
    with pytest.raises(AppError, match="1 tratto separati da '/' ma il suo intervallo comprende 2 catene"):
        complexes.check_chains(merged, templates, strict=True)
    assert "servono tanti tratti" in complexes.check_chains(merged, templates, strict=False)[0]
    # intervallo su una sola catena di un template a due catene: un tratto va bene
    single = _entries(template_rows=(HUMAN_UB,), target_rows=(YEAST_UB,), fields=("1", "A", "76", "A"))
    assert complexes.check_chains(single, templates, strict=True) == []


def test_ligand_columns_keep_the_chains_coherent():
    """Le colonne '.' dei ligandi (domain/ligands.pir_with_ligands) si aggiungono alla fine del tratto della loro catena
    in tutte le righe: i '/' restano nelle stesse colonne e la corrispondenza dei residui le salta."""
    dimer = {"code": "D_AB", "chains": ["A", "B"], "last": "76", "_chain_residues": {"A": 76, "B": 76},
             "_hetero": [("A", "101")], "ligand_groups": [{"resname": "ZN", "chain": "A", "resnum": "101"}]}
    out, _notes = ligands.pir_with_ligands(_entries(target_rows=(YEAST_UB, YEAST_UB)), [dimer])
    assert out[0].sequence == f"{HUMAN_UB}./{HUMAN_UB}" and out[1].sequence == f"{YEAST_UB}./{YEAST_UB}"
    assert complexes.check_chains(out, [dimer], strict=True) == []
    assert complexes.target_chains(out[1]) == [YEAST_UB, YEAST_UB]
    template = _load(DATA / "ubq_dimer.pdb", "t")
    same = [PirEntry(out[0].code, out[0].kind, out[0].fields, f"{HUMAN_UB}./{HUMAN_UB}"),
            PirEntry(out[1].code, out[1].kind, out[1].fields, f"{HUMAN_UB}./{HUMAN_UB}")]
    (c,) = complexes.assess(template, template, same, TEMPLATE)["comparison"]
    assert c["dockq"] == 1.0 and c["conserved_percent"] == 100.0


def test_model_script_with_symmetry_and_chain_names():
    opts = modeller.parse_options({"models": 2, "symmetry": True, "symmetry_weight": 0.5,
                                   "restraints": [{"type": "helix", "start": "23:A", "end": "34:A"}]})
    script = modeller.build_model_script(["D_AB"], "tgt", opts, symmetry=[[0, 1, 2]], chain_names=["A", "B", "C"])
    compile(script, "model.py", "exec")
    assert "self.rename_segments(segment_ids=['A', 'B', 'C'], renumber_residues=[1, 1, 1])" in script
    assert script.count("rsr.symmetry.append(Symmetry(s1, s2, 0.5))") == 2  # B e C vincolate ad A
    assert "Selection(self.chains[2]).only_std_residues().only_atom_types('CA')" in script
    assert "secondary_structure.Alpha" in script
    plain = modeller.build_model_script(["D_AB"], "tgt", modeller.parse_options({}))
    assert "rename_segments" not in plain and "symmetry" not in plain
    with pytest.raises(AppError, match="Nomi delle catene"):
        modeller.build_model_script(["D_AB"], "tgt", opts, chain_names=["A", "A"])
    align = modeller.build_complex_alignment_script({"code": "D_BA", "file": "D_BA.pdb", "chains": ["B", "A"]}, "tgt")
    compile(align, "align.py", "exec")
    assert "model_segment=('FIRST:B', 'LAST:B')" in align and "target_chain_2.ali" in align


@pytest.mark.parametrize("weight", [0, -1, 11, "x"])
def test_symmetry_weight_is_validated(weight):
    with pytest.raises(AppError, match="peso delle restrizioni di simmetria"):
        modeller.parse_options({"symmetry": True, "symmetry_weight": weight})


def test_prepared_template_keeps_the_target_chain_order(tmp_path):
    spec = modeller.TemplateSpec("DIMER", ["B", "A"], keep_order=True)
    info = modeller.prepare_template(DATA / "ubq_dimer.pdb", spec, tmp_path)
    assert info["code"] == "DIMER_BA" and info["chains"] == ["B", "A"]
    assert [c.id for c in _load(tmp_path / info["file"])] == ["B", "A"]
    default = modeller.prepare_template(DATA / "ubq_dimer.pdb", modeller.TemplateSpec("DIMER", ["B", "A"]), tmp_path)
    assert default["chains"] == ["A", "B"]  # senza la modalità Complesso: ordine del file, come prima


def test_complex_plan_names_and_symmetry():
    """Nomi delle catene del modello dal template principale; simmetria sulle catene con la stessa sequenza."""
    opts = modeller.parse_options({"symmetry": True})
    templates = [{"code": "D_BA", "chains": ["B", "A"]}]
    target = complexes.parse_target({"chains": [{"template_chain": "B", "sequence": YEAST_UB},
                                                {"template_chain": "A", "same_as": "B"}]})
    entries = _entries(target_rows=(YEAST_UB, YEAST_UB), code="D_BA", fields=("1", "B", "76", "A"))
    plan = ComputeService._complex_plan(entries, templates, opts, target)
    assert plan["names"] == ["B", "A"] and plan["groups"] == [[0, 1]] and plan["template"] == "D_BA"
    assert plan["symmetry"]["applied"] and plan["symmetry"]["groups"] == [["B", "A"]]
    assert [(c["model_chain"], c["template_chain"], c["same_as"]) for c in plan["chains"]] == [
        ("B", "B", None), ("A", "A", "B")]
    # eterodimero: simmetria richiesta ma nessuna coppia uguale → avviso, nessuna restrizione
    hetero = _entries(target_rows=(YEAST_UB, HUMAN_UB), code="D_BA", fields=("1", "B", "76", "A"))
    plan = ComputeService._complex_plan(hetero, templates, opts, None)
    assert plan["groups"] == [] and any("nessuna coppia di catene" in w for w in plan["warnings"])
    # bersaglio a una catena: nulla cambia (niente rinomina)
    single = [sequences.template_entry("U_A", HUMAN_UB, "1", "A", "76", "A"), sequences.target_entry("t", YEAST_UB)]
    plan = ComputeService._complex_plan(single, [{"code": "U_A", "chains": ["A"]}], opts, None)
    assert plan["names"] is None and plan["chains"] == [] and "una sola catena" in plan["warnings"][0]


# ---------------------------------------------------------------------------------------------- interfacce
def test_identical_complex_reproduces_the_template_interface(tmp_path):
    """Il dimero contro sé stesso, con catene rinominate e numerazione spostata: la corrispondenza passa dal PIR, non
    da nomi o numeri. Valori di riferimento: 5 contatti fra residui entro 5 Å, 8 residui d'interfaccia (S57, D58,
    N60, Q62 per catena), area 122,9 Å², DockQ 1."""
    template = _load(DATA / "ubq_dimer.pdb", "t")
    model = _load(_dimer_variant(tmp_path, "same", chains=("C", "D"), renumber=100), "m")
    out = complexes.assess(model, template, _entries(), TEMPLATE)
    assert out["chains"] == [{"template": "A", "model": "C"}, {"template": "B", "model": "D"}]
    (c,) = out["comparison"]
    assert c["template_chains"] == ["A", "B"] and c["model_chains"] == ["C", "D"]
    assert (c["native_contacts"], c["reproduced_contacts"], c["fnat"], c["irms"], c["lrms"], c["dockq"]) == (
        5, 5, 1.0, 0.0, 0.0, 1.0)
    assert (c["template_residues"], c["conserved"], c["conserved_percent"]) == (8, 8, 100.0)
    assert c["area_template"] == pytest.approx(122.9, abs=0.1) and c["area_ratio"] == 100.0
    assert c["status"] == "alta" and c["tone"] == "ok" and out["status"] == "alta"
    assert sorted(c["conserved_residues"]) == sorted(f"{ch}{n}" for ch in "CD" for n in (157, 158, 160, 162))
    assert c["text"].startswith("Interfaccia del template riprodotta: DockQ 1,00 (alta")
    assert "residues" not in out["interfaces"] and out["interfaces"]["pairs"][0]["chains"] == ["C", "D"]
    assert complexes.brief(out) == {"status": "alta", "tone": "ok", "dockq": 1.0, "interfaces": 1}


def test_rigid_motion_does_not_change_the_comparison(tmp_path):
    angle = np.radians(37.0)
    rot = np.array([[np.cos(angle), -np.sin(angle), 0.0], [np.sin(angle), np.cos(angle), 0.0], [0.0, 0.0, 1.0]])
    template = _load(DATA / "ubq_dimer.pdb", "t")
    model = _load(_dimer_variant(tmp_path, "moved", rot=rot, trans=(12.0, -5.0, 30.0)), "m")
    (c,) = complexes.assess(model, template, _entries(), TEMPLATE)["comparison"]
    assert c["dockq"] == pytest.approx(1.0, abs=1e-3) and c["lrms"] == pytest.approx(0.0, abs=0.01)


def test_shifted_chain_lowers_dockq(tmp_path):
    """Catena B spostata di 3 Å: LRMS = 3 Å (la catena A coincide), un contatto su 5 perso; DockQ secondo la formula di
    Basu & Wallner 2016 → classe media."""
    template = _load(DATA / "ubq_dimer.pdb", "t")
    model = _load(_dimer_variant(tmp_path, "shift3", shift=(3.0, 0.0, 0.0)), "m")
    (c,) = complexes.assess(model, template, _entries(), TEMPLATE)["comparison"]
    assert c["lrms"] == pytest.approx(3.0, abs=0.01) and c["fnat"] == 0.8 and c["irms"] == pytest.approx(1.37, abs=0.01)
    expected = (0.8 + 1 / (1 + (c["irms"] / 1.5) ** 2) + 1 / (1 + (3.0 / 8.5) ** 2)) / 3
    assert c["dockq"] == pytest.approx(expected, abs=2e-3) and c["dockq_class"] == "media"
    assert c["receptor"] == "A" and c["ligand"] == "B"  # stessa lunghezza: il ricettore è la prima catena


def test_chains_no_longer_in_contact(tmp_path):
    template = _load(DATA / "ubq_dimer.pdb", "t")
    model = _load(_dimer_variant(tmp_path, "far", shift=_outward(30.0)), "m")
    out = complexes.assess(model, template, _entries(), TEMPLATE)
    (c,) = out["comparison"]
    assert c["area_model"] == 0.0 and c["fnat"] == 0.0 and c["conserved"] == 0
    assert c["lrms"] == pytest.approx(30.0, abs=0.01) and c["status"] == "non riprodotta" and c["tone"] == "err"
    assert "non sono più a contatto" in c["text"] and out["status"] == "non riprodotta"


def test_new_interface_absent_from_the_template(tmp_path):
    template = _load(_dimer_variant(tmp_path, "apart", shift=_outward(30.0)), "t")
    model = _load(DATA / "ubq_dimer.pdb", "m")
    out = complexes.assess(model, template, _entries(), TEMPLATE)
    (c,) = out["comparison"]
    assert c["status"] == "nuova" and c["template_chains"] is None and "assente nel template" in c["text"]
    assert any("non ha interfacce" in n for n in out["notes"]) and out["status"] == "non valutabile"


def _trimer(tmp_path: Path) -> Path:
    """ubq_dimer.pdb più una copia della catena B spostata di 6 Å come catena C, a contatto con le altre due."""
    rows = [ln for ln in (DATA / "ubq_dimer.pdb").read_text(encoding="utf-8").splitlines() if ln.startswith("ATOM")]
    extra = [f"{ln[:21]}C{ln[22:30]}{float(ln[30:38]) + 6.0:8.3f}{ln[38:]}" for ln in rows if ln[21] == "B"]
    path = tmp_path / "trimer.pdb"
    path.write_text("\n".join(rows + extra) + "\nEND\n", encoding="utf-8")
    return path


def test_template_chains_outside_the_pir_are_not_compared(tmp_path):
    """Copia preparata con le catene A, B e C (C a contatto) e un PIR da A a B, come con un template scelto con tre
    catene e un PIR dell'utente per due: le coppie con C non sono modellate e non si confrontano (prima la catena del
    modello di C era None e il riepilogo sollevava TypeError, facendo fallire il job)."""
    template = _load(_trimer(tmp_path), "t")
    model = _load(DATA / "ubq_dimer.pdb", "m")
    out = complexes.assess(model, template, _entries(), {"code": "D_AB", "chains": ["A", "B", "C"]})
    with_c = ", ".join("–".join(p["chains"]) for p in out["template_interfaces"]["pairs"] if "C" in p["chains"])
    assert with_c  # C tocca A o B nel template
    assert [c["template_chains"] for c in out["comparison"]] == [["A", "B"]]
    assert out["comparison"][0]["model_chains"] == ["A", "B"] and out["status"] == "alta"
    assert out["chains"] == [{"template": "A", "model": "A"}, {"template": "B", "model": "B"}]
    assert any("fuori dall'intervallo del PIR" in n and f"({with_c})" in n for n in out["notes"])


def test_chain_of_the_span_aligned_only_to_gaps(tmp_path):
    """Catena B del template nell'intervallo ma allineata solo a gap: nessuna catena del modello (None), il confronto
    non è valutabile e il riepilogo lo scrive con «?»."""
    template = _load(DATA / "ubq_dimer.pdb", "t")
    model = _load(DATA / "ubq_dimer.pdb", "m")
    out = complexes.assess(model, template, _entries(target_rows=(HUMAN_UB, "-" * 76)), TEMPLATE)
    first = out["comparison"][0]
    assert first["template_chains"] == ["A", "B"] and first["model_chains"] == ["A", None]
    assert first["dockq"] is None and "A–?:" in out["summary"]


def test_unexpected_interface_error_does_not_fail_the_modeling(tmp_path, monkeypatch):
    """Un errore inatteso dell'analisi delle interfacce di un modello diventa una riga del log (la funzione restituisce
    None), come promette `_complex_assessor`; l'annullamento del job invece passa."""
    from bioloom.core.jobs import JobCancelled

    (tmp_path / "D_AB.pdb").write_bytes((DATA / "ubq_dimer.pdb").read_bytes())
    logs: list[str] = []

    class Ctx:
        def log(self, message: str) -> None:
            logs.append(message)

        def check(self) -> None:
            pass

    check = ComputeService._complex_assessor(Ctx(), tmp_path, _entries(),
                                             [{"code": "D_AB", "file": "D_AB.pdb", "chains": ["A", "B"]}],
                                             {"template": "D_AB"})

    def broken(*_args, **_kwargs):
        raise TypeError("sequence item 1: expected str instance, NoneType found")

    monkeypatch.setattr(complexes, "assess", broken)
    assert check("D_AB.pdb", logs.append, lambda: None) is None
    assert any("non riuscita" in m and "TypeError" in m for m in logs)

    def cancelled(*_args, **_kwargs):
        raise JobCancelled()

    monkeypatch.setattr(complexes, "assess", cancelled)
    with pytest.raises(JobCancelled):
        check("D_AB.pdb", logs.append, lambda: None)


def test_alignment_that_does_not_match_the_model(tmp_path):
    template = _load(DATA / "ubq_dimer.pdb", "t")
    wrong = _entries(target_rows=("W" * 76, "W" * 76))
    out = complexes.assess(template, template, wrong, TEMPLATE)
    assert out["status"] == "non valutabile" and out["comparison"] == []
    assert "non corrisponde" in out["summary"]


# ---------------------------------------------------------------------------------------------- report
def _complex_data(tmp_path) -> dict:
    template = _load(DATA / "ubq_dimer.pdb", "t")
    model = _load(_dimer_variant(tmp_path, "shift3", shift=(3.0, 0.0, 0.0)), "m")
    assessment = complexes.assess(model, template, _entries(), TEMPLATE)
    return {"chains": [{"model_chain": "A", "template_chain": "A", "length": 76, "name": "ub<1>", "same_as": None},
                       {"model_chain": "B", "template_chain": "B", "length": 76, "name": "ub<1>", "same_as": "A"}],
            "symmetry": {"requested": True, "applied": True, "weight": 1.0, "groups": [["A", "B"]]},
            "template": "D_AB", "assessment": assessment}


def test_report_section_of_the_complex(tmp_path):
    data = {"kind": "model", "title": "Dimero", "job_id": "j1", "model": "tgt.B99990001.pdb",
            "options": {"models": 1, "refinement": "fast", "symmetry": True, "symmetry_weight": 1.0},
            "complex": _complex_data(tmp_path)}
    html = report.render(data)
    assert '<section id="complesso">' in html and "Interfacce del complesso" in html
    assert html.count("<section") == html.count("</section>")  # sezioni bilanciate
    assert "DockQ" in html and "0,74" in html and "media" in html and "ub&lt;1&gt;" in html and "ub<1>" not in html
    assert "stessa sequenza della catena A" in html and "A = B" in html and "Simmetria fra catene" in html
    assert "Basu S., Wallner B. (2016)" in html and "Méndez R." in html
    # modello senza analisi: la sezione lo dice, sempre bilanciata
    data["complex"] = {**data["complex"], "assessment": None}
    html = report.render(data)
    assert "Interfacce non analizzate" in html and html.count("<section") == html.count("</section>")
    # modello a una catena: nessuna sezione
    assert '<section id="complesso">' not in report.render({"kind": "model", "job_id": "j2"})


# ---------------------------------------------------------------------------------------------- rotte (validazione)
def _complex_body(**overrides) -> dict:
    body = {"templates": [{"id": "UBQD", "chains": []}], "options": {"models": 1, "refinement": "very_fast"},
            "complex": {"name": "ubd", "chains": [{"template_chain": "A", "sequence": YEAST_UB},
                                                  {"template_chain": "B", "same_as": "A"}]}}
    body.update(overrides)
    return body


@pytest.mark.parametrize("change, code", [
    ({"complex": {"chains": [{"template_chain": "A", "sequence": YEAST_UB}]}}, "invalid_complex"),
    ({"complex": {"chains": [{"template_chain": c, "sequence": "MKV"} for c in "ABCDEFGHIJKLM"]}},
     "complex_too_large"),
    ({"complex": {"chains": [{"template_chain": "A", "sequence": YEAST_UB}, {"template_chain": "B"}]}},
     "invalid_complex"),
    ({"templates": [{"id": "UBQD", "chains": []}, {"id": "1UBQ", "chains": ["A"]}]}, "invalid_complex"),
    ({"options": {"symmetry": True, "symmetry_weight": 0}}, "bad_request"),
])
def test_model_route_rejects_invalid_complexes(client, change, code):
    # l'allineamento non ha opzioni di modellazione: il caso del peso vale solo per /api/model
    routes = ("/api/model",) if "options" in change else ("/api/model", "/api/align2d")
    for route in routes:
        r = client.post(route, json=_complex_body(**change))
        assert r.status_code == 400, r.text
        assert r.json()["error"]["code"] == code
    assert client.get("/api/jobs").json() == []  # nessun job accodato


# ---------------------------------------------------------------------------------------------- MODELLER
def _upload_dimer(client) -> None:
    with open(DATA / "ubq_dimer.pdb", "rb") as fh:
        assert client.post("/api/templates/upload", files={"file": ("UBQD.pdb", fh)}).status_code == 200


def _chain_rmsd(path: Path) -> float:
    """RMSD dei C-alfa fra le due catene del modello dopo la sovrapposizione di Kabsch."""
    model = _load(path, "m")
    a, b = (np.array([r["CA"].coord for r in c if r.id[0] == " "], dtype=float) for c in model)
    rot, trans = geometry.kabsch(b, a)
    return geometry.rmsd(geometry.apply(b, rot, trans), a)


@pytest.mark.modeller
@requires_modeller
def test_complex_alignment_preview(client):
    """Anteprima (align2d per catena): PIR a due catene con i '/' nelle stesse colonne e le catene del complesso."""
    _upload_dimer(client)
    job = client.post("/api/align2d", json=_complex_body()).json()
    done = wait_job(client, job["id"], timeout=300)
    assert done["status"] == "completed", done.get("error")
    res = done["result"]
    entries = sequences.parse_pir(res["pir"])
    tmpl = next(e for e in entries if e.is_template)
    tgt = next(e for e in entries if e.kind == "sequence")
    assert tmpl.code == "UBQD_AB" and complexes.target_chains(tgt) == [YEAST_UB, YEAST_UB]
    assert complexes.break_columns(tmpl.sequence) == complexes.break_columns(tgt.sequence)
    assert res["report"]["valid"] and res["chains"][1] == {"template_chain": "B", "name": "B", "length": 76,
                                                           "same_as": "A"}
    log = client.get(f"/api/jobs/{job['id']}/log").json()["text"]
    assert "Catena A del template ← A: 76 residui, 76 allineati, identità 96.1%" in log


@pytest.mark.modeller
@requires_modeller
def test_homodimer_with_symmetry_restraints(client):
    """Omodimero dell'ubiquitina di lievito su ubq_dimer.pdb con restrizioni di simmetria: due catene con i nomi del
    template numerate da 1, catene quasi identiche dopo la sovrapposizione, interfaccia presente e confrontata con
    quella del template (DockQ alto), sezione nel report."""
    _upload_dimer(client)
    body = _complex_body(options={"models": 2, "refinement": "fast", "symmetry": True}, quality="best")
    started = time.perf_counter()
    job = client.post("/api/model", json=body).json()
    assert job["title"] == "Modello ubd (2 catene) · UBQD"
    done = wait_job(client, job["id"], timeout=900)
    elapsed = time.perf_counter() - started
    assert done["status"] == "completed", done.get("error")
    res = done["result"]
    cx = res["complex"]
    assert [(c["model_chain"], c["template_chain"], c["length"]) for c in cx["chains"]] == [("A", "A", 76),
                                                                                           ("B", "B", 76)]
    assert cx["symmetry"]["applied"] and cx["symmetry"]["groups"] == [["A", "B"]] and cx["mode"] == "complex"
    best = next(m for m in res["models"] if m["best"])
    path = client.app.state.core.jobs.file_path(job["id"], best["file"])
    model = _load(path, "m")
    assert [c.id for c in model] == ["A", "B"]
    assert [[r.id[1] for r in c][:2] for c in model] == [[1, 2], [1, 2]]  # numerazione da 1 per catena
    rmsd = _chain_rmsd(path)
    print(f"RMSD fra le catene con simmetria: {rmsd:.3f} Å; job in {elapsed:.1f} s")
    # misurato 0,03 Å (senza simmetria 0,63 Å: test sotto), stesso seme e raffinamento "rapido"
    assert rmsd < 0.1
    assessment = cx["assessment"]
    assert assessment["template"] == "UBQD_AB" and len(assessment["interfaces"]["pairs"]) == 1
    (c,) = assessment["comparison"]
    print(f"Confronto: {c['text']}")
    assert c["template_chains"] == ["A", "B"] and c["dockq"] >= 0.49 and c["conserved_percent"] >= 50
    assert best["complex"]["status"] == assessment["status"]
    quality = client.get(f"/api/jobs/{job['id']}/quality/{best['file']}").json()
    assert quality["complex"]["comparison"][0]["dockq"] == c["dockq"]
    pdb = path.read_text(encoding="utf-8")
    assert "REMARK 999 BIOLOOM CHAINS 2 A=A B=B" in pdb and "REMARK 999 BIOLOOM SYMMETRY CA WEIGHT 1 A,B" in pdb
    html = client.get(f"/api/jobs/{job['id']}/report/{best['file']}").text
    assert "Interfacce del complesso" in html and html.count("<section") == html.count("</section>")


@pytest.mark.modeller
@requires_modeller
def test_homodimer_without_symmetry_and_from_a_pir(client):
    """Senza simmetria (e senza analisi di qualità) il modello ha comunque due catene e le interfacce del migliore; lo
    stesso allineamento dato come PIR (modalità "Da documento PIR") dà un modello a due catene con i nomi del template."""
    _upload_dimer(client)
    body = _complex_body(options={"models": 2, "refinement": "fast"}, quality="none")
    job = client.post("/api/model", json=body).json()
    done = wait_job(client, job["id"], timeout=600)
    assert done["status"] == "completed", done.get("error")
    res = done["result"]
    assert not res["complex"]["symmetry"]["applied"] and res["complex"]["assessment"]["comparison"]
    best = next(m for m in res["models"] if m["best"])
    assert best["complex"]["interfaces"] == 1  # interfacce del migliore anche senza analisi di qualità
    path = client.app.state.core.jobs.file_path(job["id"], best["file"])
    assert [c.id for c in _load(path, "m")] == ["A", "B"]
    rmsd = _chain_rmsd(path)
    print(f"RMSD fra le catene senza simmetria: {rmsd:.3f} Å")
    assert rmsd > 0.1  # misurato 0,63 Å: senza restrizioni le due catene divergono (con la simmetria 0,03 Å)
    pir = res["alignment"]
    job = client.post("/api/model", json={"alignment": pir, "templates": [{"id": "UBQD", "chains": ["A", "B"]}],
                                          "options": {"models": 1, "refinement": "very_fast"},
                                          "quality": "none"}).json()
    done = wait_job(client, job["id"], timeout=600)
    assert done["status"] == "completed", done.get("error")
    res = done["result"]
    assert res["complex"]["mode"] == "pir" and [c["model_chain"] for c in res["complex"]["chains"]] == ["A", "B"]
    best = next(m for m in res["models"] if m["best"])
    assert [c.id for c in _load(client.app.state.core.jobs.file_path(job["id"], best["file"]), "m")] == ["A", "B"]
