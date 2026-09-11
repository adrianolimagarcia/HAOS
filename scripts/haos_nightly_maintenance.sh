#!/usr/bin/env bash
# ==============================================================================
# HAOS Nightly Maintenance — integridade dos bancos + memória canônica
#
# Rotina periódica do nó (cron nativo do HAOS: `hermes cron create`). Resolve o
# tree do agente em vez de fixar caminho de máquina: /opt/haos no appliance
# (ou $HAOS_AGENT_DIR), caindo no repo deste script em dev. Sem isso o script
# só rodava na máquina do desenvolvedor (WORKSPACE_ROOT hardcoded) e abortava
# no appliance por causa do `set -e`.
#
# Passos:
#   1. integridade dos SQLite reais do nó
#   2. memória canônica: DeepDoc/RAG + GraphRAG + dream
#      (scripts/haos_memory_populate.py)
#   3. grafo AST do código — opcional, ligue com HAOS_MAINTENANCE_AST=1
# ==============================================================================
set -uo pipefail

# --- caminhos portáteis ------------------------------------------------------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# Instalação fora da ISO (install_haos.sh) grava o caminho do tree do agente em
# <home>/scripts/haos_agent_dir; sem ponteiro, cai nas heurísticas abaixo.
AGENT_PTR="${SCRIPT_DIR}/haos_agent_dir"
if [ -f "${AGENT_PTR}" ] && [ -d "$(cat "${AGENT_PTR}" 2>/dev/null)" ]; then
    AGENT_DIR="$(cat "${AGENT_PTR}")"
elif [ -n "${HAOS_AGENT_DIR:-}" ] && [ -d "${HAOS_AGENT_DIR}" ]; then
    AGENT_DIR="${HAOS_AGENT_DIR}"
elif [ -d /opt/haos ]; then
    AGENT_DIR="/opt/haos"
else
    AGENT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
fi

if [ -x "${AGENT_DIR}/venv/bin/python" ]; then
    PY="${AGENT_DIR}/venv/bin/python"
elif [ -x "${AGENT_DIR}/.venv/bin/python" ]; then
    PY="${AGENT_DIR}/.venv/bin/python"
else
    PY="$(command -v python3 || true)"
fi

# O runtime resolve o store por HERMES_HOME/HAOS_HOME; sem env ele cai em
# ~/.hermes e escreve num store órfão. Fixamos ambos no store do nó.
export HAOS_HOME="${HAOS_HOME:-${HOME}/.haos}"
export HERMES_HOME="${HERMES_HOME:-${HAOS_HOME}}"
export PYTHONPATH="${AGENT_DIR}${PYTHONPATH:+:${PYTHONPATH}}"

FAILED=0
echo "=== [HAOS Nightly Maintenance: $(date -Iseconds)] ==="
echo "    agent_dir=${AGENT_DIR} python=${PY:-ausente} home=${HAOS_HOME}"

# --- 1. integridade dos bancos ----------------------------------------------
echo "--> 1. Integridade dos SQLite"
for db in \
    "${HAOS_HOME}/state.db" \
    "${HAOS_HOME}/kanban.db" \
    "${HAOS_HOME}/memory/ragflow.db" \
    "${HAOS_HOME}/memory/reconciled_memories.db" \
    "${HAOS_HOME}/memory/graphrag.db" \
    "${HAOS_HOME}/cron/executions.db"
do
    [ -f "$db" ] || continue
    integrity="$(sqlite3 "$db" "PRAGMA integrity_check;" 2>/dev/null || echo "ERRO-LEITURA")"
    if [ "$integrity" = "ok" ]; then
        echo "    ✓ $(basename "$db"): ok"
    else
        echo "    ✗ $(basename "$db"): CORROMPIDO ($integrity)"
        FAILED=1
    fi
done

# --- 2. memória canônica ----------------------------------------------------
echo "--> 2. Memória canônica (DeepDoc/RAG + GraphRAG + dream)"
# Prefere a cópia local (instalação fora da ISO instala o populate junto do
# nightly em <home>/scripts/), caindo no tree do agente.
POPULATE="${SCRIPT_DIR}/haos_memory_populate.py"
[ -f "${POPULATE}" ] || POPULATE="${AGENT_DIR}/scripts/haos_memory_populate.py"
if [ -n "${PY:-}" ] && [ -f "${POPULATE}" ]; then
    if "$PY" "${POPULATE}" --home "${HAOS_HOME}"; then
        echo "    ✓ memória canônica atualizada"
    else
        echo "    ✗ falha ao popular a memória canônica"
        FAILED=1
    fi
else
    echo "    ⊘ pulado (python ou scripts/haos_memory_populate.py ausente)"
fi

# --- 3. grafo AST do código (opcional) --------------------------------------
if [ "${HAOS_MAINTENANCE_AST:-0}" = "1" ]; then
    echo "--> 3. Grafo AST do código (HAOS_MAINTENANCE_AST=1)"
    if [ -n "${PY:-}" ] && [ -d "${AGENT_DIR}/hermes/platform" ]; then
        if "$PY" -m hermes_cli.main haos graph build \
                --dir "${AGENT_DIR}/hermes/platform" \
                --out "${AGENT_DIR}/.haos/graphify-out" >/dev/null 2>&1; then
            echo "    ✓ grafo AST reconstruído"
        else
            echo "    ✗ falha no grafo AST"
            FAILED=1
        fi
    else
        echo "    ⊘ pulado (python ou árvore de código ausente)"
    fi
else
    echo "--> 3. Grafo AST do código: pulado (defina HAOS_MAINTENANCE_AST=1 para ativar)"
fi

if [ "$FAILED" -eq 0 ]; then
    echo "=== [Nightly Maintenance concluída com sucesso] ==="
else
    echo "=== [Nightly Maintenance concluída COM FALHAS] ==="
fi
exit "$FAILED"
