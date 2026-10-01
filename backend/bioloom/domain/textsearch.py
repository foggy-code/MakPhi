"""Ricerca testuale della ricerca globale: parole della richiesta, corrispondenza, pertinenza, escape per SQLite.

**Corrispondenza.** Il testo si divide in parole come fa il tokenizer `unicode61` di SQLite FTS5 con
`remove_diacritics 2`: sequenze di lettere e cifre (il resto, `_` compreso, separa), minuscole, senza accenti
(«Proteína» → «proteina»). Una voce corrisponde se *ogni* parola della richiesta è l'inizio di una parola di uno dei
suoi campi: «p6990» trova «sp_P69905_HBA_HUMAN», «modell» trova «Modellazione». È la semantica di una query FTS5
`"p6990"* "modell"*`; il ripiego LIKE e l'indice in memoria dei template applicano la stessa regola in Python, così
i tre percorsi danno gli stessi risultati.

**Pertinenza.** Punteggio additivo per parola, con il nome che pesa il doppio degli altri campi (pesatura per campo
come in BM25F: Robertson, Zaragoza e Taylor, CIKM 2004) e la parola intera che vale più del solo inizio, più un premio
quando il nome coincide con la richiesta o comincia con essa (le richieste brevi della ricerca "mentre scrivi" sono di
solito l'inizio del nome cercato). A parità contano la stella e poi la data (lo decide il chiamante).

| Caso | Punti |
|---|---|
| nome uguale alla richiesta (stesse parole, stesso ordine) | +10 |
| nome che comincia con la richiesta | +4 |
| parola intera del nome | 3 per parola |
| inizio di una parola del nome | 2 per parola |
| parola intera di un altro campo (accessione, descrizione, tipo…) | 1,5 per parola |
| inizio di una parola di un altro campo | 1 per parola |

**Sicurezza.** Le parole contengono solo lettere e cifre; nella query FTS5 ognuna è comunque tra virgolette, con le
virgolette interne raddoppiate, quindi `AND`, `OR`, `NEAR`, `*`, `:`, `"` e parentesi della richiesta non diventano mai
operatori. Nei LIKE `%`, `_` e `\\` si proteggono con `ESCAPE '\\'`. I valori passano sempre come parametri.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Iterable

MAX_QUERY_LENGTH = 200
MAX_TERMS = 8
MAX_TERM_LENGTH = 64

EXACT_NAME = 10.0
NAME_PREFIX = 4.0
WORD_NAME = 3.0
PREFIX_NAME = 2.0
WORD_OTHER = 1.5
PREFIX_OTHER = 1.0

_TOKEN = re.compile(r"[^\W_]+")


def fold(text: object) -> str:
    """Minuscole senza accenti (decomposizione canonica NFD e rimozione dei segni combinanti)."""
    decomposed = unicodedata.normalize("NFD", str(text or ""))
    return "".join(c for c in decomposed if not unicodedata.combining(c)).lower()


def tokens(text: object) -> list[str]:
    """Parole di un testo, come le vede la ricerca."""
    return _TOKEN.findall(fold(text))


def query_terms(query: object) -> list[str]:
    """Parole della richiesta, senza ripetizioni, al più `MAX_TERMS` di al più `MAX_TERM_LENGTH` caratteri.

    Una richiesta fatta solo di punteggiatura (`"*"`, `--`, `'; DROP`) non ha parole: nessun risultato.
    """
    out: list[str] = []
    for word in tokens(str(query or "")[:MAX_QUERY_LENGTH]):
        word = word[:MAX_TERM_LENGTH]
        if word not in out:
            out.append(word)
        if len(out) == MAX_TERMS:
            break
    return out


def fts_query(terms: Iterable[str]) -> str:
    """Query FTS5 con ogni parola tra virgolette e come prefisso: `"p6990"* "umana"*` (tutte le parole)."""
    return " ".join('"' + t.replace('"', '""') + '"*' for t in terms)


def like_escape(term: str) -> str:
    """Pattern LIKE per "contiene `term`" con `ESCAPE '\\'`."""
    return "%" + term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


def _field_tokens(fields: Iterable[object]) -> list[str]:
    out: list[str] = []
    for value in fields:
        out.extend(tokens(value))
    return out


def matches(terms: list[str], primary: object, others: Iterable[object] = ()) -> bool:
    """Ogni parola della richiesta è l'inizio di una parola del nome o di un altro campo."""
    if not terms:
        return False
    words = tokens(primary) + _field_tokens(others)
    return all(any(w.startswith(t) for w in words) for t in terms)


def score(terms: list[str], primary: object, others: Iterable[object] = ()) -> float:
    """Pertinenza (tabella del modulo); 0 se la voce non corrisponde a tutte le parole."""
    if not terms:
        return 0.0
    name = tokens(primary)
    rest = _field_tokens(others)
    total = 0.0
    for term in terms:
        if term in name:
            best = WORD_NAME
        elif any(w.startswith(term) for w in name):
            best = PREFIX_NAME
        elif term in rest:
            best = WORD_OTHER
        elif any(w.startswith(term) for w in rest):
            best = PREFIX_OTHER
        else:
            return 0.0
        total += best
    if name == terms:
        total += EXACT_NAME
    elif name[:len(terms) - 1] == terms[:-1] and len(name) >= len(terms) and name[len(terms) - 1].startswith(terms[-1]):
        total += NAME_PREFIX
    return total
