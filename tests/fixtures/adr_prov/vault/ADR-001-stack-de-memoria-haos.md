---
id: ADR-001
titulo: "Stack de memória do HAOS no nó cachyos-x8664"
status: "Aceito"
data: 2026-09-10
decidido_por: "human:adriano"
autor: "adriano"
contexto: "ativação das camadas de memória canônica após migração HAOS"
causado_by:
  - "migração HAOS (10/09, home vivo = /root/.haos) com ferramentas built-in de memória ociosas"
evidence:
  - "/root/.haos/vault e /root/.haos/okf não existiam"
  - "grafo operacional real residia no MCP sdb (hermes/graphrag-lite/graph.db)"
affects:
  - "memories/MEMORY.md"
  - "memories/USER.md"
  - "state.db"
  - "obsidian_vault/adrs/*"
  - "okf/*"
supersedes: []
superseded_by: null
prov:
  wasGeneratedBy: "task:migracao-haos-stack-memoria"
  wasAssociatedWith: "agent:haos-cachyos-x8664"
  wasDerivedFrom: "system:migracao-haos-20260910"
  causado_by: "migração HAOS com ferramentas built-in de memória ociosas"
  affects:
    - "obsidian_vault/adrs/*"
    - "okf/*"
  supersedes: []
  superseded_by: null
---

# ADR-001 — Stack de memória do HAOS no nó cachyos-x8664

**Status:** Aceito (dono ratificado via Telegram, 2026-09-10)
**Data:** 2026-09-10

## Contexto

O nó passou pela migração HAOS (10/09, home vivo = `/root/.haos`) e as ferramentas
built-in de memória (`obsidian_*`, `haos_okf_*`, `graphrag_query`) estavam registradas
porém ociosas: `/root/.haos/vault` e `/root/.haos/okf` nunca tinham sido criados, e o
grafo built-in (`/root/.haos/memory/graphrag.db`) nunca foi populado. O grafo operacional
real era/é o do MCP `graphrag` no sdb (`hermes/graphrag-lite/graph.db`, refresh */30).

## Decisão

1. **Camadas de memória ativas** (não mudar): `memories/` (MEMORY.md/USER.md, injetadas
   por turno), `state.db` (sessões/mensagens + contexto via context_expand/session_search),
   **GraphRAG via MCP sdb** (recall relacional), skills como memória procedural.
2. **Obsidian Vault** ativado neste nó: `$HERMES_HOME/vault` (`/root/.haos/vault`), pasta
   `adrs/` para ADRs canônicos. Convenção: `ADR-###-slug`.
3. **OKF** ativado neste nó: `$HERMES_HOME/okf` (`/root/.haos/okf`), para contratos e
   specs canônicos (doc_type: metric/api-contract/runbook/architecture).
4. **GraphRAG built-in** (`/root/.haos/memory/graphrag.db`): permanece DESATIVADO por
   falta de dados — o caminho canônico de recall é o MCP sdb. Não duplicar indexação.
5. ADR é imutável depois de aceita; correções viram ADR novo.

## Consequências

- Vault e OKF passam a ser consultados por `obsidian_get_adr`, `obsidian_save_note`,
  `haos_hybrid_memory_query` e `haos_okf_save_document`.
- Sempre salvar decisões de infra/arquitetura como ADR aqui antes de "esquecer" no chat.
