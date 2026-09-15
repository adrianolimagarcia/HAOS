# HAOS Linux - Build Recipe & Blueprint

Projeto de construção da distribuição independente **HAOS Linux** baseada no **Debian 13 (Trixie)** com **Kernel Linux 6.18 LTS**.

## Instalação

A ISO é **híbrida**: boota por **BIOS** e por **UEFI**, e traz dois caminhos
distintos — o modo **live/appliance** e o **instalador de SO** (o
`debian-installer` do Debian, com frontend de texto e gráfico).

### Menu de boot

| Entrada | O que faz |
|---|---|
| `HAOS Linux 1.0 (Live — Kernel tunado: Preempt Full + I/O)` | Boota o sistema **live** com os ajustes de I/O e scheduler. **Não instala nada** — é o appliance rodando da mídia. |
| `HAOS Linux 1.0 (Modo Live RAM / Appliance)` | Live em RAM (`toram`): depois do boot a mídia pode ser removida. |
| `HAOS Linux 1.0 (Modo de Recuperação / Failsafe)` | Live mínimo (`single nomodeset`) para resgatar máquina que não sobe. |
| `Instalar o HAOS Linux no disco ...` → `Instalação (texto)` / `Instalação (gráfica)` | Instalador de SO completo: particiona, formata, copia o sistema e instala o GRUB. |
| ... → `Instalação automatizada (aplica o preseed)` | Mesmo instalador com `auto=true priority=critical`: dispensa as perguntas não-críticas, mas **o particionamento continua sendo perguntado**. |
| ... → `Modo de recuperação (rescue)` | d-i em modo rescue, para consertar um sistema já instalado. |

O instalador embarcado é o `live-installer`: ele **copia o rootfs live** para a
partição escolhida em vez de baixar pacotes do espelho. O sistema instalado é,
portanto, o mesmo artefato que boota da mídia — mesmos serviços, mesmos ajustes,
mesma customização de `/etc/default/grub`.

### O instalador não decide onde apagar

O preseed embarcado (`config/preseed/haos.cfg`, que o `installer_preseed` do
live-build concatena e entrega ao d-i como `/install/preseed.cfg`) **não contém
nenhuma diretiva de partição** — nem `partman-auto/*`, nem `partman-auto/disk`,
nem `partman/confirm`. A razão está no cabeçalho do próprio arquivo: a ISO é
artefato distribuível e a entrada "automatizada" consome preseed **em silêncio**,
então uma diretiva dessas apagaria disco sem confirmação em qualquer boot da
mídia. Escolher disco, esquema e **tamanho das partições** é sempre decisão de
quem instala.

### Instalação automatizada (frota/VM): injete o SEU preseed

Para instalar sem ninguém na frente, o preseed vem de fora da ISO:

- **VM/libvirt** — boot direto no kernel do instalador (o `vmlinuz` e o
  `initrd.gz` saem da própria ISO, em `/install/`), com o preseed por HTTP:

  ```xml
  <kernel>/caminho/vmlinuz</kernel>
  <initrd>/caminho/initrd.gz</initrd>
  <cmdline>auto=true priority=critical console=ttyS0,115200 preseed/url=http://192.168.122.1:8000/preseed-auto.cfg</cmdline>
  ```

  Esse preseed **pode e deve** trazer o particionamento (`partman-auto/disk`,
  `partman-auto/method`, `partman-auto/recipe`, `partman/confirm`) e
  `d-i debian-installer/exit/poweroff boolean true`, para a máquina desligar ao
  terminar em vez de reentrar no instalador — com boot direto de kernel, um
  reboot volta ao mesmo instalador e, com o particionamento preseedado, ele
  reparticionaria o disco em loop.

- **Boot pela mídia** — na entrada automatizada, tecle `e`, acrescente
  `preseed/url=http://seu-servidor/preseed.cfg` à linha `linux` e `Ctrl+X`.

Duas armadilhas que já custaram tempo e estão tratadas no preseed embarcado:

- **`ucf/changeprompt`** — a imagem customiza `/etc/default/grub`, então o
  postinst do grub2 o encontra como conffile modificado e pergunta (template
  `ucf/changeprompt`, tipo select) o que fazer. É pergunta **crítica**: sem
  resposta, a instalação automatizada para ali e não anda mais. O preseed
  embarcado responde "manter a versão local" — é ela que carrega o cmdline de
  kernel do HAOS. Um preseed de frota que não responda isso trava do mesmo jeito.
- **`preseed/file=` é passado à mão** no `--bootappend-install` de `auto/config`:
  o append que o live-build monta sozinho sai sem o prefixo `preseed/` e o d-i o
  ignora **em silêncio** — a mídia chega a trazer `/install/preseed.cfg`, mas
  ninguém o lê.

### Requisitos de disco

O rootfs live tem ~2,3 GB descomprimido (a ISO tem 1,6 GB) e o instalador copia o
sistema descomprimido. Reserve **≥ 20 GB** para haver folga de logs, banco de
sessões e cache do agente.

## Otimizações Embarcadas

1. **Rede:**
   - Algoritmo de congestionamento **BBRv3** ativado por padrão.
   - Escalonador de pacotes **FQ (Fair Queuing)** ativado.
   - Buffers TCP estendidos e `tcp_fastopen=3` para streaming contínuo de tokens e baixa latência P2P.
2. **Sistema de Arquivos & Storage:**
   - Preparado para **Btrfs** com compressão transparente `zstd:1` (`discard=async,noatime,commit=60`).
   - Serviço `haos-storage-init` que desativa CoW (`chattr +C`) automaticamente nas pastas de bancos SQLite para performance máxima e evitar fragmentação.
   - Regras udev customizadas para I/O schedulers (`none` para NVMe, `mq-deadline` para SATA SSD, `bfq` para HDD).
3. **Memória & Kernel:**
   - **ZRAM** com compressão `zstd` configurada em metade da RAM física.
   - `transparent_hugepage=madvise` para evitar travamentos de alocação de memória por IA.
   - Limites de arquivos e processos estendidos (`nofile 1048576`, `nproc 65535`).
4. **Runtimes de Fábrica** (assados na imagem pelos hooks `config/hooks/live/`, consumindo `cache/`):
    - **Python 3.13** em `/opt/haos/venv`, gerenciado com **uv** (`uv 0.12.x`; venv materializado com o Python 3.13.5 do sistema — as dependências do agente são instaladas pelo `haos-setup` no primeiro boot via `uv sync --frozen --no-dev --python 3.13`).
    - **Node.js 26 LTS** (v26.8.2) + npm (11.19.1) em `/usr/local` — usados pelos builds web/TUI e pelo gtop.
    - **SQLite 3.53.4 compilado** com `FTS5`, `RTREE`, `MATH_FUNCTIONS` e `DBSTAT_VTAB` em `/usr/local` (o módulo sqlite3 do Python e o wrapper `haos` o usam por precedência do ld.so/LD_PRELOAD; o pacote Debian `libsqlite3-0` permanece íntegro no dpkg).
    - **gtop** (monitor de processos no terminal, npm global) e **sudo** (usuário `haos` no grupo `sudo`).
    - Daemon **`haos-edge`** (Rust) **pré-compilado vendored** em `/usr/local/bin/haos-edge` — a toolchain Rust **não** é assada. O binário é o **servidor do WebUI** (`haos-edge server`, unit `haos-edge.service` em `127.0.0.1:8788`: terminal PTY, tasks, SPA) **com autenticação do operador** (senha PBKDF2 em `/var/lib/haos/edge/webui.passwd`, sessão por cookie HttpOnly com "salvar login" de 30 dias; sem senha definida o WebUI fica inacessível — fail-closed). Definir a senha no nó: `HAOS_DATA_DIR=/var/lib/haos/edge haos-edge admin set-password`. O mesmo binário também é CLI (`status`, `team`, `doc`, `doctor`). Detalhes: `docs/haos/STANDALONE_WEBUI.md` §3.1.
    - **Playwright Chromium assado** (a partir da ISO derivada da VM): `chromium-1234` + `chromium_headless_shell` + `ffmpeg` (~656 MB) em `/home/haos/.cache/ms-playwright/` — o doctor mostra "✓ Playwright Chromium (browser engine)" e a tool `browser` fica disponível offline. (A ISO do caminho clássico `build-iso.sh` NÃO carrega o binário — só o `iso-from-vm.sh`.)
      - **Nunca aponte `PLAYWRIGHT_BROWSERS_PATH` para um diretório sem Chromium.** Uma vez setada, o Playwright resolve o browser **só** sob esse caminho: um valor obsoleto quebra todo launch (browser, stealth fetch, google_meet) enquanto o `haos doctor` segue verde, porque o check de presença também varre o cache default. Era exatamente o caso de `/opt/haos/.playwright`, que **nunca existiu** e estava no `haos-gateway.service` e no wrapper `haos` — corrigido em 2026-09-12 (a variável saiu do service; no wrapper só é passada quando `${HOME}/.cache/ms-playwright` existe) e o doctor passou a avisar (`⚠ PLAYWRIGHT_BROWSERS_PATH=... has no Chromium build`).
    - **`haos-fetch`** (bypass de Cloudflare/anti-bot via Scrapling) em `/usr/local/bin/haos-fetch`: wrapper do distro que roda `scripts/haos_fetch_cf.py` no venv (`--strategy http|stealth|auto`; stealth usa o Chromium assado). O backend **não** está em `[all]` — é opt-in e resolve no primeiro uso (`LAZY_DEPS["fetch.scrapling"]`); o instalador também pré-instala (`SKIP_FETCH_EXTRA=1` desativa).
5. **Rotina periódica de memória** (agendada no cron nativo, sem LLM):
   - Job **`SYSTEM - cron: manutencao noturna`** (`0 3 * * *`, `--no-agent --script`) visível em
     `haos cron list` e na aba Scheduler do WebUI. O `haos-storage-init` (boot,
     antes do `haos-gateway`, que é quem faz o tick) instala
     `~/.haos/scripts/haos_nightly_maintenance.sh` e recria o job se faltar —
     o cron rejeita caminho absoluto, o script tem de viver sob `<HERMES_HOME>/scripts/`.
   - O que ele faz: `PRAGMA integrity_check` nos SQLite do nó + popula a memória
     canônica (`scripts/haos_memory_populate.py`): notas do vault →
     `memory/ragflow.db` (DeepDoc/RAG FTS5), → `memory/graphrag.db` (store
     canônico, GOV-008) e consolidação do dream (`memory/reconciled_memories.db`
     + lições OKF). AST do código é opt-in (`HAOS_MAINTENANCE_AST=1`).
   - Rodar à mão: `haos cron run <job-id>`; a saída fica em `~/.haos/cron/output/<job-id>/`.
   - **Não remover este job.** A escrita de nota via `obsidian_save_note` já
     sincroniza DeepDoc + GraphRAG na hora, mas três funções continuam sendo SÓ
     deste job; sem ele o nó degrada em silêncio:
     1. **Dream (consolidação de sessões)** — único produtor das lições OKF e
        memórias reconciliadas. Nenhum outro mecanismo o dispara: removeu o job,
        as sessões acumulam para sempre (o cursor para de avançar) e o agente
        nunca mais consolida o que aprendeu.
     2. **Reindex completo do vault** — única cobertura para notas escritas por
        outras vias que NÃO passam pelo `obsidian_save_note`
        (`master_plan_orchestrator`, `context/memory/provider`,
        `federated_fabric`, edições manuais e rsync): sem ele essas notas ficam
        invisíveis ao `haos-edge doc search` e ao `graphrag_query`. O reindex
        também auto-repara drift/arquivos legados.
     3. **Integrity check** — único health check (`PRAGMA integrity_check`) dos
        SQLite do nó.

## Estrutura de Pastas

```
distro/haos-linux/
├── cache/                  # Binários pesados pré-baixados (Node 26, uv, SQLite) — preservado entre builds (o build usa `lb clean`, nunca `--purge`, senão o cache do host é apagado pelo bind mount)
├── config/
│   ├── hooks/              # Scripts de build do chroot (Node, gtop, uv+venv, SQLite, Rust, identidade)
│   ├── includes.chroot/    # Arquivos injetados no sistema (/etc/sysctl.d, systemd, /usr/local/bin, plugins)
│   └── package-lists/      # Pacotes Debian instalados no sistema base
├── scripts/                # Automações de download e helpers de build
├── iso-from-vm.sh          # Gera a ISO a partir do rootfs da VM (ciclo principal)
└── README.md
```

## Como baixar/atualizar as dependências em cache

```bash
./distro/haos-linux/scripts/fetch-dependencies.sh
```

## Atualizações embarcadas (repo HAOS)

A imagem nasce pronta para `haos update` baixar direto de
`github.com/adrianolimagarcia/HAOS` **sem login GitHub** — em qualquer um dos
dois modos:

**Modo A — repo público (mais simples, sem chave):** o clone/fetch é HTTPS
anônimo e funciona sem nenhuma credencial. Nada a fazer além de publicar o repo.

**Modo B — repo privado (deploy key read-only):**

1. **Deploy key read-only** (`haos-update-ro`) é injetada no **momento do build**
   em `/etc/haos/keys/update_ed25519` (`0600`) pelo `build-iso.sh` — a partir de
   `$HAOS_UPDATE_KEY` ou `/root/.haos/keys/update_ed25519` na máquina de build.
   - **Para criar e registrar a chave: `./scripts/provision_update_key.sh`.** Gera o
     par ed25519 no caminho padrão, imprime a metade **pública** para colar em
     `github.com/adrianolimagarcia/HAOS/settings/keys/new` (com "Allow write access"
     **desmarcado**), confere que o `distro/haos-linux/.gitignore` cobre o destino e
     que privada/`.pub` são o mesmo par. Recusa substituir uma chave existente sem
     `--force` — a antiga está embutida em toda ISO já construída, e trocá-la sem
     revogar no GitHub quebra o `haos update` dos nós que vieram dela.
   - O build valida que o arquivo é a chave **privada** (cabeçalho
     `BEGIN ... PRIVATE KEY`) antes de injetar — apontar a `.pub` por engano é
     detectado e ignorado.
   - A chave **nunca é commitada**: o destino está no `.gitignore` e o
     `build-iso.sh` remove os artefatos injetados do tree ao final (trap).
   - Revogação = apagar a deploy key no GitHub + reconstruir a imagem.
2. **Host key do `github.com`** é embarcado em `/etc/ssh/ssh_known_hosts`
   (leitura pública `0644`; SSH não-interativo recusa o primeiro contato).
3. **Instalador** (`scripts/install_haos.sh`) é embarcado em
   `/usr/local/sbin/haos-install` para re-provisionamento/manutenção no sistema
   instalado.
   - Provisiona **serviços systemd** — `haos-controlplane.service` e, pelo
     caminho canônico do repo (`hermes gateway install`), o
     `hermes-gateway.service`. Sem eles o controlplane só existia por `nohup` e
     um reboot levava o HAOS embora. Escopo acompanha quem instala (root →
     sistema, senão usuário); sem systemd no host, cai para `nohup`.
   - Instala o conjunto curado de extras (`[all]`) por padrão: sem eles a
     instalação sobe sem MCP (servidores ficam "parked") e sem os provedores já
     configurados. `--extras none` e `--services none` desligam; `--no-extras`
     e `--no-services` são atalhos.
4. Hook `45-haos-update-credential.chroot` endurece a chave de bootstrap e
   **semeia uma cópia no store do nó** (`/home/haos/.haos/keys/update_ed25519`,
   dono `haos:haos` `0600`) — quem executa `haos update` é o usuário `haos`,
   não root.
5. O wiring do `origin` (**fetch via chave read-only / push via HTTPS admin**)
   acontece **após o clone**, no `haos-setup`/`haos-install` do primeiro boot —
   o checkout de `/opt/haos` não existe dentro da imagem.

> ⚠️ No modo B a chave viaja dentro do artefato: trate a ISO como sensível —
> quem tiver a imagem consegue clonar o código do repo (read-only). Segredos
> (`.env`, API keys) continuam proibidos dentro do repositório. No modo A isso
> é irrelevante (o código já é público).

**Decisão: o Modo B é o modo padrão — a chave fica embutida.** É o que faz
`haos update` funcionar numa instalação nova sem nenhum passo manual, e o que
mantém o repo privado. O custo aceito é que a ISO vale como credencial
read-only; o que **não** se aceita é a chave ficar solta fora dela.

**Rotação da chave embutida — a ordem importa:**

1. `./scripts/provision_update_key.sh --force` gera o novo par (a antiga só é
   substituída com `--force`, justamente para não invalidar ISOs já distribuídas
   por acidente).
2. Registre a metade **pública** nova em
   `github.com/adrianolimagarcia/HAOS/settings/keys/new`, com "Allow write
   access" **desmarcado**. Deixe a antiga registrada por enquanto.
3. `./build-iso.sh` — a imagem nova passa a carregar a chave nova.
4. Distribua/atualize os nós que devem migrar.
5. **Só então** remova a chave antiga do GitHub.

Enquanto as duas estiverem registradas, nós das ISOs antigas e novas funcionam.
Revogar antes do passo 4 quebra o `haos update` de toda ISO já distribuída — é o
único passo irreversível da lista.

Higiene (o build já cuida, mas vale saber o que esperar): a chave privada nunca é
commitada (`.gitignore` cobre o destino injetado, o `chroot/` de estágio e o
`binary/`); o `build-iso.sh` apaga a cópia injetada **e** as cópias que o
`lb build` deixa em `chroot/` (varredura por nome de arquivo no `trap`); e o
`.dockerignore` mantém o diretório de perfil fora do contexto do `docker build`,
de modo que a chave não entra numa camada da imagem nem por um `COPY` futuro.
Depois de um build, `find distro -name update_ed25519` deve voltar **vazio** — a
chave existe só em `/root/.haos/keys/` (o cofre) e dentro da ISO.

## Wrapper-antigravity (vendorizado)

O `haos-antigravity.service` roda `server.mjs` de
`github.com/adrianolimagarcia/wrapper-antigravity` — proxy local que traduz
`/v1/chat/completions` (OpenAI) para a API do Antigravity/Gemini via OAuth do CLI
`agy`. O arquivo é **vendorizado** em
`config/includes.chroot/opt/haos/wrapper-antigravity/` (mais o `README.md`
upstream), com a proveniência em `UPSTREAM.md` (repo + commit).

- **Atualizar:** `./sync-antigravity-wrapper.sh --apply` e reconstruir a ISO.
  A comparação é por **hash de conteúdo**, não por data; `--check` só compara
  (exit 3 quando difere) e `--ref v1.4.0` pina um tag/SHA. O `build-iso.sh`
  chama `--check` no início e **avisa sem falhar** quando o perfil está atrasado —
  build offline continua funcionando.
- **Configuração:** `/etc/haos/antigravity.env` (lido pela unidade, que já traz
  o layout: `127.0.0.1`, porta `8790`, token e contas do opencode). O arquivo
  aponta cache/spend/`providers.json` para `/var/lib/haos/antigravity/` — criado
  pelo hook `05-create-haos-user.chroot`, dono `haos:haos`. Sem isso o wrapper
  usaria `DATA_DIR` = o próprio diretório do código, e `saveExternalProviders()`
  grava `providers.json` **sem criar diretório**.
- **Autenticação é por nó**, não cabe na imagem: `agy auth login` como o usuário
  `haos` e depois `systemctl restart haos-antigravity`. Sem token o serviço fica
  **no ar** e recusa toda requisição (journal: `No accounts loaded in pool`), e o
  `haos-setup` avisa ao escolher o Antigravity como provider.
- Detalhe do upstream que vale corrigir lá: `cache/embeddings.json`,
  `cache/embeddings-config.json` e `cache/proxy-settings.json` são derivados de
  `MODULE_DIR` e **não** têm variável de ambiente — essas configurações persistem
  dentro de `/opt/haos/wrapper-antigravity/cache/`, ou seja em diretório de
  código.


## ISO a partir da VM (ciclo de desenvolvimento)

Fluxo principal a partir de 10/09/2026: **valida-se e ajusta-se tudo DENTRO da
VM de aceitação** (pacotes, hostname, kernel, config) e, no final, a ISO é
congelada do estado validado da própria VM — sem rebuild de imagem por hooks:

```bash
./iso-from-vm.sh [IP_DA_VM] [TAG]    # ex.: ./iso-from-vm.sh 192.168.122.130 final
```

O script:
1. rsync do rootfs da VM (via SSH + sudo NOPASSWD, um filesystem só) → `chroot/`;
2. scrub do estado privado/transiente (machine-id, chaves ssh de host, chave de
   aceitação, estado/DBs do `.haos`, caches apt/log, `.git` do `/opt/haos`);
   mantém hostname, kernel, venv cheio, chave de update e o wrapper;
3. `lb binary` (só o estágio binário — os hooks do chroot **não** rodam, pois o
   rootfs da VM já tem tudo); pré-cria os stagefiles `chroot_hostname`/`chroot_hosts`
   para o live-build não sobrescrever o hostname com `localhost.localdomain`;
4. valida o squashfs (hostname, kernels, venv, ausência de chaves) e nomeia a ISO.

Custo de uma ISO nova: ~2 min de rsync + ~5 min de `lb binary` (sem download de
pacotes; usa o cache).

### Modo DEV na VM (desenvolvimento direto no appliance)

A VM de aceitação é o checkout de desenvolvimento:

- **Sync do `/opt/haos` desativado**: o `haos-setup` da VM roda com o bloco de
  clone/`rsync --delete`/pull **comentado** (marcadores `DEV-VM-SYNC-OFF`).
  Editar código em `/opt/haos` na VM não é mais sobrescrito por re-execuções do
  setup. Ativar/desativar: comentar/descomentar o bloco entre os marcadores.
- **Push direto da VM**: token do GitHub em `~/.git-credentials`
  (`credential.helper store`; o origin push já é HTTPS). Teste de escrita
  validado (branch temporária push+delete).
- **A ISO sai SEMPRE em modo produção**: o `iso-from-vm.sh` detecta os
  marcadores no snapshot, **restaura o bloco de sync byte-a-byte** (round-trip
  validado) e **escova as credenciais** (`~/.git-credentials`, `~/.config/gh`,
  chaves privadas `~/.ssh/id_*`) — token admin nunca chega à ISO.

## Limitações conhecidas (validadas em 10/09/2026)

- **Playwright Chromium assado na ISO derivada da VM** (não na clássica): o
  `npx playwright install --with-deps chromium` rodado na VM de aceitação fica
  no snapshot (`~/.cache/ms-playwright`, ~656 MB) — `haos doctor` sai
  "✓ Playwright Chromium", tool `browser` disponível offline. Na ISO do
  `build-iso.sh` o binário não está (instalar sob demanda:
  `sudo -u haos sh -c 'cd /opt/haos && npx playwright install --with-deps chromium'`).
- **"Kernel 6.18 LTS" depende da origem da ISO**: o caminho clássico
  (`build-iso.sh`) assa o kernel do trixie (`linux-image-amd64` → 6.12.107); a
  **ISO derivada da VM** (`iso-from-vm.sh`) carrega o kernel REAL instalado na
  VM — a VM de aceitação roda **6.18.15+deb13** (trixie-backports, LTS até
  dez/2028), então o claim vira verdadeiro no artefato VM. O texto
  (`/etc/issue`, `GRUB_DISTRIBUTOR`) já diz "Kernel 6.18 LTS".
- **Hostname = `haosagent`** (garantido): o instalador (d-i live) ainda
  renomeia para `debian` no fim da instalação, mas o oneshot
  `haos-hostname.service` (first boot, stamp em `/etc/haos/.hostname-set`)
  restaura `haosagent` + `127.0.1.1 haosagent` no `/etc/hosts`. Na ISO
  derivada da VM, o `/etc/hostname` do squashfs já sai `haosagent` (o
  `iso-from-vm.sh` pré-cria o stagefile do live-build para ele não
  sobrescrever com `localhost.localdomain`).
- **venv de fábrica vazio**: o `/opt/haos/venv` assado tem só o interpretador
  3.13; as dependências do agente são instaladas no primeiro boot pelo
  `haos-setup` (`uv sync --frozen --no-dev --python 3.13`). Não existe "venv de
  fábrica com tudo instalado".
- **~~`unbound.service` falha no boot~~ RESOLVIDO (11/09/2026)**: o `unbound` e o
  `haos-dns` (resolver Rust do HAOS) disputavam `127.0.0.1:53` — `bind: address
  already in use` → 5 restarts → `failed` → `degraded`. **Race condition real**:
  o vencedor da corrida variava por boot (neste nó o `haos-dns` ganhou; em outro
  o unbound poderia servir o DNS no lugar do resolver do HAOS). Fix: dono
  determinístico da `:53` = `haos-dns`; `unbound` **mascarado** no runtime
  (`/etc/systemd/system/unbound.service → /dev/null`, trackeado em
  `config/includes.chroot/`) — o pacote continua instalado porque é o resolver do
  chroot durante o **build**. Validado com reboot: `systemctl is-system-running`
  = `running`, 0 units failed.
- **`haos-dns` com upstream cifrado (DoT/DoH) — 12/09/2026**: o resolver do nó
  falava UDP puro com os quatro resolvedores públicos, ou seja, o ISP via toda
  consulta. Agora o transporte de saída é configurável em `/etc/haos/dns.toml`
  (trackeado em `config/includes.chroot/etc/haos/`): DoT (RFC 7858) e DoH
  (RFC 8484) com UDP puro como último recurso. **Invariante**: todo upstream
  carrega o IP explicitamente — DoT valida o certificado pelo `#server_name`
  (`9.9.9.9@853#dns.quad9.net`) e DoH pelo `#ip` fixado na URL
  (`https://cloudflare-dns.com/dns-query#1.1.1.1`), então o daemon nunca precisa
  resolver o nome do próprio upstream (seria circular: ele É o resolvedor do nó).
  Sem o arquivo valem os quatro upstreams UDP de sempre. Verificado na VM com um
  transporte por vez: só-DoT resolve, só-DoH resolve, e o `getent hosts` do nó
  segue funcionando. O mesmo commit corrigiu um vazamento de memória: o cache era
  um `HashMap` sem evicção (nada removia chave vencida) e virou `LruCache` com
  teto de 10 000 entradas; a resposta de upstream com `query id` diferente do
  pedido agora é descartada em vez de repassada.
- **`haos-gateway`/`haos-mesh` nascem em `failed`/`activating`** até o `haos-setup`
  preencher o venv e reiniciar os daemons (unidades habilitadas na imagem com
  ExecStart no venv, que só existe de verdade depois do setup — "ovo e galinha"
  intencional: o fluxo correto é instalar → `haos-setup`).
- **~~`haos-edge` reinicia em loop~~ RESOLVIDO (11/09/2026)**: o unit rodava
  `/usr/local/bin/haos-edge` **sem subcomando** → o binário caía em `cmd_status()`
  (imprime o banner e sai com 0) → `Restart=always` → loop infinito; o servidor
  nunca subia. O unit também rodava como `root` (HAOS_HOME errado = `/root/.haos`,
  DBs "✗ Not initialized") e o lock do control-plane ficava em `/tmp` com dono
  root (Permission denied ao trocar para o usuário `haos`). Fix: unit com
  `ExecStart=haos-edge server --port 8788 --host 127.0.0.1 --static-dir
  /opt/haos/hermes/platform/webui/static`, `User=haos`, `HAOS_HOME=/home/haos/.haos`,
  `HAOS_DATA_DIR=/var/lib/haos/edge`. Validado com reboot: `active`, 0 restarts,
  `GET /health` = healthy. **Atenção**: o servidor não tem autenticação e expõe
  terminal PTY → bind **127.0.0.1 apenas** (acesso via tunnel SSH); abrir para a
  rede exige auth primeiro.
- **CLI `hermes`**: o doctor sugere `~/.local/bin/hermes`; no appliance o entry
  canônico é o wrapper `/usr/local/bin/haos` (que re-executa como usuário `haos`).
  Criar o symlink é opcional.
