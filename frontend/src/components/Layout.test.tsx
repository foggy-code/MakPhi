import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it } from "vitest";
import { ProjectProvider, ToastProvider } from "../lib/context";
import Dashboard from "../pages/Dashboard";
import css from "../styles.css?raw";
import { Layout } from "./Layout";

// Schermi stretti (≤ 760 px, telefono): markup statico (react-dom/server) e regole del foglio di stile, senza browser.
const render = (node: React.ReactNode) =>
  renderToStaticMarkup(<MemoryRouter><ToastProvider><ProjectProvider>{node}</ProjectProvider></ToastProvider></MemoryRouter>);

/** Testo dei blocchi `@media (max-width: <px>px)` del foglio di stile. */
function media(px: number): string {
  const head = `@media (max-width: ${px}px)`;
  const blocks: string[] = [];
  for (let at = css.indexOf(head); at >= 0; at = css.indexOf(head, at + 1)) {
    const start = css.indexOf("{", at);
    for (let i = start, depth = 0; i < css.length; i++) {
      if (css[i] === "{") depth++;
      else if (css[i] === "}" && --depth === 0) {
        blocks.push(css.slice(start + 1, i));
        break;
      }
    }
  }
  return blocks.join("\n");
}

/** Dichiarazioni della regola con esattamente quel selettore (anche in un elenco di selettori) in `text`. */
function rule(text: string, selector: string): string {
  const escaped = selector.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const found = new RegExp(`(?:^|[}\\n,])\\s*(?:[^{}]*,\\s*)?${escaped}\\s*(?:,[^{}]*)?\\{([^}]*)\\}`).exec(text);
  return found ? found[1].replace(/\s+/g, " ") : "";
}

describe("navigazione sugli schermi stretti", () => {
  it("pulsante del menu nella barra in alto che apre la barra laterale (pagine, tema, Tasti, Esci)", () => {
    const html = render(<Layout />);
    expect(html).toMatch(/<button[^>]*class="[^"]*nav-toggle[^"]*"[^>]*aria-label="Menu"[^>]*aria-expanded="false"[^>]*aria-controls="sidebar"/);
    expect(html).toContain('<aside class="sidebar" id="sidebar">');
    expect(html).toContain('aria-label="Chiudi il menu"');
    const aside = html.slice(html.indexOf("<aside"), html.indexOf("</aside>"));
    for (const what of ['href="/pipeline"', 'href="/compare"', 'href="/settings"', 'aria-label="Tema"', "Tasti"]) expect(aside).toContain(what);
    expect(html).not.toContain("nav-backdrop"); // chiuso all'inizio
  });

  it("il pannello sostituisce la barra laterale nascosta, il pulsante compare solo lì", () => {
    expect(rule(css, ".nav-toggle")).toContain("display: none");
    const phone = media(760);
    expect(rule(phone, ".sidebar")).toContain("display: none");
    expect(rule(phone, ".nav-toggle")).toContain("display: inline-flex");
    const drawer = rule(phone, ".app.nav-open .sidebar");
    expect(drawer).toContain("display: flex");
    expect(drawer).toContain("position: fixed");
    expect(rule(phone, ".app.nav-open .nav-backdrop")).toContain("display: block");
  });
});

describe("Panoramica sugli schermi stretti", () => {
  it("i cinque passi usano .grid-5, che le regole per gli schermi stretti riducono", () => {
    const html = render(<Dashboard />);
    expect(html).toContain('<div class="grid grid-5">');
    expect(html).not.toContain("repeat(5"); // niente stile in linea, che vincerebbe sulle regole @media
    expect(rule(css, ".grid-5")).toContain("repeat(5, minmax(0, 1fr))");
    expect(rule(media(1100), ".grid-5")).toContain("repeat(3, minmax(0, 1fr))");
    expect(rule(media(760), ".grid-5")).toContain("grid-template-columns: 1fr");
  });
});
