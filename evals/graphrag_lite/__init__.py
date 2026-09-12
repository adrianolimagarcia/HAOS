"""Gold set do graphrag-lite (backlog HAOS P1 — docs/haos/RESEARCH_MEDIUM_ABSORPTION.md, Etapa 2).

Um gold set de ~20 casos executaveis contra a maquinaria REAL do repo
(hermes/platform/memory + hermes/platform/context/memory): cada caso constroi
fixture deterministica em tempdir, roda a API real e verifica um CONTRATO de
comportamento. Nada le codigo-fonte, nada depende de instalacao externa, nada
escreve em ~/.hermes (o runner isola HERMES_HOME por caso).

Formato dos casos (compativel com o runner de haos-evals):
    {"id", "pergunta", "descricao", "tags", "timeout",
     "grader": {"type": "python", "code": "from evals.graphrag_lite.impl import run_case; run_case('<id>')"}}

run_case sai 0 no PASS e 1 no FAIL (mensagem em stderr) — o mesmo contrato de
exit code que o runner de haos-evals usa como gate.
"""