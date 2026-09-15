# GOV-016: Anti-Drift Multi-Agent Topologies & Cognitive Governance

> **Canonical ID:** `GOV-016` (renamed from `ADR-016`). Registry: [`INDEX.md`](INDEX.md).

- **Status:** Approved
- **Date:** 2026-09-09
- **Governed By:** ADR-001 (HAOS Multi-Agent SOTA), ADR-003 (Team Runtime), GOV-010 (Worker Lanes)

## Context
Em sistemas multi-agente autônomos, delegações ingênuas e comunicações abertas entre pares (mesh não regulado) geram dois problemas críticos:
1. **Goal Drift (Desvio de Meta):** Subagentes se perdem em diálogos recursivos, desviam dos critérios de aceite e gastam orçamento de tokens sem convergir.
2. **Alucinação Coletiva:** Em tarefas abertas ou de pesquisa, suposições incorretas de um agente são validadas por outro na ausência de verificação empírica.

## Decision
O HAOS adota **Governança de Topologias Anti-Drift** parametrizada pela natureza da tarefa:

1. **Modo Engenharia de Software (Topologia Hierárquica Estrita - Tree):**
   - Relação estrita: Town Mayor / Orchestrator $\to$ Workers Especialistas.
   - Comunicação P2P entre workers é desativada por padrão.
   - Toda tarefa exige contrato fechado (`TaskSpec` $\le$ 4 KB) em workspace dedicado (`.haos/spec.json`).
   - Conclusão condicionada exclusivamente a testes reais no terminal (`returncode == 0`) e loop de *Reflexion* verbal em caso de falha.

2. **Modo Pesquisa & Não-Determinismo (Topologia Fan-Out / Fan-In com Âncora Epistêmica):**
   - **Âncora Epistêmica:** O orquestrador define a pergunta fechada e limites negativos de escopo (o que NÃO explorar).
   - **Fan-Out Ortogonal:** Despacho de no máximo 3 a 4 subagentes paralelos explorando ângulos complementares e isolados:
     - Agente A: Documentação e fontes primárias.
     - Agente B: Spike e benchmark empírico local via terminal.
     - Agente C: Análise de riscos e casos de borda.
   - **Fan-In / Juiz Crítico:** O orquestrador atua como sintetizador com Chain-of-Verification (`cove-verification`), exigindo evidências verificáveis antes de consolidar.
   - **Cristalização:** O resultado de pesquisas não-determinísticas é obrigatoriamente gravado em ADR/Obsidian ou indexado no RAGFlow, tornando-se contrato determinístico no Kanban.

3. **Teto Anti-Drift de Agentes (Ceiling):**
   - Limite padrão estrito de concorrência (`delegation.max_concurrent_children` $\le$ 4) para evitar degradação de coordenação.

## Consequences
- **Positivas:** Elimina loops de discussão infinita, reduz custos de inferência, estabiliza a previsibilidade dos workers e previne alucinações coletivas.
- **Negativas:** Requer especificação formal antecipada (`TaskSpec`/`ResearchSpec`) antes do despacho de agentes.
