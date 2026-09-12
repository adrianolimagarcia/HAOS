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

## Atualização 2026-09-13 — baseline FECHADO: 0 falhas

```
=== Summary: 3901 files, 46416 tests passed, 0 failed, 422 skipped (100% complete) in 1800.7s (16 workers) ===
```

Ponto de partida: 3901 arquivos, 46386 passando, **26 falhando**, 422 pulados. Agora **0
falhando** e 46416 passando (+30 testes que antes nunca rodavam, porque o cap de 300s matava os
arquivos antes de chegarem neles). Este era o pré-requisito da Etapa 1: com o baseline vermelho,
nenhum gate das etapas seguintes valia nada.

Mesmo assim o run fechou com **3 arquivos FLAKY** (falharam na 1ª tentativa, passaram na
repetição). Pela política do repo isso é bug, não ruído, e os três foram fechados — cada um com
prova de duas armas, isto é, com a falha reproduzida deterministicamente em vez de "não
reproduzi":

1. `test_slash_worker_mcp_discovery.py` — **era bug de produção, não de teste.** O slash worker
   chamava `wait_for_mcp_discovery()` sem argumento, ou seja o bound de 1.5s
   (`mcp_discovery_timeout`), mas o `HermesCLI` é construído UMA vez antes do `while True`, então
   o snapshot de ferramentas é congelado: um servidor MCP de perfil que perdesse a corrida de
   1.5s nunca aparecia no `/tools` daquele worker, por toda a vida dele. O bound de 15s
   (`mcp_single_query_discovery_timeout`) existe exatamente para o caso "sem segundo turno para
   recuperar", que é o caso do snapshot. Atrasando o servidor do próprio teste em 3.0s: o código
   antigo FALHA com a assinatura exata do flake (`Total: 51 tools`, sem a ferramenta MCP); com o
   fix PASSA. Isso corrige também a hipótese errada do commit `0ffde0bd17`, que subira o bound do
   `output.get` de 10 para 60s — aquele `queue.Empty` era outro sintoma do mesmo atraso.
2. `test_update_zip_two_phase.py::test_no_open_coded_venv_layout_remains_in_hermes_cli` — o frame
   do traceback é `pathlib.py:938`, que nesta CPython é `return os.scandir(self)` dentro de
   `_scandir()`, o método que o próprio pathlib documenta como base do `glob()`. Isto é: a exceção
   veio do CAMINHANTE do `pkg.rglob("*.py")` entrando num diretório que desapareceu no meio
   (`hermes_cli/__pycache__`, o diretório mais mutado durante um run de 16 workers), não da leitura
   de um arquivo. Trocado por `os.walk(..., onerror=...)` podando `__pycache__`, que não tem fonte
   `.py` nenhum — cobertura idêntica, sem a corrida.
3. `test_zombie_process_cleanup.py::test_timed_out_child_keeps_relay_session_until_its_turn_exits`
   — o log da própria suite deu a linha (515) e a prova: `Subagent 0 timed out after 0.1s` seguido
   de `where is_set = <threading.Event ...: unset>`. O teste fixava o timeout do filho em 0.1s e
   assertava que o filho tinha começado; sob carga o thread não era escalonado a tempo, o pai
   estourava primeiro, e o invariante do teste nem era exercitado. Forçando o timeout a `0.000001`
   a falha volta determinística na MESMA asserção; com 3.0s passa.

Os três são da mesma família dos anteriores: **um limite de relógio mais curto que o mecanismo que
ele espera**. Fica como regra para escrever teste aqui: o bound tem de ultrapassar o orçamento do
que está sendo esperado (join > drain, timeout > spawn, varredura tolerante a corrida de FS).

**Deploy:** 16 commits (25 arquivos) empurrados em lockstep para `haos-standalone` e `main`
(`728f72ee03..e9d10eefaa`), árvore de instalação do host atualizada e VM `/opt/haos` por `tar`+`cp`.
Verificado no appliance, não só transferido: `is_container()` = **False** na VM com
`systemd-detect-virt --container` = **none** — o falso positivo corrigido nesta sessão
comportando-se corretamente lá.

## Atualização 2026-09-13 (b) — `MagicMock/` na raiz: causa raiz encontrada

Um diretório `MagicMock/` aparecia na raiz do repo depois de runs longos, com um state DB e um
`.fts_rebuild.lock` dentro. Ficou meses sem explicação porque só nascia em suite completa.

A bisseção de 143 arquivos que mockam `_session_db` NÃO reproduziu — e o motivo é o próprio
diagnóstico: o arquivo culpado não menciona `_session_db`. A captura veio de uma armadilha em
`os.mkdir`/`os.makedirs` instalada via `sitecustomize` no venv (roda em todo subprocesso do
runner, registra a pilha completa e não altera comportamento). Cadeia capturada:

```
tests/tools/test_subagent_steer.py:190 -> delegate_task -> _build_children
  -> _build_child_preserving_parent_tools -> _build_child_agent
  -> _open_child_session_db (delegate_tool.py:102) -> hermes_state_registry.acquire
  -> SessionDB(db_path=...) (hermes_state.py:477) -> _open_writer
  -> self.db_path.parent.mkdir(parents=True, exist_ok=True)   (hermes_state.py:492)
```

`parent = MagicMock()` não é `None`, então `_open_child_session_db` (delegate_tool.py:96-98)
conclui que o pai tem session DB e abre um filho dedicado em `parent._session_db.db_path` — que é
outro mock. O `SessionDB` materializa esse nome no filesystem real, no CWD (a raiz do repo sob o
runner), junto com o lock do FTS.

Correção no TESTE, não na produção: em produção `_session_db` é `SessionDB`/`AsyncSessionDB` ou
`None`, nunca um mock — um guard novo em produção seria defense-in-depth, que o AGENTS.md rejeita.
O pai falso passa a declarar `parent._session_db = None` (dois testes) e `_open_child_session_db`
retorna cedo, sem handle e sem diretório.

Prova de duas armas, no mesmo arquivo e comando:
- arma B (código antigo): `MagicMock/mock._session_db.db_path/140098970850384.fts_rebuild.lock`
  criado em **4.1s** — 30 testes passando apesar do lixo.
- arma A (com fix): `1 files, 30 tests passed, 0 failed`, nenhum diretório criado, armadilha com
  0 mkdirs.

A armadilha foi removida do venv depois da captura. Commit `75781911d9`.

## Atualização 2026-09-13 (c) — suite v4 com o P11 e estado real dos flakes

```
=== Summary: 3901 files, 46423 tests passed, 0 failed, 422 skipped (100% complete) in 1752.9s (16 workers) ===
```

O P11 responde por +25 testes. **0 falhas**, mas 2 arquivos FLAKY — e o relatório do runner tem um
ponto cego que vale registrar: um arquivo morto pelo cap de 900s nas DUAS tentativas aparece no
sumário como "0 failed" e, no run anterior, nem na lista de FLAKY; os testes dele simplesmente não
rodam. Só a seção `Failed:`/`FLAKY` do log mostra. Ao ler um resumo deste runner, sempre confira
essas duas seções e a contagem de testes contra o run anterior.

1. `tests/gateway/test_buzz_websocket.py` — travou 2 vezes em 2 suites (`900s exceeded`, 911.66s na
   lista de durações) e passa sozinho em ~9s (3 execuções, 18 testes). Não usa porta fixa nem rede
   real (`wss://relay.example` + websockets mockados) e não toca `slash_worker`. É trava sob carga;
   a captura exige pilha antes do cap, já que o runner mata com SIGKILL e não deixa rastro.
2. `tests/test_tui_gateway_server.py` — `RuntimeError: dictionary changed size during iteration` no
   teardown de `test_prompt_submit_row_id_real_sessiondb_unknown_refuses_despite_ordinal`, e o
   `AssertionError: previous item was not torn down properly` no teste seguinte é dano colateral do
   pytest (`SetupState.teardown_exact`), não um segundo bug. O teste usa `monkeypatch, tmp_path` e
   roda um turno real do gateway; o `AttributeError: 'types.SimpleNamespace' object has no attribute
   'run_conversation'` no log é o agente falso do teste e é esperado.
   **Corrigido na raiz — ver a atualização (d).**

## Atualização 2026-09-12 (d) — flake do loggerDict: causa raiz medida e corrigida

O item 2 da seção (c) está corrigido na raiz. **A causa não é o `tui_gateway`**: é o walk *sem
snapshot* que o plugin de logging do pytest faz no registro GLOBAL de loggers, no início de CADA
fase (setup/call/teardown):

```
_pytest/logging.py, catching_logs.__enter__:
    for logger in root_logger.manager.loggerDict.values():
```

`logging.Logger.manager.loggerDict` é global do processo e cresce sempre que QUALQUER thread importa
um módulo com logger de módulo (ou chama `getLogger` com nome novo) — `Manager.getLogger` insere sob
`logging._lock`, e o walk do pytest NÃO toma esse lock. Uma thread de fundo registrando um logger no
meio do walk mata a thread PRINCIPAL com `RuntimeError: dictionary changed size during iteration`.
Como o erro cai na fase de teardown, o `SetupState.teardown_exact` nunca roda: a pilha de setup fica
suja e o teste SEGUINTE morre com `previous item was not torn down properly` — exatamente o par de
erros registrado em (c), e por isso o dano colateral é do pytest, não um segundo bug.

Medições (`tests/test_tui_gateway_server.py`, 637 testes):

- `_pytest/logging.py:359` é o ÚNICO ponto do pytest — e de todo o `site-packages` — que itera
  `loggerDict`; a única outra iteração do stdlib (`Manager._clear_cache`) já toma o lock.
- No teardown do teste que falha, `server._sessions` está VAZIO e há threads vivas
  (`Thread-2 (_loop)` = idle reaper do `tui_gateway`, `Thread-62 (_monitor)`): nenhum finalizer de
  fixture itera dict nesse caminho, então a rota "finalizer de fixture" está descartada.
- Em 1 run sem forçar nada (49,6s) threads de fundo registraram **65 nomes novos** de logger:
  18+18+18 de três threads `run` (import tardio de módulos de plugin — os nomes sintetizados embutem
  o hash do HERMES_HOME por teste, logo são novos em TODO teste), 3 de `asyncio_0`, 1 de `Thread-1`
  e 1 de `Thread-6 (_build)`.

Correção (`tests/conftest.py`): o registro global é religado a um `dict` cuja iteração devolve
snapshot (`values`/`keys`/`items`/`__iter__`), instalado no import do conftest — antes de qualquer
módulo de teste. Nada in-tree lê `loggerDict`, e todo leitor quer "os loggers que existem agora": é o
`list(...)` que faltava, aplicado no único ponto que o repo controla (o walk é código de terceiro).

Prova de duas armas (mesmo comando, com a carga forçada: thread registrando 40000 nomes novos
enquanto a fase de teardown começa):

- arma B (`tests/conftest.py` original, `registry_type=dict`): `ERROR at teardown of
  test_alpha_bulk_loggers` + `RuntimeError: dictionary changed size during iteration` e, no teste
  seguinte, `ERROR at setup ... AssertionError: previous item was not torn down properly` —
  **6/6 runs vermelhos**.
- arma A (com o fix, `registry_type=_SnapshotIteratingDict`, 100044 entradas, 40000 registros da
  thread): `2 tests passed, 0 failed` — **6/6 runs verdes**.
- Medição determinística do contrato, mesmo objeto real nos dois braços: sem o fix
  (`registry=dict`) → `RuntimeError: dictionary changed size during iteration`; com o fix
  (`registry=_SnapshotIteratingDict`) → walk sobrevive.

Regressão determinística: `tests/test_conftest_logger_registry.py` (6 testes) — iniciar o walk e
registrar um nome novo no meio levanta `RuntimeError` no registro original e sobrevive nos quatro
acessores com o fix. Nenhuma asserção depende de timing.

`tests/test_tui_gateway_server.py` com o fix: `1 files, 637 tests passed, 0 failed in 47.2s`.
