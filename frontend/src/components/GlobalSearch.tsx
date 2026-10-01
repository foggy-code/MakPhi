import { useEffect, useMemo, useRef, useState, type KeyboardEvent } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "../api/client";
import type { SearchGroupType, SearchItem, SearchResponse } from "../api/types";
import { useProject } from "../lib/context";
import { KIND_LABEL, STATUS_LABEL } from "../lib/format";
import { flattenResults, highlightParts, moveSelection, parseSearchQuery, queryForTag, queryKey, resultTarget } from "../lib/search";
import { Icon } from "./Icon";
import { TagChips } from "./Labels";

const GROUP_ICON: Record<SearchGroupType, string> = {
  project: "folder", sequence: "dna", document: "file", job: "activity", template: "library", tag: "tag",
};
const DEBOUNCE_MS = 150;

function Highlighted({ text, query }: { text: string; query: string }) {
  return <>{highlightParts(text, query).map((p, i) => (p.match ? <mark key={i}>{p.text}</mark> : <span key={i}>{p.text}</span>))}</>;
}

/**
 * Ricerca globale (Ctrl+K / ⌘K): progetti, sequenze, documenti, job, template ed etichette. Risultati raggruppati e
 * ordinati dal server; ↑/↓ (Home/End, PagSu/PagGiù) scelgono, Invio apre, Esc chiude. Un'etichetta scelta diventa il
 * filtro `#etichetta`. Senza testo mostra i preferiti.
 */
export function GlobalSearch({ onClose }: { onClose: () => void }) {
  const navigate = useNavigate();
  const { projectId, select } = useProject();
  const [text, setText] = useState("");
  const [result, setResult] = useState<SearchResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [active, setActive] = useState(0);
  const input = useRef<HTMLInputElement>(null);
  const list = useRef<HTMLDivElement>(null);
  const parsed = useMemo(() => parseSearchQuery(text), [text]);
  const key = queryKey(parsed);
  const flat = useMemo(() => flattenResults(result?.groups), [result]);

  useEffect(() => {
    input.current?.focus();
  }, []);

  useEffect(() => {
    let alive = true;
    setLoading(true);
    const timer = setTimeout(() => {
      api.globalSearch({ q: parsed.text, tag: parsed.tag, type: parsed.types, limit: 6 })
        .then((r) => {
          if (!alive) return;
          setResult(r);
          setError(null);
          setActive(0);
        })
        .catch((e) => alive && setError(e instanceof Error ? e.message : String(e)))
        .finally(() => alive && setLoading(false));
    }, DEBOUNCE_MS);
    return () => {
      alive = false;
      clearTimeout(timer);
    };
    // la chiave riassume testo, etichetta e tipi: spazi o maiuscole in più non rifanno la richiesta
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key]);

  useEffect(() => {
    list.current?.querySelector<HTMLElement>(`[data-result="${active}"]`)?.scrollIntoView({ block: "nearest" });
  }, [active]);

  useEffect(() => {
    const onKey = (e: globalThis.KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const open = (item: SearchItem) => {
    if (item.type === "tag") {
      setText(queryForTag(item.id, parsed));
      input.current?.focus();
      return;
    }
    const target = resultTarget(item);
    if (!target) return;
    if (target.projectId && target.projectId !== projectId) select(target.projectId);
    onClose();
    navigate(target.path);
  };

  const onKeyDown = (e: KeyboardEvent<HTMLInputElement>) => {
    if (["ArrowDown", "ArrowUp", "PageDown", "PageUp"].includes(e.key) || ((e.key === "Home" || e.key === "End") && e.ctrlKey)) {
      e.preventDefault();
      setActive((i) => moveSelection(i, e.key, flat.length));
    } else if (e.key === "Enter") {
      e.preventDefault();
      const chosen = flat[active];
      if (chosen) open(chosen.item);
    }
  };

  const groups = result?.groups ?? [];
  return (
    <div className="modal-back palette-back" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div className="palette" role="dialog" aria-modal="true" aria-label="Ricerca globale">
        <div className="palette-input">
          <Icon name="search" />
          <input ref={input} value={text} onChange={(e) => setText(e.target.value)} onKeyDown={onKeyDown}
            placeholder="Cerca progetti, sequenze, documenti, job, template…" aria-label="Cerca"
            role="combobox" aria-expanded={flat.length > 0} aria-controls="palette-results"
            aria-activedescendant={flat[active] ? `palette-result-${active}` : undefined} spellCheck={false} autoComplete="off" />
          {loading && <span className="spinner" style={{ width: 14, height: 14 }} />}
          <kbd>Esc</kbd>
        </div>
        {(parsed.tag || parsed.types.length > 0 || parsed.invalid.length > 0) && (
          <div className="palette-filters small">
            {parsed.tag && <span className="tag-chip">#{parsed.tag}</span>}
            {parsed.types.map((t) => <span key={t} className="badge">{t === "tag" ? "etichette" : t}</span>)}
            {parsed.invalid.length > 0 && <span className="muted">Ignorati: {parsed.invalid.join(" ")} (etichette: lettere, cifre e - _ . : +; tipi: progetti, sequenze, documenti, job, template, etichette)</span>}
          </div>
        )}
        <div className="palette-results" id="palette-results" role="listbox" ref={list}>
          {error && <div className="palette-empty" style={{ color: "var(--err)" }}>{error}</div>}
          {!error && result && !groups.length && (
            <div className="palette-empty">
              {result.favorites ? "Nessun preferito: usa la stella accanto a progetti, sequenze, job e template." : "Nessun risultato."}
            </div>
          )}
          {groups.map((g) => (
            <div key={g.type} className="palette-group">
              <div className="palette-group-head">
                <Icon name={GROUP_ICON[g.type]} size={13} /> {result?.favorites ? `${g.label} preferiti` : g.label}
                {g.truncated && <span className="muted"> · altri risultati: affina la ricerca</span>}
              </div>
              {g.items.map((it) => {
                const index = flat.find((f) => f.item === it)?.index ?? -1;
                const sub = [
                  it.type === "job" && it.kind ? KIND_LABEL[it.kind] ?? it.kind : null,
                  it.type === "job" && it.status ? STATUS_LABEL[it.status] ?? it.status : null,
                  it.type === "job" ? null : it.subtitle,
                  it.project_name && it.type !== "project" ? `progetto ${it.project_name}` : null,
                ].filter(Boolean).join(" · ");
                return (
                  <div key={`${it.type}:${it.id}`} id={`palette-result-${index}`} data-result={index} role="option" aria-selected={index === active}
                    className={`palette-item${index === active ? " active" : ""}`}
                    onMouseMove={() => index !== active && setActive(index)} onMouseDown={(e) => e.preventDefault()} onClick={() => open(it)}>
                    <div className="palette-title">
                      {it.favorite && <Icon name="starFill" size={12} className="star-on" />}
                      <span className="ellipsis"><Highlighted text={it.type === "tag" ? `#${it.title}` : it.title} query={parsed.text} /></span>
                      {it.type === "template" && it.predicted && <span className="badge badge-warn">AlphaFold</span>}
                      <TagChips tags={it.tags} max={3} />
                    </div>
                    {sub && <div className="palette-sub ellipsis">{sub}</div>}
                  </div>
                );
              })}
            </div>
          ))}
        </div>
        <div className="palette-foot tiny muted">
          <span><kbd>↑</kbd> <kbd>↓</kbd> scegli</span>
          <span><kbd>Invio</kbd> apri</span>
          <span><b>#etichetta</b> filtra per etichetta</span>
          <span><b>tipo:job</b> solo un tipo</span>
          {result && <span className="right" title="Indice della ricerca">{result.engine === "fts5" ? "indice FTS5" : "ricerca LIKE"}</span>}
        </div>
      </div>
    </div>
  );
}
