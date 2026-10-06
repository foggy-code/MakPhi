"""Mutagenesi in silico e scansione di alanina (F9.2): notazione, copia per MODELLER, indicatori strutturali e
classificazione (domain/mutations.py), varianti di UniProt simulate con la mappatura della numerazione, controlli
preventivi, rotte e job con MODELLER reale su 1UBQ e sulle strutture sintetiche (K48R, prolina nell'elica, scansione di
alanina ordinata, dimero, catena senza identificativo con un'interruzione, annullamento, energia con OpenMM).

Nessun accesso alla rete: UniProt è simulato con httpx.MockTransport sul client condiviso di engines/remote. I test di
MODELLER si saltano senza MODELLER, quello dell'energia anche senza OpenMM e PDBFixer (bioloom:4.1.0)."""

from __future__ import annotations

import csv
import io
import re
import subprocess
import time

import httpx
import numpy as np
import pytest

from bioloom.core.errors import AppError
from bioloom.core.jobs import PRIORITY, JobManager
from bioloom.domain import mutations, structure
from bioloom.domain import preflight as rules
from bioloom.engines import mutate as engine
from bioloom.engines import quality, remote, tools
from bioloom.services import preflight as preflight_module
from bioloom.services.licenses import LicenseService
from bioloom.services.mutations import scan_csv

from .conftest import DATA, HUMAN_UB, requires_modeller, ubq_variant, wait_job

UBQ = {"type": "template", "id": "1UBQ"}
GIB = 1024 ** 3
requires_openmm = pytest.mark.skipif(not (tools.find_module("openmm") and tools.find_module("pdbfixer")),
                                     reason="OpenMM o PDBFixer non installati in questa immagine")


def _model(path):
    return structure.first_model(structure.load_structure(path, "x"))


def _upload(client, path, template_id: str) -> None:
    with open(path, "rb") as fh:
        r = client.post("/api/templates/upload", files={"file": (path.name, fh, "chemical/x-pdb")},
                        data={"template_id": template_id})
    assert r.status_code == 200, r.text


def _crude_mutant(dest, resnum: int, resname: str, keep: tuple[str, ...]):
    """1UBQ con il residuo `resnum` rinominato e solo gli atomi `keep`: un mutante grezzo senza MODELLER (es. Ile30 →
    Ala con N, CA, C, O, CB), per provare gli indicatori su coordinate vere."""
    rows = []
    for ln in (DATA / "1UBQ.pdb").read_text().splitlines():
        if ln.startswith("ATOM") and int(ln[22:26]) == resnum:
            if ln[12:16].strip() not in keep:
                continue
            ln = ln[:17] + resname + ln[20:]
        rows.append(ln)
    dest.write_text("\n".join(rows) + "\n")
    return dest


# ------------------------------------------------------------------------------------------ notazione
def test_parse_notation_forms():
    out = mutations.parse("A:K48R, A:LYS63ARG;  i30p\n B:G52AA")
    assert [(p["chain"], p["resnum"], p["wt"], p["mut"]) for p in out] == [
        ("A", "48", "K", "R"), ("A", "63", "K", "R"), (None, "30", "I", "P"), ("B", "52A", "G", "A")]
    assert mutations.parse("_:M-1A")[0] == {"chain": "_", "resnum": "-1", "wt": "M", "mut": "A", "token": "_:M-1A"}
    assert mutations.parse("A:G52AARG")[0]["resnum"] == "52A" and mutations.parse("A:G52AARG")[0]["mut"] == "R"


@pytest.mark.parametrize("text,fragment", [
    ("", "almeno una mutazione"),
    ("A:K48", "Mutazione non valida"),
    ("48R", "Mutazione non valida"),
    ("A:K48K", "non cambia il residuo"),
    ("A:K48B", "Amminoacido non valido"),
    ("A:XYZ48ARG", "Amminoacido non valido"),
    ("A::K48R", "Mutazione non valida"),
    (", ".join(f"A:K{i}R" for i in range(1, 12)), "Troppe mutazioni"),
], ids=["vuota", "senza-nuovo", "senza-originale", "identica", "lettera-ignota", "tre-lettere-ignote",
        "due-punti", "troppe"])
def test_parse_notation_errors(text, fragment):
    with pytest.raises(AppError) as err:
        mutations.parse(text)
    assert fragment in err.value.message and err.value.code == "invalid_mutation"


def test_resolve_checks_the_structure(tmp_path):
    ubq = _model(DATA / "1UBQ.pdb")
    muts = mutations.resolve(mutations.parse("K48R, A:I30P"), ubq)  # una sola catena: si può omettere
    assert [m.label for m in muts] == ["A:K48R", "A:I30P"] and muts[0].to_dict()["mut_name"] == "ARG"
    cases = {
        "A:E48R": "il residuo originale non corrisponde: A48 è LYS (K), non GLU (E)",
        "B:K48R": "Catena B non presente nella struttura (catene: A)",
        "A:K200R": "Residuo 200 non presente nella catena A (residui 1–76)",
        "A:K48R, A:K48A": "compare in più mutazioni",
    }
    for text, fragment in cases.items():
        with pytest.raises(AppError) as err:
            mutations.resolve(mutations.parse(text), ubq)
        assert fragment in err.value.message, text
    with pytest.raises(AppError) as err:  # un ligando non è un amminoacido
        mutations.resolve(mutations.parse("A:H101A"), _model(DATA / "ubq_zn.pdb"))
    assert "ZN A101 non è un amminoacido" in err.value.message
    dimer = _model(DATA / "ubq_dimer.pdb")
    with pytest.raises(AppError) as err:
        mutations.resolve(mutations.parse("K48R"), dimer)
    assert "Indica la catena di «K48R»: la struttura ne ha 2 (A, B)" in err.value.message
    assert mutations.resolve(mutations.parse("B:K48R"), dimer)[0].chain == "B"


def _blank_dimer(dest):
    """Dimero sintetico con la catena A senza identificativo (colonna 22 vuota) e la catena B."""
    rows = [ln[:21] + " " + ln[22:] if ln.startswith(("ATOM", "HETATM", "TER")) and len(ln) > 21 and ln[21] == "A"
            else ln for ln in (DATA / "ubq_dimer.pdb").read_text().splitlines()]
    dest.write_text("\n".join(rows) + "\n")
    return dest


def test_canonical_notation_round_trip_with_a_blank_chain_and_another_chain(tmp_path, client_with_ubq, monkeypatch):
    """Catena senza identificativo più la catena B: la notazione salvata nei parametri del job e quella delle varianti
    di UniProt scrivono sempre la catena ('_'), così si rileggono all'esecuzione; il testo resta «K6R»."""
    dimer = _model(_blank_dimer(tmp_path / "blank_dimer.pdb"))
    muts = mutations.resolve(mutations.parse("_:K6R, B:K48R"), dimer)
    assert [m.chain for m in muts] == [" ", "B"] and mutations.notation(muts) == "K6R, B:K48R"
    saved = mutations.notation(muts, canonical=True)
    assert saved == "_:K6R, B:K48R" and mutations.resolve(mutations.parse(saved), dimer) == muts
    with pytest.raises(AppError):  # la forma dei testi non basta con più catene
        mutations.resolve(mutations.parse(mutations.notation(muts)), dimer)
    parsed = remote.parse_uniprot_variants(UNIPROT_JSON)
    mapped = mutations.map_variants(parsed["variants"], parsed["sequence"], dimer, chain="_")
    k48r = next(v for v in mapped["variants"] if v["position"] == 53)
    assert k48r["notation"] == "_:K48R" and mutations.resolve(mutations.parse(k48r["notation"]), dimer)[0].chain == " "
    # rotta di avvio: parametri del job in forma canonica, titolo con il testo
    c = client_with_ubq
    _upload(c, tmp_path / "blank_dimer.pdb", "BLANKDIMER")
    captured = {}

    def submit(kind, params, **kw):
        captured.update(params=params, title=kw.get("title"))
        return {"id": "x", "kind": kind, "params": params, "title": kw.get("title")}

    monkeypatch.setattr(c.app.state.core.jobs, "submit", submit)
    r = c.post("/api/mutate", json={"source": {"type": "template", "id": "BLANKDIMER"}, "mutations": "_:K6R"})
    assert r.status_code == 200, r.text
    assert captured["params"]["mutations"] == "_:K6R" and captured["title"].endswith("BLANKDIMER K6R")
    from bioloom.services.mutations import MutationService

    service = MutationService(c.app.state.core.jobs, c.app.state.core.compute)
    assert service.validate_mutate(captured["params"])[0].chain == " "  # come all'esecuzione


def test_resolve_chain_without_identifier(tmp_path):
    blank = _model(ubq_variant(tmp_path / "blank.pdb", blank_chain=True))
    for text in ("K48R", "_:K48R"):
        (m,) = mutations.resolve(mutations.parse(text), blank)
        assert m.chain == " " and m.label == "K48R" and m.site == "_:48"
    with pytest.raises(AppError) as err:
        mutations.resolve(mutations.parse("A:K48R"), blank)
    assert "Catena A non presente nella struttura (catene: _)" in err.value.message
    mse = _model(ubq_variant(tmp_path / "mse.pdb", hetatm={1: "MSE"}))
    assert mutations.resolve(mutations.parse("A:M1A"), mse)[0].wt == "M"  # selenometionina = metionina


def test_file_tag_and_seeds():
    k48r = mutations.Mutation("A", "48", "K", "R")
    e51q = mutations.Mutation("B", "51", "E", "Q")
    assert mutations.file_tag([k48r], multichain=False) == "K48R"
    assert mutations.file_tag([k48r, e51q], multichain=True) == "A-K48R_B-E51Q"
    assert mutations.file_tag([mutations.Mutation(" ", "48", "K", "R")], multichain=True) == "_-K48R"
    many = [mutations.Mutation("A", str(i), "K", "R") for i in range(100, 110)]
    assert mutations.file_tag(many, multichain=True) == "10mut"
    assert mutations.seeds() == [-49837, -49838, -49839]
    assert mutations.seeds(-49999) == [-49999, -49998, -49997]  # sempre fra -50000 e -2
    with pytest.raises(AppError):
        mutations.seeds(-1)


# ------------------------------------------------------------------------------------------ copia per MODELLER
def test_modeller_input_splits_breaks_and_converts_residues(tmp_path):
    """Senza i residui 35–37 la catena A diventa due tratti (A e B per MODELLER); MSE → MET con SD; acque e zinco
    esclusi; `restore_chains` riporta il nome originale e toglie il TER fra i tratti."""
    path = ubq_variant(tmp_path / "gap_mse.pdb", hetatm={1: "MSE"}, drop=range(35, 38))
    text, info = mutations.modeller_input(_model(path))
    assert info["chains"] == {"A": "A", "B": "A"} and info["groups"] == {"A": ["A", "B"]}
    assert info["breaks"] == [{"chain": "A", "after": "34", "before": "38"}]
    assert info["residues"][("A", "34")] == "A" and info["residues"][("A", "38")] == "B"
    assert info["residues_count"] == 73 and info["modified"] == ["MSE A1 → MET"]
    rows = text.splitlines()
    assert all(not ln.startswith("HETATM") for ln in rows)
    sd = next(ln for ln in rows if ln[17:20] == "MET" and ln[22:26].strip() == "1" and ln[12:16] == " SD ")
    assert sd.endswith(" S")
    assert sum(1 for ln in rows if ln.startswith("TER")) == 2
    restored, renamed = mutations.restore_chains(text, info["chains"])
    assert renamed == [] and {ln[21] for ln in restored.splitlines() if ln.startswith("ATOM")} == {"A"}
    assert sum(1 for ln in restored.splitlines() if ln.startswith("TER")) == 1  # solo a fine catena
    zn_text, zn_info = mutations.modeller_input(_model(DATA / "ubq_zn.pdb"))
    assert zn_info["excluded"] == ["ZN A101"] and " ZN " not in zn_text and "HOH" not in zn_text


def test_modeller_input_renames_a_chain_without_identifier(tmp_path):
    text, info = mutations.modeller_input(_model(ubq_variant(tmp_path / "blank.pdb", blank_chain=True)))
    assert info["chains"] == {"A": " "} and info["residues"][(" ", "48")] == "A"
    assert {ln[21] for ln in text.splitlines() if ln.startswith("ATOM")} == {"A"}
    restored, _renamed = mutations.restore_chains(text, info["chains"])
    assert {ln[21] for ln in restored.splitlines() if ln.startswith("ATOM")} == {" "}


def _two_letter_chain(dest):
    """1UBQ in mmCIF con la catena «AA» (auth_asym_id di due caratteri, come nei grandi complessi)."""
    from Bio.PDB.mmcifio import MMCIFIO

    st = structure.load_structure(DATA / "1UBQ.pdb", "x")
    structure.first_model(st)["A"].id = "AA"
    out = MMCIFIO()
    out.set_structure(st)
    out.save(str(dest))
    return dest


def test_sites_of_a_chain_with_a_two_character_name(tmp_path):
    """Catena «AA»: MODELLER la chiama A e i modelli costruiti restano così (formato PDB). Indicatori, residui
    ottimizzati ed energia cercano il sito con la lettera dei modelli; etichetta, sito e partner restano «AA»."""
    from bioloom.services.mutations import MutationService

    model = _model(_two_letter_chain(tmp_path / "ubq_aa.cif"))
    (m,) = mutations.resolve(mutations.parse("AA:K48R"), model)
    text, info = mutations.modeller_input(model)
    restored, renamed = mutations.restore_chains(text, info["chains"])  # come i file scelti dopo MODELLER
    assert renamed == ["AA→A"]
    (tmp_path / "built.pdb").write_text(restored)
    built = _model(tmp_path / "built.pdb")
    assert [c.id for c in built] == ["A"] and mutations.model_site(info, "AA", "48") == ("A", "48")
    assert mutations.model_site(info, "B", "48") == ("B", "48")  # catene di un carattere: nome originale
    service = MutationService(None, None)
    (row,) = service._site_indicators({"wt_model": built, "mut_model": built}, [m], {}, info)
    assert row["label"] == "AA:K48R" and row["site"] == "AA:48" and row["rsa_wt"] == pytest.approx(0.40, abs=0.01)
    partners = mutations._site_interactions(mutations.facts(built).interactions, ("A", "30"),
                                            mutations.renamed_chains(info))
    assert "VAL AA26" in {p["partner"] for p in partners.values()}  # legame idrogeno dell'elica, nome originale
    moved = service._moved(model, [m], info)
    assert ("A", "48") in moved and {c for c, _r in moved} == {"A"}
    shift = mutations.backbone_shift(built, built, moved)
    assert shift["outside_atoms"] == shift["atoms"] - 4 * len(moved)
    assert mutations.fragment_windows(built, service._model_sites(info, [m])) == [[("A", "47"), ("A", "48"),
                                                                                  ("A", "49")]]


def test_neighbourhoods():
    ubq = _model(DATA / "1UBQ.pdb")
    near = mutations.neighbourhood(ubq, [("A", "48")], 8.0)
    assert ("A", "48") in near and ("A", "30") not in near and len(near) == 15  # misurato
    assert len(mutations.neighbourhood(ubq, [("A", "30")], 8.0)) == 32
    zn = _model(DATA / "ubq_zn.pdb")
    assert mutations.neighbourhood_hetero(zn, [("A", "68")], 8.0) == ["ZN A101"]
    assert mutations.neighbourhood_hetero(zn, [("A", "30")], 8.0) == []


# ------------------------------------------------------------------------------------------ indicatori e classi
def _facts(path):
    return mutations.facts(_model(path))


def test_indicators_of_a_crude_core_mutation(tmp_path):
    """Ile30 (nucleo, α-elica 23–34) troncata ad alanina senza MODELLER: residuo sepolto, superficie apolare sepolta
    persa ben oltre 40 Å², sostituzione non conservativa → due segnali moderati, probabilmente destabilizzante."""
    wt = _facts(DATA / "1UBQ.pdb")
    mut = _facts(_crude_mutant(tmp_path / "i30a.pdb", 30, "ALA", ("N", "CA", "C", "O", "CB")))
    ind = mutations.site_indicators(mutations.Mutation("A", "30", "I", "A"), wt, mut, quality.MAX_ASA)
    assert ind["core"] and ind["rsa_wt"] < 0.1 and ind["ss"] == "H" and ind["ss_position"] == 7
    assert ind["buried_delta"]["apolar"] < -mutations.APOLAR_LOSS and ind["blosum62"] == -1
    assert ind["helix_propensity"] == -0.41 and ind["interface"] is None
    codes = {s["code"]: s["level"] for s in mutations.site_signals(ind)}
    assert codes == {"buried_apolar_lost": "moderate", "core_nonconservative": "moderate"}
    out = mutations.classify([ind])
    assert out["code"] == "destabilizing" and out["label"] == "probabilmente destabilizzante"
    assert out["summary"] == "Probabilmente destabilizzante: 2 segnali moderati." and out["binding"] is None


def test_indicators_of_a_proline_in_the_helix(tmp_path):
    """Ile30 → Pro (grezza: senza CG e CD) nell'elica 23–34, ottava posizione: prolina oltre il primo giro (forte) e
    legame idrogeno dell'NH perso con Val26 (i → i−4 dell'elica)."""
    wt = _facts(DATA / "1UBQ.pdb")
    mut = _facts(_crude_mutant(tmp_path / "i30p.pdb", 30, "PRO", ("N", "CA", "C", "O", "CB")))
    ind = mutations.site_indicators(mutations.Mutation("A", "30", "I", "P"), wt, mut, quality.MAX_ASA)
    assert ind["helix_propensity"] == 2.75
    lost = ind["interactions"]["lost"]
    assert {"partner": "VAL A26", "role": "NH della catena principale"}.items() <= \
        next(x for x in lost if x["type"] == "hbond").items()
    signals = {s["code"]: s for s in mutations.site_signals(ind)}
    assert signals["proline_helix"]["level"] == "strong" and "Pace & Scholtz 1998" in signals["proline_helix"]["text"]
    assert mutations.classify([ind])["code"] == "destabilizing"


def test_indicators_of_an_exposed_mutation(tmp_path):
    """Lys48 (esposto, filamento) troncata ad alanina: nessun segnale, probabilmente neutra."""
    wt = _facts(DATA / "1UBQ.pdb")
    mut = _facts(_crude_mutant(tmp_path / "k48a.pdb", 48, "ALA", ("N", "CA", "C", "O", "CB")))
    ind = mutations.site_indicators(mutations.Mutation("A", "48", "K", "A"), wt, mut, quality.MAX_ASA)
    assert not ind["core"] and ind["rsa_wt"] == pytest.approx(0.40, abs=0.01)  # RSA misurata su 1UBQ
    out = mutations.classify([ind], {"delta": 10.0, "delta_eq": 0.1})
    assert out["code"] == "neutral" and out["summary"].startswith("Probabilmente neutra")


def _ind(**over) -> dict:
    """Indicatori sintetici di un sito (esposto, coil, nessuna interazione), per le regole di classificazione."""
    base = {"site": "A:10", "label": "A:K10A", "wt": "K", "mut": "A", "rsa_wt": 0.6, "rsa_mut": 0.6, "core": False,
            "buried_wt": {"total": 50.0, "apolar": 30.0}, "buried_mut": {"total": 40.0, "apolar": 25.0},
            "buried_delta": {"total": -10.0, "apolar": -5.0}, "ss": "-", "ss_position": 0, "ss_length": 1,
            "rama": {"phi": -70.0, "psi": 140.0, "wt": "favored", "mut": "favored"}, "blosum62": -1,
            "helix_propensity": None, "interactions": {"wild_type": 0, "mutant": 0, "lost": [], "gained": []},
            "clashes": {"wild_type": 0, "mutant": 0, "worst": None}, "interface": None}
    base.update(over)
    return base


def _hb(role="catena laterale", kind="hbond", inter=False) -> dict:
    return {"type": kind, "partner": "GLU A20", "role": role, "atom": "NZ", "inter_chain": inter, "distance": 2.9}


def test_proline_in_the_first_turn_is_only_informative():
    """Prolina nel primo giro dell'elica (posizione comune, MacArthur & Thornton 1991): solo informativa; su un residuo
    esposto senza altri segnali la mutazione resta probabilmente neutra."""
    out = mutations.classify([_ind(ss="H", ss_position=1, ss_length=10, mut="P", helix_propensity=2.9)])
    assert [s["code"] for s in out["signals"]] == ["proline_helix_start"] and out["code"] == "neutral"


def test_classification_rules():
    # glicina nell'elica (ΔP ≥ 0,5) è un segnale moderato: da solo l'effetto è incerto
    helix_gly = _ind(ss="H", ss_position=4, ss_length=10, wt="A", mut="G", helix_propensity=1.0)
    out = mutations.classify([helix_gly])
    assert out["code"] == "uncertain" and out["signals"][0]["code"] == "helix_propensity"
    # agli estremi dell'elica no (cappucci)
    assert mutations.classify([_ind(ss="H", ss_position=0, ss_length=10, mut="G", helix_propensity=1.0)])[
        "code"] == "neutral"
    # filamento: prolina con l'NH legato forte, glicina moderata
    strand_pro = _ind(ss="E", mut="P", interactions={"wild_type": 1, "mutant": 0, "gained": [],
                                                    "lost": [_hb("NH della catena principale")]})
    assert {s["code"]: s["level"] for s in mutations.site_signals(strand_pro)}["proline_strand"] == "strong"
    assert mutations.site_signals(_ind(ss="E", mut="G"))[0]["code"] == "strand_breaker"
    # disolfuro perso: forte
    ss = _ind(wt="C", mut="S", interactions={"wild_type": 1, "mutant": 0, "gained": [], "lost": [
        {"type": "disulfide", "partner": "CYS A80", "role": "", "atom": "SG", "inter_chain": False, "distance": 2.0}]})
    assert mutations.classify([ss])["code"] == "destabilizing"
    # conformazione permessa solo alla glicina
    gly = _ind(wt="G", mut="A", rama={"phi": 80.0, "psi": -170.0, "wt": "allowed", "mut": "outlier"})
    assert mutations.site_signals(gly)[0]["code"] == "backbone_conformation"
    # legami idrogeno: esposto 1 informativo, 2 moderato; sepolto 1 moderato
    assert mutations.site_signals(_ind(interactions={"wild_type": 1, "mutant": 0, "gained": [],
                                                     "lost": [_hb()]}))[0]["level"] == "info"
    two = _ind(interactions={"wild_type": 2, "mutant": 0, "gained": [], "lost": [_hb(), _hb("catena principale")]})
    assert mutations.site_signals(two)[0]["level"] == "moderate"
    buried = _ind(core=True, rsa_wt=0.05, blosum62=1, interactions={"wild_type": 1, "mutant": 0, "gained": [],
                                                                      "lost": [_hb()]})
    assert mutations.site_signals(buried)[0]["level"] == "moderate"
    # ponte salino: in superficie informativo
    salt = _ind(interactions={"wild_type": 1, "mutant": 0, "gained": [], "lost": [_hb(kind="salt_bridge")]})
    assert mutations.site_signals(salt)[0]["level"] == "info"
    # carica nel nucleo e contatti sterici
    charge = _ind(core=True, rsa_wt=0.02, wt="L", mut="K", blosum62=-2)
    assert {s["code"] for s in mutations.site_signals(charge)} == {"core_nonconservative", "buried_charge"}
    clash = _ind(clashes={"wild_type": 0, "mutant": 2, "worst": 0.9})
    assert mutations.site_signals(clash)[0]["code"] == "steric_clash"
    # nucleo senza segnali: incerta; solo guadagni nel nucleo: incerta
    core = _ind(core=True, rsa_wt=0.1, blosum62=1)
    assert mutations.classify([core])["code"] == "uncertain"
    gains = _ind(core=True, rsa_wt=0.1, blosum62=1, interactions={"wild_type": 0, "mutant": 1, "lost": [],
                                                                 "gained": [_hb()]})
    assert mutations.classify([gains])["signals"][0]["level"] == "gain"


def test_multiple_sites_name_their_signals():
    """Variante con due sostituzioni: i segnali e i motivi dicono di quale sito sono; la classe vale per la variante."""
    a = _ind(label="A:A20G", ss="H", ss_position=4, ss_length=10, wt="A", mut="G", helix_propensity=1.0)
    b = _ind(label="A:L30K", core=True, rsa_wt=0.02, wt="L", mut="K", blosum62=-2)
    out = mutations.classify([a, b])
    assert out["code"] == "destabilizing" and {s["site"] for s in out["signals"]} == {"A:A20G", "A:L30K"}
    assert out["reasons"][0].startswith("A:A20G: Nell'α-elica GLY")


def test_dope_and_energy_signals():
    assert mutations.dope_signal(2.9, 300.0)["level"] == "strong"  # (residui equivalenti, ΔDOPE)
    assert mutations.dope_signal(1.2, 120.0)["level"] == "moderate"
    assert mutations.dope_signal(0.4, 40.0)["level"] == "info"
    assert mutations.dope_signal(-1.5, -150.0)["level"] == "gain"
    assert mutations.dope_signal(None, None) is None
    assert mutations.energy_signal({"ddE": 120.0})["level"] == "strong"
    assert mutations.energy_signal({"ddE": 60.0})["level"] == "moderate"
    assert mutations.energy_signal({"ddE": 20.0})["level"] == "info"
    assert mutations.energy_signal({"ddE": -80.0})["level"] == "gain"
    out = mutations.classify([_ind()], {"delta": 300.0, "delta_eq": 2.9}, {"ddE": 60.0})
    assert out["code"] == "destabilizing" and out["score"] == pytest.approx(100 + 10 + 2.9)
    assert [s["code"] for s in out["signals"]] == ["dope", "energy"]


def test_numbers_in_the_reasons_round_like_the_interface():
    """I numeri dei motivi si arrotondano come la tabella accanto (fmtNum, Intl in it-IT): metà lontano da zero
    sulla rappresentazione decimale (ΔDOPE −22,5 della riga A:Q49A: −23 nella colonna, prima −22 nel motivo)."""
    assert mutations.dope_signal(-0.225, -22.5)["text"].startswith("ΔDOPE −23 (−0,23 residui equivalenti)")
    assert [mutations._n(v, d) for v, d in ((-22.5, 0), (-23.5, 0), (22.5, 0), (0.125, 2), (1.005, 2), (2.675, 2),
                                            (305.4, 0), (-3, 0), (-0.4, 0), (-0.04, 1), (0.0, 1))] == \
        ["−23", "−24", "23", "0,13", "1,01", "2,68", "305", "−3", "0", "0,0", "0,0"]


def test_energy_terms_and_dope_summary():
    e = mutations.energy_terms(-12393.2, -12370.3, -904.0, -953.7)
    assert e["delta_folded"] == 22.9 and e["delta_reference"] == -49.7 and e["ddE"] == 72.6
    assert e["ddE_kcal"] == 17.4
    assert mutations.energy_terms(None, -1.0, -1.0, -1.0)["ddE"] is None
    rows = [{"role": "wild_type", "dope": -7918.0, "local": -5576.3, "interchain": None},
            {"role": "wild_type", "dope": -7866.6, "local": -5524.9, "interchain": None},
            {"role": "mutant", "dope": -7569.4, "local": -5227.7, "interchain": None}]
    d = mutations.dope_summary(rows[0], rows[2], 76, 32, rows)
    assert d["delta"] == 348.6 and d["delta_local"] == 348.6 and d["per_residue"] == pytest.approx(104.18, abs=0.01)
    assert d["delta_eq"] == pytest.approx(3.35, abs=0.01) and d["spread"] == {"wild_type": 51.4, "mutant": 0.0}
    assert d["interchain_delta"] is None


def test_binding_effect_and_hotspots():
    site = _ind(interface={"delta": 60.0, "rsa_complex": 0.1, "lost_inter_chain": 1})
    b = mutations.binding_effect([site], {"interchain_delta_eq": 0.8})
    assert b["hotspot"] == "probabile" and "Bogan & Thorn 1998" in b["text"]
    exposed = _ind(interface={"delta": 20.0, "rsa_complex": 0.5, "lost_inter_chain": 0})
    assert mutations.binding_effect([exposed], {"interchain_delta_eq": 0.8})["hotspot"] == "possibile"
    assert mutations.binding_effect([exposed], {"interchain_delta_eq": 0.1})["hotspot"] is None
    assert mutations.binding_effect([_ind()], {}) is None
    # coperto del tutto nel complesso (SASA arrotondata a 0,0: RSA 0): il centro dell'"O-ring", sepolto
    covered = _ind(interface={"delta": 80.0, "rsa_complex": 0.0, "lost_inter_chain": 1})
    b = mutations.binding_effect([covered], {"interchain_delta_eq": 1.0})
    assert b["hotspot"] == "probabile" and "residuo sepolto nel complesso" in b["text"]
    # RSA ignota (ASA massima non nota): non sepolto
    unknown = _ind(interface={"delta": 80.0, "rsa_complex": None, "lost_inter_chain": 1})
    b = mutations.binding_effect([unknown], {"interchain_delta_eq": 1.0})
    assert b["hotspot"] == "possibile" and "sepolto" not in b["text"]


def test_fragments_for_the_energy_reference():
    ubq = _model(DATA / "1UBQ.pdb")
    assert mutations.fragment_windows(ubq, [("A", "30")]) == [[("A", "29"), ("A", "30"), ("A", "31")]]
    # finestre adiacenti unite, estremità della catena
    assert mutations.fragment_windows(ubq, [("A", "30"), ("A", "32"), ("A", "1")]) == [
        [("A", "1"), ("A", "2")], [("A", str(i)) for i in range(29, 34)]]
    text = mutations.fragments_pdb(ubq, [("A", "30"), ("A", "60")])
    rows = [ln for ln in text.splitlines() if ln.startswith("ATOM")]
    assert {int(ln[22:26]) for ln in rows} == {29, 30, 31, 59, 60, 61} and text.count("TER") == 2
    ca30 = next(ln for ln in rows if ln[22:26].strip() == "30" and ln[12:16] == " CA ")
    x = float(ca30[30:38])
    assert x == pytest.approx(float(ubq["A"][30]["CA"].coord[0]) + 1000.0, abs=0.002)


def test_backbone_shift_outside_the_moved_residues(tmp_path):
    ubq = _model(DATA / "1UBQ.pdb")
    st = structure.load_structure(DATA / "1UBQ.pdb", "x")
    structure.first_model(st)["A"][48]["CA"].set_coord(structure.first_model(st)["A"][48]["CA"].coord + [1, 0, 0])
    moved = tmp_path / "moved.pdb"
    structure.write_pdb(st, moved)
    out = mutations.backbone_shift(ubq, _model(moved), {("A", "48")})
    assert out["outside"] == 0.0 and out["outside_atoms"] == 300 and out["all"] == pytest.approx(1 / np.sqrt(304),
                                                                                                    abs=1e-3)


# ------------------------------------------------------------------------------------------ scansione di alanina
def test_scan_residues_and_plan(tmp_path):
    ubq = _model(DATA / "1UBQ.pdb")
    assert mutations.parse_residues("A:30, 43, A:I30, A:46-48", ubq) == [
        ("A", "30"), ("A", "43"), ("A", "46"), ("A", "47"), ("A", "48")]
    for text, fragment in (("", "Indica i residui"), ("A:L30", "è ILE, non L"), ("A:99", "non presente"),
                           ("A:80-90", "Nessun residuo fra 80 e 90"), ("C:30", "Catena C non presente")):
        with pytest.raises(AppError) as err:
            mutations.parse_residues(text, ubq)
        assert fragment in err.value.message and err.value.code == "invalid_scan", text
    muts, skipped = mutations.scan_plan(ubq, [("A", "30"), ("A", "10"), ("A", "37"), ("A", "46"), ("A", "48")])
    assert [m.label for m in muts] == ["A:I30A", "A:K48A"]
    assert [(s["resnum"], s["reason"].split(":")[0]) for s in skipped] == [
        ("10", "glicina"), ("37", "prolina"), ("46", "già alanina")]
    with pytest.raises(AppError) as err:
        mutations.scan_plan(ubq, [("A", "10")])
    assert "Nessun residuo da scandire" in err.value.message
    everything = [("A", str(i)) for i in range(1, 77)]
    with pytest.raises(AppError) as err:
        mutations.scan_plan(ubq, everything)
    assert "Troppi residui per la scansione" in err.value.message and f"al massimo {mutations.MAX_SCAN}" in \
        err.value.message
    with pytest.raises(AppError) as err:
        mutations.scan_plan(ubq, everything[:30], energy=True)
    assert f"al massimo {mutations.MAX_SCAN_ENERGY} con l'energia" in err.value.message


def test_scan_order_and_csv():
    def row(label, code, score, eq):
        return {"label": label, "assessment": {"code": code, "label": mutations.CLASSES[code], "score": score,
                                               "reasons": ["motivo"], "binding": None},
                "dope": {"delta": eq * 100, "delta_eq": eq, "delta_local": None, "interchain_delta": None},
                "site": {"rsa_wt": 0.1, "ss": "H", "interface": None}, "energy": None}
    rows = mutations.scan_order([row("A:K48A", "neutral", 0.1, 0.1), row("A:I30A", "destabilizing", 122.4, 2.4),
                                 row("A:S20A", "uncertain", 10.0, 0.3), row("A:L43A", "destabilizing", 122.9, 2.9)])
    assert [r["label"] for r in rows] == ["A:L43A", "A:I30A", "A:S20A", "A:K48A"]
    table = list(csv.reader(io.StringIO(scan_csv(rows))))
    assert table[0][0] == "mutazione" and table[1][:2] == ["A:L43A", "probabilmente destabilizzante"]


# ------------------------------------------------------------------------------------------ varianti di UniProt
TAG = "MAGSS"
UNIPROT_SEQ = TAG + HUMAN_UB[:29] + "V" + HUMAN_UB[30:] + "GKVHGSLARAGKVRGQTPKV"  # I30 → V in UniProt


def _feature(kind, pos, original, variations, description="", end=None, xref=None):
    f = {"type": kind, "location": {"start": {"value": pos, "modifier": "EXACT"},
                                    "end": {"value": end or pos, "modifier": "EXACT"}},
         "description": description, "featureId": f"VAR_{pos:06d}",
         "alternativeSequence": {"originalSequence": original, "alternativeSequences": variations}}
    if xref:
        f["featureCrossReferences"] = [{"database": "dbSNP", "id": xref}]
    return f


UNIPROT_JSON = {
    "primaryAccession": "P12345", "uniProtkbId": "TEST_HUMAN",
    "sequence": {"value": UNIPROT_SEQ, "length": len(UNIPROT_SEQ)},
    "comments": [{"commentType": "DISEASE", "disease": {"diseaseId": "Malattia di prova", "acronym": "TESTD"}}],
    "features": [
        _feature("Chain", 1, "", []),
        _feature("Natural variant", 53, "K", ["R"], "in TESTD; dbSNP:rs1", xref="rs1"),  # struttura 48
        _feature("Natural variant", 2, "A", ["V"], "in dbSNP:rs2"),  # nell'etichetta N-terminale
        _feature("Natural variant", 35, "V", ["A"], ""),  # struttura 30: Ile, non Val
        _feature("Mutagenesis", 68, "K", ["A", "R"], "Loss of polyubiquitination."),  # struttura 63
        _feature("Natural variant", 70, "VL", [], "Missing", end=71),  # delezione: non puntuale
        _feature("Natural variant", 200, "W", ["C"], "fuori"),
    ],
}


def test_parse_uniprot_variants():
    out = remote.parse_uniprot_variants(UNIPROT_JSON, "P12345")
    assert out["sequence"] == UNIPROT_SEQ and out["diseases"] == {"TESTD": "Malattia di prova"}
    assert [(v["position"], v["original"], v["variation"], v["category"]) for v in out["variants"]] == [
        (2, "A", "V", "naturale"), (35, "V", "A", "naturale"), (53, "K", "R", "malattia"), (68, "K", "A", "mutagenesi"),
        (68, "K", "R", "mutagenesi"), (200, "W", "C", "naturale")]
    k53 = next(v for v in out["variants"] if v["position"] == 53)
    assert k53["diseases"] == ["Malattia di prova"] and k53["ids"] == ["dbSNP:rs1"]


def test_map_variants_onto_the_structure_numbering():
    """UniProt ha 5 residui in più all'N-terminale: la posizione 53 è il residuo 48 della struttura (non si assume che
    le numerazioni coincidano)."""
    parsed = remote.parse_uniprot_variants(UNIPROT_JSON)
    out = mutations.map_variants(parsed["variants"], parsed["sequence"], _model(DATA / "1UBQ.pdb"))
    assert out["chain"] == "A" and out["offset"] == 5 and out["identity"] == pytest.approx(98.7, abs=0.1)
    assert "Numerazione di UniProt = numerazione della struttura + 5." in out["notes"]
    rows = {(r["position"], r["variation"]): r for r in out["variants"]}
    assert rows[(53, "R")]["applicable"] and rows[(53, "R")]["notation"] == "A:K48R" and rows[(53, "R")]["resnum"] == "48"
    assert rows[(68, "A")]["notation"] == "A:K63A" and rows[(68, "R")]["notation"] == "A:K63R"
    assert rows[(35, "A")]["applicable"] is False and rows[(35, "A")]["reason"] == "nella struttura c'è ILE (I), non VAL"
    assert rows[(2, "V")]["reason"] == "posizione non presente nella struttura"
    assert rows[(200, "C")]["reason"] == "posizione fuori dalla sequenza di UniProt"
    assert out["applicable"] == 3
    # la notazione si applica davvero alla struttura
    assert mutations.resolve(mutations.parse(rows[(53, "R")]["notation"]), _model(DATA / "1UBQ.pdb"))
    with pytest.raises(AppError) as err:
        mutations.map_variants(parsed["variants"], "W" * 90, _model(DATA / "1UBQ.pdb"))
    assert err.value.code == "invalid_variants" and "non corrisponde alla sequenza di UniProt" in err.value.message


def test_map_variants_of_a_repeated_sequence():
    """Poliubiquitina (come UBB, P0CG47): tre copie identiche dell'ubiquitina. L'allineamento globale sceglierebbe una
    copia qualunque; la mutagenesi annotata sulla prima copia (K48R, K63R) deve arrivare comunque al residuo 48 e 63."""
    seq = HUMAN_UB * 3 + "C"
    variants = [{"type": "mutagenesi", "category": "mutagenesi", "position": p, "original": "K", "variation": "R",
                 "description": "", "diseases": [], "ids": [], "evidences": 0, "feature_id": ""}
                for p in (48, 63, 76 + 48, 152 + 63)]
    out = mutations.map_variants(variants, seq, _model(DATA / "1UBQ.pdb"))
    assert [(s["start"], s["end"]) for s in out["segments"]] == [(1, 76), (77, 152), (153, 228)]
    assert [v["notation"] for v in out["variants"]] == ["A:K48R", "A:K63R", "A:K48R", "A:K63R"]
    assert [v["segment"] for v in out["variants"]] == [1, 1, 2, 3] and out["offset"] is None
    assert any("3 tratti ripetuti" in n for n in out["notes"])


class FakeUniProt:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(str(request.url))
        if request.url.host == "rest.uniprot.org" and request.url.path == "/uniprotkb/P12345.json":
            return httpx.Response(200, json=UNIPROT_JSON)
        return httpx.Response(404, json={})


@pytest.fixture
def uniprot(monkeypatch, tmp_path):
    fake = FakeUniProt()
    monkeypatch.setattr(remote, "_client", httpx.Client(transport=httpx.MockTransport(lambda r: fake.handler(r))))
    monkeypatch.setattr(remote, "_cache_dir", tmp_path / "remote_cache")
    return fake


def test_uniprot_variants_request_and_cache(uniprot):
    first = remote.uniprot_variants("p12345")
    second = remote.uniprot_variants("P12345")
    assert first == second and len(uniprot.calls) == 1  # la seconda dalla cache
    with pytest.raises(AppError):
        remote.uniprot_variants("non valida")


def test_variants_route(client_with_ubq, uniprot):
    r = client_with_ubq.get("/api/mutations/variants", params={"accession": "P12345", **UBQ})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["accession"] == "P12345" and body["label"] == "1UBQ" and body["applicable"] == 3
    assert [v["notation"] for v in body["variants"] if v["applicable"]] == ["A:K48R", "A:K63A", "A:K63R"]
    r = client_with_ubq.get("/api/mutations/variants", params={"accession": "P99999", **UBQ})
    assert r.status_code == 404
    r = client_with_ubq.get("/api/mutations/variants", params={"accession": "P12345", **UBQ, "chain": "B"})
    assert r.status_code == 400 and r.json()["error"]["code"] == "invalid_variants"


# ------------------------------------------------------------------------------------------ rotte, priorità, controlli
def test_routes_validate_before_queueing(client_with_ubq):
    c = client_with_ubq
    for kind, prio in (("mutate", 0), ("alascan", 1)):
        reg = c.app.state.core.jobs._handlers[kind]
        assert PRIORITY[kind] == prio and reg.priority == prio and reg.pool == "cpu"
    r = c.post("/api/mutate", json={"source": UBQ, "mutations": "A:E48R"})
    assert r.status_code == 400 and r.json()["error"]["code"] == "invalid_mutation"
    assert "A48 è LYS (K), non GLU (E)" in r.json()["error"]["message"]
    assert c.post("/api/mutate", json={"source": UBQ, "mutations": "A:K48R", "seed": -1}).status_code == 422
    assert c.post("/api/mutate", json={"source": {"type": "template", "id": "9ZZZ"}, "mutations": "K48R"}
                  ).status_code == 404
    r = c.post("/api/alascan", json={"source": UBQ, "selection": {"mode": "residues", "residues": "A:10"}})
    assert r.status_code == 400 and "Nessun residuo da scandire" in r.json()["error"]["message"]
    r = c.post("/api/alascan", json={"source": UBQ, "selection": {"mode": "interface"}})
    assert r.status_code == 400 and "una sola catena" in r.json()["error"]["message"]
    r = c.post("/api/alascan", json={"source": UBQ, "selection": {"mode": "pocket"}})
    assert r.status_code == 400 and r.json()["error"]["code"] == "invalid_scan"
    r = c.post("/api/alascan", json={"source": UBQ, "selection": {"mode": "pocket", "pocket_job": "abc123", "pocket": 1}})
    assert r.status_code == 404
    assert c.get("/api/jobs?kind=mutate").json() == [] and c.get("/api/jobs?kind=alascan").json() == []
    assert c.get("/api/mutations/history", params=UBQ).json() == []


def test_pocket_scan_refuses_pockets_of_a_replaced_file(client, monkeypatch, tmp_path):
    """Tasche calcolate su un template poi sostituito con lo stesso nome (altro file): la scansione della tasca le
    rifiuta invece di usare residui di un'altra struttura; con il file invariato la selezione registra l'impronta."""
    from bioloom.services import structure_tools
    from bioloom.services.mutations import MutationService

    fake = {"summary": {"count": 1, "druggable": 1, "threshold": 0.5,
                        "best": {"id": 1, "druggability": 0.8, "volume": 300.0}},
            "pockets": [{"id": 1, "druggability": 0.8, "residues": [{"chain": "A", "resnum": "30"},
                                                                   {"chain": "A", "resnum": "43"}]}],
            "warnings": []}
    monkeypatch.setattr(structure_tools.pockets, "run", lambda path, ctx, workdir: fake)
    _upload(client, DATA / "1UBQ.pdb", "UBQP")
    src = {"type": "template", "id": "UBQP"}
    pockets = wait_job(client, client.post("/api/pockets", json={"source": src}).json()["id"])
    assert pockets["status"] == "completed", pockets.get("error")
    params = {"source": src, "selection": {"mode": "pocket", "pocket_job": pockets["id"], "pocket": 1}}
    service = MutationService(client.app.state.core.jobs, client.app.state.core.compute)
    muts, _skipped, selection = service.validate_scan(params)
    assert [m.label for m in muts] == ["A:I30A", "A:L43A"]
    assert selection["source_sha256"] == pockets["result"]["source_sha256"]
    _upload(client, ubq_variant(tmp_path / "rinumerata.pdb", drop=range(28, 32)), "UBQP")  # stesso id, altro file
    r = client.post("/api/alascan", json=params)
    assert r.status_code == 400 and r.json()["error"]["code"] == "invalid_scan"
    assert "versione precedente del file" in r.json()["error"]["message"]
    assert client.get("/api/jobs?kind=alascan").json() == []


@pytest.fixture
def env(monkeypatch):
    """MODELLER con licenza valida e 50 GiB liberi; i test cambiano solo ciò che controllano."""
    state = {"modeller": {"configured": True, "source": "file", "masked": "x", "available": True, "version": "10.8",
                          "message": None}, "free": 50 * GIB}
    real_status = LicenseService.status

    def status(self):
        out = real_status(self)
        out["modeller"] = dict(state["modeller"])
        return out

    monkeypatch.setattr(LicenseService, "status", status)
    monkeypatch.setattr(preflight_module, "disk_free", lambda path: state["free"])
    monkeypatch.setattr(preflight_module, "same_disk", lambda a, b: False)
    return state


def _preflight(client, kind, params):
    r = client.post("/api/compute/preflight", json={"kind": kind, "params": params})
    assert r.status_code == 200, r.text
    return r.json()


def _codes(out, level=None):
    return [c["code"] for c in out["checks"] if level is None or c["level"] == level]


def test_preflight_mutate(client_with_ubq, env, monkeypatch):
    c = client_with_ubq
    out = _preflight(c, "mutate", {"source": UBQ, "mutations": "A:K48R"})
    assert out["ok"] and _codes(out) == ["estimated_time"]
    assert out["estimate"]["seconds"] == pytest.approx(rules.mutation_estimate(76, energy=False, mutations=1)["seconds"])
    assert "76 residui, 1 sostituzione" in out["estimate"]["detail"]
    # stesso errore della rotta di avvio
    bad = _preflight(c, "mutate", {"source": UBQ, "mutations": "A:E48R"})
    assert not bad["ok"] and bad["checks"][0]["code"] == "invalid_mutation"
    assert "A48 è LYS (K), non GLU (E)" in bad["checks"][0]["message"]
    assert _codes(_preflight(c, "mutate", {"source": UBQ, "mutations": "A:K48R", "seed": 5}), "error") == \
        ["validation_error"]
    # energia: senza OpenMM avviso, con OpenMM informazione
    monkeypatch.setattr(preflight_module.refine_engine, "missing", lambda: "Rifinitura energetica non disponibile.")
    out = _preflight(c, "mutate", {"source": UBQ, "mutations": "A:K48R", "energy": True})
    assert _codes(out, "warning") == ["mutation_energy_unavailable"]
    monkeypatch.setattr(preflight_module.refine_engine, "missing", lambda: None)
    out = _preflight(c, "mutate", {"source": UBQ, "mutations": "A:K48R", "energy": True})
    assert "mutation_energy" in _codes(out, "info")
    assert out["estimate"]["seconds"] > 60
    # MODELLER assente: errore
    env["modeller"] = {"configured": False, "available": False, "message": "manca"}
    assert _codes(_preflight(c, "mutate", {"source": UBQ, "mutations": "A:K48R"}), "error") == \
        ["modeller_license_missing"]
    env["modeller"]["available"] = True
    # spazio insufficiente
    env["free"] = 1024
    assert "disk_full_data" in _codes(_preflight(c, "mutate", {"source": UBQ, "mutations": "A:K48R"}), "error")


def test_preflight_mutate_near_a_metal(client, env):
    _upload(client, DATA / "ubq_zn.pdb", "UBQZN")
    out = _preflight(client, "mutate", {"source": {"type": "template", "id": "UBQZN"}, "mutations": "A:H68A"})
    assert out["ok"] and _codes(out, "warning") == ["mutation_ligand_near"]
    assert "ZN A101" in out["checks"][0]["message"]
    far = _preflight(client, "mutate", {"source": {"type": "template", "id": "UBQZN"}, "mutations": "A:I30A"})
    assert "mutation_ligand_near" not in _codes(far)


def test_preflight_alascan(client_with_ubq, env):
    c = client_with_ubq
    out = _preflight(c, "alascan", {"source": UBQ, "selection": {"mode": "residues", "residues": "A:30, A:10, A:48"}})
    assert out["ok"] and _codes(out) == ["scan_skipped", "estimated_time"]
    assert "GLY A10" in out["checks"][0]["message"]
    assert "2 residui in alanina su 76" in out["estimate"]["detail"]
    too_many = _preflight(c, "alascan", {"source": UBQ, "selection": {"mode": "residues", "residues": "A:1-76"}})
    assert not too_many["ok"] and too_many["checks"][0]["code"] == "invalid_scan"
    single = _preflight(c, "alascan", {"source": UBQ, "selection": {"mode": "interface"}})
    assert "una sola catena" in single["checks"][0]["message"]


def test_estimates_and_bytes():
    one = rules.mutation_site_seconds(76)
    assert one == pytest.approx(1.0 + 6 * (0.85 + 0.0046 * 76) + 0.76)
    assert rules.mutation_site_seconds(76, energy=True) == pytest.approx(one + 8 + 0.8 * 76)
    scan = rules.scan_estimate(76, 5, energy=False, lanes=2)
    assert scan["seconds"] == pytest.approx(round(3 * one * 1.25, 1)) and "2 alla volta" in scan["detail"]
    assert rules.scan_estimate(76, 1, energy=False, lanes=4)["seconds"] == pytest.approx(round(one, 1))
    results, scratch = rules.mutation_bytes(76)
    assert results == rules.MUTATION_BASE_BYTES + 76 * 2 * 880 + 76 * 2 * 2000 and scratch > results


# ------------------------------------------------------------------------------------------ MODELLER reale
def _atoms(model, chain="A"):
    """Coordinate degli atomi pesanti degli amminoacidi per (residuo, atomo)."""
    return {(r.id[1], a.get_id()): a.coord.astype(float) for r in model[chain] if r.id[0] == " " for a in r
            if a.element != "H"}


@requires_modeller
def test_k48r_on_1ubq(client_with_ubq):
    """K48R (esposto, sostituzione conservativa): il residuo 48 del mutante è Arg, il resto invariato (catena
    principale ferma fuori dai residui ottimizzati, catene laterali lontane identiche); probabilmente neutra o
    incerta; qualità, report e cronologia."""
    c = client_with_ubq
    r = c.post("/api/mutate", json={"source": UBQ, "mutations": "A:lys48arg"})  # codici a tre lettere, minuscole
    assert r.status_code == 200, r.text
    assert r.json()["title"] == "Mutagenesi · 1UBQ A:K48R" and r.json()["params"]["mutations"] == "A:K48R"
    job = wait_job(c, r.json()["id"], timeout=600)
    assert job["status"] == "completed", job.get("error")
    res = job["result"]
    assert res["files"] == {"wild_type": "1UBQ_wt.pdb", "mutant": "1UBQ_K48R.pdb"}
    jobs = c.app.state.core.jobs
    mut = _model(jobs.file_path(job["id"], "1UBQ_K48R.pdb"))
    wt_opt = _model(jobs.file_path(job["id"], "1UBQ_wt.pdb"))
    original = _model(DATA / "1UBQ.pdb")
    assert mut["A"][48].get_resname() == "ARG" and {"NE", "CZ", "NH1", "NH2"} <= {a.get_id() for a in mut["A"][48]}
    assert wt_opt["A"][48].get_resname() == "LYS"
    assert [r.get_resname() for r in mut["A"] if r.id[1] != 48] == [r.get_resname() for r in original["A"]
                                                                    if r.id[1] != 48 and r.id[0] == " "]
    moved = set(mutations.neighbourhood(original, [("A", "48")], mutations.RADIUS))
    shift = mutations.backbone_shift(original, mut, moved)
    assert shift["outside"] < 0.5 and shift["outside"] == pytest.approx(0.0, abs=0.002)
    assert res["rmsd"]["outside"] == pytest.approx(0.0, abs=0.002) and res["rmsd"]["all"] < 0.5
    # oltre 10 Å (fuori da qualunque sfera di ottimizzazione, anche con la catena di Arg più lunga) tutto identico
    near = set(mutations.neighbourhood(original, [("A", "48")], 10.0))
    a, b = _atoms(original), _atoms(mut)
    far = [k for k in a if k in b and ("A", str(k[0])) not in near]
    assert len(far) > 400 and max(float(np.linalg.norm(a[k] - b[k])) for k in far) < 0.002
    assert res["assessment"]["code"] in ("neutral", "uncertain")
    assert res["dope"]["delta_eq"] < mutations.DOPE_MODERATE and res["dope"]["local_residues"] == 15
    assert len(res["models"]) == 6 and sum(1 for m in res["models"] if m["chosen"]) == 2
    assert {m["seed"] for m in res["models"]} == set(mutations.seeds())
    assert res["sites"][0]["rsa_wt"] > 0.2 and res["caveat"] and res["energy"] is None
    files = {f["path"] for f in job["files"]}
    assert {"1UBQ_K48R.pdb", "1UBQ_wt.pdb", "1UBQ_K48R.pdb.quality.json", "1UBQ_wt.pdb.quality.json", "mutate.py",
            "mutate_params.json", "mutate_input.pdb", "mutate_models.json"} <= files
    assert not any(re.fullmatch(r"(wt|mut)_\d\.pdb", f) for f in files)  # i modelli non scelti si tolgono
    text = c.get(f"/api/jobs/{job['id']}/files/1UBQ_K48R.pdb").text
    assert "REMARK 999 BIOLOOM MUTANT OF 1UBQ A:K48R" in text and "NOT A DDG" in text
    assert c.get(f"/api/jobs/{job['id']}/quality/1UBQ_K48R.pdb").json()["structure"]["residues"] == 76
    html = c.get(f"/api/jobs/{job['id']}/report/1UBQ_K48R.pdb")
    assert html.status_code == 200 and "Mutagenesi in silico" in html.text and "Feyfant E." in html.text
    assert html.text.count("<section") == html.text.count("</section>")
    assert c.get(f"/api/jobs/{job['id']}/report/1UBQ_wt.pdb").status_code == 200
    assert c.get(f"/api/jobs/{job['id']}/report/mutate_input.pdb").status_code == 404
    history = c.get("/api/mutations/history", params=UBQ).json()
    assert [(h["id"], h["kind"], h["mutations"]) for h in history] == [(job["id"], "mutate", "A:K48R")]


@requires_modeller
def test_proline_in_the_hydrophobic_core_helix(client_with_ubq):
    """I30P: Ile30 è nel nucleo idrofobico, nell'α-elica 23–34. Probabilmente destabilizzante (al più incerta), con la
    prolina nell'elica fra i motivi."""
    job = wait_job(client_with_ubq, client_with_ubq.post("/api/mutate", json={"source": UBQ, "mutations": "A:I30P"}
                                                         ).json()["id"], timeout=600)
    assert job["status"] == "completed", job.get("error")
    a = job["result"]["assessment"]
    assert a["code"] == "destabilizing" and a["signals"][0]["level"] == "strong"
    assert any("Prolina in un'α-elica" in reason for reason in a["reasons"])
    assert job["result"]["sites"][0]["core"] and job["result"]["dope"]["delta_eq"] > mutations.DOPE_MODERATE


@requires_modeller
def test_alanine_scan_of_three_residues(client_with_ubq):
    c = client_with_ubq
    r = c.post("/api/alascan", json={"source": UBQ, "selection": {"mode": "residues",
                                                                  "residues": "A:48, A:30, A:43, A:10"}})
    assert r.status_code == 200, r.text
    assert r.json()["title"] == "Scansione di alanina · 1UBQ (3 residui)"
    job = wait_job(c, r.json()["id"], timeout=900)
    assert job["status"] == "completed", job.get("error")
    res = job["result"]
    labels = [row["label"] for row in res["rows"]]
    assert sorted(labels) == ["A:I30A", "A:K48A", "A:L43A"] and labels[-1] == "A:K48A"  # dal più destabilizzante
    assert [row["assessment"]["code"] for row in res["rows"][:2]] == ["destabilizing", "destabilizing"]
    assert res["rows"][-1]["assessment"]["code"] in ("neutral", "uncertain")
    scores = [row["assessment"]["score"] for row in res["rows"]]
    assert scores[0] >= scores[1] > scores[2]
    assert [s["resnum"] for s in res["skipped"]] == ["10"] and res["summary"]["counts"]["destabilizing"] == 2
    files = {f["path"] for f in job["files"]}
    assert {"siti/I30A/I30A_wt.pdb", "siti/I30A/I30A_ala.pdb", "alascan.csv", "mutate_input.pdb"} <= files
    assert "siti/I30A/mutate_input.pdb" not in files
    ala = _model(c.app.state.core.jobs.file_path(job["id"], "siti/L43A/L43A_ala.pdb"))
    assert ala["A"][43].get_resname() == "ALA"
    table = list(csv.reader(io.StringIO(c.get(f"/api/jobs/{job['id']}/files/alascan.csv").text)))
    assert [row[0] for row in table[1:]] == labels
    html = c.get(f"/api/jobs/{job['id']}/report")
    assert html.status_code == 200 and "Scansione di alanina" in html.text and "Kortemme T." in html.text
    assert html.text.count("<section") == html.text.count("</section>") and 'id="qualita"' not in html.text


@requires_modeller
def test_interface_mutation_on_the_dimer(client):
    """D58A sull'interfaccia del dimero sintetico: DOPE fra le catene e indicatori d'interfaccia; il mutante ha le due
    catene con i nomi originali."""
    _upload(client, DATA / "ubq_dimer.pdb", "DIMER")
    job = wait_job(client, client.post("/api/mutate", json={"source": {"type": "template", "id": "DIMER"},
                                                            "mutations": "A:D58A"}).json()["id"], timeout=600)
    assert job["status"] == "completed", job.get("error")
    res = job["result"]
    assert res["files"]["mutant"] == "DIMER_A-D58A.pdb"
    assert res["dope"]["interchain_wild_type"] < 0 and res["dope"]["interchain_delta"] is not None
    assert res["sites"][0]["interface"]["delta"] > 1 and res["assessment"]["binding"]["interface"]
    mut = _model(client.app.state.core.jobs.file_path(job["id"], res["files"]["mutant"]))
    assert [c.id for c in mut] == ["A", "B"] and mut["A"][58].get_resname() == "ALA"
    assert mut["B"][58].get_resname() == "ASP"


@requires_modeller
def test_chain_without_identifier_and_a_break(client, tmp_path):
    """Catena senza identificativo con i residui 35–37 mancanti: per MODELLER due tratti, nel mutante la catena torna
    senza nome e l'interruzione resta (nessun legame fra 34 e 38)."""
    path = ubq_variant(tmp_path / "blank_gap.pdb", blank_chain=True, drop=range(35, 38))
    _upload(client, path, "UBQBLANK")
    job = wait_job(client, client.post("/api/mutate", json={"source": {"type": "template", "id": "UBQBLANK"},
                                                            "mutations": "K33A"}).json()["id"], timeout=600)
    assert job["status"] == "completed", job.get("error")
    res = job["result"]
    assert res["notation"] == "K33A" and any("Catena interrotta" in w for w in res["warnings"])
    text = client.get(f"/api/jobs/{job['id']}/files/{res['files']['mutant']}").text
    assert {ln[21] for ln in text.splitlines() if ln.startswith("ATOM")} == {" "}
    mut = _model(client.app.state.core.jobs.file_path(job["id"], res["files"]["mutant"]))
    chain = mut[" "]
    assert chain[33].get_resname() == "ALA" and 35 not in chain
    assert chain[34]["C"] - chain[38]["N"] > 5.0


@requires_modeller
def test_mutate_and_scan_on_a_chain_with_a_two_character_name(client, tmp_path):
    """mmCIF con la catena «AA»: mutagenesi e scansione arrivano in fondo (prima: KeyError 'AA' dopo MODELLER), con
    le etichette della notazione, la catena dei modelli per il 3D e la catena principale ferma fuori dai residui
    ottimizzati."""
    _upload(client, _two_letter_chain(tmp_path / "ubq_aa.cif"), "UBQAA")
    src = {"type": "template", "id": "UBQAA"}
    job = wait_job(client, client.post("/api/mutate", json={"source": src, "mutations": "AA:K48R"}).json()["id"],
                   timeout=600)
    assert job["status"] == "completed", job.get("error")
    res = job["result"]
    assert res["notation"] == "AA:K48R" and res["sites"][0]["label"] == "AA:K48R" and res["sites"][0]["rsa_wt"] > 0.2
    assert res["mutations"][0]["chain"] == "AA" and res["mutations"][0]["model_chain"] == "A"
    assert any("AA→A" in w for w in res["warnings"])
    assert res["rmsd"]["outside"] == pytest.approx(0.0, abs=0.002) and res["rmsd"]["outside_atoms"] < res["rmsd"]["atoms"]
    mut = _model(client.app.state.core.jobs.file_path(job["id"], res["files"]["mutant"]))
    assert mut["A"][48].get_resname() == "ARG"
    scan = wait_job(client, client.post("/api/alascan", json={"source": src, "selection": {
        "mode": "residues", "residues": "AA:48, AA:30"}}).json()["id"], timeout=900)
    assert scan["status"] == "completed", scan.get("error")
    rows = scan["result"]["rows"]
    assert sorted(r["label"] for r in rows) == ["AA:I30A", "AA:K48A"] and scan["result"]["failed"] == []
    assert all(r["site"]["rsa_wt"] is not None for r in rows) and any("AA→A" in w for w in scan["result"]["warnings"])
    i30a = next(r for r in rows if r["label"] == "AA:I30A")
    assert i30a["site"]["core"] and i30a["assessment"]["code"] == "destabilizing"


@requires_modeller
def test_cancelling_a_scan_stops_modeller(client_with_ubq, monkeypatch):
    procs: list[subprocess.Popen] = []
    original = JobManager._register_process

    def register(self, job_id, proc):
        procs.append(proc)
        original(self, job_id, proc)

    monkeypatch.setattr(JobManager, "_register_process", register)
    c = client_with_ubq
    c.put("/api/settings", json={"max_workers": 1})
    jobs = c.app.state.core.jobs
    job_id = c.post("/api/alascan", json={"source": UBQ, "selection": {"mode": "residues",
                                                                       "residues": "A:1-9, A:11-20"}}).json()["id"]
    deadline = time.time() + 300
    while time.time() < deadline and not any(p.poll() is None and any("mutate.py" in str(a) for a in p.args)
                                             for p in procs):
        assert jobs.get(job_id)["status"] in ("queued", "running"), jobs.get(job_id).get("error")
        time.sleep(0.1)
    worker = next(p for p in procs if p.poll() is None and any("mutate.py" in str(a) for a in p.args))
    t0 = time.time()
    jobs.cancel(job_id)
    done = wait_job(c, job_id)
    assert done["status"] == "cancelled" and time.time() - t0 < 10
    assert worker.poll() is not None


@requires_modeller
@requires_openmm
def test_k48r_energy_with_openmm(client_with_ubq):
    """Energia di OpenMM per K48R: quattro minimizzazioni, ΔΔE piccolo (misurato +6 kJ/mol; scarto fra due selvatici
    fino a 36), nessun segnale forte; i modelli minimizzati sono fra i file."""
    job = wait_job(client_with_ubq, client_with_ubq.post("/api/mutate", json={
        "source": UBQ, "mutations": "A:K48R", "energy": True}).json()["id"], timeout=1200)
    assert job["status"] == "completed", job.get("error")
    e = job["result"]["energy"]
    assert e["available"] and e["ddE"] is not None and abs(e["ddE"]) < mutations.ENERGY_STRONG
    assert e["delta_folded"] < -500 and e["delta_reference"] < -500  # composizione diversa: si annulla nel ΔΔE
    assert job["result"]["assessment"]["code"] in ("neutral", "uncertain")
    files = {f["path"] for f in job["files"]}
    assert {"openmm_selvatico/1UBQ_wt_min.pdb", "openmm_mutante/1UBQ_K48R_min.pdb"} <= files
    assert job["result"]["files"]["mutant_minimized"] == "openmm_mutante/1UBQ_K48R_min.pdb"


def test_engine_best_and_finalize(tmp_path):
    rows = [{"role": "wild_type", "dope": -10.0, "file": "wt_1.pdb"}, {"role": "wild_type", "dope": -12.0,
                                                                        "file": "wt_2.pdb"},
            {"role": "mutant", "dope": -9.0, "file": "mut_1.pdb"}, {"role": "mutant", "dope": None, "file": "mut_2.pdb"}]
    for r in rows:
        (tmp_path / r["file"]).write_text("ATOM      1  CA  ALA B   1       0.000   0.000   0.000  1.00  0.00           C\n"
                                          "TER\nEND\n")
    assert engine.best(rows, "wild_type")["file"] == "wt_2.pdb" and engine.best(rows, "mutant")["file"] == "mut_1.pdb"
    renamed = engine.finalize(tmp_path, rows, {"wild_type": "x_wt.pdb", "mutant": "x_mut.pdb"}, {"B": "A"})
    assert renamed == [] and sorted(p.name for p in tmp_path.iterdir()) == ["x_mut.pdb", "x_wt.pdb"]
    assert (tmp_path / "x_wt.pdb").read_text().splitlines()[0][21] == "A"
    assert [r["chosen"] for r in rows] == [False, True, True, False]
    with pytest.raises(AppError):
        engine.best([{"role": "mutant", "dope": None, "file": "x"}], "mutant")
