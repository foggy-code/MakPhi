import { describe, expect, it } from "vitest";
import type { ConsensusMethod, QualityReport } from "../api/types";
import {
  andList, BAND_TONE, buriedCount, clashTone, dsspBreakdown, dsspLabel, favoredTone, fmtPenalty, gFactorTone, gradeSteps, methodContribution,
  missingAnalyses, nearestPoint, outlierTone, procheckPlotsNote, parseAtom, positiveRuns, prosaData, prosaPoints, prosaResidues, prosaTone, qmeanTone, residueClashes,
  scaleText, zdopeTone,
} from "./qualityView";

describe("esito delle metriche", () => {
  it("outlier di Ramachandran: penalità del voto oltre lo 0,5%, problematico oltre il 2%", () => {
    expect([0, 0.5, 0.6, 2, 2.1].map(outlierTone)).toEqual(["ok", "ok", "warn", "warn", "err"]);
    expect([99, 98, 92, 89.9].map(favoredTone)).toEqual(["ok", "ok", "warn", "err"]);
  });
  it("clashscore, zDOPE, QMEAN, ProSA e G-factor con le soglie dichiarate", () => {
    expect([0, 5, 5.1, 20, 21].map(clashTone)).toEqual(["ok", "ok", "warn", "warn", "err"]);
    // −1 è ancora buono: consenso 70 (fascia buona) e nessuna penalità nel voto
    expect([-1.5, -1, -0.99, 0, 0.3, null].map(zdopeTone)).toEqual(["ok", "ok", "warn", "warn", "err", undefined]);
    expect([-1, -2, -3, -4.5].map(qmeanTone)).toEqual(["ok", "ok", "warn", "err"]);
    expect([-8, -6, -3, -1].map(prosaTone)).toEqual(["ok", "ok", "warn", "err"]);
    expect([0.1, -0.5, -1, -1.2, null].map(gFactorTone)).toEqual(["ok", "warn", "warn", "err", undefined]);
  });
});

const REPORT = {
  grade: { score: 72.2, grade: "B", penalties: { ramachandran: 8, clashes: 4.8, dope: 15 } },
  ramachandran: { summary: { outlier_pct: 1.5 } },
  clashes: { clashscore: 7.2 },
  dope: { score: -12000, zscore: 0.5, profile: [] },
} as unknown as QualityReport;

describe("voto", () => {
  it("una riga per penalità con la regola applicata ai valori", () => {
    expect(gradeSteps(REPORT)).toEqual([
      { label: "Ramachandran", points: 8, rule: "1,5% di outlier: 8 punti per punto percentuale oltre lo 0,5% (al massimo 40)" },
      { label: "Contatti sterici", points: 4.8, rule: "clashscore 7,20 / 1,5 (al massimo 30)" },
      { label: "Energia zDOPE", points: 15, rule: "zDOPE 0,50: 10 punti per unità sopra −1 (al massimo 20)" },
    ]);
  });
  it("senza zDOPE la penalità non c'è (non zero punti)", () => {
    const [, , dope] = gradeSteps({ ...REPORT, dope: null });
    expect(dope).toEqual({ label: "Energia zDOPE", points: null, rule: "zDOPE non calcolato: nessuna penalità" });
    expect([8, 0, null].map(fmtPenalty)).toEqual(["−8,0", "0,0", "—"]);
  });
});

describe("scontri", () => {
  it("etichette degli atomi, anche con la catena vuota o il codice d'inserzione", () => {
    expect(parseAtom("A 43 LEU CD1")).toEqual({ chain: "A", resnum: "43", resname: "LEU", atom: "CD1" });
    expect(parseAtom(" 12A GLY CA")).toEqual({ chain: "", resnum: "12A", resname: "GLY", atom: "CA" });
    expect(parseAtom("rotto")).toBeNull();
  });
  it("scontri di un residuo, da una parte o dall'altra della coppia", () => {
    const clashes = [
      { atom1: "A 43 LEU CD1", atom2: "A 50 VAL O", distance: 2.6, overlap: 0.6 },
      { atom1: "A 12 SER OG", atom2: "A 43 LEU N", distance: 2.4, overlap: 0.5 },
      { atom1: "B 43 LEU CD1", atom2: "B 44 ALA N", distance: 2.9, overlap: 0.41 },
    ];
    expect(residueClashes(clashes, { chain: "A", resnum: "43" })).toEqual([
      { own: "CD1", partner: "A 50 VAL O", distance: 2.6, overlap: 0.6 },
      { own: "N", partner: "A 12 SER OG", distance: 2.4, overlap: 0.5 },
    ]);
  });
});

describe("superficie e struttura secondaria", () => {
  it("sepolti con RSA < 0,2 sui residui con l'RSA calcolata", () => {
    expect(buriedCount([{ rsa: 0.1 }, { rsa: 0.2 }, { rsa: null }, { rsa: 0.05 }])).toEqual({ buried: 2, measured: 3 });
  });
  it("stati DSSP nell'ordine di priorità, spazio come coil", () => {
    expect(dsspBreakdown({ E: 10, H: 20, " ": 5, "-": 5, T: 10 })).toEqual([
      { code: "H", label: "α-elica", count: 20, pct: 40 },
      { code: "E", label: "filamento β", count: 10, pct: 20 },
      { code: "T", label: "turn", count: 10, pct: 20 },
      { code: "-", label: "coil", count: 10, pct: 20 },
    ]);
    expect(dsspLabel("G")).toBe("elica 3₁₀");
    expect(dsspLabel("")).toBe("coil");
  });
});

describe("consenso", () => {
  const methods = [
    { key: "grade", normalized: 80, weight: 1, points: [[0, 0], [55, 40], [70, 70], [100, 100]] },
    { key: "qmean", normalized: 60, weight: 1.5, points: [[-8, 0], [-4, 40], [-2, 70], [0, 100]] },
  ] as unknown as ConsensusMethod[];
  it("contributi: la somma è la media pesata", () => {
    const c = { methods };
    expect(methodContribution(c, methods[0])).toBeCloseTo(32);
    expect(methodContribution(c, methods[1])).toBeCloseTo(36);
  });
  it("scala lineare a tratti leggibile", () => {
    expect(scaleText(methods[0])).toBe("0 → 0 · 55 → 40 · 70 → 70 · 100 → 100");
    expect(scaleText(methods[1])).toBe("−8,0 → 0 · −4,0 → 40 · −2,0 → 70 · 0,0 → 100");
  });
});

describe("profilo ProSA", () => {
  const profile = [
    { index: 3, energy: 0.2, energy_w10: 0.1 },
    { index: 1, energy: -1, energy_w10: null },
    { index: 2, energy: 0.5, energy_w10: 0.3 },
    { index: 4, energy: -0.4, energy_w10: -0.2 },
    { index: 5, energy: 0.9, energy_w10: 0.4 },
    { index: 7, energy: 0.9, energy_w10: 0.6 },
  ];
  it("punti con la finestra 10, ordinati; tratti consecutivi sopra lo zero", () => {
    const pts = prosaPoints(profile);
    expect(pts.map((p) => p.index)).toEqual([2, 3, 4, 5, 7]);
    expect(positiveRuns(pts)).toEqual([{ start: 2, end: 3, max: 0.3 }, { start: 5, end: 5, max: 0.4 }, { start: 7, end: 7, max: 0.6 }]);
  });
  it("punto più vicino a un indice dell'asse", () => {
    const pts = prosaPoints(profile);
    expect(nearestPoint(pts, 0)).toBe(0);
    expect(nearestPoint(pts, 3.4)).toBe(1);
    expect(nearestPoint(pts, 6.2)).toBe(4);
    expect(nearestPoint(pts, 99)).toBe(4);
    expect(nearestPoint([], 1)).toBe(-1);
  });
  it("residui attribuibili solo con indici esattamente 1…N (come domain/perresidue.prosa_window)", () => {
    const residues = Array.from({ length: 6 }, (_, i) => ({ chain: "A", resnum: String(i + 1), resname: "ALA" }));
    expect(prosaResidues(profile.slice(0, 5), residues.slice(0, 5))).toHaveLength(5);
    expect(prosaResidues(profile, residues)).toBeNull(); // stesso numero di righe, ma manca il 6 e c'è il 7
    expect(prosaResidues(profile.slice(0, 5), residues)).toBeNull();
    expect(prosaResidues([...profile.slice(0, 4), { index: 4, energy: 0, energy_w10: 0 }], residues.slice(0, 5))).toBeNull();
    expect(prosaResidues(profile, undefined)).toBeNull();
  });
  it("dati con il nome del residuo solo se il profilo è allineato ai residui", () => {
    const residues = Array.from({ length: 5 }, (_, i) => ({ chain: "A", resnum: String(i + 1), resname: "ALA" }));
    const aligned = profile.slice(0, 5);
    const named = prosaData(aligned, residues);
    expect(named.columns.map((c) => c.key)).toEqual(["indice", "residuo", "w10", "w1", "sopra"]);
    expect(named.rows[0]).toEqual({ indice: 3, residuo: "ALA A3", w10: 0.1, w1: 0.2, sopra: "sì" });
    expect(named.rows[1].sopra).toBeNull();
    expect(prosaData(aligned, residues.slice(1)).columns.map((c) => c.key)).toEqual(["indice", "w10", "w1", "sopra"]);
    expect(prosaData(profile, [...residues, residues[0]]).columns.map((c) => c.key)).toEqual(["indice", "w10", "w1", "sopra"]);
  });
});

describe("nuova analisi e fasce del consenso", () => {
  const report = (over: Partial<QualityReport> = {}) => ({ dope: { score: -1200, zscore: -1.2, profile: [] }, procheck: null, prosa: null, ...over }) as QualityReport;
  it("analisi assenti che la nuova analisi aggiungerebbe, secondo le caselle", () => {
    expect(missingAnalyses(report(), { dope: true, extended: true })).toEqual(["PROCHECK", "ProSA"]);
    expect(missingAnalyses(report(), { dope: true, extended: false })).toEqual([]);
    expect(missingAnalyses(report({ dope: null }), { dope: true, extended: false })).toEqual(["DOPE"]);
    expect(missingAnalyses(report({ dope: { score: null, zscore: null, profile: [] } }), { dope: false, extended: true })).toEqual(["PROCHECK", "ProSA"]);
    const full = report({ procheck: {} as QualityReport["procheck"], prosa: {} as QualityReport["prosa"] });
    expect(missingAnalyses(full, { dope: true, extended: true })).toEqual([]);
  });
  it("elenco a parole e tono delle fasce", () => {
    expect(andList([])).toBe("");
    expect(andList(["DOPE"])).toBe("DOPE");
    expect(andList(["PROCHECK", "ProSA"])).toBe("PROCHECK e ProSA");
    expect(andList(["DOPE", "PROCHECK", "ProSA"])).toBe("DOPE, PROCHECK e ProSA");
    expect(BAND_TONE).toEqual({ buona: "ok", intermedia: "warn", scarsa: "err" });
  });
});

describe("procheckPlotsNote", () => {
  it("conta solo i grafici PostScript e dice quando non sono conservati", () => {
    expect(procheckPlotsNote(["model.sum", ...Array.from({ length: 10 }, (_, i) => `model_${String(i + 1).padStart(2, "0")}.ps`)])).toBe("I grafici PostScript di PROCHECK sono tra i file del job (10).");
    expect(procheckPlotsNote(["model.sum", "model_01.ps"])).toBe("Il grafico PostScript di PROCHECK (Ramachandran) è tra i file del job.");
    expect(procheckPlotsNote(["model.sum"])).toMatch(/^Grafici PostScript di PROCHECK non conservati/);
  });
});
