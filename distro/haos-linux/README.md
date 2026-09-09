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

## Atualizações embarcadas (repo HAOS privado)

A imagem nasce pronta para `haos update` baixar direto do repositório privado
`github.com/adrianolimagarcia/HAOS` **sem login GitHub**:

1. **Deploy key read-only** (`haos-update-ro`) é injetada no **momento do build**
   em `/etc/haos/keys/update_ed25519` (`0600`) pelo `build-iso.sh` — a partir de
   `$HAOS_UPDATE_KEY` ou `/root/.haos/keys/update_ed25519` na máquina de build.
   - A chave **nunca é commitada**: o destino está no `.gitignore` e o
     `build-iso.sh` remove os artefatos injetados do tree ao final (trap).
   - Revogação = apagar a deploy key no GitHub + reconstruir a imagem.
2. **Host key do `github.com`** é embarcado em `/etc/ssh/ssh_known_hosts`
   (SSH não-interativo recusa o primeiro contato).
3. **Instalador** (`scripts/install_haos.sh`) é embarcado em
   `/usr/local/sbin/haos-install` para re-provisionamento/manutenção no sistema
   instalado.
4. Hook `45-haos-update-credential.chroot` endurece permissões e, se
   `/opt/haos` já for um repo git na imagem, aponta o `origin` para
   **fetch via chave read-only** + **push via HTTPS** (admin).

> ⚠️ Como a chave viaja dentro do artefato, trate a ISO como sensível: quem tiver
> a imagem consegue clonar o código do repo (read-only). Segredos (`.env`, API
> keys) continuam proibidos dentro do repositório.

