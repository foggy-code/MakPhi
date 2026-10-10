# Licenze

Guida di BioLoom: [1. Primo avvio](1%20-%20Primo%20avvio.md) · [2. Funzioni](2%20-%20Funzioni.md) · [3. QMEAN e UniClust](3%20-%20QMEAN%20e%20UniClust.md) · [4. Dati e privacy](4%20-%20Dati%20e%20privacy.md) · [5. Chiavetta e server](5%20-%20Chiavetta%20e%20server.md) · **6. Licenze**

## BioLoom

BioLoom è software libero con licenza **MIT** (© 2026 foggy02, testo in [LICENSE](../LICENSE)): il codice in
`programma/`, gli avviatori e questa guida si possono usare, copiare, modificare e ridistribuire, purché resti l'avviso di
copyright e di licenza.

La licenza MIT non copre i programmi di terzi che BioLoom usa. Le immagini Docker (`bioloom`, `bioloom-qmean`) e
l'interfaccia web contengono componenti con licenze proprie, elencati con versione e licenza in
[THIRD_PARTY_NOTICES.md](../THIRD_PARTY_NOTICES.md), che indica anche dove trovare il sorgente del software GPL e LGPL
delle immagini (su richiesta, per tre anni). MODELLER, PROCHECK e ProSA II hanno invece licenze accademiche personali: le
immagini che li contengono, anche quelle nella cartella `immagini/` della copia portatile, non vanno date ad altri. Chi
riceve BioLoom costruisce le immagini e ottiene le licenze per conto proprio.

## MODELLER, ProSA II e PROCHECK

- **MODELLER:** si inserisce da Impostazioni → Licenze e viene provata con MODELLER prima di essere salvata. Su un server
  si può indicare anche `MODELLER_KEY` in `server.env`. Compare sempre mascherata (`MOD••••••A9`), anche nella verifica
  dell'installazione e nei log dei job.
- **ProSA II:** si carica il file `LicenseDB` da Impostazioni → Licenze; l'avviatore assegna al container il MAC indicato
  nella licenza, su un computer alla volta ([sotto](#prosa-un-computer-alla-volta)).
- **PROCHECK:** licenza accademica, senza chiave.

Le chiavi restano in `data/licenses/` e non entrano mai nelle immagini Docker. I pacchetti PROCHECK e ProSA
(`programma/docker/vendor/`) e le chiavi sono tuoi: non distribuirli ad altri. La copia portatile non contiene i
pacchetti né, salvo `-ConLicenze` / `--con-licenze`, le chiavi; le sue immagini però hanno i programmi già installati
([Chiavetta e server](5%20-%20Chiavetta%20e%20server.md#copia-portatile-chiavetta-usb)).

## ProSA, un computer alla volta

La licenza di ProSA II vale per un singolo computer e vieta di cambiarne la configurazione per aggirarla, mentre la
cartella dati di BioLoom può passare da un computer all'altro (chiavetta, copia portatile con `-ConLicenze` /
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
  (`./avvia-bioloom.sh server --sposta-prosa`). Da quel momento la licenza vale qui, e l'altro computer al suo avvio la
  trova attiva altrove.
- **Una licenza per computer:** in alternativa chiedi agli autori di ProSA una chiave per il nuovo computer e caricala da
  Impostazioni → Licenze.

La riga **ProSA** nella finestra dell'avviatore dice quale caso vale: licenza attivata su questo computer, licenza di questo
computer, licenza spostata su questo computer, licenza attiva su un altro computer (MAC non applicato), nessuna licenza.
Sul server il controllo vale quando `BIOLOOM_MAC` in `server.env` è il MAC della licenza
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
