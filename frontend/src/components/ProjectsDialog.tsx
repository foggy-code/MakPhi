import { useCallback, useEffect, useRef, useState, type FormEvent, type MouseEvent } from "react";
import { useNavigate } from "react-router-dom";
import { api, ApiError } from "../api/client";
import type { EntityLabels, Project, ProjectUsage } from "../api/types";
import {
  bytesText, deleteBlockedReason, deleteProjectFlow, nameMatches, needsTypedName, plural, type ProjectFlowResult, progressText,
  projectDeleteToast,
} from "../lib/cleanup";
import { useProject, useToast } from "../lib/context";
import { relTime } from "../lib/format";
import { useAsync } from "../lib/hooks";
import { favoritesFirst, FILTER_ALL, filterParams, type LabelFilter } from "../lib/tags";
import { Icon } from "./Icon";
import { FavoriteStar, LabelFilterSelect, TagChips } from "./Labels";
import { Alert, Empty, Field, Modal, Spinner } from "./ui";

/** Cestino della riga di un progetto: apre la conferma senza selezionare la riga. */
export function ProjectDeleteButton({ name, onClick }: { name: string; onClick: () => void }) {
  return (
    <button type="button" className="btn btn-sm btn-ghost" aria-label={`Elimina il progetto ${name}`}
      title="Elimina il progetto con sequenze, documenti, job e file"
      onClick={(e: MouseEvent) => { e.stopPropagation(); onClick(); }}>
      <Icon name="trash" size={13} />
    </button>
  );
}

export interface ProjectDeleteViewProps {
  project: Project;
  /** null mentre si legge (o se la lettura è fallita: `usageError`) */
  usage: ProjectUsage | null;
  usageError?: string;
  /** è l'ultimo progetto: BioLoom ne creerà uno vuoto */
  isLast: boolean;
  typed: string;
  onTyped: (text: string) => void;
  busy: boolean;
  /** avanzamento dello svuotamento («Eliminati 120 di 840 job…») */
  progress?: string;
  error?: string;
  onConfirm: () => void;
  onCancel: () => void;
  /** solo durante lo svuotamento */
  onStop?: () => void;
  onGoToJobs: () => void;
}

/** Cosa si elimina, oltre al contenuto: job con stella, con etichette e pipeline riprendibili (nessuno si salva). */
function includedNote(u: ProjectUsage): string | null {
  const parts = [
    u.jobs.starred > 0 ? plural(u.jobs.starred, "job con la stella", "job con la stella") : "",
    u.jobs.tagged > 0 ? plural(u.jobs.tagged, "job con etichette", "job con etichette") : "",
    u.jobs.resumable > 0 ? plural(u.jobs.resumable, "pipeline riprendibile", "pipeline riprendibili") : "",
  ].filter(Boolean);
  if (!parts.length) return null;
  const list = parts.length > 1 ? `${parts.slice(0, -1).join(", ")} e ${parts[parts.length - 1]}` : parts[0];
  return `Compresi ${list}.`;
}

/**
 * Conferma dell'eliminazione di un progetto, presentazionale (provata con il markup statico). Con job attivi non si
 * può confermare (motivo visibile e «Vai ai job»); un progetto vuoto si conferma con un clic; altrimenti si scrive il
 * nome. Il bottone distruttivo non ha mai il focus iniziale.
 */
export function ProjectDeleteView(p: ProjectDeleteViewProps) {
  const u = p.usage;
  const blocked = u ? deleteBlockedReason(u) : null;
  const typedNeeded = !!u && !blocked && needsTypedName(u);
  const canConfirm = !!u && !blocked && !p.busy && (!typedNeeded || nameMatches(p.typed, p.project.name));
  const submit = (e: FormEvent) => {
    e.preventDefault();
    if (canConfirm) p.onConfirm();
  };
  return (
    <form className="stack" onSubmit={submit}>
      <div>
        <div style={{ fontWeight: 650, fontSize: 15 }}>{p.project.name}</div>
        <div className="tiny muted">Creato {relTime(p.project.created_at)} · modificato {relTime(p.project.updated_at)}</div>
      </div>
      {!u ? (
        p.usageError ? <Alert tone="err">{p.usageError}</Alert> : <div className="row small muted"><Spinner /> Lettura del contenuto del progetto…</div>
      ) : (
        <div className="stack-sm">
          <div>
            {plural(u.sequences, "sequenza", "sequenze")}, {plural(u.documents, "documento", "documenti")}, {plural(u.jobs.total, "job", "job")},{" "}
            {bytesText(u.bytes, u.bytes_complete)}
          </div>
          <p className="small text-2">
            Verranno eliminati per sempre il progetto, le sue sequenze e i suoi documenti, i suoi job con tutti i file (modelli, risultati, log),
            le etichette e le stelle. Non si può annullare.
          </p>
          {includedNote(u) && <p className="small text-2">{includedNote(u)}</p>}
          <p className="tiny muted">
            I file caricati per l'analisi non appartengono ai progetti: si eliminano da soli dopo i giorni scelti nelle Impostazioni.
          </p>
        </div>
      )}
      {p.isLast && <Alert tone="info">È l'ultimo progetto: BioLoom ne creerà uno vuoto, perché ne serve sempre almeno uno.</Alert>}
      {blocked && (
        <Alert tone="warn">
          <div className="stack-sm">
            <span>{blocked}</span>
            <button type="button" className="btn btn-sm" style={{ justifySelf: "start" }} onClick={p.onGoToJobs} autoFocus>
              <Icon name="activity" size={13} /> Vai ai job
            </button>
          </div>
        </Alert>
      )}
      {typedNeeded && (
        <Field label="Scrivi il nome del progetto" hint={<>Per confermare scrivi «{p.project.name}».</>}>
          <input className="input" value={p.typed} onChange={(e) => p.onTyped(e.target.value)} disabled={p.busy}
            autoFocus autoComplete="off" spellCheck={false} />
        </Field>
      )}
      {p.error && <div role="alert"><Alert tone="err">{p.error}</Alert></div>}
      {p.busy && (
        <div className="row small" aria-live="polite">
          <Spinner /> <span>{p.progress ?? "Eliminazione in corso…"}</span>
        </div>
      )}
      <div className="row" style={{ justifyContent: "flex-end" }}>
        {p.busy && p.onStop && (
          <button type="button" className="btn" onClick={p.onStop}><Icon name="stop" size={12} /> Interrompi</button>
        )}
        {/* focus iniziale su «Annulla»; il campo del nome o «Vai ai job», quando compaiono, lo prendono loro */}
        <button type="button" className="btn" onClick={p.onCancel} disabled={p.busy} autoFocus={!typedNeeded && !blocked}>Annulla</button>
        <button type="submit" className="btn btn-danger" disabled={!canConfirm}>
          <Icon name="trash" size={14} /> {p.busy ? "Eliminazione…" : "Elimina il progetto"}
        </button>
      </div>
    </form>
  );
}

type View = { kind: "list" } | { kind: "delete"; project: Project };

/**
 * Elenco dei progetti con stella, etichette e filtro per etichetta (F8b); un clic sceglie il progetto attivo, il
 * cestino apre la conferma dell'eliminazione nello stesso dialogo (niente dialoghi annidati: un Esc li chiuderebbe
 * tutti). L'eliminazione svuota prima il progetto con la pulizia a tempo, poi chiama il DELETE (deleteProjectFlow).
 */
export function ProjectsDialog({ onClose }: { onClose: () => void }) {
  const { projects, projectId, select, reload: reloadProjects } = useProject();
  const toast = useToast();
  const navigate = useNavigate();
  const [filter, setFilter] = useState<LabelFilter>(FILTER_ALL);
  const { data, loading, setData, reload } = useAsync<Project[]>(() => api.projects(filterParams(filter)), [JSON.stringify(filter)]);
  const rows = favoritesFirst(data ?? []);
  const [view, setView] = useState<View>({ kind: "list" });
  const [usage, setUsage] = useState<ProjectUsage | null>(null);
  const [usageError, setUsageError] = useState<string | undefined>();
  const [typed, setTyped] = useState("");
  const [busy, setBusy] = useState(false);
  const [draining, setDraining] = useState(false);
  const [progress, setProgress] = useState<string | undefined>();
  const [error, setError] = useState<string | undefined>();
  const stopRef = useRef(false);
  const running = useRef(false); // un doppio invio del modulo non avvia due eliminazioni
  const mounted = useRef(true);
  const usageSeq = useRef(0);
  const activeId = useRef(projectId);
  activeId.current = projectId;

  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
      stopRef.current = true; // dialogo chiuso a metà svuotamento: il ciclo si ferma alla chiamata successiva
    };
  }, []);

  const patch = (labels: EntityLabels) => {
    setData((data ?? []).map((p) => (p.id === labels.entity_id ? { ...p, tags: labels.tags, favorite: labels.favorite } : p)));
    reloadProjects().catch(() => undefined);
  };

  /** Uso del progetto (all'apertura della conferma e dopo un rifiuto, mai a intervalli); null se non esiste più. */
  const loadUsage = useCallback(async (id: string): Promise<ProjectUsage | null> => {
    const seq = ++usageSeq.current;
    setUsage(null);
    setUsageError(undefined);
    try {
      const u = await api.projectUsage(id);
      if (mounted.current && seq === usageSeq.current) setUsage(u);
      return u;
    } catch (e) {
      if (mounted.current && seq === usageSeq.current) setUsageError(e instanceof Error ? e.message : String(e));
      if (e instanceof ApiError && e.status === 404) return null;
      throw e;
    }
  }, []);

  const openDelete = (project: Project) => {
    setView({ kind: "delete", project });
    setTyped("");
    setError(undefined);
    setProgress(undefined);
    loadUsage(project.id).catch(() => undefined);
  };

  const backToList = () => {
    usageSeq.current++;
    setView({ kind: "list" });
    setUsage(null);
    setError(undefined);
  };

  /** Il progetto non c'è più: elenchi riletti e, se era quello attivo, si torna alla Panoramica del nuovo attivo. */
  const afterGone = async (wasActive: boolean) => {
    await reloadProjects().catch(() => undefined);
    if (wasActive) navigate("/"); // il provider sta fuori dal router: la navigazione spetta al dialogo
    if (!mounted.current) return;
    backToList();
    reload();
  };

  const confirm = async () => {
    if (view.kind !== "delete" || !usage || busy || running.current) return;
    const project = view.project;
    const wasActive = project.id === activeId.current;
    running.current = true;
    stopRef.current = false;
    setBusy(true);
    setDraining(usage.jobs.total > 0);
    setError(undefined);
    setProgress(undefined);
    let r: ProjectFlowResult;
    try {
      r = await deleteProjectFlow(project.id, usage.jobs.total, {
        cleanup: api.cleanupJobs,
        remove: api.deleteProject,
        shouldStop: () => stopRef.current,
        onProgress: (t) => {
          if (!mounted.current) return;
          setProgress(progressText(t));
          if (t.outcome) setDraining(false); // svuotato: ora il DELETE, che non si interrompe
        },
      });
    } catch (e) {
      // deleteProjectFlow non rilancia: rete di sicurezza; non si sa se il DELETE sia partito, quindi si verifica
      const text = (e instanceof Error ? e.message : String(e)).trim().replace(/[.\s]+$/, "");
      r = { kind: "unknown", message: `Non so se il progetto sia stato eliminato: ${text}.`, totals: null };
    } finally {
      // il dialogo esce sempre da «busy», qualunque cosa accada
      running.current = false;
      if (mounted.current) {
        setBusy(false);
        setDraining(false);
        setProgress(undefined);
      }
    }
    // toast, elenco dei progetti e navigazione valgono anche a dialogo chiuso; gli stati locali solo se è aperto
    if (r.kind === "deleted") {
      toast.push(projectDeleteToast(project.name, r.result, r.totals), r.result.leftover || r.totals?.leftover ? "warn" : "ok");
      await afterGone(wasActive);
      return;
    }
    if (r.kind === "unknown") {
      // esito incerto (rete, server): si verifica rileggendo, mai una notifica di fallimento
      await reloadProjects().catch(() => undefined);
      let still: ProjectUsage | null | undefined;
      try {
        still = await loadUsage(project.id);
      } catch {
        still = undefined; // ancora irraggiungibile
      }
      if (still === null) {
        toast.push(`Progetto «${project.name}» eliminato.`);
        await afterGone(wasActive);
      } else if (mounted.current) {
        setError(still ? `${r.message} Il progetto è ancora presente: puoi riprovare.` : `${r.message} Ricontrolla l'elenco quando il server risponde.`);
      }
      return;
    }
    // svuotato in parte o rifiutato: messaggio nel dialogo e uso riletto
    if (!mounted.current) {
      toast.push(r.message, "warn");
      reloadProjects().catch(() => undefined);
      return;
    }
    setError(r.message);
    setTyped("");
    reloadProjects().catch(() => undefined); // i conteggi dei job sono cambiati
    reload();
    const still = await loadUsage(project.id).catch(() => undefined);
    if (still === null) {
      toast.push(`Il progetto «${project.name}» non esiste più.`, "info"); // eliminato altrove nel frattempo
      await afterGone(wasActive);
    }
  };

  const stop = () => {
    stopRef.current = true;
    setDraining(false);
    setProgress((text) => (text ? `${text} · interruzione in corso…` : "Interruzione in corso…"));
  };

  // Esc, X e sfondo: durante lo svuotamento chiedono l'interruzione, durante il DELETE non fanno nulla
  const requestClose = () => {
    if (!busy) onClose();
    else if (draining) stop();
  };

  if (view.kind === "delete") {
    const project = view.project;
    return (
      <Modal title="Elimina il progetto" onClose={requestClose}>
        <ProjectDeleteView
          project={project}
          usage={usage}
          usageError={usageError}
          isLast={projects.length <= 1}
          typed={typed}
          onTyped={setTyped}
          busy={busy}
          progress={progress}
          error={error}
          onConfirm={() => void confirm()}
          onCancel={backToList}
          onStop={draining ? stop : undefined}
          onGoToJobs={() => { select(project.id); onClose(); navigate("/jobs"); }}
        />
      </Modal>
    );
  }

  return (
    <Modal title="Progetti" wide onClose={requestClose}>
      <div className="stack">
        <div className="row small">
          <LabelFilterSelect type="project" value={filter} onChange={setFilter} />
          <span className="muted">
            Preferiti in cima · clic su un progetto per renderlo attivo · etichette e stella anche nella Panoramica · cestino per eliminarlo con
            sequenze, documenti e job.
          </span>
        </div>
        {loading && !data ? <Spinner /> : !rows.length ? (
          <Empty icon="folder" title="Nessun progetto con questo filtro" />
        ) : (
          <div className="table-wrap" style={{ maxHeight: "60vh", overflowY: "auto" }}>
            <table className="table">
              <thead>
                <tr>
                  <th style={{ width: 30 }} /><th>Progetto</th><th>Etichette</th><th className="num">Sequenze</th><th className="num">Job</th>
                  <th>Modificato</th><th style={{ width: 40 }} />
                </tr>
              </thead>
              <tbody>
                {rows.map((p) => (
                  <tr key={p.id} className={`clickable${p.id === projectId ? " selected" : ""}`}
                    onClick={() => { select(p.id); onClose(); navigate("/"); }}>
                    <td><FavoriteStar type="project" id={p.id} value={!!p.favorite} onChange={patch} /></td>
                    <td>
                      <b>{p.name}</b> {p.id === projectId && <span className="badge badge-accent">attivo</span>}
                      {p.description && <div className="tiny muted ellipsis" style={{ maxWidth: 380 }}>{p.description}</div>}
                    </td>
                    <td><TagChips tags={p.tags} onPick={(t) => setFilter({ kind: "tag", tag: t })} /></td>
                    <td className="num">{p.sequences ?? "—"}</td>
                    <td className="num">{p.jobs ?? "—"}</td>
                    <td className="tiny muted nowrap">{relTime(p.updated_at)}</td>
                    <td><ProjectDeleteButton name={p.name} onClick={() => openDelete(p)} /></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        <p className="tiny muted"><Icon name="info" size={12} /> Le etichette del progetto attivo si modificano nella Panoramica.</p>
      </div>
    </Modal>
  );
}
