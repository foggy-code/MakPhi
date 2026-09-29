export interface PirEntry {
  code: string;
  kind: string;
  header: string;
  sequence: string;
}

/** Parser PIR minimale per l'anteprima (la validazione autorevole è lato server). */
export function parsePir(text: string): PirEntry[] {
  const out: PirEntry[] = [];
  const lines = text.split(/\r?\n/);
  let i = 0;
  while (i < lines.length) {
    const line = lines[i].trim();
    if (!line.startsWith(">P1;")) {
      i++;
      continue;
    }
    const code = line.slice(4).trim();
    i++;
    while (i < lines.length && !lines[i].trim()) i++;
    const header = (lines[i] ?? "").trim();
    i++;
    const parts: string[] = [];
    while (i < lines.length && !lines[i].trim().startsWith(">P1;")) {
      parts.push(lines[i].trim());
      i++;
    }
    const seq = parts.join("").replace(/\s/g, "").split("*")[0].toUpperCase();
    out.push({ code, kind: header.split(":")[0] || "sequence", header, sequence: seq });
  }
  return out;
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
