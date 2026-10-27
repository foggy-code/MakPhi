/**
 * Strumenti per i test di interfaccia (file con `// @vitest-environment jsdom`, @testing-library/react): risposte finte
 * del server al posto di `fetch` e visibilità della scheda. Solo per i test: nessun componente lo importa.
 */
import { vi } from "vitest";

export interface FetchCall {
  url: string;
  method: string;
  init: RequestInit;
}

/** Risposta JSON come quelle del server (anche gli errori `{"error": {"code", "message"}}`). */
export function json(data: unknown, status = 200): Response {
  return new Response(JSON.stringify(data), { status, headers: { "content-type": "application/json" } });
}

/** Errore del server nel formato di MakPhi. */
export function apiError(status: number, code: string, message: string): Response {
  return json({ error: { code, message } }, status);
}

/**
 * Sostituisce `fetch`: `route` risponde a ogni chiamata (metodo e indirizzo); una chiamata non prevista riceve un 404 e
 * resta comunque in `calls`, che i test controllano.
 */
export function mockFetch(route: (method: string, url: string, init: RequestInit) => Response | Promise<Response> | undefined) {
  const calls: FetchCall[] = [];
  const fn = vi.fn(async (input: RequestInfo | URL, init: RequestInit = {}) => {
    const url = typeof input === "string" ? input : input instanceof URL ? input.href : input.url;
    const method = (init.method ?? "GET").toUpperCase();
    calls.push({ url, method, init });
    if (init.signal?.aborted) throw new DOMException("Richiesta annullata", "AbortError");
    return (await route(method, url, init)) ?? apiError(404, "not_found", `Non previsto nel test: ${method} ${url}`);
  });
  vi.stubGlobal("fetch", fn);
  return { fn, calls, count: (method: string, prefix: string) => calls.filter((c) => c.method === method && c.url.startsWith(prefix)).length };
}

/** Scheda visibile o nascosta, con l'evento `visibilitychange` che la pagina ascolta. */
export function setVisibility(state: "visible" | "hidden") {
  Object.defineProperty(document, "visibilityState", { configurable: true, get: () => state });
  Object.defineProperty(document, "hidden", { configurable: true, get: () => state === "hidden" });
  document.dispatchEvent(new Event("visibilitychange"));
}

/** Informazioni di sistema minime per la barra laterale (MODELLER pronto); `runtime` le completa (es. `auth`). */
export function systemInfo(runtime: Record<string, unknown> = {}) {
  return {
    version: "4.2.0",
    workers: 2,
    python: "3.12",
    modeller: { available: true, version: "10.5", python: "/usr/bin/python3", python_version: "3.12", attempts: [] },
    runtime: { container: true, image: "makphi:test", arch: "x86_64", auth: false, allow_shutdown: true, modeller_key: "file", ...runtime },
  };
}
