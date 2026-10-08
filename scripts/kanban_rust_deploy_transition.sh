#!/usr/bin/env bash
# scripts/kanban_rust_deploy_transition.sh
# Automação de transição segura e controlada para o Kanban Rust Runtime.
# Suporta: status, preflight, shadow, canary, rust, rollback
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

# Cores para saída
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

HAOS_EDGE_BIN="${HAOS_EDGE_BIN:-/usr/local/bin/haos-edge}"
PYTHON_BIN="${HERMES_PYTHON:-/usr/local/lib/haos-agent/venv/bin/python}"
if [ ! -x "${PYTHON_BIN}" ]; then
    PYTHON_BIN="python3"
fi

log_info() { echo -e "${BLUE}[INFO]${NC} $*"; }
log_ok() { echo -e "${GREEN}[OK]${NC} $*"; }
log_warn() { echo -e "${YELLOW}[WARN]${NC} $*"; }
log_err() { echo -e "${RED}[ERROR]${NC} $*" >&2; }

show_status() {
    echo "========================================================"
    echo "       HAOS KANBAN RUST RUNTIME DEPLOY STATUS           "
    echo "========================================================"
    echo -n "Binary 'haos-edge': "
    if [ -x "${HAOS_EDGE_BIN}" ]; then
        SHA=$(sha256sum "${HAOS_EDGE_BIN}" | awk '{print $1}')
        VER=$("${HAOS_EDGE_BIN}" --version 2>&1 || echo "unknown")
        echo -e "${GREEN}PRESENT${NC} (${VER}, sha256: ${SHA:0:16}...)"
    else
        echo -e "${RED}MISSING / NOT EXECUTABLE${NC} at ${HAOS_EDGE_BIN}"
    fi

    echo -n "Python binary: "
    if [ -x "${PYTHON_BIN}" ]; then
        PY_VER=$("${PYTHON_BIN}" --version 2>&1)
        echo -e "${GREEN}PRESENT${NC} (${PY_VER} at ${PYTHON_BIN})"
    else
        echo -e "${RED}MISSING${NC} at ${PYTHON_BIN}"
    fi

    echo "Environment Variables (current shell):"
    echo "  HERMES_KANBAN_RUST_SELECTOR: ${HERMES_KANBAN_RUST_SELECTOR:-<unset/default off>}"
    echo "  HERMES_KANBAN_RUST_CLAIM:    ${HERMES_KANBAN_RUST_CLAIM:-<unset/default off>}"
    echo "  HAOS_EDGE_BIN:               ${HAOS_EDGE_BIN:-<unset>}"

    echo "Config Resolution Check (via Python config loader):"
    "${PYTHON_BIN}" - <<EOF
from hermes_cli import kanban_rust_selector, kanban_rust_claim
print("  Effective Selector Mode:", kanban_rust_selector.get_rust_selector_mode())
print("  Effective Claim Mode:   ", kanban_rust_claim.get_rust_claim_mode())
EOF
    echo "========================================================"
}

run_preflight() {
    log_info "Executando pré-condições e testes de alta concorrência em SQLite TEMPORÁRIO (/tmp)..."
    
    # 1. Verificar binário Rust
    if [ ! -x "${HAOS_EDGE_BIN}" ]; then
        log_err "Binário haos-edge não encontrado em ${HAOS_EDGE_BIN}"
        exit 1
    fi

    # 2. Verificar subcomandos requeridos
    "${HAOS_EDGE_BIN}" kanban-select --help >/dev/null 2>&1 || {
        log_err "Subcomando 'kanban-select' não suportado pelo binário ${HAOS_EDGE_BIN}"
        exit 1
    }
    "${HAOS_EDGE_BIN}" kanban-claim --help >/dev/null 2>&1 || {
        log_err "Subcomando 'kanban-claim' não suportado pelo binário ${HAOS_EDGE_BIN}"
        exit 1
    }
    "${HAOS_EDGE_BIN}" kanban-heartbeat --help >/dev/null 2>&1 || {
        log_err "Subcomando 'kanban-heartbeat' não suportado pelo binário ${HAOS_EDGE_BIN}"
        exit 1
    }
    log_ok "Binário haos-edge possui todos os subcomandos necessários (select, claim, heartbeat)."

    # 3. Executar suite de teste em /tmp
    log_info "Rodando pytest em tests/hermes_cli/test_kanban_rust_audit_suite.py..."
    (cd "${REPO_ROOT}" && "${PYTHON_BIN}" -m pytest tests/hermes_cli/test_kanban_rust_audit_suite.py -q)
    log_ok "Auditoria de concorrência, CAS fencing, fallback de timeout e rollback concluída com sucesso."
}

set_shadow() {
    run_preflight
    echo ""
    log_info "Instruções para ativar SHADOW MODE:"
    echo "  O Shadow mode ativa apenas a observação comparativa do Selector sem afetar despacho ou claims."
    echo "  Execute no ambiente do serviço / daemon:"
    echo "    export HERMES_KANBAN_RUST_SELECTOR=shadow"
    echo "    export HERMES_KANBAN_RUST_CLAIM=off"
    echo "  Ou configure em config.yaml:"
    echo "    kanban:"
    echo "      rust_selector: shadow"
    echo "      rust_claim: off"
}

set_canary() {
    run_preflight
    echo ""
    log_info "Instruções para ativar CANARY MODE:"
    echo "  O Canary mode ativa o Selector Rust para ordenação de candidatos mantendo o Claim em Python,"
    echo "  ou ativa seletivamente por perfil isolado."
    echo "  Execute no ambiente do serviço / daemon:"
    echo "    export HERMES_KANBAN_RUST_SELECTOR=rust"
    echo "    export HERMES_KANBAN_RUST_CLAIM=off"
    echo "  Ou configure em config.yaml:"
    echo "    kanban:"
    echo "      rust_selector: rust"
    echo "      rust_claim: off"
}

set_rust() {
    run_preflight
    echo ""
    log_info "Instruções para ativar FULL RUST MODE (Selector + Claim CAS):"
    echo "  Execute no ambiente do serviço / daemon:"
    echo "    export HERMES_KANBAN_RUST_SELECTOR=rust"
    echo "    export HERMES_KANBAN_RUST_CLAIM=rust"
    echo "  Ou configure em config.yaml:"
    echo "    kanban:"
    echo "      rust_selector: rust"
    echo "      rust_claim: rust"
}

set_rollback() {
    echo ""
    log_warn "========================================================"
    log_warn "              PROCEDIMENTO DE ROLLBACK IMEDIATO         "
    log_warn "========================================================"
    echo "Para desativar 100% de qualquer integração Rust e retornar ao Python puro:"
    echo "1. No shell / ambiente:"
    echo "     export HERMES_KANBAN_RUST_SELECTOR=off"
    echo "     export HERMES_KANBAN_RUST_CLAIM=off"
    echo ""
    echo "2. No config.yaml (~/.haos/config.yaml):"
    echo "     kanban:"
    echo "       rust_selector: off"
    echo "       rust_claim: off"
    echo ""
    echo "3. Se variáveis de ambiente estiverem em drop-in do systemd (/etc/systemd/system/haos-*.service.d/):"
    echo "     remover overrides e rodar: systemctl daemon-reload && systemctl restart haos-gateway"
    log_ok "Rollback é determinístico, sem perda de dados e compatível com todas as tasks ativas."
}

case "${1:-status}" in
    status)
        show_status
        ;;
    preflight)
        run_preflight
        ;;
    shadow)
        set_shadow
        ;;
    canary)
        set_canary
        ;;
    rust)
        set_rust
        ;;
    rollback)
        set_rollback
        ;;
    *)
        echo "Uso: $0 {status|preflight|shadow|canary|rust|rollback}"
        exit 1
        ;;
esac
