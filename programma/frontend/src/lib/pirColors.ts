// Colorazioni della griglia dell'Editor PIR oltre alla classe chimica: identità con il bersaglio, conservazione della
// colonna, idrofobicità (Kyte & Doolittle 1982). Logica pura e testata (pirColors.test.ts).

export type ColorMode = "class" | "identity" | "conservation" | "hydro" | "none";

export const COLOR_MODES: { value: ColorMode; label: string }[] = [
  { value: "class", label: "Classe" },
  { value: "identity", label: "Identità" },
  { value: "conservation", label: "Conservazione" },
  { value: "hydro", label: "Idrofobicità" },
  { value: "none", label: "Nessuno" },
];

/** Scala di idropatia di Kyte & Doolittle (1982). */
export const KYTE_DOOLITTLE: Record<string, number> = {
  I: 4.5, V: 4.2, L: 3.8, F: 2.8, C: 2.5, M: 1.9, A: 1.8, G: -0.4, T: -0.7, S: -0.8, W: -0.9, Y: -1.3, P: -1.6,
  H: -3.2, E: -3.5, Q: -3.5, D: -3.5, N: -3.5, K: -3.9, R: -4.5,
};

/** Idrofobico (≥ 1,5), idrofilo (≤ −1,5) o intermedio; null per gap e lettere non standard. */
export function hydroClass(aa: string): "phobic" | "philic" | "mid" | null {
  const v = KYTE_DOOLITTLE[aa.toUpperCase()];
  if (v === undefined) return null;
  return v >= 1.5 ? "phobic" : v <= -1.5 ? "philic" : "mid";
}

const isLetter = (c: string | undefined) => !!c && /[A-Za-z]/.test(c);

/**
 * Conservazione di ogni colonna: frazione delle voci con il residuo più frequente della colonna (gap e voci più corte
 * contano come diverse). 1 = colonna identica in tutte le voci; 0 = nessun residuo.
 */
export function columnConservation(rows: string[]): number[] {
  const width = Math.max(0, ...rows.map((s) => s.length));
  const out: number[] = [];
  for (let c = 0; c < width; c++) {
    const count: Record<string, number> = {};
    let best = 0;
    for (const s of rows) {
      const ch = s[c];
      if (!isLetter(ch)) continue;
      const k = ch.toUpperCase();
      count[k] = (count[k] ?? 0) + 1;
      best = Math.max(best, count[k]);
    }
    out.push(rows.length ? best / rows.length : 0);
  }
  return out;
}

/** Livello di conservazione per la tinta di fondo: 3 ≥ 90%, 2 ≥ 70%, 1 ≥ 50%, 0 sotto. */
export const conservationLevel = (v: number) => (v >= 0.9 ? 3 : v >= 0.7 ? 2 : v >= 0.5 ? 1 : 0);
