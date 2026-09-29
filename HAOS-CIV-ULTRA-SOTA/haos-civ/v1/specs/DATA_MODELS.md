# HAOS V1 — Canonical Data Models

## Objetivo
Congelar os contratos mínimos antes de adapters de runtime.

```text
BotIdentityBundle {bot_id, identity_version_id, soul, identity, values, hashes}
IdentityVersion {id, bot_id, version, parent_id, bundle_ref, status}
LeafIdentitySnapshot {leaf_id, parent_bot_id, identity_version_id, temporary_soul_hash, prompt_hash, toolset_hash, memory_snapshot_ref}
CouncilSpec {id, purpose, members, roles, decision_mode, budget, policy_ref}
DecisionRecord {id, council_session_id, participants, evidence_refs, positions, synthesis, dissent, action_refs}
```

IDs devem ser estáveis; cada record possui `schema_version`, timestamps UTC e metadata extensível. Hashes usam serialização canônica. WebUI DTO não é domain model. `IdentityVersion` e `LeafIdentitySnapshot` são imutáveis após ativação/criação.

### Persistência
Índices mínimos por `bot_id+version`, `leaf_id`, `council_id`, `correlation_id`. Foreign keys protegem lineage quando storage suportar. JSON é reservado a extensões, não a campos quentes que precisam de índices/constraints.

## Invariantes de implementação

- Identidade persistente e snapshots são versionados; prompt é projeção, não fonte de verdade.
- Council é multi-Bot; Leaf é temporário e deriva de um Bot pai.
- Eventos são imutáveis; projeções podem ser reconstruídas.
- Toda mutação relevante é idempotente e correlacionada.
- Evolução crítica é proposal + policy + nova versão, nunca self-write silencioso.
- Memória e conhecimento carregam provenance e escopo.
- Feature flags, canary e rollback fazem parte do desenho.


## Harness mínimo

Para cada fatia: validar schema/migration, round-trip, retry sem duplicação, crash/restart, feature flag, rollback, correlation IDs, ausência de secrets em logs e um E2E curto com happy path + falha representativa. Preferir poucos testes de invariantes a uma matriz enorme de testes superficiais.

## Checklist de implementação

1. localizar símbolo real no commit alvo; 2. definir owner do estado e contrato; 3. separar canonical de projection; 4. documentar idempotency/retry/restart; 5. propagar correlation; 6. criar feature flag; 7. documentar rollback; 8. validar compatibilidade legada.

## Nota de completude
Este documento é parte do plano executável e deve ser lido junto do `MASTER_IMPLEMENTATION_PLAN.md`. O implementador deve registrar qualquer desvio por ADR, manter compatibilidade, versionar contratos e preservar auditabilidade ponta a ponta.
