import { useEffect, useRef, useState } from "react";
import { api, ApiError } from "../api/client";
import type { JobSummary, ParsedFile, Sequence, StructureSource, TemplateAnalysis, TemplateItem, TemplateRef, UploadedStructure } from "../api/types";
import { isAlphaFoldId, plddtLabel, plddtTone, predictorBadge, predictorTitle } from "../lib/alphafold";
import { useProject, useToast } from "../lib/context";
import { useAsync } from "../lib/hooks";
import { defaultTransfer } from "../lib/ligands";
import { jobChoices, modelChoices, modelFiles, parsePastedSequence, pastedTextMatches } from "../lib/picker";
import { FileButton, SequenceFileButton } from "./FileLoad";
import { Icon } from "./Icon";
import { Badge, DropZone, Segmented } from "./ui";

/** Sequenza bersaglio: da progetto, incollata (FASTA o testo) oppure da file in qualunque formato comune. */
export function SequencePicker({ value, onChange, label = "Sequenza bersaglio" }: {
  value: { name: string; sequence: string };
  onChange: (v: { name: string; sequence: string; id?: string }) => void;
  label?: string;
}) {
  const { projectId } = useProject();
  const { data: seqs } = useAsync<Sequence[]>(() => (projectId ? api.sequences(projectId) : Promise.resolve([])), [projectId]);
  const [mode, setMode] = useState<"project" | "paste" | "file">("project");
  const [fromFile, setFromFile] = useState<ParsedFile | null>(null);
  // testo grezzo del campo «Incolla»: un'intestazione FASTA in corso di scrittura resta visibile (parsePastedSequence)
  const [pasted, setPasted] = useState(value.sequence);
  useEffect(() => {
    setPasted((raw) => (pastedTextMatches(raw, value) ? raw : value.sequence));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [value.name, value.sequence]);
  useEffect(() => {
    if (seqs && seqs.length === 0) setMode((m) => (m === "project" ? "paste" : m));
  }, [seqs]);
  const selectedId = seqs?.find((s) => s.sequence === value.sequence && s.name === value.name)?.id ?? "";
  const fileIndex = fromFile?.records.findIndex((r) => r.sequence === value.sequence && r.name === value.name) ?? -1;
  return (
    <div className="stack-sm">
      <div className="row">
        <span className="small text-2" style={{ fontWeight: 550 }}>{label}</span>
        <div className="right">
          <Segmented value={mode} onChange={setMode} options={[{ value: "project", label: "Dal progetto" }, { value: "paste", label: "Incolla" }, { value: "file", label: "Da file" }]} />
        </div>
      </div>
      {mode === "file" ? (
        <div className="stack-sm">
          <div className="row">
            <SequenceFileButton
              onLoaded={(parsed) => {
                setFromFile(parsed);
                const first = parsed.records[0];
                onChange({ name: first.name, sequence: first.sequence });
              }}
            >
              Scegli un file
            </SequenceFileButton>
            {fromFile && <span className="tiny muted ellipsis" style={{ maxWidth: 260 }}>{fromFile.filename} · {fromFile.format_label}</span>}
          </div>
          {fromFile && fromFile.records.length > 1 && (
            <select className="input" value={fileIndex} onChange={(e) => {
              const r = fromFile.records[Number(e.target.value)];
              if (r) onChange({ name: r.name, sequence: r.sequence });
            }}>
              {fromFile.records.map((r, i) => (
                <option key={i} value={i}>{r.name} · {r.length} aa {r.description ? `· ${r.description.slice(0, 50)}` : ""}</option>
              ))}
            </select>
          )}
          {!fromFile && <span className="tiny muted">FASTA, PIR, Clustal, GenBank, UniProt, PDB/mmCIF (una sequenza per catena) o testo semplice.</span>}
        </div>
      ) : mode === "project" ? (
        <select
          className="input"
          value={selectedId}
          onChange={(e) => {
            const s = seqs?.find((x) => x.id === e.target.value);
            if (s) onChange({ name: s.name, sequence: s.sequence, id: s.id });
          }}
        >
          <option value="">{seqs?.length ? "— scegli una sequenza —" : "Nessuna sequenza nel progetto"}</option>
          {seqs?.map((s) => (
            <option key={s.id} value={s.id}>
              {s.name} · {s.length} aa
            </option>
          ))}
        </select>
      ) : (
        <>
          <input className="input" placeholder="Nome (es. target)" value={value.name} onChange={(e) => onChange({ ...value, name: e.target.value })} />
          <textarea
            className="input"
            rows={5}
            placeholder="Sequenza amminoacidica (testo o FASTA)"
            value={pasted}
            onChange={(e) => {
              setPasted(e.target.value);
              const next = parsePastedSequence(e.target.value, value);
              if (next.name !== value.name || next.sequence !== value.sequence) onChange(next);
            }}
          />
        </>
      )}
      {value.sequence && <span className="tiny muted">{value.sequence.replace(/[^A-Za-z]/g, "").length} residui</span>}
    </div>
  );
}

/** Selezione di uno o più template della libreria con catene e ligandi. `ligands` = false nasconde la casella
 *  "Ligandi" del singolo template: in Modellazione la scelta dei gruppi da trasferire ha un riquadro proprio
 *  (LigandPicker), nell'Editor PIR i ligandi non entrano nell'allineamento (li aggiunge la modellazione).
 *  `onAnalysis`: ogni analisi letta (anche dopo il download da RCSB o AlphaFold DB), per chi ne ha bisogno accanto. */
export function TemplatePicker({ value, onChange, max = 10, ligands = true, onAnalysis }: {
  value: TemplateRef[];
  onChange: (v: TemplateRef[]) => void;
  max?: number;
  ligands?: boolean;
  onAnalysis?: (id: string, analysis: TemplateAnalysis) => void;
}) {
  const toast = useToast();
  const { data: templates, reload } = useAsync<TemplateItem[]>(() => api.templates(), []);
  const [adding, setAdding] = useState("");
  const [analysis, setAnalysis] = useState<Record<string, TemplateAnalysis>>({});
  const [status, setStatus] = useState<Record<string, "loading" | "fetching" | "ok" | "error">>({});
  const valueRef = useRef(value);
  valueRef.current = value;
  // Analizza i template scelti; i codici PDB assenti dalla libreria vengono scaricati da RCSB, gli id
  // AF-<ACC>-F<n> (risultati del motore AlphaFold) da AlphaFold DB.
  useEffect(() => {
    value.forEach((t) => {
      if (status[t.id]) return;
      setStatus((s) => ({ ...s, [t.id]: "loading" }));
      api
        .template(t.id)
        .catch((err: ApiError) => {
          const fetcher = err.status !== 404 ? null
            : /^[0-9][A-Za-z0-9]{3}$/.test(t.id) ? api.fetchTemplate
            : isAlphaFoldId(t.id) ? api.fetchAlphaFold : null;
          if (fetcher) {
            setStatus((s) => ({ ...s, [t.id]: "fetching" }));
            return fetcher(t.id).then(() => {
              reload();
              return api.template(t.id);
            });
          }
          throw err;
        })
        .then((a) => {
          setAnalysis((prev) => ({ ...prev, [t.id]: a }));
          setStatus((s) => ({ ...s, [t.id]: "ok" }));
          onAnalysis?.(t.id, a);
          const current = valueRef.current.find((v) => v.id === t.id);
          if (current && !current.chains.length && a.chains.length) {
            onChange(valueRef.current.map((v) => (v.id === t.id ? { ...v, chains: [a.chains[0].chain] } : v)));
          }
        })
        .catch(() => setStatus((s) => ({ ...s, [t.id]: "error" })));
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [value.map((t) => t.id).join(",")]);
  const add = (id: string) => {
    if (!id || value.some((v) => v.id === id) || value.length >= max) return;
    const tpl = templates?.find((t) => t.id === id);
    onChange([...value, { id, chains: tpl?.chains?.slice(0, 1) ?? [] }]);
  };
  return (
    <div className="stack-sm">
      <div className="row">
        <select className="input" style={{ flex: 1 }} value={adding} onChange={(e) => setAdding(e.target.value)} onFocus={reload}>
          <option value="">{templates?.length ? "— aggiungi un template dalla libreria —" : "Libreria vuota: scarica un template"}</option>
          {templates?.filter((t) => !value.some((v) => v.id === t.id)).map((t) => (
            <option key={t.id} value={t.id}>
              {t.id} · {t.title ? t.title.slice(0, 60) : t.source} {t.resolution ? `· ${t.resolution} Å` : ""}
            </option>
          ))}
        </select>
        <button className="btn" disabled={!adding} onClick={() => { add(adding); setAdding(""); }}>
          <Icon name="plus" size={14} /> Aggiungi
        </button>
        <FileButton
          accept=".pdb,.ent,.cif,.mmcif"
          small={false}
          disabled={value.length >= max}
          title="Carica un file PDB/mmCIF nella libreria e usalo come template"
          onFile={async (file) => {
            try {
              const t = await api.uploadTemplate(file);
              reload();
              if (!value.some((v) => v.id === t.id)) onChange([...value, { id: t.id, chains: t.chains?.slice(0, 1) ?? [] }]);
              toast.push(`Template ${t.id} caricato nella libreria`);
            } catch (e) {
              toast.error(e);
            }
          }}
        >
          Da file
        </FileButton>
      </div>
      {value.map((t) => {
        const a = analysis[t.id];
        // casella «Ligandi»: solo con gruppi che la scelta predefinita trasferisce davvero dalle catene scelte
        const transfer = ligands && a ? defaultTransfer(t, a) : null;
        return (
          <div key={t.id} className="card flat" style={{ padding: "10px 12px" }}>
            <div className="row">
              <b>{t.id}</b>
              {status[t.id] === "fetching" && <span className="badge badge-info"><span className="spinner" style={{ width: 10, height: 10 }} /> download da {isAlphaFoldId(t.id) ? "AlphaFold DB" : "RCSB"}…</span>}
              {status[t.id] === "error" && <span className="badge badge-err">non disponibile</span>}
              {a?.meta.predicted && (
                <>
                  <Badge tone="warn" title={predictorTitle({ ...a.meta, id: t.id })}>{predictorBadge({ ...a.meta, id: t.id })}</Badge>
                  <Badge tone={plddtTone(a.meta.plddt_mean)}>{plddtLabel(a.meta.plddt_mean)}</Badge>
                </>
              )}
              <span className="small muted ellipsis" style={{ maxWidth: 260 }}>{a?.header.title}</span>
              <button className="btn btn-ghost btn-icon right" onClick={() => onChange(value.filter((v) => v.id !== t.id))} aria-label="Rimuovi">
                <Icon name="x" size={14} />
              </button>
            </div>
            <div className="row small" style={{ marginTop: 6 }}>
              <span className="text-2">Catene:</span>
              {(a?.chains ?? []).map((c) => (
                <label key={c.chain} className="check" title={`${c.residues} residui (${c.first}–${c.last})`}>
                  <input
                    type="checkbox"
                    checked={t.chains.includes(c.chain)}
                    onChange={(e) =>
                      onChange(value.map((v) => (v.id === t.id ? { ...v, chains: e.target.checked ? [...v.chains, c.chain] : v.chains.filter((x) => x !== c.chain) } : v)))
                    }
                  />
                  {c.chain} <span className="muted">({c.residues} aa)</span>
                </label>
              ))}
              {transfer && transfer.names.length > 0 && (
                <label className="check right" title={`Copia nel modello ligandi, cofattori e metalli della scelta predefinita${transfer.excluded.length ? `; esclusi: ${transfer.excluded.join("; ")}` : ""}`}>
                  <input type="checkbox" checked={!!t.hetatm} onChange={(e) => onChange(value.map((v) => (v.id === t.id ? { ...v, hetatm: e.target.checked } : v)))} />
                  Ligandi ({transfer.names.join(", ")})
                </label>
              )}
            </div>
          </div>
        );
      })}
    </div>
  );
}

/** Scelta di una struttura: template della libreria, modello prodotto da un job o file PDB/mmCIF caricato. */
export function StructurePicker({ value, onChange, label = "Struttura" }: { value: StructureSource | null; onChange: (v: StructureSource | null) => void; label?: string }) {
  const { projectId } = useProject();
  const toast = useToast();
  const { data: templates } = useAsync<TemplateItem[]>(() => api.templates(), []);
  const { data: jobs } = useAsync<JobSummary[]>(() => api.jobs({ kind: "model", status: "completed", project_id: projectId ?? undefined, limit: 30 }), [projectId]);
  const [kind, setKind] = useState<"template" | "job" | "upload">(value?.type === "job" ? "job" : value?.type === "upload" ? "upload" : "template");
  const [jobId, setJobId] = useState(value?.job_id ?? "");
  const [uploaded, setUploaded] = useState<UploadedStructure | null>(null);
  // Allinea lo stato interno quando la sorgente arriva dall'esterno (es. link da un'altra pagina).
  useEffect(() => {
    if (value?.type === "job") {
      setKind("job");
      setJobId(value.job_id ?? "");
    } else if (value?.type === "template") setKind("template");
    else if (value?.type === "upload") setKind("upload");
  }, [value?.type, value?.job_id]);
  const [models, setModels] = useState<string[]>([]);
  const [jobTitle, setJobTitle] = useState<string | null>(null);
  useEffect(() => {
    setModels([]);
    setJobTitle(null);
    if (!jobId) return;
    let current = true;
    // i job senza elenco di modelli (pipeline) non ne danno: il modello del link resta comunque scelto (modelChoices)
    api.job<unknown>(jobId)
      .then((j) => {
        if (!current) return;
        setModels(modelFiles(j.result));
        setJobTitle(j.title || null);
      })
      .catch(() => undefined);
    return () => {
      current = false;
    };
  }, [jobId]);
  // un job o un modello arrivati da un link (pipeline, loop, job più vecchio) restano visibili come scelti
  const jobOptions = jobChoices(jobs, jobId, jobTitle);
  const chosenFile = value?.type === "job" && value.job_id === jobId ? value.file : null;
  const modelOptions = modelChoices(models, chosenFile);
  return (
    <div className="stack-sm">
      <div className="row">
        <span className="small text-2" style={{ fontWeight: 550 }}>{label}</span>
        <div className="right">
          <Segmented value={kind} onChange={(k) => { setKind(k); onChange(k === "upload" && uploaded ? uploaded.source : null); }} options={[{ value: "template", label: "Libreria" }, { value: "job", label: "Modello" }, { value: "upload", label: "File" }]} />
        </div>
      </div>
      {kind === "upload" ? (
        <div className="stack-sm">
          <DropZone
            accept=".pdb,.ent,.cif,.mmcif"
            label={uploaded && value?.type === "upload" ? <><b>{uploaded.filename}</b> · catene {uploaded.chains.map((c) => c.chain).join(", ")} (trascina un altro file per cambiarlo)</>
              : value?.type === "upload" && value.file ? <><b>{value.file}</b> (da un link; trascina un altro file per cambiarlo)</>
              : <>Trascina un file <b>.pdb</b> o <b>.cif</b> oppure fai clic</>}
            onFile={(file) =>
              api.uploadStructure(file)
                .then((u) => { setUploaded(u); onChange({ ...u.source, chain: value?.chain }); })
                .catch(toast.error)
            }
          />
          <span className="tiny muted">Il file resta tra i caricamenti per i giorni impostati nella pulizia automatica; i risultati dei calcoli restano per sempre.</span>
        </div>
      ) : kind === "template" ? (
        <select className="input" value={value?.type === "template" ? value.id : ""} onChange={(e) => onChange(e.target.value ? { type: "template", id: e.target.value, chain: value?.chain } : null)}>
          <option value="">— template —</option>
          {templates?.map((t) => (
            <option key={t.id} value={t.id}>{t.id} {t.title ? `· ${t.title.slice(0, 50)}` : ""}</option>
          ))}
        </select>
      ) : (
        <div className="grid grid-2">
          <select className="input" value={jobId} onChange={(e) => { setJobId(e.target.value); onChange(null); }}>
            <option value="">— job di modellazione —</option>
            {jobOptions.map((j) => (
              <option key={j.value} value={j.value}>{j.label}</option>
            ))}
          </select>
          <select className="input" value={chosenFile ?? ""} disabled={!modelOptions.length} onChange={(e) => onChange(e.target.value ? { type: "job", job_id: jobId, file: e.target.value } : null)}>
            <option value="">— modello —</option>
            {modelOptions.map((m) => (
              <option key={m} value={m}>{m}</option>
            ))}
          </select>
        </div>
      )}
    </div>
  );
}
