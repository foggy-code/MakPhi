#!/bin/bash
# Sostituisce /usr/lib/mmseqs2/simd-dispatch dell'immagine nei container avviati da bioloom-podman.sh con Podman < 3.
# L'originale sceglie la versione di mmseqs con "[ -x file ]", che con la glibc recente usa la chiamata faccessat2:
# la libseccomp dei server vecchi (2.4.x, RHEL 8.3) non la conosce e il filtro seccomp la rifiuta, quindi il test
# fallisce su ogni file e il dispatcher cerca mmseqs-plain, che non c'è. "[ -f file ]" usa stat e funziona.

BASE=/usr/bin/mmseqs

for SIMD in avx2 avx sse4.1 ssse3 sse3 sse2 sse; do
	if grep -q "$SIMD" /proc/cpuinfo && [ -f "${BASE}-${SIMD}" ]; then
		exec "${BASE}-${SIMD}" "$@"
	fi
done

exec "${BASE}-plain" "$@"
