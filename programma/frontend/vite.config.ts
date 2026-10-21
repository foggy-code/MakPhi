import { defineConfig } from "vite";
import { configDefaults } from "vitest/config";
import react from "@vitejs/plugin-react";

// In sviluppo (npm run dev) le chiamate /api vengono inoltrate al server Python.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: { "/api": "http://127.0.0.1:8765" },
  },
  build: {
    chunkSizeWarningLimit: 1500,
    rollupOptions: {
      output: {
        // Vite 8 (rolldown) accetta solo la forma a funzione: 3Dmol.js a parte (caricato su richiesta), React e il
        // router in un blocco comune, come prima con la forma a oggetto
        manualChunks(id: string) {
          const path = id.replace(/\\/g, "/");
          if (path.includes("/node_modules/3dmol/")) return "viewer3d";
          if (/\/node_modules\/(react|react-dom|react-router|react-router-dom|scheduler)\//.test(path)) return "vendor";
          return undefined;
        },
      },
    },
  },
  // i fogli di stile si leggono nei test (`?raw`: regole per gli schermi stretti, contrasto delle tracce dell'Editor
  // PIR); vitest altrimenti li dà vuoti. Ambiente node (markup statico, funzioni); i test di interfaccia, che hanno
  // bisogno di effetti, clic e tastiera, dichiarano `// @vitest-environment jsdom` in testa al file e usano
  // @testing-library/react (risposte finte del server e visibilità della scheda in src/test/dom.ts)
  // Copertura (npm run coverage): v8, solo i sorgenti di src/ senza i test; riepilogo testuale e JSON per la CI.
  test: {
    environment: "node",
    // e2e/: test nel browser di Playwright (npm run e2e), non di vitest
    exclude: [...configDefaults.exclude, "e2e/**"],
    css: { include: [/styles\.css/, /styles[\\/]pir\.css/] },
    coverage: {
      provider: "v8",
      include: ["src/**/*.{ts,tsx}"],
      exclude: ["src/**/*.test.{ts,tsx}", "src/test/**"],
      reporter: ["text-summary", "json-summary"],
      // cartella nascosta: fuori dall'impronta del codice (docker/impronta.sh) e dal repository
      reportsDirectory: ".coverage",
    },
  },
});
