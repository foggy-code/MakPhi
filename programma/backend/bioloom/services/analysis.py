"""Analisi strutturale (F7): job `analysis` con interazioni, interfacce fra catene e conservazione sulla struttura.

La logica è in `domain/interactions.py`, `domain/interfaces.py` e `domain/conservation.py`; qui ci sono la
risoluzione della struttura e dell'allineamento, il job e la ricerca dell'analisi più recente di una struttura (per
la pagina Qualità e per il report del modello).

**Perché un job a parte e non un'opzione del job `quality`:**

- il rapporto di qualità non cambia (voto, consenso e i `<modello>.quality.json` già salvati restano quelli);
- l'allineamento multiplo della conservazione è un ingresso solo di questa analisi, che si può rilanciare con un
  altro allineamento senza rifare DOPE, PROCHECK e ProSA;
- è un calcolo Python breve che l'utente aspetta davanti allo schermo: priorità 0 in `core/jobs.PRIORITY` (come
  `quality`), pool "cpu". Nessun processo esterno, salvo l'allineamento multiplo di sequenze non allineate
  (`engines/align.multiple`, con i suoi processi sotto il budget di CPU e la cache dei calcoli).

**Parametri:** `source` (template | job | upload, come la qualità); `chains` (catene per la conservazione; vuoto =
tutte); `msa` facoltativo: `{"job_id"}` di un job `align` completato, oppure `{"text"}` con un allineamento o con
sequenze in un formato qualunque di `domain/fileformats` (FASTA, Clustal, Stockholm, …: righe già allineate si usano
così, sequenze di lunghezza diversa si allineano prima con `engines/align.multiple`), più `row` (riga della sequenza
della struttura; vuota = scelta per identità).

Il risultato non passa dalla cache dei calcoli: l'analisi di 1UBQ richiede circa 0,1 s e quella di una struttura di
5000 atomi pochi secondi (misure in ARCHITETTURA.md), e il risultato resta comunque nel job.
"""

from __future__ import annotations

import json
import sqlite3
import time
from typing import Optional

from ..core import cache
from ..core.errors import AppError, Conflict
from ..core.jobs import JobContext, JobManager
from ..domain import conservation, fileformats, interactions, interfaces, sequences, structure
from ..domain.sequences import SeqRecord
from ..engines import align
from .compute import ComputeService, describe_source, source_key

KIND = "analysis"
# testo dell'allineamento nei parametri del job (SQLite e params.json): per allineamenti più grandi si usa un job
MAX_MSA_TEXT = 10 * 1024 * 1024
MAX_UNALIGNED = 200  # come l'allineamento multiplo della pagina Allineamenti
LOOKUP_LIMIT = 200  # analisi completate esaminate per trovare la più recente di una struttura


def _records(text: str) -> tuple[list[str], list[str], bool, dict[str, str]]:
    """(nomi univoci, sequenze, già allineate, alias) da un testo in un formato qualunque di `domain/fileformats`.

    I nomi sono sanificati (`sanitize_code`, poi resi univoci): `alias` porta dal nome del file (intestazione intera,
    primo token, forma sanificata) al nome univoco, così la riga indicata con il nome del FASTA si ritrova. Con nomi
    ripetuti vale la prima occorrenza."""
    parsed = fileformats.parse_records(text.encode("utf-8"), "allineamento")
    names: list[str] = []
    rows: list[str] = []
    aliases: dict[str, str] = {}
    used: set[str] = set()
    for k, rec in enumerate(parsed["records"]):
        raw = str(rec.get("name") or "").strip()
        base = sequences.sanitize_code(rec.get("name"), default=f"seq{k + 1}")
        name, n = base, 2
        while name in used:
            name, n = f"{base}_{n}", n + 1
        used.add(name)
        names.append(name)
        full = f"{raw} {rec.get('description') or ''}".strip()
        for alias in (full, raw, raw.split()[0] if raw.split() else "", base):
            if alias:
                aliases.setdefault(alias, name)
        rows.append(str(rec.get("aligned") or rec.get("sequence") or "").upper())
    aligned = bool(parsed["aligned"]) and len({len(r) for r in rows}) == 1
    if not aligned and len({len(r) for r in rows}) == 1 and all(r.isalpha() for r in rows):
        aligned = True  # stessa lunghezza senza gap: un allineamento senza inserzioni
    if not aligned:
        rows = ["".join(c for c in r if c.isalpha()) for r in rows]
    return names, rows, aligned, aliases


def _resolve_row(row: Optional[str], names: list[str], aliases: Optional[dict[str, str]] = None) -> Optional[str]:
    """Nome della riga indicata dall'utente fra quelli dell'allineamento (None = scelta automatica).

    Vale il nome dell'allineamento, il nome originale del file (intestazione intera o primo token) oppure la sua forma
    sanificata (`sp|P0CG47|UBB_HUMAN` → `sp_P0CG47_UBB_HUMAN`, come i nomi delle righe). Errore chiaro altrimenti."""
    row = str(row or "").strip()
    if not row:
        return None
    aliases = aliases or {}
    for candidate in (row, row.split()[0], sequences.sanitize_code(row, default="")):
        if candidate in names:
            return candidate
        if candidate in aliases:
            return aliases[candidate]
    shown = ", ".join(names[:8]) + (" …" if len(names) > 8 else "")
    raise AppError(f"La riga {row} non è nell'allineamento (righe: {shown}).", code="conservation_invalid")


class AnalysisService:
    def __init__(self, jobs: JobManager, compute: ComputeService) -> None:
        self.jobs = jobs
        self.compute = compute

    def register(self) -> None:
        self.jobs.register(KIND, self.handle)

    # ------------------------------------------------------------------------------ richiesta
    def validate(self, params: dict) -> None:
        """Errori immediati, prima di accodare il job: struttura, job di allineamento, testo dell'allineamento."""
        self.compute.resolve_structure(params.get("source") or {})
        msa = params.get("msa")
        if not msa:
            return
        aliases: dict[str, str] = {}
        if msa.get("job_id"):
            names, rows, _description = self._job_alignment(str(msa["job_id"]))
            aligned = True
        elif msa.get("text"):
            names, rows, aligned, aliases = _records(str(msa["text"]))
            if len(rows) < 2:
                raise AppError("Per la conservazione servono almeno due sequenze.", code="conservation_invalid")
            if aligned:
                conservation.validate(names, rows)
            elif len(rows) > MAX_UNALIGNED:
                raise AppError(f"Da allineare al massimo {MAX_UNALIGNED} sequenze: fornisci un allineamento già "
                               "calcolato.", code="conservation_invalid")
            else:
                for name, seq in zip(names, rows):
                    sequences.validate_sequence(seq, "protein", name)
        else:
            raise AppError("Indica un job di allineamento oppure le sequenze per la conservazione.",
                           code="conservation_invalid")
        # riga indicata: deve esserci (con il nome dell'allineamento o quello del file) e avere dei residui; le
        # sequenze non allineate mantengono i loro nomi nell'allineamento multiplo
        row = _resolve_row(msa.get("row"), names, aliases)
        if row is not None and aligned:
            conservation.row_index(names, [str(r).upper() for r in rows], row)

    def _job_alignment(self, job_id: str) -> tuple[list[str], list[str], str]:
        job = self.jobs.get(job_id)
        if job["kind"] != "align":
            raise AppError(f"Il job {job_id} non è un allineamento.", code="conservation_invalid")
        if job["status"] != "completed" or not isinstance(job.get("result"), dict):
            raise Conflict("L'allineamento non è completato: la conservazione non è ancora calcolabile.")
        result = job["result"]
        ids, aligned = list(result.get("ids") or []), list(result.get("aligned") or [])
        if len(aligned) < 2 or len(ids) != len(aligned):
            raise AppError(f"Il job {job_id} non contiene un allineamento di almeno due sequenze.",
                           code="conservation_invalid")
        return [str(i) for i in ids], [str(s) for s in aligned], \
            f"job di allineamento {job_id}: {result.get('method') or result.get('mode') or 'allineamento'}"

    def _alignment(self, ctx: JobContext, msa: dict) -> tuple[list[str], list[str], str, dict[str, str]]:
        """(nomi, righe allineate, descrizione, alias dei nomi originali) dell'allineamento della conservazione."""
        if msa.get("job_id"):
            return (*self._job_alignment(str(msa["job_id"])), {})
        names, rows, aligned, aliases = _records(str(msa.get("text") or ""))
        if aligned:
            return names, rows, "allineamento fornito", aliases
        if len(rows) > MAX_UNALIGNED:
            raise AppError(f"Da allineare al massimo {MAX_UNALIGNED} sequenze.", code="conservation_invalid")
        records = []
        for name, seq in zip(names, rows):
            clean, _kind = sequences.validate_sequence(seq, "protein", name)
            records.append(SeqRecord(name, clean))
        ctx.log(f"Sequenze non allineate: allineamento multiplo di {len(records)} sequenze")
        res = align.multiple(records, ctx, tool=str(msa.get("tool") or "auto"),
                             use_cache=bool(msa.get("use_cache", True)))
        return (list(res["ids"]), list(res["aligned"]), f"allineamento multiplo calcolato: {res.get('method')}",
                aliases)

    # ------------------------------------------------------------------------------ job
    def handle(self, ctx: JobContext, p: dict) -> dict:
        source = p.get("source") or {}
        path, label = self.compute.resolve_structure(source)
        fingerprint = cache.content_hash(path)  # confrontata dal report con quella del modello
        started = time.perf_counter()
        ctx.progress(0.05, "Lettura della struttura")
        st = structure.load_structure(path, path.stem)
        model = structure.first_model(st)
        stats = structure.structure_stats(model)
        if stats["residues"] == 0:
            raise AppError("La struttura non contiene residui amminoacidici con C-alfa.")
        timings: dict[str, float] = {"lettura": time.perf_counter() - started}
        warnings: list[str] = []
        if len(st) > 1:
            warnings.append(f"La struttura ha {len(st)} modelli: analizzato il primo.")

        ctx.progress(0.1, "Legami idrogeno, ponti salini, disolfuri e interazioni aromatiche")
        t = time.perf_counter()
        inter = interactions.analyze(model)
        timings["interazioni"] = time.perf_counter() - t
        ctx.check()
        ctx.progress(0.35, "Interfacce fra catene (SASA)")
        t = time.perf_counter()
        iface = interfaces.analyze(model, inter, check=ctx.check)
        timings["interfacce"] = time.perf_counter() - t
        cons = None
        msa = p.get("msa")
        if msa:
            ctx.progress(0.7, "Allineamento per la conservazione")
            t = time.perf_counter()
            ids, rows, description, aliases = self._alignment(ctx, msa)
            ctx.progress(0.85, "Conservazione per residuo")
            cons = conservation.compute(model, ids, rows, chains=list(p.get("chains") or []) or None,
                                        row=_resolve_row(msa.get("row"), ids, aliases), source=description,
                                        check=ctx.check)
            timings["conservazione"] = time.perf_counter() - t
        total = time.perf_counter() - started
        ctx.log("Analisi strutturale in {:.2f} s ({} s)".format(
            total, ", ".join(f"{k} {v:.2f}" for k, v in timings.items())))
        s = inter["summary"]
        ctx.log(f"{s['hbonds']} legami idrogeno, {s['salt_bridges']} ponti salini, {s['disulfides']} ponti disolfuro, "
                f"{s['pi_stacking']} π-stacking, {s['cation_pi']} catione-π; {len(iface['pairs'])} interfacce")
        return {
            "source": source, "label": label, "source_sha256": fingerprint,
            "structure": {"file": path.name, **stats, "chains_detail": structure.chain_summaries(model)},
            "interactions": inter, "interfaces": iface, "conservation": cons, "warnings": warnings,
        }

    # ------------------------------------------------------------------------------ ricerca
    def latest(self, source: dict, fingerprint: Optional[str] = None) -> Optional[dict]:
        """Job `analysis` completato più recente sulla stessa struttura (stessa sorgente e, se entrambe le impronte
        ci sono, stesso contenuto del file), oppure None."""
        key = source_key(source)
        if key == ("?",):
            return None
        for row in self._sources():
            if source_key(row["source"]) != key:
                continue
            try:
                job = self.jobs.get(row["id"])
            except AppError:  # eliminato nel frattempo
                continue
            result = job.get("result")
            if not isinstance(result, dict):
                continue
            if fingerprint and result.get("source_sha256") and result["source_sha256"] != fingerprint:
                continue  # stesso nome, file cambiato dopo l'analisi
            return job
        return None

    def active(self, source: dict) -> Optional[dict]:
        """Job `analysis` in coda o in corso più recente sulla stessa sorgente, oppure None: la pagina Qualità lo
        riprende a seguire (cambio di scheda, pagina ricaricata) invece di proporre di lanciarne un altro."""
        key = source_key(source)
        if key == ("?",):
            return None
        for row in self._sources("status IN ('queued','running')", "created_at DESC"):
            if source_key(row["source"]) != key:
                continue
            try:
                return self.jobs.get(row["id"])
            except AppError:  # eliminato nel frattempo
                continue
        return None

    def _sources(self, where: str = "status='completed'", order: str = "finished_at DESC") -> list[dict]:
        """(id, sorgente) delle analisi nello stato `where` (predefinito: completate), dalla più recente. Si legge
        solo `params.source` (json_extract): i parametri possono contenere un allineamento incollato fino a 10 MB.
        Senza le funzioni JSON di SQLite si leggono i parametri interi. `where` e `order` sono costanti del modulo."""
        sql = f"FROM jobs WHERE kind=? AND {where} ORDER BY {order}, rowid DESC LIMIT ?"
        try:
            rows = self.jobs.db.query(f"SELECT id, json_extract(params, '$.source') AS source {sql}",
                                      (KIND, LOOKUP_LIMIT))
        except sqlite3.OperationalError:
            return [{"id": r["id"], "source": (r.get("params") or {}).get("source")}
                    for r in self.jobs.db.query(f"SELECT id, params {sql}", (KIND, LOOKUP_LIMIT))]
        out = []
        for r in rows:
            try:
                src = json.loads(r["source"]) if isinstance(r.get("source"), str) else None
            except ValueError:
                src = None
            out.append({"id": r["id"], "source": src})
        return out


def report_summary(job: dict) -> Optional[dict]:
    """Riepilogo per il report del modello: tutte le interazioni (legami idrogeno, ponti salini, disolfuri,
    aromatiche), le interfacce fra catene e la conservazione. None se il job non ha il risultato delle interazioni."""
    result = job.get("result") if isinstance(job.get("result"), dict) else {}
    inter = result.get("interactions") if isinstance(result.get("interactions"), dict) else None
    if inter is None:
        return None
    iface = result.get("interfaces") if isinstance(result.get("interfaces"), dict) else {}

    def rows(value: object) -> list[dict]:
        return [d for d in value or [] if isinstance(d, dict)] if isinstance(value, list) else []

    cons = result.get("conservation")
    return {"job_id": job.get("id"), "finished_at": job.get("finished_at"), "source": describe_source(result.get("source")),
            "disulfides": rows(inter.get("disulfides")), "interfaces": rows(iface.get("pairs")),
            "method": iface.get("method"), "interface_note": iface.get("note"), "min_delta": iface.get("min_delta"),
            "summary": inter.get("summary") or {}, "hydrogens": inter.get("hydrogens"),
            "hbonds": rows(inter.get("hbonds")), "salt_bridges": rows(inter.get("salt_bridges")),
            "aromatic": rows(inter.get("aromatic")),
            "conservation": cons if isinstance(cons, dict) else None,
            "warnings": [str(w) for w in result.get("warnings") or []]}
