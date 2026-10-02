import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api } from "../api/client";
import type { Example, JobSummary, SystemInfo, SystemStats } from "../api/types";
import { Icon } from "../components/Icon";
import { LabelEditor } from "../components/Labels";
import { Badge, Card, Empty, PageHead, Progress, Stat, StatusBadge } from "../components/ui";
import { useProject, useToast } from "../lib/context";
import { fmtBytes, fmtCpuSlots, KIND_LABEL, relTime } from "../lib/format";
import { useAsync, useInterval, useLiveJobList } from "../lib/hooks";
import { itemLabels } from "../lib/tags";

const FLOW = [
  { to: "/sequences", icon: "dna", title: "Sequenza", text: "Importa la proteina bersaglio da UniProt o FASTA." },
  { to: "/search", icon: "search", title: "Template", text: "Cerca strutture omologhe nel PDB e scaricale." },
  { to: "/alignment", icon: "align", title: "Allineamento", text: "Confronta sequenze e strutture, rifinisci il PIR." },
  { to: "/modeling", icon: "cube", title: "Modello", text: "Costruisci i modelli 3D con MODELLER." },
  { to: "/quality", icon: "shield", title: "Qualità", text: "Valida con Ramachandran, clash, DOPE, DSSP." },
];

export default function Dashboard() {
  const { project, projectId, reload: reloadProjects } = useProject();
  const toast = useToast();
  const navigate = useNavigate();
  const { data: info } = useAsync<SystemInfo>(() => api.systemInfo(), []);
  const { data: examples } = useAsync<Example[]>(() => api.examples(), []);
  const { data: jobs, reload: reloadJobs, setData: setJobs } = useAsync<JobSummary[]>(() => api.jobs({ project_id: projectId ?? undefined, limit: 8 }), [projectId]);
  const [stats, setStats] = useState<SystemStats | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  // job recenti dal flusso dell'elenco; CPU, memoria e disco restano periodici
  const live = useLiveJobList(jobs, setJobs, reloadJobs, (j) => !projectId || j.project_id === projectId);
  useInterval(() => {
    api.stats().then(setStats).catch(() => undefined);
    if (!live) reloadJobs();
  }, 5000);

  const addExample = async (ex: Example) => {
    if (!projectId) return;
    setBusy(ex.name);
    try {
      if (ex.sequence) {
        await api.addSequence(projectId, ex.name, ex.sequence, "esempio");
      } else if (ex.accession) {
        const entry = await api.uniprot(ex.accession);
        await api.addSequence(projectId, entry.entry_name || ex.name, entry.sequence, "uniprot", { accession: entry.accession, reviewed: entry.reviewed, protein: entry.protein_name });
      }
      toast.push(`${ex.title} aggiunta al progetto`);
      reloadProjects();
      navigate("/sequences");
    } catch (e) {
      toast.error(e);
    } finally {
      setBusy(null);
    }
  };

  const tools = info ? Object.entries(info.tools) : [];
  return (
    <div className="page">
      <PageHead
        title={project ? project.name : "BioLoom"}
        lead="Modellazione di proteine per omologia: dalla sequenza al modello 3D validato. Ogni calcolo gira in background e resta consultabile nella sezione Job."
        actions={
          <>
            <Link className="btn btn-primary" to="/wizard" title="Procedura guidata: sequenza, template, allineamento, opzioni con i controlli, risultati">
              <Icon name="wand" /> Nuovo modello guidato
            </Link>
            <Link className="btn" to="/modeling">
              <Icon name="cube" /> Nuovo modello
            </Link>
          </>
        }
      />
      {/* stella ed etichette del progetto attivo (F8b) */}
      {projectId && <LabelEditor key={projectId} type="project" id={projectId} initial={itemLabels(project)} onChange={() => reloadProjects().catch(() => undefined)} />}

      <div className="grid grid-5">
        {FLOW.map((f, i) => (
          <Link key={f.to} to={f.to} className="card" style={{ color: "inherit", textDecoration: "none" }}>
            <div className="row" style={{ marginBottom: 8 }}>
              <span className="grade grade-info" style={{ width: 26, height: 26, fontSize: 13 }}>{i + 1}</span>
              <Icon name={f.icon} />
              <b>{f.title}</b>
            </div>
            <p className="small text-2">{f.text}</p>
          </Link>
        ))}
      </div>

      <div className="grid grid-2">
        <Card title="Job recenti del progetto" right={<Link className="btn btn-sm btn-ghost" to="/jobs">Tutti i job</Link>}>
          {!jobs?.length ? (
            <Empty icon="activity" title="Nessun job ancora">Avvia una ricerca di template o una modellazione.</Empty>
          ) : (
            <div className="stack-sm">
              {jobs.map((j) => (
                <Link key={j.id} to={`/jobs?id=${j.id}`} className="row card flat" style={{ padding: "8px 12px", color: "inherit", textDecoration: "none" }}>
                  <StatusBadge status={j.status} />
                  <span className="ellipsis" style={{ flex: 1 }}>{j.title || KIND_LABEL[j.kind]}</span>
                  <span className="tiny muted nowrap">{relTime(j.created_at)}</span>
                </Link>
              ))}
            </div>
          )}
        </Card>

        <Card title="Inizia da un esempio" sub="Aggiunge la sequenza al progetto attivo">
          <div className="stack-sm">
            {examples?.map((ex) => (
              <div key={ex.name} className="row card flat" style={{ padding: "10px 12px" }}>
                <div style={{ flex: 1, minWidth: 0 }}>
                  <b>{ex.title}</b> {ex.accession && <Badge>{ex.accession}</Badge>}
                  <div className="small text-2">{ex.note}</div>
                </div>
                <button className="btn btn-sm" disabled={busy !== null} onClick={() => addExample(ex)}>
                  {busy === ex.name ? "…" : "Usa"}
                </button>
              </div>
            ))}
          </div>
        </Card>
      </div>

      <div className="grid grid-2">
        <Card title="Motori disponibili" right={<Link className="btn btn-sm btn-ghost" to="/settings">Configura</Link>}>
          {!info ? (
            <Progress value={0} indeterminate />
          ) : (
            <div className="stack">
              <div className="row">
                <span className={`grade ${info.modeller.available ? "grade-ok" : "grade-err"}`}>
                  <Icon name={info.modeller.available ? "check" : "x"} />
                </span>
                <div>
                  <b>MODELLER {info.modeller.version ?? ""}</b>
                  <div className="small text-2">
                    {info.modeller.available ? `Interprete: ${info.modeller.python}` : "Non trovato: la modellazione e il DOPE non sono disponibili. Configura l'interprete nelle Impostazioni."}
                  </div>
                </div>
              </div>
              <div className="grid grid-2" style={{ gap: 8 }}>
                {tools.map(([key, t]) => (
                  <div key={key} className="row small" title={t.path ?? "Non installato"}>
                    <span className="dot" style={{ color: t.available ? "var(--ok)" : "var(--muted)" }} />
                    <span className={t.available ? "" : "muted"}>{t.label}</span>
                  </div>
                ))}
              </div>
              <p className="tiny muted">
                Senza strumenti esterni BioLoom usa i motori integrati: DSSP (Kabsch-Sander), allineamento multiplo center-star, sovrapposizione Kabsch con TM-score e ricerca RCSB.
              </p>
            </div>
          )}
        </Card>
        <Card title="Risorse del computer">
          {!stats ? (
            <Progress value={0} indeterminate />
          ) : (
            <div className="grid grid-2">
              <Stat label="CPU" value={<>{stats.cpu_percent.toFixed(0)}<small>%</small></>} note={`${stats.cpu_count} core · ${info?.workers ?? "?"} job paralleli`} />
              <Stat label="Memoria" value={<>{stats.memory.percent.toFixed(0)}<small>%</small></>} note={`${fmtBytes(stats.memory.used)} di ${fmtBytes(stats.memory.total)}`} />
              <Stat label="Disco dati" value={<>{stats.disk.percent.toFixed(0)}<small>%</small></>} note={`${fmtBytes(stats.disk.free)} liberi`} />
              <Stat label="Job attivi" value={stats.active_jobs} note={[fmtCpuSlots(stats.cpu_slots), `Server: ${fmtBytes(stats.process_memory)}`].filter(Boolean).join(" · ")} />
            </div>
          )}
        </Card>
      </div>
    </div>
  );
}
