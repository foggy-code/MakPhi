# Licenze

Guida di MakPhi: [1. Primo avvio](1%20-%20Primo%20avvio.md) · [2. Funzioni](2%20-%20Funzioni.md) · [3. QMEAN e UniClust](3%20-%20QMEAN%20e%20UniClust.md) · [4. Dati e privacy](4%20-%20Dati%20e%20privacy.md) · [5. Chiavetta e server](5%20-%20Chiavetta%20e%20server.md) · **6. Licenze**

## MakPhi

MakPhi è software libero con licenza **MIT** (© 2026 foggy02, testo in [LICENSE](../LICENSE)): il codice in
`programma/`, gli avviatori e questa guida si possono usare, copiare, modificare e ridistribuire, purché resti l'avviso di
copyright e di licenza.

La licenza MIT non copre i programmi di terzi che MakPhi usa. Le immagini Docker (`makphi`, `makphi-qmean`) e
l'interfaccia web contengono componenti con licenze proprie, elencati con versione e licenza in
[THIRD_PARTY_NOTICES.md](../THIRD_PARTY_NOTICES.md), che indica anche dove trovare il sorgente del software GPL e LGPL
delle immagini (su richiesta, per tre anni). MODELLER, PROCHECK e ProSA II hanno invece licenze accademiche personali: le
immagini che li contengono, anche quelle nella cartella `immagini/` della copia portatile, non vanno date ad altri. Chi
riceve MakPhi costruisce le immagini e ottiene le licenze per conto proprio.

## MODELLER, ProSA II e PROCHECK

- **MODELLER:** si inserisce da Impostazioni → Licenze e viene provata con MODELLER prima di essere salvata. Su un server
  si può indicare anche `MODELLER_KEY` in `server.env`. Compare sempre mascherata (`MOD••••••A9`), anche nella verifica
  dell'installazione e nei log dei job.
- **ProSA II:** si carica il file `LicenseDB` da Impostazioni → Licenze; l'avviatore assegna al container il MAC indicato
  nella licenza, su un computer alla volta ([sotto](#prosa-un-computer-alla-volta)).
- **PROCHECK:** licenza accademica, senza chiave.

PROCHECK e ProSA vanno anche aggiunti all'immagine: la procedura completa è in
[Installare e attivare PROCHECK e ProSA](#installare-e-attivare-procheck-e-prosa).

Le chiavi restano in `data/licenses/` e non entrano mai nelle immagini Docker. I pacchetti PROCHECK e ProSA
(`programma/docker/vendor/`) e le chiavi sono tuoi: non distribuirli ad altri. La copia portatile non contiene i
pacchetti né, salvo `-ConLicenze` / `--con-licenze`, le chiavi; le sue immagini però hanno i programmi già installati
([Chiavetta e server](5%20-%20Chiavetta%20e%20server.md#copia-portatile-chiavetta-usb)).

## Installare e attivare PROCHECK e ProSA

PROCHECK e ProSA II servono all'**analisi estesa** della qualità (Ramachandran di PROCHECK, profilo e z-score di ProSA).
MakPhi funziona anche senza: l'analisi prosegue con DOPE, mkdssp, clash e le altre misure, e lo dice. Le licenze non
permettono di distribuirli, quindi né GitHub né la copia portatile li contengono: ognuno li ottiene per sé e li
aggiunge alla propria immagine. I passi sono gli stessi su Windows, macOS e Linux; per il server vedi il punto 7.

1. **Ottieni le licenze.**
   - **PROCHECK:** licenza accademica gratuita, previa registrazione presso l'EBI (R. Laskowski; pagina del programma:
     <https://www.ebi.ac.uk/thornton-srv/software/PROCHECK/>). Ricevi il pacchetto dei sorgenti, `procheck.tar.gz`;
     non c'è nessuna chiave.
   - **ProSA II 2003:** licenza personale da <https://prosa.services.came.sbg.ac.at>, legata all'indirizzo MAC di un
     computer. Nella richiesta indica il MAC della scheda di rete del computer su cui userai MakPhi (su Windows
     `getmac /v` nel Prompt dei comandi, su macOS `ifconfig en0`, su Linux `ip link`: sono sei coppie di cifre come
     `80:b6:55:12:34:56`). Ricevi il programma, `prosa2003.linux.tar.gz`, e per email il file `LicenseDB`.
2. **Metti i pacchetti nella cartella di MakPhi**, senza rinominarli e senza scompattarli:

   | File | Cartella |
   |---|---|
   | `procheck.tar.gz` (deve contenere la cartella `procheck/` con i sorgenti) | `programma/docker/vendor/procheck/` |
   | `prosa2003.linux.tar.gz` | `programma/docker/vendor/prosa/` |

   Il file `LicenseDB` **non** va qui: si carica dall'interfaccia (punto 4).
3. **Ricostruisci l'immagine.** L'avviatore non si accorge dei pacchetti nuovi (la cartella `vendor/` è fuori
   dall'impronta del codice), quindi la ricostruzione va lanciata a mano. Dalla cartella di MakPhi, in un terminale
   (su Windows anche in PowerShell), con Docker Desktop avviato:

   ```bash
   docker compose -f programma/launcher/compose.yml build makphi
   ```

   La prima volta richiede qualche minuto: PROCHECK si compila dai sorgenti. Se il pacchetto di PROCHECK non si compila,
   la costruzione si ferma con l'errore; un pacchetto assente invece viene saltato senza errori.
4. **Carica la licenza di ProSA.** Riavvia MakPhi con l'avviatore (**R** nella sua finestra), apri Impostazioni →
   **Licenze** e trascina il file `LicenseDB` nella scheda **ProSA II 2003**.
5. **Riavvia ancora con l'avviatore (R).** È all'avvio che l'avviatore legge il MAC da `LicenseDB` e lo assegna al
   container, e lega la licenza a questo computer ([sotto](#prosa-un-computer-alla-volta)).
6. **Verifica.**
   - Impostazioni → Licenze: la scheda **PROCHECK** dice «PROCHECK è installato»; la scheda **ProSA II 2003** mostra
     programma, scadenza e «MAC del container … corrisponde». Se il MAC risulta «diverso», chiudi MakPhi e riaprilo con
     l'avviatore (punto 5); se compare «ProSA non è incluso in questa immagine», il pacchetto non era al suo posto quando
     hai ricostruito (punti 2 e 3).
   - Impostazioni → **Strumenti** → **Verifica dell'installazione**: prova ogni strumento, PROCHECK e ProSA compresi,
     sull'ubiquitina 1UBQ in meno di un minuto.
   - La riga **ProSA** nella finestra dell'avviatore dice se la licenza è attiva su questo computer.
7. **Sul server con Podman.** PROCHECK funziona come sul PC. ProSA **no**: Podman senza root non può assegnare al
   container il MAC della licenza, e l'analisi prosegue senza ProSA. Sul server, inoltre, l'immagine non si costruisce: la
   si costruisce sul PC con i pacchetti al loro posto e la si copia sul server come archivio
   ([Server con Podman](5%20-%20Chiavetta%20e%20server.md#server-con-podman-senza-docker),
   [Licenze sul server](5%20-%20Chiavetta%20e%20server.md#licenze-sul-server)).
8. **Quando aggiorni MakPhi.** Sostituendo `programma/` con una versione nuova (da GitHub o da una copia portatile),
   la cartella `vendor/` arriva vuota: metti da parte i due pacchetti e rimettili prima di avviare, altrimenti la
   ricostruzione dell'immagine darà un'immagine senza PROCHECK e ProSA. Le licenze in `data/licenses/` invece restano.

I pacchetti e `LicenseDB` sono tuoi: non pubblicarli e non darli ad altri. Git li ignora, e la copia portatile non
contiene i pacchetti né, salvo `-ConLicenze` / `--con-licenze`, le chiavi; le **immagini** costruite con i pacchetti li
contengono invece già installati, e anche loro non vanno date ad altri.

## ProSA, un computer alla volta

La licenza di ProSA II vale per un singolo computer e vieta di cambiarne la configurazione per aggirarla, mentre la
cartella dati di MakPhi può passare da un computer all'altro (chiavetta, copia portatile con `-ConLicenze` /
`--con-licenze`). Per questo l'avviatore applica il MAC di `LicenseDB` solo sul computer a cui la licenza è legata:

- **Prima volta:** il primo computer che applica il MAC lega a sé la licenza nel file `data/licenses/prosa/computer`
  (una riga per licenza: il MAC e 16 cifre che identificano il computer, ricavate con un SHA-256 dall'identificativo del
  sistema, che non viene salvato). Se l'identificativo del computer non si ottiene, il MAC si applica senza controllo.
- **Su un altro computer** il MAC della licenza non si applica: il container riceve il MAC che avrebbe senza licenza e
  l'analisi prosegue senza ProSA (PROCHECK, DOPE e le altre misure restano). L'avviatore lo spiega: «ProSA: la licenza è
  attiva su un altro computer (vale per un computer alla volta). Per usarla qui chiedi una chiave per questo computer,
  oppure spostala qui (l'altro computer la perde)».
- **Spostare la licenza:** rispondi **s** alla domanda «Spostare la licenza ProSA su questo computer? [s/N]», che
  l'avviatore pone a ogni avvio (anche con **R**; Invio = no). Senza la finestra dell'avviatore (`-NoMenu` /
  `--no-menu`) e sul server si sposta con `-SpostaProsa` su Windows e `--sposta-prosa` su macOS, Linux e server
  (`./avvia-makphi.sh server --sposta-prosa`). Da quel momento la licenza vale qui, e l'altro computer al suo avvio la
  trova attiva altrove.
- **Una licenza per computer:** in alternativa chiedi agli autori di ProSA una chiave per il nuovo computer e caricala da
  Impostazioni → Licenze.

La riga **ProSA** nella finestra dell'avviatore dice quale caso vale: licenza attivata su questo computer, licenza di questo
computer, licenza spostata su questo computer, licenza attiva su un altro computer (MAC non applicato), nessuna licenza.
Sul server il controllo vale quando `MAKPHI_MAC` in `server.env` è il MAC della licenza
([Chiavetta e server](5%20-%20Chiavetta%20e%20server.md#licenze-sul-server)).

## Strumenti open source dell'immagine

Aggiunti per tasche, elettrostatica e rifinitura (versioni fissate in `programma/docker/Dockerfile` e
`programma/backend/requirements-tools.lock`; insieme circa 140 MB):

| Strumento | Versione | Licenza | Installazione |
|---|---|---|---|
| fpocket | 4.2.3 | MIT | compilato dalla release ufficiale (non è fra i pacchetti di Ubuntu 24.04) |
| APBS | 3.4.1 | BSD 3 clausole; il binario contiene FETK (LGPL 2.1 o successiva) | binario ufficiale verificato con SHA-256 (il pacchetto di Ubuntu porterebbe circa 340 MB di dipendenze) |
| PDB2PQR | 3.7.1 | BSD 3 clausole | pip |
| PROPKA | 3.5.1 | LGPL 2.1 | pip (usato da PDB2PQR) |
| OpenMM | 8.6.1 | MIT (librerie, piattaforme Reference e CPU) e LGPL 3.0 o successiva (plugin della piattaforma OpenCL, presenti nell'immagine ma non usati) | pip |
| PDBFixer | 1.12.0 | MIT | pip |

Gli altri strumenti dell'immagine (MODELLER 10.8, mkdssp, BLAST+, MMseqs2, Clustal Omega, MAFFT, MUSCLE, TM-align,
US-align) sono elencati in [Architettura → Vista d'insieme](../programma/docs/ARCHITETTURA.md#vista-dinsieme) e, con
versioni e licenze, in [THIRD_PARTY_NOTICES.md](../THIRD_PARTY_NOTICES.md). Un'immagine costruita prima di questi
strumenti continua a funzionare: tasche, elettrostatica e rifinitura dicono che lo strumento manca.
