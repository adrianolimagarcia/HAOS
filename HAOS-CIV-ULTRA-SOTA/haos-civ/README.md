# HAOS Civilization — README / Mapa Mestre de Implementação

> **COMECE AQUI.** Este arquivo é o único ponto de entrada canônico do pacote. O agente implementador deve lê-lo inteiro antes de editar código. Os demais documentos são especializações deste mapa e são abertos na ordem indicada abaixo.

## 1. Objetivo do pacote

O objetivo não é adicionar “multi-agent” superficial ao HAOS. É introduzir uma camada civilizacional auditável em quatro versões progressivas sem quebrar o runtime Hermes/HAOS existente:

```text
V1 — Fundação      -> Bots viram indivíduos persistentes com identidade, Leafs rastreáveis e Councils reais.
V2 — Sociedade     -> indivíduos formam relações, reputação, especialização, debate e delegação.
V3 — Evolução      -> aprendizado produz propostas versionadas, lineage e lifecycle governado.
V4 — Civilização   -> memória institucional, world model, constituição e manutenção autônoma governada.
```

A implementação é **incremental, feature-flagged, event-driven, idempotente, recuperável e observável**. Não deve existir big-bang rewrite.

### Resultado esperado

Ao final, um operador deve conseguir responder, usando dados persistidos e não inferência:

- qual Bot participou de uma execução;
- qual versão exata de `SOUL`, `IDENTITY` e `VALUES` estava ativa;
- quais Leafs temporários foram derivados e de qual identidade vieram;
- qual Council coordenou a decisão e sob quais regras;
- quais evidências, memórias e posições sustentaram o resultado;
- quais ações foram autorizadas ou negadas por policy;
- como reputação/evolução foram calculadas;
- como reconstruir estado por replay;
- como reverter uma versão/feature sem reescrever histórico.

---

## 2. Regra de navegação: não leia o pacote aleatoriamente

Use esta sequência. Um arquivo posterior pressupõe que os contratos anteriores já foram entendidos.

### Trilha canônica

1. **Este README** — mapa, ordem, dependências e critérios de parada.
2. [`MASTER_IMPLEMENTATION_PLAN.md`](MASTER_IMPLEMENTATION_PLAN.md) — visão end-to-end e arquitetura alvo.
3. [`ARCHITECTURE_EVOLUTION_MAP.md`](ARCHITECTURE_EVOLUTION_MAP.md) — limites entre V1/V2/V3/V4 e o que não antecipar.
4. [`IMPLEMENTATION_PLAYBOOK.md`](IMPLEMENTATION_PLAYBOOK.md) — loop operacional do agente, PRs, checkpoints e sequência executável.
5. [`DEPENDENCY_GRAPH.md`](DEPENDENCY_GRAPH.md) — o que pode ser paralelo e quais barreiras são obrigatórias.
6. [`TRACEABILITY_MATRIX.md`](TRACEABILITY_MATRIX.md) — requisito → spec → implementação → teste → gate.
7. [`TEST_AND_CHAOS_HARNESS.md`](TEST_AND_CHAOS_HARNESS.md) — suíte mínima, caos, replay, idempotência, performance e rollback.
8. [`docs-hardening/THREAT_MODEL.md`](docs-hardening/THREAT_MODEL.md) e [`kernel/SECURITY_BOUNDARIES.md`](kernel/SECURITY_BOUNDARIES.md) — antes de qualquer ação/tool privilegiada.
9. Implementar **V1**, passar o gate V1; só então V2.
10. Implementar **V2**, passar o gate V2; só então V3.
11. Implementar **V3**, passar o gate V3; só então V4.
12. Implementar **V4**, executar gate global e rollout.

Se houver conflito entre documentos, prevalece esta ordem de autoridade:

```text
invariantes deste README
  > MASTER_IMPLEMENTATION_PLAN
    > RFC/spec da versão
      > runtime/kernel spec
        > task list
          > exemplo
```

Exemplos YAML/JSON/SQL **não são contratos normativos**; são fixtures de referência.

---

## 3. Invariantes que nunca podem ser quebrados

1. **Bot é o indivíduo persistente.** `SOUL`, `IDENTITY`, `VALUES`, memória, experiência, relações e lineage pertencem ao Bot.
2. **Leaf é efêmero.** Recebe uma SOUL temporária derivada, snapshot imutável da identidade e contexto mínimo da missão. Leaf não vira cidadão por existir.
3. **SOUL temporária não muta a SOUL do pai.** Qualquer aprendizado volta como evidência/proposta, nunca como edição silenciosa.
4. **Council é entidade de primeira classe.** Tem `CouncilSpec`, propósito, membros, roles, protocolo, memória e `DecisionRecord` próprios.
5. **Identidade persistente não é prompt.** Prompt é projeção de uma versão de identidade para uma sessão.
6. **Identidade da sessão é congelada.** Uma mutação de identidade não altera silenciosamente uma execução ativa.
7. **Estado relevante gera evento.** Eventos são append-only; projeções podem ser reconstruídas.
8. **Retry não duplica efeito.** Todo comando mutável define idempotency key.
9. **Proveniência é obrigatória.** Memória, reputação, decisão, lesson, evolução e world-model apontam para evidências.
10. **Evolução é proposta versionada.** SOUL/VALUES não sofrem self-modification silenciosa.
11. **Policy supera opinião do modelo.** Um Council não pode ampliar permissões por consenso.
12. **Compatibilidade é progressiva.** Legacy mode continua funcional até a remoção deliberada.
13. **Observabilidade faz parte do contrato.** IDs, hashes, versão, custo, latência, modelo e causalidade são propagados.
14. **Canonical state ≠ projection.** Nenhuma view, cache, embedding ou grafo derivado é a única fonte de verdade.
15. **Rollback preserva história.** Restaurar conteúdo cria nova versão/estado compensatório; não apaga fatos passados.

---

## 4. Glossário operacional

| Termo | Significado operacional |
|---|---|
| **Bot** | Entidade persistente, versionada e socialmente identificável. |
| **IdentityVersion** | Snapshot imutável de identidade do Bot. |
| **SOUL** | Personalidade, princípios e estilo estável; não é autorização. |
| **VALUES** | Prioridades/limites decisórios; continuam subordinados à policy. |
| **Leaf** | Executor temporário derivado de um Bot para uma missão delimitada. |
| **LeafIdentitySnapshot** | Prova imutável de quem originou um Leaf e em que versão/contexto. |
| **Council** | Agregado persistente que coordena múltiplos Bots por protocolo explícito. |
| **CouncilSession** | Instância de decisão/debate de um Council. |
| **DecisionRecord** | Registro de posições, evidências, síntese, dissenso, policy e ações. |
| **Event Envelope** | Envelope imutável com schema version, causalidade e correlação. |
| **Projection** | Estado materializado reconstruível a partir do canonical source/eventos. |
| **ExperienceEvent** | Evidência de execução que pode alimentar aprendizado. |
| **EvolutionProposal** | Diff governado proposto para comportamento/identidade/capability. |
| **Lineage** | Relação de origem entre versões/Bots especializados. |
| **Civilization Memory** | Memória coletiva tipada: episódica, semântica, procedural, institucional e social. |
| **Constitution** | Policy versionada da civilização; deve ter enforcement operacional. |

---

## 5. Antes de editar: Fase 0 — Reconhecimento e congelamento de baseline

**Não implemente nada antes deste gate.** O pacote foi reconstruído a partir de uma sessão anterior e o repositório real pode ter mudado.

### 5.1 Leia

- [`runtime/implementation/integration/HERMES_HAOS_RUNTIME_MAPPING.md`](runtime/implementation/integration/HERMES_HAOS_RUNTIME_MAPPING.md)
- [`v1/architecture/HAOS_AGENT_IDENTITY_COUNCIL_CODE_MAPPING.md`](v1/architecture/HAOS_AGENT_IDENTITY_COUNCIL_CODE_MAPPING.md)
- [`docs-hardening/ARCHITECTURE_REVIEW.md`](docs-hardening/ARCHITECTURE_REVIEW.md)
- [`API_CONTRACTS.md`](API_CONTRACTS.md)

### 5.2 Reconstrua o mapa real do repositório

Valide no commit alvo, sem assumir que paths antigos continuam idênticos:

```text
BotSpec / bot registry / bot manager
ShadowLeaf / subagent / worker creation
AIAgent construction
system prompt construction/cache
memory providers + GraphRAG hooks
scheduler / event / transport boundaries
API + WebUI paths
persistence/migrations
tracing/logging
feature-flag mechanism
```

Na sessão recuperada, os pontos prováveis eram:

```text
hermes/platform/bots/spec.py
hermes/platform/bots/manager.py
hermes/platform/shadow_leaf.py
agent/agent_init.py
agent/system_prompt.py
```

**Isso é pista, não licença para editar sem localizar os símbolos reais.**

### 5.3 Registre baseline

Antes da primeira mudança capture:

- commit SHA e branch;
- suíte de testes existente e seu estado;
- latência/TTFB de um turno simples;
- consumo de memória aproximado;
- comportamento de Bot legado;
- criação atual de ShadowLeaf/subagent;
- schema de persistência atual;
- como IDs/correlation são gerados;
- como restart/cancel/retry funcionam.

Crie uma cópia de [`IMPLEMENTATION_STATUS_TEMPLATE.md`](IMPLEMENTATION_STATUS_TEMPLATE.md) como `IMPLEMENTATION_STATUS.md` no workspace do agente e atualize-a a cada fatia.

### Gate F0

Só prossiga se o agente consegue apontar **um único owner** para cada estado relevante e sabe onde integrar sem criar runtime paralelo.

---

# PARTE I — V1: FUNDAÇÃO

## 6. Objetivo V1

V1 estabelece indivíduos persistentes, Leafs rastreáveis e Council mínimo. Não implemente reputação/evolução/world-model aqui.

### Documentos normativos V1

Leia nesta ordem:

1. [`v1/vision/HAOS_AGENT_IDENTITY_COUNCIL_PLAN.md`](v1/vision/HAOS_AGENT_IDENTITY_COUNCIL_PLAN.md)
2. [`v1/architecture/HAOS_AGENT_IDENTITY_COUNCIL_RFC_IMPLEMENTATION.md`](v1/architecture/HAOS_AGENT_IDENTITY_COUNCIL_RFC_IMPLEMENTATION.md)
3. [`v1/specs/DATA_MODELS.md`](v1/specs/DATA_MODELS.md)
4. [`v1/specs/EVENT_CATALOG.md`](v1/specs/EVENT_CATALOG.md)
5. [`v1/specs/MIGRATIONS.md`](v1/specs/MIGRATIONS.md)
6. [`v1/implementation/HAOS_AGENT_IDENTITY_COUNCIL_IMPLEMENTATION_TASKS.md`](v1/implementation/HAOS_AGENT_IDENTITY_COUNCIL_IMPLEMENTATION_TASKS.md)
7. [`IDENTITY_VERSIONING_SPEC.md`](IDENTITY_VERSIONING_SPEC.md)
8. [`LEAF_EXECUTION_PROTOCOL.md`](LEAF_EXECUTION_PROTOCOL.md)
9. [`EVENT_SYSTEM_SPEC.md`](EVENT_SYSTEM_SPEC.md)
10. [`runtime/IDENTITY_ENGINE_SPEC.md`](runtime/IDENTITY_ENGINE_SPEC.md)
11. [`runtime/COUNCIL_ENGINE_SPEC.md`](runtime/COUNCIL_ENGINE_SPEC.md)
12. [`runtime/EVENT_BUS_SPEC.md`](runtime/EVENT_BUS_SPEC.md)

## 7. V1.1 — Contratos antes de serviços

### Implemente

Estabilize tipos/IDs primeiro:

```text
BotIdentityBundle
IdentityVersion
LeafIdentitySnapshot
CouncilSpec
CouncilSession
DecisionRecord
CivilizationEventEnvelope
EvidenceRef
MemoryRef
PolicyDecisionRef
```

Campos comuns mínimos: `schema_version`, stable ID, `created_at`, `actor_id`, `causation_id`, `correlation_id`, `metadata`.

### Teste antes de avançar

- round-trip serialize/deserialize;
- rejeição de schema inválido;
- compatibilidade de BotSpec legado;
- IDs únicos/estáveis;
- upgrade path de schema version.

### Gate V1.1

Nenhum serviço V1 usa dict ad-hoc como contrato de domínio.

---

## 8. V1.2 — Persistence + migrations + Event Envelope

### Leia

- [`kernel/PERSISTENCE_LAYER.md`](kernel/PERSISTENCE_LAYER.md)
- [`kernel/EVENT_SOURCING.md`](kernel/EVENT_SOURCING.md)
- [`kernel/STATE_MANAGEMENT.md`](kernel/STATE_MANAGEMENT.md)
- [`runtime/implementation/event-bus/schema.md`](runtime/implementation/event-bus/schema.md)
- [`runtime/implementation/event-bus/routing.md`](runtime/implementation/event-bus/routing.md)

### Implemente

- migrations forward + rollback;
- append-only event store ou equivalente consistente;
- stream sequence/version;
- outbox/inbox quando DB e publicação não forem atômicos;
- índices para owner/time/type/correlation;
- projection rebuild command;
- dedupe/idempotency store.

### Gate V1.2

Um teste deve apagar uma projeção, executar replay e obter o mesmo estado lógico.

---

## 9. V1.3 — Identity Engine

### Leia

- [`runtime/implementation/identity-engine/data-model.md`](runtime/implementation/identity-engine/data-model.md)
- [`runtime/implementation/identity-engine/services.md`](runtime/implementation/identity-engine/services.md)
- [`runtime/implementation/identity-engine/state-machine.md`](runtime/implementation/identity-engine/state-machine.md)
- exemplos: [`examples/bot-architect/SOUL.md`](examples/bot-architect/SOUL.md), [`IDENTITY.md`](examples/bot-architect/IDENTITY.md), [`VALUES.md`](examples/bot-architect/VALUES.md), [`BotSpec.yaml`](examples/bot-architect/BotSpec.yaml)

### Implemente

- resolver único de `SOUL/IDENTITY/VALUES`;
- path safety e normalização;
- bundle hash + hashes individuais;
- cache por `(bot_id, identity_version)`;
- fallback explícito para Bot legado;
- `identity show/diff/versions` em API/CLI quando aplicável;
- pending version para sessão ativa.

### Gate V1.3

Duas identidades distintas podem rodar em paralelo sem contaminar cache/prompt/memória.

---

## 10. V1.4 — Prompt bootstrap sem destruir cache

Resolva a identidade **antes** da criação da sessão/AIAgent. O prompt recebe uma projeção determinística da identidade. Mudanças após início não alteram a sessão em silêncio.

Teste pelo menos:

- mesma versão → mesmo identity digest;
- nova versão → nova sessão recebe versão nova;
- sessão antiga continua com snapshot antigo;
- conteúdo de memória dinâmica não muda hash da identidade estável;
- prompt injection vindo de memória não adquire autoridade.

### Gate V1.4

O implementador consegue provar por trace qual `identity_version` e `prompt_hash` geraram cada execução.

---

## 11. V1.5 — Leaf Execution Protocol

Leaf recebe **temporary SOUL derivada**, não a SOUL original editável.

Snapshot mínimo:

```text
leaf_id
parent_bot_id
bot_identity_version
identity_bundle_hash
mission/task hash
model + model params relevant to replay
toolset digest
memory_snapshot_ref
council/session refs
created_at / expires_at
correlation + causation
```

Teste: retry, cancel, timeout, crash/restart, duplicate create e cleanup. Leia [`LEAF_EXECUTION_PROTOCOL.md`](LEAF_EXECUTION_PROTOCOL.md) e o evento exemplo [`examples/events/leaf-created.json`](examples/events/leaf-created.json).

### Gate V1.5

Todo Leaf pode responder “de qual Bot/versão fui derivado?” sem consultar prompt textual.

---

## 12. V1.6 — Council mínimo

Council não é um “manager Bot”. Leia:

- [`runtime/implementation/council-engine/protocol.md`](runtime/implementation/council-engine/protocol.md)
- [`runtime/implementation/council-engine/debate-flow.md`](runtime/implementation/council-engine/debate-flow.md)
- [`runtime/implementation/council-engine/consensus.md`](runtime/implementation/council-engine/consensus.md)
- exemplo [`examples/council-architecture/CouncilSpec.yaml`](examples/council-architecture/CouncilSpec.yaml)

Implemente primeiro apenas:

```text
single_synthesizer
majority
consensus_with_dissent
```

As posições devem ser produzidas/armazenadas separadamente antes da síntese. O Council nunca herda automaticamente união de permissões dos membros.

### Gate V1.6

`DecisionRecord` contém posições, versões de identidade, evidências, dissenso, síntese, policy result e action refs.

---

## 13. V1.7 — Observability + inspection

Leia [`OBSERVABILITY_SPEC.md`](OBSERVABILITY_SPEC.md). Trace mínimo navegável:

```text
CouncilSession
  -> Bot position
    -> Leaf run
      -> tool calls / evidence
  -> synthesis
  -> DecisionRecord
  -> policy gate
  -> action/result
```

Propague IDs em logs e métricas. Não faça log de segredo apenas para facilitar debug.

---

## 14. V1.8 — Shadow rollout

Feature flags mínimas:

```text
civ.identity
civ.events
civ.leaf_snapshot
civ.council
```

Sequência: `off -> shadow -> opt-in -> canary -> default-on`. Compare legacy vs new path para latência, falha, custo e output sem executar efeitos duplicados.

### Gate de saída V1

Use também [`docs-hardening/ACCEPTANCE_GATE_MATRIX.md`](docs-hardening/ACCEPTANCE_GATE_MATRIX.md).

V1 só fecha quando:

- Bot legado continua funcionando;
- dois Bots com identidades diferentes não vazam estado;
- sessão congela versão de identidade;
- Leaf prova lineage e snapshot;
- retry não duplica Leaf/Decision/action;
- Council produz `DecisionRecord` reproduzível;
- event replay reconstrói projeção;
- crash/restart não viola invariantes;
- feature flag reverte para legacy sem migração destrutiva;
- trace E2E está navegável.

**Barreira:** não começar V2 antes deste gate.

---

# PARTE II — V2: SOCIEDADE

## 15. Objetivo V2

Criar interação social mensurável sem transformar reputação em “número mágico”.

### Leia nesta ordem

1. [`v2/HAOS_AGENT_CIVILIZATION_V2_SOCIEDADE.md`](v2/HAOS_AGENT_CIVILIZATION_V2_SOCIEDADE.md)
2. [`v2/HAOS_V2_SOCIEDADE_RFC_IMPLEMENTATION.md`](v2/HAOS_V2_SOCIEDADE_RFC_IMPLEMENTATION.md)
3. [`v2/HAOS_V2_DATA_MODEL.md`](v2/HAOS_V2_DATA_MODEL.md)
4. [`v2/HAOS_V2_IMPLEMENTATION_TASKS.md`](v2/HAOS_V2_IMPLEMENTATION_TASKS.md)
5. [`runtime/REPUTATION_ENGINE_SPEC.md`](runtime/REPUTATION_ENGINE_SPEC.md)
6. [`operating-model/AGENT_COLLABORATION_MODEL.md`](operating-model/AGENT_COLLABORATION_MODEL.md)
7. [`operating-model/TASK_DELEGATION_PROTOCOL.md`](operating-model/TASK_DELEGATION_PROTOCOL.md)
8. [`operating-model/SPECIALIZATION_SYSTEM.md`](operating-model/SPECIALIZATION_SYSTEM.md)

## 16. V2.1 — Relationship Graph

Arestas direcionais, tipadas, temporais e sustentadas por eventos. Nunca armazene apenas “afinidade=0.8”. Relações devem responder **por quê, quando e com base em quê**.

## 17. V2.2 — Reputation V2

Reputação é vetor por domínio e contexto, calculado a partir de evidência. O Bot não escreve diretamente sua reputação. Inclua decay configurável, confidence, amostra e source quality.

## 18. V2.3 — Role assignment e seleção

Role do Council é temporal. Selector considera capabilities + evidência reputacional + custo + disponibilidade + diversidade/redundância. Evite selecionar múltiplas cópias cognitivamente idênticas sem motivo.

## 19. V2.4 — Debate protocol

Pipeline recomendado:

```text
independent analysis
 -> evidence capture
 -> cross-examination
 -> rebuttal
 -> synthesis
 -> dissent capture
 -> policy validation
 -> DecisionRecord
```

Cada etapa tem budget, timeout e cancel semantics.

## 20. V2.5 — Delegation market

“Economy” é accounting de recursos, não mercado financeiro: tokens, tempo, quotas, prioridade, custo e utilidade. Leia [`operating-model/AGENT_ECONOMY.md`](operating-model/AGENT_ECONOMY.md) e [`operating-model/KNOWLEDGE_MARKET.md`](operating-model/KNOWLEDGE_MARKET.md).

### Gate de saída V2

- relationship history é explicável e temporal;
- reputation pode ser recalculada a partir de evidence events;
- selector consegue justificar membros/roles;
- debate preserva dissenso;
- delegação é idempotente e budget-aware;
- restart de uma CouncilSession não repete ações externas;
- métricas sociais não viram autorização.

---

# PARTE III — V3: EVOLUÇÃO

## 21. Objetivo V3

Aprender sem permitir self-modification opaca.

### Leia nesta ordem

1. [`v3/HAOS_AGENT_CIVILIZATION_V3_EVOLUCAO.md`](v3/HAOS_AGENT_CIVILIZATION_V3_EVOLUCAO.md)
2. [`v3/HAOS_V3_EVOLUTION_RFC.md`](v3/HAOS_V3_EVOLUTION_RFC.md)
3. [`v3/HAOS_V3_EVOLUTION_ENGINE.md`](v3/HAOS_V3_EVOLUTION_ENGINE.md)
4. [`v3/HAOS_V3_LINEAGE_MODEL.md`](v3/HAOS_V3_LINEAGE_MODEL.md)
5. [`v3/HAOS_V3_IMPLEMENTATION_TASKS.md`](v3/HAOS_V3_IMPLEMENTATION_TASKS.md)
6. [`runtime/EVOLUTION_ENGINE_SPEC.md`](runtime/EVOLUTION_ENGINE_SPEC.md)
7. [`operating-model/AUTONOMOUS_EVOLUTION_MODEL.md`](operating-model/AUTONOMOUS_EVOLUTION_MODEL.md)
8. [`intelligence/SELF_REFLECTION_SYSTEM.md`](intelligence/SELF_REFLECTION_SYSTEM.md)
9. [`intelligence/META_LEARNING.md`](intelligence/META_LEARNING.md)

## 22. V3.1 — Experience pipeline

Execução gera `ExperienceEvent`; consolidação extrai lesson candidates. Experience não altera identidade.

## 23. V3.2 — EvolutionProposal

Toda mudança relevante contém:

```text
proposal_id
base_identity_version
explicit patch/diff
reason
supporting evidence
expected benefit
risk/impact
validation plan
required policy/quorum
rollback plan
status + approvals
```

Mudanças em SOUL/VALUES têm gate mais forte que skill/default/heuristic tuning.

## 24. V3.3 — Identity versioning

Versões são imutáveis. Rollback cria versão compensatória. Sessões existentes continuam apontando para snapshot original.

## 25. V3.4 — Lineage + specialization

Fork de Bot contém parent, motivo, seed experiences, export policy e constraints. Memória privada não é clonada por default.

## 26. V3.5 — Lifecycle

Estado recomendado:

```text
draft -> active -> suspended -> evolving -> active
                         |          |
                         +-> deprecated -> archived
```

Toda transição gera evento e passa policy apropriada.

### Gate de saída V3

- nenhuma mutação crítica ocorre sem proposal;
- identity history é append-only;
- rollback não apaga histórico;
- lineage é navegável;
- experiência pode ser auditada até a proposta;
- restart no meio de apply não gera versão dupla;
- uma proposta rejeitada não deixa efeito parcial.

---

# PARTE IV — V4: CIVILIZAÇÃO

## 27. Objetivo V4

Criar conhecimento e governança coletivos sem declarar “verdade” o que é somente uma projeção produzida por modelos.

### Leia nesta ordem

1. [`v4/HAOS_AGENT_CIVILIZATION_V4_CIVILIZACAO.md`](v4/HAOS_AGENT_CIVILIZATION_V4_CIVILIZACAO.md)
2. [`v4/HAOS_V4_CIVILIZATION_PLAN.md`](v4/HAOS_V4_CIVILIZATION_PLAN.md)
3. [`v4/HAOS_V4_CIVILIZATION_MEMORY.md`](v4/HAOS_V4_CIVILIZATION_MEMORY.md)
4. [`v4/HAOS_V4_WORLD_MODEL.md`](v4/HAOS_V4_WORLD_MODEL.md)
5. [`v4/HAOS_V4_CONSTITUTION_SPEC.md`](v4/HAOS_V4_CONSTITUTION_SPEC.md)
6. [`v4/HAOS_V4_IMPLEMENTATION_TASKS.md`](v4/HAOS_V4_IMPLEMENTATION_TASKS.md)
7. [`runtime/CIVILIZATION_ENGINE_SPEC.md`](runtime/CIVILIZATION_ENGINE_SPEC.md)
8. [`kernel/GOVERNANCE_KERNEL.md`](kernel/GOVERNANCE_KERNEL.md)
9. [`intelligence/KNOWLEDGE_GRAPH.md`](intelligence/KNOWLEDGE_GRAPH.md)
10. [`intelligence/WORLD_MODEL_ENGINE.md`](intelligence/WORLD_MODEL_ENGINE.md)

## 28. V4.1 — Civilization Memory

Separe pelo menos:

- episódica;
- semântica;
- procedural;
- institucional;
- social.

Conflitos coexistem; resolução é evento explícito. Compaction preserva provenance e referências aos originais.

## 29. V4.2 — Knowledge Graph + World Model

Cada fact/edge registra validade temporal, confidence, provenance e status (`active`, `disputed`, `superseded`, etc.). O world model é projeção consultável e reconstruível.

## 30. V4.3 — Constitution / policy enforcement

Constituição Markdown serve para humanos, mas regra de enforcement deve existir em policy engine ou estrutura executável. Policy é avaliada fora do texto do modelo.

## 31. V4.4 — Autonomous maintenance

Jobs podem compactar memória, recalibrar reputation, detectar drift e **propor** evolução. Não podem autoaprovar mudança constitucional ou de identidade crítica.

### Gate de saída V4

- memória coletiva é reconstruível;
- conflito de fatos é representável;
- world model explica provenance;
- constitution version é registrada em toda ação governada;
- policy nega ação proibida mesmo quando todos os Bots aprovam;
- autonomous jobs são idempotentes e restart-safe;
- recovery completo pode ser ensaiado a partir de canonical state + event log.

---

# PARTE V — TRILHAS TRANSVERSAIS

## 32. Kernel

Leia estes documentos quando implementar mecanismos cross-cutting:

- [`kernel/KERNEL_ARCHITECTURE.md`](kernel/KERNEL_ARCHITECTURE.md) — fronteiras e ownership.
- [`kernel/AGENT_LIFECYCLE.md`](kernel/AGENT_LIFECYCLE.md) — estados/transições.
- [`kernel/EVENT_SOURCING.md`](kernel/EVENT_SOURCING.md) — event log, projections, replay.
- [`kernel/PERSISTENCE_LAYER.md`](kernel/PERSISTENCE_LAYER.md) — storage, migrations, WAL/DB semantics.
- [`kernel/STATE_MANAGEMENT.md`](kernel/STATE_MANAGEMENT.md) — canonical vs derived state.
- [`kernel/SECURITY_BOUNDARIES.md`](kernel/SECURITY_BOUNDARIES.md) — trust/tool/memory boundaries.
- [`kernel/GOVERNANCE_KERNEL.md`](kernel/GOVERNANCE_KERNEL.md) — policy + authority.

## 33. Runtime engines

- [`runtime/IDENTITY_ENGINE_SPEC.md`](runtime/IDENTITY_ENGINE_SPEC.md)
- [`runtime/EVENT_BUS_SPEC.md`](runtime/EVENT_BUS_SPEC.md)
- [`runtime/MEMORY_ENGINE_SPEC.md`](runtime/MEMORY_ENGINE_SPEC.md)
- [`runtime/COUNCIL_ENGINE_SPEC.md`](runtime/COUNCIL_ENGINE_SPEC.md)
- [`runtime/REPUTATION_ENGINE_SPEC.md`](runtime/REPUTATION_ENGINE_SPEC.md)
- [`runtime/EVOLUTION_ENGINE_SPEC.md`](runtime/EVOLUTION_ENGINE_SPEC.md)
- [`runtime/CIVILIZATION_ENGINE_SPEC.md`](runtime/CIVILIZATION_ENGINE_SPEC.md)
- [`runtime/RUNTIME_INTEGRATION_PLAN.md`](runtime/RUNTIME_INTEGRATION_PLAN.md)
- [`HAOS_CIVILIZATION_RUNTIME_SPEC.md`](HAOS_CIVILIZATION_RUNTIME_SPEC.md)

Não implemente sete mini-runtimes independentes. Engines são módulos sobre um runtime/event/persistence substrate comum.

## 34. Intelligence layer

Esses documentos especificam capacidades cognitivas, mas **não substituem contracts, policy ou persistence**:

- [`intelligence/COGNITIVE_ARCHITECTURE.md`](intelligence/COGNITIVE_ARCHITECTURE.md)
- [`intelligence/REASONING_ENGINE.md`](intelligence/REASONING_ENGINE.md)
- [`intelligence/PLANNING_SYSTEM.md`](intelligence/PLANNING_SYSTEM.md)
- [`intelligence/DECISION_ENGINE.md`](intelligence/DECISION_ENGINE.md)
- [`intelligence/COLLECTIVE_INTELLIGENCE.md`](intelligence/COLLECTIVE_INTELLIGENCE.md)
- [`intelligence/SELF_REFLECTION_SYSTEM.md`](intelligence/SELF_REFLECTION_SYSTEM.md)
- [`intelligence/META_LEARNING.md`](intelligence/META_LEARNING.md)
- [`intelligence/KNOWLEDGE_GRAPH.md`](intelligence/KNOWLEDGE_GRAPH.md)
- [`intelligence/WORLD_MODEL_ENGINE.md`](intelligence/WORLD_MODEL_ENGINE.md)
- [`intelligence/EMERGENT_BEHAVIOR_MODEL.md`](intelligence/EMERGENT_BEHAVIOR_MODEL.md)
- [`intelligence/INTELLIGENCE_INTEGRATION.md`](intelligence/INTELLIGENCE_INTEGRATION.md)

## 35. Operating model

Use após foundations correspondentes existirem:

- [`operating-model/AGENT_COLLABORATION_MODEL.md`](operating-model/AGENT_COLLABORATION_MODEL.md)
- [`operating-model/TASK_DELEGATION_PROTOCOL.md`](operating-model/TASK_DELEGATION_PROTOCOL.md)
- [`operating-model/SPECIALIZATION_SYSTEM.md`](operating-model/SPECIALIZATION_SYSTEM.md)
- [`operating-model/LEARNING_ECOSYSTEM.md`](operating-model/LEARNING_ECOSYSTEM.md)
- [`operating-model/AGENT_ECONOMY.md`](operating-model/AGENT_ECONOMY.md)
- [`operating-model/KNOWLEDGE_MARKET.md`](operating-model/KNOWLEDGE_MARKET.md)
- [`operating-model/AUTONOMOUS_EVOLUTION_MODEL.md`](operating-model/AUTONOMOUS_EVOLUTION_MODEL.md)
- [`operating-model/CIVILIZATION_METRICS.md`](operating-model/CIVILIZATION_METRICS.md)

---

## 36. Estratégia recomendada de PRs / fatias verticais

Evite um PR “civilization”. Use fatias revisáveis:

| PR | Conteúdo | Depende de | Gate principal |
|---|---|---|---|
| 01 | contracts + schema + migrations | F0 | round-trip + rollback |
| 02 | event envelope + outbox/inbox + replay skeleton | 01 | deterministic replay |
| 03 | IdentityResolver + BotSpec compat | 01–02 | dual identity isolation |
| 04 | prompt bootstrap + frozen session identity | 03 | cache/version stability |
| 05 | Leaf snapshot + retry/cancel/recovery | 03–04 | lineage + idempotency |
| 06 | CouncilSpec + CouncilSession + DecisionRecord | 02–05 | reproducible decision |
| 07 | observability + inspector + V1 canary | 01–06 | V1 exit gate |
| 08 | relationships + reputation | V1 | evidence recomputation |
| 09 | role selector + debate + delegation | 08 | V2 exit gate |
| 10 | experience + EvolutionProposal + versioning | V2 | no silent mutation |
| 11 | lineage + lifecycle + evolution rollout | 10 | V3 exit gate |
| 12 | civilization memory + KG/world model | V3 | provenance/rebuild |
| 13 | constitution/policy + maintenance jobs | 12 | policy supremacy |
| 14 | full recovery, chaos, performance, default-on | all | V4/global gate |

Cada PR deve deixar o sistema executável e ter rollback claro.

---

## 37. Paralelismo seguro

Consulte [`DEPENDENCY_GRAPH.md`](DEPENDENCY_GRAPH.md). Regra curta:

**Pode paralelizar:** documentação/spec validation, observability plumbing, fixtures, test harness, UI inspector, non-conflicting projections.

**Não pode paralelizar sem contrato congelado:** schema e service consumers; IdentityResolver e prompt integration; event schema e replay; Council runtime e DecisionRecord; EvolutionProposal e identity mutation; Constitution e privileged action execution.

Use barriers de integração. Mais agentes em paralelo sem ownership explícito aumenta merge risk e pode criar duas fontes de verdade.

---

## 38. Loop Harness obrigatório por task

Para **cada** task:

```text
1. DISCOVER  -> localizar código real e callers.
2. SPEC      -> identificar contrato + owner + invariantes.
3. PLAN      -> definir menor fatia vertical reversível.
4. IMPLEMENT -> domain + persistence + event + API/CLI + trace quando aplicável.
5. VERIFY    -> happy path + failure path + retry + restart quando relevante.
6. REPLAY    -> provar determinismo para estado evented/projection.
7. SECURITY  -> validar trust/capability/policy boundary.
8. PERF      -> medir delta no hot path.
9. ROLLBACK  -> ensaiar feature flag/migration/down path.
10. RECORD   -> atualizar IMPLEMENTATION_STATUS + ADR se decisão arquitetural mudou.
```

Se uma task não consegue responder `owner`, `idempotency key`, `recovery semantics`, `trace IDs` e `rollback`, ela ainda não está pronta para merge.

---

## 39. Testes que não podem faltar

A suíte detalhada está em [`TEST_AND_CHAOS_HARNESS.md`](TEST_AND_CHAOS_HARNESS.md). Casos mínimos globais:

- legacy compatibility;
- identity cache isolation;
- stale identity/session behavior;
- Leaf duplicate create/retry/cancel;
- Council crash entre decisão e action;
- outbox publish duplicate;
- event replay após apagar projection;
- DB locked/busy e restart;
- memory provenance loss prevention;
- reputation recomputation;
- EvolutionProposal double-apply;
- rollback de identity version;
- conflicting world facts;
- policy denial apesar de Council unanimemente favorável;
- autonomous maintenance duplicate run;
- secret/tool capability isolation;
- prompt injection em memory/evidence;
- performance regression no turno legado.

---

## 40. Observabilidade mínima

Toda operação relevante deve conseguir propagar quando aplicável:

```text
trace_id
correlation_id
causation_id
bot_id
identity_version_id
council_id
council_session_id
leaf_id
decision_id
proposal_id
policy_version
model/provider
prompt_hash/toolset_hash
latency/cost/token counters
```

Métricas essenciais: error rate, retry/dedupe, queue depth, event lag, replay failures, active identity versions, Leaf fanout, Council duration, policy denials, memory compaction, proposal backlog e state-rebuild health.

---

## 41. Segurança e authority model

Antes de tool/action privilegiada leia [`docs-hardening/THREAT_MODEL.md`](docs-hardening/THREAT_MODEL.md).

Princípios:

- LLM output é input não confiável.
- SOUL/VALUES não concedem capability.
- memória/evidence externa é não confiável.
- um Council não soma permissões dos membros.
- Leaf recebe least privilege por missão.
- segredo não entra em snapshot/event log.
- ação destrutiva passa por policy separada da síntese do Council.
- mudança constitucional e identidade crítica exigem gate humano/policy definido pelo deployment.

---

## 42. Recovery e incidentes

Leia [`FAILURE_RECOVERY_RUNBOOK.md`](FAILURE_RECOVERY_RUNBOOK.md). O sistema deve diferenciar:

- **retryable transport/provider error**;
- **deterministic domain rejection**;
- **policy denial**;
- **partial external side effect**;
- **projection corruption**;
- **canonical state corruption**;
- **event schema incompatibility**.

Recovery não significa “rodar tudo de novo”. Primeiro descubra se houve side effect e use idempotency/compensation.

---

## 43. Definition of Done por versão

### V1
Indivíduo, identity snapshot, Leaf lineage, Council/Decision e event replay sólidos.

### V2
Relações/reputation/debate/delegation explicáveis e baseados em evidência.

### V3
Aprendizado/evolução versionados, governados, reversíveis e com lineage.

### V4
Memória institucional/world model/constitution reconstruíveis, versionados e operacionalmente enforced.

### Global

O projeto só está “completo” quando:

- migrations up/down foram ensaiadas;
- state rebuild funciona;
- retry e restart foram testados;
- policy boundaries resistem a output malicioso;
- legacy rollback continua disponível durante janela definida;
- observabilidade permite investigação sem ler prompts manualmente;
- documentação corresponde aos símbolos reais do commit final;
- [`docs-hardening/ACCEPTANCE_GATE_MATRIX.md`](docs-hardening/ACCEPTANCE_GATE_MATRIX.md) está integralmente verde;
- [`EXECUTION_CHECKLIST.md`](EXECUTION_CHECKLIST.md) está fechado;
- existe relatório final seguindo [`AGENT_HANDOFF_TEMPLATE.md`](AGENT_HANDOFF_TEMPLATE.md).

---

## 44. Quando criar ADR

Use [`docs-hardening/ADR_TEMPLATE.md`](docs-hardening/ADR_TEMPLATE.md) quando mudar qualquer uma destas decisões:

- canonical store/event model;
- IDs / ordering / consistency;
- prompt/identity freeze semantics;
- Council decision mode semantics;
- idempotency strategy;
- memory ownership/scopes;
- reputation aggregation;
- evolution approval policy;
- policy/constitution enforcement;
- distribuição single-node → multi-node.

Não esconda decisão estrutural em comentário de código ou descrição de PR.

---

## 45. Entrega do agente implementador

O handoff final deve conter:

1. commit(s)/PR(s) e escopo;
2. tasks concluídas e pendentes;
3. migrations adicionadas;
4. feature flags e defaults;
5. tests executados + resultado;
6. chaos/recovery scenarios executados;
7. benchmarks baseline vs final;
8. riscos/resíduos conhecidos;
9. ADRs criados;
10. passos exatos de rollback;
11. resultado de cada gate V1–V4;
12. exemplo de trace Council→Bot→Leaf→Decision→Action;
13. confirmação de que docs e paths foram atualizados para o commit final.

Use [`AGENT_HANDOFF_TEMPLATE.md`](AGENT_HANDOFF_TEMPLATE.md) para padronizar a entrega.

---

## 46. Índice rápido por pergunta

| Se o agente precisa saber... | Abrir |
|---|---|
| por onde começar | este README + [`IMPLEMENTATION_PLAYBOOK.md`](IMPLEMENTATION_PLAYBOOK.md) |
| ordem/dependências | [`DEPENDENCY_GRAPH.md`](DEPENDENCY_GRAPH.md) |
| qual spec cobre qual task/test | [`TRACEABILITY_MATRIX.md`](TRACEABILITY_MATRIX.md) |
| contratos HTTP/CLI/domain | [`API_CONTRACTS.md`](API_CONTRACTS.md) |
| identidade/versionamento | [`IDENTITY_VERSIONING_SPEC.md`](IDENTITY_VERSIONING_SPEC.md) |
| Leaf/subagent | [`LEAF_EXECUTION_PROTOCOL.md`](LEAF_EXECUTION_PROTOCOL.md) |
| eventos/replay | [`EVENT_SYSTEM_SPEC.md`](EVENT_SYSTEM_SPEC.md) |
| Council/debate | [`runtime/COUNCIL_ENGINE_SPEC.md`](runtime/COUNCIL_ENGINE_SPEC.md) |
| memória | [`runtime/MEMORY_ENGINE_SPEC.md`](runtime/MEMORY_ENGINE_SPEC.md) |
| segurança | [`docs-hardening/THREAT_MODEL.md`](docs-hardening/THREAT_MODEL.md) |
| observabilidade | [`OBSERVABILITY_SPEC.md`](OBSERVABILITY_SPEC.md) |
| falhas/recovery | [`FAILURE_RECOVERY_RUNBOOK.md`](FAILURE_RECOVERY_RUNBOOK.md) |
| testes/caos | [`TEST_AND_CHAOS_HARNESS.md`](TEST_AND_CHAOS_HARNESS.md) |
| V2 sociedade | [`v2/HAOS_V2_SOCIEDADE_RFC_IMPLEMENTATION.md`](v2/HAOS_V2_SOCIEDADE_RFC_IMPLEMENTATION.md) |
| V3 evolução | [`v3/HAOS_V3_EVOLUTION_RFC.md`](v3/HAOS_V3_EVOLUTION_RFC.md) |
| V4 civilização | [`v4/HAOS_V4_CIVILIZATION_PLAN.md`](v4/HAOS_V4_CIVILIZATION_PLAN.md) |
| status/handoff | [`IMPLEMENTATION_STATUS_TEMPLATE.md`](IMPLEMENTATION_STATUS_TEMPLATE.md), [`AGENT_HANDOFF_TEMPLATE.md`](AGENT_HANDOFF_TEMPLATE.md) |
| catálogo completo | [`FILE_INDEX.md`](FILE_INDEX.md) |

---

## 47. Regra final para o outro agente

**Não tente “terminar V4” de uma vez.** Termine uma cadeia de invariantes. Em qualquer ponto, o sistema deve continuar explicável, executável e reversível.

A ordem mental correta é:

```text
identity ownership
 -> immutable snapshots
 -> events + idempotency
 -> Council decisions
 -> evidence + memory
 -> society/reputation
 -> governed evolution
 -> civilization knowledge
 -> constitution enforcement
 -> autonomous maintenance
```

Se uma camada posterior exigir violar uma invariável anterior, a solução está errada e deve ser redesenhada, não contornada.
