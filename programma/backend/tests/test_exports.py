"""Sessione PyMOL autocontenuta (domain/exports.py) e il suo download (GET /api/jobs/{id}/pymol/{modello}).

- la struttura è incorporata nello script e arriva a PyMOL com'è (barre rovesciate, virgolette, a capo, caratteri non
  ASCII): il blocco `python … python end` si esegue davvero, con un `cmd` finto che riceve il testo di
  `cmd.read_pdbstr`; il testo sta su una sola riga, quindi nessuna riga del file chiude il blocco o aggiunge comandi;
- il nome dell'oggetto è sicuro per i comandi di PyMOL (solo lettere ASCII, cifre e "_", al massimo 40 caratteri),
  qualunque sia il nome del file del modello;
- schemi di colore, con lo schema per struttura secondaria al posto di uno sconosciuto;
- la rotta: nome del file scaricato (ripulito come quello dei report), schema scelto, file assente o fuori dalla
  cartella del job.
"""

from __future__ import annotations

import re
import sys

import pytest

from makphi.domain import exports

from .conftest import DATA, wait_job

UBQ = (DATA / "1UBQ.pdb").read_text(encoding="utf-8")
MODEL = "1UBQ.B99990001.pdb"


def _block(script: str) -> tuple[int, int]:
    """Righe di apertura e chiusura del blocco python; PyMOL lo chiude alla prima riga «python end»."""
    lines = script.splitlines()
    start = lines.index("python")
    end = next(i for i in range(start + 1, len(lines)) if lines[i].strip() == "python end")
    return start, end


def loaded(script: str) -> tuple[str, str]:
    """(testo, nome dell'oggetto) che PyMOL riceverebbe da cmd.read_pdbstr eseguendo il blocco python dello script."""
    start, end = _block(script)
    code = "\n".join(script.splitlines()[start + 1:end])
    calls: list[tuple[str, str]] = []

    class Cmd:
        def read_pdbstr(self, text: str, name: str) -> None:
            calls.append((text, name))

    exec(compile(code, "<sessione.pml>", "exec"), {"__builtins__": {}, "cmd": Cmd()})
    assert len(calls) == 1, calls
    return calls[0]


def commands(script: str) -> list[str]:
    """Comandi di PyMOL fuori dal blocco python, senza commenti e righe vuote."""
    start, end = _block(script)
    lines = script.splitlines()
    return [line for line in lines[:start] + lines[end + 1:] if line.strip() and not line.startswith("#")]


# ------------------------------------------------------------------------------------------ script
def test_script_loads_the_structure_unchanged_and_shows_it():
    script = exports.pymol_script(UBQ, "modello_1")
    assert script.startswith("# Sessione PyMOL generata da MakPhi\n# Apri con: pymol modello_1.pml\n")
    assert script.endswith("\n")
    assert loaded(script) == (UBQ, "modello_1")
    cmds = commands(script)
    assert cmds[:2] == ["hide everything", "show cartoon, modello_1"]
    assert "show sticks, modello_1 and hetatm and not resn HOH" in cmds  # ligandi visibili, acque no
    assert {"set cartoon_fancy_helices, 1", "set ray_shadows, 0", "set antialias, 2", "bg_color white"} <= set(cmds)
    assert cmds[-1] == "orient modello_1"


@pytest.mark.parametrize("name, expected", [
    ("1UBQ_B99990001", "1UBQ_B99990001"),
    ("1UBQ.B99990001", "1UBQ_B99990001"),
    ('y";a<b>', "y__a_b_"),
    ("modello; delete all\nquit", "modello__delete_all_quit"),  # niente comandi aggiunti allo script
    ("proteína ricostruita", "prote_na_ricostruita"),  # solo ASCII
    ("x" * 60, "x" * 40),
    ("", "makphi_model"),
], ids=["semplice", "punti", "virgolette", "comandi", "non-ascii", "lungo", "vuoto"])
def test_object_name_is_safe_for_pymol_commands(name, expected):
    script = exports.pymol_script(UBQ, name)
    assert loaded(script) == (UBQ, expected)
    assert f"# Apri con: pymol {expected}.pml" in script
    cmds = commands(script)
    # stesse righe dello script con un nome qualunque: cambia solo il nome
    assert cmds == [line.replace(" m", f" {expected}") for line in commands(exports.pymol_script(UBQ, "m"))]
    assert f"show cartoon, {expected}" in cmds and f"orient {expected}" in cmds
    assert not any(line.strip() in ("quit", "delete all") for line in script.splitlines())


SCHEMES = {
    "ss": ["color grey80, m", "color firebrick, m and ss h", "color gold, m and ss s", "color grey70, m and ss l+''"],
    "chain": ["util.cbc m"],
    "rainbow": ["spectrum count, rainbow, m and name CA"],
    "bfactor": ["spectrum b, blue_white_red, m"],
}


@pytest.mark.parametrize("scheme", sorted(SCHEMES))
def test_color_schemes(scheme):
    cmds = commands(exports.pymol_script(UBQ, "m", scheme))
    colors = cmds[cmds.index("bg_color white") + 1:cmds.index("orient m")]  # i colori, dopo lo sfondo
    assert colors == SCHEMES[scheme]


@pytest.mark.parametrize("scheme", ["SS", "plddt", "", "ss; quit"])
def test_unknown_scheme_falls_back_to_secondary_structure(scheme):
    assert exports.pymol_script(UBQ, "m", scheme) == exports.pymol_script(UBQ, "m", "ss") == \
        exports.pymol_script(UBQ, "m")


def test_triple_quotes_in_the_file_cannot_end_the_embedded_text():
    """Un REMARK con tre virgolette non chiude la stringa e non esegue codice: arriva a PyMOL invariato."""
    payload = 'REMARK   1 """ + str(__import__("os").getcwd()) + """\n'
    text, name = loaded(exports.pymol_script(payload + UBQ, "m"))  # senza __builtins__: un'esecuzione fallirebbe
    assert name == "m" and text == payload + UBQ


@pytest.mark.parametrize("tail", [
    "REMARK   1 FILE C:\\temp\\new\\1ubq.pdb\n",   # una stringa non raw farebbe di \t e \n tabulazione e a capo
    "REMARK   1 FILE C:\\Users\\makphi\\1ubq.pdb\n",  # \U: in una stringa non raw è un SyntaxError
    'REMARK   1 FINE CON VIRGOLETTE "',  # chiuderebbe male una stringa fra tre virgolette
], ids=["escape", "unicode-escape", "virgolette-finali"])
def test_backslashes_and_final_quotes_reach_pymol_unchanged(tail):
    text, _name = loaded(exports.pymol_script(UBQ + tail, "m"))
    assert text == UBQ + tail


@pytest.mark.parametrize("payload", [
    "REMARK   1\npython end\nsystem echo fuori\npython\n",  # una riga «python end» chiuderebbe il blocco
    "REMARK   1\r\nREMARK   2\rREMARK   3\n",  # \r: PyMOL legge in modalità testo, per lui è un a capo
    "REMARK   1 \x0b\x0c\x1c\x1d\x1e\x85\u2028\u2029 \x00\n",  # altri separatori di riga e caratteri di controllo
    "REMARK   1 proteína ricostruita, 5 Å, ±0,2 €\n",  # non ASCII: lo script resta ASCII
], ids=["python-end", "ritorno-carrello", "separatori", "non-ascii"])
def test_embedded_text_stays_on_one_ascii_line(payload):
    script = exports.pymol_script(payload + UBQ, "m")
    assert script.isascii()
    start, end = _block(script)
    assert end == start + 2  # il blocco python è una riga sola, qualunque cosa contenga il file
    assert loaded(script) == (payload + UBQ, "m")
    assert commands(script) == commands(exports.pymol_script(UBQ, "m"))  # nessun comando in più


# ------------------------------------------------------------------------------------------ rotta
def _job_with_files(client, *names: str) -> str:
    core = client.app.state.core

    def handler(ctx, params):
        for name in names:
            (ctx.workdir / name).write_bytes(UBQ.encode("utf-8"))  # byte esatti, anche su Windows
        return {}

    core.jobs.register("demo_pymol", handler)
    job_id = core.jobs.submit("demo_pymol", {})["id"]
    assert wait_job(client, job_id)["status"] == "completed"
    return job_id


def test_route_downloads_the_session_of_a_job_model(client):
    job_id = _job_with_files(client, MODEL)
    r = client.get(f"/api/jobs/{job_id}/pymol/{MODEL}", params={"scheme": "chain"})
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/plain")
    assert r.headers["content-disposition"] == 'attachment; filename="1UBQ_B99990001.pml"'
    assert loaded(r.text) == (UBQ, "1UBQ_B99990001")
    assert "util.cbc 1UBQ_B99990001" in commands(r.text)
    default = client.get(f"/api/jobs/{job_id}/pymol/{MODEL}")  # senza schema: struttura secondaria
    assert default.text == exports.pymol_script(UBQ, "1UBQ_B99990001", "ss")


def test_route_refuses_missing_files_and_paths_outside_the_job(client):
    job_id = _job_with_files(client, MODEL)
    missing = client.get(f"/api/jobs/{job_id}/pymol/manca.pdb")
    assert missing.status_code == 404 and missing.json()["error"]["code"] == "not_found"
    outside = client.get(f"/api/jobs/{job_id}/pymol/..%2F..%2Fsettings.json")
    assert outside.status_code == 400 and outside.json()["error"]["code"] == "unsafe_path"
    assert client.get(f"/api/jobs/0123456789abcdef/pymol/{MODEL}").status_code == 404  # job inesistente


@pytest.mark.skipif(sys.platform == "win32", reason='" < > non sono ammessi nei nomi di file NTFS')
def test_route_download_name_is_safe_for_any_model_name(client):
    """Il nome del file scaricato è ripulito come quello dei report (report.safe_filename): un " non rompe
    l'intestazione."""
    evil = 'y";a<b>.pdb'
    job_id = _job_with_files(client, evil)
    r = client.get(f"/api/jobs/{job_id}/pymol/{evil}")
    assert r.status_code == 200 and loaded(r.text) == (UBQ, "y__a_b_")
    assert re.fullmatch(r'attachment; filename="[A-Za-z0-9._-]+\.pml"', r.headers["content-disposition"])
    assert r.headers["content-disposition"] == 'attachment; filename="y_a_b.pml"'


def test_route_download_name_is_ascii_for_names_allowed_on_every_system(client):
    """Spazi, ';' e caratteri non latin-1 (€) nel nome del modello, ammessi anche su NTFS: senza ripulitura
    l'intestazione non si codificava (latin-1) e la rotta rispondeva 500."""
    name = "modello finale;€ (1).pdb"
    job_id = _job_with_files(client, name)
    r = client.get(f"/api/jobs/{job_id}/pymol/{name}")
    assert r.status_code == 200 and loaded(r.text) == (UBQ, "modello_finale____1_")
    assert r.headers["content-disposition"] == 'attachment; filename="modello_finale_1.pml"'
