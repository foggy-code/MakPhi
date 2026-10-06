import { describe, expect, it } from "vitest";
import { isEditableTarget, isMacPlatform, NAV_SHORTCUTS, shortcutFor, shortcutHelp, type KeyLike, type ShortcutState } from "./shortcuts";

const idle: ShortcutState = { pending: false, dialogOpen: false, mac: false };
const key = (k: string, extra: Partial<KeyLike> = {}): KeyLike => ({ key: k, ...extra });
const input = { tagName: "INPUT", type: "text" };

describe("scorciatoie globali", () => {
  it("Ctrl+K su Windows/Linux, ⌘K su Mac, anche mentre si scrive", () => {
    expect(shortcutFor(key("k", { ctrlKey: true }), idle)).toEqual({ type: "search" });
    expect(shortcutFor(key("K", { ctrlKey: true, target: input }), idle)).toEqual({ type: "search" });
    expect(shortcutFor(key("k", { metaKey: true }), { ...idle, mac: true })).toEqual({ type: "search" });
    expect(shortcutFor(key("k", { metaKey: true }), idle)).toBeNull(); // ⌘ fuori dal Mac: tasto di sistema
    expect(shortcutFor(key("k", { ctrlKey: true, shiftKey: true }), idle)).toBeNull();
    expect(shortcutFor(key("k", { ctrlKey: true }), { ...idle, dialogOpen: true })).toEqual({ type: "search" });
  });

  it("/ e ? fuori dai campi di testo", () => {
    expect(shortcutFor(key("/"), idle)).toEqual({ type: "search" });
    expect(shortcutFor(key("?", { shiftKey: true }), idle)).toEqual({ type: "help" });
    expect(shortcutFor(key("/", { target: input }), idle)).toBeNull();
    expect(shortcutFor(key("?", { target: { tagName: "TEXTAREA" } }), idle)).toBeNull();
    expect(shortcutFor(key("?", { target: { tagName: "DIV", isContentEditable: true } }), idle)).toBeNull();
    expect(shortcutFor(key("/", { ctrlKey: true }), idle)).toBeNull();
    expect(shortcutFor(key("?"), { ...idle, dialogOpen: true })).toBeNull();
  });

  it("g poi una lettera: navigazione alle pagine principali", () => {
    expect(shortcutFor(key("g"), idle)).toEqual({ type: "pending" });
    expect(shortcutFor(key("s"), { ...idle, pending: true })).toEqual({ type: "navigate", path: "/sequences" });
    expect(shortcutFor(key("J"), { ...idle, pending: true })).toEqual({ type: "navigate", path: "/jobs" });
    expect(shortcutFor(key("z"), { ...idle, pending: true })).toBeNull();
    expect(shortcutFor(key("s"), idle)).toBeNull(); // senza g la lettera resta alla pagina
    expect(shortcutFor(key("g", { target: input }), idle)).toBeNull();
    expect(shortcutFor(key("s", { target: input }), { ...idle, pending: true })).toBeNull();
    expect(shortcutFor(key("g"), { ...idle, dialogOpen: true })).toBeNull();
    expect(shortcutFor(key("g", { repeat: true }), idle)).toBeNull();
    expect(shortcutFor(key("g", { altKey: true }), idle)).toBeNull();
  });

  it("lettere uniche, una per pagina", () => {
    const keys = NAV_SHORTCUTS.map((n) => n.key);
    expect(new Set(keys).size).toBe(keys.length);
    expect(new Set(NAV_SHORTCUTS.map((n) => n.path)).size).toBe(keys.length);
  });

  it("campi in cui si scrive", () => {
    expect(isEditableTarget(input)).toBe(true);
    expect(isEditableTarget({ tagName: "input" })).toBe(true); // type assente = text
    expect(isEditableTarget({ tagName: "INPUT", type: "search" })).toBe(true);
    expect(isEditableTarget({ tagName: "INPUT", type: "checkbox" })).toBe(false);
    expect(isEditableTarget({ tagName: "INPUT", type: "radio" })).toBe(false);
    expect(isEditableTarget({ tagName: "SELECT" })).toBe(true);
    expect(isEditableTarget({ tagName: "BUTTON" })).toBe(false);
    expect(isEditableTarget(null)).toBe(false);
  });

  it("pannello: tasti della piattaforma e tutte le pagine", () => {
    expect(shortcutHelp(true)[0].keys[0]).toBe("⌘ K");
    expect(shortcutHelp(false)[0].keys[0]).toBe("Ctrl K");
    expect(shortcutHelp(false).filter((h) => h.keys[0] === "g")).toHaveLength(NAV_SHORTCUTS.length);
    expect(isMacPlatform({ platform: "MacIntel" })).toBe(true);
    expect(isMacPlatform({ platform: "Win32", userAgent: "Mozilla/5.0 (Windows NT 10.0)" })).toBe(false);
    expect(isMacPlatform(null)).toBe(false);
  });
});
