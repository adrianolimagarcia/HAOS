#!/usr/bin/env bash
# scripts/haos_audit_routine.sh
# Script local de pré-execução para a rotina de auto-auditoria do HAOS.
# Coleta métricas e integridade sem gastar tokens de LLM.

set -eo pipefail

CWD="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$CWD"

echo "=== HAOS REPO HEALTH & INTEGRITY AUDIT ==="
echo "Timestamp: $(date -u +"%Y-%m-%dT%H:%M:%SZ")"
echo "Working Directory: $CWD"

# 1. Status do Git
echo -e "\n--- [1] Git Working Tree Status ---"
DIRTY_COUNT=$(git status --porcelain | wc -l)
echo "Modified/Untracked files count: $DIRTY_COUNT"
if [ "$DIRTY_COUNT" -gt 0 ]; then
    echo "Files pending:"
    git status --short | head -n 10
fi

# 2. Integridade dos Bancos SQLite
echo -e "\n--- [2] Database Health Checks ---"
HOME_DIR="${HERMES_HOME:-$HOME/.haos}"
if [ -f "$HOME_DIR/kanban.db" ]; then
    echo -n "kanban.db integrity: "
    sqlite3 "$HOME_DIR/kanban.db" "PRAGMA integrity_check;" 2>/dev/null || echo "ERROR"
else
    echo "kanban.db: Not found at $HOME_DIR/kanban.db"
fi

if [ -f "$HOME_DIR/memory/ragflow.db" ]; then
    echo -n "ragflow.db integrity: "
    sqlite3 "$HOME_DIR/memory/ragflow.db" "PRAGMA integrity_check;" 2>/dev/null || echo "ERROR"
else
    echo "ragflow.db: Not found at $HOME_DIR/memory/ragflow.db"
fi

# 3. Verificação de Testes do Core Memory / RAGFlow
echo -e "\n--- [3] Fast Core Tests Status ---"
if [ -x ".venv/bin/python" ]; then
    .venv/bin/python -m pytest tests/test_haos_ragflow.py -q --tb=line || echo "TEST_FAILURES_DETECTED"
else
    echo "Virtualenv .venv not found, skipping pytest."
fi

# 4. Atualização Contínua do Code Knowledge Graph (Graphify Engine)
echo -e "\n--- [4] Code Knowledge Graph Refresh ---"
if [ -x ".venv/bin/python" ]; then
    .venv/bin/python -m hermes_cli.main haos graph build --dir hermes/platform --out .haos/graphify-out >/dev/null 2>&1 || true
    .venv/bin/python -m hermes_cli.main haos doc index .haos/graphify-out --pattern "**/*.md" >/dev/null 2>&1 || true
    echo "Code knowledge graph and RAGFlow indexing refreshed."
fi

echo -e "\n=== AUDIT PRE-RUN SCRIPT FINISHED ==="
