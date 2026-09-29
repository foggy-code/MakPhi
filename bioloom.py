"""Avvio di BioLoom v4 dalla cartella di installazione: `python bioloom.py [--port 8765] [--no-browser]`."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "backend"))

from bioloom.__main__ import main  # noqa: E402

if __name__ == "__main__":
    main()
