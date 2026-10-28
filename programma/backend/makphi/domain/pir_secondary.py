"""Struttura secondaria delle voci di un allineamento PIR, residuo per residuo (Editor PIR).

Per ogni template: DSSP (Kabsch-Sander integrato, `secondary.assign`) sul file della struttura, letto come lo legge
MODELLER: la prima catena dal residuo iniziale della seconda riga PIR (o dal primo), le catene successive (dopo '/')
dal loro primo residuo; ogni lettera del PIR consuma il residuo amminoacidico successivo della catena ('-', '.' e '/'
no). Per il bersaglio: predizione GOR dalla sola sequenza, catena per catena (la finestra non scavalca le catene).

Selenometionine (MSE, HETATM): la copia del template per MODELLER le tiene (come MET) solo con l'opzione dei ligandi
(`hetatm`, come modeller.prepare_template); senza, mancano dalla copia e dal PIR. Se l'opzione non è nota si usa, per
ogni catena, la lettura (con o senza MSE) che corrisponde meglio alle lettere del PIR. Il DSSP le considera sempre
residui della catena: un'elica che ne contiene una resta intera.

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
#: residuo di una catena: (numero con codice d'inserzione, lettera, è una selenometionina HETATM)
Row = tuple[str, str, bool]


def _is_mse(res) -> bool:
    return res.id[0] != " " and res.get_resname().strip().upper() == "MSE"


def _is_residue(res) -> bool:
    """Residui amminoacidici: ATOM con CA, più le selenometionine HETATM (MET nella copia con i ligandi)."""
    return "CA" in res and (res.id[0] == " " or _is_mse(res))


@lru_cache(maxsize=32)
def _structure_data(path: str, mtime_ns: int) -> tuple[dict[str, list[Row]], dict[tuple[str, str], str]]:
    """Residui per catena in ordine di file e DSSP a 8 stati per (catena, numero). In memoria per file e data di
    modifica (al massimo 32 file): spostare i gap nell'editor rilegge la stessa struttura molte volte."""
    model = structure.first_model(structure.load_structure(Path(path), "template"))
    residues: dict[str, list[Row]] = {}
    for chain in model:
        rows = [(structure._resid(res), "M" if _is_mse(res) else structure.three_to_one(res.get_resname()),
                 _is_mse(res)) for res in chain if _is_residue(res)]
        if rows:
            residues[chain.id] = rows
    ss = {(row["chain"], row["resnum"]): row["ss"] for row in secondary.assign(model, accept=_is_residue)}
    return residues, ss


def structure_data(path: Path):
    return _structure_data(str(path), path.stat().st_mtime_ns)


def _field(entry: PirEntry, i: int) -> str:
    return entry.fields[i].strip() if len(entry.fields) > i else ""


def segment_chains(entry: PirEntry, spec_chains: list[str], file_chains: list[str]) -> list[str]:
    """Catena del file per ogni pezzo della voce separato da '/'. Con le catene del template scelto (tante quanti i
    pezzi): nell'ordine del file, come la copia preparata per MODELLER, salvo che la seconda riga PIR cominci da
    un'altra di esse (modalità complesso: ordine del bersaglio). Altrimenti dalla seconda riga PIR (catena iniziale e
    finale) e poi in ordine di file."""
    n = len(entry.chains())
    first = _field(entry, 1)
    if len(spec_chains) == n:
        ordered = [c for c in file_chains if c in spec_chains]
        if len(ordered) != n or (first in spec_chains and first != ordered[0]):
            return list(spec_chains)
        return ordered
    first = first or (spec_chains[0] if spec_chains else (file_chains[0] if file_chains else ""))
    last = _field(entry, 3)
    if n == 1:
        return [first]
    if n == 2 and last and last != first:
        return [first, last]
    start = file_chains.index(first) if first in file_chains else 0
    out = file_chains[start:start + n]
    return out + [""] * (n - len(out))


def _map_piece(letters: list[str], rows: list[Row], chain: str, ss: dict[tuple[str, str], str], first_index: int,
               start: str) -> dict:
    """Una catena della voce: stato DSSP e residuo del file ("A:27") per lettera, lettere diverse dal file, problemi."""
    out: list[str] = []
    ids: list[Optional[str]] = []
    mismatches: list[dict] = []
    problems: list[str] = []
    pos = 0
    if start:
        found = next((i for i, row in enumerate(rows) if row[0] == start), None)
        if found is None:
            return {"ss": [UNKNOWN] * len(letters), "ids": [None] * len(letters), "mismatches": [], "count": 0,
                    "problems": [f"residuo iniziale {start}:{chain} assente dal file"]}
        pos = found
    missing = max(0, len(letters) - (len(rows) - pos))
    count = 0
    for k, letter in enumerate(letters):
        if pos >= len(rows):
            out.append(UNKNOWN)
            ids.append(None)
            continue
        num, aa, _mse = rows[pos]
        pos += 1
        out.append(ss.get((chain, num), UNKNOWN))
        ids.append(f"{chain}:{num}")
        if letter.upper() != "X" and aa != "X" and letter.upper() != aa:
            count += 1
            if len(mismatches) < MAX_MISMATCHES:
                mismatches.append({"index": first_index + k, "letter": letter.upper(),
                                   "residue": f"{aa} {chain}:{num}"})
    if missing:
        problems.append(f"la catena {chain} del file finisce prima della voce ({missing} lettere senza residuo)")
    return {"ss": out, "ids": ids, "mismatches": mismatches, "count": count, "problems": problems}


def template_secondary(entry: PirEntry, residues: dict[str, list[Row]], ss: dict[tuple[str, str], str],
                       spec_chains: list[str], hetatm: Optional[bool] = None) -> dict:
    """DSSP per lettera della voce di un template, con le lettere che non corrispondono al file. `hetatm`: la copia
    per MODELLER tiene le selenometionine (True), non le tiene (False), non si sa (None: la lettura che corrisponde
    meglio)."""
    chains = segment_chains(entry, spec_chains, list(residues))
    out: list[str] = []
    residue_ids: list[Optional[str]] = []
    mismatches: list[dict] = []
    mismatch_count = 0
    problems: list[str] = []
    index = 1
    for k, piece in enumerate(entry.chains()):
        chain = chains[k] if k < len(chains) else ""
        rows = residues.get(chain)
        letters = [c for c in piece if c.isalpha()]
        if rows is None:
            problems.append(f"catena «{chain}» assente dal file" if chain else "catena non indicata")
            out.extend(UNKNOWN * len(letters))
            residue_ids.extend([None] * len(letters))
            index += len(letters)
            continue
        start = _field(entry, 0) if k == 0 else ""
        start = "" if start.upper() in ("FIRST", ".") else start
        without = [row for row in rows if not row[2]]
        if hetatm is True or len(without) == len(rows):
            readings = [rows]
        elif hetatm is False:
            readings = [without]
        else:
            readings = [without, rows]
        best = min((_map_piece(letters, r, chain, ss, index, start) for r in readings),
                   key=lambda m: (len(m["problems"]), m["count"]))
        out.extend(best["ss"])
        residue_ids.extend(best["ids"])
        mismatch_count += best["count"]
        mismatches.extend(best["mismatches"][:MAX_MISMATCHES - len(mismatches)])
        problems.extend(best["problems"])
        index += len(letters)
    return {"ss": "".join(out), "residue_ids": residue_ids, "chains": chains, "mismatches": mismatches,
            "mismatch_count": mismatch_count, "problems": problems}


def target_secondary(entry: PirEntry) -> dict:
    """Predizione GOR per lettera della voce, catena per catena."""
    parts = []
    for piece in entry.chains():
        letters = "".join(c for c in piece if c.isalpha())
        parts.append(secondary.predict_gor(letters)["prediction"] if letters else "")
    return {"ss": "".join(parts)}


def template_codes(template_id: str, chains: list[str]) -> set[str]:
    """Codici possibili della voce PIR di un template preparato (come modeller.prepare_template): catene nell'ordine
    del file (qui: alfabetico, di solito coincide) o in quello indicato (modalità complesso)."""
    return {sanitize_code(f"{template_id}_{''.join(order)}").upper() for order in (chains, sorted(chains))}


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


def pair_templates(entries: list[PirEntry], specs: list[dict]) -> dict[int, tuple[int, str]]:
    """Voce del PIR → template del documento, come ComputeService._normalize_pir (così l'editor mostra la struttura che
    MODELLER userà): voce per voce, il template con lo stesso codice se non è già usato, altrimenti il primo libero."""
    codes = [template_codes(s["id"], s["chains"]) for s in specs]
    used: set[int] = set()
    out: dict[int, tuple[int, str]] = {}
    for i, e in enumerate(entries):
        if not e.is_template:
            continue
        j = next((j for j, c in enumerate(codes) if e.code.upper() in c), None)
        how = "code"
        if j is None or j in used:
            j = next((j for j in range(len(specs)) if j not in used), None)
            how = "position"
        if j is not None:
            used.add(j)
            out[i] = (j, how)
    return out


def annotate(text: str, templates: list[dict], resolve: Resolver) -> dict:
    """Struttura secondaria di ogni voce del PIR. `templates`: i template del documento [{id, chains, hetatm}],
    abbinati alle voci come per la modellazione; senza, si prova a ricavarli dal codice. `resolve(id)`: file del
    template nella libreria, o None."""
    entries = parse_pir(text)
    specs = [{"id": str(t.get("id", "")), "chains": [str(c) for c in t.get("chains") or []],
              "hetatm": t.get("hetatm")} for t in templates]
    pick = pair_templates(entries, specs)
    out = []
    for i, e in enumerate(entries):
        item: dict = {"code": e.code, "kind": e.kind, "residues": sum(c.isalpha() for c in e.sequence), "ss": None,
                      "source": None, "method": None, "template": None, "matched_by": None, "message": None,
                      "mismatches": [], "mismatch_count": 0, "problems": []}
        if e.kind == "sequence":
            item.update(target_secondary(e), source="gor", method="predizione GOR dalla sola sequenza (euristica)")
        elif e.is_template:
            found: Optional[tuple[str, list[str], Path]] = None
            hetatm: Optional[bool] = None
            if i in pick:
                j, how = pick[i]
                path = resolve(specs[j]["id"])
                if path is not None:
                    found = (specs[j]["id"], specs[j]["chains"], path)
                    hetatm = specs[j]["hetatm"]
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
                    # il testo dell'eccezione (percorsi, righe del file) va solo nel log, non nella risposta
                    print(f"[MakPhi] Struttura del template {tid} non leggibile ({path.name}): {exc}", flush=True)
                    item["message"] = f"Struttura del template {tid} non leggibile (dettagli nel log di MakPhi)."
                else:
                    result = template_secondary(e, residues, ss, chains, hetatm)
                    item.update(result, source="dssp", method="DSSP (Kabsch-Sander) sul file del template",
                                template={"id": tid, "chains": result["chains"],
                                          "format": "cif" if path.suffix.lower() in (".cif", ".mmcif") else "pdb"})
        out.append(item)
    return {"entries": out}
