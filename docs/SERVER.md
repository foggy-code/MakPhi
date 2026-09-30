# BioLoom su un server

BioLoom gira negli stessi container del PC. Sul server cambia solo la configurazione (`server.env`):

- ascolto sulla rete;
- accesso con chiave;
- riavvio automatico;
- cartella dati e database scelti dall'amministratore.

## Requisiti

- Linux x86-64 con Docker Engine e il plugin Compose (`docker compose version`).
- Circa 3 GB per le immagini, più lo spazio per i dati (pdbaa ≈ 0,5 GB, database MMseqs2 ≈ 0,15 GB,
  risultati).
- Facoltativi:
  - disco con **UniClust30/UniRef30** di qualunque versione (e **QMTL**) per QMEAN locale;
  - i pacchetti PROCHECK e ProSA in `docker/vendor/` prima di costruire le immagini.

## Installazione

```bash
# 1. copia la cartella di BioLoom sul server (git, rsync o la copia portatile con la cartella immagini/)
cd /opt/bioloom

# 2. prima esecuzione: crea server.env con una chiave d'accesso casuale
bash launcher/bioloom.sh server

# 3. modifica server.env
#    BIOLOOM_DATA_PATH=/srv/bioloom/data        cartella dati
#    UNICLUST_PATH=/mnt/db                      cartella con uno o più database UniClust30/UniRef30 (entro 3 livelli)
#    QMTL_PATH=/mnt/db/qmtl                     facoltativa (QMEANDisCo)
#    BIOLOOM_PORT=8765
#    MODELLER_KEY=...                           oppure inseriscila poi dall'interfaccia
#    BIOLOOM_MAC=aa:bb:cc:dd:ee:ff              solo per la licenza ProSA (3° campo di LicenseDB)

# 4. avvio (e riavvio automatico con il sistema: restart unless-stopped)
bash launcher/bioloom.sh server
```

Senza l'avviatore:

```bash
docker compose -f launcher/compose.yml --env-file server.env up -d
COMPOSE_PROFILES=qmean BIOLOOM_QMEAN_URL=http://qmean:8080 \
  docker compose -f launcher/compose.yml --env-file server.env up -d                        # con QMEAN locale
```

## QMEAN locale e fallback

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
  Con meno il calcolo parte, ma rilegge il cs219 di continuo e può durare ore.
- **Processori:** con memoria sufficiente HHblits usa fino a 8 processori, con memoria al limite uno solo.
  `QMEAN_HHBLITS_CPU` in `server.env` fissa il numero.
- **Fallback su SWISS-MODEL:** va attivato esplicitamente in Impostazioni → Generale → QMEAN e servizi
  remoti, con il consenso all'invio e l'email. Se QMEAN locale non è raggiungibile o fallisce, il modello
  viene inviato a SWISS-MODEL e il risultato riporta il motivo. Su un server condiviso valuta se i modelli degli utenti
  possono lasciare la rete prima di attivarlo.

## Accesso

- Con `BIOLOOM_AUTH=on` l'interfaccia chiede la chiave d'accesso (`BIOLOOM_ACCESS_KEY` in `server.env`).
  Se la variabile è vuota, BioLoom genera la chiave in `data/access_key` e la stampa nel log:

  ```bash
  docker compose -f launcher/compose.yml --env-file server.env logs bioloom
  ```

- **Sessione:** il login crea un cookie HttpOnly firmato, valido 30 giorni. Cambiando la chiave e riavviando
  si invalidano tutte le sessioni.
- **Script:** possono usare l'header `Authorization: Bearer <chiave>`.
- **Tentativi sbagliati:** vengono rallentati progressivamente.
- **Arresto:** dall'interfaccia è disabilitato (`BIOLOOM_ALLOW_SHUTDOWN=0`). Si arresta con
  `docker compose ... down`.

## HTTPS

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

## Dati e backup

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

## Licenze sul server

- **MODELLER:** `MODELLER_KEY` in `server.env`, oppure dall'interfaccia (salvata in `data/licenses/`).
- **ProSA II:** carica `LicenseDB` dall'interfaccia e imposta in `BIOLOOM_MAC` il MAC indicato nella
  licenza, poi riavvia.
- **PROCHECK:** nessuna chiave.

## Aggiornamento

1. Sostituisci il codice, lasciando `data/` e `server.env`.
2. Ricostruisci le immagini:

   ```bash
   docker compose -f launcher/compose.yml --env-file server.env build
   ```

3. Riavvia con `bash launcher/bioloom.sh server`.

I dati e le impostazioni restano.
