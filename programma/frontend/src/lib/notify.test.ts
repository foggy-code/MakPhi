import { describe, expect, it } from "vitest";
import type { NotifyStatus } from "../api/types";
import { isEmail, notifySummary } from "./notify";

const base: NotifyStatus = { host: "smtp.example.org", port: 587, security: "starttls", user: "u", sender: "makphi@example.org", public_url: "", password_set: true, configured: true, source: "file", error: null, file: "licenses/posta.env" };

describe("notify", () => {
  it("riconosce gli indirizzi come il server", () => {
    expect(isEmail("utente@example.org")).toBe(true);
    expect(isEmail(" utente@example.org ")).toBe(true);
    for (const bad of ["", "utente", "utente@example", "a b@example.org", "a@b.it\nBcc: x@y.z", "a,b@example.org"]) expect(isEmail(bad)).toBe(false);
  });
  it("riassume lo stato del server di posta", () => {
    expect(notifySummary(base)).toBe("Server di posta smtp.example.org:587, mittente makphi@example.org.");
    expect(notifySummary({ ...base, configured: false, source: "none" })).toMatch(/licenses\/posta\.env della cartella dati/);
    expect(notifySummary({ ...base, configured: false, error: "Configurazione della posta non valida: Porta: troppo lungo." })).toMatch(/^Avviso via email non disponibile: Configurazione/);
    expect(notifySummary(null)).toMatch(/non letta/);
  });
});
