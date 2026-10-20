"""Esperimento: MODELLER in serie e in parallelo con lo stesso seme (riproducibilità, punto 2 dei lavori aperti).

Non fa parte della suite (il nome non segue `test_*.py`): si lancia esplicitamente, nel container di test,
    scripts/test.sh backend tests/perf_serial_parallel.py -s
e scrive i risultati in backend/.esperimento-serie-parallelo.json (cartella nascosta: fuori dall'impronta).

Bersaglio: ubiquitina di lievito (YEAST_UB), template 1UBQ catena A, 3 modelli con raffinamento "fast", per ogni seme
di SEEDS:
  - due costruzioni in serie (parallel=False): devono dare coordinate identiche (determinismo);
  - costruzioni in parallelo con 1, 2 e 3 job paralleli nelle Impostazioni (worker della modalità parallela): devono
    dare coordinate identiche fra loro, con qualunque numero di worker;
  - serie contro parallelo: MODELLER assegna a ogni modello del lavoro parallelo un seme proprio, quindi ci si
    aspetta modelli diversi; l'esperimento misura quanto (RMSD e scarto massimo per modello, senza sovrapposizione).
Le coordinate si confrontano con domain/reproducibility.compare_coordinates, la stessa funzione delle riesecuzioni.
"""

from __future__ import annotations

import json
import os
import platform
import time
from pathlib import Path

import numpy as np

from bioloom import __version__
from bioloom.domain import reproducibility as repro
from tests.conftest import YEAST_UB, requires_modeller, wait_job

SEEDS = [int(s) for s in os.environ.get("BIOLOOM_EXPERIMENT_SEEDS", "-4321,-12312,-777").split(",")]
OUT = Path(__file__).resolve().parents[1] / ".esperimento-serie-parallelo.json"


def _model(client, seed: int, parallel: bool) -> dict:
    job = client.post("/api/model", json={"target": {"name": "yeast_ub", "sequence": YEAST_UB},
                                          "templates": [{"id": "1UBQ", "chains": ["A"]}], "quality": "none",
                                          "options": {"models": 3, "refinement": "fast", "seed": seed,
                                                      "parallel": parallel}}).json()
    done = wait_job(client, job["id"], timeout=900)
    assert done["status"] == "completed", done.get("error")
    return done


def _models(client, job: dict) -> list[str]:
    core = client.app.state.core
    return [core.jobs.file_path(job["id"], m["file"]).read_text() for m in job["result"]["models"]]


def _ca(text: str) -> dict:
    return {(ln[21], ln[22:27]): [float(ln[30:38]), float(ln[38:46]), float(ln[46:54])] for ln in text.splitlines()
            if ln.startswith("ATOM") and ln[12:16].strip() == "CA"}


def _superposed_ca_rmsd(x: str, y: str) -> float:
    """RMSD dei Cα dopo la sovrapposizione ottimale (Kabsch): distingue un sistema di riferimento diverso da una
    struttura diversa."""
    a, b = _ca(x), _ca(y)
    keys = sorted(set(a) & set(b))
    p, q = np.array([a[k] for k in keys]), np.array([b[k] for k in keys])
    p, q = p - p.mean(axis=0), q - q.mean(axis=0)
    u, _s, vt = np.linalg.svd(p.T @ q)
    d = np.sign(np.linalg.det(u @ vt))
    rot = u @ np.diag([1.0, 1.0, d]) @ vt
    return round(float(np.sqrt(((p @ rot - q) ** 2).sum(axis=1).mean())), 4)


def _compare(a: list[str], b: list[str]) -> list[dict]:
    out = []
    for x, y in zip(a, b):
        c = repro.compare_coordinates(x, y)
        out.append({"rmsd": c.get("rmsd"), "backbone_rmsd": c.get("backbone_rmsd"),
                    "superposed_ca_rmsd": _superposed_ca_rmsd(x, y),
                    "max_deviation": c.get("max_deviation"), "within": c.get("within"),
                    "identical_text": [ln[30:54] for ln in x.splitlines() if ln.startswith("ATOM")]
                    == [ln[30:54] for ln in y.splitlines() if ln.startswith("ATOM")]})
    return out


@requires_modeller
def test_serial_against_parallel(client_with_ubq):
    c = client_with_ubq
    rows = []
    for seed in SEEDS:
        c.put("/api/settings", json={"max_workers": 1})
        t0 = time.perf_counter()
        serial_a = _model(c, seed, parallel=False)
        t_serial = time.perf_counter() - t0
        serial_b = _model(c, seed, parallel=False)
        parallel = {}
        times = {}
        for workers in (1, 2, 3):
            c.put("/api/settings", json={"max_workers": workers})
            t0 = time.perf_counter()
            parallel[workers] = _model(c, seed, parallel=True)
            times[workers] = time.perf_counter() - t0
        sa, sb = _models(c, serial_a), _models(c, serial_b)
        p = {w: _models(c, j) for w, j in parallel.items()}
        row = {
            "seed": seed,
            "modeller_workers": {w: j["result"]["modeller_workers"] for w, j in parallel.items()},
            "seconds": {"serial": round(t_serial, 2), **{f"parallel_{w}": round(t, 2) for w, t in times.items()}},
            "serial_vs_serial": _compare(sa, sb),
            "parallel_1_vs_2": _compare(p[1], p[2]),
            "parallel_1_vs_3": _compare(p[1], p[3]),
            "serial_vs_parallel_3": _compare(sa, p[3]),
            "dope": {"serial": [m.get("dope") for m in serial_a["result"]["models"]],
                     "parallel_3": [m.get("dope") for m in parallel[3]["result"]["models"]]},
        }
        rows.append(row)
        print(json.dumps(row, indent=1))
        assert all(r["identical_text"] for r in row["serial_vs_serial"])
        assert all(r["identical_text"] for r in row["parallel_1_vs_2"] + row["parallel_1_vs_3"])
    OUT.write_text(json.dumps({"bioloom": __version__, "machine": platform.machine(), "cpu": os.cpu_count(),
                               "date": time.strftime("%Y-%m-%dT%H:%M:%S"), "target": "yeast ubiquitin (YEAST_UB)",
                               "template": "1UBQ:A", "models": 3, "refinement": "fast", "rows": rows}, indent=1),
                   encoding="utf-8")
