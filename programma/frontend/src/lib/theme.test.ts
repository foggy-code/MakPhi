import { describe, expect, it } from "vitest";
import { DEFAULT_THEME, initialTheme, isThemePref, nextThemePref, parseThemePref, resolveTheme, THEME_KEY, THEME_OPTIONS } from "./theme";

describe("scelta del tema", () => {
  it("legge i valori salvati dalle versioni precedenti e quello nuovo", () => {
    expect(parseThemePref('"dark"')).toBe("dark");
    expect(parseThemePref('"light"')).toBe("light");
    expect(parseThemePref('"system"')).toBe("system");
    expect(parseThemePref("system")).toBe("system"); // testo semplice (scritto a mano)
    expect(parseThemePref(" light ")).toBe("light");
  });

  it("valore assente, illeggibile o sconosciuto: il predefinito (scuro, come prima)", () => {
    expect(DEFAULT_THEME).toBe("dark");
    expect(parseThemePref(null)).toBe("dark");
    expect(parseThemePref(undefined)).toBe("dark");
    expect(parseThemePref('"blu"')).toBe("dark");
    expect(parseThemePref("{")).toBe("dark");
    expect(parseThemePref("42")).toBe("dark");
    expect(parseThemePref("null", "light")).toBe("light");
  });

  it("tema effettivo: la scelta, oppure quello del sistema", () => {
    expect(resolveTheme("dark", false)).toBe("dark");
    expect(resolveTheme("light", true)).toBe("light");
    expect(resolveTheme("system", true)).toBe("dark");
    expect(resolveTheme("system", false)).toBe("light");
  });

  it("il pulsante a ciclo passa per le tre scelte", () => {
    expect(nextThemePref("dark")).toBe("light");
    expect(nextThemePref("light")).toBe("system");
    expect(nextThemePref("system")).toBe("dark");
    expect(THEME_OPTIONS.map((o) => o.value)).toEqual(["dark", "light", "system"]);
    expect(isThemePref("system") && !isThemePref("auto") && !isThemePref(1)).toBe(true);
  });

  it("tema prima del render dal localStorage (senza matchMedia il sistema vale scuro)", () => {
    const storage = (value: string | null) => ({ getItem: (k: string) => (k === THEME_KEY ? value : null) });
    expect(initialTheme(storage('"light"'))).toBe("light");
    expect(initialTheme(storage('"system"'))).toBe("dark");
    expect(initialTheme(storage(null))).toBe("dark");
    expect(initialTheme(null)).toBe("dark");
    const broken = { getItem: () => { throw new Error("storage non disponibile"); } };
    expect(initialTheme(broken)).toBe("dark");
  });
});
