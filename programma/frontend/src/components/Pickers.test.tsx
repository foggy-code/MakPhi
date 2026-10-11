// @vitest-environment jsdom
import { act, cleanup, render } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { TemplateRef } from "../api/types";
import { ToastProvider } from "../lib/context";
import { apiError, json, mockFetch } from "../test/dom";
import { AUTO_FETCH_DELAY_MS, TemplatePicker } from "./Pickers";

// Template scelto che la libreria non ha (404): il download da RCSB / AlphaFold DB parte solo dopo un'attesa e solo se
// il template è ancora scelto; smontato il selettore, nessun download né aggiornamento.
const ANALYSIS = {
  id: "1ABC", header: { method: "X-RAY", resolution: 2.0, missing_residues: [], title: "PROTEINA" }, meta: { method: "X-RAY" },
  chains: [{ chain: "A", first: 1, last: 100, residues: 100, breaks: [] }], ligands: [],
};

function server() {
  let fetched = false;
  const mock = mockFetch((method, url) => {
    if (method === "GET" && url === "/api/templates") return json([]);
    if (method === "GET" && url === "/api/templates/1ABC") return fetched ? json(ANALYSIS) : apiError(404, "not_found", "Template non trovato.");
    if (method === "POST" && url === "/api/templates/fetch") {
      fetched = true;
      return json({ id: "1ABC", chains: ["A"], cached: false });
    }
    return undefined;
  });
  return { ...mock, downloads: () => mock.count("POST", "/api/templates/fetch") };
}

const picker = (value: TemplateRef[], onChange = vi.fn()) => <ToastProvider><TemplatePicker value={value} onChange={onChange} /></ToastProvider>;

beforeEach(() => vi.useFakeTimers());
afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe("download automatico di un template assente dalla libreria", () => {
  it("dopo l'attesa, se ancora scelto: download da RCSB, analisi e prima catena scelta", async () => {
    const { downloads } = server();
    const onChange = vi.fn();
    render(picker([{ id: "1ABC", chains: [] }], onChange));
    await act(() => vi.advanceTimersByTimeAsync(AUTO_FETCH_DELAY_MS - 100));
    expect(downloads()).toBe(0);
    await act(() => vi.advanceTimersByTimeAsync(200));
    expect(downloads()).toBe(1);
    expect(onChange).toHaveBeenCalledWith([{ id: "1ABC", chains: ["A"] }]);
  });

  it("tolto prima della fine dell'attesa: nessun download", async () => {
    const { downloads } = server();
    const { rerender } = render(picker([{ id: "1ABC", chains: [] }]));
    await act(() => vi.advanceTimersByTimeAsync(100));
    rerender(picker([]));
    await act(() => vi.advanceTimersByTimeAsync(AUTO_FETCH_DELAY_MS * 3));
    expect(downloads()).toBe(0);
  });

  it("selettore smontato durante l'attesa: nessun download e nessuna modifica alla scelta", async () => {
    const { downloads } = server();
    const onChange = vi.fn();
    const { unmount } = render(picker([{ id: "1ABC", chains: [] }], onChange));
    await act(() => vi.advanceTimersByTimeAsync(100));
    unmount();
    await act(() => vi.advanceTimersByTimeAsync(AUTO_FETCH_DELAY_MS * 3));
    expect(downloads()).toBe(0);
    expect(onChange).not.toHaveBeenCalled();
  });
});
