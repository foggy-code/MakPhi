// Confronto con un predittore esterno (F9.3): pulsante «Confronta con una predizione (ESMFold)» nelle pagine Qualità e
// Modellazione, finestra con il consenso all'invio (ESMFold: la sequenza lascia il computer), controlli preventivi e
// avvio, risultati del job `predict` (esito motivato e confronto per residuo con StructuralComparison), vista da un link
// (/quality?prjob=). La logica è in lib/predict.ts, le regole nel backend (domain/prediction.py).
import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api/client";
import type { Job, PredictorName, PredictResult, Settings, StructureSource } from "../api/types";
import { useProject, useToast } from "../lib/context";
import { fmtNum, relTime } from "../lib/format";
import { useAsync, useJob } from "../lib/hooks";
import {
  consentText, esmfoldService, followedJob, metricsLine, needsConsent, originText, PREDICTOR_LABEL, predictionCompareLink, predictionQualityLink, predictPreflight,
  predictRequest, STATUS_TONE, verdictTitle,
} from "../lib/predict";
import { Icon } from "./Icon";
import { JobPanel } from "./JobPanel";
import { LaunchButton, PreflightPanel, usePreflight } from "./Preflight";
import { StructuralComparison } from "./StructuralComparison";
import { Alert, Badge, Card, Field, Modal, Segmented, Spinner, Stat } from "./ui";

/** Finestra di avvio: predittore, consenso all'invio (solo ESMFold e solo se non è già dato), controlli, avvio. */
export function PredictDialog({ source, label, residues, onClose, onLaunched }: {
  source: StructureSource;
  label: string;
  /** residui della catena, se noti (testo del consenso) */
  residues?: number | null;
  onClose: () => void;
  onLaunched: (jobId: string) => void;
}) {
  const { projectId } = useProject();
  const toast = useToast();
  const { data: loaded } = useAsync<Settings>(() => api.settings(), []);
  const [settings, setSettings] = useState<Settings | null>(null);
  const current = settings ?? loaded;
  const [predictor, setPredictor] = useState<PredictorName>("esmfold");
  const [accession, setAccession] = useState("");
  const [saving, setSaving] = useState(false);
  const consent = needsConsent(predictor, current);
  const req = predictRequest(predictor, source, { accession, projectId });
  // i controlli partono solo a consenso dato: prima la sequenza non deve nemmeno sembrare in partenza
  const preflight = usePreflight(current && !consent ? predictPreflight(req.body) : null);
  const giveConsent = async () => {
    setSaving(true);
    try {
      setSettings(await api.saveSettings({ esmfold_remote: true }));
      toast.push("Consenso all'invio a ESMFold salvato (si revoca in Impostazioni → Generale)");
    } catch (e) {
      toast.error(e);
    } finally {
      setSaving(false);
    }
  };
  const launch = async () => {
    if (!req.body) return;
    const job = await api.predict(req.body);
    toast.push(`${predictor === "esmfold" ? "Predizione ESMFold" : "Modello di AlphaFold DB"} avviato`);
    onLaunched(job.id);
    onClose();
  };
  return (
    <Modal title={`Confronta ${label} con una predizione`} onClose={onClose}>
      <div className="stack">
        <p className="small text-2">
          Una predizione indipendente della stessa sequenza fa da controllo di plausibilità: dove il modello per omologia
          diverge da una predizione sicura (pLDDT ≥ 70) conviene ricontrollare allineamento e template; dove anche la
          predizione è incerta la differenza non dice molto. Una predizione non è una struttura sperimentale.
        </p>
        <Segmented value={predictor} onChange={setPredictor} options={[
          { value: "esmfold", label: "ESMFold (servizio esterno)" },
          { value: "alphafold", label: "AlphaFold DB (accessione)" },
        ]} />
        {predictor === "alphafold" && (
          <Field label="Accessione UniProt" hint="Es. P0CG48, oppure AF-<ACC>-F2 per un frammento: a EBI va solo l'accessione; il modello si salva anche nella libreria dei template.">
            <input className="input mono" style={{ width: 220 }} value={accession} maxLength={40} onChange={(e) => setAccession(e.target.value.trim())} />
          </Field>
        )}
        {!current ? <Spinner /> : consent ? (
          <Alert tone="warn">
            <div className="stack-sm">
              <b>Consenso all'invio della sequenza</b>
              <span>{consentText(esmfoldService(current), residues ?? null)}</span>
              <div className="row" style={{ gap: 8 }}>
                <button className="btn btn-primary btn-sm" disabled={saving} onClick={giveConsent}>{saving ? <Spinner /> : <Icon name="check" size={13} />} Acconsento e continua</button>
                <button className="btn btn-sm" onClick={onClose}>Annulla</button>
              </div>
            </div>
          </Alert>
        ) : (
          <>
            {predictor === "esmfold" && <p className="tiny muted">La sequenza va a {esmfoldService(current)} (consenso dato in Impostazioni → Generale).</p>}
            <PreflightPanel state={preflight} />
            <div className="row" style={{ gap: 8, alignItems: "start" }}>
              <LaunchButton preflight={preflight} disabled={!req.body} onLaunch={launch}>Avvia il confronto</LaunchButton>
              <button className="btn" onClick={onClose}>Annulla</button>
            </div>
            {req.error && <span className="tiny muted">{req.error}</span>}
          </>
        )}
      </div>
    </Modal>
  );
}

/** Risultati di un job `predict`: esito motivato, metriche, regioni, link e confronto per residuo. */
export function PredictionResults({ jobId, result }: { jobId: string; result: PredictResult }) {
  const p = result.plausibility;
  return (
    <div className="stack">
      {result.warnings.map((w, i) => <Alert key={i} tone="warn">{w}</Alert>)}
      {p ? (
        <Alert tone={STATUS_TONE[p.status]}>
          <b>{verdictTitle(p)}.</b> {p.summary}
        </Alert>
      ) : <Alert tone="info">Predizione senza confronto: nessuna struttura indicata.</Alert>}
      <div className="grid grid-4">
        {p && <Stat label="TM-score (sul modello)" value={fmtNum(p.tm_score, 3)} note="da 0,5 stesso ripiegamento (Xu & Zhang 2010)" />}
        {p && <Stat label="lDDT" value={fmtNum(p.lddt, 3)} note={p.lddt_confident != null ? `${fmtNum(p.lddt_confident, 3)} dove la predizione è sicura` : "predizione mai sicura"} />}
        <Stat label="pLDDT della predizione" value={fmtNum(result.plddt.mean, 1)} note={p ? `${p.confident_residues} residui su ${p.pairs} con pLDDT ≥ 70` : `${result.plddt.residues} residui`} />
        {p && <Stat label="Residui che divergono" value={p.divergent_residues} note="lDDT sotto 0,5 rispetto alla predizione" />}
      </div>
      {p && p.regions.length > 0 && (
        <div className="stack-sm">
          <b className="small">Regioni che divergono</b>
          {p.regions.map((r) => (
            <div key={`${r.chain}${r.start}`} className="row small" style={{ gap: 8 }}>
              <Badge tone={r.confidence === "sicura" ? "warn" : "info"}>{r.confidence ?? "pLDDT assente"}</Badge>
              <span>{r.text}</span>
            </div>
          ))}
        </div>
      )}
      <div className="row small" style={{ gap: 8 }}>
        <Badge tone="info" title={result.recognized_as ? "Riconosciuta come modello predetto: la pagina Qualità mostra il pLDDT" : undefined}>{result.label}</Badge>
        <span className="muted">{originText(result)}{p ? ` · ${metricsLine(p)}` : ""}</span>
      </div>
      <div className="row" style={{ gap: 6 }}>
        <Link className="btn btn-sm" to={predictionQualityLink(jobId, result)}><Icon name="shield" size={13} /> Qualità della predizione</Link>
        {result.source && <Link className="btn btn-sm" to={predictionCompareLink(jobId, result)} title="Pagina Confronto con il modello come riferimento (A), per altre opzioni (TM-align)"><Icon name="compare" size={13} /> Apri in Confronto</Link>}
        <a className="btn btn-sm" href={api.jobFileUrl(jobId, result.prediction_file, true)}><Icon name="download" size={13} /> PDB della predizione</a>
      </div>
      {result.comparison && <StructuralComparison jobId={jobId} result={result.comparison} />}
      {p && (
        <details className="small text-2">
          <summary>Regole, note e limiti</summary>
          <ul style={{ margin: "6px 0 0", paddingLeft: 18 }}>
            <li>Residui abbinati per sequenza (stessa proteina); A = modello (riferimento), B = predizione. lDDT-Cα per residuo del modello (Mariani et al. 2013): sotto 0,5 il residuo diverge; regioni di almeno 3 residui divergenti.</li>
            <li>Predizione sicura dove il pLDDT medio della regione è almeno 70 (fasce di AlphaFold DB, valide per ESMFold); con meno di 30 residui sicuri il confronto non è conclusivo.</li>
            <li>Nel consenso di qualità l'accordo (lDDT sui residui sicuri) entra con peso 0,25, il più basso, solo con questo confronto.</li>
            {p.notes.map((n, i) => <li key={i}>{n}</li>)}
            <li className="muted">{p.limits}</li>
          </ul>
        </details>
      )}
    </div>
  );
}

/**
 * Riquadro del confronto con una predizione per una struttura: segue il confronto più recente (o in corso) della stessa
 * struttura, lo lancia con la finestra di avvio e mostra i risultati. `onResult` riceve il job completato (il consenso
 * della pagina Qualità lo include), o null se non c'è.
 */
export function PredictionPanel({ source, label, residues, onResult }: {
  source: StructureSource;
  label: string;
  residues?: number | null;
  onResult?: (jobId: string | null) => void;
}) {
  const key = `${source.type}|${source.id ?? ""}|${source.job_id ?? ""}|${source.file ?? ""}`;
  const { data: latest } = useAsync(() => api.latestPrediction(source).catch(() => null), [key]);
  const [chosen, setChosen] = useState<{ key: string; job: string | null }>({ key, job: null });
  const [open, setOpen] = useState(false);
  const jobId = followedJob(chosen.key === key ? chosen.job : null, latest);
  const { job, summary, cancel } = useJob<PredictResult>(jobId);
  const done = job?.status === "completed" && job.kind === "predict" && job.id === jobId ? job : null;
  const completedId = done?.result?.plausibility ? done.id : null;
  useEffect(() => {
    onResult?.(completedId);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [completedId]);
  return (
    <Card title="Confronto con una predizione" sub="controllo di plausibilità con ESMFold o AlphaFold DB"
      right={<button className="btn btn-sm" onClick={() => setOpen(true)} disabled={job?.status === "running" || job?.status === "queued"}><Icon name="compare" size={13} /> {done ? "Ripeti il confronto" : "Confronta con una predizione (ESMFold)"}</button>}>
      <div className="stack">
        {!jobId && (
          <p className="small muted">
            Confronta {label} con una predizione indipendente della stessa sequenza: TM-score, lDDT per residuo e regioni in
            cui i due divergono, incrociate con la confidenza della predizione (pLDDT) e con le regioni problematiche del
            consenso. Con ESMFold la sequenza lascia il computer, solo con il tuo consenso.
          </p>
        )}
        {jobId && !done && <JobPanel summary={summary} onCancel={cancel} compact />}
        {done?.result && (
          <>
            <span className="tiny muted">Confronto {relTime(done.finished_at)} · {done.title}</span>
            <PredictionResults key={done.id} jobId={done.id} result={done.result} />
          </>
        )}
      </div>
      {open && <PredictDialog source={source} label={label} residues={residues} onClose={() => setOpen(false)} onLaunched={(id) => setChosen({ key, job: id })} />}
    </Card>
  );
}

/** Vista di un job `predict` dal suo link (/quality?prjob=<id>), senza un'analisi di qualità. */
export function PredictionJobCard({ jobId }: { jobId: string }) {
  const { job, summary, cancel, error } = useJob<PredictResult>(jobId);
  const title = useMemo(() => (job ? job.title : "Confronto con una predizione"), [job]);
  const done = job?.status === "completed" && job.kind === "predict" ? (job as Job<PredictResult>) : null;
  return (
    <Card title={title}>
      {error ? <Alert tone="err">Confronto non trovato: {error}</Alert>
        : job && job.kind !== "predict" ? <Alert tone="warn">Il job {jobId} non è un confronto con una predizione.</Alert>
          : done?.result ? <PredictionResults jobId={done.id} result={done.result} />
            : summary ? <JobPanel summary={summary} onCancel={cancel} compact /> : <Spinner />}
      {done?.result?.predictor && <p className="tiny muted" style={{ marginTop: 8 }}>{PREDICTOR_LABEL[done.result.predictor]} · {originText(done.result)}</p>}
    </Card>
  );
}
