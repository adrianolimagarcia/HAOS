# MATRIZ GO / NO-GO DE ESTABILIZAÇÃO HAOS — 08/10/2026

## 1. Resumo Executivo
Esta matriz consolida as evidências técnicas, testes verificados, SHAs e status de prontidão para fechamento de ciclo e transição entre ambientes. Nenhuma alteração foi aplicada em produção ou na branch `main`.

---

## 2. Matriz de Decisão por Frente

| Frente | Commit / SHA | Testes & Verificação | Status Técnico | Decisão de Deploy | Depende de Aprovação Humana? |
|---|---|---|---|:---:|:---:|
| **WebUI (Bedrock & IMDS)** | `68bbad3c` (isolado em `/tmp/hermes-webui-subagent-audit`) | 53 passed, 1 skipped (0 falhas) via pytest oficial | Suporte a ECS e IRSA validado com fast-fail defensivo. EC2 IMDS link-local mantido skipped para evitar stalls. | **NO-GO** (Deploy Retido) | **SIM** (Aprovação de promoção da branch isolada para `/root/hermes-webui`) |
| **WebUI (SSE & Cancelamento)** | `b6bad11f` (consolidado em branch limpa) | 59 passed em testes de socket cancel, preheader e timeout | Ausência de loops infinitos e terminação graciosa comprovadas. | **NO-GO** (Deploy Retido) | **SIM** (Aprovação de janela de manutenção/reinicialização do WebUI) |
| **Kanban Rust Runtime** | `bdb6f9fd29` | 19 passed (4 de auditoria de concorrência + 15 de regressão) | CAS fencing validado sob concorrência de 50 threads; fallback determinístico e rollback via flags comprovados. | **NO-GO** (Canary Retido) | **SIM** (Aprovação para início da fase `shadow`/`canary` via script de transição) |
| **Compressão Telegram** | `49654cf928` (cherry-pick no coordenador) | 8 passed (contrato determinístico de preservação head/tail/summary e lock defer) | Redução de volume comprovada; limites e tolerância a lock verificados sem perda de integridade. | **GO** (Código de Testes Integrado) | **NÃO** (Testes integrados na worktree isolada do coordenador) |
| **SQLite I/O & WAL** | `620b109f9f` (RCA documentado) | Auditoria passiva em `state.db` (WAL mode, busy_timeout 5s) | Nenhuma alteração mutável, VACUUM ou PRAGMA executado no banco vivo. | **GO** (Diagnóstico Concluído) | **NÃO** (Ação passiva sem necessidade de intervenção destrutiva) |

---

## 3. Detalhamento dos Itens Retidos (NO-GO) e Pré-Requisitos

### A. WebUI (/root/hermes-webui)
- **Motivo do NO-GO para Deploy:** O repositório de produção ativo possui arquivos locais sujos (`.venv`, `data/hermes.db`, logs). Qualquer `git checkout` ou substituição forçada violaria a estabilidade do serviço em execução.
- **Pendência Técnica de IMDS:** O teste para EC2 Instance Profile puro (`169.254.169.254`) requer mock hermético de token IMDSv2 no pipeline de CI para não depender de conectividade externa de rede.
- **Ação Humana Necessária:** Validar a promoção da branch `release/clean-consolidated-frente-a` + commit `68bbad3c` via backup prévio físico (`cp -a`).

### B. Kanban Rust Runtime
- **Motivo do NO-GO para Deploy:** O binário e o harness estão 100% verificados em laboratório (`/tmp/kanban_persist_test_*.db`), mas a política de engenharia exige aprovação explícita antes de ativar o runtime em produção.
- **Ação Humana Necessária:** Autorizar a execução de `scripts/kanban_rust_deploy_transition.sh shadow` para início de telemetria sem delegação de escrita.

---

## 4. Conformidade com Diretrizes Operacionais
- **Sudo:** Zero comandos executados com privilégios de superusuário.
- **Branch main:** Intocada (mantida na base limpa `240d12e30a`).
- **Branch MCP v2 (`feat/mcp-capability-registry-v2`):** Totalmente isolada e preservada para a sessão do coordenador MCP.
- **Banco de Dados Vivo:** Zero operações mutáveis ou corrupções em `/root/.haos/state.db` ou `kanban.db`.
