#!/usr/bin/env bash
# ============================================================================
# HAOS (Hermes Agentic OS) — Standalone Control Plane Launcher
# ============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"

if [ -z "${PYTHON:-}" ]; then
    if [ -x "/usr/local/lib/haos-agent/venv/bin/python" ]; then
        PYTHON="/usr/local/lib/haos-agent/venv/bin/python"
    elif [ -x "$ROOT_DIR/venv/bin/python" ]; then
        PYTHON="$ROOT_DIR/venv/bin/python"
    else
        PYTHON="$(command -v python3)"
    fi
fi

export HAOS_HOME="${HAOS_HOME:-$HOME/.haos}"
export HERMES_HOME="${HERMES_HOME:-$HAOS_HOME}"
export PYTHONPATH="${ROOT_DIR}:${PYTHONPATH:-}"
export HAOS_HOST="${HAOS_HOST:-0.0.0.0}"
export HAOS_PORT="${HAOS_PORT:-8788}"
export HAOS_DATA_DIR="${HAOS_DATA_DIR:-$HAOS_HOME}"

echo "============================================================"
echo "🚀 Starting HAOS Standalone Control Plane"
echo "============================================================"
echo "Host:     ${HAOS_HOST}"
echo "Port:     ${HAOS_PORT}"
echo "Home:     ${HAOS_HOME}"
echo "Data:     ${HAOS_DATA_DIR}"
echo "URL:      http://${HAOS_HOST}:${HAOS_PORT}/"
echo "============================================================"

exec "${PYTHON}" "${SCRIPT_DIR}/serve_controlplane.py"
