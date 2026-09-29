# HAOS Agent Identity & Council RFC de Implementação

## Objetivo

Este documento transforma a visão de identidade persistente, Council de agentes e Leafs rastreáveis em um plano técnico executável para o HAOS.

A arquitetura alvo:

```
                    HAOS
                     |
              Council Layer
                     |
        +------------+------------+
        |            |            |
      Bots         Bots         Bots
        |            |            |
      Leafs        Leafs        Leafs
```

Princípios:

- Bot é uma entidade persistente.
- Council é uma entidade de inteligência coletiva.
- Leaf é uma execução temporária derivada de um Bot.
- Identidade é estado persistente, não apenas prompt.
- Toda execução deve ser auditável.

---

# 1. Arquitetura atual e futura

## Atual

```
BotSpec
  |
  v
BotManager
  |
  v
Executor
  |
  v
AIAgent
  |
  v
System Prompt
```

## Futura

```
BotSpec
  |
  v
IdentityResolver
  |
  +--> SOUL.md
  +--> IDENTITY.md
  +--> VALUES.md
  +--> MEMORY
  |
  v
Prompt Builder
  |
  v
AIAgent
```

Council adiciona uma camada superior:

```
User
 |
 v
Council Runtime
 |
 +-- Bot Architect
 |       |
 |       +-- Leafs
 |
 +-- Bot Researcher
 |       |
 |       +-- Leafs
 |
 +-- Bot Engineer
         |
         +-- Leafs
```

---

# 2. Novos componentes

## BotIdentity

Responsável pela identidade persistente do Bot.

Responsabilidades:

- carregar SOUL.md;
- carregar identidade;
- carregar valores;
- resolver versão da identidade;
- gerar snapshot para Leafs.

Modelo:

```python
BotIdentity:
    bot_id
    soul_path
    identity_path
    values_path
    memory_backend
    version
```

---

## IdentityResolver

Entrada:

```
BotSpec
```

Saída:

```
BotIdentity
```

Responsável por unir:

- identidade persistente;
- memória;
- contexto da tarefa.

---

## CouncilSpec

Representa um conselho persistente.

Modelo:

```python
CouncilSpec:
    id
    purpose
    members
    decision_mode
    memory_backend
    rules
```

---

## CouncilRuntime

Responsável por:

- receber objetivos;
- selecionar Bots participantes;
- criar Leafs;
- consolidar resultados;
- registrar decisões.

---

## LeafIdentitySnapshot

Cada Leaf deve congelar a identidade utilizada.

Modelo:

```python
LeafIdentitySnapshot:
    leaf_id
    parent_bot_id
    council_id
    soul_hash
    system_prompt_hash
    created_at
```

---

# 3. Estrutura de arquivos proposta

```
~/.hermes/

bots/
  architect/
    BotSpec.yaml
    SOUL.md
    IDENTITY.md
    VALUES.md
    MEMORY.md
    EXPERIENCE.md
    state.db

councils/
  architecture-council/
    CouncilSpec.yaml
    PURPOSE.md
    RULES.md
    MEMORY.md
    decisions/
    sessions/
```

---

# 4. Modelo de identidade

## SOUL.md

Define:

- personalidade base;
- valores;
- estilo cognitivo;
- limites.

Não deve ser alterado automaticamente.

## IDENTITY.md

Define:

- nome;
- papel;
- especialidade;
- histórico.

## VALUES.md

Define:

- prioridades;
- critérios de decisão;
- restrições.

## MEMORY.md

Conhecimento acumulado.

## EXPERIENCE.md

Aprendizados derivados de eventos.

Fluxo seguro:

```
Experiência
    |
    v
Proposta de evolução
    |
    v
Aprovação
    |
    v
Nova versão SOUL
```

---

# 5. Council como entidade de primeira classe

Council não é apenas uma lista de agentes.

Possui:

- propósito;
- regras;
- memória coletiva;
- decisões históricas;
- membros permanentes.

Exemplo:

```yaml
id: architecture-council
purpose: Projetar arquiteturas robustas
members:
  - architect
  - researcher
  - engineer
decision_mode: consensus
```

---

# 6. Fluxo de execução completo

Exemplo:

Usuário:

"Projetar nova arquitetura de memória"

Fluxo:

```
Request
 |
 v
Council
 |
 +-- Architect Bot
 |       |
 |       +-- Leaf analyze-design
 |
 +-- Researcher Bot
 |       |
 |       +-- Leaf compare-solutions
 |
 +-- Engineer Bot
         |
         +-- Leaf implementation-plan

          |
          v

DecisionRecord
```

---

# 7. Herança de identidade

Hierarquia:

```
Council Rules
      |
      v
Bot SOUL
      |
      v
Task Context
      |
      v
Leaf Temporary SOUL
```

O Leaf pode adaptar comportamento para a missão, mas nunca altera a identidade do Bot pai.

---

# 8. Auditoria e rastreabilidade

Toda execução registra:

```
ExecutionRecord

id
council_id
bot_id
leaf_id
soul_hash
memory_snapshot
system_prompt_hash
model
result
```

Permite responder:

- qual entidade decidiu?
- qual identidade estava ativa?
- quais evidências foram usadas?

---

# 9. Plano incremental

## Milestone 1 - Bot Identity Layer

Implementar:

- BotIdentity;
- SoulLoader;
- IdentityResolver.

Critério:

Bots podem possuir SOUL própria.

---

## Milestone 2 - Identity-aware Leafs

Implementar:

- snapshot de identidade;
- lineage;
- hashes.

Critério:

Todo Leaf é reproduzível e auditável.

---

## Milestone 3 - Council Runtime

Implementar:

- CouncilSpec;
- seleção de membros;
- criação de Leafs.

Critério:

Um objetivo pode ser resolvido por múltiplos Bots.

---

## Milestone 4 - Council Memory

Implementar:

- memória coletiva;
- DecisionRecords;
- histórico.

---

## Milestone 5 - Experience Evolution

Implementar:

- eventos de experiência;
- propostas de mudança;
- aprovação humana.

---

# 10. Testes obrigatórios

## Compatibilidade

Bots antigos continuam funcionando.

## Identidade isolada

Dois Bots possuem SOULs diferentes.

## Lineage

Validar:

```
Council -> Bot -> Leaf
```

## Reprodutibilidade

Mesmo snapshot gera mesmo contexto.

## Segurança

Experiência não altera SOUL automaticamente.

---

# Resultado esperado

O HAOS evolui de:

```
agentes executando tarefas
```

para:

```
sociedade de agentes persistentes

com:
- identidade;
- memória;
- experiência;
- conselho;
- execução temporária rastreável.
```
