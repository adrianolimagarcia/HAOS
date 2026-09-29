# HAOS V1 — Agent Identity, Council e Leafs Temporários

## Visão

V1 transforma o Bot persistente em unidade civilizacional. O Bot possui identidade própria; o Council coordena múltiplos Bots; Leafs continuam temporários e derivam uma SOUL de missão totalmente rastreável.


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


## Estrutura persistente sugerida

```text
~/.hermes/bots/<bot_id>/
  BotSpec.yaml
  SOUL.md
  IDENTITY.md
  VALUES.md
  MEMORY.md
  EXPERIENCE.md
  RELATIONSHIPS.md
  state.db
```

## Composição da SOUL do Leaf

```text
temporary_soul = base_soul(bot_identity_version)
               + task_specialization
               + execution_constraints
               + council_context
```

O resultado é congelado em `LeafIdentitySnapshot`; o texto final pode ser armazenado cifrado/restrito e o audit log sempre guarda hash, versão e references.

## Council V1

`CouncilSpec`: id, purpose, members/selector, roles, decision_mode, budget, max_concurrency, memory_scope, policy_ref. `CouncilSession`: objective, member snapshots, leaf runs, evidence set, positions, synthesis, dissent e status.

## Sequência

1. contratos/migrations;
2. identity loader/resolver;
3. prompt bootstrap;
4. Leaf snapshot;
5. events/audit;
6. Council runtime mínimo;
7. DecisionRecord;
8. UI/CLI de inspeção e rollback.


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


## Exit criteria V1

- dois Bots com Souls distintas não vazam identidade/memória;
- Leaf mostra parent, versão e hashes;
- Council usa 2+ Bots reais e preserva posições antes da síntese;
- restart/retry não cria duplicatas;
- legacy Bot mantém comportamento anterior;
- operador consegue navegar uma decisão até cada execução Leaf.


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
