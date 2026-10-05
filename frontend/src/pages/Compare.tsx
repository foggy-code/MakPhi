import { useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { api } from "../api/client";
import type { AlignResult, StructureSource, SystemInfo } from "../api/types";
import { CachedBadge, RecomputeOption } from "../components/CacheOption";
import { Icon } from "../components/Icon";
import { JobPanel } from "../components/JobPanel";
import { MetricGuide } from "../components/MetricNotes";
import { StructurePicker } from "../components/Pickers";
import { StructuralComparison } from "../components/StructuralComparison";
import { Alert, Card, Disclosure, Empty, Field, PageHead } from "../components/ui";
import { compareJobView, compareParams, jobSources, linkedSources } from "../lib/compare";
import { useProject, useToast } from "../lib/context";
import { useAsync, useJob } from "../lib/hooks";

/** Scelta automatica e programma integrato (i programmi esterni prendono il nome dalle informazioni di sistema). */
const TOOL_LABEL: Record<string, string> = { auto: "Automatico", builtin: "Kabsch + TM-score integrato" };

/** La catena resta nel suo campo: il selettore della struttura non la mostra. */
const withoutChain = (s: StructureSource | null): StructureSource | null => {
  if (!s) return null;
  const rest = { ...s };
  delete rest.chain;
  return rest;
};

/**
 * Confronto di due strutture: B sovrapposta su A (riferimento) con TM-align/US-align o Kabsch integrato, metriche
 * globali (TM-score, RMSD, GDT, lDDT) e deviazione per residuo. Le strutture arrivano anche dai link
 * (?a=template:1UBQ&ca=A&b=job:<id>:<file>), un allineamento già calcolato da ?job=.
 */
export default function Compare() {
  const { projectId } = useProject();
  const toast = useToast();
  const [params, setParams] = useSearchParams();
  const jobId = params.get("job");
  const [a, setA] = useState<StructureSource | null>(null);
  const [b, setB] = useState<StructureSource | null>(null);
  const [chainA, setChainA] = useState("");
  const [chainB, setChainB] = useState("");
  const [tool, setTool] = useState("auto");
  const [recompute, setRecompute] = useState(false);
  const { data: info } = useAsync<SystemInfo>(() => api.systemInfo(), []);
  const { job, summary, log, cancel, error } = useJob<AlignResult>(jobId, { withLog: true });
  // job del link eliminato o inesistente: errore e invito a rilanciare, invece di una colonna vuota
  const view = compareJobView(jobId, !!summary, error);

  // strutture dal link (Modellazione, Qualità, Allineamenti): precompilate, il confronto parte con «Confronta»
  const linkKey = ["a", "ca", "b", "cb"].map((k) => params.get(k) ?? "").join("|");
  useEffect(() => {
    const linked = linkedSources(params);
    if (linked.a) {
      setA(withoutChain(linked.a));
      setChainA(linked.a.chain ?? "");
    }
    if (linked.b) {
      setB(withoutChain(linked.b));
      setChainB(linked.b.chain ?? "");
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [linkKey]);

  // un job aperto senza strutture nel link (elenco dei job): le sue strutture diventano la scelta corrente
  const fromJob = jobSources(job?.params);
  useEffect(() => {
    if (!fromJob || params.get("a") || params.get("b")) return;
    setA(withoutChain(fromJob[0]));
    setChainA(fromJob[0].chain ?? "");
    setB(withoutChain(fromJob[1]));
    setChainB(fromJob[1].chain ?? "");
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [job?.id]);

  const notStructural = job && job.kind === "align" && job.params?.mode !== "structural";
  const submit = async () => {
    if (!a || !b) return;
    try {
      const sa: StructureSource = { ...a, chain: chainA || undefined };
      const sb: StructureSource = { ...b, chain: chainB || undefined };
      const j = await api.structuralAlign({ project_id: projectId ?? undefined, structures: [sa, sb], tool, use_cache: !recompute });
      setParams(compareParams(sa, sb, j.id));
    } catch (e) {
      toast.error(e);
    }
  };
  const swap = () => {
    setA(b);
    setB(a);
    setChainA(chainB);
    setChainB(chainA);
  };
  const toolAvailable = (key: string) => info?.tools[key]?.available;
  const toolLabel = TOOL_LABEL[tool] ?? info?.tools[tool]?.label ?? tool;
  const r = job?.status === "completed" && job.kind === "align" ? job.result : null;

  return (
    <div className="page">
      <PageHead
        title="Confronto strutturale"
        lead="Sovrapposizione di due strutture (modello, template, modello predetto) con TM-score, RMSD, GDT-TS/GDT-HA e lDDT, e la deviazione di ogni residuo. A è il riferimento: GDT e lDDT sono misurati sui suoi residui."
      />
      <div className="split">
        <div className="stack">
          <Card title="Strutture" icon="layers" sub="A è il riferimento, B si sovrappone su A">
            <div className="stack">
              <div className="cmp-slot">
                <StructurePicker label="A · riferimento (es. template o struttura sperimentale)" value={a} onChange={setA} />
                <Field label="Catena di A" hint="Vuoto = prima catena"><input className="input" maxLength={2} value={chainA} onChange={(e) => setChainA(e.target.value.trim())} /></Field>
              </div>
              <div className="row" style={{ justifyContent: "center" }}>
                <button type="button" className="btn btn-sm btn-ghost" onClick={swap} disabled={!a && !b} title="Scambia A e B"><Icon name="compare" size={14} /> Scambia A e B</button>
              </div>
              <div className="cmp-slot">
                <StructurePicker label="B · da sovrapporre (es. modello)" value={b} onChange={setB} />
                <Field label="Catena di B" hint="Vuoto = prima catena"><input className="input" maxLength={2} value={chainB} onChange={(e) => setChainB(e.target.value.trim())} /></Field>
              </div>
              {/* riepilogo da chiusa: programma e ricalcolo senza cache, se attivo (vale per ogni confronto successivo) */}
              <Disclosure title="Metodo e opzioni" extra={[toolLabel, recompute && "ricalcolo senza cache"].filter(Boolean).join(" · ")}>
                <div className="stack-sm">
                  <Field label="Programma" hint="TM-align/US-align allineano per struttura; l'integrato allinea per sequenza e poi sovrappone.">
                    <select className="input" value={tool} onChange={(e) => setTool(e.target.value)}>
                      <option value="auto">{TOOL_LABEL.auto}</option>
                      <option value="builtin">{TOOL_LABEL.builtin}</option>
                      {["usalign", "tmalign"].map((t) => (
                        <option key={t} value={t} disabled={!toolAvailable(t)}>{info?.tools[t]?.label ?? t}{toolAvailable(t) ? "" : " (non installato)"}</option>
                      ))}
                    </select>
                  </Field>
                  <RecomputeOption checked={recompute} onChange={setRecompute} />
                </div>
              </Disclosure>
              <button type="button" className="btn btn-primary" disabled={!a || !b} onClick={submit}><Icon name="play" size={14} /> Confronta</button>
            </div>
          </Card>
          <Card title="Come leggere le metriche" icon="info" collapsible storageKey="compare.guide">
            <div className="stack-sm">
              <MetricGuide metrics={["tm", "rmsd", "gdt", "lddt"]} open="tm" />
              <p className="tiny text-2">Ogni statistica del risultato ha i suoi «Dettagli»: valori grezzi e soglie. Metodi e limiti in docs/ARCHITETTURA.md, sezione «Confronto strutturale».</p>
            </div>
          </Card>
        </div>
        <div className="stack">
          {view === "missing" && <Alert tone="err">Confronto non disponibile: {error}</Alert>}
          {view === "job" ? (
            <JobPanel summary={summary} log={log} onCancel={cancel} />
          ) : view === "missing" ? (
            <Card>
              <Empty icon="layers" title="Avvia di nuovo il confronto">
                Il job del link non si può aprire (eliminato, link non valido o server non raggiungibile).{" "}
                {a || b ? "Le strutture del link restano scelte: rilancia con «Confronta»." : "Scegli le due strutture e rilancia con «Confronta»."}
              </Empty>
            </Card>
          ) : (
            <Card><Empty icon="layers" title="Scegli le due strutture e avvia il confronto">Da Modellazione (tabella dei modelli) e da Qualità le strutture arrivano già scelte.</Empty></Card>
          )}
          {notStructural && <Alert tone="warn">Il job indicato non è un allineamento strutturale: <Link to={`/alignment?job=${job!.id}`}>aprilo in Allineamenti</Link>.</Alert>}
          {r && r.mode === "structural" && (
            <>
              {r.cached && <div className="row"><CachedBadge result={r} /></div>}
              <StructuralComparison key={job!.id} jobId={job!.id} result={r} />
            </>
          )}
        </div>
      </div>
    </div>
  );
}
