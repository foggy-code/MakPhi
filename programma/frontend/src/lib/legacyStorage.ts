/**
 * Preferenze salvate da BioLoom (il nome del programma fino alla 4.4.0) con le chiavi `bioloom.*`: alla prima apertura
 * di MakPhi passano alle chiavi `makphi.*` (tema, progetto attivo, pannelli, editor PIR, bozze della procedura guidata)
 * e le vecchie si tolgono. Un valore già salvato con il nome nuovo non si sovrascrive. Si chiama prima del primo
 * render, perché i componenti leggono le preferenze già al montaggio.
 */
const OLD = "bioloom.";
const NEW = "makphi.";

export function migrateLegacyKeys(storage: Storage | null): void {
  if (!storage) return;
  try {
    const keys: string[] = [];
    for (let i = 0; i < storage.length; i++) {
      const key = storage.key(i);
      if (key?.startsWith(OLD)) keys.push(key);
    }
    for (const key of keys) {
      const value = storage.getItem(key);
      const target = NEW + key.slice(OLD.length);
      if (value !== null && storage.getItem(target) === null) storage.setItem(target, value);
      storage.removeItem(key);
    }
  } catch {
    // spazio esaurito o dati del sito bloccati: valgono le preferenze predefinite, come senza localStorage
  }
}
