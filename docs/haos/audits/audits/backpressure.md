# Auditoria de Arquitetura: Backpressure e Concorrência por Provedor (Frente 3)

## 1. Escopo
Análise dos mecanismos de controle de concorrência e backpressure no HAOS (`hermes/platform/execution/dispatcher.py`, `hermes/platform/models/circuit_breaker.py`, `hermes/platform/execution/scheduler.py` e integração com `hermes_cli/kanban_db_dispatch.py`), cobrindo teto global de workers, limites específicos por provider/modelo de LLM e retenção determinística de tarefas em status `READY`.

## 2. Achados-chave
- **Ausência de Controle por Provedor LLM:** O dispatcher upstream (`hermes_cli/kanban_db_dispatch.py`) apenas regula capacidade por memória do host (`count_running_tasks`, `max_in_progress`, `max_spawn`), ignorando cotas externas de APIs de modelos. Se múltiplas tarefas rodarem em paralelo, disparam requisições simultâneas para o mesmo provedor gerando tempestades de HTTP 429 (Rate Limit).
- **Consumo Indevido de Orçamento de Retries:** No `claim_tick` do `HAOSDispatcher`, tarefas eram claimadas (`claim_task`) antes de qualquer validação de disponibilidade de cota do provedor. Se a chamada falhasse por 429, o card recebia penalização em `consecutive_failures` e podia ser indevidamente marcado como falha sistêmica ou `blocked`.
- **CircuitBreaker Reativo e Desacoplado do Claim:** O `CircuitBreaker` (`hermes/platform/models/circuit_breaker.py`) atuava apenas dentro de `ExactModelRouter.select_route(...)`, levantando `ModelRouteExhaustedException` em tempo de execução dentro do worker em vez de barrar a admissão preventivamente antes do claim.
- **Falta de Retenção Determinística em `READY`:** Sem uma barreira de admissão (admission guard), cards com dependência de rotas congestionadas eram disputados e travados, impedindo a execução de outras tarefas prontas direcionadas a rotas sadias.

## 3. Lacunas Acionáveis
1. **Admission Control Multinível:** Necessidade de checagem em 4 dimensões antes de qualquer lock: teto global de workers, estado do `CircuitBreaker` da rota, cota concorrente do provider (ex: A6API, OpenAI) e cota por família de modelo.
2. **Preservação de Estado `READY`:** Tarefas com capacidade esgotada devem ser mantidas intactas no banco (`status='ready'`, `claim_lock IS NULL`), sem spawn de subprocessos, sem alocação de workspace e sem consumo do orçamento de falhas.
3. **Gerenciamento Seguro de Concorrência (`lease`):** Mecanismo thread-safe de reserva e liberação determinística de capacidade via context manager para prevenir vazamento de slots em casos de exceção ou interrupção.

## 4. Status de Implementação
- **Código Implementado:**
  - `hermes/platform/execution/backpressure.py`: Implementou `ConcurrencyGuard` (e alias `BackpressureController`) com suporte a limites globais, por provider (case-insensitive com fallback configurável), por modelo/rota composta (`family:variant:revision:provider`), integração nativa com `CircuitBreaker` (`circuit_breaker_open`), verificação de admissão read-only (`check_admission`), aquisição atômica (`acquire`/`release`), context manager de ciclo de vida seguro (`lease`) e telemetria (`stats`).
- **Testes Unitários:**
  - `tests/platform/execution/test_backpressure.py`: Suíte com 5 testes aprovados (`OK`), validando:
    1. Teto global (`test_global_concurrency_limit`).
    2. Teto por provider isolando A6API e OpenAI (`test_provider_concurrency_limit`).
    3. Teto por modelo (`test_model_concurrency_limit`).
    4. Rejeição preventiva integrada ao CircuitBreaker (`test_circuit_breaker_integration`).
    5. Liberação automática via context manager (`test_lease_context_manager`).
- **Ponto de Integração Mapeado:**
  - `HAOSDispatcher.claim_tick` (`hermes/platform/execution/dispatcher.py`): Injeção do `ConcurrencyGuard` no loop de seleção para checar `check_admission` antes de chamar `kb.claim_task`.

## 5. Recomendações
1. **Plugar `ConcurrencyGuard` no `HAOSDispatcher`:** Adicionar `concurrency_guard: Optional[ConcurrencyGuard] = None` no construtor de `HAOSDispatcher` e utilizá-lo como gate de admissão antes de `kb.claim_task` no `claim_tick`.
2. **Configuração via `config.yaml`:** Mapear limites de concorrência sob a seção `execution.concurrency` (`max_active_workers`, `provider_limits`, `model_limits`), alimentando o guard na inicialização do runtime.
3. **Telemetria de Backpressure:** Expor o snapshot de `guard.stats()` em eventos estruturados no `EventStore`/`EventBus` para acompanhamento de saturação de rotas no dashboard.
