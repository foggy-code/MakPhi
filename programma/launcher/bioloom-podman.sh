#!/usr/bin/env bash
# Avvio di BioLoom su un server con Podman ma senza compose (per esempio RHEL 8 con Podman 2.x, senza root).
# Stessi container, immagini e server.env di compose.yml, avviati con "podman run".
#
#   bioloom-podman.sh avvia      crea server.env alla prima esecuzione, poi avvia (o riavvia) i container
#   bioloom-podman.sh arresta | stato | log | verifica
#   bioloom-podman.sh aggiorna   porta le immagini al codice in programma/ e, se BioLoom è attivo, lo riavvia
#   bioloom-podman.sh servizio   unità systemd dell'utente: riavvio con il server (serve il linger)
#   opzioni: --sposta-prosa      sposta su questo server la licenza ProSA attiva su un altro computer
#            --no-menu           senza domande (per script): con BioLoom in esecuzione da un'altra cartella si ferma
#
# QMEAN locale (UNICLUST_PATH in server.env) gira in un secondo container che condivide la rete di quello di
# BioLoom: BioLoom lo raggiunge su http://127.0.0.1:8080. PODMAN=docker usa la CLI di Docker (per le prove).
set -u

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"   # programma/
BASE="$(cd "$ROOT/.." && pwd)"                               # cartella di BioLoom
ENV_FILE="$BASE/server.env"
IMAGES_DIR="$BASE/immagini"
PODMAN="${PODMAN:-podman}"
ACTION="avvia"; MOVE_PROSA=0; NO_MENU=0
while [ $# -gt 0 ]; do
  case "$1" in
    --sposta-prosa) MOVE_PROSA=1 ;;
    --no-menu) NO_MENU=1 ;;
    *) ACTION="$1" ;;
  esac
  shift
done

case "$(uname -s)" in
  Darwin) OS=mac ;;
  Linux) OS=linux ;;
  MINGW*|MSYS*|CYGWIN*) OS=windows ;;
  *) OS=other ;;
esac

c_cyan=$'\033[36m'; c_yellow=$'\033[33m'; c_red=$'\033[31m'; c_green=$'\033[32m'; c_off=$'\033[0m'
say()   { printf '  %s\n' "$*"; }
title() { printf '\n  %s%s%s\n' "$c_cyan" "$*" "$c_off"; }
warn()  { printf '  %s%s%s\n' "$c_yellow" "$*" "$c_off"; }
fail()  { printf '\n  %s[!] %s%s\n\n' "$c_red" "$*" "$c_off"; exit 1; }

command -v "$PODMAN" >/dev/null 2>&1 || fail "$PODMAN non è installato."

val() {  # valore di una variabile di server.env letto come fa docker compose (ultima riga, senza spazi ai lati,
  # virgolette e commento a fine riga " # ..." fuori dalle virgolette); $2 = predefinito
  local v=""
  [ -f "$ENV_FILE" ] && v="$(sed -n "s/^$1=//p" "$ENV_FILE" | tail -n1 | tr -d '\r' | sed 's/^[[:space:]]*//')"
  case "$v" in
    \"*) v="${v#\"}"; v="${v%%\"*}" ;;
    \'*) v="${v#\'}"; v="${v%%\'*}" ;;
    *) v="$(printf '%s' "$v" | sed 's/[[:space:]]#.*$//;s/[[:space:]]*$//')" ;;
  esac
  printf '%s' "${v:-${2:-}}"
}

VERSION="$(head -n1 "$ROOT/launcher/VERSION" 2>/dev/null | tr -d '[:space:]')"   # unica fonte: prevale su server.env
[ -n "$VERSION" ] || fail "Manca il file programma/launcher/VERSION (versione di BioLoom): la cartella programma è incompleta, copiala di nuovo."
ROOTLESS=0
[ "$PODMAN" = podman ] && [ "$(id -u)" != 0 ] && ROOTLESS=1

ENV_VERSION_CHECKED=0
check_env_version() {  # server.env di una versione precedente: BIOLOOM_VERSION non conta più
  local v
  [ "$ENV_VERSION_CHECKED" = 1 ] && return 0
  ENV_VERSION_CHECKED=1
  v="$(val BIOLOOM_VERSION)"
  [ -z "$v" ] || [ "$v" = "$VERSION" ] \
    || warn "server.env: BIOLOOM_VERSION=$v viene ignorata, si usa la versione $VERSION (programma/launcher/VERSION)."
}

have_image() { "$PODMAN" image inspect "$1" >/dev/null 2>&1; }

build_image() {  # $1 immagine  $2 Dockerfile  $3 contesto (all'immagine di BioLoom passa la versione, come compose.yml,
  # e BUILDPLATFORM, che i buildah vecchi non valorizzano da sé: il server è amd64)
  [ -f "$2" ] || return 1
  case "$1" in
    bioloom:*) "$PODMAN" build --build-arg "BIOLOOM_VERSION=$VERSION" --build-arg BUILDPLATFORM=linux/amd64 -t "$1" -f "$2" "$3" ;;
    *) "$PODMAN" build -t "$1" -f "$2" "$3" ;;
  esac
}

LOADED_TAR=""   # ultimo file di immagini/ caricato: aggiornare l'immagine non lo ricarica

ensure_image() {  # $1 immagine  $2 file tar in immagini/  $3 Dockerfile  $4 contesto
  have_image "$1" && return 0
  if [ -f "$IMAGES_DIR/$2" ]; then
    warn "Caricamento dell'immagine $1 dalla cartella 'immagini' (qualche minuto)..."
    "$PODMAN" load -i "$IMAGES_DIR/$2" >/dev/null && have_image "$1" && { LOADED_TAR="$IMAGES_DIR/$2"; return 0; }
    warn "Caricamento non riuscito: provo a costruire l'immagine."
  fi
  [ -f "$3" ] || return 1
  warn "Costruzione dell'immagine $1 (serve internet, 10-20 minuti)."
  build_image "$1" "$3" "$4"
}

# Impronta del codice (vedi bioloom.sh): docker/impronta.sh sui file di programma/, /app/IMPRONTA nell'immagine
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
  # precedenti); uscita 1 se il container non è partito (non si sa)
  local out rc
  have_image "$1" || return 0   # senza immagine "run" la cercherebbe nei registri
  # senza --platform (il server è amd64); Podman 2.x: niente controllo di salute, che passerebbe da systemd
  if [ "$PODMAN" = podman ]; then
    out="$("$PODMAN" run --rm --health-cmd none --entrypoint cat "$1" /app/IMPRONTA 2>/dev/null)"
  else
    out="$(MSYS_NO_PATHCONV=1 MSYS2_ARG_CONV_EXCL='*' "$PODMAN" run --rm --entrypoint cat "$1" /app/IMPRONTA 2>/dev/null)"
  fi
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

check_image() {  # all'avvio: sul server non si chiede, si avvisa
  image_outdated "bioloom:$VERSION" || return 0
  warn "L'immagine di BioLoom non corrisponde al codice in programma/ (aggiornato senza ricostruire l'immagine):"
  warn "per aggiornarla usa '$0 aggiorna'. Avvio con l'immagine esistente."
}

update_bioloom_image() {  # porta bioloom:$VERSION al codice: prima il file in immagini/, poi la costruzione
  local img="bioloom:$VERSION" tarfile="$IMAGES_DIR/bioloom-$VERSION.tar"
  if [ -f "$tarfile" ] && [ "$LOADED_TAR" != "$tarfile" ]; then
    warn "Caricamento dell'immagine $img dalla cartella 'immagini' (qualche minuto)..."
    if "$PODMAN" load -i "$tarfile" >/dev/null && have_image "$img"; then
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
  local missing=""   # strumenti con licenza i cui pacchetti mancano in docker/vendor/ (copia portatile, GitHub)
  [ -f "$ROOT/docker/vendor/procheck/procheck.tar.gz" ] || missing="PROCHECK"
  [ -f "$ROOT/docker/vendor/prosa/prosa2003.linux.tar.gz" ] || missing="${missing:+$missing e }ProSA"
  [ -n "$missing" ] && warn "La nuova immagine non avrà $missing: i pacchetti non sono in programma/docker/vendor/ (vedi il README lì)."
  build_image "$img" "$ROOT/docker/Dockerfile" "$ROOT" \
    || { warn "Costruzione dell'immagine $img non riuscita (vedi i messaggi sopra)."; return 1; }
  if image_outdated "$img"; then
    warn "L'immagine $img costruita non corrisponde ancora al codice in programma/."
    return 1
  fi
  say "${c_green}Immagine $img ricostruita dal codice in programma/.${c_off}"
}

update_qmean_image() {  # $1 UNICLUST_PATH; QMEAN si aggiorna se è in uso o se c'è il suo file
  local img="bioloom-qmean:$VERSION" tarfile="$IMAGES_DIR/bioloom-qmean-$VERSION.tar"
  have_image "$img" || [ -f "$tarfile" ] || [ -n "$1" ] || return 0
  if [ -f "$tarfile" ]; then
    warn "Caricamento dell'immagine $img dalla cartella 'immagini' (qualche minuto)..."
    if "$PODMAN" load -i "$tarfile" >/dev/null && have_image "$img"; then
      say "${c_green}Immagine $img aggiornata dalla cartella 'immagini'.${c_off}"; return 0
    fi
    warn "Caricamento non riuscito: provo a costruire l'immagine."
  fi
  if [ -f "$ROOT/docker/qmean/Dockerfile" ]; then
    warn "Costruzione dell'immagine $img (serve internet)..."
    if build_image "$img" "$ROOT/docker/qmean/Dockerfile" "$ROOT/docker/qmean"; then
      say "${c_green}Immagine $img aggiornata.${c_off}"; return 0
    fi
  fi
  warn "Immagine $img non aggiornata: QMEAN locale resta con l'immagine che aveva (se c'era)."
}

# ProSA, un computer alla volta (come in bioloom.sh): licenses/prosa/computer nella cartella dati lega il MAC della
# licenza al computer che lo applica per primo; gli altri non lo applicano finché non la spostano (--sposta-prosa).
prosa_mac() {  # MAC della licenza ProSA nella cartella dati $1
  [ -f "$1/licenses/prosa/LicenseDB" ] || return 0
  awk 'NF >= 4 && $3 ~ /^[0-9A-Fa-f]{12}$/ { m = tolower($3); printf "%s:%s:%s:%s:%s:%s\n", substr(m,1,2), substr(m,3,2), substr(m,5,2), substr(m,7,2), substr(m,9,2), substr(m,11,2); exit }' \
    "$1/licenses/prosa/LicenseDB"
}

norm_mac() {  # MAC scritto a mano (server.env) nella forma aa:bb:cc:dd:ee:ff; vuoto se non è un MAC
  printf '%s\n' "$1" | tr -d ':.-' | tr '[:upper:]' '[:lower:]' \
    | awk 'length($0) == 12 && $0 ~ /^[0-9a-f]+$/ { printf "%s:%s:%s:%s:%s:%s\n", substr($0,1,2), substr($0,3,2), substr($0,5,2), substr($0,7,2), substr($0,9,2), substr($0,11,2) }'
}

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

# Nel file il MAC si confronta normalizzato (minuscole, solo cifre esadecimali), come in bioloom.sh
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

prosa_check() {  # $1 MAC della licenza  $2 cartella dati; vero se il MAC si può applicare (sul server non si chiede)
  local file="$2/licenses/prosa/computer" id bound=""
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
    if prosa_bind "$1" "$id" "$file"; then PROSA_STATE=spostata; return 0; fi
    warn "ProSA: impossibile registrare lo spostamento in $file: licenza non spostata."
  else
    warn "Per spostarla qui rilancia con --sposta-prosa."
  fi
  PROSA_STATE=altro
  return 1
}

create_env() {
  local key
  key="$(LC_ALL=C tr -dc 'A-Za-z0-9' < /dev/urandom | head -c 24)"
  # come "bioloom.sh server", ma con dati nella cartella di BioLoom: senza root non si scrive in /srv. La porta resta
  # solo locale (BIOLOOM_BIND=127.0.0.1 di server.env.example): accesso con un tunnel SSH, senza aprire il firewall
  sed -e "s/^BIOLOOM_ACCESS_KEY=.*/BIOLOOM_ACCESS_KEY=$key/" \
      -e "s|^BIOLOOM_DATA_PATH=.*|BIOLOOM_DATA_PATH=$BASE/data|" \
      "$ROOT/launcher/server.env.example" > "$ENV_FILE"
  chmod 600 "$ENV_FILE"
  title "Creato $ENV_FILE"
  say "Chiave d'accesso generata: $key"
  say "BioLoom ascolterà solo su 127.0.0.1 (BIOLOOM_BIND): dagli altri computer con un tunnel SSH o un reverse proxy HTTPS."
  say "Controlla cartella dati, UniClust30 e porta, poi rilancia: $0 avvia"
}

remove_containers() { "$PODMAN" rm -f bioloom-qmean bioloom >/dev/null 2>&1 || true; }

stop_containers() { "$PODMAN" stop -t 30 bioloom-qmean bioloom >/dev/null 2>&1; remove_containers; }

container_running() { [ "$("$PODMAN" container inspect --format '{{.State.Running}}' "$1" 2>/dev/null)" = true ]; }

# Un BioLoom alla volta (come in bioloom.sh): i container hanno nomi fissi, quindi l'avvio da un'altra copia di BioLoom
# sostituirebbe quelli in esecuzione con un'altra cartella dati. L'etichetta bioloom.cartella dei container dice da
# quale cartella sono partiti; per quelli degli avviatori precedenti, senza etichetta, si confronta la cartella dati.
OTHER_DIR=""

find_other() {  # imposta OTHER_DIR: da dove è partito il BioLoom in esecuzione, se non è questa copia (vuota altrimenti)
  local name dir data
  OTHER_DIR=""
  for name in bioloom bioloom-qmean; do
    container_running "$name" || continue
    dir="$("$PODMAN" container inspect --format '{{index .Config.Labels "bioloom.cartella"}}' "$name" 2>/dev/null)"
    case "$dir" in
      ""|"<no value>") ;;
      *) [ "$dir" = "$BASE" ] || [ "$dir" -ef "$BASE" ] || OTHER_DIR="$dir"; return 0 ;;
    esac
    [ "$name" = bioloom ] || return 0   # QMEAN senza etichetta: non si sa
    dir="$("$PODMAN" container inspect --format '{{range .Mounts}}{{if eq .Destination "/data"}}{{.Source}}{{end}}{{end}}' bioloom 2>/dev/null)"
    data="$(val BIOLOOM_DATA_PATH "$BASE/data")"
    [ -z "$dir" ] || [ "$dir" = "$data" ] || [ "$dir" -ef "$data" ] || OTHER_DIR="$dir (cartella dati)"
    return 0
  done
}

single_instance() {  # prima di avviare: il BioLoom partito da un'altra cartella si arresta solo con il consenso
  # (domanda; con --no-menu ci si ferma). $1 = cosa si annulla, per il messaggio
  local answer=""
  find_other
  [ -n "$OTHER_DIR" ] || return 0
  warn "È già in esecuzione BioLoom da un'altra cartella: $OTHER_DIR"
  warn "Le copie di BioLoom su questo server usano gli stessi container: per continuare va arrestato"
  warn "(i calcoli in corso vengono interrotti; i suoi dati restano dove sono)."
  if [ "$NO_MENU" = 0 ]; then read -r -p "  Arrestarlo e continuare? [s/N] " answer || answer=""; fi
  case "$answer" in
    [sS]*) ;;
    *) fail "$1: resta in esecuzione il BioLoom di $OTHER_DIR. Per usare questa copia arrestalo prima ('$0 arresta') e riprova." ;;
  esac
  warn "Arresto del BioLoom partito da $OTHER_DIR..."
  # le unità systemd ("servizio") sono dell'utente, non della copia: fermate qui, start() le rigenera per i nuovi container
  have_units && systemctl --user stop container-bioloom-qmean.service container-bioloom.service >/dev/null 2>&1
  stop_containers
}

note_other() {  # arresta, stato, log, verifica: avviso se riguardano il BioLoom di un'altra cartella
  find_other
  [ -z "$OTHER_DIR" ] || warn "Il BioLoom in esecuzione è partito da un'altra cartella: $OTHER_DIR"
}

STORAGE_CHECKED=0
check_storage() {  # avviso, una volta sola, se Podman archivia le immagini con vfs (senza root e senza fuse-overlayfs):
  # ogni strato è una copia intera, quindi le immagini di BioLoom occupano molte volte il loro spazio. Non blocca.
  local driver
  [ "$PODMAN" = podman ] || return 0
  [ "$STORAGE_CHECKED" = 0 ] || return 0
  STORAGE_CHECKED=1
  # {{.Store.GraphDriverName}} da Podman 2 (provato con la 2.2.1); se il formato non va, l'uscita normale (YAML)
  driver="$("$PODMAN" info --format '{{.Store.GraphDriverName}}' 2>/dev/null)" \
    || driver="$("$PODMAN" info 2>/dev/null | awk 'tolower($1) == "graphdrivername:" { print $2; exit }')"
  [ "$driver" = vfs ] || return 0
  warn "Podman archivia le immagini con il driver vfs: senza fuse-overlayfs quelle di BioLoom (circa 3 GB) occupano"
  warn "molte volte il loro spazio. Installa fuse-overlayfs e configura storage.conf (Guida/5 - Chiavetta e server.md)."
}

loopback() { case "$1" in 127.*|localhost|::1|"[::1]") return 0 ;; *) return 1 ;; esac; }   # BIOLOOM_BIND locale

port_free() {  # $1 porta, $2 indirizzo: libera se lì nessun programma accetta connessioni (come in bioloom.sh)
  local rc=0
  case "$2" in
    # anche in locale un programma bloccato (coda delle connessioni piena) terrebbe ferma la connessione per minuti,
    # perché Linux scarta le richieste in più invece di rifiutarle: con timeout, dopo 3 s senza risposta, la porta è
    # occupata (una porta chiusa in locale rifiuta subito); senza timeout si prova senza limite
    127.*|::1)
      if command -v timeout >/dev/null 2>&1; then
        # shellcheck disable=SC2016 # $0 e $1 sono quelli della shell figlia
        { timeout 3 bash -c 'exec 3<>"/dev/tcp/$0/$1"' "$2" "$1"; } 2>/dev/null || rc=$?
        # 0: un programma accetta; 124, o 128 e oltre (timeout di busybox, ucciso da un segnale): nessuna risposta
        [ "$rc" != 0 ] && [ "$rc" != 124 ] && [ "$rc" -lt 128 ]
      else
        ! (exec 3<>"/dev/tcp/$2/$1") 2>/dev/null
      fi ;;
    # un indirizzo di rete che non risponde terrebbe ferma la connessione per minuti: senza timeout decide podman run
    *) command -v timeout >/dev/null 2>&1 || return 0
       # shellcheck disable=SC2016 # $0 e $1 sono quelli della shell figlia
       ! timeout 3 bash -c 'exec 3<>"/dev/tcp/$0/$1"' "$2" "$1" 2>/dev/null ;;
  esac
}

check_port() {  # $1 porta  $2 indirizzo di BIOLOOM_BIND: porta valida e non usata da un altro programma, prima di caricare
  # o costruire le immagini (podman run fallirebbe dopo, con "address already in use")
  local own="" host="$2"
  case "$1" in ""|*[!0-9]*) fail "BIOLOOM_PORT in server.env non è una porta valida (un numero da 1 a 65535): $1" ;; esac
  [ "${#1}" -le 5 ] && [ "$1" -ge 1 ] && [ "$1" -le 65535 ] \
    || fail "BIOLOOM_PORT in server.env non è una porta valida (un numero da 1 a 65535): $1"
  case "$host" in ""|0.0.0.0|localhost|::|"[::]") host=127.0.0.1 ;; "[::1]") host=::1 ;; esac
  if container_running bioloom; then
    # la porta del BioLoom in esecuzione è libera per lui: start() rimuove il container prima di ricrearlo. Se Podman
    # non la dice si suppone la stessa (decide podman run)
    own="$("$PODMAN" port bioloom 8765 2>/dev/null | head -n1 | tr -d '\r')"; own="${own##*:}"
    case "$own" in ""|*[!0-9]*) own="$1" ;; esac
  fi
  [ "$own" = "$1" ] || port_free "$1" "$host" \
    || fail "La porta $1 (BIOLOOM_PORT in server.env) è già usata da un altro programma${2:+ su $2}: chiudilo (per vedere quale: ss -ltnp) oppure cambia BIOLOOM_PORT in $ENV_FILE e rilancia '$0 avvia'."
}

# Unità systemd di "servizio": "podman generate systemd" senza --new le lega all'ID del container (PIDFile), quindi
# vanno rigenerate ogni volta che start() ricrea i container.
UNIT_DIR="${HOME:-}/.config/systemd/user"

have_units() { [ "$PODMAN" = podman ] && [ -f "$UNIT_DIR/container-bioloom.service" ]; }

write_units() {  # unità dei container attuali, abilitate; quella di un container che non c'è più viene disabilitata
  local name
  mkdir -p "$UNIT_DIR" || return 1
  for name in bioloom bioloom-qmean; do
    if ! "$PODMAN" container inspect "$name" >/dev/null 2>&1; then
      [ -f "$UNIT_DIR/container-$name.service" ] && systemctl --user disable "container-$name.service" >/dev/null 2>&1
      continue
    fi
    (cd "$UNIT_DIR" && "$PODMAN" generate systemd --files --name "$name" >/dev/null) \
      || { warn "generate systemd non riuscito per $name."; return 1; }
    systemctl --user daemon-reload
    systemctl --user enable "container-$name.service" >/dev/null 2>&1 \
      || { warn "Abilitazione di container-$name.service non riuscita."; return 1; }
  done
}

wait_healthy() {  # $1 indirizzo  $2 porta
  for _ in $(seq 1 120); do
    if command -v curl >/dev/null 2>&1; then
      # --noproxy: senza, curl passerebbe dal proxy di http_proxy anche per 127.0.0.1
      curl -fsS --noproxy '*' --max-time 5 "http://$1:$2/api/health" >/dev/null 2>&1 && return 0
    elif (exec 3<>"/dev/tcp/$1/$2") 2>/dev/null; then
      return 0
    fi
    sleep 1
  done
  return 1
}

# Il profilo seccomp dei Podman 2.x non conosce le chiamate di sistema più recenti e le rifiuta con EPERM invece di
# ENOSYS, quindi la glibc dell'immagine non ripiega su quelle vecchie: con clone3 nessun thread parte (OpenBLAS:
# "pthread_create failed", avvio bloccato), con faccessat2 "[ -x file ]" fallisce anche su file eseguibili (il
# dispatcher di mmseqs cerca allora mmseqs-plain, che non c'è). Con Podman < 3 i container usano una copia del
# profilo del server in cui le chiamate recenti che il profilo non nomina rispondono ENOSYS: restano negate, ma i
# programmi ripiegano. Il resto del filtro resta com'è. La copia (seccomp-podman.json accanto a server.env) si rifà
# quando cambiano il profilo del server o questo script.
SECCOMP_PY='
import json, sys
NUOVE = """clone3 faccessat2 fchmodat2 openat2 close_range epoll_pwait2 statx pidfd_open pidfd_send_signal pidfd_getfd
process_madvise process_mrelease mount_setattr futex_waitv cachestat rseq memfd_secret set_mempolicy_home_node
map_shadow_stack quotactl_fd landlock_create_ruleset landlock_add_rule landlock_restrict_self""".split()
p = json.load(sys.stdin)
rules = p.get("syscalls") or []
nominate = {n for r in rules for n in (r.get("names") or [r.get("name")])}
mancanti = [n for n in NUOVE if n not in nominate]
if mancanti:
    rules.insert(0, {"names": mancanti, "action": "SCMP_ACT_ERRNO", "errnoRet": 38})
p["syscalls"] = rules
json.dump(p, sys.stdout, indent=1)
'
seccomp_profile() {  # 0 e SECCOMP_FILE se serve il profilo su misura, 1 se Podman non ne ha bisogno
  local pv src="" f
  pv="$("$PODMAN" --version 2>/dev/null | sed -n 's/.*version \([0-9][0-9]*\)\..*/\1/p')"
  { [ -n "$pv" ] && [ "$pv" -lt 3 ]; } || return 1
  for f in /usr/share/containers/seccomp.json /etc/containers/seccomp.json; do
    [ -f "$f" ] && { src="$f"; break; }
  done
  [ -n "$src" ] || fail "Podman $pv: manca il profilo seccomp del sistema (/usr/share/containers/seccomp.json), da cui BioLoom ricava quello per i suoi container. Chiedi all'amministratore di installare containers-common."
  SECCOMP_FILE="$BASE/seccomp-podman.json"
  if [ ! -s "$SECCOMP_FILE" ] || [ "$src" -nt "$SECCOMP_FILE" ] || [ "$ROOT/launcher/bioloom-podman.sh" -nt "$SECCOMP_FILE" ]; then
    # il Python dell'immagine c'è sempre; lo script non apre thread, quindi gira anche con il profilo vecchio
    "$PODMAN" run --rm -i --security-opt label=disable --health-cmd none --entrypoint python3 "bioloom:$VERSION" \
        -c "$SECCOMP_PY" < "$src" > "$SECCOMP_FILE.tmp" 2>/dev/null && [ -s "$SECCOMP_FILE.tmp" ] \
      && mv -f "$SECCOMP_FILE.tmp" "$SECCOMP_FILE" \
      || { rm -f "$SECCOMP_FILE.tmp"; fail "Non riesco a preparare il profilo seccomp per Podman $pv da $src."; }
  fi
  return 0
}

start() {
  [ -f "$ENV_FILE" ] || { create_env; exit 0; }
  check_env_version
  local data uniclust qmtl port bind restart mac lic_mac
  data="$(val BIOLOOM_DATA_PATH "$BASE/data")"
  uniclust="$(val UNICLUST_PATH)"
  qmtl="$(val QMTL_PATH)"
  port="$(val BIOLOOM_PORT 8765)"
  bind="$(val BIOLOOM_BIND 127.0.0.1)"
  restart="$(val BIOLOOM_RESTART unless-stopped)"
  mac="$(val BIOLOOM_MAC)"
  mkdir -p "$data" || fail "Impossibile creare la cartella dati $data."
  touch "$data/.scrittura" 2>/dev/null || fail "La cartella dati $data non è scrivibile."
  rm -f "$data/.scrittura"
  if [ -n "$uniclust" ]; then
    find "$uniclust" -maxdepth 4 -type f \( -name '*_a3m.ffindex' -o -name '*_a3m_db.index' \) -size +0 2>/dev/null \
      | grep -q . || fail "UNICLUST_PATH in server.env non contiene database UniClust30/UniRef30 (entro 3 livelli): $uniclust"
  fi
  single_instance "Avvio annullato"
  check_storage
  check_port "$port" "$bind"

  ensure_image "bioloom:$VERSION" "bioloom-$VERSION.tar" "$ROOT/docker/Dockerfile" "$ROOT" \
    || fail "Immagine BioLoom non disponibile: copia la cartella 'immagini' della copia portatile."
  check_image
  if [ -n "$uniclust" ]; then
    ensure_image "bioloom-qmean:$VERSION" "bioloom-qmean-$VERSION.tar" "$ROOT/docker/qmean/Dockerfile" "$ROOT/docker/qmean" \
      || fail "Immagine QMEAN non disponibile."
  fi

  local common=(-d --restart "$restart" --label "bioloom.cartella=$BASE")   # etichetta: vedi find_other
  local old_seccomp=0
  if [ "$PODMAN" = podman ]; then
    # SELinux (RHEL): niente rietichettatura delle cartelle montate, che possono essere dischi condivisi.
    # Il controllo di salute di Podman 2.x passa da systemd: lo sostituisce l'attesa qui sotto.
    common+=(--security-opt label=disable --health-cmd none)
    seccomp_profile && { common+=(--security-opt "seccomp=$SECCOMP_FILE"); old_seccomp=1; }
    if [ -x /usr/libexec/podman/catatonit ] || command -v catatonit >/dev/null 2>&1; then common+=(--init)
    else warn "catatonit assente: i container partono senza processo init."; fi
  else
    common+=(--init)
  fi

  local app=("${common[@]}" --name bioloom -p "$bind:$port:8765" -v "$data:/data"
             -v bioloom_scratch:/scratch -v bioloom_cache:/cache)
  if [ "$old_seccomp" = 1 ]; then
    # faccessat2 non si aggiusta dal profilo (la libseccomp 2.4 non la conosce): il dispatcher di mmseqs dell'immagine
    # la usa con "[ -x ]", e lo sostituisce mmseqs-dispatch.sh (vedi il file)
    local dispatch="$ROOT/launcher/mmseqs-dispatch.sh"
    [ -x "$dispatch" ] || chmod +x "$dispatch" 2>/dev/null
    if [ -x "$dispatch" ]; then app+=(-v "$dispatch:/usr/lib/mmseqs2/simd-dispatch:ro")
    else warn "programma/launcher/mmseqs-dispatch.sh manca o non è eseguibile: MMseqs2 potrebbe non funzionare."; fi
  fi
  PROSA_STATE=""
  if [ "$ROOTLESS" = 1 ]; then
    # il root del container è l'utente del server: i file della cartella dati restano suoi
    app+=(-e BIOLOOM_UID=0 -e BIOLOOM_GID=0)
    [ -n "$mac" ] && [ "$mac" != "02:42:b1:00:00:01" ] \
      && warn "Podman senza root non assegna il MAC $mac: ProSA resta senza licenza valida."
  elif [ -n "$mac" ]; then
    # MAC della licenza nella cartella dati: un computer alla volta; se è su un altro, il MAC predefinito
    lic_mac="$(prosa_mac "$data")"
    if [ -n "$lic_mac" ] && [ "$(norm_mac "$mac")" = "$lic_mac" ] && ! prosa_check "$lic_mac" "$data"; then
      mac="02:42:b1:00:00:01"
    fi
    app+=(--mac-address "$mac")
  fi
  local name
  for name in BIOLOOM_ACCESS_KEY BIOLOOM_ALLOWED_HOSTS BIOLOOM_TRUSTED_PROXIES MODELLER_KEY BIOLOOM_MODELLER_PYTHON \
              BIOLOOM_ESMFOLD_URL BIOLOOM_SMTP_HOST BIOLOOM_SMTP_USER BIOLOOM_SMTP_PASSWORD BIOLOOM_SMTP_FROM \
              BIOLOOM_PUBLIC_URL; do
    app+=(-e "$name=$(val "$name")")
  done
  app+=(-e "BIOLOOM_AUTH=$(val BIOLOOM_AUTH on)" -e "BIOLOOM_ALLOW_SHUTDOWN=$(val BIOLOOM_ALLOW_SHUTDOWN 0)"
        -e "BIOLOOM_SMTP_PORT=$(val BIOLOOM_SMTP_PORT 587)" -e "BIOLOOM_SMTP_SECURITY=$(val BIOLOOM_SMTP_SECURITY starttls)")
  [ -n "$uniclust" ] && app+=(-e BIOLOOM_QMEAN_URL=http://127.0.0.1:8080)

  say "Avvio dei container..."
  local units=0
  if have_units; then
    # prima di rimuovere i container si fermano le loro unità, che altrimenti li farebbero ripartire
    units=1
    systemctl --user stop container-bioloom-qmean.service container-bioloom.service >/dev/null 2>&1 || true
  fi
  remove_containers
  "$PODMAN" run "${app[@]}" "bioloom:$VERSION" >/dev/null || fail "$PODMAN non è riuscito ad avviare BioLoom."
  if [ -n "$uniclust" ]; then
    local qm=("${common[@]}" --name bioloom-qmean --network container:bioloom
              -v "$uniclust:/uniclust_src:ro" -v bioloom_qmean_cache:/cache
              -e "QMEAN_MAX_SECONDS=$(val QMEAN_MAX_SECONDS 14400)" -e "QMEAN_HHBLITS_CPU=$(val QMEAN_HHBLITS_CPU)"
              -e "QMEAN_PARALLEL=$(val QMEAN_PARALLEL 1)" -e "QMEAN_PROFILE_CACHE_GB=$(val QMEAN_PROFILE_CACHE_GB 10)")
    if [ -n "$qmtl" ]; then qm+=(-v "$qmtl:/qmtl:ro"); else qm+=(-v bioloom_qmtl_vuota:/qmtl:ro); fi
    "$PODMAN" run "${qm[@]}" "bioloom-qmean:$VERSION" >/dev/null || fail "$PODMAN non è riuscito ad avviare QMEAN."
  fi
  local host="$bind"
  [ "$host" = 0.0.0.0 ] && host=127.0.0.1
  wait_healthy "$host" "$port" || fail "BioLoom non risponde: controlla con '$0 log'."
  title "BioLoom (server) avviato sulla porta $port${uniclust:+, con QMEAN locale}"
  if [ "$units" = 1 ]; then
    if write_units; then say "Unità systemd rigenerate per i nuovi container: valgono dal prossimo riavvio del server."
    else warn "Unità systemd non rigenerate: puntano ai container rimossi. Rilancia '$0 servizio'."; fi
  fi
  if loopback "$bind"; then
    say "Dal tuo computer: ssh -N -L $port:127.0.0.1:$port ${USER:-utente}@$(hostname -f 2>/dev/null || hostname)"
    say "poi apri http://localhost:$port"
  else
    warn "BIOLOOM_BIND=$bind: BioLoom è raggiungibile dalla rete in HTTP, con chiave d'accesso e cookie in chiaro."
    # Podman con root (come Docker) pubblica la porta con regole NAT, prima di quelle del firewall; senza root la porta
    # è aperta da un processo dell'utente e il firewall la filtra come le altre
    [ "$ROOTLESS" = 1 ] || warn "La porta pubblicata da $PODMAN scavalca il firewall (firewalld, ufw)."
    warn "Meglio BIOLOOM_BIND=127.0.0.1 con un tunnel SSH o un reverse proxy HTTPS (Guida/5 - Chiavetta e server.md)."
  fi
  say "Chiave d'accesso: BIOLOOM_ACCESS_KEY in $ENV_FILE"
  case "$PROSA_STATE" in
    attivata) say "ProSA: licenza attivata su questo computer, MAC applicato." ;;
    spostata) say "ProSA: licenza spostata su questo computer, MAC applicato." ;;
    altro)    warn "ProSA: licenza attiva su un altro computer, MAC non applicato (per spostarla qui: --sposta-prosa)." ;;
  esac
}

stale_running() {  # vero se un container in esecuzione usa un'immagine diversa da quella attuale
  local name img want
  for name in bioloom bioloom-qmean; do
    container_running "$name" || continue
    img="bioloom:$VERSION"; [ "$name" = bioloom-qmean ] && img="bioloom-qmean:$VERSION"
    # immagine attuale assente (aggiornamento non riuscito): riavviare farebbe solo ritentare la costruzione
    want="$("$PODMAN" image inspect --format '{{.Id}}' "$img" 2>/dev/null)"
    [ -n "$want" ] || continue
    [ "$("$PODMAN" container inspect --format '{{.Image}}' "$name" 2>/dev/null)" != "$want" ] && return 0
  done
  return 1
}

image_ids() { printf '%s %s' "$("$PODMAN" image inspect --format '{{.Id}}' "bioloom:$VERSION" 2>/dev/null)" \
                               "$("$PODMAN" image inspect --format '{{.Id}}' "bioloom-qmean:$VERSION" 2>/dev/null)"; }

update_all() {  # azione aggiorna: immagini al codice in programma/, poi riavvio se BioLoom è attivo
  local running=0 ok=1 before
  title "Aggiornamento delle immagini di BioLoom $VERSION"
  check_env_version
  check_storage
  container_running bioloom && running=1
  before="$(image_ids)"
  if image_current "bioloom:$VERSION"; then
    say "${c_green}L'immagine bioloom:$VERSION corrisponde già al codice in programma/.${c_off}"
  else
    update_bioloom_image || ok=0
  fi
  update_qmean_image "$(val UNICLUST_PATH)"
  if [ "$running" = 0 ]; then
    [ "$(image_ids)" = "$before" ] || say "BioLoom non è in esecuzione: le immagini aggiornate verranno usate al prossimo avvio."
  elif ! stale_running; then
    if [ "$ok" = 1 ]; then say "BioLoom è in esecuzione e usa già le immagini attuali: nessun riavvio."
    else say "BioLoom resta in esecuzione con l'immagine che aveva: nessun riavvio."; fi
  else
    single_instance "Riavvio annullato"
    warn "Riavvio di BioLoom con le immagini aggiornate (i calcoli in corso vengono interrotti)..."
    start
  fi
  [ "$ok" = 1 ] || fail "L'immagine bioloom:$VERSION non è stata aggiornata (vedi i messaggi sopra)."
}

install_service() {
  [ "$PODMAN" = podman ] || fail "Solo con Podman."
  "$PODMAN" container inspect bioloom >/dev/null 2>&1 || fail "Avvia prima BioLoom: $0 avvia"
  write_units || fail "Unità systemd non installate (vedi i messaggi sopra)."
  title "Unità systemd installate in $UNIT_DIR"
  if [ "$(loginctl show-user "${USER:-$(id -un)}" -p Linger 2>/dev/null)" != "Linger=yes" ]; then
    warn "Il linger è spento: le unità partono solo quando sei collegato. Chiedi all'amministratore:"
    warn "  loginctl enable-linger ${USER:-$(id -un)}"
  fi
}

case "$ACTION" in
  avvia|server) start ;;
  # arresta: anche il BioLoom partito da un'altra copia (i container sono gli stessi), dicendo quale è
  arresta) note_other; stop_containers; say "BioLoom è arrestato." ;;
  stato) note_other; "$PODMAN" ps -a --filter name=bioloom ;;
  log) note_other; "$PODMAN" logs --tail 200 bioloom ;;
  verifica) note_other; "$PODMAN" exec bioloom python -m bioloom.selftest ;;
  aggiorna) update_all ;;
  servizio) install_service ;;
  *) fail "Azione sconosciuta: $ACTION (avvia, arresta, stato, log, verifica, aggiorna, servizio)" ;;
esac
