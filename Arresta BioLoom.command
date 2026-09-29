#!/bin/bash
# Arresto di BioLoom su macOS: i dati restano nella cartella data.
cd "$(dirname "$0")" || exit 1
bash launcher/bioloom.sh arresta
sleep 3
