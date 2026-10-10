// @vitest-environment jsdom
import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ToastProvider } from "../lib/context";
import { apiError, json, mockFetch } from "../test/dom";
import { TemplatePicker } from "./Pickers";

// «Da file» del selettore dei template con un id già in libreria: il server risponde 409 template_exists, si chiede
// conferma; «Sostituisci» ripete l'invio con overwrite, «Annulla» non invia altro.
const EXISTS = "Il template 1ABC è già nella libreria.";
const TEMPLATE = { id: "1ABC", chains: ["A"], format: "pdb", size: 1000, source: "upload", title: "", added_at: "2026-10-10T10:00:00Z" };

function server() {
  return mockFetch((method, url, init) => {
    if (method === "GET" && url === "/api/templates") return json([]);
    if (method === "POST" && url === "/api/templates/upload") {
      const form = init.body as FormData;
      return form.get("overwrite") === "true" ? json(TEMPLATE) : apiError(409, "template_exists", EXISTS);
    }
    return undefined;
  });
}

function uploads(calls: ReturnType<typeof server>["calls"]) {
  return calls.filter((c) => c.method === "POST" && c.url === "/api/templates/upload").map((c) => {
    const form = c.init.body as FormData;
    return { file: (form.get("file") as File).name, overwrite: form.get("overwrite") };
  });
}

async function chooseFile() {
  const onChange = vi.fn();
  render(<ToastProvider><TemplatePicker value={[]} onChange={onChange} /></ToastProvider>);
  const input = document.querySelector<HTMLInputElement>('input[type="file"]');
  if (!input) throw new Error("manca il campo file di «Da file»");
  await userEvent.upload(input, new File(["ATOM\nEND\n"], "1ABC.pdb", { type: "chemical/x-pdb" }));
  return { onChange, dialog: await screen.findByRole("dialog") };
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("sostituzione di un template dal selettore", () => {
  it("409 → domanda con il messaggio del server → Sostituisci → secondo invio con overwrite", async () => {
    const { calls } = server();
    const { onChange, dialog } = await chooseFile();
    expect(dialog.textContent).toContain(EXISTS);
    expect(dialog.textContent).toContain("1ABC.pdb");
    expect(uploads(calls)).toEqual([{ file: "1ABC.pdb", overwrite: null }]);

    await userEvent.click(within(dialog).getByRole("button", { name: "Sostituisci" }));
    await waitFor(() => expect(onChange).toHaveBeenCalledWith([{ id: "1ABC", chains: ["A"] }]));
    expect(uploads(calls)).toEqual([{ file: "1ABC.pdb", overwrite: null }, { file: "1ABC.pdb", overwrite: "true" }]);
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(screen.getByRole("status").textContent).toContain("Template 1ABC sostituito nella libreria");
  });

  it("Annulla: nessun secondo invio, libreria e scelta invariate", async () => {
    const { calls } = server();
    const { onChange, dialog } = await chooseFile();
    await userEvent.click(within(dialog).getByRole("button", { name: "Annulla" }));
    expect(screen.queryByRole("dialog")).toBeNull();
    // un eventuale invio partirebbe subito dopo il clic: si lascia passare un giro di eventi
    await new Promise((resolve) => setTimeout(resolve, 20));
    expect(uploads(calls)).toHaveLength(1);
    expect(onChange).not.toHaveBeenCalled();
  });
});
