import { useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { api } from "../api/client";
import type { AlignResult, Sequence, StructureSource, SystemInfo } from "../api/types";
import { AlignmentView } from "../components/AlignmentView";
import { Heatmap } from "../components/charts";
import { SequenceFileButton } from "../components/FileLoad";
import { Icon } from "../components/Icon";
import { JobPanel } from "../components/JobPanel";
import { SequencePicker, StructurePicker } from "../components/Pickers";
import { Viewer3D } from "../components/Viewer3D";
import { Alert, Card, Empty, Field, PageHead, Segmented, Stat, Tabs } from "../components/ui";
import { useProject, useToast } from "../lib/context";
import { recordsToFasta } from "../lib/files";
import { downloadText, fmtNum } from "../lib/format";
import { useAsync, useJob } from "../lib/hooks";

type Mode = "pairwise" | "multiple" | "structural";
const MATRICES = ["BLOSUM62", "BLOSUM45", "BLOSUM80", "PAM250", "PAM30", "GONNET1992"];

function Results({ jobId, result }: { jobId: string; result: AlignResult }) {
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
  return (
    <div className="stack">
      {result.mode === "structural" ? (
        <div className="grid grid-4">
          <Stat label="TM-score" value={fmtNum(result.tm_score, 3)} note={(result.tm_score ?? 0) > 0.5 ? "stesso fold (> 0,5)" : "fold diverso (< 0,5)"} />
          <Stat label="RMSD" value={<>{fmtNum(result.rmsd, 2)}<small> Å</small></>} note={`su ${result.aligned_length} Cα allineati`} />
          <Stat label="Coppie vicine" value={result.close_pairs ?? "—"} note="Cα entro 5 Å" />
          <Stat label="Identità" value={s ? `${fmtNum(s.identity)}%` : "—"} note={result.lengths ? `lunghezze ${result.lengths.join(" / ")}` : undefined} />
        </div>
      ) : s ? (
        <div className="grid grid-4">
          <Stat label="Identità" value={`${fmtNum(s.identity)}%`} note={`${s.identities} residui identici`} />
          <Stat label="Similarità" value={`${fmtNum(s.similarity)}%`} note="BLOSUM62 > 0" />
          <Stat label="Gap" value={`${fmtNum(s.gap_percent)}%`} note={`${s.gaps} posizioni`} />
          <Stat label="Punteggio" value={fmtNum(result.score, 1)} note={`lunghezza ${s.length}`} />
        </div>
      ) : null}
      {result.note && <Alert tone="info">{result.note}</Alert>}
      {result.mode === "structural" && result.superposed_file && (
        <Card title="Sovrapposizione 3D" sub={`${result.ids[0]} (catena A) e ${result.ids[1]} (catena B)`}>
          <Viewer3D url={api.jobFileUrl(jobId, result.superposed_file)} scheme="chain" />
        </Card>
      )}
      <Card
        title="Allineamento"
        sub={result.method}
        right={
          <>
            <button className="btn btn-sm" onClick={() => exportAs("fasta")}>FASTA</button>
            <button className="btn btn-sm" onClick={() => exportAs("clustal")}>Clustal</button>
            <button className="btn btn-sm" onClick={() => exportAs("pir")}>PIR</button>
            <button className="btn btn-sm btn-primary" onClick={save}><Icon name="save" size={14} /> Salva nel progetto</button>
          </>
        }
      >
        <AlignmentView ids={result.ids} aligned={result.aligned} conservation={result.conservation} />
      </Card>
      {result.identity_matrix && result.ids.length > 2 && (
        <Card title="Matrice di identità" sub="Percentuale di identità tra coppie (posizioni senza gap)">
          <Heatmap ids={result.identity_matrix.ids} values={result.identity_matrix.values} />
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
      const j = await api.align({ ...body, project_id: projectId ?? undefined });
      setParams({ job: j.id });
    } catch (e) {
      toast.error(e);
    }
  };

  const toolAvailable = (key: string) => info?.tools[key]?.available;
  const canSubmit =
    mode === "pairwise" ? !!(a.sequence.trim() && b.sequence.trim()) : mode === "multiple" ? (msaText.match(/^>/gm)?.length ?? 0) >= 2 : !!(s1 && s2);

  return (
    <div className="page">
      <PageHead title="Allineamenti" lead="Allineamenti di sequenza (pairwise o multipli) e sovrapposizione di strutture 3D. I risultati si esportano in FASTA, Clustal o PIR per MODELLER." />
      <div className="split">
        <Card>
          <Tabs value={mode} onChange={setMode} tabs={[{ value: "pairwise", label: "Pairwise" }, { value: "multiple", label: "Multiplo" }, { value: "structural", label: "Strutturale" }]} />
          <div className="stack">
            {mode === "pairwise" && (
              <>
                <SequencePicker label="Sequenza A" value={a} onChange={setA} />
                <SequencePicker label="Sequenza B" value={b} onChange={setB} />
                <Field label="Algoritmo">
                  <Segmented value={algorithm} onChange={setAlgorithm} options={[{ value: "global", label: "Globale (NW)" }, { value: "local", label: "Locale (SW)" }]} />
                </Field>
                <div className="grid grid-3">
                  <Field label="Matrice">
                    <select className="input" value={matrix} onChange={(e) => setMatrix(e.target.value)}>
                      {MATRICES.map((m) => <option key={m}>{m}</option>)}
                    </select>
                  </Field>
                  <Field label="Apertura gap"><input className="input" type="number" step={0.5} value={gapOpen} onChange={(e) => setGapOpen(Number(e.target.value))} /></Field>
                  <Field label="Estensione gap"><input className="input" type="number" step={0.1} value={gapExtend} onChange={(e) => setGapExtend(Number(e.target.value))} /></Field>
                </div>
              </>
            )}
            {mode === "multiple" && (
              <>
                <Field label="Sequenze (multi-FASTA)" hint="Almeno due record con nomi univoci.">
                  <textarea className="input" rows={10} value={msaText} onChange={(e) => setMsaText(e.target.value)} placeholder={">seq1\nMKT...\n>seq2\nMKS..."} />
                </Field>
                <div className="row">
                  {!!projectSeqs?.length && (
                    <button className="btn btn-sm" onClick={() => setMsaText(projectSeqs.map((s) => `>${s.name}\n${s.sequence}`).join("\n"))}>
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
                <Field label="Programma">
                  <select className="input" value={msaTool} onChange={(e) => setMsaTool(e.target.value)}>
                    <option value="auto">Automatico (migliore disponibile)</option>
                    <option value="builtin">Center-star integrato</option>
                    {["clustalo", "mafft", "muscle"].map((t) => (
                      <option key={t} value={t} disabled={!toolAvailable(t)}>{info?.tools[t]?.label ?? t}{toolAvailable(t) ? "" : " (non installato)"}</option>
                    ))}
                  </select>
                </Field>
              </>
            )}
            {mode === "structural" && (
              <>
                <StructurePicker label="Struttura di riferimento" value={s1} onChange={setS1} />
                <Field label="Catena di riferimento" hint="Vuoto = prima catena"><input className="input" maxLength={2} value={chain1} onChange={(e) => setChain1(e.target.value.trim())} /></Field>
                <StructurePicker label="Struttura da sovrapporre" value={s2} onChange={setS2} />
                <Field label="Catena da sovrapporre" hint="Vuoto = prima catena"><input className="input" maxLength={2} value={chain2} onChange={(e) => setChain2(e.target.value.trim())} /></Field>
                <Field label="Programma">
                  <select className="input" value={structTool} onChange={(e) => setStructTool(e.target.value)}>
                    <option value="auto">Automatico</option>
                    <option value="builtin">Kabsch + TM-score integrato</option>
                    {["usalign", "tmalign"].map((t) => (
                      <option key={t} value={t} disabled={!toolAvailable(t)}>{info?.tools[t]?.label ?? t}{toolAvailable(t) ? "" : " (non installato)"}</option>
                    ))}
                  </select>
                </Field>
              </>
            )}
            <button className="btn btn-primary" disabled={!canSubmit} onClick={submit}><Icon name="play" size={14} /> Allinea</button>
          </div>
        </Card>
        <div className="stack">
          {jobId ? <JobPanel summary={summary} log={log} onCancel={cancel} /> : <Card><Empty icon="align" title="Configura e avvia un allineamento" /></Card>}
          {job?.status === "completed" && job.result && <Results jobId={job.id} result={job.result} />}
        </div>
      </div>
    </div>
  );
}
