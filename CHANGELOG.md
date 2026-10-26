# Registro delle modifiche

Tutte le modifiche rilevanti di BioLoom v4, dalla più recente. Formato ispirato a
[Keep a Changelog](https://keepachangelog.com/it/1.1.0/); numeri di versione secondo
[Semantic Versioning](https://semver.org/lang/it/).

**Procedura di rilascio.** Ogni modifica che arriva in `main` e cambia il comportamento del programma aumenta la
versione (`bash programma/scripts/versione.sh X.Y.Z`), aggiunge qui una sezione e, dopo il merge, riceve il tag
annotato `vX.Y.Z` sul commit di merge (`git tag -a vX.Y.Z -m "BioLoom X.Y.Z" <commit>`). La versione entra nella chiave
della cache dei calcoli: aumentarla invalida i risultati calcolati con il codice precedente. L'impronta del codice
(`/app/IMPRONTA`) identifica invece l'immagine esatta e finisce nel manifest, nell'ambiente di ogni job e nelle voci
della cache.

Le versioni fino a 4.2.0 sono ricostruite dalla storia git (21 ottobre 2026): prima non c'era un registro.

## [Non rilasciato]

### Aggiunto
- ZIP dei modelli in background: **Prepara ZIP modelli** avvia un job `pipeline_export` con l'avanzamento, e
  l'archivio si scarica quando è pronto. I report si generano in più processi. Rotte `POST` e
  `GET /api/pipeline/{id}/export`.
- `POST /api/jobs/{id}/qmean-auto/retry`: rimette in coda i QMEAN automatici falliti o annullati di una modellazione o
  pipeline. Riprende gli stessi job, quindi restano automatici e i conteggi tornano giusti, e salta i modelli che
  hanno già un QMEAN valido. Serve dopo un guasto del servizio QMEAN, quando la pipeline è completata e non si può
  riprendere.

### Corretto
- Lo ZIP della pipeline `seq_con_dominio` (139.296 coppie) restava in caricamento: si generava dentro la richiesta, a
  una coppia ogni 3 s (circa 5 giorni prima del primo byte). Inoltre i report non avevano il QMEAN, perché la ricerca
  per struttura guardava solo i 200 QMEAN più recenti di tutto BioLoom. Lo stesso difetto valeva per la pagina
  Qualità e per il report del modello. Ora la ricerca filtra per job e file, con un indice nel database.
- QMEAN locale con un servizio occupato o bloccato. Sul server una pipeline ha perso 107.918 QMEAN in 5 ore: il
  worker apriva un thread per richiesta e ogni calcolo gli faceva rifare la scansione dei database. A 1096 thread
  non rispondeva più, e ogni calcolo falliva in 4 s con "QMEAN locale non attivo".
  - Worker: 16 thread fissi con una coda (`QMEAN_HTTP_THREADS`), e le richieste scadute si chiudono senza lavoro.
    Le scansioni dei database e i conteggi dei profili sono uno alla volta e condivisi, e l'invio di un modello non
    rifà la scansione.
  - BioLoom: lo stato viene dalla cache di 15 s, con una richiesta al worker alla volta. Se il servizio non risponde
    il calcolo aspetta e riprova ogni 15 s per al più 30 minuti, invece di fallire. Un calcolo perso da un servizio
    riavviato si invia di nuovo.
  - Messaggio distinto per un servizio che non risponde ("Il servizio QMEAN locale non risponde"), invece di quello
    del disco mancante.

## [4.3.1] - 2026-10-21

### Modificato
- Strumenti di sviluppo dell'interfaccia: Vite 8 (rolldown; `manualChunks` nella forma a funzione), plugin React 6,
  vitest 5 con `@vitest/coverage-v8` 5, jsdom 30.1.2. `npm audit` non trova più vulnerabilità, nemmeno nelle
  dipendenze di sviluppo. Stessi blocchi del bundle, 818 test e 29 test nel browser superati.
- GitHub Actions: checkout 7.0.1, setup-node 7.1.0, setup-python 7.0.0, setup-buildx 4.4.1, build-push 7.4.0 (SHA
  verificati con `git ls-remote`).

### Corretto
- Riesecuzione dei pacchetti: anche il campo `workers` di `models.json` (numero di worker della modalità parallela di
  MODELLER) esce dal confronto; nella 4.3.0 ne usciva solo `modeller_workers` del risultato, e una riesecuzione con un
  altro numero di worker risultava ancora «diversa».
- Integrazione continua: avviso di shellcheck in `bioloom-podman.sh` e test di annullamento della pipeline instabile
  sui runner con pochi core (rossi dal 12 e dal 14 ottobre).

## [4.3.0] - 2026-10-21

Contiene anche le modifiche arrivate in `main` dopo 4.2.0 senza aumentare la versione (dall'11 al 21 ottobre 2026):
le immagini costruite in quel periodo si chiamano tutte `bioloom:4.2.0` ma hanno impronte diverse.

### Aggiunto
- Pipeline fino a 100.000 sequenze, anche da file; scala delle pipeline a centinaia di migliaia di elementi
  (`7803321`).
- QMEAN su tutte le coppie della pipeline, nei report dello ZIP; l'avviso email aspetta la fine dei QMEAN (`6187f58`).
- Monomero, omo- o etero-oligomero nella Libreria template (`9ca9480`).
- Lunghezza massima della sequenza come impostazione (`bfa2972`).
- ZIP della pipeline con una cartella per sequenza e QMEAN nei CSV (`308dfd4`).
- QMEAN automatico a fine modellazione e pipeline (`d570a45`).
- MODELLER in parallelo: i modelli di un job costruiti da più processi (`4d0858b`).
- Impronta del codice in `versions.bioloom()`, nel manifest di riproducibilità, nell'ambiente dei job e nelle voci
  della cache; avviso `rerun_version_code` quando la riesecuzione usa la stessa versione con codice diverso.
- Ambiente di calcolo (versioni di BioLoom, impronta, strumenti, Python, librerie) registrato all'avvio di ogni job
  in `.environment.json`; il manifest lo usa al posto di quello rilevato all'esportazione.
- Copertura dei test misurata in CI (pytest-cov nel job docker, `@vitest/coverage-v8` nel job frontend); al 21 ottobre
  2026 il backend è al 91,4 % delle righe (1533 test), il frontend al 52,3 % delle istruzioni (818 test).
- Firma HMAC-SHA256 facoltativa del manifest di riproducibilità (`data/licenses/firma-manifest.key`): firma non valida
  = rifiuto, chiave assente o diversa = avviso.
- Le pipeline registrano la modalità di MODELLER nei parametri e in ogni elemento; quelle create prima della modalità
  parallela, riprese, restano in serie.
- Test dell'interfaccia in Chromium (Playwright) con audit di accessibilità axe-core (WCAG 2.1 A/AA) su tutte le pagine
  in tema scuro e chiaro, anche in CI; avviatore Podman provato in CI; scansione delle dipendenze (pip-audit, npm audit).
- Avviso sotto la scelta del motore nelle pagine Ricerca e Pipeline: RCSB e NCBI ricevono la sequenza.
- Esperimento serie contro parallelo con lo stesso seme (`tests/perf_serial_parallel.py`) e test rigoroso: stessa
  struttura in un altro sistema di riferimento.
- Script di misura `benchmark_tempi.py`, `benchmark_qmean.py`, `misure_tempi.sh` e `analisi_tempi.py`.
- `CITATION.cff` e `.zenodo.json`; `versione.sh` tiene allineata anche la loro versione.
- Questo registro delle modifiche e i tag `v4.1.0` e `v4.2.0`.

### Modificato
- QMEAN: tutti i posti del servizio e una sola ricerca HHblits per sequenza (`83d8994`); calcoli contemporanei e
  profili HHblits riusati (`044f594`).
- Podman: rimozione dei container con attesa, `aggiorna` non lascia BioLoom spento (`06f7032`); profilo seccomp su
  misura per Podman 2.x (`6773e84`) e MMseqs2 con Podman 2.x e libseccomp 2.4 (`5a6772f`).
- Il numero di worker di MODELLER (`modeller_workers`) non entra più nel confronto delle riesecuzioni.
- Stima del tempo di costruzione lineare più quadratica nei residui, dalle misure ripetute fino a 4000 residui: prima
  era lineare e sottostimava di 13 volte a 4000 residui, e l'avviso `timeout_risk` non scattava sui job lunghi.
- Pacchetti di Ubuntu dell'immagine da un'istantanea fissata dell'archivio (`UBUNTU_SNAPSHOT`).
- `certifi` 2026.10.19 (PYSEC-2026-4320).

### Corretto
- Accessibilità: nome accessibile del pulsante della ricerca globale e dei select dei selettori di struttura, sequenza
  e template (trovati da axe-core).

## [4.2.0] - 2026-10-11

Merge `a9a30a9` (versione portata a 4.2.0 in `95bb798`, 10 ottobre).

### Aggiunto
- Versione unica (`programma/launcher/VERSION` e `versione.sh`), impronta del codice nell'immagine, comando
  `aggiorna` dell'avviatore, licenza MIT (`95bb798`, `a9a30a9`).
- Avvio su server Linux con Podman senza compose (`5f417ce`).
- Avviso per email alla fine delle pipeline, con il server di posta solo da file (`1c77e2b`, `661f97b`).
- Acque catalitiche scelte una per una ed esempi con HETATM (`a6b50a6`).
- Grafici PostScript di PROCHECK come impostazione (`878fe5c`).
- Pipeline grandi, fino a 50.000 sequenze e 256 in parallelo (`156f00b`).
- Editor PIR interattivo, allineamento sequenza-struttura, pannelli richiudibili (`079d444`, `ec6cb1d`).
- Report completo e pipeline sequenze × template; un'unica tabella delle metriche nello ZIP (`b947fbb`, `7cdec67`).
- Eliminazione dei progetti e pulizia di massa dei job (`184e3b0`).
- Risorse di HHblits nell'uscita del QMEAN locale (`0b4a34c`).

### Modificato
- Cartella più chiara: codice in `programma/`, manuale in `Guida/` (`e2fd66f`).
- ProSA un computer alla volta (licenza legata all'indirizzo di rete) (`95bb798`).

### Corretto
- Cookie `Secure` dietro proxy e documentazione allineata al codice (`b5d9905`).
- L'avviso del disordine coperto cita anche l'errore del predittore (`a5f5e4d`).
- Avviatore Windows: avvio anche senza il disco di UniClust collegato (`c4f0f74`).

## [4.1.0] - 2026-09-29

Prima versione di BioLoom v4 (`a22084a`), riscrittura in Docker, e piano di evoluzione F0–F9 completato fra il 30
settembre e il 2 ottobre 2026: integrazione continua e test nel container (F0); cache di ricerca e allineamento,
ripresa delle pipeline (F1.1, F2.1); profilazione dei tempi, budget di CPU, coda con priorità e stima del tempo
residuo (F1.2–F1.4, F2.2–F2.4); qualità per residuo e consenso (F3, F6); confronto strutturale e report HTML (F4);
modelli AlphaFold DB, scelta dei template, complessi, ligandi e regioni disordinate (F5); analisi strutturale,
tasche ed elettrostatica (F7, F7b); procedura guidata, controlli preventivi e ricerca globale (F8); rifinitura,
mutagenesi, confronto con ESMFold e pacchetti di riproducibilità (F9).

[4.3.1]: https://github.com/foggy-code/BioLoom/compare/v4.3.0...v4.3.1
[4.3.0]: https://github.com/foggy-code/BioLoom/compare/v4.2.0...v4.3.0
[4.2.0]: https://github.com/foggy-code/BioLoom/compare/v4.1.0...v4.2.0
[4.1.0]: https://github.com/foggy-code/BioLoom/releases/tag/v4.1.0
