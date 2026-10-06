"""Struttura secondaria delle voci di un allineamento PIR, residuo per residuo (Editor PIR).

Per ogni template: DSSP (Kabsch-Sander integrato, `secondary.assign`) sul file della struttura, letto come lo legge
MODELLER: la prima catena dal residuo iniziale della seconda riga PIR (o dal primo), le catene successive (dopo '/')
dal loro primo residuo; ogni lettera del PIR consuma il residuo amminoacidico successivo della catena ('-', '.' e '/'
no). Per il bersaglio: predizione GOR dalla sola sequenza, catena per catena (la finestra non scavalca le catene).

Il risultato è per residuo, non per colonna: una stringa con un carattere per lettera della voce (stati DSSP a 8,
'?' dove non c'è assegnazione), così lo spostamento dei gap nell'editor non richiede di ricalcolare nulla. Le lettere
del PIR diverse dal residuo del file sono elencate: MODELLER si fermerebbe ("Sequence difference between alignment
and PDB").
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Callable, Optional

from . import secondary, structure
from .sequences import PirEntry, parse_pir, sanitize_code

UNKNOWN = "?"
#: lettere diverse dal file riportate per voce (le altre si contano soltanto)
MAX_MISMATCHES = 20

Resolver = Callable[[str], Optional[Path]]


def _letter(res) -> str:
    name = res.get_resname().strip().upper()
    return "M" if name == "MSE" else structure.three_to_one(name)


def _is_residue(res) -> bool:
    """Residui amminoacidici come nella copia del template per MODELLER: ATOM con CA, più le selenometionine (MET)."""
    return "CA" in res and (res.id[0] == " " or res.get_resname().strip().upper() == "MSE")


@lru_cache(maxsize=32)
def _structure_data(path: str, mtime_ns: int) -> tuple[dict[str, list[tuple[str, str]]], dict[tuple[str, str], str]]:
    """Residui per catena [(numero, lettera)] in ordine di file e DSSP a 8 stati per (catena, numero). In memoria per
    file e data di modifica: spostare i gap nell'editor rilegge la stessa struttura molte volte."""
    model = structure.first_model(structure.load_structure(Path(path), "template"))
    residues: dict[str, list[tuple[str, str]]] = {}
    for chain in model:
        rows = [(structure._resid(res), _letter(res)) for res in chain if _is_residue(res)]
        if rows:
            residues[chain.id] = rows
    ss = {(row["chain"], row["resnum"]): row["ss"] for row in secondary.assign(model)}
    return residues, ss


def structure_data(path: Path):
    return _structure_data(str(path), path.stat().st_mtime_ns)


def _field(entry: PirEntry, i: int) -> str:
    return entry.fields[i].strip() if len(entry.fields) > i else ""


def segment_chains(entry: PirEntry, spec_chains: list[str], file_chains: list[str]) -> list[str]:
    """Catena del file per ogni pezzo della voce separato da '/': quelle del template scelto se sono tante quanti i
    pezzi, altrimenti dalla seconda riga PIR (catena iniziale e finale) e poi in ordine di file."""
    n = len(entry.chains())
    if len(spec_chains) == n:
        return list(spec_chains)
    first = _field(entry, 1) or (spec_chains[0] if spec_chains else (file_chains[0] if file_chains else ""))
    last = _field(entry, 3)
    if n == 1:
        return [first]
    if n == 2 and last and last != first:
        return [first, last]
    start = file_chains.index(first) if first in file_chains else 0
    out = file_chains[start:start + n]
    return out + [""] * (n - len(out))


def template_secondary(entry: PirEntry, residues: dict[str, list[tuple[str, str]]], ss: dict[tuple[str, str], str],
                       spec_chains: list[str]) -> dict:
    """DSSP per lettera della voce di un template, con le lettere che non corrispondono al file."""
    chains = segment_chains(entry, spec_chains, list(residues))
    out: list[str] = []
    mismatches: list[dict] = []
    mismatch_count = 0
    problems: list[str] = []
    index = 0
    for k, piece in enumerate(entry.chains()):
        chain = chains[k] if k < len(chains) else ""
        rows = residues.get(chain)
        letters = [c for c in piece if c.isalpha()]
        if rows is None:
            problems.append(f"catena «{chain or '?'}» assente dal file" if chain else "catena non indicata")
            out.extend(UNKNOWN * len(letters))
            index += len(letters)
            continue
        pos = 0
        start = _field(entry, 0)
        if k == 0 and start and start.upper() not in ("FIRST", "."):
            found = next((i for i, (num, _) in enumerate(rows) if num == start), None)
            if found is None:
                problems.append(f"residuo iniziale {start}:{chain} assente dal file")
                out.extend(UNKNOWN * len(letters))
                index += len(letters)
                continue
            pos = found
        missing = max(0, len(letters) - (len(rows) - pos))
        for letter in letters:
            index += 1
            if pos >= len(rows):
                out.append(UNKNOWN)
                continue
            num, aa = rows[pos]
            pos += 1
            out.append(ss.get((chain, num), UNKNOWN))
            if letter.upper() != "X" and aa != "X" and letter.upper() != aa:
                mismatch_count += 1
                if len(mismatches) < MAX_MISMATCHES:
                    mismatches.append({"index": index, "letter": letter.upper(), "residue": f"{aa} {chain}:{num}"})
        if missing:
            problems.append(f"la catena {chain} del file finisce prima della voce ({missing} lettere senza residuo)")
    return {"ss": "".join(out), "chains": chains, "mismatches": mismatches, "mismatch_count": mismatch_count,
            "problems": problems}


def target_secondary(entry: PirEntry) -> dict:
    """Predizione GOR per lettera della voce, catena per catena."""
    parts = []
    for piece in entry.chains():
        letters = "".join(c for c in piece if c.isalpha())
        parts.append(secondary.predict_gor(letters)["prediction"] if letters else "")
    return {"ss": "".join(parts)}


def template_code(template_id: str, chains: list[str]) -> str:
    """Codice della voce PIR di un template preparato per MODELLER (come modeller.prepare_template)."""
    return sanitize_code(f"{template_id}_{''.join(chains)}")


def _guess(code: str, resolve: Resolver) -> Optional[tuple[str, list[str], Path]]:
    """Template della libreria dal solo codice: il codice stesso (1UBQ), o <id>_<catene> (1UBQ_A, 1ABC_AB)."""
    path = resolve(code)
    if path is not None:
        return code, [], path
    if "_" in code:
        tid, chains = code.rsplit("_", 1)
        if chains.isalnum() and len(chains) <= 26:
            path = resolve(tid)
            if path is not None:
                return tid, list(chains), path
    return None


def annotate(text: str, templates: list[dict], resolve: Resolver) -> dict:
    """Struttura secondaria di ogni voce del PIR. `templates`: i template del documento [{id, chains}], abbinati alle
    voci per codice (come per la modellazione) e altrimenti per posizione; senza, si prova a ricavarli dal codice.
    `resolve(id)`: file del template nella libreria, o None."""
    entries = parse_pir(text)
    specs = [{"id": str(t.get("id", "")), "chains": [str(c) for c in t.get("chains") or []]} for t in templates]
    by_code = {template_code(s["id"], s["chains"]).upper(): i for i, s in enumerate(specs)}
    used: set[int] = set()
    # prima gli abbinamenti per codice, poi per posizione sulle voci rimaste (come ComputeService._normalize_pir)
    pick: dict[int, tuple[int, str]] = {}
    template_rows = [i for i, e in enumerate(entries) if e.is_template]
    for i in template_rows:
        j = by_code.get(entries[i].code.upper())
        if j is not None and j not in used:
            used.add(j)
            pick[i] = (j, "code")
    for i in template_rows:
        if i in pick:
            continue
        j = next((j for j in range(len(specs)) if j not in used), None)
        if j is not None:
            used.add(j)
            pick[i] = (j, "position")

    out = []
    for i, e in enumerate(entries):
        item: dict = {"code": e.code, "kind": e.kind, "residues": sum(c.isalpha() for c in e.sequence), "ss": None,
                      "source": None, "method": None, "template": None, "matched_by": None, "message": None,
                      "mismatches": [], "mismatch_count": 0, "problems": []}
        if e.kind == "sequence":
            item.update(target_secondary(e), source="gor", method="predizione GOR dalla sola sequenza (euristica)")
        elif e.is_template:
            found: Optional[tuple[str, list[str], Path]] = None
            if i in pick:
                j, how = pick[i]
                path = resolve(specs[j]["id"])
                if path is not None:
                    found = (specs[j]["id"], specs[j]["chains"], path)
                    item["matched_by"] = how
            if found is None:
                guess = _guess(e.code, resolve)
                if guess is not None:
                    found = guess
                    item["matched_by"] = "code_guess"
            if found is None:
                item["message"] = (f"Template «{e.code}» non trovato nella libreria: la struttura secondaria si "
                                   "mostra per i template della libreria (aggiungilo dalla Libreria template).")
            else:
                tid, chains, path = found
                try:
                    residues, ss = structure_data(path)
                except Exception as exc:  # file illeggibile: la voce resta senza struttura secondaria
                    item["message"] = f"Struttura del template {tid} non leggibile: {exc}"
                else:
                    item.update(template_secondary(e, residues, ss, chains), source="dssp",
                                method="DSSP (Kabsch-Sander) sul file del template",
                                template={"id": tid, "chains": chains})
        out.append(item)
    return {"entries": out}
