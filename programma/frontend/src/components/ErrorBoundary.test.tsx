import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, describe, expect, it } from "vitest";
import { sessionGet, sessionSet } from "../lib/safeStorage";
import { claimStaleReload, ErrorBoundary } from "./ErrorBoundary";

// sessionStorage finto su globalThis (in Node non c'è): bloccato (SecurityError), in sola lettura o funzionante
function blocked() {
  Object.defineProperty(globalThis, "sessionStorage", {
    configurable: true,
    get() {
      throw new DOMException("The operation is insecure.", "SecurityError");
    },
  });
}

function memory(failWrites = false): Map<string, string> {
  const data = new Map<string, string>();
  const storage = {
    getItem: (k: string) => data.get(k) ?? null,
    setItem: (k: string, v: string) => {
      if (failWrites) throw new DOMException("Quota exceeded", "QuotaExceededError");
      data.set(k, v);
    },
    removeItem: (k: string) => void data.delete(k),
  };
  Object.defineProperty(globalThis, "sessionStorage", { configurable: true, value: storage });
  return data;
}

const STALE = "Failed to fetch dynamically imported module: http://127.0.0.1:8765/assets/Quality-abc123.js";

afterEach(() => {
  delete (globalThis as { sessionStorage?: unknown }).sessionStorage;
});

describe("sessionStorage sicuro", () => {
  it("dati del sito bloccati: lettura null, scrittura non riuscita, nessuna eccezione", () => {
    blocked();
    expect(() => globalThis.sessionStorage).toThrow();
    expect(sessionGet("x")).toBeNull();
    expect(sessionSet("x", "1")).toBe(false);
  });

  it("senza sessionStorage (Node) e con la scrittura che fallisce", () => {
    expect(sessionGet("x")).toBeNull();
    expect(sessionSet("x", "1")).toBe(false);
    memory(true);
    expect(sessionSet("x", "1")).toBe(false);
  });

  it("storage funzionante: scrive e rilegge", () => {
    memory();
    expect(sessionSet("x", "1")).toBe(true);
    expect(sessionGet("x")).toBe("1");
  });
});

describe("ricarica per i moduli obsoleti", () => {
  it("una volta, poi non prima di un minuto", () => {
    const data = memory();
    expect(claimStaleReload(STALE, 1_000_000)).toBe(true);
    expect(data.get("makphi.chunk-reload")).toBe("1000000");
    expect(claimStaleReload(STALE, 1_030_000)).toBe(false); // il modulo manca ancora dopo la ricarica: niente ciclo
    expect(claimStaleReload(STALE, 1_060_000)).toBe(true);
  });

  it("segno delle versioni precedenti («1») o illeggibile: si ricarica", () => {
    const data = memory();
    data.set("makphi.chunk-reload", "1");
    expect(claimStaleReload(STALE, 1_000_000)).toBe(true);
    data.set("makphi.chunk-reload", "x");
    expect(claimStaleReload(STALE, 2_000_000)).toBe(true);
  });

  it("dati del sito bloccati: nessuna ricarica automatica (non si potrebbe ricordare di averla fatta)", () => {
    blocked();
    expect(claimStaleReload(STALE)).toBe(false);
    memory(true);
    expect(claimStaleReload(STALE)).toBe(false);
  });

  it("gli altri errori non ricaricano", () => {
    memory();
    expect(claimStaleReload("Cannot read properties of undefined (reading 'chains')")).toBe(false);
    expect(claimStaleReload("Importing a module script failed.")).toBe(true);
  });

  it("il disegno senza errori non tocca lo storage (prima: SecurityError e pagina bianca)", () => {
    blocked();
    expect(renderToStaticMarkup(<ErrorBoundary><p>contenuto</p></ErrorBoundary>)).toBe("<p>contenuto</p>");
  });
});
