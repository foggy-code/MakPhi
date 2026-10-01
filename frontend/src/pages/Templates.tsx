import { useEffect, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { api } from "../api/client";
import type { EntityLabels, TemplateAnalysis, TemplateItem } from "../api/types";
import { AssembliesCard } from "../components/AssembliesCard";
import { SSStrip } from "../components/charts";
import { Icon } from "../components/Icon";
import { FavoriteStar, LabelEditor, LabelFilterSelect, TagChips } from "../components/Labels";
import { Viewer3D } from "../components/Viewer3D";
import { Alert, Badge, Card, Confirm, DropZone, Empty, PageHead, Spinner, Stat } from "../components/ui";
import {
  isAlphaFoldId, isUniProtAccession, normalizeAccession, PLDDT_BAND_LABEL, PLDDT_BAND_TONE, plddtLabel, plddtTone, predictorBadge, predictorTitle, type PlddtBand,
} from "../lib/alphafold";
import { useToast } from "../lib/context";
import { fmtBytes, fmtNum, relTime } from "../lib/format";
import { useAsync } from "../lib/hooks";
import { CLASS_LABEL, CLASS_TONE, groupClass } from "../lib/ligands";
import { favoritesFirst, filterParams, filterValue, itemLabels, matchesFilter, parseFilterValue, type ItemLabels, type LabelFilter } from "../lib/tags";

/** Avviso e dettagli di un modello predetto (AlphaFold): versione, intervallo UniProt, fasce di pLDDT. */
function PredictedNote({ meta }: { meta: TemplateItem }) {
  const af = meta.alphafold;
  const fractions = meta.plddt_fractions;
  return (
    <Alert tone="warn">
      <div className="stack-sm">
        <span>
          <b>Modello predetto, non struttura sperimentale</b>
          {af ? <> · AlphaFold DB {af.version ? `v${af.version}` : ""} · UniProt {af.accession}{af.uniprot_start && af.uniprot_end ? ` (residui ${af.uniprot_start}–${af.uniprot_end})` : ""}{af.gene ? ` · ${af.gene}` : ""}</>
            : meta.predictor === "esmfold" ? " · predizione di ESMFold caricata" : predictorBadge(meta) === "AlphaFold" ? " · file di AlphaFold caricato" : " · file caricato"}
          {" "}· {plddtLabel(meta.plddt_mean)}
        </span>
        {fractions && (
          <span className="row" style={{ gap: 4 }}>
            {(Object.keys(PLDDT_BAND_LABEL) as PlddtBand[]).map((k) => (
              <Badge key={k} tone={PLDDT_BAND_TONE[k]}>
                {PLDDT_BAND_LABEL[k]} {fmtNum((fractions[k] ?? 0) * 100, 0)}%
              </Badge>
            ))}
          </span>
        )}
        <span className="tiny">
          Il pLDDT sta nella colonna B-factor. In modellazione i residui con pLDDT &lt; 70 (spesso regioni disordinate) vengono tolti dal template, salvo diversa scelta.
          {af && <> <a href={`https://alphafold.ebi.ac.uk/entry/${encodeURIComponent(af.accession)}`} target="_blank" rel="noreferrer">Scheda su AlphaFold DB</a></>}
        </span>
      </div>
    </Alert>
  );
}

function TemplateDetail({ id, labels, onDeleted, onAdded, onLabels }: { id: string; labels?: ItemLabels; onDeleted: () => void; onAdded: (id: string) => void; onLabels: (l: EntityLabels) => void }) {
  const navigate = useNavigate();
  const toast = useToast();
  const { data: a, error, loading } = useAsync<TemplateAnalysis>(() => api.template(id), [id]);
  const [confirm, setConfirm] = useState(false);
  if (loading && !a) return <Card><Spinner /></Card>;
  if (error) return <Alert tone="err">{error.message}</Alert>;
  if (!a) return null;
  const ligands = a.ligands.filter((l) => !l.additive);
  const additives = a.ligands.filter((l) => l.additive);
  const predicted = !!a.meta.predicted;
  return (
    <div className="stack">
      {predicted && <PredictedNote meta={a.meta} />}
      <Card
        title={<>{a.id} {predicted && <Badge tone="warn" title={predictorTitle(a.meta)}>{predictorBadge(a.meta)}</Badge>}</>}
        sub={a.header.title}
        right={
          <>
            <button className="btn btn-sm" onClick={() => navigate(`/quality?template=${a.id}`)}><Icon name="shield" size={14} /> Qualità</button>
            <button className="btn btn-sm btn-primary" onClick={() => navigate(`/modeling?t=${a.id}:${a.chains[0]?.chain ?? "A"}`)}><Icon name="cube" size={14} /> Usa come template</button>
            {a.chains.length > 1 && <button className="btn btn-sm" title="Modalità Complesso: una sequenza del bersaglio per ogni catena" onClick={() => navigate(`/modeling?cmplx=${encodeURIComponent(a.id)}`)}><Icon name="layers" size={14} /> Complesso</button>}
            <a className="btn btn-sm btn-ghost" href={api.templateFileUrl(a.id)} download={`${a.id}.${a.format}`}><Icon name="download" size={14} /></a>
            <button className="btn btn-sm btn-danger" onClick={() => setConfirm(true)}><Icon name="trash" size={14} /></button>
          </>
        }
      >
        <div style={{ marginBottom: 12 }}><LabelEditor type="template" id={a.id} initial={labels} onChange={onLabels} /></div>
        <div className="grid grid-4">
          <Stat label="Metodo" value={<span style={{ fontSize: 14 }}>{a.header.method || a.meta.method || "—"}</span>} />
          {predicted
            ? <Stat label="pLDDT medio" value={fmtNum(a.meta.plddt_mean ?? null, 1)} note="confidenza della predizione (0–100)" />
            : <Stat label="Risoluzione" value={a.header.resolution ? <>{fmtNum(a.header.resolution, 2)}<small> Å</small></> : "—"} />}
          <Stat label="Residui" value={a.stats.residues} note={`${a.stats.chains} catene · ${a.stats.heavy_atoms} atomi`} />
          <Stat label="Residui mancanti" value={a.header.missing_residues.length} note={a.models > 1 ? `${a.models} modelli (usato il primo)` : "REMARK 465"} />
        </div>
      </Card>
      <Viewer3D url={api.templateFileUrl(a.id)} format={a.format === "cif" ? "cif" : "pdb"} />
      <Card title="Catene">
        <div className="table-wrap">
          <table className="table">
            <thead>
              <tr><th>Catena</th><th className="num">Residui</th><th>Numerazione</th><th>Struttura secondaria (DSSP)</th><th className="num">Mancanti</th><th className="num">Interruzioni</th></tr>
            </thead>
            <tbody>
              {a.chains.map((c) => (
                <tr key={c.chain}>
                  <td><b>{c.chain}</b></td>
                  <td className="num">{c.residues}</td>
                  <td className="nowrap">{c.first}–{c.last}</td>
                  <td style={{ minWidth: 220 }}>{c.ss ? <SSStrip ss3={c.ss} /> : "—"}</td>
                  <td className="num">{c.missing ?? 0}</td>
                  <td className="num">{c.breaks.length ? <Badge tone="warn" title={c.breaks.map((b) => `${b.after}→${b.before}`).join(", ")}>{c.breaks.length}</Badge> : 0}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <p className="tiny muted" style={{ marginTop: 8 }}>
          Le interruzioni (distanza C–N &gt; 2 Å) e i residui mancanti diventano gap nell'allineamento: MODELLER li ricostruisce come loop.
        </p>
      </Card>
      {!predicted && <AssembliesCard meta={a.meta} onImported={onAdded} />}
      {(ligands.length > 0 || additives.length > 0) && (
        <Card title="Ligandi ed eteroatomi" sub="Trasferibili nel modello (Modellazione → Trasferisci ligandi, cofattori e metalli)">
          <div className="row">
            {[...ligands, ...additives].map((l, i) => (
              <Badge key={i} tone={CLASS_TONE[groupClass(l)]} title={[`${l.atoms} ${l.atoms === 1 ? "atomo" : "atomi"}`, l.description, l.reason].filter(Boolean).join(" · ")}>
                {l.resname} {l.chain}{l.resnum} · {l.label ?? CLASS_LABEL[groupClass(l)]}
              </Badge>
            ))}
          </div>
        </Card>
      )}
      {confirm && (
        <Confirm
          text={`Rimuovere ${a.id} dalla libreria? I job che lo hanno già usato conservano la propria copia.`}
          confirmLabel="Rimuovi"
          onCancel={() => setConfirm(false)}
          onConfirm={() => api.deleteTemplate(a.id).then(() => { setConfirm(false); toast.push(`${a.id} rimosso`); onDeleted(); }).catch(toast.error)}
        />
      )}
    </div>
  );
}

export default function Templates() {
  const toast = useToast();
  const [params, setParams] = useSearchParams();
  const selected = params.get("id");
  // filtro per etichetta o preferiti nell'URL (?tag=); preferiti in cima all'elenco
  const labelFilter = parseFilterValue(params.get("tag"));
  const labelKey = filterValue(labelFilter);
  const setLabelFilter = (f: LabelFilter) => setParams((p) => {
    const next = new URLSearchParams(p);
    if (filterValue(f)) next.set("tag", filterValue(f));
    else next.delete("tag");
    return next;
  });
  const selectTemplate = (id: string) => setParams((p) => {
    const next = new URLSearchParams(p);
    next.set("id", id);
    return next;
  });
  const { data: rawList, reload, loading, setData } = useAsync<TemplateItem[]>(() => api.templates(filterParams(labelFilter)), [labelKey]);
  const list = rawList ? favoritesFirst(rawList) : rawList;
  const patchLabels = (l: EntityLabels) => {
    if (!rawList) return;
    const next = rawList.map((t) => (t.id === l.entity_id ? { ...t, tags: l.tags, favorite: l.favorite } : t));
    setData(next.filter((t) => t.id === selected || matchesFilter(t, labelFilter)));
  };
  const [pdbId, setPdbId] = useState("");
  const [format, setFormat] = useState("pdb");
  const [busy, setBusy] = useState(false);
  const [accession, setAccession] = useState("");
  const accessionOk = isUniProtAccession(accession) || isAlphaFoldId(accession);
  useEffect(() => {
    if (!selected && list?.length) setParams(labelKey ? { id: list[0].id, tag: labelKey } : { id: list[0].id }, { replace: true });
  }, [list, selected, setParams, labelKey]);

  const fetchPdb = async () => {
    setBusy(true);
    try {
      const t = await api.fetchTemplate(pdbId.trim(), format);
      toast.push(t.cached ? `${t.id} era già in libreria` : `${t.id} scaricato (${t.format.toUpperCase()})`);
      setPdbId("");
      reload();
      selectTemplate(t.id);
    } catch (e) {
      toast.error(e);
    } finally {
      setBusy(false);
    }
  };
  const fetchAlphaFold = async () => {
    setBusy(true);
    try {
      const t = await api.fetchAlphaFold(normalizeAccession(accession));
      toast.push(t.cached ? `${t.id} era già in libreria` : `${t.id} scaricato da AlphaFold DB (${plddtLabel(t.plddt_mean)})`);
      setAccession("");
      reload();
      selectTemplate(t.id);
    } catch (e) {
      toast.error(e);
    } finally {
      setBusy(false);
    }
  };
  const upload = async (file: File) => {
    setBusy(true);
    try {
      const t = await api.uploadTemplate(file);
      toast.push(`${t.id} caricato`);
      reload();
      selectTemplate(t.id);
    } catch (e) {
      toast.error(e);
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="page">
      <PageHead title="Libreria template" lead="Strutture sperimentali, modelli predetti di AlphaFold DB o file personalizzati disponibili per tutti i progetti. Ogni template è analizzato: catene, residui mancanti, ligandi e struttura secondaria." />
      <div className="split">
        <div className="stack">
          <Card title="Aggiungi template">
            <div className="stack">
              <div className="row">
                <input className="input" style={{ flex: 1, textTransform: "uppercase" }} placeholder="Codice PDB (es. 1UBQ)" maxLength={4} value={pdbId} onChange={(e) => setPdbId(e.target.value)} onKeyDown={(e) => e.key === "Enter" && pdbId.length === 4 && fetchPdb()} />
                <select className="input" style={{ width: 90 }} value={format} onChange={(e) => setFormat(e.target.value)}>
                  <option value="pdb">PDB</option>
                  <option value="cif">mmCIF</option>
                </select>
                <button className="btn btn-primary" disabled={pdbId.trim().length !== 4 || busy} onClick={fetchPdb}>{busy ? <Spinner /> : <Icon name="download" />} Scarica</button>
              </div>
              <div className="stack-sm">
                <span className="small text-2" style={{ fontWeight: 550 }}>Da AlphaFold DB (UniProt)</span>
                <div className="row">
                  <input className="input" style={{ flex: 1, textTransform: "uppercase" }} placeholder="Accessione UniProt (es. P69905)" maxLength={40} value={accession} onChange={(e) => setAccession(e.target.value)} onKeyDown={(e) => e.key === "Enter" && accessionOk && !busy && fetchAlphaFold()} />
                  <button className="btn" disabled={!accessionOk || busy} onClick={fetchAlphaFold}>{busy ? <Spinner /> : <Icon name="download" />} Scarica</button>
                </div>
                <span className="tiny muted">
                  Modello predetto (non una struttura sperimentale), con il pLDDT nella colonna B-factor. L'accessione viene inviata a EBI.
                  {accession.trim() && !accessionOk && <span style={{ color: "var(--err)" }}> Accessione non valida (formato UniProtKB, oppure AF-P69905-F1).</span>}
                </span>
              </div>
              <DropZone accept=".pdb,.ent,.cif,.mmcif" onFile={upload} label={<>Trascina un file <b>.pdb</b> o <b>.cif</b> oppure fai clic (max 50 MB)</>} />
            </div>
          </Card>
          <Card title="Template in libreria" sub={list ? String(list.length) : ""} right={<LabelFilterSelect type="template" value={labelFilter} onChange={setLabelFilter} width={150} />}>
            {loading && !list ? <Spinner /> : !list?.length ? (
              labelFilter.kind !== "all"
                ? <Empty icon="tag" title="Nessun template con questo filtro" />
                : <Empty icon="library" title="Libreria vuota">Scarica un template da RCSB o dai risultati della ricerca.</Empty>
            ) : (
              <div className="table-wrap" style={{ maxHeight: 520, overflowY: "auto" }}>
                <table className="table">
                  <tbody>
                    {list.map((t) => (
                      <tr key={t.id} className={`clickable ${t.id === selected ? "selected" : ""}`} onClick={() => selectTemplate(t.id)}>
                        <td>
                          <FavoriteStar type="template" id={t.id} value={!!t.favorite} onChange={patchLabels} size={13} />{" "}
                          <b>{t.id}</b> <span className="tiny muted">{t.format.toUpperCase()}</span>
                          {t.predicted && <> <Badge tone="warn" title={predictorTitle(t)}>{predictorBadge(t)}</Badge></>}
                          {t.assembly && <> <Badge tone="info" title={`Assemblaggio biologico ${t.assembly.assembly_id} di ${t.assembly.source_id}`}>assemblaggio</Badge></>}
                          <div className="tiny muted ellipsis" style={{ maxWidth: 230 }}>{t.title || t.source}</div>
                          <TagChips tags={t.tags} max={4} onPick={(tag) => setLabelFilter({ kind: "tag", tag })} />
                        </td>
                        <td className="num small nowrap">
                          {t.predicted
                            ? <span title="pLDDT medio (confidenza della predizione)"><Badge tone={plddtTone(t.plddt_mean)}>pLDDT {fmtNum(t.plddt_mean ?? null, 1)}</Badge></span>
                            : t.resolution ? `${fmtNum(t.resolution, 2)} Å` : "—"}
                          <div className="tiny muted">{fmtBytes(t.size)} · {relTime(t.added_at)}</div>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </Card>
        </div>
        <div>{selected ? <TemplateDetail key={selected} id={selected} labels={itemLabels(list?.find((t) => t.id === selected))} onDeleted={() => { setParams(labelKey ? { tag: labelKey } : {}); reload(); }} onAdded={(id) => { reload(); setParams({ id }); }} onLabels={patchLabels} /> : <Card><Empty icon="library" title="Seleziona un template" /></Card>}</div>
      </div>
    </div>
  );
}
