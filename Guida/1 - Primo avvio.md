# Primo avvio

Guida di BioLoom: **1. Primo avvio** · [2. Funzioni](2%20-%20Funzioni.md) · [3. QMEAN e UniClust](3%20-%20QMEAN%20e%20UniClust.md) · [4. Dati e privacy](4%20-%20Dati%20e%20privacy.md) · [5. Chiavetta e server](5%20-%20Chiavetta%20e%20server.md) · [6. Licenze](6%20-%20Licenze.md)

## Avvio

1. Installa **Docker Desktop** (Windows, macOS) oppure **Docker Engine** (Linux): <https://www.docker.com/products/docker-desktop/>.
2. Fai doppio clic sull'avviatore:

   | Sistema | Avvio | Arresto |
   |---|---|---|
   | Windows | `Avvia BioLoom.bat` | `Arresta BioLoom.bat` |
   | macOS | `Avvia BioLoom.command` | `Arresta BioLoom.command` |
   | Linux | `./avvia-bioloom.sh` | `./avvia-bioloom.sh arresta` |

3. Si apre il browser su `http://127.0.0.1:8765` (se la porta è occupata, la prima libera fino alla 8800). Al primo
   avvio inserisci la chiave di licenza di MODELLER in **Impostazioni → Licenze**: è gratuita per uso accademico
   (<https://salilab.org/modeller/>).

L'avviatore fa da solo il resto:

- avvia Docker Desktop se è spento (Windows e macOS);
- carica le immagini dalla cartella `immagini/` (senza internet) oppure le costruisce la prima volta;
- trova il disco con **UniClust30/UniRef30** per QMEAN locale, in qualunque versione, anche se cambia la lettera di
  unità o il punto di montaggio;
- assegna al container il MAC richiesto dalla licenza **ProSA**, sul computer a cui la licenza è legata
  ([un computer alla volta](6%20-%20Licenze.md#prosa-un-computer-alla-volta));
- sceglie una porta libera;
- si accorge se l'immagine Docker non corrisponde più al codice in `programma/` e propone di aggiornarla
  ([sotto](#aggiornare-bioloom)).

Dalla sua finestra: **A** riapre il browser, **V** verifica gli strumenti, **L** mostra i log, **R** riavvia rifacendo la
ricerca dei dischi (dopo aver collegato quello di UniClust30), **U** indica a mano la cartella di UniClust30, **S** arresta
BioLoom, **C** chiude la finestra lasciando BioLoom attivo.

### macOS: doppio clic bloccato

I file scaricati da internet (zip di GitHub, allegati di posta) restano in quarantena e macOS non apre
`Avvia BioLoom.command`. La prima volta fai clic destro sul file → **Apri** e conferma (da macOS 15: Impostazioni di
Sistema → Privacy e sicurezza → **Apri comunque**), oppure togli la quarantena a tutta la cartella dal Terminale. Se il
doppio clic dice invece che mancano i permessi, rendi eseguibili gli avviatori:

```bash
cd /percorso/di/BioLoom        # scrivi "cd " e trascina la cartella nel Terminale
xattr -dr com.apple.quarantine .
chmod +x "Avvia BioLoom.command" "Arresta BioLoom.command" avvia-bioloom.sh programma/launcher/bioloom.sh   # permessi
```

## Un percorso tipico

| Passo | Dove | Che cosa succede |
|---|---|---|
| 1. Sequenza | **Sequenze** | incolli, carichi da file o importi da UniProt la sequenza; vedi proprietà, struttura secondaria predetta e la mappa di disordine, bassa complessità e domini |
| 2. Template | **Ricerca template**, **Libreria template** | BioLoom cerca strutture omologhe, dà a ognuna un punteggio motivato e propone i template consigliati; **Usa i consigliati per la modellazione** porta al passo successivo |
| 3. Allineamento | **Modellazione** o **Editor PIR** | MODELLER allinea il bersaglio ai template; nell'Editor PIR l'allineamento si corregge a mano |
| 4. Modello | **Modellazione** | i controlli prima dell'avvio segnalano i problemi; MODELLER costruisce i modelli e sceglie il migliore (DOPE minimo) |
| 5. Qualità | **Qualità** | voto A–D, consenso tra i metodi, problemi per residuo colorati sul 3D, confronto con il template o con una predizione |
| 6. Report | pulsante **Report** | un file HTML autonomo con tutto il percorso, da archiviare o stampare in PDF |

**Nuovo modello guidato** (barra laterale e Panoramica) fa lo stesso percorso in sei passi, con valori predefiniti
spiegati: sequenza (incollata, dal progetto, da file o da UniProt) → template (motore consigliato e template consigliati
già scelti) → allineamento (si apre nell'Editor PIR, oppure si usa un documento PIR del progetto) → opzioni (5 modelli,
raffinamento Rapido, analisi estesa se PROCHECK o ProSA ci sono, ligandi se i template ne hanno, ritaglio pLDDT per i
template AlphaFold) → avvio con l'avanzamento in tempo reale → risultati (modello migliore, voto, consenso, con
**Qualità**, **Confronta con il template** e **Report**). Il passo e i job stanno nell'indirizzo della pagina: si ricarica,
si torna indietro con il browser, si riprende più tardi o da un altro computer con lo stesso link.

Per molte sequenze insieme c'è la **Pipeline batch** ([sotto](2%20-%20Funzioni.md#pipeline-job-e-coda-dei-calcoli)).

## Aggiornare BioLoom

1. Arresta BioLoom e sostituisci nella sua cartella `programma/`, gli avviatori, `Guida/`, `README.md`, `LEGGIMI.txt`,
   `LICENSE` e `THIRD_PARTY_NOTICES.md`
   con quelli della nuova versione (anche `immagini/`, se la nuova versione ce l'ha). `data/` resta com'è: progetti,
   risultati e licenze non si toccano, e se la nuova versione cambia la struttura del database la aggiorna da sola dopo
   averne salvato una copia ([Dati e privacy](4%20-%20Dati%20e%20privacy.md#dove-stanno-i-dati)). Se hai i pacchetti di
   PROCHECK e ProSA in `programma/docker/vendor/`, mettili da parte e rimettili nella nuova `programma/` prima di
   avviare: la nuova versione non li contiene e un'immagine ricostruita senza sarebbe priva dei due strumenti
   ([pacchetti con licenza](../programma/docker/vendor/README.md)).
2. Avvia come sempre. Con un numero di versione nuovo l'avviatore carica o costruisce le immagini come al primo avvio.
   Se la versione è la stessa ma il codice è cambiato (per esempio una correzione), se ne accorge confrontando il codice
   in `programma/` con l'impronta salvata nell'immagine e chiede «Aggiornarla ora? [S/n]»: con Invio carica l'immagine
   della cartella `immagini/`, se corrisponde al codice, altrimenti la ricostruisce (serve internet; pochi minuti, 10–20
   la prima volta). Con **n** BioLoom parte con l'immagine che c'era. Se per ricostruirla mancano i pacchetti di
   PROCHECK o ProSA, l'avviatore lo dice prima e la domanda diventa «[s/N]»: con Invio non ricostruisce, così non
   perdi i due strumenti per sbaglio.

Si può anche aggiornare senza avviare BioLoom; se è acceso, viene riavviato con le immagini nuove e i calcoli in corso si
interrompono:

```bat
powershell -ExecutionPolicy Bypass -File programma\launcher\bioloom.ps1 -Azione aggiorna
```

```bash
./avvia-bioloom.sh aggiorna
```

L'azione aggiorna anche l'immagine di QMEAN, se QMEAN locale è in uso. Una ricostruzione include PROCHECK e ProSA solo se
i loro pacchetti sono in `programma/docker/vendor/`, che la copia portatile non contiene: in quel caso usa la cartella
`immagini/` della nuova copia portatile. Le immagini sostituite restano su disco: si cancellano da Docker Desktop
(Images), oppure con `docker image rm bioloom:<versione vecchia>` e, per quelle rimaste senza nome, `docker image prune`.
