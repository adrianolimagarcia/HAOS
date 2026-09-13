# Auditoria: DSH Lifecycle/Replay — gap-port clean-room para HAOS (Frente 3)

## 1. Escopo
Auditoria read-only do host de extensões e da maquinaria de evento/trace/replay do DeepSeek Harness (clone em `TEMP/references/deepseek-harness`) para fechar lacunas no scaffold HAOS:
- `hermes/platform/extensions/registry.py` (ExtensionManifest + ExtensionRegistry, estados PENDING→LOADING→ACTIVE→UNLOADING→DISPOSED; gate de dependência por capability; bridge para CapabilityRegistry) e `hermes_bridge.py` (discovery manifest-only de plugins Hermes via `hermes_cli.plugins_discovery`).
- `hermes/platform/observability/` (`events.py`, `event_store.py`, `traces.py`, `replay.py`, `metrics.py`), em especial `EventReplayer.replay_trace`.
Fonte DSH: `vendor/cordis/src/{fiber,registry,reflect,events}.ts` (framework cordis) e `packages/extensions/cordis-host-runner/src/*` (host de "packages dinâmicos"); log de sessão em `packages/core/session/` (`types.ts`, `surface.ts`, `repair.ts`).

## 2. Achados-chave
1. **Estados reais (cordis FiberState):** `PENDING (aguardando serviços requeridos) → LOADING → ACTIVE → FAILED | UNLOADING → DISPOSED` (`vendor/cordis/src/fiber.ts:147-154`). O LIFECYCLE do HAOS é exatamente esse enum **menos FAILED**. Transições são dirigidas por "epoch" do inject e eventadas via `internal/status` (`fiber.ts:586`).
2. **Resolução de dependência ≠ ordenação topológica:** é *probe de disponibilidade de serviço + retry dirigido a evento*. Quem declara `inject` espera (PENDING); quando o provedor aparece, `reflect.notify` re-checa todos os fibers e promove o consumidor (`reflect.ts:277-336`; `fiber.ts:597-639`). Provedor parado ⇒ dependente volta a PENDING e desfaz registros (pin: `cordis-host-runner/tests/composition.spec.ts:42-71`). Deps opcionais: `ctx.get()` com check de undefined — nunca gate de ativação (`sandbox.ts:18-21`). Duplicar provide falha alto ("service has been registered") (`reflect.ts:289-291`).
3. **Rollback de ativação:** "never leave a failed fiber mounted" (`cordis-host-runner/src/lifecycle.ts:1-8,22-45`): erro no start ⇒ dispose do fiber + unwind dos handler disposers; `commitActivation` só grava `currentPackageId` no sucesso completo (index.ts:981-992). Teardown LIFO; provedor aguarda dependentes descarregarem antes de remover o próprio impl (`reflect.ts:297-303`).
4. **Eventos de ciclo de vida:** `internal/status|plugin|service` + broadcasts `cordis/request-run`, `cordis/request-run-resolved`, `cordis/dynamic-package`, `cordis/dynamic-retract` (index.ts:857-862,1010-1017,1225-1229). Dispatch modes `emit|parallel|serial|bail|waterfall` (`vendor/cordis/src/events.ts:25-32`).
5. **Replay DSH é reconstrução determinística, nunca re-execução de efeitos:** log de sessão append-only, seq contíguo, histórico de mensagens *derivado* por fold puro (`deriveEventMessage`/`foldSurface`, `packages/core/session/src/surface.ts:77-121,394-414`; envelope em `types.ts:447-476`). Política fail-closed: evento desconhecido SEM marcador `ignorable` ⇒ recusa reconstruir (`types.ts:456-465`). Crash tail: `interruptedTurnClosers` sintetiza closers determinísticos (`repair.ts:29-134`).
6. **Testes DSH que fixam contratos:** lifecycle — `packages/extensions/cordis-host-runner/tests/{composition,runner,versioning,sandbox-context,sandbox}.spec.ts`, `packages/extensions/tool-cordis/tests/cordis-lifecycle.spec.ts`; log/replay — `packages/core/session/tests/{surface,repair,fork,session,seq-ranges,json,invariant}.spec.ts`, `packages/core/agent-loop/tests/resume.spec.ts`, `packages/api/session-controller/tests/{session-history-journal.host,projection-store.client,queue-store.client}.spec.ts`.

## 3. Lacunas Acionáveis
Tags: (a) portar clean-room; (b) já coberto upstream/HAOS — não duplicar; (c) fora de escopo para scaffold stdlib-only.
- **L1 (a)** Sem estado FAILED nem diagnóstico por tentativa (fase+mensagem); `activate()` levanta mas não registra (`registry.py:69-84`).
- **L2 (a)** Sem ativação "parqueada": DSH trata run com serviço ausente como sucesso pendente que auto-ativa quando o serviço chega; HAOS levanta RuntimeError (`registry.py:73-80`).
- **L3 (a)** Sem histórico de estados/identidade de tentativa (DSH: pluginRunId + attempt com waitingFor/fase; version pointers current/next).
- **L4 (a)** Transições silenciosas — sem eventos de lifecycle (additive: `subscribe` + EventSource/EventStore).
- **L5 (a)** Sem rollback parcial: bridge de capabilities vaza se ativação falha no meio (sem try/finally; `registry.py:81-83`).
- **L6 (a)** `deactivate()` sem cascade e sem ordem dependente-primeiro (provedor some com dependentes ACTIVE; `registry.py:86-93`).
- **L7 (a)** Re-ativação pós-DISPOSED indefinida (DSH: DISPOSED não reinicia — `fiber.ts:351-354`); falta `stop()` não-terminal (cordis_stop).
- **L8 (a)** Sem guarda de transição concorrente ("transition-in-flight"); `requires` com prefixo não-`capability:` (`service:z`) é silenciosamente ignorado; `optional` sem semântica (`registry.py:36,73-80`).
- **L9 (a)** Bridge duplicado de capability é last-writer-wins no CapabilityRegistry (`capabilities/registry.py:41-42` sobrescreve providers); DSH falha alto ou exigiria union.
- **L10 (a/b)** EventStore ordena só por timestamp (`event_store.py:68`) — sem seq monotônico/contiguidade; `replay_trace` é re-aplicação de efeito aberta, sem ordem garantida, sem política de evento desconhecido, sem posição de falha (`replay.py:9-15`).
- **L11 (b)** Bridge trata `PluginManifest.capabilities` (consent metadata, NÃO grant — `hermes_cli/plugins_manifest.py`) como provides real; registre split declared/granted em permissions.
- **L12 (b/c)** Não portar: loader/ordenação/grants reais (upstream `hermes_cli/plugins_{loader,state,dispatch,ledger}.py`); sandbox VM+guardas e approval/client-half (DSH JS); OTLP/exporters; reparo de turno de agente (ledger de sessão upstream). EventStore permanece observability-only — Kanban/plugins_ledger/hermes_state já são os ledgers.

## 4. Status de Implementação
Nenhuma lacuna L1–L10 foi implementada — auditoria entregou apenas o mapa (sem alterações de código). Baseline atual que os testes fixam:
- `extensions/registry.py` (v1.1 Emenda 23/24): 5 estados, `activate` com gate de capability (raise), `deactivate→DISPOSED`, bridge via `CapabilityRegistry` (registro sobrescreve providers).
- `hermes_bridge.py`: discovery manifest-only (sem import de código do plugin); `requires=[]` mapeado vazio.
- `observability/event_store.py` (sqlite `:memory:`/arquivo; PK `event_id`; índices trace/correlation); `replay.py::EventReplayer.replay_trace` (fold side-effectful por trace_id).
- Testes que pinam o estado atual: `tests/platform/test_extensions_evals.py` (deactivate→DISPOSED :56; activate sem cap levanta :52), `tests/platform/test_hermes_plugins_bridge.py` (:73-78), `tests/platform/observability/test_observability.py` (:35-37 replay_trace) — novos métodos devem ser aditivos para não quebrá-los.

## 5. Recomendações
Escopo mínimo aditivo, stdlib-only, preservando nomes públicos (`ExtensionManifest`, `ExtensionRegistry`, `Event`, `EventStore`, `EventReplayer`, `replay_trace`):
1. `hermes/platform/extensions/registry.py`: constante `FAILED`; `ExtensionEvent` + `subscribe()`; `state_history(id)`; `activate_with_deps(id, on_missing="raise"|"hold")` com waitlist por capability ausente e auto-retry ao bridge chegar (espelha `reflect.notify`); rollback try/finally em `activate()` (desfaz caps bridgeadas, retorna a PENDING/FAILED); `deactivate(id, cascade=True)` dependente-primeiro; `stop(id)` não-terminal; guarda legal de transições + in-flight (RLock).
2. `hermes/platform/observability/event_store.py`: coluna `seq` monotônica + índice `(trace_id, seq)`; ORDER BY determinístico; `events_after(cursor)`.
3. `hermes/platform/observability/replay.py`: `replay_fold(trace_id, projector, initial)` puro/determinístico + política `on_unknown` (fail-closed espelhando `ignorable`); posição de falha reportada; `replay_trace` mantido.
4. `hermes_bridge.py`: só metadados aditivos (`requires_plugins`, split declared/granted em permissions).
5. Testes invariante (unittest, sem change-detector) em `tests/platform/extensions/` e `tests/platform/observability/`: rollback sem caps parciais; hold→auto-promoção; cascade devolve dependente a PENDING; seq estritamente crescente; replay_fold idempotente/determinístico; erro em evento N reporta N-1.
Prioridade: L1+L3+L4 (histórico/eventos, ~50 linhas) → L2+L5+L6 (hold/rollback/cascade, ~120 linhas) → L10 (seq + replay_fold, ~80 linhas).
