"""Tempi e memoria di un job in funzione della lunghezza della sequenza bersaglio (impostazione max_sequence_length).

Gira dentro l'immagine di MakPhi con il codice montato in /src, come scripts/benchmark_mxn.py, una lunghezza per
container (il picco di memoria vale per tutta la vita del container):

    python /src/scripts/benchmark_lunghezza.py --length 5000 --template-file /hostdata/templates/3IU0.pdb \
        --out /out/L5000.json

Template sintetico: la catena del file PDB ripetuta finché serve, le copie affiancate su una griglia (distanza tra le
copie pari all'ingombro della catena più 8 Å, così non si toccano), residui e atomi rinumerati da 1 in un'unica catena
A, tagliata alla lunghezza chiesta. Il bersaglio è la sequenza del template con il 5 % di mutazioni casuali (seme
fisso): l'allineamento è quasi identico e il tempo misura la lunghezza, non la difficoltà del bersaglio. Le giunzioni
fra le copie sono legami lunghi che MODELLER deve chiudere: il modello non ha senso biologico, i tempi sì.

Risultato: durata e CPU del job, picco di memoria (somma degli RSS dei processi del container, campionata ogni
secondo, e il picco del cgroup quando c'è), durata delle fasi ricavata dal log del job, esito delle analisi.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import random
import re
import shutil
import tempfile
import threading
import time
from pathlib import Path

from fastapi.testclient import TestClient

from makphi.app import create_app
from makphi.config import Paths

AA = "ACDEFGHIKLMNPQRSTVWY"
THREE = {"ALA": "A", "ARG": "R", "ASN": "N", "ASP": "D", "CYS": "C", "GLN": "Q", "GLU": "E", "GLY": "G", "HIS": "H",
         "ILE": "I", "LEU": "L", "LYS": "K", "MET": "M", "PHE": "F", "PRO": "P", "SER": "S", "THR": "T", "TRP": "W",
         "TYR": "Y", "VAL": "V"}


def chain_residues(path: Path) -> list[list[str]]:
    """Residui standard della prima catena (righe ATOM), nell'ordine del file."""
    residues: list[list[str]] = []
    chain, last = None, None
    for line in path.read_text().splitlines():
        if line.startswith("ENDMDL"):
            break
        if not line.startswith("ATOM") or line[17:20] not in THREE or line[16] not in " A":
            continue
        if chain is None:
            chain = line[21]
        if line[21] != chain:
            continue
        key = line[22:27]
        if key != last:
            residues.append([])
            last = key
        residues[-1].append(line)
    return residues


def tiled_template(residues: list[list[str]], length: int) -> tuple[str, str]:
    """PDB di `length` residui fatto di copie della catena su una griglia, e la sequenza a una lettera."""
    xyz = [(float(l[30:38]), float(l[38:46]), float(l[46:54])) for r in residues for l in r]
    lo = [min(c[i] for c in xyz) for i in range(3)]
    hi = [max(c[i] for c in xyz) for i in range(3)]
    step = max(h - l for h, l in zip(hi, lo)) + 8.0
    copies = math.ceil(length / len(residues))
    side = math.ceil(copies ** (1 / 3))
    out, seq, atom, resno = [], [], 0, 0
    for k in range(copies):
        dx, dy, dz = (k % side) * step, (k // side % side) * step, (k // side // side) * step
        for res in residues:
            if resno == length:
                break
            resno += 1
            seq.append(THREE[res[0][17:20]])
            for l in res:
                atom += 1
                x, y, z = float(l[30:38]) + dx, float(l[38:46]) + dy, float(l[46:54]) + dz
                out.append(f"ATOM  {atom:5d} {l[12:16]} {l[17:20]} A{resno:4d}    {x:8.3f}{y:8.3f}{z:8.3f}"
                           f"  1.00 30.00          {l[76:78]}")
    out += [f"TER   {atom + 1:5d}      {residues[-1][0][17:20]} A{resno:4d}", "END"]
    return "\n".join(out) + "\n", "".join(seq)


def mutate(seq: str, rate: float = 0.05, seed: int = 7) -> str:
    rng = random.Random(seed)
    s = list(seq)
    for i in rng.sample(range(len(s)), int(len(s) * rate)):
        s[i] = rng.choice(AA.replace(s[i], ""))
    return "".join(s)


def rss_total() -> int:
    """Somma degli RSS di tutti i processi visibili (nel container: solo i suoi)."""
    total = 0
    for d in os.listdir("/proc"):
        if d.isdigit():
            try:
                for line in open(f"/proc/{d}/status"):
                    if line.startswith("VmRSS:"):
                        total += int(line.split()[1]) * 1024
                        break
            except OSError:
                pass
    return total


def cgroup_peak() -> int:
    for f in ("/sys/fs/cgroup/memory.peak", "/sys/fs/cgroup/memory/memory.max_usage_in_bytes"):
        try:
            return int(Path(f).read_text().strip())
        except (OSError, ValueError):
            pass
    return 0


def cpu_children() -> float:
    t = os.times()
    return t.user + t.system + t.children_user + t.children_system


class Sampler(threading.Thread):
    def __init__(self) -> None:
        super().__init__(daemon=True)
        self.peak = 0
        self.stop = threading.Event()

    def run(self) -> None:
        while not self.stop.wait(1.0):
            self.peak = max(self.peak, rss_total())


STAMP = re.compile(r"^\[(\d\d):(\d\d):(\d\d)\] (.*)$")


def timeline(log: str) -> list[dict]:
    """Righe del log con i secondi dall'inizio (il log ha l'ora al secondo)."""
    rows, first, prev = [], None, None
    day = 0
    for line in log.splitlines():
        m = STAMP.match(line)
        if not m:
            continue
        t = int(m[1]) * 3600 + int(m[2]) * 60 + int(m[3])
        if prev is not None and t < prev:
            day += 86400
        prev = t
        t += day
        first = t if first is None else first
        rows.append({"t": t - first, "line": m[4][:240]})
    return rows


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--length", type=int, required=True)
    ap.add_argument("--template-file", required=True)
    ap.add_argument("--models", type=int, default=1)
    ap.add_argument("--refinement", default="fast")
    ap.add_argument("--no-extended", action="store_true", help="senza PROCHECK e ProSA")
    ap.add_argument("--no-qmean", action="store_true", help="senza il QMEAN automatico sul modello")
    ap.add_argument("--timeout-minutes", type=int, default=24 * 60)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()

    root = Path(tempfile.mkdtemp(prefix="bench-len-", dir="/scratch"))
    data = root / "data"
    (data / "licenses").mkdir(parents=True)
    prosa_src = Path("/hostdata/licenses/prosa")
    if prosa_src.exists():
        shutil.copytree(prosa_src, data / "licenses" / "prosa")
    (data / "settings.json").write_text(json.dumps({
        "max_workers": 4, "compute_cache": False, "job_timeout_minutes": a.timeout_minutes,
        "max_sequence_length": 9999, "qmean_mode": "local",
        "qmean_timeout_minutes": a.timeout_minutes}))
    pdb, template_seq = tiled_template(chain_residues(Path(a.template_file)), a.length)
    target = mutate(template_seq)
    app = create_app(Paths(data, scratch_dir=root / "scratch", local_cache_dir=root / "lc"), frontend_dist=None)
    with TestClient(app, base_url="http://127.0.0.1:8765") as client:
        r = client.post("/api/templates/upload", files={"file": ("TL.pdb", pdb.encode(), "chemical/x-pdb")},
                        data={"template_id": "TL"})
        assert r.status_code == 200, r.text
        tid = r.json().get("id", "TL")
        body = {"fasta": f">L{a.length}\n{target}\n", "template_mode": "fixed", "fixed_combination": "each",
                "templates": [{"id": tid, "chains": ["A"], "hetatm": False}],
                "options": {"models": a.models, "refinement": a.refinement}, "quality": True,
                "extended_quality": not a.no_extended, "parallel": 1, "use_cache": False, "qmean": not a.no_qmean}
        sampler = Sampler()
        sampler.start()
        t0, c0 = time.time(), cpu_children()
        r = client.post("/api/pipeline", json=body)
        assert r.status_code == 200, r.text
        job_id = r.json()["id"]
        while True:
            job = client.get(f"/api/jobs/{job_id}").json()
            if job["status"] in ("completed", "failed", "cancelled"):
                break
            time.sleep(2)
        wall = time.time() - t0
        # QMEAN automatico sul modello migliore: un job a parte, dopo la pipeline (con il servizio QMEAN raggiungibile)
        # (accodato da un thread a pipeline finita: si aspetta che compaia, poi che finisca)
        deadline = time.time() + a.timeout_minutes * 60
        appear = time.time() + 120
        qmean_jobs: list[dict] = []
        while job["status"] == "completed" and not a.no_qmean and time.time() < deadline:
            qmean_jobs = client.get("/api/jobs", params={"kind": "qmean"}).json()
            if qmean_jobs and all(j["status"] in ("completed", "failed", "cancelled") for j in qmean_jobs):
                break
            if not qmean_jobs and time.time() > appear:
                break
            time.sleep(5)
        total_wall = time.time() - t0
        cpu = cpu_children() - c0
        sampler.stop.set()
        qmean = []
        for j in qmean_jobs:
            qmean.append({k: j.get(k) for k in ("status", "error", "created_at", "started_at", "finished_at")})
            qlog = client.get(f"/api/jobs/{j['id']}/log").json().get("text", "")
            qmean[-1]["timeline"] = timeline(qlog)
        log, offset = "", 0
        while True:  # il log si legge a pezzi
            part = client.get(f"/api/jobs/{job_id}/log", params={"offset": offset}).json()
            if not part.get("text"):
                break
            log += part["text"]
            if part["offset"] <= offset:
                break
            offset = part["offset"]
        items = client.get(f"/api/pipeline/{job_id}/items?size=10").json()["items"]
        item = items[0] if items else {}
        jobdir = data / "jobs" / job_id
        for f in jobdir.rglob("*.B9999*.pdb"):  # il modello, per altre prove (analisi, QMEAN) senza rifarlo
            shutil.copyfile(f, Path(a.out).with_suffix(".pdb"))
        item_log = ""
        for f in jobdir.rglob("programmi.log"):
            item_log = f.read_text(errors="replace")
        result = {
            "length": a.length, "template_atoms": pdb.count("\nATOM") + 1, "models": a.models,
            "refinement": a.refinement, "extended": not a.no_extended,
            "status": job["status"], "item_status": item.get("status"), "item_error": item.get("error"),
            "metrics": {k: item.get(k) for k in ("identity", "dope", "ga341", "grade", "grade_score", "rama_favored",
                                                  "clashscore", "metrics")},
            "wall_s": round(wall, 1), "total_with_qmean_s": round(total_wall, 1), "cpu_s": round(cpu, 1),
            "qmean_jobs": qmean,
            "rss_peak_mb": round(sampler.peak / 2**20), "cgroup_peak_mb": round(cgroup_peak() / 2**20),
            "results_mb": round(sum(f.stat().st_size for f in jobdir.rglob("*") if f.is_file()) / 2**20, 1),
            "timeline": timeline(log),
            # programmi dell'elemento: righe "===== [ora] [elemento] [modello] [programma] ..." di programmi.log
            "programs": [l for l in item_log.splitlines() if l.startswith("===== [")],
            "item_log_tail": item_log[-6000:],
        }
        print(json.dumps({k: v for k, v in result.items() if k not in ("timeline", "item_log_tail")}, indent=1))
        Path(a.out).write_text(json.dumps(result, indent=1))


if __name__ == "__main__":
    main()
