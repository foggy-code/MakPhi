"""Gestori dei job di calcolo: ricerca, allineamenti, MODELLER, qualità, QMEAN."""

from __future__ import annotations

import copy
import shutil
import threading
from dataclasses import asdict
from functools import partial
from pathlib import Path, PurePosixPath
from typing import Callable, Optional

from ..core import cache
from ..core.errors import AppError, Conflict, NotFound
from ..core.jobs import QMEAN_LOCAL, JobCancelled, JobContext, JobManager
from ..core.parallel import Steps
from ..core.storage import atomic_write_json, atomic_write_text, read_json, safe_join
from ..domain import consensus, sequences
from ..domain.sequences import SeqRecord
from ..engines import align, modeller, qmean_local, quality, remote, search
from ..engines.modeller import TemplateSpec
from .templates import TemplateLibrary, normalize_id

MAX_SEQUENCE = 5000
# Qualità "all": modelli dello stesso job analizzati insieme (non oltre i job paralleli delle Impostazioni). Ogni
# analisi lancia fino a 4 processi brevi (DOPE, mkdssp, PROCHECK, ProSA): con 3 analisi un computer a 4 core resta
# occupato senza code lunghe (misure in ARCHITETTURA.md, "Prestazioni"); i processi li governa comunque il budget di
# CPU del JobManager.
QUALITY_MODELS_PARALLEL = 3


class PreparedTemplates:
    """Template preparati per MODELLER riusati fra gli elementi di una pipeline (F1.2).

    `prepare_template` legge tutta la struttura anche per una sola catena (un mmCIF di 4560 residui: circa 1 s, e il
    doppio con due elementi in parallelo; misure in ARCHITETTURA.md): gli elementi con lo stesso template e le stesse
    opzioni ricevono una copia del file già preparato nella propria cartella di lavoro.

    - **Chiave:** id del template, file della libreria (percorso, dimensione, data di modifica in ns), catene, ligandi
      e soglia del ritaglio pLDDT (None = nessun ritaglio). Un template sostituito nella libreria durante il job
      (nuovo download o caricamento con lo stesso id) cambia chiave e si prepara di nuovo.
    - **Dove:** cartella nascosta dello scratch del job (`.tmp/templates`), mai tra i risultati e svuotata a fine
      job: la cache vive quanto un'esecuzione, e una ripresa ne crea una nuova.
    - **Concorrenza:** un lock per chiave; un elemento parallelo con lo stesso template aspetta la prima preparazione
      (controllando l'annullamento) invece di rifarla.
    - **Errori:** una preparazione non riuscita non si conserva: l'elemento successivo riprova e registra l'errore
      nel proprio log.
    """

    def __init__(self, root: Path) -> None:
        self.root = root
        self._lock = threading.Lock()
        self._key_locks: dict[tuple, threading.Lock] = {}
        self._entries: dict[tuple, tuple[Path, dict]] = {}
        self._folders = 0
        self.prepared = 0
        self.reused = 0

    @staticmethod
    def key(source: Path, spec: TemplateSpec) -> tuple:
        stat = source.stat()
        return (spec.id, str(source), stat.st_size, stat.st_mtime_ns, tuple(spec.chains), bool(spec.hetatm),
                None if spec.plddt_min is None else float(spec.plddt_min))

    def get(self, source: Path, spec: TemplateSpec, dest_dir: Path,
            check: Callable[[], None]) -> tuple[dict, bool]:
        """Informazioni del template preparato (copia indipendente) e se la preparazione è riusata; il file
        preparato viene copiato in `dest_dir`."""
        key = self.key(source, spec)
        with self._lock:
            lock = self._key_locks.setdefault(key, threading.Lock())
        while not lock.acquire(timeout=0.5):
            check()
        try:
            check()  # svegliato dopo l'annullamento: nessuna preparazione
            entry = self._entries.get(key)
            reused = entry is not None
            if entry is None:
                with self._lock:
                    self._folders += 1
                    folder = self.root / f"{self._folders:04d}"
                entry = (folder, modeller.prepare_template(source, spec, folder))
                self._entries[key] = entry
            with self._lock:
                if reused:
                    self.reused += 1
                else:
                    self.prepared += 1
        finally:
            lock.release()
        folder, info = entry
        dest_dir.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(folder / info["file"], dest_dir / info["file"])
        return copy.deepcopy(info), reused


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


def _removed_text(trim: dict) -> str:
    """"11 residui con pLDDT < 70", con i frammenti isolati se ce ne sono ("… e 3 in frammenti isolati di meno di 5
    residui")."""
    below = trim.get("removed_threshold", trim["removed"])
    fragments = trim.get("removed_fragments") or 0
    text = f"{below} {'residuo' if below == 1 else 'residui'} con pLDDT < {trim['threshold']:g}"
    if fragments:
        text += f" e {fragments} in frammenti isolati di meno di {trim.get('min_fragment', 5)} residui"
    return text


def _apply_plddt_trim(entry: sequences.PirEntry, fields: list[str], t: dict) -> None:
    """Riporta una riga PIR di template sul template ritagliato per pLDDT (residui tolti = gap).

    La riga può descrivere il template completo (allineamento fatto prima del ritaglio, per esempio con l'Editor
    PIR): i residui tolti diventano gap, così il bersaglio in quelle posizioni resta senza template, e l'intervallo
    passa al primo e all'ultimo residuo rimasti. Una riga che descrive già il template ritagliato resta com'è. Se la
    riga non corrisponde a nessuno dei due, l'allineamento non è per questo template: errore chiaro (MODELLER
    fallirebbe con un messaggio poco leggibile).
    """
    rows = t["_residues"]  # (catena, residuo, lettera, tenuto) del template completo, nell'ordine del file
    code = t["code"]

    def locate(resid: str, chain: str, default: int) -> int:
        value = (resid or "").strip()
        if not value or value.upper() in ("FIRST", "LAST", "."):
            return default
        chain = (chain or "").strip() or None
        for i, (c, r, _aa, _keep) in enumerate(rows):
            if r == value and (chain is None or c == chain):
                return i
        raise AppError(f"Il PIR indica il residuo {value}:{chain or '?'} che non esiste nel template {code}.",
                       code="invalid_pir")

    start = locate(fields[0], fields[1], 0)
    end_field = (fields[2] or "").strip()
    if end_field.startswith("+") and end_field[1:].isdigit():  # "+N": N residui a partire dal primo
        end = start + int(end_field[1:]) - 1
    else:
        end = locate(fields[2], fields[3], len(rows) - 1)
    segment = rows[start:end + 1]
    if not segment:
        raise AppError(f"Intervallo di residui del template {code} vuoto nel PIR.", code="invalid_pir")
    letters = "".join(c for c in entry.sequence if c.isalpha())
    full = "".join(r[2] for r in segment)
    kept_rows = [r for r in segment if r[3]]
    kept = "".join(r[2] for r in kept_rows)
    if not kept_rows:
        raise AppError(f"Nessun residuo del template {code} nell'intervallo del PIR supera la soglia pLDDT.",
                       code="plddt_too_few")
    if letters == full and full != kept:
        out, k = [], 0
        for ch in entry.sequence:
            if ch.isalpha():
                out.append(ch if segment[k][3] else "-")
                k += 1
            else:
                out.append(ch)
        entry.sequence = "".join(out)
    elif letters != kept:
        threshold = t["plddt"]["threshold"]
        raise AppError(f"Nel PIR la sequenza del template {code} non corrisponde al file del template, né completo né "
                       f"ritagliato per pLDDT < {threshold:g} ({len(letters)} residui nel PIR, {len(full)} nel "
                       f"template, {len(kept)} dopo il ritaglio).", code="invalid_pir")
    if fields[0] and fields[0].upper() not in ("FIRST", "."):
        fields[0], fields[1] = kept_rows[0][1], kept_rows[0][0]
    if fields[2] and fields[2].upper() not in ("LAST", "."):
        fields[2], fields[3] = kept_rows[-1][1], kept_rows[-1][0]


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
        jobs.register("qmean", self.handle_qmean, pool="io", resource=self._qmean_resource)

    def _qmean_resource(self, p: dict) -> Optional[str]:
        """QMEAN locale: al massimo un calcolo alla volta (il servizio ha una coda sola e HHblits richiede molta
        memoria). Gli altri restano in coda in BioLoom, senza occupare un worker del pool "io".

        Nessuna risorsa per SWISS-MODEL e quando il servizio locale non c'è (l'avviatore non ha trovato UniClust30:
        `base_url` vuoto, una variabile d'ambiente letta senza I/O sotto il lock del dispatcher): il job fallisce
        subito o passa al fallback, e i fallback partono insieme. Con il servizio presente la risorsa si restituisce
        appena finisce la parte locale, prima di un eventuale fallback (`handle_qmean`)."""
        mode = p.get("mode") or self.jobs.settings_provider().qmean_mode
        if mode == "remote" or not qmean_local.base_url():
            return None
        return QMEAN_LOCAL

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
                               use_cache=bool(p.get("use_cache", True)), uniprot=str(p.get("uniprot") or "").strip())
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
                           auto_fetch: bool = True, plddt_min: Optional[float] = None,
                           cache: Optional[PreparedTemplates] = None) -> list[dict]:
        """Template preparati per MODELLER; quelli assenti dalla libreria si scaricano (RCSB o AlphaFold DB).

        `plddt_min`: soglia di ritaglio per i template predetti (AlphaFold); None = nessun ritaglio. Le strutture
        sperimentali non si ritagliano mai: i loro B-factor non sono pLDDT. `cache` (pipeline): preparazioni riusate
        fra gli elementi dello stesso job.
        """
        prepared = []
        for spec in specs:
            ctx.check()
            if not self.library.exists(spec.id):
                if auto_fetch and len(spec.id) == 4:
                    ctx.log(f"Template {spec.id} non in libreria: download da RCSB")
                    self.library.fetch(spec.id)
                elif auto_fetch and remote.parse_alphafold_id(spec.id):
                    ctx.log(f"Template {spec.id} non in libreria: download da AlphaFold DB (EBI)")
                    # annullabile anche durante i tentativi e l'attesa di un altro elemento che lo sta scaricando
                    self.library.fetch_alphafold(spec.id, check=ctx.check, log=ctx.log)
                else:
                    raise AppError(f"Template {spec.id} non presente nella libreria.")
            predicted = self.library.is_predicted(spec.id)
            spec.plddt_min = plddt_min if predicted else None
            source = self.library.path(spec.id)
            if cache is not None:
                info, reused = cache.get(source, spec, workdir, ctx.check)
            else:
                info, reused = modeller.prepare_template(source, spec, workdir), False
            ctx.log(f"Template {info['code']}: catene {','.join(info['chains'])}, "
                    f"residui {info['first']}–{info['last']}, {len(info['sequence'].replace('/', ''))} aa"
                    + (" (preparazione riusata da un elemento precedente)" if reused else ""))
            if predicted:
                info["predicted"] = True
                trim = info.get("plddt")
                if trim is None:
                    ctx.log(f"Template {info['code']}: modello predetto (AlphaFold), usato senza ritaglio pLDDT")
                else:
                    where = ", ".join(f"{a}–{b}:{c}" if a != b else f"{a}:{c}" for a, b, c in trim["removed_ranges"])
                    ctx.log(f"Template {info['code']}: modello predetto (AlphaFold), rimossi {_removed_text(trim)}"
                            + (f" ({where})" if where else "")
                            + f", restano {trim['kept']} di {trim['total']}; pLDDT medio {trim['mean_before']} → "
                              f"{trim['mean_after']}")
            prepared.append(info)
        codes = [t["code"] for t in prepared]
        if len(set(codes)) != len(codes):
            raise AppError("Lo stesso template/catena è stato selezionato più volte.")
        return prepared

    @staticmethod
    def _public(templates: list[dict]) -> list[dict]:
        """Template per i risultati del job, senza le chiavi interne (es. l'elenco dei residui del ritaglio)."""
        return [{k: v for k, v in t.items() if not k.startswith("_")} for t in templates]

    @staticmethod
    def _predicted_notes(templates: list[dict]) -> list[str]:
        notes = []
        for t in templates:
            if not t.get("predicted"):
                continue
            trim = t.get("plddt")
            text = f"{t['code']}: modello predetto (AlphaFold DB), non una struttura sperimentale"
            if trim is None:
                text += "; nessun ritaglio dei residui a basso pLDDT."
            elif trim["removed"]:
                text += (f"; rimossi {_removed_text(trim)} (restano {trim['kept']} di {trim['total']}), "
                         "costruiti senza vincoli dal template.")
            else:
                text += f"; tutti i residui hanno pLDDT ≥ {trim['threshold']:g}."
            notes.append(text)
        return notes

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
        trimmed = False
        for pos, entry in enumerate(tmpl_entries):
            t = by_code.get(entry.code.upper())
            if t is None or t["code"] in used:
                t = next(x for x in templates if x["code"] not in used) if pos < len(templates) else None
            used.add(t["code"])
            entry.code = t["code"]
            fields = (entry.fields + [""] * 8)[:8]
            if t.get("_residues") and (t.get("plddt") or {}).get("removed"):
                _apply_plddt_trim(entry, fields, t)
                trimmed = True
            if not fields[0] or not fields[1]:
                fields[0:4] = [t["first"], t["chains"][0], t["last"], t["chains"][-1]]
            entry.fields = fields
            entry.kind = "structureX"
        if trimmed:  # colonne rimaste di soli gap (residui tolti allineati a gap del bersaglio)
            keep = [i for i in range(len(entries[0].sequence)) if any(e.sequence[i] != "-" for e in entries)]
            for e in entries:
                e.sequence = "".join(e.sequence[i] for i in keep)
        target = next(e for e in entries if e.kind == "sequence")
        target.code = sequences.sanitize_code(target.code, default="TARGET")
        return sequences.format_pir(entries), target.code, report["warnings"]

    def run_modeling(self, ctx: JobContext, *, target: Optional[SeqRecord], templates_raw: list[dict],
                     alignment: Optional[str], options_raw: dict, quality_mode: str = "best",
                     extended_quality: bool = False, template_cache: Optional[PreparedTemplates] = None) -> dict:
        """Flusso completo: template → allineamento → AutoModel → selezione → qualità. Usato anche dalla pipeline.

        Con la qualità "all" i modelli si analizzano in parallelo (al massimo `QUALITY_MODELS_PARALLEL`), ognuno con
        il proprio contesto e la propria cartella; i risultati restano nell'ordine dei modelli. `template_cache`
        (pipeline): template preparati riusati fra gli elementi del job.
        """
        workdir = ctx.workdir
        opts = modeller.parse_options(options_raw or {})
        specs = _template_specs(templates_raw)
        if opts.hetatm:
            for s in specs:
                s.hetatm = True
        modeller.modeller_python(ctx)  # errore immediato se MODELLER manca
        ctx.progress(0.02, "Preparazione dei template")
        templates = self._prepare_templates(ctx, specs, workdir,
                                            plddt_min=opts.plddt_min if opts.plddt_trim else None,
                                            cache=template_cache)
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
        warnings = [*warnings, *self._predicted_notes(templates)]
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

        def assess(m: dict) -> None:
            """Analisi di un modello: rapporto accanto al modello, metriche nel riepilogo, REMARK nel PDB."""
            sub = ctx.sub(m["file"], workdir / f"qa_{Path(m['file']).stem}")
            report = quality.analyze(workdir / m["file"], sub, with_dope=True,
                                     dope_workdir=workdir / f"qa_{Path(m['file']).stem}",
                                     with_procheck=extended_quality, with_prosa=extended_quality)
            # il consenso del modello include GA341 (e zDOPE, se il DOPE non è stato calcolato) di MODELLER
            report["consensus"] = consensus.compute(report, modeller=m)
            atomic_write_json(workdir / f"{m['file']}.quality.json", report)
            m["quality"] = quality.summary_metrics(report)
            quality.inject_remarks(workdir / m["file"], [
                f"MODEL {m['file']} TARGET {target_code}",
                f"TEMPLATES {' '.join(t['code'] for t in templates)}",
                *(f"TEMPLATE {t['code']} ALPHAFOLD "
                  + (f"PLDDT<{t['plddt']['threshold']:g} REMOVED {t['plddt']['removed']}" if t.get("plddt")
                     else "PLDDT_TRIM OFF")
                  for t in templates if t.get("predicted")),
                f"DOPE {m.get('dope')} ZDOPE {m.get('zdope')} GA341 {m.get('ga341')}",
                f"GRADE {m['quality']['grade']} ({m['quality']['grade_score']}) "
                f"RAMA_FAVORED {m['quality']['rama_favored']} CLASHSCORE {m['quality']['clashscore']}",
            ])

        workers = min(len(targets), QUALITY_MODELS_PARALLEL, max(1, int(ctx.settings.effective_workers())))
        if workers > 1:
            ctx.log(f"Analisi di qualità di {len(targets)} modelli, {workers} in parallelo")
        # esiti letti nell'ordine dei modelli: il primo modello fallito (in quest'ordine) fa fallire il job
        with Steps(check=ctx.check, parallel=workers > 1, workers=workers, name="modelli") as steps:
            for m in targets:
                steps.start(m["file"], partial(assess, m))
            for n, m in enumerate(targets):
                ctx.check()
                ctx.progress(0.8 + 0.18 * n / max(1, len(targets)),
                             f"Analisi di qualità di {m['file']}" if workers == 1 else
                             f"Analisi di qualità dei modelli ({n} di {len(targets)} completate)")
                steps.result(m["file"])
        target_seq = next((e.sequence for e in sequences.parse_pir(pir) if e.kind == "sequence"), "")
        return {
            "target": {"code": target_code, "sequence": target_seq.replace("-", "").replace("/", "")},
            "templates": self._public(templates),
            "plddt_removed": sum((t.get("plddt") or {}).get("removed", 0) for t in templates),
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
        # Template AlphaFold interi: in modellazione, con il ritaglio pLDDT, i residui tolti diventano gap del PIR
        return {"pir": pir, "report": sequences.validate_pir(pir), "templates": self._public(templates),
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
        # impronta del file analizzato: un template sostituito dopo l'analisi ha lo stesso id ma un altro contenuto
        fingerprint = cache.content_hash(path)
        report = quality.analyze(path, ctx, with_dope=bool(p.get("dope", True)),
                                 with_procheck=bool(p.get("procheck", True)), with_prosa=bool(p.get("prosa", True)))
        return {"source": p.get("source"), "label": label, "source_sha256": fingerprint, **report}

    def handle_qmean(self, ctx: JobContext, p: dict) -> dict:
        """QMEAN locale (UniClust30/UniRef30 sul disco esterno) oppure SWISS-MODEL, secondo le Impostazioni.

        Fallback facoltativo: se il calcolo locale non è possibile o non riesce e l'utente lo ha consentito
        (impostazione o scelta del singolo calcolo), il modello viene inviato a SWISS-MODEL. La risorsa esclusiva del
        servizio locale (`_qmean_resource`) si restituisce appena finisce la parte locale, prima del fallback.
        """
        settings = ctx.settings
        path, label = self.resolve_structure(p.get("source", {}))
        from ..domain import structure as st_mod

        fingerprint = cache.content_hash(path)  # confrontata dal consenso con quella dell'analisi di qualità
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
                try:
                    ctx.progress(0.05, "QMEAN locale")
                    result = qmean_local.score(pdb_text, ctx, method=p.get("method", "auto"),
                                               database=p.get("database") or settings.qmean_uniclust,
                                               timeout_minutes=settings.qmean_timeout_minutes)
                finally:
                    # parte locale finita (riuscita, fallita o annullata): il prossimo QMEAN locale può partire, anche
                    # mentre questo job aspetta SWISS-MODEL (minuti) nel fallback
                    ctx.release_resource()
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
        return {"source": p.get("source"), "label": label, **result, "source_sha256": fingerprint}

    # ------------------------------------------------------------------------------ consenso
    def consensus(self, job_id: str, model: Optional[str] = None, qmean_job: Optional[str] = None) -> dict:
        """Consenso di qualità ricalcolato con i risultati che l'analisi da sola non ha.

        * con `model`: rapporto `<model>.quality.json` di un job model, loop o pipeline;
        * senza `model`: il job è un'analisi di qualità e il rapporto è il suo risultato.

        Se la struttura è un modello salvato da un job, GA341 e zDOPE vengono dai risultati di MODELLER
        (`models.json` accanto al modello). `qmean_job` aggiunge QMEAN (Z-score globale e punteggi locali) di un
        job qmean completato sulla stessa struttura: una sorgente diversa è un errore, non un'esclusione
        silenziosa, perché i punteggi di un'altra struttura renderebbero il consenso fuorviante.
        """
        job = self.jobs.get(job_id)
        if model:
            try:
                report = read_json(self.jobs.file_path(job_id, f"{model}.quality.json"))
            except NotFound:
                report = None
            if not isinstance(report, dict):
                raise NotFound("Rapporto di qualità non disponibile per questo modello.")
            source = {"type": "job", "job_id": job_id, "file": model}
        else:
            if job["kind"] != "quality":
                raise AppError("Per questo job indica il file del modello di cui calcolare il consenso.",
                               code="consensus_model_required")
            if job["status"] != "completed" or not isinstance(job.get("result"), dict):
                raise Conflict("L'analisi di qualità non è completata: il consenso non è ancora disponibile.")
            report = job["result"]
            source = (job.get("params") or {}).get("source")
            source = source if isinstance(source, dict) else {}
        modeller_metrics = self._modeller_metrics(source) if source.get("type") == "job" else None
        qmean = self._qmean_result(qmean_job, source, report.get("source_sha256")) if qmean_job else None
        result = consensus.compute(report, qmean=qmean, modeller=modeller_metrics)
        result["inputs"] = {
            "source": source, "modeller": modeller_metrics,
            "qmean": None if qmean is None else {"job_id": qmean_job, "source": qmean.get("source"),
                                                 "method": qmean.get("method")},
        }
        return result

    def _modeller_metrics(self, source: dict) -> Optional[dict]:
        """GA341 e zDOPE di MODELLER per un modello salvato da un job (None se non è un modello MODELLER)."""
        job_id = str(source.get("job_id") or "")
        rel = PurePosixPath(str(source.get("file") or "").replace("\\", "/"))
        if not job_id or not rel.name:
            return None
        entries: list = []
        try:
            data = read_json(self.jobs.file_path(job_id, (rel.parent / "models.json").as_posix()))
        except AppError:
            data = None
        if isinstance(data, dict):
            entries = list(data.get("models") or []) + list(data.get("loop_models") or [])
        if not entries:  # models.json assente: il risultato del job (model, loop) elenca gli stessi valori
            try:
                result = self.jobs.get(job_id).get("result")
            except AppError:
                result = None
            entries = list(result.get("models") or []) if isinstance(result, dict) else []
        for entry in entries:
            if isinstance(entry, dict) and PurePosixPath(str(entry.get("file") or "")).name == rel.name:
                return {"ga341": entry.get("ga341"), "zdope": entry.get("zdope")}
        return None

    def _qmean_result(self, qmean_job: str, source: dict, fingerprint: Optional[str] = None) -> dict:
        """Risultato di un job QMEAN completato sulla stessa struttura del rapporto.

        Stessa struttura: stessa sorgente (`source_key`) e, se entrambi i job hanno l'impronta del file analizzato
        (`source_sha256`, dalla versione che la registra), lo stesso contenuto. Un template si sostituisce tenendo
        l'id (caricamento di un altro file con lo stesso nome): senza impronte (job precedenti) resta il solo id.
        """
        job = self.jobs.get(qmean_job)
        if job["kind"] != "qmean":
            raise AppError(f"Il job {qmean_job} non è un calcolo QMEAN.", code="qmean_job_invalid")
        if job["status"] != "completed" or not isinstance(job.get("result"), dict):
            raise Conflict("Il calcolo QMEAN non è completato: il consenso non può ancora includerlo.")
        # nel risultato "source" è il servizio (local/swissmodel): la struttura sta nei parametri del job
        q_source = (job.get("params") or {}).get("source")
        if not isinstance(q_source, dict) or source_key(q_source) != source_key(source):
            raise AppError(f"Il calcolo QMEAN riguarda un'altra struttura ({describe_source(q_source)}, non "
                           f"{describe_source(source)}): i suoi punteggi non valgono per questo modello.",
                           code="qmean_source_mismatch")
        q_fingerprint = job["result"].get("source_sha256")
        if fingerprint and q_fingerprint and fingerprint != q_fingerprint:
            raise AppError(f"Il file di {describe_source(source)} è cambiato tra l'analisi di qualità e il calcolo "
                           "QMEAN (stesso nome, contenuto diverso): i punteggi QMEAN non valgono per la struttura "
                           "analizzata. Ripeti l'analisi di qualità sul file attuale.", code="qmean_source_mismatch")
        return job["result"]


def source_key(source: Optional[dict]) -> tuple:
    """Identità di una sorgente di struttura, indipendente dalla forma in cui è scritta (maiuscole, "./")."""
    source = source if isinstance(source, dict) else {}
    kind = source.get("type")
    if kind == "template":
        try:
            return ("template", normalize_id(str(source.get("id") or "")))
        except AppError:
            return ("template", "")
    if kind == "job":
        return ("job", str(source.get("job_id") or ""),
                PurePosixPath(str(source.get("file") or "").replace("\\", "/")).as_posix())
    if kind == "upload":
        return ("upload", PurePosixPath(str(source.get("file") or "")).name)
    return ("?",)


def describe_source(source: Optional[dict]) -> str:
    source = source if isinstance(source, dict) else {}
    if source.get("type") == "template":
        return f"template {source.get('id')}"
    if source.get("type") == "job":
        return f"{source.get('file')} del job {source.get('job_id')}"
    if source.get("type") == "upload":
        return f"file caricato {source.get('file')}"
    return "sorgente sconosciuta"


def load_quality_report(jobs: JobManager, job_id: str, model_file: str) -> Optional[dict]:
    try:
        return read_json(jobs.file_path(job_id, f"{model_file}.quality.json"))
    except AppError:
        return None
