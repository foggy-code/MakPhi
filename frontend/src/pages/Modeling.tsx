import { useEffect, useMemo, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { api } from "../api/client";
import type { DocumentItem, JobSummary, LoopResult, ModelEntry, ModelResult, Sequence, StructureSource, SystemInfo, TemplateRef } from "../api/types";
import { AlignmentView } from "../components/AlignmentView";
import { Icon } from "../components/Icon";
import { JobPanel } from "../components/JobPanel";
import { TextFileButton } from "../components/FileLoad";
import { SequencePicker, StructurePicker, TemplatePicker } from "../components/Pickers";
import { Viewer3D } from "../components/Viewer3D";
import { Alert, Badge, Card, Empty, Field, Grade, Modal, PageHead, Segmented, Stat } from "../components/ui";
import { pirReference } from "../lib/alignConfidence";
import { useProject, useToast } from "../lib/context";
import { fileStem, formatRestraints, parseRestraints, type RestraintRow } from "../lib/files";
import { downloadText, fmtNum, relTime } from "../lib/format";
import { useAsync, useJob } from "../lib/hooks";
import { parsePir } from "../lib/pir";

const REFINEMENT = [
  { value: "very_fast", label: "Rapidissimo", note: "Test o screening: ottimizzazione minima." },
  { value: "fast", label: "Rapido", note: "Buon compromesso per molti modelli." },
  { value: "normal", label: "Standard", note: "Impostazioni predefinite di MODELLER." },
  { value: "thorough", label: "Accurato", note: "Ottimizzazione ripetuta, dinamica lenta: ~3–5× più lento." },
];

type Restraint = RestraintRow;

function ModelsTable({ jobId, models, selected, onSelect }: { jobId: string; models: ModelEntry[]; selected: string; onSelect: (f: string) => void }) {
  const navigate = useNavigate();
  return (
    <div className="table-wrap">
      <table className="table">
        <thead>
          <tr>
            <th>Modello</th>
            <th className="num" title="Energia statistica DOPE: più negativa è migliore">DOPE</th>
            <th className="num" title="DOPE normalizzato: &lt; −1 simile a strutture native">zDOPE</th>
            <th className="num" title="GA341 &gt; 0,7: fold affidabile">GA341</th>
            <th className="num">molpdf</th>
            <th>Qualità</th>
            <th />
          </tr>
        </thead>
        <tbody>
          {models.map((m) => (
            <tr key={m.file} className={`clickable ${m.file === selected ? "selected" : ""}`} onClick={() => !m.failure && onSelect(m.file)}>
              <td className="nowrap">
                <b>{m.file}</b> {m.best && <Badge tone="ok">migliore</Badge>} {m.kind === "loop" && <Badge tone="accent">loop</Badge>}
                {m.failure && <div className="tiny" style={{ color: "var(--err)" }}>{m.failure}</div>}
              </td>
              <td className="num">{fmtNum(m.dope, 1)}</td>
              <td className="num">{fmtNum(m.zdope, 2)}</td>
              <td className="num">{fmtNum(m.ga341, 2)}</td>
              <td className="num">{fmtNum(m.molpdf, 1)}</td>
              <td>{m.quality ? <span className="row" style={{ gap: 6 }}><Grade grade={m.quality.grade} /> <span className="tiny muted">{fmtNum(m.quality.rama_favored)}% fav.</span></span> : <span className="tiny muted">—</span>}</td>
              <td className="nowrap" onClick={(e) => e.stopPropagation()}>
                <button className="btn btn-sm" disabled={!!m.failure} title="Analisi di qualità completa" onClick={() => navigate(`/quality?job=${jobId}&file=${encodeURIComponent(m.file)}`)}>
                  <Icon name="shield" size={13} />
                </button>{" "}
                <a className="btn btn-sm" href={api.jobFileUrl(jobId, m.file, true)} title="Scarica PDB"><Icon name="download" size={13} /></a>{" "}
                <a className="btn btn-sm" href={api.pymolUrl(jobId, m.file)} title="Sessione PyMOL (.pml)">PyMOL</a>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function LoopDialog({ source, label, onClose }: { source: StructureSource; label: string; onClose: () => void }) {
  const { projectId } = useProject();
  const toast = useToast();
  const navigate = useNavigate();
  const [chain, setChain] = useState("A");
  const [start, setStart] = useState(1);
  const [end, setEnd] = useState(8);
  const [models, setModels] = useState(4);
  const [level, setLevel] = useState("fast");
  const submit = async () => {
    try {
      const j = await api.loop({ project_id: projectId ?? undefined, source, chain, start, end, models, level });
      toast.push("Raffinamento del loop avviato");
      onClose();
      navigate(`/modeling?job=${j.id}`);
    } catch (e) {
      toast.error(e);
    }
  };
  return (
    <Modal title={`Raffina un loop di ${label}`} onClose={onClose} footer={<><button className="btn" onClick={onClose}>Annulla</button><button className="btn btn-primary" onClick={submit}>Avvia</button></>}>
      <div className="stack">
        <p className="small text-2">MODELLER ricostruisce la regione indicata con il protocollo DOPE-loop mantenendo fisso il resto del modello. Numerazione del modello (catena A per un bersaglio a catena singola).</p>
        <div className="grid grid-3">
          <Field label="Catena"><input className="input" value={chain} maxLength={1} onChange={(e) => setChain(e.target.value.toUpperCase())} /></Field>
          <Field label="Primo residuo"><input className="input" type="number" value={start} onChange={(e) => setStart(Number(e.target.value))} /></Field>
          <Field label="Ultimo residuo"><input className="input" type="number" value={end} onChange={(e) => setEnd(Number(e.target.value))} /></Field>
          <Field label="Modelli di loop"><input className="input" type="number" min={1} max={50} value={models} onChange={(e) => setModels(Number(e.target.value))} /></Field>
          <Field label="Dinamica">
            <select className="input" value={level} onChange={(e) => setLevel(e.target.value)}>
              <option value="very_fast">Molto rapida</option>
              <option value="fast">Rapida</option>
              <option value="slow">Lenta</option>
            </select>
          </Field>
        </div>
      </div>
    </Modal>
  );
}

function Results({ jobId, result, kind }: { jobId: string; result: ModelResult | LoopResult; kind: string }) {
  const { projectId } = useProject();
  const toast = useToast();
  const [selected, setSelected] = useState(result.best_model);
  const [loop, setLoop] = useState(false);
  const best = result.models.find((m) => m.best);
  const full = kind === "model" ? (result as ModelResult) : null;
  const entries = useMemo(() => (full ? parsePir(full.alignment) : []), [full]);
  const savePir = async () => {
    if (!projectId || !full) return;
    try {
      const templates: TemplateRef[] = full.templates.map((t) => ({ id: t.id, chains: t.chains }));
      await api.saveDocument(projectId, { name: `Allineamento ${full.target.code} (job ${jobId.slice(0, 6)})`, kind: "pir", content: full.alignment, meta: { templates, job_id: jobId } });
      toast.push("PIR salvato nei documenti del progetto");
    } catch (e) {
      toast.error(e);
    }
  };
  return (
    <div className="stack">
      {full?.warnings?.map((w, i) => <Alert key={i} tone="warn">{w}</Alert>)}
      {best?.quality && (
        <div className="grid grid-4">
          <Stat label="Voto del modello migliore" value={<span className="row"><Grade grade={best.quality.grade} large /> <span>{fmtNum(best.quality.grade_score, 1)}<small>/100</small></span></span>} />
          <Stat label="Ramachandran" value={<>{fmtNum(best.quality.rama_favored)}<small>% favorite</small></>} note={`${fmtNum(best.quality.rama_outliers)}% outlier`} />
          <Stat label="Clashscore" value={fmtNum(best.quality.clashscore, 2)} note="contatti gravi / 1000 atomi" />
          <Stat label="DOPE" value={fmtNum(best.dope, 0)} note={`zDOPE ${fmtNum(best.zdope, 2)} · GA341 ${fmtNum(best.ga341, 2)}`} />
        </div>
      )}
      {best?.quality && (best.quality.prosa_z != null || best.quality.procheck_core != null) && (
        <div className="grid grid-4">
          <Stat label="ProSA II z-score" value={fmtNum(best.quality.prosa_z ?? null, 2)} note="energia del ripiegamento" />
          <Stat label="PROCHECK core" value={<>{fmtNum(best.quality.procheck_core ?? null)}<small>%</small></>} note={`G-factor ${fmtNum(best.quality.procheck_g_overall ?? null, 2)}`} />
        </div>
      )}
      <Card
        title={`${result.models.length} modelli`}
        sub="Il migliore ha il DOPE più basso; l'analisi completa è calcolata sul migliore"
        right={
          <>
            <button className="btn btn-sm" onClick={() => setLoop(true)}><Icon name="wand" size={14} /> Raffina un loop</button>
            <a className="btn btn-sm" href={api.archiveUrl(jobId)}><Icon name="download" size={14} /> Archivio completo</a>
          </>
        }
      >
        <ModelsTable jobId={jobId} models={result.models} selected={selected} onSelect={setSelected} />
      </Card>
      <Card title={`Struttura 3D · ${selected}`}>
        <Viewer3D url={api.jobFileUrl(jobId, selected)} />
      </Card>
      {full && (
        <Card title="Allineamento usato da MODELLER" right={<button className="btn btn-sm" onClick={savePir}><Icon name="save" size={14} /> Salva PIR nel progetto</button>}>
          <div className="stack-sm">
            <div className="small text-2">
              Template: {full.templates.map((t) => `${t.code} (${t.first}–${t.last}${t.ligands ? `, ${t.ligands} ligandi` : ""})`).join(", ")}
            </div>
            {entries.length > 1 && <AlignmentView ids={entries.map((e) => e.code)} aligned={entries.map((e) => e.sequence)} reference={pirReference(entries.map((e) => e.kind))} />}
          </div>
        </Card>
      )}
      {loop && <LoopDialog source={{ type: "job", job_id: jobId, file: selected }} label={selected} onClose={() => setLoop(false)} />}
    </div>
  );
}

export default function Modeling() {
  const { projectId } = useProject();
  const toast = useToast();
  const [params, setParams] = useSearchParams();
  const jobId = params.get("job");
  const [mode, setMode] = useState<"auto" | "pir">("auto");
  const [target, setTarget] = useState({ name: "", sequence: "" });
  const [templates, setTemplates] = useState<TemplateRef[]>([]);
  const [docId, setDocId] = useState("");
  const [nModels, setNModels] = useState(5);
  const [refinement, setRefinement] = useState("fast");
  const [quality, setQuality] = useState<"best" | "all" | "none">("best");
  const [extended, setExtended] = useState(true);
  const [advanced, setAdvanced] = useState(false);
  const [loopOn, setLoopOn] = useState(false);
  const [loopModels, setLoopModels] = useState(4);
  const [loopRegions, setLoopRegions] = useState("");
  const [restraints, setRestraints] = useState<Restraint[]>([]);
  const [seed, setSeed] = useState(-8123);
  const [loopSource, setLoopSource] = useState<StructureSource | null>(null);
  const [loopOpen, setLoopOpen] = useState(false);
  const { data: info } = useAsync<SystemInfo>(() => api.systemInfo(), []);
  const { data: docs, reload: reloadDocs } = useAsync<DocumentItem[]>(() => (projectId ? api.documents(projectId) : Promise.resolve([])), [projectId]);
  const { data: recent } = useAsync<JobSummary[]>(() => api.jobs({ project_id: projectId ?? undefined, kind: "model", limit: 8 }), [projectId, jobId]);
  const { job, summary, log, cancel } = useJob<ModelResult | LoopResult>(jobId, { withLog: true });

  // Parametri in arrivo da altre pagine (sequenza, template, documento PIR).
  useEffect(() => {
    const seqId = params.get("seq");
    const seqText = params.get("seqtext");
    const t = params.getAll("t");
    const doc = params.get("doc");
    if (seqText) setTarget({ name: params.get("name") || "target", sequence: seqText });
    if (seqId && projectId) api.sequences(projectId).then((l: Sequence[]) => { const s = l.find((x) => x.id === seqId); if (s) setTarget({ name: s.name, sequence: s.sequence }); });
    if (t.length) setTemplates(t.map((x) => { const [id, ch] = x.split(":"); return { id, chains: ch ? ch.split("") : [] }; }));
    if (doc) {
      setMode("pir");
      setDocId(doc);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [projectId]);

  useEffect(() => {
    if (mode !== "pir" || !docId || !projectId) return;
    api.document(projectId, docId).then((d) => {
      const tm = (d.meta?.templates as TemplateRef[] | undefined) ?? [];
      if (tm.length) setTemplates(tm);
    }).catch(() => undefined);
  }, [mode, docId, projectId]);

  const submit = async () => {
    try {
      let alignment: string | null = null;
      if (mode === "pir") {
        if (!projectId || !docId) throw new Error("Scegli un documento PIR.");
        alignment = (await api.document(projectId, docId)).content ?? "";
      }
      const regions = loopRegions
        .split(/[,;\n]/)
        .map((r) => r.trim())
        .filter(Boolean)
        .map((r) => {
          const [s, e] = r.split("-").map((x) => x.trim());
          return { start: s, end: e };
        });
      const options = {
        models: nModels,
        refinement,
        seed,
        loop: { enabled: loopOn, models: loopModels, level: "fast", regions },
        restraints,
      };
      const j = await api.model({
        project_id: projectId ?? undefined,
        target: mode === "auto" ? { name: target.name || "target", sequence: target.sequence } : null,
        alignment,
        templates,
        options,
        quality,
        extended_quality: quality !== "none" && extended,
      });
      setParams({ job: j.id });
    } catch (e) {
      toast.error(e);
    }
  };

  /** PIR da file: validato e salvato tra i documenti del progetto, poi selezionato. */
  const loadPirFile = async (text: string, file: File) => {
    if (!projectId) return;
    try {
      const report = await api.validatePir(text);
      if (!report.entries.length) {
        toast.push("Il file non contiene blocchi PIR ('>P1;'). Per altri formati di allineamento usa l'Editor PIR (Apri file).", "warn");
        return;
      }
      const doc = await api.saveDocument(projectId, { name: fileStem(file.name), kind: "pir", content: text });
      reloadDocs();
      setDocId(doc.id);
      if (!report.valid) toast.push(`PIR caricato ma con errori: ${report.errors.join(" ")}`, "warn");
      else toast.push("PIR caricato e salvato nei documenti del progetto");
    } catch (e) {
      toast.error(e);
    }
  };

  const loadRestraints = (text: string) => {
    const { restraints: rows, errors } = parseRestraints(text);
    errors.slice(0, 3).forEach((e) => toast.push(e, "warn"));
    if (rows.length) {
      setRestraints(rows);
      toast.push(`${rows.length} vincoli caricati`);
    } else if (!errors.length) toast.push("Nessun vincolo nel file.", "warn");
  };

  const pirDocs = docs?.filter((d) => d.kind === "pir") ?? [];
  const modellerMissing = info && !info.modeller.available;
  const canSubmit = templates.length > 0 && templates.every((t) => t.chains.length > 0) && (mode === "auto" ? !!target.sequence.trim() : !!docId);

  return (
    <div className="page">
      <PageHead title="Modellazione" lead="Costruzione di modelli 3D con MODELLER: allineamento automatico (align2d/salign) oppure da un PIR rifinito, ottimizzazione con vincoli spaziali e selezione del modello migliore per DOPE." />
      {modellerMissing && <Alert tone="err">MODELLER non è disponibile su questo computer. Configura l'interprete Python con MODELLER nelle Impostazioni.</Alert>}
      <div className="split">
        <div className="stack">
          <Card title="Input">
            <div className="stack">
              <Segmented value={mode} onChange={setMode} options={[{ value: "auto", label: "Allineamento automatico" }, { value: "pir", label: "Da documento PIR" }]} />
              {mode === "auto" ? (
                <SequencePicker value={target} onChange={setTarget} />
              ) : (
                <div className="stack-sm">
                  <Field label="Documento PIR" hint="Crealo o modificalo nell'Editor PIR, oppure caricalo da file.">
                    <select className="input" value={docId} onChange={(e) => setDocId(e.target.value)}>
                      <option value="">— scegli —</option>
                      {pirDocs.map((d) => <option key={d.id} value={d.id}>{d.name}</option>)}
                    </select>
                  </Field>
                  <div className="row">
                    <TextFileButton accept=".pir,.ali,.txt" onText={loadPirFile} disabled={!projectId}>Carica un file PIR</TextFileButton>
                    <span className="tiny muted">viene salvato tra i documenti del progetto</span>
                  </div>
                </div>
              )}
              <div className="stack-sm">
                <span className="small text-2" style={{ fontWeight: 550 }}>Template {mode === "pir" && "(stessi del PIR, nello stesso ordine)"}</span>
                <TemplatePicker value={templates} onChange={setTemplates} />
              </div>
            </div>
          </Card>
          <Card title="Parametri">
            <div className="stack">
              <div className="grid grid-2">
                <Field label="Numero di modelli" hint="5–10 per scegliere il migliore"><input className="input" type="number" min={1} max={100} value={nModels} onChange={(e) => setNModels(Number(e.target.value))} /></Field>
                <Field label="Analisi di qualità">
                  <select className="input" value={quality} onChange={(e) => setQuality(e.target.value as "best" | "all" | "none")}>
                    <option value="best">Solo sul modello migliore</option>
                    <option value="all">Su tutti i modelli</option>
                    <option value="none">Nessuna</option>
                  </select>
                </Field>
              </div>
              {quality !== "none" && (
                <label className="check"><input type="checkbox" checked={extended} onChange={(e) => setExtended(e.target.checked)} /> Includi PROCHECK e ProSA II nell'analisi</label>
              )}
              <Field label="Raffinamento" hint={REFINEMENT.find((r) => r.value === refinement)?.note}>
                <Segmented value={refinement} onChange={setRefinement} options={REFINEMENT.map((r) => ({ value: r.value, label: r.label }))} />
              </Field>
              <button className="btn btn-ghost btn-sm" style={{ justifySelf: "start" }} onClick={() => setAdvanced(!advanced)}>
                <Icon name="chevron" size={13} /> Opzioni avanzate
              </button>
              {advanced && (
                <div className="stack card flat">
                  <label className="check"><input type="checkbox" checked={loopOn} onChange={(e) => setLoopOn(e.target.checked)} /> Raffinamento dei loop (DOPE-loop)</label>
                  {loopOn && (
                    <div className="grid grid-2">
                      <Field label="Modelli di loop per modello"><input className="input" type="number" min={1} max={50} value={loopModels} onChange={(e) => setLoopModels(Number(e.target.value))} /></Field>
                      <Field label="Regioni (facoltative)" hint="Es. 45:A-52:A, 80:A-86:A. Vuoto = gap dell'allineamento."><input className="input" value={loopRegions} onChange={(e) => setLoopRegions(e.target.value)} /></Field>
                    </div>
                  )}
                  <div className="stack-sm">
                    <div className="row"><b className="small">Vincoli aggiuntivi</b>
                      <div className="right row">
                        <TextFileButton accept=".txt,.csv,.tsv,.rsr" onText={loadRestraints}>Da file</TextFileButton>
                        {!!restraints.length && (
                          <button className="btn btn-sm" onClick={() => downloadText("vincoli_bioloom.txt", formatRestraints(restraints))}><Icon name="download" size={13} /> Esporta</button>
                        )}
                        <button className="btn btn-sm" onClick={() => setRestraints([...restraints, { type: "helix", start: "", end: "" }])}><Icon name="plus" size={13} /> Vincolo</button>
                      </div>
                    </div>
                    {restraints.map((r, i) => (
                      <div key={i} className="row">
                        <select className="input" style={{ width: 120 }} value={r.type} onChange={(e) => setRestraints(restraints.map((x, k) => (k === i ? { type: e.target.value as Restraint["type"] } : x)))}>
                          <option value="helix">α-elica</option>
                          <option value="strand">filamento β</option>
                          <option value="distance">distanza</option>
                        </select>
                        {r.type === "distance" ? (
                          <>
                            <input className="input" style={{ width: 100 }} placeholder="CA:10:A" value={r.atom1 ?? ""} onChange={(e) => setRestraints(restraints.map((x, k) => (k === i ? { ...x, atom1: e.target.value } : x)))} />
                            <input className="input" style={{ width: 100 }} placeholder="CA:50:A" value={r.atom2 ?? ""} onChange={(e) => setRestraints(restraints.map((x, k) => (k === i ? { ...x, atom2: e.target.value } : x)))} />
                            <input className="input" style={{ width: 70 }} type="number" placeholder="Å" value={r.mean ?? ""} onChange={(e) => setRestraints(restraints.map((x, k) => (k === i ? { ...x, mean: Number(e.target.value), stdev: x.stdev ?? 0.5 } : x)))} />
                          </>
                        ) : (
                          <>
                            <input className="input" style={{ width: 90 }} placeholder="10:A" value={r.start ?? ""} onChange={(e) => setRestraints(restraints.map((x, k) => (k === i ? { ...x, start: e.target.value } : x)))} />
                            <input className="input" style={{ width: 90 }} placeholder="20:A" value={r.end ?? ""} onChange={(e) => setRestraints(restraints.map((x, k) => (k === i ? { ...x, end: e.target.value } : x)))} />
                          </>
                        )}
                        <button className="btn btn-ghost btn-icon" onClick={() => setRestraints(restraints.filter((_, k) => k !== i))}><Icon name="x" size={14} /></button>
                      </div>
                    ))}
                    <span className="tiny muted">
                      Residui nella numerazione del modello (es. 10:A); atomi come NOME:residuo:catena. Da file: una riga per vincolo,
                      es. <span className="mono">helix 10:A 20:A</span>, <span className="mono">strand 30:A 35:A</span>, <span className="mono">distance CA:10:A CA:50:A 8.0 0.5</span>.
                    </span>
                  </div>
                  <Field label="Seme casuale MODELLER" hint="Tra −50000 e −2: cambia per ottenere modelli diversi."><input className="input" type="number" value={seed} onChange={(e) => setSeed(Number(e.target.value))} /></Field>
                </div>
              )}
              <button className="btn btn-primary" disabled={!canSubmit || !!modellerMissing} onClick={submit}><Icon name="play" size={14} /> Avvia modellazione</button>
              {templates.some((t) => !t.chains.length) && <span className="tiny" style={{ color: "var(--warn)" }}>Seleziona almeno una catena per ogni template.</span>}
            </div>
          </Card>
          <Card title="Raffinamento dei loop" sub="di un modello, un template o un file PDB/mmCIF">
            <div className="stack-sm">
              <StructurePicker label="Struttura" value={loopSource} onChange={setLoopSource} />
              <button className="btn btn-sm" style={{ alignSelf: "start" }} disabled={!loopSource || !!modellerMissing} onClick={() => setLoopOpen(true)}>
                <Icon name="wand" size={13} /> Scegli il loop da raffinare
              </button>
            </div>
          </Card>
          {loopOpen && loopSource && (
            <LoopDialog source={loopSource} label={loopSource.id ?? loopSource.file ?? "struttura"} onClose={() => setLoopOpen(false)} />
          )}
          <Card title="Modellazioni recenti">
            {!recent?.length ? <p className="small muted">Nessuna.</p> : (
              <div className="stack-sm">
                {recent.map((r) => (
                  <button key={r.id} className="btn btn-ghost" style={{ justifyContent: "flex-start", height: "auto", padding: "6px 10px", borderColor: r.id === jobId ? "var(--accent)" : undefined }} onClick={() => setParams({ job: r.id })}>
                    <span className="ellipsis" style={{ flex: 1, textAlign: "left" }}>{r.title}</span>
                    <span className="tiny muted">{relTime(r.created_at)}</span>
                  </button>
                ))}
              </div>
            )}
          </Card>
        </div>
        <div className="stack">
          {jobId ? <JobPanel summary={summary} log={log} onCancel={cancel} /> : <Card><Empty icon="cube" title="Configura bersaglio e template, poi avvia la modellazione" /></Card>}
          {job?.status === "completed" && job.result && <Results key={job.id} jobId={job.id} result={job.result} kind={job.kind} />}
        </div>
      </div>
    </div>
  );
}
