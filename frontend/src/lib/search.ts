import type { SearchGroup, SearchGroupType, SearchItem } from "../api/types";
import { normalizeTag } from "./tags";

/**
 * Ricerca globale (barra nel Layout, Ctrl+K / ⌘K): lettura di ciò che si scrive, elenco piatto dei risultati per la
 * navigazione da tastiera, destinazione di ogni risultato, parti evidenziate.
 *
 * Sintassi: le parole si cercano nei nomi (ognuna come inizio di una parola, come nel backend); `#etichetta` cerca
 * solo fra gli elementi con quell'etichetta (da sola li elenca tutti); `tipo:job` (o `in:`) limita ai gruppi
 * indicati, anche più di uno (`tipo:job,template`). Senza testo né etichetta il server restituisce i preferiti.
 */
export interface ParsedQuery {
  /** testo libero, spazi normalizzati */
  text: string;
  /** etichetta normalizzata (`#…`), null senza */
  tag: string | null;
  /** gruppi richiesti (`tipo:`), vuoto = tutti */
  types: SearchGroupType[];
  /** parti non riconosciute (`#` non valida, `tipo:` sconosciuto), mostrate come avviso */
  invalid: string[];
}

const TYPE_ALIASES: Record<string, SearchGroupType> = {
  progetto: "project", progetti: "project", project: "project", projects: "project",
  sequenza: "sequence", sequenze: "sequence", seq: "sequence", sequence: "sequence",
  documento: "document", documenti: "document", doc: "document", document: "document", pir: "document",
  job: "job", jobs: "job", calcolo: "job", calcoli: "job",
  template: "template", templates: "template", libreria: "template",
  etichetta: "tag", etichette: "tag", tag: "tag", tags: "tag",
};

const TYPE_PREFIX = /^(tipo|in|type):(.*)$/i;

/** Nome italiano di ogni gruppo nei filtri (`tipo:sequenze`): il badge del filtro e il testo scritto nella barra. Sono
 *  anche alias di `TYPE_ALIASES`, così il testo si rilegge uguale. */
export const TYPE_LABEL: Record<SearchGroupType, string> = {
  project: "progetti", sequence: "sequenze", document: "documenti", job: "job", template: "template", tag: "etichette",
};

export function parseSearchQuery(input: string): ParsedQuery {
  const words: string[] = [];
  const types: SearchGroupType[] = [];
  const invalid: string[] = [];
  let tag: string | null = null;
  for (const part of (input ?? "").trim().split(/\s+/).filter(Boolean)) {
    if (part.startsWith("#") && part.length > 1) {
      const check = normalizeTag(part);
      if (check.ok && tag === null) tag = check.tag;
      else if (!check.ok) invalid.push(part);
      else words.push(part); // seconda etichetta: resta testo
      continue;
    }
    const m = TYPE_PREFIX.exec(part);
    if (m) {
      const names = m[2].split(",").map((s) => s.trim().toLowerCase()).filter(Boolean);
      const known = names.map((n) => TYPE_ALIASES[n]).filter((t): t is SearchGroupType => !!t);
      if (!names.length || known.length !== names.length) invalid.push(part);
      for (const t of known) if (!types.includes(t)) types.push(t);
      continue;
    }
    words.push(part);
  }
  return { text: words.join(" "), tag, types, invalid };
}

/** Chiave della richiesta: due testi che danno la stessa richiesta al server non la ripetono. */
export function queryKey(q: ParsedQuery): string {
  return JSON.stringify([q.text.toLowerCase(), q.tag, [...q.types].sort()]);
}

export interface FlatResult {
  group: SearchGroup;
  item: SearchItem;
  /** posizione nell'elenco piatto (navigazione con le frecce) */
  index: number;
}

/** Risultati nell'ordine in cui compaiono (gruppi, poi voci). */
export function flattenResults(groups: SearchGroup[] | null | undefined): FlatResult[] {
  const out: FlatResult[] = [];
  for (const group of groups ?? []) for (const item of group.items) out.push({ group, item, index: out.length });
  return out;
}

/** Voce attiva dopo ↑/↓ (con giro), Home/End o PagSu/PagGiù; -1 senza risultati. */
export function moveSelection(current: number, key: string, count: number, page = 5): number {
  if (count <= 0) return -1;
  const at = current < 0 || current >= count ? -1 : current;
  switch (key) {
    case "ArrowDown":
      return at < 0 ? 0 : (at + 1) % count;
    case "ArrowUp":
      return at < 0 ? count - 1 : (at - 1 + count) % count;
    case "Home":
      return 0;
    case "End":
      return count - 1;
    case "PageDown":
      return Math.min(count - 1, Math.max(0, at) + page);
    case "PageUp":
      return Math.max(0, at - page);
    default:
      return at;
  }
}

/**
 * Dove porta un risultato: pagina e progetto da attivare prima (sequenze, documenti e job stanno in un progetto). Un
 * progetto si apre scegliendolo come progetto attivo e andando alla Panoramica. Un'etichetta non porta a una pagina:
 * la ricerca diventa `#etichetta` (null qui).
 */
export function resultTarget(item: SearchItem): { path: string; projectId: string | null } | null {
  const id = encodeURIComponent(item.id);
  switch (item.type) {
    case "project":
      return { path: "/", projectId: item.id };
    case "sequence":
      return { path: `/sequences?id=${id}`, projectId: item.project_id ?? null };
    case "document":
      return { path: `/pir?doc=${id}`, projectId: item.project_id ?? null };
    case "job":
      return { path: `/jobs?id=${id}`, projectId: null }; // la pagina Job mostra il job anche di un altro progetto
    case "template":
      return { path: `/templates?id=${id}`, projectId: null };
    default:
      return null;
  }
}

/** Testo della barra dopo la scelta di un'etichetta: `#etichetta` più i filtri di tipo già scritti (nomi italiani). */
export function queryForTag(tag: string, current: ParsedQuery): string {
  const types = current.types.length ? ` tipo:${current.types.map((t) => TYPE_LABEL[t]).join(",")}` : "";
  return `#${tag}${types} `;
}

/**
 * Cosa fa Invio. I risultati mostrati restano quelli della richiesta precedente finché non arriva la risposta per il
 * testo attuale (attesa di 150 ms più la richiesta): Invio allora non apre una voce vecchia ma aspetta ("wait") e apre
 * la prima voce della risposta giusta. Con i risultati aggiornati apre la voce attiva ("open"); senza voci, nulla.
 */
export function enterAction(resultKey: string | null, key: string, count: number, active: number): { kind: "open"; index: number } | { kind: "wait" } | { kind: "none" } {
  if (resultKey !== key) return { kind: "wait" };
  if (count <= 0) return { kind: "none" };
  return { kind: "open", index: active >= 0 && active < count ? active : 0 };
}

const fold = (c: string) => (c.normalize("NFD").replace(/\p{M}/gu, "").toLowerCase()[0] ?? c);
const isWordChar = (c: string) => /[\p{L}\p{N}]/u.test(c);

/**
 * Parti di un titolo da evidenziare: l'inizio delle parole che corrisponde a una parola della ricerca (stessa regola
 * del backend: minuscole, senza accenti, parole fatte di lettere e cifre).
 */
export function highlightParts(text: string, query: string): { text: string; match: boolean }[] {
  const terms = (query ?? "").split(/[^\p{L}\p{N}]+/u).map((t) => [...t].map(fold).join("")).filter(Boolean);
  const chars = [...(text ?? "")];
  if (!terms.length || !chars.length) return chars.length ? [{ text: chars.join(""), match: false }] : [];
  const folded = chars.map(fold);
  const marks = new Array<boolean>(chars.length).fill(false);
  for (let i = 0; i < chars.length; i++) {
    if (!isWordChar(chars[i]) || (i > 0 && isWordChar(chars[i - 1]))) continue; // solo inizi di parola
    for (const term of terms) {
      const t = [...term];
      if (t.every((c, k) => folded[i + k] === c && isWordChar(chars[i + k]))) {
        for (let k = 0; k < t.length; k++) marks[i + k] = true;
      }
    }
  }
  const parts: { text: string; match: boolean }[] = [];
  for (let i = 0; i < chars.length; i++) {
    const last = parts[parts.length - 1];
    if (last && last.match === marks[i]) last.text += chars[i];
    else parts.push({ text: chars[i], match: marks[i] });
  }
  return parts;
}
