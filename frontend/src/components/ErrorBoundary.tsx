import { Component, type ErrorInfo, type ReactNode } from "react";

interface State {
  error: Error | null;
}

const RELOAD_FLAG = "bioloom.chunk-reload";

/** Mostra gli errori di rendering invece di una pagina bianca; ricarica una volta se una build più
 *  recente ha reso obsoleti i moduli già caricati dal browser. */
export class ErrorBoundary extends Component<{ children: ReactNode }, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error("Errore dell'interfaccia", error, info.componentStack);
    const stale = /dynamically imported module|Importing a module script failed|Failed to fetch/i.test(error.message);
    if (stale && !sessionStorage.getItem(RELOAD_FLAG)) {
      sessionStorage.setItem(RELOAD_FLAG, "1");
      window.location.reload();
    }
  }

  render() {
    if (!this.state.error) {
      sessionStorage.removeItem(RELOAD_FLAG);
      return this.props.children;
    }
    return (
      <div className="page">
        <div className="alert alert-err" style={{ flexDirection: "column" }}>
          <b>Si è verificato un errore nell'interfaccia.</b>
          <pre className="mono" style={{ whiteSpace: "pre-wrap", margin: 0 }}>{this.state.error.message}</pre>
          <div className="row">
            <button className="btn btn-sm" onClick={() => window.location.reload()}>Ricarica la pagina</button>
            <button className="btn btn-sm btn-ghost" onClick={() => this.setState({ error: null })}>Riprova</button>
          </div>
        </div>
      </div>
    );
  }
}
