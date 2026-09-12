# Absorção da pesquisa Medium (agentes, RAG, memória) — plano

Fonte: `/tmp/relatorio-pesquisa-medium-agentes-rag-memoria-HAOS-2026-09-12.md` (1049 linhas,
24 artigos, backlog P1–P12, "pesquisa concluída; backlog NÃO implementado").

## O que eu verifiquei antes de aceitar

Citações de código conferidas **linha por linha** neste repo — todas batem:

| citação do relatório | verificação |
|---|---|
| `hermes/platform/memory/instincts.py:24` | `CONFIDENCE_PROMOTION_THRESHOLD = 0.8` |
| `instincts.py:124` / `:132` | `get_eligible_promotions` / `mark_promoted` |
| `hermes/platform/memory/dream.py:241` | import do `InstinctStore` (o "gancho") |
| `hermes_cli/haos_cmd.py:391` | import do `InstinctStore` (único chamador) |
| "`memory/instincts/` não existe → zero instintos" | confirmado: só `graphrag.db`, `ragflow.db`, `reconciled_memories.db` |
| `delegation.max_concurrent_children=3`, `max_spawn_depth=2` | confirmado **no runtime** (`/root/.haos/config.yaml`); os *defaults* do repo são 10 e 1 |

Achado central do relatório (§5.8) é **verdadeiro neste repo**: o ciclo reflexão → patch →
validação morre no `dream`. Nada promove lição a skill automaticamente.

## Duas correções que eu devo ao relatório (e a mim)

1. **Temos GraphRAG sim.** Eu havia dito que não. `hermes/platform/memory/graphrag.py` está neste
   repo, e 72 arquivos citam graphrag. O que **não** está no repo é o `graphrag-lite` citado no §5:
   é uma **skill instalada** em `/root/.hermes/skills/software-development/graphrag-lite`
   (por isso não a achei — procurei no repo e em `/root/.haos`). Casos de eval em
   `/root/workspace/haos-evals/cases/graphrag-*.json`.
2. **Cheiro de caminho**: a skill está sob `/root/.hermes/` (home do upstream), não sob
   `/root/.haos/` (home do fork). Hipótese não confirmada: skill instalada no home errado. Vale
   checar antes de P1, porque o gold set vai medir o recall dela.

## Escopo real: duas implantações, não uma

O relatório mede a instância de **desktop** (`cachyos-x8664`, `/root/.haos`), com config runtime
própria — não o appliance (`/home/haos/.haos`) nem os defaults do repo. Consequência: todo número
"medido" precisa ser re-medido para a implantação alvo antes de virar decisão.

Onde cada item vive:

- **Neste repo** (acionável direto): P2, P3, P5, P6, P8, P9, P10, P11, P12.
- **Na skill `graphrag-lite`** (fora do repo): P1, P4, P7.
- **Ambos**: P1 precisa da skill para medir e do repo para consumir o resultado.

## Pré-requisito que o relatório não tem, e que é meu

O §10 define "pronto" com **gate da suíte (`exit = nº de falhas`)**. Hoje a suíte sai com **26
falhas** (23 pré-existentes). Um gate que sempre falha não é gate: **nenhum item do backlog pode
ser validado** enquanto o baseline estiver vermelho. Estado atual: 5 resolvidas (1 bug real de
ordem de import do bootstrap UTF-8 + 4 de rebrand), 2 com causa diagnosticada (sinal de origem no
`check_env_isolation`), 16 ainda não diagnosticadas. Registro em `docs/haos/TEST_SUITE_STATE.md`.

## Plano por etapas

**Etapa 0 — fechar o baseline da suíte.** Diagnóstico e correção das 16 restantes + as 2 do doctor.
Sem isso, os gates das etapas seguintes são decorativos.

**Etapa 1 — P11 + P3** (o relatório também abre por P11, e concordo). Gate de staging no `dream`:
hoje a única barreira para uma lição entrar em sessões futuras é a auto-confiança ≥0,5 do próprio
LLM, e o gancho em `dream.py:241` está atrás de um gate por palavra-chave no preview cru. Auditor
de skills + teto de tamanho (o parque tem 143 skills; a literatura recomenda ≤20 tools por agente).
Ambos in-repo, verificáveis, sem dependência de medição externa.

**Etapa 2 — P1 (gold set ~20 perguntas)** na skill `graphrag-lite`, com os casos que já existem em
`/root/workspace/haos-evals/cases/graphrag-*.json` como ponto de partida. É o passo que o relatório
marca como "não pule": sem ele, P4/P6/P7 são chute.

**Etapa 3 — P2 + P5.** Promoção lição → skill (depende de P11 e P3) e governança de memória
(proveniência, TTL, integridade). Fecha o ciclo de aprendizado de ponta a ponta.

**Etapa 4 — P4 + P6 + P7 + P8 + P9 + P10 + P12.** Topo da recuperação, kill-switch por orçamento,
A/B de RRF contra a mistura linear, tetos de loadout, verificação antes de mais agentes, run
contract nas missões longas do kanban, escrita em delta.

## Não-objetivos (§11) — respeitar integralmente

Sem ferramenta nova (Mem0, Letta, Zep, Graphiti, Cognee, LangMem, Ragas, Promptfoo, LangSmith…);
sem treinar/ajustar modelo com dados do projeto; **não** remover os caps de orquestração (3/2);
**não** desmontar grafo, conselho A2A ou memória hierárquica com base nesta pesquisa (o pedido é
medir); sem loop de verificação contínuo (gates noturnos e casos de eval, sim); nada de persistir
candidato que falhou; o agente **não** reescreve `MEMORY.md` nem o system prompt.

## Ressalvas de método

- Li a estrutura completa, o §5 inteiro, os §§6, 9 e 11 e os objetivos de P1–P12. **Não** li os
  §§3, 4, 7 (detalhes), 8 e 10 na íntegra.
- As afirmações "debate multi-agente não bate baseline com orçamento igualado" e "hierarquias de
  memória são superestimadas" são da literatura, não medidas no HAOS. Servem para justificar medir
  (P1/P4/P6), não para justificar cortar investimento antes da medição.
