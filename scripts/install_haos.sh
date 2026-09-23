#!/usr/bin/env bash
# ============================================================================
# HAOS (Hermes Agentic OS) — Universal Standalone Installer
# ============================================================================
# Installs HAOS from github.com/adrianolimagarcia/HAOS (default branch: main;
# --branch seleciona outra, ex.: haos-standalone).
# Designed to coexist safely with or without an existing upstream Hermes installation.
# Sets up isolated virtual environment, dependencies, CLI binaries, and default configs.
# Provisions systemd services for the controlplane and the gateway so HAOS comes
# back after a reboot (falling back to nohup when the host has no systemd), and
# installs the curated `[all]` extras by default — without them the install comes
# up without MCP servers and without the providers the user configured.
#
# Usage:
#   curl -fsSL https://raw.githubusercontent.com/adrianolimagarcia/HAOS/main/scripts/install_haos.sh | bash
#
# Or with options:
#   ./scripts/install_haos.sh --branch main --haos-home ~/.haos \
#       --update-key /path/to/haos-update-key   # read-only deploy key for private-repo `haos update`
#
# Core-only install with no services (containers, CI, tests):
#   ./scripts/install_haos.sh --no-extras --no-services
# ============================================================================

set -euo pipefail

# Visuals
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m'

log_info()  { echo -e "${CYAN}ℹ${NC} $1"; }
log_ok()    { echo -e "${GREEN}✓${NC} $1"; }
log_warn()  { echo -e "${YELLOW}⚠${NC} $1"; }
log_error() { echo -e "${RED}✗${NC} $1"; }
log_step()  { echo -e "\n${BOLD}${BLUE}==>${NC} ${BOLD}$1${NC}"; }

# Defaults
REPO_URL="${HAOS_REPO_URL:-https://github.com/adrianolimagarcia/HAOS.git}"
# Repo HEAD aponta para `main` (haos-standalone e main estão no mesmo commit hoje);
# --branch continua disponível para escolher outra.
BRANCH="${HAOS_BRANCH:-main}"
HAOS_HOME="${HAOS_HOME:-$HOME/.haos}"

if [ "$(id -u)" -eq 0 ]; then
    DEFAULT_INSTALL_DIR="/usr/local/lib/haos-agent"
    BIN_DIR="/usr/local/bin"
else
    DEFAULT_INSTALL_DIR="$HOME/.local/share/haos-agent"
    BIN_DIR="$HOME/.local/bin"
fi

INSTALL_DIR="${HAOS_INSTALL_DIR:-$DEFAULT_INSTALL_DIR}"
SKIP_SYSTEM_DEPS=false
API_KEY="${A6_API_KEY:-}"
# Read-only deploy key (private HAOS repo) used by `haos update`; never committed to the repo.
UPDATE_KEY_SRC="${HAOS_UPDATE_KEY:-}"
# Extras da venv: o conjunto curado `[all]` do pyproject — o mesmo que o Dockerfile
# e o scripts/install.sh usam (`[mcp]`, `[homeassistant]`, `[google]`, `[web]`...).
# Sem eles a instalação sobe sem MCP (servidores ficam "parked") e sem os
# provedores que o usuário configurou. `--extras none` instala só o core.
HAOS_EXTRAS="${HAOS_EXTRAS:-all}"
# Serviços systemd provisionados, para o HAOS voltar sozinho depois de um reboot.
HAOS_SERVICES="${HAOS_SERVICES:-controlplane,gateway}"
# Componente opcional: Ouroboros (engine autônomo de auto-evolução, Ralph loops, Socratic PM e QA).
# Extremamente recomendado para fornecer suporte a loops autônomos ao HAOS.
# Valores: auto (pergunta se interativo; instala por default se não-interativo), true, false.
HAOS_OUROBOROS="${HAOS_OUROBOROS:-auto}"

# Argument parsing
while [[ $# -gt 0 ]]; do
    case "$1" in
        --dir) INSTALL_DIR="$2"; shift 2 ;;
        --branch) BRANCH="$2"; shift 2 ;;
        --haos-home) HAOS_HOME="$2"; shift 2 ;;
        --api-key) API_KEY="$2"; shift 2 ;;
        --update-key) UPDATE_KEY_SRC="$2"; shift 2 ;;
        --extras) HAOS_EXTRAS="$2"; shift 2 ;;
        --no-extras) HAOS_EXTRAS="none"; shift ;;
        --services) HAOS_SERVICES="$2"; shift 2 ;;
        --no-services) HAOS_SERVICES="none"; shift ;;
        --ouroboros|--with-ouroboros) HAOS_OUROBOROS="true"; shift ;;
        --no-ouroboros|--without-ouroboros) HAOS_OUROBOROS="false"; shift ;;
        --skip-system-deps) SKIP_SYSTEM_DEPS=true; shift ;;
        --help|-h)
            echo "HAOS Standalone Installer"
            echo "Options:"
            echo "  --dir <path>          Target checkout directory (default: $DEFAULT_INSTALL_DIR)"
            echo "  --branch <name>       Git branch to clone (default: main)"
            echo "  --haos-home <path>    Configuration & data home directory (default: ~/.haos)"
            echo "  --api-key <key>       A6API Key to register in .env"
            echo "  --update-key <path>   Read-only deploy key for private-repo 'haos update'"
            echo "  --extras <list|none>  Extras pip da venv (default: all = conjunto curado)"
            echo "  --no-extras           Alias de --extras none (só o core)"
            echo "  --services <list|none> Serviços systemd (default: controlplane,gateway)"
            echo "  --no-services         Não provisiona serviço nenhum"
            echo "  --ouroboros           Instala o Ouroboros autonomous engine (extremamente recomendado)"
            echo "  --no-ouroboros        Pula a instalação do Ouroboros autonomous engine"
            echo "  --skip-system-deps    Skip apt/pacman/dnf package installation"
            exit 0
            ;;
        *) log_warn "Unknown argument: $1"; shift ;;
    esac
done

# Resolução da instalação do Ouroboros (opcional, mas extremamente recomendado)
INSTALL_OUROBOROS=false
if [ "$HAOS_OUROBOROS" = "true" ]; then
    INSTALL_OUROBOROS=true
elif [ "$HAOS_OUROBOROS" = "false" ]; then
    INSTALL_OUROBOROS=false
elif [ -t 0 ]; then
    echo ""
    echo -e "${BOLD}${BLUE}==>${NC} ${BOLD}Componente Opcional: Ouroboros Autonomous Evolution Engine${NC}"
    echo -e "O Ouroboros habilita loops autônomos de engenharia (Ralph loop, Socratic PM,"
    echo -e "geração formal de especificações, verificação de QA e auto-evolução contínua)."
    echo -e "${BOLD}${YELLOW}⭐ [EXTREMAMENTE RECOMENDADO para capacitar o HAOS com auto-evolução]${NC}"
    read -r -p "Deseja instalar o engine autônomo Ouroboros? [Y/n]: " ouroboros_choice
    case "${ouroboros_choice:-Y}" in
        [yY][eE][sS]|[yY]|"")
            INSTALL_OUROBOROS=true
            ;;
        *)
            INSTALL_OUROBOROS=false
            ;;
    esac
else
    INSTALL_OUROBOROS=true
    log_info "Ouroboros engine (opcional, extremamente recomendado): instalando por padrão (use --no-ouroboros para ignorar)."
fi

echo -e "${CYAN}"
echo "  ██╗  ██╗ █████╗  ██████╗ ███████╗"
echo "  ██║  ██║██╔══██╗██╔═══██╗██╔════╝"
echo "  ███████║███████║██║   ██║███████╗"
echo "  ██╔══██║██╔══██║██║   ██║╚════██║"
echo "  ██║  ██║██║  ██║╚██████╔╝███████║"
echo "  ╚═╝  ╚═╝╚═╝  ╚═╝ ╚═════╝ ╚══════╝"
echo "  Hermes Agentic Multi-Agent OS Installer"
echo -e "${NC}"

log_info "Target installation directory: ${BOLD}$INSTALL_DIR${NC}"
log_info "Configuration directory (HAOS_HOME): ${BOLD}$HAOS_HOME${NC}"
log_info "Git repository: ${BOLD}$REPO_URL ($BRANCH)${NC}"

# 1. System Package Detection
if [ "$SKIP_SYSTEM_DEPS" = false ]; then
    log_step "Checking and installing required system packages..."
    SUDO=""
    if [ "$(id -u)" -ne 0 ] && command -v sudo >/dev/null 2>&1; then
        SUDO="sudo"
    fi

    if command -v pacman >/dev/null 2>&1; then
        log_info "Arch/CachyOS detected (pacman). Ensuring git, curl, socat, base-devel..."
        $SUDO pacman -S --needed --noconfirm git curl socat base-devel python >/dev/null 2>&1 || true
    elif command -v apt-get >/dev/null 2>&1; then
        log_info "Debian/Ubuntu detected (apt). Ensuring git, curl, socat, build-essential, python3-venv..."
        $SUDO apt-get update -qq >/dev/null 2>&1 || true
        $SUDO apt-get install -y -qq git curl socat build-essential python3-venv python3-pip >/dev/null 2>&1 || true
    elif command -v dnf >/dev/null 2>&1; then
        log_info "Fedora/RHEL detected (dnf). Ensuring git, curl, socat, gcc..."
        $SUDO dnf install -y git curl socat gcc make python3-devel >/dev/null 2>&1 || true
    elif command -v brew >/dev/null 2>&1; then
        log_info "macOS detected (brew). Ensuring git, curl, socat..."
        brew install git curl socat >/dev/null 2>&1 || true
    fi
fi

# 2. Check Git & Curl
command -v git >/dev/null 2>&1 || { log_error "git is required. Please install git."; exit 1; }
command -v curl >/dev/null 2>&1 || { log_error "curl is required. Please install curl."; exit 1; }

# 3. Clone or Update Repo
# -----------------------------------------------------------------------------
# Um repo PRIVADO só pode ser clonado com a deploy key read-only (SSH) — o clone
# https exige credencial interativa. Por isso a chave é localizada ANTES do
# primeiro git e, quando presente, o clone/fetch vai por SSH. Sem chave, o https
# anônimo funciona apenas se o repositório for público (o `haos update` então
# também funciona sem credencial — ver nota no fim).
_resolve_update_key() {
    if [ -n "$UPDATE_KEY_SRC" ] && [ -r "$UPDATE_KEY_SRC" ]; then
        return
    fi
    # Só chaves LEGÍVEIS contam: o instalador pode rodar como root ou como um
    # usuário comum que não enxerga /root/.haos nem /etc/haos (0600 root).
    for _cand in "/root/.haos/keys/update_ed25519" "/etc/haos/keys/update_ed25519" \
                 "/home/haos/.haos/keys/update_ed25519" "$HOME/.haos/keys/update_ed25519"; do
        if [ -r "$_cand" ]; then
            UPDATE_KEY_SRC="$_cand"
            return
        fi
    done
}
_resolve_update_key

log_step "Fetching HAOS repository from GitHub..."
mkdir -p "$(dirname "$INSTALL_DIR")"

# Trust github.com host key before the first non-interactive SSH contact
# (root writes the global file; a normal user gets ~/.ssh/known_hosts).
_register_github_host_key() {
    if [ "$(id -u)" -eq 0 ] && [ -w /etc/ssh/ ]; then
        _KNOWN_HOSTS="/etc/ssh/ssh_known_hosts"
    else
        _KNOWN_HOSTS="$HOME/.ssh/known_hosts"
        mkdir -p "$HOME/.ssh" 2>/dev/null || true
    fi
    [ -f "$_KNOWN_HOSTS" ] || touch "$_KNOWN_HOSTS" 2>/dev/null || true
    if ! ssh-keygen -F github.com -f "$_KNOWN_HOSTS" >/dev/null 2>&1; then
        ssh-keyscan -t ed25519 github.com >> "$_KNOWN_HOSTS" 2>/dev/null || \
            log_warn "Could not write host key to $_KNOWN_HOSTS"
    fi
}

_SSH_REPO="git@github.com:adrianolimagarcia/HAOS.git"
_HTTPS_REPO="https://github.com/adrianolimagarcia/HAOS.git"
_USE_SSH=false
if [ -n "$UPDATE_KEY_SRC" ] && [ -r "$UPDATE_KEY_SRC" ]; then
    case "$REPO_URL" in
        "$_SSH_REPO"|"$_HTTPS_REPO")
            _USE_SSH=true
            log_info "Read-only deploy key found — cloning the private repo over SSH."
            ;;
    esac
fi

if [ -d "$INSTALL_DIR/.git" ]; then
    log_info "Updating existing checkout at $INSTALL_DIR..."
    if [ "$_USE_SSH" = true ]; then
        # Garante o transporte SSH no checkout existente antes de qualquer fetch.
        # (core.sshCommand definitivo é gravado no passo 6b, apontando para a
        # cópia persistida em $HAOS_HOME/keys — aqui só o fetch por env.)
        git -C "$INSTALL_DIR" remote set-url origin "$_SSH_REPO" 2>/dev/null || true
        _register_github_host_key
        _git_fetch() { GIT_SSH_COMMAND="ssh -i ${UPDATE_KEY_SRC} -o IdentitiesOnly=yes" git "$@"; }
    else
        # Sem deploy key: usa a URL escolhida (https anônimo se o repo for
        # público) e remove core.sshCommand legado de um provisionamento por chave.
        git -C "$INSTALL_DIR" remote set-url origin "$REPO_URL" 2>/dev/null || true
        git -C "$INSTALL_DIR" config --unset core.sshCommand 2>/dev/null || true
        _git_fetch() { git "$@"; }
    fi
    _git_fetch -C "$INSTALL_DIR" fetch origin "$BRANCH" || true
    git -C "$INSTALL_DIR" checkout "$BRANCH" || true
    _git_fetch -C "$INSTALL_DIR" pull origin "$BRANCH" || true
    # Mesmo invariante do ramo de clone abaixo: passos posteriores (o import de
    # `scripts.serve_controlplane` no fim, por exemplo) contam com o cwd na raiz
    # da instalação.
    cd "$INSTALL_DIR"
else
    log_info "Cloning $([ "$_USE_SSH" = true ] && echo "$_SSH_REPO" || echo "$REPO_URL") ($BRANCH) into $INSTALL_DIR..."
    if [ "$_USE_SSH" = true ]; then
        _register_github_host_key
        GIT_SSH_COMMAND="ssh -i ${UPDATE_KEY_SRC} -o IdentitiesOnly=yes" \
            git clone --branch "$BRANCH" --single-branch "$_SSH_REPO" "$INSTALL_DIR"
    else
        git clone --branch "$BRANCH" --single-branch "$REPO_URL" "$INSTALL_DIR"
    fi
    cd "$INSTALL_DIR"
fi

# 4. Install UV for ultra-fast Python package management
log_step "Ensuring uv package manager..."
if ! command -v uv >/dev/null 2>&1; then
    log_info "Installing uv..."
    curl -LsSf https://astral.sh/uv/install.sh | sh
    export PATH="$HOME/.cargo/bin:$HOME/.local/bin:$PATH"
fi

# 5. Create Isolated Virtual Environment
# Instala o HAOS na venv com os extras pedidos, caindo para o core se o extra não
# resolver. Mesmo desenho de tiers do scripts/install.sh (extra -> core): uma
# instalação que perde um transitivo do PyPI não pode ficar sem CLI nenhum.
install_haos_into_venv() {
    # Caminho ABSOLUTO de propósito: `.` é resolvido contra o cwd, e no caminho
    # "checkout já existe" (mais acima) não há `cd "$INSTALL_DIR"` — instalar
    # `.` ali apontava para o diretório de onde o usuário chamou o instalador
    # ("does not appear to be a Python project"). Um `haos install` sobre um
    # checkout existente abortava por isso.
    local spec="$INSTALL_DIR"
    if [ "$HAOS_EXTRAS" != "none" ]; then
        spec="$INSTALL_DIR[${HAOS_EXTRAS}]"
    fi
    if command -v uv >/dev/null 2>&1; then
        VIRTUAL_ENV="$VENV_DIR" uv pip install -e "$spec" && return 0
    else
        "$PYTHON" -m pip install -e "$spec" && return 0
    fi
    if [ "$HAOS_EXTRAS" != "none" ]; then
        log_warn "Extras '$HAOS_EXTRAS' não resolveram — instalando apenas o core."
        if command -v uv >/dev/null 2>&1; then
            VIRTUAL_ENV="$VENV_DIR" uv pip install -e "$INSTALL_DIR"
        else
            "$PYTHON" -m pip install -e "$INSTALL_DIR"
        fi
    fi
}

log_step "Configuring isolated virtual environment..."
VENV_DIR="$INSTALL_DIR/venv"
if command -v uv >/dev/null 2>&1; then
    uv venv "$VENV_DIR" --python 3.11 2>/dev/null || uv venv "$VENV_DIR"
    PYTHON="$VENV_DIR/bin/python"
    if [ "$HAOS_EXTRAS" = "none" ]; then
        log_info "Installing HAOS core (sem extras) via uv..."
    else
        log_info "Installing HAOS + extras [$HAOS_EXTRAS] via uv..."
    fi
    install_haos_into_venv
    # Optional: Scrapling (backend do haos-fetch). Desativável com SKIP_FETCH_EXTRA=1.
    # Pré-instalar aqui é conveniência do appliance (funciona offline); se falhar, o
    # haos-fetch resolve no primeiro uso via tools/lazy_deps (fetch.scrapling).
    if [ "${SKIP_FETCH_EXTRA:-0}" != "1" ]; then
        VIRTUAL_ENV="$VENV_DIR" uv pip install --quiet "scrapling[fetchers]==0.4.15" || log_warn "scrapling (backend do haos-fetch) não pré-instalado — o haos-fetch resolve no primeiro uso via lazy_deps, ou instale com: uv pip install 'scrapling[fetchers]==0.4.15'"
    fi
else
    log_warn "uv not found, falling back to python3 -m venv..."
    python3 -m venv "$VENV_DIR"
    PYTHON="$VENV_DIR/bin/python"
    "$PYTHON" -m pip install --upgrade pip
    install_haos_into_venv
    if [ "${SKIP_FETCH_EXTRA:-0}" != "1" ]; then
        "$PYTHON" -m pip install --quiet "scrapling[fetchers]==0.4.15" || log_warn "scrapling (backend do haos-fetch) não pré-instalado — o haos-fetch resolve no primeiro uso via lazy_deps, ou instale com: uv pip install 'scrapling[fetchers]==0.4.15'"
    fi
fi

# 5b. Ouroboros Autonomous Evolution Engine (Optional, Recommended)
if [ "$INSTALL_OUROBOROS" = true ]; then
    log_step "Installing Ouroboros Autonomous Evolution Engine..."
    if [ "$(id -u)" -eq 0 ]; then
        OUROBOROS_VENV_DIR="/opt/ouroboros-venv"
    else
        OUROBOROS_VENV_DIR="$INSTALL_DIR/ouroboros-venv"
    fi

    mkdir -p "$(dirname "$OUROBOROS_VENV_DIR")"
    if command -v uv >/dev/null 2>&1; then
        log_info "Creating dedicated Ouroboros venv ($OUROBOROS_VENV_DIR)..."
        uv venv "$OUROBOROS_VENV_DIR" --python 3.12 2>/dev/null || uv venv "$OUROBOROS_VENV_DIR"
        VIRTUAL_ENV="$OUROBOROS_VENV_DIR" uv pip install "ouroboros-ai>=0.54.0" || log_warn "Falha ao instalar ouroboros-ai via uv pip"
    else
        log_info "Creating dedicated Ouroboros venv ($OUROBOROS_VENV_DIR)..."
        python3 -m venv "$OUROBOROS_VENV_DIR" 2>/dev/null || python3.12 -m venv "$OUROBOROS_VENV_DIR"
        "$OUROBOROS_VENV_DIR/bin/pip" install --upgrade --quiet pip 2>/dev/null || true
        "$OUROBOROS_VENV_DIR/bin/pip" install "ouroboros-ai>=0.54.0" || log_warn "Falha ao instalar ouroboros-ai via pip"
    fi

    if [ -x "$OUROBOROS_VENV_DIR/bin/ouroboros" ]; then
        mkdir -p "$BIN_DIR"
        ln -sf "$OUROBOROS_VENV_DIR/bin/ouroboros" "$BIN_DIR/ouroboros"
        log_ok "Ouroboros installed at $BIN_DIR/ouroboros"
    else
        log_warn "Executável do Ouroboros não encontrado em $OUROBOROS_VENV_DIR/bin/ouroboros"
    fi
fi

# 6. Install Global CLI Wrappers
log_step "Installing global CLI wrappers..."
mkdir -p "$BIN_DIR"
mkdir -p "$HAOS_HOME"

# Inherit .hermes config and credentials if available.
# So CONFIGURACAO (.env/config.yaml), nunca ESTADO: ~/.hermes pode ser store de outro
# produto (ver criterio de migracao em docs/haos/DEV_WORKFLOW_VM.md) e adotar o root
# errado e falha silenciosa de integridade. Estado se migra a mao, com o gateway parado.
if [ -d "$HOME/.hermes" ]; then
    if [ ! -e "$HAOS_HOME/.env" ] && [ -f "$HOME/.hermes/.env" ]; then
        ln -sf "$HOME/.hermes/.env" "$HAOS_HOME/.env"
        log_info "Inherited .env from $HOME/.hermes/.env"
    fi
    if [ ! -e "$HAOS_HOME/config.yaml" ] && [ -f "$HOME/.hermes/config.yaml" ]; then
        ln -sf "$HOME/.hermes/config.yaml" "$HAOS_HOME/config.yaml"
        log_info "Inherited config.yaml from $HOME/.hermes/config.yaml"
    fi
fi

# Compat de caminho: ferramenta ou processo legado que ainda escreva em ~/.hermes passa
# a acertar o store canonico. Só quando o home canonico esta em uso e ~/.hermes NAO
# existe — nunca sobrescreve um store legado real nem aponta para um HAOS_HOME temporario.
if [ "$HAOS_HOME" = "$HOME/.haos" ] && [ ! -e "$HOME/.hermes" ]; then
    ln -s "$HAOS_HOME" "$HOME/.hermes" 2>/dev/null \
        && log_info "Compat: $HOME/.hermes -> $HAOS_HOME" \
        || log_warn "Nao foi possivel criar o atalho $HOME/.hermes -> $HAOS_HOME"
fi

if command -v cargo >/dev/null 2>&1 && [ -d "$INSTALL_DIR/packages/haos-edge" ]; then
    log_info "Building HAOS Rust Edge layer..."
    (cd "$INSTALL_DIR/packages/haos-edge" && cargo build --release)
    if [ -f "$INSTALL_DIR/packages/haos-edge/target/release/haos-edge" ]; then
        cp "$INSTALL_DIR/packages/haos-edge/target/release/haos-edge" "$BIN_DIR/haos-edge"
        chmod +x "$BIN_DIR/haos-edge"
        log_info "HAOS Rust Edge installed to $BIN_DIR/haos-edge"
    fi
fi

cat << EOF > "$BIN_DIR/haos"
#!/usr/bin/env bash
# Determine user home
USER_HOME="\${HOME:-/root}"
export HAOS_HOME="\${HAOS_HOME:-\$USER_HOME/.haos}"
mkdir -p "\$HAOS_HOME"

# Inherit .env and config.yaml if missing (isolated copy, no symlink) so every user
# (root, adriano, ...) gets working providers from ~/.hermes or /root/.haos fallback.
if [ ! -f "\$HAOS_HOME/.env" ]; then
    if [ -f "\$USER_HOME/.hermes/.env" ]; then cp -p "\$USER_HOME/.hermes/.env" "\$HAOS_HOME/.env"
    elif [ -f "/root/.haos/.env" ]; then cp -p "/root/.haos/.env" "\$HAOS_HOME/.env"
    elif [ -f "/root/.hermes/.env" ]; then cp -p "/root/.hermes/.env" "\$HAOS_HOME/.env"; fi
fi
if [ ! -f "\$HAOS_HOME/config.yaml" ]; then
    if [ -f "\$USER_HOME/.hermes/config.yaml" ]; then cp -p "\$USER_HOME/.hermes/config.yaml" "\$HAOS_HOME/config.yaml"
    elif [ -f "/root/.haos/config.yaml" ]; then cp -p "/root/.haos/config.yaml" "\$HAOS_HOME/config.yaml"
    elif [ -f "/root/.hermes/config.yaml" ]; then cp -p "/root/.hermes/config.yaml" "\$HAOS_HOME/config.yaml"; fi
fi

# Compat de caminho por usuario: ferramenta/processo legado que escreva em ~/.hermes
# acerta o store canonico. Só com o home canonico em uso e ~/.hermes inexistente.
if [ "\$HAOS_HOME" = "\$USER_HOME/.haos" ] && [ ! -e "\$USER_HOME/.hermes" ]; then
    ln -s "\$HAOS_HOME" "\$USER_HOME/.hermes" 2>/dev/null || true
fi

export HERMES_HOME="\${HAOS_HOME}"
export HAOS_DATA_DIR="\${HAOS_DATA_DIR:-\$HAOS_HOME}"
export HAOS_RUNTIME="1"
unset PYTHONPATH
unset PYTHONHOME

# Ultra-fast Rust Edge routing: if haos-edge is available and matches fast commands
# (doctor is intentionally routed to full Python diagnostics, not the thin edge probe)
# (\`doc search\` is intentionally Python-only: haos-edge does plain BM25 while the
#  Python store does RRF fusion of FTS5 BM25 + token/breadcrumb overlap)
EDGE_SUBCMDS="status team"
FIRST_ARG="\${1:-}"

if [ -x "$BIN_DIR/haos-edge" ]; then
    if [[ -n "\$FIRST_ARG" && " \$EDGE_SUBCMDS " =~ " \$FIRST_ARG " ]]; then
        exec "$BIN_DIR/haos-edge" "\$@"
    fi
fi

HAOS_SUBCMDS="status team doc rag graph skills eval doctor evolution scheduler intervene"
FIRST_ARG="\${1:-}"

if [[ -n "\$FIRST_ARG" && " \$HAOS_SUBCMDS " =~ " \$FIRST_ARG " ]]; then
    if [ -x "$VENV_DIR/bin/haos" ]; then
        exec "$VENV_DIR/bin/haos" haos "\$@"
    else
        export PYTHONPATH="$INSTALL_DIR:\${PYTHONPATH:-}"
        exec "$PYTHON" -m hermes_cli.main haos "\$@"
    fi
else
    if [ -x "$VENV_DIR/bin/haos" ]; then
        exec "$VENV_DIR/bin/haos" "\$@"
    else
        export PYTHONPATH="$INSTALL_DIR:\${PYTHONPATH:-}"
        exec "$PYTHON" -m hermes_cli.main "\$@"
    fi
fi
EOF
chmod +x "$BIN_DIR/haos"

cat << EOF > "$BIN_DIR/haos-agent"
#!/usr/bin/env bash
export HAOS_HOME="\${HAOS_HOME:-$HAOS_HOME}"
HERMES_DIR="\${HERMES_HOME:-\$HOME/.hermes}"
if [ -d "\$HERMES_DIR" ]; then
    mkdir -p "\$HAOS_HOME"
    [ ! -e "\$HAOS_HOME/.env" ] && [ -f "\$HERMES_DIR/.env" ] && ln -sf "\$HERMES_DIR/.env" "\$HAOS_HOME/.env"
    [ ! -e "\$HAOS_HOME/config.yaml" ] && [ -f "\$HERMES_DIR/config.yaml" ] && ln -sf "\$HERMES_DIR/config.yaml" "\$HAOS_HOME/config.yaml"
fi
export HERMES_HOME="\${HAOS_HOME}"
export HAOS_DATA_DIR="\${HAOS_DATA_DIR:-\$HAOS_HOME}"
unset PYTHONPATH
unset PYTHONHOME
export PYTHONPATH="$INSTALL_DIR:\${PYTHONPATH:-}"
cd "$INSTALL_DIR" 2>/dev/null || true
exec "$PYTHON" "$INSTALL_DIR/run_agent.py" "\$@"
EOF
chmod +x "$BIN_DIR/haos-agent"

cat << EOF > "$BIN_DIR/haos-controlplane"
#!/usr/bin/env bash
export HAOS_HOME="\${HAOS_HOME:-$HAOS_HOME}"
export HERMES_HOME="\${HAOS_HOME}"
export HAOS_DATA_DIR="\${HAOS_DATA_DIR:-\$HAOS_HOME}"
unset PYTHONPATH
unset PYTHONHOME
export PYTHONPATH="$INSTALL_DIR:\${PYTHONPATH:-}"
PID_FILE="\$HAOS_HOME/controlplane.pid"
LOG_FILE="\$HAOS_HOME/controlplane.log"

ACTION="\${1:-start}"

case "\$ACTION" in
    stop)
        if [ -f "\$PID_FILE" ]; then
            PID=\$(cat "\$PID_FILE" 2>/dev/null || true)
            if [ -n "\$PID" ] && kill -0 "\$PID" 2>/dev/null; then
                kill "\$PID" 2>/dev/null || true
                rm -f "\$PID_FILE"
                echo "✓ HAOS Control Plane stopped (PID \$PID)."
                exit 0
            fi
        fi
        fuser -k 8788/tcp 2>/dev/null || true
        rm -f "\$PID_FILE"
        echo "✓ HAOS Control Plane stopped."
        ;;
    status)
        if curl -s -o /dev/null -w "%{http_code}" "http://127.0.0.1:8788/health" 2>/dev/null | grep -q "200"; then
            LAN_IP=\$("$PYTHON" -c "from scripts.serve_controlplane import get_private_lan_ips; ips = get_private_lan_ips(); print(ips[0] if ips else '127.0.0.1')" 2>/dev/null || echo "127.0.0.1")
            echo "✓ HAOS Control Plane is RUNNING on port 8788."
            echo "  👉 LAN Access: http://\$LAN_IP:8788/chat"
            echo "  👉 Localhost:  http://127.0.0.1:8788/chat"
        else
            echo "✗ HAOS Control Plane is NOT running."
        fi
        ;;
    logs)
        tail -f "\$LOG_FILE"
        ;;
    restart)
        "\$0" stop
        sleep 1
        "\$0" start
        ;;
    start|*)
        if curl -s -o /dev/null -w "%{http_code}" "http://127.0.0.1:8788/health" 2>/dev/null | grep -q "200"; then
            LAN_IP=\$("$PYTHON" -c "from scripts.serve_controlplane import get_private_lan_ips; ips = get_private_lan_ips(); print(ips[0] if ips else '127.0.0.1')" 2>/dev/null || echo "127.0.0.1")
            echo "✓ HAOS Control Plane is already running on port 8788."
            echo "  👉 LAN Access: http://\$LAN_IP:8788/chat"
            echo "  👉 Localhost:  http://127.0.0.1:8788/chat"
            exit 0
        fi
        mkdir -p "\$HAOS_HOME"
        nohup "$PYTHON" "$INSTALL_DIR/scripts/serve_controlplane.py" > "\$LOG_FILE" 2>&1 &
        CP_PID=\$!
        echo "\$CP_PID" > "\$PID_FILE"
        sleep 1
        LAN_IP=\$("$PYTHON" -c "from scripts.serve_controlplane import get_private_lan_ips; ips = get_private_lan_ips(); print(ips[0] if ips else '127.0.0.1')" 2>/dev/null || echo "127.0.0.1")
        echo "🚀 HAOS Control Plane started in background (PID \$CP_PID)."
        echo "  👉 LAN Access: http://\$LAN_IP:8788/chat"
        echo "  👉 Localhost:  http://127.0.0.1:8788/chat"
        echo "  Logs: \$LOG_FILE"
        ;;
esac
EOF
chmod +x "$BIN_DIR/haos-controlplane"

cat << EOF > "$BIN_DIR/haos-motd"
#!/usr/bin/env bash
export HAOS_HOME="\${HAOS_HOME:-$HAOS_HOME}"
export HERMES_HOME="\${HAOS_HOME}"
exec "$PYTHON" "$INSTALL_DIR/scripts/haos_motd.py" "\$@"
EOF
chmod +x "$BIN_DIR/haos-motd"

# 6a. Cloudflare-bypass fetch wrapper (Scrapling) — haos-fetch <url> [-o out.md]
cat << EOF > "$BIN_DIR/haos-fetch"
#!/usr/bin/env bash
# HAOS Cloudflare/anti-bot bypass fetch via Scrapling (scripts/haos_fetch_cf.py).
# Uso: haos-fetch <url> [-o out.md|out.html|out.txt] [--strategy http|stealth|auto] [--text]
export HAOS_HOME="\${HAOS_HOME:-\$HOME/.haos}"
export HERMES_HOME="\${HAOS_HOME}"
unset PYTHONPATH PYTHONHOME
exec "$VENV_DIR/bin/python" "$INSTALL_DIR/scripts/haos_fetch_cf.py" "\$@"
EOF
chmod +x "$BIN_DIR/haos-fetch"

log_ok "Installed wrappers: $BIN_DIR/haos, $BIN_DIR/haos-agent, $BIN_DIR/haos-controlplane, $BIN_DIR/haos-motd, $BIN_DIR/haos-fetch"

# 6b. Provision read-only update key (fetch from a PRIVATE HAOS repo via `haos update`)
# -------------------------------------------------------------------------------
# The deploy key was already resolved before the clone (step 3) — this step only
# persists it into the node store and pins the remote for the configured transport:
#   * key present  -> fetch via the read-only SSH deploy key; push stays HTTPS admin;
#   * repo público -> o clone já foi https e o origin permanece https (fetch anônimo,
#                     sem credencial alguma — a chave é desnecessária nesse caso).
if [ -n "$UPDATE_KEY_SRC" ] && [ -r "$UPDATE_KEY_SRC" ]; then
    log_step "Provisioning read-only update key for private HAOS repo..."
    mkdir -p "$HAOS_HOME/keys"
    cp "$UPDATE_KEY_SRC" "$HAOS_HOME/keys/update_ed25519"
    chmod 700 "$HAOS_HOME/keys"
    chmod 600 "$HAOS_HOME/keys/update_ed25519"
    log_ok "Installed read-only update key -> $HAOS_HOME/keys/update_ed25519"

    cd "$INSTALL_DIR"
    # Fetch (downloads) via the read-only SSH deploy key; push stays HTTPS (maintainer admin creds).
    git remote set-url origin "git@github.com:adrianolimagarcia/HAOS.git"
    git remote set-url --push origin "https://github.com/adrianolimagarcia/HAOS.git"
    git config core.sshCommand "ssh -i $HAOS_HOME/keys/update_ed25519 -o IdentitiesOnly=yes"
    log_ok "origin configured: fetch = read-only SSH key; push = HTTPS admin"

    # Trust github.com host key (SSH refuses non-interactive first contact otherwise).
    _register_github_host_key
    log_ok "Registered github.com host key"

    # Verify the read-only fetch actually works.
    if git fetch origin "$BRANCH" >/dev/null 2>&1; then
        log_ok "Verified: 'git fetch origin $BRANCH' via read-only deploy key OK"
    else
        log_warn "git fetch via deploy key failed. Register the public key in GitHub → Settings → Deploy keys (read-only)."
    fi
else
    log_info "No deploy key found — if the repository is PUBLIC, 'haos update' fetches over anonymous HTTPS (no credential needed)."
fi

# 7. Initialize HAOS_HOME & Default Configs
log_step "Initializing configuration in $HAOS_HOME..."
mkdir -p "$HAOS_HOME"

if [ -n "$API_KEY" ]; then
    echo "A6_API_KEY=$API_KEY" > "$HAOS_HOME/.env"
    chmod 600 "$HAOS_HOME/.env"
    log_ok "Saved A6_API_KEY to $HAOS_HOME/.env"
elif [ ! -f "$HAOS_HOME/.env" ]; then
    touch "$HAOS_HOME/.env"
    chmod 600 "$HAOS_HOME/.env"
fi

if [ ! -f "$HAOS_HOME/config.yaml" ]; then
    cat << EOF > "$HAOS_HOME/config.yaml"
# HAOS Agentic Configuration
_config_version: 41
context_file_max_chars: 64000

skills:
  default_skills:
    - haos-control-plane
    - haos-lane-execution

model:
  default: deepseek-v4-flash
  provider: a6api

providers:
  a6api:
    base_url: "https://api.a6api.com/v1"
    key_env: "A6_API_KEY"
    api_mode: chat_completions
    models:
      - deepseek-v4-flash
      - gpt-5.6-luna

terminal:
  cwd: $INSTALL_DIR
  timeout: 180

display:
  skin: default
  show_reasoning: true
  reasoning_full: true
  background_process_notifications: verbose

security:
  redact_secrets: true

delegation:
  max_spawn_depth: 3
  role_models:
    mayor: deepseek-v4-flash
    witness: deepseek-v4
    polecat: deepseek-v4-flash
EOF
    if [ "$INSTALL_OUROBOROS" = true ]; then
        cat << 'EOF' >> "$HAOS_HOME/config.yaml"

mcp_servers:
  ouroboros:
    command: ouroboros
    args:
      - mcp
      - serve
      - --runtime
      - opencode
EOF
    fi
    log_ok "Created initial $HAOS_HOME/config.yaml"
elif [ "$INSTALL_OUROBOROS" = true ]; then
    if ! grep -q "ouroboros:" "$HAOS_HOME/config.yaml" 2>/dev/null; then
        cat << 'EOF' >> "$HAOS_HOME/config.yaml"

mcp_servers:
  ouroboros:
    command: ouroboros
    args:
      - mcp
      - serve
      - --runtime
      - opencode
EOF
        log_ok "Registered Ouroboros MCP server in existing $HAOS_HOME/config.yaml"
    fi
fi

# 7b. Estrutura canônica de memória (espelha o haos-storage-init da ISO)
# Instalação fora da ISO precisa nascer com os MESMOS paths de banco/vault do
# appliance: dirs canônicos, vault semeado, rotina noturna agendada. Sem isso o
# nó sobe com stores invisíveis (split-brain ~/.hermes) ou sem memória nenhuma.
log_step "Provisioning canonical memory structure..."
mkdir -p "$HAOS_HOME"/obsidian_vault/adrs "$HAOS_HOME"/okf \
         "$HAOS_HOME"/memory "$HAOS_HOME"/graphrag \
         "$HAOS_HOME"/scripts "$HAOS_HOME"/cron

# Seed do vault + GraphRAG CSV (mesma fonte da ISO: /etc/skel no chroot).
SEED_DIR="$INSTALL_DIR/distro/haos-linux/config/includes.chroot/etc/skel/.haos"
if [ -d "$SEED_DIR" ]; then
    cp -rn "$SEED_DIR/." "$HAOS_HOME/" 2>/dev/null || true
    log_ok "Vault semeado (index.md + ADR-001 + seed GraphRAG CSV)"
fi

# Rotina noturna: script mora em <home>/scripts (o cron rejeita caminho
# absoluto); o ponteiro haos_agent_dir deixa o nightly achar a venv e o
# populate do tree do agente sem depender de caminho fixo de máquina.
if [ -f "$INSTALL_DIR/scripts/haos_nightly_maintenance.sh" ]; then
    install -m 755 "$INSTALL_DIR/scripts/haos_nightly_maintenance.sh" \
        "$HAOS_HOME/scripts/haos_nightly_maintenance.sh"
    log_ok "Rotina noturna instalada em $HAOS_HOME/scripts/"
fi
if [ -f "$INSTALL_DIR/scripts/haos_memory_populate.py" ]; then
    install -m 644 "$INSTALL_DIR/scripts/haos_memory_populate.py" \
        "$HAOS_HOME/scripts/haos_memory_populate.py"
fi
printf '%s\n' "$INSTALL_DIR" > "$HAOS_HOME/scripts/haos_agent_dir"

MAINT_NAME="SYSTEM - cron: manutencao noturna"
if [ -f "$HAOS_HOME/scripts/haos_nightly_maintenance.sh" ] \
   && ! grep -q "haos_nightly_maintenance.sh" "$HAOS_HOME/cron/jobs.json" 2>/dev/null; then
    if HAOS_HOME="$HAOS_HOME" "$PYTHON" -m hermes_cli.main cron create "0 3 * * *" \
        --name "$MAINT_NAME" --no-agent --script haos_nightly_maintenance.sh >/dev/null 2>&1; then
        log_ok "Rotina noturna agendada: '$MAINT_NAME' (0 3 * * *)"
    else
        log_warn "Não foi possível agendar a rotina noturna (rode 'haos cron create 0 3 * * * --name ...' depois)."
    fi
fi

# População inicial (best-effort): cria ragflow.db + graphrag.db já no install,
# para o doc search e o graphrag_query funcionarem desde o primeiro boot.
if [ -f "$INSTALL_DIR/scripts/haos_memory_populate.py" ]; then
    if HAOS_HOME="$HAOS_HOME" PYTHONPATH="$INSTALL_DIR" "$PYTHON" \
        "$INSTALL_DIR/scripts/haos_memory_populate.py" --home "$HAOS_HOME" >/dev/null 2>&1; then
        log_ok "Memória canônica populada (DeepDoc + GraphRAG + dream)"
    else
        log_warn "População inicial da memória falhou (a rotina noturna tenta de novo)."
    fi
fi

# 8. Verification
log_step "Verifying installation..."
INSTALLED_VER=$("$BIN_DIR/haos" --version 2>/dev/null || true)
if [ -n "$INSTALLED_VER" ]; then
    log_ok "HAOS successfully installed!"
    echo "  $INSTALLED_VER"
else
    log_warn "Installation completed, but '$BIN_DIR/haos --version' returned empty. Check PATH."
fi

# 9. Provision systemd services (sobrevivem a reboot) com nohup como fallback
log_step "Provisioning HAOS services..."
# O CLI do gateway usa `--system` ou nada (usuário é o padrão dele); o
# scripts/haos_services.py pede o escopo explícito nos dois casos, para uma
# instalação de usuário nunca tentar escrever em /etc/systemd/system.
SCOPE_FLAG=""
CONTROLPLANE_SCOPE_FLAG="--user"
HAS_SYSTEMD=0
if command -v systemctl >/dev/null 2>&1; then
    # `is-system-running` sai != 0 em "degraded", então o estado é lido, não testado
    # pelo exit code (mesma checagem do scripts/haos_services.py).
    SYSTEMD_STATE="$(systemctl is-system-running 2>/dev/null || true)"
    case "$SYSTEMD_STATE" in
        running|degraded|starting|maintenance)
            HAS_SYSTEMD=1
            if [ "$(id -u)" -eq 0 ]; then
                SCOPE_FLAG="--system"
                CONTROLPLANE_SCOPE_FLAG="--system"
            fi
            ;;
    esac
fi

if [ "$HAOS_SERVICES" = "none" ]; then
    log_info "Serviços desativados (--services none): rode 'haos-controlplane start' quando quiser."
elif [ "$HAS_SYSTEMD" = "1" ]; then
    if [[ ",$HAOS_SERVICES," == *",controlplane,"* ]]; then
        if "$PYTHON" "$INSTALL_DIR/scripts/haos_services.py" install \
            --install-dir "$INSTALL_DIR" --haos-home "$HAOS_HOME" --python "$PYTHON" \
            "$CONTROLPLANE_SCOPE_FLAG"; then
            log_ok "Control plane sob systemd (volta sozinho no boot)."
        else
            log_warn "systemd recusou a unidade do controlplane — subindo com nohup."
            "$BIN_DIR/haos-controlplane" start || true
        fi
    fi
    if [[ ",$HAOS_SERVICES," == *",gateway,"* ]]; then
        # Caminho canônico do repo: hermes_cli.gateway.systemd_install, coberto por
        # tests/hermes_cli/test_gateway_service.py — a unidade não é reimplementada
        # aqui. Chamado com o python da venv INSTALADA de propósito: o gerador
        # deriva o venv do interpretador que o invoca, então chamá-lo de outro
        # checkout gravaria o caminho daquele checkout na unidade.
        # --no-start-now: numa máquina recém-instalada ainda não há credencial de
        # plataforma, e o gateway sairia 78 (config ausente) já no primeiro boot.
        # A expansão de $SCOPE_FLAG é sem aspas de propósito: vazia (escopo de
        # usuário) ela precisa virar nenhum argumento — array vazio sob `set -u`
        # quebra no bash 3.2 do macOS, que o projeto suporta.
        if "$PYTHON" -m hermes_cli.main gateway install ${SCOPE_FLAG} --no-start-now >/dev/null 2>&1; then
            if [ "$SCOPE_FLAG" = "--system" ]; then
                GW_UNIT="/etc/systemd/system/hermes-gateway.service"
            else
                GW_UNIT="$HOME/.config/systemd/user/hermes-gateway.service"
            fi
            if grep -q "$INSTALL_DIR/venv" "$GW_UNIT" 2>/dev/null; then
                log_ok "Unidade do gateway instalada apontando para $INSTALL_DIR/venv."
            else
                log_warn "Unidade do gateway não aponta para $INSTALL_DIR/venv — confira $GW_UNIT."
            fi
            log_info "Rode 'haos setup' e depois 'haos gateway start' para conectar as plataformas."
        else
            log_warn "Não foi possível instalar a unidade do gateway (rode 'haos gateway install' depois)."
        fi
    fi
else
    log_warn "systemd indisponível — subindo o controlplane com nohup (não sobrevive a reboot)."
    "$BIN_DIR/haos-controlplane" start || true
fi

LAN_IP=$("$PYTHON" -c "from scripts.serve_controlplane import get_private_lan_ips; ips = get_private_lan_ips(); print(ips[0] if ips else '127.0.0.1')" 2>/dev/null || echo "127.0.0.1")

echo ""
echo -e "${GREEN}============================================================================${NC}"
echo -e "${BOLD}${GREEN}🎉 HAOS (Hermes Agentic OS) is READY & Control Plane is ONLINE!${NC}"
echo -e "${GREEN}============================================================================${NC}"
echo ""
echo "Access Control Plane & Interactive Chat directly from your browser:"
if [ "$LAN_IP" != "127.0.0.1" ]; then
    echo -e "  👉 ${BOLD}${CYAN}LAN Access (Local Network): http://${LAN_IP}:8788/chat${NC}"
fi
echo -e "  👉 ${BOLD}${CYAN}Localhost (This Machine):  http://127.0.0.1:8788/chat${NC}"
echo ""
echo "Quick Commands:"
echo "  • CLI Interface:            haos chat"
echo "  • Ultrawork Autonomous:     haos chat -u \"sua missão\""
echo "  • Status & Diagnostics:     haos status"
echo "  • Control Plane Manager:    haos-controlplane [start|stop|restart|status|logs]"
echo "============================================================================"
