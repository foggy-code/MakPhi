// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { TOAST_MS, TOAST_RESUME_MS, ToastProvider, useToast } from "./context";

// Notifiche che si chiudono da sole, ma non mentre le si legge (puntatore sopra) o le si chiude da tastiera (focus sul
// pulsante «Chiudi la notifica»): il tempo si ferma e riprende da quello che restava, almeno TOAST_RESUME_MS. Eventi
// sincroni (fireEvent, focus()): user-event con i timer finti di vitest aspetterebbe un setTimeout che non arriva mai.
beforeEach(() => {
  vi.useFakeTimers();
});
afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

function Buttons() {
  const toast = useToast();
  return (
    <>
      <button type="button" onClick={() => toast.push("Impostazioni salvate")}>Salva</button>
      <button type="button" onClick={() => toast.error(new Error("Server non raggiungibile"))}>Errore</button>
      <button type="button">Altro</button>
    </>
  );
}

const setup = () => render(<ToastProvider><Buttons /></ToastProvider>);
const elapse = (ms: number) => act(() => vi.advanceTimersByTime(ms));
const shown = (text: string) => screen.queryByText(text) !== null;
const button = (name: string) => screen.getByRole("button", { name });
const click = (name: string) => fireEvent.click(button(name));
const hover = (text: string) => fireEvent.mouseEnter(screen.getByText(text).closest(".toast") as HTMLElement);
const unhover = (text: string) => fireEvent.mouseLeave(screen.getByText(text).closest(".toast") as HTMLElement);
const focus = (name: string) => act(() => button(name).focus());

describe("chiusura automatica delle notifiche", () => {
  it("senza interazione: 4 s le notifiche, 8 s gli errori", () => {
    setup();
    click("Salva");
    click("Errore");
    elapse(TOAST_MS.ok - 1);
    expect(shown("Impostazioni salvate")).toBe(true);
    elapse(1);
    expect(shown("Impostazioni salvate")).toBe(false);
    expect(shown("Server non raggiungibile")).toBe(true);
    elapse(TOAST_MS.err - TOAST_MS.ok);
    expect(shown("Server non raggiungibile")).toBe(false);
  });

  it("con il puntatore sopra non si chiude; uscendo riprende con il minimo se restava poco", () => {
    setup();
    click("Salva");
    elapse(3000); // resta 1 s
    hover("Impostazioni salvate");
    elapse(60_000);
    expect(shown("Impostazioni salvate")).toBe(true);
    unhover("Impostazioni salvate");
    elapse(TOAST_RESUME_MS - 1);
    expect(shown("Impostazioni salvate")).toBe(true);
    elapse(1);
    expect(shown("Impostazioni salvate")).toBe(false);
  });

  it("uscendo riprende con il tempo che restava, se è più del minimo", () => {
    setup();
    click("Errore");
    elapse(1000); // restano 7 s
    hover("Server non raggiungibile");
    elapse(10_000);
    unhover("Server non raggiungibile");
    elapse(TOAST_MS.err - 1000 - 1);
    expect(shown("Server non raggiungibile")).toBe(true);
    elapse(1);
    expect(shown("Server non raggiungibile")).toBe(false);
  });

  it("con il focus sul pulsante «Chiudi la notifica» non si chiude; quando il focus esce riprende", () => {
    setup();
    click("Errore");
    elapse(7000);
    focus("Chiudi la notifica");
    elapse(60_000);
    expect(shown("Server non raggiungibile")).toBe(true);
    focus("Altro"); // il focus esce dalla notifica
    elapse(TOAST_RESUME_MS - 1);
    expect(shown("Server non raggiungibile")).toBe(true);
    elapse(1);
    expect(shown("Server non raggiungibile")).toBe(false);
  });

  it("puntatore e focus insieme: finché resta uno dei due la notifica aspetta", () => {
    setup();
    click("Salva");
    focus("Chiudi la notifica");
    hover("Impostazioni salvate");
    unhover("Impostazioni salvate");
    elapse(60_000);
    expect(shown("Impostazioni salvate")).toBe(true); // il focus è ancora dentro
    hover("Impostazioni salvate");
    act(() => button("Chiudi la notifica").blur());
    elapse(60_000);
    expect(shown("Impostazioni salvate")).toBe(true); // ora c'è il puntatore
    unhover("Impostazioni salvate");
    elapse(TOAST_MS.ok);
    expect(shown("Impostazioni salvate")).toBe(false);
  });

  it("la chiusura a mano funziona anche durante la pausa, e la notifica chiusa non lascia timer", () => {
    setup();
    click("Salva");
    hover("Impostazioni salvate");
    click("Chiudi la notifica");
    expect(shown("Impostazioni salvate")).toBe(false);
    expect(vi.getTimerCount()).toBe(0);
  });
});
