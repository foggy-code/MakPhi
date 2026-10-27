// @vitest-environment jsdom
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { Settings } from "../api/types";
import { ProjectProvider, ToastProvider } from "../lib/context";
import { json, mockFetch, systemInfo } from "../test/dom";
import SettingsPage from "./Settings";

// Impostazioni: interprete di MODELLER bloccato in modalità server (come l'indirizzo di ESMFold) e spazio massimo
// della cache dei calcoli.
const SETTINGS: Settings = {
  contact_email: "", modeller_python: "/opt/python/bin/python3", max_workers: 0, job_timeout_minutes: 120, max_sequence_length: 5000, qmean_remote: false,
  qmean_mode: "remote", qmean_fallback_remote: false, qmean_uniclust: "", qmean_timeout_minutes: 60, default_search_engine: "rcsb",
  keep_intermediates: false, procheck_plots: "all", uploads_retention_days: 7, compute_cache: true, compute_cache_max_mb: 2048,
  esmfold_remote: false, esmfold_url: "",
};

function server(runtime: Record<string, unknown>) {
  return mockFetch((method, url, init) => {
    if (method === "GET" && url === "/api/settings") return json(SETTINGS);
    if (method === "PUT" && url === "/api/settings") return json({ ...SETTINGS, ...JSON.parse(String(init.body)) });
    if (method === "GET" && url.startsWith("/api/system/info")) return json(systemInfo(runtime));
    if (method === "GET" && url === "/api/system/storage") return json({ jobs: 0, compute_cache: 1024 });
    if (method === "GET" && url.startsWith("/api/projects")) return json([]);
    if (method === "GET" && url === "/api/notify") return json({ configured: false, source: "none", error: null });
    return undefined;
  });
}

function renderSettings(path = "/settings") {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <ToastProvider><ProjectProvider><SettingsPage /></ProjectProvider></ToastProvider>
    </MemoryRouter>,
  );
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("interprete di MODELLER (esecuzione nativa)", () => {
  it("modalità server: campo in sola lettura con la nota dell'amministratore, come ESMFold", async () => {
    server({ container: false, auth: true });
    renderSettings();
    // la nota compare con le informazioni di sistema (modalità server): da lì il campo è bloccato
    await screen.findByText(/In modalità server lo imposta l'amministratore sul server \(variabile d'ambiente MAKPHI_MODELLER_PYTHON\)/);
    expect(document.body.textContent).not.toMatch(/MAKPHI_MODELLER_PYTHON in server\.env/); // in esecuzione nativa server.env non conta
    const field = screen.getByDisplayValue("/opt/python/bin/python3");
    expect((field as HTMLInputElement).readOnly).toBe(true);
    expect((screen.getByPlaceholderText(/esm/i) as HTMLInputElement).readOnly).toBe(true);
  });

  it("modalità locale: modificabile", async () => {
    server({ container: false, auth: false });
    renderSettings();
    const field = await screen.findByDisplayValue("/opt/python/bin/python3");
    await screen.findByText(/MODELLER 10\.5 pronto/); // informazioni di sistema arrivate
    expect((field as HTMLInputElement).readOnly).toBe(false);
  });
});

describe("spazio massimo della cache dei calcoli", () => {
  it("campo in MB (100–102400) accanto alla cache dei calcoli, salvato con le altre impostazioni", async () => {
    const { calls } = server({ container: true, auth: false });
    renderSettings("/settings?tab=storage");
    const field = await screen.findByLabelText(/Spazio massimo della cache dei calcoli/) as HTMLInputElement;
    expect(field.value).toBe("2048");
    expect(field.min).toBe("100");
    expect(field.max).toBe("102400");
    await userEvent.clear(field);
    await userEvent.type(field, "4096");
    await userEvent.click(screen.getByRole("button", { name: /Salva/ }));
    const put = calls.find((c) => c.method === "PUT" && c.url === "/api/settings");
    expect(JSON.parse(String(put?.init.body)).compute_cache_max_mb).toBe(4096);
  });
});

describe("lunghezza massima della sequenza", () => {
  it("campo in residui (100–9999) fra le impostazioni di esecuzione, salvato con le altre", async () => {
    const { calls } = server({ container: true, auth: false });
    renderSettings("/settings");
    const field = await screen.findByLabelText(/Lunghezza massima della sequenza/) as HTMLInputElement;
    expect(field.value).toBe("5000");
    expect(field.min).toBe("100");
    expect(field.max).toBe("9999");
    await userEvent.clear(field);
    await userEvent.type(field, "8000");
    await userEvent.click(screen.getByRole("button", { name: /Salva/ }));
    const put = calls.find((c) => c.method === "PUT" && c.url === "/api/settings");
    expect(JSON.parse(String(put?.init.body)).max_sequence_length).toBe(8000);
  });
});
