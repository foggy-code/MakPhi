import { describe, expect, it } from "vitest";
import type { JobKind, JobStatus, ModelEntry } from "../api/types";
import {
  availableMetrics, dopePerResidue, flowProgress, greeting, jobCounts, kindBreakdown, logTail, matchesStatus, modelPoints, modelsData,
  nextFlowStep, pipelineStatusParts, plural, scoreRange, statusColor,
} from "./shellView";
import { JOB_STATUS_TONE, plural as formatPlural } from "./format";

const job = (kind: JobKind, status: JobStatus) => ({ kind, status });

const model = (file: string, dope: number | null, extra: Partial<ModelEntry> = {}): ModelEntry => ({
  file, dope, zdope: null, molpdf: 1000, ga341: null, failure: null, kind: "model", best: false, ...extra,
});

describe("testi della Panoramica", () => {
  it("plurale e saluto secondo l'ora", () => {
    expect(plural(1, "sequenza", "sequenze")).toBe("1 sequenza");
    expect(plural(0, "sequenza", "sequenze")).toBe("0 sequenze");
    expect(greeting(8)).toBe("Buongiorno");
    expect(greeting(15)).toBe("Buon pomeriggio");
    expect(greeting(21)).toBe("Buonasera");
    expect(greeting(2)).toBe("Buonasera");
  });
});

describe("indicatori dei job", () => {
  const jobs = [
    job("model", "completed"), job("model", "completed"), job("pipeline", "completed"), job("loop", "failed"),
    job("quality", "completed"), job("pockets", "completed"), job("search", "running"), job("search", "completed"), job("selftest", "completed"),
  ];
  it("attivi, completati e falliti per famiglia", () => {
    const c = jobCounts(jobs);
    expect(c.total).toBe(9);
    expect(c.active).toBe(1);
    expect(c.done).toEqual({ prep: 1, model: 3, analysis: 2, service: 1 });
    expect(c.failed.model).toBe(1);
    expect(kindBreakdown(c, "model")).toEqual([
      { kind: "model", label: "Modellazione", count: 2 },
      { kind: "pipeline", label: "Pipeline batch", count: 1 },
    ]);
  });
  it("passi del flusso: il prossimo passo è il primo senza lavoro", () => {
    const c = jobCounts([job("model", "completed")]);
    const p = flowProgress({ sequences: 2 }, c);
    expect(p.map((s) => s.label)).toEqual(["2 sequenze", "0 ricerche", "0 allineamenti", "1 modellazione", "0 analisi"]);
    expect(nextFlowStep(p)).toBe(1);
    expect(nextFlowStep(flowProgress(null, jobCounts([])))).toBe(0);
  });
  it("passi del flusso: una pipeline fa allineamento e modello; template e qualità solo come nota", () => {
    const p = flowProgress({ sequences: 1 }, jobCounts([job("pipeline", "completed"), job("pipeline", "completed")]));
    expect(p.map((s) => s.count)).toEqual([1, 0, 2, 2, 0]);
    expect(p.map((s) => s.pipelines)).toEqual([0, 2, 0, 0, 2]);
    expect(nextFlowStep(p)).toBe(1);
    const all = flowProgress({ sequences: 1 }, jobCounts([job("pipeline", "completed"), job("search", "completed"), job("quality", "completed")]));
    expect(all.every((s) => s.count > 0)).toBe(true);
    expect(nextFlowStep(all)).toBe(-1);
  });
  it("filtro per stato", () => {
    expect(matchesStatus({ status: "queued" }, "active")).toBe(true);
    expect(matchesStatus({ status: "completed" }, "active")).toBe(false);
    expect(matchesStatus({ status: "cancelled" }, "problems")).toBe(true);
    expect(matchesStatus({ status: "failed" }, "all")).toBe(true);
  });
  it("estratto del log: ultime righe, senza quelle vuote in fondo", () => {
    expect(logTail("a\nb\nc\nd\n\n", 2)).toBe("c\nd");
    expect(logTail("", 3)).toBe("");
  });
  it("estratto del log: come dividere tutto in righe, ma cercando il taglio dalla fine", () => {
    const naive = (log: string, n: number) => log.replace(/\s+$/, "").split(/\r?\n/).slice(-n).join("\n");
    for (const log of ["\nb", "a\r\nb\r\nc\r\n", "solo una riga", "x\n\n\ny\n", "p\rq\nr", "\n\n\n"]) {
      for (const n of [1, 2, 3, 10]) expect(logTail(log, n), JSON.stringify([log, n])).toBe(naive(log, n));
    }
    const long = Array.from({ length: 20000 }, (_, i) => `riga ${i}`).join("\n");
    expect(logTail(long, 3)).toBe("riga 19997\nriga 19998\nriga 19999");
  });
});

describe("pipeline", () => {
  it("composizione degli stati in percentuale, nell'ordine fisso, senza gli stati assenti", () => {
    const parts = pipelineStatusParts({ failed: 1, completed: 3, pending: 0 });
    expect(parts.map((p) => p.label)).toEqual(["Completato (3)", "Fallito (1)"]);
    expect(parts[0].value).toBeCloseTo(75);
    expect(pipelineStatusParts({})).toEqual([]);
  });
  it("colori degli stati: gli stessi toni dei badge (JOB_STATUS_TONE)", () => {
    const parts = pipelineStatusParts({ completed: 1, modeling: 1, cancelled: 1, filtered: 1, failed: 1 });
    expect(parts.map((p) => p.color)).toEqual(["var(--ok)", "var(--accent)", "var(--err)", "var(--warn)", "var(--warn)"]);
    for (const s of Object.keys(JOB_STATUS_TONE)) expect(statusColor(s)).toBe(`var(--${JOB_STATUS_TONE[s]})`);
    expect(statusColor("sconosciuto")).toBe("var(--muted)");
    expect(plural).toBe(formatPlural); // un solo formattatore, riesportato
  });
});

describe("punteggi dei modelli", () => {
  const models = [
    model("m1.pdb", -900, { zdope: -0.5 }),
    model("m2.pdb", -1000, { best: true, zdope: -1.2, quality: { grade: "B", grade_score: 78, rama_favored: 96, rama_outliers: 0.5, clashscore: 3, helix_pct: 40, strand_pct: 20, dope: -1000, zdope: -1.2, residues: 100 } }),
    model("m3.pdb", null, { failure: "errore" }),
  ];
  it("metriche disponibili: solo quelle con un valore nei modelli riusciti", () => {
    expect(availableMetrics(models)).toEqual(["dope", "zdope", "molpdf", "grade"]);
  });
  it("ordine della tabella o dal migliore (i falliti in fondo)", () => {
    expect(modelPoints(models, "dope", "table").map((p) => p.n)).toEqual([1, 2, 3]);
    expect(modelPoints(models, "dope", "value").map((p) => p.file)).toEqual(["m2.pdb", "m1.pdb", "m3.pdb"]);
    expect(modelPoints(models, "dope", "value")[2]).toMatchObject({ value: null, failed: true });
  });
  it("asse y con margine, dominio fisso e soglie comprese", () => {
    const r = scoreRange([-1000, -900]);
    expect(r.min).toBeLessThan(-1000);
    expect(r.max).toBeGreaterThan(-900);
    expect(scoreRange([0.5], [0.7], [0, 1])).toEqual({ min: 0, max: 1 });
    expect(scoreRange([-0.5], [-1]).min).toBeLessThan(-1);
    expect(scoreRange([])).toEqual({ min: 0, max: 1 });
  });
  it("DOPE per residuo e dati della tabella", () => {
    expect(dopePerResidue(models[1])).toBe(-10);
    expect(dopePerResidue(models[0])).toBeNull();
    const data = modelsData(models);
    expect(data.rows).toHaveLength(3);
    expect(data.rows[1]).toMatchObject({ n: 2, file: "m2.pdb", grade: "B", best: "sì" });
    expect(data.rows[2].failure).toBe("errore");
  });
});
