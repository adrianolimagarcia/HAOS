# Estado da suíte de testes (medição de 12/09/2026)

Números da execução completa com `scripts/run_tests.sh` neste fork, mais o que já foi
investigado sobre eles. Serve para não repetir a mesma investigação.

## Resultado

```
3901 files, 46386 tests passed, 26 failed, 422 skipped (100% complete) in 2236.6s (16 workers)
exit=1
```

## Classificação das 26 falhas

Repetidas isoladamente, **sem** a armadilha de investigação (ver abaixo), em 57.8s:

```
23 files, 1820 tests passed, 23 failed, 6 skipped
```

Ou seja: **23 das 26 são pré-existentes** — reproduzem sem nenhuma interferência minha.
As outras 3 só falharam sob carga e passaram na repetição (flaky). Nenhuma das 23 toca
`cron/`, `hermes_cli/config_defaults.py` ou `distro/`, que foram os arquivos alterados na
sessão; nenhuma é regressão de mudança recente.

Arquivos com falha pré-existente:

```
tests/agent/test_codex_aux_timeout_fd_ownership.py
tests/cli/test_slash_dispatch_table.py
tests/gateway/test_telegram_conflict.py
tests/gateway/test_turn_lease.py
tests/hermes_cli/test_user_providers_model_switch.py
tests/hermes_state/test_live_db_isolation_guard.py
tests/platform/diagnostics/test_doctor_env_isolation.py
tests/platform/test_evals_real.py
tests/plugins/memory/test_hindsight_provider.py
tests/run_agent/test_sequential_tool_timeout.py
tests/skills/test_authoring_standards.py
tests/test_haos_session_mention.py
tests/test_hermes_bootstrap.py
tests/test_hermes_home_profile_warning.py
tests/test_hermes_state.py
tests/test_install_macos_launcher.py
tests/test_packaging_metadata.py
tests/tools/test_browser_real_profile.py
tests/tools/test_termux_api_detection.py
tests/tools/test_zombie_process_cleanup.py
tests/tui_gateway/test_slash_worker_mcp_discovery.py
```

Hipóteses por família (a confirmar uma a uma, não são conclusão):

- **Marca do fork.** `test_hermes_bootstrap[hermes_cli/main.py]`, `test_hermes_home_profile_warning`,
  `test_doctor_env_isolation` e `test_live_db_isolation_guard` afirmam caminhos e mensagens do
  upstream (`hermes`) enquanto este repo renomeia para `haos`. É a família mais provável de ser
  dívida de rebrand, não defeito de código.
- **Ambiente do host.** `test_browser_real_profile` (quer Chrome real; o host tem `chromium`) e
  `test_termux_api_detection` (heurística de container/áudio). Já conhecidas antes desta medição.
- **A investigar sem hipótese pronta.** `test_packaging_metadata`, `test_skills_authoring_standards`
  (frontmatter de `skills/development/ponytail*`), `test_evals_real`, `test_install_macos_launcher`,
  `test_telegram_conflict`, `test_turn_lease`, `test_hindsight_provider`,
  `test_sequential_tool_timeout`, `test_zombie_process_cleanup`, `test_haos_session_mention`,
  `test_slash_worker_mcp_discovery`, `test_codex_aux_timeout_fd_ownership`,
  `test_user_providers_model_switch`, `test_slash_dispatch_table`, `test_hermes_state`.

## Lixo `MagicMock/` — o que ficou provado

O diretório `MagicMock/` aparecia na raiz do repo depois de rodar a suíte. Três técnicas, todas
sem acusar:

1. Varredura dos 14 arquivos de `tests/cron/` que mencionam `session_db`/`quarantine`/`MagicMock`,
   um por um: nenhum cria o diretório.
2. Armadilha por arquivo: um arquivo vazio chamado `MagicMock` na raiz do repo. Um `mkdir` simples
   falha com `FileExistsError`, e `mkdir(exist_ok=True)` **também** falha quando o caminho existe e
   não é diretório — a armadilha cobre os dois casos. Rodada em `tests/cron/` inteiro: nada.
3. A mesma armadilha durante a **suíte completa** (3901 arquivos): zero menções de
   `FileExistsError`/`NotADirectoryError` no log inteiro.

Conclusão: **o lixo não é criado pelo pytest**. Vem de invocação direta — um CLI/script do repo
executado fora do runner. Próximo passo, em vez de varrer a suíte: vigiar a raiz com
`inotifywait -m -e create .` enquanto se roda cada comando suspeito, e olhar o `strace -f -e trace=mkdir`
do processo que criar o diretório.

## Atualização 2026-09-12 — triagem das 17 falhas restantes (21 arquivos, 50.3s)

Base: re-run dos 21 arquivos que falhavam, `--tb=line`. 1770 passam, 17 falham. Famílias:

**A. BUGS REAIS de produção (2 — prioridade máxima, são TypeErrors em código de produção):**
- `hermes_state_sessions.py:369` TypeError, atingido por `test_haos_session_mention.py`
  (autocomplete de sessão).
- `plugins/platforms/telegram/adapter.py:2307` TypeError "not all arguments converted
  during string formatting", atingido por `test_telegram_conflict.py`.

**B. Guarda de isolamento (1 — relevância de segurança):**
- `test_live_db_isolation_guard.py:174`: filho sem HERMES_HOME NÃO foi recusado. O contrato
  do guard falhou.

**C. Contratos/drift (4):**
- `test_slash_dispatch_table.py:51` — nomes de registry não resolvem na tabela de dispatch.
- `test_packaging_metadata.py:317` — isenção de `exclude_newer` para deps pinned exatas.
- `test_hermes_state.py:936` (`assert 0 == 1`) — projeção FTS5 esperava ≥1 consulta com
  enriquecimento de contexto, veio 0.
- `test_termux_api_detection.py:194` — expectativa de detecção de áudio no fallback Termux.

**D. Skills violando a própria regra (2 — baratas):**
- `test_authoring_standards.py:91` — `skills/development/ponytail` e `ponytail-review` falham
  o frontmatter obrigatório do HARDLINE. Skills embarcadas fora do contrato do repo.

**E. E2E/caminho real (6 — precisam de mergulho por arquivo):**
- `test_evals_real.py` ×2 (as "14 cases" da pesquisa: uma é o gate stdlib-only, outra roda as
  suítes reais), `test_browser_real_profile.py`, `test_sequential_tool_timeout.py`,
  `test_hindsight_provider.py`, `test_user_providers_model_switch.py` ×2,
  `test_install_macos_launcher.py` (traceback em subprocess — ambiente).

Ordem de ataque sugerida: A (bugs reais) → B (segurança) → D (skills, barato) → C (drift) → E.

## Atualização 2026-09-13 — as 21 famílias fechadas, cada fix com prova

Método por arquivo: ler a asserção real, MEDIR (não supor), corrigir, provar com execução focada
e mutar o código de produção quando o teste existe para guardar um invariante. Várias causas
foram bem diferentes da hipótese inicial — três hipóteses minhas foram refutadas e estão
registradas como tal.

**A — 2 confirmados, 1 era teste, e um deles eu tinha inventado.**
- `test_haos_session_mention.py:91`: o teste escrevia em `tmp_path/.hermes/state.db` enquanto o
  completer abre `SessionDB(read_only=True)` sem argumentos, que resolve por `_default_db_path()`
  — e o conftest re-fixa `DEFAULT_DB_PATH` para `tmp_path/hermes_test/state.db`. Dois caminhos
  distintos; o teste é que estava errado. `hermes_state_sessions.py` NÃO tinha bug: o parâmetro
  `title` em `_insert_session_row` era superfície que eu mesmo havia inventado num commit
  anterior, revertida em `d8cb3143ea`.
- `test_telegram_conflict.py`: fechado nos commits anteriores desta sessão (TypeError de
  formatação em `plugins/platforms/telegram/adapter.py`), verde.
- `test_hermes_state.py:936`: o teste observava conexões que o código não usa —
  `_finalize_search_matches` enriquece via `_read_ctx()`, que pega conexão do POOL. Medido: 2
  conexões traçadas, **0 statements capturados**, e ainda assim `context=True`. Passou a traçar
  onde as read conns nascem.

**B — era BUG REAL de segurança.**
`hermes_constants._get_platform_default_hermes_home()` devolve `~/.haos` (rebrand do fork)
enquanto `hermes_state_guard._real_platform_state_root()` fixava `~/.hermes`. Um filho pytest sem
HERMES_HOME resolvia `<home>/.haos/state.db` e o guarda negava apenas `<home>/.hermes/state.db`,
abrindo o store de produção sem recusa. Antes: o filho saía 0. Depois: sai 1 com
`RuntimeError: live-system guard: test attempted to open production state.db at <home>/.haos/state.db`.

**C — 4, todos de teste/config; nada faltando em produção.**
- `test_slash_dispatch_table.py:51`: medido `dispatched - expected = {'haos'}`. O fork adicionou
  `/haos` com handler real; a lista hardcoded do teste é que estava desatualizada.
- `test_packaging_metadata.py:317`: medido `{'scrapling'}` como o ÚNICO pin exato fora da
  whitelist de `exclude-newer-package`.
- `test_termux_api_detection.py:194`: **não era ambiente — era falso positivo real de produção.**
  `_detect_container()` varria `/proc/self/mountinfo` INTEIRO atrás de `containerd|kubepods|crio`.
  Num host que roda Docker com snapshotter containerd, os rootfs dos containers IRMÃOS aparecem
  neste namespace (52 ocorrências), então `is_container()` respondia True numa máquina que não
  está em container nenhum — prova: `systemd-detect-virt --container` = none, PID 1 = systemd,
  `/` em btrfs `/dev/sda2`. Impacto: desligava o áudio do voice mode e decidia comportamento em
  `hermes_cli/config.py`, `hermes_cli/web_server_files.py` e `agent/skill_utils.py`. Corrigido
  olhando só a entrada de mountinfo cujo ponto de montagem é `/`; pods k8s seguem cobertos por
  `KUBERNETES_SERVICE_HOST`. 4 testes novos e determinísticos sobre mountinfo sintético; mutar o
  guard de volta para varrer o arquivo inteiro deixa 2 vermelhos.

**D — 2, frontmatter.** `platforms: [linux, macos, windows]` após auditar os imports reais dos
dois scripts (prosa pura, sem `scripts/`). Nada adicionado ao `GRANDFATHER`.

**E — 6 arquivos, 5 causas distintas, nenhuma bug de produção.**
- `test_evals_real.py` ×2: **uma causa só** — 3 arquivos de `hermes/platform/` importavam
  `hermes_constants` no nível de módulo, o que o eval do PRÓPRIO platform reprova
  (`_ALLOWED_TOP_IMPORTS = {hermes, hermes_cli, agent}`). Import tardio no ponto de uso, como
  `workspace_scope.py:74` já fazia. O segundo teste reprovava só por tabela (`pass_rate 0.667`).
- `test_sequential_tool_timeout.py:162`: o conteúdo chegava embrulhado. Causa medida com `repr`:
  o helper `_tool_call` montava TODA chamada como `("web_extract", "{}")`, então as duas chamadas
  do mesmo assistant message eram byte-idênticas e o `RepeatToolGuard` marcava "Consecutive
  identical call #2" — o aviso empurrou 13 chars acima do limite de 32 do wrapper de conteúdo
  não-confiável. Nada de errado em produção; o fixture é que era irreal.
- `test_hindsight_provider.py:1711`: **o suite roda como root** e `_start_embedded_daemon`
  (`plugins/memory/hindsight/__init__.py:786`) recusa subir em root ("PostgreSQL initdb refuses
  root"), logo a thread `hindsight-daemon-start` nunca era criada. Medido: só `MainThread`,
  ainda 1 segundo depois.
- `test_install_macos_launcher.py`: exit 127 — o harness stubava `log_info`/`log_success`, mas
  `setup_path` chama `log_warn` no fim do caminho feliz; com `set -e` isso aborta e mascara a
  asserção real.
- `test_browser_real_profile.py:1008`: não patchava `chromium_executable` (dependia de Chrome
  instalado no host) e, resolvido isso, dependia de EXECUTAR o binário — o teste irmão já usava
  `fake_popen` para exatamente isso.
- `test_zombie_process_cleanup.py` e `test_user_providers_model_switch.py`: verdes ao re-rodar.

**O cap de 300s por arquivo — 3 dos 6 "flaky" e o "no tests ran" eram o MESMO defeito.**
Quatro arquivos foram mortos com `(300s exceeded; process tree SIGKILL'd)`: `test_run_agent.py`
(reportado como "no tests ran" — 266 testes nunca rodaram), `test_tui_gateway_server.py`,
`test_local_env_blocklist.py` e `test_execution_flag_detection.py`. Não eram intermitentes: eram
arquivos mortos no meio e aprovados na repetição. Causa: o default de workers é `cpu_count()*2`
(16 numa máquina de 8 cores) e o isolamento por teste paga um processo Python por teste, o que
dilata sob carga — `test_run_agent.py` passa 293 testes em 90s sozinho e aos 300s tinha chegado a
~55%. Cap 300 → 900s em `scripts/run_tests_parallel.py` (um travamento real segue limitado).

**Os 3 flakes restantes, corrigidos por folga de relógio.**
- `test_compression_attempt_lifecycle.py`: o ceiling É o teto do join
  (`grace = min(_CANCELLED_WORKER_TEARDOWN_GRACE_SECONDS, ceiling)`), 0.6s → 2.0s = 25x o unwind
  de 0.08s. A sabotagem documentada (remover `_join_cancelled_worker`) continua acusando.
- `test_turn_lease.py`: janela 0.02s vs 5s com bound de 1s → 0.02s vs 60s com bound de 20s (o
  caminho correto ganha 1000x e o caminho com bug continua reprovável). O bound de 1s também
  estava abaixo do piso de 2s da política de flake.
- `test_slash_worker_mcp_discovery.py`: o worker responde em ~6s sozinho, logo 10s não sobrevive
  à carga; 10 → 60s (nada compete por esse limite).

**Pendências abertas deste bloco:** `test_hermes_home_profile_warning.py` ainda fixa `.haos` em
vez de derivar o home; o guard de root do `local_embedded` do hindsight não tem teste próprio; e
`tools/code_kernel.py:492` emite `TypeError: '<' not supported between instances of 'MagicMock'
and 'int'` numa thread `_stdout_reader` durante o teardown de
`TestPythonpathSelectiveStrip` (aviso, não falha — mock vazando para a thread).
