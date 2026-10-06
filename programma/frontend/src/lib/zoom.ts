import { useCallback, useEffect, useMemo, useRef, useState, type PointerEvent as RPointerEvent, type RefObject } from "react";

/**
 * Ingrandimento dei grafici: intervallo visibile di un asse (`Domain`), con i limiti dei dati e un'ampiezza minima,
 * più il trascinamento di una selezione sull'SVG (brush) e la rotella. Le funzioni pure sono testate in zoom.test.ts;
 * gli hook le collegano ai grafici (lo stato resta nel componente del grafico, condiviso tra la vista nella pagina e
 * quella espansa).
 */
export interface Domain {
  lo: number;
  hi: number;
}

/** Intervallo dentro [min, max], ampio almeno `minSpan` (o tutto, se i dati sono più corti). */
export function clampDomain(d: Domain, min: number, max: number, minSpan = 0): Domain {
  const full = Math.max(0, max - min);
  const span = Math.min(full, Math.max(minSpan, Math.abs(d.hi - d.lo)));
  const start = Math.min(d.lo, d.hi);
  const lo = Math.max(min, Math.min(start, max - span));
  return { lo, hi: lo + span };
}

/** Ingrandisce (factor < 1) o riduce (factor > 1) attorno a `center` (predefinito: il centro). */
export function zoomDomain(d: Domain, factor: number, min: number, max: number, minSpan = 0, center = (d.lo + d.hi) / 2): Domain {
  const c = Math.max(d.lo, Math.min(d.hi, center));
  const span = d.hi - d.lo;
  // non sotto l'ampiezza minima: il fattore si riduce, il punto sotto il mouse resta fermo
  const f = span > 0 && span * factor < minSpan ? Math.min(1, minSpan / span) : factor;
  return clampDomain({ lo: c - (c - d.lo) * f, hi: c + (d.hi - c) * f }, min, max, minSpan);
}

/** Sposta l'intervallo di `delta` restando nei limiti. */
export function panDomain(d: Domain, delta: number, min: number, max: number): Domain {
  const span = d.hi - d.lo;
  const lo = Math.max(min, Math.min(max - span, d.lo + delta));
  return { lo, hi: lo + span };
}

export function isFullDomain(d: Domain, min: number, max: number): boolean {
  const eps = Math.max(1e-9, (max - min) * 1e-6);
  return d.lo <= min + eps && d.hi >= max - eps;
}

/** Passi "tondi" per gli angoli in gradi (…, 15, 30, 45, 60, 90, …). */
export const ANGLE_STEPS = [1, 1.5, 3, 4.5, 6, 9, 10];

/** Valori "tondi" per le tacche di un asse tra lo e hi (circa `count`); `bases` sono i passi per potenza di dieci. */
export function niceTicks(lo: number, hi: number, count = 6, bases: number[] = [1, 2, 2.5, 5, 10]): number[] {
  if (!Number.isFinite(lo) || !Number.isFinite(hi) || hi <= lo) return Number.isFinite(lo) ? [lo] : [];
  const raw = (hi - lo) / Math.max(1, count);
  const mag = 10 ** Math.floor(Math.log10(raw));
  const step = bases.map((m) => m * mag).find((s) => s >= raw * (1 - 1e-9)) ?? 10 * mag;
  const out: number[] = [];
  for (let v = Math.ceil(lo / step) * step; v <= hi + step * 1e-9; v += step) out.push(Math.abs(v) < step * 1e-9 ? 0 : Number(v.toPrecision(12)));
  return out;
}

/** Indici interi visibili [first, last] di un intervallo su indici (residui, posizioni). */
export function visibleIndices(d: Domain, count: number): [number, number] {
  const first = Math.max(0, Math.floor(d.lo));
  const last = Math.min(count - 1, Math.ceil(d.hi));
  return [first, Math.max(first, last)];
}

export interface DomainState {
  domain: Domain;
  /** l'intervallo è quello completo */
  full: boolean;
  set: (d: Domain) => void;
  reset: () => void;
  zoomIn: (center?: number) => void;
  zoomOut: (center?: number) => void;
  /** sposta di una frazione dell'ampiezza visibile (es. ±0,5) */
  pan: (fraction: number) => void;
  /** zoom della rotella: fattore e centro in unità dei dati */
  zoomAt: (factor: number, center: number) => void;
}

/** Intervallo di ampiezza almeno `minSpan` centrato tra `a` e `b` (una selezione stretta resta al centro). */
export function spanAround(a: number, b: number, minSpan: number): Domain {
  const c = (a + b) / 2;
  const half = Math.max(minSpan, Math.abs(b - a)) / 2;
  return { lo: c - half, hi: c + half };
}

/**
 * Fattore di zoom di un evento della rotella (< 1 ingrandisce, > 1 riduce), proporzionale a deltaY: una tacca del
 * mouse vale circa ×0,82 / ×1,22, i tanti piccoli eventi di un touchpad o del pinch si sommano in modo continuo.
 * null senza componente verticale (scorrimento orizzontale, Maiusc + rotella): l'evento resta alla pagina.
 */
export function wheelFactor(e: Pick<WheelEvent, "deltaY" | "deltaMode">): number | null {
  if (!e.deltaY) return null;
  // deltaMode: 0 pixel, 1 righe, 2 pagine
  const px = e.deltaY * (e.deltaMode === 1 ? 16 : e.deltaMode === 2 ? 400 : 1);
  return Math.exp(Math.max(-200, Math.min(200, px)) * 0.002);
}

/**
 * Stato di un asse ingrandibile. Torna all'intervallo completo quando cambiano i limiti dei dati o `resetKey` (es. gli
 * elementi riordinati con lo stesso numero), già nel render in cui cambiano. Gli aggiornamenti partono dallo stato
 * più recente: più eventi della rotella prima di un render si sommano. `set` centra un intervallo più stretto di
 * `minSpan` sul suo centro (come nel Ramachandran).
 */
export function useDomain(min: number, max: number, minSpan = 0, resetKey?: unknown): DomainState {
  const [raw, setRaw] = useState<Domain>({ lo: min, hi: max });
  const [bounds, setBounds] = useState({ min, max, resetKey });
  if (bounds.min !== min || bounds.max !== max || !Object.is(bounds.resetKey, resetKey)) {
    setBounds({ min, max, resetKey });
    setRaw({ lo: min, hi: max });
  }
  const domain = useMemo(() => clampDomain(raw, min, max, minSpan), [raw, min, max, minSpan]);
  const update = useCallback(
    (fn: (d: Domain) => Domain) => setRaw((prev) => clampDomain(fn(clampDomain(prev, min, max, minSpan)), min, max, minSpan)),
    [min, max, minSpan],
  );
  const set = useCallback((d: Domain) => update(() => spanAround(d.lo, d.hi, minSpan)), [update, minSpan]);
  // stesso oggetto se è già tutto: niente render in più
  const reset = useCallback(() => setRaw((r) => (r.lo === min && r.hi === max ? r : { lo: min, hi: max })), [min, max]);
  return {
    domain,
    full: isFullDomain(domain, min, max),
    set,
    reset,
    zoomIn: (center?: number) => update((d) => zoomDomain(d, 0.5, min, max, minSpan, center)),
    zoomOut: (center?: number) => update((d) => zoomDomain(d, 2, min, max, minSpan, center)),
    pan: (fraction: number) => update((d) => panDomain(d, (d.hi - d.lo) * fraction, min, max)),
    zoomAt: (factor: number, center: number) => update((d) => zoomDomain(d, factor, min, max, minSpan, center)),
  };
}

// ---------------------------------------------------------------------------------- coordinate SVG

/** Punto del mouse nelle coordinate del viewBox dell'SVG (tiene conto di viewBox, scala e preserveAspectRatio). */
export function svgPoint(svg: SVGSVGElement, clientX: number, clientY: number): { x: number; y: number } {
  const ctm = typeof svg.getScreenCTM === "function" ? svg.getScreenCTM() : null;
  if (ctm && typeof svg.createSVGPoint === "function") {
    const p = svg.createSVGPoint();
    p.x = clientX;
    p.y = clientY;
    const q = p.matrixTransform(ctm.inverse());
    return { x: q.x, y: q.y };
  }
  // ripiego: proporzione sul riquadro (viewBox senza margini di adattamento)
  const box = svg.getBoundingClientRect();
  const vb = svg.viewBox?.baseVal;
  const w = vb && vb.width ? vb.width : box.width;
  const h = vb && vb.height ? vb.height : box.height;
  return { x: ((clientX - box.left) / (box.width || 1)) * w, y: ((clientY - box.top) / (box.height || 1)) * h };
}

export interface Brush {
  x0: number;
  y0: number;
  x1: number;
  y1: number;
}

/** Trascinamento minimo (unità del viewBox) perché un gesto diventi una selezione. */
export const MIN_DRAG = 4;

/** Classe sulla radice durante un trascinamento: niente selezione del testo della pagina (styles.css). */
const DRAGGING = "chart-dragging";

function setDragging(on: boolean) {
  if (typeof document === "undefined") return;
  document.documentElement.classList.toggle(DRAGGING, on);
  // la selezione nativa può essere già partita al pointerdown, prima della soglia
  if (on) window.getSelection?.()?.removeAllRanges();
}

/**
 * Selezione trascinata sull'SVG (coordinate del viewBox). Un trascinamento oltre `minDrag` unità chiama `onBrush`;
 * il clic che lo conclude va ignorato dal grafico con `consumeClick()` (vero solo subito dopo un trascinamento).
 * `area` limita l'inizio del trascinamento all'area dei dati. Il puntatore si cattura solo quando il trascinamento
 * comincia davvero: un clic semplice arriva ancora agli elementi figli (punti, rettangoli con onClick).
 * Il rettangolo finale è l'ultima posizione del puntatore (non lo stato dell'ultimo render); pulsante rilasciato fuori,
 * pointercancel (es. il browser fa scorrere la pagina al tocco) o cattura persa annullano la selezione.
 */
export function useSvgBrush(opts: {
  enabled: boolean;
  onBrush: (b: Brush) => void;
  minDrag?: number;
  area?: { x0: number; x1: number; y0: number; y1: number };
}) {
  const [brush, setBrush] = useState<Brush | null>(null);
  const start = useRef<{ x: number; y: number } | null>(null);
  const last = useRef<Brush | null>(null);
  const dragged = useRef(false);
  // istante fino al quale il clic va ignorato (il click arriva subito dopo il pointerup del trascinamento)
  const suppressUntil = useRef(0);
  const saved = useRef(opts);
  saved.current = opts;
  const minDrag = opts.minDrag ?? MIN_DRAG;
  useEffect(() => () => {
    if (dragged.current) setDragging(false);
  }, []);
  const finish = (commit: boolean) => {
    const b = last.current;
    const was = dragged.current;
    start.current = null;
    last.current = null;
    dragged.current = false;
    setBrush(null);
    if (!was) return;
    setDragging(false);
    suppressUntil.current = performance.now() + 400;
    if (commit && b) saved.current.onBrush(b);
  };
  const onPointerDown = (e: RPointerEvent<SVGSVGElement>) => {
    if (!saved.current.enabled || e.button !== 0) return;
    const p = svgPoint(e.currentTarget, e.clientX, e.clientY);
    const a = saved.current.area;
    if (a && (p.x < a.x0 || p.x > a.x1 || p.y < a.y0 || p.y > a.y1)) return;
    start.current = p;
    dragged.current = false;
  };
  const onPointerMove = (e: RPointerEvent<SVGSVGElement>) => {
    const s = start.current;
    if (!s) return;
    if (e.buttons === 0) {
      // pulsante rilasciato fuori dall'SVG senza pointerup qui: niente zoom, rettangolo tolto
      finish(false);
      return;
    }
    const p = svgPoint(e.currentTarget, e.clientX, e.clientY);
    const a = saved.current.area;
    const x = a ? Math.max(a.x0, Math.min(a.x1, p.x)) : p.x;
    const y = a ? Math.max(a.y0, Math.min(a.y1, p.y)) : p.y;
    if (!dragged.current) {
      if (Math.abs(x - s.x) < minDrag && Math.abs(y - s.y) < minDrag) return;
      dragged.current = true;
      setDragging(true);
      try {
        e.currentTarget.setPointerCapture(e.pointerId);
      } catch {
        /* puntatore già rilasciato */
      }
    }
    const b = { x0: s.x, y0: s.y, x1: x, y1: y };
    last.current = b;
    setBrush(b);
  };
  return {
    brush,
    handlers: {
      onPointerDown,
      onPointerMove,
      onPointerUp: () => finish(true),
      onPointerCancel: () => finish(false),
      // dopo un pointerup normale non fa nulla (trascinamento già chiuso)
      onLostPointerCapture: () => finish(false),
    },
    /** true se il clic appena arrivato chiude un trascinamento (e va ignorato) */
    consumeClick: () => {
      const suppress = performance.now() < suppressUntil.current;
      suppressUntil.current = 0;
      return suppress;
    },
  };
}

/**
 * Rotella sull'elemento (listener non passivo) quando `enabled`. Se `onWheel` restituisce false l'evento prosegue
 * (la pagina scorre come sempre); altrimenti è consumato (niente scorrimento né zoom del browser con Ctrl).
 */
export function useWheel(ref: RefObject<Element | null>, enabled: boolean, onWheel: (e: WheelEvent) => boolean | void) {
  const saved = useRef(onWheel);
  saved.current = onWheel;
  useEffect(() => {
    const el = ref.current;
    if (!el || !enabled) return;
    const handler = (e: Event) => {
      if (saved.current(e as WheelEvent) === false) return;
      e.preventDefault();
    };
    el.addEventListener("wheel", handler, { passive: false });
    return () => el.removeEventListener("wheel", handler);
  }, [ref, enabled]);
}

// ---------------------------------------------------------------------------------- asse x ingrandibile

/**
 * Asse x di un grafico ingrandibile, da usare dentro il componente che disegna l'SVG (uno per vista: pagina ed
 * espansa hanno ciascuna il proprio SVG, lo stato `zoom` è condiviso). `range` sono gli estremi in unità del viewBox
 * dell'area dei dati; `scale` porta un valore dei dati sull'asse, `invert` fa il contrario.
 *
 * Collegamenti all'SVG: `ref={axis.svgRef}`, `{...axis.handlers}`, e nel clic `if (axis.consumeClick()) return;`.
 * Trascinare seleziona un tratto e lo ingrandisce; la rotella ingrandisce attorno al mouse quando `wheel` è vero
 * (vista espansa) oppure, nella pagina, tenendo premuto Ctrl.
 */
export function useZoomAxis({ zoom, range, wheel = false, enabled = true, area }: {
  zoom: DomainState;
  range: [number, number];
  wheel?: boolean;
  enabled?: boolean;
  /** area dei dati in y (inizio del trascinamento), facoltativa */
  area?: { y0: number; y1: number };
}) {
  const [x0, x1] = range;
  const { lo, hi } = zoom.domain;
  const span = hi - lo || 1;
  const scale = (v: number) => x0 + ((v - lo) / span) * (x1 - x0);
  const invert = (px: number) => lo + ((px - x0) / (x1 - x0 || 1)) * span;
  const svgRef = useRef<SVGSVGElement>(null);
  const brush = useSvgBrush({
    enabled,
    area: area ? { x0, x1, y0: area.y0, y1: area.y1 } : undefined,
    onBrush: (b) => {
      // gesto quasi verticale (scorrimento mancato, mouse in verticale): non è una selezione sull'asse x
      if (Math.abs(b.x1 - b.x0) < MIN_DRAG) return;
      const a = invert(Math.min(b.x0, b.x1));
      const c = invert(Math.max(b.x0, b.x1));
      if (c > a) zoom.set({ lo: a, hi: c });
    },
  });
  useWheel(svgRef, enabled, (e) => {
    const ctrl = e.ctrlKey || e.metaKey;
    if (!wheel && !ctrl) return false; // senza Ctrl la rotella scorre la pagina come sempre
    const f = wheelFactor(e);
    if (f === null) return false; // scorrimento orizzontale: alla pagina
    // già tutto e si riduce: senza Ctrl scorre la finestra; con Ctrl si consuma (niente zoom della pagina)
    if (f > 1 && zoom.full) return ctrl ? undefined : false;
    const svg = svgRef.current;
    if (!svg) return false;
    const p = svgPoint(svg, e.clientX, e.clientY);
    zoom.zoomAt(f, invert(Math.max(x0, Math.min(x1, p.x))));
  });
  return {
    scale,
    invert,
    svgRef,
    brush: brush.brush,
    handlers: brush.handlers,
    consumeClick: brush.consumeClick,
    /** rettangolo della selezione in corso (coordinate del viewBox) da disegnare con className="brush" */
    brushRect: brush.brush
      ? { x: Math.min(brush.brush.x0, brush.brush.x1), width: Math.abs(brush.brush.x1 - brush.brush.x0) }
      : null,
  };
}
