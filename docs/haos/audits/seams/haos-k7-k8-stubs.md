# HAOS Seam Stubs — K7 (Ouroboros alimentado) & K8 (Workspace real)

Inventário preciso dos módulos HAOS que os integradores K7/K8 devem substituir por seams
reais. Survey somente-leitura — nenhum arquivo foi modificado nesta auditoria.

## Escopo

| Id | Objetivo (INTEGRATIONS.md) | Alvo |
|----|----------------------------|------|
| K7 | `evolution/analyzer.py` consome `observability/` (metrics/event_store) + `evals/` (baselines) e emite proposals com dados, não texto fixo (INTEGRATIONS.md:46, COMPLIANCE.md:36,:94) | `hermes/platform/evolution/analyzer.py` |
| K8 | `workspaces/manager.py` delega ao kanban upstream (`workspace_kind=scratch/worktree`, boards com `default_workdir`) em vez de dirs `/tmp` avulsos (INTEGRATIONS.md:47, COMPLIANCE.md:31) | `hermes/platform/workspaces/manager.py` + `hermes_cli/kanban_db_workspace.py` |

Nota de árvore: git rastreia `hermes` como *arquivo* (`D hermes` no status); todo o scaffold
`hermes/`, `haos.py` e `tests/platform/` é trabalho novo não-commitado. Não existe `__init__.py`
em lugar nenhum sob `hermes/` — `hermes.platform.*` resolve como namespace package (PEP-420) e
`evolution/__init__.py` não tem exports.

## Achados-chave

1. **analyzer.py é um stub de 16 linhas.** `OuroborosAnalyzer.analyze_execution_history(event_logs)` (analyzer.py:6) ignora `event_logs` e retorna UMA proposal hardcoded (analyzer.py:7–15): chaves `target`, `current_profile`, `proposed_profile`, `rationale` (texto fixo "+8.2% accuracy…"), `mode="PROPOSAL_ONLY"`. Esse dict de 5 chaves é todo o "shape" de proposal; não há dataclass.
2. **Nenhum caller em produção invoca o analyzer.** `haos_server.py:30` importa e `:42` instancia, mas GET `/` hardcoda o texto da proposta (haos_server.py:250) e `/trigger_eval` grava evento `eval.performance {"score":0.94}` fixo (:322). Único caller real: `tests/platform/test_full_platform.py:56–60`.
3. **MetricsCollector é in-memory e sem série temporal.** `record_latency` sobrescreve por operação (metrics.py:12–13), nada é persistido, e não há importador em produção (só testes). O único dado real persistido hoje são os eventos SQLite do `EventStore("/tmp/haos_events.db")` escrito por `haos_server.py:33,:312–314,:319,:322`.
4. **EventStore só lê por trace/correlation.** Sem query por nome/tipo nem get-all (event_store.py:66,:87) — essencial para um motor de proposals consumir stream por tipo.
5. **BaselineStore já é sqlite file-backed com série temporal** na tabela `eval_baselines` (baselines.py:33–39), mas expõe só `latest()` (:57–74) — sem `history()`. `compare_results` julga **só por pass_rate** (runner.py:80–93) e `EvalResult` não tem `from_dict`.
6. **WorkspaceManager cria `/tmp` e ignora o spec.** `create_workspace(task_id, spec)` faz `mkdir <root>/haos_workspace_<id>` (manager.py:19–22) sem usar `spec.uri/type/base_branch`; `cleanup` é `rmtree` cego (:24–26). Único caller: `adapters/kilo/adapter.py:3,:8–9,:12–13` (nunca chama cleanup — vaza dirs).
7. **O caminho canônico real já existe e é ignorado pelo manager.** `resolve_workspace(task, *, board=None)` (kanban_db_workspace.py:493–531) + `set_workspace_path` (:539–540) materializam scratch sob `workspaces_root(board)/<id>` e worktree sob `<repo>/.worktrees/<id>` ou âncora no board `default_workdir` (:421–490, guarda anti-limpeza :97–110,:113–170). O HAOS `dispatcher.py:136–137` já o usa em `claim_tick`; só o `KiloLaneAdapter` o contorna.
8. **Board `default_workdir` vive em `board.json`**: `read_board_metadata` (kanban_db.py:529–557, default `None` :539) / `write_board_metadata` (:560–591); CLI `kanban boards set-default-workdir` (kanban_boards.py:146–150). `create_task` herda para `dir/worktree` (kanban_db.py:1287–1292).
9. **Dois testes são change-detectors do stub e quebrarão com o K7/K8 real**: `test_full_platform.py:56–60` (espera ≥1 proposal com `[]` como entrada) e `test_execution.py:29` (espera substring `haos_workspace_T-3`, o nome do dir `/tmp`).

## Lacunas acionáveis

1. **Analyzer não consome dados** — *o quê*: reescrever o corpo para derivar proposals de metrics/baselines/eventos reais; *onde*: `hermes/platform/evolution/analyzer.py:6–15`; *por que importa*: K7 exige "Proposal Ouroboros muda quando metrics/baselines mudam" (INTEGRATIONS.md:46); texto fixo = falsa telemetria de shadow mode.
2. **EventStore sem leitura por nome/tipo** — *o quê*: adicionar `get_by_name(name)`/`get_all()` (SQL `WHERE name=?`/`ORDER BY timestamp`); *onde*: `hermes/platform/observability/event_store.py`; *por que importa*: contar `task.created/completed`, erros, `eval.performance` por janela é a matéria-prima das proposals; hoje só dá para percorrer por trace/correlation.
3. **Baseline sem histórico** — *o quê*: expor `history(suite_id, label=None)` na tabela `eval_baselines` (já ordenada por `created_at`); *onde*: `hermes/platform/evals/baselines.py:30–74`; *por que importa*: delta vs baseline exige série, não só `latest()`; e `compare_results` julga apenas pass_rate — avg_score/token-cost ficam fora do veredito.
4. **EvalResult sem round-trip** — *o quê*: `EvalResult.from_dict` (ou comparar no nível do dict de metrics); *onde*: `hermes/platform/evals/runner.py:33–59` vs `BaselineStore.latest()["metrics"]`; *por que importa*: sem isso, comparar um run novo contra baseline persistida exige reconstruir outcomes à mão.
5. **MetricsCollector sem série e sem produtor** — *o quê*: manter lista por op em `record_latency` e/ou alimentar de eventos; *onde*: `hermes/platform/observability/metrics.py:12–23`; *por que importa*: custo/latência real são entradas de proposal; hoje é estado morto (sem caller).
6. **Servidor não invoca o analyzer** — *o quê*: GET `/` (haos_server.py:250) e `/trigger_eval` (:322) devem chamar `ouroboros.analyze_execution_history(...)` com dados reais do `db_store`; *onde*: `hermes/platform/haos_server.py`; *por que importa*: sem isso o K7 não é observável em runtime.
7. **WorkspaceManager `/tmp` avulso** — *o quê*: `create_workspace` deve resolver o card (via `kb.get_task`/adapter `_resolve_task_id` para ids `T-…`) e delegar a `resolve_workspace(task, board=board)` + `set_workspace_path`; `cleanup_workspace` deve respeitar a guarda de managed-scratch (rmtree só dentro de `_is_managed_scratch_path`) ou virar no-op (o `complete_task` upstream já limpa); *onde*: `hermes/platform/workspaces/manager.py:16–26`; *por que importa*: K8 = "sem dir /tmp avulso"; worktree/scratch canônicos dão provenance e cleanup seguro (não apaga árvore de usuário, #28818).
8. **Manager sem contexto de board/db** — *o quê*: ctor com `board=None`, `db_path=None` (ou aceitar `KanbanAdapter`); *onde*: `manager.py:16–17`; *por que importa*: `resolve_workspace` precisa de conexão + board; o default atual só conhece `tempfile.gettempdir()`.
9. **Testes do stub** — *o quê*: atualizar `test_full_platform.py:56–60` e `test_execution.py:29` para alimentar dados reais / assertar caminho canônico, e adicionar teste do manager no padrão hermético (TemporaryDirectory + `HERMES_KANBAN_HOME`/`HERMES_HOME`, ver `tests/platform/execution/test_dispatcher.py:19–41`); *onde*: `tests/platform/`; *por que importa*: change-detectors do stub passam com implementação quebrada e falham no refactor correto.

## Status de implementação

- Nenhum achado acima virou código no tree nesta auditoria (survey read-only).
- Já existente e aproveitável (não é stub): `dispatcher.py:136–137` usa o caminho canônico via `kanban_db_workspace`; `KanbanAdapter` mapeia `workspace_type→workspace_kind` (kanban_adapter.py:33,:112) e resolve `T-…→t_…` (:90–95); upstream `kanban_db_dispatch.py:1556–1570` e `kanban.py:715–716` já executam o par `resolve_workspace`+`set_workspace_path`.
- Testes E2E hermético existentes que servem de molde: `tests/platform/execution/test_dispatcher.py` (8 testes, scratch canônico assertado em :83–85).

## Recomendações

**K7 — seam mínimo:** manter `OuroborosAnalyzer` e o nome/signature `analyze_execution_history(event_logs)`, tornando a entrada opcional (`event_logs=None, *, metrics=None, event_store=None, baseline_store=None`) e o retorno ainda `List[Dict]` com as 5 chaves (incl. `mode="PROPOSAL_ONLY"`). Dados reais: (a) deltas de baseline via histórico da tabela `eval_baselines`; (b) contagem de eventos por nome (requer o item 2 das lacunas); (c) custo/latência (item 5). Ligar o resultado em haos_server.py:250/:322. Atualizar o teste change-detector.

**K8 — seam mínimo:** preservar `WorkspaceSpec(uri,type,base_branch="main")`, `WorkspaceManager(root_dir=None)`, `create_workspace(task_id,spec)->str` e `cleanup_workspace(path)` (contrato de `kilo/adapter.py`), mas reimplementar delegando a `kanban_db_workspace.resolve_workspace`/`set_workspace_path` com board do ctor; `cleanup` delega à semântica upstream. Manter `base_branch` → `branch_name`/`wt/<task_id>` para worktree. Atualizar `test_execution.py:29` e adicionar teste no padrão hermético do `test_dispatcher.py`. Não tocar em `kanban_db*.py` (upstream é o dono do ciclo de vida).
