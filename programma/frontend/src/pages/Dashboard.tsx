import { useMemo, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api } from "../api/client";
import type { Example, JobSummary, SystemInfo, SystemStats } from "../api/types";
import { Icon } from "../components/Icon";
import { LabelEditor } from "../components/Labels";
import { Badge, Card, Disclosure, Empty, Progress, Stat, StatusBadge } from "../components/ui";
import { useProject, useToast } from "../lib/context";
import { fmtBytes, fmtEta, isActive, KIND_LABEL, relTime } from "../lib/format";
import { useAsync, useInterval, useLiveJobList } from "../lib/hooks";
import { flowProgress, greeting, jobCounts, kindBreakdown, nextFlowStep, plural, type JobCounts, type JobGroup } from "../lib/shellView";
import { itemLabels } from "../lib/tags";

const FLOW = [
  { to: "/sequences", icon: "dna", title: "Sequenza", text: "Importa la proteina bersaglio da UniProt o FASTA." },
  { to: "/search", icon: "search", title: "Template", text: "Cerca strutture omologhe nel PDB e scaricale.", viaPipeline: "Pipeline completate: cercano i template solo in modalità automatica o con più template." },
  { to: "/alignment", icon: "align", title: "Allineamento", text: "Confronta sequenze e strutture, rifinisci il PIR." },
  { to: "/modeling", icon: "cube", title: "Modello", text: "Costruisci i modelli 3D con MODELLER." },
  { to: "/quality", icon: "shield", title: "Qualità", text: "Valida con Ramachandran, clash, DOPE, DSSP.", viaPipeline: "Pipeline completate: analizzano il modello migliore solo se l'analisi di qualità è attiva." },
];

/** job letti per gli indicatori (i più recenti del progetto); i job recenti mostrati sono i primi */
const JOB_LIMIT = 200;
const RECENT = 8;

/** Utilizzo di una risorsa: barra colorata per soglia (oltre 75% attenzione, oltre 90% critico). */
function Meter({ percent }: { percent: number }) {
  const tone = percent > 90 ? " err" : percent > 75 ? " warn" : "";
  return (
    <div className={`shell-meter${tone}`} role="meter" aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round(percent)}>
      <div style={{ width: `${Math.max(1, Math.min(100, percent))}%` }} />
    </div>
  );
}

/** Dettaglio di un indicatore: job completati per tipo e falliti della famiglia. */
function GroupDetail({ counts, group, capped }: { counts: JobCounts; group: JobGroup; capped: boolean }) {
  const kinds = kindBreakdown(counts, group);
  return (
    <>
      {kinds.length ? kinds.map((k) => <span key={k.kind}>{k.label}: <b>{k.count}</b></span>) : <span>Nessun job completato.</span>}
      {counts.failed[group] > 0 && <span>Non riusciti: <b>{counts.failed[group]}</b></span>}
      {capped && <span>Conteggio sugli ultimi {JOB_LIMIT} job del progetto.</span>}
    </>
  );
}

export default function Dashboard() {
  const { project, projectId, reload: reloadProjects } = useProject();
  const toast = useToast();
  const navigate = useNavigate();
  const { data: info } = useAsync<SystemInfo>(() => api.systemInfo(), []);
  const { data: examples } = useAsync<Example[]>(() => api.examples(), []);
  const { data: jobs, reload: reloadJobs, setData: setJobs } = useAsync<JobSummary[]>(() => api.jobs({ project_id: projectId ?? undefined, limit: JOB_LIMIT }), [projectId]);
  const [stats, setStats] = useState<SystemStats | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  // job del progetto dal flusso dell'elenco; CPU, memoria e disco restano periodici
  const live = useLiveJobList(jobs, setJobs, reloadJobs, (j) => !projectId || j.project_id === projectId);
  useInterval(() => {
    api.stats().then(setStats).catch(() => undefined);
    if (!live) reloadJobs();
  }, 5000);
  const counts = useMemo(() => (jobs ? jobCounts(jobs) : null), [jobs]);
  const progress = counts ? flowProgress(project, counts) : null;
  const next = progress ? nextFlowStep(progress) : -1;
  const capped = (jobs?.length ?? 0) >= JOB_LIMIT;
  const activeJobs = jobs?.filter((j) => isActive(j.status)) ?? [];

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
  const slots = stats?.cpu_slots?.total ? stats.cpu_slots : null;
  const toolsReady = tools.filter(([, t]) => t.available).length;
  return (
    <div className="page">
      <section className="shell-hero" aria-label="Benvenuto">
        <div>
          <span className="shell-eyebrow">{greeting(new Date().getHours())} · Panoramica del progetto</span>
          <h1>{project ? project.name : "BioLoom"}</h1>
          <p className="lead">
            Modellazione di proteine per omologia: dalla sequenza al modello 3D validato. Ogni calcolo gira in background e resta consultabile
            nella sezione Job.
          </p>
          {/* stella ed etichette del progetto attivo (F8b) */}
          {projectId && <LabelEditor key={projectId} type="project" id={projectId} initial={itemLabels(project)} onChange={() => reloadProjects().catch(() => undefined)} />}
        </div>
        <div className="actions">
          <Link className="btn btn-primary" to="/wizard" title="Procedura guidata: sequenza, template, allineamento, opzioni con i controlli, risultati">
            <Icon name="wand" /> Nuovo modello guidato
          </Link>
          <Link className="btn" to="/modeling">
            <Icon name="cube" /> Nuovo modello
          </Link>
          <Link className="btn btn-ghost" to="/pipeline">
            <Icon name="layers" /> Pipeline
          </Link>
        </div>
      </section>

      <div className="grid grid-4">
        <Stat label="Job attivi" tone={activeJobs.length ? "accent" : undefined}
          value={counts ? activeJobs.length : "—"}
          note={stats ? `${plural(stats.active_jobs, "attivo", "attivi")} in tutti i progetti` : "nel progetto"}
          detail={activeJobs.length ? (
            <>
              {activeJobs.slice(0, 5).map((j) => (
                <span key={j.id}><Link to={`/jobs?id=${j.id}`}>{j.title || KIND_LABEL[j.kind]}</Link>{j.stage ? ` · ${j.stage}` : ""}</span>
              ))}
              {activeJobs.length > 5 && <span>e altri {activeJobs.length - 5}: vedi Job.</span>}
            </>
          ) : undefined} />
        <Stat label="Modellazioni" tone={counts?.done.model ? "ok" : undefined} value={counts ? counts.done.model : "—"}
          note={counts?.failed.model ? plural(counts.failed.model, "non riuscita", "non riuscite") : "completate nel progetto"}
          detail={counts ? <GroupDetail counts={counts} group="model" capped={capped} /> : undefined} />
        <Stat label="Analisi" tone={counts?.done.analysis ? "info" : undefined} value={counts ? counts.done.analysis : "—"}
          note="qualità, tasche, elettrostatica, predizioni"
          detail={counts ? <GroupDetail counts={counts} group="analysis" capped={capped} /> : undefined} />
        <Stat label="Sequenze" value={project?.sequences ?? "—"}
          note={project ? `${plural(project.documents ?? 0, "documento", "documenti")} · ${plural(project.jobs ?? 0, "job", "job")}` : "nel progetto"} />
      </div>

      <div className="grid grid-5">
        {FLOW.map((f, i) => {
          const step = progress?.[i];
          const state = step && step.count > 0 ? " done" : i === next ? " next" : "";
          return (
            <Link key={f.to} to={f.to} className={`card shell-step${state}`}>
              <div className="shell-step-head">
                <span className="n">{step && step.count > 0 ? <Icon name="check" size={14} /> : i + 1}</span>
                <Icon name={f.icon} />
                <b>{f.title}</b>
              </div>
              <p>{f.text}</p>
              {step && (
                <div className="shell-step-foot">
                  {step.count > 0 ? <Badge tone="ok">{step.label}</Badge> : i === next ? <Badge tone="accent">Prossimo passo</Badge> : <Badge>da fare</Badge>}
                  {step.pipelines > 0 && <Badge title={f.viaPipeline}>{plural(step.pipelines, "pipeline", "pipeline")}</Badge>}
                </div>
              )}
            </Link>
          );
        })}
      </div>

      <div className="grid grid-2">
        <Card icon="activity" title="Job recenti del progetto" right={<Link className="btn btn-sm btn-ghost" to="/jobs">Tutti i job <Icon name="chevron" size={13} /></Link>}>
          {!jobs?.length ? (
            <Empty icon="activity" title="Nessun job ancora">Avvia una ricerca di template o una modellazione.</Empty>
          ) : (
            <div className="shell-job-list">
              {jobs.slice(0, RECENT).map((j) => (
                <Link key={j.id} to={`/jobs?id=${j.id}`} className="shell-job-row">
                  <StatusBadge status={j.status} />
                  <span className="title" title={j.title}>{j.title ? <>{j.title}<small>{KIND_LABEL[j.kind]}</small></> : KIND_LABEL[j.kind]}</span>
                  <span className="when" title={isActive(j.status) ? fmtEta(j.eta_seconds) ?? undefined : undefined}>{relTime(j.created_at)}</span>
                  {isActive(j.status) && <Progress value={j.progress} indeterminate={j.progress < 0.03} />}
                </Link>
              ))}
            </div>
          )}
        </Card>

        <Card icon="flask" title="Inizia da un esempio" sub="Aggiunge la sequenza al progetto attivo">
          <div className="stack-sm">
            {examples?.map((ex) => (
              <div key={ex.name} className="row card flat" style={{ padding: "10px 12px" }}>
                <div style={{ flex: 1, minWidth: 0 }}>
                  <b>{ex.title}</b> {ex.accession && <Badge>{ex.accession}</Badge>}
                  <div className="small text-2">{ex.note}</div>
                </div>
                <button type="button" className="btn btn-sm" disabled={busy !== null} onClick={() => addExample(ex)} aria-label={`Usa l'esempio ${ex.title}`}>
                  {busy === ex.name ? "…" : "Usa"}
                </button>
              </div>
            ))}
          </div>
        </Card>
      </div>

      <div className="grid grid-2">
        <Card icon="settings" title="Motori disponibili" right={<Link className="btn btn-sm btn-ghost" to="/settings?tab=tools">Configura</Link>}>
          {!info ? (
            <Progress value={0} indeterminate />
          ) : (
            <div className="stack">
              <div className="row">
                <span className={`grade ${info.modeller.available ? "grade-ok" : "grade-err"}`}>
                  <Icon name={info.modeller.available ? "check" : "x"} />
                </span>
                <div style={{ minWidth: 0 }}>
                  <b>MODELLER {info.modeller.version ?? ""}</b>
                  <div className="small text-2">
                    {info.modeller.available ? `Interprete: ${info.modeller.python}` : "Non trovato: la modellazione e il DOPE non sono disponibili. Configura l'interprete nelle Impostazioni."}
                  </div>
                </div>
              </div>
              <Disclosure title="Strumenti esterni" extra={`${toolsReady} di ${tools.length} installati`}>
                <div className="stack-sm">
                  <div className="shell-tools">
                    {tools.map(([key, t]) => (
                      <div key={key} className={`row${t.available ? "" : " off"}`} title={t.path ?? "Non installato"}>
                        <span className="dot" style={{ color: t.available ? "var(--ok)" : "var(--muted)" }} />
                        <span>{t.label}</span>
                      </div>
                    ))}
                  </div>
                  <p className="small text-2">
                    Senza strumenti esterni BioLoom usa i motori integrati: DSSP (Kabsch-Sander), allineamento multiplo center-star, sovrapposizione
                    Kabsch con TM-score e ricerca RCSB.
                  </p>
                </div>
              </Disclosure>
            </div>
          )}
        </Card>
        <Card icon="monitor" title="Risorse del computer" sub="aggiornate ogni 5 secondi">
          {!stats ? (
            <Progress value={0} indeterminate />
          ) : (
            <div className="grid grid-2">
              <Stat label="CPU" value={<>{stats.cpu_percent.toFixed(0)}<small>%</small></>}
                note={<><Meter percent={stats.cpu_percent} />{stats.cpu_count} core · {info?.workers ?? "?"} job paralleli</>} />
              <Stat label="Memoria" value={<>{stats.memory.percent.toFixed(0)}<small>%</small></>}
                note={<><Meter percent={stats.memory.percent} />{fmtBytes(stats.memory.used)} di {fmtBytes(stats.memory.total)}</>}
                detail={<span>Server BioLoom: <b>{fmtBytes(stats.process_memory)}</b></span>} />
              <Stat label="Disco dati" value={<>{stats.disk.percent.toFixed(0)}<small>%</small></>}
                note={<><Meter percent={stats.disk.percent} />{fmtBytes(stats.disk.free)} liberi di {fmtBytes(stats.disk.total)}</>}
                detail={<span>File temporanei e cache si liberano in <Link to="/settings?tab=storage">Impostazioni → Spazio</Link>.</span>} />
              <Stat label="Slot di calcolo" value={slots ? <>{Math.min(slots.used, slots.total)}<small> di {slots.total}</small></> : "—"}
                note={slots ? <><Meter percent={(Math.min(slots.used, slots.total) / slots.total) * 100} />{slots.waiting ? plural(slots.waiting, "processo in attesa", "processi in attesa") : "nessun processo in attesa"}</> : "non riportati dal server"}
                detail={<span>I programmi di calcolo di tutti i job (MODELLER, PROCHECK, BLAST…) condividono questi slot; il numero di job in parallelo si imposta in <Link to="/settings">Impostazioni</Link>.</span>} />
            </div>
          )}
        </Card>
      </div>
    </div>
  );
}
