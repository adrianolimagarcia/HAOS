---
id: ADR-019
titulo: Absorção dos Conceitos Ruflo, Google ADK, Herdr e Powerline (Agent Mesh, Event Bus, Capability Router e Operational Context HUD)
status: Aceito
data: '2026-10-01'
decidido_por: human:adriano
autor: adriano / HAOS Agent
contexto: Unificação da topologia multiagente, barramento de eventos cognitivos, roteamento ponderado por confiança e HUD operacional no HAOS
causado_by:
  - adr:ADR-001
  - adr:ADR-002
  - adr:ADR-003
  - adr:ADR-014
  - adr:ADR-017
  - adr:ADR-018
  - necessidade de coordenação declarativa de agentes, rastreabilidade de eventos e visualização de estado cognitivo
evidence:
  - Ruflo SOTA: grafo de agentes onde nós são agentes/workflows e arestas são dependências/mensagens integrados ao RAGGraph
  - Google ADK (Agent Development Kit): barramento de eventos cognitivos padronizado (agent.started, agent.called_tool, agent.created_memory, agent.routed, agent.completed, agent.failed, agent.learned_rule, agent.corrected)
  - Herdr: roteamento declarativo por capacidades com micro-aprendizado de pontuação de confiança (reforço e penalização contínuos)
  - Powerline HUD: visualizador sintético em linha única do estado cognitivo do sistema (Memória, Grafo, Mesh, Sentinel, Goal)
affects:
  - packages/haos-edge/src/raggraph.rs
  - packages/haos-edge/src/lib.rs
  - hermes/platform/execution/agent_mesh.py
  - web/src/components/CivConstellation.tsx
  - web/src/components/ObsidianGraphView.tsx
  - tests/platform/test_agent_mesh_e2e.py
supersedes: []
superseded_by: null
prov:
  wasGeneratedBy: task:adrs-019-living-wiki-agent-mesh
  wasAssociatedWith: agent:haos-cachyos-x8664
  wasDerivedFrom: adr:ADR-017
  causado_by: integração do Agent Mesh, Event Bus, Capability Router e Powerline HUD ao núcleo HAOS
  affects:
    - packages/haos-edge/src/raggraph.rs
    - hermes/platform/execution/agent_mesh.py
    - tests/platform/test_agent_mesh_e2e.py
  supersedes: []
  superseded_by: null
---

# ADR-019: Absorção dos Conceitos Ruflo, Google ADK, Herdr e Powerline (Agent Mesh, Event Bus, Capability Router e Operational Context HUD)

## 1. Contexto e Motivação

Com a consolidação do motor nativo em Rust no `haos-edge` (ADR-017) e do sistema de *Parent-Child Chunking* com promoção automática de memórias baseada em confiança (ADR-018), o HAOS atingiu latências submilisegundo em busca vetorial e navegação em grafos causais. No entanto, o ecossistema de agentes especializados apresentava lacunas críticas de coordenação e observabilidade:

1. **Topologia Multiagente Desconectada do Grafo de Conhecimento**: Workflows e agentes eram tratados como processos efêmeros, desacoplados das arestas de causa e efeito do RAGGraph. As relações de dependência entre tarefas não formavam um grafo navegável unificado com as memórias de longo prazo.
2. **Ausência de um Barramento Canônico de Eventos de Ciclo de Vida**: As transições de estado dos agentes (início, chamadas de ferramentas, criação de memória, falhas, regras aprendidas e correções) ocorriam em canais heterogêneos ou logs desestruturados, sem persistência causal.
3. **Roteamento Estático ou Rígido de Tarefas**: A delegação de subtarefas dependia de heurísticas pré-fixadas, desprovidas de calibração dinâmica de confiança baseada no histórico de acertos e falhas de cada agente especializado.
4. **Falta de Feedback Operacional Sintético**: A ausência de um indicador operacional em linha única impossibilitava operadores humanos e modelos supervisores de inspecionarem instantaneamente a "saúde cognitiva" do sistema (saturação de memória, integridade do grafo, acurácia do mesh e ausência de contradições).

Para resolver esses desafios estruturais de forma definitiva e elegante, o HAOS absorve e integra os melhores padrões dos ecossistemas **Ruflo**, **Google ADK**, **Herdr** e **Powerline**.

---

## 2. As Quatro Absorções Arquiteturais

### 2.1 Ruflo: Agent Graph & Mesh Topology
O framework Ruflo estabelece que equipes de agentes e fluxos de execução devem ser modelados como topologias de malha (*Mesh Topology*) expressas na forma de nós e arestas no Grafo de Conhecimento.

- **Nós de Agente e Workflow**: Cada agente especializado (`AgentProfile`) e cada fluxo de trabalho (`WorkflowDAG`) é representado como um nó rastreável (`haos_graph_nodes`).
- **Arestas de Dependência e Coordenação**: Passos dependentes conectam-se via arestas tipadas (`DEPENDS_ON`, `COORDINATES`, `REFERENCES`).
- **Execução Declarativa e Sem Ciclos**: A classe `WorkflowDAG` valida estritamente a aciclicidade da cadeia usando o algoritmo de ordenação topológica de Kahn. Ciclos são rejeitados em tempo de compilação/validação (`ValueError`), garantindo determinismo na execução.

### 2.2 Google ADK: Agent Event Bus
Do Google Agent Development Kit (ADK), o HAOS absorve o barramento de eventos cognitivos padronizado (`AgentEventBus`). O ciclo de vida operacional é segmentado em eventos canônicos com payloads fortemente tipados:

- `agent.started`: Início de sessão ou atribuição de um passo de workflow ao agente.
- `agent.called_tool` / `agent.tool_called`: Invocação de ferramenta local ou FFI com parâmetros registrados.
- `agent.created_memory` / `agent.memory_created`: Criação ou promoção de fato consolidado no Memory Fabric.
- `agent.routed`: Seleção determinística do agente executor para uma tarefa.
- `agent.completed`: Conclusão bem-sucedida de tarefa ou passo do workflow.
- `agent.failed`: Falha ou interrupção anômala com registro do traceback.
- `agent.learned_rule`: Extração e aquisição de nova regra procedural ou heurística.
- `agent.corrected`: Disparo de auto-correção ou intervenção por supervisor.

**Persistência Dual Zero-Overhead**: O `AgentEventBus` despacha para ouvintes em memória com latência sub-microssegundo e, simultaneamente, grava os eventos na tabela `haos_agent_events` e no grafo (`haos_graph_nodes` como nós do tipo `agent_event` e `haos_graph_edges` conectando ao `target_ref`), seja via C-ABI Rust de alta performance (`libhaos_edge.so`), seja via fallback direto em SQLite.

### 2.3 Herdr: Agent Capability Registry & Router Ponderado por Confiança
Do projeto Herdr, o HAOS absorve o desacoplamento entre declaração de capacidades e roteamento adaptativo:

- **`AgentCapabilityRegistry`**: Repositório central de capacidades granulares (`AgentCapability`), categorizadas e indexadas por palavras-chave técnicas e conceituais.
- **`AgentRouter` (e alias canônico `AgentMeshRouter`)**: Avalia a descrição textual da tarefa, calcula a pontuação de similaridade léxica/semântica contra os registros de capacidades e **pondera o resultado pelo score contínuo de confiança (`trust_score`) de cada agente**.
- **Micro-Aprendizado Contínuo (Reinforcement & Penalization)**:
  - **Sucesso (`reinforce`)**: Cada tarefa concluída com êxito eleva o `trust_score` do agente em $+0.05$ (teto $1.00$) e incrementa `success_count`.
  - **Falha (`penalize`)**: Cada falha ou alucinação reduz o `trust_score` em $-0.10$ (piso $0.10$) e incrementa `failure_count`.
  - Agentes que degradam seu desempenho são dinamicamente preteridos pelo roteador em favor de nós mais confiáveis.

### 2.4 Powerline: Dynamic Operational Context HUD
Inspirado na arquitetura do Powerline e nos cabeçalhos de status operacional, o HAOS introduz o `PowerlineContextHUD`. Ele sintetiza o estado cognitivo em uma tupla estruturada de alta densidade informativa, renderizável em CLI, injeção de prompt e Web UI:

- `[MEM: 100% | N facts]` — Saúde e volume de fatos retidos no Memory Fabric.
- `[GRAPH: N nodes | M edges]` — Topologia ativa do RAGGraph causal.
- `[MESH: K agents | trust X.XX]` — Tamanho da equipe de agentes e confiança média ponderada.
- `[SENTINEL: 0 conflicts | verified]` — Integridade das restrições de segurança e consistência lógica.
- `[GOAL: Active Goal | conf Y.YY]` — Postura estratégica e objetivo ativo em execução.
- Glifos de transição Powerline (``, `│`) para exibição em terminal e dashboards.

---

## 3. Diagrama Conceitual: Intelligence Graph Unificado

No Intelligence Graph do HAOS, Memória, Agentes, Ferramentas e Eventos coexistem como nós homogêneos interconectados no mesmo espaço topológico de dados:

```
+-----------------------------------------------------------------------------------+
|                            HAOS INTELLIGENCE GRAPH                                |
+-----------------------------------------------------------------------------------+
|                                                                                   |
|    +--------------------+                     +--------------------+              |
|    |   Memory Record    |<----REFERENCES------|    Agent Event     |              |
|    | (ADR, Fact, Rule)  |                     |  (Lifecycle Trace) |              |
|    +--------------------+                     +--------------------+              |
|              ^                                          ^                         |
|              |                                          |                         |
|        CREATES_MEMORY                                EMITTED_BY                   |
|              |                                          |                         |
|    +--------------------+                     +--------------------+              |
|    |    Agent Profile   |-------EXECUTES----->|   Workflow DAG     |              |
|    | (Trust & Competence|                     |  (Step Dependencies|              |
|    +--------------------+                     +--------------------+              |
|              |                                          |                         |
|          PROVIDES                                    REQUIRES                     |
|              v                                          v                         |
|    +--------------------+                     +--------------------+              |
|    |  Agent Capability  |<---SATISFIED_BY-----|   Workflow Step    |              |
|    | (Keywords, Domain) |                     |   (Task Payload)   |              |
|    +--------------------+                     +--------------------+              |
|                                                                                   |
+-----------------------------------------------------------------------------------+
```

### Relações Semânticas Principais:
1. `(AgentProfile)-[:PROVIDES]->(AgentCapability)`: Mapeamento de competências declaradas.
2. `(AgentProfile)-[:EXECUTES]->(WorkflowStep)`: Atribuição decidida pelo `AgentRouter`.
3. `(WorkflowStep)-[:DEPENDS_ON]->(WorkflowStep)`: Restrição causal de precedência.
4. `(AgentProfile)-[:EMITS]->(AgentEvent)`: Registro histórico auditável no barramento.
5. `(AgentEvent)-[:REFERENCES]->(MemoryRecord)`: Proveniência direta entre o evento de execução e os fatos gerados ou consumidos.

---

## 4. Impactos Arquiteturais e Implementação

### 4.1 No Motor Rust (`packages/haos-edge`)
- **Tabelas Nativas no `raggraph.rs`**:
  - `haos_agent_events`: Armazenamento colunar de eventos de agentes (`event_id`, `event_type`, `agent_id`, `payload_json`, `timestamp`, `target_ref`, `created_at`).
  - Criação automática dos nós correspondentes em `haos_graph_nodes` e arestas de relacionamento em `haos_graph_edges`.
- **C-ABI Export em `packages/haos-edge/src/lib.rs`**:
  - `raggraph_record_agent_event`: Função exportada via FFI `extern "C"` que recebe strings UTF-8 e timestamp, operando diretamente sobre o banco SQLite de alta velocidade com zero cópias desnecessárias.

### 4.2 Na Plataforma Python (`hermes`)
- **Módulo `hermes/platform/execution/agent_mesh.py`**:
  - `AgentCapability` & `AgentProfile`: Estruturas de dados tipadas com dataclasses.
  - `AgentCapabilityRegistry`: Registro, busca e indexação por capacidades e palavras-chave.
  - `AgentEvent` & `AgentEventBus`: Barramento Pub/Sub com gravação nativa via C-ABI e fallback determinístico SQLite.
  - `AgentRouter` / `AgentMeshRouter`: Roteamento baseado em pontuação combinada $(MatchPoints + 1.0) \times TrustScore$, com métodos `reinforce()` e `penalize()`.
  - `WorkflowStep` & `WorkflowDAG`: Construtor e orquestrador de grafos de tarefas com validação acíclica de Kahn e propagação de contexto upstream $\rightarrow$ downstream.
  - `PowerlineContextHUD`: Renderizador de telemetria cognitiva em linha única.
- **Invariantes Arquiteturais**:
  - Conformidade estrita com **PEP-420** (ausência absoluta de `__init__.py` no namespace `hermes/platform/`).
  - Apenas bibliotecas da Standard Library Python nas dependências de plataforma.

### 4.3 Na Interface Web (`web/src/components/`)
- **`CivConstellation.tsx` e `ObsidianGraphView.tsx`**:
  - Nós de agentes (`AgentProfile`) e nós de eventos (`agent_event`) passam a ser renderizados com cores e ícones distintos dentro da constelação do grafo de conhecimento.
  - Relações de causalidade (`REFERENCES`, `DEPENDS_ON`) são exploráveis visualmente em tempo real pelo operador.

---

## 5. Consequências e Validação E2E

### 5.1 Benefícios Arquiteturais
- **Rastreabilidade Completa**: Cada decisão, invocação de ferramenta ou falha de agente fica perfeitamente documentada e indexada no grafo.
- **Resiliência Adaptativa**: Agentes com histórico de falhas sofrem penalização imediata no roteamento, evitando ciclos viciosos de erros.
- **Consciência de Estado**: O operador e os modelos supervisores possuem um indicador claro (`PowerlineContextHUD`) da integridade cognitiva do sistema em qualquer instante.

### 5.2 Validação E2E
A suíte `tests/platform/test_agent_mesh_e2e.py` valida 100% dos requisitos de ponta a ponta:
- Emissão e escuta de eventos com persistência em banco isolado (`test_agent_event_bus_emission_and_listening`).
- Registro e consulta de capacidades e perfis de agentes (`test_agent_capability_registry`).
- Roteamento adaptativo com penalização e substituição de agentes degradados (`test_agent_router_confidence_weighted_routing`).
- Execução de workflow com 4 passos encadeados e validação topológica (`test_workflow_dag_execution_with_dependencies`).
- Rejeição de ciclos em DAGs (`test_workflow_dag_cycle_detection`).
- Tratamento de falha em etapa de workflow com penalização correspondente (`test_workflow_dag_step_failure_and_event`).

---

**Decisão:** APROVADA e INTEGRADA ao núcleo do HAOS.
