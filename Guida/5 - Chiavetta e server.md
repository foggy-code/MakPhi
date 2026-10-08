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
# 1. copia la cartella di BioLoom sul server (git, rsync o la copia portatile con la cartella immagini/)
cd /opt/bioloom

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
  Se la variabile è vuota, BioLoom genera la chiave in `data/access_key` e la stampa nel log:

  ```bash
  docker compose -f programma/launcher/compose.yml --env-file server.env logs bioloom
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
- **Tentativi sbagliati:** vengono rallentati progressivamente.
- **Arresto:** dall'interfaccia è disabilitato (`BIOLOOM_ALLOW_SHUTDOWN=0`). Si arresta con
  `docker compose ... down`.

### HTTPS

BioLoom parla HTTP. Per l'accesso da Internet mettilo dietro un reverse proxy con certificato, ad esempio
Caddy. Imposta `BIOLOOM_BIND=127.0.0.1` in `server.env`, così la porta è raggiungibile solo dal proxy:

```
bioloom.example.org {
    reverse_proxy 127.0.0.1:8765
}
```

Con HTTPS il cookie di sessione viene marcato `Secure`.

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

Il database dei progetti è SQLite in modalità esclusiva. Per un backup coerente fermalo prima
(`docker compose ... stop bioloom`) oppure copia la cartella dati con BioLoom fermo.

Il programma gira con l'utente proprietario della cartella dati: su Linux i file creati appartengono
all'utente che ha creato la cartella.

### Licenze sul server

- **MODELLER:** `MODELLER_KEY` in `server.env`, oppure dall'interfaccia (salvata in `data/licenses/`).
- **ProSA II:** carica `LicenseDB` dall'interfaccia e imposta in `BIOLOOM_MAC` il MAC indicato nella
  licenza, poi riavvia.
- **PROCHECK:** nessuna chiave.

### Aggiornamento

1. Sostituisci la cartella `programma/`, gli avviatori e `Guida/`, lasciando `data/` e `server.env`.
2. Ricostruisci le immagini:

   ```bash
   docker compose -f programma/launcher/compose.yml --env-file server.env build
   ```

3. Riavvia con `./avvia-bioloom.sh server`.

I dati e le impostazioni restano.
