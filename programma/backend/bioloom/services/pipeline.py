"""Pipeline batch: da un multi-FASTA a modelli validati, un elemento per sequenza.

Con template fissi e `fixed_combination` "each" (pipeline M × N, `is_cross`) ogni sequenza si modella su ogni template,
uno alla volta: un elemento per coppia, `idx` = sequenza × N + template (`pair_of`), nome `<sequenza>__<template>`
(cartella `items/<idx>_<sequenza>__<template>/`). Senza colonne nuove: sequenza e template si ricavano dalla posizione.
Quando tutte le coppie di una sequenza sono concluse, il modello con lo zDOPE più basso diventa il migliore della
sequenza (`metrics.best_in_sequence`, `_close_sequences`). L'analisi di qualità, se richiesta, si fa su ogni coppia
(il modello migliore per DOPE fra quelli di MODELLER della coppia, come nella pipeline normale).

Ogni elemento è una riga di `pipeline_items` (interrogabile con paginazione e ordinamento lato
server); il job padre esegue gli elementi in parallelo con un pool dedicato e aggiorna il progresso.

Ripresa: una pipeline fallita, annullata o interrotta dal riavvio, oppure completata con elementi falliti,
si riprende (`resume`) rimettendo in attesa solo gli elementi non conclusi (o solo i falliti); quelli
completati restano come sono, con i loro file già salvati, e l'esecuzione elabora soltanto gli elementi in
attesa. Un elemento diventa 'completed' solo dopo che i suoi file sono arrivati nella cartella definitiva.

Tempo residuo (`eta`): mediana della durata degli elementi conclusi nell'esecuzione corrente × elementi
rimanenti / parallelismo effettivo (core/eta.py). Ogni cambiamento di un elemento viene pubblicato sulle
notifiche del JobManager, così la tabella dell'interfaccia si aggiorna dal flusso SSE dell'elenco dei job.
"""

from __future__ import annotations

import csv
import io
import json
import sqlite3
import threading
import zipfile
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

from ..core import cache
from ..core import eta as eta_estimate
from ..core.db import Database, encode_json, now_iso
from ..core.errors import AppError, Conflict, NotFound
from ..core.events import ITEM
from ..core.jobs import REQUEUABLE, JobCancelled, JobContext, JobManager, requeue_error
from ..core.storage import remove_tree, safe_join
from ..domain import fileformats, ligands, sequences, template_rank
from ..domain.report import alignment_identity
from ..domain.sequences import SeqRecord
from ..engines import quality, search
from .compute import ComputeService, PreparedTemplates, check_length, model_remarks
from .reports import ReportService

# Scelta dei template: "auto" = il migliore della ricerca, "fixed" = scelti dall'utente, "multi" = fino a
# `max_templates` template complementari della ricerca (domain/template_rank.select_complementary)
TEMPLATE_MODES = ("auto", "fixed", "multi")
# Uso dei template fissi: "combined" = un modello per sequenza con tutti i template insieme; "each" = un modello per
# ogni coppia sequenza × template (M × N elementi, vedi `is_cross`)
FIXED_COMBINATIONS = ("combined", "each")
PAIR_SEP = "__"  # nome dell'elemento di una coppia: <sequenza>__<template> (nomi sanificati: mai "__" al loro interno)
TERMINAL_ITEM = {"completed", "failed", "cancelled", "filtered"}
# Elementi che una ripresa rielabora, per ambito: "incomplete" = tutto ciò che non è completato o filtrato
# (falliti, annullati, interrotti, in corso o in attesa); "failed" = solo i falliti.
RESUME_WHERE = {
    "incomplete": "status NOT IN ('completed','filtered')",
    "failed": "status='failed'",
}
# Sequenze per pipeline: abbastanza per un lavoro da server (es. 50.000 sequenze × 6 template), con margine; le stesse
# che si leggono da un file
MAX_SEQUENCES = fileformats.MAX_RECORDS
# Elementi elaborati insieme da una pipeline: il tetto vero sono i job di calcolo in parallelo delle Impostazioni
MAX_PARALLEL = 256
# Output dei programmi esterni di un elemento, nella sua cartella (JobContext.sub, output_log)
ITEM_OUTPUT_LOG = "programmi.log"
# Pipeline di cui si tengono in memoria i parametri (`_params`)
PARAMS_CACHE_SIZE = 32
RESUME_LABEL = {"incomplete": "elementi non conclusi", "failed": "solo i falliti"}
# Stati da cui si riprende una pipeline: anche completata, se ha elementi falliti da riprovare (un errore
# temporaneo di rete o di RCSB non obbliga a rilanciare tutte le sequenze).
RESUMABLE = REQUEUABLE | {"completed"}
# Un elemento rimesso in attesa torna com'era appena creato (nome e sequenza restano).
RESET_ITEM = ("status='pending', stage='', template=NULL, template_source=NULL, identity=NULL, coverage=NULL, "
              "evalue=NULL, twilight=0, best_model=NULL, dope=NULL, ga341=NULL, grade=NULL, grade_score=NULL, "
              "rama_favored=NULL, clashscore=NULL, metrics='{}', error=NULL, started_at=NULL, finished_at=NULL")
# Stati con cui un elemento conta come elaborato nel progresso (anche un fallimento è un esito).
PROCESSED_ITEM = ("completed", "filtered", "failed")
# Elementi che l'esecuzione in corso deve ancora concludere: in attesa o in lavorazione.
OPEN_ITEM = ("pending", "running", "searching", "modeling")
ETA_SAMPLES = 1000  # elementi conclusi più recenti da cui si stima la durata tipica (tempo residuo)
PAIRS_LISTED = 1000  # M × N: oltre queste sequenze le pagine degli elementi non ne elencano i nomi (filtro per nome)
FASTA_SHOWN = 20_000  # caratteri del FASTA nelle risposte dell'API sul job (`public_job`)
SORTABLE = {"idx", "name", "status", "identity", "coverage", "dope", "ga341", "grade_score", "rama_favored",
            "clashscore", "template"}
ITEM_COLUMNS = ("idx, name, length(sequence) AS length, status, stage, template, template_source, identity, "
                "coverage, evalue, twilight, best_model, dope, ga341, grade, grade_score, rama_favored, clashscore, "
                "metrics, error, started_at, finished_at")


class PipelineService:
    def __init__(self, db: Database, jobs: JobManager, compute: ComputeService) -> None:
        self.db = db
        self.jobs = jobs
        self.compute = compute
        self._params_cache: dict[str, dict] = {}
        self._params_lock = threading.Lock()
        jobs.register("pipeline", self.handle, eta=self.eta)

    # ------------------------------------------------------------------------------ sottomissione
    def submit(self, params: dict, project_id: Optional[str]) -> dict:
        records = self.validate(params)
        from ..engines import modeller

        # modalità di MODELLER scritta nei parametri: una ripresa costruisce gli elementi rimanenti come i primi
        # anche se il predefinito cambia (`options_for_run`)
        raw = params.get("options") or {}
        options = {**raw, "parallel": modeller.parse_options(raw).parallel}
        stored = {**params, "options": options,
                  "sequences": [{"name": r.name, "length": len(r.sequence)} for r in records]}
        if is_cross(params):
            # M × N: elemento idx = sequenza × N + template (`pair_of`), in ordine di sequenza
            labels = template_labels(params.get("templates") or [])
            stored["template_labels"] = labels
            rows = [(r.name + PAIR_SEP + label, r.sequence) for r in records for label in labels]
            title = params.get("name") or f"Pipeline di {_seqs(len(records))} × {_tpls(len(labels))}"
        else:
            rows = [(r.name, r.sequence) for r in records]
            title = params.get("name") or f"Pipeline di {_seqs(len(records))}"

        def prepare(job_id: str, _workdir: Path) -> None:
            self.db.executemany(
                "INSERT INTO pipeline_items(job_id, idx, name, sequence, status) VALUES (?,?,?,?, 'pending')",
                [(job_id, i, name, seq) for i, (name, seq) in enumerate(rows)])

        return self.jobs.submit("pipeline", stored, project_id=project_id, title=title, prepare=prepare)

    def validate(self, params: dict) -> list[SeqRecord]:
        """Controlli della sottomissione, prima della coda (anche per i controlli preventivi, services/preflight.py):
        sequenze, opzioni di modellazione e modalità dei template. Restituisce le sequenze con i nomi unici."""
        records = self._records(params)
        opts = params.get("options") or {}
        from ..engines import modeller

        parsed = modeller.parse_options(opts)  # validazione anticipata
        mode = params.get("template_mode", "auto")
        if mode not in TEMPLATE_MODES:
            raise AppError(f"Modalità dei template sconosciuta: '{mode}' (ammesse: {', '.join(TEMPLATE_MODES)}).")
        if mode == "fixed" and not params.get("templates"):
            raise AppError("In modalità 'template fisso' seleziona almeno un template.")
        combination = params.get("fixed_combination") or "combined"
        if combination not in FIXED_COMBINATIONS:
            raise AppError(f"Uso dei template sconosciuto: '{combination}' (ammessi: {', '.join(FIXED_COMBINATIONS)}).")
        if is_cross(params):
            seen: set[tuple] = set()
            for t in params.get("templates") or []:
                key = (str(t.get("id") or "").upper(), tuple(sorted(str(c) for c in t.get("chains") or [])))
                if key in seen:
                    raise AppError(f"Il template {t.get('id')} è selezionato più volte con le stesse catene: ogni "
                                   "template dà un modello per sequenza, due uguali darebbero lo stesso modello.")
                seen.add(key)
        if mode == "multi":
            max_templates(params)  # errore subito, prima della coda
        if mode != "fixed" and parsed.ligands:
            # i template della ricerca non si conoscono prima: vale solo la scelta predefinita dei ligandi (hetatm)
            raise AppError("Con la ricerca automatica dei template non si possono scegliere i singoli ligandi: usa "
                           "l'opzione hetatm (metalli, cofattori e ligandi, additivi esclusi) oppure un template "
                           "fisso.", code="invalid_ligand")
        return records

    # ------------------------------------------------------------------------------ ripresa
    def resume(self, job_id: str, scope: str = "incomplete") -> dict:
        """Riprende una pipeline terminata rielaborando solo gli elementi dell'ambito scelto.

        Si riprendono le pipeline fallite o annullate e quelle completate con elementi falliti. Nell'ambito
        rientrano anche gli elementi completati il cui modello manca nei risultati definitivi (salvataggio
        mai riuscito, con una versione che segnava l'elemento completato prima di spostarne i file).
        Gli elementi scelti tornano in attesa, azzerati, e i loro file già salvati vengono eliminati
        (dopo il passaggio atomico a 'queued', così due richieste concorrenti non si sovrappongono).
        """
        where = RESUME_WHERE.get(scope)
        if where is None:
            raise AppError(f"Ambito di ripresa sconosciuto: '{scope}' (ammessi: {', '.join(RESUME_WHERE)}).")
        job = self.jobs.get(job_id, include_result=False)
        if job["kind"] != "pipeline":
            raise AppError(f"Ripresa non supportata per i job di tipo '{job['kind']}': "
                           "si possono riprendere solo le pipeline.", code="resume_unsupported")
        blocked = requeue_error(job, RESUMABLE)
        if blocked is not None:
            raise blocked
        # un file per elemento completato, solo qui (non a ogni lettura dello stato del job)
        lost = len(self._missing_models(job_id, self.jobs.workdir(job_id)))

        def count(current: dict) -> int:
            """Elementi da rielaborare; rifiuta la ripresa se non ce ne sono (ripetuto sotto il lock del gestore)."""
            n = lost + int(self.db.scalar(f"SELECT COUNT(*) FROM pipeline_items WHERE job_id=? AND {where}",
                                          (job_id,)))
            if n:
                return n
            if current["status"] == "completed":
                raise Conflict("Il job è già completato: nessuna sequenza fallita da riprovare.")
            raise Conflict("Nessun elemento fallito da riprovare." if scope == "failed" else
                           "Nessun elemento da riprendere: tutte le sequenze sono già concluse.")

        todo = count(job)

        def prepare(_job_id: str, final_dir: Path) -> None:
            # rilevati di nuovo dopo il passaggio a 'queued': nessuna esecuzione li sta cambiando
            missing = self._missing_models(job_id, final_dir)
            with self.db.transaction() as conn:
                rows = [dict(r) for r in conn.execute(f"SELECT idx, name FROM pipeline_items WHERE job_id=? "
                                                      f"AND {where}", (job_id,)).fetchall()]
                conn.execute(f"UPDATE pipeline_items SET {RESET_ITEM} WHERE job_id=? AND {where}", (job_id,))
                conn.executemany(f"UPDATE pipeline_items SET {RESET_ITEM} WHERE job_id=? AND idx=? "
                                 "AND status='completed'", [(job_id, r["idx"]) for r in missing])
            self.jobs.events.publish(job_id, ITEM)  # più elementi insieme: la tabella si ricarica
            # niente file vecchi accanto a quelli nuovi: la cartella dell'elemento riparte vuota
            for row in rows + missing:
                remove_tree(safe_join(final_dir, "items", f"{row['idx']:04d}_{row['name']}"))

        detail = RESUME_LABEL[scope]
        if lost:
            detail += f"; {lost} {'completata' if lost == 1 else 'completate'} ma senza il file del modello"
        return self.jobs.requeue(job_id, prepare=prepare, statuses=RESUMABLE, check=count,
                                 note=f"{(_models if is_cross(self._params(job_id)) else _seqs)(todo)} da rielaborare ({detail})")

    def _missing_models(self, job_id: str, final_dir: Path) -> list[dict]:
        """Elementi completati il cui modello non è nella cartella definitiva del job."""
        rows = self.db.query("SELECT idx, name, best_model FROM pipeline_items WHERE job_id=? AND status='completed' "
                             "ORDER BY idx", (job_id,))
        return [{"idx": r["idx"], "name": r["name"]} for r in rows if not _saved(final_dir, r["best_model"])]

    def resume_state(self, job: dict) -> dict:
        """Indicazione per l'interfaccia: la pipeline si può riprendere, e con quali elementi.

        Costa una sola query aggregata sulla chiave primaria, e solo per pipeline terminate (fallite,
        annullate o completate: queste ultime si riprendono se hanno elementi falliti).
        """
        if job.get("kind") != "pipeline" or requeue_error(job, RESUMABLE) is not None:
            return {"resumable": False, "resumable_items": None}
        row = self.db.query_one(
            f"SELECT COUNT(*) AS total, COALESCE(SUM({RESUME_WHERE['incomplete']}), 0) AS incomplete, "
            f"COALESCE(SUM({RESUME_WHERE['failed']}), 0) AS failed FROM pipeline_items WHERE job_id=?", (job["id"],))
        counts = {k: int(row[k]) for k in ("total", "incomplete", "failed")} if row else \
            {"total": 0, "incomplete": 0, "failed": 0}
        return {"resumable": counts["incomplete"] > 0, "resumable_items": counts}

    # ------------------------------------------------------------------------------ tempo residuo
    def eta(self, job: dict, now: datetime) -> Optional[int]:
        """Secondi rimanenti di una pipeline in esecuzione (core/eta.pipeline_eta); None se non stimabile.

        Contano solo gli elementi conclusi (completati, filtrati o falliti: anche un fallimento occupa una
        corsia) nell'esecuzione corrente, cioè avviati dopo il suo inizio (`started_at` del job, riscritto a
        ogni ripresa): dopo una ripresa gli elementi conclusi in precedenza, con durate di un'altra sessione,
        restano fuori. Rimanenti = in attesa o in lavorazione; per questi ultimi si toglie il tempo già speso.
        Durata tipica dagli ultimi ETA_SAMPLES elementi conclusi: con centinaia di migliaia di elementi leggerli tutti a
        ogni elenco dei job costerebbe secondi. Query sull'indice degli elementi per stato, solo per pipeline in
        esecuzione.
        """
        started = job.get("started_at")
        if not started:
            return None
        open_marks, done_marks = ",".join("?" * len(OPEN_ITEM)), ",".join("?" * len(PROCESSED_ITEM))
        remaining = int(self.db.scalar(
            f"SELECT COUNT(*) FROM pipeline_items WHERE job_id=? AND status IN ({open_marks})", (job["id"], *OPEN_ITEM)))
        running = [eta_estimate.seconds_between(r["started_at"], now) for r in self.db.query(
            f"SELECT started_at FROM pipeline_items WHERE job_id=? AND status IN ({open_marks}) AND status<>'pending'",
            (job["id"], *OPEN_ITEM))]
        durations = [eta_estimate.seconds_between(r["started_at"], r["finished_at"]) for r in self.db.query(
            f"SELECT started_at, finished_at FROM pipeline_items WHERE job_id=? AND status IN ({done_marks}) AND "
            "started_at >= ? ORDER BY finished_at DESC LIMIT ?", (job["id"], *PROCESSED_ITEM, started, ETA_SAMPLES))]
        parallel = effective_parallel(self._params(job["id"]), self.jobs.settings_provider().effective_workers())
        return eta_estimate.pipeline_eta(durations, remaining, parallel, running)

    @staticmethod
    def _records(params: dict) -> list[SeqRecord]:
        raw = params.get("fasta") or ""
        records = sequences.parse_fasta(raw) if raw.strip() else [
            SeqRecord(s.get("name", f"seq{i + 1}"), s.get("sequence", "")) for i, s in enumerate(params.get("records") or [])]
        if not records:
            raise AppError("Nessuna sequenza fornita alla pipeline.")
        if len(records) > MAX_SEQUENCES:
            raise AppError(f"Massimo {MAX_SEQUENCES:,} sequenze per pipeline (ne sono arrivate {len(records):,}).".replace(",", "."))
        # nomi unici, anche quelli generati: "x", "x", "x_2" → "x", "x_2", "x_2_2" (nell'archivio ZIP i file di un
        # elemento prendono il suo nome)
        used: set[str] = set()
        counters: dict[str, int] = {}
        out = []
        for i, r in enumerate(records):
            seq, _ = sequences.validate_sequence(r.sequence, "protein", r.name or f"sequenza {i + 1}")
            base = name = sequences.sanitize_code(r.name, default=f"seq{i + 1}")
            n = counters.get(base, 1)
            while name in used:
                n += 1
                name = f"{base}_{n}"
            counters[base] = n
            used.add(name)
            out.append(SeqRecord(name, seq))
        return out

    # ------------------------------------------------------------------------------ esecuzione
    def handle(self, ctx: JobContext, p: dict) -> dict:
        # Solo gli elementi in attesa: in una ripresa gli altri sono già conclusi (o esclusi dall'ambito
        # scelto) e restano come sono. Una pipeline nuova ha tutti gli elementi in attesa. Solo gli indici: nome e
        # sequenza si leggono quando l'elemento parte (con centinaia di migliaia di elementi, centinaia di MB).
        items = [int(r["idx"]) for r in self.db.query(
            "SELECT idx FROM pipeline_items WHERE job_id=? AND status='pending' ORDER BY idx", (ctx.job_id,))]
        total = int(self.db.scalar("SELECT COUNT(*) FROM pipeline_items WHERE job_id=?", (ctx.job_id,)))
        already = int(self.db.scalar(
            f"SELECT COUNT(*) FROM pipeline_items WHERE job_id=? AND status IN ({','.join('?' * len(PROCESSED_ITEM))})",
            (ctx.job_id, *PROCESSED_ITEM)))
        parallel = effective_parallel(p, ctx.settings.effective_workers())
        p, mode_note = options_for_run(p, resumed=already > 0)
        if mode_note:
            ctx.log(mode_note)
        cross = is_cross(p)
        unit = _models if cross else _seqs
        noun = "modelli" if cross else "sequenze"
        done_word = "elaborati" if cross else "elaborate"
        # template preparati una volta per esecuzione e riusati dagli elementi con lo stesso template (F1.2)
        prepared = PreparedTemplates(ctx.tmp_dir / "templates")
        done_lock = threading.Lock()
        done = {"n": already}
        if cross:
            n_tpl = len(p.get("templates") or [])
            ctx.log(f"Pipeline di {_seqs(total // max(1, n_tpl))} × {_tpls(n_tpl)} = {_models(total)}, {parallel} in "
                    "parallelo; migliore di ogni sequenza: zDOPE più basso")
        else:
            ctx.log(f"Pipeline di {_seqs(total)}, {parallel} in parallelo")
        ctx.log(f"Output dei programmi di ogni elemento: items/<elemento>/{ITEM_OUTPUT_LOG}")
        skipped = total - already - len(items)
        if len(items) < total:
            ctx.log(f"Ripresa: {unit(len(items))} da elaborare, {already} già {done_word}"
                    + (f", {skipped} lasciat{'i' if cross else 'e'} come sono (annullat{'i' if cross else 'e'})"
                       if skipped else ""))
        # M × N: una sequenza si chiude (migliore per zDOPE) appena le sue coppie sono concluse, una volta per
        # esecuzione
        closing = {"lock": threading.Lock(), "done": set()}

        def finished_one() -> None:
            with done_lock:
                done["n"] += 1
                ctx.progress(done["n"] / max(total, 1), f"{done['n']} di {total} {noun} {done_word}")

        def work(idx: int) -> None:
            item = self.db.query_one("SELECT idx, name, sequence FROM pipeline_items WHERE job_id=? AND idx=?",
                                     (ctx.job_id, idx))
            if item is None:  # job eliminato nel frattempo
                return
            try:
                self._process_item(ctx, p, item, prepared)
            except JobCancelled:
                self._update(ctx.job_id, idx, status="cancelled", stage="Annullato", finished_at=now_iso())
            finally:
                finished_one()
            if cross and not ctx.cancelled:
                self._close_sequences(ctx, p, closing, [pair_of(idx, p)[0]])

        ctx.progress(already / max(total, 1), f"{already} di {total} {noun} {done_word}")
        # al più `window` elementi affidati al pool alla volta (invece di uno per elemento subito): memoria costante
        # e, all'annullamento, nessun elemento in più da avviare
        window = 2 * parallel
        slots = threading.BoundedSemaphore(window)
        errors: list[BaseException] = []

        def release(future) -> None:
            if not future.cancelled() and future.exception() is not None:
                errors.append(future.exception())
            slots.release()

        try:
            with ThreadPoolExecutor(max_workers=parallel, thread_name_prefix=f"pipeline-{ctx.job_id[:6]}") as pool:
                for idx in items:
                    slots.acquire()
                    if ctx.cancelled or errors:
                        slots.release()
                        break
                    pool.submit(work, idx).add_done_callback(release)
                for _ in range(window):  # attesa degli elementi in corso
                    slots.acquire()
                for _ in range(window):
                    slots.release()
                if errors:
                    raise errors[0]
                if ctx.cancelled:  # elementi mai avviati: annullati con un solo aggiornamento
                    self.db.execute("UPDATE pipeline_items SET status='cancelled', stage='Annullato', finished_at=? "
                                    "WHERE job_id=? AND status='pending'", (now_iso(), ctx.job_id))
                    self.jobs.events.publish(ctx.job_id, ITEM, None)
                if cross and not ctx.cancelled:
                    # sequenze non chiuse in questa esecuzione (ripresa che non le tocca, chiusura interrotta): il
                    # migliore si ricalcola
                    pending = [s for s in range(total // max(1, len(p.get("templates") or [])))
                               if s not in closing["done"]]
                    list(pool.map(lambda s: self._close_sequences(ctx, p, closing, [s]), pending))
        finally:
            remove_tree(prepared.root)  # anche senza scratch (cartella del job) non restano copie
        ctx.check()
        counts = {row["status"]: row["n"] for row in self.db.query(
            "SELECT status, COUNT(*) AS n FROM pipeline_items WHERE job_id=? GROUP BY status", (ctx.job_id,))}
        cache_hits = int(self.db.scalar("SELECT COUNT(*) FROM pipeline_items WHERE job_id=? AND metrics LIKE ?",
                                        (ctx.job_id, '%"search_cached_at": "%')))
        if cache_hits:
            ctx.log(f"{cache_hits} " + ("ricerca di template riusata" if cache_hits == 1 else
                                        "ricerche di template riusate") + " dalla cache dei calcoli")
        if prepared.reused:
            ctx.log(f"Template preparati: {prepared.prepared}, riusati da altri elementi {prepared.reused} "
                    f"{'volta' if prepared.reused == 1 else 'volte'}")
        # Una ripresa "solo i falliti" lascia com'erano gli annullati: la pipeline non si dichiara completata
        # (quelle sequenze non sono mai state elaborate) ma resta annullata e riprendibile per quegli elementi.
        left = sum(n for status, n in counts.items() if status not in PROCESSED_ITEM)
        if left:
            one = left == 1
            gone = ("annullato" if one else "annullati") if cross else ("annullata" if one else "annullate")
            raise JobCancelled(f"{'Resta' if one else 'Restano'} {unit(left)} da elaborare "
                               f"({gone} in precedenza): usa «Riprendi» per completarl{'i' if cross else 'e'}.")
        return {"total": total, "counts": counts, "search_cache_hits": cache_hits,
                "templates_prepared": prepared.prepared, "templates_reused": prepared.reused}

    def _params(self, job_id: str) -> dict:
        """Parametri del job (non letti da `jobs.get(include_result=False)`), in sola lettura.

        I parametri di un job non cambiano dopo la sottomissione: si leggono dal database una volta e restano in una
        piccola cache. Con decine di migliaia di sequenze il FASTA nei parametri pesa diversi MB, e il tempo residuo,
        le pagine degli elementi e la matrice li chiedono di continuo mentre la pipeline gira.
        """
        with self._params_lock:
            cached = self._params_cache.get(job_id)
        if cached is not None:
            return cached
        row = self.db.query_one("SELECT params FROM jobs WHERE id=?", (job_id,)) or {}
        params = row["params"] if isinstance(row.get("params"), dict) else {}
        if row:  # un job inesistente non entra nella cache
            with self._params_lock:
                self._params_cache[job_id] = params
                while len(self._params_cache) > PARAMS_CACHE_SIZE:
                    self._params_cache.pop(next(iter(self._params_cache)))
        return params

    def _update(self, job_id: str, idx: int, **fields) -> None:
        if "metrics" in fields:
            fields["metrics"] = encode_json(fields["metrics"])
        sets = ", ".join(f"{k}=?" for k in fields)
        self.db.execute(f"UPDATE pipeline_items SET {sets} WHERE job_id=? AND idx=?", (*fields.values(), job_id, idx))
        self.jobs.events.publish(job_id, ITEM, idx)

    def _process_item(self, ctx: JobContext, p: dict, item: dict,
                      prepared: Optional[PreparedTemplates] = None) -> None:
        job_id, idx, name = ctx.job_id, item["idx"], item["name"]
        ctx.check()
        self._update(job_id, idx, status="running", stage="Avvio", started_at=now_iso())

        def on_progress(_fraction, stage):
            if stage:
                self._update(job_id, idx, stage=stage[:120])

        item_dir = ctx.workdir / "items" / f"{idx:04d}_{name}"
        # output dei programmi (MODELLER, DOPE, PROCHECK…) nella cartella dell'elemento: ~1500 righe per modello,
        # che nel log del job diventerebbero gigabyte con decine di migliaia di elementi
        sub = ctx.sub(name, item_dir, on_progress=on_progress, output_log=item_dir / ITEM_OUTPUT_LOG)
        final: Optional[dict] = None
        try:
            final = self._run_item(ctx, sub, p, item, prepared)
        finally:
            # salvataggio progressivo: i file dell'elemento passano subito dallo scratch ai risultati
            try:
                sub.commit(success=final is not None)
            except OSError as exc:
                if final:  # il modello non è arrivato tra i risultati: l'elemento non è completato
                    self._update(job_id, idx, status="failed", stage="Errore", finished_at=now_iso(),
                                 error=f"Salvataggio dei file dell'elemento non riuscito: {exc}"[:2000])
                    final = None
                sub.log(f"ERRORE: salvataggio dei file dell'elemento non riuscito: {exc}")
        # Completato solo con i file al sicuro: un'interruzione prima di qui lascia l'elemento non concluso,
        # quindi il riavvio lo segna interrotto e la ripresa lo rifà.
        if final:
            self._update(job_id, idx, **final)

    def _run_item(self, ctx: JobContext, sub: JobContext, p: dict, item: dict,
                  prepared: Optional[PreparedTemplates] = None) -> Optional[dict]:
        """Elabora un elemento. None se è fallito (esito già registrato); altrimenti i campi da scrivere dopo
        il salvataggio dei file: lo stato 'completed' con modello e metriche, oppure {} per un elemento
        filtrato (esito già registrato: senza modello, i suoi file non sono essenziali)."""
        job_id, idx, name = ctx.job_id, item["idx"], item["name"]
        # provenienza della ricerca (cache dei calcoli) e template scelti (modalità multi), conservati anche nelle
        # metriche finali
        search_info: dict = {}
        mode = p.get("template_mode", "auto")
        cross = is_cross(p)
        try:
            check_length(item["sequence"], ctx.settings.max_sequence_length)  # impostazione letta all'avvio dell'elemento
            if mode == "fixed":
                templates = p.get("templates") or []
                if cross:  # M × N: il template della coppia
                    templates = [templates[pair_of(idx, p)[1]]]
                self._update(job_id, idx, template=",".join(t.get("id", "") for t in templates),
                             template_source="scelto dall'utente")
            else:
                self._update(job_id, idx, status="searching", stage="Ricerca del template")
                engine = p.get("engine", "rcsb")
                # AlphaFold DB: con un'intestazione FASTA di UniProt (sp|ACC|NOME) l'accessione è già nota e la
                # sequenza non va a NCBI; altrimenti le proteine più simili si cercano con BLAST su Swiss-Prot.
                uniprot = search.uniprot_from_name(name) if engine == "alphafold" else ""
                result = search.search(sub, item["sequence"], engine=engine,
                                       evalue=float(p.get("evalue", 1e-3)), max_hits=25,
                                       databases_dir=self.compute.databases_dir,
                                       use_cache=bool(p.get("use_cache", True)), uniprot=uniprot)
                if result.get("cached"):
                    search_info = {"search_cached_at": result.get("cached_at")}
                    self._update(job_id, idx, metrics=search_info)
                best = search.best_template(result["hits"])
                if best is None:
                    # motore AlphaFold: le note dicono perché (proteine senza modello, EBI non disponibile)
                    error = " ".join(["Nessun template omologo trovato.", *(result.get("notes") or [])])
                    self._update(job_id, idx, status="failed", stage="Nessun template", error=error[:2000],
                                 finished_at=now_iso())
                    return None
                tw = p.get("twilight") or {}
                chosen = [best]
                coverage = best["coverage"]
                if mode == "multi":
                    # template complementari: identità del principale, copertura dell'insieme (unione)
                    selection = template_rank.select_complementary(
                        result["hits"], result.get("query_length") or len(item["sequence"]),
                        max_templates=max_templates(p))
                    by_id = {h.get("template_id"): h for h in result["hits"]}
                    chosen = [by_id[t["template_id"]] for t in selection["templates"] if t["template_id"] in by_id]
                    coverage = selection["coverage"]
                    search_info["templates"] = [
                        {k: t.get(k) for k in ("template_id", "role", "reason", "identity", "coverage", "rank_score",
                                               "predicted")} for t in selection["templates"]]
                    search_info["combined_coverage"] = selection["coverage"]
                    search_info["excluded_templates"] = len(selection["excluded"]) + selection["not_examined"]
                    for t in selection["templates"]:
                        sub.log(f"Template {t['role']} {t['template_id']} (punteggio {t['rank_score']}): {t['reason']}")
                    for e in selection["excluded"][:10]:
                        sub.log(f"Escluso {e['template_id']}: {e['reason']}")
                twilight = best["identity"] < float(tw.get("identity", 25)) or coverage < float(tw.get("coverage", 40))
                self._update(job_id, idx, template=",".join(h["template_id"] for h in chosen),
                             template_source=result["engine"], identity=best["identity"], coverage=coverage,
                             evalue=best["evalue"], twilight=int(twilight),
                             **({"metrics": search_info} if search_info else {}))
                sub.log(f"Template scelto {best['template_id']} (identità {best['identity']}%, "
                        f"copertura {best['coverage']}%"
                        + (f"; modello predetto AlphaFold, pLDDT medio {best.get('plddt')}" if best.get("predicted")
                           else "")
                        + (f"; punteggio {best['rank_score']}" if best.get("rank_score") is not None else "") + ")"
                        + (f" e {len(chosen) - 1} complementari, copertura complessiva {coverage}%"
                           if len(chosen) > 1 else ""))
                if twilight and tw.get("filter", False):
                    self._update(job_id, idx, status="filtered", stage="Zona crepuscolare: saltata",
                                 error="Identità o copertura sotto soglia (filtro attivo).", finished_at=now_iso())
                    return {}
                templates = [{"id": h["pdb_id"], "chains": [h["chain"]]} for h in chosen]
            ctx.check()
            self._update(job_id, idx, status="modeling", stage="Modellazione")
            quality_mode = "best" if p.get("quality", True) else "none"
            # bersaglio con il nome della sequenza anche per le coppie (nel PIR e nei nomi dei modelli)
            target_name = pair_names(item["name"])[0] if cross else name
            res = self.compute.run_modeling(sub, target=SeqRecord(target_name, item["sequence"]),
                                            templates_raw=templates, alignment=None,
                                            options_raw=p.get("options") or {}, quality_mode=quality_mode,
                                            extended_quality=bool(p.get("extended_quality", False)),
                                            template_cache=prepared)
            best_model = next(m for m in res["models"] if m["best"])
            q = best_model.get("quality") or {}
            rel = (sub.workdir / best_model["file"]).relative_to(ctx.workdir).as_posix()
            self._update(job_id, idx, stage="Salvataggio dei file")
            # template AlphaFold: provenienza e residui tolti per pLDDT basso nelle metriche dell'elemento
            predicted = [t for t in res.get("templates") or [] if t.get("predicted")]
            af_info = {"template_predicted": True, "plddt_removed": res.get("plddt_removed", 0)} if predicted else {}
            pair_fields = self._pair_result(sub, p, item, res, best_model) if cross else {}
            return {"status": "completed", "stage": "Completato", "best_model": rel, **pair_fields,
                    "dope": best_model.get("dope"), "ga341": best_model.get("ga341"), "grade": q.get("grade"),
                    "grade_score": q.get("grade_score"), "rama_favored": q.get("rama_favored"),
                    "clashscore": q.get("clashscore"),
                    "metrics": {"zdope": best_model.get("zdope"), "molpdf": best_model.get("molpdf"),
                                "models": len(res["models"]),
                                "modeller_parallel": bool((res.get("options") or {}).get("parallel")),
                                **q, **search_info, **af_info,
                                # M × N: lo zDOPE resta quello di MODELLER, il criterio del migliore della sequenza
                                **({"zdope": best_model.get("zdope")} if cross and best_model.get("zdope") is not None
                                   else {})},
                    "finished_at": now_iso()}
        except JobCancelled:
            raise
        except AppError as exc:
            sub.log(f"ERRORE: {exc.message}")
            self._update(job_id, idx, status="failed", stage="Errore", error=exc.message[:2000], finished_at=now_iso())
        except Exception as exc:  # un elemento non deve fermare l'intera pipeline
            sub.log(f"ERRORE INATTESO: {exc!r}")
            self._update(job_id, idx, status="failed", stage="Errore", error=f"{type(exc).__name__}: {exc}",
                         finished_at=now_iso())
        return None

    # ------------------------------------------------------------------------------ coppie sequenza × template
    def _pair_result(self, sub: JobContext, p: dict, item: dict, res: dict, best_model: dict) -> dict:
        """Coppia M × N modellata: identità e copertura del template sull'allineamento (come il report), zona
        crepuscolare segnalata (mai filtrata: il template l'ha scelto l'utente) e riga di tracciabilità della coppia
        nel PDB, aggiunta alle REMARK scritte da `run_modeling` (voto compreso, se il modello è stato analizzato)."""
        sequence, label = pair_names(item["name"])
        fields: dict = {}
        entries = sequences.parse_pir(res.get("alignment") or "")
        target = next((e.sequence for e in entries if e.kind == "sequence"), "")
        template = next((e.sequence for e in entries if e.is_template), "")
        if target and template:
            identity, coverage = alignment_identity(target, template)
            if identity is not None and coverage is not None:
                tw = p.get("twilight") or {}
                fields.update(identity=round(identity, 1), coverage=round(coverage, 1),
                              twilight=int(identity < float(tw.get("identity", 25))
                                           or coverage < float(tw.get("coverage", 40))))
        lig = res.get("ligands") if isinstance(res.get("ligands"), dict) else {}
        transferred = [ligands.group_label(g["chain"], g["resnum"], g["resname"])
                       for g in lig.get("transferred") or [] if isinstance(g, dict)]
        templates = res.get("templates") or []
        path = sub.workdir / best_model["file"]
        if path.is_file() and templates:
            quality.inject_remarks(path, model_remarks(
                best_model, (res.get("target") or {}).get("code") or sequence, templates, transferred=transferred,
                extra=(pair_remark(sequence, label),)))
        return fields

    def _close_sequences(self, ctx: JobContext, p: dict, closing: dict, seq_indices: list[int]) -> None:
        """Chiude le sequenze indicate le cui coppie sono tutte concluse (nessuna in attesa o in lavorazione), una volta
        per esecuzione: il modello con lo zDOPE più basso (a parità il DOPE, poi l'ordine dei template) diventa il
        migliore della sequenza (`metrics.best_in_sequence`). Una ripresa che rifà delle coppie lo ricalcola."""
        n = len(p.get("templates") or [])
        if not n:
            return
        for s in seq_indices:
            with closing["lock"]:
                if s in closing["done"]:
                    continue
                rows = self.db.query("SELECT idx, name, status, best_model, dope, metrics FROM pipeline_items "
                                     "WHERE job_id=? AND idx>=? AND idx<? ORDER BY idx", (ctx.job_id, s * n, s * n + n))
                if not rows or any(r["status"] in OPEN_ITEM for r in rows):
                    continue
                closing["done"].add(s)
            done = [r for r in rows if r["status"] == "completed" and r.get("best_model")]
            best = min(done, key=_best_key) if done else None
            for r in done:
                metrics = r["metrics"] if isinstance(r.get("metrics"), dict) else {}
                if bool(metrics.get("best_in_sequence")) != (r is best):
                    self._update(ctx.job_id, r["idx"], metrics={**metrics, "best_in_sequence": r is best})
            if best is not None:
                sequence, label = pair_names(best["name"])
                ctx.log(f"Sequenza {sequence}: modello migliore sul template {label} (zDOPE "
                        f"{(best['metrics'] or {}).get('zdope')}, {len(done)} di {len(rows)} coppie completate)")


    # ------------------------------------------------------------------------------ consultazione
    def items(self, job_id: str, *, page: int = 1, size: int = 25, status: Optional[str] = None,
              sort: str = "idx", direction: str = "asc", search_text: str = "", sequence: Optional[int] = None,
              template: Optional[int] = None, best_only: bool = False) -> dict:
        """Una pagina di elementi. Pipeline M × N: filtri per sequenza e per template (indici, dalla posizione
        dell'elemento), solo i migliori di ogni sequenza, e in ogni riga `pair` e `best_in_sequence`; nella risposta
        `pairs` con i nomi delle sequenze e dei template."""
        self.jobs.get(job_id, include_result=False)
        params = self._params(job_id)
        cross = is_cross(params)
        n = max(1, len(params.get("templates") or []))
        where, args = ["job_id=?"], [job_id]
        if status and status != "all":
            where.append("status=?")
            args.append(status)
        if search_text:
            where.append("(name LIKE ? OR template LIKE ?)")
            args += [f"%{search_text}%", f"%{search_text}%"]
        if cross and sequence is not None:
            where.append("idx >= ? AND idx < ?")
            args += [int(sequence) * n, int(sequence) * n + n]
        if cross and template is not None:
            where.append("idx % ? = ?")
            args += [n, int(template)]
        if cross and best_only:
            where.append("metrics LIKE ?")
            args.append('%"best_in_sequence": true%')
        clause = " AND ".join(where)
        total = int(self.db.scalar(f"SELECT COUNT(*) FROM pipeline_items WHERE {clause}", tuple(args)))
        size = max(1, min(size, 500))
        pages = max(1, (total + size - 1) // size)
        page = max(1, min(page, pages))
        col = sort if sort in SORTABLE else "idx"
        order = "DESC" if direction == "desc" else "ASC"
        rows = self.db.query(
            f"SELECT {ITEM_COLUMNS} FROM pipeline_items WHERE {clause} "
            f"ORDER BY ({col} IS NULL), {col} {order}, idx LIMIT ? OFFSET ?", (*args, size, (page - 1) * size))
        counts = {r["status"]: r["n"] for r in self.db.query(
            "SELECT status, COUNT(*) AS n FROM pipeline_items WHERE job_id=? GROUP BY status", (job_id,))}
        out = {"items": rows, "total": total, "page": page, "pages": pages, "size": size, "counts": counts}
        if cross:
            names = pair_axes(params)
            for r in rows:
                _with_pair(r, params, names)
            # i nomi di tutte le sequenze (per il filtro) solo se sono poche: con decine di migliaia peserebbero MB a
            # ogni pagina, e si cerca per nome
            count = len(names["sequences"])
            out["pairs"] = {**names, "sequences": names["sequences"] if count <= PAIRS_LISTED else [],
                            "sequence_count": count}
        return out

    def item(self, job_id: str, idx: int) -> dict:
        row = self.db.query_one(f"SELECT {ITEM_COLUMNS}, sequence FROM pipeline_items WHERE job_id=? AND idx=?",
                                (job_id, idx))
        if not row:
            raise NotFound("Elemento della pipeline non trovato.")
        params = self._params(job_id)
        if is_cross(params):
            _with_pair(row, params, pair_axes(params))
        return row

    MATRIX_FIELDS = ("status", "dope", "ga341", "identity", "coverage", "grade", "grade_score", "best_model")

    def matrix(self, job_id: str, *, page: Optional[int] = None, size: int = 50) -> dict:
        """Pipeline M × N: matrice sequenze × template con esito e metriche di ogni coppia (zDOPE, DOPE, GA341,
        molpdf, identità, copertura, voto) e il migliore di ogni sequenza. Errore per le altre pipeline.

        Con `page`: solo le righe (sequenze) di quella pagina, da `size`, più `page`, `pages`, `start` e
        `sequence_count`; con decine di migliaia di sequenze la matrice intera pesa decine di MB. In ogni caso
        `summary`: coppie per esito, sequenze con il migliore scelto, vittorie di ogni template, su tutta la matrice."""
        job = self.jobs.get(job_id, include_result=False)
        params = self._params(job_id)
        if job["kind"] != "pipeline" or not is_cross(params):
            raise AppError("La matrice c'è solo per le pipeline con un modello per ogni template (sequenze × template).",
                           code="matrix_unsupported")
        names = pair_axes(params)
        n = len(names["templates"])
        count = len(names["sequences"])
        first, last, extra = 0, count, {}
        if page is not None:
            size = max(1, min(int(size), 500))
            pages = max(1, (count + size - 1) // size)
            page = max(1, min(int(page), pages))
            first, last = (page - 1) * size, min(count, page * size)
            names = {**names, "sequences": names["sequences"][first:last]}
            extra = {"page": page, "pages": pages, "size": size, "start": first, "sequence_count": count}
        cells: list[list[Optional[dict]]] = [[None] * n for _ in range(last - first)]
        rows = self.db.query("SELECT idx, status, dope, ga341, identity, coverage, grade, grade_score, best_model, "
                             "metrics, error FROM pipeline_items WHERE job_id=? AND idx>=? AND idx<? ORDER BY idx",
                             (job_id, first * n, last * n))
        for r in rows:
            s, t = pair_of(r["idx"], params)
            if not first <= s < last or t >= n:
                continue
            metrics = r["metrics"] if isinstance(r.get("metrics"), dict) else {}
            cells[s - first][t] = {"idx": r["idx"], **{k: r.get(k) for k in self.MATRIX_FIELDS},
                                   "zdope": metrics.get("zdope"), "molpdf": metrics.get("molpdf"),
                                   "best": bool(metrics.get("best_in_sequence")), "error": r.get("error")}
        return {**names, "cells": cells, **extra, "summary": self._matrix_summary(job_id, n)}

    def _matrix_summary(self, job_id: str, n: int) -> dict:
        """Riepilogo di tutta la matrice M × N: coppie per esito (indice per stato), sequenze con il migliore scelto e
        quante volte ogni template è il migliore."""
        counts = {r["status"]: int(r["n"]) for r in self.db.query(
            "SELECT status, COUNT(*) AS n FROM pipeline_items WHERE job_id=? GROUP BY status", (job_id,))}
        wins = [0] * n
        for r in self.db.query("SELECT idx FROM pipeline_items WHERE job_id=? AND status='completed' AND metrics LIKE ?",
                               (job_id, '%"best_in_sequence": true%')):
            wins[int(r["idx"]) % max(1, n)] += 1
        return {"pairs": sum(counts.values()), "completed": counts.get("completed", 0),
                "failed": counts.get("failed", 0), "open": sum(counts.get(s, 0) for s in OPEN_ITEM),
                "closed": sum(wins), "wins": wins}

    def export_csv(self, job_id: str) -> str:
        """Tabella di tutti gli elementi. Pipeline M × N: la tabella delle metriche (una riga per coppia, come
        `metriche.csv` dello ZIP)."""
        rows = self.db.query(f"SELECT {ITEM_COLUMNS} FROM pipeline_items WHERE job_id=? ORDER BY idx", (job_id,))
        params = self._params(job_id)
        if is_cross(params):
            return _write_csv(CROSS_FIELDS, self._metric_rows(job_id, rows, params, None))
        qmean = self._qmean_by_file(job_id)
        buf = io.StringIO()
        fields = ["idx", "name", "length", "status", "template", "template_source", "identity", "coverage", "evalue",
                  "twilight", "dope", "zdope", "ga341", "grade", "grade_score", "rama_favored", "clashscore",
                  *QMEAN_FIELDS, "best_model", "error"]
        writer = csv.DictWriter(buf, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for r in rows:
            r = {**r, "zdope": (r.get("metrics") or {}).get("zdope"),
                 **(_qmean_columns(qmean.get(r.get("best_model") or "")) if r.get("status") == "completed" else {})}
            writer.writerow({k: _csv_safe(r.get(k)) for k in fields})
        return buf.getvalue()

    def _qmean_by_file(self, job_id: str) -> dict[str, dict]:
        """Risultati QMEAN dei modelli della pipeline, per file: job `qmean` completati su un file del job (QMEAN
        automatico o lanciato dalla pagina Qualità), il più recente per ogni file. Si tengono solo quelli che riguardano
        il file attuale (stessa impronta): una ripresa che rielabora un elemento scrive un modello nuovo con lo stesso
        nome, e il QMEAN di quello vecchio non vale più."""
        where = "FROM jobs WHERE kind='qmean' AND status='completed'"
        try:
            rows = self.db.query(f"SELECT json_extract(params, '$.source.file') AS file, result {where} AND "
                                 "json_extract(params, '$.source.job_id')=? ORDER BY finished_at, rowid", (job_id,))
        except sqlite3.OperationalError:  # SQLite senza funzioni JSON: parametri interi
            rows = []
            for r in self.db.query(f"SELECT params, result {where} ORDER BY finished_at, rowid"):
                source = (r.get("params") or {}).get("source") or {}
                if source.get("job_id") == job_id:
                    rows.append({"file": source.get("file"), "result": r.get("result")})
        found: dict[str, dict] = {}
        for r in rows:
            if r.get("file") and isinstance(r.get("result"), dict):
                found[str(r["file"])] = r["result"]  # in ordine di fine: resta il più recente
        out: dict[str, dict] = {}
        for file, result in found.items():
            fingerprint = result.get("source_sha256")
            if fingerprint:
                try:
                    if cache.content_hash(self.jobs.file_path(job_id, file)) != fingerprint:
                        continue
                except (AppError, OSError):
                    continue
            out[file] = result
        return out

    def export_zip(self, job_id: str, dest: Path) -> None:
        """Scrive l'archivio su file (nello scratch): una pipeline di migliaia di modelli non passa dalla RAM.

        Il report HTML di ogni elemento completato (senza immagine 3D) si genera e si scrive uno alla volta: in
        memoria c'è al più un report. Un report non generato (file dell'elemento illeggibili) non ferma
        l'esportazione: il motivo finisce in `report/non_generati.txt`. Modello, rapporto di qualità e report di un
        elemento hanno lo stesso nome nell'archivio, unico fra gli elementi (`_zip_stem`).
        """
        job = self.jobs.get(job_id)
        rows = self.db.query(f"SELECT {ITEM_COLUMNS}, sequence FROM pipeline_items WHERE job_id=? "
                             "AND status='completed' ORDER BY idx", (job_id,))
        if is_cross(job.get("params") or {}):
            self._export_cross_zip(job, rows, dest)
            return
        reports = ReportService(self.jobs, self.compute, self.db)
        with_qmean = set(self._qmean_by_file(job_id))  # report con QMEAN solo per i modelli valutati
        missing: list[str] = []
        used: set[str] = set()
        with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("riepilogo.csv", self.export_csv(job_id))
            zf.writestr("LEGGIMI.txt", README_ZIP)
            for r in rows:
                try:
                    model = self.jobs.file_path(job_id, r["best_model"])
                except NotFound:
                    continue
                stem = _zip_stem(r["name"], int(r["idx"]), used)
                zf.write(model, f"modelli/{stem}.pdb")
                quality = Path(str(model) + ".quality.json")
                if quality.exists():
                    zf.write(quality, f"qualita/{stem}.quality.json")
                try:
                    text = reports.pipeline_item(job, r, qmean=r["best_model"] in with_qmean)
                except Exception as exc:  # un report non generato non deve fermare l'esportazione
                    missing.append(f"{stem}: {getattr(exc, 'message', None) or f'{type(exc).__name__}: {exc}'}")
                    continue
                zf.writestr(f"report/{stem}.html", text)
            if missing:
                zf.writestr("report/non_generati.txt", "Report non generati\n\n" + "\n".join(missing) + "\n")
            zf.writestr("parametri.json", json.dumps(job["params"], indent=2, ensure_ascii=False))

    def _consensus(self, job_id: str, row: dict) -> tuple:
        """(punteggio, fascia) del consenso dal rapporto di qualità del modello della coppia; (None, None) senza."""
        try:
            path = self.jobs.file_path(job_id, f"{row['best_model']}.quality.json")
            cons = (json.loads(path.read_text(encoding="utf-8")) or {}).get("consensus") or {}
        except (AppError, OSError, ValueError, TypeError):
            return None, None
        return cons.get("score"), cons.get("band")

    def _metric_rows(self, job_id: str, rows: list[dict], params: dict, files: Optional[dict[int, str]]) -> list[dict]:
        return _metric_rows(rows, params, files, lambda r: self._consensus(job_id, r), self._qmean_by_file(job_id))

    def _export_cross_zip(self, job: dict, rows: list[dict], dest: Path) -> None:
        """ZIP di una pipeline M × N: una cartella per template e, dentro, una per sequenza,
        `template/<template>/<sequenza>/`, con modello, report e rapporto di qualità (se l'analisi era richiesta) della
        coppia, tutti chiamati `<sequenza>__<template>`;
        `metriche.csv` (una riga per coppia con tutte le metriche di MODELLER, del template e dell'analisi di qualità,
        e il percorso del modello nell'archivio), `migliori.csv` (una riga per sequenza, con le stesse metriche del
        modello migliore) e `parametri.json`. Report generati uno alla volta."""
        job_id, params = job["id"], job.get("params") or {}
        names = pair_axes(params)
        reports = ReportService(self.jobs, self.compute, self.db)
        with_qmean = set(self._qmean_by_file(job_id))  # report con QMEAN solo per i modelli valutati
        missing: list[str] = []
        files: dict[int, str] = {}
        with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("LEGGIMI.txt", README_CROSS_ZIP)
            for r in rows:
                try:
                    model = self.jobs.file_path(job_id, r["best_model"])
                except NotFound:
                    continue
                s, t = pair_of(r["idx"], params)
                label = names["templates"][t] if t < len(names["templates"]) else pair_names(r["name"])[1]
                sequence = names["sequences"][s] if s < len(names["sequences"]) else pair_names(r["name"])[0]
                base = f"template/{label}/{sequence}/{r['name']}"
                zf.write(model, f"{base}.pdb")
                files[int(r["idx"])] = f"{base}.pdb"
                quality_file = Path(str(model) + ".quality.json")
                if quality_file.exists():
                    zf.write(quality_file, f"{base}.quality.json")
                try:
                    text = reports.pipeline_item(job, r, qmean=r["best_model"] in with_qmean)
                except Exception as exc:  # un report non generato non deve fermare l'esportazione
                    missing.append(f"{r['name']}: {getattr(exc, 'message', None) or f'{type(exc).__name__}: {exc}'}")
                    continue
                zf.writestr(f"{base}.html", text)
            if missing:
                zf.writestr("report_non_generati.txt", "Report non generati\n\n" + "\n".join(missing) + "\n")
            every = self.db.query(f"SELECT {ITEM_COLUMNS} FROM pipeline_items WHERE job_id=? ORDER BY idx", (job_id,))
            metric_rows = self._metric_rows(job_id, every, params, files)
            zf.writestr("metriche.csv", _write_csv(CROSS_FIELDS, metric_rows))
            zf.writestr("migliori.csv", _write_csv(BEST_FIELDS, _best_rows(metric_rows, params)))
            zf.writestr("parametri.json", json.dumps(params, indent=2, ensure_ascii=False))


# tabella delle metriche di una pipeline M × N (metriche.csv e /export.csv): una riga per coppia
CROSS_FIELDS = ["idx", "sequence", "template", "template_id", "length", "status", "best_in_sequence", "file",
                "identity", "coverage", "twilight", "dope", "zdope", "ga341", "molpdf", "grade", "grade_score",
                "rama_favored", "rama_outliers", "clashscore", "helix_pct", "strand_pct", "residues",
                "consensus_score", "consensus_band", "prosa_z", "procheck_core", "procheck_g_overall",
                "qmean4_z", "qmean4", "qmean6_z", "qmean_local_mean", "qmean_interaction_z", "qmean_cbeta_z",
                "qmean_packing_z", "qmean_torsion_z", "qmean_ss_agreement_z", "qmean_acc_agreement_z", "qmean_method",
                "qmean_source", "error"]
# QMEAN (job qmean sul modello, automatico o dalla pagina Qualità): punteggi globali e componenti (Z-score)
QMEAN_FIELDS = CROSS_FIELDS[CROSS_FIELDS.index("qmean4_z"):CROSS_FIELDS.index("error")]
QMEAN_COMPONENTS = ("interaction", "cbeta", "packing", "torsion", "ss_agreement", "acc_agreement")
# metriche dell'analisi di qualità salvate nell'elemento (quality.summary_metrics, nelle metriche della riga)
QUALITY_FIELDS = ("rama_outliers", "helix_pct", "strand_pct", "residues", "prosa_z", "procheck_core",
                  "procheck_g_overall")
# migliori.csv: una riga per sequenza, con le stesse metriche del modello migliore
BEST_FIELDS = ["sequence", "best_template", "template_id", "completed", "templates", "file",
               *[f for f in CROSS_FIELDS if f not in ("idx", "sequence", "template", "template_id", "status",
                                                     "best_in_sequence", "file", "error")]]


def _qmean_columns(result: Optional[dict]) -> dict:
    """Colonne QMEAN di un risultato di job qmean: QMEAN4 (Z-score e punteggio 0-1), QMEAN6 Z-score, media dei punteggi
    locali (QMEANDisCo o QMEAN), Z-score delle componenti, metodo e servizio; vuote senza QMEAN."""
    if not isinstance(result, dict):
        return {}
    components = result.get("components") if isinstance(result.get("components"), dict) else {}
    source = result.get("source")
    return {"qmean4_z": result.get("qmean4_z"), "qmean4": result.get("qmean4"), "qmean6_z": result.get("qmean6_z"),
            "qmean_local_mean": result.get("qmeandisco"),
            **{f"qmean_{k}_z": components.get(f"{k}_z_score") for k in QMEAN_COMPONENTS},
            "qmean_method": result.get("method"),
            "qmean_source": {"local": "locale", "swissmodel": "SWISS-MODEL"}.get(source, source)}


def _metric_rows(rows: list[dict], params: dict, files: Optional[dict[int, str]],
                 consensus_of: Callable[[dict], tuple], qmean: Optional[dict[str, dict]] = None) -> list[dict]:
    """Una riga di metriche per coppia: MODELLER, template, analisi di qualità, consenso (dal rapporto di qualità) e
    QMEAN (`qmean`: risultati per file del modello)."""
    names = pair_axes(params)
    out = []
    for r in rows:
        _with_pair(r, params, names)
        metrics = r.get("metrics") if isinstance(r.get("metrics"), dict) else {}
        score, band = consensus_of(r) if r.get("status") == "completed" else (None, None)
        scores = _qmean_columns((qmean or {}).get(r.get("best_model") or "")) if r.get("status") == "completed" else {}
        out.append({**r, **scores, "sequence": r["pair"]["sequence"], "template": r["pair"]["template"],
                    "template_id": r["pair"]["template_id"], "zdope": metrics.get("zdope"),
                    "molpdf": metrics.get("molpdf"), **{k: metrics.get(k) for k in QUALITY_FIELDS},
                    "consensus_score": score, "consensus_band": band,
                    "best_in_sequence": 1 if r["best_in_sequence"] else 0,
                    "file": (files or {}).get(int(r["idx"]), "" if files is not None else r.get("best_model") or "")})
    return out


def _write_csv(fields: list[str], rows: list[dict]) -> str:
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=fields, extrasaction="ignore")
    writer.writeheader()
    for row in rows:
        writer.writerow({k: _csv_safe(row.get(k)) for k in fields})
    return buf.getvalue()


def _best_rows(metric_rows: list[dict], params: dict) -> list[dict]:
    """Una riga per sequenza: template del modello migliore (zDOPE) con tutte le sue metriche e coppie completate."""
    names = pair_axes(params)
    by_sequence: dict[int, list[dict]] = {}
    for r in metric_rows:
        by_sequence.setdefault(r["pair"]["sequence_index"], []).append(r)
    out = []
    for s, name in enumerate(names["sequences"]):
        group = by_sequence.get(s, [])
        best = next((r for r in group if r["best_in_sequence"]), None) or {}
        out.append({**best, "sequence": name, "best_template": best.get("template", ""),
                    "completed": sum(1 for r in group if r.get("status") == "completed"),
                    "templates": len(names["templates"])})
    return out


def _zip_stem(name: str, idx: int, used: set[str]) -> str:
    """Nome di un elemento nell'archivio ZIP, uguale per modelli/, qualita/ e report/ e unico fra gli elementi.

    È il nome dell'elemento, già limitato a [A-Za-z0-9_-] da `sequences.sanitize_code` (niente `safe_filename`, che
    toglie i '-' ai bordi: "abc" e "abc-" darebbero lo stesso report). Se è già usato (job di versioni precedenti, la
    cui deduplicazione poteva ripetere un nome generato: "x", "x", "x_2" → "x", "x_2", "x_2") diventa quello della
    cartella dell'elemento, `<idx>_<nome>`, con un contatore se serve.
    """
    stem = name or f"seq{idx + 1}"
    if stem in used:
        stem = f"{idx:04d}_{stem}"
    base, n = stem, 1
    while stem in used:
        n += 1
        stem = f"{base}_{n}"
    used.add(stem)
    return stem


def _seqs(n: int) -> str:
    return f"{n} sequenza" if n == 1 else f"{n} sequenze"


def _tpls(n: int) -> str:
    return f"{n} template"


def _models(n: int) -> str:
    return f"{n} modello" if n == 1 else f"{n} modelli"


def public_job(job: dict) -> dict:
    """Job di una pipeline per le risposte dell'API: nei parametri l'inizio del FASTA (`fasta_truncated`,
    `fasta_bytes`) e il numero di sequenze (`sequence_count`) al posto del loro elenco. Con 100.000 sequenze il FASTA
    è ~50 MB, e la pagina chiede il job di continuo. Il job nel database, lo ZIP (parametri.json) e i pacchetti di
    riproducibilità hanno i parametri interi. Restituisce il job, modificato solo se serve."""
    params = job.get("params")
    if job.get("kind") != "pipeline" or not isinstance(params, dict):
        return job
    fasta, listed = str(params.get("fasta") or ""), params.get("sequences")
    if len(fasta) <= FASTA_SHOWN and not (isinstance(listed, list) and len(listed) > PAIRS_LISTED):
        return job
    slim = {**params}
    if len(fasta) > FASTA_SHOWN:
        slim.update(fasta=fasta[:FASTA_SHOWN], fasta_truncated=True, fasta_bytes=len(fasta.encode("utf-8")))
    if isinstance(listed, list) and len(listed) > PAIRS_LISTED:
        slim.pop("sequences")
        slim["sequence_count"] = len(listed)
    return {**job, "params": slim}


def is_cross(params: dict) -> bool:
    """Pipeline M × N: template fissi, un modello per ogni coppia sequenza × template."""
    return params.get("template_mode") == "fixed" and params.get("fixed_combination") == "each"


def template_labels(templates: list[dict]) -> list[str]:
    """Nome di ogni template fisso nelle coppie M × N (cartelle dello ZIP, colonne delle matrici): id e catene
    sanificati (`1UBQ_A`), resi unici con un contatore."""
    labels: list[str] = []
    for i, t in enumerate(templates):
        chains = [str(c).strip() for c in t.get("chains") or [] if str(c).strip()]
        base = label = sequences.sanitize_code("_".join([str(t.get("id") or ""), *chains]), default=f"T{i + 1}")
        n = 1
        while label in labels:
            n += 1
            label = f"{base}_{n}"
        labels.append(label)
    return labels


def pair_of(idx: int, params: dict) -> tuple[int, int]:
    """(sequenza, template) dell'elemento `idx` di una pipeline M × N: gli elementi sono in ordine di sequenza."""
    return divmod(int(idx), max(1, len(params.get("templates") or [])))


def pair_names(name: str) -> tuple[str, str]:
    """(sequenza, template) dal nome di un elemento M × N, `<sequenza>__<template>`."""
    sequence, _sep, template = str(name).partition(PAIR_SEP)
    return sequence, template


def pair_remark(sequence: str, template: str) -> str:
    """Riga REMARK 999 BIOLOOM della coppia: il PDB dice a quale sequenza e a quale template si riferisce."""
    return f"PIPELINE SEQUENCE {sequence} TEMPLATE {template}"


def pair_axes(params: dict) -> dict:
    """Nomi delle sequenze (righe) e dei template (colonne) di una pipeline M × N, con gli id dei template."""
    templates = params.get("templates") or []
    labels = params.get("template_labels") or template_labels(templates)
    return {"sequences": [str(s.get("name")) for s in params.get("sequences") or [] if isinstance(s, dict)],
            "templates": list(labels), "template_ids": [str(t.get("id") or "") for t in templates]}


def _with_pair(row: dict, params: dict, names: dict) -> None:
    """Campi della coppia in una riga M × N: indici e nomi di sequenza e template, migliore della sequenza."""
    s, t = pair_of(row["idx"], params)
    sequence, label = pair_names(row["name"])
    row["pair"] = {"sequence_index": s, "template_index": t,
                   "sequence": names["sequences"][s] if s < len(names["sequences"]) else sequence,
                   "template": names["templates"][t] if t < len(names["templates"]) else label,
                   "template_id": names["template_ids"][t] if t < len(names["template_ids"]) else ""}
    metrics = row.get("metrics") if isinstance(row.get("metrics"), dict) else {}
    row["best_in_sequence"] = bool(metrics.get("best_in_sequence"))


def _best_key(row: dict) -> tuple:
    """Ordine dei modelli di una sequenza: zDOPE più basso, poi DOPE più basso, poi l'ordine dei template."""
    metrics = row.get("metrics") if isinstance(row.get("metrics"), dict) else {}
    zdope, dope = metrics.get("zdope"), row.get("dope")
    number = (int, float)
    return (zdope if isinstance(zdope, number) and not isinstance(zdope, bool) else float("inf"),
            dope if isinstance(dope, number) and not isinstance(dope, bool) else float("inf"), row["idx"])


def max_templates(params: dict) -> int:
    """Template al massimo per sequenza nella modalità multi: da 2 a `template_rank.MAX_TEMPLATES_LIMIT`,
    predefinito `template_rank.MAX_TEMPLATES` (3)."""
    value = params.get("max_templates", template_rank.MAX_TEMPLATES)
    try:
        number = int(value)
    except (TypeError, ValueError):
        number = -1
    if isinstance(value, bool) or not 2 <= number <= template_rank.MAX_TEMPLATES_LIMIT:
        raise AppError(f"Numero di template complementari non valido: {value!r} (da 2 a "
                       f"{template_rank.MAX_TEMPLATES_LIMIT}).")
    return number


def options_for_run(params: dict, resumed: bool) -> tuple[dict, Optional[str]]:
    """Parametri con la modalità di MODELLER per questa esecuzione e, se serve, la nota per il log.

    Le pipeline create prima della modalità parallela (12 ottobre 2026, 4.3.0) non la hanno nelle opzioni: i loro
    elementi sono stati costruiti in serie. Riprese, senza questa correzione costruirebbero i rimanenti in parallelo
    (il predefinito di oggi), che con lo stesso seme dà modelli diversi: la pipeline mescolerebbe le due modalità.
    Si resta in serie, come la riesecuzione dei pacchetti di allora (services/reproducibility.rerun_params)."""
    options = params.get("options") if isinstance(params.get("options"), dict) else {}
    if "parallel" in options or not resumed:
        return params, None
    return ({**params, "options": {**options, "parallel": False}},
            "Pipeline creata prima della modalità parallela di MODELLER: gli elementi rimanenti si costruiscono in "
            "serie come quelli già conclusi, così con lo stesso seme tutti i modelli restano confrontabili.")


def effective_parallel(params: dict, workers: int) -> int:
    """Elementi elaborati insieme: quelli chiesti (predefinito 2), non oltre i job paralleli delle Impostazioni
    (al massimo `MAX_PARALLEL`). Lo usano l'esecuzione e la stima del tempo residuo."""
    try:
        requested = int(params.get("parallel", 2))
    except (TypeError, ValueError):
        requested = 2
    return max(1, min(requested, int(workers), MAX_PARALLEL))


def _saved(final_dir: Path, rel: Optional[str]) -> bool:
    """Il file (percorso relativo del modello di un elemento) è nella cartella definitiva del job."""
    if not rel:
        return False
    try:
        return safe_join(final_dir, *rel.split("/")).is_file()
    except (AppError, OSError):
        return False


def _csv_safe(value):
    """Neutralizza le formule nei fogli di calcolo (CSV injection)."""
    if isinstance(value, str) and value[:1] in ("=", "+", "-", "@"):
        return "'" + value
    return "" if value is None else value


README_ZIP = """BioLoom v4 - esportazione pipeline
=====================================
modelli/            miglior modello (DOPE minimo) per ogni sequenza completata
qualita/            rapporto di qualità completo (JSON) per ogni modello
report/             report HTML di ogni modello: un file autonomo (nessuna risorsa esterna) da aprire nel
                    browser e da stampare in PDF, con bersaglio, template, allineamento, parametri di
                    MODELLER, voto, consenso, grafici di Ramachandran e profili per residuo, legenda e
                    riferimenti (senza la vista 3D, che si aggiunge generando il report dall'interfaccia);
                    con QMEAN se il modello è stato valutato
riepilogo.csv      una riga per sequenza con template, identità, copertura e metriche
parametri.json      parametri con cui è stata eseguita la pipeline

Legenda delle colonne principali
- identity / coverage: identità (%) e copertura (%) del template rispetto alla sequenza
- twilight: 1 se identità < soglia o copertura < soglia (zona crepuscolare)
- dope: energia DOPE (più negativa = migliore); zdope: DOPE normalizzato (< -1 buono)
- ga341: punteggio GA341 (> 0.7 modello affidabile)
- grade: voto BioLoom A-D (Ramachandran, clashscore, zDOPE)
- rama_favored: % di residui in regioni favorite del grafico di Ramachandran
- clashscore: contatti sterici gravi ogni 1000 atomi (approssimato, atomi pesanti)
- qmean4_z: QMEAN4 Z-score (da -4 in su: qualità paragonabile a strutture sperimentali); qmean4: punteggio 0-1;
  qmean6_z: QMEAN6 Z-score; qmean_local_mean: media dei punteggi locali (0-1)
- qmean_interaction_z, qmean_cbeta_z, qmean_packing_z, qmean_torsion_z, qmean_ss_agreement_z, qmean_acc_agreement_z:
  Z-score delle componenti di QMEAN (interazioni, C-beta, impaccamento, torsioni, accordo con struttura secondaria e
  accessibilità predette); qmean_method: QMEAN o QMEANDisCo; qmean_source: locale o SWISS-MODEL
  Le colonne QMEAN sono piene solo per i modelli valutati (QMEAN automatico o dalla pagina Qualità).
"""

README_CROSS_ZIP = """BioLoom v4 - esportazione pipeline sequenze x template
=======================================================
Ogni sequenza è stata modellata su ogni template, uno alla volta (un modello per coppia).
Ogni file si chiama <sequenza>__<template>: il nome dice da solo a quale coppia si riferisce,
anche fuori dalla sua cartella; nel PDB la riga "REMARK 999 BIOLOOM PIPELINE SEQUENCE ... TEMPLATE ..."
ripete la coppia.

template/<template>/<sequenza>/   una cartella per template e, dentro, una per sequenza, con i file della coppia:
  <seq>__<tpl>.pdb       miglior modello (DOPE minimo) della coppia
  <seq>__<tpl>.html      report HTML della coppia (autonomo, stampabile in PDF)
  <seq>__<tpl>.quality.json  rapporto di qualità della coppia (se l'analisi era richiesta)
metriche.csv             una riga per coppia con tutte le metriche: sequenza, template, migliore della
                         sequenza (best_in_sequence = 1), percorso del modello nell'archivio (file),
                         identità, copertura, punteggi di MODELLER e analisi di qualità; si filtra o si
                         riassume per sequenza o per template (tabella pivot)
migliori.csv             una riga per sequenza: template del modello migliore con le stesse metriche
parametri.json           parametri con cui è stata eseguita la pipeline (ordine di sequenze e template)

Modello migliore di una sequenza: zDOPE più basso fra i suoi template (a parità il DOPE). L'analisi di
qualità (voto, Ramachandran, clashscore), se richiesta, c'è per ogni coppia.

Legenda delle colonne principali
- identity / coverage: identità (%) e copertura (%) del template sulla sequenza, dall'allineamento
- twilight: 1 se identità < soglia o copertura < soglia (zona crepuscolare; il modello c'è comunque)
- dope: energia DOPE (più negativa = migliore, confrontabile fra template della stessa sequenza)
- zdope: DOPE normalizzato (< -1 buono); ga341: > 0.7 ripiegamento affidabile; molpdf: funzione obiettivo
- grade / grade_score: voto BioLoom A-D e suo punteggio (Ramachandran, clashscore, zDOPE)
- rama_favored / rama_outliers: % di residui in regioni favorite / outlier di Ramachandran
- clashscore: contatti sterici gravi ogni 1000 atomi; helix_pct / strand_pct: struttura secondaria (%)
- consensus_score / consensus_band: consenso fra i metodi (0-100) e fascia (buona, intermedia, scarsa)
- prosa_z, procheck_core, procheck_g_overall: ProSA II e PROCHECK, solo con l'analisi estesa
- qmean4_z: QMEAN4 Z-score (da -4 in su: qualità paragonabile a strutture sperimentali); qmean4: punteggio 0-1;
  qmean6_z: QMEAN6 Z-score; qmean_local_mean: media dei punteggi locali (0-1)
- qmean_interaction_z, qmean_cbeta_z, qmean_packing_z, qmean_torsion_z, qmean_ss_agreement_z, qmean_acc_agreement_z:
  Z-score delle componenti di QMEAN (interazioni, C-beta, impaccamento, torsioni, accordo con struttura secondaria e
  accessibilità predette); qmean_method: QMEAN o QMEANDisCo; qmean_source: locale o SWISS-MODEL
  Le colonne QMEAN sono piene solo per i modelli valutati (QMEAN automatico: il migliore di ogni sequenza, o tutte
  le coppie se richiesto; oppure calcolato dalla pagina Qualità); vuote per gli altri. Anche il report HTML di un
  modello valutato contiene QMEAN.
"""
