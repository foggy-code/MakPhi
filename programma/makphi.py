"""Avvio di MakPhi senza Docker (sviluppo), dalla cartella di MakPhi:
`python programma/makphi.py [--port 8765] [--no-browser]`. La cartella dati è `data/` accanto a `programma/`.

Riesecuzione di un pacchetto di riproducibilità: `python programma/makphi.py rerun <pacchetto.zip> [--data <cartella>]`."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "backend"))

from makphi.__main__ import main  # noqa: E402

if __name__ == "__main__":
    main()
