# HAOS Civilization Runtime — Especificação Integrada

## Objetivo

Unificar engines civilizacionais sem criar um monólito acoplado. O runtime é um conjunto de serviços de domínio ligados por eventos e interfaces pequenas.

```text
CivilizationEngine
 ├─ IdentityEngine
 ├─ CouncilEngine
 ├─ MemoryEngine
 ├─ ReputationEngine
 ├─ EvolutionEngine
 ├─ EventBus
 └─ Policy/Governance
```

O `CivilizationEngine` coordena transações de alto nível e não absorve a lógica interna de cada engine. APIs devem trabalhar com commands/queries e DTOs versionados.


## Invariantes arquiteturais

1. **Identidade persistente não é prompt efêmero.** SOUL, IDENTITY e VALUES são estado versionado; o prompt é apenas uma projeção para uma execução.
2. **Prompt caching é um contrato.** Identidade, toolset e memória de bootstrap são resolvidos antes da sessão; mutações em conversa ativa são diferidas, salvo ação explícita e auditável.
3. **Leaf não é um novo cidadão.** É uma manifestação temporária de um Bot, com snapshot imutável de identidade e contexto da missão.
4. **Council é entidade de primeira classe.** Possui `CouncilSpec`, propósito, regras, memória coletiva, protocolo de decisão e `DecisionRecord` próprios.
5. **Tudo que muda estado relevante gera evento.** Estado materializado pode ser reconstruído; eventos carregam `event_id`, causalidade, correlação, ator, escopo e versão de schema.
6. **Idempotência por padrão.** Comandos repetidos não podem duplicar efeitos. Chaves mínimas: `command_id`, `job_id+scheduled_at`, `leaf_id`, `decision_id`.
7. **Proveniência nunca se perde.** Memórias, decisões, reputação, evolução e world-model apontam para evidências e seus produtores.
8. **Evolução é proposta, não mutação silenciosa.** Mudança em SOUL/VALUES exige `EvolutionProposal`, política de aprovação e nova versão.
9. **Compatibilidade progressiva.** BotSpec legado continua válido; recursos civilizacionais entram por campos opcionais e feature flags.
10. **Observabilidade é parte da semântica.** Toda execução importante expõe trace, métricas, custos, modelo, hashes e motivo de decisão.


## Contexto de execução

`CivilizationContext` mínimo: tenant/profile, bot_id, council_id?, leaf_id?, session_id, correlation_id, actor, policy snapshot e deadline. Ele é propagado, não buscado por global.

## Estratégia de degradação

Se Reputation/WorldModel estiver indisponível, V1 direct Bot pode continuar quando policy permitir. Se IdentityResolver ou audit store estiverem indisponíveis para modo civilizacional, execução deve falhar closed: não existe execução “sem saber quem executou”.


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
