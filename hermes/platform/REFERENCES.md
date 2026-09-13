# Catalogo de Referencias, Frameworks e Protocolos - HAOS

O **HAOS (Hermes Agent Operating System v1.1)** mantem o **Hermes AIAgent como Kernel principal**. Todos os demais projetos listados abaixo atuam como fontes de padrões arquiteturais, adaptadores ou inspiração limpa (Clean-Room Integration).

---

## 1. Mapeamento de Projetos e Frameworks

| Projeto / Framework | Papel no HAOS | Link Oficial / Repositório Local |
| :--- | :--- | :--- |
| **Hermes Agent (Nous Research)** | **Kernel Principal**, AIAgent, tools, skills, memory, Kanban durável, delegation, Bot Mode, MCP, providers. | [GitHub](https://github.com/NousResearch/hermes-agent) · `TEMP/references/hermes-agent` |
| **DeepSeek Harness (DSH)** | Extension Fabric, arquitetura de plugins (Cordis), event/trace/replay e barramento append-only. | [GitHub](https://github.com/deepseek-ai/deepseek-harness) · `TEMP/references/deepseek-harness` |
| **OpenClaw** | OAuth profiles, device flows, canais, token vault, secret broker e políticas de aprovação. | [GitHub](https://github.com/openclaw/openclaw) · `TEMP/references/openclaw` |
| **LangChain** | Referência de context engineering, schemas tipados e abstrações de integração. | [GitHub](https://github.com/langchain-ai/langchain) · `TEMP/references/langchain` |
| **LangGraph** | Adapter opcional para workflows determinísticos, checkpoints e stateful agents. | [GitHub](https://github.com/langchain-ai/langgraph) · `TEMP/references/langgraph` |
| **Agno** | Equipes tipadas, contratos de entrada/saída, guardrail hooks, observabilidade e evals. | [GitHub](https://github.com/agno-agi/agno) · `TEMP/references/agno` |
| **Oh My OpenAgent (OmO)** | Posturas/especialistas, Team Mode, hooks de ciclo de vida e ergonomia de código. | [GitHub](https://github.com/code-yeongyu/oh-my-openagent) · `TEMP/references/oh-my-openagent` |
| **Orca** | Workers paralelos, Git worktrees e isolamento simples de tarefas de código. | [GitHub](https://github.com/araa47/orca) · `TEMP/references/orca` |
| **CowAgent** | Roteamento multi-modelo, delegação de capacidade multimodal (visão/áudio) sem alterar cérebro principal. | [GitHub](https://github.com/zhayujie/CowAgent) · `TEMP/references/CowAgent` |
| **Ouroboros** | Evolution Engine em *Shadow Mode*, auto-aperfeiçoamento baseado em métricas de execução. | [GitHub](https://github.com/razzant/ouroboros) / [Q00](https://github.com/Q00/ouroboros) · `TEMP/references/ouroboros` |
| **AionUI** | UX do Control Plane, visualização de equipes, mailbox, taskboard e approvals por agente. | [GitHub](https://github.com/iOfficeAI/AionUi) · `TEMP/references/AionUi` |
| **DeerFlow (ByteDance)** | Subagentes de pesquisa profunda (*Deep Research*), decomposição de perguntas e artefatos de evidência. | [GitHub](https://github.com/bytedance/deer-flow) · `TEMP/references/deer-flow` |
| **GraphRAG (Microsoft)** | Projeção de memória relacional, extração de entidades/comunidades e consultas global/local. | [GitHub](https://github.com/microsoft/graphrag) · `TEMP/references/graphrag` |
| **Obsidian** | Memória canônica/humana, especificações de projeto e Decision Records (ADRs) via CLI/Vault. | [Site Oficial](https://obsidian.md/) · [Obsidian CLI](https://obsidian.md/help/cli) |
| **Gas Town** | Referência de orquestração de times (Mayor, Deacon, Witness, Refinery, Polecat) e modelos por função. | [GitHub](https://github.com/gastownhall/gastown) · `TEMP/references/gastown` |
| **Beads** | Task/work ledger persistente e gestão de estado de trabalho operacional. | [GitHub](https://github.com/gastownhall/beads) · `TEMP/references/beads` |
| **Gas City** | Generalização do Gas Town em SDK de orquestração assíncrona por eventos. | [GitHub](https://github.com/gastownhall/gascity) · `TEMP/references/gascity` |
| **Kilo Code** | Execution backend para coding em *Git worktrees* paralelos com suporte LSP nativo. | [GitHub](https://github.com/Kilo-Org/kilocode) · `TEMP/references/kilocode` |
| **Hermes Web Dashboard** | UI web oficial do Hermes conectada diretamente ao runtime. | [Docs Oficiais](https://hermes-agent.nousresearch.com/docs/user-guide/features/web-dashboard) |
| **Hermes Studio** | UI avançada para visualização de agentes, grafo de memória, approvals e cron. | [GitHub](https://github.com/JPeetz/Hermes-Studio) · `TEMP/references/Hermes-Studio` |

---

## 2. Protocolos e Padrões Integrados

| Protocolo | Papel no HAOS | Link / Repositório Local |
| :--- | :--- | :--- |
| **MCP (Model Context Protocol)** | Agent ↔ Ferramentas e serviços MCP externos. Broker MCP, Packs e Scope Filter. | [GitHub](https://github.com/modelcontextprotocol/modelcontextprotocol) · `TEMP/references/modelcontextprotocol` |
| **ACP (Agent Client Protocol)** | Editor/IDE ↔ HAOS Platform. Integração nativa com VS Code, Zed e JetBrains. | [GitHub](https://github.com/agentclientprotocol/agent-client-protocol) · `TEMP/references/agent-client-protocol` |
| **A2A (Agent2Agent Protocol)** | Interoperabilidade e troca de mensagens estruturadas entre diferentes sistemas de agentes. | [GitHub](https://github.com/a2aproject/A2A) · `TEMP/references/A2A` |
| **ANP (Agent Network Protocol)** | Agentic Web 1.1: Identidade DID (`did:wba`), descoberta e federação de agentes na internet. | [GitHub](https://github.com/agent-network-protocol/AgentNetworkProtocol) · `TEMP/references/AgentNetworkProtocol` |
| **LSP (Language Server Protocol 3.18)** | Code Intelligence: Símbolos de código, referências, definições, diagnósticos e hierarquias por worktree. | [GitHub](https://github.com/microsoft/language-server-protocol) · `TEMP/references/language-server-protocol` |

---

## 3. Serviços e Provedores de Inferência

| Provedor / Serviço | Função no HAOS | Link |
| :--- | :--- | :--- |
| **A6API** | Provedor/relay inicial de inferência multi-modelo. Integrado no `ExactModelRouter` com failover de rotas sem trocar o modelo. | [Site Oficial A6API](https://a6api.com/) |

---

## 4. Ordem de Leitura Recomendada para Implementação Incremental

Para evoluir ou estender o HAOS, a sequência de leitura e estudo recomendada é:

1. **Kernel Base & Injeção:** [Hermes Agent](https://github.com/NousResearch/hermes-agent)
2. **Plugins & Barramento:** [DeepSeek Harness](https://github.com/deepseek-ai/deepseek-harness)
3. **Orquestração de Times & Work Ledger:** [Gas Town](https://github.com/gastownhall/gastown) ➔ [Gas City](https://github.com/gastownhall/gascity) ➔ [Beads](https://github.com/gastownhall/beads)
4. **Execução de Código & Worktrees:** [Kilo Code](https://github.com/Kilo-Org/kilocode)
5. **Posturas & Ergonomia:** [Oh My OpenAgent](https://github.com/code-yeongyu/oh-my-openagent) ➔ [OpenClaw](https://github.com/openclaw/openclaw)
6. **Guardrails & Teams:** [Agno](https://github.com/agno-agi/agno)
7. **Control Plane & UI:** [AionUI](https://github.com/iOfficeAI/AionUi) ➔ [Hermes Studio](https://github.com/JPeetz/Hermes-Studio)
8. **Evolução & Auto-aperfeiçoamento:** [Ouroboros](https://github.com/razzant/ouroboros)
9. **Pesquisa Autônoma:** [DeerFlow](https://github.com/bytedance/deer-flow)
10. **Roteamento Multimodal:** [CowAgent](https://github.com/zhayujie/CowAgent)
11. **Memória Relacional & Canônica:** [GraphRAG](https://github.com/microsoft/graphrag) ➔ [Obsidian](https://obsidian.md/)
12. **Protocolos:** [MCP](https://github.com/modelcontextprotocol/modelcontextprotocol) ➔ [LSP](https://github.com/microsoft/language-server-protocol) ➔ [ACP](https://github.com/agentclientprotocol/agent-client-protocol) ➔ [A2A](https://github.com/a2aproject/A2A) ➔ [ANP](https://github.com/agent-network-protocol/AgentNetworkProtocol)
