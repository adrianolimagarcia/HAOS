# HAOS Linux - Build Recipe & Blueprint

Projeto de construção da distribuição independente **HAOS Linux** baseada no **Debian 13 (Trixie)** com **Kernel Linux 6.18 LTS**.

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
4. **Runtimes de Fábrica:**
   - Python 3.13 isolado gerenciado com `uv` em `/opt/haos/venv`.
   - Node.js 26 LTS e npm em `/usr/local`.
   - SQLite 3.53+ compilado com `FTS5`, `RTREE`, `MATH_FUNCTIONS` e `DBSTAT_VTAB`.
   - Rust toolchain e daemon `haos-edge` compilado em `/usr/local/bin/haos-edge`.
   - Playwright Chromium headless em cache pronto para o agente navegar na web.

## Estrutura de Pastas

```
distro/haos-linux/
├── cache/                  # Pacotes e binários pesados pré-baixados (Node 26, uv, SQLite)
├── config/
│   ├── hooks/              # Scripts de build do chroot (Node, uv, venv, SQLite, Rust)
│   ├── includes.chroot/    # Arquivos injetados no sistema (/etc/sysctl.d, systemd, /usr/local/bin)
│   └── package-lists/      # Pacotes Debian instalados no sistema base
├── scripts/                # Automações de download e helpers de build
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

