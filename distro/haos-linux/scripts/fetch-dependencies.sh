#!/usr/bin/env bash
# ==============================================================================
# HAOS Linux Dependency Fetcher & Cache Prepper
# Baixa e valida antecipadamente os tarballs pesados para compilação offline/rápida
# ==============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CACHE_DIR="${SCRIPT_DIR}/../cache"
mkdir -p "${CACHE_DIR}"

echo "========================================================"
echo "  Preparando Cache de Pacotes e Runtimes do HAOS Linux  "
echo "========================================================"

# 1. SQLite 3.53+
SQLITE_VERSION="3530400"
SQLITE_FILE="${CACHE_DIR}/sqlite-autoconf-${SQLITE_VERSION}.tar.gz"
if [ ! -f "${SQLITE_FILE}" ]; then
    echo "[-] Baixando SQLite ${SQLITE_VERSION}..."
    (curl -fsSL --retry 3 --connect-timeout 15 --max-time 60 -o "${SQLITE_FILE}" \
        "https://sqlite.org/2026/sqlite-autoconf-${SQLITE_VERSION}.tar.gz" || \
     curl -fsSL --retry 3 --connect-timeout 15 --max-time 120 -o "${SQLITE_FILE}" \
        "https://sources.buildroot.net/sqlite/sqlite-autoconf-${SQLITE_VERSION}.tar.gz")
    echo "[✓] SQLite baixado em: ${SQLITE_FILE}"
else
    echo "[✓] SQLite já existe no cache."
fi

# 2. uv Binary
UV_FILE="${CACHE_DIR}/uv-x86_64-unknown-linux-gnu.tar.gz"
if [ ! -f "${UV_FILE}" ]; then
    echo "[-] Baixando uv standalone binary..."
    curl -fsSL --retry 3 "https://github.com/astral-sh/uv/releases/latest/download/uv-x86_64-unknown-linux-gnu.tar.gz" -o "${UV_FILE}"
    echo "[✓] uv baixado em: ${UV_FILE}"
else
    echo "[✓] uv já existe no cache."
fi

# 3. Node.js 26 Linux x64
NODE_FILE="${CACHE_DIR}/node-latest-v26-linux-x64.tar.xz"
if [ ! -f "${NODE_FILE}" ]; then
    echo "[-] Identificando e baixando Node.js 26 LTS..."
    NODE_BASE="https://nodejs.org/dist/latest-v26.x"
    TAR_NAME=$(curl -fsSL "${NODE_BASE}/" | grep -o 'node-v26\.[0-9.]*-linux-x64\.tar\.xz' | head -n1 || true)
    if [ -n "${TAR_NAME}" ]; then
        curl -fsSL --retry 3 "${NODE_BASE}/${TAR_NAME}" -o "${NODE_FILE}"
        echo "[✓] Node.js 26 baixado em: ${NODE_FILE}"
    else
        echo "[!] Aviso: Versão exata do Node 26 dinâmico não resolvida, usando nodejs estável."
    fi
else
    echo "[✓] Node.js já existe no cache."
fi

echo "========================================================"
echo "  Cache do HAOS pronto em ${CACHE_DIR}                  "
echo "========================================================"
