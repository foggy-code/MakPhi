"""Versione unica e impronta del codice.

La versione viene solo da programma/launcher/VERSION: le copie letterali (makphi.__version__, pyproject.toml,
package.json, i due campi del progetto in package-lock.json, ARG del Dockerfile e di docker/test/Dockerfile, i due
predefiniti di compose.yml) le allinea programma/scripts/versione.sh e qui si controlla che coincidano.
L'impronta del codice (docker/impronta.sh; la stessa definizione in launcher/impronta.ps1) si confronta con la
definizione riscritta in Python, su una piccola cartella programma/ con tutti i casi da includere ed escludere.

Il test gira da programma/backend e nel container di scripts/test.sh, dove programma/ è montata in /src; senza
launcher/VERSION (backend copiato da solo) si salta.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

import makphi

PROGRAMMA = Path(__file__).resolve().parents[2]   # tests/ → backend/ → programma/
VERSION_FILE = PROGRAMMA / "launcher" / "VERSION"
IMPRONTA_SH = PROGRAMMA / "docker" / "impronta.sh"
IMPRONTA_PS1 = PROGRAMMA / "launcher" / "impronta.ps1"
FORMATO = re.compile(r"(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)")


@pytest.fixture(scope="module")
def versione() -> str:
    if not VERSION_FILE.is_file():
        pytest.skip("programma/launcher/VERSION assente (backend senza il resto di programma/)")
    v = VERSION_FILE.read_text(encoding="utf-8").strip()
    assert FORMATO.fullmatch(v), f"VERSION non è X.Y.Z: {v!r}"
    return v


def _testo(rel: str) -> str:
    return (PROGRAMMA / rel).read_text(encoding="utf-8")


# ------------------------------------------------------------------------------------ copie della versione

def test_versione_del_pacchetto(versione):
    assert makphi.__version__ == versione


def test_versione_pyproject(versione):
    tomllib = pytest.importorskip("tomllib")   # Python 3.11+
    progetto = tomllib.loads(_testo("backend/pyproject.toml"))["project"]
    assert progetto["version"] == versione
    assert progetto["license"] == "MIT"


def test_versione_frontend(versione):
    pacchetto = json.loads(_testo("frontend/package.json"))
    assert pacchetto["version"] == versione
    assert pacchetto["license"] == "MIT"
    lock = json.loads(_testo("frontend/package-lock.json"))
    assert lock["version"] == versione
    assert lock["packages"][""]["version"] == versione
    assert lock["packages"][""]["license"] == "MIT"


def test_versione_dockerfile(versione):
    testo = _testo("docker/Dockerfile")
    assert re.findall(r"^ARG MAKPHI_VERSION=(\S*)$", testo, re.M) == [versione]
    # impronta e versione nell'immagine finale: l'avviatore le legge da lì
    assert re.search(r"^FROM .* AS impronta$", testo, re.M)
    assert re.search(r"^COPY --from=impronta /impronta /app/IMPRONTA$", testo, re.M)
    assert re.search(r"^LABEL makphi\.version=\$\{MAKPHI_VERSION\}$", testo, re.M)
    assert re.findall(r"^ARG BASE=makphi:(\S*)$", _testo("docker/test/Dockerfile"), re.M) == [versione]


def test_versione_compose(versione):
    testo = _testo("launcher/compose.yml")
    assert re.findall(r"\$\{MAKPHI_VERSION:-([^}]*)\}", testo) == [versione, versione]
    # MAKPHI_VERSION passata come build arg all'immagine makphi (ENV MAKPHI_IMAGE_VERSION, LABEL makphi.version)
    servizio = testo.split("\n  makphi:\n", 1)[1].split("\n  qmean:\n", 1)[0]
    righe = [r.strip() for r in servizio.splitlines() if r.strip() and not r.strip().startswith("#")]
    assert "args:" in righe
    assert re.match(r"(- )?MAKPHI_VERSION\b", righe[righe.index("args:") + 1])


def test_versione_citazione_e_zenodo(versione):
    # nella radice del repository: assenti nei test nel container, dove è montata solo programma/
    cff, zenodo = PROGRAMMA.parent / "CITATION.cff", PROGRAMMA.parent / ".zenodo.json"
    if not cff.is_file() or not zenodo.is_file():
        pytest.skip("CITATION.cff o .zenodo.json assenti (solo programma/ montata)")
    assert re.findall(r'^version:\s*"([^"]*)"', cff.read_text(encoding="utf-8"), re.M) == [versione]
    metadati = json.loads(zenodo.read_text(encoding="utf-8"))
    assert metadati["version"] == versione and metadati["license"] == "MIT"


def test_server_env_senza_versione(versione):
    # la versione è sempre quella di VERSION: un MAKPHI_VERSION nell'esempio la farebbe sembrare configurabile
    assert not re.search(r"^\s*MAKPHI_VERSION\s*=", _testo("launcher/server.env.example"), re.M)


# ------------------------------------------------------------------------------------ impronta del codice

ESCLUSI_PREFISSI = ("frontend/dist/", "docker/vendor/", "docker/qmean/", "docker/test/", "backend/tests/")


def _percorsi(radice: Path) -> list[str]:
    """File inclusi nell'impronta secondo la definizione, ordinati per byte."""
    percorsi = []
    for cartella in ("backend", "frontend", "docker"):
        for dirpath, _dirnames, filenames in os.walk(radice / cartella):
            for nome in filenames:
                p = Path(dirpath, nome)
                if p.is_symlink() or not p.is_file():
                    continue
                rel = p.relative_to(radice).as_posix()
                if any(s.startswith(".") or s in ("node_modules", "__pycache__") or s.endswith(".egg-info")
                       for s in rel.split("/")):
                    continue
                if rel.endswith(".pyc") or rel.startswith(ESCLUSI_PREFISSI):
                    continue
                percorsi.append(rel)
    return sorted(percorsi, key=lambda r: r.encode("utf-8"))


def _impronta_python(radice: Path) -> str:
    righe = []
    for rel in _percorsi(radice):
        contenuto = (radice / rel).read_bytes().replace(b"\r", b"")
        righe.append(f"{hashlib.sha256(contenuto).hexdigest()}  {rel}\n")
    return hashlib.sha256("".join(righe).encode("utf-8")).hexdigest()


INCLUSI = {
    "backend/makphi/__init__.py": '"""MakPhi."""\n\n__version__ = "0.0.0"\n',
    "backend/makphi/app.py": "def app():\n    return 1\n",
    "backend/requirements.lock": "fastapi==0.110\n",
    "backend/nome con spazi.txt": "spazi nel nome\n",
    "backend/testsuite/x.py": "x = 1\n",            # non è backend/tests/
    # ordine per byte: "Z" < "a", "-" < "." < "/"
    "backend/a-b.py": "1\n", "backend/a.py": "2\n", "backend/a/b.py": "3\n", "backend/Zeta.py": "4\n",
    "backend/vuoto.txt": "",
    "backend/cr_isolato.txt": b"uno\rdue",          # CR isolato: si toglie anche questo
    "frontend/package.json": "{}\n",
    "frontend/src/main.tsx": "console.log('ciao');\n",
    "frontend/distribuzione.txt": "non è frontend/dist/\n",
    "frontend/public/logo.png": b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\xff",
    "docker/Dockerfile": "FROM scratch\n",
    "docker/entrypoint.sh": '#!/bin/sh\nexec "$@"\n',
    "docker/tested/note.txt": "non è docker/test/\n",
    # fuori dal piano base: l'ordine per byte UTF-8 mette F0.. (emoji) dopo EE../EF.. (U+E000, U+FF01), mentre
    # l'ordinale UTF-16 di .NET li metterebbe prima (impronta.ps1 deve ordinare per byte come LC_ALL=C sort)
    "backend/a\U0001F600.txt": "emoji\n", "backend/a！.txt": "punto esclamativo largo\n",
    "backend/a.txt": "uso privato\n", "frontend/zé.js": "accento\n",
}
ESCLUSI = {
    "backend/makphi/__pycache__/app.cpython-312.pyc": b"\x00pyc",
    "backend/makphi/vecchio.pyc": b"\x00pyc",
    "backend/makphi.egg-info/PKG-INFO": "Name: makphi\n",
    "backend/tests/test_x.py": "def test_x():\n    pass\n",
    "backend/.ruff_cache/cache": "x\n",
    "backend/.env": "SEGRETO=1\n",
    "backend/makphi/.nascosta/dentro.py": "x = 1\n",
    "frontend/node_modules/react/index.js": "module.exports = 1;\n",
    "frontend/src/node_modules/x.js": "x\n",
    "frontend/dist/index.html": "<html></html>\n",
    "frontend/.vite/deps.json": "{}\n",
    "frontend/.DS_Store": b"\x00\x00\x00\x01Bud1",
    "docker/vendor/procheck/procheck.tar.gz": b"\x1f\x8b\x08\x00",
    "docker/qmean/Dockerfile": "FROM qmean\n",
    "docker/test/Dockerfile": "ARG BASE=makphi:0.0.0\n",
    "docker/.gitkeep": "",
    "scripts/test.sh": "#!/bin/sh\n",
    "launcher/VERSION": "0.0.0\n",
    "LEGGIMI.txt": "fuori da backend/, frontend/ e docker/\n",
}


def _crea_programma(radice: Path, fine_riga: str) -> Path:
    """Cartella programma/ di prova; i file di testo con il fine riga dato, quelli binari così come sono."""
    programma = radice / "programma"
    for rel, contenuto in {**INCLUSI, **ESCLUSI}.items():
        p = programma / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        dati = contenuto if isinstance(contenuto, bytes) else contenuto.replace("\n", fine_riga).encode("utf-8")
        p.write_bytes(dati)
    return programma


def _sh() -> str:
    sh = shutil.which("sh") or shutil.which("bash")
    if not sh or not any(shutil.which(t) for t in ("sha256sum", "shasum", "openssl")):
        pytest.skip("servono sh (o bash) e sha256sum, shasum oppure openssl")
    return sh


def _impronta_sh(sh: str, radice: Path, senza_perl: bool) -> str:
    # copia dello script accanto a programma/ (senza CR, come nella build): percorsi relativi anche con Git Bash
    (radice / "impronta.sh").write_bytes(IMPRONTA_SH.read_bytes().replace(b"\r", b""))
    env = {k: v for k, v in os.environ.items() if k != "IMPRONTA_SENZA_PERL"}
    if senza_perl:
        env["IMPRONTA_SENZA_PERL"] = "1"
    r = subprocess.run([sh, "impronta.sh", "programma"], cwd=radice, env=env, capture_output=True, timeout=300)
    if r.returncode == 3:
        pytest.skip("impronta.sh non trova sha256sum, shasum né openssl")
    assert r.returncode == 0, r.stderr.decode("utf-8", "replace")
    return r.stdout.decode("ascii").strip()


def test_impronta_definizione_python(tmp_path):
    programma = _crea_programma(tmp_path, "\n")
    assert _percorsi(programma) == sorted(INCLUSI, key=lambda r: r.encode("utf-8"))
    assert _percorsi(programma)[:8] == ["backend/Zeta.py", "backend/a-b.py", "backend/a.py", "backend/a/b.py",
                                        "backend/a.txt", "backend/a！.txt", "backend/a\U0001F600.txt",
                                        "backend/cr_isolato.txt"]


@pytest.mark.parametrize("senza_perl", [False, True], ids=["perl", "solo-sh"])
def test_impronta_sh_come_la_definizione(tmp_path, senza_perl):
    if not IMPRONTA_SH.is_file():
        pytest.skip("docker/impronta.sh assente")
    sh = _sh()
    attesa = _impronta_python(_crea_programma(tmp_path / "lf", "\n"))
    # copia di Windows (fine riga CRLF): stessa impronta, i CR non contano
    crlf = tmp_path / "crlf"
    assert _impronta_python(_crea_programma(crlf, "\r\n")) == attesa
    impronta = _impronta_sh(sh, crlf, senza_perl)
    assert re.fullmatch(r"[0-9a-f]{64}", impronta), impronta
    assert impronta == attesa


def test_impronta_powershell_come_la_definizione(tmp_path):
    """launcher/impronta.ps1 (avviatore di Windows) con Windows PowerShell o pwsh, se ci sono."""
    shell = shutil.which("powershell") or shutil.which("pwsh")
    if not shell or not IMPRONTA_PS1.is_file():
        pytest.skip("servono PowerShell e launcher/impronta.ps1")
    attesa = _impronta_python(_crea_programma(tmp_path / "lf", "\n"))
    programma = _crea_programma(tmp_path / "crlf", "\r\n")
    involucro = tmp_path / "impronta_prova.ps1"
    involucro.write_text("param($Script, $Root)\n. $Script\nGet-MakPhiImpronta $Root\n", encoding="utf-8")
    r = subprocess.run([shell, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(involucro),
                        "-Script", str(IMPRONTA_PS1), "-Root", str(programma)],
                       capture_output=True, timeout=300)
    assert r.returncode == 0, r.stderr.decode("utf-8", "replace")
    assert r.stdout.decode("ascii", "replace").strip() == attesa
