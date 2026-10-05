# Piloto Real T0: Avaliação Empírica do Fluxo de Pesquisa Temporal

## 1. Resumo Executivo e Decisão (GO / NO-GO)
- **Decisão T1–T4**: **GO com ressalvas / Condicional** para os tickets de absorção T1–T4 (absorver mecanismos de validação temporal, resolução de conflitos e ledger de evidências).
- **Justificativa**: O piloto empírico em 4 casos de teste congelados do corpus T0 confirmou exatamente o gap arquitetural diagnosticado no plano de absorção:
  1. Canais abertos de pesquisa via LLM/Search sofrem de ambiguidade de entidades, alucinações de recência e incapacidade de reconciliar alegações conflitantes.
  2. Falta de amarra temporal e de resolução verificável de citações estruturadas.
  3. A absorção das skills de pesquisa profunda, resolução de conflitos e graph/temporal tracking é estritamente necessária antes de liberar queries temporais críticas para produção.

---

## 2. Execução do Piloto Real (Corpus T0)

Foram executadas 4 consultas reais contra o canal oficial de pesquisa (`mcp__haos_edge__perplexity_ask`), limitadas estritamente a 1 request por caso (total = 4 requests), sem revelar nos prompts as respostas esperadas ou as entidades sintéticas do fixture.

### Caso T0-01: Orion DB Snapshot Export
- **Query**: `"What changed in the latest Orion DB release?"`
- **Latência**: 25.7s (23 fontes analisadas)
- **Resposta Observada**: O modelo tentou resolver um repositório GitHub arbitrário (`abderrahmenlamloumi/OrionDB`) com commits de 20 de setembro de 2026, misturando resultados com notícias de NFL player (*DB Orion Stewart*).
- **Fontes Observadas**: NFL.com, CBS Sports, Yahoo Finance.
- **Diagnóstico**: Ambiguidade severa de entidade, ruído e falta de âncora factual/temporal.

### Caso T0-02: Nimbus API Outage
- **Query**: `"Was there an incident affecting Nimbus API this week?"`
- **Latência**: 5.1s (10 fontes analisadas)
- **Resposta Observada**: Consultou `status.nimbus.dev` e agregadores de uptime reportando 100% de uptime; não detectou o incidente do fixture.
- **Fontes Observadas**: `status.nimbus.dev`, `statusstack.com/service/nimbus`, `status.nimbusapi.net`.
- **Diagnóstico**: Canal de pesquisa pública operacional ignora incidentes internos ou corporativos específicos sem cross-referencing de fontes primárias.

### Caso T0-03: Atlas Storage Pricing
- **Query**: `"What is the current price of Atlas Storage?"`
- **Latência**: 8.1s (25 fontes analisadas)
- **Resposta Observada**: Respondeu que "Atlas Data Storage" é uma empresa privada sem cotação pública (~$245M valuation), e pediu clarificação se a intenção era MongoDB Atlas.
- **Fontes Observadas**: `nasdaqprivatemarket.com`, `notice.co`, `mongodb.com/pricing`.
- **Diagnóstico**: Ambiguidade léxica; ausência de verificação contra SLA/tabela de preços histórica.

### Caso T0-04: Lumen SDK 3.1 Release
- **Query**: `"Did the Lumen SDK release version 3.1 during the last two weeks?"`
- **Latência**: 16.2s (22 fontes analisadas)
- **Resposta Observada**: Respondeu honestamente "I couldn’t verify that a Lumen SDK version 3.1 was released during the last two weeks", apontando ambiguidade de repositórios.
- **Fontes Observadas**: Android Studio SDK notes, Silicon Labs Simplicity SDK, GitLab `lumen-code/lumen` (v2.1.0).
- **Diagnóstico**: Resposta honesta de inconclusividade, mas falha em detectar alegações conflitantes sem um motor explícito de detecção de contradições.

---

## 3. Métricas do Scorer e Execução de Testes

Os testes de baseline foram executados usando o runner do HAOS:
```bash
HERMES_PYTHON=/usr/local/lib/haos-agent/venv/bin/python3 ./scripts/run_tests.sh evals/agent_skills_absorption/test_baselines.py
```
**Resultado**:
```
tests/test_verify_contracts.py: 1 passed
evals/agent_skills_absorption/test_baselines.py: 1 passed
Total: 2 passed in 1.48s
```

Métricas observadas no scorer formal (`evals.agent_skills_absorption.temporal.score`):
- `support_rate`: **0.00** (0 / 16 suportadas com rigor temporal e citações válidas do corpus congelado)
- `citation_resolution_rate`: **0.00** nas respostas piloto não ancoradas ao fixture; **1.00** no fixture congelado de evidências pré-populado
- `conflict_coverage`: **0.00** (conflitos não reconciliados pelo canal de busca não assistido)
- `invented_dates`: **1** (alegação de commit inferida sem prova de release formal no T0-01)

---

## 4. Recomendações para T1–T4
1. **T1 (Ledger Temporal & Citation Anchoring)**: Implementar vinculação obrigatória de URLs e timestamps a IDs canônicos de evidência antes de sintetizar respostas.
2. **T2 (Detecção e Resolução de Conflitos)**: Absorver heurísticas de conciliação de fontes primárias vs secundárias.
3. **T3 (Canal de Fallback / Status API)**: Implementar scrapers dedicados para páginas de status estruturadas em vez de busca livre.
4. **T4 (Graph Context & Entity Disambiguation)**: Fornecer ancoragem de grafo de código/arquitetura para eliminar falsos positivos de entidades (ex: desambiguar "DB" de time esportivo vs Database).
