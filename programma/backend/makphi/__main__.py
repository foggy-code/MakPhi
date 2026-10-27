"""Avvio: `python -m makphi [--port 8765] [--host 127.0.0.1] [--no-browser]`.

Comandi: `python -m makphi rerun <pacchetto.zip> [--data <cartella>]` (riesecuzione di un pacchetto di
riproducibilità, makphi/cli.py)."""

from __future__ import annotations

import argparse
import os
import sys
import threading
import time
import webbrowser


def main() -> None:
    if len(sys.argv) > 1 and sys.argv[1] == "rerun":  # comando, non avvio del server
        from .cli import rerun_main

        sys.exit(rerun_main(sys.argv[2:]))
    parser = argparse.ArgumentParser(prog="makphi", description="MakPhi - modellazione per omologia",
                                     epilog="Comandi: rerun <pacchetto.zip> [--data <cartella>] riesegue un pacchetto "
                                            "di riproducibilità e confronta i risultati.")
    parser.add_argument("--host", default=os.environ.get("MAKPHI_HOST", "127.0.0.1"),
                        help="Indirizzo di ascolto (default 127.0.0.1: solo questo computer)")
    parser.add_argument("--port", type=int, default=int(os.environ.get("MAKPHI_PORT", "8765")))
    parser.add_argument("--data-dir", help="Cartella dati (default: <installazione>/data)")
    parser.add_argument("--no-browser", action="store_true", help="Non aprire il browser all'avvio")
    args = parser.parse_args()

    if args.data_dir:
        os.environ["MAKPHI_DATA_DIR"] = os.path.abspath(args.data_dir)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    import uvicorn

    from . import __version__
    from .app import create_app

    app = create_app()
    config = uvicorn.Config(app, host=args.host, port=args.port, log_level="warning")
    server = uvicorn.Server(config)
    url = f"http://{'127.0.0.1' if args.host in ('0.0.0.0', '::') else args.host}:{args.port}"

    def announce() -> None:
        while not server.started:
            time.sleep(0.1)
        app.state.core.server = server
        print(f"\n  MakPhi v{__version__} in esecuzione su {url}\n  Dati: {app.state.core.paths.data}\n"
              f"  Premi Ctrl+C per arrestare.\n", flush=True)
        if not args.no_browser:
            webbrowser.open(url)

    threading.Thread(target=announce, daemon=True).start()
    server.run()
    if not server.started:  # avvio non riuscito (es. chiave d'accesso troppo corta): esce con errore come uvicorn.run
        sys.exit(3)


if __name__ == "__main__":
    main()
