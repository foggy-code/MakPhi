import { residueText } from "../lib/compareMetrics";
import { blosumTone, type MutationReport, type PirMutation } from "../lib/pirMutations";
import { Alert, Badge, Card, CopyButton } from "./ui";

const TONE: Record<string, { tone: "ok" | "info" | "err"; text: string }> = {
  conservative: { tone: "ok", text: "conservativa" },
  neutral: { tone: "info", text: "neutra" },
  radical: { tone: "err", text: "radicale" },
};
const signed = (n: number) => (n > 0 ? `+${n}` : String(n).replace("-", "−"));

/**
 * Sostituzioni fatte nell'Editor PIR rispetto alle sequenze originali del documento: notazione K48R con la numerazione
 * della voce, punteggio BLOSUM62, salto alla cella nella griglia e annullamento di una o di tutte.
 */
export function PirMutations({ report, onGo, onRevert, onResetReference }: {
  report: MutationReport;
  onGo: (m: PirMutation) => void;
  onRevert: (list: PirMutation[]) => void;
  /** le sequenze attuali diventano il riferimento (dopo modifiche di lunghezza nel testo PIR) */
  onResetReference: () => void;
}) {
  const { mutations, lengthChanged, duplicated } = report;
  if (!mutations.length && !lengthChanged.length && !duplicated.length) return null;
  const onTemplates = mutations.filter((m) => m.kind.startsWith("structure"));
  const byEntry = new Map<string, PirMutation[]>();
  for (const m of mutations) byEntry.set(m.code, [...(byEntry.get(m.code) ?? []), m]);
  const notation = [...byEntry].map(([code, list]) => `${code}: ${list.map((m) => m.label).join(", ")}`).join("\n");
  return (
    <Card
      title="Sostituzioni"
      icon="flask"
      sub={mutations.length ? `${mutations.length} rispetto alle sequenze originali` : undefined}
      right={mutations.length > 0 && (
        <>
          <CopyButton text={notation} label="Copia elenco" />
          <button type="button" className="btn btn-sm" onClick={() => onRevert(mutations)} title="Riporta tutti i residui a quelli originali">Annulla tutte</button>
        </>
      )}
    >
      <div className="stack">
        {onTemplates.length > 0 && (
          <Alert tone="warn">
            {onTemplates.length === 1 ? "Un residuo di template è cambiato" : `${onTemplates.length} residui di template sono cambiati`}: MODELLER legge le
            coordinate dal file della struttura e si ferma se il residuo nell'allineamento non corrisponde. Va bene solo per correggere un PIR che non
            corrispondeva già.
          </Alert>
        )}
        {lengthChanged.length > 0 && (
          <Alert tone="info">
            <div className="stack-sm">
              <span>
                {lengthChanged.join(", ")}: il numero di residui è cambiato nel testo PIR, quindi le sostituzioni (contate per posizione) non si possono
                più elencare per {lengthChanged.length === 1 ? "questa voce" : "queste voci"}.
              </span>
              <span><button type="button" className="btn btn-sm" onClick={onResetReference}>Usa le sequenze attuali come riferimento</button></span>
            </div>
          </Alert>
        )}
        {duplicated.length > 0 && (
          <Alert tone="info">
            Codici ripetuti ({duplicated.join(", ")}): quelle voci non si confrontano con l'originale, perché non si saprebbe a quale riferirle.
            Dai a ogni voce un codice diverso.
          </Alert>
        )}
        {mutations.length > 0 && (
          <div className="table-wrap" style={{ maxHeight: 320, overflowY: "auto" }}>
            <table className="table">
              <thead>
                <tr><th>Voce</th><th>Sostituzione</th><th>BLOSUM62</th><th className="num">Colonna</th><th /></tr>
              </thead>
              <tbody>
                {mutations.map((m) => {
                  const tone = TONE[blosumTone(m.blosum)];
                  return (
                    <tr key={`${m.row}-${m.index}`}>
                      <td>
                        <b className="mono">{m.code}</b>{" "}
                        {m.kind.startsWith("structure") ? <Badge tone="warn">template</Badge> : <Badge tone="accent">bersaglio</Badge>}
                      </td>
                      <td>
                        <b className="mono">{m.label}</b>
                        <div className="tiny text-2">{residueText(m.wt)} → {residueText(m.mut)}</div>
                      </td>
                      <td>{m.blosum === null ? "—" : <Badge tone={tone.tone}>{signed(m.blosum)} · {tone.text}</Badge>}</td>
                      <td className="num">{m.column + 1}</td>
                      <td className="num" style={{ whiteSpace: "nowrap" }}>
                        <button type="button" className="btn btn-sm btn-ghost" onClick={() => onGo(m)} title="Porta il cursore della griglia su questo residuo">Vai</button>
                        <button type="button" className="btn btn-sm" onClick={() => onRevert([m])} title={`Riporta il residuo a ${residueText(m.wt)}`}>Annulla</button>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
        <p className="small text-2">
          Numerazione per catena, come nei modelli di MakPhi: la prima catena parte dal primo residuo indicato nella seconda riga PIR (1 se manca),
          le successive (dopo «/») da 1. Con più catene la sostituzione porta il nome della catena (B:K2R): la prima e l'ultima vengono dalla seconda
          riga PIR, quelle in mezzo si presumono in ordine alfabetico. Per i template, se la numerazione del file della struttura salta, i numeri
          successivi sono solo progressivi.
        </p>
      </div>
    </Card>
  );
}
