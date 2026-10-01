"""Comandi da riga di comando oltre al server: `python bioloom.py rerun <pacchetto.zip> [--data <cartella>]`.

**rerun** (F9.4): riesegue il calcolo di un pacchetto di riproducibilità e confronta i risultati
(services/reproducibility.py), senza server. Crea un job `rerun` nella cartella dati indicata, ne mostra le fasi,
stampa il rapporto del confronto e termina con il codice:

    0  identico, equivalente (entro le tolleranze numeriche) o compatibile (entro le tolleranze scientifiche del tipo
       di calcolo, per esempio la rifinitura con OpenMM su più thread)
    1  diverso
    2  pacchetto non valido, calcolo non rieseguibile, job fallito o cartella dati in uso

La cartella dati è bloccata dal server che la usa (SQLite esclusivo): con il server attivo si usa un'altra cartella
(`--data`), oppure la pagina «Riesegui da pacchetto» o `POST /api/jobs/rerun`. Con `--data` lo scratch e la cache
locale stanno dentro quella cartella (`.scratch`, `.localcache`): il comando non tocca mai lo scratch di un server
(nel container `/scratch`). Se la cartella non ha la chiave di MODELLER si usa quella della cartella dati predefinita
(`BIOLOOM_DATA_DIR`) o della variabile `MODELLER_KEY`.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path
from typing import Optional

EXIT_SAME, EXIT_DIFFERENT, EXIT_ERROR = 0, 1, 2
POLL_SECONDS = 0.5
# stato finale di un job non completato, nei messaggi (come STATUS_LABEL dell'interfaccia)
STATUS_TEXT = {"failed": "non riuscita", "cancelled": "annullata"}


def failure_text(job: dict) -> str:
    """Messaggio di una riesecuzione terminata senza risultato: «Riesecuzione non riuscita: <errore>»."""
    error = str(job.get("error") or "").strip()
    return f"Riesecuzione {STATUS_TEXT.get(job.get('status'), job.get('status'))}" + (f": {error}" if error else ".")


def rerun_main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="bioloom rerun",
                                     description="Riesegue il calcolo di un pacchetto di riproducibilità di BioLoom e "
                                                 "confronta i risultati con quelli del pacchetto.")
    parser.add_argument("package", help="Pacchetto ZIP esportato da BioLoom (Job → Pacchetto riproducibile)")
    parser.add_argument("--data", help="Cartella dati in cui creare il job (predefinita: quella di BioLoom; deve "
                                       "essere libera, cioè senza un server attivo)")
    args = parser.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    from .config import Paths, default_paths
    from .core.container import build_core
    from .core.db import DatabaseInUse
    from .core.errors import AppError
    from .core.jobs import TERMINAL
    from .services.licenses import LicenseService
    from .services.reproducibility import REPORT_TEXT

    package = Path(args.package).expanduser()
    if not package.is_file():
        print(f"Pacchetto non trovato: {package}", file=sys.stderr)
        return EXIT_ERROR
    paths = Paths(data=Path(args.data).expanduser().resolve()) if args.data else default_paths()
    try:
        core = build_core(paths)
    except DatabaseInUse as exc:
        print(f"{exc}\nCon il server attivo usa la pagina «Riesegui da pacchetto», POST /api/jobs/rerun oppure "
              "--data con un'altra cartella.", file=sys.stderr)
        return EXIT_ERROR
    try:
        if not core.licenses.modeller_key()[0] and args.data:
            fallback = LicenseService(default_paths().licenses, core.settings.get)
            if fallback.modeller_key()[0]:
                fallback.apply()  # solo per i processi di questo comando: nessuna copia della chiave
                print("Licenza MODELLER: quella della cartella dati predefinita.")
        try:
            job = core.repro.submit(package)
        except AppError as exc:
            print(f"Riesecuzione non avviata: {exc.message}", file=sys.stderr)
            return EXIT_ERROR
        print(f"Job di riesecuzione {job['id']} nella cartella dati {paths.data}")
        stage = None
        while True:
            current = core.jobs.get(job["id"], include_result=False)
            if current["stage"] != stage:
                stage = current["stage"]
                print(f"  [{int(round(100 * float(current['progress'] or 0))):3d}%] {stage}", flush=True)
            if current["status"] in TERMINAL and not core.jobs.busy(job["id"]):
                break
            time.sleep(POLL_SECONDS)
        done = core.jobs.get(job["id"])
        if done["status"] != "completed":
            print(failure_text(done), file=sys.stderr)
            return EXIT_ERROR
        report = core.jobs.workdir(job["id"]) / REPORT_TEXT
        print()
        print(report.read_text(encoding="utf-8") if report.is_file() else done["result"].get("summary", ""))
        print(f"Rapporto e file: {core.jobs.workdir(job['id'])}")
        return EXIT_DIFFERENT if (done["result"] or {}).get("verdict") == "diverso" else EXIT_SAME
    finally:
        core.close()
