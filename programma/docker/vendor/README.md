# Pacchetti con licenza

Programmi con licenza accademica che l'immagine Docker compila o installa se trova qui il pacchetto.
Se un pacchetto manca, la build prosegue e BioLoom mostra lo strumento come "non installato".

| File | Programma | Licenza |
|---|---|---|
| `procheck/procheck.tar.gz` | PROCHECK 3.5.4 (sorgenti) | gratuita per uso accademico, previa registrazione (EBI, R. Laskowski) |
| `prosa/prosa2003.linux.tar.gz` | ProSA II 2003 (Linux x86, 32 bit) | licenza personale da <https://prosa.services.came.sbg.ac.at>; il file `LicenseDB` si carica da BioLoom (Impostazioni → Licenze), non va messo qui |

Questi file sono di chi ha ottenuto la licenza: non vanno pubblicati né distribuiti ad altri.
Sono esclusi da git (`.gitignore`) e dalla copia portatile (azione «prepara» dell'avviatore). Le sottocartelle
`procheck/` e `prosa/` restano anche vuote (`.gitkeep`): il Dockerfile le copia, e senza la build si fermerebbe.
