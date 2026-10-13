# Code Ladder (lean-agent) vs Decision Ladder (ponytail) — comparação clean-room

> Análise conceitual. Nenhum código ou texto do lean-agent foi copiado
> (repo sem licença); apenas os conceitos foram estudados e reimplementados.

## Estrutura: essencialmente a mesma escada

| lean-agent (Code Ladder) | ponytail (Decision Ladder) |
|---|---|
| 1. nothing | 1. Does this need to exist at all? (YAGNI) |
| 2. project's own code | 2. Already in this codebase? |
| 3. standard library | 3. Stdlib does it? |
| 4. platform | 4. Native platform/browser feature? |
| 5. installed package | 5. Installed dependency? |
| 6. one expression | 6. Can it be one line? |
| 7. new code | 7. Only then: minimum working code |

**Conclusão: NÃO há gap estrutural.** A escada do ponytail já cobre os
7 degraus na mesma ordem de custo. Criar uma skill nova duplicada seria
puro over-engineering (irônico, dado o tema).

## Nuances do lean-agent que o ponytail NÃO tinha (e foram incorporadas)

Foram adicionadas à skill `ponytail` v1.1.0 na seção "Ladder Discipline":

1. **Rung declaration** — o agente deve *declarar* em qual degrau parou
   ("rung 3: stdlib — usei `collections.Counter`"). Sem declaração, não
   há decisão auditável; "seja simples" vira vibe.
2. **Tie-breakers** — se dois degraus servem, o mais barato vence; e
   decidir rápido (deliberação também é custo).
3. **Runtime-version check** — antes de usar stdlib nova (degrau 3),
   conferir a versão de Python declarada pelo projeto. Função que não
   existe no runtime do projeto não é degrau 3, é degrau 7 com risco.
4. **No unmeasured optimization** — degrau 6 ("uma linha?") é sobre
   remover cerimônia, não micro-otimizar. Sem medição, não há tarefa
   de otimização.
5. **Rung evidence** — o revisor (`ponytail-review`) pode checar a
   alegação contra o diff: claim de degrau barato só vale se o diff
   realmente usa aquele recurso *e* a mudança é pequena.

## O que foi deliberadamente NÃO trazido

- **Gates interceptadores de runtime** (read gate, sibling scan,
  dependency gate): a própria ablação do lean-agent os descartou
  (custo + falsos positivos sem ganho). O HAOS já exige leitura prévia
  em `write_file`. Rejeitado, conforme o veredito da análise.
- **Bloat meter / golf detector como gate automático**: incorporado
  apenas como *conceito* no guia de evals (`docs/EVALS-HONESTAS.md`,
  teste de Goodhart) — medir linhas sem medir legibilidade é a
  armadilha que o próprio lean-agent documenta.

## Arquivos

- `skills/development/ponytail/SKILL.md` — v1.1.0 (+Ladder Discipline)
- `skills/development/ponytail-review/SKILL.md` — v1.1.0 (+Trim Guard)
- `scripts/detect_overengineering.py` — detectores AST (10 shapes)
- `scripts/trim_guard.py` — trim guard determinístico
- `docs/EVALS-HONESTAS.md` — ablation ledger + Goodhart
