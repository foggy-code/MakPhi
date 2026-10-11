import { describe, expect, it } from "vitest";
import { httpsUrl } from "./links";

// Link a servizi esterni (rapporto di SWISS-MODEL): solo https.
describe("httpsUrl", () => {
  it("accetta solo https con un host", () => {
    expect(httpsUrl("https://swissmodel.expasy.org/qmean/abc/")).toBe("https://swissmodel.expasy.org/qmean/abc/");
    expect(httpsUrl("  https://example.org/x  ")).toBe("https://example.org/x");
  });
  it("rifiuta javascript:, data:, http e valori non validi", () => {
    for (const value of ["javascript:alert(1)", "JAVASCRIPT:alert(1)", "data:text/html,<b>x</b>", "http://example.org", "//example.org", "/api/x", "", "   ", null, undefined, 42]) {
      expect(httpsUrl(value), String(value)).toBeNull();
    }
  });
});
