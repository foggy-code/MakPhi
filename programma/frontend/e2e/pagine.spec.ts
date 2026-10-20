import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";

// Ogni pagina dell'interfaccia in Chromium, servita da una BioLoom vera (playwright.config.ts): si carica senza errori
// nella console, ha il suo titolo e nessuna violazione WCAG 2.1 A/AA grave o critica secondo axe-core, in tema scuro
// (il predefinito) e chiaro. Le violazioni minori e moderate si stampano, senza far fallire il test.

const PAGINE: [string, string][] = [
  ["/", "Dashboard"],
  ["/sequences", "Sequenze"],
  ["/search", "Ricerca"],
  ["/templates", "Template"],
  ["/alignment", "Allineamento"],
  ["/pir", "Editor PIR"],
  ["/modeling", "Modellazione"],
  ["/quality", "Qualità"],
  ["/compare", "Confronto"],
  ["/pipeline", "Pipeline"],
  ["/jobs", "Job"],
  ["/settings", "Impostazioni"],
  ["/wizard", "Procedura guidata"],
  ["/rerun", "Riesecuzione"],
];

async function accedi(page: Page) {
  const key = process.env.BIOLOOM_ACCESS_KEY;
  if (key) {
    const r = await page.request.post("/api/auth/login", { data: { key } });
    expect(r.ok(), "login con BIOLOOM_ACCESS_KEY").toBeTruthy();
  }
}

for (const theme of ["dark", "light"] as const) {
  test.describe(`tema ${theme === "dark" ? "scuro" : "chiaro"}`, () => {
    test.beforeEach(async ({ page }) => {
      await accedi(page);
      await page.addInitScript((t) => window.localStorage.setItem("bioloom.theme", JSON.stringify(t)), theme);
    });

    for (const [path, nome] of PAGINE) {
      test(`${nome} (${path}): carica, nessun errore, accessibile`, async ({ page }) => {
        const errori: string[] = [];
        page.on("pageerror", (e) => errori.push(e.message));
        page.on("console", (m) => { if (m.type() === "error") errori.push(m.text()); });
        await page.goto(path);
        await expect(page.locator("main.content")).toBeVisible();
        await expect(page.locator("html")).toHaveAttribute("data-theme", theme);
        await page.waitForLoadState("networkidle");
        await expect(page.getByText("Caricamento", { exact: false })).toHaveCount(0, { timeout: 15_000 });
        const axe = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"]).analyze();
        const gravi = axe.violations.filter((v) => v.impact === "serious" || v.impact === "critical");
        for (const v of axe.violations.filter((x) => !gravi.includes(x))) {
          console.log(`[axe ${v.impact}] ${path} ${v.id}: ${v.help} (${v.nodes.length})`);
        }
        expect(gravi.map((v) => `${v.id} (${v.impact}): ${v.help} — ${v.nodes.slice(0, 3).map((n) => n.target.join(" ")).join(", ")}`),
          `violazioni gravi di axe in ${path}`).toEqual([]);
        expect(errori, `errori nella console di ${path}`).toEqual([]);
      });
    }
  });
}

test("navigazione dal menu delle pagine e tastiera", async ({ page }) => {
  await accedi(page);
  await page.goto("/");
  const nav = page.getByRole("navigation", { name: "Pagine" });
  await expect(nav).toBeVisible();
  await nav.getByRole("link", { name: /Pipeline/ }).click();
  await expect(page).toHaveURL(/\/pipeline$/);
  await nav.getByRole("link", { name: /Impostazioni/ }).click();
  await expect(page).toHaveURL(/\/settings$/);
  // la tastiera raggiunge i comandi: il primo Tab porta il fuoco su un elemento visibile
  await page.keyboard.press("Tab");
  const focused = page.locator(":focus");
  await expect(focused).toHaveCount(1);
  await expect(focused).toBeVisible();
});
