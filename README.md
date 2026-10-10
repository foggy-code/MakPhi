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

3. Si apre il browser su `http://127.0.0.1:8765`. Al primo avvio inserisci la chiave di licenza di MODELLER in
   **Impostazioni → Licenze**: è gratuita per uso accademico (<https://salilab.org/modeller/>).

Su macOS, se il doppio clic su un avviatore scaricato da internet è bloccato, vedi
[Primo avvio → macOS](Guida/1%20-%20Primo%20avvio.md#macos-doppio-clic-bloccato).

## La cartella di BioLoom

```
BioLoom/
├── Avvia BioLoom.bat / .command, avvia-bioloom.sh    avvio
├── Arresta BioLoom.bat / .command                    arresto
├── LEGGIMI.txt                                       istruzioni in breve
├── LICENSE, THIRD_PARTY_NOTICES.md                   licenza di BioLoom (MIT) e componenti di terze parti
├── Guida/                                            manuale d'uso
├── data/                                             i tuoi dati: progetti, risultati, licenze, database
├── immagini/                                         immagini Docker della copia portatile (se c'è)
└── programma/                                        il codice: non serve aprirlo per usare BioLoom
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
| [6. Licenze](Guida/6%20-%20Licenze.md) | licenza MIT di BioLoom, MODELLER, ProSA II (un computer alla volta), PROCHECK e strumenti open source |

## Sviluppo e test

Il codice è in `programma/`: `backend/` (Python, FastAPI), `frontend/` (React, TypeScript), `docker/` (immagini),
`launcher/` (avviatori e `compose.yml`), `scripts/`, `docs/`. I comandi qui sotto partono dalla cartella principale.

```bash
# tutto: ruff, pytest nell'immagine di BioLoom (MODELLER reale), typecheck e vitest del frontend
programma/scripts/test.sh
# solo il backend, con argomenti per pytest
programma/scripts/test.sh backend -k pipeline -x
# backend senza Docker (dalla cartella programma/backend/, con le dipendenze di requirements.lock): i test di
# MODELLER e degli strumenti esterni vengono saltati
python -m pytest
# immagini
docker compose -f programma/launcher/compose.yml build
# versione: stampa quella di programma/launcher/VERSION e controlla che le copie coincidano (con bash davanti: il
# bit eseguibile si perde se lo script entra nel repository da Windows)
bash programma/scripts/versione.sh
# impronta del codice, la stessa che la build salva in /app/IMPRONTA
sh programma/docker/impronta.sh programma
```

- `programma/scripts/test.sh` costruisce `bioloom-test:<immagine>` sopra l'immagine locale `bioloom:<versione>` (con
  pytest e ruff) e monta `programma/`. La versione viene da `programma/launcher/VERSION`: se il file manca lo script si
  ferma, salvo `BIOLOOM_BASE_IMAGE`. La chiave di MODELLER viene da `MODELLER_KEY` oppure da
  `data/licenses/modeller.key`; da un worktree indica la cartella dati con `BIOLOOM_DATA_PATH`. La stessa verifica gira
  in CI (`.github/workflows/ci.yml`).
- Per provare un'immagine con strumenti nuovi senza toccare quella dell'avviatore:
  `docker build -f programma/docker/Dockerfile -t bioloom:dev programma` e poi
  `BIOLOOM_BASE_IMAGE=bioloom:dev programma/scripts/test.sh`. Nelle immagini senza uno strumento (fpocket, APBS,
  OpenMM…) i suoi test vengono saltati con il motivo.
- `pytest -m network` prova i servizi remoti reali, esclusi per default.
- Profilazione dei tempi con MODELLER reale (fuori dalla suite): `programma/scripts/test.sh backend tests/perf_timing.py -s`;
  misure e condizioni in [Architettura → Prestazioni](programma/docs/ARCHITETTURA.md#prestazioni).
- `python programma/bioloom.py` avvia il server senza Docker, utile solo per lo sviluppo (dati in `data/`).
- **Rilascio di una nuova versione:** la versione sta solo in `programma/launcher/VERSION`, letta da avviatori e
  `test.sh`. `bash programma/scripts/versione.sh X.Y.Z` la cambia e allinea le copie letterali per chi non passa di lì
  (`pyproject.toml`, `bioloom/__init__.py`, `package.json` e `package-lock.json`, l'`ARG` dei due Dockerfile, i
  predefiniti di `compose.yml`); senza argomento controlla che coincidano (uscita 1 con le differenze), come
  `tests/test_version.py` e la CI. Poi `./avvia-bioloom.sh aggiorna` (Windows: `bioloom.ps1 -Azione aggiorna`)
  costruisce `bioloom:X.Y.Z` e, se QMEAN locale è in uso, `bioloom-qmean:X.Y.Z`; `prepara` fa la copia portatile.
- **Impronta del codice:** `programma/docker/impronta.sh` riassume in un SHA-256 i sorgenti che entrano nell'immagine
  (`backend/`, `frontend/` e `docker/`, senza test, `docker/vendor/`, `docker/qmean/`, file nascosti e generati; i CR non
  contano). La build la salva in `/app/IMPRONTA` e l'avviatore la confronta all'avvio: dopo una modifica a quei file
  propone di ricostruire l'immagine (con `--no-menu` / `-NoMenu` avvisa soltanto). Definizione in
  [Architettura → Versione, impronta e aggiornamento](programma/docs/ARCHITETTURA.md#versione-impronta-e-aggiornamento-delle-immagini).

## Documentazione tecnica

- [Architettura](programma/docs/ARCHITETTURA.md): container, flusso dei dati, job, motori, API, interfaccia, sicurezza e test.
- [Piano di implementazione](programma/docs/PIANO_IMPLEMENTAZIONE.md): le fasi dell'evoluzione della v4, il loro stato e i
  seguiti possibili.

## Licenza

BioLoom è software libero con licenza MIT, © 2026 foggy02: testo in [LICENSE](LICENSE). I programmi di terze parti
contenuti nelle immagini Docker e nell'interfaccia web hanno le loro licenze, elencate con le versioni in
[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md), insieme a dove trovare il sorgente del software GPL e LGPL. MODELLER,
PROCHECK e ProSA II hanno licenze accademiche personali: le immagini che li contengono non vanno distribuite
([Guida → Licenze](Guida/6%20-%20Licenze.md)).
