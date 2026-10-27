import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import type { ConsensusResult, LigandGroupCheck, LigandReport, QualityReport, ResidueRow } from "../../api/types";
import { LigandTable } from "../LigandsPanel";
import { ClashTable } from "./ClashTable";
import { ConsensusPanel } from "./ConsensusPanel";
import { ProsaProfile } from "./ProsaProfile";
import { RamaTab } from "./RamaTab";
import { GradeBreakdown, SecondaryStructureCard, StructureMeta, SummaryStats } from "./ReportSummary";
import { ResidueDetail, ResidueTable } from "./ResidueTable";

// Pagina Qualità: markup statico (react-dom/server) senza provider, come gli altri test dei componenti.
const noop = () => undefined;

function row(i: number, extra: Partial<ResidueRow> = {}): ResidueRow {
  return {
    chain: "A", resnum: String(i), resname: "ALA", aa: "A", ss: "H", ss3: "H", rsa: 0.3, bfactor: 20, phi: -60, psi: -45,
    rama: "favored", clashes: 0, dope: -0.04, ...extra,
  };
}

const residues = [row(1), row(2, { resname: "GLY", rama: "outlier", phi: 80, psi: -170, clashes: 1, rsa: 0.1 }), row(3, { ss: "T", ss3: "C" }), row(4)];

const REPORT = {
  structure: { file: "model.pdb", title: "Modello di prova", method: "", resolution: null, chains: 1, residues: 4, heavy_atoms: 32, chains_detail: [] },
  grade: { score: 79.2, grade: "B", penalties: { ramachandran: 16, clashes: 4.8, dope: 0 } },
  ramachandran: {
    summary: { total: 4, favored: 3, allowed: 0, outlier: 1, favored_pct: 75, allowed_pct: 0, outlier_pct: 25, cis_non_proline: 0 },
    points: residues.map((r) => ({ chain: r.chain, resnum: r.resnum, resname: r.resname, aa: r.aa, phi: r.phi!, psi: r.psi!, class: r.resname === "GLY" ? "glycine" : "general", region: r.rama! })),
    regions: {},
  },
  clashes: { clashscore: 7.2, count: 1, atoms: 32, clashes: [{ atom1: "A 2 GLY CA", atom2: "A 4 ALA O", distance: 2.61, overlap: 0.52 }] },
  secondary_structure: { method: "DSSP integrato", helix_pct: 75, strand_pct: 0, coil_pct: 25, counts: { H: 3, E: 0, C: 1 }, detail: { H: 3, T: 1 } },
  sasa: { method: "Shrake-Rupley", mean_rsa: 0.25, buried_pct: 25 },
  bfactor: { mean: 20, max: 20 },
  dope: { score: -1234.5, zscore: -1.4, profile: [] },
  residues,
  warnings: [],
} as unknown as QualityReport;

const CONSENSUS = {
  version: 2, score: 64.4, band: "intermedia", methods_used: 2, methods_total: 6,
  methods: [
    { key: "grade", label: "Voto MakPhi", raw: 79.2, normalized: 79.2, weight: 1, band: "buona", measures: "stereochimica", source: "domain/grading", points: [[0, 0], [55, 40], [70, 70], [100, 100]] },
    { key: "zdope", label: "z-DOPE", raw: -1.4, normalized: 49.6, weight: 1, band: "intermedia", measures: "energia statistica", source: "Shen & Sali 2006", points: [[-2, 100], [-1, 70], [0, 40], [2, 0]] },
  ],
  missing: [{ key: "qmean", label: "QMEAN4 Z-score", reason: "non calcolato" }],
  discordances: [],
  per_residue: {
    signals: [{ key: "rama", label: "Ramachandran", rule: "outlier", flagged: 1 }],
    flags: [[], ["rama", "clash"], [], []], counts: [0, 2, 0, 0], values: {}, dope_threshold: null,
  },
  regions: [{ chain: "A", start: "1", end: "3", start_index: 0, end_index: 2, length: 3, flagged: 1, methods: ["rama", "clash"], text: "Ramachandran e clash segnalano 1-3 (catena A)", expected: true }],
  thresholds: {}, warnings: [],
} as unknown as ConsensusResult;

describe("intestazione e valori riassuntivi", () => {
  it("metadati in chip, consenso e voto calcolato riga per riga", () => {
    const meta = renderToStaticMarkup(<StructureMeta report={REPORT} consensus={CONSENSUS} />);
    expect(meta).toContain("<b>4</b> residui");
    expect(meta).toContain("<b>1</b> catena");
    expect(meta).toContain("consenso 64/100 · intermedia");
    const grade = renderToStaticMarkup(<GradeBreakdown report={REPORT} />);
    expect(grade).toContain("Come è calcolato il voto");
    expect(grade).toContain("79,2/100 · voto B");
    expect(grade).toContain("Penalità Ramachandran");
    expect(grade).toContain("−16,0");
    expect(grade).toContain("A da 85, B da 70, C da 55, D sotto");
  });
  it("esito colorato e pulsante Dettagli per ogni valore", () => {
    const html = renderToStaticMarkup(<SummaryStats report={REPORT} />);
    expect(html).toContain('class="stat stat-err"'); // 25% di outlier
    expect(html).toContain('class="stat stat-warn"'); // clashscore 7,2
    expect(html).toContain('class="stat stat-ok"'); // zDOPE −1,4
    expect(html.match(/class="stat-more"/g)).toHaveLength(4);
  });
  it("struttura secondaria: grafico con PNG ed Espandi, stati DSSP a scomparsa", () => {
    const html = renderToStaticMarkup(<SecondaryStructureCard report={REPORT} />);
    expect(html).toContain("Composizione della struttura secondaria");
    expect(html).toContain("PNG");
    expect(html).toContain("Espandi");
    expect(html).toContain("Dettaglio degli stati DSSP");
    expect(html).toContain("α-elica");
  });
});

describe("consenso", () => {
  it("metodi con contributo e righe espandibili, scale e fonti, regioni in tabella", () => {
    const html = renderToStaticMarkup(<ConsensusPanel report={REPORT} consensus={CONSENSUS} error={null} selected={null} onSelect={noop} onRegion={noop} />);
    expect(html).toContain("Punteggio di consenso");
    expect(html).toContain('class="stat stat-warn"');
    expect(html).toContain('aria-label="Mostra dettagli di Voto MakPhi"');
    expect(html).toContain(">39,6<"); // 79,2 × 1 / 2
    expect(html).toContain("Scale e fonti");
    expect(html).toContain("Metodi non disponibili");
    expect(html).toContain("disordine predetto");
    expect(html).toContain("Mostra nel 3D");
  });
  it("senza consenso: solo l'errore, se c'è", () => {
    expect(renderToStaticMarkup(<ConsensusPanel report={REPORT} consensus={null} error={null} selected={null} onSelect={noop} onRegion={noop} />)).toBe("");
    expect(renderToStaticMarkup(<ConsensusPanel report={REPORT} consensus={null} error="rotta non raggiungibile" selected={null} onSelect={noop} onRegion={noop} />))
      .toContain("rotta non raggiungibile");
  });
});

describe("schede dei residui", () => {
  it("Ramachandran: riepilogo per regione e outlier come chip con angoli e classe", () => {
    const html = renderToStaticMarkup(<RamaTab report={REPORT} consensus={null} selected={{ chain: "A", resnum: "2" }} onSelect={noop} onShow3d={noop} onTable={noop} />);
    expect(html).toContain("Outlier (1)");
    expect(html).toContain('aria-pressed="true"');
    expect(html).toContain("φ 80°, ψ −170°");
    expect(html).toContain("Glicina");
    expect(html).toContain("Regioni, classi e colori");
    expect(html).toContain("Dettagli di GLY A2");
  });
  it("Ramachandran senza residui valutabili: nessun esito colorato", () => {
    const empty = { ...REPORT, ramachandran: { ...REPORT.ramachandran, points: [],
      summary: { total: 0, favored: 0, allowed: 0, outlier: 0, favored_pct: 0, allowed_pct: 0, outlier_pct: 0, cis_non_proline: 0 } } } as QualityReport;
    const html = renderToStaticMarkup(<RamaTab report={empty} consensus={null} selected={null} onSelect={noop} onShow3d={noop} onTable={noop} />);
    expect(html).toContain("Nessun residuo con angoli φ/ψ valutabili");
    expect(html).not.toContain("stat-err");
    expect(html).not.toContain("Outlier (0)");
    expect(renderToStaticMarkup(<SummaryStats report={empty} />)).toContain('<div class="stat"><div class="label">Ramachandran favorite<');
  });
  it("dettaglio del residuo: metriche, segnalazioni del consenso, scontri e pulsanti", () => {
    const html = renderToStaticMarkup(<ResidueDetail report={REPORT} selected={{ chain: "A", resnum: "2" }} consensus={CONSENSUS} onShow3d={noop} onTable={noop} onClose={noop} />);
    expect(html).toContain("problematico");
    expect(html).toContain("segnalato da Ramachandran, clash");
    expect(html).toContain("0,10 · sepolto");
    expect(html).toContain("CA – A 4 ALA O 2,61 Å");
    expect(html).toContain("Mostra nel 3D");
    expect(html).toContain("Mostra nella tabella");
    expect(renderToStaticMarkup(<ResidueDetail report={REPORT} selected={{ chain: "B", resnum: "9" }} consensus={null} />)).toBe("");
  });
  it("tabella dei residui: problematici per impostazione, con la ricerca", () => {
    const html = renderToStaticMarkup(<ResidueTable residues={residues} onPick={noop} selected={null} consensus={CONSENSUS} plddt={false} />);
    expect(html).toContain("Solo residui problematici (1)");
    expect(html).toContain("<b>GLY</b> A2");
    expect(html).not.toContain("<b>ALA</b> A1");
    expect(html).toContain('placeholder="Cerca un residuo (es. A45, GLY)"');
  });
  it("contatti sterici: righe espandibili, sovrapposizione a barra, 3D", () => {
    const html = renderToStaticMarkup(<ClashTable report={REPORT} onShow={noop} />);
    expect(html).toContain("1 contatti gravi su 32 atomi pesanti");
    expect(html).toContain('aria-label="Mostra dettagli dello scontro"');
    expect(html).toContain("A 2 GLY CA");
    expect(html).toContain("0,52");
    expect(renderToStaticMarkup(<ClashTable report={{ ...REPORT, clashes: { ...REPORT.clashes, clashes: [] } }} onShow={noop} />)).toContain("Nessun contatto sterico grave");
  });
});

describe("profilo ProSA", () => {
  const profile = residues.map((_r, i) => ({ index: i + 1, energy: 0.1 * i, energy_w10: i === 0 ? null : i === 2 ? 0.4 : -0.5 }));
  it("cornice con PNG, CSV, Espandi, zoom e i tratti sopra lo zero", () => {
    const html = renderToStaticMarkup(<ProsaProfile profile={profile} residues={residues} selected={{ chain: "A", resnum: "3" }} />);
    expect(html).toContain('class="chart-frame-title">Profilo di energia ProSA (finestra 10)<');
    expect(html).toContain("CSV");
    expect(html).toContain("Espandi");
    expect(html).toContain("residui 2–4");
    expect(html).toContain("Tratti sopra lo zero (1)");
    expect(html).toContain("Residuo selezionato");
  });
  it("senza almeno due valori niente grafico", () => {
    expect(renderToStaticMarkup(<ProsaProfile profile={[{ index: 1, energy: 0, energy_w10: 0.2 }]} />)).toBe("");
  });
});

describe("ligandi", () => {
  const group = {
    resname: "ZN", chain: "A", resnum: "101", atoms: 1, class: "metal", label: "metallo", status: "da controllare", summary: "1 donatore",
    reasons: ["numero di coordinazione basso"], contacts: [{ chain: "A", resnum: "68", resname: "HIS", distance: 2.1 }], clashes: [], links: [],
    metals: [{
      atom: "ZN", element: "ZN", symbol: "Zn", coord: [0, 0, 0], coordination_number: 1, geometry: null, cn_range: [4, 6], reference: true,
      donors: [{ chain: "A", resnum: "68", resname: "HIS", atom: "NE2", protein: true, label: "His A68 NE2", category: "N_his", donor: "N (His)", distance: 2.1,
        expected: 2.03, deviation: 0.07, status: "nella norma", water: false, same_group: false, coord: [1, 1, 1] }],
    }],
  } as unknown as LigandGroupCheck;
  const report = { version: 1, groups: [group], counts: { buono: 0, "da controllare": 1, problematico: 0 }, status: "da controllare", template_compared: false, thresholds: {}, notes: [] } as LigandReport;
  it("righe espandibili; il gruppo scelto è aperto con donatori, motivazioni e residui vicini", () => {
    const closed = renderToStaticMarkup(<LigandTable report={report} />);
    expect(closed).toContain('aria-label="Mostra dettagli di ZN A101"');
    expect(closed).not.toContain("row-detail");
    const open = renderToStaticMarkup(<LigandTable report={report} selected="A|101|ZN" onSelect={noop} />);
    expect(open).toContain("row-detail");
    expect(open).toContain("numero di coordinazione basso");
    expect(open).toContain("His A68 NE2");
    expect(open).toContain("+0,07");
    expect(open).toContain("HIS A68 (2,1 Å)");
  });
  it("posizioni libere della geometria come conteggio, non come frazione", () => {
    const metal = { ...group.metals[0], geometry: { key: "square_pyramidal", name: "piramide a base quadrata", positions: 5, grmsd: 14.1, vacancy: 0.4 } };
    const html = renderToStaticMarkup(<LigandTable report={{ ...report, groups: [{ ...group, metals: [metal] }] }} selected="A|101|ZN" onSelect={noop} />);
    expect(html).toContain("2 su 5 (40%)");
    expect(html).not.toContain(">0.4<");
  });
});

describe("tastiera, pannelli e comandi comuni", () => {
  it("righe della tabella dei residui raggiungibili con Tab, la selezionata esposta", () => {
    const html = renderToStaticMarkup(<ResidueTable residues={residues} onPick={noop} selected={{ chain: "A", resnum: "2" }} consensus={CONSENSUS} plddt={false} />);
    expect(html).toMatch(/<tr(?=[^>]*\btabindex="0")(?=[^>]*\baria-selected="true")(?=[^>]*\bclass="clickable selected)[^>]*>/);
    expect(html).toContain("input input-sm quality-search");
    expect(html).toMatch(/\bbadge-err\b[^>]*>outlier</); // etichetta breve della regione, comune (lib/ramachandran)
  });
  it("dettaglio del residuo nel pannello comune, con il nome del pulsante di chiusura", () => {
    const html = renderToStaticMarkup(<ResidueDetail report={REPORT} selected={{ chain: "A", resnum: "2" }} consensus={null} onClose={noop} />);
    expect(html).toContain('class="detail-panel"');
    expect(html).toContain('aria-label="Chiudi i dettagli del residuo"');
    expect(html).not.toContain("residue-detail");
  });
  it("«Apri tutti» al maschile per contatti e metodi, con il nome completo", () => {
    expect(renderToStaticMarkup(<ClashTable report={REPORT} onShow={noop} />)).toContain('aria-label="Apri tutti i contatti"');
    const consensus = renderToStaticMarkup(<ConsensusPanel report={REPORT} consensus={CONSENSUS} error={null} selected={null} onSelect={noop} onRegion={noop} />);
    expect(consensus).toContain('aria-label="Apri tutti i metodi"');
    expect(consensus).not.toContain("Espandi tutti");
    // l'esito del consenso a parole: la fascia, non solo il colore del bordo
    expect(consensus).toMatch(/class="stat-verdict">.*fascia intermedia/);
  });
  it("struttura secondaria: lo stato E si chiama filamento β, come in DSSP e nel backend", () => {
    const html = renderToStaticMarkup(<SecondaryStructureCard report={REPORT} />);
    expect(html).toContain("Filamento β");
    expect(html).not.toContain("Foglietto");
  });
  it("profilo ProSA: intervallo nello stesso formato dei profili per residuo", () => {
    const profile = residues.map((_r, i) => ({ index: i + 1, energy: 0.1 * i, energy_w10: i === 0 ? null : -0.5 }));
    expect(renderToStaticMarkup(<ProsaProfile profile={profile} residues={residues} />)).toContain("residui 2–4 (4 in tutto)");
  });
});
