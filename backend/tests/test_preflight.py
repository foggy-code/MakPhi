"""Controlli preventivi (F8a): regole pure di domain/preflight.py, servizio services/preflight.py e rotta
POST /api/compute/preflight.

Ogni codice ha un caso che lo produce e uno che non lo produce. Licenze, strumenti, spazio libero e servizio QMEAN
sono simulati (monkeypatch, httpx.MockTransport per il worker QMEAN); nessun accesso alla rete. Valori di
riferimento: ubiquitina di lievito su 1UBQ (73 identici su 76, 96,05%), tempi e byte misurati da
`tests/perf_timing.py::test_preflight_calibration` (docs/ARCHITETTURA.md, Prestazioni).
"""

from __future__ import annotations

import threading

import httpx
import pytest

from bioloom.core.errors import AppError
from bioloom.domain import preflight as rules
from bioloom.engines import qmean_local, remote, search, tools
from bioloom.services import preflight as preflight_module
from bioloom.services.licenses import LicenseService
from bioloom.services.preflight import PreflightService

from .conftest import DATA, HUMAN_UB, YEAST_UB, wait_job
from .test_alphafold import af_pdb
from .test_disorder import SNCA, SNCA_TAIL

GIB = 1024 ** 3
MODELLER_OK = {"configured": True, "source": "file", "masked": "MOD•••••XX", "available": True, "version": "10.8",
               "message": None}
PROSA_OK = {"installed": True, "license": True, "error": None, "program": "ProSa2003", "expires": "2030",
            "mac_required": "02:42:ac:11:00:02", "mac_current": "02:42:ac:11:00:02", "mac_ok": True}

# Sostituzioni conservative (punteggio BLOSUM62 ≥ 0): il Smith-Waterman allinea ancora tutta la sequenza
SIMILAR = {"A": "S", "R": "K", "N": "S", "D": "E", "C": "A", "Q": "E", "E": "D", "G": "A", "H": "Y", "I": "V",
           "L": "M", "K": "R", "M": "L", "F": "Y", "P": "S", "S": "T", "T": "S", "W": "Y", "Y": "F", "V": "I"}


def mutant(seq: str, keep_every: int = 4) -> str:
    """Una posizione su `keep_every` identica, le altre con una sostituzione conservativa: 1UBQ al 25% di identità."""
    return "".join(c if i % keep_every == 0 else SIMILAR[c] for i, c in enumerate(seq))


def codes(result: dict, level: str | None = None) -> list[str]:
    return [c["code"] for c in result["checks"] if level is None or c["level"] == level]


def by_code(result: dict, code: str) -> dict:
    return next(c for c in result["checks"] if c["code"] == code)


# ---------------------------------------------------------------------------------------------- fixture
@pytest.fixture
def env(monkeypatch):
    """Ambiente deterministico: MODELLER con licenza valida, PROCHECK e ProSA utilizzabili, dischi con 50 GiB liberi
    (dati e scratch su dischi diversi), nessun job attivo. I test cambiano solo ciò che controllano."""
    state = {"modeller": dict(MODELLER_OK), "prosa": dict(PROSA_OK), "free": {"data": 50 * GIB, "scratch": 50 * GIB},
             "tools": {"procheck": "/opt/procheck/procheck.scr", "prosa": "/opt/prosa/bin/prosa2003",
                       "blastp": "/usr/bin/blastp", "mmseqs": "/usr/bin/mmseqs"}}
    real_status = LicenseService.status

    def status(self):
        out = real_status(self)
        out["modeller"] = dict(state["modeller"])
        return out

    monkeypatch.setattr(LicenseService, "status", status)
    monkeypatch.setattr(LicenseService, "prosa_status", lambda self: dict(state["prosa"]))
    real_find = tools.find
    monkeypatch.setattr(tools, "find", lambda key: state["tools"].get(key) if key in state["tools"] else real_find(key))

    def free(path):
        return state["free"]["scratch" if ".scratch" in str(path) else "data"]

    monkeypatch.setattr(preflight_module, "disk_free", free)
    monkeypatch.setattr(preflight_module, "same_disk", lambda a, b: False)
    return state


def run(client, kind: str, params: dict, **context) -> dict:
    r = client.post("/api/compute/preflight", json={"kind": kind, "params": params, "context": context})
    assert r.status_code == 200, r.text
    return r.json()


def model_params(target: str = YEAST_UB, templates=None, **options) -> dict:
    return {"target": {"name": "yeast_ub", "sequence": target},
            "templates": templates or [{"id": "1UBQ", "chains": ["A"]}],
            "options": {"models": 5, "refinement": "fast", **options}, "quality": "best", "extended_quality": True}


# ---------------------------------------------------------------------------------------------- regole pure
def test_check_ordering_and_counts():
    items = [rules.check("info", "a", "x"), rules.check("error", "b", "y"), rules.check("warning", "c", "z"),
             rules.check("error", "d", "w")]
    assert [c["code"] for c in rules.ordered(items)] == ["b", "d", "c", "a"]
    assert rules.counts(items) == {"error": 2, "warning": 1, "info": 1}
    assert rules.check("info", "a", "x", template=None) == {"level": "info", "code": "a", "message": "x",
                                                           "suggestion": None}
    with pytest.raises(ValueError):
        rules.check("fatal", "a", "x")


@pytest.mark.parametrize("status,code,level", [
    ({"available": True, "configured": True}, None, None),
    ({"available": False, "configured": False, "message": "Invalid license key: XXXX"}, "modeller_license_missing",
     "error"),
    ({"available": False, "configured": True, "message": "check_lice_E> Invalid license key"},
     "modeller_license_invalid", "error"),
    ({"available": False, "configured": False, "message": "No module named 'modeller'"}, "modeller_not_installed",
     "error"),
], ids=["disponibile", "senza-chiave", "chiave-rifiutata", "non-installato"])
def test_modeller_checks(status, code, level):
    out = rules.modeller_checks(status)
    assert [(c["code"], c["level"]) for c in out] == ([] if code is None else [(code, level)])
    if code == "modeller_license_invalid":
        assert "check_lice_E" in out[0]["message"]


def test_modeller_optional_for_dope():
    [c] = rules.modeller_checks({"available": False, "configured": False}, required=False)
    assert c["code"] == "dope_unavailable" and c["level"] == "warning"
    assert rules.modeller_checks({"available": True}, required=False) == []


def test_similarity_rost_curve_and_baker_sali():
    """Zona crepuscolare dalla curva di Rost (76 residui: 33,2%; 300 residui: 20,0%) e bassa accuratezza sotto il 30%
    (Baker & Šali 2001)."""
    assert template_rank_threshold(76) == pytest.approx(33.2, abs=0.05)
    assert template_rank_threshold(300) == pytest.approx(20.0, abs=0.1)
    ubq = rules.similarity_checks([{"label": "1UBQ:A", "identity": 96.05, "aligned": 76}], 100.0)
    assert ubq == []
    low = rules.similarity_checks([{"label": "X:A", "identity": 25.0, "aligned": 76}], 100.0)
    assert [(c["code"], c["level"]) for c in low] == [("twilight_zone", "warning")]
    assert "33,2%" in low[0]["message"] and low[0]["template"] == "X:A"
    long = rules.similarity_checks([{"label": "Y:A", "identity": 25.0, "aligned": 300}], 100.0)
    assert [(c["code"], c["level"]) for c in long] == [("low_identity", "info")]
    assert rules.similarity_checks([{"label": "Z:A", "identity": 31.0, "aligned": 300}], 100.0) == []
    assert rules.similarity_checks([{"label": "N:A", "identity": None, "aligned": None}], None) == []


def template_rank_threshold(n: int) -> float:
    from bioloom.domain import template_rank

    return template_rank.rost_threshold(n)


def test_low_coverage_threshold():
    assert [c["code"] for c in rules.similarity_checks([], 39.9)] == ["low_coverage"]
    assert rules.similarity_checks([], 40.0) == []


def test_rank_checks():
    hit = {"template_id": "2ABC_A", "rank_score": 35.2, "rank_reasons": [
        {"key": "identity", "label": "Identità", "score": 20.0, "weight": 30},
        {"key": "coverage", "label": "Copertura del bersaglio", "score": 45.0, "weight": 30},
        {"key": "ligands", "label": "Ligandi", "score": None, "weight": 0}]}
    [c] = rules.rank_checks([hit])
    assert c["code"] == "template_score_poor" and c["level"] == "warning"
    assert "Identità 20/100" in c["message"] and "Copertura del bersaglio 45/100" in c["message"]
    assert rules.rank_checks([{**hit, "rank_score": 64.0}]) == []
    assert rules.rank_checks([{"template_id": "old"}]) == []  # ricerca di una versione precedente, senza punteggio


def test_plddt_checks():
    base = {"template": "AF-P0CG48-F1", "chains": ["A"], "threshold": 70.0, "total": 76, "empty": []}
    assert [c["code"] for c in rules.plddt_checks([{**base, "kept": 25}])] == ["plddt_too_few"]
    assert [c["code"] for c in rules.plddt_checks([{**base, "kept": 0, "empty": ["A"]}])] == ["plddt_too_few"]
    assert [(c["code"], c["level"]) for c in rules.plddt_checks([{**base, "kept": 35}])] == [("plddt_trim_most",
                                                                                              "warning")]
    [info] = rules.plddt_checks([{**base, "kept": 65, "ranges": "1–5:A, 71–76:A"}])
    assert info["code"] == "plddt_trim" and "1–5:A, 71–76:A" in info["message"]
    assert rules.plddt_checks([{**base, "kept": 76}]) == []
    untrimmed = {"template": "AF-P0CG48-F1", "chains": ["A"], "threshold": None, "total": 76}
    assert [c["code"] for c in rules.plddt_checks([{**untrimmed, "low": 11}])] == ["plddt_untrimmed"]
    assert rules.plddt_checks([{**untrimmed, "low": 0}]) == []


def test_coverage_checks_group_and_suggestion():
    assessment = {"warnings": [
        {"kind": "disorder_uncovered", "tone": "warn", "start": 77, "end": 166, "text": "scoperta"},
        {"kind": "domain_partial", "tone": "warn", "start": 1, "end": 90, "text": "in parte"},
        {"kind": "disorder_covered", "tone": "info", "start": 10, "end": 50, "text": "coperta"}],
        "suggestion": "Modella per domini"}
    out = rules.coverage_checks(assessment)
    assert [(c["code"], c["level"], c["group"]) for c in out] == [
        ("disorder_uncovered", "warning", "coverage"), ("domain_partial", "warning", "coverage"),
        ("disorder_covered", "info", "coverage")]
    assert out[0]["suggestion"] == "Modella per domini" and out[1]["suggestion"] is None
    assert rules.coverage_checks(None) == [] and rules.coverage_checks({"warnings": []}) == []


def test_optional_tool_checks():
    both = {"procheck": True, "prosa": True}
    assert rules.optional_tool_checks(both, {"procheck": None, "prosa": None}) == []
    one = rules.optional_tool_checks(both, {"procheck": None, "prosa": "manca la licenza."})
    assert [(c["code"], c["level"]) for c in one] == [("prosa_unavailable", "info")]
    none = rules.optional_tool_checks(both, {"procheck": "non installato.", "prosa": "manca la licenza."})
    assert [(c["code"], c["level"]) for c in none] == [("procheck_unavailable", "warning"),
                                                       ("prosa_unavailable", "warning")]
    only = rules.optional_tool_checks({"procheck": True, "prosa": False}, {"procheck": "non installato.",
                                                                          "prosa": "manca la licenza."})
    assert [(c["code"], c["level"]) for c in only] == [("procheck_unavailable", "warning")]


def test_runtime_model_matches_the_measurements():
    """Stime contro le misure (ARCHITETTURA.md, Prestazioni): job model di 5 modelli con la qualità estesa sul migliore
    13,89 s; elemento di pipeline (3 modelli, qualità senza PROCHECK e ProSA) 9,13 s; costruzione di 2 modelli
    "thorough" 20,63 s e di 1 modello su 304 residui 11,25 s (`test_preflight_calibration`)."""
    assert rules.modeling_seconds(76, 5, "fast", "best", True) == pytest.approx(13.89, rel=0.05)
    assert rules.modeling_seconds(76, 3, "fast", "best", False) == pytest.approx(9.13, rel=0.05)
    assert rules.building_seconds(76, 2, "thorough") == pytest.approx(20.63, rel=0.1)
    assert rules.building_seconds(304, 1, "fast") == pytest.approx(11.25, rel=0.05)
    assert rules.building_seconds(76, 1, "normal") == rules.building_seconds(76, 1, "fast")  # stesso md_level
    # qualità su tutti i modelli: 3 insieme, quindi 2 turni per 5 modelli
    assert rules.modeling_seconds(76, 5, quality="all", workers=4) - rules.modeling_seconds(76, 5, quality="none") \
        == pytest.approx(2 * rules.T_QUALITY)


def test_runtime_checks_and_timeout():
    est = rules.estimate(14.0, detail="76 residui")
    assert est["low"] == 7.0 and est["high"] == 28.0
    out = rules.runtime_checks(est, process_seconds=3000, timeout_seconds=7200)
    assert [c["code"] for c in out] == ["estimated_time"] and "circa 14 s (da 7 s a 28 s)" in out[0]["message"]
    risky = rules.runtime_checks(est, process_seconds=3700, timeout_seconds=7200)
    assert [c["code"] for c in risky] == ["estimated_time", "timeout_risk"]
    assert rules.fmt_seconds(45) == "45 s" and rules.fmt_seconds(3 * 60) == "3 min"
    assert rules.fmt_seconds(80 * 60) == "1 h 20 min" and rules.fmt_seconds(5 * 86400) == "5 giorni"


def test_disk_estimates_match_the_measurements():
    """Byte misurati: 5 modelli con l'analisi estesa del migliore, 76 residui 3103 KiB di risultati e 855 KiB di
    intermedi; 304 residui 4861 e 3168 KiB (la stima degli intermedi è prudente)."""
    results, scratch = rules.modeling_bytes(76, 5, 1)
    assert results / 1024 == pytest.approx(3103, rel=0.05)
    assert (scratch - results) / 1024 == pytest.approx(855, rel=0.05)
    results, scratch = rules.modeling_bytes(304, 5, 1)
    assert results / 1024 == pytest.approx(4861, rel=0.05)
    assert 3168 <= (scratch - results) / 1024 <= 3168 * 1.15


def test_disk_checks():
    need = 3 * 1024 ** 2
    assert rules.disk_checks(data_free=50 * GIB, data_need=need, scratch_free=50 * GIB, scratch_need=need) == []
    full = rules.disk_checks(data_free=1024 ** 2, data_need=need, scratch_free=50 * GIB, scratch_need=need)
    assert [(c["code"], c["level"]) for c in full] == [("disk_full_data", "error")]
    low = rules.disk_checks(data_free=500 * 1024 ** 2, data_need=need, scratch_free=200 * 1024 ** 2,
                            scratch_need=need)
    assert [(c["code"], c["level"]) for c in low] == [("disk_low_data", "warning"), ("disk_low_scratch", "warning")]
    # sotto 10 volte la stima anche con più di 1 GiB libero
    big = rules.disk_checks(data_free=5 * GIB, data_need=GIB, scratch_free=None, scratch_need=GIB)
    assert [c["code"] for c in big] == ["disk_low_data"]
    # stesso disco: una sola voce con la somma
    same = rules.disk_checks(data_free=5 * 1024 ** 2, data_need=need, scratch_free=5 * 1024 ** 2, scratch_need=need,
                             same_disk=True)
    assert [c["code"] for c in same] == ["disk_full_data"] and "stesso disco" in same[0]["message"]


def test_qmean_checks():
    ok = {"available": True, "uniclust": {"valid": True, "prefix": "UniRef30_2020_06"}}
    common = {"remote_consent": False, "email": False, "fallback": False}
    assert [c["code"] for c in rules.qmean_checks(mode="local", status=ok, memory_problem=None, **common)] == \
        ["qmean_local"]
    busy = rules.qmean_checks(mode="local", status={**ok, "busy": True}, memory_problem=None, **common)
    assert [c["code"] for c in busy] == ["qmean_local", "qmean_busy"]
    memory = rules.qmean_checks(mode="local", status=ok, memory_problem="memoria insufficiente", **common)
    assert [(c["code"], c["level"]) for c in memory] == [("qmean_memory", "error")]
    fallback = rules.qmean_checks(mode="local", status=ok, memory_problem="memoria insufficiente", fallback=True,
                                  remote_consent=True, email=True)
    assert [(c["code"], c["level"]) for c in fallback] == [("qmean_memory", "warning")]
    no_consent = rules.qmean_checks(mode="local", status={"available": False, "message": "non attivo"},
                                    memory_problem=None, fallback=True, remote_consent=False, email=True)
    assert [(c["code"], c["level"]) for c in no_consent] == [("qmean_unavailable", "error")]
    assert "consenso" in no_consent[0]["message"]
    down = rules.qmean_checks(mode="local", status={"available": False, "message": "non attivo"},
                              memory_problem=None, **common)
    assert [(c["code"], c["level"]) for c in down] == [("qmean_unavailable", "error")]
    assert [c["code"] for c in rules.qmean_checks(mode="remote", status=None, memory_problem=None, **common)] == \
        ["qmean_remote_disabled"]
    assert [c["code"] for c in rules.qmean_checks(mode="remote", status=None, memory_problem=None, fallback=False,
                                                  remote_consent=True, email=False)] == ["email_required"]
    assert [c["code"] for c in rules.qmean_checks(mode="remote", status=None, memory_problem=None, fallback=False,
                                                  remote_consent=True, email=True)] == ["qmean_remote"]


@pytest.mark.parametrize("engine,facts,expected", [
    ("rcsb", {}, []),
    ("local", {"blastp": False, "pdbaa": True}, [("blast_missing", "error")]),
    ("local", {"blastp": True, "pdbaa": False}, [("pdbaa_missing", "error")]),
    ("local", {"blastp": True, "pdbaa": True}, []),
    ("mmseqs", {"mmseqs": False}, [("mmseqs_missing", "error")]),
    ("mmseqs", {"mmseqs": True, "mmseqs_db": False}, [("mmseqs_database_missing", "error")]),
    ("mmseqs", {"mmseqs": True, "mmseqs_db": True, "mmseqs_index": False}, [("mmseqs_no_index", "info")]),
    ("mmseqs", {"mmseqs": True, "mmseqs_db": True, "mmseqs_index": True}, []),
    ("ncbi", {"email": False}, [("remote_ncbi", "info"), ("ncbi_email", "info")]),
    ("ncbi", {"email": True}, [("remote_ncbi", "info")]),
    ("alphafold", {"accession": "P0CG48"}, [("remote_alphafold", "info")]),
    ("alphafold", {"accession": ""}, [("alphafold_blast", "info")]),
])
def test_search_checks(engine, facts, expected):
    assert [(c["code"], c["level"]) for c in rules.search_checks(engine, facts)] == expected


def test_search_times():
    assert rules.search_time("mmseqs", {"mmseqs_index": True}) == (2.3, 12.0)
    assert rules.search_time("mmseqs", {"mmseqs_index": False}) == (12.0, 22.0)
    assert rules.search_time("alphafold", {"accession": ""}) == (60.0, 300.0)
    assert rules.mixed_search_time([(0.0, 10.0), (10.0, 30.0)]) == (5.0, 20.0)
    assert rules.mixed_search_time([]) is None


def test_pipeline_remote_checks():
    """Linee guida di NCBI: oltre 50 ricerche di sera o nel fine settimana; una richiesta ogni 10 s al massimo."""
    many = rules.pipeline_remote_checks(engine="ncbi", items=51, ncbi_searches=51, parallel=1)
    assert [(c["code"], c["level"]) for c in many] == [("ncbi_many_searches", "warning")]
    assert rules.pipeline_remote_checks(engine="ncbi", items=50, ncbi_searches=50, parallel=1) == []
    parallel = rules.pipeline_remote_checks(engine="ncbi", items=10, ncbi_searches=10, parallel=2)
    assert [c["code"] for c in parallel] == ["ncbi_parallel"]
    assert rules.pipeline_remote_checks(engine="rcsb", items=500, ncbi_searches=500, parallel=4) == []
    assert rules.pipeline_remote_checks(engine="alphafold", items=60, ncbi_searches=0, parallel=2) == []
    [af] = rules.pipeline_alphafold_checks(3, 1)
    assert af["code"] == "pipeline_alphafold" and "2 con l'accessione" in af["message"] and af["suggestion"]
    assert rules.pipeline_alphafold_checks(3, 0)[0]["suggestion"] is None


def test_pipeline_estimate():
    one = rules.pipeline_estimate(1, residues=76, models=3, refinement="fast", quality=True, extended=False,
                                  search=None, parallel=2)
    assert one["seconds"] == pytest.approx(9.13, rel=0.05)
    four = rules.pipeline_estimate(4, residues=76, models=3, refinement="fast", quality=True, extended=False,
                                   search=(2.0, 15.0), parallel=2)
    assert four["seconds"] == pytest.approx(2 * (rules.modeling_seconds(76, 3, "fast", "best", False) + 8.5), abs=0.05)
    assert "4 sequenze" in four["detail"] and "2 in parallelo" in four["detail"]
    assert "NCBI" in four["basis"]


def test_other_rules():
    assert [c["code"] for c in rules.queue_checks(3, 3)] == ["queue_busy"]
    assert rules.queue_checks(2, 3) == []
    assert [c["code"] for c in rules.single_model_checks(1)] == ["single_model"]
    assert rules.single_model_checks(5) == []
    assert rules.template_availability("1UBQ", in_library=True, downloadable="RCSB") == []
    assert [c["code"] for c in rules.template_availability("9ZZZ", in_library=False, downloadable="RCSB")] == \
        ["template_download"]
    assert [c["code"] for c in rules.template_availability("MIO", in_library=False, downloadable=None)] == \
        ["template_missing"]
    [dis] = rules.pipeline_disorder_checks(["a", "b"], 2, 2)
    assert dis["code"] == "pipeline_disorder" and "a, b" in dis["message"]
    assert rules.pipeline_disorder_checks([], 3, 3) == []
    assert [c["code"] for c in rules.pipeline_disorder_checks([], 3, 10)] == ["pipeline_disorder_partial"]


# ---------------------------------------------------------------------------------------------- modellazione
def test_model_yeast_ubiquitin_on_1ubq_is_clean(client_with_ubq, env):
    """Il caso di riferimento non ha errori né avvisi: identità 96,05%, copertura 100%, nessun disordine; resta la
    stima del tempo (5 modelli: circa 14 s come la misura)."""
    out = run(client_with_ubq, "model", model_params())
    assert out["ok"] is True and out["counts"] == {"error": 0, "warning": 0, "info": 1}, out["checks"]
    assert codes(out) == ["estimated_time"]
    assert out["estimate"]["seconds"] == pytest.approx(13.89, rel=0.05)
    cov = out["coverage"]
    assert cov["combined"]["coverage"] == 100.0
    [t] = cov["templates"]
    assert t["identity"] == pytest.approx(96.1, abs=0.1) and t["aligned"] == 76
    assert client_with_ubq.get("/api/jobs").json() == []  # nessun job avviato


def test_model_modeller_license(client_with_ubq, env):
    env["modeller"] = {"configured": False, "available": False, "message": "Invalid license key: XXXX"}
    out = run(client_with_ubq, "model", model_params())
    assert out["ok"] is False and codes(out, "error") == ["modeller_license_missing"]
    assert "Impostazioni → Licenze" in by_code(out, "modeller_license_missing")["suggestion"]
    env["modeller"] = {"configured": True, "available": False, "message": "check_lice_E> Invalid license key"}
    assert codes(run(client_with_ubq, "model", model_params()), "error") == ["modeller_license_invalid"]


def test_model_twilight_zone(client_with_ubq, env):
    """1UBQ al 25% di identità sulla lunghezza intera: sotto la curva di Rost per 76 residui (33,2%)."""
    target = mutant(HUMAN_UB)
    assert sum(a == b for a, b in zip(target, HUMAN_UB)) == 19
    out = run(client_with_ubq, "model", model_params(target))
    [c] = [c for c in out["checks"] if c["code"] == "twilight_zone"]
    assert c["level"] == "warning" and c["template"] == "1UBQ:A" and "curva della zona crepuscolare" in c["message"]
    assert out["ok"] is True  # un avviso chiede conferma, non blocca


def test_model_low_coverage_and_uncovered_disorder(client_with_ubq, env):
    target = HUMAN_UB + SNCA_TAIL * 3  # 211 residui, 76 coperti (36%), coda disordinata scoperta
    out = run(client_with_ubq, "model", model_params(target))
    assert "low_coverage" in codes(out, "warning")
    dis = by_code(out, "disorder_uncovered")
    assert dis["level"] == "warning" and dis["group"] == "coverage" and dis["suggestion"]
    assert out["coverage"]["combined"]["warnings"][0]["kind"] == "disorder_uncovered"


def test_model_with_a_pir_alignment(client_with_ubq, env):
    from bioloom.domain import sequences

    pir = sequences.format_pir([sequences.template_entry("1UBQ_A", HUMAN_UB, "1", "A", "76", "A"),
                                sequences.target_entry("yeast", YEAST_UB)])
    params = {"alignment": pir, "templates": [{"id": "1UBQ", "chains": ["A"]}], "options": {"models": 5},
              "quality": "best", "extended_quality": False}
    out = run(client_with_ubq, "model", params)
    assert out["ok"] and "twilight_zone" not in codes(out)
    assert out["coverage"]["templates"][0]["identity"] == pytest.approx(96.1, abs=0.1)
    bad = run(client_with_ubq, "model", {**params, "templates": [{"id": "1UBQ", "chains": ["A"]}] * 2})
    assert "invalid_pir" in codes(bad, "error")
    garbage = run(client_with_ubq, "model", {**params, "alignment": ">P1;x\nstructureX:x:1:A:3:A::::\nAAA*\n"})
    assert codes(garbage, "error") == ["invalid_pir"]


def test_model_two_chain_pir_uses_the_pir_columns(client, env):
    """Bersaglio a due catene: la copertura non si valuta (nota), l'identità sì dalle colonne del PIR (32 identici su
    152, 21,1%: sotto la curva di Rost per 152 residui, 24,1%)."""
    from bioloom.domain import sequences

    with open(DATA / "ubq_dimer.pdb", "rb") as fh:
        assert client.post("/api/templates/upload", files={"file": ("DIMR.pdb", fh, "chemical/x-pdb")}).status_code \
            == 200
    target = mutant(HUMAN_UB, 5)
    pir = sequences.format_pir([
        sequences.template_entry("DIMR_AB", HUMAN_UB + "/" + HUMAN_UB, "1", "A", "76", "B"),
        sequences.target_entry("t", target + "/" + target)])
    out = run(client, "model", {"alignment": pir, "templates": [{"id": "DIMR", "chains": ["A", "B"]}],
                                "options": {"models": 1}})
    assert out["coverage"]["note"].startswith("Bersaglio con più catene")
    tw = by_code(out, "twilight_zone")
    assert "21,1%" in tw["message"] and "152 residui allineati" in tw["message"] and tw["level"] == "warning"


def test_model_complex_mode_is_checked_like_the_launch_route(client, env):
    """Modalità Complesso (F5.3): nessun bersaglio né PIR ma `complex`; i tempi contano tutte le catene e gli errori
    del complesso sono quelli della rotta di avvio."""
    with open(DATA / "ubq_dimer.pdb", "rb") as fh:
        assert client.post("/api/templates/upload", files={"file": ("DIMR.pdb", fh, "chemical/x-pdb")}).status_code \
            == 200
    params = {"target": None, "templates": [{"id": "DIMR", "chains": ["A", "B"]}],
              "options": {"models": 1, "symmetry": True},
              "complex": {"name": "ubd", "chains": [{"template_chain": "A", "sequence": YEAST_UB},
                                                    {"template_chain": "B", "same_as": "A"}]}}
    out = run(client, "model", params)
    assert out["ok"], out["checks"]
    assert "152 residui" in out["estimate"]["detail"]
    incomplete = {**params, "complex": {"name": "ubd", "chains": [{"template_chain": "A", "sequence": YEAST_UB}]}}
    bad = run(client, "model", incomplete)
    assert not bad["ok"] and by_code(bad, "invalid_complex")["level"] == "error"
    launch = client.post("/api/model", json=incomplete)
    assert launch.status_code == 400 and launch.json()["error"]["code"] == "invalid_complex"


def test_model_templates_missing_or_downloadable(client_with_ubq, env, monkeypatch):
    def no_download(*_a, **_k):
        raise AssertionError("i controlli non devono scaricare nulla")

    monkeypatch.setattr(remote, "rcsb_download", no_download)
    out = run(client_with_ubq, "model", model_params(templates=[{"id": "1UBQ", "chains": ["A"]},
                                                                {"id": "9ZZZ", "chains": ["A"]}]))
    assert by_code(out, "template_download")["level"] == "info" and "RCSB" in by_code(out, "template_download")["message"]
    missing = run(client_with_ubq, "model", model_params(templates=[{"id": "NONESISTE", "chains": ["A"]}]))
    assert codes(missing, "error") == ["template_missing"]
    chain = run(client_with_ubq, "model", model_params(templates=[{"id": "1UBQ", "chains": ["Q"]}]))
    assert codes(chain, "error") == ["template_chain_missing"] and "disponibili: A" in chain["checks"][0]["message"]
    none = run(client_with_ubq, "model", {**model_params(), "templates": []})
    assert codes(none, "error") == ["bad_request"]


def test_model_validation_matches_the_launch_route(client_with_ubq, env):
    """Stessa validazione dell'avvio: le richieste che la rotta rifiuta sono errori dei controlli, con lo stesso
    messaggio."""
    cases = [
        model_params(models=0),
        model_params(refinement="lentissimo"),
        model_params("A" * 5001),
        model_params(""),
        model_params(ligands=[{"template": "2XYZ", "chain": "A", "resnum": "101", "resname": "ZN"}]),
    ]
    for body in cases:
        out = run(client_with_ubq, "model", body)
        launch = client_with_ubq.post("/api/model", json=body)
        errors = [c for c in out["checks"] if c["level"] == "error"]
        assert errors, body
        if launch.status_code == 200:  # la rotta accoda e l'handler rifiuta: stesso messaggio nel job
            job = wait_job(client_with_ubq, launch.json()["id"])
            assert job["status"] == "failed" and any(e["message"] in job["error"] for e in errors)
        else:
            assert launch.status_code == 400
            assert launch.json()["error"]["message"] in [e["message"] for e in errors]


def test_model_alphafold_trim(client, env):
    """Modello AlphaFold di prova (pLDDT 40 su 1–5 e 30 su 71–76, 90 altrove): ritaglio a 70 → 11 residui tolti
    (informazione), come la modellazione; senza ritaglio un avviso; con 90 solo su 1–25 il ritaglio lascia 25 residui
    (errore, minimo 30); con 90 su 1–35 toglie più della metà (avviso)."""
    def upload(name, value=None):
        text = af_pdb() if value is None else af_pdb(value)
        r = client.post("/api/templates/upload", files={"file": (f"{name}.pdb", text.encode(), "chemical/x-pdb")})
        assert r.status_code == 200 and r.json()["predicted"] is True, r.text

    upload("AF-P0CG48-F1")
    params = model_params(HUMAN_UB, templates=[{"id": "AF-P0CG48-F1", "chains": ["A"]}])
    trimmed = run(client, "model", params)
    info = by_code(trimmed, "plddt_trim")
    assert info["level"] == "info" and "11 residui" in info["message"] and "1–5:A, 71–76:A" in info["message"]
    assert trimmed["coverage"]["combined"]["covered"] == [[6, 70]]
    off = run(client, "model", model_params(HUMAN_UB, templates=[{"id": "AF-P0CG48-F1", "chains": ["A"]}],
                                            plddt_trim=False))
    assert by_code(off, "plddt_untrimmed")["level"] == "warning" and "plddt_trim" not in codes(off)
    upload("AF-P0CG47-F1", lambda r: 90.0 if r <= 25 else 30.0)
    few = run(client, "model", model_params(HUMAN_UB, templates=[{"id": "AF-P0CG47-F1", "chains": ["A"]}]))
    assert "plddt_too_few" in codes(few, "error") and "ne restano 25 su 76" in by_code(few, "plddt_too_few")["message"]
    upload("AF-P62987-F1", lambda r: 90.0 if r <= 35 else 30.0)
    most = run(client, "model", model_params(HUMAN_UB, templates=[{"id": "AF-P62987-F1", "chains": ["A"]}]))
    assert by_code(most, "plddt_trim_most")["level"] == "warning"


def test_model_timeout_risk(client_with_ubq, env):
    client_with_ubq.put("/api/settings", json={"job_timeout_minutes": 1})
    risky = run(client_with_ubq, "model", model_params(models=30))
    assert by_code(risky, "timeout_risk")["level"] == "warning"
    assert "timeout_risk" not in codes(run(client_with_ubq, "model", model_params(models=5)))


def test_model_disk_space(client_with_ubq, env):
    env["free"]["data"] = 1024 ** 2
    assert codes(run(client_with_ubq, "model", model_params()), "error") == ["disk_full_data"]
    env["free"]["data"] = 500 * 1024 ** 2
    out = run(client_with_ubq, "model", model_params())
    assert out["ok"] and codes(out, "warning") == ["disk_low_data"]
    env["free"] = {"data": 50 * GIB, "scratch": 2 * 1024 ** 2}
    assert codes(run(client_with_ubq, "model", model_params()), "error") == ["disk_full_scratch"]


def test_model_optional_tools(client_with_ubq, env):
    env["prosa"] = {**PROSA_OK, "license": False}
    out = run(client_with_ubq, "model", model_params())
    assert [(c["code"], c["level"]) for c in out["checks"] if c["code"].endswith("_unavailable")] == \
        [("prosa_unavailable", "info")]
    env["prosa"] = {**PROSA_OK, "mac_ok": False, "mac_current": "02:42:ac:11:00:09"}
    env["tools"]["procheck"] = None
    both = run(client_with_ubq, "model", model_params())
    assert codes(both, "warning") == ["procheck_unavailable", "prosa_unavailable"]
    assert "02:42:ac:11:00:09" in by_code(both, "prosa_unavailable")["message"]
    basic = run(client_with_ubq, "model", {**model_params(), "extended_quality": False})
    assert "procheck_unavailable" not in codes(basic)


def test_model_queue_and_single_model(client_with_ubq, env, monkeypatch):
    core = client_with_ubq.app.state.core
    monkeypatch.setattr(core.jobs, "count_active", lambda: 99)
    out = run(client_with_ubq, "model", model_params(models=1))
    assert "queue_busy" in codes(out, "info") and "single_model" in codes(out, "info")


def test_model_template_score_from_the_search(client_with_ubq, env, monkeypatch):
    """Con la ricerca da cui vengono i template: un template in fascia scarsa è un avviso; una ricerca di un'altra
    sequenza non conta."""
    hit = {"template_id": "1UBQ_A", "pdb_id": "1UBQ", "chain": "A", "identity": 96.1, "coverage": 100.0,
           "evalue": 1e-40, "resolution": 1.8, "query_start": 1, "query_end": 76, "rank_score": 30.0,
           "rank_reasons": [{"key": "identity", "label": "Identità", "score": 10.0, "weight": 30}]}
    monkeypatch.setattr(search, "search", lambda ctx, sequence, **_k: {"engine": "rcsb", "hits": [dict(hit)],
                                                                       "query_length": 76})
    job = client_with_ubq.post("/api/search", json={"query": {"name": "y", "sequence": YEAST_UB}}).json()
    assert wait_job(client_with_ubq, job["id"])["status"] == "completed"
    out = run(client_with_ubq, "model", model_params(), search_job=job["id"])
    assert by_code(out, "template_score_poor")["template"] == "1UBQ_A"
    other = run(client_with_ubq, "model", model_params(HUMAN_UB), search_job=job["id"])
    assert "template_score_poor" not in codes(other)
    gone = run(client_with_ubq, "model", model_params(), search_job="inesistente")
    assert by_code(gone, "check_skipped")["level"] == "info"


def test_model_domains_need_a_valid_accession(client_with_ubq, env):
    out = run(client_with_ubq, "model", model_params(), accession="non-valida")
    assert by_code(out, "invalid_accession")["level"] == "info" and out["ok"]


def test_concurrent_preflights_agree(client_with_ubq, env):
    """Controlli in parallelo (Modellazione e procedura guidata aperte insieme) sullo stesso template senza analisi in
    cache: stessi esiti."""
    core = client_with_ubq.app.state.core
    (core.paths.templates / "1UBQ.analysis.json").unlink(missing_ok=True)
    results, errors = [], []

    def work():
        try:
            results.append(PreflightService(core).run("model", model_params(mutant(HUMAN_UB)), {}))
        except Exception as exc:  # riportata dal test principale
            errors.append(exc)

    threads = [threading.Thread(target=work) for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(60)
    assert not errors and len(results) == 6
    assert all(r["checks"] == results[0]["checks"] for r in results)
    assert "twilight_zone" in codes(results[0])


def test_unknown_kind_and_invalid_params(client, env):
    assert client.post("/api/compute/preflight", json={"kind": "boh", "params": {}}).status_code == 422
    out = run(client, "search", {"query": {"sequence": YEAST_UB}, "max_hits": 1000})
    assert out["ok"] is False and codes(out) == ["validation_error"] and "max_hits" in out["checks"][0]["message"]
    with pytest.raises(AppError):
        PreflightService(client.app.state.core).run("boh", {})


# ---------------------------------------------------------------------------------------------- altri calcoli
def test_align2d_and_loop(client_with_ubq, env):
    ok = run(client_with_ubq, "align2d", {"target": {"name": "y", "sequence": YEAST_UB},
                                          "templates": [{"id": "1UBQ", "chains": ["A"]}]})
    assert ok["ok"] and ok["counts"]["warning"] == 0
    env["modeller"] = {"configured": False, "available": False}
    assert codes(run(client_with_ubq, "align2d", {"target": {"name": "y", "sequence": YEAST_UB},
                                                  "templates": [{"id": "1UBQ", "chains": ["A"]}]}), "error") == \
        ["modeller_license_missing"]
    env["modeller"] = dict(MODELLER_OK)
    loop = {"source": {"type": "template", "id": "1UBQ"}, "chain": "A", "start": 10, "end": 15}
    assert run(client_with_ubq, "loop", loop)["ok"]
    assert codes(run(client_with_ubq, "loop", {**loop, "end": 60}), "error") == ["invalid_loop"]
    assert codes(run(client_with_ubq, "loop", {**loop, "source": {"type": "template", "id": "NOPE"}}), "error") == \
        ["not_found"]


def test_quality_kind(client_with_ubq, env):
    body = {"source": {"type": "template", "id": "1UBQ"}, "dope": True, "procheck": True, "prosa": True}
    assert run(client_with_ubq, "quality", body)["checks"] == []
    env["modeller"] = {"configured": False, "available": False}
    assert [(c["code"], c["level"]) for c in run(client_with_ubq, "quality", body)["checks"]] == \
        [("dope_unavailable", "warning")]
    missing = run(client_with_ubq, "quality", {**body, "source": {"type": "upload", "file": "nulla.pdb"}})
    assert codes(missing, "error") == ["bad_request"]


def test_analysis_kind(client_with_ubq, env):
    assert run(client_with_ubq, "analysis", {"source": {"type": "template", "id": "1UBQ"}})["checks"] == []
    bad = run(client_with_ubq, "analysis", {"source": {"type": "template", "id": "1UBQ"}, "msa": {"text": ">a\nMK\n"}})
    assert codes(bad, "error") == ["conservation_invalid"]


class MemoryWorker:
    """Worker QMEAN simulato con UniRef30_2020_06 (12 GB richiesti, valore misurato) e la memoria indicata."""

    def __init__(self, memory: int, busy: bool = False) -> None:
        self.memory, self.busy = memory, busy

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if request.url.path == "/status":
            return httpx.Response(200, json={
                "uniclust": {"valid": True, "prefix": "UniRef30_2020_06", "id": "UniRef30_2020_06", "size": 194 * GIB,
                             "error": None},
                "memory": {"total": self.memory, "swap": 0}, "busy": self.busy, "queued": 0,
                "databases": [], "archives": [], "unsupported": [], "qmtl": {"present": False}})
        return httpx.Response(404)


@pytest.fixture
def qmean_worker(monkeypatch):
    def install(worker):
        qmean_local._status_cache.update({"at": 0.0, "key": None, "value": None})
        monkeypatch.setenv("BIOLOOM_QMEAN_URL", "http://qmean:8080")
        monkeypatch.setattr(qmean_local, "_client", lambda: httpx.Client(base_url="http://qmean:8080",
                                                                          transport=httpx.MockTransport(worker)))
    return install


def test_qmean_kind_memory(client_with_ubq, env, qmean_worker):
    """UniRef30_2020_06 con 8 GiB per Docker: memoria insufficiente (servono circa 12 GB), come il controllo del job;
    con 11,7 GiB parte (tolleranza del 95%)."""
    body = {"source": {"type": "template", "id": "1UBQ"}, "mode": "local"}
    qmean_worker(MemoryWorker(8 * GIB))
    out = run(client_with_ubq, "qmean", body)
    assert [(c["code"], c["level"]) for c in out["checks"]] == [("qmean_memory", "error")]
    assert "servono circa 12 GB" in out["checks"][0]["message"]
    client_with_ubq.put("/api/settings", json={"qmean_fallback_remote": True, "qmean_remote": True,
                                                "contact_email": "a@b.it"})
    qmean_worker(MemoryWorker(8 * GIB))
    assert [(c["code"], c["level"]) for c in run(client_with_ubq, "qmean", body)["checks"]] == \
        [("qmean_memory", "warning")]
    qmean_worker(MemoryWorker(int(11.7 * GIB), busy=True))
    assert codes(run(client_with_ubq, "qmean", body)) == ["qmean_local", "qmean_busy"]


def test_qmean_kind_without_service_and_remote(client_with_ubq, env, monkeypatch):
    monkeypatch.delenv("BIOLOOM_QMEAN_URL", raising=False)
    body = {"source": {"type": "template", "id": "1UBQ"}}
    assert codes(run(client_with_ubq, "qmean", body), "error") == ["qmean_unavailable"]
    assert codes(run(client_with_ubq, "qmean", {**body, "mode": "remote"}), "error") == ["qmean_remote_disabled"]
    client_with_ubq.put("/api/settings", json={"qmean_remote": True, "contact_email": "a@b.it"})
    assert codes(run(client_with_ubq, "qmean", {**body, "mode": "remote"})) == ["qmean_remote"]


def test_search_kind(client, env, monkeypatch, tmp_path):
    ok = run(client, "search", {"query": {"sequence": YEAST_UB}, "engine": "rcsb"})
    assert ok["checks"] == [] and ok["estimate"]["low"] == 2.0 and "MMseqs2" in ok["estimate"]["basis"]
    monkeypatch.setattr(search, "local_database", lambda _d: None)
    assert codes(run(client, "search", {"query": {"sequence": YEAST_UB}, "engine": "local"}), "error") == \
        ["pdbaa_missing"]
    env["tools"]["mmseqs"] = None
    assert codes(run(client, "search", {"query": {"sequence": YEAST_UB}, "engine": "mmseqs"}), "error") == \
        ["mmseqs_missing"]
    bad = run(client, "search", {"query": {"sequence": YEAST_UB}, "engine": "alphafold", "uniprot": "xx"})
    assert codes(bad, "error") == ["invalid_accession"]
    af = run(client, "search", {"query": {"sequence": YEAST_UB}, "engine": "alphafold", "uniprot": "p0cg48"})
    assert codes(af) == ["remote_alphafold"] and "P0CG48" in af["checks"][0]["message"]
    empty = run(client, "search", {"query": {"sequence": ""}, "engine": "rcsb"})
    assert codes(empty, "error") == ["empty_sequence"]


# ---------------------------------------------------------------------------------------------- pipeline
def pipeline_params(fasta: str, **extra) -> dict:
    return {"fasta": fasta, "template_mode": "auto", "engine": "rcsb", "options": {"models": 3, "refinement": "fast"},
            "quality": True, "extended_quality": False, "parallel": 2, **extra}


def test_pipeline_basic(client, env):
    out = run(client, "pipeline", pipeline_params(f">a\n{YEAST_UB}\n>b\n{HUMAN_UB}\n"))
    assert out["ok"] and codes(out) == ["estimated_time"]
    assert "2 sequenze" in out["estimate"]["detail"]
    for body, code in ((pipeline_params(""), "bad_request"),
                       (pipeline_params(f">a\n{YEAST_UB}\n", template_mode="fixed"), "bad_request"),
                       (pipeline_params(f">a\n{YEAST_UB}\n", options={"models": 0}), "bad_request")):
        result = run(client, "pipeline", body)
        assert codes(result, "error") == [code]
        launch = client.post("/api/pipeline", json=body)
        assert launch.status_code == 400 and launch.json()["error"]["message"] == result["checks"][0]["message"]


def test_pipeline_ncbi_many_sequences(client, env):
    fasta = "".join(f">s{i}\n{YEAST_UB}\n" for i in range(51))
    out = run(client, "pipeline", pipeline_params(fasta, engine="ncbi", parallel=1))
    assert codes(out, "warning") == ["ncbi_many_searches"] and "51 ricerche" in out["checks"][0]["message"]
    assert "remote_ncbi" in codes(out, "info")
    # tempi: almeno un minuto per ricerca NCBI
    assert out["estimate"]["low"] >= 51 * 60
    few = run(client, "pipeline", pipeline_params(fasta.split(">s50")[0], engine="ncbi", parallel=1))
    assert "ncbi_many_searches" not in codes(few)


def test_pipeline_alphafold_accessions(client, env):
    fasta = f">sp|P0CG48|UBC_HUMAN\n{HUMAN_UB}\n>senza\n{YEAST_UB}\n"
    out = run(client, "pipeline", pipeline_params(fasta, engine="alphafold", parallel=1))
    af = by_code(out, "pipeline_alphafold")
    assert "1 con l'accessione" in af["message"] and "1 senza" in af["message"]


def test_pipeline_long_and_disordered_sequences(client, env):
    fasta = f">lunga\n{'A' * 5001}\n>snca\n{SNCA}\n>ubq\n{HUMAN_UB}\n"
    out = run(client, "pipeline", pipeline_params(fasta))
    assert by_code(out, "pipeline_too_long")["level"] == "warning" and "lunga" in by_code(out, "pipeline_too_long")[
        "message"]
    dis = by_code(out, "pipeline_disorder")
    assert "snca" in dis["message"] and "ubq" not in dis["message"]


def test_pipeline_fixed_template_and_local_engine(client_with_ubq, env, monkeypatch):
    fixed = run(client_with_ubq, "pipeline", pipeline_params(f">a\n{YEAST_UB}\n", template_mode="fixed",
                                                             templates=[{"id": "1UBQ", "chains": ["Q"]}]))
    assert codes(fixed, "error") == ["template_chain_missing"]
    monkeypatch.setattr(search, "mmseqs_database", lambda _d: None)
    mm = run(client_with_ubq, "pipeline", pipeline_params(f">a\n{YEAST_UB}\n", engine="mmseqs"))
    assert codes(mm, "error") == ["mmseqs_database_missing"]


def test_pipeline_disk_space(client, env):
    env["free"]["data"] = 20 * 1024 ** 2
    fasta = "".join(f">s{i}\n{YEAST_UB}\n" for i in range(10))  # circa 10 × 2,9 MB di risultati
    assert codes(run(client, "pipeline", pipeline_params(fasta)), "error") == ["disk_full_data"]
