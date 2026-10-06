import { useCallback, useEffect, useImperativeHandle, useMemo, useRef, useState, type Ref } from "react";
import { hexToInt, legendFor, NO_VALUE_COLOR } from "../lib/colorscale";
import { saveFile } from "../lib/exportImage";
import { atomKey, coloringMap, describeResidue, hasMissing, type ResidueColoring, type ResidueRef } from "../lib/residueColoring";
import { dataUriToBlob, viewerPngName } from "../lib/viewerImage";
import { Icon } from "./Icon";
import { Spinner } from "./ui";
import "../styles/viewer.css";

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
  /** segmenti tratteggiati fra coppie di punti (es. coordinazione metallo → donatore dei ligandi) */
  lines?: ViewerLine[];
  /** con `centerKey`: oltre a centrare, ingrandisce sui residui evidenziati (un sito di un ligando) */
  centerZoom?: boolean;
  /** sfere disegnate sopra la struttura (es. sfere alfa e centri delle tasche di fpocket) */
  spheres?: ViewerSphere[];
}

/** Sfera disegnata sopra la struttura (coordinate e raggio in Å del file). */
export interface ViewerSphere {
  center: [number, number, number];
  radius: number;
  color: string;
  /** 0–1 (1 = opaca) */
  opacity?: number;
}

/** Segmento tratteggiato disegnato sopra la struttura (coordinate in Å del file). */
export interface ViewerLine {
  start: [number, number, number];
  end: [number, number, number];
  color: string;
  /** piccola sfera alla fine del segmento, per un atomo che il viewer non disegna (acqua) */
  marker?: boolean;
}

// Ioni metallici (un atomo): a sfere, perché a bastoncini un atomo senza legami non si vedrebbe
const METAL_ELEMENTS = new Set(["LI", "NA", "K", "RB", "CS", "MG", "CA", "SR", "BA", "AL", "GA", "V", "CR", "MN", "FE", "CO", "NI", "CU", "ZN", "MO", "W", "CD", "HG", "PT", "AU", "AG", "PB"]);

/** Vista corrente del viewer, per chi la vuole incorporare (report del modello). */
export interface Viewer3DHandle {
  /** PNG della vista come data URI (3Dmol `pngURI`); null se il viewer non mostra una struttura */
  pngDataUri: () => string | null;
}

const NO_VALUE_INT = hexToInt(NO_VALUE_COLOR);

/** Colori per residuo e, per i residui non elencati, per catena (interi 0xRRGGBB). */
interface ViewerColors {
  residues: Map<string, number>;
  chains: Map<string, number>;
}

/* eslint-disable @typescript-eslint/no-explicit-any */
function applyStyle(viewer: any, scheme: Scheme, highlight: { chain: string; resi: number }[], colors: ViewerColors | null, lines: ViewerLine[] = [], spheres: ViewerSphere[] = []) {
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
  viewer.addStyle({ hetflag: true, predicate: (a: any) => METAL_ELEMENTS.has(String(a.elem ?? "").toUpperCase()) }, { sphere: { scale: 0.35 } });
  for (const h of highlight) {
    viewer.addStyle({ chain: h.chain, resi: h.resi }, { stick: { colorscheme: "orangeCarbon", radius: 0.25 } });
  }
  viewer.removeAllShapes?.();
  for (const l of lines) {
    viewer.addCylinder({
      start: { x: l.start[0], y: l.start[1], z: l.start[2] }, end: { x: l.end[0], y: l.end[1], z: l.end[2] },
      radius: 0.07, color: l.color, dashed: true, dashLength: 0.25, gapLength: 0.15, fromCap: 1, toCap: 1,
    });
    if (l.marker) viewer.addSphere({ center: { x: l.end[0], y: l.end[1], z: l.end[2] }, radius: 0.28, color: l.color });
  }
  for (const s of spheres) {
    viewer.addSphere({ center: { x: s.center[0], y: s.center[1], z: s.center[2] }, radius: s.radius, color: s.color, opacity: s.opacity ?? 1 });
  }
  viewer.render();
}

function centerOn(viewer: any, highlight: { chain: string; resi: number }[], zoom = false) {
  if (!highlight.length) return;
  const sel = { or: highlight.map((h) => ({ chain: h.chain, resi: h.resi })) };
  if (zoom) viewer.zoomTo(sel, 400);
  else viewer.center(sel, 400);
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

/**
 * Visualizzatore 3D (3Dmol.js caricato su richiesta, contesto WebGL rilasciato allo smontaggio). In basso a destra
 * "PNG" (la vista corrente) ed "Espandi": schermo intero con la Fullscreen API o, se manca o la richiesta è rifiutata
 * (iframe), con la classe `.fullscreen`; Esc torna alla vista normale.
 */
export function Viewer3D({ pdb, url, height = 420, scheme: initial = "ss", highlight = [], format = "pdb", coloring = null, onPickResidue, centerKey, handle, lines = [], centerZoom = false, spheres = [] }: Props) {
  const frame = useRef<HTMLDivElement>(null);
  const host = useRef<HTMLDivElement>(null);
  const viewer = useRef<any>(null);
  const [text, setText] = useState<string | null>(pdb ?? null);
  const [scheme, setScheme] = useState<Scheme>(initial);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [hover, setHover] = useState<string | null>(null);
  const [drawn, setDrawn] = useState(false);
  const [full, setFull] = useState<"native" | "css" | null>(null);
  const [pngFailed, setPngFailed] = useState(false);
  const colors = useMemo((): ViewerColors | null => {
    if (!coloring) return null;
    const residues = new Map<string, number>();
    coloringMap(coloring).forEach((hex, key) => residues.set(key, hexToInt(hex)));
    const chains = new Map(Object.entries(coloring.chainColors ?? {}).map(([chain, hex]) => [chain, hexToInt(hex)] as [string, number]));
    return { residues, chains };
  }, [coloring]);
  const interactive = !!coloring || !!onPickResidue;
  // i callback di 3Dmol leggono sempre i valori correnti
  const live = useRef({ scheme, highlight, colors, coloring, onPickResidue, centerKey, interactive, lines, centerZoom, spheres });
  live.current = { scheme, highlight, colors, coloring, onPickResidue, centerKey, interactive, lines, centerZoom, spheres };

  // Immagine (PNG, report): solo con la struttura disegnata e nessun caricamento in corso (con un altro `url` il
  // viewer mostra ancora la struttura precedente finché il file nuovo non arriva) né errore.
  const ready = drawn && !loading && !error && !!text;
  const readyNow = useRef(ready);
  readyNow.current = ready;
  const snapshot = useCallback((): string | null => {
    const v = viewer.current;
    if (!v || !readyNow.current || typeof v.pngURI !== "function") return null;
    try {
      v.render(); // il canvas WebGL di 3Dmol conserva il buffer: l'immagine è la vista corrente
      return v.pngURI() as string;
    } catch {
      return null;
    }
  }, []);
  useImperativeHandle(handle, () => ({ pngDataUri: snapshot }), [snapshot]);

  const savePng = () => {
    const uri = snapshot();
    try {
      // un canvas senza dimensioni dà "data:,", che non è un'immagine: dataUriToBlob lo rifiuta
      if (uri) saveFile(dataUriToBlob(uri), viewerPngName(pdb ? null : url));
      setPngFailed(!uri);
    } catch {
      setPngFailed(true);
    }
  };

  const toggleFull = () => {
    const el = frame.current;
    if (!el) return;
    if (full === "native") document.exitFullscreen().catch(() => setFull(null));
    else if (full === "css") setFull(null);
    else if (document.fullscreenEnabled) el.requestFullscreen().catch(() => setFull("css"));
    else setFull("css");
  };

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

  // Un file alla volta: cambiando modello la richiesta precedente si annulla e una risposta in ritardo non sostituisce
  // quella nuova (il viewer, il PNG e l'immagine del report mostrerebbero la struttura sbagliata).
  useEffect(() => {
    setError(null);
    if (pdb) {
      setText(pdb);
      setLoading(false);
      return;
    }
    if (!url) {
      setText(null);
      setLoading(false);
      return;
    }
    const ctrl = new AbortController();
    let stale = false;
    setLoading(true);
    fetch(url, { signal: ctrl.signal })
      .then((r) => (r.ok ? r.text() : Promise.reject(new Error(`HTTP ${r.status}`))))
      .then((t) => { if (!stale) setText(t); })
      .catch((e) => { if (!stale && e?.name !== "AbortError") setError(String(e)); })
      .finally(() => { if (!stale) setLoading(false); });
    return () => {
      stale = true;
      ctrl.abort();
    };
  }, [pdb, url]);

  useEffect(() => {
    let cancelled = false;
    setDrawn(false); // finché la struttura nuova non è disegnata
    setPngFailed(false);
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
        applyStyle(v, cur.scheme, cur.highlight, cur.colors, cur.lines, cur.spheres);
        enablePicking(v);
        v.zoomTo();
        if (cur.centerKey !== undefined) centerOn(v, cur.highlight, cur.centerZoom);
        v.render();
        setDrawn(true);
      })
      .catch((e) => {
        setDrawn(false);
        setError(`Impossibile avviare il visualizzatore 3D: ${e}`);
      });
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [text, format]);

  useEffect(() => {
    if (viewer.current && text) applyStyle(viewer.current, scheme, highlight, colors, lines, spheres);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [scheme, JSON.stringify(highlight), colors, JSON.stringify(lines), JSON.stringify(spheres)]);

  useEffect(() => {
    if (viewer.current && text) enablePicking(viewer.current);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [interactive]);

  useEffect(() => {
    if (viewer.current && text && centerKey !== undefined) centerOn(viewer.current, highlight, centerZoom);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [centerKey]);

  // Schermo intero: lo stato segue la Fullscreen API (anche l'uscita con Esc, gestita dal browser)
  useEffect(() => {
    const sync = () => {
      const mine = !!frame.current && document.fullscreenElement === frame.current;
      setFull((f) => (mine ? "native" : f === "native" ? null : f));
    };
    document.addEventListener("fullscreenchange", sync);
    return () => document.removeEventListener("fullscreenchange", sync);
  }, []);

  useEffect(() => {
    if (full !== "css") return;
    frame.current?.scrollIntoView({ block: "nearest" });
    // Esc in fase di cattura: esce dallo schermo intero senza chiudere la finestra che contiene il viewer
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "Escape") return;
      e.stopPropagation();
      setFull(null);
    };
    window.addEventListener("keydown", onKey, true);
    return () => window.removeEventListener("keydown", onKey, true);
  }, [full]);

  // la tela di 3Dmol si adatta alle dimensioni nuove, dopo l'impaginazione
  useEffect(() => {
    const v = viewer.current;
    if (!v) return;
    const id = requestAnimationFrame(() => {
      v.resize();
      v.render();
    });
    return () => cancelAnimationFrame(id);
  }, [full]);

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
    // a schermo intero l'altezza viene dalla Fullscreen API o dalla classe .fullscreen
    <div ref={frame} className={`viewer3d${full === "css" ? " fullscreen" : ""}`} style={full ? undefined : { height }}>
      <div ref={host} style={{ position: "absolute", inset: 0 }} />
      {(loading || (!text && !error)) && (
        <div className="empty" style={{ position: "absolute", inset: 0, placeContent: "center" }}>
          {loading ? <Spinner /> : "Nessuna struttura selezionata"}
        </div>
      )}
      {error && <div className="empty viewer3d-error" style={{ position: "absolute", inset: 0, placeContent: "center" }}>{error}</div>}
      {text && coloring && <ViewerLegend coloring={coloring} />}
      {text && hover && <div className="hover3d">{hover}</div>}
      {text && (
        <div className="overlay">
          {!coloring && SCHEMES.map((s) => (
            <button key={s.value} type="button" className="btn btn-sm" aria-pressed={s.value === scheme} onClick={() => setScheme(s.value)}>
              {s.label}
            </button>
          ))}
          <button type="button" className="btn btn-sm" onClick={() => { viewer.current?.zoomTo(); viewer.current?.render(); }}>Centra</button>
        </div>
      )}
      <div className="overlay-tr">
        <button type="button" className="btn btn-sm" disabled={!ready} onClick={savePng}
          title={pngFailed ? "Esportazione non riuscita: riprova" : "Scarica la vista corrente come immagine PNG"}>
          <Icon name={pngFailed ? "alert" : "image"} size={14} /> PNG
        </button>
        <button type="button" className="btn btn-sm" disabled={!text && !full} onClick={toggleFull}
          title={full ? "Torna alla vista normale (Esc)" : "Mostra la struttura a schermo intero"}>
          <Icon name={full ? "minimize" : "maximize"} size={14} /> {full ? "Esci (Esc)" : "Espandi"}
        </button>
      </div>
    </div>
  );
}
