import { useEffect, useRef, useState } from "react";
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
}

/* eslint-disable @typescript-eslint/no-explicit-any */
function applyStyle(viewer: any, scheme: Scheme, highlight: { chain: string; resi: number }[]) {
  viewer.setStyle({}, {});
  let cartoon: any;
  if (scheme === "ss") cartoon = { colorscheme: "ssPyMol", thickness: 0.4 };
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

/** Visualizzatore 3D (3Dmol.js caricato su richiesta, contesto WebGL rilasciato allo smontaggio). */
export function Viewer3D({ pdb, url, height = 420, scheme: initial = "ss", highlight = [], format = "pdb" }: Props) {
  const host = useRef<HTMLDivElement>(null);
  const viewer = useRef<any>(null);
  const [text, setText] = useState<string | null>(pdb ?? null);
  const [scheme, setScheme] = useState<Scheme>(initial);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

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
        v.clear();
        v.addModel(text, format === "cif" ? "cif" : "pdb");
        applyStyle(v, scheme, highlight);
        v.zoomTo();
        v.render();
      })
      .catch((e) => setError(`Impossibile avviare il visualizzatore 3D: ${e}`));
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [text, format]);

  useEffect(() => {
    if (viewer.current && text) applyStyle(viewer.current, scheme, highlight);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [scheme, JSON.stringify(highlight)]);

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
      {text && (
        <div className="overlay">
          {SCHEMES.map((s) => (
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
