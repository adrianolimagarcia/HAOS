# Corpus de avaliação de memória HAOS — baseline Etapa 0

Este corpus é sintético e deliberadamente não contém transcrições reais ou segredos. Ele fixa os cenários de regressão derivados da auditoria, não mede desempenho de um LLM nem substitui o holdout de conversas autorizadas.

## Arquivos

- `fixtures/corpus_cases.json`: 12 casos congelados em cinco categorias.
- `fixtures/manifest.json`: SHA-256 e contagem congelados.
- `eval_harness.py`: valida hash e lista distribuição do corpus.
- `test_memory_baseline.py`: valida corpus e comportamento observado no runtime.

## Execução

No checkout do HAOS, a partir da raiz: `./scripts/run_tests.sh evals/gbrain_memory/test_memory_baseline.py`.

## Integridade

SHA-256 do JSON de casos: `9a314feec639ae68e68e48f2e8cc8af08451a6cac3b674c21b96f145931e0644`.
O harness de baseline carregou os 12 casos e confirmou o hash. Para revisão de alterações, qualquer edição do corpus exige atualizar manifesto e reavaliar; separar dev/eval e não expor respostas-alvo a um sistema avaliado.

## Limite de validade

Os fixtures representam hipóteses de auditoria, não dados reais. Para medições comparáveis nas Etapas 2–3, solicitar autorização do dono, escolher sessões do perfil autorizado, aplicar sanitização e varredura de segredos, separar conjuntos dev/eval, e regenerar manifesto com o artefato exportado. Não copiar diretamente `state.db` ou mensagens para o repositório.
