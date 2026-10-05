// Rifinitura energetica con OpenMM (F9.1): finestra di avvio (dalla tabella dei modelli o da una struttura qualunque)
// e risultati di un job `refine` nella pagina Modellazione, con il confronto prima/dopo, i termini dell'energia, i link a
// Qualità e Confronto e il report HTML del modello rifinito (services/reports.py).
// La logica è in lib/refine.ts, il protocollo nel backend (domain/refinement.py).
import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api } from "../api/client";
import type { RefineEnergyComponents, RefineResult, StructureSource, SystemInfo } from "../api/types";
import { useProject, useToast } from "../lib/context";
import { fmtNum, fmtSigned } from "../lib/format";
import { useAsync } from "../lib/hooks";
import { comparisonSummary, energyText, fmtDelta, fmtRefineValue, MD_DEFAULT_PS, MD_MAX_PS, refineCompareLink, refinedQualityLink, refineRequest, verdictTone } from "../lib/refine";
import { missingTools, missingToolsText } from "../lib/toolJobs";
import { Icon } from "./Icon";
import { ReportButton } from "./ReportButton";
import { Viewer3D } from "./Viewer3D";
import { Alert, Badge, Card, DetailGrid, Disclosure, Field, Grade, Modal, Stat } from "./ui";
import "../styles/tools.css";

const PROTOCOL_TEXT = "PDBFixer completa gli atomi pesanti e aggiunge gli idrogeni a pH 7; OpenMM minimizza l'energia con il campo di forza AMBER ff14SB e il solvente implicito OBC2, tenendo la catena principale (N, CA, C, O) vicina alle posizioni di partenza con restrizioni armoniche di 10 kcal/mol/Å². Ligandi, ioni e acque sono esclusi; nessun residuo mancante viene ricostruito.";

/** Termini dell'energia del campo di forza, nell'ordine della tabella. */
const ENERGY_TERMS: { key: keyof RefineEnergyComponents; label: string }[] = [
  { key: "bonds", label: "Legami" },
  { key: "angles", label: "Angoli" },
  { key: "torsions", label: "Torsioni" },
  { key: "nonbonded", label: "Non legate" },
  { key: "solvation", label: "Solvatazione (OBC2)" },
  { key: "total", label: "Totale" },
];

/** Finestra di avvio: dinamica facoltativa e idrogeni nel PDB; dopo l'avvio apre il job nella pagina Modellazione. */
export function RefineDialog({ source, label, onClose }: { source: StructureSource; label: string; onClose: () => void }) {
  const { projectId } = useProject();
  const toast = useToast();
  const navigate = useNavigate();
  const [md, setMd] = useState(false);
  const [ps, setPs] = useState(MD_DEFAULT_PS);
  const [hydrogens, setHydrogens] = useState(false);
  const { data: info } = useAsync<SystemInfo>(() => api.systemInfo(), []);
  const missing = missingToolsText(missingTools(info, ["openmm", "pdbfixer"]), "Rifinitura energetica");
  const req = refineRequest(md, ps, hydrogens);
  const submit = async () => {
    if (!req.body) return;
    try {
      const j = await api.refine({ project_id: projectId ?? undefined, source, ...req.body });
      toast.push("Rifinitura energetica avviata");
      onClose();
      navigate(`/modeling?job=${j.id}`);
    } catch (e) {
      toast.error(e);
    }
  };
  return (
    <Modal title={`Rifinitura energetica di ${label}`} onClose={onClose}
      footer={<><button type="button" className="btn" onClick={onClose}>Annulla</button><button type="button" className="btn btn-primary" disabled={!!missing || !req.body} onClick={submit}><Icon name="play" size={13} /> Avvia</button></>}>
      <div className="stack">
        {missing && <Alert tone="warn">{missing}</Alert>}
        <p className="small text-2">{PROTOCOL_TEXT} Alla fine BioLoom confronta la qualità prima e dopo (Ramachandran, clashscore, DOPE) e lo spostamento della catena principale.</p>
        <label className="check"><input type="checkbox" checked={md} onChange={(e) => setMd(e.target.checked)} /> Breve dinamica molecolare a 300 K con le stesse restrizioni, poi una seconda minimizzazione</label>
        {md && (
          <Field label="Durata della dinamica (ps)" hint={`Da 0,5 a ${MD_MAX_PS} ps; passo di 2 fs. Sulla CPU 10 ps di una proteina di 76 residui richiedono circa 2 minuti.`}>
            <input className="input" style={{ width: 110 }} type="number" min={0.5} max={MD_MAX_PS} step={0.5} value={ps} onChange={(e) => setPs(Number(e.target.value))} />
          </Field>
        )}
        <label className="check"><input type="checkbox" checked={hydrogens} onChange={(e) => setHydrogens(e.target.checked)} /> Conserva gli idrogeni nel PDB rifinito</label>
        {req.error && <span className="small" style={{ color: "var(--warn)" }}>{req.error}</span>}
      </div>
    </Modal>
  );
}

/** Termini dell'energia prima, dopo ogni fase e alla fine (kJ/mol), con la differenza finale. */
function EnergyTerms({ components }: { components: RefineResult["energy"]["components"] }) {
  const stages: { label: string; values: RefineEnergyComponents }[] = [
    { label: "Prima", values: components.before },
    { label: "Dopo la minimizzazione", values: components.after_minimization },
    ...(components.after_md ? [{ label: "Dopo la dinamica", values: components.after_md }] : []),
    { label: "Finale", values: components.final },
  ];
  return (
    <div className="table-wrap">
      <table className="table">
        <thead>
          <tr><th>Termine</th>{stages.map((s) => <th key={s.label} className="num">{s.label}</th>)}<th className="num">Differenza</th></tr>
        </thead>
        <tbody>
          {ENERGY_TERMS.map((t) => {
            const delta = components.final[t.key] - components.before[t.key];
            return (
              <tr key={t.key} className={t.key === "total" ? "row-open" : undefined}>
                <td>{t.key === "total" ? <b>{t.label}</b> : t.label}</td>
                {stages.map((s) => <td key={s.label} className="num">{fmtNum(s.values[t.key], 1)}</td>)}
                {/* solo il totale ha un verso "migliore": l'energia che scende */}
                <td className="num" style={t.key === "total" ? { color: delta < 0 ? "var(--ok)" : delta > 0 ? "var(--err)" : undefined, fontWeight: 650 } : undefined}>{fmtSigned(delta, 1)}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

/** Risultati di un job `refine`: energia, spostamento della catena principale, confronto prima/dopo, termini
 *  dell'energia, 3D, link. */
export function RefineResults({ jobId, result }: { jobId: string; result: RefineResult }) {
  const counts = comparisonSummary(result.comparison);
  const after = result.quality.after;
  const before = result.quality.before;
  const md = result.protocol.md;
  const e = result.energy;
  const input = result.input;
  return (
    <div className="stack">
      {result.warnings.map((w, i) => <Alert key={i} tone="warn">{w}</Alert>)}
      <div className="grid grid-4">
        <Stat label="Voto dopo la rifinitura" value={<span className="row"><Grade grade={after.grade} large /> <span>{fmtNum(after.grade_score, 1)}<small>/100</small></span></span>} note={`prima ${before.grade} (${fmtNum(before.grade_score, 1)})`} />
        <Stat label="Energia (campo di forza)" value={<span style={{ fontSize: 16 }}>{energyText(e)}</span>} note={e.decreased ? "diminuita (senza il termine delle restrizioni)" : "non diminuita"}
          tone={e.decreased ? "ok" : "warn"}
          detail={
            <DetailGrid items={[
              { label: "Prima → dopo", value: `${fmtNum(e.before, 1)} → ${fmtNum(e.after, 1)} ${e.unit}` },
              { label: "Restrizioni alla fine", value: `${fmtNum(e.restraint_final, 2)} ${e.unit}` },
            ]} />
          } />
        <Stat label="RMSD catena principale" value={<>{fmtNum(result.rmsd.rmsd, 2)}<small> Å</small></>} note={`${result.rmsd.atoms} atomi N, CA, C, O; spostamento massimo ${fmtNum(result.rmsd.max_displacement, 2)} Å (${result.rmsd.max_atom ?? "—"})`}
          detail={<span>Senza sovrapposizione: {fmtNum(result.rmsd.rmsd_no_fit, 2)} Å (le restrizioni tengono ferma la catena principale, quindi i due valori sono vicini).</span>} />
        <Stat label="Clashscore" value={<>{fmtNum(before.clashscore, 2)} → {fmtNum(after.clashscore, 2)}</>} note={`${counts.better} metriche migliori, ${counts.worse} peggiori, ${counts.same} invariate`}
          tone={counts.worse > counts.better ? "warn" : counts.better ? "ok" : undefined} />
      </div>
      <Card icon="compare" title="Confronto prima/dopo" sub="prima = copia pulita data a OpenMM (stessi residui, senza ligandi né idrogeni)"
        right={
          <>
            <Link className="btn btn-sm" to={refinedQualityLink(jobId, result)}><Icon name="shield" size={13} /> Qualità del rifinito</Link>
            <Link className="btn btn-sm" to={refineCompareLink(jobId, result)} title="Sovrapposizione e deviazione per residuo rispetto alla struttura di partenza"><Icon name="compare" size={13} /> Confronta con l'originale</Link>
            <a className="btn btn-sm" href={api.jobFileUrl(jobId, result.refined_file, true)}><Icon name="download" size={13} /> PDB rifinito</a>
            <ReportButton jobId={jobId} model={result.refined_file} />
          </>
        }>
        <div className="table-wrap">
          <table className="table">
            <thead><tr><th>Metrica</th><th className="num">Prima</th><th className="num">Dopo</th><th className="num">Differenza</th><th>Esito</th></tr></thead>
            <tbody>
              {result.comparison.map((row) => (
                <tr key={row.key}>
                  <td>{row.label}<div className="tiny text-2">{row.higher_is_better ? "più alto è meglio" : "più basso è meglio"}</div></td>
                  <td className="num">{fmtRefineValue(row, row.before)}</td>
                  <td className="num">{fmtRefineValue(row, row.after)}</td>
                  <td className="num">{fmtDelta(row)}</td>
                  <td>{row.verdict ? <Badge tone={verdictTone(row.verdict)}>{row.verdict}</Badge> : <span className="tiny text-2">non disponibile</span>}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Card>
      <Disclosure title="Termini dell'energia (kJ/mol)" extra={md ? "minimizzazione, dinamica, minimizzazione" : "minimizzazione"}>
        <div className="stack-sm">
          <EnergyTerms components={e.components} />
          <span className="small text-2">Valgono per confrontare prima e dopo sulla stessa struttura, non strutture diverse; il termine delle restrizioni è escluso.</span>
        </div>
      </Disclosure>
      <Card icon="cube" title={`Struttura rifinita · ${result.refined_file}`}>
        <Viewer3D url={api.jobFileUrl(jobId, result.refined_file)} height={380} />
      </Card>
      <Disclosure title="Protocollo, preparazione e fonti">
        <ul className="tools-list">
          <li><b>Preparazione</b>: {input.residues} residui{input.excluded.length ? `; esclusi ${input.excluded.join(", ")}` : ""}{input.modified.length ? `; residui modificati ${input.modified.join(", ")}` : ""}{input.breaks.length ? `; interruzioni della catena ${input.breaks.map((b) => `${b.chain} ${b.after}–${b.before}`).join(", ")}` : ""}{input.hydrogens_removed ? `; ${input.hydrogens_removed} idrogeni del file tolti` : ""}.</li>
          <li><b>PDBFixer</b> (Eastman et al. 2017): {result.added.heavy_atoms} atomi pesanti e {result.added.terminal_atoms} terminali aggiunti, {result.added.hydrogens} idrogeni a pH {fmtNum(result.protocol.ph, 0)}{result.added.nonstandard.length ? `; sostituiti ${result.added.nonstandard.join(", ")}` : ""}.</li>
          <li><b>OpenMM {result.openmm.openmm}</b> (Eastman et al. 2017; piattaforma {result.openmm.platform}, {result.openmm.threads} thread): {result.protocol.forcefield} (Maier et al. 2015), {result.protocol.solvent} (Onufriev, Bashford & Case 2004); interazioni non legate {result.openmm.nonbonded}; {result.openmm.atoms} atomi.</li>
          <li><b>Restrizioni</b>: {result.protocol.restraint.form} su {result.openmm.restrained_atoms} atomi ({result.protocol.restraint.atoms.join(", ")}), k = {fmtNum(result.protocol.restraint.k_kcal_mol_A2, 0)} kcal·mol⁻¹·Å⁻²; energia residua delle restrizioni {fmtNum(e.restraint_final, 2)} kJ/mol.</li>
          <li><b>Minimizzazione</b> L-BFGS fino a {fmtNum(result.protocol.minimization.tolerance_kj_mol_nm, 0)} kJ·mol⁻¹·nm⁻¹ (al massimo {result.protocol.minimization.max_iterations} iterazioni){md ? `; dinamica di Langevin di ${fmtNum(md.ps, 1)} ps a ${md.temperature_k} K (passo ${md.timestep_fs} fs, attrito ${md.friction_ps} ps⁻¹, seme ${md.seed}) e seconda minimizzazione` : "; nessuna dinamica"}.</li>
          <li>Tempi: {Object.entries(result.openmm.timings).map(([k, v]) => `${k} ${fmtNum(v, 1)} s`).join(", ")}.</li>
        </ul>
      </Disclosure>
    </div>
  );
}
