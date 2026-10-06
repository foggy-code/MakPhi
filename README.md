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

## La cartella di BioLoom

```
BioLoom/
├── Avvia BioLoom.bat / .command, avvia-bioloom.sh    avvio
├── Arresta BioLoom.bat / .command                    arresto
├── LEGGIMI.txt                                       istruzioni in breve
├── Guida/                                            manuale d'uso
├── data/                                             i tuoi dati: progetti, risultati, licenze, database
├── immagini/                                         immagini Docker della copia portatile (se c'è)
└── programma/                                        il codice: non serve aprirlo per usare BioLoom
```

`data/` e `immagini/` compaiono al primo avvio o con la copia portatile.

## Guida

| | |
|---|---|
| [1. Primo avvio](Guida/1%20-%20Primo%20avvio.md) | installazione, avviatore, un percorso tipico dalla sequenza al report |
| [2. Funzioni](Guida/2%20-%20Funzioni.md) | sequenze, template, allineamenti, modellazione, qualità, analisi, mutagenesi, confronto, report, pipeline, riproducibilità |
| [3. QMEAN e UniClust](Guida/3%20-%20QMEAN%20e%20UniClust.md) | QMEAN locale con UniClust30/UniRef30, memoria, fallback su SWISS-MODEL |
| [4. Dati e privacy](Guida/4%20-%20Dati%20e%20privacy.md) | dove stanno i dati, pulizia, cache, che cosa lascia il computer |
| [5. Chiavetta e server](Guida/5%20-%20Chiavetta%20e%20server.md) | copia portatile, installazione su server, accesso, HTTPS, backup |
| [6. Licenze](Guida/6%20-%20Licenze.md) | MODELLER, ProSA II, PROCHECK e strumenti open source |

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
```

- `programma/scripts/test.sh` costruisce `bioloom-test:<immagine>` sopra l'immagine locale (con pytest e ruff) e monta
  `programma/`. La chiave di MODELLER viene da `MODELLER_KEY` oppure da `data/licenses/modeller.key`; da un worktree
  indica la cartella dati con `BIOLOOM_DATA_PATH`. La stessa verifica gira in CI (`.github/workflows/ci.yml`).
- Per provare un'immagine con strumenti nuovi senza toccare quella dell'avviatore:
  `docker build -f programma/docker/Dockerfile -t bioloom:dev programma` e poi
  `BIOLOOM_BASE_IMAGE=bioloom:dev programma/scripts/test.sh`. Nelle immagini senza uno strumento (fpocket, APBS,
  OpenMM…) i suoi test vengono saltati con il motivo.
- `pytest -m network` prova i servizi remoti reali, esclusi per default.
- Profilazione dei tempi con MODELLER reale (fuori dalla suite): `programma/scripts/test.sh backend tests/perf_timing.py -s`;
  misure e condizioni in [Architettura → Prestazioni](programma/docs/ARCHITETTURA.md#prestazioni).
- `python programma/bioloom.py` avvia il server senza Docker, utile solo per lo sviluppo (dati in `data/`).

## Documentazione tecnica

- [Architettura](programma/docs/ARCHITETTURA.md): container, flusso dei dati, job, motori, API, interfaccia, sicurezza e test.
- [Piano di implementazione](programma/docs/PIANO_IMPLEMENTAZIONE.md): le fasi dell'evoluzione della v4, il loro stato e i
  seguiti possibili.
