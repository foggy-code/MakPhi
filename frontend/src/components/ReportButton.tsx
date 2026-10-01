import { useState, type RefObject } from "react";
import { api } from "../api/client";
import { useToast } from "../lib/context";
import { reportImage, reportSavedMessage } from "../lib/report";
import { Icon } from "./Icon";
import type { Viewer3DHandle } from "./Viewer3D";
import { Spinner } from "./ui";

function saveFile(filename: string, blob: Blob) {
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

/**
 * Scarica il report HTML autonomo (stampabile in PDF dal browser) di un modello o, senza `model`, di un'analisi
 * di qualità. Se `viewer` mostra la struttura del report, la vista 3D corrente viene incorporata come PNG.
 */
export function ReportButton({ jobId, model = null, qmeanJob = null, predictJob = null, viewer, disabled = false, compact = false }: {
  jobId: string;
  model?: string | null;
  /** job qmean sulla stessa struttura, da includere (consenso e profilo locale) */
  qmeanJob?: string | null;
  /** confronto con una predizione sulla stessa struttura (F9.3); senza, il server include il più recente */
  predictJob?: string | null;
  /** viewer 3D della stessa struttura, se è aperto */
  viewer?: RefObject<Viewer3DHandle | null>;
  disabled?: boolean;
  /** solo l'icona (tabella dei modelli) */
  compact?: boolean;
}) {
  const toast = useToast();
  const [busy, setBusy] = useState(false);
  const run = async () => {
    setBusy(true);
    try {
      const shot = reportImage(viewer?.current?.pngDataUri() ?? null);
      const file = await api.report(jobId, model, shot.image ? { image_png_base64: shot.image } : {}, qmeanJob, predictJob);
      saveFile(file.filename, file.blob);
      // l'eventuale motivo della vista mancante solo a report salvato: se la richiesta fallisce resta solo l'errore
      const done = reportSavedMessage(shot);
      toast.push(done.text, done.tone);
    } catch (e) {
      toast.error(e);
    } finally {
      setBusy(false);
    }
  };
  const title = "Report HTML autonomo, da aprire nel browser e stampare in PDF" + (viewer ? " (con la vista 3D se è aperta)" : "");
  return (
    <button className="btn btn-sm" disabled={disabled || busy} title={title} onClick={run}>
      {busy ? <Spinner /> : <Icon name="file" size={13} />}{compact ? "" : " Report"}
    </button>
  );
}
