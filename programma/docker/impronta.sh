#!/bin/sh
# Impronta del codice di BioLoom: un SHA-256 che cambia a ogni modifica dei sorgenti che entrano nell'immagine.
# La build la salva in /app/IMPRONTA; l'avviatore la ricalcola sulla cartella programma/ e, se differisce, sa che
# l'immagine non corrisponde al codice anche a parità di versione (codice aggiornato senza ricostruire l'immagine).
#
# Definizione (identica in launcher/impronta.ps1 e verificata da backend/tests/test_version.py):
#   - radice: la cartella programma/ (il contesto della build Docker);
#   - file regolari sotto backend/, frontend/ e docker/, esclusi i percorsi con un segmento che inizia con "."
#     (file e cartelle nascosti), uguale a node_modules o __pycache__ o che finisce in .egg-info, i file *.pyc e
#     i prefissi frontend/dist/, docker/vendor/, docker/qmean/, docker/test/, backend/tests/;
#   - percorsi relativi alla radice con "/", ordinati per byte (LC_ALL=C);
#   - per ogni file la riga "<SHA-256 del contenuto senza i byte CR>  <percorso>" (esadecimale minuscolo, due
#     spazi, LF): i CR si tolgono perché nella copia di Windows (git con core.autocrlf) i file di testo sono CRLF;
#   - impronta: SHA-256 (esadecimale minuscolo) di queste righe.
#
# Uso: sh impronta.sh <cartella programma>   → stampa l'impronta e basta.
# POSIX: gira con dash (build dell'immagine), con bash 3.2 di macOS e con Git Bash. Serve sha256sum, shasum
# oppure openssl; se c'è perl con Digest::SHA (macOS, Git Bash, quasi tutti i Linux) calcola tutto in un solo
# processo, con lo stesso risultato: la via in sh crea alcuni processi per file, lenta dove crearli costa (Git Bash:
# circa mezzo minuto invece di meno di un secondo). IMPRONTA_SENZA_PERL=1 forza la via in sh (per i test).
# Uscita 2 = uso sbagliato, 3 = nessuno strumento per l'SHA-256, 1 = altro errore.
set -eu

if [ $# -ne 1 ] || [ ! -d "$1" ]; then
  echo "uso: sh impronta.sh <cartella programma>" >&2
  exit 2
fi
cd "$1" || exit 2

# 64 cifre esadecimali minuscole
esadecimale() {
  case "$1" in
    *[!0-9a-f]*) return 1 ;;
  esac
  [ ${#1} -eq 64 ]
}

# file inclusi, uno per riga, ordinati per byte; -prune non scende nelle cartelle escluse (node_modules...)
elenco() {
  cartelle=""
  for c in backend frontend docker; do
    if [ -d "$c" ]; then cartelle="$cartelle $c"; fi
  done
  [ -n "$cartelle" ] || return 0
  # shellcheck disable=SC2086  # i nomi delle tre cartelle non hanno spazi
  find $cartelle \
    \( -name '.*' -o -name node_modules -o -name __pycache__ -o -name '*.egg-info' \
       -o \( -type d \( -path frontend/dist -o -path docker/vendor -o -path docker/qmean \
                        -o -path docker/test -o -path backend/tests \) \) \) -prune \
    -o -type f ! -name '*.pyc' -print | LC_ALL=C sort
}

if [ -z "${IMPRONTA_SENZA_PERL:-}" ] && command -v perl >/dev/null 2>&1 \
   && perl -MDigest::SHA -e 1 >/dev/null 2>&1; then
  impronta="$(elenco | perl -MDigest::SHA=sha256_hex -e '
    my $m = "";
    while (my $f = <STDIN>) {
      chomp $f;
      open(my $h, "<:raw", $f) or die "impronta.sh: file non leggibile: $f\n";
      my $c = do { local $/; <$h> };
      close $h;
      $c = "" unless defined $c;
      $c =~ tr/\r//d;
      $m .= sha256_hex($c) . "  $f\n";
    }
    print sha256_hex($m), "\n";')" || exit 1
else
  if command -v sha256sum >/dev/null 2>&1; then sha="sha256sum"
  elif command -v shasum >/dev/null 2>&1; then sha="shasum -a 256"
  elif command -v openssl >/dev/null 2>&1; then sha="openssl dgst -sha256"
  else
    echo "impronta.sh: serve sha256sum, shasum oppure openssl" >&2
    exit 3
  fi
  # solo le cifre, in $h: primo campo per sha256sum e shasum ("<hash>  -"), ultimo per openssl
  # ("SHA2-256(stdin)= <hash>", "(stdin)= <hash>" o solo "<hash>", secondo la versione)
  cifre() { case "$sha" in openssl*) h="${1##* }" ;; *) h="${1%% *}" ;; esac; }
  # manifesto: "<hash>  <percorso>" per ogni file
  manifesto() {
    elenco | while IFS= read -r f; do
      [ -r "$f" ] || { echo "impronta.sh: file non leggibile: $f" >&2; exit 1; }
      cifre "$(tr -d '\r' < "$f" | $sha)"
      esadecimale "$h" || { echo "impronta.sh: SHA-256 non valido per $f" >&2; exit 1; }
      printf '%s  %s\n' "$h" "$f"
    done
  }
  m="$(manifesto)" || exit 1
  # $(...) toglie l'a capo finale, che va rimesso (nessuna riga se non ci sono file)
  if [ -n "$m" ]; then cifre "$(printf '%s\n' "$m" | $sha)"; else cifre "$(printf '' | $sha)"; fi
  impronta="$h"
fi

esadecimale "$impronta" || { echo "impronta.sh: SHA-256 non valido" >&2; exit 1; }
printf '%s\n' "$impronta"
