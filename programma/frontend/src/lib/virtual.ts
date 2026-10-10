import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState, type RefObject, type UIEvent } from "react";
import { rowScrollDelta } from "./compare";

/**
 * Rendering a finestra delle liste lunghe (tabella dei residui in Qualità, coppie in Confronto, righe di
 * AlignmentView): oltre `VIRTUAL_THRESHOLD` righe si disegnano solo quelle visibili nel contenitore che scorre, più
 * `overscan` sopra e sotto, con due spaziatori che tengono l'altezza totale (la barra di scorrimento resta quella
 * della lista intera). Sotto la soglia si disegna tutto come prima: le liste brevi non cambiano.
 *
 * Le posizioni vengono dalle altezze delle righe (`rowOffsets`): fisse e misurate sulla prima riga disegnata per le
 * tabelle, per tipo di riga negli allineamenti. La selezione si porta in vista scorrendo solo il contenitore
 * (`revealScrollTop`, come `rowScrollDelta` di Confronto), anche quando la riga non è ancora disegnata.
 */
export const VIRTUAL_THRESHOLD = 200;
export const DEFAULT_OVERSCAN = 8;
/**
 * Altezza fissa delle righe di tabella a finestra: tutte uguali, anche quelle con un badge (22 px + 2 × 8 px di
 * padding), e senza a capo (`tr.virtual-row`). Con righe di altezze diverse la stima delle posizioni si sposterebbe
 * lungo la lista e la finestra disegnata potrebbe non coprire la parte visibile.
 */
export const TABLE_ROW_HEIGHT = 40;

export interface WindowRange {
  /** prima riga disegnata (compresa) */
  start: number;
  /** ultima riga disegnata (esclusa) */
  end: number;
  /** altezza dello spaziatore sopra e sotto le righe disegnate */
  padTop: number;
  padBottom: number;
}

export function shouldVirtualize(count: number, threshold = VIRTUAL_THRESHOLD): boolean {
  return count > threshold;
}

/** Inizio di ogni riga dall'alto della lista; l'ultimo valore (indice `count`) è l'altezza totale. */
export function rowOffsets(count: number, height: number | ((index: number) => number)): number[] {
  const out = new Array<number>(Math.max(0, count) + 1);
  out[0] = 0;
  for (let i = 0; i < count; i++) out[i + 1] = out[i] + Math.max(0, typeof height === "number" ? height : height(i));
  return out;
}

/** Prima riga il cui fondo è oltre `y` (ricerca binaria sulle posizioni). */
function rowAt(offsets: number[], y: number): number {
  const count = offsets.length - 1;
  let lo = 0;
  let hi = count;
  while (lo < hi) {
    const mid = (lo + hi) >> 1;
    if (offsets[mid + 1] <= y) lo = mid + 1;
    else hi = mid;
  }
  return lo;
}

/**
 * Righe da disegnare per la porzione visibile [`scrollTop`, `scrollTop + viewport`) della lista (coordinate della
 * lista: chi ha un'intestazione o un margine sopra le righe li sottrae prima).
 */
export function visibleRange(offsets: number[], scrollTop: number, viewport: number, overscan = DEFAULT_OVERSCAN): WindowRange {
  const count = offsets.length - 1;
  const total = offsets[count] ?? 0;
  if (count <= 0) return { start: 0, end: 0, padTop: 0, padBottom: 0 };
  const top = Math.min(Math.max(0, scrollTop), total);
  const bottom = Math.min(total, top + Math.max(0, viewport));
  const first = Math.min(rowAt(offsets, top), count - 1);
  // ultima riga che ha almeno un pixel sopra il bordo inferiore (una riga che comincia sul bordo non si vede)
  const lastRow = bottom > top ? rowAt(offsets, bottom - 1e-6) : first;
  const last = Math.max(first + 1, lastRow + 1);
  const start = Math.max(0, first - overscan);
  const end = Math.min(count, last + overscan);
  return { start, end, padTop: offsets[start], padBottom: total - offsets[end] };
}

/** Tutte le righe (lista breve o finestra disattivata). */
export function fullRange(count: number): WindowRange {
  return { start: 0, end: Math.max(0, count), padTop: 0, padBottom: 0 };
}

export interface ScrollView {
  /** `scrollTop` del contenitore */
  scrollTop: number;
  /** altezza visibile del contenitore (`clientHeight`) */
  height: number;
  /** spazio sopra la prima riga nel contenuto (intestazione fissa della tabella, margine); è coperto dall'intestazione */
  header?: number;
}

/**
 * Nuovo `scrollTop` perché la riga `index` sia tutta visibile sotto l'intestazione fissa: null se lo è già, altrimenti
 * la riga al centro, nei limiti dello scorrimento. Non serve che la riga sia disegnata.
 */
export function revealScrollTop(offsets: number[], index: number, view: ScrollView): number | null {
  const count = offsets.length - 1;
  if (index < 0 || index >= count) return null;
  const header = view.header ?? 0;
  const top = header + offsets[index];
  const bottom = header + offsets[index + 1];
  const visibleTop = view.scrollTop + header;
  const visibleBottom = view.scrollTop + view.height;
  if (top >= visibleTop && bottom <= visibleBottom) return null;
  const room = Math.max(0, view.height - header);
  const target = offsets[index] - (room - (bottom - top)) / 2;
  const max = Math.max(0, header + offsets[count] - view.height);
  return Math.round(Math.min(max, Math.max(0, target)));
}

export interface VirtualRowsOptions {
  count: number;
  /** altezza di una riga in px (stima: per le tabelle si misura la prima riga disegnata) o altezza per indice */
  rowHeight: number | ((index: number) => number);
  /** misura l'altezza delle righe (solo con `rowHeight` numerico e righe tutte alte uguali) */
  measure?: boolean;
  /** spazio fisso sopra le righe nel contenitore, se non c'è una `thead` da misurare */
  header?: number;
  threshold?: number;
  overscan?: number;
}

export interface VirtualRows<T extends HTMLElement> {
  /** contenitore che scorre (overflow: auto e altezza massima) */
  ref: RefObject<T | null>;
  onScroll: (e: UIEvent<T>) => void;
  /** finestra attiva (lista oltre la soglia) */
  active: boolean;
  range: WindowRange;
  /** porta in vista la riga `index` scorrendo solo il contenitore */
  reveal: (index: number) => void;
}

/**
 * Finestra di righe per un contenitore che scorre. Le righe disegnate portano `data-vrow={indice}`: servono alla
 * misura dell'altezza e alla correzione finale di `reveal` sulla riga vera.
 */
export function useVirtualRows<T extends HTMLElement = HTMLDivElement>(opts: VirtualRowsOptions): VirtualRows<T> {
  const { count, rowHeight, measure = false, threshold = VIRTUAL_THRESHOLD, overscan = DEFAULT_OVERSCAN } = opts;
  const ref = useRef<T | null>(null);
  const [scroll, setScroll] = useState({ top: 0, height: 600 });
  const [header, setHeader] = useState(opts.header ?? 0);
  const [measured, setMeasured] = useState<number | null>(null);
  const active = shouldVirtualize(count, threshold);
  const height = typeof rowHeight === "number" ? measured ?? rowHeight : rowHeight;
  const offsets = useMemo(() => rowOffsets(count, height), [count, height]);
  // riga da correggere sulla sua posizione vera appena disegnata (vedi `reveal`)
  const pending = useRef<number | null>(null);

  const read = useCallback(() => {
    const box = ref.current;
    if (!box) return;
    setScroll((prev) => (prev.top === box.scrollTop && prev.height === box.clientHeight ? prev : { top: box.scrollTop, height: box.clientHeight }));
  }, []);

  // gli eventi di scorrimento arrivano già al più uno per fotogramma: si legge subito (con requestAnimationFrame una
  // scheda in secondo piano, dove i fotogrammi sono sospesi, resterebbe con la finestra vecchia)
  const onScroll = read;

  // dimensioni del contenitore e intestazione della tabella; altezza vera delle righe
  // eslint-disable-next-line react-hooks/exhaustive-deps -- misura il DOM dopo ogni disegno; setHeader e setMeasured solo se la misura cambia
  useLayoutEffect(() => {
    const box = ref.current;
    if (!box) return;
    // niente "scroll anchoring" del browser: righe che escono dalla finestra e spaziatori che crescono sopra di lei
    // gli farebbero correggere lo scorrimento, e la finestra inseguirebbe la correzione
    box.style.overflowAnchor = active ? "none" : "";
    read();
    const thead = box.querySelector("thead");
    const head = thead ? thead.getBoundingClientRect().height : opts.header ?? 0;
    if (Math.abs(head - header) > 0.5) setHeader(head);
    if (measure && typeof rowHeight === "number") {
      const row = box.querySelector<HTMLElement>("[data-vrow]");
      const h = row?.getBoundingClientRect().height;
      if (h && Math.abs(h - (measured ?? rowHeight)) > 0.5) setMeasured(h);
    }
    if (pending.current !== null) {
      const row = box.querySelector<HTMLElement>(`[data-vrow="${pending.current}"]`);
      if (row) {
        pending.current = null;
        const delta = rowScrollDelta(row.getBoundingClientRect(), box.getBoundingClientRect(), head);
        if (delta) box.scrollTop += delta;
      }
    }
  });

  useEffect(() => {
    const box = ref.current;
    if (!box || typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(() => read());
    observer.observe(box);
    return () => observer.disconnect();
  }, [read]);

  const range = useMemo(
    () => (active ? visibleRange(offsets, scroll.top - header, scroll.height, overscan) : fullRange(count)),
    [active, offsets, scroll, header, overscan, count],
  );

  // `reveal` resta la stessa funzione (legge posizioni e intestazione più recenti): chi la chiama a ogni cambio di
  // selezione non la richiama quando cambia solo la misura delle righe
  const latest = useRef({ offsets, header });
  latest.current = { offsets, header };
  const reveal = useCallback((index: number) => {
    const box = ref.current;
    if (!box || index < 0) return;
    const row = box.querySelector<HTMLElement>(`[data-vrow="${index}"]`);
    if (row) { // riga già disegnata: posizione vera
      const delta = rowScrollDelta(row.getBoundingClientRect(), box.getBoundingClientRect(), latest.current.header);
      if (delta) box.scrollTop += delta;
      read();
      return;
    }
    const { offsets: rows, header: head } = latest.current;
    const top = revealScrollTop(rows, index, { scrollTop: box.scrollTop, height: box.clientHeight, header: head });
    if (top !== null) box.scrollTop = top;
    pending.current = index; // disegnata dopo lo scorrimento: correzione sulla riga vera nel prossimo render
    read();
  }, [read]);

  return { ref, onScroll, active, range, reveal };
}
