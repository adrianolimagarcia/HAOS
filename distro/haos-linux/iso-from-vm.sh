#!/usr/bin/env bash
# iso-from-vm.sh — gera a ISO HAOS a partir do rootfs da VM de aceitação.
#
# Ciclo de desenvolvimento principal (a partir da decisão "VM como alvo de
# aceitação"): valida-se e ajusta-se tudo DENTRO da VM (hostname, kernel,
# pacotes, config), e este script congela esse estado validado numa ISO nova —
# sem rebuild de imagem por hooks. O resultado é "a própria VM virou a ISO".
#
# Fluxo:
#   1. rsync do rootfs da VM (sudo NOPASSWD, um filesystem só) -> chroot/
#   2. scrub do estado privado/transiente (machine-id, chaves ssh, aceitação,
#      estado do .haos, caches) — mantendo hostname, kernel, venv cheio,
#      chave de update e o wrapper antigravity
#   3. lb binary (apenas o estágio binário; os hooks do chroot NÃO rodam —
#      o rootfs da VM já contém tudo)
#   4. validação rápida do squashfs e cópia do ISO final
#
# Uso:
#   ./iso-from-vm.sh [IP_DA_VM] [TAG_DO_ISO]
#   (defaults: IP de /tmp/haos-vm-ip.txt ou 192.168.122.130; tag "vm-<data>")

set -euo pipefail

VM_IP="${1:-$(cat /tmp/haos-vm-ip.txt 2>/dev/null || echo 192.168.122.130)}"
ISO_TAG="${2:-vm-$(date +%Y%m%d-%H%M)}"
VM_USER="${VM_USER:-haos}"
SSH_KEY="${SSH_KEY:-/root/haos-vm/id_ed25519}"

DISTRO_DIR="$(cd "$(dirname "$0")" && pwd)"
CHROOT="${DISTRO_DIR}/chroot"
ISO="${DISTRO_DIR}/haos-linux-1.0-${ISO_TAG}.iso"

echo "[1/5] rsync do rootfs da VM (${VM_IP}) -> chroot/"
if [ -n "${CHROOT_READY:-}" ] && [ -d "${CHROOT}" ]; then
  echo "  ✓ chroot existente reutilizado (CHROOT_READY=1)"
else
  rm -rf "${CHROOT}"
  mkdir -p "${CHROOT}"
  rsync -aHAXx --numeric-ids --delete \
    -e "ssh -i ${SSH_KEY} -o StrictHostKeyChecking=no -o ConnectTimeout=8" \
    --rsync-path="sudo rsync" \
    --exclude='/proc/*' --exclude='/sys/*' --exclude='/dev/*' --exclude='/run/*' \
    --exclude='/tmp/*' --exclude='/var/tmp/*' \
    --exclude='/var/log/*' --exclude='/var/lib/apt/lists/*' --exclude='/var/cache/*' \
    --exclude='/var/lib/dhcp/*' --exclude='/var/lib/NetworkManager/*' \
    --include='/home/haos/.cache/ms-playwright/' \
    --exclude='/home/haos/.cache/*' --exclude='/root/.cache/*' \
    --exclude='**/.bash_history' --exclude='/opt/haos/.git/*' \
    "${VM_USER}@${VM_IP}:/" "${CHROOT}/"
  echo "  ✓ rootfs copiado ($(du -sh "${CHROOT}" | cut -f1))"
fi

echo "[2/5] scrub do estado privado/transiente"
# o live-build copia debs em cache para cá no estágio binário — o dir precisa existir
mkdir -p "${CHROOT}/var/cache/apt/archives"
# identidade de máquina — regenerada no primeiro boot
rm -f "${CHROOT}/etc/machine-id" "${CHROOT}/var/lib/dbus/machine-id" \
      "${CHROOT}/var/lib/systemd/random-seed"
# chaves ssh do host + chave de aceitação (a instalação gera as suas)
rm -f "${CHROOT}/etc/ssh/ssh_host_"* \
      "${CHROOT}/home/haos/.ssh/authorized_keys" \
      "${CHROOT}/home/haos/.ssh/known_hosts" "${CHROOT}/home/haos/.ssh/config" \
      "${CHROOT}/home/haos/.ssh/id_"*
# credenciais de escrita do GitHub (token admin usado só em DEV — nunca na ISO)
rm -f "${CHROOT}/home/haos/.git-credentials" "${CHROOT}/home/haos/.git-credential-cache" \
      "${CHROOT}/home/haos/.gitconfig"
rm -rf "${CHROOT}/home/haos/.config/gh" "${CHROOT}/home/haos/.cache/gh"
# resolv.conf é reescrito pelo instalador no destino
rm -f "${CHROOT}/etc/resolv.conf"
# estado do HAOS — reprovisionado pelo haos-setup no primeiro boot
rm -f "${CHROOT}/home/haos/.haos/state.db"* "${CHROOT}/home/haos/.haos/kanban.db"* \
      "${CHROOT}/home/haos/.haos/cron/executions.db"* \
      "${CHROOT}/home/haos/.haos/config.yaml" "${CHROOT}/home/haos/.haos/.env"
rm -rf "${CHROOT}/home/haos/.haos/sessions" "${CHROOT}/home/haos/.haos/logs" \
       "${CHROOT}/home/haos/.haos/memories"
# cache do wrapper antigravity (sem token de auth; recriado no boot)
rm -rf "${CHROOT}/var/lib/haos/antigravity/"*
# credenciais do WebUI do operador: a ISO NUNCA carrega a senha/sessões da VM
# de desenvolvimento (a senha é definida pelo operador no primeiro boot com
# `HAOS_DATA_DIR=/var/lib/haos/edge haos-edge admin set-password`)
rm -f "${CHROOT}/var/lib/haos/edge/webui.passwd" \
      "${CHROOT}/var/lib/haos/edge/controlplane_"*.lock
rm -rf "${CHROOT}/var/lib/haos/edge/sessions"

# haos-setup em modo DEV (sync do /opt/haos desativado)? restaura o bloco
# original NO SNAPSHOT: a VM fica em DEV para sempre, mas a ISO sai com o
# update de produção (clone/pull do GitHub) funcionando. A transformação é
# reversível byte-a-byte (validada em round-trip).
if grep -q "DEV-VM-" "${CHROOT}/usr/local/bin/haos-setup" 2>/dev/null; then
  echo "  haos-setup: modo DEV detectado — restaurando sync de produção no snapshot..."
  python3 - "${CHROOT}/usr/local/bin/haos-setup" <<'PY'
import sys
p = sys.argv[1]
out, mode = [], None
for l in open(p).read().splitlines(keepends=True):
    if l.startswith('# >>> DEV-VM-NOTICE'):
        mode = 'notice'; continue
    if l.startswith('# <<< DEV-VM-NOTICE'):
        mode = None; continue
    if l.startswith('# >>> DEV-VM-SYNC-OFF'):
        mode = 'sync'; continue
    if l.startswith('# <<< DEV-VM-SYNC-OFF'):
        mode = None; continue
    if mode == 'notice':
        continue
    if mode == 'sync' and l.strip():
        out.append(l[2:] if l.startswith('# ') else l)
    else:
        out.append(l)
open(p, 'w').write(''.join(out))
PY
  bash -n "${CHROOT}/usr/local/bin/haos-setup" && echo "  ✓ sync restaurado (haos-setup em modo produção no snapshot)"
else
  echo "  haos-setup: já em modo produção (sem marcador DEV)"
fi
echo "  ✓ scrubbed"

echo "[3/5] lb binary (estágio binário; hooks não rodam)"
cd "${DISTRO_DIR}"
# o lb vive no container builder (igual ao build-iso.sh) — nunca no host
docker run --rm --privileged -v "${DISTRO_DIR}":/build haos-iso-builder:latest \
  bash -c 'cd /build && lb clean --binary 2>&1 && \
    mkdir -p .build && : > .build/chroot_hostname && : > .build/chroot_hosts && \
    lb binary 2>&1' | tail -n 3
test -f "${DISTRO_DIR}/live-image-amd64.hybrid.iso" || test -f "${DISTRO_DIR}/haos-linux-1.0-amd64-amd64.hybrid.iso"

echo "[4/5] validação rápida do squashfs"
docker run --rm -v "${DISTRO_DIR}:/b:ro" haos-iso-builder:latest bash -c '
  cd /tmp && rm -rf v && unsquashfs -d v /b/binary/live/filesystem.squashfs \
    etc/hostname boot/vmlinuz-* usr/bin/python3.13 opt/haos/venv/bin/python \
    usr/local/bin/haos-setup usr/local/bin/node \
    etc/systemd/system/haos-hostname.service etc/systemd/system/unbound.service \
    etc/systemd/system/multi-user.target.wants/unbound.service \
    var/lib/haos \
    home/haos/.ssh home/haos/.git-credentials 2>/dev/null | tail -n 1
  echo "  hostname: $(cat v/etc/hostname 2>/dev/null)"
  echo "  kernels assados: $(ls v/boot/ 2>/dev/null | grep -E "^vmlinuz" | tr "\n" " ")"
  echo "  venv: $([ -x v/usr/bin/python3.13 ] && v/usr/bin/python3.13 --version 2>&1)"
  echo "  maquina-id: $(test -f v/etc/machine-id && echo PRESENTE || echo ausente)"
  echo "  chave aceitacao: $(test -e v/home/haos/.ssh/authorized_keys && echo PRESENTE || echo ausente)"
  echo "  credencial git: $(test -e v/home/haos/.git-credentials && echo PRESENTE || echo ausente)"
  echo "  unit hostname: $(test -f v/etc/systemd/system/haos-hostname.service && echo presente || echo ausente)"
  echo "  unbound mascarado: $(test -L v/etc/systemd/system/unbound.service && echo sim || echo NAO)"
  echo "  unbound habilitado: $(test -e v/etc/systemd/system/multi-user.target.wants/unbound.service && echo SIM || echo nao)"
  echo "  senha webui: $(test -e v/var/lib/haos/edge/webui.passwd && echo PRESENTE-BUG || echo ausente-ok)"
  echo "  sessoes webui: $(test -d v/var/lib/haos/edge/sessions && echo PRESENTE-BUG || echo ausente-ok)"
  echo "  (var/lib/haos extraido: $(test -d v/var/lib/haos && echo sim || echo NAO))"
  grep -c UV_PROJECT_ENVIRONMENT v/usr/local/bin/haos-setup | sed "s/^  haos-setup UV fix: /  /"
' 2>/dev/null

echo "[5/5] copiando ISO final"
if [ -f "${DISTRO_DIR}/live-image-amd64.hybrid.iso" ]; then
  mv -f "${DISTRO_DIR}/live-image-amd64.hybrid.iso" "${ISO}"
elif [ -f "${DISTRO_DIR}/haos-linux-1.0-amd64-amd64.hybrid.iso" ]; then
  mv -f "${DISTRO_DIR}/haos-linux-1.0-amd64-amd64.hybrid.iso" "${ISO}"
fi
ls -lh "${ISO}"
echo "PRONTO: ${ISO}"
