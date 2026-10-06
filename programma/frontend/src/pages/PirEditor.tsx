import { useEffect, useMemo, useRef, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { api } from "../api/client";
import type { Align2dResult, DocumentItem, PirReport, PirSecondary, PirSecondaryEntry, TemplateRef } from "../api/types";
import { AlignmentView } from "../components/AlignmentView";
import { TextFileButton } from "../components/FileLoad";
import { Icon } from "../components/Icon";
import { JobPanel } from "../components/JobPanel";
import { PirGrid, type GridFocus, type GridSecondary } from "../components/PirGrid";
import { PirHeaderDialog } from "../components/PirHeaderDialog";
import { PirMutations } from "../components/PirMutations";
import { AnnotationDialog, AnnotationsCard } from "../components/PirAnnotations";
import { PirStructureView, type StructureFocus } from "../components/PirStructureView";
import { PirSecondaryCard } from "../components/PirSecondaryCard";
import { SequencePicker, TemplatePicker } from "../components/Pickers";
import { Alert, Badge, BarCell, Card, Confirm, Disclosure, Empty, Modal, PageHead, Tabs } from "../components/ui";
import { FormSection, InfoChips } from "../components/WorkflowParts";
import { pirReference } from "../lib/alignConfidence";
import { useProject, useToast } from "../lib/context";
import { fileStem } from "../lib/files";
import { downloadText, fmtBytes, relTime } from "../lib/format";
import { useAsync, useJob } from "../lib/hooks";
import { parsePir, pirIdentities } from "../lib/pir";
import { formatHeader, parseHeader, setEntryHeader } from "../lib/pirHeader";
import { alignmentDiff, diffCells, ranges } from "../lib/pirDiff";
import { annotationColumns, readAnnotations, residuesInColumns, type PirAnnotation } from "../lib/pirAnnotations";
import { emptyHistory, record, redo, rewritePir, squareUp, trimUncoveredEnds, undo, type History } from "../lib/pirEdit";
import { columnOfResidue, originalSequences, pirMutations, residuesOf, revertMutations, type PirMutation } from "../lib/pirMutations";
import { chainBreakIssues, conflictIssues, mismatchIssues, uncoveredIssues } from "../lib/pirIssues";
import { columnCodes, columnStates, gapConflicts, secondaryKey } from "../lib/pirSecondary";
import { templateLabel } from "../lib/workflowView";

/** Chiave dei metadati del documento con le sequenze originali (riferimento delle sostituzioni). */
const ORIGINAL_KEY = "original_sequences";

/** Preferenza "struttura secondaria nella griglia" (solo in questo browser). */
const SS_PREF = "bioloom.pir.secondary";
function readSsPref(): boolean {
  try {
    return localStorage.getItem(SS_PREF) !== "0";
  } catch {
    return true;
  }
}

function savedOriginal(meta: Record<string, unknown> | undefined): Record<string, string> | null {
  const v = meta?.[ORIGINAL_KEY];
  if (!v || typeof v !== "object" || Array.isArray(v)) return null;
  return Object.values(v).every((s) => typeof s === "string") ? (v as Record<string, string>) : null;
}

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
  // sequenze originali (senza gap) per codice: il riferimento delle sostituzioni
  const [original, setOriginal] = useState<Record<string, string>>({});
  const [templatesEditable, setTemplatesEditable] = useState(false);
  const [confirmUnlock, setConfirmUnlock] = useState(false);
  const [gridFocus, setGridFocus] = useState<GridFocus | null>(null);
  // voce di cui si modifica la seconda riga nel modulo
  const [headerRow, setHeaderRow] = useState<number | null>(null);
  // testo dell'ultima versione caricata o salvata, per il confronto; casella "mostra nella griglia"
  const [savedContent, setSavedContent] = useState("");
  const [showChanges, setShowChanges] = useState(false);
  // residui del bersaglio della nuova annotazione (finestra aperta)
  const [annotating, setAnnotating] = useState<{ from: number; to: number } | null>(null);
  // vista 3D collegata: cella del cursore della griglia, vista aperta o no
  const [cursorCell, setCursorCell] = useState({ r: 0, c: 0 });
  const [view3d, setView3d] = useState(false);
  // documento appena salvato: il cambio di indirizzo che segue non lo ricarica (cronologia e sblocco restano)
  const justSaved = useRef<string | null>(null);
  // struttura secondaria: risposta del server con la chiave della richiesta (residui e template, non i gap)
  const [ssOn, setSsOn] = useState(readSsPref);
  const [ssData, setSsData] = useState<{ key: string; data: PirSecondary } | null>(null);
  const [ssLoading, setSsLoading] = useState(false);
  const [ssError, setSsError] = useState<{ key: string; message: string } | null>(null);

  /**
   * Contenuto caricato da fuori (documento, file, job): nuova cronologia, template di nuovo bloccati. Il riferimento delle
   * sostituzioni è quello salvato con il documento, se c'è, altrimenti le sequenze appena caricate.
   */
  const load = (text: string, savedMeta?: Record<string, unknown>) => {
    setContent(text);
    setSavedContent(text);
    setHistory(emptyHistory());
    setTemplatesEditable(false);
    setOriginal(savedOriginal(savedMeta) ?? originalSequences(parsePir(text)));
  };
  /** Modifica dell'utente: lo stato precedente va nella cronologia. */
  const change = (text: string, typing = false) => {
    if (text === content) return;
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
    if (docId === justSaved.current) {
      justSaved.current = null;
      return;
    }
    api.document(projectId, docId).then((d) => {
      setName(d.name);
      load(d.content ?? "", d.meta);
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
  const mutationReport = useMemo(() => pirMutations(original, entries), [original, entries]);
  const mutated = useMemo(() => new Set(mutationReport.mutations.map((m) => `${m.row}:${m.column}`)), [mutationReport]);

  // struttura secondaria: si richiede solo quando cambiano residui, voci o template (spostare i gap non la cambia)
  const ssKey = useMemo(() => secondaryKey(entries, templatesMeta), [entries, templatesMeta]);
  useEffect(() => {
    if (!ssOn || !entries.length || ssData?.key === ssKey) return;
    const text = content;
    const key = ssKey;
    // una risposta arrivata dopo che residui o documento sono cambiati non sovrascrive quella giusta
    let cancelled = false;
    const t = setTimeout(() => {
      setSsLoading(true);
      api.pirSecondary(text, templatesMeta)
        .then((data) => {
          if (cancelled) return;
          setSsData({ key, data });
          setSsError(null);
        })
        .catch((e) => !cancelled && setSsError({ key, message: e instanceof Error ? e.message : String(e) }))
        .finally(() => !cancelled && setSsLoading(false));
    }, 500);
    return () => {
      cancelled = true;
      clearTimeout(t);
      setSsLoading(false);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ssKey, ssOn]);
  /**
   * Voce della risposta per ogni riga: per posizione se il codice coincide, altrimenti per codice (codici ripetuti: solo
   * per posizione). Finché arriva la risposta nuova si tiene quella precedente solo per le voci con lo stesso numero di
   * residui: altrimenti gli stati finirebbero sui residui sbagliati.
   */
  const ssEntries = useMemo<(PirSecondaryEntry | null)[]>(() => {
    const list = ssData?.data.entries ?? [];
    const repeated = new Set(entries.map((e) => e.code).filter((c, i, all) => all.indexOf(c) !== i));
    return entries.map((e, i) => {
      const x = list[i]?.code === e.code ? list[i] : repeated.has(e.code) ? undefined : list.find((y) => y.code === e.code);
      if (!x) return null;
      const residues = e.sequence.replace(/[^A-Za-z]/g, "").length;
      return x.residues === residues ? x : null;
    });
  }, [ssData, entries]);
  const gridSecondary = useMemo<GridSecondary | null>(() => {
    if (!ssOn || !ssData) return null;
    const states = entries.map((e, i) => (ssEntries[i]?.ss ? columnStates(e.sequence, ssEntries[i]!.ss!) : null));
    const codes = entries.map((e, i) => (ssEntries[i]?.source === "dssp" && ssEntries[i]?.ss ? columnCodes(e.sequence, ssEntries[i]!.ss!) : null));
    const sources = ssEntries.map((x) => x?.source ?? null);
    return { states, codes, sources, conflicts: gapConflicts(entries.map((e) => e.sequence), entries.map((e) => e.kind), states, sources) };
  }, [ssOn, ssData, ssEntries, entries]);
  /**
   * Toglie le code del bersaglio senza template. Il riferimento delle sostituzioni perde gli stessi residui; se la seconda
   * riga del bersaglio ha un residuo iniziale numerico, cresce di quanti residui sono stati tolti all'inizio.
   */
  const trimEnds = () => {
    const r = trimUncoveredEnds(entries.map((e) => e.sequence), entries.map((e) => e.kind));
    if (!r) {
      toast.push("Niente da ritagliare: le estremità del bersaglio hanno già un template di fronte.", "info");
      return;
    }
    const ti = entries.findIndex((e) => e.kind === "sequence");
    const t = entries[ti];
    let text = rewritePir(content, r.rows);
    const h = parseHeader(t.header);
    const first = parseInt(h.startRes, 10);
    if (r.start && Number.isFinite(first)) text = setEntryHeader(text, ti, t.code, formatHeader({ ...h, startRes: String(first + r.start) }));
    setOriginal((o) => (o[t.code] === undefined ? o : { ...o, [t.code]: o[t.code].slice(r.start, o[t.code].length - r.end) }));
    change(text);
    toast.push(
      `Tolti dal bersaglio ${r.start} residui all'inizio e ${r.end} alla fine.`
        + (r.start && !Number.isFinite(first) ? " La numerazione ora parte dal primo residuo rimasto." : ""),
      "info",
    );
  };

  /** Residui allineati diversamente rispetto all'ultima versione caricata o salvata. */
  const diff = useMemo(() => (savedContent && savedContent !== content ? alignmentDiff(parsePir(savedContent), entries) : null), [savedContent, content, entries]);
  const changedCells = useMemo(() => {
    if (!diff || !showChanges) return undefined;
    return diffCells(diff, entries.map((e) => e.sequence), entries.findIndex((e) => e.kind === "sequence"));
  }, [diff, showChanges, entries]);

  // annotazioni sui residui del bersaglio (metadati del documento) e loro colonne attuali
  const annotations = useMemo(() => readAnnotations(meta), [meta]);
  const targetSeq = entries.find((e) => e.kind === "sequence")?.sequence ?? "";
  const annotationCols = useMemo(() => annotations.map((a) => ({ a, cols: annotationColumns(targetSeq, a) })), [annotations, targetSeq]);
  const bands = useMemo(() => annotationCols.flatMap(({ a, cols }) => (cols ? [{ ...cols, color: a.color, label: a.label }] : [])), [annotationCols]);
  const setAnnotations = (list: PirAnnotation[]) => {
    setMeta((m) => ({ ...m, annotations: list }));
    setDirty(true);
  };
  const annotate = (c0: number, c1: number) => {
    const res = residuesInColumns(targetSeq, c0, c1);
    if (!res) toast.push("Nelle colonne selezionate il bersaglio non ha residui: seleziona almeno un residuo del bersaglio.", "warn");
    else setAnnotating(res);
  };

  /**
   * Template da mostrare in 3D: la voce sotto il cursore se è un template con la struttura secondaria (residuo del file
   * noto), altrimenti il primo template con un residuo nella colonna del cursore, altrimenti il primo template.
   */
  const structureFocus = useMemo<StructureFocus | null>(() => {
    const usable = (i: number) => ssEntries[i]?.source === "dssp" && !!ssEntries[i]?.template && !!ssEntries[i]?.residue_ids;
    const letterAt = (i: number) => /[A-Za-z]/.test(entries[i]?.sequence[cursorCell.c] ?? "");
    let r = usable(cursorCell.r) && letterAt(cursorCell.r) ? cursorCell.r : entries.findIndex((_e, i) => usable(i) && letterAt(i));
    if (r < 0) r = entries.findIndex((_e, i) => usable(i));
    if (r < 0) return null;
    const e = ssEntries[r]!;
    const seq = entries[r].sequence;
    let residue: string | null = null;
    if (letterAt(r)) {
      const index = seq.slice(0, cursorCell.c + 1).replace(/[^A-Za-z]/g, "").length;
      residue = e.residue_ids?.[index - 1] ?? null;
    }
    return { code: entries[r].code, template: e.template!, residue };
  }, [ssEntries, entries, cursorCell]);
  /** Clic su un residuo nella vista 3D: il cursore va sulla colonna di quel residuo nella voce del template. */
  const pick3d = (code: string, res: { chain: string; resnum: string }) => {
    const r = entries.findIndex((e) => e.code === code);
    const index = ssEntries[r]?.residue_ids?.findIndex((id) => id === `${res.chain}:${res.resnum}`) ?? -1;
    const c = index >= 0 ? columnOfResidue(entries[r].sequence, index + 1) : null;
    if (c === null) {
      toast.push(`Il residuo ${res.chain}:${res.resnum} non è nella voce ${code} del PIR.`, "info");
      return;
    }
    setTab("grid");
    setGridFocus({ r, c, n: Date.now() });
  };

  /** Controlli in tempo reale, per la griglia (segni sul righello, F8). */
  const issues = useMemo(() => {
    const rows = entries.map((e) => e.sequence);
    const kinds = entries.map((e) => e.kind);
    return [
      ...chainBreakIssues(rows),
      ...(ssOn ? mismatchIssues(rows, ssEntries) : []),
      ...conflictIssues(gridSecondary?.conflicts ?? []),
      ...uncoveredIssues(rows, kinds),
    ];
  }, [entries, ssOn, ssEntries, gridSecondary]);
  const toggleSs = (on: boolean) => {
    setSsOn(on);
    try {
      localStorage.setItem(SS_PREF, on ? "1" : "0");
    } catch {
      // preferenza non salvata (archivio del browser non disponibile): vale fino alla chiusura della pagina
    }
  };

  /** Modifica dalla griglia: le voci comparse dopo il caricamento (testo incollato, codici nuovi) prendono come riferimento la sequenza di adesso. */
  const editRows = (rows: string[]) => {
    setOriginal((o) => ({ ...originalSequences(entries), ...o }));
    change(rewritePir(content, rows));
  };
  /** Riporta all'originale le sostituzioni date; il focus torna alla griglia, sul primo residuo ripristinato. */
  const revert = (list: PirMutation[]) => {
    const rows = revertMutations(entries.map((e) => e.sequence), list);
    if (rows) change(rewritePir(content, squareUp(rows)));
    setTab("grid");
    setGridFocus({ r: list[0]?.row, c: list[0]?.column, n: Date.now() });
  };
  /** Le voci che hanno cambiato lunghezza prendono come riferimento la sequenza di adesso; le altre restano com'erano. */
  const resetReference = () => {
    const changed = new Set(mutationReport.lengthChanged);
    setOriginal((o) => ({ ...o, ...Object.fromEntries(entries.filter((e) => changed.has(e.code)).map((e) => [e.code, residuesOf(e.sequence)])) }));
    setTab("grid");
    setGridFocus({ n: Date.now() });
  };
  /**
   * Metadati da salvare. Il riferimento delle sostituzioni serve finché una voce differisce dal suo originale; altrimenti
   * si salva null: il server unisce i metadati, quindi togliere la chiave lascerebbe quello vecchio.
   */
  const metaToSave = () => {
    const out: Record<string, unknown> = { ...meta };
    const differs = mutationReport.mutations.length > 0 || mutationReport.lengthChanged.length > 0;
    out[ORIGINAL_KEY] = differs ? Object.fromEntries(entries.filter((e) => e.code in original).map((e) => [e.code, original[e.code]])) : null;
    return out;
  };

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
      const saved = metaToSave();
      const d = await api.saveDocument(projectId, { name: name || "Allineamento PIR", kind: "pir", content, meta: saved }, asNew ? undefined : docId ?? undefined);
      setDirty(false);
      setSavedContent(content);
      setMeta(d.meta ?? saved);
      reload();
      if (d.id !== docId) justSaved.current = d.id;
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
              {entries.length > 0 && diff && (diff.templates.length > 0 || diff.notComparable.length > 0) && (
                <div className="row small pirg-diff-line">
                  <span>
                    <b>Rispetto alla versione caricata o salvata:</b>{" "}
                    {diff.templates.map((d) => `residui ${ranges(d.changed)} del bersaglio allineati diversamente a ${d.code}`).join(" · ")}
                    {diff.notComparable.length > 0 && <span className="text-2">{diff.templates.length ? " · " : ""}non confrontabili (residui aggiunti o tolti, codici nuovi): {diff.notComparable.join(", ")}</span>}
                  </span>
                  {diff.templates.length > 0 && (
                    <label className="check right">
                      <input type="checkbox" checked={showChanges} onChange={(e) => setShowChanges(e.target.checked)} />
                      Mostra nella griglia
                    </label>
                  )}
                </div>
              )}
              {entries.length > 0 && tab === "grid" ? (
                <PirGrid
                  ids={entries.map((e) => e.code)}
                  kinds={entries.map((e) => e.kind)}
                  headers={entries.map((e) => e.header)}
                  rows={entries.map((e) => e.sequence)}
                  onEdit={editRows}
                  onUndo={() => step("undo")}
                  onRedo={() => step("redo")}
                  canUndo={history.past.length > 0}
                  canRedo={history.future.length > 0}
                  templatesEditable={templatesEditable}
                  onTemplatesEditable={(on) => (on ? setConfirmUnlock(true) : setTemplatesEditable(false))}
                  mutated={mutated}
                  focus={gridFocus}
                  onFocusDone={() => setGridFocus(null)}
                  secondary={gridSecondary}
                  issues={issues}
                  onEditHeader={setHeaderRow}
                  onTrimEnds={trimEnds}
                  changed={changedCells}
                  bands={bands}
                  onAnnotate={annotate}
                  onCursor={(r, c) => setCursorCell((x) => (x.r === r && x.c === c ? x : { r, c }))}
                />
              ) : (
                <textarea
                  className="input"
                  rows={16}
                  spellCheck={false}
                  aria-label="Allineamento in formato PIR"
                  value={content}
                  onChange={(e) => {
                    // mentre si scrive un allineamento nuovo resta la casella di testo (la griglia le toglierebbe il focus)
                    if (!entries.length) setTab("text");
                    change(e.target.value, true);
                  }}
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
          {entries.length > 0 && <PirStructureView open={view3d} onToggle={setView3d} focus={structureFocus} onPick={pick3d} />}
          <AnnotationsCard
            annotations={annotations}
            missing={new Set(annotationCols.filter((x) => !x.cols).map((x) => x.a.id))}
            onGo={(a) => {
              const cols = annotationColumns(targetSeq, a);
              if (!cols) return;
              setTab("grid");
              setGridFocus({ r: entries.findIndex((e) => e.kind === "sequence"), c: cols.from, n: Date.now() });
            }}
            onDelete={(a) => setAnnotations(annotations.filter((x) => x.id !== a.id))}
          />
          <PirMutations
            report={mutationReport}
            onGo={(m) => {
              setTab("grid");
              setGridFocus({ r: m.row, c: m.column, n: Date.now() });
            }}
            onRevert={revert}
            onResetReference={resetReference}
          />
          {entries.length > 0 && (
            <PirSecondaryCard
              entries={ssEntries}
              conflicts={gridSecondary?.conflicts ?? []}
              codes={entries.map((e) => e.code)}
              enabled={ssOn}
              loading={ssLoading}
              error={ssError?.key === ssKey ? ssError.message : null}
              onToggle={toggleSs}
              onGoColumn={(r, c) => {
                setTab("grid");
                setGridFocus({ r, c, n: Date.now() });
              }}
              onGoResidue={(r, index) => {
                const c = columnOfResidue(entries[r]?.sequence ?? "", index);
                if (c === null) return;
                setTab("grid");
                setGridFocus({ r, c, n: Date.now() });
              }}
            />
          )}
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
      {annotating && (
        <AnnotationDialog
          from={annotating.from}
          to={annotating.to}
          onClose={() => {
            setAnnotating(null);
            setGridFocus({ n: Date.now() });
          }}
          onSave={(label, color) => {
            setAnnotations([...annotations, { id: `a${Date.now().toString(36)}`, label, from: annotating.from, to: annotating.to, color }]);
            setAnnotating(null);
            setGridFocus({ n: Date.now() });
          }}
        />
      )}
      {headerRow !== null && entries[headerRow] && (
        <PirHeaderDialog
          code={entries[headerRow].code}
          header={entries[headerRow].header}
          onClose={() => {
            setHeaderRow(null);
            setGridFocus({ n: Date.now() });
          }}
          onSave={(code, header) => {
            const before = entries[headerRow].code;
            // la voce rinominata tiene il suo riferimento delle sostituzioni
            if (code !== before) {
              setOriginal((o) => {
                if (!(before in o)) return o;
                const { [before]: seq, ...rest } = o;
                return { ...rest, [code]: seq };
              });
            }
            change(setEntryHeader(content, headerRow, code, header));
            setHeaderRow(null);
            setGridFocus({ n: Date.now() });
          }}
        />
      )}
      {confirmUnlock && (
        <Confirm
          text={
            <>
              I residui dei template devono essere quelli del file della struttura: MODELLER ne legge le coordinate e si ferma se un residuo
              dell'allineamento non corrisponde. Sblocca solo per correggere un PIR che già non corrisponde al file. Le sostituzioni restano
              nell'elenco e si possono annullare.
            </>
          }
          confirmLabel="Sblocca i template"
          onCancel={() => setConfirmUnlock(false)}
          onConfirm={() => {
            setTemplatesEditable(true);
            setConfirmUnlock(false);
          }}
        />
      )}
      {toDelete && docId && projectId && (
        <Confirm text="Eliminare questo documento?" confirmLabel="Elimina" onCancel={() => setToDelete(false)} onConfirm={() => api.deleteDocument(projectId, docId).then(() => { setToDelete(false); setParams({}); load(""); setName(""); reload(); }).catch(toast.error)} />
      )}
    </div>
  );
}
