# Fechamento cirúrgico — resultado verificado

## P1 — contrato profile_home
Causa diretamente verificada: caller `_boot_scope` em tests/tools/test_mcp_connect_secret_scope.py e `_install_owner_secret_scope` passam profile_home, enquanto agent/secret_scope.py tinha assinatura somente secrets. RED executado no checkout raiz: 4 failed, 2 passed, exit1; TypeError profile_home. História: git show 2c3a75beaa demonstra implementação anterior de proveniência; checkout atual perdeu esse contrato enquanto callers permaneceram. É divergência/regressão de integração; commit exato que removeu não determinado (não presumir autor/caminho).

Patch restrito: restaurar implementação de scope com mapping + home, preservar set/reset/current mapping e overlay no próprio perfil; misses de home estrangeiro fecham. Config MCP discovery carregada no owner scope (teste existente falhava nesse caminho). Teste reconnect fortalecido com A→B→A, reset e home registrado, snapshot B antes hidratação e valor B fresco após reconnect. Testes usam transporte controlado e config/.env reais temporários; não validam rede remota real.

GREEN independente: scripts/run_tests.sh tests/tools/test_mcp_connect_secret_scope.py tests/agent/test_secret_scope.py tests/hermes_state/test_surgical_lifecycle_contracts.py -j 2 --file-timeout 30: 35 passed, 0 failed, exit0. Interpretador teste via HERMES_PYTHON, HOME temporário. A execução inicial sem pytest NÃO conta como RED. O agente não fez RED; coordenador reproduziu antes de integrar patch.

## P2 — SessionDB
Dois contratos executados contra implementação inalterada, DBs reais temporários:
- Callback escreve SQL, segura lock antes commit; wrapper observa acquire não bloqueante falhando no closer, comprova tentativa de lock durante escrita ativa, libera writer e confirma persistência/integridade em conexão independente.
- Worker bloqueado antes append; close retorna e _conn=None antes liberação; worker roda sob contexto de home B, reabre exatamente path A e confirma linha e integridade, sem DB B.
Ambos GREEN na base. **Causa não determinada** para warning operacional. Sem patch SessionDB. Testes provam apenas esses cenários, não ausência universal de corrupção/races.

## P3 — I/O
Após P1/P2, amostra read-only /proc PID892918 por 15.0057s: read_bytes delta0, write_bytes delta3657728 (~243756 B/s), syscr4191/syscw285, rchar18736362/wchar2149. WAL/logs abertos monitorados sem variação de tamanho. PSI host some avg10=0.14, full=0.10. Tamanho constante NÃO prova ausência de escrita (WAL reciclado/checkpoint).
**Inconclusivo:** pico histórico de leitura não reproduzido; processo conhecido, arquivo/rotina/syscall responsável pelo pico NÃO identificados. Sem contagem de fsync nem checkpoint forçado; sem tracing invasivo. Nenhuma otimização aplicada ou ganho alegado.

## Arquivos nesta etapa
agent/secret_scope.py; tools/mcp_tool_discovery.py (além dos imports MCP anteriores); tests/tools/test_mcp_connect_secret_scope.py; tests/hermes_state/test_surgical_lifecycle_contracts.py; este relatório.
Mudanças MCP anteriores preservadas tools/mcp_tool_transport.py e tools/mcp_tool_server_run.py.

## Riscos/limites
Suite completa projeto não executada; suite MCP de 152 testes passou antes desta etapa, não reutilizar como pós-patch. Prova foreign-home com multiplex desligado não adicionada nesta rodada. Pyright pode apontar wrapper de lock de teste por duck typing, não falha runtime. Nenhum deploy/restart/produção/dados persistentes alterados; commits de agentes continuam isolados, sem cherry-pick. Alterações preexistentes alheias preservadas.
