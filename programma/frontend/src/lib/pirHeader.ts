// Seconda riga di una voce PIR (MODELLER): "tipo:codice:inizio:catena:fine:catena:nome:fonte:risoluzione:fattore R".
// Lettura, controllo e scrittura per il modulo dell'Editor PIR. Logica pura e testata (pirHeader.test.ts).

import { pirLayout } from "./pir";

export const HEADER_KINDS = ["sequence", "structureX", "structureN", "structureM"] as const;

export interface PirHeader {
  kind: string;
  /** codice del file della struttura (per i template; MODELLER apre <codice>.pdb) */
  file: string;
  startRes: string;
  startChain: string;
  endRes: string;
  endChain: string;
  name: string;
  source: string;
  resolution: string;
  rFactor: string;
}

export const HEADER_FIELDS: (keyof PirHeader)[] = ["kind", "file", "startRes", "startChain", "endRes", "endChain", "name", "source", "resolution", "rFactor"];

export function parseHeader(line: string): PirHeader {
  const f = line.split(":");
  const out = {} as PirHeader;
  HEADER_FIELDS.forEach((k, i) => (out[k] = (f[i] ?? "").trim()));
  // i campi oltre il decimo (raro) restano nell'ultimo, per non perderli
  if (f.length > HEADER_FIELDS.length) out.rFactor = f.slice(HEADER_FIELDS.length - 1).join(":").trim();
  return out;
}

export function formatHeader(h: PirHeader): string {
  return HEADER_FIELDS.map((k) => h[k].trim()).join(":");
}

// residuo: numero con codice d'inserzione, +N (N residui dal primo, come in MODELLER), FIRST, LAST, "." o "@"
const RES = /^(?:-?\d+[A-Za-z]?|\+\d+|FIRST|LAST|\.|@)?$/i;
/** tipi letti da MODELLER e da MakPhi: anche "structure" senza lettera */
const KINDS = new Set<string>([...HEADER_KINDS, "structure"]);
const CHAIN = /^[A-Za-z0-9@.]?$/;
const NUM = /^(?:-?\d+(?:\.\d+)?)?$/;
const CODE = /^[A-Za-z0-9_.-]+$/;

/** Problemi del modulo (vuoto se va bene), per campo. */
export function headerErrors(code: string, h: PirHeader): Partial<Record<keyof PirHeader | "code", string>> {
  const e: Partial<Record<keyof PirHeader | "code", string>> = {};
  if (!CODE.test(code.trim())) e.code = "Solo lettere, cifre, _ . - e niente spazi.";
  if (!KINDS.has(h.kind)) e.kind = "Tipo non valido.";
  for (const k of HEADER_FIELDS) if (h[k].includes(":")) e[k] = "Senza «:» (separa i campi).";
  if (h.kind.startsWith("structure") && h.file.trim() && !CODE.test(h.file.trim())) e.file = "Solo lettere, cifre, _ . - e niente spazi.";
  if (!e.startRes && !RES.test(h.startRes.trim())) e.startRes = "Numero del residuo (es. 5 o 52A), FIRST o vuoto.";
  if (!e.endRes && !RES.test(h.endRes.trim())) e.endRes = "Numero del residuo (es. 120), LAST o vuoto.";
  if (!e.startChain && !CHAIN.test(h.startChain.trim())) e.startChain = "Un carattere (es. A) o vuoto.";
  if (!e.endChain && !CHAIN.test(h.endChain.trim())) e.endChain = "Un carattere (es. A) o vuoto.";
  if (!e.resolution && !NUM.test(h.resolution.trim())) e.resolution = "Un numero (es. 1.80, −1.00 se non nota) o vuoto.";
  if (!e.rFactor && !NUM.test(h.rFactor.trim())) e.rFactor = "Un numero (es. 0.19) o vuoto.";
  return e;
}

/**
 * Testo PIR con codice e seconda riga della voce `index` sostituiti; il resto del testo resta com'è. Una voce senza
 * seconda riga la riceve subito dopo il codice.
 */
export function setEntryHeader(text: string, index: number, code: string, header: string): string {
  const { lines, layout } = pirLayout(text);
  const e = layout[index];
  if (!e) return text;
  const eol = text.includes("\r\n") ? "\r\n" : "\n";
  if (e.headerLine >= 0) lines[e.headerLine] = header;
  else lines.splice(e.codeLine + 1, 0, header);
  lines[e.codeLine] = `>P1;${code.trim()}`;
  return lines.join(eol);
}
