import { useEffect, useMemo, useRef, useState } from "react";
import { Link } from "react-router-dom";
import type { JobSummary, ResumeItems, ResumeScope } from "../api/types";
import { safeFilename } from "../lib/exportImage";
import { downloadText, fmtEta, isActive, KIND_LABEL, plural, queueLabel } from "../lib/format";
import { resumeOptions } from "../lib/status";
import { jobTimes, logLineCount } from "../lib/toolsView";
import { Icon } from "./Icon";
import { ReproButton } from "./ReproButton";
import { Alert, Chevron, CopyButton, Modal, Progress, StatusBadge } from "./ui";
import "../styles/job.css";

interface Props {
  summary: JobSummary | null;
  log?: string;
  onCancel?: () => void;
  /** stato di ripresa del job completo (pipeline fallite, annullate o completate con elementi falliti) */
  resume?: { resumable?: boolean; resumable_items?: ResumeItems | null } | null;
  onResume?: (scope: ResumeScope) => Promise<void> | void;
  compact?: boolean;
  defaultLogOpen?: boolean;
}

/** Testo del log: segue la fine mentre arrivano righe nuove, a meno che si stia leggendo più in alto. */
function LogText({ log, full = false }: { log: string; full?: boolean }) {
  const ref = useRef<HTMLPreElement>(null);
  const follow = useRef(true);
  useEffect(() => {
    const el = ref.current;
    if (el && follow.current) el.scrollTop = el.scrollHeight;
  }, [log]);
  return (
    <pre ref={ref} className={`log${full ? " job-log-full" : ""}`} tabIndex={0} aria-label="Log del job"
      onScroll={(e) => {
        const el = e.currentTarget;
        follow.current = el.scrollHeight - el.scrollTop - el.clientHeight < 32;
      }}>
      {log || "Nessun output ancora."}
    </pre>
  );
}

/** Log del job a scomparsa, con copia, download e vista espansa a tutta altezza. */
function JobLog({ log, title, defaultOpen }: { log: string; title: string; defaultOpen: boolean }) {
  const [open, setOpen] = useState(defaultOpen);
  const [full, setFull] = useState(false);
  const lines = useMemo(() => logLineCount(log), [log]);
  const download = () => downloadText(safeFilename(`log_${title}`, "log"), log);
  const tools = (
    <>
      <CopyButton text={log} label="Copia" />
      <button type="button" className="btn btn-sm btn-ghost" disabled={!log} onClick={download} title="Scarica il log come file di testo">
        <Icon name="download" size={13} /> Scarica
      </button>
    </>
  );
  return (
    <div className="job-log">
      <div className="job-log-head">
        <button type="button" className="job-log-toggle" aria-expanded={open} onClick={() => setOpen((o) => !o)}>
          <Chevron open={open} />
          <Icon name="fileText" size={14} /> Log
          <span className="small">{lines ? plural(lines, "riga", "righe") : "vuoto"}</span>
        </button>
        <div className="right">
          {tools}
          <button type="button" className="btn btn-sm" onClick={() => setFull(true)} title="Apri il log a tutta altezza" aria-haspopup="dialog">
            <Icon name="maximize" size={13} /> Espandi
          </button>
        </div>
      </div>
      {/* un solo <pre> alla volta: con la vista espansa aperta quello della pagina non si aggiorna a ogni riga */}
      {open && !full && <LogText log={log} />}
      {full && (
        <Modal size="full" title={`Log · ${title}`} onClose={() => setFull(false)} tools={tools}>
          <LogText log={log} full />
        </Modal>
      )}
    </div>
  );
}

/** Stato di un job: avanzamento, fase e tempi, errore, log in tempo reale, annullamento e ripresa. */
export function JobPanel({ summary, log, onCancel, resume, onResume, compact = false, defaultLogOpen = false }: Props) {
  const [resuming, setResuming] = useState(false);
  if (!summary) return null;
  const running = isActive(summary.status);
  const eta = running ? fmtEta(summary.eta_seconds) : null;
  const queue = queueLabel(summary);
  const options = onResume ? resumeOptions(summary.status, resume) : null;
  const title = summary.title || KIND_LABEL[summary.kind];
  const indeterminate = summary.progress < 0.03;
  const doResume = async (scope: ResumeScope) => {
    if (!onResume) return;
    setResuming(true);
    try {
      await onResume(scope);
    } finally {
      setResuming(false);
    }
  };
  return (
    <div className="card flat job-panel">
      <div className="job-head">
        <StatusBadge status={summary.status} />
        <b className="job-title" title={title}>{title}</b>
        {summary.title && KIND_LABEL[summary.kind] && <span className="badge">{KIND_LABEL[summary.kind]}</span>}
        <div className="right">
          {!compact && <ReproButton jobId={summary.id} kind={summary.kind} status={summary.status} />}
          {!compact && (
            <Link className="btn btn-sm btn-ghost" to={`/jobs?id=${summary.id}`} title="Parametri, file e log completo del job">
              <Icon name="list" size={13} /> Pagina del job
            </Link>
          )}
          {running && onCancel && (
            <button type="button" className="btn btn-sm btn-danger" onClick={onCancel} disabled={summary.status === "cancelling"}>
              <Icon name="stop" size={12} /> Annulla
            </button>
          )}
        </div>
      </div>
      <div className="job-meta">
        {jobTimes(summary).map((t) => <span key={t.label}>{t.label} <b>{t.value}</b></span>)}
      </div>
      {running && (
        <div className="job-progress">
          <Progress value={summary.progress} indeterminate={indeterminate} />
          <div className="job-progress-line">
            <span className="stage">{summary.stage || (summary.status === "queued" ? "In attesa di uno slot di calcolo" : "In preparazione")}</span>
            {queue && <span title="I calcoli interattivi brevi partono prima di modellazioni e pipeline; a parità, in ordine di creazione">{queue}</span>}
            {eta && <span title="Stima dal ritmo del calcolo finora">{eta}</span>}
            {!indeterminate && <span className="pct">{Math.round(summary.progress * 100)}%</span>}
          </div>
        </div>
      )}
      {summary.status === "failed" && summary.error && (
        <Alert tone="err">
          <div className="stack-sm">
            <b>Il calcolo non è riuscito</b>
            <pre className="job-error">{summary.error}</pre>
            {log !== undefined && <span className="small text-2">Il log qui sotto riporta i passaggi fino all'errore.</span>}
          </div>
        </Alert>
      )}
      {options && (
        <div className="row">
          <span className="small text-2">{options.text}</span>
          <div className="right row">
            <button type="button" className="btn btn-sm btn-primary" disabled={resuming} onClick={() => doResume("incomplete")} title={options.hint}>
              <Icon name={summary.status === "completed" ? "refresh" : "play"} size={12} /> {options.label}
            </button>
            {options.failedOnly && (
              <button type="button" className="btn btn-sm" disabled={resuming} onClick={() => doResume("failed")}
                title="Rielabora solo le sequenze fallite; le annullate restano da riprendere">
                <Icon name="refresh" size={12} /> Riprova solo i falliti
              </button>
            )}
          </div>
        </div>
      )}
      {log !== undefined && <JobLog log={log} title={title} defaultOpen={defaultLogOpen} />}
    </div>
  );
}
