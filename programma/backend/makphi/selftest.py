"""Verifica dell'installazione da riga di comando: `python -m makphi.selftest`.

Usa la configurazione e le licenze della cartella dati, ma un database e uno scratch temporanei propri:
si può eseguire anche mentre il server è attivo (`docker compose exec makphi python -m makphi.selftest`).
"""

from __future__ import annotations

import shutil
import sys
import tempfile
import time
from pathlib import Path

from .config import SettingsStore, default_paths
from .core import auth
from .core.db import Database
from .core.jobs import JobManager
from .engines import remote, search, tools
from .services.licenses import LicenseService
from .services.selftest import SelfTestService

SYMBOL = {"ok": "OK ", "missing": "-- ", "error": "ERR"}


def main() -> int:
    paths = default_paths()
    # nel container del server (MAKPHI_AUTH=on) le impostazioni dell'amministratore, come il server: per esempio
    # l'interprete di MODELLER di MAKPHI_MODELLER_PYTHON, non quello salvato in settings.json
    settings = SettingsStore(paths.settings_file, server=auth.server_mode())
    tools.configure(paths.tools)
    remote.configure(paths.cache)
    search.configure(paths.local_cache)
    LicenseService(paths.licenses, settings.get).apply()
    tmp = Path(tempfile.mkdtemp(prefix="makphi_selftest_"))
    db = Database(tmp / "selftest.db")
    manager = JobManager(db, tmp / "jobs", 1, settings.get, scratch_dir=tmp / "scratch")
    SelfTestService(manager, paths.databases)
    try:
        job = manager.submit("selftest", {})
        while manager.get(job["id"])["status"] not in ("completed", "failed", "cancelled"):
            time.sleep(0.3)
        done = manager.get(job["id"])
        if done["status"] != "completed":
            print(f"Verifica non completata: {done['error']}")
            return 2
        print("\nVerifica dell'installazione di MakPhi\n")
        for row in done["result"]["checks"]:
            print(f"  [{SYMBOL[row['status']]}] {row['tool']:14s} {row['detail']}  ({row['seconds']} s)")
        counts = done["result"]["counts"]
        print(f"\n  {counts['ok']} funzionanti, {counts['missing']} non disponibili, {counts['error']} con errori\n")
        return 1 if counts["error"] else 0
    finally:
        manager.shutdown()
        db.close()
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
