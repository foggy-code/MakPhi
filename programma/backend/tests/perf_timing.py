"""Profilazione dei tempi (F1.2/F1.3) con MODELLER reale: job model, elemento di pipeline, analisi di qualità.

Non fa parte della suite (il nome non segue `test_*.py`): si lancia esplicitamente, nel container di test,
    scripts/test.sh backend tests/perf_timing.py -s
e stampa per ogni scenario la scomposizione del tempo reale in passi (`test_profile`). Le funzioni misurate sono
avvolte con monkeypatch (nessuna modifica al codice misurato); un passo eseguito in parallelo ad altri conta per la
sua durata, quindi la somma dei passi di un'analisi parallela supera il suo tempo reale.

`test_paired` confronta due varianti nello stesso processo, alternate (A B A B …), così il carico della macchina
(altri container, altri programmi) pesa allo stesso modo su entrambe: analisi in serie / in parallelo, qualità "all"
con 1 / 3 modelli insieme, pipeline con e senza la cache dei template preparati. Usa le funzioni introdotte dalla
F1.2/F1.3: sul codice precedente si lancia solo `test_profile`.

Per ogni valore: mediana e minimo delle ripetizioni. Su una macchina condivisa il minimo è la stima meno disturbata
del tempo del calcolo (Chen & Revels, "Robust benchmarking in noisy environments", 2016).
Variabili: BIOLOOM_PERF_REPEAT (ripetizioni, predefinita 3).
"""

from __future__ import annotations

import os
import statistics
import threading
import time
from collections import defaultdict
from pathlib import Path

import pytest

from bioloom.core import jobs as jobs_module
from bioloom.domain import clashes, consensus, ramachandran, secondary, structure
from bioloom.domain import preflight as preflight_rules
from bioloom.engines import modeller, procheck, prosa, quality, search
from bioloom.services import compute as compute_module
from tests.conftest import DATA, YEAST_UB, requires_modeller, wait_job

REPEAT = int(os.environ.get("BIOLOOM_PERF_REPEAT", "3"))


class Timer:
    """Durate per passo, sicure fra thread; `marks` = istanti dei marcatori di MODELLER (per modello)."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.rows: dict[str, list[float]] = defaultdict(list)

    def add(self, label: str, seconds: float) -> None:
        with self.lock:
            self.rows[label].append(seconds)

    def wrap(self, label, fn):
        def inner(*args, **kwargs):
            t0 = time.perf_counter()
            try:
                return fn(*args, **kwargs)
            finally:
                self.add(label if isinstance(label, str) else label(*args, **kwargs), time.perf_counter() - t0)
        return inner

    def reset(self) -> None:
        with self.lock:
            self.rows.clear()


_inside = threading.local()  # la lettura della struttura conta nell'analisi solo se la chiama l'analisi


def _analysis(fn):
    def inner(*args, **kwargs):
        _inside.flag = True
        try:
            return fn(*args, **kwargs)
        finally:
            _inside.flag = False
    return inner


def _load_label(*_args, **_kwargs) -> str:
    return "  lettura (Biopython)" if getattr(_inside, "flag", False) else "lettura di strutture fuori dall'analisi"


def _process_label(cmd) -> str:
    joined = " ".join(str(c) for c in cmd)
    for key in ("align.py", "model.py", "dope_eval.py", "dope_batch.py", "loop.py", "probe.py"):
        if key in joined:
            return f"processo {key}"
    if "procheck" in joined:
        return "processo PROCHECK"
    if "prosa" in joined:
        return "processo ProSA"
    if "dssp" in joined:
        return "processo mkdssp"
    return "processo " + Path(str(cmd[0])).name


@pytest.fixture
def timer(monkeypatch):
    t = Timer()
    original_run = jobs_module.JobContext.run

    def run(self, cmd, **kwargs):
        label = _process_label(cmd)
        start = time.perf_counter()
        if label == "processo model.py":  # istanti dei marcatori: import, restrizioni + modello 1, modelli successivi
            user = kwargs.get("on_line")
            last = {"t": start}

            def on_line(line):
                now = time.perf_counter()
                if line.startswith(modeller.MARKER):
                    text = line[len(modeller.MARKER):].strip()
                    if text.startswith("stage Costruzione"):
                        t.add("  model.py: avvio e lettura", now - last["t"])
                        last["t"] = now
                    elif text.startswith("model "):
                        n = int(text.split()[1].split("/")[0])
                        t.add("  model.py: restrizioni + modello 1" if n == 1 else "  model.py: modello successivo",
                              now - last["t"])
                        last["t"] = now
                if user is not None:
                    user(line)
            kwargs["on_line"] = on_line
        try:
            return original_run(self, cmd, **kwargs)
        finally:
            t.add(label, time.perf_counter() - start)

    monkeypatch.setattr(jobs_module.JobContext, "run", run)
    svc = compute_module.ComputeService
    monkeypatch.setattr(svc, "_prepare_templates", t.wrap("preparazione dei template", svc._prepare_templates))
    monkeypatch.setattr(svc, "_run_alignment", t.wrap("script di allineamento", svc._run_alignment))
    monkeypatch.setattr(quality, "analyze", t.wrap("analisi di qualità (totale)", _analysis(quality.analyze)))
    monkeypatch.setattr(structure, "load_structure", t.wrap(_load_label, structure.load_structure))
    monkeypatch.setattr(ramachandran, "analyze", t.wrap("  Ramachandran", ramachandran.analyze))
    monkeypatch.setattr(clashes, "analyze", t.wrap("  clash", clashes.analyze))
    # mkdssp: prima della F1.3 `_dssp_external` (Biopython lancia `mkdssp --version` e mkdssp), poi `_run_mkdssp`
    for name in ("_dssp_external", "_run_mkdssp"):
        if hasattr(quality, name) and not (name == "_dssp_external" and hasattr(quality, "_run_mkdssp")):
            monkeypatch.setattr(quality, name, t.wrap("  DSSP (mkdssp)", getattr(quality, name)))
    monkeypatch.setattr(secondary, "assign", t.wrap("  DSSP integrato", secondary.assign))
    monkeypatch.setattr(quality, "_sasa", t.wrap("  SASA (Shrake-Rupley)", quality._sasa))
    monkeypatch.setattr(modeller, "evaluate_dope", t.wrap("  DOPE (MODELLER)", modeller.evaluate_dope))
    monkeypatch.setattr(procheck, "run", t.wrap("  PROCHECK", procheck.run))
    monkeypatch.setattr(prosa, "run", t.wrap("  ProSA", prosa.run))
    monkeypatch.setattr(consensus, "compute", t.wrap("  consenso", consensus.compute))
    monkeypatch.setattr(quality, "inject_remarks", t.wrap("iniezione dei REMARK", quality.inject_remarks))
    monkeypatch.setattr(compute_module, "atomic_write_json",
                        t.wrap("scrittura del rapporto JSON", compute_module.atomic_write_json))
    monkeypatch.setattr(modeller, "modeller_python", t.wrap("rilevamento di MODELLER", modeller.modeller_python))
    mgr = jobs_module.JobManager
    monkeypatch.setattr(mgr, "_commit", t.wrap("salvataggio (scratch → dati)", mgr._commit))
    monkeypatch.setattr(mgr, "_execute", t.wrap("TEMPO REALE DEL JOB", mgr._execute))
    return t


def _report(title: str, runs: list[dict[str, list[float]]]) -> None:
    labels: list[str] = []
    for rows in runs:
        labels += [k for k in rows if k not in labels]
    labels.sort(key=lambda k: k == "TEMPO REALE DEL JOB")
    print(f"\n=== {title}: {len(runs)} esecuzioni")
    print(f"{'passo':45s} {'chiamate':>8s} {'mediana':>8s} {'minimo':>8s}")
    for label in labels:
        totals = [sum(rows.get(label, [])) for rows in runs]
        calls = statistics.median(len(rows.get(label, [])) for rows in runs)
        print(f"{label:45s} {calls:8.0f} {statistics.median(totals):8.2f} {min(totals):8.2f}")
    if all("processo model.py" in rows for rows in runs):  # dopo la costruzione dei modelli: qualità e salvataggio
        rest = [_after_models(rows) for rows in runs]
        print(f"{'(job - template - allineamento - model.py)':45s} {'':8s} {statistics.median(rest):8.2f} "
              f"{min(rest):8.2f}")


def _after_models(rows: dict[str, list[float]]) -> float:
    """Tempo del job dopo template, allineamento e costruzione dei modelli: la fase di qualità (in serie nel job)."""
    return sum(rows["TEMPO REALE DEL JOB"]) - sum(sum(rows.get(k, [])) for k in (
        "preparazione dei template", "script di allineamento", "processo model.py"))


def _print_pair(title: str, values: dict[str, list[float]]) -> None:
    print(f"\n=== {title}")
    print(f"{'variante':45s} {'misure':>8s} {'mediana':>8s} {'minimo':>8s}")
    for label, v in values.items():
        print(f"{label:45s} {len(v):8d} {statistics.median(v):8.2f} {min(v):8.2f}")


def _run_job(client, timer: Timer, path: str, body: dict) -> tuple[dict, dict]:
    timer.reset()
    job = client.post(path, json=body).json()
    done = wait_job(client, job["id"], timeout=1800)
    assert done["status"] == "completed", done.get("error")
    for _ in range(100):  # l'esecuzione si chiude (scratch, log) subito dopo lo stato finale
        if not client.app.state.core.jobs.busy(job["id"]):
            break
        time.sleep(0.05)
    return {k: list(v) for k, v in timer.rows.items()}, done


def _big_template(dest: Path, n_chains: int = 60) -> Path:
    """mmCIF sintetico di `n_chains` copie traslate di 1UBQ (catena A): un template grande da leggere."""
    import string

    from Bio.PDB import MMCIFIO

    st = structure.load_structure(DATA / "1UBQ.pdb", "BIG")
    model = structure.first_model(st)
    base = model["A"]
    letters = string.ascii_uppercase + string.ascii_lowercase + string.digits
    for k in range(1, n_chains):
        copy = base.copy()
        copy.id = letters[k]
        for atom in copy.get_atoms():
            atom.coord = atom.coord + (40.0 * (k % 6), 40.0 * ((k // 6) % 6), 40.0 * (k // 36))
        model.add(copy)
    io = MMCIFIO()
    io.set_structure(st)
    io.save(str(dest))
    return dest


@pytest.mark.modeller
@requires_modeller
@pytest.mark.parametrize("scenario", ["model", "model_all", "model_all_2", "pipeline", "quality_1ubq",
                                      "pipeline_big_template"])
def test_profile(client_with_ubq, timer, monkeypatch, tmp_path, scenario):
    c = client_with_ubq
    hit = {"template_id": "1UBQ_A", "pdb_id": "1UBQ", "chain": "A", "identity": 96.1, "coverage": 100.0,
           "evalue": 1e-40, "resolution": 1.8}
    monkeypatch.setattr(search, "search", lambda ctx, sequence, **_k: {"engine": "rcsb", "hits": [hit]})
    if scenario == "pipeline_big_template":
        with open(_big_template(tmp_path / "BIG1.cif"), "rb") as fh:
            r = c.post("/api/templates/upload", files={"file": ("BIG1.cif", fh, "chemical/x-mmcif")})
        assert r.status_code == 200, r.text
        big_id = r.json()["id"]
    if scenario == "model_all_2":  # qualità "all" con 2 modelli analizzati insieme invece di 3
        monkeypatch.setattr(compute_module, "QUALITY_MODELS_PARALLEL", 2, raising=False)
    runs = []
    for _ in range(REPEAT):
        if scenario in ("model", "model_all", "model_all_2"):
            # come la pagina Modellazione: 5 modelli, raffinamento fast, analisi estesa (PROCHECK + ProSA)
            body = {"target": {"name": "yeast_ub", "sequence": YEAST_UB},
                    "templates": [{"id": "1UBQ", "chains": ["A"]}],
                    "options": {"models": 5, "refinement": "fast"},
                    "quality": "best" if scenario == "model" else "all", "extended_quality": True}
            rows, _done = _run_job(c, timer, "/api/model", body)
        elif scenario == "pipeline":
            # come la pagina Pipeline: 3 modelli, fast, qualità senza PROCHECK/ProSA, un elemento
            body = {"fasta": f">yeast_ub\n{YEAST_UB}\n", "template_mode": "auto", "engine": "rcsb",
                    "options": {"models": 3, "refinement": "fast"}, "quality": True, "extended_quality": False,
                    "parallel": 1}
            rows, _done = _run_job(c, timer, "/api/pipeline", body)
        elif scenario == "pipeline_big_template":
            # 4 sequenze con lo stesso template grande (60 catene, mmCIF), 1 modello, 2 in parallelo
            fasta = "".join(f">s{i}\n{YEAST_UB}\n" for i in range(4))
            body = {"fasta": fasta, "template_mode": "fixed", "templates": [{"id": big_id, "chains": ["A"]}],
                    "options": {"models": 1, "refinement": "very_fast"}, "quality": False, "parallel": 2}
            rows, _done = _run_job(c, timer, "/api/pipeline", body)
        else:
            body = {"source": {"type": "template", "id": "1UBQ"}, "dope": True, "procheck": True, "prosa": True}
            rows, done = _run_job(c, timer, "/api/quality", body)
            assert done["result"]["dope"] is not None and done["result"]["procheck"] is not None
        runs.append(rows)
    _report(scenario, runs)


@pytest.mark.modeller
@requires_modeller
def test_profile_modeller_startup(client, timer):
    """Costo fisso di un processo MODELLER (interprete + import + Environ + librerie di DOPE)."""
    core = client.app.state.core
    scripts = {
        "vuoto": "pass\n",
        "import modeller + Environ": "from modeller import *\nlog.none()\nenv = Environ()\n",
        "import + librerie DOPE": ("from modeller import *\nlog.none()\nenv = Environ()\n"
                                   "env.libs.topology.read(file='$(LIB)/top_heav.lib')\n"
                                   "env.libs.parameters.read(file='$(LIB)/par.lib')\n"),
    }
    times: dict[str, list[float]] = defaultdict(list)

    def handler(ctx, _p):
        for _ in range(REPEAT):
            for label, script in scripts.items():
                t0 = time.perf_counter()
                modeller.run_script(ctx, script, "probe.py")
                times[label].append(time.perf_counter() - t0)
            pdb = ctx.workdir / "m.pdb"
            pdb.write_bytes((DATA / "1UBQ.pdb").read_bytes())
            t0 = time.perf_counter()
            modeller.evaluate_dope(ctx, pdb, ctx.workdir / "dope")
            times["DOPE completa di 1UBQ"].append(time.perf_counter() - t0)
        return {}

    core.jobs.register("probe", handler)
    job = core.jobs.submit("probe", {})
    done = wait_job(client, job["id"], timeout=600)
    assert done["status"] == "completed", done.get("error")
    print("\n=== avvio di MODELLER (mediana)")
    for label, values in times.items():
        print(f"{label:45s} {statistics.median(values):8.2f} {min(values):8.2f}")


@pytest.mark.modeller
@requires_modeller
def test_paired(client_with_ubq, timer, monkeypatch, tmp_path):
    """Varianti alternate nello stesso processo (vedi l'intestazione del modulo)."""
    from bioloom.services import pipeline as pipeline_module
    from bioloom.services.compute import PreparedTemplates

    c = client_with_ubq
    core = c.app.state.core
    rounds = max(REPEAT, 5)
    # 1. analisi di qualità di 1UBQ e di un modello di MODELLER: in serie come prima della F1.3 (compresa la domanda
    #    della versione a mkdssp a ogni analisi, come faceva Biopython) e in parallelo
    job = c.post("/api/model", json={"target": {"name": "yeast_ub", "sequence": YEAST_UB},
                                     "templates": [{"id": "1UBQ", "chains": ["A"]}],
                                     "options": {"models": 1, "refinement": "fast"}, "quality": "none"}).json()
    done = wait_job(c, job["id"], timeout=600)
    model = core.jobs.file_path(job["id"], done["result"]["best_model"])
    times: dict[str, list[float]] = defaultdict(list)

    def analyses(ctx, _p):
        for n in range(rounds):
            for label, path in (("1UBQ", DATA / "1UBQ.pdb"), ("modello", model)):
                for parallel in (False, True):
                    if not parallel:
                        quality._dssp_versions.clear()
                    t0 = time.perf_counter()
                    quality.analyze(path, ctx, dope_workdir=ctx.workdir / f"{label}_{parallel}_{n}",
                                    with_procheck=True, with_prosa=True, parallel=parallel)
                    times[f"analisi {label}, {'parallela' if parallel else 'seriale'}"].append(
                        time.perf_counter() - t0)
        return {}

    core.jobs.register("paired", analyses)
    probe = wait_job(c, core.jobs.submit("paired", {})["id"], timeout=1800)
    assert probe["status"] == "completed", probe.get("error")
    _print_pair(f"analisi di qualità (DOPE, mkdssp, PROCHECK, ProSA), {rounds} coppie alternate", times)

    # 2. qualità "all" su 5 modelli: un modello alla volta (come prima) / 3 insieme; fase dopo la costruzione
    phase: dict[str, list[float]] = defaultdict(list)
    body = {"target": {"name": "yeast_ub", "sequence": YEAST_UB}, "templates": [{"id": "1UBQ", "chains": ["A"]}],
            "options": {"models": 5, "refinement": "fast"}, "quality": "all", "extended_quality": True}
    for _ in range(REPEAT):
        for n in (1, 2, 3):
            monkeypatch.setattr(compute_module, "QUALITY_MODELS_PARALLEL", n)
            rows, _done = _run_job(c, timer, "/api/model", body)
            phase[f"fase di qualità, {n} modelli insieme"].append(_after_models(rows))
    _print_pair(f"qualità su 5 modelli, {REPEAT} terne alternate", phase)

    # 3. pipeline di 4 sequenze con lo stesso template grande: senza / con la cache dei template preparati
    class NoReuse(PreparedTemplates):
        def get(self, source, spec, dest_dir, check):
            return modeller.prepare_template(source, spec, dest_dir), False

    with open(_big_template(tmp_path / "BIG1.cif"), "rb") as fh:
        big_id = c.post("/api/templates/upload", files={"file": ("BIG1.cif", fh, "chemical/x-mmcif")}).json()["id"]
    fasta = "".join(f">s{i}\n{YEAST_UB}\n" for i in range(4))
    body = {"fasta": fasta, "template_mode": "fixed", "templates": [{"id": big_id, "chains": ["A"]}],
            "options": {"models": 1, "refinement": "very_fast"}, "quality": False, "parallel": 2}
    wall: dict[str, list[float]] = defaultdict(list)
    for _ in range(REPEAT):
        for label, cls in (("senza cache", NoReuse), ("con cache", PreparedTemplates)):
            monkeypatch.setattr(pipeline_module, "PreparedTemplates", cls)
            rows, _done = _run_job(c, timer, "/api/pipeline", body)
            wall[f"pipeline {label}: job"].append(sum(rows["TEMPO REALE DEL JOB"]))
            wall[f"pipeline {label}: preparazione (somma)"].append(sum(rows["preparazione dei template"]))
    _print_pair(f"pipeline 4 x template mmCIF di 4560 residui, {REPEAT} coppie alternate", wall)


def _chains_pir(template_id: str, chains: str, target: str, template_seq: str) -> str:
    """PIR con un bersaglio di `len(chains)` catene uguali (`target`) sulle catene omonime di un template."""
    from bioloom.domain import sequences

    n = len(chains)
    return sequences.format_pir([
        sequences.template_entry(f"{template_id}_{chains}", "/".join([template_seq] * n), "1", chains[0], "76",
                                 chains[-1]),
        sequences.target_entry("target", "/".join([target] * n))])


def _sizes(root: Path) -> dict[str, int]:
    """Byte per tipo di file di una cartella di job: modelli, rapporti, intermedi di MODELLER, altro."""
    out: dict[str, int] = defaultdict(int)
    for p in root.rglob("*"):
        if not p.is_file():
            continue
        name = p.name
        kind = ("intermedi MODELLER" if jobs_module.is_intermediate(name) else
                "modelli (.pdb)" if name.endswith(".pdb") else
                "rapporti di qualità (.quality.json)" if name.endswith(".quality.json") else "altro")
        out[kind] += p.stat().st_size
        out["TOTALE"] += p.stat().st_size
    return dict(out)


@pytest.mark.modeller
@requires_modeller
def test_preflight_calibration(client_with_ubq, timer, tmp_path):
    """Valori dei controlli preventivi (domain/preflight.py), nello stesso processo e alternati:

    1. raffinamento: tempo del processo model.py (2 modelli, senza qualità) per livello, rapporto con "fast";
    2. lunghezza: bersaglio di 1, 2 e 4 catene di ubiquitina di lievito (76, 152, 304 residui) su copie traslate di
       1UBQ (a 40 Å l'una dall'altra: ogni catena ha i contatti di una proteina globulare, nessuno fra le catene), 1
       modello `fast`: tempo per residuo;
    3. spazio: byte dei risultati (5 modelli, qualità estesa sul migliore) e, con "Conserva file intermedi", dei file
       che il job scrive nello scratch, per 76 e 304 residui.
    """
    c = client_with_ubq
    core = c.app.state.core
    with open(_big_template(tmp_path / "BIG1.cif", n_chains=4), "rb") as fh:
        big_id = c.post("/api/templates/upload", files={"file": ("BIG1.cif", fh, "chemical/x-mmcif")}).json()["id"]
    template_seq = next(ch["sequence"] for ch in core.library.analyze(big_id)["chains"] if ch["chain"] == "A")
    rounds = max(REPEAT, 2)

    def model_body(chains: str, **extra) -> dict:
        body = {"templates": [{"id": big_id, "chains": list(chains)}], "quality": "none", **extra}
        if len(chains) == 1:
            body["target"] = {"name": "yeast_ub", "sequence": YEAST_UB}
        else:
            body["alignment"] = _chains_pir(big_id, chains, YEAST_UB, template_seq)
        return body

    def process(rows: dict[str, list[float]]) -> float:
        return sum(rows["processo model.py"])

    # 1. livelli di raffinamento, 2 modelli: primo modello (con le restrizioni) e successivo dai marcatori
    levels: dict[str, list[float]] = defaultdict(list)
    for _ in range(rounds):
        for level in ("very_fast", "fast", "normal", "thorough"):
            rows, _done = _run_job(c, timer, "/api/model", model_body("A", options={"models": 2, "refinement": level}))
            levels[f"{level}: model.py (2 modelli)"].append(process(rows))
            levels[f"{level}: restrizioni + modello 1"].append(sum(rows["  model.py: restrizioni + modello 1"]))
            levels[f"{level}: modello successivo"].append(sum(rows["  model.py: modello successivo"]))
    _print_pair(f"raffinamento, {rounds} giri alternati", levels)
    fast = statistics.median(levels["fast: modello successivo"])
    # REFINEMENT_FACTOR: media dei 2 modelli (model.py meno avvio e restrizioni), più stabile del solo modello successivo
    overhead = preflight_rules.T_STARTUP + preflight_rules.T_RESTRAINTS
    whole = {level: statistics.median(levels[f"{level}: model.py (2 modelli)"]) - overhead
             for level in ("very_fast", "fast", "normal", "thorough")}
    for level in ("very_fast", "normal", "thorough"):
        ratio = statistics.median(levels[f"{level}: modello successivo"]) / fast
        print(f"rapporto {level}/fast: modello successivo {ratio:.2f}, media dei 2 modelli "
              f"{whole[level] / whole['fast']:.2f} (REFINEMENT_FACTOR)")

    # 2. lunghezza del bersaglio
    lengths: dict[str, list[float]] = defaultdict(list)
    for _ in range(rounds):
        for chains in ("A", "AB", "ABCD"):
            rows, _done = _run_job(c, timer, "/api/model", model_body(chains, options={"models": 1,
                                                                                     "refinement": "fast"}))
            lengths[f"{76 * len(chains)} residui: model.py (1 modello)"].append(process(rows))
    _print_pair(f"lunghezza del bersaglio, {rounds} giri alternati", lengths)
    per = {n: statistics.median(lengths[f"{n} residui: model.py (1 modello)"]) for n in (76, 152, 304)}
    print("secondi per residuo: " + ", ".join(f"{n}: {per[n] / n:.4f}" for n in per))

    # 3. spazio occupato
    for keep in (False, True):
        c.put("/api/settings", json={"keep_intermediates": keep})
        for chains in ("A", "ABCD"):
            body = model_body(chains, options={"models": 5, "refinement": "fast"})
            body.update(quality="best", extended_quality=True)
            _rows, done = _run_job(c, timer, "/api/model", body)
            sizes = _sizes(core.jobs.workdir(done["id"]))
            print(f"\n=== spazio, {76 * len(chains)} residui, 5 modelli, "
                  f"{'con i file intermedi (come nello scratch)' if keep else 'risultati'}")
            for kind, n in sorted(sizes.items()):
                print(f"{kind:45s} {n / 1024:10.1f} KiB")
    c.put("/api/settings", json={"keep_intermediates": False})
