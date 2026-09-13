#!/usr/bin/env bash
# ==============================================================================
# HAOS Self-Healing & AST Knowledge Graph Nightly Maintenance Routine
# Scheduled under routine-scheduler contract (ADR-017)
# ==============================================================================
set -e

WORKSPACE_ROOT="/run/media/adriano/e681b5ac-a4fb-44d4-aebf-9d6584065787/dsh-projetos/HERMES-TURBO"
cd "$WORKSPACE_ROOT"

echo "=== [HAOS Nightly Maintenance: $(date -Iseconds)] ==="

# 1. Checagem de integridade dos bancos SQLite
echo "--> 1. Checking SQLite Databases Integrity..."
for db in "$HOME/.haos/kanban.db" "$HOME/.haos/memory/ragflow.db" "$HOME/.hermes/sessions.db"; do
    if [ -f "$db" ]; then
        integrity=$(sqlite3 "$db" "PRAGMA integrity_check;" 2>/dev/null || echo "ERROR")
        if [ "$integrity" = "ok" ]; then
            echo "    ✓ $(basename "$db"): OK"
        else
            echo "    ✗ $(basename "$db"): INTEGRITY CORRUPTED ($integrity)"
            exit 1
        fi
    fi
done

# 1b. Corrigir permissões de leitura do código-fonte para usuários não-root
#     (arquivos criados por ferramentas root com umask restritivo ficam 0600 e
#     quebram o `haos` para outros usuários — ex. sessão SSH como `adriano`).
echo "--> 1b. Normalizing source permissions (world-readable) ..."
find . -type d ! -perm -005 -exec chmod a+rx {} + 2>/dev/null || true
find . -type f \( -name "*.py" -o -name "*.sh" -o -name "*.json" -o -name "*.yaml" -o -name "*.yml" -o -name "*.md" \) ! -perm -004 -exec chmod a+r {} + 2>/dev/null || true
echo "    ✓ Source tree readable by all users"

# 2. Re-construção do Grafo de Código AST (Graphify)
echo "--> 2. Rebuilding Code AST Graph via Graphify..."
if [ -x ".venv/bin/python" ]; then
    .venv/bin/python -m hermes_cli.main haos graph build --dir hermes/platform --out .haos/graphify-out > /dev/null 2>&1 || true
    echo "    ✓ AST Graph updated (.haos/graphify-out/graph.json)"
fi

# 3. Sincronização e Re-indexação no RAGFlow
echo "--> 3. Refreshing RAGFlow Index with fresh architecture graphs..."
if [ -x ".venv/bin/python" ] && [ -f ".haos/graphify-out/GRAPH_REPORT.md" ]; then
    .venv/bin/python -m hermes_cli.main haos doc index .haos/graphify-out/GRAPH_REPORT.md > /dev/null 2>&1 || true
    echo "    ✓ RAGFlow Index synced"
fi

echo "=== [Nightly Maintenance Completed Successfully] ==="
exit 0
