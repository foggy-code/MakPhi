import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import type { Project, ProjectUsage } from "../api/types";
import { ProjectDeleteButton, ProjectDeleteView, type ProjectDeleteViewProps } from "./ProjectsDialog";

// Markup statico (react-dom/server) della conferma di eliminazione di un progetto, senza browser.
const noop = () => undefined;
const GB = 1024 ** 3;
const PROJECT: Project = {
  id: "p1", name: "Chinasi di lievito", description: "", created_at: "2026-01-01T00:00:00+00:00", updated_at: "2026-01-02T00:00:00+00:00",
};
const usage = (o: Partial<ProjectUsage> = {}, jobs: Partial<ProjectUsage["jobs"]> = {}): ProjectUsage => ({
  id: "p1", name: PROJECT.name, created_at: PROJECT.created_at, updated_at: PROJECT.updated_at, sequences: 3, documents: 1,
  jobs: { total: 120, active: 0, starred: 2, tagged: 5, resumable: 1, by_status: { completed: 120 }, ...jobs },
  bytes: Math.round(1.4 * GB), bytes_complete: true, ...o,
});
const EMPTY = usage({ sequences: 0, documents: 0, bytes: 0 }, { total: 0, starred: 0, tagged: 0, resumable: 0 });
const render = (o: Partial<ProjectDeleteViewProps> = {}) => renderToStaticMarkup(
  <ProjectDeleteView project={PROJECT} usage={usage()} isLast={false} typed="" onTyped={noop} busy={false} onConfirm={noop} onCancel={noop}
    onGoToJobs={noop} {...o} />,
);

/** Tag del bottone di conferma (distruttivo). */
function confirmButton(html: string): string {
  const tag = /<button[^>]*class="btn btn-danger"[^>]*>/.exec(html);
  expect(tag).not.toBeNull();
  return tag![0];
}

describe("conferma dell'eliminazione di un progetto", () => {
  it("con job attivi: nessun campo del nome, motivo visibile, conferma disattivata e «Vai ai job»", () => {
    const html = render({ usage: usage({}, { active: 2 }) });
    expect(html).not.toContain("<input");
    expect(html).toContain("alert-warn");
    expect(html).toContain("Il progetto ha 2 job in coda o in esecuzione: annullali dalla pagina Job prima di eliminarlo.");
    expect(confirmButton(html)).toContain('disabled=""');
    expect(html).toMatch(/<button type="button" class="btn btn-sm"[^>]*autofocus="">.*Vai ai job<\/button>/);
  });

  it("progetto senza contenuto: conferma semplice, focus iniziale su «Annulla»", () => {
    const html = render({ usage: EMPTY });
    expect(html).not.toContain("<input");
    expect(html).toContain("0 sequenze, 0 documenti, 0 job, 0 B");
    expect(confirmButton(html)).not.toContain("disabled");
    expect(confirmButton(html)).not.toContain("autofocus");
    expect(html).toMatch(/<button type="button" class="btn" autofocus="">Annulla<\/button>/);
  });

  it("con contenuto: si scrive il nome; conferma attiva solo quando coincide", () => {
    const html = render();
    expect(html).toContain("3 sequenze, 1 documento, 120 job, 1,4 GB");
    expect(html).toContain("<span>Scrivi il nome del progetto</span>");
    expect(html).toMatch(/<span>Scrivi il nome del progetto<\/span><input class="input" autofocus="" autoComplete="off" spellCheck="false" value=""\/>/);
    expect(html).toContain("<form");
    expect(confirmButton(html)).toContain('type="submit"');
    expect(confirmButton(html)).toContain('disabled=""');
    expect(confirmButton(html)).not.toContain("autofocus");
    expect(html).not.toMatch(/autofocus="">Annulla/);
    expect(confirmButton(render({ typed: "Chinasi" }))).toContain('disabled=""');
    expect(confirmButton(render({ typed: "  chinasi   DI lievito " }))).not.toContain("disabled");
    expect(html).toContain("Compresi 2 job con la stella, 5 job con etichette e 1 pipeline riprendibile.");
    expect(html).toContain("Non si può annullare.");
  });

  it("byte non completi: «almeno»", () => {
    expect(render({ usage: usage({ bytes_complete: false }) })).toContain("120 job, almeno 1,4 GB");
  });

  it("nota sull'ultimo progetto solo se lo è", () => {
    expect(render({ isLast: true })).toContain("È l&#x27;ultimo progetto: BioLoom ne creerà uno vuoto, perché ne serve sempre almeno uno.");
    expect(render()).not.toContain("ultimo progetto");
  });

  it("in corso: «Eliminazione…», avanzamento, «Interrompi» durante lo svuotamento, conferma disattivata", () => {
    const html = render({ typed: PROJECT.name, busy: true, progress: "Eliminati 120 di 840 job · liberati 1,4 GB", onStop: noop });
    expect(confirmButton(html)).toContain('disabled=""');
    expect(html).toContain("Eliminazione…</button>");
    expect(html).toContain("Eliminati 120 di 840 job · liberati 1,4 GB");
    expect(html).toContain("Interrompi</button>");
    expect(render({ typed: PROJECT.name, busy: true })).not.toContain("Interrompi");
  });

  it("uso in lettura o non leggibile; errore del server come alert", () => {
    expect(render({ usage: null })).toContain("Lettura del contenuto del progetto…");
    expect(confirmButton(render({ usage: null }))).toContain('disabled=""');
    expect(render({ usage: null, usageError: "Progetto non trovato." })).toContain("Progetto non trovato.");
    expect(render({ error: "Il progetto è stato svuotato in parte: eliminati 5 job" })).toContain('<div role="alert"><div class="alert alert-err">');
  });

  it("mai la parola «cestino» nel testo della conferma", () => {
    for (const html of [render(), render({ usage: EMPTY }), render({ usage: usage({}, { active: 1 }) }), render({ isLast: true })]) {
      expect(html.toLowerCase()).not.toContain("cestino");
    }
  });

  it("cestino della riga: nome accessibile con il nome del progetto", () => {
    const html = renderToStaticMarkup(<ProjectDeleteButton name="Chinasi di lievito" onClick={noop} />);
    expect(html).toMatch(/<button type="button" class="btn btn-sm btn-ghost" aria-label="Elimina il progetto Chinasi di lievito" title="[^"]+">/);
  });
});
