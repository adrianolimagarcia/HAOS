---
name: haos-orchestrator
description: Orquestra workers e revisores com specs verificadas.
version: 1.0.0
author: Adriano Lima, Hermes Agent
license: MIT
platforms: [linux, macos]
category: autonomous-ai-agents
tags: [haos, orchestrator, delegation, worker, reviewer, task-spec]
---

# HAOS Orchestrator Skill

Contrato para orquestrar uma equipe de workers especializados (código, frontend, revisão) em lanes HAOS, com specs de tarefa pequenas e verificadas, monitoramento ativo e handoff contínuo.

## When to Use

Use quando for orquestrar múltiplos workers em um projeto real (não um único `delegate_task` pontual): dividir uma fase em tarefas, despachar workers em background, monitorar progresso, verificar resultados de forma independente e enviar diffs a um revisor.

## Prerequisites

- Workspace HAOS com acesso via `read_file`, `patch`, `write_file`, `terminal` e `delegate_task`.
- Validador de spec em `skills/autonomous-ai-agents/haos-orchestrator/scripts/task_spec_check.py`.
- Templates em `skills/autonomous-ai-agents/haos-orchestrator/templates/` (`task_spec.md`, `handoff.md`).
- Comandos de verificação com paridade de CI definidos para o repositório (`scripts/run_tests.sh`, lint, typecheck).

## How to Run

Valide uma spec antes de despachar, via `terminal`:

```bash
python3 skills/autonomous-ai-agents/haos-orchestrator/scripts/task_spec_check.py --spec <path/to/spec.md>
```

Valide o handoff contínuo:

```bash
python3 skills/autonomous-ai-agents/haos-orchestrator/scripts/task_spec_check.py --handoff HANDOFF.md
```

## Quick Reference

| Artefato | Propósito | Regra |
|---|---|---|
| Task spec | Contrato do worker (papel, restrições, arquivos, testes, verificação) | ≤ 4 KB; seções obrigatórias; sem "weaken asserts" |
| HANDOFF.md | Estado contínuo do orquestrador (substituível a qualquer momento) | Atualizado durante o trabalho, nunca só no fim |
| Veredito do revisor | PASS ou REWORK + arquivo:linha + problema concreto | Escopo estreito (segurança/lógica), sem sugestões de estilo |
| Verificação | Orquestrador re-roda testes/lint/typecheck do CI | "tests passed" do worker NÃO é verificação |

## Procedure

1. **Escreva a spec da tarefa** (≤ 4 KB) a partir do template `templates/task_spec.md`. Inclua: papel único, restrições de pastas, arquivos a inspecionar primeiro, contratos de funções/classes, testes com valores concretos e os comandos exatos de verificação. Nunca restrinja ferramentas por prosa na spec — o escopo de ferramentas vem do papel/postura no harness.
2. **Valide a spec** com `task_spec_check.py --spec`. Se rejeitar, divida a tarefa ou corrija antes de despachar (specs oversized são o preditor nº1 de falha).
3. **Despache o worker** com `delegate_task` (ou processo `hermes -z` para worker isolado) em background, e registre a spec versionada.
4. **Monitore ativamente**: verifique se o processo segue vivo e progride; se parecer travado, interrompa antes do timeout, inspecione o que aconteceu e decida entre corrigir ou dividir a spec.
5. **Verifique de forma independente** ao terminar: rode os MESMOS comandos do CI (`scripts/run_tests.sh`, lint, typecheck) você mesmo — não aceite "testes passaram" do worker como verificação.
6. **Envie o diff ao revisor** com escopo estreito (PASS/REWORK + arquivo:linha). REWORK pequeno corrija direto; problema maior vira nova tarefa de worker.
7. **Atualize o HANDOFF.md continuamente** (fase, completos, workers ativos, decisões, perguntas abertas, próxima ação) em `templates/handoff.md`.
8. **Conclua a fase** apenas com CI verde; registre fase, tarefa e modelos envolvidos no commit.

## Pitfalls

- Nunca despache spec acima de ~4 KB — divida primeiro (tasks 6–7 KB estouram timeouts; a mesma divisão termina em ~15 min).
- Nunca aceite "os testes passaram" do worker como verificação — re-rode você mesmo; CI remoto é o portão final.
- Nunca restrinja ferramentas por instrução em prosa na spec (injetável por prompt). Escopo de ferramentas por papel no harness.
- Não peça ao worker para enfraquecer asserts para ficar verde — a regra é: relate a falha real.
- Revisor com escopo amplo gera ruído (estilo/refactor); mantenha estreito: PASS/REWORK + linha.
- Não espere o handoff ser necessário para atualizar o HANDOFF.md.
- Não sobreteste glue/infraestrutura: testes profundos no domínio, testes pontuais na cola.

## Verification

Rode o validador sobre a spec de uma tarefa real e sobre o `HANDOFF.md`:

```bash
python3 skills/autonomous-ai-agents/haos-orchestrator/scripts/task_spec_check.py --spec <spec.md> --handoff HANDOFF.md
```

Confirme que ambas as validações passam (saída "ready to dispatch").
