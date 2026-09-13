# Auditoria: DeerFlow → Research Worker (Fase 1, port puro)

Fonte: auditoria read-only do padrão DeerFlow (clone `TEMP/references/deer-flow`, main @ 27b2b67 = DeerFlow **2.0**, rewrite sem pipeline clássico; o pipeline Deep Research original vive na branch `main-1.x` @ 2ab2876, auditada em `/tmp/df-audit/deerflow-1x`). Nenhum arquivo de referência foi modificado.

## 1. Escopo

Mapear o padrão Deep Research do DeerFlow (decomposição de pergunta → passos tipados → execução por tipo de passo → artefato de evidência com citações) para um `ResearchWorker` HAOS como `CapabilityProvider`, sem importar runtime externo (`INTEGRATIONS.md:31-33,112-118`) e sem duplicar o que o scaffold já cobre (PerceptionArtifact, Ouroboros `evidence`, Kanban/lifecycle). Linha-alvo: `INTEGRATIONS.md:68` e pendência Fase 1 (`INTEGRATIONS.md:11,102`).

## 2. Achados-chave

**Versão canônica do padrão = DeerFlow 1.x, não o clone main (2.0).** O clone apontado é a reescrita 2.0: não tem módulo de planejamento, nem artefatos/evidência tipados, nem modelos de citação (`README.md:17`: "2.0 … shares no code with v1. If you're looking for the original Deep Research framework, it's maintained on the 1.x branch"). Em 2.0 a pesquisa é *skill + convenção de prompt*: `skills/public/deep-research/SKILL.md` (fases Broad → Deep Dive → Diversity → Synthesis), `todos`/`artifacts` em `ThreadState` (`backend/docs/ARCHITECTURE.md:134-145`), citação inline `[citation:Título](url)` + seção Sources por convenção (`agents/lead_agent/prompt.py:672-728`).

**Research worker (padrão 1.x) — a cadeia a portar:**
- Decomposição = Planner emite UM `Plan` tipado, não árvore: `src/prompts/planner_model.py` — `StepType` (research|analysis|processing, :10-13), `Step{need_search, title, description, step_type, execution_res}` (:16-23), `Plan{locale, has_enough_context, thought, title, steps}` (:26-36). Teto de passos `max_step_num` (default 3, `src/config/configuration.py:49`; prompt `src/prompts/planner.md:161-199`).
- Estado-agregador: `State` (`src/graph/types.py:14-48`) — `observations`, `current_plan`, `final_report`, `citations`, `goto`; é o container único de evidência.
- Evidência tipada só existe como metadados de citação: `CitationMetadata{url,title,description?,content_snippet?,raw_content?,domain?,relevance_score,credibility_score,accessed_at,extra}` + `id = sha256(url)[:12]` (`src/citations/models.py:16-110`); `Citation{number, metadata, context?, cited_text?}` (:114-185); `from_search_result` é o adapter cru→evidência (:95-110). **Não há dataclass `Finding` upstream** — o "finding" é `Step.execution_res` + `observations`.
- Proveniência: extração de `ToolMessage` (web_search/crawl) → `extract_citations_from_messages`/`merge_citations` (`src/citations/extractor.py:20-62,147-180,183-206,364-407`, dedupe por url, maior relevance vence).

**Planner/executor (padrão 1.x):**
- `planner_node` (`src/graph/nodes.py:268-395`) valida/repara o plano: `validate_and_fix_plan` (:125-200) infere `step_type` ausente (:150-158) e impõe ≥1 passo research com `need_search` (:164-198) — lógica pura, portável como está.
- Fan-out por tipo de passo: `continue_to_running_research_team` (`src/graph/builder.py:23-47`) — RESEARCH→researcher, ANALYSIS→analyst, PROCESSING→coder; todos com `execution_res` → volta ao planner/reporter.
- Execução por passo: `_execute_agent_step` (`nodes.py:1080-1351`) — acha 1º passo não executado, roda agente, grava `step.execution_res`, acumula `observations`, extrai+mescla citações (:1332-1335,1347-1348); researcher usa só ferramentas próprias (`researcher_node` :1426-1466: web_search+crawl se habilitado, retriever primeiro).
- Síntese final = UMA resposta + citações: `reporter_node` (`nodes.py:877-954`) — observações como contexto (:913-920), lista de citações injetada DEPOIS das observações (anti-alucinação de URL, :929-938); retorna `{final_report, citations}` (:951-953).
- Emenda pura (seam): o pipeline de evidência consome resultados canônicos `{url,title,content,score}` — `clean_results_with_images` (`src/tools/tavily_search/tavily_search_api_wrapper.py:97-140`) → extractor/collector — sem engine específico. Fetcher scriptado com essa forma exercita a cadeia inteira sem rede.

## 3. Lacunas acionáveis

1. **`Finding` tipado**: DeerFlow deixa o finding implícito (execution_res + observations); HAOS precisa do dataclass para artefato de evidência pesquisável.
2. **`depth`**: 1.x só tem planos de 1 nível (passos); sub-perguntas aninhadas exigem o knob `depth` (inexistente upstream).
3. **TeamSpec/papel research**: 7 papéis canônicos são town/rig (`hermes/platform/execution/team.py:35-44`); não há papel nem postura "researcher" (`hermes/platform/posture/specs.py:28-111`); `TaskSpec.task_class="research"` existe (`tasks/spec.py:30`) mas o enum de `strategy` (:33) não tem valor research. Fica como gap de Fase posterior, aditivo.
4. **Fetcher de produção**: rede em processo é proibida por padrão; precisa da camada out-of-process (subprocesso/serviço, forma B) com contrato stdout JSON.

## 4. Status de implementação

O desenho desta auditoria **já virou código + testes** (árvore limpa, commits existentes):
- Módulo novo `hermes/platform/capabilities/research/`: `models.py` (ResearchQuestion/Citation/Finding/ResearchArtifact + to_dict/from_dict), `worker.py` (`decompose` pura fail-closed; `ResearchProvider(CapabilityProvider)` provider_id `research-worker`, probe/acquire/release; `register_research_provider`), `aggregate.py` (merge dedupe por url + síntese + `evidence` slot Ouroboros), `fetcher.py` (SubprocessFetcher, peer stdout JSON, fail-closed).
- Testes: `tests/platform/capabilities/test_research_worker.py` (bounds do decompose, roundtrip JSON, probe/acquire/release com fetcher scriptado, fetcher ausente falha, dedupe entre sub-perguntas, agregação pura com evidence, registro); `tests/platform/capabilities/research/test_fetcher_out_of_process.py` (peer real de subprocesso, rc≠0/vazio/JSON ruim fail-closed, provider out-of-process); registro `deep-research` (agentic) verificado em `tests/platform/test_assembly.py`.
- Integração: `hermes/platform/assembly.py` registra `deep-research` via `register_research_provider`; padrão segue o desacoplamento do VisionWorker (registry não importa workers).

## 5. Recomendações

1. **Port puro (C), não subprocesso deer-flow (B)**: o valor é o *padrão* (Plan/Step → dispatch por step_type → findings → merge de citações → síntese), expressável stdlib-only; importar DeerFlow 1.x arrastaria LangGraph/LangChain/pydantic para o core (proibido). B fica só para o fetcher de produção (subprocesso/serviço), plugado pelo seam `fetcher` sem tocar o núcleo.
2. **Manter o artefato compatível com Ouroboros**: `evidence` do artefato agregado encaixa em `proposal["evidence"]` (`hermes/platform/evolution/analyzer.py:81-97`) — não criar formato paralelo.
3. **Não duplicar**: reutilizar o estilo de proveniência do PerceptionArtifact (`produced_by`/`model_identity`/`uncertainty`); não re-implementar lifecycle de tarefa (Kanban cobre).
4. **Cobertura futura**: ao adicionar papel/papéis research ao TeamSpec e valor `research` em `strategy`, manter aditivo; atualizar `COMPLIANCE.md`/`REFERENCES.md` e marcar `INTEGRATIONS.md:68` ✅ quando a linha fechar.
