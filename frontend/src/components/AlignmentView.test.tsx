import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { AlignmentView } from "./AlignmentView";

// Markup statico dell'allineamento: righello numerato, colonne identiche a colore pieno con testo leggibile,
// legenda delle classi, scaricamento FASTA ed espansione.
const ids = ["bersaglio", "templ"];
const aligned = ["MKTAYIAKQRQISFVKSHFSRQ", "MKSAYIAKQ-QISFVRSHFSRQ"];

describe("allineamento", () => {
  it("righello con i numeri delle colonne e numeri di fine riga", () => {
    const html = renderToStaticMarkup(<AlignmentView ids={ids} aligned={aligned} conservation={"**.******* *****:*****"} />);
    expect(html).toContain('class="aln aln-readable"');
    expect(html).toContain("<span>1</span><span>0</span>"); // "10" sulle colonne 9–10
    expect(html).toContain('class="aln-end">22<');
    expect(html).toContain('class="aln-row aln-cons"');
    expect(html).toContain("Passa sull&#x27;allineamento per i dettagli di una posizione.");
  });

  it("colonne identiche a colore pieno con testo nero, le altre tenui; legenda delle classi", () => {
    const html = renderToStaticMarkup(<AlignmentView ids={ids} aligned={aligned} />);
    expect(html).toContain('<span style="background:#80a0f0;color:#000000;font-weight:700">M</span>');
    expect(html).toContain('<span style="background:#15c01538">T</span>'); // T contro S: colonna non identica
    expect(html).toContain("Idrofobici");
    expect(html).toContain("colore pieno: colonna identica in tutte le sequenze");
  });

  it("FASTA ed Espandi; senza FASTA dove la pagina ha già i suoi formati", () => {
    const html = renderToStaticMarkup(<AlignmentView ids={ids} aligned={aligned} />);
    expect(html).toContain("FASTA");
    expect(html).toContain("Espandi");
    expect(html).toMatch(/<button(?=[^>]*aria-haspopup="dialog")[^>]*>(?:(?!<\/button>).)*Espandi<\/button>/);
    expect(renderToStaticMarkup(<AlignmentView ids={ids} aligned={aligned} downloadable={false} />)).not.toContain("FASTA");
  });

  it("confidenza: riga del bersaglio in evidenza e riepilogo", () => {
    const html = renderToStaticMarkup(<AlignmentView ids={ids} aligned={aligned} initialMode="confidence" />);
    expect(html).toContain('class="aln-row aln-ref"');
    expect(html).toContain("Identità locale su 9 colonne rispetto a <b>bersaglio</b>");
    expect(html).not.toContain("Idrofobici");
  });

  it("senza sequenze non disegna nulla", () => {
    expect(renderToStaticMarkup(<AlignmentView ids={[]} aligned={[]} />)).toBe("");
  });
});
