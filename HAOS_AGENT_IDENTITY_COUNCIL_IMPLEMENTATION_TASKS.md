# HAOS Agent Identity & Council - Implementation Tasks

## Objetivo

Este documento transforma a RFC de arquitetura em um backlog técnico executável.

O objetivo é implementar gradualmente:

- identidade persistente por Bot;
- Council como camada de inteligência coletiva;
- Leafs temporários com identidade rastreável;
- memória individual e coletiva;
- evolução controlada de experiência.

Princípios:

- não quebrar Bots existentes;
- preservar prompt caching;
- manter ShadowLeaf como execução isolada;
- toda identidade usada em execução deve ser auditável.

---

# Fase 0 - Preparação e validação da arquitetura

## TASK-000 - Mapear pontos atuais de integração

Objetivo:

Confirmar os pontos reais do runtime antes de alterar código.

Arquivos envolvidos:

```
hermes/platform/bots/spec.py
hermes/platform/bots/manager.py
hermes/platform/shadow_leaf.py
agent/agent_init.py
agent/system_prompt.py
```

Critérios de aceite:

- fluxo BotSpec → execução documentado;
- ponto de criação do AIAgent identificado;
- ponto de montagem do system prompt identificado.

Dependências:

Nenhuma.

---

# Fase 1 - Bot Identity Layer

## TASK-001 - Criar modelo BotIdentity

Objetivo:

Criar a entidade de identidade persistente do Bot.

Arquivo novo:

```
hermes/platform/bots/identity.py
```

Implementar:

```python
BotIdentity
```

Campos mínimos:

```python
bot_id
soul
identity
values
version
```

Critérios de aceite:

- identidade pode ser criada independentemente do BotSpec;
- modelo possui serialização;
- versão da identidade é rastreável.

---

## TASK-002 - Criar IdentityResolver

Arquivo novo:

```
hermes/platform/bots/identity_resolver.py
```

Responsabilidade:

Resolver:

```
BotSpec
   |
   v
BotIdentity
```

Carregar:

```
SOUL.md
IDENTITY.md
VALUES.md
```

Critérios:

- ausência de identidade não quebra Bots antigos;
- fallback para comportamento atual.

Dependência:

TASK-001.

---

## TASK-003 - Adicionar suporte opcional no BotSpec

Arquivo:

```
hermes/platform/bots/spec.py
```

Adicionar configuração opcional:

```yaml
identity:
  soul: SOUL.md
  identity: IDENTITY.md
  values: VALUES.md
```

Critérios:

- BotSpec antigo continua válido;
- serialização existente continua funcionando.

Dependência:

TASK-002.

---

# Fase 2 - Integração com Agent Runtime

## TASK-004 - Integrar identidade ao prompt inicial

Arquivos candidatos:

```
agent/agent_init.py
agent/system_prompt.py
```

Objetivo:

Adicionar identidade resolvida ao contexto inicial do agente.

Fluxo:

```
BotIdentity
      |
      v
System Prompt
      |
      v
AIAgent
```

Restrições:

- nunca alterar prompt no meio da conversa;
- preservar cache de prompt;
- identidade deve ser resolvida antes da sessão.

Critérios:

- dois Bots podem ter SOUL diferentes;
- prompt gerado contém identidade correta.

Dependências:

TASK-002.

---

# Fase 3 - Identity-aware Shadow Leafs

## TASK-005 - Criar LeafIdentitySnapshot

Arquivo candidato:

```
hermes/platform/shadow_leaf.py
```

Adicionar modelo:

```python
LeafIdentitySnapshot
```

Campos:

```python
leaf_id
parent_bot_id
council_id
soul_hash
system_prompt_hash
created_at
```

Critérios:

Toda execução Leaf registra a identidade utilizada.

Dependências:

TASK-002.

---

## TASK-006 - Gerar SOUL temporária do Leaf

Objetivo:

Criar composição:

```
Leaf SOUL
=
Bot SOUL
+
Task Context
+
Execution Constraints
```

Regras:

- Leaf adapta função;
- Leaf não altera identidade do Bot pai;
- snapshot é imutável após criação.

Critérios:

Uma execução antiga pode ser reconstruída com o snapshot.

Dependências:

TASK-005.

---

# Fase 4 - Council Runtime

## TASK-007 - Criar estrutura Council

Criar pacote:

```
hermes/platform/council/
```

Estrutura:

```
council/
├── spec.py
├── manager.py
├── runtime.py
├── memory.py
└── decisions.py
```

---

## TASK-008 - Criar CouncilSpec

Modelo:

```python
CouncilSpec:
    id
    purpose
    members
    decision_mode
    rules
```

Critérios:

Um Council pode declarar múltiplos Bots participantes.

---

## TASK-009 - Criar CouncilRuntime

Responsabilidades:

- receber objetivo;
- selecionar membros;
- criar Leafs;
- coletar resultados;
- gerar decisão.

Fluxo:

```
Request
  |
  v
Council
  |
  +-- Architect Leaf
  +-- Researcher Leaf
  +-- Engineer Leaf
  |
  v
Decision
```

---

# Fase 5 - Memória e Decisões

## TASK-010 - Criar DecisionRecord

Modelo:

```python
DecisionRecord:
    id
    council_id
    participants
    evidence
    leaf_executions
    decision
```

Critério:

Toda decisão do Council possui histórico.

---

## TASK-011 - Implementar Council Memory

Criar:

```
Council MEMORY.md
```

Separar:

```
Bot Memory
     |
     v
conhecimento individual

Council Memory
     |
     v
aprendizado coletivo
```

---

# Fase 6 - Experience Evolution

## TASK-012 - Criar Experience Events

Registrar:

- falhas;
- aprendizados;
- padrões identificados.

Critério:

Experiência nunca altera SOUL automaticamente.

---

## TASK-013 - Criar fluxo de proposta de evolução

Fluxo:

```
Experience
    |
    v
Evolution Proposal
    |
    v
Human Approval
    |
    v
SOUL Version Update
```

---

# Testes obrigatórios

## TASK-TEST-001 - Compatibilidade

Validar:

```
BotSpec antigo
      |
      v
Execução normal
```

---

## TASK-TEST-002 - Isolamento de identidade

Criar:

```
architect/SOUL.md
researcher/SOUL.md
```

Validar que não existe vazamento.

---

## TASK-TEST-003 - Lineage de Leaf

Validar:

```
Council
  |
  Bot
  |
  Leaf
```

---

## TASK-TEST-004 - Reprodutibilidade

Mesmo:

```
Bot version
SOUL hash
Memory snapshot
Task
```

deve gerar contexto equivalente.

---

# Ordem recomendada de commits

```
1. BotIdentity model
2. IdentityResolver
3. BotSpec extension
4. Prompt integration
5. Leaf identity snapshot
6. CouncilSpec
7. CouncilRuntime
8. Decision records
9. Council memory
10. Experience evolution
```

---

# Critério final de sucesso

O HAOS deve suportar:

```
Council
   |
   +-- Persistent Bots
           |
           +-- Temporary Leafs
```

onde cada execução possui:

- identidade conhecida;
- memória rastreável;
- lineage completo;
- decisão auditável.
