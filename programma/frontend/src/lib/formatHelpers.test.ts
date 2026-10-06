import { describe, expect, it } from "vitest";
import { fmtSigned, fmtUnit, JOB_STATUS_TONE, plural } from "./format";

// Formattatori condivisi (prima ricopiati nelle lib d'area) e tono unico degli stati dei job.
describe("formattatori comuni", () => {
  it("fmtSigned: segno esplicito e meno tipografico", () => {
    expect(fmtSigned(1.234, 1)).toBe("+1,2");
    expect(fmtSigned(-0.8, 2)).toBe("−0,80");
    expect(fmtSigned(0, 1)).toBe("0,0");
  });
  it("fmtUnit: valore e unità, trattino se manca", () => {
    expect(fmtUnit(2.345, 2, "Å")).toBe("2,35 Å");
    expect(fmtUnit(null, 2, "Å")).toBe("—");
    expect(fmtUnit(undefined, 0, "Å³")).toBe("—");
    expect(fmtUnit(Number.NaN, 1, "kJ/mol")).toBe("—");
  });
  it("plural", () => {
    expect(plural(1, "sequenza", "sequenze")).toBe("1 sequenza");
    expect(plural(0, "sequenza", "sequenze")).toBe("0 sequenze");
    expect(plural(3, "residuo", "residui")).toBe("3 residui");
  });
  it("JOB_STATUS_TONE: lo stesso tono per badge e barre degli stati", () => {
    expect(JOB_STATUS_TONE.cancelled).toBe("warn");
    expect(JOB_STATUS_TONE.modeling).toBe("accent");
    expect(JOB_STATUS_TONE.completed).toBe("ok");
    expect(JOB_STATUS_TONE.failed).toBe("err");
  });
});
