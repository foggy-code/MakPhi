// Caricamento da file lato interfaccia: decodifica dei file di testo e interpretazione di formati semplici.
// Le sequenze e gli allineamenti in formati complessi (GenBank, Clustal, PDB...) li legge il server
// (api.parseSequenceFile); qui restano i testi da mettere direttamente in un campo.

export const MAX_TEXT_FILE = 10 * 1024 * 1024;

/** Byte → testo: BOM UTF-8/UTF-16, altrimenti UTF-8 (o Windows-1252 se non valido); fine riga Unix. */
export function decodeText(bytes: Uint8Array): string {
  let text: string;
  if (bytes[0] === 0xef && bytes[1] === 0xbb && bytes[2] === 0xbf) text = new TextDecoder("utf-8").decode(bytes.subarray(3));
  else if (bytes[0] === 0xff && bytes[1] === 0xfe) text = new TextDecoder("utf-16le").decode(bytes.subarray(2));
  else if (bytes[0] === 0xfe && bytes[1] === 0xff) text = new TextDecoder("utf-16be").decode(bytes.subarray(2));
  else {
    try {
      text = new TextDecoder("utf-8", { fatal: true }).decode(bytes);
    } catch {
      text = new TextDecoder("windows-1252").decode(bytes);
    }
  }
  if (text.includes("\u0000")) throw new Error("Il file sembra binario: serve un file di testo.");
  return text.replace(/\r\n?/g, "\n");
}

export async function readTextFile(file: File): Promise<string> {
  if (file.size > MAX_TEXT_FILE) throw new Error(`File troppo grande (massimo ${MAX_TEXT_FILE / 1024 / 1024} MB).`);
  return decodeText(new Uint8Array(await file.arrayBuffer()));
}

export function fileStem(name: string): string {
  return name.replace(/\.(gz)$/i, "").replace(/\.[^.]+$/, "").replace(/[^A-Za-z0-9_-]+/g, "_").slice(0, 60) || "file";
}

/**
 * Chiave MODELLER da un file: config.py di MODELLER (`license = r'...'`), riga `MODELLER_KEY=...`,
 * email di registrazione o file con la sola chiave.
 */
export function extractModellerKey(text: string): string | null {
  const patterns = [
    /license\s*=\s*r?['"]([A-Za-z0-9_-]{4,64})['"]/i,
    /(?:MODELLER_KEY|KEY_MODELLER)\s*[=:]\s*['"]?([A-Za-z0-9_-]{4,64})/,
    /(?:licen[sc]e key|chiave(?: di licenza)?)[^A-Za-z0-9]{1,20}([A-Za-z0-9_-]{6,64})/i,
    /\b(MODEL[A-Z0-9]{4,40})\b/,
  ];
  for (const p of patterns) {
    const m = text.match(p);
    if (m && m[1] !== "XXXX") return m[1];
  }
  const tokens = text.trim().split(/\s+/);
  return tokens.length === 1 && /^[A-Za-z0-9_-]{4,64}$/.test(tokens[0]) ? tokens[0] : null;
}

export type RestraintType = "helix" | "strand" | "distance";
export interface RestraintRow {
  type: RestraintType;
  start?: string;
  end?: string;
  atom1?: string;
  atom2?: string;
  mean?: number;
  stdev?: number;
}

const TYPE_ALIASES: Record<string, RestraintType> = {
  helix: "helix", elica: "helix", alpha: "helix", h: "helix",
  strand: "strand", foglietto: "strand", sheet: "strand", beta: "strand", e: "strand",
  distance: "distance", distanza: "distance", dist: "distance", d: "distance",
};

/**
 * Vincoli da file di testo, una riga per vincolo (separatori: spazi, tab, virgole o punti e virgola):
 *   helix 10:A 20:A          strand 30:A 35:A          distance CA:10:A CA:50:A 8.0 0.5
 * Accetta anche i nomi italiani (elica, foglietto, distanza); righe vuote e commenti (#) ignorati.
 */
export function parseRestraints(text: string): { restraints: RestraintRow[]; errors: string[] } {
  const restraints: RestraintRow[] = [];
  const errors: string[] = [];
  text.split("\n").forEach((raw, i) => {
    const line = raw.replace(/#.*/, "").trim();
    if (!line) return;
    const parts = line.split(/[\s,;]+/).filter(Boolean);
    const type = TYPE_ALIASES[parts[0].toLowerCase()];
    if (!type) {
      if (i === 0 && /tipo|type/i.test(parts[0])) return; // riga di intestazione di un CSV
      errors.push(`Riga ${i + 1}: tipo "${parts[0]}" sconosciuto (usa helix, strand o distance).`);
      return;
    }
    if (type === "distance") {
      const mean = Number(parts[3]);
      const stdev = parts[4] !== undefined ? Number(parts[4]) : 0.5;
      if (parts.length < 4 || !Number.isFinite(mean) || !Number.isFinite(stdev)) {
        errors.push(`Riga ${i + 1}: serve "distance ATOMO:RES:CATENA ATOMO:RES:CATENA distanza [deviazione]".`);
        return;
      }
      restraints.push({ type, atom1: parts[1].toUpperCase(), atom2: parts[2].toUpperCase(), mean, stdev });
    } else {
      if (parts.length < 3) {
        errors.push(`Riga ${i + 1}: serve "${type} INIZIO FINE" (es. ${type} 10:A 20:A).`);
        return;
      }
      restraints.push({ type, start: parts[1].toUpperCase(), end: parts[2].toUpperCase() });
    }
  });
  return { restraints, errors };
}

export function formatRestraints(rows: RestraintRow[]): string {
  const lines = ["# Vincoli MakPhi: tipo inizio fine  |  distance atomo1 atomo2 distanza deviazione"];
  for (const r of rows) {
    if (r.type === "distance") lines.push(`distance ${r.atom1 ?? ""} ${r.atom2 ?? ""} ${r.mean ?? ""} ${r.stdev ?? 0.5}`);
    else lines.push(`${r.type} ${r.start ?? ""} ${r.end ?? ""}`);
  }
  return lines.join("\n") + "\n";
}

/** Record letti da file → multi-FASTA (senza gap: le sequenze verranno riallineate). */
export function recordsToFasta(records: { name: string; sequence: string }[]): string {
  const used = new Map<string, number>();
  return records
    .map((r) => {
      let name = (r.name || "seq").replace(/\s+/g, "_");
      const n = (used.get(name) ?? 0) + 1;
      used.set(name, n);
      if (n > 1) name = `${name}_${n}`;
      const lines = r.sequence.match(/.{1,60}/g) ?? [];
      return `>${name}\n${lines.join("\n")}`;
    })
    .join("\n") + "\n";
}
