"""Report HTML dei modelli (domain/report.py): raccolta dei dati di un job model, loop, pipeline o quality.

Il report si genera al volo dai file già salvati del job, senza ricalcoli: rapporto di qualità del modello
(`<modello>.quality.json`), risultati di MODELLER (`models.json` o risultato del job), allineamento, parametri e
template (metadati della libreria). Il consenso si ricompone con GA341/zDOPE del modello; con un job QMEAN passa dalla
rotta del consenso (`ComputeService.consensus`), che controlla che QMEAN riguardi la stessa struttura.

* **model / loop:** il modello deve essere uno di quelli del risultato del job (non fallito).
* **pipeline:** il file è `items/<idx>_<nome>/<modello>` di un elemento completato; target, template scelto dalla
  ricerca e metriche vengono dalla riga dell'elemento, allineamento e modelli dalla sua cartella. Lo ZIP della
  pipeline usa `pipeline_item` per il modello migliore di ogni elemento, un report alla volta.
* **quality:** report ridotto della struttura analizzata (niente template, allineamento e parametri).
"""

from __future__ import annotations

import re
from collections import OrderedDict
from dataclasses import asdict
from pathlib import Path, PurePosixPath
from typing import Optional

from .. import __version__
from ..core.db import Database, now_iso
from ..core.errors import AppError, Conflict, NotFound
from ..core.jobs import JobManager
from ..core.storage import read_json
from ..domain import consensus, report, sequences, structure
from ..engines import modeller
from .compute import ComputeService, describe_source, load_quality_report

MODEL_KINDS = ("model", "loop", "pipeline")
# modello di un elemento di pipeline: items/<idx>_<nome>/<file>
ITEM_MODEL = re.compile(r"^items/(\d+)_([^/]+)/([^/]+)$")
HEAD_BYTES = 20_000  # intestazione dei PDB: EXPDTA di MODELLER e righe REMARK 999 BIOLOOM
ITEM_COLUMNS = ("idx, name, sequence, status, template, template_source, identity, coverage, evalue, twilight, "
                "best_model, grade, grade_score, metrics")
SOURCE_LABEL = {"alphafold": "AlphaFold DB (EBI)", "RCSB PDB": "RCSB PDB", "caricamento utente": "file caricato"}
ENGINE_LABEL = {"rcsb": "RCSB", "ncbi": "NCBI BLAST", "local": "BLAST+ locale", "mmseqs": "MMseqs2 locale",
                "alphafold": "AlphaFold DB"}
USER_TEMPLATE = "scelto dall'utente"


def _rel(model: str) -> str:
    return PurePosixPath(str(model or "").replace("\\", "/")).as_posix()


def _head(path: Optional[Path]) -> str:
    if path is None:
        return ""
    try:
        return structure.read_text_head(path, HEAD_BYTES)
    except OSError:
        return ""


def _strip_gaps(seq: str) -> str:
    """Sequenza di una voce PIR senza gap né ligandi ('.'); '/' separa le catene."""
    return "".join(c for c in seq if c.isalpha() or c == "/")


class ReportService:
    def __init__(self, jobs: JobManager, compute: ComputeService, db: Database) -> None:
        self.jobs = jobs
        self.compute = compute
        self.db = db

    # ------------------------------------------------------------------------------ ingressi
    def build(self, job_id: str, model: Optional[str] = None, *, qmean_job: Optional[str] = None,
              image_png: Optional[bytes] = None) -> tuple[str, str]:
        """(HTML del report, nome del file da scaricare). `model` per i job model, loop e pipeline; nessun modello
        per un'analisi di qualità. `image_png`: byte già verificati da `report.decode_png`."""
        job = self.jobs.get(job_id)
        kind = job["kind"]
        if not model:
            if kind != "quality":
                raise AppError("Indica il file del modello di cui generare il report.", code="report_model_required")
            data, stem = self._quality_data(job, qmean_job)
        elif kind in ("model", "loop"):
            data, stem = self._model_data(job, _rel(model), qmean_job)
        elif kind == "pipeline":
            data, stem = self._pipeline_data(job, _rel(model), qmean_job)
        elif kind == "quality":
            raise AppError("Il report di un'analisi di qualità riguarda la struttura analizzata: non indicare un file.",
                           code="report_unsupported")
        else:
            raise AppError(f"Il report è disponibile per i modelli dei job di modellazione, loop e pipeline e per le "
                           f"analisi di qualità, non per i job di tipo '{kind}'.", code="report_unsupported")
        data["image_png"] = image_png
        return report.render(data), report.safe_filename(stem)

    def pipeline_item(self, job: dict, row: dict) -> str:
        """Report del modello migliore di un elemento completato (ZIP della pipeline: senza immagine né QMEAN)."""
        data, _stem = self._pipeline_data(job, _rel(row.get("best_model") or ""), None, row=row)
        return report.render(data)

    # ------------------------------------------------------------------------------ parti comuni
    def _base(self, job: dict, kind: str) -> dict:
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
        try:
            return self.compute.library.describe(template_id)
        except (AppError, OSError):
            return {}

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
                        "plddt_mean": meta.get("plddt_mean") if predicted else None,
                        "trim": t.get("plddt") if isinstance(t.get("plddt"), dict) else None,
                        "trim_off": predicted and not t.get("plddt")})
        return out

    # ------------------------------------------------------------------------------ pipeline
    def _pipeline_data(self, job: dict, rel: str, qmean_job: Optional[str],
                       row: Optional[dict] = None) -> tuple[dict, str]:
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
        cons, qmean = self._quality(job["id"], rel, quality, {"ga341": entry.get("ga341"), "zdope": entry.get("zdope")},
                                    qmean_job)
        head = _head(path)
        alignment = self._item_text(job["id"], f"{folder}/alignment.ali")
        params = job.get("params") or {}
        metrics = row.get("metrics") if isinstance(row.get("metrics"), dict) else {}
        best = PurePosixPath(str(row.get("best_model") or "")).name
        data = self._base(job, "pipeline")
        data.update({
            "title": f"{job.get('title') or 'Pipeline'} · {row['name']}", "model": rel, "quality": quality,
            "consensus": cons, "tools": self._tools(head, quality, qmean),
            "target": {"name": row["name"], "sequence": row.get("sequence") or ""},
            "templates": self._item_templates(job["id"], folder, alignment, row, metrics, head),
            "search": self._search(row), "alignment": alignment, "options": self._options(params),
            "pipeline": {k: params.get(k) for k in ("template_mode", "engine", "evalue", "twilight")},
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
                        "predicted": predicted, "plddt_mean": meta.get("plddt_mean") if predicted else None,
                        "trim": trim, "trim_off": predicted and trim is None and e.code in trims})
        return out

    @staticmethod
    def _item_warnings(alignment: str, row: dict) -> list[str]:
        warnings = list(sequences.validate_pir(alignment)["warnings"]) if alignment else []
        if row.get("twilight"):
            warnings.append("Template in zona crepuscolare (identità o copertura sotto le soglie della pipeline): il "
                            "modello va verificato con attenzione.")
        return warnings

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
        return data, f"report_qualita_{PurePosixPath(str(label)).stem}"
