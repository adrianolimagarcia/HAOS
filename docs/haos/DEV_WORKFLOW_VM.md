# DEV_WORKFLOW_VM — Instruções e caminhos do appliance HAOS

Memória durável do fluxo de desenvolvimento do HAOS a partir de 10/09/2026.
**Leia antes de tocar no appliance/VM.** Este arquivo é a fonte da verdade
operacional; atualize-o quando algo mudar.

---

## 1. Princípios fixos (não negociáveis)

- **Repo único**: `github.com/adrianolimagarcia/HAOS` (clone local: este repo,
  pastas `HERMES-TURBO`/`/usr/local/lib/haos-agent`). Branches **`main`** e
  **`haos-standalone`** sempre em lockstep. Branding só **haos**. Upstream =
  o próprio repo.
- **NÃO executar testes de stress.** Runs direcionados apenas.
- **Não gerar ISO durante a iteração.** UMA ISO final, no fim, a partir da VM
  já validada (`./iso-from-vm.sh`).
- **Desenvolver/editar DIRETO na VM** (ambiente 100% isolado) e validar lá.
  O snapshot da VM é a fonte da ISO; o repo é a fonte de DISTRIBUIÇÃO.

## 2. Política DEV (vigente)

- **Sync do `/opt/haos` desativado na VM**: o `haos-setup` da VM roda em modo
  DEV — o bloco `clone`/`rsync --delete`/`pull` está **comentado** entre os
  marcadores `# >>> DEV-VM-SYNC-OFF` e `# <<< DEV-VM-SYNC-OFF` (com aviso em
  `# >>> DEV-VM-NOTICE`). Editar código em `/opt/haos` na VM NÃO é sobrescrito
  por re-execuções do setup.
- **Push direto da VM**: token GitHub em `~/.git-credentials`
  (`credential.helper store`; origin push = HTTPS). Identidade git:
  `HAOS Fork (local)` / `haos-fork@local`. Teste de escrita validado.
- **A ISO sai SEMPRE em modo produção**: o `iso-from-vm.sh` detecta os
  marcadores, **restaura o bloco de sync no snapshot** (round-trip
  byte-a-byte: `restore(DEV) == original` — validado) e **escova credenciais**
  (`~/.git-credentials`, `~/.config/gh`, `~/.ssh/id_*`, `.env`, `config.yaml`)
  — o token admin nunca chega à ISO.
- O `haos-setup` do **repo** permanece com o sync ATIVO (distribuição: novas
  instalações clonam/puxam do GitHub). Só o da VM fica DEV.

## 3. Caminhos (paths)

### VM de aceitação (golden)
| O quê | Caminho / valor |
|---|---|
| IP atual | ler `cat /tmp/haos-vm-ip.txt` (hoje `192.168.122.130`) |
| Usuário / sudo | `haos` (sudo NOPASSWD) |
| Chave SSH | `/root/haos-vm/id_ed25519` |
| SSH helper | `ssh -i /root/haos-vm/id_ed25519 -o StrictHostKeyChecking=no haos@$IP '<cmd>'` |
| Harness da VM | `/root/haos-vm/` → `preseed-auto.cfg`, `haos-acceptance.xml`, `haos-installed.xml`, `wait-install.sh` |
| Disco (qcow2) | `/var/lib/libvirt/images/haos-acceptance.qcow2` (80G) |
| Preseed HTTP | `http://192.168.122.1:8000` (server: `python3 -m http.server 8000 --bind 192.168.122.1 --directory /root/haos-vm`, nohup, reiniciar se morto) |
| Rootfs da VM | `/opt/haos` (código, dono `haos:haos`), `~/.haos/` (config/state), `~/.cache/ms-playwright` (Chromium) |

### Distro (pipeline de ISO)
| O quê | Caminho |
|---|---|
| Pipeline principal (VM→ISO) | `distro/haos-linux/iso-from-vm.sh` |
| Build clássico (hooks) | `distro/haos-linux/build-iso.sh` |
| Docs da distro | `distro/haos-linux/README.md` |
| Builder (container) | imagem `haos-iso-builder:latest` (`lb` SÓ existe lá — nunca no host) |
| Stagefiles live-build | `distro/haos-linux/.build/chroot_hostname`, `chroot_hosts` (pré-criar para preservar hostname) |
| Chroot do snapshot | `distro/haos-linux/chroot/` (gerado pelo rsync da VM) |
| ISOs | `distro/haos-linux/haos-linux-1.0-<tag>.iso` + backup `/root/iso-backup/` |

### Instalação / repo
| O quê | Caminho |
|---|---|
| Install tree (host) | `/usr/local/lib/haos-agent` (pull `--ff-only origin main`) |
| Clone local do repo | este workspace (HERMES-TURBO) |
| Deploy key read-only | assada na imagem (hook `45-haos-update-credential.chroot`), **NÃO** é a admin |

## 4. Deploy (toda mudança incorporável)

```bash
git add -A && git commit -m "fix(escopo): assunto curto em pt sem acentos"
git push origin haos-standalone
git branch -f main haos-standalone
git push origin haos-standalone:main
cd /usr/local/lib/haos-agent && git pull --ff-only origin main
```
Exemplos de estilo: `e420377c9`, `5f8f46034`, `2cd603b9b`; assuntos
`fix(scope): ...` / `feat(distro): ...` / `feat(haos): ...`.

## 5. Pipeline VM→ISO — pontos críticos (já pagos em sangue)

1. `rsync -aHAXx --numeric-ids --delete` do rootfs da VM via
   `--rsync-path="sudo rsync"`; excludes de estado (`/proc,/sys,/dev,/run,/tmp,
   /var/tmp,/var/log,/var/lib/apt/lists,/var/cache,/var/lib/dhcp,
   /var/lib/NetworkManager,~/.cache` **com include de
   `/home/haos/.cache/ms-playwright/`**, `.bash_history`, `/opt/haos/.git/`,
   `/etc/resolv.conf` é removido no scrub). Variável `CHROOT_READY=1` reusa o
   chroot existente (não re-rsync).
2. Scrub (imperativo): `etc/machine-id`, `var/lib/dbus/machine-id`,
   `var/lib/systemd/random-seed`, `etc/ssh/ssh_host_*`, `home/haos/.ssh/*`
   (authorized_keys, known_hosts, config, **id_\***), `home/haos/.git-credentials`,
   `home/haos/.gitconfig`, `home/haos/.config/gh`, `etc/resolv.conf`,
   `home/haos/.haos/{state.db*,kanban.db*,cron/executions.db*,config.yaml,.env,
   sessions,logs,memories}`, `var/lib/haos/antigravity/*`.
   **Preservado**: hostname, kernel, venv cheio (165 pkgs), chave de update
   `/etc/haos/keys` + `/home/haos/.haos/keys`, wrapper-antigravity, Chromium.
   Também: `mkdir -p chroot/var/cache/apt/archives` (o lb copia debs para lá —
   sem isso: `E: An unexpected failure occurred`).
3. `lb binary` roda **no container**: `docker run --rm --privileged -v "$PWD":/build
   haos-iso-builder:latest bash -c 'cd /build && lb clean --binary && mkdir -p
   .build && : > .build/chroot_hostname && : > .build/chroot_hosts && lb binary'`.
   Os stagefiles fazem o live-build PULAR `chroot_hostname`/`chroot_hosts`
   (senão grava `localhost.localdomain` e diverte `/usr/bin/hostname` — o
   `lb config --hostname` NÃO existe neste live-build: `E: invalid arguments`).
   Hook do chroot NÃO roda (rootfs da VM já tem tudo).
4. Restauração do haos-setup DEV (ver §2) no snapshot — round-trip validado.
5. Validação do squashfs (hostname, TODOS os `vmlinuz-*`, venv via
   `usr/bin/python3.13`, ausência de machine-id/chaves) + rename do ISO.

## 6. Estado validado da VM (golden — 10/09/2026)

- Hostname **`haosagent`**: garantido pelo oneshot `haos-hostname.service`
  (first boot, stamp `/etc/haos/.hostname-set`) — o d-i live renomeia para
  `debian` no fim da instalação mesmo com o `/etc/hostname` certo no squashfs.
- Kernel **`6.18.15+deb13-amd64`** (trixie-backports, LTS até dez/2028)
  instalado e bootado na VM. ISO-clássica = 6.12.107; ISO-da-VM = 6.18.15 real.
- **Playwright Chromium assado**: `~/.cache/ms-playwright/` (chromium-1234 +
  headless-shell + ffmpeg, ~656 MB). Doctor: `✓ Playwright Chromium (browser
  engine)`, tool `browser` disponível offline.
- Serviços `active`: `haos-gateway`, `haos-mesh` (127.0.0.1:9120), `haos-dns`,
  `haos-antigravity`. Venv `/opt/haos/venv`: Python 3.13.5, 165 pkgs,
  SQLite **3.53.4** (FTS5/RTREE) no caminho real do agente (`state.db`).
- **Sistema `running` com 0 units failed**: `unbound` mascarado e `haos-dns`
  dono de `127.0.0.1:53` (validado com reboot).
- `A6API_API_KEY` (do host) no `.env` da VM para teste de chat — **escovada na
  ISO**. Provider config: `model.provider: a6api`.
- Validação funcional feita: chat real com tool calls (respondeu kernel+hostname
  corretos), sessão no state.db, skills hub inicializado, handshake federado
  HMAC-SHA256 `established`, doctor runtime verde.
- `/etc/apt/sources.list` da VM: linhas `deb cdrom:` REMOVIDAS +
  `/etc/apt/apt.conf.d/00CDMountPoint` deletado (senão `apt update` falha).

## 7. Limitações conhecidas (não resolvidas)

- **RESOLVIDO (11/09/2026)** `unbound.service` vs `haos-dns`: os dois disputavam
  `127.0.0.1:53` (`bind: address already in use` → 5 restarts → `failed` →
  `degraded`); race real (o vencedor variava por boot — se o unbound ganhasse, o
  DNS do HAOS ficava fora). Fix: dono determinístico da `:53` = `haos-dns`;
  `unbound` **mascarado** no runtime
  (`/etc/systemd/system/unbound.service → /dev/null`, trackeado em
  `config/includes.chroot/`). O pacote segue instalado (é o resolver do chroot
  no build). Validado com reboot: `is-system-running` = `running`, 0 failed.
- `haos-edge` (Rust) em restart loop em alguns cenários; daemons gateway/mesh
  nascem `failed/activating` antes do primeiro `haos-setup` (venv vazio).
- Doctor: `browser-cdp`/`browser-use` "system dependency not met" (deps npm
  opcionais, não bloqueiam); advisories npm de tooling (build-time).
- A checagem "API key" do doctor não reconhece `A6API_API_KEY` (cosmético).

## 8. Comandos úteis

```bash
# VM
IP=$(cat /tmp/haos-vm-ip.txt)
ssh -i /root/haos-vm/id_ed25519 -o StrictHostKeyChecking=no haos@$IP 'haos status'
ssh -i /root/haos-vm/id_ed25519 -o StrictHostKeyChecking=no haos@$IP 'export TERM=xterm; haos doctor'
# reinstall (aceitação fresca): destroy/undefine → rm qcow2 → qemu-img create
#   → chown qemu → define → start → /root/haos-vm/wait-install.sh haos-acceptance 25
# ISO final
cd distro/haos-linux && ./iso-from-vm.sh $(cat /tmp/haos-vm-ip.txt) final
# round-trip do restore (prova rápida): aplicar o python do iso-from-vm.sh
#   sobre um haos-setup DEV e conferir diff vazio vs original
```