export interface PirEntry {
  code: string;
  kind: string;
  header: string;
  sequence: string;
}

/**
 * Posizione di una voce nel testo PIR, per riga (da 0): riga del codice `>P1;`, seconda riga (−1 se manca), righe della
 * sequenza [seqStart, seqEnd) fino alla voce successiva, riga con l'`*` finale (−1 se manca). L'Editor PIR la usa per
 * sostituire solo le righe della sequenza (lib/pirEdit rewritePir) e lasciare il resto del testo com'è.
 */
export interface PirEntryLayout {
  codeLine: number;
  headerLine: number;
  seqStart: number;
  seqEnd: number;
  starLine: number;
}

const isCodeLine = (line: string | undefined) => !!line && line.trim().startsWith(">P1;");

/** Righe del testo e posizione di ogni voce (stessa lettura di parsePir). */
export function pirLayout(text: string): { lines: string[]; layout: PirEntryLayout[] } {
  const lines = text.split(/\r?\n/);
  const layout: PirEntryLayout[] = [];
  let i = 0;
  while (i < lines.length) {
    if (!isCodeLine(lines[i])) {
      i++;
      continue;
    }
    const codeLine = i;
    i++;
    while (i < lines.length && !lines[i].trim()) i++;
    // seconda riga: la prima non vuota dopo il codice, se non è già la voce successiva
    const headerLine = i < lines.length && !isCodeLine(lines[i]) ? i : -1;
    if (headerLine >= 0) i++;
    const seqStart = i;
    let starLine = -1;
    while (i < lines.length && !isCodeLine(lines[i])) {
      if (starLine < 0 && lines[i].includes("*")) starLine = i;
      i++;
    }
    layout.push({ codeLine, headerLine, seqStart, seqEnd: i, starLine });
  }
  return { lines, layout };
}

/** Sequenza grezza della voce (maiuscole e minuscole come nel testo, senza spazi), fino all'`*`. */
export function rawSequence(lines: string[], e: PirEntryLayout): string {
  const end = e.starLine >= 0 ? e.starLine + 1 : e.seqEnd;
  return lines.slice(e.seqStart, end).join("").replace(/\s/g, "").split("*")[0];
}

/** Parser PIR minimale per l'anteprima e l'editor (la validazione autorevole è lato server). */
export function parsePir(text: string): PirEntry[] {
  const { lines, layout } = pirLayout(text);
  return layout.map((e) => {
    const header = e.headerLine >= 0 ? lines[e.headerLine].trim() : "";
    return {
      code: lines[e.codeLine].trim().slice(4).trim(),
      kind: header.split(":")[0] || "sequence",
      header,
      sequence: rawSequence(lines, e).toUpperCase(),
    };
  });
}

/** Identità tra bersaglio e ciascun template (sulle colonne senza gap). */
export function pirIdentities(entries: PirEntry[]): { code: string; identity: number; coverage: number }[] {
  const target = entries.find((e) => e.kind === "sequence");
  if (!target) return [];
  const tLen = target.sequence.replace(/[^A-Z]/g, "").length || 1;
  return entries
    .filter((e) => e.kind.startsWith("structure"))
    .map((e) => {
      let same = 0;
      let pairs = 0;
      for (let k = 0; k < Math.min(target.sequence.length, e.sequence.length); k++) {
        const a = target.sequence[k];
        const b = e.sequence[k];
        if (/[A-Z]/.test(a) && /[A-Z]/.test(b)) {
          pairs++;
          if (a === b) same++;
        }
      }
      return { code: e.code, identity: pairs ? (100 * same) / pairs : 0, coverage: (100 * pairs) / tLen };
    });
}
