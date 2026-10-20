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

## [4.3.0] - non ancora rilasciata

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
- Copertura dei test misurata in CI (pytest-cov nel job docker, `@vitest/coverage-v8` nel job frontend).
- `CITATION.cff` e `.zenodo.json`; `versione.sh` tiene allineata anche la loro versione.
- Questo registro delle modifiche e i tag `v4.1.0` e `v4.2.0`.

### Modificato
- QMEAN: tutti i posti del servizio e una sola ricerca HHblits per sequenza (`83d8994`); calcoli contemporanei e
  profili HHblits riusati (`044f594`).
- Podman: rimozione dei container con attesa, `aggiorna` non lascia BioLoom spento (`06f7032`); profilo seccomp su
  misura per Podman 2.x (`6773e84`) e MMseqs2 con Podman 2.x e libseccomp 2.4 (`5a6772f`).
- Il numero di worker di MODELLER (`modeller_workers`) non entra più nel confronto delle riesecuzioni.

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

[4.3.0]: https://github.com/foggy-code/BioLoom/compare/v4.2.0...HEAD
[4.2.0]: https://github.com/foggy-code/BioLoom/compare/v4.1.0...v4.2.0
[4.1.0]: https://github.com/foggy-code/BioLoom/releases/tag/v4.1.0
