# Implementação — verificação do coordenador

## Aplicado ao checkout principal, sem commit/deploy
- tools/mcp_tool_transport.py: retirar imports de três nomes inexistentes, que impediam importar tools.mcp_tool.
- tools/mcp_tool_discovery.py: importar contextmanager e Path usados pelo módulo.
- tools/mcp_tool_server_run.py: inicializar rebuild=False antes da primeira tentativa.

## Evidências independentes
Runner obrigatório scripts/run_tests.sh, interpretador de teste da worktree MCP via HERMES_PYTHON; HOME temporário para isolar defaults, runner cria HERMES_HOME temporário via seu fluxo. Runtime não modificado.
- Baseline no checkout principal: exit1, duas collection errors e seis failures pela importação inexistente, 13 passed.
- Depois de corrigir imports e antes de rebuild: teste permanent_failure_parks_without_retry_ladder RED exit1; UnboundLocalError rebuild, zero chamadas do transporte.
- Depois de rebuild: cinco arquivos MCP, 46 passed e 1 failed, exit1. Falha de revival após relogin: atributo _refresh_remote_config inexistente, reproduzido pelo coordenador; requer continuação.
- Mesmos cinco arquivos excluindo explicitamente test_initial_auth_failure_parks_and_revives_after_relogin: 46 passed, exit0. NÃO representa suíte completa verde.
- test_mcp_tool.py -k test_reconnect_on_disconnect: um teste selecionado, timeout do runner após ~51s, exit1; não validado contra base devido importação quebrada na base.
- git diff --check exit0.

## Limitações dos agentes
MCP criou commit isolado 011f7a30a3 contra instrução; não foi cherry-picked nem main alterado por commit. Dois agentes atingiram max_iterations sem relatório. Concorrência produziu teste não integrado e execução "No test files to run", que NÃO é validação. Stores sem patch identificado/causa provada. Não afirmar fixes nesses temas.

## Resultado final da etapa MCP
- tools/mcp_tool_server_run.py: restaurado `_refresh_remote_config`; stdio preserva config, remoto relê config sob o escopo atualizado já instalado pelo caller.
- Runner oficial em seis arquivos completos (`failure_classification`, `config_whitespace_warning`, `streamable_http_arity`, `invalid_url`, `tool_errors`, `tool`): **152 passed, 0 failed, exit0**, incluindo os testes antes bloqueados. Sem exclusões nessa execução. git diff --check exit0.
- Teste adicional `test_reconnect_rerenders_remote_headers_under_the_owners_fresh_scope`: FAILED antes de alcançar reconnect, `set_secret_scope() got an unexpected keyword argument profile_home`. Essa incompatibilidade preexistente limita validação multi-perfil. A suíte inteira do projeto não foi executada.
- Pyright aponta `name` desconhecido no mixin; objeto concreto MCPServerTask define esse atributo, mas diagnóstico estático permanece pendente.

## Pendências
Revisar dois contratos SessionDB isoladamente; não foram integrados, pois validação dos agentes não demonstrou testes executados e segundo teste não força interleaving. Stores continuam sem origem demonstrada do unlink. Nenhuma origem dos picos de I/O demonstrada, nenhum ganho de desempenho medido. Não tocar produção, sidecars ou serviços. Etapa de correções MCP concluída com limitação explícita multi-perfil; stores e SessionDB encerrados como investigação inconclusiva nesta rodada.
