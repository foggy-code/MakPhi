import { describe, expect, it } from "vitest";
import type { AlignResult, ModelEntry, PreparedTemplate, StructuralPair } from "../api/types";
import { colorFor, legendFor, NO_VALUE_COLOR } from "./colorscale";
import {
  breaksBefore, compareJobView, compareLink, compareParams, compareTargets, comparisonColoring, comparisonOutdated, decodeSource, deviatingSegments,
  DISTANCE_SCALE, encodeSource, jobSources, LDDT_SCALE, linkedSources, pairHighlight, pairIndexOf, pairRows, REFERENCE_COLOR, rowScrollDelta,
  seriesPath, tmVerdict,
} from "./compare";
import { coloringMap, describeResidue, hasMissing } from "./residueColoring";

function pair(resnum: number | string, distance: number, lddt: number | null = 1, extra: Partial<StructuralPair> = {}): StructuralPair {
  return { chain_a: "A", resnum_a: String(resnum), aa_a: "G", chain_b: "A", resnum_b: String(resnum), aa_b: "G", distance, lddt, ...extra };
}

// 1UBQ con il tratto 31–37 spostato di 6 Å (valori dei test del backend: distanze 6 Å e lDDT ~0,2 solo nel tratto)
const SHIFTED: StructuralPair[] = Array.from({ length: 76 }, (_, i) => {
  const n = i + 1;
  const inside = n >= 31 && n <= 37;
  return pair(n, inside ? 6 : 0, inside ? 0.2 : n > 50 ? 1 : 0.85);
});

describe("sorgenti nei link", () => {
  it("template, modello di un job e file caricato, andata e ritorno", () => {
    expect(encodeSource({ type: "template", id: "1UBQ" })).toBe("template:1UBQ");
    expect(encodeSource({ type: "job", job_id: "abc123", file: "items/001_x/model.B99990001.pdb" })).toBe("job:abc123:items/001_x/model.B99990001.pdb");
    expect(encodeSource({ type: "upload", file: "f00d_mio.pdb" })).toBe("upload:f00d_mio.pdb");
    expect(encodeSource({ type: "job", job_id: "abc" })).toBeNull();
    expect(encodeSource(null)).toBeNull();
    expect(decodeSource("job:abc123:items/001_x/model:1.pdb", "B")).toEqual({ type: "job", job_id: "abc123", file: "items/001_x/model:1.pdb", chain: "B" });
    expect(decodeSource("template:1UBQ", " ")).toEqual({ type: "template", id: "1UBQ" });
    expect(decodeSource("upload:f00d_mio.pdb")).toEqual({ type: "upload", file: "f00d_mio.pdb" });
    for (const bad of ["", "template:", "job:abc", "job:abc:", "job::x.pdb", "altro:1UBQ", ":x", "1UBQ"]) expect(decodeSource(bad)).toBeNull();
  });

  it("parametri della pagina Confronto, con le catene solo se indicate", () => {
    const a = { type: "template" as const, id: "1UBQ", chain: "A" };
    const b = { type: "job" as const, job_id: "j1", file: "t.B99990001.pdb" };
    expect(compareParams(a, b, "job9").toString()).toBe("a=template%3A1UBQ&ca=A&b=job%3Aj1%3At.B99990001.pdb&job=job9");
    expect(compareLink(null, b)).toBe("/compare?b=job%3Aj1%3At.B99990001.pdb");
    expect(compareLink(null, null)).toBe("/compare");
    const back = linkedSources(new URLSearchParams(compareParams(a, b, "job9")));
    expect(back).toEqual({ a, b, job: "job9" });
  });

  it("strutture dai parametri di un job di allineamento", () => {
    expect(jobSources({ structures: [{ type: "template", id: "1UBQ", chain: "A" }, { type: "upload", file: "x.pdb" }] }))
      .toEqual([{ type: "template", id: "1UBQ", chain: "A" }, { type: "upload", file: "x.pdb" }]);
    expect(jobSources({ structures: [{ type: "template", id: "1UBQ" }] })).toBeNull();
    expect(jobSources({ structures: [{ type: "template" }, { type: "upload", file: "x.pdb" }] })).toBeNull();
    expect(jobSources({ records: [] })).toBeNull();
    expect(jobSources(null)).toBeNull();
  });

  it("dalla tabella dei modelli: template usati e altri modelli dello stesso job, il modello è B", () => {
    const models = [
      { file: "t.B99990001.pdb", best: false, failure: null },
      { file: "t.B99990002.pdb", best: true, failure: null },
      { file: "t.B99990003.pdb", best: false, failure: "optimization failed" },
    ] as ModelEntry[];
    const templates = [{ id: "1UBQ", code: "1UBQA", chains: ["A"], predicted: false }, { id: "AF-P0CG48-F1", code: "AFA", chains: ["A"], predicted: true }] as PreparedTemplate[];
    const targets = compareTargets("j1", "t.B99990001.pdb", models, templates);
    expect(targets.map((t) => t.label)).toEqual(["Template 1UBQA", "Template AFA (AlphaFold)", "t.B99990002.pdb (migliore)"]);
    const first = linkedSources(new URL(targets[0].href, "http://x").searchParams);
    expect(first.a).toEqual({ type: "template", id: "1UBQ", chain: "A" });
    expect(first.b).toEqual({ type: "job", job_id: "j1", file: "t.B99990001.pdb" });
    expect(linkedSources(new URL(targets[2].href, "http://x").searchParams).a).toEqual({ type: "job", job_id: "j1", file: "t.B99990002.pdb" });
    expect(compareTargets("j1", "t.B99990001.pdb", [models[0]])).toEqual([]);
  });
});

describe("colorazione del viewer", () => {
  it("scala continua 0 → ≥ 5 Å, satura oltre; lDDT alto = blu come distanza nulla", () => {
    expect(colorFor(DISTANCE_SCALE, 0)).toBe("#2166ac");
    expect(colorFor(DISTANCE_SCALE, 5)).toBe("#b2182b");
    expect(colorFor(DISTANCE_SCALE, 12)).toBe("#b2182b");
    expect(colorFor(DISTANCE_SCALE, 2.5)).toBe("#fee090");
    expect(colorFor(DISTANCE_SCALE, 1)).not.toBe(colorFor(DISTANCE_SCALE, 2));
    expect(colorFor(LDDT_SCALE, 1)).toBe(colorFor(DISTANCE_SCALE, 0));
    const legend = legendFor(DISTANCE_SCALE, "d");
    expect(legend.kind === "gradient" && legend.ticks.map((t) => t.label)).toEqual(["0", "1", "2", "3", "4", "≥ 5 Å"]);
  });

  it("B colorata per distanza, A in grigio chiaro con il valore nel tooltip, B senza coppia in grigio", () => {
    const pairs = [pair(1, 0.1), pair(2, 6, 0.2, { resnum_b: "2A", aa_b: "W" })];
    const c = comparisonColoring(pairs, "distance");
    const map = coloringMap(c);
    expect(map.get("B:1")).toBe(colorFor(DISTANCE_SCALE, 0.1));
    expect(map.get("B:2A")).toBe("#b2182b");
    expect(map.get("A:1")).toBe(REFERENCE_COLOR);
    expect(map.get("A:2")).toBe(REFERENCE_COLOR);
    expect(c.chainColors).toEqual({ A: REFERENCE_COLOR, B: NO_VALUE_COLOR });
    expect(c.legendExtra?.map((x) => x.label)).toEqual(["A (riferimento)", "B senza coppia"]);
    expect(describeResidue(c, { chain: "B", resnum: "2A" })).toBe("6,00 Å (con G2 di A)");
    expect(describeResidue(c, { chain: "A", resnum: "2" })).toBe("6,00 Å (con W2A di B)");
    expect(hasMissing(c)).toBe(false);
    const byLddt = comparisonColoring([pair(1, 0.1, null), pair(2, 3, 0.5)], "lddt");
    expect(coloringMap(byLddt).get("B:2")).toBe("#fee090");
    expect(coloringMap(byLddt).get("B:1")).toBe(NO_VALUE_COLOR);
    expect(hasMissing(byLddt)).toBe(true);  // il residuo di B senza lDDT, non quello di A (colore fisso)
  });

  it("coppia evidenziata nei due residui e coppia di un residuo scelto nel 3D", () => {
    const pairs = [pair(35, 0), pair("36", 0.2, 1, { resnum_b: "37" })];
    expect(pairHighlight(pairs[1])).toEqual([{ chain: "A", resi: 36 }, { chain: "B", resi: 37 }]);
    expect(pairIndexOf(pairs, { chain: "B", resnum: "37" })).toBe(1);
    expect(pairIndexOf(pairs, { chain: "A", resnum: "36" })).toBe(1);
    expect(pairIndexOf(pairs, { chain: "B", resnum: "36" })).toBe(-1);
    expect(pairIndexOf(pairs, { chain: "C", resnum: "35" })).toBe(-1);
  });
});

describe("serie per posizione e tratti che deviano", () => {
  it("interruzioni dove A salta residui o cambia catena; un'inserzione non interrompe", () => {
    const pairs = [pair(1, 0), pair(2, 0), pair(5, 0), pair("5A", 0), pair(6, 0), pair(7, 0, 1, { chain_a: "B" })];
    expect(breaksBefore(pairs)).toEqual([false, false, true, false, false, true]);
    const x = (i: number) => i * 10;
    const y = (v: number) => v;
    expect(seriesPath([1, 2, 3, null, 5, 6], breaksBefore(pairs), x, y)).toBe("M0.0,1.0L10.0,2.0M20.0,3.0M40.0,5.0M50.0,6.0");
    expect(seriesPath([], [], x, y)).toBe("");
  });

  it("il tratto spostato di 1UBQ è l'unico sopra 2 Å", () => {
    const segs = deviatingSegments(SHIFTED, 2);
    expect(segs).toHaveLength(1);
    expect(segs[0]).toMatchObject({ start: 30, end: 36, from: "31", to: "37", max: 6 });
    expect(segs[0].text).toBe("31–37 (catena A): fino a 6,0 Å");
    expect(deviatingSegments(SHIFTED, 7)).toEqual([]);
  });

  it("tratti corti o spezzati da un salto di numerazione non contano", () => {
    const pairs = [pair(1, 3), pair(2, 3), pair(3, 0), pair(4, 3), pair(5, 3), pair(9, 3), pair(10, 3), pair(11, 3)];
    expect(deviatingSegments(pairs, 2).map((s) => [s.from, s.to])).toEqual([["9", "11"]]);
    expect(deviatingSegments(pairs, 2, 2).map((s) => [s.from, s.to])).toEqual([["1", "2"], ["4", "5"], ["9", "11"]]);
  });
});

describe("tabella delle coppie", () => {
  it("ordinamento per posizione, distanza (decrescente) o lDDT (crescente, mancanti in fondo) e filtro", () => {
    const pairs = [pair(1, 0.5, 0.9), pair(2, 4, 0.3), pair(3, 4, null), pair(4, 1, 0.3)];
    expect(pairRows(pairs)).toEqual([0, 1, 2, 3]);
    expect(pairRows(pairs, "distance")).toEqual([1, 2, 3, 0]);
    expect(pairRows(pairs, "lddt")).toEqual([1, 3, 0, 2]);
    expect(pairRows(pairs, "position", 1)).toEqual([1, 2, 3]);
    expect(pairRows(SHIFTED, "distance", 5)).toEqual([30, 31, 32, 33, 34, 35, 36]);
  });

  it("lettura del TM-score con le soglie di letteratura", () => {
    expect(tmVerdict(0.93)).toBe("stesso ripiegamento (> 0,5)");
    expect(tmVerdict(0.5)).toBe("somiglianza debole (0,17–0,5)");
    expect(tmVerdict(0.1)).toBe("come due strutture casuali (< 0,17)");
    expect(tmVerdict(null)).toBe("—");
  });
});

describe("scorrimento della tabella alla riga selezionata", () => {
  // contenitore da 0 a 380 px con 30 px di intestazione fissa; righe alte 30 px
  const view = { top: 100, bottom: 480 };
  it("nessuno scorrimento se la riga è già visibile sotto l'intestazione", () => {
    expect(rowScrollDelta({ top: 140, bottom: 170 }, view, 30)).toBe(0);
    expect(rowScrollDelta({ top: 450, bottom: 480 }, view, 30)).toBe(0);
  });
  it("una riga sotto o sopra l'area visibile va al centro del contenitore (non della pagina)", () => {
    const below = { top: 900, bottom: 930 };
    const delta = rowScrollDelta(below, view, 30);
    const after = { top: below.top - delta, bottom: below.bottom - delta };
    expect((after.top + after.bottom) / 2).toBeCloseTo((view.top + 30 + view.bottom) / 2, 0);
    expect(rowScrollDelta({ top: 110, bottom: 140 }, view, 30)).toBeLessThan(0); // coperta dall'intestazione
    expect(rowScrollDelta({ top: -200, bottom: -170 }, view)).toBeLessThan(0);
  });
});

describe("risultati di versioni precedenti", () => {
  const note = "Allineamento dipendente dalla sequenza: per proteine con bassa identità usare TM-align/US-align.";
  it("un vecchio risultato del metodo integrato ha la nota ma non la versione: si invita a rilanciare", () => {
    const oldBuiltin: Partial<AlignResult> = { method: "Kabsch + TM-score integrato", note, tm_score: 1 };
    const oldTool: Partial<AlignResult> = { method: "US-align", tm_score: 1 };
    expect(comparisonOutdated(oldBuiltin)).toBe(true);
    expect(comparisonOutdated(oldTool)).toBe(true);
  });
  it("un risultato nuovo senza coppie (uscita non riconosciuta) ha la versione: solo la sua nota", () => {
    const failed: Partial<AlignResult> = { method: "TM-align", note: "Uscita di TM-align non riconosciuta: confronto per residuo non disponibile.", comparison_version: 2 };
    expect(comparisonOutdated(failed)).toBe(false);
    expect(comparisonOutdated({ pairs: SHIFTED, comparison_version: 2 })).toBe(false);
  });
});

describe("pagina Confronto", () => {
  it("un job del link che non si carica (eliminato, link vecchio) mostra l'errore, non una colonna vuota", () => {
    expect(compareJobView(null, false, null)).toBe("empty");
    expect(compareJobView("0123456789abcdef", false, null)).toBe("job"); // in caricamento
    expect(compareJobView("0123456789abcdef", false, "Job 0123456789abcdef non trovato.")).toBe("missing");
    expect(compareJobView("0123456789abcdef", true, "Server MakPhi non raggiungibile.")).toBe("job"); // già mostrato
  });
});
