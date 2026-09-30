import { describe, expect, it } from "vitest";
import { jobChoices, modelChoices, modelFiles } from "./picker";

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
