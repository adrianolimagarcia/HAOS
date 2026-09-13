---
name: haos-lane-execution
description: Disciplina e contrato de execução em workspace HAOS.
version: 1.0.0
author: Adriano Lima, Hermes Agent
license: MIT
platforms: [linux, macos]
category: autonomous-ai-agents
tags: [haos, lane, worker, contract, workspace]
---

# HAOS Lane Execution Skill

Contrato canônico e disciplina de execução de tarefas em lanes e workspaces isolados do HAOS.

## When to Use

Use quando uma tarefa for executada em uma lane do HAOS (kilo ou hermes) dentro de um workspace dedicado (`.haos/spec.json`).

## Prerequisites

- Workspace canônico contendo o diretório `.haos/` montado pelo dispatcher.
- Ferramentas nativas do Hermes ativas (`read_file`, `write_file`, `patch`, `terminal`).

## How to Run

O worker lê o contrato da tarefa em `.haos/spec.json` via `read_file` e produz suas evidências e patch no workspace.

## Quick Reference

| Arquivo | Propósito |
|---|---|
| `.haos/spec.json` | Especificação completa da tarefa (`TaskSpec`) |
| `.haos/result.json` | Sumário de saída, artefatos e métricas geradas |

## Procedure

1. Inspecione a especificação em `.haos/spec.json` com `read_file`.
2. Implemente os artefatos de código e testes necessários no workspace.
3. Grave o resultado em `.haos/result.json` estruturando status e evidências.
4. Conclua com o sumário claro para aprovação no dashboard.

## Pitfalls

- Nunca modifique arquivos fora do workspace isolado.
- Nunca ignore erros de testes unitários ou critérios de aceite.

## Verification

Verifique se os artefatos foram gerados no workspace e se `.haos/result.json` reflete o sucesso da execução.
