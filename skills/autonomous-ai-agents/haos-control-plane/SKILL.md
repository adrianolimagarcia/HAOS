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
- Ferramentas de Kanban (`kanban_list`, `kanban_create`).

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

- **Job `no_agent` com `script` FORA de `~/.haos/scripts` nunca executa**: o agendador valida o
  caminho e falha com `Blocked: script path resolves outside the scripts directory`. O campo `script`
  também **não aceita argumentos** ("script.py arg" vira caminho literal inválido). Sintoma clássico:
  job `enabled: true`, `state: scheduled`, mas `last_status: error` em **todas** as execuções — ou seja,
  parece agendado e nunca rodou. Sempre ponha o alvo real em `~/.haos/scripts/` (ou um wrapper lá que
  invoque o script do workspace por `subprocess`) e verifique com
  `sqlite3 ~/.haos/cron/executions.db "SELECT job_id,status,error FROM executions ORDER BY rowid DESC LIMIT 5"`
  — `state: scheduled` no `jobs.json` **não é** prova de execução; só `status=completed` no banco é.
- **Wrapper de cron deve filtrar stdout**: com `no_agent`, o stdout é entregue verbatim. Script que
  imprime status a cada ciclo vira spam. Faça o wrapper imprimir **somente** linhas com marcadores de
  evento real (`COMPRA`/`VENDA`/`ERRO`/`ALERTA`/`Traceback`) e ficar silencioso quando nada aconteceu.
- **O REGISTRY é UM arquivo, não dois**: `$HOME/.haos/council/REGISTRY.md` é **symlink** para
  `$HOME/.hermes/council/REGISTRY.md`. "Atualizar os dois homes" com dois appends **duplica a
  entrada no mesmo arquivo**. Anexe **uma única vez** pelo caminho canônico
  (`$HOME/.hermes/council/REGISTRY.md`).
- **Peer NÃO escreve no REGISTRY — CLÁUSULA 7 da Regra de Ouro** (ratificada pelo dono 2026-09-12;
  ata `council-20260912-0215-registry-boundary`): acesso root por SSH entre pares da malha é
  permitido para operação, mas **escrita na fonte de verdade de outro nó é violação**. Mudanças são
  pedidas por **A2A** e aplicadas pelo **nó dono após ratificação**, com a autoria registrada. Vigia:
  `~/.haos/scripts/registry_guard.py` (cron `registry-guard`, 10 min, `deliver: local` — não pinga
  o chat). **Política (revisada 2026-09-12)**: escrita **local** é re-baselinada automaticamente e
  fica silenciosa (trilha em `~/.haos/council/registry_changes.log`); **só** escrita com login de
  peer **temporalmente adjacente** (`login <= mtime <= login + 900s`) gera alerta, e o alerta é
  **dedupeado por hash** (1× por conteúdo). **Login de peer ≠ autoria** — um login 20 min antes da
  escrita é coincidência; exigir adjacência foi o que matou o falso positivo. Não é preciso `--ack`
  a cada escrita (`--ack` continua existindo e limpa o marcador de alerta).
- **Sempre termine o texto anexado com newline**: sem isso, o próximo append cola o cabeçalho da
  entrada nova no meio da última linha da anterior (cabeçalhos grudados, entradas ilegíveis).
- **Editable install do venv defasa no checkout**: `/usr/local/lib/haos-agent/venv` é editable e o
  `MAPPING` do finder é gerado em **build time**. Um checkout que adiciona módulo raiz (`.py` na raiz
  do tree) deixa o módulo novo **invisível** — sintoma típico `ModuleNotFoundError: No module named
  'hermes_state_ids'` em quem importa o runtime direto. Serviços que entram por `hermes_cli.main` ou
  `hermes_bootstrap` não caem (ambos injetam a raiz do tree no `sys.path`), o que **mascara** o defeito
  até um script manutenção/oneshot quebrar. Diagnóstico: `<venv>/bin/python -c "import hermes_state_ids"`
  sem `PYTHONPATH` — falhou, o editable está defasado. Correção (offline, com backup antes):
  `uv pip install --python <venv>/bin/python --no-deps --no-build-isolation -e /usr/local/lib/haos-agent`.
- **ExecStart de unit systemd nunca deve apontar para disco removível**: unit oneshot com ExecStart em
  `/run/media/<user>/<uuid>/...` falha sempre que o disco está desmontado, e o `journalctl -u` de um
  oneshot **não mostra nada** — parece falha de código. Deploye o script em `<home>/scripts/` (mesma
  regra do job `no_agent`) e reponte o unit; remova o `RequiresMountsFor` do disco se o script não lê
  mais nada dele. Confirme com `systemctl show <unit> -p Result -p ExecMainStatus` (`Result=success`,
  `ExecMainStatus=0`) depois de `systemctl start` — não basta `systemctl status` dizer `inactive (dead)`.
- Não deixe cards concluídos sem invocar `kanban_complete`.
- Evite despachar tarefas sem critérios de aceite claros.

## Verification

Após anexar qualquer entrada no REGISTRY, valide no mesmo turno:

```bash
grep -c "<marcador único da entrada>" ~/.hermes/council/REGISTRY.md   # deve ser 1
grep -c -- "-03 ·.*- 2026-" ~/.hermes/council/REGISTRY.md            # deve ser 0 (nada colado)
```

Se aparecer duplicata ou linha colada, use o reparador (backup + dedupe de blocos consecutivos)
em `~/workspace/haos-evals/repair_registry.py`. Verifique também que o symlink segue no lugar
(`ls -la ~/.haos/council/REGISTRY.md`).

Verifique o progresso visualmente no Web Dashboard em `/haos` ou via `/haos status`.
