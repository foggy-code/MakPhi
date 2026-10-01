import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "../api/client";
import type { AssemblyList, TemplateItem } from "../api/types";
import { assemblyChainLabel, assemblyImportable, oligomerLabel } from "../lib/complex";
import { useToast } from "../lib/context";
import { useAsync } from "../lib/hooks";
import { Icon } from "./Icon";
import { Badge, Card, Spinner } from "./ui";

/**
 * Assemblaggi biologici di un template della libreria (F5.3): stato oligomerico, catene generate con la loro origine e
 * importazione come nuovo template `<ID>_ASM<n>` (coordinate generate con gli operatori di simmetria del file). Per un
 * template che è già un assemblaggio mostra da dove viene.
 */
export function AssembliesCard({ meta, onImported }: { meta: TemplateItem; onImported: (id: string) => void }) {
  const toast = useToast();
  const navigate = useNavigate();
  const { data, loading, error } = useAsync<AssemblyList>(() => api.templateAssemblies(meta.id), [meta.id]);
  const [busy, setBusy] = useState("");
  if (meta.assembly) {
    const a = meta.assembly;
    return (
      <Card title="Assemblaggio biologico" sub={`assemblaggio ${a.assembly_id} di ${a.source_id}`}
        right={<button className="btn btn-sm btn-primary" onClick={() => navigate(`/modeling?cmplx=${encodeURIComponent(meta.id)}`)}><Icon name="cube" size={14} /> Modella il complesso</button>}>
        <div className="stack-sm small text-2">
          <span>{oligomerLabel(a.oligomeric_details, a.chains.filter((c) => c.protein).length)} · {a.details === "author_defined_assembly" ? "definito dall'autore" : a.details === "software_defined_assembly" ? `definito dal programma${a.method ? ` ${a.method}` : ""}` : "origine non dichiarata"}</span>
          <span>Catene: {a.chains.map((c) => assemblyChainLabel(c)).join(", ")}</span>
        </div>
      </Card>
    );
  }
  if (loading && !data) return <Card title="Assemblaggi biologici"><Spinner /></Card>;
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
    <Card title="Assemblaggi biologici" sub={data.format === "mmcif" ? "pdbx_struct_assembly del mmCIF" : "REMARK 350 del file PDB"}>
      {!data.assemblies.length ? <p className="small muted">{data.note}</p> : (
        <div className="stack-sm">
          {data.assemblies.map((a) => (
            <div key={a.id} className="card flat" style={{ padding: "8px 10px" }}>
              <div className="row" style={{ flexWrap: "wrap" }}>
                <b>Assemblaggio {a.id}</b>
                <Badge tone="accent">{oligomerLabel(a.oligomeric_details, a.protein_chains)}</Badge>
                {a.identity_only && <Badge tone="info" title="Solo l'operatore identità: le catene sono quelle del file">come nel file</Badge>}
                <span className="tiny muted">{a.details === "author_defined_assembly" ? "definito dall'autore" : a.details === "software_defined_assembly" ? `definito dal programma${a.method ? ` ${a.method}` : ""}` : ""}</span>
                {assemblyImportable(a) && (
                  <button className="btn btn-sm right" disabled={!!busy} onClick={() => importOne(a.id)} title="Crea il template con le coordinate dell'assemblaggio (operatori di simmetria applicati)">
                    {busy === a.id ? <Spinner /> : <Icon name="layers" size={13} />} Importa come template
                  </button>
                )}
              </div>
              <div className="small text-2" style={{ marginTop: 4 }}>Catene: {a.chains.map((c) => assemblyChainLabel(c)).join(", ") || "—"}</div>
              {a.note && <div className="tiny" style={{ color: "var(--warn)" }}>{a.note}</div>}
            </div>
          ))}
        </div>
      )}
      <p className="tiny muted" style={{ marginTop: 8 }}>
        L'unità asimmetrica del cristallo non è sempre l'assemblaggio biologico: per modellare un omo-oligomero importa l'assemblaggio e usalo nella modalità Complesso della Modellazione. Le catene copiate ricevono nuovi nomi (A–Z, a–z, 0–9), con l'origine indicata.
      </p>
    </Card>
  );
}
