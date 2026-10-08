"""Benchmark della pipeline M × N (sequenze × template) con MODELLER reale, per dimensionare una macchina.

Gira dentro l'immagine di test (codice montato in /src, come scripts/test.sh) e misura, per un numero di elementi
in parallelo scelto, la durata reale della pipeline, il tempo di CPU, il picco di memoria del container (cgroup v2),
lo spazio su disco dei risultati e del database. Ogni misura va lanciata in un container nuovo: il picco di memoria
del cgroup vale per tutta la vita del container.

    python /src/scripts/benchmark_mxn.py --parallel 2 --sequences 2 --templates 6 --out /out/p2.json

Esempio completo (dalla cartella di BioLoom, Linux; chiave di MODELLER in data/licenses/modeller.key; la licenza ProSA,
se c'è, si legge da /hostdata/licenses/prosa):

    docker run --rm --init -e MODELLER_KEY="$(cat data/licenses/modeller.key)" -e PYTHONPATH=/src/backend       -v "$PWD/programma:/src:ro" -v "$PWD/data:/hostdata:ro" -v "$PWD/bench_out:/out"       --entrypoint sh bioloom:4.1.0 -c 'mkdir -p /scratch/tmp /scratch/home && exec python       /src/scripts/benchmark_mxn.py "$@"' sh --parallel 16 --sequences 8 --templates 6 --extended       --template-file /hostdata/templates/3IU0.pdb --template-file /hostdata/templates/1IU4.pdb       --sequence MYTRSK... --out /out/p16.json

Per una stima affidabile: prima `--parallel 1` (CPU per coppia su un core), poi il parallelismo previsto con almeno
due coppie per corsia (`--sequences` × `--templates` ≥ 2 × `--parallel`). Nel risultato `main_process_cpu_per_pair_s`
è la CPU del processo di BioLoom per coppia (la parte limitata dal GIL), `cpu_per_pair_s` quella totale.

Template: copie di file PDB locali (--template-file, ripetibile) caricate con identificativi diversi (T1, T2, …),
così le coppie sono tutte distinte. Sequenze: la sequenza di riferimento con il 5 % di mutazioni casuali (seme fisso).
"""

from __future__ import annotations

import argparse
import json
import os
import random
import shutil
import tempfile
import threading
import time
from pathlib import Path

from fastapi.testclient import TestClient

from bioloom.app import create_app
from bioloom.config import Paths

AA = "ACDEFGHIKLMNPQRSTVWY"
CG = Path("/sys/fs/cgroup")


def cg_read(name: str) -> str:
    try:
        return (CG / name).read_text()
    except OSError:
        return ""


def cpu_usec() -> int:
    for line in cg_read("cpu.stat").splitlines():
        if line.startswith("usage_usec"):
            return int(line.split()[1])
    return 0


def du(path: Path) -> int:
    return sum(f.stat().st_size for f in path.rglob("*") if f.is_file()) if path.exists() else 0


def db_size(data: Path) -> int:
    return sum(f.stat().st_size for f in data.glob("bioloom.db*"))


def by_ext(path: Path) -> dict:
    sizes: dict = {}
    for f in path.rglob("*"):
        if f.is_file():
            key = "".join(f.suffixes[-2:]) or f.name
            sizes[key] = sizes.get(key, 0) + f.stat().st_size
    return {k: round(v / 1024) for k, v in sorted(sizes.items(), key=lambda kv: -kv[1])[:12]}


def variants(seq: str, n: int, rate: float = 0.05, seed: int = 7) -> list[str]:
    rng = random.Random(seed)
    out = []
    for _ in range(n):
        s = list(seq)
        for i in rng.sample(range(len(s)), int(len(s) * rate)):
            s[i] = rng.choice(AA.replace(s[i], ""))
        out.append("".join(s))
    return out


class Sampler(threading.Thread):
    """Memoria del container ogni secondo (memory.current): media e massimo durante la pipeline."""

    def __init__(self) -> None:
        super().__init__(daemon=True)
        self.values: list[int] = []
        self.stop = threading.Event()

    def run(self) -> None:
        while not self.stop.wait(1.0):
            v = cg_read("memory.current").strip()
            if v.isdigit():
                self.values.append(int(v))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--parallel", type=int, required=True)
    ap.add_argument("--sequences", type=int, default=0, help="varianti della sequenza intera (senza --lengths)")
    ap.add_argument("--lengths", default="", help="lunghezze delle sequenze, separate da virgole: ogni sequenza è la "
                                                  "parte C-terminale della sequenza di riferimento, lunga quanto chiesto")
    ap.add_argument("--templates", type=int, default=6)
    ap.add_argument("--template-file", action="append", required=True)
    ap.add_argument("--sequence", required=True, help="sequenza di riferimento")
    ap.add_argument("--models", type=int, default=2)
    ap.add_argument("--refinement", default="very_fast")
    ap.add_argument("--extended", action="store_true", help="qualità estesa (PROCHECK, ProSA)")
    ap.add_argument("--no-quality", action="store_true")
    ap.add_argument("--out", required=True)
    a = ap.parse_args()

    root = Path(tempfile.mkdtemp(prefix="bench-", dir="/scratch"))
    data = root / "data"
    (data / "licenses").mkdir(parents=True)
    prosa_src = Path("/hostdata/licenses/prosa")
    if prosa_src.exists():
        shutil.copytree(prosa_src, data / "licenses" / "prosa")
    (data / "settings.json").write_text(json.dumps({"max_workers": a.parallel, "compute_cache": False,
                                                    "job_timeout_minutes": 240}))
    app = create_app(Paths(data, scratch_dir=root / "scratch", local_cache_dir=root / "lc"), frontend_dist=None)
    with TestClient(app) as client:
        files = [Path(f) for f in a.template_file]
        templates = []
        for i in range(a.templates):
            src = files[i % len(files)]
            tid = f"T{i + 1}"
            with open(src, "rb") as fh:
                r = client.post("/api/templates/upload", files={"file": (f"{tid}.pdb", fh, "chemical/x-pdb")},
                                data={"template_id": tid})
            assert r.status_code == 200, r.text
            templates.append({"id": r.json().get("id", tid), "chains": ["A"], "hetatm": False})
        if a.lengths:  # una sequenza per lunghezza, frammento C-terminale (non oltre la sequenza intera)
            lengths = [min(int(x), len(a.sequence)) for x in a.lengths.split(",") if x.strip()]
            seqs = [variants(a.sequence[-n:], 1, seed=7 + i)[0] for i, n in enumerate(lengths)]
            a.sequences = len(seqs)
        else:
            seqs = variants(a.sequence, a.sequences)
        fasta = "".join(f">s{i + 1}\n{s}\n" for i, s in enumerate(seqs))
        body = {"fasta": fasta, "template_mode": "fixed", "fixed_combination": "each", "templates": templates,
                "options": {"models": a.models, "refinement": a.refinement}, "quality": not a.no_quality,
                "extended_quality": a.extended, "parallel": a.parallel, "use_cache": False}
        db_before = db_size(data)
        sampler = Sampler()
        sampler.start()
        cpu0, t0, own0 = cpu_usec(), time.time(), time.process_time()
        r = client.post("/api/pipeline", json=body)
        assert r.status_code == 200, r.text
        job_id = r.json()["id"]
        while True:
            job = client.get(f"/api/jobs/{job_id}").json()
            if job["status"] in ("completed", "failed", "cancelled"):
                break
            time.sleep(2)
        wall, cpu = time.time() - t0, (cpu_usec() - cpu0) / 1e6
        own = time.process_time() - own0  # CPU del processo di BioLoom (Python, GIL), senza i sottoprocessi
        sampler.stop.set()
        items = client.get(f"/api/pipeline/{job_id}/items?size=500").json()["items"]
        from datetime import datetime

        jobdir = data / "jobs" / job_id
        durations, per_item = [], []
        for it in items:
            seconds = None
            if it.get("started_at") and it.get("finished_at"):
                seconds = (datetime.fromisoformat(it["finished_at"]) -
                           datetime.fromisoformat(it["started_at"])).total_seconds()
                durations.append(seconds)
            folder = jobdir / "items" / f"{it['idx']:04d}_{it['name']}"
            per_item.append({"name": it["name"], "length": it.get("length"), "status": it["status"],
                             "seconds": seconds, "bytes": du(folder)})
        zip_size = 0
        z = client.get(f"/api/pipeline/{job_id}/export.zip")
        if z.status_code == 200:
            zip_size = len(z.content)
        n = len(items)
        result = {
            "parallel": a.parallel, "sequences": a.sequences, "templates": a.templates, "pairs": n,
            "length": len(a.sequence), "lengths": [len(x) for x in seqs], "models": a.models, "refinement": a.refinement, "extended": a.extended,
            "quality": not a.no_quality, "status": job["status"],
            "completed": sum(1 for it in items if it["status"] == "completed"),
            "wall_s": round(wall, 1), "cpu_s": round(cpu, 1), "cpu_per_pair_s": round(cpu / max(n, 1), 1),
            "main_process_cpu_per_pair_s": round(own / max(n, 1), 2),
            "wall_per_pair_s": round(wall / max(n, 1), 1),
            "pairs_per_hour": round(n / wall * 3600, 1),
            "avg_cores_busy": round(cpu / wall, 2),
            "item_s_median": sorted(durations)[len(durations) // 2] if durations else None,
            "mem_peak_mb": round(int(cg_read("memory.peak").strip() or 0) / 2**20),
            "mem_sampled_max_mb": round(max(sampler.values, default=0) / 2**20),
            "mem_sampled_avg_mb": round(sum(sampler.values) / max(len(sampler.values), 1) / 2**20),
            "results_bytes": du(jobdir), "results_per_pair_kb": round(du(jobdir) / max(n, 1) / 1024, 1),
            "zip_bytes": zip_size,
            "db_growth_bytes": db_size(data) - db_before,
            "results_by_ext_kb": by_ext(jobdir),
            "scratch_bytes_after": du(root / "scratch"),
            "nproc": os.cpu_count(),
            "items": per_item,
        }
        print(json.dumps(result, indent=1))
        Path(a.out).write_text(json.dumps(result, indent=1))


if __name__ == "__main__":
    main()
