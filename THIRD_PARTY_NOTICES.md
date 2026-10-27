# Avvisi di terze parti

MakPhi è © 2026 foggy02 e ha licenza MIT ([LICENSE](LICENSE)): il codice in `programma/` (backend, interfaccia web,
avviatori, script, Dockerfile e worker di QMEAN in `programma/docker/qmean/`), la guida e gli avviatori nella cartella
principale.

MakPhi distribuisce anche software di terzi: nelle immagini Docker `makphi` e `makphi-qmean` (costruite
dall'avviatore o salvate in `immagini/` nella copia portatile) e nell'interfaccia web. Ogni componente resta sotto la
propria licenza, che qui è solo riassunta: fa fede il testo del componente. Le licenze sono indicate con gli
identificativi [SPDX](https://spdx.org/licenses/); `LicenseRef-…` indica una licenza senza identificativo SPDX.

Le versioni dei pacchetti Python, dei programmi compilati e dell'interfaccia sono fissate (Dockerfile,
`requirements*.lock`, `package-lock.json`); quelle dei pacchetti di Ubuntu no: sono le versioni rilevate il 10 ottobre
2026 in un'immagine costruita con lo stesso Dockerfile (cambiano solo versione e impronta) e la revisione Ubuntu può
cambiare a ogni ricostruzione. L'elenco esatto di un'immagine si ottiene così (dalla cartella di MakPhi; su Git Bash
per Windows anteporre `MSYS_NO_PATHCONV=1` ai comandi `docker`; `tr` toglie l'a capo e il CR che `VERSION` ha in una
cartella venuta da Windows):

```bash
V=$(tr -d '[:space:]' < programma/launcher/VERSION)
docker run --rm --platform linux/amd64 --entrypoint dpkg-query "makphi:$V" -W -f='${Package} ${Version} ${source:Package} ${source:Version}\n'
docker run --rm --platform linux/amd64 --entrypoint /opt/venv/bin/pip "makphi:$V" list
docker run --rm --platform linux/amd64 --entrypoint rpm "makphi-qmean:$V" -qa --qf '%{NAME} %{VERSION}-%{RELEASE} %{LICENSE} %{SOURCERPM}\n'
```

1. [Software non open source](#1-software-non-open-source)
2. [Immagine makphi](#2-immagine-makphi)
3. [Immagine makphi-qmean](#3-immagine-makphi-qmean)
4. [Interfaccia web](#4-interfaccia-web)
5. [Software GPL e LGPL: sorgente corrispondente](#5-software-gpl-e-lgpl-sorgente-corrispondente)
6. [Testi delle licenze](#6-testi-delle-licenze)

## 1. Software non open source

Programmi con licenze accademiche o proprietarie: MakPhi li usa, ma non può concederli in licenza ad altri.

| Programma | Dove | Licenza | Condizioni principali |
|---|---|---|---|
| MODELLER 10.8 | `makphi`, sempre (pacchetto degli autori scaricato alla costruzione) | `LicenseRef-MODELLER-Academic` | gratuita per istituzioni accademiche senza scopo di lucro, solo per ricerca interna non commerciale, con chiave personale; vietato distribuire o trasferire il programma senza il permesso scritto del licenziante. <https://salilab.org/modeller/> |
| PROCHECK 3.5.4 | `makphi`, solo se alla costruzione c'era `programma/docker/vendor/procheck/procheck.tar.gz` | `LicenseRef-PROCHECK` | gratuito per uso accademico (R. Laskowski, EBI); oggi è su GitHub senza una licenza dichiarata: condizioni da verificare con l'autore. <https://github.com/RomanLas/PROCHECK> |
| ProSA II 2003 | `makphi`, solo se c'era `programma/docker/vendor/prosa/prosa2003.linux.tar.gz` | `LicenseRef-ProSA` | didattica e ricerca di base, non commerciale; una chiave (`LicenseDB`) per ogni computer; vietato modificare la licenza o la configurazione del computer per aggirarla. Il pacchetto contiene anche Python 2.2, Tcl/Tk 8.4 e BLT 2.4 forniti dagli autori. <https://www.came.sbg.ac.at/prosa_license.html> |
| PSIPRED 3.5 | `makphi-qmean` (immagine ufficiale di QMEAN) | `LicenseRef-PSIPRED` | © 2000 D.T. Jones, tutti i diritti riservati; uso libero per ricerca, con citazione; non in servizi a pagamento senza permesso (termini della licenza di PSIPRED: da verificare per la 3.5). <http://bioinf.cs.ucl.ac.uk/psipred/> |
| SSpro/ACCpro 4 | `makphi-qmean` (immagine ufficiale di QMEAN) | `LicenseRef-SCRATCH` | gratuito per ricerca accademica non commerciale; altri usi su accordo con gli autori (UC Irvine). <https://download.igb.uci.edu> |

- **Pacchetti e chiavi.** I pacchetti di PROCHECK e ProSA (`programma/docker/vendor/`) e le chiavi (`data/licenses/`:
  chiave di MODELLER, `LicenseDB` di ProSA) sono di chi ha ottenuto la licenza: non vanno pubblicati né dati ad altri.
  Sono esclusi da git e dalla copia portatile, che contiene le chiavi solo con `--con-licenze` / `-ConLicenze`.
- **Immagini.** Le immagini, anche i file in `immagini/` della copia portatile, contengono MODELLER e, se costruite con
  i pacchetti, PROCHECK e ProSA già installati (le chiavi no). Servono a chi ha diritto a queste licenze e non vanno
  date ad altri senza il permesso dei titolari: chi vuole MakPhi ottiene programmi e chiavi per conto proprio.
- **ProSA, un computer alla volta.** L'avviatore assegna al container il MAC della licenza solo sul computer a cui è
  legata (`data/licenses/prosa/computer`); su un altro computer serve una chiave per quel computer, oppure la licenza
  va spostata (e il primo computer la perde).

## 2. Immagine makphi

Base: **Ubuntu 24.04 LTS** (immagine ufficiale `ubuntu:24.04`, 24.04.5 del 2026-09-17, fissata per digest:
`ubuntu@sha256:534baea6a22c03a63003dbc8dbe78fe34bc0d7e595d9a9dc9834884ff530eb55`) con Python 3.12 di Ubuntu. La
licenza di ogni pacchetto Ubuntu è nell'immagine in `/usr/share/doc/<pacchetto>/copyright` (testi completi in
`/usr/share/common-licenses/`).

### 2.1 Pacchetti Ubuntu installati dal Dockerfile

| Pacchetto | Versione rilevata | Licenza principale | Sito |
|---|---|---|---|
| `python3` | 3.12.3-0ubuntu2.1 | `PSF-2.0` | <https://www.python.org> |
| `python3-venv` | 3.12.3-0ubuntu2.1 | `PSF-2.0`; porta le ruote di pip 24.0 e setuptools 68.1.2 (`MIT`) | <https://www.python.org> |
| `ca-certificates` | 20260601~24.04.1 | `MPL-2.0` (certificati di Mozilla) e `GPL-2.0-or-later` (script) | <https://launchpad.net/ubuntu/+source/ca-certificates> |
| `wget` | 1.21.4-1ubuntu4.5 | `GPL-3.0-or-later` (con eccezione per OpenSSL) | <https://www.gnu.org/software/wget/> |
| `csh` | 20230828-1 | `BSD-3-Clause` | <https://www.openbsd.org> |
| `dssp` (mkdssp) | 4.2.2-2 | `BSD-2-Clause` | <https://github.com/PDB-REDO/dssp> |
| `ncbi-blast+` | 2.12.0+ds-4build2 | `NCBI-PD` (parti con licenze permissive) | <https://blast.ncbi.nlm.nih.gov> |
| `clustalo` | 1.2.4-8build1 | `GPL-2.0-or-later` | <http://www.clustal.org/omega/> |
| `mafft` | 7.505-1 | `BSD-3-Clause` (senza le estensioni non libere) | <https://mafft.cbrc.jp/alignment/software/> |
| `muscle` | 1:5.1.0-1 | `GPL-3.0-only` | <https://github.com/rcedgar/muscle> |
| `mmseqs2` | 15-6f452+ds-2 | `GPL-3.0-only` (questa versione; le più recenti sono `MIT`) | <https://github.com/soedinglab/MMseqs2> |
| `tm-align` | 20190822+dfsg-2build1 | `LicenseRef-TM-align` (permissiva: uso, modifica e distribuzione con gli avvisi degli autori) | <https://zhanggroup.org/TM-align/> |
| `libc6:i386` | 2.39-0ubuntu8.9 | `LGPL-2.1-or-later` | <https://www.gnu.org/software/libc/> |
| `libstdc++6:i386`, `libgcc-s1:i386`, `libgfortran5` | 14.2.0-4ubuntu2~24.04.1 | `GPL-3.0-or-later WITH GCC-exception-3.1` | <https://gcc.gnu.org> |
| `zlib1g:i386` | 1:1.3.dfsg-3.1ubuntu2.2 | `Zlib` | <https://zlib.net> |
| `libglib2.0-0` (in Ubuntu 24.04 `libglib2.0-0t64`) | 2.80.0-6ubuntu3.9 | `LGPL-2.1-or-later` | <https://gitlab.gnome.org/GNOME/glib> |
| `util-linux` | 2.39.3-9ubuntu6.6 | `GPL-2.0-or-later` (librerie LGPL e BSD) | <https://github.com/util-linux/util-linux> |

Le librerie a 32 bit servono a ProSA II; le stesse librerie a 64 bit ci sono comunque, come dipendenze.

### 2.2 Programmi scaricati o compilati

| Programma | Versione | Licenza | Sito |
|---|---|---|---|
| US-align (compilato dai sorgenti) | 20260527 | `LicenseRef-USalign` (permissiva, stesso testo di TM-align) | <https://github.com/pylelab/USalign> |
| fpocket (compilato dalla release) | 4.2.3 | `MIT`; include Qhull (`Qhull`) e il plugin molfile di VMD (`NCSA`, da verificare) | <https://github.com/Discngine/fpocket> |
| APBS (binario ufficiale, verificato con SHA-256) | 3.4.1 | `BSD-3-Clause`; il binario include FETK (`LGPL-2.1-or-later`) e Geoflow, PB-AM, TABI-PB (licenze da verificare) | <https://github.com/Electrostatics/apbs> |
| MODELLER (pacchetto .deb degli autori) | 10.8 | vedi [§1](#1-software-non-open-source) | <https://salilab.org/modeller/> |
| PROCHECK, ProSA II (solo con i pacchetti in `vendor/`) | 3.5.4, 2003 | vedi [§1](#1-software-non-open-source) | |

### 2.3 Pacchetti Python

Nella venv `/opt/venv`, con i testi delle licenze in `/opt/venv/lib/python3.12/site-packages/*.dist-info/`.

`programma/backend/requirements.lock`:

| Pacchetto | Versione | Licenza | Sito |
|---|---|---|---|
| annotated-doc | 0.0.5 | `MIT` | <https://github.com/fastapi/annotated-doc> |
| annotated-types | 0.8.0 | `MIT` | <https://github.com/annotated-types/annotated-types> |
| anyio | 4.15.1 | `MIT` | <https://github.com/agronholm/anyio> |
| biopython | 1.88 | `LicenseRef-Biopython-License-Agreement` (parti anche `BSD-3-Clause`) | <https://biopython.org> |
| certifi | 2026.7.22 | `MPL-2.0` | <https://github.com/certifi/python-certifi> |
| click | 8.5.0 | `BSD-3-Clause` | <https://click.palletsprojects.com> |
| fastapi | 0.141.1 | `MIT` | <https://github.com/fastapi/fastapi> |
| h11 | 0.16.0 | `MIT` | <https://github.com/python-hyper/h11> |
| httpcore | 1.0.9 | `BSD-3-Clause` | <https://github.com/encode/httpcore> |
| httpx | 0.28.1 | `BSD-3-Clause` | <https://github.com/encode/httpx> |
| idna | 3.20 | `BSD-3-Clause` | <https://github.com/kjd/idna> |
| numpy | 2.5.3 | `BSD-3-Clause AND 0BSD AND MIT AND Zlib AND CC0-1.0`, più le librerie della ruota (sotto) | <https://numpy.org> |
| psutil | 7.2.2 | `BSD-3-Clause` | <https://github.com/giampaolo/psutil> |
| pydantic | 2.13.5 | `MIT` | <https://github.com/pydantic/pydantic> |
| pydantic_core | 2.46.5 | `MIT` | <https://github.com/pydantic/pydantic> |
| python-multipart | 0.0.32 | `Apache-2.0` | <https://github.com/Kludex/python-multipart> |
| starlette | 1.7.0 | `BSD-3-Clause` | <https://github.com/Kludex/starlette> |
| typing-inspection | 0.4.4 | `MIT` | <https://github.com/pydantic/typing-inspection> |
| typing_extensions | 4.16.0 | `PSF-2.0` | <https://github.com/python/typing_extensions> |
| uvicorn | 0.53.0 | `BSD-3-Clause` | <https://uvicorn.dev> |

`programma/backend/requirements-tools.lock` e pip della venv:

| Pacchetto | Versione | Licenza | Sito |
|---|---|---|---|
| charset-normalizer | 3.5.2 | `MIT` | <https://github.com/jawah/charset_normalizer> |
| docutils | 0.17.1 | pubblico dominio; parti `BSD-2-Clause` e licenza Python (`COPYING.txt`) | <https://docutils.sourceforge.io> |
| mmcif-pdbx | 2.1.0 | `CC0-1.0` | <https://github.com/Electrostatics/mmcif_pdbx> |
| openmm | 8.6.1 | `MIT AND LGPL-3.0-or-later` (sotto) | <https://openmm.org> |
| pdb2pqr | 3.7.1 | `BSD-3-Clause` | <https://github.com/Electrostatics/pdb2pqr> |
| pdbfixer | 1.12.0 | `MIT` | <https://github.com/openmm/pdbfixer> |
| propka | 3.5.1 | `LGPL-2.1-only` | <https://github.com/jensengroup/propka> |
| requests | 2.34.2 | `Apache-2.0` | <https://requests.readthedocs.io> |
| urllib3 | 2.8.0 | `MIT` | <https://github.com/urllib3/urllib3> |
| pip (da `python3 -m venv`) | 24.0 | `MIT` (sotto) | <https://pip.pypa.io> |

- La ruota di **numpy** include OpenBLAS (`BSD-3-Clause`), LAPACK (`BSD-3-Clause-Open-MPI`), libgfortran
  (`GPL-3.0-or-later WITH GCC-exception-3.1`) e libquadmath (`LGPL-2.1-or-later`): dettagli in
  `numpy-2.5.3.dist-info/licenses/LICENSE.txt`.
- **OpenMM**: API e piattaforme Reference e CPU `MIT`; i plugin della piattaforma OpenCL, presenti nella ruota ma non
  usati da MakPhi, `LGPL-3.0-or-later`. Licenze ricavate dalle intestazioni dei sorgenti: i metadati della ruota
  indicano «Python Software Foundation License», che non vi corrisponde.
- **pip** è quello di Ubuntu (`python3-pip-whl`) e incorpora librerie con licenze `MIT`, `Apache-2.0`, BSD, `MPL-2.0` e
  `LGPL-2.1-or-later` (chardet).

### 2.4 Altri pacchetti Ubuntu: dipendenze e sistema di base

Altri 80 pacchetti sorgente, raggruppati per licenza principale (eccezioni e parti con altre licenze in
`/usr/share/doc/*/copyright`):

- **GPL** (versione 2 o 3, spesso «o successive»): apt, base-files, base-passwd, bash, coreutils, debianutils,
  diffutils, dpkg, e2fsprogs (librerie anche LGPL e MIT), findutils, gdbm, gnupg2 (`gpgv`), grep, gzip, hostname, mawk,
  netbase, procps (`libproc2`: `LGPL-2.1-or-later`), readline, sed, sensible-utils, sysvinit, tar, ubuntu-keyring,
  unminimize.
- **LGPL** (librerie): acl, argtable2, attr, audit, gnutls28, libassuan, libcap-ng, libgcrypt20, libgpg-error,
  libgzstream, libseccomp, libsemanage, libsepol, libtasn1-6, libxcrypt, npth, systemd (`libsystemd0`, `libudev1`);
  gmp, libidn2, libunistring e nettle a scelta fra `LGPL-3.0-or-later` e `GPL-2.0-or-later`.
- **A scelta fra una licenza permissiva e la GPL**: libcap2 e libzstd (`BSD-3-Clause` o `GPL-2.0-only`), mbedtls
  (`Apache-2.0` o `GPL-2.0-or-later`), pam (`BSD-3-Clause` o GPL), perl (`GPL-1.0-or-later` o `Artistic-1.0-Perl`).
- **Permissive**: bzip2 (`bzip2-1.0.6`), cdebconf e debconf (`BSD-2-Clause`), dash (`BSD-3-Clause`, parti
  `GPL-2.0-or-later`), expat (`MIT`), init-system-helpers (`BSD-3-Clause`), libbsd e libmd (BSD e ISC), libcifpp
  (`BSD-2-Clause`), libffi (`MIT`), libpsl (`MIT`), lmdb (`OLDAP-2.8`), lz4 (`BSD-2-Clause`, programmi
  `GPL-2.0-or-later`), ncurses (`X11`), openssl (`Apache-2.0`), p11-kit (`BSD-3-Clause`), pcre2 (`BSD-3-Clause`),
  python-pip (ruota di pip 24.0, `MIT`), python3.12 (`PSF-2.0`), setuptools (ruota 68.1.2, `MIT`), shadow
  (`BSD-3-Clause`, parti `GPL-2.0-or-later`), xxhash (`BSD-2-Clause`).
- **Pubblico dominio**: libselinux, media-types, ncbi-tools6 (`ncbi-data`), sqlite3, tzdata, xz-utils (`liblzma`).
- **Sleepycat**: db5.3 (Berkeley DB), che chiede di indicare come ottenere il sorgente ([§5](#5-software-gpl-e-lgpl-sorgente-corrispondente)).

### 2.5 Codice e dati di MakPhi nell'immagine

- `/app/backend`, `/app/frontend/dist`, `/entrypoint.sh`: MakPhi (`MIT`); l'interfaccia compilata contiene le librerie
  del [§4](#4-interfaccia-web).
- `/app/backend/makphi/assets/1UBQ.pdb`: struttura PDB 1UBQ (ubiquitina; Vijay-Kumar, Bugg e Cook, 1987) per la prova
  degli strumenti, dati dell'archivio wwPDB (`CC0-1.0`).

### 2.6 Usati solo per costruire (non distribuiti)

- Stadio `tools-build` (Ubuntu 24.04): g++, gcc, gfortran, make, ca-certificates, wget, unzip, per compilare US-align,
  fpocket e PROCHECK. Nell'immagine finale restano i programmi compilati e le librerie di esecuzione di GCC del §2.1.
- Stadio `frontend` (`node:22-slim` fissata per digest,
  `node@sha256:c3de60bf2f9dd0ac6370e6117950ff62d6e339527e7472301c9c78a017978392`, con Node.js 22.23.3 e npm) con le
  dipendenze di sviluppo typescript 5.9.3 (`Apache-2.0`), vite 7.3.6, @vitejs/plugin-react 5.2.0, vitest 3.2.7,
  @types/react e @types/react-dom 19.3.0 (`MIT`). Nell'immagine resta solo l'interfaccia compilata, in cui Vite può
  aggiungere piccole funzioni di supporto (`MIT`).
- Stadio `impronta` (calcolo dell'impronta dei sorgenti) e immagine di prova `makphi-test` di
  `programma/scripts/test.sh` (aggiunge pytest, pytest-timeout e ruff).

## 3. Immagine makphi-qmean

Base: immagine ufficiale di QMEAN del gruppo Schwede (SIB e Biozentrum, Università di Basilea),
`registry.scicore.unibas.ch/schwede/qmean@sha256:640916f8714d79601ce5883f72e725d3bcf1856e930c8cb7ce7d972efe08c7b8`, su
**CentOS Linux 7.9.2009**. MakPhi vi aggiunge solo i propri script in `/qmean/makphi/` (`MIT`). Versioni dalle
variabili e dalle etichette dell'immagine:

| Componente | Versione | Licenza | Sito |
|---|---|---|---|
| QMEAN | 4.3.0 | `Apache-2.0` | <https://git.scicore.unibas.ch/schwede/QMEAN> |
| OpenStructure (con lddt, molck, chemdict_tool) | 2.3.0 | `LGPL-3.0-or-later` | <https://openstructure.org> |
| Libreria dei composti `/qmtl/compounds.chemlib` | dal Chemical Component Dictionary del wwPDB | `CC0-1.0` | <https://www.wwpdb.org/data/ccd> |
| HH-suite (hhblits, hhsearch, ffindex…) | 3.3.0 | `GPL-3.0-or-later` | <https://github.com/soedinglab/hh-suite> |
| BLAST legacy (blastall, blastpgp, makemat…) | 2.2.26 | `NCBI-PD` | <https://ftp.ncbi.nlm.nih.gov/blast/executables/legacy.NOTSUPPORTED/> |
| PSIPRED | 3.5 | `LicenseRef-PSIPRED` ([§1](#1-software-non-open-source)) | <http://bioinf.cs.ucl.ac.uk/psipred/> |
| SSpro/ACCpro | 4 (4.01 secondo il readme, 4.03 secondo l'immagine) | `LicenseRef-SCRATCH` ([§1](#1-software-non-open-source)) | <https://download.igb.uci.edu> |
| Boost | 1.68.0 | `BSL-1.0` | <https://www.boost.org> |
| Eigen (intestazioni) | 3.3.1 | `MPL-2.0` | <https://eigen.tuxfamily.org> |
| CMake | 3.17.3 | `BSD-3-Clause` (incorpora curl, libuv, libarchive, liblzma, rhash, zstd) | <https://cmake.org> |
| Python (pacchetto CentOS) | 3.6.8 | `PSF-2.0` | <https://www.python.org> |
| numpy, scipy | 1.17.4, 1.4.0 | `BSD-3-Clause`; le ruote includono OpenBLAS 0.3.7 (`BSD-3-Clause`) e libgfortran (`GPL-3.0-or-later WITH GCC-exception-3.1`) | <https://numpy.org>, <https://scipy.org> |
| matplotlib | 3.1.2 | `LicenseRef-Matplotlib` (basata sulla licenza PSF) | <https://matplotlib.org> |
| cycler, kiwisolver | 0.11.0, 1.3.1 | `BSD-3-Clause` | <https://github.com/matplotlib/cycler>, <https://github.com/nucleic/kiwi> |
| pyparsing, six | 3.0.6, 1.16.0 | `MIT` | <https://github.com/pyparsing/pyparsing>, <https://github.com/benjaminp/six> |
| python-dateutil | 2.8.2 | `Apache-2.0 AND BSD-3-Clause` | <https://github.com/dateutil/dateutil> |
| pip, setuptools | 21.3.1, 39.2.0 | `MIT` | <https://pip.pypa.io> |

Sistema: 189 pacchetti RPM di CentOS 7 (fra cui glibc anche a 32 bit, compat-libstdc++-33, bash, coreutils, Perl 5.16,
Python 2.7.5 per yum, FFTW 3.3.3, libpng, libtiff, SQLite) con licenze GPL, LGPL, MIT, BSD, MPL e altre: `rpm` le
riporta per ciascun pacchetto (terzo comando in alto); testi in `/usr/share/licenses/` e `/usr/share/doc/`.

## 4. Interfaccia web

Dipendenze di produzione di `programma/frontend/package.json` e quelle che richiedono, alle versioni di
`package-lock.json`, compilate in `/app/frontend/dist`:

| Pacchetto | Versione | Licenza | Copyright | Sito |
|---|---|---|---|---|
| react, react-dom | 19.3.0 | `MIT` | Meta Platforms, Inc. e affiliate | <https://react.dev> |
| scheduler (di react-dom) | 0.28.0 | `MIT` | Meta Platforms, Inc. e affiliate | <https://react.dev> |
| react-router-dom, react-router | 7.18.4 | `MIT` | React Training LLC 2015-2019, Remix Software Inc. 2020-2021 | <https://reactrouter.com> |
| cookie (di react-router) | 1.1.1 | `MIT` | 2012-2014 Roman Shtylman, 2015 Douglas Christopher Wilson | <https://github.com/jshttp/cookie> |
| set-cookie-parser (di react-router) | 2.7.2 | `MIT` | 2015 Nathan Friedly | <https://github.com/nfriedly/set-cookie-parser> |
| 3dmol | 2.5.5 | `BSD-3-Clause` | 2014 University of Pittsburgh e collaboratori; incorpora codice di GLmol (`MIT` o `LGPL-3.0`, qui `MIT`), Three.js e jQuery (`MIT`) e lo shader FXAA di Simon Rodriguez (`MIT`) | <https://3dmol.org> |
| iobuffer (di 3dmol) | 5.4.0 | `MIT` | 2015 Michaël Zasso | <https://github.com/image-js/iobuffer> |
| netcdfjs (di 3dmol) | 3.0.0 | `MIT` | 2016 cheminfo | <https://github.com/cheminfo/netcdfjs> |
| pako (di 3dmol) | 2.2.0 | `MIT AND Zlib` | 2014-2017 Vitaly Puzrin e Andrei Tuputcyn | <https://github.com/nodeca/pako> |
| upng-js (di 3dmol) | 2.1.0 | `MIT` | 2017 Photopea | <https://github.com/photopea/UPNG.js> |
| pako (di upng-js) | 1.0.11 | `MIT AND Zlib` | 2014-2017 Vitaly Puzrin e Andrei Tuputcyn | <https://github.com/nodeca/pako> |
| html-to-image | 1.11.13 | `MIT` | 2017-2025 W.Y. | <https://github.com/bubkoo/html-to-image> |
| @fontsource-variable/inter (font Inter, file .woff2) | 5.3.0 | `OFL-1.1` | 2016 The Inter Project Authors | <https://github.com/rsms/inter>, <https://fontsource.org/fonts/inter> |

L'icona `favicon.svg` e gli stili sono di MakPhi.

## 5. Software GPL e LGPL: sorgente corrispondente

Le immagini contengono software sotto GPL o LGPL (e Berkeley DB, la cui licenza Sleepycat chiede le stesse
indicazioni), in particolare:

- `makphi`: molti pacchetti di Ubuntu (§2.1 e §2.4, fra cui bash, coreutils, glibc, le librerie di esecuzione di GCC,
  Clustal Omega, MUSCLE, MMseqs2, wget, util-linux); PROPKA; i plugin OpenCL di OpenMM; FETK nel binario di APBS;
  libgfortran e libquadmath nella ruota di numpy; chardet in pip.
- `makphi-qmean`: OpenStructure, HH-suite, molti pacchetti di CentOS 7, libgfortran nelle ruote di numpy e scipy.

**Dove si trova il sorgente**, alle versioni contenute nell'immagine:

- Pacchetti Ubuntu: `apt-get source <pacchetto>=<versione>` (con le sorgenti `deb-src` attive) oppure Launchpad, che
  conserva tutte le versioni pubblicate: `https://launchpad.net/ubuntu/+source/<pacchetto>/<versione>`. Nomi e versioni
  dei pacchetti sorgente con il primo comando in alto.
- Pacchetti CentOS 7: archivio <https://vault.centos.org/7.9.2009/> (`os/Source/SPackages/` e
  `updates/Source/SPackages/`); il pacchetto sorgente di ciascuno con il terzo comando in alto.
- Programmi e librerie di MakPhi, alle versioni fissate nel Dockerfile e nei lock: US-align `20260527` (commit
  `177cc8a2bbd3e2a6e9c5faaaa4ff5dfa1e6048f7`) e fpocket `4.2.3` (commit `4bb0d8447f62fee77e2c3c29f54b5fcaf5e2c066`),
  scaricati per commit del tag e verificati con SHA-256, APBS `v3.4.1` con i sottomoduli (fra cui FETK), OpenMM
  `8.6.1` (<https://github.com/openmm/openmm>), PROPKA `v3.5.1` (<https://github.com/jensengroup/propka>), pip 24.0
  (pacchetto sorgente Ubuntu `python-pip`), libgfortran e libquadmath da GCC (<https://gcc.gnu.org/git/>, come indicato
  nel `LICENSE.txt` della ruota di numpy). Le istruzioni di costruzione sono in `programma/docker/Dockerfile`.
- QMEAN: OpenStructure `2.3.0` (<https://git.scicore.unibas.ch/schwede/openstructure>), HH-suite `v3.3.0`
  (<https://github.com/soedinglab/hh-suite>) e QMEAN `4.3.0`, con la ricetta dell'immagine nella cartella `docker/` del
  repository di QMEAN.

**Offerta scritta.** Le immagini Docker di MakPhi passano da una persona all'altra solo come file in `immagini/` di
una copia portatile; chi le costruisce da sé riceve i pacchetti direttamente dagli archivi indicati sopra. Chi dà ad
altri una copia con le immagini, a cominciare da foggy02 per le copie che distribuisce, offre a chiunque ne faccia
richiesta, per almeno tre anni dall'ultima volta in cui distribuisce quella versione, il codice sorgente corrispondente
completo, in forma leggibile da una macchina, del software sotto GPL o LGPL che contiene: gratuitamente da un server,
oppure su un supporto fisico durevole al solo costo ragionevole del supporto e della spedizione. La richiesta va quindi
a chi ti ha dato la copia, indicando la versione di MakPhi e l'identificativo dell'immagine
(`docker image inspect --format '{{.Id}}' makphi:<versione>`).

## 6. Testi delle licenze

- Pacchetti Ubuntu: `/usr/share/doc/<pacchetto>/copyright` e `/usr/share/common-licenses/` nell'immagine `makphi`;
  pacchetti CentOS: `/usr/share/licenses/` e `/usr/share/doc/` in `makphi-qmean`.
- Pacchetti Python: cartelle `*.dist-info/` accanto a ciascun pacchetto.
- Interfaccia web: le librerie `MIT` hanno lo stesso testo di [LICENSE](LICENSE), con i titolari indicati nel
  [§4](#4-interfaccia-web). L'interfaccia compilata non contiene i testi delle licenze: qui sotto quelli di 3Dmol.js
  (con GLmol, Three.js e jQuery) e del font Inter, copiati dai pacchetti.

<details>
<summary>3Dmol.js 2.5.5 (<code>node_modules/3dmol/LICENSE</code>)</summary>

```text
3Dmol.js incorporates code from GLmol, Three.js, and jQuery and
is licensed under a BSD-3-Clause license.

* 3Dmol.js
	Copyright (c) 2014, University of Pittsburgh and contributors
	All rights reserved.

	Redistribution and use in source and binary forms, with or without
	modification, are permitted provided that the following conditions are met:

	1. Redistributions of source code must retain the above copyright
	notice, this list of conditions and the following disclaimer.

	2. Redistributions in binary form must reproduce the above copyright
	notice, this list of conditions and the following disclaimer in the
	documentation and/or other materials provided with the distribution.

	3. Neither the name of the copyright holder nor the names of its
	contributors may be used to endorse or promote products derived from
	this software without specific prior written permission.

	THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS
	"AS IS" AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT
	LIMITED TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A
	PARTICULAR PURPOSE ARE DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT
	HOLDER OR CONTRIBUTORS BE LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL,
	SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED
	TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES; LOSS OF USE, DATA, OR
	PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF
	LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING
	NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE OF THIS
	SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.

* GLmol
 GLmol - Molecular Viewer on WebGL/Javascript (0.47)
  (C) Copyright 2011-2012, biochem_fan
      License: dual license of MIT or LGPL3

  Contributors:
    Robert Hanson for parseXYZ, deferred instantiation



* Three.js
     https://github.com/mrdoob/three.js

     Copyright (c) 2010-2012 three.js Authors. All rights reserved.

     Permission is hereby granted, free of charge, to any person obtaining a copy
     of this software and associated documentation files (the "Software"), to deal
     in the Software without restriction, including without limitation the rights
     to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
     copies of the Software, and to permit persons to whom the Software is
     furnished to do so, subject to the following conditions:

     The above copyright notice and this permission notice shall be included in
     all copies or substantial portions of the Software.

     THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
     IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
     FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
     AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
     LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
     OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
     THE SOFTWARE.

* jQuery
     http://jquery.org/

     Copyright (c) 2011 John Resig

     Permission is hereby granted, free of charge, to any person obtaining
     a copy of this software and associated documentation files (the
     "Software"), to deal in the Software without restriction, including
     without limitation the rights to use, copy, modify, merge, publish,
     distribute, sublicense, and/or sell copies of the Software, and to
     permit persons to whom the Software is furnished to do so, subject to
     the following conditions:

     The above copyright notice and this permission notice shall be
     included in all copies or substantial portions of the Software.

     THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND,
     EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF
     MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND
     NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE
     LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION
     OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION
     WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.

```

</details>

<details>
<summary>Font Inter, @fontsource-variable/inter 5.3.0 (<code>node_modules/@fontsource-variable/inter/LICENSE</code>)</summary>

```text
Copyright 2016 The Inter Project Authors (https://github.com/rsms/inter) Inter-Italic[opsz,wght].ttf: Copyright 2016 The Inter Project Authors (https://github.com/rsms/inter)

This Font Software is licensed under the SIL Open Font License, Version 1.1.
This license is copied below, and is also available with a FAQ at:
http://scripts.sil.org/OFL


-----------------------------------------------------------
SIL OPEN FONT LICENSE Version 1.1 - 26 February 2007
-----------------------------------------------------------

PREAMBLE
The goals of the Open Font License (OFL) are to stimulate worldwide
development of collaborative font projects, to support the font creation
efforts of academic and linguistic communities, and to provide a free and
open framework in which fonts may be shared and improved in partnership
with others.

The OFL allows the licensed fonts to be used, studied, modified and
redistributed freely as long as they are not sold by themselves. The
fonts, including any derivative works, can be bundled, embedded,
redistributed and/or sold with any software provided that any reserved
names are not used by derivative works. The fonts and derivatives,
however, cannot be released under any other type of license. The
requirement for fonts to remain under this license does not apply
to any document created using the fonts or their derivatives.

DEFINITIONS
"Font Software" refers to the set of files released by the Copyright
Holder(s) under this license and clearly marked as such. This may
include source files, build scripts and documentation.

"Reserved Font Name" refers to any names specified as such after the
copyright statement(s).

"Original Version" refers to the collection of Font Software components as
distributed by the Copyright Holder(s).

"Modified Version" refers to any derivative made by adding to, deleting,
or substituting -- in part or in whole -- any of the components of the
Original Version, by changing formats or by porting the Font Software to a
new environment.

"Author" refers to any designer, engineer, programmer, technical
writer or other person who contributed to the Font Software.

PERMISSION & CONDITIONS
Permission is hereby granted, free of charge, to any person obtaining
a copy of the Font Software, to use, study, copy, merge, embed, modify,
redistribute, and sell modified and unmodified copies of the Font
Software, subject to the following conditions:

1) Neither the Font Software nor any of its individual components,
in Original or Modified Versions, may be sold by itself.

2) Original or Modified Versions of the Font Software may be bundled,
redistributed and/or sold with any software, provided that each copy
contains the above copyright notice and this license. These can be
included either as stand-alone text files, human-readable headers or
in the appropriate machine-readable metadata fields within text or
binary files as long as those fields can be easily viewed by the user.

3) No Modified Version of the Font Software may use the Reserved Font
Name(s) unless explicit written permission is granted by the corresponding
Copyright Holder. This restriction only applies to the primary font name as
presented to the users.

4) The name(s) of the Copyright Holder(s) or the Author(s) of the Font
Software shall not be used to promote, endorse or advertise any
Modified Version, except to acknowledge the contribution(s) of the
Copyright Holder(s) and the Author(s) or with their explicit written
permission.

5) The Font Software, modified or unmodified, in part or in whole,
must be distributed entirely under this license, and must not be
distributed under any other license. The requirement for fonts to
remain under this license does not apply to any document created
using the Font Software.

TERMINATION
This license becomes null and void if any of the above conditions are
not met.

DISCLAIMER
THE FONT SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND,
EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO ANY WARRANTIES OF
MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT
OF COPYRIGHT, PATENT, TRADEMARK, OR OTHER RIGHT. IN NO EVENT SHALL THE
COPYRIGHT HOLDER BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY,
INCLUDING ANY GENERAL, SPECIAL, INDIRECT, INCIDENTAL, OR CONSEQUENTIAL
DAMAGES, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING
FROM, OUT OF THE USE OR INABILITY TO USE THE FONT SOFTWARE OR FROM
OTHER DEALINGS IN THE FONT SOFTWARE.
```

</details>
