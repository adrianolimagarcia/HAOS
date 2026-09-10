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
# Runtimes pré-baixados (Node 26, uv, SQLite): os hooks 10/15/20/30 consomem
# este diretório dentro do chroot e o removem ao final (nada vai para a ISO).
FACTORY_SRC="${SCRIPT_DIR}/cache"
FACTORY_DEST="config/includes.chroot/usr/local/src/haos-factory"

# 1. Injetar instalador + deploy key read-only (antes do build)
KEY_SRC="${HAOS_UPDATE_KEY:-/root/.haos/keys/update_ed25519}"
INSTALLER_SRC="${REPO_ROOT}/scripts/install_haos.sh"

echo "========================================================"
echo "  Preparando artefatos embutidos da imagem..."
echo "========================================================"

# O wrapper-antigravity é vendorizado no perfil (reprodutível), então não
# acompanha o upstream sozinho. Este aviso não é fatal: build offline continua
# funcionando, mas uma ISO com o wrapper atrasado avisa em vez de passar
# silencioso. Atualizar = ./sync-antigravity-wrapper.sh --apply
if [ -x "${SCRIPT_DIR}/sync-antigravity-wrapper.sh" ]; then
    "${SCRIPT_DIR}/sync-antigravity-wrapper.sh" --check || true
fi

if [ -f "${KEY_SRC}" ]; then
    # Só injeta a chave PRIVADA (a .pub é inútil para autenticar o fetch).
    if grep -q -- "-----BEGIN .*PRIVATE KEY-----" "${KEY_SRC}" 2>/dev/null; then
        install -D -m 600 "${KEY_SRC}" "${KEY_DEST}"
        echo "  ✓ Deploy key read-only embutida: ${KEY_DEST}"
    else
        echo "  ⚠ ${KEY_SRC} não parece ser a chave PRIVADA (falta o cabeçalho"
        echo "    '-----BEGIN ... PRIVATE KEY-----'). Aponte HAOS_UPDATE_KEY para a"
        echo "    chave privada — a .pub não autentica o fetch."
    fi
else
    echo "  ⚠ Chave read-only não encontrada em ${KEY_SRC} (use HAOS_UPDATE_KEY=<path>)."
    echo "    'haos update' na imagem dependerá de outra credencial (repo público usa https sem chave)."
fi

if [ -f "${INSTALLER_SRC}" ]; then
    install -D -m 755 "${INSTALLER_SRC}" "${INSTALLER_DEST}"
    echo "  ✓ Instalador embutido: ${INSTALLER_DEST}"
else
    echo "  ⚠ Instalador não encontrado em ${INSTALLER_SRC}."
fi

# Runtimes pré-baixados p/ os hooks (offline-friendly). Sem eles, os hooks
# baixam da rede nas mesmas URLs do fetch-dependencies.sh — este passo existe
# para tornar o build reprodutível e rápido, não obrigatório.
if ls "${FACTORY_SRC}"/node-*.tar.xz "${FACTORY_SRC}"/uv-*.tar.gz "${FACTORY_SRC}"/sqlite-*.tar.gz >/dev/null 2>&1; then
    mkdir -p "${FACTORY_DEST}"
    install -m 644 "${FACTORY_SRC}"/node-*.tar.xz "${FACTORY_SRC}"/uv-*.tar.gz "${FACTORY_SRC}"/sqlite-*.tar.gz "${FACTORY_DEST}/"
    echo "  ✓ Runtimes pré-baixados injetados p/ hooks (node/uv/sqlite)."
else
    echo "  ⚠ cache/ sem tarballs de runtime — hooks vão baixar da rede"
    echo "    (para build offline antes rode ./scripts/fetch-dependencies.sh)."
fi

# Limpeza garantida mesmo se o build falhar (a chave privada NUNCA fica no tree)
cleanup_injected() {
    rm -f "${KEY_DEST}" "${INSTALLER_DEST}"
    rmdir "$(dirname "${KEY_DEST}")" 2>/dev/null || true
    # Runtimes de fábrica: remove também a cópia de staging do chroot (a do
    # includes.chroot o hook 30 remove dentro do build; a do chroot/ fica se o
    # build falhar no meio).
    rm -rf "${FACTORY_DEST}" "${SCRIPT_DIR}/chroot/usr/local/src/haos-factory" 2>/dev/null || true
    # O build também deixa cópias da chave na árvore de estágio do live-build
    # (chroot/etc/haos/keys/ e chroot/home/haos/.haos/keys/ — o hook que semeia a
    # cópia do nó). O `rm -f` acima só alcança a cópia injetada em
    # config/includes.chroot, então sem esta varredura uma chave PRIVADA ficava
    # parada em distro/haos-linux/chroot/ depois de cada build — e o contexto do
    # `docker build` é este diretório inteiro.
    #
    # A varredura é por NOME DE ARQUIVO: nada de `rm -rf` em diretório de build
    # (o `lb clean` já apagou `chroot/` através de um bind mount vivo e
    # quase levou o disco junto). A fonte (o cofre) nunca é tocada.
    local _src _found
    _src="$(realpath -m "${KEY_SRC}" 2>/dev/null || printf '%s' "${KEY_SRC}")"
    while IFS= read -r _found; do
        if [ "$(realpath -m "${_found}" 2>/dev/null || printf '%s' "${_found}")" = "${_src}" ]; then
            continue
        fi
        rm -f "${_found}"
    done < <(find "${SCRIPT_DIR}" -type f -name 'update_ed25519' 2>/dev/null || true)
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
        # NOTA: `lb clean` (sem --purge) remove chroot/ e binary/ mas PRESERVA o
        # cache — e como /build é o diretório do host via bind mount, `--purge`
        # apagava o cache/ do host inteiro (tarballs de runtime + caches apt do
        # live-build), destruindo o "cache offline" e forçando re-download total
        # a cada build. Para um rebuild do zero: `lb clean --purge` à mão.
        lb clean || true

        echo "[-] Configurando parâmetros da imagem..."
        lb config

        echo "[-] Executando build da imagem ISO (este processo pode demorar alguns minutos)..."
        lb build

        echo "[✓] Build finalizado!"
        ls -lh *.iso 2>/dev/null || true
    '
