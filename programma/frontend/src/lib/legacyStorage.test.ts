import { describe, expect, it } from "vitest";
import { migrateLegacyKeys } from "./legacyStorage";

function memoryStorage(initial: Record<string, string>): Storage {
  const data = new Map(Object.entries(initial));
  return {
    get length() {
      return data.size;
    },
    key: (i: number) => [...data.keys()][i] ?? null,
    getItem: (k: string) => data.get(k) ?? null,
    setItem: (k: string, v: string) => void data.set(k, v),
    removeItem: (k: string) => void data.delete(k),
    clear: () => data.clear(),
  };
}

describe("preferenze salvate da BioLoom", () => {
  it("passano alle chiavi di MakPhi e le vecchie si tolgono", () => {
    const s = memoryStorage({ "bioloom.theme": '"light"', "bioloom.pir.side": "0", altro: "x" });
    migrateLegacyKeys(s);
    expect(s.getItem("makphi.theme")).toBe('"light"');
    expect(s.getItem("makphi.pir.side")).toBe("0");
    expect(s.getItem("bioloom.theme")).toBeNull();
    expect(s.getItem("bioloom.pir.side")).toBeNull();
    expect(s.getItem("altro")).toBe("x");
  });

  it("un valore già salvato con il nome nuovo resta quello", () => {
    const s = memoryStorage({ "bioloom.theme": '"light"', "makphi.theme": '"dark"' });
    migrateLegacyKeys(s);
    expect(s.getItem("makphi.theme")).toBe('"dark"');
    expect(s.getItem("bioloom.theme")).toBeNull();
  });

  it("senza localStorage o con errori di scrittura non si ferma", () => {
    expect(() => migrateLegacyKeys(null)).not.toThrow();
    const s = memoryStorage({ "bioloom.theme": '"light"' });
    s.setItem = () => {
      throw new Error("QuotaExceededError");
    };
    expect(() => migrateLegacyKeys(s)).not.toThrow();
  });
});
