#!/usr/bin/env bash
# ============================================================================
# HAOS — provisiona a deploy key READ-ONLY do repo (Modo B do `haos update`)
# ============================================================================
# O que resolve: o build da ISO (`distro/haos-linux/build-iso.sh`) EMBUTE uma chave
# privada read-only em /etc/haos/keys/update_ed25519, para que `haos update` funcione
# numa instalação nova sem login no GitHub. O README do distro documenta o Modo B
# inteiro e chama essa chave de `haos-update-ro`, mas nenhum passo dizia como CRIÁ-LA
# nem onde registrar a metade pública. Gerar à mão erra o caminho, a permissão, ou
# aponta a `.pub` por engano — e o build só AVISA nesses casos, então a ISO sai sem
# credencial e a falha aparece muito depois, no nó, quando `haos update` não autentica.
#
# Escopo: este é o lado da MÁQUINA DE BUILD (gerar + registrar). O lado do NÓ — copiar
# a chave para /etc/haos/keys e semear no store do usuário `haos` — é o passo 6b do
# `scripts/install_haos.sh` (`--update-key`) e continua sendo dele.
#
# Uso:
#   ./scripts/provision_update_key.sh                 # /root/.haos/keys/update_ed25519
#   ./scripts/provision_update_key.sh --key /tmp/k    # outro caminho (bom p/ testar)
#   ./scripts/provision_update_key.sh --force         # substitui chave existente
#
# NUNCA imprime material privado — só a metade pública, a fingerprint e o comando de
# build. A chave nunca é commitada: o destino do build é ignorado pelo git
# (`distro/haos-linux/.gitignore`), e o script confere isso antes de escrever nada.
# ============================================================================

set -euo pipefail

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[0;33m'
BLUE='\033[0;34m'; CYAN='\033[0;36m'; BOLD='\033[1m'; NC='\033[0m'

log_info() { echo -e "${CYAN}ℹ${NC} $1"; }
log_ok()   { echo -e "${GREEN}✓${NC} $1"; }
log_warn() { echo -e "${YELLOW}⚠${NC} $1"; }
log_err()  { echo -e "${RED}✗${NC} $1" >&2; }
log_step() { echo -e "\n${BOLD}${BLUE}==>${NC} ${BOLD}$1${NC}"; }

KEY_PATH="${HAOS_UPDATE_KEY:-/root/.haos/keys/update_ed25519}"
KEY_COMMENT="haos-update-ro"
FORCE=0
REPO_URL="github.com/adrianolimagarcia/HAOS"

usage() {
    cat <<'EOF'
Provisiona a deploy key read-only embutida na ISO do HAOS (Modo B).

Uso:
  provision_update_key.sh [--key CAMINHO] [--comment TEXTO] [--force]

Opções:
  --key CAMINHO    Onde gravar a chave privada (padrão: /root/.haos/keys/update_ed25519,
                   ou $HAOS_UPDATE_KEY). O .pub fica ao lado.
  --comment TXT    Comentário da chave (padrão: haos-update-ro) — é o rótulo que
                   aparece na lista de deploy keys do GitHub.
  --force          Substitui uma chave já existente. Sem isto o script RECUSA:
                   a chave antiga está embutida em toda ISO já construída, e trocá-la
                   quebra o `haos update` dos nós vindos dela até reconstruir a imagem.
  -h, --help       Esta ajuda.
EOF
}

while [ $# -gt 0 ]; do
    case "$1" in
        --key)     KEY_PATH="${2:?--key exige um caminho}"; shift 2 ;;
        --comment) KEY_COMMENT="${2:?--comment exige um texto}"; shift 2 ;;
        --force)   FORCE=1; shift ;;
        -h|--help) usage; exit 0 ;;
        *) log_err "opção desconhecida: $1"; usage; exit 2 ;;
    esac
done

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
KEY_DIR="$(dirname "${KEY_PATH}")"
# Destino que o build-iso.sh injeta no chroot da imagem (tem de estar ignorado pelo git).
BUILD_KEY_DEST="${REPO_ROOT}/distro/haos-linux/config/includes.chroot/etc/haos/keys/update_ed25519"

log_step "Pré-requisitos"
command -v ssh-keygen >/dev/null 2>&1 || { log_err "ssh-keygen não encontrado (instale openssh-client)."; exit 1; }
log_ok "ssh-keygen: $(command -v ssh-keygen)"

# O gitignore é a rede de segurança que impede a chave de entrar no histórico durante o
# build. Se ele sumir, o script para: um `git add -A` na janela do build commitariam a
# chave, e histórico sobrevive a revogação.
if command -v git >/dev/null 2>&1 && git -C "${REPO_ROOT}" rev-parse --git-dir >/dev/null 2>&1; then
    if git -C "${REPO_ROOT}" check-ignore -q "distro/haos-linux/config/includes.chroot/etc/haos/keys/"; then
        log_ok "destino do build está ignorado pelo git"
    else
        log_err "o destino do build NÃO está ignorado pelo git:"
        log_err "  ${BUILD_KEY_DEST}"
        log_err "Restaure a regra em distro/haos-linux/.gitignore antes de prosseguir."
        exit 1
    fi
else
    log_warn "não é um checkout git: não foi possível conferir o .gitignore"
fi

if [ -e "${KEY_PATH}" ] && [ "${FORCE}" -ne 1 ]; then
    log_err "já existe uma chave em ${KEY_PATH}"
    log_warn "Essa chave está embutida em toda ISO já construída. Substituí-la exige:"
    log_warn "  1. apagar a deploy key no GitHub (Settings → Deploy keys);"
    log_warn "  2. rodar de novo com --force;"
    log_warn "  3. RECONSTRUIR a imagem e reinstalar os nós que dependem do repo privado."
    log_info "Para só inspecionar a chave atual: ssh-keygen -lf ${KEY_PATH}.pub"
    exit 1
fi

if [ ! -d "${KEY_DIR}" ]; then
    mkdir -p "${KEY_DIR}" || { log_err "não consegui criar ${KEY_DIR} (rode como root?)"; exit 1; }
    CREATED_DIR=1
else
    CREATED_DIR=0
fi
if [ ! -w "${KEY_DIR}" ]; then
    log_err "${KEY_DIR} não é gravável por $(id -un) — use sudo ou --key em outro caminho."
    exit 1
fi
# Só aperta o diretório que ESTE script criou: `--key /tmp/k` tem /tmp como pai, e um
# chmod 700 em /tmp deixaria o sistema inutilizável para os outros usuários.
if [ "${CREATED_DIR}" -eq 1 ]; then
    chmod 700 "${KEY_DIR}" 2>/dev/null || true
fi

log_step "Gerando par de chaves ed25519"
# Sem passphrase: quem usa a chave é `haos update`, não-interativo, sem agente SSH.
# Ver a chave privada não a protege aqui: o que protege é a permissão 0600 e o fato de
# ela nunca ser commitada (e ser revogável no GitHub).
#
# Gera em `.new` e move no lugar: `ssh-keygen -f` se recusa a sobrescrever arquivo
# existente (é assim que `--force` não substituía nada), e o caminho canônico nunca
# fica sem arquivo entre a remoção do antigo e a criação do novo.
GEN_PATH="${KEY_PATH}.new"
rm -f "${GEN_PATH}" "${GEN_PATH}.pub"
ssh-keygen -t ed25519 -N "" -C "${KEY_COMMENT}" -f "${GEN_PATH}" >/dev/null
mv -f "${GEN_PATH}" "${KEY_PATH}"
mv -f "${GEN_PATH}.pub" "${KEY_PATH}.pub"
log_ok "chave criada: ${KEY_PATH}"

log_step "Endurecendo permissões"
chmod 600 "${KEY_PATH}"
chmod 644 "${KEY_PATH}.pub"
if [ "$(id -u)" -eq 0 ]; then
    chown root:root "${KEY_PATH}" "${KEY_PATH}.pub"
    log_ok "dono root:root, 0600 (privada) / 0644 (pública)"
else
    log_warn "não sou root: dono ficou $(id -un). O build da ISO lê o arquivo, mas o"
    log_warn "padrão documentado é root:root — rode com sudo se for a máquina de build."
fi

log_step "Verificando o par"
# Uma chave truncada ou um .pub desencontrado só apareceria como "ISO sem credencial"
# no fim do build; aqui falha antes. Compara só `tipo + base64`: o `ssh-keygen -y`
# imprime o comentário que está DENTRO da privada, e o .pub pode ter outro (ou nenhum) —
# comparar a linha inteira acusaria um par correto como divergente.
ssh-keygen -y -f "${KEY_PATH}" >/dev/null 2>&1 || {
    log_err "a chave privada não pôde ser lida por ssh-keygen (arquivo inválido?)"; exit 1; }
DERIVED_PUB="$(ssh-keygen -y -f "${KEY_PATH}" | awk '{print $1" "$2}')"
DECLARED_PUB="$(awk '{print $1" "$2}' "${KEY_PATH}.pub")"
if [ "${DERIVED_PUB}" != "${DECLARED_PUB}" ]; then
    log_err "a .pub não corresponde à chave privada — não use este par."
    exit 1
fi
log_ok "privada e .pub são o mesmo par"
log_info "fingerprint: $(ssh-keygen -lf "${KEY_PATH}.pub" | awk '{print $2}')"

log_step "Registre a metade PÚBLICA no GitHub (passo que falta no README do distro)"
echo
echo -e "${BOLD}1.${NC} Copie esta linha (é a chave PÚBLICA — pode ser compartilhada):"
echo
cat "${KEY_PATH}.pub"
echo
echo -e "${BOLD}2.${NC} Cole em: https://${REPO_URL}/settings/keys/new"
echo -e "   Título: ${BOLD}${KEY_COMMENT}${NC}   •   ${YELLOW}NÃO marque \"Allow write access\"${NC}"
echo -e "   (deploy key de escrita daria push ao repo a partir de qualquer nó instalado)"
echo
echo -e "${BOLD}3.${NC} Construa a ISO apontando para a chave privada:"
echo
echo -e "   ${CYAN}HAOS_UPDATE_KEY=${KEY_PATH} ${REPO_ROOT}/distro/haos-linux/build-iso.sh${NC}"
echo
log_info "Repo público? Então nada disso é necessário: o Modo A clona por HTTPS anônimo."
log_info "Revogação: apague a deploy key no GitHub e reconstrua a imagem."
