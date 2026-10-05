import { describe, expect, it } from "vitest";
import {
  fmtAngle, inRamaView, ramaData, ramaSubtitle, RAMA_SHORT_LABEL, RAMA_TONE, regionCounts, residueMatcher, showPoint, viewRegions, type RamaPoint,
} from "./ramachandran";
import { spanAround } from "./zoom";

const pt = (resname: string, resnum: string, cls: string, region: RamaPoint["region"], phi = -60, psi = -40): RamaPoint => ({
  chain: "A", resnum, resname, aa: "X", phi, psi, class: cls, region,
});
const points = [
  pt("ALA", "12", "general", "favored"),
  pt("SER", "30", "prepro", "allowed"),
  pt("ASP", "45", "general", "outlier", 60, -150),
  pt("GLY", "50", "glycine", "favored"),
  pt("PRO", "51", "proline", "favored"),
];

describe("viste del grafico di Ramachandran", () => {
  it("classi: Tutti, Generale (con la pre-prolina), Glicina, Prolina", () => {
    expect(points.filter((p) => inRamaView(p, "all"))).toHaveLength(5);
    expect(points.filter((p) => inRamaView(p, "general")).map((p) => p.resname)).toEqual(["ALA", "SER", "ASP"]);
    expect(points.filter((p) => inRamaView(p, "glycine")).map((p) => p.resname)).toEqual(["GLY"]);
    expect(points.filter((p) => inRamaView(p, "proline")).map((p) => p.resname)).toEqual(["PRO"]);
  });
  it("regioni: Tutti usa il caso generale; rapporto senza regioni = vuote", () => {
    const general = { favored: [[-115, -40, -75, -5]] as [number, number, number, number][], allowed: [] };
    expect(viewRegions({ general }, "all")).toBe(general);
    expect(viewRegions({ general }, "glycine")).toEqual({ favored: [], allowed: [] });
    expect(viewRegions(undefined, "general")).toEqual({ favored: [], allowed: [] });
  });
  it("filtro della vista espansa", () => {
    expect(points.filter((p) => showPoint(p, "all"))).toHaveLength(5);
    expect(points.filter((p) => showPoint(p, "flagged")).map((p) => p.resname)).toEqual(["SER", "ASP"]);
    expect(points.filter((p) => showPoint(p, "outliers")).map((p) => p.resname)).toEqual(["ASP"]);
  });
});

describe("conteggi, sottotitolo e dati", () => {
  it("conteggi per regione e sottotitolo all'italiana", () => {
    expect(regionCounts(points)).toEqual({ total: 5, favored: 3, allowed: 1, outlier: 1 });
    expect(ramaSubtitle("general", points.slice(0, 3))).toBe("Caso generale (anche pre-prolina) · 3 residui · 33,3% in regioni favorite · 1 outlier");
    expect(ramaSubtitle("glycine", [points[3]])).toBe("Glicina · 1 residuo · 100,0% in regioni favorite");
    expect(ramaSubtitle("proline", [])).toBe("Prolina · nessun residuo");
  });
  it("tabella dei dati: residuo, classe e regione in italiano, angoli numerici", () => {
    const data = ramaData(points);
    expect(data.columns.map((c) => c.label)).toEqual(["Residuo", "Catena", "Numero", "Nome", "Classe", "φ (°)", "ψ (°)", "Regione"]);
    expect(data.rows[1]).toEqual({ residuo: "SER A30", catena: "A", numero: "30", nome: "SER", classe: "Pre-prolina", phi: -60, psi: -40, regione: "Consentita" });
    expect(data.rows[2].regione).toBe("Non consentita (outlier)");
  });
  it("angoli con il meno tipografico", () => {
    expect(fmtAngle(-63.24, 1)).toBe("−63,2");
    expect(fmtAngle(180)).toBe("180");
    expect(fmtAngle(-0.2)).toBe("0");
  });
});

describe("ricerca e zoom", () => {
  it("ricerca per nome, catena e numero", () => {
    expect(residueMatcher("  ")).toBeNull();
    const match = (q: string) => points.filter(residueMatcher(q)!).map((p) => p.resname);
    expect(match("a45")).toEqual(["ASP"]);
    expect(match("A:45")).toEqual(["ASP"]);
    expect(match("asp a45")).toEqual(["ASP"]);
    expect(match("gly")).toEqual(["GLY"]);
    expect(match("B45")).toEqual([]);
  });
  it("ricerca: i numeri valgono interi (A45 non trova A450 né A145), i codici di inserzione sì", () => {
    const many = [pt("ASP", "45", "general", "outlier"), pt("ASP", "450", "general", "favored"), pt("GLY", "451", "glycine", "favored"),
      pt("LYS", "145", "general", "favored"), pt("SER", "45A", "general", "allowed")];
    const match = (q: string) => many.filter(residueMatcher(q)!).map((p) => p.resnum);
    expect(match("A45")).toEqual(["45", "45A"]);
    expect(match("ASP A45")).toEqual(["45"]);
    expect(match("45")).toEqual(["45", "45A"]);
    expect(match("a4")).toEqual([]);
    expect(match("45a")).toEqual(["45A"]);
    // catena numerica: la cifra della catena non allunga il numero
    expect([{ ...pt("ALA", "12", "general", "favored"), chain: "1" }].filter(residueMatcher("12")!)).toHaveLength(1);
  });
  it("etichette brevi ed esiti delle regioni, uguali in tutta l'interfaccia", () => {
    expect(RAMA_SHORT_LABEL).toEqual({ favored: "favorita", allowed: "consentita", outlier: "outlier" });
    expect(RAMA_TONE).toEqual({ favored: "ok", allowed: "info", outlier: "err" });
  });
  it("selezione più stretta del minimo: allargata attorno al centro", () => {
    expect(spanAround(10, 12, 20)).toEqual({ lo: 1, hi: 21 });
    expect(spanAround(-100, 0, 20)).toEqual({ lo: -100, hi: 0 });
  });
});
