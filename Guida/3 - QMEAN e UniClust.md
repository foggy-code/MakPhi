# QMEAN e UniClust

Guida di BioLoom: [1. Primo avvio](1%20-%20Primo%20avvio.md) · [2. Funzioni](2%20-%20Funzioni.md) · **3. QMEAN e UniClust** · [4. Dati e privacy](4%20-%20Dati%20e%20privacy.md) · [5. Chiavetta e server](5%20-%20Chiavetta%20e%20server.md) · [6. Licenze](6%20-%20Licenze.md)

QMEAN locale gira in un container a parte e cerca gli omologhi con HHblits su UniClust30/UniRef30, che sta su un disco
esterno.

- **Versioni:** qualunque, anche più d'una sullo stesso disco: UniRef30 2020–2023, uniclust30 2018_08, uniclust30
  2016–2017 in formato HH-suite 2 (si converte una volta con **Converti** in Impostazioni → Database), database senza i
  file HMM. BioLoom usa la versione più recente pronta, oppure quella scelta in Impostazioni → Database, e segnala gli
  archivi `.tar.gz` non estratti, le copie incomplete e i database in formato MMseqs2, che HHblits non può usare.
- **Copia locale degli indici (Windows e macOS):** Docker Desktop porta il disco nel container con una condivisione di
  file, veloce a blocchi grandi ma lentissima per le letture piccole con cui HHblits scorre gli indici (solo caricarli
  richiedeva 15 minuti). **Copia indici**, in Impostazioni → Database, porta su ogni computer solo indici e cs219 (7,3 GB
  per UniRef30_2020_06, circa 3 minuti): finiscono nel disco di Docker, i file grandi restano sul disco esterno, e gli
  indici si caricano in pochi secondi. Se il database sul disco cambia, BioLoom torna a leggerlo dal disco finché non
  aggiorni la copia. Su Linux il disco è montato direttamente e la copia non serve.
- **Memoria:** HHblits deve tenere in memoria gli indici del database e l'intero cs219: con UniRef30_2020_06 servono
  circa **12 GB assegnati a Docker**. Con meno rilegge il cs219 di continuo e si ferma a metà per il timeout (con 6 GB,
  oltre 100 GB letti senza avanzare). Per questo, prima di partire, BioLoom confronta la memoria di Docker con quella
  richiesta dal database scelto (12 GB per UniRef30_2020_06, per le altre versioni una stima prudente dalla dimensione;
  Impostazioni → Database mostra entrambe): se non basta il job fallisce subito dicendo quanta memoria serve, oppure passa
  al fallback su SWISS-MODEL, se attivo. Con la memoria al limite HHblits usa un solo processore, che legge molto meno dal
  disco.
  - **Windows:** nel file `%UserProfile%\.wslconfig` metti `[wsl2]` e `memory=...GB`, poi `wsl --shutdown` (il valore
    predefinito è metà della RAM).
  - **macOS:** Docker Desktop → Settings → Resources.
- **Un calcolo alla volta:** gli altri QMEAN locali aspettano in coda ("2° in coda") senza fermare gli altri job; un
  calcolo che ripiega su SWISS-MODEL libera subito il servizio locale.
- **Fallback facoltativo su SWISS-MODEL:** se QMEAN locale non è disponibile (disco scollegato) o non riesce, il modello
  può essere inviato a SWISS-MODEL. Si attiva in Impostazioni → Generale oppure per il singolo calcolo, e richiede il
  consenso all'invio e la tua email. Il risultato dice sempre dove è stato calcolato e perché.
- **QMEAN automatico:** in Modellazione e in Pipeline la casella «Calcola QMEAN sul modello migliore alla fine» accoda
  un calcolo QMEAN a lavoro completato: sul modello migliore, oppure, nella pipeline, sul migliore di ogni sequenza (un
  job per modello, M × N compresa). Valgono le Impostazioni (locale o SWISS-MODEL, fallback); i controlli prima
  dell'avvio dicono se QMEAN potrà partire. Se alla fine non può (disco scollegato, invio non consentito) non si accoda
  nulla e il motivo è nel log del job. La pagina Qualità e il report del modello mostrano da soli il QMEAN già
  calcolato; un modello che ha già QMEAN non ne riceve un secondo, anche riprendendo la pipeline. Con migliaia di
  sequenze e QMEAN locale conta i tempi: da qualche minuto a oltre mezz'ora per modello, uno alla volta.
- **Copia di sicurezza dell'immagine:** l'immagine di QMEAN si costruisce da quella ufficiale, scaricata dal registro
  del SIB: se sparisse da lì, non si potrebbe più costruire. Conserva il file `bioloom-qmean-<versione>.tar` della copia
  portatile
  ([Copia di sicurezza dell'immagine QMEAN](5%20-%20Chiavetta%20e%20server.md#copia-di-sicurezza-dellimmagine-qmean)).
