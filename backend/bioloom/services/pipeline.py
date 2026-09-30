"""Pipeline batch: da un multi-FASTA a modelli validati, un elemento per sequenza.

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
import threading
import zipfile
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from typing import Optional

from ..core import eta as eta_estimate
from ..core.db import Database, encode_json, now_iso
from ..core.errors import AppError, Conflict, NotFound
from ..core.events import ITEM
from ..core.jobs import REQUEUABLE, JobCancelled, JobContext, JobManager, requeue_error
from ..core.storage import remove_tree, safe_join
from ..domain import report as report_domain
from ..domain import sequences
from ..domain.sequences import SeqRecord
from ..engines import search
from .compute import ComputeService
from .reports import ReportService

TERMINAL_ITEM = {"completed", "failed", "cancelled", "filtered"}
# Elementi che una ripresa rielabora, per ambito: "incomplete" = tutto ciò che non è completato o filtrato
# (falliti, annullati, interrotti, in corso o in attesa); "failed" = solo i falliti.
RESUME_WHERE = {
    "incomplete": "status NOT IN ('completed','filtered')",
    "failed": "status='failed'",
}
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
        jobs.register("pipeline", self.handle, eta=self.eta)

    # ------------------------------------------------------------------------------ sottomissione
    def submit(self, params: dict, project_id: Optional[str]) -> dict:
        records = self._records(params)
        opts = params.get("options") or {}
        from ..engines import modeller

        modeller.parse_options(opts)  # validazione anticipata
        mode = params.get("template_mode", "auto")
        if mode == "fixed" and not params.get("templates"):
            raise AppError("In modalità 'template fisso' seleziona almeno un template.")
        stored = {**params, "sequences": [{"name": r.name, "length": len(r.sequence)} for r in records]}

        def prepare(job_id: str, _workdir: Path) -> None:
            self.db.executemany(
                "INSERT INTO pipeline_items(job_id, idx, name, sequence, status) VALUES (?,?,?,?, 'pending')",
                [(job_id, i, r.name, r.sequence) for i, r in enumerate(records)])

        title = params.get("name") or f"Pipeline di {_seqs(len(records))}"
        return self.jobs.submit("pipeline", stored, project_id=project_id, title=title, prepare=prepare)

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
                                 note=f"{_seqs(todo)} da rielaborare ({detail})")

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
        Una query sulla chiave primaria degli elementi, solo per pipeline in esecuzione.
        """
        started = job.get("started_at")
        if not started:
            return None
        rows = self.db.query(
            "SELECT status, started_at, finished_at FROM pipeline_items WHERE job_id=? AND "
            f"(status IN ({','.join('?' * len(OPEN_ITEM))}) OR "
            f"(status IN ({','.join('?' * len(PROCESSED_ITEM))}) AND started_at >= ?))",
            (job["id"], *OPEN_ITEM, *PROCESSED_ITEM, started))
        durations, running, remaining = [], [], 0
        for row in rows:
            if row["status"] in PROCESSED_ITEM:
                durations.append(eta_estimate.seconds_between(row["started_at"], row["finished_at"]))
            else:
                remaining += 1
                if row["status"] != "pending":
                    running.append(eta_estimate.seconds_between(row["started_at"], now))
        stored = self.db.query_one("SELECT params FROM jobs WHERE id=?", (job["id"],)) or {}
        params = stored.get("params") if isinstance(stored.get("params"), dict) else {}
        parallel = effective_parallel(params, self.jobs.settings_provider().effective_workers())
        return eta_estimate.pipeline_eta(durations, remaining, parallel, running)

    @staticmethod
    def _records(params: dict) -> list[SeqRecord]:
        raw = params.get("fasta") or ""
        records = sequences.parse_fasta(raw) if raw.strip() else [
            SeqRecord(s.get("name", f"seq{i + 1}"), s.get("sequence", "")) for i, s in enumerate(params.get("records") or [])]
        if not records:
            raise AppError("Nessuna sequenza fornita alla pipeline.")
        if len(records) > 2000:
            raise AppError("Massimo 2000 sequenze per pipeline.")
        seen: dict[str, int] = {}
        out = []
        for i, r in enumerate(records):
            seq, _ = sequences.validate_sequence(r.sequence, "protein", r.name or f"sequenza {i + 1}")
            name = sequences.sanitize_code(r.name, default=f"seq{i + 1}")
            if name in seen:
                seen[name] += 1
                name = f"{name}_{seen[name]}"
            else:
                seen[name] = 1
            out.append(SeqRecord(name, seq))
        return out

    # ------------------------------------------------------------------------------ esecuzione
    def handle(self, ctx: JobContext, p: dict) -> dict:
        # Solo gli elementi in attesa: in una ripresa gli altri sono già conclusi (o esclusi dall'ambito
        # scelto) e restano come sono. Una pipeline nuova ha tutti gli elementi in attesa.
        items = self.db.query("SELECT idx, name, sequence FROM pipeline_items WHERE job_id=? AND status='pending' "
                              "ORDER BY idx", (ctx.job_id,))
        total = int(self.db.scalar("SELECT COUNT(*) FROM pipeline_items WHERE job_id=?", (ctx.job_id,)))
        already = int(self.db.scalar(
            f"SELECT COUNT(*) FROM pipeline_items WHERE job_id=? AND status IN ({','.join('?' * len(PROCESSED_ITEM))})",
            (ctx.job_id, *PROCESSED_ITEM)))
        parallel = effective_parallel(p, ctx.settings.effective_workers())
        done_lock = threading.Lock()
        done = {"n": already}
        ctx.log(f"Pipeline di {_seqs(total)}, {parallel} in parallelo")
        skipped = total - already - len(items)
        if len(items) < total:
            ctx.log(f"Ripresa: {_seqs(len(items))} da elaborare, {already} già elaborate"
                    + (f", {skipped} lasciate come sono (annullate)" if skipped else ""))

        def finished_one() -> None:
            with done_lock:
                done["n"] += 1
                ctx.progress(done["n"] / max(total, 1), f"{done['n']} di {total} sequenze elaborate")

        def work(item: dict) -> None:
            try:
                self._process_item(ctx, p, item)
            except JobCancelled:
                self._update(ctx.job_id, item["idx"], status="cancelled", stage="Annullato",
                             finished_at=now_iso())
            finally:
                finished_one()

        ctx.progress(already / max(total, 1), f"{already} di {total} sequenze elaborate")
        with ThreadPoolExecutor(max_workers=parallel, thread_name_prefix=f"pipeline-{ctx.job_id[:6]}") as pool:
            list(pool.map(work, items))
        ctx.check()
        counts = {row["status"]: row["n"] for row in self.db.query(
            "SELECT status, COUNT(*) AS n FROM pipeline_items WHERE job_id=? GROUP BY status", (ctx.job_id,))}
        cache_hits = sum(1 for row in self.db.query("SELECT metrics FROM pipeline_items WHERE job_id=?",
                                                    (ctx.job_id,))
                         if isinstance(row["metrics"], dict) and row["metrics"].get("search_cached_at"))
        if cache_hits:
            ctx.log(f"{cache_hits} " + ("ricerca di template riusata" if cache_hits == 1 else
                                        "ricerche di template riusate") + " dalla cache dei calcoli")
        # Una ripresa "solo i falliti" lascia com'erano gli annullati: la pipeline non si dichiara completata
        # (quelle sequenze non sono mai state elaborate) ma resta annullata e riprendibile per quegli elementi.
        left = sum(n for status, n in counts.items() if status not in PROCESSED_ITEM)
        if left:
            one = left == 1
            raise JobCancelled(f"{'Resta' if one else 'Restano'} {_seqs(left)} da elaborare "
                               f"({'annullata' if one else 'annullate'} in precedenza): usa «Riprendi» per completarle.")
        return {"total": total, "counts": counts, "search_cache_hits": cache_hits}

    def _update(self, job_id: str, idx: int, **fields) -> None:
        if "metrics" in fields:
            fields["metrics"] = encode_json(fields["metrics"])
        sets = ", ".join(f"{k}=?" for k in fields)
        self.db.execute(f"UPDATE pipeline_items SET {sets} WHERE job_id=? AND idx=?", (*fields.values(), job_id, idx))
        self.jobs.events.publish(job_id, ITEM, idx)

    def _process_item(self, ctx: JobContext, p: dict, item: dict) -> None:
        job_id, idx, name = ctx.job_id, item["idx"], item["name"]
        ctx.check()
        self._update(job_id, idx, status="running", stage="Avvio", started_at=now_iso())

        def on_progress(_fraction, stage):
            if stage:
                self._update(job_id, idx, stage=stage[:120])

        sub = ctx.sub(name, ctx.workdir / "items" / f"{idx:04d}_{name}", on_progress=on_progress)
        final: Optional[dict] = None
        try:
            final = self._run_item(ctx, sub, p, item)
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

    def _run_item(self, ctx: JobContext, sub: JobContext, p: dict, item: dict) -> Optional[dict]:
        """Elabora un elemento. None se è fallito (esito già registrato); altrimenti i campi da scrivere dopo
        il salvataggio dei file: lo stato 'completed' con modello e metriche, oppure {} per un elemento
        filtrato (esito già registrato: senza modello, i suoi file non sono essenziali)."""
        job_id, idx, name = ctx.job_id, item["idx"], item["name"]
        # provenienza della ricerca (cache dei calcoli), conservata anche nelle metriche finali
        search_info: dict = {}
        try:
            if p.get("template_mode", "auto") == "fixed":
                templates = p.get("templates") or []
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
                twilight = best["identity"] < float(tw.get("identity", 25)) or best["coverage"] < float(tw.get("coverage", 40))
                self._update(job_id, idx, template=best["template_id"], template_source=result["engine"],
                             identity=best["identity"], coverage=best["coverage"], evalue=best["evalue"],
                             twilight=int(twilight))
                sub.log(f"Template scelto {best['template_id']} (identità {best['identity']}%, "
                        f"copertura {best['coverage']}%"
                        + (f"; modello predetto AlphaFold, pLDDT medio {best.get('plddt')}" if best.get("predicted")
                           else "") + ")")
                if twilight and tw.get("filter", False):
                    self._update(job_id, idx, status="filtered", stage="Zona crepuscolare: saltata",
                                 error="Identità o copertura sotto soglia (filtro attivo).", finished_at=now_iso())
                    return {}
                templates = [{"id": best["pdb_id"], "chains": [best["chain"]]}]
            ctx.check()
            self._update(job_id, idx, status="modeling", stage="Modellazione")
            quality_mode = "best" if p.get("quality", True) else "none"
            res = self.compute.run_modeling(sub, target=SeqRecord(name, item["sequence"]), templates_raw=templates,
                                            alignment=None, options_raw=p.get("options") or {},
                                            quality_mode=quality_mode,
                                            extended_quality=bool(p.get("extended_quality", False)))
            best_model = next(m for m in res["models"] if m["best"])
            q = best_model.get("quality") or {}
            rel = (sub.workdir / best_model["file"]).relative_to(ctx.workdir).as_posix()
            self._update(job_id, idx, stage="Salvataggio dei file")
            # template AlphaFold: provenienza e residui tolti per pLDDT basso nelle metriche dell'elemento
            predicted = [t for t in res.get("templates") or [] if t.get("predicted")]
            af_info = {"template_predicted": True, "plddt_removed": res.get("plddt_removed", 0)} if predicted else {}
            return {"status": "completed", "stage": "Completato", "best_model": rel,
                    "dope": best_model.get("dope"), "ga341": best_model.get("ga341"), "grade": q.get("grade"),
                    "grade_score": q.get("grade_score"), "rama_favored": q.get("rama_favored"),
                    "clashscore": q.get("clashscore"),
                    "metrics": {"zdope": best_model.get("zdope"), "molpdf": best_model.get("molpdf"),
                                "models": len(res["models"]), **q, **search_info, **af_info},
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

    # ------------------------------------------------------------------------------ consultazione
    def items(self, job_id: str, *, page: int = 1, size: int = 25, status: Optional[str] = None,
              sort: str = "idx", direction: str = "asc", search_text: str = "") -> dict:
        self.jobs.get(job_id, include_result=False)
        where, args = ["job_id=?"], [job_id]
        if status and status != "all":
            where.append("status=?")
            args.append(status)
        if search_text:
            where.append("(name LIKE ? OR template LIKE ?)")
            args += [f"%{search_text}%", f"%{search_text}%"]
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
        return {"items": rows, "total": total, "page": page, "pages": pages, "size": size, "counts": counts}

    def item(self, job_id: str, idx: int) -> dict:
        row = self.db.query_one(f"SELECT {ITEM_COLUMNS}, sequence FROM pipeline_items WHERE job_id=? AND idx=?",
                                (job_id, idx))
        if not row:
            raise NotFound("Elemento della pipeline non trovato.")
        return row

    def export_csv(self, job_id: str) -> str:
        rows = self.db.query(f"SELECT {ITEM_COLUMNS} FROM pipeline_items WHERE job_id=? ORDER BY idx", (job_id,))
        buf = io.StringIO()
        fields = ["idx", "name", "length", "status", "template", "template_source", "identity", "coverage", "evalue",
                  "twilight", "dope", "zdope", "ga341", "grade", "grade_score", "rama_favored", "clashscore",
                  "best_model", "error"]
        writer = csv.DictWriter(buf, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for r in rows:
            r = {**r, "zdope": (r.get("metrics") or {}).get("zdope")}
            writer.writerow({k: _csv_safe(r.get(k)) for k in fields})
        return buf.getvalue()

    def export_zip(self, job_id: str, dest: Path) -> None:
        """Scrive l'archivio su file (nello scratch): una pipeline di migliaia di modelli non passa dalla RAM.

        Il report HTML di ogni elemento completato (senza immagine 3D) si genera e si scrive uno alla volta: in
        memoria c'è al più un report. Un report non generato (file dell'elemento illeggibili) non ferma
        l'esportazione: il motivo finisce in `report/non_generati.txt`.
        """
        job = self.jobs.get(job_id)
        rows = self.db.query(f"SELECT {ITEM_COLUMNS}, sequence FROM pipeline_items WHERE job_id=? "
                             "AND status='completed' ORDER BY idx", (job_id,))
        reports = ReportService(self.jobs, self.compute, self.db)
        missing: list[str] = []
        with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("riepilogo.csv", self.export_csv(job_id))
            zf.writestr("LEGGIMI.txt", README_ZIP)
            for r in rows:
                try:
                    model = self.jobs.file_path(job_id, r["best_model"])
                except NotFound:
                    continue
                zf.write(model, f"modelli/{r['name']}.pdb")
                quality = Path(str(model) + ".quality.json")
                if quality.exists():
                    zf.write(quality, f"qualita/{r['name']}.quality.json")
                try:
                    text = reports.pipeline_item(job, r)
                except Exception as exc:  # un report non generato non deve fermare l'esportazione
                    missing.append(f"{r['name']}: {getattr(exc, 'message', None) or f'{type(exc).__name__}: {exc}'}")
                    continue
                zf.writestr(f"report/{report_domain.safe_filename(r['name'])}", text)
            if missing:
                zf.writestr("report/non_generati.txt", "Report non generati\n\n" + "\n".join(missing) + "\n")
            zf.writestr("parametri.json", json.dumps(job["params"], indent=2, ensure_ascii=False))


def _seqs(n: int) -> str:
    return f"{n} sequenza" if n == 1 else f"{n} sequenze"


def effective_parallel(params: dict, workers: int) -> int:
    """Elementi elaborati insieme: quelli chiesti (predefinito 2), non oltre i job paralleli delle Impostazioni
    né oltre 8. Lo usano l'esecuzione e la stima del tempo residuo."""
    try:
        requested = int(params.get("parallel", 2))
    except (TypeError, ValueError):
        requested = 2
    return max(1, min(requested, int(workers), 8))


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
                    riferimenti (senza la vista 3D, che si aggiunge generando il report dall'interfaccia)
riepilogo.csv       una riga per sequenza con template, identità, copertura e metriche
parametri.json      parametri con cui è stata eseguita la pipeline

Legenda delle colonne principali
- identity / coverage: identità (%) e copertura (%) del template rispetto alla sequenza
- twilight: 1 se identità < soglia o copertura < soglia (zona crepuscolare)
- dope: energia DOPE (più negativa = migliore); zdope: DOPE normalizzato (< -1 buono)
- ga341: punteggio GA341 (> 0.7 modello affidabile)
- grade: voto BioLoom A-D (Ramachandran, clashscore, zDOPE)
- rama_favored: % di residui in regioni favorite del grafico di Ramachandran
- clashscore: contatti sterici gravi ogni 1000 atomi (approssimato, atomi pesanti)
"""
