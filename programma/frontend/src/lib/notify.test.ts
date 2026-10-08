import { describe, expect, it } from "vitest";
import type { NotifyStatus } from "../api/types";
import { isEmail, notifySummary } from "./notify";

const base: NotifyStatus = { host: "smtp.example.org", port: 587, security: "starttls", user: "u", sender: "bioloom@example.org", public_url: "", password_set: true, configured: true, source: "local" };

describe("notify", () => {
  it("riconosce gli indirizzi come il server", () => {
    expect(isEmail("utente@example.org")).toBe(true);
    expect(isEmail(" utente@example.org ")).toBe(true);
    for (const bad of ["", "utente", "utente@example", "a b@example.org", "a@b.it\nBcc: x@y.z", "a,b@example.org"]) expect(isEmail(bad)).toBe(false);
  });
  it("riassume lo stato del server di posta", () => {
    expect(notifySummary(base)).toBe("Server di posta smtp.example.org:587, mittente bioloom@example.org.");
    expect(notifySummary({ ...base, source: "server" })).toMatch(/configurato dall'amministratore/);
    expect(notifySummary({ ...base, configured: false })).toMatch(/Impostazioni → Notifiche email/);
    expect(notifySummary({ ...base, configured: false, source: "server" })).toMatch(/server\.env/);
    expect(notifySummary(null)).toMatch(/non letta/);
  });
});
