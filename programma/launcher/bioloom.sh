#!/usr/bin/env bash
# Avviatore di BioLoom per macOS e Linux (e Git Bash su Windows): avvia i container Docker, trova il
# disco con UniClust30, applica le licenze e apre il browser.
#
#   bioloom.sh                   avvio con menu        bioloom.sh arresta     arresto
#   bioloom.sh stato | log | verifica                  bioloom.sh prepara <cartella>   copia portatile
#   bioloom.sh server            avvio da server (server.env: rete, chiave d'accesso, percorsi)
#   bioloom.sh aggiorna          porta le immagini al codice in programma/ (file in immagini/ o costruzione) e,
#                                se BioLoom è attivo, lo riavvia come era partito (desktop o server)
#   opzioni: --no-browser --no-menu --senza-dati --senza-qmean
#            --uniclust <cartella>  cartella con UniClust30/UniRef30 (qualunque versione; auto = ricerca automatica)
#            --con-licenze  la copia portatile include chiavi di licenza, posta.env e chiave d'accesso (solo per sé)
#            --sposta-prosa  sposta su questo computer, senza chiedere, la licenza ProSA attiva su un altro
#   porta: dal desktop la prima libera da 8765 a 8800 (si riparte dall'ultima usata); BIOLOOM_PORT=<porta> nell'ambiente
#          ne chiede una precisa (se è occupata ci si ferma). Da server quella di server.env (o di BIOLOOM_PORT).
set -u

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"   # programma/: il codice
BASE="$(cd "$ROOT/.." && pwd)"                               # cartella di BioLoom: avviatori, Guida, dati, immagini
COMPOSE="$ROOT/launcher/compose.yml"
ENV_FILE="$BASE/.env.locale"
DATA_DIR="$BASE/data"
IMAGES_DIR="$BASE/immagini"
VERSION="$(head -n1 "$ROOT/launcher/VERSION" 2>/dev/null | tr -d '[:space:]')"   # unica fonte della versione
ACTION="avvia"; DEST=""; NO_BROWSER=0; NO_MENU=0; NO_DATA=0; NO_QMEAN=0; WITH_LICENSES=0; MOVE_PROSA=0
UNICLUST_CHOICE=""; UNICLUST_CHOICE_SET=0; UNICLUST_MANUAL=""
while [ $# -gt 0 ]; do
  case "$1" in
    --no-browser) NO_BROWSER=1 ;;
    --no-menu) NO_MENU=1 ;;
    --senza-dati) NO_DATA=1 ;;
    --con-licenze) WITH_LICENSES=1 ;;
    --senza-qmean) NO_QMEAN=1 ;;
    --sposta-prosa) MOVE_PROSA=1 ;;
    --uniclust) UNICLUST_CHOICE="${2:-}"; UNICLUST_CHOICE_SET=1; shift
                case "$UNICLUST_CHOICE" in auto|automatica) UNICLUST_CHOICE="" ;; esac ;;
    avvia|arresta|stato|log|verifica|prepara|server|aggiorna) ACTION="$1" ;;
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

[ -n "$VERSION" ] || fail "Manca il file programma/launcher/VERSION (versione di BioLoom): la cartella programma è incompleta, copiala di nuovo."
# Le variabili della shell prevalgono su --env-file: una BIOLOOM_VERSION vecchia in .env.locale o server.env non conta
export BIOLOOM_VERSION="$VERSION"
# Porta chiesta con BIOLOOM_PORT. Non resta nell'ambiente: compose la preferirebbe alla porta che l'avviatore sceglie e
# scrive in .env.locale (e "aggiorna" non riavvierebbe BioLoom com'era); da server start_server la riesporta
REQ_PORT="${BIOLOOM_PORT:-}"
unset BIOLOOM_PORT

# comando da suggerire nei messaggi: quello della Guida, con il percorso (questo script non è nel PATH)
LAUNCH_CMD="$BASE/avvia-bioloom.sh"; [ -f "$LAUNCH_CMD" ] || LAUNCH_CMD="$ROOT/launcher/bioloom.sh"
case "$LAUNCH_CMD" in *' '*) LAUNCH_CMD="\"$LAUNCH_CMD\"" ;; esac

compose() { docker compose -f "$COMPOSE" --env-file "$ENV_FILE" "$@"; }

env_val() {  # valore di $1 nel file $2 letto come fa docker compose: ultima riga, senza spazi ai lati, virgolette e
  # commento a fine riga (" # ..." fuori dalle virgolette)
  local v=""
  [ -f "$2" ] && v="$(sed -n "s/^$1=//p" "$2" | tail -n1 | tr -d '\r' | sed 's/^[[:space:]]*//')"
  case "$v" in
    \"*) v="${v#\"}"; v="${v%%\"*}" ;;
    \'*) v="${v#\'}"; v="${v%%\'*}" ;;
    *) v="$(printf '%s' "$v" | sed 's/[[:space:]]#.*$//;s/[[:space:]]*$//')" ;;
  esac
  printf '%s' "$v"
}

compose_path() {  # percorso di un file env com'è per compose: i relativi partono dalla cartella di compose.yml
  case "$1" in
    ""|/*|[A-Za-z]:*) printf '%s' "$1" ;;
    *) printf '%s' "$ROOT/launcher/$1" ;;
  esac
}

server_data() {  # cartella dati di server.env (BIOLOOM_DATA_PATH; predefinita quella di compose.yml)
  local d
  d="$(env_val BIOLOOM_DATA_PATH "$BASE/server.env")"
  compose_path "${d:-../../data}"
}

# ------------------------------------------------------------------------------------------- Docker
docker_desktop_note() {  # licenza di Docker Desktop (termini di Docker: abbonamento a pagamento per l'uso professionale
  # negli enti pubblici, di qualunque dimensione, e nelle organizzazioni con almeno 250 dipendenti o almeno 10 milioni di
  # dollari di fatturato annuo) e alternative
  warn "Docker Desktop è gratuito per uso personale, didattico e nelle aziende piccole (meno di 250 dipendenti e meno di"
  warn "10 milioni di dollari di fatturato annuo); negli enti pubblici, anche piccoli, e nelle altre organizzazioni, se"
  warn "l'uso non è didattico, verifica la licenza (https://www.docker.com/pricing/). In alternativa un programma che"
  local alt="Rancher Desktop o Podman Desktop con la compatibilità Docker"
  [ "$OS" = mac ] && alt="Colima, $alt"
  warn "dia i comandi docker e docker compose (non provati con BioLoom): $alt."
}

ensure_docker() {
  if ! command -v docker >/dev/null 2>&1; then
    warn "Docker non è installato. BioLoom gira dentro Docker:"
    case "$OS" in
      mac) warn "installa Docker Desktop per Mac (https://www.docker.com/products/docker-desktop/)."; docker_desktop_note ;;
      windows) warn "installa Docker Desktop (https://www.docker.com/products/docker-desktop/)."; docker_desktop_note ;;
      *) warn "installa Docker Engine con il plugin compose (gratuito: https://docs.docker.com/engine/install/)." ;;
    esac
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

LOADED_TAR=""   # ultimo file di immagini/ caricato: aggiornare l'immagine non lo ricarica

ensure_image() {  # $1 immagine  $2 file tar  $3 servizio compose
  have_image "$1" && return 0
  if [ -f "$IMAGES_DIR/$2" ]; then
    warn "Caricamento dell'immagine $1 dalla cartella 'immagini' (qualche minuto)..."
    docker load -i "$IMAGES_DIR/$2" >/dev/null && have_image "$1" && { LOADED_TAR="$IMAGES_DIR/$2"; return 0; }
    warn "Caricamento non riuscito: provo a costruire l'immagine."
  fi
  if [ -f "$ROOT/docker/Dockerfile" ]; then
    warn "Prima installazione: costruzione dell'immagine $1 (serve internet, 10-20 minuti)."
    compose build "$3" && return 0
  fi
  return 1
}

# ------------------------------------------------------------------------------------------- impronta del codice
# docker/impronta.sh riassume in un hash i file di backend/, frontend/ e docker/ che entrano nell'immagine e la build
# lo salva in /app/IMPRONTA: se i due valori differiscono, il codice in programma/ è cambiato (anche a parità di
# versione) senza ricostruire l'immagine. Senza gli strumenti per calcolarla il controllo si salta.
CODE_IMPRONTA=""; CODE_IMPRONTA_SET=0

code_impronta() {  # imposta CODE_IMPRONTA, una volta sola (vuota se non si può calcolare)
  [ "$CODE_IMPRONTA_SET" = 1 ] && return 0
  CODE_IMPRONTA_SET=1
  [ -f "$ROOT/docker/impronta.sh" ] || return 0
  CODE_IMPRONTA="$(sh "$ROOT/docker/impronta.sh" "$ROOT" 2>/dev/null)" || CODE_IMPRONTA=""
  case "$CODE_IMPRONTA" in *[!0-9a-f]*) CODE_IMPRONTA="" ;; esac
  [ "${#CODE_IMPRONTA}" = 64 ] || CODE_IMPRONTA=""
}

image_impronta() {  # impronta salvata nell'immagine $1: vuota se l'immagine manca o non ha /app/IMPRONTA (immagini
  # precedenti); uscita 1 se Docker non ha potuto avviare il container (non si sa)
  local out rc
  have_image "$1" || return 0   # senza immagine "docker run" la cercherebbe su Docker Hub
  # MSYS_NO_PATHCONV: in Git Bash /app/IMPRONTA non va convertito in un percorso di Windows
  out="$(MSYS_NO_PATHCONV=1 MSYS2_ARG_CONV_EXCL='*' docker run --rm --platform linux/amd64 --entrypoint cat "$1" /app/IMPRONTA 2>/dev/null)"
  rc=$?
  case "$rc" in
    0) printf '%s' "$out" | tr -d '[:space:]' ;;
    1) ;;   # cat: file assente
    *) return 1 ;;
  esac
}

image_outdated() {  # vero se l'immagine $1 non corrisponde al codice in programma/ (falso se non si può sapere)
  local imp
  code_impronta
  [ -n "$CODE_IMPRONTA" ] || return 1
  imp="$(image_impronta "$1")" || return 1
  [ "$imp" != "$CODE_IMPRONTA" ]
}

image_current() {  # vero se si sa che l'immagine $1 corrisponde al codice in programma/
  local imp
  code_impronta
  [ -n "$CODE_IMPRONTA" ] || return 1
  imp="$(image_impronta "$1")" || return 1
  [ "$imp" = "$CODE_IMPRONTA" ]
}

vendor_missing() {  # strumenti con licenza che una costruzione non includerebbe (pacchetti assenti in docker/vendor/)
  local m=""
  [ -f "$ROOT/docker/vendor/procheck/procheck.tar.gz" ] || m="PROCHECK"
  [ -f "$ROOT/docker/vendor/prosa/prosa2003.linux.tar.gz" ] || m="${m:+$m e }ProSA"
  printf '%s' "$m"
}

VENDOR_WARNED=0
warn_vendor() {  # avviso, una volta sola, prima di una costruzione che perderebbe PROCHECK o ProSA
  local missing
  missing="$(vendor_missing)"
  [ -n "$missing" ] || return 1
  [ "$VENDOR_WARNED" = 1 ] && return 0
  VENDOR_WARNED=1
  warn "La nuova immagine non avrà $missing: i pacchetti non sono in programma/docker/vendor/ (vedi il README lì)."
}

update_bioloom_image() {  # porta bioloom:$VERSION al codice: prima il file in immagini/, poi la costruzione
  local img="bioloom:$VERSION" tarfile="$IMAGES_DIR/bioloom-$VERSION.tar"
  if [ -f "$tarfile" ] && [ "$LOADED_TAR" != "$tarfile" ]; then
    warn "Caricamento dell'immagine $img dalla cartella 'immagini' (qualche minuto)..."
    if docker load -i "$tarfile" >/dev/null && have_image "$img"; then
      LOADED_TAR="$tarfile"
      if ! image_outdated "$img"; then say "${c_green}Immagine $img aggiornata dalla cartella 'immagini'.${c_off}"; return 0; fi
      warn "Anche l'immagine nella cartella 'immagini' viene da un codice diverso."
    else
      warn "Caricamento non riuscito."
    fi
  fi
  if [ ! -f "$ROOT/docker/Dockerfile" ]; then
    warn "L'immagine $img non corrisponde al codice in programma/ e non si può ricostruire (manca programma/docker/Dockerfile)."
    return 1
  fi
  warn "Costruzione dell'immagine $img dal codice in programma/ (serve internet; pochi minuti, 10-20 la prima volta)."
  warn_vendor
  compose build bioloom || { warn "Costruzione dell'immagine $img non riuscita (vedi i messaggi sopra)."; return 1; }
  if image_outdated "$img"; then
    warn "L'immagine $img costruita non corrisponde ancora al codice in programma/."
    return 1
  fi
  say "${c_green}Immagine $img ricostruita dal codice in programma/.${c_off}"
}

update_qmean_image() {  # $1 cartella di UniClust configurata; QMEAN si aggiorna se è in uso o se c'è il suo file
  local img="bioloom-qmean:$VERSION" tarfile="$IMAGES_DIR/bioloom-qmean-$VERSION.tar"
  have_image "$img" || [ -f "$tarfile" ] || [ -n "$1" ] || return 0
  if [ -f "$tarfile" ]; then
    warn "Caricamento dell'immagine $img dalla cartella 'immagini' (qualche minuto)..."
    if docker load -i "$tarfile" >/dev/null && have_image "$img"; then
      say "${c_green}Immagine $img aggiornata dalla cartella 'immagini'.${c_off}"; return 0
    fi
    warn "Caricamento non riuscito: provo a costruire l'immagine."
  fi
  if [ -f "$ROOT/docker/qmean/Dockerfile" ]; then
    warn "Costruzione dell'immagine $img (serve internet)..."
    if compose --profile qmean build qmean; then say "${c_green}Immagine $img aggiornata.${c_off}"; return 0; fi
  fi
  warn "Immagine $img non aggiornata: QMEAN locale resta con l'immagine che aveva (se c'era)."
}

check_image() {  # all'avvio, dopo ensure_image: immagine costruita da un codice diverso da quello in programma/
  local server="${1:-0}" answer="" ask="[S/n]" tarfile="$IMAGES_DIR/bioloom-$VERSION.tar"
  image_outdated "bioloom:$VERSION" || return 0
  if [ "$server" = 0 ] && [ "$NO_MENU" = 0 ]; then
    warn "L'immagine di BioLoom non corrisponde al codice in programma/ (aggiornato senza ricostruire l'immagine)."
    # senza un file in immagini/ da caricare si ricostruisce: se così si perdono PROCHECK o ProSA, Invio vale "no"
    if { [ ! -f "$tarfile" ] || [ "$LOADED_TAR" = "$tarfile" ]; } && warn_vendor; then ask="[s/N]"; fi
    read -r -p "  Aggiornarla ora? $ask " answer || answer="n"
    case "$answer" in
      [sS]*) answer=s ;;
      [nN]*) answer=n ;;
      *) if [ "$ask" = "[s/N]" ]; then answer=n; else answer=s; fi ;;
    esac
    [ "$answer" = s ] && update_bioloom_image && return 0
    warn "Avvio con l'immagine esistente: per aggiornarla usa $LAUNCH_CMD aggiorna"
    return 0
  fi
  warn "L'immagine di BioLoom non corrisponde al codice in programma/ (aggiornato senza ricostruire l'immagine):"
  warn "per aggiornarla usa $LAUNCH_CMD aggiorna. Avvio con l'immagine esistente."
}

stale_running() {  # vero se un container di BioLoom in esecuzione usa un'immagine diversa da quella attuale
  local service id img want
  for service in bioloom qmean; do
    id="$(compose --profile qmean ps -q --status running "$service" 2>/dev/null | head -n1)"
    [ -n "$id" ] || continue
    img="bioloom:$VERSION"; [ "$service" = qmean ] && img="bioloom-qmean:$VERSION"
    # immagine attuale assente (aggiornamento non riuscito): riavviare farebbe solo ritentare la costruzione a compose
    want="$(docker image inspect --format '{{.Id}}' "$img" 2>/dev/null)"
    [ -n "$want" ] || continue
    [ "$(docker inspect --format '{{.Image}}' "$id" 2>/dev/null)" != "$want" ] && return 0
  done
  return 1
}

running_env_file() {  # --env-file con cui compose ha avviato il BioLoom in esecuzione (etichetta del container): vuoto
  # se non è in esecuzione, "?" se l'etichetta manca (compose precedente alla 2.20 circa)
  local id lab
  id="$(compose ps -q --status running bioloom 2>/dev/null | head -n1)"
  [ -n "$id" ] || return 0
  lab="$(docker inspect --format '{{index .Config.Labels "com.docker.compose.project.environment_file"}}' "$id" 2>/dev/null)"
  case "$lab" in ""|"<no value>") printf '?' ;; *) printf '%s' "$lab" ;; esac
}

SERVER_MODE=0
use_running_env() {  # file per arresta, stato, log, verifica e aggiorna: quello con cui è partito BioLoom. Imposta
  # ENV_FILE e SERVER_MODE: 1 = server.env, 0 = .env.locale, vuoto = ci sono tutti e due e non si sa quale usa il
  # BioLoom in esecuzione. Un .env.locale senza BIOLOOM_PORT non viene da un avvio sul desktop: è quello minimo che le
  # versioni precedenti creavano anche sul server con "arresta" o "log".
  SERVER_MODE=0
  [ -f "$BASE/server.env" ] || return 0
  if ! grep -q '^BIOLOOM_PORT=' "$ENV_FILE" 2>/dev/null; then
    SERVER_MODE=1
  else
    case "$(running_env_file)" in   # compose ps va bene con l'uno o l'altro file: il progetto è sempre "bioloom"
      *server.env) SERVER_MODE=1 ;;
      ""|*.env.locale) ;;           # spento: conta il desktop (server.env può essere rimasto da una prova)
      *) SERVER_MODE="" ;;
    esac
  fi
  [ "$SERVER_MODE" = 1 ] && ENV_FILE="$BASE/server.env"
  return 0
}

# ------------------------------------------------------------------------------------------- un BioLoom alla volta
# Le copie di BioLoom su uno stesso computer (per esempio quella installata e quella della chiavetta) hanno lo stesso
# progetto compose "bioloom", quindi gli stessi container e volumi: avviarne una ricreerebbe i container dell'altra con
# un'altra cartella dati. Il progetto non cambia nome da una copia all'altra (il volume cache con l'indice MMseqs2
# resterebbe orfano): prima di avviare si guarda da quale cartella è partito il BioLoom in esecuzione (etichetta
# working_dir che compose mette ai container: la cartella di compose.yml).
OTHER_DIR=""; OTHER_DATA=""

same_dir() {  # vero se il percorso $1, scritto da compose su questo computer, è la cartella $2. -ef confronta la
  # cartella, non il testo: vale con maiuscole diverse (NTFS, APFS) e con link simbolici; i percorsi di Windows
  # (compose avviato da PowerShell) passano da cygpath in Git Bash e da wslpath in WSL
  local p="$1"
  case "$p" in
    [A-Za-z]:[\\/]*)
      if command -v cygpath >/dev/null 2>&1; then p="$(cygpath -u "$p")"
      elif command -v wslpath >/dev/null 2>&1; then p="$(wslpath -u "$p" 2>/dev/null)"; fi ;;
  esac
  [ -n "$p" ] && [ "$p" -ef "$2" ]
}

find_other() {  # imposta OTHER_DIR (cartella di BioLoom del BioLoom in esecuzione, se non è questa) e OTHER_DATA (la
  # sua cartella dati); vuote se non è in esecuzione, se è questa copia o se non si può sapere
  local id dir
  OTHER_DIR=""; OTHER_DATA=""
  id="$(compose ps -q --status running bioloom 2>/dev/null | head -n1)"
  [ -n "$id" ] || id="$(compose --profile qmean ps -q --status running 2>/dev/null | head -n1)"
  [ -n "$id" ] || return 0
  dir="$(docker inspect --format '{{index .Config.Labels "com.docker.compose.project.working_dir"}}' "$id" 2>/dev/null)"
  case "$dir" in ""|"<no value>") return 0 ;; esac
  same_dir "$dir" "$ROOT/launcher" && return 0
  OTHER_DIR="$(printf '%s' "$dir" | sed -E 's#[\\/]+$##; s#[\\/]programma[\\/]launcher$##')"
  OTHER_DATA="$(docker inspect --format '{{range .Mounts}}{{if eq .Destination "/data"}}{{.Source}}{{end}}{{end}}' "$id" 2>/dev/null)"
  # Docker Desktop mostra i bind mount con il percorso della sua macchina virtuale: si riporta quello del computer
  case "$OTHER_DATA" in
    /run/desktop/mnt/host/[a-zA-Z]/*|/host_mnt/[a-zA-Z]/*|/mnt/host/[a-zA-Z]/*)  # Windows: /run/desktop/mnt/host/d/...
      OTHER_DATA="$(printf '%s' "$OTHER_DATA" | sed -E 's#^/(run/desktop/mnt/host|host_mnt|mnt/host)/([a-zA-Z])/#\2:/#' |
                    awk '{ print toupper(substr($0, 1, 1)) substr($0, 2) }')" ;;
    /host_mnt/*) OTHER_DATA="${OTHER_DATA#/host_mnt}" ;;  # macOS (Docker Desktop meno recenti)
  esac
}

single_instance() {  # prima di avviare: il BioLoom partito da un'altra cartella si arresta solo con il consenso
  # (domanda; con --no-menu ci si ferma). $1 = cosa si annulla, per il messaggio
  local answer=""
  find_other
  [ -n "$OTHER_DIR" ] || return 0
  warn "È già in esecuzione BioLoom da un'altra cartella: $OTHER_DIR"
  warn "Le copie di BioLoom su questo computer usano gli stessi container: per continuare va arrestato"
  warn "(i calcoli in corso vengono interrotti; i suoi dati restano in ${OTHER_DATA:-quella cartella})."
  if [ "$NO_MENU" = 0 ]; then read -r -p "  Arrestarlo e continuare? [s/N] " answer || answer=""; fi
  case "$answer" in
    [sS]*) ;;
    *) fail "$1: resta in esecuzione il BioLoom di $OTHER_DIR. Per usare questa copia arrestalo prima ($LAUNCH_CMD arresta) e riprova." ;;
  esac
  warn "Arresto del BioLoom partito da $OTHER_DIR..."
  compose --profile qmean down --remove-orphans || fail "Arresto non riuscito (vedi i messaggi sopra)."
}

note_other() {  # stato, log, verifica: avviso se mostrano il BioLoom di un'altra cartella
  find_other
  [ -z "$OTHER_DIR" ] || warn "Il BioLoom in esecuzione è partito da un'altra cartella: $OTHER_DIR"
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
prosa_mac() {  # MAC della licenza ProSA nella cartella dati $1 (predefinita: data/)
  local lic="${1:-$DATA_DIR}/licenses/prosa/LicenseDB"
  [ -f "$lic" ] || return 0
  awk 'NF >= 4 && $3 ~ /^[0-9A-Fa-f]{12}$/ { m = tolower($3); printf "%s:%s:%s:%s:%s:%s\n", substr(m,1,2), substr(m,3,2), substr(m,5,2), substr(m,7,2), substr(m,9,2), substr(m,11,2); exit }' "$lic"
}

norm_mac() {  # MAC scritto a mano (server.env) nella forma aa:bb:cc:dd:ee:ff; vuoto se non è un MAC
  printf '%s\n' "$1" | tr -d '"'"'"':.-' | tr '[:upper:]' '[:lower:]' \
    | awk 'length($0) == 12 && $0 ~ /^[0-9a-f]+$/ { printf "%s:%s:%s:%s:%s:%s\n", substr($0,1,2), substr($0,3,2), substr($0,5,2), substr($0,7,2), substr($0,9,2), substr($0,11,2) }'
}

# La licenza ProSA vale per un computer alla volta: il primo computer che applica il MAC di LicenseDB la lega a sé
# in licenses/prosa/computer (una riga "<mac> <computer>" per licenza), che viaggia con la cartella dati; gli altri
# non applicano quel MAC finché la licenza non viene spostata (domanda o --sposta-prosa).
sha256_hex() {  # SHA-256 esadecimale dello standard input (vuoto se manca lo strumento)
  if command -v sha256sum >/dev/null 2>&1; then sha256sum | awk '{ print $1 }'
  elif command -v shasum >/dev/null 2>&1; then shasum -a 256 | awk '{ print $1 }'
  elif command -v openssl >/dev/null 2>&1; then openssl dgst -sha256 | awk '{ print $NF }'
  fi
}

computer_id() {  # 16 cifre esadecimali che identificano questo computer (vuoto se non si ottiene)
  local raw="" f
  case "$OS" in
    # /reg:64: da un Git Bash a 32 bit reg vedrebbe il registro a 32 bit, dove MachineGuid non c'è
    windows) raw="$(MSYS_NO_PATHCONV=1 MSYS2_ARG_CONV_EXCL='*' reg query 'HKLM\SOFTWARE\Microsoft\Cryptography' /v MachineGuid \
                      /reg:64 2>/dev/null | awk '$1 == "MachineGuid" { print $NF }')" ;;
    mac) raw="$(ioreg -rd1 -c IOPlatformExpertDevice 2>/dev/null | awk -F'"' '/IOPlatformUUID/ { print $4; exit }')" ;;
    linux)
      # WSL: il computer è Windows (stesso valore di Git Bash e PowerShell), non la distribuzione
      if grep -qi microsoft /proc/sys/kernel/osrelease 2>/dev/null && command -v reg.exe >/dev/null 2>&1; then
        raw="$(reg.exe query 'HKLM\SOFTWARE\Microsoft\Cryptography' /v MachineGuid /reg:64 2>/dev/null \
                 | awk '$1 == "MachineGuid" { print $NF }')"
      fi
      for f in /etc/machine-id /var/lib/dbus/machine-id; do
        [ -z "$raw" ] && [ -s "$f" ] && raw="$(head -n1 "$f")"
      done ;;
  esac
  raw="$(printf '%s' "$raw" | tr -d '\r' | sed 's/^[[:space:]]*//;s/[[:space:]]*$//' | tr '[:upper:]' '[:lower:]')"
  [ -n "$raw" ] || return 0
  printf 'bioloom-computer:%s' "$raw" | sha256_hex | cut -c1-16
}

# Nel file il MAC si confronta normalizzato (minuscole, solo cifre esadecimali): una riga corretta a mano con le
# maiuscole o senza i due punti vale come quella scritta dall'avviatore.
prosa_bind() {  # lega la licenza col MAC $1 al computer $2 nel file $3
  local tmp="$3.$$"
  ( umask 077
    { [ -f "$3" ] && tr -d '\r' < "$3" \
        | awk -v m="$1" 'BEGIN { gsub(/:/, "", m) } NF { k = tolower($1); gsub(/[^0-9a-f]/, "", k); if (k != m) print }'
      printf '%s %s\n' "$1" "$2"; } > "$tmp" ) \
    && mv -f "$tmp" "$3" || { rm -f "$tmp"; return 1; }
  chmod 600 "$3" 2>/dev/null || true
}

PROSA_STATE=""   # licenza ProSA: vuoto (nessuna), attivata, questo, spostata, altro, senza-id

prosa_check() {  # $1 MAC della licenza  $2 cartella dati  $3 1 = si può chiedere; vero se il MAC si può applicare
  local file="$2/licenses/prosa/computer" id bound="" answer=""
  id="$(computer_id)"
  if [ -z "$id" ]; then PROSA_STATE=senza-id; return 0; fi
  [ -f "$file" ] && bound="$(tr -d '\r' < "$file" \
    | awk -v m="$1" 'BEGIN { gsub(/:/, "", m) } { k = tolower($1); gsub(/[^0-9a-f]/, "", k) } k == m { print tolower($2); exit }')"
  if [ "$bound" = "$id" ]; then PROSA_STATE=questo; return 0; fi
  if [ -z "$bound" ]; then
    prosa_bind "$1" "$id" "$file" || warn "ProSA: impossibile registrare il computer della licenza in $file."
    PROSA_STATE=attivata; return 0
  fi
  warn "ProSA: la licenza è attiva su un altro computer (vale per un computer alla volta)."
  warn "Per usarla qui chiedi una chiave per questo computer, oppure spostala qui (l'altro computer la perde)."
  if [ "$MOVE_PROSA" = 1 ]; then
    answer=s
  elif [ "$3" = 1 ]; then
    read -r -p "  Spostare la licenza ProSA su questo computer? [s/N] " answer || answer=""
  else
    warn "Per spostarla qui rilancia con --sposta-prosa."
  fi
  case "$answer" in
    [sS]*) if prosa_bind "$1" "$id" "$file"; then PROSA_STATE=spostata; return 0; fi
           warn "ProSA: impossibile registrare lo spostamento in $file: licenza non spostata." ;;
  esac
  PROSA_STATE=altro
  return 1
}

random_mac() { printf '02:%02x:%02x:%02x:%02x:%02x\n' $((RANDOM % 256)) $((RANDOM % 256)) $((RANDOM % 256)) $((RANDOM % 256)) $((RANDOM % 256)); }

port_free() {  # $1 porta, $2 indirizzo (127.0.0.1): libera se lì nessun programma accetta connessioni
  local host="${2:-127.0.0.1}" rc=0
  case "$host" in
    # anche in locale un programma bloccato (coda delle connessioni piena) terrebbe ferma la connessione per minuti,
    # perché Linux scarta le richieste in più invece di rifiutarle: con timeout, dopo 3 s senza risposta, la porta è
    # occupata (una porta chiusa in locale rifiuta subito). Senza timeout (di solito su macOS) si prova senza limite;
    # su Windows non serve, e 3 s sarebbero pochi: rifiuta sempre, anche una porta chiusa dopo circa 2 s
    127.*|::1)
      if [ "$OS" != windows ] && command -v timeout >/dev/null 2>&1; then
        # shellcheck disable=SC2016 # $0 e $1 sono quelli della shell figlia
        { timeout 3 bash -c 'exec 3<>"/dev/tcp/$0/$1"' "$host" "$1"; } 2>/dev/null || rc=$?
        # 0: un programma accetta; 124, o 128 e oltre (timeout di busybox, ucciso da un segnale): nessuna risposta
        [ "$rc" != 0 ] && [ "$rc" != 124 ] && [ "$rc" -lt 128 ]
      else
        ! (exec 3<>"/dev/tcp/$host/$1") 2>/dev/null
      fi ;;
    # un indirizzo di rete che non risponde terrebbe ferma la connessione per minuti: senza timeout decide Docker
    *) command -v timeout >/dev/null 2>&1 || return 0
       # shellcheck disable=SC2016 # $0 e $1 sono quelli della shell figlia
       ! timeout 3 bash -c 'exec 3<>"/dev/tcp/$0/$1"' "$host" "$1" 2>/dev/null ;;
  esac
}

valid_port() {  # stampa $1 se è una porta TCP (da 1 a 65535), senza zeri iniziali; uscita 1 altrimenti
  case "$1" in ""|*[!0-9]*) return 1 ;; esac
  [ "${#1}" -le 5 ] || return 1
  set -- "$((10#$1))"
  [ "$1" -ge 1 ] && [ "$1" -le 65535 ] || return 1
  printf '%s' "$1"
}

probe_host() {  # indirizzo dove guardare se la porta di BIOLOOM_BIND=$1 è occupata: per tutte le interfacce (0.0.0.0)
  # basta 127.0.0.1, dove risponde anche chi ascolta su tutte
  case "$1" in ""|0.0.0.0|localhost|::|"[::]") printf '127.0.0.1' ;; "[::1]") printf '::1' ;; *) printf '%s' "$1" ;; esac
}

port_hint() {  # come vedere quali programmi ascoltano sulle porte, per i messaggi
  case "$OS" in
    windows) printf 'netstat -ano | findstr LISTENING, il numero in fondo è il processo in Gestione attività' ;;
    mac) printf 'lsof -nP -iTCP -sTCP:LISTEN' ;;
    *) printf 'ss -ltnp' ;;
  esac
}

own_port() {  # porta pubblicata dal BioLoom in esecuzione (vuota se non è in esecuzione): per lui è libera, compose
  # ricrea il container. $1 = porta da supporre se compose non la dice
  local p
  compose ps --status running --services 2>/dev/null | grep -qx bioloom || return 0
  p="$(compose port bioloom 8765 2>/dev/null | tail -n1 | tr -d '\r')"; p="${p##*:}"
  case "$p" in ""|*[!0-9]*) p="$1" ;; esac
  printf '%s' "$p"
}

port_range() { local p="$1"; while [ "$p" -le "$2" ]; do printf '%s\n' "$p"; p=$((p + 1)); done; }   # da $1 a $2

pick_port() {  # prima porta libera: $1 (l'ultima usata), poi le seguenti fino a 8800 e da 8765; $2 = porta del BioLoom
  # in esecuzione, libera per lui. Uscita 1 se sono tutte occupate
  local p list
  if [ "$1" -ge 8765 ] && [ "$1" -le 8800 ]; then
    list="$1 $(port_range $(($1 + 1)) 8800) $(port_range 8765 $(($1 - 1)))"
  else
    list="$1 $(port_range 8765 8800)"
  fi
  for p in $list; do
    if [ "$p" = "$2" ] || port_free "$p"; then printf '%s' "$p"; return 0; fi
  done
  return 1
}

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

url_host() { case "$1" in *:*) printf '[%s]' "$1" ;; *) printf '%s' "$1" ;; esac; }   # IPv6 fra [] negli URL

health_ok() {  # $1 porta  $2 indirizzo: vero se BioLoom risponde a /api/health. Senza curl una richiesta HTTP scritta
  # a mano: la sola connessione non basta, perché la porta pubblicata da Docker la accetta anche prima che BioLoom ascolti
  if command -v curl >/dev/null 2>&1; then
    # --noproxy: curl manderebbe al proxy di http_proxy anche 127.0.0.1 (o l'IP del server), comune sui server degli
    # enti; --max-time come il read -t 5 qui sotto, così un proxy irraggiungibile non blocca ogni tentativo
    curl -fsS --noproxy '*' --max-time 5 "http://$(url_host "$2"):$1/api/health" >/dev/null 2>&1
  else
    (exec 3<>"/dev/tcp/$2/$1" || exit 1
     printf 'GET /api/health HTTP/1.0\r\nHost: %s:%s\r\n\r\n' "$(url_host "$2")" "$1" >&3
     IFS= read -r -t 5 line <&3 || exit 1
     case "$line" in "HTTP/1."?" 200"*) exit 0 ;; *) exit 1 ;; esac) 2>/dev/null
  fi
}

wait_healthy() {  # $1 porta  $2 indirizzo (127.0.0.1)  $3 1 = smette di aspettare se il container di BioLoom si ferma.
  # Uscita: 0 risponde; 1 nessuna risposta in 2 minuti; 2 container fermo; 3 container che Docker riavvia (restart di
  # compose.yml, BIOLOOM_RESTART): BioLoom è uscito con un errore e riparte, di solito per ripeterlo. I riavvii si
  # contano dal primo controllo: un container che compose non ha ricreato può averne di vecchi
  local port="$1" host="${2:-127.0.0.1}" watch="${3:-0}" id="" state="" status count policy restarts=""
  for _ in $(seq 1 120); do
    health_ok "$port" "$host" && return 0
    if [ "$watch" = 1 ]; then
      [ -n "$id" ] || id="$(compose ps -a -q bioloom 2>/dev/null | head -n1 | tr -d '\r')"
      state=""
      [ -n "$id" ] && state="$(docker inspect --format \
        '{{.State.Status}} {{.RestartCount}} {{.HostConfig.RestartPolicy.Name}}' "$id" 2>/dev/null | tr -d '\r')"
      if [ -n "$state" ]; then
        read -r status count policy <<< "$state"
        case "$status" in
          exited|dead) case "$policy" in ""|no) return 2 ;; *) return 3 ;; esac ;;   # fra un'uscita e il riavvio
          restarting) return 3 ;;
        esac
        [ -n "$restarts" ] || restarts="$count"
        [ "$count" = "$restarts" ] || return 3
      fi
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

apple_silicon() {  # Mac con processore Apple (uname -m dice x86_64 se la shell gira in Rosetta: lo dice sysctl)
  [ "$OS" = mac ] || return 1
  [ "$(uname -m)" = arm64 ] || [ "$(sysctl -n hw.optional.arm64 2>/dev/null)" = 1 ]
}

loopback() { case "$1" in ""|127.*|localhost|::1|"[::1]") return 0 ;; *) return 1 ;; esac; }   # BIOLOOM_BIND locale

# ------------------------------------------------------------------------------------------- azioni
start_local() {
  title "BioLoom $VERSION"
  ensure_docker
  mkdir -p "$DATA_DIR" || fail "Impossibile creare la cartella dati $DATA_DIR."
  if ! touch "$DATA_DIR/.scrittura" 2>/dev/null; then fail "La cartella dati $DATA_DIR non è scrivibile."; fi
  rm -f "$DATA_DIR/.scrittura"
  ensure_env
  single_instance "Avvio annullato"
  read_saved
  if [ "$UNICLUST_CHOICE_SET" = 1 ]; then
    SAVED_UNICLUST="$UNICLUST_CHOICE"
    SAVED_MANUAL="${UNICLUST_CHOICE:+1}"
  fi
  UNICLUST_MANUAL="$SAVED_MANUAL"
  find_databases
  local mac port run_port
  mac="$(prosa_mac)"; MAC_LIC=""; PROSA_STATE=""
  if [ -n "$mac" ] && prosa_check "$mac" "$DATA_DIR" "$((1 - NO_MENU))"; then
    MAC_LIC=1
  elif [ -n "$SAVED_MAC" ] && [ -z "$SAVED_MAC_LIC" ]; then
    mac="$SAVED_MAC"   # senza licenza (o con la licenza su un altro computer): il MAC locale di questo computer
  else
    mac="$(random_mac)"
  fi
  port="$(valid_port "$SAVED_PORT")" || port=8765
  ensure_env
  run_port="$(own_port "$port")"
  if [ -n "$REQ_PORT" ]; then
    port="$(valid_port "$REQ_PORT")" || fail "BIOLOOM_PORT=$REQ_PORT non è una porta valida (un numero da 1 a 65535)."
    [ "$port" = "$run_port" ] || port_free "$port" \
      || fail "La porta $port (BIOLOOM_PORT) è già usata da un altro programma: chiudilo (per vedere quale: $(port_hint)) oppure scegli un'altra porta con BIOLOOM_PORT."
  elif ! port="$(pick_port "$port" "$run_port")"; then
    port="$(valid_port "$SAVED_PORT")" || port=8765
    if [ "$port" -ge 8765 ] && [ "$port" -le 8800 ]; then port=""; fi   # già nell'intervallo: non si ripete
    fail "Nessuna porta libera per BioLoom: ${port:+la $port e }le porte da 8765 a 8800 sono tutte usate da altri programmi. Chiudine uno (per vedere quali: $(port_hint)) oppure scegli una porta libera, per esempio: BIOLOOM_PORT=9000 $LAUNCH_CMD"
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
  check_image
  if [ -n "$UNICLUST" ] && ! ensure_image "bioloom-qmean:$VERSION" "bioloom-qmean-$VERSION.tar" qmean; then
    warn "Immagine QMEAN non disponibile: QMEAN locale resta spento."
    sed -i.bak '/^UNICLUST_PATH=/d;/^COMPOSE_PROFILES=/d;/^QMTL_PATH=/d;/^BIOLOOM_QMEAN_URL=/d' "$ENV_FILE" && rm -f "$ENV_FILE.bak"
    UNICLUST=""
  fi
  say "${c_dim}Avvio dei container...${c_off}"
  [ -z "$UNICLUST" ] && compose --profile qmean rm -sf qmean >/dev/null 2>&1
  compose up -d --remove-orphans || fail "Docker non è riuscito ad avviare BioLoom (vedi i messaggi sopra)."
  wait_healthy "$port" || fail "BioLoom non risponde: controlla con $LAUNCH_CMD log"
  local url="http://127.0.0.1:$port" hint=""
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
  case "$PROSA_STATE" in
    attivata) say "ProSA:       licenza attivata su questo computer, MAC applicato" ;;
    questo)   say "ProSA:       licenza di questo computer, MAC applicato" ;;
    spostata) say "ProSA:       licenza spostata su questo computer, MAC applicato" ;;
    senza-id) say "ProSA:       licenza trovata, MAC applicato (computer non identificato: nessun controllo)" ;;
    altro)    if [ "$NO_MENU" = 1 ]; then hint="per spostarla qui: --sposta-prosa"; else hint="R per spostarla qui"; fi
              say "${c_yellow}ProSA:       licenza attiva su un altro computer, MAC non applicato ($hint)${c_off}" ;;
    *)        say "ProSA:       nessuna licenza (si carica da Impostazioni > Licenze)" ;;
  esac
  # le immagini sono amd64 (MODELLER, ProSA, QMEAN esistono solo per x86); ProSA è x86 a 32 bit, che Rosetta 2 non esegue
  if apple_silicon; then
    say "Mac ARM:     container amd64 in emulazione, più lenti; ProSA (x86 a 32 bit) potrebbe non funzionare"
    say "             in Docker Desktop si consiglia: Use Rosetta for x86_64/amd64 emulation on Apple Silicon"
  fi
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
  local data="$DATA_DIR"
  use_running_env; ensure_env
  if [ "$SERVER_MODE" = 1 ]; then
    data="$(server_data)"; [ -d "$data" ] && data="$(cd "$data" && pwd)"
  fi
  # BioLoom partito da un'altra copia: si arresta lo stesso (i container sono gli stessi, "Arresta BioLoom" non chiede
  # e la cartella di quella copia può non esserci più, per esempio una chiavetta tolta), dicendo quale è
  find_other
  if [ -n "$OTHER_DIR" ]; then
    warn "Il BioLoom in esecuzione è partito da un'altra cartella: $OTHER_DIR"
    data="${OTHER_DATA:-$OTHER_DIR}"
  fi
  warn "Arresto di BioLoom (i calcoli in corso vengono interrotti)..."
  compose --profile qmean down
  printf '  %sBioLoom è arrestato. I dati restano in %s.%s\n' "$c_green" "$data" "$c_off"
}

portable_data() {  # copia di data/ nella copia portatile; gli argomenti sono esclusioni in più
  tar -C "$BASE" --exclude=.scratch --exclude=.localcache "$@" -cf - data | tar -C "$DEST" -xf -
}

leftover() {  # primo file nei percorsi dati, esclusi README.md e i file nascosti o di sistema (.DS_Store, ._README.md
  # che macOS crea sulle chiavette exFAT, desktop.ini, Thumbs.db): servono a riconoscere i resti di una copia precedente
  find "$@" -type f ! -name README.md ! -name '.*' ! -name desktop.ini ! -name Thumbs.db 2>/dev/null | head -n1
}

prepare_portable() {
  [ -n "$DEST" ] || { [ "$NO_MENU" = 1 ] && fail "Indica la cartella di destinazione."; read -r -p "  Cartella di destinazione: " DEST; }
  [ -n "$DEST" ] || fail "Nessuna destinazione."
  ensure_docker
  mkdir -p "$DEST" && DEST="$(cd "$DEST" && pwd)"
  # -ef confronta il file, non la stringa: vale anche con maiuscole diverse (APFS, NTFS) e con link simbolici
  [ "$DEST" -ef "$BASE" ] && fail "La destinazione è la cartella di BioLoom stessa: scegline un'altra."
  title "Preparazione della copia portatile in $DEST"
  ensure_env
  # Prima le immagini: se quella di BioLoom non corrisponde al codice in programma/ la si aggiorna, e se non si può
  # la copia si ferma qui (una copia con immagine vecchia e codice nuovo non deve uscire)
  mkdir -p "$DEST/immagini"
  local spec name tarname service
  for spec in "bioloom:$VERSION bioloom-$VERSION.tar bioloom" "bioloom-qmean:$VERSION bioloom-qmean-$VERSION.tar qmean"; do
    read -r name tarname service <<< "$spec"
    [ "$service" = qmean ] && [ "$NO_QMEAN" = 1 ] && continue
    ensure_image "$name" "$tarname" "$service" || { warn "Immagine $name non disponibile: non inclusa."; continue; }
    if [ "$service" = bioloom ] && image_outdated "$name"; then
      warn "L'immagine $name non corrisponde al codice in programma/: la aggiorno prima di salvarla."
      update_bioloom_image || fail "Immagine $name non aggiornata: la copia avrebbe un'immagine diversa dal codice. Copia interrotta."
    fi
    say "Salvataggio dell'immagine $name (qualche minuto)..."
    docker save -o "$DEST/immagini/$tarname" "$name" || fail "Salvataggio di $name non riuscito."
  done
  local f
  # LICENSE e THIRD_PARTY_NOTICES.md: licenza di BioLoom e avvisi (con l'offerta del sorgente) del software nelle immagini
  for f in "Avvia BioLoom.bat" "Arresta BioLoom.bat" "Avvia BioLoom.command" "Arresta BioLoom.command" avvia-bioloom.sh \
           README.md LEGGIMI.txt LICENSE THIRD_PARTY_NOTICES.md .gitattributes .gitignore; do
    [ -e "$BASE/$f" ] && cp -p "$BASE/$f" "$DEST/"
  done
  # Pacchetti con licenza (PROCHECK, ProSA: vedi docker/vendor/README.md) mai nella copia. vendor/ resta con il
  # README e le sottocartelle vuote perché il Dockerfile la copia (COPY docker/vendor/prosa/ fallisce se manca):
  # senza pacchetti la build prosegue e BioLoom mostra quegli strumenti come "non installato".
  tar -C "$BASE" --exclude=node_modules --exclude=dist --exclude=__pycache__ --exclude=.pytest_cache \
      --exclude=programma/docker/vendor -cf - Guida programma | tar -C "$DEST" -xf - || fail "Copia dei programmi non riuscita."
  mkdir -p "$DEST/programma/docker/vendor/procheck" "$DEST/programma/docker/vendor/prosa" || fail "Copia dei programmi non riuscita."
  [ -f "$ROOT/docker/vendor/README.md" ] && cp -p "$ROOT/docker/vendor/README.md" "$DEST/programma/docker/vendor/"
  # Chiavi di licenza, posta.env (password SMTP) e chiave d'accesso del server: solo con --con-licenze
  if [ "$NO_DATA" = 0 ] && [ -d "$DATA_DIR" ]; then
    if [ "$WITH_LICENSES" = 1 ]; then
      say "Copia dei dati (progetti, risultati, licenze, database)..."
      portable_data || fail "Copia dei dati non riuscita."
    else
      say "Copia dei dati (progetti, risultati, database; senza licenze e chiave d'accesso)..."
      portable_data --exclude=data/licenses --exclude=data/access_key || fail "Copia dei dati non riuscita."
    fi
  fi
  # resti di una copia fatta con una versione precedente, che copiava pacchetti e chiavi: si segnalano, non si cancellano
  local left=""
  [ -n "$(leftover "$DEST/programma/docker/vendor")" ] && left="pacchetti con licenza in programma/docker/vendor"
  if [ "$WITH_LICENSES" = 0 ] && [ -n "$(leftover "$DEST/data/licenses" "$DEST/data/access_key")" ]; then
    left="${left:+$left, }licenze o chiave d'accesso in data"
  fi
  title "Copia portatile pronta: $DEST"
  say "Sul computer di destinazione serve solo Docker: doppio clic su 'Avvia BioLoom' (.bat, .command o avvia-bioloom.sh)."
  if [ "$NO_DATA" = 1 ] || [ ! -d "$DATA_DIR" ]; then
    warn "Dati e licenze non copiati: sul computer di destinazione le licenze si inseriscono da Impostazioni > Licenze."
  elif [ "$WITH_LICENSES" = 1 ]; then
    warn "La copia contiene chiavi di licenza e password personali (data/licenses, data/access_key): non darla ad altri."
  else
    warn "Licenze non copiate: sul computer di destinazione si inseriscono da Impostazioni > Licenze (MODELLER, ProSA)"
    warn "e posta.env, se serve, va ricreato in data/licenses. Per includerle rifai la copia con --con-licenze."
  fi
  [ -n "$left" ] && warn "Nella destinazione restano file di una copia precedente ($left): cancellali se la copia va ad altri."
  warn "Le immagini in 'immagini' contengono MODELLER e, se installati, PROCHECK e ProSA II: la copia è per uso"
  warn "personale (o del gruppo, se le licenze lo consentono) e non va redistribuita."
}

start_server() {
  local server_env="$BASE/server.env"
  ensure_docker
  if [ ! -f "$server_env" ]; then
    local key
    key="$(LC_ALL=C tr -dc 'A-Za-z0-9' < /dev/urandom | head -c 24)"
    sed "s/^BIOLOOM_ACCESS_KEY=.*/BIOLOOM_ACCESS_KEY=$key/" "$ROOT/launcher/server.env.example" > "$server_env"
    chmod 600 "$server_env"
    title "Creato $server_env"
    say "Chiave d'accesso generata: $key"
    say "BioLoom ascolterà solo su 127.0.0.1 (BIOLOOM_BIND): dagli altri computer con un tunnel SSH o un reverse proxy HTTPS."
    say "Controlla i percorsi (cartella dati, UniClust30) e la porta, poi rilancia: $LAUNCH_CMD server"
    exit 0
  fi
  ENV_FILE="$server_env"
  check_env_version
  single_instance "Avvio annullato"
  # valori letti come li legge compose (virgolette, commenti a fine riga); percorsi relativi a compose.yml
  local uniclust data mac lic_mac port bind origin
  uniclust="$(compose_path "$(env_val UNICLUST_PATH "$server_env")")"
  data="$(server_data)"
  mkdir -p "$data" 2>/dev/null
  # porta fissa: se un altro programma la usa ci si ferma qui, prima di caricare o costruire le immagini, con un
  # messaggio chiaro (compose fallirebbe dopo, con "address already in use" o "ports are not available")
  bind="${BIOLOOM_BIND:-$(env_val BIOLOOM_BIND "$server_env")}"   # per compose la variabile della shell prevale
  if [ -n "$REQ_PORT" ]; then
    origin="BIOLOOM_PORT"; port="$REQ_PORT"
  else
    origin="BIOLOOM_PORT in server.env"; port="$(env_val BIOLOOM_PORT "$server_env")"; port="${port:-8765}"
  fi
  port="$(valid_port "$port")" || fail "$origin non è una porta valida (un numero da 1 a 65535): ${REQ_PORT:-$(env_val BIOLOOM_PORT "$server_env")}"
  [ -z "$REQ_PORT" ] || export BIOLOOM_PORT="$port"   # come prima: la variabile della shell prevale su server.env
  [ "$port" = "$(own_port "$port")" ] || port_free "$port" "$(probe_host "$bind")" \
    || fail "La porta $port ($origin) è già usata da un altro programma${bind:+ su $bind}: chiudilo (per vedere quale: $(port_hint)) oppure cambia $origin e rilancia $LAUNCH_CMD server"
  # ProSA: se BIOLOOM_MAC è il MAC della licenza nella cartella dati, vale un computer alla volta come sul desktop;
  # con la licenza su un altro computer il container prende il MAC predefinito di compose.yml (come senza licenza)
  mac="$(norm_mac "$(env_val BIOLOOM_MAC "$server_env")")"
  lic_mac="$(prosa_mac "$data")"; PROSA_STATE=""
  if [ -n "$lic_mac" ] && [ "$mac" = "$lic_mac" ] && ! prosa_check "$lic_mac" "$data" 0; then
    export BIOLOOM_MAC=02:42:b1:00:00:01
  fi
  ensure_image "bioloom:$VERSION" "bioloom-$VERSION.tar" bioloom || fail "Immagine BioLoom non disponibile."
  check_image 1
  if [ -n "$uniclust" ]; then
    [ -n "$(uniclust_below "$uniclust")" ] || fail "UNICLUST_PATH in server.env non contiene database UniClust30/UniRef30 (entro 3 livelli): $uniclust"
    ensure_image "bioloom-qmean:$VERSION" "bioloom-qmean-$VERSION.tar" qmean || fail "Immagine QMEAN non disponibile."
    COMPOSE_PROFILES=qmean BIOLOOM_QMEAN_URL=http://qmean:8080 compose up -d --remove-orphans || fail "Avvio non riuscito."
  else
    compose up -d --remove-orphans || fail "Avvio non riuscito."
  fi
  # compose up non aspetta BioLoom: un errore all'avvio (per esempio BIOLOOM_ACCESS_KEY troppo corta, il backend esce
  # con il motivo nel log) passerebbe inosservato. Con BIOLOOM_BIND=0.0.0.0 si interroga 127.0.0.1 (probe_host)
  local host rc=0
  host="$(probe_host "$bind")"
  say "${c_dim}Attesa della risposta di BioLoom su http://$(url_host "$host"):$port ...${c_off}"
  wait_healthy "$port" "$host" 1 || rc=$?
  if [ "$rc" != 0 ]; then
    warn "Ultime righe del log di BioLoom:"
    compose logs --tail 30 bioloom
    case "$rc" in
      1) fail "BioLoom non risponde su http://$(url_host "$host"):$port dopo 2 minuti: il motivo è di solito nelle righe qui sopra (tutto il log: $LAUNCH_CMD log)." ;;
      2) fail "BioLoom si è fermato durante l'avvio: il motivo è nelle righe qui sopra (spesso un valore di server.env, per esempio BIOLOOM_ACCESS_KEY più corta di 16 caratteri). Correggilo e rilancia $LAUNCH_CMD server" ;;
      *) fail "BioLoom si è fermato durante l'avvio: il motivo è nelle righe qui sopra (spesso un valore di server.env, per esempio BIOLOOM_ACCESS_KEY più corta di 16 caratteri). Correggilo e rilancia $LAUNCH_CMD server; intanto Docker continua a riavviarlo (BIOLOOM_RESTART), per fermarlo: $LAUNCH_CMD arresta" ;;
    esac
  fi
  title "BioLoom (server) avviato sulla porta $port"
  say "Accesso protetto dalla chiave in server.env (BIOLOOM_ACCESS_KEY). Per HTTPS usa un reverse proxy (vedi Guida/5 - Chiavetta e server.md)."
  if loopback "$bind"; then
    say "Raggiungibile solo da questo server; dagli altri computer con un tunnel SSH: ssh -N -L $port:127.0.0.1:$port utente@server"
  else
    # la porta pubblicata da Docker passa dalle sue regole NAT, prima di quelle di firewalld o ufw
    warn "BIOLOOM_BIND=$bind: BioLoom è raggiungibile dalla rete in HTTP, con chiave d'accesso e cookie in chiaro, e la"
    warn "porta pubblicata da Docker scavalca il firewall (firewalld, ufw). Meglio BIOLOOM_BIND=127.0.0.1 con un reverse"
    warn "proxy HTTPS o un tunnel SSH (Guida/5 - Chiavetta e server.md)."
  fi
  case "$PROSA_STATE" in
    attivata) say "ProSA: licenza attivata su questo computer, MAC applicato." ;;
    spostata) say "ProSA: licenza spostata su questo computer, MAC applicato." ;;
    altro)    warn "ProSA: licenza attiva su un altro computer, MAC non applicato (per spostarla qui: --sposta-prosa)." ;;
  esac
}

ENV_VERSION_CHECKED=0
check_env_version() {  # server.env di una versione precedente: BIOLOOM_VERSION non conta più, vale programma/launcher/VERSION
  local v
  [ "$ENV_VERSION_CHECKED" = 1 ] && return 0
  ENV_VERSION_CHECKED=1
  v="$(env_val BIOLOOM_VERSION "$ENV_FILE")"
  [ -z "$v" ] || [ "$v" = "$VERSION" ] \
    || warn "$(basename "$ENV_FILE"): BIOLOOM_VERSION=$v viene ignorata, si usa la versione $VERSION (programma/launcher/VERSION)."
}

image_ids() { printf '%s %s' "$(docker image inspect --format '{{.Id}}' "bioloom:$VERSION" 2>/dev/null)" \
                               "$(docker image inspect --format '{{.Id}}' "bioloom-qmean:$VERSION" 2>/dev/null)"; }

update_all() {  # azione aggiorna: immagini al codice in programma/, poi riavvio se BioLoom è attivo, con il file
  # (server.env o .env.locale) con cui era partito
  local running=0 ok=1 uniclust="" before
  ensure_docker
  use_running_env; ensure_env
  title "Aggiornamento delle immagini di BioLoom $VERSION"
  if [ "$SERVER_MODE" = 1 ]; then
    check_env_version
    uniclust="$(env_val UNICLUST_PATH "$ENV_FILE")"
  else
    read_saved; uniclust="$SAVED_UNICLUST"
    # non si sa quale dei due file usa BioLoom: QMEAN è in uso se UniClust è configurato in uno dei due
    [ -z "$SERVER_MODE" ] && [ -z "$uniclust" ] && uniclust="$(env_val UNICLUST_PATH "$BASE/server.env")"
  fi
  compose ps --status running --services 2>/dev/null | grep -qx bioloom && running=1
  before="$(image_ids)"
  if image_current "bioloom:$VERSION"; then
    say "${c_green}L'immagine bioloom:$VERSION corrisponde già al codice in programma/.${c_off}"
  else
    update_bioloom_image || ok=0
  fi
  update_qmean_image "$uniclust"
  if [ "$running" = 0 ]; then
    [ "$(image_ids)" = "$before" ] || say "BioLoom non è in esecuzione: le immagini aggiornate verranno usate al prossimo avvio."
  elif ! stale_running; then
    if [ "$ok" = 1 ]; then say "BioLoom è in esecuzione e usa già le immagini attuali: nessun riavvio."
    else say "BioLoom resta in esecuzione con l'immagine che aveva: nessun riavvio."; fi
  elif [ -z "$SERVER_MODE" ]; then
    warn "Non so se BioLoom è partito dal desktop (.env.locale) o da server (server.env): riavvialo tu per usare le"
    warn "immagini aggiornate ($LAUNCH_CMD arresta, poi di nuovo l'avvio che usi di solito)."
  elif [ "$SERVER_MODE" = 1 ]; then
    single_instance "Riavvio annullato"
    warn "Riavvio di BioLoom con le immagini aggiornate e le impostazioni di server.env (i calcoli in corso vengono interrotti)..."
    start_server
  else
    single_instance "Riavvio annullato"
    if [ -n "$OTHER_DIR" ]; then
      # arrestato il BioLoom di un'altra copia: questa si avvia come dal desktop (porta, licenze, UniClust), perché il
      # suo .env.locale può non venire da un avvio
      start_local
    else
      warn "Riavvio di BioLoom con le immagini aggiornate (i calcoli in corso vengono interrotti)..."
      compose up -d --remove-orphans || fail "Docker non è riuscito a riavviare BioLoom (vedi i messaggi sopra)."
      wait_healthy "${SAVED_PORT:-8765}" || fail "BioLoom non risponde: controlla con $LAUNCH_CMD log"
      title "BioLoom è pronto: http://127.0.0.1:${SAVED_PORT:-8765}"
    fi
  fi
  [ "$ok" = 1 ] || fail "L'immagine bioloom:$VERSION non è stata aggiornata (vedi i messaggi sopra)."
}

# Server con Podman ma senza compose (per esempio RHEL 8, Podman 2.x): le stesse azioni con podman run. L'avvio dal
# desktop e aggiorna senza server.env finirebbero su "docker compose" con errori poco chiari: si dice cosa fare
if command -v podman >/dev/null 2>&1 && ! docker compose version >/dev/null 2>&1; then
  podman_opts=""; [ "$MOVE_PROSA" = 1 ] && podman_opts="--sposta-prosa"
  [ "$NO_MENU" = 1 ] && podman_opts="$podman_opts --no-menu"
  # shellcheck disable=SC2086 # podman_opts va diviso negli spazi: una parola per opzione
  case "$ACTION" in
    server) exec bash "$ROOT/launcher/bioloom-podman.sh" avvia $podman_opts ;;
    # anche senza server.env (copia nuova, per esempio): i container hanno nomi fissi, quindi arresta ferma il BioLoom
    # acceso da qualunque cartella sia partito, come con Docker; per queste azioni bastano i predefiniti di server.env
    arresta|stato|log|verifica) exec bash "$ROOT/launcher/bioloom-podman.sh" "$ACTION" $podman_opts ;;
    aggiorna)
      # senza server.env il riavvio non saprebbe come avviare BioLoom: bioloom-podman.sh creerebbe il file e uscirebbe
      [ -f "$BASE/server.env" ] && exec bash "$ROOT/launcher/bioloom-podman.sh" aggiorna $podman_opts
      fail "Qui c'è Podman senza compose e BioLoom non è ancora configurato da server (manca server.env): crealo con $LAUNCH_CMD server, poi rilancia $LAUNCH_CMD aggiorna (Guida/5 - Chiavetta e server.md)." ;;
    avvia)
      # può essere un desktop (Fedora, Podman Desktop sul Mac, Docker senza il plugin compose), dove serve Docker con
      # compose, o un server con solo Podman, dove BioLoom si avvia con "server": si danno le due strade
      warn "Qui c'è Podman ma non Docker con compose, che serve per avviare BioLoom dal desktop:"
      if [ "$OS" = mac ]; then
        warn "installa Docker Desktop per Mac (https://www.docker.com/products/docker-desktop/; Guida/1 - Primo avvio.md)."
        docker_desktop_note
      else
        warn "installa Docker Desktop, o Docker Engine con il plugin compose (https://docs.docker.com/engine/install/;"
        warn "Guida/1 - Primo avvio.md)."
      fi
      warn "Su un server con solo Podman BioLoom si avvia invece da server: $LAUNCH_CMD server"
      warn "(la prima volta crea server.env; Guida/5 - Chiavetta e server.md)."
      exit 1 ;;
  esac
fi

case "$ACTION" in
  avvia) start_local ;;
  arresta) stop_all ;;
  stato) use_running_env; ensure_env; note_other; compose --profile qmean ps ;;
  log) use_running_env; ensure_env; note_other; compose logs --tail 200 ;;
  verifica) use_running_env; ensure_env; note_other; compose exec bioloom python -m bioloom.selftest ;;
  prepara) prepare_portable ;;
  server) start_server ;;
  aggiorna) update_all ;;
esac
