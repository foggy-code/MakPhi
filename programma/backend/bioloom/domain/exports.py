"""Esportazioni: script di sessione PyMOL autocontenuto."""

from __future__ import annotations

import re

SCHEMES = {
    "ss": "color grey80, {n}\ncolor firebrick, {n} and ss h\ncolor gold, {n} and ss s\ncolor grey70, {n} and ss l+''",
    "chain": "util.cbc {n}",
    "rainbow": "spectrum count, rainbow, {n} and name CA",
    "bfactor": "spectrum b, blue_white_red, {n}",
}


def pymol_script(pdb_text: str, name: str, scheme: str = "ss") -> str:
    """Script .pml con la struttura incorporata: PyMOL la riceve identica (barre rovesciate, virgolette, a capo e
    caratteri non ASCII compresi). Il testo entra come letterale Python ASCII su una sola riga (ascii(), cioè repr()
    con gli escape anche per i caratteri non ASCII): nessuna riga del file può chiudere il blocco `python` prima del
    tempo né aggiungere comandi, e lo script resta ASCII qualunque codifica usi PyMOL per leggerlo."""
    clean = re.sub(r"[^A-Za-z0-9_]", "_", name)[:40] or "bioloom_model"
    color = SCHEMES.get(scheme, SCHEMES["ss"]).format(n=clean)
    return f"""# Sessione PyMOL generata da BioLoom v4
# Apri con: pymol {clean}.pml
python
cmd.read_pdbstr({ascii(pdb_text)}, "{clean}")
python end
hide everything
show cartoon, {clean}
show sticks, {clean} and hetatm and not resn HOH
set cartoon_fancy_helices, 1
set ray_shadows, 0
set antialias, 2
bg_color white
{color}
orient {clean}
"""
