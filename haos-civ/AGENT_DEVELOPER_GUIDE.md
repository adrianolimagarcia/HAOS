# HAOS Civilization — Guia do Agente Implementador

> **Navegação canônica:** comece por [`README.md`](README.md). Este documento é uma especialização do mapa mestre; não deve ser usado isoladamente para pular barriers/gates.

## Como usar este pacote

Comece em `MASTER_IMPLEMENTATION_PLAN.md`; depois leia a fase alvo e o spec de runtime correspondente. Não implemente a partir de um único Markdown isolado.


## Mapeamento para o HAOS/Hermes existente

Os pontos abaixo foram os pontos de integração validados na sessão recuperada e devem ser reconfirmados pelo implementador antes de editar, porque o repositório pode evoluir:

- `hermes/platform/bots/spec.py` — contrato de `BotSpec` e compatibilidade de serialização;
- `hermes/platform/bots/manager.py` — registro/lifecycle/eventos do Bot;
- `hermes/platform/shadow_leaf.py` — execução temporária isolada e vínculo com o Bot pai;
- `agent/agent_init.py` — construção/inicialização de `AIAgent`;
- `agent/system_prompt.py` — montagem do prompt e ponto sensível de cache;
- novo `hermes/platform/bots/identity.py` — modelos de identidade;
- novo `hermes/platform/bots/identity_resolver.py` — resolução única de SOUL/IDENTITY/VALUES;
- novo `hermes/platform/council/` — especificação, manager, runtime, memória e decisões.

Nunca introduzir um segundo runtime paralelo se um hook existente puder carregar a nova semântica.


## Regras de edição

- localizar o hook real antes de criar novo módulo;
- manter compatibilidade de serialization e defaults;
- migrations são append-only e reversíveis;
- qualquer alteração de prompt deve justificar impacto no cache;
- eventos e modelos possuem versão explícita;
- evitar globals mutáveis; usar dependências injetáveis;
- I/O async ou background não pode bloquear streaming do agente;
- qualquer fanout de Leafs precisa de budget, cancellation e limite de concorrência;
- segredo nunca entra em snapshot textual; usar secret refs.

## Checklist antes do commit

1. arquivo/fluxo real revalidado;
2. schema/migration atualizados;
3. feature flag adicionada se muda comportamento;
4. tracing IDs propagados;
5. retry/idempotency pensados;
6. failure path demonstrado;
7. docs do contrato atualizadas;
8. release/version bump segue política do repositório em uso.


## Harness de execução e qualidade

O plano deve ser implementado em fatias verticais pequenas, cada uma atravessando modelo de dados, serviço, evento, persistência, API/CLI e observabilidade. O objetivo não é maximizar quantidade de testes, e sim impedir classes inteiras de regressão com gates baratos.

**Gates mínimos por fatia:**

- schema e migração aplicam e revertem;
- serialização round-trip;
- replay de eventos produz o mesmo estado;
- retry não duplica efeito;
- crash/restart preserva invariantes;
- feature flag permite rollback funcional;
- logs/traces carregam `bot_id`, `council_id`, `leaf_id`, `decision_id` quando aplicável;
- snapshot de prompt/identidade permanece estável durante a sessão;
- uma simulação E2E curta prova o caminho feliz e um caso de falha.

**Estratégia de rollout:** `shadow -> opt-in -> canary -> default-on -> remove-legacy`. Cada transição possui métrica de promoção e condição explícita de rollback.
