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

3. Si apre il browser su `http://127.0.0.1:8765`. Al primo avvio inserisci la chiave di licenza di
   MODELLER in **Impostazioni → Licenze**: è gratuita per uso accademico (<https://salilab.org/modeller/>).

L'avviatore fa da solo tutto il resto:

- avvia Docker Desktop se è spento;
- carica le immagini dalla cartella `immagini/` (senza internet) oppure le costruisce la prima volta;
- trova il disco con **UniClust30/UniRef30** per QMEAN, in qualunque versione, anche se cambia la lettera di
  unità o il punto di montaggio;
- applica il MAC richiesto dalla licenza **ProSA**;
- sceglie una porta libera.

Dalla sua finestra puoi:

- riaprire il browser;
- verificare gli strumenti;
- vedere i log;
- riavviare dopo aver collegato un disco (R, rifà la ricerca);
- indicare a mano la cartella di UniClust30 (U);
- arrestare BioLoom.

## QMEAN e UniClust30

- **Qualunque versione del database, anche più d'una sullo stesso disco:**
  - UniRef30 2020–2023;
  - uniclust30 2018_08;
  - uniclust30 2016–2017 in formato HH-suite 2, che si converte una volta da Impostazioni → Database;
  - database senza i file HMM.

  BioLoom usa la versione più recente pronta, oppure quella che scegli in Impostazioni → Database. Segnala gli
  archivi `.tar.gz` non estratti, le copie incomplete e i database in formato MMseqs2 non utilizzabili.
- **Copia locale degli indici (Windows e macOS):** Docker Desktop fa arrivare il disco nel container attraverso
  una condivisione di file. Questa è veloce a blocchi grandi ma lentissima per le letture piccole con cui
  HHblits scorre gli indici: solo caricarli richiedeva 15 minuti.
  - **Copia indici**, in Impostazioni → Database, porta su ogni computer solo indici e cs219 (7,3 GB per
    UniRef30_2020_06, circa 3 minuti). Finiscono nel disco di Docker; i file grandi restano sul disco esterno.
  - Con la copia gli indici si caricano in pochi secondi.
  - Se il database sul disco cambia, BioLoom torna a leggerlo dal disco finché non aggiorni la copia.
  - Su Linux il disco è montato direttamente e la copia non serve.
- **Memoria:** HHblits deve tenere in memoria gli indici del database e l'intero cs219. Con UniRef30_2020_06
  servono circa 12 GB assegnati a Docker.
  - **Con meno memoria** HHblits rilegge il cs219 di continuo e il calcolo si ferma a metà per il timeout. Con 6 GB,
    dopo l'avvio ha letto oltre 100 GB senza avanzare.
  - **Prima di partire** BioLoom confronta la memoria di Docker con quella richiesta dal database scelto: 12 GB per
    UniRef30_2020_06, per le altre versioni una stima prudente dalla dimensione del database (Impostazioni → Database
    mostra entrambe). Se non basta, il calcolo non parte e il job fallisce subito dicendo quanta memoria serve, oppure
    usa il fallback su SWISS-MODEL, se attivo.
  - **Con memoria al limite** (sopra il requisito ma sotto quella consigliata dal servizio) HHblits usa un solo
    processore: legge molto meno dal disco.
  - **Un calcolo alla volta:** gli altri QMEAN locali aspettano in coda ("2° in coda") senza fermare gli altri job.
    Un calcolo che ripiega su SWISS-MODEL libera subito il servizio locale per il successivo.
  - **Windows:** nel file `%UserProfile%\.wslconfig` metti `[wsl2]` e `memory=...GB`, poi `wsl --shutdown`.
    Il valore predefinito è metà della RAM.
  - **macOS:** Docker Desktop → Settings → Resources.
- **Fallback facoltativo su SWISS-MODEL:** se QMEAN locale non è disponibile (disco scollegato) o non riesce,
  il modello può essere inviato a SWISS-MODEL. Si attiva in Impostazioni oppure per il singolo calcolo, e
  richiede il consenso all'invio e la tua email. Il risultato indica sempre dove è stato calcolato e perché.

## Caricare dati da file

Ogni campo che riceve dati accetta anche un file:

| Dati | Formati accettati | Dove |
|---|---|---|
| Sequenze | FASTA, GenBank (con le traduzioni delle CDS), EMBL, UniProt, PIR, Clustal, Stockholm, MSF, PDB/mmCIF (una sequenza per catena), testo semplice; anche compressi `.gz`, in UTF-8 o UTF-16 | Sequenze, Ricerca, Allineamenti, Modellazione, Pipeline |
| Allineamenti | PIR, oppure FASTA allineato, Clustal, Stockholm (convertiti in PIR) | Editor PIR e Modellazione |
| Strutture | `.pdb`, `.cif` | qualità, allineamento strutturale, raffinamento dei loop, template |
| Vincoli di modellazione | testo, una riga per vincolo: `helix 10:A 20:A`, `strand 30:A 35:A`, `distance CA:10:A CA:50:A 8.0 0.5`; esportabili nello stesso formato | Modellazione |
| Chiave MODELLER | file con la chiave, email di registrazione o `config.py` | Licenze |
| Licenza ProSA | file `LicenseDB` | Licenze |

## Risultati riusati (cache dei calcoli)

Ricerche di template e allineamenti multipli e strutturali già calcolati con gli stessi dati e parametri non
vengono rifatti. Il risultato riporta la nota **dalla cache (data)**. Rilanciare una pipeline con le stesse
sequenze e altre opzioni di modellazione salta così tutte le ricerche.

- **Validità:** le ricerche su RCSB, NCBI e AlphaFold DB valgono 7 giorni, perché il PDB si aggiorna ogni settimana. Le altre
  valgono finché non cambiano il database locale (pdbaa, MMseqs2), il programma usato o il contenuto delle
  strutture.
- **Ricalcolo:** in Ricerca, Allineamenti e Pipeline l'opzione **Ricalcola ignorando la cache** rifà il calcolo e
  aggiorna il risultato salvato.
- **Disattivazione e spazio:** da Impostazioni → Spazio e file temporanei, che mostra anche lo spazio occupato. La
  cache sta in `data/cache/compute` e si pulisce da sola dopo 30 giorni.

## Coda dei calcoli

- **Priorità:** ricerche, allineamenti, analisi di qualità, analisi strutturali e QMEAN partono prima di modellazioni
  e raffinamenti dei loop, e questi prima di pipeline e aggiornamenti dei database; a parità, in ordine di creazione.
  I job in coda mostrano la posizione ("2° in coda") nella pagina Job e nel riquadro del job.
- **CPU condivisa:** i programmi di calcolo di tutti i job insieme (MODELLER, PROCHECK, ProSA, BLAST+, MMseqs2,
  allineatori…) non superano i **Job di calcolo in parallelo** delle Impostazioni (predefinito: core − 1). Una
  pipeline con molte sequenze in parallelo e un'analisi lanciata nel frattempo si dividono i processori invece di
  saturarli. Chi aspetta mostra "In attesa di CPU libera"; l'attesa non conta nel timeout e l'annullamento la
  interrompe. La Panoramica mostra gli slot di calcolo in uso.
- **Impostazione:** il numero di job in parallelo vale subito, senza riavviare BioLoom.

## Qualità per residuo e consenso

- **Viewer 3D colorato per valore:** nella pagina Qualità (schede 3D e Profili) il menu **Colora per** colora la
  struttura per struttura secondaria, Ramachandran, clash, DOPE per residuo, RSA, B-factor, **pLDDT** dei modelli
  AlphaFold (scala ufficiale di AlphaFold DB), QMEAN locale calcolato nella pagina e consenso per residuo, con la
  legenda sul viewer. Un clic su grafici o tabelle evidenzia e centra il residuo nel 3D; un clic nel 3D lo seleziona
  nella tabella e nei grafici.
- **Consenso tra i metodi:** voto BioLoom, z-DOPE, GA341, QMEAN, ProSA e PROCHECK, quando disponibili, sono portati
  su una scala 0–100 dichiarata e combinati in un punteggio con fascia buona / intermedia / scarsa. Il riquadro
  spiega i metodi che discordano ed elenca le **regioni problematiche** (tratti segnalati da almeno due metodi),
  cliccabili. Il voto A–D non cambia.
- **Confidenza dell'allineamento:** negli allineamenti (Allineamenti, Modellazione, Editor PIR) la modalità
  **Confidenza** colora le colonne per identità locale rispetto al bersaglio e segnala la zona crepuscolare
  (sotto il 25%) e i residui non coperti dal template.
- **Disordine predetto:** il consenso indica le regioni disordinate predette dalla sequenza come "attese poco
  affidabili" e spiega le regioni problematiche che vi cadono dentro, senza abbassare il punteggio.

## Disordine e domini

Dove il modello sarà poco affidabile, prima di modellare:

- **Sequenze:** la mappa delle caratteristiche mostra le regioni **disordinate** predette (FoldIndex, dalla sola
  sequenza: almeno 30 residui), quelle a **bassa complessità** (SEG), i **domini** InterPro (solo per le sequenze con
  accessione UniProt) e la struttura secondaria predetta.
- **Modellazione e Ricerca:** prima di lanciare, un avviso se una regione disordinata lunga resta fuori dalla copertura
  del template o se un dominio non è coperto, con un suggerimento (per esempio modellare per domini). Nella Ricerca ogni
  template ha il suo avviso.
- **Qualità e report:** le regioni disordinate predette compaiono nel consenso per residuo e, nel report HTML, come
  profilo accanto agli altri.
- **Limiti:** FoldIndex è un metodo semplice e documentato (carica e idropatia medie), molto meno accurato dei predittori
  moderni come IUPred2A o quelli basati sul deep learning: va letto come un'indicazione. Il calcolo avviene sul computer;
  per i domini a UniProt e InterPro va solo l'accessione.

## Confronto strutturale

La pagina **Confronto** (e la modalità Strutturale di Allineamenti) sovrappone la struttura B alla struttura A, il
riferimento (per esempio il template o una struttura sperimentale), con US-align/TM-align o con il motore integrato.

- **Metriche:** TM-score normalizzato su A e su B, RMSD, RMSD delle coppie entro 5 Å, **GDT-TS** e **GDT-HA**
  (percentuale dei Cα di A entro 1–8 Å e 0,5–4 Å) e **lDDT** sui Cα, che non usa sovrapposizioni.
- **Per residuo:** nel 3D la struttura B è colorata per distanza dal residuo corrispondente (da 0 a 5 Å e oltre) o per
  lDDT, A resta in grigio chiaro; il grafico mostra distanza e lDDT lungo la sequenza e la tabella elenca ogni coppia.
  Un clic su grafico, tabella o tratto che devia evidenzia la coppia nel 3D, un clic nel 3D la seleziona.
- **Da dove si apre:** Modellazione (menu **Confronta…** di ogni modello: con il template usato o con un altro modello
  dello stesso job), Qualità (**Confronta con …**) e Allineamenti (**Apri in Confronto**). Le due strutture arrivano
  già scelte e il link resta stabile.
- I risultati strutturali calcolati dalle versioni precedenti restano nella cache ma non si riusano: il primo confronto
  li ricalcola con i dati per residuo.

## Analisi strutturale: interazioni, interfacce, conservazione

Nella pagina Qualità la scheda **Interazioni** analizza la struttura mostrata (tutte le catene; acque e ligandi
esclusi) in pochi secondi:

- **Tabelle** di legami idrogeno, ponti salini, ponti disolfuro e interazioni aromatiche (π-stacking parallelo o a T,
  catione-π), filtrabili per catena (anche "solo fra catene diverse") e per residuo (`27`, `K27`, `LYS27`, `A:27`).
  Un clic su una riga evidenzia i due residui nel 3D; un clic nel 3D filtra le tabelle per quel residuo. I criteri
  geometrici e le fonti sono nella scheda stessa.
- **Interfacce** (strutture con più catene): residui d'interfaccia (superficie sepolta > 1 Å²), area d'interfaccia,
  composizione, interazioni fra le catene e un indicatore di plausibilità biologica basato sull'area, con i suoi
  limiti dichiarati (non sostituisce PISA o EPPIC). Il 3D si colora per superficie sepolta.
- **Conservazione:** con un allineamento multiplo (un job di Allineamenti del progetto, oppure sequenze o un file
  FASTA/Clustal/Stockholm incollati o caricati; le sequenze non allineate si allineano prima) la conservazione di ogni
  colonna (0–1, entropia di Shannon con la penalità per i gap) viene riportata sui residui della struttura e colora il
  3D con la tavolozza di ConSurf. La riga della struttura si può indicare con il nome del FASTA (`sp|P0CG47|UBB_HUMAN`)
  o con quello mostrato da BioLoom (`sp_P0CG47_UBB_HUMAN`); selenocisteina (U) e pirrolisina (O) sono ammesse.
- L'analisi è un job ("Analisi strutturale" nella pagina Job, link stabile `/quality?ajob=<id>`); la scheda mostra la
  più recente per la stessa struttura, oppure quella in corso (anche dopo aver cambiato scheda o ricaricato la pagina). Il **report HTML** del modello o dell'analisi di qualità riassume interfacce e
  ponti disolfuro, se l'analisi della stessa struttura li ha trovati.

## Report del modello

Il pulsante **Report** (tabella dei modelli in Modellazione, pagina Qualità, righe della pipeline e la sua finestra
«Visualizza») scarica un file HTML autonomo, da aprire con qualunque browser anche senza BioLoom e da stampare in PDF
(Stampa → Salva come PDF):

- intestazione con job, progetto, date e versioni (BioLoom, MODELLER, strumenti di validazione usati);
- bersaglio, template (sorgente, identità e copertura; avviso e ritaglio pLDDT per i modelli AlphaFold), allineamento
  PIR a blocchi, parametri di MODELLER e tabella dei modelli con il migliore evidenziato;
- per il modello: voto con le penalità, consenso tra i metodi (discordanze e regioni problematiche), grafici di
  Ramachandran e profili per residuo (DOPE; QMEAN locale se calcolato nella pagina Qualità; disordine predetto),
  struttura secondaria, contatti sterici principali, ligandi e metalli (se la struttura ne ha), avvisi, legenda delle
  metriche e riferimenti bibliografici;
- la **vista 3D corrente**, se il viewer è aperto su quel modello (in Qualità: scheda 3D; in Pipeline: «Visualizza»).

Per un'analisi di qualità di una struttura qualsiasi il report è ridotto (niente template, allineamento e parametri).
L'archivio ZIP della pipeline contiene un report per ogni sequenza completata (cartella `report/`, senza vista 3D).

## Scelta ragionata dei template

- **Punteggio 0–100** per ogni template trovato (colonna **Punteggio** della Ricerca, ordinata per punteggio): media
  pesata di identità (confrontata con la curva della zona crepuscolare di Rost, che dipende dalla lunghezza
  allineata), copertura del bersaglio, E-value, risoluzione (o pLDDT per i modelli AlphaFold; regola fissa per l'NMR),
  R-free, metodo, lacune dell'allineamento e data. Ligandi/cofattori e stato oligomerico sono solo informativi. Fasce
  buona ≥ 70, intermedia 40–70, scarsa < 40; un template in zona crepuscolare non arriva mai in fascia buona. Un clic
  sulla riga mostra la motivazione di ogni componente, il passaggio del mouse sul punteggio la riassume.
- **Template consigliati:** il migliore più, se servono, fino a due template **complementari** che coprono almeno 10
  residui del bersaglio non coperti o migliorano l'identità locale, senza template quasi identici a uno già scelto;
  ogni scelta ed esclusione è motivata. **Usa i consigliati per la modellazione** apre la Modellazione con la sequenza e
  quei template.
- **Pipeline → Più template complementari:** per ogni sequenza fino a N template (2–5, predefinito 3) scelti con la
  stessa regola e usati insieme da MODELLER. La colonna Template li elenca (motivi nel suggerimento e nel log), la
  copertura è quella complessiva, l'identità quella del template principale.
- Con più template MODELLER allinea i template fra loro (`salign`); se questo sposta un template rispetto al bersaglio
  (succede con template che coprono regioni diverse), BioLoom usa l'allineamento di ogni template sul bersaglio
  (`align2d`) e lo scrive nel log. Se l'unione non è possibile (template a più catene) l'allineamento spostato resta,
  con un avviso nel risultato e nel report.
- Template di strutture diverse con meno di 10 residui del bersaglio in comune non vincolano la posizione reciproca
  delle loro regioni: la motivazione dei consigliati e gli avvisi della modellazione lo dicono («Orientamento relativo
  non vincolato»).
- Le ricerche salvate nella cache dalle versioni precedenti (senza R-free e stato oligomerico) si rifanno la prima
  volta; senza rete i metadati RCSB già in cache restano utilizzabili.

## Modelli AlphaFold DB come template

Per le proteine senza una struttura sperimentale omologa si può usare un modello predetto di
[AlphaFold DB](https://alphafold.ebi.ac.uk) (EBI). È sempre segnato come **modello predetto, non struttura
sperimentale**, con il pLDDT medio (confidenza della predizione, 0–100).

- **Libreria template → Da AlphaFold DB (UniProt):** scarica il modello di un'accessione (es. `P69905`); resta in
  libreria come `AF-P69905-F1`, con il file originale.
- **Ricerca template → AlphaFold DB:** con l'accessione UniProt (precompilata per le sequenze importate da UniProt)
  prende i suoi modelli; senza, cerca le proteine più simili con NCBI BLAST su Swiss-Prot (1–5 min). Se l'accessione
  non ha modelli o EBI non risponde, la ricerca lo dice (invece di "0 template"). Nella pipeline l'accessione si legge
  dall'intestazione FASTA di UniProt (`>sp|P69905|HBA_HUMAN`), che «Usa le sequenze del progetto» aggiunge da sola
  per le sequenze importate da UniProt.
- **Modellazione e Pipeline:** con un template AlphaFold, i residui con pLDDT sotto la soglia (predefinita 70) vengono
  tolti dal template, perché le regioni a bassa confidenza (spesso disordinate) peggiorano il modello; MODELLER li
  costruisce senza template. Con loro si tolgono i frammenti rimasti isolati (meno di 5 residui tra residui tolti).
  Il numero di residui tolti è nel log e nei risultati.
- **Riconoscimento:** un file è un modello AlphaFold (pLDDT nella colonna B-factor) con l'intestazione di AlphaFold
  DB o il nome `AF-<accessione>-F<n>`, mai per i soli valori: una struttura sperimentale risolta a partire da un
  modello AlphaFold resta sperimentale. Libreria, modellazione e pagina Qualità usano la stessa regola.
- **Privacy:** a EBI va solo l'accessione; senza accessione la sequenza va a NCBI, come per il motore NCBI BLAST.

## Ligandi, cofattori e metalli

- **Scelta:** in Modellazione, **Trasferisci ligandi, cofattori e metalli dai template** elenca i gruppi eteroatomici
  delle catene scelte con la classe (metallo, cofattore noto come eme, FAD, NAD, ATP, SAM, PLP, CoA…, additivo, altro
  ligando). Gli additivi di cristallizzazione (SO₄, glicerolo, PEG, tamponi…) e gli ioni Na⁺/K⁺ del tampone sono
  deselezionati per default, con il motivo; le acque non si trasferiscono. MODELLER copia nel modello solo i gruppi
  scelti, anche partendo da un documento PIR (le colonne «.» dell'allineamento si aggiornano da sole). Con più
  template, lo stesso gruppo nello stesso sito (per esempio l'eme di due globine) diventa un solo gruppo del modello,
  vincolato da tutti i template. SAM, SAH e gli amminoacidi liberi sono ligandi; i residui modificati legati alla
  catena (MSE, SEP…) no. La Libreria template mostra la classe di ogni gruppo.
- **Verifica:** dopo la modellazione, e in ogni analisi di qualità di una struttura con ligandi, BioLoom controlla per
  ogni gruppo i residui vicini (entro 4 Å), gli scontri (sotto 2,2 Å) e, per i metalli, gli atomi che li coordinano:
  distanze rispetto ai valori attesi (Harding 2006; Ni da CheckMyMetal), numero di coordinazione e geometria più probabile
  (tetraedrica, ottaedrica…, come CheckMyMetal). Nei modelli confronta il sito con quello del template: i residui che
  coordinavano il metallo lo coordinano ancora? L'esito è **buono**, **da controllare** o **problematico**, con la
  motivazione. Il voto A–D non cambia.
- **Dove si vede:** nella pagina Qualità la sezione **Ligandi e metalli**; un clic su un gruppo lo mostra nel 3D con
  i residui coordinanti e le linee di coordinazione. Il risultato della Modellazione e il report HTML hanno la stessa
  tabella.

## Ricerca, etichette e preferiti

- **Ricerca globale:** il campo **Cerca…** in alto (oppure **Ctrl K**, **⌘K** sul Mac, o `/`) trova progetti, sequenze
  (anche per accessione UniProt), documenti, job (anche per tipo: «modellazione»), template (id, titolo, organismo) ed
  etichette, raggruppati e ordinati per pertinenza. Ogni parola vale come inizio di una parola: «p6990» trova
  «sp|P69905|HBA_HUMAN», maiuscole e accenti non contano. Frecce per scegliere, Invio per aprire (anche in un altro
  progetto), Esc per chiudere. `#etichetta` cerca solo fra gli elementi con quell'etichetta, `tipo:job` (o `tipo:template`,
  `tipo:sequenze`…) solo un tipo. A campo vuoto mostra i preferiti.
- **Etichette e stella:** progetti, job, sequenze e template hanno la stella dei preferiti e fino a 20 etichette
  (minuscole, al più 32 caratteri; «Da Rivedere» diventa «da-rivedere»). Si aggiungono nel dettaglio dell'elemento (per
  il progetto attivo nella Panoramica); negli elenchi di job e template, e nel dialogo **Progetti** della barra in alto, si
  filtra per etichetta o per preferiti. Eliminando un elemento se ne vanno anche le sue etichette.
- **Tema:** Scuro, Chiaro o **Sistema** (segue il tema del sistema operativo) in fondo alla barra laterale.
- **Tasti:** `?` (o **Tasti** nella barra laterale) elenca le scorciatoie: `g` e poi una lettera per le pagine principali
  (`g s` Sequenze, `g j` Job, `g q` Qualità…). Mentre scrivi in un campo valgono solo Ctrl K ed Esc.
- **Liste lunghe:** la tabella dei residui in Qualità, le coppie in Confronto e gli allineamenti con molte sequenze
  disegnano solo le righe visibili, e restano fluidi anche con migliaia di righe.

## Cosa contiene

| Area | Strumenti |
|---|---|
| Modellazione | MODELLER 10.8 (AutoModel, loop, allineamenti `align2d`/`salign`), con il trasferimento dei ligandi, cofattori e metalli scelti |
| Ricerca template | RCSB, NCBI BLAST e modelli predetti di AlphaFold DB (online); BLAST+ e MMseqs2 sul database locale pdbaa; punteggio motivato di ogni template e template complementari consigliati |
| Allineamenti | Clustal Omega, MAFFT, MUSCLE, TM-align, US-align, più motori integrati; confronto strutturale con GDT-TS/GDT-HA, lDDT e deviazione per residuo |
| Qualità | Ramachandran, clashscore, DSSP (mkdssp), SASA, profilo DOPE, **PROCHECK**, **ProSA II**, **QMEAN locale** (UniClust30) o SWISS-MODEL, voto A–D, consenso tra i metodi, qualità per residuo nel 3D (anche pLDDT di AlphaFold), verifica di ligandi e metalli (contatti, scontri, coordinazione); i passi indipendenti girano in parallelo e con l'analisi "Su tutti i modelli" si analizzano più modelli insieme, report HTML stampabile in PDF |
| Analisi strutturale | legami idrogeno, ponti salini, ponti disolfuro, interazioni aromatiche, interfacce fra catene (superficie sepolta, plausibilità), conservazione da un allineamento multiplo mappata sulla struttura |
| Automazione | pipeline batch da multi-FASTA, riprendibili dopo un annullamento, un errore o un riavvio ("Riprendi" o "Riprova solo i falliti": le sequenze già modellate non si rifanno) e, se completate con sequenze fallite, "Riprova i falliti"; avanzamento, log e tabella dei risultati aggiornati in tempo reale, con la stima del tempo residuo ("circa 3 min rimanenti"); un template usato da più sequenze si prepara una volta sola per pipeline; verifica dell'installazione (Impostazioni → Strumenti) |

## Dove stanno i dati

| Cosa | Dove | Viaggia con la chiavetta? |
|---|---|---|
| Progetti, risultati, template, licenze, pdbaa, database MMseqs2 | `data/` accanto all'avviatore | sì |
| Calcoli in corso e file temporanei | volume Docker `scratch`, veloce e svuotato a ogni avvio | no |
| Indice veloce MMseqs2 (≈ 1,5 GB) | volume Docker `cache` di ogni computer | no, si ricrea da Impostazioni → Database |
| UniClust30/UniRef30 e QMTL | disco esterno, montato in sola lettura | resta sul disco |
| Copia locale di indici e cs219 di UniClust30; file cs219 ricreati per i database HH-suite 2 | volume Docker `qmean_cache` di ogni computer | no |

A fine calcolo in `data/` vengono salvati solo i risultati: i file intermedi di MODELLER restano fuori, salvo
diversa impostazione. Upload e cache scaduti si puliscono da soli (Impostazioni → Spazio e file temporanei).

Quando una nuova versione di BioLoom aggiorna la struttura del database (`data/bioloom.db`), lo fa da sola all'avvio
dopo averne salvato una copia in `data/backups/` (una per aggiornamento; si può cancellare quando tutto funziona). Se
l'aggiornamento non riesce, il database resta com'era e BioLoom lo dice all'avvio.

## Chiavetta USB

Da un computer dove BioLoom funziona, prepara la copia portatile (codice, dati e immagini Docker, circa 1,5 GB
più i tuoi dati):

```bat
powershell -ExecutionPolicy Bypass -File launcher\bioloom.ps1 -Azione prepara -Destinazione E:\BioLoom
```

```bash
bash launcher/bioloom.sh prepara /Volumes/CHIAVETTA/BioLoom
```

Formatta la chiavetta in **exFAT**: si legge da Windows, macOS e Linux e non ha il limite di 4 GB del FAT32.
Sul computer di destinazione basta Docker e un doppio clic sull'avviatore.

- **Mac Apple Silicon:** i container girano in emulazione x86 (MODELLER, ProSA e QMEAN esistono solo per
  x86), quindi più lenti.
- **Linux:** l'utente deve poter usare Docker (gruppo `docker`).

## Server

```bash
bash launcher/bioloom.sh server   # la prima volta crea server.env con una chiave d'accesso casuale
nano server.env                   # cartella dati, UniClust30, porta
bash launcher/bioloom.sh server   # avvio (riavvio automatico con il server)
```

In modalità server l'interfaccia chiede la chiave d'accesso. I dettagli, compreso HTTPS con reverse proxy,
sono in [docs/SERVER.md](docs/SERVER.md).

## Licenze

- **MODELLER:** si inserisce da Impostazioni → Licenze e viene verificata prima di essere salvata. Su un
  server si può indicare anche `MODELLER_KEY` in `server.env`.
- **ProSA II:** si carica il file `LicenseDB` da Impostazioni → Licenze. L'avviatore assegna al container
  il MAC indicato nella licenza.
- **PROCHECK:** non richiede chiavi.

Le chiavi restano in `data/licenses/` e non entrano mai nelle immagini Docker. I pacchetti PROCHECK e ProSA
(`docker/vendor/`) e le chiavi sono tuoi: non distribuirli ad altri.

## Documentazione

- [Architettura](docs/ARCHITETTURA.md): container, flusso dei dati, motori, API.
- [Server](docs/SERVER.md): installazione su server, accesso, HTTPS, backup.
- [Reverse engineering della v3](docs/REVERSE_ENGINEERING.md).

## Sviluppo

```bash
# tutto: ruff, pytest nell'immagine di BioLoom (MODELLER reale), typecheck e vitest del frontend
scripts/test.sh
# solo il backend, con argomenti per pytest
scripts/test.sh backend -k pipeline -x
# backend senza Docker (dalla cartella backend/, con le dipendenze di requirements.lock): i test di
# MODELLER e degli strumenti esterni vengono saltati
python -m pytest
# immagini
docker compose -f launcher/compose.yml build
```

`scripts/test.sh` costruisce `bioloom-test:<versione>` sopra l'immagine locale (con pytest e ruff) e monta il
repository. La chiave di MODELLER viene da `MODELLER_KEY` oppure da `data/licenses/modeller.key`; da un worktree
indica la cartella dati con `BIOLOOM_DATA_PATH`. La stessa verifica gira in CI (`.github/workflows/ci.yml`).

Profilazione dei tempi con MODELLER reale (job model, elemento di pipeline, analisi di qualità, avvio di MODELLER;
non fa parte della suite): `scripts/test.sh backend tests/perf_timing.py -s`. Le misure e le condizioni sono in
[Architettura → Prestazioni](docs/ARCHITETTURA.md#prestazioni). Ogni analisi di qualità scrive nel log del job la
durata dei suoi passi ("Analisi di qualità in 1.14 s (passi in parallelo: …)").

`bioloom.py` avvia il server senza Docker, utile solo per lo sviluppo.
