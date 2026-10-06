import { Fragment, useEffect, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { api } from "../api/client";
import type { ChainSummary, EntityLabels, TemplateAnalysis, TemplateItem } from "../api/types";
import { AssembliesCard } from "../components/AssembliesCard";
import { SSStrip } from "../components/charts";
import { Icon } from "../components/Icon";
import { FavoriteStar, LabelEditor, LabelFilterSelect, TagChips } from "../components/Labels";
import { Viewer3D } from "../components/Viewer3D";
import { Alert, Badge, Card, Confirm, DetailGrid, Disclosure, DropZone, Empty, ExpandAll, ExpandButton, PageHead, Spinner, Stat, useExpanded } from "../components/ui";
import { FormSection } from "../components/WorkflowParts";
import {
  isAlphaFoldId, isUniProtAccession, normalizeAccession, PLDDT_BAND_LABEL, PLDDT_BAND_TONE, plddtLabel, plddtTone, predictorBadge, predictorTitle, type PlddtBand,
} from "../lib/alphafold";
import { useToast } from "../lib/context";
import { fmtBytes, fmtDate, fmtInt, fmtNum, relTime } from "../lib/format";
import { useAsync } from "../lib/hooks";
import { CLASS_LABEL, CLASS_TONE, groupClass, groupTitle, isDefault, isTransferable, ligandKey } from "../lib/ligands";
import { favoritesFirst, filterParams, filterValue, itemLabels, matchesFilter, parseFilterValue, type ItemLabels, type LabelFilter } from "../lib/tags";
import { numberedSequence, residueRanges, resolutionNote, resolutionTone, ssParts, templateMatches } from "../lib/workflowView";

/** colonne delle tabelle con righe espandibili (riga di dettaglio) */
const LIBRARY_COLUMNS = 3;
const CHAIN_COLUMNS = 7;

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
        <span className="small">
          Il pLDDT sta nella colonna B-factor. In modellazione i residui con pLDDT &lt; 70 (spesso regioni disordinate) vengono tolti dal template, salvo diversa scelta.
          {af && <> <a href={`https://alphafold.ebi.ac.uk/entry/${encodeURIComponent(af.accession)}`} target="_blank" rel="noreferrer">Scheda su AlphaFold DB</a></>}
        </span>
      </div>
    </Alert>
  );
}

/** Dettaglio di una catena: numerazione, interruzioni, residui mancanti, struttura secondaria e sequenza osservata. */
function ChainDetail({ c, analysis }: { c: ChainSummary; analysis: TemplateAnalysis }) {
  const missing = analysis.header.missing_residues.filter((m) => m.chain === c.chain);
  const seqres = analysis.header.seqres[c.chain];
  const ss = c.ss ? ssParts(c.ss) : null;
  return (
    <div className="stack-sm">
      <DetailGrid items={[
        { label: "Numerazione", value: `${c.first}–${c.last}` },
        { label: "Residui osservati", value: fmtInt(c.residues) },
        { label: "SEQRES", value: seqres ? `${fmtInt(seqres.length)} residui` : "—" },
        { label: "Mancanti (REMARK 465)", value: missing.length ? residueRanges(missing) : "nessuno" },
        { label: "Interruzioni (C–N > 2 Å)", value: c.breaks.length ? c.breaks.map((b) => `${b.after}→${b.before}`).join(", ") : "nessuna" },
        { label: "Gruppi eteroatomici", value: c.ligands },
        ...(ss ? ss.map((p) => ({ label: `${p.label} (DSSP)`, value: `${fmtNum(p.value, 1)}%` })) : []),
      ]} />
      {c.sequence && <pre className="seq-block wf-numbered" aria-label={`Sequenza osservata della catena ${c.chain}`}>{numberedSequence(c.sequence)}</pre>}
    </div>
  );
}

function TemplateDetail({ id, labels, onDeleted, onAdded, onLabels }: { id: string; labels?: ItemLabels; onDeleted: () => void; onAdded: (id: string) => void; onLabels: (l: EntityLabels) => void }) {
  const navigate = useNavigate();
  const toast = useToast();
  const { data: a, error, loading } = useAsync<TemplateAnalysis>(() => api.template(id), [id]);
  const [confirm, setConfirm] = useState(false);
  const open = useExpanded<string>();
  if (loading && !a) return <Card><p className="small text-2 row" style={{ gap: 8 }}><Spinner /> Analisi del template…</p></Card>;
  if (error) return <Alert tone="err">{error.message}</Alert>;
  if (!a) return null;
  const groups = [...a.ligands.filter((l) => !l.additive), ...a.ligands.filter((l) => l.additive)];
  const predicted = !!a.meta.predicted;
  const missing = a.header.missing_residues;
  const resolution = a.header.resolution;
  const allOpen = a.chains.length > 0 && a.chains.every((c) => open.has(c.chain));
  const helices = a.header.secondary_structure.filter((s) => s.type === "helix").length;
  return (
    <div className="stack">
      {predicted && <PredictedNote meta={a.meta} />}
      <Card
        title={<>{a.id} {predicted && <Badge tone="warn" title={predictorTitle(a.meta)}>{predictorBadge(a.meta)}</Badge>}</>}
        icon="cube"
        sub={a.header.title}
        right={
          <>
            <button className="btn btn-sm" onClick={() => navigate(`/quality?template=${a.id}`)}><Icon name="shield" size={14} /> Qualità</button>
            <button className="btn btn-sm btn-primary" onClick={() => navigate(`/modeling?t=${a.id}:${a.chains[0]?.chain ?? "A"}`)}><Icon name="cube" size={14} /> Usa come template</button>
            {a.chains.length > 1 && <button className="btn btn-sm" title="Modalità Complesso: una sequenza del bersaglio per ogni catena" onClick={() => navigate(`/modeling?cmplx=${encodeURIComponent(a.id)}`)}><Icon name="layers" size={14} /> Complesso</button>}
            <a className="btn btn-sm btn-ghost" href={api.templateFileUrl(a.id)} download={`${a.id}.${a.format}`} title="Scarica il file" aria-label={`Scarica ${a.id}.${a.format}`}><Icon name="download" size={14} /></a>
            <button className="btn btn-sm btn-danger" onClick={() => setConfirm(true)} title="Rimuovi dalla libreria" aria-label={`Rimuovi ${a.id} dalla libreria`}><Icon name="trash" size={14} /></button>
          </>
        }
      >
        <div className="stack">
          <LabelEditor type="template" id={a.id} initial={labels} onChange={onLabels} />
          <div className="grid grid-4">
            <Stat label="Metodo" value={<span style={{ fontSize: 14 }}>{a.header.method || a.meta.method || "—"}</span>} note={a.models > 1 ? `${a.models} modelli nel file (usato il primo)` : undefined} />
            {predicted
              ? <Stat label="pLDDT medio" value={fmtNum(a.meta.plddt_mean ?? null, 1)} note="confidenza della predizione (0–100)" tone={plddtTone(a.meta.plddt_mean)}
                  detail="Fasce di AlphaFold DB: ≥ 90 molto alta, 70–90 confidente, 50–70 bassa, < 50 molto bassa (spesso regioni disordinate)." />
              : <Stat label="Risoluzione" value={resolution ? <>{fmtNum(resolution, 2)}<small> Å</small></> : "—"} tone={resolutionTone(resolution)}
                  detail={<>{resolutionNote(resolution)} Le stesse fasce del punteggio dei template (Wlodawer et al. 2008).</>} />}
            <Stat label="Residui" value={fmtInt(a.stats.residues)} note={`${a.stats.chains} catene · ${fmtInt(a.stats.heavy_atoms)} atomi`}
              detail={a.chains.map((c) => <span key={c.chain}>Catena {c.chain}: {fmtInt(c.residues)} residui ({c.first}–{c.last})</span>)} />
            <Stat label="Residui mancanti" value={missing.length} note="REMARK 465" tone={missing.length ? "warn" : "ok"}
              detail={missing.length ? <><span>{residueRanges(missing)}</span><span>Diventano gap nell'allineamento: MODELLER li ricostruisce come loop.</span></> : "Nessun residuo mancante dichiarato nell'intestazione."} />
          </div>
          <Disclosure title="Intestazione e file" extra={`${a.format.toUpperCase()} · ${fmtBytes(a.meta.size)}`}>
            <DetailGrid items={[
              { label: "Titolo", value: a.header.title || "—" },
              { label: "Metodo", value: a.header.method || a.meta.method || "—" },
              { label: "Risoluzione", value: resolution ? `${fmtNum(resolution, 2)} Å` : "—" },
              { label: "Formato", value: a.format.toUpperCase() },
              { label: "Dimensione", value: fmtBytes(a.meta.size) },
              { label: "Origine", value: a.meta.source || "—" },
              { label: "Aggiunto", value: a.meta.added_at ? `${fmtDate(a.meta.added_at)} (${relTime(a.meta.added_at)})` : "—" },
              { label: "Modelli nel file", value: a.models },
              { label: "Catene", value: a.chains.map((c) => c.chain).join(", ") || "—" },
              { label: "Atomi pesanti", value: fmtInt(a.stats.heavy_atoms) },
              { label: "Struttura secondaria dichiarata", value: a.header.secondary_structure.length ? `${helices} eliche · ${a.header.secondary_structure.length - helices} filamenti` : "—" },
              { label: "SEQRES", value: Object.keys(a.header.seqres).length ? Object.entries(a.header.seqres).map(([ch, s]) => `${ch}: ${s.length}`).join(" · ") : "—" },
            ]} />
          </Disclosure>
        </div>
      </Card>
      <Viewer3D url={api.templateFileUrl(a.id)} format={a.format === "cif" ? "cif" : "pdb"} />
      <Card title="Catene" icon="layers" sub="clic su una riga per numerazione, interruzioni e sequenza"
        right={a.chains.length > 1 && (
          <ExpandAll noun="catene" allOpen={allOpen} onExpand={() => open.expandAll(a.chains.map((c) => c.chain))} onCollapse={open.collapseAll} />
        )}>
        <div className="table-wrap">
          <table className="table">
            <thead>
              <tr>
                <th style={{ width: 34 }} aria-label="Dettagli" />
                <th>Catena</th><th className="num">Residui</th><th>Numerazione</th><th>Struttura secondaria (DSSP)</th><th className="num">Mancanti</th><th className="num">Interruzioni</th>
              </tr>
            </thead>
            <tbody>
              {a.chains.map((c) => {
                const isOpen = open.has(c.chain);
                return (
                  <Fragment key={c.chain}>
                    <tr className={`clickable${isOpen ? " row-open" : ""}`} onClick={() => open.toggle(c.chain)}>
                      <td><ExpandButton open={isOpen} onClick={() => open.toggle(c.chain)} label={`dettagli della catena ${c.chain}`} /></td>
                      <td><b>{c.chain}</b></td>
                      <td className="num">{c.residues}</td>
                      <td className="nowrap">{c.first}–{c.last}</td>
                      <td style={{ minWidth: 220 }}>{c.ss ? <SSStrip ss3={c.ss} /> : "—"}</td>
                      <td className="num">{c.missing ?? 0}</td>
                      <td className="num">{c.breaks.length ? <Badge tone="warn" title={c.breaks.map((b) => `${b.after}→${b.before}`).join(", ")}>{c.breaks.length}</Badge> : 0}</td>
                    </tr>
                    {isOpen && (
                      <tr className="row-detail">
                        <td colSpan={CHAIN_COLUMNS}><ChainDetail c={c} analysis={a} /></td>
                      </tr>
                    )}
                  </Fragment>
                );
              })}
            </tbody>
          </table>
        </div>
        <p className="small text-2" style={{ marginTop: 8 }}>
          Le interruzioni (distanza C–N &gt; 2 Å) e i residui mancanti diventano gap nell'allineamento: MODELLER li ricostruisce come loop.
        </p>
      </Card>
      {!predicted && <AssembliesCard meta={a.meta} onImported={onAdded} />}
      {groups.length > 0 && (
        <Card title="Ligandi ed eteroatomi" icon="flask" sub="Trasferibili nel modello (Modellazione → Trasferisci ligandi, cofattori e metalli)">
          <div className="table-wrap">
            <table className="table">
              <thead><tr><th>Gruppo</th><th>Classe</th><th className="num">Atomi</th><th>Nel modello</th><th>Note</th></tr></thead>
              <tbody>
                {groups.map((l) => {
                  const cls = groupClass(l);
                  return (
                    <tr key={ligandKey(a.id, l)}>
                      <td className="mono nowrap"><b>{groupTitle(l)}</b></td>
                      <td><Badge tone={CLASS_TONE[cls]}>{l.label ?? CLASS_LABEL[cls]}</Badge></td>
                      <td className="num">{l.atoms}</td>
                      <td className="small nowrap">{!isTransferable(l) ? "non trasferibile" : isDefault(l) ? "scelto per default" : "a scelta"}</td>
                      <td className="small">{[l.description, l.reason].filter(Boolean).join(" · ") || "—"}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
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

/** Tutti i metadati di un template della libreria (riga espansa). */
function LibraryRowDetail({ t }: { t: TemplateItem }) {
  const af = t.alphafold;
  const items: { label: string; value: string | number | null | undefined }[] = [
    { label: "Titolo", value: t.title },
    { label: "Metodo", value: t.method },
    t.predicted ? { label: "pLDDT medio", value: plddtLabel(t.plddt_mean) } : { label: "Risoluzione", value: t.resolution ? `${fmtNum(t.resolution, 2)} Å` : null },
    { label: "Catene", value: t.chains?.join(", ") },
    { label: "Formato", value: t.format.toUpperCase() },
    { label: "Dimensione", value: fmtBytes(t.size) },
    { label: "Origine", value: t.source },
    { label: "Aggiunto", value: t.added_at ? fmtDate(t.added_at) : null },
    { label: "Predittore", value: t.predicted ? predictorTitle(t) : null },
    { label: "UniProt", value: af ? `${af.accession}${af.uniprot_start && af.uniprot_end ? ` (residui ${af.uniprot_start}–${af.uniprot_end})` : ""}` : null },
    { label: "Gene e organismo", value: af ? [af.gene, af.organism].filter(Boolean).join(" · ") : null },
    { label: "Versione AlphaFold DB", value: af?.version ? `v${af.version}` : null },
    { label: "Assemblaggio biologico", value: t.assembly ? `${t.assembly.assembly_id} di ${t.assembly.source_id}${t.assembly.oligomeric_details ? ` · ${t.assembly.oligomeric_details}` : ""}` : null },
  ];
  return <DetailGrid items={items.filter((it) => it.value !== null && it.value !== undefined && it.value !== "")} />;
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
  const [query, setQuery] = useState("");
  const open = useExpanded<string>();
  const accessionOk = isUniProtAccession(accession) || isAlphaFoldId(accession);
  const shown = list?.filter((t) => templateMatches(t, query)) ?? [];
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
          <Card title="Aggiungi template" icon="download">
            <div className="stack">
              <FormSection title="Da RCSB PDB" hint="struttura sperimentale">
                <div className="row">
                  <input className="input" style={{ flex: 1, textTransform: "uppercase" }} placeholder="Codice PDB (es. 1UBQ)" aria-label="Codice PDB" maxLength={4} value={pdbId} onChange={(e) => setPdbId(e.target.value)} onKeyDown={(e) => e.key === "Enter" && pdbId.length === 4 && fetchPdb()} />
                  <select className="input" style={{ width: 90 }} value={format} onChange={(e) => setFormat(e.target.value)} aria-label="Formato del file">
                    <option value="pdb">PDB</option>
                    <option value="cif">mmCIF</option>
                  </select>
                  <button className="btn btn-primary" disabled={pdbId.trim().length !== 4 || busy} onClick={fetchPdb}>{busy ? <Spinner /> : <Icon name="download" />} Scarica</button>
                </div>
              </FormSection>
              <FormSection title="Da AlphaFold DB" hint="accessione UniProt">
                <div className="row">
                  <input className="input" style={{ flex: 1, textTransform: "uppercase" }} placeholder="Accessione UniProt (es. P69905)" aria-label="Accessione UniProt" maxLength={40} value={accession} onChange={(e) => setAccession(e.target.value)} onKeyDown={(e) => e.key === "Enter" && accessionOk && !busy && fetchAlphaFold()} />
                  <button className="btn" disabled={!accessionOk || busy} onClick={fetchAlphaFold}>{busy ? <Spinner /> : <Icon name="download" />} Scarica</button>
                </div>
                <span className="small text-2">
                  Modello predetto (non una struttura sperimentale), con il pLDDT nella colonna B-factor. L'accessione viene inviata a EBI.
                  {accession.trim() && !accessionOk && <span style={{ color: "var(--err)" }}> Accessione non valida (formato UniProtKB, oppure AF-P69905-F1).</span>}
                </span>
              </FormSection>
              <FormSection title="Da file">
                <DropZone accept=".pdb,.ent,.cif,.mmcif" onFile={upload} label={<>Trascina un file <b>.pdb</b> o <b>.cif</b> oppure fai clic (max 50 MB)</>} />
              </FormSection>
            </div>
          </Card>
          <Card title="Template in libreria" icon="library" sub={list ? (query ? `${shown.length} di ${list.length}` : String(list.length)) : ""}
            right={
              <>
                <input className="input input-sm wf-filter" style={{ width: 150 }} placeholder="Cerca…" aria-label="Cerca nella libreria" value={query} onChange={(e) => setQuery(e.target.value)} />
                <LabelFilterSelect type="template" value={labelFilter} onChange={setLabelFilter} width={150} />
              </>
            }>
            {loading && !list ? <div className="empty"><Spinner label="Caricamento della libreria" /></div> : !list?.length ? (
              labelFilter.kind !== "all"
                ? <Empty icon="tag" title="Nessun template con questo filtro" />
                : <Empty icon="library" title="Libreria vuota">Scarica un template da RCSB o dai risultati della ricerca.</Empty>
            ) : !shown.length ? (
              <Empty icon="search" title="Nessun template corrisponde alla ricerca">Cerca per codice, titolo, metodo, gene o etichetta.</Empty>
            ) : (
              <div className="table-wrap" style={{ maxHeight: 560, overflowY: "auto" }}>
                <table className="table">
                  <thead>
                    <tr><th style={{ width: 34 }} aria-label="Dettagli" /><th>Template</th><th className="num">Qualità</th></tr>
                  </thead>
                  <tbody>
                    {shown.map((t) => {
                      const isOpen = open.has(t.id);
                      return (
                        <Fragment key={t.id}>
                          <tr className={`clickable ${t.id === selected ? "selected" : ""}${isOpen ? " row-open" : ""}`} onClick={() => selectTemplate(t.id)}>
                            <td><ExpandButton open={isOpen} onClick={() => open.toggle(t.id)} label={`dettagli di ${t.id}`} /></td>
                            <td>
                              <FavoriteStar type="template" id={t.id} value={!!t.favorite} onChange={patchLabels} size={13} />{" "}
                              <b>{t.id}</b> <span className="tiny text-2">{t.format.toUpperCase()}</span>
                              {t.predicted && <> <Badge tone="warn" title={predictorTitle(t)}>{predictorBadge(t)}</Badge></>}
                              {t.assembly && <> <Badge tone="info" title={`Assemblaggio biologico ${t.assembly.assembly_id} di ${t.assembly.source_id}`}>assemblaggio</Badge></>}
                              <div className="tiny text-2 ellipsis" style={{ maxWidth: 230 }} title={t.title || t.source}>{t.title || t.source}</div>
                              <TagChips tags={t.tags} max={4} onPick={(tag) => setLabelFilter({ kind: "tag", tag })} />
                            </td>
                            <td className="num small nowrap">
                              {t.predicted
                                ? <span title="pLDDT medio (confidenza della predizione)"><Badge tone={plddtTone(t.plddt_mean)}>pLDDT {fmtNum(t.plddt_mean ?? null, 1)}</Badge></span>
                                : t.resolution ? <Badge tone={resolutionTone(t.resolution)} title={resolutionNote(t.resolution)}>{fmtNum(t.resolution, 2)} Å</Badge> : "—"}
                              <div className="tiny text-2">{fmtBytes(t.size)} · {relTime(t.added_at)}</div>
                            </td>
                          </tr>
                          {isOpen && (
                            <tr className="row-detail">
                              <td colSpan={LIBRARY_COLUMNS}><LibraryRowDetail t={t} /></td>
                            </tr>
                          )}
                        </Fragment>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            )}
          </Card>
        </div>
        <div>
          {selected ? (
            <TemplateDetail key={selected} id={selected} labels={itemLabels(list?.find((t) => t.id === selected))}
              onDeleted={() => { setParams(labelKey ? { tag: labelKey } : {}); reload(); }} onAdded={(id) => { reload(); setParams({ id }); }} onLabels={patchLabels} />
          ) : (
            <Card>
              <Empty icon="library" title="Seleziona un template">Catene, residui mancanti, ligandi, assemblaggi e vista 3D compaiono qui.</Empty>
            </Card>
          )}
        </div>
      </div>
    </div>
  );
}
