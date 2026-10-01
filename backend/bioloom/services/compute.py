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
from ..domain import (assembly, complexes, consensus, interactions, interfaces, ligands, multitemplate, sequences,
                      structure, template_rank)
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
      (trasferimento attivo e gruppi scelti, None = scelta predefinita) e soglia del ritaglio pLDDT (None = nessun
      ritaglio). Un template sostituito nella libreria durante il job (nuovo download o caricamento con lo stesso id)
      cambia chiave e si prepara di nuovo.
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
        chosen = None if spec.ligands is None else tuple(sorted(tuple(g) for g in spec.ligands))
        return (spec.id, str(source), stat.st_size, stat.st_mtime_ns, tuple(spec.chains), bool(spec.hetatm),
                chosen if spec.hetatm else None, None if spec.plddt_min is None else float(spec.plddt_min),
                bool(spec.keep_order))

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


def _template_specs(raw: list[dict], keep_order: bool = False) -> list[TemplateSpec]:
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
        specs.append(TemplateSpec(id=tid, chains=chains, hetatm=bool(t.get("hetatm", False)), keep_order=keep_order))
    return specs


def complex_templates(target: dict, templates_raw: list[dict]) -> list[dict]:
    """Complesso (F5.3): un solo template (o assemblaggio), con le catene nell'ordine delle catene del bersaglio."""
    if len(templates_raw or []) != 1:
        raise AppError("Nella modalità Complesso scegli un solo template (o un assemblaggio biologico): la disposizione "
                       "delle catene viene da quel template. Per più template usa un PIR a più catene.",
                       code="invalid_complex")
    return [{**templates_raw[0], "chains": [c.template_chain for c in target["chains"]]}]


def _apply_ligand_options(specs: list[TemplateSpec], opts: modeller.ModelOptions) -> None:
    """Gruppi eteroatomici da trasferire per ogni template.

    * `opts.hetatm`: ligandi attivi per tutti i template (oltre a quelli con `hetatm` nel riferimento del template);
    * `opts.ligands` None: scelta predefinita dei template attivi (niente additivi né ioni monovalenti);
    * `opts.ligands` elenco: esattamente quei gruppi. Una voce vale per il template indicato (il template si può
      omettere solo con un template) e attiva il trasferimento per quel template; un template attivo senza voci non
      trasferisce nulla. Una voce per un template che non è tra quelli scelti è un errore.
    """
    known = {normalize_id(s.id) for s in specs}
    for g in opts.ligands or []:
        label = f"{g['resname']} {g['chain']}{g['resnum']}"
        if not g["template"] and len(specs) > 1:
            raise AppError(f"Con più template indica da quale template trasferire il ligando {label}.",
                           code="invalid_ligand")
        if g["template"] and normalize_id(g["template"]) not in known:
            raise AppError(f"Il ligando {label} indica il template {g['template']}, che non è tra quelli scelti.",
                           code="invalid_ligand")
    for s in specs:
        if opts.hetatm:
            s.hetatm = True
        if opts.ligands is None:
            continue
        mine = [(g["chain"], g["resnum"], g["resname"]) for g in opts.ligands
                if not g["template"] or normalize_id(g["template"]) == normalize_id(s.id)]
        if mine:
            s.hetatm = True
        if s.hetatm:
            s.ligands = mine


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
        """Restituisce (percorso, etichetta) per una sorgente {type: template|job|upload}.

        I campi mancanti o nulli (`StructureSource.model_dump()` scrive `"file": None`, le query di
        `/api/analysis/latest` sono facoltative) sono un errore chiaro, mai un errore interno."""
        kind = source.get("type")
        ident = str(source.get("id") or "").strip()
        job_id = str(source.get("job_id") or "").strip()
        file = str(source.get("file") or "")
        if kind == "template":
            if not ident:
                raise AppError("Indica l'identificativo del template da analizzare.", code="invalid_source")
            path = self.library.path(ident)
            return path, path.stem
        if kind == "job":
            if not job_id or not file.strip():
                raise AppError("Indica il job e il file della struttura da analizzare.", code="invalid_source")
            path = self.jobs.file_path(job_id, file)
            return path, path.stem
        if kind == "upload":
            if not file.strip():
                raise AppError("Indica il file caricato da analizzare.", code="invalid_source")
            path = safe_join(self.uploads_dir, file)
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
        # template consigliati: il migliore più quelli complementari, con la motivazione di scelte ed esclusioni
        suggested = template_rank.select_complementary(result["hits"], result.get("query_length") or len(rec.sequence))
        if suggested["templates"]:
            chosen = ", ".join(f"{t['template_id']} ({t['role']})" for t in suggested["templates"])
            ctx.log(f"Template consigliati: {chosen}; copertura complessiva {suggested['coverage']}%")
        return {**result, "query": rec.to_dict(), "best": best["template_id"] if best else None,
                "suggested": suggested}

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
            if info.get("ligand_groups") is not None:
                chosen = ", ".join(f"{g['resname']} {g['chain']}{g['resnum']} ({g['label']})"
                                   for g in info["ligand_groups"])
                left = "; ".join(f"{g['resname']} {g['chain']}{g['resnum']} ({g['reason'] or g['label']})"
                                 for g in info.get("ligands_excluded") or [])
                ctx.log(f"Template {info['code']}: ligandi trasferiti: {chosen or 'nessuno'}"
                        + (f"; non trasferiti: {left}" if left else ""))
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
                       progress: tuple[float, float]) -> tuple[str, list[str]]:
        """Allineamento del bersaglio sui template (`alignment.ali`) e avvisi: con più template, salign che ha
        spostato un template senza poter unire gli align2d (`_choose_multi_alignment`)."""
        notes: list[str] = []
        if target.name in {t["code"] for t in templates}:
            target = SeqRecord(f"{target.name}_target", target.sequence)
        atomic_write_text(workdir / "target.ali", modeller.target_ali(target.name, target.sequence))
        ctx.progress(progress[0], "Allineamento sequenza-struttura")
        modeller.run_script(ctx, modeller.build_alignment_script(templates, target.name), "align.py",
                            workdir=workdir, progress_range=progress)
        if len(templates) > 1:
            choice = self._choose_multi_alignment(ctx, templates, workdir)
            if choice["strategy"] == "salign" and choice.get("displaced"):
                notes.append(choice["message"])
        return (workdir / "alignment.ali").read_text(encoding="utf-8"), notes

    def _run_complex_alignment(self, ctx: JobContext, target: dict, template: dict, workdir: Path,
                               progress: tuple[float, float]) -> tuple[str, str]:
        """Complesso (F5.3): align2d di ogni catena del bersaglio sulla sua catena del template, unite con '/' nelle
        stesse colonne (`domain/complexes.merge_chain_alignments`). Restituisce (PIR, codice del bersaglio)."""
        code = target["name"]
        if code == template["code"]:
            code = f"{code}_target"
        for i, chain in enumerate(target["chains"]):
            atomic_write_text(workdir / modeller.chain_target_file(i), modeller.target_ali(code, chain.sequence))
        ctx.progress(progress[0], "Allineamento per catena")
        modeller.run_script(ctx, modeller.build_complex_alignment_script(template, code), "align.py",
                            workdir=workdir, progress_range=progress)
        texts = []
        for i in range(len(target["chains"])):
            path = workdir / modeller.chain_alignment_file(i)
            if not path.exists():
                raise AppError(f"MODELLER non ha scritto l'allineamento della catena {template['chains'][i]}.",
                               code="modeller_error")
            texts.append(path.read_text(encoding="utf-8"))
        entries = complexes.merge_chain_alignments(texts, template, code)
        for chain, tc, piece_t, piece_s in zip(target["chains"], template["chains"], entries[0].sequence.split("/"),
                                               entries[1].sequence.split("/")):
            pairs, same = multitemplate.identical_pairs(piece_s, piece_t)
            ctx.log(f"Catena {tc} del template ← {chain.name}: {len(chain.sequence)} residui, {pairs} allineati, "
                    f"identità {100.0 * same / pairs if pairs else 0.0:.1f}%")
        pir = sequences.format_pir(entries)
        atomic_write_text(workdir / "alignment.ali", pir)
        return pir, code

    @staticmethod
    def _choose_multi_alignment(ctx: JobContext, templates: list[dict], workdir: Path) -> dict:
        """Più template: salign oppure gli align2d di ogni template uniti sul bersaglio, se salign ha spostato un
        template rispetto al bersaglio o non è riuscito (`domain/multitemplate.choose`). Restituisce la scelta."""
        salign_path = workdir / modeller.SALIGN_FILE
        salign = salign_path.read_text(encoding="utf-8") if salign_path.exists() else None
        pairs = []
        for i in range(len(templates)):
            path = workdir / modeller.pair_file(i)
            if not path.exists():
                raise AppError(f"MODELLER non ha scritto l'allineamento del bersaglio sul template "
                               f"{templates[i]['code']}.", code="modeller_error")
            pairs.append(path.read_text(encoding="utf-8"))
        try:
            choice = multitemplate.choose(salign, pairs)
        except ValueError as exc:
            raise AppError(f"Allineamento sui template non riuscito: {exc}.", code="modeller_error") from exc
        ctx.log(choice["message"])
        if choice["strategy"] == "pairwise":
            (workdir / "alignment.pap").unlink(missing_ok=True)  # vista di salign, non più quella usata
        atomic_write_text(workdir / "alignment.ali", choice["text"])
        return choice

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
                     extended_quality: bool = False, template_cache: Optional[PreparedTemplates] = None,
                     complex_raw: Optional[dict] = None) -> dict:
        """Flusso completo: template → allineamento → AutoModel → selezione → qualità. Usato anche dalla pipeline.

        Con la qualità "all" i modelli si analizzano in parallelo (al massimo `QUALITY_MODELS_PARALLEL`), ognuno con
        il proprio contesto e la propria cartella; i risultati restano nell'ordine dei modelli. `template_cache`
        (pipeline): template preparati riusati fra gli elementi del job.

        `complex_raw` (F5.3, modalità Complesso): bersaglio a più catene con la corrispondenza catena del template →
        sequenza (`domain/complexes.parse_target`), un solo template con le catene in quell'ordine e l'allineamento per
        catena. Ogni bersaglio a più catene (anche da un PIR) ha le catene del modello con i nomi del template, le
        restrizioni di simmetria facoltative e il confronto delle interfacce con il template (`complex` nel risultato e
        nei rapporti di qualità).
        """
        workdir = ctx.workdir
        opts = modeller.parse_options(options_raw or {})
        complex_target = complexes.parse_target(complex_raw) if complex_raw else None
        if complex_target is not None:
            templates_raw = complex_templates(complex_target, templates_raw)
        specs = _template_specs(templates_raw, keep_order=complex_target is not None)
        _apply_ligand_options(specs, opts)
        modeller.modeller_python(ctx)  # errore immediato se MODELLER manca
        ctx.progress(0.02, "Preparazione dei template")
        templates = self._prepare_templates(ctx, specs, workdir,
                                            plddt_min=opts.plddt_min if opts.plddt_trim else None,
                                            cache=template_cache)
        warnings: list[str] = []
        if alignment:
            pir, target_code, warnings = self._normalize_pir(alignment, templates)
            atomic_write_text(workdir / "alignment.ali", pir)
            if complex_target is not None:
                given = complexes.target_chains(next(e for e in sequences.parse_pir(pir) if e.kind == "sequence"))
                wanted = [c.sequence for c in complex_target["chains"]]
                if given != wanted:
                    raise AppError(f"Il PIR ha {len(given)} catene nel bersaglio che non corrispondono alle "
                                   f"{len(wanted)} sequenze del complesso (stesse catene, nello stesso ordine).",
                                   code="invalid_pir")
        elif complex_target is not None:
            pir, target_code = self._run_complex_alignment(ctx, complex_target, templates[0], workdir, (0.05, 0.12))
            warnings = list(sequences.validate_pir(pir)["warnings"])
        else:
            if target is None:
                raise AppError("Indica la sequenza bersaglio oppure un allineamento PIR.")
            pir, alignment_notes = self._run_alignment(ctx, target, templates, workdir, (0.05, 0.12))
            report = sequences.validate_pir(pir)
            warnings = [*report["warnings"], *alignment_notes]
            target_code = next(e.code for e in sequences.parse_pir(pir) if e.kind == "sequence")
        # ligandi: colonne '.' dei gruppi scelti, uguali per l'allineamento automatico e per quello dell'utente
        transfer = any(t.get("ligand_groups") for t in templates)
        entries = sequences.parse_pir(pir)
        if transfer or any("." in e.sequence for e in entries):
            entries, ligand_notes = ligands.pir_with_ligands(entries, templates)
            pir = sequences.format_pir(entries)
            atomic_write_text(workdir / "alignment.ali", pir)
            warnings = [*warnings, *ligand_notes]
        warnings = [*warnings, *self._predicted_notes(templates)]
        # template di strutture diverse senza residui comuni a sufficienza: orientamento relativo arbitrario
        anchor = multitemplate.unanchored(entries, templates) if len(templates) > 1 else None
        if anchor:
            warnings.append(anchor)
        # bersaglio a più catene (F5.3): catene coerenti con il template, nomi del template, simmetria
        multi = self._complex_plan(entries, templates, opts, complex_target)
        warnings = [*warnings, *multi["warnings"]]
        for w in warnings:
            ctx.log(f"Avviso: {w}")
        if multi["chains"]:
            ctx.log("Complesso: " + ", ".join(f"catena {c['model_chain']} ({c['length']} residui"
                                              + (f", catena {c['template_chain']} del template" if c["template_chain"]
                                                 else "") + ")" for c in multi["chains"])
                    + (f"; simmetria sui C-alfa fra {', '.join('='.join(g) for g in multi['symmetry']['groups'])}, "
                       f"peso {opts.symmetry_weight:g}" if multi["symmetry"]["applied"] else ""))
        ctx.progress(0.15, "Costruzione dei modelli")
        script = modeller.build_model_script([t["code"] for t in templates], target_code, opts, hetatm=transfer,
                                             symmetry=multi["groups"], chain_names=multi["names"])
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
        verify = self._ligand_verifier(workdir, entries, templates) if transfer else None
        # gruppi del modello: un '.' del bersaglio ciascuno; lo stesso gruppo di più template (stessa colonna) conta
        # una volta, con il numero del template principale
        model_groups = [m for m in ligands.model_groups(entries, templates) if m] if transfer else []
        transferred = [ligands.group_label(g["chain"], g["resnum"], g["resname"])
                       for g in (members[0][1] for members in model_groups)]
        # complesso: interfacce del modello confrontate con quelle del template (calcolate una volta)
        interfaces_check = self._complex_assessor(ctx, workdir, entries, templates, multi) if multi["chains"] else None

        def assess(m: dict) -> None:
            """Analisi di un modello: rapporto accanto al modello, metriche nel riepilogo, REMARK nel PDB."""
            sub = ctx.sub(m["file"], workdir / f"qa_{Path(m['file']).stem}")
            report = quality.analyze(workdir / m["file"], sub, with_dope=True,
                                     dope_workdir=workdir / f"qa_{Path(m['file']).stem}",
                                     with_procheck=extended_quality, with_prosa=extended_quality)
            # il consenso del modello include GA341 (e zDOPE, se il DOPE non è stato calcolato) di MODELLER
            report["consensus"] = consensus.compute(report, modeller=m)
            if verify is not None:  # ligandi trasferiti: verifica con il confronto dei siti del template
                checked = verify(m["file"], sub.log)
                if checked is not None:
                    report["ligands"] = checked
                    m["ligands"] = ligands.summary(checked)
            if interfaces_check is not None:  # complesso: interfacce e confronto con il template
                assessed = interfaces_check(m["file"], sub.log, sub.check)
                if assessed is not None:
                    report["complex"] = assessed
                    m["complex"] = complexes.brief(assessed)
            atomic_write_json(workdir / f"{m['file']}.quality.json", report)
            m["quality"] = quality.summary_metrics(report)
            quality.inject_remarks(workdir / m["file"], [
                f"MODEL {m['file']} TARGET {target_code}",
                f"TEMPLATES {' '.join(t['code'] for t in templates)}",
                *(f"TEMPLATE {t['code']} ALPHAFOLD "
                  + (f"PLDDT<{t['plddt']['threshold']:g} REMOVED {t['plddt']['removed']}" if t.get("plddt")
                     else "PLDDT_TRIM OFF")
                  for t in templates if t.get("predicted")),
                *([f"LIGANDS {len(transferred)} " + " ".join(g.replace(" ", ":") for g in transferred)]
                  if transferred else []),
                *([f"CHAINS {len(multi['chains'])} " + " ".join(
                    f"{c['model_chain']}={c['template_chain'] or '?'}" for c in multi["chains"])] if multi["chains"]
                  else []),
                *([f"SYMMETRY CA WEIGHT {opts.symmetry_weight:g} "
                   + " ".join(",".join(g) for g in multi["symmetry"]["groups"])] if multi["symmetry"]["applied"]
                  else []),
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
        result = {
            "target": {"code": target_code, "sequence": "".join(c for c in target_seq if c.isalpha())},
            "templates": self._public(templates),
            "plddt_removed": sum((t.get("plddt") or {}).get("removed", 0) for t in templates),
            "alignment": (workdir / "alignment.ali").read_text(encoding="utf-8"),
            "alignment_report": sequences.validate_pir((workdir / "alignment.ali").read_text(encoding="utf-8")),
            "options": asdict(opts),
            "models": models,
            "best_model": best["file"],
            "warnings": warnings,
        }
        if verify is not None:
            # verifica del modello migliore anche senza analisi di qualità (rapida: solo il vicinato degli atomi)
            if best in targets:
                checked = (read_json(workdir / f"{best['file']}.quality.json") or {}).get("ligands")
            else:
                checked = verify(best["file"], ctx.log)
                if checked is not None:
                    best["ligands"] = ligands.summary(checked)
            # gruppi scritti da MODELLER, contati sul file (anche se la verifica non fosse riuscita)
            lines = (workdir / best["file"]).read_text(encoding="utf-8", errors="replace").splitlines()
            written = len({(ln[21], ln[22:27]) for ln in lines
                           if ln.startswith("HETATM") and ln[17:20].strip() not in structure.WATER_NAMES})
            if written < len(transferred):
                text = (f"Il modello migliore contiene {written} dei {len(transferred)} gruppi eteroatomici "
                        f"trasferiti ({', '.join(transferred)}): controlla le colonne '.' dell'allineamento.")
                warnings.append(text)
                ctx.log(f"Avviso: {text}")
            result["ligands"] = {"transferred": [
                {"template": ", ".join(code for code, _g in members),
                 **{k: members[0][1][k] for k in ("resname", "chain", "resnum", "class", "label")},
                 "sources": [{"template": code, "chain": g["chain"], "resnum": g["resnum"], "resname": g["resname"]}
                             for code, g in members]}
                for members in model_groups], "verification": checked}
        if multi["chains"]:
            # interfacce del modello migliore anche senza analisi di qualità (il risultato le mostra nel 3D)
            if best in targets:
                assessed = (read_json(workdir / f"{best['file']}.quality.json") or {}).get("complex")
            else:
                assessed = interfaces_check(best["file"], ctx.log, ctx.check) if interfaces_check else None
                if assessed is not None:
                    best["complex"] = complexes.brief(assessed)
            result["complex"] = {"chains": multi["chains"], "symmetry": multi["symmetry"],
                                 "template": multi["template"], "mode": "complex" if complex_target else "pir",
                                 "assessment": assessed}
        return result

    @staticmethod
    def _complex_plan(entries: list, templates: list[dict], opts: modeller.ModelOptions,
                      complex_target: Optional[dict]) -> dict:
        """Bersaglio a più catene: coerenza delle catene del PIR (`complexes.check_chains`: errore nella modalità
        Complesso, avviso con un PIR), nomi delle catene del modello (quelle del template principale, se la sua voce ha
        una catena per ogni catena del bersaglio; altrimenti A, B, C…), gruppi di simmetria e descrizione per il
        risultato. Un bersaglio a catena singola non cambia nulla (nessuna rinomina: il modello resta catena A)."""
        target = next(e for e in entries if e.kind == "sequence")
        chain_seqs = complexes.target_chains(target)
        empty = {"chains": [], "names": None, "groups": [], "warnings": [], "template": None,
                 "symmetry": {"requested": opts.symmetry, "applied": False, "groups": [], "weight": opts.symmetry_weight}}
        if len(chain_seqs) < 2:
            if opts.symmetry:
                empty["warnings"].append("Restrizioni di simmetria richieste, ma il bersaglio ha una sola catena: "
                                         "nessuna restrizione aggiunta.")
            return empty
        warnings = complexes.check_chains(entries, templates, strict=complex_target is not None)
        principal, span = None, None
        for t in templates:
            entry = next((e for e in entries if e.is_template and e.code == t["code"]), None)
            if entry is None or complexes.break_columns(entry.sequence) != complexes.break_columns(target.sequence):
                continue
            fields = (entry.fields + [""] * 4)[:4]
            chains = list(t["chains"])
            first = fields[1] if fields[1] in chains else chains[0]
            last = fields[3] if fields[3] in chains else chains[-1]
            candidate = chains[chains.index(first):chains.index(last) + 1]
            if len(candidate) == len(chain_seqs):
                principal, span = t, candidate
                break
        names = list(span) if span else list(assembly.CHAIN_IDS[:len(chain_seqs)])
        if principal is None:
            warnings.append("Nessun template ha una catena di fronte a ogni catena del bersaglio con i '/' nelle "
                            "stesse colonne: le catene del modello si chiamano A, B, C… e le interfacce non si "
                            "confrontano con un template.")
        groups = complexes.symmetry_groups(chain_seqs) if opts.symmetry else []
        if opts.symmetry and not groups:
            warnings.append("Restrizioni di simmetria richieste, ma nessuna coppia di catene del bersaglio ha la stessa "
                            "sequenza: nessuna restrizione aggiunta.")
        by_chain = {c.template_chain: c for c in (complex_target or {}).get("chains") or []}
        described = []
        for k, seq in enumerate(chain_seqs):
            tc = span[k] if span else None
            source = by_chain.get(tc) if tc else None
            described.append({"index": k, "model_chain": names[k], "template_chain": tc, "length": len(seq),
                              "name": source.name if source else None,
                              "same_as": source.same_as if source else None})
        return {"chains": described, "names": names, "groups": groups, "warnings": warnings,
                "template": principal["code"] if principal else None,
                "symmetry": {"requested": opts.symmetry, "applied": bool(groups), "weight": opts.symmetry_weight,
                             "groups": [[names[i] for i in g] for g in groups], "atoms": "CA dei residui standard"}}

    @staticmethod
    def _complex_assessor(ctx: JobContext, workdir: Path, entries: list, templates: list[dict],
                          multi: dict) -> Optional[Callable]:
        """Interfacce di un modello a più catene e confronto con il template principale (`complexes.assess`).

        La copia preparata del template e le sue interfacce si calcolano una volta (qui, prima dei modelli analizzati
        in parallelo); ogni chiamata legge il modello. Senza template confrontabile si analizzano solo le interfacce
        del modello. Un errore inatteso diventa una riga del log: non fa fallire la modellazione."""
        template = next((t for t in templates if t["code"] == multi["template"]), None)
        template_model = template_ifaces = None
        if template is not None:
            try:
                template_model = structure.first_model(structure.load_structure(workdir / template["file"],
                                                                                template["code"]))
                template_ifaces = interfaces.analyze(template_model, interactions.analyze(template_model),
                                                     check=ctx.check)
            except (AppError, ValueError, KeyError) as exc:
                ctx.log(f"Interfacce del template {template['code']} non calcolate: {exc}")
                template = None
            else:
                ctx.log(f"Interfacce del template {template['code']}: "
                        + (", ".join(f"{'–'.join(p['chains'])} {p['area']:.0f} Å²" for p in template_ifaces["pairs"])
                           or "nessuna"))

        def check(model_file: str, log: Callable[[str], None], cancel: Callable[[], None]) -> Optional[dict]:
            try:
                model = structure.first_model(structure.load_structure(workdir / model_file, Path(model_file).stem))
                if template is None:
                    iface = interfaces.analyze(model, interactions.analyze(model), check=cancel)
                    out = {"version": complexes.VERSION, "template": None, "chains": [],
                           "interfaces": {k: v for k, v in iface.items() if k != "residues"},
                           "template_interfaces": None, "comparison": [], "status": "non valutabile", "tone": "warn",
                           "summary": "Nessun template confrontabile: solo le interfacce del modello.", "notes": [],
                           "limits": complexes.LIMITS}
                else:
                    out = complexes.assess(model, template_model, entries, template, template_ifaces=template_ifaces,
                                           check=cancel)
            except (AppError, ValueError, KeyError, IndexError) as exc:
                log(f"Analisi delle interfacce di {model_file} non riuscita: {exc}")
                return None
            pairs = out["interfaces"].get("pairs") or []
            log(f"Interfacce di {model_file}: "
                + (", ".join(f"{'–'.join(p['chains'])} {p['area']:.0f} Å²" for p in pairs) or "nessuna")
                + (f"; confronto con {out['template']}: {out['status']}" if out.get("template") else ""))
            return out

        return check

    @staticmethod
    def _ligand_verifier(workdir: Path, entries: list, templates: list[dict]) -> Callable:
        """Verifica dei ligandi di un modello (domain/ligands.evaluate) con il sito del template di ogni gruppo.

        Le copie preparate dei template si leggono una volta; ogni chiamata legge il modello. Un errore inatteso
        della verifica diventa una riga del log e un rapporto senza ligandi: non fa fallire la modellazione."""
        template_models = {}
        for t in templates:
            if t.get("ligand_groups"):
                template_models[t["code"]] = structure.first_model(structure.load_structure(workdir / t["file"],
                                                                                            t["code"]))

        def verify(model_file: str, log: Callable[[str], None]) -> Optional[dict]:
            try:
                model = structure.first_model(structure.load_structure(workdir / model_file, Path(model_file).stem))
                contexts, notes = ligands.template_contexts(entries, templates, template_models, model)
                checked = ligands.evaluate(model, contexts=contexts, notes=notes)
            except (AppError, ValueError, KeyError, IndexError) as exc:
                log(f"Verifica dei ligandi di {model_file} non riuscita: {exc}")
                return None
            if checked is not None:
                counts = ", ".join(f"{n} {s}" for s, n in checked["counts"].items() if n)
                log(f"Ligandi di {model_file}: {counts}")
            return checked

        return verify

    def handle_align2d(self, ctx: JobContext, p: dict) -> dict:
        if p.get("complex"):  # complesso (F5.3): un allineamento per catena, unito con '/'
            target_complex = complexes.parse_target(p["complex"])
            modeller.modeller_python(ctx)
            templates = self._prepare_templates(
                ctx, _template_specs(complex_templates(target_complex, p.get("templates", [])), keep_order=True),
                ctx.workdir)
            pir, _code = self._run_complex_alignment(ctx, target_complex, templates[0], ctx.workdir, (0.1, 0.9))
            return {"pir": pir, "report": sequences.validate_pir(pir), "templates": self._public(templates),
                    "warnings": [], "pap": "",
                    "chains": [{"template_chain": c.template_chain, "name": c.name, "length": len(c.sequence),
                                "same_as": c.same_as} for c in target_complex["chains"]]}
        target = _record(p.get("target") or {})
        modeller.modeller_python(ctx)
        templates = self._prepare_templates(ctx, _template_specs(p.get("templates", [])), ctx.workdir)
        pir, warnings = self._run_alignment(ctx, target, templates, ctx.workdir, (0.1, 0.9))
        if len(templates) > 1:
            anchor = multitemplate.unanchored(sequences.parse_pir(pir), templates)
            warnings = [*warnings, *([anchor] if anchor else [])]
        for w in warnings:
            ctx.log(f"Avviso: {w}")
        # Template AlphaFold interi: in modellazione, con il ritaglio pLDDT, i residui tolti diventano gap del PIR
        return {"pir": pir, "report": sequences.validate_pir(pir), "templates": self._public(templates),
                "warnings": warnings,
                "pap": (ctx.workdir / "alignment.pap").read_text(encoding="utf-8", errors="replace")
                if (ctx.workdir / "alignment.pap").exists() else ""}

    def handle_model(self, ctx: JobContext, p: dict) -> dict:
        target = _record(p["target"]) if p.get("target") and p["target"].get("sequence") else None
        return self.run_modeling(ctx, target=target, templates_raw=p.get("templates", []),
                                 alignment=p.get("alignment") or None, options_raw=p.get("options", {}),
                                 quality_mode=p.get("quality", "best"),
                                 extended_quality=bool(p.get("extended_quality", False)),
                                 complex_raw=p.get("complex") or None)

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
