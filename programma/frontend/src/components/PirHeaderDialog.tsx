import { useState } from "react";
import { formatHeader, HEADER_KINDS, headerErrors, parseHeader, type PirHeader } from "../lib/pirHeader";
import { Field, Modal } from "./ui";

const KIND_LABEL: Record<string, string> = {
  sequence: "sequence: bersaglio (sequenza da modellare)",
  structureX: "structureX: template da struttura a raggi X",
  structureN: "structureN: template da NMR",
  structureM: "structureM: template da modello",
};

/**
 * Modulo per codice e seconda riga di una voce del PIR: i dieci campi separati da «:» uno per uno, con il controllo di
 * ciascuno, invece di contare i due punti a mano.
 */
export function PirHeaderDialog({ code: initialCode, header, onClose, onSave }: {
  code: string;
  header: string;
  onClose: () => void;
  onSave: (code: string, header: string) => void;
}) {
  const [code, setCode] = useState(initialCode);
  const [h, setH] = useState<PirHeader>(() => parseHeader(header));
  const errors = headerErrors(code, h);
  const valid = Object.keys(errors).length === 0;
  const template = h.kind.startsWith("structure");
  const set = (k: keyof PirHeader) => (e: { target: { value: string } }) => setH((x) => ({ ...x, [k]: e.target.value }));
  const input = (k: keyof PirHeader, label: string, hint?: string, placeholder?: string) => (
    <Field label={label} hint={errors[k] ? <span style={{ color: "var(--err)" }}>{errors[k]}</span> : hint}>
      <input className="input" value={h[k]} onChange={set(k)} placeholder={placeholder} aria-invalid={!!errors[k]} />
    </Field>
  );
  return (
    <Modal
      title={`Voce ${initialCode}: codice e seconda riga`}
      size="wide"
      onClose={onClose}
      footer={
        <>
          <button type="button" className="btn" onClick={onClose}>Annulla</button>
          <button type="button" className="btn btn-primary" disabled={!valid} onClick={() => onSave(code.trim(), formatHeader(h))}>Applica</button>
        </>
      }
    >
      <div className="stack">
        <div className="grid grid-2">
          <Field label="Codice della voce (>P1;…)" hint={errors.code ? <span style={{ color: "var(--err)" }}>{errors.code}</span> : "Per i template BioLoom usa <ID>_<catene>, es. 1ABC_A."}>
            <input className="input mono" value={code} onChange={(e) => setCode(e.target.value)} aria-invalid={!!errors.code} autoFocus />
          </Field>
          <Field label="Tipo" hint={errors.kind}>
            <select className="input" value={h.kind} onChange={set("kind")}>
              {!HEADER_KINDS.includes(h.kind as (typeof HEADER_KINDS)[number]) && <option value={h.kind}>{h.kind || "(vuoto)"}</option>}
              {HEADER_KINDS.map((k) => <option key={k} value={k}>{KIND_LABEL[k]}</option>)}
            </select>
          </Field>
        </div>
        {template && input("file", "File della struttura", "Codice del file che MODELLER apre (<codice>.pdb); BioLoom lo imposta uguale al codice della voce.")}
        <div className="grid grid-2">
          {input("startRes", "Residuo iniziale", template ? "Numero nel file (es. 5 o 52A); FIRST o vuoto per il primo." : "Di solito vuoto per il bersaglio.", "FIRST")}
          {input("startChain", "Catena iniziale", undefined, "A")}
          {input("endRes", "Residuo finale", template ? "Numero nel file; LAST o vuoto per l'ultimo." : undefined, "LAST")}
          {input("endChain", "Catena finale", undefined, "A")}
          {input("name", "Nome della proteina")}
          {input("source", "Organismo o fonte")}
          {input("resolution", "Risoluzione (Å)", "−1.00 se non nota.")}
          {input("rFactor", "Fattore R")}
        </div>
        <div className="small text-2 mono" aria-label="Anteprima della seconda riga">{formatHeader(h)}</div>
        {code.trim() !== initialCode && (
          <p className="small" style={{ color: "var(--warn)" }}>
            Cambiando il codice di un template, BioLoom lo abbina al template scelto per posizione invece che per codice: controlla l'ordine
            dei template prima di modellare.
          </p>
        )}
      </div>
    </Modal>
  );
}
