# Runbook & Evidências: Kanban Rust Candidate Selector (Read-Only Opt-In)

## 1. Visão Geral e Limites de Escopo
- **Escopo Implementado:** Fase 1 do plano incremental (`PLAN_KANBAN_RUST_DISPATCHER.md`). Apenas seleção e ordenação read-only de candidatos (`ready` e `review`) delegadas opcionalmente ao binário Rust `haos-edge`.
- **Invariantes Preservados:**
  - Python é o **único scheduler ativo**: responsável exclusivo por `dispatch.lock`, `claim_task`, leases/TTL, transações SQLite, workspaces/worktrees, e lifecycle de processos/spawning.
  - Zero escritas, zero claims e zero mutações no caminho Rust.
  - Modos de operação: `off` (padrão), `shadow` (observação e telemetria de divergência sem mutar candidatos Python) e `rust` (ordenação de candidatos entregue pelo Rust revalidada em transação pelo Python).
  - Configuração via `config.yaml` / `DEFAULT_CONFIG` (`kanban.rust_selector`, `kanban.rust_selector_binary`, `kanban.rust_selector_timeout_seconds`), com override de teste por `HERMES_KANBAN_RUST_SELECTOR` e `HAOS_EDGE_BIN`.
  - Resolução de binário segura: proibida busca automática em pastas `debug`/`target`. Binário é resolvido via config explícito, env explícito `HAOS_EDGE_BIN`, ou diretórios canônicos de sistema (`/usr/local/bin/haos-edge`, `/opt/haos/bin/haos-edge`, `/usr/bin/haos-edge`, PATH).
  - Validação estrita de contrato: JSON `kanban_selector_v1`, tipagem de candidatos, unicidade de IDs, validação de bounds e tratamento fail-open (fallback para Python transparente em caso de erro, timeout ou payload inválido).
- **Sem Deploy / Sem Integração Civilization:** Nenhuma alteração foi implantada em produção e nenhuma alegação de performance ou automação de Civilization é feita.

## 2. Contrato da CLI Rust
- **Comando:** `haos-edge kanban-select --db-path <PATH> --profile <PROFILE> --data-dir <DIR> [--include-review]`
- **Validação de Escopo:** O `db_path` canonicalizado é validado contra o `data_dir` canonicalizado do profile resolvido. Caminhos fora do `data_dir` retornam erro estruturado com código de saída 3.
- **Saída de Sucesso:**
  ```json
  {
    "contract_version": "kanban_selector_v1",
    "ok": true,
    "candidates": [
      {
        "id": "t_123",
        "assignee": "alice",
        "lane": "ready",
        "status": "ready",
        "priority": 10,
        "created_at": 1700000000
      }
    ],
    "error": null
  }
  ```

## 3. Evidências de Testes Realizados

### A. Testes Unitários Rust
Executado: `cargo test -p haos-edge kanban_selector` e `cargo test -p haos-edge`
- `kanban_selector::tests::test_select_candidates_ordering_and_filtering`: PASSOU
- `kanban_selector::tests::test_select_candidates_missing_db`: PASSOU
- Suite completa de 39 testes em `haos-edge`: PASSOU

### B. Testes E2E e Integração Python
Executado: `HERMES_PYTHON=/usr/local/lib/haos-agent/venv/bin/python scripts/run_tests.sh tests/hermes_cli/test_kanban_rust_selector.py`
- `test_rust_selector_unit_contract_and_ordering`: PASSOU (ordenação idêntica por `priority DESC, created_at ASC, id ASC`)
- `test_rust_selector_ignores_claimed_and_other_statuses`: PASSOU (ignora tarefas travadas ou em outros status)
- `test_rust_selector_modes_off_shadow_rust`: PASSOU (modos `off`, `shadow` e `rust`)
- `test_dispatch_once_integration_with_rust_selector`: PASSOU (integração real com `kbd.dispatch_once`, claims Python e spawn mockado em boundary)
- `test_rust_selector_fallback_on_failure`: PASSOU (binário inexistente/falha resulta em fallback transparente sem travar)
- `test_profile_isolation_a_b_a`: PASSOU (isolamento estrito entre `home_a` e `home_b`)
- `test_malformed_scope_and_path_traversal_rejected`: PASSOU (rejeição de traversal e db fora de `data_dir`)
- `test_stale_candidate_revalidated_by_python_claims`: PASSOU (candidato desatualizado revalidado na leitura/claim Python)
- `test_timeout_falls_back_gracefully_to_python`: PASSOU (budget timeout respeitado com fallback imediato)
- `test_ordering_parity_between_python_and_rust`: PASSOU (paridade estrita com `_lane_rows`)

Resultado: 10 passed em 3.8s.

### C. Regressão da Suite Completa Kanban
Executado: `HERMES_PYTHON=/usr/local/lib/haos-agent/venv/bin/python scripts/run_tests.sh tests/hermes_cli/test_kanban*.py`
Resultado: 61 arquivos, 400 testes aprovados, 0 falhas, 1 ignorado (windows-only) em 43.8s.
