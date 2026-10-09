import { Fragment } from "react";
import type { LigandGroupCheck, LigandReport, MetalSite } from "../api/types";
import { fmtNum, plural } from "../lib/format";
import { CLASS_LABEL, CLASS_TONE, coordinationText, donorColor, groupTitle, STATUS_TONE } from "../lib/ligands";
import { Badge, Card, DetailGrid, ExpandButton, useExpanded } from "./ui";

const keyOf = (g: LigandGroupCheck) => `${g.chain}|${g.resnum}|${g.resname}`;
const COLUMNS = 7;

/**
 * Tabella degli esiti della verifica dei ligandi (pagina Qualità e risultati della Modellazione). Ogni riga si espande
 * con il dettaglio del gruppo; con `onSelect` il clic sceglie il gruppo (evidenziato nel 3D) e ne apre il dettaglio.
 */
export function LigandTable({ report, selected, onSelect }: { report: LigandReport; selected?: string | null; onSelect?: (g: LigandGroupCheck) => void }) {
  const open = useExpanded<string>(selected ? [selected] : []);
  return (
    <div className="table-wrap">
      <table className="table">
        <thead>
          <tr>
            <th style={{ width: 34 }} aria-label="Dettagli" />
            <th>Gruppo</th><th>Classe</th><th>Esito</th><th>Coordinazione</th><th className="num">Residui entro 4 Å</th><th>Motivazione</th>
          </tr>
        </thead>
        <tbody>
          {report.groups.map((g) => {
            const k = keyOf(g);
            const isOpen = open.has(k);
            return (
              <Fragment key={k}>
                <tr className={`clickable${selected === k ? " selected" : ""}${isOpen ? " row-open" : ""}`}
                  onClick={() => {
                    if (!onSelect) return open.toggle(k);
                    onSelect(g);
                    if (!isOpen) open.toggle(k);
                  }}>
                  <td><ExpandButton open={isOpen} onClick={() => open.toggle(k)} label={`dettagli di ${groupTitle(g)}`} /></td>
                  <td className="nowrap">
                    <b className="mono">{groupTitle(g)}</b>
                    {g.description && <div className="tiny text-2">{g.description}</div>}
                    {g.template && <div className="tiny text-2">da {g.template.code}: {g.template.group}</div>}
                  </td>
                  <td><Badge tone={CLASS_TONE[g.class]}>{g.label ?? CLASS_LABEL[g.class]}</Badge></td>
                  <td><Badge tone={STATUS_TONE[g.status]}>{g.status}</Badge></td>
                  <td className="small">
                    {g.metals.length ? g.metals.map((m) => <div key={m.atom}>{coordinationText(m)}</div>)
                      : g.water?.metals.length ? `coordina ${g.water.metals.map((m) => m.label).join(", ")}`
                      : <span className="text-2">—</span>}
                  </td>
                  <td className="num" title={g.contacts.map((c) => `${c.resname} ${c.chain}${c.resnum}`).join(", ")}>{g.contacts.length}</td>
                  <td className="small">{g.summary}</td>
                </tr>
                {isOpen && (
                  <tr className="row-detail">
                    <td colSpan={COLUMNS}><LigandDetail group={g} /></td>
                  </tr>
                )}
              </Fragment>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

/** Sito di un metallo: geometria, donatori con le distanze attese e confronto con il sito del template. */
function MetalDetail({ metal: m }: { metal: MetalSite }) {
  const site = m.template_site;
  return (
    <div className="stack-sm">
      <b>{coordinationText(m)}{m.cn_range ? ` · tipico ${m.cn_range[0]}–${m.cn_range[1]}` : ""}</b>
      <DetailGrid items={[
        { label: "Atomo", value: <span className="mono">{m.atom}</span> },
        { label: "Numero di coordinazione", value: m.coordination_number },
        { label: "Geometria", value: m.geometry ? `${m.geometry.name} (${m.geometry.positions} posizioni)` : "non assegnata" },
        { label: "gRMSD", value: m.geometry ? `${fmtNum(m.geometry.grmsd, 1)}°` : "—" },
        // vacancy è la frazione di posizioni libere ((m − n) / m), non un conteggio
        { label: "Posizioni libere", value: m.geometry ? `${Math.round(m.geometry.vacancy * m.geometry.positions)} su ${m.geometry.positions} (${fmtNum(100 * m.geometry.vacancy, 0)}%)` : "—" },
        { label: "Distanze di riferimento", value: m.reference ? "Harding (2006)" : "nessun riferimento per l'elemento" },
      ]} />
      {m.donors.length > 0 && (
        <div className="table-wrap">
          <table className="table">
            <thead><tr><th>Donatore</th><th className="num">Distanza (Å)</th><th className="num">Attesa (Å)</th><th className="num">Scarto (Å)</th><th>Esito</th></tr></thead>
            <tbody>
              {m.donors.map((d) => (
                <tr key={`${d.chain}${d.resnum}${d.atom}`}>
                  <td className="nowrap">
                    <i aria-hidden="true" style={{ display: "inline-block", width: 12, height: 4, borderRadius: 2, background: donorColor(d.status), verticalAlign: "middle", marginRight: 6 }} />
                    {d.label}{d.water ? " (acqua)" : ""}
                  </td>
                  <td className="num">{fmtNum(d.distance, 2)}</td>
                  <td className="num">{fmtNum(d.expected, 2)}</td>
                  <td className="num">{d.deviation != null ? `${d.deviation > 0 ? "+" : d.deviation < 0 ? "−" : ""}${fmtNum(Math.abs(d.deviation), 2)}` : "—"}</td>
                  <td><Badge tone={d.status === "nella norma" ? "ok" : d.status === "troppo corta" ? "err" : "warn"}>{d.status}</Badge></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {site && (
        <div className="small">
          Sito del template: {site.conserved} su {site.protein_donors} donatori della proteina conservati
          {site.waters ? `, ${plural(site.waters, "acqua non trasferita", "acque non trasferite")}` : ""}
          {site.new.length > 0 && `; nuovi donatori: ${site.new.join(", ")}`}.
          {site.donors.length > 0 && (
            <ul style={{ margin: "4px 0 0", paddingLeft: 18 }}>
              {site.donors.map((d) => <li key={`${d.template}${d.atom}`}>{d.template} {d.atom} → {d.target ?? "—"}: {d.status}</li>)}
            </ul>
          )}
        </div>
      )}
    </div>
  );
}

/** Acqua trasferita: legami H e metalli nel modello, residui del sito del template ancora legati. */
function WaterDetail({ water: w }: { water: NonNullable<LigandGroupCheck["water"]> }) {
  return (
    <div className="small stack-sm">
      <div>
        <span className="text-2">Legami H (entro 3,5 Å): </span>
        {w.hbonds.length ? w.hbonds.map((h) => `${h.label} ${fmtNum(h.distance, 2)} Å`).join(", ") : "nessuno"}
      </div>
      {w.metals.length > 0 && <div><span className="text-2">Metalli coordinati: </span>{w.metals.map((m) => `${m.label} ${fmtNum(m.distance, 2)} Å`).join(", ")}</div>}
      {w.template_partners != null && w.template_partners > 0 && (
        <div>
          Sito del template: legata a {w.kept_partners} dei {w.template_partners} residui del template
          {w.lost_partners.length > 0 && ` (persi: ${w.lost_partners.join(", ")})`}.
        </div>
      )}
    </div>
  );
}

/** Dettaglio del gruppo: motivazioni, metalli, acqua, scontri, legami covalenti e residui vicini con la distanza. */
function LigandDetail({ group }: { group: LigandGroupCheck }) {
  return (
    <div className="stack">
      <DetailGrid items={[
        { label: "Gruppo", value: <span className="mono">{groupTitle(group)}</span> },
        { label: "Atomi", value: group.atoms },
        { label: "Classe", value: group.label ?? CLASS_LABEL[group.class] },
        { label: "Esito", value: <Badge tone={STATUS_TONE[group.status]}>{group.status}</Badge> },
        { label: "Dal template", value: group.template ? `${group.template.code}: ${group.template.group}` : "—" },
        { label: "Residui entro 4 Å", value: group.contacts.length },
      ]} />
      {group.reasons.length > 0 && (
        <ul className="small" style={{ margin: 0, paddingLeft: 18 }}>
          {group.reasons.map((r, i) => <li key={i}>{r}</li>)}
        </ul>
      )}
      {group.metals.map((m) => <MetalDetail key={m.atom} metal={m} />)}
      {group.water && <WaterDetail water={group.water} />}
      {group.clashes.length > 0 && (
        <div className="small"><b style={{ color: "var(--err)" }}>Scontri:</b> {group.clashes.map((c) => `${c.atom}–${c.partner} ${fmtNum(c.distance, 2)} Å`).join("; ")}</div>
      )}
      {group.links.length > 0 && (
        <div className="small">{group.links.map((l) => `${l.confirmed ? "Legame covalente" : "Possibile legame covalente"} ${l.atom}–${l.partner} (${fmtNum(l.distance, 2)} Å)`).join("; ")}</div>
      )}
      {group.contacts.length > 0 && (
        <div className="small">
          <span className="text-2">Residui entro 4 Å: </span>
          {group.contacts.map((c) => `${c.resname} ${c.chain}${c.resnum} (${fmtNum(c.distance, 1)} Å)`).join(", ")}
        </div>
      )}
    </div>
  );
}

/** Sezione "Ligandi e metalli" della pagina Qualità: esiti con il dettaglio di ogni gruppo; il clic evidenzia nel 3D. */
export function LigandsPanel({ report, selected, onSelect }: { report: LigandReport; selected: LigandGroupCheck | null; onSelect: (g: LigandGroupCheck) => void }) {
  const c = report.counts;
  return (
    <Card
      title="Ligandi e metalli"
      icon="flask"
      collapsible
      storageKey="quality.ligands"
      sub={report.template_compared ? "contatti, scontri e coordinazione, con il confronto dei siti del template" : "contatti, scontri e coordinazione dei metalli"}
      right={<Badge tone={STATUS_TONE[report.status]}>{c.buono} buoni · {c["da controllare"]} da controllare · {c.problematico} problematici</Badge>}
    >
      <div className="stack-sm">
        <LigandTable report={report} selected={selected ? keyOf(selected) : null} onSelect={onSelect} />
        {report.notes.map((n, i) => <p key={i} className="small text-2">{n}</p>)}
        <p className="small text-2">
          Clic su un gruppo: evidenziato nel 3D con i residui coordinanti e le linee di coordinazione (verde nella norma, arancione da
          controllare, rosso troppo corta). Distanze attese da Harding (2006) ± 0,25 Å; geometria dagli angoli come CheckMyMetal (Zheng
          et al. 2017: gRMSD oltre 13,5° da controllare, oltre 21,5° anomala). Il voto A–D non ne tiene conto.
        </p>
      </div>
    </Card>
  );
}
