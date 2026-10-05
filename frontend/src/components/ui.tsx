import { useEffect, useId, useRef, useState, type ReactNode } from "react";
import { fmtNum, gradeTone, STATUS_LABEL } from "../lib/format";
import { Icon } from "./Icon";

export function Card({ title, sub, right, children, className = "" }: { title?: ReactNode; sub?: ReactNode; right?: ReactNode; children?: ReactNode; className?: string }) {
  return (
    <section className={`card ${className}`}>
      {(title || right) && (
        <div className="card-head">
          {title && <h3>{title}</h3>}
          {sub && <span className="sub">{sub}</span>}
          {right && <div className="right">{right}</div>}
        </div>
      )}
      {children}
    </section>
  );
}

export function PageHead({ title, lead, actions }: { title: string; lead?: ReactNode; actions?: ReactNode }) {
  return (
    <div className="page-head">
      <div>
        <h1>{title}</h1>
        {lead && <p className="lead">{lead}</p>}
      </div>
      {actions && <div className="actions">{actions}</div>}
    </div>
  );
}

const STATUS_TONE: Record<string, string> = {
  completed: "ok",
  running: "accent",
  searching: "accent",
  modeling: "accent",
  queued: "info",
  pending: "info",
  cancelling: "warn",
  failed: "err",
  cancelled: "warn",
  filtered: "warn",
};

export function StatusBadge({ status }: { status: string }) {
  const tone = STATUS_TONE[status] ?? "";
  const live = ["running", "searching", "modeling", "cancelling"].includes(status);
  return (
    <span className={`badge ${tone ? `badge-${tone}` : ""}`}>
      <span className={`dot ${live ? "pulse" : ""}`} />
      {STATUS_LABEL[status] ?? status}
    </span>
  );
}

export function Badge({ tone, children, title }: { tone?: "ok" | "info" | "warn" | "err" | "accent"; children: ReactNode; title?: string }) {
  return (
    <span className={`badge ${tone ? `badge-${tone}` : ""}`} title={title}>
      {children}
    </span>
  );
}

export function Grade({ grade, large = false, title }: { grade?: string | null; large?: boolean; title?: string }) {
  return (
    <span className={`grade grade-${gradeTone(grade)} ${large ? "grade-lg" : ""}`} title={title ?? "Voto BioLoom (A–D)"}>
      {grade ?? "–"}
    </span>
  );
}

/** Barra di avanzamento; con `label` è una progressbar accessibile (nome e percentuale letti dagli screen reader). */
export function Progress({ value, indeterminate = false, label }: { value: number; indeterminate?: boolean; label?: string }) {
  const percent = Math.round(Math.max(0, Math.min(1, value)) * 100);
  const a11y = label
    ? { role: "progressbar", "aria-label": label, "aria-valuemin": 0, "aria-valuemax": 100, "aria-valuenow": indeterminate ? undefined : percent }
    : {};
  return (
    <div className={`progress ${indeterminate ? "indeterminate" : ""}`} {...a11y}>
      <div style={{ width: `${Math.round(Math.max(0.02, Math.min(1, value)) * 100)}%` }} />
    </div>
  );
}

export function Spinner() {
  return <span className="spinner" aria-label="Caricamento" />;
}

export function Alert({ tone = "info", children }: { tone?: "err" | "warn" | "info" | "ok"; children: ReactNode }) {
  const icon = tone === "ok" ? "check" : tone === "info" ? "info" : "alert";
  return (
    <div className={`alert alert-${tone}`}>
      <Icon name={icon} />
      <div>{children}</div>
    </div>
  );
}

export function Empty({ icon = "info", title, children }: { icon?: string; title: string; children?: ReactNode }) {
  return (
    <div className="empty">
      <Icon name={icon} size={30} />
      <div className="text-2" style={{ fontWeight: 600 }}>{title}</div>
      {children && <div className="small">{children}</div>}
    </div>
  );
}

export function Stat({ label, value, note }: { label: string; value: ReactNode; note?: ReactNode }) {
  return (
    <div className="stat">
      <div className="label">{label}</div>
      <div className="value">{value}</div>
      {note && <div className="note">{note}</div>}
    </div>
  );
}

export function Field({ label, hint, children }: { label: string; hint?: ReactNode; children: ReactNode }) {
  return (
    <label className="field">
      <span>{label}</span>
      {children}
      {hint && <span className="hint">{hint}</span>}
    </label>
  );
}

export function Segmented<T extends string>({ value, options, onChange }: { value: T; options: { value: T; label: string }[]; onChange: (v: T) => void }) {
  return (
    <div className="segmented" role="radiogroup">
      {options.map((o) => (
        <button key={o.value} type="button" role="radio" aria-checked={o.value === value} className={o.value === value ? "on" : ""} onClick={() => onChange(o.value)}>
          {o.label}
        </button>
      ))}
    </div>
  );
}

export function Tabs<T extends string>({ value, tabs, onChange }: { value: T; tabs: { value: T; label: ReactNode }[]; onChange: (v: T) => void }) {
  return (
    <div className="tabs" role="tablist">
      {tabs.map((t) => (
        <button key={t.value} role="tab" aria-selected={t.value === value} className={t.value === value ? "on" : ""} onClick={() => onChange(t.value)}>
          {t.label}
        </button>
      ))}
    </div>
  );
}

export function Modal({ title, onClose, children, footer, wide = false }: { title: ReactNode; onClose: () => void; children: ReactNode; footer?: ReactNode; wide?: boolean }) {
  const titleId = useId(); // il titolo dà il nome accessibile al dialogo
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);
  return (
    <div className="modal-back" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div className={`modal ${wide ? "wide" : ""}`} role="dialog" aria-modal="true" aria-labelledby={titleId}>
        <div className="modal-head">
          <h2 id={titleId}>{title}</h2>
          <button className="btn btn-ghost btn-icon right" onClick={onClose} aria-label="Chiudi">
            <Icon name="x" />
          </button>
        </div>
        <div className="modal-body">{children}</div>
        {footer && <div className="modal-foot">{footer}</div>}
      </div>
    </div>
  );
}

export function Confirm({ text, onConfirm, onCancel, danger = true, confirmLabel = "Conferma" }: { text: ReactNode; onConfirm: () => void; onCancel: () => void; danger?: boolean; confirmLabel?: string }) {
  return (
    <Modal
      title="Conferma"
      onClose={onCancel}
      footer={
        <>
          <button className="btn" onClick={onCancel}>Annulla</button>
          <button className={`btn ${danger ? "btn-danger" : "btn-primary"}`} onClick={onConfirm}>{confirmLabel}</button>
        </>
      }
    >
      <p className="text-2">{text}</p>
    </Modal>
  );
}

export function DropZone({ accept, onFile, label }: { accept: string; onFile: (f: File) => void; label: ReactNode }) {
  const input = useRef<HTMLInputElement>(null);
  const [over, setOver] = useState(false);
  return (
    <div
      className={`drop ${over ? "over" : ""}`}
      onClick={() => input.current?.click()}
      onDragOver={(e) => {
        e.preventDefault();
        setOver(true);
      }}
      onDragLeave={() => setOver(false)}
      onDrop={(e) => {
        e.preventDefault();
        setOver(false);
        const f = e.dataTransfer.files?.[0];
        if (f) onFile(f);
      }}
    >
      <Icon name="upload" size={20} />
      <div className="small" style={{ marginTop: 6 }}>{label}</div>
      <input
        ref={input}
        type="file"
        accept={accept}
        hidden
        onChange={(e) => {
          const f = e.target.files?.[0];
          if (f) onFile(f);
          e.target.value = "";
        }}
      />
    </div>
  );
}

export function BarCell({ value, max = 100, tone, label }: { value: number | null | undefined; max?: number; tone?: string; label?: string }) {
  const v = value ?? 0;
  const color = tone ?? (v >= 70 ? "var(--ok)" : v >= 40 ? "var(--accent)" : v >= 25 ? "var(--warn)" : "var(--err)");
  return (
    <div className="bar-cell">
      <div className="bar">
        <div style={{ width: `${Math.min(100, (v / max) * 100)}%`, background: color }} />
      </div>
      <span className="nowrap" style={{ minWidth: 42, textAlign: "right" }}>{label ?? (value === null || value === undefined ? "—" : `${fmtNum(v, 1)}%`)}</span>
    </div>
  );
}

export function CopyButton({ text, label = "Copia" }: { text: string; label?: string }) {
  const [done, setDone] = useState(false);
  return (
    <button
      className="btn btn-sm"
      onClick={() => {
        navigator.clipboard?.writeText(text).then(() => {
          setDone(true);
          setTimeout(() => setDone(false), 1500);
        });
      }}
    >
      <Icon name={done ? "check" : "copy"} size={14} /> {done ? "Copiato" : label}
    </button>
  );
}
