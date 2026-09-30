import { useEffect, useImperativeHandle, useMemo, useRef, useState, type Ref } from "react";
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
  /** accesso facoltativo alla vista corrente (immagine PNG per il report del modello) */
  handle?: Ref<Viewer3DHandle>;
}

/** Vista corrente del viewer, per chi la vuole incorporare (report del modello). */
export interface Viewer3DHandle {
  /** PNG della vista come data URI (3Dmol `pngURI`); null se il viewer non mostra una struttura */
  pngDataUri: () => string | null;
}

const NO_VALUE_INT = hexToInt(NO_VALUE_COLOR);

/* eslint-disable @typescript-eslint/no-explicit-any */
function applyStyle(viewer: any, scheme: Scheme, highlight: { chain: string; resi: number }[], colors: Map<string, number> | null) {
  viewer.setStyle({}, {});
  let cartoon: any;
  if (colors) cartoon = { colorfunc: (atom: any) => colors.get(atomKey(atom)) ?? NO_VALUE_INT };
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
      {legend.noValue && <span className="sw"><i style={{ background: NO_VALUE_COLOR }} />senza valore</span>}
    </div>
  );
}

/** Visualizzatore 3D (3Dmol.js caricato su richiesta, contesto WebGL rilasciato allo smontaggio). */
export function Viewer3D({ pdb, url, height = 420, scheme: initial = "ss", highlight = [], format = "pdb", coloring = null, onPickResidue, centerKey, handle }: Props) {
  const host = useRef<HTMLDivElement>(null);
  const viewer = useRef<any>(null);
  const [text, setText] = useState<string | null>(pdb ?? null);
  const [scheme, setScheme] = useState<Scheme>(initial);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [hover, setHover] = useState<string | null>(null);
  const colors = useMemo(() => {
    if (!coloring) return null;
    const out = new Map<string, number>();
    coloringMap(coloring).forEach((hex, key) => out.set(key, hexToInt(hex)));
    return out;
  }, [coloring]);
  const interactive = !!coloring || !!onPickResidue;
  // i callback di 3Dmol leggono sempre i valori correnti
  const live = useRef({ scheme, highlight, colors, coloring, onPickResidue, centerKey, interactive });
  live.current = { scheme, highlight, colors, coloring, onPickResidue, centerKey, interactive };

  // Immagine per il report: solo con la struttura disegnata e nessun caricamento in corso (con un altro `url` il
  // viewer mostra ancora la struttura precedente finché il file nuovo non arriva) né errore.
  const shown = useRef(false);
  const idle = useRef(true);
  idle.current = !loading && !error && !!text;
  useImperativeHandle(handle, () => ({
    pngDataUri: () => {
      const v = viewer.current;
      if (!v || !shown.current || !idle.current || typeof v.pngURI !== "function") return null;
      try {
        v.render(); // il canvas WebGL di 3Dmol conserva il buffer: l'immagine è la vista corrente
        return v.pngURI() as string;
      } catch {
        return null;
      }
    },
  }), []);

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
    shown.current = false; // finché la struttura nuova non è disegnata
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
        shown.current = true;
      })
      .catch((e) => {
        shown.current = false;
        setError(`Impossibile avviare il visualizzatore 3D: ${e}`);
      });
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
      shown.current = false;
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
