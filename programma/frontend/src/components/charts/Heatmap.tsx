import { useMemo, useState, type MouseEvent as RMouseEvent } from "react";
import { colorFor } from "../../lib/colorscale";
import type { ChartData } from "../../lib/exportImage";
import { fmtNum } from "../../lib/format";
import { svgPoint } from "../../lib/zoom";
import { ChartFrame } from "../ChartFrame";
import { useChartWidth, useTooltip } from "./common";
import { IDENTITY_SCALE, textOn } from "./linearHelpers";
import { ScaleLegend } from "./ScaleLegend";

// ---------------------------------------------------------------------------------- heatmap identità
type Cell = { fill: string; ink: string };

/**
 * Matrice di identità tra coppie di sequenze, larga quanto lo spazio disponibile (anche nel PNG); i valori sono scritti
 * nelle celle abbastanza grandi, cioè quasi sempre nella vista espansa.
 */
export function Heatmap({ ids, values, title = "Identità di sequenza a coppie", filename = "identita" }: {
  ids: string[];
  values: number[][];
  title?: string;
  filename?: string;
}) {
  // colore della cella e del suo testo (nero o bianco, il più leggibile): uguali nei due temi e nel PNG
  const cells = useMemo(() => values.map((row) => row.map((v): Cell => {
    const fill = colorFor(IDENTITY_SCALE, v);
    return { fill, ink: textOn(fill) };
  })), [values]);
  const data = (): ChartData => ({
    columns: [{ key: "id", label: "ID" }, ...ids.map((id, j) => ({ key: `c${j}`, label: id, num: true }))],
    rows: values.map((row, i) => ({ id: ids[i], ...Object.fromEntries(row.map((v, j) => [`c${j}`, v])) })),
  });
  return (
    <ChartFrame title={title} filename={filename} data={data} legend={<ScaleLegend scale={IDENTITY_SCALE} title="Identità" />}>
      {({ expanded }) => <HeatSvg ids={ids} values={values} cells={cells} expanded={expanded} />}
    </ChartFrame>
  );
}

/** Un calo della larghezza fino a tanti pixel non cambia le celle (vedi useStableWidth). */
const SLACK = 24;

/**
 * Larghezza per il calcolo delle celle, con isteresi: segue ogni aumento, un calo solo oltre `SLACK` px (intanto l'SVG
 * si adatta in scala). La matrice è l'unico grafico alto quanto è largo: altrimenti la barra di scorrimento che compare
 * (larghezza −10 px → celle e altezza minori → la barra sparisce → larghezza piena…) lo ridisegnerebbe all'infinito.
 */
function useStableWidth(width: number) {
  const [stable, setStable] = useState(width);
  const next = width > stable || width < stable - SLACK ? width : stable;
  if (next !== stable) setStable(next);
  return next;
}

/** larghezza stimata di un carattere delle etichette (11 px, per eccesso) e inclinazione (gradi) delle etichette delle colonne */
const CHAR_W = 7;
const TILT = 50;
const RAD = (TILT * Math.PI) / 180;

function HeatSvg({ ids, values, cells, expanded }: { ids: string[]; values: number[][]; cells: Cell[][]; expanded: boolean }) {
  const tip = useTooltip();
  const [hover, setHover] = useState<[number, number] | null>(null);
  const { ref, width: measured } = useChartWidth(900);
  const width = useStableWidth(measured);
  const n = ids.length;
  const chars = expanded ? 24 : 16;
  // spazio delle etichette dal nome più lungo: righe a sinistra, colonne inclinate sopra e oltre l'ultima colonna
  const text = Math.min(chars, ids.reduce((m, id) => Math.max(m, id.length), 1)) * CHAR_W;
  const left = Math.ceil(text) + 10;
  const top = Math.ceil(text * Math.sin(RAD)) + 14;
  const reach = Math.ceil(text * Math.cos(RAD));
  // la matrice sta nella larghezza disponibile, così il PNG la contiene tutta; sotto la cella minima l'SVG si rimpicciolisce
  const cell = Math.max(8, Math.min(expanded ? 64 : 34, Math.floor((width - left - reach - 4) / Math.max(1, n))));
  const W = left + cell * n + Math.max(4, Math.ceil(reach - cell / 2) + 4);
  const H = top + cell * n + 4;
  const showValues = cell >= 22;
  const fontSize = expanded ? Math.min(13, Math.max(9.5, cell * 0.34)) : 9.5;
  const atX = (k: number) => left + k * cell;
  const atY = (k: number) => top + k * cell;
  // riga e colonna della cella sotto il puntatore in grassetto
  const rowWeight = (i: number) => (hover?.[0] === i ? 650 : undefined);
  const colWeight = (j: number) => (hover?.[1] === j ? 650 : undefined);
  // celle e valori (n², anche migliaia) disegnati una volta per dimensione: il mouse non li ridisegna
  const grid = useMemo(() => (
    <>
      {values.map((row, i) => row.map((_, j) => (
        <rect key={`${i}-${j}`} x={left + j * cell} y={top + i * cell} width={cell - 1} height={cell - 1} rx={2} fill={cells[i][j].fill} />
      )))}
      {showValues && values.map((row, i) => row.map((v, j) => (
        <text key={`t${i}-${j}`} x={left + j * cell + cell / 2 - 0.5} y={top + i * cell + cell / 2 + fontSize * 0.35} textAnchor="middle"
          style={{ fill: cells[i][j].ink, fontSize, pointerEvents: "none" }}>
          {fmtNum(v, 0)}
        </text>
      )))}
    </>
  ), [values, cells, cell, left, top, showValues, fontSize]);
  /** Cella sotto il puntatore, dalla posizione (un solo gestore sull'SVG invece di uno per cella). */
  const cellAt = (e: RMouseEvent<SVGSVGElement>): [number, number] | null => {
    const p = svgPoint(e.currentTarget, e.clientX, e.clientY);
    const i = Math.floor((p.y - top) / cell);
    const j = Math.floor((p.x - left) / cell);
    return i >= 0 && i < n && j >= 0 && j < n ? [i, j] : null;
  };
  return (
    <div ref={ref}>
      <svg viewBox={`0 0 ${W} ${H}`} className="chart" style={{ maxWidth: W, maxHeight: "none" }} role="img"
        aria-label={`Identità di sequenza tra ${n} sequenze`}
        onMouseMove={(e) => {
          const c = cellAt(e);
          if (!c) {
            setHover(null);
            return tip.hide();
          }
          // stessa cella: il suggerimento segue il puntatore senza ridisegnare
          if (hover && hover[0] === c[0] && hover[1] === c[1]) return tip.move(e);
          const [i, j] = c;
          setHover(c);
          tip.show(e, (
            <>
              <b>{ids[i]} ↔ {ids[j]}</b>
              <div>{fmtNum(values[i][j], 1)}% di identità</div>
            </>
          ));
        }}
        onMouseLeave={() => {
          setHover(null);
          tip.hide();
        }}>
        {ids.map((id, i) => (
          <g key={i}>
            <text x={left - 6} y={atY(i) + cell / 2 + 3.5} textAnchor="end" fontWeight={rowWeight(i)}>{id.slice(0, chars)}</text>
            <text x={atX(i) + cell / 2} y={top - 6} textAnchor="start" fontWeight={colWeight(i)} transform={`rotate(${-TILT} ${atX(i) + cell / 2} ${top - 6})`}>{id.slice(0, chars)}</text>
          </g>
        ))}
        {grid}
        {hover && <rect x={atX(hover[1]) - 1} y={atY(hover[0]) - 1} width={cell + 1} height={cell + 1} rx={3} fill="none" stroke="var(--text)" strokeWidth={2} pointerEvents="none" />}
      </svg>
      {tip.node}
    </div>
  );
}
