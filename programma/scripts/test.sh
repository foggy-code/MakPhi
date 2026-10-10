#!/usr/bin/env bash
# Test di BioLoom v4 durante lo sviluppo, con gli stessi strumenti del programma.
#
#   programma/scripts/test.sh                 lint + test del backend (nel container) + test del frontend
#   programma/scripts/test.sh backend [args]  solo backend; gli argomenti vanno a pytest (es. -k pipeline -x)
#   programma/scripts/test.sh frontend        solo frontend (eslint + typecheck + vitest)
#   programma/scripts/test.sh lint            solo ruff
#
# Il backend gira in "bioloom-test:<base>", costruita sopra l'immagine locale "bioloom:<versione>"
# (versione da programma/launcher/VERSION, come l'avviatore; un'altra con BIOLOOM_BASE_IMAGE, es. un'immagine di
# sviluppo con strumenti nuovi)
# con pytest, pytest-timeout e ruff (si ricostruisce quando cambia l'immagine di base o docker/test/Dockerfile);
# il codice del repository è montato, non copiato.
# MODELLER: chiave da $MODELLER_KEY oppure da <cartella dati>/licenses/modeller.key
# (cartella dati: $BIOLOOM_DATA_PATH, predefinita la cartella data accanto a programma/).
# Senza chiave i test di MODELLER vengono saltati.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BASE_IMAGE="${BIOLOOM_BASE_IMAGE:-}"
if [ -z "$BASE_IMAGE" ]; then
  # la versione viene solo da programma/launcher/VERSION (programma/scripts/versione.sh), senza numeri di riserva
  VERSION="$(head -n1 "$ROOT/launcher/VERSION" 2>/dev/null | tr -d '[:space:]')" || VERSION=""
  if [ -z "$VERSION" ]; then
    echo "Manca programma/launcher/VERSION (la versione di BioLoom): non so quale immagine bioloom:<versione> usare." >&2
    echo "  Ripristina il file (es. git checkout -- programma/launcher/VERSION) oppure indica BIOLOOM_BASE_IMAGE." >&2
    exit 2
  fi
  BASE_IMAGE="bioloom:$VERSION"
fi
# un'immagine di test per ogni immagine di base: più worktree con basi diverse non si sovrascrivono a vicenda
TEST_IMAGE="${BIOLOOM_TEST_IMAGE:-bioloom-test:$(printf %s "$BASE_IMAGE" | tr -c 'A-Za-z0-9._-' '-')}"
DATA_PATH="${BIOLOOM_DATA_PATH:-$ROOT/../data}"

# Percorso per `docker -v` (su Windows con Git Bash serve la forma D:/...).
host_path() { if command -v cygpath >/dev/null 2>&1; then cygpath -m "$1"; else printf '%s' "$1"; fi; }
export MSYS_NO_PATHCONV=1

ensure_image() {
  if ! docker image inspect "$BASE_IMAGE" >/dev/null 2>&1; then
    echo "Immagine $BASE_IMAGE assente: avvia BioLoom una volta (la costruisce) oppure" >&2
    echo "  docker compose -f programma/launcher/compose.yml build" >&2
    exit 2
  fi
  local base_id want stamp
  base_id="$(docker image inspect -f '{{.Id}}' "$BASE_IMAGE")"
  # etichetta: immagine di base e somma di docker/test/Dockerfile (senza CR: uguale da Windows e da Linux)
  want="$base_id $(tr -d '\r' < "$ROOT/docker/test/Dockerfile" | cksum | awk '{ print $1 }')"
  stamp="$(docker image inspect -f '{{index .Config.Labels "bioloom.test.base"}}' "$TEST_IMAGE" 2>/dev/null || true)"
  if [ "$stamp" != "$want" ]; then
    echo "== costruzione di $TEST_IMAGE"
    docker build -q --build-arg "BASE=$BASE_IMAGE" --label "bioloom.test.base=$want" \
      -t "$TEST_IMAGE" "$(host_path "$ROOT/docker/test")" >/dev/null
  fi
}

in_container() {
  local key="${MODELLER_KEY:-}"
  if [ -z "$key" ] && [ -f "$DATA_PATH/licenses/modeller.key" ]; then
    key="$(tr -d '\r\n' < "$DATA_PATH/licenses/modeller.key")"
  fi
  docker run --rm --init -e MODELLER_KEY="$key" -e PYTHONPATH=/src/backend -e PYTHONDONTWRITEBYTECODE=1 \
    -v "$(host_path "$ROOT"):/src" -w /src/backend --entrypoint sh "$TEST_IMAGE" \
    -c 'mkdir -p /scratch/tmp /scratch/home && exec "$@"' sh "$@"
}

lint() {
  echo "== ruff"
  in_container ruff check --no-cache --config /src/backend/pyproject.toml /src/backend /src/docker/qmean
}

backend() {
  echo "== pytest"
  in_container python3 -m pytest -q -p no:cacheprovider "$@"
}

frontend() {
  echo "== frontend"
  cd "$ROOT/frontend"
  # reinstalla anche quando package-lock.json è più recente dell'ultima installazione (npm 7+ riscrive
  # node_modules/.package-lock.json a ogni installazione): una node_modules di prima può non avere eslint
  if [ ! -d node_modules ] || [ package-lock.json -nt node_modules/.package-lock.json ]; then
    echo "== npm ci (node_modules assente o più vecchia di package-lock.json)"
    npm ci --no-audit --no-fund
  fi
  npm run --silent lint
  npm run --silent typecheck
  npm test --silent
}

cmd="${1:-all}"
[ $# -gt 0 ] && shift
case "$cmd" in
  all) ensure_image; lint; backend "$@"; frontend ;;
  backend) ensure_image; backend "$@" ;;
  lint) ensure_image; lint ;;
  frontend) frontend ;;
  *) echo "uso: programma/scripts/test.sh [all|backend|frontend|lint] [argomenti di pytest]" >&2; exit 2 ;;
esac
