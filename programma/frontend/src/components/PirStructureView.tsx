import { lazy, Suspense } from "react";
import { api } from "../api/client";
import type { ResidueRef } from "../lib/residueColoring";
import { Card, Spinner } from "./ui";

// il visualizzatore 3D (3Dmol) si carica solo quando la vista è aperta
const Viewer3D = lazy(() => import("./Viewer3D").then((m) => ({ default: m.Viewer3D })));

export interface StructureFocus {
  /** codice della voce nel PIR */
  code: string;
  template: { id: string; chains: string[]; format?: "pdb" | "cif" };
  /** residuo del file sotto il cursore ("A:27"), se c'è */
  residue: string | null;
}

/** "A:52A" → catena e numero (il visualizzatore evidenzia per numero, senza codice d'inserzione). */
export function splitResidue(id: string): { chain: string; resi: number } | null {
  const m = /^([^:]*):(-?\d+)/.exec(id);
  return m ? { chain: m[1], resi: Number(m[2]) } : null;
}

/**
 * Vista 3D del template della voce sotto il cursore (o del primo template con un residuo nella colonna), con il residuo
 * del cursore evidenziato; un clic su un residuo porta il cursore della griglia sulla sua colonna.
 */
export function PirStructureView({ open, onToggle, focus, onPick }: {
  open: boolean;
  onToggle: (open: boolean) => void;
  focus: StructureFocus | null;
  onPick: (code: string, residue: ResidueRef) => void;
}) {
  const res = focus?.residue ? splitResidue(focus.residue) : null;
  const toggle = (
    <label className="check">
      <input type="checkbox" checked={open} onChange={(e) => onToggle(e.target.checked)} />
      Mostra
    </label>
  );
  return (
    <Card title="Vista 3D del template" icon="cube" sub={open && focus ? `${focus.code}${focus.residue ? ` · ${focus.residue}` : ""}` : undefined} right={toggle}>
      {!open ? (
        <p className="small text-2">Mostra la struttura del template sotto il cursore con il residuo evidenziato: utile per vedere se un'inserzione cade su un'ansa esposta.</p>
      ) : !focus ? (
        <p className="small text-2">Nessun template della libreria abbinato alle voci (serve la struttura secondaria attiva e il template nella Libreria template).</p>
      ) : (
        <div className="stack-sm">
          <Suspense fallback={<Spinner label="Caricamento della vista 3D" />}>
            <Viewer3D
              url={api.templateFileUrl(focus.template.id)}
              format={focus.template.format ?? "pdb"}
              height={360}
              highlight={res ? [res] : []}
              centerKey={focus.residue ?? focus.code}
              onPickResidue={(r) => onPick(focus.code, r)}
            />
          </Suspense>
          <p className="small text-2">Clic su un residuo: il cursore della griglia va sulla sua colonna nella voce {focus.code}.</p>
        </div>
      )}
    </Card>
  );
}
