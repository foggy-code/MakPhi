"""Report HTML autonomo di un modello (anche rifinito con OpenMM) o di un'analisi di qualità, stampabile in PDF dal
browser.

Un solo file: CSS in linea, grafici SVG generati qui, nessuno script né risorsa esterna (una Content-Security-Policy
nel documento lo ribadisce), immagine 3D facoltativa incorporata come data URI dopo la verifica che sia un PNG vero.
Le regole `@media print` tolgono l'indice, ripetono le intestazioni delle tabelle e non spezzano grafici, blocchi di
allineamento e righe a metà pagina.

Logica pura: `render(data)` riceve i dati già raccolti dal servizio (`services/reports.py`) e restituisce il testo.

Sicurezza: nomi di sequenze e di file, titoli e metadati (RCSB, UniProt, AlphaFold DB, file caricati) non sono
fidati. Ogni testo passa da `_e` (`html.escape`, anche le virgolette); gli attributi contengono solo costanti e
numeri calcolati qui (`_f`, `_num`); nessun URL viene costruito da dati in ingresso: l'unico è il data URI
dell'immagine, ricodificato in base64 dai byte del PNG verificato (`decode_png`).
"""

from __future__ import annotations

import base64
import binascii
import html
import math
import re
import struct
import zlib
from datetime import datetime, timezone
from typing import Any, Iterable, Optional

from ..core.errors import AppError
from . import consensus as consensus_mod
from . import disorder as disorder_mod
from . import plddt, ramachandran, sequences

# ---------------------------------------------------------------------------------------------- immagine 3D
# PNG del viewer (3Dmol `pngURI`): 3 MB bastano per una vista a piena pagina anche su schermi ad alta densità.
MAX_IMAGE_BYTES = 3 * 1024 * 1024
MAX_IMAGE_SIDE = 16384
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
# Profondità di bit ammesse per tipo di colore (specifica PNG, W3C/ISO 15948, tabella 11.1)
PNG_DEPTHS = {0: (1, 2, 4, 8, 16), 2: (8, 16), 3: (1, 2, 4, 8), 4: (8, 16), 6: (8, 16)}
_B64_MAX = 4 * ((MAX_IMAGE_BYTES + 2) // 3)

# Nel documento e nelle risposte della rotta: niente script, niente risorse esterne, solo stili in linea e il PNG.
CSP = ("default-src 'none'; img-src data:; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'; "
       "frame-ancestors 'none'")

KIND_LABEL = {"model": "Modellazione", "loop": "Raffinamento di un loop", "pipeline": "Pipeline batch",
              "quality": "Analisi di qualità", "refine": "Rifinitura energetica"}
KIND_LABEL.update({"mutate": "Mutagenesi in silico", "alascan": "Scansione di alanina"})  # F9.2
MUTATION_KINDS = ("mutate", "alascan")


def _image_error(reason: str) -> AppError:
    return AppError(f"L'immagine 3D non è un PNG valido: {reason}.", code="report_image_invalid")


def _too_large() -> AppError:
    return AppError(f"L'immagine 3D supera il limite di {MAX_IMAGE_BYTES // (1024 * 1024)} MB: riduci la finestra "
                    "del viewer o genera il report senza immagine.", code="report_image_too_large", status=413)


def decode_png(value: str) -> bytes:
    """Byte di un PNG inviato in base64, anche come data URI `data:image/png;base64,…` (3Dmol `pngURI`).

    Rifiuta (400 `report_image_invalid`) testo non base64, altri tipi di immagine e PNG malformati, e (413
    `report_image_too_large`) immagini oltre `MAX_IMAGE_BYTES`, controllando la lunghezza prima di decodificare.
    """
    text = re.sub(r"\s+", "", value or "")
    if text[:5].lower() == "data:":
        head, sep, text = text.partition(",")
        if not sep or head.lower() != "data:image/png;base64":
            raise _image_error("serve un data URI data:image/png;base64")
    if not text:
        raise _image_error("immagine vuota")
    if len(text) > _B64_MAX:
        raise _too_large()
    try:
        data = base64.b64decode(text, validate=True)
    except (binascii.Error, ValueError):
        raise _image_error("il testo non è in base64")
    if len(data) > MAX_IMAGE_BYTES:
        raise _too_large()
    png_size(data)
    return data


def png_size(data: bytes) -> tuple[int, int]:
    """Larghezza e altezza di un PNG valido, altrimenti AppError `report_image_invalid`.

    Controlli della specifica PNG: firma; blocchi con lunghezza entro il file, tipo di 4 lettere e CRC-32 corretto;
    IHDR per primo (13 byte, dimensioni positive, profondità di bit ammessa per il tipo di colore); almeno un IDAT;
    IEND per ultimo e nessun dato dopo.
    """
    if not data.startswith(PNG_SIGNATURE):
        raise _image_error("manca la firma PNG")
    pos, size, has_idat = len(PNG_SIGNATURE), None, False
    while True:
        if pos + 12 > len(data):
            raise _image_error("file troncato (manca IEND)")
        length = int.from_bytes(data[pos:pos + 4], "big")
        ctype = data[pos + 4:pos + 8]
        end = pos + 12 + length
        if length > 0x7FFFFFFF or end > len(data):
            raise _image_error("blocco troncato")
        if not all(65 <= b <= 90 or 97 <= b <= 122 for b in ctype):
            raise _image_error("tipo di blocco non valido")
        body = data[pos + 8:pos + 8 + length]
        if zlib.crc32(ctype + body) & 0xFFFFFFFF != int.from_bytes(data[end - 4:end], "big"):
            raise _image_error(f"CRC errato nel blocco {ctype.decode('ascii')}")
        if size is None:
            if ctype != b"IHDR" or length != 13:
                raise _image_error("il primo blocco non è IHDR")
            width, height = struct.unpack(">II", body[:8])
            depth, color = body[8], body[9]
            if not (0 < width <= MAX_IMAGE_SIDE and 0 < height <= MAX_IMAGE_SIDE):
                raise _image_error(f"dimensioni {width}×{height} fuori dai limiti")
            if depth not in PNG_DEPTHS.get(color, ()):
                raise _image_error("tipo di colore o profondità di bit non ammessi")
            size = (width, height)
        elif ctype == b"IHDR":
            raise _image_error("IHDR ripetuto")
        elif ctype == b"IDAT":
            has_idat = True
        pos = end
        if ctype == b"IEND":
            break
    if not has_idat:
        raise _image_error("nessun blocco di dati IDAT")
    if pos != len(data):
        raise _image_error("dati dopo la fine dell'immagine (IEND)")
    return size


def safe_filename(stem: str, suffix: str = ".html", default: str = "report") -> str:
    """Nome di file ASCII sicuro per `Content-Disposition`: lettere, cifre, '_', '-', '.' (senza '.', '_' e '-' ai
    bordi, quindi nomi diversi possono dare lo stesso file: lo ZIP delle pipeline usa i nomi degli elementi)."""
    clean = re.sub(r"[^A-Za-z0-9_.-]+", "_", stem or "")
    clean = re.sub(r"\.{2,}", ".", re.sub(r"_+", "_", clean)).strip("._-")[:80].strip("._-")
    return (clean or default) + suffix


# ---------------------------------------------------------------------------------------------- formattazione
def _e(value: Any) -> str:
    """Testo sicuro in HTML e SVG (anche dentro gli attributi): None → vuoto."""
    return "" if value is None else html.escape(str(value), quote=True)


def _isnum(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _num(value: Any, digits: int = 2, suffix: str = "") -> str:
    """Numero all'italiana (virgola decimale, segno meno tipografico); '—' se assente."""
    if not _isnum(value):
        return "—"
    text = f"{float(value):.{digits}f}".replace(".", ",")
    return ("−" + text[1:] if text.startswith("-") else text) + suffix


def _signed(value: Any, digits: int = 2, suffix: str = "") -> str:
    """Differenza con il segno esplicito (+1,20 / −1,20); '—' se assente."""
    if not _isnum(value):
        return "—"
    return ("+" if float(value) > 0 else "") + _num(value, digits, suffix)


def _sci(value: Any) -> str:
    """E-value: notazione scientifica sotto 0,001."""
    if not _isnum(value):
        return "—"
    if value == 0:
        return "0"
    if abs(value) < 1e-3:
        mant, exp = f"{float(value):.1e}".split("e")
        return f"{mant.replace('.', ',')}·10^{int(exp)}".replace("-", "−")
    return _num(value, 3)


def _f(value: float) -> str:
    """Coordinata SVG (sempre un numero: niente testo in ingresso negli attributi)."""
    return f"{float(value):.1f}"


def _date(value: Any) -> str:
    if not value:
        return "—"
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return _e(value)
    if dt.tzinfo is None:
        return dt.strftime("%d/%m/%Y %H:%M")
    return dt.astimezone(timezone.utc).strftime("%d/%m/%Y %H:%M UTC")


def _letters(seq: str) -> int:
    return sum(1 for c in seq if c.isalpha())


_RESNUM = re.compile(r"^-?\d+")


def _resnum_int(resnum: Any) -> Optional[int]:
    m = _RESNUM.match(str(resnum))
    return int(m.group()) if m else None


def _consecutive(a: dict, b: dict) -> bool:
    """Residui adiacenti nella stessa catena (come domain/consensus): una linea dei profili non li scavalca."""
    if a.get("chain") != b.get("chain"):
        return False
    na, nb = _resnum_int(a.get("resnum")), _resnum_int(b.get("resnum"))
    return na is None or nb is None or 0 <= nb - na <= 1


def _residue_label(r: dict) -> str:
    return f"{r.get('resname') or ''} {r.get('chain') or ''}{r.get('resnum') or ''}".strip()


def alignment_identity(target: str, template: str) -> tuple[Optional[float], Optional[float]]:
    """(identità %, copertura %) del template sul bersaglio in un allineamento.

    Identità = residui identici / colonne con un residuo in entrambe le righe (come `sequences.validate_pir` e il
    `% SEQ ID` di MODELLER); copertura = quelle colonne / residui del bersaglio. Gap, separatori di catena '/' e
    ligandi '.' non contano.
    """
    pairs = [(x, y) for x, y in zip(target.upper(), template.upper()) if x.isalpha() and y.isalpha()]
    length = _letters(target)
    identity = round(100.0 * sum(1 for x, y in pairs if x == y) / len(pairs), 4) if pairs else None
    coverage = round(100.0 * len(pairs) / length, 4) if length else None
    return identity, coverage


# ---------------------------------------------------------------------------------------------- testi di MODELLER
_MODELLER_EXPDTA = re.compile(r"^EXPDTA\s+THEORETICAL MODEL, MODELLER\s+([0-9][\w.]*)", re.MULTILINE)
_TRIM_REMARK = re.compile(r"^REMARK 999 BIOLOOM PLDDT_TRIM THRESHOLD (\S+) REMOVED (\d+) OF (\d+)(?: FRAGMENTS (\d+))?",
                          re.MULTILINE)
# ALPHAFOLD, ESMFOLD o PREDICTED (plddt.predictor_tag): i modelli precedenti a F9.3 scrivono sempre ALPHAFOLD
_MODEL_TRIM = re.compile(r"^REMARK 999 BIOLOOM TEMPLATE (\S+) (?:ALPHAFOLD|ESMFOLD|PREDICTED) "
                         r"(?:PLDDT<(\S+) REMOVED (\d+)|(PLDDT_TRIM OFF))", re.MULTILINE)


def modeller_version(head: str) -> Optional[str]:
    """Versione di MODELLER dall'intestazione del modello (`EXPDTA THEORETICAL MODEL, MODELLER 10.8 …`)."""
    m = _MODELLER_EXPDTA.search(head or "")
    return m.group(1)[:20] if m else None


def prepared_trim(head: str) -> Optional[dict]:
    """Ritaglio pLDDT dalla copia preparata del template (`REMARK 999 BIOLOOM PLDDT_TRIM …`), None se assente."""
    m = _TRIM_REMARK.search(head or "")
    if not m:
        return None
    try:
        return {"threshold": float(m.group(1)), "removed": int(m.group(2)), "total": int(m.group(3)),
                "removed_fragments": int(m.group(4) or 0), "kept": int(m.group(3)) - int(m.group(2))}
    except ValueError:
        return None


def model_trims(head: str) -> dict[str, Optional[dict]]:
    """Template predetti dalle righe di tracciabilità del modello: codice → ritaglio (None = ritaglio spento)."""
    out: dict[str, Optional[dict]] = {}
    for m in _MODEL_TRIM.finditer(head or ""):
        if m.group(4):
            out[m.group(1)] = None
            continue
        try:
            out[m.group(1)] = {"threshold": float(m.group(2)), "removed": int(m.group(3))}
        except ValueError:
            continue
    return out


# ---------------------------------------------------------------------------------------------- grafici SVG
SVG_NS = "http://www.w3.org/2000/svg"
FONT = "font-family=\"Helvetica, Arial, sans-serif\""
# colori standard del grafico di Ramachandran (convenzione PROCHECK, Laskowski et al. 1993): favorite rosse, consentite
# gialle, non consentite bianche, residui neri, outlier etichettati in rosso
REGION_FILL = {"allowed": "#ffff00", "favored": "#ff0000"}
DISALLOWED_FILL = "#ffffff"
POINT_FILL = {"favored": "#000000", "allowed": "#000000", "outlier": "#000000"}
OUTLIER_LABEL = "#c00000"
REGION_LABEL = {"favored": "favorita", "allowed": "consentita", "outlier": "outlier"}
# Pannelli come nell'interfaccia (frontend/src/components/charts/Ramachandran.tsx): la pre-prolina usa le regioni del caso generale
RAMA_PANELS = (("general", "Caso generale (anche pre-prolina)"), ("glycine", "Glicina"), ("proline", "Prolina"))
SS_FILL = {"H": "#c0392b", "E": "#d4a106", "C": "#cfd8dc"}
SS_LABEL = {"H": "elica (H, G, I)", "E": "filamento β (E, B)", "C": "coil"}


def _svg_open(width: int, height: int, label: str) -> str:
    return (f'<svg xmlns="{SVG_NS}" viewBox="0 0 {width} {height}" width="{width}" height="{height}" '
            f'role="img" aria-label="{_e(label)}" {FONT} font-size="10">')


def _choice(value: Any, allowed: Iterable[str], default: str) -> str:
    """Valore di un insieme noto (regione, classe, stato DSSP), altrimenti `default`: i dati letti dai file non
    entrano mai negli attributi e un valore inatteso non fa fallire il report."""
    return value if isinstance(value, str) and value in allowed else default


def _lookup(table: dict, value: Any, default: Any = None) -> Any:
    """Etichetta di un valore noto; per un valore sconosciuto (o non testuale) il valore stesso o `default`."""
    if isinstance(value, str) and value in table:
        return table[value]
    return value if default is None else default


def _rama_panel(point: dict) -> str:
    return _choice(point.get("class"), ("glycine", "proline"), "general")


def rama_svg(rama: dict, panel: str) -> str:
    """Grafico di Ramachandran di una classe di residui con i colori standard: regioni favorite (rosso) e consentite
    (giallo) dai rettangoli di domain/ramachandran, calibrati sulle aree di MolProbity, il resto bianco; un cerchio nero
    per residuo, outlier etichettati in rosso."""
    title = dict(RAMA_PANELS)[panel]
    size, pl, pr, pt, pb = 250, 42, 12, 22, 34
    width, height = pl + size + pr, pt + size + pb

    def x(phi: float) -> float:
        return pl + (max(-180.0, min(180.0, phi)) + 180.0) / 360.0 * size

    def y(psi: float) -> float:
        return pt + (180.0 - max(-180.0, min(180.0, psi))) / 360.0 * size

    regions = rama.get("regions").get(panel) if isinstance(rama.get("regions"), dict) else None
    if not isinstance(regions, dict):  # rapporti senza le regioni: quelle di domain/ramachandran
        regions = ramachandran.REGIONS[panel]
    out = [_svg_open(width, height, f"Grafico di Ramachandran: {title}"),
           f'<text x="{pl}" y="14" font-size="11" font-weight="bold" fill="#1f2933">{_e(title)}</text>',
           f'<rect x="{pl}" y="{pt}" width="{size}" height="{size}" fill="{DISALLOWED_FILL}" stroke="#1f2933"/>']
    for kind in ("allowed", "favored"):
        rects = regions.get(kind)
        for rect in rects if isinstance(rects, (list, tuple)) else []:
            try:
                a, b, c, d = (float(v) for v in rect)
            except (TypeError, ValueError):
                continue
            out.append(f'<rect x="{_f(x(a))}" y="{_f(y(d))}" width="{_f(x(b) - x(a))}" height="{_f(y(c) - y(d))}" '
                       f'fill="{REGION_FILL[kind]}"/>')
    for v in (-90, 0, 90):
        out.append(f'<line x1="{_f(x(v))}" x2="{_f(x(v))}" y1="{pt}" y2="{pt + size}" stroke="#7b8794" '
                   'stroke-dasharray="3 3" stroke-width="0.6"/>')
        out.append(f'<line x1="{pl}" x2="{pl + size}" y1="{_f(y(v))}" y2="{_f(y(v))}" stroke="#7b8794" '
                   'stroke-dasharray="3 3" stroke-width="0.6"/>')
    for v in (-180, -90, 0, 90, 180):
        out.append(f'<text x="{_f(x(v))}" y="{pt + size + 13}" text-anchor="middle" fill="#52606d">{v}</text>')
        out.append(f'<text x="{pl - 5}" y="{_f(y(v) + 3)}" text-anchor="end" fill="#52606d">{v}</text>')
    out.append(f'<text x="{pl + size / 2:.1f}" y="{height - 4}" text-anchor="middle" fill="#1f2933">φ (gradi)</text>')
    out.append(f'<text x="11" y="{pt + size / 2:.1f}" text-anchor="middle" fill="#1f2933" '
               f'transform="rotate(-90 11 {pt + size / 2:.1f})">ψ (gradi)</text>')
    points = [p for p in rama.get("points") or []
              if _rama_panel(p) == panel and _isnum(p.get("phi")) and _isnum(p.get("psi"))]
    # outlier sopra gli altri punti
    points.sort(key=lambda p: p.get("region") == "outlier")
    labels = []
    for p in points:
        region = _choice(p.get("region"), POINT_FILL, "allowed")
        cx, cy = x(p["phi"]), y(p["psi"])
        outlier = region == "outlier"
        tip = f"{_residue_label(p)}: φ {_num(p['phi'], 0)}°, ψ {_num(p['psi'], 0)}° ({REGION_LABEL[region]})"
        out.append(f'<circle class="rp {region}" cx="{_f(cx)}" cy="{_f(cy)}" r="{3.6 if outlier else 2.4}" '
                   f'fill="{POINT_FILL[region]}"'
                   + (' stroke="#ffffff" stroke-width="0.8"' if outlier else ' fill-opacity="0.85"')
                   + f'><title>{_e(tip)}</title></circle>')
        if outlier:  # etichetta a destra del punto, a sinistra vicino al bordo destro del grafico
            right = cx > pl + size - 60
            tx, anchor = (cx - 5, "end") if right else (cx + 5, "start")
            labels.append(f'<text class="lab" x="{_f(tx)}" y="{_f(max(pt + 9, cy - 4))}" text-anchor="{anchor}" '
                          f'font-size="9" font-weight="bold" fill="{OUTLIER_LABEL}">{_e(_residue_label(p))}</text>')
    out.extend(labels)
    out.append("</svg>")
    return "".join(out)


def _ticks(residues: list[dict], count: int = 10) -> list[int]:
    n = len(residues)
    if n <= 1:
        return [0] if n else []
    raw = max(1, n // count)
    step = next((s for s in (1, 2, 5, 10, 20, 25, 50, 100, 200, 250, 500, 1000, 2000, 5000) if s >= raw), raw)
    return list(range(0, n, step))


def _x_axis(residues: list[dict], x, top: float, bottom: float) -> list[str]:
    """Etichette dei residui e confini di catena di un grafico per residuo."""
    out = []
    for i in _ticks(residues):
        r = residues[i]
        out.append(f'<line x1="{_f(x(i))}" x2="{_f(x(i))}" y1="{_f(bottom)}" y2="{_f(bottom + 3)}" stroke="#7b8794"/>')
        out.append(f'<text x="{_f(x(i))}" y="{_f(bottom + 13)}" text-anchor="middle" fill="#52606d">'
                   f'{_e(str(r.get("chain") or "") + str(r.get("resnum") or ""))}</text>')
    for i in range(1, len(residues)):
        if residues[i].get("chain") != residues[i - 1].get("chain"):
            xm = (x(i - 1) + x(i)) / 2
            out.append(f'<line x1="{_f(xm)}" x2="{_f(xm)}" y1="{_f(top)}" y2="{_f(bottom)}" stroke="#52606d" '
                       'stroke-dasharray="2 2"/>')
            out.append(f'<text x="{_f(xm + 3)}" y="{_f(top + 9)}" fill="#52606d">catena '
                       f'{_e(residues[i].get("chain"))}</text>')
    return out


def profile_svg(residues: list[dict], values: list[Optional[float]], *, title: str,
                threshold: Optional[float] = None, threshold_label: str = "", flag_above: bool = True,
                y_range: Optional[tuple[float, float]] = None, guides: Iterable[tuple[float, str]] = (),
                digits: int = 2, mark: bool = True, shade: Iterable[tuple[int, int, str]] = ()) -> str:
    """Profilo per residuo (linea interrotta tra catene e numerazioni non consecutive), con soglia tratteggiata e i
    residui oltre la soglia (sopra se `flag_above`, altrimenti sotto) segnati in rosso (`mark`). `shade`: tratti
    (indice iniziale, finale, descrizione) evidenziati sullo sfondo. '' se mancano i valori."""
    n = len(residues)
    vals = [float(v) if _isnum(v) else None for v in values[:n]] + [None] * max(0, n - len(values))
    numeric = [v for v in vals if v is not None]
    if n < 2 or len(numeric) < 2:
        return ""
    width, height, pl, pr, pt, pb = 760, 190, 58, 14, 24, 34
    pw, ph = width - pl - pr, height - pt - pb
    if y_range:
        lo, hi = y_range
    else:
        extra = [threshold] if _isnum(threshold) else []
        lo, hi = min(numeric + extra), max(numeric + extra)
        margin = (hi - lo) * 0.06 or 1.0
        lo, hi = lo - margin, hi + margin

    def x(i: float) -> float:
        return pl + i * pw / (n - 1)

    def y(v: float) -> float:
        return pt + (hi - max(lo, min(hi, v))) / (hi - lo) * ph

    out = [_svg_open(width, height, title),
           f'<text x="{pl}" y="14" font-size="11" font-weight="bold" fill="#1f2933">{_e(title)}</text>',
           f'<rect x="{pl}" y="{pt}" width="{pw}" height="{ph}" fill="#f7f9fb" stroke="#9aa5b1"/>']
    for a, b, label in shade:
        x0, x1 = max(pl, x(a - 0.5)), min(pl + pw, x(b + 0.5))
        out.append(f'<rect class="shade" x="{_f(x0)}" y="{pt}" width="{_f(max(1.0, x1 - x0))}" height="{ph}" '
                   f'fill="#fbe3e1"><title>{_e(label)}</title></rect>')
    for value, label in guides:
        out.append(f'<line x1="{pl}" x2="{pl + pw}" y1="{_f(y(value))}" y2="{_f(y(value))}" stroke="#c4cdd5"/>')
        out.append(f'<text x="{pl + pw - 2}" y="{_f(y(value) - 2)}" text-anchor="end" fill="#7b8794">'
                   f'{_e(label)}</text>')
    for value in (lo, (lo + hi) / 2, hi):
        out.append(f'<text x="{pl - 5}" y="{_f(y(value) + 3)}" text-anchor="end" fill="#52606d">'
                   f'{_num(value, digits)}</text>')
    path, pen = [], False
    for i, v in enumerate(vals):
        if v is None:
            pen = False
            continue
        joined = pen and _consecutive(residues[i - 1], residues[i])
        path.append(f"{'L' if joined else 'M'}{_f(x(i))},{_f(y(v))}")
        pen = True
    out.append(f'<path d="{" ".join(path)}" fill="none" stroke="#1d4e89" stroke-width="1.4"/>')
    if _isnum(threshold):
        ty = y(threshold)
        out.append(f'<line x1="{pl}" x2="{pl + pw}" y1="{_f(ty)}" y2="{_f(ty)}" stroke="#c62828" '
                   'stroke-dasharray="5 3"/>')
        if threshold_label:
            out.append(f'<text x="{pl + pw - 2}" y="{_f(ty - 3)}" text-anchor="end" fill="#c62828">'
                       f'{_e(threshold_label)}</text>')
        for i, v in enumerate(vals):
            if mark and v is not None and (v > threshold if flag_above else v < threshold):
                out.append(f'<circle class="flag" cx="{_f(x(i))}" cy="{_f(y(v))}" r="2.2" fill="#c62828">'
                           f'<title>{_e(_residue_label(residues[i]))}: {_num(v, digits)}</title></circle>')
    out.extend(_x_axis(residues, x, pt, pt + ph))
    out.append("</svg>")
    return "".join(out)


DISORDER_TITLE = "Disordine predetto (FoldIndex, finestra di 51 residui)"


def disorder_svg(residues: list[dict], scores: list[Optional[float]], flags: list[bool]) -> str:
    """Profilo di FoldIndex del consenso (F5.5): soglia 0 (sotto: probabilmente disordinato) e, sullo sfondo, le
    regioni disordinate lunghe. I singoli residui sotto 0 non si segnano: conta la regione di almeno 30 residui."""
    shade = []
    for a, b in disorder_mod.ranges(flags[:len(residues)]):
        first, last = residues[a].get("resnum"), residues[b].get("resnum")
        shade.append((a, b, f"regione disordinata predetta {first}-{last} (catena {residues[a].get('chain')})"))
    return profile_svg(residues, scores, title=DISORDER_TITLE, threshold=0.0, threshold_label="0 (sotto: disordine)",
                       flag_above=False, mark=False, shade=shade, digits=2)


def ss_strip_svg(residues: list[dict]) -> str:
    """Striscia della struttura secondaria (3 stati DSSP) lungo la sequenza, con i confini di catena."""
    n = len(residues)
    if not n:
        return ""
    width, height, pl, pr, pt, bar = 760, 62, 58, 14, 10, 18  # sotto la barra: etichette dei residui
    pw = width - pl - pr

    def x(i: float) -> float:
        return pl + i * pw / n

    out = [_svg_open(width, height, "Struttura secondaria per residuo")]
    states = [_choice(r.get("ss3"), SS_FILL, "C") for r in residues]
    i = 0
    while i < n:
        state = states[i]
        j = i + 1
        while j < n and states[j] == state and residues[j].get("chain") == residues[j - 1].get("chain"):
            j += 1
        h = bar if state != "C" else bar / 3
        out.append(f'<rect x="{_f(x(i))}" y="{_f(pt + (bar - h) / 2)}" width="{_f(x(j) - x(i))}" height="{_f(h)}" '
                   f'fill="{SS_FILL[state]}"/>')
        i = j
    out.extend(_x_axis(residues, lambda k: x(k + 0.5), pt, pt + bar + 4))
    out.append("</svg>")
    return "".join(out)


# ---------------------------------------------------------------------------------------------- blocchi di testo
def sequence_block(sequence: str, width: int = 60, group: int = 10) -> str:
    """Sequenza a righe di `width` residui in gruppi di `group`, con la posizione del primo residuo della riga."""
    lines = []
    for start in range(0, len(sequence), width):
        chunk = sequence[start:start + width]
        groups = " ".join(chunk[i:i + group] for i in range(0, len(chunk), group))
        lines.append(f"{start + 1:>6}  {groups}")
    return '<pre class="seq">' + _e("\n".join(lines)) + "</pre>"


def alignment_blocks(entries: list[sequences.PirEntry], width: int = 60) -> str:
    """Allineamento PIR a blocchi di `width` colonne: righello delle colonne, per ogni voce il numero del primo e
    dell'ultimo residuo del blocco (contati sulla voce), residui identici al bersaglio evidenziati e riga di
    conservazione Clustal ('*' identico, ':' gruppo forte, '.' gruppo debole)."""
    if not entries:
        return ""
    seqs = [e.sequence for e in entries]
    total = max(len(s) for s in seqs)
    target = next((e.sequence for e in entries if e.kind == "sequence"), "")
    conservation = sequences.consensus_line(seqs) if len({len(s) for s in seqs}) == 1 else ""
    names = [e.code[:24] for e in entries]
    name_w = max(len(n) for n in names)
    pad = " " * (name_w + 7)
    counts = [0] * len(entries)
    blocks = []
    for start in range(0, total, width):
        stop = min(total, start + width)
        ruler = [" "] * (stop - start)
        for col in range(start + 10, stop + 1, 10):
            label = str(col)
            end = col - start
            for k, ch in enumerate(label):
                pos = end - len(label) + k
                if 0 <= pos < len(ruler):
                    ruler[pos] = ch
        rows = [_e(pad + "".join(ruler))]
        for k, e in enumerate(entries):
            chunk = e.sequence[start:stop]
            residues = _letters(chunk)
            first = counts[k] + 1 if residues else counts[k]
            counts[k] += residues
            body = []
            for col, ch in enumerate(chunk, start=start):
                same = e.kind != "sequence" and ch.isalpha() and col < len(target) and target[col] == ch
                body.append(f'<b class="id">{_e(ch)}</b>' if same else _e(ch))
            rows.append(f'{_e(names[k].ljust(name_w))} {first:>5} {"".join(body)} {counts[k]}')
        if conservation:
            line = "".join("/" if col < len(target) and target[col] == "/" else conservation[col]
                           for col in range(start, stop))
            rows.append(_e(pad + line))
        blocks.append('<pre class="aln blk">' + "\n".join(rows) + "</pre>")
    return "".join(blocks)


# ---------------------------------------------------------------------------------------------- legenda e fonti
REFERENCES = {
    "modeller": "Šali A., Blundell T.L. (1993) Comparative protein modelling by satisfaction of spatial restraints. "
                "J. Mol. Biol. 234:779–815.",
    "dope": "Shen M.Y., Sali A. (2006) Statistical potential for assessment and prediction of protein structures. "
            "Protein Sci. 15:2507–2524.",
    "ga341": "Melo F., Sánchez R., Sali A. (2002) Statistical potentials for fold assessment. Protein Sci. "
             "11:430–448.",
    "ga341_b": "John B., Sali A. (2003) Comparative protein structure modeling by iterative alignment, model building "
               "and model assessment. Nucleic Acids Res. 31:3982–3992.",
    "qmean": "Benkert P., Biasini M., Schwede T. (2011) Toward the estimation of the absolute quality of individual "
             "protein structure models. Bioinformatics 27:343–350.",
    "prosa": "Wiederstein M., Sippl M.J. (2007) ProSA-web: interactive web service for the recognition of errors in "
             "three-dimensional structures of proteins. Nucleic Acids Res. 35:W407–W410.",
    "prosa_b": "Sippl M.J. (1993) Recognition of errors in three-dimensional structures of proteins. Proteins "
               "17:355–362.",
    "procheck": "Laskowski R.A., MacArthur M.W., Moss D.S., Thornton J.M. (1993) PROCHECK: a program to check the "
                "stereochemical quality of protein structures. J. Appl. Cryst. 26:283–291.",
    "alphafold": "Jumper J. et al. (2021) Highly accurate protein structure prediction with AlphaFold. Nature "
                 "596:583–589.",
    "afdb": "Tunyasuvunakool K. et al. (2021) Highly accurate protein structure prediction for the human proteome. "
            "Nature 596:590–596.",
    "lddt": "Mariani V., Biasini M., Barbato A., Schwede T. (2013) lDDT: a local superposition-free score for "
            "comparing protein structures and models using distance difference tests. Bioinformatics 29:2722–2728.",
    "ramachandran": "Ramachandran G.N., Ramakrishnan C., Sasisekharan V. (1963) Stereochemistry of polypeptide chain "
                    "configurations. J. Mol. Biol. 7:95–99.",
    "lovell": "Lovell S.C. et al. (2003) Structure validation by Cα geometry: φ, ψ and Cβ deviation. Proteins "
              "50:437–450.",
    "probe": "Word J.M. et al. (1999) Visualizing and quantifying molecular goodness-of-fit: small-probe contact dots "
             "with explicit hydrogen atoms. J. Mol. Biol. 285:1711–1733.",
    "dssp": "Kabsch W., Sander C. (1983) Dictionary of protein secondary structure: pattern recognition of "
            "hydrogen-bonded and geometrical features. Biopolymers 22:2577–2637.",
    "sasa": "Shrake A., Rupley J.A. (1973) Environment and exposure to solvent of protein atoms. Lysozyme and "
            "insulin. J. Mol. Biol. 79:351–371.",
    "tien": "Tien M.Z., Meyer A.G., Sydykova D.K., Spielman S.J., Wilke C.O. (2013) Maximum allowed solvent "
            "accessibilites of residues in proteins. PLoS ONE 8:e80635.",
    "rost": "Rost B. (1999) Twilight zone of protein sequence alignments. Protein Eng. 12:85–94.",
    "harding": "Harding M.M. (2006) Small revisions to predicted distances around metal sites in proteins. "
               "Acta Cryst. D62:678–682.",
    "cmm": "Zheng H., Cooper D.R., Porebski P.J., Shabalin I.G., Handing K.B., Minor W. (2017) CheckMyMetal: a "
           "macromolecular metal-binding validation tool. Acta Cryst. D73:223–233.",
    "kuppuraj": "Kuppuraj G., Dudev M., Lim C. (2009) Factors governing metal–ligand distances and coordination "
                "geometries of metal complexes. J. Phys. Chem. B 113:2952–2960.",
    "biolip": "Yang J., Roy A., Zhang Y. (2013) BioLiP: a semi-manually curated database for biologically relevant "
              "ligand–protein interactions. Nucleic Acids Res. 41:D1096–D1103.",
    "foldindex": "Prilusky J., Felder C.E., Zeev-Ben-Mordehai T., Rydberg E.H., Man O., Beckmann J.S., Silman I., "
                 "Sussman J.L. (2005) FoldIndex: a simple tool to predict whether a given protein sequence is "
                 "intrinsically unfolded. Bioinformatics 21:3435–3438.",
    "uversky": "Uversky V.N., Gillespie J.R., Fink A.L. (2000) Why are 'natively unfolded' proteins unstructured "
               "under physiologic conditions? Proteins 41:415–427.",
    "kyte": "Kyte J., Doolittle R.F. (1982) A simple method for displaying the hydropathic character of a protein. "
            "J. Mol. Biol. 157:105–132.",
    "ward": "Ward J.J., Sodhi J.S., McGuffin L.J., Buxton B.F., Jones D.T. (2004) Prediction and functional analysis "
            "of native disorder in proteins from the three kingdoms of life. J. Mol. Biol. 337:635–645.",
    # analisi strutturale (F7)
    "krissinel": "Krissinel E., Henrick K. (2007) Inference of macromolecular assemblies from crystalline state. J. "
                 "Mol. Biol. 372:774–797.",
    "janin2008": "Janin J., Bahadur R.P., Chakrabarti P. (2008) Protein-protein interaction and quaternary structure. "
                 "Q. Rev. Biophys. 41:133–180.",
    "loconte": "Lo Conte L., Chothia C., Janin J. (1999) The atomic structure of protein-protein recognition sites. J. "
               "Mol. Biol. 285:2177–2198.",
    "baker": "Baker E.N., Hubbard R.E. (1984) Hydrogen bonding in globular proteins. Prog. Biophys. Mol. Biol. "
             "44:97–179.",
    "barlow": "Barlow D.J., Thornton J.M. (1983) Ion-pairs in proteins. J. Mol. Biol. 168:867–885.",
    "richardson": "Richardson J.S. (1981) The anatomy and taxonomy of protein structure. Adv. Protein Chem. "
                  "34:167–339.",
    # complessi e omo-oligomeri (F5.3)
    "dockq": "Basu S., Wallner B. (2016) DockQ: a quality measure for protein-protein docking models. PLoS ONE "
             "11:e0161879.",
    "capri": "Méndez R., Leplae R., De Maria L., Wodak S.J. (2003) Assessment of blind predictions of protein-protein "
             "interactions: current status of docking methods. Proteins 52:51–67.",
    # rifinitura energetica (F9.1)
    "openmm": "Eastman P. et al. (2017) OpenMM 7: rapid development of high performance algorithms for molecular "
              "dynamics. PLoS Comput. Biol. 13:e1005659.",
    "ff14sb": "Maier J.A., Martinez C., Kasavajhala K., Wickstrom L., Hauser K.E., Simmerling C. (2015) ff14SB: "
              "improving the accuracy of protein side chain and backbone parameters from ff99SB. J. Chem. Theory "
              "Comput. 11:3696–3713.",
    "obc": "Onufriev A., Bashford D., Case D.A. (2004) Exploring protein native states and large-scale conformational "
           "changes with a modified generalized Born model. Proteins 55:383–394.",
    "lbfgs": "Liu D.C., Nocedal J. (1989) On the limited memory BFGS method for large scale optimization. Math. "
             "Program. 45:503–528.",
    "kabsch": "Kabsch W. (1976) A solution for the best rotation to relate two sets of vectors. Acta Cryst. "
              "A32:922–923.",
    # confronto con una predizione esterna (F9.3)
    "esmfold": "Lin Z., Akin H., Rao R., Hie B., Zhu Z., Lu W., Smetanin N., Verkuil R., Kabeli O., Shmueli Y., dos "
               "Santos Costa A., Fazel-Zarandi M., Sercu T., Candido S., Rives A. (2023) Evolutionary-scale prediction "
               "of atomic-level protein structure with a language model. Science 379:1123–1130.",
    "tmscore": "Zhang Y., Skolnick J. (2004) Scoring function for automated assessment of protein structure template "
               "quality. Proteins 57:702–710.",
    "xu_zhang": "Xu J., Zhang Y. (2010) How significant is a protein structure similarity with TM-score = 0.5? "
                "Bioinformatics 26:889–895.",
}

LEGEND = {
    "identity": ("Identità e copertura", "identità = residui identici / colonne in cui bersaglio e template hanno "
                 "entrambi un residuo, nell'allineamento usato da MODELLER; copertura = quelle colonne / residui del "
                 "bersaglio. Sotto il 25–30% di identità l'allineamento è in zona crepuscolare (Rost 1999)."),
    "dope": ("DOPE", "energia statistica di MODELLER (Discrete Optimized Protein Energy, Shen & Sali 2006), in unità "
             "arbitrarie: più negativa è migliore; si confronta solo tra modelli della stessa sequenza."),
    "zdope": ("zDOPE", "DOPE normalizzato rispetto alle strutture native: sotto −1 il modello è simile a una "
              "struttura nativa, valori positivi indicano probabilmente un modello scarso (manuale di MODELLER)."),
    "ga341": ("GA341", "affidabilità del ripiegamento da 0 a 1 (Melo et al. 2002; John & Sali 2003): sopra 0,7 il "
              "ripiegamento è considerato affidabile. Dipende anche dall'identità con il template."),
    "molpdf": ("molpdf", "funzione obiettivo di MODELLER (violazione delle restrizioni spaziali): più bassa significa "
               "restrizioni meglio soddisfatte; non si confronta tra allineamenti diversi."),
    "grade": ("Voto BioLoom A–D", "100 − penalità Ramachandran (8 punti per punto percentuale di outlier oltre lo "
              "0,5%, al massimo 40) − penalità clash (clashscore / 1,5, al massimo 30) − penalità zDOPE (10 punti per "
              "unità sopra −1, al massimo 20). A ≥ 85, B ≥ 70, C ≥ 55, D sotto 55."),
    "rama": ("Ramachandran", "diedri φ/ψ dello scheletro (Ramachandran et al. 1963) classificati in regioni "
             "favorite e consentite calibrate sulle aree di MolProbity (Lovell et al. 2003), distinte per caso "
             "generale, glicina e prolina; la pre-prolina usa le regioni generali. Le regioni sono un'approssimazione "
             "dei contorni reali."),
    "clash": ("Clashscore", "contatti sterici gravi (sovrapposizione dei raggi di van der Waals di Probe, Word et al. "
              "1999, oltre 0,4 Å; 0,8 Å per le coppie N/O che possono formare un legame idrogeno) ogni 1000 atomi "
              "pesanti, escluse le coppie entro 3 legami covalenti e le acque. Senza idrogeni espliciti si confronta "
              "con MolProbity solo come ordine di grandezza."),
    "consensus": ("Consenso", "ogni metodo disponibile è portato su 0–100 con una scala lineare a tratti costruita "
                  "sulle soglie della letteratura (70 = soglia di affidabilità, 40 = soglia dei problemi); il consenso "
                  "è la media pesata dei soli metodi disponibili. Fasce: buona ≥ 70, intermedia 40–70, scarsa < 40. "
                  "Due metodi discordano quando uno è in fascia buona e l'altro in fascia scarsa."),
    "regions": ("Regioni problematiche", "tratti di almeno 3 residui segnalati da almeno 2 metodi, con un residuo non "
                "segnalato tollerato tra due segnalati; un tratto non attraversa catene né interruzioni della "
                "numerazione."),
    "dope_profile": ("Profilo DOPE", "energia DOPE per residuo come media mobile su 15 residui calcolata da MODELLER "
                     "(assess_dope); non ha una soglia assoluta: si segnalano i picchi oltre media + 1 deviazione "
                     "standard del profilo della stessa struttura."),
    "qmean": ("QMEAN", "QMEAN4 Z-score (Benkert et al. 2011): vicino a 0 qualità paragonabile alle strutture "
              "sperimentali di pari dimensione, −4 o meno bassa qualità. Punteggio locale per residuo da 0 a 1: sotto "
              "0,6 qualità bassa attesa (SWISS-MODEL)."),
    "prosa": ("ProSA II", "z-score dell'energia di coppia e di superficie rispetto alle strutture native di pari "
              "lunghezza (Sippl 1993; Wiederstein & Sippl 2007); energia sulla finestra di 10 residui sopra 0 = parti "
              "potenzialmente problematiche."),
    "procheck": ("PROCHECK", "percentuale di residui nelle regioni core del grafico di Ramachandran (Laskowski et al. "
                 "1993): oltre il 90% atteso per strutture a 2 Å o meglio; G-factor globale sopra −0,5 normale, sotto "
                 "−1 insolito."),
    "plddt": ("pLDDT", "confidenza locale del predittore (AlphaFold, ESMFold) da 0 a 100 (lDDT-Cα predetto; Jumper et "
              "al. 2021; Lin et al. 2023; Mariani et al. 2013), nella colonna B-factor. Fasce di AlphaFold DB "
              "(Tunyasuvunakool et al. 2021): ≥ 90 molto alta, 70–90 confidente, 50–70 bassa, < 50 molto bassa "
              "(spesso disordine). Un modello predetto è una predizione, non una struttura sperimentale."),
    "ss": ("Struttura secondaria", "assegnazione DSSP (Kabsch & Sander 1983) ridotta a 3 stati: elica (H, G, I), "
           "filamento β (E, B), coil (T, S e il resto)."),
    "disorder": ("Disordine predetto", "FoldIndex (Prilusky et al. 2005) dalla sola sequenza della struttura: "
                 "2,785·⟨H⟩ − |⟨R⟩| − 1,151 su una finestra di 51 residui, con ⟨H⟩ idropatia media di Kyte-Doolittle "
                 "su 0–1 e ⟨R⟩ carica netta media (confine carica-idropatia di Uversky et al. 2000); sotto 0 "
                 "probabilmente disordinato. Regioni di almeno 30 residui (Ward et al. 2004), con al più 3 residui sopra "
                 "0 tollerati: attese poco affidabili nel modello. Segnale informativo, fuori dal punteggio di "
                 "consenso. Metodo semplice, molto meno accurato dei predittori moderni (IUPred2A, deep learning): non "
                 "vede il disordine con composizione da proteina ripiegata né il contesto (partner, cofattori)."),
    "sasa": ("SASA relativa", "superficie accessibile al solvente (Shrake & Rupley 1973 o mkdssp) divisa per l'area "
             "massima del residuo (Tien et al. 2013); sepolto sotto il 20%."),
    "ligands": ("Ligandi e metalli", "per ogni gruppo eteroatomico: residui entro 4 Å, scontri fra atomi pesanti "
                "sotto 2,2 Å (esclusi coordinazione e legami covalenti del template); per i metalli donatori N, O, S "
                "entro la distanza attesa + 0,5 Å (Harding 2006), nella norma entro ± 0,25 Å, numero di coordinazione "
                "rispetto all'intervallo tipico e geometria dagli angoli donatore–metallo–donatore con posizioni "
                "vacanti (gRMSD oltre 13,5° da controllare, oltre 21,5° anomala; Zheng et al. 2017). Nei modelli, "
                "confronto con i residui che coordinano il metallo nel template. Il voto A–D non ne tiene conto."),
    "disulfide": ("Ponti disolfuro", "coppie di cisteine con SG–SG entro 2,0 ± 0,2 Å (legame S–S tipico circa 2,04 Å); "
                  "χ3 è il diedro CB–SG–SG–CB, di solito vicino a ±90° (Richardson 1981)."),
    "interface": ("Interfacce", "residui con ΔSASA > 1 Å² fra la catena isolata e la coppia di catene (Shrake & Rupley "
                  "1973, sonda 1,4 Å, soli amminoacidi); area d'interfaccia = metà dell'area sepolta totale "
                  "(convenzione di PISA, Krissinel & Henrick 2007). Interazioni attraverso l'interfaccia: legami "
                  "idrogeno (D…A ≤ 3,5 Å, D–H…A ≥ 120°, Baker & Hubbard 1984) e ponti salini (N–O ≤ 4,0 Å, Barlow & "
                  "Thornton 1983). Plausibilità dalla sola area: sotto 400 Å² bassa (contatti cristallini, Janin et "
                  "al. 2008), 400–600 Å² incerta, da 600 Å² compatibile (interfacce standard, Lo Conte et al. 1999): "
                  "un indicatore semplice, non una classificazione come PISA o EPPIC."),
    "complex": ("Interfacce del complesso", "ogni interfaccia del template (residui con ΔSASA > 1 Å²) si confronta con "
                "quella delle catene corrispondenti del modello attraverso l'allineamento: residui d'interfaccia del "
                "template conservati (il residuo allineato è d'interfaccia anche nel modello), area del modello e del "
                "template, e DockQ con il template come riferimento (Basu & Wallner 2016): Fnat = frazione dei "
                "contatti fra residui entro 5 Å (CAPRI, Méndez et al. 2003) presenti nel modello; iRMS = RMSD dello "
                "scheletro dei residui entro 10 Å dall'altra catena dopo la loro sovrapposizione; LRMS = RMSD dello "
                "scheletro della catena più corta dopo la sovrapposizione della più lunga; DockQ = (Fnat + 1/(1 + "
                "(iRMS/1,5)²) + 1/(1 + (LRMS/8,5)²)) / 3. Esito: ≥ 0,80 alta, ≥ 0,49 media, ≥ 0,23 accettabile, sotto "
                "non riprodotta. Misura la fedeltà all'interfaccia del template, non l'accuratezza rispetto al "
                "complesso reale. Simmetria: restrizioni di MODELLER sui C-alfa delle catene con la stessa sequenza, "
                "che rendono uguali le catene (non la disposizione, che viene dal template)."),
    "refine_energy": ("Energia del campo di forza", "energia potenziale AMBER ff14SB con solvente implicito OBC2 "
                      "(Maier et al. 2015; Onufriev et al. 2004), in kJ/mol, senza il termine delle restrizioni "
                      "(riportato a parte): prima della minimizzazione, sulla struttura con gli atomi e gli idrogeni "
                      "aggiunti da PDBFixer, e alla fine. Vale per confrontare prima e dopo sulla stessa struttura, "
                      "non fra strutture diverse; un valore iniziale molto alto indica contatti sterici gravi."),
    "refine_rmsd": ("RMSD della catena principale", "atomi N, CA, C e O in comune fra la struttura di partenza e "
                    "quella rifinita, dopo la sovrapposizione ottimale (Kabsch 1976) e senza sovrapposizione (le "
                    "restrizioni tengono le posizioni assolute); spostamento massimo con l'atomo che lo ha. Con le "
                    "restrizioni armoniche sulla catena principale resta di solito sotto 0,5 Å."),
    "refine_compare": ("Prima e dopo la rifinitura", "le stesse metriche di qualità calcolate sulla copia data a "
                       "OpenMM (stessi residui, senza ligandi né idrogeni) e sul modello rifinito; «invariato» per "
                       "differenze entro l'arrotondamento dei rapporti. La rifinitura migliora soprattutto clash e "
                       "rotameri; non corregge errori di allineamento né di ripiegamento."),
    "prediction": ("Confronto con una predizione", "il modello (riferimento) e la predizione di ESMFold (Lin et al. "
                   "2023) o di AlphaFold DB della stessa sequenza, con i residui abbinati per sequenza. TM-score "
                   "normalizzato sul modello: da 0,5 stesso ripiegamento (Xu & Zhang 2010; due strutture casuali circa "
                   "0,17, Zhang & Skolnick 2004). lDDT-Cα di ogni residuo del modello rispetto alla predizione (Mariani "
                   "et al. 2013): sotto 0,5 il residuo diverge; regioni di almeno 3 residui divergenti (uno non "
                   "divergente tollerato). La predizione è sicura in una regione se il suo pLDDT medio è almeno 70 "
                   "(fasce di AlphaFold DB, Tunyasuvunakool et al. 2021): lì una divergenza indica più probabilmente un "
                   "errore del modello (allineamento, template); dove la predizione è incerta il confronto non è "
                   "informativo. Con meno di 30 residui a predizione sicura l'esito è non conclusivo. Nel consenso, "
                   "accordo = lDDT medio sui residui a predizione sicura, scala 0,5 → 40 e 0,7 → 70, peso 0,25 (il più "
                   "basso). Una predizione non è una misura sperimentale."),
}

# mutagenesi in silico e scansione di alanina (F9.2): fonti e voci della legenda delle sezioni "Mutagenesi"
REFERENCES.update({
    "mutate_model": "Feyfant E., Sali A., Fiser A. (2007) Modeling mutations in protein structures. Protein Sci. "
                    "16:2030–2039.",
    "pace_scholtz": "Pace C.N., Scholtz J.M. (1998) A helix propensity scale based on experimental studies of peptides "
                    "and proteins. Biophys. J. 75:422–427.",
    "macarthur": "MacArthur M.W., Thornton J.M. (1991) Influence of proline residues on protein conformation. J. Mol. "
                 "Biol. 218:397–412.",
    "minor_kim": "Minor D.L. Jr., Kim P.S. (1994) Measurement of the β-sheet-forming propensities of amino acids. Nature "
                 "367:660–663.",
    "myers_pace": "Myers J.K., Pace C.N. (1996) Hydrogen bonding stabilizes globular proteins. Biophys. J. "
                  "71:2033–2039.",
    "strop_mayo": "Strop P., Mayo S.L. (2000) Contribution of surface salt bridges to protein stability. Biochemistry "
                  "39:1251–1255.",
    "chothia1974": "Chothia C. (1974) Hydrophobic bonding and accessible surface area in proteins. Nature 248:338–339.",
    "betz": "Betz S.F. (1993) Disulfide bonds and the stability of globular proteins. Protein Sci. 2:1551–1558.",
    "isom": "Isom D.G., Castañeda C.A., Cannon B.R., García-Moreno E.B. (2011) Large shifts in pKa values of lysine "
            "residues buried inside a protein. Proc. Natl. Acad. Sci. USA 108:5260–5265.",
    "blosum": "Henikoff S., Henikoff J.G. (1992) Amino acid substitution matrices from protein blocks. Proc. Natl. Acad. "
              "Sci. USA 89:10915–10919.",
    "kortemme": "Kortemme T., Baker D. (2002) A simple physical model for binding energy hot spots in protein–protein "
                "complexes. Proc. Natl. Acad. Sci. USA 99:14116–14121.",
    "bogan_thorn": "Bogan A.A., Thorn K.S. (1998) Anatomy of hot spots in protein interfaces. J. Mol. Biol. 280:1–9.",
})
LEGEND.update({
    "mutation_class": ("Effetto stimato della mutazione", "classe qualitativa da indicatori dichiarati, ognuno con un peso "
                       "(forte, moderato, informativo): probabilmente destabilizzante con un segnale forte o almeno due "
                       "moderati; incerta con un solo segnale moderato, con un residuo sepolto senza segnali o con soli "
                       "segnali di possibile stabilizzazione; probabilmente neutra senza segnali su un residuo esposto. "
                       "Non è un ΔΔG: indica la direzione probabile dell'effetto sulla stabilità."),
    "mutation_dope": ("ΔDOPE della mutazione", "DOPE del mutante meno quello del selvatico riottimizzato con lo stesso "
                      "protocollo (stessi semi, si tiene il modello migliore di 3 per ciascuno); in residui equivalenti "
                      "= ΔDOPE / (|DOPE del selvatico| / residui). Locale: coppie di atomi con almeno un atomo nei residui "
                      "entro 8 Å dai siti; fra le catene: coppie fra la catena mutata e le altre. Da 1 residuo "
                      "equivalente segnale moderato, da 2 forte."),
    "mutation_energy": ("ΔΔE con OpenMM", "(E_mutante − E_selvatico) − (E_rif,mutante − E_rif,selvatico): energie "
                        "potenziali AMBER ff14SB con solvente implicito OBC2 dopo la minimizzazione con restrizioni della "
                        "rifinitura; il riferimento (tripeptidi i−1..i+1 isolati) toglie la parte che dipende solo dalla "
                        "composizione. È un'energia senza entropia, non un'energia libera: da 50 kJ/mol segnale "
                        "moderato, da 100 forte (oltre lo scarto misurato fra due selvatici ottimizzati, fino a 36 "
                        "kJ/mol)."),
    "alascan": ("Scansione di alanina", "ogni residuo scelto diventa alanina con lo stesso protocollo della mutagenesi; "
                "glicina e prolina escluse (Kortemme & Baker 2002). Hot spot d'interfaccia probabile: residuo "
                "d'interfaccia sepolto nel complesso (RSA < 20%) con contributo al legame (ΔDOPE fra le catene ≥ 0,5 "
                "residui equivalenti o interazioni fra catene perse); possibile con il solo contributo (definizione "
                "sperimentale di hot spot: ΔΔG di legame ≥ 2 kcal/mol, Bogan & Thorn 1998)."),
})


# ---------------------------------------------------------------------------------------------- composizione
CSS = """
:root{--ink:#1f2933;--muted:#52606d;--line:#d9e2ec;--soft:#f5f7fa;--accent:#1d4e89;--ok:#1e7b4a;--warn:#b26a00;
--err:#b3261e;--A:#1e7b4a;--B:#2f7f9e;--C:#c77700;--D:#b3261e}
*{box-sizing:border-box}
html{-webkit-print-color-adjust:exact;print-color-adjust:exact}
body{margin:0;background:#fff;color:var(--ink);font:14px/1.5 system-ui,-apple-system,"Segoe UI",Roboto,
"Helvetica Neue",Arial,sans-serif}
main,header.doc,footer{max-width:960px;margin:0 auto;padding:0 24px}
header.doc{padding-top:28px;border-bottom:3px solid var(--accent);padding-bottom:14px}
.brand{font-size:12px;letter-spacing:.08em;text-transform:uppercase;color:var(--accent);font-weight:700}
h1{font-size:24px;margin:4px 0 10px;overflow-wrap:anywhere}
h2{font-size:18px;margin:28px 0 10px;padding-bottom:4px;border-bottom:1px solid var(--line);color:var(--accent)}
h3{font-size:15px;margin:18px 0 8px}
p{margin:6px 0}
dl.meta{display:grid;grid-template-columns:repeat(auto-fill,minmax(210px,1fr));gap:6px 18px;margin:0}
dl.meta div{min-width:0}
dt{font-size:11px;text-transform:uppercase;letter-spacing:.05em;color:var(--muted)}
dd{margin:0;overflow-wrap:anywhere}
nav.toc{display:flex;flex-wrap:wrap;gap:2px 14px;font-size:12px;margin:12px 0 0}
nav.toc a{color:var(--accent);text-decoration:none;white-space:nowrap}
.cards{display:grid;grid-template-columns:repeat(auto-fill,minmax(160px,1fr));gap:10px}
.card{border:1px solid var(--line);border-radius:8px;padding:10px 12px;background:var(--soft);break-inside:avoid}
.card .k{font-size:11px;text-transform:uppercase;letter-spacing:.05em;color:var(--muted)}
.card .v{font-size:22px;font-weight:700}
.card .n{font-size:12px;color:var(--muted)}
.grade{display:inline-block;min-width:34px;padding:0 8px;border-radius:6px;color:#fff;font-weight:800;text-align:center}
.grade.A{background:var(--A)}.grade.B{background:var(--B)}.grade.C{background:var(--C)}.grade.D{background:var(--D)}
.grade.big{font-size:30px;line-height:44px;min-width:52px}
table{border-collapse:collapse;width:100%;font-size:13px;margin:6px 0}
th,td{border-bottom:1px solid var(--line);padding:5px 8px;text-align:left;vertical-align:top}
th{background:var(--soft);font-size:11px;text-transform:uppercase;letter-spacing:.04em;color:var(--muted)}
td.num,th.num{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}
tr.best td{background:#e8f3ec;font-weight:600}
tr.chosen td:first-child{box-shadow:inset 3px 0 0 var(--accent)}
.tag{display:inline-block;font-size:11px;padding:0 6px;border-radius:10px;border:1px solid currentColor;
margin-left:4px;font-weight:600}
.tag.ok{color:var(--ok)}.tag.warn{color:var(--warn)}.tag.err{color:var(--err)}.tag.info{color:var(--accent)}
.note{border-left:4px solid var(--accent);background:#eef4fb;padding:8px 12px;margin:8px 0;border-radius:4px;
break-inside:avoid}
.note.warn{border-color:var(--warn);background:#fff6e6}.note.err{border-color:var(--err);background:#fdecea}
.note.ok{border-color:var(--ok);background:#e8f3ec}
pre{font:12px/1.35 ui-monospace,"SFMono-Regular",Menlo,Consolas,"Liberation Mono",monospace;margin:4px 0 10px;
white-space:pre;overflow-x:auto}
pre.aln b.id{font-weight:400;background:#d7e8f7}
.muted{color:var(--muted)}.small{font-size:12px}
.figs{display:flex;flex-wrap:wrap;gap:12px}
figure{margin:8px 0;break-inside:avoid}
figcaption{font-size:12px;color:var(--muted)}
svg{max-width:100%;height:auto}
img.shot{max-width:100%;height:auto;border:1px solid var(--line);border-radius:6px}
.swatch{display:inline-block;width:10px;height:10px;border-radius:2px;margin:0 4px 0 10px;vertical-align:middle}
ol.refs li{margin:3px 0}
dl.legend dt{font-size:13px;text-transform:none;letter-spacing:0;color:var(--ink);font-weight:700;margin-top:8px}
footer{margin-top:32px;padding-top:10px;padding-bottom:28px;border-top:1px solid var(--line);font-size:12px;
color:var(--muted)}
@page{size:A4;margin:14mm 12mm}
@media print{
body{font-size:10.5pt}
main,header.doc,footer{max-width:none;padding:0}
nav.toc{display:none}
h2{break-after:avoid;page-break-after:avoid}
h3{break-after:avoid;page-break-after:avoid}
section.page{break-before:page;page-break-before:always}
figure,.blk,.card,.note,svg,img.shot,tr{break-inside:avoid;page-break-inside:avoid}
thead{display:table-header-group}
pre{white-space:pre-wrap;overflow:visible;font-size:8.5pt}
pre.aln{white-space:pre;font-size:7.5pt}
a{color:inherit;text-decoration:none}
}
"""


class ReportStructureError(RuntimeError):
    """Errore di programmazione nella struttura del report: sezione aperta dentro un'altra, chiusa due volte, id
    ripetuto o documento finito con una sezione aperta."""


class _Doc:
    """Accumula il testo del documento e ricorda quali voci della legenda e quali fonti servono.

    L'equilibrio delle sezioni è una proprietà della classe, non di ogni funzione: `section` con una sezione ancora
    aperta, `end` senza una sezione aperta, un id ripetuto e `html` con una sezione aperta sollevano
    `ReportStructureError`. Si è scelto l'errore e non la chiusura automatica con un avviso: le sezioni dipendono solo
    dal codice (mai dai dati), quindi uno squilibrio è un errore di programmazione che il test parametrizzato su tutti i
    tipi di report (test_report.py) ferma subito; chiuderle in silenzio nasconderebbe proprio la svista (un `end`
    perso in un'unione di rami) che ha prodotto HTML con le sezioni annidate."""

    def __init__(self) -> None:
        self.parts: list[str] = []
        self.legend: set[str] = set()
        self.refs: set[str] = set()
        self.toc: list[tuple[str, str]] = []
        self.current: Optional[str] = None

    def add(self, *chunks: str) -> None:
        self.parts.extend(chunks)

    def section(self, sid: str, title: str, page_break: bool = False) -> None:
        if self.current is not None:
            raise ReportStructureError(f"sezione «{sid}» aperta mentre «{self.current}» è ancora aperta")
        if any(sid == s for s, _t in self.toc):
            raise ReportStructureError(f"sezione «{sid}» ripetuta: gli id del report devono essere univoci")
        self.toc.append((sid, title))
        self.current = sid
        self.add(f'<section id="{sid}"' + (' class="page"' if page_break else "") + f"><h2>{_e(title)}</h2>")

    def end(self) -> None:
        if self.current is None:
            raise ReportStructureError("end() senza una sezione aperta")
        self.current = None
        self.add("</section>")

    def html(self) -> str:
        """Testo del corpo; solo a sezioni tutte chiuse."""
        if self.current is not None:
            raise ReportStructureError(f"report finito con la sezione «{self.current}» ancora aperta")
        return "".join(self.parts)

    def explain(self, *keys: str) -> None:
        """Voci della legenda (chiavi di LEGEND)."""
        self.legend.update(keys)

    def cite(self, *keys: str) -> None:
        """Fonti bibliografiche (chiavi di REFERENCES)."""
        self.refs.update(keys)


def _note(text: str, tone: str = "") -> str:
    return f'<div class="note {tone}">{text}</div>'


def _card(label: str, value: str, note: str = "") -> str:
    return (f'<div class="card"><div class="k">{_e(label)}</div><div class="v">{value}</div>'
            + (f'<div class="n">{note}</div>' if note else "") + "</div>")


def _grade(letter: Any, big: bool = False) -> str:
    cls = letter if letter in ("A", "B", "C", "D") else "D"
    return f'<span class="grade {cls}{" big" if big else ""}">{_e(letter or "—")}</span>'


def _table(head: list[tuple[str, bool]], rows: list[str]) -> str:
    cols = "".join(('<th class="num">' if num else "<th>") + f"{_e(label)}</th>" for label, num in head)
    return f"<table><thead><tr>{cols}</tr></thead><tbody>{''.join(rows)}</tbody></table>"


def _td(value: str, num: bool = False) -> str:
    return f'<td class="num">{value}</td>' if num else f"<td>{value}</td>"


BAND_TONE = {"buona": "ok", "intermedia": "warn", "scarsa": "err"}
REFINEMENT_LABEL = {
    "very_fast": "rapidissimo (a.very_fast())",
    "fast": "rapido (predefiniti di AutoModel: autosched.normal, refine.very_fast)",
    "normal": "standard (autosched.normal, dinamica più lunga refine.fast)",
    "thorough": "accurato (autosched.slow, 300 iterazioni, refine.slow, 2 ripetizioni)",
}
LOOP_LEVEL_LABEL = {"very_fast": "refine.very_fast", "fast": "refine.fast", "slow": "refine.slow"}


def _raw(method: dict) -> str:
    if method.get("key") == "grade":
        return _num(method.get("raw"), 1, "/100")
    if method.get("key") == "procheck":
        return _num(method.get("raw"), 1, "%")
    return _num(method.get("raw"), 2)


# ---------------------------------------------------------------------------------------------- sezioni
def _header(data: dict, toc: list[tuple[str, str]]) -> str:
    """Intestazione: titolo del job, progetto, date, strumenti e indice delle sezioni (fuori dalla stampa)."""
    kind = data.get("kind")
    heading = {"quality": "Report dell'analisi di qualità", "refine": "Report del modello rifinito",
               "mutate": "Report della mutagenesi", "alascan": "Report della scansione di alanina"}.get(
        str(kind), "Report del modello")
    meta = [("Job", f"{_e(data.get('job_id'))} · {_e(_lookup(KIND_LABEL, kind))}"),
            ("Progetto", _e(data.get("project")) or "—")]
    if data.get("model"):
        meta.append(("Modello", _e(data.get("model"))))
    meta += [("Eseguito", _date(data.get("finished_at") or data.get("created_at"))),
             ("Report generato", _date(data.get("generated_at")))]
    tools = " · ".join(f"{_e(name)} {_e(value)}".strip() for name, value in data.get("tools") or [])
    meta.append(("Strumenti", tools or "—"))
    nav = '<nav class="toc">' + "".join(f'<a href="#{sid}">{_e(title)}</a>' for sid, title in toc) + "</nav>"
    return ('<header class="doc">' + f'<div class="brand">BioLoom · {heading}</div>'
            + f"<h1>{_e(data.get('title') or data.get('model') or 'Report')}</h1>" + '<dl class="meta">'
            + "".join(f"<div><dt>{_e(k)}</dt><dd>{v}</dd></div>" for k, v in meta) + "</dl>" + nav + "</header>")


def _summary(doc: _Doc, data: dict) -> None:
    quality, cons = data.get("quality"), data.get("consensus")
    chosen = next((m for m in data.get("models") or [] if m.get("chosen")), None) or {}
    methods = {m.get("key"): m for m in (cons or {}).get("methods") or []}
    cards = []
    if quality:
        g = quality.get("grade") or {}
        cards.append(_card("Voto BioLoom", _grade(g.get("grade"), big=True), f"punteggio {_num(g.get('score'), 1)}/100"))
        rs = (quality.get("ramachandran") or {}).get("summary") or {}
        cards.append(_card("Ramachandran", _num(rs.get("favored_pct"), 1, "%"),
                           f"favorite · {_num(rs.get('outlier_pct'), 1, '%')} outlier"))
        cards.append(_card("Clashscore", _num((quality.get("clashes") or {}).get("clashscore"), 2),
                           "contatti gravi ogni 1000 atomi"))
        doc.explain("grade", "rama", "clash")
    if cons and _isnum(cons.get("score")):
        band = cons.get("band")
        cards.append(_card("Consenso", _num(cons.get("score"), 1, "/100"),
                           f'fascia <span class="tag {_lookup(BAND_TONE, band, "")}">{_e(band)}</span> · '
                           f"{_e(cons.get('methods_used'))} metodi"))
    dope = (quality or {}).get("dope") or {}
    dope_value = dope.get("score") if _isnum(dope.get("score")) else chosen.get("dope")
    zdope = dope.get("zscore") if _isnum(dope.get("zscore")) else chosen.get("zdope")
    if _isnum(dope_value) or _isnum(zdope):
        cards.append(_card("DOPE", _num(dope_value, 0), f"zDOPE {_num(zdope, 2)}"))
        doc.explain("dope", "zdope")
        doc.cite("dope")
    ga341 = chosen.get("ga341") if _isnum(chosen.get("ga341")) else (methods.get("ga341") or {}).get("raw")
    if _isnum(ga341):
        cards.append(_card("GA341", _num(ga341, 2), "ripiegamento affidabile sopra 0,7"))
        doc.explain("ga341")
        doc.cite("ga341", "ga341_b")
    qmean = methods.get("qmean")
    if qmean:
        cards.append(_card("QMEAN4 Z-score", _num(qmean.get("raw"), 2), "−4 o meno: bassa qualità"))
    refine = data.get("refine") if isinstance(data.get("refine"), dict) else None
    if refine:  # rifinitura: energia e spostamento della catena principale
        energy = refine.get("energy") if isinstance(refine.get("energy"), dict) else {}
        rmsd = refine.get("rmsd") if isinstance(refine.get("rmsd"), dict) else {}
        cards.append(_card("Energia (kJ/mol)", _num(energy.get("after"), 0),
                           f"prima {_num(energy.get('before'), 0)} · Δ {_signed(energy.get('delta'), 0)}"))
        cards.append(_card("RMSD catena principale", _num(rmsd.get("rmsd"), 2, " Å"),
                           f"{_e(rmsd.get('atoms'))} atomi N, CA, C, O"))
        doc.explain("refine_energy", "refine_rmsd")
    cards += _mutation_cards(doc, data)  # mutagenesi e scansione di alanina (F9.2)
    if not cards:
        return
    doc.section("riepilogo", "Riepilogo")
    doc.add('<div class="cards">', *cards, "</div>")
    if not quality and data.get("kind") != "alascan":
        doc.add(_note("Analisi di qualità non calcolata per questo modello: il report riporta solo i risultati "
                      + ("della rifinitura." if data.get("kind") == "refine" else "di MODELLER."), "warn"))
    doc.end()


def _target(doc: _Doc, data: dict) -> None:
    target = data.get("target") or {}
    structure = data.get("structure")
    doc.section("bersaglio", {"quality": "Struttura analizzata", "refine": "Struttura rifinita",
                              "mutate": "Modello mutato", "alascan": "Struttura scandita"}.get(
        str(data.get("kind")), "Bersaglio"))
    chains = [c for c in str(target.get("sequence") or "").split("/") if c]
    rows = [("Nome", _e(target.get("name"))),
            ("Lunghezza", f"{sum(len(c) for c in chains)} residui" + (f" in {len(chains)} catene" if len(chains) > 1
                                                                       else ""))]
    if structure:
        rows.append(("File", _e(structure.get("file"))))
        if structure.get("title"):
            rows.append(("Titolo", _e(structure.get("title"))))
        if structure.get("method"):
            rows.append(("Metodo", _e(structure.get("method"))))
        if _isnum(structure.get("resolution")):
            rows.append(("Risoluzione", _num(structure.get("resolution"), 2, " Å")))
    if data.get("source"):
        rows.append(("Sorgente", _e(data.get("source"))))
    doc.add("<table><tbody>", *(f"<tr><th>{_e(k)}</th><td>{v}</td></tr>" for k, v in rows), "</tbody></table>")
    labels = target.get("chains") or []
    for i, seq in enumerate(chains):
        if len(chains) > 1:
            doc.add(f"<h3>Catena {_e(labels[i] if i < len(labels) else i + 1)}</h3>")
        doc.add(f'<div class="blk">{sequence_block(seq)}</div>')
    doc.end()


def _trim_text(t: dict) -> str:
    trim = t.get("trim")
    if t.get("trim_off"):
        return "usato senza ritaglio dei residui a basso pLDDT"
    if not trim:
        return "senza indicazioni sul ritaglio dei residui a basso pLDDT"
    text = f"con soglia pLDDT {_num(trim.get('threshold'), 0)}: rimossi {_e(trim.get('removed'))} residui"
    if trim.get("removed_fragments"):
        text += (f" (di cui {_e(trim.get('removed_fragments'))} in frammenti isolati di meno di "
                 f"{_e(trim.get('min_fragment') or plddt.MIN_FRAGMENT)} residui)")
    if _isnum(trim.get("kept")) and _isnum(trim.get("total")):
        text += f", restano {_e(trim.get('kept'))} di {_e(trim.get('total'))}"
    if _isnum(trim.get("mean_before")) and _isnum(trim.get("mean_after")):
        text += f"; pLDDT medio {_num(trim.get('mean_before'), 1)} → {_num(trim.get('mean_after'), 1)}"
    where = []
    ranges = trim.get("removed_ranges") or []
    for item in ranges[:12]:
        try:
            a, b, c = item
        except (TypeError, ValueError):
            continue
        where.append(f"{a}–{b}:{c}" if a != b else f"{a}:{c}")
    if where:
        text += f" (tolti {_e(', '.join(where))}{' …' if len(ranges) > 12 else ''})"
    return text


def _templates(doc: _Doc, data: dict) -> None:
    templates = data.get("templates") or []
    search = data.get("search")
    if not templates and not search:
        return
    doc.section("template", "Template")
    rows = []
    for t in templates:
        chains = ", ".join(str(c) for c in t.get("chains") or []) or "—"
        span = f"{_e(t.get('first'))}–{_e(t.get('last'))}" if t.get("first") or t.get("last") else "—"
        desc = [str(x) for x in (t.get("title"), t.get("method")) if x]
        if _isnum(t.get("resolution")):
            desc.append(f"{_num(t.get('resolution'), 2)} Å")
        source = _e(t.get("source") or "—")
        if t.get("predicted"):
            source += ' <span class="tag warn">modello predetto</span>'
        if desc:
            source += f'<div class="small muted">{_e(" · ".join(desc))}</div>'
        name = f"<b>{_e(t.get('code') or t.get('id'))}</b>"
        if t.get("id") and t.get("id") != t.get("code"):
            name += f'<div class="small muted">{_e(t.get("id"))}</div>'
        rows.append("<tr>" + _td(name) + _td(_e(chains)) + _td(span) + _td(source)
                    + _td(_num(t.get("identity"), 1, "%"), True) + _td(_num(t.get("coverage"), 1, "%"), True) + "</tr>")
    if rows:
        doc.add(_table([("Template", False), ("Catene", False), ("Intervallo", False), ("Sorgente", False),
                        ("Identità", True), ("Copertura", True)], rows))
        doc.explain("identity")
        doc.cite("rost")
    for t in templates:
        if not t.get("predicted"):
            continue
        mean = f" pLDDT medio del modello {_num(t.get('plddt_mean'), 1)}." if _isnum(t.get("plddt_mean")) else ""
        name = str(t.get("id") or t.get("code") or "")
        doc.add(_note(f"<b>{_e(t.get('code') or t.get('id'))}: {_e(plddt.predicted_text(t.get('predictor'), name))}, "
                      f"non una struttura sperimentale.</b>{mean} Template {_trim_text(t)}. I residui tolti sono "
                      "costruiti senza vincoli dal template.", "warn"))
        doc.explain("plddt")
        doc.cite(*(("esmfold",) if t.get("predictor") == "esmfold" else ("alphafold", "afdb")), "lddt")
    if search:
        engine = search.get("engine") or "—"
        if _isnum(search.get("identity")):
            text = (f"Template scelto dalla ricerca ({_e(engine)}): identità {_num(search.get('identity'), 1, '%')}, "
                    f"copertura {_num(search.get('coverage'), 1, '%')}, E-value {_sci(search.get('evalue'))}.")
            if search.get("twilight"):
                text += " <b>Zona crepuscolare</b>: identità o copertura sotto le soglie della pipeline."
            doc.add(_note(text, "warn" if search.get("twilight") else ""))
        else:
            doc.add(f'<p class="small muted">Template: {_e(engine)}.</p>')
    doc.end()


def _alignment(doc: _Doc, data: dict) -> None:
    text = data.get("alignment")
    entries = sequences.parse_pir(text) if text else []
    if not entries:
        return
    doc.section("allineamento", "Allineamento (PIR)", page_break=True)
    doc.add('<p class="small muted">Blocchi di 60 colonne con il righello delle colonne; i numeri indicano il primo e '
            "l'ultimo residuo della riga nella numerazione della voce; in azzurro i residui del template identici al "
            "bersaglio. Riga di conservazione: * identico, : gruppo forte, . gruppo debole.</p>")
    heads = []
    for e in entries:
        kind = "bersaglio" if e.kind == "sequence" else "template"
        fields = ":".join(e.fields[:4]) if e.fields else ""
        heads.append("<tr>" + _td(f"<b>{_e(e.code)}</b>") + _td(kind) + _td(_e(fields.strip(": ")) or "—")
                     + _td(str(_letters(e.sequence)), True) + "</tr>")
    doc.add(_table([("Voce", False), ("Tipo", False), ("Intervallo (PIR)", False), ("Residui", True)], heads))
    doc.add(alignment_blocks(entries))
    doc.end()


def _options(doc: _Doc, data: dict) -> None:
    opts, loop, pipe = data.get("options"), data.get("loop"), data.get("pipeline")
    if not opts and not loop:
        return
    doc.section("parametri", "Parametri di MODELLER")
    rows: list[tuple[str, str]] = []
    if loop:
        rows += [("Protocollo", "DOPELoopModel: la regione indicata viene ricostruita, il resto del modello resta "
                                "fisso; modello migliore = DOPE minimo"),
                 ("Struttura di partenza", _e(loop.get("source"))),
                 ("Regione", f"{_e(loop.get('start'))} – {_e(loop.get('end'))}"),
                 ("Modelli di loop", _e(loop.get("models"))),
                 ("Dinamica", _e(_lookup(LOOP_LEVEL_LABEL, loop.get("level"))))]
    if opts:
        rows += [("Protocollo", "AutoModel" + (" + DOPELoopModel" if opts.get("loop_refinement") else "")
                  + "; valutazione DOPE, GA341 e DOPE normalizzato; modello migliore = DOPE minimo"),
                 ("Modelli", _e(opts.get("models"))),
                 ("Raffinamento", _e(_lookup(REFINEMENT_LABEL, opts.get("refinement")))),
                 ("Seme casuale", _e(opts.get("seed"))),
                 ("Ligandi (HETATM)", _ligand_option(data, opts))]
        if opts.get("loop_refinement"):
            regions = ", ".join(f"{r[0]}–{r[1]}" for r in opts.get("loop_regions") or []
                                if isinstance(r, (list, tuple)) and len(r) == 2)
            rows += [("Modelli di loop per modello", _e(opts.get("loop_models"))),
                     ("Dinamica dei loop", _e(_lookup(LOOP_LEVEL_LABEL, opts.get("loop_level")))),
                     ("Regioni di loop", _e(regions) or "gap dell'allineamento")]
        restraints = []
        for r in opts.get("restraints") or []:
            if not isinstance(r, dict):
                continue
            if r.get("type") == "distance":
                restraints.append(f"distanza {r.get('atom1')} – {r.get('atom2')}: {_num(r.get('mean'), 2)} ± "
                                  f"{_num(r.get('stdev'), 2)} Å")
            else:
                label = "α-elica" if r.get("type") == "helix" else "filamento β"
                restraints.append(f"{label} {r.get('start')} – {r.get('end')}")
        rows.append(("Vincoli aggiuntivi", _e("; ".join(restraints)) or "nessuno"))
        if opts.get("plddt_trim") is not None:
            rows.append(("Ritaglio pLDDT (solo template predetti)",
                         f"sì, soglia {_num(opts.get('plddt_min'), 0)}" if opts.get("plddt_trim") else "no"))
        if opts.get("symmetry") or isinstance(data.get("complex"), dict):  # bersaglio a più catene (F5.3)
            rows.append(("Simmetria fra catene", f"sì, C-alfa delle catene con la stessa sequenza, peso "
                                                 f"{_num(opts.get('symmetry_weight'), 1)}" if opts.get("symmetry")
                         else "no"))
    if pipe:
        auto = pipe.get("template_mode") != "fixed"
        tw = pipe.get("twilight") if isinstance(pipe.get("twilight"), dict) else {}
        choice = "ricerca automatica" if auto else "template fisso"
        if pipe.get("template_mode") == "multi":
            choice = f"più template complementari (al massimo {_e(pipe.get('max_templates') or 3)})"
        rows += [("Scelta del template", choice),
                 ("Motore di ricerca", _e(pipe.get("engine")) if auto else "—"),
                 ("E-value massimo", _sci(pipe.get("evalue")) if auto else "—"),
                 ("Zona crepuscolare", f"identità < {_num(tw.get('identity'), 0, '%')} o copertura < "
                                       f"{_num(tw.get('coverage'), 0, '%')}"
                                       + (" (elementi filtrati)" if tw.get("filter") else " (solo segnalata)"))]
    doc.add("<table><tbody>", *(f"<tr><th>{_e(k)}</th><td>{v}</td></tr>" for k, v in rows), "</tbody></table>")
    doc.cite("modeller")
    doc.end()


def _models(doc: _Doc, data: dict) -> None:
    models = data.get("models") or []
    if not models:
        return
    doc.section("modelli", "Modelli")
    rows = []
    for m in models:
        cls = " ".join(c for c, on in (("best", m.get("best")), ("chosen", m.get("chosen"))) if on)
        name = f"<b>{_e(m.get('file'))}</b>"
        if m.get("best"):
            name += ' <span class="tag ok">migliore</span>'
        if m.get("chosen"):
            name += ' <span class="tag info">in questo report</span>'
        if m.get("kind") == "loop":
            name += ' <span class="tag info">loop</span>'
        if m.get("failure"):
            name += f'<div class="small" style="color:#b3261e">{_e(m.get("failure"))}</div>'
        grade = f"{_grade(m.get('grade'))} {_num(m.get('grade_score'), 1)}" if m.get("grade") else "—"
        rows.append(f'<tr class="{cls}">' + _td(name) + _td(_num(m.get("dope"), 1), True)
                    + _td(_num(m.get("zdope"), 2), True) + _td(_num(m.get("ga341"), 2), True)
                    + _td(_num(m.get("molpdf"), 1), True) + _td(grade) + "</tr>")
    doc.add(_table([("Modello", False), ("DOPE", True), ("zDOPE", True), ("GA341", True), ("molpdf", True),
                    ("Voto", False)], rows))
    doc.add('<p class="small muted">Il modello migliore ha il DOPE più basso; il voto c\'è solo per i modelli '
            "analizzati.</p>")
    doc.explain("dope", "zdope", "molpdf", "grade")
    doc.cite("modeller", "dope")
    if any(_isnum(m.get("ga341")) for m in models):
        doc.explain("ga341")
        doc.cite("ga341", "ga341_b")
    doc.end()


def _grade_block(doc: _Doc, quality: dict) -> None:
    g = quality.get("grade") or {}
    pen = g.get("penalties") or {}
    doc.add('<h3 id="voto">Voto</h3>', '<div class="cards">',
            _card("Voto BioLoom", _grade(g.get("grade"), big=True), f"punteggio {_num(g.get('score'), 1)}/100"),
            _card("Penalità Ramachandran", _num(pen.get("ramachandran"), 1), "8 per punto % di outlier oltre 0,5%"),
            _card("Penalità clash", _num(pen.get("clashes"), 1), "clashscore / 1,5"),
            _card("Penalità zDOPE", _num(pen.get("dope"), 1), "10 per unità sopra −1"), "</div>")
    doc.explain("grade")


def _consensus_block(doc: _Doc, cons: Optional[dict]) -> None:
    if not cons:
        return
    doc.add('<h3 id="consenso">Consenso tra i metodi</h3>')
    band = cons.get("band")
    doc.add(f"<p>Punteggio di consenso <b>{_num(cons.get('score'), 1)}/100</b>, fascia "
            f'<span class="tag {_lookup(BAND_TONE, band, "")}">{_e(band or "—")}</span>, '
            f"{_e(cons.get('methods_used'))} metodi su {_e(cons.get('methods_total'))}.</p>")
    rows = []
    for m in cons.get("methods") or []:
        label = f"<b>{_e(m.get('label'))}</b>" + f'<div class="small muted">{_e(m.get("measures"))}</div>'
        rows.append("<tr>" + _td(label) + _td(_raw(m), True) + _td(_num(m.get("normalized"), 1), True)
                    + _td(_num(m.get("weight"), 2), True)
                    + _td(f'<span class="tag {_lookup(BAND_TONE, m.get("band"), "")}">{_e(m.get("band"))}</span>') + "</tr>")
    if rows:
        doc.add(_table([("Metodo", False), ("Valore", True), ("Normalizzato (0–100)", True), ("Peso", True),
                        ("Fascia", False)], rows))
    missing = cons.get("missing") or []
    if missing:
        doc.add('<p class="small muted">Non disponibili: '
                + "; ".join(f"{_e(m.get('label'))} ({_e(m.get('reason'))})" for m in missing) + ".</p>")
    disc = cons.get("discordances") or []
    if disc:
        doc.add("<h3>Discordanze</h3>", *(_note(_e(d.get("text")), "warn") for d in disc))
    elif (cons.get("methods_used") or 0) > 1:
        doc.add(_note("I metodi disponibili concordano: nessuno è in fascia buona mentre un altro è in fascia scarsa.",
                      "ok"))
    regions = cons.get("regions") or []
    doc.add("<h3>Regioni problematiche</h3>")
    if regions:
        doc.add("<ul>", *(f"<li>{_e(r.get('text'))}: {_e(r.get('flagged'))} residui segnalati su "
                          f"{_e(r.get('length'))}</li>" for r in regions), "</ul>")
    else:
        doc.add('<p class="small muted">Nessun tratto di almeno 3 residui segnalato da almeno 2 metodi.</p>')
    dis = cons.get("disorder") if isinstance(cons.get("disorder"), dict) else {}
    if dis.get("regions"):  # segnale informativo (F5.5): spiega le regioni problematiche, non penalizza
        doc.add(_note(_e(dis.get("text")), "warn"))
        doc.explain("disorder")
        doc.cite("foldindex", "uversky", "kyte", "ward")
    pred = cons.get("predictor") if isinstance(cons.get("predictor"), dict) else {}
    if pred:  # accordo con una predizione esterna (F9.3), solo se il confronto è stato eseguito
        doc.add(_note(_e(pred.get("text")), _lookup(PREDICTION_TONE, pred.get("status"), "")))
        doc.explain("prediction")
        doc.cite("lddt", "afdb")
    signals = ((cons.get("per_residue") or {}).get("signals")) or []
    if signals:
        doc.add(_table([("Segnale per residuo", False), ("Regola", False), ("Residui segnalati", True)],
                       ["<tr>" + _td(f"<b>{_e(s.get('label'))}</b>") + _td(_e(s.get("rule")))
                        + _td(_e(s.get("flagged")), True) + "</tr>" for s in signals]))
    for w in cons.get("warnings") or []:
        doc.add(f'<p class="small muted">{_e(w)}</p>')
    doc.explain("consensus", "regions")
    keys = {m.get("key") for m in cons.get("methods") or []}
    for key, legend, refs in (("zdope", "zdope", ("dope",)), ("ga341", "ga341", ("ga341", "ga341_b")),
                              ("qmean", "qmean", ("qmean",)), ("prosa", "prosa", ("prosa", "prosa_b")),
                              ("procheck", "procheck", ("procheck",))):
        if key in keys:
            doc.explain(legend)
            doc.cite(*refs)


def _rama_block(doc: _Doc, quality: dict) -> None:
    rama = quality.get("ramachandran") or {}
    rs = rama.get("summary") or {}
    doc.add('<h3 id="ramachandran">Ramachandran</h3>',
            f"<p>{_e(rs.get('total'))} residui con φ e ψ: {_num(rs.get('favored_pct'), 1, '%')} in regioni favorite, "
            f"{_num(rs.get('allowed_pct'), 1, '%')} consentite, {_num(rs.get('outlier_pct'), 1, '%')} outlier"
            + (f"; {_e(rs.get('cis_non_proline'))} legami peptidici cis non prolinici" if rs.get("cis_non_proline")
               else "") + ".</p>")
    points = [p for p in rama.get("points") or [] if isinstance(p, dict)]
    panels = [p for p, _label in RAMA_PANELS if p == "general" or any(_rama_panel(x) == p for x in points)]
    doc.add('<div class="figs">', *(f"<figure>{rama_svg(rama, p)}</figure>" for p in panels), "</div>")
    doc.add('<p class="small muted">'
            f'<span class="swatch" style="background:{REGION_FILL["favored"]}"></span>regione favorita'
            f'<span class="swatch" style="background:{REGION_FILL["allowed"]}"></span>regione consentita'
            f'<span class="swatch" style="background:{DISALLOWED_FILL};box-shadow:inset 0 0 0 1px #9aa5b1"></span>'
            'non consentita'
            f'<span class="swatch" style="background:{POINT_FILL["favored"]};border-radius:50%"></span>residuo'
            f'<b style="color:{OUTLIER_LABEL};margin-left:10px">ABC A1</b> outlier (etichettato in rosso)</p>')
    outliers = [p for p in points if p.get("region") == "outlier"]
    if outliers:
        doc.add("<p>Outlier: " + ", ".join(f"{_e(_residue_label(p))} ({_num(p.get('phi'), 0)}°, "
                                            f"{_num(p.get('psi'), 0)}°)" for p in outliers) + ".</p>")
    else:
        doc.add(_note("Nessun residuo in regione non consentita.", "ok"))
    doc.explain("rama")
    doc.cite("ramachandran", "lovell")


def _profiles_block(doc: _Doc, quality: dict, cons: Optional[dict]) -> None:
    residues = [r for r in quality.get("residues") or [] if isinstance(r, dict)]
    per = (cons or {}).get("per_residue") or {}
    values = per.get("values") or {}
    aligned = len(per.get("counts") or []) == len(residues)
    figs = []
    dope_res, dope_values = residues, [r.get("dope") for r in residues]
    if not any(_isnum(v) for v in dope_values):  # profilo non allineabile ai residui: per indice, come nell'interfaccia
        profile = [p for p in ((quality.get("dope") or {}).get("profile")) or [] if isinstance(p, dict)]
        dope_res = [{"chain": "", "resnum": str(p.get("index")), "resname": p.get("resname")} for p in profile]
        dope_values = [p.get("energy") for p in profile]
    if sum(1 for v in dope_values if _isnum(v)) >= 2:
        threshold = per.get("dope_threshold") if aligned and dope_res is residues else \
            consensus_mod.dope_threshold(dope_values)
        figs.append(profile_svg(dope_res, dope_values, title="Energia DOPE per residuo (media mobile di MODELLER)",
                                threshold=threshold, threshold_label="media + 1 DS", digits=3))
        doc.explain("dope_profile")
        doc.cite("dope")
    if aligned and values.get("qmean"):
        figs.append(profile_svg(residues, values["qmean"], title="QMEAN locale per residuo", threshold=0.6,
                                threshold_label="0,6", flag_above=False, y_range=(0.0, 1.0)))
        doc.explain("qmean")
        doc.cite("qmean")
    if aligned and values.get("prosa"):
        figs.append(profile_svg(residues, values["prosa"], title="Energia ProSA (finestra di 10 residui)",
                                threshold=0.0, threshold_label="0"))
        doc.explain("prosa")
        doc.cite("prosa", "prosa_b")
    if ((quality.get("structure") or {}).get("bfactor_kind")) == "plddt":
        esm = (quality.get("structure") or {}).get("predictor") == "esmfold"
        figs.append(profile_svg(residues, [r.get("bfactor") for r in residues],
                                title=f"Confidenza pLDDT ({'ESMFold' if esm else 'AlphaFold'})",
                                y_range=(0.0, 100.0), digits=0, guides=((90.0, "90"), (70.0, "70"), (50.0, "50"))))
        doc.explain("plddt")
        doc.cite(*(("esmfold",) if esm else ("alphafold", "afdb")), "lddt")
    dis = (cons or {}).get("disorder") if isinstance((cons or {}).get("disorder"), dict) else {}
    scores = dis.get("scores") or []
    disorder_fig = ""
    if len(scores) == len(residues) and sum(1 for v in scores if _isnum(v)) >= 2:  # accanto ai profili (F5.5)
        disorder_fig = disorder_svg(residues, scores, dis.get("flags") or [])
        figs.append(disorder_fig)
        doc.explain("disorder")
        doc.cite("foldindex", "uversky", "kyte", "ward")
    figs = [f for f in figs if f]
    doc.add('<h3 id="profili">Profili per residuo</h3>')
    if figs:
        doc.add(*(f"<figure>{f}</figure>" for f in figs))
        doc.add('<p class="small muted">Linea rossa tratteggiata: soglia; punti rossi: residui oltre la soglia. La linea '
                "si interrompe tra catene e dove la numerazione salta."
                + (" Disordine predetto: sfondo rosa = regione disordinata lunga predetta dalla sequenza (attesa poco "
                   "affidabile nel modello)." if disorder_fig else "") + "</p>")
    else:
        doc.add('<p class="small muted">Nessun profilo per residuo disponibile (DOPE non calcolato).</p>')


def _secondary_block(doc: _Doc, quality: dict) -> None:
    ss = quality.get("secondary_structure") or {}
    sasa = quality.get("sasa") or {}
    residues = [r for r in quality.get("residues") or [] if isinstance(r, dict)]
    doc.add('<h3 id="struttura-secondaria">Struttura secondaria</h3>',
            f"<p>{_e(ss.get('method'))}: elica {_num(ss.get('helix_pct'), 1, '%')}, filamento β "
            f"{_num(ss.get('strand_pct'), 1, '%')}, coil {_num(ss.get('coil_pct'), 1, '%')}.</p>")
    strip = ss_strip_svg(residues)
    if strip:
        doc.add(f"<figure>{strip}<figcaption>"
                + "".join(f'<span class="swatch" style="background:{SS_FILL[k]}"></span>{_e(v)}'
                          for k, v in SS_LABEL.items()) + "</figcaption></figure>")
    if _isnum(sasa.get("mean_rsa")):
        doc.add(f'<p class="small">Superficie ({_e(sasa.get("method"))}): RSA media '
                f'{_num(sasa["mean_rsa"] * 100, 0, "%")}, {_num(sasa.get("buried_pct"), 1, "%")} dei residui '
                "sepolti.</p>")
        doc.explain("sasa")
        doc.cite("sasa", "tien")
    doc.explain("ss")
    doc.cite("dssp")


def _clash_block(doc: _Doc, quality: dict, limit: int = 20) -> None:
    clash = quality.get("clashes") or {}
    listed = [c for c in clash.get("clashes") or [] if isinstance(c, dict)]
    doc.add('<h3 id="clash">Contatti sterici</h3>',
            f"<p>Clashscore {_num(clash.get('clashscore'), 2)}: {_e(clash.get('count'))} contatti gravi su "
            f"{_e(clash.get('atoms'))} atomi pesanti.</p>")
    if listed:
        rows = ["<tr>" + _td(_e(c.get("atom1"))) + _td(_e(c.get("atom2"))) + _td(_num(c.get("distance"), 2), True)
                + _td(_num(c.get("overlap"), 2), True) + "</tr>" for c in listed[:limit]]
        doc.add(_table([("Atomo 1", False), ("Atomo 2", False), ("Distanza (Å)", True), ("Sovrapposizione (Å)", True)],
                       rows))
        total = clash.get("count") if _isnum(clash.get("count")) else len(listed)
        if total > min(limit, len(listed)):
            doc.add(f'<p class="small muted">Mostrati i {min(limit, len(listed))} contatti con la sovrapposizione '
                    f"maggiore su {_e(total)}.</p>")
    else:
        doc.add(_note("Nessun contatto sterico grave (sovrapposizione oltre 0,4 Å).", "ok"))
    doc.explain("clash")
    doc.cite("probe")


def _external_block(doc: _Doc, quality: dict) -> None:
    pc, pr = quality.get("procheck"), quality.get("prosa")
    if not isinstance(pc, dict) and not isinstance(pr, dict):
        return
    doc.add('<h3 id="validazione-esterna">PROCHECK e ProSA II</h3>')
    if isinstance(pc, dict):
        rama = pc.get("ramachandran") or {}
        g = pc.get("g_factors") or {}
        doc.add(f"<p><b>PROCHECK</b>: regioni core {_num(rama.get('core'), 1, '%')}, consentite "
                f"{_num(rama.get('allowed'), 1, '%')}, generose {_num(rama.get('generous'), 1, '%')}, non consentite "
                f"{_num(rama.get('disallowed'), 1, '%')}; G-factor globale {_num(g.get('overall'), 2)} (diedri "
                f"{_num(g.get('dihedrals'), 2)}, covalenti {_num(g.get('covalent'), 2)}).</p>")
        doc.explain("procheck")
        doc.cite("procheck")
    if isinstance(pr, dict):
        doc.add(f"<p><b>ProSA II</b>: z-score {_num(pr.get('zscore'), 2)} (coppie {_num(pr.get('zscore_pair'), 2)}, "
                f"superficie {_num(pr.get('zscore_surface'), 2)}).</p>")
        doc.explain("prosa")
        doc.cite("prosa", "prosa_b")


def _quality(doc: _Doc, data: dict) -> None:
    quality = data.get("quality")
    title = {"quality": "Qualità della struttura", "refine": "Qualità del modello rifinito",
             "mutate": "Qualità del modello mutato"}.get(str(data.get("kind")), "Qualità del modello scelto")
    doc.section("qualita", title, page_break=True)
    image = data.get("image_png")
    if image:
        width, height = png_size(image)
        doc.add(f'<figure><img class="shot" alt="Vista 3D" width="{width}" height="{height}" '
                f'src="data:image/png;base64,{base64.b64encode(image).decode("ascii")}">'
                "<figcaption>Vista 3D al momento della generazione del report (viewer di BioLoom).</figcaption>"
                "</figure>")
    if not quality:
        doc.add(_note("Analisi di qualità non calcolata per questo modello: avviala dalla pagina Qualità per avere "
                      "voto, consenso, Ramachandran e profili nel report.", "warn"))
        doc.end()
        return
    if (quality.get("structure") or {}).get("bfactor_kind") == "plddt":
        esm = (quality.get("structure") or {}).get("predictor") == "esmfold"  # F9.3: predizione di ESMFold
        doc.add(_note(("Predizione di ESMFold" if esm else "Modello AlphaFold") + ": la colonna B-factor contiene il "
                      "pLDDT (confidenza locale), non un fattore di temperatura."))
        doc.explain("plddt")
        doc.cite("esmfold" if esm else "alphafold", "afdb", "lddt")
    _grade_block(doc, quality)
    _consensus_block(doc, data.get("consensus"))
    _rama_block(doc, quality)
    _profiles_block(doc, quality, data.get("consensus"))
    _secondary_block(doc, quality)
    _clash_block(doc, quality)
    _external_block(doc, quality)
    doc.end()


LIGAND_TONE = {"buono": "ok", "da controllare": "warn", "problematico": "err"}


def _ligand_data(data: dict) -> Optional[dict]:
    """Verifica dei ligandi: quella del rapporto di qualità, altrimenti quella del risultato del job (modello migliore
    senza analisi di qualità)."""
    lig = ((data.get("quality") or {}).get("ligands")) or data.get("ligands")
    return lig if isinstance(lig, dict) and lig.get("groups") else None


def _ligand_option(data: dict, opts: dict) -> str:
    """Riga "Ligandi (HETATM)" dei parametri: gruppi trasferiti e da quale template, altrimenti sì/no."""
    groups = [g for g in (_ligand_data(data) or {}).get("groups") or [] if isinstance(g.get("template"), dict)]
    if groups:
        return _e(", ".join(f"{g['template'].get('group')} da {g['template'].get('code')}" for g in groups))
    if isinstance(opts.get("ligands"), list) and opts.get("ligands"):
        return _e(", ".join(f"{g.get('resname')} {g.get('chain')}{g.get('resnum')}" for g in opts["ligands"]
                            if isinstance(g, dict)))
    return "sì (additivi esclusi)" if opts.get("hetatm") else "no"


def _donor_text(d: dict) -> str:
    text = f"{_e(d.get('label'))} {_num(d.get('distance'), 2, ' Å')}"
    if _isnum(d.get("expected")):
        text += f' <span class="muted">(attesa {_num(d.get("expected"), 2)})</span>'
    if d.get("status") not in (None, "nella norma"):
        text += f' <span class="tag warn">{_e(d.get("status"))}</span>'
    return text


def _ligands(doc: _Doc, data: dict) -> None:
    """Sezione "Ligandi e metalli", solo se la struttura ha gruppi eteroatomici non d'acqua."""
    lig = _ligand_data(data)
    if lig is None:
        return
    groups = [g for g in lig.get("groups") or [] if isinstance(g, dict)]
    counts = lig.get("counts") if isinstance(lig.get("counts"), dict) else {}
    doc.section("ligandi", "Ligandi e metalli")
    doc.add(f"<p>{len(groups)} {'gruppo eteroatomico' if len(groups) == 1 else 'gruppi eteroatomici'} (acque "
            f"escluse): {_e(counts.get('buono', 0))} buoni, {_e(counts.get('da controllare', 0))} da controllare, "
            f"{_e(counts.get('problematico', 0))} problematici"
            + (", con il confronto dei siti del template" if lig.get("template_compared") else "") + ".</p>")
    rows = []
    for g in groups:
        name = f"<b>{_e(g.get('resname'))} {_e(g.get('chain'))}{_e(g.get('resnum'))}</b>"
        if g.get("description"):
            name += f'<div class="small muted">{_e(g.get("description"))}</div>'
        if isinstance(g.get("template"), dict):
            name += (f'<div class="small muted">da {_e(g["template"].get("code"))}: '
                     f'{_e(g["template"].get("group"))}</div>')
        coord = []
        for m in g.get("metals") or []:
            if not isinstance(m, dict):
                continue
            geo = m.get("geometry") if isinstance(m.get("geometry"), dict) else None
            line = f"<b>{_e(m.get('symbol'))}</b>: {_e(m.get('coordination_number'))} donatori"
            if geo:
                line += f", {_e(geo.get('name'))} (gRMSD {_num(geo.get('grmsd'), 1)}°)"
            donors = [d for d in m.get("donors") or [] if isinstance(d, dict)]
            if donors:
                line += "<br>" + "; ".join(_donor_text(d) for d in donors)
            site = m.get("template_site") if isinstance(m.get("template_site"), dict) else None
            if site:
                line += (f'<br><span class="small">Sito del template: {_e(site.get("conserved"))} su '
                         f'{_e(site.get("protein_donors"))} donatori della proteina conservati'
                         + (f", {_e(site.get('waters'))} acque non trasferite" if site.get("waters") else "")
                         + "</span>")
            coord.append(line)
        contacts = [c for c in g.get("contacts") or [] if isinstance(c, dict)]
        near = ", ".join(f"{_e(str(c.get('resname') or '').capitalize())} {_e(c.get('chain'))}{_e(c.get('resnum'))}"
                         for c in contacts[:6]) + (" …" if len(contacts) > 6 else "")
        status = g.get("status")
        rows.append("<tr>" + _td(name) + _td(_e(g.get("label")))
                    + _td(f'<span class="tag {_lookup(LIGAND_TONE, status, "")}">{_e(status)}</span>')
                    + _td("<br>".join(coord) or '<span class="muted">—</span>')
                    + _td(f"{len(contacts)}" + (f'<div class="small muted">{near}</div>' if near else ""))
                    + _td(_e(g.get("summary"))) + "</tr>")
    doc.add(_table([("Gruppo", False), ("Classe", False), ("Esito", False), ("Coordinazione", False),
                    ("Residui entro 4 Å", False), ("Motivazione", False)], rows))
    for note in lig.get("notes") or []:
        doc.add(f'<p class="small muted">{_e(note)}</p>')
    doc.explain("ligands")
    doc.cite("harding", "cmm", "kuppuraj")
    if any(g.get("template") for g in groups):
        doc.cite("biolip")
    doc.end()


PLAUSIBILITY_TONE ={"bassa": "err", "incerta": "warn", "compatibile": "ok"}


def _structural(doc: _Doc, data: dict) -> None:
    """Ponti disolfuro e interfacce fra catene dall'analisi strutturale (F7) della stessa struttura, se ce ne sono."""
    info = data.get("analysis")
    if not isinstance(info, dict):
        return
    ss = [d for d in info.get("disulfides") or [] if isinstance(d, dict)]
    pairs = [p for p in info.get("interfaces") or [] if isinstance(p, dict)]
    if not ss and not pairs:
        return
    doc.section("analisi", "Analisi strutturale")
    doc.add(f'<p class="small muted">Dall\'analisi strutturale {_e(info.get("job_id"))} del '
            f"{_date(info.get('finished_at'))} sulla stessa struttura.</p>")
    if ss:
        rows = ["<tr>" + _td(_e(_residue_label(d.get("a") or {}))) + _td(_e(_residue_label(d.get("b") or {})))
                + _td(_num(d.get("distance"), 2), True) + _td(_num(d.get("ca_distance"), 2), True)
                + _td(_num(d.get("chi3"), 0, "°"), True) + "</tr>" for d in ss]
        doc.add('<h3 id="disolfuri">Ponti disolfuro</h3>',
                _table([("Cisteina", False), ("Cisteina", False), ("SG–SG (Å)", True), ("Cα–Cα (Å)", True),
                        ("χ3", True)], rows))
        doc.explain("disulfide")
        doc.cite("richardson")
    if pairs:
        rows = []
        for p in pairs:
            chains = [str(c) for c in p.get("chains") or []][:2]
            counts = p.get("counts") if isinstance(p.get("counts"), dict) else {}
            inter = p.get("interactions") if isinstance(p.get("interactions"), dict) else {}
            level = (p.get("plausibility") or {}).get("level")
            rows.append("<tr>" + _td(_e(" – ".join(chains))) + _td(_num(p.get("area"), 0), True)
                        + _td(_e(" / ".join(str(counts.get(c, "—")) for c in chains)), True)
                        + _td(_e(inter.get("hbonds", "—")), True) + _td(_e(inter.get("salt_bridges", "—")), True)
                        + _td(_num(100 * p["apolar_fraction"], 0, "%") if _isnum(p.get("apolar_fraction")) else "—",
                              True)
                        + _td(f'<span class="tag {_lookup(PLAUSIBILITY_TONE, level, "")}">{_e(level or "—")}</span>')
                        + "</tr>")
        doc.add('<h3 id="interfacce">Interfacce fra catene</h3>',
                _table([("Catene", False), ("Area (Å²)", True), ("Residui", True), ("Legami H", True),
                        ("Ponti salini", True), ("Area apolare", True), ("Plausibilità", False)], rows))
        for p in pairs:
            residues = p.get("residues") if isinstance(p.get("residues"), dict) else {}
            parts = []
            for chain in [str(c) for c in p.get("chains") or []][:2]:
                names = ", ".join(_residue_label(r) for r in residues.get(chain) or [] if isinstance(r, dict))
                parts.append(f"<b>{_e(chain)}</b>: {_e(names) or '—'}")
            text = (p.get("plausibility") or {}).get("text")
            doc.add(f'<p class="small">Residui d\'interfaccia (ΔSASA &gt; 1 Å²) — {"; ".join(parts)}.'
                    + (f" Plausibilità: {_e(text)}." if text else "") + "</p>")
        limits = next(((p.get("plausibility") or {}).get("limits") for p in pairs if p.get("plausibility")), None)
        if limits:
            doc.add(_note(_e(limits), "warn"))
        doc.explain("interface")
        doc.cite("sasa", "krissinel", "janin2008", "loconte", "baker", "barlow")
    doc.end()


COMPLEX_TONE = {"alta": "ok", "media": "ok", "accettabile": "warn", "non riprodotta": "err", "nuova": "warn",
                "non valutabile": "warn"}


def _complex(doc: _Doc, data: dict) -> None:
    """Interfacce del complesso (F5.3): catene del modello, simmetria e confronto di ogni interfaccia con quella del
    template (residui conservati, aree, DockQ con esito motivato). Solo per i modelli a più catene."""
    info = data.get("complex")
    if not isinstance(info, dict):
        return
    doc.section("complesso", "Interfacce del complesso")
    chains = [c for c in info.get("chains") or [] if isinstance(c, dict)]
    if chains:
        parts = []
        for c in chains:
            extra = [f"catena {c['template_chain']} del template" if c.get("template_chain") else "",
                     f"{c['length']} residui" if _isnum(c.get("length")) else "",
                     f"«{c['name']}»" if c.get("name") and c.get("name") != c.get("template_chain") else "",
                     f"stessa sequenza della catena {c['same_as']}" if c.get("same_as") else ""]
            parts.append(f"<b>{_e(c.get('model_chain'))}</b> ({_e(', '.join(x for x in extra if x))})")
        doc.add(f'<p>Catene del modello: {"; ".join(parts)}. I residui di ogni catena sono numerati da 1.</p>')
    sym = info.get("symmetry") if isinstance(info.get("symmetry"), dict) else {}
    if sym.get("applied"):
        groups = ", ".join(" = ".join(str(x) for x in g) for g in sym.get("groups") or [] if isinstance(g, list))
        doc.add(f'<p class="small">Restrizioni di simmetria di MODELLER sui C-alfa dei residui standard fra le catene '
                f"con la stessa sequenza ({_e(groups)}), peso {_num(sym.get('weight'), 1)}.</p>")
    elif sym.get("requested"):
        doc.add('<p class="small muted">Simmetria richiesta ma non applicata: nessuna coppia di catene con la stessa '
                "sequenza.</p>")
    assessment = info.get("assessment") if isinstance(info.get("assessment"), dict) else None
    if assessment is None:
        doc.add('<p class="small muted">Interfacce non analizzate per questo modello (analisi di qualità non '
                "eseguita).</p>")
        doc.end()
        return
    rows = []
    for c in [x for x in assessment.get("comparison") or [] if isinstance(x, dict)]:
        status = c.get("status")
        tchains = c.get("template_chains")
        origin = (f"template {' – '.join(str(x) for x in tchains)}" if isinstance(tchains, list)
                  else "assente nel template")
        rows.append("<tr>" + _td(f"{_e(' – '.join(str(x) for x in c.get('model_chains') or [] if x))}"
                                 f'<div class="small muted">{_e(origin)}</div>')
                    + _td(f"{_num(c.get('area_model'), 0)} ({_num(c.get('area_template'), 0)})", True)
                    + _td(f"{_num(c.get('conserved_percent'), 0, '%')} ({_e(c.get('conserved'))}/"
                          f"{_e(c.get('template_residues'))})" if _isnum(c.get("conserved_percent")) else "—", True)
                    + _td(_num(c.get("fnat"), 2), True) + _td(_num(c.get("irms"), 2), True)
                    + _td(_num(c.get("lrms"), 2), True) + _td(_num(c.get("dockq"), 2), True)
                    + _td(f'<span class="tag {_lookup(COMPLEX_TONE, status, "")}">{_e(status or "—")}</span>')
                    + "</tr>")
    template = assessment.get("template")
    if template:
        doc.add(f'<p class="small muted">Confronto con il template {_e(template)} (catene corrispondenti '
                "attraverso l'allineamento).</p>")
    if rows:
        doc.add(_table([("Catene", False), ("Area (template), Å²", True), ("Residui conservati", True),
                        ("Fnat", True), ("iRMS (Å)", True), ("LRMS (Å)", True), ("DockQ", True), ("Esito", False)],
                       rows))
        for c in [x for x in assessment.get("comparison") or [] if isinstance(x, dict)]:
            if c.get("text"):
                doc.add(_note(_e(c["text"]), _lookup(COMPLEX_TONE, c.get("status"), "")))
    else:
        pairs = ((assessment.get("interfaces") or {}).get("pairs")) or []
        doc.add('<p class="small muted">' + (f"{len(pairs)} interfacce nel modello, nessuna da confrontare."
                                              if pairs else "Nessuna interfaccia fra le catene del modello.") + "</p>")
    for note in assessment.get("notes") or []:
        doc.add(_note(_e(note), "warn"))
    if assessment.get("limits"):
        doc.add(_note(_e(assessment["limits"]), "warn"))
    doc.explain("complex", "interface")
    doc.cite("dockq", "capri", "sasa", "krissinel")
    doc.end()


REFINE_DIGITS = {"grade_score": 1, "rama_favored": 1, "rama_outliers": 1, "clashscore": 2, "dope": 0, "zdope": 2}
REFINE_VERDICT_TONE = {"meglio": "ok", "peggio": "err", "invariato": "info"}
ENERGY_TERMS = (("bonds", "Legami"), ("angles", "Angoli"), ("torsions", "Torsioni"),
                ("nonbonded", "Non legate (van der Waals, Coulomb)"), ("solvation", "Solvatazione (OBC2)"),
                ("total", "Totale"))
ENERGY_STAGES = (("before", "Iniziale"), ("after_minimization", "Dopo la minimizzazione"),
                 ("after_md", "Dopo la dinamica"), ("final", "Finale"))


def _dict(value: Any) -> dict:
    return value if isinstance(value, dict) else {}


def _refinement(doc: _Doc, data: dict) -> None:
    """Rifinitura energetica (F9.1): confronto prima/dopo, energia con le componenti, spostamento della catena
    principale, protocollo e parametri di OpenMM, preparazione della struttura (PDBFixer e copia pulita)."""
    ref = _dict(data.get("refine"))
    if not ref:
        return
    doc.section("rifinitura", "Rifinitura energetica (OpenMM)")
    rows = []
    for r in ref.get("comparison") or []:
        if not isinstance(r, dict):
            continue
        digits = REFINE_DIGITS.get(str(r.get("key")), 2)
        verdict = r.get("verdict")
        tag = (f'<span class="tag {_lookup(REFINE_VERDICT_TONE, verdict, "")}">{_e(verdict)}</span>' if verdict
               else "—")
        rows.append("<tr>" + _td(_e(r.get("label"))) + _td(_num(r.get("before"), digits), True)
                    + _td(_num(r.get("after"), digits), True) + _td(_signed(r.get("delta"), digits + 1), True)
                    + _td(tag) + "</tr>")
    if rows:
        doc.add("<h3>Prima e dopo</h3>",
                _table([("Metrica", False), ("Prima", True), ("Dopo", True), ("Differenza", True), ("Esito", False)],
                       rows),
                '<p class="small muted">«Prima» è la copia data a OpenMM (stessi residui, senza ligandi né idrogeni), '
                "così il confronto riguarda gli stessi atomi.</p>")
        doc.explain("refine_compare", "grade", "rama", "clash")
    energy = _dict(ref.get("energy"))
    components = _dict(energy.get("components"))
    stages = [(key, label) for key, label in ENERGY_STAGES if isinstance(components.get(key), dict)]
    doc.add("<h3>Energia del campo di forza</h3>")
    trend = ("diminuita" if energy.get("decreased") else "non diminuita") if _isnum(energy.get("delta")) else ""
    doc.add(f"<p>Da {_num(energy.get('before'), 1)} a {_num(energy.get('after'), 1)} kJ/mol (Δ "
            f"{_signed(energy.get('delta'), 1)} kJ/mol{', ' + trend if trend else ''}); energia delle restrizioni "
            f"alla fine {_num(energy.get('restraint_final'), 2)} kJ/mol.</p>")
    if stages:
        erows = ["<tr>" + _td(_e(label)) + "".join(_td(_num(_dict(components.get(key)).get(term), 1), True)
                                                    for key, _l in stages) + "</tr>" for term, label in ENERGY_TERMS]
        doc.add(_table([("Termine (kJ/mol)", False), *((label, True) for _k, label in stages)], erows))
    doc.explain("refine_energy")
    rmsd = _dict(ref.get("rmsd"))
    doc.add("<h3>Spostamento della catena principale</h3>",
            f"<p>RMSD {_num(rmsd.get('rmsd'), 3, ' Å')} dopo la sovrapposizione, {_num(rmsd.get('rmsd_no_fit'), 3, ' Å')}"
            f" senza; spostamento massimo {_num(rmsd.get('max_displacement'), 3, ' Å')} ({_e(rmsd.get('max_atom'))}), "
            f"su {_e(rmsd.get('atoms'))} atomi N, CA, C, O in comune.</p>")
    doc.explain("refine_rmsd")

    protocol, openmm, added, prepared = (_dict(ref.get(k)) for k in ("protocol", "openmm", "added", "input"))
    restraint, minimization, md = (_dict(protocol.get(k)) for k in ("restraint", "minimization", "md"))
    cutoff = _dict(protocol.get("cutoff"))
    params: list[tuple[str, str]] = [
        ("Campo di forza", _e(protocol.get("forcefield"))),
        ("Solvente implicito", _e(protocol.get("solvent"))),
        ("Protonazione", f"PDBFixer a pH {_num(protocol.get('ph'), 1)} (varianti dei residui titolabili alla pKa del "
                         "residuo libero)"),
        ("Restrizioni", f"armoniche su {_e(', '.join(str(a) for a in restraint.get('atoms') or []))}, "
                        f"k = {_num(restraint.get('k_kcal_mol_A2'), 1)} kcal·mol⁻¹·Å⁻² "
                        f"({_num(restraint.get('k_kj_mol_nm2'), 0)} kJ·mol⁻¹·nm⁻²), {_e(restraint.get('form'))}"),
        ("Minimizzazione", f"L-BFGS fino a {_num(minimization.get('tolerance_kj_mol_nm'), 0)} kJ·mol⁻¹·nm⁻¹ o "
                           f"{_e(minimization.get('max_iterations'))} iterazioni"),
        ("Dinamica", f"Langevin, {_num(md.get('ps'), 1)} ps a {_num(md.get('temperature_k'), 0)} K (passo "
                     f"{_num(md.get('timestep_fs'), 1)} fs, {_e(md.get('steps'))} passi, attrito "
                     f"{_num(md.get('friction_ps'), 1)} ps⁻¹, seme {_e(md.get('seed'))}), poi una seconda "
                     "minimizzazione" if md else "nessuna (solo minimizzazione)"),
        ("Interazioni non legate", _e(openmm.get("nonbonded")) + (
            f" (taglio oltre {_e(cutoff.get('atoms_over'))} atomi)" if cutoff else "")),
        ("Calcolo", f"OpenMM {_e(openmm.get('openmm'))}, piattaforma {_e(openmm.get('platform'))}, "
                    f"{_e(openmm.get('threads'))} thread; {_e(openmm.get('atoms'))} atomi, "
                    f"{_e(openmm.get('restrained_atoms'))} con restrizione"),
        ("Atomi aggiunti da PDBFixer", f"{_e(added.get('heavy_atoms'))} pesanti, {_e(added.get('terminal_atoms'))} "
                                       f"terminali, {_e(added.get('hydrogens'))} idrogeni"),
        ("Idrogeni nel modello rifinito", "sì" if protocol.get("hydrogens_in_output") else "no"),
    ]
    if added.get("nonstandard"):
        params.append(("Residui non standard sostituiti", _e(", ".join(str(x) for x in added["nonstandard"]))))
    clean = []
    if prepared.get("excluded"):
        clean.append("gruppi eteroatomici esclusi " + ", ".join(str(x) for x in prepared["excluded"][:20]))
    if prepared.get("modified"):
        clean.append("residui modificati tenuti " + ", ".join(str(x) for x in prepared["modified"][:20]))
    breaks = [b for b in prepared.get("breaks") or [] if isinstance(b, dict)]
    if breaks:
        clean.append("catena interrotta (" + ", ".join(f"{b.get('chain')}{b.get('after')}/{b.get('before')}"
                                                       for b in breaks[:12]) + "), nessun residuo ricostruito")
    if prepared.get("hydrogens_removed"):
        clean.append(f"{prepared['hydrogens_removed']} idrogeni del file sostituiti")
    params.append(("Copia data a OpenMM", f"{_e(prepared.get('residues'))} residui"
                   + (": " + _e("; ".join(clean)) if clean else "; nessun gruppo escluso")))
    doc.add("<h3>Protocollo</h3>", "<table><tbody>",
            *(f"<tr><th>{_e(k)}</th><td>{v}</td></tr>" for k, v in params), "</tbody></table>")
    doc.cite("openmm", "ff14sb", "obc", "lbfgs", "kabsch")
    doc.end()


# ---------------------------------------------------------------------------------------------- mutagenesi (F9.2)
MUTATION_TONE = {"destabilizing": "err", "uncertain": "warn", "neutral": "ok"}
SIGNAL_LEVEL = {"strong": ("forte", "err"), "moderate": ("moderato", "warn"), "gain": ("possibile guadagno", "info"),
                "info": ("informativo", "info")}


def _class_tag(assessment: dict) -> str:
    code = assessment.get("code")
    return f'<span class="tag {_lookup(MUTATION_TONE, code, "")}">{_e(assessment.get("label") or "—")}</span>'


def _mutation_cards(doc: _Doc, data: dict) -> list[str]:
    """Riquadri del riepilogo di un mutante (classe, ΔDOPE, ΔΔE) o di una scansione di alanina (conteggi)."""
    cards: list[str] = []
    mut, scan = _dict(data.get("mutation")), _dict(data.get("scan"))
    if mut:
        a, d, e = _dict(mut.get("assessment")), _dict(mut.get("dope")), _dict(mut.get("energy"))
        cards.append(_card("Effetto stimato", _class_tag(a), _e(mut.get("notation"))))
        cards.append(_card("ΔDOPE", _signed(d.get("delta"), 0),
                           f"{_signed(d.get('delta_eq'), 2)} residui equivalenti"))
        if _isnum(e.get("ddE")):
            cards.append(_card("ΔΔE (OpenMM)", _signed(e.get("ddE"), 0, " kJ/mol"),
                               f"{_signed(e.get('ddE_kcal'), 1)} kcal/mol · energia, non ΔΔG"))
        doc.explain("mutation_class", "mutation_dope")
    if scan:
        s, counts = _dict(scan.get("summary")), _dict(_dict(scan.get("summary")).get("counts"))
        cards.append(_card("Residui in alanina", _e(s.get("residues")), f"{_e(s.get('skipped'))} esclusi"))
        cards.append(_card("Probabilmente destabilizzanti", _e(counts.get("destabilizing")),
                           f"incerte {_e(counts.get('uncertain'))} · neutre {_e(counts.get('neutral'))}"))
        if scan.get("interface"):
            cards.append(_card("Hot spot d'interfaccia", _e(s.get("hotspots")), "probabili o possibili"))
        doc.explain("alascan", "mutation_class", "mutation_dope")
    return cards


def _signal_items(signals: list) -> list[str]:
    out = []
    for s in signals:
        if not isinstance(s, dict):
            continue
        label, tone = SIGNAL_LEVEL.get(str(s.get("level")), ("", "info"))
        site = f" <b>{_e(s.get('site'))}</b>" if s.get("site") else ""
        out.append(f'<li><span class="tag {tone}">{_e(label)}</span>{site} {_e(s.get("text"))}</li>')
    return out


def _mutation_protocol(doc: _Doc, protocol: dict, energy: bool) -> None:
    seeds = ", ".join(str(s) for s in protocol.get("seeds") or [])
    rows = [
        ("Metodo", _e(protocol.get("method"))),
        ("Ottimizzazione", f"residuo mutato randomizzato di {_num(protocol.get('randomize'), 1)} Å e ottimizzato, poi con "
                           f"le catene laterali entro {_num(protocol.get('radius'), 1)} Å (gradienti coniugati e "
                           "ricottura fino a 1000 K; il resto fermo)"),
        ("Modelli", f"{_e(protocol.get('models'))} per variante (semi {_e(seeds)}); {_e(protocol.get('selection'))}"),
        ("Selvatico", _e(protocol.get("wild_type"))),
        ("DOPE locale", f"residui entro {_num(protocol.get('local_radius'), 1)} Å dai siti"),
        ("Nucleo", f"RSA sotto {_num(100 * float(protocol.get('core_rsa') or 0), 0)}% (ASA massime di Tien et al. "
                   "2013)"),
        ("Energia", "OpenMM: minimizzazione con restrizioni di selvatico, mutante e tripeptidi di riferimento"
         if energy else "non calcolata"),
    ]
    doc.add("<h3>Protocollo</h3>", "<table><tbody>",
            *(f"<tr><th>{_e(k)}</th><td>{v}</td></tr>" for k, v in rows), "</tbody></table>")
    doc.cite("mutate_model", "modeller", "dope", "pace_scholtz", "macarthur", "minor_kim", "myers_pace", "strop_mayo",
             "chothia1974", "betz", "isom", "blosum", "tien", "lovell", "probe")
    if energy:
        doc.cite("openmm", "ff14sb", "obc")
        doc.explain("mutation_energy")


def _mutagenesis(doc: _Doc, data: dict) -> None:
    """Mutagenesi in silico (F9.2): classe con le motivazioni e l'avvertenza, siti, DOPE ed energia, confronto delle
    metriche fra selvatico riottimizzato e mutante, protocollo."""
    mut = _dict(data.get("mutation"))
    if not mut:
        return
    from .secondary import SS_LABELS

    a = _dict(mut.get("assessment"))
    doc.section("mutagenesi", "Mutagenesi in silico")
    doc.add(_note(f"<b>{_e(mut.get('notation'))}</b>: {_class_tag(a)} {_e(a.get('summary'))}",
                  _lookup(MUTATION_TONE, a.get("code"), "")))
    doc.add(_note(_e(mut.get("caveat")), "warn"))
    rows = []
    for s in mut.get("sites") or []:
        s = _dict(s)
        lost = _dict(s.get("interactions")).get("lost") or []
        rsa = s.get("rsa_wt")
        rows.append("<tr>" + _td(_e(s.get("label"))) + _td(_e(_lookup(SS_LABELS, s.get("ss"), s.get("ss") or "—")))
                    + _td(_num(100 * rsa, 0, "%") if _isnum(rsa) else "—", True)
                    + _td(_num(s.get("blosum62"), 0), True)
                    + _td(_signed(_dict(s.get("buried_delta")).get("apolar"), 0, " Å²"), True)
                    + _td(_e(len(lost)), True) + _td("sì" if s.get("interface") else "no") + "</tr>")
    doc.add("<h3>Siti</h3>", _table([("Mutazione", False), ("Struttura secondaria", False), ("RSA", True),
                                     ("BLOSUM62", True), ("Δ superficie apolare sepolta", True),
                                     ("Interazioni perse", True), ("Interfaccia", False)], rows))
    doc.add("<h3>Motivazioni</h3>", "<ul>", *_signal_items(a.get("signals") or []), "</ul>")
    binding = _dict(a.get("binding"))
    if binding:
        doc.add(_note(_e(binding.get("text")), "warn" if binding.get("hotspot") else ""))
        doc.cite("krissinel", "bogan_thorn")
    d, e = _dict(mut.get("dope")), _dict(mut.get("energy"))
    erows = [("DOPE totale", d.get("wild_type"), d.get("mutant"), d.get("delta"), d.get("delta_eq")),
             (f"DOPE locale ({_e(d.get('local_residues'))} residui entro {_num(d.get('local_radius'), 0)} Å)",
              d.get("local_wild_type"), d.get("local_mutant"), d.get("delta_local"), None)]
    if _isnum(d.get("interchain_delta")):
        erows.append(("DOPE fra le catene", d.get("interchain_wild_type"), d.get("interchain_mutant"),
                      d.get("interchain_delta"), d.get("interchain_delta_eq")))
    table = ["<tr>" + _td(label) + _td(_num(w, 1), True) + _td(_num(m, 1), True) + _td(_signed(delta, 1), True)
             + _td(_signed(eq, 2), True) + "</tr>" for label, w, m, delta, eq in erows]
    if _isnum(e.get("ddE")):
        table.append("<tr>" + _td("Energia OpenMM (kJ/mol)") + _td(_num(e.get("wild_type"), 1), True)
                     + _td(_num(e.get("mutant"), 1), True) + _td(_signed(e.get("delta_folded"), 1), True)
                     + _td("—", True) + "</tr>")
        table.append("<tr>" + _td("Riferimento: tripeptidi isolati (kJ/mol)")
                     + _td(_num(e.get("reference_wild_type"), 1), True) + _td(_num(e.get("reference_mutant"), 1), True)
                     + _td(_signed(e.get("delta_reference"), 1), True) + _td("—", True) + "</tr>")
    doc.add("<h3>DOPE ed energia</h3>",
            _table([("Termine", False), ("Selvatico", True), ("Mutante", True), ("Differenza", True),
                    ("Residui equivalenti", True)], table))
    if _isnum(e.get("ddE")):
        doc.add(f"<p>ΔΔE = {_signed(e.get('ddE'), 1)} kJ/mol ({_signed(e.get('ddE_kcal'), 1)} kcal/mol): differenza di "
                "energia fra mutante e selvatico meno quella dei tripeptidi di riferimento.</p>")
    spread = _dict(d.get("spread"))
    if spread:
        doc.add(f'<p class="small muted">Scarto del DOPE fra i modelli della stessa variante: selvatico '
                f"{_num(spread.get('wild_type'), 1)}, mutante {_num(spread.get('mutant'), 1)} (rumore del "
                "protocollo).</p>")
    rows = []
    for r in mut.get("comparison") or []:
        r = _dict(r)
        digits = REFINE_DIGITS.get(str(r.get("key")), 2)
        verdict = r.get("verdict")
        tag = (f'<span class="tag {_lookup(REFINE_VERDICT_TONE, verdict, "")}">{_e(verdict)}</span>' if verdict
               else "—")
        rows.append("<tr>" + _td(_e(r.get("label"))) + _td(_num(r.get("before"), digits), True)
                    + _td(_num(r.get("after"), digits), True) + _td(_signed(r.get("delta"), digits + 1), True)
                    + _td(tag) + "</tr>")
    if rows:
        doc.add("<h3>Selvatico riottimizzato e mutante</h3>",
                _table([("Metrica", False), ("Selvatico", True), ("Mutante", True), ("Differenza", True),
                        ("Esito", False)], rows))
        doc.explain("grade", "rama", "clash")
    rmsd = _dict(mut.get("rmsd"))
    if rmsd:
        doc.add(f"<p>RMSD della catena principale fra selvatico e mutante: {_num(rmsd.get('all'), 3, ' Å')} su "
                f"{_e(rmsd.get('atoms'))} atomi, {_num(rmsd.get('outside'), 3, ' Å')} fuori dai residui ottimizzati "
                "(ferma per costruzione).</p>")
    _mutation_protocol(doc, _dict(mut.get("protocol")), bool(_dict(mut.get("protocol")).get("energy")))
    doc.end()


def _scan(doc: _Doc, data: dict) -> None:
    """Scansione di alanina (F9.2): tabella dalla sostituzione più destabilizzante, esclusi, hot spot, protocollo."""
    scan = _dict(data.get("scan"))
    if not scan:
        return
    doc.section("scansione", "Scansione di alanina")
    sel = _dict(scan.get("selection"))
    doc.add(f"<p>Residui scelti: {_e(sel.get('detail'))}. Ordine: dalla sostituzione più destabilizzante (classe, poi "
            "segnali, poi ΔDOPE).</p>", _note(_e(scan.get("caveat")), "warn"))
    rows = []
    for r in scan.get("rows") or []:
        r = _dict(r)
        a, d, e, s = _dict(r.get("assessment")), _dict(r.get("dope")), _dict(r.get("energy")), _dict(r.get("site"))
        binding = _dict(a.get("binding"))
        iface = ("hot spot " + str(binding.get("hotspot"))) if binding.get("hotspot") else (
            "sì" if s.get("interface") else "no")
        reason = (a.get("reasons") or ["—"])[0]
        rows.append("<tr>" + _td(_e(r.get("label"))) + _td(_class_tag(a)) + _td(_signed(d.get("delta"), 0), True)
                    + _td(_signed(d.get("delta_eq"), 2), True) + _td(_signed(d.get("interchain_delta"), 0), True)
                    + _td(_signed(e.get("ddE"), 0), True)
                    + _td(_num(100 * s["rsa_wt"], 0, "%") if _isnum(s.get("rsa_wt")) else "—", True)
                    + _td(_e(s.get("ss"))) + _td(_e(iface)) + _td(f'<span class="small">{_e(reason)}</span>') + "</tr>")
    doc.add(_table([("Mutazione", False), ("Effetto", False), ("ΔDOPE", True), ("Residui eq.", True),
                    ("ΔDOPE fra catene", True), ("ΔΔE kJ/mol", True), ("RSA", True), ("DSSP", False),
                    ("Interfaccia", False), ("Motivo principale", False)], rows))
    skipped = [x for x in scan.get("skipped") or [] if isinstance(x, dict)]
    if skipped:
        doc.add("<h3>Residui esclusi</h3>", "<ul>",
                *(f"<li>{_e(x.get('resname'))} {_e(str(x.get('chain') or '').strip() or '_')}{_e(x.get('resnum'))}: "
                  f"{_e(x.get('reason'))}</li>" for x in skipped[:60]), "</ul>")
        doc.cite("kortemme")
    hot = [h for h in scan.get("hotspots") or [] if isinstance(h, dict)]
    if hot:
        doc.add("<h3>Hot spot d'interfaccia</h3>", "<ul>",
                *(f"<li><b>{_e(h.get('label'))}</b> ({_e(h.get('level'))}): {_e(h.get('text'))}</li>" for h in hot),
                "</ul>")
    if scan.get("interface"):
        doc.cite("bogan_thorn", "krissinel")
    protocol = _dict(scan.get("protocol"))
    _mutation_protocol(doc, protocol, bool(protocol.get("energy")))
    doc.end()


PREDICTION_TONE = {"concordano": "ok", "divergenze locali": "warn", "ripiegamento diverso": "err",
                   "non conclusivo": "", "sicura": "warn", "incerta": "info", "molto incerta": "info"}


def _prediction(doc: _Doc, data: dict) -> None:
    """Confronto con una predizione esterna (F9.3): esito motivato, metriche, regioni divergenti con il pLDDT della
    predizione e le regioni del consenso, origine della predizione e limiti. Solo se il confronto è stato eseguito."""
    info = _dict(data.get("prediction"))
    plaus = _dict(info.get("plausibility"))
    if not plaus:
        return
    label = info.get("label") or plaus.get("label") or "predizione"
    doc.section("predizione", f"Confronto con una predizione ({label})")
    origin, comp, pl = _dict(info.get("origin")), _dict(info.get("comparison")), _dict(info.get("plddt"))
    if info.get("predictor") == "esmfold":
        where = (f"servizio {_e(origin.get('service'))}" + (" (dalla cache)" if origin.get("cached") else "")
                 + (", pLDDT riportato da 0–1 a 0–100" if origin.get("scale") == "0-1" else ""))
    else:
        where = f"AlphaFold DB, {_e(origin.get('entry') or info.get('accession'))}"
    doc.add(f'<p class="small muted">Dal confronto {_e(info.get("job_id"))} del {_date(info.get("finished_at"))}: '
            f"{_e(label)} ({where}); catena {_e(info.get('chain') or '—')} del modello, "
            f"{_e((info.get('sequence') or {}).get('length'))} residui predetti.</p>")
    status = plaus.get("status")
    doc.add(_note(f"<b>{_e(status)}</b>: {_e(plaus.get('summary'))}", _lookup(PREDICTION_TONE, status, "")))
    doc.add('<div class="cards">',
            _card("TM-score (sul modello)", _num(comp.get("tm_score"), 3), "da 0,5 stesso ripiegamento"),
            _card("lDDT", _num(comp.get("lddt"), 3), f"sui residui sicuri {_num(plaus.get('lddt_confident'), 3)}"),
            _card("GDT-TS", _num(comp.get("gdt_ts"), 1), f"RMSD {_num(comp.get('rmsd'), 2, ' Å')}"),
            _card("pLDDT della predizione", _num(pl.get("mean"), 1),
                  f"{_e(plaus.get('confident_residues'))} su {_e(plaus.get('pairs'))} residui ≥ 70"), "</div>")
    regions = [r for r in plaus.get("regions") or [] if isinstance(r, dict)]
    if regions:
        rows = ["<tr>" + _td(f"{_e(r.get('start'))}–{_e(r.get('end'))} ({_e(r.get('chain'))})")
                + _td(f"{_e(r.get('divergent'))}/{_e(r.get('length'))}", True) + _td(_num(r.get("lddt_mean"), 2), True)
                + _td(_num(r.get("plddt_mean"), 0), True)
                + _td(f'<span class="tag {_lookup(PREDICTION_TONE, r.get("confidence"), "")}">'
                      f'{_e(r.get("confidence") or "—")}</span>')
                + _td(_e("; ".join(str(c) for c in r.get("consensus") or [])) or '<span class="muted">—</span>')
                + "</tr>" for r in regions]
        doc.add("<h3>Regioni divergenti</h3>",
                _table([("Regione", False), ("Divergenti", True), ("lDDT medio", True), ("pLDDT medio", True),
                        ("Predizione", False), ("Consenso", False)], rows))
    else:
        doc.add('<p class="small muted">Nessuna regione di almeno 3 residui con lDDT sotto 0,5.</p>')
    for note in plaus.get("notes") or []:
        doc.add(f'<p class="small muted">{_e(note)}</p>')
    if plaus.get("limits"):
        doc.add(_note(_e(plaus["limits"]), "warn"))
    doc.explain("prediction", "plddt")
    doc.cite("esmfold" if info.get("predictor") == "esmfold" else "alphafold", "afdb", "lddt", "tmscore", "xu_zhang")
    doc.end()


def _warnings(doc: _Doc, data: dict) -> None:
    items: list[str] = []
    for w in [*(data.get("warnings") or []), *(((data.get("quality") or {}).get("warnings")) or [])]:
        if w and str(w) not in items:
            items.append(str(w))
    doc.section("avvisi", "Avvisi")
    if items:
        doc.add("<ul>", *(f"<li>{_e(w)}</li>" for w in items), "</ul>")
    else:
        doc.add('<p class="small muted">Nessun avviso.</p>')
    doc.end()


def _legend_and_refs(doc: _Doc) -> None:
    doc.section("legenda", "Legenda delle metriche", page_break=True)
    doc.add('<dl class="legend">', *(f"<dt>{_e(LEGEND[k][0])}</dt><dd>{_e(LEGEND[k][1])}</dd>"
                                     for k in LEGEND if k in doc.legend), "</dl>")
    doc.end()
    doc.section("riferimenti", "Riferimenti bibliografici")
    doc.add('<ol class="refs">', *(f"<li>{_e(REFERENCES[k])}</li>" for k in REFERENCES if k in doc.refs), "</ol>")
    doc.end()


def render(data: dict) -> str:
    """Documento HTML completo del report.

    `data` (raccolto da services/reports.py; ogni testo è trattato come non fidato): kind ("model" | "loop" |
    "pipeline" | "quality" | "refine"), title, job_id, project, created_at, finished_at, generated_at, tools [(nome, versione)],
    model (file scelto), target {name, sequence (catene separate da '/'), chains}, structure (solo quality:
    report.structure), source (descrizione della struttura analizzata), templates [{id, code, chains, first, last,
    source, title, method, resolution, identity, coverage, predicted, plddt_mean, trim, trim_off}], search (pipeline:
    engine, identity, coverage, evalue, twilight), alignment (testo PIR), options (ModelOptions come dizionario), loop
    (source, start, end, models, level), pipeline (parametri della pipeline), models [{file, kind, dope, zdope, ga341,
    molpdf, grade, grade_score, best, chosen, failure}], quality (rapporto di engines/quality), consensus
    (domain/consensus.compute), warnings [testo], image_png (byte di un PNG già verificato con `decode_png`) o None,
    ligands (verifica di domain/ligands per il modello senza rapporto di qualità; altrimenti quella del rapporto).
    analysis (services/analysis.report_summary: job_id, finished_at, disulfides, interfaces) o None.
    complex (modelli a più catene, F5.3: chains, symmetry, template, assessment di domain/complexes.assess) o None.
    refine (solo kind "refine", F9.1: dal risultato del job di services/refine.py: label, protocol, openmm, added, input,
    energy, rmsd, quality {before, after}, comparison); con "refine" niente template, allineamento, parametri di
    MODELLER e tabella dei modelli.
    mutation (kind "mutate", F9.2: risultato del job di services/mutations.py: notation, assessment, sites, dope,
    energy, comparison, rmsd, protocol, caveat) e scan (kind "alascan": rows, skipped, summary, hotspots, selection,
    protocol, caveat, interface): sezioni "Mutagenesi in silico" e "Scansione di alanina"; la scansione non ha la
    sezione della qualità.
    prediction (F9.3, services/predict.prediction_summary: job_id, finished_at, predictor, label, origin, plddt, chain,
    sequence, accession, comparison, plausibility di domain/prediction.assess) o None.
    """
    doc = _Doc()
    kind = data.get("kind")
    _summary(doc, data)
    _target(doc, data)
    if kind == "refine":
        _refinement(doc, data)
        if (data.get("quality") or {}).get("dope"):
            doc.cite("modeller", "dope")
    elif kind in MUTATION_KINDS:  # mutagenesi e scansione di alanina (F9.2): niente template né allineamento
        _mutagenesis(doc, data)
        _scan(doc, data)
    elif kind != "quality":
        _templates(doc, data)
        _alignment(doc, data)
        _options(doc, data)
        _models(doc, data)
        doc.cite("modeller")
    elif (data.get("quality") or {}).get("dope"):
        doc.cite("modeller", "dope")
    if kind != "alascan":  # la scansione non ha un modello analizzato
        _quality(doc, data)
    _ligands(doc, data)
    _complex(doc, data)
    _structural(doc, data)
    _prediction(doc, data)
    _warnings(doc, data)
    _legend_and_refs(doc)
    subject = data.get("title") or data.get("model") or ""
    title = ({"quality": "Report qualità", "refine": "Report rifinitura", "mutate": "Report mutagenesi",
              "alascan": "Report scansione di alanina"}.get(str(kind), "Report modello")
             + (f" · {subject}" if subject else ""))
    version = dict(data.get("tools") or []).get("BioLoom", "")
    footer = (f"<footer>Generato da BioLoom {_e(version)} il {_date(data.get('generated_at'))} dai file del job "
              f"{_e(data.get('job_id'))}. Le regioni di Ramachandran e il clashscore sono approssimazioni: per una "
              "validazione formale del modello finale usa anche MolProbity, PROCHECK o QMEAN.</footer>")
    return ("<!DOCTYPE html>\n"
            '<html lang="it"><head><meta charset="utf-8">'
            f'<meta http-equiv="Content-Security-Policy" content="{CSP}">'
            '<meta name="viewport" content="width=device-width, initial-scale=1">'
            '<meta name="generator" content="BioLoom">'
            f"<title>{_e(title)}</title><style>{CSS}</style></head><body>"
            + _header(data, doc.toc) + "<main>" + doc.html() + "</main>" + footer + "</body></html>\n")
