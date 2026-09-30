# BioLoom v4

Suite per la **modellazione di proteine per omologia**: dalla sequenza bersaglio al modello 3D validato,
con MODELLER come motore di costruzione.

Tutto il programma gira **dentro Docker**: funziona uguale su Windows, macOS e Linux, da una chiavetta USB,
da una cartella qualsiasi o da un server. Sul computer serve solo Docker.

## Avvio

1. Installa **Docker Desktop** (Windows, macOS) oppure **Docker Engine** (Linux): <https://www.docker.com/products/docker-desktop/>.
2. Fai doppio clic sull'avviatore:

   | Sistema | Avvio | Arresto |
   |---|---|---|
   | Windows | `Avvia BioLoom.bat` | `Arresta BioLoom.bat` |
   | macOS | `Avvia BioLoom.command` | `Arresta BioLoom.command` |
   | Linux | `./avvia-bioloom.sh` | `./avvia-bioloom.sh arresta` |

3. Si apre il browser su `http://127.0.0.1:8765`. Al primo avvio inserisci la chiave di licenza di
   MODELLER in **Impostazioni → Licenze**: è gratuita per uso accademico (<https://salilab.org/modeller/>).

L'avviatore fa da solo tutto il resto:

- avvia Docker Desktop se è spento;
- carica le immagini dalla cartella `immagini/` (senza internet) oppure le costruisce la prima volta;
- trova il disco con **UniClust30/UniRef30** per QMEAN, in qualunque versione, anche se cambia la lettera di
  unità o il punto di montaggio;
- applica il MAC richiesto dalla licenza **ProSA**;
- sceglie una porta libera.

Dalla sua finestra puoi:

- riaprire il browser;
- verificare gli strumenti;
- vedere i log;
- riavviare dopo aver collegato un disco (R, rifà la ricerca);
- indicare a mano la cartella di UniClust30 (U);
- arrestare BioLoom.

## QMEAN e UniClust30

- **Qualunque versione del database, anche più d'una sullo stesso disco:**
  - UniRef30 2020–2023;
  - uniclust30 2018_08;
  - uniclust30 2016–2017 in formato HH-suite 2, che si converte una volta da Impostazioni → Database;
  - database senza i file HMM.

  BioLoom usa la versione più recente pronta, oppure quella che scegli in Impostazioni → Database. Segnala gli
  archivi `.tar.gz` non estratti, le copie incomplete e i database in formato MMseqs2 non utilizzabili.
- **Copia locale degli indici (Windows e macOS):** Docker Desktop fa arrivare il disco nel container attraverso
  una condivisione di file. Questa è veloce a blocchi grandi ma lentissima per le letture piccole con cui
  HHblits scorre gli indici: solo caricarli richiedeva 15 minuti.
  - **Copia indici**, in Impostazioni → Database, porta su ogni computer solo indici e cs219 (7,3 GB per
    UniRef30_2020_06, circa 3 minuti). Finiscono nel disco di Docker; i file grandi restano sul disco esterno.
  - Con la copia gli indici si caricano in pochi secondi.
  - Se il database sul disco cambia, BioLoom torna a leggerlo dal disco finché non aggiorni la copia.
  - Su Linux il disco è montato direttamente e la copia non serve.
- **Memoria:** HHblits deve tenere in memoria gli indici del database e l'intero cs219. Con UniRef30_2020_06
  servono circa 12 GB assegnati a Docker.
  - **Con meno memoria** il calcolo parte, ma rilegge il cs219 di continuo e può durare ore. Con 6 GB, dopo
    l'avvio ha letto oltre 100 GB senza avanzare.
  - **Prima di partire** BioLoom avvisa e indica quanta memoria serve. Se non bastano nemmeno memoria e swap
    insieme, non avvia HHblits e usa il fallback su SWISS-MODEL, se attivo.
  - **Con memoria al limite** HHblits usa un solo processore: legge molto meno dal disco.
  - **Windows:** nel file `%UserProfile%\.wslconfig` metti `[wsl2]` e `memory=...GB`, poi `wsl --shutdown`.
    Il valore predefinito è metà della RAM.
  - **macOS:** Docker Desktop → Settings → Resources.
- **Fallback facoltativo su SWISS-MODEL:** se QMEAN locale non è disponibile (disco scollegato) o non riesce,
  il modello può essere inviato a SWISS-MODEL. Si attiva in Impostazioni oppure per il singolo calcolo, e
  richiede il consenso all'invio e la tua email. Il risultato indica sempre dove è stato calcolato e perché.

## Caricare dati da file

Ogni campo che riceve dati accetta anche un file:

| Dati | Formati accettati | Dove |
|---|---|---|
| Sequenze | FASTA, GenBank (con le traduzioni delle CDS), EMBL, UniProt, PIR, Clustal, Stockholm, MSF, PDB/mmCIF (una sequenza per catena), testo semplice; anche compressi `.gz`, in UTF-8 o UTF-16 | Sequenze, Ricerca, Allineamenti, Modellazione, Pipeline |
| Allineamenti | PIR, oppure FASTA allineato, Clustal, Stockholm (convertiti in PIR) | Editor PIR e Modellazione |
| Strutture | `.pdb`, `.cif` | qualità, allineamento strutturale, raffinamento dei loop, template |
| Vincoli di modellazione | testo, una riga per vincolo: `helix 10:A 20:A`, `strand 30:A 35:A`, `distance CA:10:A CA:50:A 8.0 0.5`; esportabili nello stesso formato | Modellazione |
| Chiave MODELLER | file con la chiave, email di registrazione o `config.py` | Licenze |
| Licenza ProSA | file `LicenseDB` | Licenze |

## Risultati riusati (cache dei calcoli)

Ricerche di template e allineamenti multipli e strutturali già calcolati con gli stessi dati e parametri non
vengono rifatti. Il risultato riporta la nota **dalla cache (data)**. Rilanciare una pipeline con le stesse
sequenze e altre opzioni di modellazione salta così tutte le ricerche.

- **Validità:** le ricerche su RCSB e NCBI valgono 7 giorni, perché il PDB si aggiorna ogni settimana. Le altre
  valgono finché non cambiano il database locale (pdbaa, MMseqs2), il programma usato o il contenuto delle
  strutture.
- **Ricalcolo:** in Ricerca, Allineamenti e Pipeline l'opzione **Ricalcola ignorando la cache** rifà il calcolo e
  aggiorna il risultato salvato.
- **Disattivazione e spazio:** da Impostazioni → Spazio e file temporanei, che mostra anche lo spazio occupato. La
  cache sta in `data/cache/compute` e si pulisce da sola dopo 30 giorni.

## Qualità per residuo e consenso

- **Viewer 3D colorato per valore:** nella pagina Qualità (schede 3D e Profili) il menu **Colora per** colora la
  struttura per struttura secondaria, Ramachandran, clash, DOPE per residuo, RSA, B-factor, **pLDDT** dei modelli
  AlphaFold (scala ufficiale di AlphaFold DB), QMEAN locale calcolato nella pagina e consenso per residuo, con la
  legenda sul viewer. Un clic su grafici o tabelle evidenzia e centra il residuo nel 3D; un clic nel 3D lo seleziona
  nella tabella e nei grafici.
- **Consenso tra i metodi:** voto BioLoom, z-DOPE, GA341, QMEAN, ProSA e PROCHECK, quando disponibili, sono portati
  su una scala 0–100 dichiarata e combinati in un punteggio con fascia buona / intermedia / scarsa. Il riquadro
  spiega i metodi che discordano ed elenca le **regioni problematiche** (tratti segnalati da almeno due metodi),
  cliccabili. Il voto A–D non cambia.
- **Confidenza dell'allineamento:** negli allineamenti (Allineamenti, Modellazione, Editor PIR) la modalità
  **Confidenza** colora le colonne per identità locale rispetto al bersaglio e segnala la zona crepuscolare
  (sotto il 25%) e i residui non coperti dal template.

## Cosa contiene

| Area | Strumenti |
|---|---|
| Modellazione | MODELLER 10.8 (AutoModel, loop, allineamenti `align2d`/`salign`) |
| Ricerca template | RCSB e NCBI BLAST (online); BLAST+ e MMseqs2 sul database locale pdbaa |
| Allineamenti | Clustal Omega, MAFFT, MUSCLE, TM-align, US-align, più motori integrati |
| Qualità | Ramachandran, clashscore, DSSP (mkdssp), SASA, profilo DOPE, **PROCHECK**, **ProSA II**, **QMEAN locale** (UniClust30) o SWISS-MODEL, voto A–D, consenso tra i metodi, qualità per residuo nel 3D (anche pLDDT di AlphaFold) |
| Automazione | pipeline batch da multi-FASTA, riprendibili dopo un annullamento, un errore o un riavvio ("Riprendi" o "Riprova solo i falliti": le sequenze già modellate non si rifanno) e, se completate con sequenze fallite, "Riprova i falliti"; verifica dell'installazione (Impostazioni → Strumenti) |

## Dove stanno i dati

| Cosa | Dove | Viaggia con la chiavetta? |
|---|---|---|
| Progetti, risultati, template, licenze, pdbaa, database MMseqs2 | `data/` accanto all'avviatore | sì |
| Calcoli in corso e file temporanei | volume Docker `scratch`, veloce e svuotato a ogni avvio | no |
| Indice veloce MMseqs2 (≈ 1,5 GB) | volume Docker `cache` di ogni computer | no, si ricrea da Impostazioni → Database |
| UniClust30/UniRef30 e QMTL | disco esterno, montato in sola lettura | resta sul disco |
| Copia locale di indici e cs219 di UniClust30; file cs219 ricreati per i database HH-suite 2 | volume Docker `qmean_cache` di ogni computer | no |

A fine calcolo in `data/` vengono salvati solo i risultati: i file intermedi di MODELLER restano fuori, salvo
diversa impostazione. Upload e cache scaduti si puliscono da soli (Impostazioni → Spazio e file temporanei).

## Chiavetta USB

Da un computer dove BioLoom funziona, prepara la copia portatile (codice, dati e immagini Docker, circa 1,5 GB
più i tuoi dati):

```bat
powershell -ExecutionPolicy Bypass -File launcher\bioloom.ps1 -Azione prepara -Destinazione E:\BioLoom
```

```bash
bash launcher/bioloom.sh prepara /Volumes/CHIAVETTA/BioLoom
```

Formatta la chiavetta in **exFAT**: si legge da Windows, macOS e Linux e non ha il limite di 4 GB del FAT32.
Sul computer di destinazione basta Docker e un doppio clic sull'avviatore.

- **Mac Apple Silicon:** i container girano in emulazione x86 (MODELLER, ProSA e QMEAN esistono solo per
  x86), quindi più lenti.
- **Linux:** l'utente deve poter usare Docker (gruppo `docker`).

## Server

```bash
bash launcher/bioloom.sh server   # la prima volta crea server.env con una chiave d'accesso casuale
nano server.env                   # cartella dati, UniClust30, porta
bash launcher/bioloom.sh server   # avvio (riavvio automatico con il server)
```

In modalità server l'interfaccia chiede la chiave d'accesso. I dettagli, compreso HTTPS con reverse proxy,
sono in [docs/SERVER.md](docs/SERVER.md).

## Licenze

- **MODELLER:** si inserisce da Impostazioni → Licenze e viene verificata prima di essere salvata. Su un
  server si può indicare anche `MODELLER_KEY` in `server.env`.
- **ProSA II:** si carica il file `LicenseDB` da Impostazioni → Licenze. L'avviatore assegna al container
  il MAC indicato nella licenza.
- **PROCHECK:** non richiede chiavi.

Le chiavi restano in `data/licenses/` e non entrano mai nelle immagini Docker. I pacchetti PROCHECK e ProSA
(`docker/vendor/`) e le chiavi sono tuoi: non distribuirli ad altri.

## Documentazione

- [Architettura](docs/ARCHITETTURA.md): container, flusso dei dati, motori, API.
- [Server](docs/SERVER.md): installazione su server, accesso, HTTPS, backup.
- [Reverse engineering della v3](docs/REVERSE_ENGINEERING.md).

## Sviluppo

```bash
# tutto: ruff, pytest nell'immagine di BioLoom (MODELLER reale), typecheck e vitest del frontend
scripts/test.sh
# solo il backend, con argomenti per pytest
scripts/test.sh backend -k pipeline -x
# backend senza Docker (dalla cartella backend/, con le dipendenze di requirements.lock): i test di
# MODELLER e degli strumenti esterni vengono saltati
python -m pytest
# immagini
docker compose -f launcher/compose.yml build
```

`scripts/test.sh` costruisce `bioloom-test:<versione>` sopra l'immagine locale (con pytest e ruff) e monta il
repository. La chiave di MODELLER viene da `MODELLER_KEY` oppure da `data/licenses/modeller.key`; da un worktree
indica la cartella dati con `BIOLOOM_DATA_PATH`. La stessa verifica gira in CI (`.github/workflows/ci.yml`).

`bioloom.py` avvia il server senza Docker, utile solo per lo sviluppo.
