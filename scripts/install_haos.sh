#!/usr/bin/env bash
# ============================================================================
# HAOS (Hermes Agentic OS) — Universal Standalone Installer
# ============================================================================
# Installs HAOS from github.com/adrianolimagarcia/HAOS (branch haos-standalone).
# Designed to coexist safely with or without an existing upstream Hermes installation.
# Sets up isolated virtual environment, dependencies, CLI binaries, and default configs.
#
# Usage:
#   curl -fsSL https://raw.githubusercontent.com/adrianolimagarcia/HAOS/main/scripts/install_haos.sh | bash
#
# Or with options:
#   ./scripts/install_haos.sh --branch haos-standalone --haos-home ~/.haos \
#       --update-key /path/to/haos-update-key   # read-only deploy key for private-repo `haos update`
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
BRANCH="${HAOS_BRANCH:-haos-standalone}"
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

# Argument parsing
while [[ $# -gt 0 ]]; do
    case "$1" in
        --dir) INSTALL_DIR="$2"; shift 2 ;;
        --branch) BRANCH="$2"; shift 2 ;;
        --haos-home) HAOS_HOME="$2"; shift 2 ;;
        --api-key) API_KEY="$2"; shift 2 ;;
        --update-key) UPDATE_KEY_SRC="$2"; shift 2 ;;
        --skip-system-deps) SKIP_SYSTEM_DEPS=true; shift ;;
        --help|-h)
            echo "HAOS Standalone Installer"
            echo "Options:"
            echo "  --dir <path>          Target checkout directory (default: $DEFAULT_INSTALL_DIR)"
            echo "  --branch <name>       Git branch to clone (default: haos-standalone)"
            echo "  --haos-home <path>    Configuration & data home directory (default: ~/.haos)"
            echo "  --api-key <key>       A6API Key to register in .env"
            echo "  --update-key <path>   Read-only deploy key for private-repo 'haos update'"
            echo "  --skip-system-deps    Skip apt/pacman/dnf package installation"
            exit 0
            ;;
        *) log_warn "Unknown argument: $1"; shift ;;
    esac
done

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
log_step "Fetching HAOS repository from GitHub..."
mkdir -p "$(dirname "$INSTALL_DIR")"
if [ -d "$INSTALL_DIR/.git" ]; then
    log_info "Updating existing checkout at $INSTALL_DIR..."
    cd "$INSTALL_DIR"
    git fetch origin "$BRANCH" || true
    git checkout "$BRANCH" || true
    git pull origin "$BRANCH" || true
else
    log_info "Cloning $REPO_URL ($BRANCH) into $INSTALL_DIR..."
    git clone --branch "$BRANCH" --single-branch "$REPO_URL" "$INSTALL_DIR"
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
log_step "Configuring isolated virtual environment..."
VENV_DIR="$INSTALL_DIR/venv"
if command -v uv >/dev/null 2>&1; then
    uv venv "$VENV_DIR" --python 3.11 2>/dev/null || uv venv "$VENV_DIR"
    PYTHON="$VENV_DIR/bin/python"
    log_info "Installing HAOS package and dependencies via uv..."
    VIRTUAL_ENV="$VENV_DIR" uv pip install -e .
else
    log_warn "uv not found, falling back to python3 -m venv..."
    python3 -m venv "$VENV_DIR"
    PYTHON="$VENV_DIR/bin/python"
    "$PYTHON" -m pip install --upgrade pip
    "$PYTHON" -m pip install -e .
fi

# 6. Install Global CLI Wrappers
log_step "Installing global CLI wrappers..."
mkdir -p "$BIN_DIR"
mkdir -p "$HAOS_HOME"

# Inherit .hermes config and credentials if available
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

export HERMES_HOME="\${HAOS_HOME}"
export HAOS_DATA_DIR="\${HAOS_DATA_DIR:-\$HAOS_HOME}"
export HAOS_RUNTIME="1"
unset PYTHONPATH
unset PYTHONHOME

# Ultra-fast Rust Edge routing: if haos-edge is available and matches fast commands
# (doctor is intentionally routed to full Python diagnostics, not the thin edge probe)
EDGE_SUBCMDS="status team"
FIRST_ARG="\${1:-}"

if [ -x "$BIN_DIR/haos-edge" ]; then
    if [[ "\$FIRST_ARG" == "doc" && "\${2:-}" == "search" ]]; then
        exec "$BIN_DIR/haos-edge" doc search "\${@:3}"
    fi
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

log_ok "Installed wrappers: $BIN_DIR/haos, $BIN_DIR/haos-agent, $BIN_DIR/haos-controlplane, $BIN_DIR/haos-motd"

# 6b. Provision read-only update key (fetch from the PRIVATE HAOS repo via `haos update`)
# -------------------------------------------------------------------------------
# The private repo can only be fetched with a credential. We use a GitHub deploy key
# registered as READ-ONLY (Settings → Deploy keys, "Allow write access" OFF), stored
# out-of-band (never committed to the repo). One shared key for the fleet (route 1):
# revoke = delete the deploy key on GitHub + replace the key file on each machine.
log_step "Provisioning read-only update key for private HAOS repo..."
if [ -z "$UPDATE_KEY_SRC" ]; then
    for _cand in "/root/.haos/keys/update_ed25519" "/etc/haos/keys/update_ed25519" "$HOME/.haos/keys/update_ed25519"; do
        if [ -f "$_cand" ]; then
            UPDATE_KEY_SRC="$_cand"
            break
        fi
    done
fi

if [ -n "$UPDATE_KEY_SRC" ] && [ -f "$UPDATE_KEY_SRC" ]; then
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
    if [ "$(id -u)" -eq 0 ] && [ -w /etc/ssh/ ]; then
        _KNOWN_HOSTS="/etc/ssh/ssh_known_hosts"
    else
        _KNOWN_HOSTS="$HOME/.ssh/known_hosts"
        mkdir -p "$HOME/.ssh"
    fi
    [ -f "$_KNOWN_HOSTS" ] || touch "$_KNOWN_HOSTS"
    if ! ssh-keygen -F github.com -f "$_KNOWN_HOSTS" >/dev/null 2>&1; then
        ssh-keyscan -t ed25519 github.com >> "$_KNOWN_HOSTS" 2>/dev/null || \
            log_warn "Could not write host key to $_KNOWN_HOSTS"
    fi
    log_ok "Registered github.com host key"

    # Verify the read-only fetch actually works.
    if git fetch origin "$BRANCH" >/dev/null 2>&1; then
        log_ok "Verified: 'git fetch origin $BRANCH' via read-only deploy key OK"
    else
        log_warn "git fetch via deploy key failed. Register the public key in GitHub → Settings → Deploy keys (read-only)."
    fi
else
    log_warn "No read-only update key found (pass --update-key <path> or place one at /etc/haos/keys/update_ed25519)."
    log_warn "'haos update' will only work if the checkout already has credentials for the private repo."
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
    log_ok "Created initial $HAOS_HOME/config.yaml"
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

# 9. Auto-Start HAOS Control Plane on Private LAN IP
log_step "Auto-starting HAOS Control Plane on private LAN..."
"$BIN_DIR/haos-controlplane" start || true

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
