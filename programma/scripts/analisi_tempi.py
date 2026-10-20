"""Analisi delle misure di scripts/benchmark_tempi.py: dispersione delle ripetizioni e legge di potenza dei tempi di
costruzione, da cui vengono le costanti di domain/preflight.py (building_seconds).

    python programma/scripts/analisi_tempi.py bench_out/tempi/server [bench_out/tempi/comune ...] --out riepilogo.json

Per ogni configurazione (macchina, lunghezza, modelli, modalità, worker): numero di ripetizioni, mediana, minimo,
massimo, deviazione standard e coefficiente di variazione della durata del job, picco di memoria.

Modello (raffinamento "fast", in serie): t(L, m) = avvio + R(L) + m · M(L), con
  M(L) = t(L, 2) − t(L, 1)          (un modello in più)
  avvio + R(L) = 2 · t(L, 1) − t(L, 2)
  R(L) = R76 · (L / 76)^p,  M(L) = M76 · (L / 76)^q     (minimi quadrati sui logaritmi delle mediane)
L'avvio (T_STARTUP) si misura a parte e qui resta quello di preflight.py. Modalità parallela: T_WORKERS = t(parallelo,
m modelli, m worker) − t(serie, 1 modello) − differenza attesa fra un giro di modelli e uno, alla stessa lunghezza.
Solo libreria standard e NumPy: gira anche fuori dall'immagine.
"""

from __future__ import annotations

import argparse
import json
import statistics
from collections import defaultdict
from pathlib import Path

import numpy as np

REFERENCE = 76  # residui dell'ubiquitina, la lunghezza delle costanti di preflight.py


def load(folders: list[str]) -> list[dict]:
    rows = []
    for folder in folders:
        for f in sorted(Path(folder).glob("*.json")):
            try:
                d = json.loads(f.read_text(encoding="utf-8"))
            except ValueError:
                continue
            if isinstance(d, dict) and d.get("status") == "completed" and d.get("job_s") is not None:
                rows.append(d)
    return rows


def stats(values: list[float]) -> dict:
    med = statistics.median(values)
    sd = statistics.stdev(values) if len(values) > 1 else 0.0
    return {"n": len(values), "median": round(med, 2), "min": round(min(values), 2), "max": round(max(values), 2),
            "sd": round(sd, 2), "cv_pct": round(100 * sd / med, 1) if med else None}


def power_fit(lengths: list[int], values: list[float]) -> dict:
    """value = a76 · (L/76)^p, minimi quadrati sui logaritmi; R² sui logaritmi."""
    x = np.log(np.array(lengths, dtype=float) / REFERENCE)
    y = np.log(np.array(values, dtype=float))
    p, log_a = np.polyfit(x, y, 1)
    pred = log_a + p * x
    ss_res = float(((y - pred) ** 2).sum())
    ss_tot = float(((y - y.mean()) ** 2).sum()) or 1.0
    return {"a76": round(float(np.exp(log_a)), 4), "exponent": round(float(p), 3), "r2_log": round(1 - ss_res / ss_tot, 4),
            "points": len(lengths)}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("folders", nargs="+")
    ap.add_argument("--out")
    a = ap.parse_args()
    rows = load(a.folders)
    groups: dict[tuple, list[dict]] = defaultdict(list)
    for r in rows:
        key = (r["host"], r["length"], r["models"], bool(r["parallel"]), r.get("modeller_workers") or 1)
        groups[key].append(r)
    table = []
    for key in sorted(groups):
        g = groups[key]
        table.append({"host": key[0], "length": key[1], "models": key[2], "parallel": key[3], "workers": key[4],
                      "job_s": stats([r["job_s"] for r in g]),
                      "estimate_s": g[0].get("estimate_s"),
                      "maxrss_mb": max(r.get("maxrss_children_mb") or 0 for r in g)})
    fits = {}
    for host in sorted({k[0] for k in groups}):
        med = {(t["length"], t["models"]): t["job_s"]["median"] for t in table
               if t["host"] == host and not t["parallel"]}
        lengths = sorted(L for (L, m) in med if m == 1 and (L, 2) in med)
        if len(lengths) < 3:
            continue
        per_model = [med[(L, 2)] - med[(L, 1)] for L in lengths]
        base = [2 * med[(L, 1)] - med[(L, 2)] for L in lengths]
        usable = [(L, mm, b) for L, mm, b in zip(lengths, per_model, base) if mm > 0 and b > 0]
        fits[host] = {
            "lengths": [u[0] for u in usable],
            "per_model_s": [round(u[1], 2) for u in usable],
            "startup_plus_restraints_s": [round(u[2], 2) for u in usable],
            "model": power_fit([u[0] for u in usable], [u[1] for u in usable]),
            "startup_plus_restraints": power_fit([u[0] for u in usable], [u[2] for u in usable]),
            "one_model_total": power_fit(lengths, [med[(L, 1)] for L in lengths]),
        }
        # avvio dei worker: parallelo con m modelli e m worker contro un modello in serie, alla stessa lunghezza
        workers = []
        for t in table:
            if t["host"] == host and t["parallel"] and (t["length"], 1) in med and (t["length"], 2) in med:
                workers.append({"length": t["length"], "models": t["models"], "workers": t["workers"],
                                "extra_s": round(t["job_s"]["median"] - med[(t["length"], 1)], 2)})
        fits[host]["parallel_extra_over_one_serial_model"] = workers
    out = {"table": table, "fits": fits}
    for t in table:
        s = t["job_s"]
        print(f"{t['host']:<14} L={t['length']:<5} m={t['models']} {'par' if t['parallel'] else 'ser'} w={t['workers']:<2}"
              f" n={s['n']} mediana {s['median']:>8.1f} s  [{s['min']:.1f}–{s['max']:.1f}]  cv {s['cv_pct']}%"
              f"  stima {t['estimate_s']}  mem {t['maxrss_mb']} MB")
    for host, f in fits.items():
        print(f"\n{host}: modello {f['model']}, avvio+restrizioni {f['startup_plus_restraints']}, "
              f"un modello {f['one_model_total']}")
        for w in f["parallel_extra_over_one_serial_model"]:
            print(f"  parallelo {w}")
    if a.out:
        Path(a.out).write_text(json.dumps(out, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
