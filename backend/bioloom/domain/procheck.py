"""Lettura del riepilogo di PROCHECK (file .sum)."""

from __future__ import annotations

import re

_FLOAT = r"(-?\d+(?:\.\d+)?)"


def _first(pattern: str, text: str, cast=float):
    m = re.search(pattern, text, re.IGNORECASE)
    if not m:
        return None
    try:
        return cast(m.group(1))
    except ValueError:
        return None


def parse_sum(text: str) -> dict:
    """Percentuali di Ramachandran nelle 4 regioni di PROCHECK, G-factor e parametri geometrici.

    Esempio di riga: `Ramachandran plot:   95.5% core    4.5% allow    0.0% gener    0.0% disall`.
    """
    rama = {"core": None, "allowed": None, "generous": None, "disallowed": None}
    m = re.search(rf"{_FLOAT}%\s*core\s+{_FLOAT}%\s*allow\w*\s+{_FLOAT}%\s*gener\w*\s+{_FLOAT}%\s*disall",
                  text, re.IGNORECASE)
    if m:
        rama = {"core": float(m.group(1)), "allowed": float(m.group(2)),
                "generous": float(m.group(3)), "disallowed": float(m.group(4))}
    return {
        "ramachandran": rama,
        "g_factors": {
            "dihedrals": _first(rf"Dihedrals:\s*{_FLOAT}", text),
            "covalent": _first(rf"Covalent:\s*{_FLOAT}", text),
            "overall": _first(rf"Overall:\s*{_FLOAT}", text),
        },
        "residues": _first(r"(\d+)\s+residues", text, int),
        "bad_contacts": _first(r"Bad contacts:\s*(\d+)", text, int),
        "max_deviation": _first(rf"Max\.deviation:\s*{_FLOAT}", text),
        "bond_len_angle": _first(rf"Bond len/angle:\s*{_FLOAT}", text),
        "mc_bond_lengths_ok": _first(rf"M/c bond lengths:\s*{_FLOAT}%", text),
        "mc_bond_angles_ok": _first(rf"M/c bond angles:\s*{_FLOAT}%", text),
        "planar_groups_ok": _first(rf"Planar groups:\s*{_FLOAT}%", text),
        "summary": text.strip()[-4000:],
    }
