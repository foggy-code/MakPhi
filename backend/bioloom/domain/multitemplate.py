"""Allineamento del bersaglio su più template per MODELLER: scelta fra `salign` e gli allineamenti a coppie.

Con più template lo script di allineamento (`engines/modeller.build_alignment_script`) fa due cose:

1. l'allineamento strutturale dei template con `salign` e quello del bersaglio sul loro profilo (il metodo del
   tutorial di MODELLER per più template omologhi: il profilo strutturale aiuta con le omologie lontane);
2. l'allineamento a coppie del bersaglio su ogni template con `align2d` (penalità dei gap dipendenti dalla
   struttura del template; Madhusudhan et al. 2006).

`salign` allinea i template fra loro in modo globale: con template che coprono regioni diverse del bersaglio
(frammenti sfalsati, domini diversi: proprio i template complementari) non ha un ancoraggio comune e può spostare un
template rispetto al bersaglio. Misurato su due frammenti sovrapposti di 1UBQ (1–50 e 30–76): il secondo finiva
allineato dal residuo 1 del bersaglio, con un modello a TM-score 0,35 rispetto a 1UBQ.

`choose` confronta, per ogni template, i residui identici al bersaglio nell'allineamento di `salign` con quelli
dell'allineamento a coppie. Se `salign` ne perde più della metà per un template (`MIN_SALIGN_RATIO`: un allineamento
spostato scende verso l'identità casuale, mentre fra due buoni allineamenti le identità differiscono di pochi
residui), o se `salign` non è riuscito, si usa l'unione degli allineamenti a coppie sul bersaglio (`merge_on_target`):
ogni template resta allineato come nel suo `align2d`. Altrimenti resta `salign`, come prima.
"""

from __future__ import annotations

from typing import Optional

from .sequences import PirEntry, format_pir, parse_pir

MIN_SALIGN_RATIO = 0.5
# Sotto questi residui identici nell'allineamento a coppie il confronto non dice nulla (template quasi senza
# identità con il bersaglio): si tiene salign.
MIN_PAIR_IDENTICAL = 5


def identical_pairs(target: str, template: str) -> tuple[int, int]:
    """(coppie allineate, coppie identiche) fra due righe di un allineamento della stessa lunghezza."""
    pairs = same = 0
    for a, b in zip(target.upper(), template.upper()):
        if a.isalpha() and b.isalpha():
            pairs += 1
            same += a == b
    return pairs, same


def _split(text: str) -> tuple[Optional[PirEntry], list[PirEntry]]:
    entries = parse_pir(text)
    target = next((e for e in entries if e.kind == "sequence"), None)
    return target, [e for e in entries if e.is_template]


def mergeable(pairs: list[str]) -> bool:
    """L'unione sul bersaglio vale per allineamenti a coppie con un template ciascuno, senza separatori di catena né
    ligandi ('/' e '.' devono stare nella stessa colonna in tutte le righe, e l'unione non li riallinea)."""
    for text in pairs:
        target, templates = _split(text)
        if target is None or len(templates) != 1:
            return False
        if any(c in "/." for c in target.sequence + templates[0].sequence):
            return False
    return True


def merge_on_target(pairs: list[str]) -> list[PirEntry]:
    """Unisce gli allineamenti a coppie bersaglio-template (PIR di `align2d`) in un allineamento multiplo ancorato
    al bersaglio: ogni template ha di fronte a ogni residuo del bersaglio lo stesso residuo (o gap) del suo
    allineamento a coppie. I residui di un template senza corrispondente nel bersaglio (inserzioni) vanno in colonne
    con un gap nel bersaglio, prima del residuo del bersaglio che li segue; le inserzioni di template diversi nello
    stesso punto condividono le colonne, allineate a sinistra (MODELLER non ricava vincoli dalle colonne senza il
    bersaglio). Righe: i template nell'ordine dato, poi il bersaglio (come `salign`).
    """
    parsed = [_split(text) for text in pairs]
    target = parsed[0][0]
    letters = [c for c in target.sequence if c.isalpha()]
    n = len(letters)
    # per ogni template: residuo di fronte a ogni residuo del bersaglio e inserzioni prima di ognuno (n + 1 punti)
    aligned: list[list[str]] = []
    inserts: list[list[str]] = []
    for tgt, (tmpl,) in parsed:
        if [c for c in tgt.sequence if c.isalpha()] != letters:
            raise ValueError(f"la sequenza del bersaglio non coincide negli allineamenti a coppie ({tmpl.code})")
        row = ["-"] * n
        ins = [""] * (n + 1)
        k = 0
        for a, b in zip(tgt.sequence, tmpl.sequence):
            if a.isalpha():
                row[k] = b if b.isalpha() else "-"
                k += 1
            elif b.isalpha():
                ins[k] += b
        aligned.append(row)
        inserts.append(ins)
    rows = [[] for _ in parsed]
    target_row: list[str] = []
    for k in range(n + 1):
        width = max(len(ins[k]) for ins in inserts)
        if width:
            target_row.append("-" * width)
            for i, ins in enumerate(inserts):
                rows[i].append(ins[k].ljust(width, "-"))
        if k < n:
            target_row.append(letters[k])
            for i, row in enumerate(aligned):
                rows[i].append(row[k])
    out = [PirEntry(code=tmpl.code, kind=tmpl.kind, fields=list(tmpl.fields), sequence="".join(rows[i]))
           for i, (_tgt, (tmpl,)) in enumerate(parsed)]
    out.append(PirEntry(code=target.code, kind=target.kind, fields=list(target.fields), sequence="".join(target_row)))
    return out


def choose(salign: Optional[str], pairs: list[str]) -> dict:
    """Allineamento da dare a MODELLER: {"strategy": "salign" | "pairwise", "text", "message", "templates"}.

    `templates`: per ogni template (codice) i residui identici al bersaglio con salign e con align2d.
    """
    pair_stats = {}
    for text in pairs:
        target, templates = _split(text)
        if target is None or len(templates) != 1:
            continue
        pair_stats[templates[0].code] = identical_pairs(target.sequence, templates[0].sequence)[1]
    stats = [{"code": code, "pairwise": same, "salign": None} for code, same in pair_stats.items()]
    can_merge = bool(pairs) and len(pair_stats) == len(pairs) and mergeable(pairs)
    target, templates = _split(salign or "")
    if target is None or not templates:
        if not can_merge:
            raise ValueError("salign non riuscito e allineamenti a coppie non utilizzabili")
        return {"strategy": "pairwise", "text": format_pir(merge_on_target(pairs)), "templates": stats,
                "message": "Allineamento strutturale dei template (salign) non riuscito: si usa l'allineamento di "
                           "ogni template sul bersaglio (align2d), unito sul bersaglio."}
    by_code = {t.code: t for t in templates}
    displaced = []
    for row in stats:
        entry = by_code.get(row["code"])
        row["salign"] = identical_pairs(target.sequence, entry.sequence)[1] if entry is not None else 0
        if row["pairwise"] >= MIN_PAIR_IDENTICAL and row["salign"] < MIN_SALIGN_RATIO * row["pairwise"]:
            displaced.append(row)
    if displaced and can_merge:
        detail = ", ".join(f"{r['code']} ({r['salign']} residui identici al bersaglio contro {r['pairwise']} "
                           "dell'allineamento a coppie)" for r in displaced)
        return {"strategy": "pairwise", "text": format_pir(merge_on_target(pairs)), "templates": stats,
                "message": f"L'allineamento strutturale dei template (salign) ha spostato {detail}: si usa "
                           "l'allineamento di ogni template sul bersaglio (align2d), unito sul bersaglio."}
    message = "Allineamento dei template con salign, coerente con gli allineamenti a coppie di ogni template."
    if displaced:
        message = ("Allineamento dei template con salign: " + ", ".join(r["code"] for r in displaced)
                   + " perde residui identici rispetto all'allineamento a coppie, ma template a più catene o con "
                     "ligandi non si possono unire sul bersaglio: verifica l'allineamento.")
    return {"strategy": "salign", "text": salign, "templates": stats, "message": message}
