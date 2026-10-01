import { describe, expect, it } from "vitest";
import type { PackageSummary, RerunFile } from "../api/types";
import {
  canExport, checksByLevel, contentText, EXPORTABLE_KINDS, fileDetail, fmtDelta, fmtValue, packageFallbackName, paramRows,
  RERUNNABLE_KINDS, rerunPreflight, sortFiles, toleranceText, versionRows,
} from "./rerun";

// Forme come le produce il backend (services/reproducibility.py, tests/test_reproducibility.py)
function summary(extra: Partial<PackageSummary> = {}): PackageSummary {
  return {
    package_id: "a".repeat(32), format_version: 1, created_at: "2026-10-01T10:00:00+00:00",
    bioloom: { version: "4.1.0", image: "4.1.0" },
    environment: { python: "3.12.3", libraries: { biopython: "1.88", numpy: "2.5.3" },
      tools: { modeller: { label: "MODELLER", available: true, version: "10.8" }, dssp: { label: "DSSP", available: true, version: "4.2.2" },
        prosa: { label: "ProSA II 2003", available: false, version: null } } },
    job: { id: "0123456789abcdef", kind: "model", title: "Modello yeast_ub · 1UBQ", status: "completed", duration_seconds: 9.2 },
    kind_label: "modellazione", params: { project_id: "p1", target: { name: "yeast_ub" }, quality: "best", options: { models: 2 } },
    seeds: { modeller_rand_seed: -8123, note: "Environ(rand_seed=…)" }, settings: null, cache: null, remote: [], databases: [], notes: [],
    rerun: { supported: true, reason: null },
    files: { count: 9, bytes: 123456, by_role: { params: { count: 1, bytes: 10 }, template: { count: 1, bytes: 50000 },
      template_prepared: { count: 1, bytes: 40000 }, alignment: { count: 1, bytes: 300 }, output: { count: 3, bytes: 30000 },
      script: { count: 2, bytes: 3000 }, log: { count: 1, bytes: 100 } }, list: [] },
    current: { bioloom: { version: "4.1.0", image: "4.1.0" },
      environment: { python: "3.12.3", libraries: { biopython: "1.88", numpy: "2.5.3" },
        tools: { modeller: { label: "MODELLER", available: true, version: "10.8" }, dssp: { label: "DSSP", available: true, version: "4.2.2" } } } },
    checks: [], manifest_sha256: "f".repeat(64), ...extra,
  };
}

describe("tipi con un pacchetto", () => {
  it("solo i job completati dei tipi esportabili; ricerca e QMEAN non si rieseguono", () => {
    expect(canExport("model", "completed")).toBe(true);
    expect(canExport("model", "running")).toBe(false);
    expect(canExport("pipeline", "completed")).toBe(false);
    expect(canExport("rerun", "completed")).toBe(false);
    expect(EXPORTABLE_KINDS).toContain("search");
    expect(RERUNNABLE_KINDS).not.toContain("search");
    expect(RERUNNABLE_KINDS).not.toContain("qmean");
    expect(RERUNNABLE_KINDS).toEqual(expect.arrayContaining(["model", "loop", "align2d", "quality", "align", "analysis", "refine"]));
  });
});

describe("anteprima del pacchetto", () => {
  it("riassume il contenuto per ruolo", () => {
    expect(contentText(summary().files.by_role)).toBe("4 file d'ingresso, 3 risultati, 2 script MODELLER, log");
    expect(contentText({ output: { count: 1, bytes: 1 } })).toBe("1 risultato");
    expect(contentText({})).toBe("nessun file");
  });

  it("confronta le versioni riga per riga", () => {
    const same = versionRows(summary(), summary().current);
    expect(same.every((r) => r.same)).toBe(true);
    expect(same.map((r) => r.label)).toEqual(["BioLoom", "Immagine Docker", "Python", "Biopython", "NumPy", "MODELLER", "DSSP", "ProSA II 2003"]);
    const current = { bioloom: { version: "4.2.0", image: null }, environment: { python: "3.12.3", libraries: { biopython: "1.88", numpy: "2.5.3" }, tools: { dssp: { label: "DSSP", available: true, version: "4.4.0" } } } };
    const rows = Object.fromEntries(versionRows(summary(), current).map((r) => [r.label, r]));
    expect(rows.BioLoom).toMatchObject({ packaged: "4.1.0", current: "4.2.0", same: false });
    expect(rows["Immagine Docker"]).toMatchObject({ packaged: "4.1.0", current: "—", same: true });
    expect(rows.MODELLER).toMatchObject({ current: "non installato", same: false });
    expect(rows.DSSP).toMatchObject({ packaged: "4.2.2", current: "4.4.0", same: false });
    expect(rows["ProSA II 2003"]).toMatchObject({ packaged: "non installato", same: true }); // non usato dal calcolo
  });

  it("parametri leggibili senza il progetto, valori lunghi abbreviati", () => {
    const rows = paramRows({ ...summary().params, alignment: ">P1;x\n" + "A".repeat(300) });
    expect(rows.map(([k]) => k)).toEqual(["target", "quality", "options", "alignment"]);
    expect(rows.find(([k]) => k === "alignment")?.[1].endsWith("…")).toBe(true);
    expect(paramRows({ a: 1, b: 2, c: 3 }, 2)).toHaveLength(2);
    expect(paramRows(null)).toEqual([]);
  });

  it("controlli preventivi solo per un pacchetto rieseguibile", () => {
    expect(rerunPreflight(summary())).toEqual({ kind: "rerun", params: { package_id: "a".repeat(32) } });
    expect(rerunPreflight(summary({ rerun: { supported: false, reason: "remoto" } }))).toBeNull();
    expect(rerunPreflight(null)).toBeNull();
  });

  it("divide gli esiti per livello", () => {
    const by = checksByLevel([{ level: "error", code: "rerun_unsupported", message: "x" }, { level: "warning", code: "rerun_version_bioloom", message: "y" }, { level: "info", code: "rerun_remote_input", message: "z" }]);
    expect([by.error.length, by.warning.length, by.info.length]).toEqual([1, 1, 1]);
    expect(checksByLevel(undefined).error).toEqual([]);
  });
});

describe("confronto", () => {
  it("formatta valori e differenze", () => {
    expect(fmtValue(-8123.456789)).toBe("-8123.4568");
    expect(fmtValue(0.0000123)).toBe("1.230e-5");
    expect(fmtValue(76)).toBe("76");
    expect(fmtValue(null)).toBe("—");
    expect(fmtValue(true)).toBe("sì");
    expect(fmtValue("x".repeat(100), 10)).toBe("xxxxxxxxx…");
    expect(fmtDelta({ delta: 0 })).toBe("Δ 0");
    expect(fmtDelta({ delta: 12.5 })).toBe("Δ +12.5");
    expect(fmtDelta({ delta: -0.0184648 })).toBe("Δ -0.0185");
    expect(fmtDelta({ delta: null })).toBe("");
  });

  const files: RerunFile[] = [
    { path: "result.json", status: "equivalent", role: "output", principal: true, diff: { numbers: 3, beyond_tolerance: 0, max_delta: 0.0001, max_delta_at: "dope", text: 0, structure: 0, examples: [] } },
    { path: "model.py", status: "different", role: "script", principal: false, diff: { numbers: 0, beyond_tolerance: 0, max_delta: 0, max_delta_at: null, text: 1, structure: 0, examples: [{ where: "riga 3", original: "a", rerun: "b" }] } },
    { path: "m.B99990001.pdb", status: "different", role: "output", principal: true, coordinates: { atoms: 602, rerun_atoms: 602, common: 602, only_original: 0, only_rerun: 0, rmsd: 0.109, max_deviation: 1.7134, max_atom: "OE1 GLN A62", within: false } },
    { path: "nuovo.txt", status: "extra", role: "output", principal: false },
    { path: "models.json", status: "missing", role: "output", principal: true },
  ];

  it("ordina i file: mancanti, diversi (i principali prima), equivalenti, in più", () => {
    expect(sortFiles(files).map((f) => f.path)).toEqual(["models.json", "m.B99990001.pdb", "model.py", "result.json", "nuovo.txt"]);
    expect(sortFiles(null)).toEqual([]);
  });

  it("descrive le differenze quantificate", () => {
    const by = Object.fromEntries(files.map((f) => [f.path, fileDetail(f)]));
    expect(by["m.B99990001.pdb"]).toBe("RMSD 0.109 Å, scarto massimo 1.7134 Å (OE1 GLN A62)");
    expect(by["result.json"]).toBe("numeri entro la tolleranza (Δ massimo 0.0001)");
    expect(by["model.py"]).toBe("1 testo diverso; es. riga 3: a → b");
    expect(by["models.json"]).toBe("non prodotto dalla riesecuzione");
    expect(by["nuovo.txt"]).toBe("prodotto solo dalla riesecuzione");
    const beyond = fileDetail({ path: "r.json", status: "different", role: "output", principal: true, diff: { numbers: 2, beyond_tolerance: 1, max_delta: 23.4568, max_delta_at: "dope", text: 0, structure: 1, examples: [{ where: "dope", original: -8123.4568, rerun: -8100 }] } });
    expect(beyond).toBe("1 numero oltre la tolleranza (Δ massimo 23.4568 in dope), 1 differenza di struttura; es. dope: -8123.4568 → -8100");
  });

  it("nome di riserva del pacchetto e nota sulle tolleranze", () => {
    expect(packageFallbackName("model", "abc")).toBe("bioloom_riproducibile_model_abc.zip");
    const text = toleranceText({ coordinates_angstrom: 0.002, score_abs: 0.001, score_rel: 1e-6, ignored: ["cached", "timings"] });
    expect(text).toContain("coordinate 0.002 Å per atomo");
    expect(text).toContain("|Δ| ≤ max(0.001; 1e-6·|valore|)");
    expect(text).toContain("Non confrontati: cached, timings, data di MODELLER");
  });
});
