# GOV-018: Ultrawork (ulw) Outcome-First & Evidence-Driven Execution

> **Canonical ID:** `GOV-018` (renamed from `ADR-018`). Registry: [`INDEX.md`](INDEX.md).

- **Status:** Approved
- **Date:** 2026-09-09
- **Governed By:** GOV-001 (Hermes Kernel), GOV-002 (Task Lifecycle), GOV-010 (Worker Lanes), GOV-016 (Anti-Drift Topologies)

## Context
Em assistentes e agentes autônomos tradicionais, é comum ocorrer a "falsa conclusão": o modelo gera um trecho de código incompleto, assume que funciona e declara o trabalho como concluído sem evidências operacionais de teste ou compilação.

## Decision
O HAOS formaliza o **Modo Ultrawork (`ulw`)**, inspirado nas disciplinas de engenharia de alta fidelidade (OmO / Hephaestus):

1. **Gatilho de Ativação:**
   - Palavras-chave no prompt (`ultrawork`, `ulw`, `/ultrawork`) chaveiam o agente para o modo de máxima rigidez e orçamento de raciocínio.

2. **Fluxo em 4 Fases Obrigatórias:**
   - **Fase 1: Reconhecimento (Explorer & Librarian):** Mapeamento prévio de AST via Codebase Wiki / Graphify e leitura de ADRs no Obsidian e contratos no RAGFlow. Nenhuma linha de código de produto é escrita nesta fase.
   - **Fase 2: Plano Estratégico (Planner / Prometheus):** Decomposição analítica em fatias verticais testáveis (`cot-decomposition`) registradas no Kanban em `READY`.
   - **Fase 3: Execução TDD (Executor / Hephaestus):** Implementação em Worker Lane isolada (`.haos/spec.json`) com loop de Reflexion (auto-correção verbal de falhas de teste sem enfraquecer asserts).
   - **Fase 4: Portão Oracle de Verificação (Oracle Gate):** A tarefa move-se para `REVIEW`. O orquestrador re-executa a suíte de testes de forma neutra e independente via terminal. A transição para `DONE` exige prova comprovada com `returncode == 0`.

3. **Invariante de Conclusão:**
   - Declarações verbais de sucesso sem evidências anexadas são terminantemente rejeitadas pelo sistema de governança.

## Consequences
- **Positivas:** Elimina código incompleto com `# TODO`, garante cobertura de testes real e documenta todas as entregas com evidências reproduzíveis.
- **Negativas:** Exige maior tempo de execução por tarefa devido à fase preliminar de reconhecimento e ao portão independente de QA.
