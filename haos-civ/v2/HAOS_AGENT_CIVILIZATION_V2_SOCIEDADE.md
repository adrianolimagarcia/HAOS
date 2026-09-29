# HAOS V2 — Sociedade de Agentes

## Objetivo

Transformar indivíduos persistentes da V1 em uma sociedade operacional. V2 introduz relações temporais, reputação baseada em evidências, papéis de Council, protocolos de debate e delegação por competência observada.

## Componentes

- Relationship Graph;
- Reputation Engine por domínio;
- Collaboration Records;
- Council Roles e specialist selection;
- Debate Protocol;
- task delegation accounting;
- collective memory curated.

## Regra crítica

Reputação não é personalidade e não é autoridade automática. É uma projeção explicável de evidências históricas. Decisões políticas do runtime continuam pertencendo à Policy/Governance.

## Protocolos

```text
objective -> decompose -> select diverse members -> independent positions
          -> critique/rebuttal -> synthesis -> dissent -> DecisionRecord
```

## Anti-groupthink

- análise independente antes de ver respostas dos pares;
- máximo de influência do “chair” limitado;
- sempre guardar dissent relevante;
- selector pode reservar vaga para perspective diversity;
- reputação não pode zerar participação de especialista novo quando capability é relevante.

## Exit criteria

- relação e reputação têm provenance;
- selector explica por que cada Bot foi escolhido;
- debate possui fases e budgets;
- replay reconstrói scores/projeções;
- manipular uma única métrica não produz privilégio irrestrito.


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

## Apêndice prático obrigatório

Antes de implementar este componente, o agente deve: localizar o símbolo real no commit alvo; registrar dependências; definir owner do estado; decidir o que é canonical vs projection; documentar idempotency key; definir restart/retry semantics; propagar correlation IDs; indicar feature flag; descrever rollback; e adicionar um teste curto que demonstre uma falha relevante além do happy path.

A implementação deve preferir contratos pequenos e explícitos. Nenhum componente pode depender de texto de prompt para autorização. Nenhuma projeção derivada pode se tornar fonte única de verdade. Qualquer cache usa chave com revisão/versionamento para impedir mistura de identidades ou estado obsoleto.


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

