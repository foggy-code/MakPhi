import { useEffect, useMemo, useState } from "react";
import { api } from "../api/client";
import type { Align2dResult, AssemblyList, Sequence, TemplateAnalysis, TemplateItem } from "../api/types";
import {
  assemblyChainLabel, assemblyImportable, assemblyLabel, complexRequest, complexTemplateRefs, MAX_COMPLEX_CHAINS, MAX_COMPLEX_RESIDUES, resolvedSequence,
  rowsForChains, splitPirChains, SYMMETRY_WEIGHT, type ChainSource, type ComplexRow, type ComplexState,
} from "../lib/complex";
import { useProject, useToast } from "../lib/context";
import { plural } from "../lib/format";
import { useAsync, useJob } from "../lib/hooks";
import { parsePir } from "../lib/pir";
import { AlignmentView } from "./AlignmentView";
import { Icon } from "./Icon";
import { JobPanel } from "./JobPanel";
import { Alert, Badge, Field, Segmented, Spinner } from "./ui";

/** Anteprima dell'allineamento per catena (job align2d con il complesso), in modalità Confidenza. */
function ComplexPreview({ jobId }: { jobId: string }) {
  const { job, summary, log, cancel } = useJob<Align2dResult>(jobId, { withLog: true });
  const result = job?.status === "completed" ? job.result : null;
  const chains = useMemo(() => {
    if (!result) return null;
    const labels = (result.chains ?? []).map((c) => `Catena ${c.template_chain} del template ← ${c.name}${c.same_as ? ` (stessa della catena ${c.same_as})` : ""} · ${c.length} residui`);
    return splitPirChains(parsePir(result.pir), labels);
  }, [result]);
  if (!result) return <JobPanel summary={summary} log={log} onCancel={cancel} compact />;
  return (
    <div className="stack-sm">
      {result.report.warnings.map((w, i) => <Alert key={i} tone="warn">{w}</Alert>)}
      {!chains ? <Alert tone="warn">L'allineamento non ha lo stesso numero di catene in tutte le voci.</Alert> : chains.map((c) => (
        <div key={c.label} className="stack-sm">
          <b className="small">{c.label}</b>
          <AlignmentView ids={c.ids} aligned={c.aligned} reference={c.reference} initialMode="confidence" />
        </div>
      ))}
    </div>
  );
}

function ChainRow({ row, rows, seqs, onChange }: { row: ComplexRow; rows: ComplexRow[]; seqs: Sequence[]; onChange: (patch: Partial<ComplexRow>) => void }) {
  // "stessa di": solo catene scelte con una sequenza propria
  const refs = rows.filter((r) => r.chain !== row.chain && r.included && r.source !== "same");
  const options: { value: ChainSource; label: string }[] = [
    { value: "project", label: "Dal progetto" }, { value: "paste", label: "Incolla" },
    ...(refs.length || row.source === "same" ? [{ value: "same" as ChainSource, label: "Stessa di…" }] : []),
  ];
  const length = resolvedSequence(row, rows).length;
  return (
    <div className="card flat" style={{ padding: "8px 10px" }}>
      <div className="row">
        <label className="check">
          <input type="checkbox" checked={row.included} onChange={(e) => onChange({ included: e.target.checked })} />
          <b>Catena {row.chain}</b>{row.residues ? <span className="text-2"> ({row.residues} aa nel template)</span> : null}
        </label>
        {row.included && <div className="right"><Segmented label={`Sequenza della catena ${row.chain}`} value={row.source} options={options} onChange={(s) => onChange({ source: s, sameAs: s === "same" ? row.sameAs || refs[0]?.chain || "" : row.sameAs })} /></div>}
      </div>
      {row.included && (
        <div className="stack-sm" style={{ marginTop: 6 }}>
          {row.source === "project" ? (
            <select className="input" value={row.sequenceId ?? ""} onChange={(e) => {
              const s = seqs.find((x) => x.id === e.target.value);
              onChange(s ? { sequenceId: s.id, sequence: s.sequence, name: s.name } : { sequenceId: "", sequence: "", name: "" });
            }}>
              <option value="">{seqs.length ? "— sequenza del progetto —" : "Nessuna sequenza nel progetto"}</option>
              {seqs.map((s) => <option key={s.id} value={s.id}>{s.name} · {s.length} aa</option>)}
            </select>
          ) : row.source === "paste" ? (
            <textarea className="input" rows={2} placeholder="Sequenza del bersaglio per questa catena (testo o FASTA)" value={row.sequence}
              onChange={(e) => onChange({ sequence: e.target.value, sequenceId: "" })} />
          ) : (
            <select className="input" value={row.sameAs} onChange={(e) => onChange({ sameAs: e.target.value })}>
              <option value="">— stessa sequenza della catena —</option>
              {refs.map((r) => <option key={r.chain} value={r.chain}>catena {r.chain}{r.name ? ` (${r.name})` : ""}</option>)}
            </select>
          )}
          <span className="small text-2">{length ? `${length} residui` : "nessuna sequenza"}{row.source === "same" && row.sameAs ? ` · come la catena ${row.sameAs} (omo-oligomero)` : ""}</span>
        </div>
      )}
    </div>
  );
}

/**
 * Modalità Complesso della Modellazione (F5.3): template (o assemblaggio biologico importato) e, per ogni sua catena,
 * la sequenza del bersaglio — dal progetto, incollata o «stessa della catena …» — con le restrizioni di simmetria per le
 * catene uguali e l'anteprima dell'allineamento per catena.
 */
export function ComplexInput({ value, onChange }: { value: ComplexState; onChange: (v: ComplexState) => void }) {
  const { projectId } = useProject();
  const toast = useToast();
  const { data: library, reload: reloadLibrary } = useAsync<TemplateItem[]>(() => api.templates(), []);
  const { data: analysis, loading, error } = useAsync<TemplateAnalysis | null>(() => (value.templateId ? api.template(value.templateId) : Promise.resolve(null)), [value.templateId]);
  const { data: assemblies } = useAsync<AssemblyList | null>(() => (value.templateId ? api.templateAssemblies(value.templateId).catch(() => null) : Promise.resolve(null)), [value.templateId]);
  const { data: seqs } = useAsync<Sequence[]>(() => (projectId ? api.sequences(projectId) : Promise.resolve([])), [projectId]);
  const [pdbId, setPdbId] = useState("");
  const [busy, setBusy] = useState("");
  const [previewJob, setPreviewJob] = useState<string | null>(null);

  // righe per le catene del template letto (quelle già compilate restano)
  useEffect(() => {
    if (!analysis || analysis.id !== value.templateId) return;
    const rows = rowsForChains(analysis.chains, value.rows);
    if (rows.length !== value.rows.length || rows.some((r, i) => r.chain !== value.rows[i]?.chain || r.residues !== value.rows[i]?.residues)) onChange({ ...value, rows });
    // eslint-disable-next-line react-hooks/exhaustive-deps -- solo all'arrivo dell'analisi, non a ogni modifica delle righe
  }, [analysis]);
  useEffect(() => setPreviewJob(null), [value.templateId]);

  const check = complexRequest(value);
  const setRow = (i: number, patch: Partial<ComplexRow>) => onChange({ ...value, rows: value.rows.map((r, k) => (k === i ? { ...r, ...patch } : r)) });
  const choose = (templateId: string) => onChange({ ...value, templateId, rows: [] });

  const fetchPdb = async () => {
    setBusy("fetch");
    try {
      const t = await api.fetchTemplate(pdbId.trim());
      reloadLibrary();
      setPdbId("");
      choose(t.id);
    } catch (e) {
      toast.error(e);
    } finally {
      setBusy("");
    }
  };
  const importAssembly = async (assemblyId: string) => {
    setBusy(assemblyId);
    try {
      const t = await api.importAssembly(value.templateId, assemblyId);
      toast.push(t.cached ? `${t.id} era già in libreria` : `Assemblaggio importato come template ${t.id}`);
      reloadLibrary();
      choose(t.id);
    } catch (e) {
      toast.error(e);
    } finally {
      setBusy("");
    }
  };
  const preview = async () => {
    if (!check.request) return;
    try {
      const j = await api.align2d({ project_id: projectId ?? undefined, templates: complexTemplateRefs(value), complex: check.request });
      setPreviewJob(j.id);
    } catch (e) {
      toast.error(e);
    }
  };

  const chosen = library?.find((t) => t.id === value.templateId);
  const importable = assemblies?.assemblies.filter(assemblyImportable) ?? [];
  const others = assemblies?.assemblies.filter((a) => !assemblyImportable(a)) ?? [];
  return (
    <div className="stack">
      <Field label="Template del complesso" hint="Una struttura con più catene, oppure un assemblaggio biologico importato dalla sua struttura.">
        <div className="stack-sm">
          <select className="input" value={value.templateId} onChange={(e) => choose(e.target.value)}>
            <option value="">{library?.length ? "— template della libreria —" : "Libreria vuota: scarica un template"}</option>
            {library?.map((t) => (
              <option key={t.id} value={t.id}>
                {t.id} · {plural(t.chains.length, "catena", "catene")}{t.assembly ? ` · assemblaggio ${t.assembly.assembly_id} di ${t.assembly.source_id}` : t.title ? ` · ${t.title.slice(0, 50)}` : ""}
              </option>
            ))}
          </select>
          <div className="row">
            <input className="input" style={{ width: 170, textTransform: "uppercase" }} placeholder="Codice PDB (es. 4HHB)" aria-label="Codice PDB" maxLength={4} value={pdbId} onChange={(e) => setPdbId(e.target.value)} />
            <button type="button" className="btn btn-sm" disabled={pdbId.trim().length !== 4 || !!busy} onClick={fetchPdb}>{busy === "fetch" ? <Spinner /> : <Icon name="download" size={13} />} Scarica da RCSB</button>
          </div>
        </div>
      </Field>
      {value.templateId && loading && !analysis && <Spinner />}
      {error && <Alert tone="err">{error.message}</Alert>}
      {chosen?.assembly && (
        <span className="small text-2">Assemblaggio {chosen.assembly.assembly_id} di {chosen.assembly.source_id}: {chosen.assembly.chains.map((c) => assemblyChainLabel(c)).join(", ")}.</span>
      )}
      {(importable.length > 0 || others.length > 0) && (
        <div className="stack-sm">
          <span className="small text-2" style={{ fontWeight: 550 }}>Assemblaggi biologici di {value.templateId}</span>
          {importable.map((a) => (
            <div key={a.id} className="row small">
              <span className="text-2" style={{ flex: 1 }}>{assemblyLabel(a)}</span>
              <button type="button" className="btn btn-sm" disabled={!!busy} onClick={() => importAssembly(a.id)}>{busy === a.id ? <Spinner /> : <Icon name="layers" size={13} />} Importa e usa</button>
            </div>
          ))}
          {others.map((a) => (
            <div key={a.id} className="small text-2">
              {assemblyLabel(a)} · {!a.buildable ? a.note : a.identity_only ? "coincide con le catene del file: usa direttamente il template" : "una sola catena proteica"}
            </div>
          ))}
        </div>
      )}
      {assemblies?.note && !importable.length && value.templateId && <span className="small text-2">{assemblies.note}</span>}
      <Field label="Nome del complesso"><input className="input" value={value.name} onChange={(e) => onChange({ ...value, name: e.target.value })} /></Field>
      {value.rows.length > 0 && (
        <div className="stack-sm">
          <span className="small text-2" style={{ fontWeight: 550 }}>Sequenza del bersaglio per ogni catena del template</span>
          {value.rows.map((r, i) => <ChainRow key={r.chain} row={r} rows={value.rows} seqs={seqs ?? []} onChange={(patch) => setRow(i, patch)} />)}
          <span className="small text-2">
            {check.chains} catene, {check.residues} residui (massimo {MAX_COMPLEX_CHAINS} catene e {MAX_COMPLEX_RESIDUES} residui: il tempo di MODELLER cresce con i residui del modello). Le catene del modello avranno i nomi del template, con i residui numerati da 1.
          </span>
          {check.errors.filter((e) => !e.startsWith("Scegli il template")).map((e) => <span key={e} className="small" style={{ color: "var(--warn)" }}>{e}</span>)}
        </div>
      )}
      {value.rows.length > 0 && (
        <div className="stack-sm">
          <label className="check" title="Restrizioni di simmetria di MODELLER sui C-alfa: ogni catena uguale è vincolata alla prima del suo gruppo">
            <input type="checkbox" checked={value.symmetry} disabled={!check.symmetric.length} onChange={(e) => onChange({ ...value, symmetry: e.target.checked })} />
            Restrizioni di simmetria fra le catene con la stessa sequenza
            {check.symmetric.length > 0 ? <Badge tone="info">{check.symmetric.map((g) => g.join(" = ")).join(" · ")}</Badge> : <span className="text-2"> (nessuna coppia di catene uguali)</span>}
          </label>
          {value.symmetry && check.symmetric.length > 0 && (
            <div className="row small">
              <span className="text-2">Peso</span>
              <input className="input" style={{ width: 90 }} type="number" min={0.1} max={10} step={0.1} aria-label="Peso delle restrizioni di simmetria" value={value.weight} onChange={(e) => onChange({ ...value, weight: Number(e.target.value) || SYMMETRY_WEIGHT })} />
              <span className="small text-2">predefinito 1, come nel manuale di MODELLER: le catene restano uguali fra loro, la loro disposizione viene dal template.</span>
            </div>
          )}
        </div>
      )}
      <div className="row">
        <button type="button" className="btn btn-sm" disabled={!check.request} onClick={preview}><Icon name="align" size={13} /> Anteprima dell'allineamento per catena</button>
        <span className="small text-2">MODELLER allinea ogni catena del bersaglio sulla sua catena del template (align2d).</span>
      </div>
      {previewJob && <ComplexPreview key={previewJob} jobId={previewJob} />}
    </div>
  );
}
