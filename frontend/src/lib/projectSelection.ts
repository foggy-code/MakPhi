/**
 * Progetto attivo dopo una rilettura dell'elenco (ProjectProvider): quello attuale se c'è ancora, altrimenti il primo,
 * altrimenti nessuno. Con un elenco vuoto si restituisce null e non l'id vecchio: un progetto eliminato (anche da
 * un'altra scheda) non resta attivo e le pagine non chiamano l'API con un id che il server non ha più.
 */
export function pickProjectId(list: { id: string }[], current: string | null): string | null {
  if (current && list.some((p) => p.id === current)) return current;
  return list.length ? list[0].id : null;
}
