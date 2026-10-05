import { useState } from "react";
import { api } from "../api/client";
import { useToast } from "../lib/context";
import { saveFile } from "../lib/exportImage";
import { canExport } from "../lib/rerun";
import { Icon } from "./Icon";
import { Spinner } from "./ui";

/**
 * «Pacchetto riproducibile» (F9.4): ZIP del job con manifest (versioni, parametri, semi, checksum), input, output,
 * script MODELLER e LEGGIMI, da rieseguire con «Riesegui da pacchetto» o `python bioloom.py rerun`. Solo per i job
 * completati dei tipi che hanno un pacchetto.
 */
export function ReproButton({ jobId, kind, status, compact = false }: { jobId: string; kind: string; status: string; compact?: boolean }) {
  const toast = useToast();
  const [busy, setBusy] = useState(false);
  if (!canExport(kind, status)) return null;
  const run = async () => {
    setBusy(true);
    try {
      const file = await api.downloadPackage(jobId);
      saveFile(file.blob, file.filename);
      toast.push("Pacchetto riproducibile scaricato: rieseguilo da Job → «Riesegui da pacchetto».");
    } catch (e) {
      toast.error(e);
    } finally {
      setBusy(false);
    }
  };
  return (
    <button type="button" className="btn btn-sm" disabled={busy} onClick={run} aria-label={compact ? "Scarica il pacchetto riproducibile" : undefined}
      title="Archivio per rieseguire e verificare il calcolo: versioni, parametri completi, semi casuali, input e risultati con checksum (senza chiavi di licenza né dati personali)">
      {busy ? <Spinner /> : <Icon name="download" size={13} />}{compact ? "" : " Pacchetto riproducibile"}
    </button>
  );
}
