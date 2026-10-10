// @vitest-environment jsdom
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ErrorBoundary } from "./ErrorBoundary";

// ErrorBoundary con gli effetti veri (componentDidCatch, pulsanti): sessionStorage che lancia SecurityError (dati del
// sito bloccati) non deve trasformare l'errore della pagina in una pagina bianca né in ricariche a ripetizione.
const STALE = "Failed to fetch dynamically imported module: http://127.0.0.1:8765/assets/Quality-abc123.js";

let failures = 0;
function Boom({ message }: { message: string }) {
  if (failures > 0) {
    failures--;
    throw new Error(message);
  }
  return <p>pagina disegnata</p>;
}

function blockStorage() {
  const descriptor = Object.getOwnPropertyDescriptor(window, "sessionStorage");
  Object.defineProperty(window, "sessionStorage", {
    configurable: true,
    get() {
      throw new DOMException("The operation is insecure.", "SecurityError");
    },
  });
  return () => {
    if (descriptor) Object.defineProperty(window, "sessionStorage", descriptor);
    else delete (window as { sessionStorage?: unknown }).sessionStorage;
  };
}

let restore: () => void = () => undefined;
let consoleError: ReturnType<typeof vi.spyOn>;
beforeEach(() => {
  restore = blockStorage();
  consoleError = vi.spyOn(console, "error").mockImplementation(() => undefined); // React e ErrorBoundary scrivono l'errore
});
afterEach(() => {
  cleanup();
  restore();
  consoleError.mockRestore();
  failures = 0;
});

describe("ErrorBoundary con sessionStorage che lancia", () => {
  it("lo storage lancia davvero (come con i dati del sito bloccati)", () => {
    expect(() => window.sessionStorage).toThrow();
  });

  it("modulo obsoleto: mostra l'errore e non ricarica (senza storage non si ricorderebbe di averlo fatto)", () => {
    failures = 2; // anche il secondo tentativo di React fallisce
    render(<ErrorBoundary><Boom message={STALE} /></ErrorBoundary>);
    const alert = screen.getByRole("alert");
    expect(alert.textContent).toContain("Si è verificato un errore nell'interfaccia");
    expect(alert.textContent).toContain(STALE);
    expect(screen.getByRole("button", { name: /Ricarica la pagina/ })).toBeTruthy();
    // una ricarica in jsdom finirebbe nel log come navigazione non implementata
    const logged = consoleError.mock.calls.map((c: unknown[]) => c.map(String).join(" ")).join("\n");
    expect(logged).not.toMatch(/Not implemented: navigation/);
  });

  it("«Riprova» ridisegna la pagina; «Copia i dettagli» funziona anche senza Clipboard API", async () => {
    failures = 2;
    const exec = vi.fn(() => true);
    Object.defineProperty(document, "execCommand", { configurable: true, writable: true, value: exec });
    render(<ErrorBoundary><Boom message="Cannot read properties of undefined (reading 'chains')" /></ErrorBoundary>);
    await userEvent.click(screen.getByRole("button", { name: /Copia i dettagli/ }));
    expect(exec).toHaveBeenCalledWith("copy");
    expect(await screen.findByRole("button", { name: /Copiato/ })).toBeTruthy();
    await userEvent.click(screen.getByRole("button", { name: "Riprova" }));
    expect(screen.getByText("pagina disegnata")).toBeTruthy();
    expect(screen.queryByRole("alert")).toBeNull();
  });
});
