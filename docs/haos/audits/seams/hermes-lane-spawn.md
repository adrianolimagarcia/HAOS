# HAOS — Hermes lane spawn seam (HermesCliLaneWorker real-spawn)

Auditoria read-only (Frente 3) que especifica o **spawn real** do worker Hermes
canônico para a lane `hermes` do HAOS, substituindo o `NotImplementedError` de
`HermesCliLaneWorker.execute`. Caminhos relativos à raiz do fork kernel
(`hermes_cli/*` = kernel; `hermes/platform/*` = HAOS).

## Escopo

- Como o kernel spawna um worker agentic por card kanban (subprocesso `hermes`),
  seu argv/env/cwd e o contrato PID/exit/resultado com o dispatcher.
- Invocação CLI headless do worker; gate `available()` honesto; handoff de
  resultado/evidência sem double-complete; contrato exato de `execute()`.
- Invariantes de unittest contra um `hermes` fake scriptado (sem chaves de modelo).

## Achados-chave

1. **Spawn real upstream** — `hermes_cli/kanban_db_dispatch.py:2163-2278`
   (`_default_spawn`): por card claimado, `Popen` de um subprocesso
   `hermes -p <profile> --cli --accept-hooks … chat -q "work kanban task <id>"`
   (argv em `_worker_argv` :2086-2123), `cwd=workspace`, `start_new_session`
   (:2267), log por task (:2126-2136), retorna `proc.pid` (:2278). Env:
   `HERMES_HOME=resolve_profile_env(profile)` (:2196-2201), `HERMES_KANBAN_TASK`
   (:2204), `HERMES_KANBAN_WORKSPACE` (:2205), `HERMES_SESSION_SOURCE=kanban`
   (:2208), `TERMINAL_CWD=workspace` (:2213-2221), branch/run/claim_lock
   (:2222-2227), `HERMES_KANBAN_DB`/`WORKSPACES_ROOT`/`BOARD` (:2240-2245),
   `HERMES_PROFILE` (:2248), `HERMES_TUI` removida (:2249-2251).
2. **Quem completa o card é o CHILD** — o worker usa as kanban tools (registradas
   só com `HERMES_KANBAN_TASK`: `tools/kanban_tools.py:64-81,128-186`;
   `model_tools.py:275,320`) e chama `kanban_complete`/`block` (guia em
   `agent/prompt_builder.py:233-304`). O dispatcher só observa: PID persistido
   (`_set_worker_pid`, kanban_db_dispatch.py:1576-1578) e reap por tick
   (`detect_crashed_workers` :937-979): rc0 sem terminal call = protocol
   violation (:720-731,:756-767); exit 75 = quota (kanban_db.py:294, :768-777).
   Exit codes do child: cli.py:4097-4141 (0 ok / 1 falha / 75 rate-limit).
3. **Não existe `hermes kanban run <id>`** — o worker é o chat oneshot genérico
   (`chat -q`, gramática `hermes_cli/_parser.py:187-225`; dispatchers:
   gateway tick, `hermes kanban dispatch` kanban_ops.py:60, `daemon` :154 +
   run_daemon kanban_db_dispatch.py:2285-2337). Lane não-perfil nunca é spawnada
   (`skipped_nonspawnable`, kanban_db_dispatch.py:1510-1517): `hermes -p <lane>`
   sai rc1 (main.py:526-531).
4. **Double-complete é real e silencioso** — `complete_task` do kernel só transita
   `running|ready|blocked|review → done` (kanban_db.py:2567-2585); se o child já
   completou, o `adapter.complete_task` (kanban_adapter.py:236-263) grava snapshots
   HAOS **só** quando o retorno é True → no-op deixa o run HAOS `running` sem
   resultado.
5. **Gate atual é só existência de binário** (`lane_executor.py:111-120`); existe
   `/usr/local/bin/hermes` aqui. Medido: `hermes --help` ≈ 0,4 s rc0 sem rede;
   `hermes --version` ≈ 2,7 s (update check de rede, `_startup_fast.py:130-200`) —
   nunca usar `--version` como probe.

## Lacunas acionáveis

- **L1 — Spawn real em `execute()`** (`hermes/platform/execution/lane_executor.py:122-137`):
  corpo vira spawn-e-wait bloqueante: escreve `workspace/.haos/spec.json`, spawna o
  binário com `--cli --accept-hooks … chat -q <prompt>` (`cwd=workspace`,
  `TERMINAL_CWD=workspace`), espera rc, lê/valida `workspace/.haos/result.json` e
  devolve o dict da lane com `pid` na evidência. Importa: sem isso a lane `hermes`
  nunca roda o kernel de verdade (Fase 1 exige evals reais).
- **L2 — Gate honesto + cache** (`lane_executor.py:99-120`): manter resolução de
  binário e adicionar probe único cacheado `[cmd, "--help"]` (timeout 10 s). Importa:
  evita spawnar worker que morre na largada (venv quebrada, launcher sem alvo).
- **L3 — Exclusão de ownership no env do child** (novo, em `execute()`): **não**
  repassar `HERMES_KANBAN_TASK/RUN_ID/CLAIM_LOCK/DB/BOARD/WORKSPACES_ROOT` (pop de
  herdados, precedente kanban_db_dispatch.py:2186-2190,2249-2251). Importa: torna o
  double-complete impossível por construção; HAOS (dona do claim em
  `dispatcher.py:131-138`) continua o único completer via `_run_and_complete`
  (:57-68) — zero mudança no dispatcher.
- **L4 — Perfil do child** (`execute()`/ctor): nunca usar o assignee do card
  (nome de lane → rc1); default = `HERMES_HOME` herdado do processo HAOS; override
  opcional via `HAOS_HERMES_PROFILE` (+ `-p` e `resolve_profile_env`,
  hermes_cli/profiles.py:169,1680). Importa: worker real precisa de um perfil com
  chaves/config para o runtime slice.
- **L5 — Timeout/livro de falha** (runtime slice, opcional): `timeout_seconds`
  default None (killpg no estouro); em `LaneError`, liberar o claim pelo padrão
  upstream kanban_db_dispatch.py:1587-1593 (senão o card fica `running` até TTL).
- **L6 — Testes fake-peer** (novo `tests/platform/execution/test_lane_agentic.py`):
  fake `hermes` (stdlib, precedente `tests/platform/capabilities/_mock_lsp_server.py`
  + test_lsp.py:31-36) gravando argv/env/cwd em log e `.haos/result.json`; invariantes
  (b)-(e) do contrato abaixo. Importa: contrato exercitado sem chaves de modelo.

## Status de implementação

- **Seam K1 (parcial)** — `execution/dispatcher.py` + `lane_executor.py` + `kanban_adapter.py`
  existem e passam E2E com a lane determinística (`LANE_WORKERS`, lane_executor.py:141-144;
  testes: `tests/platform/execution/test_dispatcher.py`, `test_execution.py`).
  `HermesCliLaneWorker.execute` ainda é `NotImplementedError` (lane_executor.py:133-137);
  gate sem probe; sem fake-peer. **Nenhuma linha de código nova foi escrita nesta auditoria.**

## Recomendações

1. **Contrato único recomendado**: spawn-e-wait + `.haos/result.json`; HAOS segue
   dono do estado do card. Alt nativa do kernel (child dono, env kanban completo)
   fica para o runtime slice — exige sync de meta no adapter e heartbeat/TTL.
2. Delta mínimo: só `lane_executor.py` (execute + probe cacheado + helpers argv/env)
   e o novo `test_lane_agentic.py`; dispatcher/adapter/registro/testes atuais intactos.
3. Esquema do result file: `{"summary": str, "evidence": dict, "artifacts": [str],
   "residual_risk": [str]}`; rc0 sem arquivo/json inválido → `LaneError`; rc≠0 →
   `LaneError` com tail do log.
4. Registrar `HermesCliLaneWorker` na lane `hermes` só por override/env quando houver
   runtime+perfil configurados (`register_lane_worker`, lane_executor.py:153-154).
5. Rodar via `scripts/run_tests.sh tests/platform/execution/test_lane_agentic.py`;
   injetar sempre `hermes_command=<fake>` (nunca depender do `hermes` real no PATH).
