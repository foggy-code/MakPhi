// Parti comuni delle pagine del flusso di lavoro (Sequenze, Libreria template, Ricerca template, procedura guidata,
// Editor PIR): metadati a chip, sezioni dei riquadri e dettaglio di un hit della ricerca (campi, punteggio con le sue
// componenti, allineamento).
import type { ReactNode } from "react";
import type { SearchHit } from "../api/types";
import { fmtEvalue, fmtInt, fmtNum } from "../lib/format";
import { rankBand, rankTone } from "../lib/templateRank";
import { rankContributions, toneColor } from "../lib/workflowView";
import { Icon } from "./Icon";
import { Badge, BarCell, DetailGrid, Disclosure } from "./ui";
import "../styles/sequences.css";

const present = (v: ReactNode) => v !== null && v !== undefined && v !== "";

/** Metadati in evidenza come chip «etichetta valore»; le voci senza valore non compaiono. */
export function InfoChips({ items }: { items: { label: string; value: ReactNode; title?: string }[] }) {
  const shown = items.filter((it) => present(it.value));
  if (!shown.length) return null;
  return (
    <div className="wf-chips">
      {shown.map((it) => <span key={it.label} className="chip" title={it.title}>{it.label} <b>{it.value}</b></span>)}
    </div>
  );
}

/** Sezione di un riquadro: titolo piccolo, nota facoltativa e contenuto, separata dalla precedente. */
export function FormSection({ title, hint, children }: { title: string; hint?: ReactNode; children: ReactNode }) {
  return (
    <div className="wf-section">
      <div className="wf-section-head">
        <h4>{title}</h4>
        {hint && <span className="hint">{hint}</span>}
      </div>
      {children}
    </div>
  );
}

// ---------------------------------------------------------------------------------- hit della ricerca
/** Tratto della query allineato dall'hit, colorato per copertura. */
export function HitCoverageBar({ hit, qlen }: { hit: SearchHit; qlen: number }) {
  const n = Math.max(1, qlen);
  const left = ((hit.query_start - 1) / n) * 100;
  const width = ((hit.query_end - hit.query_start + 1) / n) * 100;
  return (
    <div className="wf-coverage" title={`Residui ${hit.query_start}–${hit.query_end} della query`}>
      <div style={{ left: `${left}%`, width: `${width}%`, background: hit.coverage >= 80 ? "var(--ok)" : hit.coverage >= 50 ? "var(--accent)" : "var(--warn)" }} />
    </div>
  );
}

/** Allineamento locale query–template a righe di 80 colonne: «|» identici, «·» sostituzioni, spazio nei gap. */
export function HitAlignment({ hit }: { hit: SearchHit }) {
  if (!hit.query_aligned) return <p className="small text-2">Allineamento non disponibile per questo risultato.</p>;
  const mid = hit.query_aligned
    .split("")
    .map((c, i) => (c === hit.subject_aligned[i] ? "|" : c !== "-" && hit.subject_aligned[i] !== "-" ? "·" : " "))
    .join("");
  const name = hit.template_id.padEnd(9, " ").slice(0, 9);
  const rows = [];
  for (let i = 0; i < hit.query_aligned.length; i += 80) {
    rows.push(
      <div key={i} style={{ marginBottom: 8 }}>
        <div>Query    {hit.query_aligned.slice(i, i + 80)}</div>
        <div className="text-2">         {mid.slice(i, i + 80)}</div>
        <div>{name}{hit.subject_aligned.slice(i, i + 80)}</div>
      </div>,
    );
  }
  return (
    <div className="stack-sm">
      <pre className="seq-block" style={{ whiteSpace: "pre", wordBreak: "normal", maxHeight: 240 }}>{rows}</pre>
      <span className="tiny text-2">«|» residui identici · «·» sostituzioni · spazio: gap. Query {hit.query_start}–{hit.query_end}, template {hit.subject_start}–{hit.subject_end}.</span>
    </div>
  );
}

/** Tutti i campi noti di un hit: struttura, statistiche dell'allineamento, modello predetto, collegamento esterno. */
export function HitFields({ hit, queryLength }: { hit: SearchHit; queryLength: number }) {
  const link = hit.predicted
    ? hit.uniprot_accession ? { href: `https://alphafold.ebi.ac.uk/entry/${encodeURIComponent(hit.uniprot_accession)}`, label: "AlphaFold DB" } : null
    : { href: `https://www.rcsb.org/structure/${encodeURIComponent(hit.pdb_id)}`, label: "RCSB PDB" };
  const items: { label: string; value: ReactNode }[] = [
    { label: "Struttura", value: `${hit.pdb_id} · catena ${hit.chain}` },
    { label: "Titolo", value: hit.title || hit.description },
    { label: "Organismo", value: hit.organism },
    { label: "Metodo", value: hit.method },
    hit.predicted
      ? { label: "pLDDT medio", value: hit.plddt !== null && hit.plddt !== undefined ? fmtNum(hit.plddt, 1) : null }
      : { label: "Risoluzione", value: hit.resolution ? `${fmtNum(hit.resolution, 2)} Å` : null },
    { label: "R-free", value: hit.rfree !== null && hit.rfree !== undefined ? fmtNum(hit.rfree, 3) : null },
    { label: "Rilascio", value: hit.released },
    { label: "Stato oligomerico", value: hit.oligomeric_state ? `${hit.oligomeric_state}${hit.oligomeric_count ? ` (${hit.oligomeric_count} catene)` : ""}` : null },
    { label: "Ligandi", value: hit.ligands ? (hit.ligands.length ? hit.ligands.join(", ") : "nessuno") : null },
    { label: "Identità", value: `${fmtNum(hit.identity, 1)}%` },
    { label: "Positivi", value: hit.positives !== null && hit.positives !== undefined ? `${fmtNum(hit.positives, 1)}%` : null },
    { label: "Copertura della query", value: `${fmtNum(hit.coverage, 1)}% · residui ${hit.query_start}–${hit.query_end} di ${queryLength}` },
    { label: "Tratto del template", value: `${hit.subject_start}–${hit.subject_end} di ${hit.subject_length}` },
    { label: "Lunghezza allineata", value: `${fmtInt(hit.align_length)} colonne` },
    { label: "E-value", value: fmtEvalue(hit.evalue) },
    { label: "Bit score", value: fmtNum(hit.bitscore, 1) },
    { label: "Accessione UniProt", value: hit.uniprot_accession ? `${hit.uniprot_accession}${hit.uniprot_start && hit.uniprot_end ? ` (residui ${hit.uniprot_start}–${hit.uniprot_end})` : ""}` : null },
    { label: "Versione del modello", value: hit.model_version ? `v${hit.model_version}` : null },
    { label: "Origine", value: hit.source },
  ];
  return (
    <div className="stack-sm">
      <h4>Dati del template</h4>
      <DetailGrid items={items.filter((it) => present(it.value))} />
      {link && (
        <a className="small" href={link.href} target="_blank" rel="noreferrer">
          Scheda su {link.label} <Icon name="external" size={12} />
        </a>
      )}
    </div>
  );
}

/** Punteggio del template e sue componenti: punteggio, peso, punti nella media pesata, motivazione. */
export function RankBreakdown({ hit }: { hit: SearchHit }) {
  if (hit.rank_score === undefined) {
    return <p className="small text-2">Punteggio non disponibile: ricerca di una versione precedente, rilanciala per vederlo.</p>;
  }
  return (
    <div className="stack-sm">
      <div className="row" style={{ gap: 8 }}>
        <h4>Punteggio {fmtNum(hit.rank_score, 1)}/100</h4>
        <Badge tone={rankTone(hit.rank_score)}>fascia {hit.rank_band ?? rankBand(hit.rank_score)}</Badge>
        {hit.rank_twilight && <Badge tone="warn" title="Identità sotto la curva di Rost 1999 per la lunghezza allineata">zona crepuscolare</Badge>}
      </div>
      <div className="table-wrap">
        <table className="table wf-rank">
          <thead>
            <tr><th>Componente</th><th>Punteggio</th><th className="num">Peso</th><th className="num">Punti</th></tr>
          </thead>
          <tbody>
            {rankContributions(hit.rank_reasons).map((r) => (
              <tr key={r.key}>
                <td><b>{r.label}</b><div className="tiny text-2">{r.text}</div></td>
                <td>
                  {r.score === null
                    ? <span className="small text-2">{r.weight > 0 ? "non conteggiata" : "informativa"}</span>
                    : <BarCell value={r.score} tone={toneColor(rankTone(r.score))} label={fmtNum(r.score, 0)} />}
                </td>
                <td className="num">{fmtNum(r.weight, 0)}</td>
                <td className="num">{r.points === null ? "—" : fmtNum(r.points, 1)}</td>
              </tr>
            ))}
          </tbody>
          <tfoot>
            <tr><td colSpan={3}>Media pesata delle componenti conteggiate</td><td className="num">{fmtNum(hit.rank_score, 1)}</td></tr>
          </tfoot>
        </table>
      </div>
    </div>
  );
}

/** Dettaglio completo di un hit (riga espansa): campi e punteggio affiancati, allineamento, poi gli avvisi passati. */
export function HitPanel({ hit, queryLength, children }: { hit: SearchHit; queryLength: number; children?: ReactNode }) {
  return (
    <div className="stack">
      <div className="wf-hit">
        <HitFields hit={hit} queryLength={queryLength} />
        <RankBreakdown hit={hit} />
      </div>
      <Disclosure title="Allineamento query–template" extra={`${fmtInt(hit.align_length)} colonne · identità ${fmtNum(hit.identity, 1)}%`} defaultOpen>
        <HitAlignment hit={hit} />
      </Disclosure>
      {children}
    </div>
  );
}
