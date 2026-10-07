import type { PirSecondaryEntry } from "../api/types";
import { conflictText, toSS3, type GapConflict } from "../lib/pirSecondary";
import { fmtNum } from "../lib/format";
import { Alert, Badge, Card, Spinner } from "./ui";

const MATCHED: Record<string, string> = {
  code: "abbinato al template del documento per codice",
  position: "abbinato al template del documento per posizione (il codice non corrisponde): controlla che sia quello giusto",
  code_guess: "trovato nella libreria dal codice della voce",
};

/** Percentuali di elica e filamento sui residui con uno stato noto. */
function composition(ss: string): string {
  const states = ss.split("").map(toSS3).filter((s) => s !== "?");
  if (!states.length) return "nessun residuo assegnato";
  const pct = (k: string) => fmtNum((100 * states.filter((s) => s === k).length) / states.length, 0);
  return `${pct("H")}% elica · ${pct("E")}% filamento`;
}

/**
 * Struttura secondaria delle voci del PIR: fonte per voce (DSSP del template, predizione GOR del bersaglio), lettere
 * che non corrispondono al file della struttura, gap che spezzano un'elica o un filamento con il salto alla colonna.
 */
export function PirSecondaryCard({ entries, conflicts, codes, enabled, loading, error, onToggle, onGoColumn, onGoResidue }: {
  entries: (PirSecondaryEntry | null)[];
  conflicts: GapConflict[];
  /** codici delle voci, nell'ordine delle righe */
  codes: string[];
  enabled: boolean;
  loading: boolean;
  error: string | null;
  onToggle: (on: boolean) => void;
  onGoColumn: (row: number, column: number) => void;
  /** riga e residuo (da 1) della voce */
  onGoResidue: (row: number, index: number) => void;
}) {
  const toggle = (
    <label className="check">
      <input type="checkbox" checked={enabled} onChange={(e) => onToggle(e.target.checked)} />
      Mostra nella griglia
    </label>
  );
  return (
    <Card title="Struttura secondaria" icon="layers" sub={enabled && conflicts.length ? `${conflicts.length} zone da controllare` : undefined} right={toggle}>
      {!enabled ? (
        <p className="small text-2">Attiva per vedere sotto ogni voce il DSSP dei template e la predizione del bersaglio, e i gap che spezzano un'elica o un filamento.</p>
      ) : (
        <div className="stack">
          <div className="legend">
            <span><i style={{ background: "var(--helix)" }} />elica</span>
            <span><i style={{ background: "var(--strand)" }} />filamento β</span>
            <span><i style={{ background: "var(--coil)", height: 2 }} />coil</span>
            <span><i style={{ background: "var(--err-soft)", outline: "2px solid var(--err)" }} />gap dentro un elemento</span>
            <span className="text-2">template: DSSP sul file della struttura · bersaglio: predizione GOR (euristica, tenue)</span>
          </div>
          {loading && <Spinner label="Calcolo della struttura secondaria" />}
          {error && <Alert tone="warn">Struttura secondaria non disponibile: {error}</Alert>}
          {entries.some(Boolean) && (
            <div className="table-wrap">
              <table className="table">
                <thead><tr><th>Voce</th><th>Fonte</th><th>Composizione</th><th>Note</th></tr></thead>
                <tbody>
                  {entries.map((e, row) => e && (
                    <tr key={`${row}-${e.code}`}>
                      <td><b className="mono">{codes[row] ?? e.code}</b></td>
                      <td>
                        {e.source === "dssp" ? <Badge tone="ok">DSSP</Badge> : e.source === "gor" ? <Badge tone="info">predetta</Badge> : <Badge>non disponibile</Badge>}
                        {e.template && <div className="tiny text-2">{e.template.id}{e.template.chains.length ? ` · catene ${e.template.chains.join(", ")}` : ""}</div>}
                      </td>
                      <td>{e.ss ? composition(e.ss) : "—"}</td>
                      <td className="small">
                        <div className="stack-sm">
                          {e.message && <span>{e.message}</span>}
                          {e.matched_by && e.matched_by !== "code" && <span className="text-2">{MATCHED[e.matched_by]}</span>}
                          {e.problems.map((p) => <span key={p} style={{ color: "var(--warn)" }}>{p}</span>)}
                          {e.mismatch_count > 0 && (
                            <span style={{ color: "var(--err)" }}>
                              {e.mismatch_count === 1 ? "1 lettera non corrisponde" : `${e.mismatch_count} lettere non corrispondono`} al file della struttura: MODELLER si fermerà.{" "}
                              {e.mismatches.slice(0, 5).map((m) => (
                                <button key={m.index} type="button" className="btn btn-sm btn-ghost" onClick={() => onGoResidue(row, m.index)}
                                  title={`Porta il cursore sul residuo ${m.index} della voce`}>
                                  {m.letter}{m.index} (file: {m.residue})
                                </button>
                              ))}
                            </span>
                          )}
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          {conflicts.length > 0 ? (
            <div className="stack-sm">
              <b className="small">Gap che spezzano un'elica o un filamento di un template</b>
              <div className="table-wrap" style={{ maxHeight: 260, overflowY: "auto" }}>
                <table className="table">
                  <thead><tr><th>Template</th><th>Zona</th><th className="num">Colonne</th><th /></tr></thead>
                  <tbody>
                    {conflicts.map((c) => (
                      <tr key={`${c.row}-${c.from}`}>
                        <td><b className="mono">{codes[c.row]}</b></td>
                        <td>{conflictText(c)}</td>
                        <td className="num">{c.from === c.to ? c.from + 1 : `${c.from + 1}–${c.to + 1}`}</td>
                        <td className="num"><button type="button" className="btn btn-sm btn-ghost" onClick={() => onGoColumn(c.row, c.from)}>Vai</button></td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <p className="small text-2">
                Lì MODELLER deve allungare o accorciare un elemento regolare del template: spesso conviene spostare il gap nell'ansa più vicina.
                Con «Vai» il cursore va sulla zona: seleziona i gap (Maiusc+frecce) e spostali con Alt+frecce, scavalcano i residui. Sono
                indicazioni da controllare, non errori.
              </p>
            </div>
          ) : (
            !loading && entries.some((e) => e?.source === "dssp") && <p className="small text-2">Nessun gap dentro un'elica o un filamento dei template.</p>
          )}
        </div>
      )}
    </Card>
  );
}
