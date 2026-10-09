import { describe, expect, it } from "vitest";
import type { HeteroGroup, LigandGroupCheck, MetalSite, TemplateAnalysis, WaterSite } from "../api/types";
import {
  candidateGroups, coordinationLines, coordinationText, defaultTransfer, DONOR_COLOR, donorText, groupClass, isDefault, isWater, ligandFocus, ligandKey, ligandOptions,
  lookupState, sameSelection, selectionSummary, syncSelection, waterCandidates, waterText,
} from "./ligands";

function group(resname: string, chain: string, resnum: string, extra: Partial<HeteroGroup> = {}): HeteroGroup {
  return { resname, chain, resnum, atoms: 1, additive: false, ...extra };
}

// gruppi come li classifica il backend (test_ligands.py: ZN metallo, GOL/SO4 additivi, HEM cofattore, NA ione
// monovalente, MSE residuo modificato)
const LIGANDS: HeteroGroup[] = [
  group("ZN", "A", "101", { class: "metal", default: true, transferable: true, element: "ZN" }),
  group("GOL", "A", "301", { atoms: 6, additive: true, class: "additive", default: false, transferable: true, reason: "additivo comune di cristallizzazione" }),
  group("SO4", "B", "302", { atoms: 5, additive: true, class: "additive", default: false, transferable: true }),
  group("HEM", "A", "401", { atoms: 43, class: "cofactor", default: true, transferable: true, description: "eme b" }),
  group("NA", "A", "501", { class: "metal", default: false, transferable: true, reason: "ione monovalente" }),
  group("MSE", "A", "601", { atoms: 8, class: "modified", default: false, transferable: false }),
];

function analysis(ligands: HeteroGroup[] = LIGANDS): TemplateAnalysis {
  return { id: "1ABC", ligands } as unknown as TemplateAnalysis;
}

describe("scelta dei gruppi da trasferire", () => {
  it("candidati delle sole catene scelte, nell'ordine del file", () => {
    const c = candidateGroups({ id: "1ABC", chains: ["A"] }, analysis());
    expect(c.map((x) => x.group.resname)).toEqual(["ZN", "GOL", "HEM", "NA", "MSE"]);
    expect(c[0].key).toBe("1ABC|A|101|ZN");
    expect(candidateGroups({ id: "1ABC", chains: ["A", "B"] }, analysis()).map((x) => x.group.resname)).toContain("SO4");
    expect(candidateGroups({ id: "1ABC", chains: ["A"] }, null)).toEqual([]);
  });

  it("additivi e ioni monovalenti deselezionati per default, residui modificati non selezionabili", () => {
    const c = candidateGroups({ id: "1ABC", chains: ["A", "B"] }, analysis());
    const sel = syncSelection({}, c);
    expect(sel).toEqual({ "1ABC|A|101|ZN": true, "1ABC|A|301|GOL": false, "1ABC|B|302|SO4": false, "1ABC|A|401|HEM": true, "1ABC|A|501|NA": false });
    expect(ligandOptions(c, sel)).toEqual([
      { template: "1ABC", chain: "A", resnum: "101", resname: "ZN" },
      { template: "1ABC", chain: "A", resnum: "401", resname: "HEM" },
    ]);
    expect(selectionSummary(c, sel)).toBe("2 di 5 gruppi scelti (additivi e ioni del tampone esclusi per default).");
  });

  it("le scelte dell'utente restano quando cambiano i candidati; i gruppi spariti escono", () => {
    const all = candidateGroups({ id: "1ABC", chains: ["A", "B"] }, analysis());
    const chosen = { ...syncSelection({}, all), "1ABC|A|301|GOL": true, "1ABC|A|101|ZN": false };
    const onlyA = candidateGroups({ id: "1ABC", chains: ["A"] }, analysis());
    const next = syncSelection(chosen, onlyA);
    expect(next["1ABC|A|301|GOL"]).toBe(true);
    expect(next["1ABC|A|101|ZN"]).toBe(false);
    expect("1ABC|B|302|SO4" in next).toBe(false);
    expect(sameSelection(next, syncSelection(next, onlyA))).toBe(true);
    expect(sameSelection(next, chosen)).toBe(false);
  });

  it("analisi in cache delle versioni precedenti (solo additive): additivi fuori, gli altri dentro", () => {
    const old = [group("ZN", "A", "101"), group("SO4", "A", "201", { additive: true })];
    expect(old.map(groupClass)).toEqual(["ligand", "additive"]);
    expect(old.map(isDefault)).toEqual([true, false]);
    const c = candidateGroups({ id: "OLD", chains: ["A"] }, analysis(old));
    expect(ligandOptions(c, syncSelection({}, c))).toEqual([{ template: "OLD", chain: "A", resnum: "101", resname: "ZN" }]);
  });

  it("casella «Ligandi» del template (Pipeline): solo i gruppi della scelta predefinita delle catene scelte", () => {
    // ZN, GOL (additivo), HEM, NA (monovalente), MSE (residuo modificato) nella catena A; SO4 nella B
    const onlyA = defaultTransfer({ id: "1ABC", chains: ["A"] }, analysis());
    expect(onlyA.names).toEqual(["ZN", "HEM"]);
    expect(onlyA.excluded).toEqual(["GOL A301 (additivo comune di cristallizzazione)", "NA A501 (ione monovalente)", "MSE A601"]);
    // solo additivi o residui modificati: nessuna casella (spuntarla non trasferirebbe nulla)
    const additives = analysis([LIGANDS[1], LIGANDS[5]]);
    expect(defaultTransfer({ id: "1ABC", chains: ["A"] }, additives).names).toEqual([]);
    // un gruppo di una catena non scelta non conta
    expect(defaultTransfer({ id: "1ABC", chains: ["B"] }, analysis()).names).toEqual([]);
    expect(defaultTransfer({ id: "1ABC", chains: ["A"] }, null)).toEqual({ names: [], excluded: [] });
    // analisi in cache delle versioni precedenti (solo additive): nomi senza ripetizioni
    const old = analysis([group("ZN", "A", "101"), group("ZN", "A", "102"), group("SO4", "A", "201", { additive: true })]);
    expect(defaultTransfer({ id: "OLD", chains: ["A"] }, old).names).toEqual(["ZN"]);
  });

  it("lettura dei gruppi: analisi del selettore dei template prima di tutto, errori visibili e non «in corso»", () => {
    const templates = [{ id: "1ABC", chains: ["A"] }, { id: "2XYZ", chains: ["A"] }];
    // 1ABC in download (404, nuovo tentativo previsto), 2XYZ non riuscito: non pronto, 2XYZ da segnalare
    const own = { "1ABC": { error: "Template 1ABC non trovato", retrying: true }, "2XYZ": { error: "Errore interno", retrying: false } };
    expect(lookupState(templates, own)).toMatchObject({ ready: false, failed: ["2XYZ"] });
    // il selettore dei template ha finito il download di 1ABC: la sua analisi vale al posto dell'errore
    const known = { "1ABC": analysis(), "2XYZ": analysis([]) };
    const state = lookupState(templates, own, known);
    expect(state).toMatchObject({ ready: true, failed: [] });
    expect(state.analyses["1ABC"]).toBe(known["1ABC"]);
    expect(lookupState(templates, { "1ABC": "loading", "2XYZ": analysis() })).toMatchObject({ ready: false, failed: [] });
  });

  it("nessun candidato: messaggio chiaro", () => {
    expect(selectionSummary([], {})).toBe("Nessun ligando, cofattore o metallo nelle catene scelte.");
    expect(ligandKey("T", { chain: "A", resnum: "5A", resname: "HEM" })).toBe("T|A|5A|HEM");
  });
});

// Zn di ubq_zn.pdb (valori del backend: His68 NE2 e due acque a 2,10 Å, piramide a base quadrata, gRMSD 14,1°)
function zincCheck(): LigandGroupCheck {
  const donor = (label: string, chain: string, resnum: string, resname: string, water: boolean, coord: [number, number, number], status: MetalSite["donors"][number]["status"] = "nella norma") => ({
    chain, resnum, resname, atom: water ? "O" : "NE2", protein: !water, label, category: water ? "O_water" : "N_his", donor: water ? "O (acqua)" : "N (His)",
    distance: 2.1, expected: water ? 2.09 : 2.03, status, water, same_group: false, coord,
  });
  const metal: MetalSite = {
    atom: "ZN", element: "ZN", symbol: "Zn", coord: [20.137, 35.131, 16.359], coordination_number: 3,
    donors: [
      donor("His A68 NE2", "A", "68", "HIS", false, [22.045, 35.455, 17.173]),
      donor("HOH A202 O", "A", "202", "HOH", true, [20.137, 37.231, 16.359], "lunga"),
      donor("HOH A201 O", "A", "201", "HOH", true, [20.137, 35.131, 18.459], "troppo corta"),
    ],
    geometry: { key: "square_pyramidal", name: "piramide a base quadrata", positions: 5, grmsd: 14.1, vacancy: 0.4 },
    cn_range: [4, 6], reference: true,
  };
  return {
    resname: "ZN", chain: "A", resnum: "101", atoms: 1, class: "metal", label: "metallo", status: "da controllare", summary: "", reasons: [],
    contacts: [{ chain: "A", resnum: "68", resname: "HIS", distance: 2.1 }], clashes: [{ atom: "ZN", partner: "Leu A43 CB", distance: 0.9 }], links: [], metals: [metal],
  };
}

describe("verifica nel 3D e nelle tabelle", () => {
  it("testi della coordinazione", () => {
    const zn = zincCheck().metals[0];
    expect(coordinationText(zn)).toBe("Zn · 3 donatori · piramide a base quadrata (gRMSD 14,1°)");
    expect(coordinationText({ ...zn, coordination_number: 1, geometry: null })).toBe("Zn · 1 donatore");
    expect(donorText(zn.donors[0])).toBe("His A68 NE2 2,10 Å (attesa 2,03)");
    expect(donorText({ ...zn.donors[0], expected: null })).toBe("His A68 NE2 2,10 Å");
  });

  it("linee metallo → donatore colorate per esito della distanza", () => {
    const lines = coordinationLines(zincCheck());
    expect(lines).toHaveLength(3);
    expect(lines[0]).toEqual({ start: [20.137, 35.131, 16.359], end: [22.045, 35.455, 17.173], color: DONOR_COLOR.ok, label: "Zn–His A68 NE2 2,10 Å", marker: false });
    expect(lines.map((l) => l.color)).toEqual([DONOR_COLOR.ok, DONOR_COLOR.warn, DONOR_COLOR.err]);
    expect(lines.map((l) => l.marker)).toEqual([false, true, true]); // le acque non sono disegnate: sfera alla fine
  });

  it("residui evidenziati: il gruppo, i donatori della proteina e i residui in scontro, senza acque né doppioni", () => {
    expect(ligandFocus(zincCheck())).toEqual([{ chain: "A", resi: 101 }, { chain: "A", resi: 68 }, { chain: "A", resi: 43 }]);
  });
});

// acque come le restituisce il backend (test_waters.py e 3KS3: l'acqua dello zinco suggerita, una strutturale no)
function water(resnum: string, extra: Partial<WaterSite> = {}): WaterSite {
  return {
    resname: "HOH", chain: "A", resnum, atoms: 1, additive: false, class: "water", label: "acqua", default: false, transferable: true,
    reason: "acqua: si trasferisce solo se scelta una per una", partners: [], groups: [], metals: [], bfactor: 10, occupancy: 1,
    role: "", suggested: false, why: "", ...extra,
  };
}

const WATERS: WaterSite[] = [
  water("402", { role: "metal", suggested: true, why: "coordina ZN A262", metals: ["ZN A262 (Zn, 1,90 Å)"], partners: ["Thr A199 OG1 2,71 Å"], bfactor: 6.1 }),
  water("280", { role: "structural", why: "strutturale: legami H con 3 residui", partners: ["Asn A244 ND2 2,52 Å", "His A96 ND1 2,81 Å", "Phe A95 O 2,89 Å", "Leu A60 O 3,01 Å", "Ser A65 OG 3,2 Å"] }),
  water("501", { chain: "B" }),
];

describe("acque", () => {
  const withWaters = { id: "3KS3", ligands: LIGANDS.slice(0, 1), waters: { waters: WATERS, others: 126, criteria: { hbond: 3.5, structural: 3 } } } as unknown as TemplateAnalysis;

  it("candidate delle catene scelte, mai scelte per default; nell'opzione con il loro nome", () => {
    const c = [...candidateGroups({ id: "3KS3", chains: ["A"] }, withWaters), ...waterCandidates({ id: "3KS3", chains: ["A"] }, withWaters)];
    expect(c.map((x) => x.key)).toEqual(["3KS3|A|101|ZN", "3KS3|A|402|HOH", "3KS3|A|280|HOH"]);
    expect(c.filter(isWater).length).toBe(2);
    const sel = syncSelection({}, c);
    expect(sel).toEqual({ "3KS3|A|101|ZN": true, "3KS3|A|402|HOH": false, "3KS3|A|280|HOH": false });
    expect(selectionSummary(c, sel)).toBe("1 di 1 gruppo scelto.");
    const chosen = { ...sel, "3KS3|A|402|HOH": true };
    expect(ligandOptions(c, chosen)).toEqual([
      { template: "3KS3", chain: "A", resnum: "101", resname: "ZN" },
      { template: "3KS3", chain: "A", resnum: "402", resname: "HOH" },
    ]);
    expect(selectionSummary(c, chosen)).toBe("1 di 1 gruppo scelto; 1 acqua scelta.");
    expect(selectionSummary(c.filter(isWater), { "3KS3|A|402|HOH": true, "3KS3|A|280|HOH": true }))
      .toBe("Nessun ligando, cofattore o metallo nelle catene scelte; 2 acque scelte.");
    // analisi in cache precedenti (senza acque) e la casella della Pipeline: nessuna acqua
    expect(waterCandidates({ id: "3KS3", chains: ["A"] }, analysis())).toEqual([]);
    expect(defaultTransfer({ id: "3KS3", chains: ["A"] }, withWaters).names).toEqual(["ZN"]);
  });

  it("intorno di un'acqua in una riga", () => {
    expect(waterText(WATERS[0])).toBe("coordina ZN A262 (Zn, 1,90 Å) · Thr A199 OG1 2,71 Å · B 6,1");
    expect(waterText(WATERS[1])).toBe("Asn A244 ND2 2,52 Å, His A96 ND1 2,81 Å, Phe A95 O 2,89 Å, Leu A60 O 3,01 Å e altri 1 · B 10,0");
  });
});
