"""Analisi di qualità di una struttura: metriche globali e tabella unificata per residuo."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Optional

from ..core.errors import AppError, ToolUnavailable
from ..core.jobs import JobContext
from ..domain import clashes, consensus, grading, perresidue, plddt, ramachandran, secondary, structure
from . import modeller, procheck, prosa, tools

# ASA massima teorica per residuo (Tien et al. 2013), per la SASA relativa.
MAX_ASA = {"A": 129, "R": 274, "N": 195, "D": 193, "C": 167, "Q": 225, "E": 223, "G": 104, "H": 224, "I": 197,
           "L": 201, "K": 236, "M": 224, "F": 240, "P": 159, "S": 155, "T": 172, "W": 285, "Y": 263, "V": 174}


def _dssp_external(model, structure_obj) -> Optional[list[dict]]:
    exe = tools.find("dssp")
    if not exe:
        return None
    from Bio.PDB import DSSP

    fd, tmp = tempfile.mkstemp(suffix=".pdb")
    os.close(fd)
    try:
        # mkdssp 4.x riconosce il formato PDB solo se il file inizia con HEADER e richiede CRYST1:
        # si scrive una copia pulita con le sole coordinate del primo modello.
        coords = structure.pdb_text(structure_obj, keep_hetero=True)
        header = ("HEADER    BIOLOOM STRUCTURE                                                  \n"
                  "CRYST1    1.000    1.000    1.000  90.00  90.00  90.00 P 1           1          \n")
        Path(tmp).write_text(header + coords, encoding="utf-8")
        dssp = DSSP(model, tmp, dssp=exe)
        out = []
        for key in dssp.keys():
            chain, res_id = key
            row = dssp[key]
            code = row[2] if row[2] != "-" else "-"
            out.append({"chain": chain, "resnum": f"{res_id[1]}{res_id[2].strip()}", "ss": code,
                        "ss3": secondary.EIGHT_TO_THREE.get(code, "C"), "rsa": round(float(row[3]), 3)
                        if isinstance(row[3], (int, float)) else None})
        return out
    except Exception:
        return None
    finally:
        os.remove(tmp)


def _sasa(model) -> dict[tuple[str, str], float]:
    from Bio.PDB.SASA import ShrakeRupley

    # Le acque cristallografiche coprirebbero la superficie: si calcola su una copia senza solvente.
    model = model.copy()
    for chain in model:
        for res in list(chain):
            if res.id[0] == "W" or res.get_resname().strip() in structure.WATER_NAMES:
                chain.detach_child(res.id)
    sr = ShrakeRupley()
    sr.compute(model, level="R")
    out = {}
    for chain in model:
        for res in chain:
            if structure.is_amino_residue(res):
                aa = structure.three_to_one(res.get_resname())
                max_asa = MAX_ASA.get(aa)
                if max_asa:
                    out[(chain.id, structure._resid(res))] = round(min(1.5, res.sasa / max_asa), 3)
    return out


def analyze(path: Path, ctx: Optional[JobContext] = None, *, with_dope: bool = True,
            dope_workdir: Optional[Path] = None, with_procheck: bool = False, with_prosa: bool = False) -> dict:
    """Pipeline completa di validazione. Senza `ctx` (o senza MODELLER) il DOPE viene omesso.

    PROCHECK e ProSA sono facoltativi: i loro file restano accanto al DOPE (cartelle `procheck/`, `prosa/`).
    """

    def step(fraction: float, stage: str) -> None:
        if ctx:
            ctx.check()
            ctx.progress(fraction, stage)

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
    warnings: list[str] = []

    step(0.15, "Angoli diedri e Ramachandran")
    rama = ramachandran.analyze(model)
    step(0.3, "Contatti sterici (clashscore)")
    clash = clashes.analyze(model)

    step(0.45, "Struttura secondaria")
    pdb_for_tools = path
    tmp_pdb = None
    if path.suffix.lower() in (".cif", ".mmcif"):
        fd, tmp_name = tempfile.mkstemp(suffix=".pdb")
        os.close(fd)
        tmp_pdb = Path(tmp_name)
        try:
            structure.write_pdb(st, tmp_pdb, keep_hetero=True)
            pdb_for_tools = tmp_pdb
        except AppError:
            pdb_for_tools = None
    try:
        ss_rows = _dssp_external(model, st)
        ss_method = "mkdssp"
        if ss_rows is None:
            ss_rows = secondary.assign(model)
            ss_method = "DSSP integrato BioLoom (Kabsch-Sander)"

        step(0.55, "Superficie accessibile (Shrake-Rupley)")
        try:
            rsa = _sasa(model)
        except Exception as exc:
            rsa = {}
            warnings.append(f"SASA non calcolata: {exc}")

        dope = None
        if with_dope and ctx is not None:
            step(0.65, "Energia DOPE (MODELLER)")
            try:
                src = pdb_for_tools or path
                dope = modeller.evaluate_dope(ctx, src, dope_workdir or ctx.workdir / "dope")
            except ToolUnavailable:
                warnings.append("MODELLER non disponibile: profilo DOPE non calcolato.")
            except AppError as exc:
                warnings.append(f"DOPE non calcolato: {exc.message.splitlines()[0]}")

        extra: dict[str, Optional[dict]] = {"procheck": None, "prosa": None}
        tools_dir = dope_workdir or (ctx.workdir if ctx is not None else None)
        external = (("procheck", with_procheck, "PROCHECK", procheck.run, 0.75),
                    ("prosa", with_prosa, "ProSA II", prosa.run, 0.82))
        for key, enabled, label, runner, fraction in external:
            if not enabled or ctx is None or tools_dir is None:
                continue
            if pdb_for_tools is None:
                warnings.append(f"{label}: la struttura non è convertibile in formato PDB.")
                continue
            step(fraction, label)
            try:
                extra[key] = runner(pdb_for_tools, ctx, tools_dir / key)
            except ToolUnavailable as exc:
                warnings.append(f"{label} non disponibile: {exc.message}")
            except AppError as exc:
                warnings.append(f"{label} non riuscito: {exc.message.splitlines()[0]}")
    finally:
        if tmp_pdb is not None:
            tmp_pdb.unlink(missing_ok=True)

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
    # pLDDT di AlphaFold nella colonna B-factor? (stesso criterio della libreria: domain/plddt.is_alphafold_model)
    bfactor_kind = perresidue.bfactor_kind(path.name, head, plddt.atom_values(model))
    zdope = dope.get("zdope") if dope else None
    grade = grading.grade(rama["summary"]["outlier_pct"], clash["clashscore"], zdope)
    rsa_values = [r["rsa"] for r in residues if r["rsa"] is not None]
    report = {
        "structure": {"file": path.name, "title": header.get("title", ""), "method": header.get("method", ""),
                      "resolution": header.get("resolution"), **stats,
                      "chains_detail": structure.chain_summaries(model), "bfactor_kind": bfactor_kind},
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
    # consenso con i metodi di questa analisi; QMEAN e GA341 si aggiungono dopo (run_modeling, rotta del consenso)
    report["consensus"] = consensus.compute(report)
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
