# HAOS Civilization — Ordem de Implementação

> **Navegação canônica:** comece por [`README.md`](README.md). Este documento é uma especialização do mapa mestre; não deve ser usado isoladamente para pular barriers/gates.

## Caminho crítico

1. `EventEnvelope` e IDs/correlação.
2. migrations e repositories básicos.
3. `BotIdentity`/`IdentityVersion`.
4. `IdentityResolver` + cache e hashes.
5. integração no bootstrap do AIAgent.
6. `LeafIdentitySnapshot` no ShadowLeaf.
7. `DecisionRecord` e evidence refs.
8. `CouncilSpec`/`CouncilRuntime` mínimo.
9. memory isolation e Council memory.
10. relationship/reputation/debate.
11. experience/evolution/lineage.
12. civilization memory/KG/world model.
13. constitution/policy/autonomous maintenance.

## Paralelismo seguro

Após contratos e migrations, podem avançar em paralelo: Identity, Event Bus, storage adapters e observability. Council depende de identidade + eventos. Reputation depende de evidence/decision. Evolution depende de experience + versioning. V4 depende de todas as anteriores.

## Estratégia de PRs

Preferir PRs verticais de 300–900 linhas funcionais em vez de megamigração. Cada PR deve ter: feature flag; migration; docs do contrato; E2E curto; rollback notes. Squash somente se preservar rastreabilidade de migration/release notes.

## Gates de promoção

- **Shadow:** grava novo estado sem influenciar decisão.
- **Opt-in:** um Bot/Council explícito usa runtime novo.
- **Canary:** subset estável por 3–7 dias ou volume equivalente.
- **Default-on:** novos Bots usam civilization mode, legados continuam compatíveis.
- **Legacy removal:** só após export/import e telemetria mostrar ausência de uso.


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
