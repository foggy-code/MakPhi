#!/usr/bin/env bash
# Avvio di BioLoom su un server con Podman ma senza compose (per esempio RHEL 8 con Podman 2.x, senza root).
# Stessi container, immagini e server.env di compose.yml, avviati con "podman run".
#
#   bioloom-podman.sh avvia      crea server.env alla prima esecuzione, poi avvia (o riavvia) i container
#   bioloom-podman.sh arresta | stato | log | verifica
#   bioloom-podman.sh servizio   unità systemd dell'utente: riavvio con il server (serve il linger)
#
# QMEAN locale (UNICLUST_PATH in server.env) gira in un secondo container che condivide la rete di quello di
# BioLoom: BioLoom lo raggiunge su http://127.0.0.1:8080. PODMAN=docker usa la CLI di Docker (per le prove).
set -u

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"   # programma/
BASE="$(cd "$ROOT/.." && pwd)"                               # cartella di BioLoom
ENV_FILE="$BASE/server.env"
IMAGES_DIR="$BASE/immagini"
PODMAN="${PODMAN:-podman}"
ACTION="${1:-avvia}"

c_cyan=$'\033[36m'; c_yellow=$'\033[33m'; c_red=$'\033[31m'; c_off=$'\033[0m'
say()   { printf '  %s\n' "$*"; }
title() { printf '\n  %s%s%s\n' "$c_cyan" "$*" "$c_off"; }
warn()  { printf '  %s%s%s\n' "$c_yellow" "$*" "$c_off"; }
fail()  { printf '\n  %s[!] %s%s\n\n' "$c_red" "$*" "$c_off"; exit 1; }

command -v "$PODMAN" >/dev/null 2>&1 || fail "$PODMAN non è installato."

val() {  # valore di una variabile di server.env (ultima riga, senza virgolette); $2 = predefinito
  local v=""
  [ -f "$ENV_FILE" ] && v="$(sed -n "s/^$1=//p" "$ENV_FILE" | tail -n1 | tr -d '\r')"
  v="${v#\"}"; v="${v%\"}"; v="${v#\'}"; v="${v%\'}"
  printf '%s' "${v:-${2:-}}"
}

VERSION="$(val BIOLOOM_VERSION "$(head -n1 "$ROOT/launcher/VERSION" 2>/dev/null | tr -d '[:space:]')")"
VERSION="${VERSION:-4.1.0}"
ROOTLESS=0
[ "$PODMAN" = podman ] && [ "$(id -u)" != 0 ] && ROOTLESS=1

have_image() { "$PODMAN" image inspect "$1" >/dev/null 2>&1; }

ensure_image() {  # $1 immagine  $2 file tar in immagini/  $3 Dockerfile  $4 contesto
  have_image "$1" && return 0
  if [ -f "$IMAGES_DIR/$2" ]; then
    warn "Caricamento dell'immagine $1 dalla cartella 'immagini' (qualche minuto)..."
    "$PODMAN" load -i "$IMAGES_DIR/$2" >/dev/null && have_image "$1" && return 0
    warn "Caricamento non riuscito: provo a costruire l'immagine."
  fi
  [ -f "$3" ] || return 1
  warn "Costruzione dell'immagine $1 (serve internet, 10-20 minuti)."
  "$PODMAN" build -t "$1" -f "$3" "$4"
}

create_env() {
  local key
  key="$(LC_ALL=C tr -dc 'A-Za-z0-9' < /dev/urandom | head -c 24)"
  # come "bioloom.sh server", ma con dati nella cartella di BioLoom e porta raggiungibile solo dal server
  # (accesso con un tunnel SSH): senza root non si scrive in /srv e non si apre il firewall
  sed -e "s/^BIOLOOM_ACCESS_KEY=.*/BIOLOOM_ACCESS_KEY=$key/" \
      -e "s|^BIOLOOM_DATA_PATH=.*|BIOLOOM_DATA_PATH=$BASE/data|" \
      -e "s/^BIOLOOM_BIND=.*/BIOLOOM_BIND=127.0.0.1/" \
      "$ROOT/launcher/server.env.example" > "$ENV_FILE"
  chmod 600 "$ENV_FILE"
  title "Creato $ENV_FILE"
  say "Chiave d'accesso generata: $key"
  say "Controlla cartella dati, UniClust30 e porta, poi rilancia: $0 avvia"
}

remove_containers() { "$PODMAN" rm -f bioloom-qmean bioloom >/dev/null 2>&1 || true; }

wait_healthy() {  # $1 indirizzo  $2 porta
  for _ in $(seq 1 120); do
    if command -v curl >/dev/null 2>&1; then
      curl -fsS "http://$1:$2/api/health" >/dev/null 2>&1 && return 0
    elif (exec 3<>"/dev/tcp/$1/$2") 2>/dev/null; then
      return 0
    fi
    sleep 1
  done
  return 1
}

start() {
  [ -f "$ENV_FILE" ] || { create_env; exit 0; }
  local data uniclust qmtl port bind restart mac
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

  ensure_image "bioloom:$VERSION" "bioloom-$VERSION.tar" "$ROOT/docker/Dockerfile" "$ROOT" \
    || fail "Immagine BioLoom non disponibile: copia la cartella 'immagini' della copia portatile."
  if [ -n "$uniclust" ]; then
    ensure_image "bioloom-qmean:$VERSION" "bioloom-qmean-$VERSION.tar" "$ROOT/docker/qmean/Dockerfile" "$ROOT/docker/qmean" \
      || fail "Immagine QMEAN non disponibile."
  fi

  local common=(-d --restart "$restart")
  if [ "$PODMAN" = podman ]; then
    # SELinux (RHEL): niente rietichettatura delle cartelle montate, che possono essere dischi condivisi.
    # Il controllo di salute di Podman 2.x passa da systemd: lo sostituisce l'attesa qui sotto.
    common+=(--security-opt label=disable --health-cmd none)
    if [ -x /usr/libexec/podman/catatonit ] || command -v catatonit >/dev/null 2>&1; then common+=(--init)
    else warn "catatonit assente: i container partono senza processo init."; fi
  else
    common+=(--init)
  fi

  local app=("${common[@]}" --name bioloom -p "$bind:$port:8765" -v "$data:/data"
             -v bioloom_scratch:/scratch -v bioloom_cache:/cache)
  if [ "$ROOTLESS" = 1 ]; then
    # il root del container è l'utente del server: i file della cartella dati restano suoi
    app+=(-e BIOLOOM_UID=0 -e BIOLOOM_GID=0)
    [ -n "$mac" ] && [ "$mac" != "02:42:b1:00:00:01" ] \
      && warn "Podman senza root non assegna il MAC $mac: ProSA resta senza licenza valida."
  elif [ -n "$mac" ]; then
    app+=(--mac-address "$mac")
  fi
  local name
  for name in BIOLOOM_ACCESS_KEY BIOLOOM_ALLOWED_HOSTS MODELLER_KEY BIOLOOM_ESMFOLD_URL BIOLOOM_SMTP_HOST \
              BIOLOOM_SMTP_USER BIOLOOM_SMTP_PASSWORD BIOLOOM_SMTP_FROM BIOLOOM_PUBLIC_URL; do
    app+=(-e "$name=$(val "$name")")
  done
  app+=(-e "BIOLOOM_AUTH=$(val BIOLOOM_AUTH on)" -e "BIOLOOM_ALLOW_SHUTDOWN=$(val BIOLOOM_ALLOW_SHUTDOWN 0)"
        -e "BIOLOOM_SMTP_PORT=$(val BIOLOOM_SMTP_PORT 587)" -e "BIOLOOM_SMTP_SECURITY=$(val BIOLOOM_SMTP_SECURITY starttls)")
  [ -n "$uniclust" ] && app+=(-e BIOLOOM_QMEAN_URL=http://127.0.0.1:8080)

  say "Avvio dei container..."
  remove_containers
  "$PODMAN" run "${app[@]}" "bioloom:$VERSION" >/dev/null || fail "$PODMAN non è riuscito ad avviare BioLoom."
  if [ -n "$uniclust" ]; then
    local qm=("${common[@]}" --name bioloom-qmean --network container:bioloom
              -v "$uniclust:/uniclust_src:ro" -v bioloom_qmean_cache:/cache
              -e "QMEAN_MAX_SECONDS=$(val QMEAN_MAX_SECONDS 14400)" -e "QMEAN_HHBLITS_CPU=$(val QMEAN_HHBLITS_CPU)")
    if [ -n "$qmtl" ]; then qm+=(-v "$qmtl:/qmtl:ro"); else qm+=(-v bioloom_qmtl_vuota:/qmtl:ro); fi
    "$PODMAN" run "${qm[@]}" "bioloom-qmean:$VERSION" >/dev/null || fail "$PODMAN non è riuscito ad avviare QMEAN."
  fi
  local host="$bind"
  [ "$host" = 0.0.0.0 ] && host=127.0.0.1
  wait_healthy "$host" "$port" || fail "BioLoom non risponde: controlla con '$0 log'."
  title "BioLoom (server) avviato sulla porta $port${uniclust:+, con QMEAN locale}"
  if [ "$bind" = 127.0.0.1 ]; then
    say "Dal tuo computer: ssh -N -L $port:127.0.0.1:$port ${USER:-utente}@$(hostname -f 2>/dev/null || hostname)"
    say "poi apri http://localhost:$port"
  fi
  say "Chiave d'accesso: BIOLOOM_ACCESS_KEY in $ENV_FILE"
}

install_service() {
  [ "$PODMAN" = podman ] || fail "Solo con Podman."
  "$PODMAN" container inspect bioloom >/dev/null 2>&1 || fail "Avvia prima BioLoom: $0 avvia"
  local dir="$HOME/.config/systemd/user" name
  mkdir -p "$dir"
  for name in bioloom bioloom-qmean; do
    "$PODMAN" container inspect "$name" >/dev/null 2>&1 || continue
    (cd "$dir" && "$PODMAN" generate systemd --files --name "$name" >/dev/null) || fail "generate systemd non riuscito."
    systemctl --user daemon-reload
    systemctl --user enable "container-$name.service" || fail "Abilitazione di container-$name.service non riuscita."
  done
  title "Unità systemd installate in $dir"
  if [ "$(loginctl show-user "${USER:-$(id -un)}" -p Linger 2>/dev/null)" != "Linger=yes" ]; then
    warn "Il linger è spento: le unità partono solo quando sei collegato. Chiedi all'amministratore:"
    warn "  loginctl enable-linger ${USER:-$(id -un)}"
  fi
}

case "$ACTION" in
  avvia|server) start ;;
  arresta) "$PODMAN" stop -t 30 bioloom-qmean bioloom >/dev/null 2>&1; remove_containers; say "BioLoom è arrestato." ;;
  stato) "$PODMAN" ps -a --filter name=bioloom ;;
  log) "$PODMAN" logs --tail 200 bioloom ;;
  verifica) "$PODMAN" exec bioloom python -m bioloom.selftest ;;
  servizio) install_service ;;
  *) fail "Azione sconosciuta: $ACTION (avvia, arresta, stato, log, verifica, servizio)" ;;
esac
