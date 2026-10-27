// @vitest-environment jsdom
import { act, cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AUTH_EVENT } from "../api/client";
import { ProjectProvider, ToastProvider } from "../lib/context";
import { json, mockFetch, setVisibility, systemInfo } from "../test/dom";
import { Layout } from "./Layout";

// Barra laterale e barra in alto con gli effetti veri (jsdom): polling del contatore dei job con la scheda nascosta e
// «Esci da tutti i dispositivi» in modalità server. jsdom non ha EventSource: niente flusso, il contatore usa il polling.
const JOBS = "/api/jobs?status=queued%2Crunning%2Ccancelling";

function server(runtime: Record<string, unknown> = {}) {
  return mockFetch((method, url) => {
    if (method === "GET" && url.startsWith("/api/system/info")) return json(systemInfo(runtime));
    if (method === "GET" && url.startsWith("/api/projects")) return json([]);
    if (method === "GET" && url.startsWith(JOBS)) return json([]);
    if (method === "POST" && url === "/api/auth/logout-all") return json({ ok: true });
    return undefined;
  });
}

function renderLayout() {
  return render(
    <MemoryRouter>
      <ToastProvider>
        <ProjectProvider>
          <Layout />
        </ProjectProvider>
      </ToastProvider>
    </MemoryRouter>,
  );
}

beforeEach(() => {
  vi.stubGlobal("EventSource", undefined);
  setVisibility("visible");
});
afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.unstubAllGlobals();
  setVisibility("visible");
});

describe("contatore dei job: polling fermo con la scheda nascosta", () => {
  it("ogni 3 s con la scheda visibile, nessuna richiesta da nascosta, una subito al ritorno e poi di nuovo ogni 3 s", async () => {
    vi.useFakeTimers();
    const { count } = server();
    renderLayout();
    const polls = () => count("GET", JOBS);
    await act(() => vi.advanceTimersByTimeAsync(3000));
    expect(polls()).toBe(1);
    await act(() => vi.advanceTimersByTimeAsync(3000));
    expect(polls()).toBe(2);

    act(() => setVisibility("hidden"));
    await act(() => vi.advanceTimersByTimeAsync(30_000));
    expect(polls()).toBe(2);

    act(() => setVisibility("visible"));
    await act(() => vi.advanceTimersByTimeAsync(0));
    expect(polls()).toBe(3);
    await act(() => vi.advanceTimersByTimeAsync(3000));
    expect(polls()).toBe(4);
  });
});

describe("Esci da tutti i dispositivi (modalità server)", () => {
  it("in locale (accesso libero) il pulsante non c'è", async () => {
    server({ auth: false });
    renderLayout();
    expect(await screen.findByText(/job in parallelo/)).toBeTruthy(); // informazioni di sistema arrivate
    expect(screen.queryByRole("button", { name: /Esci da tutti i dispositivi/ })).toBeNull();
  });

  it("conferma → POST /api/auth/logout-all → notifica e schermata di accesso (evento auth-required)", async () => {
    const { count } = server({ auth: true });
    const required = vi.fn();
    window.addEventListener(AUTH_EVENT, required);
    try {
      renderLayout();
      await userEvent.click(await screen.findByRole("button", { name: /Esci da tutti i dispositivi/ }));
      const dialog = screen.getByRole("dialog");
      expect(dialog.textContent).toContain("compresa questa");
      expect(count("POST", "/api/auth/logout-all")).toBe(0); // niente prima della conferma
      await userEvent.click(within(dialog).getByRole("button", { name: "Esci da tutti" }));
      expect(await screen.findByText(/Tutte le sessioni sono state chiuse/)).toBeTruthy();
      expect(count("POST", "/api/auth/logout-all")).toBe(1);
      expect(required).toHaveBeenCalledTimes(1);
      expect(screen.queryByRole("dialog")).toBeNull();
    } finally {
      window.removeEventListener(AUTH_EVENT, required);
    }
  });

  it("Annulla: nessuna richiesta, la sessione resta", async () => {
    const { count } = server({ auth: true });
    renderLayout();
    await userEvent.click(await screen.findByRole("button", { name: /Esci da tutti i dispositivi/ }));
    await userEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Annulla" }));
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(count("POST", "/api/auth/logout-all")).toBe(0);
  });

  it("errore del server: notifica con il suo messaggio, nessun ritorno all'accesso", async () => {
    mockFetch((method, url) => {
      if (method === "GET" && url.startsWith("/api/system/info")) return json(systemInfo({ auth: true }));
      if (method === "GET" && url.startsWith("/api/projects")) return json([]);
      if (method === "GET" && url.startsWith(JOBS)) return json([]);
      if (method === "POST" && url === "/api/auth/logout-all") return json({ error: { code: "internal_error", message: "Errore interno (codice ab12cd34): i dettagli sono nel log di MakPhi." } }, 500);
      return undefined;
    });
    const required = vi.fn();
    window.addEventListener(AUTH_EVENT, required);
    try {
      renderLayout();
      await userEvent.click(await screen.findByRole("button", { name: /Esci da tutti i dispositivi/ }));
      await userEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Esci da tutti" }));
      expect(await screen.findByText(/Errore interno \(codice ab12cd34\)/)).toBeTruthy();
      expect(required).not.toHaveBeenCalled();
    } finally {
      window.removeEventListener(AUTH_EVENT, required);
    }
  });
});
