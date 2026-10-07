// Pipeline M × N: matrice sequenze × template colorata per una metrica (zDOPE, DOPE, GA341, molpdf, identità,
// copertura, voto), con il migliore di ogni sequenza segnato; clic su una cella completata per il modello nel 3D.
// La logica (valori, colori, riepilogo, pagine) sta in lib/pipelineMatrix.ts.
import { useEffect, useMemo, useRef, useState } from "react";
import { api } from "../api/client";
import type { JobItemsEvent, PipelineMatrix as Matrix, PipelineMatrixCell } from "../api/types";
import { textOn } from "./charts";
import { fmtNum } from "../lib/format";
import { useAsync, useInterval, useJobsFeed, useThrottled } from "../lib/hooks";
import { availableMetrics, BAD, cellColor, cellStatusText, cellValue, GOOD, MATRIX_METRICS, matrixPage, matrixSummary, MID, type MatrixMetric } from "../lib/pipelineMatrix";
import { plural } from "../lib/shellView";

const ROWS_PER_PAGE = 50;
// Oltre queste sequenze la matrice (decine di migliaia di celle) si rilegge al più una volta al minuto mentre la pipeline
// gira: costruirla a ogni elemento concluso toglierebbe tempo al calcolo
const LIVE_SEQUENCES = 2000;
const BIG_REFRESH_MS = 60_000;

export function PipelineMatrix({ jobId, running, onOpen }: {
  jobId: string;
  running: boolean;
  /** coppia completata scelta: modello da aprire nel 3D */
  onOpen: (cell: PipelineMatrixCell, sequence: string, template: string) => void;
}) {
  const { data, reload } = useAsync<Matrix>(() => api.pipelineMatrix(jobId), [jobId]);
  const big = (data?.sequences.length ?? 0) > LIVE_SEQUENCES;
  const refresh = useThrottled(reload, big ? BIG_REFRESH_MS : 1500);
  const live = useJobsFeed((type, event) => {
    if (type === "items" ? (event as JobItemsEvent | null)?.job_id === jobId : type === "ready" || type === "resync") refresh();
  });
  useInterval(reload, running && !live ? (big ? BIG_REFRESH_MS : 3000) : null);
  const wasRunning = useRef(running);
  useEffect(() => {
    if (wasRunning.current !== running) reload();
    wasRunning.current = running;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [running]);
  const [metric, setMetric] = useState<MatrixMetric>("zdope");
  const [page, setPage] = useState(1);
  const metrics = useMemo(() => (data ? availableMetrics(data) : []), [data]);
  const summary = useMemo(() => (data ? matrixSummary(data) : null), [data]);
  if (!data || !summary) return null;
  const shown = metrics.includes(metric) ? metric : (metrics[0] ?? "zdope");
  const def = MATRIX_METRICS[shown];
  const rows = data.sequences.map((name, s) => ({ name, s, cells: data.cells[s] ?? [] }));
  const view = matrixPage(rows, page, ROWS_PER_PAGE);
  return (
    <div className="stack-sm">
      <div className="row small" style={{ gap: 12, flexWrap: "wrap" }}>
        <label className="check">
          Colora per
          <select className="input input-sm" style={{ width: 150 }} value={shown} onChange={(e) => setMetric(e.target.value as MatrixMetric)} aria-label="Metrica della matrice">
            {metrics.map((m) => <option key={m} value={m}>{MATRIX_METRICS[m].label}</option>)}
          </select>
        </label>
        <span className="text-2">{def.note}.</span>
        {big && running && <span className="text-2">Matrice grande: si aggiorna una volta al minuto.</span>}
      </div>
      <div className="legend">
        <span><i style={{ background: GOOD }} />{def.perRow ? "migliore della sequenza" : "buono"}</span>
        <span><i style={{ background: MID }} />intermedio</span>
        <span><i style={{ background: BAD }} />{def.perRow ? "peggiore della sequenza" : "scarso"}</span>
        <span><b>★</b> migliore della sequenza (zDOPE più basso)</span>
      </div>
      <div className="table-wrap">
        <table className="table pipeline-matrix">
          <thead>
            <tr>
              <th scope="col">Sequenza</th>
              {data.templates.map((t, k) => (
                <th key={t} scope="col" className="num" title={`${data.template_ids[k] ?? t} · migliore per ${plural(summary.wins[k], "sequenza", "sequenze")}`}>
                  {t}<div className="shell-sub">★ {summary.wins[k]}</div>
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {view.rows.map(({ name, cells }) => (
              <tr key={name}>
                <th scope="row" className="nowrap">{name}</th>
                {data.templates.map((t, k) => {
                  const c = cells[k] ?? null;
                  const value = cellValue(c, shown);
                  const fill = cellColor(cells, c, shown);
                  const open = c?.status === "completed" && !!c.best_model;
                  const tip = [`${name} × ${t}`, c?.error ?? "", ...(c ? (["zdope", "dope", "ga341", "identity", "coverage"] as MatrixMetric[])
                    .map((m) => `${MATRIX_METRICS[m].label} ${fmtNum(cellValue(c, m), MATRIX_METRICS[m].digits)}`) : [])].filter(Boolean).join(" · ");
                  return (
                    <td key={t} className={`num${open ? " clickable" : ""}`} title={tip}
                      style={fill ? { background: fill, color: textOn(fill), fontWeight: c?.best ? 700 : undefined } : undefined}
                      onClick={open && c ? () => onOpen(c, name, t) : undefined}
                      onKeyDown={open && c ? (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); onOpen(c, name, t); } } : undefined}
                      tabIndex={open ? 0 : undefined} role={open ? "button" : undefined}
                      aria-label={open ? `Apri il modello di ${name} su ${t}` : undefined}>
                      {value != null ? fmtNum(value, def.digits) : <span className="text-2">{cellStatusText(c)}</span>}
                      {c?.best && " ★"}
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="row small">
        <span className="text-2">
          {plural(summary.completed, "coppia completata", "coppie completate")} su {summary.pairs}
          {summary.failed ? ` · ${plural(summary.failed, "fallita", "fallite")}` : ""}
          {summary.open ? ` · ${summary.open} in attesa o in corso` : ""}
          {` · migliore scelto per ${summary.closed} di ${plural(data.sequences.length, "sequenza", "sequenze")}`}
        </span>
        {view.pages > 1 && (
          <div className="right row">
            <span className="text-2">sequenze {view.start + 1}–{view.start + view.rows.length} · pagina {view.page} di {view.pages}</span>
            <button type="button" className="btn btn-sm" disabled={view.page <= 1} aria-label="Sequenze precedenti" onClick={() => setPage(view.page - 1)}>‹</button>
            <button type="button" className="btn btn-sm" disabled={view.page >= view.pages} aria-label="Sequenze successive" onClick={() => setPage(view.page + 1)}>›</button>
          </div>
        )}
      </div>
    </div>
  );
}
