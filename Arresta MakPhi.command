#!/bin/bash
# Arresto di MakPhi su macOS: i dati restano nella cartella data.
cd "$(dirname "$0")" || exit 1
bash programma/launcher/makphi.sh arresta
sleep 3
