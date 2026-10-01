import { lazy, Suspense, useEffect, useState, type ReactNode } from "react";
import { BrowserRouter, Route, Routes } from "react-router-dom";
import { api, AUTH_EVENT } from "./api/client";
import { ErrorBoundary } from "./components/ErrorBoundary";
import { Layout } from "./components/Layout";
import { Spinner } from "./components/ui";
import { ProjectProvider, ToastProvider } from "./lib/context";

const Dashboard = lazy(() => import("./pages/Dashboard"));
const Sequences = lazy(() => import("./pages/Sequences"));
const Search = lazy(() => import("./pages/Search"));
const Templates = lazy(() => import("./pages/Templates"));
const Alignment = lazy(() => import("./pages/Alignment"));
const PirEditor = lazy(() => import("./pages/PirEditor"));
const Modeling = lazy(() => import("./pages/Modeling"));
const Quality = lazy(() => import("./pages/Quality"));
const Compare = lazy(() => import("./pages/Compare"));
const Pipeline = lazy(() => import("./pages/Pipeline"));
const Jobs = lazy(() => import("./pages/Jobs"));
const SettingsPage = lazy(() => import("./pages/Settings"));
const Login = lazy(() => import("./pages/Login"));
const Wizard = lazy(() => import("./pages/Wizard")); // procedura guidata (F8a)
const Rerun = lazy(() => import("./pages/Rerun")); // riesecuzione da pacchetto (F9.4)

function Loading() {
  return (
    <div className="empty">
      <Spinner />
      Caricamento…
    </div>
  );
}

/** Su un server con accesso protetto mostra il login prima dell'applicazione (e quando la sessione scade). */
function AuthGate({ children }: { children: ReactNode }) {
  const [state, setState] = useState<"checking" | "ok" | "login">("checking");
  useEffect(() => {
    api
      .authStatus()
      .then((s) => setState(s.enabled && !s.authenticated ? "login" : "ok"))
      .catch(() => setState("ok"));
    const onRequired = () => setState("login");
    window.addEventListener(AUTH_EVENT, onRequired);
    return () => window.removeEventListener(AUTH_EVENT, onRequired);
  }, []);
  if (state === "checking") return <Loading />;
  if (state === "login") {
    return (
      <Suspense fallback={<Loading />}>
        <Login onDone={() => setState("ok")} />
      </Suspense>
    );
  }
  return <>{children}</>;
}

export function App() {
  return (
    <ToastProvider>
      <AuthGate>
      <ProjectProvider>
        <BrowserRouter>
          <Routes>
            <Route element={<Layout />}>
              {[
                ["/", <Dashboard />],
                ["/sequences", <Sequences />],
                ["/search", <Search />],
                ["/templates", <Templates />],
                ["/alignment", <Alignment />],
                ["/pir", <PirEditor />],
                ["/modeling", <Modeling />],
                ["/quality", <Quality />],
                ["/compare", <Compare />],
                ["/pipeline", <Pipeline />],
                ["/jobs", <Jobs />],
                ["/settings", <SettingsPage />],
                ["/wizard", <Wizard />],
                ["/rerun", <Rerun />],
              ].map(([path, el]) => (
                <Route
                  key={path as string}
                  path={path as string}
                  element={
                    <ErrorBoundary>
                      <Suspense fallback={<Loading />}>{el}</Suspense>
                    </ErrorBoundary>
                  }
                />
              ))}
              <Route path="*" element={<Suspense fallback={<Loading />}><Dashboard /></Suspense>} />
            </Route>
          </Routes>
        </BrowserRouter>
      </ProjectProvider>
      </AuthGate>
    </ToastProvider>
  );
}
