"""Confronto con un predittore esterno (F9.3): client ESMFold, consenso all'invio, riconoscimento, plausibilità, job.

Nessun accesso alla rete: il servizio ESMFold (e AlphaFold DB) è simulato con httpx.MockTransport sul client condiviso
di engines/remote.py, con risposte nel formato reale del servizio pubblico (verificato a mano il 1° ottobre 2026:
TITLE "ESMFOLD V1 PREDICTION FOR INPUT", pLDDT su 0–1 in ogni atomo, nessun TER né END). La finta predizione è 1UBQ
con il tratto 31–37 spostato di 6 Å verso l'esterno, come nei test del confronto strutturale (test_comparison.py:
TM-score 0,927, lDDT 0,878 con il tratto a 0,20–0,28).
"""

from __future__ import annotations

import json

import httpx
import numpy as np
import pytest

from bioloom.config import SettingsStore, UserSettings
from bioloom.core.errors import AppError, RemoteServiceError
from bioloom.core.jobs import PRIORITY
from bioloom.domain import consensus, plddt, prediction, report, structure
from bioloom.engines import modeller, quality, remote
from bioloom.engines.modeller import TemplateSpec
from bioloom.services.predict import PredictService

from .conftest import DATA, HUMAN_UB, ubq_variant, wait_job
from .test_alphafold import af_entry, af_pdb

UBQ = {"type": "template", "id": "1UBQ", "chain": "A"}
SEGMENT = range(31, 38)
ESM_HEADER = ["HEADER                                            18-OCT-22",
              "TITLE     ESMFOLD V1 PREDICTION FOR INPUT",
              "REMARK   1 REFERENCE 1",
              "REMARK   1  TITL   EVOLUTIONARY-SCALE PREDICTION OF ATOMIC LEVEL PROTEIN",
              "REMARK   1 ESM METAGENOMIC ATLAS DATA IS AVAILABLE UNDER"]


def ubq_prediction(shift: float = 6.0, value=lambda n: 0.9, header=ESM_HEADER) -> str:
    """Finta risposta del servizio: catena A di 1UBQ (solo ATOM, senza TER né END come il servizio) con il tratto 31–37
    traslato di `shift` Å verso l'esterno e il pLDDT `value(numero)` (scala 0–1 del servizio) in ogni atomo."""
    st = structure.load_structure(DATA / "1UBQ.pdb")
    chain = structure.first_model(st)["A"]
    residues = [r for r in chain if structure.is_amino_residue(r)]
    segment = [r for r in residues if r.id[1] in SEGMENT]
    direction = (np.mean([r["CA"].coord for r in segment], axis=0) - np.mean([r["CA"].coord for r in residues], axis=0))
    direction /= np.linalg.norm(direction)
    for r in segment:
        for atom in r:
            atom.set_coord(atom.coord + shift * direction)
    lines = [ln[:60] + f"{value(int(ln[22:26])):6.2f}" + ln[66:]
             for ln in structure.pdb_text(st, chains=["A"]).splitlines() if ln.startswith("ATOM")]
    return "\n".join([*header, *lines]) + "\n"


class FakeServices:
    """ESMFold (api.esmatlas.com e un indirizzo alternativo) e AlphaFold DB simulati, con il registro delle richieste."""

    def __init__(self) -> None:
        self.esm: list[tuple[int, str]] = []  # risposte in ordine; l'ultima si ripete
        self.calls: list[httpx.Request] = []
        self.af_files = {"AF-P0CG48-F1-model_v6.pdb": af_pdb()}

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request)
        if request.url.host == "alphafold.ebi.ac.uk":
            if request.url.path.startswith("/api/prediction/P0CG48"):
                return httpx.Response(200, json=[af_entry()])
            name = request.url.path.rsplit("/", 1)[-1]
            return httpx.Response(200, text=self.af_files[name]) if name in self.af_files else httpx.Response(404)
        if request.url.host not in ("api.esmatlas.com", "esm.example.org"):
            return httpx.Response(599, text="host inatteso")
        status, body = self.esm.pop(0) if len(self.esm) > 1 else (self.esm[0] if self.esm else (500, ""))
        if status == -1:
            raise httpx.ReadTimeout("timeout", request=request)
        if 300 <= status < 400:  # reindirizzamento verso `body`
            return httpx.Response(status, headers={"location": body})
        return httpx.Response(status, text=body, headers={"content-type": "chemical/x-pdb"})

    def esm_calls(self) -> list[httpx.Request]:
        return [r for r in self.calls if r.url.host != "alphafold.ebi.ac.uk"]


@pytest.fixture
def services(monkeypatch, tmp_path):
    fake = FakeServices()
    fake.esm = [(200, ubq_prediction())]
    # reindirizzamenti seguiti come nel client condiviso (remote.client)
    monkeypatch.setattr(remote, "_client", httpx.Client(transport=httpx.MockTransport(lambda r: fake.handler(r)),
                                                        follow_redirects=True))
    monkeypatch.setattr(remote, "_cache_dir", tmp_path / "remote_cache")
    monkeypatch.setattr(remote, "_sleep_checked", lambda seconds, check, sleep: None)  # tentativi senza attesa
    return fake


def _consent(client, on: bool = True) -> None:
    r = client.put("/api/settings", json={"esmfold_remote": on})
    assert r.status_code == 200 and r.json()["esmfold_remote"] is on


# ------------------------------------------------------------------------------------------ client ESMFold
def test_client_normalizes_plddt_from_unit_scale(services):
    out = remote.esmfold_predict(HUMAN_UB, name="ubq")
    req = services.esm_calls()[0]
    assert req.method == "POST" and req.content == HUMAN_UB.encode() and str(req.url) == remote.ESMFOLD_URL
    assert out["cached"] is False and out["info"]["scale"] == "0-1" and out["info"]["factor"] == 100
    assert out["info"]["residues"] == 76 and out["info"]["plddt"]["mean"] == 90.0
    lines = out["pdb"].splitlines()
    assert lines[1].startswith("TITLE     ESMFOLD V1 PREDICTION FOR INPUT")  # intestazione del servizio conservata
    assert "REMARK 999 BIOLOOM PREDICTOR ESMFOLD api.esmatlas.com" in out["pdb"]
    assert "REMARK 999 BIOLOOM PLDDT SCALE 0-1 X100 TO 0-100" in out["pdb"]
    atoms = [ln for ln in lines if ln.startswith("ATOM")]
    assert {ln[60:66] for ln in atoms} == {" 90.00"} and lines[-1] == "END"
    values = [float(ln[60:66]) for ln in atoms]
    assert plddt.predictor("prediction_esmfold.pdb", out["pdb"], values) == "esmfold"


def test_client_keeps_plddt_already_on_0_100(services):
    services.esm = [(200, ubq_prediction(value=lambda n: 40.0 if n in SEGMENT else 88.5))]
    out = remote.esmfold_predict(HUMAN_UB)
    assert out["info"]["scale"] == "0-100" and out["info"]["factor"] == 1
    assert "X100" not in out["pdb"] and out["info"]["plddt"]["mean"] == pytest.approx((69 * 88.5 + 7 * 40) / 76, 0.01)


def test_client_adds_a_recognizable_title_when_missing(services):
    services.esm = [(200, ubq_prediction(header=[]))]
    out = remote.esmfold_predict(HUMAN_UB, name="mia_seq")
    assert out["pdb"].startswith("TITLE     ESMFOLD PREDICTION, MIA_SEQ SAVED BY BIOLOOM")
    assert plddt.is_esmfold_text(out["pdb"])


@pytest.mark.parametrize("status, code, attempts", [
    (404, "esmfold_unavailable", 1),  # servizio dismesso o indirizzo sbagliato
    (413, "sequence_too_long", 1),  # limite del servizio
    (400, "esmfold_rejected", 1),
    (429, "esmfold_rejected", 1),
    (503, "esmfold_unavailable", 3),  # nuovi tentativi sui 5xx
    (-1, "esmfold_unavailable", 3),  # tempo scaduto
])
def test_client_http_errors_are_clear(services, status, code, attempts):
    services.esm = [(status, "Sequence is longer than 400." if status == 413 else "errore")]
    with pytest.raises(AppError) as err:
        remote.esmfold_predict(HUMAN_UB)
    assert err.value.code == code and len(services.esm_calls()) == attempts
    assert "ESMFold" in err.value.message
    if status == 404:
        assert "dismesso" in err.value.message and "Impostazioni" in err.value.message
    if status == 429:
        assert "Troppe richieste" in err.value.message
    assert not list((remote._cache_dir or DATA).glob("esmfold/*.json"))  # niente in cache


def test_client_retries_transient_errors(services):
    services.esm = [(503, "busy"), (200, ubq_prediction())]
    out = remote.esmfold_predict(HUMAN_UB)
    assert out["info"]["residues"] == 76 and len(services.esm_calls()) == 2


# id brevi: con il PDB intero nell'id, PYTEST_CURRENT_TEST supera il limite di 32767 caratteri delle variabili
# d'ambiente di Windows e il test va in errore prima di partire
@pytest.mark.parametrize("body, reason", [
    ("<html><body>Service Unavailable</body></html>", "nessuna coordinata"),
    (ubq_prediction(value=lambda n: 0.0), "tutta a zero"),
    (ubq_prediction(value=lambda n: 150.0), "fuori dalla scala"),
], ids=["html", "plddt-zero", "plddt-fuori-scala"])
def test_client_rejects_invalid_responses(services, body, reason):
    services.esm = [(200, body)]
    with pytest.raises(RemoteServiceError) as err:
        remote.esmfold_predict(HUMAN_UB)
    assert err.value.code == "esmfold_invalid_response" and reason in err.value.message


def test_client_rejects_oversized_responses(services, monkeypatch):
    monkeypatch.setattr(remote, "ESMFOLD_MAX_BYTES", 1000)
    with pytest.raises(RemoteServiceError) as err:
        remote.esmfold_predict(HUMAN_UB)
    assert err.value.code == "esmfold_invalid_response" and "76 residui" in err.value.message


def test_client_rejects_a_prediction_of_another_sequence(services):
    with pytest.raises(RemoteServiceError) as err:  # come il servizio con le lettere non standard: residui mancanti
        remote.esmfold_predict(HUMAN_UB[:40] + HUMAN_UB[41:] + "A")
    assert err.value.code == "esmfold_invalid_response" and "76 residui" in err.value.message


@pytest.mark.parametrize("sequence, code", [
    ("A" * 401, "sequence_too_long"),
    (HUMAN_UB[:30] + "XBZ" + HUMAN_UB[33:], "invalid_sequence"),
    ("MQIFV", "sequence_too_short"),
    ("  ", "empty_sequence"),
])
def test_client_checks_the_sequence_before_any_request(services, sequence, code):
    with pytest.raises(AppError) as err:
        remote.esmfold_predict(sequence)
    assert err.value.code == code and services.calls == []
    if code == "invalid_sequence":
        assert "B, X, Z" in err.value.message and "31, 32, 33" in err.value.message
    if code == "sequence_too_long":
        assert "401 residui" in err.value.message and "400" in err.value.message


def test_client_length_limit_is_inclusive_and_sequence_is_cleaned(services):
    assert remote.esmfold_sequence("a" * 400) == "A" * 400
    assert remote.esmfold_sequence(f" {HUMAN_UB[:38]}\n{HUMAN_UB[38:]}* ") == HUMAN_UB


def test_client_cache_by_sequence_and_service(services):
    first = remote.esmfold_predict(HUMAN_UB)
    again = remote.esmfold_predict(HUMAN_UB.lower())
    assert again["cached"] is True and again["pdb"] == first["pdb"] and len(services.esm_calls()) == 1
    assert remote.esmfold_cached(HUMAN_UB)["received"] == first["received"]
    remote.esmfold_predict(HUMAN_UB, use_cache=False)  # ricalcolo esplicito
    remote.esmfold_predict(HUMAN_UB, url="https://esm.example.org/fold")  # altro servizio: altra voce
    assert len(services.esm_calls()) == 3 and remote.esmfold_cached(HUMAN_UB, "https://altro.example.org/") is None


@pytest.mark.parametrize("url", ["ftp://x.org/fold", "https://user:pw@x.org/", "https://x.org/a b", "x.org/fold",
                                 "https://x.org/?q=1"])
def test_client_rejects_invalid_service_urls(url):
    with pytest.raises(AppError) as err:
        remote.validate_esmfold_url(url)
    assert err.value.code == "invalid_esmfold_url"


def test_client_default_and_custom_url():
    assert remote.validate_esmfold_url("") == remote.ESMFOLD_URL
    assert remote.validate_esmfold_url(" http://localhost:8000/fold ") == "http://localhost:8000/fold"


# ------------------------------------------------------------------------------------------ consenso e impostazioni
def test_settings_default_and_migration_keep_opt_in_off(tmp_path):
    assert UserSettings().esmfold_remote is False and UserSettings().esmfold_url == ""
    path = tmp_path / "settings.json"
    # impostazioni di una versione precedente: nessun campo di ESMFold, consenso a SWISS-MODEL dato
    path.write_text(json.dumps({"contact_email": "a@b.it", "qmean_remote": True, "max_workers": 3}))
    s = SettingsStore(path).get()
    assert s.esmfold_remote is False and s.esmfold_url == "" and s.qmean_remote and s.max_workers == 3


@pytest.mark.parametrize("raw", [{"esmfold_remote": "yes"}, {"esmfold_remote": 1}, {"esmfold_remote": None},
                                 {"esmfold_url": "ftp://x"}, {"esmfold_url": None}, {"esmfold_url": 5}])
def test_settings_with_odd_values_do_not_reset_everything(tmp_path, raw):
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"contact_email": "a@b.it", "max_workers": 3, **raw}))
    s = SettingsStore(path).get()
    assert s.esmfold_remote is False and s.esmfold_url == "" and s.contact_email == "a@b.it" and s.max_workers == 3


def test_settings_route_validates_the_url(client):
    r = client.put("/api/settings", json={"esmfold_url": "ftp://x"})
    assert r.status_code == 400 and r.json()["error"]["code"] == "invalid_esmfold_url"
    r = client.put("/api/settings", json={"esmfold_url": "https://esm.example.org/fold", "esmfold_remote": True})
    assert r.status_code == 200 and r.json()["esmfold_url"] == "https://esm.example.org/fold"
    assert client.get("/api/system/info").json()["services"]["esmfold_remote"] is True


# ------------------------------------------------------------------------------------------ richieste in uscita
INTERNAL = "http://169.254.169.254/latest/"


@pytest.fixture
def server_client(tmp_path, monkeypatch, services):
    """Modalità server (accesso con chiave) con impostazioni salvate prima che l'indirizzo fosse bloccato: consenso
    dato e un indirizzo della rete interna."""
    from fastapi.testclient import TestClient

    from bioloom.app import create_app
    from bioloom.config import Paths
    from bioloom.core import auth

    monkeypatch.delenv("BIOLOOM_ESMFOLD_URL", raising=False)
    data = tmp_path / "data"
    data.mkdir()
    (data / "settings.json").write_text(json.dumps({"esmfold_remote": True, "esmfold_url": INTERNAL}))
    app = create_app(Paths(data), frontend_dist=None, auth_config=auth.AuthConfig(enabled=True, key="segreto-12345"))
    with TestClient(app, headers={"Authorization": "Bearer segreto-12345"}) as c:
        yield c


def test_server_mode_esmfold_url_is_set_by_the_administrator_only(server_client, services, monkeypatch):
    """Con l'accesso con chiave, chi ha la chiave non sceglie dove il server invia richieste: PUT /api/settings rifiuta
    un altro indirizzo (403), quello salvato in settings.json non vale e si usa quello dell'amministratore
    (BIOLOOM_ESMFOLD_URL, vuoto = servizio pubblico)."""
    c = server_client
    assert c.get("/api/settings").json()["esmfold_url"] == ""  # servizio pubblico, non l'indirizzo salvato
    for url in (INTERNAL, "http://127.0.0.1:8080/fold", "https://esm.example.org/fold"):
        r = c.put("/api/settings", json={"esmfold_url": url})
        assert r.status_code == 403 and r.json()["error"]["code"] == "esmfold_url_locked", url
        assert "BIOLOOM_ESMFOLD_URL" in r.json()["error"]["message"]
    # la pagina rimanda il valore in uso insieme alle altre impostazioni: si salva tutto il resto
    r = c.put("/api/settings", json={"esmfold_url": "", "contact_email": "a@b.it"})
    assert r.status_code == 200 and r.json()["esmfold_url"] == "" and r.json()["contact_email"] == "a@b.it"
    job = c.post("/api/predict", json={"sequence": {"name": "ubq", "sequence": HUMAN_UB}}).json()
    assert wait_job(c, job["id"])["status"] == "completed"
    assert [str(r.url) for r in services.esm_calls()] == [remote.ESMFOLD_URL]
    # indirizzo dell'amministratore
    monkeypatch.setenv("BIOLOOM_ESMFOLD_URL", "https://esm.example.org/fold")
    assert c.get("/api/settings").json()["esmfold_url"] == "https://esm.example.org/fold"
    assert c.put("/api/settings", json={"esmfold_url": "https://esm.example.org/fold"}).status_code == 200
    job = c.post("/api/predict", json={"sequence": {"name": "ubq", "sequence": HUMAN_UB}}).json()
    assert wait_job(c, job["id"])["status"] == "completed"
    assert str(services.esm_calls()[-1].url) == "https://esm.example.org/fold"
    assert INTERNAL not in [str(r.url) for r in services.calls]


def test_client_does_not_follow_redirects(services):
    """Un servizio che reindirizza (es. con un 303 verso la rete interna del server) non porta il server altrove."""
    services.esm = [(303, INTERNAL)]
    with pytest.raises(RemoteServiceError) as err:
        remote.esmfold_predict(HUMAN_UB, url="https://esm.example.org/fold")
    assert err.value.code == "esmfold_unavailable" and "reindirizzamento" in err.value.message
    assert [str(r.url) for r in services.calls] == ["https://esm.example.org/fold"]


@pytest.mark.parametrize("status, code", [(403, "esmfold_rejected"), (413, "sequence_too_long")])
def test_client_keeps_error_bodies_of_other_services_out_of_the_message(services, capsys, status, code):
    """Il corpo di una risposta d'errore entra nel messaggio (e così nel job) solo dal servizio pubblico; quello di un
    altro indirizzo va nel log del server."""
    services.esm = [(status, "INTERNAL-SECRET: role=admin")]
    with pytest.raises(AppError) as err:
        remote.esmfold_predict(HUMAN_UB, url="https://esm.example.org/fold")
    assert err.value.code == code and "INTERNAL-SECRET" not in err.value.message
    assert "INTERNAL-SECRET" in capsys.readouterr().out
    services.esm = [(status, "Sequence is longer than 400." if status == 413 else "Bad request")]
    with pytest.raises(AppError) as err:
        remote.esmfold_predict(HUMAN_UB)
    assert err.value.message.count("Sequence is longer than 400." if status == 413 else "Bad request") == 1


def test_submit_without_consent_is_refused_before_any_request(client_with_ubq, services):
    r = client_with_ubq.post("/api/predict", json={"source": UBQ})
    assert r.status_code == 403 and r.json()["error"]["code"] == "esmfold_disabled"
    assert "Impostazioni" in r.json()["error"]["message"]
    assert services.calls == [] and client_with_ubq.get("/api/jobs?kind=predict").json() == []


def test_consent_is_checked_again_when_the_job_runs(client_with_ubq):
    core = client_with_ubq.app.state.core
    service = PredictService(core.jobs, core.compute)
    with pytest.raises(AppError) as err:  # handler: request(p, ctx.settings) con il consenso revocato
        service.request({"source": UBQ}, UserSettings(esmfold_remote=False))
    assert err.value.code == "esmfold_disabled" and err.value.status == 403
    assert service.request({"source": UBQ}, UserSettings(), require_consent=False)["sequence"] == HUMAN_UB


def test_job_queued_without_consent_fails_without_sending(client_with_ubq, services):
    """Un job che arriva all'esecuzione senza consenso (revocato dopo l'invio, o accodato da un altro client) fallisce
    con il motivo e non invia nulla."""
    core = client_with_ubq.app.state.core
    job = core.jobs.submit("predict", {"source": UBQ, "predictor": "esmfold"}, title="senza consenso")
    done = wait_job(client_with_ubq, job["id"])
    assert done["status"] == "failed" and "non è consentito" in done["error"] and services.calls == []


def test_concurrent_predictions_share_a_valid_cache(client_with_ubq, services):
    _consent(client_with_ubq)
    ids = [client_with_ubq.post("/api/predict", json={"source": UBQ}).json()["id"] for _ in range(3)]
    done = [wait_job(client_with_ubq, i) for i in ids]
    assert all(d["status"] == "completed" for d in done)
    assert len({d["result"]["comparison"]["tm_score"] for d in done}) == 1
    entries = list((remote._cache_dir / "esmfold").glob("*.json"))
    assert len(entries) == 1 and json.loads(entries[0].read_text())["info"]["residues"] == 76
    assert 1 <= len(services.esm_calls()) <= 3  # al più una richiesta per job, nessuna dopo la cache


@pytest.mark.parametrize("body, code", [
    ({}, "predict_input_required"),
    ({"source": {**UBQ, "chain": "Z"}}, "invalid_chain"),
    ({"sequence": {"name": "x", "sequence": "A" * 401}}, "sequence_too_long"),
    ({"predictor": "alphafold", "source": UBQ}, "accession_required"),
    ({"predictor": "alphafold", "source": UBQ, "accession": "XYZ"}, "invalid_accession"),
])
def test_submit_validates_before_queueing(client_with_ubq, services, body, code):
    _consent(client_with_ubq)
    r = client_with_ubq.post("/api/predict", json=body)
    assert r.status_code == 400 and r.json()["error"]["code"] == code
    assert services.calls == []


# ------------------------------------------------------------------------------------------ riconoscitore unico
def _pdb_head_values(text: str) -> tuple[str, list[float]]:
    return text, [float(ln[60:66]) for ln in text.splitlines() if ln.startswith("ATOM")]


def test_recognizer_esmfold_and_no_false_positives():
    normalized, _info = prediction.normalize_pdb(ubq_prediction(), HUMAN_UB)
    assert plddt.predictor("x.pdb", *_pdb_head_values(normalized)) == "esmfold"
    assert plddt.is_predicted_model("x.pdb", *_pdb_head_values(normalized))
    assert plddt.is_alphafold_model("x.pdb", *_pdb_head_values(normalized))  # nome storico, stesso riconoscitore
    # file grezzo del servizio (0–1): non passa per pLDDT su 0–100
    assert plddt.predictor("x.pdb", *_pdb_head_values(ubq_prediction())) is None
    # strutture sperimentali, modelli MODELLER e menzioni di ESMFold altrove non contano
    exp = normalized.replace("HEADER ", "EXPDTA    X-RAY DIFFRACTION\nHEADER ", 1)
    assert plddt.predictor("x.pdb", *_pdb_head_values(exp)) is None
    mod = normalized.replace("HEADER ", "EXPDTA    THEORETICAL MODEL, MODELLER 10.8\nHEADER ", 1)
    assert plddt.predictor("x.pdb", *_pdb_head_values(mod)) is None
    remark = normalized.replace("TITLE     ESMFOLD V1 PREDICTION", "REMARK 200 STARTING MODEL: ESMFOLD PREDICTION")
    assert plddt.predictor("esmfold_prediction.pdb", *_pdb_head_values(remark)) is None
    other_title = normalized.replace("TITLE     ESMFOLD V1", "TITLE     COMPARED WITH ESMFOLD V1")
    assert plddt.predictor("x.pdb", *_pdb_head_values(other_title)) is None
    assert not plddt.is_esmfold_text("data_X\n_struct.title 'ESMFOLD PREDICTION'\n_atom_site.id\n")
    # AlphaFold resta riconosciuto (regressione)
    assert plddt.predictor("x.pdb", *_pdb_head_values(af_pdb())) == "alphafold"


@pytest.mark.parametrize("name", ["1UBQ.pdb", "ubq_dimer.pdb", "ubq_zn.pdb"])
def test_recognizer_on_test_structures(name):
    path = DATA / name
    model = structure.first_model(structure.load_structure(path))
    assert plddt.predictor(name, structure.read_text_head(path), plddt.atom_values(model)) is None
    rep = quality.analyze(path, None, with_dope=False, with_procheck=False, with_prosa=False)
    assert rep["structure"]["bfactor_kind"] == "bfactor" and "predictor" not in rep["structure"]


def test_prediction_file_in_quality_library_and_preparation(client, tmp_path):
    pdb, _info = prediction.normalize_pdb(ubq_prediction(value=lambda n: 0.4 if n in SEGMENT else 0.9), HUMAN_UB)
    path = tmp_path / "prediction_esmfold.pdb"
    path.write_text(pdb)
    rep = quality.analyze(path, None, with_dope=False, with_procheck=False, with_prosa=False)
    assert rep["structure"]["bfactor_kind"] == "plddt" and rep["structure"]["predictor"] == "esmfold"
    # libreria: modello predetto con il metodo di ESMFold
    r = client.post("/api/templates/upload", files={"file": ("pred.pdb", pdb.encode())}, data={"template_id": "ESMUBQ"})
    assert r.status_code == 200, r.text
    meta = client.get("/api/templates").json()
    item = next(t for t in meta if t["id"] == "ESMUBQ")
    assert item["predicted"] is True and item["method"] == "Modello predetto (ESMFold)"
    # copia preparata per MODELLER: ritaglio pLDDT e TITLE di ESMFold conservato (niente TITLE di AlphaFold)
    info = modeller.prepare_template(path, TemplateSpec(id="ESMUBQ", chains=["A"], plddt_min=70.0), tmp_path / "prep")
    head = (tmp_path / "prep" / info["file"]).read_text()
    assert info["plddt"]["removed"] == 7 and "ESMFOLD V1 PREDICTION" in head and "ALPHAFOLD" not in head


def test_esmfold_template_is_named_esmfold_in_logs_notes_remarks_report_and_checks(client, tmp_path):
    """Una predizione di ESMFold nella libreria usata come template: log della preparazione, note dei risultati, righe
    REMARK del modello, report e controlli preventivi dicono ESMFold (il predittore riconosciuto da
    plddt.predictor), mai AlphaFold; un template senza predittore noto né nome di AlphaFold DB resta «modello
    predetto»."""
    from types import SimpleNamespace

    from bioloom.domain import preflight as rules

    pdb, _info = prediction.normalize_pdb(ubq_prediction(value=lambda n: 0.4 if n in SEGMENT else 0.9), HUMAN_UB)
    r = client.post("/api/templates/upload", files={"file": ("pred.pdb", pdb.encode())}, data={"template_id": "ESMUBQ"})
    assert r.status_code == 200, r.text
    core = client.app.state.core
    assert core.compute.library.predictor("ESMUBQ") == "esmfold"
    logs: list[str] = []
    ctx = SimpleNamespace(check=lambda: None, log=logs.append)
    [info] = core.compute._prepare_templates(ctx, [TemplateSpec(id="ESMUBQ", chains=["A"])], tmp_path, plddt_min=70.0)
    assert info["predictor"] == "esmfold"
    text = " ".join(logs)
    assert "modello predetto (ESMFold), rimossi" in text and "AlphaFold" not in text
    [note] = core.compute._predicted_notes([info])
    assert note.startswith("ESMUBQ_A: modello predetto (ESMFold), non una struttura sperimentale")
    assert plddt.predictor_tag("esmfold") == "ESMFOLD" and plddt.predictor_tag(None, "AF-P0CG48-F1") == "ALPHAFOLD"
    assert plddt.predicted_text(None, "MIOMODELLO") == "modello predetto"
    assert report.model_trims("REMARK 999 BIOLOOM TEMPLATE ESMUBQ_A ESMFOLD PLDDT<70 REMOVED 7\n") == {
        "ESMUBQ_A": {"threshold": 70.0, "removed": 7}}
    checks = rules.plddt_checks([{"template": "ESMUBQ", "threshold": None, "total": 76, "low": 7,
                                  "predictor": "esmfold"}])
    assert checks[0]["message"].startswith("ESMUBQ: modello ESMFold usato senza ritaglio")
    html = report.render({"kind": "model", "job_id": "j", "tools": [], "warnings": [], "templates": [
        {"id": "ESMUBQ", "code": "ESMUBQ_A", "chains": ["A"], "predicted": True, "predictor": "esmfold",
         "trim": info["plddt"]}]})
    assert "ESMUBQ_A: modello predetto (ESMFold), non una struttura sperimentale" in html
    assert "Lin Z." in html and "Jumper J." not in html


# ------------------------------------------------------------------------------------------ plausibilità
def _comparison(tmp_path, **kw) -> tuple[dict, dict]:
    """Confronto integrato 1UBQ (A) contro la finta predizione (B) e il pLDDT della predizione."""
    from bioloom.engines import align

    pdb, _info = prediction.normalize_pdb(ubq_prediction(**kw), HUMAN_UB)
    path = tmp_path / "pred.pdb"
    path.write_text(pdb)

    class Ctx:
        workdir = tmp_path
        settings = UserSettings()
        cancelled = False

        def progress(self, *a, **k):
            pass

        def log(self, message):
            pass

        def path(self, *parts):
            return tmp_path.joinpath(*parts)

    comp = align.structural(DATA / "1UBQ.pdb", "A", path, None, Ctx(), tool="builtin", use_cache=False)
    return comp, prediction.residue_plddt(structure.first_model(structure.load_structure(path)))


def test_plausibility_divergent_where_prediction_is_sure(tmp_path):
    comp, pl = _comparison(tmp_path)
    res = prediction.assess(comp, pl, consensus_regions=[])
    assert res["status"] == "divergenze locali" and res["tone"] == "warn"
    assert res["tm_score"] == pytest.approx(0.927, abs=1e-3) and res["lddt"] == pytest.approx(0.878, abs=1e-3)
    [region] = res["regions"]
    assert (region["chain"], region["start"], region["end"], region["divergent"]) == ("A", "31", "37", 7)
    assert region["confidence"] == "sicura" and region["plddt_mean"] == 90.0
    assert 0.2 < region["lddt_mean"] < 0.28
    assert res["summary"] == ("Stesso ripiegamento (TM-score 0,93, lDDT 0,88), ma le due strutture divergono in 31-37 "
                              "(catena A), dove la predizione è sicura (pLDDT medio 90): controlla l'allineamento e il "
                              "template in questa regione.")
    assert res["confident_residues"] == 76 and res["lddt_confident"] == pytest.approx(0.8721, abs=1e-3)
    assert sum(r["divergent"] for r in res["residues"]) == 7 and res["consensus_available"] is True


def test_plausibility_divergent_where_prediction_is_uncertain(tmp_path):
    comp, pl = _comparison(tmp_path, value=lambda n: 0.4 if n in SEGMENT else 0.9)
    res = prediction.assess(comp, pl, consensus_regions=None)
    assert res["status"] == "concordano" and res["tone"] == "ok"
    assert res["regions"][0]["confidence"] == "molto incerta" and res["regions"][0]["plddt_mean"] == 40.0
    assert res["summary"] == ("Le due strutture concordano: TM-score 0,93 (stesso ripiegamento), lDDT 0,88; divergono "
                              "solo in 31-37 (catena A), dove anche la predizione è molto incerta (pLDDT medio 40): "
                              "regione flessibile o poco determinata, il confronto lì non è informativo.")
    # i residui incerti non entrano nell'accordo: solo i 69 confidenti, che concordano
    assert res["confident_residues"] == 69 and res["lddt_confident"] == pytest.approx(0.9364, abs=1e-3)
    assert any("consenso non disponibili" in n for n in res["notes"])
    comp, pl = _comparison(tmp_path, value=lambda n: 0.6 if n in SEGMENT else 0.9)
    assert prediction.assess(comp, pl)["regions"][0]["confidence"] == "incerta"


def test_plausibility_identical_and_cross_with_consensus(tmp_path):
    comp, pl = _comparison(tmp_path, shift=0.0)
    res = prediction.assess(comp, pl, consensus_regions=[])
    assert res["status"] == "concordano" and res["regions"] == []
    assert res["summary"] == "Le due strutture concordano: TM-score 1,00 (stesso ripiegamento), lDDT 1,00."
    comp, pl = _comparison(tmp_path)
    cons = [{"chain": "A", "resnums": [str(n) for n in range(29, 34)], "text": "DOPE e Ramachandran segnalano 29-33 "
                                                                               "(catena A)"},
            {"chain": "A", "resnums": ["60", "61", "62"], "text": "altrove"}]
    res = prediction.assess(comp, pl, consensus_regions=cons)
    assert res["regions"][0]["consensus"] == ["DOPE e Ramachandran segnalano 29-33 (catena A)"]
    assert "segnalata anche dal consenso (DOPE e Ramachandran segnalano 29-33 (catena A))" in res["summary"]


def _synthetic(lddt: list[float], plddt_values: list[float], tm: float = 0.9, chains: str = "A") -> tuple[dict, dict]:
    pairs, pl = [], {}
    for i, (v, p) in enumerate(zip(lddt, plddt_values)):
        chain = chains[min(len(chains) - 1, i * len(chains) // len(lddt))]
        pairs.append({"chain_a": chain, "resnum_a": str(i + 1), "aa_a": "A", "chain_b": "A", "resnum_b": str(i + 1),
                      "aa_b": "A", "distance": 1.0, "lddt": v})
        pl[("A", str(i + 1))] = p
    return {"pairs": pairs, "tm_score": tm, "lddt": float(np.mean(lddt)), "lengths": [len(lddt), len(lddt)]}, pl


def test_plausibility_different_fold_and_inconclusive():
    comp, pl = _synthetic([0.3] * 60, [85.0] * 60, tm=0.31)
    res = prediction.assess(comp, pl, consensus_regions=[])
    assert res["status"] == "ripiegamento diverso" and res["tone"] == "err"
    assert res["summary"].startswith("Le due strutture hanno ripiegamenti diversi (TM-score 0,31, sotto 0,5)")
    comp, pl = _synthetic([0.3] * 60, [45.0] * 30 + [75.0] * 29 + [None], tm=0.31)  # 29 residui sicuri: meno di 30
    res = prediction.assess(comp, pl, consensus_regions=[])
    assert res["status"] == "non conclusivo" and res["confident_residues"] == 29 and res["tone"] == "info"
    comp, pl = _synthetic([0.9] * 20, [80.0] * 20)  # proteina corta: bastano tutti i suoi residui
    assert prediction.assess(comp, pl)["status"] == "concordano"


def _spans(res: dict) -> list[tuple[str, str, int]]:
    return [(r["start"], r["end"], r["divergent"]) for r in res["regions"]]


def test_plausibility_regions_tolerance_and_boundaries():
    lddt = ([0.9] * 10 + [0.3, 0.3, 0.8, 0.3] + [0.9] * 9 + [0.1] * 4 + [0.9] * 5 + [0.2, 0.2] + [0.9] * 5
            + [0.1] * 3 + [0.9] * 8)
    spans = _spans
    comp, pl = _synthetic(lddt, [90.0] * len(lddt))
    # 11–14 con un residuo non divergente tollerato; 24–27; 33–34 troppo corta; 40–42
    assert len(lddt) == 50 and spans(prediction.assess(comp, pl)) == [("11", "14", 3), ("24", "27", 4),
                                                                      ("40", "42", 3)]
    comp, pl = _synthetic(lddt, [90.0] * len(lddt), chains="AB")  # 24–27 attraversa il confine fra A e B (25/26)
    assert spans(prediction.assess(comp, pl)) == [("11", "14", 3), ("40", "42", 3)]
    comp, pl = _synthetic(lddt, [90.0] * len(lddt))
    comp["pairs"][40]["resnum_a"] = "45"  # interruzione della numerazione dentro 40–42
    assert spans(prediction.assess(comp, pl)) == [("11", "14", 3), ("24", "27", 4)]


def test_sequence_of_a_broken_chain_with_a_selenomethionine(tmp_path):
    """Senza i residui 35–37 e con la Met1 come MSE: la sequenza ricavata dalla catena conta la MSE (M), riconosce
    l'interruzione e segna le giunzioni; con il SEQRES del file, che corrisponde, si invia la sequenza completa."""
    path = ubq_variant(tmp_path / "gap.pdb", hetatm={1: "MSE"}, drop=range(35, 38))
    chain = structure.first_model(structure.load_structure(path))["A"]
    found = prediction.chain_sequence(chain)
    assert found["sequence"] == HUMAN_UB[:34] + HUMAN_UB[37:] and found["origin"] == "structure"
    assert found["gaps"] == [{"after": "34", "before": "38", "missing": 3}] and found["modified"] == ["MSE A1 → M"]
    assert found["junctions"] == [("A", str(n)) for n in (*range(30, 35), *range(38, 43))]
    assert "La catena A è interrotta (fra 34 e 38, 3 residui mancanti)" in found["warning"]
    full = prediction.chain_sequence(chain, HUMAN_UB, max_length=400)
    assert (full["sequence"], full["origin"], full["warning"], full["junctions"]) == (HUMAN_UB, "seqres", None, [])
    assert "non corrisponde" in prediction.chain_sequence(chain, "W" * 76)["warning"]
    assert "oltre il limite di 50" in prediction.chain_sequence(chain, HUMAN_UB, max_length=50)["warning"]
    whole = prediction.chain_sequence(structure.first_model(structure.load_structure(DATA / "1UBQ.pdb"))["A"])
    assert whole["sequence"] == HUMAN_UB and whole["gaps"] == [] and whole["warning"] is None
    # numerazione che salta senza residui mancanti (legame C–N intatto): nessuna interruzione
    renumbered = tmp_path / "salto.pdb"
    renumbered.write_text("".join(ln[:22] + f"{int(ln[22:26]) + 10:4d}" + ln[26:] if ln.startswith("ATOM") and
                                  int(ln[22:26]) > 40 else ln for ln in (DATA / "1UBQ.pdb").read_text().splitlines(True)))
    assert prediction.chain_sequence(structure.first_model(structure.load_structure(renumbered))["A"])["gaps"] == []


def test_junction_residues_never_count_as_divergent():
    comp, pl = _synthetic([0.9] * 30 + [0.1] * 6 + [0.9] * 30, [90.0] * 66)
    assert prediction.assess(comp, pl)["status"] == "divergenze locali"
    res = prediction.assess(comp, pl, junctions=[("A", str(n)) for n in range(29, 39)])
    assert res["status"] == "concordano" and res["regions"] == [] and res["confident_residues"] == 56
    assert sum(r["junction"] for r in res["residues"]) == 10 and any("giunzioni" in n for n in res["notes"])


def test_normalize_and_scale_errors():
    assert prediction.plddt_scale([0.2, 1.0]) == (100.0, "0-1")
    assert prediction.plddt_scale([0.2, 1.5]) == (1.0, "0-100")
    for bad in ([], [0.0, 0.0], [-1.0, 50.0], [50.0, 101.0], [float("nan")]):
        with pytest.raises(ValueError):
            prediction.plddt_scale(bad)
    with pytest.raises(ValueError, match="nessuna coordinata"):
        prediction.normalize_pdb("HEADER\nEND\n", HUMAN_UB)


# ------------------------------------------------------------------------------------------ consenso
def _ubq_report() -> dict:
    return quality.analyze(DATA / "1UBQ.pdb", None, with_dope=False, with_procheck=False, with_prosa=False)


def test_consensus_unchanged_without_comparison():
    rep = _ubq_report()
    base = consensus.compute(rep)
    assert consensus.compute(rep, predictor=None) == base
    assert "predictor" not in base and base["methods_total"] == 6 and base["version"] == 2
    assert all(m["key"] != "predictor" for m in base["missing"])


def test_consensus_with_comparison(tmp_path):
    rep = _ubq_report()
    comp, pl = _comparison(tmp_path)
    res = {**prediction.assess(comp, pl, consensus_regions=[]), "job_id": "j1"}
    c = consensus.compute(rep, predictor=res)
    method = next(m for m in c["methods"] if m["key"] == "predictor")
    assert method["label"] == "Accordo con ESMFold" and method["weight"] == consensus.PREDICTOR_WEIGHT == 0.25
    assert method["raw"] == pytest.approx(0.8721, abs=1e-3) and method["normalized"] == pytest.approx(87.2, abs=0.2)
    assert c["methods_total"] == 7 and c["methods_used"] == 2
    grade = rep["grade"]["score"]
    assert c["score"] == pytest.approx((grade + 0.25 * method["normalized"]) / 1.25, abs=0.1)
    sig = c["predictor"]
    assert sig["informative"] and sig["job_id"] == "j1" and sig["flagged"] == 7
    assert [i for i, f in enumerate(sig["flags"]) if f] == list(range(30, 37))  # indici di 31–37 nel rapporto
    assert sig["regions"][0]["start_index"] == 30 and sig["regions"][0]["end_index"] == 36
    # punti della scala: 0,5 → 40 e 0,7 → 70 (fasce del pLDDT)
    assert consensus.piecewise(0.5, consensus.PREDICTOR_POINTS) == 40 and \
        consensus.piecewise(0.7, consensus.PREDICTOR_POINTS) == 70
    # regioni, segnali e conteggi del consenso per residuo non cambiano (informativo)
    base = consensus.compute(rep)
    assert c["regions"] == base["regions"] and c["per_residue"] == base["per_residue"]


def test_consensus_inconclusive_comparison_and_discordance(tmp_path):
    rep = _ubq_report()
    comp, pl = _comparison(tmp_path, value=lambda n: 0.3)
    res = prediction.assess(comp, pl)
    c = consensus.compute(rep, predictor=res)
    assert c["methods_total"] == 7 and all(m["key"] != "predictor" for m in c["methods"])
    assert next(m for m in c["missing"] if m["key"] == "predictor")["reason"] == consensus.PREDICTOR_REASON
    assert c["score"] == consensus.compute(rep)["score"]
    # disaccordo dove la predizione è sicura: discordanza con il voto, con la spiegazione
    poor = {**prediction.assess(*_synthetic([0.3] * 76, [90.0] * 76, tm=0.4)), "label": "ESMFold"}
    c = consensus.compute(rep, predictor=poor)
    [disc] = c["discordances"]
    assert disc["good"] == "grade" and disc["poor"] == "predictor"
    assert "diverge dalla predizione dove questa è sicura" in disc["text"]


def test_consensus_marks_problem_regions_diverging_from_the_prediction(tmp_path):
    rep = _ubq_report()
    # una regione problematica sintetica su 30–36 (due metodi), come la scriverebbero DOPE e Ramachandran
    rows = rep["residues"]
    for r in rows[29:36]:
        r["rama"], r["clashes"] = "outlier", 1
    comp, pl = _comparison(tmp_path)
    c = consensus.compute(rep, predictor=prediction.assess(comp, pl))
    [region] = c["regions"]
    assert region["predictor_overlap"] == 6 and "diverge anche da ESMFold, sicura lì" in region["text"]
    assert "predictor_overlap" not in consensus.compute(rep)["regions"][0]


# ------------------------------------------------------------------------------------------ job, rotte, report
def test_priority_and_pool(client):
    reg = client.app.state.core.jobs._handlers["predict"]
    assert PRIORITY["predict"] == 0 and reg.priority == 0 and reg.pool == "io"


def _predict(client, body: dict) -> dict:
    r = client.post("/api/predict", json=body)
    assert r.status_code == 200, r.text
    done = wait_job(client, r.json()["id"], timeout=120)
    assert done["status"] == "completed", done
    return done


def test_predict_job_end_to_end(client_with_ubq, services):
    c = client_with_ubq
    _consent(c)
    done = _predict(c, {"source": UBQ})
    res = done["result"]
    assert done["title"] == "Predizione ESMFold · 1UBQ (confronto)"
    assert res["prediction_file"] == "prediction_esmfold.pdb" and res["recognized_as"] == "esmfold"
    assert res["origin"]["scale"] == "0-1" and res["plddt"]["mean"] == 90.0 and res["chain"] == "A"
    assert services.esm_calls()[0].content == HUMAN_UB.encode()  # la sequenza della catena del modello
    comp = res["comparison"]
    assert comp["mode"] == "structural" and comp["superposed_file"] == "superposed.pdb" and len(comp["pairs"]) == 76
    assert comp["tm_score"] == pytest.approx(0.927, abs=1e-3) and "per sequenza" in comp["note"]
    assert res["plausibility"]["status"] == "divergenze locali"
    files = {f["path"] for f in done["files"]}
    assert {"prediction_esmfold.pdb", "superposed.pdb"} <= files
    # la predizione salvata è riconosciuta come modello predetto dall'analisi di qualità
    q = c.post("/api/quality", json={"source": {"type": "job", "job_id": done["id"], "file": "prediction_esmfold.pdb"},
                                     "dope": False, "procheck": False, "prosa": False}).json()
    q = wait_job(c, q["id"])
    assert q["result"]["structure"]["bfactor_kind"] == "plddt" and q["result"]["structure"]["predictor"] == "esmfold"
    # più recente sulla struttura
    latest = c.get("/api/predict/latest", params={"type": "template", "id": "1UBQ"}).json()
    assert latest["job_id"] == done["id"] and latest["active_job_id"] is None
    assert c.get("/api/predict/latest", params={"type": "upload", "file": "x.pdb"}).json()["job_id"] is None
    # una seconda richiesta per la stessa sequenza viene dalla cache: nessuna richiesta al servizio
    again = _predict(c, {"source": UBQ})
    assert again["result"]["origin"]["cached"] is True and len(services.esm_calls()) == 1


def _upload_template(client, path, template_id: str) -> None:
    r = client.post("/api/templates/upload", files={"file": (path.name, path.read_bytes())},
                    data={"template_id": template_id})
    assert r.status_code == 200, r.text


def test_predict_job_on_a_broken_chain_sends_seqres_or_warns(client, services, tmp_path):
    """Catena senza i residui 35–37: con il SEQRES nel file va a ESMFold la sequenza completa (non 73 residui uniti);
    senza SEQRES avviso nei controlli e nel risultato, e i residui vicini alla giunzione non contano come divergenze."""
    c = client
    _consent(c)
    gap = ubq_variant(tmp_path / "gap.pdb", drop=range(35, 38))
    _upload_template(c, gap, "UBQGAP")
    src = {"type": "template", "id": "UBQGAP", "chain": "A"}
    pre = _preflight(c, {"source": src})
    assert "predict_sequence_seqres" in _codes(pre, "info") and "predict_sequence_gaps" not in _codes(pre, "warning")
    done = _predict(c, {"source": src})
    assert services.esm_calls()[0].content == HUMAN_UB.encode()
    assert done["result"]["sequence"]["origin"] == "seqres" and done["result"]["warnings"] == []
    bare = tmp_path / "gap_bare.pdb"
    bare.write_text("".join(ln for ln in gap.read_text().splitlines(True) if not ln.startswith("SEQRES")))
    _upload_template(c, bare, "UBQGAPB")
    src = {"type": "template", "id": "UBQGAPB", "chain": "A"}
    pre = _preflight(c, {"source": src})
    assert _codes(pre, "warning") == ["predict_sequence_gaps"] and pre["ok"]
    joined = HUMAN_UB[:34] + HUMAN_UB[37:]
    services.esm = [(200, "\n".join(ln for ln in ubq_prediction().splitlines()
                                    if not (ln.startswith("ATOM") and int(ln[22:26]) in range(35, 38))) + "\n")]
    res = _predict(c, {"source": src})["result"]
    assert services.esm_calls()[-1].content == joined.encode() and res["sequence"]["gaps"][0]["missing"] == 3
    assert any("interrotta" in w for w in res["warnings"]) and res["sequence"]["origin"] == "structure"
    rows = {r["resnum"]: r for r in res["plausibility"]["residues"]}
    assert rows["34"]["junction"] and rows["38"]["junction"] and not rows["20"]["junction"]
    assert not any(r["divergent"] for r in rows.values() if r["junction"])
    assert any("giunzioni" in n for n in res["plausibility"]["notes"])


def test_predict_job_with_alphafold_db(client_with_ubq, services):
    done = _predict(client_with_ubq, {"source": UBQ, "predictor": "alphafold", "accession": "P0CG48"})
    res = done["result"]
    assert res["predictor"] == "alphafold" and res["recognized_as"] == "alphafold"
    assert res["prediction_file"] == "prediction_AF-P0CG48-F1.pdb" and services.esm_calls() == []
    # stessa struttura (1UBQ con i pLDDT di prova: code basse 1–5 e 71–76): concordano
    assert res["comparison"]["tm_score"] == pytest.approx(1.0, abs=1e-3)
    assert res["plausibility"]["status"] == "concordano" and res["plausibility"]["confident_residues"] == 65


def test_consensus_route_and_report_include_the_comparison(client_with_ubq, services):
    c = client_with_ubq
    _consent(c)
    q = c.post("/api/quality", json={"source": UBQ, "dope": False, "procheck": False, "prosa": False}).json()
    q = wait_job(c, q["id"])
    plain = c.get(f"/api/jobs/{q['id']}/consensus").json()
    assert "predictor" not in plain and plain["methods_total"] == 6 and "predictor" not in plain["inputs"]
    html = c.get(f"/api/jobs/{q['id']}/report").text
    assert 'id="predizione"' not in html and "Accordo con" not in html
    pred = _predict(c, {"source": UBQ})
    cons = c.get(f"/api/jobs/{q['id']}/consensus", params={"predict_job": pred["id"]}).json()
    assert cons["methods_total"] == 7 and any(m["key"] == "predictor" for m in cons["methods"])
    assert cons["inputs"]["predictor"] == {"job_id": pred["id"], "predictor": "esmfold", "label": "ESMFold",
                                           "status": "divergenze locali"}
    assert cons["predictor"]["flagged"] == 7
    # report: il confronto più recente sulla stessa struttura, sezione bilanciata, consenso con l'accordo
    html = c.get(f"/api/jobs/{q['id']}/report").text
    assert 'id="predizione"' in html and "Confronto con una predizione (ESMFold)" in html
    assert "divergenze locali" in html and "Accordo con ESMFold" in html and "Lin Z." in html and "Xu J." in html
    assert html.count("<section") == html.count("</section>")
    assert c.get(f"/api/jobs/{q['id']}/report", params={"predict_job": pred["id"]}).status_code == 200


def test_consensus_route_rejects_other_structures_and_jobs(client_with_ubq, services, tmp_path):
    c = client_with_ubq
    _consent(c)
    q = wait_job(c, c.post("/api/quality", json={"source": UBQ, "dope": False, "procheck": False,
                                                  "prosa": False}).json()["id"])
    other = c.post("/api/templates/upload", files={"file": ("ubq_zn.pdb", (DATA / "ubq_zn.pdb").read_bytes())},
                   data={"template_id": "UBQZN"})
    assert other.status_code == 200
    services.esm = [(200, ubq_prediction())]
    pred = _predict(c, {"source": {"type": "template", "id": "UBQZN", "chain": "A"}})
    r = c.get(f"/api/jobs/{q['id']}/consensus", params={"predict_job": pred["id"]})
    assert r.status_code == 400 and r.json()["error"]["code"] == "predict_source_mismatch"
    r = c.get(f"/api/jobs/{q['id']}/report", params={"predict_job": pred["id"]})
    assert r.status_code == 400 and r.json()["error"]["code"] == "predict_source_mismatch"
    r = c.get(f"/api/jobs/{q['id']}/consensus", params={"predict_job": q["id"]})
    assert r.status_code == 400 and r.json()["error"]["code"] == "predict_job_invalid"
    only = _predict(c, {"sequence": {"name": "ubq", "sequence": HUMAN_UB}})  # senza confronto
    assert only["result"]["plausibility"] is None and only["title"] == "Predizione ESMFold · ubq"
    r = c.get(f"/api/jobs/{q['id']}/consensus", params={"predict_job": only["id"]})
    assert r.status_code == 400 and r.json()["error"]["code"] == "predict_job_invalid"


def test_report_section_render_and_escape():
    plaus = {"status": "divergenze locali", "summary": "divergono <script>", "label": "ESMFold",
             "lddt_confident": 0.87, "confident_residues": 76, "pairs": 76,
             "regions": [{"chain": "A", "start": "31", "end": "37", "divergent": 7, "length": 7, "lddt_mean": 0.24,
                          "plddt_mean": 90.0, "confidence": "sicura", "consensus": ["DOPE & co"]}],
             "notes": ["nota"], "limits": "limiti"}
    data = {"kind": "quality", "title": "t", "job_id": "abc", "tools": [("BioLoom", "4")], "warnings": [],
            "prediction": {"job_id": "p1", "finished_at": "2026-10-01T10:00:00+00:00", "predictor": "esmfold",
                           "label": "ESMFold", "origin": {"service": "https://api.esmatlas.com/x", "scale": "0-1"},
                           "plddt": {"mean": 90.0}, "chain": "A", "sequence": {"length": 76},
                           "comparison": {"tm_score": 0.927, "lddt": 0.878, "gdt_ts": 95.07, "rmsd": 1.74},
                           "plausibility": plaus}}
    html = report.render(data)
    assert 'id="predizione"' in html and "divergono &lt;script&gt;" in html and "<script>" not in html
    assert "DOPE &amp; co" in html and "0,927" in html and '<span class="tag warn">sicura</span>' in html
    assert "riportato da 0–1 a 0–100" in html and html.count("<section") == html.count("</section>")
    assert 'id="predizione"' not in report.render({**data, "prediction": None})


# ------------------------------------------------------------------------------------------ controlli preventivi
def _preflight(client, params: dict) -> dict:
    r = client.post("/api/compute/preflight", json={"kind": "predict", "params": params})
    assert r.status_code == 200, r.text
    return r.json()


def _codes(result: dict, level: str) -> list[str]:
    return [c["code"] for c in result["checks"] if c["level"] == level]


def test_preflight_predict(client_with_ubq, services):
    c = client_with_ubq
    res = _preflight(c, {"source": UBQ})
    assert _codes(res, "error") == ["esmfold_remote_disabled"] and not res["ok"]
    _consent(c)
    res = _preflight(c, {"source": UBQ})
    assert res["ok"] and "remote_esmfold" in _codes(res, "info") and "estimated_time" in _codes(res, "info")
    assert res["estimate"]["low"] == pytest.approx(1.1) and res["estimate"]["high"] == pytest.approx(125.0)
    assert "76 residui" in res["estimate"]["detail"]
    res = _preflight(c, {"sequence": {"name": "x", "sequence": "A" * 401}})
    assert _codes(res, "error") == ["sequence_too_long"]
    res = _preflight(c, {"predictor": "alphafold", "source": UBQ})
    assert _codes(res, "error") == ["accession_required"]
    res = _preflight(c, {"predictor": "alphafold", "source": UBQ, "accession": "P0CG48"})
    assert res["ok"] and "remote_alphafold" in _codes(res, "info")
    res = _preflight(c, {"sequence": {"name": "x", "sequence": HUMAN_UB}})
    assert "predict_no_compare" in _codes(res, "info")
    res = _preflight(c, {"predictor": "x"})
    assert _codes(res, "error") == ["validation_error"]
    assert services.calls == []  # nessuna richiesta remota dai controlli
    _predict(c, {"source": UBQ})
    res = _preflight(c, {"source": UBQ})
    assert "esmfold_cached" in _codes(res, "info") and "remote_esmfold" not in _codes(res, "info")
    assert res["estimate"]["high"] == pytest.approx(6.0)
