"""Analisi di qualità di una struttura: metriche globali e tabella unificata per residuo.

I passi indipendenti dell'analisi girano in parallelo (`core/parallel.Steps`, F1.3): i sottoprocessi (DOPE di
MODELLER, mkdssp, PROCHECK, ProSA) partono per primi e i calcoli Python (Ramachandran, clash, SASA) si sovrappongono
a loro. Tutti i processi passano da `JobContext.run` (budget di CPU del JobManager, annullamento, timeout), ognuno
con il proprio contesto figlio: le righe di log di un passo hanno il prefisso del passo (`[DOPE] …`). Il rapporto non
dipende dall'ordine in cui i passi finiscono: i risultati si leggono e gli avvisi si aggiungono nell'ordine della
versione seriale, che `analyze(parallel=False)` riproduce.

Nei job con molti calcoli in parallelo (pipeline su un server) i passi Python girano in un processo di appoggio
(core/offload.py, domain/qa_steps.py), fuori dal GIL del processo principale; gli esiti non cambiano.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
import threading
import time
from pathlib import Path
from typing import Optional

from ..core import offload
from ..core.errors import AppError, ToolUnavailable
from ..core.jobs import JobCancelled, JobContext
from ..core.parallel import Steps
from ..domain import (clashes, consensus, grading, ligands, perresidue, plddt, qa_steps, ramachandran, secondary,
                      structure)
from . import modeller, procheck, prosa, tools

# ASA massima teorica per residuo (Tien et al. 2013), per la SASA relativa.
MAX_ASA = qa_steps.MAX_ASA
# Attesa massima dei passi Python in un processo di appoggio, poi si calcolano in casa
OFFLOAD_TIMEOUT = 600
# Thread dell'analisi: uno per passo indipendente (mkdssp o DSSP integrato, DOPE, PROCHECK, ProSA, Ramachandran,
# clash, SASA), così nessun passo aspetta un thread libero. Non è un limite di CPU: i processi pesanti li governa il
# budget del JobManager.
STEP_THREADS = 7
DSSP_TIMEOUT = 600
# Ordine dei passi nella riga dei tempi del log (lo stesso della versione seriale)
TIMING_ORDER = ("lettura", "Ramachandran", "clash", "mkdssp", "DSSP integrato", "SASA", "DOPE", "procheck", "prosa")
TIMING_LABELS = {"procheck": "PROCHECK", "prosa": "ProSA"}

_dssp_versions: dict[str, int] = {}
_dssp_lock = threading.Lock()


def _dssp_input(structure_obj, folder: Path) -> Path:
    """Copia per mkdssp: mkdssp 4.x riconosce il formato PDB solo se il file inizia con HEADER e richiede CRYST1, quindi
    si scrive una copia pulita con le sole coordinate del primo modello."""
    coords = structure.pdb_text(structure_obj, keep_hetero=True)
    header = ("HEADER    BIOLOOM STRUCTURE                                                  \n"
              "CRYST1    1.000    1.000    1.000  90.00  90.00  90.00 P 1           1          \n")
    dest = folder / "dssp_input.pdb"
    dest.write_text(header + coords, encoding="utf-8")
    return dest


def _run(cmd: list[str], cwd: Path, ctx: Optional[JobContext], timeout: float) -> tuple[int, str]:
    """Processo esterno di un passo: con un job da ctx.run (budget, annullamento, timeout), senza job direttamente."""
    if ctx is not None:
        result = ctx.run(cmd, cwd=cwd, log_output=False, timeout=timeout)
        return result.returncode, result.output
    proc = subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True, errors="replace", timeout=timeout)
    return proc.returncode, proc.stdout + proc.stderr


def _dssp_major(exe: str, ctx: Optional[JobContext]) -> int:
    """Versione principale di mkdssp (una volta per eseguibile): dalla 4 l'uscita classica va chiesta con
    `--output-format=dssp`, come fa Biopython."""
    with _dssp_lock:
        if exe in _dssp_versions:
            return _dssp_versions[exe]
    try:
        _code, text = _run([exe, "--version"], Path(tempfile.gettempdir()), ctx, 60)
        match = re.search(r"(\d+)\.\d+", text)
        major = int(match.group(1)) if match else 4
    except JobCancelled:
        raise
    except (AppError, OSError, subprocess.SubprocessError):
        return 4  # non memorizzata: si riprova al prossimo calcolo
    with _dssp_lock:
        _dssp_versions[exe] = major
    return major


def _run_mkdssp(exe: str, model, input_pdb: Path, ctx: Optional[JobContext]) -> Optional[list[dict]]:
    """mkdssp sulla copia `_dssp_input`, lettura dell'uscita con Biopython; None se non riesce (DSSP integrato).

    Lo stesso comando di `Bio.PDB.DSSP`, con l'uscita su file invece che sullo standard output (nessuna riga di avviso
    mescolata ai dati). La lettura (`DSSP(..., file_type="DSSP")`) lavora su una copia del modello: Biopython vi
    registra i risultati e seleziona le varianti dei residui disordinati, e il modello condiviso con gli altri passi
    paralleli non deve cambiare.
    """
    from Bio.PDB import DSSP

    out_path = input_pdb.with_suffix(".dssp")
    try:
        if _dssp_major(exe, ctx) >= 4:
            cmd = [exe, "--output-format=dssp", input_pdb.name, out_path.name]
        else:
            cmd = [exe, input_pdb.name, out_path.name]
        code, _output = _run(cmd, input_pdb.parent, ctx, DSSP_TIMEOUT)
        if code != 0 or not out_path.is_file() or out_path.stat().st_size == 0:
            return None
        dssp = DSSP(model.copy(), str(out_path), file_type="DSSP")
        out = []
        for key in dssp.keys():
            chain, res_id = key
            row = dssp[key]
            code = row[2] if row[2] != "-" else "-"
            out.append({"chain": chain, "resnum": f"{res_id[1]}{res_id[2].strip()}", "ss": code,
                        "ss3": secondary.EIGHT_TO_THREE.get(code, "C"), "rsa": round(float(row[3]), 3)
                        if isinstance(row[3], (int, float)) else None})
        return out
    except JobCancelled:
        raise
    except Exception:
        return None


def _dssp_external(model, structure_obj, ctx: Optional[JobContext] = None) -> Optional[list[dict]]:
    """Struttura secondaria e SASA relativa di mkdssp; None se mkdssp manca o non riesce."""
    exe = tools.find("dssp")
    if not exe:
        return None
    folder = Path(tempfile.mkdtemp(prefix="bioloom-dssp-"))
    try:
        try:
            input_pdb = _dssp_input(structure_obj, folder)
        except Exception:
            return None
        return _run_mkdssp(exe, model, input_pdb, ctx)
    finally:
        shutil.rmtree(folder, ignore_errors=True)


# SASA relativa (anche nei processi di appoggio, domain/qa_steps.py)
_sasa = qa_steps.relative_sasa


def _timings_line(seconds: dict[str, float], total: float, parallel: bool) -> str:
    """"Analisi di qualità in 1.07 s (passi in parallelo: lettura 0.01, …, DOPE 0.95 s)", per la profilazione."""
    parts = [f"{TIMING_LABELS.get(k, k)} {seconds[k]:.2f}" for k in TIMING_ORDER if k in seconds]
    mode = "passi in parallelo" if parallel else "passi in serie"
    return f"Analisi di qualità in {total:.2f} s ({mode}: {', '.join(parts)} s)"


def analyze(path: Path, ctx: Optional[JobContext] = None, *, with_dope: bool = True,
            dope_workdir: Optional[Path] = None, with_procheck: bool = False, with_prosa: bool = False,
            parallel: bool = True) -> dict:
    """Pipeline completa di validazione. Senza `ctx` (o senza MODELLER) il DOPE viene omesso.

    PROCHECK e ProSA sono facoltativi: i loro file restano accanto al DOPE (cartelle `procheck/`, `prosa/`).
    Con `parallel` (predefinito) i passi indipendenti girano insieme; `parallel=False` li esegue uno dopo l'altro
    nell'ordine della versione seriale, con lo stesso rapporto. La struttura si legge una volta sola e i passi
    condividono il modello di Biopython in sola lettura (chi deve modificarlo, SASA e lettura di mkdssp, ne usa una
    copia). Con un job, il log riceve alla fine la durata di ogni passo.
    """

    def step(fraction: float, stage: str) -> None:
        if ctx:
            ctx.check()
            ctx.progress(fraction, stage)

    started = time.perf_counter()
    path = Path(path)
    step(0.05, "Lettura della struttura")
    st = structure.load_structure(path, path.stem)
    model = structure.first_model(st)
    is_cif = path.suffix.lower() in (".cif", ".mmcif")
    head = structure.read_text_head(path, 200_000 if is_cif else 2_000_000)
    header = structure.parse_cif_header(path) if is_cif else structure.parse_header(head)
    stats = structure.structure_stats(model)
    if stats["residues"] == 0:
        raise AppError("La struttura non contiene residui amminoacidici con C-alfa.")
    read_seconds = time.perf_counter() - started
    warnings: list[str] = []
    tools_dir = dope_workdir or (ctx.workdir if ctx is not None else None)
    external = (("procheck", with_procheck, "PROCHECK", procheck.run, 0.75),
                ("prosa", with_prosa, "ProSA II", prosa.run, 0.82))
    # copie per gli strumenti esterni (PDB da un mmCIF, ingresso di mkdssp), eliminate alla fine
    scratch = Path(tempfile.mkdtemp(prefix="bioloom-qa-"))
    try:
        pdb_for_tools: Optional[Path] = path
        if is_cif:
            try:
                structure.write_pdb(st, scratch / "structure.pdb", keep_hetero=True)
                pdb_for_tools = scratch / "structure.pdb"
            except AppError:
                pdb_for_tools = None
        dssp_exe = tools.find("dssp")
        dssp_input = None
        if dssp_exe:
            try:
                dssp_input = _dssp_input(st, scratch)
            except Exception:
                dssp_input = None  # come un mkdssp non riuscito: DSSP integrato

        with Steps(check=ctx.check if ctx is not None else None, parallel=parallel, workers=STEP_THREADS,
                   name="qualita") as steps:
            # prima i sottoprocessi, dal più lungo (il DOPE decide la durata dell'analisi); ognuno con il suo contesto
            # (prefisso nel log, cartella propria)
            with_dope = with_dope and ctx is not None
            if with_dope:
                dope_dir = dope_workdir or ctx.workdir / "dope"
                src = pdb_for_tools or path
                steps.start("DOPE", lambda: modeller.evaluate_dope(ctx.sub("DOPE", dope_dir), src, dope_dir))
            for key, enabled, label, runner, _fraction in external:
                if enabled and ctx is not None and tools_dir is not None and pdb_for_tools is not None:
                    steps.start(key, lambda runner=runner, key=key, label=label:
                                runner(pdb_for_tools, ctx.sub(label, tools_dir / key), tools_dir / key))
            if dssp_input is not None:
                steps.start("mkdssp", lambda: _run_mkdssp(dssp_exe, model, dssp_input,
                                                          ctx.sub("mkdssp", scratch) if ctx is not None else None))
            else:
                steps.start("DSSP integrato", lambda: secondary.assign(model))
            # nei job, con molti calcoli in parallelo, i passi Python girano in un processo di appoggio (fuori dal
            # GIL, core/offload.py); stessi risultati, e un passo non riuscito là si rifà qui
            remote = (offload.submit(qa_steps.python_steps, str(path), path.stem)
                      if parallel and ctx is not None else None)

            def pooled(key: str, local):
                def run():
                    if remote is not None:
                        try:
                            done = remote.result(timeout=OFFLOAD_TIMEOUT)
                        except Exception:
                            done = {}
                        if key in done:
                            return done[key]
                    return local()
                return run

            steps.start("Ramachandran", pooled("Ramachandran", lambda: ramachandran.analyze(model)))
            steps.start("clash", pooled("clash", lambda: clashes.analyze(model)))
            steps.start("SASA", pooled("SASA", lambda: _sasa(model)))

            # esiti nell'ordine della versione seriale: stesso rapporto, stessi avvisi nello stesso ordine
            step(0.15, "Angoli diedri e Ramachandran")
            rama = steps.result("Ramachandran")
            step(0.3, "Contatti sterici (clashscore)")
            clash = steps.result("clash")

            step(0.45, "Struttura secondaria")
            ss_rows = steps.result("mkdssp") if steps.started("mkdssp") else None
            ss_method = "mkdssp"
            if ss_rows is None:
                if not steps.started("DSSP integrato"):  # mkdssp non riuscito
                    steps.start("DSSP integrato", lambda: secondary.assign(model))
                ss_rows = steps.result("DSSP integrato")
                ss_method = "DSSP integrato BioLoom (Kabsch-Sander)"

            step(0.55, "Superficie accessibile (Shrake-Rupley)")
            sasa = steps.outcome("SASA")
            if isinstance(sasa.error, Exception):
                rsa = {}
                warnings.append(f"SASA non calcolata: {sasa.error}")
            elif sasa.error is not None:
                raise sasa.error
            else:
                rsa = sasa.value

            dope = None
            if with_dope:
                step(0.65, "Energia DOPE (MODELLER)")
                out = steps.outcome("DOPE")
                if isinstance(out.error, ToolUnavailable):
                    warnings.append("MODELLER non disponibile: profilo DOPE non calcolato.")
                elif isinstance(out.error, AppError):
                    warnings.append(f"DOPE non calcolato: {out.error.message.splitlines()[0]}")
                elif out.error is not None:
                    raise out.error
                else:
                    dope = out.value

            extra: dict[str, Optional[dict]] = {"procheck": None, "prosa": None}
            for key, enabled, label, _runner, fraction in external:
                if not enabled or ctx is None or tools_dir is None:
                    continue
                if pdb_for_tools is None:
                    warnings.append(f"{label}: la struttura non è convertibile in formato PDB.")
                    continue
                step(fraction, label)
                out = steps.outcome(key)
                if isinstance(out.error, ToolUnavailable):
                    warnings.append(f"{label} non disponibile: {out.error.message}")
                elif isinstance(out.error, AppError):
                    warnings.append(f"{label} non riuscito: {out.error.message.splitlines()[0]}")
                elif out.error is not None:
                    raise out.error
                else:
                    extra[key] = out.value
            timings = {"lettura": read_seconds, **steps.timings}
    finally:
        shutil.rmtree(scratch, ignore_errors=True)

    step(0.9, "Composizione del rapporto")
    ss_map = {(r["chain"], r["resnum"]): r for r in ss_rows}
    rama_map = {(p["chain"], p["resnum"]): p for p in rama["points"]}
    clash_count: dict[tuple[str, str], int] = {}
    for c in clash["clashes"]:
        for label in (c["atom1"], c["atom2"]):
            ch, num = label.split()[:2]
            clash_count[(ch, num)] = clash_count.get((ch, num), 0) + 1
    residues = []
    for chain in model:
        for res in chain:
            if not structure.is_amino_residue(res):
                continue
            key = (chain.id, structure._resid(res))
            heavy = [a.bfactor for a in res if a.element not in ("H", "D")]
            ss_row = ss_map.get(key, {})
            rp = rama_map.get(key)
            residues.append({
                "chain": chain.id, "resnum": key[1], "resname": res.get_resname(),
                "aa": structure.three_to_one(res.get_resname()),
                "ss": ss_row.get("ss", "-"), "ss3": ss_row.get("ss3", "C"),
                "rsa": ss_row.get("rsa") if ss_row.get("rsa") is not None else rsa.get(key),
                "bfactor": round(sum(heavy) / len(heavy), 2) if heavy else None,
                "phi": rp["phi"] if rp else None, "psi": rp["psi"] if rp else None,
                "rama": rp["region"] if rp else None, "clashes": clash_count.get(key, 0),
                "dope": None,
            })
    if dope and dope.get("profile"):
        profile = dope["profile"]
        if len(profile) == len(residues):
            for row, p in zip(residues, profile):
                row["dope"] = round(p["energy"], 4)
        else:
            warnings.append(f"Profilo DOPE ({len(profile)} residui) non allineabile alla struttura "
                            f"({len(residues)} residui): mostrato per indice.")
    bvals = [r["bfactor"] for r in residues if r["bfactor"] is not None]
    # pLDDT di AlphaFold o ESMFold nella colonna B-factor? (stesso criterio della libreria: domain/plddt.predictor)
    atom_bfactors = plddt.atom_values(model)
    bfactor_kind = perresidue.bfactor_kind(path.name, head, atom_bfactors)
    predicted_by = plddt.predictor(path.name, head, atom_bfactors) if bfactor_kind == "plddt" else None
    zdope = dope.get("zdope") if dope else None
    grade = grading.grade(rama["summary"]["outlier_pct"], clash["clashscore"], zdope)
    rsa_values = [r["rsa"] for r in residues if r["rsa"] is not None]
    report = {
        "structure": {"file": path.name, "title": header.get("title", ""), "method": header.get("method", ""),
                      "resolution": header.get("resolution"), **stats,
                      "chains_detail": structure.chain_summaries(model), "bfactor_kind": bfactor_kind,
                      **({"predictor": predicted_by} if predicted_by else {})},
        "grade": grade,
        "ramachandran": {"summary": rama["summary"], "points": rama["points"], "regions": rama["regions"]},
        "clashes": clash,
        "secondary_structure": {"method": ss_method, **secondary.summarize(ss_rows)},
        "sasa": {"method": "mkdssp" if ss_method == "mkdssp" else "Shrake-Rupley (Biopython)",
                 "mean_rsa": round(sum(rsa_values) / len(rsa_values), 3) if rsa_values else None,
                 "buried_pct": round(100 * sum(1 for v in rsa_values if v < 0.2) / len(rsa_values), 1)
                 if rsa_values else None},
        "bfactor": {"mean": round(sum(bvals) / len(bvals), 2) if bvals else None,
                    "max": max(bvals) if bvals else None},
        "dope": None if not dope else {"score": dope.get("dope"), "zscore": zdope,
                                       "profile": dope.get("profile", [])},
        "procheck": extra["procheck"],
        "prosa": extra["prosa"],
        "residues": residues,
        "warnings": warnings,
    }
    # ligandi, cofattori e metalli (F5.4): solo se la struttura ha gruppi eteroatomici non d'acqua; il voto non cambia.
    # Nella modellazione run_modeling la ripete con il confronto dei siti del template.
    try:
        checked = ligands.evaluate(model)
    except (ValueError, KeyError, IndexError) as exc:  # la verifica dei ligandi non ferma l'analisi
        checked = None
        warnings.append(f"Verifica dei ligandi non riuscita: {exc}")
    if checked is not None:
        report["ligands"] = checked
    # consenso con i metodi di questa analisi; QMEAN e GA341 si aggiungono dopo (run_modeling, rotta del consenso)
    report["consensus"] = consensus.compute(report)
    if ctx is not None:  # durata dei passi nel log, non nel rapporto (che non dipende dai tempi)
        ctx.log(_timings_line(timings, time.perf_counter() - started, steps.parallel))
    return report


def summary_metrics(report: dict) -> dict:
    """Metriche compatte usate nelle tabelle (modelli, pipeline, confronti)."""
    dope = report.get("dope") or {}
    procheck_rama = ((report.get("procheck") or {}).get("ramachandran") or {})
    return {
        "grade": report["grade"]["grade"], "grade_score": report["grade"]["score"],
        "rama_favored": report["ramachandran"]["summary"]["favored_pct"],
        "rama_outliers": report["ramachandran"]["summary"]["outlier_pct"],
        "clashscore": report["clashes"]["clashscore"],
        "helix_pct": report["secondary_structure"]["helix_pct"],
        "strand_pct": report["secondary_structure"]["strand_pct"],
        "dope": dope.get("score"), "zdope": dope.get("zscore"),
        "prosa_z": (report.get("prosa") or {}).get("zscore"),
        "procheck_core": procheck_rama.get("core"),
        "procheck_g_overall": ((report.get("procheck") or {}).get("g_factors") or {}).get("overall"),
        "residues": report["structure"]["residues"],
    }


def inject_remarks(pdb_path: Path, lines: list[str]) -> None:
    """Inserisce righe REMARK 999 di tracciabilità prima dei record di coordinate."""
    text = pdb_path.read_text(encoding="utf-8", errors="replace").splitlines()
    kept = [ln for ln in text if not (ln.startswith("REMARK 999") and "BIOLOOM" in ln)]
    remarks = [f"REMARK 999 BIOLOOM {ln}"[:80] for ln in lines]
    idx = next((i for i, ln in enumerate(kept) if ln.startswith(("ATOM", "HETATM", "MODEL"))), len(kept))
    pdb_path.write_text("\n".join(kept[:idx] + remarks + kept[idx:]) + "\n", encoding="utf-8")
