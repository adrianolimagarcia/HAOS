# HAOS — Divergence & Merge Plan (upstream `main`)

## 1. Contexto da divergência

`haos-fork` nasce de `origin/main` no merge-base `9dd6634` e acumula **85
commits** (a partir de `d4a2a1997` "HAOS fork: commit inicial"). A divergência é
**deliberada e aditiva**: todo o novo código vive sob `hermes/platform/`
(PEP-420, stdlib-only) e camadas de borda, sem modificar o kernel narrow-waist
(`run_agent.py`, `agent/turn_*.py`, `gateway/run*.py`, `tools/`, `model_tools.py`).

**Números verificados (2026-09-07):**
- merge-base: `9dd6634c5635321cf38840cc30e9b51226689128`
- `haos-fork` à frente de `origin/main`: **85 commits**
- `haos-fork` atrás de `origin/main` (upstream avançou): **256 commits**
- diff scoped HAOS (`origin/main...haos-fork`, paths
  `hermes/platform docs/architecture docs/haos plugins/haos
  hermes_cli/haos_cmd.py tests/platform tests/hermes_cli/test_haos_cmd.py`):
  **226 arquivos, +49.255 linhas, 0 deleções** (puramente aditivo)
- diff total da branch: **291 arquivos, +56.118 / −321**

## 2. Invariantes que a plataforma preserva (para os reviewers)

| Invariante | Como HAOS honra |
|---|---|
| Prompt cache sagrado | Sem mutação mid-conversação; seams cache-aware (skills/memória entram por hooks de sessão, não no system prompt vivo) |
| Narrow waist | `hermes/platform/` é namespace separado; nada no toolset core |
| Contribuição por camadas | 8 commits-lógicos autocontidos, revisáveis em sequência |
| E2E real | Testes com imports reais, `HERMES_HOME` temporário, fake peers scriptados |
| `.env` = segredos | Zero novas env vars de config; gates em config.yaml/CTOR |

## 3. Estratégia de merge recomendada

### Opção A — Merge incremental por camada (recomendada)
1. `git fetch origin main` e rebase/merge contínuo de `origin/main` em
   `haos-fork` para reduzir o drift (256 commits) a cada camada.
2. Submeter o PR com base atualizada; revisar em sequência as camadas:
   1. Scaffold + Memory/Skills/Capabilities (triad)
   2. Model/Provider Fabric (failover exato)
   3. Protocol Fabric + Multimodal Auto-Spawn
   4. Workspace Fabric (Lane Kilo) + AutoMerge
   5. Ouroboros + Observability/Evals
   6. Federated Mesh + Control Plane + CLI
   7. Dashboard UI + plugins/haos
   8. Docs canônicos (ADR-001, HAOS_SYSTEM_SPEC, audits)
3. **Nunca squash-merge** de uma branch 256 commits atrás sem rebase prévio
   (`git reset --hard origin/main` + re-aplicar commits) — risco de reverter
   fixes recentes do upstream (regra do repo).

### Opção B — PR de visibilidade sem merge imediato
Manter `haos-fork` como branch de referência e abrir PR "documentação +
proposta de fusão" apontando o `DIFF-MANIFEST.md` e o `PR-UPSTREAM-HAOS.md`,
sem exigir merge na primeira rodada.

## 4. Fases seguintes (runtime slice — config-gated, nunca core)

Integrações que conectam a plataforma ao runtime real, todas aditivas e
cache-aware, priorizadas pelos seams auditados em `docs/haos/audits/seams/`:

1. **Lane spawn real** — `HermesCliLaneWorker` spawna o kernel
   (`--cli … chat -q` + `.haos/result.json`); ✅ já implementado em
   `hermes/platform/execution/lane_executor.py` + `HAOS_HERMES_PROFILE` (L4).
2. **MCP fabric real** — wrapper fino `capabilities/mcp/fabric.py` delegando a
   `tools.mcp_tool_discovery` (`allowed_mcp_names`, `register_mcp_servers`,
   `get_mcp_status`) — ✅ já implementado + prova de import stdlib-only.
3. **Auth vault real** — wrapper de `auth.json` upstream
   (`write/read_credential_pool`, `resolve()` refresh-aware,
   `redact_sensitive_text`) — 🟠 pendente (mocks in-memory hoje).
4. **Runtime hooks** — sessão start/end → Memory Fabric e consolidação
   GraphRAG; promoção de skills via Ouroboros; tudo atrás de config gate.

## 5. Riscos e mitigações

| Risco | Mitigação |
|---|---|
| Drift 256 commits | Rebase incremental; testar após cada merge de upstream |
| Custo de review do diff | Revisão em camadas; commits autocontidos; 0 deleções HAOS |
| Divergência de estilo | Plataforma segue as regras de AGENTS.md (run_tests.sh, sem change-detector, E2E) |
| Dependências externas | `hermes/platform/` stdlib-only; extras opcionais (mcp/acp) nunca no nível de módulo |
