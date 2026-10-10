// @vitest-environment jsdom
import { act, cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { COPY_FAILED } from "../lib/clipboard";
import { ToastProvider } from "../lib/context";
import { CopyButton } from "./ui";

// Pulsante «Copia»: Clipboard API quando c'è; senza (server in HTTP, non localhost) il ripiego con una textarea e
// document.execCommand("copy"); se nemmeno quello riesce, «Non copiato» e una notifica d'errore.
function setClipboard(value: unknown) {
  Object.defineProperty(navigator, "clipboard", { configurable: true, value });
}
function setExecCommand(fn: ((command: string) => boolean) | undefined) {
  Object.defineProperty(document, "execCommand", { configurable: true, writable: true, value: fn });
}

beforeEach(() => {
  setClipboard(undefined); // come jsdom e un browser su HTTP: niente Clipboard API
  setExecCommand(undefined);
});
afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

describe("CopyButton", () => {
  it("con la Clipboard API: writeText con il testo, nessun ripiego", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    const exec = vi.fn(() => true);
    setClipboard({ writeText });
    setExecCommand(exec);
    render(<CopyButton text="MKTAYIAK" />);
    await userEvent.click(screen.getByRole("button", { name: /Copia/ }));
    expect(writeText).toHaveBeenCalledWith("MKTAYIAK");
    expect(exec).not.toHaveBeenCalled();
    expect(await screen.findByRole("button", { name: /Copiato/ })).toBeTruthy();
  });

  it("senza Clipboard API: textarea temporanea con il testo, execCommand('copy'), poi rimossa; il focus torna al pulsante", async () => {
    let copied: string | null = null;
    const exec = vi.fn((command: string) => {
      const area = document.activeElement as HTMLTextAreaElement;
      copied = area.value.slice(area.selectionStart, area.selectionEnd); // il testo selezionato è quello che si copia
      return command === "copy";
    });
    setExecCommand(exec);
    render(<CopyButton text={"riga 1\nriga 2"} label="Copia i dettagli" />);
    const button = screen.getByRole("button", { name: /Copia i dettagli/ });
    await userEvent.click(button);
    expect(exec).toHaveBeenCalledWith("copy");
    expect(copied).toBe("riga 1\nriga 2");
    expect(document.querySelector("textarea")).toBeNull();
    expect(document.activeElement).toBe(button);
    expect(await screen.findByRole("button", { name: /Copiato/ })).toBeTruthy();
  });

  it("writeText rifiutato: ripiego con execCommand", async () => {
    const writeText = vi.fn().mockRejectedValue(new DOMException("Document is not focused", "NotAllowedError"));
    const exec = vi.fn(() => true);
    setClipboard({ writeText });
    setExecCommand(exec);
    render(<CopyButton text="ACGT" />);
    await userEvent.click(screen.getByRole("button", { name: /Copia/ }));
    expect(writeText).toHaveBeenCalled();
    expect(exec).toHaveBeenCalledWith("copy");
    expect(await screen.findByRole("button", { name: /Copiato/ })).toBeTruthy();
  });

  it("niente riesce: «Non copiato» con la spiegazione, notifica d'errore nell'app, poi torna «Copia»", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    setClipboard({ writeText: vi.fn().mockRejectedValue(new Error("negato")) });
    setExecCommand(() => false);
    render(<ToastProvider><CopyButton text="ACGT" /></ToastProvider>);
    await userEvent.click(screen.getByRole("button", { name: /Copia/ }));
    const failed = await screen.findByRole("button", { name: /Non copiato/ });
    expect(failed.getAttribute("title")).toBe(COPY_FAILED);
    expect(screen.getByRole("status").textContent).toContain(COPY_FAILED);
    await act(() => vi.advanceTimersByTimeAsync(4100));
    expect(screen.getByRole("button", { name: /^Copia$/ })).toBeTruthy();
  });

  it("fuori dall'app (senza ToastProvider) l'errore resta sul pulsante, senza eccezioni", async () => {
    setExecCommand(() => {
      throw new Error("execCommand non disponibile");
    });
    render(<CopyButton text="ACGT" />);
    await userEvent.click(screen.getByRole("button", { name: /Copia/ }));
    expect(await screen.findByRole("button", { name: /Non copiato/ })).toBeTruthy();
  });
});
