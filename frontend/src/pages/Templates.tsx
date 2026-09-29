import { useEffect, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { api } from "../api/client";
import type { TemplateAnalysis, TemplateItem } from "../api/types";
import { SSStrip } from "../components/charts";
import { Icon } from "../components/Icon";
import { Viewer3D } from "../components/Viewer3D";
import { Alert, Badge, Card, Confirm, DropZone, Empty, PageHead, Spinner, Stat } from "../components/ui";
import { useToast } from "../lib/context";
import { fmtBytes, fmtNum, relTime } from "../lib/format";
import { useAsync } from "../lib/hooks";

function TemplateDetail({ id, onDeleted }: { id: string; onDeleted: () => void }) {
  const navigate = useNavigate();
  const toast = useToast();
  const { data: a, error, loading } = useAsync<TemplateAnalysis>(() => api.template(id), [id]);
  const [confirm, setConfirm] = useState(false);
  if (loading && !a) return <Card><Spinner /></Card>;
  if (error) return <Alert tone="err">{error.message}</Alert>;
  if (!a) return null;
  const ligands = a.ligands.filter((l) => !l.additive);
  const additives = a.ligands.filter((l) => l.additive);
  return (
    <div className="stack">
      <Card
        title={a.id}
        sub={a.header.title}
        right={
          <>
            <button className="btn btn-sm" onClick={() => navigate(`/quality?template=${a.id}`)}><Icon name="shield" size={14} /> Qualità</button>
            <button className="btn btn-sm btn-primary" onClick={() => navigate(`/modeling?t=${a.id}:${a.chains[0]?.chain ?? "A"}`)}><Icon name="cube" size={14} /> Usa come template</button>
            <a className="btn btn-sm btn-ghost" href={api.templateFileUrl(a.id)} download={`${a.id}.${a.format}`}><Icon name="download" size={14} /></a>
            <button className="btn btn-sm btn-danger" onClick={() => setConfirm(true)}><Icon name="trash" size={14} /></button>
          </>
        }
      >
        <div className="grid grid-4">
          <Stat label="Metodo" value={<span style={{ fontSize: 14 }}>{a.header.method || "—"}</span>} />
          <Stat label="Risoluzione" value={a.header.resolution ? <>{fmtNum(a.header.resolution, 2)}<small> Å</small></> : "—"} />
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
      {(ligands.length > 0 || additives.length > 0) && (
        <Card title="Ligandi ed eteroatomi" sub="Selezionabili come HETATM nella modellazione">
          <div className="row">
            {ligands.map((l, i) => <Badge key={i} tone="accent" title={`${l.atoms} atomi`}>{l.resname} {l.chain}{l.resnum}</Badge>)}
            {additives.map((l, i) => <Badge key={`x${i}`} title="Additivo di cristallizzazione">{l.resname} {l.chain}{l.resnum}</Badge>)}
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
  const { data: list, reload, loading } = useAsync<TemplateItem[]>(() => api.templates(), []);
  const [pdbId, setPdbId] = useState("");
  const [format, setFormat] = useState("pdb");
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    if (!selected && list?.length) setParams({ id: list[0].id }, { replace: true });
  }, [list, selected, setParams]);

  const fetchPdb = async () => {
    setBusy(true);
    try {
      const t = await api.fetchTemplate(pdbId.trim(), format);
      toast.push(t.cached ? `${t.id} era già in libreria` : `${t.id} scaricato (${t.format.toUpperCase()})`);
      setPdbId("");
      reload();
      setParams({ id: t.id });
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
      setParams({ id: t.id });
    } catch (e) {
      toast.error(e);
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="page">
      <PageHead title="Libreria template" lead="Strutture sperimentali o personalizzate disponibili per tutti i progetti. Ogni template è analizzato: catene, residui mancanti, ligandi e struttura secondaria." />
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
              <DropZone accept=".pdb,.ent,.cif,.mmcif" onFile={upload} label={<>Trascina un file <b>.pdb</b> o <b>.cif</b> oppure fai clic (max 50 MB)</>} />
            </div>
          </Card>
          <Card title="Template in libreria" sub={list ? String(list.length) : ""}>
            {loading && !list ? <Spinner /> : !list?.length ? (
              <Empty icon="library" title="Libreria vuota">Scarica un template da RCSB o dai risultati della ricerca.</Empty>
            ) : (
              <div className="table-wrap" style={{ maxHeight: 520, overflowY: "auto" }}>
                <table className="table">
                  <tbody>
                    {list.map((t) => (
                      <tr key={t.id} className={`clickable ${t.id === selected ? "selected" : ""}`} onClick={() => setParams({ id: t.id })}>
                        <td>
                          <b>{t.id}</b> <span className="tiny muted">{t.format.toUpperCase()}</span>
                          <div className="tiny muted ellipsis" style={{ maxWidth: 230 }}>{t.title || t.source}</div>
                        </td>
                        <td className="num small nowrap">
                          {t.resolution ? `${fmtNum(t.resolution, 2)} Å` : "—"}
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
        <div>{selected ? <TemplateDetail key={selected} id={selected} onDeleted={() => { setParams({}); reload(); }} /> : <Card><Empty icon="library" title="Seleziona un template" /></Card>}</div>
      </div>
    </div>
  );
}
