// Caricamento di un file nella libreria dei template: conferma prima di sostituire un template con lo stesso id.
import { ApiError } from "../api/client";

/** Codice dell'errore 409 di POST /api/templates/upload quando l'id è già in libreria (senza `overwrite`). */
export const TEMPLATE_EXISTS = "template_exists";

/**
 * Domanda da porre quando il caricamento trova lo stesso id in libreria: il messaggio del server, da confermare con
 * «Sostituisci» (nuovo tentativo con `overwrite`). null per ogni altro errore e per un tentativo già con `overwrite`,
 * così la conferma non si ripete mai due volte per lo stesso file.
 */
export function replaceQuestion(error: unknown, overwrite: boolean): string | null {
  if (overwrite || !(error instanceof ApiError) || error.status !== 409 || error.code !== TEMPLATE_EXISTS) return null;
  return error.message;
}
