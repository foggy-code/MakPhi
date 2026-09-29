import { useEffect, useState } from "react";
import { NavLink, Outlet, useNavigate } from "react-router-dom";
import { api } from "../api/client";
import type { SystemInfo } from "../api/types";
import { useProject, useToast } from "../lib/context";
import { SYSTEM_CHANGED, useInterval, useLocalStorage } from "../lib/hooks";
import { Icon } from "./Icon";
import { Confirm, Modal } from "./ui";

const NAV: { section: string; items: { to: string; label: string; icon: string }[] }[] = [
  { section: "", items: [{ to: "/", label: "Panoramica", icon: "home" }] },
  {
    section: "Flusso di lavoro",
    items: [
      { to: "/sequences", label: "Sequenze", icon: "dna" },
      { to: "/search", label: "Ricerca template", icon: "search" },
      { to: "/templates", label: "Libreria template", icon: "library" },
      { to: "/alignment", label: "Allineamenti", icon: "align" },
      { to: "/pir", label: "Editor PIR", icon: "file" },
      { to: "/modeling", label: "Modellazione", icon: "cube" },
      { to: "/quality", label: "Qualità", icon: "shield" },
    ],
  },
  {
    section: "Automazione",
    items: [
      { to: "/pipeline", label: "Pipeline batch", icon: "layers" },
      { to: "/jobs", label: "Job", icon: "activity" },
    ],
  },
  { section: "Sistema", items: [{ to: "/settings", label: "Impostazioni", icon: "settings" }] },
];

export function Layout() {
  const { projects, projectId, select, create } = useProject();
  const toast = useToast();
  const navigate = useNavigate();
  const [theme, setTheme] = useLocalStorage<"dark" | "light">("bioloom.theme", "dark");
  const [active, setActive] = useState(0);
  const [info, setInfo] = useState<SystemInfo | null>(null);
  const [newProject, setNewProject] = useState<string | null>(null);
  const [askShutdown, setAskShutdown] = useState(false);
  const [offline, setOffline] = useState(false);

  useEffect(() => {
    document.documentElement.dataset.theme = theme;
  }, [theme]);

  useEffect(() => {
    const load = () => api.systemInfo().then(setInfo).catch(() => undefined);
    load();
    // le Impostazioni segnalano i cambi di licenza o configurazione (es. chiave MODELLER inserita)
    window.addEventListener(SYSTEM_CHANGED, load);
    return () => window.removeEventListener(SYSTEM_CHANGED, load);
  }, []);

  useInterval(() => {
    api
      .jobs({ status: "queued,running,cancelling", limit: 100 })
      .then((j) => {
        setActive(j.length);
        setOffline(false);
      })
      .catch(() => setOffline(true));
  }, 3000);

  return (
    <div className="app">
      <aside className="sidebar">
        <div className="brand">
          <img src="/favicon.svg" alt="" />
          <div>
            <b>BioLoom</b>
            <small>v{info?.version ?? "4"} · modellazione per omologia</small>
          </div>
        </div>
        <nav className="nav">
          {NAV.map((group) => (
            <div key={group.section || "root"}>
              {group.section && <div className="nav-section">{group.section}</div>}
              {group.items.map((item) => (
                <NavLink key={item.to} to={item.to} end={item.to === "/"}>
                  <Icon name={item.icon} />
                  {item.label}
                  {item.to === "/jobs" && active > 0 && <span className="count">{active}</span>}
                </NavLink>
              ))}
            </div>
          ))}
        </nav>
        <div className="sidebar-foot">
          <div className="row" title={info?.modeller.python ?? ""}>
            <span className="dot" style={{ color: !info ? "var(--muted)" : info.modeller.available ? "var(--ok)" : "var(--err)" }} />
            MODELLER {!info ? "· verifica…" : info.modeller.available ? info.modeller.version : "non disponibile"}
          </div>
          <div className="row">
            <button className="btn btn-ghost btn-sm" onClick={() => setTheme(theme === "dark" ? "light" : "dark")}>
              <Icon name={theme === "dark" ? "sun" : "moon"} size={14} /> Tema
            </button>
            {info?.runtime?.auth && (
              <button className="btn btn-ghost btn-sm" onClick={() => api.logout().finally(() => window.location.reload())} title="Esci dalla sessione">
                <Icon name="x" size={14} /> Disconnetti
              </button>
            )}
            {info?.runtime?.allow_shutdown !== false && (
              <button className="btn btn-ghost btn-sm" onClick={() => setAskShutdown(true)} title="Arresta BioLoom">
                <Icon name="power" size={14} /> Esci
              </button>
            )}
          </div>
        </div>
      </aside>
      <div className="main">
        <header className="topbar">
          <Icon name="folder" />
          <select
            className="input"
            style={{ width: 260 }}
            value={projectId ?? ""}
            onChange={(e) => select(e.target.value)}
            aria-label="Progetto attivo"
          >
            {projects.map((p) => (
              <option key={p.id} value={p.id}>
                {p.name}
              </option>
            ))}
          </select>
          <button className="btn btn-sm" onClick={() => setNewProject("")}>
            <Icon name="plus" size={14} /> Nuovo progetto
          </button>
          <div className="spacer" />
          {offline && <span className="badge badge-err">Server non raggiungibile</span>}
          {active > 0 && (
            <button className="btn btn-sm" onClick={() => navigate("/jobs")}>
              <span className="spinner" style={{ width: 12, height: 12 }} /> {active} job attivi
            </button>
          )}
          <a className="btn btn-ghost btn-sm" href="/docs" target="_blank" rel="noreferrer">
            API <Icon name="external" size={13} />
          </a>
        </header>
        {info && !info.modeller.available && (
          <div className="banner">
            <Icon name="alert" size={15} />
            {info.runtime?.container
              ? "MODELLER è installato ma serve la tua chiave di licenza (gratuita per uso accademico)."
              : "MODELLER non è disponibile: la modellazione non può partire."}
            <div className="spacer" />
            <button className="btn btn-sm btn-primary" onClick={() => navigate("/settings?tab=licenses")}>
              Inserisci la licenza
            </button>
          </div>
        )}
        <main className="content">
          <Outlet />
        </main>
      </div>

      {newProject !== null && (
        <Modal
          title="Nuovo progetto"
          onClose={() => setNewProject(null)}
          footer={
            <>
              <button className="btn" onClick={() => setNewProject(null)}>Annulla</button>
              <button
                className="btn btn-primary"
                disabled={!newProject.trim()}
                onClick={() =>
                  create(newProject.trim())
                    .then(() => {
                      toast.push("Progetto creato");
                      setNewProject(null);
                    })
                    .catch(toast.error)
                }
              >
                Crea
              </button>
            </>
          }
        >
          <label className="field">
            Nome del progetto
            <input className="input" autoFocus value={newProject} onChange={(e) => setNewProject(e.target.value)} placeholder="Es. Chinasi di lievito" />
          </label>
        </Modal>
      )}
      {askShutdown && (
        <Confirm
          text={active > 0 ? `Ci sono ${active} job attivi: verranno interrotti. Arrestare BioLoom?` : "Arrestare BioLoom? Potrai riavviarlo con l'avviatore (Avvia BioLoom)."}
          confirmLabel="Arresta"
          onCancel={() => setAskShutdown(false)}
          onConfirm={() => {
            setAskShutdown(false);
            api.shutdown().then(() => toast.push("BioLoom si sta arrestando. Puoi chiudere questa scheda.", "info")).catch(toast.error);
          }}
        />
      )}
    </div>
  );
}
