#!/bin/sh
# Build de release do haos-edge.
#
# O binário é vendored na imagem (distro/haos-linux/config/includes.chroot/
# usr/local/bin/haos-edge), então ele NÃO pode carregar rastros da máquina de
# build: o rustc embute caminhos absolutos dos fontes (e das dependências) nas
# strings de panic, que o `strip` não remove. O --remap-path-prefix reescreve
# esses caminhos para /build, deixando o artefato limpo e mais reprodutível.
set -e

cd "$(dirname "$0")"

REMAP="--remap-path-prefix=$(pwd)=/build --remap-path-prefix=${HOME:-/root}=/build"
export RUSTFLAGS="${RUSTFLAGS:-} ${REMAP}"

cargo build --release

BIN="target/release/haos-edge"
printf '  binario: %s (%s bytes)\n' "$BIN" "$(stat -c %s "$BIN")"
printf '  caminhos da maquina de build: %s\n' "$(strings "$BIN" | grep -c "$(pwd)" || true)"
