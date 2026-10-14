import { api } from "../api/client";
import { useAsync, useInterval } from "../lib/hooks";
import { plural } from "../lib/format";
import { Icon } from "./Icon";

const WORD: Record<string, [string, string]> = {
  completed: ["completato", "completati"],
  failed: ["fallito", "falliti"],
  cancelled: ["annullato", "annullati"],
};

/** Esito dei QMEAN finiti, per stato: "28 completati, 1 fallito". */
export function autoQmeanDetail(counts: Record<string, number>): string {
  return Object.entries(WORD)
    .filter(([status]) => counts[status])
    .map(([status, [one, many]]) => `${counts[status]} ${counts[status] === 1 ? one : many}`)
    .join(", ");
}

/**
 * QMEAN automatico di una modellazione o pipeline completata: finché ci sono QMEAN da accodare, in coda o in corso,
 * avvisa che quanto si scarica ora (`what`, con l'articolo: "Lo ZIP", "Il report") non li contiene ancora; poi dice
 * quanti sono finiti.
 * Niente se il job non ha chiesto QMEAN automatico.
 */
export function AutoQmeanNotice({ jobId, completed, what }: { jobId: string; completed: boolean; what: string }) {
  const { data, reload } = useAsync(() => (completed ? api.autoQmean(jobId) : Promise.resolve(null)), [jobId, completed]);
  useInterval(reload, data?.pending ? 4000 : null);
  if (!data?.requested) return null;
  if (data.pending) {
    return (
      <div className="inline-notice" role="status">
        <Icon name="activity" size={15} />
        <span>
          QMEAN automatico in corso: {data.total ? `${data.done} di ${plural(data.total, "calcolo", "calcoli")} finiti` : "calcoli in preparazione"}.
          {" "}{what} scaricato ora non contiene ancora i QMEAN mancanti: conviene aspettare la fine (l'avviso per email, se richiesto, parte allora).
        </span>
      </div>
    );
  }
  if (!data.total) return null;
  return (
    <p className="small text-2">
      QMEAN automatico terminato: {plural(data.total, "calcolo", "calcoli")} ({autoQmeanDetail(data.counts)}). {what} scaricato ora li contiene.
    </p>
  );
}
