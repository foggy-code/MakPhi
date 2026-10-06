# Licenze

Guida di BioLoom: [1. Primo avvio](1%20-%20Primo%20avvio.md) · [2. Funzioni](2%20-%20Funzioni.md) · [3. QMEAN e UniClust](3%20-%20QMEAN%20e%20UniClust.md) · [4. Dati e privacy](4%20-%20Dati%20e%20privacy.md) · [5. Chiavetta e server](5%20-%20Chiavetta%20e%20server.md) · **6. Licenze**

- **MODELLER:** si inserisce da Impostazioni → Licenze e viene provata con MODELLER prima di essere salvata. Su un server
  si può indicare anche `MODELLER_KEY` in `server.env`.
- **ProSA II:** si carica il file `LicenseDB` da Impostazioni → Licenze; l'avviatore assegna al container il MAC indicato
  nella licenza.
- **PROCHECK:** licenza accademica, senza chiave.

Le chiavi restano in `data/licenses/` e non entrano mai nelle immagini Docker. I pacchetti PROCHECK e ProSA
(`programma/docker/vendor/`) e le chiavi sono tuoi: non distribuirli ad altri.

**Strumenti open source dell'immagine** aggiunti per tasche, elettrostatica e rifinitura (versioni fissate in
`programma/docker/Dockerfile` e `programma/backend/requirements-tools.lock`; insieme circa 140 MB):

| Strumento | Versione | Licenza | Installazione |
|---|---|---|---|
| fpocket | 4.2.3 | MIT | compilato dalla release ufficiale (non è fra i pacchetti di Ubuntu 24.04) |
| APBS | 3.4.1 | BSD 3 clausole | binario ufficiale verificato con SHA-256 (il pacchetto di Ubuntu porterebbe circa 340 MB di dipendenze) |
| PDB2PQR | 3.7.1 | BSD 3 clausole | pip |
| PROPKA | 3.5.1 | LGPL 2.1 | pip (usato da PDB2PQR) |
| OpenMM | 8.6.1 | MIT (librerie) e LGPL (piattaforme CUDA/OpenCL, non usate) | pip |
| PDBFixer | 1.12.0 | MIT | pip |

Gli altri strumenti dell'immagine (MODELLER 10.8, mkdssp, BLAST+, MMseqs2, Clustal Omega, MAFFT, MUSCLE, TM-align,
US-align) sono elencati in [Architettura → Vista d'insieme](../programma/docs/ARCHITETTURA.md#vista-dinsieme). Un'immagine costruita
prima di questi strumenti continua a funzionare: tasche, elettrostatica e rifinitura dicono che lo strumento manca.
