import { useEffect, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { api } from "../api/client";
import type { Align2dResult, AlignResult, AlignStats, Sequence, StructureSource, SystemInfo, TemplateRef } from "../api/types";
import { AlignmentView } from "../components/AlignmentView";
import { CachedBadge, RecomputeOption } from "../components/CacheOption";
import { Heatmap } from "../components/charts";
import { SequenceFileButton } from "../components/FileLoad";
import { Icon } from "../components/Icon";
import { JobPanel } from "../components/JobPanel";
import { MetricNotes } from "../components/MetricNotes";
import { SequencePicker, StructurePicker, TemplatePicker } from "../components/Pickers";
import { PirWorkspace, type PirWorkspaceState } from "../components/PirWorkspace";
import { AlignmentEditor } from "../components/AlignmentEditor";
import { StructuralComparison } from "../components/StructuralComparison";
import { Alert, Card, DetailGrid, Disclosure, Empty, Field, PageHead, Segmented, Stat, Tabs } from "../components/ui";
import { FormSection } from "../components/WorkflowParts";
import { compareLink, jobSources } from "../lib/compare";
import { identityTone } from "../lib/compareMetrics";
import { useProject, useToast } from "../lib/context";
import { recordsToFasta } from "../lib/files";
import { downloadText, fmtNum } from "../lib/format";
import { useAsync, useJob } from "../lib/hooks";

type Mode = "pairwise" | "multiple" | "structural" | "seq2struct";
const MATRICES = ["BLOSUM62", "BLOSUM45", "BLOSUM80", "PAM250", "PAM30", "GONNET1992"];
const MODE_LABEL: Record<Mode, string> = { pairwise: "Pairwise", multiple: "Multiplo", structural: "Strutturale", seq2struct: "Seq → struttura" };
/** Una riga di spiegazione per modalità, sotto il selettore. */
const MODE_HINT: Record<Mode, string> = {
  pairwise: "Due sequenze, algoritmo globale o locale.",
  multiple: "Tre o più sequenze (Clustal Omega, MAFFT, MUSCLE o integrato).",
  seq2struct: "Bersaglio su uno o più template con MODELLER: il PIR per la modellazione, da correggere qui.",
  structural: "Sovrapposizione di due strutture 3D e deviazione per residuo.",
};
const ALGORITHM_LABEL: Record<string, string> = { global: "Globale (Needleman-Wunsch)", local: "Locale (Smith-Waterman)" };
/** Scelta automatica e programma integrato per modalità (i programmi esterni prendono il nome dalle informazioni di sistema). */
const BUILTIN_TOOLS: Record<"multiple" | "structural", Record<string, string>> = {
  multiple: { auto: "Automatico (migliore disponibile)", builtin: "Center-star integrato" },
  structural: { auto: "Automatico", builtin: "Kabsch + TM-score integrato" },
};

/** Parametro del job come testo ("—" se manca); `labels` traduce i valori noti. */
function param(params: Record<string, unknown> | undefined, key: string, labels?: Record<string, string>): string {
  const v = params?.[key];
  if (v === undefined || v === null || v === "") return "—";
  if (typeof v === "number") return fmtNum(v, 1);
  return labels?.[String(v)] ?? String(v);
}

/** Statistiche dell'allineamento di due sequenze, ognuna con la spiegazione e i valori grezzi. */
function AlignmentStats({ stats: s, score, params }: { stats: AlignStats; score?: number; params?: Record<string, unknown> }) {
  const ungapped = s.length - s.gaps;
  return (
    <div className="grid grid-4">
      <Stat label="Identità" value={`${fmtNum(s.identity)}%`} note={`${s.identities} residui identici`} tone={identityTone(s.identity)}
        detail={<MetricNotes metric="identity" values={[
          { label: "Posizioni senza gap", value: ungapped },
          { label: "Sulla sequenza più corta", value: `${fmtNum(s.identity_over_shorter)}%` },
          { label: "Copertura di A", value: `${fmtNum(s.coverage_a)}%` },
          { label: "Copertura di B", value: `${fmtNum(s.coverage_b)}%` },
        ]} />} />
      <Stat label="Similarità" value={`${fmtNum(s.similarity)}%`} note="BLOSUM62 > 0"
        detail={(
          <>
            <p>Posizioni senza gap con punteggio BLOSUM62 positivo: i residui identici più le sostituzioni frequenti tra proteine omologhe (es. I↔L, K↔R, D↔E).</p>
            <DetailGrid items={[{ label: "Posizioni senza gap", value: ungapped }, { label: "Identità", value: `${fmtNum(s.identity)}%` }]} />
          </>
        )} />
      <Stat label="Gap" value={`${fmtNum(s.gap_percent)}%`} note={`${s.gaps} posizioni`}
        detail={(
          <>
            <p>Colonne con un gap in una delle due sequenze, sul totale delle colonne. Un tratto di gap è un'inserzione o una delezione: nel modello diventa un loop da costruire senza template.</p>
            <DetailGrid items={[{ label: "Colonne", value: s.length }, { label: "Con un gap", value: s.gaps }]} />
          </>
        )} />
      <Stat label="Punteggio" value={fmtNum(score, 1)} note={`lunghezza ${s.length}`}
        detail={(
          <>
            <p>Punteggio dell'allineamento ottimo con la matrice e le penalità scelte: si confronta solo tra allineamenti fatti con gli stessi parametri.</p>
            <DetailGrid items={[
              { label: "Algoritmo", value: param(params, "algorithm", ALGORITHM_LABEL) },
              { label: "Matrice", value: param(params, "matrix") },
              { label: "Apertura gap", value: param(params, "gap_open") },
              { label: "Estensione gap", value: param(params, "gap_extend") },
            ]} />
          </>
        )} />
    </div>
  );
}

function Results({ jobId, result, params }: { jobId: string; result: AlignResult; params?: Record<string, unknown> }) {
  const { projectId } = useProject();
  const toast = useToast();
  const navigate = useNavigate();
  // allineamento modificato nella griglia: esportazione e salvataggio usano questo
  const [rows, setRows] = useState<string[]>(result.aligned);
  const edited = rows.some((r, i) => r !== result.aligned[i]);
  const exportAs = async (format: "fasta" | "clustal" | "pir") => {
    try {
      const text = await api.convertAlignment(result.ids, rows, format);
      downloadText(`allineamento.${format === "clustal" ? "aln" : format}`, text);
    } catch (e) {
      toast.error(e);
    }
  };
  const save = async () => {
    if (!projectId) return;
    try {
      const text = await api.convertAlignment(result.ids, rows, "fasta");
      await api.saveDocument(projectId, { name: `${result.method.split(" ")[0]} ${result.ids.join(" / ").slice(0, 60)}${edited ? " (modificato)" : ""}`, kind: "fasta", content: text, meta: { job_id: jobId, edited } });
      toast.push("Allineamento salvato nei documenti del progetto");
    } catch (e) {
      toast.error(e);
    }
  };
  /** Allineamento di sequenze → PIR nell'Editor PIR, dove si indicano i template (seconda riga di ogni voce). */
  const toPir = async () => {
    if (!projectId) return;
    try {
      const text = await api.convertAlignment(result.ids, rows, "pir");
      const d = await api.saveDocument(projectId, { name: `PIR da ${result.method.split(" ")[0]} ${result.ids.join(" / ").slice(0, 50)}`, kind: "pir", content: text, meta: { job_id: jobId } });
      toast.push("Convertito in PIR: nell'Editor PIR indica quali voci sono template (Seconda riga…, tipo structureX).", "info");
      navigate(`/pir?doc=${d.id}`);
    } catch (e) {
      toast.error(e);
    }
  };
  const s = result.stats;
  const sources = result.mode === "structural" ? jobSources(params) : null;
  const columns = result.aligned[0]?.length ?? 0;
  return (
    <div className="stack">
      {(result.cached || sources) && (
        <div className="row">
          {result.cached && <CachedBadge result={result} />}
          {sources && <Link className="btn btn-sm right" to={compareLink(sources[0], sources[1], jobId)}><Icon name="compare" size={14} /> Apri in Confronto</Link>}
        </div>
      )}
      {result.mode === "structural" ? (
        // metriche, nota, viewer colorato per distanza, grafico e tabella per residuo
        <StructuralComparison jobId={jobId} result={result} />
      ) : s ? (
        <AlignmentStats stats={s} score={result.score} params={params} />
      ) : null}
      {result.note && result.mode !== "structural" && <Alert tone="info">{result.note}</Alert>}
      <Card
        title="Allineamento"
        icon="align"
        sub={`${result.method} · ${result.ids.length} sequenze · ${columns} colonne`}
        collapsible
        right={
          <>
            <span className="small text-2">Esporta</span>
            <button type="button" className="btn btn-sm" onClick={() => exportAs("fasta")}>FASTA</button>
            <button type="button" className="btn btn-sm" onClick={() => exportAs("clustal")}>Clustal</button>
            <button type="button" className="btn btn-sm" onClick={() => exportAs("pir")}>PIR</button>
            {result.mode !== "structural" && (
              <button type="button" className="btn btn-sm" disabled={!projectId} onClick={toPir} title="Salva come PIR e lo apre nell'Editor PIR">Converti in PIR</button>
            )}
            <button type="button" className="btn btn-sm btn-primary" onClick={save}><Icon name="save" size={14} /> Salva nel progetto</button>
          </>
        }
      >
        <div className="stack">
          {edited && <Alert tone="info">Allineamento modificato a mano: statistiche e grafici si riferiscono a quello calcolato; esportazione e salvataggio usano quello modificato.</Alert>}
          <AlignmentEditor kind={result.mode} ids={result.ids} rows={rows} onRows={setRows} structures={sources ?? []} />
          <Disclosure title="Vista di sola lettura (confidenza, consenso)">
            <AlignmentView ids={result.ids} aligned={rows} conservation={edited ? undefined : result.conservation} downloadable={false} />
          </Disclosure>
          <Disclosure title="Metodo e parametri" extra={MODE_LABEL[result.mode]}>
            <DetailGrid items={[
              { label: "Metodo", value: result.method },
              { label: "Sequenze", value: result.ids.length },
              { label: "Colonne", value: columns },
              ...(result.mode === "pairwise"
                ? [
                  { label: "Algoritmo", value: param(params, "algorithm", ALGORITHM_LABEL) },
                  { label: "Matrice", value: param(params, "matrix") },
                  { label: "Apertura gap", value: param(params, "gap_open") },
                  { label: "Estensione gap", value: param(params, "gap_extend") },
                ]
                : [{ label: "Programma richiesto", value: param(params, "tool", BUILTIN_TOOLS[result.mode]) }]),
              ...(result.score != null ? [{ label: "Punteggio", value: fmtNum(result.score, 1) }] : []),
              { label: "Job", value: <span className="mono">{jobId}</span> },
            ]} />
          </Disclosure>
        </div>
      </Card>
      {result.identity_matrix && result.ids.length > 2 && (
        <Card>
          <div className="stack-sm">
            <Heatmap ids={result.identity_matrix.ids} values={result.identity_matrix.values} title="Matrice di identità a coppie"
              filename={`identita_${result.ids.length}_sequenze`} />
            <p className="small text-2">Percentuale di residui identici tra due sequenze, sulle posizioni allineate senza gap. «Espandi» mostra il valore di ogni cella e la tabella dei dati.</p>
          </div>
        </Card>
      )}
    </div>
  );
}

/**
 * Risultato dell'allineatore di MODELLER (bersaglio su uno o più template): il PIR si modifica subito nel banco di lavoro
 * dell'Editor PIR; da qui si salva come documento del progetto, si apre nell'Editor PIR o si passa alla modellazione.
 */
function SequenceStructureResult({ jobId, result }: { jobId: string; result: Align2dResult }) {
  const { projectId } = useProject();
  const toast = useToast();
  const navigate = useNavigate();
  const templates: TemplateRef[] = result.templates.map((t) => ({ id: t.id, chains: t.chains }));
  const [ws, setWs] = useState<PirWorkspaceState>({ content: result.pir, meta: { templates }, dirty: true, valid: false });
  const [savedKey, setSavedKey] = useState(0);
  const [docId, setDocId] = useState<string | null>(null);
  const [name, setName] = useState(`Allineamento MODELLER · ${templates.map((t) => t.id).join(", ")} (job ${jobId.slice(0, 6)})`);
  useEffect(() => {
    for (const w of result.warnings ?? []) toast.push(w, "warn");
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [jobId]);
  /** Salva (o aggiorna) il documento PIR del progetto; restituisce il suo id. */
  const save = async (): Promise<string | null> => {
    if (!projectId) {
      toast.push("Scegli un progetto per salvare l'allineamento.", "warn");
      return null;
    }
    try {
      const d = await api.saveDocument(projectId, { name: name || "Allineamento PIR", kind: "pir", content: ws.content, meta: { ...ws.meta, job_id: jobId } }, docId ?? undefined);
      setDocId(d.id);
      setSavedKey((k) => k + 1);
      toast.push("Allineamento salvato nei documenti del progetto");
      return d.id;
    } catch (e) {
      toast.error(e);
      return null;
    }
  };
  const saved = async () => (ws.dirty || !docId ? await save() : docId);
  return (
    <PirWorkspace
      source={{ key: jobId, text: result.pir, meta: { templates }, dirty: true }}
      savedKey={savedKey}
      onState={setWs}
      title={<input className="input" style={{ minWidth: 260, fontWeight: 600 }} aria-label="Nome del documento" value={name} onChange={(e) => setName(e.target.value)} />}
      right={
        <>
          <button className="btn btn-sm" disabled={!ws.content} onClick={() => downloadText(`${name || "alignment"}.ali`, ws.content)}><Icon name="download" size={14} /> .ali</button>
          <button className="btn btn-sm" disabled={!projectId} onClick={() => save()}><Icon name="save" size={14} /> Salva</button>
          <button className="btn btn-sm" disabled={!projectId} onClick={async () => { const id = await saved(); if (id) navigate(`/pir?doc=${id}`); }}
            title="Salva e apre l'allineamento nell'Editor PIR">Editor PIR</button>
          <button className="btn btn-sm btn-primary" disabled={!ws.valid || !projectId} onClick={async () => { const id = await saved(); if (id) navigate(`/modeling?doc=${id}`); }}>
            <Icon name="cube" size={14} /> Modella
          </button>
        </>
      }
    />
  );
}

export default function Alignment() {
  const { projectId } = useProject();
  const toast = useToast();
  const [params, setParams] = useSearchParams();
  const jobId = params.get("job");
  const [mode, setMode] = useState<Mode>("pairwise");
  const [a, setA] = useState({ name: "", sequence: "" });
  const [b, setB] = useState({ name: "", sequence: "" });
  const [algorithm, setAlgorithm] = useState<"global" | "local">("global");
  const [matrix, setMatrix] = useState("BLOSUM62");
  const [gapOpen, setGapOpen] = useState(10);
  const [gapExtend, setGapExtend] = useState(0.5);
  const [msaText, setMsaText] = useState("");
  const [msaTool, setMsaTool] = useState("auto");
  const [s1, setS1] = useState<StructureSource | null>(null);
  const [s2, setS2] = useState<StructureSource | null>(null);
  const [chain1, setChain1] = useState("");
  const [chain2, setChain2] = useState("");
  const [structTool, setStructTool] = useState("auto");
  const [recompute, setRecompute] = useState(false);
  // sequenza → struttura: bersaglio e template della libreria
  const [target, setTarget] = useState({ name: "", sequence: "" });
  const [templates, setTemplates] = useState<TemplateRef[]>([]);
  const { data: info } = useAsync<SystemInfo>(() => api.systemInfo(), []);
  const { data: projectSeqs } = useAsync<Sequence[]>(() => (projectId ? api.sequences(projectId) : Promise.resolve([])), [projectId]);
  const { job, summary, log, cancel } = useJob<AlignResult | Align2dResult>(jobId, { withLog: true });

  useEffect(() => {
    if (job?.kind === "align2d") setMode("seq2struct");
    else if (job?.params?.mode) setMode(job.params.mode as Mode);
  }, [job?.kind, job?.params?.mode]);

  const submit = async () => {
    try {
      if (mode === "seq2struct") {
        const j = await api.align2d({ project_id: projectId ?? undefined, target: { name: target.name || "target", sequence: target.sequence }, templates });
        setParams({ job: j.id });
        return;
      }
      let body: Record<string, unknown>;
      if (mode === "pairwise") {
        body = { mode, records: [{ name: a.name || "A", sequence: a.sequence }, { name: b.name || "B", sequence: b.sequence }], algorithm, matrix, gap_open: gapOpen, gap_extend: gapExtend };
      } else if (mode === "multiple") {
        const records = msaText
          .split(/^>/m)
          .filter((x) => x.trim())
          .map((block) => {
            const [head, ...rest] = block.split(/\r?\n/);
            return { name: head.trim().split(/\s+/)[0], sequence: rest.join("") };
          });
        body = { mode, records, tool: msaTool };
      } else {
        body = { mode, structures: [{ ...s1, chain: chain1 || undefined }, { ...s2, chain: chain2 || undefined }], tool: structTool };
      }
      // il pairwise è istantaneo e non passa dalla cache
      if (mode !== "pairwise") body.use_cache = !recompute;
      const j = await api.align({ ...body, project_id: projectId ?? undefined });
      setParams({ job: j.id });
    } catch (e) {
      toast.error(e);
    }
  };

  const toolAvailable = (key: string) => info?.tools[key]?.available;
  const toolName = (m: "multiple" | "structural", key: string) => BUILTIN_TOOLS[m][key] ?? info?.tools[key]?.label ?? key;
  // riepilogo della sezione chiusa: il programma e, se attivo, il ricalcolo senza cache (vale per ogni calcolo
  // successivo, anche dopo il cambio di modalità, quando la sezione della nuova modalità parte chiusa)
  const methodExtra = (m: "multiple" | "structural", key: string) => [toolName(m, key), recompute && "ricalcolo senza cache"].filter(Boolean).join(" · ");
  const canSubmit =
    mode === "pairwise" ? !!(a.sequence.trim() && b.sequence.trim())
      : mode === "multiple" ? (msaText.match(/^>/gm)?.length ?? 0) >= 2
        : mode === "seq2struct" ? !!(target.sequence.trim() && templates.length)
          : !!(s1 && s2);

  return (
    <div className="page">
      <PageHead title="Allineamenti" lead="Allineamenti di sequenza (pairwise o multipli), di una sequenza su una o più strutture (MODELLER, per la modellazione) e sovrapposizione di strutture 3D. I risultati si esportano in FASTA, Clustal o PIR per MODELLER." />
      {/* con un risultato la pagina va a tutta larghezza: il modulo si richiude in una barra e il job si riduce */}
      <div className={jobId ? "stack" : "split"}>
        <Card key={jobId ? "with-result" : "new"} title="Nuovo allineamento" icon="align" collapsible defaultOpen={!jobId}
          sub={jobId ? `${MODE_LABEL[mode]} · apri per cambiare i parametri o avviarne un altro` : undefined}>
          <Tabs value={mode} onChange={setMode} tabs={(["pairwise", "multiple", "seq2struct", "structural"] as const).map((m) => ({ value: m, label: MODE_LABEL[m] }))} />
          <p className="small text-2" style={{ margin: "6px 0 10px" }}>{MODE_HINT[mode]}</p>
          <div className="stack">
            {mode === "pairwise" && (
              <>
                <SequencePicker label="Sequenza A" value={a} onChange={setA} />
                <SequencePicker label="Sequenza B" value={b} onChange={setB} />
                <Field label="Algoritmo">
                  <Segmented label="Algoritmo" value={algorithm} onChange={setAlgorithm} options={[{ value: "global", label: "Globale (NW)" }, { value: "local", label: "Locale (SW)" }]} />
                </Field>
                <Disclosure title="Matrice e penalità di gap" extra={`${matrix} · ${fmtNum(gapOpen, 1)} / ${fmtNum(gapExtend, 1)}`}>
                  <div className="grid grid-3">
                    <Field label="Matrice">
                      <select className="input" value={matrix} onChange={(e) => setMatrix(e.target.value)}>
                        {MATRICES.map((m) => <option key={m}>{m}</option>)}
                      </select>
                    </Field>
                    <Field label="Apertura gap"><input className="input" type="number" step={0.5} value={gapOpen} onChange={(e) => setGapOpen(Number(e.target.value))} /></Field>
                    <Field label="Estensione gap"><input className="input" type="number" step={0.1} value={gapExtend} onChange={(e) => setGapExtend(Number(e.target.value))} /></Field>
                  </div>
                </Disclosure>
              </>
            )}
            {mode === "multiple" && (
              <>
                <Field label="Sequenze (multi-FASTA)" hint="Almeno due record con nomi univoci.">
                  <textarea className="input" rows={10} value={msaText} onChange={(e) => setMsaText(e.target.value)} placeholder={">seq1\nMKT...\n>seq2\nMKS..."} />
                </Field>
                <div className="row">
                  {!!projectSeqs?.length && (
                    <button type="button" className="btn btn-sm" onClick={() => setMsaText(projectSeqs.map((s) => `>${s.name}\n${s.sequence}`).join("\n"))}>
                      <Icon name="dna" size={14} /> Usa tutte le sequenze del progetto ({projectSeqs.length})
                    </button>
                  )}
                  <SequenceFileButton
                    onLoaded={(parsed) => {
                      setMsaText(recordsToFasta(parsed.records));
                      if (parsed.aligned) toast.push("Il file era già allineato: i gap sono stati tolti e le sequenze verranno riallineate.", "info");
                    }}
                  >
                    Carica da file
                  </SequenceFileButton>
                </div>
                <Disclosure title="Metodo e opzioni" extra={methodExtra("multiple", msaTool)}>
                  <div className="stack-sm">
                    <Field label="Programma">
                      <select className="input" value={msaTool} onChange={(e) => setMsaTool(e.target.value)}>
                        <option value="auto">{BUILTIN_TOOLS.multiple.auto}</option>
                        <option value="builtin">{BUILTIN_TOOLS.multiple.builtin}</option>
                        {["clustalo", "mafft", "muscle"].map((t) => (
                          <option key={t} value={t} disabled={!toolAvailable(t)}>{info?.tools[t]?.label ?? t}{toolAvailable(t) ? "" : " (non installato)"}</option>
                        ))}
                      </select>
                    </Field>
                    <RecomputeOption checked={recompute} onChange={setRecompute} />
                  </div>
                </Disclosure>
              </>
            )}
            {mode === "seq2struct" && (
              <>
                <SequencePicker label="Sequenza bersaglio" value={target} onChange={setTarget} />
                <FormSection title="Template (strutture)" hint={templates.length ? `${templates.length} scelti` : undefined}>
                  <TemplatePicker value={templates} onChange={setTemplates} ligands={false} />
                  <p className="small text-2">
                    Allineatore di MODELLER (align2d): con un template allinea il bersaglio alla sua struttura, con penalità di gap più alte
                    nelle eliche e nei filamenti; con più template prima li sovrappone fra loro (salign) e poi allinea il bersaglio sul profilo
                    comune. Il risultato è un PIR da controllare e correggere qui sotto.
                  </p>
                </FormSection>
              </>
            )}
            {mode === "structural" && (
              <>
                <div className="cmp-slot">
                  <StructurePicker label="Struttura di riferimento" value={s1} onChange={setS1} />
                  <Field label="Catena di riferimento" hint="Vuoto = prima catena"><input className="input" maxLength={2} value={chain1} onChange={(e) => setChain1(e.target.value.trim())} /></Field>
                </div>
                <div className="cmp-slot">
                  <StructurePicker label="Struttura da sovrapporre" value={s2} onChange={setS2} />
                  <Field label="Catena da sovrapporre" hint="Vuoto = prima catena"><input className="input" maxLength={2} value={chain2} onChange={(e) => setChain2(e.target.value.trim())} /></Field>
                </div>
                <Disclosure title="Metodo e opzioni" extra={methodExtra("structural", structTool)}>
                  <div className="stack-sm">
                    <Field label="Programma">
                      <select className="input" value={structTool} onChange={(e) => setStructTool(e.target.value)}>
                        <option value="auto">{BUILTIN_TOOLS.structural.auto}</option>
                        <option value="builtin">{BUILTIN_TOOLS.structural.builtin}</option>
                        {["usalign", "tmalign"].map((t) => (
                          <option key={t} value={t} disabled={!toolAvailable(t)}>{info?.tools[t]?.label ?? t}{toolAvailable(t) ? "" : " (non installato)"}</option>
                        ))}
                      </select>
                    </Field>
                    <RecomputeOption checked={recompute} onChange={setRecompute} />
                  </div>
                </Disclosure>
              </>
            )}
            <button type="button" className="btn btn-primary" disabled={!canSubmit} onClick={submit}><Icon name="play" size={14} /> Allinea</button>
          </div>
        </Card>
        <div className="stack">
          {jobId ? <JobPanel summary={summary} log={log} onCancel={cancel} compact={summary?.status === "completed"} /> : <Card><Empty icon="align" title="Configura e avvia un allineamento">Pairwise e multiplo allineano sequenze; Sequenza → struttura allinea il bersaglio a uno o più template con MODELLER e apre il PIR da correggere; Strutturale sovrappone due strutture 3D e ne misura la deviazione per residuo.</Empty></Card>}
          {job?.status === "completed" && job.result && (job.kind === "align2d"
            ? <SequenceStructureResult key={job.id} jobId={job.id} result={job.result as Align2dResult} />
            : <Results key={job.id} jobId={job.id} result={job.result as AlignResult} params={job.params} />)}
        </div>
      </div>
    </div>
  );
}
