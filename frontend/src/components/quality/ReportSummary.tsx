import type { ConsensusResult, QualityReport, ResidueRow } from "../../api/types";
import { fmtNum } from "../../lib/format";
import {
  BAND_TONE, buriedCount, clashTone, dsspBreakdown, fmtPenalty, gFactorTone, GRADE_RULE, gradeSteps, outlierTone, prosaTone, zdopeTone,
} from "../../lib/qualityView";
import { isPlddt, type ResidueRef } from "../../lib/residueColoring";
import { StackBar } from "../charts";
import { Badge, Card, DetailGrid, Disclosure, Stat } from "../ui";
import { ProsaProfile } from "./ProsaProfile";

const PLDDT_TITLE = "Riconosciuto come nella libreria dei template: intestazione di AlphaFold DB (TITLE «ALPHAFOLD … PREDICTION» senza EXPDTA, "
  + "oppure ModelCIF senza _exptl.method) o di ESMFold (TITLE «ESMFOLD … PREDICTION» senza EXPDTA), o nome AF-<accessione>-F<n> senza un "
  + "altro metodo dichiarato, e valori tra 0 e 100";

/** Metadati della struttura come chip leggibili: residui, catene, atomi, metodo, risoluzione, pLDDT, consenso. */
export function StructureMeta({ report, consensus }: { report: QualityReport; consensus: ConsensusResult | null }) {
  const s = report.structure;
  const plddt = isPlddt(report);
  return (
    <div className="quality-meta">
      <span className="chip"><b>{s.residues}</b> residui</span>
      <span className="chip"><b>{s.chains}</b> {s.chains === 1 ? "catena" : "catene"}</span>
      <span className="chip"><b>{s.heavy_atoms}</b> atomi pesanti</span>
      {s.method && <span className="chip">{s.method}</span>}
      {s.resolution && <span className="chip"><b>{fmtNum(s.resolution, 2)}</b> Å</span>}
      {plddt && <span className="chip">pLDDT medio <b>{fmtNum(report.bfactor.mean, 1)}</b></span>}
      {consensus?.score != null && (
        <Badge tone={consensus.band ? BAND_TONE[consensus.band] : undefined} title={`${consensus.methods_used} metodi su ${consensus.methods_total}`}>
          consenso {fmtNum(consensus.score, 0)}/100{consensus.band ? ` · ${consensus.band}` : ""}
        </Badge>
      )}
      {plddt && (
        <Badge tone="info" title={PLDDT_TITLE}>
          {s.predictor === "esmfold" ? "Predizione ESMFold" : "Modello AlphaFold"}: la colonna B-factor contiene il pLDDT
        </Badge>
      )}
    </div>
  );
}

/** "Come è calcolato il voto": punteggio di partenza, una penalità per riga con la sua regola, punteggio e voto. */
export function GradeBreakdown({ report }: { report: QualityReport }) {
  const g = report.grade;
  return (
    <Disclosure title="Come è calcolato il voto" extra={`${fmtNum(g.score, 1)}/100 · voto ${g.grade}`}>
      <div className="table-wrap">
        <table className="table quality-grade-table">
          <thead><tr><th>Voce</th><th>Regola</th><th className="num">Punti</th></tr></thead>
          <tbody>
            <tr><td>Punteggio di partenza</td><td className="text-2">—</td><td className="num">100</td></tr>
            {gradeSteps(report).map((s) => (
              <tr key={s.label}>
                <td>Penalità {s.label}</td>
                <td className="small text-2">{s.rule}</td>
                <td className={`num nowrap${s.points ? " quality-minus" : ""}`}>{fmtPenalty(s.points)}</td>
              </tr>
            ))}
            <tr className="quality-total">
              <td><b>Punteggio</b></td>
              <td className="small text-2">voto {g.grade}: {GRADE_RULE}</td>
              <td className="num"><b>{fmtNum(g.score, 1)}</b></td>
            </tr>
          </tbody>
        </table>
      </div>
    </Disclosure>
  );
}

/** I quattro valori riassuntivi con l'esito e, sotto "Dettagli", spiegazione, soglie e valori grezzi. */
export function SummaryStats({ report }: { report: QualityReport }) {
  const rs = report.ramachandran.summary;
  const cl = report.clashes;
  const dope = report.dope;
  const buried = buriedCount(report.residues);
  const pen = report.grade.penalties;
  return (
    <div className="grid grid-4">
      <Stat
        label="Ramachandran favorite"
        value={<>{fmtNum(rs.favored_pct)}<small>%</small></>}
        note={`${rs.outlier} outlier (${fmtNum(rs.outlier_pct)}%) · ${rs.allowed} consentite`}
        tone={rs.total ? outlierTone(rs.outlier_pct) : undefined}
        detail={(
          <>
            <span>Residui con angoli φ/ψ nelle regioni favorite, consentite o non consentite (outlier), per classe di residuo.</span>
            <DetailGrid items={[
              { label: "Favorite", value: `${rs.favored} (${fmtNum(rs.favored_pct)}%)` },
              { label: "Consentite", value: `${rs.allowed} (${fmtNum(rs.allowed_pct)}%)` },
              { label: "Outlier", value: `${rs.outlier} (${fmtNum(rs.outlier_pct)}%)` },
              { label: "Cis non prolinici", value: rs.cis_non_proline },
              { label: "Residui valutati", value: rs.total },
              { label: "Penalità nel voto", value: fmtPenalty(pen.ramachandran) },
            ]} />
            <span>Soglie: nessuna penalità fino allo 0,5% di outlier, oltre il 2% problematico; obiettivo MolProbity per strutture ad alta risoluzione: favorite oltre il 98%.</span>
          </>
        )}
      />
      <Stat
        label="Clashscore"
        value={fmtNum(cl.clashscore, 2)}
        note={`${cl.count} contatti gravi (atomi pesanti)`}
        tone={clashTone(cl.clashscore)}
        detail={(
          <>
            <span>Contatti gravi (sovrapposizione di van der Waals oltre 0,4 Å) ogni 1000 atomi pesanti, senza idrogeni: confrontabile con MolProbity solo come ordine di grandezza.</span>
            <DetailGrid items={[
              { label: "Contatti gravi", value: cl.count },
              { label: "Atomi pesanti", value: cl.atoms },
              { label: "Calcolo", value: `1000 × ${cl.count} / ${cl.atoms}` },
              { label: "Penalità nel voto", value: `${fmtPenalty(pen.clashes)} (clashscore / 1,5)` },
            ]} />
            <span>Soglie: fino a 5 buono, oltre 20 problematico.</span>
          </>
        )}
      />
      <Stat
        label="DOPE"
        value={dope?.score ? fmtNum(dope.score, 0) : "—"}
        note={dope?.zscore != null ? `zDOPE ${fmtNum(dope.zscore, 2)} (${dope.zscore <= -1 ? "simile a strutture native" : "da verificare"})` : "richiede MODELLER"}
        tone={zdopeTone(dope?.zscore)}
        detail={(
          <>
            <span>Discrete Optimized Protein Energy (Shen &amp; Sali 2006): energia statistica di tutti gli atomi. Lo score dipende dalla lunghezza; lo zDOPE lo normalizza.</span>
            <DetailGrid items={[
              { label: "Score DOPE", value: fmtNum(dope?.score ?? null, 1) },
              { label: "zDOPE", value: fmtNum(dope?.zscore ?? null, 2) },
              { label: "Profilo", value: dope?.profile.length ? `${dope.profile.length} residui` : "—" },
              { label: "Penalità nel voto", value: dope?.zscore != null ? `${fmtPenalty(pen.dope)} (10 punti per unità sopra −1)` : "—" },
            ]} />
            <span>Soglie: zDOPE sotto −1 simile a strutture native, sopra 0 probabilmente scarso.</span>
          </>
        )}
      />
      <Stat
        label="Superficie"
        value={report.sasa.mean_rsa != null ? <>{fmtNum(report.sasa.mean_rsa * 100, 0)}<small>% RSA media</small></> : "—"}
        note={report.sasa.buried_pct != null ? `${fmtNum(report.sasa.buried_pct)}% residui sepolti` : undefined}
        tone="info"
        detail={(
          <>
            <span>Accessibilità relativa al solvente (RSA): superficie accessibile del residuo divisa per quella massima del suo tipo. Sepolto: RSA sotto 0,2.</span>
            <DetailGrid items={[
              { label: "RSA media", value: fmtNum(report.sasa.mean_rsa, 3) },
              { label: "Residui sepolti", value: `${buried.buried} su ${buried.measured}` },
              { label: "Sepolti (%)", value: report.sasa.buried_pct != null ? `${fmtNum(report.sasa.buried_pct)}%` : "—" },
              { label: "Metodo", value: report.sasa.method || "—" },
            ]} />
            <span>Informativo: non entra nel voto.</span>
          </>
        )}
      />
    </div>
  );
}

/** PROCHECK e ProSA II, se calcolati: composizione delle regioni, G-factor, z-score e profilo di energia. */
export function ExternalValidation({ report, selected, onSelect }: { report: QualityReport; selected: ResidueRef | null; onSelect: (r: ResidueRow) => void }) {
  const pc = report.procheck;
  const pr = report.prosa;
  if (!pc && !pr) return null;
  return (
    <div className="grid grid-2 quality-external">
      {pc && (
        <Card title="PROCHECK" icon="grid" sub="Laskowski et al. 1993" collapsible storageKey="quality.procheck">
          <div className="stack">
            <StackBar
              title="Ramachandran secondo PROCHECK"
              subtitle="Residui nelle regioni core, consentite, generose e non consentite (%) · una buona struttura ne ha oltre il 90% nelle core"
              filename="procheck_ramachandran"
              parts={[
                { label: "Core", value: pc.ramachandran.core ?? 0, color: "var(--ok)" },
                { label: "Consentite", value: pc.ramachandran.allowed ?? 0, color: "var(--accent)" },
                { label: "Generose", value: pc.ramachandran.generous ?? 0, color: "var(--warn)" },
                { label: "Non consentite", value: pc.ramachandran.disallowed ?? 0, color: "var(--err)" },
              ]}
            />
            <div className="grid grid-3">
              <Stat label="G-factor globale" value={fmtNum(pc.g_factors.overall, 2)} note="> −0,5 normale; < −1 insolito" tone={gFactorTone(pc.g_factors.overall)}
                detail={<span>Log-odds dei parametri stereochimici rispetto a strutture ad alta risoluzione: valori bassi indicano geometria insolita.</span>} />
              <Stat label="Diedri / covalenti" value={<span style={{ fontSize: 16 }}>{fmtNum(pc.g_factors.dihedrals, 2)} / {fmtNum(pc.g_factors.covalent, 2)}</span>}
                note="G-factor per tipo" />
              <Stat label="Contatti ravvicinati" value={pc.bad_contacts ?? "—"} note={pc.mc_bond_angles_ok != null ? `angoli di legame nei limiti: ${fmtNum(pc.mc_bond_angles_ok)}%` : undefined} />
            </div>
            <Disclosure title="Altri parametri PROCHECK">
              <div className="stack-sm">
                <DetailGrid items={[
                  { label: "Residui", value: pc.residues ?? "—" },
                  { label: "Lunghezze di legame nei limiti", value: pc.mc_bond_lengths_ok != null ? `${fmtNum(pc.mc_bond_lengths_ok)}%` : "—" },
                  { label: "Angoli di legame nei limiti", value: pc.mc_bond_angles_ok != null ? `${fmtNum(pc.mc_bond_angles_ok)}%` : "—" },
                  { label: "Gruppi planari nei limiti", value: pc.planar_groups_ok != null ? `${fmtNum(pc.planar_groups_ok)}%` : "—" },
                  { label: "Deviazione massima (Max.deviation)", value: fmtNum(pc.max_deviation, 1) },
                  { label: "Bond len/angle", value: fmtNum(pc.bond_len_angle, 1) },
                ]} />
                <p className="small text-2">I grafici PostScript completi sono tra i file del job{pc.files.length ? ` (${pc.files.length})` : ""}.</p>
              </div>
            </Disclosure>
            {pc.summary && (
              <Disclosure title="Riepilogo testuale di PROCHECK">
                <pre className="log">{pc.summary}</pre>
              </Disclosure>
            )}
          </div>
        </Card>
      )}
      {pr && (
        <Card title="ProSA II" icon="activity" sub="energia del ripiegamento (Sippl)" collapsible storageKey="quality.prosa">
          <div className="stack">
            <div className="grid grid-3">
              <Stat label="Z-score" value={fmtNum(pr.zscore, 2)} tone={prosaTone(pr.zscore)}
                note="valori positivi indicano problemi"
                detail={<span>Da confrontare con le strutture sperimentali di pari lunghezza: per catene corte l'intervallo nativo è meno negativo. Fasce del consenso: −6 o meno buona, sopra −2 scarsa.</span>} />
              <Stat label="Z coppie" value={fmtNum(pr.zscore_pair, 2)} note="potenziale di coppia" />
              <Stat label="Z superficie" value={fmtNum(pr.zscore_surface, 2)} note="potenziale di superficie" />
            </div>
            <ProsaProfile profile={pr.profile} residues={report.residues} selected={selected} onSelect={onSelect} />
          </div>
        </Card>
      )}
    </div>
  );
}

/** Composizione della struttura secondaria (3 stati) e, a scomparsa, il dettaglio degli 8 stati DSSP. */
export function SecondaryStructureCard({ report }: { report: QualityReport }) {
  const ss = report.secondary_structure;
  const rows = dsspBreakdown(ss.detail ?? {});
  return (
    <Card title="Struttura secondaria" icon="dna" sub={ss.method} collapsible storageKey="quality.secondary">
      <div className="stack">
        {/* titolo e metodo sono già nell'intestazione della sezione: il titolo del grafico resta per il nome del gruppo,
            la didascalia del PNG e la vista espansa */}
        <StackBar
          title="Composizione della struttura secondaria"
          titleHidden
          subtitle="percentuale dei residui, 8 stati DSSP raggruppati in 3"
          filename="struttura_secondaria"
          parts={[
            { label: "Elica", value: ss.helix_pct, color: "var(--helix)" },
            { label: "Filamento β", value: ss.strand_pct, color: "var(--strand)" },
            { label: "Coil", value: ss.coil_pct, color: "var(--coil)" },
          ]}
        />
        {rows.length > 0 && (
          <Disclosure title="Dettaglio degli stati DSSP" extra={`${rows.length} stati`}>
            <div className="table-wrap">
              <table className="table">
                <thead><tr><th>Stato</th><th>Descrizione</th><th className="num">Residui</th><th className="num">%</th></tr></thead>
                <tbody>
                  {rows.map((r) => (
                    <tr key={r.code}><td className="mono">{r.code}</td><td>{r.label}</td><td className="num">{r.count}</td><td className="num">{fmtNum(r.pct)}</td></tr>
                  ))}
                </tbody>
              </table>
            </div>
          </Disclosure>
        )}
      </div>
    </Card>
  );
}
