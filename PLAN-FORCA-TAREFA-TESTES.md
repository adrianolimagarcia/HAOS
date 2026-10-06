# Plano — força-tarefa de correção da suíte global

## Objetivo
Reduzir as falhas observadas na suíte completa do HERMES-TURBO sem misturá-las com a migração PROV-O, que possui gates próprios verdes.

## Regras
- Reproduzir cada arquivo isoladamente com `scripts/run_tests.sh`.
- Não atribuir causa apenas pelo nome do teste.
- Preservar alterações não relacionadas já presentes no working tree.
- Corrigir uma família por vez e executar o teste de regressão correspondente.
- Não declarar a suíte global verde enquanto houver falhas ou arquivo sem coleta.

## Lotes de trabalho

### Lote 1 — coleta e baseline
- [x] Congelar a lista final de arquivos/testes falhos da última execução.
- [x] Reexecutar cada arquivo isoladamente e capturar traceback completo.
- [x] Comparar cada arquivo com `git diff` e com as alterações pendentes do working tree.
- [x] Separar regressão real, baseline quebrado, teste obsoleto e flake/timeout.
- [x] Delegar investigação paralela dos lotes Rust/edge, gateway/CLI/sessão e memória/WebUI/ferramentas.
- [x] Consolidar os relatórios em `reports/forca-tarefa/`.

### Lote 2 — contratos Rust/edge
- [x] Investigar `tests/haos_edge/test_readonly_sse_contract.py`.
- [x] Investigar `tests/haos_edge/test_rust_writer_contract_v2.py`.
- [x] Investigar `tests/agent/test_compression_concurrent_fork.py` (coleta/timeout).
- [ ] Corrigir somente após reprodução mínima e teste de contrato.

### Lote 3 — gateway, CLI e sessão
- [ ] Investigar `tests/gateway/test_failure_writer_ownership.py` e `test_platform_base.py`.
- [ ] Investigar `tests/hermes_cli/test_approvals_suggest.py`, compat manifest, aliases/credentials e doctor.
- [ ] Investigar `tests/test_dispatch_session_id.py`.

### Lote 4 — memória, WebUI e evals
- [ ] Investigar `tests/platform/memory/test_skill_promotion.py`.
- [ ] Investigar `tests/platform/memory/test_memory_governance.py` e `tests/test_haos_hybrid_memory.py`.
- [ ] Investigar `tests/platform/webui/test_agent_hierarchy.py` e `test_council_adapter.py`.
- [ ] Investigar `tests/platform/test_evals_real.py` e `test_canonical_adr_contract.py`.

### Lote 5 — ferramentas e segurança
- [ ] Investigar approval, model tools, tirith, browser vault e terminal truncation.
- [ ] Corrigir os footguns identificados em `tools/environments/local_hermes_exec.py` e `local_health_agent.py`, se confirmados como defeitos reais.
- [ ] Corrigir `tools/environments` apenas com testes específicos e sem relação artificial com PROV-O.

### Lote 6 — validação final
- [ ] Rodar todos os gates específicos da migração PROV-O.
- [ ] Rodar `scripts/run_tests.sh` completo sem concorrência.
- [ ] Registrar pass/fail, arquivos sem coleta e limitações.

## Critério de encerramento
- Cada falha tem resultado: corrigida, aceita como baseline documentado, teste ajustado por contrato, ou bloqueio explícito.
- Nenhuma falha é atribuída à migração PROV-O sem evidência reproduzível.
- A suíte completa só é declarada verde com `returncode == 0`.
