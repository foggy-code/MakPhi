# Funzioni

Guida di BioLoom: [1. Primo avvio](1%20-%20Primo%20avvio.md) · **2. Funzioni** · [3. QMEAN e UniClust](3%20-%20QMEAN%20e%20UniClust.md) · [4. Dati e privacy](4%20-%20Dati%20e%20privacy.md) · [5. Chiavetta e server](5%20-%20Chiavetta%20e%20server.md) · [6. Licenze](6%20-%20Licenze.md)

Che cosa fa ogni pagina di BioLoom. Per iniziare, vedi prima [Un percorso tipico](1%20-%20Primo%20avvio.md#un-percorso-tipico).

## Sequenze e file

Ogni campo che riceve dati accetta anche un file:

| Dati | Formati accettati | Dove |
|---|---|---|
| Sequenze | FASTA, GenBank (con le traduzioni delle CDS), EMBL, UniProt, PIR, Clustal, Stockholm, MSF, PDB/mmCIF (una sequenza per catena), testo semplice; anche compressi `.gz`, in UTF-8, UTF-16 o Windows-1252; fino a 20 MB | Sequenze, Ricerca, Allineamenti, Modellazione, Pipeline |
| Allineamenti | PIR, oppure FASTA allineato, Clustal, Stockholm (convertiti in PIR) | Editor PIR e Modellazione |
| Strutture | `.pdb`, `.ent`, `.cif`, `.mmcif`, fino a 50 MB | Qualità, Confronto, Allineamenti strutturali, loop e rifinitura, Libreria template |
| Vincoli di modellazione | testo, una riga per vincolo: `helix 10:A 20:A`, `strand 30:A 35:A`, `distance CA:10:A CA:50:A 8.0 0.5`; esportabili nello stesso formato | Modellazione |
| Chiave MODELLER | file con la chiave, email di registrazione o `config.py` | Impostazioni → Licenze |
| Licenza ProSA | file `LicenseDB` | Impostazioni → Licenze |

**Disordine, bassa complessità e domini.** La pagina Sequenze mostra, prima di modellare, dove il modello sarà poco
affidabile: regioni **disordinate** predette dalla sola sequenza (FoldIndex, almeno 30 residui), tratti a **bassa
complessità** (SEG), **domini** InterPro (solo per le sequenze con accessione UniProt) e struttura secondaria predetta
(GOR). Le stesse regioni tornano negli avvisi della Ricerca e della Modellazione (una regione disordinata lunga o un
dominio fuori dalla copertura dei template, con un suggerimento come "modella per domini"), nel consenso della pagina
Qualità (come regioni "attese poco affidabili", senza abbassare il punteggio) e nel report. FoldIndex è un metodo
semplice e documentato, molto meno accurato dei predittori moderni come IUPred2A o quelli basati sul deep learning:
va letto come un'indicazione.

## Template

- **Motori di ricerca:** **RCSB** (predefinito), **NCBI BLAST**, **AlphaFold DB** (online); **BLAST+** e **MMseqs2** sul
  database locale pdbaa, che si scarica e si indicizza da Impostazioni → Database.
- **Punteggio 0–100** di ogni template (colonna **Punteggio**, ordinata per punteggio): media pesata di identità
  (confrontata con la zona crepuscolare di Rost, che dipende dalla lunghezza allineata), copertura del bersaglio,
  E-value, risoluzione (o pLDDT per i modelli predetti), R-free, metodo, lacune dell'allineamento e data. Fasce buona
  ≥ 70, intermedia 40–70, scarsa < 40; un template in zona crepuscolare non arriva mai in fascia buona. Un clic sulla riga
  mostra la motivazione di ogni componente.
- **Template consigliati:** il migliore più, se servono, fino a due template **complementari** che coprono almeno 10
  residui del bersaglio non coperti o migliorano l'identità locale, senza doppioni quasi identici; ogni scelta ed
  esclusione è motivata. Se due template di strutture diverse hanno meno di 10 residui del bersaglio in comune, la
  posizione reciproca delle loro regioni non è vincolata: motivazione e risultati lo dicono («Orientamento relativo non
  vincolato»).
- **Modelli AlphaFold DB:** per le proteine senza una struttura sperimentale omologa. **Libreria template → Da
  AlphaFold DB (UniProt)** scarica il modello di un'accessione (resta in libreria come `AF-P69905-F1`); il motore
  **AlphaFold DB** della Ricerca usa l'accessione (precompilata per le sequenze importate da UniProt) oppure, senza,
  cerca le proteine più simili con NCBI BLAST su Swiss-Prot (1–5 minuti). Un modello predetto è sempre segnato come
  tale, con il pLDDT medio. In modellazione i residui con pLDDT sotto la soglia (predefinita 70) e i frammenti rimasti
  isolati (meno di 5 residui) si tolgono dal template e MODELLER li costruisce senza template; il numero di residui tolti
  è nel log e nei risultati. Un file conta come modello predetto (pLDDT nella colonna B-factor) solo con l'intestazione
  di AlphaFold DB o di ESMFold o con il nome `AF-<accessione>-F<n>`, mai per i soli valori.
- **Libreria template:** download da RCSB e da AlphaFold DB, caricamento di file, catene, residui mancanti, ligandi
  classificati, struttura secondaria e 3D. Per le strutture del PDB elenca gli **assemblaggi biologici** dichiarati nel
  file: **Importa come template** crea `<ID>_ASM<n>` con le catene dell'assemblaggio (operatori di simmetria applicati).
- **Ricerche già fatte:** una ricerca con gli stessi dati e parametri non si rifà (nota **dalla cache (data)**; vedi
  [Dati e privacy](4%20-%20Dati%20e%20privacy.md)).

## Allineamenti

Pagina **Allineamenti**: a coppie, multiplo (Clustal Omega, MAFFT, MUSCLE o il motore integrato) e strutturale
(US-align, TM-align o il motore integrato, con **Apri in Confronto**); esportazione in FASTA, Clustal e PIR e
salvataggio nel progetto. L'**Editor PIR** gestisce i documenti PIR del progetto, li genera con MODELLER e li valida
mentre scrivi. In Allineamenti, Modellazione ed Editor PIR la modalità **Confidenza** colora le colonne per identità
locale rispetto al bersaglio e segnala la zona crepuscolare (sotto il 25%) e i residui non coperti dal template.

## Modellazione

- **Input:** **Allineamento automatico** (bersaglio e template), **Da documento PIR** oppure **Complesso** (sotto).
- **Raffinamento:**

  | Livello | Che cosa fa | Tempo rispetto a Rapido (misurato) |
  |---|---|---|
  | Rapidissimo | ottimizzazione minima senza dinamica: più modelli sarebbero identici, ne basta uno | circa un quarto |
  | Rapido (predefinito) | le impostazioni predefinite di MODELLER | 1 |
  | Standard | dinamica di raffinamento più lunga | circa 1,4 volte |
  | Accurato | ottimizzazione ripetuta e dinamica lenta, per il modello finale | circa 4 volte |

- **Opzioni:** numero di modelli, seme casuale, vincoli di struttura secondaria e di distanza, raffinamento dei loop
  (DOPE-loop), analisi di qualità sul modello migliore o **Su tutti i modelli**. Dopo la modellazione **Raffina un loop**
  ricostruisce un tratto di un modello; il riquadro **Raffinamento dei loop e rifinitura energetica** lo fa per qualunque
  struttura.
- **Più template:** MODELLER allinea i template fra loro; se così un template finisce spostato rispetto al bersaglio,
  BioLoom usa l'allineamento di ciascun template sul bersaglio e lo scrive nel log. Quando non può (template a più
  catene o con ligandi) lo segnala fra gli avvisi del risultato e del report: l'allineamento si corregge nell'Editor PIR.
- **Ligandi, cofattori e metalli:** **Trasferisci ligandi, cofattori e metalli dai template** elenca i gruppi delle catene
  scelte con la classe (metallo, cofattore noto come eme, FAD, NAD, ATP, SAM, PLP, CoA…, additivo, altro ligando).
  Additivi di cristallizzazione (SO₄, glicerolo, PEG, tamponi…) e ioni Na⁺/K⁺ del tampone sono deselezionati per default,
  con il motivo; le acque non si trasferiscono. Con più template lo stesso gruppo nello stesso sito (l'eme di due globine)
  diventa un solo gruppo del modello, vincolato da tutti. Dopo la modellazione BioLoom verifica ogni gruppo (residui entro
  4 Å, scontri sotto 2,2 Å, coordinazione dei metalli con le distanze attese e la geometria, sito del template conservato)
  con l'esito **buono**, **da controllare** o **problematico**; il voto A–D non cambia.
- **Complessi e omo-oligomeri** (modalità **Complesso**, anche dal pulsante **Complesso** della Libreria o da **Modella il
  complesso** di un assemblaggio): per ogni catena del template una sequenza del bersaglio, dal progetto, incollata o
  **stessa della catena …**. Le catene del modello hanno i nomi del template, con i residui numerati da 1; con catene
  uguali si possono aggiungere **restrizioni di simmetria** (rendono uguali le catene; la disposizione viene dal
  template). **Anteprima dell'allineamento per catena** mostra l'allineamento di ogni catena in modalità Confidenza. Da 2 a
  12 catene e al più 3000 residui in tutto. Per ogni modello BioLoom confronta le interfacce con quelle del template
  (residui conservati, area sepolta, **DockQ**, esito alta, media, accettabile o non riprodotta): dice quanto il modello
  riproduce l'interfaccia del template, non se l'interfaccia è quella vera del bersaglio.
- **Controlli prima dell'avvio** (Modellazione, Pipeline, procedura guidata, mutagenesi, predizioni e riesecuzioni):
  mentre scegli, BioLoom verifica senza avviare nulla licenza MODELLER, template (presenti, scaricabili, catene),
  identità sotto la zona crepuscolare e copertura, regioni disordinate e domini scoperti, ritaglio pLDDT che lascia pochi
  residui, PROCHECK e ProSA per l'analisi estesa, tempo stimato e rischio di timeout, spazio libero, molte ricerche su
  NCBI (le sue linee guida chiedono di lanciarne più di 50 di sera o nel fine settimana).
  - **Errori** (rosso): il pulsante di avvio resta disattivato, con il motivo;
  - **avvisi** (giallo): l'avvio chiede conferma («Avvia comunque»);
  - **informazioni**: in piccolo, senza conseguenze (tempo stimato, dati inviati ai servizi remoti).

  I tempi sono stimati dalle misure su un portatile a 4 CPU logiche e crescono con i residui e i modelli: su un altro
  computer possono variare di un fattore 2.
- **Risultati:** tabella dei modelli con DOPE, zDOPE, GA341 e voto, 3D, file PyMOL, archivio ZIP, **Confronta…** (con un
  template usato o con un altro modello del job), **Report**, **Rifinisci** e **Mutagenesi** per ogni modello.

## Qualità

- **Metriche:** Ramachandran, clashscore, struttura secondaria (DSSP), superficie accessibile, profilo DOPE,
  **PROCHECK**, **ProSA II** e, su richiesta, **QMEAN** ([locale o SWISS-MODEL](3%20-%20QMEAN%20e%20UniClust.md)). Una
  struttura qualunque si analizza caricandone il file; dalla Modellazione l'analisi gira sul modello migliore o su tutti.
- **Voto A–D** da outlier di Ramachandran, clashscore e zDOPE.
- **Consenso tra i metodi:** voto BioLoom, z-DOPE, GA341, QMEAN, ProSA e PROCHECK, quando disponibili, sono portati su
  una scala 0–100 dichiarata e combinati in un punteggio con fascia buona / intermedia / scarsa. Il riquadro spiega i
  metodi che discordano ed elenca le **regioni problematiche** (tratti segnalati da almeno due metodi), cliccabili. Il voto
  A–D non cambia.
- **Qualità per residuo nel 3D:** nelle schede **3D** e **Profili per residuo** il menu **Colora per** colora la struttura
  per struttura secondaria, Ramachandran, clash, DOPE per residuo, RSA, B-factor, **pLDDT** dei modelli predetti (scala
  ufficiale di AlphaFold DB), QMEAN locale calcolato nella pagina e consenso per residuo, con la legenda. Un clic su
  grafici o tabelle evidenzia e centra il residuo nel 3D; un clic nel 3D lo seleziona nella tabella e nei grafici.
- **Ligandi e metalli:** la sezione omonima riporta la verifica di ogni gruppo eteroatomico (vedi
  [Modellazione](#modellazione)); un clic su un gruppo lo mostra nel 3D con i residui coordinanti.
- **Confronta con …** il template del modello o un'altra struttura, nella pagina [Confronto](#confronto-strutturale).
- **Confronta con una predizione (ESMFold)** (anche sotto il modello scelto in Modellazione): un controllo di
  plausibilità con una predizione indipendente della stessa sequenza, da **ESMFold** (servizio pubblico di ESM Atlas, fino
  a 400 residui) oppure, con l'accessione UniProt, da **AlphaFold DB**.
  - **Sequenza:** quella della catena del modello (residui modificati compresi) oppure quella incollata nella finestra.
    Se alla catena mancano dei residui, BioLoom invia la sequenza completa del file (SEQRES) quando c'è; altrimenti
    avvisa e non conta come divergenze i residui vicini alle giunzioni: incolla la sequenza completa per un confronto
    pulito.
  - **Esito motivato:** TM-score (da 0,5 stesso ripiegamento), lDDT per residuo e regioni in cui il modello diverge dalla
    predizione, incrociate con la confidenza della predizione (pLDDT) e con le regioni problematiche del consenso, per
    esempio «divergono in 40-52, dove la predizione è sicura: controlla l'allineamento» oppure «divergono in 70-76, dove
    anche la predizione è incerta». Sotto, il confronto per residuo.
  - A confronto eseguito l'accordo con la predizione entra nel consenso con un peso basso (0,25) e il report ha la sua
    sezione; senza confronto nulla cambia. La predizione si apre in Qualità con il suo pLDDT.

  Una predizione non è una struttura sperimentale: con un template sperimentale vicino il modello per omologia è di
  solito più affidabile.

## Analisi strutturale

Nella pagina Qualità, sulla struttura mostrata (link stabili `/quality?ajob=`, `?pjob=`, `?ejob=`):

- **Interazioni:** legami idrogeno, ponti salini, ponti disolfuro e interazioni aromatiche (π-stacking parallelo o a T,
  catione-π), filtrabili per catena (anche "solo fra catene diverse") e per residuo (`27`, `K27`, `LYS27`, `A:27`); un clic
  su una riga evidenzia i due residui nel 3D, un clic nel 3D filtra le tabelle. Acque e ligandi sono esclusi.
  - **Interfacce** (strutture con più catene): residui d'interfaccia (superficie sepolta > 1 Å²), area, composizione,
    interazioni fra le catene e un indicatore di plausibilità biologica basato sull'area, con i suoi limiti dichiarati
    (non sostituisce PISA o EPPIC).
  - **Conservazione:** da un allineamento multiplo (un job di Allineamenti del progetto, oppure sequenze o un file
    FASTA/Clustal/Stockholm; le sequenze non allineate si allineano prima) la conservazione di ogni colonna (0–1,
    entropia di Shannon con la penalità per i gap) colora il 3D con la tavolozza di ConSurf. La riga della struttura si
    indica con il nome del FASTA (`sp|P0CG47|UBB_HUMAN`) o con quello mostrato da BioLoom (`sp_P0CG47_UBB_HUMAN`).
- **Tasche** (fpocket): cavità della superficie ordinate per *druggability* (0–1; da 0,5 la tasca è probabilmente adatta a
  legare una piccola molecola simile a un farmaco), con punteggio, volume, sfere alfa e residui che la rivestono, colorate
  nel 3D. Ligandi e acque si escludono dal calcolo, quindi i loro siti compaiono come tasche vuote.
- **Elettrostatica** (PDB2PQR e APBS): cariche e raggi PARSE o AMBER con la protonazione di PROPKA (pH 7, modificabile),
  equazione di Poisson-Boltzmann con 150 mM di sale, dielettrici 2 e 78,54, 25 °C. Il potenziale sulla superficie colora i
  residui dal rosso (negativo) al blu (positivo) fino a ±5 kT/e; carica netta, residui più carichi e la mappa `.dx` da
  aprire in PyMOL, Chimera o VMD.

Il report HTML del modello riassume interfacce e ponti disolfuro, se l'analisi della stessa struttura li ha trovati.

## Rifinitura energetica e mutagenesi

- **Rifinitura energetica** (**Rifinisci** nella tabella dei modelli, o per qualunque struttura nel riquadro dei loop):
  PDBFixer completa gli atomi e aggiunge gli idrogeni, OpenMM minimizza l'energia con AMBER ff14SB e solvente implicito
  tenendo ferma la catena principale con restrizioni; facoltativa una breve dinamica a 300 K. Il risultato confronta
  prima e dopo (voto, Ramachandran, clashscore, DOPE, energia, spostamento della catena principale) e porta alla
  Qualità del modello rifinito e al Confronto con l'originale. Ligandi e ioni non sono rifiniti (il modello rifinito
  contiene solo la proteina). Su 1UBQ la minimizzazione richiede da 15 s a circa un minuto.
- **Mutagenesi in silico** (scheda **Mutagenesi** della Qualità, **Mutagenesi** nella tabella dei modelli, o per qualunque
  struttura nel riquadro dei loop):
  - **Mutazioni** nella numerazione della struttura: `A:K48R`, anche `A:LYS48ARG`; più mutazioni separate da virgole (al
    più 10) formano una variante. Il residuo originale deve corrispondere a quello della struttura, altrimenti BioLoom lo
    dice prima di partire. MODELLER sostituisce il residuo e riottimizza la sua catena laterale e quelle entro 5 Å; il
    selvatico passa dallo stesso protocollo, così il confronto misura solo la sostituzione.
  - **Varianti di UniProt:** con l'accessione, le varianti naturali, associate a malattie e di mutagenesi sperimentale,
    portate sulla numerazione della struttura con un allineamento (anche quando le numerazioni non coincidono o la
    proteina ha copie ripetute); «Aggiungi» le porta nel campo.
  - **Scansione di alanina:** i residui indicati (`A:30, A:43, A:45-48`), quelli dell'interfaccia fra catene o quelli di
    una tasca trovata da fpocket diventano uno alla volta alanina (al più 50, 20 con l'energia; glicina, prolina e alanina
    escluse, con il motivo). Tabella dalla sostituzione più destabilizzante, 3D colorato per effetto, hot spot
    d'interfaccia, CSV.
  - **Effetto stimato:** *probabilmente destabilizzante*, *incerta* o *probabilmente neutra*, con le motivazioni
    (differenza di DOPE, prolina o glicina in un'elica o in un filamento, legami idrogeno, ponti salini e disolfuri persi,
    superficie idrofobica sepolta persa, residuo nel nucleo, contatti sterici, residui d'interfaccia). Facoltativa la
    differenza di energia con OpenMM (circa un minuto in più). **Non è un ΔΔG** né una previsione calibrata come FoldX o
    Rosetta (che BioLoom non usa: licenze non ridistribuibili): indica la direzione probabile dell'effetto, da
    verificare.
  - Il risultato confronta selvatico e mutante (Qualità di entrambi, pagina Confronto) e ha il suo report HTML.

Ogni calcolo è un job con un link stabile (`/modeling?job=<id>`).

## Confronto strutturale

La pagina **Confronto** sovrappone la struttura B alla struttura A, il riferimento (per esempio il template o una
struttura sperimentale), con US-align/TM-align o con il motore integrato.

- **Metriche:** TM-score normalizzato su A e su B, RMSD, RMSD delle coppie entro 5 Å, **GDT-TS** e **GDT-HA**
  (percentuale dei Cα di A entro 1–8 Å e 0,5–4 Å) e **lDDT** sui Cα, che non usa sovrapposizioni.
- **Per residuo:** nel 3D la struttura B è colorata per distanza dal residuo corrispondente (da 0 a 5 Å e oltre) o per
  lDDT, A resta in grigio chiaro; il grafico mostra distanza e lDDT lungo la sequenza e la tabella elenca ogni coppia. Un
  clic su grafico, tabella o tratto che devia evidenzia la coppia nel 3D, un clic nel 3D la seleziona.
- **Da dove si apre:** Modellazione (**Confronta…** di ogni modello), Qualità (**Confronta con …**), Allineamenti (**Apri
  in Confronto**), rifinitura, mutagenesi e confronto con una predizione. Le due strutture arrivano già scelte e il link
  resta stabile.

## Report

Il pulsante **Report** (tabella dei modelli in Modellazione, pagina Qualità, righe della pipeline e la sua finestra
«Visualizza», risultati di rifinitura e mutagenesi) scarica un file HTML autonomo, da aprire con qualunque browser
anche senza BioLoom e da stampare in PDF (Stampa → Salva come PDF):

- intestazione con job, progetto, date e versioni (BioLoom, MODELLER, strumenti di validazione usati);
- bersaglio, template (sorgente, identità e copertura; avviso e ritaglio pLDDT per i modelli predetti), allineamento
  PIR a blocchi, parametri di MODELLER e tabella dei modelli con il migliore evidenziato;
- per il modello: voto con le penalità, consenso tra i metodi (discordanze e regioni problematiche), grafici di
  Ramachandran e profili per residuo (DOPE; QMEAN locale se calcolato nella pagina Qualità; disordine predetto),
  struttura secondaria, contatti sterici principali, ligandi e metalli, interfacce del complesso, analisi strutturale,
  confronto con una predizione (quando ci sono), avvisi, legenda delle metriche e riferimenti bibliografici;
- la **vista 3D corrente**, se il viewer è aperto su quel modello (in Qualità: scheda 3D; in Pipeline: «Visualizza»).

Per un'analisi di qualità di una struttura qualsiasi il report è ridotto (niente template, allineamento e parametri);
rifinitura, mutagenesi e scansione di alanina hanno le loro sezioni. L'archivio ZIP della pipeline contiene un report
per ogni sequenza completata (cartella `report/`, senza vista 3D).

## Pipeline, job e coda dei calcoli

- **Pipeline batch:** da un multi-FASTA (o **Usa le sequenze del progetto**) ai modelli validati: per ogni sequenza
  ricerca del template, modellazione e qualità, con template da ricerca automatica, **Più template complementari** (da 2
  a 5, predefinito 3, scelti con la regola dei consigliati) oppure fissi. Filtro della zona crepuscolare, tabella dei
  risultati, CSV e ZIP; avanzamento, log e tabella si aggiornano in tempo reale con la stima del tempo residuo («circa 3
  min rimanenti»).
- **Ripresa:** una pipeline annullata, fallita o interrotta da un riavvio si riprende senza rifare le sequenze già
  modellate (**Riprendi**, oppure **Riprova solo i falliti**); una completata con sequenze fallite mostra **Riprova i
  falliti**.
- **Job:** ogni calcolo è un job, con log e file nella pagina **Job**, aggiornati in tempo reale, e un link stabile.
  Rilanciando una pipeline con le stesse sequenze e altre opzioni di modellazione, le ricerche già fatte vengono dalla
  [cache dei calcoli](4%20-%20Dati%20e%20privacy.md#dove-stanno-i-dati). Un job terminato si elimina dal suo dettaglio (cestino), con tutti i suoi
  file.
- **Pulisci job…** (pagina **Job**, oppure **Elimina job…** in Impostazioni → Spazio e file temporanei): elimina in
  blocco i job terminati che non servono più. Scegli l'ambito (il progetto attivo quando apri il dialogo, oppure tutti
  i progetti), gli stati (falliti e annullati già scelti; i completati solo spuntando **Ho capito**, perché i loro
  risultati possono servire ad altri job), l'età (predefinita: oltre 7 giorni) e il tipo. I job con la stella o con
  etichette e le pipeline che si possono ancora riprendere restano, salvo che tu lo chieda con le caselle del dialogo.
  Un'anteprima dice quanti job e quanto spazio vanno via e quali sono i più vecchi; dopo la conferma la pulizia procede
  a blocchi di pochi secondi, con l'avanzamento e **Interrompi**, e non elimina i job terminati dopo l'anteprima. I job
  in coda o in esecuzione non si toccano mai; il riepilogo dice anche quanti job sono stati saltati (in uso, ripresi o
  cambiati nel frattempo, per esempio con una stella aggiunta).
- **Eliminare un progetto:** nel dialogo **Progetti** (in alto), il cestino della riga. La conferma mostra che cosa va
  via per sempre (sequenze, documenti, job con i loro file e lo spazio che occupano, compresi quelli con la stella o con
  etichette) e, se il progetto non è vuoto, chiede di scriverne il nome. Con job in coda o in esecuzione non si può:
  prima vanno annullati (**Vai ai job**). I job si eliminano a blocchi, con l'avanzamento e **Interrompi**: se
  interrompi, il progetto, le sue sequenze e i suoi documenti restano (i job già eliminati no). Eliminando l'ultimo
  progetto BioLoom ne crea uno vuoto, «Nuovo progetto», perché ne serve sempre almeno uno.
- **File in uso:** se un file di un job è aperto in un altro programma (antivirus, Esplora risorse), il job non si
  elimina e BioLoom chiede di chiuderlo; i resti di un job già eliminato li toglie **Pulisci file temporanei**.
- **Priorità:** ricerche, allineamenti, analisi di qualità e strutturali, tasche, elettrostatica, QMEAN, mutagenesi e
  predizioni partono prima di modellazioni, loop, rifiniture, scansioni di alanina e riesecuzioni, e questi prima di
  pipeline e aggiornamenti dei database; a parità, in ordine di creazione. Un job che aspetta da molto sale di priorità,
  così nessuno resta in coda per sempre. I job in coda mostrano la posizione ("2° in coda") nella pagina Job e nel
  riquadro del job.
- **CPU condivisa:** i programmi di calcolo di tutti i job insieme (MODELLER, PROCHECK, ProSA, BLAST+, MMseqs2,
  allineatori, OpenMM…) non superano i **Job di calcolo in parallelo** delle Impostazioni (predefinito: core − 1; vale
  subito, senza riavviare). Chi aspetta mostra "In attesa di CPU libera"; l'attesa non conta nel timeout e l'annullamento
  la interrompe. La Panoramica mostra gli slot di calcolo in uso.
- **Verifica dell'installazione:** Impostazioni → Strumenti (o **V** nella finestra dell'avviatore).

## Riproducibilità

- **Pacchetto riproducibile** (dettaglio del job nella pagina Job e riquadro del job nelle pagine dei risultati): un ZIP
  con il manifest (versioni di BioLoom, dell'immagine, di MODELLER e degli altri strumenti usati, parametri completi,
  semi casuali, impostazioni che contano, input scaricati da servizi remoti con la data, checksum SHA-256 di ogni file),
  gli input (sequenze, template e copie preparate, allineamento PIR, strutture analizzate), i risultati, gli script
  MODELLER eseguiti e un LEGGIMI. Non contiene mai la chiave di MODELLER, i codici di ProSA, la tua email, la chiave
  d'accesso del server né percorsi del tuo computer. Per modellazione, loop, allineamenti, qualità, analisi strutturale,
  rifinitura, tasche, elettrostatica, mutagenesi e scansione di alanina; anche per ricerca di template, QMEAN e confronto
  con una predizione, che però dipendono da servizi remoti e non si possono rieseguire (la predizione ricevuta è nel
  pacchetto, con la data).
- **Riesegui da pacchetto** (pulsante nella pagina Job, `/rerun`): BioLoom verifica manifest e checksum, mostra
  l'anteprima (versioni nel pacchetto e qui, parametri, input remoti) con i controlli prima dell'avvio, ripete il calcolo
  con gli stessi parametri e gli input del pacchetto (nessun download, cache ignorata) e confronta i risultati:
  **identico**, **equivalente** (entro le tolleranze numeriche), **compatibile** (entro le tolleranze scientifiche
  dichiarate: il rumore di OpenMM su più thread nella rifinitura e nell'energia della mutagenesi) o **diverso**, con RMSD
  delle coordinate e differenze delle metriche. Versioni diverse di BioLoom o degli strumenti sono avvisi, non errori. Il
  rapporto (`RAPPORTO_RIESECUZIONE.txt`) resta nel job.
- **Da riga di comando:** `python programma/bioloom.py rerun <pacchetto.zip> --data <cartella>`, oppure nel container
  `docker compose -p bioloom exec bioloom python3 -m bioloom rerun /data/<pacchetto.zip> --data /scratch/riesecuzione`.
  Con il server attivo la cartella dati è in uso: serve un'altra cartella. Codice d'uscita 0 se identico, equivalente o
  compatibile, 1 se diverso, 2 se il pacchetto non è valido o il calcolo non riesce.

## Ricerca, etichette e interfaccia

- **Ricerca globale:** il campo **Cerca…** in alto (oppure **Ctrl K**, **⌘K** sul Mac, o `/`) trova progetti, sequenze
  (anche per accessione UniProt), documenti, job (anche per tipo: «modellazione»), template (id, titolo, organismo) ed
  etichette. Ogni parola vale come inizio di una parola («p6990» trova «sp|P69905|HBA_HUMAN»), maiuscole e accenti non
  contano. Frecce per scegliere, Invio per aprire (anche in un altro progetto), Esc per chiudere. `#etichetta` cerca solo
  fra gli elementi con quell'etichetta, `tipo:job` (o `tipo:template`, `tipo:sequenze`…) solo un tipo; a campo vuoto
  mostra i preferiti.
- **Etichette e stella:** progetti, job, sequenze e template hanno la stella dei preferiti e fino a 20 etichette
  (minuscole, al più 32 caratteri; «Da Rivedere» diventa «da-rivedere»). Si aggiungono nel dettaglio dell'elemento (per il
  progetto attivo nella Panoramica); negli elenchi di job e template e nel dialogo **Progetti** si filtra per etichetta o
  per preferiti.
- **Tema:** Scuro, Chiaro o **Sistema** (segue il sistema operativo), in fondo alla barra laterale.
- **Tasti:** `?` (o **Tasti** nella barra laterale) elenca le scorciatoie: `g` e poi una lettera per le pagine principali
  (`g s` Sequenze, `g j` Job, `g q` Qualità…). Mentre scrivi in un campo valgono solo Ctrl K ed Esc.
- **Schermi stretti:** la barra laterale si apre dal pulsante del menu (☰) in alto a sinistra.
