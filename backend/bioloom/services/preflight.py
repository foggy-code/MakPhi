"""Controlli preventivi (F8a): prima di avviare un calcolo, che cosa lo farebbe fallire o renderebbe il risultato poco
affidabile, senza avviare nulla.

`PreflightService.run(kind, params, context)` riceve il tipo di calcolo e lo stesso corpo della rotta di avvio (già
validato con lo stesso modello Pydantic in api/compute.py) e restituisce gli esiti ordinati (errori, avvisi,
informazioni), la stima del tempo e, per la modellazione, la verifica di copertura di services/coverage.py (che
l'interfaccia mostra con CoverageAlerts). Le regole e le soglie stanno in domain/preflight.py; qui si raccolgono i
fatti: licenze e strumenti, libreria dei template, spazio libero, stato del servizio QMEAN, job attivi.

- **Stessa validazione dell'avvio:** i controlli che la rotta di avvio o l'handler fanno subito (opzioni di MODELLER,
  sequenza, template, PIR, sequenze della pipeline, sorgente della struttura, allineamento dell'analisi) sono le stesse
  funzioni, e un loro errore diventa un esito `error` con lo stesso codice e messaggio.
- **Nessun effetto:** niente job, niente download (un template assente e scaricabile è un'informazione), niente
  richieste remote salvo UniProt e InterPro per i domini (solo con l'accessione, in cache 7 giorni, come
  `/api/bio/coverage`) e lo stato del servizio QMEAN locale (rete interna, in cache 15 s).
- **Robustezza:** un fatto che non si riesce a leggere (file illeggibile, servizio che non risponde) diventa
  un'informazione "controllo non eseguito", mai un errore del server.
"""

from __future__ import annotations

import copy
import os
import shutil
from pathlib import Path
from typing import TYPE_CHECKING, Callable, Optional

from ..core.errors import AppError
from ..domain import complexes, disorder, plddt, report, sequences, structure
from ..domain import preflight as rules
from ..engines import modeller, qmean_local, remote, search, tools
from .analysis import AnalysisService
from .compute import MAX_SEQUENCE, _apply_ligand_options, _record, _template_specs, complex_templates
from .coverage import CoverageService
from .pipeline import effective_parallel
from .templates import normalize_id

if TYPE_CHECKING:
    from ..core.container import Core

KINDS = ("model", "loop", "align2d", "pipeline", "quality", "qmean", "search", "analysis", "predict")


def disk_free(path: Path) -> Optional[int]:
    """Byte liberi sul disco di `path` (None se non si può leggere)."""
    try:
        return shutil.disk_usage(path).free
    except OSError:
        return None


def same_disk(a: Path, b: Path) -> bool:
    try:
        return os.stat(a).st_dev == os.stat(b).st_dev
    except OSError:
        return False


def _downloadable(template_id: str) -> Optional[str]:
    """Da dove il job scaricherebbe un template assente dalla libreria (come ComputeService._prepare_templates)."""
    tid = template_id.strip()
    if len(tid) == 4 and tid[0].isdigit():
        return "RCSB"
    if remote.parse_alphafold_id(tid):
        return "AlphaFold DB (EBI)"
    return None


class PreflightService:
    def __init__(self, core: "Core") -> None:
        self.core = core
        self.library = core.library
        self.settings = core.settings.get()

    # ------------------------------------------------------------------------------ ingresso
    def run(self, kind: str, params: dict, context: Optional[dict] = None) -> dict:
        if kind not in KINDS:
            raise AppError(f"Tipo di calcolo sconosciuto per i controlli: {kind}.", code="preflight_kind")
        context = context or {}
        out: dict = {"checks": [], "estimate": None, "coverage": None}
        handler: Callable[[dict, dict, dict], None] = getattr(self, f"_{kind}")
        handler(params, context, out)
        checks = rules.ordered(out["checks"])
        counts = rules.counts(checks)
        return {"kind": kind, "ok": counts["error"] == 0, "counts": counts, "checks": checks,
                "estimate": out["estimate"], "coverage": out["coverage"]}

    @staticmethod
    def _guard(out: dict, what: str, fn: Callable[[], None]) -> None:
        """Esegue un controllo; un fatto illeggibile diventa un'informazione, non un errore del server."""
        try:
            fn()
        except (AppError, OSError, ValueError, KeyError) as exc:
            message = exc.message if isinstance(exc, AppError) else str(exc)
            out["checks"].append(rules.check("info", "check_skipped", f"Controllo non eseguito ({what}): {message}"))

    # ------------------------------------------------------------------------------ fatti comuni
    def _modeller(self, out: dict, *, required: bool = True, purpose: str = "la modellazione") -> None:
        status = self.core.licenses.status()["modeller"]
        out["checks"] += rules.modeller_checks(status, required=required, purpose=purpose)

    def _optional_tools(self, out: dict, *, procheck: bool, prosa: bool) -> None:
        if not (procheck or prosa):
            return
        usable: dict[str, Optional[str]] = {"procheck": None, "prosa": None}
        if procheck and tools.find("procheck") is None:
            usable["procheck"] = "non è installato in questa immagine."
        if prosa:
            st = self.core.licenses.prosa_status()
            if not st["installed"]:
                usable["prosa"] = "non è installato in questa immagine."
            elif st.get("error"):
                usable["prosa"] = f"file di licenza non valido ({st['error']})."
            elif not st["license"]:
                usable["prosa"] = "manca la licenza (file LicenseDB)."
            elif not st["mac_ok"]:
                usable["prosa"] = (f"la licenza vale per la scheda di rete {st['mac_required']}, il container ha "
                                   f"{st['mac_current'] or 'un MAC sconosciuto'} (avvia BioLoom dall'avviatore).")
        out["checks"] += rules.optional_tool_checks({"procheck": procheck, "prosa": prosa}, usable)

    def _queue(self, out: dict) -> None:
        out["checks"] += rules.queue_checks(self.core.jobs.count_active(), self.settings.effective_workers())

    def _disk(self, out: dict, data_need: int, scratch_need: int) -> None:
        paths = self.core.paths
        out["checks"] += rules.disk_checks(data_free=disk_free(paths.data), data_need=data_need,
                                           scratch_free=disk_free(paths.scratch), scratch_need=scratch_need,
                                           same_disk=same_disk(paths.data, paths.scratch))

    def _templates(self, out: dict, raw: list[dict]) -> tuple[list, list]:
        """Template scelti: validazione come la modellazione, presenza nella libreria e catene. Restituisce tutte le
        specifiche e quelle dei template presenti nella libreria con catene valide."""
        try:
            specs = _template_specs(raw)
        except AppError as exc:
            out["checks"].append(rules.invalid(exc.code, exc.message))
            return [], []
        valid = []
        for spec in specs:
            try:
                in_library = self.library.exists(spec.id)
            except AppError:
                in_library = False
            out["checks"] += rules.template_availability(spec.id, in_library=in_library,
                                                         downloadable=_downloadable(spec.id))
            if not in_library:
                continue
            try:
                analysis = self.library.analyze(spec.id)
            except (AppError, OSError, ValueError) as exc:  # file illeggibile: la preparazione fallirebbe allo stesso modo
                out["checks"].append(rules.check("error", "template_unreadable",
                                                 f"Il template {normalize_id(spec.id)} non si legge: "
                                                 f"{getattr(exc, 'message', exc)}", template=normalize_id(spec.id)))
                continue
            available = [c["chain"] for c in analysis.get("chains") or []]
            missing = [c for c in spec.chains if c not in available]
            if missing:
                out["checks"].append(rules.check(
                    "error", "template_chain_missing",
                    f"Il template {normalize_id(spec.id)} non ha la catena {', '.join(missing)} (disponibili: "
                    f"{', '.join(available) or 'nessuna'}).", template=normalize_id(spec.id)))
                continue
            valid.append(spec)
        return specs, valid

    def _trims(self, out: dict, specs: list, opts: modeller.ModelOptions) -> None:
        """Ritaglio pLDDT dei template AlphaFold scelti, calcolato come nella modellazione (`plddt.low_confidence`)."""
        trims = []
        for spec in specs:
            if not self.library.is_predicted(spec.id):
                continue
            tid = normalize_id(spec.id)
            model = structure.first_model(structure.load_structure(self.library.path(tid), tid))
            chains = spec.chains or [c["chain"] for c in structure.chain_summaries(model)][:1]
            if opts.plddt_trim:
                trim = plddt.low_confidence(model, chains, float(opts.plddt_min))
                rows = trim["rows"]
                kept = sum(1 for r in rows if r[5])
                empty = [c for c in chains if not any(r[0] == c and r[5] for r in rows)]
                ranges = ", ".join(f"{a}–{b}:{c}" if a != b else f"{a}:{c}" for a, b, c in plddt.removed_ranges(rows))
                trims.append({"template": tid, "chains": chains, "threshold": float(opts.plddt_min),
                              "total": len(rows), "kept": kept, "empty": empty, "ranges": ranges})
            else:
                values = [v for _c, _r, v in plddt.residues(model, chains)]
                trims.append({"template": tid, "chains": chains, "threshold": None, "total": len(values),
                              "low": sum(1 for v in values if v < plddt.DEFAULT_THRESHOLD)})
        out["checks"] += rules.plddt_checks(trims)

    def _search_facts(self, engine: str, accession: str = "") -> dict:
        databases = self.core.compute.databases_dir
        facts = {"email": bool(self.settings.contact_email), "accession": accession}
        if engine == "local":
            facts.update(blastp=tools.find("blastp") is not None, pdbaa=search.local_database(databases) is not None)
        elif engine == "mmseqs":
            facts.update(mmseqs=tools.find("mmseqs") is not None,
                         mmseqs_db=search.mmseqs_database(databases) is not None,
                         mmseqs_index=search.mmseqs_local_index(databases) is not None)
        return facts

    # ------------------------------------------------------------------------------ modellazione
    def _model(self, p: dict, context: dict, out: dict) -> None:
        checks = out["checks"]
        opts: Optional[modeller.ModelOptions] = None
        try:
            opts = modeller.parse_options(p.get("options") or {})
        except (AppError, TypeError, ValueError) as exc:
            checks.append(rules.invalid(getattr(exc, "code", "invalid_options"), getattr(exc, "message", str(exc))))
        alignment = p.get("alignment") or None
        target = p.get("target") or {}
        residues, target_seq = 0, ""
        if p.get("complex"):
            # modalità Complesso (F5.3): stessa validazione della rotta di avvio (api/compute._complex); i tempi e lo
            # spazio contano tutte le catene, gli avvisi di copertura per catena non si calcolano
            try:
                parsed = complexes.parse_target(p["complex"])
                complex_templates(parsed, p.get("templates") or [])
                residues = sum(len(c.sequence) for c in parsed["chains"])
            except AppError as exc:
                checks.append(rules.invalid(exc.code, exc.message))
        elif alignment:
            residues, target_seq = self._pir(out, alignment, len(p.get("templates") or []))
        elif target.get("sequence"):
            try:
                record = _record(target)
                residues, target_seq = len(record.sequence), record.sequence
            except AppError as exc:
                checks.append(rules.invalid(exc.code, exc.message))
        else:
            # come la rotta di avvio (api/compute.submit_model)
            checks.append(rules.invalid("bad_request", "Indica una sequenza bersaglio oppure un allineamento PIR."))
        chosen, specs = self._templates(out, p.get("templates") or [])
        if opts is not None and chosen:
            try:
                _apply_ligand_options(copy.deepcopy(chosen), opts)
            except AppError as exc:
                checks.append(rules.invalid(exc.code, exc.message))
        self._modeller(out)
        if opts is not None and specs:
            self._guard(out, "ritaglio pLDDT", lambda: self._trims(out, specs, opts))
        if residues and specs and target_seq:
            self._guard(out, "copertura e identità", lambda: self._coverage(
                out, target_seq=target_seq, alignment=alignment, specs=specs, context=context,
                plddt_min=opts.plddt_min if opts is not None and opts.plddt_trim else None))
        if context.get("search_job") and specs:
            self._guard(out, "punteggio dei template", lambda: self._rank(out, str(context["search_job"]), specs,
                                                                           target_seq))
        quality_mode = p.get("quality") or "best"
        if quality_mode != "none" and p.get("extended_quality"):
            self._optional_tools(out, procheck=True, prosa=True)
        if opts is not None and residues:
            est = rules.modeling_seconds(residues, opts.models, opts.refinement, quality_mode,
                                         bool(p.get("extended_quality")), self.settings.effective_workers())
            detail = (f"{residues} residui, {opts.models} {'modello' if opts.models == 1 else 'modelli'}, "
                      f"raffinamento {rules.REFINEMENT_LABEL.get(opts.refinement, opts.refinement)}, qualità "
                      + {"best": "sul migliore", "all": "su tutti i modelli", "none": "non calcolata"}[quality_mode])
            out["estimate"] = rules.estimate(est, detail=detail)
            checks += rules.runtime_checks(out["estimate"], what="La modellazione",
                                           process_seconds=rules.building_seconds(residues, opts.models,
                                                                                  opts.refinement),
                                           timeout_seconds=self.settings.job_timeout_minutes * 60)
            analysed = 0 if quality_mode == "none" else opts.models if quality_mode == "all" else 1
            self._disk(out, *rules.modeling_bytes(residues, opts.models, analysed))
            checks += rules.single_model_checks(opts.models)
            checks += rules.identical_models_checks(opts.models, opts.refinement)
        self._queue(out)

    def _pir(self, out: dict, text: str, n_templates: int) -> tuple[int, str]:
        """PIR dell'utente: stessa validazione della modellazione (`validate_pir`, numero dei template)."""
        rep = sequences.validate_pir(text)
        if not rep["valid"]:
            out["checks"].append(rules.invalid("invalid_pir", "Allineamento PIR non valido: " + " ".join(rep["errors"])))
            return 0, ""
        entries = sequences.parse_pir(text)
        found = sum(1 for e in entries if e.is_template)
        if n_templates and found != n_templates:
            out["checks"].append(rules.invalid("invalid_pir", f"Il PIR contiene {found} template ma ne sono stati "
                                                              f"selezionati {n_templates}."))
        target = next((e for e in entries if e.kind == "sequence"), None)
        letters = "".join(c for c in (target.sequence if target else "") if c.isalpha())
        if len(letters) > MAX_SEQUENCE:
            out["checks"].append(rules.invalid("sequence_too_long", f"Sequenza troppo lunga ({len(letters)} residui, "
                                                                    f"massimo {MAX_SEQUENCE})."))
        return len(letters), target.sequence if target else ""

    def _coverage(self, out: dict, *, target_seq: str, alignment: Optional[str], specs: list, context: dict,
                  plddt_min: Optional[float]) -> None:
        """Copertura del bersaglio (disordine, domini) e identità dei template, con services/coverage.py."""
        accession = (context.get("accession") or "").strip() or None
        if accession:
            try:
                accession = remote.validate_uniprot_accession(accession)
            except AppError as exc:
                out["checks"].append(rules.check("info", "invalid_accession", f"Domini non valutati: {exc.message}"))
                accession = None
        service = CoverageService(self.library)
        result = service.check(sequence="" if alignment else target_seq, accession=accession,
                               templates=[] if alignment else [{"id": s.id, "chains": s.chains} for s in specs],
                               alignment=alignment, plddt_min=plddt_min)
        out["coverage"] = result
        combined = result.get("combined")
        out["checks"] += rules.coverage_checks(combined)
        entries = [t for t in result.get("templates") or [] if not t.get("error")]
        if alignment and result.get("note"):  # bersaglio a più catene: identità sulle colonne del PIR
            parsed = sequences.parse_pir(alignment)
            target = next((e for e in parsed if e.kind == "sequence"), None)
            letters = sum(1 for c in target.sequence if c.isalpha()) if target else 0
            entries = []
            for e in parsed:
                if e.is_template and target is not None:
                    identity, cov = report.alignment_identity(target.sequence, e.sequence)
                    entries.append({"label": e.code, "identity": identity,
                                    "aligned": round((cov or 0) * letters / 100)})
        coverage = combined.get("coverage") if combined else None
        out["checks"] += rules.similarity_checks(
            [{"label": t["label"], "identity": t.get("identity"), "aligned": t.get("aligned")} for t in entries],
            coverage)

    def _rank(self, out: dict, job_id: str, specs: list, target_seq: str) -> None:
        """Punteggio dei template scelti nella ricerca indicata (se è una ricerca completata sulla stessa sequenza)."""
        job = self.core.jobs.get(job_id)
        result = job.get("result") if job.get("kind") == "search" and job.get("status") == "completed" else None
        if not isinstance(result, dict):
            return
        query = str((result.get("query") or {}).get("sequence") or "").upper()
        target = "".join(c for c in target_seq if c.isalpha()).upper()
        if target and query and query != target:
            return  # la ricerca era per un'altra sequenza: i suoi punteggi non valgono
        wanted = {(normalize_id(s.id), c) for s in specs for c in (s.chains or [""])}
        hits = [h for h in result.get("hits") or []
                if (str(h.get("pdb_id") or "").upper(), str(h.get("chain") or "")) in wanted]
        out["checks"] += rules.rank_checks(hits)

    def _align2d(self, p: dict, context: dict, out: dict) -> None:
        try:
            _record(p.get("target") or {})
        except AppError as exc:
            out["checks"].append(rules.invalid(exc.code, exc.message))
        self._templates(out, p.get("templates") or [])  # align2d li scarica come la modellazione
        self._modeller(out, purpose="l'allineamento con MODELLER")
        self._queue(out)

    def _loop(self, p: dict, context: dict, out: dict) -> None:
        try:
            self.core.compute.resolve_structure(p.get("source") or {})
        except AppError as exc:
            out["checks"].append(rules.invalid(exc.code, exc.message))
        length = int(p.get("end", 0)) - int(p.get("start", 0))
        if length < 1 or length > 30:  # come ComputeService.handle_loop
            out["checks"].append(rules.invalid("invalid_loop", "Il loop deve contenere da 2 a 31 residui."))
        self._modeller(out, purpose="il raffinamento del loop")
        self._queue(out)

    # ------------------------------------------------------------------------------ pipeline
    def _pipeline(self, p: dict, context: dict, out: dict) -> None:
        checks = out["checks"]
        try:
            records = self.core.pipeline.validate(p)
        except (AppError, TypeError, ValueError) as exc:
            checks.append(rules.invalid(getattr(exc, "code", "invalid_options"), getattr(exc, "message", str(exc))))
            self._modeller(out, purpose="la pipeline")
            return
        opts = modeller.parse_options(p.get("options") or {})
        self._modeller(out, purpose="la pipeline")
        mode = p.get("template_mode") or "auto"
        engine = p.get("engine") or "rcsb"
        items = len(records)
        too_long = [r.name for r in records if len(r.sequence) > MAX_SEQUENCE]
        if too_long:
            checks.append(rules.check(
                "warning", "pipeline_too_long",
                f"{len(too_long)} {'sequenza supera' if len(too_long) == 1 else 'sequenze superano'} i {MAX_SEQUENCE} "
                f"residui e {'fallirà' if len(too_long) == 1 else 'falliranno'}: {', '.join(too_long[:5])}"
                + ("…" if len(too_long) > 5 else "") + ".", "Dividile in domini o toglile dal multi-FASTA."))
        search_time = None
        parallel = effective_parallel(p, self.settings.effective_workers())
        if mode == "fixed":
            _all, specs = self._templates(out, p.get("templates") or [])
            if specs:
                self._guard(out, "ritaglio pLDDT", lambda: self._trims(out, specs, opts))
        elif engine == "alphafold":
            # accessione dall'intestazione FASTA di UniProt (sp|ACC|NOME), come l'esecuzione della pipeline
            without = sum(1 for r in records if not search.uniprot_from_name(r.name))
            checks += rules.pipeline_alphafold_checks(items, without)
            search_time = rules.mixed_search_time(
                [rules.SEARCH_SECONDS["alphafold_blast"]] * without + [rules.SEARCH_SECONDS["alphafold"]] *
                (items - without))
            checks += rules.pipeline_remote_checks(engine=engine, items=items, ncbi_searches=without, parallel=parallel)
        else:
            facts = self._search_facts(engine)
            checks += rules.search_checks(engine, facts)
            search_time = rules.search_time(engine, facts)
            checks += rules.pipeline_remote_checks(engine=engine, items=items,
                                                   ncbi_searches=items if engine == "ncbi" else 0, parallel=parallel)
        quality = bool(p.get("quality", True))
        extended = quality and bool(p.get("extended_quality"))
        if extended:
            self._optional_tools(out, procheck=True, prosa=True)
        mean = round(sum(len(r.sequence) for r in records) / max(1, items))
        out["estimate"] = rules.pipeline_estimate(items, residues=mean, models=opts.models,
                                                  refinement=opts.refinement, quality=quality, extended=extended,
                                                  search=search_time, parallel=parallel)
        checks += rules.runtime_checks(out["estimate"], what="La pipeline",
                                       process_seconds=rules.building_seconds(max(len(r.sequence) for r in records),
                                                                              opts.models, opts.refinement),
                                       timeout_seconds=self.settings.job_timeout_minutes * 60)
        per_item = [rules.modeling_bytes(len(r.sequence), opts.models, 1 if quality else 0) for r in records]
        largest = sorted((s for _r, s in per_item), reverse=True)[:parallel]
        self._disk(out, sum(r for r, _s in per_item), sum(largest))
        checks += rules.identical_models_checks(opts.models, opts.refinement)
        self._guard(out, "disordine delle sequenze", lambda: self._pipeline_disorder(out, records))
        self._queue(out)

    @staticmethod
    def _pipeline_disorder(out: dict, records: list) -> None:
        names, analysed, residues = [], 0, 0
        for r in records:
            if residues + len(r.sequence) > rules.PIPELINE_DISORDER_MAX_RESIDUES:
                break
            residues += len(r.sequence)
            analysed += 1
            _scores, mask = disorder.residue_mask(r.sequence)
            if any(mask):
                names.append(r.name)
        out["checks"] += rules.pipeline_disorder_checks(names, analysed, len(records))

    # ------------------------------------------------------------------------------ qualità e QMEAN
    def _quality(self, p: dict, context: dict, out: dict) -> None:
        try:
            self.core.compute.resolve_structure(p.get("source") or {})
        except AppError as exc:
            out["checks"].append(rules.invalid(exc.code, exc.message))
        if p.get("dope", True):
            self._modeller(out, required=False)
        self._optional_tools(out, procheck=bool(p.get("procheck", True)), prosa=bool(p.get("prosa", True)))
        self._queue(out)

    def _qmean(self, p: dict, context: dict, out: dict) -> None:
        try:
            self.core.compute.resolve_structure(p.get("source") or {})
        except AppError as exc:
            out["checks"].append(rules.invalid(exc.code, exc.message))
        settings = self.settings
        mode = p.get("mode") or settings.qmean_mode
        mode = "local" if mode == "auto" else mode  # richieste della versione precedente, come handle_qmean
        fallback = settings.qmean_fallback_remote if p.get("fallback") is None else bool(p.get("fallback"))
        status, problem = None, None
        if mode == "local":
            status = qmean_local.status(p.get("database") or settings.qmean_uniclust)
            uniclust = status.get("uniclust") or {}
            if uniclust.get("valid"):
                problem = qmean_local.memory_problem(uniclust, status.get("memory"))
        out["checks"] += rules.qmean_checks(mode=mode, status=status, memory_problem=problem, fallback=fallback,
                                            remote_consent=settings.qmean_remote, email=bool(settings.contact_email))

    # ------------------------------------------------------------------------------ ricerca e analisi
    def _search(self, p: dict, context: dict, out: dict) -> None:
        try:
            _record(p.get("query") or {})
        except AppError as exc:
            out["checks"].append(rules.invalid(exc.code, exc.message))
        engine = p.get("engine") or "rcsb"
        accession = str(p.get("uniprot") or "").strip() if engine == "alphafold" else ""
        if accession:
            try:
                accession = remote.validate_uniprot_accession(accession)
            except AppError as exc:
                out["checks"].append(rules.invalid(exc.code, exc.message))
                accession = ""
        facts = self._search_facts(engine, accession)
        out["checks"] += rules.search_checks(engine, facts)
        low, high = rules.search_time(engine, facts)
        out["estimate"] = rules.estimate((low + high) / 2, low, high, detail=search.ENGINES.get(engine, engine),
                                         basis=rules.SEARCH_BASIS)

    def _analysis(self, p: dict, context: dict, out: dict) -> None:
        try:
            AnalysisService(self.core.jobs, self.core.compute).validate(p)
        except AppError as exc:
            out["checks"].append(rules.invalid(exc.code, exc.message))

    # ------------------------------------------------------------------------------ predizione esterna (F9.3)
    def _predict(self, p: dict, context: dict, out: dict) -> None:
        """Consenso all'invio (con la stessa validazione della rotta, salvo il consenso, che è un esito proprio),
        lunghezza e alfabeto per ESMFold, accessione per AlphaFold DB, rete o cache, tempo e spazio. Nessuna richiesta
        remota: la cache delle predizioni si legge dal disco."""
        from .predict import PredictService  # services/predict importa services/compute come questo modulo

        checks = out["checks"]
        settings = self.settings
        predictor = p.get("predictor") or "esmfold"
        try:
            req = PredictService(self.core.jobs, self.core.compute).request(p, settings, require_consent=False)
        except AppError as exc:
            checks.append(rules.invalid(exc.code, exc.message))
            req = None
        length = len((req or {}).get("sequence") or "")
        cached = None
        if req and predictor == "esmfold":
            hit = remote.esmfold_cached(req["sequence"], req["service"])
            cached = str(hit.get("received") or "?") if hit else None
        in_library = False
        if req and req.get("accession"):
            try:
                in_library = self.library.exists(req["accession"] if req["accession"].startswith("AF-")
                                                 else f"AF-{req['accession']}-F1")
            except AppError:
                in_library = False
        checks += rules.predict_checks(predictor=predictor, consent=settings.esmfold_remote,
                                       service=(req or {}).get("service"), length=length, cached=cached,
                                       compare=bool(p.get("source")), accession=(req or {}).get("accession"),
                                       in_library=in_library)
        if req:
            out["estimate"] = rules.predict_estimate(predictor, cached=bool(cached or in_library),
                                                     compare=bool(p.get("source")), length=length)
            checks += rules.runtime_checks(out["estimate"], what="Il confronto con la predizione"
                                           if p.get("source") else "La predizione")
            need = rules.predict_bytes(length)
            self._disk(out, need, need)
        self._queue(out)
