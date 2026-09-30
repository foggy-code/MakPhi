import { useEffect, useMemo, useRef, useState } from "react";
import { hexToInt, legendFor, NO_VALUE_COLOR } from "../lib/colorscale";
import { atomKey, coloringMap, describeResidue, hasMissing, type ResidueColoring, type ResidueRef } from "../lib/residueColoring";
import { Spinner } from "./ui";

type Scheme = "ss" | "chain" | "rainbow" | "bfactor";
const SCHEMES: { value: Scheme; label: string }[] = [
  { value: "ss", label: "Struttura sec." },
  { value: "chain", label: "Catene" },
  { value: "rainbow", label: "N→C" },
  { value: "bfactor", label: "B-factor" },
];

interface Props {
  pdb?: string | null;
  url?: string | null;
  height?: number;
  scheme?: Scheme;
  highlight?: { chain: string; resi: number }[];
  format?: "pdb" | "cif";
  /** colorazione per valore per residuo, con legenda: sostituisce gli schemi predefiniti (e i loro pulsanti) */
  coloring?: ResidueColoring | null;
  /** clic su un atomo: residuo scelto (con il passaggio del mouse compare l'etichetta del residuo) */
  onPickResidue?: (residue: ResidueRef) => void;
  /** quando cambia, la vista si centra sui residui evidenziati */
  centerKey?: number | string;
}

const NO_VALUE_INT = hexToInt(NO_VALUE_COLOR);

/** Colori per residuo e, per i residui non elencati, per catena (interi 0xRRGGBB). */
interface ViewerColors {
  residues: Map<string, number>;
  chains: Map<string, number>;
}

/* eslint-disable @typescript-eslint/no-explicit-any */
function applyStyle(viewer: any, scheme: Scheme, highlight: { chain: string; resi: number }[], colors: ViewerColors | null) {
  viewer.setStyle({}, {});
  let cartoon: any;
  if (colors) cartoon = { colorfunc: (atom: any) => colors.residues.get(atomKey(atom)) ?? colors.chains.get(atom.chain ?? "") ?? NO_VALUE_INT };
  else if (scheme === "ss") cartoon = { colorscheme: "ssPyMol", thickness: 0.4 };
  else if (scheme === "chain") cartoon = { colorscheme: "chain" };
  else if (scheme === "rainbow") cartoon = { color: "spectrum" };
  else {
    const atoms = viewer.selectedAtoms({});
    const bs = atoms.map((a: any) => a.b).filter((b: number) => typeof b === "number");
    const min = bs.length ? Math.min(...bs) : 0;
    const max = bs.length ? Math.max(...bs) : 100;
    cartoon = { colorscheme: { prop: "b", gradient: "rwb", min: max, max: min } };
  }
  viewer.setStyle({ hetflag: false }, { cartoon });
  viewer.setStyle({ hetflag: true, not: { resn: ["HOH", "WAT"] } }, { stick: { colorscheme: "greenCarbon", radius: 0.22 } });
  for (const h of highlight) {
    viewer.addStyle({ chain: h.chain, resi: h.resi }, { stick: { colorscheme: "orangeCarbon", radius: 0.25 } });
  }
  viewer.render();
}

function centerOn(viewer: any, highlight: { chain: string; resi: number }[]) {
  if (!highlight.length) return;
  viewer.center({ or: highlight.map((h) => ({ chain: h.chain, resi: h.resi })) }, 400);
}

const residueOf = (atom: any): ResidueRef => ({ chain: atom.chain ?? "", resnum: `${atom.resi}${(atom.icode ?? "").trim()}` });

/** Legenda sovrapposta al viewer (scala continua con tacche, oppure campioni). */
function ViewerLegend({ coloring }: { coloring: ResidueColoring }) {
  const legend = legendFor(coloring.scale, coloring.title, hasMissing(coloring));
  return (
    <div className="legend3d">
      <b>{legend.title}</b>
      {legend.kind === "gradient" ? (
        <div>
          <div className="bar" style={{ background: legend.css }} />
          <div className="ticks">
            {legend.ticks.map((t, i) => (
              <span key={i} style={{ left: `${t.position * 100}%`, transform: `translateX(${t.position <= 0.02 ? "0" : t.position >= 0.98 ? "-100%" : "-50%"})` }}>{t.label}</span>
            ))}
          </div>
        </div>
      ) : (
        legend.items.map((it) => <span key={it.label} className="sw"><i style={{ background: it.color }} />{it.label}</span>)
      )}
      {coloring.legendExtra?.map((it) => <span key={it.label} className="sw"><i style={{ background: it.color }} />{it.label}</span>)}
      {legend.noValue && <span className="sw"><i style={{ background: NO_VALUE_COLOR }} />senza valore</span>}
    </div>
  );
}

/** Visualizzatore 3D (3Dmol.js caricato su richiesta, contesto WebGL rilasciato allo smontaggio). */
export function Viewer3D({ pdb, url, height = 420, scheme: initial = "ss", highlight = [], format = "pdb", coloring = null, onPickResidue, centerKey }: Props) {
  const host = useRef<HTMLDivElement>(null);
  const viewer = useRef<any>(null);
  const [text, setText] = useState<string | null>(pdb ?? null);
  const [scheme, setScheme] = useState<Scheme>(initial);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [hover, setHover] = useState<string | null>(null);
  const colors = useMemo((): ViewerColors | null => {
    if (!coloring) return null;
    const residues = new Map<string, number>();
    coloringMap(coloring).forEach((hex, key) => residues.set(key, hexToInt(hex)));
    const chains = new Map(Object.entries(coloring.chainColors ?? {}).map(([chain, hex]) => [chain, hexToInt(hex)] as [string, number]));
    return { residues, chains };
  }, [coloring]);
  const interactive = !!coloring || !!onPickResidue;
  // i callback di 3Dmol leggono sempre i valori correnti
  const live = useRef({ scheme, highlight, colors, coloring, onPickResidue, centerKey, interactive });
  live.current = { scheme, highlight, colors, coloring, onPickResidue, centerKey, interactive };

  const enablePicking = (v: any) => {
    if (!live.current.interactive) return;
    v.setHoverDuration?.(120);
    v.setClickable({}, true, (atom: any) => live.current.onPickResidue?.(residueOf(atom)));
    v.setHoverable(
      {},
      true,
      (atom: any) => {
        const ref = residueOf(atom);
        const c = live.current.coloring;
        setHover(`${atom.resn ?? ""} ${ref.chain}${ref.resnum}${c ? ` · ${describeResidue(c, ref)}` : ""}`.trim());
      },
      () => setHover(null),
    );
  };

  useEffect(() => {
    setError(null);
    if (pdb) {
      setText(pdb);
      return;
    }
    if (!url) {
      setText(null);
      return;
    }
    setLoading(true);
    fetch(url)
      .then((r) => (r.ok ? r.text() : Promise.reject(new Error(`HTTP ${r.status}`))))
      .then(setText)
      .catch((e) => setError(String(e)))
      .finally(() => setLoading(false));
  }, [pdb, url]);

  useEffect(() => {
    let cancelled = false;
    if (!text || !host.current) return;
    import("3dmol")
      .then((mod: any) => {
        if (cancelled || !host.current) return;
        const $3Dmol = mod.default?.createViewer ? mod.default : mod;
        if (!viewer.current) {
          viewer.current = $3Dmol.createViewer(host.current, { backgroundColor: "#0b1120", antialias: true });
        }
        const v = viewer.current;
        const cur = live.current;
        v.clear();
        v.addModel(text, format === "cif" ? "cif" : "pdb");
        applyStyle(v, cur.scheme, cur.highlight, cur.colors);
        enablePicking(v);
        v.zoomTo();
        if (cur.centerKey !== undefined) centerOn(v, cur.highlight);
        v.render();
      })
      .catch((e) => setError(`Impossibile avviare il visualizzatore 3D: ${e}`));
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [text, format]);

  useEffect(() => {
    if (viewer.current && text) applyStyle(viewer.current, scheme, highlight, colors);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [scheme, JSON.stringify(highlight), colors]);

  useEffect(() => {
    if (viewer.current && text) enablePicking(viewer.current);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [interactive]);

  useEffect(() => {
    if (viewer.current && text && centerKey !== undefined) centerOn(viewer.current, highlight);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [centerKey]);

  useEffect(
    () => () => {
      try {
        viewer.current?.clear();
        const canvas = host.current?.querySelector("canvas");
        const gl = (canvas?.getContext("webgl2") || canvas?.getContext("webgl")) as WebGLRenderingContext | null;
        gl?.getExtension("WEBGL_lose_context")?.loseContext();
      } catch {
        /* nulla da rilasciare */
      }
      viewer.current = null;
    },
    [],
  );

  return (
    <div className="viewer3d" style={{ height }}>
      <div ref={host} style={{ position: "absolute", inset: 0 }} />
      {(loading || (!text && !error)) && (
        <div className="empty" style={{ position: "absolute", inset: 0, placeContent: "center" }}>
          {loading ? <Spinner /> : "Nessuna struttura selezionata"}
        </div>
      )}
      {error && <div className="empty" style={{ position: "absolute", inset: 0, placeContent: "center", color: "var(--err)" }}>{error}</div>}
      {text && coloring && <ViewerLegend coloring={coloring} />}
      {text && hover && <div className="hover3d">{hover}</div>}
      {text && (
        <div className="overlay">
          {!coloring && SCHEMES.map((s) => (
            <button key={s.value} className="btn btn-sm" style={s.value === scheme ? { borderColor: "var(--accent)", color: "var(--accent)" } : undefined} onClick={() => setScheme(s.value)}>
              {s.label}
            </button>
          ))}
          <button className="btn btn-sm" onClick={() => { viewer.current?.zoomTo(); viewer.current?.render(); }}>Centra</button>
        </div>
      )}
    </div>
  );
}
