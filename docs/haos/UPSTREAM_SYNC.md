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

## Pendência futura (registrada)

- **Sync total (graft + replay)**: aguarda sessão dedicada com remedição das 36
  superfícies de conflito contra o upstream atual.
- **672 arquivos classe "ambos"**: revisão por arquivo (nossa mudança + upstream).
- **232 módulos novos do upstream**: entraram junto com o port do core (fase futura).
- **ISO**: bloqueado por ordem do dono — será o último passo, após todas as pendências,
  com autorização explícita. (Registrado em 19/09/2026; não criar.)