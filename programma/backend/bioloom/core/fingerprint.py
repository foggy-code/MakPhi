"""Impronta del codice di BioLoom in esecuzione (provenienza dei risultati).

La costruzione dell'immagine salva in /app/IMPRONTA l'impronta dei sorgenti (docker/impronta.sh: SHA-256 di backend/,
frontend/ e docker/). La versione da sola non basta: fra due rilasci il codice può cambiare senza cambiare numero
(com'è successo fra 4.2.0 e 4.3.0). L'impronta finisce nelle versioni del manifest di riproducibilità
(engines/versions.bioloom), nell'ambiente registrato all'avvio di ogni job e nelle voci della cache dei calcoli.

Non entra nella chiave della cache: ogni ricostruzione dell'immagine svuoterebbe di fatto la cache (profili HHblits e
QMEAN compresi). La chiave resta legata alla versione, che si aumenta a ogni rilascio (CHANGELOG.md).

Fuori dal container (sviluppo, test in Python puro) il file non c'è e l'impronta è None. Nei test nel container il
codice è montato da fuori: l'impronta è quella dell'immagine, non del codice montato.
"""

from __future__ import annotations

import functools
import os
import re
from typing import Optional

FINGERPRINT_FILE = "/app/IMPRONTA"
_HEX64 = re.compile(r"^[0-9a-f]{64}$")


@functools.lru_cache(maxsize=1)
def code_fingerprint() -> Optional[str]:
    """SHA-256 dei sorgenti dell'immagine (64 cifre esadecimali), None se il file manca o non è valido.

    Il percorso si può cambiare con BIOLOOM_FINGERPRINT_FILE (test). Letto una volta per processo: l'immagine non
    cambia mentre il server gira."""
    path = os.environ.get("BIOLOOM_FINGERPRINT_FILE") or FINGERPRINT_FILE
    try:
        with open(path, encoding="ascii", errors="replace") as fh:
            value = fh.read(200).strip().lower()
    except OSError:
        return None
    return value if _HEX64.match(value) else None
