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
   **Preservado**: hostname, kernel, venv cheio (92 pacotes — `ls -d
   /opt/haos/venv/lib/python3.13/site-packages/*.dist-info | wc -l`; a contagem
   antiga de "165 pkgs" era de outra métrica/estado), chave de update
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
6. **Versionamento: todo commit no `main` incrementa +1 na versão** (regra do dono,
   19/09/2026). `0.21.4` → `0.21.5`, **no mesmo commit** — não num commit separado,
   senão o commit do próprio bump exigiria outro bump. O comando é
   `python scripts/release.py --bump patch --bump-only`: escreve os arquivos e
   para — **não** commita, **não** cria tag, **não** publica.

   `--bump-only` existe desde 19/09/2026 por causa disso: antes, o único caminho
   que escrevia um bump era `--publish`, porque `update_version_files` só era
   alcançado dentro de `if args.publish:` — então incrementar a versão obrigava a
   publicar um release. `--bump-only` roda **antes** de qualquer consulta de tag,
   de propósito: avançar a versão não pode depender de tags existirem.

   **O patch é um contador monotônico deliberado, não semver** (confirmado pelo
   dono, 19/09/2026): `0.21.5` hoje, `0.21.6` no próximo, `0.21.9` → `0.21.10` →
   `0.21.11`, indefinidamente. Não há reset, não há significado de
   compatibilidade, e o minor não se move. Isso é intencional — **não "conserte"**
   para semver, não resete o patch, e não trate um patch de três dígitos como
   sinal de erro. `bump_version` faz aritmética inteira pura, então
   `0.21.999` → `0.21.1000` funciona sem padding nem rollover (verificado).
   O `__release_date__` (CalVer) é independente e continua sendo a data.

   A versão vive em `hermes_cli/__init__.py` (`__version__` **e**
   `__release_date__`), `pyproject.toml`, `apps/desktop/package.json` e nos
   arquivos do bootstrap-installer (`package.json`, `tauri.conf.json`,
   `Cargo.toml`); `update_version_files` cuida de todos.

   **Nunca deixe o metadado pip para trás.** Em install editable
   (`pip install -e .`), `importlib.metadata.version("hermes-agent")` continua
   reportando o valor antigo até rodar `pip install -e . --no-deps` novamente. E
   o agente lê a própria versão por esse caminho em quatro lugares:
   `agent/transports/codex_app_server_session.py`,
   `gateway/platforms/qqbot/utils.py`, `gateway/platforms/api_server.py` e
   `hermes_cli/plugins_manifest.py`. Já aconteceu (19/09/2026): código em
   `0.21.4`, dist-info em `0.21.3`, `egg-info` em `0.21.2` — o `haos --version`
   mostrava o certo e o resto do sistema, não. Um `git pull` sozinho não
   atualiza o metadado; ele é regenerado só pelo `pip install`.

7. **Os assets do appliance seguem o `haos update` desde 19/09/2026.** O `haos-edge`
   (binário Rust, vendorizado em `distro/.../usr/local/bin/haos-edge`) e os scripts
   que o instalador copia para `HAOS_HOME/scripts/` **não** vivem na árvore Python,
   então o pull não os alcançava — só `scripts/install_haos.sh` os escrevia. O
   sintoma é silencioso: o lado Python fica atual, o appliance *parece* atualizado,
   e `haos status` / `haos team` seguem rodando código velho. Aconteceu
   (19/09/2026): o `haos-edge` instalado estava **6 commits atrás** do fonte,
   faltando a autenticação de sessão persistente do WebUI e uma correção do
   `haos doc search`; o `haos_memory_populate.py` do `HAOS_HOME` também estava
   atrás. Os dois só foram corrigidos à mão. Agora a fase de manutenção pós-update
   chama `sync_appliance_assets` (`hermes_cli/update_cmd_assets.py`), que copia só
   o que difere e é no-op em install upstream/container (nunca cria o diretório
   `scripts/`, que é do instalador).

   **Armadilha ao rodar `haos update`:** o wrapper resolve o home por `$HOME`
   (`HAOS_HOME="${HAOS_HOME:-$USER_HOME/.haos}"`). De um shell cujo `HOME` não é
   `/root` — sessão de harness, cron com ambiente limpo — ele troca o código mas
   **não reinicia o gateway**: enumera perfis a partir de outro home, não acha
   serviço nenhum e reporta `Running Hermes services: none detected` enquanto os
   três `haos-*.service` estão ativos. O update "dá certo" e o serviço segue no
   código velho. Rode com `HOME=/root` (ou `HAOS_HOME=/root/.haos` explícito) e
   confirme com `haos update --plan` antes de confiar no restart — o plan lista o
   pid e o `code_sha` de cada gateway que será reiniciado.

8. **O Memory Fabric só passou a existir de fato em 19/09/2026.** Antes disso o
   subsistema era código completo e inalcançável: `MemoryMigrator` tinha `plan()` e
   `backfill_obsidian()` e **nenhum chamador fora dos próprios testes**, o journal
   canônico nunca tinha sido criado, e `plugins.enabled` listava só `dsh-bridge` e
   `memory-recall`. A memória real do appliance vivia nos stores do dono
   (`haos_memory_populate.py`, `dream_distill.py`), não no journal.

   Três coisas destravam isso, e as três importam:

   - **`memory.provider: hermes_fabric` é o que ativa o provider.** `plugin.yaml` +
     `register(ctx)` **não** bastam: `ctx.register_memory_provider()` é inerte por
     desenho — o docstring diz "Activation is owned by `plugins/memory` via
     `memory.provider`". É `load_memory_provider(name)` →
     `_load_provider_from_dir()` ("`register(ctx)` first, else a top-level
     subclass") que constrói o provider de verdade. Habilitar em
     `plugins.enabled` só registra um provider morto.
   - **`haos memory migrate` faz o backfill** (`hermes_cli/subcommands/memory.py`,
     handler em `main_agent_cmds.py`). Dry-run por padrão; `--apply` escreve.
     Primeira execução real: 29 notas lidas, 11 decisões, 1 duplicata
     (`curadoria/2026-09-13.md`), **28 importadas**, 112 projeções replayed.
   - **O comando vive no grupo `memory` do wrapper, não no `haos haos`.** Registrar
     em `hermes_cli/haos_cmd.py` deixa o comando alcançável por
     `haos haos memory migrate` e **quebrado** no nome que alguém digita:
     `haos memory: 'migrate' is not a \`haos memory\` command`. `dream`, `log` e
     `revert` já moram nesse grupo; `migrate` é da mesma família.

   **Rollback** (nesta ordem, se o fabric precisar sair):
   1. `haos config unset memory.provider` — volta o provider para o built-in.
   2. `haos gateway restart` — o gateway só relê o provider no boot.
   3. O journal fica em `HAOS_HOME/memory/fabric.db`; apagá-lo (com `-wal`/`-shm`)
      devolve o estado "sem fabric". Nada fora dele foi movido: o vault, o
      `decisions.db`, o `vectors.db` e o GraphRAG continuam onde estavam.
   4. Backup do estado pré-cutover: `/root/haos-backup-memory-<timestamp>/`
      (`config.yaml`, `obsidian_vault/`, `memory/`).

   **Controle do cutover sem editar código:** `MemoryFeatureFlags.from_config()`
   lê `memory.fabric.cutover` do `config.yaml` e depois overrides
   `HAOS_MEMORY_*` (aceita também os nomes do origin: `CANONICAL_READS`,
   `DURABLE_PROJECTIONS`, `HYBRID_RETRIEVAL`, `VECTOR_RETRIEVAL`). Os estágios são
   um **prefixo**: o primeiro desligado derruba os seguintes, e marcar um estágio
   posterior como ligado com um anterior desligado é erro, não algo que a cascata
   resolve em silêncio. Config malformada loga ERROR e mantém os defaults, porque
   o fabric é construído no registro do plugin e derrubar o startup do agente por
   um typo de YAML seria pior.

   **Referência completa do subsistema** — as quatro projeções e o papel de cada uma,
   os dois orçamentos de recall, o teto de ingestão, a semântica do sync, cutover
   como declaração vs evidência, comandos de verificação e lacunas conhecidas:
   `docs/haos/MEMORY_FABRIC.md`.

### 5.1 Armadilha: artefatos ignorados do distro travam o build Python (já pago em sangue)

Sintoma: `uv sync`, `uv lock` ou qualquer `pip install -e .` **não termina** —
fica minutos (medido: >23 min) queimando CPU em
`setuptools.build_meta.get_requires_for_build_editable`.

Causa (medida, não suposta): o `pyproject.toml` usa descoberta por varredura
(`packages.find`). Em `setuptools/discovery.py`, `PEP420PackageFinder._looks_like_package`
devolve `True` para **todo** diretório (sem poda) e o walk roda com
`followlinks=True`. Os artefatos **git-ignorados** do distro contêm symlinks
**absolutos** que saem do repo:

- `distro/haos-linux/cache/bootstrap/var/run -> /run`
- `distro/haos-linux/cache/bootstrap/dev/fd -> /proc/self/fd`

Com isso a descoberta atravessa o filesystem inteiro e volta ao próprio repo,
recursivamente. Números do diagnóstico: `find -type d` conta **5.864** diretórios
reais, enquanto o walk visitou **368.351** em 25 s e seguia subindo; removidos os
artefatos, `find_namespace_packages` caiu de **>360 s sem terminar** para
**0,09 s** (227 pacotes, `hermes.platform` incluso).

Correção: apagar `distro/haos-linux/cache/` e `distro/haos-linux/chroot/` — os
dois são git-ignorados e regenerados (`chroot/` pelo `iso-from-vm.sh`, que faz
`rm -rf` + rsync quando ele não existe; `cache/` é resto de debootstrap manual).
Diagnóstico rápido — a assinatura da armadilha é symlink de diretório com alvo
**absoluto** (ou seja, que sai do repo):

```bash
find . -path ./.git -prune -o -type l -xtype d -print | while read l; do
  t=$(readlink "$l"); case "$t" in /*) echo "$l -> $t";; esac
done
```

Ambiente saudável não imprime nada (os únicos links legítimos, `.venv/lib64`, são
relativos).

**Não use `uv run` neste repo para diagnosticar:** `uv run` re-sincroniza o `.venv`
com as dependências default e **remove o pytest** (que vem de `--extra dev`),
quebrando a suíte logo depois. Use `.venv/bin/python` direto — e nunca rode nada que
mexa no venv enquanto uma suíte está em execução (foi assim que uma medição de
2.353 testes saiu no lugar de 9.066 e virou resultado inválido).

O CI **não** é afetado: os dois diretórios são git-ignorados, então um checkout
limpo nunca os tem.

### 5.2 Armadilha: `PLAYWRIGHT_BROWSERS_PATH` obsoleto quebra o browser em silêncio

Sintoma: a tool `browser`, o stealth do `haos-fetch` e o `google_meet` falham
("Executable doesn't exist") enquanto `haos doctor` segue mostrando
`✓ Playwright Chromium (browser engine)`.

Causa medida (2026-09-12): o `haos-gateway.service` e o wrapper `haos` exportavam
`PLAYWRIGHT_BROWSERS_PATH=/opt/haos/.playwright` — diretório que **nunca existiu**
(o Chromium assado vive em `/home/haos/.cache/ms-playwright`, 656 MB). Uma vez
setada, a variável manda o Playwright resolver o browser **só** sob aquele
caminho, mas o predicado de presença do doctor (`_chromium_search_roots`) também
varre o cache default e por isso dava verde. Prova, com o stack Node que o
agent-browser usa:

```bash
PW=$(ls -d /home/haos/.npm/_npx/*/node_modules/playwright-core | head -n 1)
node -e "console.log(require('$PW').chromium.executablePath())"
# sem a variável  -> /home/haos/.cache/ms-playwright/chromium-1234/... (existe)
# com o valor antigo -> /opt/haos/.playwright/chromium-1234/...      (não existe)
```

Correção: a variável saiu do service (o `HOME` do nó já é o certo) e no wrapper
`haos`/`haos-fetch` só é passada quando `${HOME}/.cache/ms-playwright` existe. O
doctor passou a acusar o estado obsoleto:
`⚠ PLAYWRIGHT_BROWSERS_PATH=<path> has no Chromium build`.

**Regra:** nenhum unit/wrapper pode setar `PLAYWRIGHT_BROWSERS_PATH` para um
caminho sem Chromium. Se precisar apontar, aponte para o cache assado — ou não
sete nada.

### 5.3 Divergência de provisionamento: `haos-fetch` só existia no caminho pip/venv

A árvore do distro entregava 6 wrappers (`haos`, `haos-dns`, `haos-edge`,
`haos-hostname-set`, `haos-setup`, `haos-storage-init`) e o `install_haos.sh`
entregava 5, incluindo `haos-fetch` — então a VM instalada pela ISO **não tinha**
`haos-fetch` nem Scrapling (`command not found`). O extra `fetch` **não** é um
esquecimento do `[all]`: a política de 2026-05-12 manda backend opt-in viver em
`LAZY_DEPS` e resolver no primeiro uso — o que faltava era a entrada
`LAZY_DEPS["fetch.scrapling"]` (adicionada em 2026-09-12), o wrapper no distro e
o pin exato igual nos dois lugares (`scrapling[fetchers]==0.4.15`).

Verificação E2E (o caminho que importa): remover o Scrapling do venv e chamar o
wrapper — ele se recupera sozinho no primeiro uso:

```bash
uv pip uninstall --python /opt/haos/venv/bin/python scrapling
haos-fetch https://example.com --text   # lazy-install + fetch OK
```

## 6. Estado validado da VM (golden — 10/09/2026)

- Hostname **`haosagent`**: garantido pelo oneshot `haos-hostname.service`
  (first boot, stamp `/etc/haos/.hostname-set`) — o d-i live renomeia para
  `debian` no fim da instalação mesmo com o `/etc/hostname` certo no squashfs.
- Kernel **`6.18.15+deb13-amd64`** (trixie-backports, LTS até dez/2028)
  instalado e bootado na VM. ISO-clássica = 6.12.107; ISO-da-VM = 6.18.15 real.
- **Playwright Chromium assado**: `~/.cache/ms-playwright/` (chromium-1234 +
  headless-shell + ffmpeg, ~656 MB). Doctor: `✓ Playwright Chromium (browser
  engine)`, tool `browser` disponível offline. `PLAYWRIGHT_BROWSERS_PATH` **não**
  é setado por unit/wrapper desde 12/09/2026 (§5.2) — o launch resolve o cache
  default, e o stealth do `haos-fetch` foi validado de ponta a ponta.
- **`haos-fetch` + Scrapling**: wrapper em `/usr/local/bin/haos-fetch` (distro) e
  `scrapling 0.4.15` + `playwright 1.62.0` + `patchright 1.62.3` + `curl-cffi
  0.16.3` no venv (§5.3). `haos-fetch <url>` funciona via `http` e via `stealth`.
- Serviços `active`: `haos-gateway`, `haos-mesh` (127.0.0.1:9120), `haos-edge`
  (WebUI 127.0.0.1:8788), `haos-dns`,
  `haos-antigravity`. Venv `/opt/haos/venv`: Python 3.13.5, 92 pacotes
  (`.dist-info`; inclui o backend do fetch), SQLite **3.53.4** (FTS5/RTREE) no
  caminho real do agente (`state.db`).
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
- **RESOLVIDO (11/09/2026)** `haos-edge`: o unit rodava sem subcomando (cai em
  `cmd_status()`, exit 0, loop); fix = `ExecStart=haos-edge server --port 8788
  --host 127.0.0.1 --static-dir /opt/haos/hermes/platform/webui/static`,
  `User=haos`, `HAOS_HOME=/home/haos/.haos`, `HAOS_DATA_DIR=/var/lib/haos/edge`.
  Validado com reboot (active, 0 restarts, /health healthy).
- **Autenticacao do WebUI (RESOLVIDO 11/09/2026)**: o control plane exige sessao.
  Senha do operador PBKDF2-HMAC-SHA256 (200k) em `$HAOS_DATA_DIR/webui.passwd`
  (0600) via `haos-edge admin set-password`; login em `GET /login` +
  `POST /api/login` gera cookie HttpOnly `haos_session` (arquivo 0600 em
  `sessions/`, dir 0700); `remember=true` = Max-Age 30 dias ("salvar login neste
  dispositivo", a senha nunca vai para o navegador). Publico: /health, /static,
  /login, /api/login; resto = 401 (/api/*) ou 302 -> /login (SPA). Sem senha
  definida: 503 fail-closed. Bind 127.0.0.1 (sem TLS -> exposicao na rede exige
  proxy reverso). O scrub da ISO remove webui.passwd/sessions/locks e a
  validacao do squashfs acusa PRESENTE-BUG se vazarem.
- **Vault canonico (RESOLVIDO 11/09/2026)**: o unico caminho valido e
  `<HAOS_HOME>/obsidian_vault` (lido por context_expand_tool, dashboard plugin e
  context/memory/provider). `tools/haos_memory_tools.py` apontava para
  `<HAOS_HOME>/vault` -> `obsidian_get_adr` respondia "vault nao disponivel"
  (adapter fail-closed exige .md) e `obsidian_save_note` gravava nota orfa num
  diretorio fantasma (falso sucesso). Corrigido + teste de contrato
  `tests/tools/test_haos_memory_tools_vault.py` (vermelho no codigo antigo).
  Divergencia conhecida que NAO foi alterada: `hermes/platform/memory/dream.py`
  grava ADRs em `memory/vault/adrs` (subsistema proprio, com git store).
- **`haos-edge status` honesto (11/09/2026)**: as linhas "Memory Scopes
  (Operational/Active/Enforced)" eram hardcoded e o check de RAG usava caminho
  fixo; agora reporta contagens reais (sessoes/mensagens/tarefas/chunks/notas/
  entidades/OKF/reconciliadas) dos caminhos canonicos.
- **Rotina periódica de memória (RESOLVIDO 11/09/2026)**: não existia NENHUM job
  periódico populando a memória (o `scripts/haos_nightly_maintenance.sh` existia
  mas tinha `WORKSPACE_ROOT` hardcoded da máquina de dev, usava `.venv` e abortava
  no appliance pelo `set -e`; nunca foi agendado). Agora:
  - `scripts/haos_memory_populate.py` popula os três stores canônicos de uma vez:
    DeepDoc/RAG (`memory/ragflow.db`), GraphRAG (`memory/graphrag.db`) e dream
    (`memory/reconciled_memories.db` + lições OKF). Idempotente.
  - `scripts/haos_nightly_maintenance.sh` roda integridade dos SQLite + essa
    população; resolve o tree do agente (`/opt/haos` ou `$HAOS_AGENT_DIR`) em vez
    de fixar caminho; AST do código é opt-in (`HAOS_MAINTENANCE_AST=1`).
  - Agendado no **cron nativo** do HAOS (`haos cron list`, aba Scheduler da
    WebUI): job `SYSTEM - cron: manutencao noturna`, `0 3 * * *`, `--no-agent --script`.
    O `haos-storage-init` (boot, antes do gateway) instala o script em
    `~/.haos/scripts/` e recria o job se faltar — o cron REJEITA caminho
    absoluto, o script tem de viver sob `<HERMES_HOME>/scripts/`.
  - Pitfall de caminho: `get_process_hermes_home()` = `HERMES_HOME` → `HAOS_HOME`
    → default **`~/.haos`** (o fork mudou o default de `~/.hermes` para que um
    processo SEM env — cron externo, python cru, unidade systemd genérica —
    nunca grave num store órfão). O populador ainda fixa o env resolvido antes de
    importar o runtime. `~/.hermes` fica só como origem de MIGRAÇÃO
    (o `install_haos.sh` herda `.env`/`config.yaml` de lá).
    Dívida cosmética conhecida: ~400 strings user-facing herdadas do upstream
    ainda dizem `~/.hermes/...`; o certo é passar por `display_hermes_home()`.
    **O que NÃO é cosmético (corrigido em 11/09/2026):** 28 sites construíam
    path de verdade com `Path.home()/".hermes"` (defaults reais: `main.py`,
    `_startup_fast`, `dashboard_procs`, `worktree_gc`, `self_repo_guard`,
    `workspace_scope` — boundary de segurança —, `file_safety`, `nous_rate_guard`,
    `mcp_tool_config`, `codex_app_server`, `a2a/protocol`, `photon/auth`,
    `bot_mode_dm/probe`, `bot_relay`, `mem0`/`openviking`, `telegram`,
    `google_chat` e scripts de dev). Todos passaram a resolver pelo home canônico.
    Guard: `python scripts/ci/check_legacy_hermes_home.py` (AST, não grep) roda no
    workflow do fork `.github/workflows/haos-guards.yml` e falha em default NOVO.
    Site legado intencional (cadeia de candidatos, detecção de migração, guarda do
    store real, `PROJECT_SKILLS_SUBDIRS`) leva `# haos-legacy-path: <motivo>` na
    linha — hoje são 10, e o motivo é obrigatório.
  - **Atalho de compatibilidade `~/.hermes` → `~/.haos` (11/09/2026)**: criado
    pelo `haos-storage-init` (ISO) e pelo `install_haos.sh` + wrapper `haos`
    (fora da ISO) para que ferramenta/processo legado que ainda escreva em
    `~/.hermes` acerte o store canônico. Guardas: só é criado quando o home
    canônico está em uso (`$HOME/.haos`) e `~/.hermes` NÃO existe — nunca
    sobrescreve um store legado real nem aponta para um `HAOS_HOME` temporário.
    É o que cobre o `main_dashboard` (o cliente Desktop escreve em
    `$HOME/.hermes/desktop-ssh` por design, #69551) sem tocar no código dele.
  - **Precedência dos resolvedores (decisão de 11/09/2026): `HAOS_HOME` vence
    `HERMES_HOME`.** `HERMES_HOME` é alias de compatibilidade do fork; `HAOS_HOME`
    é a variável canônica. Antes, `get_process_hermes_home()` lia `HERMES_HOME`
    primeiro e `_get_platform_default_hermes_home()` lia `HAOS_HOME` primeiro —
    com os dois setados e divergentes, o MESMO processo respondia dois homes (um
    deles o store de outro produto) e `is_haos_environment()` derrubava o branding
    para `hermes`, porque vetava pelo alias antes de olhar `HAOS_HOME`. Agora os
    dois resolvedores concordam, `is_haos_environment()` checa `HAOS_HOME`
    primeiro, e um nó que seta só `HERMES_HOME` continua funcionando (os três
    cenários têm teste). Medição deste host: nenhum processo tinha os dois envs
    divergentes — a divergência real era ENTRE processos (container da GUI do
    harness com `HERMES_HOME=/root/.hermes` vs agentes HAOS em `/root/.haos`), o
    que é configuração de launcher e não se resolve no código.
  - **Critério de migração de `~/.hermes` (decisão de 11/09/2026: NÃO adotar o
    root legado).** O fork NÃO faz adoção automática do root `~/.hermes`, mesmo
    existindo a primitiva para subdiretórios (`_legacy_path_has_content()`, usada
    em `get_hermes_dir()`). O critério, medido neste host antes de decidir:
    `~/.hermes` pode ser (a) **store de OUTRO produto**, (b) store legado do
    upstream, ou (c) lixo. Não há como distinguir com segurança pelo conteúdo, e
    adotar o root errado é falha silenciosa de integridade — pior que uma mensagem
    que mente. Evidência deste host: `/root/.hermes` tem 2,1 GB e `state.db` de
    53 MB **em uso vivo** pelo container da GUI do harness DSH (o `mountinfo` do
    container monta `/@root/.hermes` em `/root/.hermes`, com
    `HERMES_API_URL=http://127.0.0.1:8642`), enquanto `/root/.haos` tem 2,2 GB e
    `state.db` de 32 MB. São dois produtos, dois stores — adoção automática faria
    o HAOS ler o store do harness.
    Regras que decorrem do critério:
    - `~/.haos` é o canônico; `~/.hermes` pertence a quem o criou.
    - O install herda apenas **configuração** (`.env`, `config.yaml`), nunca
      estado (`state.db`, `sessions/`, `memory/`, `cron/`, `skills/`).
    - O atalho `~/.hermes -> ~/.haos` só nasce com `~/.hermes` ausente; se ele
      existe, é store de alguém e fica intocado.
    - Adoção só é legítima quando `~/.haos` está **ausente ou vazio** E `~/.hermes`
      tem dados — e é decisão explícita do operador, não do código:
      `HAOS_HOME=~/.hermes haos doctor` (para inspecionar) ou migrar com o gateway
      parado (`systemctl stop haos-gateway haos-mesh`), nunca com os dois
      populados. Copiar por cima de um `~/.haos` populado mistura históricos de
      forma irreversível.
  - **Instalação fora da ISO (11/09/2026)**: `scripts/install_haos.sh` passou a
    provisionar a MESMA estrutura canônica que o `haos-storage-init` faz na ISO —
    dirs (`obsidian_vault/adrs`, `okf`, `memory`, `graphrag`, `scripts`, `cron`),
    seed do vault + CSV do GraphRAG (de `distro/.../etc/skel/.haos/`), rotina
    noturna instalada em `$HAOS_HOME/scripts/` com ponteiro `haos_agent_dir`
    (a venv/o populate vêm do tree do agente) e job cron agendado. A população
    inicial roda no install (best-effort). Sem isso o nó subia sem memória e sem
    rotina. O nightly, instalado fora do tree, acha tudo pelo ponteiro.
  - GraphRAG: `GraphRAGAdapter` é só memória; a persistência é o write-through
    `IncrementalGraphRAGUpdater(store=GraphRAGStore(...))`. Sem `store=` o
    `graphrag.db` nunca recebe entidades (o dashboard também não passa `store=`,
    então o grafo dele é só de tela).
  - **Sync imediata de nota (11/09/2026)**: `obsidian_save_note` agora espelha a
    nota na hora em DeepDoc + GraphRAG (via `hermes/platform/memory/
    haos_memory_sync.py`), então `haos-edge doc search` e `graphrag_query` veem a
    nota nova sem esperar o job noturno. A rotina noturna continua sendo a
    passada de reconciliação completa (reindexa o vault inteiro) + dream +
    integridade. Limitação: não há tool de EXCLUIR nota, então remoção manual de
    um `.md` deixa chunk/entidade órfãos até um reindex (o `index_directory` não
    limpa chunks de arquivos que sumiram).
- **`haos web` (WebUI Python, doc STANDALONE_WEBUI.md) — verificado em 12/09/2026**:
  é superfície separada e **não tem unit no appliance** (as units presentes são
  gateway/mesh/edge/dns/antigravity/storage-init/hostname), então não está exposta.
  **Não tem login por design**: o bind default é `127.0.0.1:8788` (medido com
  `ss -ltnp`: `local=127.0.0.1:8799`) e um bind não-loopback (`--host`/`HAOS_HOST`)
  recusa bind não-loopback com `ValueError` antes de criar o socket — `hermes/platform/webui/standalone.py`,
  verificado com `--host 10.255.255.1`: o aviso sai ANTES do bind, então nada é
  exposto durante o teste. `GET /api/state` responde 200 **sem credencial** (2862
  bytes de payload real), ou seja expor a porta é expor o PTY remoto. Acesso remoto
  autenticado continua sendo o daemon Rust `haos-edge` (login de operador).
- **`/opt/haos` na VM NÃO é clone do histórico do fork (12/09/2026)**: o HEAD lá é
  um import independente (`92708cf`, um commit único com a árvore inteira) e o
  working tree carrega ~651 arquivos diferentes dele. Consequências práticas:
  `git pull`/`--ff-only` **nunca** funciona na VM (não há ancestral comum útil), o
  deploy é sempre por arquivo (`tar` + `cp`) — que é justamente o que o método faz —
  e o `local <sha>` do `haos --version` se refere a esse import, não ao commit do
  fork. Não "conserte" isso com `git checkout`/`reset` no appliance: são 651 arquivos
  de estado local e o ganho é só cosmético.
- **`haos-dns` (Rust) — build e deploy (12/09/2026)**: o daemon não vem do pip; o
  binário sai de `cargo build --release` em `packages/haos-dns` (cargo 1.98 no host)
  e é instalado em `/usr/local/bin/haos-dns` (unit `haos-dns.service`, `ExecStart`
  sem args). Guarde o anterior antes de trocar (`sudo cp -a
  /usr/local/bin/haos-dns /usr/local/bin/haos-dns.bak-<data>`) — o resolver do nó
  depende dele. O transporte dos upstreams (UDP/DoT/DoH) vem de
  `/etc/haos/dns.toml`, cuja fonte de verdade é
  `distro/haos-linux/config/includes.chroot/etc/haos/dns.toml` (o rsync da ISO pega
  `/etc` por exclusão, então o arquivo viaja nos dois caminhos). Verificação:
  `sudo systemctl restart haos-dns`, consulta UDP para `127.0.0.1:53` e
  `getent hosts example.com`; para provar um transporte, deixe SÓ ele no config
  (só-DoT resolve, só-DoH resolve). **Cuidado com `pkill -f`**: o padrão casa com o
  próprio shell — use `pkill -x haos-dns`.
- `haos-edge` é o **servidor do Standalone WebUI** (axum/tokio): /api/terminal/*
  (PTY remoto), /api/tasks, /health, e o SPA (chat/terminal/taskboard/scheduler).
  Mesmo binário também é CLI (`status`, `team`, `doc search`, `doctor`) e shim
  para o agente Python em args desconhecidos.
- Daemons gateway/mesh nascem `failed/activating` antes do primeiro `haos-setup`
  (venv vazio — ovo e galinha).
- Doctor: `browser-cdp`/`browser-use` "system dependency not met" (deps npm
  opcionais, não bloqueiam).
- Advisories npm (12/09/2026): raiz e escopo `web` estavam com 1 high + 1 moderate
  (browserslist <=4.28.6 / baseline-browser-mapping <2.11.0). O bump de lockfile
  (browserslist 4.28.8, baseline-browser-mapping 2.11.20) zerou a raiz — medido na
  VM: `npm audit --workspaces=false` passou de `{moderate:1, high:1}` para `{}` — e
  o doctor agora imprime as três linhas verdes (`✓ … deps`). Restam apenas
  moderates de tooling de teste em `web` (4: vitest/@vitest/mocker, colord,
  sanitize-html) e `ui-tui` (2), que o doctor reporta como linha verde.
- A checagem "API key" do doctor passou a reconhecer `A6API_API_KEY` e `A6_API_KEY`
  (o plugin do distro declara a primeira; o instalador grava a segunda no `.env` e
  a aponta como `providers.a6api.key_env`) — na VM a linha virou
  `✓ API key or custom endpoint configured`.
- **Sites legado `~/.hermes` — revisados em 12/09/2026** (guarda
  `scripts/ci/check_legacy_hermes_home.py --list`): 10 ocorrências, todas
  intencionais e marcadas com `haos-legacy-path:` — detecção de migração
  (`diagnostics/doctor.py`), candidatos legado de leitura (`haos_delegation_bridge`,
  `webui/controlplane`, `webui/standalone`), guarda do store real do operador
  (`hermes_cli/auth.py`), baseline do remap sob sudo (`gateway.py`), contrato do
  Desktop client (`main_dashboard.py`, #69551) e o `.hermes/skills` **relativo ao
  projeto** (`agent/skill_utils.py`). Nenhuma é default novo de HOME.
- **`model.default` é obrigatório no config do nó (achado e corrigido em
  12/09/2026)**: o `haos-setup` do distro gravava só `model.provider`, então o nó
  ficava com `model: {provider: a6api}` e **sem default** — o agente monta a
  requisição com `"model": ""` e o provider responde
  `HTTP 400 请求参数不正确`. Sintoma: `haos -z "..."` falha na primeira chamada e o
  erro não diz o que falta. Prova: com `model.default: deepseek-v4-flash` o mesmo
  one-shot responde `OK`; o payload foi capturado interceptando `httpx` (49 KB,
  `"model": ""`). O `install_haos.sh` já gravava os dois — a divergência era só o
  caminho da imagem. Corrigido em `haos-setup` (`set_node_provider <provider>
  <model>` grava os dois) e no config do nó.
  - Sintaxe do CLI: `-m` vai **verbatim** para o payload, então `-m
    a6api/deepseek-v4-flash` envia o nome qualificado e também dá 400. Use
    `--provider a6api -m deepseek-v4-flash` (verificado: ambos os payloads com
    `model='deepseek-v4-flash'` e resposta `OK`).
  - Tarefas auxiliares (`auxiliary.*`, ex. `title_generation`) têm `model: ''` no
    default e não herdam o modelo principal: sem `-m`/default elas também levam
    `model: ""`. Com modelo resolvido, herdam (`payload-02` com
    `model='deepseek-v4-flash'`).

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