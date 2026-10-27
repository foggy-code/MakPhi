# Immagine per i test: l'immagine di MakPhi (MODELLER e tutti gli strumenti) più pytest, pytest-timeout, pytest-cov e ruff.
# La costruisce programma/scripts/test.sh; il codice non entra nell'immagine, viene montato a ogni esecuzione.
# pytest-timeout (versione fissata, come in pyproject.toml) applica il limite di [tool.pytest.ini_options];
# pytest-cov (versione fissata) misura la copertura con `test.sh backend --cov`.
ARG BASE=makphi:5.0.0
FROM ${BASE}
RUN /opt/venv/bin/pip install --no-cache-dir --disable-pip-version-check "pytest>=7" "pytest-timeout==2.4.0" "pytest-cov==7.0.0" "ruff>=0.6"
