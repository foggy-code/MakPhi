# Dati e privacy

Guida di MakPhi: [1. Primo avvio](1%20-%20Primo%20avvio.md) · [2. Funzioni](2%20-%20Funzioni.md) · [3. QMEAN e UniClust](3%20-%20QMEAN%20e%20UniClust.md) · **4. Dati e privacy** · [5. Chiavetta e server](5%20-%20Chiavetta%20e%20server.md) · [6. Licenze](6%20-%20Licenze.md)

## Dove stanno i dati

| Cosa | Dove | Viaggia con la chiavetta? |
|---|---|---|
| Progetti, risultati dei job, libreria template, impostazioni, pdbaa e database MMseqs2 | `data/`, nella cartella di MakPhi | sì |
| Chiavi di licenza (MODELLER, ProSA), computer su cui è attiva la licenza ProSA (`licenses/prosa/computer`, [un computer alla volta](6%20-%20Licenze.md#prosa-un-computer-alla-volta)), `posta.env` con la password della posta, chiave d'accesso del server | `data/licenses/`, `data/access_key` | sì se MakPhi gira dalla chiavetta; la [copia portatile](5%20-%20Chiavetta%20e%20server.md#copia-portatile-chiavetta-usb) le include solo con `-ConLicenze` / `--con-licenze` |
| Risposte dei servizi remoti e cache dei calcoli | `data/cache/` | sì |
| Calcoli in corso e file temporanei | volume Docker `scratch`, veloce e svuotato a ogni avvio | no |
| Indice veloce MMseqs2 (≈ 1,5 GB) | volume Docker `cache` di ogni computer | no, si ricrea da Impostazioni → Database |
| UniClust30/UniRef30 e QMTL | disco esterno, montato in sola lettura | resta sul disco |
| Copia locale di indici e cs219 di UniClust30; cs219 ricreati per i database HH-suite 2 | volume Docker `qmean_cache` di ogni computer | no |

- **Risultati:** a fine calcolo in `data/` vengono salvati solo i risultati; i file intermedi di MODELLER restano fuori,
  salvo l'impostazione che li conserva (Impostazioni → Spazio e file temporanei). Non staccare la chiavetta mentre MakPhi
  è in funzione.
- **File caricati:** sequenze e allineamenti caricati da file vengono solo letti, mai salvati; le strutture da
  analizzare finiscono in `data/uploads/` (pulizia automatica, sotto) e i template nella libreria. Limiti: 200 MB per
  sequenze e allineamenti (al più 100.000 sequenze), 50 MB per una struttura o un template, 256 MB per un pacchetto di riproducibilità. Un file più
  grande viene rifiutato («File troppo grande: massimo 50 MB.») e non entra nella cartella dati; una richiesta oltre
  257 MB viene interrotta appena arriva, e il browser può segnalarla come errore di connessione.
- **Grafici di PROCHECK:** l'analisi estesa salva i grafici PostScript di PROCHECK (circa 3,7 MB per modello di 420
  residui, quasi tutto lo spazio dei risultati). In Impostazioni → Esecuzione, **Grafici PostScript di PROCHECK** sceglie
  se conservarli tutti, solo il Ramachandran complessivo (circa 0,1 MB) o nessuno; le metriche di PROCHECK restano le
  stesse, perché vengono dal riepilogo. Vale per le analisi fatte dopo il cambio.
- **Pulizia automatica** (all'avvio, ogni 24 ore e con **Pulisci file temporanei** in Impostazioni → Spazio e file
  temporanei, che mostra anche lo spazio occupato): file caricati più vecchi dei giorni impostati (predefinito 30), voci
  di cache oltre i 30 giorni, voci della cache dei calcoli usate meno di recente se la cache supera il suo spazio massimo
  (sotto), file temporanei rimasti, resti di job che hai già eliminato (file che erano in uso).
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
- **Spazio massimo della cache dei calcoli (MB)** (Impostazioni → Spazio e file temporanei, accanto alla cache; da 100 a
  102400, predefinito 2048): oltre il limite MakPhi elimina le voci usate meno di recente (scritte o riusate per ultime
  da più tempo) finché la cache scende al 90% del limite. Lo fa all'avvio e con la pulizia automatica, dopo un calcolo
  che aggiunge una voce (il log del job lo dice: «Cache dei calcoli oltre il tetto delle Impostazioni: eliminate 12 voci
  usate meno di recente (210 MB).») e subito quando abbassi il limite. Le eliminazioni costano solo il ricalcolo, la
  prossima volta che serve. Un valore fuori intervallo scritto a mano in `settings.json` torna al predefinito senza
  toccare le altre impostazioni.
- **Chiavi e password:** la chiave di MODELLER compare sempre mascherata (`MOD••••••A9`): in Impostazioni, nella verifica
  dell'installazione e nei log dei job, anche quando MODELLER la ripete in un messaggio d'errore. I programmi che MakPhi
  esegue (MODELLER, PROCHECK, BLAST+…) non ricevono la password della posta né la chiave d'accesso del server.
- **Aggiornamenti** ([come si aggiorna MakPhi](1%20-%20Primo%20avvio.md#aggiornare-makphi), senza toccare `data/`):
  quando una nuova versione di MakPhi aggiorna la struttura del database (`data/makphi.db`), lo fa da
  sola all'avvio dopo averne salvato una copia in `data/backups/` (una per aggiornamento; si può cancellare quando tutto
  funziona: MakPhi non lo fa da solo, e Impostazioni → Spazio e file temporanei mostra quanto occupano). Se
  l'aggiornamento non riesce, il database resta com'era e MakPhi lo dice all'avvio.

## Avviso per email

L'avviso alla fine delle pipeline usa un server di posta (SMTP) che si configura solo da file, mai dall'interfaccia:
chi usa MakPhi sceglie soltanto se ricevere l'avviso e a quale indirizzo.

1. Copia `programma/launcher/posta.env.example` nella cartella dati come `licenses/posta.env` (per esempio
   `D:\BIOLum_v4\data\licenses\posta.env`).
2. Compila le righe: `MAKPHI_SMTP_HOST` (server), `MAKPHI_SMTP_PORT` e `MAKPHI_SMTP_SECURITY` (587 con
   `starttls`, 465 con `ssl`), `MAKPHI_SMTP_USER` e `MAKPHI_SMTP_PASSWORD` (account di posta), `MAKPHI_SMTP_FROM`
   (mittente) e, se MakPhi è raggiungibile da altri computer, `MAKPHI_PUBLIC_URL` per il link al job.
3. In Impostazioni → Generale → **Notifiche email** premi **Rileggi**, poi **Invia email di prova**. Non serve
   riavviare.

**Limiti.** Al più un'email di prova ogni 30 secondi e 5 all'ora per tutto MakPhi, anche con più utenti: oltre,
**Invia email di prova** risponde «È appena stata inviata un'email di prova: riprova tra 25 s.» oppure «Limite di 5 email
di prova all'ora raggiunto: riprova tra 40 min.». Gli avvisi di fine pipeline sono al più 10 all'ora allo stesso
indirizzo e 30 all'ora in tutto: oltre, l'avviso non parte e il log del job lo dice («Email di avviso a … non inviata:
limite di 10 avvisi all'ora allo stesso indirizzo raggiunto.»). Contano anche gli invii non riusciti. Così nessuno può
usare il server di posta per mandare messaggi a raffica.

Conviene un account dedicato agli avvisi, per esempio un indirizzo Gmail creato apposta con una «password per le app»
(Account Google → Sicurezza: verifica in due passaggi, poi Password per le app), oppure il server di posta dell'università.
Il file sta con le chiavi di licenza: non finisce nei pacchetti di riproducibilità, su Git o nella copia portatile (salvo
`-ConLicenze` / `--con-licenze`), e la password non viene mai mostrata dall'interfaccia. Sul server le stesse variabili
in `server.env` hanno la precedenza sul file.

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
| Il server di posta configurato (SMTP, file `licenses/posta.env` o `server.env`) | un messaggio per l'indirizzo indicato: titolo della pipeline, esito, orari, durata, numero di elementi per stato, eventuale motivo dell'errore e link al job; **mai** risultati, sequenze, nomi delle sequenze o file | fine di una pipeline lanciata con «Avvisami per email alla fine»; email di prova |

Le risposte di UniProt, InterPro e AlphaFold DB restano in `data/cache` per 7 giorni, i metadati RCSB e le predizioni di
ESMFold per 30. Sul server l'indirizzo di ESMFold lo sceglie l'amministratore ([Chiavetta e server](5%20-%20Chiavetta%20e%20server.md#server)).

RCSB, il motore predefinito, riceve la sequenza: nelle pagine Ricerca e Pipeline, sotto la scelta del motore, un avviso
lo ricorda quando il motore scelto è RCSB o NCBI. Per sequenze riservate usa BLAST+ o MMseqs2 locale (Impostazioni →
Database), e se vuoi rendilo il predefinito in Impostazioni → Generale: la sequenza non esce dal computer.

## Firma dei pacchetti di riproducibilità

Ogni pacchetto contiene l'impronta SHA-256 del suo manifest: un pacchetto modificato per sbaglio o di proposito viene
rifiutato. Chi lo modifica di proposito, però, può ricalcolare l'impronta. Per riconoscere i pacchetti prodotti dal tuo
gruppo metti una chiave segreta (un testo qualsiasi di almeno 16 caratteri) nel file `data/licenses/firma-manifest.key`,
lo stesso su tutti i computer del gruppo. Da quel momento i pacchetti esportati sono firmati, e all'apertura di un
pacchetto MakPhi dice se la firma è valida, se è di un'altra chiave o se manca. Un pacchetto modificato dopo la firma
viene rifiutato. La chiave non entra mai nei pacchetti; come le altre licenze resta fuori da Git e dalla copia portatile.

## Errori interni

Un errore imprevisto del server (un difetto di MakPhi, non un dato sbagliato) compare nell'interfaccia come «Errore
interno (codice 3f9a0c1b): i dettagli sono nel log di MakPhi.». Il messaggio non contiene percorsi, parametri o pezzi
di file, che restano nel log sul computer (o sul server) che fa girare MakPhi: lì c'è la riga «[MakPhi] Errore interno
(codice 3f9a0c1b) su POST /api/…:» seguita dalla traccia dell'errore. Subito dopo l'errore apri il log con
`./avvia-makphi.sh log`, che mostra le ultime 200 righe (Windows:
`powershell -ExecutionPolicy Bypass -File programma\launcher\makphi.ps1 -Azione log`), e cerca il codice. Il tasto
**L** della finestra dell'avviatore non basta: mostra solo le ultime 60 righe, e dopo la riga con il codice vengono la
traccia dell'errore e una sua seconda copia senza codice, stampata dal server web (in tutto più di 100 righe). Se nel
frattempo il log è andato avanti, cerca il codice del tuo messaggio in tutto il log:

```bash
docker logs makphi-makphi-1 2>&1 | grep -A 150 3f9a0c1b       # Linux e macOS
podman logs makphi 2>&1 | grep -A 150 3f9a0c1b                 # server con Podman
```

In PowerShell: `docker logs makphi-makphi-1 2>&1 | Select-String 3f9a0c1b -Context 0,150`. Per una segnalazione
allega quel tratto del log, dopo averlo riletto: può contenere percorsi e nomi di file del tuo computer.
