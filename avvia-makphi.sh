#!/bin/bash
# Avvio di MakPhi su Linux (serve Docker). Arresto: ./avvia-makphi.sh arresta
# Su un server: ./avvia-makphi.sh server
cd "$(dirname "$0")" || exit 1
exec bash programma/launcher/makphi.sh "$@"
