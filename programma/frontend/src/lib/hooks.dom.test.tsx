// @vitest-environment jsdom
import { act, cleanup, render } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { setVisibility } from "../test/dom";
import { useVisibleInterval } from "./hooks";

// Polling sospeso con la scheda nascosta (Panoramica: CPU, memoria e disco; barra laterale: contatore dei job).
function Poller({ fn, ms }: { fn: () => void; ms: number | null }) {
  useVisibleInterval(fn, ms);
  return null;
}

beforeEach(() => {
  vi.useFakeTimers();
  setVisibility("visible");
});
afterEach(() => {
  cleanup();
  vi.useRealTimers();
  setVisibility("visible");
});

describe("useVisibleInterval", () => {
  it("fermo da nascosta, subito al ritorno, poi al suo ritmo", () => {
    const fn = vi.fn();
    render(<Poller fn={fn} ms={5000} />);
    act(() => vi.advanceTimersByTime(10_000));
    expect(fn).toHaveBeenCalledTimes(2);
    act(() => setVisibility("hidden"));
    act(() => vi.advanceTimersByTime(60_000));
    expect(fn).toHaveBeenCalledTimes(2);
    act(() => setVisibility("visible"));
    expect(fn).toHaveBeenCalledTimes(3);
    act(() => vi.advanceTimersByTime(4999));
    expect(fn).toHaveBeenCalledTimes(3);
    act(() => vi.advanceTimersByTime(1));
    expect(fn).toHaveBeenCalledTimes(4);
  });

  it("con il polling spento (ms null, es. flusso in tempo reale attivo) il ritorno non chiama nulla", () => {
    const fn = vi.fn();
    render(<Poller fn={fn} ms={null} />);
    act(() => setVisibility("hidden"));
    act(() => setVisibility("visible"));
    act(() => vi.advanceTimersByTime(60_000));
    expect(fn).not.toHaveBeenCalled();
  });

  it("chiama sempre l'ultima funzione passata", () => {
    const first = vi.fn();
    const second = vi.fn();
    const { rerender } = render(<Poller fn={first} ms={1000} />);
    rerender(<Poller fn={second} ms={1000} />);
    act(() => vi.advanceTimersByTime(1000));
    expect(first).not.toHaveBeenCalled();
    expect(second).toHaveBeenCalledTimes(1);
  });
});
