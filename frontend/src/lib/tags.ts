import type { EntityType, TagCount } from "../api/types";

/**
 * Etichette: la stessa normalizzazione del backend (`domain/labels.py`), per mostrare subito l'etichetta che verrà
 * salvata e l'errore prima di inviarla. NFC, minuscole, spazi esterni e `#` iniziale tolti, spazi interni → `-`,
 * trattini ripetuti uniti, `- _ . :` tolti ai bordi; lettere, cifre e `- _ . : +`, inizio con lettera o cifra; al più
 * 32 caratteri (oltre: errore, mai troncata).
 */
export const TAG_MAX_LENGTH = 32;
export const MAX_TAGS_PER_ENTITY = 20;

const VALID = /^[\p{L}\p{N}][\p{L}\p{N}_.:+-]*$/u;
// i segni combinanti (\p{M}) non sono lettere: in NFC quasi sempre spariscono dentro la lettera accentata; uno che
// resta (combinazione senza forma composta) rende l'etichetta non valida, come nel backend (`\w` non li comprende)

export type TagCheck = { ok: true; tag: string } | { ok: false; error: string };

export function normalizeTag(raw: string): TagCheck {
  let text = (raw ?? "").normalize("NFC").trim().toLowerCase();
  text = text.replace(/^#+/, "").trim();
  text = text.replace(/\s+/g, "-").replace(/-{2,}/g, "-").replace(/^[-_.:]+|[-_.:]+$/g, "");
  if (!text) return { ok: false, error: "Etichetta vuota." };
  if ([...text].length > TAG_MAX_LENGTH) return { ok: false, error: `Etichetta troppo lunga: al massimo ${TAG_MAX_LENGTH} caratteri.` };
  if (!VALID.test(text)) return { ok: false, error: "Etichetta non valida: usa lettere, cifre e i caratteri - _ . : +" };
  return { ok: true, tag: text };
}

/** Valore del filtro degli elenchi: tutti, solo i preferiti oppure un'etichetta. */
export type LabelFilter = { kind: "all" } | { kind: "favorites" } | { kind: "tag"; tag: string };

export const FILTER_ALL: LabelFilter = { kind: "all" };
const FAVORITES_VALUE = "*preferiti"; // un'etichetta non può cominciare con "*": nessuna ambiguità

export function filterValue(f: LabelFilter): string {
  return f.kind === "all" ? "" : f.kind === "favorites" ? FAVORITES_VALUE : f.tag;
}

export function parseFilterValue(value: string | null | undefined): LabelFilter {
  if (!value) return FILTER_ALL;
  if (value === FAVORITES_VALUE) return { kind: "favorites" };
  const check = normalizeTag(value);
  return check.ok ? { kind: "tag", tag: check.tag } : FILTER_ALL;
}

/** Parametri delle rotte degli elenchi (`?tag=` e `?favorite=true`). */
export function filterParams(f: LabelFilter): { tag?: string; favorite?: boolean } {
  return f.kind === "tag" ? { tag: f.tag } : f.kind === "favorites" ? { favorite: true } : {};
}

/** Un elemento passa il filtro (filtro lato client, es. dopo una modifica fatta senza ricaricare). */
export function matchesFilter(item: { tags?: string[]; favorite?: boolean }, f: LabelFilter): boolean {
  if (f.kind === "favorites") return !!item.favorite;
  if (f.kind === "tag") return (item.tags ?? []).includes(f.tag);
  return true;
}

/** Opzioni del filtro per un tipo di elemento: le etichette usate da quel tipo, le più usate prima. */
export function filterOptions(counts: TagCount[] | null | undefined, type: EntityType): { value: string; label: string }[] {
  const tags = (counts ?? [])
    .map((c) => ({ tag: c.tag, n: c.types[type] ?? 0 }))
    .filter((c) => c.n > 0)
    .sort((a, b) => b.n - a.n || a.tag.localeCompare(b.tag));
  return [
    { value: "", label: "Tutti" },
    { value: FAVORITES_VALUE, label: "★ Preferiti" },
    ...tags.map((t) => ({ value: t.tag, label: `#${t.tag} (${t.n})` })),
  ];
}

/** Elementi con la stella prima, nell'ordine di partenza per il resto. */
export function favoritesFirst<T extends { favorite?: boolean }>(items: T[]): T[] {
  return [...items.filter((i) => i.favorite), ...items.filter((i) => !i.favorite)];
}

export type ItemLabels = { tags: string[]; favorite: boolean };

/** Stella ed etichette di un elemento di un elenco, per il dettaglio (`LabelEditor initial`): l'elenco è la fonte dei
 *  dati, così una stella cambiata nella riga si vede anche nel dettaglio. undefined se l'elemento non è nell'elenco
 *  (filtrato o oltre il limite): il dettaglio allora le legge dal server. */
export function itemLabels<T extends { tags?: string[]; favorite?: boolean }>(item: T | null | undefined): ItemLabels | undefined {
  return item ? { tags: item.tags ?? [], favorite: !!item.favorite } : undefined;
}

/** Chiave di confronto di stella ed etichette: cambia solo quando cambiano i valori (non l'oggetto). */
export function labelsKey(labels: ItemLabels | null | undefined): string | null {
  return labels ? `${labels.favorite ? 1 : 0}|${labels.tags.join("\u0001")}` : null;
}
