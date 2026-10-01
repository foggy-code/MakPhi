import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "../api/client";
import type { EntityLabels, Project } from "../api/types";
import { useProject } from "../lib/context";
import { relTime } from "../lib/format";
import { useAsync } from "../lib/hooks";
import { favoritesFirst, FILTER_ALL, filterParams, type LabelFilter } from "../lib/tags";
import { Icon } from "./Icon";
import { FavoriteStar, LabelFilterSelect, TagChips } from "./Labels";
import { Empty, Modal, Spinner } from "./ui";

/** Elenco dei progetti con stella, etichette e filtro per etichetta (F8b); un clic sceglie il progetto attivo. */
export function ProjectsDialog({ onClose }: { onClose: () => void }) {
  const { projectId, select, reload: reloadProjects } = useProject();
  const navigate = useNavigate();
  const [filter, setFilter] = useState<LabelFilter>(FILTER_ALL);
  const { data, loading, setData } = useAsync<Project[]>(() => api.projects(filterParams(filter)), [JSON.stringify(filter)]);
  const rows = favoritesFirst(data ?? []);
  const patch = (labels: EntityLabels) => {
    setData((data ?? []).map((p) => (p.id === labels.entity_id ? { ...p, tags: labels.tags, favorite: labels.favorite } : p)));
    reloadProjects().catch(() => undefined);
  };
  return (
    <Modal title="Progetti" wide onClose={onClose}>
      <div className="stack">
        <div className="row small">
          <LabelFilterSelect type="project" value={filter} onChange={setFilter} />
          <span className="muted">Preferiti in cima · clic su un progetto per renderlo attivo · etichette e stella anche nella Panoramica.</span>
        </div>
        {loading && !data ? <Spinner /> : !rows.length ? (
          <Empty icon="folder" title="Nessun progetto con questo filtro" />
        ) : (
          <div className="table-wrap" style={{ maxHeight: "60vh", overflowY: "auto" }}>
            <table className="table">
              <thead><tr><th style={{ width: 30 }} /><th>Progetto</th><th>Etichette</th><th className="num">Sequenze</th><th className="num">Job</th><th>Modificato</th></tr></thead>
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
