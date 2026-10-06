import { Component, type ErrorInfo, type ReactNode } from "react";
import { Icon } from "./Icon";
import { CopyButton, Disclosure } from "./ui";

interface State {
  error: Error | null;
  /** componenti in cui è nato l'errore (dettagli tecnici) */
  stack: string | null;
}

const RELOAD_FLAG = "bioloom.chunk-reload";

/** Mostra gli errori di rendering invece di una pagina bianca; ricarica una volta se una build più
 *  recente ha reso obsoleti i moduli già caricati dal browser. */
export class ErrorBoundary extends Component<{ children: ReactNode }, State> {
  state: State = { error: null, stack: null };

  static getDerivedStateFromError(error: Error): Partial<State> {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error("Errore dell'interfaccia", error, info.componentStack);
    this.setState({ stack: info.componentStack ?? null });
    const stale = /dynamically imported module|Importing a module script failed|Failed to fetch/i.test(error.message);
    if (stale && !sessionStorage.getItem(RELOAD_FLAG)) {
      sessionStorage.setItem(RELOAD_FLAG, "1");
      window.location.reload();
    }
  }

  render() {
    const { error, stack } = this.state;
    if (!error) {
      sessionStorage.removeItem(RELOAD_FLAG);
      return this.props.children;
    }
    const details = [error.message, error.stack, stack].filter(Boolean).join("\n\n");
    return (
      <div className="page">
        <section className="card shell-error" role="alert">
          <div className="shell-error-head">
            <Icon name="alert" size={22} />
            <div>
              <h2>Si è verificato un errore nell'interfaccia</h2>
              <p className="small text-2">La pagina non è stata disegnata; i calcoli in corso continuano sul server.</p>
            </div>
          </div>
          <pre>{error.message}</pre>
          {(error.stack || stack) && (
            <Disclosure title="Dettagli tecnici">
              <pre>{[error.stack, stack].filter(Boolean).join("\n")}</pre>
            </Disclosure>
          )}
          <div className="row">
            <button type="button" className="btn btn-primary btn-sm" onClick={() => window.location.reload()}>
              <Icon name="refresh" size={13} /> Ricarica la pagina
            </button>
            <button type="button" className="btn btn-sm" onClick={() => this.setState({ error: null, stack: null })}>Riprova</button>
            <CopyButton text={details} label="Copia i dettagli" />
          </div>
        </section>
      </div>
    );
  }
}
