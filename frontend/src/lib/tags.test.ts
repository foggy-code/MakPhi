import { describe, expect, it } from "vitest";
import { favoritesFirst, filterOptions, filterParams, filterValue, itemLabels, labelsKey, matchesFilter, normalizeTag, parseFilterValue, TAG_MAX_LENGTH } from "./tags";

const ok = (raw: string) => {
  const r = normalizeTag(raw);
  return r.ok ? r.tag : `ERRORE: ${r.error}`;
};

describe("normalizzazione delle etichette (come domain/labels.py)", () => {
  it("forma canonica", () => {
    expect(ok("Urgente")).toBe("urgente");
    expect(ok("  urgente ")).toBe("urgente");
    expect(ok("#Da Rivedere")).toBe("da-rivedere");
    expect(ok("a  -  b")).toBe("a-b");
    expect(ok("--piega__")).toBe("piega");
    expect(ok("Zn2+")).toBe("zn2+");
    expect(ok("ca2+ legante")).toBe("ca2+-legante");
    expect(ok("v1.2:finale")).toBe("v1.2:finale");
    expect(ok("PROTEÍNA")).toBe("proteína");
    expect(ok("proteína")).toBe("proteína"); // accento combinante → NFC
    expect(ok("x".repeat(TAG_MAX_LENGTH))).toBe("x".repeat(TAG_MAX_LENGTH));
  });

  it("rifiuti", () => {
    for (const raw of ["", "   ", "#", "---", "a/b", "x".repeat(TAG_MAX_LENGTH + 1), "+uno", "😀", 'a"b', "a'b", "a;b", "<script>"]) {
      expect(normalizeTag(raw).ok, raw).toBe(false);
    }
    expect(ok("x".repeat(33))).toContain("al massimo 32");
  });
});

describe("filtro degli elenchi", () => {
  it("valore della select e ritorno", () => {
    expect(parseFilterValue("")).toEqual({ kind: "all" });
    expect(parseFilterValue(null)).toEqual({ kind: "all" });
    expect(parseFilterValue("*preferiti")).toEqual({ kind: "favorites" });
    expect(parseFilterValue("Urgente")).toEqual({ kind: "tag", tag: "urgente" });
    expect(parseFilterValue("a/b")).toEqual({ kind: "all" });
    for (const f of [{ kind: "all" }, { kind: "favorites" }, { kind: "tag", tag: "zn2+" }] as const) {
      expect(parseFilterValue(filterValue(f))).toEqual(f);
    }
  });

  it("parametri delle rotte e filtro lato client", () => {
    expect(filterParams({ kind: "all" })).toEqual({});
    expect(filterParams({ kind: "favorites" })).toEqual({ favorite: true });
    expect(filterParams({ kind: "tag", tag: "x" })).toEqual({ tag: "x" });
    expect(matchesFilter({ tags: ["x"] }, { kind: "tag", tag: "x" })).toBe(true);
    expect(matchesFilter({}, { kind: "tag", tag: "x" })).toBe(false);
    expect(matchesFilter({ favorite: true }, { kind: "favorites" })).toBe(true);
    expect(matchesFilter({}, { kind: "all" })).toBe(true);
  });

  it("opzioni: solo le etichette del tipo, le più usate prima", () => {
    const counts = [
      { tag: "rara", count: 1, types: { job: 1 } },
      { tag: "comune", count: 5, types: { job: 3, template: 2 } },
      { tag: "altro", count: 3, types: { project: 3 } },
      { tag: "bis", count: 3, types: { job: 3 } },
    ];
    expect(filterOptions(counts, "job").map((o) => o.label)).toEqual(["Tutti", "★ Preferiti", "#bis (3)", "#comune (3)", "#rara (1)"]);
    expect(filterOptions(null, "template").map((o) => o.value)).toEqual(["", "*preferiti"]);
  });

  it("preferiti prima", () => {
    const items = [{ id: 1 }, { id: 2, favorite: true }, { id: 3 }, { id: 4, favorite: true }];
    expect(favoritesFirst(items).map((i) => i.id)).toEqual([2, 4, 1, 3]);
  });
});

describe("stella ed etichette del dettaglio, dall'elemento dell'elenco", () => {
  it("l'elenco è la fonte: un cambio fatto nella riga cambia la chiave e il dettaglio si riallinea", () => {
    // Sequenze: stella cliccata nella riga → patchLabels aggiorna l'elenco; prima il dettaglio restava su ☆ e il clic
    // successivo mandava PUT invece di DELETE
    const before = itemLabels({ id: "s1", tags: ["urgente"], favorite: false });
    const after = itemLabels({ id: "s1", tags: ["urgente"], favorite: true });
    expect(after).toEqual({ tags: ["urgente"], favorite: true });
    expect(labelsKey(before)).not.toBe(labelsKey(after));
    expect(labelsKey(after)).not.toBe(labelsKey(itemLabels({ tags: ["urgente", "lievito"], favorite: true })));
    // stessi valori in un oggetto nuovo (ogni disegno della pagina): stessa chiave, nessuna rilettura
    expect(labelsKey(itemLabels({ tags: ["urgente"], favorite: true }))).toBe(labelsKey(after));
  });

  it("elemento fuori dall'elenco (filtro, limite di 200 job): nessun valore, il dettaglio legge dal server", () => {
    expect(itemLabels(undefined)).toBeUndefined();
    expect(itemLabels(null)).toBeUndefined();
    expect(labelsKey(undefined)).toBeNull();
    expect(itemLabels({})).toEqual({ tags: [], favorite: false }); // elenco senza etichette: nessuna
  });
});
