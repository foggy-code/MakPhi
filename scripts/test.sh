#!/usr/bin/env bash
# Test di BioLoom v4 durante lo sviluppo, con gli stessi strumenti del programma.
#
#   scripts/test.sh                 lint + test del backend (nel container) + test del frontend
#   scripts/test.sh backend [args]  solo backend; gli argomenti vanno a pytest (es. -k pipeline -x)
#   scripts/test.sh frontend        solo frontend (typecheck + vitest)
#   scripts/test.sh lint            solo ruff
#
# Il backend gira in "bioloom-test:<base>", costruita sopra l'immagine locale "bioloom:<versione>"
# (quella dell'avviatore; un'altra con BIOLOOM_BASE_IMAGE, es. un'immagine di sviluppo con strumenti nuovi)
# con pytest e ruff; il codice del repository è montato, non copiato.
# MODELLER: chiave da $MODELLER_KEY oppure da <cartella dati>/licenses/modeller.key
# (cartella dati: $BIOLOOM_DATA_PATH, predefinita ./data). Senza chiave i test di MODELLER vengono saltati.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VERSION="$(head -n1 "$ROOT/launcher/VERSION" 2>/dev/null | tr -d '[:space:]')"
VERSION="${VERSION:-4.1.0}"
BASE_IMAGE="${BIOLOOM_BASE_IMAGE:-bioloom:$VERSION}"
# un'immagine di test per ogni immagine di base: più worktree con basi diverse non si sovrascrivono a vicenda
TEST_IMAGE="${BIOLOOM_TEST_IMAGE:-bioloom-test:$(printf %s "$BASE_IMAGE" | tr -c 'A-Za-z0-9._-' '-')}"
DATA_PATH="${BIOLOOM_DATA_PATH:-$ROOT/data}"

# Percorso per `docker -v` (su Windows con Git Bash serve la forma D:/...).
host_path() { if command -v cygpath >/dev/null 2>&1; then cygpath -m "$1"; else printf '%s' "$1"; fi; }
export MSYS_NO_PATHCONV=1

ensure_image() {
  if ! docker image inspect "$BASE_IMAGE" >/dev/null 2>&1; then
    echo "Immagine $BASE_IMAGE assente: avvia BioLoom una volta (la costruisce) oppure" >&2
    echo "  docker compose -f launcher/compose.yml build" >&2
    exit 2
  fi
  local base_id stamp
  base_id="$(docker image inspect -f '{{.Id}}' "$BASE_IMAGE")"
  stamp="$(docker image inspect -f '{{index .Config.Labels "bioloom.test.base"}}' "$TEST_IMAGE" 2>/dev/null || true)"
  if [ "$stamp" != "$base_id" ]; then
    echo "== costruzione di $TEST_IMAGE"
    docker build -q --build-arg "BASE=$BASE_IMAGE" --label "bioloom.test.base=$base_id" \
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
  [ -d node_modules ] || npm ci --no-audit --no-fund
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
  *) echo "uso: scripts/test.sh [all|backend|frontend|lint] [argomenti di pytest]" >&2; exit 2 ;;
esac
