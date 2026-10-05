import { describe, expect, it } from "vitest";
import { pickProjectId } from "./projectSelection";

describe("progetto attivo dopo la rilettura dell'elenco", () => {
  const list = [{ id: "a" }, { id: "b" }];
  it("resta quello attuale se è ancora in elenco", () => {
    expect(pickProjectId(list, "b")).toBe("b");
  });
  it("il primo se quello attuale non c'è più o non è scelto", () => {
    expect(pickProjectId(list, "eliminato")).toBe("a");
    expect(pickProjectId(list, null)).toBe("a");
  });
  it("elenco vuoto: nessuno, mai l'id eliminato", () => {
    expect(pickProjectId([], "eliminato")).toBeNull();
    expect(pickProjectId([], null)).toBeNull();
  });
});
