import { describe, expect, it } from "vitest";
import type { JobStatus, JobSummary, LicenseStatus } from "../api/types";
import {
  cancelAndSync, fmtSeconds, type JobSync, licenseWarnings, needsFullReload, qmeanMemoryNote, qmeanRoute, resumeAndSync,
  resumeOptions,
} from "./status";

describe("qmeanMemoryNote", () => {
  const GB = 1024 ** 3;
  it("memoria del servizio e requisito del database scelto", () => {
    expect(qmeanMemoryNote({ memory: { total: 15.6 * GB, swap: 0 }, memory_required: { bytes: 12 * GB, basis: "measured" } }))
      .toBe("Memoria di Docker: 15,6 GB; richiesti circa 12 GB (valore misurato).");
    expect(qmeanMemoryNote({ memory: { total: 8 * GB, swap: 2 * GB }, memory_required: { bytes: 24 * GB, basis: "estimate" } }))
      .toBe("Memoria di Docker: 8 GB; richiesti circa 24 GB (stima dalla dimensione del database).");
  });
  it("con l'esito del server dice se la memoria basta", () => {
    const measured = { bytes: 12 * GB, basis: "measured" as const };
    expect(qmeanMemoryNote({ memory: { total: 15.6 * GB, swap: 0 }, memory_required: { ...measured, sufficient: true } }))
      .toBe("Memoria di Docker: 15,6 GB; richiesti circa 12 GB (valore misurato): sufficiente.");
    expect(qmeanMemoryNote({ memory: { total: 8 * GB, swap: 0 }, memory_required: { ...measured, sufficient: false } }))
      .toBe("Memoria di Docker: 8 GB; richiesti circa 12 GB (valore misurato): insufficiente.");
    // 12 GB assegnati, 11,7 visti dal container: accettati dal server, e la nota non sembra un difetto
    expect(qmeanMemoryNote({ memory: { total: 11.7 * GB, swap: 0 }, memory_required: { ...measured, sufficient: true } }))
      .toBe("Memoria di Docker: 11,7 GB; richiesti circa 12 GB (valore misurato): sufficiente (il container vede un po' meno della memoria assegnata a Docker).");
    expect(qmeanMemoryNote({ memory: { total: 11.7 * GB, swap: 0 }, memory_required: { ...measured, sufficient: true }, uniclust: { valid: true, prefix: "UniRef30_2020_06", error: null, memory: "swap" } }))
      .toBe("Memoria di Docker: 11,7 GB; richiesti circa 12 GB (valore misurato): sufficiente per partire, ma al limite secondo il servizio QMEAN.");
  });
  it("con il giudizio \"ok\" del worker non mostra una stima più alta della memoria", () => {
    const uniclust = { valid: true, prefix: "UniRef30_2023_02", error: null, memory: "ok" as const };
    const note = qmeanMemoryNote({ memory: { total: 16 * GB, swap: 0 }, memory_required: { bytes: 24 * GB, basis: "estimate", sufficient: true }, uniclust });
    expect(note).toBe("Memoria di Docker: 16 GB: sufficiente secondo il servizio QMEAN, che misura le dimensioni reali degli indici.");
    expect(note).not.toContain("24 GB");
  });
  it("nessuna nota senza i dati (worker o server di una versione precedente)", () => {
    expect(qmeanMemoryNote(null)).toBeNull();
    expect(qmeanMemoryNote({ memory: null, memory_required: { bytes: 12 * GB, basis: "measured" } })).toBeNull();
    expect(qmeanMemoryNote({ memory: { total: 8 * GB, swap: 0 } })).toBeNull();
  });
});

const base = { qmean_mode: "local" as const, qmean_remote: false, contact_email: "", qmean_fallback_remote: false };
const remote = { qmean_remote: true, contact_email: "a@b.it" };
const off = { available: false, message: "UniClust30 non montato" };

describe("qmeanRoute", () => {
  it("usa il servizio locale quando UniClust30 è disponibile", () => {
    const r = qmeanRoute(base, { available: true, message: null });
    expect(r.source).toBe("local");
    expect(r.hint).toContain("non lascia il computer");
    expect(r.canFallback).toBe(false);
  });
  it("senza fallback non invia nulla a SWISS-MODEL e spiega come fare", () => {
    const r = qmeanRoute({ ...base, ...remote }, off);
    expect(r.source).toBeNull();
    expect(r.hint).toContain("UniClust30 non montato");
    expect(r.hint).toContain("fallback");
    expect(r.canFallback).toBe(true);
  });
  it("con il fallback attivo passa a SWISS-MODEL solo se consenso ed email ci sono", () => {
    expect(qmeanRoute({ ...base, ...remote, qmean_fallback_remote: true }, off).source).toBe("remote");
    expect(qmeanRoute({ ...base, qmean_remote: true, qmean_fallback_remote: true }, off).source).toBeNull();
    const ok = qmeanRoute({ ...base, ...remote, qmean_fallback_remote: true }, { available: true, message: null });
    expect(ok.source).toBe("local");
    expect(ok.hint).toContain("si passa a SWISS-MODEL");
  });
  it("suggerisce la copia locale degli indici quando il disco è condiviso", () => {
    const db = { id: "u", name: "UniRef30_2020_06", family: "UniRef30", version: "2020-06", state: "ready" as const, problems: [],
      format: "HH-suite 3" as const, hhm: true, cs219_source: "database" as const, legacy_cs219: false, size: 1, shared: true,
      local: { state: "none" as const, size: 1, used: 0 } };
    const slow = qmeanRoute(base, { available: true, message: null, databases: [db], selected: "u" });
    expect(slow.hint).toContain("copia gli indici");
    const fast = qmeanRoute(base, { available: true, message: null, databases: [{ ...db, local: { state: "ready", size: 1, used: 1 } }], selected: "u" });
    expect(fast.hint).not.toContain("copia gli indici");
    expect(qmeanRoute(base, { available: true, message: null, databases: [{ ...db, shared: false }], selected: "u" }).hint).not.toContain("copia gli indici");
    const swap = qmeanRoute(base, { available: true, message: null, uniclust: { valid: true, prefix: "u", error: null, memory: "swap" } });
    expect(swap.hint).toContain("può durare ore");
  });
  it("in modalità SWISS-MODEL ignora il servizio locale", () => {
    expect(qmeanRoute({ ...base, ...remote, qmean_mode: "remote" }, { available: true, message: null }).source).toBe("remote");
    expect(qmeanRoute({ ...base, qmean_mode: "remote" }, { available: true, message: null }).source).toBeNull();
  });
});

const lic = (over: Partial<LicenseStatus["modeller"]> = {}, prosa: Partial<LicenseStatus["prosa"]> = {}): LicenseStatus => ({
  modeller: { configured: true, source: "file", masked: "MOD•••EY", available: true, version: "10.8", message: null, ...over },
  prosa: { installed: true, license: true, error: null, program: "ProSa2003", expires: "dec-31-2099", mac_required: "0a:1b:2c:3d:4e:5f", mac_current: "0a:1b:2c:3d:4e:5f", mac_ok: true, ...prosa },
  procheck: { installed: true },
});

describe("licenseWarnings", () => {
  it("nessun avviso quando tutto è a posto", () => {
    expect(licenseWarnings(lic())).toEqual([]);
  });
  it("segnala la chiave MODELLER mancante o rifiutata", () => {
    expect(licenseWarnings(lic({ configured: false, available: false }))[0]).toContain("Manca la chiave");
    expect(licenseWarnings(lic({ available: false, message: "Invalid license key" }))[0]).toContain("Invalid license key");
  });
  it("segnala il MAC diverso per ProSA", () => {
    expect(licenseWarnings(lic({}, { mac_ok: false }))[0]).toContain("MAC");
  });
});

describe("resumeOptions", () => {
  const job = (incomplete: number, failed: number, total = 40) => ({ resumable: incomplete > 0, resumable_items: { total, incomplete, failed } });
  it("propone Riprendi e Riprova solo i falliti per una pipeline annullata con falliti", () => {
    const r = resumeOptions("cancelled", job(28, 3));
    expect(r).not.toBeNull();
    expect(r!.incomplete).toBe(28);
    expect(r!.failedOnly).toBe(true);
    expect(r!.text).toBe("12 di 40 sequenze concluse · 28 da riprendere (3 fallite).");
  });
  it("nasconde Riprova solo i falliti quando coincide con Riprendi o non ci sono falliti", () => {
    expect(resumeOptions("failed", job(5, 5))!.failedOnly).toBe(false); // dopo un riavvio: tutti falliti
    const onlyCancelled = resumeOptions("cancelled", job(3, 0, 5))!;
    expect(onlyCancelled.failedOnly).toBe(false);
    expect(onlyCancelled.text).toBe("2 di 5 sequenze concluse · 3 da riprendere.");
    expect(resumeOptions("failed", job(2, 1, 5))!.text).toContain("(1 fallita)");
    expect(resumeOptions("cancelled", job(3, 1))!.label).toBe("Riprendi");
  });
  it("propone Riprova i falliti per una pipeline completata con elementi falliti", () => {
    const r = resumeOptions("completed", job(2, 2));
    expect(r).not.toBeNull();
    expect(r!.label).toBe("Riprova i falliti");
    expect(r!.failedOnly).toBe(false); // un solo pulsante: coincide con il principale
    expect(r!.text).toBe("38 di 40 sequenze concluse · 2 fallite da riprovare.");
    expect(resumeOptions("completed", job(1, 1, 5))!.text).toBe("4 di 5 sequenze concluse · 1 fallita da riprovare.");
  });
  it("niente ripresa per job attivi, completati senza falliti o senza elementi da rifare", () => {
    expect(resumeOptions("running", job(3, 1))).toBeNull(); // il job completo può essere vecchio: vale lo stato
    expect(resumeOptions("queued", job(3, 1))).toBeNull();
    expect(resumeOptions("completed", { resumable: false, resumable_items: null })).toBeNull();
    expect(resumeOptions("completed", job(0, 0))).toBeNull();
    expect(resumeOptions("failed", job(0, 0))).toBeNull();
    expect(resumeOptions("failed", null)).toBeNull();
  });
});

describe("annullamento e ripresa (useJob)", () => {
  const summary = (status: JobStatus, updated_at = "2026-01-01T10:00:00+00:00") => ({ status, updated_at }) as JobSummary;
  const tracker = (last: JobSummary | null) => {
    const seen: string[] = [];
    let reloads = 0;
    const sync: JobSync = {
      last: { current: last },
      setSummary: (s) => seen.push(s.status),
      loadFull: async () => {
        reloads += 1;
      },
    };
    return { sync, seen, reloads: () => reloads };
  };

  it("annullando un job in coda (chiuso subito) ricarica il job completo, così compaiono i pulsanti di ripresa", async () => {
    const t = tracker(summary("queued"));
    await cancelAndSync(t.sync, async () => summary("cancelled", "2026-01-01T10:01:00+00:00"));
    expect(t.seen).toEqual(["cancelled"]);
    expect(t.sync.last.current?.status).toBe("cancelled");
    expect(t.reloads()).toBe(1);
  });
  it("annullando un job in esecuzione lascia la ricarica al polling", async () => {
    const t = tracker(summary("running"));
    await cancelAndSync(t.sync, async () => summary("cancelling"));
    expect(t.seen).toEqual(["cancelling"]);
    expect(t.reloads()).toBe(0);
  });
  it("dopo la ripresa ricarica subito il job completo (file azzerati dalla preparazione)", async () => {
    const t = tracker(summary("cancelled"));
    await resumeAndSync(t.sync, async () => summary("queued", "2026-01-01T10:02:00+00:00"));
    expect(t.seen).toEqual(["queued"]);
    expect(t.sync.last.current?.status).toBe("queued");
    expect(t.reloads()).toBe(1);
  });
  it("una ripresa rifiutata non cambia nulla", async () => {
    const t = tracker(summary("failed"));
    await expect(resumeAndSync(t.sync, async () => Promise.reject(new Error("409")))).rejects.toThrow("409");
    expect(t.seen).toEqual([]);
    expect(t.sync.last.current?.status).toBe("failed");
    expect(t.reloads()).toBe(0);
  });
});

describe("needsFullReload", () => {
  const s = (status: JobStatus, updated_at = "2026-01-01T10:00:00+00:00") => ({ status, updated_at });
  it("ricarica solo al termine, non a ogni controllo di un job terminato", () => {
    expect(needsFullReload(s("running"), s("running"))).toBe(false);
    expect(needsFullReload(s("running"), s("completed"))).toBe(true);
    expect(needsFullReload(s("queued"), s("cancelled"))).toBe(true); // annullato mentre era in coda
    expect(needsFullReload(null, s("failed"))).toBe(true);
    expect(needsFullReload(s("failed"), s("failed"))).toBe(false);
  });
  it("si accorge di una ripresa conclusa fra due controlli", () => {
    expect(needsFullReload(s("cancelled"), s("completed", "2026-01-01T10:05:00+00:00"))).toBe(true);
    expect(needsFullReload(s("failed"), s("failed", "2026-01-01T10:05:00+00:00"))).toBe(true);
    expect(needsFullReload(s("failed"), s("queued"))).toBe(false); // tornato attivo: riprende il polling
  });
});

describe("fmtSeconds", () => {
  it("formatta durate brevi e lunghe", () => {
    expect(fmtSeconds(0.2)).toBe("< 1 s");
    expect(fmtSeconds(25)).toBe("25 s");
    expect(fmtSeconds(600)).toBe("10 min");
  });
});
