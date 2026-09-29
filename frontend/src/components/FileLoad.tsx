// Pulsanti "Carica da file" riutilizzati in tutte le pagine.
import { useRef, useState, type ReactNode } from "react";
import { api } from "../api/client";
import type { ParsedFile } from "../api/types";
import { useToast } from "../lib/context";
import { readTextFile } from "../lib/files";
import { Icon } from "./Icon";
import { Spinner } from "./ui";

/** Pulsante che apre il selettore di file (accetta anche il trascinamento di un file sul pulsante). */
export function FileButton({ accept, onFile, children = "Carica da file", disabled = false, small = true, title }: {
  accept?: string;
  onFile: (file: File) => Promise<void> | void;
  children?: ReactNode;
  disabled?: boolean;
  small?: boolean;
  title?: string;
}) {
  const input = useRef<HTMLInputElement>(null);
  const [busy, setBusy] = useState(false);
  const [over, setOver] = useState(false);
  const run = async (file: File) => {
    setBusy(true);
    try {
      await onFile(file);
    } finally {
      setBusy(false);
    }
  };
  return (
    <>
      <button
        type="button"
        className={`btn ${small ? "btn-sm" : ""} ${over ? "btn-primary" : ""}`}
        disabled={disabled || busy}
        title={title ?? "Scegli un file oppure trascinalo qui"}
        onClick={() => input.current?.click()}
        onDragOver={(e) => { e.preventDefault(); setOver(true); }}
        onDragLeave={() => setOver(false)}
        onDrop={(e) => {
          e.preventDefault();
          setOver(false);
          const f = e.dataTransfer.files?.[0];
          if (f) run(f);
        }}
      >
        {busy ? <Spinner /> : <Icon name="upload" size={13} />} {children}
      </button>
      <input
        ref={input}
        type="file"
        accept={accept}
        hidden
        onChange={(e) => {
          const f = e.target.files?.[0];
          e.target.value = "";
          if (f) run(f);
        }}
      />
    </>
  );
}

export const SEQUENCE_FILES = ".fasta,.fa,.faa,.fas,.fna,.seq,.txt,.pir,.ali,.aln,.clustal,.sto,.stk,.msf,.gb,.gbk,.genbank,.embl,.dat,.swiss,.pdb,.ent,.cif,.mmcif,.gz";

/** Legge sequenze o allineamenti da file tramite il server (qualunque formato comune) e segnala gli avvisi. */
export function SequenceFileButton({ onLoaded, children = "Carica da file", disabled }: {
  onLoaded: (parsed: ParsedFile) => void;
  children?: ReactNode;
  disabled?: boolean;
}) {
  const toast = useToast();
  return (
    <FileButton
      accept={SEQUENCE_FILES}
      disabled={disabled}
      title="FASTA, PIR, Clustal, Stockholm, MSF, GenBank, EMBL, UniProt, PDB/mmCIF o testo semplice (anche .gz)"
      onFile={async (file) => {
        try {
          const parsed = await api.parseSequenceFile(file);
          parsed.warnings.forEach((w) => toast.push(w, "warn"));
          toast.push(`${file.name}: ${parsed.records.length} ${parsed.records.length === 1 ? "sequenza" : "sequenze"} (${parsed.format_label})`, "info");
          onLoaded(parsed);
        } catch (e) {
          toast.error(e);
        }
      }}
    >
      {children}
    </FileButton>
  );
}

/** Legge un file di testo così com'è (PIR, vincoli, chiavi di licenza). */
export function TextFileButton({ accept, onText, children = "Carica da file", disabled }: {
  accept?: string;
  onText: (text: string, file: File) => void;
  children?: ReactNode;
  disabled?: boolean;
}) {
  const toast = useToast();
  return (
    <FileButton
      accept={accept}
      disabled={disabled}
      onFile={async (file) => {
        try {
          onText(await readTextFile(file), file);
        } catch (e) {
          toast.error(e);
        }
      }}
    >
      {children}
    </FileButton>
  );
}
