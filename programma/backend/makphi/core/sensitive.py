"""Dati sensibili (segreti): mascheratura per i messaggi e variabili d'ambiente da non passare ai programmi esterni.

- `mask`: forma mascherata di una chiave (prime tre e ultime due lettere), la stessa delle risposte sulle licenze
  (services/licenses.py). `redact` la applica a un testo per ogni segreto indicato, senza distinguere maiuscole e
  minuscole: MODELLER ripete la chiave rifiutata nei suoi messaggi d'errore, che arrivano all'interfaccia (stato del
  sistema, verifica dell'installazione) e al log dei job.
- `subprocess_env`: ambiente dei programmi esterni (MODELLER, PROCHECK, ProSA, HH-suite, BLAST+...) senza i segreti
  del server: chiave d'accesso (MAKPHI_ACCESS_KEY), configurazione della posta (MAKPHI_SMTP_*, con utente e
  password) e chiave MODELLER del server (MODELLER_KEY, KEY_MODELLER). Nessun programma esterno ne ha bisogno:
  MODELLER riceve la chiave attiva con MAKPHI_MODELLER_KEY (core/jobs.set_subprocess_env, engines/tools.py).
"""

from __future__ import annotations

import re
from typing import Callable, Iterable, Mapping, Optional

# Variabili d'ambiente del server da non passare mai ai programmi esterni
SECRET_ENV = frozenset({"MAKPHI_ACCESS_KEY", "MODELLER_KEY", "KEY_MODELLER"})
# ... e quelle con questi prefissi: tutta la configurazione della posta (server, porta, utente, password, mittente)
SECRET_ENV_PREFIXES = ("MAKPHI_SMTP_",)
# Segreti più corti non si mascherano nei testi: toglierebbero parole comuni (le chiavi MODELLER ne hanno almeno 4)
MIN_REDACT_LENGTH = 4


def mask(key: str) -> str:
    """Chiave mascherata: "MOD••••••EF" (oltre 6 caratteri), altrimenti tutta puntini."""
    if not key:
        return ""
    return key[:3] + "•" * max(3, len(key) - 5) + key[-2:] if len(key) > 6 else "•" * len(key)


def is_secret_env(name: str) -> bool:
    upper = name.upper()  # su Windows i nomi delle variabili non distinguono maiuscole e minuscole
    return upper in SECRET_ENV or upper.startswith(SECRET_ENV_PREFIXES)


def subprocess_env(environ: Mapping[str, str]) -> dict[str, str]:
    """Copia di `environ` senza le variabili segrete del server (`is_secret_env`)."""
    return {k: v for k, v in environ.items() if not is_secret_env(k)}


def _usable(secrets: Iterable[Optional[str]]) -> list[str]:
    out = {s.strip() for s in secrets if s and len(s.strip()) >= MIN_REDACT_LENGTH}
    return sorted(out, key=len, reverse=True)  # prima i più lunghi: un segreto contenuto in un altro non lo spezza


def redactor(secrets: Iterable[Optional[str]]) -> Optional[Callable[[str], str]]:
    """Funzione che maschera i `secrets` in un testo (None se non c'è niente da mascherare): da preparare una volta e
    usare su molte righe (output dei processi)."""
    values = _usable(secrets)
    if not values:
        return None
    masked = {v.lower(): mask(v) for v in values}
    pattern = re.compile("|".join(re.escape(v) for v in values), re.IGNORECASE)

    def apply(text: str) -> str:
        if not text or not pattern.search(text):
            return text
        return pattern.sub(lambda m: masked.get(m.group(0).lower(), mask(m.group(0))), text)

    return apply


def redact(text: str, secrets: Iterable[Optional[str]]) -> str:
    """`text` con ogni segreto di `secrets` mascherato (`mask`), senza distinguere maiuscole e minuscole."""
    apply = redactor(secrets)
    return apply(text) if apply is not None and isinstance(text, str) else text
