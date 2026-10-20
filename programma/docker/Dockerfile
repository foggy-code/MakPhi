# syntax=docker/dockerfile:1
# check=skip=FromPlatformFlagConstDisallowed
# BioLoom v4: l'intero programma in un'immagine Linux con MODELLER e tutti gli strumenti di analisi.
# Si costruisce dall'avviatore (o, dalla cartella di BioLoom, con `docker compose -f programma/launcher/compose.yml build`)
# partendo dalla cartella programma/.
#
# Strumenti: MODELLER 10.8, DSSP (mkdssp), BLAST+, Clustal Omega, MAFFT, MUSCLE, MMseqs2, TM-align,
# US-align, fpocket, APBS, PDB2PQR (con PROPKA), OpenMM e PDBFixer, PROCHECK e ProSA II (se i pacchetti con
# licenza sono in docker/vendor/).
# Le chiavi di licenza NON entrano nell'immagine: stanno nella cartella dati (data/licenses).
# Versione: BIOLOOM_VERSION (l'avviatore e compose.yml la passano da programma/launcher/VERSION; il valore predefinito
# qui sotto lo tiene allineato programma/scripts/versione.sh). In /app/IMPRONTA l'impronta dei sorgenti
# (docker/impronta.sh), con cui l'avviatore si accorge che l'immagine non corrisponde più al codice.
# L'immagine è solo linux/amd64 (MODELLER per amd64, ProSA a 32 bit): --platform=linux/amd64 è voluto, per questo
# `docker buildx build --check` salta la regola FromPlatformFlagConstDisallowed (seconda riga).
#
# Costruzione riproducibile: immagini di base fissate per digest e file scaricati verificati con SHA-256 (sha256sum -c
# ferma la costruzione se un file è cambiato). I pacchetti di Ubuntu vengono dall'istantanea dell'archivio alla data di
# UBUNTU_SNAPSHOT (snapshot.ubuntu.com, apt 2.8 di Ubuntu 24.04: APT::Snapshot): due costruzioni con la stessa data
# installano le stesse versioni. Fa eccezione ca-certificates, installato dall'archivio corrente prima di passare
# all'istantanea, che si legge solo in HTTPS (i pacchetti sono comunque firmati dall'archivio). Per aggiornare:
#   - pacchetti di Ubuntu (correzioni di sicurezza): una data più recente in UBUNTU_SNAPSHOT (AAAAMMGGThhmmssZ, UTC)
#   - immagine di base: `docker buildx imagetools inspect ubuntu:24.04` (o node:22-slim) e il "Digest" in cima, quello
#     dell'indice multi-architettura (non quello di una sola piattaforma), in UBUNTU_IMAGE o NODE_IMAGE
#   - MODELLER, APBS: la versione nell'ARG e in *_SHA256 lo `sha256sum` del file scaricato dall'URL del RUN
#   - US-align, fpocket: in *_COMMIT il commit del tag, da
#     `git ls-remote https://github.com/<owner>/<repo> 'refs/tags/<tag>' 'refs/tags/<tag>^{}'`: se c'è la riga che
#     finisce con ^{} il commit è quello (tag annotato: l'altra riga è l'oggetto tag), altrimenti è quello dell'unica
#     riga; in *_SHA256 lo SHA-256 del tar non compresso: `curl -fsSL <URL del RUN> | gzip -dc | sha256sum`
# poi una costruzione completa (e le versioni nuove in THIRD_PARTY_NOTICES.md).

ARG BIOLOOM_VERSION=4.3.0

# Immagini di base per digest dell'indice multi-architettura: node:22-slim (Node.js 22.23.3, Debian 12) e ubuntu:24.04
# (24.04.5 del 2026-09-17). Il tag resta nel commento e non davanti al digest: Podman 2.2.1 (bioloom-podman.sh)
# rifiuta i riferimenti con tag e digest insieme.
ARG NODE_IMAGE=node@sha256:c3de60bf2f9dd0ac6370e6117950ff62d6e339527e7472301c9c78a017978392
ARG UBUNTU_IMAGE=ubuntu@sha256:534baea6a22c03a63003dbc8dbe78fe34bc0d7e595d9a9dc9834884ff530eb55
# Istantanea dell'archivio di Ubuntu da cui si installano i pacchetti (vedi in cima)
ARG UBUNTU_SNAPSHOT=20261015T000000Z
# Piattaforma dell'interfaccia web: BUILDPLATFORM, che BuildKit e Podman (anche 2.2.1) danno da sé; linux/amd64 per
# un costruttore che non la dà. Non un predefinito di BUILDPLATFORM stesso: con BuildKit scavalcherebbe il valore
# automatico.
ARG BUILDPLATFORM
ARG FRONTEND_PLATFORM=${BUILDPLATFORM:-linux/amd64}

# ------------------------------------------------------------------ interfaccia web
# (JavaScript indipendente dalla piattaforma: si compila con l'architettura nativa, anche su Mac ARM)
FROM --platform=$FRONTEND_PLATFORM ${NODE_IMAGE} AS frontend
WORKDIR /src
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

# ------------------------------------------------------------------ compilazione di US-align, fpocket e PROCHECK
FROM --platform=linux/amd64 ${UBUNTU_IMAGE} AS tools-build
ARG UBUNTU_SNAPSHOT
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates \
    && printf 'APT::Snapshot "%s";\n' "${UBUNTU_SNAPSHOT}" > /etc/apt/apt.conf.d/50bioloom-snapshot \
    && rm -rf /var/lib/apt/lists/*
RUN apt-get update && apt-get install -y --no-install-recommends \
        g++ gfortran gcc make ca-certificates wget unzip \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /build
# US-align e fpocket: le release su GitHub non hanno file allegati, solo gli archivi dei sorgenti che GitHub genera
# al volo, senza garantire gli stessi byte del .tar.gz (la compressione è già cambiata, nel 2023). Si scarica il
# commit del tag, che non si sposta, e si verifica il tar non compresso, che git archive produce uguale a parità di
# commit.
# US-align 20260527 (tag) = commit 177cc8a; la versione stampata da USalign -h conferma il tag.
ARG USALIGN_TAG=20260527
ARG USALIGN_COMMIT=177cc8a2bbd3e2a6e9c5faaaa4ff5dfa1e6048f7
ARG USALIGN_SHA256=b75f047b7795ce4eaf63c61c4715229c2bf25010ada59570d87380ca81dda9cd
RUN wget -q "https://github.com/pylelab/USalign/archive/${USALIGN_COMMIT}.tar.gz" -O usalign.tar.gz \
    && gzip -dc usalign.tar.gz > usalign.tar && rm -f usalign.tar.gz \
    && echo "${USALIGN_SHA256}  usalign.tar" | sha256sum -c - \
    && tar -xf usalign.tar && rm -f usalign.tar && cd "USalign-${USALIGN_COMMIT}" \
    && g++ -O3 -ffast-math -o /build/USalign USalign.cpp -lm \
    && /build/USalign -h | grep -q "(Version ${USALIGN_TAG})"
# fpocket (tasche; Le Guilloux et al. 2009, licenza MIT): non è fra i pacchetti di Ubuntu 24.04, si compila dalla
# release ufficiale, 4.2.3 (tag) = commit 4bb0d84. Senza la profilazione -pg del makefile, che scriverebbe gmon.out
# nella cartella di ogni calcolo; niente cartella data/ (esempi e dizionario mmCIF: BioLoom passa a fpocket un PDB).
ARG FPOCKET_COMMIT=4bb0d8447f62fee77e2c3c29f54b5fcaf5e2c066
ARG FPOCKET_SHA256=27d87a8aa2cf44b371c854cf7676e3db6af18ebc34be6e685fcbd995953488d4
RUN wget -q "https://github.com/Discngine/fpocket/archive/${FPOCKET_COMMIT}.tar.gz" -O fpocket.tar.gz \
    && gzip -dc fpocket.tar.gz > fpocket.tar && rm -f fpocket.tar.gz \
    && echo "${FPOCKET_SHA256}  fpocket.tar" | sha256sum -c - \
    && tar -xf fpocket.tar --exclude="fpocket-${FPOCKET_COMMIT}/data" && rm -f fpocket.tar \
    && cd "fpocket-${FPOCKET_COMMIT}" && sed -i 's/ -pg//g' makefile \
    && make qhull && make bin/fpocket \
    && cp bin/fpocket /build/fpocket && cd /build && rm -rf "fpocket-${FPOCKET_COMMIT}" && test -x /build/fpocket
# APBS (Poisson-Boltzmann; Jurrus et al. 2018, licenza BSD): binario ufficiale della release, verificato con
# SHA-256. Il pacchetto apbs di Ubuntu 24.04 (stessa versione 3.4.1) porterebbe circa 340 MB di dipendenze
# (OpenMPI con le librerie ROCm/LLVM, MathJax); il binario ufficiale (30 MB) usa solo libc e libstdc++.
ARG APBS_VERSION=3.4.1
ARG APBS_SHA256=750f6a2df7b5a82b69be5c4cb192115c02fa261d0ff52cf1d9632ed1eda8b4f0
RUN wget -q "https://github.com/Electrostatics/apbs/releases/download/v${APBS_VERSION}/APBS-${APBS_VERSION}.Linux.zip" \
         -O apbs.zip \
    && echo "${APBS_SHA256}  apbs.zip" | sha256sum -c - \
    && unzip -q -j apbs.zip "APBS-${APBS_VERSION}.Linux/bin/apbs" -d /build/apbs-bin && rm -f apbs.zip \
    && chmod 755 /build/apbs-bin/apbs && test -x /build/apbs-bin/apbs
COPY docker/vendor/ /build/vendor/
RUN mkdir -p /opt/procheck \
    && if [ -f /build/vendor/procheck/procheck.tar.gz ]; then \
         tar -xzf /build/vendor/procheck/procheck.tar.gz -C /build \
         && cd /build/procheck \
         && make F77=gfortran CC=gcc FOPTS="-O2 -std=legacy -fno-automatic -w" COPTS="-O2" CLIBS=-lm \
         && for exe in anglen tplot pplot bplot clean secstr nb wirplot; do test -x "$exe" || exit 1; done \
         && cp -a /build/procheck/. /opt/procheck/ \
         && rm -f /opt/procheck/*.o /opt/procheck/*.f /opt/procheck/*.for; \
       fi

# ------------------------------------------------------------------ impronta dei sorgenti
# Stessa definizione dell'avviatore (docker/impronta.sh), calcolata qui così c'è comunque si costruisca l'immagine
# (avviatore, compose a mano, CI). Quello che .dockerignore esclude (node_modules, __pycache__, frontend/dist...)
# la definizione lo esclude comunque. Lo script si legge senza CR, come entrypoint.sh, se arriva con fine riga CRLF.
FROM --platform=linux/amd64 ${UBUNTU_IMAGE} AS impronta
COPY . /ctx
RUN tr -d '\r' < /ctx/docker/impronta.sh > /tmp/impronta.sh && sh /tmp/impronta.sh /ctx > /impronta

# ------------------------------------------------------------------ immagine finale
FROM --platform=linux/amd64 ${UBUNTU_IMAGE}
ARG BIOLOOM_VERSION
ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    BIOLOOM_CONTAINER=1 \
    BIOLOOM_IMAGE_VERSION=${BIOLOOM_VERSION} \
    BIOLOOM_DATA_DIR=/data \
    BIOLOOM_SCRATCH_DIR=/scratch \
    BIOLOOM_CACHE_DIR=/cache \
    BIOLOOM_HOST=0.0.0.0 \
    BIOLOOM_PORT=8765 \
    prodir=/opt/procheck \
    PATH=/opt/venv/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:/opt/procheck:/opt/prosa/bin \
    USER=bioloom \
    LOGNAME=bioloom

# pacchetti dall'istantanea di UBUNTU_SNAPSHOT (la configurazione resta nell'immagine e vale anche per MODELLER)
ARG UBUNTU_SNAPSHOT
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates \
    && printf 'APT::Snapshot "%s";\n' "${UBUNTU_SNAPSHOT}" > /etc/apt/apt.conf.d/50bioloom-snapshot \
    && rm -rf /var/lib/apt/lists/*
# ProSA II 2003 è un programma Linux a 32 bit: servono le librerie i386.
RUN dpkg --add-architecture i386 && apt-get update && apt-get install -y --no-install-recommends \
        python3 python3-venv ca-certificates wget csh \
        dssp ncbi-blast+ clustalo mafft muscle mmseqs2 tm-align \
        libc6:i386 libstdc++6:i386 libgcc-s1:i386 zlib1g:i386 libgfortran5 \
        libglib2.0-0 util-linux \
    && rm -rf /var/lib/apt/lists/*

# MODELLER: .deb ufficiale verificato con SHA-256. La chiave fittizia serve solo all'installazione; quella vera arriva
# da BioLoom a ogni calcolo. apt-get install del file (non dpkg -i) porta anche le dipendenze che il pacchetto dovesse
# dichiarare: per questo rilegge le liste dei pacchetti, cancellate dal livello precedente, e poi le ripulisce.
ARG MODELLER_VERSION=10.8
ARG MODELLER_SHA256=87d3d8edbf0035ee0d625999f64baa172c268c4abbaa181a65b6c96ffb470899
RUN wget -q "https://salilab.org/modeller/${MODELLER_VERSION}/modeller_${MODELLER_VERSION}-1_amd64.deb" -O /tmp/modeller.deb \
    && echo "${MODELLER_SHA256}  /tmp/modeller.deb" | sha256sum -c - \
    && apt-get update \
    && KEY_MODELLER=XXXX apt-get install -y --no-install-recommends /tmp/modeller.deb \
    && rm -rf /var/lib/apt/lists/* /tmp/modeller.deb

COPY --from=tools-build /build/USalign /usr/local/bin/USalign
COPY --from=tools-build /build/fpocket /usr/local/bin/fpocket
COPY --from=tools-build /build/apbs-bin/apbs /usr/local/bin/apbs
COPY --from=tools-build /opt/procheck /opt/procheck
COPY docker/vendor/prosa/ /tmp/prosa/
RUN if [ -f /tmp/prosa/prosa2003.linux.tar.gz ]; then \
         mkdir -p /opt/prosa/pdb /opt/prosa/bbn \
         && tar -xzf /tmp/prosa/prosa2003.linux.tar.gz -C /tmp/prosa \
         && tar -xf /tmp/prosa/prosalinux.tar -C /opt/prosa \
         && S=/opt/prosa/ProSaData/prosa-startup \
         && printf '%s\n' "factor_pair = 1.0" "factor_surf = 5.0" "lower_k = 1" "upper_k = 600" "pot_ub = 15.0" \
              "bbn_dir = /opt/prosa/bbn" "pdb_dir = /opt/prosa/pdb" \
              "pair potential /opt/prosa/ProSaData/pII3.0.pair-cb pcb" \
              "surface potential /opt/prosa/ProSaData/pII3.0.surf-cb scb" "print_level = 1" > "$S" \
         && ln -sf /data/licenses/prosa/LicenseDB /opt/prosa/ProSaData/LicenseDB \
         && chmod -R a+rX /opt/prosa && chmod +x /opt/prosa/bin/*; \
       fi \
    && rm -rf /tmp/prosa \
    && ln -sf /usr/bin/TMalign /usr/local/bin/tmalign

WORKDIR /app
COPY backend/requirements.lock backend/requirements.lock
RUN python3 -m venv --system-site-packages /opt/venv \
    && /opt/venv/bin/pip install --no-cache-dir --disable-pip-version-check -r backend/requirements.lock \
    && python3 -c "import modeller" 2>&1 | grep -q "license" || python3 -c "import modeller"
# Strumenti di calcolo in Python (PDB2PQR con PROPKA, OpenMM, PDBFixer) nella stessa venv, in un livello a parte:
# versioni fissate in requirements-tools.lock, `pip check` fa fallire la costruzione se manca una dipendenza.
COPY backend/requirements-tools.lock backend/requirements-tools.lock
RUN /opt/venv/bin/pip install --no-cache-dir --disable-pip-version-check -r backend/requirements-tools.lock \
    && /opt/venv/bin/pip check \
    && /opt/venv/bin/python -c "import openmm, pdbfixer, propka, pdb2pqr; print('OpenMM', openmm.__version__)" \
    && /opt/venv/bin/pdb2pqr --help > /dev/null
COPY backend/ backend/
COPY --from=frontend /src/dist frontend/dist
COPY docker/entrypoint.sh /entrypoint.sh
RUN sed -i 's/\r$//' /entrypoint.sh && chmod 755 /entrypoint.sh \
    && useradd -m -u 1000 -o bioloom 2>/dev/null || true \
    && mkdir -p /data /scratch /cache && chmod -R a+rX /app

# file temporanei e HOME nello scratch (creati dall'entrypoint a ogni avvio)
ENV TMPDIR=/scratch/tmp \
    HOME=/scratch/home \
    PYTHONPATH=/app/backend
WORKDIR /app/backend

# Impronta e versione per ultime: l'impronta cambia a ogni modifica del codice, i livelli sopra restano in cache.
COPY --from=impronta /impronta /app/IMPRONTA
LABEL bioloom.version=${BIOLOOM_VERSION}

EXPOSE 8765
VOLUME ["/scratch", "/cache"]
HEALTHCHECK --interval=10s --timeout=5s --start-period=30s --retries=6 \
    CMD python3 -c "import urllib.request,sys; urllib.request.urlopen('http://127.0.0.1:8765/api/health', timeout=4)" || exit 1
ENTRYPOINT ["/entrypoint.sh"]
CMD ["python3", "-m", "bioloom", "--no-browser"]
