// Cache dei calcoli: opzione "Ricalcola ignorando la cache" e nota di provenienza dei risultati riusati.
import { api } from "../api/client";
import type { CacheInfo, Settings } from "../api/types";
import { cachedLabel } from "../lib/format";
import { useAsync } from "../lib/hooks";
import { Badge } from "./ui";

/** Casella "Ricalcola ignorando la cache" (spenta = riusa i risultati identici già calcolati). */
export function RecomputeOption({ checked, onChange, label = "Ricalcola ignorando la cache" }: { checked: boolean; onChange: (v: boolean) => void; label?: string }) {
  const { data: settings } = useAsync<Settings>(() => api.settings(), []);
  const off = settings ? !settings.compute_cache : false;
  return (
    <label
      className="check"
      title={off
        ? "La cache dei calcoli è disattivata nelle Impostazioni (Spazio e file temporanei): ogni calcolo viene rifatto."
        : "Rifà il calcolo anche se ne esiste uno identico in cache, e sostituisce il risultato salvato. Le ricerche online (RCSB, NCBI) in cache valgono 7 giorni."}
    >
      <input type="checkbox" checked={checked || off} disabled={off} onChange={(e) => onChange(e.target.checked)} />
      {label}
      {off && <span className="tiny muted">(cache disattivata nelle Impostazioni)</span>}
    </label>
  );
}

/** Nota "dalla cache (data)" accanto a un risultato riusato; nulla per un risultato calcolato ora. */
export function CachedBadge({ result }: { result: CacheInfo | null | undefined }) {
  const label = cachedLabel(result);
  if (!label) return null;
  return (
    <Badge tone="info" title="Risultato riusato da un calcolo identico: nessun nuovo calcolo. Per rifarlo usa «Ricalcola ignorando la cache».">
      {label}
    </Badge>
  );
}
