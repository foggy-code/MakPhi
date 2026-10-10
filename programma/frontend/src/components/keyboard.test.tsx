// @vitest-environment jsdom
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ToastProvider, useToast } from "../lib/context";
import { DropZone, rowActivate } from "./ui";

// Elementi cliccabili raggiungibili anche da tastiera: notifiche, righe selezionabili delle tabelle (documenti PIR, job,
// modelli, sequenze, template), riquadro per il file.
afterEach(cleanup);

function PushButton() {
  const toast = useToast();
  return <button type="button" onClick={() => toast.push("Impostazioni salvate")}>Salva</button>;
}

describe("notifiche", () => {
  it("si chiudono da tastiera con il pulsante «Chiudi la notifica» (Tab, Invio)", async () => {
    const user = userEvent.setup();
    render(<ToastProvider><PushButton /></ToastProvider>);
    await user.click(screen.getByRole("button", { name: "Salva" }));
    expect(screen.getByRole("status").textContent).toContain("Impostazioni salvate");
    await user.tab();
    expect(document.activeElement).toBe(screen.getByRole("button", { name: "Chiudi la notifica" }));
    await user.keyboard("{Enter}");
    expect(screen.getByRole("status").textContent).not.toContain("Impostazioni salvate");
  });

  it("il clic sulla notifica la chiude come prima", async () => {
    const user = userEvent.setup();
    render(<ToastProvider><PushButton /></ToastProvider>);
    await user.click(screen.getByRole("button", { name: "Salva" }));
    await user.click(screen.getByText("Impostazioni salvate"));
    expect(screen.queryByText("Impostazioni salvate")).toBeNull();
  });
});

describe("righe selezionabili (rowActivate)", () => {
  function Rows({ onPick, onDelete }: { onPick: (id: string) => void; onDelete: (id: string) => void }) {
    return (
      <table className="table">
        <tbody>
          {["a", "b"].map((id) => (
            <tr key={id} className="clickable" {...rowActivate(() => onPick(id))}>
              <td>riga {id}</td>
              <td><button type="button" onClick={(e) => { e.stopPropagation(); onDelete(id); }}>Elimina {id}</button></td>
            </tr>
          ))}
        </tbody>
      </table>
    );
  }

  it("Tab raggiunge la riga, Invio e Spazio la scelgono; Invio su un pulsante della riga resta al pulsante", async () => {
    const user = userEvent.setup();
    const onPick = vi.fn();
    const onDelete = vi.fn();
    render(<Rows onPick={onPick} onDelete={onDelete} />);
    await user.tab();
    expect(document.activeElement?.textContent).toContain("riga a");
    await user.keyboard("{Enter}");
    expect(onPick).toHaveBeenLastCalledWith("a");
    await user.tab(); // pulsante della riga a
    await user.keyboard("{Enter}");
    expect(onDelete).toHaveBeenCalledWith("a");
    expect(onPick).toHaveBeenCalledTimes(1);
    await user.tab(); // riga b
    await user.keyboard(" ");
    expect(onPick).toHaveBeenLastCalledWith("b");
  });
});

describe("riquadro per il file (DropZone)", () => {
  it("è un pulsante raggiungibile con Tab: Invio apre la scelta del file", async () => {
    const user = userEvent.setup();
    const onFile = vi.fn();
    render(<DropZone accept=".pdb" onFile={onFile} label="Trascina un file .pdb oppure fai clic" />);
    const zone = screen.getByRole("button", { name: /Trascina un file/ });
    const input = document.querySelector<HTMLInputElement>('input[type="file"]')!;
    const opened = vi.fn();
    input.addEventListener("click", opened);
    await user.tab();
    expect(document.activeElement).toBe(zone);
    await user.keyboard("{Enter}");
    expect(opened).toHaveBeenCalled();
  });
});
