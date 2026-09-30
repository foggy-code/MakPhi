"""Report HTML autonomo di un modello (o di un'analisi di qualità), stampabile in PDF dal browser.

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
              "quality": "Analisi di qualità"}


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
_MODEL_TRIM = re.compile(r"^REMARK 999 BIOLOOM TEMPLATE (\S+) ALPHAFOLD (?:PLDDT<(\S+) REMOVED (\d+)|(PLDDT_TRIM OFF))",
                         re.MULTILINE)


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
    """Template AlphaFold dalle righe di tracciabilità del modello: codice → ritaglio (None = ritaglio spento)."""
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
REGION_FILL = {"allowed": "#dbe7f4", "favored": "#a8c8e6"}
POINT_FILL = {"favored": "#1d4e89", "allowed": "#d98200", "outlier": "#c62828"}
REGION_LABEL = {"favored": "favorita", "allowed": "consentita", "outlier": "outlier"}
# Pannelli come nell'interfaccia (components/charts.tsx): la pre-prolina usa le regioni del caso generale
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
    """Grafico di Ramachandran di una classe di residui: regioni favorite e consentite (rettangoli di
    domain/ramachandran, calibrati sulle aree di MolProbity) e un cerchio per residuo; outlier etichettati."""
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
           f'<rect x="{pl}" y="{pt}" width="{size}" height="{size}" fill="#f7f9fb" stroke="#9aa5b1"/>']
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
        out.append(f'<line x1="{_f(x(v))}" x2="{_f(x(v))}" y1="{pt}" y2="{pt + size}" stroke="#c4cdd5" '
                   'stroke-dasharray="3 3"/>')
        out.append(f'<line x1="{pl}" x2="{pl + size}" y1="{_f(y(v))}" y2="{_f(y(v))}" stroke="#c4cdd5" '
                   'stroke-dasharray="3 3"/>')
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
                   + (' stroke="#ffffff" stroke-width="0.8"' if outlier else ' fill-opacity="0.8"')
                   + f'><title>{_e(tip)}</title></circle>')
        if outlier:  # etichetta a destra del punto, a sinistra vicino al bordo destro del grafico
            right = cx > pl + size - 60
            tx, anchor = (cx - 5, "end") if right else (cx + 5, "start")
            labels.append(f'<text class="lab" x="{_f(tx)}" y="{_f(max(pt + 9, cy - 4))}" text-anchor="{anchor}" '
                          f'font-size="9" font-weight="bold" fill="#8e1b1b">{_e(_residue_label(p))}</text>')
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
    "foldindex": "Prilusky J., Felder C.E., Zeev-Ben-Mordehai T., Rydberg E.H., Man O., Beckmann J.S., Silman I., "
                 "Sussman J.L. (2005) FoldIndex: a simple tool to predict whether a given protein sequence is "
                 "intrinsically unfolded. Bioinformatics 21:3435–3438.",
    "uversky": "Uversky V.N., Gillespie J.R., Fink A.L. (2000) Why are 'natively unfolded' proteins unstructured "
               "under physiologic conditions? Proteins 41:415–427.",
    "kyte": "Kyte J., Doolittle R.F. (1982) A simple method for displaying the hydropathic character of a protein. "
            "J. Mol. Biol. 157:105–132.",
    "ward": "Ward J.J., Sodhi J.S., McGuffin L.J., Buxton B.F., Jones D.T. (2004) Prediction and functional analysis "
            "of native disorder in proteins from the three kingdoms of life. J. Mol. Biol. 337:635–645.",
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
    "plddt": ("pLDDT", "confidenza locale di AlphaFold da 0 a 100 (lDDT-Cα predetto; Jumper et al. 2021; Mariani et "
              "al. 2013), nella colonna B-factor. Fasce di AlphaFold DB (Tunyasuvunakool et al. 2021): ≥ 90 molto "
              "alta, 70–90 confidente, 50–70 bassa, < 50 molto bassa (spesso disordine). Un modello AlphaFold è una "
              "predizione, non una struttura sperimentale."),
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
}


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


class _Doc:
    """Accumula il testo del documento e ricorda quali voci della legenda e quali fonti servono."""

    def __init__(self) -> None:
        self.parts: list[str] = []
        self.legend: set[str] = set()
        self.refs: set[str] = set()
        self.toc: list[tuple[str, str]] = []

    def add(self, *chunks: str) -> None:
        self.parts.extend(chunks)

    def section(self, sid: str, title: str, page_break: bool = False) -> None:
        self.toc.append((sid, title))
        self.add(f'<section id="{sid}"' + (' class="page"' if page_break else "") + f"><h2>{_e(title)}</h2>")

    def end(self) -> None:
        self.add("</section>")

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
    "fast": "rapido (md_level = refine.very_fast)",
    "normal": "standard (impostazioni predefinite di AutoModel)",
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
    heading = "Report dell'analisi di qualità" if kind == "quality" else "Report del modello"
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
    if not cards:
        return
    doc.section("riepilogo", "Riepilogo")
    doc.add('<div class="cards">', *cards, "</div>")
    if not quality:
        doc.add(_note("Analisi di qualità non calcolata per questo modello: il report riporta solo i risultati di "
                      "MODELLER.", "warn"))
    doc.end()


def _target(doc: _Doc, data: dict) -> None:
    target = data.get("target") or {}
    structure = data.get("structure")
    doc.section("bersaglio", "Struttura analizzata" if data.get("kind") == "quality" else "Bersaglio")
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
        doc.add(_note(f"<b>{_e(t.get('code') or t.get('id'))}: modello predetto (AlphaFold DB), non una struttura "
                      f"sperimentale.</b>{mean} Template {_trim_text(t)}. I residui tolti sono costruiti senza "
                      "vincoli dal template.", "warn"))
        doc.explain("plddt")
        doc.cite("alphafold", "afdb", "lddt")
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
                 ("Ligandi (HETATM)", "sì" if opts.get("hetatm") else "no")]
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
            rows.append(("Ritaglio pLDDT (solo template AlphaFold)",
                         f"sì, soglia {_num(opts.get('plddt_min'), 0)}" if opts.get("plddt_trim") else "no"))
    if pipe:
        auto = pipe.get("template_mode") != "fixed"
        tw = pipe.get("twilight") if isinstance(pipe.get("twilight"), dict) else {}
        rows += [("Scelta del template", "ricerca automatica" if auto else "template fisso"),
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
            f'<span class="swatch" style="background:{POINT_FILL["favored"]}"></span>residuo favorito'
            f'<span class="swatch" style="background:{POINT_FILL["allowed"]}"></span>consentito'
            f'<span class="swatch" style="background:{POINT_FILL["outlier"]}"></span>outlier (etichettato)</p>')
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
        figs.append(profile_svg(residues, [r.get("bfactor") for r in residues], title="Confidenza pLDDT (AlphaFold)",
                                y_range=(0.0, 100.0), digits=0, guides=((90.0, "90"), (70.0, "70"), (50.0, "50"))))
        doc.explain("plddt")
        doc.cite("alphafold", "afdb", "lddt")
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
    title = "Qualità della struttura" if data.get("kind") == "quality" else "Qualità del modello scelto"
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
        doc.add(_note("Modello AlphaFold: la colonna B-factor contiene il pLDDT (confidenza locale), non un fattore di "
                      "temperatura."))
        doc.explain("plddt")
        doc.cite("alphafold", "afdb", "lddt")
    _grade_block(doc, quality)
    _consensus_block(doc, data.get("consensus"))
    _rama_block(doc, quality)
    _profiles_block(doc, quality, data.get("consensus"))
    _secondary_block(doc, quality)
    _clash_block(doc, quality)
    _external_block(doc, quality)
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
    "pipeline" | "quality"), title, job_id, project, created_at, finished_at, generated_at, tools [(nome, versione)],
    model (file scelto), target {name, sequence (catene separate da '/'), chains}, structure (solo quality:
    report.structure), source (descrizione della struttura analizzata), templates [{id, code, chains, first, last,
    source, title, method, resolution, identity, coverage, predicted, plddt_mean, trim, trim_off}], search (pipeline:
    engine, identity, coverage, evalue, twilight), alignment (testo PIR), options (ModelOptions come dizionario), loop
    (source, start, end, models, level), pipeline (parametri della pipeline), models [{file, kind, dope, zdope, ga341,
    molpdf, grade, grade_score, best, chosen, failure}], quality (rapporto di engines/quality), consensus
    (domain/consensus.compute), warnings [testo], image_png (byte di un PNG già verificato con `decode_png`) o None.
    """
    doc = _Doc()
    kind = data.get("kind")
    _summary(doc, data)
    _target(doc, data)
    if kind != "quality":
        _templates(doc, data)
        _alignment(doc, data)
        _options(doc, data)
        _models(doc, data)
        doc.cite("modeller")
    elif (data.get("quality") or {}).get("dope"):
        doc.cite("modeller", "dope")
    _quality(doc, data)
    _warnings(doc, data)
    _legend_and_refs(doc)
    subject = data.get("title") or data.get("model") or ""
    title = ("Report qualità" if kind == "quality" else "Report modello") + (f" · {subject}" if subject else "")
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
            + _header(data, doc.toc) + "<main>" + "".join(doc.parts) + "</main>" + footer + "</body></html>\n")
