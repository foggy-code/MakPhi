import { defineConfig, devices } from "@playwright/test";

// Test dell'interfaccia in un browser vero (npm run e2e): Chromium contro una MakPhi in esecuzione, con il backend
// vero (MAKPHI_URL, predefinito http://127.0.0.1:8765; MAKPHI_ACCESS_KEY se l'accesso è con chiave). I test di
// vitest restano quelli delle funzioni e dei componenti; qui si prova la pagina servita davvero: caricamento senza
// errori, navigazione, tastiera e accessibilità con axe-core (WCAG 2.1 A e AA), in tema chiaro e scuro.
// Risultati e tracce in cartelle nascoste: fuori dall'impronta del codice (docker/impronta.sh) e dal repository.
export default defineConfig({
  testDir: "e2e",
  outputDir: ".playwright/risultati",
  timeout: 60_000,
  fullyParallel: false,
  workers: 1,
  retries: process.env.CI ? 1 : 0,
  reporter: [["list"]],
  use: {
    baseURL: process.env.MAKPHI_URL ?? "http://127.0.0.1:8765",
    trace: "retain-on-failure",
    locale: "it-IT",
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
});
