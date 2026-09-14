# Sincronização com o upstream (NousResearch/hermes-agent)

Plano e números medidos em 12/09/2026, **executado parcialmente em 19/09/2026** (ver
"Execução desta sessão" abaixo). O objetivo do documento é que a próxima sessão não
precise redescobrir o terreno, e que a execução parcial fique registrada com evidência.

## Fatos medidos

- **As histórias não têm ancestral comum.** O repo do fork nasceu de um import da
  árvore do upstream (raiz `4c6f7b6f20`, 09/09/2026) — não é um clone com histórico
  compartilhado. `git merge-base HEAD upstream/main` não devolve nada e
  `git merge-base --is-ancestor HEAD upstream/main` é falso. Consequência: `git merge`
  puro é recusado ("refusing to merge unrelated histories") e um graft por
  `git replace --graft` **não é honrado pelo merge** (testado: o merge continuou
  recusando e `merge-base` ficou vazio).
- **Remote**: `upstream` = https://github.com/NousResearch/hermes-agent.git
  (`git fetch --no-tags upstream main`, ~4 min). `upstream/main` em 19/09 = `b9271bcb34`.
- **Base de import identificada**: `5cffc57bc6` (07/09/2026) — pontuando cada commit
  do upstream na janela 05–08/09 por quantos arquivos têm blob idêntico à raiz do
  fork, o melhor candidato casa **11853 de 12428 arquivos (95,4%)**. Dois commits
  empatam no topo (`5cffc57bc6`, `40da71dbb4`), equivalentes para este fim.
- **Delta do upstream desde a base (12/09)**: 455 commits, 1179 arquivos, +64109/−7876.
- **Censo de conflito** (`git merge-tree --write-tree --merge-base=5cffc57bc6 HEAD upstream/main`,
  exit 1, 36 linhas `CONFLICT`): **36 arquivos**. Distribuição: `hermes_cli/` 19,
  `tools/` 3, `tests/` 3, `cron/` 3, `agent/` 3, `tui_gateway/` 2, `gateway/` 2,
  `toolsets.py` 1. A concentração em `hermes_cli/` é esperada: é onde vive a marca
  (hermes→haos).

Lista completa dos 36 (medição de 12/09):

```
agent/auxiliary_client.py            hermes_cli/runtime_provider.py
agent/chat_completion_helpers.py     hermes_cli/session_export_md.py
agent/turn_explainers.py             hermes_cli/setup.py
cron/jobs.py                         hermes_cli/subcommands/debug.py
cron/notepad.py                      hermes_cli/subcommands/plugins.py
cron/scheduler.py                    hermes_cli/tips.py
gateway/run_notifications.py         hermes_cli/update_cmd_fleet.py
gateway/run_turn.py                  tests/agent/test_compression_attempt_lifecycle.py
hermes_cli/auth_commands.py         tests/hermes_cli/test_cmd_update.py
hermes_cli/auth.py                   tests/hermes_cli/test_update_yes_flag.py
hermes_cli/backup.py                 tools/bot_mode_dm.py
hermes_cli/banner.py                 tools/computer_use/schema.py
hermes_cli/commands_platforms.py     tools/tts_tool.py
hermes_cli/config.py                 toolsets.py
hermes_cli/debug.py                  tui_gateway/methods_session.py
hermes_cli/doctor_state.py           tui_gateway/server.py
hermes_cli/__init__.py               hermes_cli/_parser.py
hermes_cli/inventory.py              hermes_cli/plugins_cmd.py
```

## Execução desta sessão (19/09/2026) — alinhamento de conteúdo

### Fatia da reconciliação (medido nesta sessão, árvores atuais)

| Classe | Contagem | Destino |
|---|---|---|
| Só-nosso (ausentes no upstream: tools `haos_*`, `serve_haos.sh`, skills `haos-*`, camada platform) | 195 | **permanecem locais** |
| Só-upstream (presentes nas duas, divergentes, nunca tocados) | 635 | core revertido (abaixo); conteúdo mantido |
| Ambos (tocamos E o upstream mudou) | 672 | **lista de revisão por arquivo** (não sobrescritos) |
| Novos do upstream (ausentes do nosso tree) | 232 | **não adotados** (decisão de arquitetura) |

### Por que o core NÃO foi adotado (evidência de cadeia)

Adotar os 635 quebrou a importabilidade por cadeia: `model_tools.py` novo importa
`hermes_state_ids` (módulo novo, dos 232) que exige `openrouter_variant_base` do
`hermes_constants` **novo** — e `hermes_constants.py` é arquivo nosso com fixes
(classe "ambos"). Evidência: `ModuleNotFoundError: hermes_state_ids` → após adotar,
`ImportError: openrouter_variant_base` → próxima dependência. **Conclusão:** adotar o
core do upstream arrasta a arquitetura nova dele; é um port, não um sync — reforça o
"revisar, não aplicar cego" do plano original (a estratégia graft+replay resolve isso
por construção: o diff do raiz do fork é reaplicado e revisado commit a commit).

### Entregue: 140 arquivos de conteúdo adotados (branch `sync-upstream`)

- `optional-skills/` (creative, software-development, etc.) e `skills/`: evolução do
  conteúdo de skills do upstream.
- `evals/`: harnesses novos/evoluídos (desktop_bug_campaign, tool_search,
  toolperf_abeval, token_accounting, provider_wire/fallback, gateway_failure_ownership,
  desktop_mcp_oauth, codex_echo, browser_use...).
- `docs/`: evolução de conteúdo.
- Colisões resolvidas: `reddit-reading` e `rss-feeds` existiam em `skills/` E
  `optional-skills/` após a adoção (violação `unique_names` do auditor P3) —
  colapsadas para a colocação do upstream (apenas `optional-skills/`).
- Árbitro: suíte v9 sobre o tree final do sync (ver certificação abaixo). Auditor P3
  `PASS` (213 skills, 0 violações). Smoke de import do core: OK.

### Commits (branch `sync-upstream`, sem push)

```
3d7f3bcd04 sync(upstream): fase 2c - remove os 90 novos do core que restavam
ba97e42c10 sync(upstream): fase 2b - remove arremedos do core que o rm anterior nao pegou
b74be716f7 sync(upstream): fase 2 - reverte o core entrelacado, mantem apenas o conteudo
987606b464 sync(upstream): fase 1b - adota modulos novos exigidos + colapsa duplicatas
d3280faae4 sync(upstream): fase 1 - adota 635 arquivos que o upstream evoluiu (revertidos no core)
```

## Execução do sync TOTAL (graft + replay) — 20/09/2026

Remedição contra o upstream atual e execução completa da estratégia graft + replay. Branch
`haos-upstream-sync`; `main`/`haos-standalone` intactos até o portão final.

### Números medidos

| Medida | Valor |
|---|---|
| `upstream/main` remedido | `3f86ed75da` |
| Base de import | `5cffc57bc6` |
| Commit do graft (tag `graft-base`) | `06b52e9ab6` = `3f86ed75da` **+ o delta do fork aplicado** (575 arquivos diferem do upstream puro, `git diff --stat 06b52e9ab6 3f86ed75da`) |
| Commits do fork desde a raiz (`4c6f7b6f20..362abab470`) | 150 |
| Commits replayados sobre o graft (`06b52e9ab6..5a36830ded`) | **149** |
| Delta do fork vs graft | 1436 arquivos |
| Delta do fork vs tip do fork | 3296 arquivos |
| Mudanças da sessão (working tree vs `5a36830ded`) | 761 entradas (440 M + 321 A) |

O commit que não aparece no replay é `sync(upstream): fase 1b — adota modulos novos exigidos
pelos 635 e colapsa duplicatas de skill`: o conteúdo dele já está no graft (o upstream adotou
os mesmos módulos), então o replay o resolveu como vazio e o `--empty=drop` o descartou.
Nenhum outro commit do fork ficou de fora (`comm` dos subjects: 1 linha, conferida).

### As quatro classes de falha que o replay não resolve sozinho

1. **Revert (`043d35aa58`)** — a fase 2 da sessão de 19/09 devolveu o core ao estado da *base
   certificada do fork*, que para centenas de arquivos era um snapshot **anterior** ao
   upstream atual. O graft (upstream de hoje) é quem manda no conteúdo; o teste veio do graft
   e cobra o símbolo novo. Discriminador usado por arquivo:
   `git hash-object <f>` vs `git rev-parse 043d35aa58:<f>` (revert) e `06b52e9ab6:<f>` (graft).
   Arquivos restaurados do graft nesta sessão: 223 puros + 90 da varredura + os alvos dos
   agentes — entre eles `agent/{system_prompt,display,session_persistence,context_compressor}.py`,
   `tui_gateway/methods_session.py`, `hermes_cli/{commands_completion,env_loader}.py`,
   `plugins/memory/mem0/_oss_providers.py`, `plugins/model-providers/deepseek/__init__.py`.
   **Cuidado medido:** `blob == 043d35aa58` NÃO prova "sem delta do fork" — aquele commit
   preservou a marca em vários arquivos (`hermes_cli/tools_config.py` é byte-idêntico a ele e
   contém `haos`/`product_command`). Sempre grepar `haos|product_command` antes de restaurar.
2. **Perda do graft** — o graft já traz o delta do fork, mas o replay só aplica os 150 diffs
   do fork. Arquivos que vieram do **import raiz** (`4c6f7b6f20`) e nunca foram tocados por
   nenhum desses commits não existem em nenhum dos dois lados: somem silenciosamente.
   Medição: **761 entradas do tip do fork ausentes**; 572 eram renomeações do próprio upstream
   (`tests/cli`→`tests/hermes_cli`, `tests/run_agent`→`tests/agent`, `tests/state`→
   `tests/hermes_state`, `tests/acp`→`tests/acp_adapter`; 279 blobs byte-idênticos ao destino);
   189 fork-only de verdade + 64 referenciados pelo código atual. Correção:
   `git checkout 362abab470 -- <paths>` em 253 arquivos (35 hooks do live-build em `distro/`,
   101 testes, docs, scripts, apps/web/ui-tui). Dos 134 testes restaurados, **133 eram testes
   antigos do upstream que o upstream aposentou/renomeou** (basename inexistente no upstream
   atual, nenhum import de módulo fork-only) → revertidos; 1 era do fork
   (`test_plugin_index_search.py`) → removido junto com o índice (item 3).
3. **Aposentadoria pelo upstream** — `hermes_cli/plugin_index.py` (índice comunitário de
   plugins) foi **deletado** pelo upstream em `46ab5aa365 fix(catalog): remove the parallel
   community plugin index — the catalog is the only discovery system`. A árvore merged ainda
   carregava a maquinaria órfã (sem chamador) de um snapshot antigo. Alinhado com o upstream:
   removidos o módulo, o seed `hermes_cli/data/plugin_index.json`, o teste e as três funções
   órfãs em `hermes_cli/plugins_cmd.py` (`_looks_like_bare_index_name`, `_resolve_index_name`,
   `cmd_search` por índice). `haos plugins search` opera agora só pelo catálogo curado.
4. **Duplicatas de arquivos que o upstream MOVEU** — 12 arquivos TS restaurados por engano
   (`lib/fuzzy`, `lib/model-search-text`, `lib/reconnect-backoff`, `lib/format`,
   `e2e/mock-server` em `apps/desktop`, `web/`, `ui-tui/`) são snapshots anteriores à
   consolidação em `apps/shared/`; nada na árvore os importa. Revertidos.

### Decisões de união (fork preservado + upstream adotado)

- **`cron.restart_safe_scope`**: política do fork preservada (`require` por padrão;
  `prefer` no appliance via `distro/.../haos-setup`), mas a degradação passou a seguir o
  upstream — subprocesso **externo** com o handoff #101940 (o fork degradava para in-process,
  que bloqueia o gateway e também morre no restart).
- **`mcp_gateway`** (toolset fork-only): as 3 tools dele já estão em `_HERMES_CORE_TOOLS`, então o
  *nome* do toolset não precisa ser reinjetado numa lista salva — declarado em
  `_NEVER_RECOVERED_TOOLSETS` (`hermes_cli/tools_config.py`) para a recuperação de toolset nativo
  de plataforma não o recuperar. A tentativa anterior (declará-lo em `_RECENTLY_SHIPPED_TOOLSETS`)
  estava **errada**: aquele conjunto é vazio no estado estável e alimenta os testes decorados com
  `_requires_recently_shipped` em `tests/hermes_cli/test_tools_config.py`, que passam a rodar e
  falhar (o opt-out/checkbox travado não se aplica a um toolset fora de `CONFIGURABLE_TOOLSETS`).
- **`agent/turn_explainers.py` + `gateway/run_notifications.py`**: templates do upstream
  (`{cli}{profile_arg}doctor`) com o branding resolvido em runtime (`product_cli_name()`);
  o fork tinha perdido o `{profile_arg}` e deixado um `.replace()` morto.
- **`plugins/memory/mem0/_oss_providers.py`**: `_default_qdrant_path()` restaurado como fonte
  única, preservando a resolução lazy por profile que o upstream introduziu.
- **`hermes_cli/plugins_cmd.py`**: conteúdo do fork mantido (24 marcas; importa
  `plugin_index` **e** `plugins_cmd_catalog`) — só a maquinaria do índice foi removida.
- **`tests/plugins/memory/test_hindsight_provider.py`**: alvo do `monkeypatch` repontado para
  `spawn_context_thread` (costura que o upstream mantém); intenção do teste intacta.

### Guardas do fork (todos exit 0 no estado final)

`scripts/ci/check_haos_brand_hints.py` (passou a varrer também os `*.py` da raiz — 47 hints
user-facing escapavam, incluindo `haos doctor` imprimindo a string antiga do upstream),
`scripts/ci/check_legacy_hermes_home.py`, `scripts/check_compat_pointers.py`,
`scripts/ci/check_profile_archive_boundary.py`. Nenhum marcador de conflito na árvore.

### Certificação da árvore final (suíte completa)

| Medida | Baseline do fork | Árvore sincronizada |
|---|---|---|
| Arquivos | 3901 | **4266** |
| Testes passando | 46386 | **49754** |
| Falhando | 26 | **4** (→ **2** isolados) |
| Skipped | 422 | 434 |
| Tempo | 2236.6s | 2712.2s |

As 4 falhas da rodada cheia são explicadas e **nenhuma é regressão do sync**:

- `tests/tools/test_fuzzy_match.py::TestContextAwareCorrectness::test_no_match_on_large_file_is_fast` e
  `tests/agent/test_shell_hooks_tree_kill.py::test_interrupt_kills_hook_and_propagates` — **flakes de
  carga** (o segundo dá 5s para o hook subir antes de enviar `SIGINT` a si mesmo; o arquivo levou 303s
  com 16 workers em 8 CPUs). Ambos passam com a máquina ociosa: `3 files, 66 tests passed, 2 failed`.
- `tests/hermes_cli/test_local_quickstart.py` ×2 — **ambiente do host, não o sync**: o POST
  `/api/local-models/quickstart` devolve 409 "No automatic recommendation" porque o probe lê
  `usable_vram = 2 GiB` (GTX 1050 Ti de 4 GiB menos o `_MARGIN_FLOOR`) e nenhuma entrada do catálogo
  fica `zero_spill=False`. `hermes_cli/local_runtime/{hardware,catalog,estimator}.py` são
  byte-idênticos ao upstream ⇒ `upstream/main` falha igual nesta máquina; forçar
  `_nvidia_vram → None` (a condição do CI) faz o POST devolver 200.

### Classes de falha que a certificação expôs (77 falhas na 1ª medição → 2 ambientais)

1. **Revert (`043d35aa58`)** — o commit reverteu 569 arquivos para snapshots **antigos** do upstream.
   Discriminador por arquivo: `git log --find-object=$(git hash-object <f>) 3f86ed75da` — se o blob
   existe no histórico do upstream, **não há delta do fork** e adotar o conteúdo do upstream é seguro;
   havendo delta, é união. Corrigidos assim: `hermes_cli/config.py::validate_config_structure`
   (delegação a `config_load_issue`, que classifica `HomeInitializationError`/`OSError` como storage
   indisponível **com o caminho** — 11 testes), `hermes_cli/banner.py::_check_via_local_git`
   (sonda API-only de #104591, com `network=True` e `_last_target_rev` — 6 testes),
   `warn_unpinned_cron_jobs_after_model_config_change` (#7e4d02fef5), o schema
   `hermes.shared_metrics.v2.schema.json` (`"acp"` no enum, #5a1246f830),
   `plugins/model-providers/vertex/__init__.py` (`supports_model_listing=False`),
   `scripts/install.sh` + `scripts/lib/node-bootstrap.sh` (gate de `xz`, #11197; reuso de Python
   suportado, #10778) e `scripts/desktop-update/posix.sh` (canonicalização `readlink -m`; perfil do
   shim alocado com `mktemp -d`) — 8 testes. Mais 17 arquivos com **zero delta do fork** adotados
   direto do upstream (6 `AGENTS.md`, `scripts/run_tests.sh`, `dev-sandbox.sh`, os scripts de update
   do Windows, `scripts/whatsapp-bridge/*`, READMEs de plugins).
2. **Híbrido de merge** — `hermes_cli/banner.py::build_welcome_banner`: o replay enxertou o bloco do
   MEIO da função do upstream dentro da função **do fork**, sem o prólogo que inicializa
   `right_lines`/`left_lines`/`_skills_enabled`/`mcp_status`/`dim`/`text`. A primeira instrução que
   referencia um nome inexistente estoura (`UnboundLocalError`) antes de qualquer `console.print`,
   matando 7 testes na mesma linha (4 de `test_banner.py` + 3 de `test_banner_skills_width.py`, que
   eram colaterais puros — a matemática de largura já estava correta). **O graft carrega o mesmo
   híbrido nesta função**, então ali ele não é alvo confiável: o conserto foi religar na dashboard
   HAOS as duas partes que são comportamento (aviso de update e rota do free tier/"no model
   configured") e restaurar o hero do skin no header.
3. **Contrato do fork vs teste do upstream** — o teste é do upstream e prende um comportamento que o
   fork tinha divergido: `cli.py` (preload default de skill abortava a execução num perfil sem
   `skills/` instalado: um default **implícito** agora resolve contra o que existe, enquanto um
   `-s <skill>` explícito continua falhando alto), `cron/scheduler.py` (o guard de drift lia o
   **fallback de auth** como drift da config global e bloqueava a cadeia `fallback_providers` que o
   operador configurou), `hermes_cli/tools_config.py` (`mcp_gateway` em `_NEVER_RECOVERED_TOOLSETS`),
   `hermes_cli/gateway.py` (`_is_bare_unit_pinned_home(home)` como predicado, sem materializar `Path`
   a partir de um `SimpleNamespace` do teste) e `hermes_constants.py::get_default_hermes_root`
   (ancorar também no root legado `~/.hermes`, já aceito por `_is_hermes_profiles_root`, além do
   `~/.haos` do fork).
4. **Duplicata obsoleta** — os 133 testes restaurados do tip do fork eram cópias do layout **antigo**
   que o upstream moveu (`tests/test_tui_gateway_server.py` → `tests/tui_gateway/`, `tests/run_agent/`
   → `tests/agent/`, `tests/test_toolsets.py` → `tests/tools/`, ...): revertidos. Também removidos 5
   diretórios órfãos (`tests/{acp,cli,platform,relay,run_agent,state}` menos `platform`, que é a suíte
   do fork para `hermes/platform/` e passou a ser declarada em `_NON_MIRROR_DIRS`), e
   `tests/agent/test_moa_cold_start_cache_66793.py` renomeado (sem número de issue no nome, citado na
   docstring, como o próprio teste de layout exige).

### Estado final vs o tip do fork

499 entradas ausentes, todas por decisão explícita: **497 testes** (aposentados/renomeados pelo
upstream) + **2 arquivos do índice** (aposentados pelo upstream). Nada mais se perdeu.

## Procedimento (estratégia escolhida: graft + replay) — para o sync TOTAL

1. `git checkout -b haos-upstream-sync upstream/main`.
2. Aplicar as mudanças do **commit raiz do fork** (o diff dele contra a base):
   `git diff 5cffc57bc6 4c6f7b6f20 > /tmp/fork-root.patch` e `git apply -3`. **Revisar,
   não aplicar cego**: esse diff carrega tanto as edições de marca do fork quanto o
   que o fork removeu do upstream no import. Reimpor a remoção é o que o fork quer,
   mas cada remoção precisa ser olhada. Commit único: "graft: fork root onto upstream".
3. Replay dos commits do fork (era 79 em 12/09; hoje são 143):
   `git rebase --onto haos-upstream-sync 4c6f7b6f20 haos-standalone`.
   Resolver os conflitos da lista acima (remediir: upstream avançou desde `cbe9e5b294`).
4. Portões de qualidade antes de qualquer coisa tocar `main`:
   `scripts/run_tests.sh` (suíte completa, não só os diretórios tocados) e E2E no
   appliance: `haos doctor`, cron agendado (`source=builtin`), DNS (DoT/DoH), gateway.
5. Só então `git branch -f main haos-upstream-sync` + push em lockstep.

## Riscos e ressalvas

- O censo de 36 é de um merge 3-way. Um replay commit a commit pode tocar o mesmo
  arquivo mais de uma vez — o que ele mede é a superfície de arquivos, não o esforço.
- `cron/scheduler.py` conflita por dois motivos independentes: a marca do fork e o
  commit do upstream `c17629a0a2 fix(cron): scope restart-safe worker environment`,
  que **não** substitui a correção local do scope (`cron.restart_safe_scope`,
  commit `6f5ee79af4`).
- O appliance roda um import independente em `/opt/haos` (ver DEV_WORKFLOW_VM.md);
  deploy continua sendo por arquivo.
- Rollback: o trabalho vive em branch dedicada; `main` e `haos-standalone` não são
  tocados até o passo 5. Nenhum passo deste plano mexe na ISO.

## Deploy do sync (host + VM) — o que quebrou e por quê

Deploy feito em `1da580047c` (branch `haos-standalone` e `main`, ambos forçados: as histórias
são não relacionadas com a do fork antigo, então `pull --ff-only` não serve — é
`fetch` + `reset --hard`).

1. **Push recusado pelo GitHub (`remote unpack failed: index-pack failed`, e depois
   `remote: fatal: did not receive expected object <sha>`).** Causa-raiz: o clone de trabalho
   era **shallow** (`.git/shallow` com 10 boundaries de 20/08 a 09/09) — o pack enviado era
   incompleto e o `index-pack` do servidor não tinha as bases. O objeto que o servidor dizia
   faltar nem existia localmente, que é a assinatura do problema. Correção:
   `git fetch --unshallow upstream`. O push de 538 MB / 31944 commits entrou de uma vez depois
   disso (o host e a VM também estavam shallow e precisaram do mesmo tratamento).
2. **`haos-gateway` em loop de restart na VM** (`restart counter is at 116`) com
   `ModuleNotFoundError: No module named 'hermes_state_ids'`. Causa-raiz: a árvore tem módulos
   na RAIZ que o código de pacote importa no topo (`agent/conversation_compression.py` importa
   `hermes_state_ids`), e a unit lançava `python /opt/haos/gateway/run.py` — o que deixa
   `sys.path[0]` em `/opt/haos/gateway`. O reparo de `sys.path` do próprio `gateway/run.py`
   (linha ~425) roda muito depois desses imports. O host não é afetado porque sua unit usa
   `-m hermes_cli.main gateway run` e `hermes_cli/main.py:40` insere a raiz no `sys.path`.
   Correção: `Environment="PYTHONPATH=/opt/haos"` no template
   (`distro/haos-linux/.../haos-gateway.service`) e na unit instalada. A forma documentada no
   docstring do run.py (`python -m gateway.run`) já funcionava pelo mesmo motivo (`-m` põe o
   CWD, que é o `WorkingDirectory`, no `sys.path`).
3. **WIP não commitado encontrado no host** (`/usr/local/lib/haos-agent`): guarda `LoadState`
   em `gateway/shutdown_forensics.py`, `register_files()` em
   `hermes/platform/memory/memory_governance.py` + `_register_canonical_write()` em
   `tools/haos_memory_tools.py` (+ teste) e o servidor STT. Resgatado para o repo como commit
   próprio (`9e272ae126`) por merge 3-way sobre a árvore sincronizada — nunca sobrescrevendo o
   upstream. A unit `haos-stt.service` do host passou a apontar para
   `scripts/haos_stt_server.py` (arquivo versionado) e o arquivo solto na raiz foi removido.
4. **Extração do tar na VM rodava como root**: 9430 arquivos ficaram root-owned, o que impedia
   o usuário `haos` de escrever na árvore (e quebrou `git checkout`/`reset`). Corrigido com
   `chown -R haos:haos /opt/haos`; num próximo deploy, extrair como o usuário do serviço (ou
   `tar --no-same-owner`) evita o problema.
5. **Arquivos untracked em `/opt/haos` — investigados, NÃO é trabalho perdido** (ver a seção
   "WIP da VM" abaixo): os plugins `model-providers/a6api/`, `model-providers/antigravity/` e
   `wrapper-antigravity/` são o payload da ISO que vive no repo em
   `distro/haos-linux/config/includes.chroot/opt/haos/...`; o deploy apenas os copiou para o
   layout de runtime do appliance.

Verificação final: `haos doctor` rc=0 na árvore local; VM com `haos-gateway`, `haos-edge`,
`haos-dns` e `haos-mesh` ativos; job nativo `SYSTEM - cron: manutencao noturna` presente com
execuções `source=builtin`; ticker do cron com heartbeat; DNS do nó resolvendo; host e VM no
mesmo commit — **E2E PASS=8 FAIL=0** (igual ao baseline).

### Auditoria de perda contra o tip do fork (após a certificação)

`git ls-tree -r 362abab470` vs `HEAD` deu 645 arquivos ausentes. Discriminadores, em ordem:
**blob idêntico em outro caminho** (440 — só mudaram de lugar), **nome normalizado igual sem os
números de issue** (196 — o upstream renomeou), e o resto revisado à mão. Sobraram 9 candidatos:

| Candidato | Veredito |
|---|---|
| `tests/cron/test_cron_drift_alert_once.py` (3 testes) | **recuperado** — o guard de drift existe e alerta uma vez por job; o upstream cobre o formato alert-once em `test_preflight_config.py`, mas não o ramo de drift |
| `tests/hermes_cli/test_model_picker_scroll.py` (8 testes) | **recuperado** — é o viewport do picker em curses; o `test_model_picker_viewport.py` do upstream é o do prompt_toolkit (arquivos distintos, ambos existiam no fork) |
| `tests/test_process_loop_event_loop_warning.py` (5 testes) | **recuperado** — cobre o `get_running_loop()` documentado em `cli.py:1643`; nenhum teste do upstream cita #19285 |
| `tests/gateway/test_max_tokens_propagation.py` | descartado — **obsoleto**: o upstream removeu de propósito os caps de geração impostos pelo usuário (`tests/gateway/test_output_caps_removed.py`) e o teste assere o comportamento antigo |
| `hermes_cli/plugin_index.py`, `hermes_cli/data/plugin_index.json`, `tests/hermes_cli/test_plugin_index_search.py` | descartados — índice comunitário aposentado pelo upstream (`46ab5aa365`) |
| `tests/install/install-update-e2e.sh` | descartado — aposentado pelo upstream; a função vive em `tests/scripts/install/` + `tests/scripts/desktop_update/` |
| `tests/test_minisweagent_path.py` | descartado — placeholder vazio de propósito ("minisweagent_path.py was removed — see PR #2804") |

Verificação: 4 arquivos, 18 testes passando (inclui `tests/test_tests_tree_layout.py`), e
`tests/cron/` + `tests/gateway/test_output_caps_removed.py` = 111 arquivos, 1315 passando, 0
falhando.

### Segunda certificação (estado final) e o que ela pegou

Suíte completa na árvore final: **4269 arquivos, 49771 testes passando, 5 falhando, 434 skipped**
em 2226.4s. As 5 falhas: 3 são flakes de carga que **passam com a máquina ociosa**
(`test_shell_hooks_tree_kill`, `test_gateway_shutdown`, `test_session_db_recovery` — 31 testes,
0 falhando em isolamento) e 2 são as ambientais do quickstart (GPU pequena). A quinta era **real
e minha**: `tests/scripts/test_windows_footguns_full_repo_scan.py` reprovou porque o
`scripts/haos_stt_server.py` resgatado do host fazia `yaml.safe_load(open(...))` **sem
`encoding=`** — o default da plataforma é cp1252/mbcs no Windows, então um `config.yaml` com
acento voltaria como mojibake. Corrigido com bloco `with` + `encoding="utf-8"` (e `config.yaml`
ausente devolve `{}` em vez de estourar no primeiro request). Checker limpo: 1587 arquivos, 0
footguns; serviço `haos-stt` reiniciado e respondendo.

### WIP da VM que aparece como untracked (investigado)

Os plugins `model-providers/a6api/`, `model-providers/antigravity/` e `wrapper-antigravity/` são o
**payload da ISO** que vive no repo em `distro/haos-linux/config/includes.chroot/opt/haos/...`
(presentes no tip do fork E na árvore sincronizada) — o deploy os copiou para o layout de runtime
do appliance, e é por isso que aparecem como untracked. Os que realmente não existem em nenhum
branch (`tests/e2e/restart_safe_scope_smoke.py`, `tests/e2e/test_restart_safe_scope_smoke.py`,
`skel/seed-haos/`) foram verificados com `git log --all` (vazio para esses caminhos) e ficaram
preservados no appliance.

## Segunda passada de sync (3f86ed75da → 5eb99eb284, 249 commits)

O upstream avançou 249 commits depois da primeira passada. Desta vez não houve graft: o
`git merge-base` entre o HEAD do fork e o upstream novo é **exatamente `3f86ed75da`** (o replay da
primeira passada deixou a árvore descendente), então a operação é um **merge de 3 vias normal**
(num branch dedicado `haos-sync-2`, mergido em `main`/`haos-standalone` só depois da certificação).

Recon: 449 arquivos tocados pelo upstream (24315 inserções, 1525 remoções — **nenhuma remoção de
arquivo**), superfície de conflito (arquivos tocados pelos dois lados) = **102**, dos quais apenas
**5 com conflito textual** — o `git merge-tree --write-tree` previu exatamente os mesmos 5.

### Resolução dos 5 conflitos (todos por UNIÃO, nunca um lado só)

| Arquivo | Resolução |
|---|---|
| `cron/scheduler.py` (import) | mantém `DRIFT_SKIP_MARKER`/`DRIFT_SKIP_SILENT_MARKER` do fork + `_empty_requested_mcp_toolsets` do upstream |
| `hermes_cli/gateway_migrate.py` | adota o opt-out `auto_migration_opted_out` (novo; o `auto_migration_blockers` já era usado no corpo) mantendo `haos update` na docstring |
| `hermes_cli/profile_channels.py` | adota a docstring de inventário por OWNERSHIP (superset do upstream) com `haos gateway migrate --multiplex` |
| `hermes_cli/webhook.py` | adota `_route_url(name, subs[name])` (correção de rota/assinatura) mantendo `product_command("webhook")` |
| `hermes_cli/worktree_cmd.py` (2 hunks) | docstring nova (`--json`/`--older-than`) com `haos worktree`; guard `if records:` + seção `external` do upstream com `product_command('worktree')` |

### O que a certificação pegou e corrigiu

1. **Branding**: o guard `check_haos_brand_hints.py` reprovou (exit 1) com **14 violações**, todas
   em linhas novas do upstream citando o CLI `hermes`. Corrigidas: 12 literais `haos <cmd>` em
   docstrings/comentários e 3 strings user-facing convertidas para `product_command(...)`
   (`gateway_migrate.py`, `webhook.py`, `google_chat/oauth.py` — este último restaurado para a
   forma do fork). Guard limpo depois.
2. **Preservação do delta do fork**: auditoria por linha (para cada um dos 449 arquivos do merge,
   cada linha que o fork adicionou sobre `3f86ed75da` deve continuar existindo) → **0 perdas**;
   os únicos 2 "ausentes" eram falsos positivos (rewrap do import de `cron/scheduler.py` e a
   docstring antiga de `worktree_cmd.py`, substituída de propósito).
3. **Estática**: F811 (redefinição) 8 antes = 8 depois; F821 (nome indefinido) 1371 → 1369, nenhum
   novo (os "indefinidos" dos `methods_*.py` são o padrão `__getattr__` de compat, pré-existente).
4. **Falha real de doc**: o teste NOVO do upstream `test_slash_commands_doc_parity.py` exige que
   todo comando registrado apareça em `website/docs/reference/slash-commands.md` — o `/haos` do
   fork não tinha linha. Adicionada a linha na tabela Tools & Skills (teste 3/3 passando). De
   quebra, a referência órfã `:func:`reset_profile`` (função removida pelo próprio upstream,
   docstring ficou para trás) agora aponta para `reset_hermes_home_override`.
5. **Aposentadorias do upstream**: nenhuma remoção de arquivo no delta novo; dos 37 símbolos
   removidos nível-definição, o único "órfão" era a docstring acima.

### Certificação e deploy

- Suíte completa na árvore mesclada: **4318 arquivos, 50341 testes passando, 5 falhando, 434
  skipped em 2252.4s** (49 arquivos / 570 testes a mais que na primeira certificação). As 5
  falhas: a de doc (item 4, **corrigida**) + 2 flakes de carga que passam ociosos
  (`test_shell_hooks_tree_kill`, `test_gateway_shutdown` — 26 testes, 0 falhando em isolamento) +
  2 ambientais do quickstart (mesmo `409 Conflict` do env, `usable_vram = 2 GiB` nesta GPU;
  o código de `local_runtime/` agora é o upstream puro, então a classificação ambiental ficou
  mais forte, não mais fraca).
- Deploy: host `/usr/local/lib/haos-agent` e VM `/opt/haos` em **`a2f38d2d72`** (VM foi parada
  pelo dono no meio da passada e depois reiniciada — o E2E no final já rodou com os dois lados no
  commit novo). Unit `haos-gateway` no appliance faz `PYTHONPATH=/opt/haos` (fix da 1ª passada)
  e continua funcionando — desta vez não houve crash loop na VM.
- **E2E: PASS=8 FAIL=0** (doctor rc=0; 4 serviços ativos na VM; job `SYSTEM - cron: manutencao
  noturna` com 5 execuções `source=builtin`; ticker com heartbeat; DNS resolvendo; host e VM no
  mesmo commit; 7 arquivos untracked da VM preservados).

## Terceira passada de sync (5eb99eb284 → 1ad89ac018, 140 commits)

O upstream avançou de novo (140 commits, 424 arquivos, 61645 inserções, 8537 remoções) e o
merge-base continuou sendo exatamente o merge anterior (`5eb99eb284`) — de novo merge de 3 vias
normal, numa branch `haos-sync-3`. Superfície de conflito: **59 arquivos tocados pelos dois
lados** (58 auto-mergeados + **7 com conflito textual**, os mesmos 7 que o `merge-tree` previu).

### Resolução dos 7 conflitos + 1 deleção (todos por união)

| Arquivo | Resolução |
|---|---|
| `hermes_cli/config_migrations.py` | marca do fork (`product_command("curator")`) + passo novo `(45, _migrate_to_45)` do upstream |
| `hermes_cli/doctor_state.py` | lógica nova do upstream (holder scan + `_exclusive_repair_db_guard` + `_SKIP`) com o branding restaurado nas 3 mensagens user-facing |
| `hermes_cli/web_server_config.py` | mantém `"model": "general"` (fork) + `"connections": "agent"` (upstream) |
| `hermes_state_dbfile.py` | união dos imports (`product_command` + `canonical_sqlite_path`) |
| `tests/agent/test_compression_stall_fallback.py` | **lado do upstream** (idle 0.05 << teto 2.0): o upstream resolveu o mesmo flake que o fork mitigava com constantes, então a mitigação local deixou de ser necessária |
| `ui-tui/src/components/branding.tsx` (2 hunks) | null-safety `(info.model ?? '')` do upstream + marca `HAOS Engineering` do fork |
| `tools/setup_mcp_tool.py` (UD) | **aposentadoria aceita**: o upstream removeu a ferramenta (o `manage_connections` cobre MCP local) e mantém o replay shim `_setup_mcp_shim`; o delta do fork ali era só branding de strings que deixaram de existir |

### O que a certificação pegou e corrigiu

1. **Branding**: 9 violações novas dos 140 commits. 8 eram literais em docstrings/comentários
   (`gateway_windows.py` ×3, `update_cmd_windows.py` ×2, `quiet_single_query.py` ×2,
   `cron/incidents.py`) → `haos <cmd>`. A nona (`tools/connectors/mcp.py`) era um hint
   user-facing numa **constante de módulo**: virou a função `unavailable_hint()`, porque
   `product_command()` resolve o nome pelo ambiente em tempo de chamada e uma constante
   congelaria a marca no import. Guard exit 0.
2. **Preservação do delta do fork (linha a linha)**: 758 arquivos com linhas adicionadas pelo
   fork conferidos, 756 preservados integralmente e 2 "ausências" que são as resoluções
   deliberadas desta passada (o `_SKIP` do upstream carrega a mesma marca do fork; e o teste de
   compressão, onde adotamos o lado do upstream). **0 perdas.**
3. **Nível de arquivo (ponto cego fechado)**: a auditoria de linhas pulava em silêncio arquivos
   que o merge deletava (lia o working tree, caía em `OSError` e seguia). Reconciliado à parte:
   **24 arquivos do pré-merge ausentes = exatamente as 24 deleções reais do upstream** (13
   deleções + 11 origens de rename, com detecção de rename desligada). Todos os 11 destinos de
   rename existem e são byte-idênticos ao upstream (`tools/tool_gateway/*` →
   `tools/connectors/gateway/*`, `model_tools_connectors.py` → `tools/connectors/dispatch.py`,
   `tools/connector_search.py` → `tools/connectors/search.py`). Das 24, **só o
   `setup_mcp_tool.py` tinha delta do fork** — o UD tratado acima.
4. **Estática**: F811 89 antes = 89 depois; F821 com **0 arquivos suspeitos** (53 arquivos com
   F821 no merged = exatamente os 53 do pré-merge; o upstream puro tem 43 — os 10 a mais são
   arquivos do próprio fork, padrão `bind_module(globals(), server)` pré-existente). Duplicatas
   AST no mesmo escopo: 28 = 28. Os 4 guards (`brand_hints`, `legacy_hermes_home`,
   `compat_pointers`, `profile_archive_boundary`) exit 0.
5. **Versão e migração**: `pyproject.toml` 0.21.2 → **0.21.3** (bump do upstream veio junto) e
   `_config_version` 44 → **45** com `_migrate_to_45` definido (linha 546) e registrado (linha
   712) — a união do conflito era justamente essa entrada.

### Certificação e deploy

- Suíte completa: **4340 arquivos, 50511 testes passando, 6 falhando, 472 skipped em 2379.9s**
  (22 arquivos / 170 testes a mais que a 2ª passada). Triagem das 6: **4 são flakes de carga** e
  passam isolados com a máquina ociosa (`test_shell_hooks_tree_kill`, `test_session_db_recovery`
  — 20.6s sob carga vs 1.6s isolado —, `test_profiles_sidebar_cache`,
  `test_refresh_singleflight`: 37 testes, 0 falhando) e **2 são ambientais** do quickstart
  (mesmo `409 == 200`, `usable_vram = 2 GiB` nesta GPU). Nenhuma falha real na árvore mesclada.
- Deploy: host `/usr/local/lib/haos-agent` e VM `/opt/haos` em **`8483f728ca`**, serviços
  reiniciados dos dois lados (host 3/3, VM 4/4), 0 units falhadas na VM e os 7 untracked
  preservados.
- **E2E: PASS=8 FAIL=0** (doctor rc=0; 4 serviços ativos na VM; job `SYSTEM - cron: manutencao
  noturna` com 5 execuções `source=builtin`; ticker com heartbeat; DNS resolvendo; host e VM no
  mesmo commit).
- Nota de host (não é regressão do sync): `pool-rank.service` e `haos-nightly-maintenance.service`
  aparecem falhadas na máquina. O `pool-rank` roda de `/run/media/.../hermes/pool-rank/`
  (fora deste repositório) e já falhava **44 vezes em 3 dias** antes deste deploy.

## Quarta passada de sync (1ad89ac018 → 40f2702b22, 7 commits)

Passada curta e limpa, executada sob autorização explícita do dono com guardrails (branch
dedicada + rollback, snapshot do lab antes de mutar, ISO intocada, testes alvo, parada/escalada
em conflito não resolvível, mutação irreversível, risco fora do lab, falha real de teste ou
mudança de prioridade).

- Escopo: **27 arquivos**, +507/−26, **4 arquivos tocados pelos dois lados**, **0 deleções e 0
  renames**, **0 conflitos** (o `merge-tree --write-tree` deu exit 0 e o merge real confirmou).
- Conteúdo do upstream: font picker do desktop (nova chave `desktop.font_family` em
  `config_defaults.py`), `manage_connections` ausente para contas não habilitadas no portal,
  guard de plugin (confirmação de capacidades JS ambíguas) e doc do instalador macOS.
- Merge commit **`36f6ae26d8`** (pais `a71f089d24` + `40f2702b22`), feito na branch dedicada
  `haos-sync-4` (preservada) e só então fast-forwarded em `main`/`haos-standalone`.
- Rollback: tag `rollback-pre-sync4` (local **e** no remoto) + branch `haos-rollback-pre-sync4`,
  ambas em `a71f089d24`.
- **Lab**: não existia nenhum snapshot (`virsh snapshot-list` vazio) — criado
  `pre-sync4-40f2702b22` (checkpoint com memória, 20:05:48) **antes** de qualquer mutação; host e
  VM estavam em `a71f089d24` e foram para `36f6ae26d8`.

### Auditorias desta passada

| Verificação | Resultado |
|---|---|
| Guard de branding | exit 0 (0 violações novas) |
| Marca do fork nos 4 arquivos dos dois lados | intacta (46/46, 9/9, 1/1, 0/0 linhas) |
| Delta do fork (linha a linha) | **760/760 arquivos preservados, 0 linhas ausentes** |
| Arquivos ausentes / novos | 0 ausentes; 3 novos (font picker do desktop) |
| Estática F811 / F821 | 89 = 89 / 2475 = 2475 (idêntico ao pré-merge) |
| 4 guards (`brand_hints`, `legacy_hermes_home`, `compat_pointers`, `profile_archive_boundary`) | exit 0 |
| ISO | **não tocada** (0 arquivos do upstream em `distro/`) |
| Testes alvo | **23 arquivos, 508 testes, 0 falhas** |
| E2E | **PASS=8 FAIL=0** com host e VM em `36f6ae26d8` |

**Lacuna registrada (honestidade de escopo):** nesta passada rodamos **testes alvo**, não a suíte
completa (a baseline certificada é a da 3ª passada: 50511 passando). Os arquivos `.tsx` do font
picker do desktop não têm cobertura pytest por regra do próprio repositório (asserções de JS
pertencem ao vitest) e o vitest não roda aqui porque `apps/desktop/node_modules` está ausente —
então a mudança de UI do desktop entrou **sem teste executado neste lab**.

## Quinta passada de sync (40f2702b22 → bb1d255a77, 5 commits)

Passada de higiene dos scanners, autorizada pelo dono com os mesmos guardrails da anterior
(snapshot recente reutilizável, branch/rollback preservados, ISO intocada, dry-run, guards,
testes alvo, deploy só com todos os gates verdes).

- Escopo: **4 arquivos** (`tools/plugin_guard.py`, `tools/skills_guard.py` e seus dois testes),
  +181/−4, **0 deleções, 0 renames, 0 conflitos** (dry-run `merge-tree` exit 0, confirmado no
  merge real), **0 arquivos em `distro/`** (ISO intocada).
- Conteúdo: corta falsos positivos dos scanners de instalação — comentários de linha e
  changelogs deixam de ser pontuados como crítico inapelável, achados defensivos são
  contextualizados, e em prosa de doc (`.md/.txt/.rst/.html`) `agent_config_mod` e
  `hardcoded_secret` caem de critical para high (em código de runtime seguem critical). Inclui
  o bump `plugin-guard` v2 → v3 e `skills-guard` v3 → v4.
- Merge commit **`ba103ed1f1`** (pais `4c41cb2ca3` + `bb1d255a77`) na branch dedicada
  `haos-sync-5` (preservada), depois fast-forward em `main`/`haos-standalone`.
- Rollback: tag `rollback-pre-sync5` (local **e** remoto) + branch `haos-rollback-pre-sync5` em
  `4c41cb2ca3`. Conjunto de snapshots do lab: `pre-sync4-40f2702b22` (20:05) e
  `pre-sync5-bb1d255a77` (20:44), ambos checkpoints com memória criados **antes** das mutações.

| Verificação | Resultado |
|---|---|
| Dry-run antes de mutar | exit 0, 0 conflitos |
| Guard de branding | exit 0 |
| Marca do fork nos 2 scanners | intacta (1→1 em `plugin_guard.py`; `skills_guard.py` sem linha de marca antes e depois) |
| Delta do fork (linha a linha) | **760/760 preservados, 0 linhas ausentes** |
| Arquivos ausentes / novos | 0 / 0 |
| Estática F811 / F821 | 89 = 89 / 2475 = 2475 |
| 4 guards | exit 0 |
| Testes alvo | **7 arquivos, 220 testes, 0 falhas** |
| E2E | **PASS=8 FAIL=0** com host e VM em `ba103ed1f1` |

### Efeito medido nos nossos plugins (por que esta passada não muda nada aqui)

O scanner foi executado sobre os 15 plugins do fork e o payload da ISO, antes e depois:

- **antes** (v2): 15 permitidos, 5 pedindo confirmação — google_meet 29 findings,
  hermes-achievements 8, memory 46, platforms 89, security-guidance 5;
- **depois** (v3/v4): **idêntico** — 15 permitidos, os mesmos 5 `caution` com as mesmas contagens.

Nenhum veredicto muda, nenhum plugin nosso é bloqueado (os 5 são `caution` = requer
confirmação, nunca deny duro), e o gate não roda no provisionamento do appliance (o
`distro/` só copia o payload; `should_allow_plugin_install` é chamado apenas no fluxo
interativo `haos plugins install`). Não há cache de veredicto no appliance para invalidar.

**Nota de rigor:** uma comparação isolada anterior usou o scanner do tip (`1a990f3062`), onde
`tools/skills_guard.py` já tem +119 linhas posteriores. `tools/plugin_guard.py` é idêntico
entre o alvo e o tip (sha256 `cd345703da05...`), então a diferença de 1 finding em
`plugins/memory` vem de commit **fora** desta passada — não dela.

## Pendência futura (registrada)

- **Sync total**: 1ª passada (graft + replay), 2ª (`3f86ed75da` → `5eb99eb284`), 3ª
  (`5eb99eb284` → `1ad89ac018`, 140 commits), **4ª** (`1ad89ac018` → `40f2702b22`, 7 commits) e
  **5ª** (`40f2702b22` → `bb1d255a77`, 5 commits) **executadas e certificadas** (ver as seções
  acima). Método estabelecido: merge de 3 vias direto enquanto o merge-base for o merge
  anterior. **O upstream segue avançando rápido**: ao fim da 5ª passada o tip era `1a990f3062`,
  **31 commits à frente** do alvo autorizado — próxima passada pendente de autorização.
- **672 arquivos classe "ambos"**: revisão por arquivo (nossa mudança + upstream).
- **232 módulos novos do upstream**: entraram junto com o port do core (fase futura).
- **WIP local da VM**: investigado e resolvido — era o payload da ISO em `distro/` (ver a seção
  "WIP da VM que aparece como untracked"); nada a resgatar.
- **ISO**: bloqueado por ordem do dono — será o último passo, após todas as pendências,
  com autorização explícita. (Registrado em 19/09/2026; não criar.)