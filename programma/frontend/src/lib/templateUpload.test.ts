import { afterEach, describe, expect, it, vi } from "vitest";
import { api, ApiError } from "../api/client";
import { replaceQuestion, TEMPLATE_EXISTS } from "./templateUpload";

const exists = new ApiError("Il template 1UBQ è già nella libreria.", 409, TEMPLATE_EXISTS);

describe("sostituzione di un template caricato", () => {
  it("409 template_exists al primo tentativo: si chiede conferma con il messaggio del server", () => {
    expect(replaceQuestion(exists, false)).toBe("Il template 1UBQ è già nella libreria.");
  });

  it("dopo «Sostituisci» (overwrite) non si chiede una seconda volta", () => {
    expect(replaceQuestion(exists, true)).toBeNull();
  });

  it("gli altri errori restano errori", () => {
    expect(replaceQuestion(new ApiError("File troppo grande", 413, "too_large"), false)).toBeNull();
    expect(replaceQuestion(new ApiError("Conflitto", 409, "conflict"), false)).toBeNull();
    expect(replaceQuestion(new ApiError("Errore 400", 400, TEMPLATE_EXISTS), false)).toBeNull();
    expect(replaceQuestion(new Error("rete"), false)).toBeNull();
    expect(replaceQuestion("409", false)).toBeNull();
  });
});

describe("POST /api/templates/upload", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  /** fetch finto: registra il modulo inviato e risponde con lo stato e il corpo dati */
  function stubFetch(status: number, body: unknown) {
    const sent: FormData[] = [];
    vi.stubGlobal("fetch", async (_path: string, init: RequestInit) => {
      sent.push(init.body as FormData);
      return new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });
    });
    return sent;
  }
  const file = new File(["ATOM"], "1UBQ.pdb");

  it("campo overwrite solo per la sostituzione", async () => {
    const sent = stubFetch(200, { id: "1UBQ" });
    await api.uploadTemplate(file);
    await api.uploadTemplate(file, { overwrite: true });
    expect(sent[0].get("overwrite")).toBeNull();
    expect(sent[1].get("overwrite")).toBe("true");
    expect(sent[1].get("template_id")).toBeNull();
  });

  it("il 409 del server diventa la domanda di conferma", async () => {
    stubFetch(409, { error: { code: "template_exists", message: "Il template 1UBQ è già nella libreria." } });
    const err = await api.uploadTemplate(file).catch((e: unknown) => e);
    expect(replaceQuestion(err, false)).toBe("Il template 1UBQ è già nella libreria.");
  });
});
