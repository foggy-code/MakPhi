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

export function ToastProvider({ children }: { children: ReactNode }) {
  const [items, setItems] = useState<ToastItem[]>([]);
  const counter = useRef(0);
  const push = useCallback((text: string, tone: ToastTone = "ok") => {
    const id = ++counter.current;
    setItems((prev) => [...prev.slice(-4), { id, tone, text }]);
    setTimeout(() => setItems((prev) => prev.filter((t) => t.id !== id)), tone === "err" ? 8000 : 4000);
  }, []);
  const error = useCallback((e: unknown) => push(e instanceof Error ? e.message : String(e), "err"), [push]);
  const value = useMemo(() => ({ push, error }), [push, error]);
  return (
    <ToastCtx.Provider value={value}>
      {children}
      <div className="toasts" role="status" aria-live="polite">
        {items.map((t) => (
          <div key={t.id} className={`toast toast-${t.tone}`} onClick={() => setItems((p) => p.filter((x) => x.id !== t.id))}>
            {t.text}
          </div>
        ))}
      </div>
    </ToastCtx.Provider>
  );
}

export function useToast(): ToastApi {
  const ctx = useContext(ToastCtx);
  if (!ctx) throw new Error("ToastProvider mancante");
  return ctx;
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
