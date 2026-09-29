# HAOS Civilization — Roadmap Evolutivo e Runbook Operacional

**Status do documento:** plano de execução proposto  
**Escopo:** Council autônomo, curadoria de evolução, memória coletiva Markdown, observabilidade em grafos, durabilidade/idempotência, paridade Rust/Python e rollout seguro.  
**Base auditada:** `HAOS_AGENT_IDENTITY_COUNCIL_PLAN.md`, `HAOS_AGENT_IDENTITY_COUNCIL_IMPLEMENTATION_TASKS.md`, `haos-civ/runtime/COUNCIL_ENGINE_SPEC.md`, implementação Python em `hermes/platform/` e crate `packages/haos-civ/`.

> Este documento é um plano, não um relatório de funcionalidades futuras já entregues. Os estados abaixo distinguem o que foi observado no código, o que está parcialmente implementado e o que ainda deve ser construído.

---

## 1. Objetivo e resultado final

Evoluir o HAOS de um sistema que **registra entidades e executa delegações** para um runtime de sociedade de agentes em que:

```text
objetivo complexo
      |
      v
CouncilDebateRunner
      |
      +-- análises independentes de Bots reais
      +-- Leafs com identidade congelada
      +-- debate multi-turn com orçamento e timeout
      +-- síntese com dissenso preservado
      +-- DecisionRecord auditável
      |
      v
policy gate -> aprovação -> execução -> evidência
      |
      v
reputação + experiência + memória coletiva
      |
      v
proposta de evolução -> revisão -> canário -> promoção/rollback
```

O resultado deve preservar os invariantes do projeto original:

1. identidade persistente não é prompt efêmero;
2. o system prompt permanece byte-stable durante a sessão;
3. Leaf é manifestação temporária de um Bot, não um novo cidadão;
4. toda mutação relevante é event-sourced, rastreável e reproduzível;
5. execução externa só ocorre após policy gate;
6. propostas de evolução nunca alteram `SOUL.md`/`VALUES.md` silenciosamente;
7. retries são at-least-once e handlers são idempotentes;
8. orçamento é limite duro, não apenas métrica posterior;
9. rollout sempre segue `shadow -> opt-in -> canary -> default-on -> legacy removal`;
10. desligar qualquer feature flag retorna a um caminho funcional anterior.

---

## 2. Diagnóstico do estado atual

### 2.1 Entregue e validado

A base existente já cobre uma parte relevante do plano original:

| Área | Estado observado | Evidência principal |
|---|---|---|
| Identidade persistente | **Implementado** | `hermes/platform/bots/identity.py`, `identity_manager.py`, `identity_resolver.py` |
| Versionamento/rollback | **Implementado** | `IdentityManager`, versões imutáveis e rollback compensatório |
| Leaf identity snapshot | **Implementado** | `hermes/platform/bots/leaf_protocol.py`, `shadow_leaf.py` |
| Council agregado | **Implementado parcialmente** | `hermes/platform/council/spec.py` e `manager.py` |
| Posições e dissenso | **Implementado no CRUD event-sourced** | `submit_position()` e `DecisionRecord.dissent` |
| Reputação/sociedade | **Implementado** | `hermes/platform/society/` |
| Experiência de missão | **Implementado** | `BotEvolutionManager.record_experience()` |
| Propostas e stale-base | **Implementado** | `hermes/platform/evolution/bot_evolution.py` |
| Constituição e policy gate | **Implementado em escopo básico** | `CivilizationManager.evaluate_policy()` |
| Memória estruturada | **Implementado** | `KnowledgeAssertion` no EventStore |
| Curadoria analítica | **Implementado como analisador/ledger** | `evolution/analyzer.py`, `evolution/ledger.py` |
| Crate Rust fundacional | **Implementado** | `packages/haos-civ/src/` e testes nativos |
| Dashboard observatório | **Implementado como projeção read-only** | `hermes_cli/web_routers/civilization.py`, `web/src/pages/CivilizationPage.tsx` |
| Reconciliação de Leafs | **Implementado** | `reconcile_orphaned_leaves()` e `reconcile_civilization_state()` |

### 2.2 Lacunas materiais em relação às RFCs

As seguintes lacunas são o alvo deste roadmap:

| Lacuna | Impacto |
|---|---|
| Não existe `CouncilDebateRunner` que invoque LLMs e execute o FSM completo | O Council é persistente, mas ainda não é um orquestrador autônomo de debate |
| `CouncilManager` não implementa debate multi-turn, timeout, retry/cancelamento ou retomada da FSM | Sessões interrompidas não têm uma máquina operacional completa |
| `CouncilSpec.budget` existe como dado, mas não há enforcement duro | Um Council pode ultrapassar tokens, custo, fan-out ou tempo |
| Promoção de evolução pode preencher posições automaticamente com `APPROVE` | A deliberação atual é uma simulação programática, não evidência de julgamento independente |
| O analisador de evolução não está ligado a um worker diário que submeta propostas | Experiências são registradas, mas a curadoria não fecha o ciclo automaticamente |
| A memória coletiva existe no EventStore, não como `councils/<id>/MEMORY.md` projetado | Usuário não consegue inspecionar/editar a projeção Markdown diretamente |
| Não há API de escrita segura para iniciar Council, submeter aprovação ou operar propostas no observatório | A UI atual é essencialmente read-only |
| O frontend mostra uma árvore/relacionamentos explícitos, não um grafo social ou DAG visual | Colaboração, reputação e linhagem não são exploráveis visualmente |
| Não há runtime de outbox/inbox específico de ações civ | Há outbox em subsistemas de memória, mas isso não fecha a durabilidade de comandos/side effects do Council |
| Rust cobre modelos e operações fundacionais, mas não possui paridade com o runner LLM, curator, Markdown projection e APIs operacionais | “Dual stack” não significa ainda paridade de runtime completo |
| Eventos civ não têm, de forma uniforme, `command_id`, deduplicação e optimistic concurrency | Repetição de requisições pode gerar sessões/decisões duplicadas |
| `DecisionRecord` nem sempre é enriquecido com versões de identidade, evidências e Leafs reais | A auditoria final pode ficar incompleta mesmo quando o registro existe |
| Política atual casa principalmente por `action`; escopo de recurso, ator, risco e approvals externos precisam ser formalizados | O fail-closed ainda é simples demais para ações de produção |

### 2.3 Fora do escopo imediato

Não fazem parte do primeiro ciclo:

- reescrever o chat principal em React;
- alterar prompt no meio de conversa ativa;
- substituir o EventStore por outro banco sem necessidade comprovada;
- criar telemetria externa sem opt-in;
- promover mudanças de identidade automaticamente em produção;
- remover o caminho Python antes de haver paridade e rollback comprovados no Rust.

---

## 3. Arquitetura alvo

### 3.1 Componentes

```text
hermes/platform/council/
├── spec.py                 # CouncilSpec, Session, DecisionRecord
├── manager.py              # agregado e replay de eventos
├── runtime.py              # FSM durável e comandos idempotentes
├── debate_runner.py        # orquestração LLM multi-turn
├── member_runner.py        # resolução Bot -> Leaf -> execução
├── synthesis.py            # síntese estruturada e validação
├── budget.py               # limites de fan-out/tokens/custo/tempo
├── outbox.py               # intents/side effects pendentes
├── inbox.py                # deduplicação de comandos/eventos
├── memory.py               # projeção Council MEMORY.md
└── decisions.py            # exportação e validação de DecisionRecord
```

```text
hermes/platform/evolution/
├── analyzer.py             # sinais mensuráveis
├── curator.py              # lote de experiências -> proposta draft
├── ledger.py               # submit/decide idempotente
└── promotion.py            # review -> canary -> promote/rollback
```

```text
hermes_cli/web_routers/
├── civilization.py         # overview read-only + endpoints autorizados
├── council.py              # sessões, aprovação e cancelamento
└── evolution.py            # propostas, review e rollout
```

```text
web/src/
├── pages/CivilizationPage.tsx
├── components/CouncilGraph.tsx
├── components/VersionDag.tsx
├── components/CouncilSessionPanel.tsx
└── components/EvolutionApprovalPanel.tsx
```

### 3.2 Agregado e eventos canônicos

Cada mutação deve possuir um `command_id` fornecido pelo caller e uma chave natural adicional quando aplicável.

Eventos mínimos:

```text
civ.council.created
civ.council.session-started
civ.council.phase-changed
civ.council.member-dispatched
civ.council.member-position-recorded
civ.council.debate-round-started
civ.council.debate-turn-recorded
civ.council.synthesis-requested
civ.council.synthesis-recorded
civ.council.decision-recorded
civ.council.session-paused
civ.council.session-aborted
civ.council.session-failed
civ.council.action-intent-created
civ.council.action-completed
civ.council.action-failed
civ.evolution.curator-run
civ.evolution.proposal-created
civ.evolution.proposal-reviewed
civ.evolution.canary-started
civ.evolution.promoted
civ.evolution.rolled-back
civ.memory.council-projection-updated
```

Payload comum:

```json
{
  "event_id": "evt-...",
  "command_id": "cmd-...",
  "correlation_id": "csess-...",
  "causation_id": "evt-...",
  "aggregate_id": "csess-...",
  "schema_version": 1,
  "actor": "operator|bot:<id>|system",
  "timestamp": "UTC",
  "payload": {}
}
```

Regras:

- `event_id` é único e deduplicado;
- `command_id` repetido devolve o resultado anterior, sem novo efeito;
- o replay deve reconstruir exatamente a mesma sessão;
- efeitos externos só são publicados após persistir o intent/outbox;
- dados sensíveis ficam fora de logs; usar hash e referência de evidência.

---

# 4. Frente A — CouncilDebateRunner autônomo

## 4.1 Contrato funcional

Adicionar `CouncilDebateRunner` como serviço explícito, não como lógica escondida em `route_task()`.

Entrada mínima:

```python
runner.deliberate(
    council_id="arch-sec-council",
    objective="Migrar o banco para cluster distribuído",
    idempotency_key="request-2026-...",
    approved=False,
    options={
        "max_rounds": 3,
        "max_members": 4,
        "timeout_seconds": 900,
        "execute_action": False,
    },
)
```

Saída:

```json
{
  "session_id": "csess-...",
  "status": "completed|paused|aborted|failed",
  "decision_id": "dec-...",
  "participants": ["architect-bot", "security-bot"],
  "leaf_ids": ["shadow-..."],
  "dissent_count": 1,
  "budget": {"tokens": 1234, "cost_usd": 0.04, "rounds": 2},
  "action_gate": "not_requested|approval_required|approved|denied",
}
```

## 4.2 FSM durável

Estados obrigatórios:

```text
created
  -> selecting_members
  -> independent_analysis
  -> debate_round
  -> synthesis
  -> policy_check
  -> decision_recorded
  -> action_pending
  -> completed
```

Saídas de exceção:

```text
qualquer estado -> paused       # operador, lease perdido, retry agendado
qualquer estado -> aborted      # cancelamento explícito
qualquer estado -> failed       # erro irrecuperável ou quorum impossível
```

Precondições:

- `created`: Council existe e objective não é vazio;
- `selecting_members`: membros estão registrados, ativos e dentro do fan-out;
- `independent_analysis`: cada membro recebe seu próprio Leaf e snapshot;
- `debate_round`: não publicar posição de um membro como contexto de outro na rodada independente;
- `synthesis`: todas as posições disponíveis ou política explícita de quorum/degradação;
- `policy_check`: policy é avaliada sobre a ação concreta, recurso, ator e risco;
- `action_pending`: intent durável existe antes de qualquer side effect;
- `completed`: DecisionRecord e evidências foram persistidos.

Cada transição deve ser uma operação idempotente baseada em `(session_id, phase, command_id)`.

## 4.3 Execução dos membros

Para cada membro:

1. validar que o Bot está ativo;
2. resolver a identidade ativa antes de criar a sessão;
3. criar `LeafIdentitySnapshot` com `bot_id`, `identity_version_id`, hash da SOUL, modelo e contexto;
4. construir prompt de análise antes do início da execução;
5. enviar a mesma descrição do objetivo, mas sem posições de outros membros na rodada independente;
6. registrar resultado, custo, tokens, modelo, latência, referências e falha;
7. atualizar reputação apenas com evidência da execução;
8. nunca alterar a identidade persistente durante a sessão.

O runner pode usar `asyncio`/pool existente para fan-out, mas o limite deve ser controlado pelo `BudgetController`, e não por `gather()` ilimitado.

## 4.4 Debate multi-turn

Rodada 0: análise independente.  
Rodadas seguintes: cada Bot recebe um pacote sanitizado contendo:

- posições dos demais membros;
- evidências e referências;
- pontos de conflito detectados;
- orçamento remanescente;
- instruções para corrigir, manter ou dissentir;
- nenhum prompt interno ou segredo de outro Bot.

O debate encerra quando ocorrer uma destas condições:

- consenso suficiente segundo `decision_mode`;
- limite de rodadas atingido;
- budget esgotado;
- quorum mínimo perdido;
- timeout global;
- operador cancela.

Dissenso nunca deve ser descartado para produzir uma síntese “limpa”. Deve constar em `DecisionRecord.dissent` com autor, afirmação, evidência e impacto.

## 4.5 Synthesis bot

O sintetizador é um participante com papel distinto, ou uma função de síntese validada por schema. Ele deve produzir:

```json
{
  "decision": "...",
  "synthesis": "...",
  "confidence": 0.0,
  "assumptions": ["..."],
  "evidence_refs": ["leaf-...", "trace-..."],
  "dissent": {"security-bot": "..."},
  "action_plan": [{"action": "...", "resource": "...", "risk": "..."}],
  "needs_human_approval": true
}
```

Validações antes de persistir:

- JSON/schema válido;
- todos os participantes aparecem em `participants` ou são explicitamente marcados como timeout;
- toda afirmação crítica tem evidência ou é marcada como hipótese;
- `confidence` fica em `[0, 1]`;
- a decisão não pode declarar `policy_verified=true` antes do policy gate real;
- action refs apontam para intents existentes ou para ações não executadas claramente marcadas.

## 4.6 Falhas, retry e cancelamento

| Falha | Comportamento |
|---|---|
| timeout de um membro | registrar `member_timeout`; continuar se quorum permitir |
| timeout global | pausar ou produzir decisão parcial conforme policy do Council |
| provider indisponível | retry limitado com backoff e mesma idempotency key |
| resposta inválida | registrar erro de schema; retry uma vez; depois marcar membro falho |
| orçamento esgotado | parar novas chamadas; persistir `budget_exhausted` |
| restart do processo | replay + retomar do último estado sem repetir efeitos concluídos |
| cancelamento | persistir `session-aborted`; não apagar posições já gravadas |
| decisão duplicada | devolver DecisionRecord existente |

## 4.7 Critérios de aceite da Frente A

- dois ou mais Bots reais participam em um teste com providers configurados;
- posições independentes são diferentes de posições de síntese;
- ao menos um teste contém dissenso persistido;
- matar o processo entre duas fases e reiniciar retoma a sessão;
- `max_rounds`, `max_tokens`, `max_cost_usd`, `timeout_seconds` e `max_members` são limites duros;
- repetir a mesma chamada com `idempotency_key` não duplica sessão, Leafs nem decisão;
- nenhum side effect ocorre antes do policy gate e do intent durável;
- o DecisionRecord contém versões de identidade, Leafs, evidências, custo e dissenso.

---

# 5. Frente B — Curador de evolução em background

## 5.1 Objetivo

Criar um `EvolutionCurator` que leia experiências reais e produza **rascunhos** de propostas; ele nunca promove nem altera identidade sozinho.

O analisador existente (`OuroborosAnalyzer`) deve continuar sendo uma camada de cálculo mensurável. O Curator adiciona:

1. janela temporal e escopo por Bot;
2. agrupamento de sucessos/falhas;
3. limiar mínimo de evidência;
4. deduplicação por fingerprint;
5. criação idempotente de `EvolutionProposal(status="draft")`;
6. relatório de explicabilidade;
7. ligação com cron/maintenance;
8. métricas e dry-run.

## 5.2 Algoritmo

```text
ler watermark do último curator-run
      |
      v
carregar ExperienceEvents novos e janela histórica
      |
      v
agrupar por bot, domínio, tipo de evento, modelo, rota e categoria
      |
      v
calcular taxa de falha, sucesso, custo, latência e recorrência
      |
      v
exigir N evidências independentes e qualidade mínima
      |
      v
gerar sinais com rationale + evidence_refs
      |
      v
deduplicar pelo fingerprint estável
      |
      v
criar proposta draft ou atualizar observação de proposta existente
      |
      v
emitir relatório, métrica e notificação opcional
```

Exemplo de política de sinais:

- pelo menos 3 eventos em janela de 7 dias para sinal operacional;
- pelo menos 2 falhas da mesma categoria em rotas distintas para fallback;
- nenhuma proposta de `identity-critical` sem revisão humana e Conselho;
- sucesso isolado não gera alteração de valores;
- falhas sem evidência suficiente geram `observation`, não proposta;
- uma proposta repetida deve reutilizar `proposal_id` determinístico.

## 5.3 Integração com manutenção diária

Preferência de implementação:

- adicionar um job idempotente ao mecanismo de cron existente, em vez de criar um daemon paralelo;
- execução por perfil, dentro do escopo do perfil correto;
- `skip_memory=True` se o caminho de cron assim exigir;
- lock por perfil para impedir dois curators simultâneos;
- timeout próprio e limite de lote;
- registrar `civ.evolution.curator-run` com `run_id`, janela, watermark, contagens e falhas;
- falha do curator não pode interromper a manutenção dos demais subsistemas.

Configuração em `config.yaml` (não em novo `HERMES_*` para configuração não secreta):

```yaml
civilization:
  evolution_curator:
    enabled: false
    schedule: "0 3 * * *"
    window_days: 7
    min_evidence: 3
    max_bots_per_run: 50
    max_proposals_per_run: 20
    dry_run: true
    notify: false
```

A configuração deve obedecer ao contrato do `DEFAULT_CONFIG`: toda chave registrada precisa ter leitor em runtime e teste de configuração.

## 5.4 Review e promoção

Estados recomendados:

```text
draft -> review -> approved -> canary -> promoted
                    \-> rejected
canary -> rolled_back
```

Para cada proposta:

- base version hash obrigatório;
- evidence refs não vazias;
- diff de SOUL/IDENTITY/VALUES exibido de forma legível;
- classificação de risco recalculada, não confiada apenas ao caller;
- policy avaliada para o tipo de mudança;
- aprovação humana ou decisão de Council registrada;
- canário em profile isolado antes de promoção default-on;
- rollback cria versão compensatória, sem apagar eventos.

## 5.5 Critérios de aceite da Frente B

- job diário pode ser executado duas vezes sem propostas duplicadas;
- watermark e janela são reproduzíveis após restart;
- dados insuficientes não produzem proposta;
- toda proposta contém rationale numérica e evidências;
- `dry_run=true` não altera identidade nem status para promoted;
- uma proposta stale é recusada;
- promoção exige approval explícito e passa por canário;
- falha do Curator é observável e não quebra o scheduler.

---

# 6. Frente C — Council Memory em Markdown

## 6.1 Objetivo e limites

Projetar uma projeção humana-legível, não uma segunda fonte de verdade.

Fonte canônica:

```text
EventStore / eventos civ.*
```

Projeção derivada:

```text
<profile>/councils/<council_id>/MEMORY.md
```

O arquivo não deve ser lido como autoridade superior ao ledger. Edição manual precisa ter semântica explícita:

- modo somente-projeção: alterações manuais são sobrescritas/reconciliadas;
- modo anotação: seção `## Human Notes` é preservada, com hash e evento de edição;
- nunca interpretar Markdown editado como decisão aprovada sem comando/approval correspondente.

## 6.2 Formato proposto

```markdown
# Council Memory: architecture-council

- **Council ID:** `architecture-council`
- **Projection revision:** `42`
- **Generated at:** `2026-09-29T03:00:00Z`
- **Source cursor:** `events.db#1842`

## Purpose and Rules

...

## Stable Learnings

### 2026-09-28 — distributed storage
- **Assertion:** ...
- **Confidence:** 0.82
- **Provenance:** `dec-architecture-council-...`
- **Status:** active

## Decision Summaries

| Date | Decision | Confidence | Outcome | Evidence |
|---|---|---:|---|---|
| ... | ... | ... | ... | ... |

## Open Dissent and Risks

- ...

## Human Notes

<!-- preserved only under explicit annotation mode -->
```

## 6.3 Projection worker

O worker deve:

1. ler cursor da última projeção;
2. obter eventos novos do Council;
3. reconstruir a memória completa de forma determinística;
4. escrever arquivo temporário;
5. validar UTF-8, tamanho e ausência de segredos;
6. fazer replace atômico;
7. registrar checksum e cursor;
8. emitir `civ.memory.council-projection-updated`;
9. repetir com segurança após crash.

Se a projeção falhar, o EventStore continua íntegro e o backlog fica observável. O worker deve usar lock por Council e não pode bloquear a execução de novas sessões.

## 6.4 Confidencialidade

Antes de persistir texto:

- remover credenciais, tokens, cookies e conteúdo sensível de prompts;
- preferir resumo + referência de evidência;
- aplicar limite de tamanho por decisão e por arquivo;
- não projetar mensagens internas completas dos Bots;
- registrar hashes para verificação sem vazar conteúdo.

## 6.5 Critérios de aceite da Frente C

- replay dos mesmos eventos produz o mesmo Markdown, salvo timestamp explicitamente não determinístico;
- restart no meio da escrita não produz arquivo truncado;
- duas projeções concorrentes não corrompem o arquivo;
- decisão, dissenso, confiança e provenance aparecem no arquivo;
- alteração manual fora de `Human Notes` é detectada conforme a política escolhida;
- apagar a projeção permite reconstruí-la integralmente do EventStore;
- nenhum segredo conhecido pelos testes aparece no arquivo.

---

# 7. Frente D — Observatório Web: grafo social e DAG de linhagem

## 7.1 API de projeção

Manter a rota de overview atual como compatibilidade. Adicionar endpoints autenticados e read-only para dados estruturados:

```text
GET /api/civilization/graph?profile=<id>
GET /api/civilization/lineage?bot_id=<id>&profile=<id>
GET /api/civilization/councils/<id>/memory
GET /api/civilization/sessions/<id>
```

Resposta do grafo:

```json
{
  "nodes": [
    {"id":"bot:architect", "kind":"bot", "label":"Architect", "status":"active", "score":0.81},
    {"id":"council:arch", "kind":"council", "label":"Architecture", "version":1},
    {"id":"leaf:...", "kind":"leaf", "label":"...", "status":"completed"}
  ],
  "edges": [
    {"source":"bot:architect", "target":"council:arch", "kind":"member"},
    {"source":"bot:architect", "target":"bot:security", "kind":"collaboration", "weight":3},
    {"source":"leaf:...", "target":"decision:...", "kind":"evidence"}
  ],
  "cursor": 1842,
  "generated_at": "..."
}
```

Resposta da linhagem:

```json
{
  "bot_id": "architect-bot",
  "versions": [
    {"id":"ver-1", "version":1, "hash":"...", "status":"superseded", "parent_id":null},
    {"id":"ver-2", "version":2, "hash":"...", "status":"active", "parent_id":"ver-1", "proposal_id":"prop-..."}
  ],
  "edges": [{"source":"ver-1", "target":"ver-2", "kind":"derived"}],
  "rollback_edges": []
}
```

A rota deve seguir o padrão existente: token de sessão, escopo de perfil explícito, projeção allowlisted e SQLite read-only quando ler o EventStore.

## 7.2 Frontend

Adicionar à `CivilizationPage`:

1. seletor de modo: `estrutura`, `grafo social`, `linhagem`;
2. `CouncilGraph` em SVG ou Canvas, inicialmente sem dependência pesada;
3. zoom, pan, reset e filtro por entidade;
4. cores distintas para Bot, Council, Leaf, Decision, Proposal e Version;
5. tooltip com IDs, status, score e provenance;
6. click no nó abre o inspector existente;
7. grafo vazio, erro e dados parciais são estados explícitos;
8. acessibilidade: lista tabular equivalente ao gráfico;
9. nenhuma chamada de escrita sem endpoint e autorização explícitos;
10. snapshot do grafo não deve incluir conteúdo sensível dos prompts.

O DAG de versão deve deixar claro que rollback é uma nova versão compensatória, não apagamento de um ramo.

## 7.3 Performance e proteção

- limitar nós/arestas por resposta;
- paginação ou janela temporal para históricos grandes;
- cache por cursor do EventStore;
- abortar fetch ao trocar de perfil;
- não executar layout pesado a cada refresh de 30 segundos;
- preservar o funcionamento do painel PTY e do chat;
- não reconstruir o sistema de chat em React.

## 7.4 Critérios de aceite da Frente D

- o grafo exibe colaboração e reputação a partir de relações reais, sem arestas inventadas;
- o DAG mostra parent/child e rollback de versões;
- o endpoint respeita perfil e autenticação;
- a UI funciona com zero nós, nós órfãos e dados parciais;
- tabela acessível contém as mesmas relações do gráfico;
- testes Vitest cobrem renderização, erro API, troca de perfil e seleção;
- testes Python cobrem contrato JSON e isolamento de perfil sem inspecionar fontes TSX.

---

# 8. Frente transversal — Outbox/Inbox, idempotência e consistência

## 8.1 Por que é necessária

A especificação exige persistir comando/evento antes de publicar efeitos assíncronos. O EventStore atual permite replay, mas o Council precisa de uma semântica explícita para:

- dispatch de Leafs;
- chamada ao provider;
- ação pós-decisão;
- projeção Markdown;
- notificações;
- retries após restart.

## 8.2 Modelo

Tabelas ou equivalente event-sourced:

```text
civ_inbox(
  command_id PRIMARY KEY,
  aggregate_id,
  command_type,
  payload_hash,
  result_ref,
  status,
  received_at,
  completed_at
)

civ_outbox(
  effect_id PRIMARY KEY,
  aggregate_id,
  effect_type,
  payload,
  available_at,
  attempts,
  lease_owner,
  lease_until,
  last_error,
  status
)
```

Fluxo:

```text
BEGIN
  dedupe command_id
  validar revision/precondições
  append domain event
  insert outbox intent
COMMIT

worker claim lease
  executar efeito idempotente
  append success/failure event
  acknowledge effect
```

## 8.3 Regras de retry

- backoff exponencial com jitter e máximo configurável;
- lease expira e permite reclaim;
- handler verifica `effect_id` antes de side effect;
- erro permanente vai para dead-letter/status failed;
- retry nunca gera novo `leaf_id` para o mesmo intent;
- métricas: backlog, idade, retries, dead letters, tempo de replay.

## 8.4 Critérios de aceite

- crash antes do commit não deixa efeito externo;
- crash depois do commit e antes da execução permite retry;
- crash depois do efeito e antes do ack não duplica o efeito;
- comando repetido devolve o resultado persistido;
- dead-letter é operável e auditável;
- replay não publica side effects novamente.

---

# 9. Frente transversal — Paridade Rust/Python

## 9.1 Estado e estratégia

A crate `packages/haos-civ` já possui modelos, EventStore, identidade, Leaf, Council básico, sociedade, evolução e civilização. Isso é uma base forte, mas não equivale ainda ao runtime completo do Council.

A estratégia recomendada é:

1. declarar um schema canônico versionado;
2. gerar/validar fixtures JSON compartilhadas;
3. manter Python como caminho operacional inicial do runner;
4. implementar no Rust primeiro replay, orçamento, dedupe e projeções puras;
5. só então mover workers/side effects de forma incremental;
6. comparar byte-for-byte apenas onde a serialização é canônica; para timestamps/ordenação usar contratos semânticos;
7. manter feature flag de backend e rollback imediato para Python.

## 9.2 Escopo Rust por fatia

| Fatia | Rust necessário |
|---|---|
| Schema | structs com `schema_version`, serde e fixtures |
| Replay | materialização determinística da FSM completa |
| Budget | contador de tokens/custo/rounds/tempo e stop duro |
| Idempotência | inbox/outbox e command dedupe |
| Projection | Council graph/lineage read model ou API de consulta |
| C-ABI | somente funções puras/estáveis, com free explícito |
| HTTP | endpoints read-only e health/version matrix |
| Runner LLM | somente após contrato de provider e cancelamento estabilizado |

## 9.3 Critérios de aceite

- fixtures criadas em Python são lidas e reproduzidas no Rust;
- replay Python e Rust produz o mesmo estado semântico;
- stale-base, rollback e policy deny têm o mesmo resultado;
- C-ABI não vaza memória em erros/nulos;
- `cargo test -p haos-civ` e testes Python de paridade são gates de CI;
- a flag de backend permite voltar ao Python sem migração destrutiva.

---

# 10. Frente transversal — policy, aprovação e segurança

## 10.1 Policy gate enriquecido

Evoluir `ConstitutionRule` para suportar, de forma compatível:

```text
subject_selector
action
resource_selector
risk_class
required_approvals
conditions
expires_at
```

A avaliação deve devolver:

```json
{
  "result": "allow|deny|require_approval",
  "rule_id": "...",
  "constitution_version": 3,
  "required_approvals": ["human", "security-council"],
  "reason": "...",
  "decision_id": "pol-..."
}
```

Hard deny continua fail-closed. Advisory não vira allow só porque existe um campo `approved=true` no payload; a aprovação deve apontar para um registro autenticado, com `approver`, timestamp, escopo e hash da ação.

## 10.2 Ações de risco

Classificar pelo menos:

- `read_only_analysis`;
- `write_workspace`;
- `network_change`;
- `credential_access`;
- `identity_change`;
- `production_deploy`;
- `evolution_promote`.

Toda ação de produção exige action intent, policy decision, approval adequada e resultado posterior.

## 10.3 Threat model mínimo

Testar explicitamente:

- prompt injection tentando alterar regras do Council;
- membro tentando se auto-endossar;
- Bot não membro submetendo posição;
- profile A lendo memória de profile B;
- path traversal em Council/Bot/Memory;
- replay de approval antigo em outra versão;
- duplicate command com payload diferente;
- exposição de secrets em Markdown, eventos, graph ou logs;
- provider malformado retornando JSON não validável.

---

# 11. Sequência de implementação por fases

## Fase 0 — Contrato e observabilidade (1 fatia)

**Objetivo:** congelar schemas, eventos, IDs e critérios antes do runner.

Entregas:

- `CouncilSession` com `revision`, budget, status e timestamps UTC;
- envelope de eventos comum;
- inbox/outbox mínimo;
- fixtures JSON;
- métricas e correlation IDs;
- feature flags desligadas por padrão.

Gate:

- round-trip;
- replay;
- dedupe;
- crash simulation;
- testes de profile isolation.

## Fase 1 — Runner de análise independente

**Objetivo:** dois Leafs reais executando em paralelo, sem debate ainda.

Entregas:

- `member_runner.py`;
- resolução de identidade e snapshot;
- budget de fan-out/tokens/custo;
- gravação de posições e traces;
- timeout/retry/cancelamento;
- CLI `hermes civ deliberate --council ... --goal ... --dry-run`.

Rollout: opt-in por Council.

## Fase 2 — Debate e síntese

**Objetivo:** FSM multi-round com dissent e DecisionRecord completo.

Entregas:

- `CouncilDebateRunner`;
- `debate_round` persistente;
- sintetizador estruturado;
- retomada após restart;
- action gate separado de decisão;
- CLI/status JSON.

Rollout: shadow, sem executar ação externa.

## Fase 3 — Policy/action gate

**Objetivo:** permitir ação apenas após policy e aprovação.

Entregas:

- policy contextual;
- approval records;
- outbox de action intents;
- worker de execução idempotente;
- deny/advisory/timeout tests.

Rollout: opt-in e apenas ações de baixo risco.

## Fase 4 — Curator de evolução

**Objetivo:** converter experiências em drafts explicáveis.

Entregas:

- `EvolutionCurator`;
- watermark e janela;
- job diário idempotente;
- config registry/defaults;
- dashboard de drafts;
- review/canary/rollback.

Rollout: dry-run -> draft-only.

## Fase 5 — Council Memory Markdown

**Objetivo:** projeção humana-legível e reconstruível.

Entregas:

- projection worker;
- lock/atomic replace;
- `MEMORY.md` format;
- human notes policy;
- endpoint autenticado read-only;
- testes de rebuild e secrets.

Rollout: shadow projection -> habilitado por Council.

## Fase 6 — Graph Observatory

**Objetivo:** visualizar o que já é registrado.

Entregas:

- graph/lineage API;
- `CouncilGraph`;
- `VersionDag`;
- tabela acessível;
- filtros/performance;
- testes backend/frontend.

Rollout: read-only, sem risco de execução.

## Fase 7 — Rust parity e canário

**Objetivo:** mover replay/projections/budget/idempotência em fatias.

Entregas:

- fixtures cruzadas;
- endpoints Rust opcionais;
- matriz de versão;
- comparação semântica;
- rollback de backend.

Rollout: shadow -> canary por profile -> default-on.

## Fase 8 — Promoção controlada

**Objetivo:** tornar o Council operacional por padrão somente com métricas.

Pré-requisitos:

- taxa de sucesso mínima definida;
- zero duplicate effects nos testes de soak;
- backlog outbox dentro do limite;
- p95 de debate conhecido;
- nenhum deny bypass;
- rollback ensaiado;
- aprovação operacional registrada.

---

# 12. CLI, API e operações previstas

## 12.1 CLI

Comandos propostos, preservando o padrão de subcomandos existente:

```text
hermes civ council list
hermes civ deliberate --council <id> --goal <text> [--dry-run] [--idempotency-key <key>]
hermes civ session show <session-id>
hermes civ session cancel <session-id>
hermes civ session resume <session-id>
hermes civ decision show <decision-id>
hermes civ evolution-curator run [--dry-run]
hermes civ evolution list
hermes civ evolution review <proposal-id> --approve|--reject --reason <text>
hermes civ evolution canary <proposal-id>
hermes civ evolution promote <proposal-id>
hermes civ evolution rollback <bot-id> --target-version <id>
hermes civ reconcile
```

Regras:

- comandos mutáveis exigem `command_id`/idempotency key;
- saída `--json` é estável e versionada;
- modo humano não esconde erros ou status intermediários;
- `--dry-run` não cria side effect externo;
- nenhuma aprovação deve ser inferida de texto livre ambíguo.

## 12.2 API

Todos os endpoints mutáveis devem:

- exigir token de sessão;
- exigir perfil explícito ou escopo validado;
- aceitar `Idempotency-Key`;
- verificar `revision`/ETag quando houver concorrência;
- responder com status persistido, não apenas “accepted” sem rastreio;
- registrar actor/correlation/causation;
- não retornar prompt ou memória sensível por padrão.

---

# 13. Runbook operacional

## 13.1 Pré-voo

```bash
pwd
python --version
cargo --version
node --version
./.venv/bin/pytest -q tests/platform/civilization tests/platform/council tests/platform/evolution
cargo test -p haos-civ
```

Verificar:

- branch e working tree;
- perfil de teste isolado;
- backup/snapshot do EventStore;
- feature flags e backend ativo;
- providers e timeouts;
- relógio UTC e espaço em disco;
- dashboard/web watcher somente se a frente Web estiver sendo testada.

Nunca usar o perfil de produção para testes de aprovação, canário ou falha forçada.

## 13.2 Iniciar um debate em shadow

```bash
hermes civ deliberate \
  --council arch-sec-council \
  --goal "Migrar o banco para cluster distribuído" \
  --dry-run \
  --json
```

Conferir:

1. `session_id` e `correlation_id` persistidos;
2. dois ou mais membros selecionados;
3. Leafs vinculados a versões de identidade;
4. orçamento consumido e restante;
5. nenhum action intent executado;
6. DecisionRecord ou status parcial com dissent;
7. eventos no perfil correto;
8. nenhum segredo nos logs.

## 13.3 Retomar após restart

```bash
hermes civ session show <session-id> --json
hermes civ session resume <session-id> --json
hermes civ reconcile
```

Se houver outbox pendente:

```bash
hermes civ outbox status
hermes civ outbox retry <effect-id>
hermes civ outbox dead-letter list
```

A retomada deve reutilizar Leafs/intents existentes. Não criar novos IDs por simples retry.

## 13.4 Aprovar ação

Antes da aprovação:

- confirmar DecisionRecord;
- ler dissent e assumptions;
- confirmar policy decision e constitution version;
- confirmar diff de recursos e risco;
- confirmar budget restante;
- confirmar evidence refs;
- confirmar que a base de identidade não ficou stale.

Depois:

```bash
hermes civ decision approve-action <decision-id> \
  --approver operator:<name> \
  --scope "resource:<id>" \
  --json
```

A aprovação deve gerar evento e action intent. O worker executa o intent e registra sucesso/falha.

## 13.5 Rodar o Curator

```bash
hermes civ evolution-curator run --dry-run --json
hermes civ evolution list --status draft
```

Verificar:

- janela e watermark;
- número de experiências lidas;
- sinais descartados por evidência insuficiente;
- proposals deduplicadas;
- nenhum status promovido;
- erros por Bot;
- próximo horário do job diário.

## 13.6 Revisar e promover evolução

```bash
hermes civ evolution show <proposal-id> --json
hermes civ evolution review <proposal-id> --approve --reason "..."
hermes civ evolution canary <proposal-id> --profile civ-canary
hermes civ evolution promote <proposal-id> --target default
```

Promover apenas se:

- holdout gate aprovado;
- canário respeitou policy;
- não houve regressão nos critérios mínimos;
- versão ativa continua compatível;
- operador tem rollback pronto.

## 13.7 Reconciliação diária

Executar ou agendar:

```bash
hermes civ reconcile --max-age-seconds 3600 --json
```

Verificar:

- Leafs ativos órfãos;
- sessões pausadas por timeout;
- outbox atrasado;
- projections pendentes;
- versões ativas por Bot;
- propostas stale;
- divergência de backend Python/Rust;
- erros repetidos por provider.

## 13.8 Incidente: decisão incorreta ou ação indevida

1. interromper novas ações do Council afetado;
2. aplicar hard deny/feature flag de kill switch;
3. preservar EventStore, logs e hashes;
4. localizar `session_id`, `decision_id`, `action_intent_id`;
5. verificar policy decision e approval;
6. executar rollback operacional, se suportado;
7. executar rollback de identidade como versão compensatória, nunca apagar histórico;
8. marcar proposals e sessions impactadas;
9. gerar relatório de causa raiz;
10. só reabilitar após teste de replay e aprovação.

## 13.9 Incidente: backlog outbox

Indicadores:

- idade do item mais antigo;
- retries por efeito;
- leases expiradas;
- dead letters;
- taxa de sucesso do worker.

Ações:

1. parar promoção canary/default-on;
2. verificar locks, provider e espaço em disco;
3. não limpar registros manualmente;
4. retry de item isolado após validar idempotência;
5. mover para dead-letter se erro permanente;
6. reconciliar projeções e sessões;
7. registrar causa e métrica de recuperação.

## 13.10 Incidente: corrupção/divergência de memória Markdown

1. tratar EventStore como fonte canônica;
2. congelar apenas o projection worker do Council afetado;
3. copiar arquivo suspeito para evidência;
4. remover/recriar a projeção via rebuild atômico;
5. comparar cursor e checksum;
6. preservar `Human Notes` somente se o modo estiver habilitado;
7. não editar eventos para “consertar” o Markdown.

---

# 14. Plano de testes e gates

## 14.1 Unitários

- transições válidas e inválidas da FSM;
- budget decrementa atomicamente;
- parser/validator da síntese;
- fingerprint da proposta;
- Markdown escaping/redaction;
- layout de grafo com nós órfãos;
- serialização Python/Rust;
- policy selector e approval scope.

## 14.2 Integração real

Com dois perfis temporários `A -> B -> A` quando houver scope:

- iniciar sessão no perfil A;
- confirmar que B não enxerga eventos/memória de A;
- reiniciar processo;
- retomar sessão;
- conferir EventStore e projeções;
- verificar que nenhuma escrita ocorreu no perfil errado.

## 14.3 E2E

Cenário feliz:

```text
Council -> 2 Bots -> Leafs -> debate -> dissent -> synthesis
-> policy allow -> action intent -> execution -> reputation/experience
-> MEMORY.md -> graph -> curator draft
```

Cenários de falha:

- membro timeout;
- provider inválido;
- budget exhausted;
- duplicate command;
- crash entre commit e worker;
- stale evolution proposal;
- hard deny;
- advisory sem approval;
- rollback de identidade;
- projection interrompida;
- backend Rust desligado.

## 14.4 Soak/recovery

Executar uma bateria com:

- múltiplas sessões concorrentes;
- restart forçado em cada fase;
- retries e leases expiradas;
- canário com rollback;
- volume crescente de eventos;
- dashboard aberto durante escrita;
- cron curator concorrendo com reconcile.

Métricas mínimas:

```text
council_sessions_total
council_session_success_rate
council_decision_latency_p50/p95/p99
council_member_timeouts_total
council_budget_exhausted_total
council_retries_total
civ_inbox_duplicates_total
civ_outbox_backlog
civ_outbox_oldest_age
civ_projection_failures_total
civ_curator_proposals_total
civ_evolution_promotions_total
civ_evolution_rollbacks_total
```

---

# 15. Rollout, promoção e rollback

## 15.1 Estados de rollout

| Estado | Comportamento | Critério de avanço |
|---|---|---|
| Shadow | calcula e registra; não executa ação | replay, métricas e zero bypass |
| Opt-in | operador inicia Council explicitamente | E2E + recovery + budget hard limit |
| Canary | pequeno conjunto de perfis/Councils | p95, erro, custo e backlog dentro do limite |
| Default-on | caminho padrão para casos elegíveis | soak e aprovação operacional |
| Legacy removal | retirar caminho antigo | paridade, documentação e rollback não mais necessário |

## 15.2 Kill switches

Cada superfície deve possuir flag independente:

```yaml
civilization:
  council_runner_enabled: false
  council_action_execution_enabled: false
  evolution_curator_enabled: false
  council_memory_projection_enabled: false
  graph_observatory_enabled: true
  rust_backend_enabled: false
```

Desligar o runner não deve apagar sessões existentes. Desligar action execution deve deixar decisões visíveis e intents pendentes. Desligar Rust deve voltar ao Python sem alterar eventos.

## 15.3 Critérios de promoção quantitativos

Os valores exatos devem ser calibrados com baseline, mas a promoção precisa registrar limiares concretos para:

- taxa de sucesso;
- p95 de latência;
- custo por sessão;
- taxa de timeout;
- duplicate effect rate = zero;
- hard-deny bypass = zero;
- projection loss = zero;
- backlog máximo e idade máxima;
- rollback test pass rate = 100%.

Nenhuma declaração de “default-on” deve ser feita sem esses números no relatório do canário.

---

# 16. Checklist de implementação por PR

Cada fatia vertical deve conter:

- [ ] modelo/schema e `schema_version`;
- [ ] persistência/replay;
- [ ] comando idempotente;
- [ ] evento e correlation/causation IDs;
- [ ] API/CLI ou consumidor real;
- [ ] métrica/log redigido;
- [ ] feature flag com caminho de rollback;
- [ ] teste unitário;
- [ ] teste de integração com EventStore real;
- [ ] teste de crash/restart ou justificativa;
- [ ] teste de perfil A/B se houver escopo;
- [ ] documentação/runbook atualizada;
- [ ] `git diff --check`;
- [ ] nenhuma mudança de prompt no meio de conversa;
- [ ] nenhum novo core tool sem justificativa pelo Footprint Ladder;
- [ ] nenhuma configuração comportamental nova em `.env`.

---

# 17. Ordem recomendada de execução

1. congelar schema/envelope e invariantes;
2. implementar inbox/outbox e revision para o Council;
3. implementar `BudgetController`;
4. implementar member runner com dois Leafs reais;
5. implementar FSM de debate e síntese;
6. adicionar recovery/retry/cancelamento;
7. ligar policy/action gate sem side effect por padrão;
8. adicionar CLI/API autenticados;
9. implementar Curator + job diário em draft-only;
10. implementar projection `MEMORY.md`;
11. expor graph/lineage API;
12. implementar UI read-only;
13. criar fixtures e replay Rust/Python;
14. executar canário e soak;
15. somente então habilitar promoções e execução default-on.

---

# 18. Definição de pronto do programa

O programa pode ser considerado concluído quando todos os itens abaixo forem demonstrados em ambiente isolado e em um canário controlado:

- [ ] Council inicia e retoma uma sessão após restart;
- [ ] pelo menos dois Bots reais executam análises independentes;
- [ ] debate multi-turn registra posições, síntese e dissent;
- [ ] orçamento duro interrompe chamadas antes do limite exceder;
- [ ] duplicate command/side effect é idempotente;
- [ ] policy hard deny e advisory approval são fail-closed;
- [ ] DecisionRecord aponta para versões de identidade, Leafs e evidências;
- [ ] execução gera reputação e experiência reais;
- [ ] Curator produz drafts explicáveis a partir de experiências reais;
- [ ] nenhuma proposta é promovida sem aprovação e canário;
- [ ] rollback cria versão compensatória e restaura o caminho anterior;
- [ ] `councils/<id>/MEMORY.md` é reconstruível do ledger;
- [ ] grafo social e DAG exibem apenas relações persistidas;
- [ ] Rust e Python reproduzem o mesmo estado semântico;
- [ ] dashboard continua autenticado, profile-scoped e não destrutivo;
- [ ] reconcile detecta órfãos, backlog e divergências;
- [ ] métricas e runbook permitem operar sem intervenção ad hoc;
- [ ] flags de rollback foram efetivamente testadas.

Até que esses critérios estejam demonstrados, a classificação correta é **parcialmente implementado / opt-in**, e não “runtime de Council autônomo completo”.

---

## Referências internas

- `HAOS_AGENT_IDENTITY_COUNCIL_PLAN.md`
- `HAOS_AGENT_IDENTITY_COUNCIL_IMPLEMENTATION_TASKS.md`
- `haos-civ/runtime/COUNCIL_ENGINE_SPEC.md`
- `hermes/platform/council/manager.py`
- `hermes/platform/civilization/delegation.py`
- `hermes/platform/evolution/analyzer.py`
- `hermes/platform/evolution/bot_evolution.py`
- `hermes/platform/evolution/ledger.py`
- `hermes/platform/civilization/manager.py`
- `hermes_cli/web_routers/civilization.py`
- `web/src/pages/CivilizationPage.tsx`
- `packages/haos-civ/src/`
- `cron/AGENTS.md`
- `web/AGENTS.md`
