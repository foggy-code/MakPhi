import type { LigandGroupCheck, LigandReport } from "../api/types";
import { fmtNum } from "../lib/format";
import { CLASS_LABEL, CLASS_TONE, coordinationText, donorColor, donorText, groupTitle, STATUS_TONE } from "../lib/ligands";
import { Badge, Card } from "./ui";

const keyOf = (g: LigandGroupCheck) => `${g.chain}|${g.resnum}|${g.resname}`;

/** Tabella degli esiti della verifica dei ligandi (pagina Qualità e risultati della Modellazione). */
export function LigandTable({ report, selected, onSelect }: { report: LigandReport; selected?: string | null; onSelect?: (g: LigandGroupCheck) => void }) {
  return (
    <div className="table-wrap">
      <table className="table">
        <thead><tr><th>Gruppo</th><th>Classe</th><th>Esito</th><th>Coordinazione</th><th className="num">Residui entro 4 Å</th><th>Motivazione</th></tr></thead>
        <tbody>
          {report.groups.map((g) => (
            <tr key={keyOf(g)} className={`${onSelect ? "clickable" : ""}${selected === keyOf(g) ? " selected" : ""}`} onClick={() => onSelect?.(g)}>
              <td className="nowrap">
                <b className="mono">{groupTitle(g)}</b>
                {g.description && <div className="tiny muted">{g.description}</div>}
                {g.template && <div className="tiny muted">da {g.template.code}: {g.template.group}</div>}
              </td>
              <td><Badge tone={CLASS_TONE[g.class]}>{g.label ?? CLASS_LABEL[g.class]}</Badge></td>
              <td><Badge tone={STATUS_TONE[g.status]}>{g.status}</Badge></td>
              <td className="small">{g.metals.length ? g.metals.map((m) => <div key={m.atom}>{coordinationText(m)}</div>) : <span className="muted">—</span>}</td>
              <td className="num" title={g.contacts.map((c) => `${c.resname} ${c.chain}${c.resnum}`).join(", ")}>{g.contacts.length}</td>
              <td className="small">{g.summary}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/** Dettaglio del gruppo scelto: donatori con le distanze attese e confronto con il sito del template. */
function LigandDetail({ group }: { group: LigandGroupCheck }) {
  return (
    <div className="stack-sm small">
      {group.metals.map((m) => (
        <div key={m.atom} className="stack-sm">
          <b>{coordinationText(m)}{m.cn_range ? ` · tipico ${m.cn_range[0]}–${m.cn_range[1]}` : ""}</b>
          {m.donors.length > 0 && (
            <div className="row" style={{ gap: 10 }}>
              {m.donors.map((d) => (
                <span key={`${d.chain}${d.resnum}${d.atom}`} className="nowrap">
                  <i style={{ display: "inline-block", width: 10, height: 3, background: donorColor(d.status), verticalAlign: "middle", marginRight: 4 }} />
                  {donorText(d)}{d.status !== "nella norma" ? ` · ${d.status}` : ""}
                </span>
              ))}
            </div>
          )}
          {m.template_site && (
            <div className="text-2">
              Sito del template: {m.template_site.conserved} su {m.template_site.protein_donors} donatori della proteina conservati
              {m.template_site.waters ? `, ${m.template_site.waters} ${m.template_site.waters === 1 ? "acqua non trasferita" : "acque non trasferite"}` : ""}
              {m.template_site.donors.length > 0 && ` (${m.template_site.donors.map((d) => `${d.template} ${d.atom} → ${d.target ?? "—"}: ${d.status}`).join("; ")})`}
              {m.template_site.new.length > 0 && `; nuovi donatori: ${m.template_site.new.join(", ")}`}.
            </div>
          )}
        </div>
      ))}
      {group.clashes.length > 0 && <div style={{ color: "var(--err)" }}>Scontri: {group.clashes.map((c) => `${c.atom}–${c.partner} ${fmtNum(c.distance, 2)} Å`).join("; ")}</div>}
      {group.links.length > 0 && <div className="text-2">{group.links.map((l) => `${l.confirmed ? "Legame covalente" : "Possibile legame covalente"} ${l.atom}–${l.partner}`).join("; ")}</div>}
      {group.contacts.length > 0 && <div className="muted">Residui entro 4 Å: {group.contacts.map((c) => `${c.resname} ${c.chain}${c.resnum}`).join(", ")}</div>}
    </div>
  );
}

/** Sezione "Ligandi e metalli" della pagina Qualità: esiti, dettaglio del gruppo scelto; il clic evidenzia nel 3D. */
export function LigandsPanel({ report, selected, onSelect }: { report: LigandReport; selected: LigandGroupCheck | null; onSelect: (g: LigandGroupCheck) => void }) {
  const c = report.counts;
  return (
    <Card
      title="Ligandi e metalli"
      sub={report.template_compared ? "contatti, scontri e coordinazione, con il confronto dei siti del template" : "contatti, scontri e coordinazione dei metalli"}
      right={<Badge tone={STATUS_TONE[report.status]}>{c.buono} buoni · {c["da controllare"]} da controllare · {c.problematico} problematici</Badge>}
    >
      <div className="stack-sm">
        <LigandTable report={report} selected={selected ? keyOf(selected) : null} onSelect={onSelect} />
        {selected && <LigandDetail group={selected} />}
        {report.notes.map((n, i) => <p key={i} className="tiny muted">{n}</p>)}
        <p className="tiny muted">
          Clic su un gruppo: evidenziato nel 3D con i residui coordinanti e le linee di coordinazione (verde nella norma, arancione da controllare, rosso troppo corta).
          Distanze attese da Harding (2006) ± 0,25 Å; geometria dagli angoli come CheckMyMetal (Zheng et al. 2017: gRMSD oltre 13,5° da controllare, oltre 21,5° anomala). Il voto A–D non ne tiene conto.
        </p>
      </div>
    </Card>
  );
}
