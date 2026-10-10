import { useEffect, useRef, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { api } from "../api/client";
import type { Align2dResult, DocumentItem, TemplateRef } from "../api/types";
import { TextFileButton } from "../components/FileLoad";
import { Icon } from "../components/Icon";
import { JobPanel } from "../components/JobPanel";
import { SequencePicker, TemplatePicker } from "../components/Pickers";
import { PirWorkspace, type PirSource, type PirWorkspaceState } from "../components/PirWorkspace";
import { Card, Confirm, Empty, Modal, PageHead } from "../components/ui";
import { FormSection } from "../components/WorkflowParts";
import { useProject, useToast } from "../lib/context";
import { fileStem } from "../lib/files";
import { downloadText, fmtBytes, relTime } from "../lib/format";
import { useAsync, useJob } from "../lib/hooks";

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
    // template e bersaglio del job, non quelli del modulo: si possono cambiare mentre il job è in corso
    const used = (job.params?.target as { name?: string } | null | undefined)?.name;
    onDone(job.result.pir, job.result.templates.map((t) => ({ id: t.id, chains: t.chains })), used || target.name || "target");
    // eslint-disable-next-line react-hooks/exhaustive-deps -- una volta, al completamento del job
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

const DOCS_PREF = "bioloom.pir.docs";
function readDocsPref(): boolean {
  try {
    return localStorage.getItem(DOCS_PREF) !== "0";
  } catch {
    return true;
  }
}

export default function PirEditor() {
  const { projectId } = useProject();
  const toast = useToast();
  const navigate = useNavigate();
  const [params, setParams] = useSearchParams();
  const docId = params.get("doc");
  const { data: docs, reload } = useAsync<DocumentItem[]>(() => (projectId ? api.documents(projectId) : Promise.resolve([])), [projectId]);
  const [name, setName] = useState("");
  const [nameDirty, setNameDirty] = useState(false);
  const [generate, setGenerate] = useState(false);
  const [toDelete, setToDelete] = useState(false);
  // elenco dei documenti a sinistra (nascosto: la griglia prende tutta la larghezza)
  const [showDocs, setShowDocs] = useState(readDocsPref);
  const toggleDocs = () => {
    setShowDocs((v) => {
      try {
        localStorage.setItem(DOCS_PREF, v ? "0" : "1");
      } catch {
        // preferenza non salvata
      }
      return !v;
    });
  };
  // allineamento aperto nel banco di lavoro e suo stato (testo e metadati da salvare)
  const [source, setSource] = useState<PirSource>({ key: 0, text: "", meta: {} });
  const [ws, setWs] = useState<PirWorkspaceState>({ content: "", meta: {}, dirty: false, valid: false });
  const [savedKey, setSavedKey] = useState(0);
  // documento appena salvato: il cambio di indirizzo che segue non lo ricarica (cronologia e sblocco restano)
  const justSaved = useRef<string | null>(null);

  const open = (text: string, meta: Record<string, unknown>, dirty: boolean) => {
    setSource({ key: Date.now(), text, meta, dirty });
    setNameDirty(false);
  };

  useEffect(() => {
    if (!docId || !projectId) return;
    if (docId === justSaved.current) {
      justSaved.current = null;
      return;
    }
    api.document(projectId, docId).then((d) => {
      setName(d.name);
      open(d.content ?? "", d.meta ?? {}, false);
    }).catch(toast.error);
  }, [docId, projectId, toast]);

  const fromJob = params.get("fromjob");
  useEffect(() => {
    if (!fromJob) return;
    api.job<Align2dResult>(fromJob).then((j) => {
      if (!j.result) return;
      for (const w of j.result.warnings ?? []) toast.push(w, "warn");
      setName(`Allineamento MODELLER (job ${fromJob.slice(0, 6)})`);
      open(j.result.pir, { templates: j.result.templates.map((t) => ({ id: t.id, chains: t.chains })) }, true);
    }).catch(toast.error);
  }, [fromJob, toast]);

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
    open(pir, {}, true);
  };

  const save = async (asNew = false) => {
    if (!projectId) return;
    try {
      const d = await api.saveDocument(projectId, { name: name || "Allineamento PIR", kind: "pir", content: ws.content, meta: ws.meta }, asNew ? undefined : docId ?? undefined);
      setSavedKey((k) => k + 1);
      setNameDirty(false);
      reload();
      if (d.id !== docId) justSaved.current = d.id;
      setParams({ doc: d.id });
      toast.push("Documento salvato");
      return d;
    } catch (e) {
      toast.error(e);
    }
  };

  const content = ws.content;
  const dirty = ws.dirty || nameDirty;
  return (
    <div className="page">
      <PageHead
        title="Editor PIR"
        lead="Allineamento bersaglio–template nel formato di MODELLER. Correggi a mano i gap nelle regioni critiche (loop, siti attivi) prima di modellare."
        actions={
          <>
            <button className="btn" onClick={() => setGenerate(true)}><Icon name="wand" /> Genera con MODELLER</button>
            <TextFileButton accept=".pir,.ali,.txt,.fasta,.fa,.aln,.sto,.msf" onText={openFile}>Apri file</TextFileButton>
            <button className="btn" onClick={() => { setParams({}); setName(""); open("", {}, false); }}><Icon name="plus" /> Nuovo</button>
          </>
        }
      />
      <div className={showDocs ? "split" : "pire-docs-collapsed"}>
        {!showDocs && (
          <aside className="pirw-rail" aria-label="Documenti del progetto (ridotti)">
            <button type="button" className="btn btn-sm" onClick={toggleDocs} aria-expanded={false} title="Mostra i documenti del progetto">
              <Icon name="chevron" size={14} />
              <span className="pirw-rail-label">Documenti</span>
              {!!docs?.length && <span className="pirw-count">{docs.length}</span>}
            </button>
          </aside>
        )}
        {showDocs && <Card title="Documenti del progetto" icon="folder" sub={docs?.length ? String(docs.length) : undefined}
          right={<button type="button" className="btn btn-sm btn-ghost" onClick={toggleDocs} aria-expanded title="Nasconde i documenti: l'editor prende tutta la larghezza"><Icon name="chevron" size={14} className="pirw-flip" /> Riduci</button>}>
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
        </Card>}
        <div className="stack">
          <PirWorkspace
            source={source}
            savedKey={savedKey}
            onState={setWs}
            title={<input className="input" style={{ minWidth: 280, fontWeight: 600 }} placeholder="Nome del documento" aria-label="Nome del documento" value={name} onChange={(e) => { setName(e.target.value); setNameDirty(true); }} />}
            right={
              <>
                {nameDirty && !ws.dirty && <span className="tiny text-2">nome cambiato</span>}
                <button className="btn btn-sm" disabled={!content} onClick={() => downloadText(`${name || "alignment"}.ali`, content)}><Icon name="download" size={14} /> .ali</button>
                <button className="btn btn-sm" disabled={!content || !projectId} onClick={() => save(false)}><Icon name="save" size={14} /> Salva</button>
                {docId && <button className="btn btn-sm btn-danger" onClick={() => setToDelete(true)} title="Elimina il documento" aria-label="Elimina il documento"><Icon name="trash" size={14} /></button>}
                <button
                  className="btn btn-sm btn-primary"
                  disabled={!ws.valid}
                  onClick={async () => {
                    const d = dirty || !docId ? await save(false) : { id: docId };
                    if (d) navigate(`/modeling?doc=${d.id}`);
                  }}
                >
                  <Icon name="cube" size={14} /> Modella
                </button>
              </>
            }
            empty={
              <Card>
                <Empty icon="align" title="Nessun allineamento aperto">Genera un allineamento con MODELLER, apri un file (PIR, FASTA allineato, Clustal…) o scegli un documento del progetto.</Empty>
              </Card>
            }
          />
        </div>
      </div>
      {generate && (
        <GenerateDialog
          onClose={() => setGenerate(false)}
          onDone={(pir, templates, target) => {
            setGenerate(false);
            setParams({});
            setName(`Allineamento ${target} · ${templates.map((t) => t.id).join(", ")}`);
            open(pir, { templates }, true);
            toast.push("Allineamento generato: controllalo e salvalo");
          }}
        />
      )}
      {toDelete && docId && projectId && (
        <Confirm text="Eliminare questo documento?" confirmLabel="Elimina" onCancel={() => setToDelete(false)} onConfirm={() => api.deleteDocument(projectId, docId).then(() => { setToDelete(false); setParams({}); open("", {}, false); setName(""); reload(); }).catch(toast.error)} />
      )}
    </div>
  );
}
