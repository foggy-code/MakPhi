// Mutagenesi in silico e scansione di alanina (F9.2): scheda "Mutagenesi" della pagina Qualità, finestra di avvio dalla
// tabella dei modelli (Modellazione) e risultati dei job `mutate` e `alascan` (Modellazione, /modeling?job=).
// Mutazioni scritte nella notazione A:K48R oppure scelte fra le varianti di UniProt mappate sulla struttura; controlli
// preventivi con PreflightPanel e LaunchButton come le altre pagine. La logica è in lib/mutations.ts, il protocollo e
// le soglie nel backend (domain/mutations.py).
import { useEffect, useMemo, useRef, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api } from "../api/client";
import type {
  AlaScanResult, AlaScanRow, JobSummary, LatestToolJob, MutateResult, MutationClass, MutationHistoryItem, MutationSignal, PocketsResult, ScanMode,
  StructureSource, SystemInfo, VariantMapping,
} from "../api/types";
import { encodeSource } from "../lib/compare";
import { useProject, useToast } from "../lib/context";
import { fmtNum, relTime } from "../lib/format";
import { useAsync, useJob } from "../lib/hooks";
import { followedAnalysis } from "../lib/interactions";
import {
  addNotation, CATEGORY_LABEL, CATEGORY_TONE, CLASS_LABEL, CLASS_TONE, dopeText, energyText, historyOption, LEVEL_LABEL, LEVEL_TONE, MAX_SCAN,
  MAX_SCAN_ENERGY, modelQualityLink, mutateRequest, mutationCompareLinks, mutationPreflight, parseMutations, pocketsView, scanColoring, scanRequest,
  scanRowOf, scanRows, siteSummary, sitesFocus, variantRows, variantText, type VariantFilter,
} from "../lib/mutations";
import { fmtDelta, fmtRefineValue, verdictTone } from "../lib/refine";
import { resiNumber, type ResidueRef } from "../lib/residueColoring";
import { missingTools, rememberedJob, type ToolJobMemory } from "../lib/toolJobs";
import { Icon } from "./Icon";
import { JobPanel } from "./JobPanel";
import { LaunchButton, PreflightPanel, usePreflight } from "./Preflight";
import { ReportButton } from "./ReportButton";
import { sourceFileUrl } from "./StructuralAnalysis";
import { Viewer3D } from "./Viewer3D";
import { Alert, Badge, Card, Empty, Field, Modal, Segmented, Spinner, Stat } from "./ui";

type Mode = "mutate" | "alascan";

const PROTOCOL_TEXT = "MODELLER sostituisce il residuo e ottimizza la sua catena laterale e quelle entro 5 Å (protocollo mutate_model della Sali lab, con 3 modelli e il migliore per DOPE); il selvatico passa dallo stesso protocollo, così la differenza misura la sola sostituzione.";

function ClassBadge({ code, label }: { code: MutationClass; label?: string }) {
  return <Badge tone={CLASS_TONE[code]}>{label ?? CLASS_LABEL[code]}</Badge>;
}

function SignalList({ signals }: { signals: MutationSignal[] }) {
  if (!signals.length) return <p className="small muted">Nessun segnale.</p>;
  return (
    <ul className="small" style={{ margin: 0, paddingLeft: 18 }}>
      {signals.map((s, i) => (
        <li key={`${s.code}${i}`} style={{ marginBottom: 3 }}>
          <Badge tone={LEVEL_TONE[s.level]}>{LEVEL_LABEL[s.level]}</Badge> {s.site && <b>{s.site} </b>}{s.text}
        </li>
      ))}
    </ul>
  );
}

// ---------------------------------------------------------------------------------- varianti di UniProt
/** Varianti naturali e di mutagenesi di UniProt mappate sulla struttura: «Aggiungi» porta la notazione nel campo. */
function VariantPicker({ source, onAdd }: { source: StructureSource; onAdd: (notation: string) => void }) {
  const toast = useToast();
  const [accession, setAccession] = useState("");
  const [mapping, setMapping] = useState<VariantMapping | null>(null);
  const [busy, setBusy] = useState(false);
  const [filter, setFilter] = useState<VariantFilter>("applicable");
  const [query, setQuery] = useState("");
  const load = async () => {
    setBusy(true);
    try {
      setMapping(await api.uniprotVariants(accession.trim(), source));
    } catch (e) {
      setMapping(null);
      toast.error(e);
    } finally {
      setBusy(false);
    }
  };
  const rows = variantRows(mapping, filter, query);
  return (
    <div className="stack-sm">
      <div className="row small" style={{ gap: 8, alignItems: "end" }}>
        <Field label="Varianti di UniProt" hint="Accessione della proteina (es. P0CG48): varianti naturali, patologiche e di mutagenesi, numerate su UniProt e portate sulla struttura con un allineamento.">
          <input className="input" style={{ width: 150 }} placeholder="accessione" value={accession} onChange={(e) => setAccession(e.target.value.toUpperCase())} />
        </Field>
        <button className="btn btn-sm" disabled={!accession.trim() || busy} onClick={load}><Icon name="download" size={13} /> Carica le varianti</button>
      </div>
      {mapping && (
        <div className="stack-sm">
          <div className="small text-2">
            {mapping.entry_name || mapping.accession}: {mapping.variants.length} varianti puntuali, {mapping.applicable} applicabili alla catena {mapping.chain.trim() || "_"} (identità {fmtNum(mapping.identity, 1)}%, copertura {fmtNum(mapping.coverage, 1)}%).
            {mapping.notes.map((n, i) => <div key={i} className="tiny muted">{n}</div>)}
          </div>
          <div className="row small" style={{ gap: 8 }}>
            <Segmented value={filter} onChange={setFilter} options={[
              { value: "applicable", label: "Applicabili" }, { value: "malattia", label: "Malattia" }, { value: "mutagenesi", label: "Mutagenesi" }, { value: "all", label: "Tutte" },
            ]} />
            <input className="input" style={{ width: 180, minHeight: 28 }} placeholder="cerca (K48, malattia…)" value={query} onChange={(e) => setQuery(e.target.value)} />
          </div>
          {!rows.length ? <p className="small muted">Nessuna variante con questi filtri.</p> : (
            <div className="table-wrap" style={{ maxHeight: 260, overflowY: "auto" }}>
              <table className="table">
                <thead><tr><th>UniProt</th><th>Struttura</th><th>Tipo</th><th>Descrizione</th><th /></tr></thead>
                <tbody>
                  {rows.map((v) => (
                    <tr key={`${v.position}${v.variation}${v.feature_id}`}>
                      <td className="nowrap mono">{v.original}{v.position}{v.variation}{v.segment ? <span className="tiny muted"> · tratto {v.segment}</span> : null}</td>
                      <td className="nowrap">{v.applicable ? <b className="mono">{v.notation}</b> : <span className="tiny muted">{v.reason}</span>}</td>
                      <td><Badge tone={CATEGORY_TONE[v.category]}>{CATEGORY_LABEL[v.category]}</Badge></td>
                      <td className="small" style={{ minWidth: 200 }}>{v.description || "—"}{v.ids.length ? <span className="tiny muted"> · {v.ids.join(", ")}</span> : null}</td>
                      <td>{v.applicable && v.notation && <button className="btn btn-sm" title={variantText(v)} onClick={() => onAdd(v.notation!)}><Icon name="plus" size={13} /> Aggiungi</button>}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------------- avvio
/** Scelta delle tasche per la scansione: il calcolo di fpocket in corso sulla struttura (seguito fino alla fine, poi la
 *  tendina compare da sola), altrimenti il più recente completato (scheda Tasche). */
function usePockets(source: StructureSource, enabled: boolean) {
  const sourceKey = encodeSource(source) ?? "";
  const latest = useAsync<LatestToolJob | null>(async () => (enabled ? api.latestToolJob("pockets", source) : null), [sourceKey, enabled]);
  const jobId = enabled && latest.data ? followedAnalysis(null, latest.data) : null;
  const { job, summary, error: jobError } = useJob<PocketsResult>(jobId);
  const result = job?.status === "completed" && job.kind === "pockets" && job.id === jobId ? job.result ?? null : null;
  const error = latest.error?.message ?? jobError;
  const view = pocketsView({ loading: latest.loading, error, jobId, status: summary?.status, ready: !!result });
  return { view, error, jobId, result, reload: latest.reload };
}

/** Modulo di avvio: mutazioni (con le varianti di UniProt) o scansione di alanina, controlli preventivi, energia. */
export function MutationForm({ source, onLaunched, initialMode = "mutate" }: { source: StructureSource; onLaunched: (job: JobSummary) => void; initialMode?: Mode }) {
  const { projectId } = useProject();
  const [mode, setMode] = useState<Mode>(initialMode);
  const [text, setText] = useState("");
  const [energy, setEnergy] = useState(false);
  const [scanMode, setScanMode] = useState<ScanMode>("residues");
  const [residues, setResidues] = useState("");
  const [chain, setChain] = useState("");
  const [pocket, setPocket] = useState<number | null>(null);
  const { data: info } = useAsync<SystemInfo>(() => api.systemInfo(), []);
  const openmmMissing = missingTools(info, ["openmm", "pdbfixer"]).length > 0;
  const pockets = usePockets(source, mode === "alascan" && scanMode === "pocket");
  const mutate = useMemo(() => mutateRequest({ source, text, energy, projectId }), [source, text, energy, projectId]);
  const pocketJob = pockets.result ? pockets.jobId : null;
  const scan = useMemo(() => scanRequest({ source, mode: scanMode, residues, chain, pocketJob, pocket, energy, projectId }),
    [source, scanMode, residues, chain, pocketJob, pocket, energy, projectId]);
  const request = useMemo(() => (mode === "mutate" ? mutationPreflight("mutate", mutate.body) : mutationPreflight("alascan", scan.body)), [mode, mutate.body, scan.body]);
  const preflight = usePreflight(request);
  const parsed = parseMutations(text);
  const launch = async () => {
    const job = mode === "mutate" ? (mutate.body ? await api.mutate(mutate.body) : null) : (scan.body ? await api.alascan(scan.body) : null);
    if (job) onLaunched(job);
  };
  const error = mode === "mutate" ? (text.trim() ? mutate.error : null) : scan.error;
  return (
    <div className="stack">
      <Segmented value={mode} onChange={setMode} options={[{ value: "mutate", label: "Mutazioni" }, { value: "alascan", label: "Scansione di alanina" }]} />
      {mode === "mutate" ? (
        <div className="stack-sm">
          <Field label="Mutazioni" hint="Catena:residuo originale, numero, nuovo residuo (A:K48R o A:LYS48ARG); più mutazioni separate da virgole formano una sola variante. Numerazione della struttura.">
            <input className="input mono" placeholder="A:K48R, A:E51Q" value={text} onChange={(e) => setText(e.target.value)} />
          </Field>
          {parsed.items.length > 0 && !parsed.error && (
            <div className="row tiny" style={{ gap: 4 }}>{parsed.items.map((p) => <span key={p.token} className="chip">{p.chain ? `${p.chain}:` : ""}{p.wt}{p.resnum}{p.mut}</span>)}</div>
          )}
          <VariantPicker source={source} onAdd={(n) => setText((t) => addNotation(t, n))} />
        </div>
      ) : (
        <div className="stack-sm">
          <Segmented value={scanMode} onChange={setScanMode} options={[{ value: "residues", label: "Residui" }, { value: "interface", label: "Interfaccia" }, { value: "pocket", label: "Tasca" }]} />
          {scanMode === "residues" && (
            <Field label="Residui da portare in alanina" hint={`Es. A:30, A:43, A:45-48 (al massimo ${MAX_SCAN}, ${MAX_SCAN_ENERGY} con l'energia). Glicina, prolina e alanina si escludono.`}>
              <input className="input mono" placeholder="A:30, A:43, A:45-48" value={residues} onChange={(e) => setResidues(e.target.value)} />
            </Field>
          )}
          {scanMode === "interface" && (
            <Field label="Catena (facoltativa)" hint="Residui d'interfaccia (ΔSASA > 1 Å² fra catene) di questa catena; vuoto = tutte le catene.">
              <input className="input" style={{ width: 90 }} maxLength={4} value={chain} onChange={(e) => setChain(e.target.value)} />
            </Field>
          )}
          {scanMode === "pocket" && (pockets.view === "loading" ? <p className="small muted">Lettura delle tasche…</p>
            : pockets.view === "error" ? (
              <Alert tone="err">
                <div className="row" style={{ gap: 8 }}>
                  <span>Tasche non lette: {pockets.error}</span>
                  <button className="btn btn-sm" onClick={pockets.reload}><Icon name="refresh" size={13} /> Riprova</button>
                </div>
              </Alert>
            ) : pockets.view === "running" ? <p className="small muted"><Spinner /> Calcolo delle tasche in corso: la tasca si sceglie appena finisce.</p>
              : pockets.view === "failed" ? (
                <Alert tone="warn">
                  <div className="row" style={{ gap: 8 }}>
                    <span>L'ultimo calcolo delle tasche non è riuscito: rilancialo dalla scheda Tasche della pagina Qualità.</span>
                    <button className="btn btn-sm" onClick={pockets.reload}><Icon name="refresh" size={13} /> Rileggi</button>
                  </div>
                </Alert>
              ) : pockets.view === "none" || !pockets.result ? <Alert tone="info">Nessun calcolo delle tasche su questa struttura: avvialo dalla scheda Tasche della pagina Qualità.</Alert> : (
                <Field label="Tasca di fpocket" hint="I residui che rivestono la tasca diventano alanina.">
                  <select className="input" style={{ width: 320 }} value={pocket ?? ""} onChange={(e) => setPocket(e.target.value ? Number(e.target.value) : null)}>
                    <option value="">— scegli —</option>
                    {pockets.result.pockets.map((p) => <option key={p.id} value={p.id}>Tasca {p.id} · druggability {fmtNum(p.druggability ?? null, 2)} · {p.residues.length} residui</option>)}
                  </select>
                </Field>
              ))}
        </div>
      )}
      <label className="check">
        <input type="checkbox" checked={energy} disabled={openmmMissing} onChange={(e) => setEnergy(e.target.checked)} />
        Energia con OpenMM (minimizzazione di selvatico, mutante e tripeptidi di riferimento: circa un minuto per mutazione)
      </label>
      {openmmMissing && <span className="tiny muted">OpenMM o PDBFixer non sono in questa installazione: la stima userà DOPE e indicatori strutturali.</span>}
      <p className="tiny muted">{PROTOCOL_TEXT}</p>
      {error && <span className="tiny" style={{ color: "var(--warn)" }}>{error}</span>}
      <PreflightPanel state={preflight} />
      <LaunchButton preflight={preflight} disabled={!request} onLaunch={launch}>{mode === "mutate" ? "Avvia la mutagenesi" : "Avvia la scansione di alanina"}</LaunchButton>
    </div>
  );
}

/** Finestra di avvio (tabella dei modelli e riquadro delle strutture in Modellazione): apre il job in /modeling?job=. */
export function MutateDialog({ source, label, onClose }: { source: StructureSource; label: string; onClose: () => void }) {
  const toast = useToast();
  const navigate = useNavigate();
  return (
    <Modal title={`Mutagenesi di ${label}`} onClose={onClose} wide footer={<button className="btn" onClick={onClose}>Chiudi</button>}>
      <MutationForm source={source} onLaunched={(j) => {
        toast.push(j.kind === "alascan" ? "Scansione di alanina avviata" : "Mutagenesi avviata");
        onClose();
        navigate(`/modeling?job=${j.id}`);
      }} />
    </Modal>
  );
}

// ---------------------------------------------------------------------------------- risultati
/** Risultato di un job `mutate`: classe con le motivazioni, DOPE ed energia, siti, confronto, 3D, link e report. */
export function MutationResults({ jobId, result }: { jobId: string; result: MutateResult }) {
  const a = result.assessment;
  const links = mutationCompareLinks(jobId, result);
  const focus = useMemo(() => sitesFocus(result.mutations), [result]);
  return (
    <div className="stack">
      {result.warnings.map((w, i) => <Alert key={i} tone="warn">{w}</Alert>)}
      <Card title={<span className="row" style={{ gap: 8 }}>{result.notation} <ClassBadge code={a.code} label={a.label} /></span>} sub={a.summary}>
        <div className="stack">
          <div className="grid grid-4">
            <Stat label="Effetto stimato" value={<ClassBadge code={a.code} label={a.label} />} note="stima qualitativa, non un ΔΔG" />
            <Stat label="ΔDOPE" value={<span style={{ fontSize: 18 }}>{dopeText(result.dope)}</span>} note={`locale ${result.dope.delta_local != null ? fmtNum(result.dope.delta_local, 0) : "—"} su ${result.dope.local_residues} residui entro ${result.dope.local_radius} Å`} />
            <Stat label="ΔΔE (OpenMM)" value={<span style={{ fontSize: 16 }}>{energyText(result.energy)}</span>} note="energia con il riferimento del tripeptide" />
            <Stat label="Catena principale" value={<>{fmtNum(result.rmsd.all, 3)}<small> Å</small></>} note={`RMSD selvatico–mutante; ${fmtNum(result.rmsd.outside, 3)} Å fuori dai residui ottimizzati`} />
          </div>
          {a.binding && <Alert tone={a.binding.hotspot ? "warn" : "info"}>{a.binding.text}</Alert>}
          <div className="stack-sm">
            <h4>Motivazioni</h4>
            <SignalList signals={a.signals} />
          </div>
          <Alert tone="info">{result.caveat}</Alert>
        </div>
      </Card>
      <Card title="Siti" sub="indicatori del selvatico riottimizzato e del mutante">
        <div className="table-wrap">
          <table className="table">
            <thead><tr><th>Mutazione</th><th>Contesto</th><th className="num">Δ superficie apolare sepolta</th><th>Interazioni perse</th><th className="num">Clash nuovi</th><th>Interfaccia</th></tr></thead>
            <tbody>
              {result.sites.map((s) => (
                <tr key={s.site}>
                  <td className="nowrap mono"><b>{s.label}</b></td>
                  <td className="small">{siteSummary(s)}{s.helix_propensity != null && <div className="tiny muted">propensione per l'elica {s.helix_propensity > 0 ? "+" : ""}{fmtNum(s.helix_propensity, 2)} kcal/mol</div>}</td>
                  <td className="num">{fmtNum(s.buried_delta.apolar, 0)} Å²</td>
                  <td className="small">{s.interactions.lost.length ? s.interactions.lost.map((x) => `${x.partner} (${x.type === "hbond" ? `legame H, ${x.role}` : x.type === "salt_bridge" ? "ponte salino" : x.type === "disulfide" ? "disolfuro" : "aromatica"})`).join("; ") : "nessuna"}</td>
                  <td className="num">{Math.max(0, s.clashes.mutant - s.clashes.wild_type)}</td>
                  <td>{s.interface ? <Badge tone="info">ΔSASA {fmtNum(s.interface.delta, 0)} Å²</Badge> : "no"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Card>
      <Card title="Selvatico riottimizzato e mutante" sub="stesse metriche di qualità sui due modelli"
        right={
          <>
            <Link className="btn btn-sm" to={modelQualityLink(jobId, result.files.mutant)}><Icon name="shield" size={13} /> Qualità del mutante</Link>
            <Link className="btn btn-sm" to={modelQualityLink(jobId, result.files.wild_type)}><Icon name="shield" size={13} /> Qualità del selvatico</Link>
            <Link className="btn btn-sm" to={links.wildType} title="Deviazione per residuo rispetto al selvatico riottimizzato"><Icon name="compare" size={13} /> Confronta con il selvatico</Link>
            <Link className="btn btn-sm" to={links.original} title="Deviazione per residuo rispetto alla struttura di partenza"><Icon name="compare" size={13} /> Con l'originale</Link>
            <a className="btn btn-sm" href={api.jobFileUrl(jobId, result.files.mutant, true)}><Icon name="download" size={13} /> PDB mutante</a>
            {result.files.mutant_minimized && (
              <a className="btn btn-sm" href={api.jobFileUrl(jobId, result.files.mutant_minimized, true)} title="Mutante minimizzato con OpenMM (restrizioni sulla catena principale)"><Icon name="download" size={13} /> Rifinito (OpenMM)</a>
            )}
            <ReportButton jobId={jobId} model={result.files.mutant} />
          </>
        }>
        <div className="table-wrap">
          <table className="table">
            <thead><tr><th>Metrica</th><th className="num">Selvatico</th><th className="num">Mutante</th><th className="num">Differenza</th><th>Esito</th></tr></thead>
            <tbody>
              {result.comparison.map((row) => (
                <tr key={row.key}>
                  <td>{row.label}</td>
                  <td className="num">{fmtRefineValue(row, row.before)}</td>
                  <td className="num">{fmtRefineValue(row, row.after)}</td>
                  <td className="num">{fmtDelta(row)}</td>
                  <td>{row.verdict ? <Badge tone={verdictTone(row.verdict)}>{row.verdict}</Badge> : <span className="tiny muted">—</span>}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Card>
      <Card title={`Mutante · ${result.files.mutant}`} sub="residui mutati a bastoncini arancioni">
        <Viewer3D url={api.jobFileUrl(jobId, result.files.mutant)} highlight={focus} centerKey={jobId} height={380} />
      </Card>
      <details className="small text-2">
        <summary>Protocollo, modelli e fonti</summary>
        <ul style={{ margin: "6px 0 0", paddingLeft: 18 }}>
          <li><b>MODELLER</b>, {result.protocol.method} (Feyfant, Sali & Fiser 2007): residuo randomizzato di {fmtNum(result.protocol.randomize, 1)} Å e ottimizzato, poi con le catene laterali entro {fmtNum(result.protocol.radius, 1)} Å; {result.protocol.models} modelli per variante (semi {result.protocol.seeds.join(", ")}), {result.protocol.selection}.</li>
          <li><b>Modelli</b>: {result.models.map((m) => `${m.role === "wild_type" ? "selvatico" : "mutante"} ${m.seed}: DOPE ${fmtNum(m.dope, 0)}${m.chosen ? " (scelto)" : ""}`).join("; ")}.</li>
          <li><b>DOPE</b> (Shen & Sali 2006): {fmtNum(result.dope.wild_type, 1)} → {fmtNum(result.dope.mutant, 1)}; residuo equivalente = |DOPE del selvatico| / residui = {fmtNum(result.dope.per_residue, 1)}. Segnale moderato da 1 residuo equivalente, forte da 2.</li>
          {result.energy?.ddE != null && <li><b>OpenMM {result.energy.openmm}</b>: {result.energy.protocol}; E selvatico {fmtNum(result.energy.wild_type ?? null, 1)}, mutante {fmtNum(result.energy.mutant ?? null, 1)}, riferimenti {fmtNum(result.energy.reference_wild_type ?? null, 1)} e {fmtNum(result.energy.reference_mutant ?? null, 1)} kJ/mol.</li>}
          <li>Soglie e fonti degli indicatori: elica (Pace & Scholtz 1998; MacArthur & Thornton 1991), filamenti (Minor & Kim 1994), legami idrogeno (Myers & Pace 1996), ponti salini (Strop & Mayo 2000), effetto idrofobico (Chothia 1974), disolfuri (Betz 1993), cariche sepolte (Isom et al. 2011), BLOSUM62 (Henikoff & Henikoff 1992), RSA (Tien et al. 2013), nucleo sotto il {fmtNum(result.protocol.core_rsa * 100, 0)}% di RSA.</li>
        </ul>
      </details>
    </div>
  );
}

const SCAN_FILTERS: { value: MutationClass | "all" | "hotspot"; label: string }[] = [
  { value: "all", label: "Tutte" }, { value: "destabilizing", label: "Destabilizzanti" }, { value: "uncertain", label: "Incerte" },
  { value: "neutral", label: "Neutre" }, { value: "hotspot", label: "Hot spot" },
];

/** Risultato di un job `alascan`: tabella dalla sostituzione più destabilizzante, 3D colorato per effetto, dettagli. */
export function AlaScanResults({ jobId, result }: { jobId: string; result: AlaScanResult }) {
  const [filter, setFilter] = useState<MutationClass | "all" | "hotspot">("all");
  const [selected, setSelected] = useState<AlaScanRow | null>(null);
  const [centerKey, setCenterKey] = useState(0);
  const url = sourceFileUrl(result.source);
  const coloring = useMemo(() => scanColoring(result), [result]);
  const rows = scanRows(result, filter);
  const s = result.summary;
  const pick = (r: AlaScanRow) => {
    setSelected(r);
    setCenterKey((k) => k + 1);
  };
  const pick3d = (ref: ResidueRef) => setSelected(scanRowOf(result, ref.chain, ref.resnum));
  const focus = selected ? [{ chain: selected.mutation.chain, resi: resiNumber(selected.mutation.resnum) }] : [];
  return (
    <div className="stack">
      {result.warnings.map((w, i) => <Alert key={i} tone="warn">{w}</Alert>)}
      <div className="grid grid-4">
        <Stat label="Residui in alanina" value={s.residues} note={`${result.selection.detail}; ${s.skipped} esclusi`} />
        <Stat label="Probabilmente destabilizzanti" value={s.counts.destabilizing} note={`incerte ${s.counts.uncertain} · neutre ${s.counts.neutral}`} />
        <Stat label="Hot spot d'interfaccia" value={result.interface ? s.hotspots : "—"} note={result.interface ? "probabili o possibili (Bogan & Thorn 1998)" : "struttura con una sola catena"} />
        <Stat label="Energia" value={result.protocol.energy ? "OpenMM" : "—"} note={result.protocol.energy ? "ΔΔE per residuo" : "solo DOPE e indicatori"} />
      </div>
      <Alert tone="info">{result.caveat}</Alert>
      <Card title="Scansione di alanina" sub="dalla sostituzione più destabilizzante: classe, poi segnali, poi ΔDOPE"
        right={
          <>
            <a className="btn btn-sm" href={api.jobFileUrl(jobId, result.csv, true)}><Icon name="download" size={13} /> CSV</a>
            <ReportButton jobId={jobId} />
          </>
        }>
        <div className="stack-sm">
          <Segmented value={filter} onChange={setFilter} options={SCAN_FILTERS} />
          <div className="table-wrap" style={{ maxHeight: 380, overflowY: "auto" }}>
            <table className="table">
              <thead><tr><th>Mutazione</th><th>Effetto</th><th className="num">ΔDOPE</th><th className="num" title="residui equivalenti">eq.</th><th className="num">Δ fra catene</th><th className="num">ΔΔE kJ/mol</th><th className="num">RSA</th><th>DSSP</th><th>Interfaccia</th><th>Motivo principale</th></tr></thead>
              <tbody>
                {rows.map((r) => (
                  <tr key={r.label} className={`clickable${selected?.label === r.label ? " selected" : ""}`} onClick={() => pick(r)}>
                    <td className="nowrap mono"><b>{r.label}</b></td>
                    <td><ClassBadge code={r.assessment.code} label={r.assessment.label} /></td>
                    <td className="num">{fmtNum(r.dope.delta, 0)}</td>
                    <td className="num">{fmtNum(r.dope.delta_eq, 2)}</td>
                    <td className="num">{fmtNum(r.dope.interchain_delta, 0)}</td>
                    <td className="num">{fmtNum(r.energy?.ddE ?? null, 0)}</td>
                    <td className="num">{r.site.rsa_wt != null ? `${fmtNum(r.site.rsa_wt * 100, 0)}%` : "—"}</td>
                    <td>{r.site.ss}</td>
                    <td>{r.assessment.binding?.hotspot ? <Badge tone="warn">hot spot {r.assessment.binding.hotspot}</Badge> : r.site.interface ? "sì" : "no"}</td>
                    <td className="small" style={{ minWidth: 240 }}>{r.assessment.reasons[0] ?? "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {!rows.length && <p className="small muted">Nessuna riga con questo filtro.</p>}
        </div>
      </Card>
      {selected && (
        <Card title={<span className="row" style={{ gap: 8 }}>{selected.label} <ClassBadge code={selected.assessment.code} label={selected.assessment.label} /></span>} sub={`${siteSummary(selected.site)} · ΔDOPE ${dopeText(selected.dope)}`}
          right={<Link className="btn btn-sm" to={mutationCompareLinks(jobId, { files: selected.files, source: result.source }).wildType}><Icon name="compare" size={13} /> Confronta con il selvatico</Link>}>
          <div className="stack-sm">
            {selected.assessment.binding && <Alert tone={selected.assessment.binding.hotspot ? "warn" : "info"}>{selected.assessment.binding.text}</Alert>}
            <SignalList signals={selected.assessment.signals} />
          </div>
        </Card>
      )}
      {url && coloring ? (
        <Card title="Struttura colorata per effetto stimato" sub="clic su un residuo scandito: la sua riga">
          <Viewer3D url={url} format={url.endsWith(".cif") ? "cif" : "pdb"} coloring={coloring} highlight={focus} centerKey={centerKey} onPickResidue={pick3d} height={380} />
        </Card>
      ) : null}
      {result.skipped.length > 0 && (
        <p className="small text-2">Esclusi: {result.skipped.map((x) => `${x.resname} ${x.chain.trim() || "_"}${x.resnum} (${x.reason.split(":")[0]})`).join(", ")}.</p>
      )}
      {result.failed.length > 0 && <Alert tone="warn">Non calcolati: {result.failed.map((f) => `${f.label} (${f.error})`).join("; ")}.</Alert>}
    </div>
  );
}

// ---------------------------------------------------------------------------------- scheda della pagina Qualità
/** Job seguito dal pannello: avanzamento, poi i risultati. `onStatus` riceve ogni cambio di stato (la cronologia si
 *  rilegge a fine job) e "gone" se il job non c'è più (eliminato altrove: la voce sparisce dalla cronologia). */
function ResultView({ jobId, compact = false, onStatus }: { jobId: string; compact?: boolean; onStatus?: (status: string) => void }) {
  const { job, summary, cancel, error, gone } = useJob<MutateResult | AlaScanResult>(jobId);
  const status = gone ? "gone" : summary?.status ?? "";
  useEffect(() => {
    if (status) onStatus?.(status);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [status]);
  if (!summary) return error ? <Alert tone="err">{gone ? "Job non trovato" : "Job non letto"}: {error}</Alert> : <Spinner />;
  if (job?.status !== "completed" || !job.result) return <JobPanel summary={summary} onCancel={cancel} compact={compact} />;
  return job.kind === "alascan" ? <AlaScanResults jobId={jobId} result={job.result as AlaScanResult} /> : <MutationResults jobId={jobId} result={job.result as MutateResult} />;
}

/** Scheda "Mutagenesi" della pagina Qualità: avvio, cronologia della struttura e risultato seguito (conservato dalla
 *  pagina quando si cambia scheda). */
export function MutagenesisPanel({ source, memory, onMemory }: { source: StructureSource; memory?: ToolJobMemory | null; onMemory?: (m: ToolJobMemory) => void }) {
  const sourceKey = encodeSource(source) ?? "";
  const start = useRef(rememberedJob(sourceKey, memory, null));
  const [jobId, setJobId] = useState<string | null>(start.current);
  const [showForm, setShowForm] = useState(!start.current);
  const { data: history, reload } = useAsync<MutationHistoryItem[]>(() => api.mutationHistory(source), [sourceKey, jobId]);
  useEffect(() => {
    onMemory?.({ sourceKey, jobId });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sourceKey, jobId]);
  const open = (id: string) => {
    setJobId(id);
    setShowForm(false);
  };
  return (
    <div className="stack">
      <div className="row small" style={{ gap: 10 }}>
        <button className={`btn btn-sm${showForm ? "" : " btn-primary"}`} onClick={() => setShowForm(!showForm)}>
          <Icon name="dna" size={13} /> {showForm ? "Nascondi il modulo" : "Nuova mutagenesi o scansione"}
        </button>
        {history && history.length > 0 && (
          <select className="input" style={{ width: 340, minHeight: 28, padding: "2px 6px" }} value={jobId ?? ""} onChange={(e) => e.target.value && open(e.target.value)}>
            <option value="">Calcoli su questa struttura ({history.length})…</option>
            {history.map((h) => <option key={h.id} value={h.id}>{historyOption(h, relTime)}</option>)}
          </select>
        )}
        <button className="btn btn-sm btn-ghost" onClick={reload} title="Rileggi la cronologia"><Icon name="refresh" size={13} /></button>
      </div>
      {showForm && <Card><MutationForm source={source} onLaunched={(j) => open(j.id)} /></Card>}
      {jobId ? <ResultView key={jobId} jobId={jobId} compact onStatus={reload} /> : !showForm && (
        <Empty icon="dna" title="Nessuna mutagenesi su questa struttura">
          Mutazioni puntuali o multiple (anche dalle varianti di UniProt) e scansione di alanina, con una stima qualitativa dell'effetto sulla stabilità.
        </Empty>
      )}
    </div>
  );
}
