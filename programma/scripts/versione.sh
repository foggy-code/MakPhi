#!/usr/bin/env bash
# Versione unica di BioLoom. La fonte è programma/launcher/VERSION: gli avviatori e scripts/test.sh la leggono da lì;
# queste copie letterali devono coincidere (le controlla anche backend/tests/test_version.py):
#   backend/pyproject.toml          version = "X.Y.Z" in [project]
#   backend/bioloom/__init__.py     __version__ = "X.Y.Z"
#   frontend/package.json           "version" del progetto
#   frontend/package-lock.json      i due "version" del progetto (in cima e nel pacchetto ""), non quelli delle dipendenze
#   docker/Dockerfile               ARG BIOLOOM_VERSION=X.Y.Z (predefinito, per le costruzioni senza --build-arg)
#   docker/test/Dockerfile          ARG BASE=bioloom:X.Y.Z
#   launcher/compose.yml            i due ${BIOLOOM_VERSION:-X.Y.Z}
#   ../CITATION.cff                 version: "X.Y.Z" (citazione, nella radice del repository)
#   ../.zenodo.json                 "version" (archiviazione su Zenodo, nella radice del repository)
#
#   bash programma/scripts/versione.sh          stampa la versione e controlla le copie (uscita 1 con le differenze)
#   bash programma/scripts/versione.sh X.Y.Z    porta VERSION e tutte le copie a X.Y.Z, poi ricontrolla
#
# Si lancia con bash davanti: il bit eseguibile manca se il file è entrato nel repository da Windows (core.filemode).
#
# Funziona con bash 3.2 (macOS), su Linux e con Git Bash. I file si riscrivono con awk e solo se in tutti si trovano
# le copie attese; ognuno conserva i suoi fine riga (BINMODE=3: in Git Bash awk altrimenti toglierebbe i CR).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VERSION_FILE="$ROOT/launcher/VERSION"
FORMATO='^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$'

# tipo|file (relativo a programma/)|copie attese
COPIE="pyproject|backend/pyproject.toml|1
init|backend/bioloom/__init__.py|1
package|frontend/package.json|1
lock|frontend/package-lock.json|2
dockerfile|docker/Dockerfile|1
test|docker/test/Dockerfile|1
compose|launcher/compose.yml|2
cff|../CITATION.cff|1
zenodo|../.zenodo.json|1"

# Riga per riga cerca la versione secondo il tipo di file: in lettura (nuova vuota) stampa i valori trovati, in
# scrittura stampa il file con i valori sostituiti ed esce con 1 se le copie trovate non sono quelle attese.
# shellcheck disable=SC2016  # programma awk: i $ sono di awk
AWK='
function campo(pre, val,    n, resto) {
  if (!match($0, pre)) return
  n = RSTART + RLENGTH - 1
  resto = substr($0, n + 1)
  match(resto, "^" val)
  if (nuova == "") print substr(resto, 1, RLENGTH)
  else $0 = substr($0, 1, n) nuova substr(resto, RLENGTH + 1)
  trovati++
}
BEGIN { q = "\""; tra_virgolette = "[^" q "]*" }
tipo == "pyproject" {
  if ($0 ~ /^\[/) sezione = $0
  if (sezione ~ /^\[project\]/) campo("^version[ \t]*=[ \t]*" q, tra_virgolette)
}
tipo == "init" { campo("^__version__[ \t]*=[ \t]*" q, tra_virgolette) }
tipo == "package" && !trovati { campo("^  " q "version" q "[ \t]*:[ \t]*" q, tra_virgolette) }
tipo == "lock" {
  # in cima (rientro di 2 spazi) e nel pacchetto "" di "packages" (rientro di 6), non nelle dipendenze
  prima = trovati
  if (!cima) { campo("^  " q "version" q "[ \t]*:[ \t]*" q, tra_virgolette); cima = (trovati > prima) }
  else if ($0 ~ "^    " q q "[ \t]*:[ \t]*[{]") radice = 1
  else if (radice && $0 ~ /^    [}]/) radice = 0
  else if (radice) { campo("^      " q "version" q "[ \t]*:[ \t]*" q, tra_virgolette); if (trovati > prima) radice = 0 }
}
tipo == "dockerfile" { campo("^ARG[ \t]+BIOLOOM_VERSION=", "[^ \t\r]*") }
tipo == "test" { campo("^ARG[ \t]+BASE=bioloom:", "[^ \t\r]*") }
tipo == "compose" { campo("[$][{]BIOLOOM_VERSION:-", "[^}]*") }
tipo == "cff" { campo("^version:[ 	]*" q, tra_virgolette) }
tipo == "zenodo" { campo("^  " q "version" q "[ 	]*:[ 	]*" q, tra_virgolette) }
nuova != "" { print }
END { if (nuova != "" && trovati != attese) exit 1 }
'

uso() { echo "uso: bash programma/scripts/versione.sh [X.Y.Z]" >&2; exit 2; }

valida() { [[ $1 =~ $FORMATO ]]; }

# copie della versione nel file ($1 tipo, $2 file), una per riga
valori() { LC_ALL=C awk -v BINMODE=3 -v tipo="$1" -v nuova="" "$AWK" "$2"; }

ha_cr() { [ -n "$(tr -dc '\r' < "$1" | tr '\r' x)" ]; }

leggi_versione() {
  [ -f "$VERSION_FILE" ] || { echo "Manca programma/launcher/VERSION (la versione di BioLoom)." >&2; exit 1; }
  VERSION="$(head -n1 "$VERSION_FILE" | tr -d '[:space:]')"
  valida "$VERSION" \
    || { echo "programma/launcher/VERSION non contiene una versione X.Y.Z: \"$VERSION\"." >&2; exit 1; }
}

# stampa una riga per ogni copia diversa da $VERSION; uscita 1 se ce ne sono
controlla() {
  local tipo file attese vals n v errori=0
  while IFS='|' read -r tipo file attese; do
    if [ ! -f "$ROOT/$file" ]; then
      echo "  programma/$file: file assente"; errori=1; continue
    fi
    vals="$(valori "$tipo" "$ROOT/$file")"
    n=0
    [ -z "$vals" ] || n="$(printf '%s\n' "$vals" | wc -l | tr -d ' ')"
    if [ "$n" -ne "$attese" ]; then
      echo "  programma/$file: $n copie della versione invece di $attese"; errori=1; continue
    fi
    while IFS= read -r v; do
      [ "$v" = "$VERSION" ] || { echo "  programma/$file: $v"; errori=1; }
    done <<< "$vals"
  done <<< "$COPIE"
  return $errori
}

# porta VERSION e tutte le copie a $1: prima prepara tutti i file, poi li scrive (niente aggiornamenti a metà)
imposta() {
  local nuova="$1" tipo file attese i=0
  TMP="$(mktemp -d "${TMPDIR:-/tmp}/bioloom-versione.XXXXXX")"
  trap 'rm -rf "$TMP"' EXIT
  while IFS='|' read -r tipo file attese; do
    i=$((i + 1))
    [ -f "$ROOT/$file" ] || { echo "Manca programma/$file: nessun file modificato." >&2; exit 1; }
    LC_ALL=C awk -v BINMODE=3 -v tipo="$tipo" -v nuova="$nuova" -v attese="$attese" "$AWK" "$ROOT/$file" \
      > "$TMP/$i" \
      || { echo "programma/$file: la versione non è dove dovrebbe ($attese copie); nessun file modificato." >&2
           exit 1; }
  done <<< "$COPIE"
  i=0
  while IFS='|' read -r tipo file attese; do
    i=$((i + 1))
    if ! cmp -s "$TMP/$i" "$ROOT/$file"; then
      cat "$TMP/$i" > "$ROOT/$file"   # cat conserva permessi e attributi del file
      echo "  aggiornato programma/$file"
    fi
  done <<< "$COPIE"
  # VERSION con il suo fine riga (CRLF nella copia di Windows)
  if [ -f "$VERSION_FILE" ] && ha_cr "$VERSION_FILE"; then
    printf '%s\r\n' "$nuova" > "$VERSION_FILE"
  else
    printf '%s\n' "$nuova" > "$VERSION_FILE"
  fi
  echo "  programma/launcher/VERSION: $nuova"
}

case $# in
  0) ;;
  1) valida "$1" || { echo "Versione non valida: \"$1\" (serve X.Y.Z, es. 4.2.0)." >&2; exit 2; }
     imposta "$1" ;;
  *) uso ;;
esac

leggi_versione
if ! differenze="$(controlla)"; then
  {
    echo "Versione $VERSION (programma/launcher/VERSION), ma queste copie sono diverse:"
    printf '%s\n' "$differenze"
    echo "Per allinearle: bash programma/scripts/versione.sh $VERSION"
  } >&2
  exit 1
fi
echo "$VERSION"
