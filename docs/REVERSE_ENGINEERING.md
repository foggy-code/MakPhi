# Reverse engineering di BioLoom (v3) — `D:\progetto_Bioloom`

Documento di analisi del sistema originale, redatto come base di progetto per **BioLoom v4**.
Tutti i riferimenti `file:riga` sono relativi a `D:\progetto_Bioloom\codice\`.

---

## 1. Scopo e dominio

BioLoom è una suite di bioinformatica strutturale per la **modellazione per omologia** di proteine.
Il flusso scientifico supportato è quello classico:

1. **Sequenza bersaglio** (manuale, FASTA o da UniProtKB).
2. **Ricerca di template** omologhi nel PDB (RCSB Search API, BLAST+ locale, NCBI QBlast remoto).
3. **Gestione template** (download da RCSB, upload PDB/mmCIF, analisi catene/residui mancanti/HETATM).
4. **Allineamento** (pairwise Needleman-Wunsch/Smith-Waterman, MSA con Clustal Omega/MAFFT/MUSCLE/MMseqs2,
   strutturale con TM-align/US-align) e **editor PIR** per MODELLER.
5. **Modellazione 3D** con MODELLER (`AutoModel`/`DOPELoopModel`, vincoli secondari, vincoli di interfaccia
   PDBePISA, raffinamento loop).
6. **Validazione di qualità** multi-metrica: DOPE (profilo per residuo), Ramachandran, clashscore, DSSP
   (struttura secondaria, SASA), B-factor, QMEAN (Docker locale o SWISS-MODEL remoto), ProSA II, PROCHECK,
   voto composito A–D.
7. **Pipeline batch ad alto throughput** ("Wizard"): multi-FASTA → ricerca template → modello → scoring,
   persistente lato server e indipendente dal browser.

## 2. Stack e topologia

| Livello | Tecnologia | Note |
|---|---|---|
| Backend | Python, FastAPI, Biopython, httpx/requests, psutil | ~10.000 righe + ~5.000 di test (`backend/`) |
| Frontend | React 19, Vite, TanStack Query, axios, Recharts, NGL, 3Dmol.js (CDN) | ~23.500 righe (`frontend/src/`) |
| Motori nativi | MODELLER 10.x, DSSP, BLAST+, Clustal Omega, MAFFT, MUSCLE, TM-align, MMseqs2, PROCHECK, ProSA II (32 bit) | installati nell'immagine Docker Ubuntu 22.04 |
| Deploy | Docker Compose: `backend`, `frontend` (Vite dev server), `docker-proxy` (socket proxy) | QMEAN eseguito come container "Docker-out-of-Docker" |
| Launcher | `start.bat` → `setup_and_run.py` (wizard console, `.env`, compose up) | usa `backend/env` (Python 3.10 + MODELLER 10.8) |
| Documentazione | Wiki Obsidian (`wiki/`) + Quartz, regole per agenti LLM (`AGENTS.md`, `.agents/`) | processo "wiki-first" con log di ~250 KB |

### Architettura a strati del backend (dopo il refactoring "Modernizzazione M3")

```
routers/   → endpoint HTTP e modelli Pydantic (15 router)
services/  → orchestrazione: modeller_service, batch_service, quality_service, alignment_service,
             blast_service, template_search_service, rcsb_fast_search, cleanup_service
domain/    → funzioni pure: quality_models (Ramachandran, clashscore, grade, REMARK), alignment_models
adapters/  → I/O: base_runner (scritture atomiche con workaround Windows/CIFS), modeller_runner
             (generatori di script), docker_runner (QMEAN DooD, ProSA), alignment_runner (CLI), path_security
```

Il refactoring è parziale: diversi router contengono ancora logica di dominio (es. `routers/pdb_analysis.py`,
`routers/prediction_api.py`, `routers/blast.py`), e `alignment_api.py` importa da `routers/alignment_jobs.py`
che a sua volta re-esporta i servizi.

## 3. Catalogo API (v3)

Tutte sotto `/api` (salvo dove indicato). 70 endpoint totali.

| Area | Endpoint principali |
|---|---|
| BLAST | `POST /blast` (engine `rcsb`/`local`/`ncbi`), `GET /blast/{id}`, `DELETE /blast/cancel/{id}`, `GET /blast/database/status`, `POST /blast/database/download` |
| UniProt | `GET /uniprot/{id}`, `GET /uniprot/{id}/interpro` |
| RCSB | `POST /rcsb/metadata`, `POST /rcsb/search` |
| Template | `POST /pdb/download/{id}`, `POST /pdb/upload`, `GET /pdb/list`, `DELETE /pdb/clear`, `GET /template/{id}`, `GET /template/{id}/raw` |
| FASTA | `POST /fasta/save`, `POST /fasta/download/{uniprot}`, `GET /fasta/list`, `GET /fasta/{uniprot}` |
| Predizione | `POST /predict/ss` (GOR semplificato), `POST /predict/dssp-from-pdb` |
| Allineamento | `POST /align` (sincrono), `POST /align/async`, `GET /align/status/{id}`, `GET /align/result/{id}?format=`, `DELETE /align/cancel/{id}` |
| MODELLER | `POST /modeller/run` (`align2d`/`model`/`evaluate`), `GET /modeller/status/{id}`, `GET /modeller/log/{id}`, `POST /modeller/refine-loop`, `DELETE /modeller/cancel/{id}`, risultati/zip/export-bundle-v2 |
| Batch | `POST /modeller/batch/submit`, `GET /modeller/batch/status/{id}`, `GET /modeller/batch/results/{id}` (paginato), `POST /modeller/batch/cancel/{id}`, `GET /modeller/batches[/{id}]` |
| Qualità | `POST /quality/analyze`, `POST /quality/upload`, gestione file (rename/delete file e cartelle), UniClust (status/path/reconnect), `GET /quality/list-directories` |
| Sessioni | CRUD `/sessions`, `/sessions/{id}/state`, run MODELLER di sessione, clear |
| Sistema | `/api/system/stats`, `/api/system/shutdown(-status)`, `/api/user/settings`, `/api/wiki/*`, `/api/export/pymol` |

## 4. Modello dati su disco

```
backend/templates/                         libreria template PDB/CIF (globale)
backend/data/sessions/{uuid}/              metadata.json, state.json (blob di stato del frontend)
    modeller_runs/{job}/                   script, alignment.pir, stdout.log, modelli, run_info.json
backend/data/jobs/modeller/{job}/state.json   registro job MODELLER (JSON atomico)
backend/data/jobs/alignment/...            registro job di allineamento (JobRegistry separato)
backend/data/jobs/batch/{batch}/state.json registro batch con l'array completo dei sotto-job
backend/data/modeller_results/{batch}/{seq}/{template}/   modelli batch + *_quality.json
backend/data/cache/{blast,pisa}/           cache risultati remoti
backend/databases/                         pdbaa, uniclust_config.json, UniClust30 esterno
```

Tre registri di job indipendenti (MODELLER, allineamento, batch) implementano ciascuno la propria
persistenza, idratazione all'avvio e cancellazione; i job BLAST vivono solo in un dizionario in memoria.

## 5. Flussi principali

**Modellazione singola** (`services/modeller_service.py:243`): parsing del PIR fornito → sanificazione
identificatori → ricerca del template in 4 cartelle + download automatico da RCSB → filtro catene/HETATM →
se manca l'allineamento del template, allineamento Biopython BLOSUM62 → iniezione di `.` per gli HETATM →
generazione dello script `AutoModel` → sottoprocesso con timeout 600 s → parsing del riepilogo testuale di
MODELLER → *tiered scoring* (analisi completa solo sul modello con DOPE migliore) → `REMARK 999` nel PDB.

**Batch** (`services/batch_service.py:429`): un thread orchestratore per batch con `ThreadPoolExecutor`
(`N_core − 1` in profilo `local`); per ogni sequenza: ricerca template (MMseqs2 → BLAST+ locale → NCBI),
filtro *twilight zone* opzionale (identità < 25 % o copertura < 40 %, **disattivato di default**),
MODELLER, metriche. Lo stato completo viene riscritto su disco a ogni aggiornamento di ogni sotto-job.

**Qualità** (`services/quality_service.py:95`): parsing Biopython, phi/psi → classificazione, B-factor, DSSP
(solo se `mkdssp` presente), DOPE via MODELLER in-process, QMEAN (Docker con UniClust30, altrimenti
SWISS-MODEL), clashscore, PROCHECK, ProSA, voto composito.

## 6. Regole di dominio estratte

- **Ramachandran** (`domain/quality_models.py:26`): regioni rettangolari "ispirate a Lovell 2003", con casi
  separati per GLY e PRO; restituisce `core` / `allowed` / `disallowed`.
- **Clashscore** (`domain/quality_models.py:74`): sovrapposizione di van der Waals > 0,4 Å tra atomi pesanti di
  residui diversi, ×1000 / numero di atomi.
- **Voto BioLoom** (`domain/quality_models.py:246`): `score = 100 − min(40, (outlier% − 0,5)·8) − min(30, clashscore/1,5)`;
  A ≥ 85, B ≥ 70, C ≥ 55, altrimenti D.
- **Ricerca RCSB veloce** (`services/rcsb_fast_search.py`): query di sequenza RCSB → sequenze di catena via
  GraphQL → riallineamento Smith-Waterman locale BLOSUM62 (gap 11/1) → bit score e E-value
  Karlin-Altschul (λ = 0,267, K = 0,041, dimensione DB fissata a 5·10⁷).
- **PIR per MODELLER**: target `sequence:ID:::::::0.00: 0.00`; template
  `structureX:ID:primo_res:catena:ultimo_res:catena::::`; catene separate da `/`; un `.` per ogni ligando HETATM.
- **GOR** (`routers/prediction_api.py:20`): finestra di 7 residui con pesi euristici (non è GOR I/III/IV reale).
- **Tiered scoring**: DOPE per tutti i modelli, analisi completa solo sul migliore.

## 7. Difetti rilevati

### 7.1 Errori funzionali (bug)

| # | Dove | Problema | Effetto |
|---|---|---|---|
| B1 | `backend/routers/modeller_api.py:626` | `time.time()` usato senza `import time` | `GET /api/modeller/batches` va in errore 500 appena il registro batch contiene un batch |
| B2 | `backend/services/modeller_service.py:429` vs `routers/pdb_analysis.py:165` | `filter_pdb_file(..., chains=..., hetatm_names=...)` ma la firma è `(pdb_path, output_path, selected_chains, selected_hetatm)` | `TypeError` in ogni modellazione con selezione di catene/HETATM: la funzione è inutilizzabile |
| B3 | `frontend/src/contexts/TaskContext.jsx:147,287` | chiama `/api/alignment/jobs/{id}` e `/cancel`; il backend espone `/api/align/status/{id}` e `/api/align/cancel/{id}` | il monitor dei task non segue né annulla i job di allineamento |
| B4 | `frontend/src/hooks/useWizardMode.js:9,16` | chiama `/api/blast/search` e `/api/align/pairwise` (inesistenti) | 404 |
| B5 | `frontend/src/components/CompareModels.jsx:24` | chiama `/api/structure-quality/evaluate` (inesistente) | il confronto modelli non funziona |
| B6 | `backend/routers/blast.py:39` | job BLAST solo in memoria | persi a ogni riavvio (con hot-reload attivo anche a ogni modifica del codice) |
| B7 | `backend/services/quality_service.py:246` | senza `mkdssp` la struttura secondaria resta vuota senza segnalazione | su Windows nativo la DSSP non è mai disponibile |
| B8 | `backend/routers/modeller_api.py:280` | download con fallback "per nome file" su tutto l'albero risultati | con più batch può restituire il file sbagliato |

### 7.2 Errori scientifici

**Clashscore sovrastimato.** Il filtro esclude solo le coppie backbone–backbone tra residui adiacenti: le
distanze covalenti dell'anello della prolina (`C(i−1)–CD(i)` a 1–3 legami, `CA(i−1)–CD(i)` a 1–4) e le molecole
d'acqua vengono conteggiate come contatti sterici. Verifica empirica sui file del progetto:

| Struttura | Clashscore v3 | Coppie spurie dominanti |
|---|---|---|
| 1UBQ (cristallo 1,8 Å) | **31,8** (21 clash) | `C–CD` e `CA–CD` prolina, `O–O` con acque |
| 3IU0 | **24,9** (73 clash) | `C–CD` (21), `CA–CD` (20) prolina |

Con la formula del voto, un clashscore di 31,8 sottrae ~21 punti: un cristallo di riferimento scende di due classi.

**Ramachandran troppo restrittivo.** Le regioni "core" escludono la poliprolina II (φ ≈ −70°, ψ ≈ 145°) e il
bordo dell'α-elica (φ ≈ −75°, ψ ≈ −5°), che sono tra le conformazioni più popolate nelle proteine native:

| Struttura | Core v3 | Allowed v3 | Disallowed v3 |
|---|---|---|---|
| 1UBQ | 81,1 % | 18,9 % | 0 % |
| 3IU0 | 77,1 % | 22,6 % | 0,3 % |
| 9QB7 | 72,7 % | 26,2 % | 1,1 % |

Per confronto, MolProbity riporta per 1UBQ circa il 98 % di residui in regione favorita. Anche il GOR
"semplificato" è un'euristica e va dichiarato come tale nell'interfaccia.

### 7.3 Sicurezza

- `GET /api/quality/list-directories` (`routers/structure_quality_api.py:173`) permette di sfogliare l'intero
  filesystem del server (`/`, dischi host montati).
- `POST /api/quality/uniclust-reconnect` riavvia il container tramite il socket proxy di Docker, senza autenticazione.
- Upload senza limite di dimensione (`/api/pdb/upload`, `/api/quality/upload`).
- Backend esposto su `0.0.0.0:8000` senza autenticazione, con hot-reload attivo anche nell'immagine.

### 7.4 Debito architetturale

- Tre registri di job duplicati più uno volatile; contratti frontend↔backend non tipizzati (causa di B3–B5).
- `force_rmtree` duplicato in 3 file; molti workaround per i lock dei bind-mount Windows↔Docker
  (`safe_replace`, `safe_copy_file`, `get_host_path` via `docker inspect`).
- Pagine frontend monolitiche: `SequenceAlignment.jsx` (2.965 righe), `StructureAnalysis.jsx` (2.469),
  `WizardMode.jsx` (1.464), `ModellerIntegration.jsx` (1.436).
- Timeout fisso di 600 s per MODELLER: i modelli multipli o `thorough` vengono uccisi.
- Riscrittura dell'intero stato del batch (tutti i sotto-job) a ogni aggiornamento: O(n²) di I/O su batch grandi.
- Deploy dipendente da Docker Desktop, socket proxy e container QMEAN da diversi GB per una funzione opzionale.

## 8. Scelte per BioLoom v4

| Tema v3 | Decisione v4 |
|---|---|
| Docker obbligatorio | Esecuzione **nativa** (Windows/Linux/macOS) con un solo processo; Docker opzionale per gli strumenti Linux |
| 3 registri job + BLAST volatile | **Un solo `JobManager`** persistente su SQLite, con log incrementali, progresso, cancellazione e riavvio sicuro |
| MODELLER importato nel server | MODELLER sempre in **sottoprocesso** (interprete configurabile); lo script salva `a.outputs` in JSON invece del parsing di stdout |
| Allineamento template con Biopython | Allineamento **`align2d`/`salign` eseguito da MODELLER stesso** (nessuna discrepanza tra sequenza PIR e coordinate) |
| DSSP solo se installato | DSSP nativo se presente, altrimenti **assegnazione DSSP integrata** (energia dei legami H di Kabsch-Sander) |
| Clashscore con falsi positivi | Esclusione per **grafo dei legami covalenti** (≤ 3 legami) e delle acque; tolleranza per i legami H |
| MSA/strutturale solo con binari | Fallback integrati: MSA *center-star* e superposizione Kabsch + TM-score |
| QMEAN via Docker DooD | QMEAN come azione esplicita via SWISS-MODEL (opt-in, con email dell'utente) |
| Sessioni con blob di stato | **Progetti** con sequenze e documenti di allineamento persistenti |
| Contratti non tipizzati | API unica documentata (OpenAPI) e client TypeScript tipizzato |
| Nessun limite/controllo | Bind su `127.0.0.1`, limiti upload, nessun endpoint di navigazione del filesystem |

Il dettaglio dell'architettura v4 è in [`ARCHITETTURA.md`](ARCHITETTURA.md).
