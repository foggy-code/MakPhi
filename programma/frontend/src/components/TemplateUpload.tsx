import { useRef, useState } from "react";
import { api } from "../api/client";
import type { TemplateItem } from "../api/types";
import { useToast } from "../lib/context";
import { replaceQuestion } from "../lib/templateUpload";
import { Confirm } from "./ui";

/**
 * Caricamento di un file nella libreria dei template (pagina Libreria, «Da file» del selettore dei template). Se il
 * server risponde che un template con lo stesso id esiste già (409 `template_exists`) si chiede conferma con il suo
 * messaggio: «Sostituisci» ripete il caricamento con `overwrite`, «Annulla» lascia la libreria com'è (per tenerli
 * entrambi si ricarica il file con un altro nome: qui non c'è un campo per l'id, che il server prende dal nome).
 * `onUploaded` riceve il template caricato e se ne ha sostituito uno; `dialog` va disegnato dalla pagina, `busy` vale
 * durante l'invio (anche quello dopo «Sostituisci»).
 */
export function useTemplateUpload(onUploaded: (t: TemplateItem, replaced: boolean) => void) {
  const toast = useToast();
  const [pending, setPending] = useState<{ file: File; message: string } | null>(null);
  const [busy, setBusy] = useState(false);
  // la risposta arriva dopo qualche render: si chiama l'ultima `onUploaded` (valori attuali), non quella del clic
  const done = useRef(onUploaded);
  done.current = onUploaded;
  const send = async (file: File, overwrite: boolean) => {
    setBusy(true);
    try {
      const t = await api.uploadTemplate(file, { overwrite });
      done.current(t, overwrite);
    } catch (e) {
      const question = replaceQuestion(e, overwrite);
      if (question) setPending({ file, message: question });
      else toast.error(e);
    } finally {
      setBusy(false);
    }
  };
  const dialog = pending && (
    <Confirm
      text={<>
        {pending.message} «Sostituisci» mette al suo posto il file «{pending.file.name}». Per tenerli entrambi annulla e
        ricarica il file con un altro nome: l'identificativo del template viene dal nome del file.
      </>}
      confirmLabel="Sostituisci"
      onCancel={() => setPending(null)}
      onConfirm={() => {
        setPending(null);
        void send(pending.file, true);
      }}
    />
  );
  return { upload: (file: File) => send(file, false), busy, dialog };
}
