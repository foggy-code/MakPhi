# Piano di implementazione — evoluzione di BioLoom v4

Stato: **completato** — tutte le fasi F0–F9 sono realizzate. Lo stato finale di ognuna, con le differenze rispetto al
piano e i motivi, è nella sezione [Avanzamento](#avanzamento); i limiti noti che restano sono in [Seguiti
possibili](#seguiti-possibili). Le sezioni F0–F9 qui sotto sono il piano originale, lasciato com'era: dove il codice ne
differisce vale l'Avanzamento, e il funzionamento attuale è descritto in [ARCHITETTURA.md](ARCHITETTURA.md).

Il piano deriva dall'analisi di `services/pipeline.py`, `core/jobs.py`, `engines/*`, `domain/*` e del frontend React. Le
stime di effort sono relative (S ≈ 1–2 giorni, M ≈ 3–5, L ≈ 1–2 settimane, XL > 2 settimane) e non sono state misurate.

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

Tutte le fasi sono **fatte**. Ordine effettivo di consegna: F0 → F1.1 e F2.1 → F3 con F6, F5.1, F2.3 con F2.4 → F4.1,
F4.2, F1.2 con F1.3, F1.4 con F2.2 → F5.2, F5.4, F5.5, F7 → F8a (procedura guidata e controlli preventivi) → F5.3 → F8b
(ricerca globale, etichette e rifiniture) → F7b (tasche ed elettrostatica) con F9.1 → F9.2, F9.3, F9.4, poi una fase
finale di correzioni dalla prova end-to-end e dalla revisione di sicurezza e, dopo le fasi, la gestione dello spazio
(sotto la tabella). Le tappe sono state riviste a gruppi, con le correzioni in commit a parte. I dettagli di ogni punto
sono nelle sezioni di [ARCHITETTURA.md](ARCHITETTURA.md).

| Fase | Stato | Realizzato | Differenze dal piano e motivi |
|---|---|---|---|
| F0 | fatto | CI in `.github/workflows/ci.yml` con tre job (backend in Python puro, frontend con typecheck, vitest e build, immagine Docker costruita senza push con i test al suo interno); `scripts/test.sh` con `docker/test/Dockerfile`; ruff con le regole E4/E7/E9/F/B | Dati di test: invece di altre strutture del PDB, `tests/data/make_synthetic.py` genera da 1UBQ `ubq_dimer.pdb` (multi-catena) e `ubq_zn.pdb` (con un metallo), riproducibili e senza rete. Il blocco dei PR con un test rotto è un'impostazione del repository su GitHub, non del codice |
| F1.1 | fatto | `core/cache.py` per ricerche, allineamenti multipli e strutturali; chiave con timbro di database, strumenti e file; TTL 7 giorni per RCSB, NCBI e AlphaFold DB; `use_cache` nelle rotte («Ricalcola ignorando la cache») e interruttore globale `compute_cache` | Come da piano; il pairwise non passa dalla cache (è istantaneo) |
| F1.2 | fatto | Template preparati una volta per esecuzione della pipeline e copiati agli altri elementi (`services/compute.PreparedTemplates`) | Nessuno script MODELLER con più bersagli: isolerebbe peggio errori e log di ogni elemento e cambierebbe lo stato del generatore casuale fra i modelli; il guadagno massimo (avvio dei processi, 0,21–0,28 s su 9 s per elemento) non lo giustifica |
| F1.3 | fatto | Passi dell'analisi di qualità in parallelo (`core/parallel.Steps`), stesso rapporto della versione seriale; QMEAN resta un job a parte | Accettazione: le misure prima/dopo (ARCHITETTURA, Prestazioni) sono su job singoli, un elemento di pipeline e una pipeline di 4 sequenze con un template grande, a varianti alternate e confronti accoppiati nello stesso processo, invece che su una pipeline di 20 sequenze: il carico della macchina falsava i confronti fra esecuzioni separate. La costruzione dei modelli è l'86% del tempo e resta seriale (riproducibilità a parità di seme) |
| F1.4 | fatto | `core/budget.py`: slot di CPU condivisi dai processi di tutti i job, richieste elastiche per i programmi multi-thread, invecchiamento, nessun sorpasso | Il budget governa i processi, non i job: anche i passi paralleli di uno stesso job ne passano, senza un secondo limitatore |
| F2.1 | fatto | `POST /api/jobs/{id}/resume` con gli ambiti `incomplete` e `failed`, `JobManager.requeue` atomico, `PipelineService.resume`, anche da pipeline completate con elementi falliti; un elemento diventa `completed` solo dopo il salvataggio dei file | Nessuno stato `interrupted`: un riavvio lascia `failed` con il motivo e la ripresa vale per `failed`, `cancelled` e (solo pipeline) `completed`. Così stati, filtri, client e dati esistenti non cambiano e non serve una migrazione |
| F2.2 | fatto | `core/dispatch.py`: coda con priorità per pool (interattivi, poi modellazioni, poi lavori in blocco), invecchiamento, posizione in coda; QMEAN locale come risorsa esclusiva, con il controllo della memoria prima di partire e la risorsa restituita prima del ripiego su SWISS-MODEL | Nessuna colonna `priority`: la priorità si ricava dal tipo e lo schema non cambia. Nessuna stima di RAM per gli altri tipi di job: solo QMEAN ha un fabbisogno grande e misurato (12 GB con UniRef30_2020_06); i core li governa il budget di F1.4 |
| F2.3 | fatto | `core/eta.py`: mediana delle durate per le pipeline, dal progresso per gli altri job; `eta_seconds` in tutti i riepiloghi, mostrato in `JobPanel` e nell'elenco dei job | Come da piano; nessuna colonna nuova |
| F2.4 | fatto | `core/events.py` e `services/live.py`: SSE per job (`/api/jobs/{id}/events`) ed elenco (`/api/jobs/events`), ricaduta sul polling in `lib/live.ts` | Flusso dell'elenco condiviso fra le parti di una scheda e chiuso nelle schede nascoste, per il limite di 6 connessioni HTTP/1.1 del browser |
| F3.1 | fatto | `domain/perresidue.py` con pLDDT, QMEAN locale e ProSA per residuo | Ramachandran, clash e DOPE per residuo c'erano già nel rapporto di qualità: nessun endpoint `/residues`, i valori arrivano con il rapporto e con le rotte del consenso |
| F3.2 | fatto | `Viewer3D` con la colorazione per valore (struttura secondaria, Ramachandran, clash, DOPE, RSA, B-factor, pLDDT, QMEAN locale, consenso), legenda e selezione nei due sensi | Schemi in più rispetto al piano; la scala pLDDT usa i colori di AlphaFold DB |
| F3.3 | fatto | Modalità Confidenza di `AlignmentView` (Allineamenti, Modellazione, Editor PIR) | Come da piano |
| F4.1 | fatto | `domain/comparison.py` (distanze per coppia, GDT-TS e GDT-HA con ricerca per soglia, lDDT sui Cα), pagina `/compare`, link da Modellazione, Qualità e Allineamenti | Nessuna rotta né job `compare`: si estende il job `align` strutturale esistente (US-align, TM-align o integrato) con il confronto per residuo. La voce `compare` di `core/jobs.PRIORITY` è rimasta senza handler |
| F4.2 | fatto | `domain/report.py` e `services/reports.py`: HTML autonomo con grafici SVG del backend, vista 3D facoltativa come PNG verificato, report nello ZIP della pipeline | PDF dalla stampa del browser, senza `weasyprint` (nessuna dipendenza in più); report anche per rifinitura, mutagenesi e scansione di alanina |
| F5.1 | fatto | Libreria `AF-<ACC>-F<n>`, motore `alphafold`, ritaglio per pLDDT con la rimozione dei frammenti isolati, un solo riconoscitore dei modelli predetti (`domain/plddt.py`) che esclude le strutture sperimentali | L'accessione viene dall'utente o dall'intestazione UniProt, altrimenti da NCBI BLAST su Swiss-Prot (non da una ricerca su UniProt) |
| F5.2 | fatto | `domain/template_rank.py` (punteggio 0–100 con curva di Rost, copertura, E-value, risoluzione, R-free, metodo, lacune, data), selezione dei complementari con l'avviso di orientamento non vincolato, modalità pipeline `multi` (2–5 template); corretto l'allineamento multi-template (`salign` con template complementari) con il ripiego sugli `align2d` uniti | Nome del modulo `template_rank`. La ridondanza si valuta per identità di sequenza (> 95%) dagli allineamenti della ricerca, non con un clustering TM-align: richiederebbe di scaricare tutti i candidati. Ligandi e stato oligomerico sono informativi (peso 0) |
| F5.3 | fatto | Assemblaggi biologici generati dagli operatori del file (`domain/assembly.py`: mmCIF e REMARK 350), modalità Complesso con simmetria, confronto delle interfacce con il template (DockQ) | Un solo template (o assemblaggio) per complesso, con una sequenza del bersaglio per ogni sua catena; 2–12 catene e al più 3000 residui per il tempo di MODELLER. Gli assemblaggi si generano in locale invece di scaricarli da RCSB: funzionano senza rete e con i file caricati |
| F5.4 | fatto | Trasferimento selettivo dei gruppi eteroatomici, un solo gruppo per sito con più template, verifica di contatti, scontri e coordinazione con le distanze di Harding 2006 (verificate sulla fonte) | L'opzione `hetatm` precedente non trasferiva nulla (difetto corretto) |
| F5.5 | fatto | `domain/disorder.py` (FoldIndex, SEG), domini InterPro, avvisi di copertura prima del lancio, regioni `expected` nel consenso e nel report | FoldIndex invece di IUPred: IUPred dipende da file distribuiti con il programma per uso accademico, che non si possono ricostruire dalle pubblicazioni. Le regioni compaiono nella mappa della pagina Sequenze, negli avvisi, nel consenso e nel report, non sulla vista dell'allineamento |
| F6 | fatto | `domain/consensus.py`: scale e pesi dichiarati, discordanze spiegate, regioni problematiche, grafico per residuo; `grade` invariato | Dalla F9.3 il consenso accetta anche l'accordo con una predizione (peso 0,25), solo a confronto eseguito |
| F7 | fatto | Interazioni, interfacce e conservazione (job `analysis`, scheda Interazioni); tasche con fpocket e potenziale con PDB2PQR + APBS (job `pockets` ed `electrostatics`, schede Tasche ed Elettrostatica) | Calcoli interni con Biopython e numpy, senza `freesasa`. Tre tipi di job invece di un solo `analysis`. APBS nell'immagine principale invece che in un profilo compose: il binario ufficiale pesa 30 MB contro i circa 340 MB di dipendenze del pacchetto di Ubuntu. La conservazione usa un allineamento dei job di Allineamenti o uno incollato |
| F8.1 | fatto | Procedura guidata `/wizard` in sei passi (sequenza, template, allineamento, opzioni, avvio, risultati) con lo stato nell'URL e nel browser | Come da piano, con un passo in più per l'allineamento e le opzioni spiegate |
| F8.2 | fatto | Controlli preventivi `POST /api/compute/preflight` (logica in `domain/preflight.py`) in Modellazione, Pipeline, procedura guidata, mutagenesi, predizione e riesecuzione | `POST` invece di `GET`, perché il corpo è lo stesso della rotta di avvio; la soglia d'identità è la curva di Rost (con il 30% come informazione) invece di un 30% fisso; stima del tempo e dello spazio dalle misure |
| F8.3 | fatto | Tabelle `tags` e `favorites` (migrazione 2 con copia di sicurezza), ricerca globale su progetti, sequenze, documenti, job, template ed etichette (Ctrl+K) | L'indice FTS5 è temporaneo e si ricostruisce all'avvio, con il ripiego su LIKE: nulla di FTS5 nel file, così una build di SQLite senza FTS5 apre la stessa cartella dati |
| F8.4 | fatto | Tema Sistema, scorciatoie da tastiera, virtualizzazione delle liste lunghe (oltre 200 righe) | Il tema scuro esisteva già |
| F9.1 | fatto | Rifinitura con OpenMM e PDBFixer in un processo separato (`engines/refine_worker.py`), restrizioni sulla catena principale, dinamica facoltativa, confronto prima/dopo | Solvente implicito OBC2 invece di GBn2, molto più lento sulla CPU; strumenti nell'immagine principale (circa 140 MB in più in tutto) invece che in un profilo separato |
| F9.2 | fatto | Mutagenesi e scansione di alanina con MODELLER (protocollo `mutate_model` esteso ai vicini entro 5 Å), stima qualitativa dell'effetto con indicatori dichiarati, energia facoltativa con OpenMM, varianti di UniProt | Nessun ΔΔG: FoldX e Rosetta non sono ridistribuibili, e la stima resta qualitativa con l'avvertenza. Varianti da UniProt (naturali, di malattia, di mutagenesi), non da ClinVar |
| F9.3 | fatto | Confronto con ESMFold (consenso esplicito, indirizzo configurabile, fino a 400 residui) o con il modello di AlphaFold DB; TM-score, lDDT per residuo, regioni divergenti incrociate con il pLDDT e il consenso | In più rispetto al piano: AlphaFold DB come seconda sorgente e l'accordo nel consenso e nel report |
| F9.4 | fatto | Pacchetti riproducibili (manifest con versioni, parametri, semi, checksum), riesecuzione da interfaccia (`/rerun`) e con `python bioloom.py rerun`, confronto dei risultati | Esito «compatibile» in più per il rumore di OpenMM su più thread (tolleranze scientifiche dichiarate), distinto da «equivalente» (tolleranze numeriche). Ricerca, QMEAN e predizione hanno il pacchetto ma non si rieseguono (dipendono da servizi remoti) |

**Dopo le fasi: gestione dello spazio.** Eliminazione dei progetti e pulizia dei job, per non accumulare file inutili
(fuori dalla tabella, che è per fasi; funzionamento in
[ARCHITETTURA.md](ARCHITETTURA.md#eliminazione-di-progetti-e-pulizia-dei-job)). Le scelte e i motivi:

- **Solo manuale:** l'utente sceglie i criteri, vede l'anteprima e conferma; nessuna eliminazione automatica dei
  risultati dei job, né all'avvio, né nel passo giornaliero, né con «Pulisci file temporanei». La pulizia di massa non
  seleziona mai job attivi, conserva per impostazione predefinita stella, etichette e pipeline riprendibili, e i
  completati richiedono «Ho capito».
- **Ciclo a tempo invece di un job:** la pulizia è una serie di chiamate brevi e senza stato (`remaining` dice quanti job
  restano), ripetute dall'interfaccia con «Interrompi» fra l'una e l'altra. Ogni chiamata porta l'istante
  dell'anteprima confermata (`as_of`), così un job terminato dopo l'anteprima non si elimina. Un job di pulizia
  occuperebbe uno slot del pool dietro le pipeline lunghe, proprio quando serve spazio, e comparirebbe nell'elenco che
  sta svuotando. L'eliminazione di un progetto resta una richiesta completa e sincrona; l'interfaccia prima svuota il
  progetto con lo stesso ciclo, senza `as_of` perché lo svuotamento deve essere completo, e un «Interrompi» durante lo
  svuotamento impedisce sempre il DELETE.
- **Validazione nel servizio:** i campi della pulizia sono tipizzati largo e ogni valore non valido, anche di un altro
  tipo JSON (`"no"` al posto di `false`), è un 400 con il motivo in italiano, mai un 422 di Pydantic in inglese né una
  conversione silenziosa che cambierebbe i job eliminati.
- **INSERT condizionato in `submit`:** il job si crea solo se il progetto esiste, nello stesso statement; insieme al
  DELETE del progetto condizionato all'assenza di job, nessun job resta orfano senza lock globali né uno stato «in
  eliminazione» (che avrebbe richiesto una migrazione); e se la preparazione fallisce non restano né una riga `queued`
  mai accodata né la cartella (un resto con file in uso passa in `.deleting-<id>`).
- **Tombstone invece della scansione delle cartelle orfane:** la cartella di un job si rinomina in `.deleting-<id>`
  prima del DELETE della riga, e la pulizia automatica toglie solo quei resti, e solo se la riga del job non c'è più
  (altrimenti la cartella torna al suo posto). Una scansione delle cartelle senza riga, dopo un database perso o
  ripristinato da `backups/`, eliminerebbe risultati veri.

Nessuna migrazione dello schema; test in `test_cleanup.py` (98) e in quattro file del frontend (65).

Rischi trasversali: FoldX e Rosetta sono esclusi perché le loro licenze non sono ridistribuibili; l'immagine cresce di
circa 140 MB per fpocket, APBS, PDB2PQR, OpenMM e PDBFixer (`bioloom:4.1.0` 869 MB, immagine di sviluppo 1,04 GB);
l'unica migrazione dello schema è quella delle etichette (`priority` e `interrupted` non sono servite), con la copia di
sicurezza automatica; i servizi remoti hanno cache, timeout e consenso dove inviano sequenze o coordinate.

## Seguiti possibili

Limiti noti dichiarati nel codice e in [ARCHITETTURA.md](ARCHITETTURA.md), da cui partire per un'evoluzione successiva.

**Installazione e verifica**

- L'immagine `bioloom:4.1.0` dell'avviatore non contiene fpocket, APBS, PDB2PQR, OpenMM e PDBFixer: tasche,
  elettrostatica e rifinitura funzionano dopo averla ricostruita (avviatore o `docker compose -f launcher/compose.yml
  build`); i loro test girano nell'immagine di sviluppo `bioloom:dev`.
- Un calcolo QMEAN locale completo non è stato verificato sul PC di sviluppo (5,7 GB per Docker contro i 12 richiesti da
  UniRef30_2020_06): ricerca dei database, vista, copia locale e avvio di HHblits sì.
- Sui Mac Apple Silicon i container girano in emulazione x86 (MODELLER, ProSA e QMEAN esistono solo per x86).
- Il frontend si controlla solo con `tsc --noEmit` e vitest: ESLint non è installato.
- I valori per 3IU0 e 9QB7 non sono ripetuti dai test (i file non sono in `tests/data`).

**Calcolo e prestazioni**

- La costruzione dei modelli, l'86% del tempo di un job model, resta seriale: dividerla fra processi cambierebbe i modelli
  a parità di seme; servirebbero semi dichiarati per modello, registrati nel risultato.
- Il DOPE di più modelli in un solo script è stato valutato e non applicato (guadagno stimato di 0,5 s, perdita
  dell'isolamento per modello).
- Solo QMEAN ha un controllo della memoria prima di partire; gli altri tipi di job non dichiarano la RAM che usano.
- La voce `compare` di `core/jobs.PRIORITY` non ha un handler e si può togliere.

**Template e modellazione**

- La ridondanza fra template è di sola sequenza; un filtro strutturale (TM-score fra i candidati) richiederebbe di
  scaricarli tutti.
- Due frammenti dello stesso file caricati come template diversi ricevono l'avviso di orientamento non vincolato anche se
  condividono le coordinate: BioLoom non può saperlo.
- Ligandi: i gruppi di una catena senza residui proteici non si trasferiscono; il carbonio non è considerato donatore (CO
  e CN⁻ legati al ferro dell'eme risultano scontri); i codici minuscoli di `restyp.lib` in un PIR dell'utente si leggono
  come amminoacidi; il raffinamento dei loop non legge gli HETATM, quindi i ligandi non passano ai modelli di loop. Le
  distanze attese per Ser/Thr/Tyr e Met sono scelte di BioLoom non verificate su una fonte, e i valori per residuo di
  Bazayeva et al. 2024 non sono stati consultati.
- Complessi: un solo template per complesso, da 2 a 12 catene e al più 3000 residui; gli avvisi di copertura non si
  calcolano nella modalità Complesso né per i PIR a più catene; DockQ misura la fedeltà all'interfaccia del template, non
  la correttezza dell'interfaccia del bersaglio.
- AlphaFold DB: proteine oltre circa 2700 residui senza modello, isoforme non supportate.
- Nessun vincolo di interfaccia da PDBePISA (servizio EBI instabile).

**Qualità e analisi**

- Disordine: FoldIndex è molto meno accurato di IUPred2A e dei predittori di deep learning, non vede il disordine con
  composizione da proteina ripiegata né i loop corti, ha confini incerti di circa mezza finestra e non conosce il contesto
  (partner, cofattori, disolfuri).
- Interfacce: la plausibilità biologica si basa sulla sola area; non valuta conservazione né complementarità chimica
  (PISA, EPPIC).
- Elettrostatica: PDB2PQR toglie i residui modificati che non conosce (SEP, TPO…), con un avviso; la griglia è un solo
  calcolo con un tetto di memoria invece del `mg-para` di psize.
- Rifinitura: OBC2 invece di GBn2, ligandi e ioni esclusi, residui mancanti non ricostruiti; con più thread di OpenMM le
  esecuzioni differiscono di poco (esito «compatibile» nelle riesecuzioni).
- Mutagenesi: stima qualitativa, non un ΔΔG; il rumore dell'energia fra due selvatici ottimizzati arriva a 36 kJ/mol; il
  protocollo non considera ligandi, metalli, acqua né movimenti della catena principale oltre il residuo mutato; le
  varianti vengono solo da UniProt (ClinVar, previsto dal piano, non è integrato).
- Predizione esterna: il servizio pubblico di ESMFold accetta fino a 400 residui e potrebbe essere dismesso (l'indirizzo
  si cambia nelle Impostazioni); il confronto riguarda una catena e una sola conformazione, ed ESMFold è meno accurato con
  poche sequenze omologhe.

**Riproducibilità**

- Ricerca di template, QMEAN e predizione esterna hanno il pacchetto ma non si rieseguono, perché dipendono da servizi
  remoti; la pipeline non ha un pacchetto (c'è l'archivio ZIP dei risultati).
- Le versioni degli strumenti nel pacchetto sono quelle dell'installazione al momento dell'esportazione: i job non
  registrano l'ambiente a ogni esecuzione.

**Dati e spazio**

- Eliminazione automatica dei risultati dei job per età, disattivata per impostazione predefinita: oggi i risultati si
  eliminano solo a mano.
- Cestino o annullamento dell'eliminazione: un job o un progetto eliminato non si recupera.
- VACUUM e checkpoint del WAL dopo una pulizia grande: `bioloom.db` non si restringe, e un VACUUM richiede circa il
  doppio dello spazio del database e blocca l'unica connessione per tutta la sua durata.
- Una voce manuale, con anteprima, per le cartelle `jobs/<id>` già senza riga (eliminazioni non riuscite, copie
  parziali della cartella dati): nessuna scansione automatica, perché con un database perso o ripristinato sembrerebbero
  rifiuti.
- Potatura delle copie di sicurezza in `backups/` dal pannello dello spazio, che oggi le mostra soltanto.
- Eliminazione dei soli intermedi dei job falliti (conservati per la diagnosi), lasciando riga, log e risultati.
- Il rinomino della cartella di un job in `.deleting-<id>` (`os.replace`) è provato sul filesystem Linux dei test, non
  sul bind mount di Docker Desktop né su exFAT con file tenuti aperti dall'host Windows (il ripiego è un 409 «in uso»
  che lascia il job intero): da verificare sullo stack reale con una copia dei dati.
- `DELETE /api/projects/{id}` è sincrono: chiamato direttamente su un progetto con migliaia di job può superare il
  timeout di un proxy in modalità server (l'interfaccia svuota prima il progetto con la pulizia a tempo).
