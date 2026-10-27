"""Tasche e cavità con fpocket (F7b): lettura dell'uscita del programma in un risultato strutturato.

Logica pura, senza fpocket: legge i file che fpocket 4 scrive nella cartella `<nome>_out/` (test su un'uscita reale di
1UBQ in `tests/data/fpocket_1ubq_out`):

- `<nome>_info.txt`: descrittori di ogni tasca, numerata da fpocket in ordine di punteggio ("Pocket 1 :" e righe
  "chiave : valore");
- `<nome>_pockets.pqr`: le sfere alfa (centro e raggio) di tutte le tasche, residuo STP con il numero della tasca;
- `pockets/pocket<N>_atm.pdb`: gli atomi della proteina che rivestono la tasca N.

**Metodo** (Le Guilloux, Schmidtke & Tufféry, BMC Bioinformatics 10:168, 2009): tassellazione di Voronoi degli atomi,
sfere alfa (sfere vuote che toccano quattro atomi) con raggio fra 3,4 e 6,2 Å (valori predefiniti di fpocket 4,
`-m`/`-M`), raggruppate in tasche con almeno 15 sfere (`-i`). MakPhi usa i parametri predefiniti.

**Punteggi** (riportati come li calcola fpocket, non ricalcolati):

- `score`: punteggio di fpocket per ordinare le tasche (combinazione lineare di descrittori, Le Guilloux et al. 2009);
- `druggability`: punteggio 0–1 di "druggabilità" di Schmidtke & Barril (J. Med. Chem. 53:5858, 2010), regressione
  logistica su descrittori della tasca addestrata sul set di Cheng et al. 2007; sopra 0,5 la tasca è probabilmente
  in grado di legare una piccola molecola simile a un farmaco (soglia della pubblicazione e della documentazione di
  fpocket). È un indicatore statistico, non una previsione di affinità.

Il centro di una tasca è il baricentro dei centri delle sue sfere alfa (come il "centro di massa" del descrittore
"Cent. of mass - Alpha Sphere max dist" di fpocket).
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

# Druggability sopra la quale una tasca è "probabilmente druggabile" (Schmidtke & Barril 2010)
DRUGGABLE = 0.5

# Descrittori di `<nome>_info.txt` → campi del risultato (etichette di fpocket 4, spazi e maiuscole normalizzati)
INFO_FIELDS = {
    "score": "score",
    "druggability score": "druggability",
    "number of alpha spheres": "alpha_spheres",
    "total sasa": "sasa_total",
    "polar sasa": "sasa_polar",
    "apolar sasa": "sasa_apolar",
    "volume": "volume",
    "mean local hydrophobic density": "hydrophobic_density",
    "mean alpha sphere radius": "mean_sphere_radius",
    "mean alp. sph. solvent access": "mean_solvent_access",
    "apolar alpha sphere proportion": "apolar_proportion",
    "hydrophobicity score": "hydrophobicity_score",
    "volume score": "volume_score",
    "polarity score": "polarity_score",
    "charge score": "charge_score",
    "proportion of polar atoms": "polar_atoms_pct",
    "alpha sphere density": "sphere_density",
    "cent. of mass - alpha sphere max dist": "max_distance",
    "flexibility": "flexibility",
}
INTEGER_FIELDS = {"alpha_spheres", "polarity_score", "charge_score"}

_POCKET = re.compile(r"^\s*Pocket\s+(\d+)\s*:\s*$")
_FIELD = re.compile(r"^\s*([^:]+?)\s*:\s*(\S+)\s*$")


def _number(text: str) -> Optional[float]:
    try:
        return float(text)
    except ValueError:
        return None


def parse_info(text: str) -> dict[int, dict]:
    """Descrittori per tasca da `<nome>_info.txt`: {numero di fpocket: {campo: valore}}. Le righe sconosciute
    restano in `other` con l'etichetta originale (versioni di fpocket con descrittori in più)."""
    pockets: dict[int, dict] = {}
    current: Optional[dict] = None
    for line in text.splitlines():
        head = _POCKET.match(line)
        if head:
            current = pockets.setdefault(int(head.group(1)), {"other": {}})
            continue
        if current is None:
            continue
        m = _FIELD.match(line)
        if not m:
            continue
        label = " ".join(m.group(1).split()).lower()
        value = _number(m.group(2))
        key = INFO_FIELDS.get(label)
        if key is None:
            current["other"][" ".join(m.group(1).split())] = value
        elif value is not None:
            current[key] = int(round(value)) if key in INTEGER_FIELDS else value
    return pockets


def _pdb_fields(line: str) -> Optional[dict]:
    """Campi di una riga ATOM/HETATM in colonne fisse (PDB e PQR di fpocket); None se la riga non si legge."""
    try:
        return {
            "name": line[12:16].strip(), "resname": line[17:20].strip(), "chain": line[21:22].strip(),
            "resnum": f"{int(line[22:26])}{line[26:27].strip()}",
            "xyz": (float(line[30:38]), float(line[38:46]), float(line[46:54])),
        }
    except (ValueError, IndexError):
        return None


def parse_spheres(text: str) -> dict[int, list[dict]]:
    """Sfere alfa per tasca da `<nome>_pockets.pqr` (residuo STP numerato come la tasca; dopo le coordinate carica e
    raggio, separati da spazi): {numero: [{"center": [x, y, z], "radius": r}]}."""
    out: dict[int, list[dict]] = {}
    for line in text.splitlines():
        if not line.startswith(("ATOM", "HETATM")):
            continue
        fields = _pdb_fields(line)
        rest = line[54:].split()
        if fields is None or fields["resname"] != "STP" or len(rest) < 2:
            continue
        radius = _number(rest[-1])
        try:
            pocket = int(fields["resnum"].rstrip("ABCDEFGHIJKLMNOPQRSTUVWXYZ"))
        except ValueError:
            continue
        if radius is None:
            continue
        out.setdefault(pocket, []).append({"center": [round(c, 3) for c in fields["xyz"]], "radius": round(radius, 3)})
    return out


def parse_lining(text: str) -> tuple[list[dict], int]:
    """Residui (in ordine di comparsa, senza ripetizioni) e numero di atomi che rivestono una tasca, da
    `pocket<N>_atm.pdb`."""
    residues: list[dict] = []
    seen: set[tuple[str, str]] = set()
    atoms = 0
    for line in text.splitlines():
        if not line.startswith(("ATOM", "HETATM")):
            continue
        fields = _pdb_fields(line)
        if fields is None:
            continue
        atoms += 1
        key = (fields["chain"], fields["resnum"])
        if key not in seen:
            seen.add(key)
            residues.append({"chain": fields["chain"], "resnum": fields["resnum"], "resname": fields["resname"]})
    return residues, atoms


def _resnum_key(resnum: str) -> tuple[int, str]:
    digits = resnum.rstrip("ABCDEFGHIJKLMNOPQRSTUVWXYZ")
    try:
        return int(digits), resnum[len(digits):]
    except ValueError:
        return 0, resnum


def centroid(spheres: list[dict]) -> Optional[list[float]]:
    if not spheres:
        return None
    n = len(spheres)
    return [round(sum(s["center"][k] for s in spheres) / n, 3) for k in range(3)]


def read_output(out_dir: Path, name: str) -> dict:
    """Tasche dall'uscita di fpocket (`<out_dir>/<name>_info.txt`, `<name>_pockets.pqr`, `pockets/`), ordinate per
    druggability decrescente (a parità per punteggio di fpocket): l'ordine della tabella dell'interfaccia.

    Ogni tasca ha il numero di fpocket (`id`, ordine per `score`), il posto per druggability (`rank`), i descrittori
    di `INFO_FIELDS`, `druggable` (≥ `DRUGGABLE`), il centro, le sfere alfa e i residui che la rivestono ordinati per
    catena e numero."""
    info_path = out_dir / f"{name}_info.txt"
    info = parse_info(info_path.read_text(encoding="utf-8", errors="replace"))
    pqr = out_dir / f"{name}_pockets.pqr"
    spheres = parse_spheres(pqr.read_text(encoding="utf-8", errors="replace")) if pqr.is_file() else {}
    pockets = []
    for pid, desc in info.items():
        atm = out_dir / "pockets" / f"pocket{pid}_atm.pdb"
        residues, atoms = parse_lining(atm.read_text(encoding="utf-8", errors="replace")) if atm.is_file() else ([], 0)
        residues.sort(key=lambda r: (r["chain"], _resnum_key(r["resnum"])))
        own = spheres.get(pid, [])
        druggability = desc.get("druggability")
        pockets.append({
            "id": pid, **{k: v for k, v in desc.items() if k != "other"},
            "druggable": druggability is not None and druggability >= DRUGGABLE,
            "center": centroid(own), "spheres": own, "residues": residues, "atoms": atoms,
            "other": desc.get("other") or {},
        })
    pockets.sort(key=lambda p: (-(p.get("druggability") or 0.0), -(p.get("score") or 0.0), p["id"]))
    for rank, p in enumerate(pockets, 1):
        p["rank"] = rank
    return {"pockets": pockets, "summary": summarize(pockets)}


def summarize(pockets: list[dict]) -> dict:
    """Riepilogo: numero di tasche, quante probabilmente druggabili, la migliore per druggability, volume totale."""
    best = pockets[0] if pockets else None
    return {
        "count": len(pockets),
        "druggable": sum(1 for p in pockets if p.get("druggable")),
        "best": None if best is None else {"id": best["id"], "druggability": best.get("druggability"),
                                           "volume": best.get("volume"), "residues": len(best["residues"])},
        "threshold": DRUGGABLE,
    }
