---
id: ADR-009
titulo: "Espaço de ADR do repo: três namespaces e a autoridade única"
status: "Aceito"
data: 2026-09-15
decidido_por: "human:adriano"
autor: "adriano"
contexto: "espaço de ADR do repositório HERMES-TURBO e separação por namespaces"
causado_by:
  - "adr:ADR-006"
  - "adr:ADR-008"
  - "necessidade de esclarecer os três namespaces de ADR no repo após imprecisão em ADR-006 §4"
evidence:
  - "repo mantinha namespaces distintos: ADR-NNN em docs/architecture/, GOV-NNN em architecture/ADRs/ e GUILD-NNN"
affects:
  - "docs/architecture/*"
  - "architecture/ADRs/*"
supersedes:
  - "ADR-006§4"
superseded_by: null
prov:
  wasGeneratedBy: "task:governanca-namespaces-adr-repo"
  wasAssociatedWith: "agent:haos-cachyos-x8664"
  wasDerivedFrom: "adr:ADR-006"
  causado_by: "correção da descrição dos namespaces no repositório HERMES-TURBO"
  affects:
    - "docs/architecture/*"
    - "architecture/ADRs/*"
  supersedes:
    - "ADR-006§4"
  superseded_by: null
---

# ADR-009 — Espaço de ADR do repo: três namespaces e a autoridade única

**Status:** Aceito (registro de mudança medida, 2026-09-15)
**Data:** 2026-09-15
**ADR relacionadas:** ADR-006 (numeração de ADR e resolução por ID — a §4 fica superseded por este), ADR-008 (REGISTRY: estado, log, índice no grafo e espelho no vault)
**Escopo:** o espaço de ADR do **repositório** (`HERMES-TURBO`). A numeração do **vault** não é tocada por este ADR.

## Contexto (medido 2026-09-15)

O ADR-006 §4 descrevia o espaço de ADR do repo assim:

> "o repo mantém coleção própria e independente em `architecture/ADRs/` (= `docs/architecture/`),
> onde `ADR-008` = Memory Fabric e já é referenciado pelo código vivo"

Essa descrição **deixou de ser verdadeira**. O repo resolveu a colisão de numeração por
**namespace**, não elegendo uma série vencedora:

| Namespace | Local | Série | Docs |
|---|---|---|---|
| `ADR-NNN` | `docs/architecture/` | plataforma (SOTA → freeze → fases 2–7) | 7 |
| `GOV-NNN` | `architecture/ADRs/` | governança | 19 |
| `GUILD-NNN` | `architecture/ADRs/parallel/guild/` | Guild, não-canônica | 12 |

Consequências diretas do que a §4 afirmava:

1. `architecture/ADRs/` e `docs/architecture/` **não são mais o mesmo espaço** — são dois namespaces.
2. Memory Fabric no repo é **`GOV-008`**, não `ADR-008`.
3. `ADR-008` **no vault** continua sendo `adrs/ADR-008-registry-estado-log-indice-espelho.md`. O mesmo
   número significa documentos diferentes nos dois espaços — o que a §4 já dizia, e segue verdadeiro.

## Decisão

1. **A autoridade sobre o mapeamento do repo é `architecture/ADRs/INDEX.md`.** Este ADR **não** copia
   a tabela de IDs. Copiar criaria uma segunda cópia que envelhece — exatamente o defeito que a §4 do
   ADR-006 sofreu. O índice do repo é a fonte; aqui fica o ponteiro.
2. **A §4 do ADR-006 está superseded** quanto à descrição do espaço do repo. O corpo do ADR-006 **não
   é editado** — ADR aceita é imutável, regra do próprio ADR-006 §1. Esta é a nota posterior que o
   padrão das "Notas de contexto" do ADR-006 já usava para o ADR-007.
3. **A numeração do vault não muda.** O `ADR-008` do vault segue sem conflito e o próximo número livre
   do vault continua sendo `ADR-009` — este documento.
4. **`obsidian_get_adr` não é afetado.** Ele resolve por varredura de `adrs/`, comparando o prefixo
   `ADR` (`hermes/platform/memory/obsidian.py:96-102`), e o vault é o único diretório que ele varre.
   Nada do repo entra nessa resolução.

## Verificação (2026-09-15)

- 31 renomeações no repo detectadas pelo git; conteúdo conferido corpo a corpo: os únicos deltas são
  título, linha `Canonical ID` e linha de série. **Zero perda de conteúdo** nos 38 documentos.
- `tests/architecture/` + `tests/platform/` = **816 passed, 0 failed**.
- Nada no runtime lê `architecture/ADRs/`: grep em `hermes/`, `tools/`, `scripts/` = vazio.
- Índice (`memory/ragflow.db`): 20 doc_paths, **0 fantasmas**, `integrity_check=ok`. O rename não cria
  fantasma porque o índice cobre apenas o vault.

## Consequência para o recall

O espaço de ADR do repo era **ponto cego de recall**: 34 arquivos `.md` de decisão arquitetural em
nenhum corpus — `docs.db` cobre `/root/hermes-webui/docs`, não o `docs/` do repo, e
`architecture/` não era alvo de nenhum. É o mesmo defeito que o ADR-008 descreveu para o REGISTRY e o
ADR-004 para o OKF. Passa a ser coberto por um corpus `adr` explícito no `refresh.sh`.
