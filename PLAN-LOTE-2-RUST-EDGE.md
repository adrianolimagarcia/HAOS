# Plano de correção — Lote 2 Rust/edge

## Objetivo
Fechar os contratos Rust/edge sem misturar a migração PROV-O:

1. `tests/haos_edge/test_readonly_sse_contract.py` — 6 gates bloqueados.
2. `tests/haos_edge/test_rust_writer_contract_v2.py` — 8 gates bloqueados.
3. Manter `tests/agent/test_compression_concurrent_fork.py` verde — baseline confirmado em 52/52.

## Fase atual

**Fase 0 reconciliada.** A matriz G01–G14 foi adicionada abaixo e `docs/haos/rust-readonly-sse-contract-v1.md` foi reescrito para refletir a referência Python efetiva. Divergência anterior corrigida: filtros archived/hidden/parent/branch não são requisitos comprovados por `_sessions_list`; não devem ser inventados.

A fatia read-only foi delegada em worktree isolado. O plano operacional de cutover/deploy está em `docs/haos/lote-2-cutover-deploy-plan.md`, mas é somente planejamento: writer v2 e cutover seguem bloqueados. Nenhum deploy será feito.

## Auditoria independente do Writer v2 — 2026-09-24

A auditoria contra `d689488c0e` confirmou que o executor tipado existe como biblioteca, mas o cutover não está pronto:

- **G1/G2:** não há rota HTTP versionada nem autenticação interna dedicada; `WriterExecutor` não está ligado ao `run_server`.
- **G3:** envelope fechado e SQL parametrizado existem, mas operações anunciadas como `set_message_reaction` e `archive_and_compact` retornam `UnsupportedOperation`.
- **G4/G5:** idempotência, fingerprint e rollback têm testes unitários; não há prova na rota HTTP nem fault injection completo após mutação/pré-commit.
- **G6:** não existe envelope/status HTTP v2 estável; vários códigos exigidos não estão mapeados.
- **G7:** lock por profile existe no executor, mas o ciclo real do servidor não o adquire; EventHub permanece writer independente.
- **G8:** não há paridade golden, integração de rota, inventário completo de writers concorrentes ou prova de rollback de deploy.

Evidência executada: `cargo test -p haos-edge writer_ --no-default-features` passou com 15 testes. Os testes Python não foram executados porque `scripts/run_tests.sh` não encontrou virtualenv pytest. As oito falhas contratuais permanecem bloqueios intencionais. **Conclusão: Writer v2 não pode entrar em cutover/deploy.**


O código já possui partes do desenho pretendido:

- binding explícito de profile/data-dir em `packages/haos-edge/src/profile.rs`;
- envelope v2 em `writer_envelope.rs`;
- lock por profile/data-dir em `writer_lock.rs`;
- executor tipado, idempotência e transações em `writer_executor.rs`;
- leituras SQLite read-only em `lib.rs`.

Mas os endpoints HTTP/SSE e o corte de autoridade ainda não demonstram o contrato completo. Os testes atuais usam `pytest.fail("BLOCKED...")` como gates deliberadamente vermelhos; não devem ser simplesmente removidos ou convertidos em snapshots.

## Invariantes não negociáveis

- Python continua sendo a autoridade de escrita até o cutover formal.
- Nenhuma rota read-only pode escrever em `state.db`, WAL ou SHM.
- Nenhuma rota aceita SQL arbitrário.
- Profile e data-dir devem ser identidade explícita e imutável por processo/servidor.
- Autenticação deve ser validada antes de abrir o banco.
- Idempotência deve ser transacional e detectar conflito de fingerprint.
- Timeout deve resultar em rollback, nunca em escrita parcial.
- Envelopes e códigos de erro são versionados e estáveis.
- Toda mudança deve ter teste comportamental, não teste de texto-fonte.

## Fase 0 — Reconciliação do contrato e inventário

**Estado:** reconciliado em `docs/haos/rust-readonly-sse-contract-v1.md`; matriz de 14 gates abaixo. A implementação deve seguir os handlers/runtime observados, não os `BLOCKED` texts cegamente. A referência ativa `hermes/platform/webui/standalone.py::_sessions_list` não expõe filtros archived/hidden/parent/branch; esses filtros não são requisito enquanto outra fonte canônica não for identificada.

| Gate | Estado observado | Lacuna a fechar |
|---|---|---|
| G01 GET sessions fast | `server.rs::sessions_fast_handler`, rota `/api/sessions/fast`, usa `state.data_dir` | envelope/version e profile explícito compatível |
| G02 Auth before DB | middleware cookie global e SSE checa sessão; sessions handler não checa internamente | black-box deve provar 401 antes de SQLite |
| G03 Profile binding | `AppState` usa `data_dir`; `profile.rs` pure resolver existe | ligar resolver ao startup real e expor binding no envelope |
| G04 data-dir scope | path vem do estado do processo | não aceitar path arbitrário por query; profile mismatch antes do DB |
| G05 SQLite read-only | fast sessions usa `READ_ONLY`; SSE replay abre RO | PRAGMA query_only, erros HTTP coerentes, bytes DB/WAL/SHM idênticos |
| G06 Session parity | Python fallback consulta id/title/timestamps, múltiplos DB e dedup | igualar consulta e resposta que o consumidor realmente espera |
| G07 Ordenação | ambos usam atividade com fallback a início | desempate determinístico sem mudar ordem de compatibilidade |
| G08 Campos | Rust: id e timestamps numéricos; Python: session_id, título e timestamps formatados, updated_ts, db | manter campos compatíveis; decidir forma aditiva por diferencial real |
| G09 Título ausente | Python usa primeira mensagem user truncada ou `Session <id>` | Rust devolve “Sem título”; portar regra ou provar consumidor não depende |
| G10 Limit/DB/errors | default 30 nos dois; Python tenta DBs e agrega; Rust usa um DB e erro pode sair 200 | validar limit e definir ausência/falha sem exposição interna |
| G11 SSE route/auth | `/api/events/stream`; cookie check no handler | profile/envelope explícitos e erro auth antes do DB |
| G12 replay/headers | eventos `seq` crescentes, 100 + resync, snapshot; erro SQLite é silencioso | headers testados e falha pré-stream reportada (503) |
| G13 writer surface | `EventHub::new` inicia writer; `/api/events/ingest` existe | modo observer sem EventHub writer nem ingest mutável |
| G14 A/B/A + envelope + bytes | endpoints presos ao state do processo | teste real com homes A/B/A e bytes DB/WAL/SHM preservados |

**Gate:** testes comportamentais e E2E provam o caminho real. Não inferir requisito por módulo solto e não remover `BLOCKED` sem substituto executável.

## Fase 1 — Read-only SSE (`readonly-sse.v1`)

### 1.1 Binding e autenticação

- [ ] Exigir `--profile` e `--data-dir` no startup das rotas read-only.
- [ ] Rejeitar ausência, mismatch e mudança posterior de binding antes de abrir SQLite.
- [ ] Definir autenticação interna para `/api/sessions/fast` e `/api/events/stream`.
- [ ] Testar que credencial ausente/inválida falha sem leitura do banco.

### 1.2 Envelope e headers

- [ ] Criar envelope comum com `contract_version`, `profile`, `schema_version` e payload.
- [ ] Definir headers SSE estáveis (`Content-Type`, cache, conexão e versão conforme o servidor).
- [ ] Emitir eventos com envelope versionado, sem campos implícitos do processo.
- [ ] Cobrir resposta JSON e eventos SSE com testes black-box reais.

### 1.3 Consulta e paridade de sessões

- [ ] Reproduzir o filtro canônico de sessões: archived, hidden, pinned, parent/branch, ordenação, campos e `schema_version`.
- [ ] Criar fixture com perfis A/B e IDs iguais para provar isolamento A→B→A.
- [ ] Comparar resposta Rust contra a consulta Python de referência.
- [ ] Garantir modo SQLite read-only e comparar bytes de DB/WAL/SHM antes/depois.

### 1.4 Autoridade de escrita

- [ ] Identificar e desativar, para o processo read-only, `EventHub::spawn_writer_task` e qualquer ingestão/flush mutável.
- [ ] Separar explicitamente superfície de leitura da superfície writer.
- [ ] Testar que a rota SSE não cria, atualiza ou compacta estado.

**Gate da Fase 1:** os 6 testes read-only deixam de usar `pytest.fail("BLOCKED")` e passam contra um servidor real isolado, com 0 alteração de bytes.

## Fase 2 — Writer v2 tipado

### 2.1 Rota e startup

- [ ] Expor `POST /internal/rust-writer/v2/operations` apenas no modo writer explícito.
- [ ] Exigir `--profile` e `--data-dir`; remover fallback `/tmp/haos_shared_data` para o caminho v2.
- [ ] Adquirir lock antes de abrir/escrever o banco.
- [ ] Rejeitar profile/data-dir divergentes com `profile_mismatch`/`data_dir_mismatch`.

### 2.2 Autenticação e validação

- [ ] Definir `Authorization` interno independente de cookie WebUI.
- [ ] Validar versão, schema, operação, campos permitidos, tipos e payload antes do SQLite.
- [ ] Manter registry fechado de operações; rejeitar SQL, `query`, `statement`, `mutation` e campos desconhecidos.

### 2.3 Transação, idempotência e timeout

- [ ] Executar cada operação em transação SQLite única.
- [ ] Gravar chave + fingerprint + resposta na mesma transação da operação.
- [ ] Repetição com mesmo fingerprint retorna a mesma resposta sem duplicar escrita.
- [ ] Mesmo idempotency key com fingerprint diferente retorna `idempotency_conflict`.
- [ ] Validar `timeout_ms` com limite mínimo/máximo.
- [ ] Usar deadline monotônico e rollback completo em timeout.
- [ ] Adicionar readback para confirmar ausência de escrita parcial.

### 2.4 Erros e compatibilidade

- [ ] Implementar envelope de erro `{contract_version, code, message, request_id}`.
- [ ] Mapear códigos para HTTP status de forma estável.
- [ ] Cobrir todos os códigos exigidos pelo contrato, inclusive `read_only`, `busy`, `storage_unavailable` e `schema_mismatch`.

### 2.5 Cutover de autoridade

- [ ] Enumerar todos os escritores SQLite atuais.
- [ ] Manter Python como único writer durante a validação v2.
- [ ] Criar feature gate/configuração explícita para habilitar Rust writer.
- [ ] Rodar período de observação sem cutover.
- [ ] Só depois de evidência de paridade, idempotência e rollback, escolher Rust como writer.
- [ ] Manter rollback para Python e documentar procedimento.

**Gate da Fase 2:** os 8 testes writer v2 passam contra rota real e fixture temporária; nenhum writer concorrente permanece ativo no modo v2.

## Fase 3 — Integração e regressão

- [ ] `cargo test -p haos-edge`.
- [ ] `cargo fmt --check -p haos-edge`.
- [ ] `cargo clippy -p haos-edge --all-targets -- -D warnings`.
- [ ] `scripts/run_tests.sh` para os dois arquivos de contrato.
- [ ] Reexecutar `tests/agent/test_compression_concurrent_fork.py`.
- [ ] Rodar testes Python de profile A→B→A com dois homes temporários.
- [ ] Rodar teste de rollback Python após falha/timeout do writer Rust.
- [ ] Atualizar `reports/forca-tarefa/lote-2-rust-edge.md` com returncodes, fixtures e limitações.

## Fase 4 — Critério de conclusão

O lote 2 só será considerado corrigido quando:

- 6/6 gates read-only passarem;
- 8/8 gates writer v2 passarem;
- compressão continuar 52/52;
- `cargo test`, `fmt` e `clippy` passarem;
- houver teste black-box de autenticação, isolamento de perfil, read-only por bytes, idempotência, conflito, timeout/rollback e erros;
- o cutover tiver rollback documentado;
- nenhum resultado depender de `pytest.skip`, remoção de gates ou teste tautológico.

## Riscos e decisões pendentes

- **Risco:** os testes podem descrever um contrato futuro, não o endpoint atualmente exposto; a Fase 0 deve resolver isso antes de codificar.
- **Risco:** eliminar o writer EventHub pode quebrar streaming; separar leitura, broadcast e persistência é obrigatório.
- **Risco:** migrar writer antes de provar o Python como fallback pode causar perda/duplicação de sessão.
- **Pendente:** decidir se o writer v2 é processo HTTP interno, IPC ou subcomando do `haos-edge`; não assumir pela presença dos módulos.
