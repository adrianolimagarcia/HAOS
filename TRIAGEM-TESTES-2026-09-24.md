# Triagem observada — suíte global e migração PROV-O

## Data da evidência
2026-09-24 (execução completa via `scripts/run_tests.sh`).

## Resultado global
- `scripts/run_tests.sh`: `exit_code=1`.
- A saída listou falhas em múltiplas áreas do repositório e um arquivo sem testes por erro/timeout de coleta: `tests/agent/test_compression_concurrent_fork.py`.
- Não há runner ativo após a conclusão.

## Gates PROV-O executados separadamente
- `pytest tests/test_proveniencia_eval.py tests/test_prov_phase_e.py tests/scripts/test_prov_golden.py tests/scripts/test_prov_parity.py`: `15 passed`.
- `cargo test -p haos-prov`: passou; 5 testes CLI + 6 testes de contrato.
- `cargo fmt --check -p haos-prov`: passou.
- `cargo clippy -p haos-prov --all-targets -- -D warnings`: passou.

## Falhas observadas na suíte global
A execução reportou famílias em:
- `tests/haos_edge/` (SSE e writer Rust);
- `tests/gateway/`;
- `tests/hermes_cli/`;
- `tests/platform/memory/`, `tests/platform/webui/` e `tests/platform/`;
- `tests/scripts/`;
- `tests/tools/`;
- `tests/test_dispatch_session_id.py`, `tests/test_haos_external_worker.py`, `tests/test_haos_hybrid_memory.py`.

## Classificação atual
### Confirmadamente relacionados à migração
- Nenhum nos gates específicos PROV-O.
- Uma asserção antiga em `tests/test_proveniencia_eval.py` fixava `15/15`; foi corrigida para validar a relação `conformes == total` com `total >= 15`. Depois da correção: 6/6 nesse arquivo.

### Observados, mas ainda não atribuídos
Os demais devem ser tratados pela força-tarefa com reprodução isolada. O nome/área do teste não basta para provar causalidade.

### Evidência já obtida de itens fora do escopo PROV-O
- `tools/environments/local_hermes_exec.py:36`: `subprocess.run(text=True)` sem `encoding`.
- `tools/environments/local_health_agent.py:71`: `subprocess.run` sem `stdin` explícito.
- `hermes/platform/decision/__init__.py`: violação do contrato PEP-420 reportada.
- `hermes/platform/webui/controlplane.py`: símbolo `_resolve_role_model_binding` ausente no teste.
- `hermes/platform/execution/team.py`: arquivo reportado ausente pelo teste de cobertura.
- Testes de modelo reportaram divergência `deepseek-v4-flash` vs `deepseek-chat`.

Esses itens são candidatos a correção independente; não foram alterados nesta migração.

## Atualização da força-tarefa — Lote 3 executado validamente

A primeira tentativa do Lote 3 foi inválida por falta de `.venv` no worktree isolado. A repetição no workspace principal, usando `.venv/bin/python`, foi válida: 16 falhas, 256 aprovados e 1 ignorado. A causalidade ainda não foi atribuída; os tracebacks precisam ser comparados com o diff antes de qualquer correção.
