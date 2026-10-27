"""Lunghezza massima della sequenza bersaglio come impostazione (`max_sequence_length`): limiti, file scritti a mano,
e lo stesso valore in modellazione, pipeline, controlli prima dell'avvio, disordine e copertura."""

from __future__ import annotations

import json

import pytest

from makphi.config import SEQUENCE_MAX_DEFAULT, SEQUENCE_MAX_LIMIT, SEQUENCE_MAX_MIN, SettingsStore, UserSettings

from .conftest import YEAST_UB, wait_job
from .test_preflight import by_code, codes, env, model_params, pipeline_params, run  # noqa: F401 (fixture env)


# ---------------------------------------------------------------------------------------------- impostazione
def test_default_and_bounds():
    assert UserSettings().max_sequence_length == SEQUENCE_MAX_DEFAULT == 5000
    assert (SEQUENCE_MAX_MIN, SEQUENCE_MAX_LIMIT) == (100, 9999)
    assert UserSettings(max_sequence_length=SEQUENCE_MAX_LIMIT).max_sequence_length == 9999
    for bad in (SEQUENCE_MAX_MIN - 1, SEQUENCE_MAX_LIMIT + 1):
        with pytest.raises(ValueError):
            UserSettings(max_sequence_length=bad)


@pytest.mark.parametrize("value", [50, 10000, "8000", None, True, 7000.5])
def test_hand_edited_value_falls_back_to_default(tmp_path, value):
    """Un valore non valido scritto a mano in settings.json torna al predefinito e non azzera le altre impostazioni."""
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"max_sequence_length": value, "contact_email": "a@b.it"}))
    s = SettingsStore(path).get()
    assert s.max_sequence_length == SEQUENCE_MAX_DEFAULT and s.contact_email == "a@b.it"


def test_previous_settings_file_gets_default(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"max_workers": 3}))
    assert SettingsStore(path).get().max_sequence_length == SEQUENCE_MAX_DEFAULT


def test_settings_api_saves_and_rejects(client):
    r = client.put("/api/settings", json={"max_sequence_length": 8000})
    assert r.status_code == 200 and r.json()["max_sequence_length"] == 8000
    assert client.get("/api/settings").json()["max_sequence_length"] == 8000
    r = client.put("/api/settings", json={"max_sequence_length": 10000})
    assert r.status_code == 400
    assert "Lunghezza massima della sequenza (residui): deve essere al massimo 9999" in r.json()["error"]["message"]
    assert client.get("/api/settings").json()["max_sequence_length"] == 8000


# ---------------------------------------------------------------------------------------------- uso
def test_model_job_uses_the_setting(client_with_ubq):
    client_with_ubq.put("/api/settings", json={"max_sequence_length": 100})
    body = model_params("A" * 101)
    job = wait_job(client_with_ubq, client_with_ubq.post("/api/model", json=body).json()["id"])
    assert job["status"] == "failed" and "Sequenza troppo lunga (101 residui, massimo 100" in job["error"]


def test_preflight_uses_the_setting(client_with_ubq, env):  # noqa: F811 (fixture)
    long = "MKVLE" * 1200  # 6000 residui: oltre il predefinito
    assert "sequence_too_long" in codes(run(client_with_ubq, "model", model_params(long)), "error")
    client_with_ubq.put("/api/settings", json={"max_sequence_length": 6000})
    assert "sequence_too_long" not in codes(run(client_with_ubq, "model", model_params(long)))
    client_with_ubq.put("/api/settings", json={"max_sequence_length": 100})
    assert "sequence_too_long" in codes(run(client_with_ubq, "model", model_params(YEAST_UB + "A" * 30)), "error")
    search = run(client_with_ubq, "search", {"query": {"name": "q", "sequence": "A" * 101}, "engine": "rcsb"})
    assert "sequence_too_long" in codes(search, "error")


def test_pipeline_warns_and_fails_items_over_the_setting(client_with_ubq, env):  # noqa: F811 (fixture)
    client_with_ubq.put("/api/settings", json={"max_sequence_length": 100})
    fasta = f">lunga\n{'A' * 101}\n"
    out = run(client_with_ubq, "pipeline", pipeline_params(fasta))
    assert "i 100 residui" in by_code(out, "pipeline_too_long")["message"]
    body = {"fasta": fasta, "template_mode": "fixed", "templates": [{"id": "1UBQ", "chains": ["A"]}],
            "options": {"models": 1}, "quality": False, "parallel": 1}
    job = wait_job(client_with_ubq, client_with_ubq.post("/api/pipeline", json=body).json()["id"])
    items = client_with_ubq.get(f"/api/pipeline/{job['id']}/items").json()["items"]
    assert items[0]["status"] == "failed" and "massimo 100" in items[0]["error"]


def test_disorder_and_coverage_use_the_setting(client):
    seq = "MKVLE" * 1200
    assert client.post("/api/bio/disorder", json={"sequence": seq}).json()["error"]["code"] == "sequence_too_long"
    client.put("/api/settings", json={"max_sequence_length": 6000})
    assert client.post("/api/bio/disorder", json={"sequence": seq}).json()["length"] == 6000
    assert client.post("/api/bio/coverage", json={"sequence": seq}).status_code == 200
    client.put("/api/settings", json={"max_sequence_length": 5999})
    r = client.post("/api/bio/coverage", json={"sequence": seq})
    assert r.status_code == 400 and r.json()["error"]["code"] == "sequence_too_long"
