"""Etichette (tag) di progetti, job, sequenze e template: normalizzazione e validazione.

Una forma sola per etichetta, così "Urgente", " urgente " e "#urgente" sono la stessa etichetta e i filtri non si
dividono fra varianti:

- Unicode NFC (una lettera accentata è un solo carattere, comunque sia stata digitata), minuscole, spazi esterni e
  `#` iniziale tolti;
- spazi interni → `-`, trattini ripetuti uniti, separatori `- _ . :` ai bordi tolti ("  -piega-  " → "piega");
- caratteri ammessi: lettere e cifre (anche accentate), `-`, `_`, `.`, `:`, `+`; inizio con una lettera o una cifra,
  `+` anche in fondo (ioni come "zn2+", "ca2+"). Niente `/`, spazi o virgolette: l'etichetta va così com'è in un URL
  e in un filtro;
- al massimo `TAG_MAX_LENGTH` caratteri dopo la normalizzazione: un'etichetta più lunga è rifiutata, non troncata
  (due etichette lunghe diverse non devono diventare la stessa).

Il frontend applica la stessa regola (`lib/tags.ts`) per mostrare subito l'etichetta che verrà salvata.
"""

from __future__ import annotations

import re
import unicodedata

from ..core.errors import AppError

TAG_MAX_LENGTH = 32
MAX_TAGS_PER_ENTITY = 20
EDGE_SEPARATORS = "-_.:"

_SPACES = re.compile(r"\s+")
_DASHES = re.compile(r"-{2,}")
# lettera o cifra all'inizio, poi lettere, cifre e i separatori ammessi (i bordi sono già senza `- _ . :`)
_VALID = re.compile(r"^[^\W_][\w.:+-]*$")


def normalize_tag(raw: object) -> str:
    """Forma canonica di un'etichetta; AppError (400, `invalid_tag`) se vuota, troppo lunga o con caratteri non
    ammessi."""
    text = unicodedata.normalize("NFC", str(raw or "")).strip().lower().lstrip("#").strip()
    text = _DASHES.sub("-", _SPACES.sub("-", text)).strip(EDGE_SEPARATORS)
    if not text:
        raise AppError("Etichetta vuota.", code="invalid_tag")
    if len(text) > TAG_MAX_LENGTH:
        raise AppError(f"Etichetta troppo lunga: al massimo {TAG_MAX_LENGTH} caratteri.", code="invalid_tag")
    if not _VALID.match(text):
        raise AppError("Etichetta non valida: usa lettere, cifre e i caratteri - _ . : +", code="invalid_tag")
    return text
