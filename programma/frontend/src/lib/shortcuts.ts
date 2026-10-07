/**
 * Scorciatoie da tastiera globali (gestite nel Layout, elencate nel pannello "?").
 *
 * - Ctrl+K / ⌘K: ricerca globale, anche dentro un campo di testo (come negli editor e nei browser);
 * - `/`: ricerca globale, fuori dai campi di testo;
 * - `?`: pannello delle scorciatoie;
 * - `g` e poi una lettera (entro 1,5 s): vai a una pagina principale (`g s` Sequenze, `g j` Job…);
 * - Esc: chiude il dialogo aperto (la gestisce ogni dialogo).
 *
 * Mentre si scrive in un campo (input di testo, textarea, select, contenuto modificabile) valgono solo Ctrl+K / ⌘K:
 * `/`, `?` e `g` sono caratteri da scrivere. Con un dialogo aperto non si cambia pagina sotto di lui.
 */

export const PENDING_MS = 1500;

export interface NavShortcut {
  key: string;
  path: string;
  label: string;
}

export const NAV_SHORTCUTS: NavShortcut[] = [
  { key: "h", path: "/", label: "Panoramica" },
  { key: "s", path: "/sequences", label: "Sequenze" },
  { key: "r", path: "/search", label: "Ricerca template" },
  { key: "t", path: "/templates", label: "Libreria template" },
  { key: "a", path: "/alignment", label: "Allineamenti" },
  { key: "e", path: "/pir", label: "Editor PIR" },
  { key: "m", path: "/modeling", label: "Modellazione" },
  { key: "q", path: "/quality", label: "Qualità" },
  { key: "c", path: "/compare", label: "Confronto" },
  { key: "p", path: "/pipeline", label: "Pipeline batch" },
  { key: "j", path: "/jobs", label: "Job" },
  { key: "i", path: "/settings", label: "Impostazioni" },
];

/** Righe del pannello "?": tasti (per la piattaforma) e descrizione. */
export function shortcutHelp(mac: boolean): { keys: string[]; label: string }[] {
  const mod = mac ? "⌘" : "Ctrl";
  return [
    { keys: [`${mod} K`, "/"], label: "Ricerca globale (progetti, sequenze, documenti, job, template, etichette)" },
    { keys: ["↑", "↓", "Invio"], label: "Nella ricerca: scegli un risultato e aprilo" },
    { keys: ["#etichetta", "tipo:job"], label: "Nella ricerca: solo gli elementi con un'etichetta, solo un tipo" },
    { keys: ["?"], label: "Questo pannello" },
    { keys: ["Esc"], label: "Chiude il dialogo o la ricerca" },
    ...NAV_SHORTCUTS.map((n) => ({ keys: ["g", n.key], label: `Vai a ${n.label}` })),
  ];
}

export function isMacPlatform(nav: { platform?: string; userAgent?: string } | null | undefined): boolean {
  const text = `${nav?.platform ?? ""} ${nav?.userAgent ?? ""}`;
  return /Mac|iPhone|iPad|iPod/i.test(text);
}

/** Ciò che serve di un elemento per capire se si sta scrivendo (un HTMLElement nel browser). */
export interface TargetLike {
  tagName?: string;
  type?: string;
  isContentEditable?: boolean;
}

const NOT_TYPING_INPUTS = new Set(["button", "checkbox", "radio", "range", "color", "file", "submit", "reset", "image"]);

export function isEditableTarget(target: TargetLike | null | undefined): boolean {
  if (!target) return false;
  if (target.isContentEditable) return true;
  const tag = (target.tagName ?? "").toUpperCase();
  if (tag === "TEXTAREA" || tag === "SELECT") return true;
  if (tag === "INPUT") return !NOT_TYPING_INPUTS.has((target.type ?? "text").toLowerCase());
  return false;
}

export interface KeyLike {
  key: string;
  ctrlKey?: boolean;
  metaKey?: boolean;
  altKey?: boolean;
  shiftKey?: boolean;
  repeat?: boolean;
  target?: TargetLike | null;
}

export type ShortcutAction =
  | { type: "search" }
  | { type: "help" }
  | { type: "pending" }
  | { type: "navigate"; path: string }
  | null;

export interface ShortcutState {
  /** è stato premuto `g` da meno di `PENDING_MS` */
  pending: boolean;
  /** c'è un dialogo aperto (role="dialog") */
  dialogOpen: boolean;
  mac: boolean;
}

/** Azione di un tasto premuto, null = lasciarlo alla pagina. */
export function shortcutFor(e: KeyLike, state: ShortcutState): ShortcutAction {
  const mod = state.mac ? e.metaKey && !e.ctrlKey : e.ctrlKey && !e.metaKey;
  if (mod && !e.altKey && !e.shiftKey && e.key.toLowerCase() === "k") return { type: "search" };
  if (e.ctrlKey || e.metaKey || e.altKey || e.repeat) return null;
  if (isEditableTarget(e.target) || state.dialogOpen) return null;
  if (state.pending) {
    const nav = NAV_SHORTCUTS.find((n) => n.key === e.key.toLowerCase());
    return nav ? { type: "navigate", path: nav.path } : null;
  }
  if (e.key === "/") return { type: "search" };
  if (e.key === "?") return { type: "help" };
  if (e.key === "g" || e.key === "G") return e.shiftKey ? null : { type: "pending" };
  return null;
}

/** Tasti della griglia degli allineamenti (Editor PIR e Allineamenti), per il pannello "?". */
export function gridHelp(mac: boolean): { keys: string[]; label: string }[] {
  const mod = mac ? "⌘" : "Ctrl";
  return [
    { keys: ["← → ↑ ↓", `${mod} ← →`], label: "Muovi il cursore (di 10 colonne con il modificatore); Inizio / Fine: capo della riga" },
    { keys: ["Maiusc + frecce"], label: "Seleziona un blocco (anche trascinando; clic sul nome: tutta la riga)" },
    { keys: ["Spazio", "-"], label: "Inserisce un gap prima della selezione, nelle righe selezionate" },
    { keys: ["Canc", "Backspace"], label: "Toglie il gap nella colonna / prima della colonna" },
    { keys: ["Alt ←", "Alt →"], label: "Sposta il blocco selezionato; un blocco di soli gap scavalca i residui" },
    { keys: ["A…Y"], label: "Sostituisce il residuo sotto il cursore (template bloccati)" },
    { keys: ["Invio"], label: "Tavolozza delle sostituzioni con BLOSUM62 (anche doppio clic)" },
    { keys: ["F8", "Maiusc F8"], label: "Controllo successivo / precedente" },
    { keys: [`${mod} Z`, `${mod} Y`], label: "Annulla / ripristina" },
    { keys: [`${mod} A`], label: "Seleziona tutto" },
    { keys: ["Esc"], label: "Chiude la tavolozza, riduce la selezione, esce dallo schermo intero" },
  ];
}
