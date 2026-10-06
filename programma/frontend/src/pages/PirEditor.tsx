import { useEffect, useMemo, useRef, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { api } from "../api/client";
import type { Align2dResult, DocumentItem, PirReport, TemplateRef } from "../api/types";
import { AlignmentView } from "../components/AlignmentView";
import { TextFileButton } from "../components/FileLoad";
import { Icon } from "../components/Icon";
import { JobPanel } from "../components/JobPanel";
import { PirGrid } from "../components/PirGrid";
import { SequencePicker, TemplatePicker } from "../components/Pickers";
import { Alert, Badge, BarCell, Card, Confirm, Disclosure, Empty, Modal, PageHead, Tabs } from "../components/ui";
import { FormSection, InfoChips } from "../components/WorkflowParts";
import { pirReference } from "../lib/alignConfidence";
import { useProject, useToast } from "../lib/context";
import { fileStem } from "../lib/files";
import { downloadText, fmtBytes, relTime } from "../lib/format";
import { useAsync, useJob } from "../lib/hooks";
import { parsePir, pirIdentities } from "../lib/pir";
import { emptyHistory, record, redo, serializePir, undo, type History } from "../lib/pirEdit";
import { templateLabel } from "../lib/workflowView";

function GenerateDialog({ onClose, onDone }: { onClose: () => void; onDone: (pir: string, templates: TemplateRef[], name: string) => void }) {
  const { projectId } = useProject();
  const toast = useToast();
  const [target, setTarget] = useState({ name: "", sequence: "" });
  const [templates, setTemplates] = useState<TemplateRef[]>([]);
  const [jobId, setJobId] = useState<string | null>(null);
  const { job, summary, log, cancel } = useJob<Align2dResult>(jobId, { withLog: true });
  useEffect(() => {
    if (job?.status !== "completed" || !job.result) return;
    for (const w of job.result.warnings ?? []) toast.push(w, "warn");
    onDone(job.result.pir, templates, target.name || "target");
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [job?.status]);
  const submit = async () => {
    try {
      const j = await api.align2d({ project_id: projectId ?? undefined, target: { name: target.name || "target", sequence: target.sequence }, templates });
      setJobId(j.id);
    } catch (e) {
      toast.error(e);
    }
  };
  return (
    <Modal
      title="Genera allineamento con MODELLER"
      size="wide"
      onClose={onClose}
      footer={
        <>
          <button className="btn" onClick={onClose}>Chiudi</button>
          <button className="btn btn-primary" disabled={!target.sequence || !templates.length || !!summary && summary.status === "running"} onClick={submit}>
            <Icon name="play" size={14} /> Allinea
          </button>
        </>
      }
    >
      <div className="grid grid-2">
        <SequencePicker value={target} onChange={setTarget} />
        <FormSection title="Template" hint={templates.length ? `${templates.length} scelti` : undefined}>
          <TemplatePicker value={templates} onChange={setTemplates} ligands={false} />
          <p className="small text-2">Con più template MODELLER li sovrappone strutturalmente (salign) e allinea il bersaglio sul profilo comune.</p>
        </FormSection>
      </div>
      {jobId && <div style={{ marginTop: 14 }}><JobPanel summary={summary} log={log} onCancel={cancel} compact /></div>}
    </Modal>
  );
}

export default function PirEditor() {
  const { projectId } = useProject();
  const toast = useToast();
  const navigate = useNavigate();
  const [params, setParams] = useSearchParams();
  const docId = params.get("doc");
  const { data: docs, reload } = useAsync<DocumentItem[]>(() => (projectId ? api.documents(projectId) : Promise.resolve([])), [projectId]);
  const [name, setName] = useState("");
  const [content, setContent] = useState("");
  const [meta, setMeta] = useState<Record<string, unknown>>({});
  const [dirty, setDirty] = useState(false);
  const [report, setReport] = useState<PirReport | null>(null);
  const [generate, setGenerate] = useState(false);
  const [toDelete, setToDelete] = useState(false);
  const [tab, setTab] = useState<"grid" | "text">("grid");
  const [history, setHistory] = useState<History>(emptyHistory);
  // le battute di fila nel testo PIR formano un solo passo della cronologia
  const lastTyping = useRef(0);

  /** Contenuto caricato da fuori (documento, file, job): nuova cronologia. */
  const load = (text: string) => {
    setContent(text);
    setHistory(emptyHistory());
  };
  /** Modifica dell'utente: lo stato precedente va nella cronologia. */
  const change = (text: string, typing = false) => {
    const now = Date.now();
    if (!typing || now - lastTyping.current > 1000) setHistory((h) => record(h, content));
    lastTyping.current = typing ? now : 0;
    setContent(text);
    setDirty(true);
  };
  const step = (dir: "undo" | "redo") => {
    const r = dir === "undo" ? undo(history, content) : redo(history, content);
    if (!r) return;
    setHistory(r.history);
    setContent(r.value);
    setDirty(true);
    lastTyping.current = 0;
  };

  useEffect(() => {
    if (!docId || !projectId) return;
    api.document(projectId, docId).then((d) => {
      setName(d.name);
      load(d.content ?? "");
      setMeta(d.meta ?? {});
      setDirty(false);
    }).catch(toast.error);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [docId, projectId]);

  useEffect(() => {
    const from = params.get("fromjob");
    if (!from) return;
    api.job<Align2dResult>(from).then((j) => {
      if (!j.result) return;
      for (const w of j.result.warnings ?? []) toast.push(w, "warn");
      setName(`Allineamento MODELLER (job ${from.slice(0, 6)})`);
      load(j.result.pir);
      setMeta({ templates: j.result.templates.map((t) => ({ id: t.id, chains: t.chains })) });
      setDirty(true);
    }).catch(toast.error);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [params.get("fromjob")]);

  useEffect(() => {
    if (!content.trim()) return setReport(null);
    const t = setTimeout(() => api.validatePir(content).then(setReport).catch(() => undefined), 400);
    return () => clearTimeout(t);
  }, [content]);

  const entries = useMemo(() => parsePir(content), [content]);
  const identities = useMemo(() => pirIdentities(entries), [entries]);
  const templatesMeta = (meta.templates as TemplateRef[] | undefined) ?? [];

  /** Apre un file PIR così com'è; un allineamento in altro formato (FASTA, Clustal...) viene convertito in PIR. */
  const openFile = async (text: string, file: File) => {
    let pir = text;
    if (!/^>P1;/m.test(text)) {
      try {
        const parsed = await api.parseSequenceFile(file);
        if (!parsed.aligned || parsed.records.length < 2) {
          toast.push("Il file non contiene un allineamento: servono almeno due sequenze allineate (PIR, FASTA allineato, Clustal…).", "warn");
          return;
        }
        pir = await api.convertAlignment(parsed.records.map((r) => r.name), parsed.records.map((r) => r.aligned ?? r.sequence), "pir");
        toast.push(`Allineamento ${parsed.format_label} convertito in PIR: indica quali voci sono template (structureX) prima di modellare.`, "info");
      } catch (e) {
        toast.error(e);
        return;
      }
    }
    setParams({});
    setName(fileStem(file.name));
    load(pir);
    setMeta({});
    setDirty(true);
  };

  const save = async (asNew = false) => {
    if (!projectId) return;
    try {
      const d = await api.saveDocument(projectId, { name: name || "Allineamento PIR", kind: "pir", content, meta }, asNew ? undefined : docId ?? undefined);
      setDirty(false);
      reload();
      setParams({ doc: d.id });
      toast.push("Documento salvato");
      return d;
    } catch (e) {
      toast.error(e);
    }
  };

  return (
    <div className="page">
      <PageHead
        title="Editor PIR"
        lead="Allineamento bersaglio–template nel formato di MODELLER. Correggi a mano i gap nelle regioni critiche (loop, siti attivi) prima di modellare."
        actions={
          <>
            <button className="btn" onClick={() => setGenerate(true)}><Icon name="wand" /> Genera con MODELLER</button>
            <TextFileButton accept=".pir,.ali,.txt,.fasta,.fa,.aln,.sto,.msf" onText={openFile}>Apri file</TextFileButton>
            <button className="btn" onClick={() => { setParams({}); setName(""); load(""); setMeta({}); }}><Icon name="plus" /> Nuovo</button>
          </>
        }
      />
      <div className="split">
        <Card title="Documenti del progetto" icon="folder" sub={docs?.length ? String(docs.length) : undefined}>
          {!docs?.length ? <Empty icon="file" title="Nessun documento">Genera un allineamento o salvane uno dalla pagina Allineamenti.</Empty> : (
            <div className="table-wrap" style={{ maxHeight: 560, overflowY: "auto" }}>
              <table className="table">
                <tbody>
                  {docs.map((d) => (
                    <tr key={d.id} className={`clickable ${d.id === docId ? "selected" : ""}`} onClick={() => setParams({ doc: d.id })}>
                      <td>
                        <b>{d.name}</b>
                        <div className="tiny text-2">{d.kind.toUpperCase()} · {relTime(d.updated_at)}{d.size ? ` · ${fmtBytes(d.size)}` : ""}</div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Card>
        <div className="stack">
          <Card
            icon="file"
            title={<input className="input" style={{ minWidth: 280, fontWeight: 600 }} placeholder="Nome del documento" aria-label="Nome del documento" value={name} onChange={(e) => { setName(e.target.value); setDirty(true); }} />}
            right={
              <>
                {dirty && <Badge tone="warn">modifiche non salvate</Badge>}
                <button className="btn btn-sm" disabled={!content} onClick={() => downloadText(`${name || "alignment"}.ali`, content)}><Icon name="download" size={14} /> .ali</button>
                <button className="btn btn-sm" disabled={!content || !projectId} onClick={() => save(false)}><Icon name="save" size={14} /> Salva</button>
                {docId && <button className="btn btn-sm btn-danger" onClick={() => setToDelete(true)} title="Elimina il documento" aria-label="Elimina il documento"><Icon name="trash" size={14} /></button>}
                <button
                  className="btn btn-sm btn-primary"
                  disabled={!report?.valid}
                  onClick={async () => {
                    const d = dirty || !docId ? await save(false) : { id: docId };
                    if (d) navigate(`/modeling?doc=${d.id}`);
                  }}
                >
                  <Icon name="cube" size={14} /> Modella
                </button>
              </>
            }
          >
            <div className="stack-sm">
              {entries.length > 0 && (
                <Tabs value={tab} onChange={setTab} tabs={[{ value: "grid", label: "Griglia" }, { value: "text", label: "Testo PIR" }]} />
              )}
              {entries.length > 0 && tab === "grid" ? (
                <PirGrid
                  ids={entries.map((e) => e.code)}
                  kinds={entries.map((e) => e.kind)}
                  rows={entries.map((e) => e.sequence)}
                  onEdit={(rows) => change(serializePir(entries, rows))}
                  onUndo={() => step("undo")}
                  onRedo={() => step("redo")}
                  canUndo={history.past.length > 0}
                  canRedo={history.future.length > 0}
                />
              ) : (
                <textarea
                  className="input"
                  rows={16}
                  spellCheck={false}
                  aria-label="Allineamento in formato PIR"
                  value={content}
                  onChange={(e) => change(e.target.value, true)}
                  placeholder={">P1;target\nsequence:target:::::::0.00: 0.00\nMKTAYIAKQR-QISFVKSHF*\n\n>P1;1ABC_A\nstructureX:1ABC_A:1:A:120:A::::\nMKSAYIAKQRAQISFVKSHF*"}
                  style={{ whiteSpace: "pre", overflowX: "auto" }}
                />
              )}
              <InfoChips items={[
                { label: "Voci", value: entries.length || null },
                { label: "Bersaglio", value: entries.find((e) => e.kind === "sequence")?.code },
                { label: "Strutture", value: entries.filter((e) => e.kind.startsWith("structure")).length || null },
                { label: "Colonne", value: entries.length ? entries[0].sequence.length : null },
                { label: "Template associati", value: templatesMeta.length ? templatesMeta.map(templateLabel).join(", ") : null },
              ]} />
            </div>
          </Card>
          {report && (
            <Card title="Verifica" icon="shield" sub={`${report.errors.length} errori · ${report.warnings.length} avvisi`}
              right={report.valid ? <Badge tone="ok">valido per MODELLER</Badge> : <Badge tone="err">da correggere</Badge>}>
              <div className="stack">
                {report.errors.map((e, i) => <Alert key={i} tone="err">{e}</Alert>)}
                {report.warnings.map((w, i) => <Alert key={`w${i}`} tone="warn">{w}</Alert>)}
                {identities.length > 0 && (
                  <div className="table-wrap">
                    <table className="table">
                      <thead><tr><th>Template</th><th>Identità</th><th>Copertura del bersaglio</th></tr></thead>
                      <tbody>
                        {identities.map((x, i) => (
                          <tr key={`${i}-${x.code}`}><td><b>{x.code}</b></td><td><BarCell value={x.identity} /></td><td><BarCell value={x.coverage} /></td></tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}
                {report.entries.length > 0 && (
                  <Disclosure title="Voci dell'allineamento" extra={`${report.entries.length} voci`}>
                    <div className="table-wrap">
                      <table className="table">
                        <thead><tr><th>Codice</th><th>Tipo</th><th className="num">Colonne</th><th className="num">Residui</th><th className="num">Catene</th></tr></thead>
                        <tbody>
                          {report.entries.map((e, i) => (
                            <tr key={`${i}-${e.code}`}>
                              <td><b className="mono">{e.code}</b></td>
                              <td>{e.kind}</td>
                              <td className="num">{e.length}</td>
                              <td className="num">{e.residues}</td>
                              <td className="num">{e.chains}</td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                    <p style={{ marginTop: 8 }}>Identità e copertura sono calcolate sulle colonne in cui bersaglio e template hanno entrambi un residuo; la validazione autorevole è quella del server.</p>
                  </Disclosure>
                )}
              </div>
            </Card>
          )}
          {entries.length > 1 && entries.every((e) => e.sequence.length === entries[0].sequence.length) && (
            <Card title="Anteprima" icon="align" sub={`${entries.length} sequenze · ${entries[0].sequence.length} colonne`} collapsible defaultOpen={false} storageKey="pir.preview">
              <AlignmentView ids={entries.map((e) => e.code)} aligned={entries.map((e) => e.sequence)} reference={pirReference(entries.map((e) => e.kind))} />
            </Card>
          )}
          {!content.trim() && (
            <Card>
              <Empty icon="align" title="Nessun allineamento aperto">Genera un allineamento con MODELLER, apri un file (PIR, FASTA allineato, Clustal…) o scegli un documento del progetto.</Empty>
            </Card>
          )}
        </div>
      </div>
      {generate && (
        <GenerateDialog
          onClose={() => setGenerate(false)}
          onDone={(pir, templates, target) => {
            setGenerate(false);
            setParams({});
            setName(`Allineamento ${target} · ${templates.map((t) => t.id).join(", ")}`);
            load(pir);
            setMeta({ templates });
            setDirty(true);
            toast.push("Allineamento generato: controllalo e salvalo");
          }}
        />
      )}
      {toDelete && docId && projectId && (
        <Confirm text="Eliminare questo documento?" confirmLabel="Elimina" onCancel={() => setToDelete(false)} onConfirm={() => api.deleteDocument(projectId, docId).then(() => { setToDelete(false); setParams({}); load(""); setName(""); reload(); }).catch(toast.error)} />
      )}
    </div>
  );
}
