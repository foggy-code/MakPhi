import { useEffect, useState } from "react";

/**
 * Tema dell'interfaccia: scelta dell'utente (`ThemePref`) e tema effettivo (`Theme`).
 *
 * "Sistema" segue `prefers-color-scheme` del sistema operativo e cambia insieme a lui (evento `change` della media
 * query). La scelta resta in localStorage con la chiave di sempre (`bioloom.theme`, valore JSON come
 * `useLocalStorage`): i valori salvati dalle versioni precedenti ("dark", "light") restano validi, uno sconosciuto o
 * illeggibile vale il predefinito. Il tema effettivo va su `<html data-theme>`, che sceglie le variabili CSS.
 */
export type Theme = "dark" | "light";
export type ThemePref = Theme | "system";

export const THEME_KEY = "bioloom.theme";
/** Predefinito senza una scelta salvata: il tema scuro di sempre (chi non ha mai scelto non vede cambiare nulla). */
export const DEFAULT_THEME: ThemePref = "dark";
export const DARK_QUERY = "(prefers-color-scheme: dark)";

export const THEME_OPTIONS: { value: ThemePref; label: string; icon: string; hint: string }[] = [
  { value: "dark", label: "Scuro", icon: "moon", hint: "Tema scuro" },
  { value: "light", label: "Chiaro", icon: "sun", hint: "Tema chiaro" },
  { value: "system", label: "Sistema", icon: "monitor", hint: "Segue il tema chiaro o scuro del sistema operativo" },
];

const PREFS: readonly string[] = ["dark", "light", "system"];

export function isThemePref(value: unknown): value is ThemePref {
  return typeof value === "string" && PREFS.includes(value);
}

/** Scelta dal valore salvato: JSON (`"system"`) come lo scrive `useLocalStorage`, o testo semplice (`system`). */
export function parseThemePref(raw: string | null | undefined, fallback: ThemePref = DEFAULT_THEME): ThemePref {
  if (raw === null || raw === undefined) return fallback;
  let value: unknown;
  try {
    value = JSON.parse(raw);
  } catch {
    value = raw.trim();
  }
  return isThemePref(value) ? value : fallback;
}

/** Tema effettivo: quello scelto, oppure quello del sistema per "Sistema". */
export function resolveTheme(pref: ThemePref, systemDark: boolean): Theme {
  if (pref === "system") return systemDark ? "dark" : "light";
  return pref === "light" ? "light" : "dark";
}

/** Scelta successiva per il pulsante a ciclo: scuro → chiaro → sistema → scuro. */
export function nextThemePref(pref: ThemePref): ThemePref {
  const i = PREFS.indexOf(pref);
  return PREFS[(i + 1) % PREFS.length] as ThemePref;
}

/** Media query del tema del sistema, se il browser la offre. */
function darkQuery(): MediaQueryList | null {
  return typeof window !== "undefined" && typeof window.matchMedia === "function" ? window.matchMedia(DARK_QUERY) : null;
}

/** Il sistema chiede il tema scuro (senza matchMedia: scuro, il predefinito di BioLoom). */
export function systemPrefersDark(): boolean {
  return darkQuery()?.matches ?? true;
}

/** Tema da applicare prima del primo render (main.tsx), per non far lampeggiare l'altro tema. */
export function initialTheme(storage: Pick<Storage, "getItem"> | null): Theme {
  let raw: string | null;
  try {
    raw = storage?.getItem(THEME_KEY) ?? null;
  } catch {
    raw = null;
  }
  return resolveTheme(parseThemePref(raw), systemPrefersDark());
}

/** Tema effettivo scritto su `<html data-theme>` (senza documento o senza attributo: scuro, il predefinito). */
export function documentTheme(): Theme {
  return typeof document !== "undefined" && document.documentElement.dataset.theme === "light" ? "light" : "dark";
}

/** Tema effettivo della pagina, aggiornato quando `<html data-theme>` cambia (pulsante del tema, tema del sistema). */
export function useDocumentTheme(): Theme {
  const [theme, setTheme] = useState<Theme>(documentTheme);
  useEffect(() => {
    if (typeof MutationObserver === "undefined") return;
    const update = () => setTheme(documentTheme());
    update();
    const observer = new MutationObserver(update);
    observer.observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
    return () => observer.disconnect();
  }, []);
  return theme;
}

/** Sfondo del visualizzatore 3D per tema: gli stessi valori di `--viewer-bg` in styles.css (ripiego senza foglio di stile). */
export const VIEWER_BACKGROUND: Record<Theme, string> = { dark: "#0b1120", light: "#f2f4f8" };

/**
 * Stile della vista 3D per tema (3Dmol `setViewStyle`). Nel tema chiaro un contorno sottile scuro intorno a nastri,
 * bastoncini e sfere: `width` in Å, al più `maxpixels` px (per il rapporto di pixel dello schermo), così si vede anche
 * con la struttura intera e ingrandendo non copre i tratteggi sottili (provato con 3Dmol 2.5 su 1UBQ: con 0,1 Å il
 * contorno spariva a struttura intera, con 2 px riempiva i vuoti dei tratteggi). Stacca dal fondo chiaro i colori
 * chiari che restano tali, cioè i gradienti delle scale continue (centro bianco di blu-bianco-rosso, del potenziale e
 * della conservazione, giallo di viridis e della scala delle distanze), gli schemi di 3Dmol (B-factor, struttura
 * secondaria, catene) e i colori chiari di fasce e categorie senza variante (pLDDT, tasche…). Scurire quei gradienti
 * ne cambierebbe la lettura (il centro neutro di una scala divergente sembrerebbe un estremo, viridis perderebbe la
 * luminosità crescente). Nel tema scuro nessun contorno, come prima.
 */
export const VIEWER_VIEW_STYLE: Record<Theme, { style: string; color?: string; width?: number; maxpixels?: number }> = {
  dark: { style: "" },
  light: { style: "outline", color: "#1e293b", width: 0.5, maxpixels: 1.5 },
};

/** Sfondo del visualizzatore 3D: `--viewer-bg` del tema corrente, o il valore predefinito del tema. Il visualizzatore
 *  lo ricalcola quando cambia `useDocumentTheme` (WebGL non legge le variabili CSS: serve il valore). */
export function viewerBackground(theme: Theme): string {
  try {
    const css = getComputedStyle(document.documentElement).getPropertyValue("--viewer-bg").trim();
    if (css) return css;
  } catch {
    /* niente documento o foglio di stile (test) */
  }
  return VIEWER_BACKGROUND[theme];
}

/**
 * Scelta del tema salvata e tema effettivo applicato a `<html data-theme>`; con "Sistema" ascolta i cambi del sistema
 * operativo finché la scelta resta quella.
 */
export function useThemePreference(): { pref: ThemePref; theme: Theme; setPref: (p: ThemePref) => void } {
  const [pref, setPref] = useState<ThemePref>(() => {
    try {
      return parseThemePref(localStorage.getItem(THEME_KEY));
    } catch {
      return DEFAULT_THEME;
    }
  });
  useEffect(() => {
    try {
      localStorage.setItem(THEME_KEY, JSON.stringify(pref)); // stesso formato di useLocalStorage
    } catch {
      /* storage non disponibile */
    }
  }, [pref]);
  const [systemDark, setSystemDark] = useState(systemPrefersDark);
  useEffect(() => {
    if (pref !== "system") return;
    const mq = darkQuery();
    if (!mq) return;
    const update = () => setSystemDark(mq.matches);
    update();
    if (typeof mq.addEventListener === "function") {
      mq.addEventListener("change", update);
      return () => mq.removeEventListener("change", update);
    }
    mq.addListener(update); // Safari < 14
    return () => mq.removeListener(update);
  }, [pref]);
  const theme = resolveTheme(pref, systemDark);
  useEffect(() => {
    document.documentElement.dataset.theme = theme;
  }, [theme]);
  return { pref, theme, setPref };
}
