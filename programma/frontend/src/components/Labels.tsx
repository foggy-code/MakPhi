import { useEffect, useState, type MouseEvent } from "react";
import { api } from "../api/client";
import type { EntityLabels, EntityType, TagCount } from "../api/types";
import { useToast } from "../lib/context";
import { filterOptions, filterValue, labelsKey, MAX_TAGS_PER_ENTITY, normalizeTag, parseFilterValue, type ItemLabels, type LabelFilter } from "../lib/tags";
import { Icon } from "./Icon";

/** Etichette e stella (F8b): componenti condivisi da elenchi e dettagli. */

const TYPE_NAME: Record<EntityType, string> = { project: "progetto", job: "job", sequence: "sequenza", template: "template" };

/** Stella dei preferiti; il clic non seleziona la riga che la contiene. */
export function FavoriteStar({ type, id, value, onChange, size = 15 }: { type: EntityType; id: string; value: boolean; onChange?: (labels: EntityLabels) => void; size?: number }) {
  const toast = useToast();
  const [on, setOn] = useState(value);
  const [busy, setBusy] = useState(false);
  useEffect(() => setOn(value), [value]);
  const toggle = (e: MouseEvent) => {
    e.stopPropagation();
    if (busy) return;
    const next = !on;
    setOn(next); // subito, poi l'esito del server
    setBusy(true);
    api.setFavorite(type, id, next)
      .then((labels) => {
        setOn(labels.favorite);
        onChange?.(labels);
      })
      .catch((err) => {
        setOn(!next);
        toast.error(err);
      })
      .finally(() => setBusy(false));
  };
  return (
    <button type="button" className={`star-btn${on ? " on" : ""}`} onClick={toggle} aria-pressed={on}
      title={on ? `Togli il ${TYPE_NAME[type]} dai preferiti` : `Aggiungi il ${TYPE_NAME[type]} ai preferiti`}
      aria-label={on ? "Togli dai preferiti" : "Aggiungi ai preferiti"}>
      <Icon name={on ? "starFill" : "star"} size={size} />
    </button>
  );
}

/** Etichette come chip; `onPick` le rende cliccabili (es. per filtrare), `onRemove` aggiunge la ×. */
export function TagChips({ tags, onPick, onRemove, max }: { tags?: string[] | null; onPick?: (tag: string) => void; onRemove?: (tag: string) => void; max?: number }) {
  if (!tags?.length) return null;
  const shown = max !== undefined ? tags.slice(0, max) : tags;
  return (
    <span className="tag-list">
      {shown.map((t) => (
        <span key={t} className={`tag-chip${onPick ? " pickable" : ""}`} title={onPick ? `Filtra per #${t}` : `#${t}`}
          role={onPick ? "button" : undefined} tabIndex={onPick ? 0 : undefined}
          onClick={onPick ? (e) => { e.stopPropagation(); onPick(t); } : undefined}
          onKeyDown={onPick ? (e) => {
            if (e.key !== "Enter" && e.key !== " ") return;
            e.preventDefault();
            e.stopPropagation();
            onPick(t);
          } : undefined}>
          #{t}
          {onRemove && (
            <button type="button" aria-label={`Togli l'etichetta ${t}`} onClick={(e) => { e.stopPropagation(); onRemove(t); }}>×</button>
          )}
        </span>
      ))}
      {shown.length < tags.length && <span className="tiny text-2" title={tags.slice(shown.length).map((t) => `#${t}`).join(" ")}>+{tags.length - shown.length}</span>}
    </span>
  );
}

let suggestionsCache: { at: number; data: TagCount[] } | null = null;

/** Etichette in uso (per i suggerimenti), lette al più ogni 30 s. */
function useTagSuggestions(): TagCount[] {
  const [data, setData] = useState<TagCount[]>(suggestionsCache?.data ?? []);
  useEffect(() => {
    if (suggestionsCache && Date.now() - suggestionsCache.at < 30_000) return;
    api.tags().then((d) => {
      suggestionsCache = { at: Date.now(), data: d };
      setData(d);
    }).catch(() => undefined);
  }, []);
  return data;
}

function forgetSuggestions() {
  suggestionsCache = null;
}

/**
 * Stella ed etichette di un elemento, modificabili: chip con la ×, campo per aggiungerne una (Invio) con l'etichetta
 * normalizzata mostrata mentre si scrive e i suggerimenti delle etichette già usate.
 *
 * `initial`: stella ed etichette dell'elemento nell'elenco della pagina (`itemLabels`), che è la fonte dei dati: quando
 * cambiano (stella cliccata nella riga, dialogo dei progetti) il dettaglio si riallinea. Senza `initial` (elemento fuori
 * dall'elenco) le legge dal server.
 */
export function LabelEditor({ type, id, initial, onChange, compact = false }: { type: EntityType; id: string; initial?: ItemLabels | null; onChange?: (labels: EntityLabels) => void; compact?: boolean }) {
  const toast = useToast();
  const [labels, setLabels] = useState<ItemLabels | null>(initial ?? null);
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const suggestions = useTagSuggestions();
  const initialKey = labelsKey(initial);
  useEffect(() => {
    if (initial) {
      setLabels(initial);
      return;
    }
    let alive = true;
    setLabels(null);
    api.entityLabels(type, id).then((l) => alive && setLabels(l)).catch(() => alive && setLabels({ tags: [], favorite: false }));
    return () => {
      alive = false;
    };
    // `initial` cambia oggetto a ogni disegno della pagina: conta solo il suo valore (initialKey)
    // eslint-disable-next-line react-hooks/exhaustive-deps -- conta il valore di initial (initialKey), non l'oggetto
  }, [type, id, initialKey]);
  const update = (l: EntityLabels) => {
    setLabels(l);
    forgetSuggestions();
    onChange?.(l);
  };
  const check = text.trim() ? normalizeTag(text) : null;
  const full = (labels?.tags.length ?? 0) >= MAX_TAGS_PER_ENTITY;
  const add = () => {
    if (!check?.ok || busy) return;
    setBusy(true);
    api.addTag(type, id, check.tag).then((l) => { update(l); setText(""); }).catch(toast.error).finally(() => setBusy(false));
  };
  const remove = (tag: string) => api.removeTag(type, id, tag).then(update).catch(toast.error);
  const listId = `tag-suggestions-${type}`;
  return (
    <div className={`label-editor${compact ? " compact" : ""}`} onClick={(e) => e.stopPropagation()}>
      {labels && <FavoriteStar type={type} id={id} value={labels.favorite} onChange={update} />}
      <TagChips tags={labels?.tags} onRemove={remove} />
      <span className="tag-input">
        <Icon name="tag" size={13} />
        <input className="input" list={listId} value={text} placeholder={full ? "Limite di etichette raggiunto" : "Aggiungi etichetta"} disabled={full || !labels}
          aria-label="Nuova etichetta" maxLength={60} onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") {
              e.preventDefault();
              add();
            }
          }} />
        <datalist id={listId}>
          {suggestions.filter((s) => !labels?.tags.includes(s.tag)).slice(0, 30).map((s) => <option key={s.tag} value={s.tag} />)}
        </datalist>
        {check && (check.ok
          ? check.tag !== text.trim() && <span className="tiny text-2" title="Etichetta che verrà salvata">→ #{check.tag}</span>
          : <span className="tiny" style={{ color: "var(--err)" }}>{check.error}</span>)}
      </span>
    </div>
  );
}

/** Filtro di un elenco: tutti, solo preferiti, oppure un'etichetta usata da quel tipo di elemento. */
export function LabelFilterSelect({ type, value, onChange, width = 170 }: { type: EntityType; value: LabelFilter; onChange: (f: LabelFilter) => void; width?: number }) {
  const [counts, setCounts] = useState<TagCount[] | null>(null);
  const load = () => api.tags(type).then(setCounts).catch(() => setCounts([]));
  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps -- load dipende solo da type
  }, [type]);
  const options = filterOptions(counts, type);
  const current = filterValue(value);
  if (current && !options.some((o) => o.value === current)) options.push({ value: current, label: `#${current}` });
  return (
    <select className="input input-sm" style={{ width }} value={current} aria-label="Filtra per etichetta"
      onFocus={load} onChange={(e) => onChange(parseFilterValue(e.target.value))}>
      {options.map((o) => <option key={o.value || "all"} value={o.value}>{o.label}</option>)}
    </select>
  );
}
