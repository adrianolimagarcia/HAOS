# Auditoria de Adoção OpenMausBot (OMB) -> HAOS

Data: 11 de Outubro de 2026
Branch: `research/openmausbot-adoption`
Status da suíte de testes: PASSING (verificado via pytest em `tests/platform/memory/`)

## 1. Mapeamento de Funcionalidades

| Funcionalidade OpenMausBot | Status no HAOS | Arquivo(s) HAOS Relevantes | Decisão | Complexidade |
|---|---|---|---|---|
| **1. approval_timeout_policy**<br>(Timeout configurável com abort vs proceed_read_only por severidade) | PARCIAL | `tools/approval_gateway_wait.py`<br>`tools/approval_smart.py`<br>`tools/approval.py` | Adaptar HAOS: Adicionar política configurável `proceed_read_only` (que congela ferramentas mutantes mantendo ferramentas de leitura) vs `abort` (default), com suporte a severidade alta/baixa. | Média |
| **2. webhook_triggers**<br>(Endpoints HTTP autenticados para disparar eventos/sessões do agente) | PARCIAL | `hermes_cli/web_routers/`<br>`hermes_cli/web_server.py`<br>`cron/` | Adaptar HAOS: Criar `hermes_cli/web_routers/webhooks.py` integrado ao FastAPI de `web_server.py`, com autenticação HMAC/Bearer, proteção contra replay (nonce/timestamp) e mapeamento de payload para fila de prompts/eventos. | Média |
| **3. mcp_budget_spill**<br>(Orçamento de chars/tokens para MCP e spill para arquivo) | PARCIAL | `tools/tool_result_storage.py`<br>`tools/mcp_tool_content.py`<br>`tools/budget_config.py` | Adaptar HAOS: O HAOS já possui spillover em `tools/tool_result_storage.py` com limite em `mcp_result_size_chars: 50000` e cap em `mcp_tool_content.py`. Refinar e garantir tags `<persisted-output>` uniformes e testes de cobertura estritos. | Baixa |
| **4. pre_turn_decider**<br>(Heurística pré-turno: fast-path / modelo leve / LLM principal) | PARCIAL | `agent/turn_setup.py`<br>`agent/smart_model_routing.py`<br>`run_agent.py` | Adaptar HAOS: Implementar `agent/pre_turn_decider.py` preservando byte-stability e prompt caching sagrados do HAOS. | Média |
| **5. event_stream_receipts**<br>(Recibo estruturado JSON por turno com métricas e spans) | PARCIAL | `hermes/platform/kernel/tracer.py`<br>`agent/turn_response.py` | Adaptar HAOS: Conectar a telemetria do `DistributedTracer` a um gravador de recibos determinísticos em `~/.haos/receipts/<session_id>/<turn>.json`. | Baixa |
| **6. team_templates**<br>(Modelos declarativos de equipes de bots Civilization) | PARCIAL | `hermes/platform/civilization/`<br>`hermes/platform/bots/` | Adaptar HAOS: Adicionar suporte a templates declarativos (YAML/JSON) para spawn e configuração de equipes de bots especializados (ex: dev, review, research) sobre a Civilization existente. | Média |
| **7. evals_harness**<br>(Harness de evals reprodutível e determinístico) | JÁ EXISTE | `evals/`<br>`scripts/run_tests.sh` | Manter e Expandir HAOS: HAOS já possui infraestrutura em `evals/`. Adicionar suite de evals dedicada para adoção OMB em `evals/omb_adoption/`. | Baixa |

## 2. Decisões de Arquitetura e Invariantes Respeitados

1. **Prompt Caching Sagrado**: Nenhuma alteração muta histórico passado ou prefixos estáveis de prompt no decider pré-turno.
2. **Narrow Waist**: Novas capacidades são integradas via routers modulares (`web_routers`), plugins ou módulos de suporte (`agent/pre_turn_decider.py`), sem inflar schemas globais de ferramentas.
3. **Ambiente Isolado**: Nenhuma alteração é feita em `/usr/local/lib/haos-agent`. Todo desenvolvimento, testes e execução ocorrem estritamente em `/run/media/adriano/e681b5ac-a4fb-44d4-aebf-9d6584065787/dsh-projetos/HERMES-TURBO` na branch `research/openmausbot-adoption`.
