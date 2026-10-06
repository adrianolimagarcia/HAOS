# Plano de ação executivo — absorção de Agent Skills

**Estado:** EM EXECUÇÃO  
**Plano-mãe:** `docs/haos/PLAN_AGENT_SKILLS_ABSORPTION.md`

## Estratégia

Execução em dois gates para impedir implementação baseada em premissas:

### Gate 1 — provas e baselines (paralelo)

| Frente | Saída obrigatória | Poder de decisão |
|---|---|---|
| A0 Reachability | matriz HAOS/Civilization e composição real do pipeline de skills | decide G1 e integrações CIV |
| T0 Temporal | corpus virgem, baseline reproduzível e lacunas observadas | decide T1–T4 |
| CG0 Grafo | benchmark do grafo atual contra perguntas/corpus poliglota | decide se qualquer extensão é necessária |
| S1 Dívida técnica | extensão de skill + validador + testes | independente, pode concluir no Gate 1 |
| V1 Visual | skill opcional mínima + verificador seguro + testes | independente, pode concluir no Gate 1 |

### Gate 2 — implementação condicionada

- **T1–T4:** somente se T0 comprovar lacuna temporal.
- **G1:** somente no pipeline real identificado por A0; nenhuma infraestrutura paralela.
- **Grafo/Tree-sitter:** somente se CG0 mostrar ganho relevante e reproduzível.
- **Civilization:** somente com consumidor real e teste E2E; ausência de consumidor implica `DEFER`, não código especulativo.

## Ownership para evitar conflitos

- Agente A0: somente `docs/haos/audits/agent-skills-runtime-integration.md` e testes/probes temporários estritamente necessários.
- Agente T0/CG0: `evals/agent_skills_absorption/` e relatório; sem alterar produção.
- Agente S1: `skills/software-development/codebase-inspection/` e testes específicos.
- Agente V1: `optional-skills/creative/visual-explainer/` e testes específicos.
- Após Gate 1, agentes T/G/CIV recebem ownership exclusivo dos arquivos definidos pela auditoria.

## Disciplina harness

1. Reproduzir uma vez.
2. Implementar mudança mínima.
3. Rodar teste direcionado uma vez.
4. Em falha, RCA sobre a evidência; não repetir comando idêntico.
5. Rodar suíte da área uma vez quando o direcionado estiver verde.
6. Revisão adversarial única no final.
7. Nenhuma alegação de integração sem binding real e efeito observável.

## Critérios de parada

- Baseline não demonstra lacuna → cancelar implementação correspondente.
- Capacidade já atende ao contrato → documentar e não alterar.
- Integração exige novo core tool ou sistema paralelo → redesenhar na Footprint Ladder.
- Teste só passa contornando resolver/registry → rejeitar o teste.
- Civilization não tem consumidor real → `DEFER`.
