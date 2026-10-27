#!/usr/bin/env bash
# Misure ripetute dei tempi di costruzione (scripts/benchmark_tempi.py) e di QMEAN (scripts/benchmark_qmean.py), una
# misura per container, per la stima dei controlli preventivi (domain/preflight.py). Gira con Docker o con Podman
# (anche 2.x senza root, come sul server: profilo seccomp dell'avviatore se c'è).
#
#   bash programma/scripts/misure_tempi.sh server     griglia completa: 100–5000 residui, 1 e 2 modelli, REPS ripetizioni;
#                                                    modalità parallela a 300 e 1000 residui; poi QMEAN 1/8/16/32
#   bash programma/scripts/misure_tempi.sh comune     serie comune al PC e al server: 100, 300, 600, 1000 residui
#
# Variabili: IMAGE (immagine con gli strumenti, predefinita makphi:<VERSION>), CODE (cartella programma/ da montare,
# predefinita questa), DATA (cartella dati: licenses/modeller.key e templates/3IU0.pdb), OUT (risultati, predefinita
# bench_out/tempi/<serie>), JOBS (misure contemporanee, predefinito 1: sul server 24), REPS (ripetizioni, 3),
# HOST_LABEL (etichetta della macchina nei risultati), PODMAN (docker o podman), QMEAN_LEVELS (1,8,16,32; vuoto = no).
# Una misura già presente in OUT (JSON non vuoto) non si ripete: lo script si può rilanciare dopo un'interruzione.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SERIE="${1:-comune}"
case "$SERIE" in server|comune) ;; *) echo "uso: bash misure_tempi.sh [server|comune]" >&2; exit 2 ;; esac
PODMAN="${PODMAN:-$(command -v podman >/dev/null 2>&1 && echo podman || echo docker)}"
CODE="${CODE:-$ROOT}"
DATA="${DATA:-$ROOT/../data}"
OUT="${OUT:-$ROOT/../bench_out/tempi/$SERIE}"
JOBS="${JOBS:-1}"
REPS="${REPS:-3}"
HOST_LABEL="${HOST_LABEL:-$(hostname)}"
IMAGE="${IMAGE:-makphi:$(head -n1 "$ROOT/launcher/VERSION" | tr -d '[:space:]')}"
QMEAN_LEVELS="${QMEAN_LEVELS-1,8,16,32}"
mkdir -p "$OUT"
KEY="$(tr -d '\r\n' < "$DATA/licenses/modeller.key")"
export MSYS_NO_PATHCONV=1
host_path() { if command -v cygpath >/dev/null 2>&1; then cygpath -m "$1"; else printf '%s' "$1"; fi; }

OPTS=(--rm --init -e "MODELLER_KEY=$KEY" -e PYTHONPATH=/src/backend -e PYTHONDONTWRITEBYTECODE=1
      -v "$(host_path "$CODE"):/src:ro" -v "$(host_path "$DATA/templates"):/hostdata/templates:ro"
      -v "$(host_path "$OUT"):/out")
if [ "$PODMAN" = podman ]; then
  OPTS+=(--security-opt label=disable)
  for f in "$DATA/../seccomp-podman.json" "$HOME/makphi/seccomp-podman.json"; do
    if [ -f "$f" ]; then OPTS+=(--security-opt "seccomp=$f"); break; fi
  done
fi

# una misura: nome del risultato, poi gli argomenti di benchmark_tempi.py
misura() {
  local name="$1"; shift
  [ -s "$OUT/$name.json" ] && return 0
  "$PODMAN" run "${OPTS[@]}" --entrypoint sh "$IMAGE" \
    -c 'mkdir -p /scratch/tmp /scratch/home && exec python /src/scripts/benchmark_tempi.py "$@"' sh "$@" \
    --template-file /hostdata/templates/3IU0.pdb --host-label "$HOST_LABEL" --out "/out/$name.json" \
    > "$OUT/$name.log" 2>&1 || echo "misura $name non riuscita (vedi $OUT/$name.log)" >&2
}

# al più JOBS misure insieme (wait -n: bash 4.3 o successivo)
in_coda() {
  while [ "$(jobs -rp | wc -l)" -ge "$JOBS" ]; do wait -n || true; done
  misura "$@" &
}

if [ "$SERIE" = server ]; then
  LUNGHEZZE="5000 4000 3000 2000 1500 1000 800 600 400 300 200 100"   # le più lunghe prima: finiscono per ultime
else
  LUNGHEZZE="1000 600 300 100"
fi
echo "== $SERIE su $HOST_LABEL: $IMAGE, $JOBS insieme, $REPS ripetizioni, risultati in $OUT ($(date '+%F %T'))"
for L in $LUNGHEZZE; do
  for m in 2 1; do
    for r in $(seq 1 "$REPS"); do in_coda "L${L}_m${m}_serie_r$r" --length "$L" --models "$m" --rep "$r"; done
  done
done
# avvio dei worker della modalità parallela (T_WORKERS): stessi modelli in parallelo con m worker
PARALLELE="300:2 300:4"
[ "$SERIE" = server ] && PARALLELE="300:2 300:4 300:8 1000:4"
for spec in $PARALLELE; do
  L="${spec%%:*}"; m="${spec##*:}"
  for r in $(seq 1 "$REPS"); do
    in_coda "L${L}_m${m}_parallelo_r$r" --length "$L" --models "$m" --parallel --workers "$m" --rep "$r"
  done
done
wait
echo "== tempi finiti ($(date '+%F %T'))"

if [ "$SERIE" = server ] && [ -n "$QMEAN_LEVELS" ] && [ ! -s "$OUT/qmean.json" ]; then
  # nella rete del container makphi, dove risponde il worker QMEAN (come per MakPhi)
  "$PODMAN" run "${OPTS[@]}" --network container:makphi --entrypoint sh "$IMAGE" \
    -c 'mkdir -p /scratch/tmp /scratch/home && exec python /src/scripts/benchmark_qmean.py "$@"' sh \
    --levels "$QMEAN_LEVELS" --length 300 --template-file /hostdata/templates/3IU0.pdb --host-label "$HOST_LABEL" \
    --out /out/qmean.json > "$OUT/qmean.log" 2>&1 || echo "misura QMEAN non riuscita (vedi $OUT/qmean.log)" >&2
  echo "== QMEAN finito ($(date '+%F %T'))"
fi
