import { useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { api } from "../api/client";
import type { AlignResult, AlignStats, Sequence, StructureSource, SystemInfo } from "../api/types";
import { AlignmentView } from "../components/AlignmentView";
import { CachedBadge, RecomputeOption } from "../components/CacheOption";
import { Heatmap } from "../components/charts";
import { SequenceFileButton } from "../components/FileLoad";
import { Icon } from "../components/Icon";
import { JobPanel } from "../components/JobPanel";
import { MetricNotes } from "../components/MetricNotes";
import { SequencePicker, StructurePicker } from "../components/Pickers";
import { StructuralComparison } from "../components/StructuralComparison";
import { Alert, Card, DetailGrid, Disclosure, Empty, Field, PageHead, Segmented, Stat, Tabs } from "../components/ui";
import { compareLink, jobSources } from "../lib/compare";
import { identityTone } from "../lib/compareMetrics";
import { useProject, useToast } from "../lib/context";
import { recordsToFasta } from "../lib/files";
import { downloadText, fmtNum } from "../lib/format";
import { useAsync, useJob } from "../lib/hooks";

type Mode = "pairwise" | "multiple" | "structural";
const MATRICES = ["BLOSUM62", "BLOSUM45", "BLOSUM80", "PAM250", "PAM30", "GONNET1992"];
const MODE_LABEL: Record<Mode, string> = { pairwise: "Pairwise", multiple: "Multiplo", structural: "Strutturale" };
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
  const exportAs = async (format: "fasta" | "clustal" | "pir") => {
    try {
      const text = await api.convertAlignment(result.ids, result.aligned, format);
      downloadText(`allineamento.${format === "clustal" ? "aln" : format}`, text);
    } catch (e) {
      toast.error(e);
    }
  };
  const save = async () => {
    if (!projectId) return;
    try {
      const text = await api.convertAlignment(result.ids, result.aligned, "fasta");
      await api.saveDocument(projectId, { name: `${result.method.split(" ")[0]} ${result.ids.join(" / ").slice(0, 60)}`, kind: "fasta", content: text, meta: { job_id: jobId } });
      toast.push("Allineamento salvato nei documenti del progetto");
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
            <button type="button" className="btn btn-sm btn-primary" onClick={save}><Icon name="save" size={14} /> Salva nel progetto</button>
          </>
        }
      >
        <div className="stack">
          <AlignmentView ids={result.ids} aligned={result.aligned} conservation={result.conservation} downloadable={false} />
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
  const { data: info } = useAsync<SystemInfo>(() => api.systemInfo(), []);
  const { data: projectSeqs } = useAsync<Sequence[]>(() => (projectId ? api.sequences(projectId) : Promise.resolve([])), [projectId]);
  const { job, summary, log, cancel } = useJob<AlignResult>(jobId, { withLog: true });

  useEffect(() => {
    if (job?.params?.mode) setMode(job.params.mode as Mode);
  }, [job?.params?.mode]);

  const submit = async () => {
    try {
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
    mode === "pairwise" ? !!(a.sequence.trim() && b.sequence.trim()) : mode === "multiple" ? (msaText.match(/^>/gm)?.length ?? 0) >= 2 : !!(s1 && s2);

  return (
    <div className="page">
      <PageHead title="Allineamenti" lead="Allineamenti di sequenza (pairwise o multipli) e sovrapposizione di strutture 3D. I risultati si esportano in FASTA, Clustal o PIR per MODELLER." />
      <div className="split">
        <Card title="Nuovo allineamento" icon="align">
          <Tabs value={mode} onChange={setMode} tabs={(["pairwise", "multiple", "structural"] as const).map((m) => ({ value: m, label: MODE_LABEL[m] }))} />
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
          {jobId ? <JobPanel summary={summary} log={log} onCancel={cancel} /> : <Card><Empty icon="align" title="Configura e avvia un allineamento">Pairwise e multiplo allineano sequenze; Strutturale sovrappone due strutture 3D e ne misura la deviazione per residuo.</Empty></Card>}
          {job?.status === "completed" && job.result && <Results key={job.id} jobId={job.id} result={job.result} params={job.params} />}
        </div>
      </div>
    </div>
  );
}
