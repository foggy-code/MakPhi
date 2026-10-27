"""Tempo di costruzione di MODELLER in funzione della lunghezza: le misure ripetute da cui si ricava la stima dei
controlli preventivi (domain/preflight.building_seconds, punti 4 e 20–24 dei lavori aperti).

Gira dentro l'immagine di MakPhi con il codice montato in /src, come scripts/benchmark_lunghezza.py, una misura per
container (così il picco di memoria è quello di quella misura):

    python /src/scripts/benchmark_tempi.py --length 1000 --models 2 --template-file /hostdata/templates/3IU0.pdb \
        --rep 1 --out /out/L1000_m2_serie_r1.json
    python /src/scripts/benchmark_tempi.py --length 300 --models 4 --parallel --workers 4 ... (modalità parallela)

Template: la catena del file PDB, ripetuta e affiancata se il bersaglio è più lungo (benchmark_lunghezza.tiled_template);
bersaglio con il 5 % di mutazioni (seme = numero della ripetizione: ogni ripetizione ha una sequenza sua). Job `model`
senza analisi di qualità, raffinamento "fast" come la stima: si misura solo la costruzione.

Risultato (JSON): durata del job (dall'avvio alla fine, senza attesa in coda) e tempo reale, CPU dei processi figli,
worker della modalità parallela, stima attuale di `building_seconds` per confronto, picco di memoria del processo
figlio più grande (getrusage RUSAGE_CHILDREN, ru_maxrss: lo dà il kernel alla fine del processo, quindi è il picco
vero di MODELLER, senza campionamento e anche con Podman senza root, dove il cgroup non è leggibile).
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import resource
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path

from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent))
from benchmark_lunghezza import chain_residues, mutate, tiled_template  # noqa: E402

from makphi import __version__  # noqa: E402
from makphi.app import create_app  # noqa: E402
from makphi.config import Paths  # noqa: E402
from makphi.core.fingerprint import code_fingerprint  # noqa: E402
from makphi.domain import preflight  # noqa: E402


def seconds_between(a: str, b: str) -> float:
    return (datetime.fromisoformat(b) - datetime.fromisoformat(a)).total_seconds()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--length", type=int, required=True)
    ap.add_argument("--models", type=int, default=1)
    ap.add_argument("--parallel", action="store_true", help="modalità parallela di MODELLER")
    ap.add_argument("--workers", type=int, default=1, help="job paralleli nelle Impostazioni (worker possibili)")
    ap.add_argument("--refinement", default="fast")
    ap.add_argument("--rep", type=int, default=1, help="numero della ripetizione (anche seme delle mutazioni)")
    ap.add_argument("--template-file", required=True)
    ap.add_argument("--host-label", default=platform.node())
    ap.add_argument("--out", required=True)
    a = ap.parse_args()

    root = Path(tempfile.mkdtemp(prefix="bench-tempi-", dir="/scratch"))
    data = root / "data"
    (data / "licenses").mkdir(parents=True)
    (data / "settings.json").write_text(json.dumps({
        "max_workers": a.workers, "compute_cache": False, "job_timeout_minutes": 24 * 60,
        "max_sequence_length": 9999}))
    pdb, template_seq = tiled_template(chain_residues(Path(a.template_file)), a.length)
    target = mutate(template_seq, seed=a.rep)
    app = create_app(Paths(data, scratch_dir=root / "scratch", local_cache_dir=root / "lc"), frontend_dist=None)
    with TestClient(app, base_url="http://127.0.0.1:8765") as client:
        r = client.post("/api/templates/upload", files={"file": ("TL.pdb", pdb.encode(), "chemical/x-pdb")},
                        data={"template_id": "TL"})
        assert r.status_code == 200, r.text
        tid = r.json().get("id", "TL")
        body = {"target": {"name": f"L{a.length}", "sequence": target},
                "templates": [{"id": tid, "chains": ["A"]}], "quality": "none",
                "options": {"models": a.models, "refinement": a.refinement, "parallel": a.parallel}}
        t0 = time.time()
        before = os.times()
        r = client.post("/api/model", json=body)
        assert r.status_code == 200, r.text
        job_id = r.json()["id"]
        while True:
            job = client.get(f"/api/jobs/{job_id}").json()
            if job["status"] in ("completed", "failed", "cancelled"):
                break
            time.sleep(1)
        wall = time.time() - t0
        after = os.times()
        result = job.get("result") or {}
        lanes = result.get("modeller_workers") or 1
        out = {
            "host": a.host_label, "cpu_count": os.cpu_count(), "machine": platform.machine(),
            "makphi": __version__, "fingerprint": code_fingerprint(),
            "date": datetime.now().astimezone().isoformat(timespec="seconds"),
            "length": a.length, "models": a.models, "parallel": a.parallel, "workers_setting": a.workers,
            "refinement": a.refinement, "rep": a.rep,
            "status": job["status"], "error": job.get("error"),
            "job_s": round(seconds_between(job["started_at"], job["finished_at"]), 2)
            if job.get("started_at") and job.get("finished_at") else None,
            "wall_s": round(wall, 2),
            "cpu_children_s": round((after.children_user + after.children_system)
                                    - (before.children_user + before.children_system), 2),
            "modeller_workers": result.get("modeller_workers"),
            "timings": result.get("timings"),
            "estimate_s": round(preflight.building_seconds(a.length, a.models, a.refinement, lanes=lanes), 2),
            "maxrss_children_mb": round(resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss / 1024, 1),
        }
        print(json.dumps(out))
        Path(a.out).write_text(json.dumps(out, indent=1))


if __name__ == "__main__":
    main()
