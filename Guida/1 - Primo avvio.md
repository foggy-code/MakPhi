# Primo avvio

Guida di MakPhi: **1. Primo avvio** · [2. Funzioni](2%20-%20Funzioni.md) · [3. QMEAN e UniClust](3%20-%20QMEAN%20e%20UniClust.md) · [4. Dati e privacy](4%20-%20Dati%20e%20privacy.md) · [5. Chiavetta e server](5%20-%20Chiavetta%20e%20server.md) · [6. Licenze](6%20-%20Licenze.md)

## Avvio

1. Installa **Docker Desktop** (Windows, macOS; [licenza e alternative](#docker-desktop-licenza-e-alternative)) oppure
   **Docker Engine** con il plugin compose (Linux): <https://www.docker.com/products/docker-desktop/>,
   <https://docs.docker.com/engine/install/>.
2. Fai doppio clic sull'avviatore:

   | Sistema | Avvio | Arresto |
   |---|---|---|
   | Windows | `Avvia MakPhi.bat` | `Arresta MakPhi.bat` |
   | macOS | `Avvia MakPhi.command` | `Arresta MakPhi.command` |
   | Linux | `./avvia-makphi.sh` | `./avvia-makphi.sh arresta` |

3. Si apre il browser su `http://127.0.0.1:8765` (se la porta è occupata, un'altra fino alla 8800: [Porta
   occupata](#porta-occupata)). Al primo
   avvio inserisci la chiave di licenza di MODELLER in **Impostazioni → Licenze**: è gratuita per uso accademico
   (<https://salilab.org/modeller/>).

L'avviatore fa da solo il resto:

- avvia Docker Desktop se è spento (Windows e macOS);
- carica le immagini dalla cartella `immagini/` (senza internet) oppure le costruisce la prima volta;
- trova il disco con **UniClust30/UniRef30** per QMEAN locale, in qualunque versione, anche se cambia la lettera di
  unità o il punto di montaggio;
- assegna al container il MAC richiesto dalla licenza **ProSA**, sul computer a cui la licenza è legata
  ([un computer alla volta](6%20-%20Licenze.md#prosa-un-computer-alla-volta));
- sceglie una porta libera ([Porta occupata](#porta-occupata));
- si accorge se l'immagine Docker non corrisponde più al codice in `programma/` e propone di aggiornarla
  ([sotto](#aggiornare-makphi)).

Dalla sua finestra: **A** riapre il browser, **V** verifica gli strumenti, **L** mostra i log, **R** riavvia rifacendo la
ricerca dei dischi (dopo aver collegato quello di UniClust30), **U** indica a mano la cartella di UniClust30, **S** arresta
MakPhi, **C** chiude la finestra lasciando MakPhi attivo.

### Docker Desktop: licenza e alternative

Docker Desktop è gratuito per uso personale, didattico e nelle aziende piccole (meno di 250 dipendenti e meno di 10
milioni di dollari di fatturato annuo). Negli enti pubblici, anche piccoli, e nelle altre organizzazioni, se l'uso non è
didattico, verifica la licenza (<https://www.docker.com/pricing/>): può servire un abbonamento a pagamento. Su Linux
Docker Engine con il plugin compose è gratuito.

In alternativa va bene un programma che dia i comandi `docker` e `docker compose`: Rancher Desktop, Podman Desktop con
la compatibilità Docker e, su macOS, Colima. Non sono stati provati con MakPhi. Se Docker manca, su Windows e macOS
l'avviatore ricorda la licenza e queste alternative prima di fermarsi; su Linux indica Docker Engine con il plugin
compose.

### Porta occupata

MakPhi usa la porta dell'ultimo avvio (la prima volta la 8765). Se un altro programma la occupa, l'avviatore prova le
successive fino alla 8800 e poi riparte dalla 8765; la porta del MakPhi già acceso conta come libera. Se sono tutte
occupate si ferma prima di avviare («Nessuna porta libera per MakPhi: le porte da 8765 a 8800 sono tutte usate da altri
programmi…») e indica il comando per vedere quali programmi le usano:

| Sistema | Comando |
|---|---|
| Windows | `netstat -ano \| findstr LISTENING` (il numero in fondo è il processo, in Gestione attività → Dettagli) |
| macOS | `lsof -nP -iTCP -sTCP:LISTEN` |
| Linux | `ss -ltnp` |

Chiudi uno di quei programmi, oppure scegli tu la porta con la variabile `MAKPHI_PORT`, dalla cartella di MakPhi:

| Dove | Comando |
|---|---|
| Prompt dei comandi (Windows) | `set MAKPHI_PORT=9000`, poi `"Avvia MakPhi.bat"` |
| PowerShell (Windows) | `$env:MAKPHI_PORT = 9000`, poi `& ".\Avvia MakPhi.bat"` |
| Terminale (macOS, Linux) | `MAKPHI_PORT=9000 ./avvia-makphi.sh` |

Con `MAKPHI_PORT` la porta è fissa: se è occupata l'avviatore si ferma («La porta 9000 (MAKPHI_PORT) è già usata da un
altro programma…»), e un valore che non è un numero da 1 a 65535 non è accettato. La porta scelta diventa quella
dell'ultimo avvio: le volte successive, anche senza la variabile, MakPhi riparte da lì se è libera. `aggiorna` riavvia
MakPhi sempre sulla porta dell'ultimo avvio. Sul server la porta è quella di `server.env`
([Chiavetta e server](5%20-%20Chiavetta%20e%20server.md#installazione)).

### macOS: doppio clic bloccato

I file scaricati da internet (zip di GitHub, allegati di posta) restano in quarantena e macOS non apre
`Avvia MakPhi.command`. La prima volta fai clic destro sul file → **Apri** e conferma (da macOS 15: Impostazioni di
Sistema → Privacy e sicurezza → **Apri comunque**), oppure togli la quarantena a tutta la cartella dal Terminale. Se il
doppio clic dice invece che mancano i permessi, rendi eseguibili gli avviatori:

```bash
cd /percorso/di/MakPhi        # scrivi "cd " e trascina la cartella nel Terminale
xattr -dr com.apple.quarantine .
chmod +x "Avvia MakPhi.command" "Arresta MakPhi.command" avvia-makphi.sh programma/launcher/makphi.sh   # permessi
```

### Mac con processore Apple

MODELLER, ProSA e QMEAN esistono solo per processori x86: sui Mac con processore Apple (M1, M2…) i container girano in
emulazione e i calcoli sono più lenti. L'avviatore lo ricorda alla fine dell'avvio («Mac ARM: container amd64 in
emulazione…»). In Docker Desktop attiva **Settings → General → Use Rosetta for x86_64/amd64 emulation on Apple
Silicon**, l'emulazione più veloce. ProSA II è un programma x86 a 32 bit e su questi Mac non è stato provato: potrebbe
non funzionare, e in quel caso l'analisi di qualità prosegue senza ProSA, con un avviso.

### Un MakPhi alla volta

Tutte le copie di MakPhi di un computer (per esempio quella installata e quella della chiavetta) usano gli stessi
container Docker, quindi ne gira una alla volta. Se avvii una copia mentre è acceso il MakPhi di un'altra cartella,
l'avviatore lo dice («È già in esecuzione MakPhi da un'altra cartella: …») e chiede «Arrestarlo e continuare? [s/N]»:

- con **s** arresta l'altro (i suoi calcoli in corso si interrompono, i suoi dati restano nella sua cartella) e avvia
  questa copia;
- con Invio o **n** non tocca nulla e si ferma («Avvio annullato: resta in esecuzione il MakPhi di …»); con
  `-NoMenu` / `--no-menu` si ferma senza chiedere.

Lo stesso vale per l'azione `aggiorna`, quando deve riavviare MakPhi. **Arresta MakPhi** invece arresta il MakPhi
acceso da qualunque cartella sia partito, anche se quella copia non c'è più (chiavetta tolta), e dice quale era; le
azioni `stato`, `log` e `verifica` lo segnalano.

## Un percorso tipico

| Passo | Dove | Che cosa succede |
|---|---|---|
| 1. Sequenza | **Sequenze** | incolli, carichi da file o importi da UniProt la sequenza; vedi proprietà, struttura secondaria predetta e la mappa di disordine, bassa complessità e domini |
| 2. Template | **Ricerca template**, **Libreria template** | MakPhi cerca strutture omologhe, dà a ognuna un punteggio motivato e propone i template consigliati; **Usa i consigliati per la modellazione** porta al passo successivo |
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

## Aggiornare MakPhi

1. Arresta MakPhi e sostituisci nella sua cartella `programma/`, gli avviatori, `Guida/`, `README.md`, `LEGGIMI.txt`,
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
   la prima volta). Con **n** MakPhi parte con l'immagine che c'era. Se per ricostruirla mancano i pacchetti di
   PROCHECK o ProSA, l'avviatore lo dice prima e la domanda diventa «[s/N]»: con Invio non ricostruisce, così non
   perdi i due strumenti per sbaglio.

Si può anche aggiornare senza avviare MakPhi; se è acceso, viene riavviato con le immagini nuove e i calcoli in corso si
interrompono:

```bat
powershell -ExecutionPolicy Bypass -File programma\launcher\makphi.ps1 -Azione aggiorna
```

```bash
./avvia-makphi.sh aggiorna
```

L'azione aggiorna anche l'immagine di QMEAN, se QMEAN locale è in uso. Una ricostruzione include PROCHECK e ProSA solo se
i loro pacchetti sono in `programma/docker/vendor/`, che la copia portatile non contiene: in quel caso usa la cartella
`immagini/` della nuova copia portatile. Le immagini sostituite restano su disco: si cancellano da Docker Desktop
(Images), oppure con `docker image rm makphi:<versione vecchia>` e, per quelle rimaste senza nome, `docker image prune`.

### Da BioLoom a MakPhi

Fino alla versione 4.4.0 MakPhi si chiamava **BioLoom**. Per aggiornare una cartella di BioLoom segui i passi qui sopra:
gli avviatori nuovi sono `Avvia MakPhi` e `Arresta MakPhi`, quindi cancella `Avvia BioLoom` e `Arresta BioLoom` (e
rifai i collegamenti sul desktop, se li avevi). Al primo avvio MakPhi riprende tutto da solo:

- **dati**: `data/bioloom.db` diventa `data/makphi.db`; progetti, risultati e licenze restano dove sono;
- **configurazione**: le righe `BIOLOOM_…` di `.env.locale` diventano `MAKPHI_…` (l'originale resta in
  `.env.locale.bioloom`); una variabile `BIOLOOM_…` impostata a mano vale ancora;
- **container**: quelli di BioLoom si rimuovono prima dell'avvio; se BioLoom è acceso l'avviatore chiede prima di
  arrestarlo, come per [un'altra copia](#un-makphi-alla-volta);
- **volumi di Docker**: l'indice MMseqs2 e le copie di UniClust (`bioloom_cache`, `bioloom_qmean_cache`) si riusano
  senza ricostruirli;
- **browser**: tema, progetto attivo e pannelli restano come li avevi; la sessione con la chiave d'accesso resta
  aperta.

Le immagini di BioLoom (`bioloom:<versione>`, `bioloom-qmean:<versione>`) restano su disco: si cancellano con
`docker image rm`. I pacchetti di riproducibilità creati con BioLoom si aprono e si rieseguono come prima.
