# HAOS V3 — Evolução Controlada

## Objetivo

Permitir que Bots aprendam, especializem e gerem descendentes/versões sem apagar identidade anterior nem transformar cada experiência em auto-modificação.

```text
Execution -> ExperienceEvent -> LessonCandidate -> EvolutionProposal
          -> Governance -> IdentityVersion/Skill change -> Observation window
```

## Três níveis de mudança

1. **operacional:** heurísticas, retrieval weights, tool preferences — aprovação simples/automática dentro de policy;
2. **capability:** skills, specialties, routing — revisão moderada;
3. **identidade:** SOUL/VALUES/constitutive traits — gate humano/constitucional mais forte.

## Lineage

Fork de Bot cria nova identidade com parent refs e export policy. Filho recebe somente memória/skills aprovadas; nunca clona segredos ou memória privada por padrão.

## Exit criteria

- todas as mudanças têm proposal+decision+version;
- rollback preserva histórico;
- sessões antigas continuam reproduzíveis;
- Bot derivado mostra parentage e seed evidence;
- automação não promove mudança identitária crítica sozinha.


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

