# syntax=docker/dockerfile:1
# BioLoom v4: l'intero programma in un'immagine Linux con MODELLER e tutti gli strumenti di analisi.
# Si costruisce dall'avviatore (o con `docker compose -f launcher/compose.yml build`) partendo dalla
# cartella principale del progetto.
#
# Strumenti: MODELLER 10.8, DSSP (mkdssp), BLAST+, Clustal Omega, MAFFT, MUSCLE, MMseqs2, TM-align,
# US-align, PROCHECK e ProSA II (se i pacchetti con licenza sono in docker/vendor/).
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

# ------------------------------------------------------------------ compilazione di US-align e PROCHECK
FROM --platform=linux/amd64 ubuntu:24.04 AS tools-build
ARG USALIGN_TAG=20260527
RUN apt-get update && apt-get install -y --no-install-recommends \
        g++ gfortran gcc make ca-certificates wget \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /build
RUN wget -q "https://github.com/pylelab/USalign/archive/refs/tags/${USALIGN_TAG}.tar.gz" -O usalign.tar.gz \
    && tar -xzf usalign.tar.gz && cd "USalign-${USALIGN_TAG}" \
    && g++ -O3 -ffast-math -o /build/USalign USalign.cpp -lm \
    && /build/USalign -h > /dev/null
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
