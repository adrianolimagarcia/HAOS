# HAOS Civilization — Architecture Evolution Map

## Regra de ouro

```text
V1 cria entidades -> V2 cria sociedade -> V3 cria evolução -> V4 cria civilização
```

A principal defesa contra overengineering é não antecipar semântica da próxima versão. Cada fase possui entidades permitidas, dependências e anti-escopo.

## V1 — Fundação

Pergunta: **quem executou e com qual identidade?**

Entidades: `BotIdentity`, `IdentityVersion`, `LeafIdentitySnapshot`, `CouncilSpec`, `CouncilSession`, `DecisionRecord`, `CivilizationEventEnvelope`.

Não incluir reputação, genealogia, autoevolução nem world model. Council pode selecionar membros por regra simples/capability declarada.

## V2 — Sociedade

Pergunta: **como indivíduos colaboram e como a sociedade sabe em que contexto confiar neles?**

Entidades: `RelationshipEdge`, `ReputationEvent`, `DomainReputationProjection`, `DebateSession`, `RoleAssignment`, `CollaborationRecord`.

Não alterar SOUL automaticamente. Reputação é histórico evidenciado, não “QI”.

## V3 — Evolução

Pergunta: **como aprender sem apagar a própria história?**

Entidades: `ExperienceEvent`, `LessonCandidate`, `EvolutionProposal`, `EvolutionDecision`, `LineageEdge`, `LifecycleTransition`.

Mudanças críticas exigem governança. A experiência pode propor; não pode sobrescrever.

## V4 — Civilização

Pergunta: **como conhecimento, cultura e regras sobrevivem aos indivíduos?**

Entidades: `CivilizationMemory`, `KnowledgeAssertion`, `WorldModelEntity`, `ConstitutionVersion`, `PolicyRule`, `MaintenanceJob`.

Autonomia não elimina governança; ela automatiza manutenção dentro de limites versionados.

## Dependências

```text
Identity/Eventing ───────┐
Leaf lineage ────────────┼─> Council decisions ─> Reputation/Relationships
Memory provenance ───────┘                         |
                                                  v
                                      Experience/Evolution
                                                  |
                                                  v
                               Civilization Memory/World Model
                                                  |
                                                  v
                                    Constitution/Governance
```

## Matriz de compatibilidade

| Capacidade | Legacy | V1 | V2 | V3 | V4 |
|---|---:|---:|---:|---:|---:|
| BotSpec atual | ✓ | ✓ | ✓ | ✓ | ✓ |
| SOUL por Bot | - | ✓ | ✓ | ✓ | ✓ |
| Leaf snapshot | - | ✓ | ✓ | ✓ | ✓ |
| Council auditável | - | ✓ | ✓ | ✓ | ✓ |
| Reputation/relations | - | - | ✓ | ✓ | ✓ |
| Evolution proposals | - | - | - | ✓ | ✓ |
| Lineage | - | - | - | ✓ | ✓ |
| Civilization memory | - | - | - | - | ✓ |
| Constitution/policy | - | - | - | - | ✓ |


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
