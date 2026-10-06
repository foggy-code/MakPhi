import { describe, expect, it } from "vitest";
import { parsePir } from "./pir";
import { alignmentDiff, diffCells, partners, ranges } from "./pirDiff";

const pir = (t: string, p: string, code = "tpl") => parsePir(`>P1;t\nsequence:t::::::::\n${t}*\n\n>P1;${code}\nstructureX:${code}::::::::\n${p}*\n`);

describe("confronto con la versione salvata", () => {
  it("coppie bersaglio–template", () => {
    expect(partners("MKT-A", "M-TYA")).toEqual([0, -1, 1, 3]);
  });

  it("un gap aggiunto davanti a tutte le voci non cambia nulla", () => {
    expect(alignmentDiff(pir("MKTA", "MKSA"), pir("-MKTA", "-MKSA")).templates).toEqual([]);
  });

  it("residui allineati diversamente e celle da evidenziare", () => {
    const saved = pir("MKTAY-", "MKT-AY");
    const now = pir("MKTAY-", "MKTA-Y");
    const diff = alignmentDiff(saved, now);
    expect(diff.templates).toEqual([{ row: 1, code: "tpl", changed: [3, 4] }]);
    expect([...diffCells(diff, now.map((e) => e.sequence), 0)].sort()).toEqual(["0:3", "0:4", "1:3"]);
  });

  it("voci non confrontabili", () => {
    expect(alignmentDiff(pir("MKTA", "MKSA"), pir("MKTA", "MKSAA")).notComparable).toEqual(["tpl"]);
    expect(alignmentDiff(pir("MKTA", "MKSA"), pir("MKTA", "MKSA", "altro")).notComparable).toEqual(["altro"]);
    expect(alignmentDiff(pir("MKTA", "MKSA"), pir("MKTAA", "MKSA-")).notComparable).toEqual(["t"]);
  });

  it("intervalli leggibili", () => {
    expect(ranges([3, 4, 5, 9, 11, 12])).toBe("4–6, 10, 12–13");
    expect(ranges([])).toBe("");
  });
});
