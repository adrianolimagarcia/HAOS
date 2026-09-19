# MEMORY FABRIC — o que é cada peça

Referência operacional do sistema de memória do appliance. Descreve o que existe, onde
grava, como saber se está funcionando e o que ainda não está resolvido.

Complementa — não substitui — `docs/architecture/ADR-001-HAOS-MULTIAGENT-SOTA.md` §3.1.1,
que registra a decisão arquitetural. Aqui fica o estado verificável do código.

Código: `hermes/platform/context/memory/`. Provider ativo: `hermes_fabric`
(`memory.provider` em `/root/.haos/config.yaml`).

---

## 1. As duas camadas

```
vault Obsidian  ──migrate──▶  journal canônico  ──outbox──▶  4 projeções
(fonte humana)                (fabric.db)                     (derivadas)
```

- **Journal canônico** — `HAOS_HOME/memory/fabric.db`. É a verdade que o recall lê. Guarda
  registros com escopo, kind, hash de conteúdo, cadeia de supersessão e um outbox.
- **Projeções** — quatro sinks derivados do journal. Toda escrita no journal enfileira um
  evento por projeção; o `ProjectionRunner` drena e cada projector grava no seu sink.

**Direção da verdade:** o vault é a fonte humana; o journal é derivado dele pela migração; as
projeções são derivadas do journal. Nada escreve de volta no vault a partir do journal, exceto
a projeção `obsidian` — que grava em subdiretórios próprios e é marcada para não ser
reimportada (ver §2 e §6).

---

## 2. As quatro projeções — não são duplicação

`PROJECTIONS = ("obsidian", "decisions", "graphrag", "embeddings")`
(`canonical_store.py`). Cada uma é um destino diferente com um papel diferente. Nenhuma é
cópia de outra.

| Projeção | Papel | Onde grava | Filtro | Como saber que funciona |
|---|---|---|---|---|
| `embeddings` | índice vetorial para busca híbrida | `HAOS_HOME/memory/vectors.db` (`memory_vectors`) | todas | `SELECT COUNT(*) FROM memory_vectors` ≥ registros ativos |
| `decisions` | ledger cronológico de decisões | `HAOS_HOME/memory/decisions.db` (`memory_decisions`) | **só `_is_decision`** | contagem ≥ nº de registros com `kind="decision"` |
| `graphrag` | grafo de entidades/relações | `HAOS_HOME/memory/graphrag.db` (`entities`, `relations`, `communities`) | todas | `applied_events` cresce a cada evento |
| `obsidian` | nota Markdown auditável por humano | `<vault>/20-Architecture/<id>.md` (decisão) ou `<vault>/10-Memory/<scope>/<id>.md` | todas | arquivos `.md` com `fabric_committed: true` no frontmatter |

`decisions` é especializada por desenho: `_apply_decisions` retorna cedo se o registro não for
decisão. As outras três recebem tudo.

**Medição de 19/09/2026** (28 registros ativos): `embeddings` 28 · `decisions` 14 ·
`graphrag` 76 entidades / 104 relações / 144 `applied_events` · `obsidian` 40 notas.
As três primeiras gravavam em `HAOS_HOME/memory/`; a `obsidian` gravava no lugar errado — §5.

---

## 3. Os dois orçamentos de recall (não confundir)

O bloco de memória é anexado à **mensagem do usuário** em todo turno (nunca ao system prompt —
o prompt caching é inviolável). Tudo que passar do orçamento é pago em toda requisição.

| | `retrieve()` | `format_context()` |
|---|---|---|
| Papel | seleção de **candidatos** | **cap** do texto renderizado |
| Regra | `limit` hits; o melhor hit entra sempre, mesmo se maior que o budget | `len(saída) <= budget_chars`, sempre |
| Custo por registro | `min(len(content), budget // limit)` | truncado ao allowance |
| Piso | — | `_MIN_BLOCK_CHARS = 400` reservado por hit restante |

Duas armadilhas que já custaram caro:

1. **`retrieve` cobrava `len(content)` integral.** Um registro de 60k esgotava o budget e
   derrubava todos os menores depois dele — justamente os que caberiam. Um documento grande
   apagava o resto do recall. Agora cobra no máximo a cota justa de um hit.
2. **`format_context` não tinha cap nenhum.** Como `retrieve` admite o melhor hit de qualquer
   tamanho (de propósito: não deixar um registro grande e relevante invisível), o cap **tem**
   que ser aplicado aqui. Medido antes da correção: uma query devolveu **52.869 chars** com
   `budget_chars=5000`.

O piso de justiça existe porque um budget que devolve sempre um único documento não é um
budget de recall: cada hit ainda não renderizado reserva 400 chars antes de o atual receber o
seu allowance. Truncamento é marcado no texto (`[…truncado: N de M chars]`) — corte silencioso
deixaria o modelo ler um fragmento como se fosse o registro inteiro. Com allowance menor que o
próprio marcador, corte seco: um marcador que não cabe no budget que ele reporta o estoura.

`budget_chars <= 0` já é recusado em `retrieve` (retorna `[]`), então `format_context` devolve
string vazia sem precisar de guarda própria.

---

## 4. Teto de ingestão — documento não é memória

`MemoryMigrator.MAX_CONTENT_CHARS = 20_000`.

Derivado do que o recall consegue usar (`provider.prefetch` pede 5000 chars por turno) e do
degrau que o vault real mostra:

| Tamanho | O que é |
|---|---|
| 52.762 | espelho auto-gerado do REGISTRY (`curadoria/registry-espelho.md`) |
| 25.460 | artigo de pesquisa |
| 9.967 | ADR-008 — **registro legítimo** |
| ≤ 5.418 | 25 ADRs, diários e lições |

O teto cai no vão entre 25.460 e 9.967: exclui os dois documentos e não toca em nenhuma nota
autorada. Nota pulada **fica no vault** — o relatório diz quantas foram deixadas lá
(`skipped_oversized`). Ajustável por `--max-chars`.

Motivo de existir: um registro muito maior que o budget vence o rank de qualquer query e
empurra os registros que responderiam para fora do orçamento.

---

## 5. O path do vault — o CWD não é o home

`ObsidianAdapter` resolvia `vault_path or Path(".hermes/obsidian_vault")` — **relativo ao
diretório do processo**. A projeção `obsidian` escreve por esse adapter, então cada processo
gravava num vault diferente conforme de onde rodava:

- da árvore de desenvolvimento → `<dev-tree>/.hermes/obsidian_vault/` (40 notas, dentro do repo)
- de `/root` → `/root/.hermes/obsidian_vault/`
- **nunca** → `HAOS_HOME/obsidian_vault/` (o vault real, que ficou com zero)

Consequência: o guarda `skip_projected` (§6) nunca disparava contra o vault real — não havia
projeção lá para reconhecer. Agora o adapter resolve por `get_hermes_home()`, como
`provider.py` já fazia.

**Regra do projeto:** nunca hardcodar nem relativizar caminho de estado. `get_hermes_home()`
para código, `display_hermes_home()` para texto ao usuário.

---

## 6. Sync — o vault muda, o journal segue

`haos memory migrate` é **sync**, não importação de uma vez. A direção é vault → journal.

- Nota nova → `imported`
- Nota inalterada → `skipped_existing`
- **Nota editada → `updated`**: a revisão ativa é superseded e uma nova revisão entra, com o
  mesmo `logical_id`. O recall lê só registros ativos, então passa a responder o texto novo.
- Nota que é projeção do próprio fabric (`fabric_committed` no frontmatter) → `skipped_projected`
- Nota acima do teto → `skipped_oversized`

**Armadilha resolvida:** o id do registro deriva da URI (`"legacy:" + sha256(uri)[:24]`), não
do conteúdo. Comparar contra o que `store.get(id)` devolve compararia com a revisão *antiga*,
acharia diferença toda vez e acumularia uma supersessão por execução. Por isso a comparação é
contra `active_revision()`, que segue `superseded_by_map()` até a revisão viva.

**Armadilha resolvida:** `idempotency_key` **é** o `record_id` (`record_id = idempotency_key or
uuid4()`), não um deduplicador. Reusar a chave da nota na atualização colidiria com a revisão
sendo substituída — a atualização não passa `idempotency_key`.

O hash de conteúdo é `sha256(" ".join(content.split()).casefold())` — normaliza espaços e caixa.
Uma nota "editada" só em whitespace ou maiúsculas/minúsculas conta como inalterada, que é o
comportamento desejado (dedup semântica), mas explica por que `updated` fica em 0 num caso que
parecia uma edição.

---

## 7. Cutover — declaração não é estado

`MemoryFeatureFlags.position()` devolve 6 (pós-cutover) desde o primeiro boot, porque os
defaults ligam todos os estágios. Em 19/09/2026 dizia isso minutos depois de o journal receber
os primeiros registros — e as duas afirmações eram verdadeiras ao mesmo tempo: a configuração
estava completa, a migração tinha acabado de começar.

`cutover_evidence()` (exposto em `fabric_metrics()["cutover"]`) reporta as duas lado a lado:

```python
{
  "declared_position": 6,          # intenção: quais caminhos de código estão ativos
  "declared_stages": [...],        # os estágios ligados
  "serving": bool,                 # EVIDÊNCIA: derivado dos blockers abaixo
  "blockers": ["journal_empty", "projections_pending", "projections_failed", "leases_expired"],
  "outbox_pending": {...},
  "outbox_failures": {...},
}
```

`serving` responde sobre **estado**, derivado de evidência, não de intenção. `position()` não
tem consumidor em produção — só testes a chamam.

Estágios do cutover, em ordem: `canonical_writes`, `projections_via_outbox`, `canonical_fts`,
`vector_rrf`, `legacy_writers_disabled`, `legacy_readers_disabled`.

---

## 8. Como verificar

```bash
# provider ativo
HOME=/root haos config get memory.provider          # -> hermes_fabric

# budget é um cap real (maior bloco <= 5000)
HOME=/root HERMES_HOME=/root/.haos python -c "
from plugins.memory import load_memory_provider
p = load_memory_provider('hermes_fabric')
p.initialize(session_id='check', memory_scopes=('project','global'))
print(max(len(p.prefetch(q)) for q in ('Kubernetes','memoria','ADR decisao')))"
# sem initialize() o prefetch devolve vazio: o provider só carrega por sessão

# teto de ingestão e estado do sync
HOME=/root haos memory migrate --json    # skipped_oversized, updated, imported, skipped_existing

# contadores do journal
python -c "
from hermes.platform.context.memory.canonical_store import CanonicalMemoryStore
s = CanonicalMemoryStore('/root/.haos/memory/fabric.db')
print(s.operational_counters()); print(s.pending_by_projection()); s.close()"

# os quatro sinks
sqlite3 /root/.haos/memory/vectors.db    'SELECT COUNT(*) FROM memory_vectors;'
sqlite3 /root/.haos/memory/decisions.db  'SELECT COUNT(*) FROM memory_decisions;'
sqlite3 /root/.haos/memory/graphrag.db   'SELECT COUNT(*) FROM entities;'
find /root/.haos/obsidian_vault/10-Memory /root/.haos/obsidian_vault/20-Architecture -name '*.md' | wc -l
```

Rollback do fabric: `haos config unset memory.provider` → `haos gateway restart`. O journal
fica em `HAOS_HOME/memory/fabric.db` (apagar junto com `-wal` e `-shm`).

---

## 9. Lacunas conhecidas (não resolvidas)

- **Registros grandes já ingeridos.** O teto impede ingestão nova, mas não retira o que já
  entrou: em 19/09/2026 o espelho de 52k e o artigo de 25k continuam no journal (28 ativos).
  Impacto medido, com o piso de justiça ativo: o espelho ocupa **um** slot e os reais
  sobrevivem (`ADR decisao` → 5 blocos, todos ADRs; `memoria` → nenhum espelho). É degradação
  de qualidade, não de correção. Retirar exige um primitivo de retirada sem sucessor — a
  supersessão exige sucessor, e não foi verificado se as projeções tratam remoção.
- **Projeções de registros superseded.** Não foi verificado se um registro superseded é
  removido de `graphrag.db` e `vectors.db`, ou se fica órfão. Afeta qualquer supersessão, não
  só o caso acima.
- **A regra "todo commit no main incrementa +1"** é documentada (AGENTS.md,
  `DEV_WORKFLOW_VM.md` §5) mas **não é aplicada** por hook de pre-commit nem por CI.
