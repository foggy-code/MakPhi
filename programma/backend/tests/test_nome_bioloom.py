"""Compatibilità con BioLoom, il nome del programma fino alla 4.4.0: variabili BIOLOOM_*, database bioloom.db, cookie
di sessione e pacchetti di riproducibilità creati prima del cambio di nome."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from makphi.app import create_app
from makphi.config import Paths
from makphi.core import auth
from makphi.core.errors import AppError
from makphi.domain import reproducibility as repro

BACKEND = Path(__file__).resolve().parents[1]


def _environ_after_import(env: dict[str, str]) -> dict[str, str]:
    """Variabili MAKPHI_/BIOLOOM_ viste da un processo nuovo dopo `import makphi` (lo spostamento avviene all'import)."""
    clean = {k: v for k, v in os.environ.items() if not k.upper().startswith(("MAKPHI_", "BIOLOOM_"))}
    code = ("import os, json, makphi; print(json.dumps({k: v for k, v in os.environ.items() "
            "if k.upper().startswith(('MAKPHI_', 'BIOLOOM_'))}))")
    out = subprocess.run([sys.executable, "-c", code], cwd=BACKEND, env={**clean, **env}, capture_output=True,
                         text=True, check=True)
    return json.loads(out.stdout)


def test_bioloom_variables_become_makphi_and_new_names_win():
    seen = _environ_after_import({"BIOLOOM_PORT": "9000", "BIOLOOM_AUTH": "on", "MAKPHI_AUTH": "off",
                                  "BIOLOOM_ACCESS_KEY": "chiave-vecchia-abbastanza"})
    assert seen["MAKPHI_PORT"] == "9000"
    assert seen["MAKPHI_AUTH"] == "off"  # il nome nuovo prevale
    assert seen["MAKPHI_ACCESS_KEY"] == "chiave-vecchia-abbastanza"
    # spostate, non copiate: un segreto con il nome vecchio non arriva ai programmi esterni
    assert not any(k.upper().startswith("BIOLOOM_") for k in seen)


def test_bioloom_db_is_adopted_with_its_side_files(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    (data / "bioloom.db").write_bytes(b"db")
    (data / "bioloom.db-wal").write_bytes(b"wal")
    Paths(data).ensure()
    assert (data / "makphi.db").read_bytes() == b"db"
    assert (data / "makphi.db-wal").read_bytes() == b"wal"
    assert not (data / "bioloom.db").exists() and not (data / "bioloom.db-wal").exists()


def test_existing_makphi_db_is_never_replaced(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    (data / "makphi.db").write_bytes(b"nuovo")
    (data / "bioloom.db").write_bytes(b"vecchio")
    Paths(data).ensure()
    assert (data / "makphi.db").read_bytes() == b"nuovo"
    assert (data / "bioloom.db").read_bytes() == b"vecchio"


def _bioloom_manifest() -> dict:
    """Manifest come lo scriveva BioLoom 4.4.0: formato bioloom-reproducibility, versioni nella sezione "bioloom"."""
    return repro.seal({"format": "bioloom-reproducibility", "format_version": 1, "created_at": "x",
                       "bioloom": {"version": "4.4.0", "image": "4.4.0", "fingerprint": "f" * 64},
                       "environment": {}, "job": {"id": "abc", "kind": "quality"}, "params": {"dope": True},
                       "files": [{"path": "inputs/params.json", "role": "params", "sha256": "0" * 64, "size": 2}]})


def test_bioloom_packages_are_still_valid_and_read_as_makphi():
    manifest = _bioloom_manifest()
    assert repro.validate_manifest(manifest) is manifest
    assert manifest["makphi"]["version"] == "4.4.0"
    # la sezione aggiunta non altera l'impronta: il manifest si può validare di nuovo
    assert repro.validate_manifest(manifest) is manifest
    with pytest.raises(AppError) as err:
        repro.validate_manifest({**manifest, "params": {"dope": False}})
    assert err.value.code == "package_manifest_altered"


def test_signing_key_id_is_the_one_of_bioloom():
    key = b"chiave-di-firma-del-laboratorio"
    assert repro.signing_key_id(key) == hashlib.sha256(b"bioloom-manifest-key\0" + key).hexdigest()[:16]


def test_bioloom_session_cookie_is_still_accepted(tmp_path):
    cfg = auth.AuthConfig(enabled=True, key="segreto-12345")
    app = create_app(Paths(tmp_path / "data"), frontend_dist=None, auth_config=cfg)
    with TestClient(app) as c:
        assert c.get("/api/projects").status_code == 401
        c.cookies.set(auth.LEGACY_COOKIE, cfg.issue())  # emesso da BioLoom: stesso segreto, nome vecchio
        assert c.get("/api/projects").status_code == 200
        out = c.post("/api/auth/logout")
        cleared = " ".join(out.headers.get_list("set-cookie"))
        assert auth.COOKIE in cleared and auth.LEGACY_COOKIE in cleared
