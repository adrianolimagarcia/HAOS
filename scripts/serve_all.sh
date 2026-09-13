#!/usr/bin/env bash
# Start both Hermes Configuration Dashboard (port 9191) and HAOS Control Plane (port 8788)
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"

if [ -z "${PYTHON:-}" ]; then
    if [ -x "$ROOT_DIR/venv/bin/python" ]; then
        PYTHON="$ROOT_DIR/venv/bin/python"
    elif [ -x "$ROOT_DIR/.venv/bin/python" ]; then
        PYTHON="$ROOT_DIR/.venv/bin/python"
    elif [ -x "$HOME/.local/share/haos-agent/venv/bin/python" ]; then
        PYTHON="$HOME/.local/share/haos-agent/venv/bin/python"
    elif command -v python3 >/dev/null 2>&1; then
        PYTHON="$(command -v python3)"
    else
        PYTHON="python3"
    fi
fi

if [ -z "${HERMES:-}" ]; then
    if [ -x "$ROOT_DIR/bin/haos" ]; then
        HERMES="$ROOT_DIR/bin/haos"
    elif [ -x "$HOME/.local/bin/haos" ]; then
        HERMES="$HOME/.local/bin/haos"
    elif [ -x "$ROOT_DIR/venv/bin/hermes" ]; then
        HERMES="$ROOT_DIR/venv/bin/hermes"
    elif [ -x "$ROOT_DIR/.venv/bin/hermes" ]; then
        HERMES="$ROOT_DIR/.venv/bin/hermes"
    elif [ -x "$HOME/.local/share/haos-agent/venv/bin/hermes" ]; then
        HERMES="$HOME/.local/share/haos-agent/venv/bin/hermes"
    elif command -v haos >/dev/null 2>&1; then
        HERMES="$(command -v haos)"
    elif command -v hermes >/dev/null 2>&1; then
        HERMES="$(command -v hermes)"
    else
        HERMES="$PYTHON -m cli"
    fi
fi

export HAOS_HOME="${HAOS_HOME:-$HOME/.haos}"
export HERMES_HOME="${HERMES_HOME:-$HAOS_HOME}"
export PYTHONPATH="${ROOT_DIR}:${PYTHONPATH:-}"
export HAOS_HOST="${HAOS_HOST:-0.0.0.0}"
export HAOS_PORT="${HAOS_PORT:-8788}"
export HAOS_DATA_DIR="${HAOS_DATA_DIR:-$HAOS_HOME}"

echo "============================================================"
echo "🚀 Launching Hermes Ecosystem (HAOS + Standard Dashboard)"
echo "============================================================"

# 1. Start Hermes Standard Dashboard on internal 127.0.0.1:9119
echo "[1/3] Starting Hermes Web Dashboard backend on 127.0.0.1:9119..."
${HERMES} dashboard --host 127.0.0.1 --port 9119 --skip-build --no-open &
DASHBOARD_PID=$!

# 2. Start Proxy on 0.0.0.0:9191
echo "[2/3] Starting Hermes Dashboard Proxy on 0.0.0.0:9191..."
${PYTHON} "${SCRIPT_DIR}/serve_hermes_dashboard_proxy.py" &
PROXY_PID=$!

# 3. Start HAOS Control Plane on 0.0.0.0:8788
echo "[3/3] Starting HAOS Multi-Agent Control Plane on 0.0.0.0:8788..."
${PYTHON} "${SCRIPT_DIR}/serve_controlplane.py" &
CONTROL_PID=$!

echo ""
echo "✅ Both servers are online!"
echo "👉 Hermes Configuration Dashboard: http://${HAOS_HOST:-localhost}:9191/"
echo "👉 HAOS Control Plane (Team Graph): http://${HAOS_HOST:-localhost}:${HAOS_PORT:-8788}/"
echo "============================================================"

trap "kill -TERM ${DASHBOARD_PID} ${PROXY_PID} ${CONTROL_PID} 2>/dev/null || true" INT TERM EXIT
wait
