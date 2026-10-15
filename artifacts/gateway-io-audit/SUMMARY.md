# Auditoria gateway — consolidação

## Estado
Análise concluída. Nenhuma correção ou alteração de serviço/configuração autorizada ou executada pelo coordenador. Relatórios originais em `.worktrees/subagent-sa-*/artifacts/gateway-io-audit/`.

## Evidência e prioridades
1. **Confirmado pelo coordenador:** `mcp-stderr.log` com 234857350 bytes; caminho de abertura em `tools/mcp_tool_config.py:22-44` é append raw sem rotação nessa função. Agente mediu crescimento zero em 30s. Propor rastrear launcher/venv do erro amostral `curl_cffi`, distinguir erro histórico de falha atual, tratar shutdown esperado sem traceback e planejar retenção coordenada multi-processo. Não usar copytruncate como solução automática. Redirecionamento stdout de Ouroboros relatado é hipótese de quebra de protocolo, ainda sem associação ao transporte real.
2. **Confirmado pelo coordenador:** gateway mantém FDs `(deleted)` para WAL/SHM de `fabric` e `vectors`. Auditar unlink/rebuild e persistência da geração antiga; não concluir corrupção/vazamento, nem remover sidecars.
3. **Relato do agente:** warning close→write é real, mas lock serializa fechamento e callback. Reabertura protege flush tardio. Identificar owner/caller antes de corrigir lifecycle; não suprimir recuperação. Cinco testes passaram via comando alternativo, mas runner oficial falhou por pytest ausente: validação CI-parity pendente.
4. **Relato do agente:** amostra de 35s mostrou ~0.23 KiB/s leitura e ~121.3 KiB/s escrita do PID; sem demonstração do pico histórico. PSI do host e do cgroup divergem: não atribuir pressão global ao gateway.
5. **Proposto:** medir bytes e duração por append/FTS/checkpoint/chore e cron antes de reduzir pools, índices ou cadência. PRAGMAs de conexão observadora não demonstram configuração da conexão writer; WAL pode reciclar sem diminuir arquivo.

## Restrições e limitações
Relatórios são auto-relatos revisados, não reprodução independente completa. Auditoria SQLite fez contagens de tabelas apesar da orientação contra scans integrais; custo dessa leitura não foi medido. Teste de concorrência não seguiu runner oficial após falha de dependência. Caminho do relatório de concorrência veio malformado; relatório localizado no worktree correto sem tratar token informado como validado.

## Critério para execução posterior
Correções em fatias mínimas com reprodução isolada, baseline e testes de regressão; sem reinício cego, alteração de SQLite em produção ou redução de funcionalidade. Nenhuma otimização tem ganho medido nesta auditoria.
