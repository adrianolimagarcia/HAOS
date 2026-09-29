# HAOS Civilization — Master Implementation Plan (Ultra SOTA / Harness)

> **Navegação canônica:** comece por [`README.md`](README.md). Este documento é uma especialização do mapa mestre; não deve ser usado isoladamente para pular barriers/gates.

## 0. Escopo e objetivo operacional

Este é o documento principal para um agente implementador. Ele converte a visão V1–V4 em sequência de engenharia, reduz risco de big-bang e preserva o runtime Hermes/HAOS existente.


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


## 1. Arquitetura alvo

```text
                    User / Trigger / Routine
                              |
                       Civilization API
                              |
                 +------------+------------+
                 |                         |
          Council Runtime             Direct Bot Run
                 |                         |
           CouncilSession                  |
       +---------+---------+               |
       |         |         |               |
   Architect  Research  Engineer <---------+
       |         |         |
       +----- Shadow Leafs-+
                 |
        Identity Snapshots + Evidence
                 |
           Decision Record
                 |
     Memory / Reputation / Experience
                 |
         Evolution Proposals
                 |
       Governance / Constitution
```

## 2. Foundation contracts

Antes de código funcional, estabilizar os tipos: `BotIdentity`, `IdentityVersion`, `LeafIdentitySnapshot`, `CouncilSpec`, `CouncilSession`, `DecisionRecord`, `MemoryRecord`, `ExperienceEvent`, `ReputationEvent`, `EvolutionProposal`, `LineageEdge` e `CivilizationEventEnvelope`.

Todos usam UUID/ULID estável, `schema_version`, `created_at`, `causation_id`, `correlation_id`, `actor_id` e `metadata`. Eventos são imutáveis; projeções são descartáveis/reconstruíveis.

## 3. V1 — Fundação em 8 incrementos

### V1.1 BotIdentity + resolver
- criar modelos e resolver caminhos relativos ao diretório do Bot;
- fallback explícito para Bot legado;
- cache por `(bot_id, identity_version)`;
- hash SHA-256 de cada componente e do bundle final.

### V1.2 BotSpec compatibility
Adicionar bloco opcional `identity` sem mudar defaults existentes. Parser deve aceitar specs antigos e novos. Persistência deve preservar campos desconhecidos quando possível.

### V1.3 Prompt bootstrap
Resolver identidade **antes** de construir sessão/AIAgent. A identidade entra em seção determinística do system prompt. Mudança posterior gera `identity.pending_version` e só vale em próxima sessão, salvo comando explícito de reinício.

### V1.4 Leaf identity snapshot
Ao criar ShadowLeaf, congelar `bot_identity_version`, hashes, modelo, toolset digest, council/task context e referência de memória. A SOUL temporária é derivada; nunca edita SOUL do pai.

### V1.5 Event envelope e audit log
Todo create/update/run/decision gera envelope versionado. Usar append-only + projeções. Introduzir outbox se publicação e DB não puderem ser atômicos no storage atual.

### V1.6 Council mínimo
CouncilSpec com purpose, member selectors, roles, decision_mode, budgets e memory scope. Primeiros modos: `single_synthesizer`, `majority`, `consensus_with_dissent`. Guardar posições separadas antes da síntese para reduzir contaminação.

### V1.7 DecisionRecord
Persistir participantes, versões de identidade, leafs, evidências, posições, síntese, decisão, confidence e action refs. Resultado executável só após policy gate.

### V1.8 Rollout
Feature flags: `civ.identity`, `civ.leaf_snapshot`, `civ.council`, `civ.events`. Canary em bots selecionados; comparar outputs/latência/custo contra legado.

## 4. V2 — Sociedade

### V2.1 Relationship graph
Arestas tipadas, direcionais e temporais; nunca reduzir relação a um score único. Exemplos: `collaborated_with`, `reviewed`, `delegated_to`, `disagreed_with`, `corrected_by`.

### V2.2 Evidence-backed reputation
Reputação é vetor por domínio (`coding`, `research`, `security`, etc.), sustentado por eventos e decaimento configurável. Nenhum Bot pode editar sua própria reputação diretamente.

### V2.3 Role assignment
Roles do Council são contratos temporais, não identidade fixa. Seleção considera capabilities, reputation evidence, custo, disponibilidade e diversidade cognitiva.

### V2.4 Debate protocol
Fases: independent analysis → cross-examination → rebuttal → synthesis → dissent capture → decision. Cada fase recebe budget e timeout.

### V2.5 Delegation market
Matching de tarefas por requisitos objetivos. Evitar leilão financeiro real: “economy” significa accounting interno de tokens, latência, quotas e utilidade operacional.

## 5. V3 — Evolução

### V3.1 Experience pipeline
Execução produz ExperienceEvent; um consolidator extrai lesson candidates com provenance. Nada altera identidade ainda.

### V3.2 EvolutionProposal
Proposal contém diff explícito, motivação, evidências, impacto, rollback e quorum/policy necessária. Pode modificar skills, defaults, heurísticas ou, com gate mais forte, SOUL/VALUES.

### V3.3 Identity versioning
IdentityVersion é imutável. Nova versão aponta para parent; sessions antigas mantêm snapshot antigo. Rollback cria nova versão que restaura conteúdo, não reescreve história.

### V3.4 Lineage
Especialização cria Bot derivado com `parent_identity_id`, `reason`, `seed_experiences`, `fork_policy`. Não compartilhar memória privada por cópia cega; usar export policy.

### V3.5 Lifecycle
Estados: `draft -> active -> suspended -> evolving -> deprecated -> archived`. Transições têm policy e eventos.

## 6. V4 — Civilização

### V4.1 Civilization Memory
Separar episódica, semântica, procedural, institucional e social. Toda memória coletiva aponta para fontes; conflitos coexistem até resolução explícita.

### V4.2 Knowledge Graph + World Model
Entidades e fatos com validade temporal, confidence e provenance. World model é projeção consultável, não verdade absoluta. Facts superseded continuam auditáveis.

### V4.3 Constitution
Políticas versionadas governam o que Councils/Bots podem propor/executar. Constituição não fica somente em Markdown: compilar regras operacionais para policy engine quando possível.

### V4.4 Autonomous maintenance
Jobs podem compactar memória, recalibrar reputação, detectar drift e propor evolução. Eles **não** podem promover mudança constitucional/identitária crítica sem gate autorizado.

## 7. Persistence design

SQLite continua opção natural para single-node: WAL, migrations numeradas, foreign keys, busy timeout, índices por IDs/tempo e tabelas append-only para eventos. Para escala distribuída, manter interfaces para Postgres/event broker sem contaminar domínio.

Esquema mínimo sugerido:

```sql
identity_versions(id, bot_id, version, soul_hash, bundle_json, created_at)
leaf_runs(id, parent_bot_id, council_id, identity_version_id, prompt_hash, status, created_at)
councils(id, spec_json, revision, state)
council_sessions(id, council_id, objective, status, correlation_id)
decisions(id, council_session_id, payload_json, created_at)
events(event_id, stream_id, stream_seq, event_type, schema_version, payload_json, created_at)
outbox(id, event_id, topic, payload_json, published_at)
memories(id, owner_type, owner_id, kind, content_ref, provenance_json, confidence, valid_from, valid_to)
reputation_events(id, subject_bot_id, domain, evidence_ref, delta, created_at)
evolution_proposals(id, bot_id, base_version, patch_json, status, policy_ref)
```

## 8. Security boundaries

- workspace Leaf não recebe segredos não requeridos;
- snapshot registra referências, não conteúdo secreto;
- tool capabilities são allowlist por Bot/Leaf;
- Council synthesis não eleva permissões acima da interseção/policy do executor;
- prompts e memórias são dados não confiáveis: proteger contra instruction injection via tagging/segregação;
- destructive actions exigem policy gate separado da opinião do Council.

## 9. Performance budgets

V1 deve adicionar overhead previsível: resolução de identidade em cache <5 ms local; gravação de snapshot/evento fora do hot path de token streaming quando possível; Council só é ativado quando solicitado. Memory retrieval usa budget fixo por camada e evita N+1 entre Bots.

## 10. Operação e observabilidade

Dashboards: runs por Bot/Council, error rate, leaf fanout, budget/cost, queue depth, event lag, replay health, identity versions em uso, pending proposals, memory compaction e policy denials. Traces devem permitir navegar Council→Bot→Leaf→Decision→Action.

## 11. Migração

1. criar tabelas novas sem tocar legado;
2. registrar identidade implícita v0 para Bot existente somente quando ele optar por civilization mode;
3. dual-write eventos em shadow mode;
4. comparar projeção civilizacional contra estado legado;
5. ativar leitura nova por Bot;
6. manter kill switch;
7. remover legado apenas após janela estável e ferramenta de export/import.

## 12. Backlog de implementação

- P0: contratos + migrations + IdentityResolver + prompt bootstrap + Leaf snapshot + audit/event envelope.
- P0: Council mínimo + DecisionRecord + feature flags + trace correlation.
- P1: memory isolation + Council memory + evidence store + debate protocol.
- P1: relationship/reputation + selector de especialistas.
- P2: Experience/Evolution + lineage/lifecycle.
- P2: Civilization Memory + KG/World Model.
- P3: Constitution/policy compilation + autonomous maintenance.

## 13. Gates por versão

**V1 exit:** dois Bots com Souls distintas executam em paralelo sem vazamento; Leaf prova lineage; Council gera DecisionRecord reproduzível; legacy mode não muda comportamento.

**V2 exit:** delegação usa evidência/reputação e debate preserva dissenso; relation graph é temporal e explicável.

**V3 exit:** nenhuma evolução altera passado; proposta, aprovação, nova versão e rollback são rastreáveis.

**V4 exit:** memória coletiva é reconstruível, world model mostra provenance/conflitos e política constitucional barra ação proibida independentemente do modelo.


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
