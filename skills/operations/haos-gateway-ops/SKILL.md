---
name: haos-gateway-ops
description: Diagnóstico e operações de gateways HAOS.
version: 1.0.0
author: Adriano Lima, Hermes Agent
license: MIT
platforms: [linux, macos]
category: operations
tags: [haos, gateway, messaging, operations, diagnostics]
---

# HAOS Gateway Operations Skill

Diagnóstico governado e runbook operacional dos gateways de mensageria e conectividade do HAOS.

## When to Use

Use quando precisar verificar a integridade de gateways de mensageria (Telegram, Discord, Slack, etc.), conferir canais conectados, investigar quedas de sessão, latência de transporte ou validar o status do daemon `haos-gateway.service`.

## Prerequisites

- Daemon do gateway em execução ou inspecionável via systemd/status.
- Acesso de leitura aos logs de gateway (`~/.haos/logs/gateway.log` ou `~/.hermes/logs/gateway.log`).
- Portas e sockets locais de mensageria configurados.

## How to Run

Execute diagnósticos preferencialmente via inspeção não invasiva de processo, systemctl status read-only e verificação de logs:

```bash
systemctl --no-pager status haos-gateway.service
journalctl -u haos-gateway.service -n 50 --no-pager
```

## Quick Reference

| Componente / Verificação | Comando / Caminho | Ação |
|---|---|---|
| Status do serviço | `systemctl is-active haos-gateway` | Diagnóstico de vida |
| Canais ativos | `journalctl -u haos-gateway -n 100 \| grep -i "channel"` | Identificação de conexões |
| Sessões ativas | `curl -s http://127.0.0.1:9900/health` (se exposto) | Health check local |
| Erros recentes | `grep -i "error" ~/.haos/logs/gateway.log \| tail -n 20` | Triagem de falhas |

## Procedure

1. **Diagnóstico Read-Only**:
   - Inspecione se o serviço `haos-gateway.service` está ativo e sem crashes recentes.
   - Verifique a conectividade de loopback e as portas de binding (ex.: 9900).
   - Analise os logs recentes buscando falhas de autenticação de tokens de bots, desconexões de socket ou rate limiting.
   - NUNCA execute reinicializações automáticas sem diagnóstico prévio.

2. **Pré-condições**:
   - Confirmar que o problema não é externo (ex.: indisponibilidade da API do Telegram/Discord).
   - Checar se não há locks concorrentes de perfil (`_profile_runtime_scope`).
   - Obter os IDs de canais/sessões afetadas.

3. **Snapshot**:
   - Salvar o estado atual de processos e conexões de rede em arquivo temporário:
     `ss -tulpn | grep 9900 > /tmp/gateway_snapshot_$(date +%s).txt`
   - Coletar as últimas 200 linhas de logs do gateway para preservação de evidência.

4. **Autorização**:
   - Qualquer mutação (como `systemctl restart haos-gateway` ou flush de sessões) EXIGE autorização explícita do operador humano.
   - Apresente ao operador: diagnóstico, causa provável e impacto da reinicialização na entrega de mensagens.

5. **Rollback**:
   - Em caso de falha pós-reinicialização, reverter arquivos de configuração modificados a partir do snapshot e restaurar o estado estável anterior.

## Pitfalls

- **Reiniciar gateway durante streaming de mensagem**: derruba streams em andamento para o usuário, quebrando o invariante de alternância de turnos.
- **Supor falha interna quando há rate limit da plataforma**: Telegram e Discord impõem 429; reiniciar o serviço piora o backoff e pode banir o token.
- **Hardcoding de diretório de logs**: use caminhos resolvidos via profile (`get_hermes_home()`), pois perfis secundários usam `~/.haos/profiles/<name>/logs/`.

## Verification

- Confirmar que `systemctl is-active haos-gateway` retorna `active`.
- Testar ping/health check no endpoint local de mensageria.
- Verificar se o log indica reconexão bem-sucedida de todos os adaptadores de plataforma habilitados.
