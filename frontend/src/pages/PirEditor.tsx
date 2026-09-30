import { useEffect, useMemo, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { api } from "../api/client";
import type { Align2dResult, DocumentItem, PirReport, TemplateRef } from "../api/types";
import { AlignmentView } from "../components/AlignmentView";
import { TextFileButton } from "../components/FileLoad";
import { Icon } from "../components/Icon";
import { JobPanel } from "../components/JobPanel";
import { SequencePicker, TemplatePicker } from "../components/Pickers";
import { Alert, BarCell, Card, Confirm, Empty, Modal, PageHead } from "../components/ui";
import { pirReference } from "../lib/alignConfidence";
import { useProject, useToast } from "../lib/context";
import { fileStem } from "../lib/files";
import { downloadText, relTime } from "../lib/format";
import { useAsync, useJob } from "../lib/hooks";
import { parsePir, pirIdentities } from "../lib/pir";

function GenerateDialog({ onClose, onDone }: { onClose: () => void; onDone: (pir: string, templates: TemplateRef[], name: string) => void }) {
  const { projectId } = useProject();
  const toast = useToast();
  const [target, setTarget] = useState({ name: "", sequence: "" });
  const [templates, setTemplates] = useState<TemplateRef[]>([]);
  const [jobId, setJobId] = useState<string | null>(null);
  const { job, summary, log, cancel } = useJob<Align2dResult>(jobId, { withLog: true });
  useEffect(() => {
    if (job?.status === "completed" && job.result) onDone(job.result.pir, templates, target.name || "target");
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
      wide
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
        <div className="stack-sm">
          <span className="small text-2" style={{ fontWeight: 550 }}>Template</span>
          <TemplatePicker value={templates} onChange={setTemplates} ligands={false} />
          <p className="tiny muted">Con più template MODELLER li sovrappone strutturalmente (salign) e allinea il bersaglio sul profilo comune.</p>
        </div>
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

  useEffect(() => {
    if (!docId || !projectId) return;
    api.document(projectId, docId).then((d) => {
      setName(d.name);
      setContent(d.content ?? "");
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
      setName(`Allineamento MODELLER (job ${from.slice(0, 6)})`);
      setContent(j.result.pir);
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
    setContent(pir);
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
            <button className="btn" onClick={() => { setParams({}); setName(""); setContent(""); setMeta({}); }}><Icon name="plus" /> Nuovo</button>
          </>
        }
      />
      <div className="split">
        <Card title="Documenti del progetto">
          {!docs?.length ? <Empty icon="file" title="Nessun documento">Genera un allineamento o salvane uno dalla pagina Allineamenti.</Empty> : (
            <div className="table-wrap">
              <table className="table">
                <tbody>
                  {docs.map((d) => (
                    <tr key={d.id} className={`clickable ${d.id === docId ? "selected" : ""}`} onClick={() => setParams({ doc: d.id })}>
                      <td><b>{d.name}</b><div className="tiny muted">{d.kind.toUpperCase()} · {relTime(d.updated_at)}</div></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Card>
        <div className="stack">
          <Card
            title={<input className="input" style={{ minWidth: 280, fontWeight: 600 }} placeholder="Nome del documento" value={name} onChange={(e) => { setName(e.target.value); setDirty(true); }} />}
            right={
              <>
                {dirty && <span className="badge badge-warn">modifiche non salvate</span>}
                <button className="btn btn-sm" disabled={!content} onClick={() => downloadText(`${name || "alignment"}.ali`, content)}><Icon name="download" size={14} /> .ali</button>
                <button className="btn btn-sm" disabled={!content || !projectId} onClick={() => save(false)}><Icon name="save" size={14} /> Salva</button>
                {docId && <button className="btn btn-sm btn-danger" onClick={() => setToDelete(true)}><Icon name="trash" size={14} /></button>}
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
            <textarea
              className="input"
              rows={16}
              spellCheck={false}
              value={content}
              onChange={(e) => { setContent(e.target.value); setDirty(true); }}
              placeholder={">P1;target\nsequence:target:::::::0.00: 0.00\nMKTAYIAKQR-QISFVKSHF*\n\n>P1;1ABC_A\nstructureX:1ABC_A:1:A:120:A::::\nMKSAYIAKQRAQISFVKSHF*"}
              style={{ whiteSpace: "pre", overflowX: "auto" }}
            />
            {templatesMeta.length > 0 && (
              <p className="tiny muted" style={{ marginTop: 6 }}>Template associati: {templatesMeta.map((t) => `${t.id}:${t.chains.join("")}`).join(", ")}</p>
            )}
          </Card>
          {report && (
            <Card title="Verifica" right={report.valid ? <span className="badge badge-ok">valido per MODELLER</span> : <span className="badge badge-err">da correggere</span>}>
              <div className="stack">
                {report.errors.map((e, i) => <Alert key={i} tone="err">{e}</Alert>)}
                {report.warnings.map((w, i) => <Alert key={`w${i}`} tone="warn">{w}</Alert>)}
                {identities.length > 0 && (
                  <div className="table-wrap">
                    <table className="table">
                      <thead><tr><th>Template</th><th>Identità</th><th>Copertura del bersaglio</th></tr></thead>
                      <tbody>
                        {identities.map((x) => (
                          <tr key={x.code}><td><b>{x.code}</b></td><td><BarCell value={x.identity} /></td><td><BarCell value={x.coverage} /></td></tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}
              </div>
            </Card>
          )}
          {entries.length > 1 && entries.every((e) => e.sequence.length === entries[0].sequence.length) && (
            <Card title="Anteprima">
              <AlignmentView ids={entries.map((e) => e.code)} aligned={entries.map((e) => e.sequence)} reference={pirReference(entries.map((e) => e.kind))} />
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
            setContent(pir);
            setMeta({ templates });
            setDirty(true);
            toast.push("Allineamento generato: controllalo e salvalo");
          }}
        />
      )}
      {toDelete && docId && projectId && (
        <Confirm text="Eliminare questo documento?" confirmLabel="Elimina" onCancel={() => setToDelete(false)} onConfirm={() => api.deleteDocument(projectId, docId).then(() => { setToDelete(false); setParams({}); setContent(""); setName(""); reload(); }).catch(toast.error)} />
      )}
    </div>
  );
}
