import { useCallback, useEffect, useId, useRef, useState, type KeyboardEvent as RKeyboardEvent, type ReactNode } from "react";
import { COPY_FAILED, copyText } from "../lib/clipboard";
import { useOptionalToast } from "../lib/context";
import { fmtNum, gradeTone, JOB_STATUS_TONE, STATUS_LABEL } from "../lib/format";
import { Icon } from "./Icon";

/** Stato aperto/chiuso ricordato nel browser (sezioni a scomparsa); senza chiave resta solo in memoria. */
function useOpenState(storageKey: string | undefined, defaultOpen: boolean): [boolean, () => void] {
  const key = storageKey ? `bioloom.open.${storageKey}` : null;
  const [open, setOpen] = useState<boolean>(() => {
    if (!key) return defaultOpen;
    try {
      const raw = localStorage.getItem(key);
      return raw === null ? defaultOpen : raw === "1";
    } catch {
      return defaultOpen;
    }
  });
  const toggle = useCallback(() => {
    setOpen((o) => {
      if (key) {
        try {
          localStorage.setItem(key, o ? "0" : "1");
        } catch {
          /* storage non disponibile */
        }
      }
      return !o;
    });
  }, [key]);
  return [open, toggle];
}

/** Freccia delle sezioni espandibili (ruota quando è aperta). */
export function Chevron({ open, size = 14 }: { open: boolean; size?: number }) {
  return (
    <span className={`chev${open ? " open" : ""}`} aria-hidden="true">
      <Icon name="chevron" size={size} />
    </span>
  );
}

/**
 * Riquadro della pagina. Con `collapsible` (e un titolo) il titolo apre e chiude il contenuto: il contenuto si monta
 * alla prima apertura e poi resta montato, solo nascosto, così ordinamenti, filtri e zoom dei figli si conservano.
 * `storageKey` ricorda la scelta nel browser per quella sezione (vale per tutte le pagine che usano la stessa chiave:
 * una chiave per tipo di sezione, non per risultato). `icon` mette un'icona davanti al titolo.
 */
export function Card({ title, sub, right, children, className = "", icon, collapsible = false, defaultOpen = true, storageKey, id }: {
  title?: ReactNode;
  sub?: ReactNode;
  right?: ReactNode;
  children?: ReactNode;
  className?: string;
  icon?: string;
  collapsible?: boolean;
  defaultOpen?: boolean;
  storageKey?: string;
  id?: string;
}) {
  const [open, toggle] = useOpenState(storageKey, defaultOpen);
  const bodyId = useId();
  const canCollapse = collapsible && !!title;
  const [mounted, setMounted] = useState(open);
  if (canCollapse && open && !mounted) setMounted(true);
  const shown = !canCollapse || open;
  const iconEl = icon && <span className="card-icon"><Icon name={icon} size={15} /></span>;
  return (
    <section className={`card ${className}${shown ? "" : " collapsed"}`} id={id}>
      {(title || right) && (
        <div className="card-head">
          {title && (
            // il titolo resta un'intestazione anche quando apre e chiude la sezione (il pulsante sta dentro l'h3)
            <h3>
              {canCollapse ? (
                <button type="button" className="card-toggle" onClick={toggle} aria-expanded={open} aria-controls={mounted ? bodyId : undefined}>
                  <Chevron open={open} />
                  {iconEl}
                  {title}
                </button>
              ) : (
                <>
                  {iconEl}
                  {title}
                </>
              )}
            </h3>
          )}
          {sub && <span className="sub">{sub}</span>}
          {right && <div className="right">{right}</div>}
        </div>
      )}
      {canCollapse ? mounted && <div className="card-body" id={bodyId} hidden={!open}>{children}</div> : children}
    </section>
  );
}

/**
 * Sezione a scomparsa dentro un riquadro (`<details>` nativo: il contenuto resta nella pagina, anche per la ricerca
 * del browser). `extra` va a destra del titolo (conteggi, badge). Con `lazy` il contenuto si monta alla prima
 * apertura e poi resta (per i contenuti pesanti ripetuti, es. un grafico per ogni coppia di catene).
 */
export function Disclosure({ title, extra, children, defaultOpen = false, className = "", lazy = false }: {
  title: ReactNode;
  extra?: ReactNode;
  children: ReactNode;
  defaultOpen?: boolean;
  className?: string;
  lazy?: boolean;
}) {
  const [seen, setSeen] = useState(defaultOpen);
  return (
    <details className={`disclosure ${className}`} open={defaultOpen || undefined}
      onToggle={lazy ? (e) => { if ((e.currentTarget as HTMLDetailsElement).open) setSeen(true); } : undefined}>
      <summary>
        <span className="chev" aria-hidden="true"><Icon name="chevron" size={14} /></span>
        <span>{title}</span>
        {extra && <span className="summary-extra">{extra}</span>}
      </summary>
      <div className="disclosure-body">{(!lazy || seen) && children}</div>
    </details>
  );
}

/** Pulsante a freccia per le righe di tabella espandibili (non propaga il clic alla riga). */
export function ExpandButton({ open, onClick, label = "dettagli" }: { open: boolean; onClick: () => void; label?: string }) {
  return (
    <button type="button" className="expand-btn" aria-expanded={open} aria-label={open ? `Nascondi ${label}` : `Mostra ${label}`}
      title={open ? `Nascondi ${label}` : `Mostra ${label}`}
      onClick={(e) => {
        e.stopPropagation();
        onClick();
      }}>
      <Chevron open={open} />
    </button>
  );
}

/**
 * "Apri tutte" / "Chiudi tutte" per le tabelle con righe espandibili ("tutti" con `masculine`: contatti, metodi,
 * template). `noun` completa il nome accessibile ("Apri tutte le righe"); `label` sostituisce il testo di apertura
 * (es. "Apri i primi 20"). "Espandi" resta riservato alla vista grande (grafici, log, allineamento, 3D).
 */
export function ExpandAll({ allOpen, onExpand, onCollapse, noun = "righe", masculine = false, label, className = "" }: {
  allOpen: boolean;
  onExpand: () => void;
  onCollapse: () => void;
  noun?: string;
  masculine?: boolean;
  label?: string;
  className?: string;
}) {
  const all = masculine ? "tutti" : "tutte";
  const art = masculine ? "i" : "le";
  const text = allOpen ? `Chiudi ${all}` : label ?? `Apri ${all}`;
  const name = allOpen ? `Chiudi ${all} ${art} ${noun}` : label ? `${label} ${noun}` : `Apri ${all} ${art} ${noun}`;
  return (
    <button type="button" className={`btn btn-sm btn-ghost ${className}`.trim()} onClick={allOpen ? onCollapse : onExpand} aria-label={name}>
      <span className={`chev${allOpen ? " up" : ""}`} aria-hidden="true"><Icon name="chevronDown" size={13} /></span> {text}
    </button>
  );
}

/** Insieme di chiavi espanse (righe di tabella, elementi di elenco). */
export function useExpanded<K = string>(initial: K[] = []) {
  const [set, setSet] = useState<Set<K>>(() => new Set(initial));
  const toggle = useCallback((k: K) => setSet((s) => {
    const next = new Set(s);
    if (next.has(k)) next.delete(k);
    else next.add(k);
    return next;
  }), []);
  const change = useCallback((k: K, open: boolean) => setSet((s) => {
    if (s.has(k) === open) return s;
    const next = new Set(s);
    if (open) next.add(k);
    else next.delete(k);
    return next;
  }), []);
  return {
    has: (k: K) => set.has(k),
    toggle,
    /** apre senza alternare (es. "seleziona e mostra i dettagli") */
    open: (k: K) => change(k, true),
    close: (k: K) => change(k, false),
    collapseAll: () => setSet(new Set()),
    expandAll: (keys: K[]) => setSet(new Set(keys)),
    size: set.size,
  };
}

/**
 * Riga di tabella attivabile anche da tastiera: `<tr className="clickable" {...rowActivate(() => pick(r))}>`.
 * Tab la raggiunge, Invio o Spazio fanno come il clic (il focus è visibile: `.table tr.clickable:focus-visible`).
 */
export function rowActivate(onActivate: () => void) {
  return {
    tabIndex: 0,
    onClick: onActivate,
    onKeyDown: (e: RKeyboardEvent<HTMLElement>) => {
      if (e.target !== e.currentTarget || (e.key !== "Enter" && e.key !== " ")) return;
      e.preventDefault();
      onActivate();
    },
  };
}

/** Griglia etichetta/valore per i dettagli espansi. */
export function DetailGrid({ items }: { items: { label: ReactNode; value: ReactNode }[] }) {
  return (
    <div className="detail-grid">
      {items.map((it, i) => (
        <div key={i}>
          <span>{it.label}</span>
          <span>{it.value}</span>
        </div>
      ))}
    </div>
  );
}

/**
 * Pannello di dettaglio dell'elemento selezionato (residuo, interazione, tratto, coppia): titolo, stato, comandi a
 * destra, "Chiudi" (`closeLabel` lo descrive) e i campi, di solito una DetailGrid. Stile comune `.detail-panel`.
 */
export function DetailPanel({ title, label, badges, actions, onClose, closeLabel = "Chiudi il dettaglio", children }: {
  title: ReactNode;
  /** nome accessibile della sezione */
  label: string;
  badges?: ReactNode;
  actions?: ReactNode;
  onClose?: () => void;
  closeLabel?: string;
  children: ReactNode;
}) {
  return (
    <section className="detail-panel" aria-label={label}>
      <div className="detail-panel-head">
        <b>{title}</b>
        {badges}
        <div className="right">
          {actions}
          {onClose && (
            <button type="button" className="btn btn-sm btn-ghost btn-icon" onClick={onClose} aria-label={closeLabel} title="Chiudi">
              <Icon name="x" size={14} />
            </button>
          )}
        </div>
      </div>
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

export function StatusBadge({ status }: { status: string }) {
  const tone = JOB_STATUS_TONE[status] ?? "";
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

/** Indicatore di attesa, annunciato ai lettori di schermo (`label`: cosa si sta caricando). */
export function Spinner({ label = "Caricamento" }: { label?: string }) {
  return <span className="spinner" role="status" aria-label={label} />;
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

const STAT_VERDICT: Record<"ok" | "warn" | "err", string> = { ok: "buono", warn: "da controllare", err: "problematico" };

/**
 * Valore riassuntivo. `detail` aggiunge "Dettagli" sotto il valore: spiegazione, soglie, valori grezzi.
 * `tone` colora il bordo sinistro secondo l'esito e, per ok/warn/err, scrive l'esito accanto all'etichetta con
 * un'icona di forma diversa (non solo il colore); `verdict` lo sostituisce con una parola del dominio, `false` lo toglie.
 */
export function Stat({ label, value, note, detail, tone, verdict }: {
  label: string;
  value: ReactNode;
  note?: ReactNode;
  detail?: ReactNode;
  tone?: "ok" | "warn" | "err" | "info" | "accent";
  verdict?: string | false;
}) {
  const [open, setOpen] = useState(false);
  const v = verdict === false ? null : verdict ?? (tone === "ok" || tone === "warn" || tone === "err" ? STAT_VERDICT[tone] : null);
  return (
    <div className={`stat${tone ? ` stat-${tone}` : ""}`}>
      <div className="label">
        {label}
        {v && (
          <span className="stat-verdict">
            <Icon name={tone === "ok" ? "check" : tone === "warn" || tone === "err" ? "alert" : "info"} size={12} />
            <span className="sr-only">esito: </span>
            {v}
          </span>
        )}
      </div>
      <div className="value">{value}</div>
      {note && <div className="note">{note}</div>}
      {detail && (
        <button type="button" className="stat-more" aria-expanded={open} aria-label={`${open ? "Meno dettagli" : "Dettagli"}: ${label}`} onClick={() => setOpen((o) => !o)}>
          {open ? "Meno dettagli" : "Dettagli"} <Chevron open={open} size={12} />
        </button>
      )}
      {detail && open && <div className="stat-detail">{detail}</div>}
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

const SEGMENT_STEP: Record<string, number> = { ArrowRight: 1, ArrowDown: 1, ArrowLeft: -1, ArrowUp: -1 };

/**
 * Scelta fra poche opzioni (gruppo di radio). `label` dà il nome al gruppo (es. "Metrica"): serve quando ci sono più
 * gruppi vicini. Come per i radio, il gruppo è una sola tappa del Tab e le frecce (Home/Fine) cambiano la scelta.
 */
export function Segmented<T extends string>({ value, options, onChange, label }: { value: T; options: { value: T; label: string }[]; onChange: (v: T) => void; label?: string }) {
  const current = options.findIndex((o) => o.value === value);
  const onKeyDown = (e: RKeyboardEvent<HTMLDivElement>) => {
    const from = Math.max(0, current);
    const n = options.length;
    if (!n) return;
    let next: number;
    if (e.key in SEGMENT_STEP) next = (from + SEGMENT_STEP[e.key] + n) % n;
    else if (e.key === "Home") next = 0;
    else if (e.key === "End") next = n - 1;
    else return;
    e.preventDefault();
    onChange(options[next].value);
    (e.currentTarget.children[next] as HTMLElement | undefined)?.focus();
  };
  return (
    <div className="segmented" role="radiogroup" aria-label={label} onKeyDown={onKeyDown}>
      {options.map((o, i) => (
        <button key={o.value} type="button" role="radio" aria-checked={o.value === value} tabIndex={i === Math.max(0, current) ? 0 : -1}
          className={o.value === value ? "on" : ""} onClick={() => onChange(o.value)}>
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
        <button key={t.value} type="button" role="tab" aria-selected={t.value === value} className={t.value === value ? "on" : ""} onClick={() => onChange(t.value)}>
          {t.label}
        </button>
      ))}
    </div>
  );
}

/** Elementi raggiungibili con Tab dentro una finestra (quelli nascosti o con tabindex negativo restano fuori). */
const FOCUSABLE = 'a[href], button:not([disabled]), input:not([disabled]):not([type="hidden"]), select:not([disabled]), textarea:not([disabled]), summary, [tabindex]';
const TEXT_INPUT = new Set(["text", "search", "email", "url", "tel", "password"]);

/** Campo di testo con qualcosa scritto: lì Esc non chiude la finestra (si perderebbe il testo). */
function filledTextField(t: EventTarget | null): t is HTMLInputElement | HTMLTextAreaElement {
  if (t instanceof HTMLTextAreaElement) return t.value !== "";
  return t instanceof HTMLInputElement && TEXT_INPUT.has(t.type) && t.value !== "";
}

/** La finestra in primo piano: con più finestre aperte (es. una conferma sopra un'altra) Esc e Tab valgono solo lì. */
function topmost(back: HTMLElement | null): boolean {
  const all = document.querySelectorAll(".modal-back");
  return !!back && all[all.length - 1] === back;
}

/** Tab e Maiusc+Tab girano dentro la finestra. */
function trapTab(e: KeyboardEvent, box: HTMLElement) {
  const items = Array.from(box.querySelectorAll<HTMLElement>(FOCUSABLE)).filter((el) => el.tabIndex >= 0 && el.getClientRects().length > 0);
  e.preventDefault();
  if (!items.length) {
    box.focus();
    return;
  }
  const at = items.indexOf(document.activeElement as HTMLElement);
  const next = at < 0 ? (e.shiftKey ? items.length - 1 : 0) : (at + (e.shiftKey ? -1 : 1) + items.length) % items.length;
  items[next].focus();
}

/**
 * Finestra modale; `size="wide"` più larga, `size="full"` occupa quasi tutto lo schermo (grafici espansi), `tools` va
 * nell'intestazione. All'apertura il focus entra nella finestra (sul primo elemento con autoFocus, altrimenti sulla
 * finestra), Tab resta dentro, alla chiusura il focus torna dov'era (es. al pulsante "Espandi"). Esc chiude, tranne in
 * un campo di testo non vuoto: lì il primo Esc esce dal campo (quello di ricerca si svuota da sé).
 */
export function Modal({ title, onClose, children, footer, size = "default", tools }: {
  title: ReactNode;
  onClose: () => void;
  children: ReactNode;
  footer?: ReactNode;
  size?: "default" | "wide" | "full";
  tools?: ReactNode;
}) {
  const titleId = useId();
  const back = useRef<HTMLDivElement>(null);
  const box = useRef<HTMLDivElement>(null);
  const close = useRef(onClose);
  close.current = onClose;
  useEffect(() => {
    const prev = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    if (box.current && !box.current.contains(document.activeElement)) box.current.focus();
    const onKey = (e: KeyboardEvent) => {
      if (e.defaultPrevented || e.isComposing || !topmost(back.current) || !box.current) return;
      if (e.key === "Tab") {
        trapTab(e, box.current);
        return;
      }
      if (e.key !== "Escape") return;
      if (filledTextField(e.target)) {
        if (!(e.target instanceof HTMLInputElement && e.target.type === "search")) {
          e.preventDefault();
          box.current.focus();
        }
        return;
      }
      e.preventDefault();
      close.current();
    };
    window.addEventListener("keydown", onKey);
    return () => {
      window.removeEventListener("keydown", onKey);
      if (prev?.isConnected) prev.focus();
    };
  }, []);
  return (
    <div className="modal-back" ref={back} onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div className={`modal ${size === "default" ? "" : size}`} role="dialog" aria-modal="true" aria-labelledby={titleId} tabIndex={-1} ref={box}>
        <div className="modal-head">
          <h2 id={titleId}>{title}</h2>
          {tools && <div className="modal-tools">{tools}</div>}
          <button className={`btn btn-ghost btn-icon${tools ? "" : " right"}`} onClick={onClose} aria-label="Chiudi" title="Chiudi (Esc)">
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

/** Riquadro per trascinare un file o sceglierlo: clic, oppure Tab e Invio/Spazio, apre la scelta del file. */
export function DropZone({ accept, onFile, label }: { accept: string; onFile: (f: File) => void; label: ReactNode }) {
  const input = useRef<HTMLInputElement>(null);
  const [over, setOver] = useState(false);
  return (
    <div
      className={`drop ${over ? "over" : ""}`}
      role="button"
      {...rowActivate(() => input.current?.click())}
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

/**
 * Copia `text` negli appunti (lib/clipboard: Clipboard API o, senza HTTPS, il ripiego con una textarea). Riuscita:
 * «Copiato» per un momento; non riuscita: «Non copiato» sul pulsante e, dentro l'app, una notifica d'errore.
 */
export function CopyButton({ text, label = "Copia", disabled = false }: { text: string; label?: string; disabled?: boolean }) {
  const [state, setState] = useState<"idle" | "done" | "failed">("idle");
  const toast = useOptionalToast();
  const timer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  const mounted = useRef(true);
  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
      clearTimeout(timer.current);
    };
  }, []);
  const show = (next: "done" | "failed") => {
    if (!mounted.current) return;
    setState(next);
    clearTimeout(timer.current);
    timer.current = setTimeout(() => setState("idle"), next === "done" ? 1500 : 4000);
  };
  return (
    <button
      type="button"
      className="btn btn-sm"
      disabled={disabled}
      title={state === "failed" ? COPY_FAILED : undefined}
      onClick={() => {
        copyText(text).then(
          () => show("done"),
          (e) => {
            show("failed");
            toast?.error(e);
          },
        );
      }}
    >
      <Icon name={state === "done" ? "check" : state === "failed" ? "alert" : "copy"} size={14} />{" "}
      {state === "done" ? "Copiato" : state === "failed" ? "Non copiato" : label}
    </button>
  );
}
