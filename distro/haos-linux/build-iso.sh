#!/usr/bin/env bash
# ==============================================================================
# HAOS Linux ISO Generator via Docker Container Builder
# ==============================================================================
# Embutir no artefato: instalador (scripts/install_haos.sh) + deploy key
# read-only (github.com/adrianolimagarcia/HAOS) para 'haos update' funcionar
# direto do repo privado em toda instalação.
#   - A chave privada NUNCA é commitada: entra no includes.chroot SÓ durante o
#     build (a partir de $HAOS_UPDATE_KEY ou /root/.haos/keys/update_ed25519)
#     e é apagada do tree ao final (ver trap abaixo).
#   - O destino está no .gitignore como rede de segurança extra.
# ==============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "${SCRIPT_DIR}"

REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
IMAGE_BUILDER="haos-iso-builder:latest"

# Destinos dentro do perfil live-build (copiados para o chroot da imagem)
KEY_DEST="config/includes.chroot/etc/haos/keys/update_ed25519"
INSTALLER_DEST="config/includes.chroot/usr/local/sbin/haos-install"

# 1. Injetar instalador + deploy key read-only (antes do build)
KEY_SRC="${HAOS_UPDATE_KEY:-/root/.haos/keys/update_ed25519}"
INSTALLER_SRC="${REPO_ROOT}/scripts/install_haos.sh"

echo "========================================================"
echo "  Preparando artefatos embutidos da imagem..."
echo "========================================================"

if [ -f "${KEY_SRC}" ]; then
    install -D -m 600 "${KEY_SRC}" "${KEY_DEST}"
    echo "  ✓ Deploy key read-only embutida: ${KEY_DEST}"
else
    echo "  ⚠ Chave read-only não encontrada em ${KEY_SRC} (use HAOS_UPDATE_KEY=<path>)."
    echo "    'haos update' na imagem dependerá de outra credencial."
fi

if [ -f "${INSTALLER_SRC}" ]; then
    install -D -m 755 "${INSTALLER_SRC}" "${INSTALLER_DEST}"
    echo "  ✓ Instalador embutido: ${INSTALLER_DEST}"
else
    echo "  ⚠ Instalador não encontrado em ${INSTALLER_SRC}."
fi

# Limpeza garantida mesmo se o build falhar (a chave privada NUNCA fica no tree)
cleanup_injected() {
    rm -f "${KEY_DEST}" "${INSTALLER_DEST}"
    rmdir "$(dirname "${KEY_DEST}")" 2>/dev/null || true
    echo "  ✓ Artefatos injetados removidos do working tree."
}
trap cleanup_injected EXIT

echo "========================================================"
echo "  Construindo imagem Docker de build do HAOS Linux...   "
echo "========================================================"
docker build -f Dockerfile.builder -t "${IMAGE_BUILDER}" .

echo "========================================================"
echo "  Iniciando a geração da ISO do HAOS Linux via Docker   "
echo "========================================================"

docker run --rm --privileged \
    -v "${SCRIPT_DIR}:/build" \
    -w /build \
    "${IMAGE_BUILDER}" \
    bash -c '
        set -euo pipefail
        echo "[-] Limpando builds anteriores..."
        lb clean --purge || true

        echo "[-] Configurando parâmetros da imagem..."
        lb config

        echo "[-] Executando build da imagem ISO (este processo pode demorar alguns minutos)..."
        lb build

        echo "[✓] Build finalizado!"
        ls -lh *.iso 2>/dev/null || true
    '
