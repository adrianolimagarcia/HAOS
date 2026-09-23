---
id: ADR-008
titulo: "REGISTRY: estado canônico, histórico rotacionado, índice no grafo e espelho no vault"
status: "Aceito"
data: 2026-09-15
decidido_por: "human:adriano"
autor: "adriano"
contexto: "governança de infraestrutura, separação de estado canônico e log append-only"
causado_by:
  - "adr:ADR-005"
  - "adr:ADR-002"
  - "adr:ADR-003"
  - "adr:ADR-006"
  - "REGISTRY.md acumulando 281KB (69% log histórico) inflacionando o custo de contexto em cada ação de infra"
evidence:
  - "REGISTRY.md medido em 281.064 bytes / 941 linhas com 88 entradas datadas"
  - "zero indexação de council/ no grafo"
affects:
  - "REGISTRY.md"
  - "REGISTRY-HISTORICO.md"
  - "obsidian_vault/infra/REGISTRY.md"
  - "sdb/hermes/graphrag-lite"
supersedes: []
superseded_by: null
prov:
  wasGeneratedBy: "task:otimizacao-e-governanca-registry"
  wasAssociatedWith: "agent:haos-cachyos-x8664"
  wasDerivedFrom: "adr:ADR-005"
  causado_by: "inchaço do REGISTRY.md misturando estado vivo com log append-only"
  affects:
    - "REGISTRY.md"
    - "obsidian_vault/infra/*"
  supersedes: []
  superseded_by: null
---

# ADR-008 — REGISTRY: estado canônico, histórico rotacionado, índice no grafo e espelho no vault

**Status:** Aceito (decisão do dono, 2026-09-15: "faça tudo isso e repasse para os outros agentes via a2a")
**Data:** 2026-09-15
**ADR relacionadas:** ADR-005 (caminho canônico do vault e escopo dos dois grafos), ADR-002/ADR-003 (recall do grafo), ADR-006 (numeração e namespace de ADR)
**Escopo:** este nó (cachyos-x8664) como modelo; política a ser adotada pelos demais nós da malha

## Contexto (medido 2026-09-15)

O REGISTRY é fonte única de verdade do estado de infra (Regra de Ouro, cláusula 1) e é consultado
**antes de toda ação de infra** (cláusula 2) — logo seu tamanho é pago em contexto a cada ação.
Medição antes da mudança:

| medida | valor |
|---|---|
| `REGISTRY.md` | 281.064 B / 941 linhas |
| seção `## Referências` (log append-only) | 193 KB = **69%** do arquivo, 88 entradas datadas |
| subseções `###` de narrativa em `## Decisões aplicadas` | 13,5 KB (7 subseções) |
| ritmo de escrita | ~11 entradas/dia |
| corpus do grafo que varria `council/` | **nenhum** |

Dois defeitos distintos, não um:

1. **Mistura de naturezas no mesmo arquivo.** Estado (exato, lido inteiro, editado no lugar) e
   histórico (append-only, cresce sem limite) — tanto o log de entradas quanto as narrativas
   detalhadas das decisões. A seção `## Referências`, que o template reserva para ponteiros, havia
   virado o log.
2. **Ponto cego de recall.** O grafo operacional varria `skills`, `docs`, `vault` e `okf`
   (`refresh.sh`); o built-in varria só `obsidian_vault` (`haos_memory_populate.py`). O REGISTRY
   não era alcançável por recall — só por grep/leitura manual.

## Decisão

1. **A fonte continua arquivo.** `REGISTRY.md` segue canônico no caminho de sempre
   (`$HOME/.hermes/council/REGISTRY.md`, symlink em `$HOME/.haos/council/`), com vigia por hash e
   gate pré-ação. O arquivo passa a conter **estado apenas**.
2. **O histórico sai do arquivo**, em duas passadas do mesmo rotacionador
   (`~/.haos/scripts/registry_rotate.py`), ambas idempotentes:
   - **Entradas** (`## Referências` legado e `## Log recente`): as que passam de 24 h vão para
     `council/log/AAAA-MM.md`.
   - **Narrativas** (`### AAAA-MM-DD` dentro de `## Decisões aplicadas`): vão para
     `council/log/narrativas-AAAA-MM.md`, com ponteiro no lugar. O **bullet canônico** (data +
     contextId + `verificado_em`) permanece no REGISTRY — é o que a cláusula 5 exige; a subseção
     detalhada é evidência.
3. **Grafo não é fonte, é índice.** `council/` entra no grafo operacional como corpus `council`,
   com **alvos explícitos** (`REGISTRY.md` + `log/`) — nunca o diretório inteiro, que tinha 31
   `REGISTRY.md.bak-*` (versões mortas, hoje consolidadas em `council/backups/`).
4. **Espelho unidirecional no vault.** `curadoria/registry-espelho.md` é **nota gerada**,
   somente-leitura (modo 444), escrita por `registry_mirror.py` no refresh. Dá ao dono a leitura no
   Obsidian e ao grafo built-in (escopo = vault, ADR-005) o estado de infra. A seção de log é
   omitida (churn) e o carimbo é derivado do **mtime da fonte** (nunca de `now()`).
5. **Vigia multi-alvo.** `registry_guard.py` v2 fingerprinta `REGISTRY.md` + `council/log/*.md`
   (o log passou a ser alvo de append), com migração do baseline v1 e dedupe por alvo.
6. **Política para a malha.** Os demais nós adotam o mesmo desenho no **próprio** REGISTRY: o nó
   dono aplica, ninguém escreve na fonte de outro nó (cláusula 7).

## Alternativas descartadas

| alternativa | por quê não |
|---|---|
| **REGISTRY virar nota do vault** | o vault é corpus curado e estável; o REGISTRY é operacional e volátil, com ponteiros de credencial. E quebraria em cadeia: symlink, vigia por hash, validação `grep -c`, reparador, template e 8 skills. O ganho (leitura humana) é obtido pelo espelho, sem esse custo. |
| **REGISTRY virar grafo** | grafo é índice **derivado**, reconstruído do zero — não tem diff, autoria nem "vence a memória". Não pode ser fonte única de verdade. |
| **Manter tudo num arquivo e só comprimir** | o problema não é o tamanho, é a mistura: o histórico continua invisível ao recall e o custo de contexto por ação não cai. |
| **Indexar o diretório `council/` inteiro** | 31 backups `REGISTRY.md.bak-*` (~3 MB) entrariam como corpus vivo, com estado obsoleto competindo com o atual. |

## Consequências

- `REGISTRY.md`: **281.064 B → 141.033 B (-50%)**; no arquivo sobraram 24 h de log (~67 KB) e o
  estado (~71 KB, que é o tamanho do espelho). Narrativas: 13,5 KB fora.
- O REGISTRY passa a ser alcançável por recall: corpus `council` com ~79 chunks e ~2.820 entidades
  ligadas; o `recall` cita `@session:council/docs_REGISTRY.md` e `@session:council/docs_2026-09.md`.
  Efeito imediato: a primeira consulta expôs uma divergência real (a skill `hermes-a2a-council` ainda
  lista o nó `celular` na malha; o REGISTRY o marca removido em 05/09).
- **Limite do índice — ENCONTRADO E CORRIGIDO em 2026-09-15 (mesma sessão).** O indexador decidia o
  que processar **só pelo id do chunk** (`perfil:sessão:índice`); como os corpora markdown são
  regenerados da fonte a cada refresh, conteúdo que desloca caía sob um id já marcado como indexado
  e **nunca era reindexado**. Medição: **442 de 4.725 chunks (9,4%) defasados** — `default` 261,
  `skills` 166, `vault` 6, `council` 5, `okf` 4, `docs` 0. Correção: `pending` passou a ser ciente de
  conteúdo (id ausente **ou** texto diferente), preservando `--limit`, `--reset`, `--retry-failed` e
  o passe de órfãos. Resultado verificado: **442 → 0**, 442 reindexados em 1.121 s, **82 chunks antes
  desistidos revividos**, e segunda execução consecutiva com `pendentes: 0` e `graph.db` byte-a-byte
  idêntico. Lições: `okf/licoes/corpus-derivado-com-id-posicional-deixa-conteudo-defasado-em-silencio.md`
  (inclui a distinção **órfão × drift**) e
  `okf/licoes/idempotencia-medida-dentro-do-mesmo-minuto-nao-e-prova.md`.
- **Residual:** ~71 KB de estado, dos quais ~34 KB são os 31 bullets de `## Decisões aplicadas`
  (exigidos pela cláusula 5) e 13,6 KB a seção `## Serviços deste nó`. Não há mais mistura
  estado×histórico no arquivo.
- **Pendências abertas registradas no REGISTRY:** 1.564 chunks **órfãos de remoção** (status=1) ainda
  servidos como contexto pelo `query.py`; **relações acumulam** na reindexação (66.050 → 78.843);
  `A2A_TRUSTED_PEERS` (lista de ENTRADA) ainda cita `hermes-windows`; `bot_peers` com 2 IPs mortos.
- **Reversão:** acervo de backups consolidado em `council/backups/` (31 arquivos, `README.md`); o
  pré-rotação é `council/backups/REGISTRY.md.bak-20260915-rotacao-estadolog`. Dos scripts:
  `registry_guard.py.bak-20260915-v1-alvo-unico`,
  `registry_rotate.py.bak-20260915-v1-so-referencias`,
  `registry_rotate.py.bak-20260915-v2-so-entradas`; do índice:
  `graph.db.bak-20260915-drift` e `index.py.bak-20260915-drift`; do config:
  `config.yaml.bak-20260915-a2apeers`. Desligar o corpus `council` = remover a linha do `refresh.sh`.

## Critérios de aceite (verificados)

- [x] 88/88 entradas preservadas, 0 perdidas, 0 inventadas (união `Log recente` + `log/2026-09.md` =
      seção `## Referências` original).
- [x] 7/7 narrativas arquivadas, 0 perdidas, 0 inventadas; **31 bullets canônicos intactos**;
      ponteiro único.
- [x] Rotação idempotente **de verdade**: quando não há o que arquivar e o layout já é canônico, o
      script **não escreve** (mtime e sha256 idênticos em execuções separadas por >1 min).
      **Ressalva de método registrada:** o critério inicial — "sha igual em execuções consecutivas"
      — foi medido dentro do mesmo minuto e **não provava nada** (o comentário `verificado_em` tem
      carimbo de minuto). O defeito foi encontrado e corrigido; o mesmo defeito existia no espelho
      (carimbo `now()` → reescrevia a nota do vault a cada refresh, sem o REGISTRY mudar), também
      corrigido com carimbo derivado do mtime da fonte.
- [x] Rotação cobre o layout novo **e** o legado (legado: 274 KB → 127 KB com entradas 88/88 e
      narrativas 7/7); guarda de destruição aborta (exit 3) se houver seção de estado depois da
      seção de log, sem escrever.
- [x] Vigia multi-alvo, 8/8 testes: silencioso sem mudança; escrita local auto-baselinada; escrita
      externa (login de peer adjacente) alerta com exit 1 no alvo correto; dedupe do alerta;
      migração do baseline v1; baseline não re-baselinado após alerta.
- [x] Corpus `council` indexado e **com o conteúdo novo** (79 chunks, 3 sessões — inclui
      `narrativas-2026-09.md`); `recall` cita as sessões do corpus.
- [x] Espelho: modo 444, idempotente de verdade (`--check` verde após >1 min; regenera só quando a
      fonte muda), seção de log omitida.
- [x] Escritas concorrentes de outras sessões durante a migração absorvidas sem perda.
- [x] Acervo de backups consolidado em `council/backups/` (31 arquivos, dedupe por sha256 com
      contabilidade exata, colisão de nome preservada, `README.md`), sem tocar em estado vivo.
- [x] `a2a_agents` corrigido (5 → 3 peers) com leitura de volta pelo gateway (`a2a_list` = 3).

## Adoção pelos demais nós

Política distribuída por A2A em 2026-09-15 pela tool (proveniência preservada em `a2a_audit.jsonl`),
com o template `hermes-a2a-council/templates/REGISTRY-template.md` atualizado para o layout novo.

**Estado da entrega (medido, não presumido):** `asus` (`ctx-e6ccbd22c70746eb`) e `luckystrike`
(`ctx-ef7890e3df3d4c05`) receberam a mensagem (persistida como `user` no contexto de cada um) mas
**não responderam dentro dos 300 s** do canal — **adoção não confirmada**. Agent Cards confirmados ao
vivo: `hermes-luckystrike` e `hermes-asus` (200 na `:9900`); `wsl`/`hermes-windows` (mesmo host) e
`agentia`/`dedirock`/`s25-ultra` respondem ping mas recusam a 9900. Foi criado o cron
`a2a-registry-policy-followup` (`79ae97be6ea8`, a cada 3 h, **3 execuções e para**, `deliver: local`)
para colher a confirmação e registrar o resultado no próprio REGISTRY.
