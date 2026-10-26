"""Report HTML dei modelli (domain/report.py): raccolta dei dati di un job model, loop, pipeline, refine o quality.

Il report si genera al volo dai file già salvati del job, senza ricalcoli: rapporto di qualità del modello
(`<modello>.quality.json`), risultati di MODELLER (`models.json` o risultato del job), allineamento, parametri e
template (metadati della libreria). Il consenso si ricompone con GA341/zDOPE del modello; con un job QMEAN passa dalla
rotta del consenso (`ComputeService.consensus`), che controlla che QMEAN riguardi la stessa struttura.

* **model / loop:** il modello deve essere uno di quelli del risultato del job (non fallito).
* **pipeline:** il file è `items/<idx>_<nome>/<modello>` di un elemento completato; target, template scelto dalla
  ricerca e metriche vengono dalla riga dell'elemento, allineamento e modelli dalla sua cartella. Lo ZIP della
  pipeline usa `pipeline_item` per il modello migliore di ogni elemento, un report alla volta.
* **quality:** report ridotto della struttura analizzata (niente template, allineamento e parametri).
* **refine (F9.1):** il file è il modello rifinito del job (`refined_file`); al posto di template, allineamento e
  parametri di MODELLER, la rifinitura: protocollo e parametri di OpenMM, atomi aggiunti da PDBFixer, energia prima e
  dopo con le componenti, RMSD della catena principale e confronto prima/dopo delle metriche di qualità (dal risultato
  del job); la qualità è il rapporto del modello rifinito.
* **mutate / alascan (F9.2):** il file è il mutante o il selvatico riottimizzato (`files` del risultato), con la sezione
  "Mutagenesi in silico" (classe, motivazioni, DOPE ed energia, confronto, protocollo) e la qualità del modello; la
  scansione di alanina ha il report senza modello (`/report`), con la tabella e gli hot spot.
* **analisi strutturale (F7, F7b):** in ogni report, l'analisi `analysis` (interazioni, interfacce, conservazione), le
  tasche `pockets` e l'elettrostatica `electrostatics` completate più recenti sulla stessa struttura (stessa sorgente e
  stessa impronta del file), se ci sono.
* **confronto con una predizione (F9.3):** il job `predict` indicato (`predict_job`, con i controlli della rotta del
  consenso) oppure, senza, il confronto completato più recente sulla stessa struttura: sezione con l'esito e consenso
  con l'accordo con la predizione. Senza confronto il report non cambia.
"""

from __future__ import annotations

import re
from collections import OrderedDict
from dataclasses import asdict, dataclass, field
from pathlib import Path, PurePosixPath
from typing import Optional

from .. import __version__
from ..core import cache
from ..core.db import Database, now_iso
from ..core.errors import AppError, Conflict, NotFound
from ..core.jobs import JobManager
from ..core.storage import read_json
from ..domain import consensus, report, sequences, structure
from ..engines import modeller
from .analysis import AnalysisService, report_summary
from .compute import ComputeService, describe_source, load_quality_report
from .structure_tools import ELECTROSTATICS, POCKETS, StructureToolsService, electrostatics_summary, pockets_summary

MODEL_KINDS = ("model", "loop", "pipeline", "refine")
# modello di un elemento di pipeline: items/<idx>_<nome>/<file>
ITEM_MODEL = re.compile(r"^items/(\d+)_([^/]+)/([^/]+)$")
HEAD_BYTES = 20_000  # intestazione dei PDB: EXPDTA di MODELLER e righe REMARK 999 BIOLOOM
ITEM_COLUMNS = ("idx, name, sequence, status, template, template_source, identity, coverage, evalue, twilight, "
                "best_model, grade, grade_score, metrics")
SOURCE_LABEL = {"alphafold": "AlphaFold DB (EBI)", "RCSB PDB": "RCSB PDB", "caricamento utente": "file caricato"}
ENGINE_LABEL = {"rcsb": "RCSB", "ncbi": "NCBI BLAST", "local": "BLAST+ locale", "mmseqs": "MMseqs2 locale",
                "alphafold": "AlphaFold DB"}
USER_TEMPLATE = "scelto dall'utente"
_ASK = object()  # `_base`: nome del progetto da leggere nel database


def _rel(model: str) -> str:
    return PurePosixPath(str(model or "").replace("\\", "/")).as_posix()


# estensione di un file di struttura, anche compresso
_STRUCTURE_SUFFIX = re.compile(r"\.(?:pdb|ent|cif|mmcif)(?:\.gz)?$", re.IGNORECASE)


def _label_stem(label: object) -> str:
    """Nome del file del report dall'etichetta della struttura analizzata: si toglie solo l'estensione di un file di
    struttura. L'etichetta di un job di qualità o di una scansione di alanina è già senza (`resolve_structure`:
    "ubi.B99990001" per ubi.B99990001.pdb), e `PurePosixPath.stem` toglierebbe ".B99990001", dando lo stesso nome ai
    report di modelli diversi dello stesso job."""
    return _STRUCTURE_SUFFIX.sub("", PurePosixPath(str(label).replace("\\", "/")).name)


def _head(path: Optional[Path]) -> str:
    if path is None:
        return ""
    try:
        return structure.read_text_head(path, HEAD_BYTES)
    except OSError:
        return ""


@dataclass(frozen=True)
class ItemContext:
    """Dati dal database per il report di un elemento della pipeline, letti una volta per tutto lo ZIP: con questi il
    report si genera solo dai file del job (`pipeline_item_detached`), anche in un altro processo.

    `qmean`: {"job_id", "result"} del QMEAN completato più recente sul modello, se c'è (impronta verificata al
    momento del report); `tools`: riepiloghi di analisi strutturale, tasche ed elettrostatica (`analysis`, `pockets`,
    `electrostatics`, None se mancano)."""

    project: Optional[str] = None
    qmean: Optional[dict] = None
    tools: dict = field(default_factory=lambda: {"analysis": None, "pockets": None, "electrostatics": None})


def _quality_with(job_id: str, rel: str, path: Path, quality: Optional[dict], metrics: dict,
                  found: Optional[dict]) -> tuple[Optional[dict], Optional[dict]]:
    """(consenso, informazioni su QMEAN) come `ReportService._quality` con il QMEAN `found` già letto: lo include solo
    se riguarda il file attuale (impronta del QMEAN uguale a quella del file e del rapporto di qualità), come
    `_latest_qmean`; altrimenti il consenso è quello senza QMEAN."""
    if quality is None:
        return None, None
    result = (found or {}).get("result")
    if isinstance(result, dict):
        theirs = result.get("source_sha256")
        same = not theirs or ((not quality.get("source_sha256") or quality["source_sha256"] == theirs)
                              and _content_hash(path) == theirs)
        if same:
            cons = consensus.compute(quality, qmean=result, modeller=metrics)
            cons["inputs"] = {"source": {"type": "job", "job_id": job_id, "file": rel}, "modeller": metrics,
                              "qmean": {"job_id": found.get("job_id"), "source": result.get("source"),
                                        "method": result.get("method")}}
            return cons, {"version": result.get("qmean_version"), "method": result.get("method"),
                          "source": result.get("source")}
    return consensus.compute(quality, modeller=metrics), None


def _content_hash(path: Path) -> Optional[str]:
    try:
        return cache.content_hash(path)
    except OSError:
        return None


def _strip_gaps(seq: str) -> str:
    """Sequenza di una voce PIR senza gap né ligandi ('.'); '/' separa le catene."""
    return "".join(c for c in seq if c.isalpha() or c == "/")


class ReportService:
    def __init__(self, jobs: JobManager, compute: ComputeService, db: Database) -> None:
        self.jobs = jobs
        self.compute = compute
        self.db = db
        # descrizioni dei template già lette (`_library`): lo ZIP di una pipeline le prepara tutte prima (`ItemContext`)
        self.library_cache: dict[str, dict] = {}

    # ------------------------------------------------------------------------------ ingressi
    def build(self, job_id: str, model: Optional[str] = None, *, qmean_job: Optional[str] = None,
              image_png: Optional[bytes] = None, predict_job: Optional[str] = None) -> tuple[str, str]:
        """(HTML del report, nome del file da scaricare). `model` per i job model, loop e pipeline; nessun modello
        per un'analisi di qualità. `image_png`: byte già verificati da `report.decode_png`. `predict_job`: confronto
        con una predizione da includere (senza: il più recente sulla stessa struttura, se c'è). Lo stesso per
        `qmean_job`, che comprende i QMEAN automatici di modellazione e pipeline."""
        job = self.jobs.get(job_id)
        kind = job["kind"]
        if not qmean_job and (model or kind == "quality"):
            qmean_job = self._latest_qmean(job, _rel(model) if model else None)
        if not model and kind == "alascan":  # scansione di alanina (F9.2): tabella della struttura scandita
            data, stem = self._alascan_data(job)
        elif not model:
            if kind != "quality":
                raise AppError("Indica il file del modello di cui generare il report.", code="report_model_required")
            data, stem = self._quality_data(job, qmean_job)
        elif kind == "mutate":  # mutagenesi (F9.2): mutante o selvatico riottimizzato
            data, stem = self._mutate_data(job, _rel(model), qmean_job)
        elif kind in ("model", "loop"):
            data, stem = self._model_data(job, _rel(model), qmean_job)
        elif kind == "pipeline":
            data, stem = self._pipeline_data(job, _rel(model), qmean_job)
        elif kind == "refine":
            data, stem = self._refine_data(job, _rel(model), qmean_job)
        elif kind == "quality":
            raise AppError("Il report di un'analisi di qualità riguarda la struttura analizzata: non indicare un file.",
                           code="report_unsupported")
        else:
            raise AppError(f"Il report è disponibile per i modelli dei job di modellazione, loop, pipeline, "
                           f"rifinitura e mutagenesi, per le analisi di qualità e le scansioni di alanina, non per i job "
                           f"di tipo '{kind}'.", code="report_unsupported")
        data["image_png"] = image_png
        self._structure_tools(data, job, _rel(model) if model else None)
        found = self._prediction(job, _rel(model) if model else None, predict_job)
        if found is not None:  # F9.3: sezione del confronto e consenso con l'accordo con la predizione
            data["prediction"] = found
            if data.get("quality") is not None:
                data["consensus"] = self.compute.consensus(job_id, _rel(model) if model else None, qmean_job,
                                                           found["job_id"])
        return report.render(data), report.safe_filename(stem)

    def pipeline_item(self, job: dict, row: dict, *, qmean: bool = False) -> str:
        """Report del modello migliore di un elemento completato (ZIP della pipeline: senza immagine). `qmean`: il
        modello ha un QMEAN completato, da includere come nel report della pagina (il più recente valido); senza, non
        si cerca (migliaia di elementi senza QMEAN)."""
        rel = _rel(row.get("best_model") or "")
        qmean_job = self._latest_qmean(job, rel) if qmean else None
        data, _stem = self._pipeline_data(job, rel, qmean_job, row=row)
        self._structure_tools(data, job, rel)
        return report.render(data)

    def pipeline_item_detached(self, job: dict, row: dict, context: ItemContext) -> tuple[str, dict]:
        """Lo stesso report di `pipeline_item`, con i dati del database già letti (`context`): servono solo i file
        del job, quindi si può generare in un altro processo (ZIP della pipeline in parallelo). Restituisce anche
        `quality` (il rapporto di qualità letto, None senza) e `qmean` (vero se il report include il QMEAN)."""
        rel = _rel(row.get("best_model") or "")
        data, _stem = self._pipeline_data(job, rel, None, row=row, context=context)
        data.update(context.tools)
        included = bool(((data.get("consensus") or {}).get("inputs") or {}).get("qmean"))
        return report.render(data), {"quality": data.get("quality"), "qmean": included}

    def _fingerprint(self, source: dict) -> Optional[str]:
        try:
            path, _label = self.compute.resolve_structure(source)
            return cache.content_hash(path)
        except (AppError, OSError):
            return None

    def _structure_tools(self, data: dict, job: dict, model: Optional[str]) -> None:
        """Analisi strutturale (F7: interazioni, interfacce, conservazione), tasche (fpocket) ed elettrostatica (APBS)
        più recenti sulla stessa struttura, se ci sono: stessa sorgente (il modello del job, o la struttura
        dell'analisi di qualità) e stessa impronta del file. Ciascuna è facoltativa: senza, la sezione non c'è."""
        data["analysis"] = data["pockets"] = data["electrostatics"] = None
        if model:
            source: object = {"type": "job", "job_id": job["id"], "file": model}
        else:
            source = (job.get("params") or {}).get("source")
        if not isinstance(source, dict):
            return
        fingerprint = self._fingerprint(source)
        found = AnalysisService(self.jobs, self.compute).latest(source, fingerprint)
        data["analysis"] = report_summary(found) if found else None
        tools = StructureToolsService(self.jobs, self.compute)
        found = tools.latest(POCKETS, source, fingerprint)
        data["pockets"] = pockets_summary(found) if found else None
        found = tools.latest(ELECTROSTATICS, source, fingerprint)
        data["electrostatics"] = electrostatics_summary(found) if found else None

    def _prediction(self, job: dict, model: Optional[str], predict_job: Optional[str]) -> Optional[dict]:
        """Confronto con una predizione (F9.3) della stessa struttura: quello indicato, con gli stessi controlli della
        rotta del consenso (errore se è di un'altra struttura), oppure il più recente; uno che non vale più (file
        cambiato dopo l'analisi di qualità) si tralascia invece di far fallire il report."""
        from .predict import PredictService, prediction_summary  # services/predict importa services/compute

        source = ({"type": "job", "job_id": job["id"], "file": model} if model
                  else (job.get("params") or {}).get("source"))
        if not isinstance(source, dict):
            return None
        service = PredictService(self.jobs, self.compute)
        if predict_job:
            service.plausibility_for(predict_job, source)
            return prediction_summary(self.jobs.get(predict_job))
        try:
            path, _label = self.compute.resolve_structure(source)
            fingerprint: Optional[str] = cache.content_hash(path)
        except (AppError, OSError):
            fingerprint = None
        found = service.latest(source, fingerprint)
        if found is None:
            return None
        try:  # anche il consenso deve poterlo includere (impronta del rapporto di qualità)
            quality = job.get("result") if job["kind"] == "quality" else None
            service.plausibility_for(found["id"], source, (quality or {}).get("source_sha256"))
        except AppError:
            return None
        return prediction_summary(found)

    # ------------------------------------------------------------------------------ parti comuni
    def _base(self, job: dict, kind: str, project: object = _ASK) -> dict:
        """Intestazione comune; `project` è il nome del progetto se già noto (ZIP: letto una volta), altrimenti
        `_ASK` lo legge dal database."""
        if project is _ASK:
            project = None
            if job.get("project_id"):
                project = self.db.scalar("SELECT name FROM projects WHERE id=?", (job["project_id"],))
        return {"kind": kind, "title": job.get("title") or "", "job_id": job["id"], "project": project,
                "created_at": job.get("created_at"), "finished_at": job.get("finished_at"),
                "generated_at": now_iso(), "warnings": []}

    @staticmethod
    def _completed(job: dict) -> dict:
        if job["status"] != "completed" or not isinstance(job.get("result"), dict):
            raise Conflict("Il job non è completato: il report non è ancora disponibile.")
        return job["result"]

    def _quality(self, job_id: str, model: str, quality: Optional[dict], metrics: Optional[dict],
                 qmean_job: Optional[str]) -> tuple[Optional[dict], Optional[dict]]:
        """(consenso, informazioni su QMEAN) del rapporto di qualità di un modello."""
        if qmean_job:
            if quality is None:
                raise NotFound("Rapporto di qualità non disponibile per questo modello: il report non può includere "
                               "QMEAN.")
            # stessi controlli della rotta del consenso: QMEAN completato e sulla stessa struttura
            return self.compute.consensus(job_id, model, qmean_job), self._qmean_info(qmean_job)
        if quality is None:
            return None, None
        return consensus.compute(quality, modeller=metrics), None

    def _latest_qmean(self, job: dict, model: Optional[str]) -> Optional[str]:
        """QMEAN completato più recente sulla struttura del report, se il consenso può includerlo (rapporto di qualità
        presente, stessa struttura, stesso contenuto); altrimenti None, senza far fallire il report."""
        source = ({"type": "job", "job_id": job["id"], "file": model} if model
                  else (job.get("params") or {}).get("source"))
        if not isinstance(source, dict):
            return None
        try:
            if model:
                quality = read_json(self.jobs.file_path(job["id"], f"{model}.quality.json"))
            else:
                quality = job.get("result") if job["status"] == "completed" else None
            if not isinstance(quality, dict):
                return None
            path, _label = self.compute.resolve_structure(source)
            found = StructureToolsService(self.jobs, self.compute).latest("qmean", source, cache.content_hash(path))
            if found is None:
                return None
            self.compute._qmean_result(found["id"], source, quality.get("source_sha256"))
        except (AppError, OSError):
            return None
        return found["id"]

    def _qmean_info(self, qmean_job: str) -> Optional[dict]:
        result = self.jobs.get(qmean_job).get("result")
        if not isinstance(result, dict):
            return None
        return {"version": result.get("qmean_version"), "method": result.get("method"), "source": result.get("source")}

    @staticmethod
    def _tools(head: str, quality: Optional[dict], qmean: Optional[dict]) -> list[tuple[str, str]]:
        """BioLoom e gli strumenti usati, con la versione quando è nota (MODELLER dall'EXPDTA del modello)."""
        tools = [("BioLoom", __version__)]
        version = report.modeller_version(head)
        if version:
            tools.append(("MODELLER", version))
        elif quality and quality.get("dope"):
            tools.append(("MODELLER", "(DOPE)"))
        if quality:
            method = (quality.get("secondary_structure") or {}).get("method")
            if method:  # "mkdssp" oppure "DSSP integrato BioLoom (Kabsch-Sander)"
                tools.append((str(method), ""))
            if quality.get("procheck"):
                tools.append(("PROCHECK", ""))
            if quality.get("prosa"):
                tools.append(("ProSA II", "2003"))
        if qmean:
            where = "locale" if qmean.get("source") == "local" else "SWISS-MODEL"
            tools.append(("QMEAN", " ".join(str(x) for x in (qmean.get("version"), qmean.get("method")) if x)
                          + f" ({where})"))
        return tools

    @staticmethod
    def _models(entries: list, chosen: str, best: Optional[str] = None,
                grades: Optional[dict] = None) -> list[dict]:
        out = []
        for m in entries:
            if not isinstance(m, dict) or not m.get("file"):
                continue
            name = str(m["file"])
            q = m.get("quality") if isinstance(m.get("quality"), dict) else {}
            grade, score = (q.get("grade"), q.get("grade_score")) if q else (grades or {}).get(name, (None, None))
            out.append({"file": name, "kind": m.get("kind"), "dope": m.get("dope"), "zdope": m.get("zdope"),
                        "ga341": m.get("ga341"), "molpdf": m.get("molpdf"), "grade": grade, "grade_score": score,
                        "best": bool(m.get("best")) or name == best, "chosen": name == chosen,
                        "failure": m.get("failure")})
        return out

    def _library(self, template_id: Optional[str]) -> dict:
        if not template_id:
            return {}
        if template_id in self.library_cache:
            return self.library_cache[template_id]
        if self.compute is None:  # report generato fuori dal processo di BioLoom (ZIP): solo i template preparati
            return {}
        try:
            meta = self.compute.library.describe(template_id)
        except (AppError, OSError):
            meta = {}
        self.library_cache[template_id] = meta
        return meta

    @staticmethod
    def _source(meta: dict) -> str:
        if not meta:
            return "non più nella libreria"
        source = str(meta.get("source") or "sconosciuta")
        return SOURCE_LABEL.get(source, source)

    @staticmethod
    def _structure_sequence(path: Path, quality: Optional[dict]) -> tuple[str, list[str]]:
        """Sequenza per catena della struttura (dai residui del rapporto, altrimenti dal file)."""
        chains: "OrderedDict[str, list[str]]" = OrderedDict()
        rows = (quality or {}).get("residues") or []
        if rows:
            for r in rows:
                if isinstance(r, dict):
                    chains.setdefault(str(r.get("chain") or ""), []).append(str(r.get("aa") or "X")[:1])
        else:
            try:
                for c in structure.chain_summaries(structure.first_model(structure.load_structure(path, path.stem))):
                    chains[c["chain"]] = list(c["sequence"])
            except AppError:
                pass
        return "/".join("".join(v) for v in chains.values()), list(chains)

    # ------------------------------------------------------------------------------ job model e loop
    def _model_data(self, job: dict, rel: str, qmean_job: Optional[str]) -> tuple[dict, str]:
        res = self._completed(job)
        entries = [m for m in res.get("models") or [] if isinstance(m, dict)]
        entry = next((m for m in entries if m.get("file") == rel), None)
        if entry is None or entry.get("failure"):
            raise NotFound(f"Il modello {rel} non è tra i modelli riusciti del job {job['id']}.")
        path = self.jobs.file_path(job["id"], rel)
        quality = load_quality_report(self.jobs, job["id"], rel)
        metrics = {"ga341": entry.get("ga341"), "zdope": entry.get("zdope")}
        cons, qmean = self._quality(job["id"], rel, quality, metrics, qmean_job)
        head = _head(path)
        data = self._base(job, job["kind"])
        data.update({"model": rel, "quality": quality, "consensus": cons, "tools": self._tools(head, quality, qmean),
                     "models": self._models(entries, rel, res.get("best_model"))})
        if job["kind"] == "model":
            alignment = res.get("alignment") or ""
            pir_target = next((e.sequence for e in sequences.parse_pir(alignment) if e.kind == "sequence"), "")
            target = res.get("target") if isinstance(res.get("target"), dict) else {}
            data.update({
                "target": {"name": target.get("code"), "sequence": _strip_gaps(pir_target) or target.get("sequence")},
                "templates": self._prepared_templates(res.get("templates") or [], alignment),
                "alignment": alignment, "options": res.get("options") if isinstance(res.get("options"), dict) else None,
                "warnings": [str(w) for w in res.get("warnings") or []],
            })
            # ligandi trasferiti: la verifica del modello migliore c'è anche senza rapporto di qualità
            lig = res.get("ligands") if isinstance(res.get("ligands"), dict) else {}
            if rel == res.get("best_model") and isinstance(lig.get("verification"), dict):
                data["ligands"] = lig["verification"]
            # complesso (F5.3): interfacce del rapporto del modello; per il migliore anche senza analisi di qualità
            cx = res.get("complex") if isinstance(res.get("complex"), dict) else None
            if cx is not None:
                assessed = (quality or {}).get("complex")
                if not isinstance(assessed, dict) and rel == res.get("best_model"):
                    assessed = cx.get("assessment")
                data["complex"] = {**cx, "assessment": assessed if isinstance(assessed, dict) else None}
        else:  # loop: la struttura di partenza è il bersaglio, niente template né allineamento
            params = job.get("params") or {}
            region = res.get("region") if isinstance(res.get("region"), list) else []
            start, end = (list(region) + [None, None])[:2]
            seq, chains = self._structure_sequence(path, quality)
            data.update({
                "target": {"name": res.get("source") or describe_source(params.get("source")), "sequence": seq,
                           "chains": chains},
                "loop": {"source": describe_source(params.get("source")), "start": start, "end": end,
                         "models": params.get("models"), "level": params.get("level")},
            })
        return data, f"report_{PurePosixPath(rel).stem}"

    def _prepared_templates(self, prepared: list, alignment: str) -> list[dict]:
        """Template preparati dal job (risultato di run_modeling) con identità e copertura sull'allineamento."""
        entries = sequences.parse_pir(alignment)
        target = next((e.sequence for e in entries if e.kind == "sequence"), "")
        out = []
        for t in prepared:
            if not isinstance(t, dict):
                continue
            pir = next((e for e in entries if e.is_template and e.code == t.get("code")), None)
            identity, coverage = report.alignment_identity(target, pir.sequence) if pir and target else (None, None)
            meta = self._library(t.get("id"))
            predicted = bool(t.get("predicted"))
            out.append({"id": t.get("id"), "code": t.get("code"), "chains": t.get("chains") or [],
                        "first": t.get("first"), "last": t.get("last"), "source": self._source(meta),
                        "title": meta.get("title"), "method": meta.get("method"), "resolution": meta.get("resolution"),
                        "identity": identity, "coverage": coverage, "predicted": predicted,
                        "predictor": (t.get("predictor") or meta.get("predictor")) if predicted else None,
                        "plddt_mean": meta.get("plddt_mean") if predicted else None,
                        "trim": t.get("plddt") if isinstance(t.get("plddt"), dict) else None,
                        "trim_off": predicted and not t.get("plddt")})
        return out

    # ------------------------------------------------------------------------------ pipeline
    def _pipeline_data(self, job: dict, rel: str, qmean_job: Optional[str],
                       row: Optional[dict] = None, context: Optional[ItemContext] = None) -> tuple[dict, str]:
        match = ITEM_MODEL.match(rel)
        if not match:
            raise NotFound(f"{rel} non è il modello di un elemento della pipeline.")
        idx = int(match.group(1))
        if row is None:
            row = self.db.query_one(f"SELECT {ITEM_COLUMNS} FROM pipeline_items WHERE job_id=? AND idx=?",
                                    (job["id"], idx))
        if row is None or f"{match.group(1)}_{match.group(2)}" != f"{int(row['idx']):04d}_{row['name']}":
            raise NotFound(f"{rel} non è il modello di un elemento della pipeline.")
        if row["status"] != "completed":
            raise Conflict("L'elemento della pipeline non è completato: il report non è ancora disponibile.")
        folder, name = f"items/{match.group(1)}_{match.group(2)}", match.group(3)
        summary = self._item_json(job["id"], f"{folder}/models.json") or {}
        entries = [{**m, "kind": "model"} for m in summary.get("models") or [] if isinstance(m, dict)]
        entries += [{**m, "kind": "loop"} for m in summary.get("loop_models") or [] if isinstance(m, dict)]
        entry = next((m for m in entries if m.get("file") == name and not m.get("failure")), None)
        if entry is None:
            raise NotFound(f"Il modello {name} non è tra i modelli riusciti dell'elemento {row['name']}.")
        path = self.jobs.file_path(job["id"], rel)
        quality = load_quality_report(self.jobs, job["id"], rel)
        modeller_metrics = {"ga341": entry.get("ga341"), "zdope": entry.get("zdope")}
        if context is None:
            cons, qmean = self._quality(job["id"], rel, quality, modeller_metrics, qmean_job)
        else:
            cons, qmean = _quality_with(job["id"], rel, path, quality, modeller_metrics, context.qmean)
        head = _head(path)
        alignment = self._item_text(job["id"], f"{folder}/alignment.ali")
        params = job.get("params") or {}
        metrics = row.get("metrics") if isinstance(row.get("metrics"), dict) else {}
        best = PurePosixPath(str(row.get("best_model") or "")).name
        data = self._base(job, "pipeline", project=context.project if context else _ASK)
        # pipeline M × N: l'elemento è la coppia <sequenza>__<template>
        sequence, _sep, pair_template = str(row["name"]).partition("__") if params.get("fixed_combination") == "each" \
            and params.get("template_mode") == "fixed" else (row["name"], "", "")
        subject = f"{sequence} × {pair_template}" if pair_template else row["name"]
        data.update({
            "title": f"{job.get('title') or 'Pipeline'} · {subject}", "model": rel, "quality": quality,
            "consensus": cons, "tools": self._tools(head, quality, qmean),
            "target": {"name": sequence, "sequence": row.get("sequence") or ""},
            "templates": self._item_templates(job["id"], folder, alignment, row, metrics, head),
            "search": self._search(row), "alignment": alignment, "options": self._options(params),
            "pipeline": {k: params.get(k) for k in ("template_mode", "engine", "evalue", "twilight", "max_templates",
                                                    "fixed_combination")},
            "models": self._models(entries, name, best, {best: (row.get("grade"), row.get("grade_score"))}),
            "warnings": self._item_warnings(alignment, row),
        })
        # nella cartella dell'elemento i modelli hanno solo il nome del file: la tabella usa quello
        return data, f"report_{PurePosixPath(rel).stem}"

    def _item_json(self, job_id: str, rel: str) -> Optional[dict]:
        try:
            data = read_json(self.jobs.file_path(job_id, rel))
        except AppError:
            return None
        return data if isinstance(data, dict) else None

    def _item_text(self, job_id: str, rel: str) -> str:
        try:
            return self.jobs.file_path(job_id, rel).read_text(encoding="utf-8", errors="replace")
        except (AppError, OSError):
            return ""

    @staticmethod
    def _options(params: dict) -> Optional[dict]:
        raw = params.get("options") if isinstance(params.get("options"), dict) else {}
        try:
            return asdict(modeller.parse_options(raw))
        except (AppError, TypeError, ValueError):
            return dict(raw) or None

    @staticmethod
    def _search(row: dict) -> dict:
        source = row.get("template_source") or ""
        if source == USER_TEMPLATE or row.get("identity") is None:
            return {"engine": USER_TEMPLATE if source == USER_TEMPLATE else ENGINE_LABEL.get(source, source)}
        return {"engine": ENGINE_LABEL.get(source, source), "identity": row.get("identity"),
                "coverage": row.get("coverage"), "evalue": row.get("evalue"), "twilight": bool(row.get("twilight"))}

    def _item_templates(self, job_id: str, folder: str, alignment: str, row: dict, metrics: dict,
                        model_head: str) -> list[dict]:
        """Template di un elemento: voci dell'allineamento, copia preparata nella cartella (catene, intervallo,
        ritaglio pLDDT) e metadati della libreria."""
        entries = sequences.parse_pir(alignment)
        target = next((e.sequence for e in entries if e.kind == "sequence"), "")
        ids = [x.strip() for x in str(row.get("template") or "").split(",") if x.strip()]
        trims = report.model_trims(model_head)
        templates = [e for e in entries if e.is_template]
        out = []
        for e in templates:
            tid = next((i for i in ids if e.code.upper() == sequences.sanitize_code(i).upper()
                        or e.code.upper().startswith(sequences.sanitize_code(i).upper() + "_")), None)
            first, last = (e.fields + ["", "", "", ""])[0], (e.fields + ["", "", "", ""])[2]
            chains = sorted({c for c in ((e.fields + ["", "", "", ""])[1], (e.fields + ["", "", "", ""])[3]) if c})
            trim = None
            try:
                prepared = self.jobs.file_path(job_id, f"{folder}/{e.code}.pdb")
                summaries = structure.chain_summaries(structure.first_model(structure.load_structure(prepared, e.code)))
                if summaries:
                    chains = [c["chain"] for c in summaries]
                    first, last = summaries[0]["first"], summaries[-1]["last"]
                trim = report.prepared_trim(_head(prepared))
            except AppError:
                pass
            meta = self._library(tid)
            # `template_predicted` delle metriche vale per l'elemento: identifica il template solo se è l'unico
            predicted = bool(meta.get("predicted") or trim or e.code in trims
                             or (metrics.get("template_predicted") and len(templates) == 1))
            if trim is None and isinstance(trims.get(e.code), dict):
                trim = trims[e.code]
            identity, coverage = report.alignment_identity(target, e.sequence) if target else (None, None)
            out.append({"id": tid or e.code, "code": e.code, "chains": chains, "first": first, "last": last,
                        "source": self._source(meta), "title": meta.get("title"), "method": meta.get("method"),
                        "resolution": meta.get("resolution"), "identity": identity, "coverage": coverage,
                        "predicted": predicted, "predictor": meta.get("predictor") if predicted else None,
                        "plddt_mean": meta.get("plddt_mean") if predicted else None,
                        "trim": trim, "trim_off": predicted and trim is None and e.code in trims})
        return out

    @staticmethod
    def _item_warnings(alignment: str, row: dict) -> list[str]:
        warnings = list(sequences.validate_pir(alignment)["warnings"]) if alignment else []
        if row.get("twilight"):
            warnings.append("Template in zona crepuscolare (identità o copertura sotto le soglie della pipeline): il "
                            "modello va verificato con attenzione.")
        return warnings

    # ------------------------------------------------------------------------------ job refine (F9.1)
    def _refine_data(self, job: dict, rel: str, qmean_job: Optional[str]) -> tuple[dict, str]:
        res = self._completed(job)
        if rel != res.get("refined_file"):
            raise NotFound(f"{rel} non è il modello rifinito del job {job['id']} ({res.get('refined_file')}).")
        path = self.jobs.file_path(job["id"], rel)
        quality = load_quality_report(self.jobs, job["id"], rel)
        cons, qmean = self._quality(job["id"], rel, quality, None, qmean_job)
        openmm = res.get("openmm") if isinstance(res.get("openmm"), dict) else {}
        tools = self._tools(_head(path), quality, qmean)
        tools[1:1] = [("OpenMM", str(openmm.get("openmm") or "")), ("PDBFixer", "")]
        seq, chains = self._structure_sequence(path, quality)
        label = res.get("label") or describe_source(res.get("source"))
        data = self._base(job, "refine")
        data.update({
            "model": rel, "quality": quality, "consensus": cons, "tools": tools,
            "target": {"name": f"{label} (rifinito)", "sequence": seq, "chains": chains},
            "source": describe_source(res.get("source")),
            "refine": {k: res.get(k) for k in ("label", "protocol", "openmm", "added", "input", "energy", "rmsd",
                                               "quality", "comparison", "input_file")},
            "warnings": [str(w) for w in res.get("warnings") or []],
        })
        return data, f"report_{PurePosixPath(rel).stem}"

    # ------------------------------------------------------------------------------ job mutate e alascan (F9.2)
    MUTATION_FIELDS = ("notation", "mutations", "assessment", "sites", "dope", "energy", "comparison", "rmsd",
                       "protocol", "caveat", "files")

    def _mutate_data(self, job: dict, rel: str, qmean_job: Optional[str]) -> tuple[dict, str]:
        """Mutante o selvatico riottimizzato di un job `mutate`: sezione "Mutagenesi in silico" e la qualità del
        modello (il suo `<file>.quality.json`)."""
        res = self._completed(job)
        files = res.get("files") if isinstance(res.get("files"), dict) else {}
        if rel not in (files.get("mutant"), files.get("wild_type")):
            raise NotFound(f"{rel} non è un modello del job {job['id']} ({files.get('mutant')}, "
                           f"{files.get('wild_type')}).")
        path = self.jobs.file_path(job["id"], rel)
        quality = load_quality_report(self.jobs, job["id"], rel)
        cons, qmean = self._quality(job["id"], rel, quality, None, qmean_job)
        tools = self._tools(_head(path), quality, qmean)
        if not any(name == "MODELLER" for name, _v in tools):
            tools.insert(1, ("MODELLER", "(mutate_model)"))
        energy = res.get("energy") if isinstance(res.get("energy"), dict) else {}
        if energy.get("openmm"):
            tools.insert(2, ("OpenMM", str(energy["openmm"])))
        seq, chains = self._structure_sequence(path, quality)
        label = res.get("label") or describe_source(res.get("source"))
        role = "mutante " + str(res.get("notation") or "") if rel == files.get("mutant") else \
            "selvatico riottimizzato con lo stesso protocollo"
        data = self._base(job, "mutate")
        data.update({
            "model": rel, "quality": quality, "consensus": cons, "tools": tools,
            "target": {"name": f"{label} ({role.strip()})", "sequence": seq, "chains": chains},
            "source": describe_source(res.get("source")),
            "mutation": {k: res.get(k) for k in self.MUTATION_FIELDS},
            "warnings": [str(w) for w in res.get("warnings") or []],
        })
        return data, f"report_{PurePosixPath(rel).stem}"

    def _alascan_data(self, job: dict) -> tuple[dict, str]:
        """Scansione di alanina: tabella, esclusi e hot spot della struttura scandita (nessun modello analizzato)."""
        res = self._completed(job)
        source = res.get("source") if isinstance(res.get("source"), dict) else {}
        try:
            path, _label = self.compute.resolve_structure(source)
        except AppError:  # struttura eliminata dopo la scansione: la tabella basta
            path = None
        label = str(res.get("label") or "struttura")
        seq, chains = self._structure_sequence(path or Path(label), None) if path else ("", [])
        data = self._base(job, "alascan")
        data.update({
            "tools": [("BioLoom", __version__), ("MODELLER", "(mutate_model)")]
            + ([("OpenMM", "")] if (res.get("protocol") or {}).get("energy") else []),
            "target": {"name": label, "sequence": seq, "chains": chains}, "source": describe_source(source),
            "scan": {k: res.get(k) for k in ("rows", "skipped", "summary", "hotspots", "selection", "protocol",
                                             "caveat", "interface")},
            "warnings": [str(w) for w in res.get("warnings") or []],
        })
        return data, f"report_alascan_{_label_stem(label)}"

    # ------------------------------------------------------------------------------ job quality
    def _quality_data(self, job: dict, qmean_job: Optional[str]) -> tuple[dict, str]:
        quality = self._completed(job)
        # consenso della rotta: GA341/zDOPE se la struttura è un modello di un job, QMEAN se indicato
        cons = self.compute.consensus(job["id"], None, qmean_job)
        qmean = self._qmean_info(qmean_job) if qmean_job else None
        params = job.get("params") or {}
        source = params.get("source") if isinstance(params.get("source"), dict) else {}
        path = None
        try:
            path, _label = self.compute.resolve_structure(source)
        except AppError:  # file eliminato dopo l'analisi: il rapporto basta
            path = None
        struct = quality.get("structure") if isinstance(quality.get("structure"), dict) else {}
        label = quality.get("label") or struct.get("file") or "struttura"
        seq, chains = self._structure_sequence(path or Path(str(label)), quality)
        data = self._base(job, "quality")
        data.update({"quality": quality, "consensus": cons, "tools": self._tools(_head(path), quality, qmean),
                     "target": {"name": label, "sequence": seq, "chains": chains}, "structure": struct,
                     "source": describe_source(source)})
        return data, f"report_qualita_{_label_stem(label)}"
