# HAOS V1 — Code Mapping


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


## Fluxo atual esperado

```text
BotSpec -> BotManager -> Executor -> AIAgent -> System Prompt
```

## Fluxo alvo

```text
BotSpec -> IdentityResolver -> BotIdentityBundle -> Agent bootstrap -> immutable session prompt

      +-> Council membership/role
      +-> ShadowLeaf -> LeafIdentitySnapshot -> execution trace
```

## Mudanças por arquivo

### `hermes/platform/bots/spec.py`
Adicionar `identity` opcional, policy/council refs e version fields sem tornar obrigatórios para specs legados.

### `hermes/platform/bots/manager.py`
Emitir lifecycle events, resolver revision optimisticamente e expor hooks para Identity/Memory sem incorporar lógica cognitiva.

### `hermes/platform/shadow_leaf.py`
Receber snapshot pronto; registrar lineage; propagar cancellation/deadline e produzir result/evidence refs.

### `agent/agent_init.py`
Receber `identity_context` opcional; garantir que construção do agente e da sessão use a mesma versão.

### `agent/system_prompt.py`
Criar seção estável de identidade no prefixo cacheável. Não injetar memória mutável no prefixo se isso invalidar cache; memória recuperada entra na região dinâmica correta.

### novos módulos
`hermes/platform/bots/identity.py`, `identity_resolver.py`, `hermes/platform/council/(spec, manager, runtime, memory, decisions).py`.

## Review questions

- existe outro caminho de criação de `AIAgent` que bypassa esse bootstrap?
- BotManager já possui event store que deve ser reutilizado?
- ShadowLeaf já propaga parent_bot_id em todos os paths de retry?
- sessão WebUI/CLI usa o mesmo system prompt builder?

Essas perguntas devem ser respondidas no repo real antes do primeiro patch.
