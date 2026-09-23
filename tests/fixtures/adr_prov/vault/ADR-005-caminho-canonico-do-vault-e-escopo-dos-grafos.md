---
id: ADR-005
titulo: "Caminho canônico do vault e escopo dos dois grafos"
status: "Aceito"
data: 2026-09-13
decidido_por: "human:adriano"
autor: "adriano"
contexto: "auditoria da stack de memória e divergências entre documentação e runtime"
causado_by:
  - "adr:ADR-001"
  - "adr:ADR-002"
  - "adr:ADR-003"
  - "adr:ADR-004"
  - "divergência entre caminho documentado ($HERMES_HOME/vault) e real (/root/.haos/obsidian_vault) e sobreposição de grafos"
evidence:
  - "/root/.haos/vault inexistente vs /root/.haos/obsidian_vault com notas reais"
  - "graphrag.db ativo com 18 entidades sendo reconstruído de hora em hora"
  - "eval graphrag-vault-adr mostrando sobreposição de indexação no sdb"
affects:
  - "obsidian_vault/*"
  - "okf/contratos/contrato-memoria-haos-nos-cachyos.md"
  - "memory/graphrag.db"
  - "haos_memory_populate.py"
supersedes: []
superseded_by: null
prov:
  wasGeneratedBy: "task:auditoria-stack-memoria-20260913"
  wasAssociatedWith: "agent:haos-cachyos-x8664"
  wasDerivedFrom: "adr:ADR-001"
  causado_by: "divergências de documentação de caminhos e duplicidade de indexação de grafos"
  affects:
    - "obsidian_vault/*"
    - "okf/contratos/contrato-memoria-haos-nos-cachyos.md"
  supersedes: []
  superseded_by: null
---

# ADR-005 — Caminho canônico do vault e escopo dos dois grafos

**Status:** Aceito (decisão de engenharia registrada em auditoria do dono, 2026-09-13)
**Data:** 2026-09-13
**ADR relacionadas:** ADR-001 (stack de memória), ADR-002 (re-ranker), ADR-003 (graph-seed), ADR-004 (dream)

## Contexto (auditoria de 2026-09-13)

A auditoria da stack de memória encontrou duas divergências entre documentação e runtime:

1. **Caminho do vault.** ADR-001 e o contrato `okf/contratos/contrato-memoria-haos-nos-cachyos.md`
   apontam o vault para `$HERMES_HOME/vault` (`/root/.haos/vault`). **Esse diretório não existe** —
   o vault real é `/root/.haos/obsidian_vault` (11 notas: `adrs/` 4, `diario/` 3, `curadoria/` 2,
   `pesquisas/` 2). A tool `obsidian_save_note` sempre gravou em `obsidian_vault/`; só a
   documentação ficou para trás.
2. **Grafo built-in.** ADR-001 e o contrato afirmam que `$HERMES_HOME/memory/graphrag.db` está
   DESATIVADO e que "o grafo built-in do HAOS NÃO é usado". Medição de 2026-09-13: o store existe
   com **18 entidades / 16 relações / 3 comunidades**, é reescrito **de hora em hora**
   (`haos-memory-refresh.timer` → `haos_memory_populate.py`, etapa `graphrag`) a partir das notas do
   vault, e é servido pela tool `graphrag_query`. Não é dado morto: é um grafo vivo de escopo pequeno.

Fato adicional: o mesmo conteúdo do vault/ADRs também é indexado no grafo operacional do sdb
(`hermes/graphrag-lite`, 14.733 entidades / 26.624 relações / 2.929 chunks), verificado pelo eval
`graphrag-vault-adr` (ADR-001 com 31 entidades ligadas). Há sobreposição real de corpus — o que o
contrato proíbe como "NUNCA duplicar indexação".

## Decisão

1. **Caminho canônico do vault = `$HERMES_HOME/obsidian_vault`.** A referência a
   `$HERMES_HOME/vault` em ADR-001 fica registrada aqui como incorreta (ADR é imutável: a correção
   vive nesta ADR). O contrato OKF é atualizado para o caminho real.
2. **Dois grafos, com escopos explícitos e disjuntos por finalidade** (ratifica o runtime, em vez de
   desligar componente que funciona):
   - **Grafo built-in** (`$HERMES_HOME/memory/graphrag.db`, ~40 KB): grafo **das notas canônicas do
     vault**, mantido pelo refresh horário, consumido pela tool local `graphrag_query`. Custo
     desprezível; dá recall local imediato sobre o vault sem depender do sdb montado.
   - **Grafo operacional** (sdb `hermes/graphrag-lite/graph.db`, ~24 MB): corpus de skills + docs +
     OKF + ADRs, refresh */30, consumido por `mcp__graphrag__recall` (com re-ranker, ADR-002).
   - A sobreposição fica **limitada às notas do vault/ADRs**: o built-in é o espelho local do vault,
     o operacional é o corpus de trabalho. A regra "nunca duplicar indexação" passa a ser lida como
     **"nunca indexar o MESMO corpus com a MESMA finalidade em dois lugares"** — não como proibição
     de um índice local do vault.
3. **Correção de rumo se o custo crescer:** se o built-in passar a indexar corpus além do vault
   (skills/docs), ele deve ser desligado — aí sim haveria duplicação sem finalidade própria.

## Consequências

- Consulta ao vault: `graphrag_query` (built-in, local, determinístico) para as notas canônicas;
  `mcp__graphrag__recall` para o corpus operacional.
- O contrato OKF deixa de mentir sobre o caminho do vault e sobre o grafo built-in.
- Auditoria futura: divergência entre ADR/contrato e runtime vira ADR nova (não edição retroativa).
