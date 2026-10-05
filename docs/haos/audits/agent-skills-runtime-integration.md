# Relatório A0: Auditoria Definitiva de Integração do Runtime Agent Skills

**Data de Execução:** 2026-10-05  
**Escopo:** Auditoria estática e factual de integração de runtime, entrypoints de instalação, memória, pipelines de evolução e alinhamento com Golden Tasks (G1).  
**Propriedade de Escopo:** Exclusiva em `docs/haos/audits/agent-skills-runtime-integration.md`. Sem modificações no código de produção.

---

## 1. Classificação Factual de Componentes e Runtime Bindings

Classificações auditadas segundo a taxonomia estrita:
`ACTIVE` | `MANUAL_ONLY` | `DEAD_EPHEMERAL` | `NOT_IN_CIV` | `UNKNOWN`

| Componente | Classificação | Entrada | Resolver | Executor | Efeito | Evidência (file:line) |
|---|---|---|---|---|---|---|
| **Ponytail / Posture** | `ACTIVE` | Tarefa com postura selecionada ou default (`TaskSpec`, CLI `--posture`) | `PostureResolver.resolve("ponytail")` (`specs.py:17`, `resolver.py:1`) | `ContextBuilder.build_package(task, posture)` (`context_engine/compiler.py:13`) | Injeta overlay determinístico de postura (*"PONYTAIL DECISION LADDER"*, *YAGNI*, diff mínima) nas seções de contexto do agent | `hermes/platform/posture/specs.py:17-48`, `tests/test_haos_ponytail_posture.py:11-34` |
| **Grounded-Citations / Provenance** | `ACTIVE` | Cadeia de citações/evidências em respostas e queries | `EvidenceChain.verify_all()` | `ProvenanceVerifier.assess_chain()` | Classifica status formal (`grounded`, `partial`, `ungrounded`). Zero referências = `ungrounded`. Não silencia, reporta status | `hermes/platform/memory/provenance.py:15,121-137` |
| **Web Research (G005)** | `MANUAL_ONLY` | Invocação explícita da task G005 via benchmark runner | `GOLDEN_TASKS["G005"]` | `GoldenTasksRunner.run_suite(["G005"])` (usa mock ou executor injetado) | Não há daemon automático de pesquisa web; a suíte avalia citações e geração de brief sob budget de tokens | `hermes/platform/evals/golden_tasks.py:109-118,183-222` |
| **Instincts / Dream Memory** | `MANUAL_ONLY` | CLI command `hermes dream` | CLI argparser em `main_agent_cmds.py:185` | `Consolidator.run_dream(dry_run=...)` | Consolidação off-line/sob demanda de instintos em regras destiladas | `hermes_cli/main_agent_cmds.py:185-215` |
| **Codebase-Wiki vs GraphRAG** | `ACTIVE` (Wiki) / `ACTIVE` (GraphRAG) | CLI `hermes codebase-wiki export-mcp` ou chamadas MCP locais | `register_wiki_server(aggregator, graph_path)` | `wiki_dispatcher(graph_path, tool_name, args)` | Registra ferramentas MCP federadas (`wiki_query_graph`, `wiki_find_nodes`, etc.) que consultam sob demanda o grafo JSON gerado | `hermes/platform/codebase/mcp_export.py:151-171`, `hermes_cli/codebase_wiki_impl.py:126-135` |
| **Civilization Memory Integration** | `NOT_IN_CIV` | `CivilizationManager.record_assertion` / `query_knowledge` | Event store isolado em `hermes/platform/civilization` | `CivilizationManager` | Conhecimento gravado via eventos constitucionais não alimenta automaticamente o contexto diário do Hermes nem o buffer de instintos de `Consolidator` | `hermes/platform/civilization/manager.py:28-165` |

---

## 2. Entrypoints de Instalação e SkillsGuard

### Entrypoints Auditados:
1. **Official / Wshobson Catalog:**
   - Entrypoint: `haos skills install <skill_name>` via `hermes_cli/haos_cmd.py:275` (`cmd_haos_skills_install`).
   - Download de conteúdo via `WshobsonCatalog.fetch_skill_content`.
   - **Chamada ao SkillsGuard:** **SIM**. Linhas `296-307` importam `from tools.skills_guard import scan_skill`, gravam em quarentena temporária (`tempfile.TemporaryDirectory()`) e executam `scan_skill(qdir, source="trusted")`. Se o veredito for divergente de `safe` ou `caution` e `--force` não estiver ativo, a instalação é imediatamente abortada.
2. **Hub / GitHub Search & Git Install:**
   - Entrypoint: `hermes skills hub install <id>` via `hermes_cli/skills_hub.py:671` (`do_install`).
   - Entrypoint alternativo / scanner: `tools/skills_hub_install.py:31` (`install_skill`).
   - **Chamada ao SkillsGuard:** **NÃO**. O fluxo do `skills_hub.py` realiza clonagem/cópia direta sem submeter os artefatos ao scan de quarentena do `tools/skills_guard.py`.

---

## 3. Composição de Procedural Engine, Holdout Split e Council

Os três módulos operam como **subsistemas modulares desacoplados**, e **NÃO** fazem parte do instalador padrão de skills (`cmd_haos_skills_install` ou `skills_hub.py`):

1. **`ProceduralEngine` (`hermes/platform/skills/procedural_engine.py`):**
   - Gerencia ciclo de vida interno (registro em memória/disco, geração sintética e execução de testes de eval procedural).
   - Acionado via CLI específico: `haos skills promote <skill_id>` (`hermes_cli/haos_cmd.py:213-248`).
2. **`HoldoutSplit` (`hermes/platform/evals/holdout_split.py` & `evolution/promotion_holdout_gate.py`):**
   - Portão de promoção estrita com separação train/validation/test (90/10 ou 80/20) para evitar data leakage de casos de regressão.
   - Utilizado pelo `PromotionHoldoutGate` e evals da plataforma, isolado do instalador comum.
3. **`Council` (`hermes/platform/council`):**
   - Mecanismo de deliberação multi-agente / constitucional para governança de patches e admissão de mudanças de alto risco.
   - Executado via `DebateRunner` e gates de deliberação; desacoplado da instalação local do usuário.

---

## 4. Agendamento Cron e Rotinas Nightly

- **Pesquisa no Source:** Não foram encontrados agendamentos automáticos hardcoded (systemd timer ou cron ativo do OS) no runtime para tarefas de evolução contínua ou consolidadores.
- **`run_dream`:** Operação manual/on-demand (`hermes dream`).
- **Nightly Research:** Não existe cron job registrado no runtime para busca noturna automática de documentação ou skills.

---

## 5. Decisões Arquiteturais e Diretrizes para G1

1. **Persistent Wiki MCP:**
   - `codebase-wiki` já implementa exportação para `LocalMCPAggregator` via `register_wiki_server` (`hermes/platform/codebase/mcp_export.py`).
   - *Decisão:* Adotar `LocalMCPAggregator` como backend permanente para expor ferramentas MCP locais aos agentes sem overhead de servidores de processo separados.
2. **Civilization Memory Bridge:**
   - As asserções de conhecimento (`KnowledgeAssertion`) de `CivilizationManager` devem ser bridgeadas para o `ContextBuilder` (`hermes/platform/memory/context_engine/compiler.py`) via um resolver de memória unificado, impedindo que o conhecimento de governança fique ilhado.
3. **Nightly Research:**
   - Manter como comando explícito/orquestrado (via CLI ou script agendado do host), sem introduzir timers ocultos no processo principal do daemon.
4. **Ponto Exato para G1 (Golden Tasks Suite):**
   - O ponto exato de acoplamento do benchmark G1 é `GoldenTasksRunner` (`hermes/platform/evals/golden_tasks.py:183`) integrado com o validador de artefatos determinísticos `artifact_verifier` (`tests/platform/evals/test_eval_leakage_and_gates.py:190-210`).
   - Para G1, a execução deve passar por `validate_golden_tasks()` assegurando rejeição estrita de no-ops e gravação honesta via `BaselineStore` (recusando mock).
