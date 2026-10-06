#!/bin/bash
# Avvio di BioLoom su Linux (serve Docker). Arresto: ./avvia-bioloom.sh arresta
# Su un server: ./avvia-bioloom.sh server
cd "$(dirname "$0")" || exit 1
exec bash programma/launcher/bioloom.sh "$@"
