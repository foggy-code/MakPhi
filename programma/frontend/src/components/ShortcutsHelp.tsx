import { shortcutHelp } from "../lib/shortcuts";
import { Modal } from "./ui";

type Row = { keys: string[]; label: string };

function Keys({ row }: { row: Row }) {
  return (
    <>
      {row.keys.map((k, i) => (
        <span key={k}>
          {i > 0 && <span className="text-2">{row.keys[0] === "g" ? " poi " : " · "}</span>}
          <kbd>{k}</kbd>
        </span>
      ))}
    </>
  );
}

function Group({ title, rows }: { title: string; rows: Row[] }) {
  return (
    <div className="stack-sm">
      <h4>{title}</h4>
      <div className="table-wrap">
        <table className="table shortcuts">
          <tbody>
            {rows.map((row) => (
              <tr key={row.label}>
                <td className="nowrap" style={{ width: 150 }}><Keys row={row} /></td>
                <td>{row.label}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

/** Pannello "?" con le scorciatoie da tastiera (lib/shortcuts.ts): generali e `g` + lettera per le pagine. */
export function ShortcutsHelp({ mac, onClose }: { mac: boolean; onClose: () => void }) {
  const rows = shortcutHelp(mac);
  return (
    <Modal title="Scorciatoie da tastiera" onClose={onClose}>
      <div className="stack">
        <Group title="Generali" rows={rows.filter((r) => r.keys[0] !== "g")} />
        <Group title="Vai a una pagina" rows={rows.filter((r) => r.keys[0] === "g")} />
        <p className="small text-2">
          Mentre scrivi in un campo valgono solo {mac ? "⌘" : "Ctrl"} K ed Esc: gli altri tasti restano al campo. Con un dialogo
          aperto la navigazione è sospesa. Le lettere delle pagine compaiono anche accanto alle voci della barra laterale.
        </p>
      </div>
    </Modal>
  );
}
