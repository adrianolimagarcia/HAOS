# HAOS Civilization — Architecture Review / Hardening

## Principais riscos encontrados no plano interrompido

1. muitos arquivos eram stubs <1 KB e não definiam failure semantics;
2. Council podia ser interpretado como um único coordenador, contrariando a intenção de inteligência coletiva;
3. reputação/evolução careciam de provenance e política anti-gaming;
4. runtime specs não tratavam idempotência, replay, restart e rollback;
5. memory/world model não separavam canonical store de projections;
6. constituição existia como conceito textual sem enforcement;
7. faltava strategy de migração/feature flags/degraded mode.

## Hardening aplicado nesta entrega

- contratos, state machines, idempotency e event envelope;
- Council multi-Bot com independent positions + dissent;
- Leaf identity snapshot e temporary SOUL imutável;
- mutation/evolution por proposal;
- canonical memory + rebuildable graph/vector projections;
- policy gate executável;
- canary/rollback e observability como DoD;
- arquivos pequenos substituídos por specs substanciais.


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
