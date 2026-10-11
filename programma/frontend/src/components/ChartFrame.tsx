import { useEffect, useId, useMemo, useRef, useState, type ReactNode } from "react";
import { downloadCsv, downloadPng, type ChartData, type DataValue } from "../lib/exportImage";
import { fmtNum } from "../lib/format";
import { TABLE_ROW_HEIGHT, useVirtualRows } from "../lib/virtual";
import type { DomainState } from "../lib/zoom";
import { Icon } from "./Icon";
import { Modal, rowActivate, Segmented } from "./ui";

/**
 * Cornice comune dei grafici: titolo, comandi, "Espandi" (finestra a tutto schermo con il grafico più grande e la
 * tabella dei dati), "PNG" (immagine del grafico con titolo e legenda) e "CSV" (dati del grafico).
 *
 * Il grafico si passa come funzione di `{ expanded }`, così la vista espansa può disegnarlo più grande o con più
 * dettagli; lo stato (zoom, selezione, classe) resta nel componente del grafico ed è condiviso tra le due viste.
 * Mentre la vista espansa è aperta il grafico della pagina non si disegna (resta lo spazio): un solo SVG per volta.
 * Pulsanti e selettori dentro il grafico vanno marcati `data-export-ignore` per restare fuori dal PNG.
 * La cornice è un gruppo col nome del titolo: i pulsanti PNG, CSV ed Espandi lo ripetono nel nome accessibile.
 */
export interface ChartRenderContext {
  expanded: boolean;
}

export interface ChartFrameProps {
  title: ReactNode;
  /** testo per i nomi dei file (PNG, CSV) */
  filename: string;
  subtitle?: ReactNode;
  /** comandi del grafico (selettori, zoom): nell'intestazione, fuori dal PNG; anche nella vista espansa */
  controls?: ReactNode | ((ctx: ChartRenderContext) => ReactNode);
  /** legenda sotto il grafico, inclusa nel PNG */
  legend?: ReactNode | ((ctx: ChartRenderContext) => ReactNode);
  /**
   * dati per la scheda "Dati" e il CSV; una funzione si calcola solo quando servono (una volta per apertura della
   * scheda "Dati", e a ogni clic su "CSV")
   */
  data?: ChartData | (() => ChartData);
  /** clic (o Invio) su una riga della tabella dei dati (es. seleziona il residuo) */
  onRowClick?: (row: Record<string, DataValue>, index: number) => void;
  /** indice in `data.rows` dell'elemento selezionato: riga evidenziata e aria-selected nella scheda "Dati" */
  selectedIndex?: number;
  /** nota sotto l'intestazione (es. "Trascina per ingrandire"); anche funzione di { expanded } */
  hint?: ReactNode | ((ctx: ChartRenderContext) => ReactNode);
  /** senza "Espandi" (grafici già grandi) */
  expandable?: boolean;
  /**
   * titolo non visibile nella pagina (resta per i lettori di schermo, nella didascalia del PNG e nella vista espansa):
   * per un grafico che è il contenuto principale di una Card che ha già lo stesso titolo
   */
  titleHidden?: boolean;
  /** classi in più; `flat`: fondo trasparente (grafico dentro un pannello già colorato, es. una Disclosure) */
  className?: string;
  children: ReactNode | ((ctx: ChartRenderContext) => ReactNode);
}

function call<T>(v: T | ((ctx: ChartRenderContext) => T), ctx: ChartRenderContext): T {
  return typeof v === "function" ? (v as (ctx: ChartRenderContext) => T)(ctx) : v;
}

/** Pulsante "PNG" con attesa; un errore si legge accanto al pulsante (ed è annunciato), il dettaglio nel title. */
function PngButton({ target, name, chart }: { target: () => HTMLElement | null; name: string; chart: string }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  return (
    <>
      <button
        type="button"
        className="btn btn-sm btn-ghost"
        disabled={busy}
        aria-label={`Scarica PNG: ${chart}`}
        title={error ? `Esportazione non riuscita: ${error}` : "Scarica il grafico come immagine PNG"}
        onClick={async () => {
          const node = target();
          if (!node) return;
          setBusy(true);
          setError(null);
          try {
            await downloadPng(node, name);
          } catch (e) {
            setError(e instanceof Error ? e.message : String(e));
          } finally {
            setBusy(false);
          }
        }}
      >
        {busy ? <span className="spinner" style={{ width: 12, height: 12 }} /> : <Icon name={error ? "alert" : "image"} size={14} />} PNG
      </button>
      {error && <span className="chart-frame-error" role="status">PNG non riuscito</span>}
    </>
  );
}

export function ChartFrame({ title, filename, subtitle, controls, legend, data, onRowClick, selectedIndex, hint, expandable = true, titleHidden = false, className = "", children }: ChartFrameProps) {
  const titleId = useId();
  const capture = useRef<HTMLDivElement>(null);
  const bigCapture = useRef<HTMLDivElement>(null);
  const [expanded, setExpanded] = useState(false);
  const [view, setView] = useState<"chart" | "data">("chart");
  // altezza del grafico della pagina all'apertura della vista espansa: lo spazio resta, la pagina non salta
  const [placeholder, setPlaceholder] = useState(0);
  const inline: ChartRenderContext = { expanded: false };
  const big: ChartRenderContext = { expanded: true };
  const getData = () => (typeof data === "function" ? data() : data ?? null);
  // la scheda "Dati" lavora su una copia presa all'apertura: un clic su una riga (che ridisegna il genitore) non
  // ricostruisce né riordina tutto; dati passati come oggetto si seguono invece a ogni cambio
  const showTable = expanded && view === "data";
  // eslint-disable-next-line react-hooks/exhaustive-deps -- copia dei dati presa all'apertura della scheda (vedi sopra)
  const tableData = useMemo(() => (showTable ? getData() : null), [showTable, typeof data === "function" ? null : data]);
  const chartName = typeof title === "string" ? title : filename;
  const caption = (
    <div className="chart-caption" aria-hidden="true">
      <b>{title}</b>
      {subtitle && <span>{subtitle}</span>}
    </div>
  );
  // comandi del grafico (vanno a capo come gruppo) e azioni sempre insieme a destra: lo zoom non sposta PNG/CSV/Espandi
  const ctrls = (ctx: ChartRenderContext) => controls && <div className="chart-frame-controls" data-export-ignore="">{call(controls, ctx)}</div>;
  const actions = (ctx: ChartRenderContext) => (
    <div className="chart-frame-actions" data-export-ignore="">
      <PngButton target={() => (ctx.expanded ? bigCapture.current : capture.current)} name={filename} chart={chartName} />
      {data && (
        <button type="button" className="btn btn-sm btn-ghost" aria-label={`Scarica CSV: ${chartName}`} title="Scarica i dati del grafico (CSV)"
          onClick={() => { const d = getData(); if (d) downloadCsv(d, filename); }}>
          <Icon name="download" size={14} /> CSV
        </button>
      )}
      {expandable && !ctx.expanded && (
        <button type="button" className="btn btn-sm" aria-haspopup="dialog" aria-label={`Espandi: ${chartName}`} title="Espandi il grafico per analizzarlo nel dettaglio"
          onClick={() => {
            setPlaceholder(capture.current?.offsetHeight ?? 0);
            setView("chart");
            setExpanded(true);
          }}>
          <Icon name="maximize" size={14} /> Espandi
        </button>
      )}
    </div>
  );
  return (
    <div className={`chart-frame${className ? ` ${className}` : ""}`} role="group" aria-labelledby={titleId}>
      <div className="chart-frame-head">
        {titleHidden && <span id={titleId} className="sr-only">{title}</span>}
        {(!titleHidden || subtitle) && (
          <div className="chart-frame-heading">
            {!titleHidden && <span id={titleId} className="chart-frame-title">{title}</span>}
            {subtitle && <span className="chart-frame-sub">{subtitle}</span>}
          </div>
        )}
        {ctrls(inline)}
        {actions(inline)}
      </div>
      {hint && <div className="zoom-hint" data-export-ignore="">{call(hint, inline)}</div>}
      {expanded ? (
        <div className="chart-frame-capture" style={{ height: placeholder }} aria-hidden="true" />
      ) : (
        <div className="chart-frame-capture" ref={capture}>
          {caption}
          {call(children, inline)}
          {legend && call(legend, inline)}
        </div>
      )}
      {expanded && (
        <Modal
          size="full"
          title={title}
          onClose={() => setExpanded(false)}
          tools={data && (
            <Segmented label="Contenuto della finestra" value={view} onChange={setView} options={[{ value: "chart", label: "Grafico" }, { value: "data", label: "Dati" }]} />
          )}
        >
          <div className="chart-expanded">
            {subtitle && <div className="small text-2">{subtitle}</div>}
            {view === "chart" ? (
              <>
                <div className="chart-frame-head chart-frame-head-end">
                  {ctrls(big)}
                  {actions(big)}
                </div>
                {hint && <div className="zoom-hint" data-export-ignore="">{call(hint, big)}</div>}
                <div className="chart-frame-capture" ref={bigCapture}>
                  {caption}
                  {call(children, big)}
                  {legend && call(legend, big)}
                </div>
              </>
            ) : (
              <DataTable data={tableData ?? { columns: [], rows: [] }} filename={filename} onRowClick={onRowClick} selectedIndex={selectedIndex} />
            )}
          </div>
        </Modal>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------------- zoom

/**
 * Comandi di zoom di un asse: riduci, ingrandisci, sposta, tutto; con l'intervallo visibile. Fuori dal PNG.
 * `format` scrive l'intervallo (es. residui "12–48" o angoli "−90°…0°"). I pulsanti ci sono sempre (disattivati
 * quando non servono) e l'intervallo ha una larghezza minima: ingrandire non sposta i comandi sotto il puntatore.
 */
export function ZoomBar({ zoom, format, panning = true, label }: { zoom: DomainState; format: (lo: number, hi: number) => string; panning?: boolean; label?: string }) {
  return (
    <span className="zoom-bar" data-export-ignore="" role="group" aria-label={label ? `Zoom ${label}` : "Zoom"}>
      {label && <span className="tiny muted" aria-hidden="true">{label}</span>}
      {panning && (
        <button type="button" className="btn btn-sm btn-ghost btn-icon" title="Sposta indietro" aria-label="Sposta indietro" disabled={zoom.full} onClick={() => zoom.pan(-0.5)}>
          <span style={{ transform: "rotate(180deg)", display: "inline-grid" }}><Icon name="chevron" size={14} /></span>
        </button>
      )}
      <button type="button" className="btn btn-sm btn-ghost btn-icon" title="Riduci lo zoom" aria-label="Riduci lo zoom" disabled={zoom.full} onClick={() => zoom.zoomOut()}>
        <Icon name="zoomOut" size={14} />
      </button>
      <span className="range">{format(zoom.domain.lo, zoom.domain.hi)}</span>
      <button type="button" className="btn btn-sm btn-ghost btn-icon" title="Ingrandisci" aria-label="Ingrandisci" onClick={() => zoom.zoomIn()}>
        <Icon name="zoomIn" size={14} />
      </button>
      {panning && (
        <button type="button" className="btn btn-sm btn-ghost btn-icon" title="Sposta avanti" aria-label="Sposta avanti" disabled={zoom.full} onClick={() => zoom.pan(0.5)}>
          <Icon name="chevron" size={14} />
        </button>
      )}
      <button type="button" className="btn btn-sm btn-ghost" title="Mostra tutto" disabled={zoom.full} onClick={zoom.reset}>
        <Icon name="refresh" size={13} /> Tutto
      </button>
    </span>
  );
}

// ---------------------------------------------------------------------------------- tabella dei dati

function cellText(v: DataValue, num: boolean): string {
  if (v === null || v === undefined || v === "") return "—";
  if (typeof v === "number") {
    if (!num) return String(v);
    if (Number.isInteger(v)) return fmtNum(v, 0);
    return fmtNum(v, Math.abs(v) >= 100 ? 1 : 3);
  }
  return v;
}

const isEmpty = (v: DataValue) => v === null || v === undefined || v === "";

/** Confronto di due celle piene (le vuote le mette in fondo chi ordina, in entrambe le direzioni). */
function compare(a: DataValue, b: DataValue, num: boolean): number {
  if (num) return Number(a) - Number(b);
  return String(a).localeCompare(String(b), "it", { numeric: true });
}

/** Righe filtrate (testo mostrato o valore grezzo: "0,87" e "0.87" trovano 0,873) e ordinate; vuote sempre in fondo. */
export function tableRows(data: ChartData, query: string, sort: { key: string; dir: 1 | -1 } | null) {
  const q = query.trim().toLowerCase();
  let out = data.rows.map((r, i) => ({ r, i }));
  if (q) {
    out = out.filter(({ r }) => data.columns.some((c) => {
      const v = r[c.key];
      return cellText(v, !!c.num).toLowerCase().includes(q) || String(v ?? "").toLowerCase().includes(q);
    }));
  }
  if (sort) {
    const num = !!data.columns.find((c) => c.key === sort.key)?.num;
    out = [...out].sort((a, b) => {
      const va = a.r[sort.key];
      const vb = b.r[sort.key];
      const ea = isEmpty(va);
      const eb = isEmpty(vb);
      if (ea !== eb) return ea ? 1 : -1;
      return (ea ? 0 : compare(va, vb, num) * sort.dir) || a.i - b.i;
    });
  }
  return out;
}

/**
 * Tabella dei dati di un grafico: filtro testuale, ordinamento per colonna (pulsanti nelle intestazioni), righe a
 * finestra oltre 200. Con `onRowClick` le righe si attivano anche da tastiera (Tab, Invio o Spazio).
 */
export function DataTable({ data, filename, onRowClick, selectedIndex, maxHeight = "calc(100vh - 260px)" }: {
  data: ChartData;
  filename?: string;
  onRowClick?: (row: Record<string, DataValue>, index: number) => void;
  /** indice in `data.rows` della riga selezionata */
  selectedIndex?: number;
  maxHeight?: number | string;
}) {
  const [query, setQuery] = useState("");
  const [sort, setSort] = useState<{ key: string; dir: 1 | -1 } | null>(null);
  const rows = useMemo(() => tableRows(data, query, sort), [data, query, sort]);
  const virtual = useVirtualRows<HTMLDivElement>({ count: rows.length, rowHeight: TABLE_ROW_HEIGHT, measure: true });
  const rowStyle = virtual.active ? { height: TABLE_ROW_HEIGHT } : undefined;
  const { reveal } = virtual;
  useEffect(() => reveal(0), [query, sort, reveal]);
  const span = Math.max(1, data.columns.length);
  return (
    <div className="stack-sm">
      <div className="data-table-tools">
        <input className="input" placeholder="Filtra le righe…" value={query} onChange={(e) => setQuery(e.target.value)} aria-label="Filtra le righe" />
        <span className="small muted">{rows.length} di {data.rows.length} righe{onRowClick ? " · clic o Invio su una riga per selezionarla" : ""}</span>
        {filename && (
          <button type="button" className="btn btn-sm right" aria-label={`Scarica CSV: ${filename}`} title="Scarica i dati del grafico (CSV)" onClick={() => downloadCsv(data, filename)}>
            <Icon name="download" size={14} /> CSV
          </button>
        )}
      </div>
      <div ref={virtual.ref} onScroll={virtual.onScroll} className="table-wrap" style={{ maxHeight, overflowY: "auto" }}>
        <table className="table">
          <thead>
            <tr>
              {data.columns.map((c) => (
                <th key={c.key} className={`sortable${c.num ? " num" : ""}`}
                  aria-sort={sort?.key === c.key ? (sort.dir === 1 ? "ascending" : "descending") : undefined}>
                  <button type="button" className="th-sort" title="Ordina per questa colonna"
                    onClick={() => setSort((s) => (s?.key === c.key ? (s.dir === 1 ? { key: c.key, dir: -1 } : null) : { key: c.key, dir: 1 }))}>
                    {c.label}
                    {sort?.key === c.key && <span aria-hidden="true">{sort.dir === 1 ? "▲" : "▼"}</span>}
                  </button>
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {virtual.range.padTop > 0 && <tr className="virtual-spacer" aria-hidden="true" style={{ height: virtual.range.padTop }}><td colSpan={span} /></tr>}
            {rows.slice(virtual.range.start, virtual.range.end).map(({ r, i }, n) => (
              <tr key={i} data-vrow={virtual.range.start + n} style={rowStyle}
                className={`${onRowClick ? "clickable" : ""}${selectedIndex === i ? " selected" : ""}${virtual.active ? " virtual-row" : ""}`}
                aria-selected={onRowClick ? selectedIndex === i : undefined}
                {...(onRowClick ? rowActivate(() => onRowClick(r, i)) : {})}>
                {data.columns.map((c) => <td key={c.key} className={c.num ? "num" : undefined}>{cellText(r[c.key], !!c.num)}</td>)}
              </tr>
            ))}
            {virtual.range.padBottom > 0 && <tr className="virtual-spacer" aria-hidden="true" style={{ height: virtual.range.padBottom }}><td colSpan={span} /></tr>}
            {!rows.length && <tr><td colSpan={span} className="muted">Nessuna riga.</td></tr>}
          </tbody>
        </table>
      </div>
    </div>
  );
}
