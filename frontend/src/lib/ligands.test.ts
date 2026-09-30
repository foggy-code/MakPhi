import { describe, expect, it } from "vitest";
import type { HeteroGroup, LigandGroupCheck, MetalSite, TemplateAnalysis } from "../api/types";
import {
  candidateGroups, coordinationLines, coordinationText, DONOR_COLOR, donorText, groupClass, isDefault, ligandFocus, ligandKey, ligandOptions, sameSelection,
  selectionSummary, syncSelection,
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
