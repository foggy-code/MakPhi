import js from "@eslint/js";
import { defineConfig, globalIgnores } from "eslint/config";
import reactHooks from "eslint-plugin-react-hooks";
import globals from "globals";
import tseslint from "typescript-eslint";

// ESLint (npm run lint): regole raccomandate di JavaScript e di typescript-eslint (senza informazioni sui tipi, per
// restare veloce: i tipi li controlla già `tsc --noEmit`) e le due regole classiche degli hook di React. Il preset
// `recommended` di eslint-plugin-react-hooks 7 aggiunge le regole del React Compiler (purity, refs, set-state-in-effect…),
// pensate per il codice che il compilatore ottimizza: qui non si usa, quindi solo rules-of-hooks ed exhaustive-deps.
// exhaustive-deps è un errore: una dipendenza mancante è di solito una closure obsoleta, e le esclusioni volute si
// dichiarano una per una con un commento eslint-disable che ne dice il motivo (dopo «--»). Un commento eslint-disable
// che non serve più è anch'esso un errore.
export default defineConfig([
  globalIgnores(["dist", "node_modules"]),
  {
    files: ["src/**/*.{ts,tsx}"],
    extends: [js.configs.recommended, tseslint.configs.recommended],
    languageOptions: { ecmaVersion: 2022, globals: globals.browser },
    linterOptions: { reportUnusedDisableDirectives: "error" },
    plugins: { "react-hooks": reactHooks },
    rules: {
      "react-hooks/rules-of-hooks": "error",
      "react-hooks/exhaustive-deps": "error",
      // `const { a: _ignored, ...resto } = x` toglie un campo da un oggetto: come tsc (noUnusedLocals) non è un difetto
      "@typescript-eslint/no-unused-vars": ["error", { ignoreRestSiblings: true }],
    },
  },
  {
    // configurazioni di Vite e di ESLint: girano in Node
    files: ["*.config.{js,ts}"],
    extends: [js.configs.recommended, tseslint.configs.recommended],
    languageOptions: { ecmaVersion: 2022, globals: globals.node },
    linterOptions: { reportUnusedDisableDirectives: "error" },
  },
]);
