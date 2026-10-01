import { describe, expect, it } from "vitest";
import { fullRange, revealScrollTop, rowOffsets, shouldVirtualize, VIRTUAL_THRESHOLD, visibleRange } from "./virtual";

describe("finestra della virtualizzazione", () => {
  it("si attiva solo oltre la soglia", () => {
    expect(VIRTUAL_THRESHOLD).toBe(200);
    expect(shouldVirtualize(200)).toBe(false);
    expect(shouldVirtualize(201)).toBe(true);
    expect(shouldVirtualize(50, 10)).toBe(true);
    expect(fullRange(7)).toEqual({ start: 0, end: 7, padTop: 0, padBottom: 0 });
  });

  it("posizioni cumulative con altezza fissa o per indice", () => {
    expect(rowOffsets(3, 20)).toEqual([0, 20, 40, 60]);
    expect(rowOffsets(4, (i) => (i % 2 ? 12 : 19))).toEqual([0, 19, 31, 50, 62]);
    expect(rowOffsets(0, 20)).toEqual([0]);
  });

  it("righe visibili con margine sopra e sotto, spaziatori che tengono l'altezza totale", () => {
    const offsets = rowOffsets(1000, 20); // 20 000 px
    // in cima: righe 0–19 visibili (400 px), 5 di margine sotto
    expect(visibleRange(offsets, 0, 400, 5)).toEqual({ start: 0, end: 25, padTop: 0, padBottom: 20000 - 25 * 20 });
    // a metà: scrollTop 10 010 → prima riga visibile 500 (tagliata), ultima 520
    const mid = visibleRange(offsets, 10010, 400, 5);
    expect(mid).toEqual({ start: 495, end: 526, padTop: 495 * 20, padBottom: 20000 - 526 * 20 });
    expect(mid.padTop + (mid.end - mid.start) * 20 + mid.padBottom).toBe(20000);
    // in fondo e oltre il fondo (scrollTop stantio dopo un filtro che accorcia la lista)
    expect(visibleRange(offsets, 19600, 400, 5)).toEqual({ start: 975, end: 1000, padTop: 19500, padBottom: 0 });
    expect(visibleRange(offsets, 50000, 400, 5)).toEqual({ start: 994, end: 1000, padTop: 19880, padBottom: 0 });
    // scrollTop negativo (intestazione sottratta) e lista vuota
    expect(visibleRange(offsets, -36, 400, 0).start).toBe(0);
    expect(visibleRange(rowOffsets(0, 20), 0, 400)).toEqual({ start: 0, end: 0, padTop: 0, padBottom: 0 });
  });

  it("righe di altezza diversa (blocchi dell'allineamento con lo spazio fra i blocchi)", () => {
    // blocco = righello + 3 sequenze (19 px) + spazio di 12 px
    const height = (i: number) => (i % 5 === 4 ? 12 : 19);
    const offsets = rowOffsets(500, height); // 100 blocchi da 88 px
    expect(offsets[500]).toBe(8800);
    const r = visibleRange(offsets, 880, 176, 0); // blocchi 10 e 11 esatti
    expect([r.start, r.end, r.padTop]).toEqual([50, 60, 880]);
  });

  it("porta in vista la riga selezionata scorrendo solo il contenitore", () => {
    const offsets = rowOffsets(1000, 20);
    const view = { scrollTop: 0, height: 436, header: 36 }; // 400 px di righe sotto l'intestazione fissa
    expect(revealScrollTop(offsets, 5, view)).toBeNull(); // già visibile
    expect(revealScrollTop(offsets, 19, view)).toBeNull(); // ultima riga intera
    // fuori vista: al centro dello spazio sotto l'intestazione
    expect(revealScrollTop(offsets, 500, view)).toBe(500 * 20 - (400 - 20) / 2);
    // vicino all'inizio e alla fine: nei limiti dello scorrimento
    expect(revealScrollTop(offsets, 1, { ...view, scrollTop: 5000 })).toBe(0);
    expect(revealScrollTop(offsets, 999, view)).toBe(36 + 20000 - 436);
    // riga coperta dall'intestazione (appena sopra il bordo visibile)
    expect(revealScrollTop(offsets, 10, { ...view, scrollTop: 210 })).not.toBeNull();
    // indice fuori dalla lista
    expect(revealScrollTop(offsets, -1, view)).toBeNull();
    expect(revealScrollTop(offsets, 1000, view)).toBeNull();
  });
});
