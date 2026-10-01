# syntax=docker/dockerfile:1
# BioLoom v4: l'intero programma in un'immagine Linux con MODELLER e tutti gli strumenti di analisi.
# Si costruisce dall'avviatore (o con `docker compose -f launcher/compose.yml build`) partendo dalla
# cartella principale del progetto.
#
# Strumenti: MODELLER 10.8, DSSP (mkdssp), BLAST+, Clustal Omega, MAFFT, MUSCLE, MMseqs2, TM-align,
# US-align, fpocket, APBS, PDB2PQR (con PROPKA), OpenMM e PDBFixer, PROCHECK e ProSA II (se i pacchetti con
# licenza sono in docker/vendor/).
# Le chiavi di licenza NON entrano nell'immagine: stanno nella cartella dati (data/licenses).

ARG BIOLOOM_VERSION=4.1.0

# ------------------------------------------------------------------ interfaccia web
# (JavaScript indipendente dalla piattaforma: si compila con l'architettura nativa, anche su Mac ARM)
FROM --platform=$BUILDPLATFORM node:22-slim AS frontend
WORKDIR /src
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

# ------------------------------------------------------------------ compilazione di US-align, fpocket e PROCHECK
FROM --platform=linux/amd64 ubuntu:24.04 AS tools-build
ARG USALIGN_TAG=20260527
RUN apt-get update && apt-get install -y --no-install-recommends \
        g++ gfortran gcc make ca-certificates wget unzip \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /build
RUN wget -q "https://github.com/pylelab/USalign/archive/refs/tags/${USALIGN_TAG}.tar.gz" -O usalign.tar.gz \
    && tar -xzf usalign.tar.gz && cd "USalign-${USALIGN_TAG}" \
    && g++ -O3 -ffast-math -o /build/USalign USalign.cpp -lm \
    && /build/USalign -h > /dev/null
# fpocket (tasche; Le Guilloux et al. 2009, licenza MIT): non è fra i pacchetti di Ubuntu 24.04, si compila dalla
# release ufficiale. Senza la profilazione -pg del makefile, che scriverebbe gmon.out nella cartella di ogni calcolo;
# niente cartella data/ (esempi e dizionario mmCIF: BioLoom passa a fpocket un PDB).
ARG FPOCKET_TAG=4.2.3
RUN wget -q "https://github.com/Discngine/fpocket/archive/refs/tags/${FPOCKET_TAG}.tar.gz" -O fpocket.tar.gz \
    && tar -xzf fpocket.tar.gz --exclude="fpocket-${FPOCKET_TAG}/data" && rm -f fpocket.tar.gz \
    && cd "fpocket-${FPOCKET_TAG}" && sed -i 's/ -pg//g' makefile \
    && make qhull && make bin/fpocket \
    && cp bin/fpocket /build/fpocket && cd /build && rm -rf "fpocket-${FPOCKET_TAG}" && test -x /build/fpocket
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

# ------------------------------------------------------------------ immagine finale
FROM --platform=linux/amd64 ubuntu:24.04
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

# ProSA II 2003 è un programma Linux a 32 bit: servono le librerie i386.
RUN dpkg --add-architecture i386 && apt-get update && apt-get install -y --no-install-recommends \
        python3 python3-venv ca-certificates wget csh \
        dssp ncbi-blast+ clustalo mafft muscle mmseqs2 tm-align \
        libc6:i386 libstdc++6:i386 libgcc-s1:i386 zlib1g:i386 libgfortran5 \
        libglib2.0-0 util-linux \
    && rm -rf /var/lib/apt/lists/*

# MODELLER: la chiave fittizia serve solo all'installazione; quella vera arriva da BioLoom a ogni calcolo.
ARG MODELLER_VERSION=10.8
RUN wget -q "https://salilab.org/modeller/${MODELLER_VERSION}/modeller_${MODELLER_VERSION}-1_amd64.deb" -O /tmp/modeller.deb \
    && (KEY_MODELLER=XXXX dpkg -i /tmp/modeller.deb || apt-get -f install -y) \
    && rm -f /tmp/modeller.deb

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

EXPOSE 8765
VOLUME ["/scratch", "/cache"]
HEALTHCHECK --interval=10s --timeout=5s --start-period=30s --retries=6 \
    CMD python3 -c "import urllib.request,sys; urllib.request.urlopen('http://127.0.0.1:8765/api/health', timeout=4)" || exit 1
ENTRYPOINT ["/entrypoint.sh"]
CMD ["python3", "-m", "bioloom", "--no-browser"]
