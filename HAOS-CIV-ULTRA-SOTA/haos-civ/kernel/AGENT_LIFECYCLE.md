# Kernel — Agent Lifecycle

## Objetivo

Definir estados operacionais de Bots sem misturar lifecycle com identity version.


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


## Responsabilidades

- draft/active/suspended/evolving/deprecated/archived
- transition policy
- resource cleanup
- session admission

## Modelo de dados / contrato

```text
BotLifecycleState {bot_id,state,revision,reason,changed_by,at}
```

O modelo deve possuir IDs estáveis, `schema_version`, timestamps em UTC, campos de causalidade e uma representação serializável que possa ser persistida e reproduzida. Campos extensíveis devem ser `metadata`/`extensions` namespaced, evitando mudanças destrutivas a cada nova capacidade.

## Fluxo operacional

```text
command -> validate state -> event -> projection -> side effects
```

Em cada transição, validar precondições antes de emitir efeitos externos. Persistir o comando/evento antes de publicar efeitos assíncronos quando a durabilidade for necessária. Se houver side effects externos, registrar intent/outbox para retry seguro.

## Integrações

- BotManager
- Evolution
- Scheduler

## Concorrência, consistência e idempotência

- optimistic concurrency por `revision` ou `etag` em agregados persistentes;
- `command_id` obrigatório para operações mutáveis expostas por API;
- deduplicação de eventos por `event_id`;
- outbox/inbox transacional quando armazenamento e publicação de evento precisarem ser atômicos;
- locks/leases apenas em recursos realmente exclusivos; preferir ownership explícito e compare-and-swap;
- nenhum handler assume entrega exactly-once; a semântica alvo é at-least-once + processamento idempotente.

## Observabilidade

Emitir eventos e spans com IDs de correlação. Métricas mínimas: latência p50/p95/p99, taxa de sucesso, retries, conflitos, backlog, tempo de replay e cardinalidade das entidades. Dados sensíveis do prompt/memória não entram em logs por padrão; armazenar hashes e referências.

## Falhas e comportamento esperado

- **archive with running leafs:** stop admission; drain/cancel by policy

## Critérios de aceite

- [ ] invalid transitions blocked
- [ ] restart preserves state


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
