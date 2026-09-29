import { describe, expect, it } from "vitest";
import type { LicenseStatus } from "../api/types";
import { fmtSeconds, licenseWarnings, qmeanRoute } from "./status";

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

describe("fmtSeconds", () => {
  it("formatta durate brevi e lunghe", () => {
    expect(fmtSeconds(0.2)).toBe("< 1 s");
    expect(fmtSeconds(25)).toBe("25 s");
    expect(fmtSeconds(600)).toBe("10 min");
  });
});
