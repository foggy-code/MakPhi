/**
 * Copia negli appunti. `navigator.clipboard` esiste solo nei contesti sicuri (HTTPS o localhost): su un server raggiunto
 * in HTTP senza TLS manca, e `writeText` può anche essere rifiutato (permesso negato, documento senza focus). In quei
 * casi si ripiega su una textarea temporanea fuori vista e `document.execCommand("copy")`, che i browser accettano
 * durante un clic. Se nemmeno questo riesce la promessa è rifiutata con un messaggio in italiano.
 */
export const COPY_FAILED = "Copia negli appunti non riuscita: il browser non lo consente su questa pagina. Seleziona il testo e copialo con Ctrl+C.";

export async function copyText(text: string): Promise<void> {
  const clipboard = typeof navigator !== "undefined" ? navigator.clipboard : undefined;
  if (clipboard?.writeText) {
    try {
      await clipboard.writeText(text);
      return;
    } catch {
      /* rifiutato: ripiego qui sotto */
    }
  }
  if (!copyWithTextarea(text)) throw new Error(COPY_FAILED);
}

/** Ripiego senza Clipboard API: true se il browser dice di aver copiato. Focus e selezione tornano com'erano. */
export function copyWithTextarea(text: string): boolean {
  if (typeof document === "undefined" || !document.body) return false;
  const area = document.createElement("textarea");
  area.value = text;
  area.setAttribute("readonly", ""); // niente tastiera virtuale sui telefoni
  area.setAttribute("aria-hidden", "true");
  Object.assign(area.style, { position: "fixed", top: "0", left: "0", width: "1px", height: "1px", opacity: "0", pointerEvents: "none" });
  const focused = document.activeElement instanceof HTMLElement ? document.activeElement : null;
  const selection = document.getSelection();
  const ranges = selection ? Array.from({ length: selection.rangeCount }, (_, i) => selection.getRangeAt(i)) : [];
  document.body.appendChild(area);
  let copied: boolean;
  try {
    area.focus();
    area.select();
    area.setSelectionRange(0, text.length);
    copied = typeof document.execCommand === "function" && document.execCommand("copy");
  } catch {
    copied = false;
  } finally {
    area.remove();
    focused?.focus();
    if (selection) {
      selection.removeAllRanges();
      ranges.forEach((r) => selection.addRange(r));
    }
  }
  return copied;
}
