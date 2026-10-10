#!/bin/bash
# Avvio di BioLoom su macOS: doppio clic. Serve Docker (Docker Desktop o un'alternativa con CLI docker e compose: Guida 1).
cd "$(dirname "$0")" || exit 1
bash programma/launcher/bioloom.sh "$@"
status=$?
[ $status -ne 0 ] && read -r -p "Premi Invio per chiudere..." _
exit $status
