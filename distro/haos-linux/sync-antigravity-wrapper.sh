#!/usr/bin/env bash
# ==============================================================================
# Sincroniza o wrapper-antigravity vendorizado no perfil do ISO
# ==============================================================================
# O wrapper (github.com/adrianolimagarcia/wrapper-antigravity) é VENDORIZADO em
# config/includes.chroot/opt/haos/wrapper-antigravity/ — committed, revisável e
# reprodutível: duas builds do mesmo commit geram a mesma ISO. O preço disso é
# que ele não acompanha o upstream sozinho, e é o que este script resolve.
#
#   ./sync-antigravity-wrapper.sh            # só compara (default)
#   ./sync-antigravity-wrapper.sh --apply    # baixa, instala e registra o SHA
#   ./sync-antigravity-wrapper.sh --ref v1.4.0 --apply   # pina um tag/SHA
#
# A comparação é por HASH do conteúdo, não por data: se o arquivo do perfil é
# igual ao do upstream, não importa quando foi copiado.
#
# O build-iso.sh chama `--check` (não fatal) e imprime o resultado, então uma
# ISO construída com o wrapper atrasado avisa em vez de passar silencioso.
# ==============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROFILE_DIR="${SCRIPT_DIR}/config/includes.chroot/opt/haos/wrapper-antigravity"
UPSTREAM_REPO="${HAOS_ANTIGRAVITY_REPO:-https://github.com/adrianolimagarcia/wrapper-antigravity.git}"
REF="${HAOS_ANTIGRAVITY_REF:-master}"
# Só estes arquivos entram no perfil; o resto do upstream (rust-gateway-v040/,
# test/, public/) não é usado pelo serviço, que roda `node server.mjs`.
FILES=(server.mjs README.md)

APPLY=false
while [[ $# -gt 0 ]]; do
    case "$1" in
        --apply) APPLY=true; shift ;;
        --ref) REF="$2"; shift 2 ;;
        --repo) UPSTREAM_REPO="$2"; shift 2 ;;
        --help|-h)
            sed -n '2,20p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
            exit 0 ;;
        *) echo "argumento desconhecido: $1" >&2; exit 2 ;;
    esac
done

TMP=""
cleanup() { [ -n "${TMP}" ] && rm -rf "${TMP}"; }
trap cleanup EXIT

echo "==> wrapper-antigravity: comparando perfil com ${UPSTREAM_REPO} (${REF})"
TMP="$(mktemp -d)"
if ! git init -q "${TMP}" 2>/dev/null \
   || ! git -C "${TMP}" remote add origin "${UPSTREAM_REPO}" 2>/dev/null \
   || ! git -C "${TMP}" fetch -q --depth 1 origin "${REF}" 2>/dev/null; then
    echo "  ⚠ não foi possível consultar o upstream (sem rede ou sem credencial)."
    echo "    O perfil segue com o que está vendorizado — nenhuma mudança feita."
    exit 1
fi
git -C "${TMP}" checkout -q FETCH_HEAD
SHA="$(git -C "${TMP}" rev-parse FETCH_HEAD)"

CHANGED=()
for f in "${FILES[@]}"; do
    if [ ! -f "${TMP}/${f}" ]; then
        echo "  ⚠ upstream não tem ${f} — abortando sem tocar no perfil." >&2
        exit 1
    fi
    if [ ! -f "${PROFILE_DIR}/${f}" ] || ! cmp -s "${TMP}/${f}" "${PROFILE_DIR}/${f}"; then
        CHANGED+=("${f}")
    fi
done

# Proveniência: qual commit do upstream está vendorizado. Sem isto, a próxima
# pessoa só descobre olhando hash por hash.
write_upstream_note() {
    mkdir -p "${PROFILE_DIR}"
    cat > "${PROFILE_DIR}/UPSTREAM.md" <<EOF
# Proveniência deste wrapper

Vendorizado de \`${UPSTREAM_REPO}\`
(commit \`${SHA}\`, ref \`${REF}\`) por \`../sync-antigravity-wrapper.sh --apply\`
em $(date -u +%Y-%m-%dT%H:%M:%SZ).

Para atualizar: \`./sync-antigravity-wrapper.sh --apply\` no diretório
\`distro/haos-linux/\` e reconstruir a ISO. \`--check\` apenas compara.
EOF
    echo "  ✓ proveniência registrada em ${PROFILE_DIR}/UPSTREAM.md (${SHA:0:12})"
}

# Proveniência: qual commit do upstream está vendorizado. Sem isto, a próxima
# pessoa só descobre olhando hash por hash.
write_upstream_note() {
    mkdir -p "${PROFILE_DIR}"
    cat > "${PROFILE_DIR}/UPSTREAM.md" <<EOF
# Proveniência deste wrapper

Vendorizado de \`${UPSTREAM_REPO}\`
(commit \`${SHA}\`, ref \`${REF}\`) por \`../sync-antigravity-wrapper.sh --apply\`
em $(date -u +%Y-%m-%dT%H:%M:%SZ).

Para atualizar: \`./sync-antigravity-wrapper.sh --apply\` no diretório
\`distro/haos-linux/\` e reconstruir a ISO. \`--check\` apenas compara.
EOF
    echo "  ✓ proveniência registrada em ${PROFILE_DIR}/UPSTREAM.md (${SHA:0:12})"
}

if [ "${#CHANGED[@]}" -eq 0 ]; then
    echo "  ✓ perfil idêntico ao upstream ${SHA:0:12} (nada a instalar)"
    # --apply com conteúdo idêntico ainda registra a proveniência: sem isto o
    # arquivo vendorizado no commit não diz de onde veio.
    [ "${APPLY}" = true ] && write_upstream_note
    exit 0
fi

echo "  → difere do upstream ${SHA:0:12}: ${CHANGED[*]}"
if [ "${APPLY}" != true ]; then
    echo "    rode com --apply para instalar esta versão no perfil."
    exit 3
fi

# `node --check` pega arquivo truncado/colado pela metade antes de ele entrar na
# imagem — o modo de falha que uma cópia manual produz. Node não existe em toda
# máquina de build, então a checagem é opcional e avisa quando não roda.
if command -v node >/dev/null 2>&1; then
    if ! node --check "${TMP}/server.mjs" 2>/dev/null; then
        echo "  ✗ server.mjs do upstream não passa em 'node --check' — perfil intacto." >&2
        exit 1
    fi
    echo "  ✓ server.mjs válido para o node ($(node --version))"
else
    echo "  ⚠ node ausente nesta máquina: sem 'node --check' no arquivo baixado."
fi

mkdir -p "${PROFILE_DIR}"
for f in "${FILES[@]}"; do
    install -m 644 "${TMP}/${f}" "${PROFILE_DIR}/${f}"
    printf "  ✓ instalado %s (%s bytes, sha256 %s)\n" \
        "${f}" "$(stat -c %s "${PROFILE_DIR}/${f}")" \
        "$(sha256sum "${PROFILE_DIR}/${f}" | cut -c1-12)"
done

write_upstream_note
echo "==> reconstrua a ISO para esta versão entrar na imagem."
