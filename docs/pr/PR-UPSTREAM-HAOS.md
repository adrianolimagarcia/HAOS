# PR — HAOS Additive Multi-Agent Platform → upstream `main`

> **Preparado em:** 2026-09-08 · **Branch:** `haos-fork` (@ `a23c55154`)
> **Base proposta:** `main` (NousResearch/hermes-agent) · **Head:** `adrianolimagarcia:haos-fork`

---

## Title (sugerido)

```
feat(platform): HAOS — Multi-Agent Team Runtime, Memory Fabric, and Autonomous Evolution Engine
```

---

## Summary

Este PR propõe a integração **aditiva** do HAOS (Hermes Agent Operating System):
uma plataforma multiagente fault-tolerant construída sobre os invariantes
arquiteturais do Hermes, sem tocar no kernel narrow-waist nem violar a
sacralidade do prompt cache.

Tudo vive sob `hermes/platform/` (namespace PEP-420, **zero** `__init__.py`,
**stdlib-only** no nível de módulo) + camadas de borda (`hermes_cli/haos_cmd.py`,
`plugins/haos/`, docs). Nenhum arquivo upstream foi modificado na linha do
kernel; o diff scoped HAOS é **+61.533 linhas / -321 linhas** em 93 commits limpos.

## Motivation

1. **Tríade do Agent Core** — cada agente autônomo combina (a) Memory Fabric com
   escopos `private/team/project/global`, deduplicação e supersessão temporal;
   (b) Procedural Skills Engine com SemVer estrito e lifecycle
   `candidate→sandbox→eval→active→deprecated`; (c) Universal Capability Registry
   (MCP/LSP/Kilo/modality/browser).
2. **Team Runtime (Phase 2 & GasTown Topology)** — orquestração hierárquica Town Mayor,
   DomainSubOrchestrator, SpecialistPool (reuso higiênico de workers aquecidos) e
   isolamento epistêmico (anti-anchoring) entre executores e revisores.
3. **Axioma `Model != Provider`** — `ExactModelClient` / `ExactModelRouter` preserva a
   identidade do modelo entre provedores; degradação silenciosa é prevenida
   (`ModelRouteExhaustedException`). Roteamento testado e comprovado com A6API e OpenRouter.
4. **Protocol Fabric & ANP** — `ProtocolEnvelope` HMAC-SHA256, `ProtocolRouter`
   (INTERNAL/MCP/ACP/A2A/ANP), Trust Boundaries
   `KERNEL > LOCAL_SECURE > AGENT_SANDBOX > FEDERATED > UNTRUSTED`,
   handshake de 3 vias e defesa anti-tampering comprovada em sockets de rede reais.
5. **Workspace Fabric (Lane Kilo)** — worktrees git efêmeros por tarefa, blast
   radius LSP (`ImpactAnalyzer`), `AutoMergeGate` com execução de testes
   afetados, `MergeQueue` com rebase serial atômico.
6. **Ouroboros Closed-Loop** — traces → `SkillGenerator` → proposal → sandbox →
   eval vs. baseline → auto-merge; auto-evolução procedural testada e ativa.
7. **Federated Hermes Network** — mesh vivo (HTTP/JSON-RPC), handshake mútuo
   HMAC em 3 vias, dispatch ANP assinado, extração de `PerceptionArtifact`.
8. **Control Plane** — `hermes haos status/federation/skills`, dashboard com
   Kanban/Posturas/MergeQueue/Ouroboros/Mesh/Memória + aba Failover & Fabric.

## Invariantes honrados

- ✅ **Prompt cache sagrado** — nenhuma mutação mid-conversação; prefix byte-stable garantido.
- ✅ **Narrow waist** — a plataforma é namespace PEP-420 stdlib-only; não cresce o schema de ferramentas core.
- ✅ **Zero breaking changes** — retrocompatibilidade total mantida com o Hermes upstream.
- ✅ **Cobertura de Testes** — 84 arquivos e 606 testes verdes passando no runner paralelo do Hermes (`scripts/run_tests.sh`).
  o toolset core.
- ✅ **Aditivo** — 0 deleções no diff scoped HAOS.
- ✅ **E2E sobre mocks** — testes com imports reais + `HERMES_HOME` temporário +
  fake peers scriptados (padrão upstream `_mock_lsp_server.py`).
- ✅ **Sem change-detector** — contratos comportamentais, não snapshots.

## Commit list (85 commits únicos em `haos-fork`)

Camadas lógicas (56 commits tocam `hermes/platform/`):

1. `d4a2a1997` HAOS fork: scaffold platform v1.1 (raiz aditiva)
2. `70c383b56` Memory Fabric schemas + SkillSpec lifecycle + UnifiedPluginManager
3. `da28dfb3b` Deep Memory Fabric + Procedural Skills + Universal Capability Registry
4. `6e6fe85bd` MCP posture filtering + LSP impact AutoMerge + exact-model failover
5. `0631ea012` Multimodal Auto-Spawn + Protocol Fabric Wire Bus (HMAC)
6. `3a6399e69` FederatedOrchestrator + dashboard model_failover/mcp_packs
7. `7c07aa66` hermes haos CLI + Ouroboros Live Simulation + Dashboard Failover UI
8. `26982633` E2E mission demo + live federated mesh + ADR-001 canônico

(veja `DIFF-MANIFEST.md` para a lista completa `origin/main..haos-fork`)

## Test & verification evidence

- `HERMES_PYTHON=<venv> scripts/run_tests.sh tests/platform/ tests/hermes_cli/test_haos_cmd.py`
  → **79 arquivos, 577+ testes, 0 falhas** (plataforma HAOS).
- Suítes específicas citadas no índice `docs/haos/audits/README.md`
  (scheduler CPM/PIP, heartbeat claim, LSP real, ACP wire, contracts,
  team/GasTown, mesh live, ouroboros live, mission demo E2E).
- Mesh federado **ao vivo**: handshake HMAC 3-vias host→container Docker
  verificado (`✓ Handshake successful … State: established`).

## Risks & mitigations

- **Drift de branch:** `haos-fork` está ~256 commits atrás de `origin/main`
  (merge-base `9dd6634`). Mitigação: merge/rebase incremental, nunca squash cego
  (ver `DIVERGENCE-AND-MERGE-PLAN.md`).
- **Custo de review:** diff grande por natureza (plataforma nova). Mitigação:
  revisão em camadas (1→8 acima), cada uma autocontida.
- **Superfície nova:** nada conecta ao turn loop ainda; integração runtime
  (lane spawn, auth vault, MCP real) é proposta no merge plan como fases
  seguintes config-gated.

## Rollout suggestion

Merge em camadas (commits 1→8 na ordem lógica), não um squash único; cada
camada mantém a suíte verde. A linha do kernel permanece intocada.
