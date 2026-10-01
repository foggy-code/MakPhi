// Rifinitura energetica con OpenMM (F9.1): finestra di avvio (dalla tabella dei modelli o da una struttura qualunque)
// e risultati di un job `refine` nella pagina Modellazione, con il confronto prima/dopo, i link a Qualità e Confronto e il
// report HTML del modello rifinito (services/reports.py).
// La logica è in lib/refine.ts, il protocollo nel backend (domain/refinement.py).
import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api } from "../api/client";
import type { RefineResult, StructureSource, SystemInfo } from "../api/types";
import { useProject, useToast } from "../lib/context";
import { fmtNum } from "../lib/format";
import { useAsync } from "../lib/hooks";
import { comparisonSummary, energyText, fmtDelta, fmtRefineValue, MD_DEFAULT_PS, MD_MAX_PS, refineCompareLink, refinedQualityLink, refineRequest, verdictTone } from "../lib/refine";
import { missingTools, missingToolsText } from "../lib/toolJobs";
import { Icon } from "./Icon";
import { ReportButton } from "./ReportButton";
import { Viewer3D } from "./Viewer3D";
import { Alert, Badge, Card, Field, Grade, Modal, Stat } from "./ui";

const PROTOCOL_TEXT = "PDBFixer completa gli atomi pesanti e aggiunge gli idrogeni a pH 7; OpenMM minimizza l'energia con il campo di forza AMBER ff14SB e il solvente implicito OBC2, tenendo la catena principale (N, CA, C, O) vicina alle posizioni di partenza con restrizioni armoniche di 10 kcal/mol/Å². Ligandi, ioni e acque sono esclusi; nessun residuo mancante viene ricostruito.";

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
      footer={<><button className="btn" onClick={onClose}>Annulla</button><button className="btn btn-primary" disabled={!!missing || !req.body} onClick={submit}>Avvia</button></>}>
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
        {req.error && <span className="tiny" style={{ color: "var(--warn)" }}>{req.error}</span>}
      </div>
    </Modal>
  );
}

/** Risultati di un job `refine`: energia, spostamento della catena principale, confronto prima/dopo, 3D, link. */
export function RefineResults({ jobId, result }: { jobId: string; result: RefineResult }) {
  const counts = comparisonSummary(result.comparison);
  const after = result.quality.after;
  const before = result.quality.before;
  const md = result.protocol.md;
  return (
    <div className="stack">
      {result.warnings.map((w, i) => <Alert key={i} tone="warn">{w}</Alert>)}
      <div className="grid grid-4">
        <Stat label="Voto dopo la rifinitura" value={<span className="row"><Grade grade={after.grade} large /> <span>{fmtNum(after.grade_score, 1)}<small>/100</small></span></span>} note={`prima ${before.grade} (${fmtNum(before.grade_score, 1)})`} />
        <Stat label="Energia (campo di forza)" value={<span style={{ fontSize: 16 }}>{energyText(result.energy)}</span>} note={result.energy.decreased ? "diminuita (senza il termine delle restrizioni)" : "non diminuita"} />
        <Stat label="RMSD catena principale" value={<>{fmtNum(result.rmsd.rmsd, 2)}<small> Å</small></>} note={`${result.rmsd.atoms} atomi N, CA, C, O; spostamento massimo ${fmtNum(result.rmsd.max_displacement, 2)} Å (${result.rmsd.max_atom ?? "—"})`} />
        <Stat label="Clashscore" value={<>{fmtNum(before.clashscore, 2)} → {fmtNum(after.clashscore, 2)}</>} note={`${counts.better} metriche migliori, ${counts.worse} peggiori, ${counts.same} invariate`} />
      </div>
      <Card title="Confronto prima/dopo" sub="prima = copia pulita data a OpenMM (stessi residui, senza ligandi né idrogeni)"
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
                  <td>{row.label}<div className="tiny muted">{row.higher_is_better ? "più alto è meglio" : "più basso è meglio"}</div></td>
                  <td className="num">{fmtRefineValue(row, row.before)}</td>
                  <td className="num">{fmtRefineValue(row, row.after)}</td>
                  <td className="num">{fmtDelta(row)}</td>
                  <td>{row.verdict ? <Badge tone={verdictTone(row.verdict)}>{row.verdict}</Badge> : <span className="tiny muted">non disponibile</span>}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Card>
      <Card title={`Struttura rifinita · ${result.refined_file}`}>
        <Viewer3D url={api.jobFileUrl(jobId, result.refined_file)} height={380} />
      </Card>
      <details className="small text-2">
        <summary>Protocollo, energie e fonti</summary>
        <ul style={{ margin: "6px 0 0", paddingLeft: 18 }}>
          <li><b>PDBFixer</b> (Eastman et al. 2017): {result.added.heavy_atoms} atomi pesanti e {result.added.terminal_atoms} terminali aggiunti, {result.added.hydrogens} idrogeni a pH {fmtNum(result.protocol.ph, 0)}{result.added.nonstandard.length ? `; sostituiti ${result.added.nonstandard.join(", ")}` : ""}.</li>
          <li><b>OpenMM {result.openmm.openmm}</b> (Eastman et al. 2017; piattaforma {result.openmm.platform}, {result.openmm.threads} thread): {result.protocol.forcefield} (Maier et al. 2015), {result.protocol.solvent} (Onufriev, Bashford & Case 2004); interazioni non legate {result.openmm.nonbonded}; {result.openmm.atoms} atomi.</li>
          <li><b>Restrizioni</b>: {result.protocol.restraint.form} su {result.openmm.restrained_atoms} atomi ({result.protocol.restraint.atoms.join(", ")}), k = {fmtNum(result.protocol.restraint.k_kcal_mol_A2, 0)} kcal·mol⁻¹·Å⁻²; energia residua delle restrizioni {fmtNum(result.energy.restraint_final, 2)} kJ/mol.</li>
          <li><b>Minimizzazione</b> L-BFGS fino a {fmtNum(result.protocol.minimization.tolerance_kj_mol_nm, 0)} kJ·mol⁻¹·nm⁻¹ (al massimo {result.protocol.minimization.max_iterations} iterazioni){md ? `; dinamica di Langevin di ${fmtNum(md.ps, 1)} ps a ${md.temperature_k} K (passo ${md.timestep_fs} fs, attrito ${md.friction_ps} ps⁻¹, seme ${md.seed}) e seconda minimizzazione` : "; nessuna dinamica"}.</li>
          <li><b>Energie (kJ/mol)</b>: prima {fmtNum(result.energy.components.before.total, 1)} (legami {fmtNum(result.energy.components.before.bonds, 0)}, angoli {fmtNum(result.energy.components.before.angles, 0)}, torsioni {fmtNum(result.energy.components.before.torsions, 0)}, non legate {fmtNum(result.energy.components.before.nonbonded, 0)}, solvatazione {fmtNum(result.energy.components.before.solvation, 0)}); dopo {fmtNum(result.energy.components.final.total, 1)}. Valgono per confrontare prima e dopo sulla stessa struttura, non strutture diverse.</li>
          <li>Tempi: {Object.entries(result.openmm.timings).map(([k, v]) => `${k} ${fmtNum(v, 1)} s`).join(", ")}.</li>
        </ul>
      </details>
    </div>
  );
}
