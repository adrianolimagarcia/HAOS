---
name: haos-tws-hwa-ops
description: Diagnóstico read-only de esteiras TWS/HWA.
version: 1.0.0
author: Adriano Lima, Hermes Agent
license: MIT
platforms: [linux]
category: operations
tags: [haos, tws, hwa, workload-automation, conman, operations]
---

# HAOS TWS/HWA Operations Skill

Diagnóstico governado, inspeção de esteiras e runbook operacional para IBM/HCL Workload Automation (TWS/HWA).

## When to Use

Use para diagnosticar o status de esteiras de automação (jobstreams), inspecionar logs de jobs (`joblog`), avaliar o estado de instâncias MDM e agentes via `conman`, validar pré-requisitos antes de intervenções e prevenir execuções destrutivas como geração manual inadvertida de plano (`MakePlan`).

## Prerequisites

- Ambiente TWS/HWA acessível (local ou containerizado, ex.: containers `tws-hwa` / `tws-bmdm`).
- Usuário operacional configurado (ex.: `wauser`) com ambiente carregado (`/opt/hwa/TWS/bin/conman`).
- Conexão e portas de comunicação ativas (31116 engineServer, 31111/31113 netman, 31114 agent).

## How to Run

Execute inspeções exclusivamente em modo read-only via linha de comando do conman ou utilitários de status:

```bash
docker exec tws-hwa su - wauser -c "conman sc"
docker exec tws-hwa su - wauser -c "conman 'sj @#@;info'"
```

## Quick Reference

| Verificação | Comando | Ação |
|---|---|---|
| Status das CPUs | `conman sc` | Exibe estado das estações (LINKED, etc.) |
| Status dos Jobs | `conman sj @#@` | Lista job streams e status |
| Prompts pendentes | `conman 'sj @#@;deps'` ou prompts | Localiza bloqueios de confirmação |
| Status do plano | `planman showinfo` | Valida horizonte de planejamento e Run ID |
| Processos TWS | `ps -ef \| grep -E "netman\|mailman\|batchman"` | Checagem de daemons do engine |

## Procedure

1. **Diagnóstico Read-Only**:
   - Inspecione as CPUs do domínio com `conman sc` para confirmar estado `LINKED` (bandeira L).
   - Inspecione os jobs em execução ou retidos (`HOLD`, `STUCK`, `ABEND`) usando `conman sj`.
   - Consulte o joblog específico de jobs com erro sem alterar parâmetros de execução.
   - Verifique `planman showinfo` para confirmar sincronismo de plano entre Master e Backup.
   - NUNCA execute comandos destrutivos ou de mutação no plano durante a triagem.

2. **Pré-condições**:
   - **PREVENÇÃO ABSOLUTA**: Jamais execute `MakePlan` manual sem pré-aprovação de arquitetura, pois isso corrompe a linha do tempo do Symphony e reinicia o plano diário destrutivamente.
   - Validar se a data e fuso horário do host e dos containers estão coerentes.
   - Certificar-se de que dependências de jobs (arquivos, recursos, prompts) foram verificadas.

3. **Snapshot**:
   - Salvar o estado completo do Symphony e do conman antes de qualquer ação corretiva:
     `conman "sj @#@;info" > /tmp/tws_plan_snapshot_$(date +%s).txt`
   - Registrar saída de `conman sc` e status de processos do sistema operacional.

4. **Autorização**:
   - Qualquer liberação manual de prompt (`reply`), alteração de status de job (`release`, `cancel`, `rerun`) ou alteração de fence/limit EXIGE autorização formal do operador.
   - O plano de intervenção deve especificar exatamente o Job Stream, Job ID e justificativa.

5. **Rollback**:
   - Em intervenções em jobs, documentar o comando reverso (ex.: se um job foi colocado em `HOLD`, como retornar ao estado de execução original ou vice-versa).
   - Em caso de falha de conexão de agente, restaurar links via `conman "link <cpu>"`.

## Pitfalls

- **Executar `MakePlan` fora do ciclo agendado**: apaga o plano operacional corrente, reinicia contadores e quebra esteiras noturnas.
- **Confundir estado `STUCK` com erro fatal de job**: no TWS, um job stream fica em `STUCK` tipicamente aguardando resposta de operador a um `PROMPT` pendente.
- **Divergência de timezone entre MDM e BMDM**: se um container opera em UTC e outro em UTC-3, janelas de execução e dependências cruzadas sofrem atrasos artificiais.

## Verification

- Confirmar que todas as CPUs críticas retornam com flags `LTI JW M A` no conman.
- Assegurar que nenhum job essencial permaneceu em estado `ABEND` ou `STUCK` sem acompanhamento.
- Validar integridade dos joblogs e preservação dos arquivos de auditoria.
