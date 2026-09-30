"""Gestori dei job di calcolo: ricerca, allineamenti, MODELLER, qualità, QMEAN."""

from __future__ import annotations

import shutil
from dataclasses import asdict
from pathlib import Path
from typing import Optional

from ..core.errors import AppError
from ..core.jobs import JobCancelled, JobContext, JobManager
from ..core.storage import atomic_write_json, atomic_write_text, read_json, safe_join
from ..domain import sequences
from ..domain.sequences import SeqRecord
from ..engines import align, modeller, qmean_local, quality, remote, search
from ..engines.modeller import TemplateSpec
from .templates import TemplateLibrary

MAX_SEQUENCE = 5000


def _record(raw: dict, default: str = "TARGET") -> SeqRecord:
    seq, _kind = sequences.validate_sequence(raw.get("sequence", ""), "protein", raw.get("name") or "sequenza")
    if len(seq) > MAX_SEQUENCE:
        raise AppError(f"Sequenza troppo lunga ({len(seq)} residui, massimo {MAX_SEQUENCE}).")
    return SeqRecord(sequences.sanitize_code(raw.get("name"), default=default), seq)


def _template_specs(raw: list[dict]) -> list[TemplateSpec]:
    if not raw:
        raise AppError("Seleziona almeno un template.")
    if len(raw) > 10:
        raise AppError("Massimo 10 template per modello.")
    specs = []
    for t in raw:
        tid = str(t.get("id", "")).strip()
        chains = [c.strip() for c in t.get("chains", []) if str(c).strip()]
        if not tid:
            raise AppError("Template senza identificativo.")
        specs.append(TemplateSpec(id=tid, chains=chains, hetatm=bool(t.get("hetatm", False))))
    return specs


class ComputeService:
    """Registra gli handler dei job e contiene la logica condivisa (anche dalla pipeline)."""

    def __init__(self, jobs: JobManager, library: TemplateLibrary, uploads_dir: Path, databases_dir: Path) -> None:
        self.jobs = jobs
        self.library = library
        self.uploads_dir = uploads_dir
        self.databases_dir = databases_dir
        jobs.register("search", self.handle_search, pool="io")
        jobs.register("align", self.handle_align)
        jobs.register("align2d", self.handle_align2d)
        jobs.register("model", self.handle_model)
        jobs.register("loop", self.handle_loop)
        jobs.register("quality", self.handle_quality)
        jobs.register("qmean", self.handle_qmean, pool="io")

    # ------------------------------------------------------------------------------ risoluzione file
    def resolve_structure(self, source: dict) -> tuple[Path, str]:
        """Restituisce (percorso, etichetta) per una sorgente {type: template|job|upload}."""
        kind = source.get("type")
        if kind == "template":
            path = self.library.path(source.get("id", ""))
            return path, path.stem
        if kind == "job":
            path = self.jobs.file_path(source.get("job_id", ""), source.get("file", ""))
            return path, path.stem
        if kind == "upload":
            path = safe_join(self.uploads_dir, source.get("file", ""))
            if not path.is_file():
                raise AppError("File caricato non trovato.")
            return path, path.stem
        raise AppError("Sorgente della struttura non valida.")

    # ------------------------------------------------------------------------------ ricerca
    def handle_search(self, ctx: JobContext, p: dict) -> dict:
        rec = _record(p.get("query", {}), default="query")
        result = search.search(ctx, rec.sequence, engine=p.get("engine", "rcsb"),
                               evalue=float(p.get("evalue", 10.0)), max_hits=int(p.get("max_hits", 50)),
                               databases_dir=self.databases_dir, taxid=str(p.get("taxid", "")).strip(),
                               use_cache=bool(p.get("use_cache", True)))
        best = search.best_template(result["hits"])
        ctx.log(f"{len(result['hits'])} template trovati")
        return {**result, "query": rec.to_dict(), "best": best["template_id"] if best else None}

    # ------------------------------------------------------------------------------ allineamenti
    def handle_align(self, ctx: JobContext, p: dict) -> dict:
        mode = p.get("mode", "pairwise")
        use_cache = bool(p.get("use_cache", True))  # il pairwise è istantaneo: niente cache
        if mode == "structural":
            structs = p.get("structures") or []
            if len(structs) != 2:
                raise AppError("L'allineamento strutturale richiede esattamente due strutture.")
            (path_a, label_a), (path_b, label_b) = (self.resolve_structure(s) for s in structs)
            res = align.structural(path_a, structs[0].get("chain"), path_b, structs[1].get("chain"), ctx,
                                   tool=p.get("tool", "auto"),
                                   names=(f"{label_a}{structs[0].get('chain') or ''}",
                                          f"{label_b}{structs[1].get('chain') or ''}"),
                                   use_cache=use_cache)
            # anche da un risultato in cache: il PDB sovrapposto è nel risultato e si riscrive identico
            if res.get("superposed_pdb"):
                atomic_write_text(ctx.path("superposed.pdb"), res["superposed_pdb"])
                res["superposed_file"] = "superposed.pdb"
                del res["superposed_pdb"]
        else:
            kind = p.get("kind", "protein")
            records = []
            for i, raw in enumerate(p.get("records") or []):
                seq, _k = sequences.validate_sequence(raw.get("sequence", ""), kind, raw.get("name") or f"seq{i + 1}")
                records.append(SeqRecord(sequences.sanitize_code(raw.get("name"), default=f"seq{i + 1}"), seq))
            if len(records) < 2:
                raise AppError("Servono almeno due sequenze.")
            if mode == "pairwise":
                if len(records) != 2:
                    raise AppError("L'allineamento pairwise richiede esattamente due sequenze.")
                res = align.pairwise(records[0], records[1], mode=p.get("algorithm", "global"),
                                     matrix=p.get("matrix", ""), gap_open=float(p.get("gap_open", 10)),
                                     gap_extend=float(p.get("gap_extend", 0.5)), kind=kind)
            elif mode == "multiple":
                if len(records) > 200:
                    raise AppError("Massimo 200 sequenze per l'allineamento multiplo.")
                res = align.multiple(records, ctx, tool=p.get("tool", "auto"), kind=kind, use_cache=use_cache)
            else:
                raise AppError(f"Modalità di allineamento sconosciuta: {mode}")
        if res.get("aligned"):
            ids, aligned = res["ids"], res["aligned"]
            atomic_write_text(ctx.path("alignment.fasta"), sequences.format_fasta(ids, aligned))
            atomic_write_text(ctx.path("alignment.aln"), sequences.format_clustal(ids, aligned))
            res["conservation"] = res.get("conservation") or sequences.consensus_line(aligned)
        return {"mode": mode, **res}

    # ------------------------------------------------------------------------------ MODELLER
    def _prepare_templates(self, ctx: JobContext, specs: list[TemplateSpec], workdir: Path,
                           auto_fetch: bool = True) -> list[dict]:
        prepared = []
        for spec in specs:
            ctx.check()
            if not self.library.exists(spec.id):
                if auto_fetch and len(spec.id) == 4:
                    ctx.log(f"Template {spec.id} non in libreria: download da RCSB")
                    self.library.fetch(spec.id)
                else:
                    raise AppError(f"Template {spec.id} non presente nella libreria.")
            info = modeller.prepare_template(self.library.path(spec.id), spec, workdir)
            ctx.log(f"Template {info['code']}: catene {','.join(info['chains'])}, "
                    f"residui {info['first']}–{info['last']}, {len(info['sequence'].replace('/', ''))} aa")
            prepared.append(info)
        codes = [t["code"] for t in prepared]
        if len(set(codes)) != len(codes):
            raise AppError("Lo stesso template/catena è stato selezionato più volte.")
        return prepared

    def _run_alignment(self, ctx: JobContext, target: SeqRecord, templates: list[dict], workdir: Path,
                       progress: tuple[float, float]) -> str:
        if target.name in {t["code"] for t in templates}:
            target = SeqRecord(f"{target.name}_target", target.sequence)
        atomic_write_text(workdir / "target.ali", modeller.target_ali(target.name, target.sequence))
        ctx.progress(progress[0], "Allineamento sequenza-struttura")
        modeller.run_script(ctx, modeller.build_alignment_script(templates, target.name), "align.py",
                            workdir=workdir, progress_range=progress)
        return (workdir / "alignment.ali").read_text(encoding="utf-8")

    @staticmethod
    def _normalize_pir(text: str, templates: list[dict]) -> tuple[str, str, list[str]]:
        """Adatta un PIR fornito dall'utente ai template preparati: codici e file coerenti."""
        report = sequences.validate_pir(text)
        if not report["valid"]:
            raise AppError("Allineamento PIR non valido: " + " ".join(report["errors"]), code="invalid_pir",
                           details=report)
        entries = sequences.parse_pir(text)
        tmpl_entries = [e for e in entries if e.is_template]
        if len(tmpl_entries) != len(templates):
            raise AppError(f"Il PIR contiene {len(tmpl_entries)} template ma ne sono stati selezionati "
                           f"{len(templates)}.")
        by_code = {t["code"].upper(): t for t in templates}
        used = set()
        for pos, entry in enumerate(tmpl_entries):
            t = by_code.get(entry.code.upper())
            if t is None or t["code"] in used:
                t = next(x for x in templates if x["code"] not in used) if pos < len(templates) else None
            used.add(t["code"])
            entry.code = t["code"]
            fields = (entry.fields + [""] * 8)[:8]
            if not fields[0] or not fields[1]:
                fields[0:4] = [t["first"], t["chains"][0], t["last"], t["chains"][-1]]
            entry.fields = fields
            entry.kind = "structureX"
        target = next(e for e in entries if e.kind == "sequence")
        target.code = sequences.sanitize_code(target.code, default="TARGET")
        return sequences.format_pir(entries), target.code, report["warnings"]

    def run_modeling(self, ctx: JobContext, *, target: Optional[SeqRecord], templates_raw: list[dict],
                     alignment: Optional[str], options_raw: dict, quality_mode: str = "best",
                     extended_quality: bool = False) -> dict:
        """Flusso completo: template → allineamento → AutoModel → selezione → qualità. Usato anche dalla pipeline."""
        workdir = ctx.workdir
        opts = modeller.parse_options(options_raw or {})
        specs = _template_specs(templates_raw)
        if opts.hetatm:
            for s in specs:
                s.hetatm = True
        modeller.modeller_python(ctx)  # errore immediato se MODELLER manca
        ctx.progress(0.02, "Preparazione dei template")
        templates = self._prepare_templates(ctx, specs, workdir)
        warnings: list[str] = []
        if alignment:
            pir, target_code, warnings = self._normalize_pir(alignment, templates)
            atomic_write_text(workdir / "alignment.ali", pir)
        else:
            if target is None:
                raise AppError("Indica la sequenza bersaglio oppure un allineamento PIR.")
            pir = self._run_alignment(ctx, target, templates, workdir, (0.05, 0.12))
            report = sequences.validate_pir(pir)
            warnings = report["warnings"]
            target_code = next(e.code for e in sequences.parse_pir(pir) if e.kind == "sequence")
        for w in warnings:
            ctx.log(f"Avviso: {w}")
        ctx.progress(0.15, "Costruzione dei modelli")
        script = modeller.build_model_script([t["code"] for t in templates], target_code, opts)
        modeller.run_script(ctx, script, "model.py", workdir=workdir, progress_range=(0.15, 0.8))
        summary = modeller.read_models(workdir)
        models = []
        for m in summary.get("models", []):
            models.append({**m, "kind": "model"})
        for m in summary.get("loop_models", []) or []:
            models.append({**m, "kind": "loop"})
        ok = [m for m in models if not m.get("failure") and m.get("file") and (workdir / m["file"]).exists()]
        if not ok:
            raise AppError("MODELLER non ha prodotto modelli validi.", code="modeller_error")
        best = min(ok, key=lambda m: m["dope"] if m.get("dope") is not None else float("inf"))
        for m in models:
            m["best"] = m is best
        targets = ok if quality_mode == "all" else ([best] if quality_mode == "best" else [])
        for n, m in enumerate(targets):
            ctx.check()
            ctx.progress(0.8 + 0.18 * n / max(1, len(targets)), f"Analisi di qualità di {m['file']}")
            sub = ctx.sub(m["file"], workdir / f"qa_{Path(m['file']).stem}")
            report = quality.analyze(workdir / m["file"], sub, with_dope=True,
                                     dope_workdir=workdir / f"qa_{Path(m['file']).stem}",
                                     with_procheck=extended_quality, with_prosa=extended_quality)
            atomic_write_json(workdir / f"{m['file']}.quality.json", report)
            m["quality"] = quality.summary_metrics(report)
            quality.inject_remarks(workdir / m["file"], [
                f"MODEL {m['file']} TARGET {target_code}",
                f"TEMPLATES {' '.join(t['code'] for t in templates)}",
                f"DOPE {m.get('dope')} ZDOPE {m.get('zdope')} GA341 {m.get('ga341')}",
                f"GRADE {m['quality']['grade']} ({m['quality']['grade_score']}) "
                f"RAMA_FAVORED {m['quality']['rama_favored']} CLASHSCORE {m['quality']['clashscore']}",
            ])
        target_seq = next((e.sequence for e in sequences.parse_pir(pir) if e.kind == "sequence"), "")
        return {
            "target": {"code": target_code, "sequence": target_seq.replace("-", "").replace("/", "")},
            "templates": templates,
            "alignment": (workdir / "alignment.ali").read_text(encoding="utf-8"),
            "alignment_report": sequences.validate_pir((workdir / "alignment.ali").read_text(encoding="utf-8")),
            "options": asdict(opts),
            "models": models,
            "best_model": best["file"],
            "warnings": warnings,
        }

    def handle_align2d(self, ctx: JobContext, p: dict) -> dict:
        target = _record(p.get("target", {}))
        modeller.modeller_python(ctx)
        templates = self._prepare_templates(ctx, _template_specs(p.get("templates", [])), ctx.workdir)
        pir = self._run_alignment(ctx, target, templates, ctx.workdir, (0.1, 0.9))
        return {"pir": pir, "report": sequences.validate_pir(pir), "templates": templates,
                "pap": (ctx.workdir / "alignment.pap").read_text(encoding="utf-8", errors="replace")
                if (ctx.workdir / "alignment.pap").exists() else ""}

    def handle_model(self, ctx: JobContext, p: dict) -> dict:
        target = _record(p["target"]) if p.get("target") and p["target"].get("sequence") else None
        return self.run_modeling(ctx, target=target, templates_raw=p.get("templates", []),
                                 alignment=p.get("alignment") or None, options_raw=p.get("options", {}),
                                 quality_mode=p.get("quality", "best"),
                                 extended_quality=bool(p.get("extended_quality", False)))

    def handle_loop(self, ctx: JobContext, p: dict) -> dict:
        path, label = self.resolve_structure(p.get("source", {}))
        chain = str(p.get("chain", "A")).strip() or "A"
        start, end = f"{int(p['start'])}:{chain}", f"{int(p['end'])}:{chain}"
        if int(p["end"]) - int(p["start"]) < 1 or int(p["end"]) - int(p["start"]) > 30:
            raise AppError("Il loop deve contenere da 2 a 31 residui.")
        n_models = int(p.get("models", 4))
        code = sequences.sanitize_code(label, default="MODEL")
        shutil.copyfile(path, ctx.workdir / f"{code}.pdb")
        script = modeller.build_loop_script(f"{code}.pdb", code, start, end, n_models, p.get("level", "fast"))
        modeller.run_script(ctx, script, "loop.py", progress_range=(0.05, 0.85))
        models = [m for m in modeller.read_models(ctx.workdir).get("loop_models", [])
                  if not m.get("failure") and (ctx.workdir / m["file"]).exists()]
        if not models:
            raise AppError("Il raffinamento non ha prodotto modelli validi.")
        best = min(models, key=lambda m: m["dope"] if m.get("dope") is not None else float("inf"))
        for m in models:
            m["best"] = m is best
            m["kind"] = "loop"
        ctx.progress(0.88, "Analisi di qualità del modello migliore")
        report = quality.analyze(ctx.workdir / best["file"], ctx.sub("qa", ctx.workdir / "qa"),
                                 dope_workdir=ctx.workdir / "qa")
        atomic_write_json(ctx.workdir / f"{best['file']}.quality.json", report)
        best["quality"] = quality.summary_metrics(report)
        return {"source": label, "region": [start, end], "models": models, "best_model": best["file"]}

    # ------------------------------------------------------------------------------ qualità
    def handle_quality(self, ctx: JobContext, p: dict) -> dict:
        path, label = self.resolve_structure(p.get("source", {}))
        report = quality.analyze(path, ctx, with_dope=bool(p.get("dope", True)),
                                 with_procheck=bool(p.get("procheck", True)), with_prosa=bool(p.get("prosa", True)))
        return {"source": p.get("source"), "label": label, **report}

    def handle_qmean(self, ctx: JobContext, p: dict) -> dict:
        """QMEAN locale (UniClust30/UniRef30 sul disco esterno) oppure SWISS-MODEL, secondo le Impostazioni.

        Fallback facoltativo: se il calcolo locale non è possibile o non riesce e l'utente lo ha consentito
        (impostazione o scelta del singolo calcolo), il modello viene inviato a SWISS-MODEL.
        """
        settings = ctx.settings
        path, label = self.resolve_structure(p.get("source", {}))
        from ..domain import structure as st_mod

        pdb_text = st_mod.pdb_text(st_mod.load_structure(path, label))
        mode = p.get("mode") or settings.qmean_mode
        if mode == "auto":  # richieste della versione precedente
            mode = "local"
        fallback = settings.qmean_fallback_remote if p.get("fallback") is None else bool(p.get("fallback"))

        def swissmodel(reason: Optional[str] = None) -> dict:
            if not settings.qmean_remote:
                raise AppError("L'invio a SWISS-MODEL non è consentito: abilitalo nelle Impostazioni.",
                               code="qmean_disabled")
            if not settings.contact_email:
                raise AppError("Per QMEAN su SWISS-MODEL imposta la tua email nelle Impostazioni.",
                               code="email_required")
            ctx.progress(0.1, "Invio a SWISS-MODEL QMEAN")
            out = remote.swissmodel_qmean(pdb_text, settings.contact_email, check=ctx.check, log=ctx.log)
            if reason:
                out["fallback"] = {"from": "local", "reason": reason}
            return out

        if mode == "remote":
            result = swissmodel()
        else:
            try:
                ctx.progress(0.05, "QMEAN locale")
                result = qmean_local.score(pdb_text, ctx, method=p.get("method", "auto"),
                                           database=p.get("database") or settings.qmean_uniclust,
                                           timeout_minutes=settings.qmean_timeout_minutes)
            except JobCancelled:
                raise
            except AppError as exc:  # comprende ToolUnavailable (servizio o database assenti) e timeout
                reason = exc.message.splitlines()[0]
                if not fallback:
                    hint = "" if settings.remote_qmean_allowed() else \
                        " Puoi attivare il fallback su SWISS-MODEL nelle Impostazioni."
                    raise AppError(f"{reason}{hint}", code=exc.code, status=exc.status) from exc
                if not settings.remote_qmean_allowed():
                    raise AppError(f"{reason.rstrip('.')}. Il fallback su SWISS-MODEL è attivo ma serve anche il consenso "
                                   "all'invio e l'email nelle Impostazioni.", code="qmean_unavailable") from exc
                ctx.log(f"QMEAN locale non riuscito ({reason}): fallback su SWISS-MODEL")
                try:
                    result = swissmodel(reason)
                except AppError as remote_exc:
                    raise AppError(f"QMEAN locale: {reason.rstrip('.')}. Fallback su SWISS-MODEL non riuscito: "
                                   f"{remote_exc.message}", code=remote_exc.code) from remote_exc
        return {"source": p.get("source"), "label": label, **result}


def load_quality_report(jobs: JobManager, job_id: str, model_file: str) -> Optional[dict]:
    try:
        return read_json(jobs.file_path(job_id, f"{model_file}.quality.json"))
    except AppError:
        return None
