import { useState } from "react";
import { api, ApiError } from "../api/client";
import { Icon } from "../components/Icon";
import { Alert, Disclosure, Spinner } from "../components/ui";

/** Accesso con chiave: compare solo quando BioLoom gira su un server con accesso protetto. */
export default function Login({ onDone }: { onDone: () => void }) {
  const [key, setKey] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await api.login(key.trim());
      onDone();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="login-back">
      <form className="card login-card stack" onSubmit={submit} aria-labelledby="login-title">
        <div className="shell-login-head">
          <span className="shell-mark"><img src="/favicon.svg" alt="" /></span>
          <div>
            <h1 id="login-title">Accedi a BioLoom</h1>
            <span className="small">Modellazione per omologia · installazione condivisa</span>
          </div>
        </div>
        <p className="text-2">
          Questa installazione di BioLoom è condivisa in rete: inserisci la chiave d'accesso che ti ha dato chi la gestisce.
        </p>
        <label className="field">
          <span>Chiave d'accesso</span>
          <input className="input mono" type="password" autoFocus autoComplete="current-password" value={key} onChange={(e) => setKey(e.target.value)}
            aria-invalid={!!error || undefined} />
        </label>
        {error && <Alert tone="err">{error}</Alert>}
        <button className="btn btn-primary" type="submit" disabled={busy || !key.trim()} style={{ width: "100%", height: 38 }}>
          {busy ? <Spinner /> : <Icon name="check" size={14} />} Entra
        </button>
        <Disclosure title="Dove si trova la chiave">
          Sul server la chiave è in <span className="mono">server.env</span> (BIOLOOM_ACCESS_KEY) oppure, se non impostata lì, nel file
          {" "}<span className="mono">access_key</span> della cartella dati.
        </Disclosure>
      </form>
    </div>
  );
}
