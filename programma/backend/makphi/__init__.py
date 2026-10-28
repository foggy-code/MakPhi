"""MakPhi - suite per la modellazione di proteine per omologia."""

import os as _os

__version__ = "5.0.1"

# Fino alla 4.4.0 il programma si chiamava BioLoom: le variabili BIOLOOM_* (server.env, .env.locale, container già
# configurati) valgono come le MAKPHI_*, che hanno la precedenza. Si spostano, non si copiano: un segreto con il nome
# vecchio (BIOLOOM_ACCESS_KEY, BIOLOOM_SMTP_*) non arriva così ai programmi esterni (core/sensitive.py).
for _old in [k for k in _os.environ if k.upper().startswith("BIOLOOM_")]:
    _value = _os.environ.pop(_old)
    _os.environ.setdefault("MAKPHI_" + _old[len("BIOLOOM_"):], _value)
