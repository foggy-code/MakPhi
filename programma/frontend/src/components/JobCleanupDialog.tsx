import { useCallback, useEffect, useId, useMemo, useRef, useState, type ReactNode } from "react";
import { api } from "../api/client";
import type { CleanupPreview } from "../api/types";
import {
  AGE_OPTIONS, buildCleanupRequest, bytesText, CLEANUP_STATUSES, type CleanupCriteria, cleanupRunRequest, type CleanupScope,
  cleanupSummary, cleanupTarget, cleanupTargetKey, type CleanupTotals, completedIncluded, defaultCriteria, dialogPreview, emptyTotals,
  makePreviewLoop, plural, type PreviewLoop, type PreviewState, previewLine, progressFraction, progressText, resetUnderstood, runCleanup,
  STATUS_LABEL, summaryTone, toggleStatus,
} from "../lib/cleanup";
import { useProject } from "../lib/context";
import { fmtDate, fmtInt, KIND_LABEL, STATUS_LABEL as JOB_STATUS_LABEL } from "../lib/format";
import { Icon } from "./Icon";
import { Alert, Field, Modal, Progress, Spinner } from "./ui";

/** Fasi del dialogo: criteri e anteprima, pulizia in corso, interruzione chiesta, riepilogo. */
export type CleanupPhase = "form" | "running" | "stopping" | "done";

export interface CleanupDialogViewProps {
  phase: CleanupPhase;
  criteria: CleanupCriteria;
  onCriteria: (c: CleanupCriteria) => void;
  /** nome del progetto di «Questo progetto» (l'attivo all'apertura), null se non ce n'è */
  projectName: string | null;
  /** il progetto è noto ed è ancora l'attivo: senza, «Questo progetto» è disattivato e la conferma pure */
  projectKnown: boolean;
  /** il progetto attivo è cambiato dopo l'apertura: «Questo progetto» non segue il nuovo, resta disattivato */
  projectChanged?: boolean;
  preview: PreviewState;
  /** l'anteprima mostrata è quella dei criteri attuali (altrimenti è attenuata) */
  current: boolean;
  canRun: boolean;
  totals: CleanupTotals | null;
  onRun: () => void;
  onStop: () => void;
  onClose: () => void;
  onRetry: () => void;
}

const legendStyle = { fontSize: 12.5, fontWeight: 550, color: "var(--text-2)" } as const;

/** Etichetta del bottone di conferma: «Elimina 42 job (1,4 MB)» con l'anteprima dei criteri attuali. */
function runLabel(data: CleanupPreview | null): string {
  return data && data.matched > 0 ? `Elimina ${fmtInt(data.matched)} job (${bytesText(data.bytes, data.bytes_complete)})` : "Elimina i job";
}

/** Avanzamento della pulizia: barra accessibile e testo in una regione annunciata. */
export function CleanupProgress({ totals, stopping }: { totals: CleanupTotals | null; stopping: boolean }) {
  const t = totals ?? emptyTotals();
  return (
    <div className="stack-sm">
      <Progress value={progressFraction(t)} indeterminate={t.total === null} label="Avanzamento della pulizia" />
      <div className="small" aria-live="polite">
        <div>{progressText(t)}</div>
        {stopping && <div className="muted">Interruzione in corso…</div>}
      </div>
      <p className="tiny muted">La pulizia procede a blocchi di pochi secondi: «Interrompi» la ferma alla fine del blocco in corso.</p>
    </div>
  );
}

/** Contenuto dell'anteprima: conteggi, esclusioni e i primi job che verrebbero eliminati. */
function PreviewBody({ preview, criteria: c, projectKnown, projectChanged, onRetry }:
  Pick<CleanupDialogViewProps, "preview" | "criteria" | "projectKnown" | "projectChanged" | "onRetry">) {
  if (preview.status === "idle") {
    const hint = !c.statuses.length
      ? "Scegli almeno uno stato da eliminare."
      : c.scope === "project" && projectChanged
        ? "Il progetto attivo è cambiato dopo l'apertura: chiudi e riapri il dialogo per pulire quello nuovo, oppure scegli «Tutti i progetti»."
        : c.scope === "project" && !projectKnown
          ? "Nessun progetto attivo: scegli «Tutti i progetti» o un progetto nella barra in alto."
          : "";
    return <p className="small muted">{hint}</p>;
  }
  if (preview.status === "error") {
    return (
      <Alert tone="err">
        <div className="row" style={{ justifyContent: "space-between" }}>
          <span>Anteprima non disponibile: {preview.message}</span>
          <button type="button" className="btn btn-sm" onClick={onRetry}><Icon name="refresh" size={13} /> Riprova</button>
        </div>
      </Alert>
    );
  }
  if (preview.status !== "ready") {
    return <div className="row small muted"><Spinner /> Calcolo dell'anteprima…</div>;
  }
  const d = preview.data;
  const byStatus = CLEANUP_STATUSES.filter((s) => d.by_status?.[s])
    .map((s) => plural(d.by_status[s], JOB_STATUS_LABEL[s].toLowerCase(), STATUS_LABEL[s].toLowerCase())); // «1 annullato», «2 falliti»
  return (
    <div className="stack-sm">
      <div style={{ fontWeight: 650 }}>
        {d.matched ? previewLine(d) : c.olderThanDays !== null ? "Nessun job corrisponde: prova con «qualsiasi età»" : "Nessun job corrisponde ai criteri"}
      </div>
      {d.matched > 0 && byStatus.length > 1 && <div className="small text-2">{byStatus.join(" · ")}</div>}
      {d.resumable > 0 && (
        <div className="small text-2">{plural(d.resumable, "pipeline riprendibile esclusa", "pipeline riprendibili escluse")}</div>
      )}
      {d.protected > 0 && <div className="small text-2">{plural(d.protected, "conservato", "conservati")} per la stella o le etichette</div>}
      {d.matched > 0 && d.sample.length > 0 && (
        <div>
          <div className="tiny muted">I primi da eliminare, dal più vecchio:</div>
          <ul className="small" style={{ margin: "4px 0 0", paddingLeft: 18 }}>
            {d.sample.map((s) => (
              <li key={s.id}>
                {s.title || s.id}{" "}
                <span className="tiny muted">
                  · {KIND_LABEL[s.kind as keyof typeof KIND_LABEL] ?? s.kind} · {JOB_STATUS_LABEL[s.status] ?? s.status} · {fmtDate(s.finished_at)}
                </span>
              </li>
            ))}
          </ul>
          {d.matched > d.sample.length && <div className="tiny muted">… e altri {fmtInt(d.matched - d.sample.length)}</div>}
        </div>
      )}
    </div>
  );
}

/**
 * Dialogo della pulizia dei job, presentazionale (tutto via props, provato con il markup statico): criteri, anteprima,
 * conferma; durante l'esecuzione l'avanzamento con «Interrompi»; alla fine il riepilogo.
 */
export function CleanupDialogView(p: CleanupDialogViewProps) {
  const statusLegend = useId();
  const keepLegend = useId();
  const c = p.criteria;
  const set = p.onCriteria;
  const ready = p.preview.status === "ready" && p.current ? p.preview.data : null;
  const stale = !p.current && (p.preview.status === "ready" || p.preview.status === "error");
  const loading = p.preview.status === "waiting" || p.preview.status === "loading";
  let body: ReactNode;
  let footer: ReactNode;
  if (p.phase === "form") {
    body = (
      <div className="stack">
        <p className="small text-2">
          Elimina per sempre i job terminati che corrispondono ai criteri, con tutti i loro file. I job in coda o in esecuzione non si toccano.
        </p>
        <Field label="Ambito">
          <select className="input" value={c.scope} onChange={(e) => set({ ...c, scope: e.target.value as CleanupScope })}>
            <option value="project" disabled={!p.projectKnown}>
              {p.projectKnown && p.projectName ? `Questo progetto (${p.projectName})`
                : p.projectChanged ? `Questo progetto (${p.projectName ? `${p.projectName}, non più attivo` : "cambiato dopo l'apertura"})`
                : "Questo progetto (nessuno attivo)"}
            </option>
            <option value="all">Tutti i progetti</option>
          </select>
        </Field>
        <div className="grid grid-2">
          <Field label="Età">
            <select className="input" value={c.olderThanDays === null ? "" : String(c.olderThanDays)}
              onChange={(e) => set({ ...c, olderThanDays: e.target.value ? Number(e.target.value) : null })}>
              {AGE_OPTIONS.map((o) => <option key={o.label} value={o.value === null ? "" : String(o.value)}>{o.label}</option>)}
            </select>
          </Field>
          <Field label="Tipo">
            <select className="input" value={c.kind ?? ""} onChange={(e) => set({ ...c, kind: e.target.value || null })}>
              <option value="">Tutti i tipi</option>
              {Object.entries(KIND_LABEL).map(([k, l]) => <option key={k} value={k}>{l}</option>)}
            </select>
          </Field>
        </div>
        <div role="group" aria-labelledby={statusLegend} className="stack-sm">
          <div id={statusLegend} style={legendStyle}>Stati da eliminare</div>
          <div className="row">
            {CLEANUP_STATUSES.map((s) => (
              <label key={s} className="check">
                <input type="checkbox" checked={c.statuses.includes(s)} onChange={() => set(toggleStatus(c, s))} /> {STATUS_LABEL[s]}
              </label>
            ))}
          </div>
          <label className="check">
            <input type="checkbox" checked={c.includeResumable} onChange={(e) => set({ ...c, includeResumable: e.target.checked })} />
            Includi anche le pipeline riprendibili
          </label>
          <span className="tiny muted">
            Pipeline fallite o annullate con elementi già completati: escluse, se non le includi, perché si possono ancora riprendere.
          </span>
        </div>
        <div role="group" aria-labelledby={keepLegend} className="stack-sm">
          <div id={keepLegend} style={legendStyle}>Conserva comunque</div>
          <label className="check">
            <input type="checkbox" checked={c.keepFavorites} onChange={(e) => set({ ...c, keepFavorites: e.target.checked })} /> I job con la stella
          </label>
          <label className="check">
            <input type="checkbox" checked={c.keepTagged} onChange={(e) => set({ ...c, keepTagged: e.target.checked })} /> I job con etichette
          </label>
          <span className="tiny muted">Le etichette contano solo come «conserva»: il filtro per etichetta della pagina Job qui non vale.</span>
        </div>
        {completedIncluded(c) && (
          <div className="stack-sm">
            <Alert tone="warn">
              Include i job completati: modelli e risultati verranno eliminati per sempre; i job che li usano come sorgente non potranno più essere
              ripresi né aperti nel visualizzatore.
            </Alert>
            <label className="check">
              <input type="checkbox" checked={c.understood} onChange={(e) => set({ ...c, understood: e.target.checked })} /> Ho capito
            </label>
          </div>
        )}
        <section className="card flat" aria-label="Anteprima della pulizia" aria-busy={loading || stale} aria-live="polite"
          style={{ opacity: stale ? 0.55 : 1, transition: "opacity 0.1s" }}>
          <PreviewBody preview={p.preview} criteria={c} projectKnown={p.projectKnown} projectChanged={p.projectChanged} onRetry={p.onRetry} />
        </section>
      </div>
    );
    footer = (
      <>
        <button type="button" className="btn" onClick={p.onClose}>Annulla</button>
        <button type="button" className="btn btn-danger" disabled={!p.canRun} onClick={p.onRun}>
          <Icon name="trash" size={14} /> {runLabel(ready)}
        </button>
      </>
    );
  } else if (p.phase === "done") {
    const t = p.totals ?? emptyTotals();
    body = (
      <div role={t.outcome === "error" ? "alert" : "status"}>
        <Alert tone={summaryTone(t)}>{cleanupSummary(t)}</Alert>
      </div>
    );
    footer = <button type="button" className="btn btn-primary" onClick={p.onClose} autoFocus>Chiudi</button>;
  } else {
    body = <CleanupProgress totals={p.totals} stopping={p.phase === "stopping"} />;
    // «Interrompi» prende il posto della conferma: il secondo clic di un doppio clic su «Elimina» (detail 2) non lo preme
    footer = (
      <button type="button" className="btn" onClick={(e) => e.detail <= 1 && p.onStop()} disabled={p.phase === "stopping"} autoFocus>
        <Icon name="stop" size={12} /> Interrompi
      </button>
    );
  }
  return (
    <Modal title="Pulisci job" onClose={p.onClose} footer={footer}>
      {body}
    </Modal>
  );
}

/**
 * Pulizia dei job (pagina Job, Impostazioni → Spazio): i criteri ripartono dai valori sicuri a ogni apertura (niente
 * localStorage). «Questo progetto» è il progetto attivo all'apertura: se intanto l'attivo cambia, l'ambito diventa non
 * disponibile invece di passare all'altro, e «Ho capito» si azzera a ogni cambio di destinazione. L'esecuzione usa
 * l'`as_of` dell'anteprima confermata. Durante l'esecuzione Esc, la X e lo sfondo non chiudono: chiedono
 * l'interruzione, che arriva alla fine della chiamata in corso, e il dialogo resta aperto fino al riepilogo.
 * `onCleaned` parte una volta per esecuzione, qualunque esito; se il dialogo si smonta (cambio di pagina) il ciclo si
 * ferma alla chiamata successiva.
 */
export function JobCleanupDialog({ onClose, onCleaned, initial }: {
  onClose: () => void;
  onCleaned: (totals: CleanupTotals) => void;
  initial?: { scope?: CleanupScope; kind?: string | null };
}) {
  const { project, projectId } = useProject();
  const [criteria, setCriteria] = useState<CleanupCriteria>(() => defaultCriteria(initial));
  const [phase, setPhase] = useState<CleanupPhase>("form");
  const [loopState, setLoopState] = useState<PreviewState>({ status: "idle" });
  const [lastReady, setLastReady] = useState<PreviewState | null>(null);
  const [totals, setTotals] = useState<CleanupTotals | null>(null);
  // progetto fissato all'apertura (il nome si ricorda appena è noto, per mostrarlo anche quando non è più l'attivo)
  const [pinnedId] = useState(projectId);
  const [pinnedName, setPinnedName] = useState<string | null>(project && project.id === projectId ? project.name : null);
  const loop = useRef<PreviewLoop | null>(null);
  const stopRef = useRef(false);
  const started = useRef(false); // una sola esecuzione per apertura, anche con due clic nello stesso render
  const mounted = useRef(true);
  const cleaned = useRef(onCleaned);
  cleaned.current = onCleaned;

  const target = cleanupTarget({ id: pinnedId, name: pinnedName }, { projectId, project });
  if (target.id && target.name && target.name !== pinnedName) setPinnedName(target.name);
  // destinazione cambiata (ambito scelto o progetto non più attivo): «Ho capito» si azzera prima che la conferma valga
  const targetKey = cleanupTargetKey(criteria.scope, target.id);
  const [ackTarget, setAckTarget] = useState(targetKey);
  if (ackTarget !== targetKey) {
    setAckTarget(targetKey);
    setCriteria((c) => resetUnderstood(c, ackTarget, targetKey));
  }
  const request = useMemo(() => buildCleanupRequest(criteria, target.id), [criteria, target.id]);
  const { key, shown, current, canRun } = dialogPreview({
    request, loop: loopState, lastReady, completed: completedIncluded(criteria), understood: criteria.understood,
  });

  useEffect(() => {
    mounted.current = true;
    const l = makePreviewLoop({
      call: api.cleanupPreview,
      onState: (s) => {
        setLoopState(s);
        if (s.status === "ready") setLastReady(s);
      },
    });
    loop.current = l;
    return () => {
      mounted.current = false;
      stopRef.current = true; // smontato a metà esecuzione: il ciclo si ferma alla chiamata successiva
      l.stop();
      loop.current = null;
    };
  }, []);

  useEffect(() => {
    if (phase === "form") loop.current?.request(request);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key, phase]);

  const run = async () => {
    // i criteri attuali con l'as_of dell'anteprima confermata: un job finito dopo l'anteprima non si elimina
    const confirmed = cleanupRunRequest(request, loopState);
    if (!confirmed || !canRun || phase !== "form" || started.current) return;
    started.current = true;
    stopRef.current = false;
    setTotals(null);
    setPhase("running");
    const seen: { last: CleanupTotals | null } = { last: null };
    let t: CleanupTotals;
    try {
      t = await runCleanup(confirmed, api.cleanupJobs, {
        shouldStop: () => stopRef.current,
        onProgress: (progress) => {
          seen.last = progress;
          if (mounted.current) setTotals(progress);
        },
      });
    } catch (e) {
      // runCleanup non rilancia: rete di sicurezza, un'eccezione diventa l'esito «error» con i totali visti fin lì
      t = { ...(seen.last ?? emptyTotals()), outcome: "error", error: e instanceof Error ? e.message : String(e) };
    }
    // il dialogo esce sempre da «running»/«stopping» e onCleaned parte una volta, anche se lancia un'eccezione
    try {
      cleaned.current(t);
    } finally {
      if (mounted.current) {
        setTotals(t);
        setPhase("done");
      }
    }
  };

  const stop = useCallback(() => {
    stopRef.current = true;
    setPhase((ph) => (ph === "running" ? "stopping" : ph));
  }, []);

  const requestClose = useCallback(() => {
    if (phase === "running") stop();
    else if (phase !== "stopping") onClose();
  }, [phase, stop, onClose]);

  return (
    <CleanupDialogView
      phase={phase}
      criteria={criteria}
      onCriteria={setCriteria}
      projectName={target.name}
      projectKnown={!!target.id}
      projectChanged={target.changed}
      preview={shown}
      current={current}
      canRun={canRun}
      totals={totals}
      onRun={() => void run()}
      onStop={stop}
      onClose={requestClose}
      onRetry={() => loop.current?.retry()}
    />
  );
}
