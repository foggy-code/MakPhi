import { Fragment, type ReactNode } from "react";
import type { ChartData } from "../../lib/exportImage";
import { fmtNum } from "../../lib/format";
import { ChartFrame } from "../ChartFrame";
import { BarCell } from "../ui";

// ---------------------------------------------------------------------------------- barre composizione
export interface StackPart {
  label: string;
  /** percentuale */
  value: number;
  color: string;
}

function Bar({ parts, height }: { parts: StackPart[]; height: number }) {
  const total = parts.reduce((s, p) => s + p.value, 0) || 1;
  return (
    <div style={{ display: "flex", height, borderRadius: height / 2, overflow: "hidden", background: "var(--panel-3)" }}>
      {parts.map((p) => (
        <div key={p.label} title={`${p.label}: ${fmtNum(p.value, 1)}%`} style={{ width: `${(p.value / total) * 100}%`, background: p.color }} />
      ))}
    </div>
  );
}

function Legend({ parts }: { parts: StackPart[] }) {
  return (
    <div className="legend">
      {parts.map((p) => (
        <span key={p.label}><i style={{ background: p.color }} />{p.label} {fmtNum(p.value, 1)}%</span>
      ))}
    </div>
  );
}

/**
 * Composizione in percentuale (struttura secondaria, classi di residui). Senza `title` è la barra semplice con la
 * legenda; con `title` diventa un grafico con PNG, CSV ed "Espandi" (nella vista espansa una barra per categoria).
 * `titleHidden` e `className` vanno alla cornice (ChartFrame).
 */
export function StackBar({ parts, title, subtitle, filename, titleHidden, className }: {
  parts: StackPart[];
  title?: string;
  subtitle?: ReactNode;
  filename?: string;
  titleHidden?: boolean;
  className?: string;
}) {
  if (!title) {
    return (
      <div className="stack-sm">
        <Bar parts={parts} height={12} />
        <Legend parts={parts} />
      </div>
    );
  }
  const data = (): ChartData => ({
    columns: [{ key: "categoria", label: "Categoria" }, { key: "percentuale", label: "Percentuale", num: true }],
    rows: parts.map((p) => ({ categoria: p.label, percentuale: p.value })),
  });
  return (
    <ChartFrame title={title} subtitle={subtitle} filename={filename ?? title} data={data} legend={<Legend parts={parts} />} titleHidden={titleHidden} className={className}>
      {({ expanded }) => expanded ? (
        <div className="stack">
          <Bar parts={parts} height={28} />
          <div className="stackbar-rows">
            {parts.map((p) => (
              <Fragment key={p.label}>
                <span>{p.label}</span>
                <BarCell value={p.value} tone={p.color} label={`${fmtNum(p.value, 1)}%`} />
              </Fragment>
            ))}
          </div>
        </div>
      ) : <Bar parts={parts} height={14} />}
    </ChartFrame>
  );
}
