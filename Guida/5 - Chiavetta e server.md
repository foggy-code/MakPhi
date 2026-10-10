# Chiavetta e server

Guida di BioLoom: [1. Primo avvio](1%20-%20Primo%20avvio.md) · [2. Funzioni](2%20-%20Funzioni.md) · [3. QMEAN e UniClust](3%20-%20QMEAN%20e%20UniClust.md) · [4. Dati e privacy](4%20-%20Dati%20e%20privacy.md) · **5. Chiavetta e server** · [6. Licenze](6%20-%20Licenze.md)

## Copia portatile (chiavetta USB)

Da un computer dove BioLoom funziona, prepara la copia con codice, dati e immagini Docker (circa
1 GB l'immagine di BioLoom e 2,2 GB quella di QMEAN, più i tuoi dati; `-SenzaDati` / `--senza-dati` e `-SenzaQmean` /
`--senza-qmean` escludono dati e immagine di QMEAN):

```bat
powershell -ExecutionPolicy Bypass -File programma\launcher\bioloom.ps1 -Azione prepara -Destinazione E:\BioLoom
```

```bash
./avvia-bioloom.sh prepara /Volumes/CHIAVETTA/BioLoom
```

Le immagini si salvano per prime. Se quella di BioLoom non corrisponde più al codice in `programma/` (codice aggiornato
senza ricostruirla), l'avviatore la aggiorna prima di salvarla, come l'azione `aggiorna`
([Primo avvio](1%20-%20Primo%20avvio.md#aggiornare-bioloom)); se non ci riesce la copia si ferma prima di copiare i
file («Copia interrotta»), perché una copia con l'immagine vecchia e il codice nuovo non deve uscire.

Per non diffondere per errore software con licenza e credenziali personali, la copia **non contiene**:

- **i pacchetti di PROCHECK e ProSA II** (`programma/docker/vendor/`), che la licenza non permette di distribuire: resta
  la cartella con il suo README. Per ricostruire le immagini con questi strumenti, per esempio dopo un aggiornamento,
  rimetti i pacchetti nella cartella;
- **chiavi e password**, salvo `-ConLicenze` / `--con-licenze`: `data/licenses/` (chiave di MODELLER, `LicenseDB` di
  ProSA, `posta.env` con la password della posta) e `data/access_key` (chiave d'accesso del server). Sul computer di
  destinazione le licenze si inseriscono da Impostazioni → Licenze; dopo aver caricato `LicenseDB`, riavvia BioLoom
  con l'avviatore (**R**) perché applichi il MAC della licenza. `posta.env` va ricreato se serve
  ([Avviso per email](4%20-%20Dati%20e%20privacy.md#avviso-per-email)). Con l'opzione, per una copia solo tua,
  l'avviatore ricorda che la copia contiene chiavi personali e non va data ad altri.

La licenza ProSA vale per un computer alla volta: se BioLoom gira dalla chiavetta su più computer, o la copia contiene le
licenze, sugli altri computer la licenza risulta attiva altrove e l'avviatore chiede se spostarla
([Licenze](6%20-%20Licenze.md#prosa-un-computer-alla-volta)).

Le immagini della cartella `immagini/` contengono comunque i programmi installati (MODELLER e, se c'erano i pacchetti,
PROCHECK e ProSA II): la copia è per uso personale, o del tuo gruppo se le licenze lo consentono, e non va
redistribuita. Se rifai la copia su una chiavetta preparata con una versione precedente di BioLoom, l'avviatore segnala
pacchetti e chiavi rimasti: cancellali prima di darla ad altri.

Formatta la chiavetta in **exFAT**: si legge da Windows, macOS e Linux e non ha il limite di 4 GB del FAT32. Sul
computer di destinazione basta Docker e un doppio clic sull'avviatore.

- **Mac Apple Silicon:** i container girano in emulazione x86 (MODELLER, ProSA e QMEAN esistono solo per x86), quindi
  più lenti.
- **Linux:** l'utente deve poter usare Docker (gruppo `docker`).

## Server

BioLoom gira negli stessi container del PC. Sul server cambia solo la configurazione (`server.env`):

- ascolto sulla rete;
- accesso con chiave;
- riavvio automatico;
- cartella dati e database scelti dall'amministratore.

### Requisiti

- Linux x86-64 con Docker Engine e il plugin Compose (`docker compose version`).
- Circa 3 GB per le immagini, più lo spazio per i dati (pdbaa ≈ 0,5 GB, database MMseqs2 ≈ 0,15 GB,
  risultati).
- Facoltativi:
  - disco con **UniClust30/UniRef30** di qualunque versione (e **QMTL**) per QMEAN locale;
  - i pacchetti PROCHECK e ProSA in `programma/docker/vendor/` prima di costruire le immagini.

### Installazione

```bash
# 1. copia la cartella di BioLoom sul server (git, rsync o la copia portatile con la cartella immagini/);
#    da Windows (scp, WinSCP) i permessi di esecuzione si perdono: chmod li rimette (con git non serve)
cd /opt/bioloom
chmod +x avvia-bioloom.sh programma/launcher/*.sh

# 2. prima esecuzione: crea server.env con una chiave d'accesso casuale
./avvia-bioloom.sh server

# 3. modifica server.env
#    BIOLOOM_DATA_PATH=/srv/bioloom/data        cartella dati
#    UNICLUST_PATH=/mnt/db                      cartella con uno o più database UniClust30/UniRef30 (entro 3 livelli)
#    QMTL_PATH=/mnt/db/qmtl                     facoltativa (QMEANDisCo)
#    BIOLOOM_PORT=8765
#    MODELLER_KEY=...                           oppure inseriscila poi dall'interfaccia
#    BIOLOOM_MAC=aa:bb:cc:dd:ee:ff              solo per la licenza ProSA (3° campo di LicenseDB)

# 4. avvio (e riavvio automatico con il sistema: restart unless-stopped)
./avvia-bioloom.sh server
```

Senza l'avviatore:

```bash
docker compose -f programma/launcher/compose.yml --env-file server.env up -d
COMPOSE_PROFILES=qmean BIOLOOM_QMEAN_URL=http://qmean:8080 \
  docker compose -f programma/launcher/compose.yml --env-file server.env up -d   # con QMEAN locale
```

### Server con Podman (senza Docker)

Su molti server Red Hat il comando `docker` è in realtà **Podman** (avviso «Emulate Docker CLI using podman»), spesso
vecchio e senza `compose`. L'avviatore se ne accorge da solo: `./avvia-bioloom.sh server` (e `arresta`, `stato`, `log`,
`verifica`, `aggiorna`) usa allora `programma/launcher/bioloom-podman.sh`, che avvia gli stessi container con
`podman run`. Non servono permessi di amministratore.

```bash
# 1. sul tuo PC: copia portatile senza dati; le immagini hanno già PROCHECK e ProSA (non sono su GitHub)
powershell -ExecutionPolicy Bypass -File programma\launcher\bioloom.ps1 -Azione prepara -Destinazione D:\BioLoom_server -SenzaDati
scp -r D:/BioLoom_server utente@server:~/bioloom

# 2. sul server: chmod rimette i permessi di esecuzione persi con scp da Windows; poi la prima esecuzione crea
#    server.env (dati in ~/bioloom/data, porta solo locale) e la seconda avvia
cd ~/bioloom && chmod +x avvia-bioloom.sh programma/launcher/*.sh
./avvia-bioloom.sh server
nano server.env
./avvia-bioloom.sh server

# 3. sul tuo PC: tunnel SSH, poi http://localhost:8765
ssh -N -L 8765:127.0.0.1:8765 utente@server
```

- **Accesso:** con Podman `server.env` nasce con `BIOLOOM_BIND=127.0.0.1`: BioLoom è raggiungibile solo dal server, e
  dal tuo PC con il tunnel. Non serve aprire porte nel firewall né HTTPS.
- **File:** i file della cartella dati restano del tuo utente.
- **ProSA:** Podman senza root non può assegnare al container il MAC della licenza, quindi l'analisi prosegue senza
  ProSA (PROCHECK, DOPE e le altre misure restano) e l'avviatore non lega la licenza al server.
- **Riavvio:** i container ripartono da soli se si fermano per errore. Senza linger, chiudendo SSH di solito restano
  attivi, ma non è garantito: controlla con `./avvia-bioloom.sh stato` dopo esserti ricollegato. Per tenerli attivi e
  farli ripartire con il server, l'amministratore attiva il linger (`loginctl enable-linger <utente>`); poi
  `programma/launcher/bioloom-podman.sh servizio` installa le unità systemd. Senza linger, dopo un riavvio del server
  rilancia `./avvia-bioloom.sh server`.

### Pipeline grandi

Per modellare molte coppie sequenza × template (per esempio 20.000 sequenze × 6 template) imposta in Impostazioni →
Esecuzione i **Job di calcolo in parallelo** pari ai core fisici del server (fino a 256) e, nella pagina Pipeline,
**In parallelo** allo stesso numero. Con l'analisi estesa, imposta **Grafici PostScript di PROCHECK** su «Solo il
Ramachandran» o «Nessuno»: con tutti i grafici 26.532 sequenze × 6 template occupano circa 630 GB, senza circa
150 GB. Da 8 job in parallelo BioLoom esegue i calcoli Python dell'analisi di qualità in processi di appoggio (uno ogni 4 job, 60–100 MB ciascuno), così i core restano tutti occupati. Una pipeline accetta
fino a 50.000 sequenze. Per stimare tempi e spazio sul server prima di partire c'è il benchmark
`programma/scripts/benchmark_mxn.py` (istruzioni nel file).

### QMEAN locale e fallback

- **Più versioni:** `UNICLUST_PATH` può contenere più database. BioLoom li elenca in Impostazioni → Database,
  dove si sceglie quale usare; se non si sceglie, usa il più recente pronto.
- **Formati:** vanno bene HH-suite 3 (UniRef30 2020–2023, uniclust30 2018_08), anche senza i file `_hhm`.
- **HH-suite 2 (uniclust30 2016–2017):** va convertito una volta con Impostazioni → Database → Converti. Il
  file `cs219` ricreato finisce nel volume `bioloom_qmean_cache`; il disco resta in sola lettura.
- **Archivi:** i `.tar.gz` vanno estratti prima, con `tar -xzf`: BioLoom li segnala ma non li estrae.
- **Disco di rete (NFS, SMB):** le letture piccole di HHblits sono lente. BioLoom riconosce il filesystem e
  propone **Copia indici**, che copia indici e cs219 (7,3 GB per UniRef30_2020_06) nel volume
  `bioloom_qmean_cache`. Con un disco locale (`ext4`, `xfs`) non serve.
- **Memoria:** HHblits deve tenere in memoria gli indici e l'intero cs219: circa 12 GB con UniRef30_2020_06.
  Con meno rilegge il cs219 di continuo e si fermerebbe a metà: BioLoom confronta prima la memoria del container
  `qmean` con quella richiesta dal database scelto e, se non basta, il job fallisce subito con il motivo (o passa al
  fallback su SWISS-MODEL, se attivo). Un calcolo QMEAN locale alla volta: gli altri restano in coda.
- **Processori:** con memoria sufficiente HHblits usa fino a 8 processori, con memoria al limite uno solo.
  `QMEAN_HHBLITS_CPU` in `server.env` fissa il numero.
- **Fallback su SWISS-MODEL:** va attivato esplicitamente in Impostazioni → Generale → QMEAN e servizi
  remoti, con il consenso all'invio e l'email. Se QMEAN locale non è raggiungibile o fallisce, il modello
  viene inviato a SWISS-MODEL e il risultato riporta il motivo. Su un server condiviso valuta se i modelli degli utenti
  possono lasciare la rete prima di attivarlo.

### Accesso

- Con `BIOLOOM_AUTH=on` l'interfaccia chiede la chiave d'accesso (`BIOLOOM_ACCESS_KEY` in `server.env`).
  Se la variabile è vuota, BioLoom genera la chiave in `access_key` nella cartella dati e la stampa nel log all'avvio:

  ```bash
  ./avvia-bioloom.sh log        # ultime 200 righe del log, con Docker o con Podman
  ```

- **Sessione:** il login crea un cookie HttpOnly firmato, valido 30 giorni. Cambiando la chiave e riavviando
  si invalidano tutte le sessioni.
- **ESMFold:** chi ha la chiave può consentire l'invio delle sequenze (Impostazioni → Generale), ma non scegliere
  l'indirizzo del servizio: lo imposti tu con `BIOLOOM_ESMFOLD_URL` in `server.env` (vuoto = API pubblica di ESM
  Atlas), così nessuno può far chiamare al server indirizzi della sua rete interna.
- **Avviso per email:** il server di posta lo imposti solo tu, con `BIOLOOM_SMTP_HOST`, `BIOLOOM_SMTP_PORT`,
  `BIOLOOM_SMTP_SECURITY` (starttls, ssl o none), `BIOLOOM_SMTP_USER`, `BIOLOOM_SMTP_PASSWORD`, `BIOLOOM_SMTP_FROM` e,
  per il link al job nel messaggio, `BIOLOOM_PUBLIC_URL` in `server.env` (oppure nel file `licenses/posta.env` della
  cartella dati, come in locale: le variabili di `server.env` hanno la precedenza). Dall'interfaccia non si modifica:
  chi ha la chiave d'accesso sceglie solo se ricevere l'avviso di una pipeline e a quale indirizzo, e può inviare
  l'email di prova. Chiedi ai tecnici se il server può uscire verso un server SMTP esterno (porte 587 e 465) oppure
  quale relay interno usare.
- **Script:** possono usare l'header `Authorization: Bearer <chiave>`.
- **Richieste da altri siti:** le richieste che modificano i dati partite da pagine di altri siti vengono
  rifiutate, e le pagine di BioLoom non si possono aprire dentro quelle di altri siti.
- **Senza chiave d'accesso** (`BIOLOOM_AUTH=off`, sconsigliato su un server): BioLoom risponde solo a `localhost`
  e `127.0.0.1`, che va bene con il tunnel SSH. Per aprirlo con il nome o l'indirizzo del server aggiungilo a
  `BIOLOOM_ALLOWED_HOSTS` in `server.env` (più nomi separati da virgole; `*` = qualunque nome). Con
  `BIOLOOM_AUTH=on` non serve.
- **Tentativi sbagliati:** vengono rallentati progressivamente.
- **Arresto:** dall'interfaccia è disabilitato (`BIOLOOM_ALLOW_SHUTDOWN=0`). Si arresta con
  `./avvia-bioloom.sh arresta`, con Docker o con Podman.

### HTTPS

BioLoom parla HTTP. Per l'accesso da Internet mettilo dietro un reverse proxy con certificato, ad esempio
Caddy. Imposta `BIOLOOM_BIND=127.0.0.1` in `server.env`, così la porta è raggiungibile solo dal proxy:

```
bioloom.example.org {
    reverse_proxy 127.0.0.1:8765
}
```

Con HTTPS il cookie di sessione viene marcato `Secure`. BioLoom lo riconosce dall'header `X-Forwarded-Proto` del
proxy: Caddy lo invia da solo, con nginx aggiungi `proxy_set_header X-Forwarded-Proto $scheme;`. Con nginx aggiungi
anche `proxy_set_header X-Forwarded-Host $http_host;`: serve ai browser vecchi, che non dicono da quale pagina parte
una richiesta, per riconoscere quelle di BioLoom.

Gli aggiornamenti in tempo reale dei job usano Server-Sent Events (`/api/jobs/events`, `/api/jobs/<id>/events`):
connessioni lunghe su cui il server scrive man mano. Caddy le inoltra senza configurazione; nginx rispetta
l'header `X-Accel-Buffering: no` che BioLoom invia (`proxy_read_timeout` deve superare i 15 s fra un
heartbeat e l'altro: il predefinito di 60 s va bene). Se un proxy bufferizza lo stesso, l'interfaccia se ne accorge (nessun
evento entro 5 s) e torna agli aggiornamenti periodici.

### Dati e backup

| Percorso | Contenuto | Backup |
|---|---|---|
| `BIOLOOM_DATA_PATH` | progetti (`bioloom.db`), risultati dei job, template, licenze, pdbaa, database MMseqs2 | sì |
| volume `bioloom_scratch` | calcoli in corso, svuotato a ogni avvio | no |
| volume `bioloom_cache` | indice veloce MMseqs2, ricostruibile da Impostazioni → Database | no |
| `UNICLUST_PATH`, `QMTL_PATH` | database esterni, montati in sola lettura | no, sono scaricabili |
| volume `bioloom_qmean_cache` | file cs219 ricreati per i database HH-suite 2 | no, si rifanno con Converti |

Il database dei progetti è SQLite in modalità esclusiva. Per un backup coerente copia la cartella dati con BioLoom
fermo: `./avvia-bioloom.sh arresta`, poi di nuovo `./avvia-bioloom.sh server`.

Il programma gira con l'utente proprietario della cartella dati: su Linux i file creati appartengono
all'utente che ha creato la cartella.

### Licenze sul server

- **MODELLER:** `MODELLER_KEY` in `server.env`, oppure dall'interfaccia (salvata in `data/licenses/`).
- **ProSA II:** carica `LicenseDB` dall'interfaccia e imposta in `BIOLOOM_MAC` il MAC indicato nella
  licenza, poi riavvia. La licenza vale per un computer alla volta
  ([Licenze](6%20-%20Licenze.md#prosa-un-computer-alla-volta)): al primo avvio con quel MAC l'avviatore la lega al
  server. Se risulta attiva su un altro computer (per esempio la cartella dati viene da un PC dove la usavi), il
  container prende il MAC predefinito e l'avviatore lo dice alla fine dell'avvio; sul server non chiede, quindi per
  spostarla qui (l'altro computer la perde) rilancia con `./avvia-bioloom.sh server --sposta-prosa`.
- **PROCHECK:** nessuna chiave.

### Aggiornamento

1. Sostituisci la cartella `programma/`, gli avviatori e `Guida/`, lasciando `data/` e `server.env`. Se la vecchia
   `programma/docker/vendor/` ha i pacchetti di PROCHECK e ProSA, mettili da parte prima e rimettili nella nuova: la
   nuova versione (copia portatile, GitHub) non li contiene. Se il server non li ha, porta anche la cartella
   `immagini/` di una copia portatile nuova, preparata dove i pacchetti ci sono: un'immagine ricostruita sul server ne
   sarebbe priva.
2. Aggiorna le immagini, con Docker o con Podman:

   ```bash
   ./avvia-bioloom.sh aggiorna
   ```

   L'avviatore confronta il codice in `programma/` con l'impronta salvata nell'immagine di BioLoom. Se non coincidono
   carica `immagini/bioloom-<versione>.tar`; se manca o viene anche lei da un codice diverso, ricostruisce l'immagine
   (serve internet; pochi minuti, 10–20 la prima volta). Aggiorna anche l'immagine di QMEAN se QMEAN locale è
   configurato (`UNICLUST_PATH`) o la sua immagine o il suo file ci sono; se lì qualcosa non riesce lo segnala, e QMEAN
   resta con l'immagine che aveva. Se BioLoom è attivo con un'immagine diversa lo riavvia con il file con cui era
   partito (`server.env`; i calcoli in corso si interrompono); altrimenti le immagini nuove valgono dal prossimo
   `./avvia-bioloom.sh server`. Se l'immagine di BioLoom non si aggiorna, l'azione finisce con un errore e BioLoom
   resta com'era. Con Podman, se hai installato le unità systemd (`servizio`), l'avviatore le rigenera per i container
   nuovi: valgono dal prossimo riavvio del server.

Se il codice è stato sostituito senza `aggiorna`, `./avvia-bioloom.sh server` se ne accorge: avvisa che l'immagine non
corrisponde al codice e parte con quella esistente.

`server.env` non contiene più `BIOLOOM_VERSION`: la versione delle immagini è sempre quella di
`programma/launcher/VERSION`. In un `server.env` di una versione precedente la riga può restare: l'avviatore la ignora e
lo dice («server.env: BIOLOOM_VERSION=4.1.0 viene ignorata, si usa la versione …»). Conta solo con `docker compose`
lanciato a mano: lì toglila. Senza l'avviatore le immagini si ricostruiscono con
`docker compose -f programma/launcher/compose.yml --env-file server.env build` (con `COMPOSE_PROFILES=qmean` anche
QMEAN), poi si riavvia con gli stessi comandi `up -d` dell'[installazione](#installazione).

I dati e le impostazioni restano. Le immagini della versione precedente restano su disco finché non le cancelli
(`docker image rm bioloom:<versione> bioloom-qmean:<versione>`, con Podman `podman image rm`); quelle sostituite a parità
di versione restano senza nome e si tolgono con `docker image prune` (`podman image prune`).
