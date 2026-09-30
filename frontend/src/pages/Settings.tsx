import { useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { api } from "../api/client";
import type { Settings, SystemInfo } from "../api/types";
import { Icon } from "../components/Icon";
import { DatabasesPanel, LicensesPanel, StoragePanel, ToolsPanel } from "../components/SettingsPanels";
import { Alert, Card, Field, PageHead, Spinner, Tabs } from "../components/ui";
import { useToast } from "../lib/context";
import { notifySystemChanged } from "../lib/hooks";

type Tab = "general" | "licenses" | "databases" | "tools" | "storage";
const TABS: { value: Tab; label: string }[] = [
  { value: "general", label: "Generale" },
  { value: "licenses", label: "Licenze" },
  { value: "databases", label: "Database" },
  { value: "tools", label: "Strumenti" },
  { value: "storage", label: "Spazio e file temporanei" },
];

export default function SettingsPage() {
  const toast = useToast();
  const [params, setParams] = useSearchParams();
  const tab = (TABS.some((t) => t.value === params.get("tab")) ? params.get("tab") : "general") as Tab;
  const [form, setForm] = useState<Settings | null>(null);
  const [info, setInfo] = useState<SystemInfo | null>(null);
  const [checking, setChecking] = useState(false);
  useEffect(() => {
    api.settings().then(setForm).catch(toast.error);
    api.systemInfo().then(setInfo).catch(toast.error);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  if (!form) return <div className="empty"><Spinner /></div>;
  const set = <K extends keyof Settings>(k: K, v: Settings[K]) => setForm({ ...form, [k]: v });
  const save = async () => {
    try {
      setForm(await api.saveSettings(form));
      toast.push("Impostazioni salvate");
      setInfo(await api.systemInfo(true));
      notifySystemChanged();
    } catch (e) {
      toast.error(e);
    }
  };
  const verify = async () => {
    setChecking(true);
    try {
      await api.saveSettings({ modeller_python: form.modeller_python });
      setInfo(await api.systemInfo(true));
      notifySystemChanged();
    } catch (e) {
      toast.error(e);
    } finally {
      setChecking(false);
    }
  };
  const container = !!info?.runtime?.container;
  const saves = tab === "general" || tab === "storage";
  return (
    <div className="page">
      <PageHead
        title="Impostazioni"
        lead="Licenze, database, strumenti e comportamento dei calcoli. Tutto è salvato nella cartella dati e viaggia con essa."
        actions={saves ? <button className="btn btn-primary" onClick={save}><Icon name="save" /> Salva</button> : undefined}
      />
      <Tabs value={tab} tabs={TABS} onChange={(t) => setParams(t === "general" ? {} : { tab: t }, { replace: true })} />
      <div style={{ marginTop: 16 }}>
        {tab === "licenses" && <LicensesPanel />}
        {tab === "databases" && <DatabasesPanel />}
        {tab === "tools" && <ToolsPanel info={info} onRefresh={() => api.systemInfo(true).then(setInfo).catch(toast.error)} />}
        {tab === "storage" && <StoragePanel form={form} set={set} info={info} />}
        {tab === "general" && (
          <div className="grid grid-2" style={{ alignItems: "start" }}>
            <div className="stack">
              <Card title="MODELLER">
                <div className="stack">
                  {info && (info.modeller.available ? (
                    <Alert tone="ok">MODELLER {info.modeller.version} pronto (Python {info.modeller.python_version}).</Alert>
                  ) : (
                    <Alert tone="err">
                      MODELLER non è utilizzabile. {container ? "Inserisci la chiave di licenza nella scheda Licenze." : "Tentativi:"}
                      {!container && (
                        <ul style={{ margin: "6px 0 0", paddingLeft: 18 }}>
                          {info.modeller.attempts.map((a, i) => <li key={i} className="small"><span className="mono">{a.python}</span>: {a.error}</li>)}
                        </ul>
                      )}
                    </Alert>
                  ))}
                  <button className="btn btn-sm" style={{ alignSelf: "start" }} onClick={() => setParams({ tab: "licenses" })}>
                    <Icon name="shield" size={13} /> Gestisci le licenze
                  </button>
                  {!container && (
                    <Field label="Interprete Python con MODELLER (esecuzione nativa)" hint="Lascia vuoto per usare quello del server.">
                      <div className="row">
                        <input className="input mono" style={{ flex: 1 }} value={form.modeller_python} onChange={(e) => set("modeller_python", e.target.value)} placeholder="(automatico)" />
                        <button className="btn" onClick={verify} disabled={checking}>{checking ? <Spinner /> : <Icon name="refresh" size={14} />} Verifica</button>
                      </div>
                    </Field>
                  )}
                </div>
              </Card>
              <Card title="Esecuzione">
                <div className="grid grid-2">
                  <Field label="Job di calcolo in parallelo" hint={`0 = automatico (${info?.workers ?? "core − 1"}). Limita anche i programmi di calcolo contemporanei di tutti i job (MODELLER, PROCHECK, BLAST…); vale subito.`}>
                    <input className="input" type="number" min={0} max={64} value={form.max_workers} onChange={(e) => set("max_workers", Number(e.target.value))} />
                  </Field>
                  <Field label="Timeout per processo (minuti)" hint="MODELLER e strumenti esterni">
                    <input className="input" type="number" min={1} max={1440} value={form.job_timeout_minutes} onChange={(e) => set("job_timeout_minutes", Number(e.target.value))} />
                  </Field>
                  <Field label="Motore di ricerca predefinito">
                    <select className="input" value={form.default_search_engine} onChange={(e) => set("default_search_engine", e.target.value as Settings["default_search_engine"])}>
                      <option value="rcsb">RCSB (rapido, online)</option>
                      <option value="ncbi">NCBI BLAST (online)</option>
                      <option value="local">BLAST+ locale (pdbaa)</option>
                      <option value="mmseqs">MMseqs2 locale (pdbaa)</option>
                    </select>
                  </Field>
                </div>
              </Card>
            </div>
            <Card title="QMEAN e servizi remoti">
              <div className="stack">
                <Field label="Dove calcolare QMEAN" hint="In locale serve il disco con UniClust30/UniRef30 (qualunque versione; si sceglie in Database).">
                  <select className="input" value={form.qmean_mode} onChange={(e) => set("qmean_mode", e.target.value as Settings["qmean_mode"])}>
                    <option value="local">In locale (UniClust30/UniRef30 sul disco esterno)</option>
                    <option value="remote">Su SWISS-MODEL</option>
                  </select>
                </Field>
                {form.qmean_mode === "local" && (
                  <label className="check">
                    <input type="checkbox" checked={form.qmean_fallback_remote} onChange={(e) => set("qmean_fallback_remote", e.target.checked)} />
                    Fallback su SWISS-MODEL: se QMEAN locale non è disponibile (disco scollegato) o non riesce, invia il modello a SWISS-MODEL
                  </label>
                )}
                {form.qmean_fallback_remote && form.qmean_mode === "local" && !(form.qmean_remote && form.contact_email) && (
                  <Alert tone="warn">Il fallback funziona solo con il consenso all'invio a SWISS-MODEL e la tua email (qui sotto).</Alert>
                )}
                <Field label="Tempo massimo per un calcolo QMEAN locale (minuti)" hint="HHblits su UniClust30 da disco esterno può richiedere oltre mezz'ora per modello.">
                  <input className="input" type="number" min={5} max={1440} value={form.qmean_timeout_minutes} onChange={(e) => set("qmean_timeout_minutes", Number(e.target.value))} />
                </Field>
                <Field label="Email di contatto" hint="Inviata a NCBI (buone pratiche d'uso) e obbligatoria per SWISS-MODEL. Non viene usata per altro.">
                  <input className="input" type="email" value={form.contact_email} onChange={(e) => set("contact_email", e.target.value)} placeholder="nome@universita.it" />
                </Field>
                <label className="check">
                  <input type="checkbox" checked={form.qmean_remote} onChange={(e) => set("qmean_remote", e.target.checked)} />
                  Consenti l'invio dei modelli a SWISS-MODEL per il calcolo di QMEAN
                </label>
                <p className="tiny muted">
                  Le coordinate lasciano il computer solo con questa opzione attiva, per un calcolo su SWISS-MODEL o per il fallback, se lo hai attivato. QMEAN locale e le ricerche locali (BLAST+, MMseqs2) non usano la rete; RCSB e NCBI ricevono la sola sequenza.
                </p>
              </div>
            </Card>
          </div>
        )}
      </div>
    </div>
  );
}
