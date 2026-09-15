# GOV-017: Scheduled Routine Contracts & Boundary Approvals

> **Canonical ID:** `GOV-017` (renamed from `ADR-017`). Registry: [`INDEX.md`](INDEX.md).

- **Status:** Approved
- **Date:** 2026-09-09
- **Governed By:** GOV-001 (Hermes Kernel), GOV-002 (Task Lifecycle), GOV-011 (SQLite-first), GOV-016 (Anti-Drift Topologies)

## Context
Rotinas agendadas recorrentes (Cron jobs) que executam prompts abertos sem fronteiras delimitadas correm o risco de:
1. **Falhas Silenciosas ou Degradação:** Execução de tarefas vazias consumindo tokens sem novos dados.
2. **Efeitos Colaterais Críticos Descontrolados:** Modificação de produção ou disparo de eventos externos sem supervisão humana (falta de portões de aprovação).
3. **Falta de Auditabilidade:** Jobs definidos por strings temporárias e opacas na linha de comando sem especificação legível.

## Decision
O HAOS formaliza a governança de rotinas agendadas adotando o padrão **RoutineSpec as Markdown Contracts** e **Boundary Approvals**:

1. **Estrutura de Contrato de Rotina (`RoutineSpec`):**
   - Toda rotina persistente é tratada como um contrato auditável contendo:
     - Gatilho temporal determinístico (expressão cron ou intervalo).
     - Script local de pré-execução (`script`) para coleta de métricas/diffs com zero custo de LLM antes da inferência.
     - Escopo mínimo de skills e toolsets (`skills: [...]`) para garantir isolamento e economia de contexto.
     - Destino explícito de entrega (`deliver`) desacoplado da sessão interativa do usuário.

2. **Fronteira de Segurança e Takeover Humano (Boundary Approval):**
   - Rotinas operam em modo **Read-Heavy / Report-First**.
   - Qualquer mutação de alto impacto (alteração de infraestrutura, exclusão de dados, push para branch canônica) deve acionar formulário de confirmação estruturado via `request_operator_form` ou pausar a rotina aguardando aprovação explícita do operador no Control Plane.

3. **Integração Operacional com RAGFlow e Health Checks:**
   - Rotinas de reindexação e integridade do repositório operam em background gravando métricas diretamente no SQLite operacional (`kanban.db` / `ragflow.db`), emitindo alertas apenas mediante anomalias comprovadas.

## Consequences
- **Positivas:** Previne loops zumbis de cron, garante previsibilidade de custos, protege o repositório contra mutações não autorizadas em background e mantém trilha de auditoria completa.
- **Negativas:** Operações críticas agendadas passam a exigir confirmação do operador, não sendo 100% autônomas sem intervenção inicial.
