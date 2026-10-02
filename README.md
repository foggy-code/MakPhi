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

- **Priorità:** ricerche, allineamenti, analisi di qualità, analisi strutturali (anche tasche ed elettrostatica) e
  QMEAN partono prima di modellazioni, raffinamenti dei loop e rifiniture energetiche, e questi prima di pipeline e
  aggiornamenti dei database; a parità, in ordine di creazione.
  I job in coda mostrano la posizione ("2° in coda") nella pagina Job e nel riquadro del job.
- **CPU condivisa:** i programmi di calcolo di tutti i job insieme (MODELLER, PROCHECK, ProSA, BLAST+, MMseqs2,
  allineatori…) non superano i **Job di calcolo in parallelo** delle Impostazioni (predefinito: core − 1). Una
  pipeline con molte sequenze in parallelo e un'analisi lanciata nel frattempo si dividono i processori invece di
  saturarli. Chi aspetta mostra "In attesa di CPU libera"; l'attesa non conta nel timeout e l'annullamento la
  interrompe. La Panoramica mostra gli slot di calcolo in uso.
- **Impostazione:** il numero di job in parallelo vale subito, senza riavviare BioLoom.

## Procedura guidata e controlli prima dell'avvio

- **Nuovo modello guidato** (barra laterale e Panoramica) porta dalla sequenza al modello in sei passi:
  1. **sequenza** incollata, dal progetto, da file o da UniProt (accessione), con l'anteprima di disordine, bassa
     complessità e domini;
  2. **template**: motore consigliato (con le alternative spiegate e la loro disponibilità su questo computer), risultati
     in ordine di punteggio e i consigliati già scelti;
  3. **allineamento** con MODELLER, mostrato in modalità Confidenza; si apre nell'Editor PIR per correggerlo, e un
     documento PIR salvato nel progetto si può usare al suo posto;
  4. **opzioni** con valori predefiniti spiegati (5 modelli, raffinamento Rapido, analisi estesa se PROCHECK o ProSA ci
     sono, ligandi se i template ne hanno, ritaglio pLDDT per i template AlphaFold);
  5. **avvio** con l'avanzamento in tempo reale;
  6. **risultati**: modello migliore, voto e consenso, con **Qualità**, **Confronta con il template** e **Report**.

  Lo stato sta nell'indirizzo della pagina (passo e job) e nel browser: si ricarica, si torna indietro con il browser o
  si riprende più tardi; un link aperto su un altro computer riprende dai job.
- **Controlli prima dell'avvio** (Modellazione, Pipeline e procedura guidata): mentre scegli, BioLoom verifica senza
  avviare nulla licenza MODELLER, template (presenti, scaricabili, catene), identità sotto la zona crepuscolare e
  copertura, regioni disordinate e domini scoperti, ritaglio pLDDT che lascia pochi residui, PROCHECK e ProSA per
  l'analisi estesa, tempo stimato e rischio di timeout, spazio libero nella cartella dati e nello scratch, molte
  ricerche su NCBI (le sue linee guida chiedono di lanciarne più di 50 di sera o nel fine settimana).
  - **Errori** (rosso): il pulsante di avvio resta disattivato, con il motivo;
  - **avvisi** (giallo): l'avvio chiede conferma («Avvia comunque»);
  - **informazioni**: in piccolo, senza conseguenze (tempo stimato, dati inviati ai servizi remoti).

  I tempi sono stimati dalle misure su un portatile a 4 CPU logiche e crescono con i residui e i modelli: su un altro
  computer possono variare di un fattore 2.

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
  template ha il suo avviso; in Modellazione gli avvisi fanno parte dei controlli prima dell'avvio e chiedono conferma.
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

## Tasche, elettrostatica e rifinitura energetica

Tre calcoli con programmi inclusi nell'immagine Docker (se mancano, BioLoom lo dice e il resto funziona):

- **Tasche** (pagina Qualità, scheda **Tasche**): fpocket trova le cavità della superficie e le ordina per
  *druggability* (0–1; da 0,5 la tasca è probabilmente adatta a legare una piccola molecola simile a un farmaco). La
  tabella riporta punteggio, volume, sfere alfa e residui che rivestono ogni tasca; nel 3D le tasche sono colorate,
  con i centri delle sfere alfa e il centro della tasca. Un clic su una riga mostra solo quella tasca. Ligandi e acque
  si escludono dal calcolo, quindi i loro siti compaiono come tasche vuote.
- **Elettrostatica** (scheda **Elettrostatica**): PDB2PQR assegna cariche e raggi (PARSE o AMBER) con la protonazione a
  pH 7 di PROPKA, APBS risolve l'equazione di Poisson-Boltzmann (150 mM di sale, dielettrici 2 e 78,54, 25 °C). Il
  potenziale sulla superficie colora i residui dal rosso (negativo) al blu (positivo), fino a ±5 kT/e; la scheda
  riporta la carica netta e i residui più carichi, e la mappa `.dx` si scarica per PyMOL, Chimera o VMD.
- **Rifinitura energetica** (Modellazione: **Rifinisci** nella tabella dei modelli, oppure per qualunque struttura nel
  riquadro dei loop): PDBFixer completa gli atomi e aggiunge gli idrogeni, OpenMM minimizza l'energia con AMBER ff14SB
  e solvente implicito, tenendo ferma la catena principale con restrizioni; facoltativa una breve dinamica a 300 K. Il
  risultato confronta prima e dopo (voto, Ramachandran, clashscore, DOPE, energia, spostamento della catena
  principale) e porta alla pagina Qualità del modello rifinito e al Confronto con l'originale. Ligandi e ioni non sono
  rifiniti (il modello rifinito contiene solo la proteina). Su 1UBQ la minimizzazione richiede da 15 s a un minuto.

Ogni calcolo è un job (pagina Job; link `/quality?pjob=`, `/quality?ejob=`, `/modeling?job=`). Parametri, scelte e
fonti sono nelle schede e in [Architettura](docs/ARCHITETTURA.md#tasche-elettrostatica-e-rifinitura-energetica-f7b-f91).

## Mutagenesi in silico e scansione di alanina

Nella pagina Qualità (scheda **Mutagenesi**) e in Modellazione (**Mutagenesi** nella tabella dei modelli, oppure per
qualunque struttura nel riquadro dei loop):

- **Mutazioni** nella numerazione della struttura: `A:K48R`, anche `A:LYS48ARG`; più mutazioni separate da virgole
  formano una variante. Il residuo originale deve corrispondere a quello della struttura (altrimenti BioLoom lo dice prima
  di partire). MODELLER sostituisce il residuo e riottimizza la sua catena laterale e quelle vicine (entro 5 Å); il
  selvatico passa dallo stesso protocollo, così il confronto misura solo la sostituzione.
- **Varianti di UniProt:** con l'accessione della proteina, le varianti naturali, associate a malattie e di mutagenesi
  sperimentale, portate sulla numerazione della struttura con un allineamento (anche quando le due numerazioni non
  coincidono, o la proteina ha copie ripetute come la poliubiquitina); «Aggiungi» le porta nel campo.
- **Scansione di alanina:** i residui indicati (`A:30, A:43, A:45-48`), quelli dell'interfaccia fra catene o quelli di
  una tasca trovata da fpocket diventano uno alla volta alanina (al più 50; glicina e prolina escluse). Tabella dalla
  sostituzione più destabilizzante, 3D colorato per effetto, hot spot d'interfaccia, CSV.
- **Effetto stimato:** *probabilmente destabilizzante*, *incerta* o *probabilmente neutra*, con le motivazioni: differenza
  di DOPE fra mutante e selvatico, prolina o glicina in un'elica o in un filamento, legami idrogeno, ponti salini e
  disolfuri persi, superficie idrofobica sepolta persa, residuo nel nucleo, contatti sterici, residui d'interfaccia.
  Facoltativa la differenza di energia con OpenMM (circa un minuto in più). **Non è un ΔΔG** né una previsione
  calibrata come FoldX o Rosetta (che BioLoom non usa: licenze non ridistribuibili): è un'indicazione della direzione
  probabile dell'effetto, da verificare.
- Il risultato confronta selvatico e mutante (Qualità di entrambi, pagina Confronto) e ha il suo report HTML. Ogni
  calcolo è un job (`/modeling?job=`). Protocollo, soglie e fonti in
  [Architettura](docs/ARCHITETTURA.md#mutagenesi-in-silico-e-scansione-di-alanina-f92).

## Confronto con una predizione (ESMFold)

Un controllo di plausibilità del modello per omologia con una predizione indipendente della stessa sequenza
(**Confronta con una predizione (ESMFold)** nella pagina Qualità e sotto il modello scelto in Modellazione):

- **ESMFold** (servizio pubblico di ESM Atlas, fino a 400 residui) oppure, con l'accessione UniProt, il modello di
  **AlphaFold DB**. La predizione si salva tra i file del job e la pagina Qualità la apre con il pLDDT (confidenza della
  predizione; il servizio ESMFold lo scrive su 0–1 e BioLoom lo riporta su 0–100).
- **Sequenza:** quella della catena del modello (selenometionine e altri residui modificati compresi) oppure quella
  incollata nella finestra. Se alla catena mancano dei residui, BioLoom invia la sequenza completa del file (SEQRES)
  quando c'è; altrimenti avvisa (unire i tratti darebbe una proteina che non esiste) e non conta come divergenze i
  residui vicini alle giunzioni: incolla la sequenza completa per un confronto pulito.
- **Esito motivato:** TM-score (da 0,5 stesso ripiegamento), lDDT per residuo e regioni in cui il modello diverge dalla
  predizione, incrociate con la confidenza della predizione e con le regioni problematiche del consenso: «le due
  strutture concordano», «divergono in 40-52, dove la predizione è sicura: controlla l'allineamento», oppure «divergono
  in 70-76, dove anche la predizione è incerta». Sotto, il confronto per residuo (3D sovrapposto, distanze, lDDT).
- **Consenso e report:** a confronto eseguito, l'accordo con la predizione entra nel consenso di qualità con un peso
  basso (0,25) e il report HTML ha la sezione del confronto; senza confronto nulla cambia.
- **Privacy:** con ESMFold la **sequenza** (mai le coordinate) lascia il computer, solo con il tuo consenso: lo chiede il
  pulsante la prima volta, e si revoca in Impostazioni → Generale (dove si può indicare anche un altro servizio ESMFold).
  Con AlphaFold DB va a EBI solo l'accessione. Una predizione già ottenuta resta in cache su questo computer per 30
  giorni.

Una predizione non è una struttura sperimentale: con un template sperimentale vicino il modello per omologia è di solito
più affidabile. Dettagli in [Architettura](docs/ARCHITETTURA.md#confronto-con-un-predittore-esterno-f93).

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
  modello AlphaFold resta sperimentale. Libreria, modellazione e pagina Qualità usano la stessa regola, che riconosce
  anche le predizioni di ESMFold (intestazione «ESMFOLD … PREDICTION», come le salva il confronto con una predizione).
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

## Complessi e omo-oligomeri

- **Assemblaggio biologico:** il file del PDB contiene l'unità asimmetrica del cristallo, che non sempre è il complesso
  biologico. Nella **Libreria template** la scheda di un template elenca gli assemblaggi dichiarati nel file (REMARK 350
  del PDB, `pdbx_struct_assembly` del mmCIF) con lo stato oligomerico e le catene; **Importa come template** crea il
  template `<ID>_ASM<n>` con le coordinate dell'assemblaggio (operatori di simmetria applicati). Le catene copiate
  ricevono nuovi nomi, con l'origine indicata (es. «B = A con l'operatore 2»).
- **Modellazione → Complesso:** si sceglie il template (o l'assemblaggio) e, per ogni sua catena, la sequenza del
  bersaglio: dal progetto, incollata oppure **stessa della catena A** per gli omo-oligomeri. Le catene del modello hanno i
  nomi del template, con i residui numerati da 1. Con catene uguali si possono aggiungere **restrizioni di simmetria**
  (MODELLER, C-alfa, peso 1): le catene restano identiche fra loro, la loro disposizione viene dal template.
  **Anteprima dell'allineamento per catena** mostra l'allineamento di ogni catena in modalità Confidenza. Limiti: da 2 a
  12 catene e al più 3000 residui in tutto (il tempo di MODELLER cresce con i residui del modello).
- **Interfacce:** per ogni modello a più catene BioLoom confronta le interfacce con quelle del template: residui
  d'interfaccia conservati, area sepolta del modello e del template e **DockQ** (Basu & Wallner 2016) con il template
  come riferimento, con l'esito alta, media, accettabile o non riprodotta. Nel risultato il 3D colora le catene e le
  interfacce (conservate, solo nel modello, perse); il report HTML ha la sezione **Interfacce del complesso**. Il confronto
  dice quanto il modello riproduce l'interfaccia del template, non se l'interfaccia è quella vera del bersaglio.
- Anche un PIR a più catene (Editor PIR, modalità «Da documento PIR») produce un modello con le catene del template, le
  interfacce e la simmetria facoltativa.

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
- **Schermi stretti (telefono):** la barra laterale si apre dal pulsante del menu (☰) in alto a sinistra.
- **Tasti:** `?` (o **Tasti** nella barra laterale) elenca le scorciatoie: `g` e poi una lettera per le pagine principali
  (`g s` Sequenze, `g j` Job, `g q` Qualità…). Mentre scrivi in un campo valgono solo Ctrl K ed Esc.
- **Liste lunghe:** la tabella dei residui in Qualità, le coppie in Confronto e gli allineamenti con molte sequenze
  disegnano solo le righe visibili, e restano fluidi anche con migliaia di righe.

## Pacchetti riproducibili e riesecuzione

- **Pacchetto riproducibile** (dettaglio del job nella pagina Job e riquadro del job nelle pagine dei risultati): un file
  ZIP con il manifest (versione di BioLoom e dell'immagine, versioni di MODELLER e degli altri strumenti usati,
  parametri completi, semi casuali, impostazioni che contano, input scaricati da servizi remoti con la data, checksum
  SHA-256 di ogni file), gli input (sequenze, template della libreria e copie preparate, allineamento PIR, strutture
  analizzate), i risultati, gli script MODELLER eseguiti e un LEGGIMI. Non contiene mai la chiave di MODELLER, i codici
  di ProSA, la tua email, la chiave d'accesso del server né percorsi del tuo computer. Per modellazione, loop,
  allineamenti, qualità, analisi strutturale, rifinitura, tasche, elettrostatica, mutagenesi e scansione di alanina;
  anche ricerca di template, QMEAN e confronto con una predizione, ma questi dipendono da servizi remoti e non si
  possono rieseguire in locale (la predizione ricevuta, con la data, è nel pacchetto).
- **Riesegui da pacchetto** (pulsante nella pagina Job, `/rerun`): carica il pacchetto; BioLoom ne verifica manifest e
  checksum e ne mostra l'anteprima (versioni nel pacchetto e qui, parametri, input remoti) con i controlli preventivi.
  Il calcolo si ripete con gli stessi parametri, la cache dei calcoli ignorata, i template e le strutture del pacchetto
  (nessun download), poi si confronta con i risultati del pacchetto: **identico**, **equivalente** (entro le
  tolleranze numeriche), **compatibile** (differenze entro le tolleranze scientifiche del calcolo: il rumore di OpenMM
  su più thread nella rifinitura e nell'energia della mutagenesi, catena principale entro 0,05 Å ed energia entro
  max(50 kJ/mol; 0,5%)) o **diverso**, con RMSD delle coordinate, differenze di DOPE, GA341 e delle metriche principali. Versioni
  diverse di BioLoom o degli strumenti sono avvisi, non errori. Il rapporto (`RAPPORTO_RIESECUZIONE.txt`) resta nel job.
- **Da riga di comando:** `python bioloom.py rerun <pacchetto.zip> --data <cartella>` (nel container: `docker compose -p
  bioloom exec bioloom python3 -m bioloom rerun /data/<pacchetto.zip> --data /scratch/riesecuzione`). Con il server
  attivo la cartella dati è in uso: serve un'altra cartella. Codice d'uscita 0 se identico, equivalente o compatibile,
  1 se diverso, 2 se il pacchetto non è valido o il calcolo non riesce.
- MODELLER con lo stesso seme ripete gli stessi modelli; la rifinitura con OpenMM su più thread dà differenze piccole
  (ordine delle somme fra i thread): il confronto le quantifica e, entro le tolleranze dichiarate, le dice compatibili.

## Cosa contiene

| Area | Strumenti |
|---|---|
| Modellazione | MODELLER 10.8 (AutoModel, loop, allineamenti `align2d`/`salign`), con il trasferimento dei ligandi, cofattori e metalli scelti; complessi e omo-oligomeri (una sequenza per catena, restrizioni di simmetria, confronto delle interfacce con il template) su strutture a più catene o assemblaggi biologici importati |
| Ricerca template | RCSB, NCBI BLAST e modelli predetti di AlphaFold DB (online); BLAST+ e MMseqs2 sul database locale pdbaa; punteggio motivato di ogni template e template complementari consigliati |
| Allineamenti | Clustal Omega, MAFFT, MUSCLE, TM-align, US-align, più motori integrati; confronto strutturale con GDT-TS/GDT-HA, lDDT e deviazione per residuo |
| Qualità | Ramachandran, clashscore, DSSP (mkdssp), SASA, profilo DOPE, **PROCHECK**, **ProSA II**, **QMEAN locale** (UniClust30) o SWISS-MODEL, voto A–D, consenso tra i metodi, qualità per residuo nel 3D (anche pLDDT di AlphaFold), verifica di ligandi e metalli (contatti, scontri, coordinazione); i passi indipendenti girano in parallelo e con l'analisi "Su tutti i modelli" si analizzano più modelli insieme, report HTML stampabile in PDF |
| Analisi strutturale | legami idrogeno, ponti salini, ponti disolfuro, interazioni aromatiche, interfacce fra catene (superficie sepolta, plausibilità), conservazione da un allineamento multiplo mappata sulla struttura; tasche con **fpocket** (druggability), potenziale elettrostatico con **PDB2PQR** (PROPKA) e **APBS** |
| Rifinitura | **OpenMM** e **PDBFixer**: minimizzazione con restrizioni sulla catena principale e dinamica breve facoltativa, confronto prima/dopo |
| Mutagenesi | mutazioni puntuali e multiple con MODELLER (protocollo mutate_model), varianti di UniProt mappate sulla struttura, scansione di alanina con gli hot spot d'interfaccia, stima qualitativa dell'effetto (DOPE, indicatori strutturali, energia con OpenMM facoltativa) |
| Controllo di plausibilità | confronto del modello con una predizione di **ESMFold** (servizio esterno, con il consenso) o di AlphaFold DB: TM-score, lDDT per residuo, regioni divergenti incrociate con il pLDDT e con il consenso |
| Automazione | pipeline batch da multi-FASTA, riprendibili dopo un annullamento, un errore o un riavvio ("Riprendi" o "Riprova solo i falliti": le sequenze già modellate non si rifanno) e, se completate con sequenze fallite, "Riprova i falliti"; avanzamento, log e tabella dei risultati aggiornati in tempo reale, con la stima del tempo residuo ("circa 3 min rimanenti"); un template usato da più sequenze si prepara una volta sola per pipeline; verifica dell'installazione (Impostazioni → Strumenti) |
| Riproducibilità | pacchetto ZIP di un job (versioni, parametri, semi, checksum, input, risultati, script) e riesecuzione con il confronto dei risultati, dall'interfaccia o con `python bioloom.py rerun` |

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

**Licenze degli strumenti open source dell'immagine** (versioni fissate in `docker/Dockerfile` e
`backend/requirements-tools.lock`):

| Strumento | Versione | Licenza | Installazione |
|---|---|---|---|
| fpocket | 4.2.3 | MIT | compilato dalla release ufficiale (non è fra i pacchetti di Ubuntu 24.04) |
| APBS | 3.4.1 | BSD 3 clausole | binario ufficiale verificato con SHA-256 (il pacchetto di Ubuntu porterebbe circa 340 MB di dipendenze) |
| PDB2PQR | 3.7.1 | BSD 3 clausole | pip |
| PROPKA | 3.5.1 | LGPL 2.1 | pip (usato da PDB2PQR) |
| OpenMM | 8.6.1 | MIT (librerie) e LGPL (piattaforme CUDA/OpenCL, non usate) | pip |
| PDBFixer | 1.12.0 | MIT | pip |

Insieme aggiungono circa 140 MB all'immagine.

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

Per provare un'immagine con strumenti nuovi senza toccare quella dell'avviatore: `docker build -f docker/Dockerfile -t
bioloom:dev .` e poi `BIOLOOM_BASE_IMAGE=bioloom:dev scripts/test.sh`. Con l'immagine attuale i test degli strumenti che
mancano (fpocket, APBS, OpenMM…) vengono saltati con il motivo.

Profilazione dei tempi con MODELLER reale (job model, elemento di pipeline, analisi di qualità, avvio di MODELLER;
non fa parte della suite): `scripts/test.sh backend tests/perf_timing.py -s`. Le misure e le condizioni sono in
[Architettura → Prestazioni](docs/ARCHITETTURA.md#prestazioni). Ogni analisi di qualità scrive nel log del job la
durata dei suoi passi ("Analisi di qualità in 1.14 s (passi in parallelo: …)").

`bioloom.py` avvia il server senza Docker, utile solo per lo sviluppo.
