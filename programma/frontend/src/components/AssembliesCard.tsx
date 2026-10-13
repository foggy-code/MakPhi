import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "../api/client";
import type { AssemblyChain, AssemblyList, AssemblySummary, ChainComposition, TemplateItem } from "../api/types";
import { assemblyChainLabel, assemblyComposition, assemblyImportable, compositionLabel, oligomerLabel } from "../lib/complex";
import { useToast } from "../lib/context";
import { useAsync } from "../lib/hooks";
import { Icon } from "./Icon";
import { Badge, Card, DetailGrid, Disclosure, Spinner } from "./ui";
import "../styles/tools.css";

/** Oltre queste catene l'elenco va in una sezione a scomparsa (capsidi, filamenti). */
const CHAINS_INLINE = 12;

function originLabel(details: string, method: string): string {
  if (details === "author_defined_assembly") return "definito dall'autore";
  if (details === "software_defined_assembly") return `definito dal programma${method ? ` ${method}` : ""}`;
  return "origine non dichiarata";
}

/** Catene come chip: nome breve ("B=A·2"), per esteso nel suggerimento; le catene non proteiche attenuate. */
function ChainChips({ chains }: { chains: AssemblyChain[] }) {
  if (!chains.length) return <span className="small text-2">—</span>;
  const chips = (
    <div className="tools-chips">
      {chains.map((c) => (
        <span key={c.chain} className="chip mono" title={`${assemblyChainLabel(c)}${c.protein ? "" : " (non proteica)"}`}>
          {assemblyChainLabel(c, true)}{!c.protein && <span className="text-2"> · non proteica</span>}
        </span>
      ))}
    </div>
  );
  return chains.length > CHAINS_INLINE ? <Disclosure title="Catene generate" extra={`${chains.length} catene`}>{chips}</Disclosure> : chips;
}

/** Un assemblaggio dichiarato nel file: stato oligomerico, origine, generatori, catene e importazione. */
function AssemblyItem({ a, composition, busy, onImport }: { a: AssemblySummary; composition?: ChainComposition; busy: string; onImport: (id: string) => void }) {
  const comp = assemblyComposition(a, composition);
  return (
    <div className="card flat stack-sm" style={{ padding: "10px 12px" }}>
      <div className="row" style={{ flexWrap: "wrap" }}>
        <b>Assemblaggio {a.id}</b>
        <Badge tone="accent">{oligomerLabel(a.oligomeric_details, a.protein_chains)}</Badge>
        {comp && comp.chains > 1 && (
          <Badge tone={comp.kind === "hetero" ? "warn" : "info"} title="Dalle entità delle catene d'origine (card Catene); stechiometria come in RCSB">
            {compositionLabel(comp.kind, comp.chains)} · {comp.stoichiometry}
          </Badge>
        )}
        {a.identity_only && <Badge tone="info" title="Solo l'operatore identità: le catene sono quelle del file">come nel file</Badge>}
        <span className="small text-2">{originLabel(a.details, a.method)}</span>
        {assemblyImportable(a) && (
          <button type="button" className="btn btn-sm right" disabled={!!busy} onClick={() => onImport(a.id)} title="Crea il template con le coordinate dell'assemblaggio (operatori di simmetria applicati)">
            {busy === a.id ? <Spinner /> : <Icon name="layers" size={13} />} Importa come template
          </button>
        )}
      </div>
      <DetailGrid items={[
        { label: "Catene", value: a.chains.length ? `${a.chain_count} (${a.protein_chains} proteiche)` : "—" },
        { label: "Catene del file", value: a.source_chains?.length ? a.source_chains.join(", ") : "—" },
        { label: "Atomi", value: a.atoms != null ? a.atoms.toLocaleString("it-IT") : "—" },
        { label: "Generatori", value: a.generators.length ? a.generators.map((g) => `${g.chains.join(", ")} × ${g.operators}`).join("; ") : "—" },
      ]} />
      <ChainChips chains={a.chains} />
      {a.note && <span className="small" style={{ color: "var(--warn)" }}>{a.note}</span>}
    </div>
  );
}

/**
 * Assemblaggi biologici di un template della libreria (F5.3): stato oligomerico, catene generate con la loro origine e
 * importazione come nuovo template `<ID>_ASM<n>` (coordinate generate con gli operatori di simmetria del file). Per un
 * template che è già un assemblaggio mostra da dove viene.
 */
export function AssembliesCard({ meta, composition, onImported }: { meta: TemplateItem; composition?: ChainComposition; onImported: (id: string) => void }) {
  const toast = useToast();
  const navigate = useNavigate();
  const { data, loading, error } = useAsync<AssemblyList>(() => api.templateAssemblies(meta.id), [meta.id]);
  const [busy, setBusy] = useState("");
  if (meta.assembly) {
    const a = meta.assembly;
    return (
      <Card icon="layers" title="Assemblaggio biologico" sub={`assemblaggio ${a.assembly_id} di ${a.source_id}`}
        right={<button type="button" className="btn btn-sm btn-primary" onClick={() => navigate(`/modeling?cmplx=${encodeURIComponent(meta.id)}`)}><Icon name="cube" size={14} /> Modella il complesso</button>}>
        <div className="stack-sm small">
          <span className="text-2">{oligomerLabel(a.oligomeric_details, a.chains.filter((c) => c.protein).length)} · {originLabel(a.details, a.method)}</span>
          <ChainChips chains={a.chains} />
        </div>
      </Card>
    );
  }
  if (loading && !data) return <Card icon="layers" title="Assemblaggi biologici"><Spinner /></Card>;
  if (error || !data || (!data.assemblies.length && !data.note)) return null;
  const importOne = async (assemblyId: string) => {
    setBusy(assemblyId);
    try {
      const t = await api.importAssembly(meta.id, assemblyId);
      toast.push(t.cached ? `${t.id} era già in libreria` : `Assemblaggio ${assemblyId} importato come ${t.id}`);
      onImported(t.id);
    } catch (e) {
      toast.error(e);
    } finally {
      setBusy("");
    }
  };
  return (
    <Card icon="layers" title="Assemblaggi biologici" sub={data.format === "mmcif" ? "pdbx_struct_assembly del mmCIF" : "REMARK 350 del file PDB"}>
      {!data.assemblies.length ? <p className="small text-2">{data.note}</p> : (
        <div className="stack-sm">
          {data.assemblies.map((a) => <AssemblyItem key={a.id} a={a} composition={composition} busy={busy} onImport={importOne} />)}
        </div>
      )}
      <p className="small text-2" style={{ marginTop: 10 }}>
        L'unità asimmetrica del cristallo non è sempre l'assemblaggio biologico: per modellare un omo-oligomero importa l'assemblaggio e usalo nella modalità Complesso della Modellazione. Le catene copiate ricevono nuovi nomi (A–Z, a–z, 0–9), con l'origine indicata.
      </p>
    </Card>
  );
}
