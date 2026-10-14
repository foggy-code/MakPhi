import { afterEach, describe, expect, it, vi } from "vitest";
import { apiError, json, mockFetch } from "../test/dom";
import { api, ApiError, AUTH_EVENT } from "./client";

// Client delle API: identificativi codificati nei percorsi, lettura di file con annullamento, errori del server.
afterEach(() => {
  vi.unstubAllGlobals();
});

describe("identificativi nei percorsi", () => {
  it("progetti, sequenze, documenti, job e pipeline passano da encodeURIComponent come template e file", async () => {
    const { calls } = mockFetch(() => json({}));
    await api.job("a/b?c");
    await api.sequences("p 1");
    await api.document("p#1", "d/1");
    await api.deleteSequence("p1", "s/1");
    await api.cancelJob("j?1");
    await api.pipelineMatrix("j/1", 2, 50);
    expect(calls.map((c) => c.url)).toEqual([
      "/api/jobs/a%2Fb%3Fc",
      "/api/projects/p%201/sequences",
      "/api/projects/p%231/documents/d%2F1",
      "/api/projects/p1/sequences/s%2F1",
      "/api/jobs/j%3F1/cancel",
      "/api/pipeline/j%2F1/matrix?page=2&size=50",
    ]);
    expect(api.jobFileUrl("j/1", "models/m 1.pdb", true)).toBe("/api/jobs/j%2F1/files/models/m%201.pdb?download=true");
    expect(api.archiveUrl("abc123")).toBe("/api/jobs/abc123/archive"); // gli id normali restano uguali
  });
});

describe("api.fileText (struttura per il visualizzatore 3D)", () => {
  it("testo anche se il server dichiara JSON", async () => {
    mockFetch(() => new Response('{"non": "json da interpretare"}', { headers: { "content-type": "application/json" } }));
    await expect(api.fileText("/api/templates/1ABC/file")).resolves.toBe('{"non": "json da interpretare"}');
  });

  it("richiesta annullata: AbortError a chi chiama, non «Server non raggiungibile»", async () => {
    mockFetch(() => undefined);
    const ctrl = new AbortController();
    ctrl.abort();
    await expect(api.fileText("/api/jobs/j/files/m.pdb", ctrl.signal)).rejects.toMatchObject({ name: "AbortError" });
  });

  it("sessione scaduta: evento della schermata di accesso ed errore con il messaggio del server", async () => {
    const required = vi.fn();
    vi.stubGlobal("window", { dispatchEvent: required });
    mockFetch(() => apiError(401, "auth_required", "Accesso richiesto."));
    const error = await api.fileText("/api/jobs/j/files/m.pdb").catch((e) => e);
    expect(error).toBeInstanceOf(ApiError);
    expect(error.message).toBe("Accesso richiesto.");
    expect(required).toHaveBeenCalledTimes(1);
    expect((required.mock.calls[0][0] as Event).type).toBe(AUTH_EVENT);
  });
});

describe("errori del server", () => {
  it("429 rate_limited (email di prova): un errore come gli altri, con il messaggio e l'attesa del server", async () => {
    mockFetch(() => apiError(429, "rate_limited", "Troppe email di prova: riprova fra 25 secondi."));
    const error = await api.testNotify("a@b.it").catch((e) => e);
    expect(error).toBeInstanceOf(ApiError);
    expect(error.status).toBe(429);
    expect(error.code).toBe("rate_limited");
    expect(error.message).toBe("Troppe email di prova: riprova fra 25 secondi.");
  });

  it("logout-all: POST senza corpo particolare", async () => {
    const { calls } = mockFetch(() => json({ ok: true }));
    await expect(api.logoutAll()).resolves.toEqual({ ok: true });
    expect(calls).toMatchObject([{ method: "POST", url: "/api/auth/logout-all" }]);
  });
});
