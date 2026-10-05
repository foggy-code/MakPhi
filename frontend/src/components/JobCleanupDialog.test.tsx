import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import type { CleanupPreview } from "../api/types";
import {
  buildCleanupRequest, type CleanupCriteria, type CleanupTotals, completedIncluded, criteriaKey, defaultCriteria, dialogPreview, emptyTotals,
  type PreviewState, toggleStatus,
} from "../lib/cleanup";
import { CleanupDialogView, type CleanupDialogViewProps } from "./JobCleanupDialog";

// Markup statico (react-dom/server) del dialogo presentazionale; anteprima e conferma calcolate come nel contenitore.
const noop = () => undefined;
const PROJECT = "p1";
const preview = (matched: number): CleanupPreview => ({
  matched, bytes: matched * 1024, bytes_complete: true, by_status: { failed: matched }, by_kind: {}, protected: 2, resumable: 1,
  sample: matched ? [{ id: "0123456789abcdef", kind: "model", title: "Modello di prova", status: "failed", finished_at: null }] : [],
  as_of: "2026-10-05T10:00:00+00:00",
});

/** Il dialogo nello stato dato: `loop` = stato del ciclo dell'anteprima per la chiave dei criteri (senza richiesta: idle). */
function render(o: {
  criteria?: CleanupCriteria;
  projectKnown?: boolean;
  projectChanged?: boolean;
  loop?: (key: string) => PreviewState;
  lastReady?: PreviewState | null;
  phase?: CleanupDialogViewProps["phase"];
  totals?: CleanupTotals | null;
} = {}) {
  const criteria = o.criteria ?? defaultCriteria();
  const projectKnown = o.projectKnown ?? true;
  const request = buildCleanupRequest(criteria, projectKnown ? PROJECT : null);
  const key = request ? criteriaKey(request) : null;
  const loop: PreviewState = o.loop && key ? o.loop(key) : { status: "idle" };
  const v = dialogPreview({ request, loop, lastReady: o.lastReady ?? null, completed: completedIncluded(criteria), understood: criteria.understood });
  return renderToStaticMarkup(
    <CleanupDialogView phase={o.phase ?? "form"} criteria={criteria} onCriteria={noop} projectName="Chinasi" projectKnown={projectKnown}
      projectChanged={o.projectChanged}
      preview={v.shown} current={v.current} canRun={v.canRun} totals={o.totals ?? null} onRun={noop} onStop={noop} onClose={noop} onRetry={noop} />,
  );
}

const ready = (matched: number) => (key: string): PreviewState => ({ status: "ready", key, data: preview(matched) });

/** Tag del bottone di conferma (distruttivo). */
function confirmButton(html: string): string {
  const tag = /<button[^>]*class="btn btn-danger"[^>]*>/.exec(html);
  expect(tag).not.toBeNull();
  return tag![0];
}

/** La casella con quel testo è spuntata (undefined se non c'è). */
function checked(html: string, text: string): boolean | undefined {
  const label = html.split('<label class="check">').slice(1).map((s) => s.slice(0, s.indexOf("</label>"))).find((s) => s.includes(text));
  return label === undefined ? undefined : /<input[^>]*checked=""/.test(label);
}

describe("dialogo della pulizia dei job", () => {
  it("valori predefiniti: falliti e annullati, non i completati; stella ed etichette conservate; oltre 7 giorni", () => {
    const html = render();
    expect(checked(html, "Falliti")).toBe(true);
    expect(checked(html, "Annullati")).toBe(true);
    expect(checked(html, "Completati")).toBe(false);
    expect(checked(html, "I job con la stella")).toBe(true);
    expect(checked(html, "I job con etichette")).toBe(true);
    expect(checked(html, "Includi anche le pipeline riprendibili")).toBe(false);
    expect(html).toContain('<option value="7" selected="">oltre 7 giorni</option>');
    expect(html).toContain('<option value="project" selected="">Questo progetto (Chinasi)</option>');
    expect(html).toContain('<option value="" selected="">Tutti i tipi</option>');
    // gruppi con la legenda visibile
    expect(html.match(/role="group" aria-labelledby="[^"]+"/g)).toHaveLength(2);
    expect(html).toContain("Stati da eliminare");
    expect(html).toContain("Conserva comunque");
    expect(html).toContain("Le etichette contano solo come «conserva»");
    expect(html).not.toContain("Ho capito");
    expect(html).not.toContain("alert-warn");
    // nome accessibile del dialogo dal titolo
    const labelledby = /role="dialog" aria-modal="true" aria-labelledby="([^"]+)"/.exec(html);
    expect(labelledby).not.toBeNull();
    expect(html).toContain(`<h2 id="${labelledby![1]}">Pulisci job</h2>`);
  });

  it("conferma attiva solo con l'anteprima pronta dei criteri attuali, con il numero e lo spazio", () => {
    const html = render({ loop: ready(42) });
    expect(confirmButton(html)).not.toContain("disabled");
    expect(confirmButton(html)).not.toContain("autofocus");
    expect(html).toContain("Elimina 42 job (42 KB)");
    expect(html).toContain("42 job, 42 KB");
    expect(html).toContain("1 pipeline riprendibile esclusa");
    expect(html).toContain("2 conservati per la stella o le etichette");
    expect(html).toContain("Modello di prova");
    expect(html).toMatch(/aria-busy="false"/);
    // conteggi per stato con il singolare
    const mixed = (key: string): PreviewState => ({ status: "ready", key, data: { ...preview(3), by_status: { failed: 2, cancelled: 1 } } });
    expect(render({ loop: mixed })).toContain("2 falliti · 1 annullato");
  });

  it("conferma disattivata con anteprima superata (attenuata), vuota, in errore, senza progetto o completati senza «Ho capito»", () => {
    const stale = render({ loop: (key) => ({ status: "loading", key }), lastReady: { status: "ready", key: "criteri-di-prima", data: preview(9) } });
    expect(confirmButton(stale)).toContain('disabled=""');
    expect(stale).toContain("9 job, 9 KB"); // l'anteprima di prima resta visibile, attenuata
    expect(stale).toMatch(/aria-busy="true"[^>]*style="opacity:0.55/);
    expect(stale).toContain("</svg> Elimina i job</button>");

    const empty = render({ loop: ready(0) });
    expect(confirmButton(empty)).toContain('disabled=""');
    expect(empty).toContain("Nessun job corrisponde: prova con «qualsiasi età»");

    const failed = render({ loop: (key) => ({ status: "error", key, message: "Errore 500" }) });
    expect(confirmButton(failed)).toContain('disabled=""');
    expect(failed).toContain("Anteprima non disponibile: Errore 500");
    expect(failed).toContain("Riprova");

    const noProject = render({ projectKnown: false, loop: ready(5) });
    expect(confirmButton(noProject)).toContain('disabled=""');
    expect(noProject).toContain('<option value="project" disabled="" selected="">Questo progetto (nessuno attivo)</option>');
    expect(noProject).toContain("Nessun progetto attivo: scegli «Tutti i progetti»");

    const completed = toggleStatus(defaultCriteria(), "completed");
    expect(confirmButton(render({ criteria: completed, loop: ready(5) }))).toContain('disabled=""');
    expect(confirmButton(render({ criteria: { ...completed, understood: true }, loop: ready(5) }))).not.toContain("disabled");

    expect(confirmButton(render({ criteria: { ...defaultCriteria(), statuses: [] } }))).toContain('disabled=""');
  });

  it("progetto attivo cambiato dopo l'apertura: «Questo progetto» disattivato col nome di prima, nota breve, conferma disattivata", () => {
    const html = render({ projectKnown: false, projectChanged: true, loop: ready(5) });
    expect(html).toContain('<option value="project" disabled="" selected="">Questo progetto (Chinasi, non più attivo)</option>');
    expect(html).toContain("Il progetto attivo è cambiato dopo l&#x27;apertura: chiudi e riapri il dialogo per pulire quello nuovo");
    expect(html).not.toContain("Nessun progetto attivo");
    expect(confirmButton(html)).toContain('disabled=""');
    // con «Tutti i progetti» la nota non serve e la conferma torna possibile
    const all = render({ criteria: defaultCriteria({ scope: "all" }), projectKnown: false, projectChanged: true, loop: ready(5) });
    expect(all).not.toContain("Il progetto attivo è cambiato");
    expect(confirmButton(all)).not.toContain("disabled");
  });

  it("avviso e «Ho capito» solo con i completati", () => {
    const html = render({ criteria: toggleStatus(defaultCriteria(), "completed"), loop: ready(5) });
    expect(checked(html, "Completati")).toBe(true);
    expect(html).toContain("alert-warn");
    expect(html).toContain(
      "Include i job completati: modelli e risultati verranno eliminati per sempre; i job che li usano come sorgente non potranno più essere",
    );
    expect(checked(html, "Ho capito")).toBe(false);
  });

  it("in esecuzione: barra di avanzamento accessibile, testo annunciato e «Interrompi»", () => {
    const totals: CleanupTotals = { ...emptyTotals(), deleted: 120, total: 840, remaining: 720, freedBytes: 2048, calls: 2 };
    const html = render({ phase: "running", totals });
    expect(html).toMatch(/role="progressbar" aria-label="Avanzamento della pulizia" aria-valuemin="0" aria-valuemax="100" aria-valuenow="14"/);
    expect(html).toMatch(/<div class="small" aria-live="polite"><div>Eliminati 120 di 840 job · liberati 2 KB<\/div>/);
    expect(html).toMatch(/<button type="button" class="btn" autofocus="">.*Interrompi<\/button>/);
    expect(html).not.toContain("btn-danger");
    // prima della prima risposta: barra indeterminata
    expect(render({ phase: "running" })).toContain("Avvio della pulizia…");
  });

  it("interruzione chiesta: «Interruzione in corso…» e «Interrompi» disattivato", () => {
    const html = render({ phase: "stopping", totals: { ...emptyTotals(), deleted: 10, total: 100, remaining: 90, calls: 1 } });
    expect(html).toContain("Interruzione in corso…");
    expect(html).toMatch(/<button type="button" class="btn" disabled="" autofocus="">.*Interrompi<\/button>/);
  });

  it("riepilogo: errore come alert, gli altri esiti come stato", () => {
    const done = render({ phase: "done", totals: { ...emptyTotals(), deleted: 3, total: 3, remaining: 0, freedBytes: 1024, calls: 1, outcome: "done" } });
    expect(done).toContain('<div role="status"><div class="alert alert-ok">');
    expect(done).toContain("Eliminati 3 job, liberati 1 KB.");
    expect(done).toContain(">Chiudi</button>");
    const failed = render({ phase: "done", totals: { ...emptyTotals(), deleted: 3, calls: 1, outcome: "error", error: "Errore 500" } });
    expect(failed).toContain('<div role="alert"><div class="alert alert-err">');
    expect(failed).toContain("La pulizia si è interrotta per un errore: Errore 500");
  });
});
