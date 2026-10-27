# Sicurezza

## Versioni supportate

Le correzioni di sicurezza arrivano solo nell'ultima versione di MakPhi, come nuova versione (tag `vX.Y.Z` e sezione
nel [CHANGELOG](CHANGELOG.md)). Chi usa una versione precedente, BioLoom compresa, aggiorna all'ultima: dati, volumi e
configurazione restano validi ([Primo avvio](Guida/1%20-%20Primo%20avvio.md)).

| Versione | Correzioni di sicurezza |
|---|---|
| 5.x (ultima) | sì |
| fino alla 4.4.0 (BioLoom) | no |

## Segnalare una vulnerabilità

Non aprire una issue pubblica e non descrivere il problema in una pull request. Usa la segnalazione privata di GitHub:
scheda **Security** del repository → **Report a vulnerability**. La vede solo il manutentore, e la correzione si
prepara in privato prima di renderla pubblica.

Nella segnalazione indica:

- versione di MakPhi e impronta del codice dell'immagine
  (`docker run --rm --entrypoint cat makphi:<versione> /app/IMPRONTA`);
- modalità: locale (avviatore sul PC) o server (`MAKPHI_AUTH=on`, reverse proxy, Podman);
- i passi per riprodurre il problema e cosa permette di fare (leggere dati, eseguire comandi, fermare MakPhi…).

Non mettere nella segnalazione le tue chiavi (MODELLER, `LicenseDB` di ProSA, chiave d'accesso, password SMTP) né dati
di ricerca riservati: se servono per riprodurre il problema, usa valori di prova.

MakPhi è mantenuto da una sola persona: la risposta arriva appena possibile, senza tempi garantiti. Chi segnala viene
citato nel CHANGELOG insieme alla correzione, se lo desidera.

## Cosa rientra

- Il codice di MakPhi: backend, interfaccia web, avviatori, script, Dockerfile e worker di QMEAN (`programma/`), e la
  configurazione predefinita che ne deriva.
- Esempi: accesso alle API senza chiave con `MAKPHI_AUTH=on`, richieste di altri siti (CSRF, DNS rebinding,
  clickjacking), percorsi che escono dalla cartella dei dati, chiavi di licenza o segreti che compaiono in risposte,
  log o report, file caricati che fanno eseguire comandi.

Non rientrano, ma vanno segnalati ai rispettivi autori: i programmi di terze parti nelle immagini (MODELLER,
PROCHECK, ProSA, QMEAN, BLAST+, HH-suite… elencati in [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)) e i servizi
esterni (SWISS-MODEL, RCSB PDB, UniProt, ESMFold). Non sono vulnerabilità di MakPhi le configurazioni che la
documentazione indica come non sicure, per esempio `MAKPHI_BIND=0.0.0.0` senza `MAKPHI_AUTH=on`, né le immagini
ricevute da altri e non costruite dal repository.

## Come è protetto MakPhi

Il modello di sicurezza (porta solo su `127.0.0.1`, chiave d'accesso sul server, controlli contro le richieste di altri
siti, container senza root, chiavi di licenza mascherate, limiti sui caricamenti) è descritto in
[Architettura → Sicurezza e privacy](programma/docs/ARCHITETTURA.md#sicurezza-e-privacy); i dati che MakPhi invia ai
servizi esterni in [Guida → Dati e privacy](Guida/4%20-%20Dati%20e%20privacy.md).
