import { useEffect, useRef, useState } from "react";
import { Link, NavLink, Outlet, useLocation, useNavigate } from "react-router-dom";
import { api } from "../api/client";
import type { SystemInfo } from "../api/types";
import { useProject, useToast } from "../lib/context";
import { SYSTEM_CHANGED, useInterval, useJobsFeed } from "../lib/hooks";
import { isMacPlatform, NAV_SHORTCUTS, PENDING_MS, shortcutFor } from "../lib/shortcuts";
import { THEME_OPTIONS, useThemePreference } from "../lib/theme";
import { GlobalSearch } from "./GlobalSearch";
import { Icon } from "./Icon";
import { ProjectsDialog } from "./ProjectsDialog";
import { ShortcutsHelp } from "./ShortcutsHelp";
import { Confirm, Modal } from "./ui";
import "../styles/shell.css";

const MAC = typeof navigator !== "undefined" && isMacPlatform(navigator);

/**
 * Scorciatoie globali (lib/shortcuts.ts): ricerca, pannello "?", `g` + lettera per le pagine. Un tasto gestito qui non
 * arriva alla pagina; mentre si scrive in un campo valgono solo Ctrl+K / ⌘K. Con il 3D a schermo intero (API Fullscreen o
 * ripiego CSS) nessuna: le finestre si aprirebbero sotto il visualizzatore, invisibili ma con il focus.
 */
function useGlobalShortcuts(handlers: { search: () => void; help: () => void; navigate: (path: string) => void }) {
  const saved = useRef(handlers);
  saved.current = handlers;
  useEffect(() => {
    let pendingUntil = 0;
    const onKey = (e: KeyboardEvent) => {
      if (e.defaultPrevented) return;
      if (document.fullscreenElement || document.querySelector(".viewer3d.fullscreen")) return;
      const action = shortcutFor(e as unknown as Parameters<typeof shortcutFor>[0], {
        pending: Date.now() < pendingUntil,
        dialogOpen: !!document.querySelector('[role="dialog"]'),
        mac: MAC,
      });
      pendingUntil = 0;
      if (!action) return;
      e.preventDefault();
      if (action.type === "pending") pendingUntil = Date.now() + PENDING_MS;
      else if (action.type === "search") saved.current.search();
      else if (action.type === "help") saved.current.help();
      else saved.current.navigate(action.path);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);
}

const NAV: { section: string; items: { to: string; label: string; icon: string }[] }[] = [
  { section: "", items: [{ to: "/", label: "Panoramica", icon: "home" }] },
  {
    section: "Flusso di lavoro",
    items: [
      { to: "/wizard", label: "Nuovo modello guidato", icon: "wand" },
      { to: "/sequences", label: "Sequenze", icon: "dna" },
      { to: "/search", label: "Ricerca template", icon: "search" },
      { to: "/templates", label: "Libreria template", icon: "library" },
      { to: "/alignment", label: "Allineamenti", icon: "align" },
      { to: "/pir", label: "Editor PIR", icon: "file" },
      { to: "/modeling", label: "Modellazione", icon: "cube" },
      { to: "/quality", label: "Qualità", icon: "shield" },
      { to: "/compare", label: "Confronto", icon: "compare" },
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

/** Lettera della scorciatoia `g` + lettera di ogni pagina, mostrata al passaggio del mouse. */
const NAV_KEY = new Map(NAV_SHORTCUTS.map((n) => [n.path, n.key]));

/** Stato di MODELLER in fondo alla barra laterale: pronto (con i job paralleli), licenza mancante, in verifica. */
function SystemStatus({ info, active }: { info: SystemInfo | null; active: number }) {
  const ready = !!info?.modeller.available;
  const color = !info ? "var(--muted)" : ready ? "var(--ok)" : "var(--err)";
  return (
    <div className="shell-status" title={info?.modeller.python ?? undefined}>
      <span className="dot" style={{ color }} />
      <b>MODELLER {ready ? info?.modeller.version : ""}</b>
      {info && !ready && <Link className="btn btn-sm btn-ghost" to="/settings?tab=licenses">Licenza</Link>}
      <span className="small">
        {!info ? "Verifica in corso…" : ready ? `${info.workers} job in parallelo · ${active ? `${active} attivi` : "nessuno attivo"}` : "Non disponibile: serve la licenza"}
      </span>
    </div>
  );
}

export function Layout() {
  const { projects, projectId, select, create } = useProject();
  const toast = useToast();
  const navigate = useNavigate();
  const location = useLocation();
  const { pref: themePref, setPref: setThemePref } = useThemePreference();
  const [active, setActive] = useState(0);
  const [info, setInfo] = useState<SystemInfo | null>(null);
  const [newProject, setNewProject] = useState<string | null>(null);
  const [askShutdown, setAskShutdown] = useState(false);
  const [offline, setOffline] = useState(false);
  const [searchOpen, setSearchOpen] = useState(false);
  const [helpOpen, setHelpOpen] = useState(false);
  const [projectsOpen, setProjectsOpen] = useState(false);
  // schermi stretti: la barra laterale è un pannello sopra la pagina, aperto dal pulsante del menu nella barra in alto
  const [navOpen, setNavOpen] = useState(false);
  useEffect(() => {
    setNavOpen(false); // cambiando pagina (anche con le scorciatoie) il pannello si chiude
  }, [location.pathname, location.search]);
  useEffect(() => {
    if (!navOpen) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape" && !document.querySelector('[role="dialog"]')) setNavOpen(false);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [navOpen]);
  useGlobalShortcuts({
    search: () => setSearchOpen((open) => !open), // Ctrl+K a ricerca aperta la chiude
    help: () => setHelpOpen(true),
    navigate: (path) => navigate(path),
  });

  useEffect(() => {
    const load = () => api.systemInfo().then(setInfo).catch(() => undefined);
    load();
    // le Impostazioni segnalano i cambi di licenza o configurazione (es. chiave MODELLER inserita)
    window.addEventListener(SYSTEM_CHANGED, load);
    return () => window.removeEventListener(SYSTEM_CHANGED, load);
  }, []);

  // contatore dei job attivi dal flusso dell'elenco; se il flusso cade, polling (che segnala anche il server fermo)
  const live = useJobsFeed((type, data) => {
    const count = (data as { active?: unknown } | null)?.active;
    if ((type === "ready" || type === "active") && typeof count === "number") {
      setActive(count);
      setOffline(false);
    }
  });
  useInterval(() => {
    api
      .jobs({ status: "queued,running,cancelling", limit: 100 })
      .then((j) => {
        setActive(j.length);
        setOffline(false);
      })
      .catch(() => setOffline(true));
  }, live ? null : 3000);

  return (
    <div className={navOpen ? "app nav-open" : "app"}>
      <aside className="sidebar" id="sidebar">
        <div className="brand">
          <span className="shell-mark"><img src="/favicon.svg" alt="" /></span>
          <div>
            <b>BioLoom<span className="shell-version">v{info?.version ?? "4"}</span></b>
            <small>Modellazione per omologia</small>
          </div>
          <button type="button" className="btn btn-ghost btn-icon nav-close" onClick={() => setNavOpen(false)} aria-label="Chiudi il menu" title="Chiudi il menu (Esc)">
            <Icon name="x" size={16} />
          </button>
        </div>
        <nav className="nav" aria-label="Pagine">
          {NAV.map((group) => (
            <div key={group.section || "root"}>
              {group.section && <div className="nav-section">{group.section}</div>}
              {group.items.map((item) => (
                <NavLink key={item.to} to={item.to} end={item.to === "/"} onClick={() => setNavOpen(false)}>
                  <Icon name={item.icon} />
                  {item.label}
                  {item.to === "/jobs" && active > 0 && <span className="count" title={`${active} job attivi`}>{active}</span>}
                  {NAV_KEY.has(item.to) && <kbd className="shell-key" aria-hidden="true">g {NAV_KEY.get(item.to)}</kbd>}
                </NavLink>
              ))}
            </div>
          ))}
        </nav>
        <div className="sidebar-foot">
          <SystemStatus info={info} active={active} />
          <div className="segmented theme-switch" role="radiogroup" aria-label="Tema">
            {THEME_OPTIONS.map((o) => (
              <button key={o.value} type="button" role="radio" aria-checked={themePref === o.value} className={themePref === o.value ? "on" : ""}
                title={o.hint} onClick={() => setThemePref(o.value)}>
                <Icon name={o.icon} size={13} /> {o.label}
              </button>
            ))}
          </div>
          <div className="shell-foot-actions">
            <button type="button" className="btn btn-ghost btn-sm" onClick={() => setHelpOpen(true)} title="Scorciatoie da tastiera (?)">
              <Icon name="keyboard" size={14} /> Tasti
            </button>
            {info?.runtime?.auth && (
              <button type="button" className="btn btn-ghost btn-sm" onClick={() => api.logout().finally(() => window.location.reload())} title="Esci dalla sessione">
                <Icon name="x" size={14} /> Disconnetti
              </button>
            )}
            {info?.runtime?.allow_shutdown !== false && (
              <button type="button" className="btn btn-ghost btn-sm" onClick={() => setAskShutdown(true)} title="Arresta BioLoom">
                <Icon name="power" size={14} /> Esci
              </button>
            )}
          </div>
        </div>
      </aside>
      {navOpen && <div className="nav-backdrop" onClick={() => setNavOpen(false)} />}
      <div className="main">
        <header className="topbar">
          <button type="button" className="btn btn-ghost btn-icon nav-toggle" onClick={() => setNavOpen((open) => !open)} aria-label="Menu"
            aria-expanded={navOpen} aria-controls="sidebar" title="Pagine, tema e uscita">
            <Icon name="menu" />
          </button>
          <div className="shell-project" role="group" aria-label="Progetto">
            <Icon name="folder" />
            <select className="input" value={projectId ?? ""} onChange={(e) => select(e.target.value)} aria-label="Progetto attivo">
              {projects.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.name}
                </option>
              ))}
            </select>
            <button type="button" className="btn btn-sm" onClick={() => setNewProject("")} title="Crea un nuovo progetto">
              <Icon name="plus" size={14} /> <span className="shell-label">Nuovo</span>
            </button>
            <button type="button" className="btn btn-sm btn-ghost" onClick={() => setProjectsOpen(true)} title="Tutti i progetti: stella, etichette ed eliminazione">
              <Icon name="star" size={14} /> <span className="shell-label">Progetti</span>
            </button>
          </div>
          <button type="button" className="search-trigger" onClick={() => setSearchOpen(true)} title="Ricerca globale" aria-label="Ricerca globale">
            <Icon name="search" size={14} />
            <span>Cerca progetti, job, template…</span>
            <kbd>{MAC ? "⌘" : "Ctrl"} K</kbd>
          </button>
          <div className="spacer" />
          {offline && <span className="badge badge-err"><Icon name="alert" size={12} /> Server non raggiungibile</span>}
          {active > 0 && (
            <button type="button" className="btn btn-sm shell-jobs" onClick={() => navigate("/jobs")} title="Apri l'elenco dei job">
              <span className="spinner" style={{ width: 12, height: 12 }} aria-hidden="true" /> {active} {active === 1 ? "job attivo" : "job attivi"}
            </button>
          )}
          <a className="btn btn-ghost btn-sm" href="/docs" target="_blank" rel="noreferrer" title="Documentazione delle API (nuova scheda)">
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
            <button type="button" className="btn btn-sm btn-primary" onClick={() => navigate("/settings?tab=licenses")}>
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
              <button type="button" className="btn" onClick={() => setNewProject(null)}>Annulla</button>
              <button
                type="button"
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
      {searchOpen && <GlobalSearch onClose={() => setSearchOpen(false)} />}
      {helpOpen && <ShortcutsHelp mac={MAC} onClose={() => setHelpOpen(false)} />}
      {projectsOpen && <ProjectsDialog onClose={() => setProjectsOpen(false)} />}
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
