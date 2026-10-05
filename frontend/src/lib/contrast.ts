/**
 * Contrasto tra colori secondo WCAG 2.1 (luminanza relativa, rapporto da 1:1 a 21:1). Serve ai test del foglio di
 * stile: le coppie testo/sfondo dei due temi devono restare leggibili (AA: 4,5:1 per il testo normale).
 */
export type RGBA = [number, number, number, number];

/** `#rgb`, `#rrggbb`, `rgb(r, g, b)` o `rgba(r, g, b, a)`; null per un formato diverso (es. `var(...)`). */
export function parseColor(text: string): RGBA | null {
  const s = text.trim().toLowerCase();
  const hex = /^#([0-9a-f]{3}|[0-9a-f]{6})$/.exec(s);
  if (hex) {
    const h = hex[1].length === 3 ? hex[1].replace(/./g, (c) => c + c) : hex[1];
    return [parseInt(h.slice(0, 2), 16), parseInt(h.slice(2, 4), 16), parseInt(h.slice(4, 6), 16), 1];
  }
  const fn = /^rgba?\(\s*([\d.]+)\s*,\s*([\d.]+)\s*,\s*([\d.]+)\s*(?:,\s*([\d.]+)\s*)?\)$/.exec(s);
  if (fn) return [Number(fn[1]), Number(fn[2]), Number(fn[3]), fn[4] === undefined ? 1 : Number(fn[4])];
  return null;
}

/** Colore semitrasparente steso su uno sfondo opaco. */
export function composite(top: RGBA, under: RGBA): RGBA {
  const a = top[3];
  return [top[0] * a + under[0] * (1 - a), top[1] * a + under[1] * (1 - a), top[2] * a + under[2] * (1 - a), 1];
}

export function luminance([r, g, b]: RGBA): number {
  const lin = (c: number) => {
    const v = c / 255;
    return v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4;
  };
  return 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b);
}

/** Rapporto di contrasto (≥ 1); un primo piano semitrasparente si compone prima sullo sfondo. */
export function contrastRatio(fg: RGBA, bg: RGBA): number {
  const f = fg[3] < 1 ? composite(fg, bg) : fg;
  const [hi, lo] = [luminance(f), luminance(bg)].sort((a, b) => b - a);
  return (hi + 0.05) / (lo + 0.05);
}

/** Variabili `--nome: valore;` del primo blocco con quel selettore esatto (es. `:root`, `:root[data-theme="light"]`). */
export function cssTokens(css: string, selector: string): Record<string, string> {
  const escaped = selector.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  // il blocco inizia a capo o dopo un'altra regola o un commento
  const block = new RegExp(`(?:^|\\}|\\*/)\\s*${escaped}\\s*\\{([^}]*)\\}`).exec(css);
  const out: Record<string, string> = {};
  if (!block) return out;
  for (const m of block[1].matchAll(/--([\w-]+)\s*:\s*([^;]+);/g)) out[m[1]] = m[2].trim();
  return out;
}
