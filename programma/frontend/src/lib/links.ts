/**
 * Indirizzo di un servizio esterno (es. `results_page` di SWISS-MODEL) da usare come link solo se è https: un valore
 * arrivato dalla rete non deve poter diventare un link `javascript:`, `data:` o in chiaro. Altrimenti null.
 */
export function httpsUrl(value: unknown): string | null {
  if (typeof value !== "string" || !value.trim()) return null;
  try {
    const url = new URL(value.trim());
    return url.protocol === "https:" && !!url.hostname ? url.href : null;
  } catch {
    return null;
  }
}
