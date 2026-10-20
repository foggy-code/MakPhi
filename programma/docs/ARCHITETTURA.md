# Architettura di BioLoom v4

Questo documento è per
chi sviluppa: come è fatto il programma e perché, con le misure e i valori di riferimento dei test. L'uso è descritto nella
[Guida](../../Guida/1%20-%20Primo%20avvio.md), l'evoluzione per fasi (le sigle F0–F9 usate qui) nel [piano di
implementazione](PIANO_IMPLEMENTAZIONE.md).

I percorsi di questo documento (`backend/`, `docker/`, `launcher/`, `scripts/`…) sono relativi alla cartella
`programma/`, e da lì partono anche i comandi; la cartella di BioLoom che la contiene ha gli avviatori, `LEGGIMI.txt`,
`Guida/`, `data/` e `immagini/` (vedi [Backend](#backend)).

**Obiettivi:**

- modellazione di proteine per omologia, dalla sequenza al modello validato;
- tutto il programma in container Docker, portatile tra sistemi operativi, chiavetta e server;
- contratti API unici e verificati;
- metriche di qualità verificate su strutture di riferimento (senza falsi positivi su prolina e acque);
- funzionamento anche senza gli strumenti esterni facoltativi.

## Indice

- [Vista d'insieme](#vista-dinsieme): [container e volumi](#container-e-volumi) · [cartella dati](#cartella-dati) ·
  [flusso dei dati e file temporanei](#flusso-dei-dati-e-file-temporanei) ·
  [versione, impronta e aggiornamento delle immagini](#versione-impronta-e-aggiornamento-delle-immagini) ·
  [costruzione riproducibile dell'immagine](#costruzione-riproducibile-dellimmagine) ·
  [avviatori: un BioLoom alla volta e controlli all'avvio](#avviatori-un-bioloom-alla-volta-e-controlli-allavvio)
- [Backend](#backend): [pacchetti](#pacchetti) · [job](#job) ·
  [coda con priorità e budget di CPU](#coda-con-priorità-e-budget-di-cpu) ·
  [ripresa delle pipeline](#ripresa-delle-pipeline) ·
  [tempo residuo e aggiornamenti in tempo reale](#tempo-residuo-e-aggiornamenti-in-tempo-reale) ·
  [cache dei calcoli](#cache-dei-calcoli) ·
  [libreria dei template: caricamento e sostituzione](#libreria-dei-template-caricamento-e-sostituzione) ·
  [migrazioni dello schema](#migrazioni-dello-schema) ·
  [licenze dei programmi esterni](#licenze-dei-programmi-esterni)
- [Calcoli](#calcoli), nell'ordine del percorso dalla sequenza al modello:
  - sequenza e template: [disordine, bassa complessità e domini](#disordine-bassa-complessità-e-domini) ·
    [ricerca di template](#ricerca-di-template) · [scelta ragionata dei template](#scelta-ragionata-dei-template) ·
    [modelli AlphaFold DB](#modelli-alphafold-db) · [allineamenti](#allineamenti)
  - modellazione: [MODELLER](#modeller) ·
    [più template: salign o align2d uniti sul bersaglio](#più-template-salign-o-align2d-uniti-sul-bersaglio) ·
    [ligandi, cofattori e metalli](#ligandi-cofattori-e-metalli) · [complessi e omo-oligomeri](#complessi-e-omo-oligomeri)
  - qualità: [validazione di qualità](#validazione-di-qualità) ·
    [QMEAN locale con qualunque versione di UniClust30](#qmean-locale-con-qualunque-versione-di-uniclust30) ·
    [consenso tra i metodi e qualità per residuo](#consenso-tra-i-metodi-e-qualità-per-residuo) ·
    [confronto strutturale](#confronto-strutturale)
  - analisi del modello: [analisi strutturale](#analisi-strutturale) ·
    [tasche, elettrostatica e rifinitura energetica](#tasche-elettrostatica-e-rifinitura-energetica) ·
    [mutagenesi in silico e scansione di alanina](#mutagenesi-in-silico-e-scansione-di-alanina) ·
    [confronto con un predittore esterno](#confronto-con-un-predittore-esterno)
  - risultato: [report del modello](#report-del-modello)
- [Funzioni trasversali](#funzioni-trasversali): [controlli preventivi](#controlli-preventivi) ·
  [procedura guidata](#procedura-guidata) · [etichette, preferiti e ricerca globale](#etichette-preferiti-e-ricerca-globale) ·
  [riproducibilità: pacchetti e riesecuzione](#riproducibilità-pacchetti-e-riesecuzione) ·
  [eliminazione di progetti e pulizia dei job](#eliminazione-di-progetti-e-pulizia-dei-job)
- [Prestazioni](#prestazioni): [misure per i controlli preventivi](#misure-per-i-controlli-preventivi)
- [Frontend](#frontend)
- [Sicurezza e privacy](#sicurezza-e-privacy)
- [Test](#test)
- [Fuori perimetro](#fuori-perimetro)

## Vista d'insieme

### Container e volumi

```
Computer ospite (Windows, macOS, Linux; Apple Silicon in emulazione x86)
  Avvia BioLoom.bat/.command, avvia-bioloom.sh ─▶ programma/launcher/bioloom.ps1 | bioloom.sh ─▶ docker compose (progetto "bioloom")
    · trova UniClust30/QMTL sui dischi · legge il MAC da LicenseDB · porta libera · carica immagini/ o le costruisce
    · versione da launcher/VERSION · impronta del codice confrontata con /app/IMPRONTA · ProSA un computer alla volta
    · un BioLoom alla volta per computer (le copie hanno gli stessi container)

Container "bioloom" (Ubuntu 24.04, Python 3.12, utente = proprietario di /data)
  Browser ──HTTP──▶ FastAPI (8765; pubblicata su 127.0.0.1, anche sul server; in rete solo con chiave d'accesso)
                     ├─ /api/*   rotte REST (OpenAPI su /docs)      ├─ /*  interfaccia React compilata
                     ├─ JobManager: code con priorità "cpu" (core−1) + "io" (8 + posti QMEAN) · budget di CPU condiviso · stato su SQLite · lavoro in /scratch
                     │    └─ sottoprocessi: MODELLER 10.8, mkdssp, BLAST+, MMseqs2, Clustal Omega, MAFFT, MUSCLE,
                     │                      TM-align, US-align, PROCHECK, ProSA II, fpocket, PDB2PQR, APBS, OpenMM
                     └─ servizi remoti: RCSB · UniProt · InterPro · NCBI QBlast · AlphaFold DB (EBI) · SWISS-MODEL (opt-in)
                                        · ESMFold (ESM Atlas, opt-in)
  /data     ◀─ bind mount  cartella data/ (chiavetta, cartella o server): viaggia con BioLoom
  /scratch  ◀─ volume      lavoro dei job e file temporanei (veloce, svuotato a ogni avvio)
  /cache    ◀─ volume      indici ricostruibili di questo computer (MMseqs2)

Container "qmean" (profilo compose "qmean", immagine ufficiale QMEAN 4.3.0 + worker HTTP)
  rete interna :8080 (nessuna porta pubblicata) · /uniclust_src e /qmtl in sola lettura dal disco esterno
  /cache ◀─ volume qmean_cache: copia locale di indici e cs219 di UniClust30, cs219 ricreati per i database HH-suite 2
```

Le versioni degli strumenti sono fissate in `docker/Dockerfile` (immagini di base per digest, file scaricati verificati
con SHA-256: vedi [Costruzione riproducibile dell'immagine](#costruzione-riproducibile-dellimmagine)) e
`backend/requirements-tools.lock`; PROCHECK e ProSA (pacchetti con licenza) arrivano da `docker/vendor/` e mancano
nelle immagini costruite senza.

### Cartella dati

```
data/
  bioloom.db          progetti, sequenze, documenti, job, elementi di pipeline, etichette e preferiti (SQLite, WAL esclusivo, schema versionato)
  backups/            copie di sicurezza di bioloom.db fatte prima di ogni migrazione dello schema (mai eliminate da sole)
  jobs/<id>/          params.json, job.log, result.json e i soli risultati (niente intermedi MODELLER)
  jobs/.deleting-<id>/   resto di un job eliminato (o mai accodato) con file ancora in uso, tolto dalla pulizia automatica
  jobs/.rerun-incoming/  pacchetti di riproducibilità in arrivo, spostati nel job alla sua creazione
  templates/          libreria globale (ID.pdb|cif + ID.meta.json + ID.analysis.json in cache; .upload-*/ file in arrivo)
  uploads/            strutture caricate per l'analisi (pulizia automatica)
  cache/              risposte dei servizi remoti (uniprot, uniprot_variants, interpro, rcsb_meta, alphafold, esmfold)
                      e cache dei calcoli in cache/compute (pulizia automatica; cache/compute anche sotto un tetto)
  licenses/           modeller.key, prosa/LicenseDB (permessi 0600, mai nelle immagini); prosa/computer, il computer
                      a cui è legata ogni licenza ProSA (scritto dagli avviatori, vedi Licenze dei programmi esterni)
  databases/          pdbaa (BLAST+), pdbaa_mmseqs (database MMseqs2 senza indice); durante un aggiornamento
                      anche le cartelle di lavoro <nome>.new-<job> e <nome>.old-<job>
  tools/              facoltativa: eseguibili aggiunti ai percorsi in cui `engines/tools` cerca gli strumenti
  access_key          chiave d'accesso generata in modalità server, se BIOLOOM_ACCESS_KEY è vuota
  session_epoch       epoca delle sessioni della modalità server (un intero; «Esci da tutti i dispositivi» la incrementa)
  settings.json       impostazioni utente
```

Nel container `BIOLOOM_DATA_DIR=/data`, `BIOLOOM_SCRATCH_DIR=/scratch` e `BIOLOOM_CACHE_DIR=/cache`. Fuori da Docker
(sviluppo con `python bioloom.py`) la cartella dati è `data/` accanto a `programma/`, lo scratch `data/.scratch` e la cache locale
`data/.localcache`, salvo le stesse variabili.

I risultati dei job (`jobs/<id>`) si eliminano solo a mano: dal dettaglio del job, con «Pulisci job…» nella pagina
Job o «Elimina job…» in Impostazioni → Spazio e file temporanei, oppure eliminando il progetto (vedi [Eliminazione di
progetti e pulizia dei job](#eliminazione-di-progetti-e-pulizia-dei-job)). `backups/` non si elimina mai da sola: lo
spazio occupato la mostra. Il file `bioloom.db` non si restringe dopo un'eliminazione di massa: nessun VACUUM, per
scelta (servirebbe circa il doppio dello spazio del database e bloccherebbe l'unica connessione); SQLite riusa le
pagine liberate per i dati nuovi.

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
  cartella dati (errore chiaro). Lo schema ha una versione e si aggiorna all'avvio con migrazioni non distruttive,
  dopo una copia di sicurezza in `backups/` (vedi [Migrazioni dello schema](#migrazioni-dello-schema)).
- **Pulizia automatica** (`services/housekeeping.py`), all'avvio, ogni 24 ore e con «Pulisci file temporanei» in
  Impostazioni → Spazio e file temporanei (`POST /api/system/cleanup`):
  - upload più vecchi dei giorni impostati (`uploads_retention_days`, predefinito 30; 0 = mai);
  - cache remota e cache dei calcoli oltre i 30 giorni (`CACHE_MAX_AGE_DAYS`, più del TTL più lungo), più i file
    temporanei di scritture interrotte;
  - scratch orfano (cartelle di job non attivi: si conservano tutti i job attivi, letti in SQL senza limite di numero,
    e quelli con l'esecuzione o una ripresa ancora in corso, `busy`) e file di `scratch/tmp` oltre le 24 ore, come i
    pacchetti caricati per una riesecuzione;
  - resti di job già eliminati dall'utente: le cartelle `jobs/.deleting-<id>` rimaste per file in uso, solo se la riga
    del job non c'è più (`JobManager.purge_tombstones`, chiave `jobs` del rapporto, byte in `freed_bytes`).

  Risultati dei job, libreria dei template, database e licenze non vengono mai toccati. Nessun passo elimina cartelle
  `jobs/<id>` senza riga: dopo un ripristino del database da `backups/` o una copia parziale della cartella dati
  (`Database` crea in silenzio un file nuovo se manca) sarebbero risultati veri. Un elemento che non si riesce a
  eliminare (file in uso) non ferma la pulizia e non si conta (si riprova alla volta successiva); se un passo fallisce
  (per esempio una cartella illeggibile) i resti dei job eliminati si tolgono comunque, e la pulizia all'avvio non
  impedisce mai l'avvio. Il rapporto ha le chiavi `uploads`, `cache`, `scratch`, `jobs`, `freed_bytes` e `at`, con i
  soli elementi eliminati davvero; `GET /api/system/storage` mostra anche `backups` (solo lettura).
- **Eliminazione e pulizia manuale (job e progetti):** i risultati dei job si eliminano solo su richiesta e con
  conferma: un job dal dettaglio (`DELETE /api/jobs/{id}`), i job terminati in blocco con «Pulisci job…» (criteri,
  anteprima, ciclo a tempo) e un progetto con tutti i suoi job dal dialogo «Progetti». L'eliminazione è atomica sul
  disco (cartella rinominata in `.deleting-<id>` prima del DELETE della riga) e dice se qualcosa è rimasto
  (`leftover`); vedi [Eliminazione di progetti e pulizia dei job](#eliminazione-di-progetti-e-pulizia-dei-job).
- **Processi esterni:** ognuno ha il proprio gruppo di processi; annullamento e timeout terminano anche i
  figli (mafft e PROCHECK sono script), e così un errore durante la lettura dell'output (log o progresso su disco
  pieno) prima che arrivi all'handler: nessun processo resta orfano fuori dal budget di CPU. Il timeout dei job vale
  per tutti gli strumenti. I processi di calcolo
  prendono gli slot del budget di CPU condiviso fra i job (vedi [Coda con priorità e budget di
  CPU](#coda-con-priorità-e-budget-di-cpu)).
- **MMseqs2:** il database (≈ 140 MB) sta nei dati. L'indice k-mer (≈ 1,5 GB) sta nel volume `/cache` di
  ogni computer, con un timbro dimensione/data che lo invalida quando il database cambia.

  | Ricerca | Tempo misurato |
  |---|---|
  | indice nel volume locale | 2,3 s |
  | indice sul bind mount | 22 s |
  | senza indice | 12 s |

### Versione, impronta e aggiornamento delle immagini

**Versione unica.** La fonte è `launcher/VERSION` (4.2.0). Gli avviatori (`bioloom.sh`, `bioloom.ps1`,
`bioloom-podman.sh`) e `scripts/test.sh` la leggono da lì, senza numeri di riserva: se il file manca si fermano con un
errore (`test.sh` no, se ha `BIOLOOM_BASE_IMAGE`). Gli avviatori la esportano come `BIOLOOM_VERSION` prima di ogni `docker compose`: nell'interpolazione di compose
le variabili della shell prevalgono su `--env-file`, quindi una `BIOLOOM_VERSION` rimasta in `.env.locale` o in un
`server.env` di una versione precedente non conta (per `server.env` l'avviatore avvisa che è ignorata);
`bioloom-podman.sh` legge `server.env` variabile per variabile e fa prevalere `VERSION`. `server.env.example` non ha più
la variabile. Le copie letterali, per chi non passa dagli avviatori:

| File | Copia |
|---|---|
| `backend/pyproject.toml`, `backend/bioloom/__init__.py` | `version`, `__version__` |
| `frontend/package.json`, `frontend/package-lock.json` | `version` del progetto (nel lock i due del progetto, in cima e nel pacchetto `""`, non quelli delle dipendenze) |
| `docker/Dockerfile` | `ARG BIOLOOM_VERSION=…`, predefinito delle costruzioni senza `--build-arg` |
| `docker/test/Dockerfile` | `ARG BASE=bioloom:…` |
| `launcher/compose.yml` | i due `${BIOLOOM_VERSION:-…}` delle immagini `bioloom` e `bioloom-qmean` |

`bash scripts/versione.sh` stampa la versione e controlla le copie (se differiscono le elenca su stderr ed esce con 1);
`bash scripts/versione.sh X.Y.Z` porta `VERSION` e tutte le copie a X.Y.Z conservando i fine riga, e scrive solo se
in ogni file trova il numero di copie atteso (altrimenti non tocca nulla). Si lancia con `bash` davanti perché il bit
eseguibile manca se lo script entra nel repository da Windows (`core.filemode`). `tests/test_version.py` e la CI
ripetono il controllo. `compose.yml` passa `BIOLOOM_VERSION` all'immagine `bioloom` come build arg senza valore
(se non è impostata vale il predefinito del Dockerfile), così `ENV BIOLOOM_IMAGE_VERSION` (in `/api/system/info` e
nel manifest dei pacchetti di riproducibilità) e `LABEL bioloom.version` sono quelli veri anche costruendo con compose;
`bioloom-podman.sh` usa `podman build --build-arg` (con `BUILDPLATFORM=linux/amd64`, vedi [Costruzione riproducibile
dell'immagine](#costruzione-riproducibile-dellimmagine)). La versione entra nella chiave della
[cache dei calcoli](#cache-dei-calcoli), che quindi riparte a ogni nuova versione.

**Impronta del codice.** Riconosce un'immagine che non corrisponde al codice anche a parità di versione (`programma/`
sostituito o modificato senza ricostruire). Definizione, identica nell'implementazione di riferimento
`docker/impronta.sh` (sh POSIX: dash nella build, bash 3.2 di macOS, Git Bash) e in `launcher/impronta.ps1`
(`Get-BioLoomImpronta([string]$Root)`, Windows PowerShell 5.1 e pwsh 7, `$null` se non può calcolarla; `bioloom.ps1` la
carica con dot-source):

1. radice `programma/` (il contesto della build);
2. file regolari sotto `backend/`, `frontend/` e `docker/`, esclusi: ogni percorso con un segmento che inizia con `.`
   (file e cartelle nascosti: `.gitkeep`, `.venv`, `.ruff_cache`, `.DS_Store`…), uguale a `node_modules` o
   `__pycache__` o che finisce in `.egg-info`; i file `*.pyc`; i prefissi `frontend/dist/`, `docker/vendor/`,
   `docker/qmean/`, `docker/test/`, `backend/tests/`;
3. percorsi relativi alla radice con `/`, ordinati per byte UTF-8 (`LC_ALL=C sort`; in PowerShell sulle chiavi
   esadecimali dei byte, perché l'ordinale di .NET confronta unità UTF-16 e sbaglia i caratteri fuori dal piano base);
4. per ogni file la riga `<SHA-256 del contenuto senza i byte CR>  <percorso>` più LF (esadecimale minuscolo, due
   spazi): i CR si tolgono perché nella copia di Windows i file di testo sono CRLF;
5. impronta = SHA-256 (64 cifre esadecimali minuscole) di queste righe in UTF-8.

`sh impronta.sh <radice>` stampa solo l'impronta (uscita 2 = uso sbagliato, 3 = nessuno strumento per l'SHA-256, 1 =
altro errore). Con perl e `Digest::SHA` calcola tutto in un processo (0,3 s in Git Bash); altrimenti segue la via in sh,
con `sha256sum`, `shasum -a 256` o `openssl dgst -sha256` (27 s in Git Bash, dove creare processi costa;
`IMPRONTA_SENZA_PERL=1` la forza). `find` usa `-prune` per non scendere in `node_modules`; PowerShell legge a blocchi
da 1 MiB saltando i CR (0,4 s). Nessuno dei due segue collegamenti simbolici o giunzioni.

Nella build lo stadio `impronta` (`UBUNTU_IMAGE`, Ubuntu 24.04 per digest, `COPY . /ctx`, script letto senza CR come
`entrypoint.sh`) scrive `/impronta`; l'immagine finale la copia in `/app/IMPRONTA` con `LABEL bioloom.version`, per
ultime, così una modifica del codice non invalida la cache dei livelli sopra. L'impronta c'è comunque si costruisca
l'immagine (avviatore, compose a mano, CI); la `.dockerignore` esclude solo cose che la definizione esclude già, quindi
il valore della build è quello dell'host (verificato con un albero Windows in CRLF). `docker/vendor/` non conta: dopo
aver aggiunto i pacchetti con licenza l'immagine si ricostruisce a mano. File di sistema come `Thumbs.db` o
`desktop.ini` dentro le tre cartelle invece contano.

Lettura dall'immagine: `docker run --rm --platform linux/amd64 --entrypoint cat <immagine> /app/IMPRONTA` (in Git Bash
con `MSYS_NO_PATHCONV`; con Podman `podman run --rm --health-cmd none`, senza `--platform`). File assente (immagini
precedenti: `cat` esce con 1) = impronta diversa; container che non parte = non si sa, controllo saltato; immagine
assente = nessun `run`, che la cercherebbe su Docker Hub. L'impronta del codice si calcola una volta per esecuzione
dell'avviatore; se non si può calcolare (strumenti assenti) i controlli si saltano in silenzio.

**Controllo all'avvio** (desktop, `bioloom.sh server`, `bioloom-podman.sh avvia`), dopo aver assicurato
`bioloom:<versione>`: con impronte diverse, sul desktop in modo interattivo l'avviatore chiede «Aggiornarla ora? [S/n]»
(Invio = sì; «[s/N]», Invio = no, con un avviso prima, quando ricostruirebbe senza i pacchetti di PROCHECK o ProSA:
nessun tar da caricare e pacchetti assenti in `docker/vendor/`) ed esegue il passo 1 di `aggiorna`; con no o se l'aggiornamento non riesce, con `--no-menu` / `-NoMenu` e
sul server avvisa con il comando da usare e parte con l'immagine esistente.

**Azione `aggiorna`**: `bioloom.sh aggiorna` (anche da `avvia-bioloom.sh`), `bioloom.ps1 -Azione aggiorna`,
`bioloom-podman.sh aggiorna`. `bioloom.sh` la inoltra a `bioloom-podman.sh` (con `--sposta-prosa`, se c'è) quando c'è
`podman` senza `docker compose` e la cartella di BioLoom ha `server.env`. Con Docker il riavvio usa il file con cui
BioLoom è partito (etichetta `com.docker.compose.project.environment_file` del container; un `.env.locale` minimo
senza porta vale come server; senza etichetta e con entrambi i file nessun riavvio automatico, con un messaggio).
Prima di ogni ricostruzione, se mancano i pacchetti di PROCHECK o ProSA, l'avviatore avvisa che la nuova immagine
non li avrà. L'avviatore PowerShell ha solo il desktop.

1. Immagine `bioloom`: se corrisponde già, nulla. Altrimenti carica `immagini/bioloom-<versione>.tar` (se non è già
   stato caricato nella stessa esecuzione) e, se ora l'impronta coincide, ha finito; altrimenti, se c'è
   `docker/Dockerfile`, la ricostruisce (`compose build bioloom` con `BIOLOOM_VERSION` esportata; Podman
   `podman build --build-arg BIOLOOM_VERSION`) e ricontrolla. Se alla fine non coincide avvisa, e l'azione esce con 1.
   L'immagine sostituita resta senza nome.
2. Immagine `bioloom-qmean`, solo se c'è già, se c'è il suo tar o se UniClust è configurato: carica il tar,
   altrimenti (o se il caricamento fallisce) `compose --profile qmean build qmean`. Un fallimento è solo un avviso.
3. Riavvio solo se un container in esecuzione usa un'immagine diversa da quella che ora ha il nome (`{{.Image}}` del
   container contro `{{.Id}}` dell'immagine), così i calcoli non si interrompono se non è cambiato nulla: desktop
   `compose up -d` con `.env.locale`, server come `bioloom.sh server`, Podman come `avvia`; prima, il controllo del
   BioLoom partito da un'altra cartella (vedi [Avviatori](#avviatori-un-bioloom-alla-volta-e-controlli-allavvio)). Con
   BioLoom fermo e un'immagine cambiata dice che verrà usata al prossimo avvio.

**`prepara`** salva le immagini prima di copiare i file: se `bioloom:<versione>` non corrisponde al codice la aggiorna
come al passo 1 (senza riavvio) e, se non ci riesce, si ferma prima di copiare, perché una copia portatile con immagine
vecchia e codice nuovo non deve uscire. Crea `.env.locale` se manca, che serve alla costruzione con compose.

### Costruzione riproducibile dell'immagine

`docker/Dockerfile` fissa tutto ciò che scarica, così due costruzioni dello stesso codice partono dagli stessi byte. I
pacchetti di Ubuntu no: si installano aggiornati, con le correzioni di sicurezza del giorno, e le versioni rilevate sono
in `THIRD_PARTY_NOTICES.md`.

| Cosa | Come è fissato | ARG |
|---|---|---|
| `node:22-slim` (Node.js 22.23.3) e `ubuntu:24.04` (24.04.5 del 2026-09-17) | digest dell'indice multi-architettura, nella forma `ubuntu@sha256:…` | `NODE_IMAGE`, `UBUNTU_IMAGE` (un solo punto per i tre stadi Ubuntu: `tools-build`, `impronta`, immagine finale) |
| US-align 20260527, fpocket 4.2.3 | commit del tag (`177cc8a`, `4bb0d84`) e SHA-256 del tar non compresso | `USALIGN_COMMIT`, `USALIGN_SHA256`, `FPOCKET_COMMIT`, `FPOCKET_SHA256`; `USALIGN_TAG` resta e si controlla con l'uscita di `USalign -h` («(Version 20260527)»); `FPOCKET_TAG` tolto (tag e commit nel commento) |
| MODELLER 10.8 (`modeller_10.8-1_amd64.deb`, 14.229.846 byte) | SHA-256 del `.deb` | `MODELLER_VERSION`, `MODELLER_SHA256` |
| APBS 3.4.1 | SHA-256 dello zip della release | `APBS_VERSION`, `APBS_SHA256` |
| immagine di base di QMEAN (`docker/qmean/Dockerfile`) | digest di `registry.scicore.unibas.ch/schwede/qmean` | — |

- **Tag e digest:** il tag non sta davanti al digest perché Podman 2.2.1 (server RHEL 8, `bioloom-podman.sh`) rifiuta i
  riferimenti con tutti e due («Docker references with both a tag and digest are currently not supported»); resta nel
  commento, come già in `docker/qmean/Dockerfile`.
- **US-align e fpocket per commit:** le loro release non hanno file allegati, solo gli archivi dei sorgenti che GitHub
  genera al volo senza garantire gli stessi byte del `.tar.gz` (la compressione è già cambiata, nel 2023). Un commit non
  si sposta come un tag, e `git archive` produce lo stesso tar a parità di commit: si scarica
  `…/archive/<commit>.tar.gz`, si decomprime e si verifica il tar.
- **Verifica:** `echo "<sha256>  <file>" | sha256sum -c -` nello stesso RUN del download. Un file cambiato ferma la
  costruzione con «<file>: FAILED» e «sha256sum: WARNING: 1 computed checksum did NOT match»; con `USALIGN_TAG`
  diverso dalla versione del sorgente si ferma sul controllo di `USalign -h`.
- **MODELLER:** nello stesso RUN verifica dello SHA-256, `apt-get update`, `KEY_MODELLER=XXXX apt-get install -y
  --no-install-recommends /tmp/modeller.deb` e cancellazione delle liste. Prima era `dpkg -i … || apt-get -f install
  -y`, ma il `.deb` (convertito con alien) non dichiara dipendenze: `dpkg -i` riusciva sempre e il ripiego non partiva
  mai, e senza le liste apt, cancellate dal livello precedente, non avrebbe potuto funzionare.
- **Piattaforma dell'interfaccia:** lo stadio `frontend` (JavaScript, indipendente dalla piattaforma) gira
  sull'architettura di chi costruisce: `FROM --platform=$FRONTEND_PLATFORM ${NODE_IMAGE}`, con `ARG BUILDPLATFORM` senza
  valore e `ARG FRONTEND_PLATFORM=${BUILDPLATFORM:-linux/amd64}`. BuildKit e Podman (anche 2.2.1, provato: `linux/amd64`)
  danno `BUILDPLATFORM` da sé; `linux/amd64` vale per un costruttore che non lo dà. Non un predefinito di
  `BUILDPLATFORM` stesso: con BuildKit scavalcherebbe il valore automatico (con `ARG BUILDPLATFORM=linux/s390x` lo stadio
  veniva costruito per s390x, quindi su un Mac ARM l'interfaccia si compilerebbe in emulazione). `bioloom-podman.sh`
  passa comunque `--build-arg BUILDPLATFORM=linux/amd64` all'immagine di BioLoom, per i buildah vecchi che non lo
  valorizzano.
- **`docker buildx build --check -f docker/Dockerfile .`:** nessun avviso. La seconda riga del file,
  `# check=skip=FromPlatformFlagConstDisallowed`, salta la regola sul `--platform=linux/amd64` costante, voluto perché
  l'immagine è solo amd64 (MODELLER per amd64, ProSA a 32 bit).
- **Aggiornare:** la procedura è nei commenti in testa al Dockerfile: `docker buildx imagetools inspect ubuntu:24.04`
  (o `node:22-slim`) e il «Digest» dell'indice, non quello di una piattaforma; per MODELLER e APBS la versione nell'ARG
  e lo `sha256sum` del file; per US-align e fpocket il commit del tag (`git ls-remote <repository> 'refs/tags/<tag>'
  'refs/tags/<tag>^{}'`: con il solo nome del tag esce solo l'oggetto di un tag annotato; se c'è la riga che finisce con
  `^{}`, il commit è quello) e `curl -fsSL <URL> | gzip -dc | sha256sum`. Poi una costruzione completa e le versioni
  nuove in `THIRD_PARTY_NOTICES.md`.
- **QMEAN:** l'immagine di base di `bioloom-qmean` sta solo nel registro del gruppo Schwede: se sparisse, `bioloom-qmean`
  non si potrebbe più costruire. La copia di sicurezza è il tar della copia portatile o un `docker save`, che
  `aggiorna` e l'avvio caricano da `immagini/` ([Guida → Copia di sicurezza dell'immagine
  QMEAN](../../Guida/5%20-%20Chiavetta%20e%20server.md#copia-di-sicurezza-dellimmagine-qmean)).

### Avviatori: un BioLoom alla volta e controlli all'avvio

**Un BioLoom alla volta.** Tutte le copie di BioLoom hanno lo stesso progetto compose (`name: bioloom` in
`compose.yml`), quindi gli stessi container e volumi: avviarne una ricreerebbe i container dell'altra con un'altra
cartella dati. Il nome non cambia da una copia all'altra, perché il volume `cache` con l'indice MMseqs2 resterebbe
orfano; gli avviatori guardano invece da dove è partito il BioLoom in esecuzione.

- **Docker** (`bioloom.sh`, `bioloom.ps1`): etichetta `com.docker.compose.project.working_dir` del container in
  esecuzione (`bioloom`, altrimenti uno del profilo `qmean`), cioè la cartella di `compose.yml` come la scrive compose
  sul computer (Docker Desktop: `C:\…\programma\launcher`), confrontata con il proprio `programma/launcher`. In bash con
  `-ef` dopo `cygpath -u` (Git Bash) o `wslpath -u` (WSL), quindi a meno di maiuscole e collegamenti; in PowerShell con
  `GetFullPath` e `-ieq`, che ignora maiuscole, `/` contro `\` e la barra finale. PowerShell legge etichette e mount con
  `{{json …}}`, perché la 5.1 toglie le virgolette interne agli argomenti dei programmi e `{{index … "etichetta"}}`
  non arriverebbe intero. La cartella dati dell'altra copia è il `Source` del mount su `/data`.
- **Podman** (`bioloom-podman.sh`): i container hanno nomi fissi e quelli creati dall'avviatore l'etichetta
  `bioloom.cartella=<cartella di BioLoom>`. Per quelli delle versioni precedenti, senza etichetta, si confronta la
  cartella montata su `/data` con `BIOLOOM_DATA_PATH` (nei messaggi: `<cartella dati> (cartella dati)`).

Il controllo si fa prima di avviare: dal desktop subito dopo la verifica della cartella dati (prima di ProSA e della
ricerca dei dischi), con `server` e, in `aggiorna`, solo quando c'è un riavvio. Il BioLoom di un'altra cartella si
arresta solo con il consenso: «Arrestarlo e continuare? [s/N]», chiesto come gli altri prompt se non c'è `--no-menu` /
`-NoMenu`. Invio, input chiuso o la modalità senza domande escono con 1 («[!] Avvio annullato: resta in esecuzione il
BioLoom di <cartella>. Per usare questa copia arrestalo prima (<comando> arresta) e riprova.», in `aggiorna` «Riavvio
annullato»), così la finestra del `.bat`/`.command` resta aperta. Con «sì» `compose --profile qmean down
--remove-orphans`; con Podman lo stop delle unità systemd di `servizio` (dell'utente, non della copia: l'avvio le
rigenera) e la rimozione dei container. In `aggiorna` dal desktop, arrestata l'altra copia, questa parte come un avvio
normale (porta, licenze, UniClust), perché il suo `.env.locale` può non venire da un avvio.

`arresta` ferma invece comunque il BioLoom in esecuzione e dice da quale cartella era partito («Il BioLoom in esecuzione
è partito da un'altra cartella: …», poi la cartella dati di quella copia): i container sono gli stessi, «Arresta
BioLoom» non fa domande, la cartella dell'altra copia può non esserci più (chiavetta tolta) e arrestare non cancella
nulla. `stato`, `log` e `verifica` danno lo stesso avviso. Limite noto: un BioLoom avviato da WSL (`/mnt/c/…`) e
controllato da Git Bash o PowerShell risulta di un'altra cartella anche se è la stessa; il contrario funziona.

**Rete del server (`BIOLOOM_BIND`).** `server.env.example` ha `BIOLOOM_BIND=127.0.0.1` (prima `0.0.0.0`), quindi anche
il `server.env` creato da `bioloom.sh server`; `bioloom-podman.sh` lo forzava già e ora lo prende dall'esempio.
Dagli altri computer si arriva con un reverse proxy HTTPS o un tunnel SSH. Sono locali `127.*`, `localhost` e `::1` (in
bash anche il valore vuoto, che per compose vale 127.0.0.1; la variabile della shell prevale su `server.env`, come per
compose). Alla creazione di `server.env` l'avviatore dice che BioLoom ascolterà solo su 127.0.0.1; a ogni avvio, con un
valore locale, stampa il comando del tunnel (`ssh -N -L <porta>:127.0.0.1:<porta> utente@server`), altrimenti avvisa
senza fermarsi: HTTP con chiave e cookie in chiaro e, con Docker o con Podman da root, porta pubblicata con regole NAT
che passano prima di firewalld e ufw. Con Podman senza root la porta è aperta da un processo dell'utente e il firewall
la filtra: la riga sul firewall manca. I `server.env` esistenti con `0.0.0.0` restano così, con l'avviso.

**Porta.** Dal desktop (`bioloom.sh`, `bioloom.ps1`) la prima libera partendo da quella dell'ultimo avvio
(`BIOLOOM_PORT` di `.env.locale`), poi le successive fino alla 8800 e da 8765: prima la ricerca partiva dall'ultima
usata e non tornava più alle porte libere più basse. La porta del BioLoom in esecuzione conta come libera (la dice
`compose port bioloom 8765`; se compose non la dice si suppone quella salvata). Un valore guasto in `.env.locale` vale
8765 (prima il cast `[int]` mandava in errore lo script PowerShell).

- **Tutte occupate:** l'avviatore si ferma prima di compose, con «Nessuna porta libera per BioLoom: [la N e ]le porte da
  8765 a 8800 sono tutte usate da altri programmi…» e il comando per vedere chi ascolta (`netstat -ano | findstr
  LISTENING`, `lsof -nP -iTCP -sTCP:LISTEN`, `ss -ltnp`). Prima proseguiva comunque e compose falliva con «address
  already in use» o «ports are not available».
- **`BIOLOOM_PORT` nell'ambiente:** porta fissa. Occupata → «La porta N (BIOLOOM_PORT) è già usata da un altro
  programma…»; non un numero da 1 a 65535 → «BIOLOOM_PORT=x non è una porta valida…». Finisce in `.env.locale` come
  porta dell'ultimo avvio, e l'avviatore la toglie poi dall'ambiente: compose preferirebbe la variabile della shell alla
  porta scritta in `.env.locale`, e prima BioLoom partiva su una porta mentre l'avviatore lo aspettava su un'altra, e
  `aggiorna` non lo riavviava com'era.
- **Server** (`bioloom.sh server`, `bioloom-podman.sh`): la porta fissa di `server.env` (o di `BIOLOOM_PORT`
  nell'ambiente, che per compose prevale, come prima) si controlla prima di caricare o costruire le immagini, con lo
  stesso messaggio («… (BIOLOOM_PORT in server.env) è già usata da un altro programma su <bind>…») e il valore
  validato. Per `BIOLOOM_BIND=0.0.0.0` (o vuoto, `localhost`, `::`) si guarda 127.0.0.1, dove risponde anche chi
  ascolta su tutte le interfacce. La porta del BioLoom acceso conta come libera anche qui.
- **Come si controlla in bash:** una connessione a `/dev/tcp/<host>/<porta>`, libera se nessuno accetta. In locale con
  `timeout 3` quando c'è: una porta chiusa rifiuta subito, mentre un programma bloccato con la coda piena terrebbe ferma
  la connessione per minuti (Linux scarta le richieste in più invece di rifiutarle). Allo scadere la porta conta come
  occupata: libera solo con un codice d'uscita diverso da 0 e da 124 e sotto 128 (il timeout di busybox esce con 143), e
  `{ …; } 2>/dev/null` toglie il «Terminated» di bash 3.2. Su Windows (Git Bash) niente timeout: lì una porta locale
  chiusa si rifiuta dopo circa 2,1 s, e con 3 s le porte libere rischierebbero di risultare occupate. Senza `timeout`
  (di solito macOS) la connessione resta senza limite, come prima; per un indirizzo di rete senza `timeout` decide
  Docker.
- **In PowerShell** (`Test-PortFree`): prima una connessione a 127.0.0.1 con 300 ms di attesa (su una porta chiusa
  Windows ritenta per circa 2 s), poi l'apertura con `TcpListener`, che trova anche le porte riservate da Windows
  (`netsh interface ipv4 show excludedportrange`). La connessione serve perché un programma in ascolto su 0.0.0.0 non
  impediva a `TcpListener` di aprire 127.0.0.1, e la porta risultava libera.
- **Limiti noti:** in Git Bash le porte riservate da Hyper-V non si vedono (solo la connessione); l'uscita di
  `podman port` su Podman 2.2.1 non è verificata (se non si legge, si suppone la porta del container stesso: nessun falso
  blocco).
- **Prove** (con docker finto, fuori dalla suite): 22 scenari di `bioloom.sh` nell'immagine `bash:3.2`, con porte occupate
  da listener veri (desktop, server, Podman, tutte occupate, `BIOLOOM_PORT` libera, occupata o non valida, ritorno da
  8800 a 8765, listener su 0.0.0.0, BioLoom già in esecuzione); 11 di `bioloom.ps1` con `docker.bat`. Sul codice
  precedente «tutte occupate», «`BIOLOOM_PORT` occupata», server e Podman arrivavano a compose, e in PowerShell non si
  vedeva il listener su 0.0.0.0.

**Attesa dell'avvio da server** (`start_server` di `bioloom.sh`, anche da `aggiorna` quando riavvia da server).
`compose up -d` non aspetta BioLoom: prima l'avviatore stampava subito «BioLoom (server) avviato…» anche con un backend
che usciva all'avvio (chiave troppo corta, `BIOLOOM_TRUSTED_PROXIES` non valida) e che `BIOLOOM_RESTART=unless-stopped`
faceva ripartire di continuo. Ora, dopo `compose up`, «Attesa della risposta di BioLoom su http://<host>:<porta> ...» e
`wait_healthy <porta> <host> 1`: `/api/health` una volta al secondo per 2 minuti su `probe_host "$bind"` (127.0.0.1 per
`0.0.0.0`, `::`, `[::]`, `localhost` o vuoto; `::1` per `[::1]`; altrimenti l'indirizzo di `BIOLOOM_BIND`, con gli IPv6
fra `[]` nell'URL, `url_host`) e la porta già validata. Con il terzo argomento a 1 guarda anche il container
(`compose ps -a -q bioloom`, poi `docker inspect` di stato, `RestartCount` e politica di riavvio):

- uscita 2: container `exited` o `dead` senza politica di riavvio;
- uscita 3: `restarting`, `exited` o `dead` con una politica attiva (fra un'uscita e il riavvio), oppure `RestartCount`
  cresciuto dal primo controllo (un container che compose non ha ricreato può avere riavvii vecchi);
- uscita 1: nessuna risposta in 2 minuti.

In tutti e tre i casi `compose logs --tail 30 bioloom` e `fail` (uscita 1) con «BioLoom non risponde su
http://<host>:<porta> dopo 2 minuti…» oppure «BioLoom si è fermato durante l'avvio: il motivo è nelle righe qui sopra
(spesso un valore di server.env, per esempio BIOLOOM_ACCESS_KEY più corta di 16 caratteri)…», e per l'uscita 3 in più
«intanto Docker continua a riavviarlo (BIOLOOM_RESTART), per fermarlo: … arresta»: l'avviatore non ferma il container da
sé. Dal desktop (`start_local`, ramo desktop di `aggiorna`) `wait_healthy "$port"` resta com'era: 127.0.0.1, senza
controllo del container.

- **Con curl** (`health_ok`, e `wait_healthy` di `bioloom-podman.sh`): `curl -fsS --noproxy '*' --max-time 5`. curl
  usa `http_proxy` anche per 127.0.0.1 (provato con curl 8.19 su Windows), e sui server degli
  enti la variabile è spesso impostata: prima un proxy che rifiutava la richiesta faceva dire «BioLoom non risponde…»
  dopo 2 minuti con il container sano, e uno irraggiungibile faceva aspettare a ogni tentativo tutto il timeout di
  connessione (21 s su Windows, ~130 s su Linux). `--max-time 5` vale come il `read -t 5` del ramo senza curl.
- **Senza curl** (`health_ok`): una vera `GET /api/health HTTP/1.0` su `/dev/tcp`, con `read -t 5` della riga di stato,
  che deve essere 200. Prima bastava aprire la connessione, che la porta pubblicata da Docker accetta anche quando
  BioLoom non ascolta: senza curl l'attesa finiva subito.
- **Prove** (docker finto, copia di prova, `BIOLOOM_RESTART=unless-stopped`): risposta con bind 127.0.0.1 e 0.0.0.0,
  anche senza curl (uscita 0); container uscito (log e «si è fermato durante l'avvio…»); container in riavvio (riavvii
  che crescono, stato `restarting`, `exited` con la politica attiva: messaggio con «Docker continua a riavviarlo…
  arresta»); nessuna risposta con il ciclo accorciato, bind 127.0.0.1 e `[::1]`. La sonda senza curl dà esito negativo
  su una porta che accetta e chiude subito, su una che risponde 503, su una muta (dopo 5 s) e su una chiusa.
- **Limiti noti:** `bioloom-podman.sh` non ha avuto lo stesso intervento: aspetta 2 minuti senza accorgersi che il
  container è uscito e senza mostrare il log («BioLoom non risponde: controlla con '… log'.»), senza curl gli basta la
  connessione, e con bind `::` o `[::]` costruisce un URL sbagliato (tratta come caso speciale solo `0.0.0.0`).

**Docker Desktop.** Quando Docker manca, `bioloom.ps1` e `bioloom.sh` (su macOS e Windows, e su macOS anche quando c'è
solo Podman) aggiungono la licenza e le alternative: gratuito per uso personale, didattico e nelle aziende piccole (meno
di 250 dipendenti e meno di 10 milioni di dollari di fatturato annuo); negli enti pubblici, anche piccoli, e nelle altre
organizzazioni, se l'uso non è didattico, «verifica la licenza» con il link ai prezzi (secondo i termini di Docker serve
un abbonamento per l'uso professionale negli enti pubblici di qualunque dimensione e dove si raggiunge almeno una delle
due soglie). Poi i programmi che danno i comandi `docker` e `docker compose`, dichiarati non provati con BioLoom: Rancher
Desktop, Podman Desktop con la compatibilità Docker e, su macOS, Colima. Prima solo `bioloom.ps1` ne parlava, con
«gratuito per uso personale e accademico». Su Linux l'avviatore indica Docker Engine con il plugin compose, gratuito. La
copia portatile finisce con «Sul computer di destinazione serve solo Docker (Docker Desktop o un programma con i comandi
docker e docker compose)».

**Mac con processore Apple.** Il riepilogo dell'avvio dal desktop (`bioloom.sh`) aggiunge due righe (container amd64 in
emulazione, ProSA x86 a 32 bit che potrebbe non funzionare, l'opzione «Use Rosetta for x86_64/amd64 emulation on Apple
Silicon» di Docker Desktop) se `uname -m` è `arm64`, oppure se `sysctl -n hw.optional.arm64` è 1: in una shell in
Rosetta `uname -m` dice `x86_64`. ProSA con Rosetta non è verificato su un Mac vero.

**Podman.**

- **Driver di archiviazione:** all'avvio e in `aggiorna`, una volta per esecuzione, `podman info --format
  '{{.Store.GraphDriverName}}'` (provato con la 2.2.1; se il formato non va, `graphDriverName:` dall'uscita YAML). Con
  `vfs` (senza root e senza fuse-overlayfs ogni strato è una copia intera) un avviso che rimanda alla Guida
  (fuse-overlayfs e `storage.conf`); non blocca mai.
- **Senza compose:** `bioloom.sh` passa sempre a `bioloom-podman.sh` (con `--sposta-prosa` e `--no-menu`) `server`,
  `arresta`, `stato`, `log` e `verifica`, anche senza `server.env` (bastano i predefiniti; `arresta` ferma il BioLoom
  acceso da qualunque cartella); `aggiorna` solo con `server.env`, perché senza il riavvio creerebbe il file e
  uscirebbe. L'avvio dal desktop finisce con un messaggio che dà le due strade (Docker con compose sul PC, `server` su
  un server con solo Podman). Prima queste azioni arrivavano a `docker compose` con «Docker non è installato», «unknown
  command: docker compose», un falso «BioLoom è arrestato» o l'uscita 127.
- **`--no-menu`** (nuova in `bioloom-podman.sh`): nessuna domanda.

## Backend

### Pacchetti

| Pacchetto (`backend/bioloom/`) | Ruolo |
|---|---|
| radice | avvio, applicazione, configurazione, comandi da riga di comando |
| `core/` | infrastruttura: persistenza, job, coda, budget di CPU, cache, eventi |
| `domain/` | logica scientifica pura e testata: nessun I/O di rete, nessun processo esterno |
| `engines/` | adattatori verso i programmi esterni e i servizi remoti |
| `services/` | servizi applicativi e handler dei job: combinano dominio, motori e persistenza |
| `api/` | rotte FastAPI sottili con modelli Pydantic |

Le dipendenze vanno dall'alto verso il basso (`api` → `services` → `engines`/`domain` → `core`), con una sola eccezione
voluta. `services/reproducibility.py` (`_start_models`) importa i modelli Pydantic delle rotte di avvio da `api/`, così
una riesecuzione valida i parametri del manifest con lo stesso contratto della richiesta originale. L'import è
ritardato dentro la funzione, perché le rotte importano a loro volta i servizi.

Fuori dal backend: `frontend/` (vedi [Frontend](#frontend)); `docker/` con il `Dockerfile` dell'immagine, `entrypoint.sh`,
`impronta.sh` (impronta del codice), il worker QMEAN (`docker/qmean/`: `qmean_worker.py`, `uniclust.py`,
`run_qmean_wrapper.py`), l'immagine dei test (`docker/test/`) e i pacchetti con licenza (`docker/vendor/`); `launcher/`
con gli avviatori, `impronta.ps1`, `compose.yml`, `server.env.example` e `VERSION` (vedi [Versione, impronta e
aggiornamento delle immagini](#versione-impronta-e-aggiornamento-delle-immagini)); `scripts/test.sh` e
`scripts/versione.sh`.

Tutto questo sta in `programma/`. La cartella di BioLoom, quella che si copia sulla chiavetta, contiene solo ciò che
serve all'utente: gli avviatori (`Avvia BioLoom.bat`/`.command`, `Arresta BioLoom.bat`/`.command`, `avvia-bioloom.sh`,
che richiamano `programma/launcher/`), `LEGGIMI.txt`, `LICENSE` (MIT) e `THIRD_PARTY_NOTICES.md`, il manuale `Guida/`,
`data/` e `immagini/`; più `.env.locale` e `server.env`, scritti dall'avviatore. Gli avviatori calcolano due radici:
`ROOT`/`$Root` = `programma/` (compose, Dockerfile, versione) e `BASE`/`$Base` = la cartella di BioLoom (dati, immagini,
configurazione della macchina).
`compose.yml` costruisce l'immagine con contesto `programma/` (la `.dockerignore` è lì) e monta `../../data`.

**Server con Podman senza compose** (RHEL 8, Podman 2.x senza root): `bioloom.sh server`, se c'è `podman` e manca
`docker compose`, passa a `launcher/bioloom-podman.sh`, che ripete `compose.yml` con `podman run` e legge lo stesso
`server.env` (variabile per variabile, non con `--env-file`, così nel container entrano solo quelle di compose).
Differenze: QMEAN gira con `--network container:bioloom` e BioLoom lo chiama su `127.0.0.1:8080` (un pod vorrebbe
l'immagine `pause` da internet); `--security-opt label=disable` evita di rietichettare con SELinux le cartelle
montate, che possono essere dischi condivisi; `--health-cmd none` perché il controllo di salute di Podman 2.x passa da
systemd, e l'attesa la fa l'avviatore; senza root il root del container è l'utente del server, quindi l'avviatore
passa `BIOLOOM_UID=0` e l'entrypoint resta root invece di passare all'utente 1000 (che sul server sarebbe un subuid
estraneo, senza permessi sulla cartella dati); il MAC non si può assegnare, quindi ProSA non trova una licenza valida.
`bioloom-podman.sh servizio` genera le unità systemd dell'utente (`podman generate systemd --name`), che partono
con il server solo con il linger attivo. Etichetta `bioloom.cartella`, driver di archiviazione e azioni senza compose:
vedi [Avviatori](#avviatori-un-bioloom-alla-volta-e-controlli-allavvio).

**Radice**

| Modulo | Contenuto |
|---|---|
| `__main__` | avvio del server (`python -m bioloom`, `--host`, `--port`, `--no-browser`; uscita 3 se l'avvio non riesce, per esempio con una chiave d'accesso troppo corta) e comando `rerun` |
| `app` | applicazione FastAPI: rotte, middleware (Host e origine, chiave d'accesso, CORS dello sviluppo, limite del corpo), errori interni con un codice, interfaccia compilata (`frontend/dist`) |
| `cli` | comandi oltre al server: `python bioloom.py rerun` (vedi [Riproducibilità](#riproducibilità-pacchetti-e-riesecuzione)) |
| `config` | percorsi (`Paths`), impostazioni utente (`UserSettings`, `SettingsStore`), limite degli upload |
| `selftest` | verifica dell'installazione da riga di comando (`python -m bioloom.selftest`) |

**`core/`**

| Modulo | Contenuto |
|---|---|
| `auth` | chiave d'accesso della modalità server (`BIOLOOM_AUTH=on`): lunghezza minima, sessioni revocabili con l'epoca, freno ai tentativi (`LoginLimiter`), indirizzo del client dietro un proxy fidato (vedi [Sicurezza e privacy](#sicurezza-e-privacy)) |
| `bodylimit` | limite della dimensione del corpo delle richieste, prima che Starlette lo legga (`BodyLimit`, `request_limit`; vedi [Sicurezza e privacy](#sicurezza-e-privacy)) |
| `budget` | budget di CPU condiviso fra i job (vedi [Coda con priorità e budget di CPU](#coda-con-priorità-e-budget-di-cpu)) |
| `cache` | cache dei calcoli (vedi [Cache dei calcoli](#cache-dei-calcoli)) |
| `container` | composizione dei servizi, creata una volta all'avvio |
| `db` | SQLite esclusivo, migrazioni versionate (vedi [Migrazioni dello schema](#migrazioni-dello-schema)) |
| `dispatch` | coda con priorità di ogni pool |
| `errors` | errori applicativi con codice e stato HTTP, serializzati in modo uniforme |
| `eta` | stima del tempo residuo |
| `events` | notifiche in-process dei cambiamenti dei job |
| `guard` | protezione dalle richieste di altri siti: header Host (DNS rebinding), origine (CSRF), niente iframe (clickjacking), vedi [Sicurezza e privacy](#sicurezza-e-privacy) |
| `jobs` | `JobManager`, `JobContext`, scratch e salvataggio definitivo, eliminazione atomica sul disco (`.deleting-<id>`) |
| `parallel` | passi indipendenti di un calcolo su un pool locale di thread (`Steps`, vedi [Prestazioni](#prestazioni)) |
| `sensitive` | dati sensibili: mascheratura delle chiavi nei testi (`mask`, `redact`, `redactor`) e ambiente dei programmi esterni senza i segreti del server (`subprocess_env`) |
| `storage` | scritture atomiche, `safe_join` (regge le cartelle in eliminazione su Windows; rifiuta i percorsi che, risolti, escono dalla radice: `..` che risale, percorsi assoluti esterni, link simbolici e giunzioni verso fuori), identificatori validati, rimozione di cartelle con esito (`remove_tree`), dimensioni con un tempo massimo (`dir_size_until`) |

**`domain/`**

| Modulo | Contenuto |
|---|---|
| `assembly` | assemblaggi biologici dal mmCIF e dal REMARK 350 |
| `clashes` | clashscore sugli atomi pesanti |
| `comparison` | confronto strutturale per residuo: distanze, GDT, lDDT |
| `complexes` | bersagli a più catene, simmetria, confronto delle interfacce con il template |
| `consensus` | consenso tra i metodi di validazione |
| `conservation` | conservazione per colonna di un allineamento multiplo, mappata sulla struttura |
| `disorder` | disordine FoldIndex, bassa complessità SEG, domini e copertura del bersaglio |
| `electrostatics` | PQR, griglia e input di APBS, mappe OpenDX, potenziale di superficie |
| `exports` | sessione PyMOL: la struttura incorporata identica con `ascii()` su una sola riga, script `.pml` tutto ASCII |
| `fileformats` | lettura di sequenze e allineamenti da file in ogni formato comune |
| `geometry` | Kabsch, RMSD, TM-score |
| `grading` | voto A–D |
| `interactions` | legami idrogeno, ponti salini, disolfuri, interazioni aromatiche |
| `interfaces` | interfacce fra catene: residui, area sepolta, interazioni, plausibilità |
| `labels` | normalizzazione delle etichette |
| `ligands` | classificazione dei gruppi eteroatomici, colonne '.' del PIR, verifica di contatti, scontri e coordinazione dei metalli |
| `multitemplate` | scelta fra `salign` e gli `align2d` uniti sul bersaglio con più template, orientamento non vincolato |
| `mutations` | mutagenesi: notazione, copia per MODELLER, indicatori, classificazione, scansione, varianti di UniProt |
| `perresidue` | grandezze per residuo esterne al rapporto di qualità: pLDDT, QMEAN locale, ProSA |
| `plddt` | pLDDT dei modelli predetti: fasce, ritaglio, riconoscimento dei file |
| `pockets` | lettura dell'uscita di fpocket |
| `prediction` | file di un predittore esterno e plausibilità di un modello rispetto alla predizione |
| `preflight` | regole dei controlli preventivi |
| `procheck` | lettura del `.sum` di PROCHECK |
| `ramachandran` | diedri dello scheletro e regioni di Ramachandran |
| `refinement` | protocollo della rifinitura, copia pulita, confronto prima/dopo |
| `report` | report HTML autonomo, grafici SVG, verifica del PNG |
| `reproducibility` | controlli dell'archivio caricato, impronta del manifest, oscuramento dei segreti, confronto con le tolleranze |
| `secondary` | DSSP integrata e predizione GOR |
| `sequences` | FASTA, IUPAC, PIR, Clustal, statistiche di allineamento |
| `structure` | PDB/mmCIF, catene, header, residui mancanti, filtri |
| `template_rank` | punteggio motivato dei template e selezione dei complementari |
| `textsearch` | parole, corrispondenza e pertinenza della ricerca globale, escape per FTS5 e LIKE |

**`engines/`**

| Modulo | Contenuto |
|---|---|
| `align` | allineamenti a coppie (Biopython), multipli (Clustal Omega, MAFFT, MUSCLE o center-star integrato), strutturali (US-align, TM-align o Kabsch) |
| `electrostatics` | PDB2PQR con PROPKA, poi APBS |
| `modeller` | preparazione dei template, script, esecuzione di MODELLER |
| `mutate` | script di MODELLER dei mutanti, energia con OpenMM |
| `pockets` | fpocket |
| `procheck` | PROCHECK |
| `prosa` | ProSA II |
| `qmean_local` | client del worker QMEAN |
| `quality` | pipeline di validazione |
| `refine`, `refine_worker` | OpenMM e PDBFixer in un processo separato |
| `remote` | client HTTP dei servizi remoti (UniProt, InterPro, RCSB, NCBI, AlphaFold DB, SWISS-MODEL, ESMFold) |
| `search` | ricerca di template: RCSB, NCBI, BLAST+, MMseqs2, AlphaFold DB |
| `tools` | rilevamento di strumenti e librerie Python di calcolo, preambolo di licenza |
| `versions` | versioni degli strumenti per i pacchetti di riproducibilità |

**`services/`**

| Modulo | Contenuto |
|---|---|
| `analysis` | job `analysis` (analisi strutturale) |
| `compute` | job `search`, `align`, `align2d`, `model`, `loop`, `quality`, `qmean`; template preparati riusati (`PreparedTemplates`) |
| `coverage` | copertura del bersaglio da parte dei template, avvisi su disordine e domini |
| `databases` | pdbaa, MMseqs2, stato di UniClust30; job dei database (pdbaa e MMseqs2 uno alla volta, risorsa `PDBAA_FILES`) |
| `globalsearch` | ricerca globale |
| `housekeeping` | pulizia automatica e spazio occupato |
| `jobcleanup` | pulizia di massa dei job (criteri, anteprima, ciclo a tempo), uso ed eliminazione dei progetti con i loro job (vedi [Eliminazione di progetti e pulizia dei job](#eliminazione-di-progetti-e-pulizia-dei-job)) |
| `labels` | etichette e preferiti |
| `licenses` | chiave MODELLER, licenza ProSA, stato di PROCHECK |
| `live` | flussi Server-Sent Events dei job |
| `mutations` | job `mutate` e `alascan`, varianti di UniProt |
| `pipeline` | job `pipeline` (batch) |
| `predict` | job `predict`: predizione esterna e confronto con il modello |
| `preflight` | fatti per i controlli preventivi |
| `projects` | progetti, sequenze, documenti |
| `refine` | job `refine` |
| `reports` | dati del report di un modello o di un'analisi |
| `reproducibility` | pacchetti dei job e job `rerun` |
| `selftest` | job `selftest` (verifica dell'installazione su 1UBQ) |
| `structure_tools` | job `pockets` ed `electrostatics` |
| `templates` | libreria dei template, assemblaggi, download da RCSB e AlphaFold DB, caricamento con sostituzione confermata (vedi [Libreria dei template](#libreria-dei-template-caricamento-e-sostituzione)) |

**`api/`** (OpenAPI completo su `/docs`)

| Modulo | Prefisso | Contenuto |
|---|---|---|
| `analysis` | `/api/analysis` | analisi strutturale e la più recente per struttura |
| `auth` | `/api/auth` | stato, login, logout, chiusura di tutte le sessioni (`logout-all`) |
| `bio` | `/api` | analisi di sequenza, disordine, copertura, FASTA, conversione degli allineamenti, validazione PIR, UniProt, InterPro, metadati RCSB |
| `compute` | `/api` | avvio di ricerca di template (`POST /api/search`), allineamenti, modellazione, loop, qualità, QMEAN, pipeline (elementi ed esportazioni) e controlli preventivi |
| `databases` | `/api/databases` | stato e job dei database |
| `deps` | — | dipendenze condivise (servizi, lettura limitata degli upload: 413 `too_large`) |
| `files` | `/api/files` | sequenze e strutture da file, strutture caricate |
| `jobs` | `/api/jobs` | elenco, stato, eventi, log, file, annullamento, ripresa, eliminazione (con byte liberati e resti), pulizia di massa con anteprima (`/cleanup`, `/cleanup/preview`), qualità, consenso, report, PyMOL, archivio |
| `labels` | `/api/tags`, `/api/favorites`, `/api/search` | etichette, preferiti e ricerca globale (`GET /api/search`) |
| `licenses` | `/api/licenses` | MODELLER e ProSA |
| `mutations` | `/api` | mutagenesi, scansione di alanina, varianti, cronologia |
| `predict` | `/api` | confronto con un predittore esterno |
| `projects` | `/api/projects` | progetti, sequenze, documenti; uso di un progetto (`/{id}/usage`) ed eliminazione con tutti i suoi job |
| `reproducibility` | `/api/jobs` | pacchetto di un job, anteprima e riesecuzione di un pacchetto |
| `structure_tools` | `/api` | tasche, elettrostatica, rifinitura e i più recenti per struttura |
| `system` | `/api` | salute, informazioni, risorse, spazio, verifica, pulizia, esempi, impostazioni, arresto |
| `templates` | `/api/templates` | libreria, download, upload (409 `template_exists` senza `overwrite`), assemblaggi, file, eliminazione |

### Job

Tutti i calcoli passano dal `JobManager` (priorità di ogni tipo nella [coda](#coda-con-priorità-e-budget-di-cpu)):

| Tipo | Avvio | Pool |
|---|---|---|
| `search` | `POST /api/search` | io |
| `align` (a coppie, multiplo, strutturale) | `POST /api/align` | cpu |
| `align2d` | `POST /api/align2d` | cpu |
| `quality` | `POST /api/quality`, `/api/quality/upload` | cpu |
| `qmean` | `POST /api/qmean` | io, con la risorsa esclusiva del QMEAN locale |
| `analysis` | `POST /api/analysis` | cpu |
| `pockets`, `electrostatics` | `POST /api/pockets`, `/api/electrostatics` | cpu |
| `mutate` | `POST /api/mutate` | cpu |
| `predict` | `POST /api/predict` | io |
| `model`, `loop` | `POST /api/model`, `/api/model/loop` | cpu |
| `refine` | `POST /api/refine` | cpu |
| `alascan` | `POST /api/alascan` | cpu |
| `rerun` | `POST /api/jobs/rerun` | cpu |
| `selftest` | `POST /api/system/selftest` | cpu |
| `pipeline` | `POST /api/pipeline` | cpu |
| `db_update_pdbaa` | `POST /api/databases/pdbaa/update` | io, con la risorsa esclusiva dei file di pdbaa |
| `db_build_mmseqs`, `db_index_mmseqs` | `POST /api/databases/mmseqs/build`, `/mmseqs/index` | io, con la risorsa esclusiva dei file di pdbaa |
| `db_convert_uniclust`, `db_local_uniclust` | `POST /api/databases/uniclust/convert`, `/uniclust/local` | io, con la risorsa esclusiva del QMEAN locale |

- stato `queued → running → completed | failed | cancelled` (più `cancelling`) salvato su SQLite; non ci sono altri
  stati (`core/jobs.TERMINAL`, `ACTIVE`);
- coda con priorità per tipo di job e budget di CPU condiviso dai processi esterni di tutti i job (vedi [Coda con
  priorità e budget di CPU](#coda-con-priorità-e-budget-di-cpu)), con la posizione in coda `queue_position`;
- log incrementale (`GET /api/jobs/{id}/log?offset=`) con l'output dei sottoprocessi in tempo reale;
- progresso e fase aggiornati dagli handler (per MODELLER tramite marcatori `@@BIOLOOM` stampati dallo script),
  con la stima del tempo residuo `eta_seconds` e gli aggiornamenti in tempo reale via Server-Sent Events (vedi
  [Tempo residuo e aggiornamenti in tempo reale](#tempo-residuo-e-aggiornamenti-in-tempo-reale));
- annullamento immediato (i processi registrati vengono terminati) e timeout configurabile (`job_timeout_minutes`,
  predefinito 120);
- lunghezza massima della sequenza bersaglio configurabile (`max_sequence_length`, «Lunghezza massima della sequenza
  (residui)»: predefinito 5000, il limite fisso delle versioni precedenti, da 100 a 9999, `config.SEQUENCE_MAX_*`).
  Il massimo viene dal formato PDB, che numera i residui con 4 cifre. Il valore in uso vale per ricerca, allineamento,
  modellazione (`compute.check_length`, errore `sequence_too_long`), per ogni elemento della pipeline (fallisce con lo
  stesso messaggio, gli altri proseguono), per i controlli prima dell'avvio e per disordine e copertura
  (`disorder.clean(max_length=...)`); il wizard lo legge dalle Impostazioni. Tempi e memoria misurati sul server in
  [Lunghezza della sequenza: tempi misurati](#lunghezza-della-sequenza-tempi-misurati);
- al riavvio i job rimasti attivi vengono marcati come falliti (nessuno stato "fantasma"), con fase
  "Interrotto"; gli elementi di pipeline non conclusi diventano falliti con lo stesso motivo;
- lo stato finale si scrive anche se il salvataggio dei file non riesce (disco dati pieno) o se la cartella di
  lavoro nello scratch non si crea (scratch pieno o non scrivibile, codice `scratch_error`): il job finisce
  `failed` con il motivo invece di restare `running` fino al riavvio;
- un job fallito o annullato si può rimettere in coda (`JobManager.requeue`), oggi usato dalle pipeline; da
  `completed` solo se il chiamante lo chiede esplicitamente (`statuses`), come la pipeline con elementi falliti;
- eliminazione (`delete`), ripresa e avvio sono mutuamente esclusivi: l'eliminazione aspetta (fino a 10 s) che
  l'esecuzione precedente o la preparazione di una ripresa abbiano finito, poi cancella la riga con un
  `DELETE … WHERE status IN ('completed','failed','cancelled')`. Un job ripreso nel frattempo non si elimina
  (409) e, dopo l'eliminazione, una ripresa riceve 404: niente job eliminati mentre ripartono, né cartelle
  orfane;
- l'eliminazione è atomica sul disco: sotto il lock la cartella `jobs/<id>` si rinomina in `jobs/.deleting-<id>`
  prima del DELETE. Se il rinomino non riesce (file in uso sull'host) il job resta intero, riga e file, con un 409; se
  il DELETE non riesce il rinomino si annulla (fino a 3 tentativi; se nessuno riesce la cartella resta in
  `.deleting-<id>` accanto alla riga, e la pulizia automatica la riporta in `jobs/<id>` se quel nome è libero, mai la
  elimina). Fuori dal lock
  la cartella rinominata si misura e si rimuove: la risposta dice i byte liberati e `leftover` (rimossa solo in parte;
  il resto lo toglie la pulizia automatica). Con
  `only_if` (la pulizia di massa) una condizione in più sulle colonne di `jobs` (stella, etichette, età, pipeline
  riprendibili) si valuta prima di toccare il disco e di nuovo nello stesso DELETE (vedi [Eliminazione di progetti e
  pulizia dei job](#eliminazione-di-progetti-e-pulizia-dei-job));
- `submit` verifica il progetto nello stesso INSERT (`INSERT … SELECT … WHERE ? IS NULL OR EXISTS (progetto)`, un solo
  statement): un progetto inesistente o appena eliminato dà 404 «Progetto non trovato.», mai un job orfano, e
  `project_id` vuoto (campo vuoto di un form) vale «nessun progetto». Se qualcosa fallisce prima dell'accodamento
  (INSERT o preparazione) la riga `queued` mai accodata si cancella e la cartella si rimuove; quello che non si riesce a
  togliere (file in uso) passa in `.deleting-<id>` e lo toglie la pulizia automatica. L'errore originale arriva sempre
  al chiamante, anche se questa pulizia non riesce;
- **ambiente dei programmi esterni** (`default_env`): quello del server senza i suoi segreti
  (`core/sensitive.subprocess_env`: `BIOLOOM_ACCESS_KEY`, `MODELLER_KEY`, `KEY_MODELLER` e tutte le `BIOLOOM_SMTP_*`,
  con utente e password della posta; i nomi senza distinguere maiuscole, come su Windows), più le variabili di
  `set_subprocess_env` (`BIOLOOM_MODELLER_KEY`, la chiave attiva, con cui MODELLER continua a funzionare). Nessun
  programma esterno usa le variabili tolte. Lo usano anche i processi lanciati fuori da un job (sonde delle versioni in
  `engines/versions.py`, passi di qualità senza `ctx`, prova di MODELLER), che prima ereditavano tutto `os.environ`.
  Limite: `BIOLOOM_MODELLER_KEY` arriva a tutti i programmi esterni, non solo a MODELLER;
- **chiavi nell'output dei processi:** `JobContext._popen` maschera riga per riga le chiavi MODELLER (`output_secrets`:
  quella attiva, quella dell'ambiente del processo e `MODELLER_KEY`) prima del log del job, del file dell'output e
  dell'esito restituito a chi ha lanciato il processo, che finisce nei messaggi d'errore: MODELLER ripete la chiave
  rifiutata nel suo messaggio;
- **righe nel log a esecuzione chiusa** (`JobManager.log_if_present`): per chi scrive nel log da un altro thread dopo la
  fine (l'esito dell'avviso per email), solo se la riga del job c'è ancora, la cartella è al suo posto e il job non è in
  eliminazione, sotto lo stesso lock di `delete`. Prima una riga scritta dopo l'eliminazione ricreava `jobs/<id>/job.log`,
  una cartella senza riga che nessuna pulizia toglie.

### Coda con priorità e budget di CPU

**Coda** (`core/dispatch.py`). Ogni pool ha un dispatcher con una coda ordinata al posto del `ThreadPoolExecutor`
FIFO: "cpu" con i job di calcolo in parallelo delle Impostazioni (`effective_workers`, core − 1 se automatico), "io"
con 8. I worker prendono sempre il primo job avviabile in quest'ordine (`core/jobs.PRIORITY`):

| Priorità | Tipi di job | Perché |
|---|---|---|
| 0 | `search`, `align`, `align2d`, `quality`, `qmean`, `analysis`, `pockets`, `electrostatics`, `mutate`, `predict` | interattivi brevi: l'utente aspetta davanti allo schermo |
| 1 | `model`, `loop`, `selftest`, `refine`, `alascan`, `rerun` (registrato con priorità 1) e i tipi non elencati | minuti |
| 2 | `pipeline`, `db_update_pdbaa`, `db_build_mmseqs`, `db_index_mmseqs`, `db_convert_uniclust`, `db_local_uniclust` | lavori lunghi in blocco |

`PRIORITY` contiene anche `compare` (0), rimasto dal piano (F4.1): nessun handler lo registra, perché il
confronto strutturale usa il job `align` (vedi [Confronto strutturale](#confronto-strutturale)).

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
- **File di pdbaa** (`PDBAA_FILES`, `services/databases`): `db_update_pdbaa`, `db_build_mmseqs` e `db_index_mmseqs`
  scrivono una cartella che un altro legge per costruire la propria (pdbaa → `pdbaa_mmseqs` → indice nel volume
  `/cache`), quindi girano uno alla volta, anche lanciati da più utenti; chi aspetta resta `queued` con la fase "In
  coda: un altro job sta aggiornando pdbaa o il database MMseqs2" (aggiunta a `RESOURCE_STAGE` da `services/databases`,
  senza toccare `core/jobs`). Le risorse valgono dentro un pool, perché ogni dispatcher tiene le proprie: per questo
  build e indice MMseqs2 sono passati da "cpu" a "io", come QMEAN. I loro processi (`blastdbcmd`, `createdb`,
  `createindex`) passano comunque dal budget di CPU, e la copia dei file non occupa più un posto di calcolo. Ogni job
  lavora accanto alla cartella definitiva (stesso disco, per lo scambio con un rename) in `<nome>.new-<job>` e
  `<nome>.old-<job>`; un'estrazione o una copia non riuscita, anche per annullamento, si toglie subito, e gli avanzi di
  un'interruzione (arresto, memoria esaurita; anche quelli a nome fisso `<nome>.new`/`.old` delle versioni precedenti)
  li toglie il job successivo, che tiene la risorsa. Per rendere globali le risorse esclusive, fra pool diversi,
  andrebbe cambiato `core/dispatch.py`.

**Budget di CPU** (`core/budget.py`). Senza coordinamento, pipeline × elementi in parallelo × strumenti saturavano la
CPU. Gli slot sono `effective_workers`, condivisi da tutti i job. `JobContext.run(cpu=...)` li prende prima di avviare
il processo e li restituisce quando termina: `True` (predefinito) = 1, un numero per i programmi multi-thread,
`False` per i processi leggeri o di I/O.

| Processo | Slot |
|---|---|
| MODELLER (allineamento, modelli, loop, DOPE) | 1: gli script non usano `parallel.Job`, un thread |
| PROCHECK, ProSA II, TM-align, US-align, `blastp` (`-num_threads` predefinito 1), Clustal Omega (`--threads` predefinito 1), MAFFT (`--thread` predefinito 0 = un thread), MUSCLE 3, `mmseqs createdb`, fpocket, PDB2PQR (Python), APBS (binario ufficiale senza OpenMP) | 1 |
| MMseqs2 `easy-search` e `createindex`, MUSCLE 5 (di suo userebbe tutti i processori), rifinitura con OpenMM (thread della piattaforma CPU) | da 1 a core − 1, uno per thread: al suo turno il processo prende gli slot liberi e parte con altrettanti thread (`--threads`/`-threads`) |
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

### Avviso per email alla fine delle pipeline

`notify_email` nei parametri di `POST /api/pipeline` (vuoto = nessun avviso): alla sottomissione l'indirizzo si
controlla (`notify.EMAIL_RE`, nessun a capo né separatore) e serve un server di posta configurato
(`notify_not_configured`, `invalid_email`, 400). `JobManager.on_finish` chiama gli ascoltatori dopo la scrittura di uno
stato finale; `NotifyService.on_finish` legge i parametri del job e, se c'è l'indirizzo, invia in un thread suo:

- **Messaggio** (testo semplice, `Auto-Submitted: auto-generated`): oggetto «BioLoom: pipeline «titolo» completata |
  fallita | annullata»; titolo, esito, elementi per stato (`pipeline_items`), avvio, fine e durata (UTC), motivo
  dell'errore (300 caratteri) per fallite e annullate, link `<public_url>/pipeline?job=<id>` se c'è l'indirizzo di
  BioLoom, identificativo del job. Nessun risultato, sequenza, nome di sequenza o file.
- **Invio:** `smtplib`, timeout 30 s, STARTTLS o SSL/TLS con il contesto predefinito (certificato verificato) oppure
  nessuna sicurezza; accesso solo con un utente. Errori temporanei (connessione, risposte 4xx) riprovati dopo 10 e 60 s;
  utente o password rifiutati e destinatario rifiutato non si riprovano. L'esito («Email di avviso inviata a …» o «non
  inviata: motivo») va nel log del job appena l'esecuzione si chiude (`busy`, al più 2 minuti), solo se il job esiste
  ancora (`JobManager.log_if_present`: un job eliminato durante l'invio non si vede ricreare la cartella). Ogni
  esecuzione (anche una ripresa) avvisa una volta, alla sua fine.
- **Configurazione, mai dall'interfaccia** (`GET /api/notify` in sola lettura, `POST /api/notify/test`; nessun `PUT`):
  in qualunque modalità le variabili `BIOLOOM_SMTP_*` e `BIOLOOM_PUBLIC_URL` dell'ambiente (server.env), se c'è
  `BIOLOOM_SMTP_HOST`, altrimenti le stesse righe `NOME=valore` nel file `licenses/posta.env` della cartella dati
  (`read_env_file`: commenti `#`, virgolette esterne tolte; modello in `programma/launcher/posta.env.example`), riletto a
  ogni uso. Lo stato dice la fonte (`env`, `file`, `none`) e, per valori non validi, il motivo in italiano (`error`:
  l'avviso resta spento). La password non torna mai (`password_set`) ed è fra i segreti oscurati nei pacchetti di
  riproducibilità (`LicenseService.extra_secrets`). Chi usa BioLoom non può né cambiare il server di posta né far
  contattare al server un host della sua rete; sceglie solo se ricevere l'avviso e a quale indirizzo.
- **Intestazioni:** nessun a capo in utente, mittente e destinatario (validatori); l'oggetto si ricompone su una riga
  e si tronca a 150 caratteri.
- **Limiti di frequenza** (in ogni modalità; ogni tentativo d'invio conta, riuscito o no, perché un server di posta che
  non risponde tiene occupato un thread fino a tre tentativi, oltre un minuto):
  - email di prova al più una ogni 30 s (`TEST_MIN_INTERVAL`) e 5 all'ora (`TEST_PER_HOUR`) per tutto il server,
    altrimenti 429 `rate_limited` con l'attesa: «È appena stata inviata un'email di prova: riprova tra N s.» oppure
    «Limite di 5 email di prova all'ora raggiunto: riprova tra N min.» (`wait_text`: per eccesso, in minuti da 90 s).
    Senza un server di posta pronto la prova risponde prima 400 `notify_not_configured` («Server di posta non
    configurato.»), senza contare;
  - avvisi di fine job al più 10 all'ora allo stesso indirizzo, senza distinguere maiuscole (`NOTICE_PER_RECIPIENT`), e
    30 all'ora in tutto (`NOTICE_PER_HOUR`): oltre, l'avviso non parte e il motivo va nel log del job («Email di avviso a
    … non inviata: limite di 10 avvisi all'ora allo stesso indirizzo raggiunto.»), perché il job ormai è finito e non c'è
    un errore da restituire. Un avviso costa poco a chi lo chiede (basta una pipeline annullata subito);
  - le prenotazioni si registrano sotto un lock prima dell'invio, così richieste in parallelo non passano insieme. Gli
    avvisi delle pipeline annullate in coda partono ancora: con un accesso condiviso l'annullamento può farlo un altro
    utente, e il limite basta contro l'abuso. Il client del frontend tratta il 429 come ogni errore, con il messaggio del
    server.

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
  job paralleli delle Impostazioni, al massimo 256).
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
  scheda ne usa al massimo due (elenco e job aperto; tre sulla pagina Qualità con QMEAN e analisi insieme, quattro per
  i pochi secondi di un'analisi strutturale lanciata nella scheda Interazioni).

### Cache dei calcoli

Modulo `core/cache.py`; le risposte dei servizi remoti hanno una cache propria in `engines/remote.py` (vedi [Sicurezza
e privacy](#sicurezza-e-privacy)).

Ricerche di template e allineamenti multipli e strutturali non si rifanno se la stessa richiesta è già stata
calcolata: rilanciare una pipeline con le stesse sequenze e altre opzioni di modellazione salta tutte le
ricerche. Il pairwise è istantaneo e non passa dalla cache.

- **Dove:** un file JSON per voce in `data/cache/compute/<operazione>/<chiave>.json` (`search`,
  `align_multiple`, `align_structural`). Sta nella cartella dati e viaggia con la chiavetta. La pulizia automatica
  la tratta come le risposte remote (30 giorni) e in più la tiene sotto il tetto (sotto). In Impostazioni → Spazio
  l'occupazione è mostrata a parte.
- **Chiave:** SHA-256 del JSON canonico (chiavi ordinate) con operazione, versione del formato della cache e di
  BioLoom, input normalizzati, parametri e timbro delle dipendenze:

  | Operazione | Input e parametri | Timbro |
  |---|---|---|
  | ricerca | sequenza (maiuscole, senza spazi), motore, E-value, risultati massimi, taxid (solo NCBI), accessione UniProt (solo AlphaFold DB), versione del formato dei risultati (2, motori sul PDB: vedi [Scelta ragionata dei template](#scelta-ragionata-dei-template)) | BLAST+ e MMseqs2: dimensione e data di tutti i file del database (`pdbaa.*`, `pdbaa_h*`), eseguibile (percorso, dimensione, data), uso dell'indice veloce MMseqs2 |
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
- **Tetto** (impostazione `compute_cache_max_mb`, «Spazio massimo della cache dei calcoli (MB)»: predefinito 2048, da
  100 a 102400, `config.COMPUTE_CACHE_*`): oltre il tetto `prune` elimina le voci usate meno di recente finché la cache
  scende al 90% (`PRUNE_TARGET`), così la pulizia successiva non arriva alla scrittura dopo.
  - **Uso:** «usata» = scritta o letta con successo. `lookup` aggiorna con `_touch` la data di ultimo accesso (atime) e
    lascia quella di modifica, su cui conta la pulizia per età; l'ordine è dato dalla più recente delle due.
  - **Costo:** la cartella non si scorre a ogni scrittura. Lo spazio occupato si misura alla prima pulizia per tetto e
    poi si aggiorna a ogni `store` (anche per una voce sostituita); è una stima: le voci tolte dalla pulizia per età la
    lasciano più alta del vero, e allora la pulizia arriva prima e la rimisura. `enforce` scorre la cartella solo se la
    stima manca o supera il tetto.
  - **Quando:** all'avvio e con la pulizia automatica (`services/housekeeping`, dopo quella per età; le voci tolte si
    contano in `cache` del rapporto, i byte in `freed_bytes`), dopo le scritture di `run_cached` (riga nel log del job:
    «Cache dei calcoli oltre il tetto delle Impostazioni: eliminate N voci usate meno di recente (X MB).») e, in un
    thread, quando `PUT /api/settings` cambia il tetto.
  - **Casi limite:** i file temporanei di scritture interrotte contano nello spazio ma non si toccano (li toglie la pulizia
    automatica dopo un giorno); un file che non si elimina (in uso) resta per la volta dopo; una pulizia alla volta.
  - **Impostazione:** le impostazioni precedenti ricevono il predefinito; un valore che non è un intero fra il minimo e
    il massimo, scritto a mano in `settings.json`, torna al predefinito senza azzerare le altre (`UserSettings.from_file`).
- **Provenienza:** un risultato riusato ha `cached: true` e `cached_at` (data ISO del calcolo), e il log del job
  riporta "Risultato dalla cache del …". Nella pipeline la data finisce nelle metriche dell'elemento
  (`search_cached_at`, badge "cache" nella tabella) e il risultato del job conta le ricerche riusate
  (`search_cache_hits`).

### Libreria dei template: caricamento e sostituzione

La libreria (`services/templates.TemplateLibrary`, cartella `templates/`) ha per ogni template un file `<ID>.pdb` o
`<ID>.cif`, il `<ID>.meta.json` e l'analisi in cache. Un file entra sempre allo stesso modo (`_install`): si scrive e si
legge (`domain/structure.load_structure`) in una cartella temporanea della libreria, `.upload-<casuale>/<ID>.<fmt>`
(stesso disco; con lo stesso nome del file definitivo, così gli errori di lettura citano `<ID>.pdb` come prima), poi
un `os.replace` sotto il lock lo mette al suo posto e solo dopo si toglie il file con l'altro formato (.pdb ↔ .cif): chi
legge trova sempre il vecchio file o il nuovo, mai nessuno dei due. Scritture concorrenti dello stesso id (due
sostituzioni, un download e una sostituzione) sono in fila su un lock per id (`_write_lock`) attorno a installazione e
registrazione dei metadati, preso sempre dopo quello dei download (`_fetch_lock`) e mai attorno al download stesso.
Un file illeggibile non tocca la libreria. Il punto
iniziale tiene le cartelle temporanee fuori dall'indice della ricerca globale (l'elenco conta solo i file); quelle
rimaste da un arresto si tolgono alla creazione della libreria, cioè all'avvio.

**`POST /api/templates/upload`** (`file`; `template_id`, altrimenti l'id viene dal nome del file; `overwrite`):

- `overwrite`, campo del modulo: `true`, `1`, `on` (anche `yes`) = sì; `false`, `0`, `off` o assente = no; un altro
  valore → 422 `validation_error`.
- **Id già in libreria senza `overwrite`:** 409 subito, prima di scrivere o leggere il file (la domanda all'utente è
  immediata anche con un file da 50 MB), libreria invariata:
  `{"error": {"code": "template_exists", "message": …, "details": {"id", "jobs", "projects"}}}` con il messaggio
  «Nella libreria c'è già un template <ID>[, usato da N job[ in M progetto/progetti]]. Per sostituirlo conferma la
  sovrascrittura (i calcoli successivi useranno il nuovo file); altrimenti scegli un altro identificativo.». Lo stesso
  409 se l'id arriva da un'altra richiesta mentre il file si legge.
- **Con `overwrite`:** il file si sostituisce solo se si legge; uno rovinato dà 400 `invalid_structure` e il template
  resta intatto. Metadati (`added_at`, `source: "caricamento utente"`) e analisi si rifanno dal nuovo file, anche
  cambiando formato (resta un solo file); se la registrazione non riesce il `meta.json` vecchio si toglie, perché non
  descrive il nuovo file. La risposta 200 ha il campo `replaced` (true/false).
- **Uso:** `api/templates.template_usage` conta i job che citano `"<ID>"` nei parametri JSON (sorgente di un'analisi,
  template di una modellazione o di una pipeline a template fissi) e i loro progetti distinti. È approssimato: conta
  anche una sequenza chiamata come l'id e non conta le pipeline che scelgono il template dalla ricerca.

**Altri chiamanti:**

- **Download** (`fetch` da RCSB, `fetch_alphafold`): passano da `_install` senza `overwrite`, quindi non sostituiscono
  mai un template presente, nemmeno uno caricato dall'utente mentre il download è in corso (si usa quello, con
  `cached`). Un file scaricato illeggibile non entra in libreria: 502 `remote_error` («RCSB PDB: la struttura <ID>
  scaricata non è leggibile (…)», lo stesso per AlphaFold DB); prima restava un template rotto.
- La pipeline scarica solo i template che mancano; gli assemblaggi hanno già il loro conflitto esplicito; la
  riesecuzione usa una libreria privata. `save_text` è stato tolto: nessuno lo chiamava.
- `os.replace` non ritenta: nel container Linux un file aperto non lo blocca, fuori da Docker su Windows fallirebbe.

**Interfaccia** (`components/TemplateUpload.tsx`, `lib/templateUpload.ts`): l'hook `useTemplateUpload` è l'unico punto
che carica un template (pagina Libreria e «Da file» del selettore dei template in Modellazione, Allineamenti, Pipeline ed
Editor PIR). Al 409 `template_exists` del primo tentativo (`replaceQuestion`) mostra il `Confirm` dell'app, con lo stile
"danger", il messaggio del server e «"Sostituisci" mette al suo posto il file «<nome>»»; pulsanti **Annulla** e
**Sostituisci**. Il secondo tentativo ha `overwrite=true` (`api.uploadTemplate(file, { templateId?, overwrite? })`) e
non chiede mai una seconda conferma. Avvisi: «<ID> sostituito» nella Libreria, «Template <ID> sostituito nella
libreria» nel selettore (altrimenti «caricato»). Dopo una sostituzione il dettaglio della Libreria si ridisegna da capo
(chiave con una revisione) e il selettore rilegge l'analisi, togliendo le catene scelte che non esistono più; se non ne
resta nessuna prende la prima.

### Migrazioni dello schema

Modulo `core/db.py`.

Lo schema SQLite ha una versione in `meta.schema_version`. `SCHEMA` è lo schema di base (versione 1, quello di prima
delle migrazioni) e `MIGRATIONS` elenca i passaggi successivi (`Migration(versione, descrizione, istruzioni)`).
All'apertura `Database._migrate`:

1. esegue lo schema di base (`IF NOT EXISTS`, come ha sempre fatto) e legge la versione; un database **di una versione
   più recente** di BioLoom non si apre (`MigrationError`: le sue tabelle potrebbero avere vincoli che questa versione
   non conosce);
2. se ci sono migrazioni da applicare a un database esistente, fa prima una **copia di sicurezza** in
   `backups/bioloom.schema<N>.<data>.db` con l'API di backup di SQLite: la copia passa dalla connessione e contiene anche
   le pagine ancora nel WAL (una copia del solo file le perderebbe; verificato con un database della versione 1 lasciato
   con le righe solo nel WAL) e funziona con il lock esclusivo. Si scrive con un nome temporaneo, si verifica con
   `PRAGMA quick_check` e solo dopo prende il nome definitivo; se non riesce (disco pieno, cartella non scrivibile) la
   migrazione non parte e l'avvio si ferma con il motivo. Un database nuovo non ha copia;
3. applica ogni migrazione in **una transazione** con la nuova `schema_version` scritta nella stessa transazione: un
   errore a metà annulla tutto (anche il DDL, transazionale in SQLite) e il file resta alla versione precedente, con dati
   e tabelle di prima; l'avvio si ferma con `MigrationError`, che dice a quale versione è rimasto e dov'è la copia.

Le istruzioni sono **idempotenti** (`IF NOT EXISTS`): la versione 4.1.0, aperta sulla stessa cartella dati dopo la
nuova, riscrive `schema_version` a 1 a ogni avvio, e al ritorno la migrazione riparte su tabelle già presenti senza
errori e senza perdere etichette (provato). Le copie restano in `backups/` (una per migrazione: sono rare) e si possono
cancellare a mano. Il log dell'avvio riporta le migrazioni applicate e la copia.

| Versione | Migrazione |
|---|---|
| 1 | schema di base: `meta`, `projects`, `sequences`, `documents`, `jobs`, `pipeline_items` |
| 2 | etichette e preferiti: tabelle `tags` (tipo, id, etichetta) e `favorites` (tipo, id), indice per etichetta, trigger di pulizia su `projects`, `sequences`, `jobs` |

### Licenze dei programmi esterni

Servizio `services/licenses.py`.

- **MODELLER:** la chiave viene da `data/licenses/modeller.key` oppure da `MODELLER_KEY`.
  - Arriva ai soli sottoprocessi tramite `BIOLOOM_MODELLER_KEY`; `MODELLER_KEY` del server non passa ai programmi
    esterni (`core/sensitive.subprocess_env`).
  - Il preambolo degli script (`engines/tools.LICENSE_PREAMBLE`) esegue il `config.py` dell'installazione e
    registra `modeller.config` con la chiave in `sys.modules` prima di `import modeller`. L'installazione
    non viene modificata.
  - Una chiave nuova viene provata con MODELLER prima di essere salvata; nelle risposte è sempre mascherata.
  - **Mascheratura** (`core/sensitive.mask`, `redact`; senza distinguere maiuscole, solo segreti di almeno 4 caratteri):
    `tools.detect_modeller` maschera nei messaggi dei tentativi la chiave provata, quella attiva e `MODELLER_KEY`, così
    ogni uso del suo risultato la riceve già mascherata (`MOD••••••A9`): `/api/system/info`, licenze, verifica
    dell'installazione, messaggi d'errore dei job che passano da `modeller_python()`. L'output dei processi dei job lo
    maschera `JobContext._popen` (vedi [Job](#job)). Prima solo le rotte delle licenze sostituivano la chiave, e solo
    scritta con le stesse maiuscole.
- **ProSA II 2003:** `LicenseDB` ha righe `programma scadenza MAC codice`. L'avviatore imposta quel MAC sul
  container (`mac_address`), e l'interfaccia confronta il MAC richiesto con quello effettivo. La licenza vale per un
  solo computer e vieta di cambiarne la configurazione per aggirarla, mentre la cartella dati gira su più computer
  (chiavetta, copia con `--con-licenze`): gli avviatori (`bioloom.sh`, `bioloom.ps1`, `bioloom-podman.sh`) applicano
  il MAC un computer alla volta.
  - **Identificativo del computer:** Windows `MachineGuid` di `HKLM\SOFTWARE\Microsoft\Cryptography` (PowerShell
    `Get-ItemProperty`, poi la vista del registro a 64 bit, che serve a un PowerShell a 32 bit; Git Bash `reg query`);
    macOS `IOPlatformUUID` (`ioreg -rd1 -c IOPlatformExpertDevice`); Linux `/etc/machine-id`, poi
    `/var/lib/dbus/machine-id` (sotto WSL il `MachineGuid` di Windows con `reg.exe`). Stringa senza spazi ai lati e in
    minuscolo; `id_computer` = primi 16 caratteri dell'SHA-256 esadecimale di `bioloom-computer:<stringa>` (UTF-8,
    senza a capo): Git Bash, PowerShell e WSL sullo stesso Windows danno lo stesso valore. Senza identificativo nessun
    controllo: il MAC si applica (stato `senza-id`, «computer non identificato»).
  - **Legami:** `data/licenses/prosa/computer`, una riga `<mac> <id_computer>` per licenza (MAC `aa:bb:cc:dd:ee:ff`
    minuscolo), fine riga LF, scritto su un file temporaneo e rinominato; le righe delle altre licenze restano. Permessi
    0600 dagli avviatori bash; PowerShell non cambia le ACL, perché la cartella dati passa fra computer e utenti
    diversi. Il file segue `data/licenses/` (copia portatile solo con `--con-licenze`).
  - **Decisione**, quando l'avviatore applicherebbe il MAC: MAC senza legame → lo lega a questo computer e lo applica
    (stato `attivata`); legato a questo computer → lo applica (`questo`); legato a un altro → non lo applica, e il
    container prende il MAC che avrebbe senza licenza (quello locale salvato in `.env.locale` o uno nuovo `02:…`), con
    il messaggio «ProSA: la licenza è attiva su un altro computer…». In modo interattivo chiede «Spostare la licenza
    ProSA su questo computer? [s/N]»; `--sposta-prosa` / `-SpostaProsa` sposta senza chiedere; con sì riscrive il
    legame e applica il MAC (`spostata`), altrimenti stato `altro`. La riga «ProSA:» del menu dice quale caso vale.
  - **Server:** il MAC viene da `BIOLOOM_MAC` di `server.env`, e il controllo vale solo se coincide con quello del
    `LicenseDB` nella cartella dati (`BIOLOOM_DATA_PATH`, relativa a `compose.yml` come per compose); con la licenza su
    un altro computer il container prende `02:42:b1:00:00:01`, il predefinito di `compose.yml`. Sul server non si chiede
    mai: serve `--sposta-prosa`. Con Podman senza root il MAC non si assegna, e non si controlla né registra nulla.
- **PROCHECK:** licenza accademica senza chiave.

Il codice di BioLoom ha licenza MIT (`LICENSE` nella cartella di BioLoom; `license = "MIT"` in `backend/pyproject.toml`,
espressione SPDX che richiede setuptools 77 o successivo, e in `frontend/package.json`). I componenti di terzi delle
immagini e dell'interfaccia, con le licenze e il sorgente del software GPL e LGPL, sono in `THIRD_PARTY_NOTICES.md`.

## Calcoli

Le sezioni seguono il percorso dalla sequenza al modello: sequenza e template, modellazione, qualità, analisi del
modello, report. Ognuna dice dove sta la logica pura (`domain/`), quale adattatore esegue i programmi (`engines/`),
quale servizio crea i job (`services/`) e quali rotte li espongono (`api/`).

### Disordine, bassa complessità e domini

Logica in `domain/disorder.py`, copertura del bersaglio in `services/coverage.py`, rotte in `api/bio.py` (F5.5).

Dove il modello per omologia sarà poco affidabile, dalla sola sequenza (logica pura, nessuna dipendenza nuova). Tre
segnali separati.

| Segnale | Metodo | Parametri | Fonte |
|---|---|---|---|
| Disordine per residuo | FoldIndex = 2,785·⟨H⟩ − \|⟨R⟩\| − 1,151; ⟨H⟩ idropatia media di Kyte-Doolittle portata su 0–1 ((KD + 4,5)/9), ⟨R⟩ carica netta media a pH 7 (K, R +1; D, E −1; H neutra). Sotto 0 probabilmente disordinato | finestra di 51 residui centrata, passo 1 | Prilusky et al., Bioinformatics 21:3435, 2005; confine carica-idropatia di Uversky et al., Proteins 41:415, 2000; Kyte & Doolittle, J. Mol. Biol. 157:105, 1982 |
| Regioni disordinate lunghe | almeno 30 residui con FoldIndex < 0; fino a 3 residui sopra 0 tollerati tra due tratti (meno di un giro d'elica, 3,6 residui), che non contano nei 30 | | "long disordered regions" di Ward et al., J. Mol. Biol. 337:635, 2004 |
| Bassa complessità | SEG: finestre con entropia ≤ K1 innescano, estensione alle finestre ≤ K2, poi il sottotratto di composizione meno probabile (P0, accorciamento fino a 100 residui) come `seg`; segmenti sovrapposti fusi | finestra 12, K1 2,2 e K2 2,5 bit | Wootton & Federhen, Comput. Chem. 17:149, 1993 |
| Domini | InterPro (`remote.interpro_domains`): voci `domain`, oppure le superfamiglie omologhe se non ce ne sono; voci sovrapposte per almeno metà del tratto più corto fuse (firme di banche dati diverse dello stesso dominio) | solo con un'accessione UniProt | |

- **Perché FoldIndex e non IUPred:** IUPred (Dosztányi et al. 2005) converte l'energia di coppia in probabilità con la
  sua matrice e una distribuzione tabulata, file distribuiti con il programma per uso accademico: ricostruirli dalle
  pubblicazioni non darebbe gli stessi numeri. FoldIndex è definito da due costanti pubblicate e da una scala standard.
- **Estremità:** il server di FoldIndex dà valori solo ai centri delle finestre complete. Qui ogni residuo ha un valore e
  nei primi e negli ultimi 25 la finestra si accorcia (fino a 26 residui). Con una finestra fissa appoggiata al bordo,
  l'ultima finestra dell'ubiquitina (26–76, FoldIndex −0,018) si ripeterebbe su 25 residui e darebbe una regione
  disordinata 32–76 a una proteina ripiegata (test).
- **Lettere:** X non entra nelle medie; B, Z, J come media delle due lettere, U come C, O come K. In SEG le finestre con
  lettere non standard non si valutano. Una sequenza nucleotidica (≥ 90% di A, C, G, T/U, N, `sequences.detect_kind`) si
  rifiuta (`not_protein`).
- **Limiti** (anche nella risposta, `limits`, e nell'interfaccia): non è IUPred2A né un predittore di deep learning
  (molto più accurati nelle valutazioni CAID); non vede il disordine con composizione da proteina ripiegata
  (l'N-terminale dell'α-sinucleina risulta ordinato) né i loop disordinati corti; i confini sono incerti di circa mezza
  finestra; non conosce il contesto (partner, cofattori, ponti disolfuro).

**Valori di riferimento** (test in `test_disorder.py`):

| Sequenza | Risultato |
|---|---|
| ubiquitina umana (1UBQ, 76) | nessuna regione, frazione disordinata 0; FoldIndex per residuo tra −0,17 e +0,26; FoldIndex della sequenza intera +0,090, uguale a 2,785·(GRAVY + 4,5)/9 − 1,151 con il GRAVY di Biopython (−0,489, carica netta 0) |
| α-sinucleina umana (P37840, 140) | una regione 98–140 (attesa ~96–140: la coda acida C-terminale), frazione 0,307; nessuna regione prima di 90 (limite noto) |
| ubiquitina con 25 Q dopo il residuo 40 | SEG: esattamente il tratto poli-Q 40–66 ("Q 27"); ogni residuo diverso aggiunto rende la composizione più probabile (P0) |
| ubiquitina (SEG) | nessuna regione a bassa complessità |

**Copertura e avvisi prima di modellare** (`disorder.coverage_assessment`, `services/coverage.CoverageService`):

- **Copertura** = residui del bersaglio con un residuo del template di fronte:
  - template della libreria (Modellazione, allineamento automatico): Smith-Waterman locale (BLOSUM62, gap 11/1, come i
    motori di ricerca) su ogni catena scelta, unite; è una stima dell'allineamento di MODELLER. Per un template AlphaFold con
    il ritaglio attivo i residui tolti (`plddt.low_confidence`: soglia e frammenti isolati, come in modellazione) non
    coprono (test: pLDDT < 70 su 1–5 e 71–76 → coperti 6–70);
  - PIR (Modellazione, da documento): le colonne del PIR, esatte; bersagli a più catene non valutati (nota);
  - intervalli (Ricerca): il tratto `query_start`–`query_end` di ogni hit, valutato da solo (candidati alternativi);
    template e PIR si valutano anche insieme (`combined`), come li usa MODELLER.
- **Avvisi:** `disorder_uncovered` (warn) se almeno metà di una regione disordinata lunga è scoperta: MODELLER la
  costruisce senza vincoli, come un tratto esteso senza significato strutturale; `disorder_covered` (info) altrimenti:
  nel template può essere ordinata da un partner o dal cristallo, oppure la predizione è sbagliata (FoldIndex segna
  come disordinate anche proteine ripiegate molto idrofile: 3IU0, GRAVY −1,07, 89% della catena). Domini: coperti dal 90% (i confini delle firme InterPro
  sono approssimati di qualche residuo), `domain_uncovered` sotto il 10%, `domain_partial` in mezzo.
- **Suggerimento:** con almeno due domini e uno non coperto del tutto, "modella per domini" (template per dominio, oppure
  più template insieme); con un dominio solo, cercare un template che lo copra (NCBI BLAST, AlphaFold DB); con le sole
  regioni disordinate scoperte, escluderle dal bersaglio o leggerle come ipotesi.
- **Domini sul bersaglio:** i confini di InterPro stanno sulla sequenza di UniProt (`remote.uniprot_entry`): valgono se il
  bersaglio è quella sequenza o un suo tratto (spostamento, nota "Bersaglio = residui 4–79 di UniProt …"), altrimenti si
  tralasciano con una nota. UniProt e InterPro restano in cache 7 giorni. Senza accessione nessun dominio e nessuna
  richiesta remota; accessione sconosciuta o servizi non raggiungibili danno una nota, non un errore; un'accessione con un
  formato non valido è un 400 (`invalid_accession`).

**Rotte** (`api/bio.py`):

| Rotta | Corpo | Risposta ed errori |
|---|---|---|
| `POST /api/bio/disorder` | `{"sequence"}`: testo o FASTA con una sola sequenza | punteggi (`scores`, scala di FoldIndex), `regions`, `fraction` (residui nelle regioni), `below_fraction`, `low_complexity`, `params`, `limits`. Nessuna rete. 400 `sequence_too_long` oltre `max_sequence_length` residui (come la modellazione), `empty_sequence`, `not_protein`, `multiple_sequences`; 422 oltre 200 000 caratteri |
| `POST /api/bio/coverage` | `sequence`, `accession`, `templates` [{id, chains}] (al massimo 10), `alignment` (PIR), `ranges` [{label, start, end}] (al massimo 500), `plddt_min` | `disorder`, `domains`, `domains_info` (fonte o motivo dell'assenza), `templates` (per template o intervallo: copertura, avvisi, domini, suggerimento; `error` se il template non c'è o non ha la catena, gli altri si valutano lo stesso), `combined` |

**Consenso** (`consensus.disorder_signal`): FoldIndex per catena sulle lettere dei residui presenti nel file, nell'ordine
del rapporto (per un modello di MODELLER è la sequenza intera del bersaglio; in una struttura sperimentale con residui
mancanti la finestra scorre sui residui presenti). Il campo `disorder` ha punteggi, residui nelle regioni, regioni e una
frase; `informative: true`. Una regione problematica con almeno metà dei residui in una regione disordinata diventa
`expected` e il testo lo dice ("…, in una regione disordinata predetta (attesa poco affidabile)"), come la coda di una
proteina costruita senza template. Test: stesso rapporto con la sequenza dell'α-sinucleina e con una sequenza tutta I →
stesso punteggio, stessi segnali e stesse regioni; su 1UBQ, `ubq_dimer.pdb` (per catena, stessi valori nelle due catene)
e `ubq_zn.pdb` nessuna regione. `VERSION` passa a 2; i rapporti salvati prima (versione 1, senza `disorder`) funzionano: le
rotte del consenso e del report lo ricalcolano.

**Interfaccia:**

- **Sequenze:** riquadro "Disordine, bassa complessità e domini" con la mappa delle caratteristiche (tracce Disordine,
  Bassa complessità, Domini InterPro con l'accessione, Struttura secondaria GOR; le tracce vuote lo dicono), sintesi e
  "Metodi e limiti". Una sola richiesta (`/api/bio/coverage` senza template).
- **Modellazione:** gli avvisi (`combined`) appena bersaglio e template (o il PIR) sono scelti, con la soglia pLDDT se c'è
  un template AlphaFold. Dalla F8a arrivano dai controlli preventivi (stessa verifica, una sola richiesta, vedi [Controlli
  preventivi](#controlli-preventivi)) e i loro avvisi chiedono conferma all'avvio;
  prima venivano da `/api/bio/coverage` e non bloccavano il lancio. L'accessione per i domini
  (`modelingAccession`) viene dal bersaglio della modalità in uso: in modalità PIR dalla voce `sequence` del documento
  (sequenza del progetto uguale, oppure codice `sp|ACC|…`), non dal selettore della modalità automatica, nascosto.
- **Ricerca:** per ogni hit un badge ("disordine scoperto · dominio scoperto"), gli avvisi nella riga espansa e, sopra la
  tabella, quelli del template consigliato.
- **Qualità:** la frase del disordine sotto le regioni problematiche e una riga "disordine (info)" nel grafico del consenso
  per residuo; le regioni `expected` in arancione invece che in rosso.
- Logica pura in `lib/disorder.ts` (tracce, richieste, badge, ordine degli avvisi), componente `CoverageAlerts`.

### Ricerca di template

- **RCSB** (predefinito): Search API (MMseqs2 lato server) → sequenze di catena via GraphQL → riallineamento
  Smith-Waterman locale per identità, copertura e coordinate. Ritenta automaticamente i timeout 5xx di RCSB.
- **NCBI QBlast**: conforme alle linee guida (tool/email, polling ogni 60 s, attesa massima 45 min).
- **BLAST+ locale**: `blastp` contro `data/databases/pdbaa` (scaricabile e aggiornabile da NCBI con un job). L'XML dei
  risultati (`-outfmt 5`) va con `-out` in un file a nome unico (gli elementi di una pipeline condividono la cartella)
  nella cartella temporanea del job (`.tmp` nello scratch), fuori dai risultati e dai file salvati nella cache, e si
  elimina appena letto (`search._local_blast`): dall'output del processo `JobContext.run` conserva solo le ultime righe,
  e con molti risultati (decine di migliaia di righe) l'XML arrivava troncato. Risultati invariati, verificati con 800
  hit e oltre 26.000 righe.
- **MMseqs2 locale**: `easy-search -s 7.5` sul database creato da pdbaa (`blastdbcmd` → `createdb`), con
  l'indice locale se presente.
- **AlphaFold DB** (modelli predetti, vedi [sotto](#modelli-alphafold-db)): accessione UniProt indicata, oppure
  proteine Swiss-Prot più simili trovate con NCBI QBlast.
- **Metadati RCSB degli hit** (motori sul PDB): metodo, risoluzione, titolo e, dalla versione della scelta
  ragionata, R-free, data di rilascio, ligandi e stato oligomerico (vedi sotto).
- Template consigliato: punteggio composito più alto (vedi [Scelta ragionata dei
  template](#scelta-ragionata-dei-template)); a parità di punteggio una struttura sperimentale
  prima di un modello predetto, poi identità × copertura, la miglior risoluzione, il pLDDT medio più alto. Un modello
  AlphaFold è un'ipotesi e non un dato: la componente del metodo lo pesa meno, quindi vince solo se allinea
  chiaramente meglio il bersaglio (prima bastava un punto di identità in più).

### Scelta ragionata dei template

Logica pura in `domain/template_rank.py` (F5.2), testata su hit sintetici dell'ubiquitina di lievito su 1UBQ. Il punteggio si calcola **dopo** la cache
dei calcoli, a ogni lettura (`search.search` → `rank_hits`): cambiare pesi o scale non richiede di invalidare le
ricerche.

**Punteggio 0–100.** Ogni componente porta il valore grezzo su 0–100 con una funzione lineare a tratti
(`consensus.piecewise`, saturata fuori dai punti); come nel consenso di qualità la soglia "affidabile" cade a 70 e
quella "problematica" a 40 (fasce buona ≥ 70, intermedia 40–70, scarsa < 40). Il punteggio è la media pesata delle
componenti disponibili: un dato assente (motore locale senza metadati, R-free non depositato) non penalizza. Ogni hit
ha `rank_score`, `rank_band`, `rank_twilight` e `rank_reasons` (per componente `key`, `label`, `score` 0–100 o null,
`weight`, `text` in italiano).

| Componente | Peso | Scala (valore → punteggio) | Fonte |
|---|---|---|---|
| Identità | 30 | 0 → 0; curva di Rost pI(L) → 40; max(50%, pI + 10) → 70; 100% → 100 | Rost 1999, Protein Eng 12:85 (eq. 2, n = 0: pI = 480 · L^(−0,32(1+e^(−L/1000))), 100% fino a 11 residui, 19,5% oltre 450); Baker & Šali 2001 (oltre il 50% modelli di alta accuratezza) |
| Copertura del bersaglio | 30 | lineare 0–100% | frazione del modello con vincoli dal template; 40% (soglia predefinita della pipeline) = confine della fascia scarsa |
| E-value | 5 | −log10 E: 0 → 0, 2 → 40, 3 → 70, ≥ 10 → 100 | Pearson 2013 (E < 10⁻³ omologia affidabile) |
| Risoluzione | 12 | raggi X e crio-EM: 1,0 Å → 100, 2,5 → 70, 3,5 → 40, 5,0 → 0; NMR: 60 fisso; predetti: pLDDT 50 → 0, 70 → 40, 90 → 70, 100 → 100 | Wlodawer et al. 2008; fasce di AlphaFold DB (Tunyasuvunakool et al. 2021); NMR scelta BioLoom |
| R-free | 5 | 0,20 → 100, 0,25 → 70, 0,30 → 40, 0,40 → 0 (solo diffrazione) | Brünger 1992; Kleywegt & Brünger 1996 (oltre 0,40 errori gravi); punti intermedi scelta BioLoom |
| Metodo | 8 | diffrazione 100, crio-EM 90, NMR 80, altri sperimentali 50, predetto 40 | scelta BioLoom: pesa solo ciò che la risoluzione non dice |
| Lacune dell'allineamento | 7 | lacuna più lunga 0 → 100, 4 → 90, 8 → 70, 15 → 40, 30 → 0; −2 per ogni lacuna oltre la seconda | Fiser, Do & Šali 2000 (errore dei loop crescente con la lunghezza) |
| Data di rilascio | 3 | 1990 → 40, 2008 → 80, 2014 → 100 | R-free dal 1992, dati sperimentali obbligatori dal 2008, rapporti di validazione wwPDB dal 2014 (Read et al. 2011; Gore et al. 2017) |
| Ligandi e cofattori | 0 | informativo: olo (codici senza acqua e additivi di cristallizzazione) o apo | — |
| Stato oligomerico | 0 | informativo: assemblaggio biologico 1 | — |

- **Pesi:** identità e copertura pesano uguale (l'identità dice quanto è accurata la parte con template, la copertura
  quanta parte ne ha uno): un template completo al 40% batte uno quasi identico che copre metà del bersaglio, uno in
  zona crepuscolare no. La qualità sperimentale pesa sui dettagli, non sul ripiegamento; l'E-value dipende già da
  identità e lunghezza.
- **Identità sulla curva di Rost:** l'identità confrontata con la curva è quella sulle coppie allineate (colonne con
  un residuo in entrambe le righe, come in Rost), che per allineamenti con lacune è un po' più alta di quella della
  tabella (sulla lunghezza dell'allineamento); la motivazione riporta il valore e i residui allineati. La formula
  pubblicata scende fino a circa 420 residui e poi risale di pochi centesimi verso 19,5%.
- **Regola della zona crepuscolare** (scelta BioLoom): sotto la curva la copertura conta al più quanto l'identità;
  un template in zona crepuscolare resta al massimo intermedio (0,3 × 40 + 0,3 × 40 + 0,4 × 100 = 64).
- **Valori di riferimento** (test): ubiquitina di lievito su 1UBQ (73 identici su 76, 1,8 Å, 1987, senza R-free):
  identità 97,6 (curva di Rost 33,2% per 76 residui), risoluzione 84, data 40, **punteggio 95,3**; lo stesso
  allineamento a 3,8 Å con R-free 0,33 dà 87,5, una struttura a 1,5 Å al 35,5% di identità 82,0; NMR 90,6; il
  modello AlphaFold con pLDDT 88,6 89,8 (la struttura sperimentale vince, la sola metà del bersaglio no).

**Template complementari** (`select_complementary`, fino a `max_templates`, predefinito 3):

1. il primo è il migliore (`sort_key`, lo stesso di `best_template`);
2. i successivi, in ordine di merito, si scartano se sono in zona crepuscolare o in fascia scarsa; entrano se coprono
   almeno 10 residui del bersaglio non coperti; altrimenti si scartano se ridondanti (identità reciproca > 95% su
   almeno 10 residui in comune con uno scelto); altrimenti entrano se migliorano l'identità locale (+20 punti sulla
   finestra centrata di 9 residui, come la modalità Confidenza) in almeno 10 residui; altrimenti si scartano.

| Soglia | Valore | Motivo |
|---|---|---|
| Template al massimo | 3 (2–5) | Larsson et al. 2008: con MODELLER due o tre template scelti bene migliorano in media il modello, di più non aiuta |
| Residui nuovi | 10 | un tratto più corto MODELLER lo ricostruisce come un loop con accuratezza in genere accettabile (Fiser et al. 2000) |
| Ridondanza | identità reciproca > 95% | a quell'identità le strutture sono quasi uguali (Chothia & Lesk 1986: RMSD del nucleo circa 0,45 Å) |
| Guadagno locale | +20 punti su 9 residui | 2 residui identici in più nella finestra |

- **Confronto transitivo:** le posizioni sul bersaglio e l'identità reciproca vengono dagli allineamenti della
  ricerca (`query_aligned`, `subject_aligned`, `query_start`), senza scaricare le strutture: il filtro di ridondanza
  è di sola sequenza. Un TM-score > 0,9 fra i candidati richiederebbe di scaricarli tutti; oltre il 95% di identità le
  strutture sono comunque quasi uguali, e due template strutturalmente simili ma divergenti in sequenza non sono
  ridondanti come informazione (vincoli indipendenti). Senza le righe dell'allineamento si usano gli intervalli.
- **Costrutto più lungo:** un candidato quasi identico a uno scelto nella regione comune entra se copre almeno 10
  residui nuovi (porta vincoli dove non ce ne sono; nella regione comune i vincoli coincidono), con una nota.
- **Regioni con pochi residui in comune:** un complementare con meno di 10 residui del bersaglio in comune con
  ciascuno dei template già scelti, e di un'altra voce PDB (le catene della stessa voce condividono le coordinate), ha
  nella motivazione l'avviso che l'orientamento relativo delle regioni non è vincolato (nessun vincolo di distanza fra
  residui di template diversi) e `unanchored: true`. La modellazione ripete l'avviso sull'allineamento usato (sotto).
- **Risultato:** `templates` (con `role` principale/complementare, `reason`, `new_residues`, `ranges`,
  `unanchored` per i complementari), `excluded`
  (con `reason`), `not_examined` (oltre il massimo), `coverage` e `covered_residues` dell'insieme. Costo: identità
  locale con somme prefisse, calcolata una volta per template scelto (400 candidati su 1000 residui: 0,12 s
  misurati nel container di test).

**Ricerca (`handle_search`):** ogni hit ha punteggio e motivazioni, il risultato ha `best` (con il nuovo punteggio) e
`suggested` (selezione con 3 template al massimo). Il log riporta i template consigliati.

**Metadati RCSB** (`remote.rcsb_metadata`): la query GraphQL chiede anche `refine { ls_R_factor_R_free pdbx_refine_id
}` (R-free dell'affinamento a raggi X se ce ne sono più) e `assemblies { pdbx_struct_assembly { id oligomeric_details
oligomeric_count } rcsb_struct_symmetry { kind oligomeric_state } }` (assemblaggio 1, che non è sempre il primo
dell'elenco, e la sua simmetria globale: "Monomer", "Homo 2-mer"). Campi verificati sul servizio reale (settembre
2026, `test_remote.py` con `-m network`: 3IU0 R-free 0,244, 1UBQ senza R-free, 4HHB "Hetero 4-mer"). Negli hit:
`rfree`, `released`, `ligands`, `oligomeric_state`, `oligomeric_count` (`remote.hit_metadata`).

- **Cache delle risposte:** le voci hanno `version: 2`. Una voce senza versione (scritta prima, senza R-free) si
  riscarica; se RCSB non risponde si usa lo stesso, segnata `stale`, e la ricerca che la usa è parziale (non entra
  nella cache dei calcoli): un computer senza rete non perde metodo e risoluzione già noti.
- **Risposte GraphQL con soli errori** (query rifiutata): errore del servizio, non più un elenco vuoto che diventava
  "nessun template trovato".
- **Cache dei calcoli:** il descrittore della ricerca ha `version: 2` (`SEARCH_VERSION`) per i motori sul PDB, e il
  risultato `search_version`; `accept` scarta anche alla lettura una voce senza. Le ricerche della versione precedente
  hanno un'altra chiave e si rifanno (i motori remoti scadevano comunque dopo 7 giorni); il motore `alphafold` non
  cambia e tiene le sue voci.

**Pipeline, modalità `multi`** (`template_mode: "multi"`, `max_templates` 2–5, predefinito 3): per ogni sequenza la
ricerca e poi `select_complementary`; `run_modeling` riceve tutti i template scelti.

- **Campi dell'elemento:** `template` = gli id separati da virgole, `identity` ed `evalue` del principale, `coverage`
  = copertura dell'insieme (anche per il filtro della zona crepuscolare); nelle metriche `templates` (ruolo e
  motivazione), `combined_coverage`, `excluded_templates`. Il log dell'elemento ha una riga per template scelto ed
  escluso (le prime 10). Nessuna colonna nuova: lo schema SQLite non cambia.
- **Interfaccia:** la colonna Template elenca i template (complementari segnati), con le motivazioni nel suggerimento;
  il report dell'elemento dice "più template complementari (al massimo N)".

**Pipeline, sequenze × template** (`template_mode: "fixed"`, `fixed_combination: "each"`; predefinito `"combined"`,
un modello per sequenza con tutti i template, al massimo 10): ogni sequenza si modella su ogni template, uno alla volta.
M sequenze e N template danno M × N elementi; nessun limite su N (resta quello di 100.000 sequenze per pipeline,
`pipeline.MAX_SEQUENCES`).

- **Pipeline grandi** (decine di migliaia di elementi, su un server):
  - l'output dei programmi esterni di ogni elemento (MODELLER, DOPE, PROCHECK, ProSA, mkdssp: ~1500 righe, ~100 KB per
    coppia) va in `items/<elemento>/programmi.log`, un blocco per processo con orario, programma e codice di uscita
    (`JobContext.sub(output_log=...)`); nel `job.log` restano i messaggi di BioLoom;
  - i parametri del job (con il FASTA: ~8 MB per 20.000 sequenze) si leggono una volta e restano in una cache di 32
    pipeline (`PipelineService._params`; non cambiano dopo la sottomissione): tempo residuo, pagine e matrice non li
    rileggono dal database;
  - il dettaglio di un job (`GET /api/jobs/{id}`) elenca al massimo 2000 file, fermando la visita delle cartelle
    (`files_truncated: true`); tutti i file restano nell'archivio e nell'esportazione;
  - la matrice sequenze × template con oltre 2000 sequenze si rilegge al più una volta al minuto mentre la pipeline
    gira;
  - fino a 100.000 sequenze per pipeline (`pipeline.MAX_SEQUENCES` = `fileformats.MAX_RECORDS`: le stesse che si
    leggono da un file, fino a 200 MB, 1 GB decompresso). Misure con 100.000 × 3 = 300.000 elementi su un PC a 4
    thread: avvio 7–8 s (picco ~1,1 GB, una volta), annullamento 0,4 s, pagina degli elementi, matrice, elenco dei
    job e tempo residuo sotto il mezzo secondo, CSV 9–10 s;
  - l'esecuzione legge solo gli indici degli elementi in attesa e nome e sequenza quando ciascuno parte, e affida al
    pool al più 2 × parallelo elementi alla volta (`PipelineService.handle`): memoria costante e, all'annullamento,
    gli elementi mai avviati diventano annullati con un solo UPDATE;
  - indice `idx_pipeline_items_status` (job, stato, fine, inizio) nello schema di base (creato anche sui database
    esistenti, senza alzare la versione): conteggi per stato e tempo residuo, che stima la durata tipica dagli ultimi
    1000 elementi conclusi (`ETA_SAMPLES`), senza leggere le righe con le sequenze;
  - `GET /api/jobs/{id}`, l'avvio e la ripresa restituiscono i parametri di una pipeline grande con i primi 20.000
    caratteri del FASTA (`fasta_truncated`, `fasta_bytes`) e il numero di sequenze (`sequence_count`) al posto del loro
    elenco (`pipeline.public_job`); database, `parametri.json` dello ZIP e pacchetti hanno i parametri interi;
  - le pagine degli elementi M × N elencano i nomi delle sequenze (filtro) solo fino a 1000 (`PAIRS_LISTED`), sempre
    con `sequence_count`; oltre, si cerca per nome;
  - `GET /api/pipeline/{id}/matrix?page=&size=` dà una pagina di sequenze, con il riepilogo di tutta la matrice
    (`summary`: coppie per esito, sequenze chiuse, vittorie dei template) calcolato in SQL; senza `page`, la matrice
    intera;
  - nella pagina Pipeline un FASTA oltre ~2 MB non si mostra nel campo di testo (solo numero di sequenze, dimensione e
    «Svuota»).
- **Elementi:** uno per coppia, in ordine di sequenza: `idx` = sequenza × N + template (`pipeline.pair_of`), nome
  `<sequenza>__<template>` (cartella `items/<idx>_<sequenza>__<template>/`), `template` = id del template. L'etichetta
  del template è id e catene sanificati (`1UBQ_A`, `template_labels`, salvate in `params.template_labels`). Nessuna
  colonna nuova: sequenza e template si ricavano dalla posizione, quindi la ripresa (che azzera le righe) non li perde.
  Lo stesso template con le stesse catene due volte è un errore della sottomissione.
- **Modellazione:** `run_modeling` con il solo template della coppia e il nome della sequenza come bersaglio; con
  l'analisi di qualità richiesta, ogni coppia analizza il proprio modello migliore per DOPE, come la pipeline normale.
  Identità e copertura dall'allineamento (`report.alignment_identity`), zona crepuscolare solo segnalata. Nel PDB la
  riga `REMARK 999 BIOLOOM PIPELINE SEQUENCE <s> TEMPLATE <t>`, aggiunta alle REMARK dell'analisi (`model_remarks`).
  Nelle metriche lo zDOPE resta quello di MODELLER anche dopo l'analisi.
- **Migliore della sequenza:** quando tutte le coppie di una sequenza sono concluse (`_close_sequences`, una volta per
  esecuzione, sotto un lock) il modello con lo zDOPE di MODELLER più basso (a parità il DOPE, poi l'ordine dei template)
  diventa il migliore (`metrics.best_in_sequence`). A fine esecuzione si chiudono anche le sequenze non toccate da una
  ripresa, così il migliore si ricalcola sempre sulle coppie completate.
- **Consultazione:** `GET /api/pipeline/{id}/items` accetta `sequence`, `template` (indici) e `best`; ogni riga ha
  `pair` (indici, nomi, id del template) e `best_in_sequence`, la risposta `pairs` (nomi di righe e colonne).
  `GET /api/pipeline/{id}/matrix`: matrice sequenze × template con stato, zDOPE, DOPE, GA341, molpdf, identità,
  copertura, voto e migliore di ogni coppia (400 `matrix_unsupported` per le altre pipeline).
- **ZIP:** una cartella per template, `template/<template>/<sequenza>__<template>.{pdb,html,quality.json}` (rapporto
  di qualità di ogni coppia, se l'analisi era richiesta); `metriche.csv`, un'unica tabella con una riga per coppia
  (`CROSS_FIELDS`: sequenza, template, `best_in_sequence`, percorso del modello nell'archivio, identità, copertura,
  zona crepuscolare, DOPE, zDOPE, GA341, molpdf, voto, Ramachandran favorite e outlier, clashscore, elica, filamento,
  residui, consenso e fascia dal rapporto di qualità, ProSA e PROCHECK con l'analisi estesa); `migliori.csv` (una riga
  per sequenza con le stesse metriche del migliore); `parametri.json`, `LEGGIMI.txt`. Niente matrici separate: la
  tabella si riassume per sequenza o per template con una tabella pivot. `GET /api/pipeline/{id}/export.csv` restituisce
  la stessa tabella (percorsi nei risultati del job). Il report della coppia ha il titolo "sequenza × template".
- **Controlli preventivi:** nota `pipeline_pairs` con il numero di coppie; stima su M × N modellazioni, ognuna con la
  sua analisi.
- **Interfaccia:** scelta "Uso dei template" sotto il selettore dei template fissi, con il conteggio delle coppie (il
  selettore non limita più il numero di template). Nei risultati filtri per sequenza e per template, "solo il
  migliore", badge "migliore", zDOPE sotto il DOPE e la vista "Matrice sequenze × template"
  (`components/PipelineMatrix.tsx`, logica in `lib/pipelineMatrix.ts`): celle colorate per la metrica scelta (DOPE e
  molpdf rispetto alla riga), stella sul migliore, conteggio dei migliori per template, clic per il modello nel 3D.

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
- **Riconoscimento dei modelli AlphaFold** (`plddt.predictor`, nome storico `is_alphafold_model`, unico per libreria,
  rapporto di qualità, copia preparata del template e, dalla F9.3, predizioni di ESMFold: vedi [Confronto con un
  predittore esterno](#confronto-con-un-predittore-esterno)): i valori della colonna B-factor (tutti gli atomi degli
  amminoacidi) stanno in 0–100 e non sono tutti nulli, e
  - l'intestazione ha i segni certi di AlphaFold DB (`is_alphafold_text`): TITLE "ALPHAFOLD … PREDICTION" senza
    EXPDTA (sempre presente nelle strutture sperimentali), oppure ModelCIF con `_ma_qa_metric` e il nome AlphaFold
    senza `_exptl.method`;
  - oppure il nome segue lo schema di AlphaFold DB, `AF-<accessione UniProt>-F<n>[-model_v<k>]` (file scaricati,
    caricati con prefisso, id di libreria e loro copie `AF-…_A.pdb`), e l'intestazione non dichiara un'altra
    origine (`declares_other_origin`: EXPDTA, `_exptl.method`, anche in forma di loop, oppure `REMARK   6 MODELLER`).

  Non contano le menzioni di AlphaFold altrove (per esempio `REMARK 200 STARTING MODEL: ALPHAFOLD` di una struttura
  risolta per sostituzione molecolare): i B-factor di una struttura sperimentale o di un modello MODELLER non vanno
  mai presi per pLDDT. Un file di AlphaFold caricato dall'utente diventa `predicted` con lo stesso criterio. Il
  predittore riconosciuto (`predictor`: `alphafold` o `esmfold`) si salva nei metadati e dà il nome nei testi
  (`plddt.predicted_text`: log della preparazione, note dei risultati, controlli preventivi del ritaglio, report,
  distintivi dell'interfaccia; riga `REMARK 999 BIOLOOM TEMPLATE <codice> ALPHAFOLD|ESMFOLD|PREDICTED …` del
  modello): una predizione di ESMFold usata come template non si chiama mai AlphaFold, e un modello predetto senza
  predittore noto né nome di AlphaFold DB è «modello predetto». I
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

### Allineamenti

- **Pairwise**: Biopython `PairwiseAligner` (NW/SW, BLOSUM/PAM/GONNET, NUC.4.4), gap terminali gratuiti nel globale.
- **Multiplo**: Clustal Omega → MAFFT → MUSCLE se installati, altrimenti *center-star* integrato; matrice di identità.
- **Strutturale**: US-align/TM-align se installati, altrimenti sovrapposizione di Kabsch con ricerca del
  TM-score massimo (algoritmo del programma TM-score) su un allineamento di sequenza; PDB sovrapposto per il viewer e
  confronto per residuo (vedi [Confronto strutturale](#confronto-strutturale)).

### MODELLER

Il server non importa mai `modeller`: ogni operazione è uno script generato ed eseguito in un
sottoprocesso. Nel container è MODELLER 10.8 del pacchetto .deb; in esecuzione nativa di sviluppo si può
indicare un altro interprete (impostazione `modeller_python` o `BIOLOOM_MODELLER_PYTHON`). In modalità server vale solo
la variabile, come `esmfold_url`: `SettingsStore` con `server` la usa al posto del valore salvato e `PUT /api/settings`
rifiuta un altro valore (403 `modeller_python_locked`), perché è un programma che il server esegue; anche la verifica da
riga di comando (`selftest`) usa le impostazioni del server. `compose.yml` e `bioloom-podman.sh` passano al container
la variabile di `server.env`, un percorso nel container; vuota vale il rilevamento automatico, cioè l'interprete
dell'immagine, che si usa anche quando a quel percorso non c'è un Python con MODELLER (`modeller_candidates`). Flusso
di un job `model`:

1. **Template**: conversione in PDB pulito (catene scelte, solo i ligandi scelti, niente acqua, MSE→MET; vedi
   [Ligandi, cofattori e metalli](#ligandi-cofattori-e-metalli)).
2. **Allineamento**: se non viene fornito un PIR, MODELLER stesso lo calcola (`align2d` con un template,
   `salign` strutturale + allineamento sul profilo con più template, con il controllo degli `align2d` di ogni
   template: vedi [Più template](#più-template-salign-o-align2d-uniti-sul-bersaglio)). Il PIR fornito dall'utente
   viene validato (lunghezze, catene, caratteri) e riallineato ai codici/file dei template preparati. In entrambi i casi le colonne
   '.' dei ligandi si riscrivono secondo la scelta delle opzioni.
3. **AutoModel / DOPELoopModel** con raffinamento (`very_fast`/`fast`/`normal`/`thorough`, impostazioni in
   `modeller.REFINEMENT_SCRIPT`: `a.very_fast()`; predefiniti di AutoModel con la dinamica `refine.very_fast`; dinamica
   `refine.fast`; VTFM `autosched.slow` con 300 iterazioni, dinamica `refine.slow` e ciclo ripetuto 2 volte; motivi e
   misure in [Prestazioni](#misure-per-i-controlli-preventivi)), vincoli di struttura secondaria e di distanza, regioni
   di loop, seme casuale. I risultati (`molpdf`, DOPE, GA341,
   zDOPE) sono scritti in `models.json` direttamente da `a.outputs`.
4. **Selezione e qualità**: modello migliore = DOPE minimo; analisi completa sul migliore (o su tutti, fino a 3
   modelli insieme), con righe `REMARK 999 BIOLOOM` di tracciabilità nel PDB.

Con un bersaglio a più catene (modalità Complesso o PIR a più catene) l'allineamento è per catena, le catene del modello
prendono i nomi del template e l'analisi comprende le interfacce: vedi [Complessi e
omo-oligomeri](#complessi-e-omo-oligomeri).

Nella pipeline un template usato da più elementi si prepara una volta per esecuzione e gli altri ne ricevono una copia
(vedi [Prestazioni](#prestazioni)).

### Più template: salign o align2d uniti sul bersaglio

Logica in `domain/multitemplate.py`, script in `engines/modeller.py`.

Con più template lo script di allineamento esegue `salign` (template fra loro, poi il bersaglio sul loro profilo:
il metodo del tutorial di MODELLER) e, per ogni template, `align2d` del bersaglio (`alignment_pair_<n>.ali`); se
`salign` solleva un'eccezione lo script prosegue. `domain/multitemplate.choose` confronta per ogni template i residui
identici al bersaglio con `salign` e con `align2d`:

- se `salign` ne perde più della metà per un template (allineamento spostato: scende verso l'identità casuale, mentre
  fra due buoni allineamenti le identità differiscono di pochi residui; sotto 5 identici il confronto non vale) o non
  è riuscito, si usa l'unione degli `align2d` sul bersaglio (`merge_on_target`): ogni template resta allineato come
  nel suo allineamento a coppie, le inserzioni dei template vanno in colonne con un gap nel bersaglio. Il file PAP di
  `salign` si toglie;
- altrimenti resta `salign`, come prima;
- template a più catene o con ligandi ('/' e '.' da tenere in colonna) non si uniscono: resta `salign`; se un template
  risulta spostato (`displaced` nella scelta) il messaggio è un **avviso** del job (`warnings` del risultato della
  modellazione e dell'allineamento align2d, pagina Modellazione, sezione Avvisi del report), non solo una riga del log:
  il modello è costruito su un allineamento che BioLoom sa probabilmente spostato (con i frammenti di 1UBQ, TM-score
  0,35). Il job non fallisce: l'allineamento si può correggere nell'Editor PIR.

La scelta è nel log del job. Misurato con due frammenti di 1UBQ come template dell'ubiquitina di lievito
(`test_template_rank.py`): con il solo `salign` il frammento 30–76 finiva allineato dal residuo 1 del bersaglio e il
modello aveva TM-score 0,35 rispetto a 1UBQ (voto D); con gli `align2d` uniti 0,998 (frammenti 1–50 e 30–76) e 0,997
(1–40 e 41–76, disgiunti ma nello stesso sistema di riferimento), come con 1UBQ intero (0,998). Con due template
completi (1UBQ e una copia) `salign` è coerente e resta.

**Orientamento relativo non vincolato** (`multitemplate.unanchored`, su ogni allineamento usato da MODELLER, anche
quello dell'utente, e sull'align2d con più template): i template della stessa voce (stesso id: catene dello stesso
file) condividono il sistema di riferimento; due voci sono *ancorate* se i loro template coprono almeno 10 residui del
bersaglio in comune (`MIN_ANCHOR`, come `MIN_SHARED` della selezione). Se le voci ancorate fra loro formano più di un
insieme, MODELLER costruisce ogni regione dal suo template e le unisce solo con la stereochimica del tratto che le
collega: la posizione reciproca delle regioni (fra domini, per esempio) non viene da nessun template ed è in pratica
arbitraria. Avviso nel risultato, nel log e nel report («Orientamento relativo non vincolato: UBQN_A (residui 1–40 del
bersaglio) e UBQR_A (residui 41–76 del bersaglio) vengono da strutture diverse…»; test con MODELLER su 1–40 di 1UBQ e
41–76 di una sua copia ruotata e traslata). Due frammenti dello stesso file caricati come template diversi (come nel
test dei frammenti disgiunti sopra) hanno l'avviso anche se in realtà condividono le coordinate: BioLoom non può
saperlo.

### Ligandi, cofattori e metalli

Logica in `domain/ligands.py` (F5.4), copia preparata e PIR in `engines/modeller.py`, verifica in `engines/quality.py`
e `services/compute.py`.

Il trasferimento dei gruppi eteroatomici dal template al modello è selettivo e viene verificato dopo la modellazione.

- **Prima della F5.4** l'opzione `hetatm` scriveva tutti gli eteroatomi nella copia preparata, ma lo script di
  allineamento non leggeva gli HETATM e l'intervallo del template finiva sull'ultimo residuo: MODELLER ignorava i
  gruppi e il modello non ne aveva (verificato con MODELLER 10.8 su `ubq_zn.pdb`). La casella «Ligandi» del singolo
  template non attivava nemmeno `env.io.hetatm`.
- **Classificazione** (`classify`, `candidates`), nel dettaglio del template (`GET /api/templates/{id}` → `ligands`,
  `ANALYSIS_VERSION` 3: le analisi in cache si ricalcolano) e nella scelta della Modellazione. Le acque non compaiono
  fra i gruppi: hanno il loro elenco (`waters`, vedi **Acque** sotto).

  | Classe | Criterio | Per default |
  |---|---|---|
  | metallo | un solo atomo di un elemento metallico (colonna element, altrimenti il nome: ZN, FE2, CU1…) | sì; no per Na⁺, K⁺ e altri monovalenti (spesso dal tampone) |
  | cofattore noto | codice CCD in `COFACTORS`: HEM, HEC, HEA, FAD, FMN, NAD, NAI, NAP, NDP, ATP, ADP, AMP, ANP, GTP, GDP, GNP, SAM, SAH, PLP, PMP, COA, ACO, TPP, TDP, BTN, LPA, FES, SF4, F3S, CLA, BCL, RET, B12, CNC, PQQ, H4B, MGD | sì |
  | additivo | `COMMON_ADDITIVES` di `domain/structure` più glicoli, tamponi, crioprotettori e piccoli anioni (MPD, PG4, PGE, EPE, MES, TRS, CIT, BME, ACY, IMD, SCN, AZI…) | no, con la motivazione: di solito senza ruolo biologico (come le esclusioni di BioLiP, Yang et al. 2013) |
  | residuo modificato | ha N, CA e C dello scheletro e un legame peptidico (C–N ≤ 1,9 Å) con il residuo precedente o seguente della catena (MSE, SEP, TPO…); mai un nome di `COFACTORS` | mai trasferibile: fa parte della catena |
  | altro ligando | tutto il resto (inibitori, zuccheri, amminoacidi liberi legati come substrato…) | sì |

  Il legame peptidico distingue i residui modificati da SAM, SAH e dagli amminoacidi liberi, che hanno gli stessi
  atomi N, CA e C ma non sono nella catena: prima erano «residui modificati», quindi non trasferibili, esclusi dalla
  scelta predefinita e dalla verifica e contati come proteina nei contatti. L'esito si conserva nel residuo (`xtra`):
  la verifica lo chiede per ogni atomo vicino.

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
  2. Per ogni catena con gruppi trasferiti si aggiungono le colonne dei gruppi alla fine del tratto di quella catena
     (prima del `/`): `.` nel template e nel bersaglio, `-` negli altri template. **Più template con lo stesso
     gruppo nello stesso sito** (due globine con l'eme, due omologhi con lo stesso Zn: capita con la modalità `multi`
     e con la scelta predefinita della Modellazione) condividono la colonna: `.` in ciascuno di quei template e un solo
     `.` nel bersaglio, quindi un solo gruppo nel modello, vincolato da tutti. Prima ogni template aveva la sua colonna
     e il modello aveva due Zn a 2,6 Å (o due porfirine sovrapposte), senza avvisi. Stesso gruppo = stesso nome e sito
     in comune: i residui dei donatori dei metalli e dei legami covalenti (altrimenti quelli entro 4 Å, `contacts` di
     `ligand_groups`), riportati attraverso le righe del PIR sulle colonne con un residuo del bersaglio (servono i
     numeri dei residui della copia preparata, `_chain_ids`). L'associazione rispetta in ogni riga l'ordine dei gruppi
     nel file (MODELLER legge i `.` di un template in quell'ordine) e unisce più gruppi possibile (programmazione
     dinamica, `_match_slots`); un gruppo senza corrispondente ha una colonna nuova prima di quella del gruppo unito
     che lo segue nello stesso template. Con due template che hanno gli stessi gruppi in ordine opposto se ne unisce
     solo uno. Una nota dice quali gruppi sono stati uniti.
  3. L'intervallo del template si aggiorna: fine sull'ultimo gruppo (se l'ultima catena ne ha), `+N` ricontato, una
     fine che indicava un eteroatomo tolto torna sull'ultimo residuo.

  Errore chiaro se i `/` del template e del bersaglio non sono nelle stesse colonne o se la voce del template non
  comprende tutti i residui di una catena con ligandi (i gruppi stanno dopo l'ultimo residuo). Lo script dei modelli
  imposta `env.io.hetatm = True` solo se almeno un gruppo è trasferito.
- **Acque** (acque catalitiche e strutturali): mai trasferite per default, scelte una per una nell'opzione `ligands`
  con il loro nome (`{template, chain, resnum, resname: "HOH"}`), quindi con la stessa validazione, la stessa cache
  delle copie preparate e lo stesso controllo del template.
  - **Candidate** (`water_candidates`, nell'analisi del template come `waters`, `ANALYSIS_VERSION` 4): solo le acque
    con un legame H (N/O entro 3,5 Å) con la proteina o con un gruppo, oppure nella sfera di coordinazione di un
    metallo (anche il ferro di un eme); le altre sono contate (`others`). Ruolo: `metal`, `bridge` (ponte con un
    ligando o cofattore, non con un additivo), `structural` (legami H con 3 o più residui); suggerite le prime due.
    Su 3KS3 (anidrasi carbonica II, 0,9 Å): 481 acque, 355 legate, 32 strutturali, 2 suggerite (HOH 402 e 707 sullo
    zinco). Su 1A6K (aquomet-mioglobina): 185 acque, 3 suggerite (HOH 1001 sul ferro, due a ponte con l'eme).
  - **Copia preparata:** le acque scelte dopo i gruppi eteroatomici della loro catena, rinominate HOH (MODELLER non
    riconosce WAT, DOD…); `_Selector` accetta l'insieme delle acque da tenere. `ligand_groups` le descrive (classe
    `water`, `hbonds`, `coordinates`), `waters` le conta.
  - **PIR:** dentro BioLoom un'acqua è una colonna «.» come un ligando (si unisce solo a un'acqua di un altro
    template: nome HOH e sito dai residui dei legami H). Una «w» minuscola non sopravvive a `sequences.parse_pir`
    (maiuscolo: diventerebbe W, triptofano), quindi solo il file letto da MODELLER, `alignment_modeller.ali`
    (`ligands.modeller_pir`), ha «w» nelle colonne delle acque, e lo script imposta `env.io.water = True` e
    `alnfile='alignment_modeller.ali'`. Verificato con MODELLER 10.8 su `ubq_zn.pdb`: con `.w` l'acqua esce
    HETATM HOH dopo lo Zn, a 2,1 Å come nel template. Il file entra nel pacchetto di riproducibilità.
  - **Verifica** (`evaluate(…, waters=True)`, solo nella modellazione: il modello ha solo le acque trasferite):
    scontri (sotto 2,2 Å, mai «legami covalenti»), legami H e metalli nel modello; nessuno dei due → da controllare;
    meno della metà dei residui legati nel template ancora legati → da controllare; metallo coordinato nel template e
    non nel modello → da controllare. Il numero di coordinazione di un metallo aggiunge solo le acque del template
    *non* trasferite (quelle trasferite sono già fra i donatori). L'analisi di qualità di una struttura qualunque non
    verifica le acque.
  - **Limite:** MODELLER tiene l'acqua dove sta nel template rispetto ai residui vicini (come i gruppi BLK), non
    ottimizza la rete di legami idrogeno.
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

  **Distanze attese** (Å), verificate a settembre 2026 (`REFERENCE_DISTANCES`; test
  `test_reference_distances_match_the_sources`):

  - Na, Mg, K, Ca, Mn, Fe, Co, Cu, Zn: tabella «Metal-donor atom target distances (revised July 2006)» di Harding
    (2006, Acta Cryst. D62:678–682), dal sito dell'autrice (`tanna.bch.ed.ac.uk/newtargs_06.html`, copia del
    29/04/2009 nell'Internet Archive; il PDF della rivista non era raggiungibile), basata sul CSD e sulle strutture
    proteiche a ≤ 1,25 Å. Tutti i valori coincidono con quelli di BioLoom. Le stelle (★) sono l'affidabilità indicata
    dalla tabella (`REFERENCE_RELIABILITY`): ★★★ deviazione standard ~0,05 Å, ★★ 0,10, ★ 0,15–0,20, nessuna stella la
    meno affidabile.
  - Ni (assente in Harding): Tabella 3 di Zheng et al. (2017, CheckMyMetal, distanze del CSD), letta nel testo
    completo di Europe PMC (PMC5349434): O dell'acqua 2,08(6); O legato a un carbonio 2,07(7) (moda più lunga; l'altra
    è 1,86), usato per Asp/Glu e per lo scheletro; N 2,09(7) (moda più lunga; l'altra 1,89); S 2,18(3).
  - Bazayeva et al. (2024, Acta Cryst. D80:362–376, MetalPDB a < 1,5 Å) non trovano differenze significative con
    Harding et al. (2010) entro la dispersione (carbonile dello scheletro dei metalli alcalini e alcalino-terrosi
    entro 0,05 Å; Zn–N(His) 2,05 ± 0,06 per Nδ1 e 2,03 ± 0,07 Å per Nε2). I loro valori per residuo sono nel
    materiale supplementare, non consultato: nessun valore di BioLoom viene da lì.
  - Donatori senza colonna propria, dalle note della tabella di Harding: Asn/Gln come Asp/Glu monodentato (prima di
    questa verifica BioLoom usava lo scheletro); O, N e S dei ligandi come l'O dell'Asn (quindi Asp/Glu), l'N dell'His
    e l'S della Cys. Scelte di BioLoom non verificate su una fonte: Ser/Thr/Tyr con il valore dell'acqua (per Harding
    Ser/Thr stanno fra acqua e carbossilato, Tyr circa 0,1 Å sotto il carbossilato) e Met con quello della Cys.

  | Metallo | O acqua | O Asp/Glu | O scheletro | N His | S Cys | Numero di coordinazione tipico |
  |---|---|---|---|---|---|---|
  | Na | 2,41 ★★ | 2,41 ★★ | 2,38 ★★ | — | — | 4–7 |
  | Mg | 2,07 ★★★ | 2,07 ★★ | 2,26 | — | — | 5–6 |
  | K | 2,81 ★ | 2,82 ★ | 2,74 ★ | — | — | 5–8 |
  | Ca | 2,39 ★★ | 2,36 ★★ | 2,36 ★★ | — | — | 6–8 |
  | Mn | 2,19 ★★★ | 2,15 ★★★ | 2,19 | 2,21 ★★ | 2,35 | 4–6 |
  | Fe | 2,09 ★★ | 2,04 ★★ | 2,04 | 2,16 ★ | 2,30 ★★★ | 4–6 |
  | Co | 2,09 ★★ | 2,05 ★★★ | 2,08 | 2,14 ★★ | 2,25 ★ | 4–6 |
  | Ni (CSD) | 2,08 | 2,07 | 2,07 | 2,09 | 2,18 | 4–6 |
  | Cu | 2,13 | 1,99 ★ | 2,04 | 2,02 ★★ | 2,15 | 3–6 |
  | Zn | 2,09 ★★★ | 1,99 ★★★ | 2,07 | 2,03 ★★★ | 2,31 ★★ | 4–6 |

  Gli intervalli del numero di coordinazione seguono quanto riportano le fonti (Zheng et al. 2017: Mg ottaedrico
  stretto, siti con meno o più di 6 donatori rari; Na di solito 6, a volte 5 o 4; K di solito ottaedrico. Bazayeva et
  al. 2024: Zn con 4 donatori nel 61% dei siti); gli estremi sono una scelta di BioLoom. Altri metalli (Cd, Hg…): donatori entro 3,0 Å, geometria, esito
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
  modello ha meno gruppi di quelli trasferiti. `transferred`, il REMARK e l'avviso contano i gruppi del modello (i `.`
  del bersaglio, `ligands.model_groups`): un gruppo di più template compare una volta, con `template` = i codici
  separati da virgole, il numero del template principale e i gruppi di ciascun template in `sources`. Il confronto
  con il sito del template usa il principale (il primo). Un errore inatteso della verifica va nel log e non fa fallire il job.
  Voto e consenso non cambiano.
- **Valori di riferimento** (test in `test_ligands.py`):
  - `ubq_zn.pdb`: Zn con NE2 di His68 (2,10 Å, attesa 2,03) e due acque (2,10 Å, attesa 2,09), angoli 67,2°, 81,1° e
    90,0°: piramide a base quadrata con 2 posizioni vacanti, gRMSD 14,1° → «da controllare» (numero di coordinazione 3
    su 4–6, geometria distorta, vacanze 40%);
  - lo Zn spostato a 0,9 Å dal CB di Leu43 → «problematico» (scontro);
  - modello dell'ubiquitina di lievito (MODELLER, 1 modello `very_fast`) con il solo Zn trasferito da `ubq_zn.pdb` più
    un SO₄ escluso: Zn A77 a 2,10 Å da NE2 di His68, sito del template conservato (1 su 1), 2 acque non trasferite →
    numero di coordinazione 1 (3) → «da controllare»; lo stesso PIR con i ligandi spenti perde il `.` con un avviso e il
    modello non ha HETATM;
  - due copie di `ubq_zn.pdb` come template, scelta predefinita: una colonna `.` condivisa, un solo HETATM Zn nel
    modello, `transferred` con `ZNA_A, ZNB_A`.
- **Limiti:** i gruppi di una catena senza residui proteici non sono trasferibili (si trasferiscono con la catena
  scelta); il carbonio non è considerato donatore (CO e CN⁻ legati al ferro dell'eme risultano scontri); i codici
  minuscoli di `restyp.lib` in un PIR dell'utente (`z`, `h`, `w`) vengono letti in maiuscolo come amminoacidi: per i
  ligandi si usa `.`; il raffinamento dei loop di un modello non legge gli HETATM (MODELLER predefinito), quindi i
  ligandi non passano ai modelli di loop.

### Complessi e omo-oligomeri

Modelli con più catene (F5.3): assemblaggio biologico del template, bersaglio a una sequenza per catena, restrizioni di
simmetria fra catene uguali e confronto delle interfacce del modello con quelle del template. Logica pura in `domain/assembly.py`
e `domain/complexes.py`, nessuna dipendenza nuova (Biopython e numpy).

**Assemblaggi biologici** (`domain/assembly.py`). Il file del PDB contiene l'unità asimmetrica; l'assemblaggio si genera
con gli operatori dichiarati nel file, qui, invece di scaricare da RCSB l'assemblaggio già generato
(`<ID>-assembly<N>.cif`): funziona anche senza rete, con i file già in libreria e con quelli caricati, e i nomi delle catene
seguono una regola nota (quelli di RCSB, "A-2", non sono identificativi PDB di un carattere).

- **Lettura:** mmCIF (`pdbx_struct_assembly`, `pdbx_struct_assembly_gen`, `pdbx_struct_oper_list`) oppure REMARK 350 del
  PDB (BIOMOLECULE, AUTHOR/SOFTWARE DETERMINED…, APPLY THE FOLLOWING TO CHAINS con le righe AND CHAINS, BIOMT1–3; i numeri
  BIOMT di gruppi diversi sono operatori diversi). La libreria scarica da RCSB in formato PDB, quindi il REMARK 350 è il caso
  più comune. Nel mmCIF le catene dei generatori sono `label_asym_id` (proteina, ligandi e acque hanno asym diversi),
  mentre Biopython raggruppa per `auth_asym_id`: ogni residuo del primo modello riceve il suo `label_asym_id`
  dall'`atom_site`, e un generatore prende esattamente i residui elencati.
- **Espressioni degli operatori** (dizionario PDBx): "1", "1,2,5", "1-4", "(1-4)" e il prodotto cartesiano "(1,2)(3,4)",
  applicato da destra a sinistra (x' = M1·(M3·x + t3) + t1); al massimo 1000 combinazioni per generatore.
- **Nomi delle catene:** copie nell'ordine dei generatori, degli operatori e delle catene del file; la prima copia di ogni
  catena dell'autore tiene il nome (se di un carattere), le altre prendono il primo carattere libero di A–Z, a–z, 0–9 non
  tenuto da una prima copia (catene A e B, due operatori per A: A, C = A·2, B). Ogni catena ha origine, operatore e
  `identity` (coordinate del file così come sono) nel risultato e nelle righe `REMARK 999 BIOLOOM ASSEMBLY CHAIN`.
- **Limiti:** 62 catene (identificativi di un carattere) e 99 999 atomi (numerazione del PDB); oltre, l'assemblaggio si
  elenca con il motivo ma non si importa. Prima posizione alternativa degli atomi, primo modello del file.
- **Libreria** (`TemplateLibrary.assemblies`, `import_assembly`): `GET /api/templates/{id}/assemblies` (stato oligomerico,
  catene generate con l'origine, `buildable`, `identity_only`) e `POST /api/templates/{id}/assemblies/{n}` → template
  `<ID>_ASM<n>` in PDB, con titolo, metodo e risoluzione del file d'origine e `assembly` nei metadati (template d'origine,
  assemblaggio, catene, impronta SHA-256 del file d'origine). L'id non si tronca: oltre i 40 caratteri l'id d'origine si
  accorcia e prende un'impronta (`<inizio>_<6 cifre di SHA-1 dell'id>_ASM<n>`, `assembly_template_id`), così gli
  assemblaggi 1, 2 e 10 di un id lungo restano template distinti. L'assemblaggio chiesto si verifica prima (404 se il file
  non lo dichiara). Un'importazione già fatta dello stesso assemblaggio dallo stesso file si riusa (`cached`); se il file
  d'origine cambia, si rigenera; un template con quel nome che non è lo stesso assemblaggio dello stesso template dà 409.
  Importazioni concorrenti dello stesso assemblaggio: un lock per template, una sola costruzione. Un assemblaggio non ha
  assemblaggi propri; i modelli AlphaFold non ne hanno.

**Bersaglio a più catene** (`complexes.parse_target`, opzione `complex` di `POST /api/model` e `/api/align2d`):
`{name, chains: [{template_chain, sequence | same_as, name}]}` su un solo template (o assemblaggio), le cui catene
diventano quelle della corrispondenza, nell'ordine (`TemplateSpec.keep_order`: la copia preparata ha le catene in
quell'ordine, così i '/' del PIR si corrispondono). Errori chiari (400) prima di accodare il job: una sola catena, catena
del template ripetuta o non valida, sequenza mancante, `same_as` verso una catena assente o senza sequenza propria, più
template, limiti.

| Limite | Valore | Motivo |
|---|---|---|
| Catene | da 2 a 12 | omo-oligomeri comuni fino ai dodecameri; oltre (capsidi, ferritine a 24 catene) una sequenza per catena non è pratica, e l'analisi delle interfacce cresce con le coppie di catene a contatto (fino a 66) |
| Residui in tutto | 3000 | tempo di MODELLER per modello misurato in crescita un po' più che lineare con i residui (tabella sotto): con 3000 residui 5 modelli "accurati" (3–5 volte più lenti del "rapido") restano nel timeout predefinito dei job (120 min), con 5000 (il limite di una catena singola) no |

**Allineamento per catena** (`modeller.build_complex_alignment_script`, `complexes.merge_chain_alignments`): `align2d` di
ogni catena del bersaglio sul segmento FIRST:X–LAST:X della sua catena del template; le righe si uniscono con '/', che
stanno quindi nelle stesse colonne, e l'intervallo del template va dal primo residuo della prima catena all'ultimo
dell'ultima. Il log riporta per catena residui allineati e identità. La stessa funzione è l'anteprima della pagina
(`/api/align2d` con `complex`: `chains` nel risultato).

**PIR a più catene** (`complexes.check_chains`, `sequences.validate_pir`): MODELLER 10.8 tollera i '/' in colonne diverse e
perfino una voce di template senza '/' su due catene (verificato: il modello si costruisce, con le catene fuse o un residuo
di una catena allineato all'altra). BioLoom quindi:

- nella modalità Complesso rifiuta (400 `invalid_pir`) '/' in colonne diverse fra template e bersaglio e una voce di
  template con un numero di tratti diverso dalle catene del suo intervallo; con un PIR dell'utente nella modalità
  Complesso le catene del bersaglio devono essere le sequenze del complesso;
- con un PIR qualunque dà gli stessi controlli come avvisi (`validate_pir`: "Separatori di catena ('/') … in colonne
  diverse da quelle del bersaglio").

Le colonne '.' dei ligandi (`ligands.pir_with_ligands`) restano compatibili: si aggiungono alla fine del tratto di ogni
catena in tutte le righe, i '/' non si spostano.

**Modello** (`ComputeService._complex_plan`, `modeller.build_model_script`): per ogni bersaglio con più catene (anche da un
PIR) le catene del modello prendono i nomi del template principale (il primo con una catena di fronte a ogni catena del
bersaglio e i '/' nelle stesse colonne; altrimenti A, B, C… con un avviso), con i residui numerati da 1 in ogni catena
(`rename_segments` in `special_patches`; MODELLER di suo numera di seguito, 77–152 per la seconda catena). Un bersaglio a
catena singola non cambia (catena A, come prima). `REMARK 999 BIOLOOM CHAINS 2 A=A B=B` nei modelli analizzati.

**Simmetria** (opzioni `symmetry`, `symmetry_weight` da 0 escluso a 10, predefinito 1): le catene del bersaglio con la
stessa sequenza formano un gruppo e ogni catena è vincolata alla prima con `restraints.symmetry.append(Symmetry(s1, s2,
peso))` sui C-alfa dei residui standard (`only_std_residues().only_atom_types('CA')`), come nell'esempio del manuale di
MODELLER ("Building a model with symmetry restraints", peso 1,0). La restrizione penalizza le differenze fra le distanze
interne corrispondenti delle due selezioni (Šali & Blundell 1993): rende uguali le catene, non impone la disposizione, che
viene dal template. Le selezioni devono avere gli stessi atomi: per questo solo catene identiche (MODELLER altrimenti
fallisce, "Number of selected atoms … is not the same", verificato). Richiesta senza catene uguali: avviso, nessuna
restrizione. `REMARK 999 BIOLOOM SYMMETRY CA WEIGHT 1 A,B`.

**Confronto delle interfacce** (`complexes.assess`, in `<modello>.quality.json` → `complex` e, per il modello migliore
anche senza analisi di qualità, nel risultato `complex.assessment`; `models[].complex` = esito e DockQ minimo):

| Misura | Definizione | Fonte |
|---|---|---|
| Interfacce del modello e del template | `domain/interfaces` (ΔSASA > 1 Å², area = metà dell'area sepolta), interfacce del template calcolate una volta per job | Krissinel & Henrick 2007 |
| Corrispondenza | residuo del template → residuo del modello dalle colonne del PIR usato da MODELLER (lettere del template sulla copia preparata dal residuo iniziale, lettere del bersaglio sui residui del modello); catena del template → catena del modello per maggioranza | |
| Residui conservati | residui d'interfaccia del template il cui residuo allineato è d'interfaccia nella coppia corrispondente del modello (non allineati contati a parte) | |
| Fnat | contatti fra residui del template (atomi pesanti entro 5 Å) presenti fra i residui corrispondenti del modello | CAPRI, Méndez et al. 2003 |
| iRMS | RMSD degli atomi N, CA, C, O dei residui entro 10 Å dall'altra catena nel template, dopo la loro sovrapposizione | Méndez et al. 2003; Basu & Wallner 2016 |
| LRMS | RMSD dello scheletro della catena più corta dopo la sovrapposizione della più lunga (ricettore) | idem |
| DockQ | (Fnat + 1/(1 + (iRMS/1,5)²) + 1/(1 + (LRMS/8,5)²)) / 3; ≥ 0,80 alta, ≥ 0,49 media, ≥ 0,23 accettabile, sotto non riprodotta | Basu & Wallner, PLoS ONE 11:e0161879, 2016 |

- **Coppie confrontate:** solo quelle del template con le due catene nell'intervallo della sua voce del PIR
  (`residue_map` → `span`): la copia preparata può avere altre catene scelte (es. A, B e C con un PIR da A a B), che non
  sono modellate; una nota le elenca. Una catena dell'intervallo allineata solo a gap non ha una catena del modello (`?`
  nel testo). Un errore inatteso dell'analisi diventa una riga del log e il modello resta senza confronto: non fa fallire
  la modellazione (l'annullamento del job invece passa); nei risultati il modello migliore senza confronto lo dice
  («Analisi delle interfacce non riuscita…: vedi il log del job»).
- **Esito motivato:** classe di DockQ con il testo ("Interfaccia del template riprodotta: DockQ 0,97 (alta; Fnat 1,00, iRMS
  0,35 Å, LRMS 1,66 Å); residui d'interfaccia del template conservati 75% (6 su 8); area 117 Å² contro 123 Å² del template
  (95%)"); "non sono più a contatto" se le catene corrispondenti del modello non si toccano; un'interfaccia del modello che
  il template non ha è "nuova" (contatto creato dalla modellazione, da verificare). Esito del modello = il peggiore.
- **Limiti** (nel risultato e nel report): il template non è la struttura vera del complesso del bersaglio; DockQ misura la
  fedeltà all'interfaccia del template, non l'accuratezza. Le coppie si confrontano anche con residui mutati. Un contatto
  cristallino piccolo resta piccolo: vale la plausibilità dell'area di `domain/interfaces`.
- **Costo:** due analisi delle interfacce per modello (modello e, una volta, template) più le interazioni; le ricerche dei
  contatti usano un albero sugli atomi di una catena e una ricerca per atomo dell'altra.

**Valori di riferimento** (test in `test_assembly.py`, `test_complexes.py`):

| Caso | Valore |
|---|---|
| mmCIF sintetico (1UBQ, asym A, più due acque asym B nella stessa catena dell'autore) con identità + rotazione C2 | catene A e B; B coincide con la catena B di `ubq_dimer.pdb` entro 0,01 Å; le acque seguono la loro catena; interfaccia 122,9 Å² come il dimero |
| stesso file, generatore con il solo asym A / espressione "(1,2)(3)" | acque escluse / copie "1×3" e "2×3" con la traslazione applicata per prima |
| 1UBQ (REMARK 350) | un assemblaggio monomerico, catena A, solo identità |
| dimero contro sé stesso (catene rinominate, numeri spostati di 100) | 5 contatti entro 5 Å, 8 residui d'interfaccia (S57, D58, N60, Q62 per catena), Fnat 1, iRMS 0, LRMS 0, DockQ 1 |
| catena B spostata di 3 Å | LRMS 3,00 Å, Fnat 0,80 (4 su 5), iRMS 1,37 Å, DockQ 0,745 → media |
| catena B allontanata di 30 Å | area 0, Fnat 0, LRMS 30 Å → non riprodotta, "non sono più a contatto" |
| MODELLER: omodimero dell'ubiquitina di lievito su `ubq_dimer.pdb`, 2 modelli "rapidi", simmetria | catene A e B numerate da 1, RMSD dei C-alfa fra le catene dopo la sovrapposizione 0,03 Å (senza simmetria 0,63 Å), DockQ 0,97 (alta), 75% dei residui d'interfaccia conservati, area 117 contro 123 Å²; job in 14 s con l'analisi del migliore |

**Tempi di MODELLER con più catene** (copie di `ubq_dimer.pdb`, raffinamento "rapido", container di test con altri test
in esecuzione sulla stessa macchina: valori indicativi):

| Catene | Residui | 1 modello | 2 modelli | Per modello |
|---|---|---|---|---|
| 2 | 152 | 7,5 s | 13,9 s | 6,4 s |
| 4 | 304 | 10,9 s | 18,8 s | 7,9 s |
| 8 | 608 | 22,3 s | 42,3 s | 20,0 s |

**Interfaccia** (`components/ComplexInput.tsx`, `ComplexResults.tsx`, `AssembliesCard.tsx`, logica in `lib/complex.ts`):

- **Libreria template:** scheda "Assemblaggi biologici" (stato, catene con l'origine, «Importa come template»); per un
  assemblaggio importato la sua origine e «Modella il complesso»; pulsante «Complesso» per i template a più catene; badge
  "assemblaggio" nell'elenco.
- **Modellazione → Complesso** (`?cmplx=<template>` dalla libreria): template (con download da RCSB e importazione degli
  assemblaggi), una riga per catena (dal progetto, incollata o «stessa di…»; le catene del template con la stessa sequenza
  partono già come «stessa della catena …»), conteggi e limiti come il backend (`complexRequest`), simmetria con il peso,
  anteprima dell'allineamento per catena in modalità Confidenza. Gli avvisi di copertura del bersaglio non si calcolano in
  questa modalità.
- **Risultati:** riquadro "Complesso" (catene del modello e del template, simmetria, tabella del confronto con DockQ ed
  esito; clic su una riga = testo dell'esito e interfaccia centrata nel 3D) e 3D colorato per catena con i residui
  d'interfaccia conservati (verde), solo nel modello (arancione) e persi (rosso). Per un modello diverso dal migliore il
  confronto viene dal suo rapporto di qualità, se analizzato. Badge "interfacce: …" nella tabella dei modelli.

### Validazione di qualità

Pipeline di validazione in `engines/quality.py`; PROCHECK e ProSA in `engines/procheck.py` ed `engines/prosa.py`,
QMEAN in `engines/qmean_local.py` (sezione seguente).

| Metrica | Implementazione |
|---|---|
| Ramachandran | φ/ψ calcolati per catena rispettando le interruzioni; regioni favorite/consentite per caso generale, glicina, prolina (pre-prolina come generale); cis-peptidi non prolinici |
| Clashscore | atomi pesanti, raggi di Probe, esclusione delle coppie entro 3 legami (grafo covalente) e delle acque, tolleranza per coppie polari |
| Struttura secondaria | `mkdssp` se installato, altrimenti DSSP integrato (energia elettrostatica dei legami H, turn, eliche α/3₁₀/π, ponti e ladder β, bend) |
| SASA relativa | `mkdssp` oppure Shrake-Rupley di Biopython normalizzato con le ASA massime di Tien et al. 2013 |
| DOPE | MODELLER: totale, z-score normalizzato, profilo per residuo (finestra 15) |
| Voto A–D | 100 − penalità outlier Ramachandran − penalità clashscore − penalità zDOPE (> −1) |
| PROCHECK | `procheck.scr` (csh + Fortran compilato nell'immagine): Ramachandran a 4 regioni, G-factor, parametri di legame dal riepilogo `model.sum`; grafici PostScript tra i file del job secondo l'impostazione `procheck_plots`: tutti (10 file, ~3,7 MB per 420 residui, i due grafici per residuo da soli 2,4 MB), solo il Ramachandran complessivo (`%%Title: Ramachandran plot`, ~0,1 MB) o nessuno; le metriche non cambiano. Nel risultato `plots` dice la scelta usata |
| ProSA II | z-score combinato, di coppia e di superficie; profilo di energia per residuo (finestra 1 e 10) |
| QMEAN | su richiesta. **Locale**: worker nel container `qmean` (HHblits su UniClust30/UniRef30 di qualunque versione; QMEANDisCo se c'è la QMTL), un calcolo alla volta, annullabile. **Remoto**: SWISS-MODEL, con email e consenso esplicito. Modalità `local` / `remote`, più **fallback facoltativo** dal locale a SWISS-MODEL (impostazione o scelta del singolo calcolo) |
| Ligandi e metalli | campo `ligands` solo se la struttura ha gruppi eteroatomici non d'acqua: contatti, scontri, coordinazione dei metalli (`domain/ligands.py`, vedi [Ligandi, cofattori e metalli](#ligandi-cofattori-e-metalli)); nessun effetto sul voto |

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
- **Processi di appoggio** (`core/offload.py`, `domain/qa_steps.py`): con molti job in parallelo i calcoli Python
  (Ramachandran, clash, SASA: ~1 s di CPU per una proteina di 420 residui) occupano il GIL del processo principale, e su
  ~30 s di calcolo per coppia di una pipeline M × N lo saturano oltre 15–20 coppie in parallelo (misura del
  benchmark `scripts/benchmark_mxn.py`: ~1,6 s di CPU del processo di BioLoom per coppia). Da 8 job di calcolo in
  parallelo nelle Impostazioni (`offload.MIN_WORKERS`) un pool di processi avviati con "spawn" (uno ogni 4 job,
  60–100 MB ciascuno) esegue `qa_steps.python_steps` sul file della struttura, mentre i sottoprocessi partono come
  prima; i passi di `Steps` aspettano quell'esito. Un passo non riuscito nel processo di appoggio (o un pool rotto, o
  più di 10 minuti di attesa) si rifà nel processo principale, con gli stessi errori e avvisi; sotto gli 8 job e senza
  job (`ctx` assente) tutto resta nel processo principale. Il pool segue le Impostazioni e si chiude all'arresto.
- **Più modelli** (qualità "Su tutti i modelli"): `run_modeling` ne analizza fino a 3 insieme (non oltre i job
  paralleli delle Impostazioni), ognuno con il proprio contesto; metriche, rapporti e REMARK restano nell'ordine dei
  modelli, e il primo modello fallito in quell'ordine fa fallire il job come prima.

**Valori di riferimento** sulle strutture di prova. Per 1UBQ i valori sono verificati dai test
(`test_structure_quality.py`, `test_parallel_quality.py`); per 3IU0 e 9QB7, i cui file non sono in `tests/data`, i
test non li ripetono:

| Struttura | Residui in regione favorita | Clashscore |
|---|---|---|
| 1UBQ | 95,9 % | 1,7 |
| 3IU0 | 94,3 % | 0,4 |
| 9QB7 | 94,0 % | 0,2 |

### QMEAN locale con qualunque versione di UniClust30

Worker HTTP nel container `qmean` (`docker/qmean/qmean_worker.py`, database in `docker/qmean/uniclust.py`, avvio di
QMEAN in `run_qmean_wrapper.py`); client in `engines/qmean_local.py`, job `qmean` in `services/compute.py`.

- **Ricerca:** l'avviatore accetta un database fino a 3 livelli sotto `UNICLUST_PATH` (`uniclust_below` in
  `bioloom.sh`, `Get-UniclustBelow` in `bioloom.ps1`): è il limite indicato all'utente nella Guida. Il worker
  esplora la cartella montata su `/uniclust_src` fino a 4 livelli, uno in più, quindi trova sempre ciò che l'avviatore
  ha accettato. La ricerca automatica sui dischi è un'altra cosa: scorre 4 livelli di cartelle per trovare la radice
  (3 sul disco di sistema). Il worker riconosce ogni
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
      | UniRef30_2020_06 | 12 GB | misurato: 3,8 GB di indici in memoria + 6 GB di cs219 + 1 GB + 10% (Guida, QMEAN e UniClust) |
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
    - Più di 8 non serve: sul server a 224 core (UniRef30_2023_02, 3PGK di 415 residui) un calcolo dura 141 s con 8
      processori, 129 con 16, 134 con 32.
  - **Calcoli contemporanei (`QMEAN_PARALLEL`):** il worker esegue fino a N calcoli insieme, uno per thread, con i
    processori divisi fra loro (al massimo 8 ciascuno); `slots_for` scende a quanti HHblits entrano nel 90% della
    memoria (il cs219 nella cache del sistema conta una volta). Copie locali e conversioni restano da sole (`Gate`: un
    calcolo nuovo non sorpassa una copia in attesa). Ogni calcolo ha la sua vista del database nella propria cartella di
    lavoro: la vista condivisa, ricreata da un calcolo che partiva, toglieva i collegamenti sotto un HHblits in corso
    (`File exists` alla prima prova con 16 calcoli). Lo stato dichiara i posti (`slots`) e BioLoom li usa come
    capacità della risorsa `QMEAN_LOCAL` del dispatcher (`set_resource_capacity`, letta senza I/O dall'ultimo stato:
    1 finché il worker non è stato interrogato, poi i posti dichiarati). I job `qmean` stanno nel pool "io", che ha
    `IO_WORKERS` (8) più i posti oltre il primo di questa risorsa (`JobManager.io_workers`): con 8 fissi il server
    teneva occupati 8 posti su 32. Il dispatcher crea i thread che mancano quando la capacità cresce senza `wake`
    (posti letti dal primo calcolo, con la coda già piena) e riordina la coda solo dopo un evento che può rendere
    avviabile un job: con 126.000 QMEAN in coda un ordinamento costa circa 0,08 s, e prima lo rifacevano tutti i
    thread inattivi a ogni fine di un job. Misurato sullo stesso server, 3PGK ripetuta:

    | Calcoli insieme | Totale | Per modello |
    |---|---|---|
    | 1 | 141 s | 141 s |
    | 16 | 122 s | 7,6 s |
    | 32 | 142 s | 4,4 s |
    | 64 | 167 s | 2,6 s |

    Con 16 calcoli la memoria usata è rimasta intorno a 20 GB: gli indici stanno nella cache del sistema, condivisa.
  - **Profili riusati (`QMEAN_PROFILE_CACHE_GB`, predefinito 10, 100 sul server):** HHblits dipende solo dalla sequenza. Il wrapper
    avvolge `run_qmean._seqanno`: prima della ricerca copia `<md5 della sequenza>.a3m` dalla cartella del database in
    `/cache/profiles` (il nome cambia se l'indice degli a3m cambia), dopo salva il profilo nuovo con una scrittura
    atomica; `run_qmean.py` usa da sé un a3m già presente. Risultato identico (provato: stesso QMEAN4 Z-score), ricerca
    saltata. **Una sola ricerca per sequenza:** il wrapper prende un blocco esclusivo (flock su
    `.locks/<prime 4 cifre dell'md5>.lock` nella cartella del database) prima di guardare la cache e lo tiene fino al
    salvataggio del profilo; con un profilo già pronto lo lascia subito. I modelli della stessa sequenza che partono
    insieme aspettano il primo e riusano il suo profilo. Il kernel libera il blocco anche se il processo viene ucciso
    (timeout, annullamento). La pulizia toglie i profili meno usati oltre il massimo (fino all'80%), al più una volta
    al minuto (`PRUNE_SECONDS`), e lo stato dei profili si rilegge al più ogni 30 s: contarli vuol dire leggere ogni
    file (0,09 s ogni 14.000 sul server) e BioLoom chiede lo stato prima di ogni calcolo. Sul server il massimo è 100 GB
    (`bioloom-podman.sh`, `server.env.example`): un profilo occupa circa 0,8 MB, e con 10 GB una pipeline M × N di
    23.000 sequenze cancellava i profili dei migliori prima che le altre coppie li riusassero (misurato il 2026-10-20:
    13.917 profili, 10,71 GB su 10,74). Il worker riporta `bioloom_profiles` (riusati, calcolati) e BioLoom lo scrive
    nel log.
  - **Risorse nell'uscita:** il worker riporta processori, giudizio sulla memoria (`ok` | `swap`) e secondi dell'intero
    calcolo (`bioloom_hhblits`). `engines/qmean_local.score` li porta nel risultato del job come `hhblits` e li scrive nel
    log («QMEAN locale concluso in … s nel worker»). Il campo è `null` con SWISS-MODEL e con un worker che non li riporta.
  - **Codifica:** l'immagine QMEAN (CentOS 7) non ha `C.UTF-8`. Senza `LANG=en_US.UTF-8` Python 3.6 usa ASCII
    e un messaggio con lettere accentate fermava il worker all'avvio.
- **Verifiche:**
  - sul disco reale `UniRef30_2020_06` (HH-suite 3, 194 GB, disco USB), per ricerca, vista, copia locale e
    avvio di HHblits;
  - un calcolo QMEAN completo non è stato possibile per la memoria di questo PC (5,7 GB per Docker su 11,8 di RAM);
  - su un disco di prova con tre versioni in formati diversi e un archivio non estratto.

### Consenso tra i metodi e qualità per residuo

Logica in `domain/consensus.py` e `domain/perresidue.py` (F6). Il voto A–D non cambia: `consensus` è un campo in più del rapporto di qualità.

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
  | Accordo con una predizione (F9.3, solo con il confronto) | lDDT-Cα sui residui a predizione sicura: 0 → 0, 0,5 → 40, 0,7 → 70, 1 → 100 | 0,25 | Mariani et al. 2013; fasce del pLDDT (lDDT-Cα predetto) di Tunyasuvunakool et al. 2021; vedi [Confronto con un predittore esterno](#confronto-con-un-predittore-esterno) |

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
  predetto secondo `plddt.predictor` (nome storico `is_alphafold_model`), l'unico criterio di BioLoom, lo stesso della
  libreria dei template e della copia preparata per MODELLER (vedi [Modelli AlphaFold DB](#modelli-alphafold-db)):
  segni certi di AlphaFold DB o di ESMFold nell'intestazione, oppure il nome `AF-<ACC>-F<n>` senza un'altra origine
  dichiarata, e valori in 0–100 non tutti nulli; `report.structure.predictor` dice quale (`alphafold`, `esmfold`).

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
- **Disordine predetto** (F5.5, vedi [Disordine, bassa complessità e domini](#disordine-bassa-complessità-e-domini)): il
  consenso ha un campo `disorder` (versione 2) e le regioni problematiche che vi cadono dentro hanno `expected`. Non cambia
  punteggio, segnali, conteggi né regioni.
- **Accordo con una predizione esterna** (F9.3, vedi [Confronto con un predittore esterno](#confronto-con-un-predittore-esterno)):
  solo con `predict_job` nelle rotte del consenso (o nel report). Il metodo `predictor` entra nel punteggio con peso 0,25
  (fra i mancanti, con il motivo, se il confronto non è conclusivo); il campo `predictor` ha lDDT e pLDDT per residuo, i
  residui che divergono dove la predizione è sicura (`flags`) e le regioni divergenti con gli indici del rapporto; le
  regioni problematiche in cui il modello diverge anche da una predizione sicura hanno `predictor_overlap` e lo dicono nel
  testo. È informativo per residuo: non conta fra i metodi delle regioni. Le discordanze con gli altri metodi hanno frasi
  proprie (ambito `external`: «la stereochimica è regolare ma il modello diverge dalla predizione dove questa è sicura…»).
  Senza confronto il risultato è identico a prima (test): stessi metodi (6), stessi mancanti, nessun campo in più,
  versione 2 invariata.

### Confronto strutturale

Logica in `domain/comparison.py` (F4.1), esecuzione in `engines/align.structural` (job `align` in modalità
strutturale).

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

**Interfaccia** (`pages/Compare.tsx`, `components/StructuralComparison.tsx`, `lib/compare.ts`):

- **Un solo componente** per la pagina Confronto e la modalità Strutturale di Allineamenti: usa il job `align`
  strutturale esistente (`api.structuralAlign`), nessun tipo di job nuovo.
- **Colorazione:** `comparisonColoring` colora i residui di B della catena B del PDB sovrapposto per distanza (scala
  continua blu → giallo → rosso tra 0 e 5 Å, saturata oltre: 5 Å è la soglia delle coppie vicine di TM-align) o per
  lDDT (stessa scala rovesciata su 0–1); i residui di A hanno lo stesso valore nel passaggio del mouse ma il colore fisso
  del riferimento (grigio chiaro, ardesia scura nel tema chiaro, `ResidueValue.color`). Nessun colore chiaro nella
  scala, per non confonderlo con A. Per i residui senza coppia vale il colore della catena
  (`ResidueColoring.chainColors`: A come il riferimento, B grigio "senza valore"), con i campioni in legenda
  (`legendExtra`).
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

### Analisi strutturale

Interazioni, interfacce fra catene e conservazione mappata sulla struttura (F7). Logica pura in `domain/interactions.py`,
`domain/interfaces.py` e `domain/conservation.py` (numpy, `NeighborSearch`, `ShrakeRupley` e `PairwiseAligner` di
Biopython: nessuno strumento nuovo); job `analysis` in `services/analysis.py`, rotte in `api/analysis.py`.

**Perché un job a parte** e non un'opzione del job `quality`: il rapporto di qualità (voto, consenso, i
`<modello>.quality.json` già salvati) non cambia; l'allineamento multiplo è un ingresso solo di quest'analisi, che si
rilancia con un altro allineamento senza rifare DOPE, PROCHECK e ProSA. È un calcolo Python breve: priorità 0
(`core/jobs.PRIORITY`, come `quality`), pool "cpu"; l'unico processo esterno possibile è l'allineamento multiplo di
sequenze non allineate (`engines/align.multiple`, sotto il budget di CPU e con la cache dei calcoli). Il risultato non
passa dalla cache dei calcoli: costa pochi secondi e resta nel job. Nessuna colonna né tabella nuova in SQLite.

**Interazioni** (tutte le catene del primo modello, soli amminoacidi: acque, ligandi e ioni esclusi):

| Interazione | Criterio | Fonte |
|---|---|---|
| Legame idrogeno | D…A ≤ 3,5 Å e D…A–AA ≥ 90°; con l'idrogeno H…A ≤ 2,5 Å, D–H…A ≥ 120° e H…A–AA ≥ 90°; per i gruppi che ruotano, senza idrogeno, AA(D)–D…A ≥ 90° | Baker & Hubbard 1984; angoli degli antecedenti come HBPLUS (McDonald & Thornton 1994) |
| Ponte salino | O carico di Asp/Glu (OD1, OD2, OE1, OE2) entro 4,0 Å da un N di Lys/Arg/His (NZ; NE, NH1, NH2; ND1, NE2); una voce per coppia di residui con la coppia di atomi più vicina e il numero di coppie | Barlow & Thornton 1983 |
| π-stacking | centroidi ≤ 5,5 Å, spostamento ≤ 2,0 Å; parallelo con angolo fra i piani ≤ 30°, a T con angolo ≥ 60° (fra 30° e 60° non si riporta) | McGaughey et al. 1998; Salentin et al. 2015 (PLIP) |
| Catione-π | NZ di Lys o CZ di Arg ≤ 6,0 Å dal centroide, spostamento ≤ 2,0 Å | Gallivan & Dougherty 1999; Salentin et al. 2015 |
| Ponte disolfuro | SG–SG 2,0 ± 0,2 Å (legame S–S tipico circa 2,04 Å), con Cα–Cα e χ3 (CB–SG–SG–CB, di solito vicino a ±90°) | Richardson 1981 |

- **Idrogeni:** quelli del file se ci sono (un donatore senza idrogeni non dona, un N di istidina protonato non
  accetta). Altrimenti si posizionano a 1,0 Å dove la geometria è fissa, come HBPLUS: N–H dello scheletro come DSSP
  (Kabsch & Sander 1983), NH degli anelli di His e Trp e NE di Arg sulla bisettrice esterna, NH₂ di Asn, Gln e Arg nel
  piano a 120° dal legame C–N. OH di Ser/Thr/Tyr e NH₃⁺ di Lys e dell'N-terminale ruotano: criterio sugli atomi pesanti.
  Senza idrogeni entrambi gli N dell'istidina valgono da donatori e accettori.
- **Esclusioni:** coppie nello stesso residuo; coppie scheletro-scheletro fra residui consecutivi legati; lo zolfo
  come donatore o accettore; le estremità N e C nei ponti salini; His come catione nel catione-π. Una coppia di atomi
  entrambi donatori e accettori (Ser–Thr, His) compare una volta, nel verso con l'angolo migliore.
- **Tipi di legame idrogeno:** `backbone` (scheletro-scheletro), `mixed`, `sidechain`; `inter_chain` per le coppie fra
  catene diverse.

**Interfacce** (strutture con più catene proteiche):

- SASA per atomo con Shrake & Rupley 1973 (sonda 1,4 Å, 100 punti, raggi di Biopython; senza idrogeni) nel complesso,
  in ogni catena isolata e in ogni coppia di catene che si toccano (atomi entro 2 × (1,8 + 1,4) = 6,4 Å, le sole la
  cui SASA può cambiare). Aggiungere atomi copre solo punti: ΔSASA non è mai negativa.
- Residui d'interfaccia: ΔSASA > 1 Å² fra catena isolata e coppia (PISA, Krissinel & Henrick 2007; Janin et al. 2008).
  Con più di due catene la coppia si misura sulle sole due catene: i residui sepolti da una terza non entrano.
- Area d'interfaccia = Σ ΔSASA delle due catene / 2 (metà dell'area sepolta totale B, convenzione di PISA).
- Composizione: residui apolari (G A V L I M P F W), polari (S T C N Q Y H), carichi (D E K R); frazione apolare
  dell'area sepolta (atomi C e S). Interazioni attraverso l'interfaccia: quelle della tabella sopra con le due estremità
  nelle due catene.
- **Plausibilità biologica** (indicatore semplice, sulla sola area): < 400 Å² bassa, nell'intervallo dei contatti di
  impaccamento cristallino (B < 800 Å², Janin et al. 2008); 400–600 Å² incerta; ≥ 600 Å² compatibile, come le
  interfacce proteina-proteina standard (B 1200–2000 Å², Lo Conte, Chothia & Janin 1999). **Limiti** (nel risultato e
  nell'interfaccia): le interfacce biologiche piccole e i contatti cristallini grandi si sovrappongono; non valuta
  conservazione né complementarità chimica (PISA, EPPIC); in un modello per omologia l'interfaccia è quella del
  template; un'unità asimmetrica o una struttura sintetica può non essere l'assemblaggio biologico.
- `residues` elenca ΔSASA, SASA nel complesso e isolata per tutti i residui (colorazione del 3D).

**Conservazione** (`domain/conservation.py`):

- Pesi delle sequenze di Henikoff & Henikoff 1994 (il gap conta come 21° tipo, come in Capra & Singh), frequenze pesate
  dei 20 amminoacidi fra le sequenze con un residuo nella colonna (lettere non standard = gap), H = −Σ p ln p
  normalizzata con ln(min(20, N)), N = sequenze. Conservazione = (1 − H/H_max) × (1 − g), g = frazione pesata di gap:
  1 per una colonna invariante senza gap. Colonne con più del 30% di gap (frazione semplice): nessun punteggio, come il
  `gap_cutoff` di Capra & Singh 2007. Con meno di 10 sequenze un avviso (scelta BioLoom).
- **Mappatura:** la sequenza della catena (residui della struttura, X per i non standard) si allinea (Needleman-Wunsch,
  BLOSUM62, gap 10/0,5, gap terminali gratuiti, come `align.pairwise`) alla riga senza gap: residuo → residuo della
  riga → colonna. Residui mancanti, code in più e inserzioni non spostano la numerazione. Le lettere che BLOSUM62 non
  ha (U selenocisteina, O pirrolisina, J) si allineano come X nella riga e nella catena (prima un ValueError faceva
  fallire tutto il job, interazioni comprese); l'identità confronta le lettere originali. Riga indicata (`row`) oppure,
  per ogni catena, la più simile: candidate per 3-mer in comune (le righe di soli gap escluse), le prime 5 allineate;
  serve identità ≥ 90% e copertura ≥ 50% (l'allineamento deve contenere la sequenza della catena). Una riga indicata
  vale anche sotto, con un avviso; indicata di soli gap è un errore chiaro. Le catene senza riga corrispondente si
  elencano in `unmatched`; nessuna catena → job fallito (`conservation_no_match`).
- **Riga indicata:** i nomi delle righe del testo incollato sono sanificati (`sanitize_code`: `sp|P0CG47|UBB_HUMAN` →
  `sp_P0CG47_UBB_HUMAN`) e resi univoci; `row` vale con il nome dell'allineamento, con quello del file (intestazione
  intera o primo token; con nomi ripetuti la prima occorrenza) o con la sua forma sanificata, anche per le sequenze
  non allineate e per i job di allineamento. Una riga che non c'è è un errore prima di accodare il job (400
  `conservation_invalid`, con alcune righe disponibili), non un job fallito dopo interazioni e interfacce.
- **Allineamento:** `msa.job_id` di un job `align` completato (righe `aligned`), oppure `msa.text` in un formato qualunque
  di `domain/fileformats` (righe già allineate usate così; sequenze di lunghezza diversa allineate con
  `engines/align.multiple`, al massimo 200; testo fino a 10 MB, nei parametri del job). Il center-star integrato
  allinea U, O e J come X e restituisce le lettere originali.

**Rotte:**

| Rotta | Funzione |
|---|---|
| `POST /api/analysis` | `{source, chains?, msa?: {job_id \| text, row?, tool?}}` → job `analysis`; errori subito: struttura assente 404, sorgente senza file o job 400 `invalid_source`, job di allineamento inesistente 404, non `align`, testo non valido o riga indicata assente 400 `conservation_invalid`, allineamento non completato 409 |
| `GET /api/analysis/latest?type=&id=&job_id=&file=` | `{job_id, finished_at, active_job_id}`: `job_id` è l'analisi completata più recente sulla stessa struttura (`source_key` di `services/compute` e stessa impronta SHA-256 del file, se il file c'è ancora), altrimenti `null` (un template sostituito con lo stesso id non ha più analisi); `active_job_id` l'analisi della stessa sorgente in coda o in corso lanciata dopo, altrimenti `null`. Parametri mancanti (es. `type=upload` senza `file`): nessuna analisi, non un errore interno |

Il risultato (`GET /api/jobs/{id}`): `source`, `label`, `source_sha256`, `structure`, `interactions` (criteri, riepilogo,
elenchi), `interfaces`, `conservation` (o null), `warnings`. Il log riporta la durata dei passi ("Analisi strutturale in
0.12 s (lettura 0.01, interazioni 0.01, interfacce 0.00 s)") e i conteggi.

**Valori di riferimento** (test in `test_interactions.py`, `test_interfaces.py`, `test_conservation.py`):

| Struttura | Valore |
|---|---|
| 1UBQ | 63 legami idrogeno (45 scheletro, 14 misti, 4 catene laterali), idrogeni posizionati; elica 23–34: gli 8 legami i→i+4 di un'elica ideale di 12 residui (n − 4); foglietto: 21 legami fra filamenti diversi (8 nella forcina β1–β2, 5 fra β1 e β5 paralleli, 5 fra β3 e β5, 3 fra β3 e β4); i 20 legami del foglietto del DSSP integrato (energia < −0,5 kcal/mol) tutti trovati, più quello dell'N-terminale che il DSSP non valuta, e il 93% dei legami geometrici dello scheletro è anche del DSSP |
| 1UBQ | ponti salini K11–E34 (3,35 Å), K27–D52 (2,90 Å, NZ–OD2) e R54–D58 (3,88 Å), le sole coppie N⁺–O⁻ entro 4,0 Å del file; nessun disolfuro né interazione aromatica |
| 1UBQ con gli H dello scheletro nel file | stessi legami dello scheletro (meno l'N-terminale), nessun donatore delle catene laterali, stessi ponti salini |
| `ubq_dimer.pdb` | interfaccia A–B simmetrica: Ser57, Asp58, Asn60, Gln62 in entrambe le catene; area 123 Å² (B 246 Å²), plausibilità bassa; nessuna interazione fra le catene; 26 contatti < 4,5 Å |
| sintetiche | disolfuro a 2,05 Å con χ3 = 90° trovato, a 2,30 e 1,75 Å no; ponte salino a 3,9 Å sì, a 4,1 no; π parallelo a 3,8 Å, a T a 5,0 Å, spostato di 3 Å no; catione-π a 4,0 Å sì, spostato di 3 Å o a 6,2 Å no; NH₂ posizionati a 1,0 Å e 120° nel piano |
| conservazione su 1UBQ | 12 righe con la colonna 20 variabile (10 amminoacidi) e 3 gap nelle colonne 75–76: colonne invarianti 1, colonna 20 < 0,1, P19 (S nel lievito) intermedia, 75–76 con il 25% di gap 0,75; pesi di Henikoff e punteggi calcolati a mano |

**Prestazioni** (container di test, Intel i3-1115G4): 1UBQ, interazioni 0,01 s; `ubq_dimer.pdb` (1204 atomi), interfacce
0,46 s (tre calcoli della SASA); quattro copie del dimero, 4816 atomi e 8 catene, analisi completa con la conservazione
2,6 s (il test fissa un limite largo di 60 s). Il costo è quello della SASA: complesso + catene isolate + coppie.

**Report** (`domain/report.py`, `services/reports.py`): la sezione "Analisi strutturale" (conteggi e tabelle complete
delle interazioni; ponti disolfuro con SG–SG, Cα–Cα e χ3; interfacce con area, residui, legami H, ponti salini, area
apolare, composizione, plausibilità, limiti e ΔSASA per residuo; conservazione con il profilo per residuo) compare nei
report del modello, dell'elemento di pipeline (anche nello ZIP) e dell'analisi di qualità quando esiste un'analisi
strutturale della stessa struttura. Tasche ed elettrostatica (F7b) hanno le loro sezioni. Il report non ricalcola nulla.

**Interfaccia** (`components/StructuralAnalysis.tsx`, logica in `lib/interactions.ts`):

- Scheda **Interazioni** della pagina Qualità: mostra l'analisi più recente della struttura del rapporto (`latest`) e la
  lancia o rilancia, con la conservazione facoltativa (job di allineamento del progetto, oppure testo o file letto da
  `/api/files/sequences`, con la riga della struttura facoltativa). La pagina conserva il job seguito e le scelte
  dell'allineamento quando si cambia scheda (`AnalysisPanelMemory`: il pannello si smonta); un'analisi in coda o in
  corso sulla stessa struttura si riprende anche da `active_job_id` (pagina ricaricata), quindi «Analizza
  interazioni» resta disabilitato invece di lanciarne un doppione.
- Tabelle di legami idrogeno, ponti salini, disolfuri e interazioni aromatiche con righe uniformi (`interactionRows`),
  filtri per catena (tutte, solo fra catene diverse, una catena) e per residuo (`parseResidueQuery`: `27`, `K27` o `A27`
  — una lettera vale come catena o come amminoacido —, `LYS27`, `A:27`, `52A`, testo libero). Un clic su una riga
  evidenzia e centra i due residui (`highlightFor`); un clic nel 3D filtra le tabelle per quel residuo.
- 3D colorato con l'API di colorazione di `Viewer3D`: superficie sepolta (fasce di ΔSASA con la soglia di 1 Å² esclusa,
  come il backend) o conservazione (tavolozza di ConSurf, Ashkenazy et al. 2016, su scala assoluta 0–1, con la striscia
  per residuo); "Evidenzia l'interfaccia" per ogni coppia di catene.
- Link stabile di un'analisi: `/quality?ajob=<id>` (anche «Apri» nella pagina Job).

### Tasche, elettrostatica e rifinitura energetica

Tre calcoli (F7b, F9.1) con strumenti nuovi dell'immagine: tasche con **fpocket**, potenziale elettrostatico con **PDB2PQR + APBS**,
rifinitura energetica con **OpenMM + PDBFixer**. Logica pura in `domain/pockets.py`, `domain/electrostatics.py`,
`domain/refinement.py`; motori in `engines/pockets.py`, `engines/electrostatics.py`, `engines/refine.py` (con
`engines/refine_worker.py`); job in `services/structure_tools.py` (`pockets`, `electrostatics`) e `services/refine.py`
(`refine`); rotte in `api/structure_tools.py`. Stesso schema del job `analysis`: sorgente della struttura come la
qualità, impronta SHA-256 del file nel risultato, calcolo più recente sulla stessa struttura da `/latest`. Nessuna
colonna né tabella nuova in SQLite, nessuna impostazione nuova; niente cache dei calcoli (fpocket è istantaneo, la mappa
di APBS è un file del job, il modello rifinito è il risultato del job).

**Strumenti e degradazione.** `engines/tools.py` rileva gli eseguibili `fpocket`, `pdb2pqr`, `apbs` (`TOOLS`) e le
librerie Python `openmm` e `pdbfixer` (`MODULES`, con `importlib.util.find_spec`, senza importarle nel server). Tutti
compaiono in `/api/system/info` (Impostazioni → Strumenti, Panoramica) e nella verifica dell'installazione (1UBQ:
tasche, carica netta e potenziale, minimizzazione). Senza uno strumento il job fallisce subito con `ToolUnavailable` e
il messaggio di `tools.missing_message` ("Ricerca delle tasche non disponibile: manca fpocket in questa installazione…");
le schede dell'interfaccia lo dicono prima di proporre il calcolo e disattivano il pulsante. L'immagine bioloom:4.1.0,
senza questi strumenti, continua a funzionare.

**Tasche (fpocket 4.2.3, `pockets`, priorità 0).**

- Copia pulita della struttura (`pockets.clean_copy`, `structure.pdb`: primo modello, prima altloc, sola proteina,
  catene e numerazione originali); i gruppi eteroatomici esclusi sono elencati in un avviso e i siti che occupano
  risultano tasche vuote. I residui modificati della catena scritti come HETATM (MSE, SEP, TPO…: N, CA e C con un legame
  peptidico a un vicino, `ligands.is_modified_residue`) restano nella copia e non sono «gruppi esclusi»: toglierli
  lasciava nella proteina un buco che fpocket riportava come tasca (le selenometionine delle strutture SeMet). Le MSE
  diventano MET (ATOM, SE → SD, come nella preparazione dei template), così l'elettrostatica, che usa la stessa copia, le
  calcola; un amminoacido libero legato come substrato (senza legame peptidico) resta escluso come ligando.
- fpocket con i parametri predefiniti (sfere alfa da 3,4 a 6,2 Å, almeno 15 per tasca; Le Guilloux et al., BMC
  Bioinformatics 10:168, 2009) da `ctx.run` (uno slot). Si conserva `structure_out/` (PDB con le sfere STP, script
  PyMOL/VMD, file per tasca).
- `domain/pockets.read_output`: descrittori di `<nome>_info.txt` (punteggio, druggability, volume, sfere alfa, SASA,
  idrofobicità, polarità, carica…; quelli sconosciuti in `other`), sfere da `<nome>_pockets.pqr`, residui che rivestono
  ogni tasca da `pockets/pocket<N>_atm.pdb`, centro = baricentro dei centri delle sfere alfa. Ordine per druggability
  (Schmidtke & Barril, J. Med. Chem. 53:5858, 2010), soglia di "probabilmente druggabile" 0,5.
- **1UBQ** (uscita reale in `tests/data/fpocket_1ubq_out`): 4 tasche; la tasca 2 (Met1, Glu18, Pro19, Ser57, Asn60,
  Ile61, Gln62, Lys63; 19 sfere, circa 324 Å³) è l'unica sopra 0,5 (0,535), poi la 4 (0,498). Il volume è una stima
  Monte Carlo e varia di qualche Å³ fra esecuzioni; tasche e residui no. 0,4 s.

**Elettrostatica (PDB2PQR 3.7.1 + PROPKA 3.5.1 + APBS 3.4.1, `electrostatics`, priorità 0).**

| Passo | Scelta | Fonte |
|---|---|---|
| Cariche e raggi | PDB2PQR, campo di forza PARSE (predefinito, parametrizzato per Poisson-Boltzmann) o AMBER; `--drop-water --keep-chain`. Una catena senza identificativo (colonna 22 vuota) prende nella copia una lettera libera, perché PDB2PQR la scriverebbe come A e i residui del PQR non si ritroverebbero; il risultato riporta il nome originale. Gli altri residui modificati della catena (SEP, TPO…) restano nella copia ma PDB2PQR non ha i loro parametri e li toglie dal PQR: un avviso proprio lo dice | Dolinsky et al. 2007; Jurrus et al. 2018; Sitkoff, Sharp & Honig 1994 |
| Protonazione | PROPKA a pH 7 (modificabile, 0–14) | Olsson et al. 2011 |
| Equazione | Poisson-Boltzmann linearizzata, `mg-auto`, `bcfl sdh`, `srfm smol`, `chgm spl2`, `sdens 10`, `swin 0,3` | Baker et al. 2001 |
| Mezzo | dielettrico 2 (proteina) e 78,54 (acqua a 25 °C), 150 mM di ioni ±1 di raggio 2,0 Å, sonda 1,4 Å, 298,15 K | input generato da PDB2PQR |
| Griglia | come `psize.py`: grossolana 1,7 × estensione della molecola (sfere atomiche del PQR), fine estensione + 20 Å, centrate; punti 32k + 1 con passo fine 0,5 Å; oltre il tetto di memoria il passo cresce del 10% alla volta (un solo processo, invece del `mg-para` di psize) | psize.py di PDB2PQR |
| Memoria | stima 500 byte per punto, tetto 1000 MB (circa 2,1 milioni di punti, lo stesso massimo di psize con 200 byte e 400 MB) | misurato: 586 MB di picco per 1,21 milioni di punti su 1UBQ |
| Potenziale per residuo | interpolazione trilineare della mappa sui punti della superficie accessibile (sfera r + 1,4 Å, 100 punti per atomo non coperti da altri atomi); media per atomo, poi sugli atomi esposti del residuo (idrogeni compresi); residui sepolti senza valore | Shrake & Rupley 1973; spostamento di 1,4 Å come UCSF Chimera |
| Scala | rosso (negativo) – bianco – blu (positivo), saturata a ±5 kT/e; 1 kT/e = 25,69 mV a 298,15 K | convenzione di PyMOL e Chimera per APBS |

- File del job: `structure.pdb`, `structure.pqr`, `apbs.in` (per ripetere il calcolo), `potential.dx` (mappa in kT/e,
  scaricabile: 16 MB per 1UBQ, al massimo circa 30 MB con il tetto della griglia), `pdb2pqr.log`, `apbs.log`. Due
  processi da `ctx.run`, uno slot ciascuno (il binario ufficiale di APBS non usa OpenMP).
- Risultato: carica netta e residui carichi per tipo (`charge_summary`: Lys+, Glu−…, estremità N e C), griglia,
  parametri, campionamento, scala, estremi della mappa, potenziale per residuo nell'ordine della struttura originale,
  riepilogo (residui esposti, minimo, massimo, media, residui oltre ±1 kT/e).
- **1UBQ:** carica netta 0 a pH 7 (7 Lys + 4 Arg + N-terminale contro 5 Asp + 6 Glu + C-terminale; His68 neutra con HD1;
  PROPKA dà pI 7,07): il test accetta |q| ≤ 1 per una His68 che a pH 7 potrebbe risultare protonata. Griglia 97 × 97 ×
  129 (passo 0,55/0,57/0,46 Å). Potenziale di superficie da −2,09 a +3,84 kT/e su 66 residui esposti; tutte le 7 Lys
  positive (da +0,34 a +2,42), 5 Glu su 6 negative (Glu16, fra Lys29 e Lys33, +0,26). Lo stesso con AMBER (carica 0).
  Tempi: PDB2PQR 1,0 s, APBS 3,5 s, superficie 0,8 s.

**Rifinitura energetica (OpenMM 8.6.1 + PDBFixer 1.12.0, `refine`, priorità 1).** Protocollo dichiarato in
`domain/refinement.py` e nel risultato (`protocol`):

1. Copia pulita (`input.pdb`): primo modello, prima altloc, residui amminoacidici (anche modificati, che PDBFixer
   sostituisce), senza acque, ligandi, ioni (esclusi con avviso: il campo di forza non ha parametri per loro) e senza
   idrogeni. Alle interruzioni della catena (C–N > 2,0 Å) un `TER`: senza, OpenMM legherebbe i due residui lontani e la
   minimizzazione li avvicinerebbe. Il `TER` c'è anche per una catena senza identificativo (colonna 22 vuota, comune nei
   file di dinamica molecolare): le catene si confrontano senza spazi. Nessun residuo mancante viene ricostruito; le
   estremità dell'interruzione diventano cariche (avviso).
2. PDBFixer: atomi pesanti mancanti e idrogeni a pH 7.
3. AMBER ff14SB (`amber14-all.xml`) con solvente implicito **OBC2** nella forza nativa `GBSAOBCForce` (raggi mbondi2 e
   fattori di scala di `implicit/obc2.xml`, dielettrici 1/78,5, termine ACE, nessun sale). **Perché non GBn2** (più
   accurato con ff14SB): in OpenMM esiste solo come `CustomGBForce`, lenta sulla piattaforma CPU. Misure su 1UBQ (1231
   atomi con gli idrogeni, i3-1115G4, 4 thread): GBn2 201 ms per passo e 370 s di minimizzazione; `implicit/obc2.xml`
   (anch'essa `CustomGBForce`) 183 ms; OBC2 nativo 21 ms e 15 s, con la stessa energia di `obc2.xml` entro 0,003
   kJ/mol. Con le restrizioni sulla catena principale il modello di solvente cambia poco la geometria finale; il costo
   decide se la rifinitura è usabile. Oltre 4000 atomi le interazioni non legate e GB si troncano a 2,0 nm
   (`CutoffNonPeriodic`, dielettrico di campo di reazione 1 come gli script implicit di OpenMM).
4. Restrizioni armoniche sugli atomi N, CA, C, O verso le posizioni di partenza: E = k·|r − r₀|², k = 10 kcal·mol⁻¹·Å⁻²
   (convenzione di `restraint_wt` di AMBER, senza ½) = 4184 kJ·mol⁻¹·nm⁻².
5. Minimizzazione L-BFGS fino a 10 kJ·mol⁻¹·nm⁻¹ (al massimo 5000 iterazioni); facoltativa una dinamica di Langevin a
   300 K (attrito 1 ps⁻¹, passo 2 fs, legami con H vincolati, seme fisso) di 0,5–50 ps (10 proposti nell'interfaccia)
   con le stesse restrizioni, seguita da una seconda minimizzazione.
6. Uscita: `<nome>_refined.pdb` (idrogeni facoltativi, catene e numerazione originali, righe `REMARK 999 BIOLOOM` con
   protocollo, energie, RMSD e voto), `refine_result.json` del processo.

- **Processo separato:** il server non importa OpenMM. `refine_worker.py` gira con lo stesso interprete del server
  (`sys.executable`) da `ctx.run`, con richiesta di CPU elastica (da 1 a core − 1 slot, altrettanti thread della
  piattaforma CPU, `OMP_NUM_THREADS=1`); avanzamento con i marcatori `@@BIOLOOM progress <frazione> <fase>`
  ("Minimizzazione…", "Dinamica molecolare: 3,5 di 10 ps"). Annullamento e timeout terminano il processo anche a metà
  di una minimizzazione o di una dinamica.
- **Energie:** potenziale del campo di forza senza il termine delle restrizioni (riportato a parte), con le componenti
  (legami, angoli, torsioni, non legate, solvatazione), prima della minimizzazione (struttura completata da PDBFixer)
  e alla fine. Servono al confronto sulla stessa struttura, non fra strutture. L'energia iniziale dipende dalle
  posizioni degli idrogeni scelte da PDBFixer (`Modeller.addHydrogens` di OpenMM le mette in posizioni casuali, con il
  modulo `random`, e le ottimizza con una breve minimizzazione; PDBFixer minimizza gli atomi pesanti aggiunti con un
  integratore a seme casuale): senza seme su 1UBQ l'energia iniziale variava fra −191 e +987 kJ/mol e la riesecuzione dello
  stesso job dava catene laterali diverse (RMSD 0,109 Å, Gln62 spostata di 1,7 Å). Dalla F9.4 il processo usa il seme
  della dinamica (`random.seed(MD_SEED)` e `addMissingAtoms(seed=MD_SEED)`): con un thread della piattaforma CPU due
  esecuzioni sono identiche byte per byte; con più thread restano differenze piccole (misurate su 1UBQ: energia iniziale
  −395,3 e −395,6 kJ/mol, RMSD fra i due modelli rifiniti 0,016 Å): con più thread la piattaforma CPU di OpenMM non
  ripete le somme in virgola mobile nello stesso ordine, e la minimizzazione amplifica le differenze dell'ultima cifra
  (con `OPENMM_CPU_THREADS=1` e un thread le esecuzioni coincidono; vedi
  [Riproducibilità](#riproducibilità-pacchetti-e-riesecuzione)).
- **Confronto prima/dopo:** `quality.analyze` (DOPE con MODELLER, senza PROCHECK e ProSA) in parallelo sulla copia
  pulita (prima: stessi residui, senza ligandi né idrogeni) e sul rifinito (dopo); righe di `refinement.compare` (voto,
  Ramachandran favorite e outlier, clashscore, DOPE, zDOPE; "invariato" entro l'arrotondamento dei rapporti); RMSD della
  catena principale dopo Kabsch (`comparison.kabsch_rmsd`) e senza sovrapposizione, con lo spostamento massimo. Il
  rapporto del rifinito è `<nome>_refined.pdb.quality.json`: la pagina Qualità lo apre come quello di un modello.
- **1UBQ e 1UBQ con χ1 di Ile30 ruotato di 120°** (test): energia 710 → −12 448 kJ/mol, RMSD 0,08 Å, clashscore 1,66
  → 0, favorite 95,9 % invariate, zDOPE −2,73 → −2,76; con il rotamero sbagliato energia 146 870 → −12 399 kJ/mol,
  clashscore 3,32 → 0, RMSD 0,09 Å; con 10 ps di dinamica stessa geometria (RMSD 0,09 Å), zDOPE −2,66. Tempi (4 core,
  altri job in corso): minimizzazione 15–70 s, 10 ps di dinamica 130 s.

**Rotte** (`api/structure_tools.py`):

| Rotta | Funzione |
|---|---|
| `POST /api/pockets` | `{source}` → job `pockets` ("Tasche · …"); struttura assente 404, sorgente incompleta 400 `invalid_source`, prima della coda |
| `POST /api/electrostatics` | `{source, forcefield: PARSE \| AMBER, ph: 0–14}` → job `electrostatics`; campo di forza o pH non validi 422 |
| `POST /api/refine` | `{source, md_ps: 0–50, hydrogens}` → job `refine` ("Rifinitura · …, dinamica 10 ps"); durata non valida 422 |
| `GET /api/pockets/latest`, `GET /api/electrostatics/latest` | come `/api/analysis/latest`: `{job_id, finished_at, active_job_id}` per la stessa sorgente e la stessa impronta del file |

La mappa si scarica da `/api/jobs/{id}/files/potential.dx?download=true`.

**Interfaccia.**

- **Qualità**, schede **Tasche** ed **Elettrostatica** (`components/StructureTools.tsx`, logica in `lib/pockets.ts`,
  `lib/electrostatics.ts`, `lib/toolJobs.ts`): calcolo più recente della struttura del rapporto (o in corso), lancio e
  rilancio, job seguito conservato dalla pagina cambiando scheda. Tasche: tabella ordinata per druggability (barra,
  badge "druggabile", punteggio, volume, sfere, idrofobicità, residui), 3D con i residui delle prime 8 tasche colorati
  con la tavolozza di Okabe-Ito e il resto grigio chiaro, centri delle sfere alfa come sferette e centro della tasca in
  bianco, ardesia scura nel tema chiaro (`CENTER_COLOR`, `Viewer3D.spheres`); clic su una riga → solo quella tasca, residui evidenziati e vista centrata; clic nel 3D
  → la tasca del residuo. Elettrostatica: campo di forza e pH, carica netta con i residui carichi per tipo, 3D e
  striscia per residuo con la scala ±5 kT/e e la legenda, residui più positivi e più negativi (clic → evidenza; solo
  quelli con almeno 10 punti di superficie: Ile13 di 1UBQ, con un solo punto esposto a +3,8 kT/e, resta nel 3D ma non
  nell'elenco),
  download di `.dx`, PQR e input di APBS, parametri e fonti. Link `/quality?pjob=<id>` e `/quality?ejob=<id>` («Apri»
  nella pagina Job).
- **Modellazione** (`components/Refinement.tsx`, `lib/refine.ts`): «Rifinisci» in ogni riga della tabella dei modelli
  e «Rifinitura energetica (OpenMM)» per una struttura qualunque (riquadro dei loop). La finestra spiega il protocollo e
  propone dinamica (0,5–50 ps) e idrogeni; il job si apre in `/modeling?job=<id>` con voto, energia, RMSD, clashscore, la
  tabella prima/dopo con l'esito di ogni metrica, il 3D del rifinito, «Qualità del rifinito», «Confronta con
  l'originale» (pagina Confronto con l'originale come riferimento A), il PDB e «Report» (anche dalla pagina Qualità del
  rifinito).
- **Report del modello rifinito** (`services/reports._refine_data`, sezione `rifinitura` di `domain/report.py`): al posto
  di template, allineamento, parametri e tabella dei modelli di MODELLER, una sezione bilanciata con il confronto
  prima/dopo (voto, Ramachandran, clashscore, DOPE, zDOPE con l'esito), l'energia del campo di forza con le componenti
  (iniziale, dopo la minimizzazione, dopo la dinamica, finale) e l'energia delle restrizioni, l'RMSD della catena
  principale con lo spostamento massimo, il protocollo (campo di forza, solvente, protonazione, restrizioni,
  minimizzazione, dinamica, interazioni non legate, piattaforma e thread di OpenMM, atomi aggiunti da PDBFixer, copia
  data a OpenMM con esclusioni, residui modificati e interruzioni) e le fonti (OpenMM 7, ff14SB, OBC, L-BFGS, Kabsch);
  il resto (qualità, ligandi, analisi strutturale, avvisi, legenda) come negli altri report, sul modello rifinito.

**Immagine Docker** (`docker/Dockerfile`):

| Strumento | Come | Perché | Spazio |
|---|---|---|---|
| fpocket 4.2.3 (MIT) | compilato dalla release ufficiale nello stadio `tools-build` (senza `-pg` e senza `data/`) | non è nei pacchetti di Ubuntu 24.04 | 1,9 MB |
| APBS 3.4.1 (BSD) | binario ufficiale della release GitHub, verificato con SHA-256 | il pacchetto `apbs` di Ubuntu (stessa versione) porta circa 340 MB di dipendenze (OpenMPI con ROCm/LLVM, MathJax); il binario usa solo libc e libstdc++ | 29,9 MB |
| PDB2PQR 3.7.1 (BSD), PROPKA 3.5.1 (LGPL 2.1), OpenMM 8.6.1 (MIT/LGPL), PDBFixer 1.12.0 (MIT) | pip nella venv dell'applicazione da `backend/requirements-tools.lock` (con le dipendenze non già fissate), `pip check` | PDB2PQR e OpenMM 8.6 di PyPI sono le versioni correnti; i pacchetti di Ubuntu (OpenMM 8.0, PDBFixer 1.9) sono legati al numpy di sistema | 107 MB |

Totale: circa 139 MB in più nei livelli dell'immagine (bioloom:4.1.0 869 MB, immagine di sviluppo 1,04 GB; contenuto
compresso 215 → 244 MB). I test di questi strumenti si saltano, con il motivo, nelle immagini che non li hanno.

### Mutagenesi in silico e scansione di alanina

Mutanti costruiti con MODELLER (F9.2), stima qualitativa dell'effetto sulla stabilità, scansione di alanina e varianti di
UniProt. Logica pura in `domain/mutations.py`; motore in `engines/mutate.py` (script di MODELLER, energia con
`engines/refine.py`); job `mutate` e `alascan` in `services/mutations.py`; rotte in `api/mutations.py`. Nessuno strumento
nuovo e nessuna dipendenza: MODELLER c'è già, OpenMM e PDBFixer (F9.1) sono facoltativi. FoldX e Rosetta non si usano
(licenze non ridistribuibili). Nessuna colonna né tabella nuova in SQLite, nessuna impostazione nuova; niente cache dei
calcoli (i modelli sono file del job).

**Notazione.** `[catena:]<originale><numero>[inserzione]<nuovo>`, codici a una o tre lettere (`A:K48R`, `A:LYS48ARG`,
`A:G52AA` = Gly 52A → Ala), più mutazioni separate da virgole (una sola variante, al più 10); la catena si omette con
una sola catena, `_` è la catena senza identificativo. La parte dopo il numero si legge dalla lunghezza (1 = residuo, 2 =
inserzione + residuo, 3 = tre lettere, 4 = inserzione + tre lettere), quindi non è mai ambigua; le lettere degli
amminoacidi non distinguono maiuscole e minuscole, la catena sì. Errori chiari (`invalid_mutation`, 400 prima della coda):
voce non valida, amminoacido non standard, sostituzione identica, catena inesistente o omessa con più catene, residuo
assente o non amminoacido, **residuo originale diverso** ("A48 è LYS (K), non GLU (E): usa la numerazione della
struttura"), residuo ripetuto. La rotta salva nei parametri la forma canonica con la catena sempre scritta
(`Mutation.code`: `A:K48R`, `_:K48R` per la catena senza identificativo), che si rilegge all'esecuzione anche su una
struttura con una catena senza nome e altre catene; titolo, risultato e nomi dei file usano la forma dei testi
(`label`: `K48R`). Anche la notazione delle varianti di UniProt è quella canonica. Il frontend ripete la stessa sintassi
(`lib/mutations.parseMutations`) per un riscontro immediato.

**Copia per MODELLER** (`modeller_input`): solo la proteina del primo modello (residui modificati come il residuo
standard: MSE → MET con SE → SD, SEP → SER…), prima altloc, niente idrogeni, acque e gruppi eteroatomici (esclusi con
avviso; quelli entro 8 Å dai siti con un avviso proprio). Ogni tratto continuo (C–N ≤ 2,0 Å) è una catena per MODELLER:
senza, `generate_topology` legherebbe i residui ai lati di un'interruzione. Il primo tratto di una catena con un nome di
un carattere lo conserva, gli altri tratti e la catena senza nome prendono una lettera libera; `restore_chains` riporta i
nomi originali nei modelli e toglie i TER fra i tratti della stessa catena. Una catena con un nome di più caratteri
(`auth_asym_id` di un mmCIF, es. «AA») resta nei modelli con la lettera di MODELLER del suo tratto (il formato PDB ne
ammette una), con l'avviso «Catene … rinominate»: indicatori, residui ottimizzati (RMSD fuori da essi) ed energia
cercano i siti nei modelli con `model_site` (una lettera per tratto: la mappa è per residuo), mentre la riga
d'interfaccia, il sito, l'etichetta e i partner delle interazioni restano con il nome originale (`AA:K48R`); il
risultato porta `model_chain` per il 3D dei modelli.

**Protocollo** (`MUTATE_SCRIPT`, da `mutate_model.py` della Sali lab; Feyfant, Sali & Fiser, Protein Sci. 16:2030,
2007):

1. `Selection.mutate`, topologia dalla sequenza mutata, coordinate trasferite dal selvatico e costruite per coordinate
   interne dove mancano, numerazione originale (`res_num_from`);
2. restrizioni statistiche: stereochimica, φ/ψ binormali, diedri ω e χ1–χ4;
3. residuo mutato randomizzato di 4 Å e ottimizzato da solo (coppie non legate interne alla selezione), poi con le
   catene laterali dei residui entro 5 Å (estensione di BioLoom: lo script originale ottimizza solo il residuo mutato):
   gradienti coniugati sul programma dei loop, ricottura (150 → 1000 → 300 K) e gradienti coniugati finali. Il resto
   della struttura non si muove: la catena principale fuori dai residui mutati è identica (test: RMSD 0,000 Å, atomi
   oltre 10 Å identici);
4. **stesso trattamento per il selvatico**: i residui da mutare si "mutano" in sé stessi con la stessa randomizzazione
   e gli stessi semi. Senza, la differenza misurerebbe anche l'ottimizzazione: su 1UBQ la sola riottimizzazione di Ile30
   cambia il DOPE di 40–90 unità rispetto al cristallo;
5. **3 modelli per variante** (semi consecutivi da −49837, il seme di `mutate_model.py`; modificabile), si tiene quello
   con il DOPE più basso (come la scelta del modello di AutoModel, Shen & Sali 2006). Misurato su 1UBQ: con un modello lo
   scarto fra due semi arrivava a 55 unità di DOPE; con il migliore di 3, due serie di semi danno ΔDOPE di K48R −39 e
   −34, di I30G +349 e +368;
6. DOPE di ogni modello (`complete_pdb(..., transfer_res_num=True)`: senza, la numerazione della seconda catena
   ripartirebbe e la selezione locale fallirebbe): totale, **locale** (`assess_dope` sui residui entro 8 Å dai siti:
   conta le coppie con almeno un atomo selezionato) e, con più catene, **fra le catene** (per un gruppo g:
   DOPE(g) + DOPE(resto) − DOPE(tutto), cioè le sole coppie fra g e le altre catene). Risultati in `mutate_models.json`,
   progressi con `@@BIOLOOM model k/n`. Un processo per variante (selvatico e mutante insieme, 6 modelli), da
   `JobContext.run` con uno slot.

**Energia con OpenMM** (facoltativa, `energy`): selvatico e mutante scelti passano dalla minimizzazione con restrizioni
della rifinitura (`engines/refine.run`: AMBER ff14SB, OBC2, catena principale a 10 kcal·mol⁻¹·Å⁻²), poi i **tripeptidi di
riferimento** i−1..i+1 estratti dalle due strutture minimizzate (`fragments_pdb`: finestre adiacenti unite, frammenti
distanti 1000 Å nello stesso calcolo, meno di 0,1 kJ/mol di interazione) con lo stesso protocollo. ΔΔE = (E_mut − E_wt) −
(E_rif,mut − E_rif,wt): il riferimento toglie la parte che dipende solo dalla composizione (K48R: −659 kJ/mol fra le
proteine, −664 fra i tripeptidi, ΔΔE +6). È un'energia senza entropia, non un'energia libera, e il rumore è grande: due
selvatici ottimizzati di K48 differiscono fino a 36 kJ/mol nei termini del ΔΔE. Quattro processi di OpenMM, due alla
volta; senza OpenMM l'energia manca con un avviso e la stima resta. I minimizzati sono fra i file
(`openmm_mutante/<nome>_min.pdb`): la «rifinitura del mutante».

**Stima dell'effetto** (`site_indicators`, `site_signals`, `classify`): indicatori dichiarati, ognuno con un peso
qualitativo; *probabilmente destabilizzante* con un segnale forte o almeno due moderati, *incerta* con uno moderato (o
residuo sepolto senza segnali, o soli segnali di possibile stabilizzazione), *probabilmente neutra* senza segnali su un
residuo esposto. Ogni risultato porta le motivazioni in italiano e l'avvertenza (`CAVEAT`): non è un ΔΔG.

| Indicatore | Segnale | Fonte o motivo |
|---|---|---|
| ΔDOPE in residui equivalenti = ΔDOPE / (\|DOPE del selvatico\| / residui) | ≥ 2 forte, ≥ 1 moderato, ≤ −1 possibile guadagno | DOPE (Shen & Sali 2006); scala per residuo per proteine di dimensioni diverse; soglie oltre il rumore misurato (scelta BioLoom). Un residuo più grande aggiunge contatti: un ΔDOPE negativo non dimostra una stabilizzazione |
| ΔΔE di OpenMM | ≥ 100 kJ/mol forte, ≥ 50 moderato, ≤ −50 possibile guadagno | soglie oltre lo scarto di 36 kJ/mol fra due selvatici (scelta BioLoom) |
| prolina in α-elica (DSSP H) oltre il primo giro | forte | propensione per l'elica, Pace & Scholtz 1998 (Pro +3,16 kcal/mol rispetto ad Ala); nel primo giro solo informativa (MacArthur & Thornton 1991) |
| altra sostituzione in α-elica con ΔP ≥ 0,5 kcal/mol (non agli estremi) | moderato | Pace & Scholtz 1998 |
| prolina o glicina in un filamento β | forte se si perde il legame H dell'NH, altrimenti moderato | Minor & Kim 1994; Smith, Withka & Regan 1994 |
| conformazione φ/ψ del selvatico fuori dalle regioni del nuovo residuo | forte | regioni di Ramachandran di BioLoom (Lovell et al. 2003): es. una glicina con φ > 0 |
| ponte disolfuro perso | forte | Betz 1993 |
| legami idrogeno persi del residuo (criteri di `domain/interactions`) | moderato se sepolto o almeno 2 | circa 1 kcal/mol ciascuno (Myers & Pace 1996) |
| ponte salino perso | moderato se sepolto, altrimenti informativo | Strop & Mayo 2000 |
| superficie apolare sepolta persa ≥ 40 Å² (riferimento: il residuo nel tripeptide isolato, Shrake & Rupley) | moderato | effetto idrofobico circa 25 cal·mol⁻¹·Å⁻² (Chothia 1974) |
| residuo nel nucleo (RSA < 20%, ASA massime di Tien et al. 2013) con BLOSUM62 < 0 | moderato | Henikoff & Henikoff 1992 |
| carica introdotta nel nucleo | moderato | Isom et al. 2011 |
| nuovi contatti sterici del residuo mutato (sovrapposizione > 0,4 Å) | moderato | Word et al. 1999 |
| residuo d'interfaccia (ΔSASA > 1 Å², `domain/interfaces`) | effetto sul legame, a parte: hot spot *probabile* se sepolto nel complesso (RSA < 20%, anche 0: un residuo coperto del tutto, il centro dell'«O-ring», è il più sepolto; RSA ignota non conta come sepolta) e con contributo al legame (ΔDOPE fra le catene ≥ 0,5 residui equivalenti o interazioni fra catene perse), *possibile* con il solo contributo | Bogan & Thorn 1998; Kortemme & Baker 2002 |

**Scansione di alanina** (`alascan`): residui da un elenco (`A:30, A:43, A:45-48`, anche con la verifica `A:I30`),
dall'interfaccia (tutte le catene o una) o da una tasca di un job `pockets` completato sulla stessa struttura e sullo
stesso file (impronta `source_sha256` del job delle tasche uguale a quella del file attuale; un template sostituito
con lo stesso nome dà 400 `invalid_scan` «le tasche sono state calcolate su una versione precedente del file»,
anche all'esecuzione di un job accodato prima della sostituzione; l'impronta va nella `selection` del risultato). Glicina e
prolina escluse (la sostituzione cambia la flessibilità della catena principale e non misura la catena laterale, come in
Kortemme & Baker 2002), alanina esclusa, residui non standard esclusi, tutti con il motivo. **Al più 50 residui** (6
modelli di MODELLER ciascuno: circa 7 minuti stimati per una proteina di 300 residui con 3 processi insieme), **20 con
l'energia** (4 minimizzazioni per residuo). Ogni residuo è un passo indipendente (`core/parallel.Steps`, fino ai job
paralleli delle Impostazioni) con il suo processo e il suo slot; una copia sola per MODELLER nella cartella del job, i
modelli in `siti/<residuo>/`. Righe ordinate dalla più destabilizzante (classe, poi punteggio = 100 per segnale forte +
10 per moderato + residui equivalenti di ΔDOPE, poi ΔDOPE); `alascan.csv`; un residuo fallito diventa un avviso, tutti
falliti fanno fallire il job.

**Varianti di UniProt** (`remote.uniprot_variants`, `map_variants`): le feature "Natural variant" e "Mutagenesis" sono già
nella risposta `{acc}.json` usata da `uniprot_entry`, che però ne tiene solo tipo e posizione: la stessa richiesta ha un
proprio namespace di cache (7 giorni), così le voci di `uniprot_entry` in cache non cambiano formato. Si tengono le
sostituzioni di un residuo; categoria *malattia* se la descrizione ("in CMH1; …") cita la sigla di una malattia dei
commenti DISEASE della voce, *mutagenesi*, *naturale*. La sequenza di UniProt si allinea a ogni catena (Needleman-Wunsch,
BLOSUM62, gap terminali gratuiti, come la conservazione); la numerazione non si assume mai uguale (es. UniProt con
l'etichetta N-terminale: K53 → A:K48R, nota "Numerazione di UniProt = numerazione della struttura + 5"). Con le
ripetizioni (poliubiquitina: tre copie in UBB, P0CG47) l'allineamento globale sceglierebbe una copia qualunque e la
mutagenesi annotata sulla prima non si mapperebbe: nelle parti della voce fuori dal primo tratto si cercano le copie con
identità entro 5 punti e quasi la stessa copertura, e le varianti di ogni copia arrivano sulla struttura (`segment`).
Una variante è applicabile se l'amminoacido originale coincide con quello della struttura; altrimenti il motivo. Sotto il
30% di identità le varianti non si mappano (errore), sotto il 90% una nota (omologo).

**Rotte** (`api/mutations.py`):

| Rotta | Funzione |
|---|---|
| `POST /api/mutate` | `{source, mutations, energy, seed}` → job `mutate` ("Mutagenesi · 1UBQ A:K48R"); struttura assente 404, notazione o residui non validi 400 `invalid_mutation`, seme fuori da −50000…−2 422 |
| `POST /api/alascan` | `{source, selection: {mode: residues \| interface \| pocket, residues, chain, pocket_job, pocket}, energy, seed}` → job `alascan`; scelta non valida o troppi residui 400 `invalid_scan`, tasche non completate 409 |
| `GET /api/mutations/variants?accession=&type=&id=&job_id=&file=&chain=` | varianti mappate sulla struttura (`chain`, `identity`, `coverage`, `offset`, `segments`, `variants` con `applicable`, `notation`, `reason`, `segment`) |
| `GET /api/mutations/history?type=&id=&job_id=&file=` | job `mutate` e `alascan` sulla stessa sorgente, dal più recente |

Risultato di `mutate`: `mutations`, `notation`, `files` (selvatico riottimizzato `<nome>_wt.pdb` e mutante
`<nome>_K48R.pdb`, con `.quality.json`; con l'energia i minimizzati), `models` (DOPE di tutti i modelli, `chosen`),
`dope`, `energy`, `sites` (indicatori), `assessment` (classe, motivazioni, segnali, punteggio, effetto sul legame),
`quality` e `comparison` (`refinement.compare` fra selvatico riottimizzato e mutante), `rmsd` (catena principale fuori dai
residui ottimizzati e totale), `protocol`, `warnings`, `caveat`. Righe `REMARK 999 BIOLOOM` nel mutante (mutazioni,
protocollo, ΔDOPE, classe "QUALITATIVE, NOT A DDG").

**Controlli preventivi** (`kind` `mutate` e `alascan`): stessa validazione delle rotte, MODELLER, OpenMM se chiesta
l'energia, gruppi eteroatomici e interruzioni vicini ai siti, residui esclusi, tempo e spazio. Tempi misurati sul portatile
dei test: un modello di mutante 0,85 + 0,0046 s per residuo, avvio 1 s, indicatori 0,01 s per residuo, analisi di qualità
dei due modelli 1,5 × quella di un modello; energia 8 + 0,8 s per residuo (misurata solo su 1UBQ); nella scansione +25%
per ogni residuo in più nello stesso turno (3 insieme su 4 CPU logiche: 14,7 s per turno contro 9 s di un residuo da
solo). Job `mutate` su 1UBQ: 10–15 s, 80 s con l'energia.

**Valori di riferimento** (test in `test_mutations.py`, container di sviluppo):

| Caso | Valore |
|---|---|
| K48R su 1UBQ | Arg 48 nel mutante, Lys nel selvatico riottimizzato, catena principale fuori dai residui ottimizzati identica (0,000 Å), atomi oltre 10 Å identici; ΔDOPE −39 (−0,37 residui equivalenti), DOPE locale su 15 residui; RSA 47%: probabilmente neutra. Con OpenMM ΔΔE +6 kJ/mol |
| I30P su 1UBQ | Ile30 sepolto (RSA 4%) nell'elica 23–34, ottava posizione: prolina oltre il primo giro (+2,75 kcal/mol), ΔDOPE +305 (+2,93), legame H dell'NH con Val26 perso, 46 Å² apolari persi: probabilmente destabilizzante (2 segnali forti, 4 moderati) |
| I30G, L43G | ΔDOPE +349 e +404 (+3,35 e +3,85); I30G con OpenMM ΔΔE +73 kJ/mol: probabilmente destabilizzanti |
| scansione A:30, A:43, A:48 (G10 esclusa) | L43A (+308, 2,93) e I30A (+253, 2,43) probabilmente destabilizzanti, K48A (+10) probabilmente neutra, in quest'ordine |
| D58A sul dimero sintetico | residuo d'interfaccia, DOPE fra le catene circa −150, nessun hot spot (interfaccia piccola, 123 Å²) |
| mutanti grezzi senza MODELLER | Ile30 → Ala: due segnali moderati (superficie apolare, nucleo non conservativo); Ile30 → Pro: prolina oltre il primo giro e NH di Val26; Lys48 → Ala: nessun segnale |

**Interfaccia** (`components/Mutagenesis.tsx`, logica in `lib/mutations.ts`):

- **Qualità**, scheda **Mutagenesi**: modulo (mutazioni con l'anteprima della notazione, oppure scansione per residui,
  interfaccia o tasca dell'ultimo calcolo di fpocket), varianti di UniProt (accessione → tabella con filtri Applicabili,
  Malattia, Mutagenesi, Tutte, ricerca, «Aggiungi» porta la notazione nel campo senza doppioni), energia facoltativa
  (disattivata senza OpenMM), `PreflightPanel` e `LaunchButton`; cronologia dei calcoli sulla struttura (stato in
  italiano, riletta a ogni cambio di stato del job seguito; un job eliminato altrove mostra «Job non trovato» e sparisce
  dal menu); il job seguito si conserva cambiando scheda. Per la tasca il modulo segue anche un calcolo di fpocket in
  corso («Calcolo delle tasche in corso…», poi la tendina compare da sola), e un errore di lettura è un avviso con
  «Riprova», non «nessun calcolo».
- **Modellazione**: «Mutagenesi» in ogni riga della tabella dei modelli e «Mutagenesi e scansione di alanina» per una
  struttura qualunque (stesso modulo in una finestra); il job si apre in `/modeling?job=<id>` (anche «Apri» nella pagina
  Job).
- **Risultati del mutante**: classe con le motivazioni pesate e l'avvertenza, ΔDOPE, ΔΔE, RMSD, tabella dei siti,
  confronto delle metriche, «Qualità del mutante», «Qualità del selvatico», «Confronta con il selvatico» (pagina Confronto
  con il selvatico riottimizzato come riferimento A) e «Con l'originale», PDB, rifinito (OpenMM), report; 3D del mutante
  con i siti a bastoncini.
- **Risultati della scansione**: conteggi, filtri (destabilizzanti, incerte, neutre, hot spot), tabella ordinata, dettaglio
  della riga scelta, 3D della struttura colorato per classe con la tavolozza di Okabe & Ito (residui esclusi in grigio,
  non scanditi chiari; clic → la riga), CSV e report.

**Limiti dichiarati** (nel risultato, nel report e nell'interfaccia): stima qualitativa, non un ΔΔG né una previsione
calibrata; i predittori dedicati sbagliano il segno di una parte delle mutazioni; un modello per omologia aggiunge
l'incertezza della struttura; il protocollo non considera ligandi, metalli, acqua né movimenti della catena principale
oltre il residuo mutato.

### Confronto con un predittore esterno

Controllo di plausibilità (F9.3) di un modello per omologia con una predizione indipendente della stessa sequenza: ESMFold
(servizio ESM Atlas) oppure, con un'accessione UniProt, il modello di AlphaFold DB già supportato (F5.1). Logica pura in
`domain/prediction.py`; client in `engines/remote.py`; job `predict` in `services/predict.py`; rotte in `api/predict.py`.
Nessuna dipendenza nuova, nessuna colonna né tabella SQLite nuova; due impostazioni nuove (`esmfold_remote`,
`esmfold_url`).

**Client ESMFold** (`remote.esmfold_predict`):

| Aspetto | Scelta | Verifica |
|---|---|---|
| Richiesta | `POST` all'indirizzo dell'impostazione `esmfold_url` (vuoto = `https://api.esmatlas.com/foldSequence/v1/pdb/`) con la sequenza come testo nel corpo | a mano il 1° ottobre 2026: 200, `chemical/x-pdb` |
| Risposta | PDB con HEADER, TITLE «ESMFOLD V1 PREDICTION FOR INPUT», riferimento e licenza (REMARK 1), ATOM numerati da 1 sulla catena A, senza TER né END | ubiquitina umana (76 residui): 601 atomi in 1,0 s; un'altra sequenza di 50 residui: 19 s (il servizio ha una coda) |
| pLDDT | per atomo nella colonna B-factor, su **0–1** (Cα dell'ubiquitina da 0,45 a 0,96): un file con un massimo non oltre 1 si moltiplica per 100 (`prediction.plddt_scale`), uno già su 0–100 (libreria di ESMFold, altri servizi) resta com'è; valori negativi, oltre 100 o tutti nulli sono una risposta non valida | |
| Lunghezza | al più 400 residui, controllata prima dell'invio (`sequence_too_long`); almeno 10 | oltre 400 il servizio risponde 413 «Sequence is longer than 400.» (stesso codice, se il limite del servizio cambiasse) |
| Alfabeto | solo i 20 amminoacidi standard, prima dell'invio (`invalid_sequence`, con le posizioni) | con X, B, Z il servizio risponde 200 ma salta quei residui (50 inviati, 47 nel file) |
| Verifica della risposta | coordinate presenti, lettere dei Cα uguali alla sequenza inviata e nello stesso ordine, al più 20 MB | |
| Errori | 404 → `esmfold_unavailable` («potrebbe essere stato dismesso, oppure l'indirizzo è sbagliato»); 413 → `sequence_too_long`; altri 4xx (429 con «troppe richieste») → `esmfold_rejected`; rete, tempo scaduto (300 s) e 5xx dopo 3 tentativi (pause di 5 e 10 s, annullabili) → `esmfold_unavailable`; reindirizzamento (3xx, non seguito) → `esmfold_unavailable`; risposta non valida → `esmfold_invalid_response`. Il testo della risposta d'errore entra nel messaggio solo dal servizio pubblico | test con il servizio simulato |
| Cache | `data/cache/esmfold`, chiave = indirizzo + sequenza, 30 giorni (come la pulizia della cache remota); nessuna voce per una risposta non valida; scritture atomiche, anche con più job insieme | |
| File salvato | intestazione del servizio conservata (oppure un TITLE «ESMFOLD PREDICTION, … SAVED BY BIOLOOM»), `REMARK 999 BIOLOOM PREDICTOR ESMFOLD <servizio>` e `… PLDDT SCALE 0-1 X100 TO 0-100`, pLDDT su 0–100, END | |

Se il servizio pubblico venisse dismesso, l'indirizzo si cambia nelle Impostazioni (un servizio compatibile: POST della
sequenza, risposta PDB); il limite di 400 residui resta.

**Consenso all'invio.** La sequenza (mai le coordinate) lascia il computer: impostazione `esmfold_remote`, spenta per
default. Le impostazioni precedenti non l'hanno e la prendono spenta; un valore che non è un vero booleano (file
modificato a mano) torna spento senza azzerare le altre impostazioni, e un indirizzo non valido torna vuoto. Il consenso
si controlla nella rotta (403 `esmfold_disabled`: nessun job, nessuna richiesta), nei controlli preventivi
(`esmfold_remote_disabled`) e di nuovo nell'handler (un job accodato dopo una revoca fallisce senza inviare). Con
AlphaFold DB non serve: va solo l'accessione.

**Riconoscimento della predizione** (`plddt.predictor`, lo stesso riconoscitore unico dei modelli AlphaFold): TITLE
«ESMFOLD … PREDICTION» senza EXPDTA e valori su 0–100 → `esmfold`. Il file grezzo del servizio (0–1) non passa per pLDDT
(fasce e ritaglio lo leggerebbero tutto come «molto basso»); una menzione di ESMFold altrove (REMARK, un TITLE che non
comincia con ESMFOLD), un EXPDTA (struttura sperimentale, modello MODELLER) o un mmCIF non contano, e non c'è un
riconoscimento dal nome. 1UBQ, `ubq_dimer.pdb` e `ubq_zn.pdb` restano `bfactor` (test). La predizione salvata si apre in
Qualità con il pLDDT, si carica in libreria come «Modello predetto (ESMFold)» e, come template, si ritaglia per pLDDT
conservando il suo TITLE (`modeller._provenance_header`).

**Job `predict`** (priorità 0, pool "io": quasi tutto il tempo è l'attesa del servizio):

1. sequenza: quella indicata (campo «Sequenza» della finestra), oppure quella della catena del modello (`chain`,
   predefinita la prima con Cα; `prediction.chain_sequence`): i residui modificati della catena contano con la lettera
   del residuo standard (MSE → M), e una catena interrotta (C–N oltre 2,0 Å; un salto di numerazione fra residui legati
   non toglie residui) non si invia a tratti uniti, che darebbero una proteina che non esiste: si usa la sequenza
   completa del file (SEQRES del PDB, `_entity_poly` del mmCIF) se contiene i tratti osservati nell'ordine, ha solo
   lettere standard e sta nel limite di ESMFold (informazione `predict_sequence_seqres`; il confronto abbina i residui
   per sequenza). Altrimenti si inviano i tratti con l'avviso nei controlli preventivi (`predict_sequence_gaps`) e nel
   risultato, e i residui entro 5 posizioni da una giunzione non contano come divergenti né come sicuri (nota
   nell'esito);
2. predizione: `prediction_esmfold.pdb`, oppure il modello di AlphaFold DB (dalla libreria, che lo scarica se manca)
   copiato come `prediction_AF-<ACC>-F<n>.pdb`;
3. confronto (con il modello, `source`): `align.structural` con il percorso integrato, modello = A (riferimento),
   predizione = B. La corrispondenza è per sequenza perché è la stessa proteina: restano anche le coppie lontane, che
   TM-align lascerebbe fuori facendo sparire dal confronto per residuo proprio il tratto che diverge. PDB sovrapposto
   `superposed.pdb`;
4. esito (`prediction.assess`) con le regioni problematiche del consenso del modello: rapporto `<modello>.quality.json`
   oppure l'analisi di qualità più recente della stessa struttura (stessa impronta del file).

Risultato: `predictor`, `label`, `origin` (servizio, cache, scala originale; per AlphaFold DB l'id del modello),
`prediction_file`, `recognized_as`, `plddt`, `sequence` (nome, lunghezza, origine `input` | `structure` | `seqres`,
interruzioni, residui modificati), `source`, `chain`, `source_sha256`, `comparison` (formato
dell'allineamento strutturale, `mode: structural`, per StructuralComparison), `plausibility`, `warnings`, `limits`.

**Esito** (`domain/prediction.py`):

| Livello | Regola | Fonte |
|---|---|---|
| ripiegamento | TM-score normalizzato sul modello ≥ 0,5 | Xu & Zhang, Bioinformatics 26:889, 2010; due strutture casuali circa 0,17 (Zhang & Skolnick, Proteins 57:702, 2004) |
| residuo divergente | lDDT-Cα del residuo del modello rispetto alla predizione < 0,5 | Mariani et al. 2013; confine della fascia «molto bassa» del pLDDT, che è un lDDT-Cα predetto (scelta BioLoom) |
| regione divergente | almeno 3 residui divergenti, uno non divergente tollerato fra due, stessa catena e numerazione contigua | come le regioni problematiche del consenso |
| predizione sicura | pLDDT medio della regione ≥ 70; incerta da 50 a 70; molto incerta sotto 50 | fasce di AlphaFold DB (Tunyasuvunakool et al. 2021), valide per ESMFold (Lin et al. 2023: stesso lDDT-Cα predetto) |
| conclusivo | almeno 30 residui (o tutti, per una catena più corta) con pLDDT ≥ 70 | come `plddt.MIN_TRIMMED_RESIDUES`: i più piccoli domini che si ripiegano da soli |

- **non conclusivo** (informazione): la predizione è sicura su troppo pochi residui per dire qualcosa sul ripiegamento;
- **ripiegamento diverso** (errore): TM-score sotto 0,5 con una predizione sicura: «controlla la scelta del template e
  l'allineamento»;
- **divergenze locali** (avviso): almeno una regione divergente dove la predizione è sicura: «Stesso ripiegamento
  (TM-score 0,93, lDDT 0,88), ma le due strutture divergono in 31-37 (catena A), dove la predizione è sicura (pLDDT medio
  90): controlla l'allineamento e il template in questa regione.»; le regioni incerte si aggiungono come non informative;
- **concordano** (ok): «Le due strutture concordano: TM-score 0,93 (stesso ripiegamento), lDDT 0,88; divergono solo in
  31-37 (catena A), dove anche la predizione è molto incerta (pLDDT medio 40): regione flessibile o poco determinata, il
  confronto lì non è informativo.»

Una regione divergente che tocca una regione problematica del consenso lo dice («segnalata anche dal consenso (DOPE e
Ramachandran segnalano 29-33 (catena A))»): due prove indipendenti sullo stesso tratto. Valori di riferimento (test in
`test_predict.py`, 1UBQ contro la sua copia con il tratto 31–37 spostato di 6 Å verso l'esterno come finta predizione):

| pLDDT della finta predizione | Esito | TM-score | lDDT | Regione divergente | lDDT dove la predizione è sicura |
|---|---|---|---|---|---|
| 90 ovunque | divergenze locali | 0,927 | 0,878 | 31–37, lDDT medio 0,24, sicura | 0,872 (76 residui) |
| 40 sul tratto, 90 altrove | concordano | 0,927 | 0,878 | 31–37, molto incerta | 0,936 (69 residui) |
| copia identica | concordano | 1,000 | 1,000 | nessuna | 1,000 |

Nel consenso di 1UBQ con il solo voto (98,9) il primo caso aggiunge l'accordo 0,872 → 87,2 con peso 0,25: 96,6 invece
di 98,9; nel secondo il metodo vale 93,6 e non segnala nulla.

**Limiti** (nel risultato e nell'interfaccia): una predizione non è una misura sperimentale; ESMFold è meno accurato di
AlphaFold sulle proteine con poche sequenze omologhe (Lin et al. 2023) e può sbagliare anche con pLDDT alto; un template
sperimentale vicino è di solito più affidabile della predizione; il confronto riguarda una catena (le interfacce di un
complesso non si valutano) e una sola conformazione (movimenti di dominio e stati legati diversi dal template danno
divergenze che non sono errori).

**Consenso e report.** Solo se l'utente ha eseguito il confronto: le rotte del consenso accettano `predict_job` (job
`predict` completato con il confronto, sulla stessa struttura: stessa sorgente e, se c'è, stessa impronta del file;
altrimenti 400 `predict_source_mismatch` o `predict_job_invalid`, 409 se non completato) e il report del modello include
il confronto indicato o il più recente (vedi [Consenso](#consenso-tra-i-metodi-e-qualità-per-residuo)
e [Report del modello](#report-del-modello)).

**Rotte** (`api/predict.py`):

| Rotta | Funzione |
|---|---|
| `POST /api/predict` | `{predictor: esmfold \| alphafold, source?, chain?, sequence?, accession?}` → job `predict` («Predizione ESMFold · 1UBQ (confronto)»); errori prima della coda: 403 `esmfold_disabled` (consenso), 400 `predict_input_required`, `invalid_chain`, `sequence_too_long`, `invalid_sequence`, `accession_required`, `invalid_accession`, `invalid_esmfold_url` |
| `GET /api/predict/latest?type=&id=&job_id=&file=` | come `/api/analysis/latest`: confronto completato più recente sulla stessa struttura e quello in coda o in corso |

Controlli preventivi (`kind: predict`): consenso, stessa validazione della rotta, rete o cache (nessuna richiesta dai
controlli), tempo (servizio pubblico misurato da 1 a 19 s, con i tentativi fino a 2 minuti; dalla cache meno di un secondo;
più il confronto, fino a 5 s a 400 residui) e spazio (circa 2,6 KB per residuo più 64 KB) (vedi [Controlli
preventivi](#controlli-preventivi)).

**Interfaccia** (`components/Predictor.tsx`, logica in `lib/predict.ts`):

- **Qualità** e **Modellazione** (sotto il 3D del modello scelto): riquadro «Confronto con una predizione» con il
  confronto più recente della struttura (o quello in corso, anche dopo aver ricaricato la pagina) e il pulsante
  «Confronta con una predizione (ESMFold)». La finestra fa scegliere ESMFold o AlphaFold DB (accessione); con ESMFold e
  senza consenso spiega prima che cosa lascia il computer e verso dove e chiede «Acconsento e continua» (salva
  `esmfold_remote`), poi mostra i controlli preventivi e `LaunchButton`. Con ESMFold c'è il campo facoltativo della
  sequenza completa (FASTA o testo). Una sequenza nota oltre i 400 residui (quella incollata, altrimenti la catena) non
  chiede il consenso per un invio impossibile: avviso con AlphaFold DB proposto e avvio nascosto (`esmfoldBlocked`).
  Impostazioni non lette: errore con «Riprova» invece di una rotella infinita; AlphaFold DB non ne dipende.
- Risultati: esito con il suo tono, TM-score, lDDT (anche dove la predizione è sicura), pLDDT, residui divergenti, regioni
  con la confidenza, origine (servizio, cache, scala riportata a 0–100), link a Qualità della predizione e a Confronto, PDB
  della predizione e il componente `StructuralComparison` sul PDB sovrapposto del job.
- In Qualità il confronto completato entra nel consenso (`predict_job`), con la nota dell'esito e la riga «predizione
  (info)» nel grafico per residuo, e nel report del pulsante Report. Link stabile `/quality?prjob=<id>` («Apri risultati»
  nella pagina Job).
- **Impostazioni → Generale**: riquadro «Predizione di struttura (ESMFold)» con il consenso e l'indirizzo del servizio
  (vuoto = servizio pubblico; un indirizzo non valido è un 400 `invalid_esmfold_url` della rotta delle impostazioni).
  In modalità server l'indirizzo è in sola lettura: lo imposta l'amministratore con `BIOLOOM_ESMFOLD_URL` (vedi
  [Sicurezza e privacy](#sicurezza-e-privacy)).

### Report del modello

`domain/report.py` (HTML e grafici, logica pura; F4.2) e `services/reports.py` (raccolta dei dati): un file HTML autonomo,
stampabile in PDF dal browser, generato al volo dai file già salvati del job, senza ricalcoli.

- **Rotte:**
  - `GET /api/jobs/{id}/report/{modello}` per i job `model`, `loop` e `pipeline` (per la pipeline
    `items/<idx>_<nome>/<modello>` di un elemento completato) e per il modello rifinito di un job `refine`
    (`refined_file`);
  - `GET /api/jobs/{id}/report` per un job `quality`: report ridotto, senza template, allineamento e parametri;
  - mutagenesi (F9.2): `GET /api/jobs/{id}/report/{modello}` per il mutante o il selvatico riottimizzato di un job
    `mutate` (sezione "Mutagenesi in silico" e la qualità del modello) e `GET /api/jobs/{id}/report` per un job `alascan`
    (sezione "Scansione di alanina", niente qualità);
  - le varianti `POST`, con corpo `{"image_png_base64"}`, incorporano la vista 3D;
  - `qmean_job=` aggiunge QMEAN con gli stessi controlli della rotta del consenso; `predict_job=` (F9.3) sceglie il
    confronto con una predizione da includere (senza, il più recente sulla stessa struttura); `download=false`
    restituisce il file `inline` invece che `attachment`.

  Nome del file: `report_<modello>.html` o `report_qualita_<struttura>.html`, solo `[A-Za-z0-9_.-]`.
- **Errori:** modello non tra quelli riusciti del job, file che non è un modello (per `refine`: non è il modello
  rifinito), elemento con un altro nome → 404; job o elemento non completato → 409; nessun modello per un job
  model/loop/pipeline/refine → 400 `report_model_required`; job di
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
  | Analisi strutturale | job `analysis` completato più recente sulla stessa struttura (`AnalysisService.latest`: stessa sorgente, `{job, file}` per i modelli, e stessa impronta del file), riassunto da `analysis.report_summary`: conteggi delle interazioni, tabelle complete di legami idrogeno, ponti salini e interazioni aromatiche (blocchi richiudibili), ponti disolfuro, interfacce (area, residui, interazioni fra le catene, composizione, plausibilità con i limiti, ΔSASA per residuo in un grafico a barre) e conservazione (righe usate, profilo per residuo). La sezione compare quando c'è un'analisi (vedi [Analisi strutturale](#analisi-strutturale)) |
  | Tasche | job `pockets` più recente sulla stessa struttura (`StructureToolsService.latest`, stessi criteri), riassunto da `structure_tools.pockets_summary` (senza le sfere alfa): riepilogo, druggability, volume e punteggio di fpocket per tasca (barre, la druggabile in verde) e tabella con i descrittori e tutti i residui che rivestono ogni tasca |
  | Elettrostatica | job `electrostatics` più recente sulla stessa struttura, riassunto da `structure_tools.electrostatics_summary`: carica netta, intervallo del potenziale, profilo del potenziale per residuo con le soglie, residui marcati, tabella completa (richiudibile), cariche per tipo, parametri di PDB2PQR e APBS |
  | Dati per residuo | appendice con tutti i residui del rapporto: DSSP, φ, ψ, Ramachandran, clash, RSA, DOPE, B-factor o pLDDT e, se ci sono, QMEAN locale, ProSA, disordine, lDDT rispetto alla predizione e le segnalazioni del consenso (righe con almeno 2 metodi evidenziate); in un blocco richiudibile |
  | Mutagenesi | job `mutate` e `alascan` (F9.2): dal risultato del job; classe con le motivazioni pesate e l'avvertenza, siti (struttura secondaria, RSA, BLOSUM62, superficie apolare sepolta, interazioni perse, interfaccia), DOPE totale, locale e fra le catene, energia di OpenMM con i riferimenti, metriche del selvatico riottimizzato e del mutante, protocollo e fonti; per la scansione la tabella ordinata, gli esclusi e gli hot spot. Sezioni bilanciate (`doc.section`/`doc.end`) |
  | Interfacce del complesso | modelli a più catene (F5.3): `complex` del risultato (catene del modello e del template, simmetria) e il confronto delle interfacce del rapporto di qualità del modello (per il migliore senza analisi, quello del risultato): tabella con area (e quella del template), residui conservati, Fnat, iRMS, LRMS, DockQ ed esito, testo motivato per interfaccia e limiti; nei parametri la riga "Simmetria fra catene" (vedi [Complessi e omo-oligomeri](#complessi-e-omo-oligomeri)) |
  | Confronto con una predizione | F9.3: il job `predict` indicato (`predict_job=`, con i controlli della rotta del consenso: un'altra struttura è un 400) oppure il confronto completato più recente sulla stessa struttura (stessa sorgente e impronta; uno che non vale più per il rapporto si tralascia). Sezione "Confronto con una predizione (ESMFold)" bilanciata con l'esito, TM-score, lDDT, GDT-TS, pLDDT, regioni divergenti con la confidenza e le regioni del consenso, note e limiti; consenso con l'accordo con la predizione (vedi [Confronto con un predittore esterno](#confronto-con-un-predittore-esterno)). Senza confronto il report non cambia |

  Un modello senza rapporto di qualità ha il report dei soli risultati di MODELLER, con un avviso.
- **Struttura del documento:** l'equilibrio delle sezioni è una proprietà di `_Doc`, non di ogni funzione: aprire una
  sezione con un'altra ancora aperta, chiuderla senza averla aperta, ripetere un id o finire il documento con una sezione
  aperta solleva `ReportStructureError`. Errore e non chiusura automatica: le sezioni dipendono solo dal codice, quindi
  uno squilibrio è un errore di programmazione (è successo due volte in un'unione di rami) che il test parametrizzato su
  ogni tipo di report (modello, loop, pipeline, qualità con e senza disordine, rifinitura, mutagenesi, scansione, con e
  senza ligandi, complessi, predizione e analisi strutturale) ferma subito, controllando anche id univoci e HTML ben
  formato con `html.parser`; chiuderle in silenzio nasconderebbe la svista.
- **Grafici SVG** (generati dal backend, XML ben formato):
  - Ramachandran: un pannello per classe, come nell'interfaccia (generale con la pre-prolina, glicina, prolina), con i
    rettangoli favoriti/consentiti di `domain/ramachandran` e un cerchio per residuo con φ e ψ (74 su 76 in 1UBQ); gli
    outlier sono sopra gli altri punti ed etichettati;
  - profili per residuo: DOPE (media mobile di MODELLER, soglia media + 1 DS del consenso: picco 73–76 su 1UBQ), QMEAN
    locale (sotto 0,6), energia ProSA sulla finestra di 10 residui (sopra 0) e pLDDT (linee a 50, 70 e 90). La linea
    si interrompe tra catene e dove la numerazione salta;
  - accanto ai profili, il disordine predetto del consenso (F5.5): FoldIndex per residuo con la soglia 0 e, sullo sfondo,
    le regioni disordinate lunghe (i singoli residui sotto 0 non si segnano); nel consenso una nota con le regioni,
    "attese poco affidabili";
  - striscia della struttura secondaria a 3 stati e barra impilata della composizione (con il conteggio degli 8 stati
    DSSP); barra impilata delle regioni di Ramachandran di PROCHECK con la tabella dei parametri geometrici;
  - profili di RSA (soglia 20%), B-factor (solo strutture sperimentali con valori non tutti uguali) ed energia ProSA dal
    profilo di ProSA quando non è attribuibile ai residui del consenso;
  - tracce del consenso (`tracks_svg`, come `ConsensusTracks` dell'interfaccia): una riga per metodo con i residui
    segnalati, il numero di metodi per residuo, le regioni problematiche (tratteggiate se attese nel disordine predetto)
    e, se ci sono, disordine predetto e divergenza dalla predizione;
  - punteggi dei modelli (DOPE, zDOPE, molpdf, GA341, voto): un punto per modello su una scala adattata ai dati
    (`bars_svg(dots=True)`: i DOPE di modelli diversi differiscono di poche decine su migliaia), il migliore in verde;
  - grafici a barre (`bars_svg`) per ΔSASA d'interfaccia, druggability, volume e punteggio delle tasche, ΔDOPE e ΔΔE
    della scansione di alanina (colore della classe stimata); profili per la conservazione, il potenziale elettrostatico,
    lDDT e pLDDT del confronto con una predizione.
- **Tabelle lunghe:** tutti i residui, tutte le interazioni, i clash oltre i primi 20 e i residui dell'elettrostatica
  stanno in blocchi `<details>`, chiusi sullo schermo; in stampa `details::details-content{content-visibility:visible}`
  li apre nei browser che supportano lo pseudo-elemento (verificato su Chrome; quelli senza stampano solo il titolo del
  blocco, che va aperto prima di stampare). Sugli
  schermi stretti le tabelle scorrono in orizzontale invece di allargare la pagina.
- **Legenda e fonti:** solo le metriche presenti, con le soglie usate da BioLoom, e solo le fonti dei metodi presenti
  (Šali & Blundell 1993, Shen & Sali 2006, Melo et al. 2002, John & Sali 2003, Benkert et al. 2011, Sippl 1993,
  Wiederstein & Sippl 2007, Laskowski et al. 1993, Jumper et al. 2021, Tunyasuvunakool et al. 2021, Mariani et al.
  2013, Ramachandran et al. 1963, Lovell et al. 2003, Word et al. 1999, Kabsch & Sander 1983, Shrake & Rupley 1973,
  Tien et al. 2013, Rost 1999, Prilusky et al. 2005, Uversky et al. 2000, Kyte & Doolittle 1982, Ward et al. 2004;
  con l'analisi strutturale Richardson 1981, Krissinel & Henrick 2007, Janin et al. 2008, Lo Conte et al. 1999,
  Baker & Hubbard 1984, Barlow & Thornton 1983, McDonald & Thornton 1994, McGaughey et al. 1998, Gallivan & Dougherty
  1999, Salentin et al. 2015, e con la conservazione Capra & Singh 2007, Henikoff & Henikoff 1994; con le tasche Le
  Guilloux et al. 2009, Schmidtke & Barril 2010; con l'elettrostatica Baker et al. 2001, Jurrus et al. 2018, Olsson et
  al. 2011, Sitkoff et al. 1994 (PARSE); con le interfacce di un complesso Basu & Wallner 2016, Méndez et al.
  2003; con il confronto con una predizione Lin et al. 2023, Zhang & Skolnick 2004, Xu & Zhang 2010).
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

## Funzioni trasversali

Funzioni che attraversano le pagine e i tipi di job: controlli prima dell'avvio, procedura guidata, organizzazione con
etichette e ricerca, riproducibilità, eliminazione dei progetti e pulizia dei job.

### Controlli preventivi

Prima di avviare un calcolo, che cosa lo farebbe fallire o renderebbe il risultato poco affidabile, senza avviare nulla
(F8a). Regole e soglie, con le fonti, in `domain/preflight.py` (logica pura); i fatti (licenze, strumenti, libreria,
spazio, servizio QMEAN, job attivi) li raccoglie `services/preflight.PreflightService`, creato a ogni richiesta come
`CoverageService` (nessun servizio nuovo nel `Core`).

**Rotta** `POST /api/compute/preflight` con `{kind, params, context}`:

- `kind`: `model`, `loop`, `align2d`, `pipeline`, `quality`, `qmean`, `search`, `analysis`, `mutate`, `alascan`, `predict` (F9.3), `rerun` (riesecuzione di un
  pacchetto caricato, `params` = `{package_id}`: vedi [Riproducibilità](#riproducibilità-pacchetti-e-riesecuzione));
- `params`: lo stesso corpo della rotta di avvio, validato con lo stesso modello Pydantic (`ModelIn`, `PipelineIn`, …):
  un campo non valido è un esito `error` (`validation_error`), come il 422 dell'avvio, non un 422 dei controlli;
- `context` (facoltativo): `accession` (domini InterPro, come `/api/bio/coverage`), `search_job` (ricerca da cui vengono i
  template: punteggio dei template scelti).

Risposta: `ok` (nessun errore), `counts`, `checks` (errori, poi avvisi, poi informazioni, ciascuno `{level, code,
message, suggestion}` più `group: "coverage"` per gli avvisi di copertura e `template`), `estimate` (`seconds`, `low`,
`high`, `detail`, `basis`) e, per la modellazione, `coverage` (la verifica completa di `services/coverage.py`).

| Livello | Significato | Interfaccia |
|---|---|---|
| `error` | la rotta di avvio o l'handler rifiuterebbero la richiesta, oppure il job fallirebbe di sicuro | pulsante disattivato con il primo errore come motivo |
| `warning` | parte, ma il risultato rischia di essere poco affidabile o il job di non finire | conferma «Avvia comunque» con l'elenco degli avvisi |
| `info` | da sapere, senza conseguenze sul risultato | elenco discreto sotto gli avvisi |

- **Stessa validazione dell'avvio:** `modeller.parse_options`, `compute._record`, `_template_specs`,
  `_apply_ligand_options`, `sequences.validate_pir` con il numero dei template, `PipelineService.validate` (estratta da
  `submit`), `resolve_structure`, `AnalysisService.validate`, i limiti del loop: un loro errore è un esito `error` con lo
  stesso codice e lo stesso messaggio (test: per ogni richiesta rifiutata dall'avvio, 400 o job fallito, lo stesso
  messaggio fra gli errori dei controlli).
- **Nessun effetto:** niente job, niente download (un template assente con un codice PDB o un id AlphaFold è
  un'informazione: lo scaricherà il job), richieste remote solo per i domini (UniProt e InterPro con l'accessione, in
  cache 7 giorni) e per lo stato del servizio QMEAN locale (rete interna, in cache 15 s).
- **Robustezza:** un fatto che non si legge (template illeggibile durante il ritaglio, ricerca eliminata) diventa
  `check_skipped` (informazione), mai un errore del server.

**Esiti** (ogni codice ha un test che lo produce e uno che non lo produce, `test_preflight.py`):

| Codice | Livello | Regola | Fonte o motivo |
|---|---|---|---|
| `modeller_license_missing`, `modeller_license_invalid`, `modeller_not_installed` | error | MODELLER non utilizzabile (stato di `LicenseService`: chiave assente, rifiutata, modulo assente) | senza MODELLER modellazione, loop e allineamento non partono |
| `dope_unavailable` | warning | analisi di qualità con il DOPE senza MODELLER | l'analisi prosegue senza zDOPE |
| `template_missing`, `template_chain_missing`, `template_unreadable` | error | template né in libreria né scaricabile, catena assente, file illeggibile | la preparazione fallirebbe |
| `template_download` | info | assente ma scaricabile da RCSB o AlphaFold DB | serve la rete all'avvio |
| `twilight_zone` | warning | identità sotto la curva di Rost per le coppie allineate | Rost 1999 (`template_rank.rost_threshold`): omologia non dimostrata |
| `low_identity` | info | sopra la curva ma sotto il 30% | Baker & Šali 2001: bassa accuratezza, gli errori di allineamento dominano |
| `low_coverage` | warning | copertura dei template insieme sotto il 40% | soglia della pipeline e confine della fascia scarsa della copertura (`template_rank`) |
| `template_score_poor` | warning | con `search_job`: punteggio del template sotto 40, con le due componenti più basse | fascia scarsa di `template_rank` |
| `disorder_uncovered`, `domain_uncovered`, `domain_partial` / `disorder_covered` | warning / info | gli avvisi di `disorder.coverage_assessment` (gruppo `coverage`, il suggerimento sul primo avviso) | F5.5; nessun calcolo duplicato |
| `plddt_too_few` | error | ritaglio che lascia meno di 30 residui o una catena vuota | come `modeller.prepare_template` (`plddt.MIN_TRIMMED_RESIDUES`) |
| `plddt_trim_most` / `plddt_trim` | warning / info | il ritaglio toglie più della metà dei residui / qualcuno (con gli intervalli) | AlphaFold confidente solo su una minoranza della catena |
| `plddt_untrimmed` | warning | ritaglio spento e residui con pLDDT < 70 | vincoli da conformazioni predette con bassa confidenza |
| `procheck_unavailable`, `prosa_unavailable` | warning se nessuno degli strumenti chiesti gira, altrimenti info | PROCHECK assente; ProSA assente, senza licenza, licenza illeggibile o MAC diverso | l'opzione non avrebbe effetto / l'analisi prosegue |
| `estimated_time` / `timeout_risk` | info / warning | stima del tempo; costruzione dei modelli (un solo processo) oltre metà del timeout dei processi | incertezza della stima di un fattore 2 |
| `disk_full_data`, `disk_full_scratch` / `disk_low_*` | error / warning | spazio libero sotto la stima / sotto il massimo fra 1 GiB e 10 volte la stima; dati e scratch sullo stesso disco: una voce con la somma | il salvataggio finale fallirebbe a calcolo finito; 1 GiB per database SQLite con il WAL, cache (fino a 20 MB per voce) e download degli altri job (scelta BioLoom) |
| `qmean_memory`, `qmean_unavailable` | error, warning con il fallback consentito | `qmean_local.memory_problem` (12 GB per UniRef30_2020_06) o servizio assente; fallback attivo senza consenso o email = errore | come il controllo del job QMEAN |
| `qmean_remote_disabled`, `email_required` / `qmean_remote`, `qmean_local`, `qmean_busy` | error / info | SWISS-MODEL senza consenso o email; dove si calcola, servizio occupato | |
| `blast_missing`, `pdbaa_missing`, `mmseqs_missing`, `mmseqs_database_missing` | error | motore locale senza programma o database | |
| `mmseqs_no_index`, `remote_ncbi`, `ncbi_email`, `alphafold_blast`, `remote_alphafold` | info | ricerca più lenta senza l'indice (12 s invece di 2,3); dati che vanno a NCBI o EBI | misure di MMseqs2 in [Flusso dei dati](#flusso-dei-dati-e-file-temporanei) |
| `ncbi_many_searches` / `ncbi_parallel` | warning / info | pipeline con oltre 50 ricerche NCBI (motore NCBI, o AlphaFold DB senza accessione) / più ricerche NCBI insieme | linee guida di NCBI per l'interfaccia URL di BLAST: oltre 50 ricerche di sera o nel fine settimana, una richiesta ogni 10 s |
| `pipeline_too_long` / `pipeline_alphafold`, `pipeline_disorder` | warning / info | sequenze oltre `max_sequence_length` residui (falliranno); sequenze con e senza accessione; sequenze con regioni disordinate lunghe (FoldIndex, fino a 500 000 residui in tutto) | |
| `rerun_not_reproducible`, `rerun_unsupported`, `package_*`, `rerun_tool_unavailable` / `rerun_version_bioloom`, `rerun_version_image`, `rerun_version_tool`, `rerun_version_library`, `rerun_tool_missing`, `rerun_platform` / `rerun_remote_input`, `package_note` | error / warning / info | riesecuzione (`kind: "rerun"`): calcolo che dipende da servizi remoti o non rieseguibile, pacchetto non valido o scaduto, parametri che la rotta di avvio rifiuterebbe, strumento obbligatorio assente / versioni diverse da quelle del pacchetto / input remoti con la data e note del pacchetto; più licenza MODELLER, PROCHECK e ProSA, durata del calcolo originale, spazio e coda come gli altri calcoli | versioni diverse: avvisi, mai errori (F9.4) |
| `esmfold_remote_disabled` / `sequence_too_long`, `invalid_sequence`, `accession_required`, `invalid_chain`, `predict_input_required` | error | predizione (F9.3): ESMFold senza il consenso all'invio; la stessa validazione della rotta `POST /api/predict` (sequenza oltre 400 residui o con lettere non standard, accessione mancante per AlphaFold DB, catena assente) | il consenso è un esito a sé, con il suggerimento |
| `remote_esmfold`, `esmfold_cached`, `remote_alphafold`, `alphafold_in_library`, `predict_no_compare` | info | la sequenza andrà al servizio (rete); predizione già in cache (nessuna richiesta); a EBI va solo l'accessione, o il modello è già in libreria; nessuna struttura da confrontare | nessuna richiesta remota dai controlli: la cache si legge dal disco |
| `predict_sequence_gaps` / `predict_sequence_seqres` | warning / info | ESMFold con la sequenza della catena: catena interrotta senza una sequenza completa utilizzabile nel file (i tratti si unirebbero; residui vicini alle giunzioni non informativi) / catena interrotta, si invia il SEQRES | suggerimento: incollare la sequenza completa nella finestra |
| `queue_busy`, `single_model`, `identical_models`, `invalid_accession`, `check_skipped` | info | job attivi ≥ job in parallelo; un solo modello; più modelli con il raffinamento rapidissimo (`a.very_fast()` non perturba le coordinate iniziali: modelli identici, anche fra gli avvisi del risultato); accessione non valida (niente domini); controllo non eseguito | misura in [Prestazioni](#misure-per-i-controlli-preventivi) |
| `invalid_mutation`, `invalid_scan` | error | notazione, catena, residuo originale o scelta dei residui rifiutati dalla rotta di avvio (stesso messaggio); oltre 50 residui (20 con l'energia) | mutagenesi (F9.2) |
| `mutation_energy_unavailable` / `mutation_energy` | warning / info | energia chiesta senza OpenMM o PDBFixer (il job prosegue senza) / che cosa misura | |
| `mutation_ligand_near`, `mutation_break_near`, `scan_skipped` | warning, info, info | gruppo eteroatomico entro 8 Å dai residui da mutare (MODELLER lavora sulla sola proteina); interruzione della catena entro 5 Å; residui esclusi dalla scansione (Gly, Pro, Ala) | |

**Stima del tempo** (secondi; costanti misurate, vedi [Prestazioni](#misure-per-i-controlli-preventivi)):
preparazione 0,02 + allineamento 0,34 · s + avvio di MODELLER 0,26 + s · (restrizioni 0,46 + M · 2,29 · f) + qualità,
con s = residui / 76, M modelli, f il fattore del raffinamento (very_fast 0,24, fast 1, normal 1,42, thorough 4,17) e la
qualità 1,22 · s sul migliore (1,23 senza PROCHECK e ProSA), su tutti i modelli a gruppi di 3. Intervallo: da metà al
doppio (altri processori, emulazione x86, carico). Pipeline: ⌈elementi / parallelo effettivo⌉ × (modellazione + ricerca
media), con le ricerche da 1 a 5 minuti per NCBI, 2,3–12 s per MMseqs2, pochi secondi per gli altri. Su 1UBQ (test): 13,9 s
per 5 modelli con la qualità estesa (misurato 13,89), 9,2 s per un elemento di pipeline (9,13), 11,3 s di costruzione per
304 residui (11,25).

**Spazio stimato:** risultati 2,5 MiB + residui × (880 × modelli + 3600 × modelli analizzati); scratch in più 2300 × residui
× modelli per gli intermedi di MODELLER. Pipeline: la somma dei risultati e, per lo scratch, gli elementi più grandi che
girano insieme.

**Copertura e identità** (`services/coverage.py`): i template della libreria e le righe del PIR hanno anche `identity` e
`aligned` dallo stesso allineamento della copertura (Smith-Waterman, BLOSUM62, gap 11/1, come la ricerca; per il PIR le
colonne), che i controlli confrontano con la curva di Rost; per un bersaglio a più catene l'identità viene dalle colonne del
PIR (`report.alignment_identity`).

**Interfaccia** (`components/Preflight.tsx`, logica in `lib/preflight.ts`):

- `usePreflight(request)`: richiesta 0,6 s dopo l'ultimo cambio (0,8 s nella Pipeline), risposte superate ignorate, nuovi
  tentativi (5, ogni 4 s) finché un template risulta da scaricare; `check()` ripete i controlli subito.
- `PreflightPanel`: errori e avvisi come riquadri con il suggerimento, avvisi di copertura con `CoverageAlerts` (una volta
  sola, dalla verifica della stessa risposta), informazioni in piccolo con la base della stima nel suggerimento.
- `LaunchButton` (`launchDecision`): disattivato con il motivo se c'è un errore; con avvisi una finestra di conferma
  («Avvia comunque»); con i controlli ancora in corso li ripete al clic; se i controlli non riescono (server) l'avvio resta
  possibile, perché la rotta di avvio valida comunque.
- **Modellazione:** il riquadro sta sopra «Avvia modellazione» e sostituisce la verifica di copertura del riquadro Input
  (una richiesta invece di due); il corpo della richiesta è lo stesso dell'avvio (`useMemo`). **Pipeline:** sopra «Avvia
  pipeline». **Procedura guidata:** prima della ricerca (`search`), dell'allineamento (`align2d`) e della modellazione
  (`model`, con `search_job`). **Confronto con una predizione** (F9.3): nella finestra di avvio, dopo il consenso
  all'invio (`predict`).

### Procedura guidata

Pagina `pages/Wizard.tsx` (F8a).

`/wizard`, «Nuovo modello guidato» nella barra laterale e nella Panoramica. Riusa i componenti delle altre pagine
(`SequencePicker`, `FeatureMap`, `JobPanel` con il flusso in tempo reale, `AlignmentView` aperto in modalità Confidenza con
il nuovo `initialMode`, `LigandPicker`, `PlddtOption`, `Viewer3D`, `ReportButton`, `compareTargets`) e le rotte esistenti;
la logica dei passi è in `lib/wizard.ts` (pura, testata).

| Passo | Contenuto | Per andare avanti |
|---|---|---|
| 1 Sequenza | incollata, dal progetto, da file o da UniProt (accessione); anteprima di disordine, bassa complessità e domini (`/api/bio/coverage`); «Aggiungi al progetto» | sequenza proteica di 1–`max_sequence_length` residui (Impostazioni, predefinito 5000) |
| 2 Template | motore consigliato (`engineChoices`: RCSB, oppure il motore locale delle Impostazioni se è pronto) con le alternative spiegate e la disponibilità; risultati per punteggio, consigliati preselezionati una volta per ricerca; suggerimento di un altro motore se non ci sono template o il migliore è in zona crepuscolare | almeno un template, una catena per voce PDB |
| 3 Allineamento | job `align2d` sui template scelti; «Apri nell'Editor PIR» (`/pir?fromjob=`) oppure un documento PIR del progetto (con i template del documento) | allineamento completato o documento scelto |
| 4 Opzioni | 5 modelli (esempio di base del tutorial di MODELLER), raffinamento Rapido (le impostazioni predefinite di MODELLER; Standard ha la dinamica più lunga `refine.fast`, circa 1,4 volte il tempo, misurato), analisi estesa se PROCHECK o ProSA sono utilizzabili, ligandi se la scelta predefinita trasferisce qualcosa, ritaglio pLDDT a 70 con un template AlphaFold; ogni opzione con la spiegazione (`optionNotes`); i predefiniti si ricalcolano finché l'utente non cambia nulla | controlli preventivi senza errori |
| 5 Avvio | `JobPanel` del job `model`; a fine calcolo, se lo si stava guardando, ai risultati; un job che non c'è più (bozza o link a un job eliminato o ripulito) mostra l'errore con «Torna alle opzioni» (`missingModelJob`) | modellazione completata |
| 6 Risultati | modello migliore (DOPE minimo), voto, consenso (dal rapporto di qualità), DOPE, zDOPE, GA341, 3D; Qualità, Confronta con il template, Report, Tutti i modelli | |

- **Stato:** passo e job nell'URL (`?step=&search=&align=&doc=&model=`), il resto (bersaglio, accessione, motore, template,
  opzioni, scelta dei ligandi) nel browser (`localStorage`, `bioloom.wizard.<progetto>`, letto con `sanitizeDraft`: un
  valore non valido torna al predefinito). All'apertura la bozza, poi l'URL, che prevale; un link senza bozza (altro
  browser) riprende bersaglio, motore, template e opzioni dai parametri dei job (`fillFromJobs`).
- **Passaggi:** un cambio invalida i passi successivi (sequenza diversa → ricerca, template, allineamento e modello;
  template diversi → allineamento e modello; opzioni cambiate dall'utente → modello); un passo oltre quelli raggiungibili
  (`reachable`) mostra il più avanzato possibile.
- **Cronologia:** ogni passo è una voce della cronologia del browser (indietro e avanti cambiano passo); i job nuovi
  aggiornano l'URL senza voci nuove. Il passaggio automatico ai risultati avviene solo se il job era in corso sotto gli occhi
  dell'utente: tornando al passo 5 con «indietro» si resta lì (con «Vai ai risultati»).

### Etichette, preferiti e ricerca globale

Fase F8b: etichette e stella dei preferiti su progetti, job, sequenze e template, ricerca globale.

**Etichette e stella** (`domain/labels.py`, `services/labels.py`, rotte in `api/labels.py`). Progetti, job, sequenze e
template possono avere etichette (al più 20) e la stella dei preferiti. Chiave: tipo di elemento e id (per i template
l'id normalizzato della libreria, maiuscolo); un vincolo CHECK ammette solo i quattro tipi.

- **Normalizzazione** (una forma sola per etichetta, la stessa in `lib/tags.ts`): Unicode NFC, minuscole, spazi esterni
  e `#` iniziale tolti, spazi interni → `-`, trattini ripetuti uniti, `- _ . :` tolti ai bordi; ammessi lettere e cifre
  (anche accentate) e `- _ . : +`, inizio con una lettera o una cifra (`+` anche in fondo: "zn2+"); al più 32 caratteri,
  oltre è un errore (`invalid_tag`), mai un troncamento. "Urgente", " urgente " e "#URGENTE" sono la stessa etichetta.
- **Pulizia a cascata:** trigger del database (migrazione 2) tolgono etichette e stella quando si elimina un progetto, una
  sequenza o un job, qualunque sia il percorso: rotta, eliminazione di un progetto con i suoi job uno per uno, cascata
  `ON DELETE` delle sequenze (SQLite esegue i trigger anche sulle righe eliminate in cascata: verificato). I template
  stanno su file: la libreria avvisa dopo l'eliminazione (`TemplateLibrary.subscribe`), e `prune_templates` toglie
  all'avvio e prima dei conteggi le etichette dei template spariti per altre vie (file cancellati a mano); non tocca
  nulla se la cartella della libreria non esiste.
- **Pulizia dei job:** «Pulisci job…» conserva per impostazione predefinita i job con la stella o con etichette
  (`keep_favorites`, `keep_tagged`), leggendo `favorites` e `tags` nella selezione e di nuovo nel DELETE di ogni job: una
  stella aggiunta fra la selezione e l'eliminazione salva il job. Le etichette contano solo come «conserva»: il filtro
  `?tag=` della pagina Job non restringe la pulizia. Eliminando un progetto, stella ed etichette dei suoi job non lo
  salvano: la conferma dice quanti sono (vedi [Eliminazione di progetti e pulizia
  dei job](#eliminazione-di-progetti-e-pulizia-dei-job)).
- **Concorrenza:** controllo dell'elemento, limite e inserimento nella stessa transazione della connessione unica: due
  aggiunte concorrenti non superano il limite, la stessa etichetta aggiunta da più richieste resta una, e un elemento
  eliminato nel frattempo non riceve etichette orfane (per i template, eliminati fra controllo e inserimento, una verifica
  successiva toglie l'etichetta).

| Rotta | Funzione |
|---|---|
| `GET /api/tags?entity_type=` | etichette in uso con il numero di elementi, in totale e per tipo |
| `GET`, `POST {tag}` `/api/tags/{tipo}/{id}` | etichette e stella dell'elemento; aggiunta (404 se l'elemento non esiste, 400 se l'etichetta non è valida o oltre il limite) |
| `DELETE /api/tags/{tipo}/{id}/{etichetta}` | rimozione, idempotente |
| `GET /api/favorites?entity_type=`, `PUT`/`DELETE /api/favorites/{tipo}/{id}` | preferiti |
| `GET /api/projects`, `/api/projects/{id}/sequences`, `/api/jobs`, `/api/templates` | con `tags` e `favorite` in ogni riga e i filtri `?tag=` e `?favorite=`; per i job il filtro è in SQL, prima del limite |

**Ricerca globale** (`domain/textsearch.py`, `services/globalsearch.py`, `GET /api/search?q=&tag=&type=&limit=`).

- **FTS5:** la build di SQLite dell'immagine (3.45.1 di Ubuntu 24.04, `ENABLE_FTS5` fra le opzioni di compilazione:
  verificato) lo ha; si controlla comunque all'avvio creando una tabella temporanea, e senza FTS5 si usa LIKE
  (`BIOLOOM_SEARCH_FTS=0` lo forza). L'indice è **temporaneo**: una tabella FTS5 per tipo (`temp.search_<tipo>`, rowid =
  rowid della riga) riempita all'avvio e tenuta allineata da trigger temporanei su inserimenti, eliminazioni (anche in
  cascata) e modifiche dei soli campi indicizzati (gli aggiornamenti di progresso dei job non lo toccano). Nulla di FTS5
  finisce in `bioloom.db`: una build senza FTS5 apre e scrive la stessa cartella dati, cosa impossibile con tabelle
  virtuali o trigger FTS5 salvati nello schema. Costo misurato: 0,04 s per 10 000 job all'avvio.
- **Campi:** progetti (nome; descrizione), sequenze (nome; accessione, proteina e organismo dai metadati), documenti
  (nome; tipo), job (titolo; tipo e sua etichetta italiana, "modellazione" trova i job `model`, "rifinitura" i `refine`,
  "tasche" i `pockets`: ogni tipo registrato nel `JobManager` ha la sua etichetta, uguale a `KIND_LABEL` del frontend,
  verificato da un test; l'indice temporaneo si ricostruisce a ogni avvio, quindi un'etichetta nuova vale anche per i job
  già nel database, e il ripiego LIKE usa la stessa espressione), template (id; titolo e
  organismo dai metadati o dall'intestazione del file: `SOURCE … ORGANISM_SCIENTIFIC` o la voce mmCIF), etichette. I
  template stanno su file: un indice in memoria si ricostruisce quando cambia la firma della cartella (nomi, date e
  dimensioni di strutture e metadati) o quando la libreria avvisa; l'organismo letto dal file resta in cache per data.
- **Corrispondenza:** ogni parola della richiesta deve essere l'inizio di una parola di un campo, con le parole divise
  come il tokenizer `unicode61 remove_diacritics 2` (lettere e cifre; minuscole; senza accenti): «p6990» trova
  «sp_P69905_HBA_HUMAN», «proteina» trova «Proteína», «ubq» non trova «1UBQ» (non è l'inizio di una parola). FTS5 (query
  `"p6990"* "hba"*`) o LIKE (`bioloom_fold(campo) LIKE '%p6990%'`, funzione SQL della connessione che toglie accenti e
  maiuscole) selezionano i candidati; la stessa regola in Python decide e dà la pertinenza, così FTS5, LIKE e template
  danno gli stessi risultati (provato sugli stessi dati).
- **Pertinenza** (pesatura per campo come in BM25F, Robertson, Zaragoza e Taylor 2004: il nome vale il doppio degli altri
  campi): parola intera nel nome 3, inizio di parola nel nome 2, parola intera in un altro campo 1,5, inizio 1; +10 se il
  nome coincide con la richiesta, +4 se comincia con essa; +1 con la stella. A parità: il più recente, poi titolo e id.
  Per gruppo si leggono al più 200 candidati per BM25 (FTS5) o 2000 dai più recenti (LIKE) e se ne restituiscono `limit`
  (5, al più 50) con `truncated`; i gruppi si ordinano per il miglior punteggio.
- **Filtri:** `tag` restringe agli elementi con l'etichetta (senza testo li elenca tutti); `type` ai gruppi indicati;
  senza testo né etichetta si elencano i preferiti.
- **Sicurezza:** le parole contengono solo lettere e cifre e nella query FTS5 stanno comunque tra virgolette (con le
  virgolette interne raddoppiate): `AND`, `OR`, `NEAR`, `*`, `:`, parentesi e virgolette della richiesta restano parole o
  spariscono, mai operatori; nei LIKE `%`, `_` e `\` sono protetti con `ESCAPE`; tutto passa come parametro. Richiesta al
  più 200 caratteri e 8 parole.

**Interfaccia:**

- **Ricerca globale** (`components/GlobalSearch.tsx`, `lib/search.ts`): campo «Cerca…» nella barra in alto, Ctrl+K / ⌘K
  o `/`. Risultati raggruppati come li ordina il server, con le parole trovate evidenziate (`highlightParts`, stessa
  regola del backend); ↑/↓ (Ctrl+Home/End, PagSu/PagGiù) scelgono, Invio apre, Esc chiude. Finché la risposta per il
  testo attuale non arriva (150 ms di attesa più la richiesta) le voci di prima restano attenuate e Invio aspetta la
  risposta giusta per aprirne la prima voce (`enterAction`), invece di aprire una voce della ricerca precedente.
  `#etichetta` cerca solo fra gli elementi con quell'etichetta, `tipo:job` (o `in:`, nomi italiani e inglesi, anche più
  tipi) limita i gruppi, mostrati con il nome italiano (`TYPE_LABEL`: «sequenze», «progetti»…); scelta un'etichetta, la
  ricerca diventa `#etichetta` (con `tipo:` in italiano). Senza testo mostra i preferiti. Aprire un risultato di un altro progetto
  lo rende prima il progetto attivo (`resultTarget`); `/sequences?id=` e `/templates?id=&tag=` scelgono l'elemento.
- **Etichette e stella** (`components/Labels.tsx`, `lib/tags.ts`): stella e chip negli elenchi di job, template e
  sequenze e nel dialogo «Progetti» della barra in alto (preferiti in cima, filtro); `LabelEditor` (stella, chip con la ×,
  campo con l'etichetta normalizzata mostrata mentre si scrive e i suggerimenti di quelle già usate) nei dettagli e,
  per il progetto attivo, nella Panoramica. L'elenco della pagina è la fonte dei dati: il dettaglio riceve stella ed
  etichette dell'elemento (`itemLabels`) e si riallinea quando cambiano (stella cliccata nella riga, dialogo
  «Progetti»); un elemento fuori dall'elenco (filtro, limite dei job) le legge dal server. Filtro per etichetta o preferiti (`LabelFilterSelect`) negli elenchi di job e
  template, nell'URL (`?tag=`), così un link lo conserva; un clic su un chip filtra per quell'etichetta.

### Riproducibilità: pacchetti e riesecuzione

Un job completato si esporta in un **pacchetto di riproducibilità** (ZIP) che lo documenta per intero e che BioLoom sa
**rieseguire**, confrontando poi i risultati con quelli del pacchetto (F9.4). Regole pure in `domain/reproducibility.py`
(limiti e controlli dell'archivio, impronta del manifest, oscuramento, confronto, testi), versioni degli strumenti in
`engines/versions.py`, pacchetti e job `rerun` in `services/reproducibility.py`, rotte in `api/reproducibility.py`,
comando in `cli.py`.

**Contenuto del pacchetto** (`GET /api/jobs/{id}/package`, solo job completati: 409 altrimenti). Scritto su un file
temporaneo dello scratch e inviato con `FileResponse` come gli altri archivi; nessun file passa dalla memoria oltre
64 MB (i file più grandi si copiano a blocchi).

| Percorso | Ruolo | Contenuto |
|---|---|---|
| `manifest.json` | — | formato e versione del formato; versione di BioLoom e dell'immagine (`BIOLOOM_IMAGE_VERSION`); Python, sistema, architettura, Biopython e NumPy; versioni degli strumenti usati dal calcolo; job (tipo, titolo, date, durata); **parametri completi**; semi casuali; impostazioni che influiscono sul risultato; provenienza dalla cache dei calcoli; input remoti con la data; timbro dei database locali (ricerca); possibilità di riesecuzione con il motivo; note; elenco dei file con ruolo, SHA-256, dimensione e percorso nel job; `manifest_sha256` |
| `LEGGIMI.txt` | `documentation` | contenuto e istruzioni per rieseguire (interfaccia, comando, API), tolleranze, input remoti, note |
| `inputs/params.json`, `inputs/sequences.fasta` | `params`, `sequences` | parametri e sequenze (bersaglio, catene del complesso, sequenze dell'allineamento, query) |
| `inputs/library/<ID>.pdb\|cif`, `<ID>.meta.json` | `template`, `template_meta` | file originale del template nella libreria e metadati (sorgente, data, modello predetto); se il template non è più in libreria, la copia preparata del job, con una nota |
| `inputs/templates/<file>` | `template_prepared` | copie preparate del job (catene, ligandi, ritaglio pLDDT), con il percorso nel job per il confronto |
| `inputs/alignment.ali` | `alignment` | PIR usato da MODELLER |
| `inputs/uploads/<file>`, `inputs/jobs/<id>/<file>` | `structure` | strutture analizzate: file caricati, modelli di altri job |
| `inputs/jobs/<id>/alignment.json` | `msa` | allineamento della conservazione preso da un job `align` (analisi strutturale) |
| `inputs/jobs/<id>/pockets.json` | `job_result` | tasche di fpocket di un job `pockets` scelte per una scansione di alanina per tasca (impronta del file e residui): la riesecuzione le legge dal pacchetto (`PackagedMutations`), anche dopo l'eliminazione del job delle tasche |
| `outputs/<percorso>` | `output` | tutti i file del job con lo stesso percorso, `result.json` compreso |
| `scripts/<percorso>` | `script` | script MODELLER eseguiti (`model.py`, `align.py`, `loop.py`, `dope_eval.py`): solo documentazione |
| `log/job.log` | `log` | log del calcolo, oscurato |

Misurati: analisi di qualità di 1UBQ con PROCHECK 19 file, 2,8 MB decompressi, 0,4 MB compressi; modellazione di
yeast_ub su 1UBQ con 5 modelli e la qualità estesa 36 file, 3,3 MB e 0,5 MB, esportata in 0,8 s.

- **Versioni** (`engines/versions.py`): come la verifica dell'installazione gli strumenti si trovano con `tools.find`, e
  ognuno dice la sua versione con l'opzione apposita (`mkdssp --version`, `blastp -version`, `TMalign -v`,
  `USalign -h`, `mmseqs version`, `apbs --version`, `pdb2pqr --version`, …), in un processo breve con timeout e la
  versione in memoria per eseguibile (percorso, dimensione, data). MODELLER da `tools.detect_modeller`, PROCHECK dallo
  script (`PROCHECK v.3.5.4`), OpenMM, PDBFixer, Biopython e NumPy dai metadati dei pacchetti, senza importarli. Solo gli
  strumenti del tipo di job (e delle opzioni: PROCHECK e ProSA solo con la qualità estesa). Le versioni sono quelle
  dell'installazione al momento dell'esportazione (`environment.detected`): i job non registrano l'ambiente a ogni
  esecuzione.
- **Semi:** `modeller_rand_seed` delle opzioni (predefinito −8123, il seme di `Environ()` di MODELLER, che usano anche
  loop e allineamenti); per la mutagenesi e la scansione i semi dei modelli di ogni variante (`mutations.seeds`) e, con
  l'energia, `openmm_seed`; per la rifinitura `openmm_seed` (`refinement.MD_SEED`) e i thread usati; gli altri calcoli
  sono deterministici.
- **Impostazioni** (`RELEVANT_SETTINGS`): `compute_cache`, `keep_intermediates`, `procheck_plots`, `job_timeout_minutes` (e per QMEAN
  modalità, fallback, consenso, nome del database). Non l'email, l'interprete di MODELLER, i processori o la
  conservazione degli upload: non cambiano il risultato e alcune sono dati personali o percorsi.
- **Input remoti:** template scaricati da RCSB o AlphaFold DB (data di `added_at` nei metadati), ricerche RCSB, NCBI e
  AlphaFold DB, metadati RCSB dei motori locali, QMEAN di SWISS-MODEL (con la pagina dei risultati), la predizione di
  un job `predict` (servizio ESMFold con la data di ricezione, anche dalla cache, o modello di AlphaFold DB). Un template
  modificato in libreria dopo la fine del job (oltre 1 s: le date dei job sono al secondo; durante il job è il job stesso
  che lo scarica) e un file analizzato con un'impronta diversa da `source_sha256` del risultato sono annotati; il
  secondo rende la riesecuzione non disponibile (l'input non è più quello del job).
- **Segreti** (`Redactor`): chiave MODELLER (file, `MODELLER_KEY`, chiave attiva dei sottoprocessi), codici delle licenze
  ProSA, email di contatto e chiave d'accesso del server (impostazione, `BIOLOOM_ACCESS_KEY`, `data/access_key`, quella
  attiva) diventano «[rimosso]» in ogni file testuale e nel manifest, senza distinguere le maiuscole; cartella dati,
  scratch, cache locale, installazione e home diventano segnaposti (`<cartella dati>/jobs/…`), solo all'inizio di un
  percorso: «https://data.rcsb.org» non contiene «/data». Ogni file oscurato è segnato (`redacted`) e ricontrollato: un
  file non testuale con un segreto resta fuori dal pacchetto, con una nota. Gli script non contengono mai la chiave
  (il preambolo la legge dall'ambiente).

**Tipi di job.**

| Tipo | Pacchetto | Riesecuzione | Confronto delle metriche |
|---|---|---|---|
| `model`, `loop` | sì | sì | per modello DOPE, zDOPE, GA341, molpdf, voto, Ramachandran, clashscore; modello migliore; PIR usato |
| `align2d` | sì | sì | PIR |
| `quality` | sì | sì | voto, punteggio, Ramachandran, clashscore, struttura secondaria, DOPE, ProSA, PROCHECK |
| `align` (pairwise, multiplo, strutturale) | sì | sì | programma e righe allineate; TM-score, RMSD, GDT, lDDT |
| `analysis` | sì | sì | legami H, ponti salini, disolfuri, π-stacking, catione-π, interfacce e area sepolta |
| `refine` | sì | sì | energie prima e dopo, RMSD della catena principale, qualità dopo |
| `pockets`, `electrostatics` | sì | sì | tasche e druggability; carica netta e potenziale |
| `mutate` | sì | sì (MODELLER con gli stessi semi; con l'energia OpenMM e PDBFixer indispensabili) | classe stimata, punteggio, ΔDOPE (totale, residui equivalenti, locale, fra le catene), RMSD selvatico–mutante, ΔΔE e le energie delle quattro minimizzazioni |
| `alascan` | sì (con le tasche del job `pockets` per la scansione di una tasca) | sì | classi (conteggi), ordine della tabella, per residuo classe, ΔDOPE e ΔΔE |
| `search`, `qmean`, `predict` | sì | no: dipendono da servizi remoti (o dal servizio QMEAN con UniClust30; la predizione di ESMFold o di AlphaFold DB, registrata come input remoto datato, con la predizione ricevuta fra i risultati); 400 `rerun_not_reproducible` | — |
| altri (`pipeline`, `selftest`, database, `rerun`) | no: 400 `package_unsupported` (per la pipeline l'archivio ZIP dei risultati) | — | — |

**Pacchetto caricato: input non fidato** (`open_package`, `domain/reproducibility.check_archive`):

| Controllo | Errore |
|---|---|
| nome: percorso assoluto, unità di Windows, barra rovesciata, componenti vuote, `.` o `..`, nascoste o riservate (CON, NUL…), caratteri di controllo, oltre 240 caratteri o 12 livelli, fuori da `inputs/ outputs/ scripts/ log/` (salvo manifest e LEGGIMI) | `package_unsafe_path` |
| collegamento simbolico o file speciale (modo Unix negli attributi esterni) | `package_symlink`, `package_special_file` |
| stesso nome due volte, anche solo per maiuscole o forma Unicode (all'estrazione su Windows o macOS uno coprirebbe l'altro) | `package_duplicate` |
| cifratura, metodo diverso da stored e deflate | `package_encrypted`, `package_compression` |
| oltre 256 MB compressi, 512 MB per file o 1 GB decompressi, oltre 5000 file; un file più lungo di quanto dichiara | `package_too_large` (413), `package_too_many_files`, `package_bomb` |
| rapporto di compressione oltre 100:1 per un file oltre 1 MB (deflate arriva a 1032:1; i file di BioLoom fino a 35:1, misurato) | `package_bomb` |
| manifest assente, non JSON, di un altro formato o di una versione più recente del formato; campi mancanti; percorsi o checksum non validi; campi letti dopo di forma inattesa (`environment.tools`/`libraries` non oggetti, `python`/`machine` non testi, `notes`, `remote`, `databases` non elenchi, `seeds`, `settings`, `cache`, `rerun` non oggetti, `job.duration_seconds` non numerico, date e titolo non testi): l'impronta si ricalcola, quindi un manifest risigillato di forma inattesa è un rifiuto motivato in anteprima, controlli preventivi e avvio, mai un errore interno | `package_invalid`, `package_manifest_invalid`, `package_format` |
| impronta del manifest diversa (SHA-256 del JSON canonico senza `manifest_sha256`): parametri, checksum o versioni modificati dopo l'esportazione | `package_manifest_altered` |
| file non dichiarati o mancanti; SHA-256 o dimensione diversi (verificati a blocchi, di nuovo all'estrazione) | `package_undeclared`, `package_missing_file`, `package_checksum` |
| parametri che la rotta di avvio rifiuterebbe (stesso modello Pydantic: `ModelIn`, `QualityIn`, …, e gli stessi controlli immediati) | `package_params_invalid` |

- **Nessun codice del pacchetto:** si estraggono solo gli ingressi (mai `scripts/` né `log/`), nella cartella
  temporanea del job (`.tmp/pacchetto`, eliminata a fine job). Gli script si rigenerano dai parametri validati, come
  per un calcolo nuovo; i valori finiscono negli script con le stesse regole (regex, `repr()`).
- **Versioni diverse** (`environment_warnings`): BioLoom, immagine, strumenti, Python, Biopython, NumPy, architettura
  diversi da quelli del pacchetto sono **avvisi** (anteprima, controlli preventivi, log e risultato della riesecuzione),
  mai errori; uno strumento usato dal calcolo e assente qui è un avviso, e se è indispensabile (OpenMM, fpocket,
  APBS…) un errore dei controlli preventivi.

**Riesecuzione** (job `rerun`, priorità 1, pool "cpu"):

1. `POST /api/jobs/rerun/inspect` (multipart, `file`): il pacchetto si salva a blocchi in
   `scratch/tmp/rerun_<id>.zip` (eliminato dalla pulizia dopo 24 ore, o con `DELETE /api/jobs/rerun/{id}`), si verifica
   e risponde l'anteprima con `package_id`: manifest, versioni nel pacchetto e qui, esiti. Un pacchetto rifiutato non
   resta nello scratch.
2. Controlli preventivi `kind: "rerun"`, `params: {package_id}`: gli esiti dell'anteprima più licenza MODELLER
   (obbligatoria per model, loop, align2d; avviso per il DOPE), PROCHECK e ProSA, strumenti indispensabili, durata del
   calcolo originale come stima (da metà al doppio), spazio (pacchetto più risultati nei dati; ingressi più il doppio dei
   risultati nello scratch), coda.
3. `POST /api/jobs/rerun` (multipart: `file` oppure `package_id`; `project_id`): stesse verifiche, poi il pacchetto si
   copia in `data/jobs/.rerun-incoming/` e alla creazione del job si sposta (rinomina, stessa cartella) in
   `data/jobs/<id>/package.zip`: dopo l'inserimento del job non resta nulla che possa fallire per lo spazio. Lo stesso
   `package_id` si può lanciare più volte, anche insieme (provato con due avvii concorrenti).
4. Il job riapre e riverifica il pacchetto, estrae gli ingressi ed esegue **gli handler di sempre** su un
   `PackagedCompute`: un `ComputeService` senza registrazione (`register=False`) con una libreria privata
   (`inputs/library`), i file caricati e i file di altri job del pacchetto e `auto_fetch` spento (un template assente è
   un errore, mai un download); l'analisi strutturale legge l'allineamento dal pacchetto (`PackagedAnalysis`). Parametri
   validati, `use_cache` False (anche nell'allineamento della conservazione), nessun progetto. I file si scrivono in
   `riesecuzione/`, con gli stessi percorsi del job originale, e il risultato in `riesecuzione/result.json`.
5. Confronto e rapporto: `confronto.json` e `RAPPORTO_RIESECUZIONE.txt` nella cartella del job; il risultato del job
   ha esito, conteggi, strutture, metriche, avvisi, tolleranze e i file non identici.

**Comando** `python bioloom.py rerun <pacchetto.zip> [--data <cartella>]` (anche `python -m bioloom rerun`): crea il job
nella cartella dati indicata (predefinita: quella di BioLoom), ne mostra le fasi, stampa il rapporto ed esce con 0
(identico, equivalente o compatibile), 1 (diverso) o 2 (pacchetto non valido, calcolo non rieseguibile, job fallito o
annullato, con il messaggio in italiano «Riesecuzione non riuscita: …», cartella dati in uso da un server:
`DatabaseInUse`). Con `--data` scratch e cache locale stanno dentro quella cartella: il comando non
tocca lo scratch di un server, che il `recover` all'avvio svuoterebbe. Senza chiave MODELLER nella cartella indicata si
usa quella della cartella dati predefinita o di `MODELLER_KEY`.

**Confronto** (`compare_file`, `verdict`):

- **File:** ogni file del pacchetto con un percorso nel job (risultati, template preparati, PIR, script) si confronta con
  quello della riesecuzione: `identical` (byte uguali, oppure uguali senza le parti che non dipendono dal calcolo),
  `equivalent` (valori uguali entro le tolleranze), `different` (con le differenze quantificate), `missing`; i file
  prodotti solo dalla riesecuzione sono `extra` (intermedi di MODELLER esclusi).
  - **PDB:** coordinate dei record ATOM/HETATM per catena, residuo, nome e altloc, **senza sovrapposizione** (un modello
    riprodotto ha le stesse coordinate assolute; una sovrapposizione nasconderebbe una rototraslazione): atomi in comune
    e solo in uno dei due, RMSD, scarto massimo con l'atomo; gli altri record parola per parola.
  - **JSON:** stessa struttura (campi ed elementi), stessi testi, numeri entro la tolleranza; differenza massima e dove,
    primi esempi.
  - **Altri testi** (PIR, profili DOPE, `.sum` di PROCHECK…): riga per riga e parola per parola, numeri con la tolleranza.
- **Non dipendono dal calcolo** e non si confrontano: la data e l'ora che MODELLER scrive nella riga EXPDTA dei modelli
  (riprese dal rapporto di qualità nel metodo della struttura), `cached`, `cached_at`, `timings` e `threads`. Misurato
  rieseguendo gli stessi job (qualità con PROCHECK, modellazione con la qualità estesa): data di MODELLER e log a parte,
  tutti i file sono identici byte per byte.
- **Tolleranze:** coordinate 0,002 Å per atomo (il formato PDB scrive tre decimali: due arrotondamenti dell'ultima cifra
  differiscono al più di √3 · 0,001 ≈ 0,0017 Å); numeri |Δ| ≤ max(0,001; 10⁻⁶ · |valore|) (doppia precisione IEEE 754:
  le stesse somme in un altro ordine cambiano le ultime cifre; il DOPE di un modello di 1UBQ, circa −8000, ha così 0,008).
- **Esito:** `diverso` se un file principale (non uno script né un intermedio di MODELLER) è diverso o mancante;
  `identico` se ogni file confrontato è identico e non ce ne sono altri; altrimenti `equivalente`. Un esito `diverso`
  diventa **`compatibile`** se il tipo di calcolo ha tolleranze scientifiche dichiarate (`SCIENTIFIC_TOLERANCES`,
  `scientific_check`) e la riesecuzione le rispetta: nessun file principale mancante, ogni struttura principale con gli
  stessi atomi e la catena principale (N, CA, C, O, senza sovrapposizione) entro 0,05 Å, le energie dichiarate entro
  max(50 kJ/mol; 0,5%) e le grandezze `exact` uguali. Vale per la rifinitura (energie prima e dopo) e, solo con
  l'energia, per mutagenesi e scansione (energie delle minimizzazioni; classe stimata, ΔDOPE e conteggi delle classi
  devono restare uguali: se cambiano, il risultato è diverso). È il rumore di OpenMM su più thread, che somma le forze in
  un ordine diverso a ogni esecuzione: misurato rieseguendo la rifinitura di 1UBQ con 3 thread (10 riesecuzioni),
  catena principale 0,0035–0,020 Å (tutti gli atomi fino a 0,16 Å, catene laterali esposte fino a 1,2 Å), energia
  finale 2,7–41 kJ/mol su circa −12 400 (≤ 0,33%), energia iniziale entro 2 kJ/mol; 0,05 Å è 2,5 volte lo scarto
  massimo e resta sotto lo spostamento della rifinitura (0,08 Å). Rapporto e pagina mostrano i controlli con i limiti;
  le altre differenze (diedri, DOPE, profili, che seguono le coordinate) restano elencate. Con un thread
  (`OPENMM_CPU_THREADS=1`) l'esecuzione è identica.
- **Metriche principali** per tipo (tabella sopra), dal `result.json` del pacchetto e della riesecuzione, con la
  differenza e l'esito entro la tolleranza.

**Misure** (test, MODELLER 10.8, immagine 4.1.0 e di sviluppo):

| Calcolo | Esito |
|---|---|
| qualità di 1UBQ | identico |
| modellazione di yeast_ub su 1UBQ, 2–5 modelli, raffinamento `fast`, seme −8123 | identico (29 file su 29 con 5 modelli e la qualità estesa; riesecuzione in 14,3 s, originale 13,8 s) |
| stesso pacchetto con il seme cambiato (manifest risigillato) | diverso: RMSD dei modelli oltre la tolleranza, DOPE diverso |
| allineamento strutturale e analisi strutturale con le sorgenti eliminate dopo l'esportazione | identico (input dal pacchetto) |
| rifinitura di 1UBQ, un thread di OpenMM | identico |
| rifinitura di 1UBQ, più thread | compatibile (catena principale 0,0035–0,020 Å, energia finale fino a 41 kJ/mol): la piattaforma CPU di OpenMM non ripete le somme nello stesso ordine con più thread (vedi la rifinitura sopra) |
| mutagenesi K48R su 1UBQ, scansione di alanina di una tasca | identico (stessi semi di MODELLER; le tasche dal pacchetto) |
| confronto con ESMFold | pacchetto con la predizione come input remoto datato; riesecuzione rifiutata (`rerun_not_reproducible`) |

**Interfaccia:** «Pacchetto riproducibile» (`components/ReproButton.tsx`) nel dettaglio della pagina Job e nel riquadro
del job (`JobPanel`) delle pagine dei risultati, per i job completati dei tipi con un pacchetto; pagina «Riesegui da
pacchetto» (`pages/Rerun.tsx`, `/rerun`, pulsante nella pagina Job; logica in `lib/rerun.ts`): caricamento con verifica,
anteprima (calcolo, contenuto, semi, cache, impronta, versioni nel pacchetto e qui con le differenze evidenziate,
parametri, input remoti, elenco dei file), `PreflightPanel` e `LaunchButton`, job seguito in tempo reale e rapporto
(esito, controlli delle tolleranze scientifiche per l'esito compatibile, strutture con l'RMSD della catena principale,
metriche con le differenze, file non identici, tolleranze, rapporto e archivio da scaricare), riesecuzioni recenti. Il
job originale del pacchetto è un link solo se esiste in questa installazione (un pacchetto di un altro computer, o con
il job eliminato, lo mostra come testo); la pagina Job mostra «Job non trovato in questa installazione» per un id che
non c'è. `/rerun?job=<id>` apre una riesecuzione («Apri risultati» nella pagina Job).

### Eliminazione di progetti e pulizia dei job

Due capacità **manuali**, scelte e confermate dall'utente, per non accumulare file inutili: la pulizia dei job
terminati con criteri espliciti e l'eliminazione di un progetto con tutti i suoi job. Nessuna eliminazione automatica
dei risultati dei job, né all'avvio, né nel passo giornaliero, né con «Pulisci file temporanei»: la pulizia automatica
toglie solo i resti `.deleting-<id>` di job che l'utente ha già eliminato (vedi [Flusso dei
dati](#flusso-dei-dati-e-file-temporanei)). Logica in `services/jobcleanup.py` (`JobCleanup`, creato in `build_core`
con `stopping` = `Core.shutting_down`, come i flussi SSE), eliminazione di un job e `submit` in `core/jobs.py`, progetto
sostitutivo in `services/projects.py` (`delete_if_unused`), rotte in `api/jobs.py` e `api/projects.py`. Nessuna
migrazione: tabelle, cascate e trigger esistenti.

**Rotte** (dietro la chiave d'accesso come le altre; risposte 200 con un oggetto, errori `{"error": {code, message,
details?}}` in italiano):

| Rotta | Risposta |
|---|---|
| `GET /api/projects/{id}/usage` | sola lettura, per la conferma: `id`, `name`, `created_at`, `updated_at`, `sequences`, `documents`, `jobs` (`total`, `active`, `starred`, `tagged`, `resumable`, `by_status` con gli stati presenti, attivi compresi), `bytes`, `bytes_complete`; conteggi da SQL su tutti i job del progetto |
| `DELETE /api/projects/{id}` | completa e sincrona: `deleted`, `jobs_deleted`, `freed_bytes`, `leftover` (cartelle rimosse solo in parte), `replacement` (il progetto vuoto creato perché era l'ultimo, nel formato di `GET /api/projects/{id}`, altrimenti null) |
| `POST /api/jobs/cleanup/preview` | sola lettura: `matched`, `bytes`, `bytes_complete`, `by_status`, `by_kind`, `protected` (risparmiati da stella o etichette), `resumable` (pipeline riprendibili risparmiate, 0 con `include_resumable`), `sample` (i 5 più vecchi che verrebbero eliminati: `id`, `kind`, `title`, `status`, `finished_at`), `as_of` (l'istante usato come «adesso», UTC al secondo nel formato `…+00:00`, da ripassare all'esecuzione confermata) |
| `POST /api/jobs/cleanup` | una chiamata a tempo: `deleted`, `skipped` (ripresi, in uso, già eliminati o non più corrispondenti ai criteri, per esempio per una stella aggiunta o perché terminati di nuovo dopo `as_of`: non è un errore), `errors` (eccezioni inattese su un job, contate senza interrompere), `freed_bytes`, `leftover`, `remaining` (corrispondenti non ancora esaminati) |
| `DELETE /api/jobs/{id}` | `deleted`, `freed_bytes`, `leftover` (0 o 1) |
| `POST /api/system/cleanup` · `GET /api/system/storage` | in più `jobs` (resti `.deleting-*` eliminati, i loro byte in `freed_bytes`) · `backups` (solo mostrata) |

Le due rotte della pulizia sono dichiarate dopo `/events` e prima di `/{job_id}`. Corpo comune (`CleanupIn`), **senza
valori predefiniti per ambito e stati**:

| Campo | Valori |
|---|---|
| `scope` | obbligatorio: `project` (un progetto) o `all` (tutti, compresi i job senza progetto o con un progetto che non esiste più) |
| `project_id` | obbligatorio con `project`, vietato con `all` (anche vuoto); progetto inesistente 404 |
| `statuses` | obbligatorio, sottoinsieme non vuoto di `completed`, `failed`, `cancelled` (duplicati tolti); uno stato attivo è un errore |
| `kinds` | null = tutti i tipi; altrimenti un elenco non vuoto di identificatori, non confrontati con i tipi registrati (righe vecchie con tipi non più registrati) |
| `older_than_days` | null = qualsiasi età; altrimenti un intero da 0 a 36 500 (booleani, testi e decimali rifiutati) |
| `keep_favorites`, `keep_tagged` | predefiniti true; solo `true` o `false` (`"no"`, `0` o null sono un errore, mai un «no») |
| `include_resumable` | predefinito false: escluse le pipeline fallite o annullate con almeno un elemento completato (riprendibili); solo `true` o `false` |
| `max_seconds` | solo `/cleanup`, predefinito 15 (anche con null); un numero si limita in silenzio a 1–25; booleani, testi, elenchi e valori non finiti sono un errore |
| `as_of` | facoltativo: l'`as_of` dell'anteprima confermata, un istante ISO 8601 con il fuso orario (anche con `Z`), portato in UTC e al secondo; senza fuso, illeggibile, non testo o più di un minuto nel futuro è un errore. Fa da «adesso» per l'età e nessun job terminato dopo corrisponde; null = adesso, senza limite superiore |

Tutti i campi di `CleanupIn` sono tipizzati largo (`Any`) e li valida `JobCleanup.criteria` (`max_seconds` lo valida
`run`): ogni valore non valido, anche null o di un altro tipo JSON (`scope` null, `statuses` come testo, `"no"` al
posto di `false`), è un 400 `invalid_cleanup` con il motivo in italiano, mai un 422 di Pydantic in inglese. Solo un
corpo che non è un oggetto JSON, o che manca, ha il 422 `validation_error` generico.

**Selezione.** Frammenti SQL sulle colonne di `jobs`, senza alias, così lo stesso testo vale per la selezione, i
conteggi e la condizione del DELETE (`only_if`): stella (`favorites`), etichette (`tags`), pipeline riprendibili
(`kind='pipeline'`, `failed` o `cancelled`, un elemento di `pipeline_items` `completed`) ed età
`julianday(COALESCE(finished_at, updated_at, created_at)) < julianday(?)`: in pratica la fine del job, altrimenti
l'ultimo aggiornamento (`updated_at` non è mai NULL); il limite è «adesso» meno N giorni, calcolato in Python (mai
`datetime('now')` di SQLite), e una data illeggibile (NULL per `julianday`) conserva il job. «Adesso» è `as_of` se il
corpo lo indica, altrimenti l'ora UTC, sempre al secondo; con `as_of` si aggiunge `julianday(COALESCE(finished_at,
updated_at, created_at)) < julianday(as_of)`, che vale per la selezione, per la condizione del DELETE e per i conteggi
di protetti e riprendibili. Il confronto è stretto perché le date sono al secondo: un job terminato nello stesso secondo
dell'anteprima, forse dopo la sua lettura, si risparmia (l'esito può dire un job in meno dell'anteprima) invece di
rischiare di eliminarlo senza averlo mostrato. I job che corrispondono ai filtri (stati, progetto, tipi, età, `as_of`) si dividono in tre
parti disgiunte: **da eliminare** (non protetti e, senza `include_resumable`, non riprendibili), **protetti** (stella o
etichette, secondo i `keep_*` attivi) e **riprendibili** risparmiati.

**Anteprima** (`preview`): una SELECT di `rowid, id, kind, title, status, finished_at, updated_at` (mai `params` né
`result`, grandi per le pipeline) in ordine di creazione; `matched`, `by_status`, `by_kind` e `sample` si ricavano in
Python, `protected` e `resumable` con due `COUNT(*)`; `as_of` è l'istante usato come «adesso». I byte si sommano con
`dir_size_until` entro `BYTES_SECONDS` (3 s): se il tempo scade `bytes_complete` è false e `bytes` è un minimo
(«almeno»). Le dimensioni misurate per intero si ricordano per (id, `updated_at`), che per un job terminato non cambia,
e si sommano sempre; il tempo va alle cartelle non ancora misurate, prima quelle mai provate, poi quelle «lente» (una
volta non finite in tempo), dalla provata meno di recente. Una cartella interrotta dal tempo conta con i byte letti
fin lì e passa in fondo; quelle non raggiunte contano 0 (ognuna delle due memorie tiene al più 50 000 voci, oltre si
svuotano entrambe). Ripetendo l'anteprima o l'uso del progetto diventa quindi esatta la dimensione di ogni cartella che
da sola si misura entro 3 s, perché a ogni chiamata almeno una cartella viene misurata per intero o passa in fondo; una
cartella che da sola richiede di più resta parziale a ogni chiamata e `bytes_complete` resta false.

**Esecuzione** (`run`): un ciclo a tempo senza stato, da ripetere finché `remaining` è 0. L'interfaccia ripassa a ogni
chiamata l'`as_of` dell'anteprima confermata: l'età si calcola da quell'istante e un job terminato dopo (anche mentre
il ciclo gira, o ripreso e terminato di nuovo) non corrisponde più, né nella SELECT né nel DELETE. Il limite è solo nel
tempo: un job con una data illeggibile o nel futuro può comparire in un'anteprima senza limite d'età ma non si elimina,
mentre stella ed etichette si rileggono a ogni DELETE (una tolta dopo l'anteprima rende eliminabile il job). Senza
`as_of` vale l'ora di ogni chiamata, senza limite superiore: è il caso dello svuotamento di un progetto da eliminare,
che deve essere completo.

```
scadenza = adesso + max_seconds (1–25); cursore = 0
finché non ci si ferma:
    righe = SELECT rowid, id FROM jobs WHERE rowid > cursore AND <da eliminare> ORDER BY rowid LIMIT 50
    nessuna riga → esaurito
    per ogni riga:
        arresto del server → stop (la riga non è esaminata: resta in remaining)
        cursore = rowid
        busy(id), esecuzione o ripresa in corso → skipped, senza l'attesa di 10 s
        delete(id, only_if=<da eliminare>) → deleted, freed_bytes, leftover
            Conflict o NotFound → skipped · altra eccezione → errors, stampata nel log del server
        tempo scaduto → stop (controllato dopo il tentativo: ogni chiamata esamina almeno un job)
remaining = 0 se esaurito, altrimenti COUNT dei job da eliminare con rowid > cursore
```

- **Terminazione del client:** il cursore sul `rowid` non riesamina un job nella stessa chiamata e i saltati non entrano
  in `remaining`, quindi un job non eliminabile non tiene vivo il ciclo; il client si ferma comunque dopo 2 risposte di
  fila senza eliminazioni.
- **Criteri rivalutati nel DELETE:** la condizione di selezione passa a `JobManager.delete` come `only_if`, valutata prima
  di toccare il disco e di nuovo nello stesso DELETE: una stella o un'etichetta aggiunte nel frattempo (`add_tag` e
  `set_favorite` non prendono il lock del `JobManager`), o una nuova fine del job dopo `as_of`, salvano il job, contato
  fra i saltati.
- **Byte:** `freed_bytes` conta solo i job eliminati senza eccezioni, senza i byte rimasti nelle cartelle `leftover`. Il
  lock del database non si tiene mai durante un'eliminazione e non ci sono elenchi `IN (…)` con migliaia di id.
- **Perché un ciclo a tempo e non un job in background:** un job di pulizia occuperebbe uno slot del pool dietro le
  pipeline lunghe, proprio quando serve spazio, comparirebbe nell'elenco che sta svuotando e lascerebbe riga e cartella
  proprie. Il ciclo è idempotente e sicuro se la scheda si chiude; due schede insieme si saltano a vicenda (un job già
  eliminato dall'altra è un saltato), senza un lock «una pulizia alla volta».

**Eliminazione di un job** (`JobManager.delete(job_id, *, only_if=None) -> Deleted(freed_bytes, leftover)`):

1. controlli di sempre: job inesistente 404, attivo 409, attesa (fino a 10 s) della chiusura dell'esecuzione o della
   preparazione di una ripresa;
2. il job si rilegge, perché durante l'attesa il lock è rilasciato: ripreso nel frattempo 409 («Il job è stato ripreso
   nel frattempo…»), sparito 404;
3. con `only_if`, `SELECT 1 … AND (<condizione>)`: se il job non corrisponde più, 409 «Il job non corrisponde più ai
   criteri della pulizia.» senza toccare il disco;
4. se la pulizia automatica sta togliendo un resto con lo stesso nome, 409 «I file del job si stanno già eliminando:
   riprova tra qualche secondo.»;
5. sotto il lock il nome entra in `_removing`. Se c'è `.deleting-<id>` e non `jobs/<id>`, la cartella del job è rimasta
   lì da un rinomino inverso non riuscito: vale come già spostata. Altrimenti un `.deleting-<id>` di un tentativo
   precedente si toglie e `os.replace(jobs/<id>, jobs/.deleting-<id>)`; se il rinomino non riesce, 409 «I file del job
   sono in uso (antivirus, Esplora risorse o un programma che li ha aperti): chiudili e riprova.» e il job resta intero;
6. `DELETE … WHERE id=? AND status IN (terminali) [AND (<condizione>)]`; se non elimina la riga il rinomino si annulla
   (fino a 3 tentativi, con pause di 0,05 e 0,1 s: un antivirus può bloccarlo per un istante), poi 404 (eliminato
   altrove) o 409 (non più corrispondente, oppure ripreso);
7. fuori dal lock: evento `deleted` con il progetto, chiusura del log (così una ripresa concorrente non la aspetta),
   misura e rimozione di `.deleting-<id>`, nome tolto da `_removing`, rimozione dello scratch del job.

`remove_tree` (`core/storage.py`) restituisce se non è rimasto nulla e non lascia mai uscire un errore
dell'eliminazione, neanche per un file singolo. Quando un file o una cartella non si elimina dà al proprietario i
permessi che servono (lettura e scrittura sul file; lettura, scrittura e accesso sulla cartella da eliminare e su
quella che lo contiene, se è dentro l'albero) e riprova una volta: i permessi si aggiungono e non si tolgono mai, i
collegamenti simbolici non si seguono e un'apertura o una lettura non riuscita si lascia com'è. Una cartella da cui
qualcosa non si è potuto togliere si riprova fino a 3 volte, con pause di 0,1 e 0,2 s.

`purge_tombstones` (pulizia automatica) toglie le cartelle `jobs/.deleting-<id>` che nessuna eliminazione sta
rimuovendo (stesso insieme `_removing`; controllo e prenotazione sotto il lock, atomici rispetto a `delete`), e solo se
la riga del job non c'è più. Se la riga c'è ancora (DELETE mancato e rinomino inverso non riuscito, o arresto brusco
fra il rinomino e il DELETE) la cartella è del job, anche con la stella: torna in `jobs/<id>` se quel nome è libero,
altrimenti resta dov'è con un messaggio nel log; mai eliminata. Anche una riga che non si riesce a verificare lascia la
cartella dov'è. Restituisce le cartelle tolte per intero e i byte liberati (anche quelli di una rimozione parziale); una
cartella sparita fra l'elenco e il lock non si conta. Nessuna euristica di età: le `.deleting-<id>` le creano solo
`delete` e `submit` (resto di un job mai accodato); `.rerun-incoming` e le cartelle `jobs/<id>`, con o senza riga, non
si toccano.

**Eliminazione di un progetto** (`delete_project`, chiamata da `Core.delete_project`):

```
progetto = projects.get(id)                                    400 identificatore non valido, 404
guardia: id già in eliminazione → 409 delete_in_progress, altrimenti lo si aggiunge
per al più 3 passate (PROJECT_PASSES):
    attivi = COUNT dei job del progetto in coda, in esecuzione o in annullamento (su tutto il progetto)
    attivi > 0 → 409 project_active_jobs, details {active, jobs_deleted}   (jobs_deleted 0 alla prima passata)
    budget = numero dei job del progetto all'inizio della passata
    finché budget > 0:
        ids = SELECT id FROM jobs WHERE project_id=? ORDER BY rowid LIMIT min(200, budget)   sempre dall'inizio, mai OFFSET
        per ogni id: arresto del server → 503 shutting_down · budget − 1 · delete(id):
            NotFound → avanti · Conflict → 409 project_busy, details {jobs_deleted}
    delete_if_unused(id) → eliminato: risposta
409 project_busy («Il progetto continua a ricevere nuovi job: …»)
alla fine, comunque vada: id tolto dalla guardia
```

`ProjectService.delete_if_unused` lavora in una sola transazione, che tiene il lock del database:
`DELETE FROM projects WHERE id=? AND NOT EXISTS (SELECT 1 FROM jobs WHERE project_id=?)`; se non elimina nulla, 404 se
il progetto non c'è più, altrimenti una nuova passata; se elimina l'ultimo progetto ne inserisce uno vuoto, «Nuovo
progetto» («Creato automaticamente: BioLoom richiede almeno un progetto.»), restituito come `replacement`. Sequenze e
documenti vanno via in cascata, etichette e stella con i trigger. `ensure_default` resta all'avvio.

- **Nulla eliminato con job attivi all'inizio:** il controllo SQL, su tutti i job del progetto, precede il primo
  DELETE, quindi un 409 `project_active_jobs` alla prima passata non ha eliminato nulla (`jobs_deleted` 0). Se invece
  un job inviato durante l'eliminazione è attivo alla passata successiva, l'eliminazione si ferma lì con lo stesso 409:
  i job già eliminati restano eliminati, il progetto resta, e `details.jobs_deleted` e il messaggio («(k job già
  eliminati)») dicono quanti.
- **Nessun job orfano:** la riga del progetto si cancella solo se nessun job la referenzia, e `submit` rifiuta i progetti
  inesistenti; entrambi sono statement singoli serializzati dal lock del database: o vince l'INSERT e il DELETE ritenta
  alla passata successiva, o vince il DELETE e l'INSERT riceve 404.
- **Sempre almeno un progetto:** due eliminazioni degli ultimi due progetti si serializzano nella transazione; la prima
  vede ancora l'altro progetto, la seconda nessuno e crea il sostituto: ne resta esattamente uno, mai zero né due.
- **Eliminazione parziale dichiarata:** un job in uso o ripreso interrompe con 409 `project_busy`; i job già eliminati
  restano eliminati, il progetto resta e il messaggio lo dice. Lo stato è coerente e la richiesta si può ripetere.
- **Nessun ciclo infinito:** ogni passata esamina al più i job che il progetto aveva al suo inizio, così una sorgente
  continua di job nuovi finisce in `project_busy` dopo 3 passate (o prima in `project_active_jobs`, se un job nuovo è
  ancora attivo all'inizio di una passata) invece di tenere occupata la richiesta.
- **Una richiesta per progetto:** una seconda eliminazione dello stesso progetto riceve subito 409 `delete_in_progress`,
  senza occupare un thread ad aspettare; progetti diversi non si aspettano a vicenda.

**Ordine dei lock:** guardia di `JobCleanup` → `JobManager._lock` → `Database._lock`, mai al contrario. La guardia
protegge solo l'appartenenza all'insieme dei progetti in eliminazione e non si tiene mai durante una chiamata ad altri
componenti; `JobManager.delete` non si chiama mai dentro `db.transaction()`.

**Errori:**

| Codice | Stato | Quando |
|---|---|---|
| `invalid_cleanup` | 400 | ambito mancante o non valido, `project_id` mancante con `project` o presente con `all`, stati vuoti, attivi («I job in coda o in esecuzione non si eliminano: annullali prima.») o sconosciuti, `kinds` vuoto o non valido, età non intera o fuori intervallo, sì/no diversi da `true`/`false`, `max_seconds` non numerico, `as_of` non valido o nel futuro; anche null o un altro tipo JSON |
| `invalid_identifier`, `not_found` | 400, 404 | id del progetto non valido o inesistente (uso, eliminazione, ambito della pulizia) |
| `project_active_jobs` | 409 | il progetto ha job in coda, in esecuzione o in annullamento: «… annullali prima di eliminarlo»; `details` `{active, jobs_deleted}`: alla prima passata nulla è stato eliminato (0), dopo (un job partito durante l'eliminazione) i job già eliminati restano tali e il messaggio lo dice |
| `project_busy` | 409 | un job del progetto è in uso o è stato ripreso nel frattempo, oppure il progetto continua a ricevere job (`details.jobs_deleted`) |
| `delete_in_progress` | 409 | un'altra eliminazione dello stesso progetto è in corso |
| `shutting_down` | 503 | arresto del server durante l'eliminazione di un progetto: il messaggio dice quanti job sono stati eliminati e che il progetto resta (l'interfaccia lo mostra come esito certo, non da verificare) |
| `conflict` | 409 | un singolo job (dettaglio): attivo, ancora in chiusura o ripreso, file in uso, resto ancora in rimozione; nella pulizia questi casi, e il job non più corrispondente, sono `skipped` |

**Coperto e non coperto.** L'eliminazione toglie le righe di `jobs`, le cartelle `jobs/<id>`, etichette e stelle
(trigger), gli elementi di pipeline (cascata), lo scratch del job e i resti `.deleting-*`. Restano fuori, con motivo:

- `jobs/.rerun-incoming` (svuotata all'avvio; il pacchetto in arrivo si toglie in `finally`); lo scratch (`recover`
  all'avvio e pulizia automatica); gli upload (globali, a età, senza legame con job o progetti: la conferma lo dice);
  `cache/compute` (testi, non percorsi; scade dopo 30 giorni); i template (nessun riferimento ai job); `backups/` (rete
  di sicurezza, solo mostrata nello spazio occupato);
- il file `bioloom.db` e il WAL non si restringono (nessun VACUUM automatico, vedi [Cartella dati](#cartella-dati));
- i job completati possono essere la sorgente di altri job (strutture `{type: "job"}`, consenso con `qmean_job` e
  `predict_job`): la conferma lo dice e i completati richiedono «Ho capito»;
- le cartelle `jobs/<id>` già senza riga restano dove sono: nessuna scansione, né automatica né manuale (con un database
  perso o ripristinato sembrerebbero rifiuti); i job con un `project_id` che non esiste più si raggiungono solo con
  l'ambito «Tutti i progetti»;
- `DELETE /api/projects/{id}` chiamata direttamente su un progetto con migliaia di job è una richiesta lunga, che in
  modalità server può superare il timeout di un proxy: l'interfaccia lo evita svuotando prima il progetto;
- il rinomino della cartella è provato sul filesystem Linux dei test; sul bind mount di Docker Desktop, su exFAT e con
  file tenuti aperti dall'host Windows il ripiego è il 409 «in uso» che lascia il job intero, da verificare sullo stack
  reale con una copia dei dati.

**Interfaccia** (logica in `lib/cleanup.ts`, pura e testata con chiamate e timer finti; i componenti la ricevono via
props):

- **Pagina Job:** «Pulisci job…» (cestino, accanto a «Riesegui da pacchetto») apre `JobCleanupDialog` con l'ambito e il
  tipo filtrati nella pagina; il filtro `?tag=` si ignora. A fine esecuzione, qualunque esito, si ricaricano elenco e
  conteggi dei progetti, e il job aperto si deseleziona solo se il suo stato risponde 404 (l'assenza dall'elenco,
  limitato a 200 e filtrato, non prova l'eliminazione). Il cestino del dettaglio (`aria-label` «Elimina il job») è
  protetto dal doppio clic e avvisa se è rimasto qualcosa (`leftover`).
- **Criteri** (`JobCleanupDialog`): ripartono dai valori sicuri a ogni apertura, senza `localStorage`: falliti e annullati
  (completati mai preselezionati), «oltre 7 giorni» (un job fallito da pochi minuti è probabilmente in diagnosi), stella
  ed etichette conservate, pipeline riprendibili escluse. Ambito «Questo progetto (nome)», disattivato senza un progetto
  attivo, o «Tutti i progetti»; `buildCleanupRequest` non costruisce alcuna richiesta senza stati o con «Questo progetto»
  senza un progetto noto: mai «tutti i progetti» al posto di un id sconosciuto. «Questo progetto» è il progetto attivo
  all'apertura del dialogo (`cleanupTarget`): se intanto l'attivo cambia (per esempio rileggendo i progetti al ritorno
  sulla scheda), l'ambito diventa non disponibile, «Questo progetto (nome, non più attivo)», con una nota che invita a
  riaprire il dialogo o a scegliere «Tutti i progetti», e non passa mai all'altro progetto. Con «Completati» un avviso
  (risultati usati da altri job) e la casella «Ho capito» obbligatoria, che si azzera a ogni cambio dell'ambito o del
  progetto (`cleanupTargetKey`, `resetUnderstood`).
- **Anteprima** (`makePreviewLoop`): 400 ms dopo l'ultimo cambio, una sola richiesta in volo con la più recente in coda,
  risposte di criteri superati scartate (`criteriaKey`); mentre si ricalcola resta l'ultima, attenuata (`aria-busy`).
  Mostra «42 job, 1,4 GB» («almeno» con i byte parziali), i conteggi per stato se gli stati sono più d'uno, le
  pipeline riprendibili escluse, i job conservati per stella o etichette e i 5 più vecchi. «Elimina N job (X MB)» è
  attivo solo con l'anteprima pronta per i criteri attuali e almeno un job (`canRun`).
- **Esecuzione** (`runCleanup`): la richiesta è quella dell'anteprima confermata con il suo `as_of`
  (`cleanupRunRequest`, ripassato a ogni chiamata: un job terminato dopo l'anteprima non si elimina); chiamate
  strettamente in sequenza con `max_seconds` 5; eliminati, byte e resti si sommano, saltati ed errori sono quelli
  dell'ultima chiamata (i saltati si ritrovano a ogni chiamata). Fine con `done` (`remaining` 0), `stopped`
  («Interrompi», fra una chiamata e l'altra), `stalled` (2 risposte di fila senza eliminazioni), `capped` (10 000
  chiamate) o `error` (totali parziali con il messaggio, mai un'eccezione): anche una risposta che non è un oggetto con
  conteggi numerici non negativi («Risposta del server non valida per la pulizia.», così un corpo vuoto non vale mai
  «finito») o un'eccezione di una callback. Barra accessibile (`Progress` con `label`) e «Eliminati 120 di 840 job ·
  liberati 1,4 GB» in una regione `aria-live`. Il riepilogo chiama i saltati «job saltati: in uso, ripresi, già
  eliminati altrove o non più corrispondenti ai criteri (per esempio per una stella o un'etichetta aggiunta nel
  frattempo)», con il tono di avviso: non è un errore, ma non tutto ciò che corrispondeva è stato eliminato. Durante
  l'esecuzione Esc, la X e lo sfondo chiedono l'interruzione invece di chiudere e il dialogo resta aperto fino al
  riepilogo; se si smonta (cambio di pagina) il ciclo si ferma alla chiamata successiva. Il dialogo esce sempre
  dall'esecuzione e `onCleaned` parte una sola volta, qualunque esito. «Interrompi» prende il posto della conferma e
  ignora il secondo clic di un doppio clic su «Elimina».
- **Dialogo «Progetti»** (`ProjectsDialog`): un cestino in ogni riga (`aria-label` «Elimina il progetto <nome>») apre la
  vista «Elimina il progetto» nello stesso `Modal` (niente dialoghi annidati: ogni `Modal` ascolta Esc sulla finestra, e
  un Esc li chiuderebbe tutti). La vista (`ProjectDeleteView`) mostra l'uso del progetto, letto all'apertura e dopo un
  rifiuto, mai a intervalli: nome, date, «3 sequenze, 1 documento, 120 job, 1,4 GB», che cosa va via per sempre, quanti
  job con la stella, con etichette e pipeline riprendibili sono compresi, che i file caricati non appartengono ai
  progetti e, sull'ultimo progetto, che ne verrà creato uno vuoto. Con job attivi il motivo e «Vai ai job», senza
  conferma; un progetto vuoto si conferma con un clic (focus su «Annulla»); altrimenti si scrive il nome
  (`nameMatches`: maiuscole, spazi in più e forme Unicode non contano). Il bottone distruttivo non ha mai il focus
  iniziale.
- **Eliminazione** (`deleteProjectFlow`): prima lo svuotamento con la pulizia a tempo (`drainRequest`: il progetto, tutti
  e tre gli stati terminali, stella, etichette e pipeline riprendibili comprese, qualsiasi età, senza `as_of`, quindi
  completo), interrompibile; poi `DELETE /api/projects/{id}`, che resta completo e autorevole (ricontrolla gli attivi
  ed elimina i job rimasti). «Interrompi» (o Esc, la X, lo sfondo, il cambio di pagina) durante lo svuotamento impedisce
  sempre il DELETE, anche quando l'ultima chiamata, che non si interrompe a metà, ha eliminato tutti i job: il progetto,
  le sue sequenze e i suoi documenti restano, e il dialogo lo dice. Esiti: eliminato (notifica con i job e i byte dello
  svuotamento e del DELETE, il sostituto e gli eventuali resti; elenco riletto e, se era il progetto attivo, ritorno a
  «/»: il provider sta fuori dal router); svuotamento incompleto o interrotto, 4xx o 503 `shutting_down` sul DELETE
  (esito certo, il progetto resta: messaggio nel dialogo, con i job già eliminati dallo svuotamento, uso riletto, nome
  da riscrivere); rete o altri 5xx sul DELETE (esito incerto, verificato rileggendo elenco e uso, mai una notifica di
  fallimento). Il dialogo esce sempre dallo stato «in corso», anche per un'eccezione inattesa, che si tratta come esito
  incerto. Notifiche, elenco e navigazione valgono anche a dialogo chiuso.
- **Impostazioni → Spazio e file temporanei:** il pulsante della pulizia automatica è «Pulisci file temporanei» (le
  azioni che eliminano risultati hanno altri nomi e stanno in un riquadro a parte) e la sua notifica conta i resti di
  job eliminati; il riquadro «Job e progetti» mostra lo spazio dei risultati dei job e «Elimina job…» (dialogo con
  «Tutti i progetti»); fra le voci dello spazio occupato «Copie di sicurezza del database».
- **Progetto attivo** (`ProjectProvider`, `lib/projectSelection.pickProjectId`): dopo ogni rilettura dell'elenco resta
  quello attuale se c'è ancora, altrimenti il primo, altrimenti nessuno (un elenco vuoto non lascia mai l'id eliminato);
  l'elenco si rilegge anche quando la scheda torna visibile, così un progetto eliminato in un'altra scheda non resta
  offerto. Il progetto attivo non si sincronizza fra le schede.

## Prestazioni

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

1. **Passi dell'analisi in parallelo** (`quality.analyze`, vedi [Validazione di qualità](#validazione-di-qualità)):
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

### Misure per i controlli preventivi

`test_preflight_calibration` (`scripts/test.sh backend tests/perf_timing.py -s -k preflight_calibration`, stesse condizioni
di sopra, con altri container di test attivi: i tempi assoluti sono circa il 3% più alti della tabella, per questo le stime
usano i rapporti con i valori della tabella). Mediana di 3 giri, varianti alternate nello stesso processo.

| Raffinamento (2 modelli, 76 residui) | `model.py` | Restrizioni + modello 1 | Modello successivo | Rapporto con fast (media dei 2 modelli) |
|---|---|---|---|---|
| very_fast (`a.very_fast()`) | 1,84 | 0,95 | 0,58 | 0,24 |
| fast (predefiniti di AutoModel: `autosched.normal`, 200 iterazioni, `refine.very_fast`) | 5,31 | 2,73 | 2,31 | 1 |
| normal (come fast, dinamica `refine.fast`) | 7,26 | 3,38 | 3,69 | 1,42 |
| thorough (`autosched.slow`, 300 iterazioni, `refine.slow`, ciclo ripetuto 2 volte) | 19,87 | 9,88 | 9,65 | 4,17 |

Il rapporto (`REFINEMENT_FACTOR`) è il tempo di `model.py` meno avvio e restrizioni (0,72 s), diviso per quello di fast:
la media dei due modelli. Sul solo modello successivo vale 0,25, 1,60 e 4,18; con la dinamica `refine.fast` il tempo
cambia di più da un modello all'altro (3,38 s il primo, restrizioni comprese, 3,69 il secondo).

**Perché questi livelli.** Prima della F8a "normal" (nessuna impostazione) e "fast" (`md_level =
refine.very_fast`) erano la stessa ottimizzazione: in MODELLER 10.8 `refine.very_fast` è già il predefinito di AutoModel
(sorgente di `modeller/automodel/automodel.py`, `set_defaults`). Una prova con MODELLER reale (1UBQ, 76 residui, 3
modelli, 2 giri) ha misurato per modello: predefiniti 2,20 s; programma VTFM `autosched.fast` 2,32 s; `autosched.fast`
con 100 iterazioni 2,24 s; `autosched.slow` con 300 iterazioni 2,52 s; dinamica `refine.fast` 3,63 s. Il programma
dell'ottimizzazione VTFM non cambia il tempo in modo misurabile, la dinamica sì: per questo "fast" resta il predefinito di
MODELLER (il livello proposto dall'interfaccia, che quindi non cambia) e "normal" ha la ricottura più lunga
`refine.fast`. "very_fast" è `a.very_fast()`, che non perturba le coordinate iniziali (`rand_method = None`): nella
stessa prova i modelli erano identici (stesso DOPE, RMSD 0 Å), e con la perturbazione rimessa erano diversi ma molto
peggiori (DOPE −6853 contro −7621) perché 50 iterazioni non bastano a recuperarla; BioLoom lascia il metodo di MODELLER
e avvisa (controlli preventivi e risultato) che i modelli sono uguali. "thorough" segue l'esempio del manuale senza
`max_molpdf = 1e6`, più basso del predefinito di MODELLER 10.8 (1e7). Tutti i livelli scrivono le impostazioni per
esteso nello script (`REFINEMENT_SCRIPT`), così non dipendono dai predefiniti di una versione futura.

| Bersaglio (1 modello fast) | `model.py` | Secondi per residuo |
|---|---|---|
| 76 residui (1 catena) | 2,93 | 0,0386 |
| 152 residui (2 catene) | 5,61 | 0,0369 |
| 304 residui (4 catene) | 11,25 | 0,0370 |

Bersagli a più catene di ubiquitina di lievito su copie di 1UBQ traslate di 40 Å (ogni catena con i contatti di una
proteina globulare, nessuno fra le catene): il tempo cresce in proporzione ai residui.

| Spazio (5 modelli fast, analisi estesa del migliore) | 76 residui | 304 residui |
|---|---|---|
| modelli PDB | 329 KiB | 1308 KiB |
| rapporto di qualità | 60 KiB | 209 KiB |
| altro (PROCHECK, log, allineamento, script) | 2714 KiB | 3344 KiB |
| **risultati** | **3103 KiB** | **4861 KiB** |
| intermedi di MODELLER (scratch, "Conserva file intermedi") | 855 KiB | 3168 KiB |

### Lunghezza della sequenza: tempi misurati

Misure del 2026-10-12 sul server christopher (Podman, 224 core) con `scripts/benchmark_lunghezza.py`, una lunghezza
per container: 1 modello con raffinamento `fast`, qualità estesa e QMEAN locale (UniRef30_2023_02) sul modello. Il
template è sintetico (catena di 3IU0 ripetuta e affiancata su una griglia), il bersaglio è la sua sequenza con il 5 % di
mutazioni: i tempi misurano la lunghezza, non la difficoltà del bersaglio. ProSA senza licenza valida (Podman senza
root), quindi quasi gratis.

| Residui | Modello + qualità | QMEAN | Memoria di BioLoom (RSS) |
|---|---|---|---|
| 500 | 50 s | 2,0 min | 156 MB |
| 1000 | 2,2 min | 2,4 min | 187 MB |
| 2000 | 7,4 min | 3,2 min | 255 MB |
| 3500 | 24 min | 5,0 min | 470 MB |
| 5000 | 55 min | 6,6 min | 801 MB |

- Il tempo di MODELLER cresce circa come L^1,7 (raddoppiare la lunghezza costa circa tre volte il tempo); l'analisi
  di qualità resta sotto i 2 minuti fino a 5000 residui (DOPE è il passo più lungo); HHblits in QMEAN occupa ~16,5 GB
  indipendentemente dalla lunghezza.
- 7500 e 9999 residui non sono stati misurati (prove interrotte): il massimo 9999 dell'impostazione viene dal formato
  PDB e non è verificato con i programmi di analisi.

## Frontend

React 19 + TypeScript + Vite, nessuna libreria UI esterna: componenti propri (`components/ui.tsx`), grafici SVG
(`components/charts/`, un file per grafico: `Ramachandran.tsx`, `residue.tsx` per i profili per residuo con
striscia di struttura secondaria, `ConsensusTracks.tsx`, `Heatmap.tsx`, `FeatureMap.tsx` per la mappa delle feature
UniProt, `StackBar.tsx`; tutti dentro la cornice comune `components/ChartFrame.tsx` con PNG, CSV e vista espansa),
viewer 3D 3Dmol.js caricato su richiesta con rilascio del contesto WebGL.
Il client API (`api/client.ts`) e i tipi (`api/types.ts`) sono l'unico punto di contatto con il backend. Le parti di
interfaccia delle singole funzioni sono descritte nelle loro sezioni (paragrafi **Interfaccia**).

| Cartella (`frontend/src/`) | Contenuto |
|---|---|
| radice | `main.tsx` (tema applicato prima del primo render), `App.tsx` (rotte e accesso con chiave), `styles.css` (variabili CSS e temi) |
| `api/` | `client.ts` (tutte le chiamate al backend) e `types.ts` (tipi delle risposte) |
| `pages/` | una pagina per rotta (tabella sotto), caricata su richiesta |
| `components/` | componenti condivisi (`ui`, `charts`, `Viewer3D`, `JobPanel`, `Pickers`, `AlignmentView`, `Layout`, `GlobalSearch`, `Labels`, `Preflight`, `ReportButton`, `ReproButton`, `FileLoad`, `ErrorBoundary`, `ProjectsDialog`, `JobCleanupDialog`, `TemplateUpload`…) e riquadri delle funzioni (`StructuralAnalysis`, `StructureTools`, `Refinement`, `Mutagenesis`, `Predictor`, `ComplexInput`, `ComplexResults`, `AssembliesCard`, `LigandPicker`, `LigandsPanel`, `CoverageAlerts`…) |
| `lib/` | logica pura con i test accanto (`*.test.ts`, per esempio `cleanup.ts` per la pulizia dei job e l'eliminazione dei progetti, `projectSelection.ts` per il progetto attivo, `templateUpload.ts` per la conferma della sostituzione dei template), `safeStorage.ts` (sessionStorage senza eccezioni), `clipboard.ts` (copia negli appunti con ripiego), `links.ts` (link esterni solo https), più gli hook (`hooks.ts`: `useJob`, `useVisibleInterval`) e i contesti di progetto e notifiche (`context.tsx`, con `useOptionalToast`) |
| `test/` | strumenti dei test di interfaccia (`dom.ts`: risposte finte del server al posto di `fetch`, visibilità della scheda); nessun componente lo importa |

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

**Navigazione, tema, scorciatoie e liste lunghe:**

- **Schermi stretti** (≤ 760 px): la barra laterale (pagine, tema, Tasti, Disconnetti, Esci da tutti i dispositivi,
  Esci) si apre come pannello sopra la pagina dal pulsante del menu nella barra in alto (`Layout`, classe `nav-open`) e
  si chiude scegliendo una pagina, con Esc, con la ✕ o toccando fuori; le griglie (`grid-2`…`grid-5`) passano a una
  colonna.
- **Tema** (`lib/theme.ts`): Scuro, Chiaro o Sistema nella barra laterale. "Sistema" segue `prefers-color-scheme` e
  cambia con il sistema operativo (evento `change` della media query); il tema si applica prima del primo render
  (`initialTheme` in `main.tsx`). La scelta resta in `bioloom.theme` come prima: i valori salvati "dark" e "light" valgono
  ancora, uno illeggibile vale il predefinito (scuro). Anche il visualizzatore 3D segue il tema (sotto).
- **Scorciatoie** (`lib/shortcuts.ts`, pannello «Tasti» o `?`): Ctrl+K / ⌘K anche dentro un campo; `/`, `?` e `g` più
  una lettera (`g s` Sequenze, `g j` Job, `g q` Qualità… entro 1,5 s) solo fuori dai campi di testo e senza dialoghi
  aperti; Esc chiude dialoghi e ricerca.
- **Liste lunghe** (`lib/virtual.ts`, `useVirtualRows`): oltre 200 righe si disegnano solo quelle visibili più 8 sopra e
  sotto, con due spaziatori che tengono l'altezza della lista; sotto la soglia tutto come prima. Usato dalla tabella dei
  residui in Qualità, dalle coppie in Confronto (righe alte 40 px, tutte uguali e misurate) e dalle righe di
  AlignmentView (righello, sequenze e consenso 19 px, spazio fra i blocchi 12 px; riquadro alto al più il 70% della
  finestra). La selezione dal 3D, dai grafici o dai tratti si porta in vista scorrendo solo il contenitore anche se la
  riga non è disegnata (`revealScrollTop`, poi `rowScrollDelta` sulla riga vera); lo "scroll anchoring" del browser è
  spento nel contenitore. Misurato con 304 residui: 20 righe disegnate invece di 304.

**Errori dell'interfaccia** (`components/ErrorBoundary.tsx`): un errore di disegno mostra la scheda «Si è verificato un
errore nell'interfaccia» (messaggio, dettagli tecnici, «Ricarica la pagina», «Riprova», «Copia i dettagli») invece
della pagina bianca.

- **Moduli obsoleti:** un import dinamico non riuscito (`dynamically imported module`, `Importing a module script
  failed`, `Failed to fetch`: i moduli di una build precedente dopo un aggiornamento) ricarica la pagina da sé al più una
  volta al minuto per scheda (`claimStaleReload`). Il segno `bioloom.chunk-reload` in sessionStorage conserva l'ora
  dell'ultima ricarica e non si cancella mai; un segno delle versioni precedenti («1») o illeggibile vale come assente.
- **Perché così:** prima il segno si toglieva in `render()`, e con i dati del sito bloccati già leggere `sessionStorage`
  lancia SecurityError, quindi pagina bianca. Toglierlo in `componentDidMount`/`componentDidUpdate` avrebbe creato un
  ciclo: il componente si monta e cancella il segno prima che il modulo a caricamento differito fallisca, e con un modulo
  mancante davvero la pagina si ricaricherebbe senza fine.
- **Senza sessionStorage** nessuna ricarica automatica, perché non si potrebbe ricordare di averla già fatta: resta il
  pulsante. `lib/safeStorage.ts` (`sessionGet`, `sessionSet`) legge e scrive dentro try/catch (lettura non riuscita =
  null, scrittura non riuscita = false), come `main.tsx` per localStorage.

**Tastiera, copia, aggiornamenti periodici e visualizzatore 3D:**

- **Tastiera:** `rowActivate` (Tab, poi Invio o Spazio; un Invio su un pulsante della riga resta al pulsante) e
  `aria-current` sulla riga scelta valgono anche per le righe di Editor PIR (documenti), Job, Modellazione (modelli; uno
  fallito non si sceglie), Sequenze (le due tabelle) e Libreria template; `DropZone` è un `role="button"` con lo stesso
  comportamento (apre la scelta del file). Le notifiche hanno il pulsante × (`aria-label` «Chiudi la notifica», `title`
  «Chiudi»), e il clic sulla notifica la chiude ancora. Il riquadro del consenso non cambia: ogni riga ha già «Mostra nel
  3D», e una riga attivabile avrebbe raddoppiato le fermate del Tab.
- **Durata delle notifiche** (`lib/context.tsx`): ogni notifica è un componente `Toast` con il proprio timer, che la
  chiude dopo `TOAST_MS` (4 s, 8 s gli errori) ma resta fermo mentre la notifica ha il puntatore sopra o il focus dentro
  (per esempio sul ×). Quando li perde riparte con il tempo che restava, mai meno di `TOAST_RESUME_MS` (2 s). Prima si
  chiudevano dopo 4 o 8 s anche con il focus. Limite: il timer usa `Date.now()` e non si ferma con la scheda nascosta.
- **Copia** (`lib/clipboard.copyText`, `CopyButton` in `components/ui.tsx`): Clipboard API e, se manca (contesto non
  sicuro: server in HTTP senza TLS) o viene rifiutata, una textarea temporanea fuori vista con
  `document.execCommand("copy")`, che i browser accettano durante un clic; focus e selezione tornano com'erano. Se non
  riesce nemmeno questo il pulsante mostra «Non copiato» per 4 s, con la spiegazione (`COPY_FAILED`) nel suggerimento e,
  dentro l'app, una notifica d'errore (`useOptionalToast`: fuori dal `ToastProvider` niente notifica e nessuna
  eccezione). Prima, senza la Clipboard API, il pulsante non faceva nulla.
- **Aggiornamenti periodici con la scheda nascosta** (`useVisibleInterval` in `lib/hooks.ts`): fermi mentre
  `document.visibilityState` è `hidden`, una chiamata subito al ritorno e poi di nuovo al loro ritmo; con `ms` null
  (polling spento, per esempio con il flusso in tempo reale attivo) il ritorno non chiama nulla, e la funzione chiamata è
  sempre l'ultima passata. Lo usano il contatore dei job di `Layout` (ogni 3 s) e le statistiche della Panoramica.
- **`Viewer3D`:**
  - il file della struttura passa da `api.fileText(url, signal)`: testo anche se il server dichiara JSON; cambiando file
    la richiesta precedente si annulla e la sua risposta non conta; una richiesta annullata non diventa «Server non
    raggiungibile»; con la sessione scaduta torna la schermata di accesso (evento `auth-required`) con il messaggio del
    server invece di «HTTP 401»; un file che non arriva dà «Struttura non disponibile: …»;
  - sfondo e comandi dal tema: variabili `--viewer-*` in `styles.css` (fondo `#0b1120` nel tema scuro, `#f2f4f8` nel
    chiaro), lette con `useDocumentTheme` e `viewerBackground` (`lib/theme.ts`). La tela cambia sfondo al cambio di tema
    senza ricaricare la struttura, e il PNG della vista e l'immagine del report prendono lo stesso fondo. Pulsanti,
    legenda e messaggi usano gli stessi token (testi ≥ 4,5:1, anello del focus ≥ 3:1 nei due temi);
  - grigio di contesto dei residui (`lib/colorscale.ts`): `CONTEXT_COLOR` `#e2e8f0` e, nel tema chiaro,
    `CONTEXT_COLOR_LIGHT` `#7c8797` (circa 3,3:1 sul fondo del 3D, 3,6:1 sotto la legenda, almeno 3:1 sui pannelli),
    scelto solo al momento del disegno con `themedColor` (`coloringMap(coloring, theme)`, colori per catena, legenda,
    strisce dei residui, `ScaleLegend`): le funzioni che preparano i colori restano indipendenti dal tema. Lo usano le
    ultime fasce del consenso, clash, interfaccia e, come `OUTSIDE_COLOR`, tasche e scansione di alanina; nel tema scuro
    cambia solo il grigio di clash e interfaccia (da `#cbd5e1` a `#e2e8f0`). Con `#e2e8f0` sul fondo chiaro il contrasto
    era circa 1,1:1;
  - altre varianti per il tema chiaro, nella stessa tabella `LIGHT_THEME_COLORS` (chiave: il colore del tema scuro) e
    misurate sul fondo del 3D: riferimento A del Confronto, `REFERENCE_COLOR` `#cbd5e1` → `REFERENCE_COLOR_LIGHT`
    `#1e293b` (grigio ardesia scuro, 13,3:1; ora definito in `colorscale.ts` e riesportato da `lib/compare.ts`); «1
    metodo» del consenso, `ONE_METHOD_COLOR` `#fde047` → `#666f12` (giallo oliva scuro, 5,0:1); centri delle tasche,
    `MARKER_COLOR` `#ffffff` → `#1e293b` (13,3:1; `CENTER_COLOR` in `lib/pockets.ts`). Il tema scuro non cambia. A resta
    un grigio come «B senza coppia» (`#6b7280`) ma molto più scuro (circa 3:1 fra i due): la differenza è di luminosità e
    resta con la deuteranopia e la protanopia; un viola, con la deuteranopia, sembrerebbe il blu delle coppie coincidenti
    della scala delle distanze. Un giallo scuro (ocra, oro) con la deuteranopia diventa quasi uguale al rosso di «3 o
    più metodi»; l'oliva è la tinta che se ne allontana di più;
  - `drawColor(scale, value, theme, fixed?)` applica la variante solo ai colori fissi (`ResidueValue.color`), alle fasce
    e alle categorie; i gradienti delle scale continue restano quelli della scala, altrimenti la conservazione a 0,5,
    bianca come i centri delle tasche, verrebbe scurita. La usano `coloringMap` (`residueColoring.ts`) e la striscia per
    residuo (`charts/residue.tsx`); `ConsensusTracks` colora barre e legenda con `themedColor`, quindi «1 metodo» è
    oliva anche nel grafico del consenso. In `Viewer3D` sfere e linee prendono il colore del tema al momento del disegno
    e si ridisegnano quando il tema cambia;
  - contorno nel tema chiaro (`VIEWER_VIEW_STYLE` in `lib/theme.ts`, `setViewStyle` di 3Dmol): `outline` `#1e293b`,
    0,5 Å, al più 1,5 px, applicato da `Viewer3D` alla creazione e a ogni cambio di tema; nel tema scuro nessun
    contorno. Stacca dal fondo i colori chiari che restano tali: i gradienti delle scale continue (centro bianco di
    blu-bianco-rosso, del potenziale e della conservazione, giallo di viridis e della scala delle distanze), gli schemi di
    3Dmol (B-factor, struttura secondaria, catene) e le fasce e categorie chiare senza variante. Scurire quei gradienti
    ne cambierebbe la lettura: il centro neutro di una scala divergente sembrerebbe un estremo, viridis perderebbe la
    luminosità crescente. Il contorno compare anche nel PNG della vista e nell'immagine del report. Valori scelti con una
    pagina statica fuori dall'app (3Dmol 2.5.5, 1UBQ): con 0,1 Å il contorno spariva a struttura intera, con un massimo di
    2 px riempiva i vuoti dei tratteggi;
  - legenda del 3D nel tema chiaro (`styles.css`): campioni e barra con un bordo `--chart-axis` (almeno 3:1);
  - limiti noti nel tema chiaro: restano sotto 3:1 sul fondo, aiutati solo dal contorno, «2 metodi» (`#fb923c`, circa
    2,1:1), «1 clash» (`#f59e0b`), l'interfaccia (`#facc15`), l'ambra `#fbbf24` di struttura secondaria e Ramachandran,
    i colori pLDDT `#FFDB13` e `#65CBF3`, le tasche `#F0E442`, `#56B4E9` ed `#E69F00`, il coil `#94a3b8` e il centro
    `#fee090` della scala delle distanze. Con la deuteranopia «1 metodo» dista 12 (ΔE CIE76) da «3 o più», contro circa
    20 della coppia più vicina nel tema scuro: per fare meglio servirebbe una scala del consenso per il tema chiaro con
    «3 o più» molto più scuro, ma `#dc2626` è condiviso con la scala dei clash. Contorno e colori nuovi non sono stati
    provati dentro BioLoom avviato; nella pagina di prova le sfere semitrasparenti sembravano senza contorno, e a
    struttura intera le linee di coordinazione tratteggiate sembrano quasi continue. Il grigio di contesto è vicino a
    «senza valore» (`#6b7280`): oggi non compaiono insieme in consenso, clash, tasche e scansione, ma potrebbero nella
    vista dell'interfaccia con valori mancanti.
- **Client** (`api/client.ts`): gli id di progetti, sequenze, documenti, job e pipeline nei percorsi passano da `enc()`
  (`encodeURIComponent`), come già template e file; `request()` accetta un `AbortSignal` e la lettura come testo; un 429
  (`rate_limited`) è un errore come gli altri, con il messaggio e l'attesa del server; `logoutAll()` chiama
  `POST /api/auth/logout-all`.
- **Link esterni:** il rapporto di SWISS-MODEL in Qualità è un link solo se l'indirizzo è `https://` con un host
  (`lib/links.httpsUrl`), come lo filtra già il backend (`remote.https_url`).
- **Letture superate:** in Modellazione la lettura delle sequenze del progetto ha `.catch` con la notifica d'errore e,
  come quella del documento PIR, un flag di annullamento: una risposta arrivata dopo il cambio di progetto o di
  documento non sostituisce i dati nuovi.
- **Download automatico dei template** (`TemplatePicker`): un codice PDB o un id `AF-…` che la libreria non ha (404) si
  scarica dopo `AUTO_FETCH_DELAY_MS` (600 ms) e solo se è ancora scelto; ogni lettura ha un numero di tentativo, quindi
  un template tolto (o rimesso) o un selettore smontato non scrivono nulla, e un template tolto durante l'attesa si
  rilegge da capo se torna.
- **Esci da tutti i dispositivi** (`Layout`, solo con `runtime.auth`): accanto a «Disconnetti», con la conferma
  «Chiudere tutte le sessioni aperte con la chiave d'accesso, su ogni computer e browser, compresa questa? …»; poi la
  notifica «Tutte le sessioni sono state chiuse: inserisci di nuovo la chiave d'accesso.» e la schermata di accesso
  (`notifyAuthRequired`: il cookie è già cancellato). Un errore resta una notifica con il messaggio del server, e la
  sessione non cambia.
- **Impostazioni:** l'interprete di MODELLER (campo presente solo in esecuzione nativa, `!container`) è in sola lettura
  in modalità server, con la nota «In modalità server lo imposta l'amministratore sul server (variabile d'ambiente
  BIOLOOM_MODELLER_PYTHON); vuoto = rilevamento automatico.»: la variabile e non `server.env`, che conta solo nei
  container (lo passano `compose.yml` e `bioloom-podman.sh`), dove il campo non c'è. Nella scheda Spazio, accanto alla
  cache dei calcoli, «Spazio massimo della cache dei calcoli (MB)» (da 100 a 102400, predefinito 2048); il campo è
  obbligatorio nel tipo `Settings`.

**ESLint** (`npm run lint`, configurazione flat in `eslint.config.js`):

- **Regole:** per `src/**/*.{ts,tsx}` quelle raccomandate di JavaScript e di typescript-eslint, senza informazioni sui
  tipi per restare veloce (i tipi li controlla `tsc --noEmit`), e di eslint-plugin-react-hooks 7 solo `rules-of-hooks`
  ed `exhaustive-deps`: il preset `recommended` aggiunge le regole del React Compiler (purity, refs,
  set-state-in-effect…), che il progetto non usa. I `*.config.{js,ts}` hanno le stesse regole con i globali di Node.
  Unico ritocco: `no-unused-vars` con `ignoreRestSiblings`, per `{ as_of: _ignored, ...base }`, accettato anche da tsc.
- **`exhaustive-deps` è un errore**, non un avviso: una dipendenza mancante è di solito una closure obsoleta, e ogni
  esclusione voluta si dichiara con il suo motivo dopo «--»
  (`// eslint-disable-next-line react-hooks/exhaustive-deps -- <motivo>`). Con `reportUnusedDisableDirectives: "error"`
  anche un commento di esclusione che non serve più è un errore, quindi `--max-warnings 0` non serve.
- **Esclusioni:** 50, tutte con il motivo (49 `exhaustive-deps`, fra cui il layout effect senza dipendenze di
  `lib/virtual.ts`, che misura la pagina dopo ogni disegno, e una per i tipi di 3Dmol in `Viewer3D`). Delle 61 di prima,
  nessuna con un motivo, 12 sono state tolte completando le dipendenze senza cambiare comportamento o perché la regola
  non scattava più.
- **Difetti trovati:** l'Editor PIR → Genera allineamento prendeva template, catene e nome del bersaglio dal modulo
  quando il job finiva, quindi da valori cambiati nel frattempo: ora li prende dal job (`job.result.templates`,
  `job.params.target.name`). Minori: `templatesMeta ?? []` in `PirWorkspace` rendeva inutile un `useMemo`; una
  dipendenza superflua in `Modeling`.
- eslint-plugin-react-refresh è stato scartato: serve solo all'aggiornamento a caldo di `npm run dev` e avrebbe chiesto
  di spostare funzioni in altri file (16 casi in 11 file).

Lo eseguono `scripts/test.sh frontend` e il job frontend della CI.

Lo stato di navigazione (job aperto, sequenza, template) vive nell'URL: ogni risultato ha un link stabile.

| Pagina (`pages/`) | Funzione |
|---|---|
| Panoramica (`/`) | flusso guidato, esempi, motori disponibili, risorse del computer; stella ed etichette del progetto attivo |
| Nuovo modello guidato (`/wizard`) | sequenza → template → allineamento → opzioni → avvio → risultati, con i controlli preventivi prima di ogni calcolo e lo stato nell'URL e nel browser (vedi [Procedura guidata](#procedura-guidata)); Modellazione e Pipeline hanno gli stessi controlli sopra il pulsante di avvio |
| Sequenze (`/sequences`) | import FASTA/UniProt, proprietà ProtParam, predizione GOR, annotazioni UniProt/InterPro/PDB; mappa di disordine (FoldIndex), bassa complessità (SEG), domini InterPro e GOR; stella ed etichette (`?id=` sceglie la sequenza) |
| Ricerca template (`/search`) | RCSB/NCBI/locale/AlphaFold DB (accessione UniProt facoltativa, precompilata dalle sequenze UniProt), tabella ordinabile (predefinito: punteggio), colonna Punteggio con fascia e motivazioni (suggerimento; per componente nella riga espansa, sopra l'allineamento dell'hit), riquadro dei template consigliati (principale e complementari, esclusi con il motivo) con «Usa i consigliati per la modellazione» (`lib/templateRank.modelingLink`: un `t=ID:CATENA` per template), righe consigliate evidenziate, download, "modella con"; badge "modello predetto" e pLDDT; nota "dalla cache (data)" e ricalcolo ignorando la cache; per ogni hit gli avvisi su regioni disordinate e domini fuori dal tratto allineato |
| Libreria template (`/templates`) | download RCSB e "Da AlphaFold DB (UniProt)", upload PDB/mmCIF (con «Sostituisci» per un id già in libreria, vedi [Libreria dei template](#libreria-dei-template-caricamento-e-sostituzione)), catene, residui mancanti, ligandi classificati (metallo, cofattore noto, additivo, altro ligando, residuo modificato), DSSP, 3D; badge "AlphaFold", pLDDT medio e fasce di confidenza; assemblaggi biologici (REMARK 350, `pdbx_struct_assembly`) con lo stato oligomerico e «Importa come template» (`<ID>_ASM<n>`), badge "assemblaggio"; stella, etichette e filtro (`?tag=`), preferiti in cima |
| Allineamenti (`/alignment`) | pairwise, multiplo, strutturale (con il confronto per residuo e «Apri in Confronto»); esportazione FASTA/Clustal/PIR; salvataggio nel progetto; cache per multiplo e strutturale |
| Editor PIR (`/pir`) | documenti PIR del progetto, generazione con MODELLER, validazione in tempo reale |
| Modellazione (`/modeling`) | input automatico, da PIR o «Complesso» (template o assemblaggio, una sequenza per catena anche «stessa della catena …», simmetria, anteprima dell'allineamento per catena; nei risultati confronto delle interfacce con DockQ e 3D colorato per catena e interfaccia), parametri, vincoli, loop; soglia pLDDT con un template AlphaFold (anche nella Pipeline); avvisi prima del lancio su regioni disordinate e domini non coperti dai template, con suggerimento; modelli, 3D, PyMOL, report HTML (con la vista 3D del modello selezionato), archivio; «Confronta…» di ogni modello con un template usato o con un altro modello del job; «Trasferisci ligandi, cofattori e metalli dai template» con la scelta dei gruppi (additivi e ioni del tampone deselezionati per default, `LigandPicker`, che usa le analisi già lette dal selettore dei template anche dopo un download; una lettura non riuscita si mostra con «Riprova», un 404 di un template in download si riprova da sé, e sotto «Avvia modellazione» si dice quale template blocca) e la verifica del modello migliore; «Rifinisci» (OpenMM) per ogni modello e per una struttura qualunque, con i risultati prima/dopo; «Mutagenesi» (mutazioni, varianti di UniProt, scansione di alanina) per ogni modello e per una struttura qualunque, con i risultati del mutante o della scansione; «Confronta con una predizione (ESMFold)» per il modello scelto (F9.3) |
| Qualità (`/quality`) | voto, consenso tra i metodi (discordanze, regioni problematiche, disordine predetto come segnale informativo), Ramachandran, profili, clash, 3D colorato per valore per residuo con selezione bidirezionale, confronto analisi; report HTML (con QMEAN calcolato nella pagina e, dalla scheda 3D, la vista corrente); «Confronta con» il template del modello o un'altra struttura; sezione Ligandi e metalli (esiti, coordinazione, confronto con il sito del template): il clic su un gruppo lo mostra nel 3D con i residui coordinanti e le linee di coordinazione (`LigandsPanel`, `lib/ligands.ts`; ioni metallici sempre a sfere); scheda Interazioni (analisi strutturale: legami H, ponti salini, disolfuri, aromatiche, interfacce, conservazione; `?ajob=`); schede Tasche (fpocket, `?pjob=`) ed Elettrostatica (PDB2PQR + APBS, `?ejob=`); scheda Mutagenesi (mutazioni e scansione di alanina della struttura del rapporto, cronologia); «Confronta con una predizione (ESMFold)» con l'esito motivato e il confronto per residuo, incluso nel consenso e nel report (`?prjob=`, F9.3) |
| Confronto (`/compare`) | `?a=&ca=&b=&cb=&job=`: B sovrapposta ad A (riferimento), metriche (TM-score su A e su B, RMSD, RMSD entro 5 Å, GDT-TS, GDT-HA, lDDT, lunghezza allineata), 3D con B colorata per distanza (0 → ≥ 5 Å) o lDDT e A in grigio (ardesia scura nel tema chiaro), grafico di distanza e lDDT per posizione, tratti che deviano, tabella per coppia; clic ovunque → coppia evidenziata nel 3D |
| Pipeline batch (`/pipeline`) | multi-FASTA → modelli validati, template da ricerca automatica, «Più template complementari» (N da 2 a 5) o fissi (casella «Ligandi» del template solo se la scelta predefinita trasferisce qualcosa dalle catene scelte, con i gruppi esclusi nel suggerimento: `lib/ligands.defaultTransfer`); filtro zona crepuscolare, tabella paginata (template usati elencati, motivazioni nel suggerimento), CSV/ZIP; ricerche riusate dalla cache; ripresa (tutti gli elementi non conclusi o solo i falliti); avanzamento, tempo residuo e tabella aggiornati in tempo reale |
| Job (`/jobs`) | log e file di ogni calcolo; ripresa delle pipeline fallite, annullate o completate con elementi falliti; elenco e dettaglio in tempo reale, con il tempo residuo dei job in esecuzione e la posizione di quelli in coda ("2° in coda"); stella, etichette e filtro per etichetta o preferiti (`?tag=`); «Pacchetto riproducibile» e «Riesegui da pacchetto»; eliminazione di un job dal dettaglio e «Pulisci job…» per i job terminati in blocco, con criteri, anteprima e conferma (vedi [Eliminazione di progetti e pulizia dei job](#eliminazione-di-progetti-e-pulizia-dei-job)) |
| Riesegui da pacchetto (`/rerun`) | caricamento e verifica di un pacchetto, anteprima del manifest con le versioni, controlli preventivi, avvio, rapporto del confronto; `?job=` apre una riesecuzione (vedi [Riproducibilità](#riproducibilità-pacchetti-e-riesecuzione)) |
| Impostazioni (`/settings`) | schede Generale (esecuzione, QMEAN, email, consenso e indirizzo di ESMFold), Licenze (MODELLER, ProSA, PROCHECK), Database (pdbaa, MMseqs2, UniClust30), Strumenti (verifica dell'installazione), Spazio e file temporanei (spazio occupato con le copie di sicurezza del database, «Pulisci file temporanei», cache dei calcoli con il suo spazio massimo, file intermedi di MODELLER, riquadro «Job e progetti» con «Elimina job…») |
| Login (`Login.tsx`, senza rotta) | solo in modalità server, quando l'API risponde 401 (anche dal visualizzatore 3D e dopo «Esci da tutti i dispositivi»); un 429 del login mostra il messaggio del server con l'attesa |

## Sicurezza e privacy

**Protezioni.**

- **Porta:** pubblicata solo su `127.0.0.1`, in locale e, per default, anche sul server (`BIOLOOM_BIND` di
  `server.env.example`), dove gli altri computer arrivano con un reverse proxy HTTPS o un tunnel SSH. `0.0.0.0` espone
  HTTP in chiaro e, con Docker, una porta che scavalca il firewall: l'avviatore lo segnala a ogni avvio (vedi
  [Avviatori](#avviatori-un-bioloom-alla-volta-e-controlli-allavvio)).
- **Server:** con `BIOLOOM_AUTH=on` tutte le API (e `/docs`) richiedono la chiave d'accesso, tramite
  cookie HttpOnly firmato o header Bearer, con il freno ai tentativi anche per la chiave come Bearer (sotto,
  **Accesso con chiave**); il middleware controlla anche le rotte di streaming prima che il flusso parta.
- **Richieste di altri siti (CSRF):** la porta su `127.0.0.1` non basta, perché il browser che apre BioLoom apre anche
  altri siti, e un `fetch` con `mode: "no-cors"` o un form arrivano senza preflight CORS (arresto, aggiornamento dei
  database, upload). `core/guard`, il middleware più esterno (rifiuta prima della chiave d'accesso), controlla in
  entrambe le modalità le richieste che modificano (tutti i metodi tranne GET, HEAD e OPTIONS), in quest'ordine:
  `Origin` dello sviluppo con Vite (`dev_origins` di `create_app`, le stesse di CORS; solo fuori dal container,
  dove `BIOLOOM_CONTAINER=1` le spegne) → ammessa; con `Sec-Fetch-Site` passano solo `same-origin` e `none` (punto di
  vista del browser: vale anche dietro un reverse proxy); altrimenti con `Origin` servono nome e porta dell'header
  `Host` o del primo `X-Forwarded-Host`, con la porta predefinita dello schema se manca (`null` mai); senza nessuno
  dei due (curl, script, avviatori, test) la richiesta passa. Rifiuto: 403 `cross_origin`. Il frontend usa percorsi
  relativi, quindi la stessa origine, anche con `npm run dev` (il proxy di Vite conserva l'header `Host`).
- **DNS rebinding:** senza accesso protetto un dominio ostile risolto su 127.0.0.1 diventerebbe «stessa origine» e
  leggerebbe tutta l'API. `core/guard` ammette solo l'header `Host` con nome (la porta non conta: il proxy di Vite
  inoltra `localhost:5173`) `127.0.0.1`, `localhost`, `::1` o in `BIOLOOM_ALLOWED_HOSTS` (virgole o spazi, senza
  maiuscole, anche come URL; `*` = tutti; letta all'avvio, come `BIOLOOM_AUTH`), altrimenti 400 `host_not_allowed`;
  `Host` assente (client non browser) e `/api/health` passano. Con `BIOLOOM_AUTH=on` il controllo è spento:
  proteggono già chiave e cookie, e il server si raggiunge per IP, nome o reverse proxy.
- **Iframe (clickjacking):** una pagina di BioLoom in un iframe trasparente di un altro sito invierebbe le richieste
  come stessa origine e passerebbe il controllo dell'origine (due clic su «Arresta»). `core/guard` aggiunge a ogni
  risposta, anche ai rifiuti, `X-Frame-Options: DENY` e `Content-Security-Policy: frame-ancestors 'none'`, salvo una
  CSP con `frame-ancestors` già presente (report HTML). Il frontend non mette mai in un iframe le proprie pagine.
- **Cookie dietro un reverse proxy:** il cookie di sessione è `Secure` se la richiesta arriva in HTTPS. Vale anche
  quando un proxy TLS lo dichiara con `X-Forwarded-Proto: https` (`api/auth._https`). uvicorn si fida degli header
  inoltrati solo da 127.0.0.1, e il proxy raggiunge il container dalla rete di Docker. L'header conta solo per
  questo flag: se falso, rende il cookie più restrittivo. Gli header inoltrati non sono fidati in generale:
  `X-Forwarded-For` conta solo per il freno ai tentativi e solo dai proxy di `BIOLOOM_TRUSTED_PROXIES` (sotto),
  altrimenti chiunque lo scriverebbe per aggirarlo.
- **Arresto dall'interfaccia:** solo con `BIOLOOM_ALLOW_SHUTDOWN=1` (preset locale).
- **Container:** gira senza root (`setpriv` all'utente proprietario di `/data`). QMEAN non ha accesso al
  socket di Docker e usa i database in sola lettura.
- **Licenze:** in `data/licenses` con permessi 0600, mascherate nelle risposte API, mai nelle immagini. Il
  messaggio d'errore di MODELLER, che ripete la chiave, viene mascherato ovunque arrivi: risposte delle licenze,
  `/api/system/info`, verifica dell'installazione, log, output ed errori dei processi dei job (vedi
  [Licenze](#licenze-dei-programmi-esterni)).
- **Segreti e programmi esterni:** i programmi esterni non ricevono `BIOLOOM_ACCESS_KEY`, `MODELLER_KEY`,
  `KEY_MODELLER` né le `BIOLOOM_SMTP_*` (vedi [Job](#job)); ricevono `BIOLOOM_MODELLER_KEY`, la chiave MODELLER attiva,
  anche quelli diversi da MODELLER.
- **Errori interni** (`app.unexpected`): al client 500 `internal_error` con «Errore interno (codice xxxxxxxx): i dettagli
  sono nel log di BioLoom.» (`secrets.token_hex(4)`); nel log «[BioLoom] Errore interno (codice xxxxxxxx) su METODO
  percorso:» e il traceback. Prima il messaggio conteneva `str(exc)`, con percorsi, parametri o pezzi di file. Limite:
  uvicorn stampa anche il suo traceback, senza codice, quindi nel log l'errore compare due volte. Le due tracce
  superano insieme le 60 righe di `compose logs --tail 60` (tasto **L** degli avviatori: misurato con uvicorn, riga del
  codice alla 6, fine della traccia di uvicorn alla 179), quindi con L la riga del codice resta fuori: la Guida rimanda
  a `log` (200 righe) e a `docker logs … | grep -A 150 <codice>`.
- **Percorsi:** passano tutti da `safe_join` e da identificatori validati; nessun endpoint di navigazione
  del filesystem. `safe_join` (`core/storage`) risolve il percorso e lo accetta se resta dentro la radice:
  - **cartelle in eliminazione su Windows:** `Path.resolve()` di una cartella che un altro thread sta eliminando (lo
    scratch a fine job) restituisce `\\?\C:\…` o `C:\$Extend\$Deleted\…`, fuori dalla radice anche per un id valido.
    Prima `GET /api/jobs/{id}` (e le altre rotte che passano da `workdir` o `live_dir`) rispondeva per un istante 400
    `unsafe_path`: 3094 rifiuti su 20.000 chiamate in una prova ripetuta. Ora, quando la risoluzione completa esce dalla
    radice, si risale fino alla prima cartella madre che si risolve dentro la radice e si riaggiungono i nomi restanti
    così come sono, anche per i percorsi annidati (`id/sub/file`);
  - **sempre rifiutati:** i percorsi che, risolti, escono dalla radice: `..` che risale oltre la radice, percorsi
    assoluti esterni, link simbolici e giunzioni verso fuori. Nel caso normale, con la risoluzione dentro la radice, vale
    il percorso risolto, come prima: `a/../b` diventa `b`, un nome vuoto la radice stessa, `x.` resta `x.`, e un link o
    una giunzione che porta dentro la radice si segue (`test_safe_join_rejects_links_leaving_the_root`);
  - **in più nel ripiego** (risoluzione completa fuori dalla radice): `..`, nomi vuoti o di soli punti e spazi, madri
    fuori dalla radice, ogni link simbolico o giunzione fra i nomi riaggiunti (`os.lstat` con l'attributo di reparse,
    perché `os.path.islink` non vede le giunzioni) e i nomi che Windows riscriverebbe (il percorso finale deve coincidere
    con `os.path.abspath`: dispositivi come `CON`, punti o spazi finali). Su Linux un percorso esce dalla radice solo con
    `..`, percorsi assoluti o link, che il ripiego rifiuta: le garanzie sono quelle di prima;
  - **limite:** se è la radice stessa a essere in eliminazione, si risolve ancora nella forma strana; il risultato resta
    confinato lì dentro e il file non esiste, quindi la risposta è 404 invece di 400.
- **Upload:** limitati (50 MB, `BIOLOOM_MAX_UPLOAD_MB`; 200 MB per i file di sequenze e allineamenti, 1 GB
  decompressi, al più 100.000 sequenze lette; 256 MB per un pacchetto di riproducibilità; 64 kB per `LicenseDB`) e validati come strutture prima
  dell'uso. Oltre il limite dell'endpoint `read_upload` risponde 413 `too_large` («File troppo grande: massimo N MB.»;
  prima il codice era `bad_request`). Gli archivi pdbaa si estraggono con filtro `data` e solo file senza percorso.
- **Limite del corpo** (`core/bodylimit.BodyLimit`, middleware ASGI puro come `core/guard`): i limiti degli endpoint
  valgono quando l'endpoint legge il file, dopo che Starlette ha già scritto tutto il multipart nella cartella
  temporanea, quindi un corpo di qualche GB la riempirebbe prima del 413. Il middleware rifiuta le richieste oltre
  `request_limit()`, il più grande dei caricamenti legittimi più 1 MB per confini e campi del multipart
  (`max(MAX_UPLOAD_BYTES, MAX_PACKAGE_BYTES, MAX_BYTES delle sequenze) + MULTIPART_MARGIN`: 257 MB con le impostazioni
  predefinite, di più se `BIOLOOM_MAX_UPLOAD_MB` supera 256), così un caricamento accettato dall'endpoint non si ferma
  mai qui.
  - Con `Content-Length` oltre il limite: 413 subito, senza leggere un byte del corpo (misurato con uvicorn: 10 GB
    dichiarati → 413 in 0,01 s).
  - Senza (corpo chunked): conta i byte che l'app legge e oltre il limite smette di passarli e sostituisce con il 413
    la risposta dell'app (FastAPI darebbe 400 al corpo interrotto).
  - Risposta: 413 `too_large` «Richiesta troppo grande: BioLoom accetta al massimo 257 MB per richiesta.», con
    `Connection: close`, perché il resto del corpo non si legge.
  - Ordine dei middleware, dal più esterno: `core/guard` → chiave d'accesso → CORS → `BodyLimit` → rotte. Richieste di
    altri siti e senza chiave si rifiutano prima, e nessuno dei due controlli legge il corpo; dentro CORS il 413 resta
    leggibile dall'interfaccia di Vite in sviluppo; fra il `receive` contato e le rotte non c'è nessun
    `BaseHTTPMiddleware`.
  - Limiti noti: se il server chiude mentre il client sta ancora inviando, il client può vedere la connessione chiusa
    (reset) invece del 413 (con uvicorn reale, in chunked, succede), quindi conviene che l'interfaccia controlli la
    dimensione prima dell'invio; il limite è unico per l'app, quindi un file da 200 MB a un endpoint da 50 MB arriva
    ancora nella cartella temporanea prima del 413 dell'endpoint (un limite per percorso sarebbe più stretto).
- **Lavoro sincrono fuori dal loop:** gli endpoint `async` che ricevono un file (`/api/files/sequences`,
  `/api/files/structure`, `/api/quality/upload`, `/api/templates/upload`, `/api/licenses/prosa`) leggono i byte con
  `read_upload` e passano a un thread (`anyio.to_thread.run_sync`) decompressione e riconoscimento del formato, lettura
  della struttura, analisi, scritture su disco e database e, per ProSA, lo stato restituito, che prova MODELLER con un
  processo esterno: il loop resta libero per le altre richieste (flussi SSE, `/api/health`).
- **File caricati:** sequenze e allineamenti (`/api/files/sequences`) vengono solo letti e non salvati. Le
  strutture (`/api/files/structure`) vanno tra gli upload, validate e soggette alla pulizia automatica.
- **Script MODELLER:** generati con valori validati (regex per residui e atomi, numeri con limiti) e
  `repr()`.
- **CSV esportati:** con neutralizzazione delle formule.
- **Report HTML:** testi sempre escapati, nessuno script né risorsa esterna, CSP nel documento e nella risposta,
  immagine accettata solo come PNG verificato (vedi [Report del modello](#report-del-modello)).
- **Pacchetti di riproducibilità:** nell'esportazione chiavi di licenza, email, chiave d'accesso e percorsi locali
  vengono sostituiti e ogni file è ricontrollato; il pacchetto caricato è un input non fidato (percorsi, collegamenti,
  duplicati, bombe di decompressione, impronta del manifest, checksum, parametri validati come all'avvio) e nessuno
  script del pacchetto si estrae né si esegue (vedi [Riproducibilità](#riproducibilità-pacchetti-e-riesecuzione)).

**Accesso con chiave (modalità server).**

- **Chiave:** `BIOLOOM_ACCESS_KEY` di almeno 16 caratteri (`MIN_KEY_LENGTH`; quella generata e quelle scritte dagli
  avviatori in `server.env` ne hanno 24). Più corta: `AccessKeyError` nel lifespan, il motivo stampato ben visibile
  («ERRORE: BIOLOOM_ACCESS_KEY è troppo corta: N caratteri, ne servono almeno 16 …», con le due soluzioni: una chiave più
  lunga in `server.env`, per esempio `openssl rand -hex 12`, oppure la variabile vuota, così BioLoom ne genera una in
  `data/access_key`) e uscita con codice 3 (`__main__`, `server.started` falso; prima usciva con 0). Limiti: una chiave
  scritta a mano in `data/access_key` si controlla solo contro il minimo di 8 caratteri (sotto, `load` ne genera una
  nuova di 24, `secrets.token_urlsafe(18)`, e la stampa nel log; da 8 a 15 è accettata). Con Docker `start_server` di
  `bioloom.sh` aspetta la risposta e, se il container esce o riparte, mostra le ultime 30 righe del log e si ferma con
  l'errore ([Avviatori](#avviatori-un-bioloom-alla-volta-e-controlli-allavvio), **Attesa dell'avvio da server**); con
  Podman `wait_healthy` ferma l'avviatore dopo 2 minuti, senza il log.
- **Sessioni revocabili:** il segreto della firma è lo SHA-256 di `bioloom-session:<chiave>`, più `:epoch:<n>` quando
  l'epoca non è 0. L'epoca sta in `data/session_epoch` (un intero, creato a 0): all'epoca 0 il segreto è quello delle
  versioni precedenti, quindi aggiornare BioLoom non chiude le sessioni aperte. `POST /api/auth/logout-all` (sessione
  valida o chiave come Bearer, attraverso il freno) salva l'epoca successiva con una scrittura atomica prima di usarla
  (`revoke_all`): tutti i cookie emessi prima, compreso quello di chi chiede, non valgono più, anche dopo un riavvio.
  Risponde `{"ok": true}` e cancella il cookie; gli script con la chiave continuano a funzionare. Errori: 400
  `auth_disabled` in locale, 401 `auth_required` senza sessione, 429 `rate_limited`, 500 `session_epoch_error` se
  l'epoca non si salva (nulla cambia). Un file illeggibile o che non contiene solo un intero non negativo non vale 0, che
  riattiverebbe le sessioni revocate: si sceglie un'epoca nuova a caso, la si salva e lo si scrive nel log, e tutte le
  sessioni si chiudono. Un file assente vale 0 (come dopo un aggiornamento); uno non scrivibile all'avvio lascia l'epoca
  in memoria. Limite: cancellare il file riporta l'epoca a 0 e riattiva le sessioni dell'epoca 0 non ancora scadute
  (serve l'accesso in scrittura alla cartella dati). `/api/auth/status` non dipende dall'epoca (con un cookie risponde
  come prima); con la chiave come Bearer passa dal freno (sotto) e può rispondere 429 `rate_limited`.
- **Freno ai tentativi** (`core/auth.LoginLimiter`, uno per app in `app.state.login_limiter`), per indirizzo del client:
  - un login alla volta: gli altri dallo stesso indirizzo, in parallelo, ricevono subito 429;
  - dopo una chiave sbagliata la rotta attende `LOGIN_STEP` (0,5 s) per ogni errore degli ultimi 15 minuti
    (`LOGIN_WINDOW`), al più 10 s (`LOGIN_MAX_DELAY`), più 0,05 s di margine (asyncio può svegliarsi qualche millisecondo
    prima), poi risponde 401 `auth_failed`; fino alla fine dell'attesa ogni tentativo dallo stesso indirizzo, anche con
    la chiave giusta, riceve 429 `rate_limited` («Troppi tentativi d'accesso da questo indirizzo: riprova tra N s.»),
    anche se il client ha chiuso la connessione;
  - la chiave come Bearer, su ogni rotta protetta, su `/status` e su `logout-all`, passa da `try_key`, che controlla,
    confronta e registra l'errore insieme sotto il lock: indirizzo fermo → 429 senza confrontare la chiave (nemmeno
    quella giusta: la risposta direbbe quale è); chiave sbagliata → un errore come quelli del login e 401 subito
    (l'attesa la fanno i tentativi successivi); chiave giusta → passa senza occupare l'indirizzo né azzerare gli errori,
    così gli script fanno richieste in parallelo. Un cookie di sessione valido non passa mai dal freno;
  - al più 4096 indirizzi seguiti (`LOGIN_MAX_CLIENTS`): quelli senza errori recenti né tentativi in corso si
    dimenticano, oltre il massimo i meno recenti;
  - prima: un dizionario globale senza limite di indirizzi, l'attesa solo nella richiesta che aveva sbagliato (le
    richieste in parallelo la scavalcavano) e la chiave come Bearer senza freno. `AuthConfig.request_allowed` e
    `authenticated` non ci sono più: al loro posto `requires_auth`, `session_valid`, `bearer_key` e
    `api/auth.authenticate` / `access_denied`, usati dal middleware, da `/status` e da `logout-all`.
- **Indirizzo del client** (`client_address`): quello della connessione. Con `BIOLOOM_TRUSTED_PROXIES` (indirizzi o
  reti CIDR separati da virgole o spazi; una voce non valida ferma l'avvio con il motivo, `AccessKeyError`) e una
  connessione da uno di quei proxy, il primo indirizzo di `X-Forwarded-For` partendo da destra che non è un proxy fidato:
  l'ultimo aggiunto è quello che il proxy ha visto collegarsi, quelli più a sinistra li può scrivere il client; una voce
  che non è un indirizzo ferma la ricerca all'ultimo proxy fidato, e gli IPv4 scritti come IPv6 (`::ffff:…`) tornano
  IPv4. Senza proxy fidati, una richiesta inoltrata da un indirizzo non fidato che passa dal freno (`client_of`: il
  `login` della schermata d'accesso o una chiave come Bearer in `authenticate`; non quelle con un cookie di sessione
  valido né quelle senza chiave, come `/api/auth/status` e i file statici) fa scrivere nel log, una volta per indirizzo
  (al più 16), che il freno tratta tutti i client come uno solo, con il valore da mettere nella variabile.
  Con `BIOLOOM_BIND=127.0.0.1` il container vede lo stesso indirizzo (il gateway della rete di Docker, da
  docker-proxy; con Podman senza root di solito quello del port forwarder di rootlessport) per tutte le connessioni,
  sia dal reverse proxy sia dal tunnel SSH. Con il tunnel non c'è `X-Forwarded-For`: niente avviso nel log, e il
  freno vale per tutti i client anche con `BIOLOOM_TRUSTED_PROXIES`: chi sbaglia la chiave fa aspettare gli altri fino a
  10 s a ogni errore, e ritentando di continuo (ogni errore rinnova l'attesa) li tiene fuori finché non smette.
  - **Dal file al container:** `compose.yml` (`BIOLOOM_TRUSTED_PROXIES: "${BIOLOOM_TRUSTED_PROXIES:-}"`) e
    `bioloom-podman.sh` (ciclo dei `-e`) la passano da `server.env`. `server.env.example` la elenca vuota dopo
    `BIOLOOM_ALLOWED_HOSTS`, con il commento: solo dietro un reverse proxy che invia `X-Forwarded-For`; indirizzi o reti
    CIDR separati da virgole; il valore giusto lo suggerisce il log al primo inserimento della chiave arrivato dal
    proxy (schermata d'accesso o Bearer; con una sessione già aperta bisogna uscire prima), di solito il gateway della
    rete dei container (es. `172.18.0.1`: compose crea la rete del progetto, mentre `172.17.0.1` è quello della rete
    `bridge` predefinita); tutta la rete `172.16.0.0/12` solo con `BIOLOOM_BIND=127.0.0.1`, perché con `0.0.0.0` Docker
    conserva l'indirizzo dei client diretti (DNAT) e un computer della LAN in 172.16–31.x.x, o un altro container
    sull'host, diventerebbe fidato e potrebbe scrivere `X-Forwarded-For`; vuota = header ignorato; un valore sbagliato
    ferma l'avvio. Il backend la legge solo con `BIOLOOM_AUTH=on` (`app.state.trusted_proxies`, vuota in locale), e
    con Docker l'errore lo mostra l'attesa di `start_server` (log e uscita 1). Prima nessuno dei due la passava: dietro
    un reverse proxy il freno valeva per tutti i client insieme anche se il log suggeriva la variabile.
  - **Limiti:** risolve solo il caso del reverse proxy, non quello del tunnel SSH. Con la variabile impostata anche le
    connessioni del tunnel e dei programmi sul server arrivano dallo stesso indirizzo del proxy, quindi fidato, e possono
    scrivere da sé `X-Forwarded-For`: chi ha un account sul server può cambiare indirizzo a ogni tentativo e non subire
    l'attesa. Non c'è un limite globale (con IPv6 un client ha molti indirizzi).
- **Impostazioni dell'amministratore** (`api/system.SERVER_LOCKED`): in modalità server `esmfold_url` e
  `modeller_python` vengono da `BIOLOOM_ESMFOLD_URL` e `BIOLOOM_MODELLER_PYTHON`; la pagina rimanda il valore in uso, che
  si accetta, e un valore diverso dà 403 `esmfold_url_locked` o `modeller_python_locked`. La verifica da riga di comando
  usa le stesse impostazioni (`auth.server_mode()`). Nei container `BIOLOOM_MODELLER_PYTHON` arriva da `server.env`
  (`compose.yml`, `bioloom-podman.sh`; `server.env.example` la elenca vuota dopo `MODELLER_KEY`), come percorso nel
  container. `engines/tools.modeller_candidates` prova in ordine il valore in uso, `BIOLOOM_MODELLER_PYTHON` e
  `sys.executable`: un percorso inesistente o senza MODELLER si salta e vale il Python dell'immagine. Dal desktop compose
  la passa se è esportata nella shell, e lì è solo una candidata in più.
- **Limiti delle email:** email di prova e avvisi di fine job hanno limiti di frequenza anche in locale (vedi [Avviso per
  email](#avviso-per-email-alla-fine-delle-pipeline)).

**Richieste in uscita** (`engines/remote.py`, tranne il download di pdbaa in `services/databases.py`). Tutte vanno a
indirizzi fissi con identificatori validati (codici PDB, accessioni UniProt con la regex ufficiale, isoforme escluse),
tranne il servizio ESMFold, il cui indirizzo è un'impostazione. QMEAN locale, BLAST+, MMseqs2, l'analisi di qualità, le
analisi strutturali, la rifinitura e la mutagenesi non usano la rete; l'interfaccia non carica risorse esterne.

| Servizio | Dati inviati | Quando | Controlli |
|---|---|---|---|
| RCSB Search (`search.rcsb.org`) | sequenza | motore `rcsb` | nuovi tentativi sui 5xx |
| RCSB file e GraphQL (`files.rcsb.org`, `data.rcsb.org`) | codici PDB | download dei template, metadati degli hit di tutti i motori sul PDB (`rcsb_metadata`, anche per BLAST+ e MMseqs2) | metadati in cache 30 giorni |
| NCBI QBlast (`blast.ncbi.nlm.nih.gov`) | sequenza, `tool=BioLoom` e l'email di contatto se impostata | motore `ncbi`; motore `alphafold` senza accessione (database `swissprot`); nella pipeline le sequenze senza intestazione FASTA di UniProt | linee guida di NCBI (stato al più una volta al minuto, attesa massima 45 min); avvisi dei controlli preventivi oltre 50 ricerche |
| UniProt (`rest.uniprot.org`) | accessione o testo cercato | importazione, annotazioni, domini sul bersaglio, varianti della mutagenesi | cache 7 giorni |
| InterPro (`www.ebi.ac.uk/interpro`) | accessione UniProt, mai la sequenza | domini (pagina Sequenze, `/api/bio/coverage`, controlli preventivi) | cache 7 giorni; disordine e bassa complessità si calcolano nel container |
| AlphaFold DB (`alphafold.ebi.ac.uk`) | accessione UniProt, mai la sequenza né le coordinate | importazione nella libreria, motore `alphafold`, predizione `alphafold` | file scaricati solo da URL `https://alphafold.ebi.ac.uk/files/…`, qualunque URL contenga la risposta; cache 7 giorni |
| SWISS-MODEL QMEAN (`swissmodel.expasy.org`) | coordinate del modello ed email | QMEAN remoto, scelto o come fallback | consenso esplicito all'invio (`qmean_remote`) ed email obbligatori; pagina dei risultati restituita solo se è un indirizzo `https://` con un host, senza credenziali né spazi (`remote.https_url`), altrimenti `null` |
| ESMFold (`esmfold_url`, predefinito `api.esmatlas.com` di Meta) | sequenza di una catena, mai le coordinate | job `predict` con ESMFold | consenso esplicito (sotto); nessun reindirizzamento seguito; risposta al più 20 MB, verificata prima di essere salvata; cache 30 giorni |
| NCBI FTP (`ftp.ncbi.nlm.nih.gov`) | nulla: solo download | job `db_update_pdbaa` | estrazione con filtro `data` |

- **ESMFold (F9.3):** il consenso (`esmfold_remote`) è spento per default, anche per le cartelle dati esistenti; il
  pulsante lo chiede prima dei controlli e dell'avvio spiegando che cosa parte e verso dove, la rotta lo verifica (403,
  nessuna richiesta) e l'handler di nuovo all'esecuzione; si revoca in Impostazioni → Generale. In modalità server
  l'indirizzo lo sceglie l'amministratore (`BIOLOOM_ESMFOLD_URL` in `server.env`, vuoto = servizio pubblico):
  `PUT /api/settings` rifiuta un altro `esmfold_url` (403 `esmfold_url_locked`) e quello salvato in `settings.json` non
  vale (`SettingsStore` con `server`). Il corpo di una risposta d'errore entra nel messaggio del job solo dal servizio
  pubblico (gli altri vanno nel log del server). I dati della libreria di ESM Atlas sono distribuiti con licenza CC-BY 4.0
  (intestazione del file, conservata).
- **AlphaFold DB:** senza accessione il motore invia la sequenza a NCBI (QBlast su Swiss-Prot), come il motore `ncbi`.

## Test

**Conteggi** (contati l'11 ottobre 2026 con `pytest --collect-only -q` in `backend/` e con `npx vitest list` in
`frontend/`):

| Suite | File | Test |
|---|---|---|
| backend, `backend/tests` (pytest) | 59 | 1461 nella suite predefinita (58 file), più 5 con il marcatore `network`, esclusi da `addopts` (`pytest -m network`): i 4 di `test_remote.py` e uno di `test_rete_bloccata.py` |
| frontend, `frontend/src/**/*.test.ts(x)` (vitest) | 101 | 815 (11 file in ambiente jsdom) |

I test che richiedono MODELLER, PROCHECK, ProSA o gli strumenti dell'immagine di sviluppo (fpocket, PDB2PQR, APBS,
OpenMM, PDBFixer) si saltano da soli, con il motivo, dove mancano (`requires_modeller` e le condizioni degli strumenti):
il numero di quelli eseguiti dipende quindi dall'immagine e dalla chiave di MODELLER. Oltre a 1UBQ, `tests/data`
contiene uscite reali di MODELLER (`dope_1ubq.profile`), PROCHECK, ProSA, fpocket e PDB2PQR su 1UBQ e le strutture
sintetiche generate da `make_synthetic.py`: `ubq_dimer.pdb` (omodimero C2 con interfaccia di contatto) e `ubq_zn.pdb`
(sito Zn²⁺ su His68 e due acque).

**Esecuzione:**

- `scripts/test.sh`: ruff, pytest nell'immagine con MODELLER reale, eslint, typecheck e vitest (`test.sh frontend`:
  eslint, typecheck e vitest, senza Docker né `launcher/VERSION`, che servono solo ai comandi con l'immagine: backend e
  `lint`, cioè ruff nel container). L'immagine di test `bioloom-test:<base>` (pytest, pytest-timeout, ruff) si
  ricostruisce quando cambia l'immagine di base o `docker/test/Dockerfile`: l'etichetta `bioloom.test.base` contiene
  l'id dell'immagine di base e la somma `cksum` del Dockerfile senza CR (prima solo l'id, quindi un `bioloom-test`
  esistente non prendeva i pacchetti nuovi finché non cambiava la base).
- **Limite per test:** `timeout = 1800` in `[tool.pytest.ini_options]`, con pytest-timeout 2.4.0 (la 2.5.0 è stata
  ritirata da PyPI) nelle dipendenze dev di `pyproject.toml`, nell'immagine di test e nella CI. Un test bloccato
  fallisce con «Failed: Timeout (>1800.0s) from pytest-timeout» e il suo stack, invece di lasciare appesa la suite.
  1800 s e non meno: il test più lungo misurato dura 44,7 s, ma i test aspettano un singolo job fino a 1200 s
  (`wait_job`), e su un computer lento deve arrivare prima il loro messaggio. Senza il plugin (Python di sistema)
  pytest dà solo «PytestConfigWarning: Unknown config option: timeout».
- **Senza Docker e su Windows:** dalla cartella `backend/`, un ambiente virtuale con `pip install -e ".[dev]"` (pytest,
  pytest-timeout, ruff; per le versioni dell'immagine prima `pip install -r requirements.lock`); i test che chiedono
  MODELLER o gli strumenti dell'immagine si saltano. La suite passa anche con il Python di Windows: ultima esecuzione
  completa (11 ottobre 2026) 1395 passati, 66 saltati e 5 esclusi in 6 min 47 s. I test sensibili al sistema si
  adattano: in `test_cleanup.py` i permessi si confrontano con quelli reali della cartella (0o755 solo su Linux), e il
  link simbolico di `safe_join` si salta su Windows (manca il privilegio) mentre la giunzione gira solo lì;
  in `test_compute_cache.py` `write_bytes` invece di `write_text`, che su Windows scrive CRLF; in `test_report.py` e in
  `test_exports.py` il nome di file con `" < >` si salta su Windows, e uno con caratteri ammessi anche da NTFS gira
  dappertutto (in `test_report.py` con `'` e `&`); in `test_reproducibility.py` i separatori del sistema (e il percorso
  come lo scrive JSON); in `test_predict.py` id brevi nel parametrize (con quelli lunghi `PYTEST_CURRENT_TEST` superava
  i 32767 caratteri ammessi su Windows).
- **Niente rete reale** (fixture autouse `_no_real_network` in `tests/conftest.py`): nei test senza il marcatore
  `network` falliscono subito, con un messaggio che dice come fare, httpx con il trasporto vero (anche asincrono), la
  risoluzione di nomi non locali (`socket.getaddrinfo`) e `connect`/`connect_ex` verso indirizzi non locali (quindi anche
  smtplib e urllib). Locali sono localhost, 127.0.0.0/8, `::1`, 0.0.0.0, `::` e i nomi `*.localhost`. Ogni tentativo si
  registra, e il test fallisce anche se il codice ha trasformato l'errore in «servizio non raggiungibile». La fixture non
  usa `monkeypatch`, quindi un `monkeypatch.undo()` nel test non toglie il blocco. `httpx.MockTransport`, `TestClient` e
  i server su 127.0.0.1 funzionano; i test verso i servizi veri hanno il marcatore `network` (dichiarato in
  `pyproject.toml`, escluso da `addopts`).
- **Attese senza pause fisse** (`tests/conftest.py`): `until()` (attesa su una condizione, con scadenza, al posto delle
  sei copie locali), `blocked_in(thread, funzione)` (un thread fermo in un'attesa chiamata da quella funzione),
  `dispatcher_settled()` (tutti i worker liberi di un pool in attesa e la coda ferma), `WatchedLock` (lock che segnala
  chi lo trova occupato). Restano solo pause che simulano lavoro, cicli di polling con scadenza e la pausa voluta di
  1,3 s in `test_scheduling.py`. `wait_job` mostra codice e corpo di una risposta d'errore invece di un `KeyError`.
- **CI** (`.github/workflows/ci.yml`), quattro job più uno manuale:
  - **backend:** «Versione unica» (`scripts/versione.sh`: le copie devono coincidere con `launcher/VERSION`) e «Impronta
    del codice» (`impronta.sh`, `impronta.sh` con `IMPRONTA_SENZA_PERL=1` e `Get-BioLoomImpronta` in pwsh devono dare lo
    stesso valore su `programma/`), poi ruff e pytest in Python puro senza strumenti esterni, con pytest-timeout;
  - **frontend:** eslint, typecheck, vitest, build;
  - **avviatori:** shellcheck con `-S warning` (il livello che oggi passa; restano due note SC2015 di livello info, in
    `bioloom.sh` e `bioloom-podman.sh`) su `launcher/*.sh`, `avvia-bioloom.sh`, `docker/*.sh`, `scripts/*.sh`, gli
    script dei test di QMEAN e i due `.command`; PSScriptAnalyzer con `-Severity Error, ParseError` sugli script
    PowerShell di `launcher/`; bit di esecuzione 100755 di ogni `.sh` e `.command` tracciato, altrimenti «Script senza
    il bit di esecuzione nel repository (git update-index --chmod=+x <file>):» con l'elenco;
  - **docker** (dopo backend e frontend): immagine completa con `BIOLOOM_VERSION` come build arg e i test al suo
    interno; nel passo «Impronta nell'immagine» `/app/IMPRONTA` deve essere l'impronta del codice da cui l'immagine è
    costruita. Poi un controllo che i test non abbiano cambiato l'impronta (altrimenti `prepara` ricostruirebbe
    l'immagine), l'avviatore vero (`--no-menu --no-browser`, `/api/health`, `arresta`) e la copia portatile:
    `prepara "$RUNNER_TEMP/copia BioLoom" --no-menu --senza-qmean --senza-dati`, `bioloom:<versione>` tolta da Docker,
    avvio dalla copia, che la carica da `immagini/`, salute e arresto. Se un passo fallisce, l'ultimo mostra container e
    log;
  - **qmean**, solo da Actions → CI → Run workflow con l'opzione «qmean» (l'immagine di base pesa circa 2 GB):
    costruzione di `bioloom-qmean` e `/status` del worker, che risponde anche senza database.

  `ci.yml` passa actionlint 1.7.12 (verificato prima del passaggio agli SHA, che cambia solo gli 11 `uses`); gli script
  passano shellcheck 0.11.0 e 0.9.0 (la versione dei runner) a livello warning e PSScriptAnalyzer 1.25.0 senza errori.

  **Actions fissate per SHA:** ogni `uses:` punta allo SHA completo del commit, con il tag nel commento: checkout v4.4.0
  (`11d5960a…`), setup-python v5.6.0 (`a26af69b…`), setup-node v4.4.0 (`49933ea5…`), setup-buildx-action v3.12.0
  (`8d2750c6…`), build-push-action v6.19.2 (`10e90e36…`), cioè i commit a cui puntavano i tag principali usati prima
  (`@v4`, `@v5`, `@v3`, `@v6`): il codice eseguito è lo stesso, ma un tag spostato non lo cambia più. I cinque sono tag
  semplici, che puntano direttamente al commit (verificato anche con l'API di GitHub). Per trovare il commit di un tag:
  `git ls-remote https://github.com/<owner>/<repo> 'refs/tags/<tag>' 'refs/tags/<tag>^{}'`; se c'è la riga che finisce
  con `^{}` (tag annotato) il commit è quello, perché con il solo nome del tag `ls-remote` dà l'oggetto del tag.

  **Dependabot** (`.github/dependabot.yml`), ogni lunedì alle 06:00 (Europe/Rome), con un'attesa (`cooldown`) di 7
  giorni prima di proporre una versione appena uscita (14 per le major npm) e senza attesa per gli aggiornamenti di
  sicurezza:
  - `github-actions`: minori e correzioni in una PR (`actions-minori`), major in un'altra (`actions-principali`), da
    leggere con le note di rilascio; al più 3 PR aperte. Riscrive SHA e commento insieme;
  - `npm` in `programma/frontend`: minori e correzioni in una PR (`npm-minori`), major una per PR, perché di solito
    chiedono modifiche a codice o configurazione; al più 5;
  - `pip` in `programma/backend` con `increase-if-necessary`: tocca i limiti minimi di `requirements.txt` e
    `pyproject.toml` solo se una versione nuova ne resta fuori; al più 3. `requirements.lock` e `requirements-tools.lock`
    restano fuori (estensione `.lock`) e si rigenerano a mano, quindi questa voce produrrà poche PR;
  - fuori da Dependabot anche i digest delle immagini di base e gli SHA-256 e i commit dei file scaricati nei
    Dockerfile, che si aggiornano a mano ([Costruzione riproducibile](#costruzione-riproducibile-dellimmagine)).

  `dependabot.yml` è valido rispetto allo schema di SchemaStore, ma `cooldown` e `groups` non sono verificati sul parser
  di GitHub: dopo il push si controllano in Insights → Dependency graph → Dependabot.
- **Immagine di sviluppo:** per provare strumenti nuovi senza toccare l'immagine dell'avviatore,
  `docker build -f docker/Dockerfile -t bioloom:dev .` e `BIOLOOM_BASE_IMAGE=bioloom:dev scripts/test.sh` (test in
  `bioloom-test:bioloom-dev`).
- **Profilazione:** `scripts/test.sh backend tests/perf_timing.py -s` (fuori dalla suite, vedi [Prestazioni](#prestazioni)).
- **Verifica nel container:** `python -m bioloom.selftest` (o Impostazioni → Strumenti).
- **Verifica di QMEAN senza il disco reale:** mini-database creato con
  `docker/qmean/tests/make_mini_uniclust.sh` (punteggi non significativi).

**Backend** (numero di test fra parentesi):

- **API, dominio e MODELLER:** `test_api.py` (11: salute, progetti, sequenze e documenti, path traversal, libreria,
  allineamenti e qualità come job, errori di validazione leggibili e in italiano, paginazione degli elementi di
  pipeline); `test_sequences.py` (8: FASTA, validazione, PIR, zona crepuscolare, Clustal, proprietà);
  `test_structure_quality.py` (10: catene e header, Ramachandran di riferimento, clashscore senza falsi positivi
  covalenti, DSSP integrato, GOR, voto, Kabsch e TM-score, center-star, rapporto senza DOPE); `test_modeller.py` (14:
  script validi, livelli di raffinamento con impostazioni distinte e predefinito di MODELLER, opzioni non valide,
  preparazione dei template, modellazione end-to-end); `test_search.py` (5: parsing di BLAST, client NCBI simulato,
  nuovi tentativi su RCSB); `test_synthetic_data.py` (2: le strutture sintetiche restano valide).
- **Job e ripresa:** `test_jobs.py` (12), `test_jobs_robustezza.py` (8) e `test_pipeline_resume.py` (13): successo,
  errori, annullamento e timeout, riavvio simulato; scratch non scrivibile e errori nella preparazione del job (stato
  finale `failed`, poi eliminazione e ripresa), processo esterno terminato se la lettura del suo output si interrompe,
  annullamento che arriva mentre un processo si registra; solo gli elementi non conclusi, ambito "solo i falliti",
  pipeline completate con falliti, file vecchi eliminati, richieste concorrenti, nuovo annullamento, progresso,
  elementi completati solo a file salvati (disco pieno, modelli mancanti), eliminazione concorrente a una ripresa,
  stato finale scritto anche se il salvataggio non riesce.
- **Flusso dei dati** (`test_dataflow.py`, 11): scratch, salvataggio definitivo, intermedi, blocco SQLite (seconda
  istanza), pulizia, gruppi di processi, archivi su file temporanei.
- **Eliminazione di progetti e pulizia dei job** (`test_cleanup.py`, 108; job inseriti direttamente nel database con una
  cartella di dimensione nota, e dopo ogni eliminazione il controllo che non restino job orfani, cartelle di job senza
  riga, etichette o stelle di elementi spariti, elementi di pipeline senza job né resti `.deleting-*`):
  - `submit`: progetto inesistente (404, nessuna riga né cartella), progetto vuoto = nessun progetto, preparazione
    fallita senza riga né cartella (stessa eccezione), resto non rimovibile passato in `.deleting-<id>` e tolto dalla
    pulizia automatica, pulizia che solleva senza nascondere l'errore originale, invii continui in un progetto mentre
    lo si elimina;
  - un job: rimozione con i resti e le pause solo fra i tentativi, errori di `remove_tree` che non escono mai (aperture
    e letture fallite lasciate com'erano, file in sola lettura sbloccato, nessuna cartella resa illeggibile, file
    singolo con permessi o eliminazione rifiutati), byte liberati, cartella in uso (409, job intero), rimozione parziale
    con il resto tolto dalla pulizia automatica, resto in rimozione saltato, resto di un job che ha ancora la riga mai
    eliminato (torna al suo posto, o resta se il nome è occupato), resto sparito non contato, cartella rimasta nel resto
    trattata come quella del job, rinomino inverso riprovato, condizione `only_if` non più vera al controllo o solo nel
    DELETE (rinomino annullato), cartelle senza riga, `ref` e `.rerun-incoming` mai toccate dalla pulizia automatica,
    elemento non eliminabile che non ferma la pulizia né si conta, scratch di tutti i job attivi (anche oltre 500) e di
    quelli ancora in chiusura conservato, avvio che non si ferma per una pulizia iniziale guasta, `backups` nello spazio
    e mai eliminata, misura delle cartelle a tempo;
  - progetti: uso (515 job, attivi, stella, etichette, pipeline riprendibili, byte, 404, 400, sola lettura), 620 job
    eliminati con 30 job di un altro progetto e 5 globali intatti, job attivo più vecchio dietro 600 completati (409,
    nulla eliminato, `jobs_deleted` 0), job partito durante l'eliminazione (409 `project_active_jobs` con i 3 job già
    eliminati nei dettagli e nel messaggio), etichette, sequenze e documenti, «Nuovo progetto» al posto dell'ultimo, job
    ripreso (409 `project_busy` con i job già eliminati, poi ripetibile), job che compaiono durante l'eliminazione (uno
    solo: riuscita; uno nuovo per ogni job eliminato: 409 dopo 3 passate), seconda richiesta (409 `delete_in_progress`
    subito), ultimi due progetti eliminati insieme (ne resta esattamente uno, 5 volte), arresto (503);
  - pulizia: anteprima in sola lettura uguale all'esecuzione, filtri (8 casi: stati, età con le date di fine, di
    aggiornamento e illeggibili, tipi, progetti e «tutti» con job senza progetto o con un progetto inesistente, stella,
    etichette), stati attivi rifiutati e job attivi mai toccati, ambito esplicito (400 per entrambe le rotte, 404),
    tipi ed età non validi (7 casi), valori null o di un altro tipo (18 casi, anche `"no"` al posto di `false` con un job
    con la stella: sempre 400 `invalid_cleanup`, mai 422, nulla eliminato), `max_seconds` null e limitato, `as_of` non
    valido (8 casi per entrambe le rotte), portato in UTC e al secondo, «adesso» del limite d'età, esecuzione che non va
    oltre l'anteprima confermata (un job fallito dopo e uno ripreso e fallito di nuovo restano; senza `as_of` si
    eliminano), byte dell'anteprima che convergono oltre una cartella lenta, pipeline riprendibili escluse per
    impostazione predefinita, protetti ed esempi, stella aggiunta fra selezione e DELETE, 2500 job in un ciclo di
    chiamate, un job per chiamata con l'orologio che salta 10 s a ogni lettura, job in uso saltato senza attesa e che
    non tiene vivo il ciclo, job sparito, errore contato senza interrompere, eventi, etichette e ricerca globale, byte
    senza i resti, rotte (400, non 404 o 405) e chiave d'accesso (401);
  - `safe_join` (10): una cartella e un file annidato creati ed eliminati da un altro thread mentre se ne calcola il
    percorso (gara reale: in una prova su Windows il ripiego è scattato 383 volte su 1732 chiamate, senza risultati
    errati), 5 casi di risalita fuori dalla radice (`..`, `../fuori`, `sub/../../fuori`, `a/../../fuori` in un solo
    nome, `<job>/../../fuori`) con in più un percorso assoluto esterno, il ripiego simulato con `Path.resolve` sostituito
    (gira su ogni sistema), link verso fuori e verso dentro: symlink su Linux (su Windows manca il privilegio), giunzione
    su Windows.
- **Licenze e accesso** (`test_licenses_auth.py`, 11): chiave MODELLER (fonti, formato, prova prima del salvataggio),
  LicenseDB di ProSA, chiave d'accesso, modalità server, cookie `Secure` dietro un proxy HTTPS, arresto consentito dal solo avviatore.
- **Segreti e accesso** (`test_segreti_accesso.py`, 25): `mask` e `redact` (maiuscole, segreti contenuti in altri);
  prova di MODELLER con la chiave attiva e la candidata mascherate; `/api/system/info`, verifica dell'installazione e
  licenze senza la chiave in chiaro; ambiente dei programmi esterni senza segreti (anche con i nomi in minuscolo) e
  output di un job mascherato; freno ai tentativi (un tentativo alla volta e attese crescenti, memoria limitata, login in
  parallelo rifiutati, chiave come Bearer attraverso il freno con 40 richieste in parallelo su `/status` che confrontano
  una sola chiave, rotte protette e `logout-all` con Bearer, `try_key`); indirizzo del client dietro un proxy fidato
  (voci scritte dal client ignorate, avviso per un proxy non fidato, valore non valido che ferma l'avvio) e freno per
  client dietro il proxy; chiave corta che ferma l'avvio; `logout-all` che chiude ogni sessione, epoca 0 che tiene le
  sessioni delle versioni precedenti, file dell'epoca rovinato o illeggibile che le chiude tutte, `logout-all` in locale;
  `modeller_python` fissato dal server e verifica da riga di comando con le impostazioni del server; limiti delle email
  di prova (anche gli invii falliti contano, anche dall'API) e degli avvisi di fine job.
- **Richieste di altri siti** (`test_guard.py`, 17): header Host (loopback con qualunque porta, IPv6,
  `BIOLOOM_ALLOWED_HOSTS` anche come URL, `*`, spento con l'accesso protetto, `/api/health` esente), origine
  (`Sec-Fetch-Site`, `Origin` uguale all'Host o a `X-Forwarded-Host`, `null`, origini dello sviluppo solo fuori dal
  container), arresto da un altro sito rifiutato senza arrestare, intestazioni contro gli iframe, guardia più esterna
  della chiave d'accesso.
- **Caricamenti** (`test_upload_limits.py`, 12): `BodyLimit` su un'app ASGI di prova (`Content-Length` oltre il limite
  rifiutato senza leggere il corpo; corpo a blocchi fermato durante la lettura, anche con un'app che trasformerebbe
  l'interruzione in 400; corpi entro il limite invariati); limite dell'app uguale al caricamento più grande più il
  margine, con la guardia più esterna e `BodyLimit` più interno; con un limite ridotto 413 `too_large` prima
  dell'endpoint, con e senza `Content-Length`; caricamenti normali, altre richieste e limite dell'endpoint (413
  `too_large` per `LicenseDB`) invariati; per i cinque endpoint che ricevono file, `/api/health` che risponde mentre
  l'endpoint lavora sul file (controprova: con il lavoro di nuovo nel loop il test rileva il blocco).
- **Sostituzione dei template** (`test_template_overwrite.py`, 11; due sono sovrapposizioni dello stesso id: due
  sostituzioni .pdb e .cif, un download RCSB e una sostituzione, registrate nell'ordine): 409 `template_exists` senza `overwrite` con la
  libreria invariata; job e progetti che usano il template nel messaggio e nei dettagli (un job di un altro template non
  conta); file rovinato, PDB e mmCIF, che non tocca il template (400 `invalid_structure` che cita l'id e non la cartella
  temporanea; 409 senza conferma); sostituzione con l'analisi rifatta e nessuna cartella temporanea rimasta; cambio di
  formato con un solo file (`on`, poi `1`); id nuovo con `overwrite` come un caricamento normale e valore non valido
  (422); cartelle temporanee tolte all'avvio; download da RCSB che non sostituisce un template caricato nel frattempo e
  file scaricato illeggibile che non entra in libreria.
- **Job dei database** (`test_pdbaa_jobs.py`, 4; download di NCBI simulato con cancelli): i tre job nello stesso pool
  con la stessa risorsa esclusiva; due aggiornamenti di pdbaa mai insieme (il secondo in coda con la sua fase, un solo
  download alla volta, una cartella di lavoro per job con il suo id, avanzi delle versioni precedenti tolti e cartelle
  estranee lasciate); build e indice MMseqs2 che aspettano la fine dell'aggiornamento; estrazione non riuscita senza
  cartelle rimaste.
- **Versione unica e impronta del codice** (`test_version.py`, 10; saltati senza `launcher/VERSION`, cioè con il backend
  copiato da solo): `bioloom.__version__`, `pyproject.toml` (anche `license = "MIT"`), `package.json` e i due campi
  del progetto in `package-lock.json` (con la licenza), `ARG` dei due Dockerfile, stadio `impronta` con
  `COPY --from=impronta` e `LABEL bioloom.version`, i due predefiniti e il build arg di `compose.yml`,
  `server.env.example` senza `BIOLOOM_VERSION`; la definizione dell'impronta riscritta in Python su una cartella
  `programma/` sintetica con tutti i casi da includere ed escludere (nomi con spazi, ordine per byte, CR isolati, file
  binari, `node_modules` annidati, prefissi simili come `docker/tested/` e `frontend/distribuzione.txt`) e
  `impronta.sh` uguale a quella, con perl e solo in sh, sulla stessa cartella in CRLF, come `Get-BioLoomImpronta` di
  `impronta.ps1` (con Windows PowerShell o pwsh; saltato senza).
- **Strumenti esterni** (`test_engines_extra.py`, 10): parser di PROCHECK e ProSA tarati su output reali di 1UBQ,
  MMseqs2, QMEAN locale con worker simulato, strumenti mancanti come avvisi, verifica dell'installazione (anche fpocket,
  PDB2PQR + APBS e OpenMM: "ok" se ci sono, "non disponibile" altrimenti).
- **UniClust30 e QMEAN:** `test_uniclust_discovery.py` (11): riconoscimento di tutti i formati, scelta della versione,
  copie interrotte, archivi e formato MMseqs2, cs219 convertiti e copia locale validi finché il disco non cambia, limite
  di profondità, filesystem dei mount, stima della memoria, un thread con la memoria al limite.
  `test_qmean_fallback.py` (20): scelta della versione e fallback su SWISS-MODEL, risorse di HHblits nell'uscita e nel log, migrazione della modalità "auto",
  memoria richiesta dal database (12 GB misurati per UniRef30_2020_06, stima per le altre versioni, giudizio del worker,
  tolleranza del 95%): job fallito subito o fallback con 8 GB, partenza con 11,7 GB; due QMEAN locali eseguiti uno alla
  volta, il secondo in coda con posizione e fase, senza bloccare il pool "io"; QMEAN su SWISS-MODEL senza esclusione, e
  nemmeno senza servizio locale (due fallback insieme, nessuna fase di coda); servizio locale restituito prima del
  fallback (un secondo QMEAN locale parte e finisce mentre il primo aspetta SWISS-MODEL); nota sulla memoria con l'esito
  (`memory_required.sufficient`, anche quando decide il giudizio del worker).
- **File** (`test_fileformats.py`, 8): lettura dei formati di sequenza (FASTA anche allineato, PIR, Clustal, Stockholm,
  GenBank con le CDS, SwissProt, codifiche e compressione) e caricamento di strutture usabili come sorgente.
- **Consenso** (`test_consensus.py`, 69):
  - scale, pesi, fasce, metodi mancanti, discordanze;
  - segnali per residuo, regioni con tolleranza, confini di catena e numerazione;
  - QMEAN e ProSA per residuo;
  - 1UBQ con le uscite reali di MODELLER (`dope_1ubq.profile`), PROCHECK e ProSA;
  - rotta con QMEAN simulato (stessa struttura, altra struttura, template sostituito con lo stesso id, job non
    QMEAN o non completato) e modelli MODELLER simulati;
  - riconoscimento del pLDDT (intestazione AlphaFold sintetica, ModelCIF, nome di AlphaFold DB, file normale,
    valori fuori scala; strutture sperimentali con `REMARK 200 STARTING MODEL: ALPHAFOLD` o
    `_pdbx_initial_refinement_model` AlphaFold, modello MODELLER con un nome `AF-…`) e stesso esito per libreria,
    rapporto di qualità e copia preparata del template sugli stessi file.
- **AlphaFold DB** (`test_alphafold.py`, 50; EBI simulato con `httpx.MockTransport`, NCBI con monkeypatch): parser della
  risposta (campi mancanti, più voci e frammenti, versioni, URL estranei), 404, accessioni non valide senza
  richieste, cache e nuovi tentativi, download PDB/mmCIF, libreria (id, meta, record originali, frammenti, file
  caricati, metadati precedenti completati alla prima lettura), annullamento durante i tentativi del download e
  durante l'attesa dello stesso modello, motore con accessione e con BLAST simulato (estrazione delle accessioni,
  identità 96,1% e copertura su 1UBQ contro l'ubiquitina di lievito; accessione senza modello o EBI non
  disponibile = job fallito con il motivo, risultati parziali con le note e senza cache), cache dei calcoli,
  `best_template` a parità, ritaglio per pLDDT su 1UBQ con B-factor riscritti (11 residui tolti, soglia compresa,
  minimo 30, frammenti isolati di 1 e 3 residui tolti e di 6 tenuti), PIR riportato sul ritaglio, pipeline
  (accessioni da `sp|` e `tr|`) e, con MODELLER, modellazione e pipeline con template ritagliato.
- **Cache dei calcoli** (`test_compute_cache.py`, 39): riscontri senza rieseguire i motori (simulati), invalidazione per
  parametri, sequenza, timbro del database o dello strumento, scadenza, file corrotti, job falliti e annullati,
  riuso nella pipeline e scritture concorrenti.
- **Robustezza del backend** (`test_robustezza_backend.py`, 20): `log_if_present` che non ricrea un job eliminato, e
  avviso per email inviato mentre il job si elimina senza cartelle rimaste; blastp locale che legge l'XML dal file (800
  risultati, oltre 20.000 righe, tutti letti, mentre all'output del processo, troncato, mancano l'intestazione XML e il
  primo risultato) e i suoi errori (uscita con errore, file dei risultati non scritto); tetto della cache dei calcoli
  (voci usate meno di recente eliminate per prime, con l'ordine fissato da `os.utime`; `lookup` che segna la voce come
  usata, con l'orologio di `_touch` spostato avanti di un'ora; stima dello spazio aggiornata a ogni scrittura e scansione
  solo oltre il tetto; tetto applicato dopo la scrittura di `run_cached` e dalla pulizia automatica; impostazione
  validata, e valore scritto a mano che torna al predefinito); errori 500 senza dettagli, con il codice nel log; pagina
  dei risultati di SWISS-MODEL solo https (9 casi: http, `javascript:`, `data:`, senza host, con credenziali, con spazi,
  valori non testuali).
- **Confronto strutturale** (`test_comparison.py`, 29; integrato e TM-align/US-align veri dell'immagine): lDDT calcolato
  a mano (tre Cα, residuo senza corrispondente, raggio e soglie esclusi), invarianza al moto rigido, GDT massimizzato
  per soglia (due gruppi rigidi), 1UBQ contro sé stessa e contro una copia ruotata e traslata (TM 1, GDT 100, lDDT 1,
  matrice e PDB sovrapposto con A e B al loro posto), tratto 31–37 spostato di 6 Å (distanze e lDDT bassi solo lì,
  valori di riferimento), catena A contro B di `ubq_dimer.pdb`, residuo inserito (35A) e Cα mancante (lDDT uguale
  alla formula ricavata a parte), accordo fra i percorsi, colonne non allineate di TM-align (tratto a 12 Å), uscite
  anomale simulate (residui che non corrispondono, matrice mancante), lettura delle due forme della matrice e
  invalidazione delle voci di cache precedenti.
- **Tempo residuo e flussi SSE** (`test_live.py`, 22): formule dell'ETA (arrotondamento, soglie, mai negativa), pipeline
  con durate note e dopo una ripresa, job con progresso; notifiche fra thread; righe intere del log; flusso di un job
  con TestClient (stato, log contiguo, fine, offset, 404, ripresa), limite dei flussi, flusso dell'elenco (job,
  elementi di pipeline, filtro per progetto, eliminazioni, heartbeat), chiusura all'arresto, accesso con chiave
  (401 senza cookie, 200 con cookie o Bearer); con un server uvicorn vero: eventi mentre il job è fermo,
  chiusura dopo `end`, posto liberato alla disconnessione del client, arresto non bloccato dai flussi aperti.
  I test aspettano le condizioni, non tempi fissi: un flusso in attesa di notifiche (`ChangeFeed.waiting`) ha
  già inviato tutto ciò che era stato pubblicato prima.
- **Coda con priorità e budget di CPU** (`test_scheduling.py`, 30), con processi veri che scrivono inizio e fine: mai più
  processi contemporanei degli slot (da job diversi e da thread dello stesso job), annullamento durante l'attesa,
  timeout non consumato dall'attesa e ancora valido per il processo, impostazione dei job in parallelo cambiata
  senza riavvio (budget e pool "cpu"), programmi multi-thread su più slot (fissi ed elastici: questi prendono subito
  gli slot liberi), processi di I/O fuori dal budget, ordine per priorità senza sorpassi, invecchiamento con un orologio
  simulato (budget e coda: una richiesta di pipeline servita entro 2 livelli anche con richieste interattive continue,
  FIFO a parità di priorità effettiva), fase "In attesa di CPU libera" (job, elemento di pipeline, figlio senza fase,
  passi in parallelo), ordine di esecuzione per tipo con un solo worker e FIFO a parità, risorsa esclusiva restituita
  prima della fine (il job successivo parte, la chiusura non libera la risorsa presa da un altro), ripresa che torna al
  suo posto, annullamento e ripresa di un job in coda, job in coda all'arresto marcati da `recover`, eventi invariati,
  posizione nel flusso dell'elenco, pipeline con 4 elementi in parallelo su 2 slot senza deadlock.
- **Analisi in parallelo e template preparati** (`test_parallel_quality.py`, 23): `Steps` (ordine dei risultati diverso
  da quello di conclusione, modalità seriale, passo fallito isolato, annullamento durante l'attesa con passi in coda che
  non partono); rapporto in parallelo identico a quello seriale su 1UBQ (favorite 95,9 %, clashscore 1,66, zDOPE
  −2,73, PROCHECK core 95,5 %), `ubq_dimer.pdb`, `ubq_zn.pdb` e un modello di MODELLER; avvisi dei passi falliti
  uguali, nello stesso ordine, e passo lento non bloccato; annullamento con tre processi veri da 60 s tutti terminati
  in pochi secondi e nessun thread rimasto; mkdssp solo da `ctx.run` con la stessa assegnazione del percorso di
  Biopython; prefissi dei passi nel log; qualità "all" con 3 modelli insieme uguale a quella seriale; cache dei
  template (copia per elemento, chiave con template, catene, ligandi e soglia pLDDT, file sostituito, errori non
  conservati, elementi concorrenti, attesa annullabile) e pipeline di 4 sequenze con una sola preparazione.
- **Report del modello** (`test_report.py`, 58; MODELLER simulato con le coordinate e il profilo DOPE reali di 1UBQ, più
  un test con MODELLER reale): tutte le sezioni; identità 96,1% dell'ubiquitina di lievito su 1UBQ, voto 98,9,
  consenso 99,6; SVG ben formati con 74 punti di Ramachandran e il picco DOPE 73–76; escape di `<script>`, `"` e `&`
  in titoli, nomi, metadati, residui e atomi; PNG valido incorporato, PNG malformati (firma, CRC, troncati, dati
  dopo IEND, senza IDAT, altri tipi) e oltre 3 MB rifiutati; modelli non del job, falliti, job non completato;
  loop; report di un job quality con QMEAN simulato (70–76 sotto 0,6); ZIP della pipeline con un report per
  elemento completato e rotta del report di un elemento; nomi unici nello ZIP ("abc" e "abc-", "x", "x", "x_2", job
  precedenti con nomi ripetuti); nomi di file sicuri (con `'` e `&` dappertutto; con `" < >` solo dove il file system li
  ammette, quindi non su Windows); ogni tipo di report e le sezioni facoltative con sezioni bilanciate
  e mai annidate, id univoci e HTML ben formato (`html.parser`); `_Doc` rifiuta sezioni annidate, chiuse due volte,
  ripetute o aperte a fine documento.
- **Scelta ragionata dei template** (`test_template_rank.py`, 40; RCSB simulato con monkeypatch e `httpx.MockTransport`):
  curva di Rost (valori a 76, 100, 250, 450 residui, asintoto); punteggio dell'ubiquitina di lievito su 1UBQ (95,3)
  con ogni componente; alta identità a bassa risoluzione e R-free alto contro alta risoluzione al 35,5%; NMR e
  crio-EM; modello predetto (pLDDT, metodo, sperimentale che vince a parità e perde con metà copertura); copertura
  parziale; zona crepuscolare dipendente dalla lunghezza e copertura limitata dall'identità; lacune; scala
  dell'E-value; metadati assenti che non penalizzano; ligandi e stato oligomerico informativi; selezione
  complementare (due metà → entrambe, quasi identici → uno solo, frammenti sovrapposti, costrutto più lungo,
  guadagno di identità locale, zona crepuscolare, limite, senza righe dell'allineamento, 400 candidati su 1000
  residui); metadati RCSB con R-free e assemblaggio 1, voci di cache vecchie riscaricate o usate senza rete, risposte
  GraphQL con soli errori; ricerca con punteggi e consigliati anche dalla cache, voci di cache della versione
  precedente non lette, metadati vecchi che non entrano in cache; pipeline multi (template passati a MODELLER,
  colonne, metriche, log, validazione di `max_templates`, filtro sulla copertura dell'insieme); unione degli
  `align2d` sul bersaglio e scelta fra `salign` e `align2d`; con MODELLER reale due frammenti di 1UBQ (sovrapposti e
  disgiunti) → modello completo a TM-score > 0,95 e due template completi che tengono `salign`.
- **Ligandi, cofattori e metalli** (`test_ligands.py`, 27): classificazione (ZN metallo, HOH esclusa, GOL e SO4 additivi,
  HEM cofattore, Na⁺ monovalente, MSE residuo modificato) anche nel dettaglio del template; geometrie ideali e
  incomplete; sito Zn di `ubq_zn.pdb` (valori di riferimento), Zn spostato nella proteina (scontro), distanze lunghe e
  troppo corte; copia preparata con i soli gruppi scelti in fondo alla catena e il sito del file originale; chiave dei
  template preparati che cambia con la scelta; opzioni (validazione, template obbligatorio con più template, pipeline
  automatica); colonne '.' del PIR (automatico, fine esplicita e "+N", '.' dell'utente tolti, catene, più template,
  errori); confronto con il sito del template (conservato, sostituito, non allineato, allineamento che non
  corrisponde); rapporto di qualità e report HTML solo con ligandi; distanze di riferimento uguali alle fonti; con
  MODELLER, ubiquitina di lievito su `ubq_zn.pdb` con il solo Zn trasferito (PIR, HETATM, verifica, report) e lo stesso
  PIR con i ligandi spenti.
- **Disordine e domini** (`test_disorder.py`, 48; UniProt e InterPro simulati con `httpx.MockTransport`): formula di
  FoldIndex calcolata a mano (I, K, A, cariche opposte, X, B), finestra centrata e accorciata alle estremità, FoldIndex
  globale dell'ubiquitina uguale a quello dal GRAVY di Biopython (+0,090), ubiquitina ordinata (frazione 0) e perché la
  finestra fissa al bordo sbaglierebbe, α-sinucleina 98–140 (tolleranza 90–105 sull'inizio), regioni di 30 residui con 3
  tollerati, validazione (5000 residui compresi, 5001 no, acidi nucleici, FASTA con più record); SEG con entropia e P0
  calcolate a mano, tratto poli-Q esatto, X, fusione dei segmenti; domini InterPro fusi e superfamiglie di riserva;
  avvisi di copertura (disordine scoperto per metà, coperto, domini coperti, parziali, scoperti, suggerimenti);
  consenso con le regioni `expected` (metà dei residui) senza variazione di punteggio, segnali e regioni rispetto a una
  sequenza ordinata, 1UBQ, dimero e sito Zn; report con il profilo di FoldIndex, le regioni evidenziate e le fonti; rotte
  `/api/bio/disorder` (errori 400 e 422) e `/api/bio/coverage` con template della libreria, InterPro e cache, tratto di
  UniProt e sequenza diversa, servizi non raggiungibili, accessione sconosciuta o non valida, intervalli della ricerca,
  PIR, template non valutabili, template AlphaFold con il ritaglio pLDDT, verifiche concorrenti sullo stesso template
  senza analisi in cache.
- **Analisi strutturale** (`test_interactions.py` 15, `test_interfaces.py` 10, `test_conservation.py` 14,
  `test_analysis_api.py` 19): legami idrogeno di elica e foglietto di 1UBQ con il confronto con il DSSP integrato, ponti
  salini noti verificati sulle coordinate del file, idrogeni espliciti, geometrie sintetiche (disolfuro, ponte salino,
  legame idrogeno sugli atomi pesanti, coppia elencata una volta, NH₂ posizionati, π-stacking, catione-π) e soglie;
  interfaccia simmetrica e piccola di `ubq_dimer.pdb`, ΔSASA mai negativa, coppie solo fra catene vicine, terza catena,
  bande di plausibilità, annullamento fra i calcoli della SASA; pesi di Henikoff e punteggi calcolati a mano, gap,
  mappatura su 1UBQ (anche con code e inserzioni nella riga), scelta della riga, catene multiple, allineamenti non
  validi, selenocisteina e pirrolisina; prestazioni su 4816 atomi; job e rotte (allineamento da testo, da sequenze non
  allineate e da un job; errori; analisi più recente per sorgente e impronta; job concorrenti), sezione del report
  (dimero sì, 1UBQ no, modelli di un job, escape).
- **Tasche, elettrostatica e rifinitura** (`test_pockets.py` 11, `test_electrostatics.py` 19, `test_refine.py` 12): parser
  sulle uscite reali di fpocket (4 tasche di 1UBQ, ordine per druggability, sfere = sfere dichiarate, baricentro) e di
  PDB2PQR (carica netta 0, residui carichi per tipo, His68 con HD1); griglia come psize (97 × 97 × 129, le dimensioni
  usate da APBS), passo allargato oltre il tetto, input di APBS con ogni parametro; mappe OpenDX sintetiche nel formato
  di APBS e interpolazione esatta su un campo lineare; punti di Shrake-Rupley (atomo isolato, sovrapposti, sepolto);
  potenziale per residuo su 1UBQ con una mappa lineare; copia pulita di tasche ed elettrostatica con MSE e SEP scritte
  come HETATM (tenute, MSE → MET, nessun avviso di gruppi esclusi); elettrostatica con PDB2PQR e APBS simulati su una
  catena senza identificativo (cariche e potenziali per residuo, catena originale nel risultato), lettera libera per la
  catena senza nome accanto a una catena A, SEP tolta da PDB2PQR con il suo avviso; costanti e unità del protocollo,
  copia pulita della rifinitura (Zn escluso, TER all'interruzione 34/38, anche con la catena senza identificativo e
  nessun legame 29–35 nella topologia di PDBFixer), confronto prima/dopo e RMSD (spostamento noto di un Cα); report del
  modello rifinito (sezioni, energia per componente, protocollo, fonti; solo il file rifinito); rilevamento delle
  librerie senza importarle e messaggi degli strumenti mancanti; job falliti con il motivo senza gli strumenti,
  validazione delle rotte prima della coda, priorità. Con gli strumenti (immagine di sviluppo): fpocket su 1UBQ (stesse
  tasche dell'uscita salvata, file conservati, `/latest`) e su `ubq_zn.pdb` (Zn escluso con avviso); PDB2PQR + APBS su
  1UBQ (carica |q| ≤ 1, tutte le Lys esposte positive, almeno 5 Glu su 6 negative, medie separate di oltre 1,5 kT/e,
  residui sepolti senza valore, `.dx` scaricabile, PARSE e AMBER) e annullamento durante APBS (processo terminato);
  rifinitura di 1UBQ con χ1 di Ile30 ruotato (energia diminuita, RMSD < 0,5 Å, clashscore non peggiore, REMARK,
  rapporto di qualità), con 0,5 ps di dinamica, idrogeni e un'interruzione della catena (nessun residuo ricostruito,
  34 e 38 non legati) e annullamento durante la dinamica (processo terminato).
- **Mutagenesi** (`test_mutations.py`, 51): notazione (forme a una e tre lettere, inserzioni, catena omessa o senza
  identificativo, errori), verifica sulla struttura (residuo originale sbagliato, catena inesistente, ligando, residuo
  ripetuto), copia per MODELLER (tratti alle interruzioni, MSE → MET, catena senza nome, ripristino dei nomi), intorni e
  gruppi vicini, indicatori su mutanti grezzi di 1UBQ, regole della classificazione e soglie di DOPE ed energia, hot spot
  (anche con RSA 0 nel complesso, probabile, e con RSA ignota, non sepolto), frammenti di riferimento, scansione (elenco,
  intervalli, esclusi, limiti, ordine, CSV), varianti di UniProt simulate con la mappatura della numerazione (anche con
  le ripetizioni) e la cache, notazione canonica con la catena senza identificativo accanto alla catena B (parametri del
  job, varianti di UniProt, rilettura all'esecuzione), catena «AA» di un mmCIF (siti, partner, residui ottimizzati e
  frammenti con la lettera dei modelli), tasche di un template sostituito rifiutate, controlli preventivi, rotte e
  priorità. Con MODELLER: K48R su 1UBQ (Arg 48, resto invariato, neutra o incerta, report, cronologia), I30P
  (destabilizzante, elica), scansione di 3 residui ordinata, D58A sul dimero (DOPE fra le catene), catena senza
  identificativo con un'interruzione, mutagenesi e scansione di una catena «AA» con le etichette «AA:…», annullamento
  durante MODELLER; con OpenMM l'energia di K48R.
- **Confronto con un predittore esterno** (`test_predict.py`, 74; ESMFold e AlphaFold DB simulati con
  `httpx.MockTransport` nel formato reale del servizio): client (pLDDT su 0–1 riportato a 0–100 e su 0–100 lasciato
  com'è, TITLE aggiunto se manca, 404/413/400/429/503/tempo scaduto con il codice e i tentativi giusti, nuovo tentativo
  riuscito, risposte non valide, troppo grandi o di un'altra sequenza, sequenza troppo lunga, con lettere non standard,
  corta o vuota senza richieste, 400 residui compresi, cache per sequenza e servizio, indirizzi non validi); consenso
  all'invio (impostazioni precedenti e valori strani → spento senza azzerare le altre, rotta delle impostazioni, 403
  senza job né richieste, job accodato senza consenso fallito senza invio); validazione prima della coda; riconoscitore
  unico (ESMFold sì, file grezzo 0–1, EXPDTA, MODELLER, REMARK, altro TITLE e mmCIF no, AlphaFold ancora sì; 1UBQ,
  `ubq_dimer.pdb`, `ubq_zn.pdb` no; rapporto di qualità, libreria «Modello predetto (ESMFold)» e copia preparata con il
  ritaglio e il TITLE); plausibilità su 1UBQ con il tratto 31–37 spostato (predizione sicura → divergenze locali,
  incerta → concordano, copia identica, incrocio con il consenso), ripiegamento diverso, non conclusivo, regioni con
  tolleranza, confini di catena e di numerazione, scala ed errori del file; consenso identico senza confronto, metodo
  con peso 0,25 e punteggio dalla formula, residui segnalati agli indici del rapporto, regioni e segnali invariati,
  metodo fra i mancanti se non conclusivo, discordanza con il voto, regione problematica marcata; priorità e pool; job
  end-to-end (file riconosciuto dall'analisi di qualità, più recente, cache al secondo job), AlphaFold DB, tre job
  insieme con una cache valida; rotte del consenso e del report (sezione bilanciata, citazioni, struttura diversa 400,
  job non predict o senza confronto 400), markup della sezione con l'escape; controlli preventivi (consenso, lunghezza,
  accessione, cache, tempi, nessuna richiesta remota); sequenza di una catena interrotta con una MSE (giunzioni, SEQRES
  che corrisponde, che non corrisponde o troppo lungo, numerazione che salta senza residui mancanti), giunzioni mai
  divergenti, job con il SEQRES (sequenza completa inviata) e senza (avviso nei controlli e nel risultato); template di
  ESMFold chiamato ESMFold in log, note, REMARK, report e controlli preventivi.
- **Complessi e omo-oligomeri** (`test_assembly.py` 34, `test_complexes.py` 40): espressioni degli operatori (elenchi,
  intervalli, prodotti, errori, limite delle combinazioni) e prodotti applicati da destra a sinistra; mmCIF sintetico con
  identità + rotazione C2 → la catena B di `ubq_dimer.pdb` entro 0,01 Å, acque della stessa catena dell'autore incluse
  o escluse secondo l'asym, prodotti "(1,2)(3)"; REMARK 350 di 1UBQ e con righe AND CHAINS, BIOMT incompleti, nomi delle
  copie (catene riservate, catene di più caratteri), limite delle 62 catene, PDB generato con intestazione, REMARK e
  stessa interfaccia; rotte della libreria (elenco, importazione, riuso, rigenerazione con il file cambiato,
  assemblaggio di un assemblaggio, errori, importazioni concorrenti); id degli assemblaggi senza troncamento
  (assemblaggi 1, 2 e 3 di un template di 37 caratteri distinti, 409 per un omonimo che è un altro assemblaggio);
  bersaglio a più catene (`same_as`, errori della corrispondenza, limiti di 12 catene e 3000 residui compresi), gruppi
  di simmetria, unione degli allineamenti per catena, coerenza delle catene di un PIR (errore nella modalità Complesso,
  avviso altrimenti), script con simmetria e nomi delle catene, peso validato, copia preparata nell'ordine del
  bersaglio, nomi e simmetria del piano del modello; confronto delle interfacce (dimero contro sé stesso con catene
  rinominate e numeri spostati, moto rigido, catena spostata di 3 Å con DockQ dalla formula, catene non più a contatto,
  interfaccia nuova, PIR che non corrisponde, template a tre catene con un PIR da A a B, catena allineata solo a gap,
  errore inatteso dell'analisi che non fa fallire il job), sezione del report bilanciata con l'escape; rotte con
  complessi non validi (nessun job accodato); con MODELLER l'anteprima per catena e l'omodimero dell'ubiquitina di
  lievito con e senza simmetria (catene, numerazione, RMSD fra le catene, DockQ, REMARK, report) e dallo stesso PIR.
- **Controlli preventivi** (`test_preflight.py`, 64; licenze, strumenti, spazio e worker QMEAN simulati): ogni codice con
  un caso che lo produce e uno che non lo produce; curva di Rost (33,2% a 76 residui, 20,0% a 300) e soglia di Baker &
  Šali; stime contro le misure (job model 13,89 s, elemento di pipeline 9,13 s, i quattro livelli di raffinamento
  distinti e in ordine, 304 residui, byte dei risultati e degli intermedi); modelli identici con «rapidissimo»;
  ubiquitina di lievito su 1UBQ senza errori né avvisi (identità 96,1%, copertura 100%) e nessun job avviato; 1UBQ al 25%
  di identità (zona crepuscolare), bersaglio con una coda disordinata (copertura e disordine), PIR a una e a due catene,
  template assenti, scaricabili o con una catena sbagliata (nessun download), ritaglio pLDDT (11 residui, 25 rimasti, più
  della metà, ritaglio spento), timeout, spazio, PROCHECK e ProSA (licenza, MAC), coda, punteggio dalla ricerca, stessa
  validazione della rotta di avvio (stesso messaggio), controlli concorrenti; QMEAN con 8 e 11,7 GiB per
  UniRef30_2020_06 e fallback; motori di ricerca, pipeline con 51 ricerche NCBI, accessioni AlphaFold, sequenze lunghe e
  disordinate, spazio.
- **Migrazioni** (`test_migrations.py`, 14), con un database della versione 1 generato da `db.SCHEMA` con dati in tutte
  le tabelle: passaggio alla 2 senza righe perse né cambiate, copia di sicurezza integra con lo schema 1 (anche con le
  righe rimaste solo nel WAL), riapertura senza nuove copie, migrazione ripetuta dopo che la versione 4.1.0 ha
  riscritto `schema_version` a 1 (etichette conservate), errore a metà della migrazione (database, tabelle e versione
  invariati), errore nella migrazione successiva (resta la precedente), nessuna migrazione senza copia, database nuovo,
  schema più recente rifiutato, seconda istanza, trigger di pulizia (anche in cascata) e vincolo sui tipi, avvio
  dell'applicazione su una cartella dati della versione 1, scritture concorrenti.
- **Etichette e preferiti** (`test_labels.py`, 35): normalizzazione (forme equivalenti, accenti composti e scomposti,
  "zn2+", 32 caratteri, caratteri rifiutati), rotte per i quattro tipi, errori (404, 422, 400, limite di 20), preferiti,
  elenchi con `tags`/`favorite` e filtri `?tag=`/`?favorite=` (job filtrati prima del limite), pulizia alla
  cancellazione di sequenze, progetti (job e sequenze in cascata), job e template (anche cancellati a mano dalla
  cartella; cartella assente non toccata), aggiunte concorrenti (limite rispettato, stessa etichetta una volta),
  template eliminato durante l'aggiunta.
- **Ricerca globale** (`test_global_search.py`, 73; ogni test con FTS5 e con LIKE): parole e query FTS5/LIKE con escape,
  tabella dei punteggi, organismo dalle intestazioni PDB/mmCIF, ordine per pertinenza (Chinasi 13, «Chinasi di
  lievito» 7, descrizione 1,5), accenti e maiuscole, accessione, gruppi (documento per nome, job per tipo, template per
  id, titolo e organismo), limite e `truncated`, 22 richieste con caratteri speciali e tentativi di iniezione SQL e FTS5
  (tabelle intatte, nessun risultato "tutto"), operatori come parole, indice allineato a modifiche, eliminazioni e
  cascate, template aggiunti e tolti a mano dalla cartella, gruppo delle etichette e filtro `tag`, preferiti in testa e
  senza testo, stessi risultati dai due motori, indice di 10 000 job, ripiego senza FTS5 e nessun oggetto FTS5 nel file;
  ogni tipo di job registrato con un'etichetta uguale a quella del frontend, job dei tipi nuovi trovati per etichetta
  («rifinitura», «tasche», «mutagenesi», …) con FTS5 e con LIKE.
- **Riproducibilità** (`test_reproducibility.py`, 59): nomi pericolosi (`..`, assoluti, unità di Windows, barre
  rovesciate, nascosti, riservati, profondità), collegamenti simbolici, file speciali, duplicati anche per maiuscole,
  cifratura, bomba di decompressione (4 MB di zeri, circa 1000:1) e limiti di numero e dimensione; impronta del manifest
  (parametri cambiati, formato più recente, percorsi non validi); manifest risigillati di forma inattesa
  (`environment.libraries`/`tools` elenchi, `python` oggetto, `notes` numero, `remote` di testi, durata testuale,
  `rerun` elenco) rifiutati con `package_manifest_invalid` in anteprima, controlli preventivi e avvio, parametri liberi
  di una ricerca senza errore interno; oscuramento (segreti senza maiuscole, percorsi solo all'inizio,
  «https://data.rcsb.org» intatto); confronto delle coordinate di 1UBQ senza sovrapposizione (660 atomi, traslazione di
  0,5 Å → RMSD 0,5; un atomo spostato di 0,66 Å → RMSD 0,66/√660 e scarto massimo con l'atomo; 0,001 Å entro la
  tolleranza; acque mancanti), data di MODELLER ignorata, JSON e testi con la tolleranza, esito (anche «compatibile» con
  risultati simulati entro e oltre le tolleranze scientifiche: catena principale, energia, file mancante, classe della
  mutagenesi cambiata), avvisi sulle versioni, versioni lette dai programmi; pacchetto di un'analisi di qualità
  (manifest, checksum di ogni file, input, LEGGIMI), nessun segreto in nessun file con chiave MODELLER, email e chiave
  d'accesso del test scritte anche nel log (controllo programmatico che elenca solo i file), file grande comprimibile
  salvato senza compressione, errori (job in esecuzione 409, tipo non supportato, inesistente); ricerca RCSB simulata:
  input remoti con la data e riesecuzione rifiutata; manifest alterato, checksum errato, file non dichiarati o mancanti,
  ZIP non valido, path traversal, collegamento simbolico e bomba rifiutati senza scrivere nulla né creare job; script
  del pacchetto mai estratti; riesecuzione identica di qualità, allineamento strutturale (file caricato poi eliminato) e
  analisi strutturale (job di allineamento eliminato), con anteprima, controlli preventivi (stima, pacchetto scaduto,
  corpo non valido) e due avvii concorrenti dello stesso pacchetto; versione diversa come avviso; tipo non rieseguibile
  e parametri non validi in un manifest risigillato; pacchetto caricato eliminato dalla pulizia dopo 24 ore; comando
  `rerun` in un sottoprocesso con una cartella dati temporanea (codice 0 e rapporto; 1 se diverso; 2 con un checksum
  errato e «Riesecuzione non riuscita: …»); con MODELLER modellazione (seme −8123, script, template preparati, PIR)
  rieseguita identica e, con un altro seme, diversa con RMSD e DOPE quantificati, align2d identico, pacchetto e
  riesecuzione di K48R e della scansione di una tasca (tasche dal pacchetto dopo l'eliminazione del job); con OpenMM
  rifinitura identica con un thread; pacchetto del confronto con ESMFold rifiutato come non riproducibile, con la
  predizione come input remoto datato.
- **Pipeline M × N** (`test_pipeline_cross.py`, 7; MODELLER simulato come in `test_pipeline_resume.py`): ogni sequenza
  modellata su ogni template fisso, ogni coppia analizzata e il migliore scelto per zDOPE, migliore segnato ma non
  analizzato senza qualità, ZIP con una cartella per template, coppia fallita e ripresa che rifà la scelta, validazione,
  matrice solo per le pipeline incrociate.
- **Pipeline grandi** (`test_large_pipeline.py`, 14; `test_pipeline_scala.py`: file e pipeline da 100.000 sequenze,
  pulizia delle sequenze, annullamento, matrice a pagine, risposte senza il FASTA intero): fino a 100.000 sequenze, parallelismo dalle Impostazioni oltre 8
  (fino a 256), processi di appoggio dell'analisi (`core/offload` e `domain/qa_steps`: stesso rapporto della versione nel
  processo principale, ripiego sul processo principale se il pool fallisce, risultati parziali completati), output dei
  programmi in `programmi.log` dell'elemento e non nel log del job, parametri letti una volta, elenco dei file limitato.
- **Avviso per email** (`test_notify.py`, 15; server SMTP finto in locale): configurazione solo da file o variabili
  d'ambiente e password mai restituita, file non valido spiegato, email di prova, autenticazione e destinatario
  rifiutati, nuovi tentativi, un solo avviso a fine pipeline senza risultati, nessun avviso se non chiesto, invio fallito
  scritto nel log del job, intestazioni non iniettabili.
- **Editor PIR, struttura secondaria per residuo** (`test_pir_secondary.py`, 15): DSSP dei template e GOR del bersaglio,
  gap che non cambiano il risultato, residuo iniziale dall'intestazione, due catene dopo '/', selenometionine secondo
  l'opzione dei ligandi, lettere diverse dal file segnalate, abbinamento come in modellazione, predizione che non
  scavalca le catene, limiti della richiesta, cache delle strutture.
- **Grafici di PROCHECK** (`test_procheck_plots.py`, 5): grafici PostScript conservati secondo `procheck_plots` (tutti, solo
  il Ramachandran complessivo, nessuno) con le stesse metriche; impostazione salvata e validata dall'API.
- **Acque catalitiche e strutturali** (`test_waters.py`, 15; `tests/data/ubq_zn.pdb`): acque suggerite sullo zinco e a
  ponte con un ligando vero, solvente lontano escluso, trasferite solo se scelte, copia preparata con le acque dopo i
  ligandi (WAT scritta come HOH), colonne '.' in BioLoom e 'w' per MODELLER (stessa colonna per la stessa acqua di due
  template), verifica dell'acqua nel modello e dell'acqua che ha perso il metallo, acque nell'analisi del template,
  esempi con template HETATM; con MODELLER, ubiquitina di lievito su `ubq_zn.pdb` con lo Zn e la sua acqua catalitica.
- **Sessione PyMOL** (`test_exports.py`, 28): il blocco `python … python end` della sessione eseguito davvero, con un
  `cmd` finto, per verificare che la struttura arrivi identica: barre rovesciate (sequenze come `\t` e `\1`, escape
  Unicode, `C:\Users\…` in un REMARK), virgolette finali e tre virgolette, che non chiudono il testo e non diventano più
  tre apici; testo incorporato su una sola riga ASCII anche con «python end», `\r`, altri separatori di riga e caratteri
  non ASCII nel file; nome dell'oggetto sicuro per i comandi di PyMOL (7 casi: punti, virgolette, comandi, non ASCII,
  lungo, vuoto); schemi di colore, e schema sconosciuto che torna alla struttura secondaria; la rotta
  `/api/jobs/{id}/pymol/{modello}` (sessione di un modello, file assente e percorso fuori dalla cartella del job, nome
  del file scaricato ripulito come quello dei report: con caratteri ammessi anche da NTFS gira dappertutto, con `"<>`
  si salta su Windows). Il codice: `domain/exports.pymol_script` incorpora il testo con `ascii()` (`repr()` con gli
  escape anche per i caratteri non ASCII) in una sola riga `cmd.read_pdbstr(…)`, quindi nessuna riga del file può
  chiudere il blocco `python` prima del tempo né aggiungere comandi, e lo script `.pml` è tutto ASCII;
  `api/jobs.py::model_pymol` passa il nome da `report.safe_filename(name, ".pml", "bioloom_model")`. Prima le barre
  rovesciate arrivavano a PyMOL come sequenze di escape, `C:\Users\…` o una virgoletta finale davano SyntaxError, le tre
  virgolette diventavano tre apici, e un nome con `"` o non codificabile nell'intestazione (anche su Windows, per
  esempio `modello finale;€ (1).pdb`) dava 500. Limiti: il `.pml` non è stato provato con un PyMOL vero (la riga è lunga
  quanto il PDB); il nome del file scaricato può differire dal nome dell'oggetto e dalla riga «Apri con:» dello script
  (`safe_filename` comprime i `_`: `y_a_b.pml` contro `y__a_b_`), mentre per i nomi normali come `1UBQ_B99990001`
  coincidono.
- **Rete bloccata** (`test_rete_bloccata.py`, 22 più uno con il marcatore `network`): connessioni reali (httpx, nomi,
  socket) che falliscono subito con il messaggio e fanno fallire il test; `MockTransport` e server locali che
  funzionano; che cosa conta come locale (19 casi: loopback IPv4 e IPv6, IPv4 scritto come IPv6, 0.0.0.0, `localhost` e
  `*.localhost` anche con maiuscole e punto finale; non locali gli indirizzi privati e pubblici, `rest.uniprot.org`,
  `qmean`, `localhost.example.org`); marcatore `network` dichiarato ed escluso per default (`test_remote.py` resta
  escluso), e il test marcato, escluso anche lui, che verifica che il blocco non valga per i test `network`.
- **Servizi reali** (`test_remote.py`, 4, solo con `-m network`): ricerca RCSB dell'ubiquitina, voce e domini UniProt,
  metadati RCSB con R-free e stato oligomerico.

**Frontend** (logica pura in `lib/`, markup statico dei componenti con `react-dom/server` e, nei file con
`// @vitest-environment jsdom`, componenti montati con @testing-library/react; numero di test fra parentesi):

- **Formati, file e stato:** `pir.test.ts` (3: parser PIR, identità e copertura, formattazione); `files.test.ts` (8:
  decodifica dei file, chiave MODELLER da file, vincoli da e verso file, nomi dei record); `format.test.ts` (16: numeri
  all'italiana, nota "dalla cache", ricerche riusate, posizione in coda "2° in coda" solo per i job in coda, slot di
  calcolo in uso, tempo residuo leggibile `fmtEta`); `status.test.ts` (23: logica QMEAN locale/remoto/fallback e memoria
  del servizio rispetto al requisito con l'esito del server — sufficiente, insufficiente, al limite, 11,7 GB su 12,
  giudizio "ok" del worker al posto di una stima più alta —, avvisi delle licenze, pulsanti di ripresa delle pipeline e
  ricarica del job dopo l'annullamento e dopo la ripresa); `ui.test.tsx` (2: numeri all'italiana nei componenti).
- **Tempo reale** (`live.test.ts`, 21, con flussi e timer finti): scelta fra flusso e polling, riconnessione con backoff,
  timeout di connessione, fine del flusso, flusso condiviso dell'elenco, ricomposizione del log (anche con polling e
  flusso sovrapposti) e aggiornamento degli elenchi, anche dopo una ricarica lenta, niente polling per un job che il
  server non ha (404).
- **Colori e qualità per residuo:** `colorscale.test.ts` (9: scale e legende, fasce con limite escluso o compreso),
  `residueColoring.test.ts` (12: conversione del rapporto nei valori per residuo, pLDDT con le fasce del backend),
  `qualityReport.test.ts` (5: residui problematici e struttura del rapporto mostrato), `alignConfidence.test.ts` (9:
  confidenza degli allineamenti).
- **Confronto strutturale** (`compare.test.ts`, 17): sorgenti nei link (andata e ritorno, valori non validi),
  destinazioni di «Confronta…» dai modelli, scala delle distanze saturata a 5 Å, colore fisso di A e colore per catena,
  coppia di un residuo scelto nel 3D, interruzioni del grafico (salti e codici di inserzione), tratti che deviano,
  ordinamento e filtro della tabella, lettura del TM-score, scorrimento del solo contenitore della tabella alla riga
  selezionata, risultati di versioni precedenti riconosciuti da `comparison_version` (non dall'assenza di una nota), job
  del link non trovato nella pagina Confronto. `picker.test.ts` (7): job e modello arrivati da un link (elemento di
  pipeline, job loop, job non tra gli ultimi 30) restano visibili come scelti, risultati senza elenco dei modelli.
- **Pipeline e report:** `PipelineItemActions.test.tsx` (3): report del modello in ogni riga completata, disattivato per
  le altre, e report con la vista 3D dalla finestra «Visualizza». `report.test.ts` (9): vista 3D inviata solo se è un PNG
  entro 3 MB, motivo della vista mancante solo nell'avviso del salvataggio riuscito, nome del file da
  Content-Disposition (senza percorsi) e nome di riserva come quello del server.
- **Template:** `alphafold.test.ts` (15): accessioni e id `AF-…`, accessione precompilata che segue la sequenza,
  intestazioni `sp|`/`tr|` della pipeline, fasce di pLDDT, quando mostrare la soglia, riepilogo del ritaglio (anche con i
  frammenti isolati). `templateRank.test.ts` (8): fasce come nel backend, righe e ordine delle motivazioni, tooltip, ruoli
  dei consigliati (anche per i risultati precedenti, dal solo `best`), collegamento alla Modellazione con un template per
  `t`, numero di template della modalità multi, template e suggerimento di un elemento di pipeline. `ligands.test.ts`
  (12): candidati delle catene scelte, additivi e ioni monovalenti deselezionati per default, residui modificati non
  selezionabili, scelte dell'utente conservate, analisi in cache precedenti, testi della coordinazione, linee metallo →
  donatore colorate per esito (con sfera per le acque) e residui evidenziati.
- **Disordine e domini** (`disorder.test.ts` 10, `CoverageAlerts.test.tsx` 3): tracce della mappa (disordine, bassa
  complessità, domini solo con l'accessione, GOR con il colore dello stato, tracce vuote), sintesi, accessione del
  bersaglio (sequenza del progetto o intestazione `sp|`), richieste di Modellazione (template senza catene esclusi, PIR,
  soglia pLDDT) e Ricerca (un intervallo per hit), ordine degli avvisi, badge degli hit, riga di sintesi; markup degli
  avvisi e della mappa.
- **Analisi strutturale** (`interactions.test.ts` 14, `StructuralAnalysis.test.tsx` 3): righe uniformi delle interazioni,
  filtri per catena e per residuo (una lettera come catena o amminoacido, codici di inserzione, testo libero), residui da
  evidenziare, scale della conservazione e dell'interfaccia (soglia di 1 Å² esclusa), opzioni di colorazione, richiesta
  dell'allineamento e FASTA da un file letto dal server; markup della vista (tabella dei legami idrogeno, interfaccia e
  avvisi, catene che non si toccano, file della struttura per il viewer).
- **Tasche, elettrostatica e rifinitura** (`pockets.test.ts` 6, `electrostatics.test.ts` 4, `refine.test.ts` 6): soglia
  di druggability, colori per posto, residuo condiviso fra due tasche, tasca scelta, sfere e centri, tasca di un residuo;
  scala ±5 kT/e saturata, kT/e in mV, colori per segno e residui sepolti, residui estremi e cariche per tipo; richiesta
  della rifinitura, link a Qualità e Confronto (originale come A), differenze ed esiti, energia; job conservato per
  struttura e strumenti mancanti da `/api/system/info`.
- **Mutagenesi** (`mutations.test.ts` 11, `Mutagenesis.test.tsx` 2): notazione come il backend, aggiunta delle varianti
  senza doppioni, richieste di mutagenesi e scansione con i controlli preventivi, testi di DOPE, energia e sito, link a
  Qualità e Confronto (selvatico riottimizzato e originale come A), filtri e colorazione della scansione per classe con
  gli esclusi, varianti di UniProt (applicabili prima, filtri, ricerca) e cronologia (stato in italiano); stato della
  scelta delle tasche (in lettura, errore, nessun calcolo, in corso, non riuscito, pronto); siti nel 3D con la catena
  dei modelli; markup dei risultati del mutante (classe, segnali pesati, siti, link, avvertenza) e della scansione
  (ordine, esclusi, CSV).
- **Complessi** (`complex.test.ts` 14, `ComplexResults.test.tsx` 4): righe per catena con «stessa della catena …» proposta
  per le catene uguali, sequenze pulite, richiesta del complesso con gli errori del backend (corrispondenza incompleta,
  limiti compresi), simmetria solo con catene uguali, allineamento per catena dell'anteprima, colorazione per catena con
  i residui d'interfaccia conservati, nuovi e persi, residui da centrare, testi degli assemblaggi; markup della tabella
  del confronto.
- **Controlli preventivi e procedura guidata** (`preflight.test.ts` 9, `Preflight.test.tsx` 3, `wizard.test.ts` 20): esiti
  per livello e copertura a parte, decisione del pulsante (bloccato, conferma, pronto, in corso, non riuscito), testi e
  tempi come il backend, richieste di Modellazione e Pipeline; markup del riquadro (copertura una volta sola);
  validazione della sequenza, preselezione dei consigliati una volta per ricerca, una catena per voce PDB,
  invalidazione dei passi successivi, motore consigliato, opzioni predefinite e spiegazioni, richieste delle rotte, passi
  raggiungibili, URL (andata e ritorno, valori non validi, indietro del browser), bozza non valida, ripresa da un link
  senza bozza, risultati.
- **Confronto con una predizione** (`predict.test.ts` 10, `Predictor.test.tsx` 4): consenso chiesto solo per ESMFold e
  solo se manca (anche con le impostazioni non ancora lette), testo del consenso, indirizzo effettivo del servizio,
  richiesta e controlli preventivi, limite di 400 residui come il backend, job seguito (scelto, in corso, più recente),
  esito, metriche e origine, link a Qualità e Confronto (modello come A), nota del consenso; markup dei risultati (esito
  con il tono, regioni, link, escape, senza confronto); oltre 400 residui nessun consenso né avvio (sequenza incollata o
  catena), sequenza completa incollata (FASTA o testo) nella richiesta; markup della finestra di avvio (avviso oltre il
  limite, campo della sequenza); distintivo del predittore dei template (ESMFold, AlphaFold, generico).
- **Riproducibilità** (`rerun.test.ts`, 11): tipi con un pacchetto e rieseguibili, contenuto per ruolo, versioni nel
  pacchetto e qui (differenze, strumenti assenti o non usati), parametri leggibili, richiesta dei controlli preventivi
  solo per un pacchetto rieseguibile, valori e differenze formattati, ordine e dettaglio dei file del confronto (RMSD,
  numeri oltre la tolleranza, testi, struttura), nota sulle tolleranze; mutagenesi e scansione rieseguibili, predizione
  solo esportabile; esito «compatibile» con le righe dei controlli delle tolleranze scientifiche.
- **Eliminazione di progetti e pulizia dei job** (`cleanup.test.ts` 45, `projectSelection.test.ts` 3,
  `JobCleanupDialog.test.tsx` 8, `ProjectsDialog.test.tsx` 9; chiamate e timer finti): valori predefiniti sicuri,
  ordine degli stati e «Ho capito» azzerato togliendo i completati, nessuna richiesta senza stati o con «Questo
  progetto» senza un progetto (mai «tutti» al suo posto), nessun `project_id` con «Tutti i progetti», chiave dei criteri
  (senza `max_seconds` né `as_of`); conferma possibile solo con l'anteprima pronta per i criteri attuali (superata,
  vuota, in corso, in errore, completati senza «Ho capito»); esecuzione con l'`as_of` dell'anteprima pronta per i
  criteri attuali, mai di un'altra; «Questo progetto» fissato all'apertura (non segue un nuovo progetto attivo, elenco
  non ancora letto, «Ho capito» azzerato al cambio di destinazione); anteprima con debounce (tre richieste ravvicinate →
  una chiamata, una sola in volo con la più recente alla fine, risposta superata scartata, criteri uguali, richiesta
  nulla, errore e «Riprova», arresto); ciclo a tempo (somme, saltati dell'ultima chiamata, `max_seconds` 5, `as_of` in
  ogni chiamata, fine a `remaining` 0, «Interrompi» fra le chiamate, `stalled`, errore con i totali parziali, risposta
  senza la forma attesa ed eccezione di una callback come `error`, tetto di chiamate), avanzamento, riepiloghi (con i
  job saltati e i loro motivi) e tono per ogni esito; plurali, nome da scrivere (spazi, maiuscole, forme Unicode),
  blocco con job attivi, svuotamento senza eccezioni, notifica con e senza progetto sostitutivo, flusso
  dell'eliminazione (senza job solo il DELETE, svuotamento poi DELETE, svuotamento incompleto o con una risposta non
  valida senza DELETE, «Interrompi» durante l'ultima chiamata dello svuotamento senza DELETE, 409 e 503
  `shutting_down` rifiutati, rete o altri 5xx da verificare); progetto attivo dopo la rilettura (presente, assente,
  elenco vuoto → nessuno); markup del dialogo della pulizia (predefiniti, conferma, progetto attivo cambiato dopo
  l'apertura, avviso dei completati, barra accessibile e regione annunciata, interruzione, riepilogo come stato o come
  alert) e della conferma dell'eliminazione (job attivi, progetto vuoto con il focus su «Annulla», nome da scrivere,
  byte parziali, ultimo progetto, in corso, errori, nome accessibile del cestino, mai la parola «cestino» nel testo).
- **Editor PIR e allineamenti:** `pirEdit.test.ts` (19: gap inseriti e tolti senza mai togliere residui, blocchi
  spostati, pareggio delle righe), `pirMutations.test.ts` (16: matrice e toni del punteggio, sostituzioni numerate per
  catena dalla seconda riga PIR, posizioni per colonna), `pirIssues.test.ts` (9: separatori non allineati, colonne di
  soli gap, residui senza template, lettere diverse dal file, F8 e Maiusc+F8), `pirSecondary.test.ts` (9: DSSP a 3
  stati, inserzioni dentro un'elica e delezioni di fronte a un filamento), `pirColors.test.ts` (6: idropatia,
  conservazione per colonna, code del bersaglio senza template), `pirDiff.test.ts` (5), `pirAnnotations.test.ts` (3),
  `pirHeader.test.ts` (3: i dieci campi dell'intestazione), `pirTrackContrast.test.ts` (2), `alignmentView.test.ts` (8:
  righello, colonne identiche, contrasto ≥ 4,5:1 nei due temi), `alignmentFasta.test.ts` (3), `AlignmentView.test.tsx`
  (5).
- **Grafici e cornici:** `linear.test.tsx` (14: profili per residuo con cornice, PNG, CSV, zoom, struttura secondaria,
  regioni attese tratteggiate, heatmap), `linearHelpers.test.ts` (10: tacche, intervalli visibili, testo nero o bianco
  ≥ 4,5:1), `Ramachandran.test.tsx` (7: regioni, glicina a triangolo, outlier etichettati, residuo selezionato, zoom),
  `ramachandran.test.ts` (10: classi, regioni, conteggi, ricerca per residuo), `ChartFrame.test.tsx` (6),
  `ValueBars.test.tsx` (4), `ModelScores.test.tsx` (4: il migliore distinto per forma e non solo per colore),
  `Viewer3D.test.tsx` (2), `zoom.test.ts` (5) e `zoomInput.test.ts` (5: dominio, rotella e touchpad),
  `exportImage.test.ts` (2: nome sicuro del file, CSV), `viewerImage.test.ts` (5: nome e data URI della vista 3D).
- **Viste delle pagine:** `quality.test.tsx` (19: consenso e voto riga per riga, Ramachandran, struttura secondaria,
  dettaglio del residuo, tabella dei residui, contatti sterici), `qualityView.test.ts` (17: penalità del voto con la
  regola, soglie di clashscore, zDOPE, QMEAN, ProSA e G-factor, contributi del consenso), `StructuralComparison.test.tsx`
  (5) e `compareMetrics.test.ts` (12: letture di TM-score, RMSD, GDT, lDDT con soglie e fonti), `ToolPanels.test.tsx` (9:
  barre, avanzamento e log dei job, descrittori delle tasche, ΔSASA), `toolsView.test.ts` (12: assi, ordine dei residui,
  tasche, potenziale, accordo con la predizione), `WorkflowParts.test.tsx` (7) e `workflowView.test.ts` (11: punteggio
  dei template con le componenti, copertura, controlli, sequenza a blocchi), `shellView.test.ts` (13: Panoramica, passi
  del flusso, stati e log), `pipelineMatrix.test.ts` (4: pipeline M × N), `notify.test.ts` (2: indirizzi e stato del
  server di posta), `residueTerms.test.ts` (3).
- **Componenti di base e accessibilità:** `foundation.test.tsx` (14: card e sezioni richiudibili montate alla prima
  apertura, gruppi di comandi con un nome, esito scritto oltre al colore), `contrast.test.ts` (11: testi ≥ 4,5:1 su ogni
  superficie nei due temi), `chartTokens.test.ts` (20) e `sequenceTokens.test.ts` (4): colori dei grafici e delle classi
  di residui ≥ 3:1 e anello del focus nei due temi; per il visualizzatore 3D fondo e comandi del tema (`--viewer-*`,
  testi ≥ 4,5:1, regole senza colori fissi, anello pieno sui pulsanti) e grigio di contesto ≥ 3:1 sul fondo, sotto la
  legenda e sui pannelli, usato dalle cinque colorazioni; varianti del tema chiaro almeno 3:1 sul fondo del 3D, sotto la
  legenda e sui pannelli, tema scuro invariato, A contro «B senza coppia» almeno 2,5:1 e 25 di ΔE CIE76 (visione
  normale, deuteranopia e protanopia simulate con Machado 2009), A nel tema chiaro almeno 25 contro tutta la scala delle
  distanze, «1 metodo» almeno 12 contro le altre fasce, gradienti continui non scuriti, contorno solo nel tema chiaro e
  almeno 3:1, regola CSS del bordo della legenda; `aminoAcids.test.ts` (3: una sola tabella delle classi),
  `formatHelpers.test.ts` (4).
- **Errori dell'interfaccia e caricamento dei template** (`ErrorBoundary.test.tsx` 8, `templateUpload.test.ts` 5):
  sessionStorage sicuro (dati del sito bloccati: lettura null e scrittura non riuscita senza eccezioni; senza
  sessionStorage e con la scrittura che fallisce; storage funzionante); ricarica per i moduli obsoleti una volta e poi
  non prima di un minuto, con il segno delle versioni precedenti («1») o illeggibile, mai con i dati del sito bloccati
  né per gli altri errori, e disegno senza errori che non tocca lo storage; conferma chiesta solo al 409
  `template_exists` del primo tentativo, mai dopo «Sostituisci», altri errori che restano errori, campo `overwrite`
  solo per la sostituzione, 409 del server che diventa la domanda.
- **Navigazione, tema, ricerca ed etichette:** `virtual.test.ts` (5: soglia, posizioni con altezza fissa e per indice,
  righe visibili con margine e spaziatori, fondo e scorrimento oltre il fondo, blocchi dell'allineamento, riga
  selezionata portata in vista sotto l'intestazione fissa e nei limiti), `theme.test.ts` (5: valori salvati vecchi e
  nuovi, valori illeggibili, tema del sistema, ciclo, tema prima del render), `search.test.ts` (14: `#etichetta`,
  `tipo:`, testo, chiave della richiesta, elenco piatto, frecce con giro e pagine, destinazioni, evidenza),
  `shortcuts.test.ts` (6: Ctrl+K e ⌘K, `/`, `?`, `g` + lettera, campi di testo e dialoghi aperti), `tags.test.ts` (8:
  stessa normalizzazione del backend, filtro degli elenchi), `Layout.test.tsx` (3: pulsante del menu e barra laterale
  sugli schermi stretti, griglia dei passi della Panoramica).
- **Interfaccia montata (jsdom)**, con @testing-library/react e gli strumenti di `src/test/dom.ts`:
  `CopyButton.test.tsx` (5: Clipboard API, textarea temporanea con `execCommand` e focus restituito, `writeText`
  rifiutato, «Non copiato» con la notifica, fuori dall'app senza eccezioni); `TemplateUpload.test.tsx` (2: 409 →
  domanda → «Sostituisci» con il secondo invio in `overwrite`; «Annulla» senza secondo invio); `Layout.dom.test.tsx` (5:
  contatore dei job fermo con la scheda nascosta; «Esci da tutti i dispositivi» assente in locale, conferma →
  `logout-all` → notifica e schermata di accesso, «Annulla», errore); `ErrorBoundary.dom.test.tsx` (3: sessionStorage
  che lancia davvero, modulo obsoleto senza ricarica, «Riprova» e «Copia i dettagli» senza Clipboard API);
  `Viewer3D.dom.test.tsx` (7: 401 con la schermata di accesso, richiesta precedente annullata, sfondo che cambia con il
  tema senza ricaricare la struttura, grigio di contesto più scuro nel tema chiaro nel disegno e nella legenda, contorno
  che compare e sparisce al cambio di tema, colore dei centri delle tasche per tema, riferimento A nel disegno e nella
  legenda); `context.dom.test.tsx` (6: chiusura a 4 e a 8 s, pausa con il puntatore sopra e ripresa con il minimo di
  2 s, ripresa con il tempo residuo quando supera il minimo, pausa con il focus e ripresa quando esce, puntatore e focus
  insieme, chiusura a mano durante la pausa senza timer rimasti; con i timer finti di vitest user-event aspetta un
  `setTimeout` che non arriva, quindi `fireEvent` e `focus()`);
  `Pickers.test.tsx` (3: download dopo l'attesa se il template è ancora scelto, nessun download se tolto o con il
  selettore smontato); `keyboard.test.tsx` (4: notifiche chiuse da tastiera e con il clic, righe con Tab, Invio e Spazio,
  `DropZone`); `hooks.dom.test.tsx` (3: `useVisibleInterval`); `theme.dom.test.tsx` (2: sfondo del 3D dal tema e da
  `--viewer-bg`); `Settings.test.tsx` (3: interprete di MODELLER in sola lettura in modalità server e modificabile in
  locale, spazio massimo della cache). Il test del polling fallisce con il vecchio `useInterval` (12 richieste invece di
  2). In ambiente node: `client.test.ts` (6: id codificati nei percorsi, `fileText` come testo, richiesta annullata senza
  «Server non raggiungibile», sessione scaduta, 429 come errore, `logout-all`) e `links.test.ts` (2: `httpsUrl`).

## Fuori perimetro

| Funzione | Motivo |
|---|---|
| Vincoli di interfaccia PDBePISA | servizio EBI instabile; sostituibile con vincoli di distanza manuali |
| Wiki integrata e regole per agenti LLM | fuori dal perimetro dell'applicazione |
