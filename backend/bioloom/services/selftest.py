"""Verifica dell'installazione: esegue ogni strumento su una struttura nota (ubiquitina, 1UBQ).

Disponibile come job ("Verifica installazione" nelle Impostazioni) e da riga di comando:
`python -m bioloom.selftest` (dentro il container: `docker compose exec bioloom python -m bioloom.selftest`).
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Callable

from ..core.errors import AppError, ToolUnavailable
from ..core.jobs import JobContext, JobManager
from ..domain import structure
from ..domain.sequences import SeqRecord
from ..engines import align, modeller, procheck, prosa, qmean_local, quality, search, tools

ASSET = Path(__file__).resolve().parents[1] / "assets" / "1UBQ.pdb"
UBQ = "MQIFVKTLTGKTITLEVEPSDTIENVKAKIQDKEGIPPDQQRLIFAGKQLEDGRTLSDYNIQKESTLHLVLRLRGG"
YEAST = "MQIFVKTLTGKTITLEVESSDTIDNVKSKIQDKEGIPPDQQRLIFAGKQLEDGRTLSDYNIQKESTLHLVLRLRGG"
NEDD8 = "MLIKVKTLTGKEIEIDIEPTDKVERIKERVEEKEGIPPQQQRLIYSGKQMNDEKTAADYKILGGSVLHLVLALRGG"


class SelfTestService:
    def __init__(self, jobs: JobManager, databases_dir: Path) -> None:
        self.databases_dir = databases_dir
        jobs.register("selftest", self.handle)

    def handle(self, ctx: JobContext, p: dict) -> dict:
        checks: list[tuple[str, Callable[[JobContext, Path], str]]] = [
            ("MODELLER", self._modeller), ("DSSP", self._dssp), ("BLAST+", self._blast),
            ("MMseqs2", self._mmseqs), ("Clustal Omega", lambda c, w: self._msa(c, "clustalo")),
            ("MAFFT", lambda c, w: self._msa(c, "mafft")), ("MUSCLE", lambda c, w: self._msa(c, "muscle")),
            ("TM-align", lambda c, w: self._structural(c, "tmalign")),
            ("US-align", lambda c, w: self._structural(c, "usalign")),
            ("PROCHECK", self._procheck), ("ProSA II", self._prosa), ("QMEAN locale", self._qmean),
        ]
        results = []
        for n, (name, fn) in enumerate(checks):
            ctx.check()
            ctx.progress(n / len(checks), f"Verifica di {name}")
            workdir = ctx.workdir / name.split()[0].lower().replace("+", "p")
            workdir.mkdir(parents=True, exist_ok=True)
            start = time.monotonic()
            try:
                detail = fn(ctx, workdir)
                status = "ok"
            except ToolUnavailable as exc:
                status, detail = "missing", exc.message
            except AppError as exc:
                status, detail = "error", exc.message.splitlines()[0]
            except Exception as exc:  # la verifica non deve fermarsi al primo guasto
                status, detail = "error", f"{type(exc).__name__}: {exc}"
            results.append({"tool": name, "status": status, "detail": detail,
                            "seconds": round(time.monotonic() - start, 2)})
            ctx.log(f"{name:14s} {status:8s} {detail}")
        counts = {s: sum(1 for r in results if r["status"] == s) for s in ("ok", "missing", "error")}
        return {"checks": results, "counts": counts}

    # ------------------------------------------------------------------ singole verifiche
    @staticmethod
    def _modeller(ctx: JobContext, workdir: Path) -> str:
        info = tools.detect_modeller(ctx.settings.modeller_python, force=True)
        if not info["available"]:
            raise ToolUnavailable(next((a["error"] for a in info["attempts"] if a.get("error")), "non disponibile"))
        dope = modeller.evaluate_dope(ctx, ASSET, workdir)
        return f"versione {info['version']}, DOPE di 1UBQ {dope['dope']:.0f} (z {dope['zdope']:.2f})"

    @staticmethod
    def _dssp(ctx: JobContext, workdir: Path) -> str:
        if not tools.find("dssp"):
            raise ToolUnavailable("mkdssp non installato (si usa il DSSP integrato).")
        st = structure.load_structure(ASSET, "1UBQ")
        rows = quality._dssp_external(structure.first_model(st), st)
        if not rows:
            raise AppError("mkdssp non ha prodotto l'assegnazione.")
        helix = sum(1 for r in rows if r["ss3"] == "H")
        return f"{len(rows)} residui assegnati, {helix} in elica"

    def _blast(self, ctx: JobContext, workdir: Path) -> str:
        if not tools.find("blastp"):
            raise ToolUnavailable("blastp non installato.")
        if search.local_database(self.databases_dir) is None:
            raise ToolUnavailable("programma presente, database pdbaa assente (Impostazioni → Database).")
        sub = ctx.sub("blast", workdir)
        res = search.search(sub, YEAST, engine="local", evalue=1e-5, max_hits=5, databases_dir=self.databases_dir)
        best = search.best_template(res["hits"])
        return f"{len(res['hits'])} template, migliore {best['template_id'] if best else '-'}"

    def _mmseqs(self, ctx: JobContext, workdir: Path) -> str:
        if not tools.find("mmseqs"):
            raise ToolUnavailable("mmseqs non installato.")
        if search.mmseqs_database(self.databases_dir) is None:
            raise ToolUnavailable("programma presente, indice MMseqs2 non ancora preparato.")
        sub = ctx.sub("mmseqs", workdir)
        res = search.search(sub, YEAST, engine="mmseqs", evalue=1e-5, max_hits=5, databases_dir=self.databases_dir)
        best = search.best_template(res["hits"])
        return f"{len(res['hits'])} template, migliore {best['template_id'] if best else '-'}"

    @staticmethod
    def _msa(ctx: JobContext, tool: str) -> str:
        if not tools.find(tool):
            raise ToolUnavailable(f"{tools.TOOLS[tool]['label']} non installato.")
        sub = ctx.sub(tool, ctx.workdir / tool)
        res = align.multiple([SeqRecord("ubq", UBQ), SeqRecord("yeast", YEAST), SeqRecord("nedd8", NEDD8)],
                             sub, tool=tool)
        return f"3 sequenze allineate in {len(res['aligned'][0])} colonne"

    @staticmethod
    def _structural(ctx: JobContext, tool: str) -> str:
        if not tools.find(tool):
            raise ToolUnavailable(f"{tools.TOOLS[tool]['label']} non installato.")
        sub = ctx.sub(tool, ctx.workdir / tool)
        res = align.structural(ASSET, "A", ASSET, "A", sub, tool=tool, names=("1UBQ", "1UBQ"))
        return f"1UBQ su se stessa: TM-score {res.get('tm_score')}, RMSD {res.get('rmsd')}"

    @staticmethod
    def _procheck(ctx: JobContext, workdir: Path) -> str:
        data = procheck.run(ASSET, ctx, workdir)
        rama = data["ramachandran"]
        return f"core {rama['core']}%, disallowed {rama['disallowed']}%, G-factor {data['g_factors']['overall']}"

    @staticmethod
    def _prosa(ctx: JobContext, workdir: Path) -> str:
        data = prosa.run(ASSET, ctx, workdir)
        return f"z-score 1UBQ {data['zscore']}"

    @staticmethod
    def _qmean(ctx: JobContext, workdir: Path) -> str:
        info = qmean_local.status(ctx.settings.qmean_uniclust, force=True)
        if not info["available"]:
            raise ToolUnavailable(info.get("message") or "non disponibile")
        qmtl = (info.get("qmtl") or {}).get("version")
        found = len(info.get("databases") or [])
        return (f"servizio attivo, database {info['uniclust'].get('prefix')}"
                f"{' (' + str(found) + ' versioni trovate)' if found > 1 else ''}, "
                f"{'QMTL ' + qmtl + ' (QMEANDisCo)' if qmtl else 'senza QMTL (metodo QMEAN)'}")
