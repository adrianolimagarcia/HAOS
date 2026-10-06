# Plano verificável: dispatcher Kanban incremental em `haos-edge`

**Status:** proposta; não implementada nem implantada.  
**Escopo:** planejar migração do loop/dispatch para Rust mantendo Kanban como ledger canônico e Civilization como autoridade de missão/política. Nenhum runtime, configuração existente, ADR ou dado operacional foi alterado.

## Evidência observada no worktree

- `plugins/kanban/systemd/hermes-kanban-dispatcher.service:1-14,23` chama daemon standalone, mas declara-o depreciado; despacho normalmente é no gateway (`kanban.dispatch_in_gateway=true`). Não é correto tratar o unit como o único scheduler.
- `hermes_cli/kanban_db_dispatch.py:1708-1764` implementa `dispatch_once`: lock por caminho de DB, tick de recuperação/dispatch, hooks após o lock. `:1780-1883` verifica perfil/capacidade, claim, workspace e spawn; `:1917+` contém reclaim; `:2489-2647` prepara env isolado e subprocesso; `:2654-2706` roda daemon.
- `hermes_cli/kanban_db_connect.py:160-205` usa `.dispatch.lock` não bloqueante. Em erro ao abrir lock, degrada para `acquired=True`; documento isso como risco a resolver/decisão antes de assumir exclusão mútua Rust.
- `hermes_cli/kanban_db.py:865-935` define schema `tasks` com `claim_lock`, `claim_expires`, `idempotency_key`, `worker_pid`, fingerprint, heartbeat e `current_run_id`. `:2161-2204` faz CAS `status='running'`, abre `task_runs`, escreve evento `claimed` numa transação; `:2208-2239` valida pais e chama hook após commit. Status canônicos são minúsculos (`:104`).
- `dispatch_once` chama `claim_task`/`claim_review_task` antes de workspace e spawn (`kanban_db_dispatch.py:1840-1876`); se spawn falhar, registra falha/libera claim (`:1877-1883`). Há janela crash-after-claim-before-spawn a tratar.
- `hermes/platform/tasks/kanban_adapter.py:246-319` integra `HAOS_EDGE_URL` para claim/heartbeat e faz fallback Python em exceção. O edge claim escreve `RUNNING` maiúsculo, `claimer` e `claim_expires_at` (`packages/haos-edge/src/server.rs:2023-2066`), diferente do schema Python (`status='running'`, `claim_lock`, `claim_expires`). O handler heartbeat (`:2077-2119`) não exige worker_id se omitido. O fallback torna esse fast path não prova de writer parity nem rollout seguro.
- Esses endpoints são montados em `server.rs:428-429`; payload aceita `db_path`, default resolve apenas HAOS_DATA_DIR/HERMES_HOME/root (`:2023-2029`). Não vi validação de origem/perfil/board nesse handler. Não afirmar claims Rust em produção: há código/endpoints e cliente configurável, mas implantação/cobertura não verificada.
- `hermes/platform/execution/mission_runtime.py:30-55` liga supervisor por perfil/DB; `:57-135` compila nós em cartões Kanban; `:137-182` start/recovery; `:184-225` pause/resume/shutdown; `:237-376` loop verifica estado desejado, dependências, approvals, claim e execução. Portanto existe integração de missão Python com Kanban, mas busca no dispatcher Rust/Python não mostrou wiring direto do dispatcher à Civilization. `packages/haos-civ/src` não contém correspondência de `kanban`/`dispatch`.
- REGISTRY observado em `/root/.hermes/council/REGISTRY.md` e plano state DBs são registros separados. Este plano não os altera. O `REGISTRY.md` presente não forneceu evidência de dispatcher Rust ativo.
- `docs/haos/DEV_WORKFLOW_VM.md:9-37,71+` limita deploy/VM/ISO. Esta tarefa é exclusivamente documentação, sem deploy.

## Objetivo e não-objetivos

Migrar gradualmente a **responsabilidade de scheduling/dispatch** para haos-edge sem duplicar ledger, política de Civilization ou spawning concorrente. Kanban `kanban.db` continua a única autoridade persistente de task/run/status/claim/lease/retry/eventos; Civilization/MissionStore mantém desired state, approval gates, associação missão-nó e governança. Edge coordena tick e executa interfaces aprovadas, não cria ledger paralelo.

Não inclui migrar schema/estado, todo executor para Rust, reimplementar MissionSupervisor, mudar políticas, remover Python, afirmar meta de performance, editar config, deploy ou ativar serviço. Nenhum número de redução/latência é assumido.

## Decisões propostas a aprovar antes de código

1. **Single scheduler writer:** por DB+board, um dispatcher ativo. Fase shadow é somente leitura; cutover requer fence/lease de scheduler exclusivo e prova de que gateway dispatch, daemon e CLI dispatch não podem simultaneamente mutar/claimar. `dispatch.lock` atual não bloqueia todos os writers nem é distribuído; resolver falha-aberta e identidade de board/path canônico antes do canary.
2. **Single state authority:** só APIs/código canônico Kanban fazem transições; Rust deve espelhar semântica, tabelas/colunas e eventos upstream, não gravar colunas inventadas. civilization desired-state/gate segue em MissionStore; aprovação não é inferida de Kanban status ou EventStore.
3. **Integração Civilization:** inicialmente MissionSupervisor continua responsável por admission, approval, wake/cancel cooperativo e execução local. Definir contrato para ele publicar/admitir cards e observar resultados; dispatcher não cria execução alternativa. Só depois de contrato explícito pode supervisor delegar tick ao serviço Rust. Evitar dois loops consumindo os mesmos cards.
4. **IPC:** API local versionada, autenticação com credencial interna/peer authorization, binding immutable a profile + board + canonical DB identity. Não aceitar path arbitrário de payload; resolver allowlisted DB pelo edge, comparar inode/canonical path, rejeitar symlink/path traversal e cross-profile. Proteger replay/request IDs e limitar TTL/valores. Unix socket recomendado quando suportado; loopback HTTP só com auth e bind restrito. Confirmar endpoint auth middleware efetivo no router antes de reutilizar.
5. **Lease fencing:** claim devolve claim_lock/run_id e monotonic fencing generation; heartbeat, completion, failure e cancel exigem token vigente + worker identity e CAS status. Claim expirado não autoriza worker antigo completar. Não tratar `worker_id` opcional como autorização. Timestamps UTC/epoch e TTL validado/limitado.
6. **Crash window:** persistir claim + outbox intent de spawn atomicamente no ledger/extension transaction; dispatcher recupera intents idempotentes. Se subprocess spawn aconteceu e PID ainda não foi persistido, reconciliar por task/run/env marker/process fingerprint; nunca spawn cego em retry. Definir semanticamente at-least-once (execução externa pode duplicar) e operações idempotentes por task_id/run_id; efeitos externos do agente não podem ser exactly-once garantidos.
7. **Spawn seguro:** `Command` com argv fixo/allowlist de executáveis e modo de worker, sem shell/interpolação. Reutilizar resolução validada de profile, secret-scope, sanitized env, cwd/workspace, board DB, worker fingerprint, logs, restart-safe cgroup/session e external worker registry; mapear comportamento Python antes de portar. Falhar fechado para profile inexistente, DB mismatch, credential/auth failure e executável não permitido.

## Sequência vertical e gates

### Fase 0 — Contrato e baseline (read-only)

- Congelar schema fingerprint e fixtures reais sanitizadas: tasks, task_runs, events, hooks, workspace, review, retry, multi-profile/board; mapear transições Python e configuração efetiva do gateway.
- Capturar baseline por janela representativa sem inventar metas: taxa de ticks, itens elegíveis/claims/spawns/reviews, skips por causa, duração de tick e spawn, falhas, stale/reclaim, workers órfãos, duplicidade, WAL/locks. Guardar comandos, dataset/ambiente, versão e resultado observável.
- Definir threat model IPC, ownership de scheduler, formato dos IDs e protocolo de rollback. Revisar a matriz de campo em edge contra schema canônico.
- **Gate:** inventário verificável + golden snapshots + testes de contrato escritos/aceitos; sem efeitos no board.

### Fase 1 — Protocolo tipado Rust, sem writes

- Adicionar módulos isoláveis de model/validation/query; endpoint status/candidate enumeration read-only por profile/board. Shadow compara Rust candidate decisions com Python `dispatch_once(dry_run=True)` sem disparar hooks mutáveis nem spawn.
- Normalizar casos `ready` e `review`, dependências, assignee/profile, caps global/por perfil, default assignee e recovery. Diff estruturado inclui reason codes e candidate order.
- **Gate:** fixtures e property tests provam mesma seleção/ordenação/reason codes; nenhuma escrita SQL/claim/processo; divergências classificadas/zeradas ou explicitamente aprovadas.

### Fase 2 — Claim e lease compatíveis, ainda sem spawn

- Trocar handler atual por transação compatível com `claim_task`/`claim_review_task`, schema/status lowercase, claim_lock, task_runs/current_run_id/events e parent gate. Remover mutação via `spec_id` ambígua: resolução a exatamente um task ID canônico.
- Rust API transacional com fencing token; Python cliente opcional só em testes/canary fechado. Eliminar fallback silencioso para mutação Python quando Rust declara write mode; fallback só em modo legacy explicitamente selecionado antes da operação. Nunca tentar segunda claim após timeout ambíguo sem reconciliar request ID.
- **Gate:** testes reais em SQLite temporário com duas conexões/processos concorrentes, perfil A→B→A, expired lease, heartbeat de worker errado, stale worker completion, retry/review e crash rollback; estado/eventos equivalentes ao Python golden.

### Fase 3 — Outbox/recovery + execução controlada

- Outbox/intent transacional para claim->spawn; consumidor reiniciável dedupe por run/request ID. Definir armazenamento aditivo em Kanban, migração compatível e retenção antes de implementação; não usar `state.db` nem REGISTRY como queue.
- Adicionar executor spawn Rust com allowlist, env/profile parity, PID/fingerprint, logs e readiness. Feature flag default Python; edge em dry-run por observação; shadow jamais executa.
- **Gate:** falhas injetadas entre commit claim, criar processo, persistir PID, hook e conclusão; restart/reconcile não duplica spawn de modo não detectável; integração verifica logs/env não-secretos, isolamento profile/board e review path. Segurança revisada.

### Fase 4 — Hooks, review, cancel, retry e Civilization

- Entregar eventos/hooks pós-commit fora de lock, idempotentes e ordenados; outbox garante reentrega. Não chamar subscriber lento na transação. Contrato para pause/resume/wake e approvals deve manter MissionSupervisor como gate soberano; cancel propaga cooperação ao worker, não apaga claim sem fencing/reconciliação.
- Portar políticas de failure_limit, retry status para run de review, max runtime, orphan/crash recovery, default assignee e backpressure/per-profile limits. Não reduzir a comportamento apenas READY->RUNNING.
- **Gate:** testes integrados end-to-end: missão → card canônico → approval pending bloqueia spawn → approve acorda exatamente uma admissão → pause bloqueia novas admissões/cancel coopera → resume/recovery continua → resultado reflete no mesmo Kanban/MissionStore. Testar event replay/hook duplicate.

### Fase 5 — Canary e cutover reversível

- Shadow readonly contra gateway real e medir deltas. Canary por board/profile isolado: habilitar dispatcher Rust como scheduler único para essa partição, desabilitar dispatcher Python somente nela e garantir controle operacional para não haver overlap. Medir baseline vs canary por métricas pré-definidas, erros e consistência, sem meta arbitrária.
- Aumentar escopo em degraus apenas após janela observada e aceite humano. Manter cliente/dispatcher Python compatível e kill switch. Nenhum claim/spawn Python concorrente no board canary.
- **Rollback:** parar novas admissions Rust, conservar/drain workers vivos (não limpar locks à força), desabilitar Rust scheduler, reconciliar claims/outbox/PIDs com fencing, só então reativar Python. Não trocar autoridade durante lease ativo. Verificar contagem de runs/status/eventos e ausência de duplicatas antes de retomar. Rollback não deve apagar execuções em andamento.
- **Gate:** runbook exercitado em staging com rollback e reentrada; aceite explícito de integridade, auth, isolamento e métricas; deployment fora deste documento.

## Critérios de aceite verificáveis

- Um único scheduler mutante por DB+board durante cada modo; concorrência perde sem write e observável.
- Schema e status exatamente compatíveis com upstream (`ready/running/review`, colunas reais), com CAS/transação e eventos/run rows correlatos.
- Claim/heartbeat/completion/retry/review aceitam apenas fencing vigente; cross-profile/board/path e request replay rejeitados.
- Dispatcher Rust shadow não escreve nem spawna; em modo ativo crash/restart recupera intents sem perder task nem spawn duplicado silencioso.
- Spawn usa argv sem shell e profile-secret scope/board/workspace corretos; profile inexistente falha fechado.
- Civilization pause/approval/cancel/recovery mantém semântica atual e nenhum segundo ledger de missão/task é introduzido.
- Métricas com fonte, janela e amostra documentadas; comparação com baseline reproduzível. Sem promessa numérica não medida.
- Suites relevantes passam: testes Kanban DB/dispatcher e workspaces/hooks; testes edge Rust (`cargo test`); testes `MissionSupervisor`/integração; testes de perfil A→B→A; E2E de subprocess/rollback em SQLite temporário. Registrar comandos e saídas reais no futuro relatório, não neste plano.

## Riscos, custos e incógnitas abertas

- Edge atual tem incompatibilidade de schema/status e heartbeat permissivo: não habilitar caminho de escrita existente antes de corrigi-lo. A rota pode usar DB incompatível sem erro explícito ou cair em fallback Python.
- Kernel lock Python é advisory com degradação fail-open; precisamos provar coordenação cross-process e entre Rust/Python/CLI. SQLite serializa writes, mas não assegura scheduler único/sem duplicate spawn.
- Sem outbox atomicamente coordenado, claim commit seguido de crash pode deixar task running sem worker; retry cego pode executar efeitos duplicados.
- Port de `_default_spawn` amplo (profile/segredos/env/cgroup/external workers/restart safety) é custo substancial e superfície de segurança; considerar manter spawn em Python via IPC como etapa intermediária, sob um único supervisor.
- `MissionSupervisor` atual roda seus próprios loops e executa via executor em processo; coexistência com daemon precisa de admission ownership explícita. `MissionStore` e `kanban.db` são estados diferentes com funções distintas, não fundir.
- Board DB path/config/auth/endpoint binding, edge service lifecycle, status migrations e capacidade atual de outbox não foram integralmente inspecionados; confirmar antes de estimar prazo/desenhar schema final.
- Não foram executados testes, benchmarks, canary ou deploy nesta tarefa. Alegações de “sub-1ms”, redução percentual ou produção não são evidência.

## Proveniência e limites

Linhas referem-se ao snapshot deste worktree e podem mudar. Foram inspecionados os fontes indicados e docs locais; não foi feita auditoria exaustiva de todo middleware/router/config de edge. Não foi possível comprovar estado Kanban ativo por ferramenta nesta tarefa; estado do REGISTRY é distinto. A saída é plano de engenharia sujeito a decisão de arquitetura e revisão humana, não aprovação para cutover.