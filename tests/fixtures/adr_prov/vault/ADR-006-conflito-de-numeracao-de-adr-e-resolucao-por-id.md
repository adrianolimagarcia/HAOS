---
id: ADR-006
titulo: "Conflito de numeração de ADR e a resolução por ID"
status: "Aceito"
data: 2026-09-15
decidido_por: "human:adriano"
autor: "adriano"
contexto: "resolução de colisão de numeração de ADRs no vault canônico e protocolo de ID"
causado_by:
  - "adr:ADR-005"
  - "duas ADRs ocupavam o número 005 no vault canônico quebrando obsidian_get_adr('ADR-005')"
evidence:
  - "obsidian_get_adr('ADR-005') retornava ADR de PDF em vez de caminho canônico do vault"
  - "colisão entre ADR-005-caminho-canonico e ADR-005-extracao-de-pdf"
affects:
  - "obsidian_vault/adrs/ADR-005*"
  - "obsidian_vault/adrs/ADR-007*"
supersedes: []
superseded_by: null
prov:
  wasGeneratedBy: "task:resolucao-conflito-numeracao-adrs"
  wasAssociatedWith: "agent:haos-cachyos-x8664"
  wasDerivedFrom: "adr:ADR-005"
  causado_by: "colisão de ID no vault quebrando lookup determinístico de ADR"
  affects:
    - "obsidian_vault/adrs/*"
  supersedes: []
  superseded_by: null
---

# ADR-006 — Conflito de numeração de ADR e a resolução por ID

**Status:** Aceito (registro de defeito medido, 2026-09-15)
**Data:** 2026-09-15
**ADR relacionadas:** ADR-005 (caminho canônico do vault), ADR-007 (extração de PDF, renumerada de ADR-005)

## Contexto

Duas ADRs ocupavam o número 005 no vault canônico:

- `adrs/ADR-005-caminho-canonico-do-vault-e-escopo-dos-grafos.md`
- `adrs/ADR-005-extracao-de-pdf-pymupdf4llm.md` — **renumerada para `ADR-007-extracao-de-pdf-pymupdf4llm.md` em 2026-09-15**

A colisão não é cosmética: o protocolo de memória do HAOS consulta ADR **por ID**, não por
caminho. `SOUL.md` instrui "ADR: `obsidian_get_adr`"; a skill `haos-memory-stack` lista
`obsidian_get_adr("ADR-###")` como a ferramenta determinística para decisão de arquitetura; e a
regra de pré-infra manda consultar a ADR antes de mexer no código.

## Consequência medida (2026-09-15)

Chamada real da tool:

```
obsidian_get_adr("ADR-005")
-> path = adrs/ADR-005-extracao-de-pdf-pymupdf4llm.md      (medicao ANTES da renumeracao)
```

Após a renumeração de 2026-09-15, a mesma chamada devolve
`adrs/ADR-005-caminho-canonico-do-vault-e-escopo-dos-grafos.md` — que é o que as 6 citações
existentes de "ADR-005" sempre quiseram.

A consulta devolve a ADR de **extração de PDF**, não a de **escopo do vault**. Quem pede a decisão
sobre o caminho canônico do vault recebe um documento sobre `pymupdf4llm`.

O modo de falha é o pior possível: a resposta é **bem-sucedida** (`found: true`, conteúdo
preenchido) e não sinaliza ambiguidade. Não há erro para o operador perceber — só a decisão errada
citada com aparência de fundamento.

Mecanismo: `obsidian_get_adr` resolve por varredura de `adrs/` e devolve o primeiro casamento do
prefixo. Com dois arquivos no mesmo prefixo, o vencedor é a ordem de diretório, não a intenção.

Contraste: `ADR-002` e `ADR-003` resolvem corretamente (prefixos únicos).

## Decisão

1. Este registro documenta o conflito. O conteúdo das ADRs existentes **não foi reescrito** — ADR
   aceita é imutável (ADR-005); corrigir história não é o caminho.
2. A colisão foi resolvida por **renumeração do identificador**, não por edição de conteúdo: o
   arquivo passou a `ADR-007-extracao-de-pdf-pymupdf4llm.md`, com a decisão e o texto originais
   intactos e uma linha `**Renumerada:**` registrando a mudança no próprio documento.
3. Com a colisão desfeita, `"ADR-005"` volta a ser inequívoco: resolve para a ADR de caminho
   canônico do vault — o que as 6 citações existentes sempre quiseram.
4. O próximo número livre passa a ser **ADR-008** — **no escopo do vault**. Este número pertence ao espaço de ADR do vault; o repo mantém coleção própria e independente em `architecture/ADRs/` (= `docs/architecture/`), onde **`ADR-008` = Memory Fabric** e já é referenciado pelo código vivo (`graphrag_store.py`, `haos_memory_populate.py`, `haos_memory_tools.py`). O mesmo número significa documentos diferentes nos dois espaços.

## Renumeração executada (2026-09-15)

**Renomeado:** `ADR-005-extracao-de-pdf-pymupdf4llm.md` → `ADR-007-extracao-de-pdf-pymupdf4llm.md`,
por decisão do dono.

Verificação após a renomeação:

```
obsidian_get_adr("ADR-005")  -> adrs/ADR-005-caminho-canonico-do-vault-e-escopo-dos-grafos.md
obsidian_get_adr("ADR-007")  -> adrs/ADR-007-extracao-de-pdf-pymupdf4llm.md
```

Varredura de citações por caminho **antes** de renomear: apenas este ADR-006 e o `REGISTRY`
(ambos atualizados no mesmo ato). Nenhum contrato OKF e nenhuma skill citavam a ADR de PDF por
caminho — o raio real era 2 arquivos, não o que se temia.

Notas de contexto:

- O vault **não** é repositório git; a renomeação foi feita por `mv`, com backup prévio do arquivo,
  deste ADR-006 e do `REGISTRY`.
- Os índices (`graphrag.db`, `ragflow.db`, `state.db`) ainda podem carregar o caminho antigo até o
  próximo refresh; são derivados, não fonte.
- A ADR de PDF cita `mcp_graphrag_recall` (nome de tool inexistente; ver correção datada no
  REGISTRY de 2026-09-15). Como ADR é imutável, a correção **não** foi feita no corpo dela — fica
  registrada aqui e no REGISTRY.

## Esquema de numeração (decisão do dono, 2026-09-15)

Mantidos **3 dígitos zero-padded**; alargar apenas quando o número chegar a 999.

Motivo medido: **não existe limite de 3 dígitos**. `get_adr` extrai o ID por
`p.stem.split("-", 2)` e compara string (`hermes/platform/memory/obsidian.py:96-102`), sem regex,
validador ou cast numérico — `ADR-1000`, `ADR-10000` e até `ADR-0A0A000A` já resolvem hoje. O único
`zfill(3)` do repo está num teste.

O único efeito real de passar de 999 seria ordem lexicográfica (`ADR-1000` antes de `ADR-999`), e
**nada no runtime ordena ou lista ADRs**. Proposta de esquema alfanumérico de largura maior foi
avaliada e recusada por ser over-engineering: custo de legibilidade permanente para todos, contra
um problema que chega em ~993 ADRs no ritmo atual (7 em uso).
