import { describe, expect, it } from "vitest";
import { annotationColumns, readAnnotations, residuesInColumns } from "./pirAnnotations";

describe("annotazioni", () => {
  it("residui del bersaglio nelle colonne selezionate", () => {
    expect(residuesInColumns("MK--TAY", 1, 4)).toEqual({ from: 2, to: 3 });
    expect(residuesInColumns("MK--TAY", 2, 3)).toBeNull();
  });

  it("seguono i residui quando i gap si spostano", () => {
    expect(annotationColumns("MK--TAY", { from: 2, to: 3 })).toEqual({ from: 1, to: 4 });
    expect(annotationColumns("--MKTAY", { from: 2, to: 3 })).toEqual({ from: 3, to: 4 });
    expect(annotationColumns("MK", { from: 2, to: 3 })).toBeNull();
  });

  it("lettura dai metadati", () => {
    const ok = { id: "a", label: "sito attivo", from: 2, to: 4, color: "#22c55e" };
    expect(readAnnotations({ annotations: [ok, { id: "b", from: 0 }, null, { ...ok, to: 1 }] })).toEqual([ok]);
    expect(readAnnotations({})).toEqual([]);
  });
});
