#!/bin/bash
# Avvio di BioLoom su macOS: doppio clic (serve Docker Desktop).
cd "$(dirname "$0")" || exit 1
bash programma/launcher/bioloom.sh "$@"
status=$?
[ $status -ne 0 ] && read -r -p "Premi Invio per chiudere..." _
exit $status
