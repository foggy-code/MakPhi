"""Elettrostatica di Poisson-Boltzmann (F7b): file PQR, griglia e input di APBS, mappa OpenDX, potenziale sulla
superficie per residuo. Logica pura (numpy, KD-tree di Biopython): PDB2PQR e APBS li esegue `engines/electrostatics`.

**Catena di calcolo:**

1. PDB2PQR 3 (Dolinsky et al., Nucleic Acids Res. 35:W522, 2007; Jurrus et al., Protein Sci. 27:112, 2018) aggiunge
   gli idrogeni, assegna i protonati a pH 7 con PROPKA 3 (Olsson et al., J. Chem. Theory Comput. 7:525, 2011) e scrive
   cariche e raggi del campo di forza in un file PQR. Campo di forza predefinito PARSE (Sitkoff, Sharp & Honig, J. Phys.
   Chem. 98:1978, 1994: cariche e raggi parametrizzati proprio per i calcoli di continuo di Poisson-Boltzmann; è il
   predefinito di PDB2PQR); in alternativa AMBER (ff99 di PDB2PQR).
2. APBS 3.4 (Baker et al., PNAS 98:10037, 2001) risolve l'equazione di Poisson-Boltzmann linearizzata (`lpbe`) con
   `mg-auto`: griglia grossolana e griglia fine centrate sulla molecola, dimensionate come `psize.py` di PDB2PQR
   (`grid_parameters`). Parametri dichiarati in `APBS_PARAMETERS`: dielettrico della proteina 2 e del solvente 78,54
   (acqua a 25 °C), forza ionica 150 mM (ioni ±1 di raggio 2,0 Å, come l'input generato da PDB2PQR), 298,15 K,
   superficie molecolare liscia (`smol`, sonda 1,4 Å), cariche a spline cubica (`spl2`), bordo con Debye-Hückel
   (`sdh`). Il potenziale è in kT/e (a 298,15 K, 1 kT/e = 25,69 mV).
3. Il potenziale si campiona sulla superficie accessibile al solvente (`residue_potentials`): per ogni atomo i punti
   della sua sfera di raggio r + 1,4 Å non coperti da altri atomi (Shrake & Rupley, J. Mol. Biol. 79:351, 1973, 100
   punti per atomo come Biopython), cioè dove sta il centro di una molecola d'acqua a contatto; è lo stesso spostamento
   di 1,4 Å dalla superficie che usa UCSF Chimera per colorare le superfici con il potenziale (Pettersen et al., J.
   Comput. Chem. 25:1605, 2004). Valore di un atomo esposto = media sui suoi punti (interpolazione trilineare della
   mappa); valore del residuo = media sugli atomi esposti (idrogeni compresi, con i raggi del PQR). I residui senza
   atomi esposti non hanno valore.

La scala del 3D è divergente rosso (negativo) – bianco – blu (positivo), saturata a ±5 kT/e (`SCALE_LIMIT`), la
convenzione dei visualizzatori di APBS (PyMOL, Chimera).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional

import numpy as np

# ------------------------------------------------------------------------------------------ parametri dichiarati
FORCEFIELDS = ("PARSE", "AMBER")
DEFAULT_FORCEFIELD = "PARSE"
DEFAULT_PH = 7.0
APBS_PARAMETERS = {
    "equation": "lpbe",           # Poisson-Boltzmann linearizzata
    "pdie": 2.0,                  # dielettrico interno (proteina)
    "sdie": 78.54,                # dielettrico del solvente (acqua, 25 °C)
    "ionic_strength": 0.150,      # mol/L, coppia di ioni monovalenti
    "ion_radius": 2.0,            # Å
    "temperature": 298.15,        # K
    "srfm": "smol",               # superficie molecolare liscia
    "srad": 1.4,                  # raggio della sonda del solvente, Å
    "sdens": 10.0,                # punti per Å² della superficie
    "swin": 0.3,                  # finestra della spline della superficie, Å
    "chgm": "spl2",               # cariche distribuite con spline cubica B
    "bcfl": "sdh",                # condizione al bordo: Debye-Hückel a sfera singola
}
# psize.py di PDB2PQR: griglia grossolana = 1,7 × molecola, fine = molecola + 20 Å, passo fine desiderato 0,5 Å.
# Memoria: psize stima 200 byte per punto con un tetto di 400 MB; APBS 3.4.1 ne usa di più (misurato su 1UBQ: 586 MB
# di picco per 1,21 milioni di punti, circa 500 byte per punto), quindi MakPhi stima 500 byte per punto con un tetto
# di 1000 MB: lo stesso numero massimo di punti di psize (circa 2,1 milioni) con una stima realistica. Oltre il tetto
# psize divide il calcolo fra più processi (mg-para); MakPhi tiene un solo processo e allarga il passo della griglia
# fine, dichiarato nel risultato.
CFAC = 1.7
FADD = 20.0
SPACE = 0.5
GMEMFAC = 500
GMEMCEIL_MB = 1000
MAX_SPACE = 3.0
PROBE = 1.4
SPHERE_POINTS = 100
SCALE_LIMIT = 5.0  # kT/e: estremi della scala di colore
KT_E_MV = 25.69    # mV per kT/e a 298,15 K (kT/e = RT/F)
# soglia di "potenziale marcato" per i conteggi del riepilogo (kT/e)
STRONG = 1.0
# residui con gruppi titolabili in PROPKA (anche con i nomi delle varianti di AMBER); gli altri residui carichi sono
# estremità della catena
TITRATABLE = {"LYS", "LYN", "ARG", "HIS", "HIP", "HID", "HIE", "ASP", "ASH", "GLU", "GLH", "CYS", "CYM", "TYR", "TYM"}


@dataclass
class PqrAtom:
    chain: str
    resnum: str
    resname: str
    name: str
    xyz: tuple[float, float, float]
    charge: float
    radius: float


# ------------------------------------------------------------------------------------------ PQR
def _resnum(value: str, icode: str = "") -> str:
    return f"{int(value)}{icode.strip()}"


def parse_pqr(text: str) -> list[PqrAtom]:
    """Atomi di un file PQR di PDB2PQR. Colonne fisse come il PDB fino alle coordinate (catena in colonna 22 con
    `--keep-chain`), poi carica e raggio separati da spazi; se le colonne fisse non si leggono (`--whitespace` o un
    altro programma) si usano i campi separati da spazi."""
    atoms: list[PqrAtom] = []
    for line in text.splitlines():
        if not line.startswith(("ATOM", "HETATM")):
            continue
        rest = line[54:].split()
        try:
            if len(rest) < 2:
                raise ValueError
            atoms.append(PqrAtom(chain=line[21:22].strip(), resnum=_resnum(line[22:26], line[26:27]),
                                 resname=line[17:20].strip(), name=line[12:16].strip(),
                                 xyz=(float(line[30:38]), float(line[38:46]), float(line[46:54])),
                                 charge=float(rest[0]), radius=float(rest[1])))
            continue
        except ValueError:
            pass
        parts = line.split()
        try:  # ATOM serial name resname [chain] resnum x y z q r
            if len(parts) == 11:
                chain, resnum = parts[4], parts[5]
            elif len(parts) == 10:
                chain, resnum = "", parts[4]
            else:
                continue
            digits = resnum.rstrip("ABCDEFGHIJKLMNOPQRSTUVWXYZ")
            atoms.append(PqrAtom(chain=chain, resnum=_resnum(digits, resnum[len(digits):]), resname=parts[3],
                                 name=parts[2], xyz=(float(parts[-5]), float(parts[-4]), float(parts[-3])),
                                 charge=float(parts[-2]), radius=float(parts[-1])))
        except ValueError:
            continue
    return atoms


def net_charge(atoms: list[PqrAtom]) -> float:
    return round(sum(a.charge for a in atoms), 3) + 0.0  # + 0.0: niente "-0.0" da somme di poco negative


def residue_charges(atoms: list[PqrAtom]) -> dict[tuple[str, str], float]:
    """Carica di ogni residuo (somma delle cariche dei suoi atomi), estremità N e C comprese nel primo e nell'ultimo."""
    out: dict[tuple[str, str], float] = {}
    for a in atoms:
        key = (a.chain, a.resnum)
        out[key] = out.get(key, 0.0) + a.charge
    return {k: round(v, 3) + 0.0 for k, v in out.items()}


def charge_summary(atoms: list[PqrAtom], names: Optional[dict[tuple[str, str], str]] = None) -> dict:
    """Residui carichi (|q| ≥ 0,5) a pH dato, con i conteggi per tipo: una lettura dei protonati scelti da PROPKA.
    `names`: nome del residuo nella struttura originale (PDB2PQR rinomina le varianti, es. HIP o ASH con AMBER)."""
    charges = residue_charges(atoms)
    pqr_names: dict[tuple[str, str], str] = {}
    for a in atoms:
        pqr_names.setdefault((a.chain, a.resnum), a.resname)
    charged = []
    for (chain, resnum), q in charges.items():
        if abs(q) >= 0.5:
            name = (names or {}).get((chain, resnum)) or pqr_names[(chain, resnum)]
            charged.append({"chain": chain, "resnum": resnum, "resname": name, "charge": round(q, 2)})
    positive = sum(1 for c in charged if c["charge"] > 0)
    by_name: dict[str, int] = {}
    for c in charged:
        # un residuo non titolabile carico è un'estremità della catena (NH3+ o COO−, sommati al primo o all'ultimo)
        name = c["resname"] if c["resname"] in TITRATABLE else ("N-term" if c["charge"] > 0 else "C-term")
        label = f"{name}{'+' if c['charge'] > 0 else '−'}"
        by_name[label] = by_name.get(label, 0) + 1
    return {"net_charge": net_charge(atoms), "positive": positive, "negative": len(charged) - positive,
            "by_type": dict(sorted(by_name.items())), "charged": charged}


# ------------------------------------------------------------------------------------------ griglia e input APBS
def _grid_points(length: float, space: float) -> int:
    """Punti di griglia per `mg-auto` come psize: n = 32·k + 1 (multigrid a 4 livelli), almeno 33."""
    t = int(length / space + 0.5)
    n = 32 * int((t - 1) / 32.0 + 0.5) + 1
    return max(33, n)


def grid_parameters(atoms: list[PqrAtom], space: float = SPACE, ceiling_mb: float = GMEMCEIL_MB) -> dict:
    """Griglie di `mg-auto` dimensionate sulla molecola (estensione delle sfere atomiche del PQR), come psize.py:
    grossolana = CFAC × estensione, fine = estensione + FADD (non oltre la grossolana), centrate sulla molecola; punti
    dal passo fine. Se la memoria stimata (GMEMFAC byte per punto) supera il tetto, il passo fine cresce del 10% alla
    volta fino a rientrarvi (al massimo MAX_SPACE Å)."""
    if not atoms:
        raise ValueError("nessun atomo nel PQR")
    xyz = np.array([a.xyz for a in atoms], dtype=float)
    rad = np.array([a.radius for a in atoms], dtype=float)
    lo = (xyz - rad[:, None]).min(axis=0)
    hi = (xyz + rad[:, None]).max(axis=0)
    mol = hi - lo
    coarse = CFAC * mol
    fine = np.minimum(mol + FADD, coarse)
    center = (hi + lo) / 2
    wanted = space
    while True:
        dime = [_grid_points(float(f), space) for f in fine]
        memory = GMEMFAC * dime[0] * dime[1] * dime[2] / 1024 / 1024
        if memory <= ceiling_mb or space >= MAX_SPACE:
            break
        space = min(MAX_SPACE, space * 1.1)
    spacing = [float(f) / (n - 1) for f, n in zip(fine, dime)]
    return {
        "dime": dime, "cglen": [round(float(c), 3) for c in coarse], "fglen": [round(float(f), 3) for f in fine],
        "center": [round(float(c), 3) for c in center], "molecule": [round(float(m), 3) for m in mol],
        "spacing": [round(s, 3) for s in spacing], "memory_mb": round(memory, 1), "ceiling_mb": ceiling_mb,
        "requested_spacing": wanted, "coarsened": space > wanted + 1e-9,
    }


def apbs_input(pqr_name: str, grid: dict, dx_stem: str = "potential") -> str:
    """Input di APBS (`mg-auto`) con i parametri di `APBS_PARAMETERS`; scrive il potenziale in `<dx_stem>.dx`."""
    p = APBS_PARAMETERS
    fmt = lambda values: " ".join(f"{v:.3f}" for v in values)  # noqa: E731
    conc = f"{p['ionic_strength']:.3f}"
    return "\n".join([
        "# MakPhi: potenziale elettrostatico (APBS mg-auto), griglia dimensionata come psize.py",
        "read",
        f"    mol pqr {pqr_name}",
        "end",
        "elec name makphi",
        "    mg-auto",
        f"    dime {grid['dime'][0]} {grid['dime'][1]} {grid['dime'][2]}",
        f"    cglen {fmt(grid['cglen'])}",
        f"    fglen {fmt(grid['fglen'])}",
        "    cgcent mol 1",
        "    fgcent mol 1",
        "    mol 1",
        f"    {p['equation']}",
        f"    bcfl {p['bcfl']}",
        f"    ion charge 1 conc {conc} radius {p['ion_radius']:.1f}",
        f"    ion charge -1 conc {conc} radius {p['ion_radius']:.1f}",
        f"    pdie {p['pdie']:.2f}",
        f"    sdie {p['sdie']:.2f}",
        f"    srfm {p['srfm']}",
        f"    chgm {p['chgm']}",
        f"    sdens {p['sdens']:.1f}",
        f"    srad {p['srad']:.1f}",
        f"    swin {p['swin']:.1f}",
        f"    temp {p['temperature']:.2f}",
        "    calcenergy no",
        "    calcforce no",
        f"    write pot dx {dx_stem}",
        "end",
        "quit",
        "",
    ])


# ------------------------------------------------------------------------------------------ mappa OpenDX
@dataclass
class DxGrid:
    origin: np.ndarray   # (3,) Å
    delta: np.ndarray    # (3,) passo lungo x, y, z (griglia ortogonale, come la scrive APBS)
    counts: tuple[int, int, int]
    values: np.ndarray   # (nx, ny, nz), kT/e


def parse_dx(text: str) -> DxGrid:
    """Mappa OpenDX scritta da APBS: `gridpositions counts`, `origin`, tre righe `delta` (diagonali), poi i valori con
    z che varia più in fretta (ordine di APBS e del formato)."""
    counts: Optional[tuple[int, int, int]] = None
    origin: Optional[np.ndarray] = None
    deltas: list[list[float]] = []
    lines = text.splitlines()
    start = None
    for i, line in enumerate(lines):
        words = line.split()
        if not words or line.startswith("#"):
            continue
        if line.startswith("object") and "gridpositions" in line:
            counts = tuple(int(w) for w in words[-3:])  # type: ignore[assignment]
        elif words[0] == "origin":
            origin = np.array([float(w) for w in words[1:4]])
        elif words[0] == "delta":
            deltas.append([float(w) for w in words[1:4]])
        elif line.startswith("object") and "data follows" in line:
            start = i + 1
            break
    if counts is None or origin is None or len(deltas) != 3 or start is None:
        raise ValueError("intestazione OpenDX incompleta")
    delta_matrix = np.array(deltas)
    if np.abs(delta_matrix - np.diag(np.diag(delta_matrix))).max() > 1e-9:
        raise ValueError("griglia OpenDX non ortogonale")
    n = counts[0] * counts[1] * counts[2]
    data: list[str] = []
    for line in lines[start:]:
        if line.startswith(("attribute", "object", "component")):
            break
        data.extend(line.split())
        if len(data) >= n:
            break
    values = np.array(data[:n], dtype=float)
    if values.size != n:
        raise ValueError(f"mappa OpenDX con {values.size} valori invece di {n}")
    return DxGrid(origin=origin, delta=np.diag(delta_matrix).copy(), counts=counts, values=values.reshape(counts))


def interpolate(grid: DxGrid, points: np.ndarray) -> np.ndarray:
    """Valori della mappa nei punti (N, 3) per interpolazione trilineare; NaN fuori dalla griglia."""
    pts = np.asarray(points, dtype=float).reshape(-1, 3)
    rel = (pts - grid.origin) / grid.delta
    counts = np.array(grid.counts)
    out = np.full(len(pts), np.nan)
    inside = np.all((rel >= 0) & (rel <= counts - 1), axis=1)
    if not inside.any():
        return out
    r = rel[inside]
    i0 = np.minimum(np.floor(r).astype(int), counts - 2)
    f = r - i0
    v = grid.values
    x0, y0, z0 = i0[:, 0], i0[:, 1], i0[:, 2]
    fx, fy, fz = f[:, 0], f[:, 1], f[:, 2]
    acc = np.zeros(len(r))
    for dx in (0, 1):
        wx = fx if dx else 1 - fx
        for dy in (0, 1):
            wy = fy if dy else 1 - fy
            for dz in (0, 1):
                wz = fz if dz else 1 - fz
                acc += wx * wy * wz * v[x0 + dx, y0 + dy, z0 + dz]
    out[inside] = acc
    return out


# ------------------------------------------------------------------------------------------ superficie
def sphere_points(n: int = SPHERE_POINTS) -> np.ndarray:
    """Punti quasi uniformi sulla sfera unitaria (spirale aurea), gli stessi di `Bio.PDB.SASA.ShrakeRupley`."""
    dl = math.pi * (3 - 5 ** 0.5)
    dz = 2.0 / n
    k = np.arange(n)
    z = 1 - dz / 2 - k * dz
    r = np.sqrt(np.clip(1 - z * z, 0, None))
    lon = k * dl
    return np.column_stack([np.cos(lon) * r, np.sin(lon) * r, z])


def accessible_points(coords: np.ndarray, radii: np.ndarray, probe: float = PROBE,
                      n_points: int = SPHERE_POINTS) -> list[np.ndarray]:
    """Per ogni atomo i punti della superficie accessibile al solvente (sfera di raggio r + sonda) non coperti dalle
    sfere allargate degli altri atomi, come Shrake & Rupley 1973; un atomo sepolto ha un elenco vuoto."""
    from Bio.PDB.kdtrees import KDTree

    coords = np.ascontiguousarray(coords, dtype=np.float64)
    expanded = np.asarray(radii, dtype=float) + probe
    unit = sphere_points(n_points)
    neighbors: list[list[int]] = [[] for _ in range(len(coords))]
    if len(coords) > 1:
        reach = 2 * float(expanded.max())
        for pair in KDTree(coords, 10).neighbor_search(reach):
            i, j = pair.index1, pair.index2
            if pair.radius < expanded[i] + expanded[j]:
                neighbors[i].append(j)
                neighbors[j].append(i)
    out = []
    for i in range(len(coords)):
        pts = coords[i] + expanded[i] * unit
        near = neighbors[i]
        if near:
            d2 = ((pts[:, None, :] - coords[near][None, :, :]) ** 2).sum(axis=2)
            free = (d2 >= (expanded[near] ** 2)[None, :] - 1e-9).all(axis=1)
            pts = pts[free]
        out.append(pts)
    return out


def residue_potentials(atoms: list[PqrAtom], grid: DxGrid, probe: float = PROBE,
                       n_points: int = SPHERE_POINTS) -> dict[tuple[str, str], dict]:
    """Potenziale sulla superficie accessibile per residuo (chiave (catena, residuo)): media sugli atomi esposti della
    media dei loro punti; `exposed` = atomi esposti, `points` = punti campionati. Un residuo senza atomi esposti (o con
    i punti fuori dalla griglia) ha `potential` None."""
    coords = np.array([a.xyz for a in atoms], dtype=float)
    radii = np.array([a.radius for a in atoms], dtype=float)
    per_atom = accessible_points(coords, radii, probe, n_points)
    sums: dict[tuple[str, str], list[float]] = {}
    counts: dict[tuple[str, str], int] = {}
    for atom, pts in zip(atoms, per_atom):
        key = (atom.chain, atom.resnum)
        sums.setdefault(key, [])
        if not len(pts):
            continue
        values = interpolate(grid, pts)
        values = values[np.isfinite(values)]
        if values.size:
            sums[key].append(float(values.mean()))
            counts[key] = counts.get(key, 0) + int(values.size)
    out = {}
    for key, values in sums.items():
        out[key] = {"potential": round(float(np.mean(values)), 3) if values else None, "exposed": len(values),
                    "points": counts.get(key, 0)}
    return out


def summarize(residues: list[dict]) -> dict:
    """Riepilogo del potenziale di superficie per residuo: estremi, media, residui marcatamente positivi o negativi."""
    values = [r["potential"] for r in residues if r.get("potential") is not None]
    if not values:
        return {"residues": 0, "min": None, "max": None, "mean": None, "positive": 0, "negative": 0,
                "threshold": STRONG}
    return {"residues": len(values), "min": round(min(values), 2), "max": round(max(values), 2),
            "mean": round(sum(values) / len(values), 2), "positive": sum(1 for v in values if v > STRONG),
            "negative": sum(1 for v in values if v < -STRONG), "threshold": STRONG}
