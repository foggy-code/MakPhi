"""Copertura del bersaglio da parte dei template e avvisi su disordine e domini prima di modellare (F5.5).

Serve alle pagine Modellazione e Ricerca: prima di lanciare, dice se il bersaglio ha regioni disordinate lunghe
fuori dalla copertura del template o domini non coperti, con un suggerimento (`disorder.coverage_assessment`).

- **Disordine e bassa complessità:** dalla sola sequenza (`domain/disorder.predict`), senza rete.
- **Domini:** solo con un'accessione UniProt, dai domini di InterPro (`remote.interpro_domains`, in cache 7 giorni).
  I confini sono sulla sequenza di UniProt (`remote.uniprot_entry`, in cache): si riportano sul bersaglio se questo è
  la sequenza di UniProt o un suo tratto, altrimenti si tralasciano con una nota. Senza accessione nessun dominio e
  nessuna richiesta remota; un servizio non raggiungibile dà una nota, non un errore.
- **Copertura:** residui del bersaglio con un residuo del template di fronte.
  - template della libreria (Modellazione, allineamento automatico): allineamento locale Smith-Waterman (BLOSUM62,
    gap 11/1, come i motori di ricerca) del bersaglio su ciascuna catena scelta. È una stima: MODELLER (align2d)
    allinea a modo suo, ma i tratti non coperti di un allineamento locale restano in genere senza template anche
    lì. Per i template AlphaFold con il ritaglio pLDDT i residui sotto soglia (e i frammenti isolati,
    `plddt.low_confidence`, come in modellazione) non coprono;
  - allineamento PIR (Modellazione, da documento): le colonne del PIR, esatte;
  - intervalli (Ricerca): il tratto del bersaglio allineato da ogni hit (`query_start`–`query_end`).
  I template e il PIR si valutano anche insieme (`combined`: MODELLER li usa tutti); gli intervalli sono candidati
  alternativi e si valutano uno per uno.
- **Identità** (template della libreria e PIR): residui identici sulle coppie allineate (`identity`, `aligned`), dallo
  stesso allineamento della copertura; la usano i controlli preventivi (services/preflight.py) per la zona
  crepuscolare.
"""

from __future__ import annotations

import re
from typing import Optional

from ..core.errors import AppError, NotFound, RemoteServiceError
from ..domain import disorder, plddt, sequences, structure
from ..domain.sequences import SeqRecord
from ..engines import align, remote
from .templates import TemplateLibrary, normalize_id

MAX_TEMPLATES = 10  # come la modellazione (services/compute._template_specs)
MAX_RANGES = 500  # come i risultati massimi della ricerca
# Lettere fuori dall'alfabeto di BLOSUM62 (U, O, J): X nell'allineamento, come engines/search
_BLOSUM_OTHER = re.compile(r"[^ARNDCQEGHILKMFPSTWYVBZX]")


def local_pairs(target: str, template: str) -> list[tuple[int, int]]:
    """Coppie (posizione nel bersaglio, posizione nel template), 1-based, dell'allineamento locale Smith-Waterman
    (BLOSUM62, gap 11/1): le colonne con un residuo in entrambe le sequenze."""
    if not target or not template:
        return []
    res = align.pairwise(SeqRecord("target", _BLOSUM_OTHER.sub("X", target)),
                         SeqRecord("template", _BLOSUM_OTHER.sub("X", template)),
                         mode="local", matrix="BLOSUM62", gap_open=11.0, gap_extend=1.0)
    if not res.get("ranges"):
        return []
    ti, si = res["ranges"][0][0], res["ranges"][1][0]
    pairs = []
    for a, b in zip(*res["aligned"]):
        if a != "-" and b != "-":
            pairs.append((ti, si))
        ti += a != "-"
        si += b != "-"
    return pairs


def _identity(identical: int, aligned: int) -> Optional[float]:
    """Identità (%) sulle coppie allineate, come `report.alignment_identity` e il `% SEQ ID` di MODELLER."""
    return round(100.0 * identical / aligned, 1) if aligned else None


def pir_coverage(text: str) -> tuple[str, list[dict], Optional[str]]:
    """Bersaglio (lettere della riga `sequence`), copertura di ogni template dalle colonne del PIR e una nota (None
    se va tutto bene). Solo bersagli a una catena. Per ogni template anche le coppie allineate (`aligned`) e
    l'identità su quelle coppie (`identity`), per i controlli preventivi (services/preflight.py)."""
    entries = sequences.parse_pir(text)
    target = next((e for e in entries if e.kind == "sequence"), None)
    if target is None:
        raise AppError("Il PIR non contiene la voce del bersaglio (riga 'sequence').", code="invalid_pir")
    if "/" in target.sequence.strip("/"):
        return "", [], ("Bersaglio con più catene: gli avvisi su disordine e domini valgono per un bersaglio a catena "
                        "singola.")
    letters = "".join(c for c in target.sequence if c.isalpha())
    out = []
    for e in entries:
        if not e.is_template:
            continue
        covered, pos, identical = set(), 0, 0
        for k, c in enumerate(target.sequence):
            if not c.isalpha():
                continue
            pos += 1
            if k < len(e.sequence) and e.sequence[k].isalpha():
                covered.add(pos)
                identical += e.sequence[k].upper() == c.upper()
        out.append({"label": e.code, "covered_positions": covered, "aligned": len(covered),
                    "identity": _identity(identical, len(covered))})
    return letters, out, None


class CoverageService:
    def __init__(self, library: TemplateLibrary) -> None:
        self.library = library

    # ------------------------------------------------------------------------------ domini
    def domains(self, accession: Optional[str], target: str) -> tuple[list[dict], dict]:
        """Domini di InterPro riportati sul bersaglio e le informazioni sulla loro provenienza."""
        if not (accession or "").strip():
            return [], {"source": None, "accession": None, "available": False,
                        "note": "Nessuna accessione UniProt: i domini non sono noti (BioLoom non li cerca da sé)."}
        acc = remote.validate_uniprot_accession(accession or "")
        info = {"source": "InterPro", "accession": acc, "available": False, "note": None}
        try:
            entry = remote.uniprot_entry(acc)
            entries = remote.interpro_domains(acc)
        except NotFound:
            info["note"] = f"UniProt non conosce l'accessione {acc}: domini non disponibili."
            return [], info
        except RemoteServiceError as exc:
            info["note"] = f"Domini non disponibili: {exc.message}"
            return [], info
        reference = str(entry.get("sequence") or "").upper()
        offset = 0 if reference == target else reference.find(target) if target else -1
        if offset < 0:
            info["note"] = (f"La sequenza del bersaglio non coincide con quella di UniProt {acc} né con un suo tratto: i "
                            "confini dei domini non si possono riportare sul bersaglio.")
            return [], info
        shifted = []
        for e in entries:
            locations = [{"start": int(loc["start"]) - offset, "end": int(loc["end"]) - offset}
                         for loc in e.get("locations") or [] if isinstance(loc.get("start"), int)
                         and isinstance(loc.get("end"), int)]
            shifted.append({**e, "locations": locations})
        info["available"] = True
        if offset:
            info["note"] = f"Bersaglio = residui {offset + 1}–{offset + len(target)} di UniProt {acc}."
        return disorder.target_domains(shifted, len(target)), info

    # ------------------------------------------------------------------------------ copertura dei template
    def template_positions(self, target: str, template_id: str, chains: list[str],
                           plddt_min: Optional[float]) -> dict:
        """Posizioni del bersaglio coperte da un template della libreria (unione delle catene scelte)."""
        tid = normalize_id(template_id)
        analysis = self.library.analyze(tid)
        available = {c["chain"]: c for c in analysis.get("chains") or []}
        chosen = [c for c in chains if c] or list(available)[:1]
        missing = [c for c in chosen if c not in available]
        if missing:
            raise AppError(f"Il template {tid} non ha la catena {', '.join(missing)}.")
        trim_note = None
        kept: dict[str, list[bool]] = {}
        if plddt_min is not None and self.library.is_predicted(tid):
            model = structure.first_model(structure.load_structure(self.library.path(tid), tid))
            for c in chosen:
                rows = plddt.low_confidence(model, [c], float(plddt_min))["rows"]
                if len(rows) == len(available[c]["sequence"]):
                    kept[c] = [bool(r[5]) for r in rows]
            trim_note = f"residui con pLDDT < {float(plddt_min):g} esclusi, come nel ritaglio della modellazione"
        covered: set[int] = set()
        # identità: sulla catena con più coppie allineate (quella su cui MODELLER allinea il bersaglio; le catene di un
        # omo-oligomero danno la stessa), sui soli residui che restano nel template
        best = (0, 0)
        for c in chosen:
            keep = kept.get(c)
            seq = available[c]["sequence"]
            pairs = identical = 0
            for t_pos, s_pos in local_pairs(target, seq):
                if keep is None or keep[s_pos - 1]:
                    covered.add(t_pos)
                    pairs += 1
                    identical += target[t_pos - 1] == seq[s_pos - 1]
            best = max(best, (pairs, identical))
        return {"label": f"{tid}:{''.join(chosen)}", "id": tid, "chains": chosen, "covered_positions": covered,
                "note": trim_note, "aligned": best[0], "identity": _identity(best[1], best[0])}

    # ------------------------------------------------------------------------------ verifica completa
    def check(self, *, sequence: str = "", accession: Optional[str] = None, templates: Optional[list[dict]] = None,
              alignment: Optional[str] = None, ranges: Optional[list[dict]] = None,
              plddt_min: Optional[float] = None, max_length: int = disorder.MAX_LENGTH) -> dict:
        templates, ranges = templates or [], ranges or []
        if len(templates) > MAX_TEMPLATES:
            raise AppError(f"Massimo {MAX_TEMPLATES} template per verifica.")
        if len(ranges) > MAX_RANGES:
            raise AppError(f"Massimo {MAX_RANGES} intervalli per verifica.")
        note = None
        sets: list[dict] = []  # usati insieme (modellazione): valutati anche in `combined`
        if alignment:
            target, pir_sets, note = pir_coverage(alignment)
            if note:
                return {"length": 0, "note": note, "disorder": None, "domains": [], "domains_info": None,
                        "templates": [], "combined": None}
            sets.extend(pir_sets)
        else:
            target = sequence
        target = disorder.clean(target, max_length)
        prediction = disorder.predict(target, max_length=max_length)
        domains, domains_info = self.domains(accession, target)
        if not alignment:
            for t in templates:
                label = f"{t.get('id')}"
                try:
                    sets.append(self.template_positions(target, str(t.get("id") or ""), list(t.get("chains") or []),
                                                        plddt_min))
                except AppError as exc:  # template assente o catena sbagliata: gli altri si valutano lo stesso
                    sets.append({"label": label, "id": t.get("id"), "error": exc.message})
        n = len(target)

        def assess(covered: set[int]) -> dict:
            return disorder.coverage_assessment(n, prediction["regions"], domains, covered)

        out_templates = []
        for s in sets:
            if "error" in s:
                out_templates.append({"label": s["label"], "id": s.get("id"), "error": s["error"]})
                continue
            out_templates.append({"label": s["label"], "id": s.get("id"), "chains": s.get("chains"),
                                  "note": s.get("note"), "identity": s.get("identity"), "aligned": s.get("aligned"),
                                  **assess(s["covered_positions"])})
        valid = [s for s in sets if "error" not in s]
        combined = None
        if valid:
            union = set().union(*(s["covered_positions"] for s in valid))
            combined = {"label": "template insieme" if len(valid) > 1 else valid[0]["label"], **assess(union)}
        for r in ranges:  # candidati alternativi (hit della ricerca): uno per uno
            try:
                start, end = int(r.get("start")), int(r.get("end"))
            except (TypeError, ValueError):
                raise AppError("Intervallo di copertura non valido: servono inizio e fine numerici.")
            start, end = max(1, min(start, end)), min(n, max(start, end))
            out_templates.append({"label": str(r.get("label") or f"{start}-{end}")[:80],
                                  **assess(set(range(start, end + 1)))})
        return {"length": n, "note": note, "disorder": prediction, "domains": domains, "domains_info": domains_info,
                "templates": out_templates, "combined": combined}
