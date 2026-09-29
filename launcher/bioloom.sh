#!/usr/bin/env bash
# Avviatore di BioLoom per macOS e Linux (e Git Bash su Windows): avvia i container Docker, trova il
# disco con UniClust30, applica le licenze e apre il browser.
#
#   bioloom.sh                   avvio con menu        bioloom.sh arresta     arresto
#   bioloom.sh stato | log | verifica                  bioloom.sh prepara <cartella>   copia portatile
#   bioloom.sh server            avvio da server (server.env: rete, chiave d'accesso, percorsi)
#   opzioni: --no-browser --no-menu --senza-dati --senza-qmean
#            --uniclust <cartella>  cartella con UniClust30/UniRef30 (qualunque versione; auto = ricerca automatica)
set -u

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
COMPOSE="$ROOT/launcher/compose.yml"
ENV_FILE="$ROOT/.env.locale"
DATA_DIR="$ROOT/data"
IMAGES_DIR="$ROOT/immagini"
VERSION="$(head -n1 "$ROOT/launcher/VERSION" 2>/dev/null | tr -d '[:space:]')"
VERSION="${VERSION:-4.1.0}"
ACTION="avvia"; DEST=""; NO_BROWSER=0; NO_MENU=0; NO_DATA=0; NO_QMEAN=0
UNICLUST_CHOICE=""; UNICLUST_CHOICE_SET=0; UNICLUST_MANUAL=""
while [ $# -gt 0 ]; do
  case "$1" in
    --no-browser) NO_BROWSER=1 ;;
    --no-menu) NO_MENU=1 ;;
    --senza-dati) NO_DATA=1 ;;
    --senza-qmean) NO_QMEAN=1 ;;
    --uniclust) UNICLUST_CHOICE="${2:-}"; UNICLUST_CHOICE_SET=1; shift
                case "$UNICLUST_CHOICE" in auto|automatica) UNICLUST_CHOICE="" ;; esac ;;
    avvia|arresta|stato|log|verifica|prepara|server) ACTION="$1" ;;
    *) DEST="$1" ;;
  esac
  shift
done

case "$(uname -s)" in
  Darwin) OS=mac ;;
  Linux) OS=linux ;;
  MINGW*|MSYS*|CYGWIN*) OS=windows ;;
  *) OS=other ;;
esac

c_cyan=$'\033[36m'; c_yellow=$'\033[33m'; c_red=$'\033[31m'; c_green=$'\033[32m'; c_dim=$'\033[2m'; c_off=$'\033[0m'
say()   { printf '  %s\n' "$*"; }
title() { printf '\n  %s%s%s\n' "$c_cyan" "$*" "$c_off"; }
warn()  { printf '  %s%s%s\n' "$c_yellow" "$*" "$c_off"; }
fail()  { printf '\n  %s[!] %s%s\n\n' "$c_red" "$*" "$c_off"; exit 1; }

compose() { docker compose -f "$COMPOSE" --env-file "$ENV_FILE" "$@"; }

# ------------------------------------------------------------------------------------------- Docker
ensure_docker() {
  if ! command -v docker >/dev/null 2>&1; then
    warn "Docker non è installato. BioLoom gira dentro Docker:"
    if [ "$OS" = mac ]; then warn "installa Docker Desktop per Mac (https://www.docker.com/products/docker-desktop/)"
    else warn "installa Docker Engine (https://docs.docker.com/engine/install/) oppure Docker Desktop"; fi
    exit 1
  fi
  local out
  out="$(docker info --format '{{.ServerVersion}}' 2>&1)" && return 0
  if printf '%s' "$out" | grep -qi "permission denied"; then
    fail "L'utente non può usare Docker: aggiungilo al gruppo docker (sudo usermod -aG docker \$USER) e rientra nella sessione."
  fi
  if [ "$OS" = mac ]; then
    printf '  %sAvvio di Docker Desktop' "$c_yellow"
    open -a Docker >/dev/null 2>&1 || fail "Impossibile avviare Docker Desktop: aprilo manualmente."
    for _ in $(seq 1 90); do
      sleep 2; printf '.'
      docker info >/dev/null 2>&1 && { printf '%s\n' "$c_off"; return 0; }
    done
    printf '%s\n' "$c_off"; fail "Docker Desktop non ha risposto entro 3 minuti."
  fi
  fail "Docker non è in esecuzione: avvialo (es. sudo systemctl start docker) e riprova."
}

have_image() { docker image inspect "$1" >/dev/null 2>&1; }

ensure_image() {  # $1 immagine  $2 file tar  $3 servizio compose
  have_image "$1" && return 0
  if [ -f "$IMAGES_DIR/$2" ]; then
    warn "Caricamento dell'immagine $1 dalla cartella 'immagini' (qualche minuto)..."
    docker load -i "$IMAGES_DIR/$2" >/dev/null && have_image "$1" && return 0
    warn "Caricamento non riuscito: provo a costruire l'immagine."
  fi
  if [ -f "$ROOT/docker/Dockerfile" ]; then
    warn "Prima installazione: costruzione dell'immagine $1 (serve internet, 10-20 minuti)."
    compose build "$3" && return 0
  fi
  return 1
}

# ------------------------------------------------------------------------------------------- UniClust30/UniRef30 e QMTL
# Qualunque versione: HH-suite 3 (<nome>_a3m.ffindex) o HH-suite 2 (<nome>_a3m_db.index), anche più versioni.
# Il worker QMEAN riconosce formati e versioni; qui basta trovare le cartelle e montare quella che le contiene.
uniclust_names() {  # nomi dei database presenti in una cartella
  local f
  for f in "$1"/*_a3m.ffindex "$1"/*_a3m_db.index; do
    [ -s "$f" ] || continue
    f="$(basename "$f")"; f="${f%_a3m.ffindex}"; printf '%s\n' "${f%_a3m_db.index}"
  done | sort -u
}

is_uniclust() { [ -n "$1" ] && [ -d "$1" ] && [ -n "$(uniclust_names "$1")" ]; }

uniclust_below() {  # cartelle con un database dentro $1 (fino a 3 livelli)
  [ -n "$1" ] && [ -d "$1" ] || return 0
  find "$1" -maxdepth 4 -type f \( -name '*_a3m.ffindex' -o -name '*_a3m_db.index' \) -size +0 \
       -not -path '*/.*' 2>/dev/null | while IFS= read -r f; do dirname "$f"; done | sort -u
}

common_parent() {  # cartella comune a tutte le righe in ingresso
  awk 'NR == 1 { n = split($0, first, "/"); common = n; next }
       { m = split($0, cur, "/"); i = 1; while (i <= common && i <= m && first[i] == cur[i]) i++; common = i - 1 }
       END { out = ""; for (i = 1; i <= common; i++) out = out (i > 1 ? "/" : "") first[i]; print (out == "" ? "/" : out) }'
}

version_key() { printf '%s\n' "$1" | sed -nE 's/.*((19|20)[0-9]{2})[_.-]?([0-9]{2}).*/\1\3/p' | head -n1; }

is_qmtl() { [ -n "$1" ] && [ -f "$1/VERSION" ] && [ -f "$1/indexer.dat" ]; }

search_roots() {
  case "$OS" in
    mac) ls -d /Volumes/*/ 2>/dev/null; echo "$HOME" ;;
    linux) ls -d /media/"${USER:-x}"/*/ /run/media/"${USER:-x}"/*/ /media/*/ /mnt/*/ 2>/dev/null; echo "$HOME" ;;
    windows) local l; for l in c d e f g h i j k l m n o p q r s t u v w x y z; do [ -d "/$l" ] && echo "/$l/"; done ;;
  esac
}

find_databases() {  # imposta UNICLUST (cartella da montare), UNICLUST_NAMES, QMTL, ARCHIVE
  UNICLUST=""; UNICLUST_NAMES=""; ARCHIVE=""; QMTL="${SAVED_QMTL:-}"
  is_qmtl "$QMTL" || QMTL=""
  local found
  [ "${FORCE_SCAN:-0}" = 1 ] && [ -z "${SAVED_MANUAL:-}" ] && SAVED_UNICLUST=""  # R: nuova ricerca sui dischi
  found="$(uniclust_below "${SAVED_UNICLUST:-}")"
  if [ -n "$found" ]; then
    UNICLUST="$SAVED_UNICLUST"
  elif [ -n "${SAVED_MANUAL:-}" ] && [ -n "${SAVED_UNICLUST:-}" ]; then
    warn "La cartella indicata per UniClust30 ($SAVED_UNICLUST) non è raggiungibile: cerco sui dischi."
    UNICLUST_MANUAL=""
  fi
  if [ -z "$UNICLUST" ]; then
    say "${c_dim}Ricerca di UniClust30/UniRef30 sui dischi collegati...${c_off}"
    local root best="" best_key="" dir key names drive
    while IFS= read -r root; do
      [ -d "$root" ] || continue
      found="$(find "$root" -maxdepth 4 -type f \( -name '*_a3m.ffindex' -o -name '*_a3m_db.index' \) -size +0 \
                 -not -path '*/node_modules/*' -not -path '*/.*' 2>/dev/null | while IFS= read -r f; do dirname "$f"; done | sort -u)"
      if [ -z "$found" ]; then
        [ -z "$ARCHIVE" ] && ARCHIVE="$(find "$root" -maxdepth 4 -type f -iname '*uni*.tar.gz' -not -path '*/.*' 2>/dev/null | head -n1)"
        continue
      fi
      while IFS= read -r dir; do
        for names in $(uniclust_names "$dir"); do
          key="$(version_key "$names")"
          if [ -z "$best" ] || [ "${key:-0}" -gt "${best_key:-0}" ]; then best="$dir"; best_key="${key:-0}"; drive="$root"; fi
        done
      done <<< "$found"
      [ -n "$best" ] && break  # primo disco con database (se ce ne sono su più dischi, conta il primo trovato)
    done < <(search_roots)
    if [ -n "$best" ]; then
      found="$(uniclust_below "$drive")"
      if [ "$(printf '%s\n' "$found" | grep -c .)" -gt 1 ]; then UNICLUST="$(printf '%s\n' "$found" | common_parent)"; else UNICLUST="$best"; fi
      UNICLUST_MANUAL=""
    fi
  fi
  if [ -n "$UNICLUST" ]; then
    UNICLUST_NAMES="$(uniclust_below "$UNICLUST" | while IFS= read -r dir; do uniclust_names "$dir"; done | sort -u | paste -sd ',' - | sed 's/,/, /g')"
  fi
  if [ -z "$QMTL" ]; then
    local root hit
    while IFS= read -r root; do
      [ -d "$root" ] || continue
      while IFS= read -r hit; do
        is_qmtl "$(dirname "$hit")" && { QMTL="$(dirname "$hit")"; break; }
      done < <(find "$root" -maxdepth 4 -type f -name 'indexer.dat' 2>/dev/null)
      [ -n "$QMTL" ] && break
    done < <(search_roots)
  fi
}

ask_uniclust() {  # cartella indicata a mano (menu U o --uniclust)
  local path
  say "Indica la cartella che contiene UniClust30/UniRef30 (anche quella superiore, se ci sono più versioni)."
  read -r -p "  Percorso (invio = ricerca automatica): " path || return 1
  path="${path%\"}"; path="${path#\"}"
  if [ -n "$path" ] && [ -z "$(uniclust_below "$path")" ]; then
    warn "Nessun database HH-suite in $path (cerco file *_a3m.ffindex o *_a3m_db.index)."
    return 1
  fi
  UNICLUST_CHOICE="$path"; UNICLUST_CHOICE_SET=1
}

# ------------------------------------------------------------------------------------------- licenze, porta, file .env
prosa_mac() {
  local lic="$DATA_DIR/licenses/prosa/LicenseDB"
  [ -f "$lic" ] || return 0
  awk 'NF >= 4 && $3 ~ /^[0-9A-Fa-f]{12}$/ { m = tolower($3); printf "%s:%s:%s:%s:%s:%s\n", substr(m,1,2), substr(m,3,2), substr(m,5,2), substr(m,7,2), substr(m,9,2), substr(m,11,2); exit }' "$lic"
}

random_mac() { printf '02:%02x:%02x:%02x:%02x:%02x\n' $((RANDOM % 256)) $((RANDOM % 256)) $((RANDOM % 256)) $((RANDOM % 256)) $((RANDOM % 256)); }

port_free() { ! (exec 3<>"/dev/tcp/127.0.0.1/$1") 2>/dev/null; }

host_path() {  # percorso comprensibile da Docker (su Git Bash per Windows: C:\...)
  if [ "$OS" = windows ] && command -v cygpath >/dev/null 2>&1; then cygpath -w "$1"; else printf '%s\n' "$1"; fi
}

read_saved() {
  SAVED_UNICLUST=""; SAVED_QMTL=""; SAVED_PORT=""; SAVED_MAC=""; SAVED_MAC_LIC=""; SAVED_MANUAL=""
  [ -f "$ENV_FILE" ] || return 0
  SAVED_UNICLUST="$(sed -n 's/^UNICLUST_PATH=//p' "$ENV_FILE" | tr -d '\r')"
  SAVED_MANUAL="$(sed -n 's/^UNICLUST_MANUAL=//p' "$ENV_FILE" | tr -d '\r')"
  SAVED_QMTL="$(sed -n 's/^QMTL_PATH=//p' "$ENV_FILE" | tr -d '\r')"
  if [ "$OS" = windows ] && command -v cygpath >/dev/null 2>&1; then
    [ -n "$SAVED_UNICLUST" ] && SAVED_UNICLUST="$(cygpath -u "$SAVED_UNICLUST")"
    [ -n "$SAVED_QMTL" ] && SAVED_QMTL="$(cygpath -u "$SAVED_QMTL")"
  fi
  SAVED_PORT="$(sed -n 's/^BIOLOOM_PORT=//p' "$ENV_FILE" | tr -d '\r')"
  SAVED_MAC="$(sed -n 's/^BIOLOOM_MAC=//p' "$ENV_FILE" | tr -d '\r')"
  SAVED_MAC_LIC="$(sed -n 's/^BIOLOOM_MAC_FROM_LICENSE=//p' "$ENV_FILE" | tr -d '\r')"
}

ensure_env() { [ -f "$ENV_FILE" ] || printf 'BIOLOOM_VERSION=%s\n' "$VERSION" > "$ENV_FILE"; }

wait_healthy() {
  local port="$1"
  for _ in $(seq 1 120); do
    if command -v curl >/dev/null 2>&1; then
      curl -fsS "http://127.0.0.1:$port/api/health" >/dev/null 2>&1 && return 0
    elif (exec 3<>"/dev/tcp/127.0.0.1/$port") 2>/dev/null; then
      return 0
    fi
    sleep 1
  done
  return 1
}

open_url() {
  case "$OS" in
    mac) open "$1" ;;
    linux) xdg-open "$1" >/dev/null 2>&1 || say "Apri nel browser: $1" ;;
    windows) cmd //c start "" "$1" ;;
  esac
}

# ------------------------------------------------------------------------------------------- azioni
start_local() {
  title "BioLoom $VERSION"
  ensure_docker
  mkdir -p "$DATA_DIR" || fail "Impossibile creare la cartella dati $DATA_DIR."
  if ! touch "$DATA_DIR/.scrittura" 2>/dev/null; then fail "La cartella dati $DATA_DIR non è scrivibile."; fi
  rm -f "$DATA_DIR/.scrittura"
  read_saved
  if [ "$UNICLUST_CHOICE_SET" = 1 ]; then
    SAVED_UNICLUST="$UNICLUST_CHOICE"
    SAVED_MANUAL="${UNICLUST_CHOICE:+1}"
  fi
  UNICLUST_MANUAL="$SAVED_MANUAL"
  find_databases
  local mac port
  mac="$(prosa_mac)"
  if [ -z "$mac" ]; then
    if [ -n "$SAVED_MAC" ] && [ -z "$SAVED_MAC_LIC" ]; then mac="$SAVED_MAC"; else mac="$(random_mac)"; fi
    MAC_LIC=""
  else
    MAC_LIC=1
  fi
  port="${SAVED_PORT:-8765}"
  ensure_env
  if ! compose ps --status running --services 2>/dev/null | grep -qx bioloom; then
    while ! port_free "$port" && [ "$port" -lt 8800 ]; do port=$((port + 1)); done
  fi
  {
    echo "# Generato dall'avviatore di BioLoom: configurazione di questo computer (non va copiata altrove)."
    echo "BIOLOOM_VERSION=$VERSION"
    echo "BIOLOOM_PORT=$port"
    echo "BIOLOOM_MAC=$mac"
    [ -n "$MAC_LIC" ] && echo "BIOLOOM_MAC_FROM_LICENSE=1"
    if [ -n "$UNICLUST" ]; then
      echo "UNICLUST_PATH=$(host_path "$UNICLUST")"
      [ -n "$UNICLUST_MANUAL" ] && echo "UNICLUST_MANUAL=1"
      echo "COMPOSE_PROFILES=qmean"
      echo "BIOLOOM_QMEAN_URL=http://qmean:8080"
      [ -n "$QMTL" ] && echo "QMTL_PATH=$(host_path "$QMTL")"
    fi
  } > "$ENV_FILE"
  ensure_image "bioloom:$VERSION" "bioloom-$VERSION.tar" bioloom \
    || fail "Immagine BioLoom non disponibile: copia la cartella 'immagini' dalla chiavetta originale oppure collegati a internet."
  if [ -n "$UNICLUST" ] && ! ensure_image "bioloom-qmean:$VERSION" "bioloom-qmean-$VERSION.tar" qmean; then
    warn "Immagine QMEAN non disponibile: QMEAN locale resta spento."
    sed -i.bak '/^UNICLUST_PATH=/d;/^COMPOSE_PROFILES=/d;/^QMTL_PATH=/d;/^BIOLOOM_QMEAN_URL=/d' "$ENV_FILE" && rm -f "$ENV_FILE.bak"
    UNICLUST=""
  fi
  say "${c_dim}Avvio dei container...${c_off}"
  [ -z "$UNICLUST" ] && compose --profile qmean rm -sf qmean >/dev/null 2>&1
  compose up -d --remove-orphans || fail "Docker non è riuscito ad avviare BioLoom (vedi i messaggi sopra)."
  wait_healthy "$port" || fail "BioLoom non risponde: controlla con 'bioloom.sh log'."
  local url="http://127.0.0.1:$port"
  title "BioLoom è pronto: $url"
  say "Dati:        $DATA_DIR"
  if [ -n "$UNICLUST" ]; then
    say "${c_green}QMEAN:       locale, ${UNICLUST_NAMES:-?} in $UNICLUST${QMTL:+ + QMTL (QMEANDisCo)}${c_off}"
    case "$UNICLUST_NAMES" in *,*) say "${c_dim}             versione da usare: Impostazioni > Database (predefinita la più recente)${c_off}" ;; esac
  elif [ -n "$ARCHIVE" ]; then
    say "${c_yellow}QMEAN:       trovato l'archivio $ARCHIVE ma non è estratto: estrailo sul disco e usa R (Riavvia)${c_off}"
  else
    say "${c_yellow}QMEAN:       locale spento (nessun disco con UniClust30/UniRef30; U per indicare la cartella)${c_off}"
  fi
  if [ -n "$MAC_LIC" ]; then say "ProSA:       licenza trovata, MAC applicato"; else say "ProSA:       nessuna licenza (si carica da Impostazioni > Licenze)"; fi
  [ "$NO_BROWSER" = 1 ] || open_url "$url"
  [ "$NO_MENU" = 1 ] || menu "$url"
}

menu() {
  local key
  while true; do
    printf '\n  [A] Apri nel browser   [V] Verifica strumenti   [L] Log   [R] Riavvia (rileva di nuovo i dischi)\n'
    printf '  [U] Indica la cartella di UniClust30/UniRef30   [S] Arresta BioLoom ed esci\n'
    printf '  [C] Chiudi (BioLoom resta attivo)\n'
    read -r -p "  Scelta: " key || return 0
    case "$(printf '%s' "$key" | tr '[:lower:]' '[:upper:]')" in
      A) open_url "$1" ;;
      V) compose exec bioloom python -m bioloom.selftest ;;
      L) compose logs --tail 60 ;;
      R) FORCE_SCAN=1; start_local; return ;;
      U) if ask_uniclust; then start_local; return; fi ;;
      S) stop_all; return ;;
      C) return ;;
    esac
  done
}

stop_all() {
  ensure_env
  warn "Arresto di BioLoom (i calcoli in corso vengono interrotti)..."
  compose --profile qmean down
  printf '  %sBioLoom è arrestato. I dati restano in %s.%s\n' "$c_green" "$DATA_DIR" "$c_off"
}

prepare_portable() {
  [ -n "$DEST" ] || { [ "$NO_MENU" = 1 ] && fail "Indica la cartella di destinazione."; read -r -p "  Cartella di destinazione: " DEST; }
  [ -n "$DEST" ] || fail "Nessuna destinazione."
  ensure_docker
  mkdir -p "$DEST" && DEST="$(cd "$DEST" && pwd)"
  title "Preparazione della copia portatile in $DEST"
  local f
  for f in "Avvia BioLoom.bat" "Arresta BioLoom.bat" "Avvia BioLoom.command" "Arresta BioLoom.command" avvia-bioloom.sh \
           README.md LEGGIMI.txt .dockerignore .gitattributes .gitignore; do
    [ -e "$ROOT/$f" ] && cp -p "$ROOT/$f" "$DEST/"
  done
  tar -C "$ROOT" --exclude=node_modules --exclude=dist --exclude=__pycache__ --exclude=.pytest_cache \
      -cf - launcher docker docs backend frontend | tar -C "$DEST" -xf - || fail "Copia dei sorgenti non riuscita."
  if [ "$NO_DATA" = 0 ] && [ -d "$DATA_DIR" ]; then
    say "Copia dei dati (progetti, risultati, licenze, database)..."
    tar -C "$ROOT" --exclude=.scratch --exclude=.localcache -cf - data | tar -C "$DEST" -xf - || fail "Copia dei dati non riuscita."
  fi
  mkdir -p "$DEST/immagini"
  local spec name tarname service
  for spec in "bioloom:$VERSION bioloom-$VERSION.tar bioloom" "bioloom-qmean:$VERSION bioloom-qmean-$VERSION.tar qmean"; do
    read -r name tarname service <<< "$spec"
    [ "$service" = qmean ] && [ "$NO_QMEAN" = 1 ] && continue
    ensure_image "$name" "$tarname" "$service" || { warn "Immagine $name non disponibile: non inclusa."; continue; }
    say "Salvataggio dell'immagine $name (qualche minuto)..."
    docker save -o "$DEST/immagini/$tarname" "$name" || fail "Salvataggio di $name non riuscito."
  done
  title "Copia portatile pronta: $DEST"
  say "Sul computer di destinazione serve solo Docker: doppio clic su 'Avvia BioLoom' (.bat, .command o avvia-bioloom.sh)."
}

start_server() {
  local server_env="$ROOT/server.env"
  ensure_docker
  if [ ! -f "$server_env" ]; then
    local key
    key="$(LC_ALL=C tr -dc 'A-Za-z0-9' < /dev/urandom | head -c 24)"
    sed "s/^BIOLOOM_ACCESS_KEY=.*/BIOLOOM_ACCESS_KEY=$key/" "$ROOT/launcher/server.env.example" > "$server_env"
    chmod 600 "$server_env"
    title "Creato $server_env"
    say "Chiave d'accesso generata: $key"
    say "Controlla i percorsi (cartella dati, UniClust30) e la porta, poi rilancia: bioloom.sh server"
    exit 0
  fi
  ENV_FILE="$server_env"
  local uniclust data
  uniclust="$(sed -n 's/^UNICLUST_PATH=//p' "$server_env" | tr -d '\r')"
  data="$(sed -n 's/^BIOLOOM_DATA_PATH=//p' "$server_env" | tr -d '\r')"
  [ -n "$data" ] && mkdir -p "$data"
  ensure_image "bioloom:$VERSION" "bioloom-$VERSION.tar" bioloom || fail "Immagine BioLoom non disponibile."
  if [ -n "$uniclust" ]; then
    [ -n "$(uniclust_below "$uniclust")" ] || fail "UNICLUST_PATH in server.env non contiene database UniClust30/UniRef30 (entro 3 livelli): $uniclust"
    ensure_image "bioloom-qmean:$VERSION" "bioloom-qmean-$VERSION.tar" qmean || fail "Immagine QMEAN non disponibile."
    COMPOSE_PROFILES=qmean BIOLOOM_QMEAN_URL=http://qmean:8080 compose up -d --remove-orphans || fail "Avvio non riuscito."
  else
    compose up -d --remove-orphans || fail "Avvio non riuscito."
  fi
  title "BioLoom (server) avviato sulla porta $(sed -n 's/^BIOLOOM_PORT=//p' "$server_env" | tr -d '\r')"
  say "Accesso protetto dalla chiave in server.env (BIOLOOM_ACCESS_KEY). Per HTTPS usa un reverse proxy (vedi docs/SERVER.md)."
}

case "$ACTION" in
  avvia) start_local ;;
  arresta) stop_all ;;
  stato) ensure_env; compose --profile qmean ps ;;
  log) ensure_env; compose logs --tail 200 ;;
  verifica) ensure_env; compose exec bioloom python -m bioloom.selftest ;;
  prepara) prepare_portable ;;
  server) start_server ;;
esac
