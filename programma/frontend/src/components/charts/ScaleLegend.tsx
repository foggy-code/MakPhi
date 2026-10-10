import { legendFor, NO_VALUE_COLOR, themedColor, type ColorScale } from "../../lib/colorscale";
import { useDocumentTheme } from "../../lib/theme";

/** Spostamento dell'etichetta di una tacca: agli estremi resta dentro la barra. */
const tickShift = (position: number) => (position <= 0.02 ? "none" : position >= 0.98 ? "translateX(-100%)" : "translateX(-50%)");

/**
 * Legenda di una scala di colori sotto un grafico (inclusa nel PNG): barra a gradiente con le tacche per le scale
 * continue, campioni per fasce e categorie (con un bordo sottile: i colori chiari restano visibili sul fondo bianco),
 * campioni aggiunti (`extra`) e "senza valore". Stessa lettura della legenda del visualizzatore 3D, con gli stessi colori
 * del tema (`themedColor`).
 */
export function ScaleLegend({ scale, title, noValue = false, extra }: {
  scale: ColorScale;
  title: string;
  noValue?: boolean;
  extra?: { color: string; label: string }[];
}) {
  const legend = legendFor(scale, title, noValue);
  const theme = useDocumentTheme();
  return (
    <div className="legend heat-legend">
      <span>{legend.title}</span>
      {legend.kind === "gradient" ? (
        <span className="heat-scale">
          <span className="heat-bar" style={{ background: legend.css }} />
          <span className="heat-ticks">
            {legend.ticks.map((t) => <span key={t.label} style={{ left: `${t.position * 100}%`, transform: tickShift(t.position) }}>{t.label}</span>)}
          </span>
        </span>
      ) : (
        legend.items.map((it) => <span key={it.label}><i className="edged" style={{ background: themedColor(it.color, theme) }} />{it.label}</span>)
      )}
      {extra?.map((it) => <span key={it.label}><i className="edged" style={{ background: themedColor(it.color, theme) }} />{it.label}</span>)}
      {legend.noValue && <span><i className="edged" style={{ background: NO_VALUE_COLOR }} />senza valore</span>}
    </div>
  );
}
