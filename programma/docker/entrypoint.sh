#!/bin/sh
# Avvio del container MakPhi.
# - Lo scratch (volume Docker) contiene solo lavoro temporaneo: viene svuotato a ogni avvio.
# - /cache (volume Docker) contiene dati ricostruibili locali a questo computer (indice MMseqs2).
# - Il programma gira con l'utente proprietario della cartella dati montata su /data (così su Linux i
#   file creati sulla chiavetta o nella cartella del server restano dell'utente); se la cartella
#   appartiene a root (Docker Desktop su Windows/macOS) si usa l'utente 1000. Con Podman senza root
#   l'avviatore passa MAKPHI_UID=0: si resta root nel container, che sul server è l'utente stesso.
set -e

if [ "$(id -u)" = "0" ]; then
    uid="${MAKPHI_UID:-$(stat -c %u /data 2>/dev/null || echo 1000)}"
    gid="${MAKPHI_GID:-$(stat -c %g /data 2>/dev/null || echo 1000)}"
    # MAKPHI_UID=0 esplicito (Podman senza root): il root del container è già l'utente che lo ha avviato
    if [ "$uid" = "0" ] && [ "${MAKPHI_UID:-}" != "0" ]; then
        uid=1000
        gid=1000
        # cartella dati appena creata da Docker e vuota: la si assegna all'utente del programma
        if [ -z "$(ls -A /data 2>/dev/null)" ]; then chown 1000:1000 /data 2>/dev/null || true; fi
    fi
    find /scratch -mindepth 1 -maxdepth 1 -exec rm -rf {} + 2>/dev/null || true
    mkdir -p /scratch/tmp /scratch/jobs /scratch/home /cache
    chown "$uid:$gid" /scratch /scratch/tmp /scratch/jobs /scratch/home
    chown -R "$uid:$gid" /cache
    if ! setpriv --reuid="$uid" --regid="$gid" --clear-groups test -w /data; then
        echo "[MakPhi] ATTENZIONE: la cartella dati non è scrivibile dall'utente $uid." >&2
    fi
    exec setpriv --reuid="$uid" --regid="$gid" --clear-groups "$@"
fi
mkdir -p /scratch/tmp /scratch/jobs /scratch/home 2>/dev/null || true
exec "$@"
