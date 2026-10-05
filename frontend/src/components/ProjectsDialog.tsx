import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "../api/client";
import type { EntityLabels, Project } from "../api/types";
import { useProject } from "../lib/context";
import { fmtDate, relTime } from "../lib/format";
import { useAsync } from "../lib/hooks";
import { favoritesFirst, FILTER_ALL, filterParams, type LabelFilter } from "../lib/tags";
import { Icon } from "./Icon";
import { FavoriteStar, LabelFilterSelect, TagChips } from "./Labels";
import { Empty, Modal, Spinner } from "./ui";

/**
 * Elenco dei progetti con stella, etichette, filtro per etichetta (F8b) e ricerca per nome o descrizione; un clic
 * sceglie il progetto attivo.
 */
export function ProjectsDialog({ onClose }: { onClose: () => void }) {
  const { projectId, select, reload: reloadProjects } = useProject();
  const navigate = useNavigate();
  const [filter, setFilter] = useState<LabelFilter>(FILTER_ALL);
  const [query, setQuery] = useState("");
  const { data, loading, setData } = useAsync<Project[]>(() => api.projects(filterParams(filter)), [JSON.stringify(filter)]);
  const q = query.trim().toLowerCase();
  const rows = favoritesFirst(data ?? []).filter((p) => !q || p.name.toLowerCase().includes(q) || p.description?.toLowerCase().includes(q));
  const patch = (labels: EntityLabels) => {
    setData((data ?? []).map((p) => (p.id === labels.entity_id ? { ...p, tags: labels.tags, favorite: labels.favorite } : p)));
    reloadProjects().catch(() => undefined);
  };
  return (
    <Modal title="Progetti" size="wide" onClose={onClose}>
      <div className="stack">
        <div className="shell-list-tools">
          <input className="input" style={{ width: 240, minHeight: 30 }} placeholder="Cerca per nome o descrizione" aria-label="Cerca un progetto" autoFocus
            value={query} onChange={(e) => setQuery(e.target.value)} />
          <LabelFilterSelect type="project" value={filter} onChange={setFilter} />
          <span className="count">{data ? `${rows.length} di ${data.length}` : ""}</span>
        </div>
        <p className="small text-2">Preferiti in cima · clic su un progetto per renderlo attivo · etichette e stella anche nella Panoramica.</p>
        {loading && !data ? <div className="empty"><Spinner label="Caricamento dei progetti" /></div> : !rows.length ? (
          <Empty icon="folder" title="Nessun progetto con questo filtro" />
        ) : (
          <div className="table-wrap" style={{ maxHeight: "60vh", overflowY: "auto" }}>
            <table className="table">
              <thead><tr><th style={{ width: 30 }} aria-label="Preferito" /><th>Progetto</th><th>Etichette</th><th className="num">Sequenze</th><th className="num">Documenti</th><th className="num">Job</th><th>Modificato</th></tr></thead>
              <tbody>
                {rows.map((p) => (
                  <tr key={p.id} className={`clickable${p.id === projectId ? " selected" : ""}`}
                    onClick={() => { select(p.id); onClose(); navigate("/"); }}>
                    <td><FavoriteStar type="project" id={p.id} value={!!p.favorite} onChange={patch} /></td>
                    <td>
                      <b>{p.name}</b> {p.id === projectId && <span className="badge badge-accent">attivo</span>}
                      {p.description && <div className="shell-sub ellipsis" style={{ maxWidth: 380 }} title={p.description}>{p.description}</div>}
                    </td>
                    <td><TagChips tags={p.tags} onPick={(t) => setFilter({ kind: "tag", tag: t })} /></td>
                    <td className="num">{p.sequences ?? "—"}</td>
                    <td className="num">{p.documents ?? "—"}</td>
                    <td className="num">{p.jobs ?? "—"}</td>
                    <td className="shell-sub nowrap" title={`Creato ${fmtDate(p.created_at)} · modificato ${fmtDate(p.updated_at)}`}>{relTime(p.updated_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        <p className="small text-2"><Icon name="info" size={12} /> Le etichette del progetto attivo si modificano nella Panoramica.</p>
      </div>
    </Modal>
  );
}
