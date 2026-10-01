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
  let value: unknown = raw;
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
  let raw: string | null = null;
  try {
    raw = storage?.getItem(THEME_KEY) ?? null;
  } catch {
    raw = null;
  }
  return resolveTheme(parseThemePref(raw), systemPrefersDark());
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
