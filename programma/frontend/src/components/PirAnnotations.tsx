import { useState } from "react";
import { ANNOTATION_COLORS, type PirAnnotation } from "../lib/pirAnnotations";
import { Card, Field, Modal } from "./ui";

/** "35–37" con i numeri della griglia (`labelOf`: numero del residuo n-esimo del bersaglio, da 1). */
const span = (from: number, to: number, labelOf: (n: number) => string) => (from === to ? labelOf(from) : `${labelOf(from)}–${labelOf(to)}`);

/** Nome e colore di una nuova annotazione sui residui `from`–`to` del bersaglio. */
export function AnnotationDialog({ from, to, labelOf, onClose, onSave }: {
  from: number;
  to: number;
  labelOf: (n: number) => string;
  onClose: () => void;
  onSave: (label: string, color: string) => void;
}) {
  const [label, setLabel] = useState("");
  const [color, setColor] = useState(ANNOTATION_COLORS[0].value);
  const ok = label.trim().length > 0 && label.trim().length <= 80;
  return (
    <Modal
      title={`Annotazione sui residui ${span(from, to, labelOf)} del bersaglio`}
      onClose={onClose}
      footer={
        <>
          <button type="button" className="btn" onClick={onClose}>Annulla</button>
          <button type="button" className="btn btn-primary" disabled={!ok} onClick={() => onSave(label.trim(), color)}>Aggiungi</button>
        </>
      }
    >
      <div className="stack">
        <Field label="Nome" hint="Es. sito attivo, ponte disolfuro C26–C84, motivo P-loop.">
          <input className="input" value={label} maxLength={80} onChange={(e) => setLabel(e.target.value)} autoFocus
            onKeyDown={(e) => { if (e.key === "Enter" && ok) onSave(label.trim(), color); }} />
        </Field>
        <Field label="Colore">
          <div className="row" style={{ gap: 6 }} role="radiogroup" aria-label="Colore dell'annotazione">
            {ANNOTATION_COLORS.map((c) => (
              <button key={c.value} type="button" role="radio" aria-checked={c.value === color} className={`btn btn-sm${c.value === color ? " btn-primary" : ""}`}
                onClick={() => setColor(c.value)}>
                <i className="pirg-swatch" style={{ background: c.value }} /> {c.label}
              </button>
            ))}
          </div>
        </Field>
      </div>
    </Modal>
  );
}

/** Elenco delle annotazioni del documento, con il salto alla colonna e l'eliminazione. */
export function AnnotationsCard({ annotations, missing, labelOf, onGo, onDelete }: {
  annotations: PirAnnotation[];
  labelOf: (n: number) => string;
  /** annotazioni i cui residui non ci sono più nel bersaglio */
  missing: Set<string>;
  onGo: (a: PirAnnotation) => void;
  onDelete: (a: PirAnnotation) => void;
}) {
  if (!annotations.length) return null;
  return (
    <Card title="Annotazioni" icon="tag" sub={String(annotations.length)}>
      <div className="table-wrap">
        <table className="table">
          <thead><tr><th>Nome</th><th className="num">Residui del bersaglio</th><th /></tr></thead>
          <tbody>
            {annotations.map((a) => (
              <tr key={a.id}>
                <td><i className="pirg-swatch" style={{ background: a.color }} /> {a.label}</td>
                <td className="num">
                  {missing.has(a.id) ? `${a.from}–${a.to} (posizioni)` : span(a.from, a.to, labelOf)}
                  {missing.has(a.id) && <div className="tiny" style={{ color: "var(--warn)" }}>residui non più nel bersaglio</div>}
                </td>
                <td className="num" style={{ whiteSpace: "nowrap" }}>
                  <button type="button" className="btn btn-sm btn-ghost" disabled={missing.has(a.id)} onClick={() => onGo(a)}>Vai</button>
                  <button type="button" className="btn btn-sm" onClick={() => onDelete(a)} aria-label={`Elimina l'annotazione ${a.label}`}>Elimina</button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="small text-2" style={{ marginTop: 8 }}>Le annotazioni seguono i residui del bersaglio quando sposti i gap e si salvano con il documento.</p>
    </Card>
  );
}
