import { Fragment } from "react";
import type { ConsensusMethod, ConsensusRegion, ConsensusResult, QualityReport, ResidueRow } from "../../api/types";
import { fmtNum, plural } from "../../lib/format";
import { consensusPredictorNote } from "../../lib/predict";
import { BAND_TONE, methodContribution, scaleText, totalWeight } from "../../lib/qualityView";
import { SIGNAL_LABEL, type ResidueRef } from "../../lib/residueColoring";
import { ConsensusTracks } from "../charts";
import { Icon } from "../Icon";
import { Alert, Badge, BarCell, Card, DetailGrid, Disclosure, ExpandAll, ExpandButton, Stat, useExpanded } from "../ui";

function fmtRaw(m: ConsensusMethod): string {
  if (m.key === "grade") return `${fmtNum(m.raw, 1)}/100`;
  if (m.key === "procheck") return `${fmtNum(m.raw, 1)}%`;
  return fmtNum(m.raw, 2);
}

const COLUMNS = 7;

/** Consenso tra i metodi: punteggio, metodi con scala e fonte, discordanze, regioni e segnalazioni per residuo. */
export function ConsensusPanel({ report, consensus, error, selected, onSelect, onRegion }: {
  report: QualityReport;
  consensus: ConsensusResult | null;
  error: string | null;
  selected: ResidueRef | null;
  onSelect: (r: ResidueRow) => void;
  onRegion: (region: ConsensusRegion) => void;
}) {
  const open = useExpanded<string>();
  if (!consensus) {
    return error ? <Card title="Consenso tra i metodi" icon="layers"><Alert tone="warn">{error}</Alert></Card> : null;
  }
  const c = consensus;
  const weights = totalWeight(c);
  const predictor = consensusPredictorNote(c);
  const allOpen = c.methods.length > 0 && c.methods.every((m) => open.has(m.key));
  return (
    <Card title="Consenso tra i metodi" icon="layers" sub="scale e pesi dichiarati in domain/consensus.py"
      right={c.band && <Badge tone={BAND_TONE[c.band]}>fascia {c.band}</Badge>}>
      <div className="stack">
        {error && <Alert tone="warn">{error}</Alert>}
        <div className="quality-consensus-top">
          <Stat
            label="Punteggio di consenso"
            value={<>{fmtNum(c.score, 0)}<small>/100</small></>}
            tone={c.band ? BAND_TONE[c.band] : undefined}
            verdict={c.band ? `fascia ${c.band}` : undefined}
            note={`${c.methods_used} metodi su ${c.methods_total} · buona ≥ 70, scarsa < 40`}
            detail={(
              <>
                <span>Media pesata: Σ (normalizzato × peso) / Σ pesi = {fmtNum(c.score, 1)}.</span>
                <DetailGrid items={c.methods.map((m) => ({ label: m.label, value: `${fmtNum(m.normalized, 0)} × ${fmtNum(m.weight, 2)} → ${fmtNum(methodContribution(c, m), 1)} punti` }))} />
              </>
            )}
          />
          <p className="small text-2">
            Ogni metodo è portato su 0–100 con una scala lineare a tratti costruita sulle soglie della letteratura (70 = soglia di
            affidabilità, 40 = soglia dei problemi); il consenso è la media pesata dei soli metodi disponibili. Il voto A–D resta
            quello di BioLoom.
          </p>
        </div>
        <div className="stack-sm">
          <div className="row">
            <h4>Metodi ({c.methods.length})</h4>
            {c.methods.length > 0 && (
              <ExpandAll masculine noun="metodi" className="right" allOpen={allOpen} onExpand={() => open.expandAll(c.methods.map((m) => m.key))} onCollapse={open.collapseAll} />
            )}
          </div>
          <div className="table-wrap">
            <table className="table">
              <thead>
                <tr>
                  <th style={{ width: 34 }} aria-label="Dettagli" />
                  <th>Metodo</th><th className="num">Valore</th><th>Normalizzato</th><th className="num">Peso</th><th className="num">Contributo</th><th>Fascia</th>
                </tr>
              </thead>
              <tbody>
                {c.methods.map((m) => {
                  const isOpen = open.has(m.key);
                  return (
                    <Fragment key={m.key}>
                      <tr className={`clickable${isOpen ? " row-open" : ""}`} onClick={() => open.toggle(m.key)}>
                        <td><ExpandButton open={isOpen} onClick={() => open.toggle(m.key)} label={`dettagli di ${m.label}`} /></td>
                        <td><b>{m.label}</b><div className="tiny text-2">{m.measures}</div></td>
                        <td className="num nowrap">{fmtRaw(m)}</td>
                        <td><BarCell value={m.normalized} label={fmtNum(m.normalized, 0)} /></td>
                        <td className="num">{fmtNum(m.weight, 2)}</td>
                        <td className="num" title="Punti portati al consenso: normalizzato × peso / somma dei pesi">{fmtNum(methodContribution(c, m), 1)}</td>
                        <td><Badge tone={BAND_TONE[m.band]}>{m.band}</Badge></td>
                      </tr>
                      {isOpen && (
                        <tr className="row-detail">
                          <td colSpan={COLUMNS}>
                            <DetailGrid items={[
                              { label: "Misura", value: m.measures },
                              { label: "Valore grezzo", value: fmtRaw(m) },
                              { label: "Scala (grezzo → punteggio)", value: `${scaleText(m)}, lineare tra i punti` },
                              { label: "Peso", value: `${fmtNum(m.weight, 2)} su ${fmtNum(weights, 2)} (${fmtNum(weights ? (100 * m.weight) / weights : 0, 0)}%)` },
                              { label: "Contributo al consenso", value: `${fmtNum(methodContribution(c, m), 1)} punti su ${fmtNum(c.score, 1)}` },
                              { label: "Fonte", value: m.source },
                            ]} />
                          </td>
                        </tr>
                      )}
                    </Fragment>
                  );
                })}
              </tbody>
            </table>
          </div>
          {c.missing.length > 0 && (
            <Disclosure title="Metodi non disponibili" extra={c.missing.map((m) => m.label).join(", ")}>
              <ul className="quality-list">
                {c.missing.map((m) => <li key={m.key}><b>{m.label}</b>: {m.reason}.</li>)}
              </ul>
            </Disclosure>
          )}
          <Disclosure title="Scale e fonti">
            <ul className="quality-list">
              {c.methods.map((m) => (
                <li key={m.key}><b>{m.label}</b>: {scaleText(m)} (lineare tra i punti). <span className="text-2">{m.source}.</span></li>
              ))}
            </ul>
          </Disclosure>
          {c.per_residue.signals.length > 0 && (
            <Disclosure title="Segnali per residuo" extra={`${c.per_residue.signals.length} metodi`}>
              <ul className="quality-list">
                {c.per_residue.signals.map((s) => (
                  <li key={s.key}><b>{s.label}</b>: {s.rule} <span className="text-2">({plural(s.flagged, "residuo segnalato", "residui segnalati")})</span></li>
                ))}
              </ul>
            </Disclosure>
          )}
        </div>
        <div className="stack-sm">
          <h4>Discordanze</h4>
          {c.discordances.length > 0 ? c.discordances.map((d, k) => <Alert key={k} tone="warn">{d.text}</Alert>) : c.methods_used > 1 ? (
            <Alert tone="ok">I metodi disponibili concordano: nessuno è in fascia buona mentre un altro è in fascia scarsa.</Alert>
          ) : <p className="small text-2">Serve più di un metodo per confrontarli.</p>}
        </div>
        <div className="stack-sm">
          <h4>Regioni problematiche ({c.regions.length})</h4>
          {c.regions.length ? <RegionTable regions={c.regions} onRegion={onRegion} /> : (
            <p className="small text-2">Nessun tratto di almeno 3 residui segnalato da almeno 2 metodi.</p>
          )}
          {/* disordine predetto dalla sequenza (F5.5): spiega le regioni, non entra nel punteggio */}
          {!!c.disorder?.regions.length && <Alert tone="info">{c.disorder.text}</Alert>}
          {/* accordo con una predizione esterna (F9.3), solo con il confronto eseguito */}
          {predictor && <Alert tone={predictor.tone}>{predictor.text}</Alert>}
          <ConsensusTracks residues={report.residues} consensus={c} selected={selected} onSelect={onSelect} onRegion={onRegion} />
        </div>
        {c.warnings.map((w, k) => <p key={k} className="small text-2">{w}</p>)}
      </div>
    </Card>
  );
}

/** Regioni segnalate da più metodi: tratto, residui, metodi e note; il clic le evidenzia nel 3D. */
function RegionTable({ regions, onRegion }: { regions: ConsensusRegion[]; onRegion: (r: ConsensusRegion) => void }) {
  return (
    <div className="table-wrap">
      <table className="table">
        <thead><tr><th>Tratto</th><th className="num">Residui</th><th>Metodi</th><th>Note</th><th aria-label="Azioni" /></tr></thead>
        <tbody>
          {regions.map((r) => (
            <tr key={`${r.chain}${r.start}`} className="clickable" title={r.text} onClick={() => onRegion(r)}>
              <td className="nowrap"><b>{r.start === r.end ? r.start : `${r.start}–${r.end}`}</b> <span className="text-2">catena {r.chain}</span></td>
              <td className="num nowrap">{r.length} <span className="text-2">({r.flagged} segnalati)</span></td>
              <td><span className="row" style={{ gap: 4 }}>{r.methods.map((k) => <Badge key={k} tone="warn">{SIGNAL_LABEL[k] ?? k}</Badge>)}</span></td>
              <td className="small">
                {r.expected && <Badge tone="info" title="Almeno metà dei residui in una regione disordinata predetta">disordine predetto</Badge>}
                {!!r.predictor_overlap && <Badge tone="err" title="Residui in cui il modello diverge anche da una predizione sicura">diverge dalla predizione ({r.predictor_overlap})</Badge>}
                {!r.expected && !r.predictor_overlap && "—"}
              </td>
              <td>
                <button type="button" className="btn btn-sm btn-ghost" onClick={(e) => { e.stopPropagation(); onRegion(r); }}>
                  <Icon name="cube" size={13} /> Mostra nel 3D
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
