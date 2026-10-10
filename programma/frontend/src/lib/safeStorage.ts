/**
 * sessionStorage senza eccezioni. Con i dati del sito bloccati (o in certe finestre private) già leggere
 * `window.sessionStorage` lancia SecurityError, e una scrittura può fallire per spazio esaurito: qui una lettura non
 * riuscita vale null e una scrittura non riuscita restituisce false, invece di interrompere chi chiama (main.tsx fa lo
 * stesso per localStorage).
 */
function session(): Storage | null {
  try {
    return globalThis.sessionStorage ?? null;
  } catch {
    return null;
  }
}

export function sessionGet(key: string): string | null {
  try {
    return session()?.getItem(key) ?? null;
  } catch {
    return null;
  }
}

/** true se il valore è stato scritto. */
export function sessionSet(key: string, value: string): boolean {
  const storage = session();
  if (!storage) return false;
  try {
    storage.setItem(key, value);
    return true;
  } catch {
    return false;
  }
}
