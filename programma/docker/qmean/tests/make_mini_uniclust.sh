#!/bin/bash
# Crea un mini database in formato UniClust30 (HH-suite: a3m, hhm, cs219) per provare QMEAN locale
# senza il disco esterno. Si esegue dentro l'immagine QMEAN, che contiene HH-suite:
#   docker run --rm --entrypoint bash -v <cartella>:/out makphi-qmean:<versione> /tests/make_mini_uniclust.sh /out
# I punteggi ottenuti con questo database NON sono significativi: serve solo a verificare il flusso.
set -euo pipefail
out="${1:-/out}"
name="mini_uniclust30"
work="$(mktemp -d)"
cd "$work"
cat > seqs.fasta <<'EOF'
>ubq_human ubiquitina umana
MQIFVKTLTGKTITLEVEPSDTIENVKAKIQDKEGIPPDQQRLIFAGKQLEDGRTLSDYNIQKESTLHLVLRLRGG
>ubq_yeast ubiquitina di lievito
MQIFVKTLTGKTITLEVESSDTIDNVKSKIQDKEGIPPDQQRLIFAGKQLEDGRTLSDYNIQKESTLHLVLRLRGG
>nedd8_human NEDD8
MLIKVKTLTGKEIEIDIEPTDKVERIKERVEEKEGIPPQQQRLIYSGKQMNDEKTAADYKILGGSVLHLVLALRGG
>sumo1_human SUMO1 (dominio ubiquitin-like)
MSDQEAKPSTEDLGDKKEGEYIKLKVIGQDSSEIHFKVKMTTHLKKLKESYCQRQGVPMNSLRFLFEGQRIADNHTPKELGMEEEDVIEVYQEQTGG
>ubl4a_human UBL4A
MQLTVKALQGRECSLQVPEDELVSTLKQLVSEKLNVPVRQQRLLFKGKALADGKRLSDYSIGPNSKLNLVVKPLEK
EOF
mkdir a3m
awk '/^>/{name=substr($1,2); f="a3m/" name ".a3m"} {print > f}' seqs.fasta
ffindex_build -s "${name}_a3m.ffdata" "${name}_a3m.ffindex" a3m/
ffindex_apply "${name}_a3m.ffdata" "${name}_a3m.ffindex" -i "${name}_hhm.ffindex" -d "${name}_hhm.ffdata" \
    -- hhmake -i stdin -o stdout -v 0
cstranslate -f -x 0.3 -c 4 -I a3m -i "${name}_a3m" -o "${name}_cs219" \
    -A "${HHLIB:-/usr/local}/data/cs219.lib" -D "${HHLIB:-/usr/local}/data/context_data.lib"
mkdir -p "$out"
cp "${name}"_{a3m,hhm,cs219}.ff{data,index} "$out/"
ls -la "$out"
