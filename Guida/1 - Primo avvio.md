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
- assegna al container il MAC richiesto dalla licenza **ProSA**;
- sceglie una porta libera.

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
