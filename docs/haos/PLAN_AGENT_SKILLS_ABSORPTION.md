# Plano de absorção das Agent Skills no HAOS/Civilization

**Status:** PROPOSTO — implementação não iniciada  
**Data:** 2026-10-04  
**Fonte de inspiração:** artigo “20 AI Agent Skills With ~800K GitHub Stars…”  
**Princípios:** evidência antes de integração; skills antes de core tools; uma validação por slice + uma revisão adversarial final; nenhuma promoção autônoma.

## 1. Objetivo

Transformar as ideias úteis do artigo em melhorias pequenas, mensuráveis e integradas aos caminhos canônicos do HAOS, sem criar agregadores, grafos, memórias ou control planes paralelos.

Este plano cobre três frentes:

1. auditar se os equivalentes já existentes são realmente alcançáveis pelo HAOS e pelo Civilization;
2. implementar um contrato temporal para pesquisas recentes;
3. decidir quais propostas devem ser absorvidas como skill, extensão de skill, CLI+skill ou não absorvidas.

## 2. Restrições arquiteturais

- Seguir o Footprint Ladder: estender código → CLI+skill → serviço/toolset/plugin/MCP somente quando necessário.
- Não adicionar core tool para algo resolvível por skill, CLI ou script auxiliar.
- Não alterar o system prompt durante uma conversa; dados temporais e mapas são lidos sob demanda.
- Resolver estado com `get_hermes_home()` no momento da chamada e provar isolamento A→B→A quando houver estado por perfil.
- Não conectar Civilization diretamente a stores de memória nem automatizar evolução/promoção sem contrato, provenance e aprovação já existentes.
- Testes de integração devem percorrer bindings reais; presença de arquivos, imports ou mocks não prova reachability.
- Usar `scripts/run_tests.sh`, nunca `pytest` direto.
- Cada slice executa seus testes uma vez; falha leva a RCA e correção dirigida, não a reruns idênticos.
- Uma revisão adversarial única encerra cada marco. Reabrir o marco somente com evidência nova.

## 3. Evidência inicial e correções de premissa

| Capacidade | Existe | HAOS hoje | Civilization hoje | Evidência observada | Veredito inicial |
|---|---:|---|---|---|---|
| Ponytail/YAGNI | Sim | Parcial | Não provado | `skills/development/ponytail/`, `ponytail-review/`; postura registrada; `PostureResolver` aparece em `hermes/platform/execution/spawn_resolver.py`, mas não foi provado no loop normal | Auditar binding real antes de qualquer wiring |
| Pesquisa + citações | Sim | Produção parcial | Integração de pesquisa existe; enforcement não provado | Perplexity/Medium/Reddit/web/delegação estão expostos; `grounded-citations` é procedural | Não criar middleware de citações antes de medir falhas reais |
| Napkin-equivalent | Sim | Produção | Parcial | Instincts, dream, staging, OKF, skill promotion e projeções de memória existem | Não ligar bots diretamente ao `InstinctStore`; usar rota canônica de staging/promotion |
| Codebase Wiki + GraphRAG | Sim | Skill/CLI alcançável; MCP não persistente | Não provado | `docs/haos/CODEBASE_WIKI.md`; `register_wiki_server()` só é chamado por testes e `hermes_cli/codebase_wiki_impl.py` | Corrigir reachability somente se um caso de uso exigir MCP no Civilization |
| Pesquisa noturna | Componentes existem | Dream/cron em produção | Parcial | Dream, provenance e promotion gates existem; missão autônoma noturna não foi provada | Não adicionar ciclo noturno autônomo nesta fase |

### Correções aos relatórios delegados

- O codebase-wiki **já possui cache incremental por SHA-256**, idempotência e arestas AST para Python. Não criar outro `sync-graph --diff`.
- A decisão atual exclui TS/JS deliberadamente. Tree-sitter só entra após benchmark virgem demonstrar perguntas importantes não respondidas pelo corpus Python+docs.
- Um processo curto `haos codebase-wiki --mcp` registra ferramentas apenas no agregador daquele processo; isso não prova disponibilidade no gateway nem no Civilization.
- A recomendação de escrever experiências de bots diretamente em `InstinctStore` viola staging/promotion. Deve ser rejeitada.
- A recomendação de inserir pesquisa/evolução autônoma no cron noturno amplia efeitos, custo e risco sem caso de uso medido. Deve ser rejeitada nesta rodada.
- O validador temporal pode verificar estrutura, datas e referências; não pode afirmar “discrepância factual” apenas por determinismo sintático.

## 4. Organização por agentes e entregáveis

### Agente A — Auditor de reachability HAOS/Civilization

**Missão:** provar, para cada equivalente existente, o caminho desde entrada real até execução e efeito persistido.

**Método:**

1. Mapear entrada (CLI, gateway, turn loop, cron ou missão Civilization).
2. Mapear resolução de skill/tool/posture e seus gates.
3. Mapear execução, estado escrito e observabilidade.
4. Localizar teste E2E que percorra esse mesmo binding.
5. Classificar: `ACTIVE`, `MANUAL_ONLY`, `DEAD/EPHEMERAL`, `NOT_IN_CIV`, `UNKNOWN`.

**Entregável:** `docs/haos/audits/agent-skills-runtime-integration.md` com matriz `capacidade → entrada → binding → executor → efeito → teste → lacuna` e `arquivo:linha`.

**Critério de aceite:** nenhuma integração recebe `ACTIVE` apenas porque existe um arquivo, registro ou teste unitário.

### Agente B — Contrato temporal de pesquisa

**Missão:** executar os slices da seção 5, um por PR/commit lógico e testável.

**Entregável:** contrato, ledger/validador, skill e eval comparativa, sem novo agregador.

### Agente C — Skills e Footprint Ladder

**Missão:** executar a triagem da seção 6; cada candidato precisa de baseline, caso de uso e gate de promoção.

**Entregável:** uma decisão por candidato: `EXTEND_SKILL`, `NEW_OPTIONAL_SKILL`, `CLI+SKILL`, `DEFER`, `REJECT`.

### Agente D — Revisor adversarial

**Missão:** revisar uma vez, depois dos artefatos dos agentes A–C.

**Bloqueadores obrigatórios:** sistema paralelo, tool/schema bloat, estado fora de profile scope, teste que não percorre produção, LLM autoavaliando sua própria saída, dependência não pinada, promoção automática ou claim sem métrica.

## 5. Plano detalhado — Temporal Research Contract

### 5.1 Resultado esperado

Toda pesquisa explicitamente recente deve informar:

- janela solicitada e janela efetivamente pesquisada;
- cutoff absoluto em ISO-8601 UTC;
- canais tentados e seus estados;
- fontes encontradas, datas publicadas quando conhecidas e data de acesso;
- conflitos entre claims/fontes;
- lacunas e impacto sobre a conclusão;
- confiança de frescor, separada de confiança factual.

Pesquisas sem intenção temporal continuam no fluxo atual.

### 5.2 Contrato mínimo

```json
{
  "contract_version": 1,
  "window": {
    "requested": "last30days",
    "start_utc": "2026-09-04T00:00:00Z",
    "end_utc": "2026-10-04T00:00:00Z"
  },
  "cutoff_utc": "2026-10-04T00:00:00Z",
  "coverage": [
    {"channel": "perplexity", "status": "ok", "result_count": 4, "reason": null}
  ],
  "findings": [
    {
      "claim": "...",
      "source_ids": [1],
      "published_at": null,
      "freshness": "unverified"
    }
  ],
  "disagreements": [],
  "evidence_gaps": []
}
```

**Regras:**

- `published_at = null` é permitido; nunca inferir data da posição no ranking.
- Fonte fora da janela pode ser marcada `historical_context`; não pode sustentar claim “ocorrido na janela”.
- Canal inacessível/degradado gera coverage explícito, não retry infinito.
- `disagreements: []` significa “nenhum identificado”, não “fontes concordam”.
- Validação determinística confirma schema, datas, IDs, janela e completude estrutural; veracidade factual continua exigindo inspeção das fontes.

### 5.3 Slice T0 — baseline e corpus virgem

**Antes de código:** congelar 12–20 perguntas que não contenham a resposta, distribuídas entre anúncio recente, incidente, preço, release, afirmação conflitante e fonte sem data.

**Baseline:** executar o fluxo atual uma vez e preservar respostas, fontes, custo/latência e falhas.

**Métricas:**

- claims temporais suportados por fonte dentro da janela;
- datas não inventadas;
- conflitos relevantes expostos;
- canais/falhas declarados;
- citações resolvíveis;
- custo e latência, reportados sem meta inventada.

**Gate:** só avançar se o baseline demonstrar ao menos uma lacuna concreta que o contrato pretende corrigir.

### 5.4 Slice T1 — preservar metadados upstream

**Escopo candidato:** `plugins/web/perplexity/provider.py`, normalização em `tools/web_tools*`, testes correspondentes.

**Mudança mínima:** preservar campos temporais opcionais retornados pelo provedor sem alterar as chaves legadas.

**Teste contratual:** payload com data mantém a data; payload sem data continua válido e não recebe data fabricada.

**Não fazer:** impor um schema temporal a toda busca web nem adicionar parâmetros ao core tool.

### 5.5 Slice T2 — ledger temporal no grounded-citations

**Escopo candidato:** `skills/research/grounded-citations/scripts/sources.py` e testes da skill.

**Mudanças:**

- evoluir o ledger com migração explícita e reversível;
- guardar `published_at`, `accessed_at`, `channel` e classificação `in_window|historical_context|unknown`;
- adicionar cabeçalho do contrato, coverage, disagreements e evidence gaps;
- validar timezone, intervalos, referências e justificativa de fontes fora da janela.

**Decisão de projeto:** verificar primeiro se o ledger atual é compartilhado ou por execução. Não introduzir `HERMES_CITATION_LEDGER`; configuração comportamental não vai para env var. Preferir caminho passado explicitamente ou estado profile-scoped resolvido no runtime.

**Testes:** migração v1→v2, datas ausentes, fonte fora da janela, timezone normalizado, canal degradado e duas execuções concorrentes sem colisão.

### 5.6 Slice T3 — skill `temporal-research`

**Local:** `skills/research/temporal-research/`, caso seja suficientemente geral; caso contrário iniciar em `optional-skills/research/`.

**Fluxo:**

1. detectar intenção temporal explícita;
2. fixar cutoff uma vez;
3. converter janela relativa para UTC;
4. consultar apenas canais disponíveis;
5. ingerir evidências no ledger;
6. separar fatos recentes de contexto histórico;
7. registrar conflitos e gaps;
8. renderizar relatório;
9. executar verificador determinístico uma vez.

**Padrões HARDLINE:** descrição ≤60 caracteres, ferramentas nativas, helper scripts para lógica não trivial, testes offline e nenhum acesso amplo implícito.

### 5.7 Slice T4 — delegação estruturada

Usar `output_schema` somente nas missões de pesquisa temporal. O schema vive como template/referência da skill; não cresce o schema global de tools.

**Gate:** um retorno inválido recebe no máximo a correção limitada já suportada pela delegação. Persistindo a falha, entregar resultado parcial marcado como não conforme — nunca loop.

### 5.8 Slice T5 — integração Civilization, somente após T0–T4

Civilization não deve ser requisito para pesquisa normal. Integração permitida apenas se existir missão/council consumidor real.

**Forma recomendada:** anexar o contrato verificado como artefato/provenance da missão. Não duplicar o conteúdo no EventHub e em stores paralelos. Se eventos forem necessários, armazenar identificador/hash/caminho profile-scoped, não o relatório inteiro.

**Gate:** teste E2E com duas homes A→B→A prova isolamento e que uma missão do Civilization consegue ler o artefato correto.

### 5.9 Aceite global do contrato temporal

- Zero datas fabricadas nos fixtures.
- 100% dos IDs de citação resolvem no ledger.
- Toda fonte desconhecida permanece `unknown/unverified`.
- Coverage distingue `empty`, `inaccessible`, `degraded` e `bypassed`.
- Nenhuma nova core tool.
- System prompt permanece byte-estável durante a conversa.
- Baseline vs tratamento usa o mesmo corpus virgem e reporta resultados, inclusive regressões.
- Testes direcionados verdes via `scripts/run_tests.sh`; sem rede viva na CI.

## 6. Triagem das propostas como skills

### 6.1 Auditoria de dívida técnica — `EXTEND_SKILL`

**Destino preferido:** ampliar `skills/software-development/codebase-inspection/` com template em `references/`; criar skill separada apenas se o gatilho semântico não couber sem tornar a skill grande.

**Contrato de achado:**

```text
id, file:line, observed_evidence, impact, effort, confidence,
proposed_remediation, verification, disposition
```

`disposition` ∈ `debt`, `risk`, `false_positive`, `looks_bad_but_correct`.

**Slice mínimo:** template + validador de paths/linhas + três fixtures: dívida real, falso positivo arquitetural e evidência insuficiente.

**Gate:** nenhum achado sem leitura direta do arquivo; confiança não substitui evidência; escopo obrigatório por módulo/path.

**Civilization:** apenas consumidor opcional de relatório. Não requer ratificação para a skill procedural.

### 6.2 Grafo determinístico — `DEFER_EXTENSION`, não nova skill

O HAOS já possui indexação incremental SHA-256, AST Python, docs, confidence labels, query helper e skill.

**Próxima ação correta:** medir perguntas não respondidas, especialmente chamadas dinâmicas e TS/JS. Criar corpus virgem poliglota e comparar:

1. grafo atual Python+docs;
2. extensão mínima TS/JS;
3. Graphify externo isolado.

**Métricas:** precisão das arestas contra ground truth, perguntas arquiteturais respondidas, tempo, memória, tamanho do artefato e custo operacional.

**Gate de implementação:** somente estender se o tratamento superar o baseline em perguntas relevantes sem regressão de determinismo/idempotência. Tree-sitter fica opcional/lazy e com limites de dependência pinados.

**Reachability separada:** decidir entre skill+arquivo (já suficiente) e MCP persistente. Não registrar no gateway/Civilization só para declarar integração.

### 6.3 Explicações visuais — `NEW_OPTIONAL_SKILL`

**Destino:** `optional-skills/creative/visual-explainer/`.

**Slice mínimo:** entrada JSON de grafo/diagrama → renderização SVG determinística → sanitização → verificação → artefato.

**Segurança:** rejeitar scripts, handlers `on*`, `foreignObject`, URLs externas e referências não permitidas; usar allowlist. Sanitização por remoção silenciosa não basta para artefatos não confiáveis.

**Legibilidade:** não prometer validação de sobreposição apenas com `xml.etree`; isso exige motor geométrico/renderização. Primeira versão valida XML, viewport, IDs, links, sanitização e limites. Geometria avançada só com renderer escolhido e benchmarkado.

**Testes:** SVG seguro, XSS, referência externa, dimensões inválidas, determinismo e arquivo excessivo.

**Civilization:** não necessário.

### 6.4 Gate formal para skills externas — `EXTEND_EXISTING_PIPELINE`

**Antes de editar:** o Agente A deve provar se `tools/skills_guard.py`, procedural engine, holdout gate e instalação via hub formam hoje um pipeline único. Nomes próximos não provam composição.

**Contrato de ingresso:**

- origem e URL canônicas;
- commit SHA de 40 caracteres para conteúdo remoto;
- licença identificada e política de compatibilidade explícita;
- hashes dos artefatos;
- manifesto declarativo de arquivos, rede, subprocessos e credenciais;
- dependências com limites/pins conforme política do repo;
- análise estática;
- sandbox/preflight;
- eval virgem contra baseline;
- decisão de promoção auditável.

**Classes de risco:**

- somente instrução/read-only;
- escreve no workspace;
- rede;
- subprocessos/instalação;
- credenciais;
- mutação externa.

A classe define sandbox e aprovação. Skill sem manifesto ou com comportamento excedente falha fechada.

**Civilization:** conselho apenas para risco alto/identity-critical se esse for o caminho canônico já usado pela promoção. Não tornar o Civilization dependência obrigatória da instalação de skill comum.

**Slice mínimo:** integrar manifesto e provenance ao comando de instalação existente, com fixture local Git; nenhuma rede viva. Depois provar fail-closed pelo caminho real de instalação, não pela função isolada.

## 7. Ordem de execução

1. **A0 — Auditoria de reachability** dos cinco equivalentes e do pipeline de promoção de skills.
2. **T0 — Baseline temporal virgem.**
3. **S1 — Tech-debt como extensão de skill.** Baixo risco, utilidade imediata.
4. **T1–T2 — Metadados + ledger temporal.**
5. **G1 — Manifesto mínimo para skills externas**, condicionado ao resultado da auditoria.
6. **T3–T4 — Skill temporal + delegação estruturada.**
7. **V1 — Visual explainer opcional.**
8. **CG0 — Benchmark do grafo.** Implementação somente se o gate passar.
9. **CIV — Integrações Civilization justificadas por consumidor real.**
10. **R1 — Uma revisão adversarial final** e decisão de rollout.

## 8. Estratégia de testes e harness

Para cada slice:

1. definir um comportamento observável e fixture pequena;
2. executar teste direcionado e confirmar falha pela razão esperada;
3. implementar a menor mudança;
4. executar o mesmo conjunto direcionado uma vez;
5. executar os testes da área afetada uma vez;
6. revisar diff, profile scope, cache e segurança;
7. parar quando os critérios forem satisfeitos.

Não usar contagens globais, snapshots de catálogos, regex sobre fonte nem mocks que contornem registry/resolver. Gates de segurança e resolução devem ter E2E com imports reais e `HERMES_HOME` temporário; mudanças profile-scoped exigem A→B→A.

## 9. Riscos principais

- **Falsa integração:** registro existe, mas nenhuma entrada de produção o alcança.
- **Duplicação:** novo grafo/ledger/pipeline competindo com o canônico.
- **Autoavaliação:** o mesmo LLM gera e aprova sua evidência.
- **Prompt-cache:** dados voláteis inseridos no prefixo estático.
- **Profile leak:** paths ou caches resolvidos no import/launch profile.
- **Supply chain:** skill instala scripts/deps além do manifesto.
- **Civilization overreach:** governança usada como justificativa para acoplamento universal.
- **Métrica induzível:** perguntas contêm respostas ou usam corpus conhecido pelo implementador.

## 10. Definition of Done

O plano só será considerado implementado quando:

- a matriz de reachability tiver provas reprodutíveis;
- o contrato temporal superar ou caracterizar honestamente o baseline virgem;
- cada nova skill passar auditoria HARDLINE e seus testes offline;
- o gate externo falhar fechado no caminho real de instalação;
- nenhuma integração Civilization for declarada sem E2E real;
- os artefatos forem profile-scoped e recuperáveis;
- rollback estiver documentado e testado onde houver migração;
- a revisão adversarial não encontrar bloqueador aberto.

## 11. Comandos de verificação previstos

Executar somente os subconjuntos correspondentes aos arquivos realmente alterados:

```bash
scripts/run_tests.sh tests/skills/test_grounded_citations_skill.py tests/tools/test_web_tools_perplexity.py -q
scripts/run_tests.sh tests/skills/test_haos_codebase_wiki_skill.py tests/test_haos_codebase_wiki.py tests/test_haos_codebase_wiki_cli.py -q
scripts/run_tests.sh tests/skills/ -q
python scripts/audit_skills.py
```

Os comandos são previstos, não executados nesta fase de planejamento.
