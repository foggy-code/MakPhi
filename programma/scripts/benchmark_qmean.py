"""QMEAN locale con calcoli contemporanei su sequenze tutte diverse (dati grezzi, punto 23 dei lavori aperti).

Dopo `0aa292a` BioLoom usa tutti i posti del worker QMEAN (QMEAN_PARALLEL) e fa una sola ricerca HHblits per
sequenza: misurare con la stessa sequenza ripetuta favorirebbe la cache dei profili. Qui ogni calcolo ha una
sequenza nuova (mutazioni casuali con un seme che dipende dall'ora), quindi ogni calcolo fa la sua ricerca HHblits.

Gira dentro l'immagine di BioLoom con il codice montato in /src, nella rete del container `bioloom` (dove il worker
QMEAN risponde, come per BioLoom stesso):

    podman run --network container:bioloom ... python /src/scripts/benchmark_qmean.py \
        --levels 1,8,16,32 --length 300 --template-file /hostdata/templates/3IU0.pdb --out /out/qmean.json

1. costruisce con MODELLER tanti modelli quanti la somma dei livelli (un modello "fast" per sequenza, in parallelo);
2. per ogni livello c invia insieme c modelli mai valutati e aspetta che finiscano tutti.
Risultato: per ogni calcolo durata dall'invio alla fine, attesa in coda, secondi di HHblits dichiarati dal worker,
processori e memoria di HHblits, profili calcolati o riusati; per ogni livello durata complessiva e calcoli all'ora.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path

import httpx
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent))
from benchmark_lunghezza import chain_residues, mutate, tiled_template  # noqa: E402

from bioloom import __version__  # noqa: E402
from bioloom.app import create_app  # noqa: E402
from bioloom.config import Paths  # noqa: E402


def build_models(n: int, length: int, template_file: str, seed0: int, workers: int) -> list[str]:
    root = Path(tempfile.mkdtemp(prefix="bench-qmean-", dir="/scratch"))
    data = root / "data"
    (data / "licenses").mkdir(parents=True)
    (data / "settings.json").write_text(json.dumps({"max_workers": workers, "compute_cache": False,
                                                    "job_timeout_minutes": 600}))
    pdb, template_seq = tiled_template(chain_residues(Path(template_file)), length)
    app = create_app(Paths(data, scratch_dir=root / "scratch", local_cache_dir=root / "lc"), frontend_dist=None)
    models: list[str] = []
    with TestClient(app, base_url="http://127.0.0.1:8765") as client:
        r = client.post("/api/templates/upload", files={"file": ("TL.pdb", pdb.encode(), "chemical/x-pdb")},
                        data={"template_id": "TL"})
        assert r.status_code == 200, r.text
        tid = r.json().get("id", "TL")
        ids = []
        for k in range(n):
            body = {"target": {"name": f"q{k}", "sequence": mutate(template_seq, rate=0.10, seed=seed0 + k)},
                    "templates": [{"id": tid, "chains": ["A"]}], "quality": "none",
                    "options": {"models": 1, "refinement": "fast"}}
            r = client.post("/api/model", json=body)
            assert r.status_code == 200, r.text
            ids.append(r.json()["id"])
        for job_id in ids:
            while True:
                job = client.get(f"/api/jobs/{job_id}").json()
                if job["status"] in ("completed", "failed", "cancelled"):
                    break
                time.sleep(2)
            assert job["status"] == "completed", job.get("error")
            best = job["result"]["best_model"]
            models.append((data / "jobs" / job_id / best).read_text())
    return models


def run_level(url: str, models: list[str], poll: float = 5.0) -> dict:
    with httpx.Client(base_url=url, timeout=httpx.Timeout(60.0, connect=5.0)) as c:
        t0 = time.time()
        sent = {}
        for pdb in models:
            r = c.post("/jobs", json={"pdb": pdb, "method": "auto"})
            assert r.status_code == 202, r.text
            sent[r.json()["id"]] = time.time()
        done: dict[str, dict] = {}
        started: dict[str, float] = {}
        while len(done) < len(sent):
            time.sleep(poll)
            for job_id in sent:
                if job_id in done:
                    continue
                job = c.get(f"/jobs/{job_id}").json()
                if job.get("status") == "running" and job_id not in started:
                    started[job_id] = time.time()
                if job.get("status") in ("completed", "failed", "cancelled"):
                    result = job.get("result") or {}
                    hh = result.get("bioloom_hhblits") or {}
                    prof = result.get("bioloom_profiles") or {}
                    done[job_id] = {"status": job["status"], "error": job.get("error"),
                                    "total_s": round(time.time() - sent[job_id], 1),
                                    "queued_s": round(started.get(job_id, sent[job_id]) - sent[job_id], 1),
                                    "hhblits_s": hh.get("seconds"), "hhblits_cpu": hh.get("cpu"),
                                    "hhblits_memory": hh.get("memory"),
                                    "profiles_computed": prof.get("computed"), "profiles_reused": prof.get("reused")}
        makespan = time.time() - t0
    rows = list(done.values())
    ok = [r for r in rows if r["status"] == "completed"]
    return {"concurrent": len(models), "makespan_s": round(makespan, 1),
            "per_hour": round(len(ok) * 3600 / makespan, 1) if makespan > 0 else None,
            "completed": len(ok), "jobs": rows}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--levels", default="1,8,16,32")
    ap.add_argument("--length", type=int, default=300)
    ap.add_argument("--template-file", required=True)
    ap.add_argument("--build-workers", type=int, default=32)
    ap.add_argument("--host-label", default=platform.node())
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    url = os.environ.get("BIOLOOM_QMEAN_URL", "http://127.0.0.1:8080").rstrip("/")
    levels = [int(x) for x in a.levels.split(",")]
    status = httpx.get(url + "/status", timeout=20).json()
    seed0 = int(time.time())  # sequenze mai viste dalla cache dei profili del worker
    t0 = time.time()
    models = build_models(sum(levels), a.length, a.template_file, seed0, a.build_workers)
    build_s = time.time() - t0
    out = {"host": a.host_label, "cpu_count": os.cpu_count(), "bioloom": __version__,
           "date": datetime.now().astimezone().isoformat(timespec="seconds"), "length": a.length,
           "worker_status": status,
           "build_s": round(build_s, 1), "seed0": seed0, "levels": []}
    i = 0
    for level in levels:
        res = run_level(url, models[i:i + level])
        i += level
        out["levels"].append(res)
        print(json.dumps({k: v for k, v in res.items() if k != "jobs"}), flush=True)
        Path(a.out).write_text(json.dumps(out, indent=1))


if __name__ == "__main__":
    main()
