# Lote 2 — Rust/edge e compressão

## Escopo e estado do worktree

- Worktree: `hermes-subagent/subagent-sa-0-d2dd69ed`.
- `git status --short` antes/depois: limpo; `git diff --stat`: vazio.
- Não houve alteração em código ou nos três testes investigados.
- `PLAN-FORCA-TAREFA-TESTES.md` e `TRIAGEM-TESTES-2026-09-24.md` estão vazios neste worktree.

## Execução

O runner canônico foi usado em cada arquivo (`scripts/run_tests.sh`). A primeira tentativa não iniciou porque o worktree não tem `.venv`/`venv` com pytest e `HERMES_PYTHON` estava vazio. A repetição, ainda pelo runner canônico, usou o interpretador disponível `/usr/local/lib/haos-agent/venv/bin/python` via `HERMES_PYTHON`; o resultado é executável e reproduzível no ambiente atual.

| Arquivo | Resultado observado | Classificação |
|---|---:|---|
| `tests/haos_edge/test_readonly_sse_contract.py` | 7 coletados; **1 passou, 6 falharam**; 1.7 s | **Baseline independente** (gate contratual deliberadamente bloqueado) |
| `tests/haos_edge/test_rust_writer_contract_v2.py` | 9 coletados; **1 passou, 8 falharam**; 1.3 s | **Baseline independente** (gate contratual deliberadamente bloqueado) |
| `tests/agent/test_compression_concurrent_fork.py` | 52 coletados; **52 passaram, 0 falharam**; 45.8 s | **Sem falha reproduzida**; não é flake/timeout observado |

A tentativa inicial sem interpretador elegível falhou antes da coleta em cada arquivo com:

```text
error: no virtualenv with pytest found ...
```

Isso é **problema de ambiente de execução**, não falha de teste nem “arquivo sem coleta”. Após fornecer um interpretador que realmente importa pytest, os três arquivos coletaram normalmente.

## Falhas Rust/edge

### `test_readonly_sse_contract.py`

As seis falhas são `pytest.fail(...)` explícitos no próprio teste, com mensagens `BLOCKED`, e não tracebacks inesperados do runtime. O contrato ainda não está implementado no estado verificado:

1. ausência de `contract_version`/`profile` explícitos nas respostas e dependência de `HAOS_DATA_DIR`;
2. `/api/sessions/fast` aceitando chamada sem autenticação;
3. servidor Rust sem binding explícito de home/profile;
4. SSE sem envelope versionado e sem profile explícito;
5. divergência de paridade de sessões (arquivamento, parent/branch, ordenação, campos e `schema_version`);
6. superfícies escritoras Rust ainda impedindo provar que Python é a única autoridade de escrita.

O teste de invariantes SQLite read-only passou, portanto não há evidência de falha nessa parte do fixture.

**Classificação:** baseline independente, não regressão comprovada. O arquivo foi introduzido/atualizado no commit `c1f024c266` (`test(haos-edge): specify Rust writer contract v2`), o worktree não tem diff local, e os próprios testes documentam o bloqueio esperado.

### `test_rust_writer_contract_v2.py`

As oito falhas são igualmente `pytest.fail(...)` explícitos (`BLOCKED`). O estado verificado não possui:

- rota POST `/internal/rust-writer/v2/operations` versionada;
- binding obrigatório e imutável de profile/data-dir;
- fronteira de autenticação interna antes do acesso ao banco;
- dispatcher tipado que rejeite SQL bruto/campos desconhecidos;
- idempotência atômica com conflito de fingerprint;
- deadline transacional com rollback;
- envelope/códigos de erro v2 estáveis;
- corte para manter o writer como única autoridade de mutação.

**Classificação:** baseline independente, não regressão comprovada. O teste também pertence ao commit `c1f024c266`, não há alteração local, e sua docstring diz que os gates deliberadamente falham enquanto o contrato não for implementado.

## Arquivo de compressão / coleta

`tests/agent/test_compression_concurrent_fork.py` não apresentou falha: foram coletados 52 testes e todos passaram. O processo levou 45.8 s, abaixo de qualquer timeout reportado pelo runner; não houve retry marcado como flaky.

**Classificação:** sem falha reproduzida. A hipótese de “arquivo sem coleta” não se confirma no ambiente após resolver o pré-requisito do runner. Se “sem coleta” se referia à tentativa inicial, a causa foi exclusivamente ausência de um interpretador/venv elegível com pytest.

## Conclusão

Não há regressão comprovada neste lote. Há dois gates contratuais Rust/edge conscientemente vermelhos por funcionalidades ainda bloqueadas e um arquivo de compressão verde. Nenhum código foi corrigido.
