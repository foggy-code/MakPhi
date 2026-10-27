"""Rifinitura energetica (F9.1): job `refine` su un modello di un job o su una struttura, con il confronto prima/dopo.

1. **Rifinitura** (`engines/refine.py`, protocollo in `domain/refinement.py`): copia pulita, PDBFixer, AMBER ff14SB con
   OBC2, minimizzazione con restrizioni sulla catena principale, dinamica facoltativa; il modello rifinito è
   `<nome>_refined.pdb` nella cartella del job, con le righe `REMARK 999 MAKPHI` del protocollo e delle energie.
2. **Qualità prima e dopo** (`engines/quality.analyze`, in parallelo, con DOPE se c'è MODELLER; senza PROCHECK e
   ProSA): "prima" è la copia pulita data a OpenMM (stessi residui, senza ligandi né idrogeni), così il confronto
   riguarda gli stessi atomi; il rapporto del rifinito è salvato accanto al modello (`<nome>_refined.pdb.quality.json`)
   e si apre nella pagina Qualità come quello di un modello di MODELLER.
3. **Confronto**: righe di `refinement.compare` (voto, Ramachandran, clashscore, DOPE, zDOPE), energia del campo di
   forza prima e dopo, RMSD della catena principale (`refinement.backbone_rmsd`).

**Priorità** 1 (`core/jobs.PRIORITY`, come `model`): minuti, non secondi. Pool "cpu"; il processo di OpenMM prende da
1 a processori − 1 slot del budget di CPU.
"""

from __future__ import annotations

import time
from functools import partial
from pathlib import Path

from ..core import cache
from ..core.errors import AppError
from ..core.jobs import JobContext, JobManager
from ..core.parallel import Steps
from ..core.storage import atomic_write_json
from ..domain import refinement, sequences, structure
from ..engines import quality, refine
from .compute import ComputeService

KIND = "refine"


def _quality(path: Path, ctx: JobContext, folder: Path) -> dict:
    return quality.analyze(path, ctx, with_dope=True, dope_workdir=folder)


class RefineService:
    def __init__(self, jobs: JobManager, compute: ComputeService) -> None:
        self.jobs = jobs
        self.compute = compute

    def register(self) -> None:
        self.jobs.register(KIND, self.handle)

    def validate(self, params: dict) -> None:
        """Errori immediati, prima di accodare il job: struttura e durata della dinamica."""
        self.compute.resolve_structure(params.get("source") or {})
        try:
            refinement.validate_md(params.get("md_ps") or 0.0)
        except ValueError as exc:
            raise AppError(f"Durata della dinamica non valida: {exc}.", code="invalid_refine")

    def handle(self, ctx: JobContext, p: dict) -> dict:
        source = p.get("source") or {}
        path, label = self.compute.resolve_structure(source)
        fingerprint = cache.content_hash(path)
        md_ps = refinement.validate_md(p.get("md_ps") or 0.0)
        keep_h = bool(p.get("hydrogens", False))
        started = time.perf_counter()
        output = f"{sequences.sanitize_code(label, default='model')}_refined.pdb"
        ctx.progress(0.02, "Preparazione della struttura")
        data = refine.run(path, ctx, ctx.workdir, output=output, md_ps=md_ps, keep_hydrogens=keep_h,
                          progress=lambda f, stage: ctx.progress(0.03 + 0.67 * f, stage))
        for w in data["warnings"]:
            ctx.log(f"Avviso: {w}")
        refined = ctx.workdir / output
        before_path = ctx.workdir / refine.INPUT

        ctx.progress(0.72, "Analisi di qualità prima e dopo la rifinitura")
        with Steps(check=ctx.check, parallel=True, workers=2, name="rifinitura") as steps:
            steps.start("prima", partial(_quality, before_path, ctx.sub("prima", ctx.workdir / "qa_prima"),
                                         ctx.workdir / "qa_prima"))
            steps.start("dopo", partial(_quality, refined, ctx.sub("dopo", ctx.workdir / "qa_dopo"),
                                        ctx.workdir / "qa_dopo"))
            before = steps.result("prima")
            after = steps.result("dopo")
        ctx.progress(0.96, "Confronto prima/dopo")
        rmsd = refinement.backbone_rmsd(structure.first_model(structure.load_structure(before_path, "prima")),
                                        structure.first_model(structure.load_structure(refined, "dopo")))
        energy = refinement.energy_change(data["energy"])
        metrics_before = quality.summary_metrics(before)
        metrics_after = quality.summary_metrics(after)
        rows = refinement.compare(metrics_before, metrics_after)
        atomic_write_json(ctx.workdir / f"{output}.quality.json", after)
        quality.inject_remarks(refined, [
            f"REFINED FROM {label} OPENMM {data.get('openmm')} {refinement.FORCEFIELD.split(' (')[0]} OBC2",
            f"RESTRAINT BACKBONE N CA C O K {refinement.RESTRAINT_KCAL_A2:g} KCAL/MOL/A2"
            + (f" MD {md_ps:g} PS {refinement.MD_TEMPERATURE:g} K" if md_ps else " MD OFF"),
            f"ENERGY KJ/MOL BEFORE {energy['before']} AFTER {energy['after']}",
            f"BACKBONE RMSD {rmsd['rmsd']} A ({rmsd['atoms']} ATOMS)",
            f"GRADE {metrics_after['grade']} ({metrics_after['grade_score']}) RAMA_FAVORED "
            f"{metrics_after['rama_favored']} CLASHSCORE {metrics_after['clashscore']}",
        ])
        ctx.log(f"Rifinitura: energia {energy['before']} → {energy['after']} kJ/mol, RMSD della catena principale "
                f"{rmsd['rmsd']} Å; clashscore {metrics_before['clashscore']} → {metrics_after['clashscore']}, "
                f"Ramachandran favorite {metrics_before['rama_favored']} → {metrics_after['rama_favored']}%")
        ctx.log(f"Rifinitura completata in {time.perf_counter() - started:.1f} s")
        return {
            "source": source, "label": label, "source_sha256": fingerprint, "refined_file": output,
            "input_file": refine.INPUT, "protocol": refinement.protocol(md_ps, keep_h),
            "openmm": {k: data.get(k) for k in ("openmm", "platform", "threads", "atoms", "nonbonded",
                                                "restrained_atoms", "md_steps", "timings")},
            "added": {"heavy_atoms": data.get("heavy_atoms_added"), "terminal_atoms": data.get("terminal_atoms_added"),
                      "hydrogens": data.get("hydrogens_added"), "nonstandard": data.get("nonstandard") or []},
            "input": data["input"], "energy": {**energy, "components": data["energy"]},
            "rmsd": rmsd, "quality": {"before": metrics_before, "after": metrics_after}, "comparison": rows,
            "warnings": [*data["warnings"], *_analysis_warnings(before, after)],
        }


def _analysis_warnings(before: dict, after: dict) -> list[str]:
    """Avvisi delle due analisi di qualità: una volta sola quelli comuni (es. DOPE senza MODELLER), con "prima:" o
    "dopo:" quelli di una sola."""
    a = list(dict.fromkeys(before.get("warnings") or []))
    b = list(dict.fromkeys(after.get("warnings") or []))
    common = [w for w in a if w in b]
    return [*common, *(f"Prima: {w}" for w in a if w not in common), *(f"Dopo: {w}" for w in b if w not in common)]
