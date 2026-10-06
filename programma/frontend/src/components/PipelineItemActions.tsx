import { useRef } from "react";
import { Link } from "react-router-dom";
import { api } from "../api/client";
import type { PipelineItem } from "../api/types";
import { Icon } from "./Icon";
import { ReportButton } from "./ReportButton";
import { Modal } from "./ui";
import { Viewer3D, type Viewer3DHandle } from "./Viewer3D";

/**
 * Azioni di un elemento di pipeline con un modello: vista 3D, qualità, PDB e report HTML del modello. Il report c'è
 * anche senza analisi di qualità dell'elemento (pipeline senza «Analisi di qualità del modello migliore»): la pagina
 * Qualità, in quel caso, non lo offre.
 */
export function PipelineItemActions({ jobId, item, onView }: { jobId: string; item: Pick<PipelineItem, "best_model" | "status">; onView: () => void }) {
  const model = item.best_model;
  if (!model) return null;
  return (
    <>
      <button type="button" className="btn btn-sm" onClick={onView} title="Visualizza" aria-label="Visualizza il modello in 3D"><Icon name="eye" size={13} /></button>{" "}
      <Link className="btn btn-sm" to={`/quality?job=${jobId}&file=${encodeURIComponent(model)}`} title="Qualità" aria-label="Qualità del modello"><Icon name="shield" size={13} /></Link>{" "}
      <a className="btn btn-sm" href={api.jobFileUrl(jobId, model, true)} title="Scarica PDB" aria-label="Scarica il PDB del modello"><Icon name="download" size={13} /></a>{" "}
      {/* il server genera il report solo per un elemento completato (409 altrimenti) */}
      <ReportButton jobId={jobId} model={model} compact disabled={item.status !== "completed"} />
    </>
  );
}

/** Finestra «Visualizza» del modello di un elemento: il report generato da qui incorpora la vista 3D corrente. */
export function PipelineModelView({ jobId, name, model, onClose }: { jobId: string; name: string; model: string; onClose: () => void }) {
  const viewer = useRef<Viewer3DHandle>(null);
  return (
    <Modal
      title={`Modello di ${name}`}
      size="wide"
      onClose={onClose}
      footer={
        <>
          <span className="small text-2" style={{ alignSelf: "center" }}>Il report include la vista 3D corrente.</span>
          <ReportButton jobId={jobId} model={model} viewer={viewer} />
        </>
      }
    >
      <Viewer3D url={api.jobFileUrl(jobId, model)} height={520} handle={viewer} />
    </Modal>
  );
}
