# Architettura di BioLoom v4

BioLoom v4 è la riscrittura di BioLoom v3 (vedi [REVERSE_ENGINEERING.md](REVERSE_ENGINEERING.md)).

**Obiettivi:**

- stesso dominio scientifico della v3;
- tutto il programma in container Docker, portatile tra sistemi operativi, chiavetta e server;
- contratti API unici e verificati;
- correzione dei difetti scientifici rilevati nella v3;
- funzionamento anche senza gli strumenti esterni facoltativi.

## Vista d'insieme

```
Computer ospite (Windows, macOS, Linux; Apple Silicon in emulazione x86)
  Avvia BioLoom.bat/.command, avvia-bioloom.sh ─▶ launcher/bioloom.ps1 | bioloom.sh ─▶ docker compose (progetto "bioloom")
    · trova UniClust30/QMTL sui dischi · legge il MAC da LicenseDB · porta libera · carica immagini/ o le costruisce

Container "bioloom" (Ubuntu 24.04, Python 3.12, utente = proprietario di /data)
  Browser ──HTTP──▶ FastAPI (8765; pubblicata su 127.0.0.1, oppure in rete con chiave d'accesso)
                     ├─ /api/*   rotte REST (OpenAPI su /docs)      ├─ /*  interfaccia React compilata
                     ├─ JobManager: pool "cpu" (core−1) + "io" (8) · stato su SQLite · lavoro in /scratch
                     │    └─ sottoprocessi: MODELLER 10.8, mkdssp, BLAST+, MMseqs2, Clustal Omega, MAFFT, MUSCLE,
                     │                      TM-align, US-align, PROCHECK, ProSA II
                     └─ servizi remoti: RCSB · UniProt · InterPro · NCBI QBlast · SWISS-MODEL (opt-in)
  /data     ◀─ bind mount  cartella data/ (chiavetta, cartella o server): viaggia con BioLoom
  /scratch  ◀─ volume      lavoro dei job e file temporanei (veloce, svuotato a ogni avvio)
  /cache    ◀─ volume      indici ricostruibili di questo computer (MMseqs2)

Container "qmean" (profilo compose "qmean", immagine ufficiale QMEAN 4.3.0 + worker HTTP)
  rete interna :8080 (nessuna porta pubblicata) · /uniclust_src e /qmtl in sola lettura dal disco esterno
  /cache ◀─ volume qmean_cache: cs219 ricreati per i database HH-suite 2

data/
  bioloom.db          progetti, sequenze, documenti, job, elementi di pipeline (SQLite, WAL esclusivo)
  jobs/<id>/          params.json, job.log, result.json e i soli risultati (niente intermedi MODELLER)
  templates/          libreria globale (ID.pdb|cif + ID.meta.json + ID.analysis.json in cache)
  uploads/  cache/    file caricati per l'analisi; risposte remote e cache dei calcoli in cache/compute (pulizia automatica)
  licenses/           modeller.key, prosa/LicenseDB (permessi 0600, mai nelle immagini)
  databases/          pdbaa (BLAST+), pdbaa_mmseqs (database MMseqs2 senza indice)
  settings.json       impostazioni utente
```

### Flusso dei dati e file temporanei

- **Esecuzione nello scratch:** ogni job lavora in `/scratch/jobs/<id>`. Nella cartella dati restano, e
  sono subito visibili, solo `params.json` e `job.log` (con flush al massimo ogni 0,5 s, per non martellare
  la chiavetta).
- **Salvataggio definitivo a fine job:** i risultati passano in `data/jobs/<id>` e lo scratch viene
  cancellato.
  - Job riuscito o annullato: si scartano i file intermedi di MODELLER (`*.rsr`, `*.sch`, `*.ini`,
    traiettorie `.D…`, violazioni `.V…`, modelli iniziali di loop) e le copie temporanee, salvo
    l'impostazione "Conserva file intermedi".
  - Job fallito: si conserva tutto, per la diagnosi.
- **Pipeline:** ogni elemento viene salvato appena finisce (`JobContext.commit`), così un batch lungo non
  perde lavoro e lo scratch resta piccolo.
- **Archivi ZIP** (job e pipeline): vengono scritti in un file temporaneo dello scratch e inviati con
  `FileResponse`, che lo elimina dopo l'invio. Nessun archivio passa dalla memoria.
- **SQLite:** `locking_mode=EXCLUSIVE` impostato prima del WAL, quindi nessun file `-shm`. È affidabile
  sui bind mount di Docker Desktop e su exFAT, e impedisce a una seconda istanza di aprire la stessa
  cartella dati (errore chiaro).
- **Pulizia automatica** (`services/housekeeping.py`), all'avvio, ogni 24 ore e con un pulsante:
  - upload più vecchi dei giorni impostati;
  - cache remota e cache dei calcoli oltre i 30 giorni, più i file temporanei di scritture interrotte;
  - scratch orfano.
- **Processi esterni:** ognuno ha il proprio gruppo di processi; annullamento e timeout terminano anche i
  figli (mafft e PROCHECK sono script). Il timeout dei job vale per tutti gli strumenti.
- **MMseqs2:** il database (≈ 140 MB) sta nei dati. L'indice k-mer (≈ 1,5 GB) sta nel volume `/cache` di
  ogni computer, con un timbro dimensione/data che lo invalida quando il database cambia.

  | Ricerca | Tempo misurato |
  |---|---|
  | indice nel volume locale | 2,3 s |
  | indice sul bind mount | 22 s |
  | senza indice | 12 s |

### Licenze (`services/licenses.py`)

- **MODELLER:** la chiave viene da `data/licenses/modeller.key` oppure da `MODELLER_KEY`.
  - Arriva ai soli sottoprocessi tramite `BIOLOOM_MODELLER_KEY`.
  - Il preambolo degli script (`engines/tools.LICENSE_PREAMBLE`) esegue il `config.py` dell'installazione e
    registra `modeller.config` con la chiave in `sys.modules` prima di `import modeller`. L'installazione
    non viene modificata.
  - Una chiave nuova viene provata con MODELLER prima di essere salvata; nelle risposte è sempre mascherata.
- **ProSA II 2003:** `LicenseDB` ha righe `programma scadenza MAC codice`. L'avviatore imposta quel MAC sul
  container (`mac_address`), e l'interfaccia confronta il MAC richiesto con quello effettivo.
- **PROCHECK:** licenza accademica senza chiave.

## Backend (`backend/bioloom/`)

| Pacchetto | Contenuto |
|---|---|
| `core/` | `storage` (scritture atomiche, `safe_join`, identificatori), `db` (SQLite esclusivo), `jobs` (JobManager, JobContext, scratch e salvataggio definitivo), `cache` (cache dei calcoli), `auth` (chiave d'accesso), `errors`, `container` (composizione dei servizi) |
| `domain/` | Logica pura e testata: `sequences` (FASTA, IUPAC, PIR, Clustal, statistiche), `structure` (PDB/mmCIF, catene, header, filtri), `ramachandran`, `clashes`, `secondary` (DSSP integrata + GOR), `geometry` (Kabsch, TM-score), `grading`, `procheck` (lettura del .sum), `exports` (PyMOL) |
| `engines/` | Adattatori: `modeller` (preparazione template, script, esecuzione), `quality` (pipeline di validazione), `procheck`, `prosa`, `qmean_local` (client del worker), `align`, `search` (RCSB, NCBI, BLAST+, MMseqs2), `remote` (client HTTP), `tools` (rilevamento strumenti, preambolo di licenza) |
| `services/` | `projects`, `templates` (libreria), `compute` (handler dei job), `pipeline` (batch), `licenses`, `databases` (pdbaa, MMseqs2, stato UniClust), `housekeeping` (pulizia e spazio), `selftest` (verifica dell'installazione) |
| `api/` | Rotte FastAPI sottili con modelli Pydantic (`auth`, `licenses`, `databases` oltre a sistema, progetti, calcoli, job) |

### Job

Tutti i calcoli (`search`, `align`, `align2d`, `model`, `loop`, `quality`, `qmean`, `pipeline`) passano dal
`JobManager`:

- stato `queued → running → completed | failed | cancelled` (più `cancelling`) salvato su SQLite;
- log incrementale (`GET /api/jobs/{id}/log?offset=`) con l'output dei sottoprocessi in tempo reale;
- progresso e fase aggiornati dagli handler (per MODELLER tramite marcatori `@@BIOLOOM` stampati dallo script);
- annullamento immediato (i processi registrati vengono terminati) e timeout configurabile;
- al riavvio i job rimasti attivi vengono marcati come falliti (nessuno stato "fantasma").

### MODELLER

Il server non importa mai `modeller`: ogni operazione è uno script generato ed eseguito in un
sottoprocesso. Nel container è MODELLER 10.8 del pacchetto .deb; in esecuzione nativa di sviluppo si può
indicare un altro interprete (`BIOLOOM_MODELLER_PYTHON`). Flusso di un job `model`:

1. **Template**: conversione in PDB pulito (catene scelte, ligandi facoltativi, niente acqua, MSE→MET).
2. **Allineamento**: se non viene fornito un PIR, MODELLER stesso lo calcola (`align2d` con un template,
   `salign` strutturale + allineamento sul profilo con più template). Il PIR fornito dall'utente viene validato
   (lunghezze, catene, caratteri) e riallineato ai codici/file dei template preparati.
3. **AutoModel / DOPELoopModel** con raffinamento (`very_fast`/`fast`/`normal`/`thorough`), vincoli di
   struttura secondaria e di distanza, regioni di loop, seme casuale. I risultati (`molpdf`, DOPE, GA341,
   zDOPE) sono scritti in `models.json` direttamente da `a.outputs`.
4. **Selezione e qualità**: modello migliore = DOPE minimo; analisi completa sul migliore (o su tutti), con
   righe `REMARK 999 BIOLOOM` di tracciabilità nel PDB.

### Validazione di qualità (`engines/quality.py`)

| Metrica | Implementazione |
|---|---|
| Ramachandran | φ/ψ calcolati per catena rispettando le interruzioni; regioni favorite/consentite per caso generale, glicina, prolina (pre-prolina come generale); cis-peptidi non prolinici |
| Clashscore | atomi pesanti, raggi di Probe, esclusione delle coppie entro 3 legami (grafo covalente) e delle acque, tolleranza per coppie polari |
| Struttura secondaria | `mkdssp` se installato, altrimenti DSSP integrato (energia elettrostatica dei legami H, turn, eliche α/3₁₀/π, ponti e ladder β, bend) |
| SASA relativa | `mkdssp` oppure Shrake-Rupley di Biopython normalizzato con le ASA massime di Tien et al. 2013 |
| DOPE | MODELLER: totale, z-score normalizzato, profilo per residuo (finestra 15) |
| Voto A–D | 100 − penalità outlier Ramachandran − penalità clashscore − penalità zDOPE (> −1) |
| PROCHECK | `procheck.scr` (csh + Fortran compilato nell'immagine): Ramachandran a 4 regioni, G-factor, parametri di legame; grafici PostScript tra i file del job |
| ProSA II | z-score combinato, di coppia e di superficie; profilo di energia per residuo (finestra 1 e 10) |
| QMEAN | su richiesta. **Locale**: worker nel container `qmean` (HHblits su UniClust30/UniRef30 di qualunque versione; QMEANDisCo se c'è la QMTL), un calcolo alla volta, annullabile. **Remoto**: SWISS-MODEL, con email e consenso esplicito. Modalità `local` / `remote`, più **fallback facoltativo** dal locale a SWISS-MODEL (impostazione o scelta del singolo calcolo) |

### QMEAN locale con qualunque versione di UniClust30 (`docker/qmean/uniclust.py`)

- **Ricerca:** il worker esplora la cartella montata su `/uniclust_src` fino a 4 livelli e riconosce ogni
  database HH-suite dal file `<nome>_a3m.ffindex` (HH-suite 3) o `<nome>_a3m_db.index` (HH-suite 2). Per
  ciascuno ricava versione (`AAAA_MM` nel nome), famiglia (UniRef30 / uniclust30), formato, presenza degli
  HMM e dimensione.
- **Controlli:** un controllo rapido di coerenza (voci dell'indice entro il file dati) segnala le copie
  interrotte. Vengono segnalati anche gli archivi `.tar.gz` non estratti e i database MMseqs2 di ColabFold,
  che HHblits non può usare.
- **Scelta del database:** quello indicato in BioLoom (impostazione `qmean_uniclust`), altrimenti il più
  recente pronto. Non c'è mai un ripiego silenzioso su un'altra versione.
- **Vista per il calcolo:** per ogni calcolo si crea una vista con i nomi standard (collegamenti simbolici,
  nessuna scrittura sul disco).
  - `run_qmean.py` viene avviato da `run_qmean_wrapper.py`, che gli passa quel prefisso: da solo QMEAN
    accetterebbe un unico database con tutti e 6 i file.
  - Senza HMM, HHblits 3 li calcola dagli allineamenti (verificato). Un file HMM vuoto, invece, lo manda
    in errore: per questo la vista non ne crea di finti.
- **Database HH-suite 2** (uniclust30 2016–2017): il `.cs219` non è leggibile da HHblits 3.
  - Il job `db_convert_uniclust` lo ricrea dagli allineamenti con `cstranslate -f -x 0.3 -c 4 -I a3m` e lo
    conserva nel volume `/cache` insieme a un timbro dell'indice a3m.
  - Se il database sul disco cambia, il timbro non corrisponde più e la conversione va ripetuta.
- **Copia locale degli indici** (job `db_local_uniclust`): serve con Docker Desktop, dove il disco arriva nel
  container come `9p` (Windows) o `virtiofs` (macOS).
  - **Misure con UniRef30_2020_06 da disco USB:** 38–60 MB/s a blocchi grandi, ma 1,8 MB/s con le letture da
    512 byte con cui HHblits scorre gli indici. Anche il cs219, mappato in memoria, arrivava a circa 2 MB/s:
    dopo 20 minuti HHblits non aveva finito di leggerlo.
  - **Cosa si copia:** indici (`*.ffindex`) e cs219 vanno nel volume `/cache` (7,3 GB in 185 s, a
    42–49 MB/s). I file grandi (`a3m.ffdata`, `hhm.ffdata`) restano sul disco e se ne leggono solo le voci
    trovate.
  - **Timbro:** ogni file copiato porta dimensione e data dell'originale. Se non corrispondono, la vista torna
    al file sul disco e la scheda chiede di aggiornare la copia.
  - **Coda:** copia e rimozione passano dalla stessa coda dei calcoli, quindi non cambiano mai i file sotto
    un HHblits in corso.
  - **Riconoscimento:** il filesystem si legge da `/proc/mounts`; su Linux con disco montato direttamente
    (`ext4`, `xfs`…) la copia non viene proposta.
- **Processori e memoria:**
  - **Memoria:** HHblits tiene in memoria gli indici letti (misurati 3,8 GB per 1,36 GB di indici) e mappa
    il cs219 dal file. Nel prefiltro con gap lo legge in ordine sparso, quindi il cs219 deve stare tutto nella
    cache.
    - **Misura:** con 5,7 GB per Docker ne restavano in memoria 36 MB su 6,3 GB. HHblits ha letto oltre
      100 GB in 4 minuti con la CPU ferma; lo swap si è riempito e le cartelle condivise davano
      `Cannot allocate memory`.
    - **Stima del worker:** confronta con la memoria del container (`/proc/meminfo`, limite del cgroup)
      due valori: minimo = 3 × indici + 1 GB; consigliato = minimo + cs219.
    - **Esito:**
      - `ok` se il consigliato sta nel 90% della memoria;
      - `swap` (parte ma lento, con avviso e memoria consigliata) se il minimo sta in memoria + swap;
      - altrimenti `insufficient`: HHblits non parte e il backend passa al fallback, se attivo.
  - **Processori:** `run_qmean.py` lancia HHblits con `-cpu 1`; il wrapper ne passa di più.
    - Con memoria `ok` usa tutti i processori del container, fino a 8.
    - Con memoria al limite ne usa 1: a parità di tempo, 4 thread avevano letto 190 GB contro 35.
    - `QMEAN_HHBLITS_CPU` fissa il numero.
    - Il risultato non cambia.
  - **Codifica:** l'immagine QMEAN (CentOS 7) non ha `C.UTF-8`. Senza `LANG=en_US.UTF-8` Python 3.6 usa ASCII
    e un messaggio con lettere accentate fermava il worker all'avvio.
- **Verifiche:**
  - sul disco reale `UniRef30_2020_06` (HH-suite 3, 194 GB, disco USB), per ricerca, vista, copia locale e
    avvio di HHblits;
  - un calcolo QMEAN completo non è stato possibile per la memoria di questo PC (5,7 GB per Docker su 11,8 di RAM);
  - su un disco di prova con tre versioni in formati diversi e un archivio non estratto.

Valori sulle strutture di riferimento (test automatici):

| Struttura | v3 core/favorite | v4 favorite | v3 clashscore | v4 clashscore |
|---|---|---|---|---|
| 1UBQ | 81,1 % | 95,9 % | 31,8 | 1,7 |
| 3IU0 | 77,1 % | 94,3 % | 24,9 | 0,4 |
| 9QB7 | 72,7 % | 94,0 % | — | 0,2 |

### Allineamenti

- **Pairwise**: Biopython `PairwiseAligner` (NW/SW, BLOSUM/PAM/GONNET, NUC.4.4), gap terminali gratuiti nel globale.
- **Multiplo**: Clustal Omega → MAFFT → MUSCLE se installati, altrimenti *center-star* integrato; matrice di identità.
- **Strutturale**: US-align/TM-align se installati, altrimenti sovrapposizione di Kabsch con ricerca del
  TM-score massimo (algoritmo del programma TM-score) su un allineamento di sequenza; PDB sovrapposto per il viewer.

### Ricerca di template

- **RCSB** (predefinito): Search API (MMseqs2 lato server) → sequenze di catena via GraphQL → riallineamento
  Smith-Waterman locale per identità, copertura e coordinate. Ritenta automaticamente i timeout 5xx di RCSB.
- **NCBI QBlast**: conforme alle linee guida (tool/email, polling ogni 60 s, attesa massima 45 min).
- **BLAST+ locale**: `blastp` contro `data/databases/pdbaa` (scaricabile e aggiornabile da NCBI con un job).
- **MMseqs2 locale**: `easy-search -s 7.5` sul database creato da pdbaa (`blastdbcmd` → `createdb`), con
  l'indice locale se presente.
- Template consigliato: massimo identità × copertura, a parità miglior risoluzione.

### Cache dei calcoli (`core/cache.py`)

Ricerche di template e allineamenti multipli e strutturali non si rifanno se la stessa richiesta è già stata
calcolata: rilanciare una pipeline con le stesse sequenze e altre opzioni di modellazione salta tutte le
ricerche. Il pairwise è istantaneo e non passa dalla cache.

- **Dove:** un file JSON per voce in `data/cache/compute/<operazione>/<chiave>.json` (`search`,
  `align_multiple`, `align_structural`). Sta nella cartella dati e viaggia con la chiavetta. La pulizia automatica
  la tratta come le risposte remote (30 giorni). In Impostazioni → Spazio l'occupazione è mostrata a parte.
- **Chiave:** SHA-256 del JSON canonico (chiavi ordinate) con operazione, versione del formato della cache e di
  BioLoom, input normalizzati, parametri e timbro delle dipendenze:

  | Operazione | Input e parametri | Timbro |
  |---|---|---|
  | ricerca | sequenza (maiuscole, senza spazi), motore, E-value, risultati massimi, taxid (solo NCBI) | BLAST+ e MMseqs2: dimensione e data di tutti i file del database (`pdbaa.*`, `pdbaa_h*`), eseguibile (percorso, dimensione, data), uso dell'indice veloce MMseqs2 |
  | multiplo | nomi e sequenze nell'ordine dato, tipo (proteina/acido nucleico), programma effettivo (`auto` risolto) | eseguibile di Clustal Omega/MAFFT/MUSCLE |
  | strutturale | SHA-256 del contenuto e formato dei due file, catene, programma effettivo | eseguibile di TM-align/US-align |

  Il nome delle strutture non entra nella chiave (un file caricato di nuovo ha un prefisso casuale): gli `ids`
  del risultato si riapplicano dopo la lettura.
- **Scadenza:** 7 giorni per RCSB e NCBI (il PDB cambia ogni settimana); per i motori locali decide il timbro del
  database e la pulizia a 30 giorni. Una voce "dal futuro" di oltre un giorno (orologio di un altro computer) non
  vale.
- **Valore:** risultato del motore e testo dei file che il calcolo ha scritto nella cartella di lavoro
  (`query.fasta`, `mmseqs_hits.m8`, `msa_input/output.fasta`, `mobile.pdb`, `target.pdb` e i file di TM-align).
  Un risultato dalla cache ricrea gli stessi file del job. Nel caso strutturale il testo del PDB sovrapposto è nel
  risultato (`superposed_pdb`) e il job lo riscrive come `superposed.pdb`. Oltre 20 MB di file, o con file non
  testuali, il risultato non entra in cache.
- **Cosa non entra:** calcoli falliti, job annullati, risultati parziali (ricerca locale senza i metadati RCSB,
  output di TM-align non riconosciuto).
- **Lettura tollerante:** un file assente, corrotto, di un altro formato, con chiave diversa o scaduto è un
  mancato riscontro, mai un errore. Anche un errore di scrittura (disco pieno) non fa fallire il calcolo. I percorsi
  dei file da ricreare passano da `safe_join` e non possono uscire dalla cartella di lavoro.
- **Concorrenza:** due calcoli identici in parallelo (pipeline) possono calcolare entrambi. La scrittura atomica
  lascia comunque una voce valida.
- **Controllo:** il parametro `use_cache` (predefinito `true`) di `/api/search`, `/api/align` e `/api/pipeline`
  corrisponde nell'interfaccia a "Ricalcola ignorando la cache": con `false` si ricalcola e la voce viene
  sostituita. L'impostazione `compute_cache` (predefinita accesa; le impostazioni precedenti la ricevono accesa)
  disattiva letture e scritture. La verifica dell'installazione ricalcola sempre (`use_cache=False`), perché deve
  eseguire davvero gli strumenti.
- **Provenienza:** un risultato riusato ha `cached: true` e `cached_at` (data ISO del calcolo), e il log del job
  riporta "Risultato dalla cache del …". Nella pipeline la data finisce nelle metriche dell'elemento
  (`search_cached_at`, badge "cache" nella tabella) e il risultato del job conta le ricerche riusate
  (`search_cache_hits`).

## Frontend (`frontend/`)

React 19 + TypeScript + Vite, nessuna libreria UI esterna: componenti propri (`components/ui.tsx`), grafici SVG
(`components/charts.tsx`: Ramachandran, profili per residuo con striscia di struttura secondaria, heatmap,
mappa delle feature UniProt), viewer 3D 3Dmol.js caricato su richiesta con rilascio del contesto WebGL.
Il client API (`api/client.ts`) e i tipi (`api/types.ts`) sono l'unico punto di contatto con il backend.
Lo stato di navigazione (job aperto, sequenza, template) vive nell'URL: ogni risultato ha un link stabile.

| Pagina | Funzione |
|---|---|
| Panoramica | flusso guidato, esempi, motori disponibili, risorse del computer |
| Sequenze | import FASTA/UniProt, proprietà ProtParam, predizione GOR, annotazioni UniProt/InterPro/PDB |
| Ricerca template | RCSB/NCBI/locale, tabella ordinabile, allineamento dell'hit, download, "modella con"; nota "dalla cache (data)" e ricalcolo ignorando la cache |
| Libreria template | download RCSB, upload PDB/mmCIF, catene, residui mancanti, ligandi, DSSP, 3D |
| Allineamenti | pairwise, multiplo, strutturale; esportazione FASTA/Clustal/PIR; salvataggio nel progetto; cache per multiplo e strutturale |
| Editor PIR | documenti PIR del progetto, generazione con MODELLER, validazione in tempo reale |
| Modellazione | input automatico o da PIR, parametri, vincoli, loop; modelli, 3D, PyMOL, archivio |
| Qualità | voto, Ramachandran, profili, clash, residui problematici evidenziati in 3D, confronto analisi |
| Pipeline batch | multi-FASTA → modelli validati, filtro zona crepuscolare, tabella paginata, CSV/ZIP; ricerche riusate dalla cache |
| Job | log e file di ogni calcolo |
| Impostazioni | schede Generale (esecuzione, QMEAN, email), Licenze (MODELLER, ProSA, PROCHECK), Database (pdbaa, MMseqs2, UniClust30), Strumenti (verifica dell'installazione), Spazio e file temporanei (con la cache dei calcoli) |
| Login | solo in modalità server, quando l'API risponde 401 |

## Sicurezza e privacy

- **Porta:** in locale è pubblicata solo su `127.0.0.1`.
- **Server:** con `BIOLOOM_AUTH=on` tutte le API (e `/docs`) richiedono la chiave d'accesso, tramite
  cookie HttpOnly firmato o header Bearer, con ritardo sui tentativi falliti.
- **Arresto dall'interfaccia:** solo con `BIOLOOM_ALLOW_SHUTDOWN=1` (preset locale).
- **Container:** gira senza root (`setpriv` all'utente proprietario di `/data`). QMEAN non ha accesso al
  socket di Docker e usa i database in sola lettura.
- **Licenze:** in `data/licenses` con permessi 0600, mascherate nelle risposte API, mai nelle immagini. Il
  messaggio d'errore di MODELLER, che ripete la chiave, viene mascherato.
- **Percorsi:** passano tutti da `safe_join` e da identificatori validati; nessun endpoint di navigazione
  del filesystem.
- **Upload:** limitati (50 MB, `BIOLOOM_MAX_UPLOAD_MB`) e validati come strutture prima dell'uso. Gli
  archivi pdbaa si estraggono con filtro `data` e solo file senza percorso.
- **Script MODELLER:** generati con valori validati (regex per residui e atomi, numeri con limiti) e
  `repr()`.
- **CSV esportati:** con neutralizzazione delle formule.
- **Privacy:** le coordinate lasciano il computer solo per QMEAN su SWISS-MODEL, scelto o come fallback, e
  sempre con il consenso esplicito all'invio. QMEAN locale, BLAST+ e MMseqs2 non usano la rete.
- **File caricati:** sequenze e allineamenti (`/api/files/sequences`) vengono solo letti e non salvati. Le
  strutture (`/api/files/structure`) vanno tra gli upload, validate e soggette alla pulizia automatica.

## Test

- **`backend/tests` (pytest), circa 150 test,** eseguiti sia nativamente sia nel container:
  - dominio, job manager, API, generazione script, MODELLER end-to-end;
  - flusso dati: scratch, salvataggio definitivo, intermedi, blocco SQLite, pulizia, gruppi di processi;
  - licenze e accesso;
  - parser di PROCHECK e ProSA tarati su output reali di 1UBQ;
  - MMseqs2 e QMEAN locale con worker simulato;
  - riconoscimento di tutti i formati di UniClust30 (`test_uniclust_discovery.py`);
  - scelta della versione e fallback su SWISS-MODEL (`test_qmean_fallback.py`);
  - lettura dei formati di sequenza e caricamento di strutture (`test_fileformats.py`);
  - cache dei calcoli (`test_compute_cache.py`): riscontri senza rieseguire i motori (simulati), invalidazione per
    parametri, sequenza, timbro del database o dello strumento, scadenza, file corrotti, job falliti e annullati,
    riuso nella pipeline e scritture concorrenti;
  - verifica dell'installazione.

  `pytest -m network` prova i servizi reali. Oltre a 1UBQ, `tests/data` contiene strutture sintetiche generate
  da `make_synthetic.py`: `ubq_dimer.pdb` (omodimero C2 con interfaccia di contatto) e `ubq_zn.pdb` (sito Zn²⁺
  su His68 e due acque).
- **`frontend/src/**/*.test.ts` (vitest):**
  - parser PIR e formattazione (compresa la nota "dalla cache");
  - logica QMEAN locale/remoto/fallback e avvisi delle licenze;
  - decodifica dei file, chiave MODELLER da file, vincoli da e verso file.
- **Esecuzione:** `scripts/test.sh` (ruff, pytest nell'immagine con MODELLER reale, typecheck e vitest). La CI
  (`.github/workflows/ci.yml`) esegue gli stessi controlli: test rapidi senza strumenti esterni, frontend, e
  immagine completa con i test al suo interno.
- **Verifica nel container:** `python -m bioloom.selftest` (o Impostazioni → Strumenti).
- **Verifica di QMEAN senza il disco reale:** mini-database creato con
  `docker/qmean/tests/make_mini_uniclust.sh` (punteggi non significativi).

## Non portato dalla v3

| Funzione v3 | Motivo |
|---|---|
| Vincoli di interfaccia PDBePISA | servizio EBI instabile; sostituibile con vincoli di distanza manuali |
| Wiki integrata e regole per agenti LLM | fuori dal perimetro dell'applicazione |
