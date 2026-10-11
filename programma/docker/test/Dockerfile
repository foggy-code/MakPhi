# Immagine per i test: l'immagine di BioLoom (MODELLER e tutti gli strumenti) più pytest, pytest-timeout e ruff.
# La costruisce programma/scripts/test.sh; il codice non entra nell'immagine, viene montato a ogni esecuzione.
# pytest-timeout (versione fissata, come in pyproject.toml) applica il limite di [tool.pytest.ini_options].
ARG BASE=bioloom:4.2.0
FROM ${BASE}
RUN /opt/venv/bin/pip install --no-cache-dir --disable-pip-version-check "pytest>=7" "pytest-timeout==2.4.0" "ruff>=0.6"
