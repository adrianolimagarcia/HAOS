---
name: haos-mcp-ops
description: Diagnóstico e integridade de servidores MCP.
version: 1.0.0
author: Adriano Lima, Hermes Agent
license: MIT
platforms: [linux, macos]
category: operations
tags: [haos, mcp, protocol, tools, operations, diagnostics]
---

# HAOS MCP Operations Skill

Diagnóstico governado e runbook de integridade para servidores e ferramentas Model Context Protocol (MCP) no HAOS.

## When to Use

Use para diagnosticar o status de servidores MCP conectados, medir latência de inicialização e chamadas de ferramenta, validar integridade dos schemas retornados e auditar desconexões de transporte (stdio/SSE).

## Prerequisites

- Arquivo de configuração de MCPs ativo (`~/.haos/config.yaml` ou profile equivalente).
- Ambiente Python com client MCP ou ferramentas CLI de inspeção.
- Permissão de leitura para inspecionar logs e processos dos servidores MCP locais.

## How to Run

Execute diagnósticos de servidores MCP de maneira não destrutiva, validando listagem de tools e tempos de resposta:

```bash
hermes mcp list
python -m hermes_cli.main mcp status
```

## Quick Reference

| Verificação | Comando / Método | Ação |
|---|---|---|
| Lista de servidores | `grep -A 10 "mcp_servers:" ~/.haos/config.yaml` | Checagem de configuração |
| Processos MCP ativos | `pgrep -a -f "mcp|ouroboros|haos-edge"` | Localizar processos |
| Integridade de schemas | `python -c "import json; ..."` | Validar JSON Schema das tools |
| Latência de transporte | Medição de round-trip RPC via tool_search | Avaliar degradação |

## Procedure

1. **Diagnóstico Read-Only**:
   - Inspecione a lista de servidores MCP registrados na configuração sem alterar valores.
   - Verifique se os processos filhos (para transportes stdio) estão vivos e saudáveis.
   - Teste a descoberta de ferramentas (`list_tools`) medindo o tempo de resposta em milissegundos.
   - Valide se os schemas retornados obedecem à especificação JSON Schema Draft-07 sem campos malformados.

2. **Pré-condições**:
   - Confirmar que nenhuma chamada de ferramenta crítica está em execução no momento da checagem.
   - Identificar exatamente qual servidor MCP apresenta anomalia (ex.: ouroboros, haos-edge, perplexity).

3. **Snapshot**:
   - Gravar o snapshot do catálogo de ferramentas atual e configuração em `/tmp/mcp_snapshot_$(date +%s).json`.
   - Registrar métricas de latência e saídas de erro do stderr do processo.

4. **Autorização**:
   - Parar, matar subprocessos MCP ou modificar parâmetros de conexão no `config.yaml` EXIGE autorização formal do operador.
   - Relatar ao operador o ID do servidor, tempo de timeout observado e impacto de reinício nas sessões ativas.

5. **Rollback**:
   - Se a reconfiguração ou reinício do servidor MCP falhar, restaurar o arquivo de configuração e restabelecer o processo original registrado no snapshot.

## Pitfalls

- **Mutação cega de timeout**: elevar timeouts excessivamente mascara deadlocks em servidores MCP baseados em stdio.
- **Processos órfãos de MCP stdio**: matar o agente sem fechar o pipe deixa daemons consumindo CPU/RAM em segundo plano.
- **Incompatibilidade de schema silenciosa**: tools com schemas inválidos são ignoradas silenciosamente por alguns providers de LLM, sumindo do catálogo.

## Verification

- Confirmar que todas as tools esperadas do servidor MCP respondem à listagem.
- Verificar se a latência média do handshake MCP está dentro dos limites operacionais (< 500ms local).
- Certificar-se de que não restaram processos zumbis após diagnósticos.
