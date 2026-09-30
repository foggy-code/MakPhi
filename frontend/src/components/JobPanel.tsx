import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import type { JobSummary, ResumeItems, ResumeScope } from "../api/types";
import { fmtDuration, isActive, KIND_LABEL } from "../lib/format";
import { resumeOptions } from "../lib/status";
import { Icon } from "./Icon";
import { Alert, Progress, StatusBadge } from "./ui";

interface Props {
  summary: JobSummary | null;
  log?: string;
  onCancel?: () => void;
  /** stato di ripresa del job completo (pipeline fallite o annullate) */
  resume?: { resumable?: boolean; resumable_items?: ResumeItems | null } | null;
  onResume?: (scope: ResumeScope) => Promise<void> | void;
  compact?: boolean;
  defaultLogOpen?: boolean;
}

/** Stato di un job: avanzamento, fase corrente, errore, log in tempo reale, annullamento e ripresa. */
export function JobPanel({ summary, log, onCancel, resume, onResume, compact = false, defaultLogOpen = false }: Props) {
  const [showLog, setShowLog] = useState(defaultLogOpen);
  const [resuming, setResuming] = useState(false);
  const logRef = useRef<HTMLPreElement>(null);
  useEffect(() => {
    if (logRef.current) logRef.current.scrollTop = logRef.current.scrollHeight;
  }, [log, showLog]);
  if (!summary) return null;
  const running = isActive(summary.status);
  const options = onResume ? resumeOptions(summary.status, resume) : null;
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
    <div className="card flat stack">
      <div className="row">
        <StatusBadge status={summary.status} />
        <b className="ellipsis" style={{ maxWidth: 520 }}>{summary.title || KIND_LABEL[summary.kind]}</b>
        <span className="muted small nowrap">{fmtDuration(summary.started_at ?? summary.created_at, summary.finished_at)}</span>
        <div className="right row">
          {log !== undefined && (
            <button className="btn btn-sm btn-ghost" onClick={() => setShowLog(!showLog)}>
              <Icon name="file" size={14} /> {showLog ? "Nascondi log" : "Log"}
            </button>
          )}
          {!compact && (
            <Link className="btn btn-sm btn-ghost" to={`/jobs?id=${summary.id}`}>
              Dettagli
            </Link>
          )}
          {running && onCancel && (
            <button className="btn btn-sm btn-danger" onClick={onCancel} disabled={summary.status === "cancelling"}>
              <Icon name="stop" size={12} /> Annulla
            </button>
          )}
        </div>
      </div>
      {running && (
        <div className="stack-sm">
          <Progress value={summary.progress} indeterminate={summary.progress < 0.03} />
          <span className="small text-2">{summary.stage}</span>
        </div>
      )}
      {summary.status === "failed" && summary.error && (
        <Alert tone="err">
          <pre className="mono" style={{ margin: 0, whiteSpace: "pre-wrap" }}>{summary.error}</pre>
        </Alert>
      )}
      {options && (
        <div className="row">
          <span className="small text-2">{options.text}</span>
          <div className="right row">
            <button className="btn btn-sm btn-primary" disabled={resuming} onClick={() => doResume("incomplete")}
              title="Rielabora le sequenze non concluse; quelle completate restano come sono">
              <Icon name="play" size={12} /> Riprendi
            </button>
            {options.failedOnly && (
              <button className="btn btn-sm" disabled={resuming} onClick={() => doResume("failed")}
                title="Rielabora solo le sequenze fallite; le annullate restano da riprendere">
                <Icon name="refresh" size={12} /> Riprova solo i falliti
              </button>
            )}
          </div>
        </div>
      )}
      {showLog && log !== undefined && (
        <pre className="log" ref={logRef}>{log || "Nessun output ancora."}</pre>
      )}
    </div>
  );
}
