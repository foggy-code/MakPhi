# Pacchetti con licenza

Programmi con licenza accademica che l'immagine Docker compila o installa se trova qui il pacchetto. La procedura
completa per l'utente (licenze, pacchetti, ricostruzione, `LicenseDB`, verifica, server) è nella Guida:
[Licenze → Installare e attivare PROCHECK e ProSA](../../../Guida/6%20-%20Licenze.md#installare-e-attivare-procheck-e-prosa).
Se un pacchetto manca, la build prosegue e BioLoom mostra lo strumento come "non installato".

| File | Programma | Licenza |
|---|---|---|
| `procheck/procheck.tar.gz` | PROCHECK 3.5.4 (sorgenti) | gratuita per uso accademico, previa registrazione (EBI, R. Laskowski) |
| `prosa/prosa2003.linux.tar.gz` | ProSA II 2003 (Linux x86, 32 bit) | licenza personale da <https://prosa.services.came.sbg.ac.at>; il file `LicenseDB` si carica da BioLoom (Impostazioni → Licenze), non va messo qui |

Dopo aver aggiunto o tolto un pacchetto l'immagine va ricostruita a mano: questa cartella non entra nell'impronta del
codice (`docker/impronta.sh`), quindi l'avviatore non se ne accorge e la sua azione «aggiorna» non la ricostruisce.
Dalla cartella di BioLoom:

```bash
docker compose -f programma/launcher/compose.yml build bioloom
# con Podman, al posto di <versione> quella di programma/launcher/VERSION:
podman build --build-arg BIOLOOM_VERSION=<versione> -t bioloom:<versione> -f programma/docker/Dockerfile programma
```

poi riavvia BioLoom con l'avviatore (**R**, oppure `./avvia-bioloom.sh server` sul server). Viceversa, ogni
ricostruzione fatta senza i pacchetti (dall'avviatore all'avvio, con «aggiorna» o con «prepara») dà un'immagine senza
PROCHECK e ProSA. Per questo, quando aggiorni BioLoom sostituendo `programma/` con una versione nuova (copia portatile o
GitHub, che non li contengono), metti da parte i pacchetti e rimettili qui prima di avviare.

Questi file sono di chi ha ottenuto la licenza: non vanno pubblicati né distribuiti ad altri.
Sono esclusi da git (`.gitignore`) e dalla copia portatile (azione «prepara» dell'avviatore). Le sottocartelle
`procheck/` e `prosa/` restano anche vuote (`.gitkeep`): il Dockerfile le copia, e senza la build si fermerebbe.
