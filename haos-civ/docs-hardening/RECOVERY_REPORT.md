# Relatório de Recuperação da Sessão

A sessão recuperada possuía um plano consistente de V1→V4 e já havia criado dezenas de arquivos. A etapa final pretendia expandir `KERNEL_ARCHITECTURE.md`, `MEMORY_ENGINE_SPEC.md`, `EVOLUTION_ENGINE_SPEC.md` e `COGNITIVE_ARCHITECTURE.md`, mas os últimos comandos apenas imprimiram mensagens; a sessão terminou com `usage-limit-exceeded` do provider.

Também foi validado que muitos arquivos tinham entre ~90 e ~850 bytes, insuficientes para orientar um agente pouco experiente. Esta entrega refaz esses stubs como especificações completas e acrescenta invariantes, failure semantics, idempotência, replay, migration, observabilidade, security e rollout.

O pacote não afirma que código foi implementado no repositório: ele é um plano/RFC executável. Caminhos de integração herdados da sessão precisam ser revalidados no commit alvo antes da edição.

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

