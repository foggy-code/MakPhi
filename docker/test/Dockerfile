# Immagine per i test: l'immagine di BioLoom (MODELLER e tutti gli strumenti) più pytest e ruff.
# La costruisce scripts/test.sh; il codice non entra nell'immagine, viene montato a ogni esecuzione.
ARG BASE=bioloom:4.1.0
FROM ${BASE}
RUN /opt/venv/bin/pip install --no-cache-dir --disable-pip-version-check "pytest>=7" "ruff>=0.6"
