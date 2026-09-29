# HAOS Agent Identity & Council - Code Mapping

## Objetivo

Mapear a arquitetura proposta de Identity, Council e Leafs para os pontos reais do repositório HAOS/Hermes.

Este documento serve como guia de implementação para evitar criar uma arquitetura paralela desconectada do runtime existente.

---

# 1. Fluxo atual identificado

Arquitetura atual:

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

Arquitetura alvo:

```
BotSpec
  |
  v
BotIdentityResolver
  |
  +--> SOUL.md
  +--> IDENTITY.md
  +--> VALUES.md
  +--> Memory
  |
  v
Prompt Assembly
  |
  v
AIAgent

Council Runtime
  |
  +--> Bots
          |
          +--> Shadow Leafs
```

---

# 2. Mapeamento de componentes

## BotSpec existente

Local:

```
hermes/platform/bots/spec.py
```

Responsabilidade atual:

- identidade básica (`id`, `name`);
- capabilities;
- policy;
- routines;
- triggers.

Evolução proposta:

Adicionar referência opcional para identidade:

```yaml
identity:
  soul: SOUL.md
  identity: IDENTITY.md
  values: VALUES.md
```

Não substituir o modelo atual.

Compatibilidade:

Bots antigos continuam sem camada de identidade.

---

# 3. BotIdentity

Novo componente sugerido:

```
hermes/platform/bots/identity.py
```

Responsabilidade:

- carregar identidade persistente do Bot;
- resolver arquivos de identidade;
- gerar snapshots para Leafs;
- versionar identidade.

Modelo:

```python
@dataclass
class BotIdentity:
    bot_id: str
    soul: str
    identity: str
    values: str
    version: int
```

---

# 4. Identity Resolver

Novo componente:

```
hermes/platform/bots/identity_resolver.py
```

Fluxo:

```
BotSpec
  |
  v
IdentityResolver
  |
  +--> SOUL.md
  +--> IDENTITY.md
  +--> VALUES.md
  |
  v
BotIdentity
```

Esse componente deve ser o ponto único de resolução de identidade.

---

# 5. Prompt Assembly

Área crítica:

```
agent/system_prompt.py
agent/agent_init.py
```

Objetivo:

Adicionar identidade resolvida ao prompt sem quebrar:

- cache de prompt;
- estabilidade de sessão;
- alternância de mensagens.

Fluxo futuro:

```
BotIdentity
      |
      v
System Prompt Builder
      |
      v
AIAgent
```

A identidade deve ser resolvida antes da criação da sessão.

Nunca alterar SOUL durante uma conversa ativa.

---

# 6. ShadowLeaf Integration

Arquivo identificado:

```
hermes/platform/shadow_leaf.py
```

Hoje:

- cria execução isolada;
- possui `parent_bot_id`;
- usa worktree temporária.

Evolução:

Adicionar:

```
LeafIdentitySnapshot
```

Com:

```python
leaf_id
parent_bot_id
council_id
soul_hash
system_prompt_hash
created_at
```

Fluxo:

```
Bot
 |
 v
ShadowLeaf
 |
 v
Temporary Identity Snapshot
 |
 v
Execution
```

---

# 7. Council Runtime

Nova camada:

```
hermes/platform/council/
```

Possíveis módulos:

```
council/
├── spec.py
├── manager.py
├── runtime.py
├── memory.py
└── decisions.py
```

Responsabilidades:

## CouncilSpec

Define:

- propósito;
- membros;
- regras;
- modo de decisão.

## CouncilRuntime

Coordena:

- seleção de Bots;
- criação de Leafs;
- agregação de resultados.

## DecisionStore

Mantém histórico:

- participantes;
- evidências;
- decisões.

---

# 8. Relação com Bot Manager

Arquivo atual:

```
hermes/platform/bots/manager.py
```

Possível evolução:

BotManager continua responsável por Bots.

CouncilManager separado:

```
BotManager
    |
    +--> Bot Registry

CouncilManager
    |
    +--> Council Registry
```

Evitar transformar BotManager em um componente gigante.

---

# 9. Memória

Separar três níveis:

```
Council Memory
      |
      v
aprendizado coletivo

Bot Memory
      |
      v
conhecimento individual

Leaf Context
      |
      v
contexto temporário da missão
```

Cada camada deve possuir rastreabilidade própria.

---

# 10. Ordem recomendada de implementação

## Etapa 1

Criar:

- BotIdentity;
- IdentityResolver.

Validar:

Um Bot consegue carregar SOUL própria.

---

## Etapa 2

Integrar com prompt assembly.

Validar:

A identidade entra no contexto inicial do agente.

---

## Etapa 3

Integrar ShadowLeaf.

Validar:

Todo Leaf possui:

```
Council -> Bot -> Leaf
```

---

## Etapa 4

Criar Council Runtime.

Validar:

Um objetivo pode envolver múltiplos Bots.

---

## Etapa 5

Adicionar memória coletiva e decisões.

---

# 11. Critérios de aceite

## Identidade

Dois Bots podem possuir:

```
architect/SOUL.md
researcher/SOUL.md
```

sem interferência.

## Auditoria

Uma execução deve responder:

- qual Council iniciou;
- qual Bot executou;
- qual Leaf foi criado;
- qual SOUL estava ativa.

## Compatibilidade

Bots antigos continuam funcionando.

## Segurança

EXPERIENCE não altera SOUL automaticamente.

---

# Resultado esperado

O código evolui de:

```
Bot = configuração de execução
```

para:

```
Council
  |
  Bot persistente
  |
  Leaf temporário rastreável
```

Mantendo os invariantes do HAOS:

- identidade persistente;
- execução isolada;
- cache seguro;
- auditoria completa;
- evolução controlada.
