# Piano di implementazione — evoluzione di BioLoom v4

Stato: **in corso** — avanzamento nella sezione [Avanzamento](#avanzamento) in fondo. Deriva dall'analisi di `services/pipeline.py`, `core/jobs.py`,
`engines/*`, `domain/*` e del frontend React. Le stime di effort sono relative (S ≈ 1–2 giorni, M ≈ 3–5, L ≈ 1–2
settimane, XL > 2 settimane) e non sono state misurate.

## Principi

- **Una fase = un branch `feat/...` = una PR**, con test e aggiornamento di `docs/ARCHITETTURA.md`.
- **Nessuna regressione scientifica:** ogni nuova metrica passa da `domain/` (logica pura, testata su 1UBQ) prima di
  arrivare a un motore o a una rotta.
- **Funzionamento senza strumenti opzionali:** ogni funzione che richiede uno strumento esterno (OpenMM, fpocket,
  APBS, FoldX…) si rileva in `engines/tools.py` e degrada con un messaggio chiaro, come già avviene per ProSA/QMEAN.
- **Docker resta l'unico ambiente:** nuovi strumenti nell'immagine `bioloom` solo se piccoli; quelli pesanti in
  profili compose separati (come `qmean`).
- **Nessun dato inviato a servizi remoti senza opt-in** (regola già in vigore per SWISS-MODEL).

## Dipendenze tra le fasi

```
F0 Fondamenta (CI, test)
 ├─ F1 Efficienza del calcolo ──┬─ F2 Job, coda e tempo reale
 │                              └─ F5 Nuovi motori di modellazione (F5 usa la cache di F1)
 ├─ F3 Viewer e qualità per residuo ─┬─ F4 Confronto e report
 │                                   └─ F7 Analisi strutturale (usa il viewer)
 ├─ F5 Template e modellazione avanzata
 ├─ F6 Qualità con consenso (usa F3)
 └─ F8 Esperienza guidata (wizard, avvisi, ricerca) ─ dopo F1–F6
F9 Rifinitura, mutagenesi, riproducibilità ─ dopo F5–F7
```

F1, F2 e F3 sono indipendenti e possono procedere in parallelo. F8 è l'ultima perché integra le altre.

---

## F0 — Fondamenta (S)

**Obiettivo:** poter cambiare il codice con fiducia.

| Attività | Dettaglio |
|---|---|
| CI GitHub Actions | `pytest` (backend), `tsc --noEmit` + `vitest` (frontend), build Docker senza push |
| Lint | `ruff` (backend, regole E4/E7/E9/F/B in `pyproject.toml`); nel frontend il controllo resta `tsc --noEmit` (ESLint non è installato) |
| Dati di test | Aggiungere 1–2 strutture piccole oltre a 1UBQ (una multi-catena, una con ligando) in `backend/tests/data/` |
| Esecuzione | `scripts/test.sh`: pytest nell'immagine di BioLoom (MODELLER reale) e test del frontend; i test di MODELLER si saltano da soli senza MODELLER (`requires_modeller`, già presente) |

**Accettazione:** CI verde su `main`; un PR che rompe un test viene bloccato.

## F1 — Efficienza del calcolo (M)

**1.1 Cache di ricerca e allineamento**
- Nuovo `core/cache.py`: chiave = SHA-256 di (operazione, sequenza normalizzata, motore, parametri, versione del
  database). Valore su file in `data/cache/compute/` con scrittura atomica (`core/storage`).
- `engines/search.search()` e `engines/align.*` consultano la cache prima di eseguire; parametro
  `use_cache=True` con opzione "ignora cache" nell'interfaccia.
- Invalidazione: timbro del database (già presente per MMseqs2), scadenza in `housekeeping` (come la cache remota).
- **Test:** stessa ricerca due volte → secondo accesso senza sottoprocesso; cambio di parametro → mancato hit.

**1.2 Raggruppamento per template in pipeline**
- In `PipelineService._run_item`, dopo la ricerca, raggruppa gli elementi per `(template, catena)`.
- `engines/modeller.prepare_template` eseguito una volta per gruppo e riusato (copia nello scratch dell'elemento).
- Un unico script MODELLER può modellare più target con lo stesso template, riducendo gli avvii dell'interprete.
- **Test:** pipeline con 4 sequenze e 1 template → una sola preparazione (verificata dal log).

**1.3 Validazione in parallelo**
- In `engines/quality.py`, DOPE, PROCHECK, ProSA e clash/Ramachandran girano su un pool interno; QMEAN resta separato per
  la memoria.
- **Test:** stessi risultati numerici della versione seriale; tempo ridotto sul modello di prova.

**1.4 Limiti di concorrenza coerenti**
- `parallel` della pipeline oggi è ≤ 8 indipendente dal numero di job attivi. Introdurre un budget di CPU condiviso tra
  job (semaforo in `JobManager`) per evitare la saturazione.

**Accettazione fase:** misura prima/dopo su una pipeline di 20 sequenze, documentata in `ARCHITETTURA.md`.

## F2 — Job, coda e tempo reale (M)

**2.1 Ripresa delle pipeline**
- Nuova rotta `POST /api/jobs/{id}/resume`: consentita per job `failed`/`cancelled` di tipo `pipeline`; riporta a
  `pending` gli elementi `failed|running|cancelled` (con opzione "solo i falliti") e rimette in coda.
- Il riavvio non marca più come fallita una pipeline con elementi salvati: la marca `interrupted` e la rende
  riprendibile. Aggiornare `TERMINAL/ACTIVE` e il frontend (`status.ts`).

**2.2 Priorità e prenotazione risorse**
- Colonna `priority` in `jobs`; i job interattivi (`search`, `align`, `quality`) prima delle pipeline.
- Ogni tipo di job dichiara una stima di RAM/core; QMEAN prenota ~12 GB e non parte se non disponibili (messaggio
  esplicito invece del fallimento a metà).

**2.3 ETA**
- Mediana della durata degli elementi completati → tempo residuo in `progress` (campo `eta_seconds`); mostrato in
  `JobPanel` e `Pipeline.tsx`.

**2.4 Aggiornamenti in tempo reale (SSE)**
- Nuova rotta `GET /api/jobs/{id}/events` (Server-Sent Events): stato, progresso, righe di log.
- `useJob` passa a `EventSource` con ripiego sul polling se la connessione cade.
- Rotta analoga per l'elenco dei job (`/api/jobs/events`).

**Test:** ripresa dopo terminazione forzata; ordine di priorità; SSE con client simulato.

## F3 — Viewer e qualità per residuo (M)

**3.1 Metriche per residuo nel backend**
- Nuovo `domain/perresidue.py`: per ogni residuo Ramachandran (favorevole/consentito/outlier), numero di clash,
  DOPE per residuo (`engines/modeller` lo può emettere con `assess_dope` per residuo), B-factor/pLDDT.
- Endpoint `GET /api/jobs/{id}/files/{file}/residues` con un array compatto per catena.

**3.2 Colorazione nel viewer**
- `Viewer3D.tsx`: nuovi schemi `rama`, `clash`, `dope`, `plddt`; legenda con scala; clic sul grafico di qualità
  evidenzia il residuo nel 3D (e viceversa).
- Estendere il tipo `Scheme` e `applyStyle`; mantenere il rilascio del contesto WebGL.

**3.3 Confidenza sull'allineamento**
- `AlignmentView` e `PirEditor`: sfondo per identità locale, gap, regioni in zona crepuscolare (soglie già in
  `pipeline` → riuso).

**Test:** unità su `perresidue` con 1UBQ (risultati noti); `vitest` per la mappatura colori.

## F4 — Confronto e report (M)

**4.1 Confronto affiancato / sovrapposto**
- Nuova pagina `Compare.tsx`: due modelli (o modello vs template vs predetto), sovrapposizione con Kabsch
  (`domain/geometry`), RMSD, TM-score, RMSD per residuo, differenze evidenziate.
- Rotta `POST /api/compute/compare` come job `compare` (leggero, pool `cpu`), risultato con matrice di
  trasformazione e tabella per residuo.

**4.2 Report del modello**
- `domain/report.py` genera HTML autonomo (immagini del viewer come PNG lato client inviate al server, oppure
  grafici SVG generati dal backend): sequenza, template, allineamento, parametri, metriche, grade e legenda.
- Esportazione PDF con `weasyprint` o stampa da browser (decisione: partire dall'HTML, il PDF è opzionale).
- Il pulsante "Report" compare in `Modeling` e `Quality`; il report entra anche nel ZIP di `export_zip`.

**Test:** report generato su un modello di esempio, con controllo dei campi obbligatori.

## F5 — Template e modellazione avanzata (L–XL)

Suddivisa in sotto-PR indipendenti, dalla più semplice.

**5.1 AlphaFold DB come sorgente di template (M)**
- Client in `engines/remote.py` per `alphafold.ebi.ac.uk` (per ID UniProt): download di PDB/mmCIF e pLDDT (B-factor).
- Nuovo motore in `engines/search.ENGINES` che, per una sequenza, trova l'accessione UniProt (via UniProt/BLAST) e
  restituisce il modello AF come "template candidato" con etichetta di sorgente e pLDDT medio.
- Ritaglio delle regioni con pLDDT basso prima di usarlo come template (opzione, soglia 70).
- Avviso esplicito: è un modello predetto, non una struttura sperimentale.
- **Opt-in remoto** come per gli altri servizi.

**5.2 Selezione ragionata dei template (M)**
- `domain/templates_rank.py`: punteggio composito con identità, copertura, risoluzione, R-free, ligandi, gap, stato
  (apo/olo). Metadati già raccolti in `templates/*.meta.json`.
- Clusterizzazione per similarità strutturale (TM-align) per evitare template ridondanti.
- Nuova modalità pipeline `template_mode: "multi"` che sceglie 2–3 template complementari; l'interfaccia mostra la
  motivazione della scelta.

**5.3 Complessi e omo-oligomeri (L)**
- Lettura dell'assemblaggio biologico (mmCIF `pdbx_struct_assembly`) in `domain/structure`.
- Script MODELLER multi-catena con `automodel` a più catene, simmetria con restrizioni `symmetry`.
- Controllo delle interfacce nel modulo di qualità (contatti, area sepolta).
- UI: selezione delle catene del bersaglio e dei template per catena.

**5.4 Ligandi e cofattori (M)**
- Estendere l'opzione esistente `ligands` con: scelta dei singoli eteroatomi da trasferire, verifica dei contatti e
  dei clash, geometria di coordinazione dei metalli (distanze/angoli in `domain/`).

**5.5 Predizione di regioni disordinate e domini (S–M)**
- Domini da InterPro (client esistente), disordine da un predittore leggero (implementazione interna semplice o
  IUPred se la licenza lo consente). Le regioni disordinate compaiono sull'allineamento e nel report come "modello
  poco affidabile".

**Test:** per ogni sotto-fase, dati di 1–2 casi noti (proteina con omologo AF, omodimero, proteina con eme).

## F6 — Qualità con consenso (S–M)

- `domain/consensus.py`: combina grade BioLoom, DOPE, GA341, QMEAN, ProSA, PROCHECK in un punteggio 0–100 con pesi
  dichiarati (nel modulo, con test) e indica quali metodi discordano.
- `Quality.tsx`: grafico per residuo sovrapposto dei metodi disponibili; testo esplicativo generato dalla
  discordanza ("DOPE segnala il loop 40–52, Ramachandran no").
- Compatibilità: il campo `grade` esistente non cambia; `consensus` è un campo nuovo.

## F7 — Analisi strutturale (L)

Tutti come job `analysis` opzionali, con strumento rilevato o messaggio di indisponibilità.

| Analisi | Strumento | Note |
|---|---|---|
| SASA, interfacce, ponti salini, legami H | libreria interna (Biopython/`freesasa`) | Pura, senza dipendenze pesanti |
| Tasche e cavità | `fpocket` | Aggiunta binaria all'immagine, piccola |
| Elettrostatica | `pdb2pqr` + `APBS` | Profilo compose separato; visualizzazione come superficie colorata |
| Conservazione | MSA già calcolato (Clustal/MAFFT/MMseqs2) | Entropia di Shannon per colonna → colorazione del viewer (usa F3) |

## F8 — Esperienza guidata (M–L)

**8.1 Procedura "dalla sequenza al modello"**
- Nuova pagina `Wizard.tsx`: sequenza → template suggerito (F5.2) → conferma → modellazione → qualità → report.
  Ogni passo riusa i componenti esistenti; nessuna logica duplicata nel frontend.

**8.2 Avvisi preventivi**
- `GET /api/compute/preflight` (rotta e servizio): controlla licenza MODELLER, identità < 30 %, memoria per QMEAN,
  dimensione della sequenza, spazio su disco, database mancanti. Il frontend mostra gli avvisi prima del lancio.

**8.3 Ricerca globale, tag e preferiti**
- Tabella `tags` e ricerca su progetti/job/template/sequenze (SQLite FTS5). Barra di ricerca nel `Layout`.

**8.4 Rifiniture**
- Tema scuro (variabili CSS già in `styles.css`), scorciatoie da tastiera, virtualizzazione delle tabelle lunghe
  (modelli e viste di allineamento).

## F9 — Rifinitura, mutagenesi, riproducibilità (L)

**9.1 Rifinitura energetica**
- `engines/refine.py` con OpenMM (minimizzazione, opzionalmente MD breve con vincoli sullo scheletro); confronto
  prima/dopo con Ramachandran, clash e DOPE nell'interfaccia.

**9.2 Mutagenesi in silico**
- Sostituzioni puntuali e scansione di alanina con MODELLER; ΔΔG con uno strumento open (es. `pyrosetta`/`foldx` solo
  se la licenza lo consente, altrimenti punteggio statistico basato su DOPE). Importazione di varianti da
  ClinVar/UniProt per la pagina di una proteina.

**9.3 Confronto con predittori esterni**
- ESMFold (API pubblica) come controllo di plausibilità opzionale; TM-score contro il modello per omologia (F4.1).

**9.4 Riproducibilità**
- Esportazione guidata di `params.json` + script MODELLER completo + versione degli strumenti in un archivio; un
  comando `bioloom rerun <archivio>` in `bioloom.py`.

---

## Tabella riassuntiva

| Fase | Contenuto | Effort | Dipende da | Rischio |
|---|---|---|---|---|
| F0 | CI, lint, dati di test | S | — | Basso |
| F1 | Cache, raggruppamento template, validazione parallela | M | F0 | Medio (concorrenza) |
| F2 | Ripresa, priorità, ETA, SSE | M | F0 | Medio (stato dei job) |
| F3 | Metriche per residuo, viewer, confidenza allineamento | M | F0 | Basso |
| F4 | Confronto, report | M | F3 | Basso |
| F5 | AlphaFold DB, selezione template, complessi, ligandi, disordine | L–XL | F1, F3 | Alto (complessi) |
| F6 | Consenso di qualità | S–M | F3 | Basso |
| F7 | Analisi strutturale | L | F3 | Medio (strumenti esterni) |
| F8 | Wizard, avvisi, ricerca, rifiniture | M–L | F1–F6 | Basso |
| F9 | Rifinitura, mutagenesi, riproducibilità | L | F5–F7 | Alto (licenze, tempi) |

## Rischi trasversali

- **Licenze:** MODELLER (chiave), ProSA (MAC), e possibili FoldX/Rosetta per il ΔΔG. Prima di F9.2 verificare cosa
  è ridistribuibile o richiede chiave dell'utente.
- **Dimensione dell'immagine Docker:** ogni strumento nuovo va misurato; APBS e OpenMM in profili separati.
- **Compatibilità dei dati:** modifiche a tabelle SQLite (`priority`, `tags`, `interrupted`) richiedono una
  migrazione in `core/db.py` che non tocchi i dati esistenti (backup automatico prima della migrazione).
- **Servizi remoti** (AlphaFold DB, ESMFold, ClinVar): cache, timeout e opt-in; funzionamento offline garantito.

## Ordine di consegna suggerito

1. **F0** → 2. **F1.1 + F2.1** (cache e ripresa) → 3. **F3** (viewer per residuo) → 4. **F4.1** (confronto) →
5. **F5.1** (AlphaFold DB) → 6. **F6** → 7. **F1.2–1.4 + F2.2–2.4** → 8. **F4.2** (report) → 9. **F5.2–5.5** →
10. **F7** → 11. **F8** → 12. **F9**.

Le prime cinque tappe danno il beneficio più visibile con il rischio più basso.

## Avanzamento

| Fase | Stato | Note |
|---|---|---|
| F0 | fatto | CI (`.github/workflows/ci.yml`), `scripts/test.sh` + `docker/test/Dockerfile`, ruff configurato, strutture sintetiche `ubq_dimer.pdb` e `ubq_zn.pdb` |
