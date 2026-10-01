import { describe, expect, it } from "vitest";
import { jobChoices, modelChoices, modelFiles, parsePastedSequence, pastedTextMatches, type PastedSequence } from "./picker";

const listed = [
  { id: "aaaaaaaaaaaaaaaa", title: "Modello di ubiquitina" },
  { id: "bbbbbbbbbbbbbbbb", title: "" },
];

describe("selettore della struttura di un job", () => {
  it("il job della struttura arrivata da un link compare anche se non è tra gli ultimi job di modellazione", () => {
    // Pipeline → Qualità → «Confronta con un'altra struttura»: b=job:<pipeline>:items/0000_a/a.B99990001.pdb
    const choices = jobChoices(listed, "cccccccccccccccc", "Pipeline di 3 sequenze");
    expect(choices[0]).toEqual({ value: "cccccccccccccccc", label: "Pipeline di 3 sequenze (da un link)" });
    expect(choices.map((c) => c.value)).toEqual(["cccccccccccccccc", "aaaaaaaaaaaaaaaa", "bbbbbbbbbbbbbbbb"]);
    expect(jobChoices(listed, "dddddddddddddddd")[0].label).toBe("job dddddddddddddddd (da un link)"); // titolo non ancora letto
    expect(jobChoices(null, "dddddddddddddddd")).toHaveLength(1); // elenco non ancora caricato
  });
  it("un job già elencato o nessun job scelto non aggiunge nulla", () => {
    expect(jobChoices(listed, "aaaaaaaaaaaaaaaa", "Modello di ubiquitina")).toEqual([
      { value: "aaaaaaaaaaaaaaaa", label: "Modello di ubiquitina" },
      { value: "bbbbbbbbbbbbbbbb", label: "job bbbbbbbbbbbbbbbb" },
    ]);
    expect(jobChoices(listed, "")).toHaveLength(2);
  });
  it("modelli di un job model o loop; una pipeline non li elenca e il file del link resta scelto", () => {
    const model = { models: [{ file: "t.B99990001.pdb", failure: null }, { file: "t.B99990002.pdb", failure: "errore" }, { file: "t.BL00010001.pdb" }] };
    expect(modelFiles(model)).toEqual(["t.B99990001.pdb", "t.BL00010001.pdb"]);
    expect(modelFiles({ counts: { completed: 3 }, items: 3 })).toEqual([]); // PipelineResult: nessun errore
    expect(modelFiles(null)).toEqual([]);
    const item = "items/0000_a/a.B99990001.pdb";
    expect(modelChoices(modelFiles({ counts: {} }), item)).toEqual([item]);
    expect(modelChoices(["m1.pdb", "m2.pdb"], "m2.pdb")).toEqual(["m1.pdb", "m2.pdb"]);
    expect(modelChoices(["m1.pdb"], null)).toEqual(["m1.pdb"]);
  });
});

describe("sequenza incollata o digitata nel selettore", () => {
  const empty: PastedSequence = { name: "target", sequence: "" };
  const lines = (...rows: string[]) => rows.join("\n");

  /** Digitazione carattere per carattere come in una textarea controllata dal testo grezzo. */
  const type = (text: string, start: PastedSequence = empty) => {
    let value = start;
    for (let i = 1; i <= text.length; i++) value = parsePastedSequence(text.slice(0, i), value);
    return value;
  };

  it("un'intestazione FASTA scritta a mano non cancella nulla: si analizza dopo l'a capo", () => {
    // prima: al primo '>' la sequenza diventava vuota e il campo, che mostrava la sequenza, perdeva il testo
    expect(parsePastedSequence(">", empty)).toEqual(empty);
    expect(parsePastedSequence(">ubq_lie", empty)).toEqual(empty);
    expect(pastedTextMatches(">ubq_lie", empty)).toBe(true); // il campo conserva il testo grezzo
    expect(parsePastedSequence(lines(">ubq_lievito descrizione", ""), empty)).toEqual({ name: "ubq_lievito", sequence: "" });
    expect(type(lines(">ubq_lievito descrizione", "MQIF VKTL", "TGK"))).toEqual({ name: "ubq_lievito", sequence: "MQIFVKTLTGK" });
  });

  it("incollare un FASTA intero dà nome e sequenza; con più voci vale la prima", () => {
    const pasted = ">sp|P0CG63|UBI4 Ubiquitin\nmqifvktltg\r\nktitlevess 20\n";
    expect(parsePastedSequence(pasted, empty)).toEqual({ name: "sp|P0CG63|UBI4", sequence: "MQIFVKTLTGKTITLEVESS" });
    expect(parsePastedSequence(lines(">a", "MQ", ">b", "KK", ""), empty)).toEqual({ name: "a", sequence: "MQ" });
    expect(parsePastedSequence(lines(">", "MQ"), empty)).toEqual({ name: "target", sequence: "MQ" }); // senza nome resta quello di prima
  });

  it("una sequenza semplice resta com'è; un '>' aggiunto davanti non la perde", () => {
    expect(parsePastedSequence("mqif vktl", empty)).toEqual({ name: "target", sequence: "mqif vktl" });
    const plain = { name: "target", sequence: "MQIFVKTL" };
    expect(parsePastedSequence(">MQIFVKTL", plain)).toEqual(plain);
    expect(parsePastedSequence(lines(">nuovo", "MQIFVKTL"), plain)).toEqual({ name: "nuovo", sequence: "MQIFVKTL" });
  });

  it("un valore cambiato da fuori (progetto, file) sostituisce il testo grezzo; il solo nome no", () => {
    expect(pastedTextMatches(lines(">a", "MQ"), { name: "a", sequence: "MQ" })).toBe(true);
    expect(pastedTextMatches(lines(">a", "MQ"), { name: "rinominato", sequence: "MQ" })).toBe(true);
    expect(pastedTextMatches(lines(">a", "MQ"), { name: "b", sequence: "KKK" })).toBe(false);
    expect(pastedTextMatches("MQ", { name: "x", sequence: "" })).toBe(false); // nuovo bersaglio vuoto
  });
});
