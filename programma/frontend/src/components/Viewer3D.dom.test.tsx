// @vitest-environment jsdom
import { act, cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { AUTH_EVENT } from "../api/client";
import { CONTEXT_COLOR, CONTEXT_COLOR_LIGHT, hexToInt, MARKER_COLOR_LIGHT, REFERENCE_COLOR, REFERENCE_COLOR_LIGHT } from "../lib/colorscale";
import { CENTER_COLOR } from "../lib/pockets";
import type { ResidueColoring } from "../lib/residueColoring";
import { VIEWER_BACKGROUND, VIEWER_VIEW_STYLE } from "../lib/theme";
import { apiError, mockFetch } from "../test/dom";
import { Viewer3D } from "./Viewer3D";

// Visualizzatore 3D con gli effetti veri: lettura del file dal client delle API (sessione scaduta → accesso, non
// «HTTP 401»; richiesta annullata cambiando file) e sfondo del tema. 3Dmol (WebGL) è sostituito da un viewer finto.
const viewer = vi.hoisted(() => {
  const calls: { background: unknown[]; cartoon: unknown[]; viewStyle: unknown[]; spheres: unknown[] } = { background: [], cartoon: [], viewStyle: [], spheres: [] };
  const fake = {
    clear: () => undefined, addModel: () => undefined, addStyle: () => undefined,
    setStyle: (_sel: unknown, style: { cartoon?: unknown }) => void (style?.cartoon && calls.cartoon.push(style.cartoon)),
    // le sfere dell'ultimo disegno (ogni disegno toglie prima le forme)
    selectedAtoms: () => [], removeAllShapes: () => void (calls.spheres.length = 0), addCylinder: () => undefined,
    addSphere: (s: { color: unknown }) => void calls.spheres.push(s.color),
    // 3Dmol modifica l'oggetto ricevuto: si registra una copia
    setViewStyle: (p: Record<string, unknown>) => void calls.viewStyle.push({ ...p }),
    render: () => undefined, zoomTo: () => undefined, center: () => undefined, resize: () => undefined,
    setClickable: () => undefined, setHoverable: () => undefined, setHoverDuration: () => undefined,
    setBackgroundColor: (color: unknown) => void calls.background.push(color),
    pngURI: () => "data:image/png;base64,AAAA",
  };
  return { calls, fake, createViewer: (_host: unknown, opts: { backgroundColor: unknown }) => {
    calls.background.push(opts.backgroundColor);
    return fake;
  } };
});
vi.mock("3dmol", () => ({ default: { createViewer: viewer.createViewer } }));

const PDB = "ATOM      1  CA  ALA A   1       0.000   0.000   0.000  1.00 20.00           C\nEND\n";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  delete document.documentElement.dataset.theme;
  viewer.calls.background.length = 0;
  viewer.calls.cartoon.length = 0;
  viewer.calls.viewStyle.length = 0;
  viewer.calls.spheres.length = 0;
});

describe("lettura del file della struttura", () => {
  it("sessione scaduta (401 auth_required): evento della schermata di accesso e messaggio del server, non «HTTP 401»", async () => {
    mockFetch(() => apiError(401, "auth_required", "Accesso richiesto: inserisci la chiave d'accesso."));
    const required = vi.fn();
    window.addEventListener(AUTH_EVENT, required);
    try {
      render(<Viewer3D url="/api/jobs/j1/files/modello.pdb" />);
      expect(await screen.findByText(/Accesso richiesto/)).toBeTruthy();
      expect(required).toHaveBeenCalledTimes(1);
      expect(document.body.textContent).not.toContain("HTTP 401");
    } finally {
      window.removeEventListener(AUTH_EVENT, required);
    }
  });

  it("cambiando file la richiesta precedente si annulla e la sua risposta non conta", async () => {
    const pending: { url: string; signal?: AbortSignal | null; resolve: (r: Response) => void }[] = [];
    vi.stubGlobal("fetch", vi.fn((url: string, init: RequestInit = {}) => new Promise<Response>((resolve) => {
      pending.push({ url, signal: init.signal, resolve });
    })));
    const { rerender } = render(<Viewer3D url="/api/jobs/j1/files/a.pdb" />);
    rerender(<Viewer3D url="/api/jobs/j1/files/b.pdb" />);
    expect(pending.map((p) => p.url)).toEqual(["/api/jobs/j1/files/a.pdb", "/api/jobs/j1/files/b.pdb"]);
    expect(pending[0].signal?.aborted).toBe(true);
    expect(pending[1].signal?.aborted).toBe(false);
  });
});

describe("sfondo del tema", () => {
  it("la tela nasce con lo sfondo del tema e lo cambia quando cambia il tema, senza ricaricare la struttura", async () => {
    document.documentElement.dataset.theme = "dark";
    render(<Viewer3D pdb={PDB} />);
    await waitFor(() => expect(viewer.calls.background).toEqual([VIEWER_BACKGROUND.dark]));
    await act(async () => {
      document.documentElement.dataset.theme = "light";
    });
    expect(viewer.calls.background).toEqual([VIEWER_BACKGROUND.dark, VIEWER_BACKGROUND.light]);
    expect(screen.getByRole("button", { name: /PNG/ }).hasAttribute("disabled")).toBe(false); // struttura disegnata
  });

  it("grigio di contesto più scuro nel tema chiaro, nel disegno e nella legenda (si vedrebbe a malapena sul fondo chiaro)", async () => {
    const coloring: ResidueColoring = {
      key: "pocket", title: "Tasche", values: [{ chain: "A", resnum: "2", value: "1" }],
      scale: { kind: "categorical", categories: [{ key: "1", color: "#E69F00", label: "Tasca 1" }] },
      describe: (v) => String(v), chainColors: { A: CONTEXT_COLOR }, legendExtra: [{ color: CONTEXT_COLOR, label: "fuori dalle tasche" }],
    };
    // colore del residuo nell'ultimo stile disegnato (A1 fuori dalle tasche, A2 nella tasca 1)
    const residueColor = (resi: number) =>
      (viewer.calls.cartoon.at(-1) as { colorfunc?: (atom: { chain?: string; resi?: number }) => number } | undefined)?.colorfunc?.({ chain: "A", resi });
    const outside = () => residueColor(1);
    const swatch = () => (screen.getByText("fuori dalle tasche").querySelector("i") as HTMLElement).style.background;
    document.documentElement.dataset.theme = "dark";
    render(<Viewer3D pdb={PDB} coloring={coloring} />);
    await waitFor(() => expect(outside()).toBe(hexToInt(CONTEXT_COLOR)));
    expect(swatch()).toBe("rgb(226, 232, 240)");
    await act(async () => {
      document.documentElement.dataset.theme = "light";
    });
    expect(outside()).toBe(hexToInt(CONTEXT_COLOR_LIGHT));
    expect(swatch()).toBe("rgb(124, 135, 151)");
    expect(residueColor(2)).toBe(hexToInt("#E69F00")); // gli altri colori restano quelli della scala
  });

  it("contorno scuro solo nel tema chiaro (gradienti chiari staccati dal fondo), tolto tornando al tema scuro", async () => {
    document.documentElement.dataset.theme = "light";
    render(<Viewer3D pdb={PDB} />);
    await waitFor(() => expect(viewer.calls.viewStyle).toEqual([VIEWER_VIEW_STYLE.light]));
    await act(async () => {
      document.documentElement.dataset.theme = "dark";
    });
    expect(viewer.calls.viewStyle.at(-1)).toEqual(VIEWER_VIEW_STYLE.dark);
    expect(viewer.calls.background.at(-1)).toBe(VIEWER_BACKGROUND.dark);
  });

  it("centro delle tasche: bianco nel tema scuro, ardesia scura nel chiaro, ridisegnato al cambio di tema", async () => {
    const spheres = [{ center: [0, 0, 0] as [number, number, number], radius: 1.1, color: CENTER_COLOR, opacity: 0.9 },
      { center: [1, 0, 0] as [number, number, number], radius: 0.45, color: "#E69F00", opacity: 0.85 }];
    document.documentElement.dataset.theme = "dark";
    render(<Viewer3D pdb={PDB} spheres={spheres} />);
    await waitFor(() => expect(viewer.calls.spheres).toEqual([CENTER_COLOR, "#E69F00"]));
    await act(async () => {
      document.documentElement.dataset.theme = "light";
    });
    expect(viewer.calls.spheres).toEqual([MARKER_COLOR_LIGHT, "#E69F00"]);
  });

  it("confronto: la struttura A (riferimento) più scura nel tema chiaro, nel disegno e nella legenda", async () => {
    const coloring: ResidueColoring = {
      key: "distance", title: "Distanza", values: [{ chain: "A", resnum: "1", value: 0.4, color: REFERENCE_COLOR }],
      scale: { kind: "continuous", stops: [{ value: 0, color: "#2166ac" }, { value: 5, color: "#b2182b" }] },
      describe: (v) => String(v), chainColors: { A: REFERENCE_COLOR }, legendExtra: [{ color: REFERENCE_COLOR, label: "A (riferimento)" }],
    };
    const colorOf = (resi: number) =>
      (viewer.calls.cartoon.at(-1) as { colorfunc?: (atom: { chain?: string; resi?: number }) => number } | undefined)?.colorfunc?.({ chain: "A", resi });
    const swatch = () => (screen.getByText("A (riferimento)").querySelector("i") as HTMLElement).style.background;
    document.documentElement.dataset.theme = "dark";
    render(<Viewer3D pdb={PDB} coloring={coloring} />);
    await waitFor(() => expect(colorOf(1)).toBe(hexToInt(REFERENCE_COLOR)));
    expect(colorOf(2)).toBe(hexToInt(REFERENCE_COLOR)); // residuo non elencato: colore della catena
    expect(swatch()).toBe("rgb(203, 213, 225)");
    await act(async () => {
      document.documentElement.dataset.theme = "light";
    });
    expect(colorOf(1)).toBe(hexToInt(REFERENCE_COLOR_LIGHT));
    expect(colorOf(2)).toBe(hexToInt(REFERENCE_COLOR_LIGHT));
    expect(swatch()).toBe("rgb(30, 41, 59)");
  });
});
