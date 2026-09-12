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
