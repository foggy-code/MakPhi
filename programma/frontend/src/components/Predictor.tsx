// Confronto con un predittore esterno (F9.3): pulsante «Confronta con una predizione (ESMFold)» nelle pagine Qualità e
// Modellazione, finestra con il consenso all'invio (ESMFold: la sequenza lascia il computer), controlli preventivi e
// avvio, risultati del job `predict` (esito motivato, accordo per residuo, regioni che divergono e confronto con
// StructuralComparison), vista da un link (/quality?prjob=). La logica è in lib/predict.ts e lib/toolsView.ts, le
// regole nel backend (domain/prediction.py).
import { Fragment, useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api/client";
import type { Job, PlausibilityRegion, PredictorName, PredictResult, Settings, StructureSource } from "../api/types";
import { useProject, useToast } from "../lib/context";
import { fmtNum, relTime } from "../lib/format";
import { useAsync, useJob } from "../lib/hooks";
import {
  consentText, ESMFOLD_MAX_LENGTH, esmfoldBlocked, esmfoldService, followedJob, metricsLine, needsConsent, originText, pastedSequence, PREDICTOR_LABEL,
  predictionCompareLink, predictionQualityLink, predictPreflight, predictRequest, STATUS_TONE, verdictTitle,
} from "../lib/predict";
import { PLDDT_SCALE } from "../lib/residueColoring";
import { lddtData, lddtSeries, plddtFractionsText, type LddtMetric } from "../lib/toolsView";
import { Icon } from "./Icon";
import { JobPanel } from "./JobPanel";
import { LaunchButton, PreflightPanel, usePreflight } from "./Preflight";
import { StructuralComparison } from "./StructuralComparison";
import { ExpandAll } from "./ToolParts";
import { ValueBars } from "./ValueBars";
import { Alert, Badge, Card, DetailGrid, Disclosure, ExpandButton, Field, Modal, rowActivate, Segmented, Spinner, Stat, useExpanded } from "./ui";

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
  const { data: loaded, error: settingsError, loading: settingsLoading, reload: reloadSettings } = useAsync<Settings>(() => api.settings(), []);
  const [settings, setSettings] = useState<Settings | null>(null);
  const current = settings ?? loaded;
  const [predictor, setPredictor] = useState<PredictorName>("esmfold");
  const [accession, setAccession] = useState("");
  const [sequence, setSequence] = useState("");
  const [saving, setSaving] = useState(false);
  const consent = needsConsent(predictor, current);
  // sequenza nota e oltre il limite di ESMFold: niente consenso per un invio che non può avvenire
  const blocked = esmfoldBlocked(predictor, residues, sequence);
  const own = pastedSequence(sequence);
  const req = predictRequest(predictor, source, { accession, projectId, sequence, name: label });
  // AlphaFold DB non chiede il consenso: non dipende dalle impostazioni
  const ready = predictor === "alphafold" || !!current;
  // i controlli partono solo a consenso dato: prima la sequenza non deve nemmeno sembrare in partenza
  const preflight = usePreflight(ready && !blocked && (predictor === "alphafold" || !consent) ? predictPreflight(req.body) : null);
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
        <Segmented label="Predittore" value={predictor} onChange={setPredictor} options={[
          { value: "esmfold", label: "ESMFold (servizio esterno)" },
          { value: "alphafold", label: "AlphaFold DB (accessione)" },
        ]} />
        {predictor === "alphafold" && (
          <Field label="Accessione UniProt" hint="Es. P0CG48, oppure AF-<ACC>-F2 per un frammento: a EBI va solo l'accessione; il modello si salva anche nella libreria dei template.">
            <input className="input mono" style={{ width: 220 }} value={accession} maxLength={40} onChange={(e) => setAccession(e.target.value.trim())} />
          </Field>
        )}
        {predictor === "esmfold" && (
          <Field label="Sequenza (facoltativa)" hint="Vuota = la sequenza della catena del modello. Se la catena ha residui mancanti, incolla qui la sequenza completa (SEQRES o UniProt): unire i tratti darebbe una proteina che non esiste.">
            <textarea className="input mono" rows={2} value={sequence} maxLength={12000} placeholder="sequenza della catena" onChange={(e) => setSequence(e.target.value)} />
            {own && <span className="tiny text-2">{own.length} residui</span>}
          </Field>
        )}
        {blocked ? (
          <>
            <Alert tone="warn">ESMFold accetta al più {ESMFOLD_MAX_LENGTH} residui ({own ? "la sequenza indicata" : "questa catena"} ne ha {own ? own.length : residues}): confronta con AlphaFold DB indicando l'accessione, oppure incolla un dominio alla volta.</Alert>
            <div className="row"><button type="button" className="btn" onClick={onClose}>Annulla</button></div>
          </>
        ) : !ready ? (settingsError && !settingsLoading ? (
          <Alert tone="err">
            <div className="stack-sm">
              <span>Impostazioni non lette: {settingsError.message}</span>
              <div className="row" style={{ gap: 8 }}>
                <button type="button" className="btn btn-sm" onClick={reloadSettings}><Icon name="refresh" size={13} /> Riprova</button>
                <button type="button" className="btn btn-sm" onClick={onClose}>Annulla</button>
              </div>
            </div>
          </Alert>
        ) : <Spinner />) : predictor === "esmfold" && consent ? (
          <Alert tone="warn">
            <div className="stack-sm">
              <b>Consenso all'invio della sequenza</b>
              <span>{consentText(esmfoldService(current), own ? own.length : residues ?? null)}</span>
              <div className="row" style={{ gap: 8 }}>
                <button type="button" className="btn btn-primary btn-sm" disabled={saving} onClick={giveConsent}>{saving ? <Spinner /> : <Icon name="check" size={13} />} Acconsento e continua</button>
                <button type="button" className="btn btn-sm" onClick={onClose}>Annulla</button>
              </div>
            </div>
          </Alert>
        ) : (
          <>
            {predictor === "esmfold" && <p className="small text-2">La sequenza va a {esmfoldService(current)} (consenso dato in Impostazioni → Generale).</p>}
            <PreflightPanel state={preflight} />
            <div className="row" style={{ gap: 8, alignItems: "start" }}>
              <LaunchButton preflight={preflight} disabled={!req.body} onLaunch={launch}>Avvia il confronto</LaunchButton>
              <button type="button" className="btn" onClick={onClose}>Annulla</button>
            </div>
            {req.error && <span className="small text-2">{req.error}</span>}
          </>
        )}
      </div>
    </Modal>
  );
}

const REGION_COLUMNS = 7;
const regionKey = (r: PlausibilityRegion) => `${r.chain}${r.start}`;

/** Regioni che divergono: righe espandibili (valori medi, regioni del consenso); il clic evidenzia il tratto nel grafico. */
function RegionTable({ regions, band, onBand }: { regions: PlausibilityRegion[]; band: { lo: number; hi: number } | null; onBand: (r: PlausibilityRegion) => void }) {
  const open = useExpanded<string>();
  const allOpen = open.size === regions.length;
  return (
    <div className="stack-sm">
      <div className="tools-bar">
        <h4>Regioni che divergono</h4>
        <span className="small text-2">clic su una riga: il tratto nel grafico; di nuovo per toglierlo</span>
        {regions.length > 1 && <div className="right"><ExpandAll allOpen={allOpen} onExpand={() => open.expandAll(regions.map(regionKey))} onCollapse={open.collapseAll} /></div>}
      </div>
      <div className="table-wrap">
        <table className="table">
          <thead>
            <tr>
              <th style={{ width: 34 }} aria-label="Dettagli" />
              <th>Regione</th><th className="num">Residui</th><th className="num">lDDT medio</th><th className="num">pLDDT medio</th><th>Predizione</th><th>Indicazione</th>
            </tr>
          </thead>
          <tbody>
            {regions.map((r) => {
              const key = regionKey(r);
              const isOpen = open.has(key);
              const sel = !!band && band.lo === r.start_index && band.hi === r.end_index;
              return (
                <Fragment key={key}>
                  <tr className={`clickable${sel ? " selected" : isOpen ? " row-open" : ""}`} aria-selected={sel} {...rowActivate(() => onBand(r))}>
                    <td><ExpandButton open={isOpen} onClick={() => open.toggle(key)} label={`dettagli della regione ${r.start}-${r.end}`} /></td>
                    <td className="nowrap"><b>{r.start}–{r.end}</b> <span className="text-2">catena {r.chain}</span></td>
                    <td className="num">{r.divergent}/{r.length}</td>
                    <td className="num">{fmtNum(r.lddt_mean, 2)}</td>
                    <td className="num">{fmtNum(r.plddt_mean, 0)}</td>
                    <td><Badge tone={r.confidence === "sicura" ? "warn" : "info"}>{r.confidence ?? "pLDDT assente"}</Badge></td>
                    <td className="small" style={{ minWidth: 260 }}>{r.text}</td>
                  </tr>
                  {isOpen && (
                    <tr className="row-detail">
                      <td colSpan={REGION_COLUMNS}>
                        <div className="stack-sm">
                          <DetailGrid items={[
                            { label: "Dove", value: r.where },
                            { label: "Residui divergenti", value: `${r.divergent} su ${r.length}` },
                            { label: "lDDT medio", value: fmtNum(r.lddt_mean, 3) },
                            { label: "pLDDT medio della predizione", value: fmtNum(r.plddt_mean, 1) },
                          ]} />
                          {r.consensus.length > 0 ? (
                            <>
                              <h4>Regioni problematiche del consenso che vi cadono</h4>
                              <ul className="tools-list">{r.consensus.map((c, i) => <li key={i}>{c}</li>)}</ul>
                            </>
                          ) : <span className="small text-2">Nessuna regione problematica del consenso in questo tratto.</span>}
                        </div>
                      </td>
                    </tr>
                  )}
                </Fragment>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}

/** Risultati di un job `predict`: esito motivato, metriche, accordo per residuo, regioni, link e confronto. */
export function PredictionResults({ jobId, result }: { jobId: string; result: PredictResult }) {
  const p = result.plausibility;
  const [metric, setMetric] = useState<LddtMetric>("lddt");
  const [band, setBand] = useState<{ lo: number; hi: number } | null>(null);
  const series = useMemo(() => (p ? lddtSeries(p, metric) : null), [p, metric]);
  const fractions = plddtFractionsText(result.plddt.fractions ?? {});
  return (
    <div className="stack">
      {result.warnings.map((w, i) => <Alert key={i} tone="warn">{w}</Alert>)}
      {p ? (
        <Alert tone={STATUS_TONE[p.status]}>
          <b>{verdictTitle(p)}.</b> {p.summary}
        </Alert>
      ) : <Alert tone="info">Predizione senza confronto: nessuna struttura indicata.</Alert>}
      <div className="grid grid-4">
        {p && <Stat label="TM-score (sul modello)" value={fmtNum(p.tm_score, 3)} note="da 0,5 stesso ripiegamento (Xu & Zhang 2010)" tone={STATUS_TONE[p.status]}
          detail={<DetailGrid items={[
            { label: "GDT-TS", value: fmtNum(p.gdt_ts, 1) },
            { label: "RMSD", value: p.rmsd != null ? `${fmtNum(p.rmsd, 2)} Å` : "—" },
            { label: "Coppie di residui", value: String(p.pairs) },
          ]} />} />}
        {p && <Stat label="lDDT" value={fmtNum(p.lddt, 3)} note={p.lddt_confident != null ? `${fmtNum(p.lddt_confident, 3)} dove la predizione è sicura` : "predizione mai sicura"} />}
        <Stat label="pLDDT della predizione" value={fmtNum(result.plddt.mean, 1)} note={p ? `${p.confident_residues} residui su ${p.pairs} con pLDDT ≥ 70` : `${result.plddt.residues} residui`}
          detail={fractions ? <span>Residui per fascia: {fractions}.</span> : undefined} />
        {p && <Stat label="Residui che divergono" value={p.divergent_residues} note="lDDT sotto 0,5 rispetto alla predizione" tone={p.divergent_residues ? "warn" : "ok"} />}
      </div>
      {p && series && series.items.length > 0 && (
        <ValueBars
          title="Accordo per residuo con la predizione"
          subtitle={metric === "lddt" ? "lDDT-Cα del modello rispetto alla predizione: sotto 0,5 il residuo diverge" : "pLDDT della predizione per residuo, con i colori di AlphaFold DB"}
          filename={`predizione_${result.label}_${metric}`}
          series={series}
          data={() => lddtData(p)}
          noun="residui"
          band={band}
          describe={(i) => {
            const r = p.residues[i];
            return (
              <div className="text-2">
                lDDT {fmtNum(r.lddt, 2)} · pLDDT {fmtNum(r.plddt, 1)}
                {r.divergent ? (r.confident ? " · diverge dove la predizione è sicura" : " · diverge, predizione incerta") : ""}
                {r.junction ? " · vicino a una giunzione" : ""}
              </div>
            );
          }}
          controls={<Segmented label="Metrica" value={metric} onChange={setMetric} options={[{ value: "lddt", label: "lDDT" }, { value: "plddt", label: "pLDDT" }]} />}
          legend={metric === "lddt" ? (
            <div className="legend">
              <span><i style={{ background: "var(--accent)" }} />Concorda</span>
              <span><i style={{ background: "var(--warn)" }} />Diverge dove la predizione è sicura</span>
              <span><i style={{ background: "var(--coil)" }} />Diverge con predizione incerta, o giunzione</span>
              {band && <span><i style={{ background: "var(--warn-soft)", boxShadow: "inset 0 0 0 1px var(--warn)" }} />Regione scelta</span>}
            </div>
          ) : (
            <div className="legend">
              {PLDDT_SCALE.bins.map((b) => <span key={b.label}><i style={{ background: b.color }} />{b.label}</span>)}
            </div>
          )}
        />
      )}
      {p && p.regions.length > 0 && (
        <RegionTable regions={p.regions} band={band} onBand={(r) => setBand((b) => (b && b.lo === r.start_index && b.hi === r.end_index ? null : { lo: r.start_index, hi: r.end_index }))} />
      )}
      <div className="row small" style={{ gap: 8 }}>
        <Badge tone="info" title={result.recognized_as ? "Riconosciuta come modello predetto: la pagina Qualità mostra il pLDDT" : undefined}>{result.label}</Badge>
        <span className="text-2">{originText(result)}{p ? ` · ${metricsLine(p)}` : ""}</span>
      </div>
      <div className="row" style={{ gap: 6 }}>
        <Link className="btn btn-sm" to={predictionQualityLink(jobId, result)}><Icon name="shield" size={13} /> Qualità della predizione</Link>
        {result.source && <Link className="btn btn-sm" to={predictionCompareLink(jobId, result)} title="Pagina Confronto con il modello come riferimento (A), per altre opzioni (TM-align)"><Icon name="compare" size={13} /> Apri in Confronto</Link>}
        <a className="btn btn-sm" href={api.jobFileUrl(jobId, result.prediction_file, true)}><Icon name="download" size={13} /> PDB della predizione</a>
      </div>
      {result.comparison && <StructuralComparison jobId={jobId} result={result.comparison} />}
      {p && (
        <Disclosure title="Regole, note e limiti" extra={p.notes.length ? `${p.notes.length} note` : undefined}>
          <ul className="tools-list">
            <li>Residui abbinati per sequenza (stessa proteina); A = modello (riferimento), B = predizione. lDDT-Cα per residuo del modello (Mariani et al. 2013): sotto 0,5 il residuo diverge; regioni di almeno 3 residui divergenti.</li>
            <li>Predizione sicura dove il pLDDT medio della regione è almeno 70 (fasce di AlphaFold DB, valide per ESMFold); con meno di 30 residui sicuri il confronto non è conclusivo.</li>
            <li>Nel consenso di qualità l'accordo (lDDT sui residui sicuri) entra con peso 0,25, il più basso, solo con questo confronto.</li>
            {p.notes.map((n, i) => <li key={i}>{n}</li>)}
            <li className="text-2">{p.limits}</li>
          </ul>
        </Disclosure>
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
    // eslint-disable-next-line react-hooks/exhaustive-deps -- si avvisa solo il cambio di risultato; onResult può cambiare a ogni disegno del genitore
  }, [completedId]);
  return (
    <Card icon="compare" title="Confronto con una predizione" sub="controllo di plausibilità con ESMFold o AlphaFold DB"
      right={
        <button type="button" className="btn btn-sm" onClick={() => setOpen(true)} disabled={job?.status === "running" || job?.status === "queued"}>
          <Icon name="compare" size={13} /> {done ? "Ripeti il confronto" : "Confronta con una predizione (ESMFold)"}
        </button>
      }>
      <div className="stack">
        {!jobId && (
          <p className="small text-2">
            Confronta {label} con una predizione indipendente della stessa sequenza: TM-score, lDDT per residuo e regioni in
            cui i due divergono, incrociate con la confidenza della predizione (pLDDT) e con le regioni problematiche del
            consenso. Con ESMFold la sequenza lascia il computer, solo con il tuo consenso.
          </p>
        )}
        {jobId && !done && <JobPanel summary={summary} onCancel={cancel} compact />}
        {done?.result && (
          <>
            <span className="small text-2">Confronto {relTime(done.finished_at)} · {done.title}</span>
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
    <Card icon="compare" title={title}>
      {error ? <Alert tone="err">Confronto non trovato: {error}</Alert>
        : job && job.kind !== "predict" ? <Alert tone="warn">Il job {jobId} non è un confronto con una predizione.</Alert>
          : done?.result ? <PredictionResults jobId={done.id} result={done.result} />
            : summary ? <JobPanel summary={summary} onCancel={cancel} compact /> : <Spinner />}
      {done?.result?.predictor && <p className="small text-2" style={{ marginTop: 8 }}>{PREDICTOR_LABEL[done.result.predictor]} · {originText(done.result)}</p>}
    </Card>
  );
}
