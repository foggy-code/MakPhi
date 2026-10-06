"""AlphaFold DB come sorgente di template (F5.1): client, libreria, motore di ricerca, ritaglio per pLDDT.

Nessun accesso alla rete: AlphaFold DB (EBI) è simulato con httpx.MockTransport sul client condiviso di
engines/remote.py e NCBI BLAST con monkeypatch. Il modello AlphaFold di prova è 1UBQ con i B-factor riscritti come
pLDDT e l'header dei file di AlphaFold DB.
"""

from __future__ import annotations

import copy
import json
import re
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from bioloom.config import UserSettings
from bioloom.core.errors import AppError, NotFound, RemoteServiceError
from bioloom.core.jobs import JobCancelled
from bioloom.domain import plddt, sequences, structure
from bioloom.engines import modeller, remote, search
from bioloom.services.compute import ComputeService
from bioloom.services.templates import TemplateLibrary

from .conftest import DATA, HUMAN_UB, YEAST_UB, requires_modeller, wait_job

AF_HEADER = [
    "HEADER                                            01-AUG-25",
    "TITLE     ALPHAFOLD MONOMER V2.0 PREDICTION FOR POLYUBIQUITIN-C (P0CG48)",
    "COMPND    MOL_ID: 1;",
    "COMPND   2 MOLECULE: POLYUBIQUITIN-C;",
    "REMARK   1 ALPHAFOLD DATA, COPYRIGHT (2021) DEEPMIND TECHNOLOGIES LIMITED. THE",
    "REMARK   1 INFORMATION PROVIDED IS THEORETICAL MODELLING ONLY.",
    "DBREF  XXXX A    1    76  UNP    P0CG48   UBC_HUMAN        1     76",
]


def tails_low(resnum: int) -> float:
    """pLDDT di prova: code N- e C-terminali a bassa confidenza (1–5 → 40, 71–76 → 30), il resto 90."""
    return 40.0 if resnum <= 5 else 30.0 if resnum >= 71 else 90.0


def af_pdb(value=tails_low, header: bool = True) -> str:
    """Catena A di 1UBQ (senza acque) con il B-factor riscritto come pLDDT = value(numero del residuo)."""
    lines = [ln for ln in (DATA / "1UBQ.pdb").read_text(encoding="utf-8").splitlines() if ln.startswith("ATOM")]
    out = [ln[:60] + f"{value(int(ln[22:26])):6.2f}" + ln[66:] for ln in lines]
    return "\n".join((AF_HEADER if header else []) + out + ["TER", "END"]) + "\n"


def af_entry(acc: str = "P0CG48", sequence: str = HUMAN_UB, fragment: int = 1, version: int = 6, **over) -> dict:
    """Voce di /api/prediction nel formato reale (2025, v6), ridotta ai campi rilevanti."""
    eid = f"AF-{acc}-F{fragment}"
    entry = {
        "toolUsed": "AlphaFold Monomer v2.0 pipeline", "providerId": "GDM", "entityType": "protein",
        "modelEntityId": eid, "modelCreatedDate": "2025-08-01T00:00:00Z", "globalMetricValue": 88.62,
        "fractionPlddtVeryLow": 0.004, "fractionPlddtLow": 0.041, "fractionPlddtConfident": 0.384,
        "fractionPlddtVeryHigh": 0.571, "latestVersion": version, "allVersions": list(range(1, version + 1)),
        "sequence": sequence, "sequenceStart": 1, "sequenceEnd": len(sequence), "gene": "UBC",
        "uniprotAccession": acc, "uniprotId": "UBC_HUMAN", "uniprotDescription": "Polyubiquitin-C", "taxId": 9606,
        "organismScientificName": "Homo sapiens", "chainId": "A", "isComplex": False, "entryId": eid,
        "pdbUrl": f"https://alphafold.ebi.ac.uk/files/{eid}-model_v{version}.pdb",
        "cifUrl": f"https://alphafold.ebi.ac.uk/files/{eid}-model_v{version}.cif",
        "paeDocUrl": f"https://alphafold.ebi.ac.uk/files/{eid}-predicted_aligned_error_v{version}.json",
        "uniprotSequence": sequence, "uniprotStart": 1, "uniprotEnd": len(sequence),
    }
    entry.update(over)
    return entry


class FakeAlphaFold:
    """AlphaFold DB simulato: risposte dell'API e file per URL, con il registro delle richieste."""

    def __init__(self) -> None:
        self.predictions: dict[str, object] = {"P0CG48": [af_entry()]}
        self.files: dict[str, tuple[int, str]] = {
            "AF-P0CG48-F1-model_v6.pdb": (200, af_pdb())}
        self.calls: list[str] = []
        self.fail_api = 0  # prossime risposte 503 dell'API

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(str(request.url))
        path = request.url.path
        if request.url.host != "alphafold.ebi.ac.uk":
            return httpx.Response(599, text="host inatteso")
        if path.startswith("/api/prediction/"):
            if self.fail_api:
                self.fail_api -= 1
                return httpx.Response(503, text="Service Unavailable")
            acc = path.rsplit("/", 1)[1]
            if acc not in self.predictions:
                return httpx.Response(404, json={})
            return httpx.Response(200, json=self.predictions[acc])
        if path.startswith("/files/"):
            status, text = self.files.get(path[len("/files/"):], (404, "<Error>NoSuchKey</Error>"))
            return httpx.Response(status, text=text)
        return httpx.Response(404, text="")

    def api_calls(self) -> list[str]:
        return [u for u in self.calls if "/api/prediction/" in u]

    def file_calls(self) -> list[str]:
        return [u for u in self.calls if "/files/" in u]


@pytest.fixture
def afdb(monkeypatch, tmp_path):
    fake = FakeAlphaFold()
    # handler letto a ogni richiesta: un test può sostituirlo (risposte lente, errori)
    monkeypatch.setattr(remote, "_client", httpx.Client(transport=httpx.MockTransport(lambda r: fake.handler(r))))
    monkeypatch.setattr(remote, "_cache_dir", tmp_path / "remote_cache")
    # nuovi tentativi senza attesa (solo le pause di remote.py, non time.sleep di tutto il processo)
    monkeypatch.setattr(remote, "_sleep_checked", lambda seconds, check, sleep: None)
    return fake


@pytest.fixture
def no_ncbi(monkeypatch):
    def forbidden(*_a, **_k):
        raise AssertionError("NCBI BLAST non deve essere usato con l'accessione UniProt")

    monkeypatch.setattr(remote, "ncbi_blast", forbidden)


# ------------------------------------------------------------------------------------------ parser dell'API
def test_parse_real_format():
    [e] = remote.parse_alphafold_entries([af_entry()], "P0CG48")
    assert e["entry_id"] == "AF-P0CG48-F1" and e["accession"] == "P0CG48" and e["fragment"] == 1
    assert e["version"] == 6 and e["versions"] == [1, 2, 3, 4, 5, 6] and e["plddt"] == 88.62
    assert (e["uniprot_start"], e["uniprot_end"]) == (1, 76) and e["sequence"] == HUMAN_UB
    assert e["pdb_url"].endswith("AF-P0CG48-F1-model_v6.pdb") and e["cif_url"].endswith("-model_v6.cif")
    assert e["pae_url"].endswith("predicted_aligned_error_v6.json")
    assert e["plddt_fractions"] == {"very_high": 0.571, "confident": 0.384, "low": 0.041, "very_low": 0.004}
    assert (e["gene"], e["organism"], e["description"], e["chain"]) == ("UBC", "Homo sapiens", "Polyubiquitin-C", "A")


def test_parse_tolerates_missing_fields_and_older_versions():
    # API del 2021-2022: niente latestVersion, globalMetricValue, uniprotStart/End né "sequence"
    old = {"entryId": "AF-P69905-F1", "uniprotAccession": "P69905", "uniprotSequence": "MVLSPADKTNVKAAWGKVGAHAGEYGAE",
           "pdbUrl": "https://alphafold.ebi.ac.uk/files/AF-P69905-F1-model_v4.pdb"}
    [e] = remote.parse_alphafold_entries([old], "P69905")
    assert e["version"] == 4 and e["plddt"] is None and e["plddt_fractions"] is None
    assert e["cif_url"] == "https://alphafold.ebi.ac.uk/files/AF-P69905-F1-model_v4.cif"
    assert (e["uniprot_start"], e["uniprot_end"]) == (1, 28) and e["chain"] == "A" and e["gene"] == ""
    # solo allVersions, nessun URL: URL ricostruiti dall'identificativo e dall'ultima versione
    [e] = remote.parse_alphafold_entries([{"entryId": "AF-P69905-F1", "allVersions": [1, 2, 3], "sequence": "MV"}],
                                         "P69905")
    assert e["version"] == 3 and e["pdb_url"] == "https://alphafold.ebi.ac.uk/files/AF-P69905-F1-model_v3.pdb"
    # senza alcun identificativo: costruito dall'accessione (frammento 1); valori fuori scala ignorati
    [e] = remote.parse_alphafold_entries({"uniprotAccession": "P69905", "globalMetricValue": 140, "latestVersion": "x"},
                                         "P69905")
    assert e["entry_id"] == "AF-P69905-F1" and e["plddt"] is None and e["version"] is None and e["pdb_url"] is None
    assert remote.parse_alphafold_entries({}, "P69905") == [] and remote.parse_alphafold_entries(None, "P69905") == []
    assert remote.parse_alphafold_entries("testo", "P69905") == []


def test_parse_several_entries_fragments_and_duplicates():
    full = "M" + "A" * 2999
    data = [
        af_entry("Q8WZ42", full, fragment=2, uniprotStart=201, uniprotEnd=1600),  # sequenze UniProt intere
        af_entry("Q8WZ42", full[:1400], fragment=1, version=4, uniprotStart=1, uniprotEnd=1400),
        af_entry("Q8WZ42", full[:1400], fragment=1, version=6, uniprotStart=1, uniprotEnd=1400),
        {"entryId": "AF-0000000066503175", "isComplex": True},  # complesso: non è un template monomerico
        "rumore",
    ]
    entries = remote.parse_alphafold_entries(data, "Q8WZ42")
    assert [e["entry_id"] for e in entries] == ["AF-Q8WZ42-F1", "AF-Q8WZ42-F2"]
    assert entries[0]["version"] == 6  # a parità di identificativo, la versione più recente
    assert entries[1]["sequence"] == full[200:1600] and len(entries[1]["sequence"]) == 1400


def test_parse_ignores_urls_outside_alphafold_db():
    [e] = remote.parse_alphafold_entries([af_entry(pdbUrl="https://example.org/x.pdb", paeDocUrl="file:///etc/passwd")],
                                         "P0CG48")
    assert e["pdb_url"] == "https://alphafold.ebi.ac.uk/files/AF-P0CG48-F1-model_v6.pdb" and e["pae_url"] is None


@pytest.mark.parametrize("bad", ["", "   ", "P0CG48-2", "12345", "ABCDEF", "P0CG4", "1UBQ", "AF-P0CG48", "P0CG48;rm"])
def test_invalid_accession_rejected_without_requests(afdb, bad):
    with pytest.raises(AppError) as err:
        remote.alphafold_entries(bad)
    assert err.value.code == "invalid_accession" and "Accessione UniProt non valida" in err.value.message
    assert afdb.calls == []


def test_accession_format_and_alphafold_ids():
    assert remote.validate_uniprot_accession(" p69905 ") == "P69905"
    assert remote.validate_uniprot_accession("A0A0B4J2F0") == "A0A0B4J2F0"  # formato a 10 caratteri
    assert remote.parse_alphafold_id("af-p0cg48-f2") == ("P0CG48", 2)
    assert remote.parse_alphafold_id("AF-P0CG48-F0") is None and remote.parse_alphafold_id("1UBQ") is None
    assert remote.parse_alphafold_id("AF-ABCDEF-F1") is None  # accessione non UniProt


def test_entries_404_is_a_clear_not_found_and_is_not_cached(afdb):
    for _ in range(2):
        with pytest.raises(NotFound) as err:
            remote.alphafold_entries("P69905")
        assert err.value.code == "alphafold_not_found" and err.value.status == 404
        assert "AlphaFold DB non ha un modello per l'accessione UniProt P69905" in err.value.message
    assert len(afdb.api_calls()) == 2
    afdb.predictions["P69905"] = []  # risposta senza voci utilizzabili: stesso esito
    with pytest.raises(NotFound):
        remote.alphafold_entries("P69905")


def test_entries_are_cached_like_other_remote_answers(afdb, tmp_path):
    assert remote.alphafold_entries("p0cg48")[0]["entry_id"] == "AF-P0CG48-F1"
    assert remote.alphafold_entries("P0CG48")[0]["plddt"] == 88.62
    assert len(afdb.api_calls()) == 1
    assert list((tmp_path / "remote_cache" / "alphafold").glob("*.json"))


def test_transient_errors_are_retried_then_reported(afdb):
    afdb.fail_api = 2
    assert remote.alphafold_entries("P0CG48")  # due 503, poi la risposta
    assert len(afdb.api_calls()) == 3
    afdb.predictions["P69905"] = [af_entry("P69905")]
    afdb.fail_api = 3
    with pytest.raises(RemoteServiceError):
        remote.alphafold_entries("P69905")


def test_download_pdb_then_cif_and_validation(afdb):
    [entry] = remote.parse_alphafold_entries([af_entry()], "P0CG48")
    text, fmt = remote.alphafold_download(entry)
    assert fmt == "pdb" and text.startswith("HEADER") and "ALPHAFOLD" in text
    del afdb.files["AF-P0CG48-F1-model_v6.pdb"]  # PDB assente: mmCIF
    afdb.files["AF-P0CG48-F1-model_v6.cif"] = (200, "data_AF\nloop_\n_atom_site.id\nATOM 1 N N . MET A 1 1\n")
    assert remote.alphafold_download(entry)[1] == "cif"
    afdb.files["AF-P0CG48-F1-model_v6.cif"] = (200, "<html>manutenzione</html>")  # pagina d'errore con stato 200
    with pytest.raises(RemoteServiceError, match="non contiene coordinate"):
        remote.alphafold_download(entry)
    del afdb.files["AF-P0CG48-F1-model_v6.cif"]
    with pytest.raises(NotFound, match="né PDB né mmCIF"):
        remote.alphafold_download(entry)


# ------------------------------------------------------------------------------------------ libreria dei template
def test_library_saves_the_model_with_meta_and_original_records(client, afdb):
    r = client.post("/api/templates/alphafold", json={"accession": "p0cg48"})
    assert r.status_code == 200, r.text
    t = r.json()
    assert t["id"] == "AF-P0CG48-F1" and t["cached"] is False and t["format"] == "pdb"
    assert t["source"] == "alphafold" and t["predicted"] is True
    assert t["note"] == "modello predetto, non struttura sperimentale"
    assert t["plddt_mean"] == 88.62 and t["method"] == "Modello predetto (AlphaFold)" and t["resolution"] is None
    af = t["alphafold"]
    assert (af["accession"], af["version"], af["fragment"], af["uniprot_start"], af["uniprot_end"]) == \
        ("P0CG48", 6, 1, 1, 76)
    # pLDDT per residuo dal file: 65 residui a 90 (molto alta), 5 a 40 e 6 a 30 (molto bassa)
    assert t["plddt_fractions"] == {"very_high": round(65 / 76, 3), "confident": 0.0, "low": 0.0,
                                    "very_low": round(11 / 76, 3)}
    text = client.get("/api/templates/AF-P0CG48-F1/file").text
    assert text.splitlines()[:2] == [AF_HEADER[0], AF_HEADER[1]]  # HEADER/TITLE/REMARK originali conservati
    assert "REMARK   1 ALPHAFOLD DATA" in text
    [item] = client.get("/api/templates").json()
    assert item["predicted"] is True and item["plddt_mean"] == 88.62 and item["alphafold"]["version"] == 6
    analysis = client.get("/api/templates/AF-P0CG48-F1").json()
    assert analysis["chains"][0]["residues"] == 76 and analysis["meta"]["predicted"] is True
    # già in libreria: nessuna nuova richiesta a EBI, né per accessione né per identificativo
    before = len(afdb.calls)
    assert client.post("/api/templates/alphafold", json={"accession": "P0CG48"}).json()["cached"] is True
    assert client.post("/api/templates/alphafold", json={"accession": "AF-P0CG48-F1"}).json()["cached"] is True
    assert len(afdb.calls) == before
    assert client.app.state.core.library.is_predicted("AF-P0CG48-F1")


def test_library_errors_are_clear(client, afdb):
    bad = client.post("/api/templates/alphafold", json={"accession": "P0CG48-2"})
    assert bad.status_code == 400 and bad.json()["error"]["code"] == "invalid_accession"
    missing = client.post("/api/templates/alphafold", json={"accession": "P69905"})
    assert missing.status_code == 404 and "P69905" in missing.json()["error"]["message"]
    afdb.files["AF-P0CG48-F1-model_v6.pdb"] = (200, "ATOM  spazzatura\n")  # coordinate illeggibili
    broken = client.post("/api/templates/alphafold", json={"accession": "P0CG48"})
    assert broken.status_code == 502 and not client.get("/api/templates").json()


def test_library_fragments_of_long_proteins(client, afdb):
    afdb.predictions["Q8WZ42"] = [af_entry("Q8WZ42", HUMAN_UB, fragment=1), af_entry("Q8WZ42", HUMAN_UB, fragment=2)]
    afdb.files["AF-Q8WZ42-F2-model_v6.pdb"] = (200, af_pdb())
    t = client.post("/api/templates/alphafold", json={"accession": "AF-Q8WZ42-F2"}).json()
    assert t["id"] == "AF-Q8WZ42-F2" and t["alphafold"]["fragment"] == 2
    assert [u.rsplit("/", 1)[1] for u in afdb.file_calls()] == ["AF-Q8WZ42-F2-model_v6.pdb"]
    none = client.post("/api/templates/alphafold", json={"accession": "AF-Q8WZ42-F9"})
    assert none.status_code == 404 and "AF-Q8WZ42-F1, AF-Q8WZ42-F2" in none.json()["error"]["message"]


def test_concurrent_fetches_download_the_model_once(tmp_path, afdb):
    """Elementi di pipeline in parallelo con lo stesso template AlphaFold: un solo download, nessun file a metà."""
    library = TemplateLibrary(tmp_path / "templates")
    handler = afdb.handler
    started = threading.Event()

    def slow(request):
        if "/files/" in request.url.path:
            started.set()
            time.sleep(0.3)
        return handler(request)

    afdb.handler = slow
    results: list[dict] = []
    errors: list[Exception] = []

    def fetch():
        try:
            results.append(library.fetch_alphafold("P0CG48"))
        except Exception as exc:  # raccolto e verificato sotto
            errors.append(exc)

    threads = [threading.Thread(target=fetch) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(10)
    assert not errors and len(results) == 4 and started.is_set()
    assert len(afdb.file_calls()) == 1 and len(afdb.api_calls()) == 1
    assert sorted(r["cached"] for r in results) == [False, True, True, True]
    assert {r["id"] for r in results} == {"AF-P0CG48-F1"} and library.is_predicted("AF-P0CG48-F1")


class Cancelled:
    """`check` di un job che si può annullare dal test (come JobContext.check)."""

    def __init__(self) -> None:
        self.event = threading.Event()
        self.calls = 0

    def __call__(self) -> None:
        self.calls += 1
        if self.event.is_set():
            raise JobCancelled()


def test_alphafold_download_stops_as_soon_as_the_job_is_cancelled(tmp_path, afdb):
    """EBI risponde 503 al file e il job viene annullato durante il download: nessun altro tentativo."""
    library = TemplateLibrary(tmp_path / "templates")
    cancelled = Cancelled()
    afdb.files["AF-P0CG48-F1-model_v6.pdb"] = (503, "Service Unavailable")
    handler = afdb.handler

    def cancel_on_download(request):
        if "/files/" in request.url.path:
            cancelled.event.set()  # l'utente annulla mentre il file è in arrivo
        return handler(request)

    afdb.handler = cancel_on_download
    with pytest.raises(JobCancelled):
        library.fetch_alphafold("P0CG48", check=cancelled)
    assert len(afdb.file_calls()) == 1 and not library.exists("AF-P0CG48-F1")


def test_waiting_for_the_same_alphafold_download_can_be_cancelled(tmp_path, afdb):
    """Un elemento di pipeline in attesa del download dello stesso modello (lock per template) si ferma
    all'annullamento, senza aspettare l'altro e senza scaricare di nuovo."""
    library = TemplateLibrary(tmp_path / "templates")
    lock = library._fetch_lock("AF-P0CG48-F1")
    lock.acquire()  # un altro elemento sta scaricando lo stesso modello
    cancelled = Cancelled()
    outcome: dict = {}

    def fetch():
        try:
            library.fetch_alphafold("P0CG48", check=cancelled)
        except JobCancelled as exc:
            outcome["cancelled"] = exc

    thread = threading.Thread(target=fetch)
    try:
        thread.start()
        time.sleep(0.2)
        assert thread.is_alive()  # in attesa del lock
        cancelled.event.set()
        thread.join(3)
        assert not thread.is_alive() and "cancelled" in outcome
    finally:
        lock.release()
    assert afdb.calls == [] and cancelled.calls >= 1
    # il lock è libero: il download successivo procede
    assert library.fetch_alphafold("P0CG48")["cached"] is False


def test_modelling_passes_cancellation_to_the_alphafold_download(service, tmp_path, afdb):
    ctx = Ctx()
    ctx.check = Cancelled()
    afdb.files["AF-P0CG48-F1-model_v6.pdb"] = (503, "Service Unavailable")
    handler = afdb.handler

    def cancel_on_download(request):
        if "/files/" in request.url.path:
            ctx.check.event.set()
        return handler(request)

    afdb.handler = cancel_on_download
    with pytest.raises(JobCancelled):
        service._prepare_templates(ctx, [modeller.TemplateSpec("AF-P0CG48-F1", ["A"])], tmp_path / "w",
                                   plddt_min=70)
    assert len(afdb.file_calls()) == 1


def test_templates_registered_before_the_predicted_flag_are_completed(tmp_path, ubq_path):
    """Cartella dati precedente ai modelli predetti: meta.json senza `predicted`. Elenco, dettaglio e modellazione
    danno la stessa risposta, e i metadati si completano una volta."""
    root = tmp_path / "templates"
    root.mkdir()
    (root / "MIO_AF.pdb").write_text(af_pdb(), encoding="utf-8")
    (root / "1UBQ.pdb").write_bytes(ubq_path.read_bytes())
    old = {"source": "caricamento utente", "added_at": "2026-01-01T00:00:00+00:00", "title": "", "method": "",
           "resolution": None, "chains": ["A"]}
    for tid in ("MIO_AF", "1UBQ"):
        (root / f"{tid}.meta.json").write_text(json.dumps({"id": tid, **old}), encoding="utf-8")
    library = TemplateLibrary(root)
    by_id = {t["id"]: t for t in library.list()}
    assert by_id["MIO_AF"]["predicted"] is True and by_id["1UBQ"]["predicted"] is False
    assert by_id["MIO_AF"]["plddt_mean"] == round((5 * 40 + 6 * 30 + 65 * 90) / 76, 2)
    assert library.is_predicted("MIO_AF") and not library.is_predicted("1UBQ")
    described = library.describe("MIO_AF")
    assert described["predicted"] is True and described["note"] == "modello predetto, non struttura sperimentale"
    assert described["plddt_fractions"]["very_high"] == round(65 / 76, 3)
    assert described["method"] == "Modello predetto (AlphaFold)" and described["source"] == "caricamento utente"
    saved = json.loads((root / "MIO_AF.meta.json").read_text(encoding="utf-8"))
    assert saved["predicted"] is True and saved["added_at"] == old["added_at"]  # riscritto, il resto invariato
    assert json.loads((root / "1UBQ.meta.json").read_text(encoding="utf-8"))["predicted"] is False


def test_uploaded_alphafold_files_are_recognized_only_by_certain_signs(client):
    up = client.post("/api/templates/upload", files={"file": ("mio_af.pdb", af_pdb().encode())}).json()
    assert up["predicted"] is True and up["source"] == "caricamento utente"
    assert up["plddt_mean"] == round((5 * 40 + 6 * 30 + 65 * 90) / 76, 2)
    exp = client.post("/api/templates/upload", files={"file": ("1UBQ.pdb", (DATA / "1UBQ.pdb").read_bytes())}).json()
    assert exp["predicted"] is False
    # TITLE con "AlphaFold" ma record EXPDTA: struttura sperimentale, i B-factor non sono pLDDT
    assert not plddt.is_alphafold_text("TITLE     ALPHAFOLD V2.0 PREDICTION CONFIRMED\nEXPDTA    X-RAY DIFFRACTION\n")
    assert plddt.is_alphafold_text("\n".join(AF_HEADER))
    assert plddt.is_alphafold_text("data_AF\n_ma_qa_metric_global.metric_value 88\n_software.name AlphaFold\n"
                                   "_atom_site.id\n")
    assert not plddt.is_alphafold_text("data_1UBQ\n_exptl.method 'X-RAY'\n_atom_site.id\n")


# ------------------------------------------------------------------------------------------ motore di ricerca
def _search(client, **extra) -> dict:
    body = {"query": {"name": "q", "sequence": YEAST_UB}, "engine": "alphafold", "evalue": 0.001, "max_hits": 50,
            **extra}
    job = client.post("/api/search", json=body).json()
    assert "id" in job, job
    return wait_job(client, job["id"])


def _log(client, job_id: str) -> str:
    return client.get(f"/api/jobs/{job_id}/log").json()["text"]


def test_search_with_the_uniprot_accession(client, afdb, no_ncbi):
    done = _search(client, uniprot="P0CG48")
    assert done["status"] == "completed", done
    res = done["result"]
    assert res["engine"] == "alphafold" and res["uniprot"] == "P0CG48" and res["best"] == "AF-P0CG48-F1"
    assert res["engine_label"] == "AlphaFold DB, modelli predetti dell'accessione UniProt P0CG48"
    assert done["title"] == "Ricerca template (AlphaFold DB) · q"
    [hit] = res["hits"]
    # lievito contro uomo: 3 differenze su 76 (P19S, E24D, A28S), di cui E→D e A→S simili in BLOSUM62
    assert hit["identity"] == 96.1 and hit["positives"] == 98.7 and hit["coverage"] == 100.0
    assert (hit["query_start"], hit["query_end"], hit["subject_start"], hit["subject_end"]) == (1, 76, 1, 76)
    assert hit["template_id"] == hit["pdb_id"] == "AF-P0CG48-F1" and hit["chain"] == "A"
    assert hit["source"] == "alphafold" and hit["predicted"] is True and hit["plddt"] == 88.62
    assert hit["resolution"] is None and hit["model_version"] == 6 and hit["uniprot_accession"] == "P0CG48"
    assert hit["evalue"] < 1e-30 and hit["title"] == "Polyubiquitin-C (UBC)"
    assert afdb.file_calls() == []  # la ricerca non scarica i modelli


def test_search_identity_and_coverage_on_a_partial_model(client, afdb, no_ncbi):
    # modello che copre solo i residui 1–40 del bersaglio (un frammento): copertura 40/76
    afdb.predictions["P69905"] = [af_entry("P69905", HUMAN_UB[:40])]
    [hit] = _search(client, uniprot="P69905", query={"name": "q", "sequence": HUMAN_UB})["result"]["hits"]
    assert hit["identity"] == 100.0 and hit["coverage"] == round(100 * 40 / 76, 1) and hit["query_end"] == 40


SWISSPROT_XML = """<?xml version="1.0"?>
<!DOCTYPE BlastOutput PUBLIC "-//NCBI//NCBI BlastOutput/EN" "http://www.ncbi.nlm.nih.gov/dtd/NCBI_BlastOutput.dtd">
<BlastOutput>
  <BlastOutput_program>blastp</BlastOutput_program><BlastOutput_version>BLASTP 2.16.0+</BlastOutput_version>
  <BlastOutput_reference>ref</BlastOutput_reference><BlastOutput_db>swissprot</BlastOutput_db>
  <BlastOutput_query-ID>Query_1</BlastOutput_query-ID><BlastOutput_query-def>query</BlastOutput_query-def>
  <BlastOutput_query-len>76</BlastOutput_query-len>
  <BlastOutput_param><Parameters><Parameters_matrix>BLOSUM62</Parameters_matrix><Parameters_expect>0.001</Parameters_expect>
  <Parameters_gap-open>11</Parameters_gap-open><Parameters_gap-extend>1</Parameters_gap-extend><Parameters_filter>F</Parameters_filter>
  </Parameters></BlastOutput_param>
  <BlastOutput_iterations><Iteration>
    <Iteration_iter-num>1</Iteration_iter-num><Iteration_query-ID>Query_1</Iteration_query-ID>
    <Iteration_query-def>query</Iteration_query-def><Iteration_query-len>76</Iteration_query-len>
    <Iteration_hits>
{hits}
    </Iteration_hits>
    <Iteration_stat><Statistics><Statistics_db-num>2</Statistics_db-num><Statistics_db-len>152</Statistics_db-len>
      <Statistics_hsp-len>0</Statistics_hsp-len><Statistics_eff-space>0</Statistics_eff-space><Statistics_kappa>0.041</Statistics_kappa>
      <Statistics_lambda>0.267</Statistics_lambda><Statistics_entropy>0.14</Statistics_entropy></Statistics></Iteration_stat>
  </Iteration></BlastOutput_iterations>
</BlastOutput>
"""


def _xml_hit(n: int, hit_id: str, accession: str, hit_def: str, evalue: str) -> str:
    return (f"<Hit><Hit_num>{n}</Hit_num><Hit_id>{hit_id}</Hit_id><Hit_def>{hit_def}</Hit_def>"
            f"<Hit_accession>{accession}</Hit_accession><Hit_len>76</Hit_len><Hit_hsps><Hsp>"
            f"<Hsp_num>1</Hsp_num><Hsp_bit-score>150.2</Hsp_bit-score><Hsp_score>380</Hsp_score>"
            f"<Hsp_evalue>{evalue}</Hsp_evalue><Hsp_query-from>1</Hsp_query-from><Hsp_query-to>76</Hsp_query-to>"
            f"<Hsp_hit-from>1</Hsp_hit-from><Hsp_hit-to>76</Hsp_hit-to><Hsp_query-frame>0</Hsp_query-frame>"
            f"<Hsp_hit-frame>0</Hsp_hit-frame><Hsp_identity>73</Hsp_identity><Hsp_positive>75</Hsp_positive>"
            f"<Hsp_gaps>0</Hsp_gaps><Hsp_align-len>76</Hsp_align-len><Hsp_qseq>{YEAST_UB}</Hsp_qseq>"
            f"<Hsp_hseq>{HUMAN_UB}</Hsp_hseq><Hsp_midline>{YEAST_UB}</Hsp_midline></Hsp></Hit_hsps></Hit>")


BLAST_HITS = SWISSPROT_XML.replace("{hits}", "\n".join([
    _xml_hit(1, "sp|P0CG48.1|UBC_HUMAN", "P0CG48", "RecName: Full=Polyubiquitin-C", "3e-50"),
    _xml_hit(2, "sp|P0CG47.1|", "P0CG47", "RecName: Full=Polyubiquitin-B", "4e-50"),
    _xml_hit(3, "sp|P0CG48.1|UBC_HUMAN", "P0CG48", "RecName: Full=Polyubiquitin-C (ripetuto)", "5e-40"),
    _xml_hit(4, "gi|12345|ref|NP_000000.1|", "NP_000000", "proteina RefSeq", "1e-30"),
    _xml_hit(5, "gnl|BL_ORD_ID|7", "P62990.1", "RecName: Full=Ubiquitin", "2e-30"),
]))


def test_uniprot_accessions_from_swissprot_blast():
    hits = search.uniprot_hits_from_blast_xml(BLAST_HITS)
    assert [h["accession"] for h in hits] == ["P0CG48", "P0CG47", "P62990"]  # ordine di BLAST, senza ripetizioni
    assert hits[0]["evalue"] == 3e-50 and hits[0]["bitscore"] == 150.2
    assert search.uniprot_from_name("sp_P0CG48_UBC_HUMAN") == "P0CG48"
    assert search.uniprot_from_name("tr_A0A0B4J2F0_X") == "A0A0B4J2F0"
    assert search.uniprot_from_name("ubiquitina") == "" and search.uniprot_from_name("sp_1234_X") == ""


@pytest.fixture
def fake_swissprot(monkeypatch):
    calls = []

    def fake_blast(sequence, **kwargs):
        calls.append({"sequence": sequence, **kwargs})
        return BLAST_HITS

    monkeypatch.setattr(remote, "ncbi_blast", fake_blast)
    return calls


def test_search_without_accession_uses_ncbi_blast_on_swissprot(client, afdb, fake_swissprot):
    afdb.predictions["P62990"] = [af_entry("P62990", YEAST_UB, globalMetricValue=95.0)]
    done = _search(client)
    assert done["status"] == "completed", done
    assert len(fake_swissprot) == 1 and fake_swissprot[0]["database"] == "swissprot"
    assert fake_swissprot[0]["max_hits"] == search.ALPHAFOLD_ACCESSIONS
    hits = done["result"]["hits"]
    # P0CG47 non ha modelli (404): saltata; E-value e bit score dei modelli trovati sono quelli di BLAST
    assert [h["template_id"] for h in hits] == ["AF-P0CG48-F1", "AF-P62990-F1"]
    assert hits[0]["evalue"] == 3e-50 and hits[1]["evalue"] == 2e-30 and hits[1]["identity"] == 100.0
    # consigliato: massimo identità × copertura (100% contro 96%), non il primo per E-value
    assert done["result"]["best"] == "AF-P62990-F1"
    log = _log(client, done["id"])
    assert "Proteine Swiss-Prot più simili: P0CG48, P0CG47, P62990" in log
    assert "P0CG47: nessun modello in AlphaFold DB" in log
    assert done["result"]["notes"] == ["Senza modello in AlphaFold DB: P0CG47."]  # anche nell'interfaccia
    assert sorted(u.rsplit("/", 1)[1] for u in afdb.api_calls()) == ["P0CG47", "P0CG48", "P62990"]


def test_alphafold_search_uses_the_compute_cache(client, afdb, fake_swissprot):
    first = _search(client, uniprot="P0CG48")
    second = _search(client, uniprot="P0CG48")
    assert second["result"]["cached"] is True and second["result"]["hits"] == first["result"]["hits"]
    assert len(afdb.api_calls()) == 1
    # accessione diversa (o assente, cioè BLAST): mancato riscontro
    afdb.predictions["P62990"] = [af_entry("P62990", YEAST_UB)]
    assert not _search(client, uniprot="P62990")["result"].get("cached")
    assert not _search(client)["result"].get("cached") and len(fake_swissprot) == 1
    assert _search(client)["result"]["cached"] is True and len(fake_swissprot) == 1
    # ricalcolo forzato
    assert not _search(client, uniprot="P0CG48", use_cache=False)["result"].get("cached")


def test_alphafold_unavailable_for_every_protein_fails_the_search(client, afdb, fake_swissprot):
    afdb.fail_api = 99  # EBI non risponde per nessuna accessione: errore, non "0 template trovati"
    done = _search(client)
    assert done["status"] == "failed"
    assert "AlphaFold DB non disponibile" in done["error"] and "P0CG48, P0CG47, P62990" in done["error"]
    afdb.fail_api = 0
    again = _search(client)
    assert again["status"] == "completed" and not again["result"].get("cached") and again["result"]["hits"]


def test_partial_alphafold_results_are_not_cached(client, afdb, fake_swissprot):
    afdb.predictions["P62990"] = [af_entry("P62990", YEAST_UB)]
    afdb.fail_api = 3  # P0CG48 senza risposta dopo tre tentativi; P0CG47 senza modello; P62990 trovato
    done = _search(client)
    assert done["status"] == "completed", done
    assert [h["template_id"] for h in done["result"]["hits"]] == ["AF-P62990-F1"]
    notes = done["result"]["notes"]
    assert "Senza modello in AlphaFold DB: P0CG47." in notes
    assert any(n.startswith("AlphaFold DB non disponibile per P0CG48: risultato parziale") for n in notes)
    again = _search(client)
    assert not again["result"].get("cached") and len(again["result"]["hits"]) == 2
    assert again["result"]["notes"] == ["Senza modello in AlphaFold DB: P0CG47."]


def test_alphafold_search_with_an_accession_reports_why_there_are_no_models(client, afdb, no_ncbi):
    # accessione senza modello: il job fallisce con il messaggio di AlphaFold DB (non un elenco vuoto)
    missing = _search(client, uniprot="P69905")
    assert missing["status"] == "failed"
    assert "AlphaFold DB non ha un modello per l'accessione UniProt P69905" in missing["error"]
    # EBI non raggiungibile
    afdb.fail_api = 99
    down = _search(client, uniprot="P0CG48")
    assert down["status"] == "failed" and "AlphaFold DB non disponibile per l'accessione P0CG48" in down["error"]
    afdb.fail_api = 0
    ok = _search(client, uniprot="P0CG48")
    assert ok["status"] == "completed" and ok["result"]["notes"] == [] and not ok["result"].get("cached")


def test_search_rejects_an_invalid_accession_before_queueing(client):
    r = client.post("/api/search", json={"query": {"name": "q", "sequence": YEAST_UB}, "engine": "alphafold",
                                         "uniprot": "XYZ"})
    assert r.status_code == 400 and r.json()["error"]["code"] == "invalid_accession"
    assert client.get("/api/jobs?kind=search").json() == []


def test_best_template_prefers_experimental_structures_at_equal_score():
    exp = {"template_id": "1UBQ_A", "identity": 96.0, "coverage": 100.0, "resolution": None}
    af = {"template_id": "AF-P0CG48-F1", "identity": 96.0, "coverage": 100.0, "resolution": None, "predicted": True,
          "plddt": 99.0}
    assert search.best_template([af, exp])["template_id"] == "1UBQ_A"
    assert search.best_template([exp, af])["template_id"] == "1UBQ_A"
    # punteggio composito (F5.2): un punto di identità in più non basta a un modello predetto, allineare molto
    # meglio sì (la struttura sperimentale copre solo il 60% del bersaglio)
    assert search.best_template([exp, {**af, "identity": 97.0}])["template_id"] == "1UBQ_A"
    partial = {**exp, "coverage": 60.0}
    assert search.best_template([partial, af])["template_id"] == "AF-P0CG48-F1"
    low = {**af, "template_id": "AF-P0CG47-F1", "plddt": 70.0}
    assert search.best_template([low, af])["template_id"] == "AF-P0CG48-F1"  # tra modelli: pLDDT più alto


# ------------------------------------------------------------------------------------------ ritaglio per pLDDT
def _write(tmp_path: Path, text: str, name: str = "AF-P0CG48-F1.pdb") -> Path:
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


def test_prepare_template_removes_low_plddt_residues(tmp_path):
    src = _write(tmp_path, af_pdb())
    info = modeller.prepare_template(src, modeller.TemplateSpec("AF-P0CG48-F1", ["A"], plddt_min=70), tmp_path / "job")
    trim = info["plddt"]
    assert (trim["total"], trim["removed"], trim["kept"], trim["threshold"]) == (76, 11, 65, 70.0)
    assert trim["removed_ranges"] == [["1", "5", "A"], ["71", "76", "A"]]
    assert trim["mean_before"] == round(6230 / 76, 2) and trim["mean_after"] == 90.0
    assert (info["first"], info["last"], info["sequence"]) == ("6", "70", HUMAN_UB[5:70])
    assert info["code"] == "AF-P0CG48-F1_A"
    text = (tmp_path / "job" / info["file"]).read_text()
    assert text.count(" CA ") == 65
    assert text.startswith("HEADER") and "ALPHAFOLD MONOMER V2.0 PREDICTION" in text  # provenienza conservata
    assert "REMARK 999 BIOLOOM PLDDT_TRIM THRESHOLD 70 REMOVED 11 OF 76" in text
    assert "DBREF" not in text and "SEQRES" not in text
    assert [r for r in info["_residues"] if not r[3]][0] == ("A", "1", "M", False)
    # i B-factor rimasti sono ancora pLDDT
    kept = structure.first_model(structure.load_structure(tmp_path / "job" / info["file"]))
    assert {round(v) for _c, _r, v in plddt.residues(kept)} == {90}


def test_threshold_is_exclusive_and_configurable(tmp_path):
    # 1–5 a 70 (un tratto di 5 residui: non è un frammento isolato), 6 a 69,99
    src = _write(tmp_path, af_pdb(lambda n: 70.0 if n <= 5 else 69.99 if n == 6 else 95.0))
    info = modeller.prepare_template(src, modeller.TemplateSpec("AF", ["A"], plddt_min=70), tmp_path / "a")
    assert info["plddt"]["removed"] == 1 and info["first"] == "1"  # 70 resta, 69,99 no
    assert info["plddt"]["removed_ranges"] == [["6", "6", "A"]] and info["plddt"]["removed_fragments"] == 0
    info = modeller.prepare_template(src, modeller.TemplateSpec("AF", ["A"], plddt_min=50), tmp_path / "b")
    assert info["plddt"]["removed"] == 0 and info["plddt"]["removed_ranges"] == []
    assert info["sequence"] == HUMAN_UB


def test_too_few_residues_left_is_a_clear_error(tmp_path):
    # 30 residui confidenti (il minimo): si procede; 29: errore
    ok = _write(tmp_path, af_pdb(lambda n: 90.0 if 20 <= n < 50 else 20.0), "ok.pdb")
    info = modeller.prepare_template(ok, modeller.TemplateSpec("OK", ["A"], plddt_min=70), tmp_path / "ok")
    assert info["plddt"]["kept"] == plddt.MIN_TRIMMED_RESIDUES == 30
    few = _write(tmp_path, af_pdb(lambda n: 90.0 if 20 <= n < 49 else 20.0), "few.pdb")
    with pytest.raises(AppError) as err:
        modeller.prepare_template(few, modeller.TemplateSpec("FEW", ["A"], plddt_min=70), tmp_path / "few")
    assert err.value.code == "plddt_too_few"
    assert "ne restano 29 su 76 (minimo 30)" in err.value.message and "Abbassa la soglia" in err.value.message


def fragments_profile(resnum: int) -> float:
    """pLDDT che dopo la soglia 70 lascia tratti isolati di 1 (16), 3 (21–23) e 6 residui (28–33) in una regione
    a bassa confidenza, più 1–10 e 41–76 confidenti."""
    high = resnum <= 10 or resnum == 16 or 21 <= resnum <= 23 or 28 <= resnum <= 33 or resnum >= 41
    return 90.0 if high else 40.0


def test_isolated_fragments_are_removed_after_the_threshold(tmp_path):
    src = _write(tmp_path, af_pdb(fragments_profile))
    info = modeller.prepare_template(src, modeller.TemplateSpec("AF-P0CG48-F1", ["A"], plddt_min=70), tmp_path / "job")
    trim = info["plddt"]
    # sotto soglia 11–15, 17–20, 24–27, 34–40 (20 residui); frammenti di 1 e 3 residui tolti, quello di 6 resta
    assert (trim["removed_threshold"], trim["removed_fragments"], trim["min_fragment"]) == (20, 4, plddt.MIN_FRAGMENT)
    assert (trim["total"], trim["removed"], trim["kept"]) == (76, 24, 52)
    assert trim["removed_ranges"] == [["11", "27", "A"], ["34", "40", "A"]]
    kept = {r[1] for r in info["_residues"] if r[3]}
    assert {"16", "21", "22", "23"}.isdisjoint(kept) and {str(n) for n in range(28, 34)} <= kept
    text = (tmp_path / "job" / info["file"]).read_text()
    assert text.count(" CA ") == 52 and "REMOVED 24 OF 76 FRAGMENTS 4" in text
    # con lunghezza minima 1 restano tutti i tratti sopra soglia
    loose = plddt.low_confidence(structure.first_model(structure.load_structure(src)), ["A"], 70, min_fragment=1)
    assert (loose["below"], loose["fragments"], len(loose["removed"])) == (20, 0, 20)
    # log e avviso lo dicono
    ctx = Ctx()
    library = TemplateLibrary(tmp_path / "templates")
    library.save_upload("AF-P0CG48-F1.pdb", src.read_bytes())
    service = ComputeService(SimpleNamespace(register=lambda *a, **k: None), library, tmp_path / "u", tmp_path / "d")
    [t] = service._prepare_templates(ctx, [modeller.TemplateSpec("AF-P0CG48-F1", ["A"])], tmp_path / "w", plddt_min=70)
    assert ("rimossi 20 residui con pLDDT < 70 e 4 in frammenti isolati di meno di 5 residui (11–27:A, 34–40:A), "
            "restano 52 di 76") in ctx.logs[-1]
    assert "rimossi 20 residui con pLDDT < 70 e 4 in frammenti isolati" in service._predicted_notes([t])[0]


def test_experimental_templates_are_never_trimmed(tmp_path, ubq_path):
    info = modeller.prepare_template(ubq_path, modeller.TemplateSpec("1UBQ", ["A"]), tmp_path)
    assert "plddt" not in info and info["sequence"] == HUMAN_UB
    text = (tmp_path / "1UBQ_A.pdb").read_text()
    # nessun record di AlphaFold; il metodo sperimentale resta dichiarato nella copia
    assert not text.startswith("HEADER") and text.startswith("EXPDTA    X-RAY DIFFRACTION")


def test_model_options_validate_the_plddt_threshold():
    opts = modeller.parse_options({})
    assert opts.plddt_trim is True and opts.plddt_min == 70.0
    opts = modeller.parse_options({"plddt_trim": False, "plddt_min": 50})
    assert opts.plddt_trim is False and opts.plddt_min == 50.0
    assert modeller.parse_options({"plddt_min": None}).plddt_min == 70.0
    for bad in (-1, 101, "alto", float("nan")):
        with pytest.raises(AppError, match="soglia pLDDT"):
            modeller.parse_options({"plddt_min": bad})


class Ctx:
    """Contesto minimo per la preparazione dei template (log e annullamento)."""

    def __init__(self) -> None:
        self.logs: list[str] = []
        self.settings = UserSettings()

    def log(self, message: str) -> None:
        self.logs.append(message)

    def check(self) -> None:
        pass


@pytest.fixture
def service(tmp_path, afdb):
    library = TemplateLibrary(tmp_path / "templates")
    jobs = SimpleNamespace(register=lambda *a, **k: None)
    return ComputeService(jobs, library, tmp_path / "uploads", tmp_path / "databases")


def test_prepare_templates_fetches_alphafold_and_trims_only_predicted(service, tmp_path, ubq_path):
    service.library.save_upload("1UBQ.pdb", ubq_path.read_bytes())
    ctx = Ctx()
    specs = [modeller.TemplateSpec("AF-P0CG48-F1", ["A"]), modeller.TemplateSpec("1UBQ", ["A"])]
    af, exp = service._prepare_templates(ctx, specs, tmp_path / "work", plddt_min=70)
    assert service.library.exists("AF-P0CG48-F1")  # scaricato da AlphaFold DB prima della modellazione
    assert af["predicted"] is True and af["plddt"]["removed"] == 11
    # 1UBQ ha B-factor tutti sotto 70: se fosse ritagliato non resterebbe nulla
    assert "plddt" not in exp and not exp.get("predicted") and exp["sequence"] == HUMAN_UB
    log = "\n".join(ctx.logs)
    assert "download da AlphaFold DB (EBI)" in log
    assert "rimossi 11 residui con pLDDT < 70 (1–5:A, 71–76:A), restano 65 di 76" in log
    notes = service._predicted_notes([af, exp])
    assert len(notes) == 1 and "modello predetto (AlphaFold DB), non una struttura sperimentale" in notes[0]
    assert all("_residues" not in t for t in service._public([af, exp]))
    # ritaglio disattivato: il modello si usa intero, e il log lo dice
    ctx = Ctx()
    [whole] = service._prepare_templates(ctx, [modeller.TemplateSpec("AF-P0CG48-F1", ["A"])], tmp_path / "w2")
    assert "plddt" not in whole and whole["sequence"] == HUMAN_UB and "senza ritaglio pLDDT" in ctx.logs[-1]


def _pir(template_row: str, first: str = "1", last: str = "76", target: str = YEAST_UB) -> str:
    return (f">P1;AF-P0CG48-F1_A\nstructureX:AF-P0CG48-F1_A:{first}:A:{last}:A::::\n{template_row}*\n\n"
            f">P1;yeast\nsequence:yeast:::::::0.00:0.00\n{target}*\n")


def test_user_pir_is_brought_onto_the_trimmed_template(service, tmp_path):
    [t] = service._prepare_templates(Ctx(), [modeller.TemplateSpec("AF-P0CG48-F1", ["A"])], tmp_path / "w",
                                     plddt_min=70)
    # PIR scritto sul template completo: i residui tolti diventano gap e l'intervallo passa a 6–70
    pir, target, _warnings = ComputeService._normalize_pir(_pir(HUMAN_UB), [t])
    entries = sequences.parse_pir(pir)
    tmpl = next(e for e in entries if e.is_template)
    assert tmpl.fields[:4] == ["6", "A", "70", "A"] and target == "yeast"
    assert tmpl.sequence == "-" * 5 + HUMAN_UB[5:70] + "-" * 6
    assert next(e for e in entries if e.kind == "sequence").sequence == YEAST_UB  # bersaglio intatto
    assert sequences.validate_pir(pir)["valid"]
    # PIR già sul template ritagliato (es. da un allineamento precedente): resta com'è
    trimmed = _pir("-" * 5 + HUMAN_UB[5:70] + "-" * 6, "6", "70")
    again = next(e for e in sequences.parse_pir(ComputeService._normalize_pir(trimmed, [t])[0]) if e.is_template)
    assert again.sequence == tmpl.sequence and again.fields[:4] == ["6", "A", "70", "A"]
    # riga del template che non è né il modello completo né quello ritagliato (un residuo in più): errore chiaro
    row = HUMAN_UB[:2] + "Q" + HUMAN_UB[2:]
    with pytest.raises(AppError, match="non corrisponde al file del template"):
        ComputeService._normalize_pir(_pir(row, target=YEAST_UB[:2] + "-" + YEAST_UB[2:]), [t])
    # intervallo FIRST/LAST: nessun numero da aggiornare, sequenza con i gap
    first_last = ComputeService._normalize_pir(_pir(HUMAN_UB, "FIRST", "LAST"), [t])[0]
    fl = next(e for e in sequences.parse_pir(first_last) if e.is_template)
    assert fl.fields[:4] == ["FIRST", "A", "LAST", "A"] and fl.sequence.startswith("-----")
    with pytest.raises(AppError, match="non esiste nel template"):
        ComputeService._normalize_pir(_pir(HUMAN_UB, "200", "276"), [t])


def test_all_gap_columns_are_removed_after_trimming(service, tmp_path):
    [t] = service._prepare_templates(Ctx(), [modeller.TemplateSpec("AF-P0CG48-F1", ["A"])], tmp_path / "w",
                                     plddt_min=70)
    # bersaglio più corto (senza le code): le colonne delle code diventano di soli gap e spariscono
    target = "-" * 5 + YEAST_UB[5:70] + "-" * 6
    pir = ComputeService._normalize_pir(_pir(HUMAN_UB, target=target), [t])[0]
    entries = sequences.parse_pir(pir)
    assert all(len(e.sequence) == 65 for e in entries)
    assert next(e for e in entries if e.is_template).sequence == HUMAN_UB[5:70]


# ------------------------------------------------------------------------------------------ pipeline e MODELLER
def test_pipeline_alphafold_engine_without_modelling(client, afdb, fake_swissprot):
    """Ricerca AlphaFold nella pipeline (filtro zona crepuscolare: niente MODELLER): accessione dall'intestazione
    FASTA di UniProt (anche TrEMBL: l'interfaccia la scrive così per le sequenze del progetto importate da UniProt),
    altrimenti BLAST su Swiss-Prot."""
    body = {"fasta": f">sp|P0CG48|UBC_HUMAN\n{YEAST_UB}\n>ignota\n{YEAST_UB}\n>tr|P0CG48|UBC_HUMAN\n{YEAST_UB}\n",
            "engine": "alphafold", "twilight": {"filter": True, "identity": 99.0, "coverage": 40.0}, "parallel": 1}
    job = client.post("/api/pipeline", json=body).json()
    done = wait_job(client, job["id"])
    assert done["status"] == "completed", done
    items = client.get(f"/api/pipeline/{job['id']}/items").json()["items"]
    assert [i["name"] for i in items] == ["sp_P0CG48_UBC_HUMAN", "ignota", "tr_P0CG48_UBC_HUMAN"]
    assert [i["status"] for i in items] == ["filtered", "filtered", "filtered"]
    assert all(i["template"] == "AF-P0CG48-F1" and i["template_source"] == "alphafold" for i in items)
    assert items[0]["identity"] == 96.1
    assert len(fake_swissprot) == 1  # solo la sequenza senza accessione passa da NCBI


@pytest.mark.modeller
@requires_modeller
def test_end_to_end_modeling_with_a_trimmed_alphafold_template(client, afdb):
    job = client.post("/api/model", json={"target": {"name": "yeast_ub", "sequence": YEAST_UB},
                                          "templates": [{"id": "AF-P0CG48-F1", "chains": ["A"]}],
                                          "options": {"models": 1, "refinement": "very_fast"}}).json()
    done = wait_job(client, job["id"], timeout=600)
    assert done["status"] == "completed", done.get("error")
    res = done["result"]
    [t] = res["templates"]
    assert t["predicted"] is True and t["plddt"]["removed"] == 11 and "_residues" not in t
    assert res["plddt_removed"] == 11 and res["options"]["plddt_min"] == 70.0
    assert any("modello predetto (AlphaFold DB)" in w for w in res["warnings"])
    best = next(m for m in res["models"] if m["best"])
    pdb = client.get(f"/api/jobs/{job['id']}/files/{best['file']}").text
    assert pdb.count(" CA ") == 76  # anche le code senza template sono nel modello
    assert "REMARK 999 BIOLOOM TEMPLATE AF-P0CG48-F1_A ALPHAFOLD PLDDT<70 REMOVED 11" in pdb
    # l'allineamento automatico usa la sequenza ritagliata del template
    tmpl = next(e for e in sequences.parse_pir(res["alignment"]) if e.is_template)
    assert re.sub(r"[^A-Z]", "", tmpl.sequence) == HUMAN_UB[5:70]
    assert "rimossi 11 residui con pLDDT < 70" in _log(client, job["id"])

    # stesso template con un PIR scritto sul modello completo: coerente con il ritaglio
    job = client.post("/api/model", json={"alignment": _pir(HUMAN_UB),
                                          "templates": [{"id": "AF-P0CG48-F1", "chains": ["A"]}],
                                          "options": {"models": 1, "refinement": "very_fast", "plddt_min": 60}}).json()
    done = wait_job(client, job["id"], timeout=600)
    assert done["status"] == "completed", done.get("error")
    assert done["result"]["templates"][0]["plddt"]["threshold"] == 60.0
    assert done["result"]["templates"][0]["plddt"]["removed"] == 11


@pytest.mark.modeller
@requires_modeller
def test_end_to_end_pipeline_with_the_alphafold_engine(client, afdb, no_ncbi):
    body = {"fasta": f">sp|P0CG48|UBC_HUMAN\n{YEAST_UB}\n", "engine": "alphafold", "parallel": 1, "quality": False,
            "options": {"models": 1, "refinement": "very_fast", "plddt_min": 70}}
    job = client.post("/api/pipeline", json=body).json()
    done = wait_job(client, job["id"], timeout=600)
    assert done["status"] == "completed", done
    [item] = client.get(f"/api/pipeline/{job['id']}/items").json()["items"]
    assert item["status"] == "completed", item
    assert item["template"] == "AF-P0CG48-F1" and item["template_source"] == "alphafold"
    assert item["metrics"]["template_predicted"] is True and item["metrics"]["plddt_removed"] == 11
    assert client.app.state.core.library.exists("AF-P0CG48-F1")  # scaricato nella libreria prima di modellare
    assert len(afdb.file_calls()) == 1


def test_json_of_entries_is_serializable(afdb):
    entries = remote.alphafold_entries("P0CG48")
    assert json.loads(json.dumps(entries)) == copy.deepcopy(entries)
