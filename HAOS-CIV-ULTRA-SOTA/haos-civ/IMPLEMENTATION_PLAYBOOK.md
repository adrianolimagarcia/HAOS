# HAOS Civilization — Implementation Playbook para Agente Executor

Este documento converte o plano arquitetural em comportamento operacional. O objetivo é impedir que um agente implemente dezenas de arquivos sem manter estado, dependências, evidência de testes e rollback.

## 1. Contrato de execução

O agente deve trabalhar em fatias verticais pequenas. Cada fatia precisa atravessar, quando aplicável: domain model → persistence → service → event → API/CLI → observability → tests. Não crie uma camada inteira de abstrações “para usar depois”.

Antes de qualquer edição, leia [`README.md`](README.md) e mantenha `IMPLEMENTATION_STATUS.md` baseado em [`IMPLEMENTATION_STATUS_TEMPLATE.md`](IMPLEMENTATION_STATUS_TEMPLATE.md).

## 2. Loop por fatia

### A. Discover
- localizar símbolos/callers reais com search/grep/LSP;
- registrar commit SHA e paths;
- mapear estado canônico e projeções;
- localizar testes existentes relevantes;
- identificar compatibilidade pública/API.

### B. Contract
Escreva no status da task:
- input/output;
- owner do estado;
- lifecycle;
- idempotency key;
- correlation IDs;
- error taxonomy;
- recovery/restart semantics;
- security/policy boundary;
- feature flag;
- rollback.

### C. Implement
Prefira adicionar comportamento sob flag e reutilizar hooks existentes. Não introduza runtime, event bus ou memory store paralelo sem ADR explícito.

### D. Verify
No mínimo: unit contract, integration path, failure path, retry/idempotency e restart quando houver estado persistente.

### E. Observe
Trace/log/metric devem carregar IDs suficientes para provar o fluxo. Evite conteúdo sensível.

### F. Roll back
Teste downgrade/flag-off/compensation antes de declarar concluído.

### G. Record
Atualize status, links para commits/PR, testes, benchmark e ADR.

## 3. Barriers

### Barrier B0 — Baseline congelado
Saída: code map atualizado + testes baseline + commit alvo.

### Barrier B1 — Contracts congelados
Saída: models + schemas + migration plan + event envelope. Nenhum consumidor deve continuar usando payloads ad-hoc.

### Barrier B2 — Canonical persistence/replay
Saída: migrations, append-only semantics, projection rebuild, idempotency primitive.

### Barrier B3 — Identity stable
Saída: resolver, prompt bootstrap, session freeze e isolation tests.

### Barrier B4 — Leaf stable
Saída: snapshot/lineage/retry/cancel/restart.

### Barrier B5 — Council stable
Saída: CouncilSession + independent positions + DecisionRecord + policy separation.

### Barrier V1
Executar exit gate completo antes de V2.

### Barrier V2
Relationship/reputation/roles/debate/delegation auditáveis antes de experience/evolution.

### Barrier V3
Versioning/lineage/proposal apply robustos antes de civilization memory/constitution.

### Barrier V4
Recovery + policy + world model + maintenance completos antes de default-on.

## 4. Estratégia de branches/PR

Cada PR deve ter uma responsabilidade arquitetural, não somente diretório. Uma divisão recomendada está no README. Rebase/merge entre barriers, não mantenha branches divergentes de schema por longos períodos.

Para trabalho paralelo:
- um owner altera schema/contract;
- consumidores aguardam contract freeze ou usam generated fixture estável;
- cada lane tem paths preferenciais;
- integração acontece em barrier branch;
- conflitos conceituais geram ADR, não merge manual silencioso.

## 5. Definition of Ready de uma task

Task só entra em implementação quando:
- requisito é rastreável em [`TRACEABILITY_MATRIX.md`](TRACEABILITY_MATRIX.md);
- dependências estão verdes;
- paths reais foram localizados;
- owner do estado está definido;
- existe test oracle claro;
- rollback é possível.

## 6. Definition of Done de uma task

- code + tests passam;
- contract atualizado;
- migration reversível;
- event/versioning documentados;
- retry não duplica efeitos;
- observabilidade adicionada;
- security review quando há tool/secret/action;
- benchmark quando toca hot path;
- status atualizado;
- nenhuma TODO crítica oculta.

## 7. Política de compatibilidade

Alterações civilization-first devem ser opcionais até gate de promoção. BotSpec legado não deve exigir SOUL files. Missing identity deve cair em fallback explícito e observável, não em comportamento implícito diferente.

## 8. Política de migrations

- migrations numeradas e monotônicas;
- up/down testado onde storage permite;
- backfill idempotente;
- nenhuma migration longa no startup sem budget;
- schema version em evento e records persistidos;
- remover coluna/tabela só após janela de compatibilidade.

## 9. Política de eventos

Cada mutação importante declara:
- comando que a originou;
- event type/version;
- stream/aggregate;
- causation/correlation;
- idempotency/dedupe;
- consumer side effects;
- replay behavior.

Consumidor deve tolerar evento repetido. Unknown future version deve falhar de forma explícita/isolada, não interpretar silenciosamente.

## 10. Política de memória

Memória é dado não confiável com provenance. O storage canônico e os índices/GraphRAG devem permanecer separáveis. Reindex precisa ser possível sem perder record original. Scopes privados não podem vazar para Council/global por conveniência.

## 11. Política de Council

Separar claramente:
- seleção de membros;
- geração independente;
- debate;
- síntese;
- decision record;
- autorização/policy;
- execução.

Não permitir que síntese do LLM seja também o policy engine.

## 12. Política de evolução

Experience gera candidate; candidate pode virar proposal; proposal é validada; apply cria nova versão; rollout mede; rollback cria estado compensatório. Pule qualquer estágio apenas se policy explícita definir isso para uma classe de mudança de baixo risco.

## 13. Performance discipline

Medir antes/depois para mudanças em prompt bootstrap, memory retrieval, event write, Leaf create e Council orchestration. Evitar chamadas de rede no caminho legado quando civilization features estão off.

Budgets iniciais sugeridos devem ser calibrados no hardware real:
- identity resolution quente: single-digit milliseconds;
- event append: bounded/local, sem aguardar consumers não críticos;
- tracing assíncrono quando seguro;
- retrieval: budget fixo de resultados/tokens;
- Council fanout: concurrency limit configurável.

## 14. Stop conditions

Pare a implementação de uma fatia e corrija arquitetura se ocorrer:
- duas fontes canônicas para o mesmo estado;
- retry cria ação duplicada;
- versão de identidade muda no meio da sessão;
- Leaf consegue editar parent SOUL;
- policy depende do LLM obedecer prompt;
- projection não consegue ser rebuild;
- segredo aparece em evento/snapshot/log;
- feature flag off ainda altera comportamento legado;
- migration exige apagar histórico para rollback.

## 15. Handoff entre agentes

Antes de outro agente assumir, gere um handoff usando [`AGENT_HANDOFF_TEMPLATE.md`](AGENT_HANDOFF_TEMPLATE.md), incluindo branch/commit, barrier atual, tasks verdes/vermelhas, testes, decisões e próximo comando/arquivo recomendado. O sucessor deve conseguir continuar sem reler a conversa original.
