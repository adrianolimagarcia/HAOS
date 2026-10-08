# Runbook: Procedimento de Deploy Persistente, Shadow/Canary e Rollback do Kanban Rust

## 1. Visão Geral e Arquitetura

O **Kanban Rust Runtime** adiciona componentes de alta performance ao pipeline do Kanban no HAOS:
1. **`kanban-select` (Rust)**: Seleção determinística e ordenação de candidatos (`ready` e `review`) em SQLite.
2. **`kanban-claim` (Rust)**: Aquisição atômica de tarefas com fencing de lease (`claim_lock`), validação de dependências (direct parents), emissão de auditoria append-only (`task_events`) e registro em `task_runs`.
3. **`kanban-heartbeat` (Rust)**: Renovação rápida de lease de tarefas em execução.

### Invariantes de Segurança:
- O banco `/root/.haos/kanban.db` **nunca é migrado de forma destrutiva**: o schema é 100% compatível entre Python e Rust.
- Python mantém total soberania sobre workspace resolution (`git worktree`), spawning de subprocessos/agentes e hooks.
- **Fail-open & Graceful Fallback**: Se o binário falhar, sofrer timeout ou retornar erro de contrato, o dispatcher em Python reassume imediatamente.
- **Detecção de Timeout com Adoção**: Se o Rust gravar no banco mas o subprocesso expirar na resposta ao Python, a task já alocada é adotada com segurança evitando execuções duplicadas.

---

## 2. Matriz de Componentes e Hashes (SHAs)

- **Binário `haos-edge`**:
  - Caminho canônico: `/usr/local/bin/haos-edge`
  - Versão: `haos-edge 0.1.0`
  - SHA-256: `ecfc13981403d6f35f96cae60073bdfda44e4a0653683018d93ef0d62fd2ceb2`
  - Subcomandos validados: `kanban-select`, `kanban-claim`, `kanban-heartbeat`
- **Módulos Python de Bridge**:
  - `hermes_cli/kanban_rust_claim.py`
  - `hermes_cli/kanban_rust_selector.py`
  - `hermes_cli/kanban_db_dispatch.py` (pontos de integração no loop de despacho)
  - `hermes_cli/config_defaults.py` (defaults declarados: `rust_selector: off`, `rust_claim: off`)

---

## 3. Plano de Deploy Gradual (Shadow -> Canary -> Rust)

O plano de deploy é dividido em 3 fases progressivas, todas auditadas contra testes em banco temporário.

### Fase 1: Pré-voo (Preflight Verification)
Executar script de validação:
```bash
./scripts/kanban_rust_deploy_transition.sh preflight
```
O teste valida subcomandos do binário e roda a suite de alta concorrência em `/tmp/kanban_persist_test_*.db`.

### Fase 2: Shadow Mode (Observabilidade sem Risco)
No modo `shadow`, o Rust executa a seleção de candidatos em paralelo com o Python, mas os resultados do Python continuam sendo os únicos despachados. Divergências na lista ordenada geram alertas em log (`Kanban selector shadow mismatch`).
- **Ativação via Config (`~/.haos/config.yaml`):**
  ```yaml
  kanban:
    rust_selector: shadow
    rust_claim: off
  ```
- **Ativação via Env:**
  ```bash
  export HERMES_KANBAN_RUST_SELECTOR=shadow
  export HERMES_KANBAN_RUST_CLAIM=off
  ```

### Fase 3: Canary Mode (Selector Ativo, Claim em Python)
O Rust seleciona os candidatos, mas a escrita e o claim atômico continuam sob controle do Python.
- **Ativação via Config:**
  ```yaml
  kanban:
    rust_selector: rust
    rust_claim: off
  ```
- **Ativação via Env:**
  ```bash
  export HERMES_KANBAN_RUST_SELECTOR=rust
  export HERMES_KANBAN_RUST_CLAIM=off
  ```

### Fase 4: Full Rust Mode (Selector + Atomic Claim & Lease Fencing)
Ativação total da seleção e dos claims atômicos em Rust.
- **Ativação via Config:**
  ```yaml
  kanban:
    rust_selector: rust
    rust_claim: rust
  ```
- **Ativação via Env:**
  ```bash
  export HERMES_KANBAN_RUST_SELECTOR=rust
  export HERMES_KANBAN_RUST_CLAIM=rust
  ```

---

## 4. Procedimento de Rollback Determinístico

O rollback é **imediato, sem perda de dados e 100% interoperável**. Se houver qualquer instabilidade:

### A. Desativação por Variáveis de Ambiente
```bash
export HERMES_KANBAN_RUST_SELECTOR=off
export HERMES_KANBAN_RUST_CLAIM=off
```

### B. Desativação por Arquivo de Configuração
No arquivo `~/.haos/config.yaml`:
```yaml
kanban:
  rust_selector: off
  rust_claim: off
```

### C. Se configurado via systemd drop-in
Se overrides estiverem configurados em `/etc/systemd/system/haos-gateway.service.d/override.conf`:
1. Remover ou comentar as variáveis `HERMES_KANBAN_RUST_*`.
2. Executar:
   ```bash
   systemctl daemon-reload
   systemctl restart haos-gateway
   ```

### Garantia de Consistência no Rollback:
- Tarefas que foram iniciadas pelo Rust mantêm seu `claim_lock` e `current_run_id` no SQLite.
- O despachante Python reconhece essas tarefas como ativas normalmente.
- Tarefas concluídas via Python (`complete_task`) fecham o ciclo perfeitamente, registrando eventos de conclusão e liberando leases.

---

## 5. Matriz de Evidências de Teste em SQLite Temporário

Todos os testes foram executados exclusivamente contra diretórios temporários `/tmp/kanban_persist_test_*.db`, preservando o banco de produção `/root/.haos/kanban.db` intacto.

| Caso de Teste | Descrição do Cenário | Resultado Observado |
|---|---|---|
| `test_concurrent_claims_cas_fencing` | 10 threads simultâneas disputando uma única tarefa via `haos-edge kanban-claim`. | **PASSOU** (Exatamente 1 vencedor; 1 run criado; 1 evento `claimed`). |
| `test_high_concurrency_multi_task_cas_fencing` | 50 threads concorrentes disputando 5 tarefas distintas simultaneamente. | **PASSOU** (Exatamente 5 vencedores distintos; 5 runs; zero colisões). |
| `test_timeout_fallback_and_adoption` | Injeção de timeout antes da escrita (fallback Python) e timeout pós-escrita (adoção sem duplicação de run). | **PASSOU** (Fallback suave e idempotente). |
| `test_rollback_to_python_only` | Troca a quente de `rust` para `off`, validação de despacho puro em Python e conclusão de tarefas criadas pelo Rust. | **PASSOU** (Interoperabilidade 100% comprovada). |

**Comando de Execução:**
```bash
pytest tests/hermes_cli/test_kanban_rust_audit_suite.py -v
```
Resultado: **4 passed in 3.71s** (0 falhas, 0 warnings de concorrência).
