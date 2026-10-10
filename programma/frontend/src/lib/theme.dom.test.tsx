// @vitest-environment jsdom
import { act, cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { useDocumentTheme, viewerBackground, VIEWER_BACKGROUND } from "./theme";

// Sfondo del visualizzatore 3D (come lo calcola Viewer3D): segue <html data-theme> anche quando cambia dopo il primo
// disegno.
function Probe() {
  return <span data-testid="bg">{viewerBackground(useDocumentTheme())}</span>;
}

afterEach(() => {
  cleanup();
  delete document.documentElement.dataset.theme;
});

describe("sfondo del visualizzatore 3D", () => {
  it("scuro senza tema, chiaro e di nuovo scuro al cambio di data-theme", async () => {
    render(<Probe />);
    const bg = screen.getByTestId("bg");
    expect(bg.textContent).toBe(VIEWER_BACKGROUND.dark);
    await act(async () => {
      document.documentElement.dataset.theme = "light";
    });
    expect(bg.textContent).toBe(VIEWER_BACKGROUND.light);
    await act(async () => {
      document.documentElement.dataset.theme = "dark";
    });
    expect(bg.textContent).toBe(VIEWER_BACKGROUND.dark);
  });

  it("la variabile --viewer-bg del foglio di stile, quando c'è, ha la precedenza", () => {
    document.documentElement.style.setProperty("--viewer-bg", "#123456");
    try {
      render(<Probe />);
      expect(screen.getByTestId("bg").textContent).toBe("#123456");
    } finally {
      document.documentElement.style.removeProperty("--viewer-bg");
    }
  });
});
