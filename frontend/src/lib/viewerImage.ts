/**
 * Immagine della vista 3D (Viewer3D): nome del file dalla struttura mostrata e conversione del data URI di 3Dmol in
 * Blob. A schermo intero il PNG pesa qualche MB e un data URI così lungo supera il limite degli URL dei download.
 */
import { safeFilename } from "./exportImage";

const STRUCTURE_EXT = /\.(pdb|ent|cif|mmcif|bcif)(\.gz)?$/i;

function decodePart(part: string): string {
  try {
    return decodeURIComponent(part);
  } catch {
    return part;
  }
}

/**
 * Nome del PNG dall'URL del file: ".../files/models/target.B99990001.pdb" → "target.B99990001_3d.png"; i template
 * (/api/templates/<id>/file) prendono l'id; senza URL (testo PDB passato direttamente) "struttura_3d.png".
 */
export function viewerPngName(url?: string | null): string {
  const parts = (url ?? "").split(/[?#]/)[0].split("/").filter(Boolean).map(decodePart);
  let name = parts[parts.length - 1] ?? "";
  if (name === "file" && parts.length > 1) name = parts[parts.length - 2];
  return safeFilename(`${name.replace(STRUCTURE_EXT, "") || "struttura"}_3d`, "png");
}

/** Blob da un data URI in base64 (come `pngURI()` di 3Dmol). */
export function dataUriToBlob(uri: string): Blob {
  const match = /^data:([^;,]*)(?:;[^,]*)?;base64,/.exec(uri);
  if (!match) throw new Error("Immagine non valida");
  const bin = atob(uri.slice(match[0].length));
  const bytes = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
  return new Blob([bytes], { type: match[1] || "application/octet-stream" });
}
