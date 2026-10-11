import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { api } from "../api/client";
import type { Project } from "../api/types";
import { useLocalStorage } from "./hooks";
import { pickProjectId } from "./projectSelection";

// ---------------------------------------------------------------------------------- notifiche
type ToastTone = "ok" | "err" | "info" | "warn";
interface ToastItem {
  id: number;
  tone: ToastTone;
  text: string;
}
interface ToastApi {
  push: (text: string, tone?: ToastTone) => void;
  error: (e: unknown) => void;
}
const ToastCtx = createContext<ToastApi | null>(null);

/** Durata di una notifica (ms): gli errori restano il doppio. */
export const TOAST_MS: Record<ToastTone, number> = { ok: 4000, info: 4000, warn: 4000, err: 8000 };
/** Tempo minimo (ms) dopo che una notifica perde il puntatore o il focus, per finire di leggerla: se ne restava meno,
 *  riparte da qui. */
export const TOAST_RESUME_MS = 2000;

/**
 * Una notifica: si chiude da sola dopo `TOAST_MS`, ma non mentre ha il puntatore sopra o il focus dentro (il pulsante
 * «Chiudi la notifica»), così non sparisce mentre la si legge o la si sta chiudendo (WCAG 2.2.1). Quando li perde
 * riprende con il tempo che restava, almeno `TOAST_RESUME_MS`.
 */
function Toast({ item, onDismiss }: { item: ToastItem; onDismiss: (id: number) => void }) {
  const [hover, setHover] = useState(false);
  const [focus, setFocus] = useState(false);
  const remaining = useRef(TOAST_MS[item.tone]);
  const paused = hover || focus;
  useEffect(() => {
    if (paused) return;
    const started = Date.now();
    const timer = setTimeout(() => onDismiss(item.id), remaining.current);
    // pausa (o notifica chiusa): si conserva il tempo che resta, con il minimo per la ripresa
    return () => {
      clearTimeout(timer);
      remaining.current = Math.max(TOAST_RESUME_MS, remaining.current - (Date.now() - started));
    };
  }, [paused, item.id, onDismiss]);
  return (
    // il clic sulla notifica la chiude come prima; da tastiera c'è il pulsante «Chiudi la notifica» (Tab, Invio)
    <div className={`toast toast-${item.tone}`} onClick={() => onDismiss(item.id)}
      onMouseEnter={() => setHover(true)} onMouseLeave={() => setHover(false)}
      onFocus={() => setFocus(true)}
      onBlur={(e) => { if (!e.currentTarget.contains(e.relatedTarget as Node | null)) setFocus(false); }}>
      <span className="toast-text">{item.text}</span>
      <button type="button" className="toast-close" aria-label="Chiudi la notifica" title="Chiudi"
        onClick={(e) => { e.stopPropagation(); onDismiss(item.id); }}>
        <span aria-hidden="true">×</span>
      </button>
    </div>
  );
}

export function ToastProvider({ children }: { children: ReactNode }) {
  const [items, setItems] = useState<ToastItem[]>([]);
  const counter = useRef(0);
  const push = useCallback((text: string, tone: ToastTone = "ok") => {
    const id = ++counter.current;
    setItems((prev) => [...prev.slice(-4), { id, tone, text }]);
  }, []);
  const error = useCallback((e: unknown) => push(e instanceof Error ? e.message : String(e), "err"), [push]);
  const dismiss = useCallback((id: number) => setItems((p) => p.filter((x) => x.id !== id)), []);
  const value = useMemo(() => ({ push, error }), [push, error]);
  return (
    <ToastCtx.Provider value={value}>
      {children}
      <div className="toasts" role="status" aria-live="polite">
        {items.map((t) => <Toast key={t.id} item={t} onDismiss={dismiss} />)}
      </div>
    </ToastCtx.Provider>
  );
}

export function useToast(): ToastApi {
  const ctx = useContext(ToastCtx);
  if (!ctx) throw new Error("ToastProvider mancante");
  return ctx;
}

/** Notifiche se c'è il ToastProvider, altrimenti null: per i componenti di base usati anche fuori dall'app (CopyButton). */
export function useOptionalToast(): ToastApi | null {
  return useContext(ToastCtx);
}

// ---------------------------------------------------------------------------------- progetto attivo
interface ProjectApi {
  projects: Project[];
  project: Project | null;
  projectId: string | null;
  select: (id: string) => void;
  reload: () => Promise<void>;
  create: (name: string) => Promise<Project>;
}
const ProjectCtx = createContext<ProjectApi | null>(null);

export function ProjectProvider({ children }: { children: ReactNode }) {
  const [projects, setProjects] = useState<Project[]>([]);
  const [projectId, setProjectId] = useLocalStorage<string | null>("bioloom.project", null);
  // id attuale per `reload`: una rilettura partita prima di un cambio di progetto non lavora su un valore vecchio
  const current = useRef(projectId);
  current.current = projectId;

  const reload = useCallback(async () => {
    const list = await api.projects();
    setProjects(list);
    // progetto eliminato (anche da un'altra scheda): il primo rimasto; elenco vuoto: nessuno, mai l'id eliminato
    const next = pickProjectId(list, current.current);
    if (next !== current.current) {
      current.current = next;
      setProjectId(next);
    }
  }, [setProjectId]);

  useEffect(() => {
    reload().catch(() => undefined);
  }, [reload]);

  // scheda di nuovo visibile: l'elenco si rilegge, così non offre un progetto eliminato in un'altra scheda (il progetto
  // attivo non si sincronizza fra le schede)
  useEffect(() => {
    const onVisible = () => {
      if (document.visibilityState === "visible") reload().catch(() => undefined);
    };
    document.addEventListener("visibilitychange", onVisible);
    return () => document.removeEventListener("visibilitychange", onVisible);
  }, [reload]);

  const create = useCallback(
    async (name: string) => {
      const p = await api.createProject(name);
      await reload();
      setProjectId(p.id);
      return p;
    },
    [reload, setProjectId],
  );

  const value = useMemo<ProjectApi>(
    () => ({
      projects,
      project: projects.find((p) => p.id === projectId) ?? null,
      projectId,
      select: setProjectId,
      reload,
      create,
    }),
    [projects, projectId, setProjectId, reload, create],
  );
  return <ProjectCtx.Provider value={value}>{children}</ProjectCtx.Provider>;
}

export function useProject(): ProjectApi {
  const ctx = useContext(ProjectCtx);
  if (!ctx) throw new Error("ProjectProvider mancante");
  return ctx;
}
