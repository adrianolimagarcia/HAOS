# Plano: HAOS Agent Identity, Conselho de Agentes e Leafs Temporários

## Visão

Evoluir o HAOS de um orquestrador de agentes para um sistema operacional de entidades cognitivas persistentes, mantendo compatibilidade com o modelo atual de agentes, SOUL.md, Profiles, Bots e Shadow Leafs.

A proposta combina:

- identidade persistente por Bot;
- personalidade e valores versionados;
- memória própria;
- conselho multi-agent;
- execução temporária de Leafs com SOUL derivada e rastreável;
- governança humana sobre evolução de identidade.

O princípio central:

> Um Bot é uma entidade persistente. Um Leaf é uma manifestação temporária dessa entidade para uma missão específica.

---

# 1. Modelo conceitual

## Estado atual

Hoje:

```
BotSpec
 ├── capabilities
 ├── policy
 ├── routines
 └── triggers
```

O Bot descreve capacidade operacional, mas não possui identidade explícita.

## Modelo proposto

```
Bot
 |
 +-- Identity
 |    +-- SOUL.md
 |    +-- IDENTITY.md
 |    +-- VALUES.md
 |
 +-- Memory
 |    +-- MEMORY.md
 |    +-- state.db
 |
 +-- Experience
 |    +-- EXPERIENCE.md
 |
 +-- Relationships
 |    +-- RELATIONSHIPS.md
 |
 +-- Skills
 |
 +-- Council Role
 |
 +-- Leafs
      +-- temporary SOUL
      +-- execution trace
      +-- lineage
```

---

# 2. Estrutura de um Bot persistente

Sugestão:

```
~/.hermes/bots/

architect/
├── BotSpec.yaml
├── SOUL.md
├── IDENTITY.md
├── VALUES.md
├── MEMORY.md
├── EXPERIENCE.md
├── RELATIONSHIPS.md
├── skills/
└── state.db
```

## SOUL.md

Define natureza do agente:

- personalidade base;
- tom;
- princípios;
- limites;
- forma de raciocínio.

Nunca deve ser alterada automaticamente sem governança.

## IDENTITY.md

Define identidade:

- nome;
- função;
- especialização;
- origem;
- papel dentro do conselho.

## VALUES.md

Define decisões:

- prioridades;
- critérios de escolha;
- comportamentos proibidos.

## MEMORY.md

Memória consolidada:

- fatos aprendidos;
- contexto de projetos;
- conhecimento acumulado.

## EXPERIENCE.md

Camada evolutiva:

- eventos importantes;
- erros cometidos;
- aprendizados derivados.

Não modifica SOUL diretamente.

---

# 3. Conselho de Agentes

Criar uma camada superior onde Bots persistentes colaboram.

Exemplo:

```
                 Council
                    |
        +-----------+-----------+
        |           |           |
   Architect   Researcher   Engineer
        |           |           |
     Leafs      Leafs       Leafs
```

Cada membro possui:

- identidade própria;
- memória própria;
- visão própria;
- responsabilidades próprias.

O conselho não substitui o agente principal. Ele funciona como uma estrutura de decisão.

---

# 4. Papéis do Conselho

Exemplos:

## Architect

Responsável por:

- arquitetura;
- trade-offs;
- visão de longo prazo.

## Researcher

Responsável por:

- evidências;
- investigação;
- análise externa.

## Engineer

Responsável por:

- implementação;
- testes;
- execução prática.

## Security

Responsável por:

- riscos;
- validações;
- ameaças.

---

# 5. Leafs temporários

Manter o conceito atual de Shadow Leafs.

Um Leaf não é uma nova entidade.

É uma instância temporária de um Bot.

Exemplo:

```
Architect Bot
      |
      +-- Leaf: analyze-database-design
              |
              +-- temporary SOUL
              +-- task context
              +-- execution history
              +-- parent_bot_id
```

---

# 6. SOUL temporária dos Leafs

Cada Leaf recebe uma composição:

```
Temporary SOUL
=
Bot SOUL base
+
Task specialization
+
Execution constraints
+
Council context
```

Exemplo:

Bot:

"Sou arquiteto de sistemas, valorizo simplicidade e manutenção."

Leaf:

"Durante esta missão atuarei como arquiteto focado em banco de dados distribuído."

A SOUL temporária deve ser:

- gerada;
- armazenada;
- hash identificável;
- vinculada ao Bot pai;
- totalmente auditável.

---

# 7. Rastreabilidade completa

Todo Leaf deve registrar:

```
LeafRecord

id
parent_bot_id
created_at
purpose
soul_snapshot
memory_snapshot
model
system_prompt_hash
result
lessons_learned
```

Objetivo:

Permitir responder:

- qual identidade executou esta ação?
- qual SOUL estava ativa?
- qual memória influenciou a decisão?
- qual experiência gerou mudança?

---

# 8. Evolução controlada da personalidade

Evitar:

```
experiência -> altera SOUL automaticamente
```

Modelo seguro:

```
Leafs
 |
 v
 EXPERIENCE.md
 |
 v
 proposta de evolução
 |
 v
 aprovação humana
 |
 v
 nova versão SOUL.md
```

Toda mudança de identidade deve ser versionada.

---

# 9. Integração com BotSpec atual

Evoluir sem quebrar compatibilidade.

Adicionar opcionalmente:

```yaml
identity:
  soul: SOUL.md
  identity: IDENTITY.md
  values: VALUES.md

memory:
  backend: state.db

experience:
  enabled: true

council:
  role: architect
```

Bots antigos continuam funcionando.

---

# 10. Fases de implementação

## Fase 1 - Identity Layer

Criar:

- BotIdentity;
- carregamento SOUL específico;
- IDENTITY.md;
- VALUES.md.

Objetivo:

Bots passam a ter personalidade própria.

---

## Fase 2 - Memory Isolation

Adicionar:

- memória por Bot;
- banco próprio;
- histórico independente.

---

## Fase 3 - Leaf Identity

Integrar com ShadowLeaf:

- snapshot de SOUL;
- lineage;
- hashes;
- auditoria.

---

## Fase 4 - Experience System

Criar:

- EXPERIENCE.md;
- aprendizado derivado;
- propostas de evolução.

---

## Fase 5 - Council Runtime

Implementar:

- criação de conselho;
- papéis;
- comunicação entre Bots;
- decisões multi-agent.

---

# 11. Princípios de design

## Identidade não é prompt

Prompt é uma execução.

Identidade é estado persistente.

## Leaf não é agente novo

Leaf é uma projeção temporária de uma entidade.

## Memória não é personalidade

Conhecimento acumulado não deve alterar automaticamente valores.

## Evolução deve ser auditável

Toda mudança importante precisa de histórico.

---

# Resultado esperado

O HAOS passa de:

```
agentes executando tarefas
```

para:

```
sociedade de agentes persistentes

com identidades,
memórias,
experiências,
conselho,
e execuções temporárias rastreáveis.
```

Mantendo o espírito SOTA: identidade persistente, execução segura, rastreabilidade total e governança humana.

# 12. Council como camada arquitetural de primeira classe

O Council não deve ser tratado como uma simples coleção de Bots. Ele é uma entidade persistente de inteligência coletiva.

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

Um Council possui:

- propósito próprio;
- membros definidos;
- protocolo de decisão;
- memória coletiva;
- histórico de decisões;
- regras de colaboração.

Estrutura proposta:

```
~/.hermes/councils/

architecture-council/
├── CouncilSpec.yaml
├── PURPOSE.md
├── RULES.md
├── MEMORY.md
├── decisions/
├── members/
│   ├── architect/
│   │   ├── BotSpec.yaml
│   │   ├── SOUL.md
│   │   └── MEMORY.md
│   ├── researcher/
│   └── engineer/
└── sessions/
```

## Fluxo de decisão

```
Usuário
  |
  v
Council
  |
  +-- Architect
  |      +-- Leafs
  |
  +-- Researcher
  |      +-- Leafs
  |
  +-- Engineer
         +-- Leafs
```

O Council decide:

- quais especialistas participam;
- quais investigações são necessárias;
- quais Leafs devem ser criados;
- como consolidar evidências;
- quando uma decisão está madura.

## Council Memory

Além da memória individual dos Bots, existe memória coletiva:

```
Bot MEMORY.md
      |
      v
conhecimento individual

Council MEMORY.md
      |
      v
aprendizados coletivos
```

Exemplo:

Um Architect pode aprender uma preferência arquitetural, enquanto o Council registra padrões históricos de decisões que funcionaram ou falharam.

## Decisão auditável

Toda decisão do Council deve registrar:

```
DecisionRecord

id
council_id
participants
bot_versions
leaf_executions
evidence
reasoning
decision
```

Isso permite rastrear:

- quais Bots participaram;
- quais identidades estavam ativas;
- quais Leafs produziram evidências;
- como a decisão foi formada.

O Council transforma o HAOS de um sistema multi-agent em um runtime de sociedade de agentes persistentes.
