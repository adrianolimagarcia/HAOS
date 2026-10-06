# Plano — Lote 2 Rust Edge: transformar gates em critérios executáveis

## Objetivo
Fechar as pendências Read-only/SSE e Writer v2 com verificações reproduzíveis, sem iniciar cutover/deploy e mantendo Python como autoridade de escrita até aprovação explícita dos gates.

## Diagnóstico confirmado
- Os testes em `tests/haos_edge/test_readonly_sse_contract.py` contêm `pytest.fail("BLOCKED")` em cinco critérios; a asserção real de imutabilidade é apenas a referência SQLite Python, não uma execução do binário Rust.
- `tests/haos_edge/test_rust_writer_contract_v2.py` contém sete `pytest.fail("BLOCKED")`; não inicia servidor nem testa HTTP/SQLite real. Logo, esses arquivos registram bloqueios, mas não são gates funcionais que uma implementação possa satisfazer.
- Resultado reportado pelo agente: Writer test `1 passed, 8 failed`; não foi reexecutado nesta reconciliação. Read-only não foi executado por ausência de venv no worktree do agente.
- Commit `70789c4708` não é integrável isoladamente: seu `server.rs` contém o literal não-Rust `Option<String>` e importa módulos que não aparecem no próprio commit. A rota está ligada à inicialização, mas isso não prova compilação, autenticação antes de DB, nem paridade.
- O checkout de trabalho contém alterações e artefatos não relacionados; não devem ser descartados.

## Fases

### Fase 0 — Base e integridade
1. Registrar `git status`, HEAD e worktree graph.
2. Escolher uma cadeia de commits coerente, sem cherry-pick cego entre bases distintas.
3. Preservar arquivos preexistentes e manter todas as mudanças em worktree/branch isolado.
4. Verificar que os dois arquivos de contrato não foram modificados para reduzir critérios.

### Fase 1 — Executabilidade dos critérios
1. Substituir cada `pytest.fail("BLOCKED")` por testes comportamentais observáveis; não apagar requisito.
2. Expor um router builder/in-process harness ou iniciar o binário real em subprocesso com diretórios temporários e porta efêmera.
3. Criar fixtures reais SQLite; exercitar requests HTTP e conferir respostas, banco, WAL/SHM e isolamento.
4. Para cada teste, documentar pré-condição, ação, asserções observáveis e comando de execução.
5. Só então considerar vermelho/verdes como resultado funcional, distinguindo falha de setup.

### Fase 2 — Read-only/SSE
Implementar profile/data-dir explícitos e vinculados; autenticação antes da abertura do banco; envelope versionado; filtros/ordenação/campos compatíveis com Python; SSE com headers definidos; A→B→A com DBs distintos; abrir banco read-only e comparar hashes de DB/WAL/SHM antes/depois; remover/suprimir handlers, tasks e conexões gravadoras em modo observer. Provar os critérios por black-box contra binário Rust e referência Python.

### Fase 3 — Writer v2
Rota interna versionada; token próprio e auth antes de qualquer acesso SQLite; binding imutável de profile/data-dir; operações fechadas/typed sem SQL genérico; implementação coerente de todas operações anunciadas; idempotência e fingerprint na mesma transação da mutação; deadline monotônico com rollback/readback; envelope de erro code/status estável. Testar via HTTP real com DB temporário, tokens inválidos, retries, payload conflitante, lock SQLite e falha injetável de transação.

### Fase 4 — Verificação de integração
1. Compilar e testar crates afetados (`cargo fmt --check`, `cargo test -p haos-edge`, `cargo clippy -p haos-edge --all-targets -- -D warnings`, conforme toolchain do repo).
2. Rodar suíte Python exclusivamente por `scripts/run_tests.sh` com interpretador do venv do checkout e sem mascarar falhas.
3. Rodar gates black-box, baseline compression 52/52, regressões pertinentes e `git diff --check`.
4. Revisor independente confere base, diffs, testes e ausência de modificações nos testes para enfraquecê-los.
5. Atualizar matriz G01–G14 com nome de teste, comando e resultado observado.

### Fase 5 — Preparação de cutover (sem executar)
Somente após gates funcionais aprovados: documentar feature gate default-off, fallback Python, backup/restore ensaiado em staging, métricas/logs e rollback. Não alterar serviços ou produção neste plano.

## Critério de conclusão deste plano
- Todos os testes deixam de ser placeholders sem reduzir critérios.
- 6/6 Read-only/SSE e 8/8 Writer v2 passam por execução real e reproduzível.
- Testes de crate/lint/regressão especificados passam.
- Python permanece autoridade única; nenhum deploy/cutover foi realizado.
- Evidência inclui SHA, diff, comandos e saídas verificadas.

## TODO
- [x] F0: escolher e registrar base coerente, preservar working tree. **Concluído 2026-09-26** — base `feat/rust-writer-migration-plan` @ `d689488c0e` (linear de `main` `3d4dc936a5`); working tree preservado (7 `M`, 66 `??`, 0 staged, stash vazio); gates inalterados desde `c1f024c266` (6+8 `pytest.fail` intactos). `feat/rust-writer-contract-v2` e `fix/rust-pending-cleanup` são duplicatas já contidas em HEAD (diff vazio). `412c5f81ef` (rota v2) é exclusivo e fica **adiado para a Fase 2**. Evidência completa em `reports/forca-tarefa/lote-2-rust-edge.md` § F0.
- [x] F1: criar testes black-box executáveis mantendo requisitos originais. **Concluído 2026-09-26** — 14/14 `pytest.fail` removidos e substituídos por testes comportamentais reais (binário `haos-edge` em subprocesso, HTTP, SQLite temporário; nenhum teste lê código-fonte). Resultado: **15 passed, 1 failed** em 77 s (`tests/haos_edge/`, worktree `.worktrees/lote2-observer-reconcile`). A única falha é `test_session_parity_fixture_is_required`, agora um vermelho funcional com diff de campo-a-campo (não mais placeholder) — pertence ao F2. Detalhe da divergência e comando em `reports/forca-tarefa/lote-2-rust-edge.md` § F1.
- [ ] F2: implementar e provar Read-only/SSE.
- [ ] F3: implementar e provar Writer v2.
- [ ] F4: executar verificações completas e auditoria independente.
- [ ] F5: preparar checklist de staging/cutover/rollback, sem executar produção.
