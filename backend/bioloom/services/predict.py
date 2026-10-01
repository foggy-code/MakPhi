"""Confronto con un predittore esterno di struttura (F9.3): job `predict`, controllo di plausibilità di un modello.

1. **Predizione.** ESMFold dal servizio ESM Atlas (`engines/remote.esmfold_predict`: la sequenza lascia il computer,
   quindi solo con il consenso dell'impostazione `esmfold_remote`, controllato all'invio e di nuovo all'esecuzione),
   oppure, se l'utente lo sceglie e c'è un'accessione UniProt, il modello di AlphaFold DB già supportato
   (`TemplateLibrary.fetch_alphafold`: a EBI va solo l'accessione). La predizione è un file del job
   (`prediction_esmfold.pdb`, `prediction_AF-<ACC>-F<n>.pdb`) riconosciuto come modello predetto dal riconoscitore unico
   (`domain/plddt.predictor`): la pagina Qualità lo apre con il pLDDT.
2. **Confronto con il modello** (se c'è una struttura, `source`): `engines/align.structural` con il percorso integrato
   (corrispondenza per sequenza: è la stessa proteina, e così restano anche le coppie lontane), modello = A,
   riferimento; PDB sovrapposto `superposed.pdb` per il componente StructuralComparison.
3. **Esito** (`domain/prediction.assess`): TM-score, regioni divergenti incrociate con il pLDDT della predizione e con
   le regioni problematiche del consenso del modello (rapporto `<modello>.quality.json`, oppure l'analisi di qualità
   più recente della stessa struttura).

Il consenso di qualità include l'accordo con la predizione solo se la si chiede (`ComputeService.consensus` con
`predict_job`, `plausibility_for`); il report del modello aggiunge la sezione del confronto più recente sulla stessa
struttura (`latest`).

**Priorità** 0 (`core/jobs.PRIORITY`): l'utente aspetta davanti allo schermo. Pool "io": quasi tutto il tempo è
l'attesa del servizio remoto; il confronto integrato è Python breve (meno di 0,1 s su 1UBQ).
"""

from __future__ import annotations

import shutil
import time
from typing import Optional

from ..core import cache
from ..core.errors import AppError, Conflict
from ..core.jobs import JobContext, JobManager
from ..core.storage import atomic_write_text
from ..domain import plddt, prediction, sequences, structure
from ..engines import align, remote
from .compute import ComputeService, describe_source, load_quality_report, source_key
from .structure_tools import StructureToolsService

KIND = "predict"
PREDICTION_ESMFOLD = "prediction_esmfold.pdb"
SUPERPOSED = "superposed.pdb"
CONSENT_MESSAGE = ("L'invio delle sequenze a ESMFold non è consentito: abilitalo in Impostazioni → Generale (la "
                   "sequenza lascia il computer e va al servizio esterno).")
COMPARISON_NOTE = ("Corrispondenza dei residui per sequenza (Needleman-Wunsch): la predizione è della stessa proteina "
                   "e così restano anche le coppie lontane, che TM-align lascerebbe fuori.")


class PredictService:
    def __init__(self, jobs: JobManager, compute: ComputeService) -> None:
        self.jobs = jobs
        self.compute = compute

    def register(self) -> None:
        self.jobs.register(KIND, self.handle, pool="io")

    # ------------------------------------------------------------------------------ richiesta
    def request(self, params: dict, settings, *, require_consent: bool = True) -> dict:
        """Richiesta validata (rotta di avvio, controlli preventivi, handler): predittore, sequenza da predire,
        struttura da confrontare con la catena, accessione per AlphaFold DB. Errori in italiano prima di qualunque
        richiesta remota: struttura o catena assenti, sequenza non valida o troppo lunga per ESMFold, accessione
        mancante, consenso all'invio non dato (`require_consent`)."""
        name = str(params.get("predictor") or "esmfold")
        if name not in prediction.PREDICTORS:
            raise AppError(f"Predittore sconosciuto: {name} (ammessi: {', '.join(prediction.PREDICTORS)}).",
                           code="invalid_predictor")
        source = params.get("source") if isinstance(params.get("source"), dict) else None
        raw_seq = params.get("sequence") if isinstance(params.get("sequence"), dict) else {}
        chain = str(params.get("chain") or (source or {}).get("chain") or "").strip()
        path, label, trace = None, "", []
        if source:
            path, label = self.compute.resolve_structure(source)
            model = structure.first_model(structure.load_structure(path, label))
            trace = structure.ca_trace(model, chain or None)
            if not trace:
                raise AppError(f"La struttura {label} non ha {'la catena ' + chain if chain else 'residui con Cα'}.",
                               code="invalid_chain")
            chain = chain or trace[0]["chain"]
            trace = [t for t in trace if t["chain"] == chain]
        seq = str(raw_seq.get("sequence") or "")
        if not seq and not trace:
            raise AppError("Indica la sequenza da predire oppure la struttura da confrontare con la predizione.",
                           code="predict_input_required")
        seq = seq or "".join(t["aa"] for t in trace)
        seq_name = sequences.sanitize_code(raw_seq.get("name") or label or "sequenza", default="sequenza")
        out = {"predictor": name, "source": source, "path": path, "label": label, "chain": chain or None,
               "name": seq_name, "accession": None, "service": None}
        if name == "esmfold":
            out["sequence"] = remote.esmfold_sequence(seq)  # alfabeto, lunghezza: prima di ogni invio
            out["service"] = remote.validate_esmfold_url(settings.esmfold_url)
            if require_consent and not settings.esmfold_remote:
                raise AppError(CONSENT_MESSAGE, code="esmfold_disabled", status=403)
        else:
            accession = str(params.get("accession") or "").strip()
            if not accession:
                raise AppError("Indica l'accessione UniProt (o l'id AF-<ACC>-F<n>) del modello di AlphaFold DB.",
                               code="accession_required")
            parsed = remote.parse_alphafold_id(accession)
            out["accession"] = (f"AF-{parsed[0]}-F{parsed[1]}" if parsed
                                else remote.validate_uniprot_accession(accession))
            out["sequence"] = _clean_sequence(seq)
            out["service"] = remote.ALPHAFOLD
        return out

    # ------------------------------------------------------------------------------ job
    def handle(self, ctx: JobContext, p: dict) -> dict:
        started = time.perf_counter()
        settings = ctx.settings
        # il consenso si ricontrolla all'esecuzione: un job in coda non parte se nel frattempo è stato revocato
        req = self.request(p, settings)
        fingerprint = cache.content_hash(req["path"]) if req["path"] else None
        warnings: list[str] = []
        label = prediction.PREDICTORS[req["predictor"]]["label"]
        if req["predictor"] == "esmfold":
            ctx.progress(0.05, "Predizione ESMFold (servizio esterno)")
            ctx.log(f"Invio della sequenza {req['name']} ({len(req['sequence'])} residui) a {req['service']}")
            data = remote.esmfold_predict(req["sequence"], url=req["service"], name=req["name"], check=ctx.check,
                                          log=ctx.log)
            file = PREDICTION_ESMFOLD
            atomic_write_text(ctx.path(file), data["pdb"])
            info = data["info"]
            origin = {"service": data["url"], "cached": bool(data.get("cached")), "received": data.get("received"),
                      "scale": info.get("scale")}
            ctx.log(("Predizione dalla cache (ricevuta il " + str(data.get("received") or "?") + ")"
                     if data.get("cached") else "Predizione ricevuta")
                    + f": {info['residues']} residui, pLDDT medio {info['plddt']['mean']}"
                    + (" (scala del servizio 0–1, riportata a 0–100)" if info.get("scale") == "0-1" else ""))
        else:
            ctx.progress(0.05, "Modello di AlphaFold DB")
            meta = self.compute.library.fetch_alphafold(req["accession"], check=ctx.check, log=ctx.log)
            src = self.compute.library.path(meta["id"])
            file = f"prediction_{src.name}"
            shutil.copyfile(src, ctx.path(file))
            origin = {"service": remote.ALPHAFOLD, "cached": False, "entry": meta["id"],
                      "version": (meta.get("alphafold") or {}).get("version")}
            ctx.log(f"Modello di AlphaFold DB {meta['id']} (a EBI va solo l'accessione)")
        ctx.check()
        pred_path = ctx.path(file)
        pred_model = structure.first_model(structure.load_structure(pred_path, "prediction"))
        found = plddt.predictor(file, structure.read_text_head(pred_path, 200_000), plddt.atom_values(pred_model))
        if found is None:
            warnings.append(f"Il file di {label} non è riconosciuto come modello predetto (pLDDT nella colonna "
                            "B-factor): il confronto non può usare la confidenza della predizione.")
        pl_map = prediction.residue_plddt(pred_model) if found else {}
        result = {
            "predictor": req["predictor"], "label": label, "origin": origin, "prediction_file": file,
            "recognized_as": found, "sequence": {"name": req["name"], "length": len(req["sequence"] or "")},
            "plddt": plddt.summarize(list(pl_map.values())), "accession": req["accession"],
            "source": req["source"], "model_label": req["label"] or None, "chain": req["chain"],
            "source_sha256": fingerprint, "comparison": None, "plausibility": None, "warnings": warnings,
            "limits": prediction.LIMITS,
        }
        if req["path"] is not None:
            ctx.progress(0.6, "Confronto con il modello")
            sub = ctx.sub("confronto", ctx.workdir, on_progress=lambda f, s: ctx.progress(
                None if f is None else 0.6 + 0.3 * f, s))
            comp = align.structural(req["path"], req["chain"], pred_path, None, sub, tool="builtin",
                                    names=(f"{req['label']}{req['chain'] or ''}", label))
            superposed = comp.pop("superposed_pdb", None)
            if superposed:
                atomic_write_text(ctx.path(SUPERPOSED), superposed)
                comp["superposed_file"] = SUPERPOSED
            comp.update({"mode": "structural", "note": COMPARISON_NOTE})
            report = self._quality_report(req["source"], fingerprint)
            regions = prediction.consensus_regions(report) if report else None
            ctx.progress(0.92, "Esito del confronto")
            plaus = prediction.assess(comp, pl_map, predictor=req["predictor"], consensus_regions=regions)
            result.update({"comparison": comp, "plausibility": plaus})
            ctx.log(f"Confronto con {describe_source(req['source'])}: TM-score {comp.get('tm_score')}, lDDT "
                    f"{comp.get('lddt')}; esito: {plaus['status']}. {plaus['summary']}")
        for w in warnings:
            ctx.log(f"Avviso: {w}")
        ctx.log(f"Completato in {time.perf_counter() - started:.1f} s")
        return result

    def _quality_report(self, source: dict, fingerprint: Optional[str]) -> Optional[dict]:
        """Rapporto di qualità della stessa struttura, per le regioni problematiche del consenso: quello salvato
        accanto al modello di un job, altrimenti l'analisi di qualità completata più recente (stessa sorgente e
        stessa impronta del file)."""
        if source.get("type") == "job":
            report = load_quality_report(self.jobs, str(source.get("job_id") or ""), str(source.get("file") or ""))
            if isinstance(report, dict):
                return report
        job = StructureToolsService(self.jobs, self.compute).latest("quality", source, fingerprint)
        result = (job or {}).get("result")
        return result if isinstance(result, dict) else None

    # ------------------------------------------------------------------------------ consenso e report
    def plausibility_for(self, predict_job: str, source: dict, fingerprint: Optional[str] = None) -> dict:
        """Esito di un job `predict` completato sulla stessa struttura del rapporto (per il consenso e il report).

        Stessa struttura: stessa sorgente (`source_key`) e, se entrambi hanno l'impronta del file, stesso contenuto.
        Un'altra struttura è un errore, non un'esclusione silenziosa: l'accordo con la predizione di un'altra
        struttura renderebbe il consenso fuorviante."""
        job = self.jobs.get(predict_job)
        if job["kind"] != KIND:
            raise AppError(f"Il job {predict_job} non è un confronto con una predizione.", code="predict_job_invalid")
        if job["status"] != "completed" or not isinstance(job.get("result"), dict):
            raise Conflict("Il confronto con la predizione non è completato: il consenso non può ancora includerlo.")
        result = job["result"]
        plaus = result.get("plausibility")
        if not isinstance(plaus, dict):
            raise AppError(f"Il job {predict_job} ha solo la predizione, senza il confronto con un modello.",
                           code="predict_job_invalid")
        p_source = (job.get("params") or {}).get("source")
        if source_key(p_source) != source_key(source):
            raise AppError(f"Il confronto con la predizione riguarda un'altra struttura ({describe_source(p_source)}, "
                           f"non {describe_source(source)}).", code="predict_source_mismatch")
        theirs = result.get("source_sha256")
        if fingerprint and theirs and fingerprint != theirs:
            raise AppError(f"Il file di {describe_source(source)} è cambiato dopo il confronto con la predizione "
                           "(stesso nome, contenuto diverso): ripeti il confronto.", code="predict_source_mismatch")
        return {**plaus, "job_id": predict_job}

    def latest(self, source: dict, fingerprint: Optional[str] = None) -> Optional[dict]:
        """Job `predict` completato più recente con il confronto sulla stessa struttura, oppure None."""
        job = StructureToolsService(self.jobs, self.compute).latest(KIND, source, fingerprint)
        return job if job and isinstance((job.get("result") or {}).get("plausibility"), dict) else None

    def lookup(self, source: dict) -> dict:
        """Risposta di `/api/predict/latest` (come `/api/analysis/latest`)."""
        return StructureToolsService(self.jobs, self.compute).lookup(KIND, source)


def _clean_sequence(sequence: str) -> str:
    """Sequenza senza spazi, in maiuscolo (AlphaFold DB: non si invia, serve solo al risultato)."""
    return "".join(sequence.split()).upper()


def prediction_summary(job: dict) -> Optional[dict]:
    """Riepilogo per il report del modello: predittore, origine, pLDDT, metriche del confronto ed esito."""
    result = job.get("result") if isinstance(job.get("result"), dict) else {}
    plaus = result.get("plausibility")
    if not isinstance(plaus, dict):
        return None
    comp = result.get("comparison") if isinstance(result.get("comparison"), dict) else {}
    return {"job_id": job.get("id"), "finished_at": job.get("finished_at"), "predictor": result.get("predictor"),
            "label": result.get("label"), "origin": result.get("origin") or {}, "plddt": result.get("plddt") or {},
            "chain": result.get("chain"), "sequence": result.get("sequence") or {},
            "accession": result.get("accession"),
            "comparison": {k: comp.get(k) for k in ("tm_score", "tm_score_b", "rmsd", "rmsd_close", "gdt_ts", "gdt_ha",
                                                    "lddt", "aligned_length", "lengths", "method")},
            "plausibility": plaus}
