import { shortcutHelp } from "../lib/shortcuts";
import { Modal } from "./ui";

/** Pannello "?" con le scorciatoie da tastiera (lib/shortcuts.ts). */
export function ShortcutsHelp({ mac, onClose }: { mac: boolean; onClose: () => void }) {
  return (
    <Modal title="Scorciatoie da tastiera" onClose={onClose}>
      <table className="table shortcuts">
        <tbody>
          {shortcutHelp(mac).map((row) => (
            <tr key={row.label}>
              <td className="nowrap">
                {row.keys.map((k, i) => (
                  <span key={k}>
                    {i > 0 && <span className="muted">{row.keys[0] === "g" ? " poi " : " · "}</span>}
                    <kbd>{k}</kbd>
                  </span>
                ))}
              </td>
              <td>{row.label}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <p className="tiny muted" style={{ marginTop: 10 }}>
        Mentre scrivi in un campo valgono solo {mac ? "⌘" : "Ctrl"} K ed Esc: gli altri tasti restano al campo. Con un dialogo
        aperto la navigazione è sospesa.
      </p>
    </Modal>
  );
}
