import { describe, expect, it } from "vitest";
import type { PirSecondaryEntry } from "../api/types";
import { chainBreakIssues, conflictIssues, issueSummary, issuesAt, mismatchIssues, nextIssue, uncoveredIssues, type PirIssue } from "./pirIssues";

const entry = (mismatches: PirSecondaryEntry["mismatches"]): PirSecondaryEntry => ({
  code: "t", kind: "structureX", residues: 0, ss: null, source: "dssp", method: null, template: null, matched_by: null, message: null,
  mismatches, mismatch_count: mismatches.length, problems: [],
});

describe("controlli", () => {
  it("separatori di catena non allineati", () => {
    expect(chainBreakIssues(["MK/TA", "MKT/A"])).toEqual([
      { row: null, from: 2, to: 3, severity: "err", text: "Separatore di catena «/» non nella stessa colonna in tutte le voci" },
    ]);
    expect(chainBreakIssues(["MK/TA", "MK/TA"])).toEqual([]);
  });

  it("residui del bersaglio senza template di fronte", () => {
    const issues = uncoveredIssues(["MKTAYI", "--TA--", "---AY-"], ["sequence", "structureX", "structureX"]);
    expect(issues.map((i) => [i.row, i.from, i.to, i.severity])).toEqual([[0, 0, 1, "info"], [0, 5, 5, "info"]]);
    expect(uncoveredIssues(["MK"], ["sequence"])).toEqual([]);
  });

  it("lettere diverse dal file, nella colonna attuale del residuo", () => {
    const issues = mismatchIssues(["MKTA", "-M-KT"], [null, entry([{ index: 2, letter: "K", residue: "Q A:2" }])]);
    expect(issues).toEqual([{ row: 1, from: 3, to: 3, severity: "err", text: "K nel PIR, Q A:2 nel file: MODELLER si fermerà" }]);
  });

  it("gap dentro elementi come avvisi", () => {
    expect(conflictIssues([{ row: 1, kind: "insertion", element: "H", from: 3, to: 4 }])[0]).toMatchObject({ severity: "warn", from: 3, to: 4 });
  });
});

describe("navigazione", () => {
  const list: PirIssue[] = [
    { row: 1, from: 10, to: 12, severity: "warn", text: "a" },
    { row: null, from: 3, to: 3, severity: "err", text: "b" },
    { row: 0, from: 10, to: 10, severity: "info", text: "c" },
  ];

  it("F8 va al successivo in ordine di colonna e riga, poi ricomincia", () => {
    expect(nextIssue(list, 0, 0, 1)?.text).toBe("b");
    expect(nextIssue(list, 0, 3, 1)?.text).toBe("c");
    expect(nextIssue(list, 0, 10, 1)?.text).toBe("a");
    expect(nextIssue(list, 1, 10, 1)?.text).toBe("b");
  });

  it("Maiusc+F8 va al precedente", () => {
    expect(nextIssue(list, 1, 10, -1)?.text).toBe("c");
    expect(nextIssue(list, 0, 0, -1)?.text).toBe("a");
    expect(nextIssue([], 0, 0, 1)).toBeNull();
  });

  it("problemi nella cella e riepilogo", () => {
    expect(issuesAt(list, 1, 11).map((i) => i.text)).toEqual(["a"]);
    expect(issuesAt(list, 5, 3).map((i) => i.text)).toEqual(["b"]);
    expect(issueSummary(list)).toBe("1 errore · 1 avviso · 1 nota");
  });
});
