import { describe, expect, it } from "vitest";
import type { AlaScanResult, AlaScanRow, AnalysisResult, ElectrostaticsResult, MutationInteraction, MutationSite, Plausibility, Pocket } from "../api/types";
import { CLASS_COLOR } from "./mutations";
import { PLDDT_SCALE } from "./residueColoring";
import {
  barRange, barSpan, compareResidues, interactionDetail, interactionLine, interactionText, jobTimes, labelEvery, lddtSeries, logLineCount, plddtFractionsText,
  pocketDetail, pocketSeries, potentialSeries, scanData, scanMetrics, scanSeries, siteDetail,
} from "./toolsView";

describe("assi dei grafici a barre", () => {
  it("estremi: comprendono lo zero e le soglie, con margine; simmetrici o fissi", () => {
    expect(barRange([0, 0])).toEqual({ min: 0, max: 1 });
    expect(barRange([null, 2, 4])).toEqual({ min: 0, max: 4 + 4 * 0.06 });
    expect(barRange([1], [3]).max).toBeCloseTo(3.18);
    const sym = barRange([-2, 5], [], { symmetric: true });
    expect(sym.min).toBeCloseTo(-5.6);
    expect(sym.max).toBeCloseTo(5.6);
    expect(barRange([0.2, 0.9], [], { domain: [0, 1] })).toEqual({ min: 0, max: 1 });
  });

  it("etichette dell'asse x a passi tondi", () => {
    expect(labelEvery(50, 30)).toBe(1);
    expect(labelEvery(20, 30)).toBe(2);
    expect(labelEvery(9, 30)).toBe(5);
    expect(labelEvery(0.33, 36)).toBe(200);
    expect(labelEvery(0, 30)).toBe(1);
  });

  it("intervallo visibile e ordine dei residui", () => {
    const items = [{ tick: "1" }, { tick: "2" }, { tick: "3" }, { tick: "4" }];
    expect(barSpan(items, { lo: -0.5, hi: 3.5 }, "residui")).toBe("1–4 di 4 residui");
    expect(barSpan(items, { lo: 0.6, hi: 2.4 }, "residui")).toBe("2–3 di 4 residui");
    const sorted = [{ chain: "B", resnum: "1" }, { chain: "A", resnum: "100" }, { chain: "A", resnum: "52A" }, { chain: "A", resnum: "52" }].sort(compareResidues);
    expect(sorted.map((r) => `${r.chain}${r.resnum}`)).toEqual(["A52", "A52A", "A100", "B1"]);
  });
});

const SITE = {
  site: "A:30", label: "A:I30P", wt: "I", mut: "P", rsa_wt: 0.042, rsa_mut: 0.061, core: true,
  buried_wt: { total: 163.4, apolar: 137.7 }, buried_mut: { total: 123.9, apolar: 91.8 }, buried_delta: { total: -39.5, apolar: -45.9 },
  ss: "H", ss_position: 7, ss_length: 12, rama: { phi: -65.1, psi: -40, wt: "favored", mut: "allowed" }, blosum62: -3, helix_propensity: 2.75,
  interactions: { wild_type: 1, mutant: 0, gained: [], lost: [] }, clashes: { wild_type: 0, mutant: 1, worst: 0.42 }, interface: null,
} as MutationSite;

function row(label: string, chain: string, resnum: string, code: "destabilizing" | "neutral", eq: number): AlaScanRow {
  return {
    label, mutation: { chain, resnum, wt: "L", mut: "A", label, wt_name: "LEU", mut_name: "ALA" }, site: SITE,
    dope: { delta: eq * 100, delta_eq: eq, interchain_delta_eq: null } as AlaScanRow["dope"], energy: null, files: { wild_type: "w.pdb", mutant: "m.pdb" },
    assessment: { code, label: code, summary: "", score: 0, binding: null, reasons: [`motivo ${label}`], signals: [] },
  };
}

describe("scansione di alanina", () => {
  const scan = {
    rows: [row("A:L48A", "A", "48", "destabilizing", 2.4), row("A:L9A", "A", "9", "neutral", 0.1)],
    protocol: { thresholds: { dope_moderate: 1, dope_strong: 2, energy_moderate: 50, energy_strong: 100 } },
    interface: false,
  } as unknown as AlaScanResult;

  it("barre nell'ordine della sequenza o dell'effetto, colori della classe, soglie del backend", () => {
    const byPos = scanSeries(scan, "eq", "position");
    expect(byPos.items.map((i) => i.tick)).toEqual(["L9", "L48"]);
    expect(byPos.rows.map((r) => r.label)).toEqual(["A:L9A", "A:L48A"]);
    expect(byPos.items[1].color).toBe(CLASS_COLOR.destabilizing);
    expect(byPos.references.map((r) => r.value)).toEqual([1, 2]);
    expect(scanSeries(scan, "eq", "effect").items.map((i) => i.key)).toEqual(["A:L48A", "A:L9A"]);
    expect(scanSeries(scan, "dope", "effect").references).toEqual([]);
    expect(scanSeries({ ...scan, protocol: { thresholds: {} } } as unknown as AlaScanResult, "eq", "effect").references).toEqual([]);
    expect(scanData(byPos.rows).rows[0]).toMatchObject({ mutazione: "A:L9A", eq: 0.1, rsa: 4.2, motivo: "motivo A:L9A" });
  });

  it("valori disponibili: fra catene con più catene, ΔΔE solo se calcolata", () => {
    expect(scanMetrics(scan).map((m) => m.value)).toEqual(["eq", "dope"]);
    const energy = { ...scan, interface: true, rows: [{ ...scan.rows[0], energy: { available: true, ddE: 30 } }] } as AlaScanResult;
    expect(scanMetrics(energy).map((m) => m.value)).toEqual(["eq", "dope", "interchain", "energy"]);
  });

  it("dettaglio del sito e interazioni", () => {
    const hb: MutationInteraction = { type: "hbond", partner: "VAL A26", role: "NH della catena principale", atom: "N", inter_chain: false, distance: 2.97 };
    expect(interactionText(hb)).toBe("VAL A26 (legame H, NH della catena principale)");
    expect(interactionLine(hb)).toBe("VAL A26 (legame H, NH della catena principale) · atomo N · 2,97 Å");
    expect(interactionText({ ...hb, type: "salt_bridge" })).toBe("VAL A26 (ponte salino)");
    const detail = Object.fromEntries(siteDetail(SITE).map((d) => [d.label, d.value]));
    expect(detail["Esposizione (RSA)"]).toBe("4% → 6% · nel nucleo");
    expect(detail["Superficie apolare sepolta"]).toBe("138 → 92 Å² (−46)");
    expect(detail["DSSP"]).toBe("H · posizione 8 di 12");
    expect(detail["Ramachandran (φ, ψ)"]).toBe("-65°, -40° · favorita → consentita");
    expect(detail["Contatti sterici gravi"]).toBe("0 → 1 (peggiore 0,42 Å)");
    expect(detail["Interfaccia"]).toBe("no");
  });
});

describe("tasche, potenziale, interazioni, predizione", () => {
  const pocket = {
    id: 3, rank: 1, druggability: 0.71, score: 0.42, volume: 512.4, alpha_spheres: 40, center: [1, 2.25, -3], spheres: [], atoms: 55,
    residues: [{ chain: "A", resnum: "10", resname: "LEU" }], druggable: true, other: { "Proportion of polar atoms": 31.5 },
  } as unknown as Pocket;

  it("tasche: druggability su 0–1 con la soglia, descrittori nel dettaglio", () => {
    const s = pocketSeries([pocket], "druggability");
    expect(s.domain).toEqual([0, 1]);
    expect(s.references[0].value).toBe(0.5);
    expect(s.items[0]).toMatchObject({ key: "3", tick: "3", value: 0.71 });
    expect(pocketSeries([pocket], "volume").references).toEqual([]);
    const d = Object.fromEntries(pocketDetail(pocket).map((x) => [x.label, x.value]));
    expect(d["Druggability"]).toBe("0,710 (≥ 0,5)");
    expect(d["Centro (x, y, z)"]).toBe("1,0, 2,3, -3,0 Å");
    expect(d["Proportion of polar atoms"]).toBe("31,500");
  });

  it("potenziale: rosso negativo, blu positivo, attenuato entro la soglia, simmetrico", () => {
    const res = (resnum: string, potential: number | null) => ({ chain: "A", resnum, resname: "LYS", potential, exposed_atoms: 3, points: 20, charge: 1 });
    const s = potentialSeries({ residues: [res("1", -3), res("2", 0.5), res("3", null)], summary: { threshold: 1 } } as unknown as ElectrostaticsResult);
    expect(s.symmetric).toBe(true);
    expect(s.items.map((i) => [i.color, !!i.faded])).toEqual([["var(--err)", false], ["var(--info)", true], ["var(--info)", true]]);
    expect(s.references.map((r) => r.value)).toEqual([1, -1]);
  });

  it("dettaglio di un legame idrogeno; chiave sconosciuta", () => {
    const end = (chain: string, resnum: string, resname: string, atom: string) => ({ chain, resnum, resname, atom });
    const result = {
      interactions: {
        hbonds: [{ a: end("B", "41", "GLN", "NE2"), b: end("A", "27", "LYS", "O"), distance: 3.04, ha_distance: 2.1, angle: 150.7, donor_angle: null, acceptor_angle: 130, hydrogen: "placed", kind: "mixed", inter_chain: true }],
        salt_bridges: [], disulfides: [], aromatic: [],
      },
    } as unknown as AnalysisResult;
    const key = "hbonds:B41NE2-A27O";
    const d = Object.fromEntries((interactionDetail(result, "hbonds", key) ?? []).map((x) => [x.label, x.value]));
    expect(d["Donatore"]).toBe("GLN B41 NE2");
    expect(d["Distanza H…A"]).toBe("2,10 Å");
    expect(d["Angolo D–H…A"]).toBe("151°");
    expect(d["Angolo AA–D…A"]).toBe("—");
    expect(d["Idrogeno"]).toBe("posizionato");
    expect(d["Fra catene diverse"]).toBe("sì");
    expect(interactionDetail(result, "hbonds", "altro")).toBeNull();
  });

  it("accordo con la predizione: colori per caso, pLDDT con i colori di AlphaFold DB", () => {
    const r = (resnum: string, lddt: number, plddt: number, divergent: boolean, confident: boolean, junction = false) => ({ chain: "A", resnum, lddt, plddt, divergent, confident, junction });
    const p = { residues: [r("1", 0.9, 95, false, true), r("2", 0.3, 92, true, true), r("3", 0.2, 40, true, false), r("4", 0.4, 80, true, true, true)], thresholds: {} } as unknown as Plausibility;
    expect(lddtSeries(p, "lddt").items.map((i) => i.color)).toEqual(["var(--accent)", "var(--warn)", "var(--coil)", "var(--coil)"]);
    expect(lddtSeries(p, "lddt").references[0].value).toBe(0.5);
    const pl = lddtSeries(p, "plddt");
    expect(pl.domain).toEqual([0, 100]);
    expect(pl.items[0].color).toBe(PLDDT_SCALE.bins[0].color);
    expect(plddtFractionsText({ very_high: 0.8, confident: 0.2, low: 0, very_low: 0 })).toBe("molto alta 80% · confidente 20%");
  });
});

describe("job", () => {
  it("tempi: in coda, in corso, concluso", () => {
    const labels = (s: Parameters<typeof jobTimes>[0]) => jobTimes(s).map((t) => t.label);
    expect(labels({ status: "queued", created_at: "2026-01-01T10:00:00Z", started_at: null, finished_at: null })).toEqual(["Creato", "In coda da"]);
    expect(labels({ status: "running", created_at: "2026-01-01T10:00:00Z", started_at: "2026-01-01T10:00:00Z", finished_at: null })).toEqual(["Creato", "Avviato", "In corso da"]);
    const done = jobTimes({ status: "completed", created_at: "2026-01-01T10:00:00Z", started_at: "2026-01-01T10:01:00Z", finished_at: "2026-01-01T10:03:05Z" });
    expect(done.map((t) => t.label)).toEqual(["Creato", "Avviato", "In coda per", "Durata", "Terminato"]);
    expect(done[2].value).toBe("1 min 0 s");
    expect(done[3].value).toBe("2 min 5 s");
  });

  it("righe del log", () => {
    expect(logLineCount("")).toBe(0);
    expect(logLineCount("a")).toBe(1);
    expect(logLineCount("a\nb\n")).toBe(2);
    expect(logLineCount("a\nb")).toBe(2);
  });
});
