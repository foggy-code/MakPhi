/**
 * Report HTML del modello (backend: domain/report.py): immagine del viewer 3D da incorporare e nome del file
 * scaricato. Logica pura, testata in report.test.ts.
 */

/** Limite del server per l'immagine (domain/report.MAX_IMAGE_BYTES): oltre, il report si genera senza. */
export const REPORT_IMAGE_MAX_BYTES = 3 * 1024 * 1024;
const PNG_PREFIX = "data:image/png;base64,";

/** Byte rappresentati da un testo base64, senza decodificarlo. */
export function base64Bytes(b64: string): number {
  const clean = b64.replace(/\s+/g, "");
  const pad = clean.endsWith("==") ? 2 : clean.endsWith("=") ? 1 : 0;
  return Math.max(0, Math.floor((clean.length * 3) / 4) - pad);
}

export interface ReportImage {
  /** data URI da inviare (`image_png_base64`), null = report senza vista 3D */
  image: string | null;
  /** perché l'immagine non c'è, se il viewer ne ha data una inutilizzabile (da mostrare solo a report salvato) */
  note: string | null;
}

/**
 * Vista 3D (data URI di 3Dmol `pngURI`) da incorporare nel report: un PNG entro il limite del server. Senza
 * viewer aperto non c'è immagine; un'immagine non PNG o troppo grande non si invia (il server la rifiuterebbe) e
 * il report si genera comunque, con il motivo in `note` (vedi `reportSavedMessage`).
 */
export function reportImage(dataUri: string | null | undefined): ReportImage {
  if (!dataUri) return { image: null, note: null };
  if (!dataUri.startsWith(PNG_PREFIX) || dataUri.length === PNG_PREFIX.length) {
    return { image: null, note: "la vista 3D non è disponibile come PNG" };
  }
  if (base64Bytes(dataUri.slice(PNG_PREFIX.length)) > REPORT_IMAGE_MAX_BYTES) {
    return { image: null, note: "la vista 3D supera 3 MB (riduci la finestra del viewer)" };
  }
  return { image: dataUri, note: null };
}

/**
 * Avviso dopo il salvataggio riuscito del report: con la vista 3D, senza, oppure senza perché la vista non era
 * utilizzabile (tono "warn", con il motivo). Un report non generato mostra solo l'errore: il motivo dell'immagine
 * mancante non si annuncia prima della richiesta.
 */
export function reportSavedMessage({ image, note }: ReportImage): { text: string; tone: "ok" | "warn" } {
  if (image) return { text: "Report scaricato, con la vista 3D corrente", tone: "ok" };
  if (note) return { text: `Report scaricato senza la vista 3D: ${note}.`, tone: "warn" };
  return { text: "Report scaricato", tone: "ok" };
}

/**
 * Nome del file dall'intestazione Content-Disposition (`filename*=UTF-8''…` oppure `filename="…"`), altrimenti
 * `fallback`. Solo il nome: un eventuale percorso viene tolto.
 */
export function filenameFromDisposition(header: string | null | undefined, fallback: string): string {
  if (!header) return fallback;
  let name = "";
  const star = /filename\*\s*=\s*(?:UTF-8|utf-8)''([^;]+)/.exec(header);
  if (star) {
    try {
      name = decodeURIComponent(star[1].trim());
    } catch {
      name = "";
    }
  }
  if (!name) {
    const plain = /filename\s*=\s*(?:"([^"]*)"|([^;]+))/.exec(header);
    name = (plain?.[1] ?? plain?.[2] ?? "").trim();
  }
  const base = name.split(/[\\/]/).pop()?.trim() ?? "";
  return base && base !== "." && base !== ".." ? base : fallback;
}

/** Nome di riserva del report, come quello del server: `report_<modello senza estensione>.html`. */
export function reportFallbackName(model: string | null | undefined, jobId: string): string {
  const stem = (model ?? "").split("/").pop()?.replace(/\.[^.]*$/, "") ?? "";
  const clean = (stem ? stem : `qualita_${jobId}`).replace(/[^A-Za-z0-9_.-]+/g, "_").replace(/_+/g, "_").replace(/^[._-]+|[._-]+$/g, "");
  return `report_${clean || jobId}.html`;
}
