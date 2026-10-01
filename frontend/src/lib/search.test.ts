import { describe, expect, it } from "vitest";
import type { SearchGroup, SearchItem } from "../api/types";
import { flattenResults, highlightParts, moveSelection, parseSearchQuery, queryForTag, queryKey, resultTarget } from "./search";

const item = (type: SearchItem["type"], id: string, extra: Partial<SearchItem> = {}): SearchItem => ({
  type, id, title: id, subtitle: "", score: 1, project_id: null, project_name: null, ...extra,
});

describe("lettura della ricerca", () => {
  it("testo libero con spazi normalizzati", () => {
    expect(parseSearchQuery("  ubiquitina   lievito ")).toEqual({ text: "ubiquitina lievito", tag: null, types: [], invalid: [] });
    expect(parseSearchQuery("")).toEqual({ text: "", tag: null, types: [], invalid: [] });
  });

  it("#etichetta normalizzata, la prima vale come filtro", () => {
    expect(parseSearchQuery("#Urgente ubq")).toEqual({ text: "ubq", tag: "urgente", types: [], invalid: [] });
    expect(parseSearchQuery("#zn2+")).toMatchObject({ text: "", tag: "zn2+" });
    expect(parseSearchQuery("#a #b")).toMatchObject({ tag: "a", text: "#b" });
    expect(parseSearchQuery("#a/b x")).toMatchObject({ tag: null, text: "x", invalid: ["#a/b"] });
    expect(parseSearchQuery("#")).toMatchObject({ tag: null, text: "#" }); // un # da solo è testo
  });

  it("tipo: e in: con nomi italiani e inglesi, anche più gruppi", () => {
    expect(parseSearchQuery("tipo:job ubq").types).toEqual(["job"]);
    expect(parseSearchQuery("in:Sequenze,template x")).toMatchObject({ text: "x", types: ["sequence", "template"] });
    expect(parseSearchQuery("type:jobs tipo:job").types).toEqual(["job"]);
    expect(parseSearchQuery("tipo:pianeta x")).toMatchObject({ types: [], invalid: ["tipo:pianeta"], text: "x" });
    expect(parseSearchQuery("tipo: x").invalid).toEqual(["tipo:"]);
    expect(parseSearchQuery("ratio:2").text).toBe("ratio:2"); // non è un filtro
  });

  it("caratteri speciali restano testo (il server li tratta come parole o li ignora)", () => {
    expect(parseSearchQuery(`"ubq" OR * '; DROP`)).toMatchObject({ text: `"ubq" OR * '; DROP`, tag: null, types: [] });
  });

  it("chiave della richiesta", () => {
    expect(queryKey(parseSearchQuery("UBQ tipo:job,template"))).toBe(queryKey(parseSearchQuery("ubq  in:template,job")));
    expect(queryKey(parseSearchQuery("ubq"))).not.toBe(queryKey(parseSearchQuery("ubq #x")));
  });
});

describe("navigazione da tastiera", () => {
  const groups: SearchGroup[] = [
    { type: "project", label: "Progetti", truncated: false, items: [item("project", "p1"), item("project", "p2")] },
    { type: "job", label: "Job", truncated: true, items: [item("job", "j1")] },
  ];

  it("elenco piatto nell'ordine dei gruppi", () => {
    const flat = flattenResults(groups);
    expect(flat.map((f) => [f.index, f.group.type, f.item.id])).toEqual([[0, "project", "p1"], [1, "project", "p2"], [2, "job", "j1"]]);
    expect(flattenResults(null)).toEqual([]);
  });

  it("frecce con giro, Home/End, pagine", () => {
    expect(moveSelection(-1, "ArrowDown", 3)).toBe(0);
    expect(moveSelection(2, "ArrowDown", 3)).toBe(0);
    expect(moveSelection(0, "ArrowUp", 3)).toBe(2);
    expect(moveSelection(-1, "ArrowUp", 3)).toBe(2);
    expect(moveSelection(1, "Home", 3)).toBe(0);
    expect(moveSelection(0, "End", 3)).toBe(2);
    expect(moveSelection(0, "PageDown", 12)).toBe(5);
    expect(moveSelection(10, "PageDown", 12)).toBe(11);
    expect(moveSelection(3, "PageUp", 12)).toBe(0);
    expect(moveSelection(1, "a", 3)).toBe(1);
    expect(moveSelection(7, "ArrowDown", 3)).toBe(0); // indice rimasto da una lista più lunga
    expect(moveSelection(0, "ArrowDown", 0)).toBe(-1);
  });
});

describe("destinazione dei risultati", () => {
  it("pagina e progetto da attivare", () => {
    expect(resultTarget(item("project", "p1"))).toEqual({ path: "/", projectId: "p1" });
    expect(resultTarget(item("sequence", "s1", { project_id: "p2" }))).toEqual({ path: "/sequences?id=s1", projectId: "p2" });
    expect(resultTarget(item("document", "d1", { project_id: "p2" }))).toEqual({ path: "/pir?doc=d1", projectId: "p2" });
    expect(resultTarget(item("job", "j1", { project_id: "p2" }))).toEqual({ path: "/jobs?id=j1", projectId: null });
    expect(resultTarget(item("template", "AF-P69905-F1"))).toEqual({ path: "/templates?id=AF-P69905-F1", projectId: null });
    expect(resultTarget(item("tag", "urgente"))).toBeNull();
  });

  it("un'etichetta scelta diventa il filtro della ricerca", () => {
    expect(queryForTag("urgente", parseSearchQuery("urg"))).toBe("#urgente ");
    expect(queryForTag("urgente", parseSearchQuery("urg tipo:job"))).toBe("#urgente tipo:job ");
  });
});

describe("evidenza delle parole trovate", () => {
  const marked = (text: string, q: string) => highlightParts(text, q).filter((p) => p.match).map((p) => p.text);

  it("inizio delle parole, senza accenti né maiuscole", () => {
    expect(marked("Chinasi di lievito", "chin lie")).toEqual(["Chin", "lie"]);
    expect(marked("Proteína ùmana", "proteina um")).toEqual(["Proteína", "ùm"]);
    expect(marked("sp_P69905_HBA_HUMAN", "p6990")).toEqual(["P6990"]);
    expect(marked("1UBQ", "ubq")).toEqual([]); // non è inizio di parola
    expect(highlightParts("abc", "").map((p) => p.text)).toEqual(["abc"]);
    expect(highlightParts("", "x")).toEqual([]);
  });

  it("le parti ricompongono il testo", () => {
    const parts = highlightParts("Modello ubiquitina lievito", "ubi");
    expect(parts.map((p) => p.text).join("")).toBe("Modello ubiquitina lievito");
    expect(parts).toEqual([{ text: "Modello ", match: false }, { text: "ubi", match: true }, { text: "quitina lievito", match: false }]);
  });
});
