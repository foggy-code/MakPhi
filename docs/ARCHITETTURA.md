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
                     ├─ JobManager: code con priorità "cpu" (core−1) + "io" (8) · budget di CPU condiviso · stato su SQLite · lavoro in /scratch
                     │    └─ sottoprocessi: MODELLER 10.8, mkdssp, BLAST+, MMseqs2, Clustal Omega, MAFFT, MUSCLE,
                     │                      TM-align, US-align, PROCHECK, ProSA II
                     └─ servizi remoti: RCSB · UniProt · InterPro · NCBI QBlast · AlphaFold DB (EBI) · SWISS-MODEL (opt-in)
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
  perde lavoro e lo scratch resta piccolo. Un elemento risulta `completed` solo dopo che i suoi file sono
  arrivati nella cartella definitiva: se il salvataggio non riesce (disco dati pieno) l'elemento è fallito, e
  un'interruzione prima di quel momento lo lascia non concluso. Per lo stesso motivo una pipeline fallita,
  annullata o interrotta dal riavvio, o completata con elementi falliti, si riprende senza rifare gli elementi
  conclusi (vedi [Ripresa delle pipeline](#ripresa-delle-pipeline)).
- **Archivi ZIP** (job e pipeline): vengono scritti in un file temporaneo dello scratch e inviati con
  `FileResponse`, che lo elimina dopo l'invio. Nessun archivio passa dalla memoria. Quello della pipeline contiene
  anche un report HTML per elemento, generato e scritto uno alla volta (vedi [Report del modello](#report-del-modello)).
- **SQLite:** `locking_mode=EXCLUSIVE` impostato prima del WAL, quindi nessun file `-shm`. È affidabile
  sui bind mount di Docker Desktop e su exFAT, e impedisce a una seconda istanza di aprire la stessa
  cartella dati (errore chiaro).
- **Pulizia automatica** (`services/housekeeping.py`), all'avvio, ogni 24 ore e con un pulsante:
  - upload più vecchi dei giorni impostati;
  - cache remota e cache dei calcoli oltre i 30 giorni, più i file temporanei di scritture interrotte;
  - scratch orfano.
- **Processi esterni:** ognuno ha il proprio gruppo di processi; annullamento e timeout terminano anche i
  figli (mafft e PROCHECK sono script). Il timeout dei job vale per tutti gli strumenti. I processi di calcolo
  prendono gli slot del budget di CPU condiviso fra i job (vedi [Coda con priorità e budget di
  CPU](#coda-con-priorità-e-budget-di-cpu)).
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
| `core/` | `storage` (scritture atomiche, `safe_join`, identificatori), `db` (SQLite esclusivo), `jobs` (JobManager, JobContext, scratch e salvataggio definitivo), `dispatch` (coda con priorità di ogni pool), `budget` (budget di CPU condiviso fra i job), `cache` (cache dei calcoli), `auth` (chiave d'accesso), `errors`, `container` (composizione dei servizi), `eta` (stima del tempo residuo), `events` (notifiche in-process dei cambiamenti dei job), `parallel` (passi indipendenti di un calcolo su un pool locale di thread, vedi [Prestazioni](#prestazioni)) |
| `domain/` | Logica pura e testata: `sequences` (FASTA, IUPAC, PIR, Clustal, statistiche), `structure` (PDB/mmCIF, catene, header, filtri), `ramachandran`, `clashes`, `secondary` (DSSP integrata + GOR), `geometry` (Kabsch, TM-score), `comparison` (confronto strutturale per residuo: distanze, GDT, lDDT), `grading`, `consensus` (consenso tra i metodi), `perresidue` (pLDDT, QMEAN locale e ProSA per residuo), `procheck` (lettura del .sum), `exports` (PyMOL), `plddt` (pLDDT dei modelli AlphaFold: fasce, ritaglio, riconoscimento dei file), `report` (report HTML autonomo del modello, grafici SVG, verifica del PNG), `ligands` (classificazione dei gruppi eteroatomici, colonne '.' del PIR, verifica di contatti, scontri e coordinazione dei metalli) |
| `engines/` | Adattatori: `modeller` (preparazione template, script, esecuzione), `quality` (pipeline di validazione), `procheck`, `prosa`, `qmean_local` (client del worker), `align`, `search` (RCSB, NCBI, BLAST+, MMseqs2, AlphaFold DB), `remote` (client HTTP), `tools` (rilevamento strumenti, preambolo di licenza) |
| `services/` | `projects`, `templates` (libreria), `compute` (handler dei job), `pipeline` (batch), `licenses`, `databases` (pdbaa, MMseqs2, stato UniClust), `housekeeping` (pulizia e spazio), `selftest` (verifica dell'installazione), `live` (flussi Server-Sent Events dei job), `reports` (dati del report di un modello o di un'analisi) |
| `api/` | Rotte FastAPI sottili con modelli Pydantic (`auth`, `licenses`, `databases` oltre a sistema, progetti, calcoli, job) |

### Job

Tutti i calcoli (`search`, `align`, `align2d`, `model`, `loop`, `quality`, `qmean`, `pipeline`) passano dal
`JobManager`:

- stato `queued → running → completed | failed | cancelled` (più `cancelling`) salvato su SQLite;
- coda con priorità per tipo di job e budget di CPU condiviso dai processi esterni di tutti i job (vedi [Coda con
  priorità e budget di CPU](#coda-con-priorità-e-budget-di-cpu)), con la posizione in coda `queue_position`;
- log incrementale (`GET /api/jobs/{id}/log?offset=`) con l'output dei sottoprocessi in tempo reale;
- progresso e fase aggiornati dagli handler (per MODELLER tramite marcatori `@@BIOLOOM` stampati dallo script),
  con la stima del tempo residuo `eta_seconds` e gli aggiornamenti in tempo reale via Server-Sent Events (vedi
  [Tempo residuo e aggiornamenti in tempo reale](#tempo-residuo-e-aggiornamenti-in-tempo-reale));
- annullamento immediato (i processi registrati vengono terminati) e timeout configurabile;
- al riavvio i job rimasti attivi vengono marcati come falliti (nessuno stato "fantasma"), con fase
  "Interrotto"; gli elementi di pipeline non conclusi diventano falliti con lo stesso motivo;
- lo stato finale si scrive anche se il salvataggio dei file non riesce (disco dati pieno): il job finisce
  `failed` con il motivo invece di restare `running` fino al riavvio;
- un job fallito o annullato si può rimettere in coda (`JobManager.requeue`), oggi usato dalle pipeline; da
  `completed` solo se il chiamante lo chiede esplicitamente (`statuses`), come la pipeline con elementi falliti;
- eliminazione (`delete`), ripresa e avvio sono mutuamente esclusivi: l'eliminazione aspetta (fino a 10 s) che
  l'esecuzione precedente o la preparazione di una ripresa abbiano finito, poi cancella la riga con un
  `DELETE … WHERE status IN ('completed','failed','cancelled')`. Un job ripreso nel frattempo non si elimina
  (409) e, dopo l'eliminazione, una ripresa riceve 404: niente job eliminati mentre ripartono, né cartelle
  orfane.

### Ripresa delle pipeline

`POST /api/jobs/{id}/resume` con corpo facoltativo `{"scope": "incomplete" | "failed"}`, per i job `pipeline` in
stato `failed` o `cancelled`, oppure `completed` con elementi falliti (un errore temporaneo di rete o di RCSB non
obbliga a rilanciare tutte le sequenze):

- **Ambito:** `incomplete` (predefinito) rimette in attesa tutti gli elementi non completati né filtrati
  (falliti, annullati, interrotti, in corso o in attesa); `failed` solo i falliti. In entrambi rientrano anche
  gli elementi `completed` il cui modello manca nella cartella definitiva (dati di versioni che segnavano
  l'elemento completato prima di salvarne i file). Quel controllo dei file si fa solo alla ripresa, non a ogni
  lettura del job.
- **Azzeramento:** gli elementi scelti tornano `pending` con i campi di risultato vuoti (template, identità,
  modello, metriche, errore, orari), in una transazione. La loro cartella `items/<idx>_<nome>` nei risultati
  definitivi viene eliminata (`safe_join` + `remove_tree`), così non restano file del tentativo precedente.
  Gli elementi conclusi e i loro file non si toccano.
- **Concorrenza:** il passaggio a `queued` è un solo `UPDATE … WHERE status IN (…)` sugli stati ammessi
  (`failed`, `cancelled` e, per le pipeline, `completed`) con controllo delle righe modificate: fra richieste
  concorrenti una sola riprende il job, le altre ricevono 409. Subito prima, sotto il lock del gestore, si
  ricontrolla che ci siano elementi da rifare: una richiesta arrivata dopo una ripresa già conclusa non rimette
  in coda una pipeline completata senza nulla da fare. L'azzeramento avviene dopo quel passaggio e prima
  dell'esecuzione; anche il passaggio `queued → running` è condizionato, così un job annullato in coda e poi
  ripreso (due esecuzioni in attesa nel pool) parte una volta sola. Se l'esecuzione precedente ha già scritto lo
  stato finale ma non ha ancora chiuso log e scratch, la ripresa aspetta fino a 10 s. Una ripresa da
  `completed` riparte con il progresso a zero (lo aggiorna l'esecuzione).
- **Esecuzione:** `handle()` elabora solo gli elementi `pending`; il progresso parte dagli elementi già
  elaborati ("12 di 40 sequenze elaborate") e il risultato conta tutti gli elementi. Il log del job si conserva:
  si aggiungono "Ripresa del job: …" e il riepilogo della ripresa. Una pipeline nuova ha tutti gli elementi
  `pending` e si comporta come prima.
- **Esito con elementi lasciati indietro:** una ripresa "solo i falliti" di una pipeline annullata lascia gli
  annullati come sono. In quel caso la pipeline finisce `cancelled` ("Restano N sequenze da elaborare…") e non
  `completed`, perché quelle sequenze non sono mai state elaborate; resta riprendibile per quegli elementi.
- **Errori:** job attivo → 409; job completato senza elementi falliti → 409; nessun elemento nell'ambito → 409;
  job di altro tipo → 400 `resume_unsupported`; ambito non valido → 422.
- **Interfaccia:** `GET /api/jobs/{id}` aggiunge `resumable` e `resumable_items` (`total`, `incomplete`,
  `failed`), calcolati con una sola query aggregata e solo per pipeline terminate. `JobPanel` (pagine Pipeline e
  Job) mostra "Riprendi" e, se ci sono falliti e anche altri elementi non conclusi, "Riprova solo i falliti"; per
  una pipeline completata con elementi falliti mostra solo "Riprova i falliti". `useJob` riprende il polling
  quando il job torna attivo e controlla ogni 5 s i job riprendibili, così si accorge anche di una ripresa
  avviata da un'altra scheda. Dopo una ripresa ricarica subito il job completo (i file azzerati dalla
  preparazione spariscono dall'elenco), e dopo l'annullamento di un job in coda, che si chiude subito, lo
  ricarica per mostrare i pulsanti di ripresa.
- **Nessuno stato nuovo:** non esiste uno stato `interrupted`. Un'interruzione per riavvio resta `failed` con il
  suo motivo e la ripresa vale per `failed`, `cancelled` e, con elementi falliti, `completed`. Così
  `TERMINAL`/`ACTIVE`, i filtri per stato, i client e i dati esistenti non cambiano, e lo schema SQLite resta lo
  stesso (nessuna migrazione).

### Tempo residuo e aggiornamenti in tempo reale

**Stima del tempo residuo** (`core/eta.py`, logica pura; `JobManager.estimate`). I riepiloghi dei job (elenco,
dettaglio, `/status`, annullamento, ripresa, eventi SSE) hanno `eta_seconds`, null quando non è stimabile. Nessuna
colonna nuova: si calcola dai dati già salvati.

| Job | Stima | Quando manca |
|---|---|---|
| pipeline | mediana della durata (`finished_at − started_at`) degli elementi conclusi nell'esecuzione corrente × elementi rimanenti, meno il tempo già speso da quelli in corso (al massimo la mediana ciascuno), / min(parallelismo effettivo, rimanenti) | meno di 2 elementi conclusi nell'esecuzione corrente |
| altri | t_trascorso × (1 − p) / p dal progresso p dichiarato dall'handler | p < 0,05 o meno di 10 s di esecuzione |
| in coda, in annullamento, terminati | — | sempre |

- **Esecuzione corrente:** elementi avviati dopo lo `started_at` del job, che viene riscritto a ogni avvio. Dopo
  una ripresa contano solo gli elementi rielaborati: le durate degli elementi conclusi in una sessione precedente
  (altra macchina, altra rete, cache calda) non entrano nella mediana. Contano completati, filtrati e falliti:
  anche un fallimento occupa una corsia per quel tempo.
- **Perché la mediana:** le durate hanno code lunghe (una ricerca NCBI di venti minuti, un elemento fallito in un
  secondo); la mediana ha punto di rottura del 50 % (Huber e Ronchetti, *Robust Statistics*, 2009). Lavoro residuo /
  processori è il limite inferiore classico del completamento di compiti indipendenti su processori identici
  (Graham, SIAM J. Appl. Math. 17, 1969). Togliere il tempo già speso dagli elementi in corso fa scendere la stima
  con continuità invece che a scatti.
- **Parallelismo effettivo:** lo stesso dell'esecuzione (`pipeline.effective_parallel`: quello chiesto, non oltre i
  job paralleli delle Impostazioni né oltre 8).
- **Arrotondamento:** 5 s sotto il minuto, 10 s sotto i 10 minuti, 1 min sotto l'ora, 5 min oltre; mai negativa.
  L'interfaccia mostra "circa 3 min rimanenti" (`fmtEta`) in `JobPanel` e nell'elenco dei job.

**Notifiche** (`core/events.py`). Il `JobManager` pubblica ogni cambiamento su un `ChangeFeed` in memoria: stato e
progresso (`_set_progress`, `_finish`, avvio, annullamento, ripresa), righe di log arrivate su disco (a ogni flush,
quindi al massimo ogni 0,5 s), esecuzione chiusa (`idle`), job eliminato (con il progetto). La pipeline pubblica ogni
aggiornamento di un elemento. Ogni cambiamento ha una versione crescente e resta in un buffer circolare (4096): chi
è rimasto indietro oltre il buffer lo scopre e rilegge tutto. Le coroutine dei flussi aspettano con un
`asyncio.Event` svegliato dal thread che pubblica (`loop.call_soon_threadsafe`): nessun thread occupato per client.

**Flussi SSE** (`services/live.py`, rotte in `api/jobs.py`). `text/event-stream` con `Cache-Control: no-cache` e
`X-Accel-Buffering: no`; ogni evento ha dati JSON su una riga.

| Rotta | Eventi |
|---|---|
| `GET /api/jobs/{id}/events?offset=&log=` | `status` (riepilogo con `eta_seconds`) quando cambia; `log` con `{start, offset, text}`: righe intere dal byte `offset` (come `/log?offset=`); `end` a job terminato, poi il flusso si chiude. `log=false`: solo stato. 404 se il job non esiste |
| `GET /api/jobs/events?project_id=` | `ready` (con `active`) all'apertura; `job` quando un job cambia stato, progresso, fase o stima; `items` con gli `idx` degli elementi di pipeline cambiati (null = molti); `active` (numero dei job attivi); `deleted`; `resync` se il flusso ha perso cambiamenti |

- **Fine di un job:** lo stato terminale si invia solo a esecuzione chiusa (`JobManager.busy` falso: log chiuso e
  scritto tutto), insieme alle ultime righe e a `end`. Chi ricarica il job a quel punto trova il log completo. Se la
  chiusura tarda oltre 10 s, il flusso chiude comunque con quello che c'è. Un flusso aperto su un job già terminato
  invia subito stato, log ed `end`; una ripresa riporta il job attivo e il client apre un nuovo flusso.
- **Righe intere:** `read_log(whole_lines=True)` non restituisce una riga a metà (flush a metà riga o a metà di un
  carattere multibyte): ogni riga arriva una volta sola e intera. Un log lungo arriva in più passate da 1 MB.
- **Costo:** le letture (SQLite, file di log) passano per il thread pool solo il tempo della lettura; anche senza
  notifiche lo stato si rilegge al più ogni secondo (controllo di riserva), e le notifiche ravvicinate si
  raggruppano (0,2 s). Commento `: ping` dopo 15 s senza eventi, così proxy e browser non chiudono la connessione.
- **Limiti e chiusura:** al massimo 64 flussi aperti (oltre: 503 `too_many_streams` e il client resta sul
  polling). Un flusso finisce quando il client si disconnette (`StreamingResponse` annulla il generatore e il posto
  si libera), a fine job, dopo 30 minuti (evento `reconnect`: il client si riconnette subito dall'offset e l'accesso
  viene ricontrollato) e all'arresto del server (`Core.shutting_down`: `should_exit` di uvicorn o JobManager
  chiuso). Senza quest'ultimo controllo uvicorn aspetterebbe all'infinito le connessioni aperte e `docker stop`
  finirebbe con SIGKILL; misurato: arresto in 1,4 s con un flusso aperto.
- **Accesso:** il middleware di `app.py` controlla cookie o Bearer anche sulle rotte di streaming, prima che il
  flusso parta (401). `EventSource` invia i cookie della stessa origine; gli script usano `Authorization: Bearer`.

**Frontend** (`lib/live.ts`, logica pura testata con flussi e timer finti; `lib/hooks.ts`).

- `useJob` apre il flusso del job (`LiveFeed`) quando il job è attivo, la scheda è visibile e il caricamento
  completo è fatto, dall'offset del log appena letto; lo riapre dopo ogni caricamento completo (ripresa). Gli
  eventi `status` passano da `recordStatus` come il polling: stesse regole di ricarica di F2.1. `sliceLogChunk`
  accoda solo i byte nuovi (sovrapposizioni con un polling concorrente tagliate in byte UTF-8, buchi ignorati).
  Il polling, a sua volta, scarta la risposta di `/log` se nel frattempo il flusso ha fatto avanzare l'offset
  (`polledLogChunk`: flusso riaperto mentre una richiesta era in corso), così nessuna riga compare due volte.
- **Ricaduta sul polling:** senza `EventSource`, con la scheda nascosta o con il flusso caduto (errore, 401, 503,
  nessun evento entro 5 s) torna il polling di prima (`jobPollInterval`), mentre il flusso riprova con backoff
  1, 2, 4… fino a 30 s; il primo evento ricevuto azzera il backoff. Il controllo lento dei job terminati ma
  riprendibili (5 s) resta com'era.
- **Flusso dell'elenco condiviso:** uno per scheda (`FeedHub`), usato dal contatore della barra laterale, dalla
  pagina Job, dalle pipeline recenti e dai job recenti della Panoramica (`useLiveJobList`: aggiornamenti sul posto
  con `mergeJob`, ricarica per job nuovi o eliminati; sopra ogni elenco ricaricato si riapplicano i riepiloghi del
  flusso più recenti della lettura, per `updated_at`, con `RecentJobs`: una ricarica partita prima della fine di un
  job e arrivata dopo non lo riporta "in esecuzione") e dalla tabella della Pipeline (ricarica al più una volta al
  secondo sugli eventi `items` del job). Ognuno sospende il proprio polling mentre il flusso è attivo e ricarica a
  ogni `ready` (dopo una riconnessione). CPU, memoria e disco della Panoramica restano periodici.
- **Connessioni:** un browser tiene al massimo 6 connessioni HTTP/1.1 per server, contando tutte le schede: per
  questo l'elenco è condiviso e le schede nascoste chiudono i flussi e li riaprono quando tornano visibili. Una
  scheda ne usa al massimo due (elenco e job aperto; tre sulla pagina Qualità con QMEAN e analisi insieme).

### Coda con priorità e budget di CPU

**Coda** (`core/dispatch.py`). Ogni pool ha un dispatcher con una coda ordinata al posto del `ThreadPoolExecutor`
FIFO: "cpu" con i job di calcolo in parallelo delle Impostazioni (`effective_workers`, core − 1 se automatico), "io"
con 8. I worker prendono sempre il primo job avviabile in quest'ordine (`core/jobs.PRIORITY`):

| Priorità | Tipi di job | Perché |
|---|---|---|
| 0 | `search`, `align`, `align2d`, `quality`, `qmean` (e `compare`) | interattivi brevi: l'utente aspetta davanti allo schermo |
| 1 | `model`, `loop`, `selftest` e i tipi non elencati | minuti |
| 2 | `pipeline`, `db_update_pdbaa`, `db_build_mmseqs`, `db_index_mmseqs`, `db_convert_uniclust`, `db_local_uniclust` | lavori lunghi in blocco |

- **A parità:** `created_at`, poi il rowid della riga (ordine di creazione anche nello stesso secondo). Un job ripreso
  rientra con la sua data di creazione e passa davanti ai job creati dopo di lui.
- **Invecchiamento** (`dispatch.AGING_SECONDS` = 300 s): la priorità effettiva di un job in coda migliora di un
  livello ogni 5 minuti di attesa, fino a 0. Senza, un flusso continuo di job interattivi lasciava una pipeline in
  coda per sempre. 5 minuti sono più di un calcolo interattivo tipico (ricerca, allineamento, qualità: da secondi a
  pochi minuti), quindi le richieste di chi aspetta davanti allo schermo passano ancora davanti; con un flusso
  continuo una modellazione parte dopo al più 5 minuti e una pipeline dopo al più 10, più i job creati prima di lei.
  A parità di priorità effettiva vale la creazione: un job della stessa priorità arrivato dopo non sorpassa chi
  aspetta da prima, perché ha atteso di meno. L'ordine si calcola a ogni scelta (e per `queue_position`), non
  all'ingresso in coda.
- **Nessuna colonna nuova:** la priorità si ricava dal tipo; lo schema SQLite non cambia. La coda sta in memoria e lo
  stato vero resta su SQLite: una voce della coda dice solo "prova ad avviare". `_execute` parte soltanto da `queued`,
  con il passaggio condizionato di prima, quindi un tentativo rimasto indietro non avvia nulla due volte. Un job ha al
  più una voce in coda.
- **Compatibilità:** annullare un job in coda scrive lo stato finale e lo toglie dalla coda nello stesso passo, sotto
  il lock del gestore, così una ripresa successiva non può vedersi togliere la voce nuova. La ripresa lo rimette in
  coda a preparazione finita. All'arresto la coda si svuota e il `recover` del prossimo avvio marca i job rimasti
  `queued`, come prima.
- **Eventi:** quelli di prima (`job` all'ingresso in coda, all'avvio, a ogni fase e alla fine; `idle` a esecuzione
  chiusa); ETA invariata.
- **Posizione:** `queue_position` in tutti i riepiloghi (elenco, dettaglio, `/status`, annullamento, ripresa, SSE). È
  la posizione da 1 nella coda del pool, null per chi non è in coda (anche una ripresa in preparazione). I flussi SSE
  la contano nella firma del riepilogo: quando parte il job davanti, il cambio arriva come `job` o `status`.
  L'interfaccia mostra "2° in coda" in `JobPanel` e nell'elenco dei job (`queueLabel` in `lib/format.ts`).
- **Capacità dinamica:** pool "cpu" e budget rileggono `effective_workers` a ogni scelta. `PUT /api/settings` con
  `max_workers` li risveglia subito (`JobManager.reconfigure`): l'impostazione vale senza riavvio.
- **Risorse esclusive** (`register(resource=...)`): il servizio QMEAN locale (`QMEAN_LOCAL`) esegue al massimo un
  calcolo alla volta; lo chiedono i job `qmean` in modalità locale e le conversioni e copie di UniClust30, che nel
  servizio passano dalla stessa coda. Un job con la risorsa occupata resta `queued`, con la sua posizione e la fase
  "In coda: il servizio QMEAN locale sta eseguendo un altro calcolo", e il worker passa al successivo: il pool "io"
  continua a servire gli altri job. La risorsa si calcola al momento della scelta: `mode` del job o delle
  Impostazioni; un QMEAN su SWISS-MODEL non la chiede, e nemmeno uno in modalità locale senza servizio QMEAN
  (`BIOLOOM_QMEAN_URL` assente: l'avviatore non ha trovato UniClust30), che fallisce subito o va al fallback. Si tiene
  fino alla chiusura dell'esecuzione, salvo che il job la restituisca prima con `JobContext.release_resource` (il
  dispatcher gliela passa all'avvio): `handle_qmean` lo fa appena finisce la parte locale, riuscita o no, così un
  fallback su SWISS-MODEL (minuti di attesa) non tiene in coda il QMEAN locale successivo, e più fallback partono
  insieme. Restituita, la risorsa non si libera una seconda volta alla chiusura (quella presa nel frattempo da un
  altro job resta sua). Prima i job QMEAN restavano `running` in attesa della coda del worker e consumavano il loro
  timeout.

**Budget di CPU** (`core/budget.py`). Senza coordinamento, pipeline × elementi in parallelo × strumenti saturavano la
CPU. Gli slot sono `effective_workers`, condivisi da tutti i job. `JobContext.run(cpu=...)` li prende prima di avviare
il processo e li restituisce quando termina: `True` (predefinito) = 1, un numero per i programmi multi-thread,
`False` per i processi leggeri o di I/O.

| Processo | Slot |
|---|---|
| MODELLER (allineamento, modelli, loop, DOPE) | 1: gli script non usano `parallel.Job`, un thread |
| PROCHECK, ProSA II, TM-align, US-align, `blastp` (`-num_threads` predefinito 1), Clustal Omega (`--threads` predefinito 1), MAFFT (`--thread` predefinito 0 = un thread), MUSCLE 3, `mmseqs createdb` | 1 |
| MMseqs2 `easy-search` e `createindex`, MUSCLE 5 (di suo userebbe tutti i processori) | da 1 a core − 1, uno per thread: al suo turno il processo prende gli slot liberi e parte con altrettanti thread (`--threads`/`-threads`) |
| `blastdbcmd -entry all` (estrazione di pdbaa per MMseqs2) | 0: I/O |
| mkdssp (da Biopython, non da `ctx.run`, meno di un secondo), servizio QMEAN (container a parte), download | fuori dal budget |

- **Multi-thread:** un programma multi-thread occupa uno slot per thread, e i suoi thread li decidono gli slot che
  ottiene. Il comando si costruisce dal numero di thread (`ctx.run(lambda n: [...], cpu=N)`) e la richiesta è
  elastica: al suo turno prende da 1 a N slot, quelli liberi. Da solo resta veloce, insieme agli altri non satura, e
  non tiene ferma la coda aspettando che se ne liberino N (con richieste fisse di N slot una ricerca MMseqs2 in una
  pipeline avrebbe aspettato la fine di tutte le modellazioni in corso, bloccando anche quelle dietro di lei). Un
  comando fisso con `cpu=N` aspetta invece tutti gli N slot (`ctx.cpu_threads` limita N alla capacità).
- **Ordine:** priorità del job, poi arrivo. Niente sorpassi: il primo in ordine parte appena ha abbastanza slot, e
  chi viene dopo aspetta anche se gli basterebbero. Così una richiesta fissa di N slot non resta indietro per sempre
  dietro a quelle da uno.
- **Invecchiamento** (`budget.AGING_SECONDS` = 60 s): la priorità effettiva di una richiesta migliora di un livello
  ogni minuto di attesa, fino a 0, con le stesse regole della coda (a parità vale l'arrivo). I processi dei calcoli
  interattivi durano pochi secondi (DOPE, PROCHECK, ProSA, TM-align) o meno di un minuto (BLAST+ e MMseqs2 locali):
  un'analisi lanciata dall'utente passa ancora davanti, ma con un flusso continuo di processi interattivi un processo
  di modellazione aspetta al più 1 minuto e uno di pipeline al più 2 prima di contare solo l'arrivo. Chi aspetta
  riesamina il proprio turno a ogni rilascio e comunque ogni secondo, quindi l'invecchiamento vale anche senza
  risvegli.
- **Nessun deadlock:**
  - gli slot li tengono solo processi in esecuzione, che finiscono da soli, per timeout o per annullamento;
  - la pipeline non ne prende mentre aspetta i suoi elementi: li prendono i processi degli elementi;
  - ogni richiesta è limitata alla capacità, quindi si può sempre soddisfare.

  Verificato con una pipeline di 4 elementi in parallelo su 2 slot, con passi in parallelo dentro ogni elemento.
- **Attesa:** si interrompe con l'annullamento (`cancel` sveglia subito chi aspetta: il processo non parte) e con
  l'arresto. Non conta nel timeout del processo, che parte con il processo.
- **Fase:** "In attesa di CPU libera" al posto della fase del job se aspetta un suo processo (anche di un contesto
  figlio senza fase propria, come l'analisi di qualità dentro la modellazione) e nessun altro suo processo è in
  esecuzione. Altrimenti l'attesa compare in coda alla fase: passi in parallelo ("Validazione · in attesa di CPU
  libera": il job non è fermo) ed elementi di pipeline ("3 di 5 sequenze elaborate · in attesa di CPU libera"), così
  le fasi della pipeline restano. Finita l'attesa torna la fase dichiarata dall'handler.
- **Thread dello stesso job:** i passi eseguiti in parallelo da thread diversi che chiamano `ctx.run` (validazione in
  parallelo) sono governati allo stesso modo, senza codice apposito.
- **Diagnostica:** `/api/system/stats` → `cpu_slots` (totali, usati, processi in attesa), nella Panoramica.

### MODELLER

Il server non importa mai `modeller`: ogni operazione è uno script generato ed eseguito in un
sottoprocesso. Nel container è MODELLER 10.8 del pacchetto .deb; in esecuzione nativa di sviluppo si può
indicare un altro interprete (`BIOLOOM_MODELLER_PYTHON`). Flusso di un job `model`:

1. **Template**: conversione in PDB pulito (catene scelte, solo i ligandi scelti, niente acqua, MSE→MET; vedi
   [Ligandi, cofattori e metalli](#ligandi-cofattori-e-metalli-domainligandspy)).
2. **Allineamento**: se non viene fornito un PIR, MODELLER stesso lo calcola (`align2d` con un template,
   `salign` strutturale + allineamento sul profilo con più template). Il PIR fornito dall'utente viene validato
   (lunghezze, catene, caratteri) e riallineato ai codici/file dei template preparati. In entrambi i casi le colonne
   '.' dei ligandi si riscrivono secondo la scelta delle opzioni.
3. **AutoModel / DOPELoopModel** con raffinamento (`very_fast`/`fast`/`normal`/`thorough`), vincoli di
   struttura secondaria e di distanza, regioni di loop, seme casuale. I risultati (`molpdf`, DOPE, GA341,
   zDOPE) sono scritti in `models.json` direttamente da `a.outputs`.
4. **Selezione e qualità**: modello migliore = DOPE minimo; analisi completa sul migliore (o su tutti, fino a 3
   modelli insieme), con righe `REMARK 999 BIOLOOM` di tracciabilità nel PDB.

Nella pipeline un template usato da più elementi si prepara una volta per esecuzione e gli altri ne ricevono una copia
(vedi [Prestazioni](#prestazioni)).

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
| Ligandi e metalli | campo `ligands` solo se la struttura ha gruppi eteroatomici non d'acqua: contatti, scontri, coordinazione dei metalli (`domain/ligands.py`, vedi [Ligandi, cofattori e metalli](#ligandi-cofattori-e-metalli-domainligandspy)); nessun effetto sul voto |

**Esecuzione dei passi** (`quality.analyze`, `core/parallel.Steps`). La struttura si legge una volta; poi partono insieme
i sottoprocessi (DOPE di MODELLER, mkdssp, PROCHECK, ProSA) e i calcoli Python (Ramachandran, clash, SASA; il DSSP
integrato se mkdssp manca), su un pool di thread del singolo calcolo. QMEAN resta un job a parte (memoria).

- **Stesso rapporto della versione seriale:** gli esiti si leggono e gli avvisi si aggiungono nell'ordine di prima
  (Ramachandran, clash, struttura secondaria, SASA, DOPE, PROCHECK, ProSA), qualunque sia l'ordine in cui i passi
  finiscono; `analyze(parallel=False)` esegue gli stessi passi uno dopo l'altro e i test confrontano i due rapporti.
  I passi leggono lo stesso modello di Biopython senza modificarlo: la SASA e la lettura dell'uscita di mkdssp (che
  registra i risultati sui residui e sceglie le varianti dei residui disordinati) lavorano su una copia.
- **Processi:** tutti da `JobContext.run` (budget di CPU del JobManager, annullamento, timeout), ognuno con un contesto
  figlio: le righe di log di un passo hanno il suo prefisso (`[DOPE] …`, `[PROCHECK] …`) e cartelle proprie (`dope/` o
  la cartella del modello, `procheck/`, `prosa/`, temporanea per mkdssp). mkdssp non è più lanciato da Biopython: lo
  stesso comando (`mkdssp --output-format=dssp`, dalla versione 4) scrive su file e Biopython legge l'uscita
  (`DSSP(..., file_type="DSSP")`); la versione di mkdssp si chiede una volta per eseguibile.
- **Errori e annullamento:** un passo che fallisce dà lo stesso avviso di prima e gli altri continuano; un errore
  inatteso fa fallire l'analisi come prima. All'annullamento il JobManager termina i processi, i passi in coda non
  partono e l'analisi aspetta la fine di quelli in corso prima di uscire.
- **Log:** a fine analisi una riga con la durata dei passi, per esempio "Analisi di qualità in 1.14 s (passi in
  parallelo: lettura 0.01, Ramachandran 0.20, clash 0.20, mkdssp 0.25, SASA 0.16, DOPE 1.10, PROCHECK 0.36, ProSA
  0.10 s)". Con i passi insieme ognuno dura un po' di più (GIL, core condivisi): conta il totale.
- **Più modelli** (qualità "Su tutti i modelli"): `run_modeling` ne analizza fino a 3 insieme (non oltre i job
  paralleli delle Impostazioni), ognuno con il proprio contesto; metriche, rapporti e REMARK restano nell'ordine dei
  modelli, e il primo modello fallito in quell'ordine fa fallire il job come prima.

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
    - **Controllo di BioLoom prima di partire** (`qmean_local.memory_problem`, nello stato: `available`, `message`,
      `memory_required`): con `swap` il calcolo partiva e, come nella misura sopra, di solito si fermava a metà per il
      timeout. Ora la memoria del container (`memory.total` del worker, senza lo swap: il cs219 va tenuto nella cache)
      si confronta con il requisito del database scelto; se non basta, il job fallisce subito con il motivo in italiano
      (quanta memoria serve, quanta ce n'è, come aumentarla) oppure passa al fallback su SWISS-MODEL, se attivo.

      | Caso | Requisito | Fonte |
      |---|---|---|
      | UniRef30_2020_06 | 12 GB | misurato: 3,8 GB di indici in memoria + 6 GB di cs219 + 1 GB + 10% (README) |
      | altre versioni | 12 GB × dimensione / 194 GB × 1,5, arrotondato al GB superiore, minimo 2 GB | stima: memoria e dimensione crescono insieme con il numero di cluster, ma le versioni vecchie hanno allineamenti meno profondi e quindi più indice e cs219 per GB; da qui il 50% in più |

      - Il giudizio del worker vale per primo: `insufficient` blocca sempre; `ok` (calcolato sulle dimensioni vere
        degli indici, con il 10% di margine) lascia partire anche quando la stima prudente direbbe di no.
      - Tolleranza: si accetta il 95% del requisito, perché `/proc/meminfo` vede un po' meno della memoria assegnata
        (con 12 GB per Docker circa 11,7 GB).
      - Un worker di una versione che non riporta la memoria non viene controllato.
      - Nello stato `memory_required.sufficient` riporta l'esito (`memory_sufficient`: true, false o null se non si
        sa), e Impostazioni → Database lo scrive accanto al requisito (`qmeanMemoryNote`): "Memoria di Docker:
        11,7 GB; richiesti circa 12 GB (valore misurato): sufficiente…". Con il giudizio `ok` del worker e una stima
        più alta della memoria la nota non mostra la stima ma "sufficiente secondo il servizio QMEAN", che misura le
        dimensioni vere degli indici: prima compariva "richiesti circa 14 GB" accanto a un servizio disponibile.
      - Esempio: UniRef30_2020_06 con 8 GB per Docker → "memoria insufficiente per UniRef30_2020_06: servono circa
        12 GB assegnati a Docker (valore misurato) e il servizio QMEAN ne ha 8,0…"; con 11,7 GB parte.
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

### Consenso tra i metodi e qualità per residuo (`domain/consensus.py`, `domain/perresidue.py`)

Il voto A–D non cambia: `consensus` è un campo in più del rapporto di qualità.

- **Scale:** ogni metodo è portato su 0–100 con una funzione lineare a tratti (saturata fuori dai punti). I punti
  mettono la soglia "affidabile" della letteratura a 70 e quella "problematica" a 40, così le fasce (buona ≥ 70,
  intermedia 40–70, scarsa < 40) valgono per tutti i metodi e per il consenso.

  | Metodo | Punti (valore → punteggio) | Peso | Fonte |
  |---|---|---|---|
  | Voto BioLoom | 0 → 0, 55 → 40, 70 → 70, 100 → 100 | 1 | soglie del voto (D sotto 55, B da 70) |
  | z-DOPE | −2 → 100, −1 → 70, 0 → 40, 2 → 0 | 1 | Shen & Sali 2006; manuale MODELLER: sotto −1 simile a strutture native, positivo probabilmente scarso |
  | GA341 | 0 → 0, 0,7 → 70, 1 → 100 | 0,5 | Melo et al. 2002; John & Sali 2003: ripiegamento affidabile sopra 0,7 |
  | QMEAN4 Z-score | −8 → 0, −4 → 40, −2 → 70, 0 → 100 | 1,5 | Benkert et al. 2011; SWISS-MODEL: −4 o meno bassa qualità, vicino a 0 ottimo |
  | ProSA z-score | 2 → 0, −2 → 40, −6 → 70, −8 → 100 | 1 | Sippl 1993; Wiederstein & Sippl 2007: positivo o vicino a zero = errore (−0,60 "troppo alto"), −8,29 nativo; −6 scelta BioLoom |
  | PROCHECK core | 50 → 0, 70 → 40, 90 → 70, 100 → 100 | 0,75 | Laskowski et al. 1993: oltre il 90% atteso a 2 Å o meglio; 70% scelta BioLoom |

  Pesi: QMEAN combina quattro potenziali ed è già riferito a strutture sperimentali di pari dimensione; GA341 valuta
  solo il ripiegamento e, contenendo l'identità con il template, satura vicino a 1 per quasi tutti i modelli per
  omologia; PROCHECK misura i soli diedri dello scheletro, già nel voto. Il voto contiene una penalità zDOPE (al
  massimo 20 punti, solo sopra −1): la sovrapposizione con z-DOPE è parziale e accettata. L'intervallo nativo di
  ProSA dipende dalla lunghezza (meno negativo per le catene corte): una catena nativa sotto i 100 residui può
  risultare "intermedia".
- **Punteggio:** media pesata dei soli metodi disponibili, con numero di metodi usati, metodi mancanti (con il
  motivo) e fascia. Con un solo metodo il rapporto lo avverte.
- **Discordanze:** ogni coppia con un metodo in fascia buona e uno in fascia scarsa, con una frase che dice che
  cosa misura ciascuno (ambiti geometria, energia, ripiegamento) e come leggere la differenza.
- **Consenso per residuo:** segnali allineati a `report.residues`:

  | Segnale | Regola |
  |---|---|
  | Ramachandran | outlier |
  | clash | almeno un contatto grave |
  | DOPE | profilo per residuo oltre media + 1 DS del profilo della stessa struttura. Il profilo è già una media mobile calcolata da MODELLER (`assess_dope(smoothing_window=15)`, come nel tutorial; l'intestazione di `dope.profile` dichiara una finestra effettiva di 13 residui) e non si smussa di nuovo. Non ha una soglia assoluta: se ne leggono i picchi |
  | QMEAN | punteggio locale sotto 0,6 (SWISS-MODEL); indice 1-based per catena, escluso con avviso se il numero di residui non corrisponde |
  | ProSA | energia sulla finestra di 10 residui sopra 0; indice sequenziale, escluso con avviso se non corrisponde |

  Regioni problematiche: almeno 3 residui segnalati da almeno 2 metodi, con un residuo non segnalato tollerato
  tra due segnalati (anche più volte nello stesso tratto). Un tratto non attraversa catene né interruzioni della
  numerazione. Testo del tipo "DOPE e Ramachandran segnalano 40-52 (catena A)", con gli indici per il viewer.
- **Dove si calcola:**
  - `quality.analyze` con ciò che l'analisi ha (niente QMEAN né GA341);
  - `run_modeling` lo ricalcola con GA341 e zDOPE di MODELLER prima di salvare `<modello>.quality.json`;
  - `GET /api/jobs/{id}/consensus?qmean_job=` per un job `quality` (rapporto nel risultato) e
    `GET /api/jobs/{id}/consensus/{modello}?qmean_job=` per i modelli di job `model`, `loop` e `pipeline`
    (rapporto nel file). Sono due rotte perché i due rapporti stanno in posti diversi. Entrambe leggono GA341 e
    zDOPE dal `models.json` accanto al modello, se la struttura è un modello salvato da un job: questo vale
    anche per un'analisi di qualità lanciata su quel modello. Con `qmean_job` aggiungono Z-score e punteggi
    locali di un job QMEAN completato.
- **Controlli della rotta:** la sorgente del job QMEAN (nei parametri: nel risultato `source` è il servizio) deve
  indicare la stessa struttura. Si confrontano template normalizzato, job + file, nome del file caricato e, quando
  entrambi i job la hanno, l'impronta del file analizzato (`source_sha256`, SHA-256 del contenuto, nei risultati
  dei job `quality` e `qmean`): un template sostituito con lo stesso id tra l'analisi e QMEAN è un'altra struttura.
  I job precedenti, senza impronta, si confrontano solo per sorgente. Una sorgente diversa dà 400
  `qmean_source_mismatch` e non viene esclusa in silenzio, perché i punteggi di un'altra struttura renderebbero il
  consenso fuorviante. Un job che non è QMEAN dà 400 `qmean_job_invalid`, un QMEAN non completato 409. I rapporti
  delle versioni precedenti, senza consenso, funzionano: la rotta lo calcola.
- **pLDDT (`perresidue.bfactor_kind`):** `report.structure.bfactor_kind = "plddt"` se il file è un modello
  AlphaFold secondo `plddt.is_alphafold_model`, l'unico criterio di BioLoom, lo stesso della libreria dei template
  e della copia preparata per MODELLER (vedi [Modelli AlphaFold DB](#modelli-alphafold-db)): segni certi di AlphaFold
  DB nell'intestazione, oppure il nome `AF-<ACC>-F<n>` senza un'altra origine dichiarata, e valori in 0–100 non tutti
  nulli.

  I B-factor cristallografici stanno spesso in 0–100 (1UBQ: massimo 42,75): per questo la sola scala non basta.
  Una menzione di AlphaFold altrove nell'intestazione non conta: molte strutture sperimentali sono risolte per
  sostituzione molecolare a partire da un modello AlphaFold (`REMARK 200 STARTING MODEL: ALPHAFOLD`,
  `_pdbx_initial_refinement_model.source_name AlphaFold`) e i loro B-factor restano B-factor.
- **Valori su 1UBQ** (uscite reali di MODELLER, PROCHECK e ProSA in `tests/data`):

  | Metodo | Valore | Punteggio |
  |---|---|---|
  | voto | 98,9 | 98,9 |
  | zDOPE | −2,73 | 100 |
  | ProSA | −9,26 | 100 |
  | PROCHECK core | 95,5% | 86,5 |
  | **consenso** | | **97,0, fascia buona** |

  Nessuna discordanza e nessuna regione problematica. Segnali isolati:
  - un clash su Arg72;
  - il picco DOPE sulla coda C-terminale flessibile (73–76; soglia −0,0355).

### Report del modello

`domain/report.py` (HTML e grafici, logica pura) e `services/reports.py` (raccolta dei dati): un file HTML autonomo,
stampabile in PDF dal browser, generato al volo dai file già salvati del job, senza ricalcoli.

- **Rotte:**
  - `GET /api/jobs/{id}/report/{modello}` per i job `model`, `loop` e `pipeline` (per la pipeline
    `items/<idx>_<nome>/<modello>` di un elemento completato);
  - `GET /api/jobs/{id}/report` per un job `quality`: report ridotto, senza template, allineamento e parametri;
  - le varianti `POST`, con corpo `{"image_png_base64"}`, incorporano la vista 3D;
  - `qmean_job=` aggiunge QMEAN con gli stessi controlli della rotta del consenso; `download=false` restituisce il
    file `inline` invece che `attachment`.

  Nome del file: `report_<modello>.html` o `report_qualita_<struttura>.html`, solo `[A-Za-z0-9_.-]`.
- **Errori:** modello non tra quelli riusciti del job, file che non è un modello, elemento con un altro nome → 404; job
  o elemento non completato → 409; nessun modello per un job model/loop/pipeline → 400 `report_model_required`; job di
  altro tipo, o un file per un'analisi di qualità → 400 `report_unsupported`; immagine non PNG → 400
  `report_image_invalid`; oltre 3 MB → 413 `report_image_too_large`.
- **Dati:**

  | Sezione | Da dove |
  |---|---|
  | Intestazione | job (titolo, progetto, date); versione di BioLoom; MODELLER dall'`EXPDTA THEORETICAL MODEL, MODELLER 10.8` del modello; metodo DSSP, PROCHECK e ProSA dal rapporto; QMEAN (versione, metodo, locale o SWISS-MODEL) dal job qmean |
  | Bersaglio | voce `sequence` del PIR (catene separate da '/'); pipeline: riga dell'elemento; loop e quality: residui del rapporto |
  | Template | job model: template preparati del risultato e metadati della libreria (sorgente, titolo, metodo, risoluzione, pLDDT medio); pipeline: voci del PIR, copia preparata nella cartella dell'elemento (catene, intervallo, `REMARK 999 BIOLOOM PLDDT_TRIM`), righe `TEMPLATE … ALPHAFOLD` del modello e ricerca (motore, identità, copertura, E-value, zona crepuscolare) |
  | Identità e copertura | ricalcolate sull'allineamento usato da MODELLER: residui identici / colonne con un residuo in entrambe le righe, come `validate_pir` e il `% SEQ ID` di MODELLER (ubiquitina di lievito su 1UBQ: 96,05%); copertura = quelle colonne / residui del bersaglio |
  | Allineamento | PIR del risultato o `alignment.ali` dell'elemento, a blocchi di 60 colonne: righello, primo e ultimo residuo di ogni riga, residui identici al bersaglio evidenziati, riga di conservazione Clustal (`sequences.consensus_line`) |
  | Parametri | `options` del risultato; pipeline: `parse_options` dei parametri più motore, E-value e soglie della zona crepuscolare; loop: regione, modelli e dinamica |
  | Modelli | risultato del job o `models.json` dell'elemento; voto solo per i modelli analizzati |
  | Qualità | `<modello>.quality.json` (o risultato del job quality); consenso ricomposto con GA341 e zDOPE del modello (`consensus.compute`), oppure dalla rotta del consenso quando c'è QMEAN |
  | Ligandi e metalli | campo `ligands` del rapporto di qualità; per il modello migliore senza analisi, `ligands.verification` del risultato. Sezione solo se presente: esito, classe, coordinazione con i donatori e le distanze attese, residui entro 4 Å, confronto con il sito del template, motivazione; nei parametri i gruppi trasferiti e da quale template |

  Un modello senza rapporto di qualità ha il report dei soli risultati di MODELLER, con un avviso.
- **Grafici SVG** (generati dal backend, XML ben formato):
  - Ramachandran: un pannello per classe, come nell'interfaccia (generale con la pre-prolina, glicina, prolina), con i
    rettangoli favoriti/consentiti di `domain/ramachandran` e un cerchio per residuo con φ e ψ (74 su 76 in 1UBQ); gli
    outlier sono sopra gli altri punti ed etichettati;
  - profili per residuo: DOPE (media mobile di MODELLER, soglia media + 1 DS del consenso: picco 73–76 su 1UBQ), QMEAN
    locale (sotto 0,6), energia ProSA sulla finestra di 10 residui (sopra 0) e pLDDT (linee a 50, 70 e 90). La linea
    si interrompe tra catene e dove la numerazione salta;
  - striscia della struttura secondaria a 3 stati.
- **Legenda e fonti:** solo le metriche presenti, con le soglie usate da BioLoom, e solo le fonti dei metodi presenti
  (Šali & Blundell 1993, Shen & Sali 2006, Melo et al. 2002, John & Sali 2003, Benkert et al. 2011, Sippl 1993,
  Wiederstein & Sippl 2007, Laskowski et al. 1993, Jumper et al. 2021, Tunyasuvunakool et al. 2021, Mariani et al.
  2013, Ramachandran et al. 1963, Lovell et al. 2003, Word et al. 1999, Kabsch & Sander 1983, Shrake & Rupley 1973,
  Tien et al. 2013, Rost 1999).
- **Stampa:** `@page` A4 e regole `@media print`: niente indice, intestazioni delle tabelle ripetute, nessuna
  interruzione dentro grafici, blocchi di allineamento, riquadri e righe, allineamento e qualità da una pagina nuova,
  colori conservati (`print-color-adjust: exact`).
- **Immagine 3D:**
  - `Viewer3D` espone `handle.pngDataUri()` (3Dmol `pngURI`), che risponde solo con la struttura disegnata e nessun
    caricamento in corso. Il frontend la invia solo per il modello mostrato dal viewer (in Modellazione il modello
    selezionato, in Qualità con la scheda 3D aperta) e solo se è un PNG entro 3 MB (`lib/report.ts`); altrimenti il
    report si genera senza immagine e il motivo compare nell'avviso del salvataggio riuscito ("Report scaricato senza
    la vista 3D: …", `reportSavedMessage`), non prima della richiesta: un report che poi fallisce mostra solo
    l'errore. In Pipeline il pulsante Report c'è in ogni riga completata (report dell'elemento anche senza analisi di
    qualità) e nella finestra «Visualizza», che incorpora la vista corrente.
  - `decode_png` accetta base64 o un data URI `image/png` e controlla la lunghezza prima di decodificare. Poi verifica
    firma, blocchi (lunghezza, tipo, CRC-32), IHDR (lati fino a 16384, profondità ammessa per il tipo di colore),
    almeno un IDAT, IEND finale e nessun dato dopo. Il data URI del report è ricodificato dai byte verificati.
- **Sicurezza:**
  - tutti i testi passano da `html.escape`, virgolette comprese: nomi di sequenze e di file, titoli e metadati
    RCSB/UniProt/AlphaFold, nomi di residui e atomi dei file caricati;
  - gli attributi contengono solo costanti e numeri, nessun URL viene da dati in ingresso, nessuno script;
  - `Content-Security-Policy: default-src 'none'; img-src data:; style-src 'unsafe-inline'` sia nel documento (vale
    anche aperto da disco) sia nella risposta, con `X-Content-Type-Options: nosniff` e `Cache-Control: no-store`.
- **Pipeline:** `export_zip` aggiunge `report/<nome>.html` per ogni elemento completato (senza immagine), generati e
  scritti uno alla volta: in memoria c'è al più un report. Un report che non si può generare finisce in
  `report/non_generati.txt` e non ferma l'esportazione. Il LEGGIMI dello ZIP descrive la cartella.
  - **Nomi:** `modelli/<nome>.pdb`, `qualita/<nome>.quality.json` e `report/<nome>.html` usano lo stesso nome
    dell'elemento, unico nell'archivio: il report sta sempre accanto al suo modello. I nomi vengono da
    `sequences.sanitize_code` (solo `[A-Za-z0-9_-]`); la deduplicazione registra anche i nomi generati ("x", "x",
    "x_2" → "x", "x_2", "x_2_2"). Un nome ripetuto in un job di una versione precedente diventa nello ZIP quello della
    cartella dell'elemento (`0002_x_2`). Prima il report passava da `safe_filename`, che toglie i '-' ai bordi: "abc"
    e "abc-" davano due voci `report/abc.html` e all'estrazione una copriva l'altra.

### Allineamenti

- **Pairwise**: Biopython `PairwiseAligner` (NW/SW, BLOSUM/PAM/GONNET, NUC.4.4), gap terminali gratuiti nel globale.
- **Multiplo**: Clustal Omega → MAFFT → MUSCLE se installati, altrimenti *center-star* integrato; matrice di identità.
- **Strutturale**: US-align/TM-align se installati, altrimenti sovrapposizione di Kabsch con ricerca del
  TM-score massimo (algoritmo del programma TM-score) su un allineamento di sequenza; PDB sovrapposto per il viewer e
  confronto per residuo (vedi sotto).

### Confronto strutturale (`domain/comparison.py`, `engines/align.structural`)

B si sovrappone su A, che è il **riferimento**: GDT e lDDT si misurano sui residui di A. La corrispondenza tra i
residui è quella dell'allineamento ed è fissata; nessuna metrica la cambia.

**Due percorsi, stessi campi.**

- **Integrato:** allineamento di sequenza (Needleman-Wunsch, BLOSUM62) delle catene, poi `tm_score_search` normalizzato
  su A (quella su B entra solo nel TM-score su B e fra le sovrapposizioni provate dal GDT).
- **TM-align / US-align:** ogni struttura va in un file con la sola catena da allineare (quella indicata o la prima
  con Cα, come nel percorso integrato): i programmi leggono la prima catena, e così la mappatura si ricostruisce dagli
  stessi file che leggono. I residui sono quelli che Biopython legge da quei file (solo ATOM con Cα, prima altloc,
  codici di inserzione distinti). Le righe dell'allineamento (mobile, segni, bersaglio) devono avere, senza gap, le
  stesse lettere (`X` vale per ogni residuo non standard, tradotto in modo diverso dai due): altrimenti il risultato ha
  una nota, nessun confronto per residuo e non entra in cache. Una colonna con due residui è una coppia solo se segnata
  `:` (< 5 Å) o `.`: lo spazio indica residui nella stessa colonna oltre la distanza di taglio del programma, non
  allineati (verificato con un tratto spostato di 12 Å: 69 coppie, come "Aligned length"). Le coordinate di B vengono
  dalla matrice di `-m` (formato di TM-align, righe da 1, e di US-align, righe da 0: X = t + U·x); senza matrice
  (versioni vecchie) si ricalcola la sovrapposizione del TM-score sulle stesse coppie, con una nota. Con la matrice
  BioLoom riottiene il TM-score stampato dal programma (test).
- **PDB per il viewer:** in entrambi i percorsi lo costruisce BioLoom con la sovrapposizione del TM-score: catena A = A,
  catena B = B sovrapposta, numerazione originale. Prima, con US-align il file conteneva solo B e con TM-align le catene
  erano invertite rispetto al percorso integrato.

**Metriche.**

| Campo | Definizione | Fonte |
|---|---|---|
| `pairs[]` | per coppia: catena, numero e lettera in A e in B, distanza dei Cα (Å) dopo la sovrapposizione del TM-score, lDDT del residuo di A | |
| `tm_score`, `tm_score_b` | normalizzato su A e su B | Zhang & Skolnick, Proteins 57:702, 2004 |
| `rmsd` | tutte le coppie dopo la loro sovrapposizione di Kabsch, come lo riportano TM-align e US-align (prima, nell'integrato, sulla sovrapposizione del TM-score) | |
| `rmsd_close`, `close_pairs` | coppie a meno di 5 Å nella sovrapposizione del TM-score (le `:` di TM-align) | Zhang & Skolnick, Nucleic Acids Res 33:2302, 2005 |
| `gdt[]`, `gdt_ts`, `gdt_ha` | Pd = % dei Cα di A (su tutti i residui di A) entro d Å, estremo compreso; TS = media di P1, P2, P4, P8; HA = media di P0,5, P1, P2, P4 | Zemla, Nucleic Acids Res 31:3370, 2003 (LGA); Read & Chavali, Proteins 69 S8:27, 2007 |
| `lddt`, `lddt_distances` | Cα, raggio di inclusione 15 Å su A, conservata se \|d_B − d_A\| < 0,5, 1, 2, 4 Å; media delle quattro frazioni | Mariani et al., Bioinformatics 29:2722, 2013 |
| `transform` | rotazione e traslazione che portano B su A (x' = R·x + t) | |

- **GDT: ricerca per soglia.** Il GDT di LGA massimizza separatamente ogni Pd su sovrapposizioni diverse. BioLoom fa
  lo stesso con sottoinsiemi iterativi: semi = frammenti contigui delle coppie di lunghezza L, L/2, L/4… fino a 4 (passo
  di mezzo frammento, come `tm_score_search`; al massimo 48 frammenti per lunghezza, un limite che conta solo oltre i
  ~100 residui) più la sovrapposizione del TM-score; da ogni
  seme Kabsch sulle coppie entro d e di nuovo le coppie entro d, fino a convergenza (massimo 20 passi; un insieme già
  esplorato per quella soglia ferma il ciclo). Ogni sovrapposizione provata conta per tutte le soglie. È un'euristica
  come quella di LGA: il valore è un **limite inferiore** del massimo vero e mai inferiore al valore sulla
  sovrapposizione del TM-score. Esempio (test): con il tratto 31–37 di 1UBQ spostato di 6 Å, a 4 Å la sovrapposizione
  del TM-score tiene 69 Cα, la ricerca ne trova 75; il massimo vero è 76 (traslazione a metà), quindi il GDT-TS è
  sottostimato di 0,3 punti. In un caso a due gruppi rigidi la ricerca trova l'ottimo (15 su 15 contro 10).
- **lDDT:** non dipende dalla sovrapposizione. Una distanza che coinvolge un residuo di A senza corrispondente in B conta
  come non conservata (come gli atomi mancanti del lDDT originale), quindi il lDDT globale penalizza anche la
  copertura. Globale = tutte le distanze insieme (non la media dei residui). Il controllo stereochimico e le simmetrie
  delle catene laterali del lDDT a tutti gli atomi non si applicano ai Cα.
- **Coppie diverse fra i percorsi:** l'integrato allinea per sequenza e tiene anche coppie lontane (tratto a 12 Å: 76
  coppie, lDDT 0,870), TM-align le lascia fuori (69 coppie, lDDT 0,855). Con la stessa corrispondenza i due percorsi
  danno gli stessi valori, a parte le due ricerche euristiche del TM-score (tratto a 3 Å: 0,9557 contro 0,9553, distanze
  diverse di circa 0,1 Å).
- **Costo** (5000 coppie, catena sintetica): ricerca del TM-score 9 s, GDT 7 s, lDDT 2,4 s (a blocchi di 128 righe della
  matrice delle distanze, niente matrice 5000 × 5000 intera); 1UBQ: meno di 0,1 s.

**Valori di riferimento** (test in `test_comparison.py`, uguali con l'integrato, TM-align e US-align salvo dove indicato):

| B rispetto ad A = 1UBQ | TM-score su A (su B) | RMSD | GDT-TS | GDT-HA | lDDT |
|---|---|---|---|---|---|
| 1UBQ, copia ruotata e traslata; catena B contro catena A di `ubq_dimer.pdb` | 1,000 | 0,00 | 100 | 100 | 1,000 |
| tratto 31–37 spostato di 6 Å verso l'esterno | 0,927 | 1,74 (entro 5 Å: 0,00 su 69) | 95,07 | 92,76 | 0,878 (tratto 0,20–0,28, fuori ≥ 0,83) |
| un TRP 35A inserito in B | 1,000 (0,987) | 0,00 | 100 | 100 | 1,000 |
| Cα del residuo 20 tolto in B | 0,987 (1,000) | 0,00 | 98,68 | 98,68 | 0,977 |

**Cache.** Il descrittore strutturale ha `version: 2` (`STRUCTURAL_VERSION`): le voci scritte prima, senza confronto
per residuo, hanno un'altra chiave e non si leggono più (le toglie la pulizia a 30 giorni). In più `run_cached(accept=)`
scarta alla lettura una voce che non ha tutti i campi del confronto (`_complete`), e `keep` non scrive in cache un
risultato senza coppie (mappatura non ricostruita). I job completati prima restano leggibili: l'interfaccia mostra le
metriche che hanno e chiede di rilanciare per il confronto per residuo. Per distinguerli ogni risultato nuovo ha
`comparison_version` (= `STRUCTURAL_VERSION`), anche quando il confronto per residuo non è riuscito: l'invito a
rilanciare compare senza coppie e senza `comparison_version`, anche accanto alla nota "Allineamento dipendente dalla
sequenza…" che il metodo integrato scriveva già prima; un risultato nuovo senza coppie mostra solo la sua nota.

### Ricerca di template

- **RCSB** (predefinito): Search API (MMseqs2 lato server) → sequenze di catena via GraphQL → riallineamento
  Smith-Waterman locale per identità, copertura e coordinate. Ritenta automaticamente i timeout 5xx di RCSB.
- **NCBI QBlast**: conforme alle linee guida (tool/email, polling ogni 60 s, attesa massima 45 min).
- **BLAST+ locale**: `blastp` contro `data/databases/pdbaa` (scaricabile e aggiornabile da NCBI con un job).
- **MMseqs2 locale**: `easy-search -s 7.5` sul database creato da pdbaa (`blastdbcmd` → `createdb`), con
  l'indice locale se presente.
- **AlphaFold DB** (modelli predetti, vedi [sotto](#modelli-alphafold-db)): accessione UniProt indicata, oppure
  proteine Swiss-Prot più simili trovate con NCBI QBlast.
- Template consigliato: massimo identità × copertura; a parità, una struttura sperimentale prima di un modello
  predetto, poi la miglior risoluzione, poi il pLDDT medio più alto. Un modello AlphaFold è un'ipotesi e non un dato:
  vince solo se allinea meglio il bersaglio.

### Modelli AlphaFold DB

Per le proteine senza struttura sperimentale omologa un modello di AlphaFold DB (EBI) fa da template o da
riferimento. È sempre segnato come **modello predetto, non struttura sperimentale**.

- **Client** (`engines/remote.py`): `GET https://alphafold.ebi.ac.uk/api/prediction/{accessione}`, con l'accessione
  controllata dalla regex ufficiale di UniProtKB (isoforme escluse) prima di ogni richiesta. Il parser è tollerante:
  identificativo da `entryId` o `modelEntityId`, versione da `latestVersion`, dagli URL (`_vN`) o da `allVersions`,
  intervallo da `uniprotStart/End` o `sequenceStart/End`, sequenza del frammento anche ritagliata dalla sequenza
  UniProt intera, pLDDT medio da `globalMetricValue` (0–100, altrimenti nullo). Le voci senza id `AF-<ACC>-F<n>`
  (complessi) si scartano; a parità di id resta la versione più recente. Risposte in cache 7 giorni
  (`data/cache/alphafold`, come UniProt), nuovi tentativi sui 5xx, 404 o nessuna voce = `alphafold_not_found`
  ("nessun modello per l'accessione…", per esempio proteine oltre 2700 residui, escluse da AlphaFold DB). Download
  del PDB, oppure del mmCIF se il PDB manca, solo da URL `https://alphafold.ebi.ac.uk/files/…` e con controllo che
  il file contenga coordinate (non una pagina d'errore).
- **Libreria** (`TemplateLibrary.fetch_alphafold`, `POST /api/templates/alphafold` con `{"accession"}`, anche
  `AF-<ACC>-F2` per i frammenti delle proteine lunghe): id `AF-<ACC>-F<n>` (compatibile con `normalize_id`), file
  originale intatto (HEADER/TITLE/REMARK di AlphaFold e pLDDT nella colonna B-factor), meta con `source: "alphafold"`,
  `predicted: true`, `note`, `plddt_mean`, `plddt_fractions` (dal file) e `alphafold` (versione, frammento,
  intervallo UniProt, gene, organismo, URL del PAE). Un modello già in libreria non genera richieste. Il download
  riceve l'annullamento del job (`fetch_alphafold(check=, log=)`): i tentativi verso EBI e le loro pause si fermano,
  e anche l'attesa di un altro elemento di pipeline che sta scaricando lo stesso modello (lock per template preso a
  intervalli di 0,5 s con il controllo dell'annullamento; chi si sveglia dopo l'annullamento non riscarica).
- **Riconoscimento dei modelli AlphaFold** (`plddt.is_alphafold_model`, unico per libreria, rapporto di qualità e
  copia preparata del template): i valori della colonna B-factor (tutti gli atomi degli amminoacidi) stanno in 0–100
  e non sono tutti nulli, e
  - l'intestazione ha i segni certi di AlphaFold DB (`is_alphafold_text`): TITLE "ALPHAFOLD … PREDICTION" senza
    EXPDTA (sempre presente nelle strutture sperimentali), oppure ModelCIF con `_ma_qa_metric` e il nome AlphaFold
    senza `_exptl.method`;
  - oppure il nome segue lo schema di AlphaFold DB, `AF-<accessione UniProt>-F<n>[-model_v<k>]` (file scaricati,
    caricati con prefisso, id di libreria e loro copie `AF-…_A.pdb`), e l'intestazione non dichiara un'altra
    origine (`declares_other_origin`: EXPDTA, `_exptl.method`, anche in forma di loop, oppure `REMARK   6 MODELLER`).

  Non contano le menzioni di AlphaFold altrove (per esempio `REMARK 200 STARTING MODEL: ALPHAFOLD` di una struttura
  risolta per sostituzione molecolare): i B-factor di una struttura sperimentale o di un modello MODELLER non vanno
  mai presi per pLDDT. Un file di AlphaFold caricato dall'utente diventa `predicted` con lo stesso criterio. I
  metadati registrati prima di questi campi (senza `predicted`) si completano alla prima lettura (`_meta`: elenco,
  dettaglio e `is_predicted` passano di lì) con `predicted`, `plddt_mean` e `plddt_fractions`, e si riscrivono: la
  libreria e l'interfaccia (badge, opzione della soglia pLDDT) vedono quello che vede la modellazione.
- **Motore `alphafold`** (`engines/search.py`): AlphaFold DB non ha una ricerca per sequenza.
  - Con l'accessione (`uniprot` di `/api/search`, precompilato dall'interfaccia per le sequenze importate da UniProt;
    nella pipeline dall'intestazione FASTA `sp|ACC|NOME` o `tr|ACC|NOME`) si usano i modelli di quell'accessione.
    Nella pagina Ricerca l'accessione precompilata segue la sequenza da cui viene: incollando o caricando un'altra
    sequenza si toglie, a meno che l'utente non l'abbia scritta a mano; un FASTA incollato con l'intestazione di
    UniProt la precompila. Nella Pipeline «Usa le sequenze del progetto» scrive `sp|ACC|NOME` (`tr|` per le voci non
    revisionate) per le sequenze importate da UniProt, così non passano da NCBI.
  - Senza, NCBI QBlast su `swissprot` (`ncbi_blast(database="swissprot")`); le accessioni vengono da `sp|P0CG48.1|…`
    o dall'accessione NCBI con versione (`P0CG48.1`), nell'ordine di BLAST e senza ripetizioni, e si prendono i
    modelli delle prime 10.
  - Per ogni modello: Smith-Waterman (`align.pairwise`, BLOSUM62, gap 11/1) del bersaglio sulla sequenza del modello;
    identità e positivi sulla lunghezza dell'allineamento, copertura = tratto del bersaglio allineato, come gli altri
    motori. E-value di BLAST se l'accessione viene da lì, altrimenti Karlin-Altschul sul solo confronto a coppie
    (indicativo). Hit con `source: "alphafold"`, `predicted`, `plddt`, `template_id = pdb_id = AF-<ACC>-F<n>`, catena A.
  - Cache dei calcoli con il TTL dei motori remoti (7 giorni); l'accessione entra nella chiave solo per questo
    motore (le chiavi degli altri non cambiano).
  - Esiti: con l'accessione indicata il candidato è uno solo, quindi nessun modello (404, `alphafold_not_found`) o
    EBI non disponibile fanno fallire il job con il messaggio di AlphaFold DB, invece di "0 template trovati". Senza
    accessione una proteina senza modello si salta (esito definitivo); EBI non disponibile per alcune proteine rende
    il risultato parziale (niente cache), per tutte fa fallire il job come gli errori di RCSB e NCBI. Il risultato
    ha `notes` (proteine senza modello, proteine senza risposta) che la pagina Ricerca mostra, anche al posto del
    suggerimento sull'E-value quando non ci sono modelli; nella pipeline finiscono nell'errore dell'elemento.
  - Nella pipeline (`template_mode: auto`) funziona come gli altri: il modello scelto si scarica nella libreria
    prima della modellazione (`_prepare_templates` scarica da RCSB i codici di 4 caratteri e da AlphaFold DB gli id
    `AF-…`).
- **Ritaglio per pLDDT** (`domain/plddt.py`, `modeller.prepare_template`): per i template predetti, opzioni di
  modellazione `plddt_trim` (predefinita accesa) e `plddt_min` (predefinita 70, 0–100), anche nella pipeline. Il
  pLDDT è il B-factor del C-alfa. Fasce di AlphaFold DB (Tunyasuvunakool et al., Nature 596:590, 2021), estremo
  inferiore compreso: ≥ 90 molto alta, 70–90 confidente, 50–70 bassa, < 50 molto bassa (spesso disordine). Sotto 70
  lo scheletro non è affidabile e, come template, trasferirebbe vincoli su conformazioni non predette con confidenza.
  - Si tolgono i residui con pLDDT < soglia; il template preparato, la sua sequenza e primo/ultimo residuo sono
    quelli ritagliati, con i record descrittivi di AlphaFold (o un TITLE "ALPHAFOLD … PREDICTION" se il file non li
    ha, come un mmCIF) e una riga `REMARK 999 BIOLOOM PLDDT_TRIM`: la copia resta riconoscibile come modello
    AlphaFold qualunque sia il nome. La copia di una struttura non AlphaFold conserva invece il suo EXPDTA (o lo
    riceve dal `_exptl.method` del mmCIF), così i suoi B-factor non passano per pLDDT.
  - **Frammenti isolati** (`plddt.MIN_FRAGMENT = 5`): dopo la soglia si tolgono anche i tratti sopra soglia più
    corti di 5 residui contigui (nel file e nella numerazione) accanto ad almeno un residuo tolto. Cinque residui sono
    il minimo per un giro d'elica α con il legame i→i+4; il pLDDT misura solo la confidenza locale, e la posizione di
    un tratto più corto in mezzo a regioni disordinate (che dice il PAE, non il pLDDT) darebbe a MODELLER
    restrizioni da un punto arbitrario. Un tratto corto delimitato solo dalle estremità della catena resta. Il
    risultato separa i conteggi (`removed_threshold`, `removed_fragments`, `min_fragment`; `removed` è il totale).
  - Minimo 30 residui rimasti (`plddt_too_few`): circa la dimensione dei più piccoli domini che si ripiegano da soli
    (HP35 della villina, dominio WW, zinc finger C2H2); sotto, il template è un frammento e align2d non è affidabile.
  - **Allineamento automatico**: MODELLER legge il template ritagliato, quindi i residui tolti sono gap del template.
  - **PIR dell'utente** (`_normalize_pir`): una riga scritta sul modello completo viene riportata sul ritaglio (residui
    tolti → gap, intervallo al primo/ultimo residuo rimasto, colonne di soli gap eliminate); una riga già ritagliata
    resta com'è; una riga diversa da entrambe è un errore chiaro. `align2d` (Editor PIR) allinea sul modello intero.
  - Log ("rimossi 11 residui con pLDDT < 70 (1–5:A, 71–76:A), restano 65 di 76", con "… e 4 in frammenti isolati
    di meno di 5 residui" se ce ne sono), risultato (`templates[].plddt`,
    `plddt_removed`, avviso "modello predetto"), `REMARK 999 BIOLOOM TEMPLATE … ALPHAFOLD PLDDT<70 REMOVED 11` nel
    modello e, nella pipeline, `template_predicted`/`plddt_removed` nelle metriche dell'elemento.
  - Le strutture sperimentali non si ritagliano mai.

### Ligandi, cofattori e metalli (`domain/ligands.py`)

Il trasferimento dei gruppi eteroatomici dal template al modello è selettivo e viene verificato dopo la modellazione.

- **Prima di questa versione** l'opzione `hetatm` scriveva tutti gli eteroatomi nella copia preparata, ma lo script di
  allineamento non leggeva gli HETATM e l'intervallo del template finiva sull'ultimo residuo: MODELLER ignorava i
  gruppi e il modello non ne aveva (verificato con MODELLER 10.8 su `ubq_zn.pdb`). La casella «Ligandi» del singolo
  template non attivava nemmeno `env.io.hetatm`.
- **Classificazione** (`classify`, `candidates`), nel dettaglio del template (`GET /api/templates/{id}` → `ligands`,
  `ANALYSIS_VERSION` 3: le analisi in cache si ricalcolano) e nella scelta della Modellazione. Le acque non compaiono.

  | Classe | Criterio | Per default |
  |---|---|---|
  | metallo | un solo atomo di un elemento metallico (colonna element, altrimenti il nome: ZN, FE2, CU1…) | sì; no per Na⁺, K⁺ e altri monovalenti (spesso dal tampone) |
  | cofattore noto | codice CCD in `COFACTORS`: HEM, HEC, HEA, FAD, FMN, NAD, NAI, NAP, NDP, ATP, ADP, AMP, ANP, GTP, GDP, GNP, SAM, SAH, PLP, PMP, COA, ACO, TPP, TDP, BTN, LPA, FES, SF4, F3S, CLA, BCL, RET, B12, CNC, PQQ, H4B, MGD | sì |
  | additivo | `COMMON_ADDITIVES` di `domain/structure` più glicoli, tamponi, crioprotettori e piccoli anioni (MPD, PG4, PGE, EPE, MES, TRS, CIT, BME, ACY, IMD, SCN, AZI…) | no, con la motivazione: di solito senza ruolo biologico (come le esclusioni di BioLiP, Yang et al. 2013) |
  | residuo modificato | ha N, CA e C dello scheletro (MSE, SEP, TPO…) | mai trasferibile: fa parte della catena |
  | altro ligando | tutto il resto (inibitori, zuccheri…) | sì |

- **Opzioni di modellazione:** `hetatm` attiva i ligandi per tutti i template con la scelta predefinita; `ligands` è
  l'elenco `[{template, chain, resnum, resname}]` dei gruppi da trasferire, esattamente quelli, e attiva i template che
  cita (un template attivo senza voci non trasferisce nulla). Con più template il `template` è obbligatorio; un template
  non scelto, un gruppo che non c'è o un residuo modificato sono errori chiari (`invalid_ligand`, `ligand_not_found`,
  `ligand_not_transferable`). I valori sono validati (regex, al massimo 100) e non entrano mai negli script. Il
  riferimento del template conserva `hetatm` (Pipeline con template fisso). Nella pipeline con ricerca automatica vale
  solo `hetatm`: i template non si conoscono prima (400 `invalid_ligand`).
- **Copia preparata** (`modeller.prepare_template`): solo i gruppi scelti, spostati in fondo alla loro catena
  nell'ordine del file; le selenometionine restano come prima (MET) con i ligandi attivi. Il risultato ha
  `ligand_groups` (gruppi trasferiti con il sito nel file originale: donatori dei metalli, acque comprese, e legami
  covalenti) e `ligands_excluded` (gli altri gruppi delle catene, con il motivo); il log li elenca. La chiave di
  `PreparedTemplates` comprende la scelta.
- **PIR** (`pir_with_ligands`, per l'allineamento automatico e per quello dell'utente): MODELLER, con
  `env.io.hetatm = True`, legge ogni gruppo HETATM come residuo `.` (BLK) e lo copia nel modello solo se anche il
  bersaglio ha `.` nella stessa colonna. Verificato con MODELLER 10.8: con `.` in entrambe le righe lo Zn arriva nel
  modello, con `-` nel bersaglio no; `.` vale anche per i gruppi a cui `restyp.lib` dà un codice proprio (`z` per lo
  Zn).
  1. I `.` già presenti (PIR dell'utente, allineamento salvato da una modellazione precedente) diventano gap e le colonne
     rimaste vuote si tolgono, con un avviso se non coincidono con la scelta: la scelta è quella delle opzioni.
  2. Per ogni catena con gruppi trasferiti si aggiunge una colonna per gruppo alla fine del tratto di quella catena
     (prima del `/`): `.` nel template e nel bersaglio, `-` negli altri template.
  3. L'intervallo del template si aggiorna: fine sull'ultimo gruppo (se l'ultima catena ne ha), `+N` ricontato, una
     fine che indicava un eteroatomo tolto torna sull'ultimo residuo.

  Errore chiaro se i `/` del template e del bersaglio non sono nelle stesse colonne o se la voce del template non
  comprende tutti i residui di una catena con ligandi (i gruppi stanno dopo l'ultimo residuo). Lo script dei modelli
  imposta `env.io.hetatm = True` solo se almeno un gruppo è trasferito.
- **Verifica** (`evaluate`), per ogni gruppo eteroatomico non d'acqua (i residui modificati no):

  | Controllo | Regola | Esito |
  |---|---|---|
  | contatti | residui della proteina con un atomo pesante entro 4 Å | nessuno: da controllare |
  | scontri | atomi pesanti a meno di 2,2 Å dalla proteina o da un altro gruppo, escluse la coordinazione (metallo–donatore nella sfera) e i legami covalenti (somma dei raggi covalenti + 0,45 Å) che il template ha con il residuo allineato; senza template la distanza di legame è un «possibile legame covalente» | problematico |
  | donatori dei metalli | N, O, S (His, Cys, Met, Asp/Glu, Asn/Gln, Ser/Thr/Tyr, scheletro, acqua, altri ligandi) entro la distanza attesa + 0,5 Å | nessuno: problematico |
  | distanze | attesa ± 0,25 Å nella norma, oltre «lunga»/«corta», sotto attesa − 0,5 Å «troppo corta» | da controllare / problematico |
  | donatore insolito | categoria senza distanza attesa per il metallo (es. Ca–N(His)), N non imidazolico | da controllare |
  | numero di coordinazione | fuori dall'intervallo tipico, contando le acque del template non trasferite | da controllare |
  | geometria | gRMSD oltre 13,5° o posizioni vacanti oltre il 25% (CheckMyMetal) | da controllare |
  | geometria | gRMSD oltre 21,5° | problematico |
  | sito del template | meno di tutti i donatori della proteina conservati; meno della metà | da controllare; problematico |

  **Distanze attese** (Å; Harding 2006, strutture proteiche a ≤ 1,25 Å; Ni dalla Tabella 3 di Zheng et al. 2017, CSD).
  Le analisi più recenti differiscono di qualche centesimo (Zn–S(Cys) 2,32, Cu–S(Cys) 2,20, Na–O 2,35 Å in Bazayeva
  et al. 2024), entro la tolleranza. Ser/Thr/Tyr usano il valore dell'acqua, Asn/Gln e i carbonili quello dello
  scheletro, Met e gli S dei ligandi quello della Cys, gli O dei ligandi quello di Asp/Glu, gli N dei ligandi quello
  dell'His.

  | Metallo | O acqua | O Asp/Glu | O scheletro | N His | S Cys | Numero di coordinazione tipico |
  |---|---|---|---|---|---|---|
  | Na | 2,41 | 2,41 | 2,38 | — | — | 4–7 |
  | Mg | 2,07 | 2,07 | 2,26 | — | — | 5–6 |
  | K | 2,81 | 2,82 | 2,74 | — | — | 5–8 |
  | Ca | 2,39 | 2,36 | 2,36 | — | — | 6–8 |
  | Mn | 2,19 | 2,15 | 2,19 | 2,21 | 2,35 | 4–6 |
  | Fe | 2,09 | 2,04 | 2,04 | 2,16 | 2,30 | 4–6 |
  | Co | 2,09 | 2,05 | 2,08 | 2,14 | 2,25 | 4–6 |
  | Ni | 2,08 | 2,07 | 2,07 | 2,09 | 2,18 | 4–6 |
  | Cu | 2,13 | 1,99 | 2,04 | 2,02 | 2,15 | 3–6 |
  | Zn | 2,09 | 1,99 | 2,07 | 2,03 | 2,31 | 4–6 |

  Gli intervalli del numero di coordinazione seguono le distribuzioni nei siti proteici (Mg²⁺ quasi sempre 6, Zn²⁺ di
  solito 4); gli estremi sono una scelta di BioLoom. Altri metalli (Cd, Hg…): donatori entro 3,0 Å, geometria, esito
  al più «da controllare».
- **Geometria** (`geometry`, come CheckMyMetal, Zheng et al. 2017, con le geometrie ideali confrontate per angoli di
  Kuppuraj et al. 2009): per le geometrie ideali con m posizioni, da n a n + 2 (al massimo 7: lineare, trigonale
  planare, tetraedrica, quadrato planare, bipiramide trigonale, piramide a base quadrata, ottaedrica, bipiramide
  pentagonale), si cerca l'assegnazione degli n donatori che minimizza lo scarto quadratico medio fra gli angoli
  donatore–metallo–donatore osservati e ideali (gRMSD); le posizioni libere sono vacanti. Vince la gRMSD minima; entro
  1° quella con meno vacanze (tre donatori di un tetraedro: tetraedrica con una vacanza, non trigonale planare).
- **Confronto con il sito del template** (modellazione, `template_contexts`): le colonne del PIR usato da MODELLER
  legano i residui del template (dalla copia preparata, dal residuo iniziale dell'intervallo) a quelli del modello, e
  i `.` legano i gruppi. Ogni residuo che coordina il metallo nel template è *conservato* (il residuo allineato lo
  coordina nel modello), *perso*, *sostituito* (da un residuo che non coordina con la catena laterale), *non allineato*
  o *non modellato* (catena o tratto fuori dall'allineamento). Le acque del template non si trasferiscono: si contano
  per il numero di coordinazione («1 (3 con 2 acque del template, non trasferite)»). Un allineamento che non
  corrisponde al modello o alla copia preparata lascia la verifica senza confronto, con una nota.
- **Dove:** `quality.analyze` aggiunge `ligands` (senza template) se la struttura ha gruppi eteroatomici non d'acqua;
  `run_modeling` lo ricalcola con il confronto per ogni modello analizzato, mette il riepilogo nel modello
  (`models[].ligands`: gruppi, esito peggiore, conteggi) e nel risultato `ligands` (`transferred`, `verification` del
  modello migliore anche senza analisi di qualità), scrive `REMARK 999 BIOLOOM LIGANDS n ZN:A101 …` e avvisa se il
  modello ha meno gruppi di quelli trasferiti. Un errore inatteso della verifica va nel log e non fa fallire il job.
  Voto e consenso non cambiano.
- **Valori di riferimento** (test in `test_ligands.py`):
  - `ubq_zn.pdb`: Zn con NE2 di His68 (2,10 Å, attesa 2,03) e due acque (2,10 Å, attesa 2,09), angoli 67,2°, 81,1° e
    90,0°: piramide a base quadrata con 2 posizioni vacanti, gRMSD 14,1° → «da controllare» (numero di coordinazione 3
    su 4–6, geometria distorta, vacanze 40%);
  - lo Zn spostato a 0,9 Å dal CB di Leu43 → «problematico» (scontro);
  - modello dell'ubiquitina di lievito (MODELLER, 1 modello `very_fast`) con il solo Zn trasferito da `ubq_zn.pdb` più
    un SO₄ escluso: Zn A77 a 2,10 Å da NE2 di His68, sito del template conservato (1 su 1), 2 acque non trasferite →
    numero di coordinazione 1 (3) → «da controllare»; lo stesso PIR con i ligandi spenti perde il `.` con un avviso e il
    modello non ha HETATM.
- **Limiti:** i gruppi di una catena senza residui proteici non sono trasferibili (si trasferiscono con la catena
  scelta); il carbonio non è considerato donatore (CO e CN⁻ legati al ferro dell'eme risultano scontri); i codici
  minuscoli di `restyp.lib` in un PIR dell'utente (`z`, `h`, `w`) vengono letti in maiuscolo come amminoacidi: per i
  ligandi si usa `.`; il raffinamento dei loop di un modello non legge gli HETATM (MODELLER predefinito), quindi i
  ligandi non passano ai modelli di loop.

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
  | ricerca | sequenza (maiuscole, senza spazi), motore, E-value, risultati massimi, taxid (solo NCBI), accessione UniProt (solo AlphaFold DB) | BLAST+ e MMseqs2: dimensione e data di tutti i file del database (`pdbaa.*`, `pdbaa_h*`), eseguibile (percorso, dimensione, data), uso dell'indice veloce MMseqs2 |
  | multiplo | nomi e sequenze nell'ordine dato, tipo (proteina/acido nucleico), programma effettivo (`auto` risolto) | eseguibile di Clustal Omega/MAFFT/MUSCLE |
  | strutturale | SHA-256 del contenuto e formato dei due file, catene, programma effettivo, versione del risultato (2: con il confronto per residuo) | eseguibile di TM-align/US-align |

  Il nome delle strutture non entra nella chiave (un file caricato di nuovo ha un prefisso casuale): gli `ids`
  del risultato si riapplicano dopo la lettura.
- **Scadenza:** 7 giorni per RCSB, NCBI e AlphaFold DB (il PDB cambia ogni settimana, Swiss-Prot e AlphaFold DB con
  le loro versioni); per i motori locali decide il timbro del
  database e la pulizia a 30 giorni. Una voce "dal futuro" di oltre un giorno (orologio di un altro computer) non
  vale.
- **Valore:** risultato del motore e testo dei file che il calcolo ha scritto nella cartella di lavoro
  (`query.fasta`, `mmseqs_hits.m8`, `msa_input/output.fasta`, `mobile.pdb`, `target.pdb` e i file di TM-align).
  Un risultato dalla cache ricrea gli stessi file del job. Nel caso strutturale il testo del PDB sovrapposto è nel
  risultato (`superposed_pdb`) e il job lo riscrive come `superposed.pdb`. Oltre 20 MB di file, o con file non
  testuali, il risultato non entra in cache.
- **Cosa non entra:** calcoli falliti, job annullati, risultati parziali (ricerca locale senza i metadati RCSB,
  output di TM-align non riconosciuto o senza la mappatura dei residui). Con `accept` una voce trovata ma incompleta
  (risultato strutturale senza il confronto per residuo) è un mancato riscontro e il nuovo calcolo la sostituisce.
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

### Prestazioni

Profilazione dei job con MODELLER reale (F1.2, F1.3) e ottimizzazioni applicate solo dove la misura dava un guadagno.
Lo strumento è `tests/perf_timing.py`, fuori dalla suite: `scripts/test.sh backend tests/perf_timing.py -s`.

- **Cosa misura:** `test_profile` avvolge con monkeypatch le funzioni dei passi (nessuna modifica al codice misurato)
  e misura il tempo reale del job dall'avvio alla chiusura dell'esecuzione (`JobManager._execute`); i tempi dei
  singoli modelli vengono dai marcatori `@@BIOLOOM` di `model.py`. `test_paired` alterna due varianti nello stesso
  processo (A B A B …), così il carico della macchina pesa allo stesso modo su entrambe. `test_profile_modeller_startup`
  misura il costo fisso di un processo MODELLER.
- **Condizioni:**
  - container di test `bioloom-test:4.1.0` (MODELLER 10.8, mkdssp 4.2.2, PROCHECK; ProSA senza licenza, quindi si
    ferma subito con l'avviso);
  - Docker Desktop su Windows 11, 4 CPU logiche (Intel i3-1115G4: 2 core con hyper-threading) e 5,7 GB per Docker;
  - template 1UBQ (catena A) e bersaglio ubiquitina di lievito (76 residui, 96 % di identità), ricerca simulata
    (un hit 1UBQ), raffinamento `fast`;
  - "prima" = codice a0e410b, "dopo" = questa versione, lanciati alternati per scenario (due giri da 3 esecuzioni
    ciascuno). Si riporta la mediana, in secondi; il minimo differisce di meno di 0,5 s. Un primo giro con altri
    container di test attivi sulla stessa macchina dava tempi fino a +40 % su tutti i passi, per entrambe le versioni:
    per questo gli scenari sono alternati e i confronti fini sono accoppiati nello stesso processo.

**Scomposizione di un job model** (5 modelli, qualità sul migliore con PROCHECK e ProSA, come nella pagina
Modellazione):

| Passo | Prima | Dopo |
|---|---|---|
| Preparazione del template | 0,02 | 0,02 |
| Script di allineamento (`align.py`, align2d) | 0,33 | 0,34 |
| Costruzione: avvio di MODELLER e lettura | 0,24 | 0,26 |
| Costruzione: restrizioni e modello 1 | 2,75 | 2,76 |
| Costruzione: ognuno dei 4 modelli successivi | 2,29 | 2,29 |
| **Costruzione dei modelli (`model.py`)** | **12,23** | **12,14** |
| Analisi: lettura della struttura | 0,00 | 0,01 |
| Analisi: Ramachandran | 0,01 | 0,24 ¹ |
| Analisi: clash | 0,01 | 0,20 ¹ |
| Analisi: mkdssp (prima con `mkdssp --version` a ogni analisi) | 0,17 | 0,30 ¹ |
| Analisi: SASA (Shrake-Rupley) | 0,11 | 0,27 ¹ |
| Analisi: DOPE di MODELLER | 0,98 | 1,20 ¹ |
| Analisi: PROCHECK | 0,22 | 0,41 ¹ |
| Analisi: ProSA (senza licenza) | 0,01 | 0,12 ¹ |
| Analisi: consenso | 0,00 | 0,00 |
| **Analisi di qualità (tempo reale)** | **1,50** | **1,22** |
| Iniezione dei REMARK, rapporto JSON, salvataggio (scratch → dati) | 0,01 | 0,01 |
| **Job intero** | **14,18** | **13,89** |

¹ In parallelo: durata del passo mentre gira insieme agli altri (GIL per i calcoli Python, core condivisi). Conta il
tempo reale dell'analisi, che scende verso quello del passo più lungo (il DOPE).

**Scenari** (tempo reale del job):

| Scenario | Prima | Dopo | Differenza |
|---|---|---|---|
| Job model, 5 modelli, qualità sul migliore con PROCHECK e ProSA | 14,18 | 13,89 | −2 % (analisi 1,50 → 1,22) |
| Job model, 5 modelli, qualità su tutti con PROCHECK e ProSA | 20,05 | 17,11 | −15 % (fase di qualità ² 7,64 → 4,54) |
| Elemento di pipeline, 3 modelli, qualità senza PROCHECK e ProSA (come la pagina Pipeline) | 9,30 | 9,13 | −2 % (analisi 1,40 → 1,23) |
| Job di qualità su 1UBQ (DOPE, PROCHECK, ProSA) | 1,48 | 1,25 | −16 % |
| Pipeline di 4 sequenze, stesso template mmCIF di 4560 residui (60 catene), 1 modello `very_fast`, senza qualità, 2 in parallelo | 9,38 | 5,64 | −40 % (preparazione, somma sugli elementi: 9,37 → 2,07) |

² Tempo del job dopo template, allineamento e costruzione dei modelli: analisi, REMARK e salvataggio.

**Confronti accoppiati** (`test_paired`, stesso processo, varianti alternate):

| Confronto | Varianti (mediana; minimo) |
|---|---|
| Analisi di 1UBQ, 5 coppie | in serie come prima 1,50 (1,41); in parallelo 1,17 (1,12) |
| Analisi di un modello di MODELLER, 5 coppie | in serie 1,54 (1,38); in parallelo 1,19 (1,13) |
| Qualità su tutti i 5 modelli, fase di qualità, 3 terne | 1 modello alla volta 6,07; 2 insieme 4,90; 3 insieme 4,26 |
| Pipeline di 4 sequenze sul template grande, 3 coppie | senza cache dei template 9,00; con cache 5,53 |

**Avvio di MODELLER** (costo fisso di ogni processo): interprete e preambolo della licenza 0,03 s; `import modeller`
ed `Environ()` 0,21 s; più le librerie di DOPE 0,28 s; DOPE completa di 1UBQ 0,95 s.

**Cosa si è applicato:**

1. **Passi dell'analisi in parallelo** (`quality.analyze`, vedi [Validazione di qualità](#validazione-di-qualità-enginesqualitypy)):
   −0,3 s per analisi (−22 % nei confronti accoppiati). Il limite è il DOPE, circa 1 s, di cui 0,28 s di avvio.
2. **Modelli in parallelo con la qualità "Su tutti i modelli"**: 3 insieme (`QUALITY_MODELS_PARALLEL`), perché su 4
   CPU logiche 3 batte 2 (4,26 contro 4,90 s) e oltre non ci sono modelli abbastanza lunghi da sovrapporre. Non si va
   oltre i job paralleli delle Impostazioni.
3. **mkdssp da `ctx.run`**: lo stesso comando di Biopython, con un processo in meno a ogni analisi (la versione si
   chiede una volta), sotto il budget di CPU, annullabile e con timeout. Prima Biopython lo lanciava da sé, fuori da
   ogni controllo del JobManager.
4. **Template preparati riusati nella pipeline** (`services/compute.PreparedTemplates`): `prepare_template` legge
   tutta la struttura anche per una catena. Per 1UBQ costa 0,02 s, e il guadagno è nullo; per un mmCIF di 4560 residui
   circa 1 s, 2 s con due elementi in parallelo (GIL). Con la cache un solo elemento prepara, gli altri copiano il
   file.
   - **Chiave:** id del template; file della libreria (percorso, dimensione, data di modifica in ns); catene;
     ligandi (attivazione e gruppi scelti, None = scelta predefinita); soglia del ritaglio pLDDT (None senza ritaglio).
   - **Vita e spazio:** la cache vive un'esecuzione, in `.tmp/templates` dello scratch, e si svuota a fine pipeline.
   - **Concorrenza ed errori:** un lock per chiave con attesa annullabile; le preparazioni fallite non si conservano.
   - **Tracce:** il log segna "(preparazione riusata da un elemento precedente)" e un riepilogo finale; il risultato ha
     `templates_prepared` e `templates_reused`.

**Valutato e non applicato:**

- **Più sequenze in un unico processo MODELLER** (proposta iniziale della F1.2): escluso, per tenere separati errori e
  log di ogni elemento. Il guadagno massimo sarebbe l'avvio dei processi (0,21–0,28 s su 9 s per elemento).
- **Costruzione dei modelli su più processi:** è il passo che pesa di più (86 % del job model, 81 % dell'elemento di
  pipeline). Resta seriale per tre motivi:
  - **Riproducibilità:** `automodel` non reimposta il generatore casuale per modello. Lo stato passa dal modello k al
    k+1, quindi, a parità di seme, dividere i modelli fra processi darebbe modelli diversi da quelli di prima, e
    dipendenti dalla suddivisione.
  - **Pipeline:** gli elementi girano già in parallelo.
  - **Budget di CPU:** dal JobManager (F1.4).
  - **Possibile evoluzione:** una proposta futura potrà usare semi dichiarati per modello, registrati nel risultato.
- **(c) Riletture della struttura:** l'analisi legge ogni modello una volta sola. Le altre letture misurate sono
  trascurabili: la struttura in analisi (0,00–0,01 s), il template 1UBQ (0,01 s), il consenso calcolato due volte
  (0,00 s). Nessun guadagno misurabile.
- **(d) DOPE di più modelli in un solo script:** servirebbe solo alla qualità "Su tutti i modelli"; con "Solo sul
  modello migliore" e nella pipeline il DOPE è uno.
  - **Guadagno:** l'avvio pesa 0,28 s su 0,95 s, quindi un solo script risparmierebbe al massimo 4 × 0,28 ≈ 1,1 s di
    CPU. Con 2 core fisici sono circa 0,5 s di tempo reale sulla fase di qualità da 4,26 s (stima, non misurata).
  - **Costo:** il DOPE dei 5 modelli diventerebbe seriale in un solo processo (≈ 0,3 + 5 × 0,65 ≈ 3,6 s), mentre ora
    si divide su 3 corsie. Un modello illeggibile fermerebbe il DOPE di tutti.
  - **Esito:** guadagno piccolo e incerto, non vale la perdita dell'isolamento per modello.
- **Un pool o un limite di CPU proprio:** i pool di `core/parallel.Steps` appartengono al singolo calcolo e servono
  solo ad aspettare più passi insieme. Ogni processo passa da `JobContext.run`, quindi il budget di CPU condiviso del
  JobManager (F1.4) governa anche i passi paralleli, senza un secondo limitatore.

## Frontend (`frontend/`)

React 19 + TypeScript + Vite, nessuna libreria UI esterna: componenti propri (`components/ui.tsx`), grafici SVG
(`components/charts.tsx`: Ramachandran, profili per residuo con striscia di struttura secondaria, heatmap,
mappa delle feature UniProt), viewer 3D 3Dmol.js caricato su richiesta con rilascio del contesto WebGL.
Il client API (`api/client.ts`) e i tipi (`api/types.ts`) sono l'unico punto di contatto con il backend.

**Qualità per residuo nel 3D e confidenza degli allineamenti:**

- **`Viewer3D`:** accetta una colorazione per valore per residuo (`coloring`: valori, scala, titolo) applicata al
  cartoon con `colorfunc`. Mostra la legenda sovrapposta e lascia in grigio i residui senza valore. Con una
  colorazione i pulsanti degli schemi predefiniti spariscono; senza, il viewer resta com'era (Modellazione,
  Allineamenti, Template, Pipeline).
- **Interazione:** con `onPickResidue` un clic su un atomo sceglie il residuo (`setClickable`) e il passaggio del
  mouse mostra residuo e valore (`setHoverable`). `centerKey` centra la vista sui residui evidenziati.
- **Logica pura con test:**
  - `lib/colorscale.ts`: scale continue con salti netti alle soglie, a fasce, categoriali; legende;
  - `lib/residueColoring.ts`: conversione del rapporto nelle opzioni "Colora per", compresa la scala pLDDT con i
    colori di AlphaFold DB (#0053D6, #65CBF3, #FFDB13, #FF7D45, come il tema pLDDT di Mol\*) sulle fasce di
    `plddtBand`, con il limite inferiore compreso come il backend e il ritaglio: ≥ 90 molto alta, 70–90 confidente,
    50–70 bassa, < 50 molto bassa (un residuo a 70,00 resta nel template ed è "confidente" anche nel 3D);
  - `lib/qualityReport.ts`: residui problematici (outlier, clash o almeno 2 metodi del consenso), la stessa regola
    per il filtro della tabella e per "Evidenzia tutti i residui problematici" nel 3D; struttura del rapporto
    mostrato (quella del job o del rapporto salvato, mai la scelta del selettore, che vale per la prossima analisi);
  - `lib/alignConfidence.ts`.
- **Selezione bidirezionale (pagina Qualità):**
  - un clic su grafici, tabelle, clash o regioni del consenso evidenzia e centra nel 3D;
  - un clic nel 3D seleziona il residuo: scheda del residuo, riga evidenziata nella tabella, linea nei profili e
    nel grafico del consenso.
  - Il QMEAN calcolato nella pagina fa ricalcolare il consenso dalla rotta e diventa un'opzione di colorazione.
- **Confidenza dell'allineamento (`AlignmentView`, modalità "Confidenza"):**
  - **Riferimento:** il bersaglio è la prima riga, oppure la voce `sequence` del PIR in Modellazione ed Editor PIR.
  - **Identità locale:** per ogni template, su 9 colonne centrate. Le colonne di gap del bersaglio non contano; un
    gap del template di fronte a un residuo del bersaglio conta come non identico. Per ogni colonna vale il miglior
    template che la copre.
  - **Evidenze:** gap del template, zona crepuscolare sotto il 25% (soglia della pipeline; Rost 1999) e residui del
    bersaglio non coperti. Una traccia riassuntiva sopra l'allineamento elenca le regioni in numerazione del
    bersaglio.

**Confronto strutturale (`pages/Compare.tsx`, `components/StructuralComparison.tsx`, `lib/compare.ts`):**

- **Un solo componente** per la pagina Confronto e la modalità Strutturale di Allineamenti: usa il job `align`
  strutturale esistente (`api.structuralAlign`), nessun tipo di job nuovo.
- **Colorazione:** `comparisonColoring` colora i residui di B della catena B del PDB sovrapposto per distanza (scala
  continua blu → giallo → rosso tra 0 e 5 Å, saturata oltre: 5 Å è la soglia delle coppie vicine di TM-align) o per
  lDDT (stessa scala rovesciata su 0–1); i residui di A hanno lo stesso valore nel passaggio del mouse ma il colore fisso
  del riferimento (grigio chiaro, `ResidueValue.color`). Nessun colore chiaro nella scala, per non confonderlo con A. Per
  i residui senza coppia vale il colore della catena (`ResidueColoring.chainColors`: A grigio chiaro, B grigio "senza
  valore"), con i campioni in legenda (`legendExtra`).
- **Selezione:** clic su grafico, tabella o tratto che devia (almeno 3 coppie consecutive oltre 1, 2 o 4 Å, le soglie
  del GDT, senza salti della numerazione di A) evidenzia i due residui e centra la vista; clic nel 3D seleziona la
  coppia (`pairIndexOf`, su catena A o B). Il grafico interrompe la linea dove A ha residui senza coppia. La tabella
  porta in vista la riga selezionata scorrendo solo il proprio contenitore (`rowScrollDelta`): `scrollIntoView`
  scorreva anche la pagina e un clic nel 3D portava via il viewer.
- **Link stabili:** `?a=template:1UBQ&ca=A&b=job:<id>:<file>&cb=&job=` (`encodeSource`/`decodeSource`, catena a parte):
  «Confronta…» nella tabella dei modelli di Modellazione (`compareTargets`: template usati e altri modelli del job; il
  modello è B), «Confronta con» nella pagina Qualità (i template del job del modello, o un riferimento a scelta) e «Apri
  in Confronto» negli Allineamenti. Il link precompila, il confronto parte con «Confronta». Nel selettore della
  struttura (`StructurePicker`, `lib/picker.ts`) un job che non è tra gli ultimi job di modellazione elencati (una
  pipeline, un job loop, un job più vecchio) compare in cima «(da un link)», con il suo modello fra le scelte: prima la
  struttura sembrava non scelta e sceglierne un'altra dall'elenco la azzerava. Un `?job=` che non si carica (job
  eliminato, link vecchio) mostra l'errore e l'invito a rilanciare (`compareJobView`), e `useJob` smette di
  interrogare un job che il server non ha (404).
- **Risultati precedenti** (senza `pairs` e senza `comparison_version`): metriche che hanno, viewer con le catene e un
  avviso per rilanciare, anche accanto alla nota del metodo integrato.

Lo stato di navigazione (job aperto, sequenza, template) vive nell'URL: ogni risultato ha un link stabile.

| Pagina | Funzione |
|---|---|
| Panoramica | flusso guidato, esempi, motori disponibili, risorse del computer |
| Sequenze | import FASTA/UniProt, proprietà ProtParam, predizione GOR, annotazioni UniProt/InterPro/PDB |
| Ricerca template | RCSB/NCBI/locale/AlphaFold DB (accessione UniProt facoltativa, precompilata dalle sequenze UniProt), tabella ordinabile, allineamento dell'hit, download, "modella con"; badge "modello predetto" e pLDDT; nota "dalla cache (data)" e ricalcolo ignorando la cache |
| Libreria template | download RCSB e "Da AlphaFold DB (UniProt)", upload PDB/mmCIF, catene, residui mancanti, ligandi classificati (metallo, cofattore noto, additivo, altro ligando, residuo modificato), DSSP, 3D; badge "AlphaFold", pLDDT medio e fasce di confidenza |
| Allineamenti | pairwise, multiplo, strutturale (con il confronto per residuo e «Apri in Confronto»); esportazione FASTA/Clustal/PIR; salvataggio nel progetto; cache per multiplo e strutturale |
| Editor PIR | documenti PIR del progetto, generazione con MODELLER, validazione in tempo reale |
| Modellazione | input automatico o da PIR, parametri, vincoli, loop; soglia pLDDT con un template AlphaFold (anche nella Pipeline); modelli, 3D, PyMOL, report HTML (con la vista 3D del modello selezionato), archivio; «Confronta…» di ogni modello con un template usato o con un altro modello del job; «Trasferisci ligandi, cofattori e metalli dai template» con la scelta dei gruppi (additivi e ioni del tampone deselezionati per default, `LigandPicker`) e la verifica del modello migliore |
| Qualità | voto, consenso tra i metodi (discordanze, regioni problematiche), Ramachandran, profili, clash, 3D colorato per valore per residuo con selezione bidirezionale, confronto analisi; report HTML (con QMEAN calcolato nella pagina e, dalla scheda 3D, la vista corrente); «Confronta con» il template del modello o un'altra struttura; sezione Ligandi e metalli (esiti, coordinazione, confronto con il sito del template): il clic su un gruppo lo mostra nel 3D con i residui coordinanti e le linee di coordinazione (`LigandsPanel`, `lib/ligands.ts`; ioni metallici sempre a sfere) |
| Confronto | `/compare?a=&ca=&b=&cb=&job=`: B sovrapposta ad A (riferimento), metriche (TM-score su A e su B, RMSD, RMSD entro 5 Å, GDT-TS, GDT-HA, lDDT, lunghezza allineata), 3D con B colorata per distanza (0 → ≥ 5 Å) o lDDT e A in grigio chiaro, grafico di distanza e lDDT per posizione, tratti che deviano, tabella per coppia; clic ovunque → coppia evidenziata nel 3D |
| Pipeline batch | multi-FASTA → modelli validati, filtro zona crepuscolare, tabella paginata, CSV/ZIP; ricerche riusate dalla cache; ripresa (tutti gli elementi non conclusi o solo i falliti); avanzamento, tempo residuo e tabella aggiornati in tempo reale |
| Job | log e file di ogni calcolo; ripresa delle pipeline fallite o annullate; elenco e dettaglio in tempo reale, con il tempo residuo dei job in esecuzione e la posizione di quelli in coda ("2° in coda") |
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
- **Report HTML:** testi sempre escapati, nessuno script né risorsa esterna, CSP nel documento e nella risposta,
  immagine accettata solo come PNG verificato (vedi [Report del modello](#report-del-modello)).
- **Privacy:** le coordinate lasciano il computer solo per QMEAN su SWISS-MODEL, scelto o come fallback, e
  sempre con il consenso esplicito all'invio. QMEAN locale, BLAST+ e MMseqs2 non usano la rete.
- **AlphaFold DB:** l'importazione nella libreria e il motore `alphafold` inviano a EBI (alphafold.ebi.ac.uk) solo
  l'accessione UniProt, mai la sequenza né le coordinate. Senza accessione il motore invia la sequenza a NCBI
  (QBlast su Swiss-Prot), come il motore `ncbi`; nella pipeline ciò vale per le sequenze senza intestazione FASTA di
  UniProt. I file si scaricano solo da URL `https://alphafold.ebi.ac.uk/files/…`, qualunque URL contenga la risposta.
- **File caricati:** sequenze e allineamenti (`/api/files/sequences`) vengono solo letti e non salvati. Le
  strutture (`/api/files/structure`) vanno tra gli upload, validate e soggette alla pulizia automatica.

## Test

- **`backend/tests` (pytest), circa 400 test,** eseguiti sia nativamente sia nel container:
  - dominio, job manager, API, generazione script, MODELLER end-to-end;
  - ripresa dei job e delle pipeline (`test_jobs.py`, `test_pipeline_resume.py`): solo gli elementi non
    conclusi, riavvio simulato, ambito "solo i falliti", pipeline completate con falliti, file vecchi eliminati,
    richieste concorrenti, nuovo annullamento, progresso, elementi completati solo a file salvati (disco pieno,
    modelli mancanti), eliminazione concorrente a una ripresa;
  - flusso dati: scratch, salvataggio definitivo, intermedi, blocco SQLite, pulizia, gruppi di processi;
  - licenze e accesso;
  - parser di PROCHECK e ProSA tarati su output reali di 1UBQ;
  - MMseqs2 e QMEAN locale con worker simulato;
  - riconoscimento di tutti i formati di UniClust30 (`test_uniclust_discovery.py`);
  - scelta della versione e fallback su SWISS-MODEL (`test_qmean_fallback.py`), memoria richiesta dal database
    (12 GB misurati per UniRef30_2020_06, stima per le altre versioni, giudizio del worker, tolleranza del 95%): job
    fallito subito o fallback con 8 GB, partenza con 11,7 GB; due QMEAN locali eseguiti uno alla volta, il secondo in
    coda con posizione e fase, senza bloccare il pool "io"; QMEAN su SWISS-MODEL senza esclusione, e nemmeno senza
    servizio locale (due fallback insieme, nessuna fase di coda); servizio locale restituito prima del fallback (un
    secondo QMEAN locale parte e finisce mentre il primo aspetta SWISS-MODEL); nota sulla memoria con l'esito
    (`memory_required.sufficient`, anche quando decide il giudizio del worker);
  - lettura dei formati di sequenza e caricamento di strutture (`test_fileformats.py`);
  - consenso tra i metodi (`test_consensus.py`):
    - scale, pesi, fasce, metodi mancanti, discordanze;
    - segnali per residuo, regioni con tolleranza, confini di catena e numerazione;
    - QMEAN e ProSA per residuo;
    - 1UBQ con le uscite reali di MODELLER (`dope_1ubq.profile`), PROCHECK e ProSA;
    - rotta con QMEAN simulato (stessa struttura, altra struttura, template sostituito con lo stesso id, job non
      QMEAN o non completato) e modelli MODELLER simulati;
    - riconoscimento del pLDDT (intestazione AlphaFold sintetica, ModelCIF, nome di AlphaFold DB, file normale,
      valori fuori scala; strutture sperimentali con `REMARK 200 STARTING MODEL: ALPHAFOLD` o
      `_pdbx_initial_refinement_model` AlphaFold, modello MODELLER con un nome `AF-…`) e stesso esito per libreria,
      rapporto di qualità e copia preparata del template sugli stessi file;
  - AlphaFold DB (`test_alphafold.py`, EBI simulato con `httpx.MockTransport`, NCBI con monkeypatch): parser della
    risposta (campi mancanti, più voci e frammenti, versioni, URL estranei), 404, accessioni non valide senza
    richieste, cache e nuovi tentativi, download PDB/mmCIF, libreria (id, meta, record originali, frammenti, file
    caricati, metadati precedenti completati alla prima lettura), annullamento durante i tentativi del download e
    durante l'attesa dello stesso modello, motore con accessione e con BLAST simulato (estrazione delle accessioni,
    identità 96,1% e copertura su 1UBQ contro l'ubiquitina di lievito; accessione senza modello o EBI non
    disponibile = job fallito con il motivo, risultati parziali con le note e senza cache), cache dei calcoli,
    `best_template` a parità, ritaglio per pLDDT su 1UBQ con B-factor riscritti (11 residui tolti, soglia compresa,
    minimo 30, frammenti isolati di 1 e 3 residui tolti e di 6 tenuti), PIR riportato sul ritaglio, pipeline
    (accessioni da `sp|` e `tr|`) e, con MODELLER, modellazione e pipeline con template ritagliato;
  - cache dei calcoli (`test_compute_cache.py`): riscontri senza rieseguire i motori (simulati), invalidazione per
    parametri, sequenza, timbro del database o dello strumento, scadenza, file corrotti, job falliti e annullati,
    riuso nella pipeline e scritture concorrenti;
  - confronto strutturale (`test_comparison.py`, integrato e TM-align/US-align veri dell'immagine): lDDT calcolato a
    mano (tre Cα, residuo senza corrispondente, raggio e soglie esclusi), invarianza al moto rigido, GDT massimizzato
    per soglia (due gruppi rigidi), 1UBQ contro sé stessa e contro una copia ruotata e traslata (TM 1, GDT 100, lDDT 1,
    matrice e PDB sovrapposto con A e B al loro posto), tratto 31–37 spostato di 6 Å (distanze e lDDT bassi solo lì,
    valori di riferimento), catena A contro B di `ubq_dimer.pdb`, residuo inserito (35A) e Cα mancante (lDDT uguale
    alla formula ricavata a parte), accordo fra i percorsi, colonne non allineate di TM-align (tratto a 12 Å), uscite
    anomale simulate (residui che non corrispondono, matrice mancante), lettura delle due forme della matrice e
    invalidazione delle voci di cache precedenti;
  - tempo residuo e flussi SSE (`test_live.py`): formule dell'ETA (arrotondamento, soglie, mai negativa), pipeline con
    durate note e dopo una ripresa, job con progresso; notifiche fra thread; righe intere del log; flusso di un job
    con TestClient (stato, log contiguo, fine, offset, 404, ripresa), limite dei flussi, flusso dell'elenco (job,
    elementi di pipeline, filtro per progetto, eliminazioni, heartbeat), chiusura all'arresto, accesso con chiave
    (401 senza cookie, 200 con cookie o Bearer); con un server uvicorn vero: eventi mentre il job è fermo,
    chiusura dopo `end`, posto liberato alla disconnessione del client, arresto non bloccato dai flussi aperti.
    I test aspettano le condizioni, non tempi fissi: un flusso in attesa di notifiche (`ChangeFeed.waiting`) ha
    già inviato tutto ciò che era stato pubblicato prima;
  - coda con priorità e budget di CPU (`test_scheduling.py`), con processi veri che scrivono inizio e fine: mai più
    processi contemporanei degli slot (da job diversi e da thread dello stesso job), annullamento durante l'attesa,
    timeout non consumato dall'attesa e ancora valido per il processo, impostazione dei job in parallelo cambiata
    senza riavvio (budget e pool "cpu"), programmi multi-thread su più slot (fissi ed elastici: questi prendono subito
    gli slot liberi), processi di I/O fuori dal budget, ordine
    per priorità senza sorpassi, invecchiamento con un orologio simulato (budget e coda: una richiesta di pipeline
    servita entro 2 livelli anche con richieste interattive continue, FIFO a parità di priorità effettiva), fase "In
    attesa di CPU libera" (job, elemento di pipeline, figlio senza fase, passi in parallelo),
    ordine di esecuzione per tipo con un solo worker e FIFO a parità, risorsa esclusiva restituita prima della fine
    (il job successivo parte, la chiusura non libera la risorsa presa da un altro), ripresa che torna al suo posto,
    annullamento e
    ripresa di un job in coda, job in coda all'arresto marcati da `recover`, eventi invariati, posizione nel flusso
    dell'elenco, pipeline con 4 elementi in parallelo su 2 slot senza deadlock;
  - analisi in parallelo e template preparati (`test_parallel_quality.py`): `Steps` (ordine dei risultati diverso da
    quello di conclusione, modalità seriale, passo fallito isolato, annullamento durante l'attesa con passi in coda che
    non partono); rapporto in parallelo identico a quello seriale su 1UBQ (favorite 95,9 %, clashscore 1,66, zDOPE
    −2,73, PROCHECK core 95,5 %), `ubq_dimer.pdb`, `ubq_zn.pdb` e un modello di MODELLER; avvisi dei passi falliti
    uguali, nello stesso ordine, e passo lento non bloccato; annullamento con tre processi veri da 60 s tutti terminati
    in pochi secondi e nessun thread rimasto; mkdssp solo da `ctx.run` con la stessa assegnazione del percorso di
    Biopython; prefissi dei passi nel log; qualità "all" con 3 modelli insieme uguale a quella seriale; cache dei
    template (copia per elemento, chiave con template, catene, ligandi e soglia pLDDT, file sostituito, errori non
    conservati, elementi concorrenti, attesa annullabile) e pipeline di 4 sequenze con una sola preparazione;
  - report del modello (`test_report.py`, MODELLER simulato con le coordinate e il profilo DOPE reali di 1UBQ, più un
    test con MODELLER reale): tutte le sezioni; identità 96,1% dell'ubiquitina di lievito su 1UBQ, voto 98,9,
    consenso 99,6; SVG ben formati con 74 punti di Ramachandran e il picco DOPE 73–76; escape di `<script>`, `"` e `&`
    in titoli, nomi, metadati, residui e atomi; PNG valido incorporato, PNG malformati (firma, CRC, troncati, dati
    dopo IEND, senza IDAT, altri tipi) e oltre 3 MB rifiutati; modelli non del job, falliti, job non completato;
    loop; report di un job quality con QMEAN simulato (70–76 sotto 0,6); ZIP della pipeline con un report per
    elemento completato e rotta del report di un elemento; nomi unici nello ZIP ("abc" e "abc-", "x", "x", "x_2", job
    precedenti con nomi ripetuti); nomi di file sicuri;
  - ligandi, cofattori e metalli (`test_ligands.py`): classificazione (ZN metallo, HOH esclusa, GOL e SO4 additivi, HEM
    cofattore, Na⁺ monovalente, MSE residuo modificato) anche nel dettaglio del template; geometrie ideali e incomplete;
    sito Zn di `ubq_zn.pdb` (valori di riferimento), Zn spostato nella proteina (scontro), distanze lunghe e troppo corte;
    copia preparata con i soli gruppi scelti in fondo alla catena e il sito del file originale; chiave dei template
    preparati che cambia con la scelta; opzioni (validazione, template obbligatorio con più template, pipeline
    automatica); colonne '.' del PIR (automatico, fine esplicita e "+N", '.' dell'utente tolti, catene, più template,
    errori); confronto con il sito del template (conservato, sostituito, non allineato, allineamento che non
    corrisponde); rapporto di qualità e report HTML solo con ligandi; con MODELLER, ubiquitina di lievito su
    `ubq_zn.pdb` con il solo Zn trasferito (PIR, HETATM, verifica, report) e lo stesso PIR con i ligandi spenti;
  - verifica dell'installazione.

  `pytest -m network` prova i servizi reali. Oltre a 1UBQ, `tests/data` contiene strutture sintetiche generate
  da `make_synthetic.py`: `ubq_dimer.pdb` (omodimero C2 con interfaccia di contatto) e `ubq_zn.pdb` (sito Zn²⁺
  su His68 e due acque).
- **`frontend/src/**/*.test.ts(x)` (vitest):**
  - parser PIR e formattazione (compresa la nota "dalla cache");
  - logica QMEAN locale/remoto/fallback e avvisi delle licenze;
  - pulsanti di ripresa delle pipeline e ricarica del job quando cambia stato, dopo l'annullamento e dopo la
    ripresa;
  - posizione in coda ("2° in coda", solo per i job in coda), slot di calcolo in uso (`format.test.ts`), memoria del
    servizio QMEAN rispetto al requisito, con l'esito del server (sufficiente, insufficiente, al limite, 11,7 GB su 12,
    giudizio "ok" del worker al posto di una stima più alta: `status.test.ts`);
  - tempo residuo leggibile (`fmtEta`); scelta fra flusso e polling, riconnessione con backoff, timeout di
    connessione, fine del flusso, flusso condiviso dell'elenco, ricomposizione del log (anche con polling e flusso
    sovrapposti) e aggiornamento degli elenchi, anche dopo una ricarica lenta, niente polling per un job che il server
    non ha (404) (`live.test.ts`, con flussi e timer finti);
  - decodifica dei file, chiave MODELLER da file, vincoli da e verso file;
  - scale di colore e legende (`colorscale.test.ts`, fasce con limite escluso o compreso), conversione del rapporto
    nei valori per residuo, pLDDT compreso con le fasce del backend (`residueColoring.test.ts`), residui
    problematici e struttura del rapporto mostrato (`qualityReport.test.ts`), confidenza degli allineamenti
    (`alignConfidence.test.ts`);
  - confronto strutturale (`compare.test.ts`): sorgenti nei link (andata e ritorno, valori non validi), destinazioni
    di «Confronta…» dai modelli, scala delle distanze saturata a 5 Å, colore fisso di A e colore per catena, coppia di
    un residuo scelto nel 3D, interruzioni del grafico (salti e codici di inserzione), tratti che deviano, ordinamento e
    filtro della tabella, lettura del TM-score, scorrimento del solo contenitore della tabella alla riga selezionata,
    risultati di versioni precedenti riconosciuti da `comparison_version` (non dall'assenza di una nota), job del link
    non trovato nella pagina Confronto;
  - selettore della struttura di un job (`picker.test.ts`): job e modello arrivati da un link (elemento di pipeline,
    job loop, job non tra gli ultimi 30) restano visibili come scelti, risultati senza elenco dei modelli;
  - azioni degli elementi di pipeline (`PipelineItemActions.test.tsx`, markup statico di `react-dom/server`): report
    del modello in ogni riga completata, disattivato per le altre, e report con la vista 3D dalla finestra
    «Visualizza».
  - AlphaFold (`alphafold.test.ts`): accessioni e id `AF-…`, accessione precompilata che segue la sequenza,
    intestazioni `sp|`/`tr|` della pipeline, fasce di pLDDT, quando mostrare la soglia, riepilogo del ritaglio
    (anche con i frammenti isolati).
  - report (`report.test.ts`): vista 3D inviata solo se è un PNG entro 3 MB, motivo della vista mancante solo
    nell'avviso del salvataggio riuscito, nome del file da Content-Disposition (senza percorsi) e nome di riserva come
    quello del server.
  - ligandi (`ligands.test.ts`): candidati delle catene scelte, additivi e ioni monovalenti deselezionati per
    default, residui modificati non selezionabili, scelte dell'utente conservate, analisi in cache precedenti, testi
    della coordinazione, linee metallo → donatore colorate per esito (con sfera per le acque) e residui evidenziati.
- **Profilazione:** `scripts/test.sh backend tests/perf_timing.py -s` (fuori dalla suite, vedi
  [Prestazioni](#prestazioni)).
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
