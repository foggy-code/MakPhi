// Schede delle Impostazioni: licenze, database, strumenti, spazio e file temporanei.
import { useEffect, useRef, useState } from "react";
import { api } from "../api/client";
import type { DatabaseStatus, JobSummary, LicenseStatus, SelfTestResult, Settings, SystemInfo, UniclustDatabase } from "../api/types";
import { useToast } from "../lib/context";
import { fmtBytes } from "../lib/format";
import { extractModellerKey } from "../lib/files";
import { notifySystemChanged, useAsync, useJob } from "../lib/hooks";
import { fmtSeconds, licenseWarnings } from "../lib/status";
import { TextFileButton } from "./FileLoad";
import { Icon } from "./Icon";
import { JobPanel } from "./JobPanel";
import { Alert, Badge, Card, DropZone, Field, Spinner, Stat } from "./ui";

/** Avvia un job di servizio (database, verifica) e ne mostra l'avanzamento. */
function useServiceJob<R>(onDone?: (r: R | null) => void) {
  const toast = useToast();
  const [jobId, setJobId] = useState<string | null>(null);
  const { job, summary, cancel } = useJob<R>(jobId);
  const notified = useRef<string | null>(null);
  const done = useRef(onDone);
  done.current = onDone;
  useEffect(() => {
    if (!job || job.id === notified.current || !["completed", "failed", "cancelled"].includes(job.status)) return;
    notified.current = job.id;
    if (job.status === "completed") done.current?.(job.result);
    else if (job.error) toast.error(new Error(job.error));
  }, [job, toast]);
  const start = (fn: () => Promise<JobSummary>) => fn().then((j) => setJobId(j.id)).catch(toast.error);
  const running = !!summary && ["queued", "running", "cancelling"].includes(summary.status);
  return { start, job, summary, cancel, running, jobId };
}

// ---------------------------------------------------------------------------------------------- licenze
export function LicensesPanel() {
  const toast = useToast();
  const { data, setData, loading } = useAsync<LicenseStatus>(() => api.licenses(), []);
  const [key, setKey] = useState("");
  const [busy, setBusy] = useState(false);
  if (loading && !data) return <div className="empty"><Spinner /></div>;
  if (!data) return null;
  const update = (s: LicenseStatus) => {
    setData(s);
    notifySystemChanged();
  };
  const saveKey = async () => {
    setBusy(true);
    try {
      update(await api.setModellerKey(key));
      setKey("");
      toast.push("Chiave MODELLER verificata e salvata");
    } catch (e) {
      toast.error(e);
    } finally {
      setBusy(false);
    }
  };
  const m = data.modeller;
  const p = data.prosa;
  return (
    <div className="grid grid-2" style={{ alignItems: "start" }}>
      <div className="stack">
        {licenseWarnings(data).map((w, i) => <Alert key={i} tone="warn">{w}</Alert>)}
        <Card title="MODELLER" sub="licenza accademica gratuita">
          <div className="stack">
            {m.available ? (
              <Alert tone="ok">
                MODELLER {m.version} pronto.{" "}
                {m.source === "file" && <>Chiave salvata nella cartella dati (<span className="mono">{m.masked}</span>).</>}
                {m.source === "env" && <>Chiave fornita dalla configurazione del server (MODELLER_KEY).</>}
                {!m.source && <>Chiave già presente nell'installazione di MODELLER.</>}
              </Alert>
            ) : (
              <Alert tone="err">{m.configured ? `La chiave attuale non è valida: ${m.message ?? ""}` : "Nessuna chiave di licenza: la modellazione non può partire."}</Alert>
            )}
            <Field label={m.configured ? "Sostituisci la chiave" : "Chiave di licenza MODELLER"} hint={<>Si ottiene gratis per uso accademico su <a href="https://salilab.org/modeller/registration.html" target="_blank" rel="noreferrer">salilab.org/modeller</a>. BioLoom la prova con MODELLER e la salva solo se viene accettata.</>}>
              <div className="row">
                <input className="input mono" style={{ flex: 1 }} value={key} onChange={(e) => setKey(e.target.value)} placeholder="MODELXXXX" autoComplete="off" spellCheck={false} />
                <button className="btn btn-primary" onClick={saveKey} disabled={busy || key.trim().length < 4}>{busy ? <Spinner /> : <Icon name="check" size={14} />} Verifica e salva</button>
              </div>
            </Field>
            <div className="row">
              <TextFileButton
                accept=".txt,.key,.py,.env,.eml,.msg,text/plain"
                onText={(text) => {
                  const found = extractModellerKey(text);
                  if (found) { setKey(found); toast.push("Chiave letta dal file: premi Verifica e salva", "info"); }
                  else toast.push("Nel file non c'è una chiave MODELLER riconoscibile.", "warn");
                }}
              >
                Leggi la chiave da un file
              </TextFileButton>
              <span className="tiny muted">file di testo, email di registrazione salvata o config.py di un'altra installazione di MODELLER</span>
            </div>
            {m.source === "file" && (
              <button className="btn btn-sm btn-ghost" style={{ alignSelf: "start" }} onClick={() => api.removeModellerKey().then(update).catch(toast.error)}>
                <Icon name="trash" size={13} /> Rimuovi la chiave salvata
              </button>
            )}
          </div>
        </Card>
        <Card title="PROCHECK">
          {data.procheck.installed
            ? <Alert tone="ok">PROCHECK è installato: nessuna chiave richiesta (licenza accademica).</Alert>
            : <Alert tone="info">PROCHECK non è incluso in questa immagine: metti il pacchetto in docker/vendor/procheck e ricostruisci l'immagine.</Alert>}
        </Card>
      </div>
      <Card title="ProSA II 2003" sub="licenza legata al MAC del computer">
        <div className="stack">
          {!p.installed && <Alert tone="info">ProSA non è incluso in questa immagine (pacchetto in docker/vendor/prosa).</Alert>}
          {p.license ? (
            <>
              <dl className="kv">
                <dt>Programma</dt><dd>{p.program}</dd>
                <dt>Scadenza</dt><dd>{p.expires}</dd>
                <dt>MAC della licenza</dt><dd className="mono">{p.mac_required}</dd>
                <dt>MAC del container</dt><dd className="mono">{p.mac_current ?? "—"} {p.mac_ok ? <Badge tone="ok">corrisponde</Badge> : <Badge tone="warn">diverso</Badge>}</dd>
              </dl>
              {!p.mac_ok && <Alert tone="warn">Chiudi BioLoom e riaprilo con l'avviatore: legge il MAC dalla licenza e lo assegna al container.</Alert>}
              <button className="btn btn-sm btn-ghost" style={{ alignSelf: "start" }} onClick={() => api.removeProsaLicense().then(update).catch(toast.error)}>
                <Icon name="trash" size={13} /> Rimuovi la licenza
              </button>
            </>
          ) : (
            <p className="small text-2">Carica il file <span className="mono">LicenseDB</span> ricevuto via email da ProSA (una riga con programma, scadenza, MAC e codice).</p>
          )}
          {p.error && <Alert tone="err">{p.error}</Alert>}
          <DropZone accept="*" label={p.license ? "Sostituisci il file LicenseDB" : "Trascina qui il file LicenseDB o fai clic"} onFile={(f) => api.uploadProsaLicense(f).then((s) => { update(s); toast.push("Licenza ProSA salvata"); }).catch(toast.error)} />
          <p className="tiny muted">Le licenze restano nella cartella dati (sottocartella licenses) e viaggiano con essa: non entrano mai nell'immagine Docker.</p>
        </div>
      </Card>
    </div>
  );
}

// ---------------------------------------------------------------------------------------------- database
export function DatabasesPanel() {
  const { data, reload } = useAsync<DatabaseStatus>(() => api.databases(), []);
  const svc = useServiceJob(() => reload());
  if (!data) return <div className="empty"><Spinner /></div>;
  const q = data.qmean;
  return (
    <div className="grid grid-2" style={{ alignItems: "start" }}>
      <div className="stack">
        {svc.summary && <JobPanel summary={svc.summary} onCancel={svc.cancel} compact />}
        <Card title="pdbaa (BLAST+)" sub="sequenze del PDB per la ricerca locale">
          <div className="stack">
            {data.pdbaa.usable
              ? <Alert tone="ok">Database presente: {fmtBytes(data.pdbaa.size)}{data.pdbaa.updated ? `, aggiornato il ${data.pdbaa.updated}` : ""}.</Alert>
              : <Alert tone="info">Database assente: la ricerca locale (BLAST+ e MMseqs2) non è disponibile. RCSB e NCBI funzionano comunque.</Alert>}
            <button className="btn" style={{ alignSelf: "start" }} disabled={svc.running} onClick={() => svc.start(api.updatePdbaa)}>
              <Icon name="download" size={14} /> {data.pdbaa.usable ? "Aggiorna da NCBI" : "Scarica da NCBI"}
            </button>
            <p className="tiny muted">Circa 0,5 GB nella cartella dati. Il download avviene nell'area temporanea e sostituisce il database solo a fine operazione.</p>
          </div>
        </Card>
        <Card title="MMseqs2" sub="ricerca locale veloce">
          <div className="stack">
            {data.mmseqs.usable ? (
              <Alert tone={data.mmseqs.indexed_here ? "ok" : "info"}>
                Database pronto ({fmtBytes(data.mmseqs.size)}).{" "}
                {data.mmseqs.indexed_here ? `Indice veloce presente su questo computer (${fmtBytes(data.mmseqs.index_size)}).` : "Su questo computer manca l'indice veloce: le ricerche funzionano ma sono più lente."}
              </Alert>
            ) : <Alert tone="info">Database MMseqs2 non ancora preparato: si crea da pdbaa in pochi minuti.</Alert>}
            <div className="row">
              <button className="btn" disabled={svc.running || !data.pdbaa.usable} onClick={() => svc.start(api.buildMmseqs)}>
                <Icon name="refresh" size={14} /> {data.mmseqs.usable ? "Ricrea da pdbaa" : "Prepara MMseqs2"}
              </button>
              {data.mmseqs.usable && !data.mmseqs.indexed_here && (
                <button className="btn btn-primary" disabled={svc.running} onClick={() => svc.start(api.indexMmseqs)}>
                  <Icon name="play" size={12} /> Crea indice veloce
                </button>
              )}
            </div>
            <p className="tiny muted">Il database (≈ 140 MB) sta nella cartella dati; l'indice (≈ 1,5 GB) resta su ciascun computer, nel volume Docker locale, per non appesantire la chiavetta.</p>
          </div>
        </Card>
      </div>
      <UniclustCard status={q} running={svc.running} onConvert={(id) => svc.start(() => api.convertUniclust(id))}
        onLocal={(id, action) => svc.start(() => api.uniclustLocal(id, action))} onChanged={reload} />
    </div>
  );
}

/** Stato della copia locale degli indici (solo quando serve o esiste). */
function LocalCopyNote({ db }: { db: UniclustDatabase }) {
  const local = db.local;
  if (!local) return null;
  if (local.state === "copying") {
    return <div className="tiny">Copia degli indici in corso: {Math.round((local.progress ?? 0) * 100)}%</div>;
  }
  if (local.state === "ready") {
    return <div className="tiny" style={{ color: "var(--ok)" }}>Indici su questo computer ({fmtBytes(local.used)})</div>;
  }
  if (local.state === "stale") {
    return <div className="tiny" style={{ color: "var(--warn)" }}>Copia locale da aggiornare: il database sul disco è cambiato</div>;
  }
  if (db.shared && db.state !== "incomplete") {
    return <div className="tiny" style={{ color: "var(--warn)" }}>Disco condiviso: HHblits è lento, copia gli indici ({fmtBytes(local.size)})</div>;
  }
  return null;
}

const UNICLUST_STATE: Record<UniclustDatabase["state"], { label: string; tone: "ok" | "warn" | "err" | "info" }> = {
  ready: { label: "pronto", tone: "ok" },
  needs_conversion: { label: "da convertire", tone: "warn" },
  converting: { label: "conversione in corso", tone: "info" },
  incomplete: { label: "incompleto", tone: "err" },
};

/** UniClust30/UniRef30: versioni trovate sul disco esterno, scelta della versione, conversione dei formati vecchi. */
function UniclustCard({ status: q, running, onConvert, onLocal, onChanged }: {
  status: DatabaseStatus["qmean"];
  running: boolean;
  onConvert: (id: string) => void;
  onLocal: (id: string, action: "copy" | "remove") => void;
  onChanged: () => void;
}) {
  const toast = useToast();
  const { data: settings, setData: setSettings } = useAsync<Settings>(() => api.settings(), []);
  const choose = (value: string) =>
    api.saveSettings({ qmean_uniclust: value })
      .then((s) => { setSettings(s); onChanged(); toast.push(value ? "Versione di UniClust30 impostata" : "Si userà la versione più recente pronta"); })
      .catch(toast.error);
  const dbs = q.databases ?? [];
  const ready = dbs.filter((d) => d.state === "ready");
  return (
    <Card title="UniClust30 / UniRef30 per QMEAN locale" sub="qualunque versione, dal disco esterno">
      <div className="stack">
        {q.available ? (
          <Alert tone="ok">
            QMEAN locale usa <b>{q.uniclust?.prefix}</b>{q.uniclust?.version ? ` (${q.uniclust.version})` : ""}.{" "}
            {q.uniclust?.hhm === false && "Il database non ha i profili HMM: HHblits li calcola dagli allineamenti. "}
            {q.qmtl?.present ? `QMTL ${q.qmtl.version}: si usa QMEANDisCo.` : "QMTL assente: si usa il metodo QMEAN."}
          </Alert>
        ) : (
          <Alert tone="warn">{q.message ?? "QMEAN locale non disponibile."}</Alert>
        )}
        {q.uniclust?.memory_message && (
          <Alert tone={q.uniclust.memory === "insufficient" ? "err" : "warn"}>
            {q.uniclust.memory === "swap" && <>{q.uniclust.memory_message}<br /></>}
            <span className="small">
              Windows: nel file <span className="mono">%UserProfile%\.wslconfig</span> scrivi <span className="mono">[wsl2]</span> e
              {" "}<span className="mono">memory=…GB</span> (il valore indicato, se la RAM del computer lo permette: di norma Docker ne riceve metà),
              poi <span className="mono">wsl --shutdown</span> e riavvia Docker Desktop.
              {" "}macOS: Docker Desktop → Settings → Resources → Memory. In alternativa usa il fallback su SWISS-MODEL.
            </span>
          </Alert>
        )}
        {dbs.length > 0 && (
          <>
            <div className="table-wrap">
              <table className="table">
                <thead><tr><th>Versione</th><th>Formato</th><th className="num">Dimensione</th><th>Stato</th></tr></thead>
                <tbody>
                  {dbs.map((d) => (
                    <tr key={d.id} className={d.id === q.selected ? "selected" : ""}>
                      <td>
                        <b>{d.name}</b> {d.id === q.selected && <Badge tone="accent">in uso</Badge>}
                        <div className="tiny muted">{d.family}{d.version ? ` · ${d.version}` : ""}{d.hhm ? "" : " · senza HMM"}{d.cs219_source === "converted" ? " · cs219 ricreato" : ""}</div>
                        {d.problems.map((p, i) => <div key={i} className="tiny" style={{ color: "var(--err)" }}>{p}</div>)}
                        <LocalCopyNote db={d} />
                        <div className="row" style={{ gap: 6, marginTop: 6 }}>
                          {d.state === "needs_conversion" && (
                            <button className="btn btn-sm" disabled={running} onClick={() => onConvert(d.id)} title="Ricrea il file cs219 per HH-suite 3 dagli allineamenti (una volta sola)">
                              <Icon name="refresh" size={12} /> Converti
                            </button>
                          )}
                          {d.state !== "incomplete" && d.local && d.local.state !== "copying" && d.local.state !== "ready" && (d.shared || d.local.state === "stale") && (
                            <button className={`btn btn-sm${d.id === q.selected && d.state === "ready" ? " btn-primary" : ""}`} disabled={running}
                              onClick={() => onLocal(d.id, "copy")}
                              title={`Copia indici e cs219 (${fmtBytes(d.local.size)}) nel volume Docker di questo computer: HHblits evita le letture lente dal disco condiviso`}>
                              <Icon name="download" size={12} /> Copia indici ({fmtBytes(d.local.size)})
                            </button>
                          )}
                          {d.local && (d.local.state === "ready" || d.local.state === "stale") && (
                            <button className="btn btn-sm btn-ghost" disabled={running} onClick={() => onLocal(d.id, "remove")} title="Elimina la copia locale e libera spazio (il database sul disco non cambia)">
                              <Icon name="trash" size={12} />
                            </button>
                          )}
                        </div>
                      </td>
                      <td className="small">{d.format}</td>
                      <td className="num small">{fmtBytes(d.size)}</td>
                      <td><Badge tone={UNICLUST_STATE[d.state].tone}>{UNICLUST_STATE[d.state].label}</Badge></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <Field label="Versione da usare" hint="Automatica = la più recente pronta. La scelta vale anche su altri computer, se trovano la stessa versione.">
              <select className="input" value={settings?.qmean_uniclust ?? ""} onChange={(e) => choose(e.target.value)} disabled={!settings}>
                <option value="">Automatica (la più recente pronta)</option>
                {ready.map((d) => <option key={d.id} value={d.id}>{d.name}{d.version ? ` · ${d.version}` : ""}</option>)}
              </select>
            </Field>
            {dbs.some((d) => d.shared) && (
              <p className="tiny muted">
                Il disco arriva nel container attraverso la condivisione di file di Docker Desktop: è veloce a blocchi grandi ma molto lento per le
                letture piccole con cui HHblits scorre gli indici. <b>Copia indici</b> porta su questo computer solo indici e cs219 (pochi GB, nel disco
                di Docker: su Windows di solito in C:): i file grandi restano sul disco e se ne leggono solo le voci trovate. Va fatto una volta per computer; se il database
                cambia, BioLoom se ne accorge e torna a leggere dal disco finché non aggiorni la copia.
              </p>
            )}
            {dbs.some((d) => d.state === "needs_conversion") && (
              <p className="tiny muted">
                Le versioni in formato HH-suite 2 (uniclust30 2016–2017) vanno convertite una volta: BioLoom ricrea il file cs219 dagli allineamenti
                e lo conserva su questo computer, senza modificare il disco. Sui database completi richiede alcune ore.
              </p>
            )}
          </>
        )}
        {(q.archives ?? []).map((a) => (
          <Alert key={a.file} tone="warn">Trovato l'archivio <span className="mono">{a.file}</span> ({fmtBytes(a.size)}) ma non è estratto: estrailo sul disco (es. <span className="mono">tar -xzf</span>) e riavvia BioLoom.</Alert>
        ))}
        {(q.unsupported ?? []).map((u) => <Alert key={u.file} tone="warn"><span className="mono">{u.file}</span>: {u.reason}</Alert>)}
        {(q.local_orphans ?? []).map((o) => (
          <Alert key={o.id} tone="info">
            <div className="row" style={{ justifyContent: "space-between" }}>
              <span>Copia locale di <span className="mono">{o.id}</span> ({fmtBytes(o.size)}): il database non è collegato.</span>
              <button className="btn btn-sm btn-ghost" disabled={running} onClick={() => onLocal(o.id, "remove")}><Icon name="trash" size={12} /> Elimina</button>
            </div>
          </Alert>
        ))}
        <div className="step-list">
          <div className="step"><span className="n">1</span><div className="small">Collega il disco con UniClust30 o UniRef30, in qualunque versione (UniRef30 2020–2023, uniclust30 2016–2018), anche più versioni in cartelle diverse.</div></div>
          <div className="step"><span className="n">2</span><div className="small">Avvia (o riavvia) BioLoom con l'avviatore: trova da solo le versioni sui dischi collegati, anche se cambia la lettera o il punto di montaggio. Se non le trova, indica la cartella con il tasto U.</div></div>
          <div className="step"><span className="n">3</span><div className="small">Facoltativo: una cartella con la QMTL abilita QMEANDisCo.</div></div>
        </div>
        <p className="tiny muted">
          QMEAN è calibrato dagli autori su UniClust30 2018_08: con altre versioni i termini di accordo di struttura secondaria e accessibilità
          possono variare leggermente. Su un server il percorso si indica in server.env (UNICLUST_PATH, QMTL_PATH). Il disco resta in sola lettura.
        </p>
      </div>
    </Card>
  );
}

// ---------------------------------------------------------------------------------------------- strumenti
const STATUS_ICON = { ok: "check", missing: "info", error: "alert" } as const;
const STATUS_COLOR = { ok: "var(--ok)", missing: "var(--muted)", error: "var(--err)" } as const;

export function ToolsPanel({ info, onRefresh }: { info: SystemInfo | null; onRefresh: () => void }) {
  const [result, setResult] = useState<SelfTestResult | null>(null);
  const svc = useServiceJob<SelfTestResult>((r) => setResult(r));
  return (
    <div className="grid grid-2" style={{ alignItems: "start" }}>
      <Card title="Verifica dell'installazione" sub="ogni strumento provato sull'ubiquitina (1UBQ)" right={<button className="btn btn-primary btn-sm" disabled={svc.running} onClick={() => svc.start(api.selftest)}>{svc.running ? <Spinner /> : <Icon name="play" size={12} />} Avvia</button>}>
        <div className="stack">
          {svc.running && svc.summary && <JobPanel summary={svc.summary} onCancel={svc.cancel} compact />}
          {result ? (
            <>
              <div className="check-list">
                {result.checks.map((c) => (
                  <div key={c.tool} className="check-row">
                    <span style={{ color: STATUS_COLOR[c.status] }}><Icon name={STATUS_ICON[c.status]} size={15} /></span>
                    <b>{c.tool}</b>
                    <span className="small text-2">{c.detail}</span>
                    <span className="tiny muted">{fmtSeconds(c.seconds)}</span>
                  </div>
                ))}
              </div>
              <p className="small">{result.counts.ok} funzionanti · {result.counts.missing} non disponibili · {result.counts.error} con errori</p>
            </>
          ) : !svc.running && <p className="small muted">Prova MODELLER, DSSP, BLAST+, MMseqs2, Clustal Omega, MAFFT, MUSCLE, TM-align, US-align, PROCHECK, ProSA e il servizio QMEAN. Richiede meno di un minuto.</p>}
        </div>
      </Card>
      <Card title="Strumenti rilevati" right={<button className="btn btn-sm" onClick={onRefresh}><Icon name="refresh" size={13} /> Rileva</button>}>
        {info && (
          <div className="table-wrap">
            <table className="table">
              <tbody>
                {Object.entries(info.tools).map(([k, t]) => (
                  <tr key={k}>
                    <td>
                      <span className="row" style={{ gap: 6 }}><span className="dot" style={{ color: t.available ? "var(--ok)" : "var(--muted)" }} /><b>{t.label}</b></span>
                      <div className="tiny muted">{t.use}</div>
                    </td>
                    <td className="small">{t.available ? <span className="mono tiny">{t.path}</span> : <span className="tiny muted">non installato: si usa il motore integrato, se esiste</span>}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>
    </div>
  );
}

// ---------------------------------------------------------------------------------------------- spazio
const STORAGE_LABELS: [string, string, string][] = [
  ["jobs", "Risultati dei job", "cartella dati"],
  ["templates", "Libreria template", "cartella dati"],
  ["databases", "Database locali", "cartella dati"],
  ["uploads", "File caricati", "cartella dati, pulizia automatica"],
  ["cache", "Cache servizi remoti", "cartella dati, pulizia automatica"],
  ["database_file", "Database progetti", "cartella dati"],
  ["scratch", "Area di lavoro temporanea", "volume Docker, svuotata a ogni avvio"],
  ["local_cache", "Indici locali", "volume Docker di questo computer"],
];

export function StoragePanel({ form, set, info }: { form: Settings; set: <K extends keyof Settings>(k: K, v: Settings[K]) => void; info: SystemInfo | null }) {
  const toast = useToast();
  const { data: storage, reload } = useAsync<Record<string, number>>(() => api.storage(), []);
  const [cleaning, setCleaning] = useState(false);
  const clean = async () => {
    setCleaning(true);
    try {
      const r = await api.cleanup();
      toast.push(`Pulizia completata: liberati ${fmtBytes(r.freed_bytes)}`);
      reload();
    } catch (e) {
      toast.error(e);
    } finally {
      setCleaning(false);
    }
  };
  return (
    <div className="grid grid-2" style={{ alignItems: "start" }}>
      <Card title="Occupazione" right={<button className="btn btn-sm" onClick={clean} disabled={cleaning}>{cleaning ? <Spinner /> : <Icon name="trash" size={13} />} Pulisci ora</button>}>
        {storage ? (
          <div className="grid grid-2">
            {STORAGE_LABELS.filter(([k]) => k in storage).map(([k, label, note]) => (
              <Stat key={k} label={label} value={<span style={{ fontSize: 16 }}>{fmtBytes(storage[k])}</span>} note={note} />
            ))}
          </div>
        ) : <Spinner />}
      </Card>
      <div className="stack">
        <Card title="File temporanei e risultati">
          <div className="stack">
            <p className="small text-2">
              I calcoli girano nell'area di lavoro temporanea (veloce, sul computer); a fine job nella cartella dati restano solo i risultati.
              I job falliti conservano tutti i file per capire cosa è andato storto.
            </p>
            <label className="check">
              <input type="checkbox" checked={form.keep_intermediates} onChange={(e) => set("keep_intermediates", e.target.checked)} />
              Conserva anche i file intermedi di MODELLER (restrizioni .rsr, traiettorie, profili di violazione)
            </label>
            <Field label="Conservazione dei file caricati per l'analisi (giorni)" hint="0 = conserva sempre. I risultati dei job non vengono mai eliminati automaticamente.">
              <input className="input" type="number" min={0} max={3650} value={form.uploads_retention_days} onChange={(e) => set("uploads_retention_days", Number(e.target.value))} />
            </Field>
          </div>
        </Card>
        <Card title="Installazione">
          {info && (
            <dl className="kv">
              <dt>Versione</dt><dd>BioLoom {info.version}{info.runtime?.image ? ` · immagine ${info.runtime.image}` : ""}</dd>
              <dt>Esecuzione</dt><dd>{info.runtime?.container ? "container Docker" : "nativa"} · Python {info.python} · {info.runtime?.arch}</dd>
              <dt>Accesso</dt><dd>{info.runtime?.auth ? "protetto da chiave (server)" : "locale, senza login"}</dd>
              <dt>Cartella dati</dt><dd className="mono">{info.data_dir}</dd>
            </dl>
          )}
        </Card>
      </div>
    </div>
  );
}
