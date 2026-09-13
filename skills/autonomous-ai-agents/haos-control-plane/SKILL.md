---
name: haos-control-plane
description: Operação do control plane HAOS e ciclo kanban de tarefas.
version: 1.0.0
author: Adriano Lima, Hermes Agent
license: MIT
platforms: [linux, macos]
category: autonomous-ai-agents
tags: [haos, kanban, orchestration, dispatcher, control-plane]
---

# HAOS Control Plane Skill

Guia de operação e comandos do Control Plane e Kanban do HAOS v1.1.

## When to Use

Use para monitorar status do board, orquestrar subagentes, gerenciar cards e disparar o dispatcher.

## Prerequisites

- Base de dados Kanban ativa (`HERMES_KANBAN_DB` ou canônica).
- Ferramentas de Kanban (`kanban_list_tasks`, `kanban_create_task`, `kanban_update_task`).

## How to Run

Execute no terminal Hermes com `/haos status` ou `/haos dispatch`, ou via ferramentas de modelo.

## Quick Reference

| Comando | Ação |
|---|---|
| `/haos status` | Exibe total de tarefas por status e cursor de eventos |
| `/haos dispatch` | Processa cards com status `READY` imediatamente |
| `/haos constructor` | Inicia entrevista para estruturação de projeto |

## Procedure

1. Consulte o status das tarefas e identifique pendências.
2. Crie cards no Kanban com especificação detalhada (`TaskSpec`) e status `READY`.
3. Dispare o processamento ou delegue aos subagentes apropriados.
4. Revise os cards concluídos em `DONE` e registre aprovações no Dashboard.

## Pitfalls

- Não deixe cards concluídos sem invocar `complete_task`.
- Evite despachar tarefas sem critérios de aceite claros.

## Verification

Verifique o progresso visualmente no Web Dashboard em `/haos` ou via `/haos status`.
