"""Mutagenesi in silico e scansione di alanina (F9.2): job `mutate` e `alascan`, varianti di UniProt.

- **mutate** (priorità 0, `core/jobs.PRIORITY`, come `analysis`: un processo di MODELLER di pochi secondi, l'utente
  aspetta davanti allo schermo; con l'energia di OpenMM un minuto o due): una variante con una o più sostituzioni
  (notazione `A:K48R, A:E51Q`). Il selvatico passa dallo stesso protocollo del mutante (`domain/mutations.py`); dei
  `MODELS` modelli di ciascuno si tiene quello con il DOPE più basso. Poi indicatori strutturali, ΔDOPE, ΔΔE facoltativo
  con OpenMM, classe qualitativa con le motivazioni, analisi di qualità del selvatico ottimizzato e del mutante
  (`<file>.quality.json`, pagina Qualità e report) e confronto delle metriche (`refinement.compare`).
- **alascan** (priorità 1, come `model`: un processo di MODELLER per residuo, minuti): ogni residuo scelto (elenco,
  interfaccia di una catena o tasca di un job `pockets`) diventa alanina con lo stesso protocollo; i residui sono passi
  indipendenti (`core/parallel.Steps`, fino ai job paralleli delle Impostazioni) e ogni processo prende il suo slot del
  budget di CPU. Tabella ordinata dalla sostituzione più destabilizzante, hot spot d'interfaccia, CSV.
- **Varianti di UniProt** (`variants`): feature naturali e di mutagenesi della voce (engines/remote, cache 7 giorni)
  portate sulla numerazione della struttura con un allineamento (`domain/mutations.map_variants`).

Niente cache dei calcoli e nessuna tabella nuova: i modelli sono file del job, il risultato resta nel job; la cronologia
di una struttura si legge dai parametri dei job come `/api/analysis/latest`.
"""

from __future__ import annotations

import csv
import io
import json
import sqlite3
import threading
import time
from functools import partial
from pathlib import Path
from typing import Optional

from ..core import cache
from ..core.errors import AppError, Conflict
from ..core.jobs import JobContext, JobManager
from ..core.parallel import Steps
from ..core.storage import atomic_write_json, atomic_write_text
from ..domain import interfaces, mutations, refinement, sequences, structure
from ..engines import mutate as engine
from ..engines import quality, remote
from .compute import ComputeService, source_key

MUTATE = "mutate"
ALASCAN = "alascan"
KINDS = (MUTATE, ALASCAN)
HISTORY_LIMIT = 200  # job esaminati per la cronologia di una struttura
SCAN_MODES = ("residues", "interface", "pocket")


def _quality(path: Path, ctx: JobContext, folder: Path) -> dict:
    return quality.analyze(path, ctx, with_dope=True, dope_workdir=folder)


class MutationService:
    def __init__(self, jobs: JobManager, compute: ComputeService) -> None:
        self.jobs = jobs
        self.compute = compute

    def register(self) -> None:
        self.jobs.register(MUTATE, self.handle_mutate)
        self.jobs.register(ALASCAN, self.handle_scan)

    # ------------------------------------------------------------------------------ struttura e validazione
    def _structure(self, source: dict) -> tuple[Path, str, object, int]:
        """(percorso, etichetta, primo modello, modelli nel file) della sorgente."""
        path, label = self.compute.resolve_structure(source or {})
        st = structure.load_structure(path, path.stem)
        return path, label, structure.first_model(st), len(st)

    def validate_mutate(self, params: dict) -> list[mutations.Mutation]:
        """Errori immediati, prima di accodare il job: struttura, notazione, residui, seme."""
        _path, _label, model, _n = self._structure(params.get("source") or {})
        mutations.seeds(int(params.get("seed") or mutations.SEED))
        return mutations.resolve(mutations.parse(params.get("mutations") or ""), model)

    def validate_scan(self, params: dict) -> tuple[list[mutations.Mutation], list[dict], dict]:
        """Residui della scansione (in alanina), esclusi con il motivo e descrizione della scelta."""
        _path, _label, model, _n = self._structure(params.get("source") or {})
        mutations.seeds(int(params.get("seed") or mutations.SEED))
        residues, selection = self._scan_residues(model, params.get("source") or {}, params.get("selection") or {})
        muts, skipped = mutations.scan_plan(model, residues, energy=bool(params.get("energy")))
        return muts, skipped, selection

    def _scan_residues(self, model, source: dict, sel: dict) -> tuple[list[tuple[str, str]], dict]:
        mode = sel.get("mode") or "residues"
        if mode not in SCAN_MODES:
            raise AppError(f"Scelta dei residui non valida: {mode}.", code="invalid_scan")
        chain = (sel.get("chain") or "").strip() or None
        if mode == "residues":
            residues = mutations.parse_residues(sel.get("residues") or "", model)
            return residues, {"mode": mode, "detail": f"{len(residues)} residui indicati"}
        if mode == "interface":
            result = interfaces.analyze(model)
            if len(result.get("chains") or []) < 2:
                raise AppError("La struttura ha una sola catena proteica: nessuna interfaccia da scandire.",
                               code="invalid_scan")
            rows = [r for r in result["residues"] if r["delta"] > interfaces.INTERFACE_MIN_DELTA
                    and (chain is None or r["chain"] == chain or (chain == mutations.BLANK and not r["chain"].strip()))]
            if not rows:
                raise AppError("Nessun residuo d'interfaccia" + (f" nella catena {chain}." if chain else "."),
                               code="invalid_scan")
            return [(r["chain"], r["resnum"]) for r in rows], {
                "mode": mode, "chain": chain,
                "detail": f"residui d'interfaccia (ΔSASA > 1 Å²){' della catena ' + chain if chain else ''}"}
        job_id = str(sel.get("pocket_job") or "").strip()
        if not job_id or sel.get("pocket") in (None, ""):
            raise AppError("Indica il calcolo delle tasche e la tasca da scandire.", code="invalid_scan")
        job = self.jobs.get(job_id)
        if job.get("kind") != "pockets" or job.get("status") != "completed" or not isinstance(job.get("result"), dict):
            raise Conflict("Il calcolo delle tasche indicato non è completato.")
        if source_key((job.get("params") or {}).get("source")) != source_key(source):
            raise AppError("Le tasche indicate sono di un'altra struttura.", code="invalid_scan")
        pocket = next((p for p in job["result"].get("pockets") or [] if int(p.get("id", -1)) == int(sel["pocket"])),
                      None)
        if pocket is None:
            raise AppError(f"La tasca {sel['pocket']} non c'è nel calcolo {job_id}.", code="invalid_scan")
        residues = []
        for r in pocket.get("residues") or []:
            res = mutations.find_residue(model[r["chain"]], r["resnum"]) if r["chain"] in model else None
            if res is not None and (r["chain"], r["resnum"]) not in residues:
                residues.append((r["chain"], r["resnum"]))
        return residues, {"mode": mode, "pocket_job": job_id, "pocket": int(sel["pocket"]),
                          "detail": f"residui della tasca {int(sel['pocket'])} di fpocket"}

    # ------------------------------------------------------------------------------ parti comuni dei job
    @staticmethod
    def _context_warnings(model, info: dict, sites: list[tuple[str, str]], models_in_file: int) -> list[str]:
        """Avvisi sulla copia per MODELLER: modelli in più, gruppi esclusi (con quelli vicini ai siti), residui
        modificati, interruzioni, catene rinominate."""
        out = []
        if models_in_file > 1:
            out.append(f"La struttura ha {models_in_file} modelli: usato il primo.")
        if info["excluded"]:
            names = ", ".join(info["excluded"][:10]) + (f" e altri {len(info['excluded']) - 10}"
                                                        if len(info["excluded"]) > 10 else "")
            out.append(f"Gruppi eteroatomici esclusi dal calcolo: {names}. I mutanti contengono solo la proteina.")
            near = mutations.neighbourhood_hetero(model, sites, mutations.LOCAL_RADIUS)
            if near:
                out.append(f"Entro {mutations.LOCAL_RADIUS:g} Å dai residui mutati c'è {', '.join(near)}: il protocollo "
                           "non ne tiene conto (né del suo contributo alla stabilità).")
        if info["modified"]:
            out.append("Residui modificati trattati come il residuo standard: " + ", ".join(info["modified"][:10])
                       + ".")
        if info["breaks"]:
            where = ", ".join(f"{mutations.chain_name(b['chain'])}{b['after']}/{b['before']}" for b in info["breaks"][:8])
            out.append(f"Catena interrotta ({where}): ogni tratto è una catena separata per MODELLER, nessun residuo "
                       "mancante viene ricostruito.")
        return out

    @staticmethod
    def _variants(muts: list[mutations.Mutation], info: dict) -> list[dict]:
        """Varianti per lo script: il selvatico riottimizzato (residui in sé stessi) e il mutante."""
        def rows(target: str) -> list[dict]:
            return [{"chain": info["residues"][(m.chain, m.resnum)], "resnum": m.resnum,
                     "restyp": mutations.AMINO[m.wt if target == "wt" else m.mut]} for m in muts]
        return [{"name": "wt", "role": "wild_type", "mutations": rows("wt")},
                {"name": "mut", "role": "mutant", "mutations": rows("mut")}]

    @staticmethod
    def _interchain(row: dict, info: dict, chains: list[str]) -> Optional[float]:
        """DOPE fra le catene che riguarda le catene mutate: quello del loro gruppo (una catena) o il totale fra tutte
        (più catene mutate: metà della somma dei gruppi, ogni coppia di catene vi compare due volte)."""
        values = row.get("interchain") or []
        groups = list(info["groups"])
        if len(values) != len(groups) or len(groups) < 2:
            return None
        if len(set(chains)) == 1:
            return float(values[groups.index(chains[0])])
        return float(sum(values)) / 2.0

    def _build(self, ctx: JobContext, workdir: Path, model, muts: list[mutations.Mutation], info: dict,
               text: str, seed: int, names: tuple[str, str], progress_range: tuple[float, float],
               keep_input: bool = True) -> dict:
        """MODELLER per una variante: modelli, scelta dei migliori, file finali, ΔDOPE, indicatori strutturali."""
        sites = [(m.chain, m.resnum) for m in muts]
        local = [(info["residues"][k], k[1]) for k in mutations.neighbourhood(model, sites, mutations.LOCAL_RADIUS)
                 if k in info["residues"]]
        groups = list(info["groups"].values()) if len(info["groups"]) > 1 else []
        rows = engine.run(ctx, workdir, input_text=text, variants=self._variants(muts, info),
                          seeds=mutations.seeds(seed), local=local, groups=groups, progress_range=progress_range)
        if keep_input is False:  # scansione: la copia per MODELLER è una sola, nella cartella del job
            (workdir / engine.INPUT).unlink(missing_ok=True)
        renamed = engine.finalize(workdir, rows, {"wild_type": names[0], "mutant": names[1]}, info["chains"])
        best_wt, best_mut = engine.best(rows, "wild_type"), engine.best(rows, "mutant")
        chains = [m.chain for m in muts]
        for row in (best_wt, best_mut):
            row["interchain"] = self._interchain(row, info, chains)
        dope = mutations.dope_summary(best_wt, best_mut, info["residues_count"], len(local), rows)
        wt_model = structure.first_model(structure.load_structure(workdir / names[0], "wt"))
        mut_model = structure.first_model(structure.load_structure(workdir / names[1], "mut"))
        return {"rows": rows, "dope": dope, "renamed": renamed, "wt_model": wt_model, "mut_model": mut_model,
                "local": len(local)}

    @staticmethod
    def _interface_rows(model) -> dict[tuple[str, str], dict]:
        result = interfaces.analyze(model)
        if len(result.get("chains") or []) < 2:
            return {}
        return {(r["chain"], r["resnum"]): r for r in result["residues"]}

    def _site_indicators(self, built: dict, muts: list[mutations.Mutation], iface: dict) -> list[dict]:
        wt_facts = mutations.facts(built["wt_model"])
        mut_facts = mutations.facts(built["mut_model"])
        return [mutations.site_indicators(m, wt_facts, mut_facts, quality.MAX_ASA, iface.get((m.chain, m.resnum)))
                for m in muts]

    @staticmethod
    def _moved(model, muts: list[mutations.Mutation]) -> set[tuple[str, str]]:
        return set(mutations.neighbourhood(model, [(m.chain, m.resnum) for m in muts], mutations.RADIUS))

    @staticmethod
    def _protocol(seed: int, energy: bool) -> dict:
        return {"method": "mutate_model.py della Sali lab (Feyfant, Sali & Fiser 2007), esteso alle catene laterali "
                          "vicine", "radius": mutations.RADIUS, "local_radius": mutations.LOCAL_RADIUS,
                "models": mutations.MODELS, "seeds": mutations.seeds(seed), "randomize": mutations.RANDOMIZE,
                "selection": "modello con il DOPE più basso per variante",
                "wild_type": "selvatico riottimizzato con lo stesso protocollo", "energy": energy,
                "core_rsa": mutations.CORE_RSA, "thresholds": {
                    "dope_strong": mutations.DOPE_STRONG, "dope_moderate": mutations.DOPE_MODERATE,
                    "energy_strong": mutations.ENERGY_STRONG, "energy_moderate": mutations.ENERGY_MODERATE,
                    "apolar_loss": mutations.APOLAR_LOSS, "helix_moderate": mutations.HELIX_MODERATE}}

    # ------------------------------------------------------------------------------ job mutate
    def handle_mutate(self, ctx: JobContext, p: dict) -> dict:
        started = time.perf_counter()
        source = p.get("source") or {}
        path, label, model, n_models = self._structure(source)
        fingerprint = cache.content_hash(path)
        muts = mutations.resolve(mutations.parse(p.get("mutations") or ""), model)
        seed = int(p.get("seed") or mutations.SEED)
        want_energy = bool(p.get("energy"))
        ctx.progress(0.02, "Preparazione della struttura")
        text, info = mutations.modeller_input(model)
        sites = [(m.chain, m.resnum) for m in muts]
        warnings = self._context_warnings(model, info, sites, n_models)
        code = sequences.sanitize_code(label, default="struttura")
        tag = mutations.file_tag(muts, multichain=len(info["groups"]) > 1)
        names = (f"{code}_wt.pdb", f"{code}_{tag}.pdb")
        if names[0] == names[1]:
            names = (f"{code}_selvatico.pdb", names[1])
        ctx.log(f"Mutazioni: {mutations.notation(muts)}; {mutations.MODELS} modelli per variante, semi "
                f"{', '.join(str(s) for s in mutations.seeds(seed))}")
        ctx.progress(0.05, "Mutante e selvatico con MODELLER")
        built = self._build(ctx, ctx.workdir, model, muts, info, text, seed, names, (0.05, 0.55))
        if built["renamed"]:
            warnings.append("Catene con un nome di più caratteri rinominate nei modelli (formato PDB): "
                            + ", ".join(built["renamed"]) + ".")
        ctx.progress(0.58, "Indicatori strutturali")
        site_rows = self._site_indicators(built, muts, self._interface_rows(model))
        energy = None
        if want_energy:
            ctx.progress(0.62, "Energia con OpenMM")
            energy = engine.energy(ctx, ctx.workdir, ctx.workdir / names[0], ctx.workdir / names[1], sites,
                                   names=(Path(names[0]).stem, Path(names[1]).stem),
                                   progress=lambda f, stage: ctx.progress(0.62 + 0.23 * f, stage))
            if not energy.get("available"):
                warnings.append(f"{energy.get('message')} L'energia non è stata calcolata: la stima usa DOPE e "
                                "indicatori strutturali.")
            elif energy.get("error"):
                warnings.append(f"Energia con OpenMM non riuscita: {energy['error']}")
        assessment = mutations.classify(site_rows, built["dope"], energy if energy and energy.get("ddE") is not None
                                        else None)
        ctx.progress(0.86, "Analisi di qualità del selvatico ottimizzato e del mutante")
        with Steps(check=ctx.check, parallel=True, workers=2, name="mutagenesi") as steps:
            for key, name in (("wild_type", names[0]), ("mutant", names[1])):
                folder = ctx.workdir / f"qa_{key}"
                steps.start(key, partial(_quality, ctx.workdir / name, ctx.sub(key, folder), folder))
            reports = {k: steps.result(k) for k in ("wild_type", "mutant")}
        for key, name in (("wild_type", names[0]), ("mutant", names[1])):
            atomic_write_json(ctx.workdir / f"{name}.quality.json", reports[key])
        metrics = {k: quality.summary_metrics(v) for k, v in reports.items()}
        rmsd = mutations.backbone_shift(built["wt_model"], built["mut_model"], self._moved(model, muts))
        quality.inject_remarks(ctx.workdir / names[1], [
            f"MUTANT OF {label} {mutations.notation(muts).replace(', ', ' ')}",
            f"MODELLER MUTATE_MODEL RADIUS {mutations.RADIUS:g} A BEST DOPE OF {mutations.MODELS} SEEDS "
            + " ".join(str(s) for s in mutations.seeds(seed)),
            f"DELTA DOPE {built['dope']['delta']} ({built['dope']['delta_eq']} RESIDUE EQUIVALENTS)",
            f"ASSESSMENT {assessment['code'].upper()} (QUALITATIVE, NOT A DDG)",
        ])
        quality.inject_remarks(ctx.workdir / names[0], [
            f"WILD TYPE OF {label} REOPTIMIZED AT {' '.join(m.site for m in muts)} (SAME PROTOCOL AS THE MUTANT)"])
        dope = built["dope"]
        ctx.log(f"ΔDOPE {dope['delta']} ({dope['delta_eq']} residui equivalenti), locale {dope['delta_local']}; "
                f"classe: {assessment['label']}")
        ctx.log(f"Mutagenesi completata in {time.perf_counter() - started:.1f} s")
        return {
            "version": mutations.VERSION, "source": source, "label": label, "source_sha256": fingerprint,
            "mutations": [m.to_dict() for m in muts], "notation": mutations.notation(muts),
            "files": {"wild_type": names[0], "mutant": names[1],
                      **({"wild_type_minimized": energy["files"]["wild_type"],
                          "mutant_minimized": energy["files"]["mutant"]}
                         if energy and energy.get("files") else {})},
            "protocol": self._protocol(seed, want_energy),
            "models": [{k: r.get(k) for k in ("variant", "role", "seed", "dope", "local", "interchain", "chosen",
                                              "moved_atoms")} for r in built["rows"]],
            "dope": dope, "energy": energy, "sites": site_rows, "assessment": assessment,
            "quality": metrics, "comparison": refinement.compare(metrics["wild_type"], metrics["mutant"]),
            "rmsd": rmsd, "residues": info["residues_count"], "warnings": warnings, "caveat": mutations.CAVEAT,
        }

    # ------------------------------------------------------------------------------ job alascan
    def handle_scan(self, ctx: JobContext, p: dict) -> dict:
        started = time.perf_counter()
        source = p.get("source") or {}
        path, label, model, n_models = self._structure(source)
        fingerprint = cache.content_hash(path)
        residues, selection = self._scan_residues(model, source, p.get("selection") or {})
        want_energy = bool(p.get("energy"))
        muts, skipped = mutations.scan_plan(model, residues, energy=want_energy)
        seed = int(p.get("seed") or mutations.SEED)
        ctx.progress(0.02, "Preparazione della struttura")
        text, info = mutations.modeller_input(model)
        atomic_write_text(ctx.workdir / engine.INPUT, text)
        warnings = self._context_warnings(model, info, [(m.chain, m.resnum) for m in muts], n_models)
        iface = self._interface_rows(model)
        multichain = len(info["groups"]) > 1
        total = len(muts)
        lock = threading.Lock()
        done = [0]
        ctx.log(f"Scansione di alanina: {total} residui ({selection['detail']}); {len(skipped)} esclusi")
        ctx.progress(0.04, f"0 di {total} residui")

        def one(m: mutations.Mutation) -> dict:
            tag = mutations.file_tag([m], multichain)
            folder = ctx.workdir / "siti" / tag
            sub = ctx.sub(tag, folder)
            names = (f"{tag}_wt.pdb", f"{tag}_ala.pdb")
            built = self._build(sub, folder, model, [m], info, text, seed, names, (0.0, 1.0), keep_input=False)
            site_rows = self._site_indicators(built, [m], iface)
            energy = None
            if want_energy:
                energy = engine.energy(sub, folder, folder / names[0], folder / names[1], [(m.chain, m.resnum)],
                                       names=(Path(names[0]).stem, Path(names[1]).stem))
            assessment = mutations.classify(site_rows, built["dope"],
                                            energy if energy and energy.get("ddE") is not None else None)
            with lock:
                done[0] += 1
                ctx.progress(0.04 + 0.92 * done[0] / total, f"{done[0]} di {total} residui")
            sub.log(f"{m.label}: {assessment['label']} (ΔDOPE {built['dope']['delta']})")
            return {"mutation": m.to_dict(), "label": m.label, "site": site_rows[0], "dope": built["dope"],
                    "energy": energy, "assessment": assessment,
                    "files": {"wild_type": f"siti/{tag}/{names[0]}", "mutant": f"siti/{tag}/{names[1]}"}}

        lanes = max(1, min(total, int(ctx.settings.effective_workers())))
        with Steps(check=ctx.check, parallel=lanes > 1, workers=lanes, name="alascan") as steps:
            for m in muts:
                steps.start(m.label, partial(one, m))
            rows, failed = [], []
            for m in muts:
                out = steps.outcome(m.label)
                if out.error is None:
                    rows.append(out.value)
                elif isinstance(out.error, AppError):
                    failed.append({"label": m.label, "error": out.error.message.splitlines()[0]})
                else:
                    raise out.error
        if not rows:
            raise AppError("Nessun residuo della scansione è riuscito: " + "; ".join(
                f"{f['label']}: {f['error']}" for f in failed[:3]), code="alascan_failed")
        for f in failed:
            warnings.append(f"{f['label']} non calcolato: {f['error']}")
        if want_energy:
            missing = next((r["energy"].get("message") for r in rows
                            if r.get("energy") and not r["energy"].get("available")), None)
            if missing:
                warnings.append(f"{missing} L'energia non è stata calcolata.")
        rows = mutations.scan_order(rows)
        counts = {code: sum(1 for r in rows if r["assessment"]["code"] == code) for code in mutations.CLASSES}
        hotspots = [{"label": r["label"], "level": r["assessment"]["binding"]["hotspot"],
                     "text": r["assessment"]["binding"]["text"]}
                    for r in rows if (r["assessment"].get("binding") or {}).get("hotspot")]
        atomic_write_text(ctx.workdir / "alascan.csv", scan_csv(rows))
        ctx.log(f"Scansione completata in {time.perf_counter() - started:.1f} s: {counts['destabilizing']} "
                f"probabilmente destabilizzanti, {counts['uncertain']} incerte, {counts['neutral']} neutre; "
                f"{len(hotspots)} hot spot")
        return {
            "version": mutations.VERSION, "source": source, "label": label, "source_sha256": fingerprint,
            "selection": selection, "protocol": self._protocol(seed, want_energy), "rows": rows, "skipped": skipped,
            "failed": failed, "summary": {"residues": len(rows), "skipped": len(skipped), "counts": counts,
                                          "hotspots": len(hotspots)},
            "hotspots": hotspots, "interface": bool(iface), "residues": info["residues_count"], "csv": "alascan.csv",
            "warnings": warnings, "caveat": mutations.CAVEAT,
        }

    # ------------------------------------------------------------------------------ cronologia e varianti
    def history(self, source: dict, limit: int = 20) -> list[dict]:
        """Job `mutate` e `alascan` sulla stessa sorgente, dal più recente (anche in coda o in corso)."""
        key = source_key(source)
        if key == ("?",):
            return []
        sql = ("FROM jobs WHERE kind IN ('mutate','alascan') ORDER BY created_at DESC, rowid DESC LIMIT ?")
        try:
            rows = self.jobs.db.query(f"SELECT id, json_extract(params, '$.source') AS source {sql}", (HISTORY_LIMIT,))
        except sqlite3.OperationalError:
            rows = [{"id": r["id"], "source": json.dumps((r.get("params") or {}).get("source"))}
                    for r in self.jobs.db.query(f"SELECT id, params {sql}", (HISTORY_LIMIT,))]
        out = []
        for r in rows:
            try:
                src = json.loads(r["source"]) if isinstance(r.get("source"), str) else None
            except ValueError:
                src = None
            if source_key(src) != key:
                continue
            try:
                job = self.jobs.get(r["id"], include_result=False)
            except AppError:
                continue
            params = self.jobs.db.query_one("SELECT params FROM jobs WHERE id=?", (r["id"],)) or {}
            p = params.get("params") or {}
            out.append({**{k: job.get(k) for k in ("id", "kind", "title", "status", "created_at", "finished_at")},
                        "mutations": p.get("mutations") if job["kind"] == MUTATE else None,
                        "energy": bool(p.get("energy"))})
            if len(out) >= limit:
                break
        return out

    def variants(self, accession: str, source: dict, chain: Optional[str] = None) -> dict:
        """Varianti di UniProt dell'accessione portate sulla struttura (posizioni, applicabilità, notazione)."""
        _path, label, model, _n = self._structure(source)
        entry = remote.uniprot_variants(accession)
        mapped = mutations.map_variants(entry["variants"], entry["sequence"], model, chain)
        return {"accession": entry["accession"], "entry_name": entry.get("entry_name", ""), "label": label,
                "uniprot_length": len(entry["sequence"]), **mapped}


SCAN_COLUMNS = ("mutazione", "classe", "punteggio", "delta_dope", "delta_dope_residui_equivalenti", "delta_dope_locale",
                "delta_dope_fra_catene", "ddE_kJ_mol", "rsa_selvatico", "struttura_secondaria", "interfaccia",
                "hot_spot", "motivi")


def scan_csv(rows: list[dict]) -> str:
    """Tabella della scansione di alanina in CSV (separatore virgola, punto decimale)."""
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(SCAN_COLUMNS)
    for r in rows:
        a, d, e, s = r["assessment"], r["dope"], r.get("energy") or {}, r["site"]
        writer.writerow([r["label"], a["label"], a["score"], d.get("delta"), d.get("delta_eq"), d.get("delta_local"),
                         d.get("interchain_delta"), e.get("ddE"), s.get("rsa_wt"), s.get("ss"),
                         "sì" if s.get("interface") else "no", (a.get("binding") or {}).get("hotspot") or "",
                         " | ".join(a["reasons"])])
    return buffer.getvalue()
