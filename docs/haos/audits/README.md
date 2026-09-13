# HAOS — Audit & Seam Findings Registry

Frente 3: Consolidação das auditorias de subagentes `[ready]` que mapearam seams
upstream e internos do ecossistema HAOS/Hermes, para garantir que nenhum achado
se perca e fechar lacunas acionáveis com testes.

> **Status verificado (2026-09-07):** a maioria das lacunas apontadas nas
> auditorias já foi fechada por fases posteriores da arquitetura (os relatórios
> analisaram stubs que foram substituídos por implementações reais). Abaixo, cada
> linha traz o status **verificado contra o código e a suíte de testes** — não o
> status do momento da auditoria.

## Índice de relatórios

| Área | Relatório | Status verificado | Evidência |
|------|-----------|-------------------|-----------|
| Upstream MCP client surface | `seams/upstream-mcp-client.md` | 🟢 fechada (wrapper + provider) | `hermes/platform/capabilities/mcp/fabric.py` (discover_servers/scope_servers/health + `MCPCapabilityProvider`), `tests/platform/capabilities/test_mcp_fabric.py` |
| Hermes auth vault surface | `seams/upstream-auth-vault.md` | 🟠 **lacuna aberta** — mocks in-memory em `hermes/platform/auth/`; wrapper real de `auth.json` (write/read_credential_pool, resolve refresh-aware) pendente | sem wrapper real / sem teste E2E |
| ACP + LSP upstream seams | `seams/upstream-acp-lsp.md` | 🟢 fechada (K5/K6 reais, stdlib-only) | `protocols/acp/adapter.py` (`ACPSessionClient`) + `tests/platform/protocols/test_acp.py` (9✓); `capabilities/lsp/manager.py` (`LSPClient` real + `StaticLSPClient` explícito) + `tests/platform/capabilities/test_lsp.py` |
| HAOS seam stubs K7/K8 | `seams/haos-k7-k8-stubs.md` | 🟢 fechada (analyzer real + workspace canônico + seq/série) | `evolution/analyzer.py` (328 ln, shadow-mode de dados), `workspaces/manager.py` delega `resolve_workspace`+`set_workspace_path`, `observability/event_store.py` (seq + `events_after`/`get_all`), `observability/metrics.py` série temporal bounded |
| K6 real LSP client | `seams/k6-lsp-client.md` | 🟢 fechada | `capabilities/lsp/{manager,protocol}.py`; suítes verdes |
| K5 ACP wire client | `seams/k5-acp-client.md` | 🟢 fechada | `protocols/acp/adapter.py`; `test_acp.py` 9✓ |
| Hermes lane spawn seam | `seams/hermes-lane-spawn.md` | 🟢 fechada (spawn-e-wait + `.haos/result.json`) | `execution/lane_executor.py` (`HermesCliLaneWorker.execute`, `available` com probe, `_build_argv`, contrato result.json) + `tests/platform/execution/test_lane_agentic.py` |
| Heartbeat bug + regression | `audits/heartbeat-bug.md` | 🟢 fechada (bug real corrigido) | `tasks/kanban_adapter.py` `ok = bool(result)`; regressão em `tests/platform/tasks/test_task_posture.py` |
| FailureClassifier wiring | `audits/failure-classifier.md` | 🟢 fechada (11 categorias + precedência + wiring no dispatcher) | `execution/classify.py`, `execution/dispatcher.py` (outcome dinâmico via classificador) |
| ExecutionPlan / DependencyEdge | `audits/execution-plan.md` | 🟢 fechada (fail-fast + persistência plan_json) | `tasks/spec.py` (`__post_init__`), `tasks/kanban_adapter.py` (`store/load_execution_plan`), `tests/platform/tasks/test_task_posture.py` |
| Scheduler / priority inheritance | `audits/scheduler-priority.md` | 🟢 fechada (CPM + PIP + age bonus implementados) | `execution/scheduler.py` (`compute_deterministic_critical_path`, `compute_inherited_priorities`, age bonus) + `tests/platform/execution/test_scheduler_and_shape.py` |
| Backpressure / provider concurrency | `audits/backpressure.md` | 🟢 fechada | `execution/backpressure.py` (`ConcurrencyGuard`/`BackpressureController`) |
| Review pipeline / anti-anchoring | `audits/review-pipeline.md` | 🟢 fechada (isolamento anti-anchoring + AcceptanceEngine) | `tasks/review_pipeline.py`, `tasks/acceptance.py`; veredito persistido via KanbanAdapter |
| DSH lifecycle / replay | `audits/dsh-lifecycle-replay.md` | 🟢 fechada (estados FAILED/STOPPED + eventos + seq + replay_fold) | `extensions/registry.py` (FAILED, subscribe, state_history, activate_with_deps, stop, deactivate cascade), `observability/replay.py` (`replay_fold` + `on_unknown`) |
| GasTown roles / team | `audits/gastown-roles.md` | 🟢 fechada (port) | `execution/team.py` (`TeamSpec`/`TeamRole`/`TeamResolver`), posturas `supervisor`/`refinery`, `tests/platform/execution/test_team.py` (9 invariantes) |
| Agno typed contracts | `audits/agno-contracts.md` | 🟢 fechada (input/output schema por fronteira) | `execution/contracts.py`, `tasks/spec.py` (`task_contract`), gate no `dispatcher.py`, `tests/platform/execution/test_contracts.py` |
| DeerFlow research worker | `audits/deerflow-worker.md` | 🟢 fechada (port pure + fetcher out-of-process) | `capabilities/research/{worker,fetcher,models,aggregate}.py`, `tests/platform/capabilities/` |

Legenda: 🟢 fechada (código + teste verificado) · 🟠 lacuna aberta (documentada
para Frente 2 / runtime slice).

## Lacunas abertas remanescentes (não duplicar trabalho já feito)

1. **Auth vault real** (`seams/upstream-auth-vault.md`): os mocks in-memory de
   `hermes/platform/auth/` seguem sem wrapper real sobre o `auth.json` upstream
   (0600, texto-plano) — `write_credential_pool`/`read_credential_pool`
   (hermes_cli/auth.py), `resolve()` refresh-aware e `redact_sensitive_text`
   (agent/redact.py). Integração pertence ao runtime slice (Frente 2), quando o
   HAOS rodar dentro do processo Hermes com `HERMES_HOME` real.
2. **E2E opcionais com deps externas** (K5/K6/mesh): contra `import acp` real e
   language servers binários (pyright/gopls) — exigem extras `[acp]`/`[mcp]` em
   venv isolado; não são requisito do CI stdlib-only.
3. **Série de metrics alimentada por produtor**: a série temporal do
   `MetricsCollector` está fechada e testada; conectar produtores reais
   (dispatcher/lane) ao collector é enriquecimento do runtime slice.
