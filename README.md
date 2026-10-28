# MakPhi

[![DOI](https://zenodo.org/badge/DOI/10.5281/zenodo.23729820.svg)](https://doi.org/10.5281/zenodo.23729820)

Suite per la **modellazione di proteine per omologia**: dalla sequenza bersaglio al modello 3D validato,
con MODELLER come motore di costruzione.

Tutto il programma gira **dentro Docker**: funziona uguale su Windows, macOS e Linux, da una chiavetta USB,
da una cartella qualsiasi o da un server. Sul computer serve solo Docker.

## Avvio

1. Installa **Docker Desktop** (Windows, macOS) oppure **Docker Engine** con il plugin compose (Linux):
   <https://www.docker.com/products/docker-desktop/>. Docker Desktop è gratuito per uso personale, didattico e nelle
   aziende piccole; per enti pubblici e altre organizzazioni, e per le alternative, vedi
   [Primo avvio → Docker Desktop](Guida/1%20-%20Primo%20avvio.md#docker-desktop-licenza-e-alternative).
2. Fai doppio clic sull'avviatore:

   | Sistema | Avvio | Arresto |
   |---|---|---|
   | Windows | `Avvia MakPhi.bat` | `Arresta MakPhi.bat` |
   | macOS | `Avvia MakPhi.command` | `Arresta MakPhi.command` |
   | Linux | `./avvia-makphi.sh` | `./avvia-makphi.sh arresta` |

3. Si apre il browser su `http://127.0.0.1:8765` (se la porta è occupata, un'altra fino alla 8800:
   [Porta occupata](Guida/1%20-%20Primo%20avvio.md#porta-occupata)). Al primo avvio inserisci la chiave di licenza di
   MODELLER in **Impostazioni → Licenze**: è gratuita per uso accademico (<https://salilab.org/modeller/>).
4. Facoltativo, per l'analisi estesa della qualità: **PROCHECK** e **ProSA II** non sono inclusi (le loro licenze non
   lo permettono). Si ottengono a parte e si aggiungono all'immagine:
   [Licenze → Installare e attivare PROCHECK e ProSA](Guida/6%20-%20Licenze.md#installare-e-attivare-procheck-e-prosa).

Su macOS, se il doppio clic su un avviatore scaricato da internet è bloccato, vedi
[Primo avvio → macOS](Guida/1%20-%20Primo%20avvio.md#macos-doppio-clic-bloccato).

## La cartella di MakPhi

```
MakPhi/
├── Avvia MakPhi.bat / .command, avvia-makphi.sh    avvio
├── Arresta MakPhi.bat / .command                    arresto
├── LEGGIMI.txt                                       istruzioni in breve
├── LICENSE, THIRD_PARTY_NOTICES.md                   licenza di MakPhi (MIT) e componenti di terze parti
├── SECURITY.md                                       come segnalare una vulnerabilità
├── Guida/                                            manuale d'uso
├── data/                                             i tuoi dati: progetti, risultati, licenze, database
├── immagini/                                         immagini Docker della copia portatile (se c'è)
└── programma/                                        il codice: non serve aprirlo per usare MakPhi
```

`data/` e `immagini/` compaiono al primo avvio o con la copia portatile.

## Guida

| | |
|---|---|
| [1. Primo avvio](Guida/1%20-%20Primo%20avvio.md) | installazione, avviatore, un percorso tipico dalla sequenza al report, aggiornamento |
| [2. Funzioni](Guida/2%20-%20Funzioni.md) | sequenze, template, allineamenti, modellazione, qualità, analisi, mutagenesi, confronto, report, pipeline, riproducibilità |
| [3. QMEAN e UniClust](Guida/3%20-%20QMEAN%20e%20UniClust.md) | QMEAN locale con UniClust30/UniRef30, memoria, fallback su SWISS-MODEL |
| [4. Dati e privacy](Guida/4%20-%20Dati%20e%20privacy.md) | dove stanno i dati, pulizia, cache, che cosa lascia il computer |
| [5. Chiavetta e server](Guida/5%20-%20Chiavetta%20e%20server.md) | copia portatile, installazione e aggiornamento su server, accesso, HTTPS, backup |
| [6. Licenze](Guida/6%20-%20Licenze.md) | licenza MIT di MakPhi, MODELLER, installare e attivare PROCHECK e ProSA II (un computer alla volta), strumenti open source |

## Sviluppo e test

Il codice è in `programma/`: `backend/` (Python, FastAPI), `frontend/` (React, TypeScript), `docker/` (immagini),
`launcher/` (avviatori e `compose.yml`), `scripts/`, `docs/`. I comandi qui sotto partono dalla cartella principale.

```bash
# tutto: ruff, pytest nell'immagine di MakPhi (MODELLER reale), eslint, typecheck e vitest del frontend
programma/scripts/test.sh
# solo il backend, con argomenti per pytest
programma/scripts/test.sh backend -k pipeline -x
# backend senza Docker, dalla cartella programma/backend/: un ambiente virtuale con MakPhi e le dipendenze di
# sviluppo (pytest, pytest-timeout, ruff); i test di MODELLER e degli strumenti esterni vengono saltati
python -m venv .venv && . .venv/bin/activate       # Windows (PowerShell): .venv\Scripts\Activate.ps1
pip install -e ".[dev]"
python -m pytest
ruff check --config pyproject.toml . ../docker/qmean
# frontend senza Docker (dalla cartella programma/frontend/): eslint, controllo dei tipi, test
npm run lint && npm run typecheck && npx vitest run
# immagini
docker compose -f programma/launcher/compose.yml build
# versione: stampa quella di programma/launcher/VERSION e controlla che le copie coincidano (con bash davanti: il
# bit eseguibile si perde se lo script entra nel repository da Windows)
bash programma/scripts/versione.sh
# impronta del codice, la stessa che la build salva in /app/IMPRONTA
sh programma/docker/impronta.sh programma
```

- `programma/scripts/test.sh` costruisce `makphi-test:<immagine>` sopra l'immagine locale `makphi:<versione>` (con
  pytest, pytest-timeout e ruff; la ricostruisce quando cambia l'immagine di base o `docker/test/Dockerfile`) e monta
  `programma/`. La versione viene da `programma/launcher/VERSION`: se il file manca lo script si ferma, salvo
  `MAKPHI_BASE_IMAGE`. La chiave di MODELLER viene da `MODELLER_KEY` oppure da `data/licenses/modeller.key`; da un
  worktree indica la cartella dati con `MAKPHI_DATA_PATH`. `test.sh frontend` esegue eslint, typecheck e vitest senza
  Docker né `VERSION`; `test.sh lint` (ruff) gira nel container come il backend.
- **Ambiente virtuale del backend:** `pip install -e ".[dev]"` installa MakPhi in modo modificabile con i limiti minimi
  di `pyproject.toml`; per le stesse versioni dell'immagine e della CI installa prima `pip install -r requirements.lock`.
  `.venv/` e `makphi.egg-info/` sono ignorate da git e non entrano nell'impronta del codice, quindi non fanno proporre
  una nuova immagine all'avviatore.
- **Limite per test:** con pytest-timeout 2.4.0 (dipendenze dev di `pyproject.toml`, immagine di test, CI) un test
  bloccato fallisce dopo 1800 s con «Failed: Timeout (>1800.0s) from pytest-timeout» e il suo stack, invece di lasciare
  appesa la suite. Senza il plugin pytest dà solo l'avviso «Unknown config option: timeout» e il limite non vale.
- **ESLint:** `npm run lint` (configurazione in `programma/frontend/eslint.config.js`): regole raccomandate di JavaScript
  e di typescript-eslint e le due regole degli hook di React. `react-hooks/exhaustive-deps` è un errore: un'esclusione
  voluta si dichiara con il motivo dopo «--»
  (`// eslint-disable-next-line react-hooks/exhaustive-deps -- <motivo>`), e anche un commento di esclusione che non
  serve più è un errore.
- **Test di interfaccia:** vitest gira in ambiente node (funzioni e markup statico dei componenti); i file che hanno
  bisogno di effetti, clic e tastiera dichiarano `// @vitest-environment jsdom` in testa e usano @testing-library/react,
  con le risposte finte del server e la visibilità della scheda di `src/test/dom.ts`. jsdom 30.1.1 richiede Node 22
  dalla 22.22.2, Node 24 dalla 24.15 oppure Node 26 o successivo (l'immagine ha la 22.23.3).
- **CI** (`.github/workflows/ci.yml`): `backend` (versione unica, impronta, ruff e pytest in Python puro), `frontend`
  (eslint, typecheck, vitest, build), `avviatori` (shellcheck, PSScriptAnalyzer e bit di esecuzione degli script `.sh` e
  `.command`: se manca, `git update-index --chmod=+x <file>`), `docker` (immagine completa con i test al suo interno,
  poi l'avviatore vero e la copia portatile avviata dal file in `immagini/`). Il job `qmean`, che costruisce l'immagine
  di QMEAN e prova il worker, parte solo a mano: Actions → CI → Run workflow, con l'opzione «qmean». Le Actions sono
  fissate per SHA completo del commit, con il tag nel commento: un tag spostato non cambia il codice che gira.
- **Dependabot** (`.github/dependabot.yml`): ogni lunedì propone poche PR raggruppate per le Actions, per npm
  (`programma/frontend`) e per pip (limiti minimi di `programma/backend`), con 7 giorni di attesa per le versioni appena
  uscite (14 per le major npm) e subito per gli aggiornamenti di sicurezza. `requirements.lock`, i digest delle immagini
  di base e gli SHA-256 dei file scaricati nel Dockerfile restano da aggiornare a mano.
- **Immagini di base e file scaricati:** `programma/docker/Dockerfile` fissa le immagini di base per digest e verifica
  con SHA-256 MODELLER, APBS, US-align e fpocket (questi due scaricati per commit): se un file cambia, la costruzione si
  ferma con «…: FAILED». Per aggiornarli segui la procedura nei commenti in testa al Dockerfile, poi aggiorna le versioni
  in `THIRD_PARTY_NOTICES.md`.
- Per provare un'immagine con strumenti nuovi senza toccare quella dell'avviatore:
  `docker build -f programma/docker/Dockerfile -t makphi:dev programma` e poi
  `MAKPHI_BASE_IMAGE=makphi:dev programma/scripts/test.sh`. Nelle immagini senza uno strumento (fpocket, APBS,
  OpenMM…) i suoi test vengono saltati con il motivo.
- **Niente rete nei test:** la fixture `_no_real_network` di `tests/conftest.py`, attiva in ogni test senza il marcatore
  `network`, fa fallire subito ogni connessione verso un host che non è questo computer (httpx con il trasporto vero,
  risoluzione dei nomi, socket, quindi anche smtplib), con un messaggio che dice come fare; il test fallisce anche se il
  codice ha trasformato l'errore in «servizio non raggiungibile». Nei test si usano `httpx.MockTransport`, server su
  127.0.0.1 o client finti. I test verso i servizi veri hanno `@pytest.mark.network`, sono esclusi per default e si
  lanciano con `pytest -m network`.
- **Anche su Windows:** la suite del backend passa con il Python di Windows, oltre che nell'immagine e nella CI su Linux:
  i test che dipendono da permessi POSIX, link simbolici, fine riga o nomi di file non ammessi da NTFS si adattano al
  sistema o si saltano con il motivo ([Architettura → Test](programma/docs/ARCHITETTURA.md#test)).
- Profilazione dei tempi con MODELLER reale (fuori dalla suite): `programma/scripts/test.sh backend tests/perf_timing.py -s`;
  misure e condizioni in [Architettura → Prestazioni](programma/docs/ARCHITETTURA.md#prestazioni).
- `python programma/makphi.py` avvia il server senza Docker, utile solo per lo sviluppo (dati in `data/`).
- **Rilascio di una nuova versione:** la versione sta solo in `programma/launcher/VERSION`, letta da avviatori e
  `test.sh`. `bash programma/scripts/versione.sh X.Y.Z` la cambia e allinea le copie letterali per chi non passa di lì
  (`pyproject.toml`, `makphi/__init__.py`, `package.json` e `package-lock.json`, l'`ARG` dei due Dockerfile, i
  predefiniti di `compose.yml`); senza argomento controlla che coincidano (uscita 1 con le differenze), come
  `tests/test_version.py` e la CI. Poi `./avvia-makphi.sh aggiorna` (Windows: `makphi.ps1 -Azione aggiorna`)
  costruisce `makphi:X.Y.Z` e, se QMEAN locale è in uso, `makphi-qmean:X.Y.Z`; `prepara` fa la copia portatile.
- **Impronta del codice:** `programma/docker/impronta.sh` riassume in un SHA-256 i sorgenti che entrano nell'immagine
  (`backend/`, `frontend/` e `docker/`, senza test, `docker/vendor/`, `docker/qmean/`, file nascosti e generati; i CR non
  contano). La build la salva in `/app/IMPRONTA` e l'avviatore la confronta all'avvio: dopo una modifica a quei file
  propone di ricostruire l'immagine (con `--no-menu` / `-NoMenu` avvisa soltanto). Definizione in
  [Architettura → Versione, impronta e aggiornamento](programma/docs/ARCHITETTURA.md#versione-impronta-e-aggiornamento-delle-immagini).

## Documentazione tecnica

- [Architettura](programma/docs/ARCHITETTURA.md): container, flusso dei dati, job, motori, API, interfaccia, sicurezza e test.
- [Sicurezza](SECURITY.md): versioni supportate e come segnalare una vulnerabilità in privato.
- [Piano di implementazione](programma/docs/PIANO_IMPLEMENTAZIONE.md): le fasi dell'evoluzione della v4, il loro stato e i
  seguiti possibili.

## Come citare MakPhi

MakPhi è archiviato su Zenodo. Per citarlo in generale usa il DOI di concetto
[10.5281/zenodo.23729820](https://doi.org/10.5281/zenodo.23729820), che porta sempre all'ultima versione; in un
articolo cita invece la versione che hai usato, con il DOI che Zenodo le assegna (5.0.1:
[10.5281/zenodo.23729821](https://doi.org/10.5281/zenodo.23729821)). Su GitHub **Cite this repository** dà la
citazione pronta in APA e BibTeX, da [CITATION.cff](CITATION.cff).

## Licenza

MakPhi è software libero con licenza MIT, © 2026 Francesco Fogagnolo: testo in [LICENSE](LICENSE). I programmi di terze
parti contenuti nelle immagini Docker e nell'interfaccia web hanno le loro licenze, elencate con le versioni in
[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md), insieme a dove trovare il sorgente del software GPL e LGPL. MODELLER,
PROCHECK e ProSA II hanno licenze accademiche personali: le immagini che li contengono non vanno distribuite
([Guida → Licenze](Guida/6%20-%20Licenze.md)).
