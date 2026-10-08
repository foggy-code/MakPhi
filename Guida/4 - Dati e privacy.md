# Dati e privacy

Guida di BioLoom: [1. Primo avvio](1%20-%20Primo%20avvio.md) · [2. Funzioni](2%20-%20Funzioni.md) · [3. QMEAN e UniClust](3%20-%20QMEAN%20e%20UniClust.md) · **4. Dati e privacy** · [5. Chiavetta e server](5%20-%20Chiavetta%20e%20server.md) · [6. Licenze](6%20-%20Licenze.md)

## Dove stanno i dati

| Cosa | Dove | Viaggia con la chiavetta? |
|---|---|---|
| Progetti, risultati dei job, libreria template, licenze, impostazioni, pdbaa e database MMseqs2 | `data/`, nella cartella di BioLoom | sì |
| Risposte dei servizi remoti e cache dei calcoli | `data/cache/` | sì |
| Calcoli in corso e file temporanei | volume Docker `scratch`, veloce e svuotato a ogni avvio | no |
| Indice veloce MMseqs2 (≈ 1,5 GB) | volume Docker `cache` di ogni computer | no, si ricrea da Impostazioni → Database |
| UniClust30/UniRef30 e QMTL | disco esterno, montato in sola lettura | resta sul disco |
| Copia locale di indici e cs219 di UniClust30; cs219 ricreati per i database HH-suite 2 | volume Docker `qmean_cache` di ogni computer | no |

- **Risultati:** a fine calcolo in `data/` vengono salvati solo i risultati; i file intermedi di MODELLER restano fuori,
  salvo l'impostazione che li conserva (Impostazioni → Spazio e file temporanei). Non staccare la chiavetta mentre BioLoom
  è in funzione.
- **Grafici di PROCHECK:** l'analisi estesa salva i grafici PostScript di PROCHECK (circa 3,7 MB per modello di 420
  residui, quasi tutto lo spazio dei risultati). In Impostazioni → Esecuzione, **Grafici PostScript di PROCHECK** sceglie
  se conservarli tutti, solo il Ramachandran complessivo (circa 0,1 MB) o nessuno; le metriche di PROCHECK restano le
  stesse, perché vengono dal riepilogo. Vale per le analisi fatte dopo il cambio.
- **Pulizia automatica** (all'avvio, ogni 24 ore e con **Pulisci file temporanei** in Impostazioni → Spazio e file
  temporanei, che mostra anche lo spazio occupato): file caricati più vecchi dei giorni impostati (predefinito 30), voci
  di cache oltre i 30 giorni, file temporanei rimasti, resti di job che hai già eliminato (file che erano in uso).
  Risultati dei job, libreria template, database e licenze non vengono mai toccati.
- **Eliminazione manuale:** i risultati dei job si eliminano solo a mano e con conferma: un job dal suo dettaglio, molti
  insieme con **Pulisci job…**, un progetto intero dal dialogo **Progetti** ([sopra](2%20-%20Funzioni.md#pipeline-job-e-coda-dei-calcoli)).
  Non si può annullare: quello che elimini non si recupera. Lo spazio liberato è quello dei file dei job; il file del
  database non si restringe.
- **Cache dei calcoli:** ricerche di template e allineamenti multipli e strutturali già calcolati con gli stessi dati e
  parametri non si rifanno, e il risultato riporta **dalla cache (data)**. Le ricerche su RCSB, NCBI e AlphaFold DB valgono
  7 giorni (il PDB si aggiorna ogni settimana); le altre finché non cambiano il database locale, il programma o il
  contenuto delle strutture. In Ricerca, Allineamenti e Pipeline **Ricalcola ignorando la cache** rifà il calcolo; la
  cache si disattiva in Impostazioni → Spazio e file temporanei.
- **Aggiornamenti:** quando una nuova versione di BioLoom aggiorna la struttura del database (`data/bioloom.db`), lo fa da
  sola all'avvio dopo averne salvato una copia in `data/backups/` (una per aggiornamento; si può cancellare quando tutto
  funziona: BioLoom non lo fa da solo, e Impostazioni → Spazio e file temporanei mostra quanto occupano). Se
  l'aggiornamento non riesce, il database resta com'era e BioLoom lo dice all'avvio.

## Che cosa lascia il computer

Ricerche locali (BLAST+, MMseqs2), QMEAN locale, analisi di qualità, analisi strutturali, rifinitura e mutagenesi non
usano la rete. L'interfaccia non carica nulla da internet. Escono dal computer solo queste richieste, e solo quando lanci
il calcolo indicato:

| Servizio | Che cosa riceve | Quando |
|---|---|---|
| RCSB PDB, ricerca (`search.rcsb.org`) | la **sequenza** bersaglio | ricerca di template con il motore RCSB (il predefinito), anche dalla pipeline e dalla procedura guidata |
| RCSB PDB, file e metadati (`files.rcsb.org`, `data.rcsb.org`) | codici PDB | download di un template; metodo, risoluzione e titolo degli hit di ogni motore sul PDB |
| NCBI BLAST (`blast.ncbi.nlm.nih.gov`) | la **sequenza** e, se l'hai impostata, la tua **email** (lo chiedono le linee guida di NCBI) | motore NCBI BLAST; motore AlphaFold DB senza accessione (BLAST su Swiss-Prot) |
| UniProt (`rest.uniprot.org`) | l'accessione, oppure il testo cercato | importazione da UniProt, annotazioni e domini della pagina Sequenze, varianti per la mutagenesi |
| InterPro (EBI) | l'accessione UniProt | domini delle sequenze con accessione (pagina Sequenze, avvisi di copertura, controlli prima dell'avvio) |
| AlphaFold DB (EBI) | l'accessione UniProt | modelli in libreria, motore AlphaFold DB, confronto con AlphaFold DB |
| SWISS-MODEL (QMEAN) | le **coordinate** del modello e la tua **email** | QMEAN su SWISS-MODEL, scelto o come fallback: solo con il consenso all'invio |
| ESMFold (ESM Atlas `api.esmatlas.com`, o l'indirizzo impostato) | la **sequenza** di una catena, mai le coordinate | confronto con una predizione ESMFold: solo con il consenso, chiesto dal pulsante la prima volta e revocabile in Impostazioni → Generale |
| NCBI FTP | nulla (solo download) | aggiornamento di pdbaa da Impostazioni → Database |
| Il tuo server di posta (SMTP, Impostazioni → Notifiche email) | un messaggio per l'indirizzo indicato: titolo della pipeline, esito, orari, durata, numero di elementi per stato, eventuale motivo dell'errore e link al job; **mai** risultati, sequenze, nomi delle sequenze o file | fine di una pipeline lanciata con «Avvisami per email alla fine»; email di prova |

Le risposte di UniProt, InterPro e AlphaFold DB restano in `data/cache` per 7 giorni, i metadati RCSB e le predizioni di
ESMFold per 30. Sul server l'indirizzo di ESMFold lo sceglie l'amministratore ([Chiavetta e server](5%20-%20Chiavetta%20e%20server.md#server)).
