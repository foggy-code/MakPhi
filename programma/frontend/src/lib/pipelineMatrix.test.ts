import { describe, expect, it } from "vitest";
import type { PipelineMatrix, PipelineMatrixCell } from "../api/types";
import { availableMetrics, BAD, cellColor, cellStatusText, cellValue, GOOD } from "./pipelineMatrix";

function cell(over: Partial<PipelineMatrixCell> = {}): PipelineMatrixCell {
  return { idx: 0, status: "completed", dope: -7000, zdope: -1.2, ga341: 0.9, molpdf: 300, identity: 90, coverage: 100,
    grade: null, grade_score: null, best_model: "items/0000_a__T1/a.pdb", best: false, error: null, ...over };
}

const matrix: PipelineMatrix = {
  sequences: ["a", "b"],
  templates: ["T1_A", "T2_A"],
  template_ids: ["T1", "T2"],
  summary: { pairs: 4, completed: 2, failed: 1, open: 1, closed: 1, wins: [0, 1] },
  cells: [
    [cell({ dope: -7000, zdope: -1.0 }), cell({ idx: 1, dope: -7600, zdope: -1.8, best: true, grade: "A", grade_score: 92 })],
    [cell({ idx: 2, status: "failed", dope: null, zdope: null, ga341: null, molpdf: null, identity: null, coverage: null, best_model: null }),
      cell({ idx: 3, status: "modeling", dope: null, zdope: null, ga341: null, molpdf: null, identity: 40, coverage: 80, best_model: null })],
  ],
};

describe("matrice M × N", () => {
  it("legge i valori e le metriche disponibili", () => {
    expect(cellValue(matrix.cells[0][1], "zdope")).toBe(-1.8);
    expect(cellValue(matrix.cells[1][0], "zdope")).toBeNull();
    expect(cellValue(null, "dope")).toBeNull();
    expect(availableMetrics(matrix)).toEqual(["zdope", "dope", "ga341", "molpdf", "identity", "coverage", "grade_score"]);
    expect(availableMetrics({ ...matrix, cells: [[cell({ grade_score: null })]] })).not.toContain("grade_score");
  });

  it("colora DOPE rispetto alla riga e zDOPE con la scala fissa", () => {
    const row = matrix.cells[0];
    expect(cellColor(row, row[1], "dope")).toBe(GOOD); // il più basso della sequenza
    expect(cellColor(row, row[0], "dope")).toBe(BAD);
    expect(cellColor(row, row[1], "zdope")).not.toBe(cellColor(row, row[0], "zdope"));
    expect(cellColor(matrix.cells[1], matrix.cells[1][0], "dope")).toBeNull();
    expect(cellColor([row[0]], row[0], "dope")).toBe(GOOD); // un solo valore nella riga
  });

  it("scrive lo stato delle celle senza valore", () => {
    expect(cellStatusText(matrix.cells[1][0])).toBe("fallita");
    expect(cellStatusText(matrix.cells[1][1])).toBe("in corso");
    expect(cellStatusText(null)).toBe("—");
  });
});
